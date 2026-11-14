"""The MCP server catalog (P3-M6.1, docs/05 §2, docs/14).

A short curated list Mera knows how to set up (stdio entries are pinned versions, as
the registry requires; re-check the pins when releasing), plus an optional search of the public MCP
Registry. Installing from either is the same as the form: the server lands in
``pending_review`` with every tool disabled, and an Admin approves it. Nothing in a
catalog entry is trusted beyond that: descriptions are shown, never followed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Final

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str  # stable key, also the default slug
    title: str
    description: str
    transport: str  # http | stdio
    url: str | None = None
    package: str | None = None
    auth_method: str = "none"
    egress_allowlist: tuple[str, ...] = ()
    homepage: str | None = None
    source: str = "curated"  # curated | registry
    tags: tuple[str, ...] = field(default=())

    def view(self) -> dict[str, Any]:
        d = asdict(self)
        d["egress_allowlist"] = list(self.egress_allowlist)
        d["tags"] = list(self.tags)
        return d


CURATED: Final[tuple[CatalogEntry, ...]] = (
    CatalogEntry(
        name="fetch",
        title="Fetch",
        description="Reads a web page and returns it as Markdown. Useful to the Research agent.",
        transport="stdio",
        package="pypi:mcp-server-fetch==2025.4.7",
        tags=("research", "web"),
    ),
    CatalogEntry(
        name="github",
        title="GitHub",
        description="Issues, pull requests and releases from repositories you choose.",
        transport="http",
        url="https://api.githubcopilot.com/mcp/",
        auth_method="oauth",
        egress_allowlist=("api.githubcopilot.com",),
        homepage="https://github.com/github/github-mcp-server",
        tags=("source", "engineering"),
    ),
    CatalogEntry(
        name="notion",
        title="Notion",
        description="Pages and databases from a Notion workspace, as research or a source.",
        transport="http",
        url="https://mcp.notion.com/mcp",
        auth_method="oauth",
        egress_allowlist=("mcp.notion.com",),
        tags=("source", "docs"),
    ),
    CatalogEntry(
        name="linear",
        title="Linear",
        description="Issues and project updates, for release notes and changelog posts.",
        transport="http",
        url="https://mcp.linear.app/mcp",
        auth_method="oauth",
        egress_allowlist=("mcp.linear.app",),
        tags=("source", "engineering"),
    ),
    CatalogEntry(
        name="brave-search",
        title="Brave Search",
        description="Web and news search for the Research agent. Needs an API key.",
        transport="stdio",
        package="npm:@modelcontextprotocol/server-brave-search@0.6.2",
        auth_method="env",
        egress_allowlist=("api.search.brave.com",),
        tags=("research", "search"),
    ),
    CatalogEntry(
        name="time",
        title="Time",
        description="Time and time-zone conversion. Harmless; a good first server to try.",
        transport="stdio",
        package="pypi:mcp-server-time==2025.8.4",
        tags=("utility",),
    ),
)


def curated(q: str | None) -> list[CatalogEntry]:
    if not q:
        return list(CURATED)
    needle = q.strip().lower()
    return [
        e
        for e in CURATED
        if needle in e.name
        or needle in e.title.lower()
        or needle in e.description.lower()
        or any(needle in t for t in e.tags)
    ]


def find(name: str) -> CatalogEntry | None:
    return next((e for e in CURATED if e.name == name), None)


def from_registry(payload: bytes) -> list[CatalogEntry]:
    """Entries from an MCP Registry ``/v0/servers`` response. Only remotes over
    streamable HTTP with an https URL are offered; anything odd is skipped."""
    try:
        doc = json.loads(payload)
    except ValueError:
        return []
    out: list[CatalogEntry] = []
    for item in (doc.get("servers") if isinstance(doc, dict) else None) or []:
        srv = item.get("server", item) if isinstance(item, dict) else None
        if not isinstance(srv, dict):
            continue
        name = srv.get("name")
        remotes = srv.get("remotes") or []
        url = next(
            (
                r.get("url")
                for r in remotes
                if isinstance(r, dict)
                and r.get("type") in ("streamable-http", "http")
                and isinstance(r.get("url"), str)
                and r["url"].startswith("https://")
            ),
            None,
        )
        if not isinstance(name, str) or url is None:
            continue
        host = url.split("/")[2].split(":")[0].lower()
        out.append(
            CatalogEntry(
                name=name[:120],
                title=str(srv.get("title") or name)[:120],
                description=str(srv.get("description") or "")[:500],
                transport="http",
                url=url[:2048],
                egress_allowlist=(host,),
                source="registry",
            )
        )
    return out[:30]


def slug_for(name: str) -> str:
    """A registry name like ``io.github.owner/server`` as a workspace slug."""
    tail = name.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
    cleaned = "".join(c if c.isascii() and c.isalnum() else "_" for c in tail).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"s_{cleaned}"
    return cleaned[:31].ljust(2, "x")
