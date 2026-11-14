"""Is an approver's edit reply an instruction or replacement text? (docs/07 §3.3, M6.10)

Heuristics, deliberately simple and deterministic:

- short imperative phrases without post-like structure are **instructions** ("shorter",
  "lead with the read-replica question");
- text approaching the post's length, or carrying its own line breaks, links or hashtags,
  is a **replacement**;
- the middle ground is **ambiguous**, and the bot asks once, with two buttons.

The reply is untrusted text either way: an instruction is fenced as data in the Writer's
prompt; a replacement goes through the same guardrails as any person's edit.
"""

from __future__ import annotations

import re
from typing import Literal

Kind = Literal["instruction", "replacement", "ambiguous"]

_URL = re.compile(r"https?://\S+")
_TAG = re.compile(r"(?:^|\s)#\w+")
_IMPERATIVE = re.compile(
    r"^\s*(make|be|keep|cut|drop|remove|add|use|lead|start|end|shorten|lengthen|rewrite|"
    r"mention|avoid|say|focus|swap|replace|change|tone|less|more|shorter|longer|simpler|"
    r"punchier|friendlier|formal|casual|fix|try|don't|do not|no )\b",
    re.IGNORECASE,
)


def classify(reply: str, current_body: str) -> Kind:
    text = reply.strip()
    if not text:
        return "ambiguous"
    words = len(text.split())
    body_words = max(1, len(current_body.split()))
    structured = "\n" in text or bool(_URL.search(text)) or bool(_TAG.search(text))
    if words >= max(25, int(body_words * 0.6)) or (structured and words >= 12):
        return "replacement"
    if words <= 12 and not structured:
        return "instruction"
    if _IMPERATIVE.match(text) and not structured and words <= 30:
        return "instruction"
    return "ambiguous"
