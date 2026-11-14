"""Go-live preflight (P2-M0): configuration findings, live probes against mocks, and that
no secret ever reaches the output."""

from __future__ import annotations

import base64
import json
import smtplib
from typing import Any

import httpx
import pytest

from mera import preflight
from mera.config import Settings
from mera.preflight import Check, config_checks, live_checks, render

GOOD: dict[str, Any] = {
    "mera_env": "development",
    "mera_secret_key": "s" * 40,
    "mera_master_key": base64.b64encode(bytes(range(32))).decode(),
    "database_url": "postgresql+asyncpg://u:p@h/db",
}
TOKEN = "123456:SECRET-bot-token-value"
LIVE = {
    "mera_env": "production",
    "sandbox_mode": False,
    "sandbox_llm": False,
    "mera_public_url": "https://mera.example.com",
    "mera_api_url": "https://api.mera.example.com",
    "web_proxy_secret": "p" * 32,
    "smtp_host": "relay.example.com",
    "smtp_from": "Mera <mera@example.com>",
    "smtp_username": "mailer",
    "smtp_password": "smtp-password-value",
    "linkedin_client_id": "li-id",
    "linkedin_client_secret": "li-secret-value",
    "telegram_bot_token": TOKEN,
    "telegram_bot_username": "mera_bot",
    "telegram_webhook_secret": "w" * 32,
}
KEYS = {"ANTHROPIC_API_KEY": "sk-ant-value", "OPENAI_API_KEY": "sk-openai-value"}


def make(**over: object) -> Settings:
    return Settings(_env_file=None, **{**GOOD, **over})  # type: ignore[arg-type]


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {f"{c.area}/{c.name}": c for c in checks}


def test_a_ready_production_config_has_no_failures() -> None:
    checks = by_name(config_checks(make(**LIVE), KEYS))
    assert [k for k, c in checks.items() if c.status == "fail"] == []
    li = checks["platforms/LinkedIn"]
    assert li.status == "ok"
    assert (
        "https://api.mera.example.com/api/v1/social-accounts/oauth/linkedin_organization/callback"
        in li.detail
    )
    assert checks["telegram/mode"].detail.endswith("/api/v1/webhooks/telegram to the bots service.")
    assert checks["platforms/X"].status == "warn"  # not configured is a warning, not a failure


def test_sandbox_defaults_only_warn() -> None:
    checks = config_checks(make(sandbox_mode=True, sandbox_llm=True), {})
    assert {c.status for c in checks} == {"warn"}


@pytest.mark.parametrize(
    ("over", "env", "key", "fragment"),
    [
        ({"linkedin_client_secret": None}, KEYS, "platforms/LinkedIn", "LINKEDIN_CLIENT_SECRET"),
        ({}, {"OPENAI_API_KEY": "x"}, "mode/llm", "ANTHROPIC_API_KEY"),
        ({"mera_api_url": "http://api.mera.example.com"}, KEYS, "urls/MERA_API_URL", "not HTTPS"),
        ({"telegram_mode": "polling"}, KEYS, "telegram/mode", "Polling"),
    ],
)
def test_production_findings(
    over: dict[str, object], env: dict[str, str], key: str, fragment: str
) -> None:
    # Production refuses some of these at load time; check them in staging, which shares
    # the live rules here.
    settings = make(**{**LIVE, "mera_env": "staging", **over})
    check = by_name(config_checks(settings, env))[key]
    assert check.status == "fail"
    assert fragment in check.detail


def test_no_platform_at_all_fails_outside_sandbox() -> None:
    settings = make(**{**LIVE, "linkedin_client_id": None, "linkedin_client_secret": None})
    assert by_name(config_checks(settings, KEYS))["platforms/any"].status == "fail"


# ---------------------------------------------------------------- live probes


def _telegram_and_urls(
    webhook_url: str,
    *,
    me_ok: bool = True,
    username: str = "mera_bot",
    last_error: str | None = None,
    web_status: int = 200,
):  # type: ignore[no-untyped-def]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        path = request.url.path
        if path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": me_ok, "result": {"username": username}})
        if path.endswith("/getWebhookInfo"):
            result: dict[str, Any] = {"url": webhook_url, "pending_update_count": 2}
            if last_error:
                result["last_error_message"] = last_error
            return httpx.Response(200, json={"ok": True, "result": result})
        if path == "/healthz":
            return httpx.Response(200)
        return httpx.Response(web_status)

    factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # noqa: E731
    return factory, seen


@pytest.fixture
def no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_db(settings: Settings) -> list[Check]:
        return [Check("database", "role", "ok", "stub")]

    monkeypatch.setattr(preflight, "_database", fake_db)


@pytest.fixture
def smtp_ok(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, *a: object, **k: object) -> None:
            calls.append("connect")

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *e: object) -> None:
            calls.append("quit")

        def starttls(self, context: object) -> None:
            calls.append("starttls")

        def login(self, user: str, password: str) -> None:
            calls.append(f"login:{user}")

        def sendmail(self, sender: str, to: list[str], msg: str) -> None:
            calls.append(f"send:{to[0]}")

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return calls


async def test_live_probes_all_green(no_db: None, smtp_ok: list[str]) -> None:
    settings = make(**{**LIVE, "mera_env": "staging"})
    factory, seen = _telegram_and_urls("https://api.mera.example.com/api/v1/webhooks/telegram")
    checks = by_name(await live_checks(settings, client_factory=factory, send_test_email="me@x.io"))
    assert checks["telegram/token"].detail == "Bot @mera_bot."
    assert checks["telegram/webhook"].status == "ok"
    assert checks["urls/api /healthz"].status == "ok"
    assert checks["urls/web /login"].status == "ok"
    assert checks["email/relay"].status == "ok"
    assert smtp_ok == ["connect", "starttls", "login:mailer", "send:me@x.io", "quit"]
    assert "https://mera.example.com/login" in seen


async def test_live_probes_find_the_usual_mistakes(no_db: None, smtp_ok: list[str]) -> None:
    settings = make(**{**LIVE, "mera_env": "staging"})
    factory, _ = _telegram_and_urls("", username="other_bot", web_status=502)
    checks = by_name(await live_checks(settings, client_factory=factory))
    assert checks["telegram/username"].status == "fail"
    assert "not set" in checks["telegram/webhook"].detail
    assert checks["urls/web /login"].status == "fail"

    factory, _ = _telegram_and_urls(
        "https://api.mera.example.com/api/v1/webhooks/telegram", last_error="Wrong response 502"
    )
    checks = by_name(await live_checks(settings, client_factory=factory))
    assert checks["telegram/webhook"].status == "warn"

    factory, _ = _telegram_and_urls("", me_ok=False)
    checks = by_name(await live_checks(settings, client_factory=factory))
    assert checks["telegram/token"].status == "fail"


async def test_polling_with_a_webhook_left_set_fails(no_db: None, smtp_ok: list[str]) -> None:
    settings = make(**{**LIVE, "mera_env": "development", "telegram_mode": "polling"})
    factory, _ = _telegram_and_urls("https://old.example.com/hook")
    checks = by_name(await live_checks(settings, client_factory=factory))
    assert "deleteWebhook" in checks["telegram/webhook"].detail


async def test_unreachable_relay_and_telegram(no_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*a: object, **k: object) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(smtplib, "SMTP", refuse)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(down))  # noqa: E731
    checks = by_name(
        await live_checks(make(**{**LIVE, "mera_env": "staging"}), client_factory=factory)
    )
    assert checks["telegram/api"].status == "fail"
    assert checks["urls/api /healthz"].status == "fail"
    assert checks["email/relay"].status == "fail"


async def test_secrets_never_reach_the_output(
    no_db: None,
    smtp_ok: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make(**{**LIVE, "mera_env": "staging"})
    factory, _ = _telegram_and_urls("")
    checks = config_checks(settings, KEYS) + await live_checks(settings, client_factory=factory)
    text = render(checks) + json.dumps([c.__dict__ for c in checks])
    for secret in (
        TOKEN,
        "li-secret-value",
        "smtp-password-value",
        "p" * 32,
        "w" * 32,
        "sk-ant-value",
        "SECRET-bot",
    ):
        assert secret not in text


def test_cli_exit_code(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        "mera.config.get_settings", lambda: make(sandbox_mode=True, sandbox_llm=True)
    )
    assert preflight.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["area"] == "mode"
    monkeypatch.setattr(
        "mera.config.get_settings",
        lambda: make(sandbox_mode=True, sandbox_llm=True, linkedin_client_id="only-id"),
    )
    assert preflight.main([]) == 1
    assert "LINKEDIN_CLIENT_SECRET is missing" in capsys.readouterr().out
