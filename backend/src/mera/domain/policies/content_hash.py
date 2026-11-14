"""The approval anchor (M5.5, 5.6; docs/03 §6 "The content hash", "The composite hash").

``compute_content_hash`` is what an approval binds to. It is deterministic (sorted keys,
sorted hashtags, fixed separators), covers media by **checksum** rather than id, binds the
**target account**, and leaves out everything non-semantic (timestamps, ids, confidence,
schedule), so rescheduling never invalidates an approval and retargeting always does.

``compute_composite_hash`` names a whole set's current content in one value for "Approve
all": any edit, and enabling or disabling any variant, changes it.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MediaRef:
    checksum: bytes
    alt_text: str | None


@dataclass(frozen=True, slots=True)
class VariantHash:
    social_account_id: uuid.UUID
    enabled: bool
    content_hash: bytes | None  # None until the variant has a version


def _norm(text: str | None) -> str | None:
    # NFC so that visually identical text from different input methods hashes alike.
    return unicodedata.normalize("NFC", text) if text is not None else None


def compute_content_hash(
    *,
    platform: str,
    social_account_id: uuid.UUID,
    body: str,
    thread_parts: Sequence[str] | None,
    hashtags: Iterable[str],
    link_url: str | None,
    media: Sequence[MediaRef],
) -> bytes:
    payload = {
        "platform": platform,
        "social_account_id": str(social_account_id),
        "body": _norm(body),
        "thread_parts": [_norm(p) for p in thread_parts] if thread_parts is not None else None,
        "hashtags": sorted(_norm(h) or "" for h in hashtags),
        "link_url": link_url,
        # Order matters for media (it is the carousel order), so it is not sorted.
        "media": [{"checksum": m.checksum.hex(), "alt": _norm(m.alt_text)} for m in media],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def compute_composite_hash(variants: Iterable[VariantHash]) -> bytes | None:
    """``None`` while any enabled variant has no version yet: there is nothing whole to
    approve."""
    pairs: list[tuple[str, str]] = []
    for v in variants:
        if not v.enabled:
            continue
        if v.content_hash is None:
            return None
        pairs.append((str(v.social_account_id), v.content_hash.hex()))
    canonical = json.dumps(sorted(pairs), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).digest()
