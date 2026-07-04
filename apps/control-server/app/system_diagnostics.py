"""Deterministic system settings diagnostics (Issue #101).

Static, LLM-free health checks for required configuration: environment
variables, filesystem paths and permissions, provider/model consistency,
and pipeline prerequisites. Failures that can only be observed at runtime
(LLM timeouts, auth errors, snapshot/index failures) are surfaced verbatim
from the most recent persisted run records; they are never interpreted or
classified by heuristics.

Every decision in this module is a finite-set or structural validation
(Principle 6): env var presence, enum membership, path existence and
read/write permission, known model-family prefix matching, and persisted
run status values.

probe-agent:
  role: Deterministic system settings diagnostics service
  capability: system-configuration-health
  element_type: core
  consumers: [dashboard, control-server]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify that missing/invalid required configuration and last observed run failures are reported with impact and remediation, without any LLM call.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .db import get_conn
from .llm import PROVIDER_KEY_ENV, is_reasoning_model

# Severity vocabulary from Issue #101. Order = worst first.
SEVERITY_ORDER = ["error", "blocked", "warning", "unknown", "ok"]

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
    category: str  # repository | database | auth | llm | pipeline
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
            title="Repository roots configured",
            severity="error",
            detail="PROBE_REPOSITORY_ROOTS is not set.",
            impact=(
                "All repository access is disabled: repository configuration, "
                "snapshots, and every System Understanding step that reads code "
                "will fail or stay missing."
            ),
            remediation=(
                "Set PROBE_REPOSITORY_ROOTS to the absolute path(s) containing "
                "your Git repositories (path-separator separated) and restart "
                "the Control Server."
            ),
            related_env=["PROBE_REPOSITORY_ROOTS"],
            related_pages=["/repository"],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
        )
    roots = [r.strip() for r in raw.split(os.pathsep) if r.strip()]
    missing = [r for r in roots if not os.path.isdir(r)]
    unreadable = [
        r for r in roots if os.path.isdir(r) and not os.access(r, os.R_OK)
    ]
    if missing or unreadable:
        problems = []
        if missing:
            problems.append(f"missing directories: {', '.join(missing)}")
        if unreadable:
            problems.append(f"unreadable directories: {', '.join(unreadable)}")
        return DiagnosticCheck(
            check_id="repository_roots",
            category="repository",
            title="Repository roots configured",
            severity="error",
            detail=f"PROBE_REPOSITORY_ROOTS contains {'; '.join(problems)}.",
            impact="Repositories under these roots cannot be discovered or snapshotted.",
            remediation=(
                "Fix the paths in PROBE_REPOSITORY_ROOTS, or create the "
                "directories and grant read permission to the Control Server "
                "process."
            ),
            related_env=["PROBE_REPOSITORY_ROOTS"],
            related_paths=missing + unreadable,
            related_pages=["/repository"],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
        )
    return DiagnosticCheck(
        check_id="repository_roots",
        category="repository",
        title="Repository roots configured",
        severity="ok",
        detail=f"{len(roots)} repository root(s) exist and are readable.",
        impact="",
        remediation="",
        related_env=["PROBE_REPOSITORY_ROOTS"],
        related_paths=roots,
        related_pages=["/repository"],
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
            title="Repository configured for this system",
            severity="warning",
            detail="No repository is configured for the selected system.",
            impact=(
                "Snapshots cannot be created, so the whole System Understanding "
                "pipeline stays missing."
            ),
            remediation="Open the Repository tab and select a repository path.",
            related_pages=["/repository"],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
        )
    repo_path = row["repo_path"]
    problems: List[str] = []
    if not os.path.isdir(repo_path):
        problems.append("path does not exist or is not a directory")
    else:
        if not os.access(repo_path, os.R_OK):
            problems.append("path is not readable")
        if not os.path.exists(os.path.join(repo_path, ".git")):
            problems.append("path is not a Git repository (missing .git)")
    if problems:
        return DiagnosticCheck(
            check_id="repository_config",
            category="repository",
            title="Repository configured for this system",
            severity="error",
            detail=f"Configured repository path {repo_path}: {'; '.join(problems)}.",
            impact="New snapshots and repository reads will fail for this system.",
            remediation=(
                "Fix the mount/path so the configured repository exists, is "
                "readable, and is a Git repository, or reconfigure the "
                "repository in the Repository tab."
            ),
            related_env=["PROBE_REPOSITORY_ROOTS"],
            related_paths=[repo_path],
            related_pages=["/repository"],
            related_pipeline_steps=["repository_configured", "snapshot_ready"],
        )
    return DiagnosticCheck(
        check_id="repository_config",
        category="repository",
        title="Repository configured for this system",
        severity="ok",
        detail=f"Repository path {repo_path} exists, is readable, and is a Git repository.",
        impact="",
        remediation="",
        related_paths=[repo_path],
        related_pages=["/repository"],
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
                title="Ready repository snapshot",
                severity="warning",
                detail="No snapshot has been created for this system yet.",
                impact=(
                    "Documentation indexing, symbol indexing, entrypoint "
                    "discovery, and capability hierarchy all require a ready "
                    "snapshot."
                ),
                remediation="Create a snapshot from the Repository tab.",
                related_pages=["/repository"],
                related_pipeline_steps=["snapshot_ready"],
            )
        severity = "error" if last_error else "warning"
        return DiagnosticCheck(
            check_id="snapshot_status",
            category="repository",
            title="Ready repository snapshot",
            severity=severity,
            detail=f"No ready snapshot; latest snapshot #{latest['id']} status is '{latest['status']}'.",
            impact="Every pipeline step that reads snapshot contents is blocked.",
            remediation=(
                "Retry snapshot creation from the Repository tab. If it keeps "
                "failing, check the last observed error below and the "
                "repository path diagnostics."
            ),
            related_pages=["/repository"],
            related_pipeline_steps=["snapshot_ready"],
            last_observed_error=last_error,
        )

    indexed_count = conn.execute(
        "SELECT COUNT(*) FROM snapshot_files WHERE snapshot_id = ? AND inclusion_status = 'indexed'",
        (ready["id"],),
    ).fetchone()[0]
    if indexed_count == 0:
        return DiagnosticCheck(
            check_id="snapshot_status",
            category="repository",
            title="Ready repository snapshot",
            severity="warning",
            detail=(
                f"Latest ready snapshot #{ready['id']} contains 0 indexed files. "
                "Include/exclude patterns may be filtering out everything."
            ),
            impact=(
                "Draft generation, symbol indexing, and entrypoint discovery "
                "will produce empty results."
            ),
            remediation=(
                "Review the include/exclude patterns in the Repository tab and "
                "re-create the snapshot."
            ),
            related_pages=["/repository"],
            related_pipeline_steps=["snapshot_ready", "symbols_indexed", "documentation_indexed"],
            last_observed_error=last_error,
        )
    return DiagnosticCheck(
        check_id="snapshot_status",
        category="repository",
        title="Ready repository snapshot",
        severity="ok",
        detail=f"Latest ready snapshot #{ready['id']} has {indexed_count} indexed file(s).",
        impact="",
        remediation="",
        related_pages=["/repository"],
        related_pipeline_steps=["snapshot_ready"],
        last_observed_error=last_error,
    )


def _check_database_storage() -> DiagnosticCheck:
    from .db import db_path

    path = db_path()
    directory = os.path.dirname(os.path.abspath(path)) or "."
    problems: List[str] = []
    if not os.path.isdir(directory):
        problems.append(f"database directory does not exist: {directory}")
    else:
        if not os.access(directory, os.W_OK):
            problems.append(f"database directory is not writable: {directory}")
        if os.path.exists(path):
            if not os.access(path, os.R_OK):
                problems.append("database file is not readable")
            if not os.access(path, os.W_OK):
                problems.append("database file is not writable")
    if problems:
        return DiagnosticCheck(
            check_id="database_storage",
            category="database",
            title="Database storage",
            severity="error",
            detail="; ".join(problems) + ".",
            impact=(
                "Traces, policies, snapshots, and intelligence runs cannot be "
                "persisted; most write operations will fail."
            ),
            remediation=(
                "Point PROBE_DB_PATH at a writable location, or fix the "
                "directory/file permissions."
            ),
            related_env=["PROBE_DB_PATH"],
            related_paths=[path],
        )
    return DiagnosticCheck(
        check_id="database_storage",
        category="database",
        title="Database storage",
        severity="ok",
        detail=f"Database path {path} is readable and writable.",
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
            title="Auth and system scope",
            severity="error",
            detail=f"Selected system id {system_id} does not exist.",
            impact="All system-scoped requests fail with 404.",
            remediation="Select an existing system in the header, or create one.",
        )
    if users == 0 and not legacy_keys:
        return DiagnosticCheck(
            check_id="auth_scope",
            category="auth",
            title="Auth and system scope",
            severity="warning",
            detail=(
                "No active users and no CONTROL_API_KEYS: the server is running "
                "without authentication (MVP compatibility mode)."
            ),
            impact="Anyone who can reach the Control Server has full access.",
            remediation=(
                "Set CONTROL_ADMIN_USERNAME / CONTROL_ADMIN_PASSWORD to "
                "bootstrap an admin user, or configure CONTROL_API_KEYS."
            ),
            related_env=[
                "CONTROL_ADMIN_USERNAME",
                "CONTROL_ADMIN_PASSWORD",
                "CONTROL_API_KEYS",
            ],
            related_pages=["/admin"],
        )
    return DiagnosticCheck(
        check_id="auth_scope",
        category="auth",
        title="Auth and system scope",
        severity="ok",
        detail=(
            f"Selected system '{system_row['name']}' exists; "
            f"{users} active user(s)"
            + (", legacy API keys configured" if legacy_keys else "")
            + "."
        ),
        impact="",
        remediation="",
        related_env=["CONTROL_API_KEYS"],
    )


def _api_key_status(provider: str) -> tuple:
    """Return (has_matching_key, detail_fragment) for a non-mock provider."""
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
            f"No LLM_API_KEY or {specific_env} is set; found {', '.join(other_set)}, "
            f"which does not correspond to provider '{provider}'."
        )
    return False, f"Neither LLM_API_KEY nor {specific_env} is set."


def _positive_number_problem(env_name: str, *, integer: bool = False) -> Optional[str]:
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        return f"{env_name}={raw!r} is not a valid number"
    if value <= 0:
        return f"{env_name}={raw!r} must be positive"
    return None


def _check_llm_base_config() -> DiagnosticCheck:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    problems: List[str] = []
    severity = "ok"

    if provider not in KNOWN_PROVIDERS:
        problems.append(
            f"LLM_PROVIDER={provider!r} is not a supported provider "
            f"({', '.join(sorted(KNOWN_PROVIDERS))})"
        )
        severity = "error"
    elif provider != "mock":
        has_key, key_detail = _api_key_status(provider)
        if not has_key:
            problems.append(key_detail.rstrip("."))
            severity = "error"

    timeout_problem = _positive_number_problem("LLM_TIMEOUT")
    if timeout_problem:
        problems.append(timeout_problem)
        if severity == "ok":
            severity = "warning"

    detail = (
        "; ".join(problems) + "."
        if problems
        else f"LLM_PROVIDER={provider} with a usable API key configuration."
    )
    if provider == "mock" and not problems:
        severity = "warning"
        detail = (
            "LLM_PROVIDER=mock: all LLM output is deterministic mock data for "
            "tests/local smoke checks, and reasoning-required pipeline steps "
            "are blocked."
        )
    return DiagnosticCheck(
        check_id="llm_base_config",
        category="llm",
        title="LLM provider configuration",
        severity=severity,
        detail=detail,
        impact=(
            "Generate & Evaluate and every reasoning-model feature fail at "
            "call time when the provider or API key is invalid."
            if severity == "error"
            else ("Mock output must not be treated as real analysis." if provider == "mock" else "")
        ),
        remediation=(
            "Set LLM_PROVIDER to one of openai/anthropic/gemini/mock and "
            "provide LLM_API_KEY (or the provider-specific key) for non-mock "
            "providers."
            if problems or provider == "mock"
            else ""
        ),
        related_env=["LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT"],
        related_pages=["/generation"],
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
        else " (INTELLIGENCE_LLM_PROVIDER is empty, falling back to LLM_PROVIDER)"
    )

    problems: List[str] = []
    severity = "ok"

    if provider not in KNOWN_PROVIDERS:
        problems.append(
            f"effective intelligence provider {provider!r}{fallback_note} is not "
            f"a supported provider ({', '.join(sorted(KNOWN_PROVIDERS))})"
        )
        severity = "error"
    else:
        family = _model_family_matches(provider, model)
        if family is False:
            problems.append(
                f"model {model!r} belongs to a different provider's model family "
                f"than effective provider {provider!r}{fallback_note}"
            )
            severity = "error"
        elif family is None and provider != "mock":
            problems.append(
                f"model {model!r} does not match any known model family for "
                f"provider {provider!r}; it may be an invalid model id, and its "
                "reasoning capability cannot be verified statically"
            )
            severity = "warning"

        if provider == "mock":
            problems.append(
                "effective intelligence provider is 'mock'; reasoning-required "
                "steps are blocked and any output is visibly mock data"
            )
            severity = _worst_severity([severity, "blocked"])
        elif family is True and severity != "error" and not is_reasoning_model(provider, model):
            problems.append(
                f"model {model!r} is not reasoning-capable; documentation "
                "indexing, claim scanning, and capability hierarchy generation "
                "require a reasoning model and will be blocked"
            )
            severity = _worst_severity([severity, "blocked"])

        if provider != "mock":
            has_key, key_detail = _api_key_status(provider)
            if not has_key:
                problems.append(key_detail.rstrip("."))
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
            "INTELLIGENCE_LLM_* is not set; intelligence features use the "
            "generic LLM_PROVIDER/LLM_MODEL configuration"
        )

    detail = (
        "; ".join(problems) + "."
        if problems
        else f"Effective intelligence model: {provider}/{model}{fallback_note}."
    )
    return DiagnosticCheck(
        check_id="intelligence_llm_config",
        category="llm",
        title="Intelligence reasoning model configuration",
        severity=severity,
        detail=detail,
        impact=(
            "Claim scanning, docs-code reconciliation, "
            "and capability hierarchy generation fail or stay blocked without a "
            "valid reasoning model; there is no heuristic fallback."
            if severity in ("error", "blocked", "warning")
            else ""
        ),
        remediation=(
            "Set INTELLIGENCE_LLM_PROVIDER and INTELLIGENCE_LLM_MODEL to a "
            "reasoning-capable provider/model pair (and the matching API key), "
            "then re-run the System Understanding build."
            if severity in ("error", "blocked", "warning")
            else ""
        ),
        related_env=related_env,
        related_pages=["/system-understanding", "/repository"],
        related_pipeline_steps=reasoning_steps,
    )


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
            title="Last reasoning-model run",
            severity="unknown",
            detail=(
                "No reasoning-model run has been recorded for this system yet, "
                "so runtime problems (timeout, auth, invalid model, parse "
                "errors) have not been observed."
            ),
            impact="Configuration may look valid but still fail at call time.",
            remediation=(
                "Run a System Understanding build or draft generation to "
                "exercise the configured model."
            ),
            related_pages=["/system-understanding"],
        )
    observed_at = row["completed_at"] or row["started_at"]
    if row["status"] == "failed":
        return DiagnosticCheck(
            check_id="llm_last_run",
            category="llm",
            title="Last reasoning-model run",
            severity="error",
            detail=(
                f"The most recent reasoning run (#{row['id']}, {row['run_type']}) "
                "failed."
            ),
            impact=(
                "Reasoning-backed artifacts (drafts, claims, capability "
                "hierarchy) were not produced or are stale."
            ),
            remediation=(
                "Read the last observed error below; fix the configuration it "
                "points at (API key, model id, timeout) and re-run the build."
            ),
            related_env=[
                "INTELLIGENCE_LLM_PROVIDER",
                "INTELLIGENCE_LLM_MODEL",
                "LLM_API_KEY",
                "INTELLIGENCE_LLM_TIMEOUT",
            ],
            related_pages=["/system-understanding"],
            related_pipeline_steps=[
                "documentation_indexed",
                "documentation_claims_scanned",
                "capability_hierarchy_ready",
            ],
            last_observed_error=LastObservedError(
                source=f"intelligence_runs#{row['id']}:{row['run_type']}",
                status=row["status"],
                error=row["error_details"],
                observed_at=observed_at,
            ),
        )
    mock_note = " (mock run)" if row["is_mock"] else ""
    return DiagnosticCheck(
        check_id="llm_last_run",
        category="llm",
        title="Last reasoning-model run",
        severity="ok",
        detail=(
            f"The most recent reasoning run (#{row['id']}, {row['run_type']}) "
            f"has status '{row['status']}'{mock_note}."
        ),
        impact="",
        remediation="",
        related_pages=["/system-understanding"],
    )


def _latest_ready_snapshot_id(conn, system_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM repository_snapshots WHERE system_id = ? AND status = 'ready' "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    return row["id"] if row else None


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
        related_pages=["/system-understanding"],
        related_pipeline_steps=pipeline_steps,
    )
    if snapshot_id is None:
        return DiagnosticCheck(
            severity="blocked",
            detail="No ready snapshot exists, so this step cannot run.",
            impact="The step stays missing until a snapshot is created.",
            remediation="Create a snapshot from the Repository tab first.",
            **base,
        )
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
            return DiagnosticCheck(
                severity="blocked",
                detail=(
                    "This step has never run and requires a reasoning model, "
                    "which is not configured."
                ),
                impact="The step shows as blocked/missing in System Understanding.",
                remediation=(
                    "Fix the intelligence reasoning model configuration "
                    "(see the LLM checks), then run a build."
                ),
                **base,
            )
        return DiagnosticCheck(
            severity="warning",
            detail="This step has not run for the current snapshot.",
            impact="The step shows as missing in System Understanding.",
            remediation=not_run_remediation,
            **base,
        )
    if row["status"] == "completed":
        return DiagnosticCheck(
            severity="ok",
            detail=f"Latest run (#{row['id']}, {row['run_type']}) completed.",
            impact="",
            remediation="",
            **base,
        )
    return DiagnosticCheck(
        severity="error",
        detail=(
            f"Latest run (#{row['id']}, {row['run_type']}) has status "
            f"'{row['status']}'."
        ),
        impact="The step's artifacts are missing or stale.",
        remediation=(
            "Read the last observed error below, fix the root cause, and "
            "re-run the build."
        ),
        last_observed_error=LastObservedError(
            source=f"intelligence_runs#{row['id']}:{row['run_type']}",
            status=row["status"],
            error=row["error_details"],
            observed_at=row["completed_at"] or row["started_at"],
        ),
        **base,
    )


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
        related_pages=["/system-understanding"],
        related_pipeline_steps=pipeline_steps,
    )
    if snapshot_id is None:
        return DiagnosticCheck(
            severity="blocked",
            detail="No ready snapshot exists, so this step cannot run.",
            impact="The step stays missing until a snapshot is created.",
            remediation="Create a snapshot from the Repository tab first.",
            **base,
        )
    row = conn.execute(artifact_sql, (system_id, snapshot_id)).fetchone()
    if row is None:
        if requires_reasoning and not reasoning_available:
            return DiagnosticCheck(
                severity="blocked",
                detail=(
                    "No artifact exists for the current snapshot and the "
                    "required reasoning model is not configured."
                ),
                impact="The step shows as blocked/missing in System Understanding.",
                remediation=(
                    "Fix the intelligence reasoning model configuration "
                    "(see the LLM checks), then run a build."
                ),
                **base,
            )
        return DiagnosticCheck(
            severity="warning",
            detail="No artifact exists for the current snapshot.",
            impact="The step shows as missing in System Understanding.",
            remediation=not_run_remediation,
            **base,
        )
    return DiagnosticCheck(
        severity="ok",
        detail="Artifacts exist for the current snapshot.",
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
        related_pages=["/system-understanding"],
        related_pipeline_steps=pipeline_steps,
    )
    if snapshot_id is None:
        return DiagnosticCheck(
            severity="blocked",
            detail="No ready snapshot exists, so this step cannot run.",
            impact="The step stays missing until a snapshot is created.",
            remediation="Create a snapshot from the Repository tab first.",
            **base,
        )
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
            detail="This build step has not run for the current snapshot.",
            impact="The step shows as missing in System Understanding.",
            remediation=not_run_remediation,
            **base,
        )
    if row["status"] == "completed":
        return DiagnosticCheck(
            severity="ok",
            detail=f"Latest build step (#{row['id']}, {step}) completed.",
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
        detail=f"Latest build step (#{row['id']}, {step}) has status '{row['status']}'.",
        impact="The step's artifacts are missing or stale.",
        remediation="Read the build step error below, fix the root cause, and re-run the build.",
        last_observed_error=LastObservedError(
            source=f"system_understanding_build_steps#{row['id']}:{step}",
            status=row["status"],
            error=row["error"],
            observed_at=row["completed_at"] or row["started_at"],
        ),
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
        checks.append(_check_last_reasoning_run(conn, system_id))

        snapshot_id = _latest_ready_snapshot_id(conn, system_id)
        checks.append(
            _run_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_symbol_index",
                title="Symbol index run",
                run_types=["symbol_index"],
                pipeline_steps=["symbols_indexed"],
                requires_reasoning=False,
                reasoning_available=reasoning_available,
                not_run_remediation="Run Build / Refresh in System Understanding to index code symbols.",
            )
        )
        checks.append(
            _run_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_entrypoint_index",
                title="Entrypoint index run",
                run_types=["entrypoint_index"],
                pipeline_steps=["entrypoints_discovered"],
                requires_reasoning=False,
                reasoning_available=reasoning_available,
                not_run_remediation="Run Build / Refresh in System Understanding to discover entrypoints.",
            )
        )
        checks.append(
            _build_step_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_documentation_index",
                title="Documentation index build step",
                step="documentation_index",
                pipeline_steps=["documentation_indexed"],
                not_run_remediation="Run Build / Refresh in System Understanding to index documentation chunks.",
            )
        )
        checks.append(
            _artifact_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_understanding_graph",
                title="Understanding graph (documentation claims)",
                artifact_sql=(
                    "SELECT id FROM understanding_graph_snapshots "
                    "WHERE system_id = ? AND snapshot_id = ? LIMIT 1"
                ),
                pipeline_steps=["documentation_claims_scanned", "docs_code_reconciled"],
                requires_reasoning=True,
                reasoning_available=reasoning_available,
                not_run_remediation="Run Build / Refresh in System Understanding to scan documentation claims.",
            )
        )
        checks.append(
            _run_backed_pipeline_check(
                conn, system_id, snapshot_id,
                check_id="pipeline_capability_hierarchy",
                title="Capability hierarchy run",
                run_types=["capability_hierarchy"],
                pipeline_steps=["capability_hierarchy_ready"],
                requires_reasoning=True,
                reasoning_available=reasoning_available,
                not_run_remediation="Run Build / Refresh in System Understanding to generate the capability hierarchy.",
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
