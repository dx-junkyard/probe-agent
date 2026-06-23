"""Tests for Issue #36 Decision Workspace Context Pack Builder."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-context-test.db"))
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


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "production", "description": ""},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _setup(admin_client, name="System A"):
    admin_token = _login(admin_client)
    system = _create_system(admin_client, admin_token, name)
    return admin_token, system["id"]


def _create_workspace(admin_client, headers, **kwargs):
    payload = {"title": "Theme"}
    payload.update(kwargs)
    r = admin_client.post("/workspaces", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _pin(admin_client, headers, workspace_id, item_type, item_id, label=""):
    r = admin_client.post(
        f"/workspaces/{workspace_id}/context",
        json={"item_type": item_type, "item_id": str(item_id), "label": label},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _seed_component(system_id, component_id="summarizer"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO component_profiles
                   (system_id, component_id, purpose, responsibility, expected_input,
                    expected_output, failure_impact, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id,
                component_id,
                "Summarize text",
                "Turns long text into a short summary",
                "raw text",
                "short summary",
                "low",
                "",
                now,
                now,
            ),
        )


def _seed_traces(system_id, component_id="summarizer", count=3, with_error=True):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO components
                   (system_id, component_id, mode, updated_at)
               VALUES (?, ?, 'trace', ?)""",
            (system_id, component_id, now),
        )
        for i in range(count):
            conn.execute(
                """INSERT INTO traces
                       (system_id, trace_id, component_id, mode, input_json, output_text,
                        error, duration_ms, timestamp)
                   VALUES (?, ?, ?, 'trace', ?, ?, ?, 1.0, ?)""",
                (
                    system_id,
                    f"trace-{i}",
                    component_id,
                    f'{{"text": "input {i}"}}',
                    f"summary {i}",
                    "boom" if with_error and i == 0 else None,
                    now + i,
                ),
            )


def _seed_evaluation(system_id, component_id="summarizer"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO evaluation_criteria
                   (system_id, component_id, name, description, criterion_type,
                    expected_value, weight, enabled, created_at, updated_at)
               VALUES (?, ?, 'length', '', 'manual', NULL, 1.0, 1, ?, ?)""",
            (system_id, component_id, now, now),
        )
        conn.execute(
            """INSERT INTO evaluation_results
                   (system_id, trace_id, component_id, criterion_id, status, score,
                    reason, actual_output, expected_value, created_at)
               VALUES (?, 'trace-0', ?, 1, 'ng', 0.0, 'too short', 'x', 'y', ?)""",
            (system_id, component_id, now),
        )
        conn.execute(
            """INSERT INTO evaluation_results
                   (system_id, trace_id, component_id, criterion_id, status, score,
                    reason, actual_output, expected_value, created_at)
               VALUES (?, 'trace-1', ?, 1, 'ok', 1.0, '', 'x', 'y', ?)""",
            (system_id, component_id, now),
        )


def test_pack_with_no_pinned_items_has_only_system_and_focus(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = _create_workspace(
        admin_client, headers, title="Improve summarizer", focus="summarizer quality"
    )

    r = admin_client.get(f"/workspaces/{workspace['id']}/context-pack", headers=headers)
    assert r.status_code == 200, r.text
    pack = r.json()

    assert pack["system"]["system_id"] == system_id
    assert pack["focus"]["focus"] == "summarizer quality"
    assert pack["features"] == []
    assert pack["components"] == []
    assert pack["traces"] == []
    assert pack["evaluations"] == []
    assert pack["probe_plans"] == []
    assert pack["experiments"] == []
    assert pack["evidence"] == []
    # No ready repository snapshot exists for this fresh system.
    assert pack["repository"] is None
    assert any("repository" in m for m in pack["missing_information"])


def test_pinned_component_yields_component_and_evaluation_digest(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _seed_component(system_id)
    _seed_traces(system_id)
    _seed_evaluation(system_id)
    workspace = _create_workspace(admin_client, headers)
    _pin(admin_client, headers, workspace["id"], "component", "summarizer")

    r = admin_client.get(f"/workspaces/{workspace['id']}/context-pack", headers=headers)
    pack = r.json()

    assert len(pack["components"]) == 1
    assert pack["components"][0]["component_id"] == "summarizer"
    assert pack["components"][0]["purpose"] == "Summarize text"

    # Trace digests are only produced for "trace" pins, not "component" pins.
    assert pack["traces"] == []

    assert len(pack["evaluations"]) == 1
    evaluation = pack["evaluations"][0]
    assert evaluation["component_id"] == "summarizer"
    assert evaluation["passed_count"] == 1
    assert evaluation["failed_count"] == 1
    assert "too short" in evaluation["top_failure_reasons"]


def test_pinned_trace_component_yields_trace_digest(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _seed_traces(system_id, count=3, with_error=True)
    workspace = _create_workspace(admin_client, headers)
    _pin(admin_client, headers, workspace["id"], "trace", "summarizer")

    r = admin_client.get(f"/workspaces/{workspace['id']}/context-pack", headers=headers)
    pack = r.json()

    assert len(pack["traces"]) == 1
    digest = pack["traces"][0]
    assert digest["component_id"] == "summarizer"
    assert digest["trace_count"] == 3
    assert digest["error_count"] == 1
    assert digest["representative_input"] is not None


def test_trace_digest_redacts_common_secret_fields(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _seed_traces(system_id, count=1, with_error=False)
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """UPDATE traces
               SET input_json = ?, output_text = ?
               WHERE system_id = ? AND component_id = ?""",
            (
                '{"password":"do-not-send","api_key":"sk-secret"}',
                "Authorization: Bearer private-token",
                system_id,
                "summarizer",
            ),
        )
    workspace = _create_workspace(admin_client, headers)
    _pin(admin_client, headers, workspace["id"], "trace", "summarizer")

    pack = admin_client.get(
        f"/workspaces/{workspace['id']}/context-pack", headers=headers
    ).json()
    digest = pack["traces"][0]
    assert "do-not-send" not in digest["representative_input"]
    assert "sk-secret" not in digest["representative_input"]
    assert "private-token" not in digest["representative_output"]
    assert "[REDACTED]" in digest["representative_input"]


def test_unknown_pinned_references_are_recorded_as_missing(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = _create_workspace(admin_client, headers)
    from app.db import get_conn

    with get_conn() as conn:
        for item_type, item_id in [
            ("component", "does-not-exist"),
            ("experiment", "9999"),
            ("probe_plan", "9999"),
        ]:
            conn.execute(
                """INSERT INTO workspace_context_items
                       (workspace_id, system_id, item_type, item_id, label, created_at)
                   VALUES (?, ?, ?, ?, '', ?)""",
                (workspace["id"], system_id, item_type, item_id, time.time()),
            )

    r = admin_client.get(f"/workspaces/{workspace['id']}/context-pack", headers=headers)
    pack = r.json()

    assert pack["components"] == []
    assert pack["experiments"] == []
    assert pack["probe_plans"] == []
    assert any("does-not-exist" in m for m in pack["missing_information"])
    assert any("9999" in m for m in pack["missing_information"])


def test_context_pack_is_deterministic_for_identical_state(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _seed_component(system_id)
    _seed_traces(system_id)
    _seed_evaluation(system_id)
    workspace = _create_workspace(admin_client, headers)
    _pin(admin_client, headers, workspace["id"], "trace", "summarizer")
    _pin(admin_client, headers, workspace["id"], "component", "summarizer")

    r1 = admin_client.get(f"/workspaces/{workspace['id']}/context-pack", headers=headers)
    r2 = admin_client.get(f"/workspaces/{workspace['id']}/context-pack", headers=headers)
    assert r1.json() == r2.json()


def test_context_pack_does_not_leak_across_systems(admin_client):
    token, system_a = _setup(admin_client, "System A")
    _, system_b = _setup(admin_client, "System B")
    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b)

    _seed_component(system_a, "shared-id")
    _seed_component(system_b, "shared-id")

    workspace_a = _create_workspace(admin_client, headers_a)
    _pin(admin_client, headers_a, workspace_a["id"], "component", "shared-id")

    r = admin_client.get(f"/workspaces/{workspace_a['id']}/context-pack", headers=headers_a)
    pack = r.json()
    assert pack["system"]["system_id"] == system_a
    assert len(pack["components"]) == 1

    # System B cannot fetch a context pack for System A's workspace at all.
    r = admin_client.get(
        f"/workspaces/{workspace_a['id']}/context-pack", headers=headers_b
    )
    assert r.status_code == 404


def test_pinned_item_budget_records_omission(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = _create_workspace(admin_client, headers)
    for i in range(12):
        component_id = f"component-{i}"
        _seed_component(system_id, component_id)
        _pin(admin_client, headers, workspace["id"], "component", component_id)

    r = admin_client.get(f"/workspaces/{workspace['id']}/context-pack", headers=headers)
    pack = r.json()

    assert len(pack["components"]) == 10
    assert any("budget" in m for m in pack["missing_information"])


def test_category_character_budget_is_independent_and_records_omission(
    admin_client, monkeypatch
):
    import app.workspace_context as workspace_context

    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _seed_component(system_id)
    workspace = _create_workspace(admin_client, headers)
    _pin(admin_client, headers, workspace["id"], "component", "summarizer")
    monkeypatch.setattr(workspace_context, "MAX_CATEGORY_CHARS", 20)

    pack = admin_client.get(
        f"/workspaces/{workspace['id']}/context-pack", headers=headers
    ).json()

    assert pack["components"] == []
    assert any(
        "category character budget" in message
        for message in pack["missing_information"]
    )
