"""Fencing untrusted text inside prompts (docs/09 prompt-injection layer 1).

Retrieved articles, feed titles and human-typed drafts go inside an XML-ish block the
prompt declares to be data. Any spelling of that block's tag name inside the text (case,
separators, zero-width characters) is rewritten, so the text cannot close the fence early
and continue as instructions.
"""

from __future__ import annotations

import re
from functools import lru_cache

_GAP = r"[\W_​-‏⁠﻿]*"


@lru_cache(maxsize=32)
def _pattern(tag: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in re.split(r"[_\-\s]+", tag) if w]
    return re.compile(_GAP.join(words), re.IGNORECASE)


def neutralise(text: str, *tags: str) -> str:
    for tag in tags:
        text = _pattern(tag).sub("quoted-text", text)
    return text


def fenced(tag: str, text: str, *also: str) -> str:
    """``<tag>`` + text with every listed tag name defused + ``</tag>``."""
    return f"<{tag}>\n{neutralise(text, tag, *also)}\n</{tag}>"
