"""Workspace OAuth apps and the app snapshot attached to an OAuth grant."""

import json
import uuid
from enum import StrEnum

from mera.domain.ports.key_provider import KeyProvider
from mera.domain.value_objects.platform import Platform
from mera.infrastructure.platforms.connectors import PlatformConnector
from mera.infrastructure.platforms.oauth_connectors import (
    ClientCredentials,
    LinkedInConnector,
    MetaConnector,
    ThreadsConnector,
    XConnector,
)


class OAuthProvider(StrEnum):
    LINKEDIN = "linkedin"
    META = "meta"
    THREADS = "threads"
    X = "x"


PROVIDER_PLATFORMS = {
    OAuthProvider.LINKEDIN: (Platform.LINKEDIN_MEMBER, Platform.LINKEDIN_ORGANIZATION),
    OAuthProvider.META: (Platform.FACEBOOK, Platform.INSTAGRAM),
    OAuthProvider.THREADS: (Platform.THREADS,),
    OAuthProvider.X: (Platform.X,),
}
PLATFORM_PROVIDER = {
    platform: provider
    for provider, platforms in PROVIDER_PLATFORMS.items()
    for platform in platforms
}
APP_SNAPSHOT = "_mera_workspace_oauth_app"


def seal_app(
    kp: KeyProvider, workspace_id: uuid.UUID, provider: OAuthProvider, client: ClientCredentials
) -> str:
    return kp.encrypt(
        json.dumps([client.client_id, client.client_secret]).encode(),
        workspace_id=workspace_id,
        purpose=f"workspace.oauth.{provider.value}",
    )


def connector_for_app(
    kp: KeyProvider, workspace_id: uuid.UUID, platform: Platform, snapshot: str
) -> PlatformConnector:
    provider = PLATFORM_PROVIDER[platform]
    client_id, secret = json.loads(
        kp.decrypt(snapshot, workspace_id=workspace_id, purpose=f"workspace.oauth.{provider.value}")
    )
    client = ClientCredentials(client_id, secret)
    match provider:
        case OAuthProvider.LINKEDIN:
            return LinkedInConnector(platform, client)
        case OAuthProvider.META:
            return MetaConnector(platform, client)
        case OAuthProvider.THREADS:
            return ThreadsConnector(client)
        case OAuthProvider.X:
            return XConnector(client)
