"""Request dependencies: services, request metadata, and the authenticated user (M2.4).

Workspace binding is deliberately *not* a dependency: services bind the principal inside
the same transaction as the action, so the role checked is the role at that moment.
"""

from __future__ import annotations

import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mera.api.middleware.request_id import current_request_id
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import AuthenticationFailed, Forbidden
from mera.application.services.access_service import AccessService
from mera.application.services.account_service import AccountService
from mera.application.services.agent_run_service import AgentRunService
from mera.application.services.analytics_service import AnalyticsService
from mera.application.services.approval_service import ApprovalService
from mera.application.services.auth_service import AuthService
from mera.application.services.channel_service import ChannelService
from mera.application.services.content_service import ContentService
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
from mera.application.services.publishing_service import PublishingService
from mera.application.services.security_service import SecurityService
from mera.application.services.source_service import SourceService
from mera.application.services.template_service import TemplateService
from mera.application.services.voice_service import VoiceService
from mera.application.services.workspace_service import WorkspaceService

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login")


def auth_service(request: Request) -> AuthService:
    svc: AuthService = request.app.state.auth_service
    return svc


def workspace_service(request: Request) -> WorkspaceService:
    svc: WorkspaceService = request.app.state.workspace_service
    return svc


def _client_ip(request: Request) -> str | None:
    """The socket peer, which ``TrustedClientIPMiddleware`` has already replaced with the
    browser's address for requests the web app forwards; behind a reverse proxy, uvicorn's
    ``FORWARDED_ALLOW_IPS`` does the same. Never read X-Forwarded-For here. Anything that
    is not an IP address (tests, unix sockets) is recorded as unknown rather than failing
    the insert."""
    host = request.client.host if request.client else None
    if host is None:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def access_service(request: Request) -> AccessService:
    svc: AccessService = request.app.state.access_service
    return svc


def account_service(request: Request) -> AccountService:
    svc: AccountService = request.app.state.account_service
    return svc


def group_service(request: Request) -> GroupService:
    svc: GroupService = request.app.state.group_service
    return svc


def source_service(request: Request) -> SourceService:
    svc: SourceService = request.app.state.source_service
    return svc


def content_service(request: Request) -> ContentService:
    svc: ContentService = request.app.state.content_service
    return svc


def post_service(request: Request) -> PostService:
    svc: PostService = request.app.state.post_service
    return svc


def security_service(request: Request) -> SecurityService:
    svc: SecurityService = request.app.state.security_service
    return svc


def hashtag_service(request: Request) -> HashtagService:
    svc: HashtagService = request.app.state.hashtag_service
    return svc


def template_service(request: Request) -> TemplateService:
    svc: TemplateService = request.app.state.template_service
    return svc


def voice_service(request: Request) -> VoiceService:
    svc: VoiceService = request.app.state.voice_service
    return svc


def media_service(request: Request) -> MediaService:
    svc: MediaService = request.app.state.media_service
    return svc


def approval_service(request: Request) -> ApprovalService:
    svc: ApprovalService = request.app.state.approval_service
    return svc


def analytics_service(request: Request) -> AnalyticsService:
    svc: AnalyticsService = request.app.state.analytics_service
    return svc


def media_generation_service(request: Request) -> MediaGenerationService:
    svc: MediaGenerationService = request.app.state.media_generation_service
    return svc


def inbox_service(request: Request) -> InboxService:
    svc: InboxService = request.app.state.inbox_service
    return svc


def orchestration_service(request: Request) -> OrchestrationService:
    svc: OrchestrationService = request.app.state.orchestration_service
    return svc


def mcp_auth(request: Request) -> McpAuthService:
    svc: McpAuthService = request.app.state.mcp_auth
    return svc


def mcp_registry(request: Request) -> McpRegistryService:
    svc: McpRegistryService = request.app.state.mcp_registry
    return svc


def channel_service(request: Request) -> ChannelService:
    svc: ChannelService = request.app.state.channel_service
    return svc


def publishing_service(request: Request) -> PublishingService:
    svc: PublishingService = request.app.state.publishing_service
    return svc


def governance_service(request: Request) -> GovernanceService:
    svc: GovernanceService = request.app.state.governance_service
    return svc


def agent_run_service(request: Request) -> AgentRunService:
    svc: AgentRunService = request.app.state.agent_run_service
    return svc


def oauth_service(request: Request) -> OAuthService:
    svc: OAuthService = request.app.state.oauth_service
    return svc


def request_meta(request: Request) -> RequestMeta:
    client = _client_ip(request)
    # Audit metadata only, never authority. The web app's server-side proxy proves it
    # forwarded the request with a shared secret, so a script cannot dress its actions up
    # as UI use (Sprint 4 review L9). Without a configured secret everything is "api".
    secret = request.app.state.settings.web_proxy_secret
    claimed = request.headers.get("x-mera-proxy-secret", "")
    is_web = secret is not None and hmac.compare_digest(
        claimed.encode(), secret.get_secret_value().encode()
    )
    surface = "web" if is_web else "mcp" if via_mcp(request) else "api"
    return RequestMeta(
        surface=surface,
        ip_address=client,
        request_id=current_request_id(),
        user_agent=request.headers.get("user-agent"),
        second_factor=(request.headers.get("x-mera-second-factor") or None),
    )


def via_mcp(request: Request) -> bool:
    """The request came through Mera's mcp service (it presents the shared secret)."""
    secret = request.app.state.settings.mcp_service_secret
    claimed = request.headers.get("x-mera-service", "")
    return secret is not None and hmac.compare_digest(
        claimed.encode(), secret.get_secret_value().encode()
    )


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    auth: Annotated[AuthService, Depends(auth_service)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationFailed("Not signed in.", code="invalid_token")
    return await auth.authenticate(credentials.credentials, via_mcp=via_mcp(request))


async def current_human(
    user: Annotated[AuthenticatedUser, Depends(current_user)],
) -> AuthenticatedUser:
    """Endpoints about the person themself (/auth/me) do not accept API keys."""
    if user.is_api_key:
        raise Forbidden(
            "API keys cannot do this; sign in as a user.", code="user_credentials_required"
        )
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
HumanUser = Annotated[AuthenticatedUser, Depends(current_human)]
Access = Annotated[AccessService, Depends(access_service)]
OAuth = Annotated[OAuthService, Depends(oauth_service)]
Accounts = Annotated[AccountService, Depends(account_service)]
Groups = Annotated[GroupService, Depends(group_service)]
Sources = Annotated[SourceService, Depends(source_service)]
Content = Annotated[ContentService, Depends(content_service)]
Posts = Annotated[PostService, Depends(post_service)]
Voices = Annotated[VoiceService, Depends(voice_service)]
Hashtags = Annotated[HashtagService, Depends(hashtag_service)]
Security = Annotated[SecurityService, Depends(security_service)]
Templates = Annotated[TemplateService, Depends(template_service)]
Media = Annotated[MediaService, Depends(media_service)]
Inbox = Annotated[InboxService, Depends(inbox_service)]
McpAuth = Annotated[McpAuthService, Depends(mcp_auth)]
McpRegistry = Annotated[McpRegistryService, Depends(mcp_registry)]
Orchestration = Annotated[OrchestrationService, Depends(orchestration_service)]
MediaGen = Annotated[MediaGenerationService, Depends(media_generation_service)]
Approvals = Annotated[ApprovalService, Depends(approval_service)]
Governance = Annotated[GovernanceService, Depends(governance_service)]
Publishing = Annotated[PublishingService, Depends(publishing_service)]
Channels = Annotated[ChannelService, Depends(channel_service)]
Analytics = Annotated[AnalyticsService, Depends(analytics_service)]
AgentRuns = Annotated[AgentRunService, Depends(agent_run_service)]
Meta = Annotated[RequestMeta, Depends(request_meta)]
Auth = Annotated[AuthService, Depends(auth_service)]
Workspaces = Annotated[WorkspaceService, Depends(workspace_service)]
