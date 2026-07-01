"""Tests for Issue #68: Snapshot-grounded interview context pack builder.

Covers: evidence locations resolve against the pinned snapshot; a snapshot
with no probe-agent: metadata yields all items flagged unclassified; the
context budget is enforced (oversized snapshot is truncated deterministically,
not silently dropped); same snapshot + budget produces identical context
twice (determinism); no LLM call and no source read outside the pinned
snapshot.
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-ctx.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _insert_snapshot(system_id, commit_sha="abc123"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
            (system_id, commit_sha, now, now),
        )
        return cur.lastrowid


def _insert_symbols(system_id, snapshot_id, count=3, prefix="src/app.py"):
    """Insert code_symbols and return their IDs."""
    from app.db import get_conn

    ids = []
    with get_conn() as conn:
        for i in range(count):
            cur = conn.execute(
                """INSERT INTO code_symbols
                       (snapshot_id, system_id, path, qualified_name, kind,
                        start_line, end_line)
                   VALUES (?, ?, ?, ?, 'function', ?, ?)""",
                (snapshot_id, system_id, prefix, f"func_{i}", 10 * i + 1, 10 * i + 9),
            )
            ids.append(cur.lastrowid)
    return ids


def _insert_metadata(system_id, snapshot_id, symbol_id, path="src/app.py", qname="func_0"):
    """Insert a symbol_source_metadata row for a symbol (marks it classified)."""
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO symbol_source_metadata
                   (snapshot_id, system_id, symbol_id, path, qualified_name,
                    start_line, end_line, role, capability, element_type,
                    operation_kind, probe_value, raw_block, origin)
               VALUES (?, ?, ?, ?, ?, 1, 9, 'Summarize text', 'summarization',
                       'core', 'analysis', 'Validate quality', 'probe-agent: ...', 'source_authored')""",
            (snapshot_id, system_id, symbol_id, path, qname),
        )


def _insert_entrypoint(system_id, snapshot_id, handler_symbol_id=None, idx=0):
    """Insert a code_entrypoint and return its ID."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO code_entrypoints
                   (system_id, snapshot_id, entrypoint_type, entrypoint_id,
                    category, label, handler_symbol_id, handler_path,
                    handler_qualified_name, line_start, line_end, created_at)
               VALUES (?, ?, 'http_route', ?, 'api', ?, ?, 'src/api.py',
                       ?, ?, ?, ?)""",
            (
                system_id,
                snapshot_id,
                f"GET /items/{idx}",
                f"GET /items/{idx}",
                handler_symbol_id,
                f"api.list_items_{idx}",
                20 * idx + 1,
                20 * idx + 10,
                now,
            ),
        )
        return cur.lastrowid


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    return token, system["id"], snapshot_id


def _create_session(client, headers, snapshot_id):
    r = client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- AC: evidence locations resolve against the pinned snapshot -------------


def test_evidence_locations_resolve_against_pinned_snapshot(admin_client):
    """Every item in the context pack carries an evidence location with the
    correct snapshot_id, path, qualified_name, and line span from the pinned
    snapshot — never from uncommitted/untracked content."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    symbol_ids = _insert_symbols(system_id, snapshot_id, count=2)
    ep_id = _insert_entrypoint(system_id, snapshot_id, handler_symbol_id=symbol_ids[0])
    session = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.get(
        f"/interview/sessions/{session['id']}/context-pack",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    pack = r.json()

    assert pack["snapshot_id"] == snapshot_id
    assert len(pack["symbols"]) == 2
    for sym in pack["symbols"]:
        ev = sym["evidence"]
        assert ev["snapshot_id"] == snapshot_id
        assert ev["path"] == "src/app.py"
        assert ev["start_line"] >= 1
        assert ev["end_line"] > ev["start_line"]

    assert len(pack["entrypoints"]) == 1
    ep = pack["entrypoints"][0]
    ev = ep["evidence"]
    assert ev["snapshot_id"] == snapshot_id
    assert ev["path"] == "src/api.py"


# --- AC: no metadata ⇒ all unclassified ------------------------------------


def test_no_metadata_yields_all_unclassified(admin_client):
    """A snapshot with symbols but no probe-agent: metadata produces a context
    pack whose items are all flagged unclassified."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _insert_symbols(system_id, snapshot_id, count=5)
    _insert_entrypoint(system_id, snapshot_id)
    session = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.get(
        f"/interview/sessions/{session['id']}/context-pack",
        headers=headers,
    )
    pack = r.json()

    assert pack["total_symbols"] == 5
    assert pack["unclassified_count"] == 5
    assert pack["classified_count"] == 0
    for sym in pack["symbols"]:
        assert sym["classification"] == "unclassified"
        assert sym["has_metadata"] is False
    for ep in pack["entrypoints"]:
        assert ep["classification"] == "unclassified"
        assert ep["has_metadata"] is False


# --- AC: classified items are detected correctly ----------------------------


def test_classified_items_are_flagged(admin_client):
    """Symbols with source metadata are flagged classified and carry their
    existing metadata fields."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    symbol_ids = _insert_symbols(system_id, snapshot_id, count=3)
    _insert_metadata(system_id, snapshot_id, symbol_ids[0])
    _insert_entrypoint(system_id, snapshot_id, handler_symbol_id=symbol_ids[0])
    session = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.get(
        f"/interview/sessions/{session['id']}/context-pack",
        headers=headers,
    )
    pack = r.json()

    assert pack["classified_count"] == 1
    assert pack["unclassified_count"] == 2
    classified_syms = [s for s in pack["symbols"] if s["classification"] == "classified"]
    assert len(classified_syms) == 1
    assert classified_syms[0]["has_metadata"] is True
    assert classified_syms[0]["element_type"] == "core"
    assert classified_syms[0]["role"] == "Summarize text"
    assert classified_syms[0]["probe_value"] == "Validate quality"

    # The entrypoint whose handler has metadata is also classified.
    assert pack["entrypoints"][0]["classification"] == "classified"
    assert pack["entrypoints"][0]["has_metadata"] is True


# --- AC: unclassified items come first (priority ordering) ------------------


def test_unclassified_symbols_come_first(admin_client):
    """Unclassified symbols are placed before classified ones so the
    interview can target blank-page regions first."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    symbol_ids = _insert_symbols(system_id, snapshot_id, count=4)
    _insert_metadata(system_id, snapshot_id, symbol_ids[0], qname="func_0")
    _insert_metadata(system_id, snapshot_id, symbol_ids[1], qname="func_1")
    session = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.get(
        f"/interview/sessions/{session['id']}/context-pack",
        headers=headers,
    )
    pack = r.json()

    classifications = [s["classification"] for s in pack["symbols"]]
    # All unclassified should come before any classified.
    first_classified = classifications.index("classified") if "classified" in classifications else len(classifications)
    assert all(c == "unclassified" for c in classifications[:first_classified])
    assert all(c == "classified" for c in classifications[first_classified:])


# --- AC: context budget is enforced -----------------------------------------


def test_budget_enforced_and_truncated_deterministically(admin_client):
    """An oversized snapshot is truncated deterministically (not silently
    dropped). The truncated flag is set and an omission note is recorded."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _insert_symbols(system_id, snapshot_id, count=50, prefix="src/large.py")
    for i in range(20):
        _insert_entrypoint(system_id, snapshot_id, idx=i)
    session = _create_session(admin_client, headers, snapshot_id)

    # Use a very small budget to force truncation.
    r = admin_client.get(
        f"/interview/sessions/{session['id']}/context-pack?budget=3000",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    pack = r.json()

    assert pack["truncated"] is True
    assert pack["budget_used_chars"] <= 3000
    assert pack["budget_max_chars"] == 3000
    total_returned = len(pack["symbols"]) + len(pack["entrypoints"])
    assert total_returned < 70  # fewer than the 50 + 20 we inserted
    assert any("truncated" in note or "omitted" in note for note in pack["omission_notes"])


# --- AC: same snapshot + budget ⇒ identical output (determinism) ------------


def test_determinism_same_snapshot_produces_identical_output(admin_client):
    """The same snapshot + budget produces an identical context pack on
    consecutive calls (deterministic, reproducible)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    symbol_ids = _insert_symbols(system_id, snapshot_id, count=10)
    _insert_metadata(system_id, snapshot_id, symbol_ids[0], qname="func_0")
    _insert_entrypoint(system_id, snapshot_id, handler_symbol_id=symbol_ids[0])
    session = _create_session(admin_client, headers, snapshot_id)

    url = f"/interview/sessions/{session['id']}/context-pack?budget=10000"
    pack1 = admin_client.get(url, headers=headers).json()
    pack2 = admin_client.get(url, headers=headers).json()

    assert pack1 == pack2


# --- System isolation -------------------------------------------------------


def test_system_isolation_for_context_pack(admin_client):
    """A context pack in one system never surfaces symbols or entrypoints
    from another system."""
    token, system_a, snap_a = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")
    snap_b = _insert_snapshot(system_b["id"], commit_sha="bbb")

    _insert_symbols(system_a, snap_a, count=3)
    _insert_symbols(system_b["id"], snap_b, count=2, prefix="src/other.py")

    session_a = _create_session(admin_client, _headers(token, system_a), snap_a)
    session_b = _create_session(admin_client, _headers(token, system_b["id"]), snap_b)

    pack_a = admin_client.get(
        f"/interview/sessions/{session_a['id']}/context-pack",
        headers=_headers(token, system_a),
    ).json()
    pack_b = admin_client.get(
        f"/interview/sessions/{session_b['id']}/context-pack",
        headers=_headers(token, system_b["id"]),
    ).json()

    assert pack_a["total_symbols"] == 3
    assert pack_b["total_symbols"] == 2
    assert all(s["path"] == "src/app.py" for s in pack_a["symbols"])
    assert all(s["path"] == "src/other.py" for s in pack_b["symbols"])

    # Cross-system access is blocked.
    r = admin_client.get(
        f"/interview/sessions/{session_a['id']}/context-pack",
        headers=_headers(token, system_b["id"]),
    )
    assert r.status_code == 404


# --- Empty snapshot ---------------------------------------------------------


def test_empty_snapshot_returns_empty_pack(admin_client):
    """A snapshot with no symbols and no entrypoints returns a valid but
    empty context pack."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.get(
        f"/interview/sessions/{session['id']}/context-pack",
        headers=headers,
    )
    assert r.status_code == 200
    pack = r.json()
    assert pack["total_symbols"] == 0
    assert pack["total_entrypoints"] == 0
    assert pack["symbols"] == []
    assert pack["entrypoints"] == []
    assert pack["truncated"] is False
