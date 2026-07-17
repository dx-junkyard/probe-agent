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

from . import state_messages, system_state
from .db import get_conn
from .llm import KNOWN_PROVIDERS, PROVIDER_KEY_ENV, is_reasoning_model

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
    title = state_messages.check_title("repository_roots")
    raw = os.getenv("PROBE_REPOSITORY_ROOTS", "").strip()
    if not raw:
        msg = state_messages.check_message("repository_roots", "missing_env")
        return DiagnosticCheck(
            check_id="repository_roots",
            category="repository",
            title=title,
            severity="error",
            detail=msg["detail"],
            impact=msg["impact"],
            remediation=msg["remediation"],
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
            problems.append(state_messages.check_problem_fragment(
                "repository_roots.missing_dirs", dirs=", ".join(missing)))
        if unreadable:
            problems.append(state_messages.check_problem_fragment(
                "repository_roots.unreadable_dirs", dirs=", ".join(unreadable)))
        msg = state_messages.check_message("repository_roots", "problems")
        return DiagnosticCheck(
            check_id="repository_roots",
            category="repository",
            title=title,
            severity="error",
            detail=msg["detail"].format(problems="; ".join(problems)),
            impact=msg["impact"],
            remediation=msg["remediation"],
            related_env=["PROBE_REPOSITORY_ROOTS"],
            related_paths=missing + unreadable,
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
            fix_kind=FIX_KIND_DIALOG,
        )
    msg = state_messages.check_message("repository_roots", "ok")
    return DiagnosticCheck(
        check_id="repository_roots",
        category="repository",
        title=title,
        severity="ok",
        detail=msg["detail"].format(count=len(roots)),
        impact="",
        remediation="",
        related_env=["PROBE_REPOSITORY_ROOTS"],
        related_paths=roots,
        related_pages=[PAGE_REPOSITORY],
        related_pipeline_steps=["repository_configured"],
    )


def _check_repository_config(conn, system_id: int) -> DiagnosticCheck:
    title = state_messages.check_title("repository_config")
    row = conn.execute(
        "SELECT repo_path FROM repository_configs WHERE system_id = ?",
        (system_id,),
    ).fetchone()
    if row is None:
        msg = state_messages.check_message("repository_config", "missing")
        return DiagnosticCheck(
            check_id="repository_config",
            category="repository",
            title=title,
            severity="warning",
            detail=msg["detail"],
            impact=msg["impact"],
            remediation=msg["remediation"],
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_REPOSITORY,
            fix_anchor=ANCHOR_REPO_CONFIG,
        )
    repo_path = row["repo_path"]
    problems: List[str] = []
    if not os.path.isdir(repo_path):
        problems.append(state_messages.check_problem_fragment("repository_config.not_a_dir"))
    else:
        if not os.access(repo_path, os.R_OK):
            problems.append(state_messages.check_problem_fragment("repository_config.unreadable"))
        if not os.path.exists(os.path.join(repo_path, ".git")):
            problems.append(state_messages.check_problem_fragment("repository_config.not_git"))
    if problems:
        msg = state_messages.check_message("repository_config", "invalid")
        return DiagnosticCheck(
            check_id="repository_config",
            category="repository",
            title=title,
            severity="error",
            detail=msg["detail"].format(repo_path=repo_path, problems="; ".join(problems)),
            impact=msg["impact"],
            remediation=msg["remediation"],
            related_env=["PROBE_REPOSITORY_ROOTS"],
            related_paths=[repo_path],
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_REPOSITORY,
            fix_anchor=ANCHOR_REPO_CONFIG,
        )
    msg = state_messages.check_message("repository_config", "ok")
    return DiagnosticCheck(
        check_id="repository_config",
        category="repository",
        title=title,
        severity="ok",
        detail=msg["detail"].format(repo_path=repo_path),
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

    title = state_messages.check_title("snapshot_status")
    if ready is None:
        if latest is None:
            msg = state_messages.check_message("snapshot_status", "no_snapshot")
            return DiagnosticCheck(
                check_id="snapshot_status",
                category="repository",
                title=title,
                severity="warning",
                detail=msg["detail"],
                impact=msg["impact"],
                remediation=msg["remediation"],
                related_pages=[PAGE_REPOSITORY],
                related_pipeline_steps=["snapshot_ready"],
                fix_kind=FIX_KIND_NAVIGATE,
                fix_page=PAGE_REPOSITORY,
                fix_anchor=ANCHOR_SNAPSHOT_CREATE,
            )
        severity = "error" if last_error else "warning"
        msg = state_messages.check_message("snapshot_status", "not_ready")
        return DiagnosticCheck(
            check_id="snapshot_status",
            category="repository",
            title=title,
            severity=severity,
            detail=msg["detail"].format(latest_id=latest["id"], latest_status=latest["status"]),
            impact=msg["impact"],
            remediation=msg["remediation"],
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
        msg = state_messages.check_message("snapshot_status", "zero_indexed")
        return DiagnosticCheck(
            check_id="snapshot_status",
            category="repository",
            title=title,
            severity="warning",
            detail=msg["detail"].format(ready_id=ready["id"]),
            impact=msg["impact"],
            remediation=msg["remediation"],
            related_pages=[PAGE_REPOSITORY],
            related_pipeline_steps=["snapshot_ready", "symbols_indexed", "documentation_indexed"],
            last_observed_error=last_error,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_REPOSITORY,
            fix_anchor=ANCHOR_REPO_PATTERNS,
        )
    msg = state_messages.check_message("snapshot_status", "ok")
    return DiagnosticCheck(
        check_id="snapshot_status",
        category="repository",
        title=title,
        severity="ok",
        detail=msg["detail"].format(ready_id=ready["id"], indexed_count=indexed_count),
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
        problems.append(state_messages.check_problem_fragment(
            "database_storage.dir_missing", directory=directory))
    else:
        if not os.access(directory, os.W_OK):
            problems.append(state_messages.check_problem_fragment(
                "database_storage.dir_unwritable", directory=directory))
        if os.path.exists(path):
            if not os.access(path, os.R_OK):
                problems.append(state_messages.check_problem_fragment("database_storage.file_unreadable"))
            if not os.access(path, os.W_OK):
                problems.append(state_messages.check_problem_fragment("database_storage.file_unwritable"))
    title = state_messages.check_title("database_storage")
    if problems:
        msg = state_messages.check_message("database_storage", "problems")
        return DiagnosticCheck(
            check_id="database_storage",
            category="database",
            title=title,
            severity="error",
            detail="; ".join(problems) + "。",
            impact=msg["impact"],
            remediation=msg["remediation"],
            related_env=["PROBE_DB_PATH"],
            related_paths=[path],
            fix_kind=FIX_KIND_DIALOG,
        )
    msg = state_messages.check_message("database_storage", "ok")
    return DiagnosticCheck(
        check_id="database_storage",
        category="database",
        title=title,
        severity="ok",
        detail=msg["detail"].format(path=path),
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
    title = state_messages.check_title("auth_scope")
    if system_row is None:
        # get_system_id normally guarantees existence; keep a defensive branch.
        msg = state_messages.check_message("auth_scope", "unknown_system")
        return DiagnosticCheck(
            check_id="auth_scope",
            category="auth",
            title=title,
            severity="error",
            detail=msg["detail"].format(system_id=system_id),
            impact=msg["impact"],
            remediation=msg["remediation"],
            fix_kind=FIX_KIND_DIALOG,
        )
    if users == 0 and not legacy_keys:
        msg = state_messages.check_message("auth_scope", "open_mode")
        return DiagnosticCheck(
            check_id="auth_scope",
            category="auth",
            title=title,
            severity="warning",
            detail=msg["detail"],
            impact=msg["impact"],
            remediation=msg["remediation"],
            related_env=[
                "CONTROL_ADMIN_USERNAME",
                "CONTROL_ADMIN_PASSWORD",
                "CONTROL_API_KEYS",
            ],
            related_pages=[PAGE_ADMIN],
            fix_kind=FIX_KIND_DIALOG,
        )
    msg = state_messages.check_message("auth_scope", "ok")
    return DiagnosticCheck(
        check_id="auth_scope",
        category="auth",
        title=title,
        severity="ok",
        detail=msg["detail"].format(
            system_name=system_row["name"],
            users=users,
            legacy_note=msg["legacy_note"] if legacy_keys else "",
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
            state_messages.check_message("llm_base_config", "unsupported_provider")["detail_fragment"].format(
                provider=provider, known=", ".join(sorted(KNOWN_PROVIDERS)),
            )
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

    ok_msg = state_messages.check_message("llm_base_config", "ok")
    problems_msg = state_messages.check_message("llm_base_config", "problems")
    detail = (
        "; ".join(problems) + "。"
        if problems
        else ok_msg["detail"].format(provider=provider)
    )
    if provider == "mock" and not problems:
        severity = "warning"
        mock_msg = state_messages.check_message("llm_base_config", "mock")
        detail = mock_msg["detail"]
    impact = (
        problems_msg["impact"]
        if severity == "error"
        else (state_messages.check_message("llm_base_config", "mock")["impact"] if provider == "mock" else "")
    )
    remediation = problems_msg["remediation"] if (problems or provider == "mock") else ""
    return DiagnosticCheck(
        check_id="llm_base_config",
        category="llm",
        title=state_messages.check_title("llm_base_config"),
        severity=severity,
        detail=detail,
        impact=impact,
        remediation=remediation,
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

    def _fragment(variant: str) -> str:
        return state_messages.check_message("intelligence_llm_config", variant)["detail_fragment"]

    if provider not in KNOWN_PROVIDERS:
        problems.append(
            _fragment("unsupported_provider").format(
                provider=provider, fallback_note=fallback_note, known=", ".join(sorted(KNOWN_PROVIDERS)),
            )
        )
        severity = "error"
    else:
        family = _model_family_matches(provider, model)
        if family is False:
            problems.append(
                _fragment("wrong_family").format(model=model, provider=provider, fallback_note=fallback_note)
            )
            severity = "error"
        elif family is None and provider != "mock":
            problems.append(_fragment("unknown_family").format(model=model, provider=provider))
            severity = "warning"

        if provider == "mock":
            problems.append(_fragment("mock"))
            severity = _worst_severity([severity, "blocked"])
        elif family is True and severity != "error" and not is_reasoning_model(provider, model):
            problems.append(_fragment("not_reasoning_model").format(model=model))
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
        problems.append(_fragment("unset_defaults_to_generic"))

    ok_msg = state_messages.check_message("intelligence_llm_config", "ok")
    detail = (
        "; ".join(problems) + "。"
        if problems
        else ok_msg["detail"].format(provider=provider, model=model, fallback_note=fallback_note)
    )
    problems_msg = state_messages.check_message("intelligence_llm_config", "problems")
    return DiagnosticCheck(
        check_id="intelligence_llm_config",
        category="llm",
        title=state_messages.check_title("intelligence_llm_config"),
        severity=severity,
        detail=detail,
        impact=problems_msg["impact"] if severity in ("error", "blocked", "warning") else "",
        remediation=problems_msg["remediation"] if severity in ("error", "blocked", "warning") else "",
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
            "remediation": state_messages.check_message(
                "llm_last_run", "failure_guidance_evidence"
            )["remediation"],
            "related_env": [],
            "related_pages": pages,
            "related_pipeline_steps": steps,
            "fix_page": pages[0],
            "fix_anchor": None,
        }
    return {
        "remediation": state_messages.check_message(
            "llm_last_run", "failure_guidance_generic"
        )["remediation"],
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


def _check_last_reasoning_run(
    conn, system_id: int, snapshot_id: Optional[int],
) -> DiagnosticCheck:
    """Report only a reasoning failure that belongs to the current snapshot.

    A historical Interview review must not turn a later deterministic System
    Understanding build red.  Each snapshot is an immutable evidence scope,
    so the diagnostic is scoped to the latest ready snapshot as well.
    """
    llm_last_run_title = state_messages.check_title("llm_last_run")
    if snapshot_id is None:
        msg = state_messages.check_message("llm_last_run", "no_snapshot")
        return DiagnosticCheck(
            check_id="llm_last_run",
            category="llm",
            title=llm_last_run_title,
            severity="unknown",
            detail=msg["detail"],
            impact=msg["impact"],
            remediation=msg["remediation"],
            related_pages=[PAGE_REPOSITORY],
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_REPOSITORY,
            fix_anchor=ANCHOR_SNAPSHOT_CREATE,
        )
    row = conn.execute(
        "SELECT id, run_type, status, error_details, completed_at, started_at, is_mock "
        "FROM intelligence_runs "
        "WHERE system_id = ? AND snapshot_id = ? AND decision_method = 'reasoning_llm' "
        "ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row is None:
        # Informational state, not a defect: nothing has exercised the
        # configured reasoning model yet. This check must not present itself
        # as the cause of other warnings (e.g. an empty capability hierarchy,
        # whose build is deterministic and would not record a reasoning run),
        # and its remediation must name the operations that actually record
        # reasoning runs instead of a generic "run Build".
        msg = state_messages.check_message("llm_last_run", "no_run_yet")
        return DiagnosticCheck(
            check_id="llm_last_run",
            category="llm",
            title=llm_last_run_title,
            severity="unknown",
            detail=msg["detail"],
            impact=msg["impact"],
            remediation=msg["remediation"],
            related_pages=[PAGE_SYSTEM_UNDERSTANDING],
            related_pipeline_steps=[
                "documentation_claims_scanned",
                "docs_code_reconciled",
            ],
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_SYSTEM_UNDERSTANDING,
            fix_anchor=ANCHOR_BUILD,
        )
    observed_at = row["completed_at"] or row["started_at"]
    if row["status"] == "failed":
        guidance = _last_run_failure_guidance(row["run_type"], row["error_details"])
        msg = state_messages.check_message("llm_last_run", "failed")
        return DiagnosticCheck(
            check_id="llm_last_run",
            category="llm",
            title=llm_last_run_title,
            severity="error",
            detail=msg["detail"].format(run_id=row["id"], run_type=row["run_type"]),
            impact=msg["impact"],
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
    msg = state_messages.check_message("llm_last_run", "ok")
    return DiagnosticCheck(
        check_id="llm_last_run",
        category="llm",
        title=llm_last_run_title,
        severity="ok",
        detail=msg["detail"].format(
            run_id=row["id"], run_type=row["run_type"], status=row["status"], mock_note=mock_note
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
    msg = state_messages.shared_check_message("no_ready_snapshot")
    return DiagnosticCheck(
        severity="blocked",
        detail=msg["detail"],
        impact=msg["impact"],
        remediation=msg["remediation"],
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
        detail = state_messages.shared_check_message("baseline_ok.current")["detail"].format(label=label)
    elif impact.status == "unchanged":
        detail = state_messages.shared_check_message("baseline_ok.reusable")["detail"].format(
            label=label, baseline_snapshot_id=baseline.snapshot_id
        )
    else:
        detail = state_messages.shared_check_message("baseline_ok.other")["detail"].format(label=label)
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
    msg = state_messages.shared_check_message("baseline_impacted")
    target = "System Purpose" if purpose else "Core Capabilities"
    title = msg["title_purpose"] if purpose else msg["title_capabilities"]
    reason = " ".join(impact.reasons) if impact.reasons else msg["default_reason"]
    payload = {k: v for k, v in base.items() if k not in {"title", "snapshot_id"}}
    return DiagnosticCheck(
        severity="warning",
        detail=msg["detail"].format(target=target, baseline_snapshot_id=baseline.snapshot_id, reason=reason),
        impact=msg["impact"],
        remediation=msg["remediation"].format(target=target),
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
    msg = state_messages.shared_check_message("unconfirmed_understanding")
    target = "System Purpose" if purpose else "Core Capabilities"
    title = msg["title_purpose"] if purpose else msg["title_capabilities"]
    count = baseline.purpose_count if purpose else baseline.capability_count
    payload = {k: v for k, v in base.items() if k not in {"title", "snapshot_id"}}
    return DiagnosticCheck(
        severity="warning",
        detail=msg["detail"].format(target=target, count=count),
        impact=msg["impact"],
        remediation=msg["remediation"].format(target=target),
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
        title=state_messages.check_title("system_purpose"),
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
            detail=state_messages.shared_check_message("system_purpose.satisfied")["detail"],
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
    msg = state_messages.shared_check_message("system_purpose.missing")
    return DiagnosticCheck(
        severity="warning",
        detail=msg["detail"],
        impact=msg["impact"],
        remediation=msg["remediation"],
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
        title=state_messages.check_title("system_capabilities"),
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
            detail=state_messages.shared_check_message("system_capabilities.satisfied")["detail"].format(count=status.count),
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
    msg = state_messages.shared_check_message("system_capabilities.missing")
    return DiagnosticCheck(
        severity="warning",
        detail=msg["detail"],
        impact=msg["impact"],
        remediation=msg["remediation"],
        fix_kind=FIX_KIND_NAVIGATE,
        fix_page=PAGE_INTERVIEW,
        fix_anchor=ANCHOR_INTERVIEW_CAPABILITIES,
        **{k: v for k, v in base.items() if k != "snapshot_id"},
    )


def _reasoning_unavailable_check(base: dict, *, detail: str) -> DiagnosticCheck:
    """Blocked result when a step needs reasoning but no reasoning model is set.

    ``detail`` differs per pipeline step; everything else is shared.
    """
    shared = state_messages.shared_check_message("reasoning_unavailable")
    return DiagnosticCheck(
        severity="blocked",
        detail=detail,
        impact=shared["impact"],
        remediation=shared["remediation"],
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
                detail=state_messages.shared_check_message("run_backed.not_run_blocked_reasoning")["detail"],
            )
        not_run = state_messages.shared_check_message("run_backed.not_run")
        return DiagnosticCheck(
            severity="warning",
            detail=not_run["detail"],
            impact=not_run["impact"],
            remediation=not_run_remediation,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_SYSTEM_UNDERSTANDING,
            fix_anchor=ANCHOR_BUILD,
            **base,
        )
    if row["status"] == "completed":
        return DiagnosticCheck(
            severity="ok",
            detail=state_messages.shared_check_message("run_backed.completed")["detail"].format(
                run_id=row["id"], run_type=row["run_type"]
            ),
            impact="",
            remediation="",
            **base,
        )
    err = state_messages.shared_check_message("run_backed.error")
    return DiagnosticCheck(
        severity="error",
        detail=err["detail"].format(run_id=row["id"], run_type=row["run_type"], status=row["status"]),
        impact=err["impact"],
        remediation=err["remediation"],
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
        title=state_messages.check_title("pipeline_capability_hierarchy"),
        run_types=["capability_hierarchy"],
        pipeline_steps=["capability_hierarchy_ready"],
        requires_reasoning=True,
        reasoning_available=reasoning_available,
        not_run_remediation=state_messages.pipeline_not_run_remediation("pipeline_capability_hierarchy"),
    )
    if (
        check.severity == "ok"
        and snapshot_id is not None
        and system_state._capability_count_in_current_snapshot(conn, system_id, snapshot_id) == 0
    ):
        empty = state_messages.check_message("pipeline_capability_hierarchy", "zero_capabilities")
        return replace(
            check,
            severity="warning",
            detail=check.detail + empty["detail_suffix"],
            impact=empty["impact"],
            remediation=empty["remediation"],
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
                detail=state_messages.shared_check_message("artifact_backed.not_run_blocked_reasoning")["detail"],
            )
        not_run = state_messages.shared_check_message("artifact_backed.not_run")
        return DiagnosticCheck(
            severity="warning",
            detail=not_run["detail"],
            impact=not_run["impact"],
            remediation=not_run_remediation,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_SYSTEM_UNDERSTANDING,
            fix_anchor=ANCHOR_BUILD,
            **base,
        )
    return DiagnosticCheck(
        severity="ok",
        detail=state_messages.shared_check_message("artifact_backed.ok")["detail"],
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
        not_run = state_messages.shared_check_message("build_step.not_run")
        return DiagnosticCheck(
            severity="warning",
            detail=not_run["detail"],
            impact=not_run["impact"],
            remediation=not_run_remediation,
            fix_kind=FIX_KIND_NAVIGATE,
            fix_page=PAGE_SYSTEM_UNDERSTANDING,
            fix_anchor=ANCHOR_BUILD,
            **base,
        )
    if row["status"] == "completed":
        return DiagnosticCheck(
            severity="ok",
            detail=state_messages.shared_check_message("build_step.completed")["detail"].format(
                run_id=row["id"], step=step
            ),
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
    err = state_messages.shared_check_message("build_step.error")
    return DiagnosticCheck(
        severity=severity,
        detail=err["detail"].format(run_id=row["id"], step=step, status=row["status"]),
        impact=err["impact"],
        remediation=err["remediation"],
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

        checks.append(_check_last_reasoning_run(conn, system_id, snapshot_id))

        checks.append(
            _run_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_symbol_index",
                title=state_messages.check_title("pipeline_symbol_index"),
                run_types=["symbol_index"],
                pipeline_steps=["symbols_indexed"],
                requires_reasoning=False,
                reasoning_available=reasoning_available,
                not_run_remediation=state_messages.pipeline_not_run_remediation("pipeline_symbol_index"),
            )
        )
        checks.append(
            _run_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_entrypoint_index",
                title=state_messages.check_title("pipeline_entrypoint_index"),
                run_types=["entrypoint_index"],
                pipeline_steps=["entrypoints_discovered"],
                requires_reasoning=False,
                reasoning_available=reasoning_available,
                not_run_remediation=state_messages.pipeline_not_run_remediation("pipeline_entrypoint_index"),
            )
        )
        checks.append(
            _build_step_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_documentation_index",
                title=state_messages.check_title("pipeline_documentation_index"),
                step="documentation_index",
                pipeline_steps=["documentation_indexed"],
                not_run_remediation=state_messages.pipeline_not_run_remediation("pipeline_documentation_index"),
            )
        )
        checks.append(
            _artifact_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_understanding_graph",
                title=state_messages.check_title("pipeline_understanding_graph"),
                artifact_sql=(
                    "SELECT id FROM understanding_graph_snapshots "
                    "WHERE system_id = ? AND snapshot_id = ? LIMIT 1"
                ),
                pipeline_steps=["documentation_claims_scanned", "docs_code_reconciled"],
                requires_reasoning=True,
                reasoning_available=reasoning_available,
                not_run_remediation=state_messages.pipeline_not_run_remediation("pipeline_understanding_graph"),
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
