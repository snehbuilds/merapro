"""``KeyProvider`` port (docs/01 §5.6, docs/09 §4.2).

Encrypts secrets at rest. Every ciphertext is bound by its associated data to a workspace
and a purpose, so a credential copied from one workspace's row into another's — or from a
social account into an approval channel — fails to decrypt rather than being used.
"""

from __future__ import annotations

import uuid
from typing import Protocol


class DecryptionError(Exception):
    """Wrong workspace, wrong purpose, unknown key version, or tampered ciphertext. The
    cause is deliberately not distinguished."""


class KeyProvider(Protocol):
    def encrypt(
        self,
        plaintext: bytes,
        *,
        workspace_id: uuid.UUID | None,
        purpose: str,
        record_id: uuid.UUID | None = None,
    ) -> str: ...

    def decrypt(
        self,
        token: str,
        *,
        workspace_id: uuid.UUID | None,
        purpose: str,
        record_id: uuid.UUID | None = None,
    ) -> bytes: ...

    def needs_rotation(self, token: str) -> bool:
        """True when the token was sealed under an older master-key version."""
        ...
