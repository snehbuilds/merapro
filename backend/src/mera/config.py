"""Process configuration.

Fails at startup on anything missing or malformed (docs/10 §4). A service that boots with a
weak master key and discovers it at the first encrypt call is worse than one that refuses
to boot.
"""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value shipped in .env.example. Production refuses to boot with it.
# base64 of a readable 32-byte phrase, so it is plainly not a real key.
EXAMPLE_MASTER_KEY = base64.b64encode(b"example-key-do-not-use-in-prod!!").decode()
EXAMPLE_SECRET_KEY = "dev-only-secret-key-change-me-0123456789abcdef"  # noqa: S105


class Env(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ---- core ----
    # Required, no default: a production deploy that forgets it must not boot as development
    # (sandbox on, example keys accepted).
    mera_env: Env
    mera_secret_key: SecretStr
    mera_master_key: SecretStr
    mera_public_url: str = "http://localhost:3000"
    mera_api_url: str = "http://localhost:8000"

    # ---- database ----
    # The app connects as a non-superuser role so RLS applies (ADR-0006).
    database_url: str
    # Owner role used only by migrations. Falls back to database_url when unset.
    database_migration_url: str | None = None
    database_pool_size: int = Field(default=10, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)

    # ---- sandbox ----
    sandbox_mode: bool = False  # .env.example turns it on for local work
    sandbox_llm: bool = False
    sandbox_media: bool = True

    # ---- media (M5.14) ----
    # Where the filesystem object store keeps uploads and renditions (a volume in Compose).
    media_root: str = "var/media"

    # ---- OAuth login (unset = provider disabled; sandbox uses a fake) ----
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: SecretStr | None = None

    # ---- platform apps (M8; unset = that platform cannot be connected outside sandbox) ----
    linkedin_client_id: str | None = None
    linkedin_client_secret: SecretStr | None = None
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    threads_app_id: str | None = None
    threads_app_secret: SecretStr | None = None
    x_client_id: str | None = None
    x_client_secret: SecretStr | None = None
    # Sandbox mode publishes through the fake adapter even when real apps are configured.
    publish_poll_seconds: int = Field(default=30, ge=5, le=600)

    # ---- approval bot (M7) ----
    # One bot serves every workspace (docs/07 §6). Unset: Telegram is unavailable and the
    # sandbox channel records cards for /sandbox instead.
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    # Sent by Telegram in X-Telegram-Bot-Api-Secret-Token; required when a token is set.
    telegram_webhook_secret: SecretStr | None = None
    # "webhook" in production; "polling" for local development without public HTTPS (M7.16).
    telegram_mode: str = Field(default="webhook", pattern="^(webhook|polling)$")

    # ---- WhatsApp approvals (P2-M4; docs/runbooks/whatsapp-business-setup.md) ----
    # Unset: WhatsApp is unavailable. All four are needed together.
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None  # verifies X-Hub-Signature-256
    whatsapp_verify_token: SecretStr | None = None  # the webhook subscription handshake
    whatsapp_template_language: str = "en"
    # The Cloud API base. Change only to point a test stack at a fake (apps/web/e2e).
    whatsapp_graph_url: str = "https://graph.facebook.com/v23.0"
    # The business number as people dial it (digits only), for the wa.me pairing link.
    whatsapp_display_number: str | None = None
    # Business-initiated (template) messages per calendar month, across the deployment.
    whatsapp_monthly_template_cap: int = Field(default=1000, ge=0)

    # ---- media generation (P2-M5; docs/06 §4.5) ----
    # fake: sandbox only (never outside it). openai: any OpenAI-compatible images API.
    media_provider: str = Field(default="fake", pattern="^(fake|openai|none)$")
    media_api_key: SecretStr | None = None
    media_model: str = "gpt-image-1"
    media_base_url: str = "https://api.openai.com/v1"
    # Per image, square, by quality; non-square costs 1.5x. Re-verify against the invoice.
    media_price_low_usd: float = Field(default=0.011, ge=0)
    media_price_medium_usd: float = Field(default=0.042, ge=0)
    media_price_high_usd: float = Field(default=0.167, ge=0)
    media_job_timeout_s: int = Field(default=180, ge=10, le=1800)
    # Workspace defaults; an Admin changes them per workspace in Settings.
    media_request_ceiling_usd: float = Field(default=0.25, ge=0)
    media_monthly_budget_usd: float = Field(default=20.0, ge=0)
    # Video generation is off until a video provider is wired (docs/13 §2.5).
    media_video_enabled: bool = False
    # The Media agent writes the prompt and the alt text (a small model is enough).
    media_prompt_model: str = "openai/gpt-5-mini"
    media_prompt_fallbacks: list[str] = Field(
        default_factory=lambda: ["anthropic/claude-haiku-4-5"]
    )

    # ---- engagement (P2-M6) ----
    engagement_model: str = "openai/gpt-5-mini"
    engagement_fallbacks: list[str] = Field(default_factory=lambda: ["anthropic/claude-haiku-4-5"])
    # New comments triaged by the agent per workspace per poll; the rest wait for the next.
    engagement_triage_per_poll: int = Field(default=50, ge=0, le=1000)

    # ---- Mera's MCP server (P2-M8; docs/05 §1) ----
    # Shared by the mcp service and the API: proves a request came through the mcp service,
    # the only place an MCP OAuth token is accepted. 16+ characters.
    mcp_service_secret: SecretStr | None = None
    # The MCP endpoint as clients see it: the OAuth resource (RFC 8707) tokens are bound to.
    mcp_resource_url: str = "http://localhost:8002/mcp"
    # Origins allowed to call the Streamable HTTP endpoint from a browser (docs/05 §1.1).
    mcp_allowed_origins: list[str] = Field(default_factory=list)
    # The mcp-sandbox relay that runs stdio servers (P2-M9.4); never the api host.
    mcp_sandbox_url: str = "http://mcp-sandbox:8010"
    # Proves a call to the relay comes from the API or a worker (16+ characters); the
    # relay holds the same value. Unset: stdio servers cannot be started.
    mcp_sandbox_token: SecretStr | None = None
    # mcp-egress: the grant API the API and workers register each server's allowlist
    # with, and the proxy address a sandboxed server is given. Unset admin token: stdio
    # servers run with no egress at all.
    mcp_egress_admin_url: str = "http://mcp-egress:3129"
    mcp_egress_admin_token: SecretStr | None = None
    mcp_egress_proxy: str = "mcp-egress:3128"
    # P3-M6.1: the public MCP Registry's server list, searched by the catalog. Off when
    # unset; the curated list always works.
    mcp_registry_search_url: str | None = None

    # ---- outbound email (Notifier) ----
    # Unset: verification links are refused outside sandbox and invitation links are only
    # shown to the inviter. Any SMTP relay works (corporate relay, SES SMTP, a provider).
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None  # e.g. "Mera <mera@example.com>"

    # ---- team security (P2-M2) ----
    # Admins and Owners must use two-step sign-in; until they enrol they act as Editors.
    # Unset: enforced outside development.
    mfa_required_for_admins: bool | None = None

    # ---- limits ----
    # Off only in tests that exercise many logins from one client; never in production.
    rate_limit_enabled: bool = True
    max_accounts_per_workspace: int = Field(default=25, ge=1, le=500)
    # Shared with the web app's proxy; lets the audit log tell UI actions from API use.
    web_proxy_secret: SecretStr | None = None

    # ---- ingestion and the Research agent (M4) ----
    research_model: str = "anthropic/claude-haiku-4-5"
    research_fallbacks: list[str] = Field(default_factory=lambda: ["openai/gpt-5-mini"])
    embedding_model: str = "openai/text-embedding-3-small"
    # Per workspace per UTC day, across the Research agent's calls (docs/06 §8).
    research_daily_cost_cap_usd: float = Field(default=2.0, ge=0)
    # Semantic dedupe threshold (docs/03 §5: "starting at 0.92, tuned against real data").
    dedupe_similarity: float = Field(default=0.92, gt=0.5, le=1.0)
    freshness_half_life_hours: float = Field(default=24.0, gt=0)
    ingest_max_new_items_per_fetch: int = Field(default=30, ge=1, le=200)
    # Use the operating system's trust store for outbound fetches (a corporate TLS proxy's
    # CA lives there, not in certifi).
    http_system_trust_store: bool = True

    # ---- drafting agents (M5; docs/06 §7: strong models where quality matters) ----
    writer_model: str = "anthropic/claude-sonnet-4-5"
    writer_fallbacks: list[str] = Field(default_factory=lambda: ["openai/gpt-5"])
    editor_model: str = "anthropic/claude-sonnet-4-5"
    editor_fallbacks: list[str] = Field(default_factory=lambda: ["openai/gpt-5"])
    hashtag_model: str = "openai/gpt-5-mini"
    hashtag_fallbacks: list[str] = Field(default_factory=list)
    # Alt text suggestions (P2-M1.8): any vision-capable model.
    alt_text_model: str = "anthropic/claude-haiku-4-5"
    alt_text_fallbacks: list[str] = Field(default_factory=lambda: ["openai/gpt-5-mini"])
    # Writer, Editor and Hashtag together, per workspace per UTC day.
    drafting_daily_cost_cap_usd: float = Field(default=5.0, ge=0)
    # Every agent together, per workspace per UTC calendar month (M5.16, docs/06 §8).
    workspace_monthly_cost_cap_usd: float = Field(default=100.0, ge=0)

    # ---- observability ----
    log_level: str = "INFO"
    log_format: str = Field(default="json", pattern="^(json|console)$")
    otel_exporter_otlp_endpoint: str | None = None
    # Standard OTLP format, e.g. ``Authorization=Basic%20<base64(instance_id:token)>``.
    # It is deliberately not written to application logs.
    otel_exporter_otlp_headers: SecretStr | None = None

    @field_validator("mera_secret_key")
    @classmethod
    def _secret_key_length(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value().encode()) < 32:
            raise ValueError("MERA_SECRET_KEY must be at least 32 bytes")
        return v

    @field_validator("mera_master_key")
    @classmethod
    def _master_key_shape(cls, v: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(v.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("MERA_MASTER_KEY must be base64") from exc
        if len(raw) != 32:
            raise ValueError("MERA_MASTER_KEY must decode to exactly 32 bytes")
        return v

    @field_validator(
        "web_proxy_secret",
        "telegram_webhook_secret",
        "smtp_password",
        "whatsapp_access_token",
        "whatsapp_app_secret",
        "whatsapp_verify_token",
        "media_api_key",
        "mcp_service_secret",
        "mcp_sandbox_token",
        "mcp_egress_admin_token",
        mode="before",
    )
    @classmethod
    def _blank_secret_is_unset(cls, v: object) -> object:
        # Compose passes "" for an unset variable; an empty secret would match an empty
        # header and prove nothing.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("web_proxy_secret")
    @classmethod
    def _proxy_secret_length(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < 16:
            raise ValueError("WEB_PROXY_SECRET must be at least 16 characters")
        return v

    @field_validator("mcp_sandbox_token", "mcp_egress_admin_token")
    @classmethod
    def _internal_token_length(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < 16:
            raise ValueError("MCP_SANDBOX_TOKEN and MCP_EGRESS_ADMIN_TOKEN need 16+ characters")
        return v

    @field_validator("database_url", "database_migration_url")
    @classmethod
    def _asyncpg_driver(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("postgresql+asyncpg://"):
            raise ValueError("database URLs must use the postgresql+asyncpg:// driver")
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.mera_env is not Env.DEVELOPMENT:
            # Staging is a shared server too: fakes and example keys are local-only.
            if self.mera_master_key.get_secret_value() == EXAMPLE_MASTER_KEY:
                raise ValueError("MERA_MASTER_KEY is the example value; generate a real key")
            if self.mera_secret_key.get_secret_value() == EXAMPLE_SECRET_KEY:
                raise ValueError("MERA_SECRET_KEY is the example value; generate a real key")
            for name, tok in (
                ("MCP_SANDBOX_TOKEN", self.mcp_sandbox_token),
                ("MCP_EGRESS_ADMIN_TOKEN", self.mcp_egress_admin_token),
            ):
                if tok is not None and tok.get_secret_value().startswith("change-me"):
                    raise ValueError(f"{name} is the docker-compose default; generate one")
        if self.mera_env is Env.STAGING and self.sandbox_mode:
            raise ValueError("SANDBOX_MODE=true is not allowed when MERA_ENV=staging")
        if self.mera_env is not Env.DEVELOPMENT and self.sandbox_llm:
            raise ValueError("SANDBOX_LLM=true is only allowed when MERA_ENV=development")
        if self.telegram_bot_token is not None:
            secret = self.telegram_webhook_secret
            if secret is None or len(secret.get_secret_value()) < 16:
                raise ValueError(
                    "TELEGRAM_WEBHOOK_SECRET (16+ characters) is required with TELEGRAM_BOT_TOKEN"
                )
        wa = (
            self.whatsapp_phone_number_id,
            self.whatsapp_access_token,
            self.whatsapp_app_secret,
            self.whatsapp_verify_token,
        )
        if any(v is not None for v in wa) and not all(v is not None for v in wa):
            raise ValueError(
                "WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ACCESS_TOKEN, WHATSAPP_APP_SECRET and "
                "WHATSAPP_VERIFY_TOKEN are needed together"
            )
        if self.mera_env is Env.PRODUCTION and not self.whatsapp_graph_url.startswith(
            "https://graph.facebook.com/"
        ):
            raise ValueError("WHATSAPP_GRAPH_URL may point elsewhere only outside production")
        if self.smtp_host and not self.smtp_from:
            raise ValueError("SMTP_FROM is required with SMTP_HOST")
        if self.smtp_host and self.smtp_security == "none" and self.mera_env is Env.PRODUCTION:
            raise ValueError("SMTP_SECURITY=none is not allowed in production")
        if self.mera_env is Env.PRODUCTION:
            if self.telegram_mode == "polling":
                raise ValueError("TELEGRAM_MODE=polling is for local development only")
            if self.sandbox_mode:
                raise ValueError("SANDBOX_MODE=true is not allowed when MERA_ENV=production")
            if self.mera_master_key.get_secret_value() == EXAMPLE_MASTER_KEY:
                raise ValueError("MERA_MASTER_KEY is the example value; generate a real key")
            if self.web_proxy_secret is None:
                raise ValueError(
                    "WEB_PROXY_SECRET is required in production: without it every browser "
                    "shares the web server's address for rate limits and the audit log"
                )
            if not self.rate_limit_enabled:
                raise ValueError("RATE_LIMIT_ENABLED=false is not allowed in production")
            if self.mera_secret_key.get_secret_value() == EXAMPLE_SECRET_KEY:
                raise ValueError("MERA_SECRET_KEY is the example value; generate a real key")
        return self

    @property
    def mfa_enforced(self) -> bool:
        if self.mfa_required_for_admins is not None:
            return self.mfa_required_for_admins
        return self.mera_env is not Env.DEVELOPMENT

    @property
    def migration_url(self) -> str:
        return self.database_migration_url or self.database_url

    @property
    def libpq_dsn(self) -> str:
        """DSN for psycopg (procrastinate), derived from the asyncpg URL."""
        url = self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        if "ssl=require" in url and "sslmode" not in url:
            url = url.replace("ssl=require", "sslmode=require")
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # populated from the environment
