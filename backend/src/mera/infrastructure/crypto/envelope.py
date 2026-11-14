"""Envelope encryption over the master key (M2.11, docs/09 §4.2).

- KEK per master-key version: HKDF-SHA256(master, info="mera/kek/v{n}").
- Each secret gets a fresh random 256-bit DEK; the DEK is sealed with the KEK and the data
  with the DEK, both AES-256-GCM, both under the same associated data.
- AAD = ``mera/v1|{workspace_id or "global"}|{purpose}``: the binding that makes a secret
  undecryptable anywhere but where it was written.
- The token records the key version, so rotation is lazy: decrypt with any version on the
  keyring, re-encrypt with the current one when ``needs_rotation`` says so.

Token format: ``mera:enc:1:{key_version}:{b64(nonce‖sealed DEK)}:{b64(nonce‖ciphertext)}``.
"""

from __future__ import annotations

import base64
import os
import re
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from mera.domain.ports.key_provider import DecryptionError

_PREFIX = "mera:enc:1"
_NONCE = 12
_PURPOSE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _aad(workspace_id: uuid.UUID | None, purpose: str, record_id: uuid.UUID | None) -> bytes:
    if not _PURPOSE.match(purpose):
        raise ValueError(f"invalid purpose {purpose!r}")
    scope = str(workspace_id) if workspace_id is not None else "global"
    aad = f"mera/v1|{scope}|{purpose}"
    # Binding the row means a ciphertext copied onto another row of the same workspace and
    # purpose (another account's credentials) will not decrypt there.
    if record_id is not None:
        aad += f"|{record_id}"
    return aad.encode()


def _kek(master: bytes, version: int) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=f"mera/kek/v{version}".encode()
    ).derive(master)


class LocalKeyProvider:
    """Keys derived from ``MERA_MASTER_KEY``. Production KMS/Vault is Phase 4 (docs/09)."""

    def __init__(self, keyring: dict[int, bytes], current_version: int) -> None:
        if current_version not in keyring:
            raise ValueError("current key version is not on the keyring")
        for version, master in keyring.items():
            if len(master) != 32:
                raise ValueError(f"master key v{version} must be 32 bytes")
        self._keks = {v: _kek(m, v) for v, m in keyring.items()}
        self._current = current_version

    @classmethod
    def from_base64(cls, master_key_b64: str, *, version: int = 1) -> LocalKeyProvider:
        return cls({version: base64.b64decode(master_key_b64)}, version)

    def encrypt(
        self,
        plaintext: bytes,
        *,
        workspace_id: uuid.UUID | None,
        purpose: str,
        record_id: uuid.UUID | None = None,
    ) -> str:
        aad = _aad(workspace_id, purpose, record_id)
        dek = AESGCM.generate_key(bit_length=256)
        n1, n2 = os.urandom(_NONCE), os.urandom(_NONCE)
        sealed_dek = AESGCM(self._keks[self._current]).encrypt(n1, dek, aad)
        ciphertext = AESGCM(dek).encrypt(n2, plaintext, aad)
        return f"{_PREFIX}:{self._current}:{_b64e(n1 + sealed_dek)}:{_b64e(n2 + ciphertext)}"

    def decrypt(
        self,
        token: str,
        *,
        workspace_id: uuid.UUID | None,
        purpose: str,
        record_id: uuid.UUID | None = None,
    ) -> bytes:
        aad = _aad(workspace_id, purpose, record_id)
        try:
            prefix_a, prefix_b, fmt, version_s, dek_part, data_part = token.split(":")
            if f"{prefix_a}:{prefix_b}:{fmt}" != _PREFIX:
                raise ValueError("unknown format")
            kek = self._keks[int(version_s)]
            dek_blob, data_blob = _b64d(dek_part), _b64d(data_part)
            dek = AESGCM(kek).decrypt(dek_blob[:_NONCE], dek_blob[_NONCE:], aad)
            return AESGCM(dek).decrypt(data_blob[:_NONCE], data_blob[_NONCE:], aad)
        except (ValueError, KeyError, InvalidTag) as exc:
            raise DecryptionError("ciphertext cannot be decrypted here") from exc

    def needs_rotation(self, token: str) -> bool:
        try:
            return int(token.split(":")[3]) != self._current
        except (IndexError, ValueError):
            return True
