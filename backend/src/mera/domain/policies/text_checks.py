"""Deterministic text guardrails and platform validation (M5.12; docs/06 §5, docs/08).

No model is involved, so these run identically on agent output and on text a person typed
(docs/06 §5: a guardrail that only inspects machine output teaches people to route around
it).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
# International-ish phone numbers: 10-15 digits with optional separators, not inside words.
_PHONE = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{2,4})?(?!\w)"
)
# Ten to thirteen digits in a row, not part of a longer number or word.
_PHONE_RUN = re.compile(r"(?<![\w+])\+?\(?\d{3}\)?\d{7,10}(?!\w)")
_US_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)[2-9]\d{3}\s\d{4}\s\d{4}(?!\d)")
_PAN = re.compile(r"(?<![A-Z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Z0-9])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_HASHTAG = re.compile(r"[^\w]", re.UNICODE)
X_URL_WEIGHT: Final = 23  # X counts every URL as 23 characters


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    excerpt: str


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


# Latin lookalikes from Cyrillic and Greek that NFKC leaves alone (review M2).
_CONFUSABLES: Final = str.maketrans(
    {
        "\u0430": "a",
        "\u0435": "e",
        "\u043e": "o",
        "\u0440": "p",
        "\u0441": "c",
        "\u0443": "y",
        "\u0445": "x",
        "\u0456": "i",
        "\u0458": "j",
        "\u0455": "s",
        "\u043a": "k",
        "\u043c": "m",
        "\u0442": "t",
        "\u0432": "b",
        "\u043d": "h",
        "\u03b1": "a",
        "\u03bf": "o",
        "\u03c1": "p",
        "\u03b5": "e",
        "\u03b9": "i",
        "\u03ba": "k",
        "\u03bd": "v",
        "\u03c4": "t",
    }
)


def skeleton(text: str) -> str:
    """The text as the checks see it: NFKC (fullwidth and compatibility forms), format
    characters removed (zero-width spaces, soft hyphens, joiners), casefolded, common
    Cyrillic and Greek lookalikes mapped to Latin."""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    return t.casefold().translate(_CONFUSABLES)


def find_pii(text: str) -> list[Finding]:
    """Emails (including inside link query strings), phone numbers, government ids (US SSN,
    Aadhaar, PAN) and card numbers. Phone and id checks skip URLs: a link's digits are not
    a phone number."""
    norm = unicodedata.normalize("NFKC", text)
    norm = "".join(ch for ch in norm if unicodedata.category(ch) != "Cf")
    scrubbed = _URL.sub(" ", norm)
    found: list[Finding] = [Finding("email", m.group(0).strip()) for m in _EMAIL.finditer(norm)]
    for kind, pattern in (("ssn", _US_SSN), ("aadhaar", _AADHAAR), ("pan", _PAN)):
        found += [Finding(kind, m.group(0).strip()) for m in pattern.finditer(scrubbed)]
    for pattern in (_PHONE, _PHONE_RUN):
        for m in pattern.finditer(scrubbed):
            if len(re.sub(r"\D", "", m.group(0))) >= 10:  # "2024-2025" is not a phone
                found.append(Finding("phone", m.group(0).strip()))
    for m in _CARD.finditer(scrubbed):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append(Finding("card", m.group(0).strip()))
    # A card or id number can also match the phone pattern; report each excerpt once.
    unique: dict[str, Finding] = {}
    for f in found:
        unique.setdefault(f.excerpt, f)
    return list(unique.values())


def find_banned(text: str, banned: Sequence[str]) -> list[Finding]:
    """Matches on the skeleton, as a phrase on word boundaries and also run together (so
    "#badword" and "bad-word" catch "bad word")."""
    sk = skeleton(text)
    out = []
    for word in banned:
        words = skeleton(word).split()
        if not words:
            continue
        spaced = r"(?<!\w)" + r"[\s\-_.]*".join(map(re.escape, words)) + r"(?!\w)"
        m = re.search(spaced, sk)
        if m:
            out.append(Finding("banned_word", m.group(0)))
    return out


_TAG_TOKEN = re.compile(r"(?<!\w)#\w+")


def prose(text: str) -> str:
    """The words of a post without its links and hashtags: what the duplicate guardrails
    compare. Every account must carry the same source link, so the link is not evidence
    that two posts say the same thing."""
    return " ".join(_TAG_TOKEN.sub(" ", _URL.sub(" ", text)).split())


def normalize_hashtag(tag: str) -> str | None:
    """'#Postgres 18!' -> 'postgres18'. None when nothing usable remains."""
    t = _HASHTAG.sub("", unicodedata.normalize("NFKC", tag.lstrip("#"))).lower()
    return t[:60] or None


def graphemes(text: str) -> int:
    """Approximate grapheme count: combining marks and joiners attach to the previous
    character. Good enough for limit checks; adapters re-verify at publish (M8)."""
    count = 0
    prev_join = False
    for ch in text:
        cat = unicodedata.category(ch)
        if ch == "‍":
            prev_join = True
            continue
        if cat.startswith("M") or ch in ("️", "︎") or prev_join or 0x1F3FB <= ord(ch) <= 0x1F3FF:
            prev_join = False
            continue
        count += 1
    return count


def weighted_length(text: str, *, platform: str, counts_graphemes: bool) -> int:
    if platform == "x":
        return len(_URL.sub("x" * X_URL_WEIGHT, text))
    return graphemes(text) if counts_graphemes else len(text)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str  # too_long, part_too_long, too_many_hashtags, threads_unsupported, empty
    severity: str  # warn or block
    message: str


def compose(body: str, hashtags: Sequence[str]) -> str:
    """The text as it will be posted: body, then the tags on their own line."""
    tags = " ".join(f"#{t}" for t in hashtags)
    return f"{body.rstrip()}\n\n{tags}" if tags else body.rstrip()


def validate(
    *,
    platform: str,
    body: str,
    thread_parts: Sequence[str] | None,
    hashtags: Sequence[str],
    max_chars: int,
    counts_graphemes: bool,
    supports_threads: bool,
    conventional_hashtags: tuple[int, int],
    max_hashtags: int | None = None,
    link_placement: str = "body",
    media_count: int = 0,
    media_without_alt: int = 0,
    requires_media: bool = False,
    max_media: int | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not body.strip() and not thread_parts:
        return [ValidationIssue("empty", "block", "The post is empty.")]
    if requires_media and media_count == 0:
        issues.append(
            ValidationIssue(
                "media_required", "block", "This platform needs at least one image to post."
            )
        )
    if max_media is not None and media_count > max_media:
        issues.append(
            ValidationIssue(
                "too_many_media",
                "block",
                f"{media_count} images; this platform accepts at most {max_media}.",
            )
        )
    if media_without_alt:
        issues.append(
            ValidationIssue(
                "missing_alt_text",
                "warn",
                f"{media_without_alt} image(s) without alt text; screen-reader users get nothing.",
            )
        )
    if thread_parts:
        if not supports_threads:
            issues.append(
                ValidationIssue("threads_unsupported", "block", "This platform has no threads.")
            )
        parts = [*thread_parts[:-1], compose(thread_parts[-1], hashtags)]
        for i, part in enumerate(parts, 1):
            n = weighted_length(part, platform=platform, counts_graphemes=counts_graphemes)
            if n > max_chars:
                issues.append(
                    ValidationIssue(
                        "part_too_long", "block", f"Part {i} is {n} / {max_chars} characters."
                    )
                )
    else:
        n = weighted_length(
            compose(body, hashtags), platform=platform, counts_graphemes=counts_graphemes
        )
        if n > max_chars:
            issues.append(ValidationIssue("too_long", "block", f"{n} / {max_chars} characters."))
    if max_hashtags is not None and len(hashtags) > max_hashtags:
        issues.append(
            ValidationIssue(
                "hashtag_limit",
                "block",
                f"{len(hashtags)} hashtags; this platform accepts at most {max_hashtags}.",
            )
        )
    elif len(hashtags) > conventional_hashtags[1]:
        issues.append(
            ValidationIssue(
                "too_many_hashtags",
                "warn",
                f"{len(hashtags)} hashtags; {conventional_hashtags[1]} is the convention here, "
                "and more looks automated.",
            )
        )
    if link_placement != "body" and any(_URL.search(t) for t in [body, *(thread_parts or [])]):
        where = {
            "first_comment": "Mera posts the link as the first comment",
            "reply": "Mera posts the link as a reply",
            "none": "links are not clickable here; name the source instead",
        }.get(link_placement, "")
        issues.append(
            ValidationIssue(
                "link_in_body",
                "warn",
                f"A link in the text reduces reach on this platform; {where}.",
            )
        )
    return issues
