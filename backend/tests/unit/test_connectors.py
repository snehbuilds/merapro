"""The sandbox connector and the capability registry (no network, no database)."""

from __future__ import annotations

import base64
import os
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from mera.config import Settings
from mera.domain.ports.key_provider import DecryptionError
from mera.domain.value_objects.platform import AccountType, Platform
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.platforms.capabilities import CAPABILITIES, capabilities_for
from mera.infrastructure.platforms.connectors import (
    ConnectorError,
    FakeConnector,
    ReauthRequired,
    build_connectors,
    s256,
)


def test_every_platform_has_a_descriptor_and_phase_one_is_the_planned_six() -> None:
    assert set(CAPABILITIES) == set(Platform)
    phase_one = {p for p, c in CAPABILITIES.items() if c.phase == 1}
    assert phase_one == {
        Platform.LINKEDIN_MEMBER,
        Platform.LINKEDIN_ORGANIZATION,
        Platform.FACEBOOK,
        Platform.INSTAGRAM,
        Platform.THREADS,
        Platform.X,
    }
    x = capabilities_for(Platform.X)
    assert x.prohibits_duplicate_across_accounts
    assert x.rate_limit_shared_across_accounts
    assert capabilities_for(Platform.INSTAGRAM).requires_media
    assert capabilities_for(Platform.BLUESKY).counts_graphemes


async def test_fake_connector_round_trip_and_failure_modes() -> None:
    c = FakeConnector(Platform.X)
    verifier = "v" * 43
    url = c.authorization_url(
        state="st", code_challenge=s256(verifier), redirect_uri="https://api/cb"
    )
    assert parse_qs(urlsplit(url).query)["state"] == ["st"]
    ident = await c.exchange(
        code="fake:x-1:alex:business", code_verifier=verifier, redirect_uri="x"
    )
    assert (ident.platform_account_id, ident.account_type) == ("x-1", AccountType.BUSINESS)
    # Single use, and the verifier must match.
    with pytest.raises(ConnectorError, match="PKCE"):
        await c.exchange(code="fake:x-1:alex:business", code_verifier=verifier, redirect_uri="x")
    for code in ("nope", "fake:x-2:h:page", "fake:x-2:h:martian", "other:x:h:personal"):
        c.authorization_url(state=code, code_challenge=s256(verifier), redirect_uri="x")
        with pytest.raises(ConnectorError):
            await c.exchange(code=code, code_verifier=verifier, redirect_uri="x")
    creds, expires = await c.refresh(ident.credentials)
    assert creds["access_token"] != ident.credentials["access_token"]
    assert expires is not None
    assert (await c.check_health(creds)).token_valid
    with pytest.raises(ReauthRequired):
        await c.refresh({"handle": "revoked_one"})
    with pytest.raises(ReauthRequired):
        await c.check_health({"handle": "revoked_one"})
    await c.revoke(creds)
    assert c.revoked == ["alex"]


def _settings(**kw: object) -> Settings:
    base = {
        "mera_env": "development",
        "mera_secret_key": "s" * 40,
        "mera_master_key": base64.b64encode(os.urandom(32)).decode(),
        "database_url": "postgresql+asyncpg://u:p@h/db",
    }
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[arg-type]


def test_fakes_only_in_development_sandbox() -> None:
    assert set(build_connectors(_settings(sandbox_mode=True))) == set(Platform)
    # Bluesky (app password) and Mastodon (per-instance app) need no operator credentials,
    # so they are real outside sandbox; every other platform waits for its app keys (P2-M3).
    open_ = {Platform.BLUESKY, Platform.MASTODON}
    assert set(build_connectors(_settings(sandbox_mode=False))) == open_
    assert set(build_connectors(_settings(mera_env="staging", sandbox_mode=False))) == open_


def test_record_binding_in_envelope() -> None:
    kp = LocalKeyProvider({1: os.urandom(32)}, 1)
    ws, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = kp.encrypt(b"creds", workspace_id=ws, purpose="p", record_id=a)
    assert kp.decrypt(token, workspace_id=ws, purpose="p", record_id=a) == b"creds"
    for other in (b, None):
        with pytest.raises(DecryptionError):
            kp.decrypt(token, workspace_id=ws, purpose="p", record_id=other)
