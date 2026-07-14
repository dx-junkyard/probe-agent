"""State/diagnostic/pipeline message catalog (Issue #240).

Single source of truth for user-facing state copy (summary / detail / impact
/ remediation / action_label) that used to be scattered as f-strings across
``system_state.py``, ``system_diagnostics.py``, and
``system_understanding_service.py``. Two problems this fixes:

1. Display language was inconsistent: ``system_state.py`` /
   ``system_diagnostics.py`` text was already Japanese, but
   ``system_understanding_service.py``'s pipeline-step labels, stage
   labels/descriptions, gap titles/next-actions, and some ``PipelineStep``
   detail text were English. Every value in this module is Japanese.
2. The same message text lived inline in each producing function, with no
   single place to audit or test for completeness. Every ``state_id`` /
   ``check_id`` (+ explicit variant, where one check/branch produces more
   than one wording) a producing module can emit must have an entry here;
   the finite lists near the bottom of this module
   (``ALL_PIPELINE_FAMILY_STATE_IDS`` and friends) exist so
   ``tests/test_state_messages.py`` can assert no key is missing.

Design (Principle 6 -- deterministic, finite, no reasoning model):

- Every value below is a static template string. Dynamic content is limited
  to named ``str.format`` parameters that are themselves already-finite
  facts (counts, snapshot ids, commit SHAs, raw upstream error/status
  strings) -- never free text authored by a reasoning model.
- Lookups use plain ``dict[key]`` indexing (via the small ``_get`` helper
  below), which raises ``KeyError`` with a clear message when a key is
  missing. This is intentional: a missing catalog entry must fail loudly
  (breaking whatever test exercises that code path) rather than silently
  falling back to English or blank text.
- Where multiple ``StateItem`` / ``DiagnosticCheck`` branches share
  identical wording modulo one or two substitutions (the ``_pipeline_state_item``
  factory in ``system_state.py``, and the ``_run_backed_pipeline_check`` /
  ``_artifact_backed_pipeline_check`` / ``_build_step_pipeline_check`` /
  ``_no_ready_snapshot_check`` / ``_reasoning_unavailable_check`` shared
  helpers in ``system_diagnostics.py``), this module still expands them into
  concrete ``state_id`` / ``check_id`` keyed entries (built programmatically
  from a small per-prefix parameter table) rather than a single generic
  template keyed by something other than the literal id -- so the catalog
  key really is the ``state_id`` / ``check_id`` the API emits, per the issue
  body's "テンプレートのキーは state_id / check_id を正とし" rule.

probe-agent:
  role: Static Japanese message catalog for deterministic state/diagnostic/pipeline copy
  capability: system-state-assessment
  element_type: element
  consumers: [control-server]
  operation_kind: read
  state_effects: []
  probe_value: Verify every state_id/check_id the deterministic state layers can emit resolves to a catalog entry instead of silently falling back to blank/English text.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _get(catalog: Dict[str, Dict[str, str]], key: str, *, catalog_name: str) -> Dict[str, str]:
    try:
        return catalog[key]
    except KeyError as exc:
        raise KeyError(
            f"state_messages.{catalog_name}: no catalog entry for key {key!r}. "
            "Add one instead of falling back to hardcoded/English text (Issue #240)."
        ) from exc


def _fmt(template: Dict[str, str], field: str, **kwargs: Any) -> str:
    raw = template.get(field, "")
    if not raw:
        return ""
    return raw.format(**kwargs) if kwargs else raw


# ---------------------------------------------------------------------------
# system_state.py: understanding.{purpose,capabilities}.<kind>
# ---------------------------------------------------------------------------

UNDERSTANDING_MESSAGES: Dict[str, Dict[str, str]] = {
    "understanding.purpose.satisfied": {
        "summary": "System Purpose は現在の snapshot で確認済みです。",
        "detail": "System Purpose は現在の snapshot で定義されています。",
    },
    "understanding.capabilities.satisfied": {
        "summary": "Core Capabilities は現在の snapshot で確認済みです。",
        "detail": "Core Capabilities は現在の snapshot で定義されています。",
    },
    "understanding.purpose.baseline_reusable": {
        "summary": "System Purpose は確認済み baseline を再利用できます。",
        "detail": (
            "System Purpose は snapshot #{baseline_snapshot_id} の確認済み理解を再利用できます。"
            "今回の差分には再確認が必要な根拠変更は見つかりませんでした。"
        ),
    },
    "understanding.capabilities.baseline_reusable": {
        "summary": "Core Capabilities は確認済み baseline を再利用できます。",
        "detail": (
            "Core Capabilities は snapshot #{baseline_snapshot_id} の確認済み理解を再利用できます。"
            "今回の差分には再確認が必要な根拠変更は見つかりませんでした。"
        ),
    },
    "understanding.purpose.diff_impacted": {
        "summary": "System Purpose は最新 snapshot との差分で再確認が必要です。",
        "detail": (
            "System Purpose は snapshot #{baseline_snapshot_id} で確認済みですが、"
            "最新 snapshot との差分に影響候補があります。{reason}"
        ),
        "impact": "確認済み理解をそのまま使えるか判断が必要です。",
        "remediation": "Interview で System Purpose を再確認してください。",
        "action_label": "Interview でSystem Purposeを再確認",
    },
    "understanding.capabilities.diff_impacted": {
        "summary": "Core Capabilities は最新 snapshot との差分で再確認が必要です。",
        "detail": (
            "Core Capabilities は snapshot #{baseline_snapshot_id} で確認済みですが、"
            "最新 snapshot との差分に影響候補があります。{reason}"
        ),
        "impact": "確認済み理解をそのまま使えるか判断が必要です。",
        "remediation": "Interview で Core Capabilities を再確認してください。",
        "action_label": "Interview でCore Capabilitiesを再確認",
    },
    "understanding.purpose.unconfirmed": {
        "summary": "System Purpose に未確認の候補があります。",
        "detail": (
            "Interview に未確認の System Purpose 候補が {count} 件あります。"
            "baseline として再利用するには、開発者による明示的な確認が必要です。"
        ),
        "impact": "未確認の理解は、snapshot 更新後の差分診断や probe 設計の前提としてまだ採用されません。",
        "remediation": "Interview で System Purpose を確認済みにしてください。",
        "action_label": "Interview でSystem Purposeを確認",
    },
    "understanding.capabilities.unconfirmed": {
        "summary": "Core Capabilities に未確認の候補があります。",
        "detail": (
            "Interview に未確認の Core Capabilities 候補が {count} 件あります。"
            "baseline として再利用するには、開発者による明示的な確認が必要です。"
        ),
        "impact": "未確認の理解は、snapshot 更新後の差分診断や probe 設計の前提としてまだ採用されません。",
        "remediation": "Interview で Core Capabilities を確認済みにしてください。",
        "action_label": "Interview でCore Capabilitiesを確認",
    },
    "understanding.purpose.missing_baseline": {
        "summary": "System Purpose が未定義です。",
        "detail": "System Purpose が未定義です。確認済み・未確認いずれの baseline もありません。",
        "impact": "probe 設計・flow 探索・改善提案・ユーザー意図との整合の前提となる根幹情報が欠けています。",
        "remediation": "Interview で System Purpose を定義・確認してください。",
        "action_label": "Interview でSystem Purposeを定義",
    },
    "understanding.capabilities.missing_baseline": {
        "summary": "Core Capabilities が未定義です。",
        "detail": "Core Capabilities が未定義です。確認済み・未確認いずれの baseline もありません。",
        "impact": "probe 設計・flow 探索・改善提案・ユーザー意図との整合の前提となる根幹情報が欠けています。",
        "remediation": "Interview で Core Capabilities を定義・確認してください。",
        "action_label": "Interview でCore Capabilitiesを定義",
    },
}


def understanding_message(state_id: str) -> Dict[str, str]:
    return _get(UNDERSTANDING_MESSAGES, state_id, catalog_name="UNDERSTANDING_MESSAGES")


# ---------------------------------------------------------------------------
# system_state.py: pipeline.<prefix>.<suffix> (the _pipeline_state_item family)
# ---------------------------------------------------------------------------

# subject text + not_run remediation fragment per pipeline-step prefix.
_PIPELINE_STEP_PARAMS: Dict[str, Dict[str, str]] = {
    "pipeline.symbol_index": {
        "subject": "シンボル索引",
        "not_run_action": "コードシンボルを索引付けして",
    },
    "pipeline.entrypoint_index": {
        "subject": "エントリポイント索引",
        "not_run_action": "エントリポイントを検出して",
    },
    "pipeline.documentation_index": {
        "subject": "ドキュメント索引",
        "not_run_action": "ドキュメントチャンクを索引付けして",
    },
    "pipeline.capability_hierarchy": {
        "subject": "Capability 階層",
        "not_run_action": " capability 階層を生成して",
    },
}

# Generic per-suffix templates. "{subject}" / "{not_run_action}" are filled
# in per prefix below; "blocked_by_reasoning" only ever fires for
# pipeline.capability_hierarchy (the only caller that passes
# blocked_by_reasoning=True -- see system_state._capability_hierarchy_item).
_PIPELINE_SUFFIX_TEMPLATES: Dict[str, Dict[str, str]] = {
    "blocked_by_reasoning": {
        "summary": "{subject} は reasoning モデル未設定のためブロックされています。",
        "detail": "このステップには reasoning モデルが必要ですが、現在は利用可能な reasoning モデルが設定されていません。",
        "impact": "System Understanding でこのステップはブロックとして表示されます。",
        "remediation": "intelligence 用 reasoning モデル設定を修正してからビルドを実行してください。",
        "action_label": "LLM 設定を確認",
    },
    "running": {
        "summary": "{subject} を実行中です。",
        "detail": "System Understanding build がこの snapshot に対して実行中です。完了まで待機してください。",
        "impact": "このステップの成果物は build 完了後に利用できます。",
        "remediation": "現在の build の完了を待ってください。",
    },
    "blocked": {
        "summary": "{subject} がブロックされています。",
        "detail": "直近のビルドステップは blocked です。依存ステップやビルドステップのエラーを確認してください。",
        "impact": "System Understanding でこのステップはブロックとして表示されます。",
        "remediation": "ブロック原因を解消してから Build / Refresh を再実行してください。",
        "action_label": "Build 状態を確認",
    },
    "failed": {
        "summary": "{subject} が失敗しています。",
        "detail": "直近の実行が failed です。エラーを確認し、原因を修正してから再実行してください。",
        "impact": "このステップの成果物が欠落しているか古くなっています。",
        "remediation": "System Understanding で Build / Refresh を再実行してください。",
        "action_label": "Build / Refresh を再実行",
    },
    "not_run": {
        "summary": "{subject} が未実行です。",
        "detail_cancelled": "直近の実行は cancelled です。必要なら Build / Refresh を再実行してください。",
        "detail_default": "このステップは現在の snapshot に対して実行されていません。",
        "impact": "System Understanding でこのステップは未実行として表示されます。",
        "remediation": "System Understanding で Build / Refresh を実行して{not_run_action}ください。",
        "action_label": "Build / Refresh を実行",
    },
}

# Only capability_hierarchy uses the blocked_by_reasoning suffix today.
_PIPELINE_SUFFIX_BY_PREFIX: Dict[str, List[str]] = {
    prefix: (
        ["running", "blocked", "failed", "not_run"]
        + (["blocked_by_reasoning"] if prefix == "pipeline.capability_hierarchy" else [])
    )
    for prefix in _PIPELINE_STEP_PARAMS
}


def _build_pipeline_family_messages() -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for prefix, params in _PIPELINE_STEP_PARAMS.items():
        for suffix in _PIPELINE_SUFFIX_BY_PREFIX[prefix]:
            template = _PIPELINE_SUFFIX_TEMPLATES[suffix]
            resolved = {
                field: (value.format(**params) if isinstance(value, str) else value)
                for field, value in template.items()
            }
            out[f"{prefix}.{suffix}"] = resolved
    return out


PIPELINE_FAMILY_MESSAGES: Dict[str, Dict[str, str]] = _build_pipeline_family_messages()

# Every concrete state_id the _pipeline_state_item family can emit -- used by
# the completeness test (Issue #240 acceptance: "全 state_id に対するカタロ
# グキー存在検証（コード中で生成され得る全 state_id を列挙する仕組み）").
ALL_PIPELINE_FAMILY_STATE_IDS: List[str] = sorted(PIPELINE_FAMILY_MESSAGES.keys())


def pipeline_family_message(state_id: str) -> Dict[str, str]:
    return _get(PIPELINE_FAMILY_MESSAGES, state_id, catalog_name="PIPELINE_FAMILY_MESSAGES")


# ---------------------------------------------------------------------------
# system_state.py: pipeline.capability_hierarchy.empty (Issue #210's
# "completed but zero capability nodes" branch -- 3 dynamic variants
# depending on Interview proposal review/approval state).
# ---------------------------------------------------------------------------

CAPABILITY_HIERARCHY_EMPTY_MESSAGES: Dict[str, Dict[str, str]] = {
    "needs_review": {
        "detail": (
            "capability_hierarchy の実行（#{run_id}）は完了していますが、現在の snapshot に "
            "`probe-agent:` docstring メタデータがありません。"
            "Interview session #{session_id} の提案 {needs_review} 件は再レビュー待ちです。"
        ),
        "remediation": (
            "Interview session #{session_id} で提案を再レビューして承認し、レビュー用差分を生成してください。"
            "差分を対象リポジトリへ適用して新しい snapshot を作成した後、Build / Refresh を実行してください。"
        ),
        "action_label": "Interview の提案を再レビュー",
    },
    "approved": {
        "detail": (
            "capability_hierarchy の実行（#{run_id}）は完了していますが、現在の snapshot に "
            "`probe-agent:` docstring メタデータがありません。承認済み Interview 提案はまだソースに反映されていません。"
        ),
        "remediation": (
            "Interview session #{session_id} でレビュー用差分を生成し、対象リポジトリへ適用してください。"
            "その後、新しい snapshot を作成して Build / Refresh を実行してください。"
        ),
        "action_label": "Interview で差分を生成",
    },
    "none": {
        "detail": (
            "capability_hierarchy の実行（#{run_id}）は完了していますが、"
            "現在の snapshot に capability ノードが存在しません。対象リポジトリに"
            " `probe-agent:` docstring メタデータが見つからなかったことが原因です。"
        ),
        "remediation": (
            "Interview で Core Capabilities を確認して提案を生成・承認し、レビュー用差分を対象リポジトリへ適用してください。"
            "新しい snapshot を作成した後、Build / Refresh を実行してください。"
        ),
        "action_label": "Interview で Core Capabilities を確認",
    },
    # Static, variant-independent fields shared by all three branches above.
    "_shared": {
        "summary": "Capability 階層は実行済みですが capability が 0 件です。",
        "impact": "Core Capabilities が未定義のため、probe 設計・flow 探索・改善提案の前提が欠けています。",
    },
}


def capability_hierarchy_empty_message(variant: str) -> Dict[str, str]:
    return _get(
        CAPABILITY_HIERARCHY_EMPTY_MESSAGES, variant, catalog_name="CAPABILITY_HIERARCHY_EMPTY_MESSAGES"
    )


# ---------------------------------------------------------------------------
# system_state.py: remaining individually-constructed StateItem messages.
# ---------------------------------------------------------------------------

STATE_MESSAGES: Dict[str, Dict[str, str]] = {
    "snapshot.ready.running": {
        "summary": "Snapshot を作成中です。",
        "detail": "最新の snapshot #{latest_id} は作成中です。ready になるまで待機してください。",
        "impact": "snapshot が ready になるまで、snapshot の内容を読むパイプラインステップは開始できません。",
        "remediation": "Snapshot 作成の完了を待ってください。",
    },
    "snapshot.ready.missing": {
        "summary": "ready な snapshot がありません。",
        "detail_no_latest": "このシステムではまだ snapshot が作成されていません。",
        "detail_with_latest": "ready な snapshot がありません。最新の snapshot #{latest_id} の状態は '{latest_status}' です。",
        "impact": "snapshot の内容を読むすべてのパイプラインステップがブロックされます。",
        "remediation": "Repository タブの Snapshots から snapshot を作成してください。",
        "action_label": "Snapshot を作成",
    },
    "snapshot.latest.stale_for_interview": {
        "summary": "Interview は古い snapshot に基づいています。",
        "detail": (
            "進行中の Interview session #{interview_session_id} は snapshot #{interview_snapshot_id} を"
            "基準にしていますが、最新の ready snapshot は #{latest_snapshot_id} です。"
        ),
        "impact": "Interview の根拠が最新 snapshot と一致していない可能性があります。",
        "remediation": "最新 snapshot を基準に Interview を見直してください。",
        "action_label": "Interview を確認",
    },
    "interview.materialized.rebuild_required": {
        "summary": "Interview の反映後に System Understanding の再 build が必要です。",
        "detail": "最新の Interview materialization が直近の完了済み build より新しいため、理解結果を更新する必要があります。",
        "remediation": "System Understanding で Build / Refresh を実行してください。",
        "action_label": "Build / Refresh を実行",
    },
    "pipeline.docs_code_reconcile.partial": {
        "summary": "Docs-code 照合に必要なデータの一部のみが揃っています。",
        "detail": "ドキュメントの主張とコードシンボルのどちらか一方のみが揃っています。docs-code 照合には両方が必要です。",
        "impact": "System Understanding で docs-code 照合が未完了として表示されます。",
        "remediation": "System Understanding で Build / Refresh を実行してください。",
        "action_label": "Build / Refresh を実行",
    },
    "pipeline.docs_code_reconcile.not_run": {
        "summary": "Docs-code 照合が未実行です。",
        "detail": "ドキュメントの主張とコードシンボルのどちらも揃っていないため、docs-code 照合は未実行です。",
        "impact": "System Understanding で docs-code 照合が未完了として表示されます。",
        "remediation": "System Understanding で Build / Refresh を実行してください。",
        "action_label": "Build / Refresh を実行",
    },
    "runtime.connectivity.no_signal": {
        "summary": "SDK からのトレースをまだ受信していません。",
        "detail_blocking": "このシステムでは probe SDK からのトレースを一度も受信していません。承認済みの probe plan もまだありません。",
        "detail_nonblocking": (
            "このシステムでは probe SDK からのトレースを一度も受信していません。"
            "承認済みの probe plan は既にありますが、SDK からのトレースはまだ届いていません。"
        ),
        "impact": "計装経路（承認済み probe plan または実際のトレース受信）が確立していません。",
        "remediation": (
            "Probe Planner で probe plan を作成・承認するか、対象アプリケーションに "
            "@probe を組み込んで Control Server に接続してください。"
        ),
        "action_label": "Probe Planner を確認",
    },
    "proposal.experiments.undecided": {
        "summary": "未評価の experiment が {count} 件あります。",
        "detail": (
            "完了した experiment のうち {count} 件は、まだ human decision"
            "（adopted / rejected / needs_more_data）が記録されていません。"
        ),
        "impact": "評価が確定するまで、候補実装の採否判断が完了しません。",
        "remediation": "Experiments で完了した experiment の decision を記録してください。",
        "action_label": "Experiments を確認",
    },
    "proposal.probe_plans.proposed": {
        "summary": "レビュー待ちの probe plan が {count} 件あります。",
        "detail": "{count} 件の probe plan が提案されたまま、レビュー・承認されていません。",
        "impact": "承認されるまで、計装（instrumentation）経路の確立に寄与しません。",
        "remediation": "Probe Planner でレビューして承認してください。",
        "action_label": "Probe Planner でレビュー",
    },
    "proposal.probe_plans.approved_without_patch": {
        "summary": "検証済みパッチが未生成の承認済み probe plan が {count} 件あります。",
        "detail": "承認済み probe plan のうち {count} 件に、検証済みの patch がまだありません。",
        "impact": "patch が検証されるまで、対象リポジトリへの計装は完了しません。",
        "remediation": "Probe Planner で patch を生成・検証してください。",
        "action_label": "Probe Planner で patch を生成",
    },
    "repository.configuration.missing": {
        "summary": "対象 repository が設定されていません。",
        "detail": "Snapshot と System Understanding を開始するには対象 repository の設定が必要です。",
        "remediation": "Repository タブで対象 repository を設定してください。",
        "action_label": "Repository を設定",
    },
    "repository.head.unreadable": {
        "summary": "Repository HEAD を読み取れません。",
        "remediation": "Repository のパスとアクセス権を確認してください。",
        "action_label": "Repository 設定を確認",
    },
    "repository.snapshot.stale": {
        "summary": "HEAD が最新 snapshot より進んでいます。",
        "detail": "HEAD {current_head} は最新 ready snapshot #{snapshot_id} ({commit_sha}) と異なります。",
        "remediation": "Repository で新しい snapshot を作成してください。",
        "action_label": "Snapshot を作成",
    },
    "repository.working_tree.dirty": {
        "summary": "未コミット差分があります。",
        "detail": "Working tree に {dirty_count} 件の未コミット変更があります。",
        "remediation": "patch 適用や snapshot の前に commit、stash、または差分の確認をしてください。",
        "action_label": "Repository を確認",
    },
}

# Fixed, hand-enumerated list of every non-pipeline-family, non-understanding,
# non-diagnostic-projection state_id system_state.py can emit -- for the
# completeness test.
ALL_FIXED_STATE_IDS: List[str] = sorted(STATE_MESSAGES.keys())


def state_message(state_id: str) -> Dict[str, str]:
    return _get(STATE_MESSAGES, state_id, catalog_name="STATE_MESSAGES")


# Generic action_label template for a diagnostic-projected StateItem
# (system_state._diagnostic_state_item): "「<check title>」を修正".
DIAGNOSTIC_STATE_ACTION_LABEL_TEMPLATE = "「{title}」を修正"


# ---------------------------------------------------------------------------
# system_state.py: user_phase display labels (Issue #240 -- additive
# `phases[].label`; the Dashboard's USER_PHASE_LABELS becomes a fallback).
# ---------------------------------------------------------------------------

PHASE_LABELS: Dict[str, str] = {
    "setup": "必要最低限の設定",
    "preparation": "診断準備",
    "diagnosis": "診断",
}


def phase_label(phase: str) -> str:
    return PHASE_LABELS.get(phase, phase)


# ---------------------------------------------------------------------------
# system_diagnostics.py: static titles, keyed by check_id.
# ---------------------------------------------------------------------------

CHECK_TITLES: Dict[str, str] = {
    "repository_roots": "リポジトリルートの設定",
    "repository_config": "このシステムのリポジトリ設定",
    "snapshot_status": "利用可能なリポジトリ snapshot",
    "database_storage": "データベースストレージ",
    "auth_scope": "認証とシステムスコープ",
    "llm_base_config": "LLM プロバイダ設定",
    "intelligence_llm_config": "Intelligence 用 reasoning モデル設定",
    "llm_last_run": "直近の reasoning モデル実行",
    "system_purpose": "System Purpose の定義",
    "system_capabilities": "主な機能 / Core Capabilities の把握",
    "pipeline_symbol_index": "シンボル索引の実行",
    "pipeline_entrypoint_index": "エントリポイント索引の実行",
    "pipeline_documentation_index": "ドキュメント索引のビルドステップ",
    "pipeline_understanding_graph": "Understanding グラフ（ドキュメントの主張）",
    "pipeline_capability_hierarchy": "capability 階層の実行",
}


def check_title(check_id: str) -> str:
    try:
        return CHECK_TITLES[check_id]
    except KeyError as exc:
        raise KeyError(
            f"state_messages.CHECK_TITLES: no title for check_id={check_id!r}. "
            "Add one instead of falling back to hardcoded/English text (Issue #240)."
        ) from exc


# ---------------------------------------------------------------------------
# system_diagnostics.py: per-check_id branch text (only for checks whose
# wording is check-specific rather than produced by one of the shared
# structural helpers cataloged in SHARED_CHECK_MESSAGES below).
# ---------------------------------------------------------------------------

CHECK_MESSAGES: Dict[str, Dict[str, Dict[str, str]]] = {
    "repository_roots": {
        "missing_env": {
            "detail": "環境変数 PROBE_REPOSITORY_ROOTS が設定されていません。",
            "impact": (
                "リポジトリへのアクセスがすべて無効になり、リポジトリ設定・"
                "snapshot 作成・コードを読む System Understanding の各ステップが"
                "失敗または未実行のままになります。"
            ),
            "remediation": (
                "Git リポジトリが置かれている絶対パスを PROBE_REPOSITORY_ROOTS に"
                "設定し（複数指定はパス区切り文字で連結）、Control Server を"
                "再起動してください。"
            ),
        },
        "problems": {
            "detail": "PROBE_REPOSITORY_ROOTS に問題があります: {problems}。",
            "impact": "これらのルート配下のリポジトリを検出・snapshot 化できません。",
            "remediation": (
                "PROBE_REPOSITORY_ROOTS のパスを修正するか、ディレクトリを作成して"
                "Control Server プロセスに読み取り権限を付与してください。"
            ),
        },
        "ok": {
            "detail": "{count} 個のリポジトリルートが存在し、読み取り可能です。",
        },
    },
    "repository_config": {
        "missing": {
            "detail": "選択中のシステムに解析対象のリポジトリが設定されていません。",
            "impact": "snapshot を作成できないため、System Understanding パイプライン全体が未実行のままになります。",
            "remediation": "Repository タブを開き、解析対象のリポジトリパスを選択してください。",
        },
        "invalid": {
            "detail": "設定されたリポジトリパス {repo_path}: {problems}。",
            "impact": "このシステムでの新規 snapshot 作成とリポジトリ読み取りが失敗します。",
            "remediation": (
                "設定したリポジトリが存在し、読み取り可能で、Git リポジトリになる"
                "ようにマウント/パスを修正するか、Repository タブでリポジトリを"
                "設定し直してください。"
            ),
        },
        "ok": {
            "detail": "リポジトリパス {repo_path} は存在し、読み取り可能な Git リポジトリです。",
        },
    },
    "snapshot_status": {
        "no_snapshot": {
            "detail": "このシステムではまだ snapshot が作成されていません。",
            "impact": "ドキュメント索引・シンボル索引・エントリポイント検出・capability 階層の生成には ready 状態の snapshot が必要です。",
            "remediation": "Repository タブの Snapshots から snapshot を作成してください。",
        },
        "not_ready": {
            "detail": "ready な snapshot がありません。最新の snapshot #{latest_id} の状態は '{latest_status}' です。",
            "impact": "snapshot の内容を読むすべてのパイプラインステップがブロックされます。",
            "remediation": (
                "Repository タブの Snapshots から snapshot 作成を再試行してください。"
                "失敗が続く場合は、下記の直近のエラーとリポジトリパスの診断を"
                "確認してください。"
            ),
        },
        "zero_indexed": {
            "detail": (
                "最新の ready snapshot #{ready_id} には索引付けされたファイルが "
                "0 件しかありません。include/exclude パターンですべて除外されている"
                "可能性があります。"
            ),
            "impact": "ドラフト生成・シンボル索引・エントリポイント検出の結果が空になります。",
            "remediation": "Repository タブの include/exclude パターンを見直し、snapshot を作成し直してください。",
        },
        "ok": {
            "detail": "最新の ready snapshot #{ready_id} には索引付けされたファイルが {indexed_count} 件あります。",
        },
    },
    "database_storage": {
        "problems": {
            "impact": (
                "トレース・ポリシー・snapshot・intelligence run を永続化できず、"
                "ほとんどの書き込み操作が失敗します。"
            ),
            "remediation": (
                "PROBE_DB_PATH を書き込み可能な場所に設定するか、ディレクトリ/"
                "ファイルの権限を修正してください。"
            ),
        },
        "ok": {
            "detail": "データベースパス {path} は読み書き可能です。",
        },
    },
    "auth_scope": {
        "unknown_system": {
            "detail": "選択中のシステム id {system_id} が存在しません。",
            "impact": "システムスコープのリクエストがすべて 404 になります。",
            "remediation": "ヘッダーで既存のシステムを選択するか、新しいシステムを作成してください。",
        },
        "open_mode": {
            "detail": (
                "アクティブなユーザーがおらず CONTROL_API_KEYS も未設定です。"
                "サーバーは認証なし（MVP 互換モード）で動作しています。"
            ),
            "impact": "Control Server に到達できる誰もが全アクセス権を持ちます。",
            "remediation": (
                "CONTROL_ADMIN_USERNAME / CONTROL_ADMIN_PASSWORD を設定して管理者"
                "ユーザーを初期化するか、CONTROL_API_KEYS を設定してください。"
            ),
        },
        "ok": {
            "detail": "選択中のシステム '{system_name}' は存在します。アクティブユーザー {users} 名{legacy_note}。",
            "legacy_note": "、レガシー API キー設定済み",
        },
    },
    "llm_base_config": {
        "unsupported_provider": {
            "detail_fragment": "LLM_PROVIDER={provider!r} はサポートされていないプロバイダです（{known}）",
        },
        "problems": {
            "impact": "プロバイダや API キーが不正な場合、Generate & Evaluate とすべての reasoning モデル機能が呼び出し時に失敗します。",
            "remediation": (
                "LLM_PROVIDER を openai/anthropic/gemini/mock のいずれかに設定し、"
                "mock 以外のプロバイダでは LLM_API_KEY（またはプロバイダ固有のキー）を"
                "設定してください。"
            ),
        },
        "mock": {
            "detail": (
                "LLM_PROVIDER=mock: すべての LLM 出力はテスト/ローカル動作確認用の"
                "決定的なモックデータで、reasoning が必要なパイプラインステップは"
                "ブロックされます。"
            ),
            "impact": "モック出力を実際の解析として扱わないでください。",
            "remediation": (
                "LLM_PROVIDER を openai/anthropic/gemini/mock のいずれかに設定し、"
                "mock 以外のプロバイダでは LLM_API_KEY（またはプロバイダ固有のキー）を"
                "設定してください。"
            ),
        },
        "ok": {
            "detail": "LLM_PROVIDER={provider} と使用可能な API キー設定です。",
        },
    },
    "intelligence_llm_config": {
        "unsupported_provider": {
            "detail_fragment": "実効的な intelligence プロバイダ {provider!r}{fallback_note} はサポートされていません（{known}）",
        },
        "wrong_family": {
            "detail_fragment": "モデル {model!r} は実効プロバイダ {provider!r}{fallback_note} とは別プロバイダのモデルファミリに属しています",
        },
        "unknown_family": {
            "detail_fragment": (
                "モデル {model!r} はプロバイダ {provider!r} の既知のモデルファミリに"
                "一致しません。不正なモデル ID の可能性があり、reasoning 能力を"
                "静的に検証できません"
            ),
        },
        "mock": {
            "detail_fragment": (
                "実効的な intelligence プロバイダが 'mock' です。reasoning が必要な"
                "ステップはブロックされ、出力は明示的なモックデータです"
            ),
        },
        "not_reasoning_model": {
            "detail_fragment": (
                "モデル {model!r} は reasoning 非対応です。ドキュメント索引・"
                "claim スキャン・capability 階層の生成には reasoning モデルが必要で、"
                "ブロックされます"
            ),
        },
        "unset_defaults_to_generic": {
            "detail_fragment": (
                "INTELLIGENCE_LLM_* が未設定です。intelligence 機能は汎用の "
                "LLM_PROVIDER/LLM_MODEL 設定を使用します"
            ),
        },
        "ok": {
            "detail": "実効的な intelligence モデル: {provider}/{model}{fallback_note}。",
        },
        "problems": {
            "impact": (
                "有効な reasoning モデルがないと claim スキャン・docs-code 照合・"
                "capability 階層の生成が失敗またはブロックされ、ヒューリスティックな"
                "フォールバックはありません。"
            ),
            "remediation": (
                "INTELLIGENCE_LLM_PROVIDER と INTELLIGENCE_LLM_MODEL を reasoning 対応の"
                "プロバイダ/モデルの組み合わせ（および対応する API キー）に設定し、"
                "System Understanding のビルドを再実行してください。"
            ),
        },
    },
    "llm_last_run": {
        "no_snapshot": {
            "detail": "最新 snapshot がないため、この snapshot に紐づく reasoning 実行はありません。",
            "impact": "snapshot を作成後に reasoning 実行の状態を確認できます。",
            "remediation": "Repository で snapshot を作成してください。",
        },
        "no_run_yet": {
            "detail": (
                "このシステムではまだ reasoning モデルの実行が記録されていないため、"
                "実行時の問題（タイムアウト・認証・不正なモデル・パースエラー）は"
                "観測されていません。これは設定したモデルの疎通が未確認という"
                "情報であり、他の warning / error 診断の原因を示すものではありません。"
            ),
            "impact": "設定は正しく見えても、呼び出し時に失敗する可能性があります。",
            "remediation": (
                "reasoning モデルの実行は、System Understanding のビルド"
                "（ドキュメント主張スキャン）・ドラフト生成・Interview の対話で"
                "記録されます。他に warning / error の診断が表示されている場合は、"
                "先にそちらの『次の操作』を実施してください。"
            ),
        },
        "failed": {
            "detail": "直近の reasoning 実行（#{run_id}, {run_type}）が失敗しました。",
            "impact": "reasoning に基づく成果物（ドラフト・claim・capability 階層）が生成されていないか、古くなっています。",
        },
        "ok": {
            "detail": "直近の reasoning 実行（#{run_id}, {run_type}）の状態は '{status}'{mock_note} です。",
        },
        "failure_guidance_evidence": {
            "remediation": (
                "下記の直近のエラーは API キー・モデル ID・タイムアウト設定ではなく、"
                "reasoning モデルの構造化出力に含まれる evidence_refs が許可された"
                "snapshot 範囲外だったことを示しています。無効な evidence_refs は保存前に"
                "破棄されるべきなので、該当機能を再実行し、同じ validation error が続く"
                "場合は evidence_refs の生成/検証ロジックを確認してください。"
            ),
        },
        "failure_guidance_generic": {
            "remediation": (
                "下記の直近のエラーを確認し、指し示す設定（API キー・モデル ID・"
                "タイムアウト）を修正してから、System Understanding でビルドを"
                "再実行してください。"
            ),
        },
    },
    "pipeline_capability_hierarchy": {
        "zero_capabilities": {
            "detail_suffix": " ただし capability ノードが 0 件のため、Core Capabilities は未定義のままです。",
            "impact": "Core Capabilities が未定義のため、probe 設計や flow 探索の前提が欠けています。",
            "remediation": (
                "Interview で Core Capabilities を確認するか、対象リポジトリに "
                "`probe-agent:` メタデータを追加してから Build / Refresh を再実行してください。"
            ),
        },
    },
}


def check_message(check_id: str, variant: str) -> Dict[str, str]:
    checks = _get(CHECK_MESSAGES, check_id, catalog_name="CHECK_MESSAGES")
    try:
        return checks[variant]
    except KeyError as exc:
        raise KeyError(
            f"state_messages.CHECK_MESSAGES[{check_id!r}]: no variant {variant!r}. "
            "Add one instead of falling back to hardcoded/English text (Issue #240)."
        ) from exc


# Conditional problem sub-fragments for the repository/database checks. Each
# check assembles a "; ".join(...) of the applicable fragments into its
# detail; keeping them here (rather than inline f-strings) means every piece
# of user-facing diagnostic copy has one home. Keyed by "<check_id>.<cond>".
CHECK_PROBLEM_FRAGMENTS: Dict[str, str] = {
    "repository_roots.missing_dirs": "存在しないディレクトリ: {dirs}",
    "repository_roots.unreadable_dirs": "読み取りできないディレクトリ: {dirs}",
    "repository_config.not_a_dir": "パスが存在しないかディレクトリではありません",
    "repository_config.unreadable": "パスを読み取りできません",
    "repository_config.not_git": "Git リポジトリではありません（.git がありません）",
    "database_storage.dir_missing": "データベースのディレクトリが存在しません: {directory}",
    "database_storage.dir_unwritable": "データベースのディレクトリに書き込みできません: {directory}",
    "database_storage.file_unreadable": "データベースファイルを読み取りできません",
    "database_storage.file_unwritable": "データベースファイルに書き込みできません",
}


def check_problem_fragment(key: str, **kwargs: Any) -> str:
    try:
        template = CHECK_PROBLEM_FRAGMENTS[key]
    except KeyError as exc:
        raise KeyError(
            f"state_messages.CHECK_PROBLEM_FRAGMENTS: no entry for key={key!r}. "
            "Add one instead of falling back to hardcoded/English text (Issue #240)."
        ) from exc
    return template.format(**kwargs) if kwargs else template


# ---------------------------------------------------------------------------
# system_diagnostics.py: shared structural helper templates. These back
# _no_ready_snapshot_check, _reasoning_unavailable_check (detail passed in by
# the caller, only the impact/remediation are generic here),
# _run_backed_pipeline_check, _artifact_backed_pipeline_check, and
# _build_step_pipeline_check -- functions that are already shared by several
# check_ids in the source, so their wording is generic rather than
# check_id-specific (matching the existing code structure 1:1).
# ---------------------------------------------------------------------------

SHARED_CHECK_MESSAGES: Dict[str, Dict[str, str]] = {
    "no_ready_snapshot": {
        "detail": "ready な snapshot がないため、このステップは実行できません。",
        "impact": "snapshot が作成されるまでこのステップは未実行のままです。",
        "remediation": "まず Repository タブから snapshot を作成してください。",
    },
    "reasoning_unavailable": {
        "impact": "System Understanding でこのステップはブロック/未実行として表示されます。",
        "remediation": "intelligence 用 reasoning モデル設定（上記の LLM チェックを参照）を修正してからビルドを実行してください。",
    },
    "run_backed.not_run": {
        "detail": "このステップは現在の snapshot に対して実行されていません。",
        "impact": "System Understanding でこのステップは未実行として表示されます。",
    },
    "run_backed.not_run_blocked_reasoning": {
        "detail": "このステップは一度も実行されておらず、reasoning モデルが必要ですが設定されていません。",
    },
    "run_backed.completed": {
        "detail": "直近の実行（#{run_id}, {run_type}）は完了しました。",
    },
    "run_backed.error": {
        "detail": "直近の実行（#{run_id}, {run_type}）の状態は '{status}' です。",
        "impact": "このステップの成果物が欠落しているか古くなっています。",
        "remediation": "下記の直近のエラーを確認し、根本原因を修正してから、System Understanding でビルドを再実行してください。",
    },
    "artifact_backed.not_run": {
        "detail": "現在の snapshot に対する成果物がありません。",
        "impact": "System Understanding でこのステップは未実行として表示されます。",
    },
    "artifact_backed.not_run_blocked_reasoning": {
        "detail": "現在の snapshot に対する成果物がなく、必要な reasoning モデルが設定されていません。",
    },
    "artifact_backed.ok": {
        "detail": "現在の snapshot に対する成果物が存在します。",
    },
    "build_step.not_run": {
        "detail": "このビルドステップは現在の snapshot に対して実行されていません。",
        "impact": "System Understanding でこのステップは未実行として表示されます。",
    },
    "build_step.completed": {
        "detail": "直近のビルドステップ（#{run_id}, {step}）は完了しました。",
    },
    "build_step.error": {
        "detail": "直近のビルドステップ（#{run_id}, {step}）の状態は '{status}' です。",
        "impact": "このステップの成果物が欠落しているか古くなっています。",
        "remediation": "下記のビルドステップのエラーを確認し、根本原因を修正してからビルドを再実行してください。",
    },
    "baseline_ok.current": {
        "detail": "{label} は現在の snapshot で確認済みです。",
    },
    "baseline_ok.reusable": {
        "detail": (
            "{label} は snapshot #{baseline_snapshot_id} の確認済み理解を再利用できます。"
            "今回の差分には再確認が必要な根拠変更は見つかりませんでした。"
        ),
    },
    "baseline_ok.other": {
        "detail": "{label} は確認済みです。",
    },
    "baseline_impacted": {
        "detail": (
            "{target} は snapshot #{baseline_snapshot_id} で確認済みですが、"
            "最新 snapshot との差分に影響候補があります。{reason}"
        ),
        "impact": (
            "確認済み理解をそのまま使えるか判断が必要です。"
            "目的・主要機能が変わっていない場合は Interview で再確認してください。"
        ),
        "remediation": "Interview で {target} を再確認してください。",
        "title_purpose": "System Purpose の再確認",
        "title_capabilities": "主な機能 / Core Capabilities の再確認",
        "default_reason": "前回確認済み理解に影響しうる差分があります。",
    },
    "unconfirmed_understanding": {
        "detail": (
            "Interview に未確認の {target} 候補が {count} 件あります。"
            "baseline として再利用するには、開発者による明示的な確認が必要です。"
        ),
        "impact": "未確認の理解は、snapshot 更新後の差分診断や probe 設計の前提としてまだ採用されません。",
        "remediation": "Interview で {target} を確認済みにしてください。",
        "title_purpose": "System Purpose の確認",
        "title_capabilities": "主な機能 / Core Capabilities の確認",
    },
    "system_purpose.satisfied": {
        "detail": "System Purpose が定義されています。",
    },
    "system_purpose.missing": {
        "detail": "System Purpose が未定義です。Pipeline のステップが完了していても、このシステムが何のためのものかが定義されていません。",
        "impact": "probe 設計・flow 探索・改善提案・ユーザー意図との整合の前提となる根幹情報が欠けています。",
        "remediation": "Interview で System Purpose を定義・確認してください。",
    },
    "system_capabilities.satisfied": {
        "detail": "{count} 件の Core Capability が定義されています。",
    },
    "system_capabilities.missing": {
        "detail": "対象システムの主な機能（Core Capabilities）が未定義・空です。",
        "impact": "probe 候補選定・flow 探索・改善提案の前提となる、システムの主要な機能の把握ができていません。",
        "remediation": "Interview で主な機能を特定するか、Capability Map で capability 階層を確認してください。",
    },
}


def shared_check_message(key: str) -> Dict[str, str]:
    return _get(SHARED_CHECK_MESSAGES, key, catalog_name="SHARED_CHECK_MESSAGES")


ALL_CHECK_IDS: List[str] = sorted(CHECK_TITLES.keys())


# ---------------------------------------------------------------------------
# system_understanding_service.py: pipeline step display labels (Issue #240
# -- additive PipelineStep.label; the Dashboard's STEP_LABELS becomes a
# fallback).
# ---------------------------------------------------------------------------

PIPELINE_STEP_LABELS: Dict[str, str] = {
    "repository_configured": "リポジトリ設定",
    "snapshot_ready": "Snapshot 作成済み",
    "documentation_indexed": "ドキュメント索引",
    "documentation_claims_scanned": "ドキュメント主張スキャン",
    "symbols_indexed": "コードシンボル索引",
    "entrypoints_discovered": "エントリポイント検出",
    "docs_code_reconciled": "Docs-code 照合",
    "capability_hierarchy_ready": "Capability 階層",
}


def pipeline_step_label(step: str) -> str:
    try:
        return PIPELINE_STEP_LABELS[step]
    except KeyError as exc:
        raise KeyError(
            f"state_messages.PIPELINE_STEP_LABELS: no label for step={step!r}. "
            "Add one instead of falling back to hardcoded/English text (Issue #240)."
        ) from exc


# The "Build / Refresh を実行して…" remediation each pipeline diagnostic check
# shows when its step has not run yet, keyed by the diagnostic check_id
# (system_diagnostics.run_system_diagnostics passes these at call time).
PIPELINE_NOT_RUN_REMEDIATION: Dict[str, str] = {
    "pipeline_symbol_index": "System Understanding で Build / Refresh を実行してコードシンボルを索引付けしてください。",
    "pipeline_entrypoint_index": "System Understanding で Build / Refresh を実行してエントリポイントを検出してください。",
    "pipeline_documentation_index": "System Understanding で Build / Refresh を実行してドキュメントチャンクを索引付けしてください。",
    "pipeline_understanding_graph": "System Understanding で Build / Refresh を実行してドキュメントの主張をスキャンしてください。",
    "pipeline_capability_hierarchy": "System Understanding で Build / Refresh を実行して capability 階層を生成してください。",
}


def pipeline_not_run_remediation(check_id: str) -> str:
    try:
        return PIPELINE_NOT_RUN_REMEDIATION[check_id]
    except KeyError as exc:
        raise KeyError(
            f"state_messages.PIPELINE_NOT_RUN_REMEDIATION: no entry for check_id={check_id!r}. "
            "Add one instead of falling back to hardcoded/English text (Issue #240)."
        ) from exc


# ---------------------------------------------------------------------------
# system_understanding_service.py: PipelineStep.detail text (Issue #240 --
# was English; dynamic upstream error/status text is still interpolated
# verbatim as a named parameter since it is a raw system fact, not authored
# copy).
# ---------------------------------------------------------------------------

PIPELINE_STEP_DETAIL_MESSAGES: Dict[str, str] = {
    "documentation_indexed.no_chunks": "ドキュメントチャンクが見つかりませんでした。",
    "documentation_indexed.build_step_failed_default": "documentation_index 処理が失敗しました。",
    "documentation_indexed.build_step_blocked_default": "documentation_index 処理がブロックされています。",
    "documentation_indexed.run_status": "実行ステータス: {status}",
    "documentation_claims_scanned.blocked": "reasoning モデルが設定されていないため実行できません。",
    "symbols_indexed.run_status": "実行ステータス: {status}",
    "docs_code_reconciled.partial": "必要なデータの一部のみが揃っています。",
    "capability_hierarchy_ready.empty": (
        "capability 階層の実行は完了しましたが、capability が 0 件です。"
        "対象リポジトリに `probe-agent:` docstring メタデータが見つからなかったことが原因です。"
        "Interview の提案をレビュー・承認し、レビュー用差分を対象リポジトリへ適用してから"
        "新しい snapshot を作成し、Build / Refresh を実行してください。"
    ),
    "capability_hierarchy_ready.status": "ステータス: {status}",
    "capability_hierarchy_ready.blocked": "reasoning モデルが設定されていないため実行できません。",
}


def pipeline_step_detail(key: str, **kwargs: Any) -> str:
    try:
        template = PIPELINE_STEP_DETAIL_MESSAGES[key]
    except KeyError as exc:
        raise KeyError(
            f"state_messages.PIPELINE_STEP_DETAIL_MESSAGES: no entry for key={key!r}. "
            "Add one instead of falling back to hardcoded/English text (Issue #240)."
        ) from exc
    return template.format(**kwargs) if kwargs else template


# ---------------------------------------------------------------------------
# system_understanding_service.py: Hub stage display label + description
# (Issue #240 -- additive StageStatus.label/description; the Dashboard's
# STAGE_LABELS/STAGE_DESCRIPTIONS become a fallback).
# ---------------------------------------------------------------------------

STAGE_MESSAGES: Dict[str, Dict[str, str]] = {
    "understand": {
        "label": "理解する",
        "description": "リポジトリから発見された docs・symbol・capability・docs-code gap。",
    },
    "observe": {
        "label": "観測箇所を決める",
        "description": "計装先を選ぶ前に確認すべき entrypoint と flow。",
    },
    "instrument": {
        "label": "計装する",
        "description": "観測対象の entrypoint に対する probe plan・patch・検証状態。",
    },
    "evaluate": {
        "label": "評価する",
        "description": "記録されたトレース・experiment・判断結果。",
    },
}


def stage_message(stage: str) -> Dict[str, str]:
    return _get(STAGE_MESSAGES, stage, catalog_name="STAGE_MESSAGES")


# ---------------------------------------------------------------------------
# system_understanding_service.py: docs-code gap title templates + next
# actions (Issue #240 -- were English).
# ---------------------------------------------------------------------------

GAP_TITLE_TEMPLATES: Dict[str, str] = {
    "docs_only": "ドキュメントに記載されていますが、対応する実装が見つかりません: {name}",
    "code_only": "実装されていますが、ドキュメントに記載がありません: {name}",
    "source_doc_mismatch": "ソースメタデータとドキュメントの内容が一致していません: {name}",
    "stale_explanation": "説明が古くなっている可能性があります: {name}",
    "unclassified_entrypoint": "エントリポイントが capability 階層に分類されていません: {name}",
    "missing_probe_flow": "probe flow が定義されていません: {name}",
    "missing_evidence": "ドキュメントの主張に path/line の根拠がありません: {name}",
    "ambiguous_ownership": "所有関係が曖昧です: {name}",
}

GAP_TITLE_FALLBACK_TEMPLATE = "Gap: {name}"

# Issue #199: single source of truth for "gap type -> resolution action(s)".
# See system_understanding_service.GAP_NEXT_ACTIONS for the primary/secondary
# action ordering rule; this module only owns the (now Japanese) text.
GAP_NEXT_ACTIONS: Dict[str, List[Dict[str, Any]]] = {
    "docs_only": [
        {"action": "ドキュメントの根拠を確認", "link": None},
        {"action": "実装 issue を作成", "link": None},
    ],
    "code_only": [
        {"action": "ソースシンボルを開く", "link": "/repository"},
        {"action": "ドキュメントまたはソースメタデータを追加", "link": "/interview"},
    ],
    "source_doc_mismatch": [
        {"action": "説明の更新を提案", "link": "/capability-map"},
    ],
    "stale_explanation": [
        {"action": "説明の更新を提案", "link": "/capability-map"},
    ],
    "unclassified_entrypoint": [
        {"action": "Interview を開く", "link": "/interview"},
        {"action": "ソースメタデータを追加", "link": "/interview"},
    ],
    "missing_probe_flow": [
        {"action": "Flow Explorer を開く", "link": "/flow-explorer"},
        {"action": "Probe Plan を作成", "link": "/probe-planner"},
    ],
    "missing_evidence": [
        {"action": "ドキュメント索引を改善", "link": "/repository"},
    ],
    "ambiguous_ownership": [
        {"action": "Interview で所有関係を明確化", "link": "/interview"},
    ],
}

# The one gap-card action that hands off to the Issue #107 issue-draft flow
# instead of being a plain navigate/disabled button (see gap-worklist.tsx's
# CREATE_ISSUE_ACTION).
GAP_CREATE_ISSUE_ACTION = "実装 issue を作成"

GAP_NOTE_TEMPLATES: Dict[str, str] = {
    "unclassified_entrypoint": "エントリポイント {entrypoint_type}:{entrypoint_id} は capability に分類されていません",
    "missing_probe_flow": "エントリポイント {entrypoint_type}:{entrypoint_id} は分類済みですが probe plan がありません",
    "missing_evidence": "ドキュメントの主張『{name}』に file/line の根拠がありません",
}


def gap_title(gap_type: str, name: str) -> str:
    template = GAP_TITLE_TEMPLATES.get(gap_type, GAP_TITLE_FALLBACK_TEMPLATE)
    return template.format(name=name)


def gap_next_actions(gap_type: str) -> List[Dict[str, Any]]:
    return [dict(a) for a in GAP_NEXT_ACTIONS.get(gap_type, [])]


def gap_note(key: str, **kwargs: Any) -> str:
    try:
        template = GAP_NOTE_TEMPLATES[key]
    except KeyError as exc:
        raise KeyError(
            f"state_messages.GAP_NOTE_TEMPLATES: no entry for key={key!r}. "
            "Add one instead of falling back to hardcoded/English text (Issue #240)."
        ) from exc
    return template.format(**kwargs)


# ---------------------------------------------------------------------------
# system_understanding_service.py: Hub success summary (Issue #240 --
# additive SystemUnderstandingSummary.success_summary; replaces the
# Dashboard's client-assembled `successSummary`).
# ---------------------------------------------------------------------------


def success_summary(*, done: int, total: int, symbol_count: int, entrypoint_count: int) -> str:
    return f"分析完了 — {done}/{total} ステップ ・ {symbol_count} シンボル ・ {entrypoint_count} エントリポイント"
