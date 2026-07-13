"""Tests for Issue #242 Phase A / #243: replay-capture trace fields.

Covers ingest + read-back of the additive input_capture / replayability /
replay_reasons fields, backward compatibility for old SDK payloads, enum
validation (422 on unknown values), and the additive SQLite migration
(pre-Phase-A rows stay NULL; no bulk reclassification).
"""

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-replay.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _trace(trace_id="t1", component_id="capt", **extra):
    ev = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": ["(1, 2)"], "kwargs": {}},
        "output": "'ok'",
        "error": None,
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }
    ev.update(extra)
    return ev


_CAPTURE = {
    "args": [{"__probe__": "tuple", "items": [1, 2]}],
    "kwargs": {"tag": {"__probe__": "set", "items": ["a", "b"]}},
}


def test_ingest_with_replay_fields_persists_and_returns(client):
    r = client.post("/traces", json=_trace(
        input_capture=_CAPTURE,
        replayability="replayable",
        replay_reasons=[],
    ))
    assert r.status_code == 201

    rows = client.get("/components/capt/traces").json()
    assert len(rows) == 1
    assert rows[0]["input_capture"] == _CAPTURE
    assert rows[0]["replayability"] == "replayable"
    assert rows[0]["replay_reasons"] == []
    # Existing repr-based fields are untouched.
    assert rows[0]["input"] == {"args": ["(1, 2)"], "kwargs": {}}
    assert rows[0]["output"] == "'ok'"


def test_partial_classification_with_reasons_round_trips(client):
    client.post("/traces", json=_trace(
        input_capture={"args": [{"__probe__": "unsupported", "type": "Socket"}],
                       "kwargs": {}},
        replayability="partial",
        replay_reasons=["unsupported_type", "redacted"],
    ))
    row = client.get("/components/capt/traces").json()[0]
    assert row["replayability"] == "partial"
    assert row["replay_reasons"] == ["unsupported_type", "redacted"]


def test_unreplayable_with_null_capture(client):
    client.post("/traces", json=_trace(
        input_capture=None,
        replayability="unreplayable",
        replay_reasons=["size_limit_exceeded"],
    ))
    row = client.get("/components/capt/traces").json()[0]
    assert row["input_capture"] is None
    assert row["replayability"] == "unreplayable"
    assert row["replay_reasons"] == ["size_limit_exceeded"]


def test_old_sdk_payload_without_fields_returns_nulls(client):
    r = client.post("/traces", json=_trace())
    assert r.status_code == 201
    row = client.get("/components/capt/traces").json()[0]
    assert row["input_capture"] is None
    assert row["replayability"] is None
    assert row["replay_reasons"] is None


def test_invalid_replayability_rejected_422(client):
    r = client.post("/traces", json=_trace(replayability="maybe"))
    assert r.status_code == 422


def test_invalid_replay_reason_rejected_422(client):
    r = client.post("/traces", json=_trace(
        replayability="partial", replay_reasons=["because-i-said-so"]))
    assert r.status_code == 422


def test_repost_without_fields_replaces_capture(client):
    client.post("/traces", json=_trace(
        input_capture=_CAPTURE, replayability="replayable", replay_reasons=[]))
    client.post("/traces", json=_trace())  # re-post, old SDK shape
    row = client.get("/components/capt/traces").json()[0]
    assert row["input_capture"] is None
    assert row["replayability"] is None


def test_migration_adds_columns_and_preserves_pre_phase_a_rows(tmp_path, monkeypatch):
    """A pre-Phase-A database gains the new columns via ALTER TABLE and its
    existing rows read back as NULL (no bulk reclassification)."""
    db_path = tmp_path / "pre-phase-a.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE traces (
            system_id    INTEGER NOT NULL,
            trace_id     TEXT NOT NULL,
            component_id TEXT NOT NULL,
            mode         TEXT,
            input_json   TEXT,
            output_text  TEXT,
            error        TEXT,
            duration_ms  REAL,
            timestamp    REAL NOT NULL,
            PRIMARY KEY (system_id, trace_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO traces
            (system_id, trace_id, component_id, mode, input_json, output_text,
             error, duration_ms, timestamp)
        VALUES (1, 'old-1', 'legacy-comp', 'trace',
                ?, ?, NULL, 1.0, ?)
        """,
        (json.dumps({"args": ["'x'"], "kwargs": {}}), "'X'", time.time()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
    from app.main import app
    with TestClient(app) as c:
        # Startup (init_db) must have added the columns without touching rows.
        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        cols = {r["name"] for r in check.execute("PRAGMA table_info(traces)")}
        assert {"input_capture_json", "replayability", "replay_reasons_json"} <= cols
        row = check.execute(
            "SELECT * FROM traces WHERE trace_id = 'old-1'"
        ).fetchone()
        assert row["input_capture_json"] is None
        assert row["replayability"] is None
        assert row["replay_reasons_json"] is None
        legacy_id = check.execute(
            "SELECT id FROM systems WHERE name = 'Legacy System'"
        ).fetchone()["id"]
        check.close()
        assert row["system_id"] == legacy_id  # fresh systems table -> id 1

        # The old row is served through the API with explicit nulls.
        rows = c.get("/components/legacy-comp/traces").json()
        assert len(rows) == 1
        assert rows[0]["trace_id"] == "old-1"
        assert rows[0]["input_capture"] is None
        assert rows[0]["replayability"] is None
        assert rows[0]["replay_reasons"] is None
        assert rows[0]["output"] == "'X'"

        # New ingest into the migrated DB works with the new fields.
        r = c.post("/traces", json=_trace(
            trace_id="new-1", component_id="legacy-comp",
            input_capture=_CAPTURE, replayability="replayable",
            replay_reasons=[]))
        assert r.status_code == 201
        rows = c.get("/components/legacy-comp/traces").json()
        by_id = {r_["trace_id"]: r_ for r_ in rows}
        assert by_id["new-1"]["input_capture"] == _CAPTURE
        assert by_id["old-1"]["input_capture"] is None
