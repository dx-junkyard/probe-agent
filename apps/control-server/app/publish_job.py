"""Publish job state machine: commit/push/PR creation for an approved probe
patch against a connected GitHub repository (Issue #216, sub-task 3).

Two explicit phases, matching Principle 8's approval gate:

* prepare (started when the job is created): pending -> authenticating ->
  fetching -> checking_out -> applying_patch -> validating ->
  awaiting_approval. Everything up to and including applying the patch to an
  isolated job worktree happens automatically; the job then *stops* and
  waits for a human.
* publish (started only by an explicit ``approve`` call): awaiting_approval
  -> committing -> pushing -> creating_pr -> completed. probe-agent never
  merges or closes the resulting Pull Request.

Same pattern as ``system_understanding_jobs.py``: a daemon thread runs the
phase, wrapped in an exception-safe wrapper so a crash still marks the job
failed instead of leaving it stuck. Unlike that job type there is no
per-step table -- ``publish_jobs.status`` *is* the step, since the sequence
is short, linear, and has a single explicit human gate in the middle.

Git/GitHub-API safety, all fail-closed (Principle 5/6/8):

* All git/API operations for a phase run inside a single
  ``repo_manager.connection_lock(connection_id)`` (an RLock, so the nested
  calls into ``repo_manager`` helpers that also take the lock do not
  deadlock) -- this serializes every publish job against every other
  git/publish operation on the same connection.
* The remote base branch SHA is re-resolved and compared against the
  patch's pinned commit SHA twice: once when entering ``fetching`` (prepare)
  and once more immediately before ``pushing`` (publish). Any mismatch is a
  stale patch -- the job fails closed with an explanation to create a new
  snapshot / regenerate / re-validate. There is no automatic rebase.
* The branch pushed to is always server-generated
  (``publish_guards.generate_branch_name``) and validated
  (``publish_guards.validate_push_target``) to never equal the base/default
  branch; force push is never used (``git push origin
  HEAD:refs/heads/<branch>``, no ``--force``, remote name always ``origin``).
* Only files present in the patch diff are staged, and each path is
  structurally validated (`publish_guards.validate_patch_file_path`) before
  `git add`.
* An installation token is created fresh inside each phase, held only in a
  local variable, and never written to ``publish_jobs`` or logged --
  ``error`` is always passed through ``github_app._sanitize`` first.
* Every terminal state (completed/failed/cancelled) cleans up the job
  worktree in a ``finally``-equivalent path and records ``cleanup_state``.
* Explicit Disconnect revokes publish permission immediately (Issue #227,
  enforced in ``routes/github_connections.py::delete_connection``): every
  job still in the prepare phase or awaiting approval is cancelled in the
  same transaction as the disconnect, ``approve_publish_job``'s
  compare-and-set requires the connection to still be ``connected``, and
  ``_require_connection_still_connected`` re-checks the connection at phase
  entry and immediately before the push, on top of
  ``_require_publish_installation_assignment``'s check right before every
  token issuance. A job that has already reached an in-flight publish phase
  (committing/pushing/creating_pr) blocks the disconnect instead of being
  interrupted mid-push.

Retry / recovery (Issue #226): a publish-phase failure (post-approval) no
longer always dead-ends at terminal ``failed``. ``_run_publish_phase`` /
``_run_reconcile_phase`` classify failures with dedicated exception
subclasses of ``PublishJobConflict``:

* ``StaleBaseBranchError`` (the existing "base branch moved" case) and
  ``ConnectionRevokedError`` (the connection was disconnected mid-flight --
  its ``connection_id`` can never become ``connected`` again, since
  reconnect always creates a new connection row) stay terminal ``failed``.
* ``ManualInterventionRequiredError`` (only raised during reconcile, when
  the remote branch exists but does not match the job's recorded commit,
  or a recreated worktree fails to re-apply the patch) becomes
  ``manual_intervention_required`` -- a resting state that is never
  auto-retried and never auto-overwritten/force-pushed.
* Everything else (plain ``PublishJobConflict``, ``GitHubAppError``,
  ``RepoManagerError``, ``PublishGuardError``, an unexpected exception)
  becomes ``retryable_failed``.

``retry_publish_job`` (manual, any retry count) and
``app/publish_recovery.py::auto_retry_eligible_jobs`` (automatic, capped by
``PUBLISH_AUTO_RETRY_MAX``) both compare-and-set a resting job to
``reconciling`` and run ``_run_reconcile_phase``, which re-derives the
correct next step from the *actual* remote state (same branch, same commit
pinning as the original job -- never a new branch, never a force push):
skip the commit/push if the remote branch already has the recorded commit
(recovering or creating the PR), refuse and require manual intervention if
the remote branch exists with a different commit, or recreate the worktree
and redo the normal commit/push/PR sequence if the branch was never pushed.
A DB-backed lease (``publish_connection_leases``) guards a connection
across process restarts on top of ``repo_manager.connection_lock``'s
in-process RLock. Every status transition (whether driven by ``_set_status``
or one of the explicit compare-and-set transitions in ``approve``/``cancel``/
``retry``/recovery) is recorded append-only in ``publish_audit_events`` in
the same transaction that performs it.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import github_app, publish_guards, repo_manager
from .github_installations import (
    GitHubInstallationAccessError,
    require_active_installation_assignment,
)
from .db import get_conn
from .github_app import GitHubAppError
from .patch_generator import apply_unified_diff
from .publish_audit import record_publish_audit_event
from .repo_manager import RepoManagerError

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")

# Resting states a job can sit in after a publish-phase failure (Issue #226):
# neither terminal nor actively progressing. Only retry/cancel/disconnect
# transitions move a job out of these.
_RETRYABLE_STATUSES = ("retryable_failed", "manual_intervention_required")
_CANCELLABLE_STATUSES = ("pending", "awaiting_approval") + _RETRYABLE_STATUSES


class PublishJobError(RuntimeError):
    pass


class PublishJobNotFound(PublishJobError):
    pass


class PublishJobConflict(PublishJobError):
    pass


class StaleBaseBranchError(PublishJobConflict):
    """Base branch moved since the patch was pinned; terminal, no auto-rebase
    (Principle 5/8) -- distinguished from other post-approval failures so
    ``_run_publish_phase`` / ``_run_reconcile_phase`` classify it as terminal
    ``failed`` instead of ``retryable_failed`` (Issue #226)."""


class ConnectionRevokedError(PublishJobConflict):
    """The connection was disconnected mid-flight (Issue #227); terminal --
    this job's ``connection_id`` can never become ``connected`` again
    (reconnect always creates a new connection row), so retrying under the
    same connection can never succeed (Issue #226 classification)."""


class ManualInterventionRequiredError(PublishJobConflict):
    """The remote repository state cannot be reconciled deterministically
    without human review -- a remote branch exists that does not match the
    job's recorded commit, or a recreated worktree failed to re-apply the
    patch. probe-agent never overwrites or force-pushes to resolve this
    (Issue #226)."""


@dataclass
class _CleanupOutcome:
    state: str
    error: Optional[str]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.time()


def _get_job_row(job_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()


def _update_job(job_id: int, **fields: Any) -> None:
    """Unconditional field update (used for recording facts -- branch name,
    SHAs, PR url, cleanup outcome -- which must be persisted regardless of
    whether the job has since reached a terminal state). Never used for a
    ``status`` change -- use ``_set_status`` (or one of the explicit
    compare-and-set transitions) so every status change is audited."""
    if not fields:
        return
    fields.setdefault("updated_at", _now())
    sets = ", ".join(f"{k} = ?" for k in fields)
    args = list(fields.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE publish_jobs SET {sets} WHERE id = ?", args)


def _set_status(
    job_id: int, status: str, *, audit_reason: Optional[str] = None, **extra: Any
) -> bool:
    """Transition ``status`` unless the job already reached a terminal
    state concurrently (e.g. a cancel raced with this phase thread) -- the
    terminal state always wins. Returns False if the transition did not
    apply; the caller must stop making further progress when that happens.

    Records an append-only ``publish_job_status_transition`` audit row
    (Issue #226) in the same transaction whenever the update actually
    applies and the status genuinely changed. ``audit_reason`` is an
    optional structural reason string added to the audit detail (e.g.
    ``"lease_held"``, ``"stuck_detected"``, ``"startup_recovery"``) -- it is
    never persisted as a DB column, only as audit detail.
    """
    fields = {"status": status, "heartbeat_at": _now(), "updated_at": _now(), **extra}
    sets = ", ".join(f"{k} = ?" for k in fields)
    placeholders = ", ".join("?" for _ in _TERMINAL_STATUSES)
    args = list(fields.values()) + [job_id, *_TERMINAL_STATUSES]
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, system_id FROM publish_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return False
        old_status = row["status"]
        cur = conn.execute(
            f"UPDATE publish_jobs SET {sets} WHERE id = ? AND status NOT IN ({placeholders})",
            args,
        )
        applied = cur.rowcount > 0
        if applied and old_status != status:
            detail: Dict[str, Any] = {"from": old_status, "to": status}
            if audit_reason:
                detail["reason"] = audit_reason
            record_publish_audit_event(
                conn,
                system_id=row["system_id"],
                job_id=job_id,
                event_type="publish_job_status_transition",
                detail=detail,
            )
        return applied


def _username_for(conn, user_id: Optional[int]) -> Optional[str]:
    if user_id is None:
        return None
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["username"] if row else None


def _build_validation_summary(patch_id: int) -> Dict[str, Any]:
    """Structural summary of the patch's validation_runs rows -- the latest
    row per variant. Read fresh at the `validating` step so a concurrent
    change since job creation is still caught (fail closed, no
    re-interpretation of the runs themselves)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM validation_runs WHERE patch_id = ? ORDER BY id DESC",
            (patch_id,),
        ).fetchall()
    summary: Dict[str, Any] = {}
    for row in rows:
        variant = row["variant"]
        if variant in summary:
            continue
        summary[variant] = {
            "run_id": row["id"],
            "overall_success": bool(row["overall_success"]),
            "total_duration_ms": row["total_duration_ms"],
            "created_at": row["created_at"],
        }
    return summary


def _validation_gate_passes(summary: Dict[str, Any]) -> bool:
    return (
        summary.get("baseline", {}).get("overall_success") is True
        and summary.get("probed", {}).get("overall_success") is True
    )


def _safe_cleanup_worktree(connection_id: int, job_id: int) -> _CleanupOutcome:
    try:
        repo_manager.cleanup_job_worktree(connection_id, job_id)
        return _CleanupOutcome(state="removed", error=None)
    except RepoManagerError as exc:
        return _CleanupOutcome(state="cleanup_failed", error=github_app._sanitize(str(exc)))


def _extract_and_validate_file_paths(diff: str, worktree_path: str):
    paths = publish_guards.extract_diff_paths(diff)
    if not paths:
        raise PublishJobConflict("Patch diff has no stageable files")
    allow_workflow = publish_guards.allow_workflow_changes()
    for path in paths:
        publish_guards.validate_patch_file_path(
            path, worktree_path, allow_workflow_changes=allow_workflow
        )
    return paths


def _remote_branch_sha_or_none(connection_row, token: str, branch: str) -> Optional[str]:
    try:
        return repo_manager.resolve_remote_branch_sha(connection_row, token, branch)
    except RepoManagerError as exc:
        if "not found on remote" in str(exc):
            return None
        raise


def _require_publish_installation_assignment(connection_id: int, system_id: int) -> None:
    """Re-check the DB authorization immediately before issuing a token.

    Also re-checks the connection itself is still ``connected`` (Issue #227):
    this function runs immediately before every installation-token issuance
    in every phase, so it is the enforcement point for "re-validate right
    before push/token"."""
    with get_conn() as conn:
        connection = conn.execute(
            "SELECT installation_id, status FROM github_connections WHERE id = ? AND system_id = ?",
            (connection_id, system_id),
        ).fetchone()
        if connection is None:
            raise PublishJobNotFound("GitHub connection not found")
        if connection["status"] != "connected":
            raise ConnectionRevokedError(
                f"GitHub connection is no longer connected (status={connection['status']})"
            )
        try:
            require_active_installation_assignment(
                conn, connection["installation_id"], system_id
            )
        except GitHubInstallationAccessError as exc:
            raise PublishJobConflict(str(exc)) from None


def _require_connection_still_connected(connection_id: int) -> None:
    """Fail closed if the connection was disconnected since this phase last
    checked (Issue #227). Used at phase entry and immediately before the
    push, in addition to `_require_publish_installation_assignment`'s check
    right before every token issuance."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM github_connections WHERE id = ?", (connection_id,)
        ).fetchone()
    status = row["status"] if row is not None else "missing"
    if status != "connected":
        raise ConnectionRevokedError(
            f"GitHub connection is no longer connected (status={status}); "
            "publish was cancelled by a disconnect"
        )


# ---------------------------------------------------------------------------
# Connection lease (Issue #226): a cross-process guard for the reconcile
# phase, on top of `repo_manager.connection_lock`'s in-process RLock (which
# does not protect against two separate server processes/replicas both
# retrying the same connection at once).
# ---------------------------------------------------------------------------


def _lease_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def _lease_ttl_seconds() -> float:
    try:
        return float(os.getenv("PUBLISH_LEASE_TTL_SECONDS", "120"))
    except (TypeError, ValueError):
        return 120.0


def _acquire_connection_lease(conn, connection_id: int, owner: str, ttl: float) -> bool:
    """Single transaction: clear an expired lease row, then attempt to take
    it. Returns False if a live lease for a *different* owner exists;
    re-acquiring/renewing as the same owner always succeeds."""
    now = _now()
    conn.execute(
        "DELETE FROM publish_connection_leases WHERE connection_id = ? AND expires_at < ?",
        (connection_id, now),
    )
    existing = conn.execute(
        "SELECT owner FROM publish_connection_leases WHERE connection_id = ?", (connection_id,)
    ).fetchone()
    if existing is not None and existing["owner"] != owner:
        return False
    conn.execute(
        """
        INSERT INTO publish_connection_leases (connection_id, owner, acquired_at, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(connection_id) DO UPDATE SET
            owner = excluded.owner, acquired_at = excluded.acquired_at, expires_at = excluded.expires_at
        """,
        (connection_id, owner, now, now + ttl),
    )
    return True


def _release_connection_lease(connection_id: int, owner: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM publish_connection_leases WHERE connection_id = ? AND owner = ?",
            (connection_id, owner),
        )


# ---------------------------------------------------------------------------
# Job creation (validation gate mirrors
# routes/project_intelligence.py::apply_probe_patch_endpoint)
# ---------------------------------------------------------------------------


def create_publish_job(
    system_id: int,
    connection_id: int,
    patch_id: int,
    requested_by_user_id: Optional[int],
    *,
    spawn: bool = True,
) -> int:
    publish_guards.assert_no_unsafe_push_config()

    now = _now()
    with get_conn() as conn:
        connection_row = conn.execute(
            "SELECT * FROM github_connections WHERE id = ? AND system_id = ?",
            (connection_id, system_id),
        ).fetchone()
        if connection_row is None:
            raise PublishJobNotFound("GitHub connection not found")
        try:
            require_active_installation_assignment(
                conn, connection_row["installation_id"], system_id
            )
        except GitHubInstallationAccessError as exc:
            raise PublishJobConflict(str(exc)) from None
        if connection_row["status"] != "connected":
            raise PublishJobConflict(
                "GitHub connection must be verified (status=connected) before publishing"
            )
        if not connection_row["default_branch"]:
            raise PublishJobConflict(
                "Connection has no default_branch; verify the connection first"
            )

        patch_row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ? AND system_id = ?",
            (patch_id, system_id),
        ).fetchone()
        if patch_row is None:
            raise PublishJobNotFound("Patch not found")
        if patch_row["status"] == "failed" or not patch_row["diff"].strip():
            raise PublishJobConflict("Patch is not applicable")

        validation_rows = conn.execute(
            "SELECT variant, overall_success FROM validation_runs "
            "WHERE patch_id = ? ORDER BY id DESC",
            (patch_id,),
        ).fetchall()
        latest_validation: Dict[str, bool] = {}
        for row in validation_rows:
            latest_validation.setdefault(row["variant"], bool(row["overall_success"]))
        if not (
            latest_validation.get("baseline") is True
            and latest_validation.get("probed") is True
        ):
            raise PublishJobConflict(
                "A successful baseline and probed validation is required before publishing"
            )

        cur = conn.execute(
            """
            INSERT INTO publish_jobs
                (system_id, connection_id, patch_id, snapshot_id, base_branch,
                 status, requested_by_user_id, created_at, updated_at, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                system_id,
                connection_id,
                patch_id,
                patch_row["snapshot_id"],
                connection_row["default_branch"],
                requested_by_user_id,
                now,
                now,
                now,
            ),
        )
        job_id = cur.lastrowid
        record_publish_audit_event(
            conn,
            system_id=system_id,
            connection_id=connection_id,
            job_id=job_id,
            event_type="publish_job_requested",
            actor_user_id=requested_by_user_id,
        )

    if spawn:
        _spawn_prepare(job_id)
    return job_id


def _spawn_prepare(job_id: int) -> None:
    thread = threading.Thread(target=_run_prepare_job_safely, args=(job_id,), daemon=True)
    thread.start()


def _run_prepare_job_safely(job_id: int) -> None:
    try:
        _run_prepare_phase(job_id)
    except Exception:  # pragma: no cover - defensive
        logger.exception("publish job %s prepare phase crashed", job_id)
        job = _get_job_row(job_id)
        if job is not None:
            cleanup = _safe_cleanup_worktree(job["connection_id"], job_id)
            _set_status(
                job_id,
                "failed",
                error="Internal error during prepare phase",
                completed_at=_now(),
                cleanup_state=cleanup.state,
                cleanup_error=cleanup.error,
            )


# ---------------------------------------------------------------------------
# Prepare phase
# ---------------------------------------------------------------------------


def _run_prepare_phase(job_id: int) -> None:
    job = _get_job_row(job_id)
    if job is None:
        return
    connection_id = job["connection_id"]

    with get_conn() as conn:
        connection_row = conn.execute(
            "SELECT * FROM github_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        patch_row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ?", (job["patch_id"],)
        ).fetchone()

    error: Optional[str] = None
    with repo_manager.connection_lock(connection_id):
        try:
            # Phase re-entry check (Issue #227): the connection may have been
            # disconnected between job creation and this thread actually
            # starting/resuming, even before the first status transition.
            _require_connection_still_connected(connection_id)
            if not _set_status(job_id, "authenticating"):
                return
            _require_publish_installation_assignment(connection_id, job["system_id"])
            token = github_app.create_installation_token(
                connection_row["installation_id"], api_base_url=connection_row["api_base_url"]
            ).token

            if not _set_status(job_id, "fetching"):
                return
            repo_manager.ensure_mirror(connection_row, token)
            remote_sha = repo_manager.resolve_remote_branch_sha(
                connection_row, token, job["base_branch"]
            )
            if remote_sha != patch_row["commit_sha"]:
                raise PublishJobConflict(
                    "Base branch has moved since the patch was generated/validated "
                    f"(patch is pinned to {patch_row['commit_sha']}, remote {job['base_branch']} "
                    f"is now at {remote_sha}); create a new snapshot, regenerate the probe "
                    "patch, and re-validate before publishing. If this happens immediately "
                    "after creating the connection, double-check that this GitHub connection "
                    "(owner/repo) actually points at the same repository this analysis is "
                    "based on -- a mismatched connection always looks like a moved base branch."
                )
            base_commit_sha = remote_sha
            _update_job(job_id, base_commit_sha=base_commit_sha)

            if not _set_status(job_id, "checking_out"):
                return
            worktree_path = repo_manager.create_job_worktree(connection_id, job_id, base_commit_sha)

            if not _set_status(job_id, "applying_patch"):
                return
            apply_error = apply_unified_diff(worktree_path, patch_row["diff"])
            if apply_error:
                raise PublishJobConflict(f"Failed to apply patch to the job worktree: {apply_error}")
            # Fail fast on unsafe file paths before we ever get to staging.
            _extract_and_validate_file_paths(patch_row["diff"], worktree_path)

            if not _set_status(job_id, "validating"):
                return
            validation_summary = _build_validation_summary(job["patch_id"])
            if not _validation_gate_passes(validation_summary):
                raise PublishJobConflict(
                    "Validation is no longer green (baseline+probed) for this patch"
                )
            _update_job(job_id, validation_summary=json.dumps(validation_summary))

            if not _set_status(job_id, "awaiting_approval"):
                return
            return
        except PublishJobConflict as exc:
            error = str(exc)
        except (GitHubAppError, RepoManagerError, publish_guards.PublishGuardError) as exc:
            error = github_app._sanitize(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("publish job %s prepare phase failed", job_id)
            error = github_app._sanitize(str(exc))

        # Prepare-phase failures always stay terminal `failed` (Issue #226):
        # nothing was ever pushed, so there is nothing to reconcile.
        cleanup = _safe_cleanup_worktree(connection_id, job_id)
        _set_status(
            job_id,
            "failed",
            error=error,
            completed_at=_now(),
            cleanup_state=cleanup.state,
            cleanup_error=cleanup.error,
        )


# ---------------------------------------------------------------------------
# Approval / publish phase
# ---------------------------------------------------------------------------


def approve_publish_job(
    job_id: int,
    system_id: int,
    approved_by_user_id: Optional[int],
    *,
    spawn: bool = True,
) -> None:
    """Move ``awaiting_approval -> committing``.

    The compare-and-set below is the authority (Issue #227): it only applies
    when the job is still ``awaiting_approval`` *and* its connection is
    still ``status='connected'`` at the same instant, so a disconnect that
    races with an approval call can never let the approval through. The
    explicit checks above the CAS exist only to give a more specific 409
    message in the common (non-racy) case; they must not be relied on for
    correctness on their own.
    """
    now = _now()
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM publish_jobs WHERE id = ? AND system_id = ?", (job_id, system_id)
        ).fetchone()
        if job is None:
            raise PublishJobNotFound("Publish job not found")
        if job["status"] != "awaiting_approval":
            raise PublishJobConflict(
                f"Publish job is not awaiting approval (status={job['status']})"
            )
        connection = conn.execute(
            "SELECT status FROM github_connections WHERE id = ?", (job["connection_id"],)
        ).fetchone()
        if connection is None or connection["status"] != "connected":
            status = connection["status"] if connection is not None else "missing"
            raise PublishJobConflict(
                f"GitHub connection is not connected (status={status}); cannot approve"
            )

        cur = conn.execute(
            """
            UPDATE publish_jobs
            SET status = 'committing', approved_by_user_id = ?, approved_at = ?,
                updated_at = ?, heartbeat_at = ?
            WHERE id = ? AND status = 'awaiting_approval'
              AND (SELECT status FROM github_connections
                   WHERE id = publish_jobs.connection_id) = 'connected'
            """,
            (approved_by_user_id, now, now, now, job_id),
        )
        if cur.rowcount == 0:
            job = conn.execute(
                "SELECT * FROM publish_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection = conn.execute(
                "SELECT status FROM github_connections WHERE id = ?", (job["connection_id"],)
            ).fetchone()
            if connection is None or connection["status"] != "connected":
                status = connection["status"] if connection is not None else "missing"
                raise PublishJobConflict(
                    f"GitHub connection is not connected (status={status}); cannot approve"
                )
            raise PublishJobConflict(
                f"Publish job approval state changed concurrently (status={job['status']})"
            )

        record_publish_audit_event(
            conn,
            system_id=system_id,
            connection_id=job["connection_id"],
            job_id=job_id,
            event_type="publish_job_status_transition",
            detail={"from": "awaiting_approval", "to": "committing"},
        )
        record_publish_audit_event(
            conn,
            system_id=system_id,
            connection_id=job["connection_id"],
            job_id=job_id,
            event_type="publish_job_approved",
            actor_user_id=approved_by_user_id,
        )

    if spawn:
        _spawn_publish(job_id)


def _spawn_publish(job_id: int) -> None:
    thread = threading.Thread(target=_run_publish_job_safely, args=(job_id,), daemon=True)
    thread.start()


def _run_publish_job_safely(job_id: int) -> None:
    try:
        _run_publish_phase(job_id)
    except Exception:  # pragma: no cover - defensive
        logger.exception("publish job %s publish phase crashed", job_id)
        job = _get_job_row(job_id)
        if job is not None:
            cleanup = _safe_cleanup_worktree(job["connection_id"], job_id)
            _set_status(
                job_id,
                "retryable_failed",
                error="Internal error during publish phase",
                cleanup_state=cleanup.state,
                cleanup_error=cleanup.error,
            )


def _publish_steps(job_id: int) -> None:
    """Commit -> push -> creating_pr -> completed, assuming the caller has
    already transitioned the job to ``committing`` (either straight from
    ``awaiting_approval`` via ``approve_publish_job``, or from
    ``reconciling`` during a retry that needed to recreate the worktree and
    redo the commit -- Issue #226). Raises on failure; the caller's except
    block classifies and persists the failure."""
    job = _get_job_row(job_id)
    if job is None:
        return
    connection_id = job["connection_id"]
    system_id = job["system_id"]

    with get_conn() as conn:
        connection_row = conn.execute(
            "SELECT * FROM github_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        patch_row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ?", (job["patch_id"],)
        ).fetchone()
        plan_row = conn.execute(
            "SELECT * FROM probe_plans WHERE id = ?", (patch_row["plan_id"],)
        ).fetchone()
        requested_by = _username_for(conn, job["requested_by_user_id"])
        approved_by = _username_for(conn, job["approved_by_user_id"])

    worktree_path = repo_manager.job_path(job_id)
    validation_summary = json.loads(job["validation_summary"]) if job["validation_summary"] else {}

    publish_guards.assert_no_unsafe_push_config()
    branch_name = job["branch_name"] or publish_guards.generate_branch_name(
        job_id, job["base_commit_sha"]
    )
    publish_guards.validate_push_target(
        branch_name, job["base_branch"], connection_row["default_branch"]
    )
    if not job["branch_name"]:
        _update_job(job_id, branch_name=branch_name)

    file_paths = _extract_and_validate_file_paths(patch_row["diff"], worktree_path)

    commit_sha = job["commit_sha"]
    current_head = _current_head(worktree_path)
    if commit_sha and commit_sha == current_head:
        pass  # already committed by a previous (interrupted) run
    else:
        add_result = repo_manager._run_git(
            worktree_path, ["add", "--"] + file_paths, timeout=30
        )
        if add_result.returncode != 0:
            raise PublishJobConflict(f"git add failed: {repo_manager._stderr(add_result)}")

        staged = repo_manager._run_git(
            worktree_path, ["diff", "--cached", "--name-only"], timeout=30
        )
        staged_paths = set(
            staged.stdout.decode("utf-8", errors="replace").split()
        )
        if staged_paths != set(file_paths):
            raise PublishJobConflict(
                "Staged files do not match the patch diff; refusing to commit"
            )

        commit_message = publish_guards.build_commit_message(
            objective=plan_row["objective"] if plan_row else "",
            patch_id=job["patch_id"],
            base_commit_sha=job["base_commit_sha"],
            system_id=system_id,
        )
        commit_result = repo_manager._run_git(
            worktree_path,
            [
                "-c",
                f"user.name={publish_guards.bot_name()}",
                "-c",
                f"user.email={publish_guards.bot_email()}",
                "commit",
                "-m",
                commit_message,
            ],
            timeout=30,
        )
        if commit_result.returncode != 0:
            raise PublishJobConflict(
                f"git commit failed: {repo_manager._stderr(commit_result)}"
            )
        commit_sha = _current_head(worktree_path)
    _update_job(job_id, commit_sha=commit_sha)

    if not _set_status(job_id, "pushing"):
        return
    _require_publish_installation_assignment(connection_id, system_id)
    token = github_app.create_installation_token(
        connection_row["installation_id"], api_base_url=connection_row["api_base_url"]
    ).token

    current_base_sha = repo_manager.resolve_remote_branch_sha(
        connection_row, token, job["base_branch"]
    )
    if current_base_sha != job["base_commit_sha"]:
        raise StaleBaseBranchError(
            "Base branch moved since preparation (stale patch); expected "
            f"{job['base_commit_sha']}, remote {job['base_branch']} is now at "
            f"{current_base_sha}. Create a new snapshot, regenerate the probe "
            "patch, and re-validate before publishing."
        )

    pushed_sha = _remote_branch_sha_or_none(connection_row, token, branch_name)
    if pushed_sha != commit_sha:
        # Last re-validation point (Issue #227), immediately before
        # the actual push: a disconnect could have landed after the
        # token-issuance check above but before this line.
        _require_connection_still_connected(connection_id)
        push_result = repo_manager.push(
            connection_row,
            worktree_path,
            token,
            f"HEAD:refs/heads/{branch_name}",
        )
        if push_result.returncode != 0:
            raise PublishJobConflict(
                f"git push failed: {repo_manager._stderr(push_result, token)}"
            )

    if not _set_status(job_id, "creating_pr"):
        return
    existing_prs = github_app.list_open_pull_requests_for_branch(
        connection_row["owner"],
        connection_row["repo"],
        token,
        head_branch=branch_name,
        api_base_url=connection_row["api_base_url"],
    )
    if existing_prs:
        pr = existing_prs[0]
    else:
        pr = github_app.create_pull_request(
            connection_row["owner"],
            connection_row["repo"],
            token,
            title=publish_guards.build_pr_title(
                plan_row["objective"] if plan_row else "", job["patch_id"]
            ),
            head=branch_name,
            base=job["base_branch"],
            body=publish_guards.build_pr_body(
                patch_id=job["patch_id"],
                base_commit_sha=job["base_commit_sha"],
                validation_summary=validation_summary,
                requested_by=requested_by,
                approved_by=approved_by,
            ),
            api_base_url=connection_row["api_base_url"],
        )
    _update_job(job_id, pr_url=pr.get("html_url"), pr_number=pr.get("number"))

    if not _set_status(job_id, "completed", completed_at=_now()):
        return
    cleanup = _safe_cleanup_worktree(connection_id, job_id)
    _update_job(job_id, cleanup_state=cleanup.state, cleanup_error=cleanup.error)


def _run_publish_phase(job_id: int) -> None:
    job = _get_job_row(job_id)
    if job is None:
        return
    connection_id = job["connection_id"]

    error: Optional[str] = None
    terminal_status = "retryable_failed"
    with repo_manager.connection_lock(connection_id):
        try:
            # Phase re-entry check (Issue #227): approve() already moved the
            # job to 'committing', so this is not the first status
            # transition of the phase -- but it is the first opportunity
            # this thread has to see a disconnect that happened between
            # approval and the thread actually starting/resuming.
            _require_connection_still_connected(connection_id)
            if not _set_status(job_id, "committing"):
                return
            _publish_steps(job_id)
            return
        except StaleBaseBranchError as exc:
            error = str(exc)
            terminal_status = "failed"
        except ConnectionRevokedError as exc:
            error = str(exc)
            terminal_status = "failed"
        except PublishJobConflict as exc:
            error = str(exc)
        except (GitHubAppError, RepoManagerError, publish_guards.PublishGuardError) as exc:
            error = github_app._sanitize(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("publish job %s publish phase failed", job_id)
            error = github_app._sanitize(str(exc))

        cleanup = _safe_cleanup_worktree(connection_id, job_id)
        _set_status(
            job_id,
            terminal_status,
            error=error,
            completed_at=_now() if terminal_status == "failed" else None,
            cleanup_state=cleanup.state,
            cleanup_error=cleanup.error,
        )


def _current_head(worktree_path: str) -> str:
    result = repo_manager._run_git(worktree_path, ["rev-parse", "HEAD"], timeout=30)
    if result.returncode != 0:
        raise PublishJobConflict(f"Failed to resolve worktree HEAD: {repo_manager._stderr(result)}")
    return result.stdout.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# Retry / reconcile phase (Issue #226)
# ---------------------------------------------------------------------------


def retry_publish_job(
    job_id: int, system_id: int, actor_user_id: Optional[int], *, spawn: bool = True
) -> None:
    """Compare-and-set ``retryable_failed`` / ``manual_intervention_required``
    -> ``reconciling``. Manual retry (this function) is allowed regardless
    of ``retry_count`` -- the ``PUBLISH_AUTO_RETRY_MAX`` cap only applies to
    the periodic worker's automatic retries
    (``app/publish_recovery.py::auto_retry_eligible_jobs``)."""
    now = _now()
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM publish_jobs WHERE id = ? AND system_id = ?", (job_id, system_id)
        ).fetchone()
        if job is None:
            raise PublishJobNotFound("Publish job not found")
        if job["status"] not in _RETRYABLE_STATUSES:
            raise PublishJobConflict(
                f"Publish job cannot be retried (status={job['status']})"
            )
        connection = conn.execute(
            "SELECT status FROM github_connections WHERE id = ?", (job["connection_id"],)
        ).fetchone()
        if connection is None or connection["status"] != "connected":
            status = connection["status"] if connection is not None else "missing"
            raise PublishJobConflict(
                f"GitHub connection is not connected (status={status}); cannot retry"
            )

        placeholders = ", ".join("?" for _ in _RETRYABLE_STATUSES)
        cur = conn.execute(
            f"""
            UPDATE publish_jobs
            SET status = 'reconciling', retry_count = retry_count + 1, last_attempt_at = ?,
                updated_at = ?, heartbeat_at = ?
            WHERE id = ? AND status IN ({placeholders})
              AND (SELECT status FROM github_connections
                   WHERE id = publish_jobs.connection_id) = 'connected'
            """,
            (now, now, now, job_id, *_RETRYABLE_STATUSES),
        )
        if cur.rowcount == 0:
            current = conn.execute(
                "SELECT * FROM publish_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection = conn.execute(
                "SELECT status FROM github_connections WHERE id = ?", (current["connection_id"],)
            ).fetchone()
            if connection is None or connection["status"] != "connected":
                status = connection["status"] if connection is not None else "missing"
                raise PublishJobConflict(
                    f"GitHub connection is not connected (status={status}); cannot retry"
                )
            raise PublishJobConflict(
                f"Publish job retry state changed concurrently (status={current['status']})"
            )

        record_publish_audit_event(
            conn,
            system_id=system_id,
            connection_id=job["connection_id"],
            job_id=job_id,
            event_type="publish_job_status_transition",
            detail={"from": job["status"], "to": "reconciling"},
        )
        record_publish_audit_event(
            conn,
            system_id=system_id,
            connection_id=job["connection_id"],
            job_id=job_id,
            event_type="publish_job_retry_requested",
            actor_user_id=actor_user_id,
        )

    if spawn:
        _spawn_reconcile(job_id)


def _spawn_reconcile(job_id: int) -> None:
    thread = threading.Thread(target=_run_reconcile_job_safely, args=(job_id,), daemon=True)
    thread.start()


def _run_reconcile_job_safely(job_id: int) -> None:
    try:
        _run_reconcile_phase(job_id)
    except Exception:  # pragma: no cover - defensive
        logger.exception("publish job %s reconcile phase crashed", job_id)
        job = _get_job_row(job_id)
        if job is not None:
            cleanup = _safe_cleanup_worktree(job["connection_id"], job_id)
            _set_status(
                job_id,
                "retryable_failed",
                error="Internal error during reconcile phase",
                cleanup_state=cleanup.state,
                cleanup_error=cleanup.error,
            )


def _run_reconcile_phase(job_id: int) -> None:
    job = _get_job_row(job_id)
    if job is None:
        return
    connection_id = job["connection_id"]
    system_id = job["system_id"]
    owner = _lease_owner()
    ttl = _lease_ttl_seconds()

    with repo_manager.connection_lock(connection_id):
        with get_conn() as conn:
            lease_acquired = _acquire_connection_lease(conn, connection_id, owner, ttl)
        if not lease_acquired:
            # Another process/replica already holds the lease for this
            # connection -- defer without doing any git/API work at all.
            _set_status(job_id, "retryable_failed", audit_reason="lease_held")
            return

        error: Optional[str] = None
        terminal_status = "retryable_failed"
        try:
            _require_connection_still_connected(connection_id)
            _require_publish_installation_assignment(connection_id, system_id)

            with get_conn() as conn:
                connection_row = conn.execute(
                    "SELECT * FROM github_connections WHERE id = ?", (connection_id,)
                ).fetchone()
                patch_row = conn.execute(
                    "SELECT * FROM probe_patches WHERE id = ?", (job["patch_id"],)
                ).fetchone()

            token = github_app.create_installation_token(
                connection_row["installation_id"], api_base_url=connection_row["api_base_url"]
            ).token
            remote_base_sha = repo_manager.resolve_remote_branch_sha(
                connection_row, token, job["base_branch"]
            )
            if remote_base_sha != job["base_commit_sha"]:
                raise StaleBaseBranchError(
                    "Base branch has moved since the patch was pinned "
                    f"(patch is pinned to {job['base_commit_sha']}, remote {job['base_branch']} "
                    f"is now at {remote_base_sha}); create a new snapshot, regenerate the "
                    "probe patch, and re-validate before publishing. If this happens "
                    "immediately after creating the connection, double-check that this "
                    "GitHub connection (owner/repo) actually points at the same repository "
                    "this analysis is based on -- a mismatched connection always looks like "
                    "a moved base branch."
                )

            branch_name = job["branch_name"] or publish_guards.generate_branch_name(
                job_id, job["base_commit_sha"]
            )
            if not job["branch_name"]:
                _update_job(job_id, branch_name=branch_name)

            remote_branch_sha = _remote_branch_sha_or_none(connection_row, token, branch_name)

            if remote_branch_sha is not None:
                if job["commit_sha"] and remote_branch_sha == job["commit_sha"]:
                    # The push already succeeded on a previous (interrupted)
                    # attempt -- never re-commit or re-push, only recover or
                    # create the Pull Request.
                    if not _set_status(job_id, "creating_pr"):
                        return
                    existing_prs = github_app.list_open_pull_requests_for_branch(
                        connection_row["owner"],
                        connection_row["repo"],
                        token,
                        head_branch=branch_name,
                        api_base_url=connection_row["api_base_url"],
                    )
                    if existing_prs:
                        pr = existing_prs[0]
                    else:
                        validation_summary = (
                            json.loads(job["validation_summary"])
                            if job["validation_summary"]
                            else {}
                        )
                        with get_conn() as conn:
                            plan_row = conn.execute(
                                "SELECT * FROM probe_plans WHERE id = ?",
                                (patch_row["plan_id"],),
                            ).fetchone()
                            requested_by = _username_for(conn, job["requested_by_user_id"])
                            approved_by = _username_for(conn, job["approved_by_user_id"])
                        pr = github_app.create_pull_request(
                            connection_row["owner"],
                            connection_row["repo"],
                            token,
                            title=publish_guards.build_pr_title(
                                plan_row["objective"] if plan_row else "", job["patch_id"]
                            ),
                            head=branch_name,
                            base=job["base_branch"],
                            body=publish_guards.build_pr_body(
                                patch_id=job["patch_id"],
                                base_commit_sha=job["base_commit_sha"],
                                validation_summary=validation_summary,
                                requested_by=requested_by,
                                approved_by=approved_by,
                            ),
                            api_base_url=connection_row["api_base_url"],
                        )
                    _update_job(job_id, pr_url=pr.get("html_url"), pr_number=pr.get("number"))
                    if not _set_status(job_id, "completed", completed_at=_now()):
                        return
                    cleanup = _safe_cleanup_worktree(connection_id, job_id)
                    _update_job(job_id, cleanup_state=cleanup.state, cleanup_error=cleanup.error)
                    return
                else:
                    raise ManualInterventionRequiredError(
                        f"Remote branch {branch_name} already exists but does not match the "
                        f"recorded commit ({job['commit_sha'] or 'none recorded'}); refusing "
                        "to overwrite or force-push it. Investigate the remote branch manually."
                    )

            # Branch missing on the remote: recreate the worktree if a
            # previous failure already cleaned it up, then run the normal
            # commit -> push -> creating_pr -> completed sequence.
            worktree_path = repo_manager.job_path(job_id)
            if not os.path.isdir(worktree_path):
                worktree_path = repo_manager.create_job_worktree(
                    connection_id, job_id, job["base_commit_sha"]
                )
                apply_error = apply_unified_diff(worktree_path, patch_row["diff"])
                if apply_error:
                    raise ManualInterventionRequiredError(
                        f"Failed to re-apply the patch to a recreated job worktree: {apply_error}"
                    )
                _extract_and_validate_file_paths(patch_row["diff"], worktree_path)
                if job["commit_sha"]:
                    # The old commit object no longer exists in a fresh
                    # worktree; `_publish_steps` will make a new commit.
                    _update_job(job_id, commit_sha=None)

            if not _set_status(job_id, "committing"):
                return
            _publish_steps(job_id)
            return
        except StaleBaseBranchError as exc:
            error = str(exc)
            terminal_status = "failed"
        except ConnectionRevokedError as exc:
            error = str(exc)
            terminal_status = "failed"
        except ManualInterventionRequiredError as exc:
            error = str(exc)
            terminal_status = "manual_intervention_required"
        except PublishJobConflict as exc:
            error = str(exc)
        except (GitHubAppError, RepoManagerError, publish_guards.PublishGuardError) as exc:
            error = github_app._sanitize(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("publish job %s reconcile phase failed", job_id)
            error = github_app._sanitize(str(exc))
        finally:
            _release_connection_lease(connection_id, owner)

        cleanup = _safe_cleanup_worktree(connection_id, job_id)
        _set_status(
            job_id,
            terminal_status,
            error=error,
            completed_at=_now() if terminal_status == "failed" else None,
            cleanup_state=cleanup.state,
            cleanup_error=cleanup.error,
        )


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def cancel_publish_job(job_id: int, system_id: int) -> None:
    """Cancel a job that has not (yet, or any longer) reached an in-flight
    publish phase. Allowed from the prepare phase, ``awaiting_approval``,
    and the resting ``retryable_failed`` / ``manual_intervention_required``
    states (Issue #226: cancel means giving up on this job -- any remote
    branch or Pull Request already pushed/opened is left untouched, exactly
    like the disconnect-triggered auto-cancel path)."""
    now = _now()
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM publish_jobs WHERE id = ? AND system_id = ?", (job_id, system_id)
        ).fetchone()
        if job is None:
            raise PublishJobNotFound("Publish job not found")
        if job["status"] not in _CANCELLABLE_STATUSES:
            raise PublishJobConflict(
                f"Publish job cannot be cancelled (status={job['status']})"
            )
        placeholders = ", ".join("?" for _ in _CANCELLABLE_STATUSES)
        cur = conn.execute(
            f"""
            UPDATE publish_jobs
            SET status = 'cancelled', completed_at = ?, updated_at = ?
            WHERE id = ? AND status IN ({placeholders})
            """,
            (now, now, job_id, *_CANCELLABLE_STATUSES),
        )
        if cur.rowcount == 0:
            raise PublishJobConflict("Publish job status changed concurrently; cannot cancel")

        record_publish_audit_event(
            conn,
            system_id=system_id,
            connection_id=job["connection_id"],
            job_id=job_id,
            event_type="publish_job_status_transition",
            detail={"from": job["status"], "to": "cancelled"},
        )
        record_publish_audit_event(
            conn,
            system_id=system_id,
            connection_id=job["connection_id"],
            job_id=job_id,
            event_type="publish_job_cancelled",
            detail={"reason": "manual"},
        )

    cleanup = _safe_cleanup_worktree(job["connection_id"], job_id)
    _update_job(job_id, cleanup_state=cleanup.state, cleanup_error=cleanup.error)
