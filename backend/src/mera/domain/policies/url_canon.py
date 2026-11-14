"""URL canonicalisation for exact dedupe (M4.4, docs/03 §5 "Deduplication, in two passes").

The same article arrives through several feeds with different tracking parameters, a
fragment, an upper-case host or an explicit default port. Each of those is stripped before
hashing, so ``UNIQUE (workspace_id, url_hash)`` catches it. The path is kept as given
(case and trailing slash can be meaningful to a server); query parameters are sorted.
"""

from __future__ import annotations

import hashlib
from typing import Final
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

_TRACKING_EXACT: Final[frozenset[str]] = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "yclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "ref_url",
        "_hsenc",
        "_hsmi",
        "mkt_tok",
        "spm",
        "cmpid",
        "ncid",
        "sr_share",
    }
)
_TRACKING_PREFIXES: Final[tuple[str, ...]] = ("utm_", "pk_", "hsa_", "oly_")
_DEFAULT_PORTS: Final[dict[str, int]] = {"http": 80, "https": 443}


class InvalidUrl(ValueError):
    pass


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_EXACT or k.startswith(_TRACKING_PREFIXES)


def canonicalize(url: str) -> str:
    """Return the canonical form of an absolute http(s) URL, or raise ``InvalidUrl``."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise InvalidUrl("only http and https URLs are accepted")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise InvalidUrl("URL has no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidUrl("URL has an invalid port") from exc
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = quote(unquote(parts.path or "/"), safe="/%:@!$&'()*+,;=-._~")
    query = urlencode(
        sorted(
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
        )
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(canonical_url: str) -> bytes:
    return hashlib.sha256(canonical_url.encode("utf-8")).digest()
