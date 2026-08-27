"""Issue #276: stable gap identity, finite manual triage and noise policy."""

import time

import pytest
from fastapi.testclient import TestClient


def _gap(*, notes="missing docs", line=10, source_id=None):
    return {
        "gap_type": "code_only",
        "severity": "info",
        "title": "Undocumented symbol: service.run",
        "node_name": "service.run",
        "notes": notes,
        "capability_key": "execution",
        "doc_refs": [],
        "symbol_refs": [
            {"path": "src/service.py", "qualified_name": "service.run", "start_line": line}
        ],
        "entrypoint_refs": [],
        "code_refs": [
            {
                "source": "code_symbol",
                "path": "src/service.py",
                "qualified_name": "service.run",
                "start_line": line,
                "end_line": line + 5,
                "symbol_id": line,
            }
        ],
        "next_actions": [],
        "source_id": source_id,
    }


def test_gap_key_and_fingerprint_ignore_lines_and_snapshot_local_ids():
    from app.gap_triage import gap_content_fingerprint, gap_key

    first = _gap(line=10)
    shifted = _gap(line=410)
    assert gap_key(first) == gap_key(shifted)
    assert gap_content_fingerprint(first) == gap_content_fingerprint(shifted)
    assert gap_key(first) == "code_only|symbol|src/service.py::service.run"


def test_node_target_is_deterministic_and_human_readable_across_snapshots():
    from app.gap_triage import gap_key
    from app.understanding_graph import _node_id

    node_id_a = _node_id("open_question", "how retries work")
    node_id_b = _node_id("open_question", "how retries work")
    assert node_id_a == node_id_b
    first = {
        **_gap(source_id=f"node:{node_id_a}"),
        "gap_type": "missing_evidence",
        "node_name": "how retries work",
        "symbol_refs": [],
        "code_refs": [],
    }
    second = {**first, "source_id": f"node:{node_id_b}"}
    key = gap_key(first)
    assert key == gap_key(second)
    assert "how retries work" in key
    assert node_id_a in key


def test_triage_lifecycle_is_an_explicit_finite_set():
    from app.gap_triage import ALLOWED_TRANSITIONS

    assert ALLOWED_TRANSITIONS == {
        "open": {"acknowledged"},
        "acknowledged": {"open", "dismissed", "resolved"},
        "dismissed": {"open"},
        "resolved": {"open"},
    }


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-gap-triage.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    )
    assert response.status_code == 200, response.text
    return response.cookies.get("probe_session")


def _create_system(client, token, name):
    response = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _headers(token, system_id):
    return {
        "Authorization": f"Bearer {token}",
        "X-Probe-System-Id": str(system_id),
    }


def _ready_snapshot(system_id, suffix="a"):
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/repo', ?, 'ready', ?, ?)""",
            (system_id, suffix * 40, time.time(), time.time()),
        )
        return cur.lastrowid


def test_finite_lifecycle_audit_isolation_reopen_and_open_only_counts(
    admin_client, monkeypatch
):
    from app import system_understanding_service as understanding
    from app.db import get_conn
    from app.gap_triage import gap_key
    from app.system_state import _open_gap_triage_item
    from app.system_understanding_jobs import _record_gap_history

    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "gap-a")
    system_b = _create_system(admin_client, token, "gap-b")
    snapshot_a = _ready_snapshot(system_a, "a")
    _ready_snapshot(system_b, "b")
    live = [_gap(), {**_gap(), "node_name": "service.stop", "title": "Stop", "symbol_refs": [
        {"path": "src/service.py", "qualified_name": "service.stop"}
    ]}]
    monkeypatch.setattr(
        understanding,
        "_load_gaps_from_reconciler",
        lambda conn, system_id, snapshot_id: [dict(g) for g in live],
    )

    headers_a = _headers(token, system_a)
    body = admin_client.get("/repository/system-understanding", headers=headers_a).json()
    first = body["gaps"][0]
    assert first["triage_status"] == "open"
    assert first["gap_key"] == gap_key(live[0])
    assert len(body["gap_summary"]) == 1
    assert body["gap_summary"][0]["count"] == 2

    # Finite lifecycle rejects skipping acknowledgement.
    invalid = admin_client.post(
        "/repository/system-understanding/gap-triage",
        headers=headers_a,
        json={
            "gap_key": first["gap_key"],
            "content_fingerprint": first["content_fingerprint"],
            "status": "dismissed",
        },
    )
    assert invalid.status_code == 409

    acknowledged = admin_client.post(
        "/repository/system-understanding/gap-triage",
        headers=headers_a,
        json={
            "gap_key": first["gap_key"],
            "content_fingerprint": first["content_fingerprint"],
            "status": "acknowledged",
        },
    )
    assert acknowledged.status_code == 201, acknowledged.text
    assert acknowledged.json()["decision_method"] == "manual"
    assert acknowledged.json()["decided_by_user_id"] is not None

    dismissed = admin_client.post(
        "/repository/system-understanding/gap-triage",
        headers=headers_a,
        json={
            "gap_key": first["gap_key"],
            "content_fingerprint": first["content_fingerprint"],
            "status": "dismissed",
            "note": "intentional internal symbol",
        },
    )
    assert dismissed.status_code == 201, dismissed.text

    # System B has the same live gap but none of system A's decision state.
    body_b = admin_client.get(
        "/repository/system-understanding", headers=_headers(token, system_b)
    ).json()
    assert body_b["gaps"][0]["triage_status"] == "open"

    body_a = admin_client.get("/repository/system-understanding", headers=headers_a).json()
    assert body_a["gaps"][0]["triage_status"] == "dismissed"
    assert body_a["gap_summary"][0]["count"] == 1
    with get_conn() as conn:
        item = _open_gap_triage_item(conn, system_a, snapshot_a)
        assert item is not None
        assert item.evidence["open_gap_count"] == 1
        assert item.evidence["triaged_gap_count"] == 1

    # Semantic content change reopens the same locator. GET is read-only.
    live[0] = _gap(notes="implementation changed")
    changed = admin_client.get("/repository/system-understanding", headers=headers_a).json()
    assert changed["gaps"][0]["gap_key"] == first["gap_key"]
    assert changed["gaps"][0]["triage_status"] == "open"
    assert changed["gaps"][0]["triage_reopen_reason"] == "content_changed"
    with get_conn() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM gap_triage_decisions WHERE system_id = ?",
            (system_a,),
        ).fetchone()[0]
        build_id = conn.execute(
            """INSERT INTO system_understanding_builds
                (system_id, snapshot_id, status, created_at)
               VALUES (?, ?, 'completed', ?)""",
            (system_a, snapshot_a, time.time()),
        ).lastrowid
        _record_gap_history(conn, system_a, snapshot_a, build_id, time.time())
        after_rows = conn.execute(
            "SELECT * FROM gap_triage_decisions WHERE system_id = ? ORDER BY id",
            (system_a,),
        ).fetchall()
        assert len(after_rows) == before + 1
        assert after_rows[-1]["status"] == "open"
        assert after_rows[-1]["decision_method"] == "deterministic"
        assert after_rows[-1]["note"] == "content_changed"
        history_count = conn.execute(
            """SELECT count FROM system_understanding_gap_history
               WHERE system_id = ? AND build_id = ? AND gap_type = 'code_only'""",
            (system_a, build_id),
        ).fetchone()[0]
        assert history_count == 2

    # Durable reopen must not fall back to the older dismissed row if content
    # later returns to its previous rendering.
    live[0] = _gap()
    returned = admin_client.get("/repository/system-understanding", headers=headers_a).json()
    assert returned["gaps"][0]["triage_status"] == "open"
    assert returned["gaps"][0]["triage_reopen_reason"] is None


def test_zero_open_gap_build_is_preserved_in_trend_and_system_scoped(
    admin_client, monkeypatch
):
    from app import system_understanding_service as understanding
    from app.db import get_conn
    from app.system_understanding_jobs import _record_gap_history

    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "trend-a")
    system_b = _create_system(admin_client, token, "trend-b")
    snapshot_a = _ready_snapshot(system_a, "c")
    snapshot_b = _ready_snapshot(system_b, "d")
    live = [_gap()]
    monkeypatch.setattr(
        understanding,
        "_load_gaps_from_reconciler",
        lambda conn, system_id, snapshot_id: [dict(g) for g in live],
    )

    def record(system_id, snapshot_id):
        with get_conn() as conn:
            build_id = conn.execute(
                """INSERT INTO system_understanding_builds
                    (system_id, snapshot_id, status, created_at)
                   VALUES (?, ?, 'completed', ?)""",
                (system_id, snapshot_id, time.time()),
            ).lastrowid
            _record_gap_history(conn, system_id, snapshot_id, build_id, time.time())
            return build_id

    record(system_a, snapshot_a)
    first = admin_client.get(
        "/repository/system-understanding", headers=_headers(token, system_a)
    ).json()["gaps"][0]
    acknowledged = admin_client.post(
        "/repository/system-understanding/gap-triage",
        headers=_headers(token, system_a),
        json={
            "gap_key": first["gap_key"],
            "content_fingerprint": first["content_fingerprint"],
            "status": "acknowledged",
        },
    )
    assert acknowledged.status_code == 201, acknowledged.text
    current_build = record(system_a, snapshot_a)
    record(system_b, snapshot_b)

    with get_conn() as conn:
        trend_a = understanding._load_gap_trend(conn, system_a)
        trend_b = understanding._load_gap_trend(conn, system_b)
        marker = conn.execute(
            """SELECT gap_type, count FROM system_understanding_gap_history
               WHERE system_id = ? AND build_id = ?""",
            (system_a, current_build),
        ).fetchone()
    assert [(t.gap_type, t.previous, t.current) for t in trend_a] == [
        ("code_only", 1, 0)
    ]
    assert trend_b == []
    assert dict(marker) == {"gap_type": "__no_open_gaps__", "count": 0}
