"""Media: upload, a source's OG image, per-platform renditions, attach to a variant (M5.14).

One pipeline whatever the origin (docs/03 §6): checksum, store the master, derive the
rendition each platform needs, attach it to a version. Three rules carry the weight:

- **Bytes are checked before they are kept.** ``images.probe`` refuses anything that is not
  a still JPEG, PNG or WebP, and anything big enough to be a decompression bomb, before a
  pixel is decoded. Renditions are re-encoded without metadata, so a phone photo's GPS
  location never reaches a platform.
- **Media is in the content hash by checksum.** Attaching, removing, reordering or changing
  alt text creates a new immutable version through ``core.create_version``, which moves the
  hash and so invalidates any approval. There is no in-place image swap.
- **The approver sees what goes out.** The version records the *rendition* (the exact crop
  and encoding a platform receives), not the master.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.agents.alt_text import AltTextRequest, AltTextSuggestion
from mera.application.agents.base import AgentContext, CostCapReached
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting import core
from mera.application.drafting.pipeline import Drafting
from mera.application.errors import (
    ConflictError,
    InvalidInput,
    NotFoundError,
    ServiceUnavailableError,
    TooManyAttempts,
)
from mera.application.ingest import agent_runs
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.llm import LLMError
from mera.domain.ports.object_store import ObjectNotFound
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.platform import Platform
from mera.domain.value_objects.posts import PostSetState
from mera.infrastructure.db.models import (
    AccountGroupMember,
    ContentItem,
    MediaAsset,
    PostSet,
    PostVariant,
    PostVersion,
    Workspace,
)
from mera.infrastructure.db.rate_limit import Limit, take
from mera.infrastructure.db.repositories.audit import record
from mera.infrastructure.http.safe_fetch import FetchError, SafeFetcher, public_message
from mera.infrastructure.media import images
from mera.infrastructure.platforms.capabilities import MediaAspect, capabilities_for
from mera.infrastructure.storage.filesystem import FilesystemObjectStore

log = structlog.get_logger(__name__)

UPLOAD_LIMIT: Final = Limit(capacity=60, per_seconds=60)
WORKSPACE_MEDIA_BYTES: Final = 2 * 1024**3  # masters and renditions together
MAX_ALT: Final = 1000
MAX_ATTACHED: Final = 20  # the largest carousel any platform takes
MAX_FILE_BYTES: Final = 200 * 1024**2
GENERIC_TYPES: Final = {
    "application/pdf": ("document", "pdf"),
    "video/mp4": ("video", "mp4"),
    "video/quicktime": ("video", "mov"),
}
VERSION_CONFLICT: Final = (
    "This account's post changed since you opened it; reload and reapply your change."
)


@dataclass(frozen=True, slots=True)
class MediaView:
    id: uuid.UUID
    media_type: str
    content_type: str
    width: int
    height: int
    byte_size: int
    origin: str
    source_url: str | None
    rendition_key: str
    parent_asset_id: uuid.UUID | None
    checksum: str
    default_alt_text: str | None
    created_at: datetime
    original_filename: str | None = None
    status: str = "ready"
    renditions: list[MediaView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MediaChoice:
    asset_id: uuid.UUID  # a master
    aspect: str | None = None  # a key from the platform's media_aspects; default = first
    alt_text: str | None = None  # default = the master's default alt text


def _view(a: MediaAsset, renditions: list[MediaAsset] | None = None) -> MediaView:
    return MediaView(
        id=a.id,
        media_type=a.media_type,
        content_type=a.content_type,
        width=a.width,
        height=a.height,
        byte_size=a.byte_size,
        origin=a.origin,
        source_url=a.source_url,
        rendition_key=a.rendition_key,
        parent_asset_id=a.parent_asset_id,
        checksum=a.checksum.hex(),
        default_alt_text=a.default_alt_text,
        created_at=a.created_at,
        original_filename=a.original_filename,
        status=a.status,
        renditions=[_view(r) for r in renditions or []],
    )


def _alt(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.replace("\x00", "").split())
    if len(cleaned) > MAX_ALT:
        raise InvalidInput(
            f"Alt text can be at most {MAX_ALT} characters.", code="alt_too_long", field="alt_text"
        )
    return cleaned or None


def _reject(exc: images.ImageRejected) -> InvalidInput:
    return InvalidInput(exc.message, code=exc.code, field="file")


class MediaService(WorkspaceScopedService):
    def __init__(
        self,
        sm: Any,
        store: FilesystemObjectStore,
        fetcher: SafeFetcher,
        drafting: Drafting | None = None,
    ) -> None:
        super().__init__(sm)
        self._store = store
        self._fetcher = fetcher
        self._drafting = drafting

    async def suggest_alt_text(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        asset_id: uuid.UUID,
        *,
        platform: str | None = None,
        post_excerpt: str | None = None,
    ) -> AltTextSuggestion:
        """A suggested description of one image (P2-M1.8). Nothing is saved: the person
        edits the suggestion and saves it, which makes a new version as any edit does."""
        if self._drafting is None:
            raise ServiceUnavailableError("Alt text suggestions are not configured here.")
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
        data, _content_type, _checksum = await self.content(user, workspace_id, meta, asset_id)
        try:
            info = images.probe(data)
            side = 1024
            scale = min(1.0, side / max(info.width, info.height))
            w, h = max(1, round(info.width * scale)), max(1, round(info.height * scale))
            small = await asyncio.to_thread(images.derive, data, w, h, 900_000)
        except images.ImageRejected as exc:
            raise InvalidInput(exc.message, code=exc.code) from exc
        data_url = "data:image/jpeg;base64," + base64.b64encode(small.data).decode()
        name = core.platform_name(platform) if platform else "social media"
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key="alt_text",
            trigger="manual",
            subject_type="media_asset",
            subject_id=asset_id,
        )
        ctx = AgentContext(
            self._sm,
            run,
            self._drafting.config.daily_cost_cap_usd,
            monthly_cap_usd=self._drafting.config.monthly_cost_cap_usd,
        )
        try:
            out = await self._drafting.alt_text.run(
                ctx, AltTextRequest(data_url, name, (post_excerpt or None) and post_excerpt[:500])
            )
        except CostCapReached as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=str(exc))
            raise ServiceUnavailableError("The AI budget for today is spent.") from exc
        except LLMError as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=exc.kind)
            raise ServiceUnavailableError(
                "Alt text suggestions are unavailable right now."
            ) from exc
        await agent_runs.finish_run(self._sm, run, state="succeeded")
        return out

    # ---------------------------------------------------------------------- intake

    async def upload(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        data: bytes,
        alt_text: str | None,
    ) -> MediaView:
        alt = _alt(alt_text)
        await self._upload_budget(user)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
            await self._writable(s, workspace_id)
        try:
            info = await asyncio.to_thread(images.probe, data)
        except images.ImageRejected as exc:
            raise _reject(exc) from exc
        return await self._ingest(
            user,
            workspace_id,
            meta,
            data,
            info,
            origin="upload",
            alt=alt,
            source_url=None,
            content_item_id=None,
        )

    async def upload_file(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        data: bytes,
        content_type: str,
        filename: str | None,
        alt_text: str | None,
        origin: str = "upload",
        source_url: str | None = None,
    ) -> MediaView:
        """Ingest an image, MP4/QuickTime video, or PDF after signature and size checks."""
        if len(data) > MAX_FILE_BYTES:
            raise InvalidInput("Files can be at most 200 MB.", code="file_too_large", field="file")
        if content_type.startswith("image/"):
            if origin == "upload":
                return await self.upload(user, workspace_id, meta, data=data, alt_text=alt_text)
            await self._upload_budget(user)
            async with self._tx(user, workspace_id, meta) as s:
                principal = await load_user_principal(s, user, workspace_id)
                rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
                await self._writable(s, workspace_id)
            try:
                info = await asyncio.to_thread(images.probe, data)
            except images.ImageRejected as exc:
                raise _reject(exc) from exc
            return await self._ingest(
                user,
                workspace_id,
                meta,
                data,
                info,
                origin=origin,
                alt=_alt(alt_text),
                source_url=source_url,
                content_item_id=None,
            )
        kind_ext = GENERIC_TYPES.get(content_type.lower().split(";", 1)[0])
        if kind_ext is None:
            raise InvalidInput(
                "Unsupported media type.", code="unsupported_media_type", field="content_type"
            )
        if content_type.startswith("application/pdf") and not data.startswith(b"%PDF-"):
            raise InvalidInput("The file is not a valid PDF.", code="mime_mismatch", field="file")
        if content_type.startswith("video/") and b"ftyp" not in data[:32]:
            raise InvalidInput(
                "The file is not a supported video.", code="mime_mismatch", field="file"
            )
        await self._upload_budget(user)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
            await self._writable(s, workspace_id)
        media_type, ext = kind_ext
        checksum = hashlib.sha256(data).digest()
        async with self._tx(user, workspace_id, meta) as s:
            existing = await _master_by_checksum(s, checksum)
            if existing is not None:
                return _view(existing)
            await self._within_quota(s, len(data))
        asset_id = uuid7()
        key = f"ws/{workspace_id}/{asset_id}.{ext}"
        await self._store.put(key, data, content_type)
        try:
            async with self._tx(user, workspace_id, meta) as s:
                await self._within_quota(s, len(data), lock=True)
                asset = MediaAsset(
                    id=asset_id,
                    workspace_id=workspace_id,
                    media_type=media_type,
                    object_key=key,
                    content_type=content_type,
                    width=0,
                    height=0,
                    byte_size=len(data),
                    origin=origin,
                    source_url=source_url,
                    rendition_key="master",
                    checksum=checksum,
                    default_alt_text=_alt(alt_text),
                    original_filename=(filename or "")[:255] or None,
                    uploaded_by_user_id=user.user_id,
                )
                s.add(asset)
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "media_asset.created",
                        workspace_id=workspace_id,
                        subject_type="media_asset",
                        subject_id=asset_id,
                        after={
                            "origin": origin,
                            "media_type": media_type,
                            "bytes": len(data),
                            "sha256": checksum.hex(),
                        },
                    ),
                )
                await s.refresh(asset)
                return _view(asset)
        except BaseException:
            await self._store.delete(key)
            raise

    async def from_remote_url(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        url: str,
        content_type: str | None,
        filename: str | None,
        alt_text: str | None,
    ) -> MediaView:
        try:
            fetched = await self._fetcher.get(
                url,
                headers={"Accept": "image/*,video/mp4,video/quicktime,application/pdf"},
                max_bytes=MAX_FILE_BYTES,
                accept_statuses=frozenset({200}),
            )
        except FetchError as exc:
            code, message = public_message(exc.kind, str(exc))
            raise InvalidInput(message, code=f"remote_{code}", field="url") from exc
        detected = content_type or fetched.headers.get("content-type", "").split(";", 1)[0]
        return await self.upload_file(
            user,
            workspace_id,
            meta,
            data=fetched.body,
            content_type=detected,
            filename=filename,
            alt_text=alt_text,
            origin="remote_url",
            source_url=url,
        )

    async def from_content_item(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        content_item_id: uuid.UUID,
    ) -> MediaView:
        """The article's own preview image, fetched through the SSRF guard."""
        await self._upload_budget(user)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
            await self._writable(s, workspace_id)
            item = await s.get(ContentItem, content_item_id)
            if item is None or item.state == "filtered":
                raise NotFoundError("Content item not found.")
            url = item.og_image_url
            title = item.title
        if not url:
            raise InvalidInput(
                "This article has no preview image.", code="no_og_image", field="content_item_id"
            )
        try:
            fetched = await self._fetcher.get(
                url,
                headers={"Accept": "image/jpeg, image/png, image/webp"},
                max_bytes=images.MAX_UPLOAD_BYTES,
                accept_statuses=frozenset({200}),
            )
        except FetchError as exc:
            code, message = public_message(exc.kind, str(exc))
            raise InvalidInput(
                f"The article's image could not be fetched: {message}",
                code=f"og_image_{code}",
                field="content_item_id",
            ) from exc
        try:
            info = await asyncio.to_thread(images.probe, fetched.body)
        except images.ImageRejected as exc:
            raise _reject(exc) from exc
        return await self._ingest(
            user,
            workspace_id,
            meta,
            fetched.body,
            info,
            origin="og_image",
            alt=_alt(f"Preview image for: {title}"[:MAX_ALT]) if title else None,
            source_url=url,
            content_item_id=content_item_id,
        )

    async def ingest_generated(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        data: bytes,
        info: images.ImageInfo,
        *,
        alt: str | None,
    ) -> MediaView:
        """A generated image (P2-M5): the same pipeline as an upload, origin ``generated``,
        on behalf of the person who asked for it. The quota still applies."""
        return await self._ingest(
            user,
            workspace_id,
            meta,
            data,
            info,
            origin="generated",
            alt=_alt(alt) if alt else None,
            source_url=None,
            content_item_id=None,
        )

    async def _ingest(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        data: bytes,
        info: images.ImageInfo,
        *,
        origin: str,
        alt: str | None,
        source_url: str | None,
        content_item_id: uuid.UUID | None,
    ) -> MediaView:
        checksum = hashlib.sha256(data).digest()
        async with self._tx(user, workspace_id, meta) as s:
            existing = await _master_by_checksum(s, checksum)
            if existing is not None:
                return _view(existing, await _renditions(s, existing.id))
            await self._within_quota(s, len(data))
        asset_id = uuid7()
        key = f"ws/{workspace_id}/{asset_id}.{images.EXTENSIONS[info.content_type]}"
        await self._store.put(key, data, info.content_type)
        try:
            async with self._tx(user, workspace_id, meta) as s:
                await self._within_quota(s, len(data), lock=True)
                asset = MediaAsset(
                    id=asset_id,
                    workspace_id=workspace_id,
                    media_type="image",
                    object_key=key,
                    content_type=info.content_type,
                    width=info.width,
                    height=info.height,
                    byte_size=len(data),
                    origin=origin,
                    source_url=source_url,
                    content_item_id=content_item_id,
                    rendition_key="master",
                    checksum=checksum,
                    default_alt_text=alt,
                    uploaded_by_user_id=user.user_id,
                )
                s.add(asset)
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "media_asset.created",
                        workspace_id=workspace_id,
                        subject_type="media_asset",
                        subject_id=asset_id,
                        after={"origin": origin, "bytes": len(data), "sha256": checksum.hex()},
                    ),
                )
                await s.refresh(asset)
                return _view(asset)
        except IntegrityError:
            # The same bytes arrived concurrently: keep the winner, drop our copy.
            await self._store.delete(key)
            async with self._tx(user, workspace_id, meta) as s:
                winner = await _master_by_checksum(s, checksum)
                if winner is None:
                    raise
                return _view(winner, await _renditions(s, winner.id))
        except BaseException:
            await self._store.delete(key)
            raise

    # ---------------------------------------------------------------------- reading

    async def list_media(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, limit: int
    ) -> list[MediaView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            rows = (
                await s.execute(
                    select(MediaAsset)
                    .where(MediaAsset.parent_asset_id.is_(None))
                    .order_by(MediaAsset.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
            return [_view(r) for r in rows]

    async def get(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        asset_id: uuid.UUID,
    ) -> MediaView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            a = await _master(s, asset_id)
            return _view(a, await _renditions(s, a.id))

    async def content(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        asset_id: uuid.UUID,
    ) -> tuple[bytes, str, str]:
        """Bytes of a master or rendition, its content type and checksum (for an ETag)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            a = await s.get(MediaAsset, asset_id)
            if a is None:
                raise NotFoundError("Media not found.")
            key, content_type, checksum = a.object_key, a.content_type, a.checksum.hex()
        try:
            data = await self._store.get(key)
        except ObjectNotFound as exc:
            raise NotFoundError("Media not found.") from exc
        if hashlib.sha256(data).hexdigest() != checksum:
            # What is served must be what the version's hash names (review S7-L11).
            log.error("media.checksum_mismatch", asset_id=str(asset_id))
            raise ConflictError("This image's stored bytes are damaged.", code="media_corrupt")
        return data, content_type, checksum

    async def update_alt_text(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        asset_id: uuid.UUID,
        alt_text: str | None,
    ) -> MediaView:
        """The default for future attachments; attached copies change only via a new version."""
        alt = _alt(alt_text)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
            await self._writable(s, workspace_id)
            a = await _master(s, asset_id, lock=True)
            a.default_alt_text = alt
            await s.flush()
            return _view(a, await _renditions(s, a.id))

    async def delete(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        asset_id: uuid.UUID,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
            await self._writable(s, workspace_id)
            a = await _master(s, asset_id, lock=True)
            in_use = await s.scalar(
                select(func.count())
                .select_from(PostVersion)
                .where(PostVersion.media.contains([{"master_id": str(a.id)}]))
            )
            if in_use:
                # Versions are immutable records of what was approved and published; the
                # image they name must stay readable.
                raise ConflictError(
                    "This image is part of a post's history and cannot be deleted.",
                    code="media_in_use",
                )
            keys = [a.object_key, *(r.object_key for r in await _renditions(s, a.id))]
            await s.delete(a)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "media_asset.deleted",
                    workspace_id=workspace_id,
                    subject_type="media_asset",
                    subject_id=asset_id,
                ),
            )
        for key in keys:
            await self._store.delete(key)

    # ---------------------------------------------------------------------- attach

    async def set_variant_media(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        *,
        choices: list[MediaChoice],
        based_on_version_id: uuid.UUID | None,
    ) -> None:
        """Replace the variant's media with ``choices`` (in order) as a new version: same
        text, the platform's rendition of each image, alt text per image."""
        if len(choices) > MAX_ATTACHED:
            raise InvalidInput(
                f"At most {MAX_ATTACHED} images per post.", code="too_many_media", field="media"
            )
        if len({c.asset_id for c in choices}) != len(choices):
            raise InvalidInput("Each image can be attached once.", code="duplicate_media")
        async with self._tx(user, workspace_id, meta) as s:
            ps, v = await self._editable_variant(
                s, user, workspace_id, set_id, variant_id, based_on_version_id
            )
            caps = capabilities_for(Platform(v.platform))
            plans: list[tuple[MediaAsset, MediaAspect | None, str | None]] = []
            for c in choices:
                master = await _master(s, c.asset_id)
                if master.media_type != "image" and choices != [c]:
                    raise InvalidInput(
                        "Videos and documents must be attached alone.", code="mixed_media"
                    )
                if master.media_type == "document" and not v.platform.startswith("linkedin"):
                    raise InvalidInput(
                        "Documents are only supported for LinkedIn.",
                        code="platform_media_unsupported",
                    )
                aspect = (
                    _aspect(caps.media_aspects, c.aspect) if master.media_type == "image" else None
                )
                alt = _alt(c.alt_text) if c.alt_text is not None else master.default_alt_text
                plans.append((master, aspect, alt))
        # Renditions are derived outside the variant lock (CPU work, file writes).
        entries = []
        for master, aspect, alt in plans:
            r = (
                await self._ensure_rendition(user, workspace_id, meta, master, aspect, caps)
                if aspect is not None
                else master
            )
            entries.append(
                {
                    "asset_id": str(r.id),
                    "master_id": str(master.id),
                    "checksum": r.checksum.hex(),
                    "alt_text": alt,
                    "aspect": aspect.key if aspect is not None else None,
                    "media_type": master.media_type,
                    "width": r.width,
                    "height": r.height,
                    "content_type": r.content_type,
                }
            )
        async with self._tx(user, workspace_id, meta) as s:
            ps, v = await self._editable_variant(
                s, user, workspace_id, set_id, variant_id, based_on_version_id, lock=True
            )
            # Re-read every image under a share lock: a delete that committed after the
            # renditions were derived must not leave the version naming missing bytes, and
            # a delete cannot commit while this version is being written (review S7-H1).
            ids = {uuid.UUID(str(e["asset_id"])) for e in entries} | {
                uuid.UUID(str(e["master_id"])) for e in entries
            }
            if ids:
                present = set(
                    (
                        await s.execute(
                            select(MediaAsset.id)
                            .where(MediaAsset.id.in_(ids))
                            .with_for_update(read=True)
                        )
                    ).scalars()
                )
                if present != ids:
                    raise ConflictError(
                        "An image was deleted while this was saving; pick the images again.",
                        code="media_missing",
                    )
            prior = await s.get(PostVersion, v.current_version_id)
            assert prior is not None
            version = await core.create_version(
                s,
                v,
                core.ContentInput(
                    prior.body,
                    list(prior.thread_parts) if prior.thread_parts else None,
                    list(prior.hashtags),
                    prior.link_url,
                    entries,
                    prior.first_comment,
                ),
                source="human",
                # The text is unchanged, so its embedding and the Editor's review still hold.
                embedding=list(prior.embedding) if prior.embedding is not None else None,
                voice=await core.voice_for_variant(s, v),
                user_id=user.user_id,
                confidence=prior.confidence_score,
                compliance_flags=list(prior.compliance_flags),
                agent_notes=dict(prior.agent_notes),
                hashtag_suggestions=list(prior.hashtag_suggestions),
            )
            if version.id == prior.id:
                return  # nothing changed
            await core.refresh_set(s, ps)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "post_version.media_changed",
                    workspace_id=workspace_id,
                    subject_type="post_version",
                    subject_id=version.id,
                    social_account_id=v.social_account_id,
                    subject_content_hash=version.content_hash,
                    after={
                        "variant_id": str(v.id),
                        "version_no": version.version_no,
                        "media": [e["checksum"] for e in entries],
                    },
                ),
            )

    async def _editable_variant(
        self,
        s: AsyncSession,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        based_on_version_id: uuid.UUID | None,
        *,
        lock: bool = False,
    ) -> tuple[PostSet, PostVariant]:
        principal = await load_user_principal(s, user, workspace_id)
        ps = await s.get(PostSet, set_id, with_for_update=lock)
        if ps is None:
            raise NotFoundError("Post set not found.")
        v = await s.get(PostVariant, variant_id, with_for_update=lock)
        if v is None or v.post_set_id != set_id:
            raise NotFoundError("Variant not found.")
        groups = list(
            (
                await s.execute(
                    select(AccountGroupMember.account_group_id).where(
                        AccountGroupMember.social_account_id == v.social_account_id
                    )
                )
            ).scalars()
        )
        self._require_for_account(
            principal, Permission.EDIT_POSTS, workspace_id, v.social_account_id, groups
        )
        await self._writable(s, workspace_id)
        if ps.state == PostSetState.ARCHIVED.value:
            raise ConflictError("This post set is archived.", code="post_set_archived")
        if v.current_version_id is None:
            raise ConflictError(
                "Draft this account's text before adding images.", code="not_drafted"
            )
        if based_on_version_id is None:
            raise InvalidInput(
                "Say which version you changed (based_on_version_id).",
                code="based_on_version_required",
                field="based_on_version_id",
            )
        if v.current_version_id != based_on_version_id:
            raise ConflictError(VERSION_CONFLICT, code="version_conflict")
        return ps, v

    async def _ensure_rendition(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        master: MediaAsset,
        aspect: MediaAspect,
        caps: Any,
    ) -> MediaAsset:
        base_key = f"{aspect.key}@{aspect.width}x{aspect.height}"
        cap = int(caps.max_image_bytes)
        async with self._tx(user, workspace_id, meta) as s:
            fitting = (
                await s.execute(
                    select(MediaAsset)
                    .where(
                        MediaAsset.parent_asset_id == master.id,
                        MediaAsset.rendition_key.in_([base_key, f"{base_key}~{cap}"]),
                        MediaAsset.byte_size <= cap,
                    )
                    .order_by(MediaAsset.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if fitting is not None:
                return fitting
            taken = await s.scalar(
                select(func.count())
                .select_from(MediaAsset)
                .where(
                    MediaAsset.parent_asset_id == master.id, MediaAsset.rendition_key == base_key
                )
            )
            await self._within_quota(s, cap)
        rendition_key = f"{base_key}~{cap}" if taken else base_key
        try:
            source = await self._store.get(master.object_key)
        except ObjectNotFound as exc:
            raise ConflictError("The original image is missing.", code="media_missing") from exc
        try:
            out = await asyncio.to_thread(images.derive, source, aspect.width, aspect.height, cap)
        except images.ImageRejected as exc:
            raise _reject(exc) from exc
        rid = uuid7()
        key = f"ws/{workspace_id}/{master.id}/{rid}.jpg"
        await self._store.put(key, out.data, out.content_type)
        try:
            async with self._tx(user, workspace_id, meta) as s:
                await self._within_quota(s, len(out.data), lock=True)
                if await s.get(MediaAsset, master.id, with_for_update=True) is None:
                    raise NotFoundError("Media not found.")  # deleted while deriving
                r = MediaAsset(
                    id=rid,
                    workspace_id=workspace_id,
                    media_type="image",
                    object_key=key,
                    content_type=out.content_type,
                    width=out.width,
                    height=out.height,
                    byte_size=len(out.data),
                    origin="rendition",
                    parent_asset_id=master.id,
                    rendition_key=rendition_key,
                    checksum=hashlib.sha256(out.data).digest(),
                    default_alt_text=None,
                    uploaded_by_user_id=user.user_id,
                )
                s.add(r)
                await s.flush()
                await s.refresh(r)
                return r
        except IntegrityError:
            # Derived concurrently; derivation is deterministic, so the winner is equivalent.
            await self._store.delete(key)
            async with self._tx(user, workspace_id, meta) as s:
                winner = (
                    await s.execute(
                        select(MediaAsset).where(
                            MediaAsset.parent_asset_id == master.id,
                            MediaAsset.rendition_key == rendition_key,
                        )
                    )
                ).scalar_one()
                return winner
        except BaseException:
            await self._store.delete(key)
            raise

    # ---------------------------------------------------------------------- helpers

    async def _upload_budget(self, user: AuthenticatedUser) -> None:
        actor = user.api_key.api_key_id if user.api_key else user.user_id
        decision = await take(self._sm, f"media_upload:{actor}", UPLOAD_LIMIT)
        if not decision.allowed:
            raise TooManyAttempts(
                "Too many uploads; try again shortly.", retry_after=decision.retry_after
            )

    async def _within_quota(self, s: AsyncSession, incoming: int, *, lock: bool = False) -> None:
        if lock:
            # One writer per workspace between the check and the insert, so parallel
            # uploads cannot all pass the same check (review S7-M6).
            await s.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtext("
                    "'media_quota:' || current_setting('mera.workspace_id')))"
                )
            )
        used = await s.scalar(select(func.coalesce(func.sum(MediaAsset.byte_size), 0))) or 0
        if int(used) + incoming > WORKSPACE_MEDIA_BYTES:
            raise ConflictError(
                "This workspace's media storage is full; delete unused images first.",
                code="media_quota_exceeded",
            )

    async def _writable(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        ws = await s.get(Workspace, workspace_id)
        if ws is None:
            raise NotFoundError("Workspace not found.")
        self._ensure_writable(ws)


def _aspect(aspects: tuple[MediaAspect, ...], key: str | None) -> MediaAspect:
    if key is None:
        return aspects[0]
    for a in aspects:
        if a.key == key:
            return a
    raise InvalidInput(
        f"This platform takes {', '.join(a.key for a in aspects)} images.",
        code="unsupported_aspect",
        field="aspect",
    )


async def _master(s: AsyncSession, asset_id: uuid.UUID, *, lock: bool = False) -> MediaAsset:
    a = await s.get(MediaAsset, asset_id, with_for_update=lock)
    if a is None or a.parent_asset_id is not None:
        raise NotFoundError("Media not found.")
    return a


async def _master_by_checksum(s: AsyncSession, checksum: bytes) -> MediaAsset | None:
    return (
        await s.execute(
            select(MediaAsset).where(
                MediaAsset.parent_asset_id.is_(None), MediaAsset.checksum == checksum
            )
        )
    ).scalar_one_or_none()


async def _renditions(s: AsyncSession, master_id: uuid.UUID) -> list[MediaAsset]:
    return list(
        (
            await s.execute(
                select(MediaAsset)
                .where(MediaAsset.parent_asset_id == master_id)
                .order_by(MediaAsset.created_at)
            )
        ).scalars()
    )
