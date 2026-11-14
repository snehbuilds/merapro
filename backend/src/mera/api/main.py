"""FastAPI application factory (M1.8)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.api.errors import ErrorEnvelope, install_error_handlers
from mera.api.health import router as health_router
from mera.api.middleware.body_limit import BodyLimitMiddleware
from mera.api.middleware.client_ip import TrustedClientIPMiddleware
from mera.api.middleware.idempotency import IdempotencyMiddleware
from mera.api.middleware.rate_limit import RateLimitMiddleware
from mera.api.middleware.request_id import RequestIdMiddleware
from mera.api.v1 import router as v1_router
from mera.api.v1.routers.mcp import well_known as mcp_well_known
from mera.application.channels.factory import build_bot, build_whatsapp_bot
from mera.application.digests import DigestPreferences
from mera.application.drafting.factory import build_drafting
from mera.application.ingest.factory import build_ingestion
from mera.application.media_factory import build_media_fetcher, build_object_store
from mera.application.services.ab_service import AbService
from mera.application.services.access_service import AccessService
from mera.application.services.account_service import AccountService
from mera.application.services.agent_run_service import AgentRunService
from mera.application.services.analytics_service import AnalyticsService
from mera.application.services.approval_service import ApprovalService
from mera.application.services.auth_service import AuthService
from mera.application.services.channel_service import ChannelService
from mera.application.services.clone_service import CloneService
from mera.application.services.content_service import ContentService
from mera.application.services.evergreen_service import EvergreenService
from mera.application.services.governance_service import GovernanceService
from mera.application.services.group_service import GroupService
from mera.application.services.hashtag_service import HashtagService
from mera.application.services.inbox_service import InboxService
from mera.application.services.mcp_auth_service import McpAuthService
from mera.application.services.mcp_registry_service import McpRegistryService
from mera.application.services.media_generation_service import MediaGenerationService
from mera.application.services.media_service import MediaService
from mera.application.services.oauth_service import OAuthService
from mera.application.services.orchestration_service import OrchestrationService
from mera.application.services.post_service import PostService
from mera.application.services.privacy_service import PrivacyService
from mera.application.services.publishing_service import PublishingService
from mera.application.services.security_service import SecurityService
from mera.application.services.source_service import SourceService
from mera.application.services.template_service import TemplateService
from mera.application.services.voice_service import VoiceService
from mera.application.services.webhook_service import WebhookService
from mera.application.services.workspace_service import WorkspaceService
from mera.config import Env, Settings, get_settings
from mera.infrastructure.crypto.envelope import LocalKeyProvider
from mera.infrastructure.db.session import (
    UnsafeDatabaseRole,
    assert_rls_bound,
    create_engine,
    create_sessionmaker,
)
from mera.infrastructure.media.generators import build_media_generator
from mera.infrastructure.notify import build_notifier
from mera.infrastructure.oauth.registry import build_providers
from mera.infrastructure.platforms.connectors import build_connectors
from mera.infrastructure.platforms.engagement import build_engagement
from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer
from mera.observability import configure_logging, configure_tracing

API_PREFIX = "/api/v1"
log = structlog.get_logger(__name__)


async def _check_database_role(engine: AsyncEngine, settings: Settings) -> None:
    """Outside development, refuse to boot on an RLS-bypassing role. In development the
    database may simply be down, so only warn."""
    try:
        await assert_rls_bound(engine)
    except UnsafeDatabaseRole:
        if settings.mera_env is not Env.DEVELOPMENT:
            raise
        log.warning("database.role_bypasses_rls", hint="DATABASE_URL should use mera_app")
    except Exception as exc:
        if settings.mera_env is not Env.DEVELOPMENT:
            raise
        log.warning("database.role_check_skipped", error_type=type(exc).__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        await _check_database_role(engine, settings)
        app.state.engine = engine
        sessionmaker = create_sessionmaker(engine)
        app.state.sessionmaker = sessionmaker
        notifier = build_notifier(settings)
        app.state.notifier = notifier
        security = SecurityService(
            sessionmaker, LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value())
        )
        app.state.security_service = security
        app.state.auth_service = AuthService(
            sessionmaker,
            secret=settings.mera_secret_key.get_secret_value(),
            rate_limit=settings.rate_limit_enabled,
            sandbox=settings.sandbox_mode,
            public_url=settings.mera_public_url,
            notifier=notifier,
            security=security,
            mfa_enforced=settings.mfa_enforced,
        )
        app.state.workspace_service = WorkspaceService(sessionmaker)
        app.state.mcp_auth = McpAuthService(
            sessionmaker,
            issuer=settings.mera_api_url,
            resource=settings.mcp_resource_url,
            fetcher=build_media_fetcher(settings),
            web_url=settings.mera_public_url,
        )
        app.state.auth_service.mcp = app.state.mcp_auth
        app.state.oauth_service = OAuthService(
            sessionmaker,
            app.state.auth_service,
            build_providers(settings),
            api_url=settings.mera_api_url,
            public_url=settings.mera_public_url,
        )
        app.state.access_service = AccessService(
            sessionmaker,
            sandbox=settings.sandbox_mode,
            public_url=settings.mera_public_url,
            notifier=notifier,
        )
        key_provider = LocalKeyProvider.from_base64(settings.mera_master_key.get_secret_value())
        app.state.key_provider = key_provider
        app.state.connectors = build_connectors(settings)
        app.state.account_service = AccountService(
            sessionmaker,
            connectors=app.state.connectors,
            key_provider=key_provider,
            api_url=settings.mera_api_url,
            public_url=settings.mera_public_url,
            max_accounts=settings.max_accounts_per_workspace,
        )
        app.state.group_service = GroupService(sessionmaker)
        ingestion = build_ingestion(settings, sessionmaker)
        app.state.ingestion = ingestion
        app.state.source_service = SourceService(
            sessionmaker, ingestion, half_life_hours=settings.freshness_half_life_hours
        )
        app.state.content_service = ContentService(
            sessionmaker, ingestion, half_life_hours=settings.freshness_half_life_hours
        )
        drafting = build_drafting(settings, sessionmaker)
        app.state.drafting = drafting
        deferrer = ProcrastinateDeferrer(settings.libpq_dsn)
        app.state.deferrer = deferrer
        app.state.post_service = PostService(sessionmaker, drafting, deferrer)
        app.state.voice_service = VoiceService(sessionmaker, drafting)
        app.state.hashtag_service = HashtagService(sessionmaker)
        app.state.template_service = TemplateService(sessionmaker)
        object_store = build_object_store(settings)
        app.state.object_store = object_store
        app.state.media_service = MediaService(
            sessionmaker, object_store, build_media_fetcher(settings), drafting=drafting
        )
        app.state.agent_run_service = AgentRunService(sessionmaker, settings)
        app.state.approval_service = ApprovalService(
            sessionmaker, deferrer, mfa_enforced=settings.mfa_enforced
        )
        app.state.media_generation_service = MediaGenerationService(
            sessionmaker,
            settings,
            build_media_generator(settings),
            app.state.media_service,
            deferrer,
            drafting,
            approvals=app.state.approval_service,
        )
        app.state.inbox_service = InboxService(
            sessionmaker,
            build_engagement(settings),
            key_provider,
            deferrer,
            drafting,
            triage_per_poll=settings.engagement_triage_per_poll,
            mfa_enforced=settings.mfa_enforced,
        )
        app.state.orchestration_service = OrchestrationService(
            sessionmaker, drafting, deferrer, app.state.media_generation_service
        )
        app.state.mcp_registry = McpRegistryService(sessionmaker, settings, key_provider, deferrer)
        ingestion.mcp = app.state.mcp_registry
        app.state.media_generation_service._registry = app.state.mcp_registry
        app.state.governance_service = GovernanceService(sessionmaker)
        app.state.publishing_service = PublishingService(sessionmaker, deferrer)
        app.state.bot = build_bot(
            settings,
            sessionmaker,
            deferrer,
            posts=app.state.post_service,
            media=app.state.media_generation_service,
            inbox=app.state.inbox_service,
            registry=app.state.mcp_registry,
        )
        app.state.whatsapp = build_whatsapp_bot(
            settings,
            sessionmaker,
            deferrer,
            posts=app.state.post_service,
            media=app.state.media_generation_service,
            inbox=app.state.inbox_service,
            registry=app.state.mcp_registry,
        )
        app.state.channel_service = ChannelService(
            sessionmaker, settings, app.state.bot, app.state.whatsapp
        )
        app.state.analytics_service = AnalyticsService(sessionmaker)
        app.state.webhook_service = WebhookService(
            sessionmaker, key_provider, allow_http=settings.sandbox_mode
        )
        app.state.digest_preferences = DigestPreferences(sessionmaker)
        app.state.ab_service = AbService(sessionmaker, app.state.post_service)
        app.state.clone_service = CloneService(sessionmaker, app.state.workspace_service)
        app.state.evergreen_service = EvergreenService(
            sessionmaker, app.state.post_service, mfa_enforced=settings.mfa_enforced
        )
        app.state.privacy_service = PrivacyService(
            sessionmaker, object_store, deferrer, security=app.state.security_service
        )
        configure_tracing(settings, "mera-api")
        try:
            yield
        finally:
            deferrer.close()
            await engine.dispose()

    app = FastAPI(
        title="Mera API",
        version="1.0.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        lifespan=lifespan,
        responses={"default": {"model": ErrorEnvelope, "description": "Error envelope"}},
    )
    app.state.settings = settings
    # Starlette wraps in reverse order of addition: RequestId is outermost, so the rate
    # limiter and idempotency layer can stamp request ids on the responses they produce.
    secret = settings.mera_secret_key.get_secret_value()
    app.add_middleware(IdempotencyMiddleware, secret=secret)
    if settings.rate_limit_enabled:
        app.add_middleware(RateLimitMiddleware, secret=secret)
    # Before anything reads a body: oversized uploads never reach the multipart parser.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(RequestIdMiddleware)
    # Outermost: the rate limiter, the audit metadata and login lockout all read the
    # browser's address, not the web server's, for requests the web app forwards.
    if settings.web_proxy_secret is not None:
        app.add_middleware(
            TrustedClientIPMiddleware, proxy_secret=settings.web_proxy_secret.get_secret_value()
        )
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(mcp_well_known)
    app.include_router(v1_router, prefix=API_PREFIX)
    return app


def app_factory() -> FastAPI:
    """Uvicorn entry: ``uvicorn mera.api.main:app_factory --factory``."""
    return create_app()
