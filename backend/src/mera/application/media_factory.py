"""Wiring for the media pipeline: the object store and the fetcher for article images."""

from __future__ import annotations

from pathlib import Path

from mera.config import Settings
from mera.infrastructure.http.safe_fetch import SafeFetcher
from mera.infrastructure.storage.filesystem import FilesystemObjectStore, signing_key_from_secret


def build_object_store(settings: Settings) -> FilesystemObjectStore:
    return FilesystemObjectStore(
        Path(settings.media_root),
        signing_key=signing_key_from_secret(settings.mera_secret_key.get_secret_value()),
        public_base_url=settings.mera_api_url,
    )


def build_media_fetcher(settings: Settings) -> SafeFetcher:
    return SafeFetcher(
        # A generic client name: no contact details in outbound requests.
        user_agent="MeraBot/1.0 (link preview)",
        system_trust_store=settings.http_system_trust_store,
    )
