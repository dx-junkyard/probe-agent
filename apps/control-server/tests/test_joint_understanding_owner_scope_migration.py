"""Issue #461: migration/backward-compatibility verification for
`joint_understanding_session`'s `owner_scope` / `discussion_thread_id` /
dependency-manifest rebuild.

`init_db()` has already run by the time any fixture exists, so the widened
table is what a naive test would find. This file follows the same idiom
`tests/test_product_objective_migration.py` uses: downgrade the table back
to its pre-#461 shape after the real `init_db()` has run once, seed rows the
way a pre-#461 installation would have, then call `init_db()` again and
verify the migration:

* is STRUCTURALLY detected (no version flag to drift from the schema it
  describes);
* preserves every row's id, every column value, and every existing FK
  reference to it (`joint_understanding_finding` etc. cascade correctly
  after the rebuild -- the concrete risk `ALTER TABLE ... RENAME TO`
  otherwise creates for every table referencing the renamed one, verified
  here rather than only argued in a comment);
* is idempotent (a second/third `init_db()` changes nothing further);
* survives a process restart (a fresh `get_conn()` after `init_db()` sees
  the same rows).
"""

from __future__ import annotations

import os
import subprocess
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ju-owner-scope-migration.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems", json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _dump(conn, table):
    """Every row of `table`, ordered by id, as plain dicts."""
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]  # noqa: S608


# The pre-#461 shape, reproduced verbatim (the shape the table had at Issue
# #337, unchanged since).
_LEGACY_DDL = """
CREATE TABLE joint_understanding_session (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    origin_kind         TEXT NOT NULL,
    origin_id           INTEGER NOT NULL,
    trigger             TEXT NOT NULL,
    question_text       TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',
    outcome             TEXT,
    outcome_reason      TEXT,
    outcome_finding_ids TEXT NOT NULL DEFAULT '[]',
    outcome_premise_state TEXT,
    outcome_premise_reason TEXT,
    closed_by_actor_kind  TEXT,
    closed_by_user_id     INTEGER,
    closed_by_username    TEXT,
    premise_snapshot_id INTEGER,
    premise_commit_sha  TEXT,
    premise_revision_id INTEGER,
    premise_content_hash TEXT,
    premise_capability_digest TEXT,
    premise_intent_digest TEXT,
    premise_review_subject_id TEXT,
    premise_tracking_version TEXT,
    premise_captured_at REAL,
    schema_version      TEXT NOT NULL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    closed_at           REAL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);
CREATE INDEX idx_joint_understanding_session_session
    ON joint_understanding_session (session_id, status);
CREATE INDEX idx_joint_understanding_session_origin
    ON joint_understanding_session (system_id, origin_kind, origin_id);
"""

_LEGACY_COLUMNS = (
    "id", "session_id", "system_id", "origin_kind", "origin_id", "trigger",
    "question_text", "status", "outcome", "outcome_reason",
    "outcome_finding_ids", "outcome_premise_state", "outcome_premise_reason",
    "closed_by_actor_kind", "closed_by_user_id", "closed_by_username",
    "premise_snapshot_id", "premise_commit_sha", "premise_revision_id",
    "premise_content_hash", "premise_capability_digest", "premise_intent_digest",
    "premise_review_subject_id", "premise_tracking_version", "premise_captured_at",
    "schema_version", "created_at", "updated_at", "closed_at",
)


def _downgrade_joint_understanding_session(conn):
    """Put `joint_understanding_session` back into its pre-#461 shape.

    `init_db()` has already run by the time any fixture exists, so the
    rebuilt table is what a test would otherwise find. Preserves every row
    exactly (only pre-#461 columns exist to preserve).
    """
    cols = ", ".join(_LEGACY_COLUMNS)
    conn.executescript(
        f"""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE joint_understanding_session_tmp AS
            SELECT {cols} FROM joint_understanding_session;
        DROP TABLE joint_understanding_session;
        DROP INDEX IF EXISTS idx_joint_understanding_session_session;
        DROP INDEX IF EXISTS idx_joint_understanding_session_origin;
        DROP INDEX IF EXISTS idx_joint_understanding_session_discussion_thread;
        """
    )
    conn.executescript(_LEGACY_DDL)
    conn.execute(
        f"INSERT INTO joint_understanding_session ({cols}) "  # noqa: S608
        f"SELECT {cols} FROM joint_understanding_session_tmp"
    )
    conn.executescript(
        """
        DROP TABLE joint_understanding_session_tmp;
        PRAGMA foreign_keys = ON;
        """
    )


def test_structural_detection_is_idempotent_on_a_fresh_database(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "fresh.db"))
    from app.db import _columns, get_conn, init_db

    init_db()
    with get_conn() as conn:
        first_cols = sorted(_columns(conn, "joint_understanding_session"))
        first_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'joint_understanding_session'"
        ).fetchone()["sql"]
    init_db()
    init_db()
    with get_conn() as conn:
        second_cols = sorted(_columns(conn, "joint_understanding_session"))
        second_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'joint_understanding_session'"
        ).fetchone()["sql"]
    assert first_cols == second_cols
    assert first_sql == second_sql
    assert "owner_scope" in first_cols
    assert "discussion_thread_id" in first_cols
    assert "premise_dependency_manifest_json" in first_cols
    assert "premise_dependency_manifest_digest" in first_cols


def test_migration_preserves_rows_ids_and_referential_integrity(admin_client, tmp_path):
    """The core claim: an existing Interview JU session and its findings
    survive the rebuild byte-for-byte, and cross-table FK cascade still
    works afterward (the concrete risk of `ALTER TABLE ... RENAME TO`
    rewriting every OTHER table's FK clause to the dropped `_legacy` name)."""
    from app.db import get_conn, init_db

    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Migration System")
    headers = _headers(token, system_id)

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, "/tmp/x", "commitA", now, now),
        )
        snapshot_id = cur.lastrowid

    session_id = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "根拠は?"}, headers=headers,
    ).json()
    ju = admin_client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": "qa", "origin_id": qa["id"], "trigger": "explicit_request",
            "question_text": "根拠が分かりません",
        },
        headers=headers,
    ).json()["session"]
    ju_id = ju["id"]

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO joint_understanding_finding
                (joint_understanding_id, system_id, origin_role, claim_kind,
                 statement, decision_method, created_at)
               VALUES (?, ?, 'developer', 'fact', 'stmt', 'manual', ?)""",
            (ju_id, system_id, now),
        )
        conn.execute(
            """INSERT INTO joint_understanding_action
                (joint_understanding_id, system_id, action_kind, decision_method, created_at)
               VALUES (?, ?, 'hold', 'manual', ?)""",
            (ju_id, system_id, now),
        )
        conn.commit()
        _downgrade_joint_understanding_session(conn)
        conn.commit()
        before_session = _dump(conn, "joint_understanding_session")
        before_finding = _dump(conn, "joint_understanding_finding")
        before_action = _dump(conn, "joint_understanding_action")
        before_count = conn.execute(
            "SELECT COUNT(*) AS n FROM joint_understanding_session"
        ).fetchone()["n"]

    init_db()

    with get_conn() as conn:
        after_session = _dump(conn, "joint_understanding_session")
        after_count = conn.execute(
            "SELECT COUNT(*) AS n FROM joint_understanding_session"
        ).fetchone()["n"]
        after_finding = _dump(conn, "joint_understanding_finding")
        after_action = _dump(conn, "joint_understanding_action")

        assert before_count == after_count == 1
        assert [r["id"] for r in before_session] == [r["id"] for r in after_session]
        # Every pre-#461 column value is byte-for-byte unchanged.
        for col in _LEGACY_COLUMNS:
            assert before_session[0][col] == after_session[0][col], col
        # The new columns backfilled correctly.
        assert after_session[0]["owner_scope"] == "interview"
        assert after_session[0]["discussion_thread_id"] is None
        assert after_session[0]["premise_dependency_manifest_json"] == "[]"
        assert after_session[0]["premise_dependency_manifest_digest"] is None
        assert before_finding == after_finding
        assert before_action == after_action

        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # Cross-table FK cascade actually works post-rebuild: deleting the
        # rebuilt session row cascades to its finding/action rows via THEIR
        # OWN (unqualified-name) FK -- the exact case `ALTER TABLE RENAME`'s
        # default cross-table rewrite would silently break if the migration
        # did not disable it for the rename step.
        conn.execute("DELETE FROM joint_understanding_session WHERE id = ?", (ju_id,))
        remaining_findings = conn.execute(
            "SELECT COUNT(*) AS n FROM joint_understanding_finding WHERE joint_understanding_id = ?",
            (ju_id,),
        ).fetchone()["n"]
        remaining_actions = conn.execute(
            "SELECT COUNT(*) AS n FROM joint_understanding_action WHERE joint_understanding_id = ?",
            (ju_id,),
        ).fetchone()["n"]
        assert remaining_findings == 0
        assert remaining_actions == 0
        conn.rollback()  # do not actually keep this destructive probe


def test_migration_is_idempotent_across_repeated_init_db_calls(admin_client):
    from app.db import get_conn, init_db

    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Idempotent System")
    headers = _headers(token, system_id)
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, "/tmp/x", "commitB", now, now),
        )
        snapshot_id = cur.lastrowid
    session_id = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "なぜ?"}, headers=headers,
    ).json()
    admin_client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": "qa", "origin_id": qa["id"], "trigger": "explicit_request",
            "question_text": "分かりません",
        },
        headers=headers,
    )

    with get_conn() as conn:
        _downgrade_joint_understanding_session(conn)
        conn.commit()

    init_db()
    with get_conn() as conn:
        after_first = _dump(conn, "joint_understanding_session")
    init_db()
    init_db()
    with get_conn() as conn:
        after_third = _dump(conn, "joint_understanding_session")
    assert after_first == after_third


def test_migration_survives_a_process_restart(admin_client, tmp_path):
    """A fresh connection (simulating a server restart) sees the migrated
    shape without a further `init_db()` call in that "process"."""
    from app.db import get_conn, init_db

    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Restart System")
    headers = _headers(token, system_id)
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, "/tmp/x", "commitC", now, now),
        )
        snapshot_id = cur.lastrowid
    session_id = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "なぜ2?"}, headers=headers,
    ).json()
    ju_id = admin_client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": "qa", "origin_id": qa["id"], "trigger": "explicit_request",
            "question_text": "分かりません2",
        },
        headers=headers,
    ).json()["session"]["id"]

    with get_conn() as conn:
        _downgrade_joint_understanding_session(conn)
        conn.commit()

    init_db()

    # Simulate the process restarting: open a brand-new connection to the
    # SAME on-disk file (not the same Python process's cached anything).
    db_path = os.environ["PROBE_DB_PATH"]
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    try:
        row = raw.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        assert row is not None
        assert row["owner_scope"] == "interview"
        assert row["session_id"] == session_id
        assert row["discussion_thread_id"] is None
    finally:
        raw.close()

    # And a real `init_db()` restart-path call is still a no-op.
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        assert row is not None


def test_backup_restore_round_trip_preserves_the_migrated_shape(admin_client, tmp_path):
    """A file-level backup/restore (the operational analogue of Issue #461's
    'backup/restore を検証する') must reproduce the same migrated rows."""
    from app.db import get_conn, init_db
    import shutil

    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Backup System")
    headers = _headers(token, system_id)
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, "/tmp/x", "commitD", now, now),
        )
        snapshot_id = cur.lastrowid
    session_id = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "なぜ3?"}, headers=headers,
    ).json()
    admin_client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": "qa", "origin_id": qa["id"], "trigger": "explicit_request",
            "question_text": "分かりません3",
        },
        headers=headers,
    )
    with get_conn() as conn:
        _downgrade_joint_understanding_session(conn)
        conn.commit()
    init_db()
    with get_conn() as conn:
        before = _dump(conn, "joint_understanding_session")

    db_path = os.environ["PROBE_DB_PATH"]
    backup_path = str(tmp_path / "backup.db")
    shutil.copyfile(db_path, backup_path)

    restored = sqlite3.connect(backup_path)
    restored.row_factory = sqlite3.Row
    try:
        after = [dict(r) for r in restored.execute(
            "SELECT * FROM joint_understanding_session ORDER BY id"
        )]
    finally:
        restored.close()
    assert before == after
