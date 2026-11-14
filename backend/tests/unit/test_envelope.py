"""Envelope encryption (crypto is a 100% area). M2 acceptance: a secret encrypted for
workspace A fails to decrypt under workspace B's AAD."""

from __future__ import annotations

import base64
import os
import uuid

import pytest

from mera.domain.ports.key_provider import DecryptionError
from mera.infrastructure.crypto.envelope import LocalKeyProvider

WS_A, WS_B = uuid.uuid4(), uuid.uuid4()
K1, K2 = os.urandom(32), os.urandom(32)


@pytest.fixture
def kp() -> LocalKeyProvider:
    return LocalKeyProvider({1: K1}, 1)


def test_roundtrip(kp: LocalKeyProvider) -> None:
    token = kp.encrypt(b"oauth-secret", workspace_id=WS_A, purpose="social_account.credentials")
    assert token.startswith("mera:enc:1:1:")
    assert b"oauth-secret" not in token.encode()
    assert (
        kp.decrypt(token, workspace_id=WS_A, purpose="social_account.credentials")
        == b"oauth-secret"
    )


def test_each_encryption_is_unique(kp: LocalKeyProvider) -> None:
    a = kp.encrypt(b"same", workspace_id=WS_A, purpose="p")
    b = kp.encrypt(b"same", workspace_id=WS_A, purpose="p")
    assert a != b


def test_workspace_b_cannot_decrypt_workspace_a(kp: LocalKeyProvider) -> None:
    token = kp.encrypt(b"secret", workspace_id=WS_A, purpose="p")
    with pytest.raises(DecryptionError):
        kp.decrypt(token, workspace_id=WS_B, purpose="p")
    with pytest.raises(DecryptionError):
        kp.decrypt(token, workspace_id=None, purpose="p")


def test_wrong_purpose_cannot_decrypt(kp: LocalKeyProvider) -> None:
    token = kp.encrypt(b"secret", workspace_id=WS_A, purpose="approval_channel.credentials")
    with pytest.raises(DecryptionError):
        kp.decrypt(token, workspace_id=WS_A, purpose="social_account.credentials")


def test_global_scope_roundtrip(kp: LocalKeyProvider) -> None:
    token = kp.encrypt(b"x", workspace_id=None, purpose="user.totp")
    assert kp.decrypt(token, workspace_id=None, purpose="user.totp") == b"x"


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t[:-2] + ("A" if t[-2] != "A" else "B") + t[-1],  # flip ciphertext
        lambda t: t.replace("mera:enc:1", "mera:enc:9"),
        lambda t: t.replace(":1:", ":7:", 1),  # unknown key version
        lambda t: "garbage",
        lambda t: t + ":extra",
    ],
)
def test_tampering_is_detected(kp: LocalKeyProvider, mangle) -> None:  # type: ignore[no-untyped-def]
    token = kp.encrypt(b"secret", workspace_id=WS_A, purpose="p")
    with pytest.raises(DecryptionError):
        kp.decrypt(mangle(token), workspace_id=WS_A, purpose="p")


def test_lazy_rotation() -> None:
    old = LocalKeyProvider({1: K1}, 1)
    token = old.encrypt(b"secret", workspace_id=WS_A, purpose="p")
    new = LocalKeyProvider({1: K1, 2: K2}, 2)
    assert new.needs_rotation(token)
    assert new.decrypt(token, workspace_id=WS_A, purpose="p") == b"secret"
    fresh = new.encrypt(b"secret", workspace_id=WS_A, purpose="p")
    assert not new.needs_rotation(fresh)
    assert new.needs_rotation("garbage")
    # A provider without the old key cannot read it.
    with pytest.raises(DecryptionError):
        LocalKeyProvider({2: K2}, 2).decrypt(token, workspace_id=WS_A, purpose="p")


def test_from_base64_and_validation() -> None:
    kp = LocalKeyProvider.from_base64(base64.b64encode(K1).decode())
    assert (
        kp.decrypt(kp.encrypt(b"y", workspace_id=WS_A, purpose="p"), workspace_id=WS_A, purpose="p")
        == b"y"
    )
    with pytest.raises(ValueError, match="not on the keyring"):
        LocalKeyProvider({1: K1}, 2)
    with pytest.raises(ValueError, match="32 bytes"):
        LocalKeyProvider({1: b"short"}, 1)
    with pytest.raises(ValueError, match="invalid purpose"):
        kp.encrypt(b"z", workspace_id=WS_A, purpose="Bad|Purpose")
