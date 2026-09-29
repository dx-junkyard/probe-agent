"""Coverage of the lists an assistant ask hands to the model (Issue #470).

Contract: docs/01-specifications/capabilities/assistant-answer-quality.md §3.2.

A list cut at a limit used to be indistinguishable, to the model, from a list
that was simply that long.  "It is not in the list" then read as "it does not
exist".  Every cut now carries a ``CoverageEntry`` -- kept OUT of ``facts`` on
purpose (a fact about the System and a fact about how much of it was shown are
different things).

``total`` is filled only when it is cheap and exact (a ``COUNT(*)`` in the same
connection, or the length of a list already fully in memory).  Otherwise it is
``None`` -- never a guess.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

COVERAGE_STATES = ("complete", "truncated", "empty", "unavailable", "unsupported")


@dataclass(frozen=True)
class CoverageEntry:
    section: str
    state: str  # one of COVERAGE_STATES
    returned: int
    total: Optional[int] = None
    limit: Optional[int] = None
    more_available_via: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Keep the payload compact: an absent reason is not a fact.
        if data.get("reason") is None:
            data.pop("reason")
        return data


def bounded_list(
    section: str,
    items: Sequence[Any],
    limit: int,
    *,
    total: Optional[int] = None,
    more_available_via: Optional[str] = None,
) -> Tuple[List[Any], CoverageEntry]:
    """Cut ``items`` to ``limit`` and say what happened.

    ``items`` may be the full list, or a ``limit + 1`` probe (a SQL ``LIMIT``
    fetched one row past the limit) -- either way ``len(items) > limit`` means
    truncated.  ``total`` (when the caller knows it exactly) also proves
    truncation even if ``items`` was already cut by the caller.
    """
    seen = list(items)
    kept = seen[:limit]
    returned = len(kept)
    truncated = len(seen) > limit or (total is not None and total > returned)
    if truncated:
        state = "truncated"
    elif returned == 0:
        state = "empty"
    else:
        state = "complete"
    entry = CoverageEntry(
        section=section,
        state=state,
        returned=returned,
        total=total,
        limit=limit,
        more_available_via=more_available_via if truncated else None,
    )
    return kept, entry


def unavailable_entry(section: str, reason: str = "") -> CoverageEntry:
    return CoverageEntry(
        section=section, state="unavailable", returned=0, total=None,
        reason=reason or None,
    )


def unsupported_entry(section: str, reason: str = "") -> CoverageEntry:
    return CoverageEntry(
        section=section, state="unsupported", returned=0, total=None,
        reason=reason or None,
    )
