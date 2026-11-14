"""A source's include/exclude keywords and language (M4.3). No model involved.

Keywords match case-insensitively on word boundaries, so "go" does not match "google",
and a phrase ("open source") matches as a phrase. Exclusion wins over inclusion. An empty
include list admits everything not excluded.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class FilterResult:
    admitted: bool
    reason: str | None = None  # "excluded:<kw>", "no_include_match", "language:<code>"


@lru_cache(maxsize=1024)
def _pattern(keyword: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in keyword.strip().split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def normalize_keywords(raw: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for k in raw:
        k = " ".join(k.split()).lower()
        if k:
            seen.setdefault(k, None)
    return list(seen)


def apply(
    text: str,
    *,
    include: Sequence[str],
    exclude: Sequence[str],
    language: str | None = None,
    detected_language: str | None = None,
) -> FilterResult:
    if language and detected_language and language.lower() != detected_language.lower():
        return FilterResult(False, f"language:{detected_language}")
    for kw in exclude:
        if kw.strip() and _pattern(kw).search(text):
            return FilterResult(False, f"excluded:{kw}")
    real_includes = [k for k in include if k.strip()]
    if real_includes and not any(_pattern(k).search(text) for k in real_includes):
        return FilterResult(False, "no_include_match")
    return FilterResult(True)
