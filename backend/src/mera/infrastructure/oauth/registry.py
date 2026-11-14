"""Which OIDC providers are available: real ones when credentials are configured; sandbox
fakes otherwise, but only in development with sandbox mode on. A fake accepts any
identity it is handed, so it must never be reachable on a shared server (review M6)."""

from __future__ import annotations

from mera.config import Env, Settings
from mera.infrastructure.oauth import oidc
from mera.infrastructure.oauth.base import OidcProvider
from mera.infrastructure.oauth.fake import FakeOidcProvider


def build_providers(settings: Settings) -> dict[str, OidcProvider]:
    providers: dict[str, OidcProvider] = {}
    fakes = settings.sandbox_mode and settings.mera_env is Env.DEVELOPMENT
    if settings.google_client_id and settings.google_client_secret:
        providers["google"] = oidc.google(
            settings.google_client_id, settings.google_client_secret.get_secret_value()
        )
    elif fakes:
        providers["google"] = FakeOidcProvider("google", trusts_email=True)
    if settings.microsoft_client_id and settings.microsoft_client_secret:
        providers["microsoft"] = oidc.microsoft(
            settings.microsoft_client_id, settings.microsoft_client_secret.get_secret_value()
        )
    elif fakes:
        providers["microsoft"] = FakeOidcProvider("microsoft", trusts_email=False)
    return providers
