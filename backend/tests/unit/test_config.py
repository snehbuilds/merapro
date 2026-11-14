from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from mera.config import EXAMPLE_MASTER_KEY, Env, Settings

GOOD = {
    "mera_env": "development",
    "mera_secret_key": "s" * 40,
    "mera_master_key": base64.b64encode(bytes(range(32))).decode(),
    "database_url": "postgresql+asyncpg://u:p@h/db",
    "web_proxy_secret": "p" * 32,
}


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**GOOD, **overrides})  # type: ignore[arg-type]


def test_valid_settings_load() -> None:
    s = make()
    assert s.sandbox_mode is True
    assert s.libpq_dsn == "postgresql://u:p@h/db"
    assert s.migration_url == s.database_url


def test_short_secret_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        make(mera_secret_key="short")


@pytest.mark.parametrize(
    "key",
    ["not-base64!!", base64.b64encode(b"x" * 16).decode(), base64.b64encode(b"x" * 33).decode()],
)
def test_master_key_must_be_32_bytes_base64(key: str) -> None:
    with pytest.raises(ValidationError, match="MERA_MASTER_KEY"):
        make(mera_master_key=key)


def test_non_asyncpg_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        make(database_url="postgresql://u:p@h/db")


def test_production_refuses_sandbox_mode() -> None:
    with pytest.raises(ValidationError, match="SANDBOX_MODE"):
        make(mera_env=Env.PRODUCTION, sandbox_mode=True)


def test_production_refuses_example_master_key() -> None:
    with pytest.raises(ValidationError, match="example value"):
        make(mera_env=Env.PRODUCTION, sandbox_mode=False, mera_master_key=EXAMPLE_MASTER_KEY)


def test_production_with_real_keys_boots() -> None:
    s = make(mera_env=Env.PRODUCTION, sandbox_mode=False)
    assert s.mera_env is Env.PRODUCTION


def test_mera_env_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MERA_ENV", raising=False)
    fields = {k: v for k, v in GOOD.items() if k != "mera_env"}
    with pytest.raises(ValidationError, match="mera_env"):
        Settings(_env_file=None, **fields)  # type: ignore[arg-type]


def test_production_refuses_disabled_rate_limits() -> None:
    with pytest.raises(ValidationError, match="RATE_LIMIT_ENABLED"):
        make(mera_env=Env.PRODUCTION, sandbox_mode=False, rate_limit_enabled=False)


def test_staging_refuses_sandbox_and_example_keys() -> None:
    with pytest.raises(ValidationError, match="staging"):
        make(mera_env=Env.STAGING, sandbox_mode=True)
    with pytest.raises(ValidationError, match="example value"):
        make(mera_env=Env.STAGING, mera_master_key=EXAMPLE_MASTER_KEY)


def test_sandbox_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    assert make().sandbox_mode is False


def test_blank_secrets_count_as_unset() -> None:
    # Compose passes "" for an unset variable; an empty secret must not match an
    # empty header.
    s = make(web_proxy_secret="", telegram_webhook_secret="  ", smtp_password="")
    assert s.web_proxy_secret is None
    assert s.telegram_webhook_secret is None
    assert s.smtp_password is None


def test_short_proxy_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="WEB_PROXY_SECRET must be at least 16"):
        make(web_proxy_secret="short")


def test_production_requires_the_proxy_secret() -> None:
    with pytest.raises(ValidationError, match="WEB_PROXY_SECRET is required in production"):
        make(mera_env=Env.PRODUCTION, sandbox_mode=False, web_proxy_secret=None)


def test_smtp_settings() -> None:
    with pytest.raises(ValidationError, match="SMTP_FROM is required"):
        make(smtp_host="relay")
    with pytest.raises(ValidationError, match="SMTP_SECURITY=none"):
        make(
            mera_env=Env.PRODUCTION,
            sandbox_mode=False,
            smtp_host="relay",
            smtp_from="m@example.com",
            smtp_security="none",
        )
    s = make(smtp_host="relay", smtp_from="m@example.com", smtp_security="ssl", smtp_port=465)
    assert s.smtp_port == 465
