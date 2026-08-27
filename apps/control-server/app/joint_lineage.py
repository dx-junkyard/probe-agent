"""Outcome lineage for Joint Understanding (Issue #338).

The existing `joint_understanding` metrics (#334) count start rate, close
outcomes, finding kinds, and reflux volume. Every one of them is a UTILIZATION
or SELF-REPORTED-STATE number: they say the feature was used and what label it
ended with. None of them can answer the question Epic #328 actually asks —
did understanding improve? — because an outcome label is a claim about the
conversation, not an observation of what happened afterwards:

- a session that closed ``understood`` and a session whose hypothesis was
  reversed two rounds later both count as a conclusion,
- a provisional adoption that has since needed re-confirmation still counts as
  a clean close,
- a question the system asked three times counts as three translations, not as
  one question the developer had to answer repeatedly.

This module derives only the finite events that persisted facts can establish
without interpretation. Everything here is derived from persisted facts rather
than guessed from adjacent outcomes. The same rows always produce the same
events, which makes the metrics reproducible; a transition that can mean two
things remains unmeasured until an explicit observation contract is captured.

The lineage deliberately does NOT score anything. It reports what happened, in
finite terms, and the metrics layer aggregates it into separate quality /
burden numbers — never one composite.

probe-agent:
  role: deterministic derivation of the finite unknown / hypothesis / question /
    decision lineage events from persisted Joint Understanding facts
  capability: interactive-system-understanding
  element_type: logic
  operation_kind: io
  state_effects: [database-read]
  probe_value: Verify the same persisted rows always derive the same finite events, that a hypothesis reversal is distinguished from a correction and from a maintained adoption, that an unknown's creation and its resolution belong to one lineage, and that nothing here is a composite score.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

# Finite lineage event kinds (Principle 6). Grouped by the subject whose life
# they describe, because "did understanding improve" is a question about a
# SUBJECT over time, not about one row.
#
# unknown_*   : an explicitly recorded gap in what the system could establish
# hypothesis_*: a candidate explanation and what became of it
# question_*  : a question the system decided to put to the developer
# decision_*  : the developer's own judgement and whether it held
# classification_corrected: reserved for an explicit future observation that
# the Question Router's route turned out to be wrong
LINEAGE_EVENT_KINDS = (
    "unknown_created",
    "unknown_resolved",
    "unknown_remained",
    "hypothesis_created",
    "hypothesis_confirmed",
    "hypothesis_reversed",
    "hypothesis_corrected",
    "hypothesis_superseded",
    "question_asked",
    "question_reasked",
    "question_withdrawn",
    "decision_proposed",
    "decision_adopted",
    "decision_rejected",
    "decision_undone",
    "classification_corrected",
)

# The subject axis, so an aggregate can pick "all hypotheses" without matching
# event-name prefixes as strings.
LINEAGE_SUBJECT_KINDS = ("unknown", "hypothesis", "question", "decision", "classification")

_EVENT_SUBJECT: Dict[str, str] = {
    "unknown_created": "unknown",
    "unknown_resolved": "unknown",
    "unknown_remained": "unknown",
    "hypothesis_created": "hypothesis",
    "hypothesis_confirmed": "hypothesis",
    "hypothesis_reversed": "hypothesis",
    "hypothesis_corrected": "hypothesis",
    "hypothesis_superseded": "hypothesis",
    "question_asked": "question",
    "question_reasked": "question",
    "question_withdrawn": "question",
    "decision_proposed": "decision",
    "decision_adopted": "decision",
    "decision_rejected": "decision",
    "decision_undone": "decision",
    "classification_corrected": "classification",
}

# Terminal verdicts per subject. A subject with no terminal event yet is still
# open, and an open subject is excluded from a rate's denominator -- counting it
# as a success or a failure would be reporting an outcome that has not happened.
_TERMINAL_EVENTS: Dict[str, Set[str]] = {
    "unknown": {"unknown_resolved", "unknown_remained"},
    "hypothesis": {
        "hypothesis_confirmed", "hypothesis_reversed",
        "hypothesis_corrected", "hypothesis_superseded",
    },
    "decision": {"decision_adopted", "decision_rejected", "decision_undone"},
}


def subject_kind_for(event_kind: str) -> str:
    """The subject an event describes (deterministic)."""
    if event_kind not in _EVENT_SUBJECT:
        raise ValueError(f"Unknown lineage event kind: {event_kind!r}")
    return _EVENT_SUBJECT[event_kind]


@dataclass(frozen=True)
class LineageEvent:
    """One derived event in a subject's life.

    ``subject_id`` identifies the thing whose lineage this is -- a finding id
    for an unknown/hypothesis, a session id for a question/decision/
    classification. ``supersedes_subject_id`` is the successor link that makes
    an unknown's creation and its resolution one lineage rather than two
    unrelated counts.
    """

    event_kind: str
    subject_kind: str
    subject_id: int
    system_id: int
    session_id: int
    joint_understanding_id: int
    at: float
    supersedes_subject_id: Optional[int] = None
    detail: str = ""


@dataclass
class SessionBurden:
    """Per-session effort, counted from persisted facts only."""

    joint_understanding_id: int
    system_id: int
    session_id: int
    rounds: int = 0
    developer_actions: int = 0
    developer_findings: int = 0
    questions_asked: int = 0
    reasks: int = 0


@dataclass
class LineageSummary:
    events: List[LineageEvent] = field(default_factory=list)
    burdens: List[SessionBurden] = field(default_factory=list)

    def count(self, *event_kinds: str) -> int:
        wanted = set(event_kinds)
        return sum(1 for e in self.events if e.event_kind in wanted)

    def terminal_subjects(self, subject_kind: str) -> int:
        """Subjects of this kind that have reached a terminal verdict.

        The denominator for every rate here. An open subject is excluded
        because counting it either way would report an outcome that has not
        happened yet -- which is exactly the "outcome label as a proxy for
        quality" move Issue #338 exists to remove.
        """
        terminal = _TERMINAL_EVENTS.get(subject_kind, set())
        return len({
            e.subject_id for e in self.events if e.event_kind in terminal
        })


def derive_lineage(conn, *, system_id: int, session_id: Optional[int] = None) -> LineageSummary:
    """Derive the finite lineage events for a System (or one interview session).

    One pass over the persisted Joint Understanding rows. Deterministic and
    order-stable: rows are read in id order and every classification below is a
    first-match rule over a finite set, so the same database always yields the
    same event list.
    """
    summary = LineageSummary()

    session_clause = "AND s.session_id = ?" if session_id is not None else ""
    params: List[object] = [system_id]
    if session_id is not None:
        params.append(session_id)
    sessions = conn.execute(
        f"""SELECT s.* FROM joint_understanding_session AS s
            WHERE s.system_id = ? {session_clause}
            ORDER BY s.id""",
        tuple(params),
    ).fetchall()
    if not sessions:
        return summary

    ju_ids = [row["id"] for row in sessions]
    placeholders = ",".join("?" for _ in ju_ids)
    findings = conn.execute(
        f"""SELECT * FROM joint_understanding_finding
            WHERE joint_understanding_id IN ({placeholders}) ORDER BY id""",
        tuple(ju_ids),
    ).fetchall()
    actions = conn.execute(
        f"""SELECT * FROM joint_understanding_action
            WHERE joint_understanding_id IN ({placeholders}) ORDER BY id""",
        tuple(ju_ids),
    ).fetchall()
    rounds = conn.execute(
        f"""SELECT * FROM joint_understanding_investigation_round
            WHERE joint_understanding_id IN ({placeholders}) ORDER BY id""",
        tuple(ju_ids),
    ).fetchall()
    translations = conn.execute(
        f"""SELECT * FROM joint_understanding_translation
            WHERE joint_understanding_id IN ({placeholders}) ORDER BY id""",
        tuple(ju_ids),
    ).fetchall()
    findings_by_ju: Dict[int, List] = {}
    for row in findings:
        findings_by_ju.setdefault(row["joint_understanding_id"], []).append(row)

    for session in sessions:
        ju_id = session["id"]
        own_findings = findings_by_ju.get(ju_id, [])
        # finding id -> the finding that superseded it, for the successor link.
        successor: Dict[int, object] = {
            r["supersedes_finding_id"]: r
            for r in own_findings
            if r["supersedes_finding_id"] is not None
        }
        closed = session["status"] == "closed"

        def _event(kind: str, subject_id: int, *, at: float, **kwargs) -> None:
            summary.events.append(LineageEvent(
                event_kind=kind,
                subject_kind=subject_kind_for(kind),
                subject_id=subject_id,
                system_id=system_id,
                session_id=session["session_id"],
                joint_understanding_id=ju_id,
                at=at,
                **kwargs,
            ))

        for finding in own_findings:
            if finding["origin_role"] != "investigation":
                continue
            finding_id = finding["id"]
            replacement = successor.get(finding_id)

            if finding["claim_kind"] == "unknown":
                _event("unknown_created", finding_id, at=finding["created_at"])
                if replacement is not None and replacement["claim_kind"] != "unknown":
                    # A later finding replaced the gap with something: the
                    # unknown and its resolution are ONE lineage, which is what
                    # lets "how many gaps got closed" be counted at all.
                    _event(
                        "unknown_resolved", finding_id,
                        at=replacement["created_at"],
                        supersedes_subject_id=replacement["id"],
                        detail=replacement["claim_kind"],
                    )
                elif closed:
                    # The conversation ended with the gap still open. A real
                    # result, and a different one from resolving it.
                    _event(
                        "unknown_remained", finding_id,
                        at=session["closed_at"] or session["updated_at"],
                    )
                continue

            if finding["claim_kind"] != "hypothesis":
                continue
            _event("hypothesis_created", finding_id, at=finding["created_at"])
            if replacement is not None:
                # Replacing a hypothesis with another hypothesis is an explicit
                # correction. A fact successor only says the hypothesis was
                # superseded: without an explicit relation it may confirm OR
                # refute the hypothesis, so reporting a reversal would invent
                # an observation that is not present in the rows.
                kind = (
                    "hypothesis_corrected"
                    if replacement["claim_kind"] == "hypothesis"
                    else "hypothesis_superseded"
                )
                _event(
                    kind, finding_id, at=replacement["created_at"],
                    supersedes_subject_id=replacement["id"],
                    detail=replacement["claim_kind"],
                )
            # A provisional human adoption is not evidence confirmation, and a
            # session close does not by itself settle a hypothesis. Both remain
            # non-terminal until an explicit observation contract is recorded.

        # Questions: a translation that concluded the developer must be asked.
        asked = 0
        for translation in translations:
            if translation["joint_understanding_id"] != ju_id:
                continue
            if translation["ask_developer"]:
                asked += 1
                _event(
                    "question_reasked" if asked > 1 else "question_asked",
                    ju_id, at=translation["created_at"], detail=str(translation["id"]),
                )
            elif asked:
                # A later pass concluded the question no longer needs to go to
                # the developer -- the system answered it after all.
                _event(
                    "question_withdrawn", ju_id, at=translation["created_at"],
                    detail=str(translation["id"]),
                )

        proposed = [a for a in actions if a["joint_understanding_id"] == ju_id
                    and a["action_kind"] == "decide"]
        if proposed:
            _event("decision_proposed", ju_id, at=proposed[0]["created_at"])
        if closed and session["outcome"] == "decided":
            _event(
                "decision_adopted", ju_id,
                at=session["closed_at"] or session["updated_at"],
            )

        summary.burdens.append(SessionBurden(
            joint_understanding_id=ju_id,
            system_id=system_id,
            session_id=session["session_id"],
            rounds=sum(1 for r in rounds if r["joint_understanding_id"] == ju_id),
            developer_actions=sum(
                1 for a in actions if a["joint_understanding_id"] == ju_id
            ),
            developer_findings=sum(
                1 for f in own_findings if f["origin_role"] == "developer"
            ),
            questions_asked=asked,
            reasks=max(0, asked - 1),
        ))

    summary.events.sort(key=lambda e: (e.at, e.joint_understanding_id, e.event_kind))
    return summary


# --- #311's start condition (observation only) ---------------------------------

# The two observation classes CLAUDE.md names as #311's start condition:
# "a real observation cohort and undo/misclassification cases". Reported as
# COUNTS with an explicitly unset threshold -- the same discipline #341 applies
# to its metric attention policy, and for the same reason: the number that
# should gate #311 has to be decided from real data, and inventing one here
# would be the self-reported readiness score Issue #338 forbids.
BULK_APPROVAL_CRITERIA = (
    "observed_sessions",
    "misclassification_cases",
    "undo_cases",
)

# Finite verdicts. `threshold_unset` is not a failure and not a pass: the
# criterion is measurable and measured, and what counts as enough is a decision
# nobody has made yet.
BULK_APPROVAL_VERDICTS = ("unmeasured", "threshold_unset")


@dataclass(frozen=True)
class BulkApprovalCriterion:
    key: str
    observed: int
    verdict: str
    note: str


def bulk_approval_readiness(summary: LineageSummary) -> List[BulkApprovalCriterion]:
    """The observed counts behind #311's start condition. Never a go/no-go."""
    observed_sessions = len(summary.burdens)
    counts = {
        "observed_sessions": observed_sessions,
        "misclassification_cases": summary.count("classification_corrected"),
        "undo_cases": summary.count("decision_undone"),
    }
    result: List[BulkApprovalCriterion] = []
    for key in BULK_APPROVAL_CRITERIA:
        if key != "observed_sessions":
            result.append(BulkApprovalCriterion(
                key=key,
                observed=0,
                verdict="unmeasured",
                note="explicit observation contract is not yet defined",
            ))
            continue
        result.append(BulkApprovalCriterion(
            key=key,
            observed=counts[key],
            verdict="unmeasured" if observed_sessions == 0 else "threshold_unset",
            note=(
                "no Joint Understanding session has been observed yet"
                if observed_sessions == 0
                else "measured; the threshold that would gate #311 is deliberately "
                     "unset until there is enough real data to choose one"
            ),
        ))
    return result


__all__ = [
    "BULK_APPROVAL_CRITERIA",
    "BULK_APPROVAL_VERDICTS",
    "BulkApprovalCriterion",
    "LINEAGE_EVENT_KINDS",
    "LINEAGE_SUBJECT_KINDS",
    "LineageEvent",
    "LineageSummary",
    "SessionBurden",
    "bulk_approval_readiness",
    "derive_lineage",
    "subject_kind_for",
]
