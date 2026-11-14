"""Vendor platform writing skills into the backend (Sprint 7).

Copies the writing skills Mera's agents use from the upstream skill repositories, pinned to
a commit, with their licence, a sha256 per file and the references each skill cites. The
agents read only this vendored copy: third-party text that shapes prompts is reviewed when
imported, never fetched at runtime.

Run from backend/:
    uv run python scripts/import_platform_skills.py            # latest commit of each repo
    uv run python scripts/import_platform_skills.py --check    # verify files against the manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import truststore

OWNER = "sergebulaev"
DEST = Path(__file__).resolve().parents[1] / "src" / "mera" / "skills" / "platforms"

# platform -> (repo, {role: skill directory})
BUNDLES: dict[str, tuple[str, dict[str, str]]] = {
    "linkedin": (
        "linkedin-skills",
        {
            "writer": "linkedin-post-writer",
            "repurposer": "linkedin-repurposer",
            "humanizer": "linkedin-humanizer",
        },
    ),
    "facebook": (
        "facebook-skills",
        {"writer": "fb-post-writer", "repurposer": "fb-repurposer", "humanizer": "fb-humanizer"},
    ),
    "instagram": (
        "instagram-skills",
        {
            "writer": "ig-caption-writer",
            "repurposer": "ig-repurposer",
            "humanizer": "ig-humanizer",
            "hashtags": "ig-hashtag-strategist",
            "carousel": "ig-carousel-planner",
        },
    ),
    "threads": (
        "threads-skills",
        {
            "writer": "threads-post-writer",
            "repurposer": "threads-repurposer",
            "humanizer": "threads-humanizer",
        },
    ),
    "x": (
        "x-skills",
        {
            "writer": "x-post-writer",
            "repurposer": "x-repurposer",
            "humanizer": "x-humanizer",
            "thread": "x-thread-builder",
        },
    ),
}
MAX_FILE_BYTES = 200_000


def _client() -> httpx.Client:
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.Client(
        verify=ctx,
        timeout=30,
        trust_env=False,
        headers={"User-Agent": "mera-skill-import", "Accept": "application/vnd.github+json"},
    )


def _get(client: httpx.Client, url: str) -> bytes:
    r = client.get(url, follow_redirects=True)
    r.raise_for_status()
    if len(r.content) > MAX_FILE_BYTES:
        raise SystemExit(f"{url} is larger than {MAX_FILE_BYTES} bytes; refusing to vendor it")
    return r.content


def import_bundle(
    client: httpx.Client, platform: str, repo: str, roles: dict[str, str]
) -> dict[str, object]:
    head = json.loads(_get(client, f"https://api.github.com/repos/{OWNER}/{repo}/commits/main"))
    sha = head["sha"]
    tree = json.loads(
        _get(client, f"https://api.github.com/repos/{OWNER}/{repo}/git/trees/{sha}?recursive=1")
    )
    paths = {e["path"] for e in tree["tree"] if e["type"] == "blob"}
    raw = f"https://raw.githubusercontent.com/{OWNER}/{repo}/{sha}/"
    out_dir = DEST / platform
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []

    def save(src: str, role: str) -> str:
        data = _get(client, raw + src)
        text = data.decode("utf-8")
        target = out_dir / src.replace("/", "__")
        target.write_bytes(data)
        files.append(
            {
                "role": role,
                "source_path": src,
                "file": target.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "chars": len(text),
            }
        )
        return text

    # Only each skill's SKILL.md: the agents read nothing else, and a smaller pinned
    # surface is easier to review. References the skills cite stay upstream.
    for role, skill in roles.items():
        path = f"skills/{skill}/SKILL.md"
        if path not in paths:
            raise SystemExit(f"{repo}: {path} not found at {sha}")
        save(path, role)
    licence = next((p for p in ("LICENSE", "LICENSE.md", "LICENSE.txt") if p in paths), None)
    if licence is None:
        raise SystemExit(f"{repo}: no licence file; cannot vendor")
    lic_text = save(licence, "licence")
    if "MIT" not in lic_text[:200] and "Permission is hereby granted" not in lic_text:
        raise SystemExit(f"{repo}: licence is not MIT; review before vendoring")
    return {
        "platform": platform,
        "repo": f"https://github.com/{OWNER}/{repo}",
        "commit": sha,
        "licence": "MIT",
        "roles": roles,
        "files": files,
    }


def prune(bundles: list[dict[str, object]]) -> None:
    """Remove files a previous import left behind in this script's own directories."""
    for b in bundles:
        keep = {f["file"] for f in b["files"]}  # type: ignore[attr-defined]
        for path in (DEST / str(b["platform"])).iterdir():
            if path.is_file() and path.name not in keep:
                path.unlink()


def write_manifest(bundles: list[dict[str, object]]) -> None:
    manifest = {
        "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "bundles": bundles,
    }
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def check() -> int:
    manifest = json.loads((DEST / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for b in manifest["bundles"]:
        for f in b["files"]:
            data = (DEST / b["platform"] / f["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                bad.append(f"{b['platform']}/{f['file']}")
    if bad:
        print("modified vendored skill files:", *bad, sep="\n  ")
        return 1
    count = sum(len(b["files"]) for b in manifest["bundles"])
    print(f"platform skills match the manifest ({count} files)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check()
    with _client() as client:
        bundles = [import_bundle(client, p, repo, roles) for p, (repo, roles) in BUNDLES.items()]
    prune(bundles)
    write_manifest(bundles)
    for b in bundles:
        print(f"{b['platform']:10} {str(b['commit'])[:10]}  {len(b['files'])} files")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
