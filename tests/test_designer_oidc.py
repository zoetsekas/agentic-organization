"""OIDC identity for the designer (WS-021 M3, ADR-0047).

Everything here runs offline: an RSA key pair is generated in-process, the
JWKS "endpoint" is a callable that returns a dict, and tokens are signed with
the same pure-Python PKCS#1 v1.5 construction the verifier checks. Nothing
imports a JWT library, because nothing in `auth.py` does.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import time

import pytest
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.designer import (
    AuditAction,
    AuditOutcome,
    Authenticator,
    DesignerService,
    GroupRoleMapping,
    JWKSCache,
    Member,
    MemoryRepository,
    OIDCConfig,
    TokenVerifier,
    UserRole,
)
from orgagents.designer.audit import AuditLog
from orgagents.designer.auth import (
    AuthConfigurationError,
    InvalidAudience,
    InvalidIssuer,
    InvalidNonce,
    InvalidSignature,
    JWKSUnavailable,
    MissingCredentials,
    TokenExpired,
    TokenNotYetValid,
    UnknownSigningKey,
    UnsupportedAlgorithm,
    verifier_from_settings,
)
from orgagents.designer.models import DesignerSettings
from orgagents.designer.rbac import VIEW, decide

ISSUER = "https://id.example.test/"
AUDIENCE = "orgagents-designer"

# -- a tiny RSA implementation, for tests only ----------------------------
#
# `cryptography` cannot be imported in this environment, so the key pair is
# generated here. 1024 bits keeps generation fast; these keys sign nothing but
# the tokens in this file.

_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _is_probable_prime(n: int, rounds: int = 24, rng=random) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = rng.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, rng) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate, rng=rng):
            return candidate


class Key:
    """An RSA key pair, as a JWK and as a signer."""

    def __init__(self, kid: str, seed: int, bits: int = 1024) -> None:
        rng = random.Random(seed)
        e = 65537
        while True:
            p, q = _prime(bits // 2, rng), _prime(bits // 2, rng)
            if p == q:
                continue
            phi = (p - 1) * (q - 1)
            if phi % e == 0:      # e must be invertible mod phi
                continue
            self.kid, self.n, self.e = kid, p * q, e
            self.d = pow(e, -1, phi)
            self.size = (self.n.bit_length() + 7) // 8
            return

    def jwk(self) -> dict:
        return {"kty": "RSA", "kid": self.kid, "use": "sig", "alg": "RS256",
                "n": _b64(self.n.to_bytes(self.size, "big")),
                "e": _b64(self.e.to_bytes(3, "big"))}

    def sign(self, message: bytes) -> bytes:
        t = _DIGEST_INFO + hashlib.sha256(message).digest()
        em = b"\x00\x01" + b"\xff" * (self.size - len(t) - 3) + b"\x00" + t
        return pow(int.from_bytes(em, "big"), self.d, self.n).to_bytes(
            self.size, "big")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _segment(obj: dict) -> str:
    return _b64(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def token(key: Key, *, alg: str = "RS256", kid: str | None = None,
          sign_with: Key | None = None, **claims) -> str:
    now = int(time.time())
    payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-bob",
               "name": "Bob Reyes", "email": "bob@example.test",
               "groups": ["designers"], "iat": now, "exp": now + 300}
    payload.update({k: v for k, v in claims.items() if v is not None})
    for k, v in claims.items():
        if v is None:
            payload.pop(k, None)
    header = {"alg": alg, "typ": "JWT", "kid": kid if kid is not None else key.kid}
    signing_input = f"{_segment(header)}.{_segment(payload)}".encode("ascii")
    signature = (sign_with or key).sign(signing_input)
    return f"{signing_input.decode('ascii')}.{_b64(signature)}"


# -- fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def key() -> Key:
    return Key("key-1", seed=1)


@pytest.fixture(scope="module")
def rotated_key() -> Key:
    return Key("key-2", seed=2)


@pytest.fixture()
def jwks(key):
    """A mutable key set plus a call counter, standing in for the endpoint."""

    state = {"keys": [key.jwk()], "calls": 0, "now": 1_000.0}

    def source():
        state["calls"] += 1
        if state.get("down"):
            raise OSError("the identity provider is unreachable")
        return {"keys": state["keys"]}

    state["source"] = source
    return state


def make_verifier(jwks, **config) -> TokenVerifier:
    cfg = OIDCConfig(issuer=ISSUER, audiences=(AUDIENCE,), **config)
    cache = JWKSCache(jwks["source"], ttl_seconds=300,
                      clock=lambda: jwks["now"])
    return TokenVerifier(cfg, cache)


def oidc_settings(**extra) -> DesignerSettings:
    return DesignerSettings(
        persistence="memory", auth_mode="oidc", oidc_issuer=ISSUER,
        oidc_audiences=[AUDIENCE],
        oidc_group_roles={"designers": "editor", "auditors": "admin"},
        **extra,
    )


@pytest.fixture()
def auth(jwks):
    """An authenticator in OIDC mode with an audit log we can read back."""
    log = AuditLog(MemoryRepository())
    return Authenticator(oidc_settings(), verifier=make_verifier(jwks), audit=log)


# -- the happy path --------------------------------------------------------


def test_a_valid_token_authenticates_and_maps_a_group_to_a_role(auth, key):
    who = auth.authenticate(authorization=f"Bearer {token(key)}")

    assert who.verified and who.auth_mode == "oidc"
    assert who.user_id == "user-bob"      # the subject, never the email
    assert who.display_name == "Bob Reyes" and who.email == "bob@example.test"
    assert who.issuer == ISSUER and who.groups == ("designers",)
    assert who.granted_role("any-workspace") is UserRole.EDITOR


def test_the_strongest_of_several_mapped_groups_wins(auth, key):
    who = auth.authenticate(
        authorization=f"Bearer {token(key, groups=['designers', 'auditors'])}")
    assert who.granted_role("w1") is UserRole.ADMIN


def test_a_workspace_scoped_mapping_beats_the_installation_wide_one():
    mapping = GroupRoleMapping.from_config(
        {"designers": "viewer", "ws_1": {"designers": "admin"}})
    grants = mapping.grants(["designers"])
    assert grants == {"*": UserRole.VIEWER, "ws_1": UserRole.ADMIN}


def test_an_explicit_membership_is_not_rewritten_by_the_directory(key, jwks):
    """Precedence tier 1: an admin's decision outranks a group claim."""
    service = DesignerService(MemoryRepository(), DesignerSettings(persistence="memory"))
    from orgagents.designer.rbac import Principal

    owner = Principal(user_id="alice", display_name="Alice")
    workspace = service.create_workspace(owner, "Acme")
    service.add_member(owner, workspace.id,
                       Member(user_id="user-bob", role=UserRole.VIEWER))

    auth = Authenticator(oidc_settings(), verifier=make_verifier(jwks))
    bob = auth.authenticate(authorization=f"Bearer {token(key)}")  # group -> editor
    from orgagents.designer.rbac import role_of

    assert role_of(service.repository.get_workspace(workspace.id), bob) is UserRole.VIEWER


# -- deny by default -------------------------------------------------------


def test_an_unmapped_group_grants_nothing(auth, key):
    who = auth.authenticate(
        authorization=f"Bearer {token(key, groups=['interns', 'everyone'])}")

    assert who.verified                      # identity is proven
    assert who.granted_roles == {}           # and confers nothing
    assert who.granted_role("ws_1") is None

    service = DesignerService(MemoryRepository(), DesignerSettings(persistence="memory"))
    from orgagents.designer.rbac import Principal

    workspace = service.create_workspace(Principal(user_id="alice"), "Acme")
    assert not decide(service.repository.get_workspace(workspace.id), who, VIEW)


def test_a_token_with_no_groups_claim_grants_nothing(auth, key):
    who = auth.authenticate(authorization=f"Bearer {token(key, groups=None)}")
    assert who.groups == () and who.granted_roles == {}


# -- refusals --------------------------------------------------------------


def test_an_expired_token_is_refused(auth, key):
    now = int(time.time())
    with pytest.raises(TokenExpired):
        auth.authenticate(
            authorization=f"Bearer {token(key, iat=now - 7200, exp=now - 3600)}")


def test_a_token_from_another_issuer_is_refused(auth, key):
    with pytest.raises(InvalidIssuer):
        auth.authenticate(
            authorization=f"Bearer {token(key, iss='https://evil.example/')}")


def test_a_token_for_another_audience_is_refused(auth, key):
    with pytest.raises(InvalidAudience):
        auth.authenticate(
            authorization=f"Bearer {token(key, aud='some-other-app')}")


def test_a_tampered_payload_fails_the_signature_check(auth, key):
    header, payload, signature = token(key).split(".")
    forged = json.loads(base64.urlsafe_b64decode(payload + "=="))
    forged["groups"] = ["auditors"]          # self-promotion to admin
    with pytest.raises(InvalidSignature):
        auth.authenticate(
            authorization=f"Bearer {header}.{_segment(forged)}.{signature}")


def test_a_token_signed_by_the_wrong_key_is_refused(auth, key, rotated_key):
    with pytest.raises(InvalidSignature):
        auth.authenticate(
            authorization=f"Bearer {token(key, sign_with=rotated_key)}")


def test_a_token_naming_an_unknown_key_is_refused(auth, key):
    with pytest.raises(UnknownSigningKey):
        auth.authenticate(authorization=f"Bearer {token(key, kid='key-999')}")


def test_the_alg_header_cannot_choose_how_the_token_is_checked(auth, key):
    for alg in ("none", "HS256"):
        with pytest.raises(UnsupportedAlgorithm):
            auth.authenticate(authorization=f"Bearer {token(key, alg=alg)}")


def test_a_nonce_is_checked_when_the_caller_kept_one(auth, key):
    good = token(key, nonce="n-123")
    assert auth.authenticate(authorization=f"Bearer {good}", nonce="n-123").verified
    with pytest.raises(InvalidNonce):
        auth.authenticate(authorization=f"Bearer {good}", nonce="n-456")


def test_clock_skew_is_bounded_and_explicit(jwks, key):
    now = int(time.time())
    fresh_from_a_fast_clock = token(key, iat=now + 30, exp=now + 330)
    tolerant = Authenticator(oidc_settings(), verifier=make_verifier(
        jwks, clock_skew_seconds=60))
    strict = Authenticator(oidc_settings(), verifier=make_verifier(
        jwks, clock_skew_seconds=0))

    assert tolerant.authenticate(
        authorization=f"Bearer {fresh_from_a_fast_clock}").verified
    with pytest.raises(TokenNotYetValid):
        strict.authenticate(authorization=f"Bearer {fresh_from_a_fast_clock}")


# -- JWKS: caching, rotation, availability --------------------------------


def test_keys_are_cached_across_requests(auth, key, jwks):
    for _ in range(5):
        auth.authenticate(authorization=f"Bearer {token(key)}")
    assert jwks["calls"] == 1


def test_key_rotation_is_picked_up_without_a_restart(auth, key, rotated_key, jwks):
    auth.authenticate(authorization=f"Bearer {token(key)}")

    # The issuer rotates: it publishes both keys and signs with the new one.
    jwks["keys"] = [key.jwk(), rotated_key.jwk()]
    jwks["now"] += 60          # past the refresh rate limit
    who = auth.authenticate(authorization=f"Bearer {token(rotated_key)}")
    assert who.verified and jwks["calls"] == 2

    # And once the old key is retired, tokens it signed stop verifying.
    jwks["keys"] = [rotated_key.jwk()]
    jwks["now"] += 60
    auth.verifier.jwks.refresh(force=True)
    with pytest.raises(UnknownSigningKey):
        auth.authenticate(authorization=f"Bearer {token(key)}")


def test_a_provider_outage_does_not_invalidate_cached_keys(auth, key, jwks):
    auth.authenticate(authorization=f"Bearer {token(key)}")
    jwks["down"] = True
    jwks["now"] += 3_600       # the cached key set is now stale
    assert auth.authenticate(authorization=f"Bearer {token(key)}").verified


def test_an_outage_with_a_cold_cache_is_a_clear_failure(jwks, key):
    jwks["down"] = True
    cold = Authenticator(oidc_settings(), verifier=make_verifier(jwks))
    with pytest.raises(JWKSUnavailable):
        cold.authenticate(authorization=f"Bearer {token(key)}")


def test_a_bogus_kid_cannot_be_turned_into_a_flood_of_provider_requests(
        auth, key, jwks):
    for n in range(10):
        with pytest.raises(UnknownSigningKey):
            auth.authenticate(authorization=f"Bearer {token(key, kid=f'x{n}')}")
    assert jwks["calls"] == 1


# -- modes -----------------------------------------------------------------


def test_a_bare_header_authenticates_nothing_when_oidc_is_configured(auth):
    with pytest.raises(MissingCredentials):
        auth.authenticate(user_header="alice", name_header="Alice")


def test_a_header_is_accepted_in_trusted_proxy_mode():
    proxy = Authenticator(DesignerSettings(persistence="memory",
                                           auth_mode="trusted_proxy"))
    who = proxy.authenticate(user_header="alice", name_header="Alice")
    assert who.user_id == "alice" and who.auth_mode == "trusted_proxy"
    assert not who.verified and who.granted_roles == {}


def test_the_legacy_header_mode_name_still_loads_as_trusted_proxy():
    assert DesignerSettings(auth_mode="header").auth_mode == "trusted_proxy"


def test_oidc_mode_without_an_issuer_refuses_rather_than_falling_back():
    broken = DesignerSettings(persistence="memory", auth_mode="oidc")
    with pytest.raises(AuthConfigurationError):
        verifier_from_settings(broken)
    with pytest.raises(AuthConfigurationError):
        Authenticator(broken).authenticate(authorization="Bearer x.y.z")


def test_the_configured_mode_is_visible_in_settings():
    settings = oidc_settings()
    dumped = settings.model_dump(mode="json")
    assert dumped["auth_mode"] == "oidc"
    assert dumped["oidc_issuer"] == ISSUER
    assert dumped["oidc_group_roles"] == {"designers": "editor",
                                          "auditors": "admin"}


# -- the audit log ---------------------------------------------------------


def _failures(auth):
    return [e for e in auth.audit.repository.audit_events()
            if e.action is AuditAction.AUTH_FAILED]


def test_authentication_failures_are_recorded(auth, key):
    with pytest.raises(TokenExpired):
        now = int(time.time())
        auth.authenticate(
            authorization=f"Bearer {token(key, iat=now - 7200, exp=now - 3600)}")
    with pytest.raises(MissingCredentials):
        auth.authenticate(user_header="alice")

    recorded = _failures(auth)
    assert [e.outcome for e in recorded] == [AuditOutcome.DENIED] * 2
    assert {e.detail["code"] for e in recorded} == {"expired", "missing_credentials"}
    assert recorded[1].detail["claimed_user"] == "alice"
    # No token, no claims: attacker-controlled input stays out of the log.
    assert all("token" not in e.detail for e in recorded)


def test_a_successful_authentication_is_not_logged_per_request(auth, key):
    auth.authenticate(authorization=f"Bearer {token(key)}")
    assert _failures(auth) == []


# -- over HTTP -------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return TestClient(create_app(str(tmp_path / "oidc.db")))


def _switch_to_oidc(client, jwks, **settings_extra):
    app = client.app
    app.state.designer_auth = Authenticator(
        oidc_settings(**settings_extra), verifier=make_verifier(jwks),
        audit=app.state.designer.audit,
    )
    return app


def test_the_api_defaults_to_single_user_local_mode_and_says_so(client):
    # `none`, not `trusted_proxy`: with no proxy in front, the old default
    # believed any caller's X-User (ADR-0114). `none` still reads the header,
    # and says it is single-user local.
    settings = client.get("/api/designer/settings").json()
    assert settings["auth_mode"] == "none"
    assert client.get("/api/designer/whoami",
                      headers={"X-User": "alice"}).json()["user_id"] == "alice"


def test_the_api_refuses_a_bare_header_once_oidc_is_configured(client, jwks, key):
    _switch_to_oidc(client, jwks)
    assert client.get("/api/designer/whoami",
                      headers={"X-User": "alice"}).status_code == 401

    ok = client.get("/api/designer/whoami",
                    headers={"Authorization": f"Bearer {token(key)}"})
    assert ok.status_code == 200 and ok.json()["user_id"] == "user-bob"


def test_authentication_is_401_and_authorization_stays_403(client, jwks, key):
    # A workspace exists, created by someone else while in proxy mode.
    client.post("/api/designer/workspaces", json={"name": "Acme"},
                headers={"X-User": "alice", "X-User-Name": "Alice"})
    _switch_to_oidc(client, jwks)

    assert client.get("/api/designer/audit").status_code == 401

    # Verified, but "designers" maps to editor, which does not grant audit.read.
    editor = {"Authorization": f"Bearer {token(key)}"}
    assert client.get("/api/designer/audit", headers=editor).status_code == 403

    # Verified, and "auditors" maps to admin, which does.
    admin = {"Authorization": f"Bearer {token(key, groups=['auditors'])}"}
    assert client.get("/api/designer/audit", headers=admin).status_code == 200


def test_an_unmapped_group_gets_nothing_over_http(client, jwks, key):
    client.post("/api/designer/workspaces", json={"name": "Acme"},
                headers={"X-User": "alice"})
    _switch_to_oidc(client, jwks)
    headers = {"Authorization": f"Bearer {token(key, groups=['interns'])}"}

    assert client.get("/api/designer/whoami", headers=headers).status_code == 200
    assert client.get("/api/designer/systems", headers=headers).json() == []
    assert client.get("/api/designer/audit", headers=headers).status_code == 403


def test_http_failures_reach_the_designer_audit_log(client, jwks):
    app = _switch_to_oidc(client, jwks)
    client.get("/api/designer/whoami", headers={"X-User": "mallory"})

    events = [e for e in app.state.designer.repository.audit_events()
              if e.action is AuditAction.AUTH_FAILED]
    assert events and events[-1].detail["claimed_user"] == "mallory"
    assert events[-1].outcome is AuditOutcome.DENIED
