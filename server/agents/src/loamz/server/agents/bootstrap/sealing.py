"""AES-256-GCM sealing of chat content and stored tokens, with a key ring so keys can be rotated.

A value is stored as ``sealed:v1:<key id>:<base64url(nonce + ciphertext)>``. Each scope (a chat, a user's token) gets its own key,
derived from a master key with HKDF, and the field's name is authenticated, so a sealed value moved to another row or another chat
fails to open. To rotate: add a new key id, make it primary, and leave the old ones in the ring until what they sealed is rewritten.

This keeps content out of the database, backups and replicas. The process holding the keys can read it; end-to-end encryption
(docs/security/e2ee-threat-model.md) is a different guarantee. Keys come from the environment today; wrapping them with the
organization's KMS is the next step.
"""

from __future__ import annotations

import base64
import binascii
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from loamz.engine.agents.kernel.runtime.sealing import PlainSealer, Sealer, SealError

PREFIX = "sealed:v1:"
SALT = b"loamz.sealing.v1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class AesSealer:
    def __init__(self, keys: dict[str, str], primary: str = "") -> None:
        if not keys:
            raise ValueError("At least one sealing key is needed")
        self._masters: dict[str, bytes] = {}
        for kid, secret in keys.items():
            if ":" in kid or not kid:
                raise ValueError("A sealing key id is a short name without ':'")
            raw = _unb64(secret)
            if len(raw) < 32:
                raise ValueError(
                    f"Sealing key {kid!r} must be at least 32 random bytes (base64url)"
                )
            self._masters[kid] = raw
        self._primary = primary or next(iter(keys))
        if self._primary not in self._masters:
            raise ValueError("The primary sealing key is not in the key ring")
        self._derive = lru_cache(maxsize=4096)(self._derive_uncached)

    def _derive_uncached(self, kid: str, scope: str) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=SALT, info=scope.encode()).derive(
            self._masters[kid]
        )

    def seal(self, scope: str, where: str, value: str) -> str:
        nonce = os.urandom(12)
        box = AESGCM(self._derive(self._primary, scope)).encrypt(
            nonce, value.encode(), f"{scope}|{where}".encode()
        )
        return f"{PREFIX}{self._primary}:{_b64(nonce + box)}"

    def open(self, scope: str, where: str, value: str) -> str:
        if not value.startswith(PREFIX):
            return value  # written before sealing was switched on
        try:
            kid, body = value[len(PREFIX) :].split(":", 1)
            raw = _unb64(body)
            if kid not in self._masters or len(raw) < 28:
                raise SealError("This value was sealed with a key that is not in the key ring")
            return (
                AESGCM(self._derive(kid, scope))
                .decrypt(raw[:12], raw[12:], f"{scope}|{where}".encode())
                .decode()
            )
        except (InvalidTag, binascii.Error, ValueError) as error:
            if isinstance(error, SealError):
                raise
            raise SealError(
                "This value cannot be opened: wrong key, wrong place or damaged"
            ) from None


def new_key() -> str:
    """A fresh 256-bit master key, to put in the key ring."""
    return _b64(os.urandom(32))


def sealer_from(keys: dict[str, str], primary: str = "") -> Sealer:
    keys = {kid: secret for kid, secret in keys.items() if secret}  # an unset variable is no key
    return AesSealer(keys, primary if primary in keys else "") if keys else PlainSealer()
