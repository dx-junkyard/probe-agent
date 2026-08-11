"""Shared Snapshot preflight for candidate generation / Replay / Experiment
creation (Issue #369).

Two *independent* finite axes describe a snapshot and must never be rendered
as one another:

``processing`` (`repository_snapshots.status`)
    Did the snapshot's own analysis finish? ``ready`` means "indexing
    completed", nothing more. It says nothing about whether the repository has
    moved on since.

``freshness`` (:data:`SnapshotFreshnessState`)
    Does the snapshot's pinned commit still equal the repository's HEAD?
    ``current`` yes, ``stale`` no, ``unknown`` when HEAD could not be read.

The audited bug was exactly the conflation of the two: the Repository page
warned that the snapshot was behind HEAD while a *different, older* snapshot
was labelled 「ready」, and Experiments offered every stale snapshot as an
ordinary option under the same 「ready」 word.

Everything here is deterministic and finite (Principle 6): the checks classify
into an explicitly enumerated set, HEAD/commit comparison is structural, and
no reasoning model is involved. Git access is read-only — ``rev-parse`` and
``merge-base``/``rev-list`` through :mod:`app.git_ops` — and never fetches,
pulls, checks out, or writes to the target repository (Principle 5).

Connection discipline: :func:`gather_preflight` runs ``git`` subprocesses, so
it opens its own short-lived connections and must be called with **no**
``get_conn()`` connection open (see ``.claude/skills/control-server``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, get_args

from .db import get_conn
from .git_ops import GitError, repository_head_relation, resolve_head
from .models import (
    SnapshotFreshnessState,
    SnapshotPreflightCheckId,
    SnapshotPreflightCheckStatus,
    SnapshotPreflightVerdict,
    SnapshotStatus,
)

FRESHNESS_STATES = get_args(SnapshotFreshnessState)
CHECK_IDS = get_args(SnapshotPreflightCheckId)
CHECK_STATUSES = get_args(SnapshotPreflightCheckStatus)
VERDICTS = get_args(SnapshotPreflightVerdict)

# The check copy is a small local Japanese catalog rather than a
# `state_messages.py` entry: these are not `StateItem` / `DiagnosticCheck` /
# pipeline-step keys, and `tests/test_state_messages.py` drives that catalog
# from those producers only. Dynamic parts stay finite facts (ids, shas,
# counts) interpolated by `str.format`.
_MESSAGES = {
    ("snapshot_processing", "missing"): (
        "Snapshot がありません",
        "この System にはまだ解析済みの Snapshot がありません。",
        "Repository 画面で Snapshot を作成してください。",
    ),
    ("snapshot_processing", "ready"): (
        "解析処理は完了しています",
        "Snapshot #{snapshot_id} の解析処理は完了しています（ready）。",
        "",
    ),
    ("snapshot_processing", "indexing"): (
        "解析処理が実行中です",
        "Snapshot #{snapshot_id} はまだ解析中です。",
        "解析の完了を待ってから実行してください。",
    ),
    ("snapshot_processing", "failed"): (
        "解析処理が失敗しています",
        "Snapshot #{snapshot_id} の解析処理は失敗しました。",
        "Repository 画面で Snapshot を作り直してください。",
    ),
    ("commit_pinned", "ok"): (
        "commit が固定されています",
        "Snapshot #{snapshot_id} は commit {short_sha} に固定されています。",
        "",
    ),
    ("commit_pinned", "missing"): (
        "commit が固定されていません",
        "Snapshot #{snapshot_id} に commit が記録されていません。",
        "Repository 画面で Snapshot を作り直してください。",
    ),
    ("freshness", "current"): (
        "最新の commit と一致しています",
        "Snapshot の commit {short_sha} は現在の HEAD と一致しています。",
        "",
    ),
    ("freshness", "stale"): (
        "最新の commit と一致していません",
        "Snapshot の commit は {short_sha}、現在の HEAD は {short_head} です"
        "（{relation_note}）。この Snapshot で実行すると、HEAD にある変更は"
        "評価対象に含まれません。",
        "最新の内容で評価する場合は Repository 画面で Snapshot を作成し直して"
        "ください。過去の Snapshot を再現目的で使う場合は、理由を添えて続行を"
        "記録してください。",
    ),
    ("freshness", "unknown"): (
        "最新かどうかを判定できません",
        "リポジトリの HEAD を読み取れないため、鮮度を判定できません: {head_error}",
        "リポジトリ設定とパスを確認してください。判定できない場合でも実行は"
        "妨げません。",
    ),
    ("symbol_index", "ok"): (
        "symbol index があります",
        "Snapshot #{snapshot_id} の symbol index は作成済みです。",
        "",
    ),
    ("symbol_index", "missing"): (
        "symbol index がありません",
        "Snapshot #{snapshot_id} の symbol index が未作成のため、対象 symbol を"
        "解決できない場合があります。",
        "Repository 画面の「Snapshot と symbol index を更新」で作成してください。",
    ),
    ("understanding", "completed"): (
        "understanding は構築済みです",
        "Snapshot #{snapshot_id} に対する understanding の構築は完了しています。",
        "",
    ),
    ("understanding", "running"): (
        "understanding を構築中です",
        "Snapshot #{snapshot_id} に対する understanding の構築が実行中です。",
        "完了を待つと、評価時に参照できる情報が増えます。",
    ),
    ("understanding", "failed"): (
        "understanding の構築が完了していません",
        "Snapshot #{snapshot_id} に対する understanding の構築結果は "
        "{build_status} です。",
        "System Understanding 画面で再実行できます。未完了でも実行は妨げません。",
    ),
    ("understanding", "missing"): (
        "understanding が未構築です",
        "Snapshot #{snapshot_id} に対する understanding はまだ構築されていません。",
        "System Understanding 画面で構築できます。未構築でも実行は妨げません。",
    ),
}

_RELATION_NOTES = {
    "behind": "HEAD より {commits_behind} commit 前",
    "diverged": "HEAD と履歴が分岐",
    "same": "HEAD と同一",
    "unknown": "差分の commit 数は不明",
}


@dataclass(frozen=True)
class PreflightFacts:
    """Every input the preflight decision reads, as persisted/observed facts.

    Pure value object: :func:`evaluate_preflight` takes only this, so the same
    facts always produce the same verdict (no clock, no request-scoped value).
    """

    snapshot_id: Optional[int] = None
    processing_state: Optional[SnapshotStatus] = None
    snapshot_commit_sha: Optional[str] = None
    head_sha: Optional[str] = None
    head_error: Optional[str] = None
    head_relation: str = "unknown"
    commits_behind: Optional[int] = None
    has_symbol_index: bool = False
    understanding_build_status: Optional[str] = None
    recommended_snapshot_id: Optional[int] = None
    recommended_snapshot_commit_sha: Optional[str] = None
    recommended_snapshot_freshness: SnapshotFreshnessState = "unknown"


@dataclass(frozen=True)
class PreflightCheck:
    check_id: SnapshotPreflightCheckId
    status: SnapshotPreflightCheckStatus
    summary: str
    detail: str
    remediation: str


@dataclass(frozen=True)
class PreflightResult:
    snapshot_id: Optional[int]
    processing_state: Optional[SnapshotStatus]
    freshness: SnapshotFreshnessState
    commit_sha: Optional[str]
    head_sha: Optional[str]
    head_relation: str
    commits_behind: Optional[int]
    verdict: SnapshotPreflightVerdict
    checks: List[PreflightCheck] = field(default_factory=list)
    recommended_snapshot_id: Optional[int] = None
    recommended_snapshot_commit_sha: Optional[str] = None
    recommended_snapshot_freshness: SnapshotFreshnessState = "unknown"
    # True only when freshness is definitively `stale`. `unknown` never
    # requires an acknowledgement: an unreadable HEAD is not evidence that the
    # snapshot is behind (two-stage rule, same as `repository-gate.ts`).
    requires_stale_acknowledgement: bool = False

    @property
    def is_recommended(self) -> bool:
        return (
            self.snapshot_id is not None
            and self.snapshot_id == self.recommended_snapshot_id
        )


class SnapshotPreflightError(Exception):
    """A preflight precondition the caller must translate to HTTP."""


class StaleSnapshotNotAcknowledged(SnapshotPreflightError):
    """A definitively stale snapshot was used without a recorded reason."""

    def __init__(self, result: PreflightResult):
        self.result = result
        super().__init__(stale_continuation_message(result))


def short_sha(sha: Optional[str]) -> str:
    return sha[:8] if sha else "—"


def classify_freshness(
    *,
    snapshot_commit_sha: Optional[str],
    head_sha: Optional[str],
    head_error: Optional[str],
) -> SnapshotFreshnessState:
    """Finite freshness classification. First match wins.

    Deliberately independent of the processing state: a ``failed`` snapshot
    pinned to the current HEAD is still ``current``, and a ``ready`` snapshot
    three commits back is ``stale``.
    """
    if head_error or not head_sha or not snapshot_commit_sha:
        return "unknown"
    return "current" if snapshot_commit_sha == head_sha else "stale"


def _message(key, **params) -> tuple:
    summary, detail, remediation = _MESSAGES[key]
    return (
        summary.format(**params),
        detail.format(**params),
        remediation.format(**params),
    )


def _check(check_id, status, key, **params) -> PreflightCheck:
    summary, detail, remediation = _message(key, **params)
    return PreflightCheck(
        check_id=check_id,
        status=status,
        summary=summary,
        detail=detail,
        remediation=remediation,
    )


def evaluate_preflight(facts: PreflightFacts) -> PreflightResult:
    """Pure, deterministic preflight evaluation over persisted facts.

    Every check lands in the finite :data:`CHECK_STATUSES` set; the verdict is
    the worst status present (``blocking`` > ``attention`` > everything else).
    ``unknown`` never blocks and never downgrades the verdict on its own.
    """
    freshness = classify_freshness(
        snapshot_commit_sha=facts.snapshot_commit_sha,
        head_sha=facts.head_sha,
        head_error=facts.head_error,
    )
    params = {
        "snapshot_id": facts.snapshot_id,
        "short_sha": short_sha(facts.snapshot_commit_sha),
        "short_head": short_sha(facts.head_sha),
        "head_error": facts.head_error or "原因不明",
        "commits_behind": facts.commits_behind,
        "build_status": facts.understanding_build_status or "未実行",
        "relation_note": _RELATION_NOTES.get(
            facts.head_relation, _RELATION_NOTES["unknown"]
        ).format(commits_behind=facts.commits_behind),
    }

    checks: List[PreflightCheck] = []

    # 1. processing state -- "did the analysis finish".
    if facts.snapshot_id is None or facts.processing_state is None:
        checks.append(
            _check(
                "snapshot_processing", "blocking",
                ("snapshot_processing", "missing"), **params
            )
        )
    elif facts.processing_state == "ready":
        checks.append(
            _check(
                "snapshot_processing", "ok",
                ("snapshot_processing", "ready"), **params
            )
        )
    elif facts.processing_state == "indexing":
        checks.append(
            _check(
                "snapshot_processing", "blocking",
                ("snapshot_processing", "indexing"), **params
            )
        )
    else:
        checks.append(
            _check(
                "snapshot_processing", "blocking",
                ("snapshot_processing", "failed"), **params
            )
        )

    # 2. pinned commit -- every downstream read is `git show <sha>:<path>`.
    if facts.snapshot_id is not None:
        if facts.snapshot_commit_sha:
            checks.append(
                _check("commit_pinned", "ok", ("commit_pinned", "ok"), **params)
            )
        else:
            checks.append(
                _check(
                    "commit_pinned", "blocking",
                    ("commit_pinned", "missing"), **params
                )
            )

        # 3. freshness -- the axis that is NOT the processing state.
        if freshness == "current":
            checks.append(
                _check("freshness", "ok", ("freshness", "current"), **params)
            )
        elif freshness == "stale":
            checks.append(
                _check("freshness", "attention", ("freshness", "stale"), **params)
            )
        else:
            checks.append(
                _check("freshness", "unknown", ("freshness", "unknown"), **params)
            )

        # 4. symbol index.
        if facts.has_symbol_index:
            checks.append(
                _check("symbol_index", "ok", ("symbol_index", "ok"), **params)
            )
        else:
            checks.append(
                _check(
                    "symbol_index", "attention",
                    ("symbol_index", "missing"), **params
                )
            )

        # 5. understanding build state for this snapshot.
        status = facts.understanding_build_status
        if status == "completed":
            checks.append(
                _check("understanding", "ok", ("understanding", "completed"), **params)
            )
        elif status in ("queued", "running"):
            checks.append(
                _check(
                    "understanding", "attention",
                    ("understanding", "running"), **params
                )
            )
        elif status is None:
            checks.append(
                _check(
                    "understanding", "attention",
                    ("understanding", "missing"), **params
                )
            )
        else:
            checks.append(
                _check(
                    "understanding", "attention",
                    ("understanding", "failed"), **params
                )
            )

    if any(c.status == "blocking" for c in checks):
        verdict: SnapshotPreflightVerdict = "blocked"
    elif any(c.status == "attention" for c in checks):
        verdict = "attention"
    else:
        verdict = "ready"

    return PreflightResult(
        snapshot_id=facts.snapshot_id,
        processing_state=facts.processing_state,
        freshness=freshness,
        commit_sha=facts.snapshot_commit_sha,
        head_sha=facts.head_sha,
        head_relation=facts.head_relation,
        commits_behind=facts.commits_behind,
        verdict=verdict,
        checks=checks,
        recommended_snapshot_id=facts.recommended_snapshot_id,
        recommended_snapshot_commit_sha=facts.recommended_snapshot_commit_sha,
        recommended_snapshot_freshness=facts.recommended_snapshot_freshness,
        requires_stale_acknowledgement=freshness == "stale",
    )


def stale_continuation_message(result: PreflightResult) -> str:
    """The reason + impact a caller must show before continuing on a stale
    snapshot, and the exact text returned with the 409."""
    parts = [
        f"Snapshot #{result.snapshot_id} は最新の commit と一致していません"
        f"（Snapshot: {short_sha(result.commit_sha)} / HEAD: "
        f"{short_sha(result.head_sha)}）。",
        "この Snapshot で実行すると HEAD にある変更は評価対象に含まれません。",
    ]
    if result.recommended_snapshot_id is not None and not result.is_recommended:
        parts.append(
            f"推奨 Snapshot は #{result.recommended_snapshot_id}"
            f"（{short_sha(result.recommended_snapshot_commit_sha)}）です。"
        )
    parts.append(
        "過去の Snapshot を再現目的で使う場合は stale_snapshot_ack.reason に"
        "理由を記録して再送してください。"
    )
    return " ".join(parts)


# --- Fact gathering -------------------------------------------------------------


def _latest_ready_snapshot(conn, system_id: int):
    return conn.execute(
        """
        SELECT id, commit_sha, status, repo_path, created_at
        FROM repository_snapshots
        WHERE system_id = ? AND status = 'ready' AND repo_path != ''
        ORDER BY id DESC LIMIT 1
        """,
        (system_id,),
    ).fetchone()


def _snapshot_by_id(conn, system_id: int, snapshot_id: int):
    return conn.execute(
        """
        SELECT id, commit_sha, status, repo_path, created_at
        FROM repository_snapshots WHERE id = ? AND system_id = ?
        """,
        (snapshot_id, system_id),
    ).fetchone()


def _latest_build_status(conn, system_id: int, snapshot_id: int) -> Optional[str]:
    row = conn.execute(
        """
        SELECT status FROM system_understanding_builds
        WHERE system_id = ? AND snapshot_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (system_id, snapshot_id),
    ).fetchone()
    return row["status"] if row else None


def _has_symbol_index(conn, snapshot_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM code_symbols WHERE snapshot_id = ? LIMIT 1",
        (snapshot_id,),
    ).fetchone()
    return row is not None


def gather_preflight(
    system_id: int, snapshot_id: Optional[int] = None
) -> PreflightResult:
    """Read the facts and evaluate them for one snapshot.

    ``snapshot_id`` omitted resolves the same "latest ready snapshot" the
    Replay / Candidate Studio endpoints resolve by default, so the preflight
    describes exactly the snapshot the run would use.

    Must be called with no ``get_conn()`` connection open: it runs ``git``
    subprocesses between its reads.
    """
    with get_conn() as conn:
        recommended = _latest_ready_snapshot(conn, system_id)
        if snapshot_id is None:
            snapshot = recommended
        else:
            snapshot = _snapshot_by_id(conn, system_id, snapshot_id)
        repo_row = conn.execute(
            "SELECT repo_path FROM repository_configs WHERE system_id = ?",
            (system_id,),
        ).fetchone()
        has_symbols = (
            _has_symbol_index(conn, snapshot["id"]) if snapshot is not None else False
        )
        build_status = (
            _latest_build_status(conn, system_id, snapshot["id"])
            if snapshot is not None
            else None
        )

    repo_path = (repo_row["repo_path"] if repo_row else None) or (
        snapshot["repo_path"] if snapshot is not None else None
    )

    head_sha: Optional[str] = None
    head_error: Optional[str] = None
    if repo_path:
        try:
            head_sha = resolve_head(repo_path)
        except GitError as exc:
            head_error = str(exc)
    else:
        head_error = "リポジトリが設定されていません"

    snapshot_commit = snapshot["commit_sha"] if snapshot is not None else None
    relation = repository_head_relation(repo_path or "", snapshot_commit, head_sha)

    recommended_freshness = classify_freshness(
        snapshot_commit_sha=recommended["commit_sha"] if recommended else None,
        head_sha=head_sha,
        head_error=head_error,
    )

    return evaluate_preflight(
        PreflightFacts(
            snapshot_id=snapshot["id"] if snapshot is not None else None,
            processing_state=snapshot["status"] if snapshot is not None else None,
            snapshot_commit_sha=snapshot_commit,
            head_sha=head_sha,
            head_error=head_error,
            head_relation=relation.head_relation,
            commits_behind=relation.commits_behind,
            has_symbol_index=has_symbols,
            understanding_build_status=build_status,
            recommended_snapshot_id=recommended["id"] if recommended else None,
            recommended_snapshot_commit_sha=(
                recommended["commit_sha"] if recommended else None
            ),
            recommended_snapshot_freshness=recommended_freshness,
        )
    )


def resolve_stale_decision(
    result: PreflightResult, ack_reason: Optional[str]
) -> tuple:
    """Return the ``(freshness, head_sha, stale_ack_reason)`` triple to persist.

    Raises :class:`StaleSnapshotNotAcknowledged` when the snapshot is
    definitively stale and the caller supplied no reason. The reason is a
    ``decision_method: manual`` fact: the developer, not the model or a
    heuristic, decides that a reproduction run on an older snapshot is
    intended.
    """
    reason = (ack_reason or "").strip()
    if result.requires_stale_acknowledgement and not reason:
        raise StaleSnapshotNotAcknowledged(result)
    return (
        result.freshness,
        result.head_sha,
        reason or None,
    )
