"""Platform writing skills (Sprint 7): the vendored playbooks the agents follow per platform.

The skills are third-party markdown, vendored and pinned by ``scripts/import_platform_skills.py``
(commit, licence, sha256 per file in ``platforms/manifest.json``). This module only reads
that copy, verifies each file's checksum before use, and renders the text an agent sees:

- frontmatter, fenced code blocks and lines about the upstream tooling (publishing,
  scheduling, image generation, scraping, env files) are removed — Mera publishes only
  through its own approval gate, and an agent here has no such tools;
- pointers to other files and skills ("Resources", "Related skills") are dropped;
- the result is capped at a heading boundary.

What remains is writing guidance: hooks, structure, length, tone, anti-patterns.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parent / "platforms"
MAX_CHARS: Final = 12_000

# Mera platform -> skill bundle.
BUNDLE_FOR: Final[dict[str, str]] = {
    "linkedin_member": "linkedin",
    "linkedin_organization": "linkedin",
    "facebook": "facebook",
    "instagram": "instagram",
    "threads": "threads",
    "x": "x",
}

_TOOLING = re.compile(
    r"publora|pixfaro|apify|lib\.[a-z_]+\(|\.env\b|api[_ ]?key|_token\b|scripts?/|"
    r"python3? |npx |pip install|approval card|schedul(e|ing) (it|via|through)",
    re.IGNORECASE,
)
_DROP_SECTIONS: Final = ("resources", "related skills", "setup", "installation", "configuration")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCE = re.compile(r"^```.*?^```[^\n]*\n?", re.DOTALL | re.MULTILINE)


class SkillTampered(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SkillFile:
    role: str
    source_path: str
    file: str
    sha256: str
    chars: int


@dataclass(frozen=True, slots=True)
class Bundle:
    name: str
    repo: str
    commit: str
    licence: str
    roles: dict[str, str]
    files: tuple[SkillFile, ...]


@lru_cache(maxsize=1)
def manifest() -> dict[str, Bundle]:
    path = ROOT / "manifest.json"
    if not path.exists():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for b in data["bundles"]:
        out[b["platform"]] = Bundle(
            name=b["platform"],
            repo=b["repo"],
            commit=b["commit"],
            licence=b["licence"],
            roles=dict(b["roles"]),
            files=tuple(SkillFile(**f) for f in b["files"]),
        )
    return out


def bundle_for(platform: str) -> Bundle | None:
    name = BUNDLE_FOR.get(platform)
    return manifest().get(name) if name else None


def _read(bundle: Bundle, f: SkillFile) -> str:
    data = (ROOT / bundle.name / f.file).read_bytes()
    if hashlib.sha256(data).hexdigest() != f.sha256:
        # A vendored playbook changed outside the import script: refuse to feed it to a model.
        raise SkillTampered(f"{bundle.name}/{f.file} does not match its pinned checksum")
    return data.decode("utf-8")


def render(markdown: str, *, limit: int = MAX_CHARS) -> str:
    # Nothing a reviewer cannot see reaches a prompt: format characters (zero-width,
    # Unicode tags), HTML comments, an unterminated fence's tail, and our own fence tag.
    text = unicodedata.normalize("NFKC", markdown)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", text, flags=re.S)
    text = _FRONTMATTER.sub("", text)
    text = _FENCE.sub("", text)
    if "```" in text:
        text = text[: text.index("```")]
    text = re.sub(r"</?\s*vendored_platform_playbook[^>]*>", "", text, flags=re.I)
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            skipping = heading.group(2).strip().lower().startswith(_DROP_SECTIONS)
        if skipping or _TOOLING.search(line):
            continue
        kept.append(line.rstrip())
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    if len(out) <= limit:
        return out
    cut = out.rfind("\n#", 0, limit)
    return out[: cut if cut > limit // 2 else limit].rstrip() + "\n\n[…trimmed for length]"


@lru_cache(maxsize=64)
def playbook(platform: str, role: str, *, limit: int = MAX_CHARS) -> str | None:
    """The rendered skill for one platform and role (writer, repurposer, humanizer,
    hashtags, carousel, thread), or ``None`` when the platform has none."""
    bundle = bundle_for(platform)
    if bundle is None:
        return None
    f = next((x for x in bundle.files if x.role == role), None)
    return render(_read(bundle, f), limit=limit) if f else None


def raw_file(platform_bundle: str, role: str) -> str | None:
    """The vendored file as imported (for the review screen)."""
    bundle = manifest().get(platform_bundle)
    if bundle is None:
        return None
    f = next((x for x in bundle.files if x.role == role), None)
    return _read(bundle, f) if f else None
