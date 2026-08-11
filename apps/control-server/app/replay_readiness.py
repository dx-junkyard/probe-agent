"""Replay readiness preflight, evaluated BEFORE a candidate is generated
(Issue #372).

The audited component had 11 traces and every one of them was `not captured`.
The Candidate Studio start screen said `11 traces`, auto-selected the most
recent 50, and only discovered the problem after a reasoning-model call had
already been spent — so the developer paid for a candidate that could never
be evaluated.

This module answers, before anything is generated:

* how many of the component's traces can actually restore their inputs,
* which of the auto-selected window survive that filter,
* whether the surrounding preconditions (symbol, approval, sandbox) hold, and
* what to do when the answer is "none".

Everything is deterministic and finite (Principle 6): the replayability
values are the SDK's own classification (`probe_agent.replay_capture`), the
counts are SQL, and the verdict is a first-match rule over them. Nothing here
guesses whether an uncaptured trace *could* have been replayed — Issue #372's
non-goal is exactly that: "Replay不能なTraceを推測で復元すること".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from probe_agent.replay_capture import PARTIAL, REPLAYABLE, UNREPLAYABLE

__all__ = [
    "NOT_CAPTURED",
    "ReplayCounts",
    "ReplayReadinessCheck",
    "ReplayTraceReadiness",
    "ReplayReadiness",
    "count_replayability",
    "evaluate_readiness",
]

#: A trace whose component never opted into `replay_capture` carries NULL,
#: which is distinct from an attempted-but-failed capture (`unreplayable`).
#: The remediation differs — one needs a code change, the other needs a
#: different trace — so the two are never merged.
NOT_CAPTURED = "not_captured"

#: Reason codes are the SDK's finite set; this is only the display ordering
#: for "the main reason" shown next to a trace.
_REASON_PRIORITY = (
    "capture_failed",
    "redaction_blocked",
    "size_limit_exceeded",
    "round_trip_failed",
    "unsupported_type",
    "depth_limit_exceeded",
    "redacted",
)


@dataclass(frozen=True)
class ReplayCounts:
    """How a set of traces splits across the finite replayability values."""

    total: int = 0
    replayable: int = 0
    partial: int = 0
    unreplayable: int = 0
    not_captured: int = 0

    @property
    def usable(self) -> int:
        """Traces that can contribute a comparison at all.

        `partial` counts: its capture restores the call with some fields
        masked, which still produces a real baseline-vs-candidate diff. The
        caller is told the distinction so the comparison denominator is never
        silently overstated.
        """
        return self.replayable + self.partial


@dataclass(frozen=True)
class ReplayReadinessCheck:
    check_id: str
    status: str  # ok | attention | blocking
    summary: str
    detail: str
    remediation: str


@dataclass(frozen=True)
class ReplayTraceReadiness:
    trace_id: str
    replayability: str
    primary_reason: Optional[str] = None


@dataclass
class ReplayReadiness:
    component_id: str
    counts: ReplayCounts = field(default_factory=ReplayCounts)
    #: The traces a run would actually use, after the auto-selection window.
    selected: ReplayCounts = field(default_factory=ReplayCounts)
    selection_limit: int = 0
    selection_is_automatic: bool = True
    verdict: str = "blocked"  # ready | attention | blocked
    checks: List[ReplayReadinessCheck] = field(default_factory=list)
    snapshot_id: Optional[int] = None
    traces: List[ReplayTraceReadiness] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.verdict == "blocked"


def _classify(replayability: Optional[str]) -> str:
    if replayability in (REPLAYABLE, PARTIAL, UNREPLAYABLE):
        return replayability
    return NOT_CAPTURED


def count_replayability(rows: Sequence) -> ReplayCounts:
    """Split rows (each with a ``replayability`` key) into the finite buckets."""
    buckets = {REPLAYABLE: 0, PARTIAL: 0, UNREPLAYABLE: 0, NOT_CAPTURED: 0}
    for row in rows:
        buckets[_classify(row["replayability"])] += 1
    return ReplayCounts(
        total=len(rows),
        replayable=buckets[REPLAYABLE],
        partial=buckets[PARTIAL],
        unreplayable=buckets[UNREPLAYABLE],
        not_captured=buckets[NOT_CAPTURED],
    )


def primary_reason(reasons: Optional[Sequence[str]]) -> Optional[str]:
    """The single most actionable reason code, for a per-trace hint."""
    if not reasons:
        return None
    for code in _REASON_PRIORITY:
        if code in reasons:
            return code
    return reasons[0]


def _capture_remediation(component_id: str) -> str:
    return (
        f"@probe(component_id=\"{component_id}\", replay_capture=True) を付けて "
        "再デプロイし、実 workload の Trace が届くのを待ってください。"
    )


def evaluate_readiness(
    *,
    component_id: str,
    counts: ReplayCounts,
    selected: ReplayCounts,
    selection_limit: int,
    selection_is_automatic: bool,
    symbol_resolved: bool,
    symbol_detail: str = "",
    approval_active: bool,
    sandbox_available: bool,
    sandbox_detail: str = "",
) -> ReplayReadiness:
    """Deterministic readiness verdict. First match wins per check; the overall
    verdict is the worst check status present.

    A blocking check must always carry a remediation: the whole point of
    running this before generation is that the developer leaves with a next
    step instead of a spent LLM call.
    """
    checks: List[ReplayReadinessCheck] = []

    if counts.total == 0:
        checks.append(
            ReplayReadinessCheck(
                "traces", "blocking",
                "Traceがありません",
                f"component '{component_id}' の Trace はまだ記録されていません。",
                "policy を trace モードにして、実 workload を動かしてください。",
            )
        )
    elif selected.usable == 0:
        # The audited case: traces exist, none of them can restore inputs.
        detail = (
            f"{counts.total}件のTraceのうち、Replayに使えるものは0件です"
            f"（capture未設定 {counts.not_captured}件 / "
            f"capture失敗 {counts.unreplayable}件）。"
        )
        checks.append(
            ReplayReadinessCheck(
                "replayable_traces", "blocking",
                "Replay可能なTraceが0件です",
                detail,
                _capture_remediation(component_id),
            )
        )
    else:
        detail = (
            f"評価に使えるTraceは{selected.usable}件です"
            f"（完全復元 {selected.replayable}件 / 一部マスク {selected.partial}件）。"
        )
        status = "ok"
        excluded = selected.unreplayable + selected.not_captured
        if selected.partial or excluded:
            # Every usable input is masked somewhere; the comparison is real
            # but its denominator is not the full input.
            status = "attention"
            if selected.partial:
                detail += f" 一部マスクされた入力が{selected.partial}件あり、比較は部分再現を含みます。"
            if excluded:
                detail += f" Replay不能な{excluded}件は評価母数から除外されます。"
        checks.append(
            ReplayReadinessCheck(
                "replayable_traces", status,
                f"Replay可能: {selected.usable}件 / {selected.total}件",
                detail,
                "" if status == "ok"
                else "除外・マスク理由を確認し、必要なら別のTraceを選ぶかreplay_capture設定を見直してください。",
            )
        )

    if selection_is_automatic and counts.total > selection_limit:
        checks.append(
            ReplayReadinessCheck(
                "selection_window", "attention",
                f"直近{selection_limit}件を自動選択します",
                f"component の Trace は{counts.total}件あり、そのうち直近{selection_limit}件"
                f"（Replay可能 {selected.usable}件）が評価に使われます。",
                "対象を指定したい場合は Trace を選択するか Replay Set を作成してください。",
            )
        )

    checks.append(
        ReplayReadinessCheck(
            "symbol", "ok" if symbol_resolved else "blocking",
            "対象シンボルを解決できます" if symbol_resolved else "対象シンボルを解決できません",
            symbol_detail or (
                "Snapshot 上で component に対応する関数シンボルが特定できています。"
                if symbol_resolved
                else "Snapshot 上で component に対応する関数シンボルを特定できません。"
            ),
            "" if symbol_resolved
            else "Repository 画面で symbol index を作成し直してください。",
        )
    )

    # Approval is a human gate (Issue #244) and is never auto-satisfied. It is
    # `attention`, not `blocking`: a session may be prepared before approval,
    # but the run itself will refuse.
    checks.append(
        ReplayReadinessCheck(
            "approval", "ok" if approval_active else "attention",
            "Replay承認あり" if approval_active else "Replay承認がありません",
            "この component の Replay 実行は承認済みです。" if approval_active
            else "Replay の実行には人手の承認が必要です（候補の作成自体は可能です）。",
            "" if approval_active else "Replay 承認パネルで承認してください。",
        )
    )

    checks.append(
        ReplayReadinessCheck(
            "sandbox", "ok" if sandbox_available else "blocking",
            "実行環境が利用できます" if sandbox_available else "実行環境が利用できません",
            sandbox_detail or (
                "隔離 worktree での実行が可能です。" if sandbox_available
                else "隔離 worktree を作成できません。"
            ),
            "" if sandbox_available
            else "Repository 設定と実行バックエンドの設定を確認してください。",
        )
    )

    if any(c.status == "blocking" for c in checks):
        verdict = "blocked"
    elif any(c.status == "attention" for c in checks):
        verdict = "attention"
    else:
        verdict = "ready"

    return ReplayReadiness(
        component_id=component_id,
        counts=counts,
        selected=selected,
        selection_limit=selection_limit,
        selection_is_automatic=selection_is_automatic,
        verdict=verdict,
        checks=checks,
    )
