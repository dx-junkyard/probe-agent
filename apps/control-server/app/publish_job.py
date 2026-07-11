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
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import github_app, publish_guards, repo_manager
from .db import get_conn
from .github_app import GitHubAppError
from .patch_generator import apply_unified_diff
from .repo_manager import RepoManagerError

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class PublishJobError(RuntimeError):
    pass


class PublishJobNotFound(PublishJobError):
    pass


class PublishJobConflict(PublishJobError):
    pass


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
    whether the job has since reached a terminal state)."""
    if not fields:
        return
    fields.setdefault("updated_at", _now())
    sets = ", ".join(f"{k} = ?" for k in fields)
    args = list(fields.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE publish_jobs SET {sets} WHERE id = ?", args)


def _set_status(job_id: int, status: str, **extra: Any) -> bool:
    """Transition ``status`` unless the job already reached a terminal
    state concurrently (e.g. a cancel raced with this phase thread) -- the
    terminal state always wins. Returns False if the transition did not
    apply; the caller must stop making further progress when that happens."""
    fields = {"status": status, "heartbeat_at": _now(), "updated_at": _now(), **extra}
    sets = ", ".join(f"{k} = ?" for k in fields)
    placeholders = ", ".join("?" for _ in _TERMINAL_STATUSES)
    args = list(fields.values()) + [job_id, *_TERMINAL_STATUSES]
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE publish_jobs SET {sets} WHERE id = ? AND status NOT IN ({placeholders})",
            args,
        )
        return cur.rowcount > 0


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
            _update_job(
                job_id,
                status="failed",
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
            if not _set_status(job_id, "authenticating"):
                return
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
                    "patch, and re-validate before publishing"
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

        cleanup = _safe_cleanup_worktree(connection_id, job_id)
        _update_job(
            job_id,
            status="failed",
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
        cur = conn.execute(
            """
            UPDATE publish_jobs
            SET status = 'committing', approved_by_user_id = ?, approved_at = ?,
                updated_at = ?, heartbeat_at = ?
            WHERE id = ? AND status = 'awaiting_approval'
            """,
            (approved_by_user_id, now, now, now, job_id),
        )
        if cur.rowcount == 0:
            raise PublishJobConflict("Publish job approval state changed concurrently")

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
            _update_job(
                job_id,
                status="failed",
                error="Internal error during publish phase",
                completed_at=_now(),
                cleanup_state=cleanup.state,
                cleanup_error=cleanup.error,
            )


def _run_publish_phase(job_id: int) -> None:
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

    error: Optional[str] = None
    with repo_manager.connection_lock(connection_id):
        try:
            if not _set_status(job_id, "committing"):
                return

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
            token = github_app.create_installation_token(
                connection_row["installation_id"], api_base_url=connection_row["api_base_url"]
            ).token

            current_base_sha = repo_manager.resolve_remote_branch_sha(
                connection_row, token, job["base_branch"]
            )
            if current_base_sha != job["base_commit_sha"]:
                raise PublishJobConflict(
                    "Base branch moved since preparation (stale patch); expected "
                    f"{job['base_commit_sha']}, remote {job['base_branch']} is now at "
                    f"{current_base_sha}. Create a new snapshot, regenerate the probe "
                    "patch, and re-validate before publishing."
                )

            pushed_sha = _remote_branch_sha_or_none(connection_row, token, branch_name)
            if pushed_sha != commit_sha:
                with repo_manager._ephemeral_credentials(token) as env_extra:
                    push_result = repo_manager._run_git(
                        worktree_path,
                        ["push", "origin", f"HEAD:refs/heads/{branch_name}"],
                        timeout=repo_manager.fetch_timeout(),
                        env_extra=env_extra,
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
            return
        except PublishJobConflict as exc:
            error = str(exc)
        except (GitHubAppError, RepoManagerError, publish_guards.PublishGuardError) as exc:
            error = github_app._sanitize(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("publish job %s publish phase failed", job_id)
            error = github_app._sanitize(str(exc))

        cleanup = _safe_cleanup_worktree(connection_id, job_id)
        _update_job(
            job_id,
            status="failed",
            error=error,
            completed_at=_now(),
            cleanup_state=cleanup.state,
            cleanup_error=cleanup.error,
        )


def _current_head(worktree_path: str) -> str:
    result = repo_manager._run_git(worktree_path, ["rev-parse", "HEAD"], timeout=30)
    if result.returncode != 0:
        raise PublishJobConflict(f"Failed to resolve worktree HEAD: {repo_manager._stderr(result)}")
    return result.stdout.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def cancel_publish_job(job_id: int, system_id: int) -> None:
    now = _now()
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM publish_jobs WHERE id = ? AND system_id = ?", (job_id, system_id)
        ).fetchone()
        if job is None:
            raise PublishJobNotFound("Publish job not found")
        if job["status"] not in ("pending", "awaiting_approval"):
            raise PublishJobConflict(
                f"Publish job cannot be cancelled (status={job['status']})"
            )
        cur = conn.execute(
            """
            UPDATE publish_jobs
            SET status = 'cancelled', completed_at = ?, updated_at = ?
            WHERE id = ? AND status IN ('pending', 'awaiting_approval')
            """,
            (now, now, job_id),
        )
        if cur.rowcount == 0:
            raise PublishJobConflict("Publish job status changed concurrently; cannot cancel")

    cleanup = _safe_cleanup_worktree(job["connection_id"], job_id)
    _update_job(job_id, cleanup_state=cleanup.state, cleanup_error=cleanup.error)
