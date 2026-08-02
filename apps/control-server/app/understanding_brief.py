"""Understanding Brief + Decision Readiness (Issues #351 / #352 / #353).

The Interview screen's answer to three questions the developer has before any
workflow step matters:

1. What does the AI think this system is for?  -> the **Understanding Brief**
   (Vision / System Purpose / Core Capabilities, §1 below)
2. Which parts are settled and which are not?  -> per-claim **confirmation
   state** and **provenance**, kept as two independent axes (§2)
3. May I proceed with this understanding, and why?  -> **Decision Readiness**
   (§3)

Everything here is deterministic: finite vocabularies plus structural checks
over persisted rows (Principle 6). No reasoning model is called from this
module. The open-ended part -- what the Vision/Purpose/Capabilities actually
*are* -- is produced upstream by `system_understanding_reviewer` and by the
developer's own Intent Brief; this module only classifies and arranges what
those already persisted.

Three boundaries this module must not cross:

* **It never gates.** Readiness is an explanation, not a permission: the
  single primary action of the workflow keeps coming from
  `app/interview_workflow.py`, and 「進行不可」 never removes it. Stopping the
  flow until every uncertainty is gone is an explicit non-goal of #353.
* **It never decides the workflow state.** `W0-A`..`W7` has exactly one
  canonical engine (Issue #349). Readiness is a second, independent axis over
  the same persisted facts -- "can I trust this understanding" rather than
  "where am I in the flow" -- and the two are deliberately not merged.
* **It never confirms anything.** Reads only; the human gates (理解の確認 /
  Alignment 項目の確定 / 提案の承認 / 差分の適用 / 観測の開始) are untouched.

probe-agent:
  role: Deterministic Understanding Brief and Decision Readiness derivation
  capability: interactive-system-understanding
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read]
  probe_value: Verify Vision/Purpose/Capability claims never present an unconfirmed AI hypothesis as an established fact, that provenance and confirmation stay independent, and that Readiness is derived from persisted facts only and never gates an action.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import interview_workflow, runtime_alignment, runtime_reality

__all__ = [
    "CLAIM_KINDS",
    "CONFIRMATION_STATES",
    "PROVENANCE_KINDS",
    "READINESS_STATES",
    "REASON_SEVERITIES",
    "CHANGE_KINDS",
    "BRIEF_SECTIONS",
    "CORE_CAPABILITY_INITIAL_LIMIT",
    "KEY_UNCONFIRMED_LIMIT",
    "CONFIRMATION_LABELS",
    "PROVENANCE_LABELS",
    "READINESS_LABELS",
    "READINESS_DESCRIPTIONS",
    "ClaimFacts",
    "ReadinessFacts",
    "ReadinessReason",
    "classify_confirmation",
    "classify_provenance",
    "evaluate_readiness",
    "claim_digest",
    "build_understanding_brief",
]


# --- §0. Finite vocabularies -------------------------------------------------

#: The three claim kinds the Brief separates. Merging any two of them is the
#: exact failure #351 exists to fix, so they never share a list.
CLAIM_KINDS: Tuple[str, ...] = ("vision", "system_purpose", "core_capability")

#: 確認状態 -- how settled a claim is. Independent of PROVENANCE_KINDS.
CONFIRMATION_STATES: Tuple[str, ...] = (
    "confirmed",         # 確認済み
    "ai_hypothesis",     # AI 仮説・未確認
    "conflicting",       # 矛盾あり
    "unknown",           # 不明・情報不足
    "recheck_required",  # 変更後の再確認待ち
)

#: 出所 -- where the claim's CONTENT came from. Deliberately a separate axis:
#: "a human pressed 確認" must never be readable as "a human authored this".
PROVENANCE_KINDS: Tuple[str, ...] = (
    "developer_intent",     # 開発者が明示した意図
    "implementation_fact",  # コード・ドキュメントから得た実装事実
    "runtime_observation",  # Runtime の観測
    "ai_hypothesis",        # AI の解釈・仮説
)

READINESS_STATES: Tuple[str, ...] = (
    "not_built",
    "building",
    "needs_confirmation",
    "ready",
    "recheck_required",
    "blocked",
)

#: A reason is either something that stops the current decision, something the
#: developer should look at, or context. #353: 参考情報の不足と判断を止める
#: 不足を混同しない.
REASON_SEVERITIES: Tuple[str, ...] = ("blocking", "attention", "informational")

#: Change kinds carried over from the deterministic revision diff.
CHANGE_KINDS: Tuple[str, ...] = (
    "added",
    "removed",
    "summary_changed",
    "confidence_changed",
)

#: The `current_understanding` sections the Brief is built from. The remaining
#: sections (elements / API boundaries / probe flow candidates) are detail,
#: reachable through progressive disclosure, and never promoted to this level.
BRIEF_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("vision", "vision"),
    ("system_purpose", "system_purpose"),
    ("core_capabilities", "core_capability"),
)

#: Sections whose item counts the Dashboard shows on its collapsed detail
#: entries, so it never has to walk the understanding itself to label them.
DETAIL_SECTIONS: Tuple[str, ...] = (
    "capability_elements",
    "supporting_elements",
    "api_boundaries",
    "probe_flow_candidates",
)

#: 「初期表示では3〜5件程度に絞る」 (#352). This is a cap, never a pad: a
#: system with two Core Capabilities shows two.
CORE_CAPABILITY_INITIAL_LIMIT = 5

#: How many 重要な未確認事項 the initial view carries. The rest stay in the
#: existing worklists (Q&A / Alignment / gap 一覧) rather than being duplicated.
KEY_UNCONFIRMED_LIMIT = 5

#: Intent Brief fields that together make up 「誰の状態を、どのように変えたい
#: か」. Used only to say WHICH information is missing when no Vision exists --
#: never to synthesise a Vision out of them.
VISION_INTENT_FIELDS: Tuple[str, ...] = ("goal", "pain", "success_criteria")


# --- Japanese developer-facing labels ----------------------------------------
#
# Same convention as routes/interview_workflow.py: the server owns the copy for
# its own finite vocabularies (CLAUDE.md UI language rule), the Dashboard
# renders it. Canonical identifiers (W2, reason codes) stay as-is.

CONFIRMATION_LABELS: Dict[str, str] = {
    "confirmed": "確認済み",
    "ai_hypothesis": "AI 仮説・未確認",
    "conflicting": "矛盾あり",
    "unknown": "不明・情報不足",
    "recheck_required": "変更後の再確認待ち",
}

PROVENANCE_LABELS: Dict[str, str] = {
    "developer_intent": "開発者が明示した意図",
    "implementation_fact": "コード・ドキュメントの実装事実",
    "runtime_observation": "Runtime の観測",
    "ai_hypothesis": "AI の解釈・仮説",
}

READINESS_LABELS: Dict[str, str] = {
    "not_built": "まだ判断できません",
    "building": "システムが理解を作成しています",
    "needs_confirmation": "確認が必要です",
    "ready": "この理解で進めます",
    "recheck_required": "再確認が必要です",
    "blocked": "この理解では進めません",
}

READINESS_DESCRIPTIONS: Dict[str, str] = {
    "not_built": "判断の対象となるシステム理解がまだありません。",
    "building": "理解の作成・更新が終わるまで、確認や訂正はできません。",
    "needs_confirmation": (
        "判断の対象はありますが、あなたの確認が必要な内容が残っています。"
    ),
    "ready": (
        "今回の工程に必要な内容は確認済みで、判断を止める未解決事項はありません。"
        "理解が完全・無誤謬という意味ではありません。"
    ),
    "recheck_required": (
        "前回の確認後に重要な理解が変わりました。前回の確定をそのまま再利用しません。"
    ),
    "blocked": (
        "矛盾・根拠不足・処理の失敗により、現在の理解を判断材料として信頼できません。"
    ),
}

CHANGE_KIND_LABELS: Dict[str, str] = {
    "added": "追加",
    "removed": "削除",
    "summary_changed": "説明の変更",
    "confidence_changed": "確認状態の変化",
}

SECTION_LABELS: Dict[str, str] = {
    "vision": "Vision",
    "system_purpose": "System Purpose",
    "core_capabilities": "主要機能",
    "capability_elements": "機能要素",
    "supporting_elements": "支援要素",
    "api_boundaries": "API 境界",
    "probe_flow_candidates": "プローブ対象フロー候補",
}

#: What is missing when no Vision can be shown, keyed by the Intent Brief field
#: that would supply it. Fixed sentences -- never model output.
VISION_MISSING_INFORMATION: Dict[str, str] = {
    "goal": "このシステムで実現したい状態・価値",
    "pain": "誰のどんな困りごとを解消したいか",
    "success_criteria": "成功をどう判断するか",
}


# --- §1. Claims --------------------------------------------------------------


@dataclass(frozen=True)
class ClaimFacts:
    """Everything the two classifiers below may look at, for ONE claim.

    All of it is a persisted structural fact. In particular there is no
    similarity score and no free-text comparison: a claim is "the same claim"
    as a previously confirmed one when its name matches exactly, which is the
    same rule `understanding_diff` already uses.
    """

    kind: str
    name: str
    summary: str = ""
    why_core: str = ""
    #: The reviewer's own confidence enum for this item.
    confidence_level: str = "uncertain"
    confidence_reason: str = ""
    evidence_count: int = 0
    #: This exact name was part of the understanding revision the developer
    #: confirmed.
    previously_confirmed: bool = False
    #: ...and its meaning-bearing content changed since that confirmation.
    content_changed_since_confirmation: bool = False
    #: The developer wrote or explicitly confirmed this text themselves
    #: (today: an Intent Brief item). Not "the developer confirmed a revision
    #: that contained it" -- that is `previously_confirmed`.
    developer_authored: bool = False
    developer_confirmed: bool = False
    #: The claim's evidence resolves to a component with fresh trace data.
    runtime_observed: bool = False


def classify_confirmation(facts: ClaimFacts) -> str:
    """確認状態, first match. Never returns a value outside CONFIRMATION_STATES.

    Order matters: a conflict outranks a stale confirmation, and a stale
    confirmation outranks the confirmation itself -- otherwise a claim the
    developer confirmed and the system then changed would keep reading
    「確認済み」, which is precisely the silent replacement #351 forbids.
    """
    if facts.confidence_level == "conflicting":
        return "conflicting"
    if facts.previously_confirmed and facts.content_changed_since_confirmation:
        return "recheck_required"
    if facts.developer_confirmed or facts.previously_confirmed:
        return "confirmed"
    # An uncertain claim with nothing behind it is 不明・情報不足, not a
    # hypothesis the developer can weigh: there is no material to judge.
    if facts.confidence_level == "uncertain" and facts.evidence_count == 0:
        return "unknown"
    return "ai_hypothesis"


def classify_provenance(facts: ClaimFacts) -> str:
    """出所, first match. Independent of `classify_confirmation`.

    Deliberately does NOT consider whether a human pressed 確認: confirming an
    AI-written sentence does not turn it into the developer's own intent, and
    conflating the two is the specific mix-up #351/#352 call out.
    """
    if facts.developer_authored:
        return "developer_intent"
    if facts.runtime_observed:
        return "runtime_observation"
    if facts.evidence_count > 0:
        return "implementation_fact"
    return "ai_hypothesis"


def claim_digest(item: Any) -> str:
    """Digest of a claim's meaning-bearing content, excluding its name.

    The name is the match key (as in `understanding_diff`), so a rename is an
    add + a remove rather than a content change. Everything a developer would
    have read when confirming -- summary, rationale, evidence, related docs
    and APIs -- is inside the digest; model confidence is not, because a
    confidence wobble on identical content is not a change of the claim.
    """
    if not isinstance(item, dict):
        return ""
    evidence = []
    for ref in item.get("evidence") or []:
        if not isinstance(ref, dict):
            continue
        evidence.append(
            [
                str(ref.get("path") or ""),
                int(ref.get("start_line") or 0),
                int(ref.get("end_line") or 0),
                str(ref.get("summary") or ""),
            ]
        )
    evidence.sort()
    payload = {
        "summary": item.get("summary") or "",
        "why_core": item.get("why_core") or "",
        "evidence": evidence,
        "related_docs": sorted(str(v) for v in (item.get("related_docs") or [])),
        "related_apis": sorted(str(v) for v in (item.get("related_apis") or [])),
        "children": sorted(str(v) for v in (item.get("children") or [])),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- §3. Decision Readiness --------------------------------------------------


@dataclass(frozen=True)
class ReadinessReason:
    """One reason, always attached to something the developer can go look at.

    #353: 件数だけで理由を代替しない -- a reason carries the claim it is about
    so the UI can offer a way to that claim, not merely a number.
    """

    code: str
    severity: str
    message: str
    target_kind: str = "none"
    target_name: str = ""


@dataclass(frozen=True)
class ReadinessFacts:
    """Persisted facts the readiness evaluation reads. No clock, no client
    state, no request-scoped value -- a reload reproduces the same verdict."""

    has_understanding: bool = False
    building: bool = False
    blocking_failure: bool = False
    #: Names of claims whose confirmation state is `conflicting`.
    conflicting_claims: Tuple[str, ...] = ()
    purpose_present: bool = False
    core_capability_count: int = 0
    #: Core Capabilities that are neither confirmed nor awaiting a recheck.
    #: A claim awaiting a recheck is excluded on purpose: it is not 「未確認」,
    #: and reporting it as such next to 「前回の確認後に変わりました」 tells the
    #: developer two different stories about the same claim.
    unconfirmed_capability_count: int = 0
    vision_present: bool = False
    vision_confirmed: bool = False
    #: Every Purpose claim is settled -- confirmed, or confirmed-then-changed
    #: (the latter is carried by `recheck_claim_count` instead).
    purpose_confirmed: bool = False
    ever_confirmed: bool = False
    capability_recheck_required: bool = False
    #: Deterministic changes to Vision/Purpose/Core Capabilities since the
    #: confirmed revision.
    change_count: int = 0
    #: Claims whose confirmation state is `recheck_required`. Tracked
    #: separately from `change_count` because the two are not the same
    #: measurement: `claim_digest` also covers evidence, related docs, and
    #: children, which the section diff (names / summaries / confidence) does
    #: not report. Without this, an evidence-only edit to a confirmed claim
    #: would slip through as 「進行可能」.
    recheck_claim_count: int = 0
    snapshot_stale: bool = False
    #: Kinds of the processes currently running, for the 構築中 reason text.
    running_process_kinds: Tuple[str, ...] = ()


def evaluate_readiness(facts: ReadinessFacts) -> Tuple[str, List[ReadinessReason]]:
    """Decision Readiness, first-match state + its reasons.

    The state answers 「進めてよいか」; the reasons answer 「なぜ」. Both come
    from the same facts, so they can never disagree -- a screen that says
    「進行可能」 while listing a blocking reason is impossible by construction.
    """
    reasons: List[ReadinessReason] = []

    if facts.building:
        for kind in facts.running_process_kinds:
            reasons.append(
                ReadinessReason(
                    code="process_running",
                    severity="informational",
                    message=_PROCESS_RUNNING_MESSAGE.get(
                        kind, "システムが処理を実行しています。"
                    ),
                    target_kind="process",
                    target_name=kind,
                )
            )
        if not reasons:
            reasons.append(
                ReadinessReason(
                    code="process_running",
                    severity="informational",
                    message="システムが処理を実行しています。",
                    target_kind="process",
                )
            )
        return "building", reasons

    if not facts.has_understanding:
        return "not_built", [
            ReadinessReason(
                code="understanding_not_built",
                severity="informational",
                message="まだシステム理解を構築していません。",
            )
        ]

    blocking: List[ReadinessReason] = []
    if facts.blocking_failure:
        blocking.append(
            ReadinessReason(
                code="blocking_failure",
                severity="blocking",
                message="理解を作る処理が失敗したままで、判断材料が揃っていません。",
                target_kind="process",
            )
        )
    for name in facts.conflicting_claims:
        blocking.append(
            ReadinessReason(
                code="claim_conflict",
                severity="blocking",
                message=f"「{name}」の解釈に矛盾があります。",
                target_kind="claim",
                target_name=name,
            )
        )
    if not facts.purpose_present and facts.core_capability_count == 0:
        blocking.append(
            ReadinessReason(
                code="no_judgeable_content",
                severity="blocking",
                message="System Purpose も主要機能も抽出できておらず、判断の対象がありません。",
            )
        )

    if blocking:
        return "blocked", blocking + _attention_reasons(facts)

    if facts.ever_confirmed and (
        facts.change_count > 0
        or facts.recheck_claim_count > 0
        or facts.capability_recheck_required
    ):
        recheck: List[ReadinessReason] = []
        if facts.change_count > 0:
            recheck.append(
                ReadinessReason(
                    code="understanding_changed",
                    severity="attention",
                    message=(
                        f"前回の確認後に Vision・Purpose・主要機能が {facts.change_count} 件変わりました。"
                    ),
                )
            )
        elif facts.recheck_claim_count > 0:
            # The section diff saw nothing (only evidence / related docs /
            # children moved), but the claim the developer confirmed is not
            # the claim on screen any more.
            recheck.append(
                ReadinessReason(
                    code="confirmed_claim_edited",
                    severity="attention",
                    message=(
                        f"確認済みの主張 {facts.recheck_claim_count} 件で、根拠や関連情報が変わりました。"
                    ),
                )
            )
        if facts.capability_recheck_required:
            recheck.append(
                ReadinessReason(
                    code="capability_composition_stale",
                    severity="attention",
                    message="確定済みの Capability 構成が、最新の理解に対応していません。",
                )
            )
        return "recheck_required", recheck + _attention_reasons(facts)

    attention = _attention_reasons(facts)
    if not facts.ever_confirmed or any(r.severity == "attention" for r in attention):
        return "needs_confirmation", attention

    return "ready", [
        ReadinessReason(
            code="ready",
            severity="informational",
            message=(
                "System Purpose と主要機能は確認済みで、判断を止める未解決事項はありません。"
            ),
        )
    ] + attention


_PROCESS_RUNNING_MESSAGE: Dict[str, str] = {
    "understanding_build": "システム理解を構築しています。",
    "understanding_update": "システム理解を更新しています。",
    "alignment_build": "意図とのズレを突き合わせています。",
    "question_routing": "質問を分類して調査しています。",
    "intent_candidates": "意図の候補を作成しています。",
    "proposal_generation": "計測の提案を作成しています。",
    "diff_generation": "差分を生成しています。",
    "runtime_reality_check": "実態チェックを実行しています。",
}


def _attention_reasons(facts: ReadinessFacts) -> List[ReadinessReason]:
    """Reasons that ask for the developer's attention without stopping them.

    Kept separate from the blocking list on purpose: a missing Vision is worth
    knowing about, and is emphatically not a reason to hold the flow (#353's
    非目標).
    """
    out: List[ReadinessReason] = []
    if not facts.purpose_confirmed and facts.purpose_present:
        out.append(
            ReadinessReason(
                code="purpose_unconfirmed",
                severity="attention",
                message="System Purpose が未確認です。",
                target_kind="claim",
                target_name="system_purpose",
            )
        )
    if not facts.purpose_present:
        out.append(
            ReadinessReason(
                code="purpose_missing",
                severity="attention",
                message="System Purpose を抽出できていません。",
                target_kind="claim",
                target_name="system_purpose",
            )
        )
    if facts.unconfirmed_capability_count > 0:
        out.append(
            ReadinessReason(
                code="capabilities_unconfirmed",
                severity="attention",
                message=(
                    f"主要機能 {facts.core_capability_count} 件のうち "
                    f"{facts.unconfirmed_capability_count} 件が未確認です。"
                ),
                target_kind="claim",
                target_name="core_capability",
            )
        )
    if not facts.vision_present:
        out.append(
            ReadinessReason(
                code="vision_missing",
                severity="informational",
                message="Vision を推定できていません。コードだけでは確定できない場合があります。",
                target_kind="claim",
                target_name="vision",
            )
        )
    elif not facts.vision_confirmed:
        out.append(
            ReadinessReason(
                code="vision_unconfirmed",
                severity="informational",
                message="Vision は未確認です。",
                target_kind="claim",
                target_name="vision",
            )
        )
    if facts.snapshot_stale:
        out.append(
            ReadinessReason(
                code="snapshot_stale",
                severity="informational",
                message="この理解は、最新ではない snapshot に固定されています。",
                target_kind="snapshot",
            )
        )
    return out


# --- §4. Reading the persisted facts -----------------------------------------


@dataclass
class BriefClaim:
    kind: str
    name: str
    summary: str
    confirmation: str
    provenance: str
    confirmation_label: str
    provenance_label: str
    reason: str = ""
    contribution: str = ""
    #: The claim text came from a mock LLM run. Only reachable through an
    #: `ai_proposed` Intent Brief item: the Understanding reviewer fails
    #: closed on a mock client, so `current_understanding` is never mock.
    #: Marked rather than hidden -- CLAUDE.md forbids presenting mock output
    #: as analysed data, not showing it.
    is_mock: bool = False
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    related_docs: List[str] = field(default_factory=list)
    related_apis: List[str] = field(default_factory=list)


@dataclass
class UnderstandingChange:
    change_kind: str
    section: str
    section_label: str
    name: str
    detail: str


@dataclass
class BriefResult:
    session_id: Optional[int]
    built: bool
    vision: Optional[BriefClaim]
    vision_missing_information: List[str]
    system_purpose: List[BriefClaim]
    core_capabilities: List[BriefClaim]
    core_capability_initial_count: int
    key_unconfirmed: List[BriefClaim]
    detail_counts: Dict[str, int]
    readiness_state: str
    readiness_label: str
    readiness_description: str
    readiness_reasons: List[ReadinessReason]
    changes_since_confirmation: List[UnderstandingChange]
    confirmed_at: Optional[float]
    confirmed_revision_id: Optional[int]
    #: The revision the Brief was built from. Shown while a rebuild is in
    #: flight so 「更新中」 and 「いま画面にあるのはこれ」 stay distinguishable
    #: (#354 `W1`).
    revision_id: Optional[int]
    snapshot_id: Optional[int]


def _claim_from_item(
    kind: str,
    item: Dict[str, Any],
    facts: ClaimFacts,
) -> BriefClaim:
    confirmation = classify_confirmation(facts)
    provenance = classify_provenance(facts)
    return BriefClaim(
        kind=kind,
        name=facts.name,
        summary=facts.summary,
        confirmation=confirmation,
        provenance=provenance,
        confirmation_label=CONFIRMATION_LABELS[confirmation],
        provenance_label=PROVENANCE_LABELS[provenance],
        reason=facts.confidence_reason,
        contribution=facts.why_core,
        evidence=[e for e in (item.get("evidence") or []) if isinstance(e, dict)],
        related_docs=[str(v) for v in (item.get("related_docs") or [])],
        related_apis=[str(v) for v in (item.get("related_apis") or [])],
    )


def _confirmed_revision(
    conn: sqlite3.Connection, session: Dict[str, Any]
) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    """The understanding revision the developer actually confirmed.

    Preferred source is #312's canonical Capability confirmation, which
    records `source_revision_id` explicitly. A zero-base session confirms
    without any structured revision, and older sessions predate the
    confirmation table, so the fallback is the newest revision created at or
    before `understanding_confirmed_at` -- still a persisted fact, never a
    guess about which revision "probably" was on screen.
    """
    session_id = session["id"]
    system_id = session["system_id"]
    confirmed_at = session.get("understanding_confirmed_at")
    if confirmed_at is None:
        return None, None

    row = conn.execute(
        """SELECT source_revision_id FROM understanding_capability_confirmation
           WHERE system_id = ? AND session_id = ? AND source_revision_id IS NOT NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, session_id),
    ).fetchone()
    revision_id = row["source_revision_id"] if row else None
    if revision_id is None:
        fallback = conn.execute(
            """SELECT id FROM understanding_revision
               WHERE session_id = ? AND system_id = ? AND created_at <= ?
               ORDER BY id DESC LIMIT 1""",
            (session_id, system_id, confirmed_at),
        ).fetchone()
        revision_id = fallback["id"] if fallback else None
    if revision_id is None:
        return None, None

    revision = conn.execute(
        "SELECT current_understanding FROM understanding_revision WHERE id = ?",
        (revision_id,),
    ).fetchone()
    if revision is None or not revision["current_understanding"]:
        return revision_id, None
    try:
        parsed = json.loads(revision["current_understanding"])
    except (TypeError, ValueError):
        return revision_id, None
    return revision_id, parsed if isinstance(parsed, dict) else None


def _items(understanding: Optional[Dict[str, Any]], section: str) -> List[Dict[str, Any]]:
    if not understanding:
        return []
    value = understanding.get(section)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("name")]


def _confirmed_index(
    confirmed: Optional[Dict[str, Any]], section: str
) -> Dict[str, str]:
    return {str(item["name"]): claim_digest(item) for item in _items(confirmed, section)}


def _latest_intent_items(
    conn: sqlite3.Connection, session_id: int, system_id: int
) -> Dict[str, Dict[str, Any]]:
    """Newest non-superseded Intent Brief row per field."""
    rows = conn.execute(
        """SELECT field, value_text, status, origin, is_mock FROM interview_intent_item
           WHERE session_id = ? AND system_id = ? AND superseded_by_id IS NULL
             AND status != 'not_applicable'
           ORDER BY id""",
        (session_id, system_id),
    ).fetchall()
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        latest[row["field"]] = dict(row)
    return latest


def _runtime_observed(
    conn: sqlite3.Connection,
    system_id: int,
    snapshot_id: int,
    evidence: Sequence[Dict[str, Any]],
    *,
    now: float,
) -> bool:
    """Does this claim's evidence map to a component with FRESH traces?

    Reuses #290's deterministic evidence -> component mapping, which returns
    None whenever the mapping is ambiguous, so a claim is never credited with
    an observation that might belong to a different component. Freshness uses
    the same `RUNTIME_FACT_FRESH_SECONDS` setting the Runtime Reality Check
    uses -- a claim backed only by long-stale traces is not "observed now".
    """
    if not evidence:
        return False
    component_id = runtime_alignment.resolve_component_for_evidence(
        conn, snapshot_id, evidence
    )
    if not component_id:
        return False
    row = conn.execute(
        "SELECT MAX(timestamp) AS last FROM traces WHERE system_id = ? AND component_id = ?",
        (system_id, component_id),
    ).fetchone()
    last = row["last"] if row else None
    if last is None:
        return False
    return (now - float(last)) <= runtime_reality.fresh_seconds()


_KEY_UNCONFIRMED_ORDER = {
    "conflicting": 0,
    "recheck_required": 1,
    "unknown": 2,
    "ai_hypothesis": 3,
}
_KIND_ORDER = {"vision": 0, "system_purpose": 1, "core_capability": 2}


def _key_unconfirmed(claims: Sequence[BriefClaim]) -> List[BriefClaim]:
    """重要な未確認事項, deterministically ordered and capped.

    Severity first, then Vision -> Purpose -> Capability, then the order the
    reviewer returned them in. No score, no ranking model.
    """
    ranked = [
        (
            _KEY_UNCONFIRMED_ORDER[c.confirmation],
            _KIND_ORDER[c.kind],
            index,
            c,
        )
        for index, c in enumerate(claims)
        if c.confirmation in _KEY_UNCONFIRMED_ORDER
    ]
    ranked.sort(key=lambda entry: entry[:3])
    return [entry[3] for entry in ranked[:KEY_UNCONFIRMED_LIMIT]]


def _changes_since_confirmation(
    confirmed: Optional[Dict[str, Any]],
    current: Optional[Dict[str, Any]],
) -> List[UnderstandingChange]:
    """Vision / Purpose / Core Capability changes since the confirmed revision.

    Only these three sections: #353 asks for 「何が、どのように変わったか」 at
    the level the developer made their decision on. The complete revision diff
    stays available through the existing `understanding-diff` endpoint.
    """
    from .understanding_diff import diff_understanding

    if confirmed is None:
        return []
    wanted = {section for section, _ in BRIEF_SECTIONS}
    out: List[UnderstandingChange] = []
    for section_diff in diff_understanding(confirmed, current):
        section = section_diff["section"]
        if section not in wanted:
            continue
        label = SECTION_LABELS.get(section, section)
        for name in section_diff["added"]:
            out.append(
                UnderstandingChange("added", section, label, name, f"{label}に追加されました。")
            )
        for name in section_diff["removed"]:
            out.append(
                UnderstandingChange(
                    "removed", section, label, name, f"{label}から削除されました。"
                )
            )
        for change in section_diff["confidence_changed"]:
            before = CONFIDENCE_LEVEL_LABELS.get(
                change.get("before") or "", change.get("before") or "不明"
            )
            after = CONFIDENCE_LEVEL_LABELS.get(
                change.get("after") or "", change.get("after") or "不明"
            )
            out.append(
                UnderstandingChange(
                    "confidence_changed",
                    section,
                    label,
                    str(change.get("name") or ""),
                    f"確信度が「{before}」から「{after}」に変わりました。",
                )
            )
        for name in section_diff["summary_changed"]:
            out.append(
                UnderstandingChange(
                    "summary_changed", section, label, name, "説明が変わりました。"
                )
            )
    return out


#: The reviewer's confidence enum, for change descriptions only. The Brief's
#: own 確認状態 vocabulary is CONFIRMATION_STATES -- these two are different
#: things and are deliberately labelled differently.
CONFIDENCE_LEVEL_LABELS: Dict[str, str] = {
    "confirmed": "確信あり",
    "likely": "おそらく正しい",
    "uncertain": "未確認",
    "conflicting": "矛盾あり",
}


def build_understanding_brief(
    conn: sqlite3.Connection,
    system_id: int,
    session_id: Optional[int],
    *,
    now: Optional[float] = None,
) -> BriefResult:
    """Assemble the Brief and Readiness for one session (or for none).

    `session_id=None` is a real case, not an error: `W0-A` / `W0-B` exist
    before any session does, and the screen still has to say honestly that
    there is no understanding yet rather than showing a placeholder Vision.
    """
    now = time.time() if now is None else now

    gathered = interview_workflow.gather_facts(conn, system_id, session_id)
    session = gathered.session
    if session is None:
        return _empty_result(session_id=None)

    understanding: Optional[Dict[str, Any]] = None
    if session.get("current_understanding"):
        try:
            parsed = json.loads(session["current_understanding"])
            understanding = parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            understanding = None

    confirmed_revision_id, confirmed_understanding = _confirmed_revision(conn, session)
    latest_revision = conn.execute(
        """SELECT id FROM understanding_revision
           WHERE session_id = ? AND system_id = ? ORDER BY id DESC LIMIT 1""",
        (session["id"], system_id),
    ).fetchone()
    intent_items = _latest_intent_items(conn, session["id"], system_id)
    snapshot_id = session["snapshot_id"]

    def build_claims(section: str, kind: str) -> List[BriefClaim]:
        confirmed_index = _confirmed_index(confirmed_understanding, section)
        claims: List[BriefClaim] = []
        for item in _items(understanding, section):
            name = str(item["name"])
            digest = claim_digest(item)
            previously = name in confirmed_index
            confidence = item.get("confidence") or {}
            claims.append(
                _claim_from_item(
                    kind,
                    item,
                    ClaimFacts(
                        kind=kind,
                        name=name,
                        summary=str(item.get("summary") or ""),
                        why_core=str(item.get("why_core") or ""),
                        confidence_level=str(confidence.get("level") or "uncertain"),
                        confidence_reason=str(confidence.get("reason") or ""),
                        evidence_count=len(
                            [e for e in (item.get("evidence") or []) if isinstance(e, dict)]
                        ),
                        previously_confirmed=previously,
                        content_changed_since_confirmation=(
                            previously and confirmed_index[name] != digest
                        ),
                        runtime_observed=_runtime_observed(
                            conn,
                            system_id,
                            snapshot_id,
                            [e for e in (item.get("evidence") or []) if isinstance(e, dict)],
                            now=now,
                        ),
                    ),
                )
            )
        return claims

    purpose_claims = build_claims("system_purpose", "system_purpose")
    capability_claims = build_claims("core_capabilities", "core_capability")
    vision_claim, vision_missing = _resolve_vision(
        build_claims("vision", "vision"), intent_items
    )

    all_claims = ([vision_claim] if vision_claim else []) + purpose_claims + capability_claims
    built = bool(all_claims) or interview_workflow._has_understanding_content(understanding)

    changes = _changes_since_confirmation(confirmed_understanding, understanding)

    readiness_facts = ReadinessFacts(
        has_understanding=built,
        building=bool(gathered.facts.running_process_kinds),
        blocking_failure=bool(gathered.blocking_failures),
        conflicting_claims=tuple(
            c.name for c in all_claims if c.confirmation == "conflicting"
        ),
        purpose_present=bool(purpose_claims),
        core_capability_count=len(capability_claims),
        unconfirmed_capability_count=sum(
            1
            for c in capability_claims
            if c.confirmation not in ("confirmed", "recheck_required")
        ),
        recheck_claim_count=sum(
            1 for c in all_claims if c.confirmation == "recheck_required"
        ),
        vision_present=vision_claim is not None,
        vision_confirmed=bool(vision_claim and vision_claim.confirmation == "confirmed"),
        purpose_confirmed=bool(
            purpose_claims
            and all(
                c.confirmation in ("confirmed", "recheck_required")
                for c in purpose_claims
            )
        ),
        ever_confirmed=session.get("understanding_confirmed_at") is not None,
        capability_recheck_required=interview_workflow._capability_confirmation_required(
            conn, session
        ),
        change_count=len(changes),
        snapshot_stale=gathered.snapshot_stale,
        running_process_kinds=tuple(gathered.facts.running_process_kinds),
    )
    state, reasons = evaluate_readiness(readiness_facts)

    return BriefResult(
        session_id=session["id"],
        built=built,
        vision=vision_claim,
        vision_missing_information=vision_missing,
        system_purpose=purpose_claims,
        core_capabilities=capability_claims,
        core_capability_initial_count=min(
            len(capability_claims), CORE_CAPABILITY_INITIAL_LIMIT
        ),
        key_unconfirmed=_key_unconfirmed(all_claims),
        detail_counts={
            section: len(_items(understanding, section)) for section in DETAIL_SECTIONS
        },
        readiness_state=state,
        readiness_label=READINESS_LABELS[state],
        readiness_description=READINESS_DESCRIPTIONS[state],
        readiness_reasons=reasons,
        changes_since_confirmation=changes,
        confirmed_at=session.get("understanding_confirmed_at"),
        confirmed_revision_id=confirmed_revision_id,
        revision_id=(latest_revision["id"] if latest_revision else None),
        snapshot_id=snapshot_id,
    )


def _resolve_vision(
    ai_claims: List[BriefClaim],
    intent_items: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[BriefClaim], List[str]]:
    """The one Vision statement, plus what is missing when there is none.

    First match:

    1. A confirmed Intent Brief `goal`. The developer's own statement of what
       they want to change outranks any model reading of the repository --
       that is the whole point of separating Vision from Purpose.
    2. The reviewer's `vision` claim, classified like any other claim (an
       evidence-less one is already clamped to `uncertain` upstream, so it can
       only surface as 不明・情報不足 or AI 仮説).
    3. An unconfirmed Intent Brief `goal`, shown as the hypothesis it is.
    4. Nothing -- and then the missing information is named, because
       「Vision を推定できない」 is itself a fact worth showing (#352).
    """
    goal = intent_items.get("goal")
    if goal and goal.get("status") == "confirmed":
        return (
            BriefClaim(
                kind="vision",
                name=str(goal["value_text"]),
                summary="",
                confirmation="confirmed",
                provenance="developer_intent",
                confirmation_label=CONFIRMATION_LABELS["confirmed"],
                provenance_label=PROVENANCE_LABELS["developer_intent"],
                reason="Intent Brief であなたが確定した内容です。",
                is_mock=bool(goal.get("is_mock")),
            ),
            [],
        )
    if ai_claims:
        return ai_claims[0], []
    if goal:
        developer_authored = goal.get("origin") == "user"
        provenance = "developer_intent" if developer_authored else "ai_hypothesis"
        return (
            BriefClaim(
                kind="vision",
                name=str(goal["value_text"]),
                summary="",
                confirmation="ai_hypothesis",
                provenance=provenance,
                confirmation_label=CONFIRMATION_LABELS["ai_hypothesis"],
                provenance_label=PROVENANCE_LABELS[provenance],
                reason="Intent Brief で未確定の内容です。",
                is_mock=bool(goal.get("is_mock")),
            ),
            [],
        )
    missing = [
        VISION_MISSING_INFORMATION[field_name]
        for field_name in VISION_INTENT_FIELDS
        if field_name not in intent_items
    ]
    return None, missing


def _empty_result(session_id: Optional[int]) -> BriefResult:
    state, reasons = evaluate_readiness(ReadinessFacts())
    return BriefResult(
        session_id=session_id,
        built=False,
        vision=None,
        vision_missing_information=[
            VISION_MISSING_INFORMATION[field_name] for field_name in VISION_INTENT_FIELDS
        ],
        system_purpose=[],
        core_capabilities=[],
        core_capability_initial_count=0,
        key_unconfirmed=[],
        detail_counts={section: 0 for section in DETAIL_SECTIONS},
        readiness_state=state,
        readiness_label=READINESS_LABELS[state],
        readiness_description=READINESS_DESCRIPTIONS[state],
        readiness_reasons=reasons,
        changes_since_confirmation=[],
        confirmed_at=None,
        confirmed_revision_id=None,
        revision_id=None,
        snapshot_id=None,
    )
