"""Pure-ish sample components: summarize / classify / normalize_json."""

import json
import re
from typing import Any, Dict


def summarize(text: str, max_chars: int = 60) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def summarize_v2(text: str, max_chars: int = 60) -> str:
    """Candidate: take the first sentence, fall back to truncation."""
    text = text.strip()
    parts = re.split(r"(?<=[。．.!?])\s*", text, maxsplit=1)
    head = parts[0] if parts else text
    if len(head) <= max_chars:
        return head
    return head[:max_chars].rstrip() + "..."


_KEYWORDS = {
    "bug": ["error", "fail", "crash", "exception"],
    "feature": ["add", "support", "introduce", "new"],
    "doc": ["readme", "doc", "comment"],
}


def classify(text: str) -> str:
    t = text.lower()
    for label, keywords in _KEYWORDS.items():
        if any(k in t for k in keywords):
            return label
    return "other"


def classify_v2(text: str) -> str:
    """Candidate: scoring-based classifier."""
    t = text.lower()
    scores: Dict[str, int] = {}
    for label, keywords in _KEYWORDS.items():
        scores[label] = sum(t.count(k) for k in keywords)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "other"


def normalize_json(payload: str) -> str:
    obj: Any = json.loads(payload)
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
