"""Keep the developer's interview answers in reasoning prompts, and stop
re-asking them.

Two failures made an Interview re-ask questions the developer had already
answered, reworded, round after round:

* the answered Q&A JSON was cut to a character budget from the tail, so the
  answers given in later rounds (the newest ones) were the first to go;
* the understanding-review prompt placed the Q&A section after the graph and
  the whole prompt is truncated from the tail, so a large graph dropped every
  answer.

`fit_newest_first` keeps whole items, newest first, and says how many older
ones were left out. `is_reasked` is a deterministic guard on the model's
output: an open question that overlaps an answered one is dropped instead of
being shown again.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Tuple

DEFAULT_ANSWERED_QA_PROMPT_MAX_CHARS = 12_000

# Character-bigram containment above which a generated question is treated as
# a rewording of an answered one. Measured on a real session (System 7,
# interview 15): rewordings scored 0.41-0.59, distinct questions of the same
# round at most 0.21.
REASK_THRESHOLD = 0.4

_PUNCT = re.compile(r"[\s`'\"『』「」（）()\[\]、。・?？!！:：/,\-→]+")
_FILLERS = (
    "でしょうか", "ですか", "ますか", "確認したいです", "でよいですか", "それとも",
    "として", "について", "この", "その", "理解",
)


def answered_qa_max_chars() -> int:
    raw = os.getenv("INTERVIEW_ANSWERED_QA_MAX_CHARS", str(DEFAULT_ANSWERED_QA_PROMPT_MAX_CHARS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("INTERVIEW_ANSWERED_QA_MAX_CHARS must be an integer") from exc
    if value < 1:
        raise ValueError("INTERVIEW_ANSWERED_QA_MAX_CHARS must be positive")
    return value


def fit_newest_first(items: List[Dict[str, Any]], max_chars: int) -> Tuple[List[Dict[str, Any]], int]:
    """Whole items that fit in `max_chars` of JSON, preferring the newest
    (last) ones, returned in their original order, plus how many were omitted."""
    kept: List[Dict[str, Any]] = []
    used = 2  # the enclosing []
    for item in reversed(items):
        size = len(json.dumps(item, ensure_ascii=False)) + 2
        if used + size > max_chars:
            break
        kept.append(item)
        used += size
    kept.reverse()
    return kept, len(items) - len(kept)


def answered_qa_prompt_json(items: List[Dict[str, Any]], max_chars: int) -> str:
    kept, omitted = fit_newest_first(items, max_chars)
    text = json.dumps(kept, ensure_ascii=False)
    if omitted:
        text += f"\n[{omitted} older answered question(s) omitted to fit the budget; newer answers supersede them]"
    return text


def _norm(text: str) -> str:
    t = _PUNCT.sub("", unicodedata.normalize("NFKC", text).lower())
    for filler in _FILLERS:
        t = t.replace(filler, "")
    return t


def _bigrams(text: str) -> set:
    t = _norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def overlap(a: str, b: str) -> float:
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def is_reasked(question: str, answered_questions: Iterable[str], threshold: float = REASK_THRESHOLD) -> bool:
    return any(overlap(question, q) >= threshold for q in answered_questions)
