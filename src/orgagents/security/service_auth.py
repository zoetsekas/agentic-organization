"""How a caller proves who it is to an agent's worker (ADR-0114).

Two things, deliberately separate:

* a **service token** says *which platform component* is calling a worker
  (`/run`, `/workflow`, `/approve`). One per worker; only the platform and the
  chat surface hold them, and an agent never holds another agent's, so an
  agent that could reach a neighbour's port still cannot make it act;
* an **approval token** says *which person* released *which call*. It is an
  Ed25519 signature over the agent (and `aud`, the same agent id), the tool,
  a hash of the canonical arguments, the approver, an expiry, a nonce and the
  `kid` of the signing key, made by the component that authenticated the
  person. A request field naming an approver proves nothing: anyone who can
  reach the port can type a name. A token proves the issuer vouched for that
  person, for that call, now.

Signing is asymmetric (ADR-0114 v1.1): only the issuer holds a private key.
Workers hold public keys, by `kid`, so a worker -- or anything that reads its
environment -- can verify releases and can never mint one, for itself or for
anybody else. Several public keys may be trusted at once, which is how the
issuer's key is rotated without a gap.

Wire format: `<b64url(claims JSON)>.<b64url(Ed25519 signature over the first
part)>`. Keys travel as `kid:b64url(32 raw bytes)`, lists comma-separated.

`cryptography` is imported only where a key is used, so a component that only
checks bearer tokens does not need it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

#: How long a person's release is good for before the worker sees it. Short:
#: it is minted at the click and presented immediately.
APPROVAL_TOKEN_TTL = 300


class ApprovalTokenError(Exception):
    """The token does not prove what it claims; the reason is safe to show."""


def canonical_arguments(arguments: Optional[dict[str, Any]]) -> str:
    """One spelling of a set of arguments, so `{a, b}` and `{b, a}` sign alike."""
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"),
                      default=str)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# -- keys ----------------------------------------------------------------------

def generate_signing_key(kid: Optional[str] = None) -> tuple[str, str]:
    """A new issuer keypair as (`kid:private`, `kid:public`). The private half
    goes to the issuer only; the public half to every worker."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    kid = kid or time.strftime("k%Y%m%d") + "-" + secrets.token_hex(3)
    key = Ed25519PrivateKey.generate()
    raw_priv = key.private_bytes(serialization.Encoding.Raw,
                                 serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
    raw_pub = key.public_key().public_bytes(serialization.Encoding.Raw,
                                            serialization.PublicFormat.Raw)
    return f"{kid}:{_b64(raw_priv)}", f"{kid}:{_b64(raw_pub)}"


def _split_key(spec: str) -> tuple[str, bytes]:
    kid, sep, value = (spec or "").strip().partition(":")
    if not sep or not kid or not value:
        raise ValueError("a key is written 'kid:base64url'")
    raw = _unb64(value)
    if len(raw) != 32:
        raise ValueError(f"key {kid!r} is not 32 bytes")
    return kid, raw


def public_key_of(signing_key: str) -> str:
    """`kid:public` for a `kid:private`."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    kid, raw = _split_key(signing_key)
    pub = Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return f"{kid}:{_b64(pub)}"


def parse_public_keys(spec: str) -> dict[str, bytes]:
    """`kid:pub,kid:pub` -> {kid: raw}. Empty means: trust nobody."""
    out: dict[str, bytes] = {}
    for part in (spec or "").split(","):
        if part.strip():
            kid, raw = _split_key(part)
            out[kid] = raw
    return out


# -- tokens --------------------------------------------------------------------

def issue_approval(signing_key: str, agent_id: str, tool: str,
                   arguments: Optional[dict[str, Any]], approver: str, *,
                   ttl: int = APPROVAL_TOKEN_TTL, now: Optional[float] = None,
                   nonce: Optional[str] = None) -> str:
    """Sign one person's release of one call with the issuer's private key
    (`kid:private`). Only an issuer that has authenticated `approver` may
    call this."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    kid, raw = _split_key(signing_key)
    claims = {
        "agent": agent_id,
        "aud": agent_id,
        "tool": tool,
        "args": hashlib.sha256(canonical_arguments(arguments).encode()).hexdigest(),
        "approver": approver,
        "exp": int((now if now is not None else time.time()) + ttl),
        "nonce": nonce or secrets.token_urlsafe(12),
        "kid": kid,
    }
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    sig = Ed25519PrivateKey.from_private_bytes(raw).sign(body.encode())
    return f"{body}.{_b64(sig)}"


def verify_approval(public_keys: "dict[str, bytes] | str", token: str, *,
                    agent_id: str, tool: str,
                    arguments: Optional[dict[str, Any]],
                    now: Optional[float] = None) -> dict[str, Any]:
    """The claims, if and only if the token was signed by a trusted issuer key
    for exactly this call to this agent and has not expired. Replay is the
    caller's to refuse (see `NonceStore`)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    keys = parse_public_keys(public_keys) if isinstance(public_keys, str) else public_keys
    if not keys:
        raise ApprovalTokenError("this worker trusts no approval issuer; it accepts no approvals")
    good = False
    try:
        body, sig = token.split(".", 1)
        # The kid only selects which trusted key to try; nothing else in the
        # body is read until the signature holds.
        kid = json.loads(_unb64(body)).get("kid", "")
        raw = keys.get(kid) if isinstance(kid, str) else None
        if raw is not None:
            Ed25519PublicKey.from_public_bytes(raw).verify(_unb64(sig), body.encode())
            good = True
    except (ValueError, AttributeError, TypeError, InvalidSignature):
        good = False
    if not good:
        raise ApprovalTokenError("the approval is not signed by an issuer this worker trusts")
    claims = json.loads(_unb64(body))
    if claims.get("agent") != agent_id or claims.get("aud") != agent_id:
        raise ApprovalTokenError("the approval is for another agent")
    if claims.get("tool") != tool:
        raise ApprovalTokenError("the approval is for another tool")
    digest = hashlib.sha256(canonical_arguments(arguments).encode()).hexdigest()
    if claims.get("args") != digest:
        raise ApprovalTokenError("the approval is for other arguments")
    if int(claims.get("exp", 0)) < (now if now is not None else time.time()):
        raise ApprovalTokenError("the approval has expired")
    if not claims.get("nonce"):
        raise ApprovalTokenError("the approval has no nonce")
    return claims


class NonceStore:
    """Nonces of releases already used, kept until they would have expired
    anyway, and written through to a file so a restarted worker still refuses
    a replay (ADR-0114). With no path it is memory only."""

    def __init__(self, path: "str | Path | None" = None) -> None:
        self.path = Path(path) if path else None
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            now = time.time()
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    if float(entry["exp"]) >= now:
                        self._seen[str(entry["nonce"])] = float(entry["exp"])
                except (ValueError, KeyError, TypeError):
                    continue
            self._rewrite()

    def _rewrite(self) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("".join(json.dumps({"nonce": n, "exp": e}) + "\n"
                               for n, e in self._seen.items()), encoding="utf-8")
        os.replace(tmp, self.path)

    def use(self, nonce: str, exp: float) -> bool:
        """Record `nonce`; False if it was already used (a replay)."""
        with self._lock:
            now = time.time()
            expired = [n for n, e in self._seen.items() if e < now]
            for n in expired:
                del self._seen[n]
            if nonce in self._seen:
                return False
            self._seen[nonce] = float(exp)
            if self.path:
                if expired:
                    self._rewrite()
                else:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"nonce": nonce, "exp": float(exp)}) + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())
            return True

    def __contains__(self, nonce: str) -> bool:
        return nonce in self._seen


def bearer_matches(authorization: str, expected: str) -> bool:
    """A constant-time check of `Authorization: Bearer <token>`."""
    if not expected:
        return False
    scheme, _, value = (authorization or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(
        value.strip().encode(), expected.encode())
