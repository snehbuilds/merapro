"""What changed between two versions of one account's post (docs/04 §4.6 diff).

Word-level for prose, set-level for hashtags, order-aware for media (it is the carousel
order, and the content hash treats it that way). Pure: the approver's "what did the
rewrite change?" view and the bot's diff both read this.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from typing import Any

_TOKENS = re.compile(r"\s+|[^\s]+")


def text_ops(a: str, b: str) -> list[dict[str, str]]:
    """``equal``, ``delete`` and ``insert`` runs that turn ``a`` into ``b``, whitespace kept."""
    ta, tb = _TOKENS.findall(a), _TOKENS.findall(b)
    ops: list[dict[str, str]] = []

    def add(op: str, text: str) -> None:
        if not text:
            return
        if ops and ops[-1]["op"] == op:
            ops[-1]["text"] += text
        else:
            ops.append({"op": op, "text": text})

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=ta, b=tb, autojunk=False).get_opcodes():
        if tag == "equal":
            add("equal", "".join(ta[i1:i2]))
        else:
            add("delete", "".join(ta[i1:i2]))
            add("insert", "".join(tb[j1:j2]))
    return ops


def diff(a: Any, b: Any) -> dict[str, Any]:
    """``a`` and ``b`` are versions (anything with body, thread_parts, hashtags, link_url,
    media and content_hash)."""
    tags_a, tags_b = list(a.hashtags), list(b.hashtags)
    media_a = [str(m.get("checksum")) for m in a.media]
    media_b = [str(m.get("checksum")) for m in b.media]
    alt_a = {str(m.get("checksum")): m.get("alt_text") for m in a.media}
    alt_b = {str(m.get("checksum")): m.get("alt_text") for m in b.media}
    parts_a: Sequence[str] = a.thread_parts or []
    parts_b: Sequence[str] = b.thread_parts or []
    return {
        "same_content": a.content_hash == b.content_hash,
        "body": text_ops(a.body, b.body),
        "body_changed": a.body != b.body,
        "thread_parts": [
            text_ops(parts_a[i] if i < len(parts_a) else "", parts_b[i] if i < len(parts_b) else "")
            for i in range(max(len(parts_a), len(parts_b)))
        ],
        "hashtags_added": [t for t in tags_b if t not in tags_a],
        "hashtags_removed": [t for t in tags_a if t not in tags_b],
        "link_before": a.link_url,
        "link_after": b.link_url,
        "media_added": [c for c in media_b if c not in media_a],
        "media_removed": [c for c in media_a if c not in media_b],
        "media_reordered": [c for c in media_a if c in media_b]
        != [c for c in media_b if c in media_a],
        "alt_text_changed": sorted(c for c in alt_a.keys() & alt_b.keys() if alt_a[c] != alt_b[c]),
    }
