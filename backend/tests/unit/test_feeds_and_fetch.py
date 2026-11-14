"""M4.2/4.3/4.9: feed parsing, extraction, and the SSRF guard on outbound fetches."""

from __future__ import annotations

import gzip
import ipaddress
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from mera.infrastructure.http.safe_fetch import (
    AddressPolicy,
    FetchError,
    SafeFetcher,
    is_public,
    validate_public_url,
)
from mera.infrastructure.ingest.feeds import (
    NotAFeed,
    decode,
    detect_language,
    extract_article,
    html_to_text,
    parse_feed,
)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>PG News</title>
<item><title>PostgreSQL 18 Released</title>
<link>https://www.postgresql.org/about/news/pg18/?utm_source=rss</link>
<description>&lt;p&gt;Async I/O &amp;amp; more.&lt;/p&gt;</description>
<author>pgsql@example.org</author>
<pubDate>Thu, 25 Sep 2025 12:00:00 GMT</pubDate>
<media:content url="https://img.example.org/pg.png" medium="image"/></item>
<item><title>No link</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>LWN</title>
<entry><title>Kernel 6.18</title><link href="https://lwn.net/Articles/1/"/>
<updated>2025-11-01T08:00:00Z</updated>
<content type="html">&lt;b&gt;Long&lt;/b&gt; content</content>
<link rel="enclosure" type="image/jpeg" href="https://lwn.net/x.jpg"/></entry></feed>"""

ARTICLE = """<html><head><title>Kafka 4.0</title>
<meta property="og:image" content="https://e.com/k.png">
<meta name="author" content="Jane Doe"></head><body><nav>menu</nav><article>
<h1>Kafka 4.0 removes ZooKeeper</h1>
<p>KRaft becomes the only supported metadata mode in this release of Apache Kafka.</p>
<p>Clusters still on ZooKeeper must migrate before upgrading; the release notes document it.</p>
<p>The change is not reversible, so operators should test the migration in staging first.</p>
</article><footer>(c) 2025</footer></body></html>"""


def test_parse_rss_and_atom() -> None:
    feed = parse_feed(RSS, base_url="https://www.postgresql.org/news.rss")
    assert feed.title == "PG News"
    assert len(feed.entries) == 1  # the entry without a link is skipped
    e = feed.entries[0]
    assert e.title == "PostgreSQL 18 Released"
    assert e.text == "Async I/O & more."
    assert e.published_at is not None
    assert e.published_at.year == 2025
    assert e.image_url == "https://img.example.org/pg.png"

    atom = parse_feed(ATOM, base_url="https://lwn.net/headlines/rss")
    assert atom.entries[0].url == "https://lwn.net/Articles/1/"
    assert atom.entries[0].text == "Long content"
    assert atom.entries[0].image_url == "https://lwn.net/x.jpg"

    with pytest.raises(NotAFeed):
        parse_feed(b"<html><body>hello</body></html>", base_url="https://e.com")


def test_extraction_language_and_decoding() -> None:
    ex = extract_article(ARTICLE, url="https://e.com/kafka")
    assert "KRaft becomes the only supported metadata mode" in ex.text
    assert "menu" not in ex.text
    assert ex.image_url == "https://e.com/k.png"
    assert detect_language("KRaft becomes the only supported metadata mode in this release") == "en"
    assert detect_language("short") is None
    assert (
        detect_language("Die Datenbank wurde heute in einer neuen Version veröffentlicht") == "de"
    )
    assert html_to_text("<p>a&nbsp;<b>b</b></p>") == "a b"
    assert decode("é".encode("latin-1"), "text/html; charset=iso-8859-1") == "é"
    assert decode("é".encode(), None) == "é"
    assert decode(b"\xff\xfe", "text/html; charset=nonsense") == "ÿþ"


@pytest.mark.parametrize(
    ("ip", "public"),
    [
        ("93.184.216.34", True),
        ("2606:4700::6810:85e5", True),
        ("10.0.0.1", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("192.168.1.1", False),
        ("0.0.0.0", False),  # noqa: S104 - a test case, nothing binds
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:10.0.0.1", False),
        ("::ffff:93.184.216.34", True),
        ("64:ff9b::a00:1", False),
        ("2002:a00:1::", False),  # 6to4 wrapping 10.0.0.1
    ],
)
def test_is_public(ip: str, public: bool) -> None:
    assert is_public(ipaddress.ip_address(ip)) is public


def test_validate_public_url() -> None:
    assert validate_public_url("https://example.com/feed") is None
    assert validate_public_url("http://localhost/feed") == "the URL points at a local host"
    assert validate_public_url("http://metadata.internal/x") == "the URL points at a local host"
    assert (
        validate_public_url("http://169.254.169.254/latest")
        == "the URL points at a non-public address"
    )
    assert (
        validate_public_url("https://user:pw@example.com/")
        == "URLs with credentials are not allowed"
    )
    assert validate_public_url("gopher://example.com/") == "only http and https URLs can be fetched"
    assert validate_public_url("http://example.com:22/") == "port 22 is not allowed"


# ---------------------------------------------------------------- live local server


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a: object) -> None:
        pass

    def _send(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        port = self.server.server_address[1]
        p = self.path
        if p == "/feed":
            if self.headers.get("If-None-Match") == '"v1"':
                self._send(304, {})
                return
            self._send(200, {"ETag": '"v1"', "Content-Encoding": "gzip"}, gzip.compress(RSS))
        elif p == "/big":
            self._send(200, {}, b"x" * 5000)
        elif p == "/hop":
            self._send(302, {"Location": f"http://127.0.0.1:{port}/feed"})
        elif p == "/to-private":
            self._send(301, {"Location": f"http://10.0.0.5:{port}/"})
        elif p == "/loop":
            self._send(302, {"Location": "/loop"})
        else:
            self._send(404, {})


@pytest.fixture(scope="module")
def server() -> Iterator[int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield int(srv.server_address[1])
    srv.shutdown()


class LoopbackOnly(AddressPolicy):
    def allows(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return ip == ipaddress.ip_address("127.0.0.1")


async def _resolve(host: str, port: int) -> list[str]:
    answers = {"rebind.example": ["93.184.216.34", "10.0.0.1"]}
    return answers.get(host, ["127.0.0.1"])


def _fetcher(port: int, policy: AddressPolicy | None = None, resolver: Any = None) -> SafeFetcher:
    return SafeFetcher(
        user_agent="test",
        policy=policy or LoopbackOnly(),
        resolver=resolver or _resolve,
        system_trust_store=False,
        allowed_ports=frozenset({port}),
    )


async def test_fetch_conditional_gzip_and_redirects(server: int) -> None:
    f = _fetcher(server)
    url = f"http://feeds.example:{server}/feed"
    r = await f.get(url, max_bytes=1_000_000)
    assert r.status == 200
    assert r.body == RSS
    assert r.headers["etag"] == '"v1"'
    r = await f.get(url, headers={"If-None-Match": '"v1"'}, max_bytes=1_000_000)
    assert r.not_modified
    r = await f.get(f"http://feeds.example:{server}/hop", max_bytes=1_000_000)
    assert r.url.endswith("/feed")
    assert r.status == 200


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("/big", "too_large"),
        ("/to-private", "blocked_address"),
        ("/loop", "too_many_redirects"),
        ("/missing", "http_status"),
    ],
)
async def test_fetch_refusals(server: int, path: str, kind: str) -> None:
    with pytest.raises(FetchError) as ei:
        await _fetcher(server).get(f"http://feeds.example:{server}{path}", max_bytes=1000)
    assert ei.value.kind == kind


async def test_guard_checks_every_resolved_address_and_the_default_policy(server: int) -> None:
    default = AddressPolicy()
    # One public and one private answer: refused (DNS rebinding / split answers).
    with pytest.raises(FetchError) as ei:
        await _fetcher(server, policy=default).get(
            f"http://rebind.example:{server}/feed", max_bytes=1000
        )
    assert ei.value.kind == "blocked_address"
    # The production policy refuses loopback even when the name looks harmless.
    with pytest.raises(FetchError) as ei:
        await _fetcher(server, policy=default).get(
            f"http://feeds.example:{server}/feed", max_bytes=1000
        )
    assert ei.value.kind == "blocked_address"
    # IP literals are checked too, and ports outside the allow-list never connect.
    with pytest.raises(FetchError, match="non-public"):
        await _fetcher(server, policy=default).get(
            f"http://127.0.0.1:{server}/feed", max_bytes=1000
        )
    with pytest.raises(FetchError, match="port"):
        await _fetcher(server).get("http://feeds.example:9/feed", max_bytes=1000)
    with pytest.raises(FetchError) as ei:
        await _fetcher(server).get("file:///etc/passwd", max_bytes=1000)
    assert ei.value.kind == "invalid_url"


async def test_dns_failure_is_reported(server: int) -> None:
    async def nxdomain(host: str, port: int) -> list[str]:
        raise OSError("nxdomain")

    async def empty(host: str, port: int) -> list[str]:
        return []

    for resolver in (nxdomain, empty):
        with pytest.raises(FetchError) as ei:
            await _fetcher(server, resolver=resolver).get(
                f"http://nope.example:{server}/feed", max_bytes=10
            )
        assert ei.value.kind == "dns"
