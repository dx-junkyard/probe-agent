"""IK-0037: component_id is assigned by the system, not by the probe planner LLM."""

import re
import subprocess

from reasoning_pipeline_helpers import (  # noqa: F401  (fixtures)
    FixedClient,
    admin_client,
    draft_response,
    git_repo,
    link,
    snapshot,
    system_headers,
    use_client,
)


def _plan_response(component_id=None):
    point = {
        "symbol_qualified_name": "list_users",
        "symbol_path": "src/main.py",
        "reason": "Central path.",
        "recommended_mode": "trace",
        "side_effect_risk": "low",
        "replayability": "safe",
    }
    if component_id is not None:
        point["component_id"] = component_id
    return {"objective": "Observe listing.", "probe_points": [point], "avoid_reasons": []}


def _accepted_links(client, h, monkeypatch):
    use_client(monkeypatch, FixedClient(draft_response()))
    assert client.post("/repository/drafts/generate", headers=h).status_code == 201
    client.post("/repository/symbols/index", headers=h)
    use_client(monkeypatch, FixedClient({"links": [link("user-management", "list_users")]}))
    links = client.post("/repository/code-links/generate", headers=h).json()
    for item in links["links"]:
        client.put(
            f"/repository/code-links/{item['id']}/review",
            json={"review_status": "accepted"},
            headers=h,
        )


def _plan(client, h, monkeypatch, response):
    fake = use_client(monkeypatch, FixedClient(response))
    r = client.post("/repository/probe-plans/generate?feature_id=user-management", headers=h)
    assert r.status_code == 201, r.text
    return fake, r.json()


def test_derivation_is_deterministic_kebab_and_path_sensitive():
    from app.component_ids import derive_component_id

    a = derive_component_id("src/main.py", "UserService.list_users")
    assert a == derive_component_id("src/main.py", "UserService.list_users")
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", a)
    assert a.startswith("userservice-list-users-")
    assert a != derive_component_id("src/other.py", "UserService.list_users")
    assert derive_component_id("x.py", "") .startswith("symbol-")


def test_prompt_schema_has_no_component_id_and_model_value_is_ignored(
    admin_client, git_repo, monkeypatch
):
    from app.component_ids import derive_component_id
    from app.probe_planner import PROMPT_VERSION

    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _accepted_links(admin_client, h, monkeypatch)

    fake, plan = _plan(admin_client, h, monkeypatch, _plan_response("llm-made-up-id"))
    prompt = fake.prompts[0][1]["content"]
    assert '"component_id"' not in prompt
    assert "component_id:" not in prompt
    assert PROMPT_VERSION == "v2"
    [point] = plan["probe_points"]
    assert point["component_id"] == derive_component_id("src/main.py", "list_users")


def test_replanning_keeps_the_same_component_id(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _accepted_links(admin_client, h, monkeypatch)

    _, first = _plan(admin_client, h, monkeypatch, _plan_response("first-id"))
    _, second = _plan(admin_client, h, monkeypatch, _plan_response("second-id"))
    assert first["probe_points"][0]["component_id"] == second["probe_points"][0]["component_id"]


def test_previous_plan_id_is_kept_even_if_derivation_differs(
    admin_client, git_repo, monkeypatch
):
    from app.db import get_conn

    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _accepted_links(admin_client, h, monkeypatch)
    _, first = _plan(admin_client, h, monkeypatch, _plan_response())
    with get_conn() as conn:
        conn.execute(
            "UPDATE probe_points SET component_id = 'legacy-list-observer' WHERE id = ?",
            (first["probe_points"][0]["id"],),
        )
    _, second = _plan(admin_client, h, monkeypatch, _plan_response())
    assert second["probe_points"][0]["component_id"] == "legacy-list-observer"


def test_component_id_already_in_code_wins(admin_client, git_repo, monkeypatch):
    main = git_repo / "src" / "main.py"
    main.write_text(
        main.read_text().replace(
            "from fastapi import APIRouter",
            "from fastapi import APIRouter\nfrom probe_agent import probe",
        ).replace(
            "@router.get(\"/users\")\ndef list_users",
            "@router.get(\"/users\")\n@probe(component_id=\"user-lister\")\ndef list_users",
        )
    )
    subprocess.run(["git", "-C", str(git_repo), "commit", "-am", "probe"], check=True,
                   capture_output=True)
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _accepted_links(admin_client, h, monkeypatch)
    _, plan = _plan(admin_client, h, monkeypatch, _plan_response("ignored"))
    assert plan["probe_points"][0]["component_id"] == "user-lister"


def test_derived_id_taken_by_another_symbol_gets_a_longer_digest(tmp_path):
    import sqlite3

    from app.component_ids import assign_component_ids, derive_component_id

    conn = sqlite3.connect(str(tmp_path / "c.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE code_symbols (id INTEGER PRIMARY KEY, system_id INT, snapshot_id INT,"
        " path TEXT, qualified_name TEXT, component_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE probe_points (id INTEGER PRIMARY KEY, system_id INT, path TEXT,"
        " symbol TEXT, component_id TEXT)"
    )
    short = derive_component_id("a.py", "run")
    conn.execute(
        "INSERT INTO code_symbols (system_id, snapshot_id, path, qualified_name, component_id)"
        " VALUES (1, 1, 'other.py', 'other', ?)",
        (short,),
    )
    result = assign_component_ids(conn, 1, [("a.py", "run"), ("b.py", "run")])
    assigned = result[("a.py", "run")]
    assert assigned.source == "derived"
    assert assigned.component_id == derive_component_id("a.py", "run", 12)
    assert result[("b.py", "run")].component_id == derive_component_id("b.py", "run")
    # Another System is unaffected.
    assert assign_component_ids(conn, 2, [("a.py", "run")])[("a.py", "run")].component_id == short
