"""NATS NKeys, without the `nkeys` package (ADR-0118 v1.1).

An NKey is an Ed25519 keypair in NATS's own spelling: base32, a prefix byte
saying what the key is (`U` a user, `S` a seed) and a CRC-16. The broker's
configuration holds a user's *public* key only; the connecting client proves
it holds the seed by signing the nonce the server sends. So a broker, or
anything that can read its configuration or environment, can recognise every
agent and impersonate none of them.

The same seed is the agent's hop-signing key: a worker signs each delegation
hop it sends with it, and receivers verify with the public keys (see
`orgagents.runtime.agent_bus`). One identity per agent, held by that agent
alone.

The `nkeys` package on PyPI ships as an sdist with a C dependency; this is the
~60 lines of it the platform needs, on `cryptography` (already a core
dependency, ADR-0114). `install_nkeys_shim()` registers it under the name
`nkeys` so `nats-py`'s `nkeys_seed_str=` works unchanged.
"""
from __future__ import annotations

import base64
import sys
import types
from typing import Optional

PREFIX_SEED = 18 << 3        # 'S'
PREFIX_USER = 20 << 3        # 'U'
PREFIX_OPERATOR = 14 << 3    # 'O'
PREFIX_ACCOUNT = 0           # 'A'


class NKeyError(ValueError):
    """Not a well-formed NKey (bad base32, prefix or checksum)."""


def _crc16(data: bytes) -> int:
    """CRC-16/XMODEM, as nats-server computes it."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _encode(raw: bytes) -> str:
    crc = _crc16(raw)
    return base64.b32encode(raw + bytes([crc & 0xFF, crc >> 8])).decode().rstrip("=")


def _decode(text: str) -> bytes:
    text = (text or "").strip()
    try:
        raw = base64.b32decode(text + "=" * (-len(text) % 8))
    except (ValueError, TypeError) as e:
        raise NKeyError(f"not base32: {e}") from None
    if len(raw) < 3:
        raise NKeyError("too short")
    body, crc = raw[:-2], raw[-2] | (raw[-1] << 8)
    if _crc16(body) != crc:
        raise NKeyError("checksum mismatch")
    return body


def encode_public(raw_public: bytes, prefix: int = PREFIX_USER) -> str:
    return _encode(bytes([prefix]) + raw_public)


def encode_seed(raw_seed: bytes, prefix: int = PREFIX_USER) -> str:
    b1 = PREFIX_SEED | (prefix >> 5)
    b2 = (prefix & 31) << 3
    return _encode(bytes([b1, b2]) + raw_seed)


def decode_seed(seed: str) -> tuple[int, bytes]:
    """(the public prefix byte, the 32-byte Ed25519 seed)."""
    body = _decode(seed)
    if len(body) != 34 or (body[0] & 0xF8) != PREFIX_SEED:
        raise NKeyError("not a seed")
    prefix = ((body[0] & 7) << 5) | ((body[1] & 0xF8) >> 3)
    return prefix, body[2:]


def decode_public(public: str, prefix: Optional[int] = PREFIX_USER) -> bytes:
    """The 32 raw bytes of a public NKey, checking its kind."""
    body = _decode(public)
    if len(body) != 33 or (prefix is not None and body[0] != prefix):
        raise NKeyError("not a public key of the expected kind")
    return body[1:]


def _private(raw_seed: bytes):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.from_private_bytes(raw_seed)


def create_user() -> tuple[str, str]:
    """A new user NKey as (seed `SU...`, public `U...`)."""
    import secrets

    raw = secrets.token_bytes(32)
    return encode_seed(raw), public_of(encode_seed(raw))


def public_of(seed: str) -> str:
    from cryptography.hazmat.primitives import serialization

    prefix, raw = decode_seed(seed)
    pub = _private(raw).public_key().public_bytes(serialization.Encoding.Raw,
                                                  serialization.PublicFormat.Raw)
    return encode_public(pub, prefix)


def sign(seed: str, data: bytes) -> bytes:
    return _private(decode_seed(seed)[1]).sign(data)


def verify(public: str, data: bytes, signature: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(decode_public(public, None)).verify(
            signature, data)
        return True
    except (InvalidSignature, NKeyError, ValueError):
        return False


class KeyPair:
    """What `nats-py` asks `nkeys.from_seed` for."""

    def __init__(self, seed: bytes | bytearray | str) -> None:
        self._seed = bytes(seed).decode() if not isinstance(seed, str) else seed
        self.public_key = public_of(self._seed).encode()

    def sign(self, data: bytes) -> bytes:
        return sign(self._seed, data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        return verify(self.public_key.decode(), data, signature)

    def wipe(self) -> None:
        self._seed = ""


def install_nkeys_shim() -> None:
    """Make `import nkeys` resolve to this module's `from_seed`, unless the
    real package is installed."""
    try:
        import nkeys  # noqa: F401
        return
    except ImportError:
        pass
    mod = types.ModuleType("nkeys")
    mod.from_seed = KeyPair                      # type: ignore[attr-defined]
    mod.KeyPair = KeyPair                        # type: ignore[attr-defined]
    sys.modules["nkeys"] = mod
