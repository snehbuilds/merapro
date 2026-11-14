"""Sandbox responders for the drafting agents (Writer, Hashtag, Editor).

Deterministic, offline, and shaped like the real contracts. The fake Writer follows the
prompt's rules in a crude way: it builds the post from the source's own sentences (so it
never invents facts), always includes the link, and varies its opening and sentence choice
with the number of siblings it was shown (so two accounts on one platform get different
text, as the cross-account guardrail requires). Differentiate mode shifts the choice
further; revise mode applies the instruction literally ("shorter", "more professional",
"add a hook").
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from mera.domain.ports.llm import Message

_SENT = re.compile(r"(?<=[.!?])\s+")
_FIELD = re.compile(r"^(Title|Publication|Link|Topics|Summary|Key passages): ?(.*)$", re.M)
PERSONAL_OPENERS = (
    "Worth a read:",
    "Here's what caught my eye.",
    "One thing I'm watching:",
    "Quick take:",
    "My read on this:",
    "Bookmarking this one.",
)
ORG_OPENERS = (
    "News from {pub}:",
    "In brief:",
    "For our community:",
    "Update worth knowing:",
    "From the {pub} newsroom:",
    "Today's read:",
)


def _find(pattern: str, text: str, default: str) -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else default


def _block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def _sentences(*texts: str) -> list[str]:
    out: list[str] = []
    for t in texts:
        for s in _SENT.split(" ".join(t.split())):
            s = s.strip()
            if len(s) > 25 and s not in out:
                out.append(s)
    return out


def _fit(opener: str, picked: list[str], tail: str, limit: int) -> str:
    while picked:
        body = " ".join([opener, *picked, tail]).strip()
        if len(body) <= limit:
            return body
        if len(picked) > 1:
            picked = picked[:-1]
            continue
        room = limit - len(opener) - len(tail) - 3
        if room < 20:
            break
        picked = [picked[0][: room - 1].rstrip() + "…"]
        return " ".join([opener, *picked, tail]).strip()
    return f"{opener} {tail}".strip()[:limit]


def writer_responder(messages: Sequence[Message]) -> dict[str, Any]:
    system = next((m.content for m in messages if m.role == "system"), "")
    user = "\n".join(m.content for m in messages if m.role == "user")
    fields = {k: v.strip() for k, v in _FIELD.findall(_block(user, "untrusted_source_material"))}
    pub = fields.get("Publication") or "the source"
    link = fields.get("Link") if fields.get("Link", "none") != "none" else None
    account_type = _find(r"type (\w+)", system, "personal")
    limit = int(_find(r"max length: (\d+)", system, "500"))
    siblings = system.count("<untrusted_prior_draft>")
    differentiate = "DIFFERENTIATE" in system
    nickname = _find(r"account: (.*?) \(", system, "")
    # A stable per-account offset, so accounts on different platforms differ too; the
    # sibling count still guarantees distinct sentences within one platform.
    k = siblings + (2 if differentiate else 0) + hashlib.sha1(nickname.encode()).digest()[0] % 5  # noqa: S324
    prior = _block(user, "untrusted_prior_draft")
    instruction = _block(user, "untrusted_edit_request").lower()
    in_body = "link placement: body" in system
    tail = f"Via {pub}: {link}" if link and in_body else f"(via {pub})"

    openers = PERSONAL_OPENERS if account_type == "personal" else ORG_OPENERS
    opener = openers[k % len(openers)].format(pub=pub)
    sentences = _sentences(fields.get("Summary", ""), fields.get("Key passages", ""))
    if not sentences:
        brief = _block(user, "untrusted_source_material")
        sentences = _sentences(brief) or [fields.get("Title") or "Something worth sharing."]
    hook = "announcement"
    if prior and "REVISION" in system:
        prior_sentences = _sentences(prior) or [prior]
        if "short" in instruction:
            body = _fit(opener, prior_sentences[1:2] or prior_sentences[:1], tail, min(limit, 220))
        elif "professional" in instruction:
            body = re.sub(r"\bI'm\b", "We are", re.sub(r"\bI\b", "We", prior))
        elif "hook" in instruction:
            body = "Why does this matter? " + prior
            hook = "question"
        else:
            k += 1
            start = (2 * k) % max(len(sentences), 1)
            picked = (sentences[start:] + sentences[:start])[:2]
            body = _fit(openers[k % len(openers)].format(pub=pub), picked, tail, limit)
    else:
        start = (2 * k) % max(len(sentences), 1)
        picked = (sentences[start:] + sentences[:start])[: 1 if limit <= 300 else 2]
        body = _fit(opener, picked, tail, limit)
    return {
        "post": {
            "body": body[:limit] if len(body) > limit else body,
            "thread_parts": None,
            "link_url": link,
            "hook_type": hook,
            "angle": (
                f"Opens with '{opener}' and leads with source sentence {start_of(k, sentences)}."
            ),
            "self_critique": "Sandbox draft: assembled from the source's own sentences.",
        },
        "source_attribution": pub,
    }


def start_of(k: int, sentences: list[str]) -> int:
    return ((2 * k) % max(len(sentences), 1)) + 1


def hashtag_responder(messages: Sequence[Message]) -> dict[str, Any]:
    system = next((m.content for m in messages if m.role == "system"), "")
    hi = int(_find(r"PLATFORM CONVENTION: \d+ to (\d+)", system, "2"))
    user = "\n".join(m.content for m in messages if m.role == "user")
    topics_line = _block(user, "untrusted_topics")
    banned_line = _find(r"NEVER USE: (.*)", system, "").lower()
    banned = {b.strip().lstrip("#") for b in banned_line.split(",")}
    topics = [t.strip() for t in topics_line.split(",") if t.strip() and t.strip() != "(none)"]
    tags: list[dict[str, Any]] = []
    for t in topics:
        tag = re.sub(r"[^\w]", "", t.lower())
        if not tag or tag in banned or any(x["tag"] == tag for x in tags):
            continue
        tags.append(
            {
                "tag": tag,
                "rank": len(tags) + 1,
                "basis": "content_only",
                "sample_size": 0,
                "lift_vs_account_median": None,
                "rationale": "Describes the post's subject (sandbox; no performance history yet).",
            }
        )
        if len(tags) >= hi:
            break
    return {"tags": tags, "omitted_reason": None if tags else "No candidate topic fits the post."}


def editor_responder(messages: Sequence[Message]) -> dict[str, Any]:
    user = "\n".join(m.content for m in messages if m.role == "user")
    draft = _block(user, "untrusted_draft")
    issues = []
    if "http" not in draft and "Topic brief" not in user:
        issues.append(
            {
                "category": "attribution",
                "severity": "block",
                "excerpt": draft[:80],
                "explanation": "The source is not linked.",
                "suggested_fix": "Add the source link.",
            }
        )
    digest = hashlib.sha1(draft.encode()).digest()[0]  # noqa: S324 - not security
    return {
        "issues": issues,
        "confidence_score": round(0.70 + (digest % 22) / 100, 2),
        "confidence_rationale": "Sandbox estimate: grounded in the source's own sentences.",
        "factual_claims": [],
        "voice_match": 0.78,
    }


def alt_text_responder(messages: Sequence[Message]) -> dict[str, Any]:
    """Sandbox alt text: says what it knows (an image is attached, its size) and that a
    person should describe it. Never pretends to have seen the image."""
    images = [u for m in messages for u in m.images]
    if not images:
        return {"alt_text": "", "contains_text": False, "confidence": 0.0}
    size_kb = max(1, len(images[0]) * 3 // 4 // 1024)
    return {
        "alt_text": f"Sandbox description of the attached image (about {size_kb} KB). "
        "Replace with what the image shows.",
        "contains_text": False,
        "confidence": 0.1,
    }


def media_responder(messages: Sequence[Message]) -> dict[str, Any]:
    """Sandbox image plan: one prompt per requested image, from the post's own sentences."""
    system = messages[0].content if messages else ""
    user = messages[-1].content if messages else ""
    count = int(_find(r"Write (\d+) image generation prompt", system, "1"))
    post = re.sub(r"</?untrusted_post>", "", user.split("</untrusted_post>")[0])
    picked = _sentences(post) or [" ".join(post.split()[:20]) or "an abstract illustration"]
    prompts, alts = [], []
    for i in range(count):
        point = picked[i % len(picked)][:200]
        prompts.append(f"Clean editorial illustration, soft light, flat colours: {point}")
        alts.append(f"Illustration for the post: {point[:160]}")
    return {"prompts": prompts, "alt_texts": alts, "safe": True, "reason": ""}


def engagement_responder(messages: Sequence[Message]) -> dict[str, Any]:
    """Sandbox triage by keywords. Instructions inside the comment are classified as
    content (``other``), never followed."""
    user = messages[-1].content if messages else ""
    m = re.search(r"<untrusted_comment[^>]*>(.*?)</untrusted_comment>", user, re.S)
    text = (m.group(1) if m else "").strip()
    low = text.lower()
    base = {"toxicity_score": 0.02, "spam_score": 0.02, "language": "en", "reply_confidence": 0.0}
    if any(k in low for k in ("$$$", "click my profile", "earn money", "crypto giveaway")):
        return {**base, "sentiment": "neutral", "intent": "spam", "spam_score": 0.97}
    if any(k in low for k in ("idiot", "stupid", "hate you")):
        return {**base, "sentiment": "negative", "intent": "toxic", "toxicity_score": 0.9}
    if "ignore your" in low or "ignore previous" in low or "instructions" in low:
        return {
            **base,
            "sentiment": "neutral",
            "intent": "other",
            "escalation_reason": "Looks like an attempt to instruct the assistant.",
        }
    if any(k in low for k in ("broken", "refund", "doesn't work", "terrible", "disappointed")):
        return {
            **base,
            "sentiment": "negative",
            "intent": "complaint",
            "escalation_reason": "A complaint: a person should answer.",
        }
    if "?" in text:
        return {
            **base,
            "sentiment": "neutral",
            "intent": "question",
            "suggested_reply": "It took us about two weeks, most of it testing the cut-over.",
            "reply_confidence": 0.72,
        }
    if any(k in low for k in ("thanks", "great", "exactly", "love", "helpful")):
        return {
            **base,
            "sentiment": "positive",
            "intent": "praise",
            "suggested_reply": "Glad it was useful, hope the move goes smoothly for you.",
            "reply_confidence": 0.85,
        }
    return {**base, "sentiment": "neutral", "intent": "other"}


def analytics_responder(messages: Sequence[Message]) -> dict[str, Any]:
    """Sandbox weekly summary: the finding titles, each cited; one recommendation per
    strong finding. Uses nothing that is not in the findings."""
    lines = [ln for ln in _block(messages[-1].content, "untrusted_findings").splitlines() if ln]
    found = [re.match(r"^\[([^\]]+)\] (.+?)\. ", ln) for ln in lines]
    items = [(m.group(1), m.group(2)) for m in found if m]
    if not items:
        return {"summary": "Not enough data yet for findings this week.", "cites": []}
    return {
        "summary": "This week: " + "; ".join(t for _, t in items) + ".",
        "cites": [i for i, _ in items],
        "recommendations": [
            {"text": f"Lean into it: {t}.", "cites": [i]}
            for i, t in items
            if "does well" in t or "is ahead of" in t
        ][:3],
    }


RESPONDERS = {
    "AnalyticsSummary": analytics_responder,
    "EngagementTriage": engagement_responder,
    "MediaPlan": media_responder,
    "AltTextSuggestion": alt_text_responder,
    "WriterOutput": writer_responder,
    "HashtagOutput": hashtag_responder,
    "EditorOutput": editor_responder,
}
