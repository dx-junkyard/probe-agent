"""The shared verified-evidence feed (Issue #336).

Epic #328's reflux (#332) attached a system-verified fact to the understanding
surface without recording it as anyone's answer. For a ``qa`` origin that
landed in ``interview_qa.investigation_json``, which the Q&A card displays.
For every other origin it landed in ``joint_understanding_reflux`` and stopped
there: the Understanding rebuild, the Alignment build, and Inquiry answering
all read their own inputs and none of them read that ledger. The fact was
recorded, attributable, and invisible to every consumer that could have used
it -- which is the "isolated third conversation feature" Epic #328 set out to
remove.

This module is the one place a verified fact is published to, and the one
place a rebuild reads it from. Three properties make it usable as a rebuild
input rather than as a second opinion:

- **Its own provenance.** A feed entry is always
  ``decision_method='reasoning_llm'`` and carries the reasoning run and the
  premise it was established against. It is never ``manual``: nobody answered
  anything, and a rebuild must be able to tell an investigated fact apart from
  a developer's confirmed answer.
- **Idempotent publication.** The publication digest includes the source
  session and finding as well as the fact's semantic digest. Re-running reflux
  for the same finding is idempotent, while an independent investigation may
  re-establish the same fact against a newer premise without being hidden by
  an older, subsequently stale source.
- **Currency at read time, not at write time.** ``current_entries`` drops an
  entry whose finding a later finding corrected, and every entry whose source
  session's premise is no longer ``current`` (Issue #337's verdict, evaluated
  from the same rows). A stale fact stays readable as history and stops being
  offered as a current input -- publishing cannot know that in advance.

Consumption is recorded separately from human confirmation, because they are
different facts: ``understanding_evidence_consumption`` says a rebuild used an
entry, while ``interview_session.understanding_confirmed_at`` (and Issue
#312's Capability confirmation) says a human accepted the result. Collapsing
them would let "the AI fed this in" read as "the developer agreed with it".

probe-agent:
  role: publication, currency filtering, and consumption audit of verified
    investigation facts shared across the understanding rebuilds
  capability: interactive-system-understanding
  element_type: core
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify republishing the same finding does not duplicate a feed entry, that a superseded finding and a non-current premise are both excluded from current_entries while staying readable, and that a consumption record is never mistaken for a human confirmation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .joint_premise import ASSERTABLE_PREMISE_STATES, evaluate_session_premise

FEED_SCHEMA_VERSION = "understanding-evidence-feed-v1"

# Where a feed entry came from. Finite (Principle 6) and currently one value:
# Joint Understanding is the only producer of verified facts that are not
# already an input to a rebuild. A second producer must be added here
# deliberately, with its own currency rule.
FEED_SOURCE_KINDS = ("joint_understanding",)

# Which rebuild consumed an entry. Finite, and deliberately NOT including any
# human action: a consumption row says the AI fed a fact into a rebuild, never
# that a developer accepted the result.
FEED_CONSUMER_KINDS = ("understanding_build", "alignment_build", "inquiry_answer")

# How many current entries a consumer is offered. A rebuild prompt has a
# budget; the cap is deterministic (newest first) so the same rows produce the
# same prompt.
DEFAULT_FEED_LIMIT = 40


@dataclass
class FeedEntry:
    """One verified fact as a rebuild consumer sees it."""

    id: int
    system_id: int
    session_id: int
    source_kind: str
    source_id: int
    finding_id: int
    origin_kind: str
    origin_id: int
    statement: str
    evidence: List[Dict[str, object]] = field(default_factory=list)
    runtime_evidence: List[Dict[str, object]] = field(default_factory=list)
    intelligence_run_id: Optional[int] = None
    premise_snapshot_id: Optional[int] = None
    premise_commit_sha: Optional[str] = None
    created_at: float = 0.0

    def as_prompt_fact(self) -> Dict[str, object]:
        """The shape a rebuild prompt receives.

        ``decision_method`` is stated explicitly rather than left implicit:
        the prompt has to be able to tell this apart from a developer's
        confirmed answer, which is the whole reason the feed exists as its own
        input rather than being merged into the Q&A section.
        """
        return {
            "statement": self.statement,
            "origin": {"kind": self.origin_kind, "id": self.origin_id},
            "evidence": [
                {
                    "path": e.get("path"),
                    "start_line": e.get("start_line"),
                    "end_line": e.get("end_line"),
                    "summary": e.get("summary", ""),
                }
                for e in self.evidence
            ],
            "runtime_evidence": [
                {
                    "component_id": e.get("component_id"),
                    "runtime_check": e.get("runtime_check"),
                }
                for e in self.runtime_evidence
            ],
            "decision_method": "reasoning_llm",
            "intelligence_run_id": self.intelligence_run_id,
            "commit_sha": self.premise_commit_sha,
        }


def compute_evidence_digest(
    *,
    source_kind: str,
    statement: str,
    evidence: Sequence[Dict[str, object]],
    runtime_evidence: Sequence[Dict[str, object]],
) -> str:
    """Canonical digest of one fact's MEANING, for idempotent publication.

    The finding id is deliberately not an input: two rounds that establish the
    same statement from the same span are the same fact, and a rebuild should
    see it once. Everything that could change what the fact asserts is an
    input, so a genuinely different claim gets its own entry.
    """
    payload = {
        "source_kind": source_kind,
        "statement": statement.strip(),
        "evidence": sorted(
            [
                [
                    str(e.get("path") or ""),
                    int(e.get("start_line") or 0),
                    int(e.get("end_line") or 0),
                ]
                for e in evidence
            ]
        ),
        "runtime_evidence": sorted(
            [
                [
                    str(e.get("component_id") or ""),
                    str(e.get("runtime_check") or ""),
                ]
                for e in runtime_evidence
            ]
        ),
    }
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_publication_digest(
    *,
    source_kind: str,
    source_id: int,
    finding_id: int,
    statement: str,
    evidence: Sequence[Dict[str, object]],
    runtime_evidence: Sequence[Dict[str, object]],
) -> str:
    """Idempotency key for one attributable publication.

    Currency is evaluated from the source session's premise. Consequently,
    two sessions that establish the same semantic fact are not interchangeable:
    the older source may become stale while the newer source remains current.
    Keeping the source and finding in this key preserves that distinction while
    making a retry of the exact same publication deterministic.
    """
    semantic_digest = compute_evidence_digest(
        source_kind=source_kind,
        statement=statement,
        evidence=evidence,
        runtime_evidence=runtime_evidence,
    )
    canonical = json.dumps(
        {
            "semantic_digest": semantic_digest,
            "source_kind": source_kind,
            "source_id": int(source_id),
            "finding_id": int(finding_id),
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def publish_joint_understanding_finding(
    conn,
    *,
    system_id: int,
    session_id: int,
    joint_understanding_id: int,
    origin_kind: str,
    origin_id: int,
    finding_row,
    premise_snapshot_id: Optional[int],
    premise_commit_sha: Optional[str],
    now: float,
) -> Optional[int]:
    """Publish one refluxed investigation fact. ``None`` when already present.

    Called from the reflux path inside its transaction, so a rolled-back
    reflux publishes nothing. ``INSERT OR IGNORE`` on the digest is what makes
    a repeated or incremental reflux idempotent -- the caller does not have to
    track which findings it has published before.
    """
    evidence = json.loads(finding_row["evidence_json"])
    runtime_evidence = json.loads(finding_row["runtime_evidence_json"])
    digest = compute_publication_digest(
        source_kind="joint_understanding",
        source_id=joint_understanding_id,
        finding_id=finding_row["id"],
        statement=finding_row["statement"],
        evidence=evidence,
        runtime_evidence=runtime_evidence,
    )
    cur = conn.execute(
        """INSERT OR IGNORE INTO understanding_evidence_feed
            (system_id, session_id, source_kind, source_id, finding_id,
             origin_kind, origin_id, statement, evidence_json,
             runtime_evidence_json, decision_method, intelligence_run_id,
             premise_snapshot_id, premise_commit_sha, content_digest,
             schema_version, created_at)
        VALUES (?, ?, 'joint_understanding', ?, ?, ?, ?, ?, ?, ?,
                'reasoning_llm', ?, ?, ?, ?, ?, ?)""",
        (
            system_id, session_id, joint_understanding_id, finding_row["id"],
            origin_kind, origin_id, finding_row["statement"],
            finding_row["evidence_json"], finding_row["runtime_evidence_json"],
            finding_row["intelligence_run_id"], premise_snapshot_id,
            premise_commit_sha, digest, FEED_SCHEMA_VERSION, now,
        ),
    )
    return cur.lastrowid if cur.rowcount else None


def _row_to_entry(row) -> FeedEntry:
    return FeedEntry(
        id=row["id"],
        system_id=row["system_id"],
        session_id=row["session_id"],
        source_kind=row["source_kind"],
        source_id=row["source_id"],
        finding_id=row["finding_id"],
        origin_kind=row["origin_kind"],
        origin_id=row["origin_id"],
        statement=row["statement"],
        evidence=json.loads(row["evidence_json"]),
        runtime_evidence=json.loads(row["runtime_evidence_json"]),
        intelligence_run_id=row["intelligence_run_id"],
        premise_snapshot_id=row["premise_snapshot_id"],
        premise_commit_sha=row["premise_commit_sha"],
        created_at=row["created_at"],
    )


def current_entries(
    conn,
    *,
    system_id: int,
    session_id: Optional[int] = None,
    limit: int = DEFAULT_FEED_LIMIT,
) -> List[FeedEntry]:
    """The verified facts a rebuild may currently use, newest first.

    Two exclusions, both evaluated HERE rather than at publication time,
    because neither is knowable when the fact is published:

    - the finding was corrected by a later finding in its own session
      (``supersedes_finding_id``): the correction chain is current, not the
      original claim,
    - the source session's premise is no longer ``current`` (Issue #337): the
      ground the fact was established on has moved, disappeared, or was never
      comparable.

    Excluded entries stay in the table and remain readable as history. Nothing
    here deletes or rewrites a published fact.

    ``session_id`` scopes to one interview session's own conversations; omitted,
    the whole System's feed is offered (a fact about the code does not stop
    being true because a different interview session established it).
    """
    clauses = ["f.system_id = ?", "f.superseded = 0"]
    params: List[object] = [system_id]
    if session_id is not None:
        clauses.append("f.session_id = ?")
        params.append(session_id)
    rows = conn.execute(
        f"""SELECT f.* FROM understanding_evidence_feed AS f
            WHERE {' AND '.join(clauses)}
              AND NOT EXISTS (
                SELECT 1 FROM joint_understanding_finding AS newer
                WHERE newer.supersedes_finding_id = f.finding_id
              )
            ORDER BY f.id DESC""",
        tuple(params),
    ).fetchall()

    # One premise evaluation per source session, not per entry: a session with
    # twenty refluxed facts is one verdict, and the evaluation reads rows.
    verdict_cache: Dict[int, str] = {}
    entries: List[FeedEntry] = []
    for row in rows:
        source_id = row["source_id"]
        if source_id not in verdict_cache:
            source = conn.execute(
                "SELECT * FROM joint_understanding_session WHERE id = ? AND system_id = ?",
                (source_id, system_id),
            ).fetchone()
            verdict_cache[source_id] = (
                evaluate_session_premise(conn, source).state
                if source is not None
                else "missing"
            )
        if verdict_cache[source_id] not in ASSERTABLE_PREMISE_STATES:
            continue
        entries.append(_row_to_entry(row))
        if len(entries) >= limit:
            break
    return entries


def record_consumption(
    conn,
    *,
    system_id: int,
    session_id: int,
    consumer_kind: str,
    entry_ids: Sequence[int],
    revision_id: Optional[int],
    intelligence_run_id: Optional[int],
    now: float,
) -> None:
    """Record that a rebuild used these entries.

    Deliberately NOT a confirmation. The human's acceptance of the rebuilt
    understanding is a separate fact with its own record
    (``interview_session.understanding_confirmed_at``, and Issue #312's
    Capability confirmation); writing both from one place would let "the AI fed
    this in" be read as "the developer agreed with it".

    ``INSERT OR IGNORE`` keeps a retried rebuild from double-counting the same
    (entry, consumer, run) triple.
    """
    if consumer_kind not in FEED_CONSUMER_KINDS:
        raise ValueError(
            f"consumer_kind must be one of {list(FEED_CONSUMER_KINDS)}, got {consumer_kind!r}"
        )
    for entry_id in entry_ids:
        conn.execute(
            """INSERT OR IGNORE INTO understanding_evidence_consumption
                (evidence_id, system_id, session_id, consumer_kind, revision_id,
                 intelligence_run_id, consumed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id, system_id, session_id, consumer_kind, revision_id,
                intelligence_run_id, now,
            ),
        )


def prompt_facts(entries: Sequence[FeedEntry]) -> List[Dict[str, object]]:
    """The rebuild-prompt payload for a set of entries (order preserved)."""
    return [entry.as_prompt_fact() for entry in entries]


__all__ = [
    "DEFAULT_FEED_LIMIT",
    "FEED_CONSUMER_KINDS",
    "FEED_SCHEMA_VERSION",
    "FEED_SOURCE_KINDS",
    "FeedEntry",
    "compute_evidence_digest",
    "compute_publication_digest",
    "current_entries",
    "prompt_facts",
    "publish_joint_understanding_finding",
    "record_consumption",
]
