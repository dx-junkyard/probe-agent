"""Overview / System Intelligence Brief projection (Issues #380-#384).

The Overview screen used to answer "did it connect, is there data" with four
metric cards and a full Component table. Issue #380 redefines it as the
decision cockpit: what is this system FOR, what did we just learn, and what is
the one thing to do next.

This module is the canonical server-side projection behind that screen. Five
sections, each derived deterministically from persisted rows (Principle 6):

* **A. System Brief** (#381) -- Vision / System Purpose / Core Capabilities /
  Decision Readiness. Not a new understanding model: it IS
  `understanding_brief.build_understanding_brief`, the same one the Interview
  screen renders (#351-#354), read for this System's newest Interview session.
* **B. 今わかったこと** (#382) -- at most three findings, extracted and ranked
  by a finite rule set over persisted facts. No LLM scoring, no keyword
  ranking, no client heuristic.
* **C. 次にすること** (#383) -- a first-match rule table that yields exactly
  zero or one primary action, with its reason, its completion condition, and
  what it unlocks.
* **D. 改善ループの現在地** -- `system_state.derive_user_phase` under
  developer-facing labels. The Overview never introduces a second phase model.
* **E. Runtime health** (#384) -- Issue #370's connectivity axes plus bounded
  recent-window quality counts, as a SECONDARY area.

Three boundaries this module must not cross:

* **It never gates and never approves.** Every human gate (理解の確認 /
  Alignment 項目の確定 / 提案の承認 / 差分の適用 / 観測の開始 / 採否の記録 /
  publish) stays exactly where it is. The Overview only says which one is
  next.
* **It never re-decides another surface's canonical state.** Readiness comes
  from `understanding_brief`, the interview workflow state from
  `interview_workflow`, the phase from `system_state`. A disagreement between
  the Overview and those screens is impossible by construction.
* **It writes nothing.** Opening the Overview is not a human decision, so no
  view, no acknowledgement, and no "last seen" marker is ever persisted here
  (#382: 暗黙のpage viewを人間判断として保存しない).

probe-agent:
  role: Deterministic Overview / System Intelligence Brief projection
  capability: system-state-assessment
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read]
  probe_value: Verify the Overview reuses the canonical Brief/workflow/phase projections instead of re-deriving them, that findings are selected by a finite deterministic rule set, and that exactly zero or one primary action is returned for every representative state.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, get_args

from . import replay_readiness, state_facts, system_state, understanding_brief
from .db import get_conn
from .models import (
    SMOKE_CHECK_COMPONENT_ID,
    OverviewActionKey,
    OverviewActionState,
    OverviewFindingKind,
    OverviewFindingProvenance,
    OverviewFindingSeverity,
    OverviewFindingStatus,
    OverviewFindingsState,
    OverviewLoopStage,
    OverviewLoopStageStatus,
    OverviewSection,
)

__all__ = [
    "FINDING_KINDS",
    "FINDING_SEVERITIES",
    "FINDING_STATUSES",
    "FINDING_PROVENANCES",
    "FINDINGS_STATES",
    "ACTION_KEYS",
    "ACTION_STATES",
    "LOOP_STAGES",
    "LOOP_STAGE_STATUSES",
    "SECTIONS",
    "INITIAL_FINDING_LIMIT",
    "RUNTIME_WINDOW_SECONDS",
    "Finding",
    "FindingInputs",
    "NextActionFacts",
    "OverviewAction",
    "OverviewLoopStageView",
    "OverviewResult",
    "RuntimeHealth",
    "Target",
    "collect_findings",
    "select_findings",
    "decide_next_action",
    "build_loop_stages",
    "build_overview",
    "resolve_pending_publish",
    "resolve_pending_instrumentation_publish",
    "load_repository_fact",
    "load_pending_experiments",
    "load_variant_facts",
    "load_decision_facts",
    "load_snapshot_commit",
    "load_revision_created_at",
]


# --- §0. Finite vocabularies -------------------------------------------------
#
# Each is defined ONCE as a `Literal` in `app/models.py` and mirrored here
# with `get_args`, the same discipline #351 established: the response schema
# carries the enum, `tests/test_interview_type_parity.py` holds the Dashboard
# union to it, and this module still gets plain tuples to iterate over.

FINDING_KINDS: Tuple[str, ...] = get_args(OverviewFindingKind)
FINDING_SEVERITIES: Tuple[str, ...] = get_args(OverviewFindingSeverity)
FINDING_STATUSES: Tuple[str, ...] = get_args(OverviewFindingStatus)
FINDING_PROVENANCES: Tuple[str, ...] = get_args(OverviewFindingProvenance)
FINDINGS_STATES: Tuple[str, ...] = get_args(OverviewFindingsState)
ACTION_KEYS: Tuple[str, ...] = get_args(OverviewActionKey)
ACTION_STATES: Tuple[str, ...] = get_args(OverviewActionState)
LOOP_STAGES: Tuple[str, ...] = get_args(OverviewLoopStage)
LOOP_STAGE_STATUSES: Tuple[str, ...] = get_args(OverviewLoopStageStatus)
SECTIONS: Tuple[str, ...] = get_args(OverviewSection)

#: 「最大3件」 (#382). A cap, never a pad: two findings render as two.
INITIAL_FINDING_LIMIT = 3

#: The bounded window every runtime QUALITY count is measured over. A
#: cumulative total can never show that something stopped or started going
#: wrong, which is the whole reason #370 split freshness out of `state`.
RUNTIME_WINDOW_SECONDS = 24 * 3600


# --- Japanese developer-facing labels ----------------------------------------
#
# Same convention as `state_messages.py` / `routes/interview_workflow.py`: the
# server owns the copy for its own finite vocabularies (CLAUDE.md UI language
# rule) and the Dashboard renders it. Canonical identifiers stay canonical.

FINDING_KIND_LABELS: Dict[str, str] = {
    "claim_conflict": "理解の矛盾",
    "understanding_blocked": "理解の構築失敗",
    "understanding_changed": "理解の変化",
    "capability_composition_stale": "確定済み構成の失効",
    "unconfirmed_core_claim": "未確認の主要な理解",
    "runtime_mismatch": "実装理解と実行時の食い違い",
    "runtime_unobserved": "未観測の理解",
    "connectivity_lost": "Trace 受信の停止",
    "snapshot_stale": "Snapshot の陳腐化",
    "evaluation_decision_pending": "採否の判断待ち",
    "improvement_candidate_ready": "評価済みの改善候補",
}

FINDING_SEVERITY_LABELS: Dict[str, str] = {
    "blocker": "判断を止めています",
    "human_decision_required": "あなたの判断が必要です",
    "material_change": "判断の前提が変わりました",
    "informative": "参考情報",
}

FINDING_STATUS_LABELS: Dict[str, str] = {
    "new": "前回の確認より後に発生",
    "ongoing": "前回の確認時点から継続",
    "not_compared": "比較の基準がありません",
}

FINDING_PROVENANCE_LABELS: Dict[str, str] = {
    # The first four are worded exactly like `understanding_brief`'s, because
    # they ARE the same values: a claim's provenance carries into a finding
    # about that claim unchanged.
    "developer_intent": "開発者が明示した意図",
    "implementation_fact": "コード・ドキュメントの実装事実",
    "runtime_observation": "Runtime の観測",
    "ai_hypothesis": "AI の解釈・仮説",
    "developer_decision": "あなたの判断の記録",
    "system_process": "システムの処理記録",
    "mixed": "複数の出所",
}

FRESHNESS_LABELS: Dict[str, str] = {
    "never_received": "まだ受信していません",
    "receiving_now": "受信中",
    "delayed": "受信が滞っています",
    "stale": "受信が止まっています",
}

#: The six loop stages under the names the developer reads. The keys are
#: `system_state.PHASE_ORDER` verbatim -- the mapping is a rename, not a
#: second model, so the sidebar's user_phase and this rail can never disagree.
LOOP_STAGE_LABELS: Dict[str, str] = {
    "setup": "Setup: 対象を登録する",
    "preparation": "Understand: システムを理解する",
    "instrumentation": "Instrument: 計測を組み込む",
    "observation": "Observe: 実行時を観測する",
    "evaluation": "Evaluate: 候補を評価する",
    "publish": "Publish: 変更を公開する",
}

LOOP_STAGE_MEANINGS: Dict[str, str] = {
    "setup": "対象リポジトリを登録し、解析できる状態にします。",
    "preparation": "このシステムが何のためにあり、何ができるかを、根拠つきで確定します。",
    "instrumentation": "確定した理解のどこを観測するかを決め、計測を組み込みます。",
    "observation": "実際の入出力・エラー・所要時間を受け取り、理解と突き合わせます。",
    "evaluation": "観測に基づく改善候補を隔離環境で比較し、採否を記録します。",
    "publish": "採用した変更をレビュー可能な形で公開します。",
}

LOOP_STAGE_NEXT_MILESTONE: Dict[str, str] = {
    "setup": "解析できる snapshot が 1 件そろうこと。",
    "preparation": "System Purpose と主要機能をあなたが確認済みにすること。",
    "instrumentation": "承認済みの計測経路が 1 本できること。",
    "observation": "実際の Trace を受信し続けている状態になること。",
    "evaluation": "候補を比較し、採否を 1 件記録すること。",
    "publish": "採用した変更の Pull Request を出すこと。",
}


# --- §1. Findings ------------------------------------------------------------


class _BriefUnavailable(Exception):
    """Raised internally when findings cannot be built because the Brief could
    not be read. Distinct from a genuine extraction failure so the two report
    different sentences."""


class _FindingsUnavailable(Exception):
    """A finding dependency could not be read; this is not an empty set."""


@dataclass(frozen=True)
class Target:
    route: str
    label: str
    params: Dict[str, str] = field(default_factory=dict)
    anchor: Optional[str] = None


@dataclass(frozen=True)
class Finding:
    """One 「今わかったこと」.

    `dedupe_key` collapses findings that share a ROOT CAUSE (#382:
    同じ原因の重複findingは集約する); `subject_key` collapses findings about
    the same SUBJECT across kinds, so a claim that is both conflicting and
    unconfirmed reports the blocking fact once rather than twice.
    """

    kind: str
    severity: str
    summary: str
    decision_impact: str
    provenance: str
    dedupe_key: str
    subject_key: str = ""
    snapshot_id: Optional[int] = None
    revision_id: Optional[int] = None
    runtime_window_seconds: Optional[float] = None
    #: When the underlying fact came into existence, read from the row that
    #: carries it. Never "when the developer last opened this page".
    first_seen: Optional[float] = None
    last_updated: Optional[float] = None
    target: Optional[Target] = None
    evidence: Tuple[Dict[str, Any], ...] = ()
    occurrence_count: int = 1

    @property
    def id(self) -> str:
        """Stable across reloads and rebuilds.

        Derived from the kind and the dedupe key rather than from a row id: a
        rebuilt Alignment item or a new understanding revision renumbers rows
        while describing the same finding, and a finding whose id changed on
        every rebuild could never be reported as `ongoing`.
        """
        digest = hashlib.sha256(self.dedupe_key.encode("utf-8")).hexdigest()[:12]
        return f"{self.kind}:{digest}"


#: Ranking ladder. Order is the contract, not a preference: a blocker outranks
#: a decision, which outranks a change of premise, which outranks context.
_SEVERITY_RANK = {value: index for index, value in enumerate(FINDING_SEVERITIES)}

#: Within one severity, a finding that appeared after the baseline is the one
#: the developer has not seen yet.
_STATUS_RANK = {"new": 0, "ongoing": 1, "not_compared": 2}

#: Final ordering gate inside a severity+status band. Fixed, hand-authored,
#: never derived from counts -- two findings must never swap places because a
#: number moved.
_KIND_RANK = {value: index for index, value in enumerate(FINDING_KINDS)}


@dataclass(frozen=True)
class FindingInputs:
    """Everything `collect_findings` may read. All persisted facts.

    Passing a value object rather than a connection keeps the extraction a
    pure function: every representative state is unit-testable without a
    database, which is what makes the ranking and the empty states verifiable.
    """

    #: `understanding_brief.BriefResult`, or None when it could not be built.
    brief: Optional[Any] = None
    #: Timestamp of the newest understanding revision (when the AI's current
    #: reading came into existence).
    revision_created_at: Optional[float] = None
    #: Unresolved blocking process failures affecting the understanding.
    blocking_failures: Tuple[Dict[str, Any], ...] = ()
    #: The session's snapshot is older than the System's newest ready one.
    snapshot_stale: bool = False
    #: When that newer snapshot became available -- i.e. when this
    #: understanding started being out of date.
    latest_ready_snapshot_at: Optional[float] = None
    #: Alignment items carrying #290's finite `runtime_check` state.
    runtime_checks: Tuple[Dict[str, Any], ...] = ()
    #: Only the live axis. The cumulative `state` is deliberately absent: a
    #: finding is a statement about NOW, and `never_received` is already
    #: unreachable here because `classify_connectivity_freshness` returns it
    #: exactly when nothing has arrived.
    connectivity_freshness: str = "never_received"
    last_real_trace_at: Optional[float] = None
    #: The System's own freshness thresholds, needed to date the onset of a
    #: `delayed` / `stale` state (see `freshness_onset`).
    delayed_after_seconds: float = state_facts.DEFAULT_DELAYED_AFTER_SECONDS
    stale_after_seconds: float = state_facts.DEFAULT_STALE_AFTER_SECONDS
    undecided_experiment_count: int = 0
    undecided_experiment_at: Optional[float] = None
    undecided_experiment_id: Optional[int] = None
    completed_variant_count: int = 0
    completed_variant_at: Optional[float] = None
    session_id: Optional[int] = None


def _claim_route(session_id: Optional[int], anchor: str = "understanding-brief") -> Target:
    params = {"session": str(session_id)} if session_id else {}
    return Target(
        route="/interview",
        label="システム理解を開く",
        params=params,
        anchor=anchor,
    )


def collect_findings(inputs: FindingInputs) -> List[Finding]:
    """Extract every candidate finding. Pure; ordering happens in
    `select_findings`.

    Each block below is one deterministic extractor over one persisted fact.
    Nothing here scores importance -- importance is the `severity` the fact's
    own kind carries, which is why the ladder is fixed in
    `FINDING_SEVERITIES` and not computed per finding.
    """
    out: List[Finding] = []
    brief = inputs.brief
    snapshot_id = getattr(brief, "snapshot_id", None) if brief else None
    revision_id = getattr(brief, "revision_id", None) if brief else None

    # 1. A blocking process failure: the understanding could not be built, so
    #    there is nothing to judge with. `system_process` provenance -- this is
    #    a fact about probe-agent, not about the target system.
    for row in inputs.blocking_failures:
        kind_label = row.get("process_kind") or "understanding_build"
        out.append(
            Finding(
                kind="understanding_blocked",
                severity="blocker",
                summary="システム理解を作る処理が失敗したままです。",
                decision_impact=(
                    "現在の理解を判断材料として信頼できません。"
                    "再試行するまで、この画面の内容は失敗前のものです。"
                ),
                provenance="system_process",
                dedupe_key=f"understanding_blocked:{kind_label}",
                subject_key="understanding",
                snapshot_id=snapshot_id,
                revision_id=revision_id,
                first_seen=row.get("finished_at"),
                last_updated=row.get("finished_at"),
                target=_claim_route(inputs.session_id),
                evidence=({"process_kind": kind_label, "error": row.get("error") or ""},),
            )
        )

    # 2. Contradictory claims. The Brief already classified these; the Overview
    #    only surfaces them -- it does not re-classify (#380 principle 6).
    for claim in _brief_claims(brief):
        if claim.confirmation != "conflicting":
            continue
        out.append(
            Finding(
                kind="claim_conflict",
                severity="blocker",
                summary=f"「{claim.name}」の解釈に矛盾があります。",
                decision_impact=(
                    "この主張を前提にした判断は、どちらの解釈が正しいか決まるまで保留してください。"
                ),
                provenance=_claim_provenance(claim),
                dedupe_key=f"claim_conflict:{claim.kind}:{claim.name}",
                subject_key=f"claim:{claim.kind}:{claim.name}",
                snapshot_id=snapshot_id,
                revision_id=revision_id,
                first_seen=inputs.revision_created_at,
                last_updated=inputs.revision_created_at,
                target=_claim_route(inputs.session_id),
                evidence=tuple(claim.evidence[:3]),
            )
        )

    # 3. The understanding moved after the developer confirmed it. Aggregated
    #    per section: five changed capabilities are one 「主要機能が変わった」
    #    finding, not five entries competing for the same three slots.
    changes = list(getattr(brief, "changes_since_confirmation", []) or [])
    # A claim's provenance carries into the finding about its change. Hardcoding
    # `implementation_fact` here reported a developer-authored Vision edit as a
    # code reading.
    #
    # The key is (section, name), not name alone: the three sections are
    # independent namespaces, and a Vision and a Core Capability may legitimately
    # share a name. Keyed by name, whichever section was walked last silently
    # overwrote the others, so a Vision change could inherit a Capability's
    # provenance. The section vocabulary comes from `understanding_brief`'s own
    # `BRIEF_SECTIONS`, which is also what `UnderstandingChange.section` carries.
    claim_provenance = {
        (section, claim.name): _claim_provenance(claim)
        for section, claim in _brief_claims_by_section(brief)
    }
    by_section: Dict[str, List[Any]] = {}
    for change in changes:
        by_section.setdefault(change.section, []).append(change)
    for section, section_changes in by_section.items():
        label = section_changes[0].section_label
        details = "、".join(
            sorted({f"{c.name}: {c.detail}" for c in section_changes})[:3]
        )
        provenance = aggregate_provenance(
            [
                claim_provenance[(c.section, c.name)]
                for c in section_changes
                if (c.section, c.name) in claim_provenance
            ]
        )
        out.append(
            Finding(
                kind="understanding_changed",
                severity="material_change",
                summary=f"{label}が前回の確認後に {len(section_changes)} 件変わりました。",
                decision_impact=(
                    "前回の確定をそのまま再利用できません。変更点を確認してから次に進んでください。"
                ),
                provenance=provenance,
                dedupe_key=f"understanding_changed:{section}",
                subject_key=f"section:{section}",
                snapshot_id=snapshot_id,
                revision_id=revision_id,
                first_seen=inputs.revision_created_at,
                last_updated=inputs.revision_created_at,
                target=_claim_route(inputs.session_id),
                evidence=({"section": section, "changes": details},),
                occurrence_count=len(section_changes),
            )
        )

    # 4. A confirmed Capability composition that no longer matches the current
    #    understanding (#312's canonical confirmation going stale).
    readiness_codes = {
        reason.code for reason in getattr(brief, "readiness_reasons", []) or []
    }
    if "capability_composition_stale" in readiness_codes:
        out.append(
            Finding(
                kind="capability_composition_stale",
                severity="human_decision_required",
                summary="確定済みの Capability 構成が、最新の理解に対応していません。",
                decision_impact="構成を確認し直すまで、Capability 単位の判断は前回の前提のままです。",
                provenance="implementation_fact",
                dedupe_key="capability_composition_stale",
                subject_key="capability_composition",
                snapshot_id=snapshot_id,
                revision_id=revision_id,
                first_seen=inputs.revision_created_at,
                last_updated=inputs.revision_created_at,
                target=_claim_route(inputs.session_id),
            )
        )

    # 5. Core claims the developer has never settled. Aggregated to one
    #    finding: 「未確認が N 件ある」 is a single decision, and listing each
    #    of them here would duplicate the Interview's own worklist.
    unsettled = [
        claim
        for claim in _brief_claims(brief)
        if claim.confirmation in ("ai_hypothesis", "unknown", "recheck_required")
    ]
    if unsettled:
        names = "、".join(f"「{c.name}」" for c in unsettled[:3])
        out.append(
            Finding(
                kind="unconfirmed_core_claim",
                severity="human_decision_required",
                summary=f"Vision・Purpose・主要機能のうち {len(unsettled)} 件が未確認です（{names} など）。",
                decision_impact=(
                    "未確認の内容は AI の解釈です。これを前提に計測や改善を進めると、"
                    "前提ごと作り直しになる可能性があります。"
                ),
                provenance="ai_hypothesis",
                dedupe_key="unconfirmed_core_claim",
                subject_key="understanding_confirmation",
                snapshot_id=snapshot_id,
                revision_id=revision_id,
                first_seen=inputs.revision_created_at,
                last_updated=inputs.revision_created_at,
                target=_claim_route(inputs.session_id),
                occurrence_count=len(unsettled),
            )
        )

    # 6. Runtime disagreeing with the implementation reading (#290). The finite
    #    `runtime_check` value is read as persisted; it is never recomputed.
    mismatches = [r for r in inputs.runtime_checks if r.get("runtime_check") == "mismatch"]
    if mismatches:
        newest = max((r.get("updated_at") or 0) for r in mismatches) or None
        oldest = min((r.get("created_at") or 0) for r in mismatches) or None
        out.append(
            Finding(
                kind="runtime_mismatch",
                severity="human_decision_required",
                summary=f"実行時の観測と一致しない理解が {len(mismatches)} 件あります。",
                decision_impact=(
                    "コードから読み取った理解と、実際に動いている挙動が違います。"
                    "どちらを正とするかはあなたの判断です。"
                ),
                provenance="runtime_observation",
                dedupe_key="runtime_mismatch",
                subject_key="runtime_alignment",
                snapshot_id=snapshot_id,
                revision_id=revision_id,
                runtime_window_seconds=RUNTIME_WINDOW_SECONDS,
                first_seen=oldest,
                last_updated=newest,
                target=Target(
                    route="/interview",
                    label="突き合わせ結果を開く",
                    params={"session": str(inputs.session_id)} if inputs.session_id else {},
                    anchor="review-queue-panel",
                ),
                occurrence_count=len(mismatches),
            )
        )

    unobserved = [
        r for r in inputs.runtime_checks if r.get("runtime_check") in ("unobserved", "stale")
    ]
    if unobserved:
        newest = max((r.get("updated_at") or 0) for r in unobserved) or None
        out.append(
            Finding(
                kind="runtime_unobserved",
                severity="informative",
                summary=f"実行時に確認できていない理解が {len(unobserved)} 件あります。",
                decision_impact=(
                    "正しいとも誤りとも言えません。観測範囲を広げると判断材料が増えます。"
                ),
                provenance="runtime_observation",
                dedupe_key="runtime_unobserved",
                subject_key="runtime_alignment",
                snapshot_id=snapshot_id,
                revision_id=revision_id,
                runtime_window_seconds=RUNTIME_WINDOW_SECONDS,
                first_seen=newest,
                last_updated=newest,
                target=Target(route="/components", label="Components を開く"),
                occurrence_count=len(unobserved),
            )
        )

    # 7. Reception stopped. Only meaningful once something HAS been received --
    #    `never_received` is the setup state, not a regression, and is handled
    #    by the next action instead.
    if inputs.connectivity_freshness in ("delayed", "stale"):
        onset = freshness_onset(
            inputs.connectivity_freshness,
            last_real_trace_at=inputs.last_real_trace_at,
            delayed_after_seconds=inputs.delayed_after_seconds,
            stale_after_seconds=inputs.stale_after_seconds,
        )
        out.append(
            Finding(
                kind="connectivity_lost",
                severity="material_change",
                summary=(
                    "Trace の受信が止まっています。"
                    if inputs.connectivity_freshness == "stale"
                    else "Trace の受信が滞っています。"
                ),
                decision_impact=(
                    "実行時の観測が更新されていないため、runtime を根拠にした判断は"
                    "最後に受信した時点のものです。"
                ),
                provenance="runtime_observation",
                dedupe_key="connectivity_lost",
                subject_key="connectivity",
                runtime_window_seconds=RUNTIME_WINDOW_SECONDS,
                # The finding began when the threshold was CROSSED, not when
                # the last trace arrived. Using the trace time made a system
                # that went quiet after the developer's last confirmation read
                # 「継続」 whenever its final trace predated that confirmation
                # -- i.e. the newest problems looked like the oldest ones.
                first_seen=onset,
                last_updated=onset,
                target=Target(route="/connect-sdk", label="SDK 接続を確認する"),
            )
        )

    # 8. The understanding is pinned to a commit the repository has moved past.
    if inputs.snapshot_stale:
        out.append(
            Finding(
                kind="snapshot_stale",
                severity="material_change",
                summary="この理解は、最新ではない snapshot に固定されています。",
                decision_impact=(
                    "リポジトリはすでに先に進んでいます。現在のコードに対する判断としては"
                    "古い可能性があります。"
                ),
                provenance="implementation_fact",
                dedupe_key="snapshot_stale",
                subject_key="snapshot",
                snapshot_id=snapshot_id,
                first_seen=inputs.latest_ready_snapshot_at,
                last_updated=inputs.latest_ready_snapshot_at,
                target=Target(route="/repository", label="Repository を開く"),
            )
        )

    # 9. A completed evaluation with no recorded human decision. This is a
    #    pending HUMAN gate, so it is `human_decision_required` -- the Overview
    #    surfaces it and never records the decision itself.
    if inputs.undecided_experiment_count > 0:
        params = (
            {"experiment": str(inputs.undecided_experiment_id)}
            if inputs.undecided_experiment_id
            else {}
        )
        out.append(
            Finding(
                kind="evaluation_decision_pending",
                severity="human_decision_required",
                summary=f"評価が完了し、採否が未記録の候補が {inputs.undecided_experiment_count} 件あります。",
                decision_impact=(
                    "比較結果は出ています。採用・不採用・追加データ必要のいずれかを"
                    "記録するまで、この改善サイクルは閉じません。"
                ),
                provenance="developer_decision",
                dedupe_key="evaluation_decision_pending",
                subject_key="evaluation",
                first_seen=inputs.undecided_experiment_at,
                last_updated=inputs.undecided_experiment_at,
                target=Target(route="/experiments", label="Experiments を開く", params=params),
                occurrence_count=inputs.undecided_experiment_count,
            )
        )
    elif inputs.completed_variant_count > 0:
        out.append(
            Finding(
                kind="improvement_candidate_ready",
                severity="informative",
                summary=f"隔離環境で比較済みの候補が {inputs.completed_variant_count} 件あります。",
                decision_impact="採否を記録すると、公開または次のサイクルへ進めます。",
                provenance="implementation_fact",
                dedupe_key="improvement_candidate_ready",
                subject_key="evaluation",
                first_seen=inputs.completed_variant_at,
                last_updated=inputs.completed_variant_at,
                target=Target(route="/candidate-studio", label="AI Candidate Studio を開く"),
                occurrence_count=inputs.completed_variant_count,
            )
        )

    return out


def _brief_claims_by_section(brief: Optional[Any]) -> List[Tuple[str, Any]]:
    """``(section, claim)`` for every Brief claim.

    The section keys are `understanding_brief.BRIEF_SECTIONS`' first elements,
    which is exactly what `UnderstandingChange.section` reports -- so a change
    and its claim are matched on the same vocabulary rather than on two
    hand-written lists that could drift.
    """
    if brief is None:
        return []
    out: List[Tuple[str, Any]] = []
    for section, _kind in understanding_brief.BRIEF_SECTIONS:
        if section == "vision":
            vision = getattr(brief, "vision", None)
            if vision is not None:
                out.append((section, vision))
            continue
        for claim in getattr(brief, section, []) or []:
            out.append((section, claim))
    return out


def _brief_claims(brief: Optional[Any]) -> List[Any]:
    if brief is None:
        return []
    claims: List[Any] = []
    if getattr(brief, "vision", None) is not None:
        claims.append(brief.vision)
    claims.extend(getattr(brief, "system_purpose", []) or [])
    claims.extend(getattr(brief, "core_capabilities", []) or [])
    return claims


def _claim_provenance(claim: Any) -> str:
    """A claim's provenance, carried through unchanged.

    `OverviewFindingProvenance` is a strict SUPERSET of the Brief's vocabulary
    precisely so this is a pass-through. It used to fall back to
    `ai_hypothesis` for anything it did not recognise, and since the Overview
    set was missing `developer_intent`, a Vision the developer had written and
    confirmed was displayed as the AI's guess -- the one confusion #381/#382
    exist to prevent.

    An unrecognised value now means the two vocabularies have drifted, which
    is a contract bug rather than a claim about the system, so it reports
    `mixed` (「複数の出所」) rather than naming a source that may be wrong.
    `tests/test_interview_type_parity.py` is what keeps this unreachable.
    """
    value = getattr(claim, "provenance", None)
    if value in FINDING_PROVENANCES:
        return value
    return "mixed"


def aggregate_provenance(values: Sequence[str]) -> str:
    """One provenance for a finding aggregated from several claims.

    Finite and explicit (#382): one distinct source is that source, several
    are `mixed`. Aggregation never picks a winner, because picking one implies
    the others agreed with it.

    An empty input means every changed claim was REMOVED, so none of them
    exists in the current understanding to carry a provenance. A removal is
    only observable by comparing two revisions the reviewer produced from the
    code, so `implementation_fact` is the source of that observation -- stated
    as a rule here rather than left as a silent default.
    """
    distinct = {value for value in values if value in FINDING_PROVENANCES}
    if not distinct:
        return "implementation_fact"
    if len(distinct) == 1:
        return next(iter(distinct))
    return "mixed"


def freshness_onset(
    freshness: str,
    *,
    last_real_trace_at: Optional[float],
    delayed_after_seconds: float,
    stale_after_seconds: float,
) -> Optional[float]:
    """When the current reception state BEGAN.

    `classify_connectivity_freshness` compares the age of the newest workload
    trace against two thresholds, so the moment a system became `delayed` or
    `stale` is exactly the moment that threshold was crossed -- the last trace
    plus the threshold, not the last trace itself. The two differ by up to a
    day at the default settings, which is more than enough to make a brand-new
    outage report itself as pre-existing.
    """
    if last_real_trace_at is None:
        return None
    if freshness == "stale":
        return last_real_trace_at + stale_after_seconds
    if freshness == "delayed":
        return last_real_trace_at + delayed_after_seconds
    return last_real_trace_at


def classify_status(
    first_seen: Optional[float], baseline_at: Optional[float]
) -> str:
    """`new` / `ongoing` / `not_compared`, first match.

    The baseline is the developer's own 理解の確認 (`understanding_confirmed_at`)
    -- a persisted human decision, never an implicit page view (#382). Without
    one, newness is not knowable and saying 「継続」 would be a guess, so the
    third value exists rather than a default.
    """
    if baseline_at is None:
        return "not_compared"
    if first_seen is None:
        return "ongoing"
    return "new" if first_seen > baseline_at else "ongoing"


def select_findings(
    findings: Sequence[Finding], *, baseline_at: Optional[float]
) -> Tuple[List[Finding], List[str]]:
    """Deduplicate and rank. Returns ``(ordered_findings, statuses)``.

    Two collapses, in this order:

    1. **Same root cause** (`dedupe_key`) -- the higher-priority representative
       survives and its occurrence count is kept.
    2. **Same subject across kinds** (`subject_key`) -- a claim that is both
       conflicting and unconfirmed reports the blocking fact only. Without
       this, one problem could occupy two of the three slots.

    The final order is `severity → status → kind → last_updated (newest
    first) → id`. Every gate is finite and every tie-break is total, so the
    same facts always produce the same three findings.
    """
    by_cause: Dict[str, Finding] = {}
    for finding in findings:
        existing = by_cause.get(finding.dedupe_key)
        if existing is None or _rank(finding, baseline_at) < _rank(existing, baseline_at):
            by_cause[finding.dedupe_key] = finding

    by_subject: Dict[str, Finding] = {}
    loose: List[Finding] = []
    for finding in by_cause.values():
        if not finding.subject_key:
            loose.append(finding)
            continue
        existing = by_subject.get(finding.subject_key)
        if existing is None or _rank(finding, baseline_at) < _rank(existing, baseline_at):
            by_subject[finding.subject_key] = finding

    ordered = sorted(
        list(by_subject.values()) + loose, key=lambda f: _rank(f, baseline_at)
    )
    return ordered, [classify_status(f.first_seen, baseline_at) for f in ordered]


def _rank(finding: Finding, baseline_at: Optional[float]) -> tuple:
    status = classify_status(finding.first_seen, baseline_at)
    return (
        _SEVERITY_RANK.get(finding.severity, len(_SEVERITY_RANK)),
        _STATUS_RANK.get(status, len(_STATUS_RANK)),
        _KIND_RANK.get(finding.kind, len(_KIND_RANK)),
        -(finding.last_updated or 0.0),
        finding.id,
    )


def findings_state(
    findings: Sequence[Finding], *, baseline_at: Optional[float]
) -> str:
    """`has_findings` / `no_findings` / `not_compared`, first match.

    「重要な新規findingなし」 and 「比較基準なし」 are different answers to
    different questions and must never render the same way (#382), so the
    empty case splits on whether a baseline exists at all.
    """
    if findings:
        return "has_findings"
    if baseline_at is None:
        return "not_compared"
    return "no_findings"


# --- §2. The single next action ----------------------------------------------


@dataclass(frozen=True)
class OverviewAction:
    key: str
    label: str
    reason: str
    completion_condition: str
    value: str
    target: Target
    rule_row: int
    source_state_ids: Tuple[str, ...] = ()
    source_finding_ids: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()


@dataclass(frozen=True)
class NextActionFacts:
    """Persisted facts the rule table reads. Every field defaults to the
    conservative ("earlier in the flow") value, so a fact that could not be
    determined never advances the developer past a step they have not taken --
    the same discipline `UserPhaseFacts` uses."""

    repository_configured: bool = False
    latest_snapshot_status: Optional[str] = None
    ready_snapshot_exists: bool = False
    #: `understanding_brief`'s verdict, reused verbatim. Only meaningful when
    #: `brief_available` -- see the availability flags below.
    readiness_state: str = "not_built"
    #: `interview_workflow`'s canonical state, reused verbatim.
    workflow_state: str = "W0-A"
    interview_session_id: Optional[int] = None
    connectivity_state: str = "no_signal"
    connectivity_freshness: str = "never_received"
    undecided_experiment: Optional["PendingExperiment"] = None
    undecided_experiment_count: int = 0
    pending_publish: Optional["PendingPublish"] = None
    pending_instrumentation_publish: Optional["PendingPublish"] = None
    completed_variant_run_exists: bool = False
    decided_experiment_exists: bool = False

    # --- Availability of the canonical facts above --------------------------
    #
    # These exist because a fact that could not be READ is not the same fact as
    # a step that has not been TAKEN, and the defaults above cannot express the
    # difference. The first version substituted `not_built` for an unreadable
    # Brief and `no_signal` / `never_received` for unreadable Runtime, so a
    # failed section produced a confident, wrong CTA: 「システムを理解する」 for
    # a system that was already understood, 「SDK を接続する」 for one that had
    # been receiving traces for weeks. That is the same one-word-two-facts
    # defect #366 spent an entire epic removing, and it is worse here because
    # the wrong word is a call to action.
    #
    # An unavailable fact never falls back to a value; it removes every rule
    # that reads it (`decide_next_action` fails closed).
    brief_available: bool = True
    runtime_available: bool = True
    workflow_available: bool = True
    #: The remaining fact groups, each loaded by its own guarded loader. A
    #: failing SQL statement must not become `0` / `None` / `False`: an
    #: unreadable experiment table is not "no experiments", and a failing
    #: publish query is not "nothing to publish".
    repository_available: bool = True
    experiments_available: bool = True
    variants_available: bool = True
    publish_available: bool = True
    instrumentation_publish_available: bool = True
    decisions_available: bool = True
    snapshots_available: bool = True


@dataclass(frozen=True)
class PendingExperiment:
    """A completed evaluation with no recorded human decision.

    Carries the id so the CTA can deep-link to the exact row. A count alone
    sends the developer to a list and makes them find it again.
    """

    experiment_id: int
    count: int = 1
    completed_at: Optional[float] = None


@dataclass(frozen=True)
class PendingPublish:
    """One exact publish artifact and its semantic lineage."""

    patch_id: int
    count: int = 1
    applied_at: Optional[float] = None
    experiment_id: Optional[int] = None
    variant_id: Optional[int] = None


def decide_next_action(
    facts: NextActionFacts,
) -> Tuple[Optional[OverviewAction], str, str]:
    """The Overview's first-match rule table. Returns
    ``(action, action_state, message)``.

    Exactly zero or one action, always. The order below is the contract: an
    earlier row is a step whose absence makes every later row meaningless, so
    a system with no repository is never told to record an evaluation
    decision.

    `waiting` is not the absence of a rule -- it is the rule for "the system
    is working and there is nothing for you to press". It carries no action on
    purpose (#383: 実行不能な操作をdisabledで常設しない).

    `unavailable` is the other no-action state and is NOT the same thing: the
    system is not working on anything, we simply could not read the facts a
    rule needs. Rows are skipped rather than evaluated against a default, and
    if a skipped row could have been the answer the whole table returns
    `unavailable` (#383: API部分失敗時は推測でCTAを出さず、安全なdegraded状態に
    する).
    """
    interview_params = (
        {"session": str(facts.interview_session_id)}
        if facts.interview_session_id
        else {}
    )
    unavailable = (
        None,
        "unavailable",
        "現在の状態を判定できなかったため、次の操作を提示できません。推測での案内は行いません。",
    )

    # Rows 1-2 read the repository configuration and the snapshot status. With
    # those unreadable there is no first row to answer with, and every later
    # row presupposes them.
    if not facts.repository_available:
        return unavailable

    # 1: no repository. Nothing downstream can be evaluated without one.
    if not facts.repository_configured:
        return (
            OverviewAction(
                key="prepare_repository",
                label="Repository を設定する",
                reason="解析対象のリポジトリがまだ登録されていません。",
                completion_condition="リポジトリを登録し、snapshot が 1 件作成されること。",
                value="コードを読み取れるようになり、システム理解の構築に進めます。",
                target=Target(route="/repository", label="Repository を開く"),
                rule_row=1,
                source_state_ids=("repository.configuration.missing",),
            ),
            "available",
            "",
        )

    # An unreadable snapshot projection is not evidence that no ready
    # snapshot exists.  Once row 1 has been handled, every remaining rule
    # presupposes snapshot state and must fail closed.
    if not facts.snapshots_available:
        return unavailable

    # 2: a snapshot exists but is not usable yet. Same action key, different
    #    reason -- 「構築中」 and 「失敗」 are the same next operation for the
    #    developer (go look at Repository) with different explanations.
    if not facts.ready_snapshot_exists:
        status = facts.latest_snapshot_status
        if status in ("indexing", "pending", "running"):
            return (
                None,
                "waiting",
                "snapshot を作成しています。完了すると理解の構築に進めます。",
            )
        reason = (
            "snapshot の作成に失敗しています。"
            if status == "failed"
            else "解析できる snapshot がまだありません。"
        )
        return (
            OverviewAction(
                key="prepare_repository",
                label="Snapshot を作り直す",
                reason=reason,
                completion_condition="status が ready の snapshot が 1 件できること。",
                value="コードの断面が固定され、根拠つきの理解を構築できます。",
                target=Target(route="/repository", label="Repository を開く"),
                rule_row=2,
                source_state_ids=("snapshot.missing",),
            ),
            "available",
            "",
        )

    # Rows 3-7 all read the Brief's readiness verdict or the interview
    # workflow state. If either could not be read, none of them may be
    # evaluated -- and because one of them could have been the answer, the
    # table cannot fall through to a later row either.
    if not facts.brief_available or not facts.workflow_available:
        return unavailable

    # 3: the system is building or updating the understanding.
    if facts.readiness_state == "building" or facts.workflow_state == "W1":
        return (None, "waiting", "システムが理解を作成・更新しています。")

    # 4: the understanding cannot be trusted as it stands.
    if facts.readiness_state == "blocked":
        return (
            OverviewAction(
                key="resolve_understanding_blocker",
                label="理解の問題を解消する",
                reason="矛盾・根拠不足・処理の失敗により、現在の理解を判断材料にできません。",
                completion_condition="矛盾が解消し、Decision Readiness が「進行不可」でなくなること。",
                value="システム理解を、以降のすべての判断の前提として使えるようになります。",
                target=Target(
                    route="/interview",
                    label="システム理解を開く",
                    params=interview_params,
                    anchor="understanding-brief",
                ),
                rule_row=4,
                source_state_ids=("understanding.readiness.blocked",),
            ),
            "available",
            "",
        )

    # 5: nothing has been built yet.
    if facts.readiness_state == "not_built":
        return (
            OverviewAction(
                key="build_understanding",
                label="システムを理解する",
                reason="このシステムが何のためにあるかを、まだ AI が読み取っていません。",
                completion_condition="Vision・System Purpose・主要機能が根拠つきで提示されること。",
                value="何を計測すべきかを、勘ではなく理解に基づいて決められます。",
                target=Target(
                    route="/interview", label="システム理解を開く", params=interview_params
                ),
                rule_row=5,
                source_state_ids=("understanding.readiness.not_built",),
            ),
            "available",
            "",
        )

    # 6: unanswered questions the AI cannot resolve on its own. Placed ABOVE
    #    the confirmation row because `W3` is exactly the state in which the
    #    understanding is not yet confirmable.
    if facts.workflow_state == "W3":
        return (
            OverviewAction(
                key="answer_interview_questions",
                label="AI の質問に答える",
                reason="コードだけでは決められない点が残っており、あなたの回答を待っています。",
                completion_condition="必須の質問がすべて回答済み・引き継ぎ済み・保留のいずれかになること。",
                value="回答が理解に反映され、確認まで進めます。",
                target=Target(
                    route="/interview",
                    label="質問に答える",
                    params=interview_params,
                    anchor="work-surface-W3",
                ),
                rule_row=6,
                source_state_ids=("interview.workflow.W3",),
            ),
            "available",
            "",
        )

    # 7: the understanding is judgeable but not settled by a human.
    if facts.readiness_state in ("needs_confirmation", "recheck_required"):
        recheck = facts.readiness_state == "recheck_required"
        return (
            OverviewAction(
                key="confirm_understanding",
                label="理解を確認する",
                reason=(
                    "前回の確認後に主要な理解が変わりました。"
                    if recheck
                    else "AI の読み取りは出ていますが、あなたの確認がまだです。"
                ),
                completion_condition="System Purpose と主要機能をあなたが確認済みにすること。",
                value="以降の計測・改善判断が、確認済みの前提の上で行えるようになります。",
                target=Target(
                    route="/interview",
                    label="理解を確認する",
                    params=interview_params,
                    anchor="understanding-brief",
                ),
                rule_row=7,
                source_state_ids=(f"understanding.readiness.{facts.readiness_state}",),
            ),
            "available",
            "",
        )

    # Rows 8-10 read the connectivity axes. Same rule: an unreadable Runtime
    # section must not become 「まだ接続していません」. Rows 1-7 above have
    # already been evaluated against facts that WERE readable, so reaching
    # here means none of them matched -- and a later row cannot be promoted
    # over a skipped one.
    if not facts.runtime_available:
        return unavailable

    # 8: understanding settled, nothing has ever arrived from the SDK.
    if facts.connectivity_state == "no_signal":
        return (
            OverviewAction(
                key="connect_sdk",
                label="SDK を接続する",
                reason="確認済みの理解はありますが、実行時のデータがまだ 1 件も届いていません。",
                completion_condition="対象システムから Trace を 1 件受信すること。",
                value="理解が実際の挙動と合っているかを、観測で確かめられます。",
                target=Target(route="/connect-sdk", label="接続手順を開く"),
                rule_row=8,
                source_state_ids=("runtime.connectivity.no_signal",),
            ),
            "available",
            "",
        )

    # 9: the transport works (a smoke check arrived) but the workload never
    #    has. A different problem from row 8 and a different next step.
    if facts.connectivity_freshness == "never_received":
        return (
            OverviewAction(
                key="start_observation",
                label="観測を開始する",
                reason="疎通は確認できていますが、対象の処理からの Trace がまだ届いていません。",
                completion_condition="smoke check 以外の Trace を受信すること。",
                value="実際の入出力・エラー・所要時間を、理解と突き合わせられます。",
                target=Target(route="/components", label="Components を開く"),
                rule_row=9,
                source_state_ids=("runtime.connectivity.smoke_only",),
            ),
            "available",
            "",
        )

    # 10: reception regressed. A pending human decision below would be judged
    #     against observations that stopped updating, so this comes first.
    if facts.connectivity_freshness in ("delayed", "stale"):
        return (
            OverviewAction(
                key="restore_observation",
                label="観測を復旧する",
                reason="Trace の受信が止まっている、または滞っています。",
                completion_condition="直近の受信間隔がしきい値の範囲内に戻ること。",
                value="現在のシステムに対する判断材料が、また最新になります。",
                target=Target(route="/connect-sdk", label="接続状態を確認する"),
                rule_row=10,
                source_state_ids=(f"runtime.freshness.{facts.connectivity_freshness}",),
            ),
            "available",
            "",
        )

    # Rows 11-13 read the experiment / publish / variant tables.
    if not facts.experiments_available or not facts.publish_available:
        return unavailable

    # 11: an evaluation finished and is waiting on the human gate. The CTA
    #     carries the specific Experiment, so the developer lands on the row
    #     rather than on a list they have to search.
    if facts.undecided_experiment is not None:
        pending = facts.undecided_experiment
        return (
            OverviewAction(
                key="record_experiment_decision",
                label="採否を記録する",
                reason=f"評価が完了した候補が {pending.count} 件、未決定のまま残っています。",
                completion_condition="採用・不採用・追加データ必要のいずれかを記録すること。",
                value="採用した場合は公開へ、そうでない場合は次の候補へ進めます。",
                target=Target(
                    route="/experiments",
                    label="Experiments を開く",
                    params={"experiment": str(pending.experiment_id)},
                ),
                rule_row=11,
                source_state_ids=("proposal.experiments.undecided",),
            ),
            "available",
            "",
        )

    # 12: the exact variant the developer adopted has a persisted publish
    # artifact and no completed job for that same artifact.  This is the
    # improvement loop's Publish step; no System-wide existence or timestamp
    # inference participates.
    if facts.pending_publish is not None:
        pending_publish = facts.pending_publish
        return (
            OverviewAction(
                key="publish_improvement",
                label="採用した改善を公開する",
                reason=(
                    f"採用済みで未公開の改善変更が {pending_publish.count} 件あります。"
                ),
                completion_condition=(
                    "この改善変更の Pull Request が作成されること（公開承認と merge はあなたが行います）。"
                ),
                value=(
                    "評価して採用した変更を、レビュー可能な形で共有できます。"
                ),
                target=Target(
                    route="/github",
                    label="GitHub 連携を開く",
                    params={
                        "patch": str(pending_publish.patch_id),
                        "experiment": str(pending_publish.experiment_id),
                    },
                ),
                rule_row=12,
                source_state_ids=("publish.improvement_artifacts.unpublished",),
            ),
            "available",
            "",
        )

    if not facts.variants_available or not facts.decisions_available:
        return unavailable

    # 13: observing normally, nothing evaluated yet.  An unrelated pending
    # instrumentation patch must not hide this improvement-loop step.
    if not facts.completed_variant_run_exists and not facts.decided_experiment_exists:
        return (
            OverviewAction(
                key="create_candidate",
                label="改善候補を作る",
                reason="観測は届いており、まだ評価した候補がありません。",
                completion_condition="候補を 1 件生成し、隔離環境で baseline と比較すること。",
                value="改善できるかどうかを、対象リポジトリを変更せずに確かめられます。",
                target=Target(route="/candidate-studio", label="AI Candidate Studio を開く"),
                rule_row=13,
                source_state_ids=("proposal.candidates.none",),
            ),
            "available",
            "",
        )

    # 14: instrumentation publishing is a separate maintenance action.  Its
    # wording and identity never claim that an adopted improvement was
    # published, and it comes after the improvement-cycle decision rows.
    if not facts.instrumentation_publish_available:
        return unavailable

    if facts.pending_instrumentation_publish is not None:
        instrumentation = facts.pending_instrumentation_publish
        return (
            OverviewAction(
                key="publish_instrumentation",
                label="計測の変更を公開する",
                reason=f"適用済みで未公開の計測 patch が {instrumentation.count} 件あります。",
                completion_condition="この計測 patch の Pull Request が作成されること。",
                value="観測に使っている計測コードをレビュー可能な形で共有できます。",
                target=Target(
                    route="/github",
                    label="GitHub 連携を開く",
                    params={"patch": str(instrumentation.patch_id)},
                ),
                rule_row=14,
                source_state_ids=("publish.instrumentation_patches.unpublished",),
            ),
            "available",
            "",
        )

    # 15: one full cycle is behind us. Cyclical, so this is an offer to start
    #     the next one -- not a claim that there is nothing left to do.
    return (
        OverviewAction(
            key="start_next_cycle",
            label="次の改善サイクルを始める",
            reason="現在のサイクルで判断待ちの項目はありません。",
            completion_condition="新しい観測または新しい候補から、次の判断対象が生まれること。",
            value="変化を調べ直し、次の改善対象を選べます。",
            target=Target(route="/components", label="Components を開く"),
            rule_row=15,
        ),
        "complete",
        "",
    )


# --- §3. The improvement loop -------------------------------------------------


@dataclass(frozen=True)
class OverviewLoopStageView:
    stage: str
    label: str
    status: str
    meaning: str
    next_milestone: str = ""
    complete: bool = False


def build_loop_stages(
    user_phase: str, phases: Sequence[Any]
) -> List[OverviewLoopStageView]:
    """Render `derive_user_phase`'s output as the six-stage rail.

    `status` is derived from the phase's OWN completion flag and the current
    phase pointer -- never from which pages the developer has visited (#383:
    単なるページ訪問ではなく永続的な到達事実から表示する).
    """
    completion = {p.phase: bool(p.complete) for p in phases}
    out: List[OverviewLoopStageView] = []
    for stage in LOOP_STAGES:
        complete = completion.get(stage, False)
        if stage == user_phase:
            status: str = "current"
        elif complete:
            status = "reached"
        else:
            status = "future"
        out.append(
            OverviewLoopStageView(
                stage=stage,
                label=LOOP_STAGE_LABELS[stage],
                status=status,
                meaning=LOOP_STAGE_MEANINGS[stage],
                next_milestone=(
                    LOOP_STAGE_NEXT_MILESTONE[stage] if status == "current" else ""
                ),
                complete=complete,
            )
        )
    return out


# --- §4. Runtime health -------------------------------------------------------


@dataclass
class RuntimeHealth:
    state: str
    freshness: str
    freshness_label: str
    transport_freshness: str
    evaluated_at: float
    delayed_after_seconds: float
    stale_after_seconds: float
    last_real_trace_at: Optional[float] = None
    seconds_since_last_trace: Optional[float] = None
    last_trace_at: Optional[float] = None
    seconds_since_last_any_trace: Optional[float] = None
    real_trace_count_5m: int = 0
    real_trace_count_1h: int = 0
    real_trace_count_24h: int = 0
    component_count: int = 0
    total_trace_count: int = 0
    mode_counts: Dict[str, int] = field(default_factory=dict)
    window_seconds: float = RUNTIME_WINDOW_SECONDS
    error_count: int = 0
    runtime_mismatch_count: int = 0
    replayable_count: int = 0
    partial_count: int = 0
    unreplayable_count: int = 0
    not_captured_count: int = 0
    observed_component_count: int = 0
    known_component_count: int = 0
    core_capability_count: int = 0
    capability_coverage_state: str = "not_computed"
    observed_capability_count: Optional[int] = None
    unmapped_component_count: Optional[int] = None


# --- §5. Assembly -------------------------------------------------------------


@dataclass
class OverviewResult:
    system_id: int
    generated_at: float
    interview_session_id: Optional[int] = None
    brief: Optional[Any] = None
    snapshot_id: Optional[int] = None
    snapshot_commit_sha: Optional[str] = None
    latest_ready_snapshot_id: Optional[int] = None
    snapshot_freshness: str = "unavailable"
    understanding_revision_id: Optional[int] = None
    understanding_confirmed_at: Optional[float] = None
    findings: List[Finding] = field(default_factory=list)
    finding_statuses: List[str] = field(default_factory=list)
    findings_initial_count: int = 0
    findings_state: str = "not_compared"
    findings_baseline_state: str = "unavailable"
    findings_baseline_label: str = ""
    findings_baseline_at: Optional[float] = None
    next_action: Optional[OverviewAction] = None
    next_action_state: str = "unavailable"
    next_action_message: str = ""
    loop_stages: List[OverviewLoopStageView] = field(default_factory=list)
    user_phase: str = "setup"
    runtime: Optional[RuntimeHealth] = None
    degraded_sections: List[str] = field(default_factory=list)
    degraded_detail: Dict[str, str] = field(default_factory=dict)


# --- Guarded fact loaders ----------------------------------------------------
#
# Each next-action fact group is loaded by its own function so `build_overview`
# can catch a failure per group and report it, rather than letting one bad
# statement turn the whole response into a 500. Before this split, a failing
# publish query took down a page whose Brief, Runtime health, findings and loop
# had all loaded fine.
#
# None of these ever returns a "safe default" on failure -- they raise, and the
# caller records the group as unavailable. A `0` returned from an exception is
# the same lie as `not_built` for an unreadable Brief.


def load_repository_fact(conn, system_id: int) -> bool:
    """Is a repository configured for this System (same truthiness rule as
    `system_state._repository_state_items`: an empty `repo_path` is not
    configured)."""
    config = state_facts.get_repository_config(conn, system_id)
    return config is not None and bool(config["repo_path"])


def load_pending_experiments(conn, system_id: int) -> List[Dict[str, Any]]:
    """Completed experiments with no recorded human decision, newest first.

    The ordering is total (`completed_at DESC, id DESC`) so the CTA deep-links
    to the same experiment on every request.
    """
    return [
        dict(row)
        for row in conn.execute(
            """SELECT id, completed_at FROM experiments
                WHERE system_id = ? AND status = 'completed'
                  AND human_decision = 'undecided'
                ORDER BY completed_at DESC, id DESC""",
            (system_id,),
        ).fetchall()
    ]


def load_variant_facts(conn, system_id: int) -> Tuple[int, Optional[float]]:
    """``(completed non-baseline replay variant count, newest completed_at)``."""
    row = conn.execute(
        """SELECT COUNT(*) AS n, MAX(completed_at) AS last_at FROM replay_variants
            WHERE system_id = ? AND status = 'completed' AND is_baseline = 0""",
        (system_id,),
    ).fetchone()
    if row is None:
        return 0, None
    return (row["n"] or 0), row["last_at"]


def load_decision_facts(conn, system_id: int) -> bool:
    """Has any experiment in this System recorded a human decision."""
    return state_facts.has_decided_experiment(conn, system_id)


def resolve_pending_publish(conn, system_id: int) -> Optional[PendingPublish]:
    """An adopted improvement artifact with no completed job in its lineage."""
    rows = conn.execute(
        """SELECT a.id, a.patch_id, a.experiment_id, a.variant_id, a.created_at
             FROM improvement_publish_artifacts a
             JOIN experiments e
               ON e.id = a.experiment_id AND e.system_id = a.system_id
             JOIN experiment_variants v
               ON v.id = a.variant_id AND v.experiment_id = e.id
            WHERE a.system_id = ?
              AND a.status = 'ready'
              AND e.human_decision = 'adopted'
              AND e.human_decision_variant_key = v.variant_key
              AND NOT EXISTS (
                  SELECT 1 FROM publish_jobs j
                   WHERE j.patch_id = a.patch_id
                     AND j.system_id = a.system_id
                     AND j.status = 'completed'
              )
            ORDER BY a.created_at DESC, a.id DESC""",
        (system_id,),
    ).fetchall()
    if not rows:
        return None
    return PendingPublish(
        patch_id=rows[0]["patch_id"],
        count=len(rows),
        applied_at=rows[0]["created_at"],
        experiment_id=rows[0]["experiment_id"],
        variant_id=rows[0]["variant_id"],
    )


def resolve_pending_instrumentation_publish(
    conn, system_id: int
) -> Optional[PendingPublish]:
    """An applied instrumentation patch with no completed job for it."""
    rows = conn.execute(
        """SELECT p.id AS id, p.applied_at AS applied_at
             FROM probe_patches p
            WHERE p.system_id = ?
              AND p.apply_status = 'applied'
              AND NOT EXISTS (
                  SELECT 1 FROM improvement_publish_artifacts a
                   WHERE a.patch_id = p.id AND a.system_id = p.system_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM publish_jobs j
                   WHERE j.patch_id = p.id
                     AND j.system_id = p.system_id
                     AND j.status = 'completed'
              )
            ORDER BY p.applied_at DESC, p.id DESC""",
        (system_id,),
    ).fetchall()
    if not rows:
        return None
    return PendingPublish(
        patch_id=rows[0]["id"], count=len(rows), applied_at=rows[0]["applied_at"]
    )


def _latest_interview_session_id(conn, system_id: int) -> Optional[int]:
    """The System's newest Interview session.

    The same rule the Interview screen auto-selects with (`ORDER BY id DESC`),
    so the Brief on the Overview and the Brief on the Interview screen are
    always about the same session -- and the Overview's deep links land on it.
    """
    row = conn.execute(
        "SELECT id FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    return row["id"] if row else None


def load_snapshot_commit(
    conn, system_id: int, snapshot_id: int
) -> Optional[str]:
    """Commit pinned by a System-scoped snapshot, or ``None`` if absent."""
    row = conn.execute(
        "SELECT commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
        (snapshot_id, system_id),
    ).fetchone()
    return row["commit_sha"] if row else None


def load_revision_created_at(conn, revision_id: int) -> Optional[float]:
    """Creation time of one understanding revision."""
    row = conn.execute(
        "SELECT created_at FROM understanding_revision WHERE id = ?",
        (revision_id,),
    ).fetchone()
    return row["created_at"] if row else None


def _runtime_checks(conn, system_id: int, session_id: Optional[int]) -> List[Dict[str, Any]]:
    """Alignment items carrying #290's persisted finite `runtime_check`.

    Read as stored. The Overview never re-runs a Runtime Reality Check and
    never recomputes the match verdict -- that is a reasoning-model decision
    owned by its own endpoint (Principle 6/7).
    """
    if session_id is None:
        return []
    rows = conn.execute(
        """SELECT id, runtime_check, current_claim, created_at, updated_at
             FROM alignment_item
            WHERE system_id = ? AND session_id = ? AND runtime_check IS NOT NULL
            ORDER BY id""",
        (system_id, session_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _runtime_health(
    conn,
    system_id: int,
    now: float,
    capability_count: int,
    runtime_mismatch_count: int,
) -> RuntimeHealth:
    facts = state_facts.get_connectivity_facts(
        conn, system_id, SMOKE_CHECK_COMPONENT_ID, now=now
    )
    delayed_after, stale_after = state_facts.get_freshness_thresholds(conn, system_id)
    state = state_facts.classify_connectivity_state(
        real_trace_count=facts.real_trace_count,
        smoke_trace_count=facts.smoke_trace_count,
    )
    freshness = state_facts.classify_connectivity_freshness(
        last_trace_at=facts.last_real_trace_at,
        now=now,
        delayed_after_seconds=delayed_after,
        stale_after_seconds=stale_after,
    )
    transport = state_facts.classify_connectivity_freshness(
        last_trace_at=facts.last_trace_at,
        now=now,
        delayed_after_seconds=delayed_after,
        stale_after_seconds=stale_after,
    )

    components = conn.execute(
        "SELECT mode, COUNT(*) AS n FROM components WHERE system_id = ? GROUP BY mode",
        (system_id,),
    ).fetchall()
    mode_counts = {row["mode"]: row["n"] for row in components}

    # Quality counts are measured over a BOUNDED window, unlike the cumulative
    # totals: "12 errors" means nothing without saying when. The window is
    # reported alongside so the Dashboard never has to assume one.
    window_start = now - RUNTIME_WINDOW_SECONDS
    quality = conn.execute(
        """SELECT
               SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END) AS errors
             FROM traces
            WHERE system_id = ? AND component_id != ? AND timestamp >= ?""",
        (system_id, SMOKE_CHECK_COMPONENT_ID, window_start),
    ).fetchone()
    replay_rows = conn.execute(
        """SELECT replayability FROM traces
            WHERE system_id = ? AND component_id != ? AND timestamp >= ?""",
        (system_id, SMOKE_CHECK_COMPONENT_ID, window_start),
    ).fetchall()
    counts = replay_readiness.count_replayability(replay_rows)

    observed = conn.execute(
        """SELECT COUNT(DISTINCT component_id) AS n FROM traces
            WHERE system_id = ? AND component_id != ? AND timestamp >= ?""",
        (system_id, SMOKE_CHECK_COMPONENT_ID, window_start),
    ).fetchone()

    return RuntimeHealth(
        state=state,
        freshness=freshness,
        freshness_label=FRESHNESS_LABELS[freshness],
        transport_freshness=transport,
        evaluated_at=now,
        delayed_after_seconds=delayed_after,
        stale_after_seconds=stale_after,
        last_real_trace_at=facts.last_real_trace_at,
        seconds_since_last_trace=(
            None if facts.last_real_trace_at is None else now - facts.last_real_trace_at
        ),
        last_trace_at=facts.last_trace_at,
        seconds_since_last_any_trace=(
            None if facts.last_trace_at is None else now - facts.last_trace_at
        ),
        real_trace_count_5m=facts.real_trace_count_5m,
        real_trace_count_1h=facts.real_trace_count_1h,
        real_trace_count_24h=facts.real_trace_count_24h,
        component_count=sum(mode_counts.values()),
        total_trace_count=facts.total_trace_count,
        mode_counts=mode_counts,
        window_seconds=RUNTIME_WINDOW_SECONDS,
        error_count=(quality["errors"] or 0) if quality else 0,
        runtime_mismatch_count=runtime_mismatch_count,
        replayable_count=counts.replayable,
        partial_count=counts.partial,
        unreplayable_count=counts.unreplayable,
        not_captured_count=counts.not_captured,
        observed_component_count=(observed["n"] or 0) if observed else 0,
        known_component_count=sum(mode_counts.values()),
        core_capability_count=capability_count,
        # Capability coverage is NOT derivable today: a trace carries a
        # `component_id` and nothing persists a component -> Core Capability
        # mapping. The first version divided one by the other anyway, which is
        # a ratio between two different entities and could exceed 100% as soon
        # as one Capability had two components. Saying 「算出できません」 is the
        # honest reading; approximating it is the #366 defect in a new place.
        capability_coverage_state="not_computed",
        observed_capability_count=None,
        unmapped_component_count=None,
    )


def build_overview(system_id: int, *, now: Optional[float] = None) -> OverviewResult:
    """Compose the whole Overview projection for one System.

    Section by section, each guarded independently: a section that cannot be
    derived is reported in `degraded_sections` and the rest still render
    (#384 -- 部分失敗で画面全体が空にならない). The guard is deliberately
    narrow: it degrades one section's DISPLAY, and never substitutes a guessed
    value for a fact the section failed to read.

    `build_system_state` opens its own connection and runs `git`, so it is
    called BEFORE this function opens one (CLAUDE.md: never hold a
    `get_conn()` connection across an external call).
    """
    now = time.time() if now is None else now
    result = OverviewResult(system_id=system_id, generated_at=now)

    assessment = None
    try:
        assessment = system_state.build_system_state(system_id)
        result.user_phase = assessment.user_phase
        result.loop_stages = build_loop_stages(assessment.user_phase, assessment.phases)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(result, "loop", exc)

    with get_conn() as conn:
        availability = {
            "session": True,
            "snapshots": True,
            "snapshot_commit": True,
            "revision": True,
            "runtime_checks": True,
            "repository": True,
            "experiments": True,
            "variants": True,
            "publish": True,
            "instrumentation_publish": True,
            "decisions": True,
        }

        session_id = None
        try:
            session_id = _latest_interview_session_id(conn, system_id)
        except Exception as exc:  # pragma: no cover - defensive
            availability["session"] = False
            _degrade(result, "brief", exc, detail_key="brief.session")
        result.interview_session_id = session_id

        latest_ready = None
        latest_snapshot_status = None
        try:
            latest_ready = state_facts.get_latest_ready_snapshot(conn, system_id)
            result.latest_ready_snapshot_id = latest_ready["id"] if latest_ready else None
            latest_snapshot = state_facts.get_latest_snapshot(conn, system_id)
            latest_snapshot_status = latest_snapshot["status"] if latest_snapshot else None
        except Exception as exc:  # pragma: no cover - defensive
            availability["snapshots"] = False
            result.latest_ready_snapshot_id = None
            _degrade(result, "brief", exc, detail_key="brief.snapshots")
        latest_ready_at = None
        if latest_ready is not None:
            latest_ready_at = latest_ready["completed_at"] or latest_ready["created_at"]

        brief = None
        if availability["session"]:
            try:
                brief = understanding_brief.build_understanding_brief(
                    conn, system_id, session_id, now=now
                )
                result.brief = brief
                result.snapshot_id = brief.snapshot_id
            except Exception as exc:  # pragma: no cover - defensive
                _degrade(result, "brief", exc)

        if result.snapshot_id is not None:
            try:
                result.snapshot_commit_sha = load_snapshot_commit(
                    conn, system_id, result.snapshot_id
                )
            except Exception as exc:  # pragma: no cover - defensive
                availability["snapshot_commit"] = False
                _degrade(result, "brief", exc, detail_key="brief.snapshot_commit")

        # Decided here, never by the Dashboard comparing two ids. Same rule as
        # #369: one definition of 「最新か」, on the server. `unavailable` is a
        # real third value -- with no pinned snapshot or no ready snapshot to
        # compare against, "behind" is not a fact we have.
        if (
            not availability["snapshots"]
            or result.snapshot_id is None
            or result.latest_ready_snapshot_id is None
        ):
            result.snapshot_freshness = "unavailable"
        elif result.snapshot_id == result.latest_ready_snapshot_id:
            result.snapshot_freshness = "current"
        else:
            result.snapshot_freshness = "stale"
        result.understanding_revision_id = getattr(brief, "revision_id", None)
        result.understanding_confirmed_at = getattr(brief, "confirmed_at", None)

        # The interview workflow's canonical engine is reused, but only its
        # PURE half. `evaluate_session_workflow` persists the workflow
        # checkpoint and can open a backward request -- progress facts that
        # belong to the Interview screen's own evaluation, not to somebody
        # glancing at the Overview. `gather_facts` + `evaluate_candidate_state`
        # give the same candidate state with no decision recorded.
        gathered = None
        workflow_state = "W0-A"
        try:
            from . import interview_workflow

            gathered = interview_workflow.gather_facts(conn, system_id, session_id)
            workflow_state, _rule_row = interview_workflow.evaluate_candidate_state(
                gathered.facts
            )
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(result, "next_action", exc)

        revision_created_at = None
        if brief is not None and brief.revision_id is not None:
            try:
                revision_created_at = load_revision_created_at(conn, brief.revision_id)
            except Exception as exc:  # pragma: no cover - defensive
                availability["revision"] = False
                _degrade(result, "findings", exc, detail_key="findings.revision")

        capability_count = len(getattr(brief, "core_capabilities", []) or [])
        runtime_checks: List[Dict[str, Any]] = []
        runtime_mismatch_count = 0
        if availability["session"]:
            try:
                runtime_checks = _runtime_checks(conn, system_id, session_id)
                runtime_mismatch_count = sum(
                    1 for row in runtime_checks if row.get("runtime_check") == "mismatch"
                )
            except Exception as exc:  # pragma: no cover - defensive
                availability["runtime_checks"] = False
                _degrade(result, "runtime", exc, detail_key="runtime.checks")
        else:
            availability["runtime_checks"] = False

        if availability["runtime_checks"]:
            try:
                result.runtime = _runtime_health(
                    conn, system_id, now, capability_count, runtime_mismatch_count
                )
            except Exception as exc:  # pragma: no cover - defensive
                _degrade(result, "runtime", exc)

        # Each fact group is loaded under its own guard. A failure records the
        # group as unavailable and leaves its value at the conservative
        # default -- which no rule is then allowed to read.
        undecided: List[Dict[str, Any]] = []
        pending_publish: Optional[PendingPublish] = None
        pending_instrumentation_publish: Optional[PendingPublish] = None
        variant_count, variant_at = 0, None
        repository_configured = False
        decided_experiment_exists = False
        for group, load in (
            ("repository", lambda: load_repository_fact(conn, system_id)),
            ("experiments", lambda: load_pending_experiments(conn, system_id)),
            ("publish", lambda: resolve_pending_publish(conn, system_id)),
            (
                "instrumentation_publish",
                lambda: resolve_pending_instrumentation_publish(conn, system_id),
            ),
            ("variants", lambda: load_variant_facts(conn, system_id)),
            ("decisions", lambda: load_decision_facts(conn, system_id)),
        ):
            try:
                loaded = load()
            except Exception as exc:  # pragma: no cover - defensive
                availability[group] = False
                _degrade(result, "next_action", exc, detail_key=f"next_action.{group}")
                continue
            if group == "repository":
                repository_configured = loaded
            elif group == "experiments":
                undecided = loaded
            elif group == "publish":
                pending_publish = loaded
            elif group == "instrumentation_publish":
                pending_instrumentation_publish = loaded
            elif group == "variants":
                variant_count, variant_at = loaded
            else:
                decided_experiment_exists = loaded

        try:
            if brief is None:
                # Without the Brief there is no baseline, so no finding could
                # be honestly labelled `new` or `ongoing` -- and every
                # Brief-derived finding is missing anyway. Reporting
                # `unavailable` is the whole answer; the Runtime section still
                # renders on its own.
                raise _BriefUnavailable()
            if not all(
                (
                    availability["session"],
                    availability["snapshots"],
                    availability["revision"],
                    availability["runtime_checks"],
                    availability["experiments"],
                    availability["variants"],
                    gathered is not None,
                    result.runtime is not None,
                )
            ):
                raise _FindingsUnavailable()
            result.findings, result.finding_statuses, result.findings_state = _findings(
                brief=brief,
                gathered=gathered,
                session_id=session_id,
                revision_created_at=revision_created_at,
                latest_ready_snapshot_at=latest_ready_at,
                runtime_checks=runtime_checks,
                runtime=result.runtime,
                undecided=undecided,
                completed_variant_count=variant_count,
                completed_variant_at=variant_at,
            )
            result.findings_initial_count = min(
                len(result.findings), INITIAL_FINDING_LIMIT
            )
        except _BriefUnavailable:
            result.findings = []
            result.finding_statuses = []
            result.findings_state = "unavailable"
            if "findings" not in result.degraded_sections:
                result.degraded_sections.append("findings")
            result.degraded_detail["findings"] = (
                "システム理解を取得できなかったため、変化を比較できませんでした。"
            )
        except _FindingsUnavailable:
            result.findings = []
            result.finding_statuses = []
            result.findings_state = "unavailable"
            if "findings" not in result.degraded_sections:
                result.degraded_sections.append("findings")
            result.degraded_detail.setdefault(
                "findings",
                "変化の判定に必要な情報を取得できなかったため、発見の有無を判定していません。",
            )
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(result, "findings", exc)
            result.findings_state = "unavailable"

        # Three states, not two. 「確認していない」 is a claim about the
        # developer; 「読めなかった」 is a claim about this request. Collapsing
        # them told a System whose Brief happened to fail that its developer
        # had never confirmed anything.
        if brief is None:
            result.findings_baseline_state = "unavailable"
            result.findings_baseline_at = None
            result.findings_baseline_label = (
                "比較の基準を取得できませんでした。確認済みかどうかは判定していません"
            )
        elif brief.confirmed_at is None:
            result.findings_baseline_state = "no_baseline"
            result.findings_baseline_at = None
            result.findings_baseline_label = (
                "まだ理解を確認していないため、比較の基準がありません"
            )
        else:
            result.findings_baseline_state = "has_baseline"
            result.findings_baseline_at = brief.confirmed_at
            result.findings_baseline_label = "前回あなたがシステム理解を確認した時点"

        # `decide_next_action` is ALWAYS called now. Availability travels with
        # the facts, so the rule table itself decides how far it can safely
        # evaluate -- there is no outer "skip everything" branch that could
        # disagree with it.
        action_facts = NextActionFacts(
            repository_configured=repository_configured,
            latest_snapshot_status=latest_snapshot_status,
            ready_snapshot_exists=result.latest_ready_snapshot_id is not None,
            readiness_state=(
                brief.readiness_state if brief is not None else "not_built"
            ),
            workflow_state=workflow_state,
            interview_session_id=session_id,
            connectivity_state=(
                result.runtime.state if result.runtime else "no_signal"
            ),
            connectivity_freshness=(
                result.runtime.freshness if result.runtime else "never_received"
            ),
            undecided_experiment=(
                PendingExperiment(
                    experiment_id=undecided[0]["id"],
                    count=len(undecided),
                    completed_at=undecided[0]["completed_at"],
                )
                if undecided
                else None
            ),
            undecided_experiment_count=len(undecided),
            pending_publish=pending_publish,
            pending_instrumentation_publish=pending_instrumentation_publish,
            completed_variant_run_exists=variant_count > 0,
            decided_experiment_exists=decided_experiment_exists,
            brief_available=brief is not None,
            runtime_available=result.runtime is not None,
            workflow_available=gathered is not None,
            repository_available=availability["repository"],
            experiments_available=availability["experiments"],
            variants_available=availability["variants"],
            publish_available=availability["publish"],
            instrumentation_publish_available=availability["instrumentation_publish"],
            decisions_available=availability["decisions"],
            snapshots_available=availability["snapshots"],
        )
        action, state, message = decide_next_action(action_facts)
        result.next_action = _attach_finding_sources(action, result.findings)
        result.next_action_state = state
        result.next_action_message = message
        if state == "unavailable" and "next_action" not in result.degraded_sections:
            # The rule table failed closed on a fact whose own loader
            # succeeded (Brief / Runtime / workflow), so record the section
            # here rather than leaving the Dashboard to infer it.
            result.degraded_sections.append("next_action")

    return result


def _attach_finding_sources(
    action: Optional[OverviewAction], findings: Sequence[Finding]
) -> Optional[OverviewAction]:
    """Link the action to the findings that explain it (#383: #382 の finding
    を理由として参照できる設計にする).

    The link is by the action's own `source_state_ids` prefix matched against
    a fixed map -- never by text similarity between an action label and a
    finding summary.
    """
    if action is None:
        return None
    kinds = _ACTION_FINDING_KINDS.get(action.key, ())
    if not kinds:
        return action
    ids = tuple(f.id for f in findings if f.kind in kinds)
    if not ids:
        return action
    return OverviewAction(
        key=action.key,
        label=action.label,
        reason=action.reason,
        completion_condition=action.completion_condition,
        value=action.value,
        target=action.target,
        rule_row=action.rule_row,
        source_state_ids=action.source_state_ids,
        source_finding_ids=ids,
        blockers=action.blockers,
    )


#: Which finding kinds explain which action. Fixed and hand-authored: an
#: action must never claim a finding as its reason because their wording
#: happened to overlap.
_ACTION_FINDING_KINDS: Dict[str, Tuple[str, ...]] = {
    "resolve_understanding_blocker": ("claim_conflict", "understanding_blocked"),
    "confirm_understanding": (
        "understanding_changed",
        "unconfirmed_core_claim",
        "capability_composition_stale",
    ),
    "restore_observation": ("connectivity_lost",),
    "record_experiment_decision": ("evaluation_decision_pending",),
    "create_candidate": ("runtime_mismatch", "improvement_candidate_ready"),
}


def _findings(
    *,
    brief,
    gathered,
    session_id,
    revision_created_at,
    latest_ready_snapshot_at,
    runtime_checks,
    runtime,
    undecided,
    completed_variant_count,
    completed_variant_at,
) -> Tuple[List[Finding], List[str], str]:
    blocking = ()
    snapshot_stale = False
    if gathered is not None:
        blocking = tuple(
            dict(row)
            for row in gathered.blocking_failures
            if row["process_kind"] in understanding_brief.BRIEF_AFFECTING_PROCESS_KINDS
        )
        snapshot_stale = gathered.snapshot_stale

    inputs = FindingInputs(
        brief=brief,
        revision_created_at=revision_created_at,
        blocking_failures=blocking,
        snapshot_stale=snapshot_stale,
        latest_ready_snapshot_at=latest_ready_snapshot_at,
        runtime_checks=tuple(runtime_checks),
        connectivity_freshness=(runtime.freshness if runtime else "never_received"),
        last_real_trace_at=(runtime.last_real_trace_at if runtime else None),
        delayed_after_seconds=(
            runtime.delayed_after_seconds
            if runtime
            else state_facts.DEFAULT_DELAYED_AFTER_SECONDS
        ),
        stale_after_seconds=(
            runtime.stale_after_seconds
            if runtime
            else state_facts.DEFAULT_STALE_AFTER_SECONDS
        ),
        undecided_experiment_count=len(undecided),
        undecided_experiment_at=(undecided[0]["completed_at"] if undecided else None),
        undecided_experiment_id=(undecided[0]["id"] if undecided else None),
        completed_variant_count=completed_variant_count,
        completed_variant_at=completed_variant_at,
        session_id=session_id,
    )
    baseline_at = getattr(brief, "confirmed_at", None) if brief else None
    candidates = collect_findings(inputs)
    ordered, statuses = select_findings(candidates, baseline_at=baseline_at)
    return ordered, statuses, findings_state(ordered, baseline_at=baseline_at)


def _degrade(
    result: OverviewResult,
    section: str,
    exc: Exception,
    *,
    detail_key: Optional[str] = None,
) -> None:
    """Record one section as degraded.

    `detail_key` lets several fact groups degrade the same displayed section
    while still naming which group failed, so an operator can tell a failing
    publish query from a failing experiment query.
    """
    if section not in result.degraded_sections:
        result.degraded_sections.append(section)
    result.degraded_detail[detail_key or section] = f"{type(exc).__name__}: {exc}"
