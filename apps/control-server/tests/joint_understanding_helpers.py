"""Shared test helpers for Joint Understanding findings (Issue #337) and for
discussion-scope sessions (Issue #461).

``POST /joint-understanding/{id}/findings`` is developer-only: an
``investigation`` or ``translation`` finding is written exclusively by its
internal producer (``/investigate``, ``/translate``), which validates every
citation against the pinned snapshot. Tests that need a *pre-existing*
investigation finding in order to exercise something else (reflux, outcome
basis, metrics) therefore insert it the way the producer does, rather than
going through an endpoint that -- correctly -- refuses it.

``insert_producer_finding`` writes exactly what ``_persist_loop`` /
``translate_joint_understanding`` write, including the Issue #337 provenance
columns, so a test fixture can never accidentally create a row shape the
production producers cannot.

Issue #461's actual promotion endpoint (``POST
/assistant/discussion-proposals/{id}/hypotheses/{hid}/promote``) does not
exist until Issue #455 lands. ``insert_discussion_thread`` /
``insert_discussion_ju_session`` / ``fake_discussion_origin_provider`` /
``fake_dependency_digest_resolver`` let tests exercise the
``owner_scope='discussion'`` premise/gate contract this Issue owns without
depending on that endpoint or on ``assistant_discussion*.py`` (a
concurrently-edited file this Issue does not own) beyond a bare INSERT into
the table it already declares.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional

_PRODUCER_BY_ROLE = {
    "investigation": "investigation_loop",
    "translation": "translator",
}


def insert_producer_finding(
    *,
    ju_id: int,
    system_id: int,
    origin_role: str = "investigation",
    claim_kind: str = "fact",
    statement: str = "リトライは3回で打ち切られる",
    evidence: Optional[List[Dict[str, Any]]] = None,
    runtime_evidence: Optional[List[Dict[str, Any]]] = None,
    supports_finding_ids: Optional[List[int]] = None,
    competing_explanations: Optional[List[str]] = None,
    refutation_conditions: Optional[List[str]] = None,
    next_investigation: Optional[str] = None,
    uncertainty: str = "",
    supersedes_finding_id: Optional[int] = None,
    intelligence_run_id: Optional[int] = None,
    is_mock: bool = False,
    created_at: Optional[float] = None,
) -> int:
    """Insert one producer-authored finding directly, as its producer would."""
    from app.db import get_conn

    if origin_role not in _PRODUCER_BY_ROLE:
        raise AssertionError(
            f"origin_role={origin_role!r} is not producer-authored; post a "
            "developer finding through the API instead"
        )
    if evidence is None:
        evidence = (
            [{"path": "app/retry.py", "start_line": 1, "end_line": 5, "summary": ""}]
            if origin_role == "investigation" and claim_kind != "unknown"
            else []
        )
    if claim_kind == "hypothesis" and origin_role == "investigation":
        competing_explanations = competing_explanations or ["設定由来かもしれない"]
        refutation_conditions = refutation_conditions or ["設定値が3以外なら誤り"]
        next_investigation = next_investigation or "競合する説明を追加調査する"
    now = created_at if created_at is not None else time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO joint_understanding_finding
                (joint_understanding_id, system_id, origin_role, claim_kind,
                 statement, evidence_json, runtime_evidence_json,
                 supports_finding_ids, competing_explanations,
                 refutation_conditions, next_investigation, uncertainty,
                 supersedes_finding_id, decision_method, intelligence_run_id,
                 is_mock, producer_kind, actor_kind, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reasoning_llm', ?, ?,
                    ?, 'system', ?)""",
            (
                ju_id, system_id, origin_role, claim_kind, statement,
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(runtime_evidence or [], ensure_ascii=False),
                json.dumps(supports_finding_ids or []),
                json.dumps(competing_explanations or [], ensure_ascii=False),
                json.dumps(refutation_conditions or [], ensure_ascii=False),
                next_investigation, uncertainty, supersedes_finding_id,
                intelligence_run_id, 1 if is_mock else 0,
                _PRODUCER_BY_ROLE[origin_role], now,
            ),
        )
        conn.execute(
            "UPDATE joint_understanding_session SET updated_at = ? WHERE id = ?",
            (now, ju_id),
        )
        return cur.lastrowid


# --- Discussion-scope fixtures (Issue #461) ------------------------------------


def insert_discussion_thread(
    *,
    system_id: int,
    thread_key: str = "gap-1|entity|overview_finding|gap-1",
    scope: str = "entity",
    screen_id: str = "objective-map",
    target_kind: str = "overview_finding",
    target_ref: str = "gap-1",
    created_at: Optional[float] = None,
) -> int:
    """Insert a bare ``assistant_discussion_thread`` row.

    Issue #461's fixtures need a Discussion thread to own a discussion-scope
    Joint Understanding session, but do not own ``assistant_discussion*.py``
    (Epic #443's concurrent work does) -- writing the row directly with the
    columns that table already declares keeps this file from depending on an
    API surface that work might still be changing.
    """
    from app.db import get_conn

    now = created_at if created_at is not None else time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO assistant_discussion_thread
                (system_id, thread_key, scope, screen_id, target_kind, target_ref,
                 target_title, captured_target_digest, status, created_at, updated_at,
                 schema_version)
            VALUES (?, ?, ?, ?, ?, ?, '', '', 'open', ?, ?, 'assistant-discussion-thread-v1')""",
            (system_id, thread_key, scope, screen_id, target_kind, target_ref, now, now),
        )
        return cur.lastrowid


@contextmanager
def fake_discussion_origin_provider(facts_by_id: Dict[int, Any]):
    """Temporarily register a fake ``origin_kind='discussion'`` provider.

    ``facts_by_id`` maps a (fixture) hypothesis id to the
    ``app.joint_premise.DiscussionOriginFacts`` it resolves to; an id absent
    from it resolves to ``None`` (the hypothesis no longer exists). Restores
    whatever provider was registered BEFORE this context (not unconditionally
    ``None``) -- Issue #455 registers a real production provider at import
    time, so hardcoding ``None`` here would silently unregister it for every
    test that runs after the first one using this fixture.
    """
    import app.joint_premise as jp

    def _provider(conn, *, origin_id: int, system_id: int):
        return facts_by_id.get(origin_id)

    previous = jp._discussion_origin_provider  # noqa: SLF001
    jp.register_discussion_origin_provider(_provider)
    try:
        yield
    finally:
        jp.register_discussion_origin_provider(previous)


@contextmanager
def fake_dependency_digest_resolver(digests_by_key: Dict[str, Optional[str]]):
    """Temporarily register a fake dependency-manifest digest resolver.

    ``digests_by_key`` maps ``"{target_kind}:{target_ref}"`` to the digest it
    should resolve to; a key absent from it resolves to ``None`` (the
    dependency target no longer exists). Restores whatever resolver was
    registered BEFORE this context, for the same reason
    ``fake_discussion_origin_provider`` does (Issue #455 registers a real one
    at import time).
    """
    import app.joint_premise as jp

    def _resolver(conn, *, target_kind: str, target_ref: str, system_id: int):
        return digests_by_key.get(f"{target_kind}:{target_ref}")

    previous = jp._dependency_digest_resolver  # noqa: SLF001
    jp.register_dependency_digest_resolver(_resolver)
    try:
        yield
    finally:
        jp.register_dependency_digest_resolver(previous)


@contextmanager
def no_discussion_origin_provider():
    """Temporarily UNREGISTER the discussion-origin provider, restoring
    whatever was registered before on exit. Issue #455 always registers a
    real one at import time, so a test that wants to exercise the "provider
    unavailable" fail-closed path needs this rather than relying on nothing
    ever being registered."""
    import app.joint_premise as jp

    previous = jp._discussion_origin_provider  # noqa: SLF001
    jp.register_discussion_origin_provider(None)
    try:
        yield
    finally:
        jp.register_discussion_origin_provider(previous)


@contextmanager
def no_dependency_digest_resolver():
    """Temporarily UNREGISTER the dependency-digest resolver. See
    ``no_discussion_origin_provider`` -- the same reasoning applies."""
    import app.joint_premise as jp

    previous = jp._dependency_digest_resolver  # noqa: SLF001
    jp.register_dependency_digest_resolver(None)
    try:
        yield
    finally:
        jp.register_dependency_digest_resolver(previous)


def insert_discussion_ju_session(
    *,
    system_id: int,
    discussion_thread_id: int,
    origin_id: int,
    origin_content_hash: str = "hypothesis-hash-1",
    question_text: str = "この変更は目的に寄与するか?",
    trigger: str = "explicit_request",
    dependencies: Optional[List[Dict[str, str]]] = None,
    now: Optional[float] = None,
) -> int:
    """Create an ``owner_scope='discussion'`` Joint Understanding session.

    Issue #455's real promotion endpoint does not exist yet, so this inserts
    the row through the same premise-capture path
    (``app.joint_premise.capture_premise_bundle`` with no ``session_row``)
    that endpoint will eventually use, registering a minimal fake
    ``origin_kind='discussion'`` provider for the duration of the capture
    unless one is already registered.
    """
    from app.db import get_conn
    from app.joint_premise import DiscussionOriginFacts, capture_premise_bundle
    from app.joint_understanding import SCHEMA_VERSION, validate_owner_scope

    validate_owner_scope(
        owner_scope="discussion", session_id=None,
        discussion_thread_id=discussion_thread_id,
    )
    moment = now if now is not None else time.time()

    def _capture(conn):
        return capture_premise_bundle(
            conn, origin_kind="discussion", origin_id=origin_id,
            system_id=system_id, now=moment, dependencies=dependencies or [],
        )

    with get_conn() as conn:
        from app import joint_premise as jp

        if jp.discussion_origin_provider_registered():
            premise = _capture(conn)
        else:
            with fake_discussion_origin_provider({
                origin_id: DiscussionOriginFacts(
                    current_origin_id=origin_id, content_hash=origin_content_hash,
                ),
            }):
                premise = _capture(conn)
        cur = conn.execute(
            """INSERT INTO joint_understanding_session
                (owner_scope, discussion_thread_id, system_id, origin_kind,
                 origin_id, trigger, question_text, status,
                 premise_snapshot_id, premise_commit_sha, premise_revision_id,
                 premise_content_hash, premise_capability_digest,
                 premise_intent_digest, premise_review_subject_id,
                 premise_tracking_version, premise_captured_at,
                 premise_dependency_manifest_json,
                 premise_dependency_manifest_digest, schema_version,
                 created_at, updated_at)
            VALUES ('discussion', ?, ?, 'discussion', ?, ?, ?, 'open',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                discussion_thread_id, system_id, origin_id, trigger, question_text,
                premise["premise_snapshot_id"], premise["premise_commit_sha"],
                premise["premise_revision_id"], premise["premise_content_hash"],
                premise["premise_capability_digest"], premise["premise_intent_digest"],
                premise["premise_review_subject_id"], premise["premise_tracking_version"],
                premise["premise_captured_at"],
                premise["premise_dependency_manifest_json"],
                premise["premise_dependency_manifest_digest"],
                SCHEMA_VERSION, moment, moment,
            ),
        )
        return cur.lastrowid
