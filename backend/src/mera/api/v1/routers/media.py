"""Media (docs/04 §4.7): upload, the article's image, renditions, attach to a variant."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import CurrentUser, Media, Meta, Posts
from mera.api.v1.routers.posts import SETS, WS, PostSetOut, _set
from mera.application.errors import InvalidInput, NotFoundError
from mera.application.services.media_service import MediaChoice
from mera.domain.ports.object_store import ObjectNotFound
from mera.infrastructure.media.images import MAX_UPLOAD_BYTES

router = APIRouter(tags=["media"])
files_router = APIRouter(tags=["media"])

# Served bytes are images only, and are never rendered as a document.
_FILE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-site",
}
_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    media_type: str
    content_type: str
    width: int
    height: int
    byte_size: int
    origin: str = Field(description="upload, og_image, generated or rendition")
    source_url: str | None
    rendition_key: str = Field(description="master, or aspect@size, e.g. 4x5@1080x1350")
    parent_asset_id: uuid.UUID | None
    checksum: str = Field(description="sha256 hex; what a version's content hash covers")
    default_alt_text: str | None
    created_at: datetime
    renditions: list[MediaOut] = Field(default_factory=list)


class MediaList(BaseModel):
    data: list[MediaOut]


class FromContentItemIn(BaseModel):
    content_item_id: uuid.UUID


class AltTextIn(BaseModel):
    alt_text: str | None = Field(default=None, max_length=1000)


class MediaChoiceIn(BaseModel):
    asset_id: uuid.UUID = Field(description="A master asset")
    aspect: str | None = Field(
        default=None, max_length=16, description="One of the platform's aspects; default first"
    )
    alt_text: str | None = Field(
        default=None, max_length=1000, description="Default: the asset's default alt text"
    )


class VariantMediaIn(BaseModel):
    media: list[MediaChoiceIn] = Field(max_length=20, description="In order (carousel order)")
    based_on_version_id: uuid.UUID = Field(description="The version you changed")


@router.post(f"{WS}/media", response_model=MediaOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    workspace_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP, up to 20 MB")],
    user: CurrentUser,
    svc: Media,
    meta: Meta,
    alt_text: Annotated[str | None, Form(max_length=1000)] = None,
) -> MediaOut:
    """Upload an image. The same bytes uploaded twice return the existing asset."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidInput("Images can be at most 20 MB.", code="file_too_large", field="file")
    view = await svc.upload(user, workspace_id, meta, data=data, alt_text=alt_text)
    return MediaOut.model_validate(view)


@router.post(
    f"{WS}/media/from-content-item", response_model=MediaOut, status_code=status.HTTP_201_CREATED
)
async def media_from_content_item(
    workspace_id: uuid.UUID, body: FromContentItemIn, user: CurrentUser, svc: Media, meta: Meta
) -> MediaOut:
    """Fetch the article's preview (OG) image into the media library."""
    view = await svc.from_content_item(user, workspace_id, meta, body.content_item_id)
    return MediaOut.model_validate(view)


@router.get(f"{WS}/media", response_model=MediaList)
async def list_media(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Media,
    meta: Meta,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MediaList:
    """The media library: masters, newest first."""
    rows = await svc.list_media(user, workspace_id, meta, limit)
    return MediaList(data=[MediaOut.model_validate(r) for r in rows])


@router.get(f"{WS}/media/{{asset_id}}", response_model=MediaOut)
async def get_media(
    workspace_id: uuid.UUID, asset_id: uuid.UUID, user: CurrentUser, svc: Media, meta: Meta
) -> MediaOut:
    """A master and its per-platform renditions."""
    return MediaOut.model_validate(await svc.get(user, workspace_id, meta, asset_id))


@router.get(
    f"{WS}/media/{{asset_id}}/content",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}}},
)
async def media_content(
    workspace_id: uuid.UUID,
    asset_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    svc: Media,
    meta: Meta,
) -> Response:
    """The image bytes of a master or a rendition (immutable: cache by checksum)."""
    data, content_type, checksum = await svc.content(user, workspace_id, meta, asset_id)
    etag = f'"{checksum}"'
    headers = {**_FILE_HEADERS, "ETag": etag, "Cache-Control": "private, max-age=86400"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=data, media_type=content_type, headers=headers)


class AltSuggestIn(BaseModel):
    platform: str | None = Field(default=None, max_length=40)
    post_excerpt: str | None = Field(default=None, max_length=500)


class AltSuggestOut(BaseModel):
    alt_text: str = Field(description="A suggestion; nothing is saved until you save it")
    contains_text: bool
    confidence: float


@router.post(f"{WS}/media/{{asset_id}}/alt-text/suggest", response_model=AltSuggestOut)
async def suggest_media_alt_text(
    workspace_id: uuid.UUID,
    asset_id: uuid.UUID,
    user: CurrentUser,
    svc: Media,
    meta: Meta,
    body: AltSuggestIn | None = None,
) -> AltSuggestOut:
    """A suggested description of the image from a vision model (P2-M1.8). Review and
    edit it, then save it as the image's alt text; text shown inside the image is never
    treated as instructions."""
    out = await svc.suggest_alt_text(
        user,
        workspace_id,
        meta,
        asset_id,
        platform=body.platform if body else None,
        post_excerpt=body.post_excerpt if body else None,
    )
    return AltSuggestOut(
        alt_text=out.alt_text, contains_text=out.contains_text, confidence=out.confidence
    )


@router.patch(f"{WS}/media/{{asset_id}}/alt-text", response_model=MediaOut)
async def update_media_alt_text(
    workspace_id: uuid.UUID,
    asset_id: uuid.UUID,
    body: AltTextIn,
    user: CurrentUser,
    svc: Media,
    meta: Meta,
) -> MediaOut:
    """The default alt text for future attachments. Attached images keep theirs until the
    post's media is set again (that is a new version)."""
    view = await svc.update_alt_text(user, workspace_id, meta, asset_id, body.alt_text)
    return MediaOut.model_validate(view)


@router.delete(f"{WS}/media/{{asset_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(
    workspace_id: uuid.UUID, asset_id: uuid.UUID, user: CurrentUser, svc: Media, meta: Meta
) -> Response:
    """Refused while any version (draft or history) uses the image."""
    await svc.delete(user, workspace_id, meta, asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(f"{SETS}/{{set_id}}/variants/{{variant_id}}/media", response_model=PostSetOut)
async def set_variant_media(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: VariantMediaIn,
    user: CurrentUser,
    svc: Media,
    posts: Posts,
    meta: Meta,
) -> PostSetOut:
    """Set this account's images, in order, as a new version (same text). Each image is the
    platform's rendition of the chosen aspect; send an empty list to remove them all."""
    await svc.set_variant_media(
        user,
        workspace_id,
        meta,
        set_id,
        variant_id,
        choices=[MediaChoice(c.asset_id, c.aspect, c.alt_text) for c in body.media],
        based_on_version_id=body.based_on_version_id,
    )
    return _set(await posts.get(user, workspace_id, set_id, meta))


@files_router.get(
    "/media-files/{key:path}",
    response_class=Response,
    include_in_schema=False,
)
async def signed_media_file(
    key: str,
    request: Request,
    exp: Annotated[int, Query()],
    sig: Annotated[str, Query(max_length=64)],
) -> Response:
    """A presigned rendition URL (platforms that fetch media by URL). No session: the HMAC
    signature and expiry are the authorisation, and only that one key is readable."""
    store = request.app.state.object_store
    if not store.verify(key, exp, sig):
        raise NotFoundError("Not found.")
    try:
        data = await store.get(key)
    except ObjectNotFound as exc:
        raise NotFoundError("Not found.") from exc
    ext = key.rsplit(".", 1)[-1]
    return Response(
        content=data,
        media_type=_TYPES.get(ext, "application/octet-stream"),
        headers={**_FILE_HEADERS, "Cache-Control": "private, no-store"},
    )
