"""Go-live preflight (P2-M0): is this deployment configured to post for real?

    python -m mera.preflight            # configuration only, no network
    python -m mera.preflight --live     # also probe the database, Telegram, SMTP, URLs

Every check reports ok, warn or fail with what to do next. Secrets are never printed:
only whether a value is set, and for Telegram the bot's public username. The exit code
is 1 when anything fails, so CI or a deploy script can gate on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import smtplib
import ssl
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx

from mera.config import Env, Settings

Status = Literal["ok", "warn", "fail"]

# Which platform app serves which Mera platforms (docs/08), for the callback list.
PLATFORM_APPS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "LinkedIn": (
        "linkedin_client_id",
        "linkedin_client_secret",
        ("linkedin_member", "linkedin_organization"),
    ),
    "Meta (Facebook, Instagram)": ("meta_app_id", "meta_app_secret", ("facebook", "instagram")),
    "Threads": ("threads_app_id", "threads_app_secret", ("threads",)),
    "X": ("x_client_id", "x_client_secret", ("x",)),
}

# LiteLLM reads provider keys from the environment by model prefix.
LLM_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


@dataclass(frozen=True)
class Check:
    area: str
    name: str
    status: Status
    detail: str


def _is_https(url: str) -> bool:
    return urlsplit(url).scheme == "https"


def _is_local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in {"localhost", "127.0.0.1", "::1"}


def callback_url(settings: Settings, platform: str) -> str:
    import os
    pub_api = os.environ.get("MERA_PUBLIC_API_URL")
    if pub_api:
        base = pub_api.rstrip("/").removesuffix("/api/v1")
    elif "api:" in settings.mera_api_url or "localhost" in settings.mera_api_url:
        base = settings.mera_public_url
    else:
        base = settings.mera_api_url
    return f"{base.rstrip('/')}/api/v1/social-accounts/oauth/{platform}/callback"


def webhook_url(settings: Settings) -> str:
    import os
    pub_api = os.environ.get("MERA_PUBLIC_API_URL")
    if pub_api:
        base = pub_api.rstrip("/").removesuffix("/api/v1")
    elif "api:" in settings.mera_api_url or "localhost" in settings.mera_api_url:
        base = settings.mera_public_url
    else:
        base = settings.mera_api_url
    return f"{base.rstrip('/')}/api/v1/webhooks/telegram"


def config_checks(settings: Settings, environ: Mapping[str, str] | None = None) -> list[Check]:
    """Offline checks of the loaded settings. Pure: no network, no database."""
    env = os.environ if environ is None else environ
    live = settings.mera_env is not Env.DEVELOPMENT
    out: list[Check] = []

    def add(area: str, name: str, status: Status, detail: str) -> None:
        out.append(Check(area, name, status, detail))

    # ---- mode ----
    if settings.sandbox_mode:
        add(
            "mode",
            "sandbox",
            "warn" if not live else "fail",
            "SANDBOX_MODE=true: posts go to the fake adapters and cards to /sandbox. "
            "Set SANDBOX_MODE=false to post for real.",
        )
    else:
        add("mode", "sandbox", "ok", "Sandbox is off: connected accounts post for real.")
    if settings.sandbox_llm:
        add(
            "mode",
            "llm",
            "warn",
            "SANDBOX_LLM=true: drafts come from the offline fake model, not a real one.",
        )
    else:
        needed = sorted(
            {
                m.split("/", 1)[0]
                for m in (
                    settings.research_model,
                    settings.writer_model,
                    settings.editor_model,
                    settings.hashtag_model,
                )
            }
        )
        missing = [LLM_KEYS[p] for p in needed if p in LLM_KEYS and not env.get(LLM_KEYS[p])]
        if missing:
            add(
                "mode",
                "llm",
                "fail",
                f"Models need {', '.join(missing)} in the environment (or set SANDBOX_LLM=true).",
            )
        else:
            add("mode", "llm", "ok", f"Model providers configured: {', '.join(needed)}.")

    # ---- public URLs ----
    for name, url in (
        ("MERA_PUBLIC_URL", settings.mera_public_url),
        ("MERA_API_URL", settings.mera_api_url),
    ):
        if _is_https(url):
            add("urls", name, "ok", url)
        elif _is_local(url):
            add(
                "urls",
                name,
                "fail" if live else "warn",
                f"{url} is only reachable from this machine; platforms and Telegram need a "
                "public HTTPS address (a tunnel works for testing).",
            )
        else:
            add("urls", name, "fail", f"{url} is not HTTPS; OAuth callbacks require HTTPS.")

    # ---- security ----
    if settings.web_proxy_secret is None:
        add(
            "security",
            "WEB_PROXY_SECRET",
            "fail" if live else "warn",
            "Unset: every browser shares the web server's address for rate limits and the "
            "audit log. Set MERA_WEB_PROXY_SECRET in .env (openssl rand -base64 32); Compose "
            "gives it to both api and web.",
        )
    else:
        add("security", "WEB_PROXY_SECRET", "ok", "Set.")
    if not settings.rate_limit_enabled:
        add("security", "rate limits", "fail" if live else "warn", "RATE_LIMIT_ENABLED=false.")

    # ---- email ----
    if settings.smtp_host:
        add(
            "email",
            "SMTP",
            "ok",
            f"{settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security}), "
            f"from {settings.smtp_from}.",
        )
    else:
        add(
            "email",
            "SMTP",
            "warn",
            (
                "No SMTP_HOST: sandbox keeps emails in memory and shows links in the app."
                if settings.sandbox_mode
                else "No SMTP_HOST: email verification is refused and invitation links must "
                "be shared by hand."
            ),
        )

    # ---- platform apps ----
    any_platform = False
    for label, (id_field, secret_field, platforms) in PLATFORM_APPS.items():
        has_id = bool(getattr(settings, id_field))
        has_secret = getattr(settings, secret_field) is not None
        urls = " ; ".join(callback_url(settings, p) for p in platforms)
        if has_id and has_secret:
            any_platform = True
            add("platforms", label, "ok", f"Register these redirect URIs: {urls}")
        elif has_id or has_secret:
            absent = secret_field if has_id else id_field
            add("platforms", label, "fail", f"{absent.upper()} is missing.")
        else:
            add("platforms", label, "warn", "Not configured: these accounts cannot be connected.")
    if not any_platform:
        add(
            "platforms",
            "any",
            "fail" if not settings.sandbox_mode else "warn",
            "No platform app is configured, so nothing can post for real.",
        )

    # ---- Telegram ----
    if settings.telegram_bot_token is None:
        add(
            "telegram",
            "bot",
            "warn",
            "No TELEGRAM_BOT_TOKEN: approval cards go to the web queue and /sandbox only.",
        )
    else:
        if not settings.telegram_bot_username:
            add(
                "telegram",
                "username",
                "warn",
                "TELEGRAM_BOT_USERNAME is unset; pairing deep links need it.",
            )
        if settings.telegram_mode == "webhook":
            add(
                "telegram",
                "mode",
                "ok",
                f"Webhook: route {webhook_url(settings)} to the bots service.",
            )
        else:
            add(
                "telegram",
                "mode",
                "warn" if not live else "fail",
                "Polling: fine on a laptop, not for a shared deployment.",
            )
    return out


# ---------------------------------------------------------------- live probes


def _alembic_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[2]
    ini = backend / "alembic.ini"
    if not ini.exists():
        return None
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def _database(settings: Settings) -> list[Check]:
    from sqlalchemy import text

    from mera.infrastructure.db.session import UnsafeDatabaseRole, assert_rls_bound, create_engine

    engine = create_engine(settings)
    try:
        try:
            await assert_rls_bound(engine)
        except UnsafeDatabaseRole as exc:
            return [Check("database", "role", "fail", str(exc))]
        async with engine.connect() as conn:
            current = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except Exception as exc:
        return [Check("database", "connect", "fail", f"{type(exc).__name__}: cannot connect.")]
    finally:
        await engine.dispose()
    head = _alembic_head()
    checks = [Check("database", "role", "ok", "Connected as an RLS-bound role.")]
    if head is None or current == head:
        checks.append(Check("database", "migrations", "ok", f"At revision {current}."))
    else:
        checks.append(
            Check(
                "database",
                "migrations",
                "fail",
                f"At {current}, code expects {head}: run migrations.",
            )
        )
    return checks


async def _telegram(settings: Settings, client: httpx.AsyncClient) -> list[Check]:
    if settings.telegram_bot_token is None:
        return []
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}"
    try:
        me = (await client.get(f"{base}/getMe")).json()
        info = (await client.get(f"{base}/getWebhookInfo")).json()
    except (httpx.HTTPError, ValueError):
        return [Check("telegram", "api", "fail", "Cannot reach api.telegram.org.")]
    if not me.get("ok"):
        return [Check("telegram", "token", "fail", "Telegram rejected the bot token.")]
    username = me["result"].get("username", "")
    checks = [Check("telegram", "token", "ok", f"Bot @{username}.")]
    if settings.telegram_bot_username and settings.telegram_bot_username.lstrip("@") != username:
        checks.append(
            Check(
                "telegram",
                "username",
                "fail",
                f"TELEGRAM_BOT_USERNAME does not match the token's bot @{username}.",
            )
        )
    hook = info.get("result", {}) if info.get("ok") else {}
    url = hook.get("url", "")
    if settings.telegram_mode == "webhook":
        if url != webhook_url(settings):
            checks.append(
                Check(
                    "telegram",
                    "webhook",
                    "fail",
                    f"Webhook is {url or 'not set'}; expected {webhook_url(settings)}. "
                    "See docs/runbooks/telegram-bot-setup.md.",
                )
            )
        elif hook.get("last_error_message"):
            checks.append(
                Check(
                    "telegram",
                    "webhook",
                    "warn",
                    f"Telegram's last delivery failed: {hook['last_error_message']}",
                )
            )
        else:
            checks.append(
                Check(
                    "telegram",
                    "webhook",
                    "ok",
                    f"Set; {hook.get('pending_update_count', 0)} updates pending.",
                )
            )
    elif url:
        checks.append(
            Check(
                "telegram",
                "webhook",
                "fail",
                "Polling mode, but a webhook is set; Telegram refuses getUpdates "
                "until it is deleted (deleteWebhook).",
            )
        )
    return checks


def _smtp_probe(settings: Settings, send_to: str | None) -> Check:
    assert settings.smtp_host is not None
    context = ssl.create_default_context()
    try:
        client: smtplib.SMTP
        if settings.smtp_security == "ssl":
            client = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=10, context=context
            )
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
        with client:
            if settings.smtp_security == "starttls":
                client.starttls(context=context)
            if settings.smtp_username:
                password = (
                    settings.smtp_password.get_secret_value() if settings.smtp_password else ""
                )
                client.login(settings.smtp_username, password)
            if send_to:
                client.sendmail(
                    settings.smtp_from or "",
                    [send_to],
                    f"From: {settings.smtp_from}\r\nTo: {send_to}\r\n"
                    "Subject: Mera preflight\r\n\r\nEmail delivery from Mera works.\r\n",
                )
    except (smtplib.SMTPException, OSError) as exc:
        return Check(
            "email", "relay", "fail", f"{type(exc).__name__}: the relay refused or is unreachable."
        )
    return Check(
        "email",
        "relay",
        "ok",
        f"Relay accepted the login{f'; test sent to {send_to}' if send_to else ''}.",
    )


async def _urls(settings: Settings, client: httpx.AsyncClient) -> list[Check]:
    checks = []
    for name, url in (
        ("api /healthz", f"{settings.mera_api_url.rstrip('/')}/healthz"),
        ("web /login", f"{settings.mera_public_url.rstrip('/')}/login"),
    ):
        try:
            r = await client.get(url)
            status: Status = "ok" if r.status_code == 200 else "fail"
            checks.append(Check("urls", name, status, f"{url} answered {r.status_code}."))
        except httpx.HTTPError as exc:
            checks.append(Check("urls", name, "fail", f"{url}: {type(exc).__name__}."))
    return checks


async def live_checks(
    settings: Settings,
    *,
    send_test_email: str | None = None,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> list[Check]:
    factory = client_factory or (lambda: httpx.AsyncClient(timeout=10, follow_redirects=False))
    checks = await _database(settings)
    async with factory() as client:
        checks += await _telegram(settings, client)
        checks += await _urls(settings, client)
    if settings.smtp_host:
        checks.append(await asyncio.to_thread(_smtp_probe, settings, send_test_email))
    return checks


# ---------------------------------------------------------------- CLI

_MARK = {"ok": "✓", "warn": "!", "fail": "✗"}


def render(checks: list[Check]) -> str:
    lines = []
    area = None
    for c in checks:
        if c.area != area:
            area = c.area
            lines.append(f"\n{area.upper()}")
        lines.append(f"  {_MARK[c.status]} {c.name}: {c.detail}")
    fails = sum(c.status == "fail" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    lines.append(f"\n{fails} failing, {warns} warnings, {len(checks) - fails - warns} ok")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mera.preflight",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="also probe the database, Telegram, the mail relay and the URLs",
    )
    parser.add_argument(
        "--send-test-email",
        metavar="ADDRESS",
        help="with --live: send one test email through the relay",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    from mera.config import get_settings

    settings = get_settings()
    checks = config_checks(settings)
    if args.live:
        checks += asyncio.run(live_checks(settings, send_test_email=args.send_test_email))
    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in {"utf-8", "utf8"}:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(render(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
