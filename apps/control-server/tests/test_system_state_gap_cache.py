"""Gap counts on /system-state are cached behind a fingerprint of their inputs."""
import sqlite3
import time

from app.db import SCHEMA


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    now = time.time()
    conn.execute("INSERT INTO systems (id, name, created_at, updated_at) VALUES (1, 's', ?, ?)", (now, now))
    snap = conn.execute("INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, file_count, created_at)"
                        " VALUES (1, '/r', 'abc', 'completed', 0, ?)", (now,)).lastrowid
    return conn, snap


def test_counts_are_reused_until_an_input_changes(monkeypatch):
    import app.system_state as st
    import app.system_understanding_service as sus

    st._GAP_COUNT_CACHE.clear()
    calls = []
    monkeypatch.setattr(sus, "_load_gaps_from_reconciler",
                        lambda conn, s, snap: calls.append(1) or [{"gap_type": "code_only"}, {"gap_type": "docs_only"}])
    monkeypatch.setattr(sus, "_attach_gap_triage", lambda conn, s, snap, gaps: None)
    monkeypatch.setattr(sus, "_open_gaps", lambda gaps: gaps)
    conn, snap = _db()
    assert st._open_gap_counts(conn, 1, snap) == (2, 2)
    assert st._open_gap_counts(conn, 1, snap) == (2, 2)
    assert len(calls) == 1
    conn.execute(
        "INSERT INTO code_symbols (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line)"
        " VALUES (?, 1, 'a.py', 'f', 'function', 1, 2)", (snap,))
    st._open_gap_counts(conn, 1, snap)
    assert len(calls) == 2  # a new symbol invalidates the cached counts
