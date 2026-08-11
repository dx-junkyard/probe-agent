"""Issue #369: `ready` (analysis finished) is not `current` (matches HEAD).

The audited bug was one word covering both: the Repository page warned that a
snapshot was behind HEAD while labelling a different, older snapshot 「ready」,
and Experiments offered every stale snapshot as an ordinary 「snapshot ready」
option. These tests pin that the two axes are independent, that exactly one
snapshot is recommended, and that continuing on a stale snapshot needs an
explicit human reason.
"""

import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.snapshot_preflight import (
    PreflightFacts,
    classify_freshness,
    evaluate_preflight,
    resolve_stale_decision,
    StaleSnapshotNotAcknowledged,
)


# --- the two axes are independent --------------------------------------------


@pytest.mark.parametrize(
    "snapshot_sha, head_sha, head_error, expected",
    [
        ("abc", "abc", None, "current"),
        ("abc", "def", None, "stale"),
        # An unreadable HEAD is not evidence that the snapshot is behind.
        ("abc", None, "no repo", "unknown"),
        ("abc", "def", "boom", "unknown"),
        (None, "def", None, "unknown"),
    ],
)
def test_freshness_is_decided_only_by_commit_identity(
    snapshot_sha, head_sha, head_error, expected
):
    assert classify_freshness(
        snapshot_commit_sha=snapshot_sha, head_sha=head_sha, head_error=head_error
    ) == expected


def test_a_ready_snapshot_can_be_stale():
    """`ready` says the analysis finished. It says nothing about HEAD."""
    result = evaluate_preflight(
        PreflightFacts(
            snapshot_id=1,
            processing_state="ready",
            snapshot_commit_sha="old",
            head_sha="new",
            head_relation="behind",
            commits_behind=3,
            has_symbol_index=True,
            understanding_build_status="completed",
            recommended_snapshot_id=1,
        )
    )
    assert result.processing_state == "ready"
    assert result.freshness == "stale"
    assert result.verdict != "ready"
    assert result.requires_stale_acknowledgement is True


def test_a_failed_snapshot_on_head_is_current_but_blocked():
    """The converse: matching HEAD does not make an unfinished analysis usable."""
    result = evaluate_preflight(
        PreflightFacts(
            snapshot_id=1,
            processing_state="failed",
            snapshot_commit_sha="same",
            head_sha="same",
            head_relation="same",
        )
    )
    assert result.freshness == "current"
    assert result.verdict == "blocked"


def test_a_current_ready_snapshot_passes():
    result = evaluate_preflight(
        PreflightFacts(
            snapshot_id=7,
            processing_state="ready",
            snapshot_commit_sha="same",
            head_sha="same",
            head_relation="same",
            has_symbol_index=True,
            understanding_build_status="completed",
            recommended_snapshot_id=7,
        )
    )
    assert result.verdict == "ready"
    assert result.is_recommended is True
    assert result.requires_stale_acknowledgement is False


def test_unknown_freshness_never_blocks_and_never_demands_an_ack():
    """Not knowing is not the same as knowing it is stale."""
    result = evaluate_preflight(
        PreflightFacts(
            snapshot_id=1,
            processing_state="ready",
            snapshot_commit_sha="abc",
            head_sha=None,
            head_error="repository unreadable",
            has_symbol_index=True,
            understanding_build_status="completed",
            recommended_snapshot_id=1,
        )
    )
    assert result.freshness == "unknown"
    assert result.requires_stale_acknowledgement is False
    assert result.verdict != "blocked"


def test_missing_snapshot_blocks():
    result = evaluate_preflight(PreflightFacts())
    assert result.verdict == "blocked"
    assert any(c.status == "blocking" for c in result.checks)


def test_every_check_carries_a_remediation():
    """A blocked preflight must always say what to do next."""
    result = evaluate_preflight(PreflightFacts())
    assert result.checks
    for check in result.checks:
        assert check.summary and check.remediation


def test_a_non_recommended_snapshot_is_marked_as_such():
    result = evaluate_preflight(
        PreflightFacts(
            snapshot_id=1,
            processing_state="ready",
            snapshot_commit_sha="old",
            head_sha="new",
            recommended_snapshot_id=2,
            recommended_snapshot_commit_sha="new",
            recommended_snapshot_freshness="current",
        )
    )
    assert result.is_recommended is False
    assert result.recommended_snapshot_id == 2


# --- the stale continuation decision is a manual fact -------------------------


def test_continuing_on_a_stale_snapshot_requires_a_reason():
    result = evaluate_preflight(
        PreflightFacts(
            snapshot_id=1,
            processing_state="ready",
            snapshot_commit_sha="old",
            head_sha="new",
            has_symbol_index=True,
            understanding_build_status="completed",
        )
    )
    with pytest.raises(StaleSnapshotNotAcknowledged):
        resolve_stale_decision(result, None)
    with pytest.raises(StaleSnapshotNotAcknowledged):
        resolve_stale_decision(result, "   ")

    freshness, head_sha, reason = resolve_stale_decision(result, "過去バグの再現")
    assert freshness == "stale"
    assert head_sha == "new"
    assert reason == "過去バグの再現"


def test_a_current_snapshot_needs_no_reason():
    result = evaluate_preflight(
        PreflightFacts(
            snapshot_id=1,
            processing_state="ready",
            snapshot_commit_sha="same",
            head_sha="same",
            has_symbol_index=True,
            understanding_build_status="completed",
        )
    )
    freshness, _, reason = resolve_stale_decision(result, None)
    assert freshness == "current"
    assert reason is None


# --- endpoint over a real repository -----------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-preflight.db"))
    # `repository_head_relation` runs through the repository-root allowlist
    # (`git_ops._validate_repo_path`), so the freshness comparison needs the
    # same configuration production has. Note that `resolve_head` does NOT
    # enforce the allowlist -- a pre-existing asymmetry in `git_ops`, not
    # something this preflight introduces -- so without this the endpoint
    # reports a HEAD but an `unknown` relation.
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "target-repo"
    path.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (path / "app.py").write_text("def f():\n    return 1\n")
    git("add", ".")
    git("commit", "-m", "first")
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True
    ).stdout.strip()
    return path, first, git


def _insert_snapshot(db_path, repo_path, commit_sha, status="ready"):
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        now = time.time()
        conn.execute(
            "INSERT OR IGNORE INTO repository_configs (system_id, repo_path, updated_at)"
            " VALUES (1, ?, ?)",
            (str(repo_path), now),
        )
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, file_count,
                    total_size, indexed_size, created_at, completed_at)
               VALUES (1, ?, ?, ?, 1, 10, 10, ?, ?)""",
            (str(repo_path), commit_sha, status, now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_endpoint_reports_current_for_a_snapshot_pinned_to_head(client, repo, tmp_path):
    path, head, _ = repo
    _insert_snapshot(str(tmp_path / "probe-preflight.db"), path, head)

    body = client.get("/snapshot-preflight").json()
    assert body["processing_state"] == "ready"
    assert body["freshness"] == "current"
    assert body["commit_sha"] == head
    assert body["is_recommended"] is True


def test_endpoint_reports_stale_after_head_moves(client, repo, tmp_path):
    path, first, git = repo
    _insert_snapshot(str(tmp_path / "probe-preflight.db"), path, first)
    (path / "app.py").write_text("def f():\n    return 2\n")
    git("add", ".")
    git("commit", "-m", "second")

    body = client.get("/snapshot-preflight").json()
    # Still `ready` -- the analysis did finish. The freshness axis is what moved.
    assert body["processing_state"] == "ready"
    assert body["freshness"] == "stale"
    assert body["head_relation"] == "behind"
    assert body["commits_behind"] == 1
    assert body["requires_stale_acknowledgement"] is True
    assert body["stale_continuation_note"]


def test_exactly_one_snapshot_is_recommended(client, repo, tmp_path):
    """Multiple snapshots on the same commit must not both look preferred."""
    path, head, _ = repo
    db = str(tmp_path / "probe-preflight.db")
    older = _insert_snapshot(db, path, head)
    newer = _insert_snapshot(db, path, head)

    latest = client.get("/snapshot-preflight").json()
    assert latest["recommended_snapshot_id"] == newer
    assert latest["snapshot_id"] == newer

    explicit = client.get(f"/snapshot-preflight?snapshot_id={older}").json()
    # The older one is usable for reproduction, but is not the recommendation.
    assert explicit["snapshot_id"] == older
    assert explicit["is_recommended"] is False
    assert explicit["recommended_snapshot_id"] == newer


def test_partially_failed_snapshot_blocks_with_a_reason(client, repo, tmp_path):
    path, head, _ = repo
    db = str(tmp_path / "probe-preflight.db")
    failed = _insert_snapshot(db, path, head, status="failed")

    body = client.get(f"/snapshot-preflight?snapshot_id={failed}").json()
    assert body["verdict"] == "blocked"
    blocking = [c for c in body["checks"] if c["status"] == "blocking"]
    assert blocking and all(c["remediation"] for c in blocking)


def test_unknown_snapshot_id_is_a_404(client, repo, tmp_path):
    path, head, _ = repo
    _insert_snapshot(str(tmp_path / "probe-preflight.db"), path, head)
    assert client.get("/snapshot-preflight?snapshot_id=99999").status_code == 404


def test_preflight_leaves_the_target_repository_unchanged(client, repo, tmp_path):
    path, head, _ = repo
    _insert_snapshot(str(tmp_path / "probe-preflight.db"), path, head)

    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True
    ).stdout
    client.get("/snapshot-preflight")
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True
    ).stdout
    assert before == after
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True
        ).stdout.strip()
        == head
    )
