"""Media acceptance (M5.14): upload, renditions per platform, attach as a new version,
Instagram's media requirement, the article's image, signed URLs and isolation."""

from __future__ import annotations

import io
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from PIL import Image

from tests.integration.test_accounts_m3 import _app, _connect, _setup
from tests.integration.test_drafting_m5 import (
    CapturingDeferrer,
    _run_queued,
    _sets,
    _story,
    deferrer,  # noqa: F401 - shared fixture
)
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import (
    FakeFetcher,
    fetcher,  # noqa: F401 - shared fixture
)

pytestmark = pytest.mark.integration


def _photo(w: int = 3000, h: int = 2000, color: tuple[int, int, int] = (40, 90, 160)) -> bytes:
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (47.0, 36.0, 0.0)}  # GPS location, as a phone would add
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _media(ws: str, suffix: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/media{suffix}"


async def _upload(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, data: bytes, alt: str | None = None
) -> httpx.Response:
    return await api.post(
        _media(ws),
        headers=_h(s),
        files={"file": ("photo.jpg", data, "image/jpeg")},
        data={"alt_text": alt} if alt else None,
    )


async def _drafted_set(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, deferrer: CapturingDeferrer
) -> dict[str, Any]:
    ig = (await _connect(api, s, ws, "instagram", handle="ig_brand", kind="business"))["connected"]
    li = (await _connect(api, s, ws, "linkedin_member", handle="li_me"))["connected"]
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={
            "topic": "Why we moved our nightly batch jobs to Postgres 18",
            "targets": {"social_account_ids": [ig, li]},
        },
    )
    assert r.status_code == 201, r.text
    await _run_queued(api, deferrer)
    return (await api.get(_sets(ws, r.json()["id"]), headers=_h(s))).json()


def _variant(ps: dict[str, Any], platform: str) -> dict[str, Any]:
    return next(v for v in ps["variants"] if v["platform"] == platform)


async def test_upload_dedupes_and_refuses_what_is_not_an_image(api: httpx.AsyncClient) -> None:
    s, ws = await _setup(api, "mediaup")
    photo = _photo()
    r = await _upload(api, s, ws, photo, alt="A blue square")
    assert r.status_code == 201, r.text
    m = r.json()
    assert (m["width"], m["height"], m["origin"], m["rendition_key"]) == (
        3000,
        2000,
        "upload",
        "master",
    )
    assert m["default_alt_text"] == "A blue square"
    again = await _upload(api, s, ws, photo)
    assert again.json()["id"] == m["id"]  # same bytes, same asset

    r = await api.post(
        _media(ws), headers=_h(s), files={"file": ("x.html", b"<script>1</script>", "image/jpeg")}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (422, "unsupported_media_type")

    # Oversized uploads are refused before the multipart body is parsed.
    r = await api.post(
        _media(ws),
        headers={
            **_h(s),
            "content-type": "multipart/form-data; boundary=x",
            "content-length": str(30 * 1024 * 1024),
        },
        content=b"--x--",
    )
    assert (r.status_code, r.json()["error"]["code"]) == (413, "file_too_large")

    # A body streamed without a length (as the web proxy sends it) is cut off at the cap.
    async def chunks():
        yield (
            b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n'
            b"Content-Type: image/jpeg\r\n\r\n"
        )
        for _ in range(23):
            yield b"x" * (1024 * 1024)

    r = await api.post(
        _media(ws),
        headers={**_h(s), "content-type": "multipart/form-data; boundary=x"},
        content=chunks(),
    )
    assert (r.status_code, r.json()["error"]["code"]) == (413, "file_too_large")

    listed = (await api.get(_media(ws), headers=_h(s))).json()["data"]
    assert [x["id"] for x in listed] == [m["id"]]
    r = await api.get(_media(ws, f"/{m['id']}/content"), headers=_h(s))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    r2 = await api.get(
        _media(ws, f"/{m['id']}/content"),
        headers={**_h(s), "If-None-Match": r.headers["etag"]},
    )
    assert r2.status_code == 304


async def test_instagram_needs_an_image_and_attaching_one_is_a_new_version(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "mediaig")
    ps = await _drafted_set(api, s, ws, deferrer)
    ig = _variant(ps, "instagram")
    assert ig["state"] == "blocked"
    assert any(i["code"] == "media_required" for i in ig["current_version"]["validation"])
    li = _variant(ps, "linkedin_member")
    assert li["state"] == "drafting"
    asset = (await _upload(api, s, ws, _photo(), alt="Our batch dashboard")).json()

    before = ig["current_version"]
    url = _sets(ws, ps["id"], f"/variants/{ig['id']}/media")
    r = await api.put(
        url,
        headers=_h(s),
        json={"media": [{"asset_id": asset["id"]}], "based_on_version_id": before["id"]},
    )
    assert r.status_code == 200, r.text
    ig = _variant(r.json(), "instagram")
    cv = ig["current_version"]
    assert cv["version_no"] == before["version_no"] + 1
    assert cv["body"] == before["body"]  # same text
    assert cv["content_hash"] != before["content_hash"]  # media is inside the hash
    assert ig["state"] == "drafting", ig["guardrails"]
    [entry] = cv["media"]
    assert (entry["aspect"], entry["width"], entry["height"]) == ("4x5", 1080, 1350)
    assert entry["master_id"] == asset["id"]
    assert entry["alt_text"] == "Our batch dashboard"
    # The version shows the rendition that goes out, and that rendition has no location data.
    r = await api.get(_media(ws, f"/{entry['asset_id']}/content"), headers=_h(s))
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (1080, 1350)
        assert not im.getexif()

    # Stale edits and shapes the platform does not take are refused.
    r = await api.put(
        url,
        headers=_h(s),
        json={"media": [{"asset_id": asset["id"]}], "based_on_version_id": before["id"]},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "version_conflict")
    r = await api.put(
        url,
        headers=_h(s),
        json={
            "media": [{"asset_id": asset["id"], "aspect": "16x9"}],
            "based_on_version_id": cv["id"],
        },
    )
    assert (r.status_code, r.json()["error"]["code"]) == (422, "unsupported_aspect")

    # LinkedIn gets its own crop of the same master, derived once and reused.
    li = _variant(r.json() if r.status_code == 200 else ps, "linkedin_member")
    r = await api.put(
        _sets(ws, ps["id"], f"/variants/{li['id']}/media"),
        headers=_h(s),
        json={
            "media": [{"asset_id": asset["id"], "alt_text": "Dashboard, LinkedIn crop"}],
            "based_on_version_id": li["current_version"]["id"],
        },
    )
    assert r.status_code == 200, r.text
    [li_entry] = _variant(r.json(), "linkedin_member")["current_version"]["media"]
    assert (li_entry["aspect"], li_entry["width"], li_entry["height"]) == ("1.91x1", 1200, 628)
    detail = (await api.get(_media(ws, f"/{asset['id']}"), headers=_h(s))).json()
    assert sorted(r["rendition_key"] for r in detail["renditions"]) == [
        "1.91x1@1200x628",
        "4x5@1080x1350",
    ]

    # The diff names exactly what the media change did, and that the text did not move.
    r = await api.get(
        _sets(ws, ps["id"], f"/variants/{ig['id']}/versions/{before['id']}/diff/{cv['id']}"),
        headers=_h(s),
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["from_version_no"], d["to_version_no"]) == (before["version_no"], cv["version_no"])
    assert d["same_content"] is False
    assert d["body_changed"] is False
    assert d["media_added"] == [entry["checksum"]]
    assert d["media_removed"] == []
    # A version of another account's post is not reachable through this variant.
    r = await api.get(
        _sets(
            ws,
            ps["id"],
            f"/variants/{ig['id']}/versions/{before['id']}/diff/{li['current_version']['id']}",
        ),
        headers=_h(s),
    )
    assert r.status_code == 404

    # An image in a post's history cannot be deleted out from under it.
    r = await api.delete(_media(ws, f"/{asset['id']}"), headers=_h(s))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "media_in_use")

    # Removing the image is a new version too, and Instagram is blocked again.
    r = await api.put(url, headers=_h(s), json={"media": [], "based_on_version_id": cv["id"]})
    assert r.status_code == 200, r.text
    ig = _variant(r.json(), "instagram")
    assert ig["current_version"]["media"] == []
    assert ig["state"] == "blocked"

    # A text edit keeps the images that are attached.
    r = await api.put(
        url,
        headers=_h(s),
        json={
            "media": [{"asset_id": asset["id"]}],
            "based_on_version_id": ig["current_version"]["id"],
        },
    )
    ig = _variant(r.json(), "instagram")
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{ig['id']}/versions"),
        headers=_h(s),
        json={
            "body": "Edited caption about our batch jobs.",
            "based_on_version_id": ig["current_version"]["id"],
        },
    )
    assert r.status_code == 201, r.text
    assert len(_variant(r.json(), "instagram")["current_version"]["media"]) == 1


async def test_media_is_workspace_private(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "mediaown")
    asset = (await _upload(api, s, ws, _photo(color=(200, 10, 10)))).json()
    s2, ws2 = await _setup(api, "mediaother")
    ps2 = await _drafted_set(api, s2, ws2, deferrer)
    ig2 = _variant(ps2, "instagram")
    # Another workspace can neither read the asset nor attach it by id.
    r = await api.get(_media(ws2, f"/{asset['id']}/content"), headers=_h(s2))
    assert r.status_code == 404
    r = await api.put(
        _sets(ws2, ps2["id"], f"/variants/{ig2['id']}/media"),
        headers=_h(s2),
        json={
            "media": [{"asset_id": asset["id"]}],
            "based_on_version_id": ig2["current_version"]["id"],
        },
    )
    assert r.status_code == 404
    r = await api.get(_media(ws, f"/{asset['id']}/content"), headers=_h(s2))
    assert r.status_code in (403, 404)


async def test_article_image_and_signed_urls(api: httpx.AsyncClient, fetcher: FakeFetcher) -> None:
    s, ws = await _setup(api, "mediaog")
    item = await _story(api, s, ws, fetcher)
    svc = _app(api).state.media_service
    original = svc._fetcher
    svc._fetcher = fetcher
    try:
        r = await api.post(
            _media(ws, "/from-content-item"), headers=_h(s), json={"content_item_id": item}
        )
        assert (r.status_code, r.json()["error"]["code"]) == (422, "no_og_image")
        from sqlalchemy import update

        from mera.domain.context import WorkspaceContext
        from mera.infrastructure.db.models import ContentItem
        from mera.infrastructure.db.session import workspace_transaction
        from tests.integration.test_ingest_m4 import _sm

        og = "https://cdn.story.example/og.jpg"
        async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
            await tx.execute(
                update(ContentItem).where(ContentItem.id == uuid.UUID(item)).values(og_image_url=og)
            )
        fetcher.pages[og] = _photo(1200, 630)
        r = await api.post(
            _media(ws, "/from-content-item"), headers=_h(s), json={"content_item_id": item}
        )
        assert r.status_code == 201, r.text
        m = r.json()
        assert (m["origin"], m["source_url"]) == ("og_image", og)
        assert m["default_alt_text"].startswith("Preview image for:")
    finally:
        svc._fetcher = original

    store = _app(api).state.object_store
    from sqlalchemy import select

    from mera.infrastructure.db.models import MediaAsset

    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        key = (
            await tx.execute(
                select(MediaAsset.object_key).where(MediaAsset.id == uuid.UUID(m["id"]))
            )
        ).scalar_one()
    url = await store.presigned_url(key, timedelta(minutes=5))
    path = url.split("://", 1)[1].split("/", 1)[1]
    r = await api.get("/" + path)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    flipped = path[:-1] + ("0" if path[-1] != "0" else "1")
    r = await api.get("/" + flipped)
    assert r.status_code == 404
    other = path.replace(key, key.rsplit("/", 1)[0] + "/other.jpg")
    assert (await api.get("/" + other)).status_code == 404
