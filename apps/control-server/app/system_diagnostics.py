"""Deterministic system settings diagnostics (Issue #101 / #115).

Static, LLM-free health checks for required configuration: environment
variables, filesystem paths and permissions, provider/model consistency,
and pipeline prerequisites. Failures that can only be observed at runtime
(LLM timeouts, auth errors, snapshot/index failures) are surfaced verbatim
from the most recent persisted run records; remediation is selected only by
finite, literal error classes, never by LLM or open-ended inference.

Every decision in this module is a finite-set or structural validation
(Principle 6): env var presence, enum membership, path existence and
read/write permission, known model-family prefix matching, and persisted
run status values.

Issue #115 makes the user-facing text Japanese and, for each check, records
where the problem is fixed so the Dashboard can route the user there:

- ``fix_kind`` is a finite value, ``navigate`` or ``dialog``. ``navigate``
  means an in-app control fixes the problem (repository config, snapshot
  creation, Build / Refresh); ``dialog`` means the fix is an environment
  variable / restart that has no in-app control and must be explained in a
  dialog.
- ``fix_page`` / ``fix_anchor`` name the Dashboard route and the UI element
  to highlight. Both are members of small explicit sets defined below and
  are chosen structurally per check branch — no free-text inference.

probe-agent:
  role: Deterministic system settings diagnostics service
  capability: system-configuration-health
  element_type: core
  consumers: [dashboard, control-server]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify that missing/invalid required configuration and last observed run failures are reported in Japanese with impact, remediation, and a deterministic fix location, without any LLM call.
"""

from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from . import system_state
from .db import get_conn
from .llm import PROVIDER_KEY_ENV, is_reasoning_model

# Severity vocabulary from Issue #101. Order = worst first.
SEVERITY_ORDER = ["error", "blocked", "warning", "unknown", "ok"]

# How the Dashboard should route the user to the fix (finite set, Issue #115).
FIX_KIND_NAVIGATE = "navigate"
FIX_KIND_DIALOG = "dialog"

# Dashboard routes.
PAGE_REPOSITORY = "/repository"
PAGE_SYSTEM_UNDERSTANDING = "/system-understanding"
PAGE_ADMIN = "/admin"
PAGE_INTERVIEW = "/interview"
PAGE_CAPABILITY_MAP = "/capability-map"

# Fix anchors. These must match the ``diag-anchor`` attributes rendered by the
# Dashboard so the target UI can be highlighted. Finite, explicit set.
ANCHOR_REPO_CONFIG = "repo-config"
ANCHOR_REPO_PATTERNS = "repo-patterns"
ANCHOR_SNAPSHOT_CREATE = "snapshot-create"
ANCHOR_BUILD = "build"
ANCHOR_INTERVIEW_PURPOSE = "interview-purpose"
ANCHOR_INTERVIEW_CAPABILITIES = "interview-capabilities"

KNOWN_PROVIDERS = {"openai", "anthropic", "gemini", "mock"}

# Known model-family prefixes per provider. Membership here is a finite
# structural check; it does not guarantee the model id is valid upstream —
# runtime validity shows up in last observed run errors instead.
MODEL_FAMILY_PREFIXES: Dict[str, tuple] = {
    "openai": ("gpt-", "o1", "o3", "o4"),
    "anthropic": ("claude-",),
    "gemini": ("gemini-",),
    "mock": ("mock",),
}

_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "gemini": "gemini-1.5-flash",
    "mock": "mock",
}


@dataclass
class LastObservedError:
    source: str
    status: str
    error: Optional[str] = None
    observed_at: Optional[float] = None


@dataclass
class DiagnosticCheck:
    check_id: str
    category: str  # repository | database | auth | llm | pipeline | understanding
    title: str
    severity: str  # ok | warning | error | blocked | unknown
    detail: str
    impact: str
    remediation: str
    related_env: List[str] = field(default_factory=list)
    related_paths: List[str] = field(default_factory=list)
    related_pages: List[str] = field(default_factory=list)
    related_pipeline_steps: List[str] = field(default_factory=list)
    last_observed_error: Optional[LastObservedError] = None
    decision_method: str = "deterministic"
    # Issue #115: where the user fixes the problem.
    fix_kind: str = FIX_KIND_DIALOG
    fix_page: Optional[str] = None
    fix_anchor: Optional[str] = None


@dataclass
class SystemDiagnosticsReport:
    system_id: int
    generated_at: float
    overall_severity: str
    severity_counts: Dict[str, int]
    checks: List[DiagnosticCheck] = field(default_factory=list)


def _worst_severity(severities: List[str]) -> str:
    for level in SEVERITY_ORDER:
        if level in severities:
            return level
    return "ok"


def _effective_intelligence_provider_model() -> tuple:
    provider = (
        os.getenv("INTELLIGENCE_LLM_PROVIDER") or os.getenv("LLM_PROVIDER", "openai")
    ).strip().lower()
    model = (os.getenv("INTELLIGENCE_LLM_MODEL") or os.getenv("LLM_MODEL") or "").strip()
    if not model:
        model = _DEFAULT_MODELS.get(provider, "gpt-4o-mini")
    return provider, model


def _model_family_matches(provider: str, model: str) -> Optional[bool]:
    """True/False for known families; None when the model matches no known family."""
    prefixes = MODEL_FAMILY_PREFIXES.get(provider)
    if prefixes is None:
        return None
    if model.lower().startswith(prefixes):
        return True
    for other_provider, other_prefixes in MODEL_FAMILY_PREFIXES.items():
        if other_provider != provider and model.lower().startswith(other_prefixes):
            return False
    return None


def _check_repository_roots() -> DiagnosticCheck:
    raw = os.getenv("PROBE_REPOSITORY_ROOTS", "").strip()
    if not raw:
        return DiagnosticCheck(
            check_id="repository_roots",
            category="repository",
            title="リポジトリルートの設定",
            severity="error",
            detail="環境変数 PROBE_REPOSITORY_ROOTS が設定されていません。",
            impact=(
                "リポジトリへのアクセスがすべて無効になり、リポジトリ設定・"
                "snapshot 作成・コードを読む System Understanding の各ステップが"
                "失敗または未実行のままになります。"
            ),
            remediation=(
                "Git リポジトリが置かれている絶対パスを PROBE_REPOSITORY_ROOTS に"
                "設定し（複数指定はパス区切り文字で連結）、Control Server を"
                "再起動してください。"
            ),
            related_env=["PROBE_REPOSITORY_ROOTS"],
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
            fix_kind=FIX_KIND_DIALOG,
        )
    roots = [r.strip() for r in raw.split(os.pathsep) if r.strip()]
    missing = [r for r in roots if not os.path.isdir(r)]
    unreadable = [
        r for r in roots if os.path.isdir(r) and not os.access(r, os.R_OK)
    ]
    if missing or unreadable:
        problems = []
        if missing:
            problems.append(f"存在しないディレクトリ: {', '.join(missing)}")
        if unreadable:
            problems.append(f"読み取りできないディレクトリ: {', '.join(unreadable)}")
        return DiagnosticCheck(
            check_id="repository_roots",
            category="repository",
            title="リポジトリルートの設定",
            severity="error",
            detail=f"PROBE_REPOSITORY_ROOTS に問題があります: {'; '.join(problems)}。",
            impact="これらのルート配下のリポジトリを検出・snapshot 化できません。",
            remediation=(
                "PROBE_REPOSITORY_ROOTS のパスを修正するか、ディレクトリを作成して"
                "Control Server プロセスに読み取り権限を付与してください。"
            ),
            related_env=["PROBE_REPOSITORY_ROOTS"],
            related_paths=missing + unreadable,
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
            fix_kind=FIX_KIND_DIALOG,
        )
    return DiagnosticCheck(
        check_id="repository_roots",
        category="repository",
        title="リポジトリルートの設定",
        severity="ok",
        detail=f"{len(roots)} 個のリポジトリルートが存在し、読み取り可能です。",
        impact="",
        remediation="",
        related_env=["PROBE_REPOSITORY_ROOTS"],
        related_paths=roots,
        related_pages=[PAGE_REPOSITORY],
        related_pipeline_steps=["repository_configured"],
    )


def _check_repository_config(conn, system_id: int) -> DiagnosticCheck:
    row = conn.execute(
        "SELECT repo_path FROM repository_configs WHERE system_id = ?",
        (system_id,),
    ).fetchone()
    if row is None:
        return DiagnosticCheck(
            check_id="repository_config",
            category="repository",
            title="このシステムのリポジトリ設定",
            severity="warning",
            detail="選択中のシステムに解析対象のリポジトリが設定されていません。",
            impact=(
                "snapshot を作成できないため、System Understanding パイプライン"
                "全体が未実行のままになります。"
            ),
            remediation="Repository タブを開き、解析対象のリポジトリパスを選択してください。",
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_REPOSITORY,
            fix_anchor=ANCHOR_REPO_CONFIG,
        )
    repo_path = row["repo_path"]
    problems: List[str] = []
    if not os.path.isdir(repo_path):
        problems.append("パスが存在しないかディレクトリではありません")
    else:
        if not os.access(repo_path, os.R_OK):
            problems.append("パスを読み取りできません")
        if not os.path.exists(os.path.join(repo_path, ".git")):
            problems.append("Git リポジトリではありません（.git がありません）")
    if problems:
        return DiagnosticCheck(
            check_id="repository_config",
            category="repository",
            title="このシステムのリポジトリ設定",
            severity="error",
            detail=f"設定されたリポジトリパス {repo_path}: {'; '.join(problems)}。",
            impact="このシステムでの新規 snapshot 作成とリポジトリ読み取りが失敗します。",
            remediation=(
                "設定したリポジトリが存在し、読み取り可能で、Git リポジトリになる"
                "ようにマウント/パスを修正するか、Repository タブでリポジトリを"
                "設定し直してください。"
            ),
            related_env=["PROBE_REPOSITORY_ROOTS"],
            related_paths=[repo_path],
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_REPOSITORY,
            fix_anchor=ANCHOR_REPO_CONFIG,
        )
    return DiagnosticCheck(
        check_id="repository_config",
        category="repository",
        title="このシステムのリポジトリ設定",
        severity="ok",
        detail=f"リポジトリパス {repo_path} は存在し、読み取り可能な Git リポジトリです。",
        impact="",
        remediation="",
        related_paths=[repo_path],
        related_pages=[PAGE_REPOSITORY],
        related_pipeline_steps=["repository_configured"],
    )


def _check_snapshot_status(conn, system_id: int) -> DiagnosticCheck:
    latest = conn.execute(
        "SELECT id, status, file_count, error_summary, completed_at, created_at "
        "FROM repository_snapshots WHERE system_id = ? ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    ready = conn.execute(
        "SELECT id, file_count FROM repository_snapshots "
        "WHERE system_id = ? AND status = 'ready' ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()

    last_error = None
    if latest is not None and latest["status"] not in ("ready", "indexing"):
        last_error = LastObservedError(
            source=f"repository_snapshots#{latest['id']}",
            status=latest["status"],
            error=latest["error_summary"],
            observed_at=latest["completed_at"] or latest["created_at"],
        )

    if ready is None:
        if latest is None:
            return DiagnosticCheck(
                check_id="snapshot_status",
                category="repository",
                title="利用可能なリポジトリ snapshot",
                severity="warning",
                detail="このシステムではまだ snapshot が作成されていません。",
                impact=(
                    "ドキュメント索引・シンボル索引・エントリポイント検出・"
                    "capability 階層の生成には ready 状態の snapshot が必要です。"
                ),
                remediation="Repository タブの Snapshots から snapshot を作成してください。",
                related_pages=[PAGE_REPOSITORY],
                related_pipeline_steps=["snapshot_ready"],
                fix_kind=FIX_KIND_NAVIGATE,
                fix_page=PAGE_REPOSITORY,
                fix_anchor=ANCHOR_SNAPSHOT_CREATE,
            )
        severity = "error" if last_error else "warning"
        return DiagnosticCheck(
            check_id="snapshot_status",
            category="repository",
            title="利用可能なリポジトリ snapshot",
            severity=severity,
            detail=(
                f"ready な snapshot がありません。最新の snapshot #{latest['id']} の"
                f"状態は '{latest['status']}' です。"
            ),
            impact="snapshot の内容を読むすべてのパイプラインステップがブロックされます。",
            remediation=(
                "Repository タブの Snapshots から snapshot 作成を再試行してください。"
                "失敗が続く場合は、下記の直近のエラーとリポジトリパスの診断を"
                "確認してください。"
            ),
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["snapshot_ready"],
            last_observed_error=last_error,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_REPOSITORY,
            fix_anchor=ANCHOR_SNAPSHOT_CREATE,
        )

    indexed_count = conn.execute(
        "SELECT COUNT(*) FROM snapshot_files WHERE snapshot_id = ? AND inclusion_status = 'indexed'",
        (ready["id"],),
    ).fetchone()[0]
    if indexed_count == 0:
        return DiagnosticCheck(
            check_id="snapshot_status",
            category="repository",
            title="利用可能なリポジトリ snapshot",
            severity="warning",
            detail=(
                f"最新の ready snapshot #{ready['id']} には索引付けされたファイルが "
                "0 件しかありません。include/exclude パターンですべて除外されている"
                "可能性があります。"
            ),
            impact=(
                "ドラフト生成・シンボル索引・エントリポイント検出の結果が空になります。"
            ),
            remediation=(
                "Repository タブの include/exclude パターンを見直し、snapshot を"
                "作成し直してください。"
            ),
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["snapshot_ready", "symbols_indexed", "documentation_indexed"],
            last_observed_error=last_error,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_REPOSITORY,
            fix_anchor=ANCHOR_REPO_PATTERNS,
        )
    return DiagnosticCheck(
        check_id="snapshot_status",
        category="repository",
        title="利用可能なリポジトリ snapshot",
        severity="ok",
        detail=f"最新の ready snapshot #{ready['id']} には索引付けされたファイルが {indexed_count} 件あります。",
        impact="",
        remediation="",
        related_pages=[PAGE_REPOSITORY],
        related_pipeline_steps=["snapshot_ready"],
        last_observed_error=last_error,
    )


def _check_database_storage() -> DiagnosticCheck:
    from .db import db_path

    path = db_path()
    directory = os.path.dirname(os.path.abspath(path)) or "."
    problems: List[str] = []
    if not os.path.isdir(directory):
        problems.append(f"データベースのディレクトリが存在しません: {directory}")
    else:
        if not os.access(directory, os.W_OK):
            problems.append(f"データベースのディレクトリに書き込みできません: {directory}")
        if os.path.exists(path):
            if not os.access(path, os.R_OK):
                problems.append("データベースファイルを読み取りできません")
            if not os.access(path, os.W_OK):
                problems.append("データベースファイルに書き込みできません")
    if problems:
        return DiagnosticCheck(
            check_id="database_storage",
            category="database",
            title="データベースストレージ",
            severity="error",
            detail="; ".join(problems) + "。",
            impact=(
                "トレース・ポリシー・snapshot・intelligence run を永続化できず、"
                "ほとんどの書き込み操作が失敗します。"
            ),
            remediation=(
                "PROBE_DB_PATH を書き込み可能な場所に設定するか、ディレクトリ/"
                "ファイルの権限を修正してください。"
            ),
            related_env=["PROBE_DB_PATH"],
            related_paths=[path],
            fix_kind=FIX_KIND_DIALOG,
        )
    return DiagnosticCheck(
        check_id="database_storage",
        category="database",
        title="データベースストレージ",
        severity="ok",
        detail=f"データベースパス {path} は読み書き可能です。",
        impact="",
        remediation="",
        related_env=["PROBE_DB_PATH"],
        related_paths=[path],
    )


def _check_auth_scope(conn, system_id: int) -> DiagnosticCheck:
    users = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
    legacy_keys = bool(os.getenv("CONTROL_API_KEYS", "").strip())
    system_row = conn.execute(
        "SELECT id, name FROM systems WHERE id = ?", (system_id,)
    ).fetchone()
    if system_row is None:
        # get_system_id normally guarantees existence; keep a defensive branch.
        return DiagnosticCheck(
            check_id="auth_scope",
            category="auth",
            title="認証とシステムスコープ",
            severity="error",
            detail=f"選択中のシステム id {system_id} が存在しません。",
            impact="システムスコープのリクエストがすべて 404 になります。",
            remediation="ヘッダーで既存のシステムを選択するか、新しいシステムを作成してください。",
            fix_kind=FIX_KIND_DIALOG,
        )
    if users == 0 and not legacy_keys:
        return DiagnosticCheck(
            check_id="auth_scope",
            category="auth",
            title="認証とシステムスコープ",
            severity="warning",
            detail=(
                "アクティブなユーザーがおらず CONTROL_API_KEYS も未設定です。"
                "サーバーは認証なし（MVP 互換モード）で動作しています。"
            ),
            impact="Control Server に到達できる誰もが全アクセス権を持ちます。",
            remediation=(
                "CONTROL_ADMIN_USERNAME / CONTROL_ADMIN_PASSWORD を設定して管理者"
                "ユーザーを初期化するか、CONTROL_API_KEYS を設定してください。"
            ),
            related_env=[
                "CONTROL_ADMIN_USERNAME",
                "CONTROL_ADMIN_PASSWORD",
                "CONTROL_API_KEYS",
            ],
            related_pages=[PAGE_ADMIN],
            fix_kind=FIX_KIND_DIALOG,
        )
    return DiagnosticCheck(
        check_id="auth_scope",
        category="auth",
        title="認証とシステムスコープ",
        severity="ok",
        detail=(
            f"選択中のシステム '{system_row['name']}' は存在します。"
            f"アクティブユーザー {users} 名"
            + ("、レガシー API キー設定済み" if legacy_keys else "")
            + "。"
        ),
        impact="",
        remediation="",
        related_env=["CONTROL_API_KEYS"],
    )


def _api_key_status(provider: str) -> tuple:
    """Return (has_matching_key, detail_fragment) for a non-mock provider.

    The fragment is Japanese and carries no trailing period so callers can
    join it with other problems.
    """
    generic = bool((os.getenv("LLM_API_KEY") or "").strip())
    specific_env = PROVIDER_KEY_ENV.get(provider)
    specific = bool((os.getenv(specific_env) or "").strip()) if specific_env else False
    if generic or specific:
        return True, ""
    other_set = [
        env for prov, env in PROVIDER_KEY_ENV.items()
        if prov != provider and (os.getenv(env) or "").strip()
    ]
    if other_set:
        return False, (
            f"LLM_API_KEY も {specific_env} も設定されていません。"
            f"{', '.join(other_set)} が見つかりましたが、プロバイダ '{provider}' "
            "には対応していません"
        )
    return False, f"LLM_API_KEY も {specific_env} も設定されていません"


def _positive_number_problem(env_name: str, *, integer: bool = False) -> Optional[str]:
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        return f"{env_name}={raw!r} は有効な数値ではありません"
    if value <= 0:
        return f"{env_name}={raw!r} は正の数である必要があります"
    return None


def _check_llm_base_config() -> DiagnosticCheck:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    problems: List[str] = []
    severity = "ok"

    if provider not in KNOWN_PROVIDERS:
        problems.append(
            f"LLM_PROVIDER={provider!r} はサポートされていないプロバイダです"
            f"（{', '.join(sorted(KNOWN_PROVIDERS))}）"
        )
        severity = "error"
    elif provider != "mock":
        has_key, key_detail = _api_key_status(provider)
        if not has_key:
            problems.append(key_detail)
            severity = "error"

    timeout_problem = _positive_number_problem("LLM_TIMEOUT")
    if timeout_problem:
        problems.append(timeout_problem)
        if severity == "ok":
            severity = "warning"

    detail = (
        "; ".join(problems) + "。"
        if problems
        else f"LLM_PROVIDER={provider} と使用可能な API キー設定です。"
    )
    if provider == "mock" and not problems:
        severity = "warning"
        detail = (
            "LLM_PROVIDER=mock: すべての LLM 出力はテスト/ローカル動作確認用の"
            "決定的なモックデータで、reasoning が必要なパイプラインステップは"
            "ブロックされます。"
        )
    return DiagnosticCheck(
        check_id="llm_base_config",
        category="llm",
        title="LLM プロバイダ設定",
        severity=severity,
        detail=detail,
        impact=(
            "プロバイダや API キーが不正な場合、Generate & Evaluate とすべての "
            "reasoning モデル機能が呼び出し時に失敗します。"
            if severity == "error"
            else ("モック出力を実際の解析として扱わないでください。" if provider == "mock" else "")
        ),
        remediation=(
            "LLM_PROVIDER を openai/anthropic/gemini/mock のいずれかに設定し、"
            "mock 以外のプロバイダでは LLM_API_KEY（またはプロバイダ固有のキー）を"
            "設定してください。"
            if problems or provider == "mock"
            else ""
        ),
        related_env=["LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT"],
        related_pages=["/generation"],
        fix_kind=FIX_KIND_DIALOG,
    )


def _check_intelligence_llm_config() -> DiagnosticCheck:
    provider, model = _effective_intelligence_provider_model()
    explicit_provider = bool((os.getenv("INTELLIGENCE_LLM_PROVIDER") or "").strip())
    explicit_model = bool((os.getenv("INTELLIGENCE_LLM_MODEL") or "").strip())
    related_env = [
        "INTELLIGENCE_LLM_PROVIDER",
        "INTELLIGENCE_LLM_MODEL",
        "INTELLIGENCE_LLM_TIMEOUT",
        "INTELLIGENCE_MAX_OUTPUT_TOKENS",
        "LLM_PROVIDER",
        "LLM_MODEL",
    ]
    reasoning_steps = [
        "documentation_claims_scanned",
        "docs_code_reconciled",
        "capability_hierarchy_ready",
    ]
    fallback_note = (
        ""
        if explicit_provider
        else "（INTELLIGENCE_LLM_PROVIDER が未設定のため LLM_PROVIDER にフォールバック）"
    )

    problems: List[str] = []
    severity = "ok"

    if provider not in KNOWN_PROVIDERS:
        problems.append(
            f"実効的な intelligence プロバイダ {provider!r}{fallback_note} は"
            f"サポートされていません（{', '.join(sorted(KNOWN_PROVIDERS))}）"
        )
        severity = "error"
    else:
        family = _model_family_matches(provider, model)
        if family is False:
            problems.append(
                f"モデル {model!r} は実効プロバイダ {provider!r}{fallback_note} とは"
                "別プロバイダのモデルファミリに属しています"
            )
            severity = "error"
        elif family is None and provider != "mock":
            problems.append(
                f"モデル {model!r} はプロバイダ {provider!r} の既知のモデルファミリに"
                "一致しません。不正なモデル ID の可能性があり、reasoning 能力を"
                "静的に検証できません"
            )
            severity = "warning"

        if provider == "mock":
            problems.append(
                "実効的な intelligence プロバイダが 'mock' です。reasoning が必要な"
                "ステップはブロックされ、出力は明示的なモックデータです"
            )
            severity = _worst_severity([severity, "blocked"])
        elif family is True and severity != "error" and not is_reasoning_model(provider, model):
            problems.append(
                f"モデル {model!r} は reasoning 非対応です。ドキュメント索引・"
                "claim スキャン・capability 階層の生成には reasoning モデルが必要で、"
                "ブロックされます"
            )
            severity = _worst_severity([severity, "blocked"])

        if provider != "mock":
            has_key, key_detail = _api_key_status(provider)
            if not has_key:
                problems.append(key_detail)
                severity = "error"

    for env_name, integer in (
        ("INTELLIGENCE_LLM_TIMEOUT", False),
        ("INTELLIGENCE_MAX_OUTPUT_TOKENS", True),
    ):
        problem = _positive_number_problem(env_name, integer=integer)
        if problem:
            problems.append(problem)
            severity = _worst_severity([severity, "warning"])

    if not explicit_model and not explicit_provider and severity == "ok":
        problems.append(
            "INTELLIGENCE_LLM_* が未設定です。intelligence 機能は汎用の "
            "LLM_PROVIDER/LLM_MODEL 設定を使用します"
        )

    detail = (
        "; ".join(problems) + "。"
        if problems
        else f"実効的な intelligence モデル: {provider}/{model}{fallback_note}。"
    )
    return DiagnosticCheck(
        check_id="intelligence_llm_config",
        category="llm",
        title="Intelligence 用 reasoning モデル設定",
        severity=severity,
        detail=detail,
        impact=(
            "有効な reasoning モデルがないと claim スキャン・docs-code 照合・"
            "capability 階層の生成が失敗またはブロックされ、ヒューリスティックな"
            "フォールバックはありません。"
            if severity in ("error", "blocked", "warning")
            else ""
        ),
        remediation=(
            "INTELLIGENCE_LLM_PROVIDER と INTELLIGENCE_LLM_MODEL を reasoning 対応の"
            "プロバイダ/モデルの組み合わせ（および対応する API キー）に設定し、"
            "System Understanding のビルドを再実行してください。"
            if severity in ("error", "blocked", "warning")
            else ""
        ),
        related_env=related_env,
        related_pages=[PAGE_SYSTEM_UNDERSTANDING, PAGE_REPOSITORY],
        related_pipeline_steps=reasoning_steps,
        fix_kind=FIX_KIND_DIALOG,
    )


def _last_run_failure_guidance(run_type: str, error: Optional[str]) -> Dict[str, Any]:
    error_text = (error or "").lower()
    if "evidence validation failed" in error_text:
        pages = (
            [PAGE_INTERVIEW]
            if run_type == "interview_dialogue"
            else [PAGE_SYSTEM_UNDERSTANDING]
        )
        steps = (
            ["interview_dialogue"]
            if run_type == "interview_dialogue"
            else [
                "documentation_claims_scanned",
                "capability_hierarchy_ready",
            ]
        )
        return {
            "remediation": (
                "下記の直近のエラーは API キー・モデル ID・タイムアウト設定ではなく、"
                "reasoning モデルの構造化出力に含まれる evidence_refs が許可された"
                "snapshot 範囲外だったことを示しています。無効な evidence_refs は保存前に"
                "破棄されるべきなので、該当機能を再実行し、同じ validation error が続く"
                "場合は evidence_refs の生成/検証ロジックを確認してください。"
            ),
            "related_env": [],
            "related_pages": pages,
            "related_pipeline_steps": steps,
            "fix_page": pages[0],
            "fix_anchor": None,
        }
    return {
        "remediation": (
            "下記の直近のエラーを確認し、指し示す設定（API キー・モデル ID・"
            "タイムアウト）を修正してから、System Understanding でビルドを"
            "再実行してください。"
        ),
        "related_env": [
            "INTELLIGENCE_LLM_PROVIDER",
            "INTELLIGENCE_LLM_MODEL",
            "LLM_API_KEY",
            "INTELLIGENCE_LLM_TIMEOUT",
        ],
        "related_pages": [PAGE_SYSTEM_UNDERSTANDING],
        "related_pipeline_steps": [
            "documentation_indexed",
            "documentation_claims_scanned",
            "capability_hierarchy_ready",
        ],
        "fix_page": PAGE_SYSTEM_UNDERSTANDING,
        "fix_anchor": ANCHOR_BUILD,
    }


def _check_last_reasoning_run(conn, system_id: int) -> DiagnosticCheck:
    row = conn.execute(
        "SELECT id, run_type, status, error_details, completed_at, started_at, is_mock "
        "FROM intelligence_runs "
        "WHERE system_id = ? AND decision_method = 'reasoning_llm' "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    if row is None:
        return DiagnosticCheck(
            check_id="llm_last_run",
            category="llm",
            title="直近の reasoning モデル実行",
            severity="unknown",
            detail=(
                "このシステムではまだ reasoning モデルの実行が記録されていないため、"
                "実行時の問題（タイムアウト・認証・不正なモデル・パースエラー）は"
                "観測されていません。"
            ),
            impact="設定は正しく見えても、呼び出し時に失敗する可能性があります。",
            remediation=(
                "System Understanding のビルドまたはドラフト生成を実行して、"
                "設定したモデルを動作確認してください。"
            ),
            related_pages=[PAGE_SYSTEM_UNDERSTANDING],
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_SYSTEM_UNDERSTANDING,
            fix_anchor=ANCHOR_BUILD,
        )
    observed_at = row["completed_at"] or row["started_at"]
    if row["status"] == "failed":
        guidance = _last_run_failure_guidance(row["run_type"], row["error_details"])
        return DiagnosticCheck(
            check_id="llm_last_run",
            category="llm",
            title="直近の reasoning モデル実行",
            severity="error",
            detail=(
                f"直近の reasoning 実行（#{row['id']}, {row['run_type']}）が"
                "失敗しました。"
            ),
            impact=(
                "reasoning に基づく成果物（ドラフト・claim・capability 階層）が"
                "生成されていないか、古くなっています。"
            ),
            remediation=guidance["remediation"],
            related_env=guidance["related_env"],
            related_pages=guidance["related_pages"],
            related_pipeline_steps=guidance["related_pipeline_steps"],
            last_observed_error=LastObservedError(
                source=f"intelligence_runs#{row['id']}:{row['run_type']}",
                status=row["status"],
                error=row["error_details"],
                observed_at=observed_at,
            ),
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=guidance["fix_page"],
            fix_anchor=guidance["fix_anchor"],
        )
    mock_note = "（モック実行）" if row["is_mock"] else ""
    return DiagnosticCheck(
        check_id="llm_last_run",
        category="llm",
        title="直近の reasoning モデル実行",
        severity="ok",
        detail=(
            f"直近の reasoning 実行（#{row['id']}, {row['run_type']}）の状態は "
            f"'{row['status']}'{mock_note} です。"
        ),
        impact="",
        remediation="",
        related_pages=[PAGE_SYSTEM_UNDERSTANDING],
    )


def _latest_ready_snapshot_id(conn, system_id: int) -> Optional[int]:
    return system_state.latest_ready_snapshot_id(conn, system_id)


def _no_ready_snapshot_check(base: dict) -> DiagnosticCheck:
    """Blocked result shared by every pipeline check when no snapshot is ready."""
    payload = {k: v for k, v in base.items() if k != "snapshot_id"}
    return DiagnosticCheck(
        severity="blocked",
        detail="ready な snapshot がないため、このステップは実行できません。",
        impact="snapshot が作成されるまでこのステップは未実行のままです。",
        remediation="まず Repository タブから snapshot を作成してください。",
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_REPOSITORY,
        fix_anchor=ANCHOR_SNAPSHOT_CREATE,
        **payload,
    )


def _baseline_ok_check(
    *,
    base: dict,
    baseline: "system_state.UnderstandingBaseline",
    impact: "system_state.UnderstandingDiffImpact",
    purpose: bool,
) -> DiagnosticCheck:
    label = "System Purpose" if purpose else "Core Capability"
    if baseline.snapshot_id == base.get("snapshot_id"):
        detail = f"{label} は現在の snapshot で確認済みです。"
    elif impact.status == "unchanged":
        detail = (
            f"{label} は snapshot #{baseline.snapshot_id} の確認済み理解を再利用できます。"
            "今回の差分には再確認が必要な根拠変更は見つかりませんでした。"
        )
    else:
        detail = f"{label} は確認済みです。"
    payload = {k: v for k, v in base.items() if k != "snapshot_id"}
    return DiagnosticCheck(
        severity="ok",
        detail=detail,
        impact="",
        remediation="",
        **payload,
    )


def _baseline_impacted_check(
    *,
    base: dict,
    baseline: "system_state.UnderstandingBaseline",
    impact: "system_state.UnderstandingDiffImpact",
    purpose: bool,
) -> DiagnosticCheck:
    target = "System Purpose" if purpose else "Core Capabilities"
    title = "System Purpose の再確認" if purpose else "主な機能 / Core Capabilities の再確認"
    reason = " ".join(impact.reasons) if impact.reasons else "前回確認済み理解に影響しうる差分があります。"
    payload = {k: v for k, v in base.items() if k not in {"title", "snapshot_id"}}
    return DiagnosticCheck(
        severity="warning",
        detail=(
            f"{target} は snapshot #{baseline.snapshot_id} で確認済みですが、"
            f"最新 snapshot との差分に影響候補があります。{reason}"
        ),
        impact=(
            "確認済み理解をそのまま使えるか判断が必要です。"
            "目的・主要機能が変わっていない場合は Interview で再確認してください。"
        ),
        remediation=f"Interview で {target} を再確認してください。",
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_INTERVIEW,
        fix_anchor=ANCHOR_INTERVIEW_PURPOSE if purpose else ANCHOR_INTERVIEW_CAPABILITIES,
        title=title,
        **payload,
    )


def _unconfirmed_understanding_check(
    *,
    base: dict,
    baseline: "system_state.UnderstandingBaseline",
    purpose: bool,
) -> DiagnosticCheck:
    target = "System Purpose" if purpose else "Core Capabilities"
    title = "System Purpose の確認" if purpose else "主な機能 / Core Capabilities の確認"
    count = baseline.purpose_count if purpose else baseline.capability_count
    payload = {k: v for k, v in base.items() if k not in {"title", "snapshot_id"}}
    return DiagnosticCheck(
        severity="warning",
        detail=(
            f"Interview に未確認の {target} 候補が {count} 件あります。"
            "baseline として再利用するには、開発者による明示的な確認が必要です。"
        ),
        impact=(
            "未確認の理解は、snapshot 更新後の差分診断や probe 設計の前提として"
            "まだ採用されません。"
        ),
        remediation=f"Interview で {target} を確認済みにしてください。",
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_INTERVIEW,
        fix_anchor=ANCHOR_INTERVIEW_PURPOSE if purpose else ANCHOR_INTERVIEW_CAPABILITIES,
        title=title,
        **payload,
    )


def _check_system_purpose(conn, system_id: int, snapshot_id: Optional[int]) -> DiagnosticCheck:
    """Issue #120: System Purpose is a prerequisite for probe design, flow

    exploration, and improvement proposals matching user intent, not just a
    blank field on a page — so it is tracked here alongside required
    settings, and its remediation is prioritized ahead of ``llm_last_run``.

    Issue #193: the branch classification (satisfied / baseline_reusable /
    diff_impacted / unconfirmed / missing_baseline) comes from
    ``system_state.evaluate_understanding`` so this check and the System
    State Assessment layer agree on the same evidence; only the Japanese
    text stays here for backward-compatible ``/system-diagnostics`` output.
    """
    base = dict(
        check_id="system_purpose",
        category="understanding",
        title="System Purpose の定義",
        related_pages=[PAGE_SYSTEM_UNDERSTANDING, PAGE_INTERVIEW],
        related_pipeline_steps=["capability_hierarchy_ready"],
        snapshot_id=snapshot_id,
    )
    if snapshot_id is None:
        return _no_ready_snapshot_check(base)
    status = system_state.evaluate_understanding(conn, system_id, snapshot_id, purpose=True)
    if status.kind == "satisfied_current":
        return DiagnosticCheck(
            severity="ok",
            detail="System Purpose が定義されています。",
            impact="",
            remediation="",
            **{k: v for k, v in base.items() if k != "snapshot_id"},
        )
    if status.kind == "baseline_reusable":
        return _baseline_ok_check(base=base, baseline=status.baseline, impact=status.impact, purpose=True)
    if status.kind == "diff_impacted":
        return _baseline_impacted_check(base=base, baseline=status.baseline, impact=status.impact, purpose=True)
    if status.kind == "unconfirmed":
        return _unconfirmed_understanding_check(base=base, baseline=status.baseline, purpose=True)
    return DiagnosticCheck(
        severity="warning",
        detail=(
            "System Purpose が未定義です。Pipeline のステップが完了していても、"
            "このシステムが何のためのものかが定義されていません。"
        ),
        impact=(
            "probe 設計・flow 探索・改善提案・ユーザー意図との整合の前提となる"
            "根幹情報が欠けています。"
        ),
        remediation="Interview で System Purpose を定義・確認してください。",
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_INTERVIEW,
        fix_anchor=ANCHOR_INTERVIEW_PURPOSE,
        **{k: v for k, v in base.items() if k != "snapshot_id"},
    )


def _check_system_capabilities(conn, system_id: int, snapshot_id: Optional[int]) -> DiagnosticCheck:
    """Issue #120: main system capabilities (Core Capabilities) are the other

    prerequisite for probe/flow/improvement work, tracked the same way as
    System Purpose above.

    Issue #193: see ``_check_system_purpose`` — branch classification comes
    from ``system_state.evaluate_understanding``.
    """
    base = dict(
        check_id="system_capabilities",
        category="understanding",
        title="主な機能 / Core Capabilities の把握",
        related_pages=[PAGE_SYSTEM_UNDERSTANDING, PAGE_INTERVIEW, PAGE_CAPABILITY_MAP],
        related_pipeline_steps=["capability_hierarchy_ready"],
        snapshot_id=snapshot_id,
    )
    if snapshot_id is None:
        return _no_ready_snapshot_check(base)
    status = system_state.evaluate_understanding(conn, system_id, snapshot_id, purpose=False)
    if status.kind == "satisfied_current":
        return DiagnosticCheck(
            severity="ok",
            detail=f"{status.count} 件の Core Capability が定義されています。",
            impact="",
            remediation="",
            **{k: v for k, v in base.items() if k != "snapshot_id"},
        )
    if status.kind == "baseline_reusable":
        return _baseline_ok_check(base=base, baseline=status.baseline, impact=status.impact, purpose=False)
    if status.kind == "diff_impacted":
        return _baseline_impacted_check(base=base, baseline=status.baseline, impact=status.impact, purpose=False)
    if status.kind == "unconfirmed":
        return _unconfirmed_understanding_check(base=base, baseline=status.baseline, purpose=False)
    return DiagnosticCheck(
        severity="warning",
        detail="対象システムの主な機能（Core Capabilities）が未定義・空です。",
        impact=(
            "probe 候補選定・flow 探索・改善提案の前提となる、システムの主要な"
            "機能の把握ができていません。"
        ),
        remediation=(
            "Interview で主な機能を特定するか、Capability Map で capability 階層を"
            "確認してください。"
        ),
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_INTERVIEW,
        fix_anchor=ANCHOR_INTERVIEW_CAPABILITIES,
        **{k: v for k, v in base.items() if k != "snapshot_id"},
    )


def _reasoning_unavailable_check(base: dict, *, detail: str) -> DiagnosticCheck:
    """Blocked result when a step needs reasoning but no reasoning model is set.

    ``detail`` differs per pipeline step; everything else is shared.
    """
    return DiagnosticCheck(
        severity="blocked",
        detail=detail,
        impact="System Understanding でこのステップはブロック/未実行として表示されます。",
        remediation=(
            "intelligence 用 reasoning モデル設定（上記の LLM チェックを参照）を"
            "修正してからビルドを実行してください。"
        ),
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_SYSTEM_UNDERSTANDING,
        fix_anchor=ANCHOR_BUILD,
        **base,
    )


def _run_backed_pipeline_check(
    conn,
    system_id: int,
    snapshot_id: Optional[int],
    *,
    check_id: str,
    title: str,
    run_types: List[str],
    pipeline_steps: List[str],
    requires_reasoning: bool,
    reasoning_available: bool,
    not_run_remediation: str,
) -> DiagnosticCheck:
    base = dict(
        check_id=check_id,
        category="pipeline",
        title=title,
        related_pages=[PAGE_SYSTEM_UNDERSTANDING],
        related_pipeline_steps=pipeline_steps,
    )
    if snapshot_id is None:
        return _no_ready_snapshot_check(base)
    placeholders = ",".join("?" for _ in run_types)
    row = conn.execute(
        f"SELECT id, run_type, status, error_details, completed_at, started_at "
        f"FROM intelligence_runs "
        f"WHERE system_id = ? AND snapshot_id = ? AND run_type IN ({placeholders}) "
        f"ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id, *run_types),
    ).fetchone()
    if row is None:
        if requires_reasoning and not reasoning_available:
            return _reasoning_unavailable_check(
                base,
                detail=(
                    "このステップは一度も実行されておらず、reasoning モデルが"
                    "必要ですが設定されていません。"
                ),
            )
        return DiagnosticCheck(
            severity="warning",
            detail="このステップは現在の snapshot に対して実行されていません。",
            impact="System Understanding でこのステップは未実行として表示されます。",
            remediation=not_run_remediation,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_SYSTEM_UNDERSTANDING,
            fix_anchor=ANCHOR_BUILD,
            **base,
        )
    if row["status"] == "completed":
        return DiagnosticCheck(
            severity="ok",
            detail=f"直近の実行（#{row['id']}, {row['run_type']}）は完了しました。",
            impact="",
            remediation="",
            **base,
        )
    return DiagnosticCheck(
        severity="error",
        detail=(
            f"直近の実行（#{row['id']}, {row['run_type']}）の状態は "
            f"'{row['status']}' です。"
        ),
        impact="このステップの成果物が欠落しているか古くなっています。",
        remediation=(
            "下記の直近のエラーを確認し、根本原因を修正してから、System "
            "Understanding でビルドを再実行してください。"
        ),
        last_observed_error=LastObservedError(
            source=f"intelligence_runs#{row['id']}:{row['run_type']}",
            status=row["status"],
            error=row["error_details"],
            observed_at=row["completed_at"] or row["started_at"],
        ),
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_SYSTEM_UNDERSTANDING,
        fix_anchor=ANCHOR_BUILD,
        **base,
    )


def _check_capability_hierarchy_pipeline(
    conn,
    system_id: int,
    snapshot_id: Optional[int],
    *,
    reasoning_available: bool,
) -> DiagnosticCheck:
    """Issue #210: same run-status check as ``_run_backed_pipeline_check``,
    but a completed run with zero capability nodes is downgraded from "ok" to
    "warning" instead of contradicting ``_check_system_capabilities`` (which
    already reports zero capabilities as a warning) by calling the same
    completed-but-empty run "ok" here.
    """
    check = _run_backed_pipeline_check(
        conn, system_id, snapshot_id,
        check_id="pipeline_capability_hierarchy",
        title="capability 階層の実行",
        run_types=["capability_hierarchy"],
        pipeline_steps=["capability_hierarchy_ready"],
        requires_reasoning=True,
        reasoning_available=reasoning_available,
        not_run_remediation="System Understanding で Build / Refresh を実行して capability 階層を生成してください。",
    )
    if (
        check.severity == "ok"
        and snapshot_id is not None
        and system_state._capability_count_in_current_snapshot(conn, system_id, snapshot_id) == 0
    ):
        return replace(
            check,
            severity="warning",
            detail=check.detail + " ただし capability ノードが 0 件のため、Core Capabilities は未定義のままです。",
            impact="Core Capabilities が未定義のため、probe 設計や flow 探索の前提が欠けています。",
            remediation=(
                "Interview で Core Capabilities を確認するか、対象リポジトリに "
                "`probe-agent:` メタデータを追加してから Build / Refresh を再実行してください。"
            ),
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_INTERVIEW,
            fix_anchor=ANCHOR_INTERVIEW_CAPABILITIES,
        )
    return check


def _artifact_backed_pipeline_check(
    conn,
    system_id: int,
    snapshot_id: Optional[int],
    *,
    check_id: str,
    title: str,
    artifact_sql: str,
    pipeline_steps: List[str],
    requires_reasoning: bool,
    reasoning_available: bool,
    not_run_remediation: str,
) -> DiagnosticCheck:
    base = dict(
        check_id=check_id,
        category="pipeline",
        title=title,
        related_pages=[PAGE_SYSTEM_UNDERSTANDING],
        related_pipeline_steps=pipeline_steps,
    )
    if snapshot_id is None:
        return _no_ready_snapshot_check(base)
    row = conn.execute(artifact_sql, (system_id, snapshot_id)).fetchone()
    if row is None:
        if requires_reasoning and not reasoning_available:
            return _reasoning_unavailable_check(
                base,
                detail=(
                    "現在の snapshot に対する成果物がなく、必要な reasoning モデルが"
                    "設定されていません。"
                ),
            )
        return DiagnosticCheck(
            severity="warning",
            detail="現在の snapshot に対する成果物がありません。",
            impact="System Understanding でこのステップは未実行として表示されます。",
            remediation=not_run_remediation,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_SYSTEM_UNDERSTANDING,
            fix_anchor=ANCHOR_BUILD,
            **base,
        )
    return DiagnosticCheck(
        severity="ok",
        detail="現在の snapshot に対する成果物が存在します。",
        impact="",
        remediation="",
        **base,
    )


def _build_step_pipeline_check(
    conn,
    system_id: int,
    snapshot_id: Optional[int],
    *,
    check_id: str,
    title: str,
    step: str,
    pipeline_steps: List[str],
    not_run_remediation: str,
) -> DiagnosticCheck:
    base = dict(
        check_id=check_id,
        category="pipeline",
        title=title,
        related_pages=[PAGE_SYSTEM_UNDERSTANDING],
        related_pipeline_steps=pipeline_steps,
    )
    if snapshot_id is None:
        return _no_ready_snapshot_check(base)
    row = conn.execute(
        """SELECT id, status, error, completed_at, started_at
           FROM system_understanding_build_steps
           WHERE system_id = ? AND snapshot_id = ? AND step = ?
           ORDER BY id DESC LIMIT 1""",
        (system_id, snapshot_id, step),
    ).fetchone()
    if row is None:
        return DiagnosticCheck(
            severity="warning",
            detail="このビルドステップは現在の snapshot に対して実行されていません。",
            impact="System Understanding でこのステップは未実行として表示されます。",
            remediation=not_run_remediation,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_SYSTEM_UNDERSTANDING,
            fix_anchor=ANCHOR_BUILD,
            **base,
        )
    if row["status"] == "completed":
        return DiagnosticCheck(
            severity="ok",
            detail=f"直近のビルドステップ（#{row['id']}, {step}）は完了しました。",
            impact="",
            remediation="",
            **base,
        )
    if row["status"] == "blocked":
        severity = "blocked"
    elif row["status"] in ("failed", "cancelled"):
        severity = "error"
    else:
        severity = "warning"
    return DiagnosticCheck(
        severity=severity,
        detail=f"直近のビルドステップ（#{row['id']}, {step}）の状態は '{row['status']}' です。",
        impact="このステップの成果物が欠落しているか古くなっています。",
        remediation="下記のビルドステップのエラーを確認し、根本原因を修正してからビルドを再実行してください。",
        last_observed_error=LastObservedError(
            source=f"system_understanding_build_steps#{row['id']}:{step}",
            status=row["status"],
            error=row["error"],
            observed_at=row["completed_at"] or row["started_at"],
        ),
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_SYSTEM_UNDERSTANDING,
        fix_anchor=ANCHOR_BUILD,
        **base,
    )


def run_system_diagnostics(system_id: int) -> SystemDiagnosticsReport:
    """Run all deterministic settings/health checks for one system."""
    from .system_understanding_service import _is_reasoning_model_available

    reasoning_available = _is_reasoning_model_available()
    checks: List[DiagnosticCheck] = []

    checks.append(_check_repository_roots())
    checks.append(_check_database_storage())
    checks.append(_check_llm_base_config())
    checks.append(_check_intelligence_llm_config())

    with get_conn() as conn:
        checks.append(_check_repository_config(conn, system_id))
        checks.append(_check_snapshot_status(conn, system_id))
        checks.append(_check_auth_scope(conn, system_id))

        snapshot_id = _latest_ready_snapshot_id(conn, system_id)

        # Issue #120: System Purpose and Core Capabilities are prerequisites
        # for probe design, flow exploration, and improvement proposals, so
        # they are checked ahead of the pipeline/llm_last_run checks below —
        # a completed pipeline with no purpose is still a warning, not "ok".
        checks.append(_check_system_purpose(conn, system_id, snapshot_id))
        checks.append(_check_system_capabilities(conn, system_id, snapshot_id))

        checks.append(_check_last_reasoning_run(conn, system_id))

        checks.append(
            _run_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_symbol_index",
                title="シンボル索引の実行",
                run_types=["symbol_index"],
                pipeline_steps=["symbols_indexed"],
                requires_reasoning=False,
                reasoning_available=reasoning_available,
                not_run_remediation="System Understanding で Build / Refresh を実行してコードシンボルを索引付けしてください。",
            )
        )
        checks.append(
            _run_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_entrypoint_index",
                title="エントリポイント索引の実行",
                run_types=["entrypoint_index"],
                pipeline_steps=["entrypoints_discovered"],
                requires_reasoning=False,
                reasoning_available=reasoning_available,
                not_run_remediation="System Understanding で Build / Refresh を実行してエントリポイントを検出してください。",
            )
        )
        checks.append(
            _build_step_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_documentation_index",
                title="ドキュメント索引のビルドステップ",
                step="documentation_index",
                pipeline_steps=["documentation_indexed"],
                not_run_remediation="System Understanding で Build / Refresh を実行してドキュメントチャンクを索引付けしてください。",
            )
        )
        checks.append(
            _artifact_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_understanding_graph",
                title="Understanding グラフ（ドキュメントの主張）",
                artifact_sql=(
                    "SELECT id FROM understanding_graph_snapshots "
                    "WHERE system_id = ? AND snapshot_id = ? LIMIT 1"
                ),
                pipeline_steps=["documentation_claims_scanned", "docs_code_reconciled"],
                requires_reasoning=True,
                reasoning_available=reasoning_available,
                not_run_remediation="System Understanding で Build / Refresh を実行してドキュメントの主張をスキャンしてください。",
            )
        )
        checks.append(
            _check_capability_hierarchy_pipeline(
                conn, system_id, snapshot_id,
                reasoning_available=reasoning_available,
            )
        )

    severity_counts: Dict[str, int] = {level: 0 for level in SEVERITY_ORDER}
    for check in checks:
        severity_counts[check.severity] = severity_counts.get(check.severity, 0) + 1
    overall = _worst_severity([c.severity for c in checks])
    return SystemDiagnosticsReport(
        system_id=system_id,
        generated_at=time.time(),
        overall_severity=overall,
        severity_counts=severity_counts,
        checks=checks,
    )
