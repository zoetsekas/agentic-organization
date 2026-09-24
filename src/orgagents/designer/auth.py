"""Who the person *is*, before RBAC asks what they may do (ADR-0047).

`rbac.py` answers "what may this principal do here". It cannot answer "is this
principal who the request says it is" — until now that answer came from a
request header, which is a claim anyone who can reach the port may make. This
module supplies a verified answer: an OIDC ID token, checked against the
issuer's published keys, mapped onto workspace roles through configuration.

Three properties shape it.

**Nothing is trusted that was not verified.** Signature, issuer, audience,
expiry, not-before, issued-at and — where the caller supplies one — the nonce
are all checked before a single claim is read for identity. The algorithm is
pinned to the configured asymmetric set, so a token that asks to be verified
with `none` or with an HMAC over the public key is refused rather than
accommodated.

**Deny by default survives authentication.** A verified token proves identity;
it does not confer access. A group that no mapping names grants nothing, and a
person with no mapped group and no workspace membership is authenticated and
still refused everything.

**The header path stays, but has to be asked for.** `auth_mode` is explicit:
`trusted_proxy` means "an authenticating proxy in front of me owns identity and
I am reading its headers", `oidc` means "I verify tokens myself". In `oidc`
mode a bare header authenticates nothing, because a silent fallback turns the
whole of this module into decoration.

There is deliberately no hard dependency on a JWT library. The verification
this module needs — RSASSA-PKCS1-v1_5 over a SHA-2 digest — is a modular
exponentiation and a byte comparison, and writing it here keeps the designer
installable anywhere `hashlib` is, at the cost of supporting RSA only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .audit import AuditAction, AuditLog, AuditOutcome
from .models import DesignerSettings, UserRole
from .rbac import Principal

logger = logging.getLogger(__name__)

# Roles from weakest to strongest. Two mapped groups are not a conflict to be
# reported — the strongest of them wins, which is what every directory-backed
# system does and what people expect when they are added to a second group.
ROLE_STRENGTH: dict[UserRole, int] = {
    UserRole.VIEWER: 1,
    UserRole.REVIEWER: 2,
    UserRole.EDITOR: 3,
    UserRole.ADMIN: 4,
    UserRole.OWNER: 5,
}

# Installation-wide key in a role map: "this group grants this role everywhere".
ANY_WORKSPACE = "*"


# -- failures -------------------------------------------------------------
#
# One class per reason, all with a stable `code`, because "authentication
# failed" in a log is useless at 3am and because the API layer needs to tell
# 401 (we do not know who you are) from 403 (we know, and no).


class AuthError(Exception):
    """Authentication failed. Always a 401, never a 403."""

    code = "unauthenticated"


class AuthConfigurationError(AuthError):
    """The installation is misconfigured — not the caller's fault, but we
    still refuse, because guessing is how fallbacks become vulnerabilities."""

    code = "misconfigured"


class MissingCredentials(AuthError):
    code = "missing_credentials"


class MalformedToken(AuthError):
    code = "malformed_token"


class UnsupportedAlgorithm(AuthError):
    code = "unsupported_algorithm"


class UnknownSigningKey(AuthError):
    code = "unknown_key"


class InvalidSignature(AuthError):
    code = "invalid_signature"


class TokenExpired(AuthError):
    code = "expired"


class TokenNotYetValid(AuthError):
    code = "not_yet_valid"


class TokenTooOld(AuthError):
    code = "stale_iat"


class InvalidIssuer(AuthError):
    code = "wrong_issuer"


class InvalidAudience(AuthError):
    code = "wrong_audience"


class InvalidNonce(AuthError):
    code = "bad_nonce"


class JWKSUnavailable(AuthError):
    """The issuer's key set could not be read and nothing usable is cached.

    This is the failure mode OIDC adds that header identity did not have: the
    designer is now unavailable when the provider is, for tokens signed with a
    key we have not seen. ADR-0047 records that trade.
    """

    code = "jwks_unavailable"


# -- base64url and JOSE wire format ---------------------------------------


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same answer
        raise MalformedToken(f"segment is not base64url: {exc}") from exc


def _b64url_uint(value: str) -> int:
    return int.from_bytes(_b64url_decode(value), "big")


# DER-encoded DigestInfo prefixes for RSASSA-PKCS1-v1_5 (RFC 8017 §9.2). Fixed
# byte strings, so the "encode then compare" check below is a comparison and
# not a parse — nothing in the signature gets interpreted.
_DIGEST_INFO = {
    "SHA-256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "SHA-384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "SHA-512": bytes.fromhex("3051300d060960864801650304020305000440"),
}

ALGORITHMS: dict[str, tuple[str, Callable[[], "hashlib._Hash"]]] = {
    "RS256": ("SHA-256", hashlib.sha256),
    "RS384": ("SHA-384", hashlib.sha384),
    "RS512": ("SHA-512", hashlib.sha512),
}


@dataclass(frozen=True)
class RSAPublicKey:
    """A JWK, reduced to the two numbers verification needs."""

    kid: str
    n: int
    e: int
    alg: str = ""
    use: str = "sig"

    @property
    def modulus_bytes(self) -> int:
        return (self.n.bit_length() + 7) // 8

    @classmethod
    def from_jwk(cls, jwk: Mapping[str, Any]) -> "RSAPublicKey":
        if jwk.get("kty") != "RSA":
            raise AuthConfigurationError(
                f"unsupported key type {jwk.get('kty')!r}; only RSA is verified here"
            )
        return cls(
            kid=str(jwk.get("kid", "")),
            n=_b64url_uint(str(jwk["n"])),
            e=_b64url_uint(str(jwk["e"])),
            alg=str(jwk.get("alg", "")),
            use=str(jwk.get("use", "sig")),
        )

    def verify(self, signing_input: bytes, signature: bytes, alg: str) -> None:
        """RSASSA-PKCS1-v1_5 verify, or raise `InvalidSignature`."""
        digest_name, hasher = ALGORITHMS[alg]
        k = self.modulus_bytes
        if len(signature) != k:
            raise InvalidSignature("signature length does not match the key")
        m = pow(int.from_bytes(signature, "big"), self.e, self.n)
        em = m.to_bytes(k, "big")
        digest = hasher(signing_input).digest()
        t = _DIGEST_INFO[digest_name] + digest
        # PKCS#1 v1.5: 0x00 || 0x01 || 0xFF... || 0x00 || DigestInfo.
        expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
        if len(expected) != k or not _constant_time_eq(em, expected):
            raise InvalidSignature("signature does not verify against this key")


def _constant_time_eq(a: bytes, b: bytes) -> bool:
    # Signature verification uses only public values, so this is belt and
    # braces; it costs nothing and removes the question from review.
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0


# -- JWKS: fetching, caching, rotation ------------------------------------

JWKSSource = Callable[[], Mapping[str, Any]]


class HttpJWKSSource:
    """Fetch a JWKS document over HTTPS.

    Injectable on purpose: this is the only part of verification that touches
    the network, so tests (and air-gapped installs) hand in a callable instead.
    """

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout

    def __call__(self) -> Mapping[str, Any]:
        if not self.url.startswith("https://"):
            raise AuthConfigurationError(
                f"the JWKS URI must be https, got {self.url!r}"
            )
        with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


class JWKSCache:
    """Keys, cached, with a bounded willingness to go and look again.

    Rotation is the normal case, not an incident: an issuer publishes the new
    key, signs with it, and retires the old one later. So an unknown `kid` is
    treated as "probably rotated" and triggers one refresh — rate-limited, so a
    stream of tokens bearing a bogus `kid` cannot be turned into a stream of
    requests to the provider.

    If a refresh fails while keys are cached, the cached keys keep being served
    and the failure is logged. Availability of the provider should not become
    an availability requirement of every request.
    """

    def __init__(self, source: JWKSSource, *, ttl_seconds: float = 300.0,
                 min_refresh_interval: float = 10.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.source = source
        self.ttl_seconds = ttl_seconds
        self.min_refresh_interval = min_refresh_interval
        self._clock = clock
        self._keys: dict[str, RSAPublicKey] = {}
        self._fetched_at: Optional[float] = None
        self._last_attempt: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def keys(self) -> dict[str, RSAPublicKey]:
        return dict(self._keys)

    def _fetch(self) -> None:
        self._last_attempt = self._clock()
        document = self.source()
        keys: dict[str, RSAPublicKey] = {}
        for jwk in document.get("keys", []) or []:
            if jwk.get("kty") != "RSA" or jwk.get("use", "sig") != "sig":
                continue  # encryption keys and curves we cannot verify with
            key = RSAPublicKey.from_jwk(jwk)
            keys[key.kid] = key
        if not keys:
            raise JWKSUnavailable("the key set contains no usable RSA signing key")
        self._keys = keys
        self._fetched_at = self._clock()

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            now = self._clock()
            if (not force and self._last_attempt is not None
                    and now - self._last_attempt < self.min_refresh_interval):
                return
            try:
                self._fetch()
            except AuthError:
                if not self._keys:
                    raise
                logger.warning("JWKS refresh failed; serving %d cached key(s)",
                               len(self._keys), exc_info=True)
            except Exception as exc:  # noqa: BLE001 - transport errors vary
                if not self._keys:
                    raise JWKSUnavailable(f"could not read the key set: {exc}") from exc
                logger.warning("JWKS refresh failed; serving %d cached key(s): %s",
                               len(self._keys), exc)

    def _is_stale(self) -> bool:
        return (self._fetched_at is None
                or self._clock() - self._fetched_at >= self.ttl_seconds)

    def key(self, kid: str) -> RSAPublicKey:
        if not self._keys or self._is_stale():
            self.refresh()
        key = self._keys.get(kid) if kid else _sole(self._keys)
        if key is None:
            # Rotation: the kid is new to us, so look again — but through the
            # rate limit, so a stream of tokens bearing invented kids cannot be
            # amplified into a stream of requests to the provider.
            self.refresh()
            key = self._keys.get(kid) if kid else _sole(self._keys)
        if key is None:
            raise UnknownSigningKey(
                f"no signing key with kid {kid!r} in the issuer's key set"
            )
        return key


def _sole(keys: Mapping[str, RSAPublicKey]) -> Optional[RSAPublicKey]:
    """A token without a `kid` is only resolvable if there is one key."""
    return next(iter(keys.values())) if len(keys) == 1 else None


# -- configuration --------------------------------------------------------


@dataclass(frozen=True)
class OIDCConfig:
    """What a token has to satisfy. Every field is a check, not a hint."""

    issuer: str
    audiences: tuple[str, ...]
    algorithms: tuple[str, ...] = ("RS256", "RS384", "RS512")
    # Clocks drift. A skew window is unavoidable; it is also a real weakening
    # of expiry, so it is small and explicit rather than generous and hidden.
    clock_skew_seconds: int = 60
    # Optional upper bound on how old `iat` may be, for installs that want
    # freshness beyond the issuer's chosen token lifetime.
    max_age_seconds: Optional[int] = None
    require_iat: bool = True
    nonce_required: bool = False
    subject_claim: str = "sub"
    groups_claim: str = "groups"
    name_claim: str = "name"
    email_claim: str = "email"

    def __post_init__(self) -> None:
        if not self.issuer:
            raise AuthConfigurationError("OIDC mode needs an issuer")
        if not self.audiences:
            raise AuthConfigurationError("OIDC mode needs at least one audience")
        unknown = [a for a in self.algorithms if a not in ALGORITHMS]
        if unknown:
            raise AuthConfigurationError(
                f"unsupported signing algorithm(s): {', '.join(unknown)}"
            )


@dataclass(frozen=True)
class GroupRoleMapping:
    """Claims to workspace roles, with a precedence that is written down.

    1. An explicit workspace membership beats everything here. A person added
       by an admin keeps the role that admin chose; the directory does not
       quietly demote or promote them. (`rbac.role_of` enforces this.)
    2. Otherwise, a mapping scoped to that workspace applies.
    3. Otherwise, an installation-wide (`*`) mapping applies.
    4. Otherwise nothing. An unmapped group grants no access at all — it is not
       an error and not a default role, it is simply not a grant.

    Within tiers 2 and 3 the strongest matching role wins.
    """

    # workspace id (or "*") -> group name -> role
    by_workspace: Mapping[str, Mapping[str, UserRole]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "GroupRoleMapping":
        """Accepts the flat form `{group: role}` and the scoped form
        `{workspace_id: {group: role}}`, and mixes of the two."""
        by_workspace: dict[str, dict[str, UserRole]] = {}
        for key, value in (config or {}).items():
            if isinstance(value, Mapping):
                by_workspace.setdefault(str(key), {}).update(
                    {str(g): UserRole(r) for g, r in value.items()}
                )
            else:
                by_workspace.setdefault(ANY_WORKSPACE, {})[str(key)] = UserRole(value)
        return cls(by_workspace=by_workspace)

    def _strongest(self, scope: str, groups: Sequence[str]) -> Optional[UserRole]:
        table = self.by_workspace.get(scope, {})
        matched = [table[g] for g in groups if g in table]
        return max(matched, key=lambda r: ROLE_STRENGTH[r]) if matched else None

    def grants(self, groups: Iterable[str]) -> dict[str, UserRole]:
        """The roles these groups grant, keyed by workspace id (`*` = all)."""
        seen = list(dict.fromkeys(groups))
        grants: dict[str, UserRole] = {}
        for scope in self.by_workspace:
            role = self._strongest(scope, seen)
            if role is not None:
                grants[scope] = role
        return grants


# -- the verified principal ----------------------------------------------


@dataclass(frozen=True)
class AuthenticatedPrincipal(Principal):
    """A `Principal` that can say where its identity came from.

    Everything downstream keeps taking a `Principal`, so RBAC, the service and
    the audit log are unchanged by this module's existence; what changes is
    that in `oidc` mode the object they receive is one a signature vouched for.
    """

    subject: str = ""
    issuer: str = ""
    auth_mode: str = "trusted_proxy"
    groups: tuple[str, ...] = ()
    expires_at: Optional[int] = None
    token_id: str = ""
    claims: Mapping[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.auth_mode == "oidc"

    def summary(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "auth_mode": self.auth_mode,
            "verified": self.verified,
            "issuer": self.issuer,
            "groups": list(self.groups),
            "granted_roles": {k: v.value for k, v in self.granted_roles.items()},
        }


# -- verification ---------------------------------------------------------


class TokenVerifier:
    """Verify an OIDC ID token and turn it into claims."""

    def __init__(self, config: OIDCConfig, jwks: JWKSCache, *,
                 clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self.jwks = jwks
        self._clock = clock

    def verify(self, token: str, *, nonce: Optional[str] = None
               ) -> dict[str, Any]:
        header, payload, signing_input, signature = _split(token)

        alg = str(header.get("alg", ""))
        if alg not in self.config.algorithms:
            # Covers `none` and the HMAC-over-the-public-key confusion attack:
            # anything not on the configured list is refused before any key is
            # looked up, so the token cannot choose how it is checked.
            raise UnsupportedAlgorithm(f"algorithm {alg!r} is not accepted here")

        key = self.jwks.key(str(header.get("kid", "")))
        if key.alg and key.alg != alg:
            raise UnsupportedAlgorithm(
                f"key {key.kid!r} is published for {key.alg}, token says {alg}"
            )
        key.verify(signing_input, signature, alg)

        # Only now are claims worth reading.
        skew = self.config.clock_skew_seconds
        now = int(self._clock())

        if payload.get("iss") != self.config.issuer:
            raise InvalidIssuer(
                f"token issuer {payload.get('iss')!r} is not "
                f"{self.config.issuer!r}"
            )

        audience = payload.get("aud")
        auds = [audience] if isinstance(audience, str) else list(audience or [])
        if not set(map(str, auds)) & set(self.config.audiences):
            raise InvalidAudience(
                f"token audience {auds!r} does not include this installation"
            )

        exp = payload.get("exp")
        if exp is None:
            raise MalformedToken("token has no expiry")
        if now > int(exp) + skew:
            raise TokenExpired("token has expired")

        nbf = payload.get("nbf")
        if nbf is not None and now + skew < int(nbf):
            raise TokenNotYetValid("token is not valid yet")

        iat = payload.get("iat")
        if iat is None:
            if self.config.require_iat:
                raise MalformedToken("token has no iat")
        else:
            if now + skew < int(iat):
                raise TokenNotYetValid("token was issued in the future")
            if (self.config.max_age_seconds is not None
                    and now - int(iat) > self.config.max_age_seconds + skew):
                raise TokenTooOld("token is older than this installation accepts")

        # A nonce binds the token to the authorization request that asked for
        # it. It is only checkable when the caller kept that request's nonce,
        # so it is checked when supplied and required only if configured.
        claimed_nonce = payload.get("nonce")
        if nonce is not None:
            if claimed_nonce != nonce:
                raise InvalidNonce("token nonce does not match the request")
        elif self.config.nonce_required:
            raise InvalidNonce("this installation requires a nonce and none was given")

        if not payload.get(self.config.subject_claim):
            raise MalformedToken(
                f"token has no {self.config.subject_claim!r} claim to identify a person"
            )
        return payload


def _split(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    parts = (token or "").strip().split(".")
    if len(parts) != 3:
        raise MalformedToken("a JWS compact serialization has three segments")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except json.JSONDecodeError as exc:
        raise MalformedToken(f"segment is not JSON: {exc}") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise MalformedToken("header and payload must be JSON objects")
    signature = _b64url_decode(parts[2])
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    return header, payload, signing_input, signature


# -- the front door -------------------------------------------------------


def _groups_of(payload: Mapping[str, Any], claim: str) -> tuple[str, ...]:
    raw = payload.get(claim)
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(g for g in raw.replace(",", " ").split() if g)
    if isinstance(raw, (list, tuple)):
        return tuple(str(g) for g in raw)
    return ()


#: The identity headers trusted-proxy mode reads unless configured otherwise
#: (ORGAGENTS_PROXY_USER_HEADER / _NAME_HEADER / _EMAIL_HEADER). X-User stays
#: the default so a proxy (or the platform client) that sets X-User keeps
#: working; a deployment behind a forward-auth service names the headers that
#: service returns, as docker/compose/fabric.yml does. Whatever the names, the
#: proxy MUST strip them from client requests before it sets them.
DEFAULT_PROXY_USER_HEADER = "X-User"
DEFAULT_PROXY_NAME_HEADER = "X-User-Name"
DEFAULT_PROXY_EMAIL_HEADER = "X-User-Email"


class Authenticator:
    """Resolves the principal for a request, per the configured `auth_mode`.

    `trusted_proxy` reads headers and says so; `oidc` verifies a bearer token
    and refuses headers; `none` is for single-user local use and grants no
    roles of its own. Refusals are written to the audit log, because a burst of
    them is the first sign of both a misconfiguration and an attack.
    """

    def __init__(self, settings: DesignerSettings, *,
                 verifier: Optional[TokenVerifier] = None,
                 mapping: Optional[GroupRoleMapping] = None,
                 audit: Optional[AuditLog] = None,
                 proxy_secret: str = "",
                 proxy_sources: Optional[list[str]] = None,
                 proxy_user_header: str = "",
                 proxy_name_header: str = "",
                 proxy_email_header: str = "") -> None:
        self.settings = settings
        self.verifier = verifier
        self.mapping = mapping if mapping is not None else GroupRoleMapping.from_config(
            settings.oidc_group_roles
        )
        self.audit = audit
        # How trusted-proxy mode knows a request came *through* the proxy
        # (ADR-0114): a secret header only the proxy sets, or the proxy's
        # source addresses, or both. Without either, X-User is a header
        # anybody who can reach the port may set.
        self.proxy_secret = proxy_secret
        self.proxy_sources = list(proxy_sources or [])
        # Which headers carry identity in trusted-proxy mode (ADR-0114). They
        # must be headers the proxy *sets from its authentication* (a
        # forward-auth response) and strips from every client request first;
        # the fabric compose uses oauth2-proxy's X-Auth-Request-*. No other
        # identity header is read -- X-User included, when it is not the
        # configured one -- so a client header that slips past is ignored.
        self.proxy_user_header = proxy_user_header or DEFAULT_PROXY_USER_HEADER
        self.proxy_name_header = proxy_name_header or DEFAULT_PROXY_NAME_HEADER
        self.proxy_email_header = proxy_email_header or DEFAULT_PROXY_EMAIL_HEADER

    def identity_headers(self, headers: Any) -> dict[str, str]:
        """The user/name/email header values this mode reads, as keyword
        arguments for `authenticate`. `headers` is a case-insensitive mapping
        (Starlette's). In `trusted_proxy` mode only the configured names are
        read; otherwise the conventional X-User family is."""
        def get(name: str) -> str:
            return str(headers.get(name, "") or "")
        if self.settings.auth_mode == "trusted_proxy":
            return {"user_header": get(self.proxy_user_header),
                    "name_header": get(self.proxy_name_header),
                    "email_header": get(self.proxy_email_header)}
        return {"user_header": get("X-User") or "anonymous",
                "name_header": get("X-User-Name"),
                "email_header": get("X-User-Email")}

    def require_proxy_guard(self) -> None:
        """Refuse to run trusted-proxy mode with nothing to tell the proxy
        from anybody else. Called at startup, so the mistake stops the
        designer rather than silently trusting every caller."""
        if self.settings.auth_mode == "trusted_proxy" and not (
                self.proxy_secret or self.proxy_sources):
            raise AuthConfigurationError(
                "auth_mode is 'trusted_proxy' but neither ORGAGENTS_PROXY_SECRET "
                "nor ORGAGENTS_PROXY_SOURCES is set, so any caller could claim "
                "any identity in X-User; configure one, or use 'oidc', or "
                "'none' for single-user local use")

    def _from_proxy(self, proxy_secret_header: str, client_host: str) -> bool:
        import hmac
        import ipaddress

        if self.proxy_secret and not hmac.compare_digest(
                (proxy_secret_header or "").encode(), self.proxy_secret.encode()):
            return False
        if self.proxy_sources:
            try:
                addr = ipaddress.ip_address(client_host)
            except ValueError:
                return False
            return any(addr in ipaddress.ip_network(s, strict=False)
                       for s in self.proxy_sources)
        return True

    # In trusted-proxy mode the header names are configurable (ADR-0114): the
    # designer reads exactly the header the proxy's authentication sets, not a
    # conventional name a client might also send.
    def authenticate(self, *, authorization: str = "", user_header: str = "",
                     name_header: str = "", email_header: str = "",
                     nonce: Optional[str] = None, proxy_secret_header: str = "",
                     client_host: str = "") -> AuthenticatedPrincipal:
        mode = self.settings.auth_mode
        try:
            if mode == "oidc":
                return self._authenticate_oidc(authorization, nonce=nonce)
            if mode == "none":
                return AuthenticatedPrincipal(
                    user_id=user_header or "anonymous",
                    display_name=name_header or user_header or "anonymous",
                    email=email_header, auth_mode="none",
                )
            if (self.proxy_secret or self.proxy_sources) and not self._from_proxy(
                    proxy_secret_header, client_host):
                raise MissingCredentials(
                    "trusted-proxy mode accepts identity only from the proxy, "
                    "and this request did not come through it")
            return self._authenticate_proxy(user_header, name_header, email_header)
        except AuthError as exc:
            self._record_failure(exc, mode, user_header)
            raise

    def _authenticate_proxy(self, user_header: str, name_header: str,
                            email_header: str) -> AuthenticatedPrincipal:
        if not user_header:
            raise MissingCredentials(
                f"trusted-proxy mode expects the proxy to set {self.proxy_user_header}"
            )
        return AuthenticatedPrincipal(
            user_id=user_header, display_name=name_header or user_header,
            email=email_header, auth_mode="trusted_proxy", subject=user_header,
        )

    def _authenticate_oidc(self, authorization: str, *, nonce: Optional[str]
                           ) -> AuthenticatedPrincipal:
        if self.verifier is None:
            raise AuthConfigurationError(
                "auth_mode is 'oidc' but no issuer/JWKS is configured"
            )
        token = _bearer(authorization)
        payload = self.verifier.verify(token, nonce=nonce)
        config = self.verifier.config
        groups = _groups_of(payload, config.groups_claim)
        subject = str(payload[config.subject_claim])
        # The user id is the issuer's subject, not an email: emails get reused
        # and reassigned, subjects do not.
        return AuthenticatedPrincipal(
            user_id=subject,
            display_name=str(payload.get(config.name_claim, "") or subject),
            email=str(payload.get(config.email_claim, "") or ""),
            granted_roles=self.mapping.grants(groups),
            subject=subject,
            issuer=str(payload.get("iss", "")),
            auth_mode="oidc",
            groups=groups,
            expires_at=int(payload["exp"]),
            token_id=str(payload.get("jti", "")),
            claims=dict(payload),
        )

    def _record_failure(self, exc: AuthError, mode: str, attempted: str) -> None:
        if self.audit is None:
            return
        self.audit.record(
            AuditAction.AUTH_FAILED, None, outcome=AuditOutcome.DENIED,
            reason=str(exc),
            # No token, no claims: a failed token is attacker-controlled input
            # and does not belong in an append-only log.
            detail={"code": exc.code, "auth_mode": mode,
                    "claimed_user": attempted},
        )


def _bearer(authorization: str) -> str:
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise MissingCredentials(
            "OIDC mode requires an 'Authorization: Bearer <id token>' header"
        )
    return value.strip()


def verifier_from_settings(settings: DesignerSettings, *,
                           jwks_source: Optional[JWKSSource] = None,
                           clock: Callable[[], float] = time.time
                           ) -> Optional[TokenVerifier]:
    """Build a verifier from persisted settings, or None if OIDC is not set up.

    `jwks_source` is injectable so that tests, and installations that mirror
    their provider's key set locally, never reach the network.
    """
    if settings.auth_mode != "oidc":
        return None
    if not settings.oidc_issuer or not settings.oidc_audiences:
        raise AuthConfigurationError(
            "auth_mode is 'oidc' but oidc_issuer/oidc_audiences are not set"
        )
    source = jwks_source
    if source is None:
        if not settings.oidc_jwks_uri:
            raise AuthConfigurationError(
                "auth_mode is 'oidc' but oidc_jwks_uri is not set"
            )
        source = HttpJWKSSource(settings.oidc_jwks_uri)
    config = OIDCConfig(
        issuer=settings.oidc_issuer,
        audiences=tuple(settings.oidc_audiences),
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
        max_age_seconds=settings.oidc_max_age_seconds,
        nonce_required=settings.oidc_require_nonce,
        groups_claim=settings.oidc_groups_claim,
    )
    return TokenVerifier(
        config,
        JWKSCache(source, ttl_seconds=settings.oidc_jwks_ttl_seconds),
        clock=clock,
    )
