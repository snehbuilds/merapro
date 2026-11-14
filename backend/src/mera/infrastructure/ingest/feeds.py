"""RSS/Atom parsing and article extraction (M4.2, 4.3). No model involved.

Parsing untrusted XML is CPU work an attacker controls, so (security review H1):

- A body that declares a DTD (``<!DOCTYPE`` / ``<!ENTITY``) is refused before parsing:
  feedparser expands internal entities, and a few hundred bytes of nested entities cost
  seconds of CPU and megabytes of text. Real feeds do not need a DTD.
- ``parse_feed_async`` and ``extract_article_async`` run on a small dedicated thread pool
  with a timeout, never on the event loop, and never on the default pool that DNS and
  other work share. A stuck parse can occupy at most that pool.
- At most ``MAX_ENTRIES`` entries are converted, and NUL characters (which Postgres text
  rejects, losing the whole batch) are stripped from every field.
"""

from __future__ import annotations

import asyncio
import calendar
import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import py3langid
import trafilatura

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
MAX_TEXT_CHARS = 20_000
MAX_ENTRIES = 300
PARSE_TIMEOUT = 15.0
_PARSE_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mera-parse")


def _clean(v: str) -> str:
    return v.replace("\x00", "")


@dataclass(frozen=True, slots=True)
class FeedEntry:
    url: str
    title: str
    author: str | None
    published_at: datetime | None
    text: str  # plain text from the feed's content or summary
    image_url: str | None


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    title: str | None
    entries: list[FeedEntry]
    bozo: bool  # feedparser found the XML malformed (it still parses what it can)


@dataclass(frozen=True, slots=True)
class Extracted:
    text: str
    title: str | None
    author: str | None
    published_at: datetime | None
    image_url: str | None


class NotAFeed(ValueError):
    pass


def html_to_text(fragment: str) -> str:
    return _clean(_WS.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip())


def _when(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.fromtimestamp(calendar.timegm(t), tz=UTC)
            except (OverflowError, ValueError, TypeError):
                continue
    return None


def _image(entry: Any) -> str | None:
    for media in entry.get("media_content", []) or []:
        url = media.get("url")
        if url and str(media.get("medium", "image")) == "image":
            return str(url)
    for thumb in entry.get("media_thumbnail", []) or []:
        if thumb.get("url"):
            return str(thumb["url"])
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image/"):
            return str(link.get("href"))
    return None


def parse_feed(body: bytes, *, base_url: str) -> ParsedFeed:
    if _DTD.search(body):
        raise NotAFeed("feeds that declare a DTD or entities are not accepted")
    parsed = feedparser.parse(body, response_headers={"content-location": base_url})
    version = parsed.get("version") or ""
    if not version and not parsed.entries:
        raise NotAFeed("the response is not an RSS or Atom feed")
    entries: list[FeedEntry] = []
    for e in parsed.entries[:MAX_ENTRIES]:
        url = e.get("link") or ""
        if not url:
            continue
        content = ""
        for c in e.get("content", []) or []:
            if c.get("value"):
                content = str(c["value"])
                break
        text = html_to_text(content or str(e.get("summary") or e.get("description") or ""))
        entries.append(
            FeedEntry(
                url=_clean(str(url)).strip(),
                title=html_to_text(str(e.get("title") or ""))[:500] or str(url),
                author=(_clean(str(e.get("author")))[:200] if e.get("author") else None),
                published_at=_when(e),
                text=text[:MAX_TEXT_CHARS],
                image_url=_image(e),
            )
        )
    title = parsed.feed.get("title") if hasattr(parsed, "feed") else None
    return ParsedFeed(
        title=str(title)[:200] if title else None, entries=entries, bozo=bool(parsed.get("bozo"))
    )


def extract_article(page_html: str, *, url: str) -> Extracted:
    text = (
        trafilatura.extract(
            page_html, url=url, include_comments=False, include_tables=False, favor_precision=True
        )
        or ""
    )
    meta = trafilatura.extract_metadata(page_html, default_url=url)
    published: datetime | None = None
    if meta is not None and meta.date:
        try:
            published = datetime.fromisoformat(str(meta.date)).replace(tzinfo=UTC)
        except ValueError:
            published = None
    return Extracted(
        text=_clean(_WS.sub(" ", text).strip())[:MAX_TEXT_CHARS],
        title=(_clean(meta.title) if meta is not None and meta.title else None),
        author=(_clean(meta.author) if meta is not None and meta.author else None),
        published_at=published,
        image_url=(meta.image if meta is not None else None),
    )


def detect_language(text: str) -> str | None:
    sample = text[:2000].strip()
    if len(sample) < 20:
        return None
    lang, _score = py3langid.classify(sample)
    return str(lang)


def decode(body: bytes, content_type: str | None) -> str:
    charset = None
    if content_type and "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip().strip('"')
    for enc in (charset, "utf-8", "latin-1"):
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


class ParseTimeout(ValueError):
    pass


async def _off_loop(fn: Any, *args: Any, **kw: Any) -> Any:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_PARSE_POOL, lambda: fn(*args, **kw)), PARSE_TIMEOUT
        )
    except TimeoutError as exc:
        raise ParseTimeout("parsing took too long") from exc


async def parse_feed_async(body: bytes, *, base_url: str) -> ParsedFeed:
    try:
        result: ParsedFeed = await _off_loop(parse_feed, body, base_url=base_url)
    except ParseTimeout as exc:
        raise NotAFeed("the feed took too long to parse") from exc
    return result


async def extract_article_async(page_html: str, *, url: str) -> Extracted:
    result: Extracted = await _off_loop(extract_article, page_html, url=url)
    return result
