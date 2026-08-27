"""Compatibility and completion-gate tests for Epic #394 Phase 1 (#396).

Three promises from `docs/evolutionary-pipeline.md` that live outside the
Node contract itself:

1. ADR-1 検証方法 -- the Evolution Node implementation changes NOTHING about
   the Probe Cell layer: the CREATE TABLE DDL of `cell_definitions`,
   `cell_bindings` and `agent_role_cards` is byte-identical to the frozen
   strings below. A failure here means a Cell table was touched, which ADR-1
   forbids for the whole Epic (Cells are read-only referents of Nodes).
2. §8.1 完了ゲート -- the three §7 pilots can be registered as Evolution
   Nodes without writing anything into the target repository's runtime data
   (no Component, no Probe Point, no Cell, no snapshot is fabricated).
3. ADR-3 -- an implementation's identity carries NO provider/model real-name
   field, in the table and in the API document alike; such names may only
   live inside the versioned `config`/`provenance` payloads.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "evolution-compat-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_headers(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# 1. ADR-1: the Cell tables' DDL is byte-identical to the pre-Node schema
# ---------------------------------------------------------------------------

# Frozen from `sqlite_master` of an `init_db()` database. These strings are
# the contract: do NOT regenerate them to make a failing test pass -- a
# mismatch IS the finding (a Cell table changed, which ADR-1 forbids).
_FROZEN_CELL_DDL = {
    "cell_definitions": (
        "CREATE TABLE cell_definitions (\n"
        "    id              INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    system_id       INTEGER NOT NULL,\n"
        "    cell_id         TEXT NOT NULL,\n"
        "    roster_json     TEXT,\n"
        "    role_card_id    INTEGER NOT NULL,\n"
        "    status          TEXT NOT NULL DEFAULT 'dormant'\n"
        "                        CHECK (status IN ('active', 'dormant', 'retired')),\n"
        "    mission         TEXT NOT NULL DEFAULT '',\n"
        "    created_at      REAL NOT NULL,\n"
        "    updated_at      REAL NOT NULL,\n"
        "    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,\n"
        "    FOREIGN KEY (role_card_id) REFERENCES agent_role_cards (id) ON DELETE RESTRICT,\n"
        "    UNIQUE (system_id, cell_id)\n"
        ")"
    ),
    "cell_bindings": (
        "CREATE TABLE cell_bindings (\n"
        "    id                    INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    system_id             INTEGER NOT NULL,\n"
        "    cell_definition_id    INTEGER NOT NULL,\n"
        "    version               INTEGER NOT NULL,\n"
        "    snapshot_id           INTEGER NOT NULL,\n"
        "    commit_sha            TEXT NOT NULL,\n"
        "    path                  TEXT NOT NULL,\n"
        "    qualified_symbol      TEXT NOT NULL,\n"
        "    component_id          TEXT NOT NULL,\n"
        "    probe_point_id        INTEGER,\n"
        "    probe_pattern_id      INTEGER,\n"
        "    feature_refs_json     TEXT NOT NULL DEFAULT '[]',\n"
        "    capability_refs_json  TEXT NOT NULL DEFAULT '[]',\n"
        "    entrypoint_refs_json  TEXT NOT NULL DEFAULT '[]',\n"
        "    status                TEXT NOT NULL DEFAULT 'active'\n"
        "                              CHECK (status IN ('active', 'stale', "
        "'review_required', 'superseded')),\n"
        "    status_reason         TEXT NOT NULL DEFAULT '',\n"
        "    created_at            REAL NOT NULL,\n"
        "    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,\n"
        "    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) "
        "ON DELETE CASCADE,\n"
        "    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) "
        "ON DELETE RESTRICT,\n"
        "    FOREIGN KEY (probe_point_id) REFERENCES probe_points (id) ON DELETE SET NULL,\n"
        "    FOREIGN KEY (probe_pattern_id) REFERENCES probe_patterns (id) "
        "ON DELETE SET NULL,\n"
        "    UNIQUE (system_id, cell_definition_id, version)\n"
        ")"
    ),
    "agent_role_cards": (
        "CREATE TABLE agent_role_cards (\n"
        "    id                          INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    system_id                   INTEGER NOT NULL,\n"
        "    role_key                    TEXT NOT NULL,\n"
        "    version                     TEXT NOT NULL,\n"
        "    status                      TEXT NOT NULL DEFAULT 'draft'\n"
        "                                     CHECK (status IN ('draft', 'active', "
        "'deprecated')),\n"
        "    mission                     TEXT NOT NULL,\n"
        "    scope_json                  TEXT NOT NULL DEFAULT '[]',\n"
        "    out_of_scope_json           TEXT NOT NULL DEFAULT '[]',\n"
        "    model_alias                 TEXT NOT NULL,\n"
        "    tool_policy_json            TEXT NOT NULL DEFAULT '{}',\n"
        "    acceptance_template_json    TEXT NOT NULL DEFAULT '[]',\n"
        "    rubric_ref                  TEXT,\n"
        "    changelog                   TEXT NOT NULL,\n"
        "    schema_version              TEXT NOT NULL,\n"
        "    decision_method             TEXT NOT NULL DEFAULT 'manual',\n"
        "    created_by                  TEXT,\n"
        "    created_at                  REAL NOT NULL,\n"
        "    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,\n"
        "    UNIQUE (system_id, role_key, version)\n"
        ")"
    ),
}


class TestCellDdlUnchanged:
    def test_cell_table_ddl_is_byte_identical_to_the_frozen_contract(self, admin_client):
        from app.db import get_conn, init_db

        init_db()
        with get_conn() as conn:
            for table, frozen_sql in _FROZEN_CELL_DDL.items():
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                assert row is not None, f"{table} is missing entirely"
                assert row["sql"] == frozen_sql, (
                    f"{table}'s CREATE TABLE DDL changed. ADR-1 forbids any "
                    "change to the Cell layer for the whole of Epic #394 -- "
                    "do not update the frozen string; revert the schema change."
                )


# ---------------------------------------------------------------------------
# 2. §8.1 完了ゲート: the three §7 pilots register without runtime writes
# ---------------------------------------------------------------------------

# node_key slug / modality per docs/evolutionary-pipeline.md §7 and §5:
# pilot 1 stays reasoning_llm (open-ended intent classification), pilot 2 is
# already-stabilized deterministic code, pilot 3 stays reasoning_llm (open
# natural-language translation).
_PILOTS = [
    ("question-router", "app/question_router.py route_question()", "reasoning_llm"),
    (
        "interview-workflow-evaluate-candidate-state",
        "app/interview_workflow.py evaluate_candidate_state()",
        "deterministic_code",
    ),
    (
        "understanding-translator",
        "app/understanding_translator.py translate_findings()",
        "reasoning_llm",
    ),
]

# Runtime/repository-derived tables that registering a design-time Node must
# never touch (ADR-2: a Node may exist before any instrumentation does).
_RUNTIME_TABLES = (
    "components",
    "probe_points",
    "probe_plans",
    "cell_definitions",
    "cell_bindings",
    "agent_role_cards",
    "repository_snapshots",
    "traces",
)


class TestPilotRegistrationSmoke:
    def test_the_three_pilots_register_as_nodes_without_runtime_data(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Pilots")

        for node_key, mission_target, modality in _PILOTS:
            r = admin_client.post(
                "/evolution-nodes",
                json={"node_key": node_key},
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text
            node = r.json()
            assert node["maturity"] == "exploring"

            r = admin_client.post(
                f"/evolution-nodes/{node['id']}/versions",
                json={
                    "mission": f"pilot contract for {mission_target}",
                    "side_effect_class": "pure",
                    "trust_boundary": "internal",
                },
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text
            version = r.json()

            r = admin_client.post(
                f"/evolution-nodes/{node['id']}/implementations",
                json={"node_version_id": version["id"], "modality": modality},
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text
            assert r.json()["modality"] == modality

        r = admin_client.get("/evolution-nodes", headers=_headers(token, system_id))
        assert sorted(n["node_key"] for n in r.json()["nodes"]) == sorted(
            key for key, _target, _modality in _PILOTS
        )

        # The completion gate's actual claim: registration wrote NOTHING into
        # any runtime/repository-derived table -- the pilots are pure
        # design-time entities with no Component, Probe Point, Cell, or
        # snapshot behind them.
        with get_conn() as conn:
            for table in _RUNTIME_TABLES:
                count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                assert count == 0, (
                    f"registering a pilot Node wrote into {table}; a Node must "
                    "be registrable before any runtime data exists (ADR-2)"
                )


# ---------------------------------------------------------------------------
# 3. ADR-3: no provider/model real-name field in implementation identity
# ---------------------------------------------------------------------------


class TestNoProviderModelFields:
    def test_implementation_table_has_no_provider_or_model_column(self, admin_client):
        from app.db import get_conn, init_db

        init_db()
        with get_conn() as conn:
            columns = [
                row["name"]
                for row in conn.execute("PRAGMA table_info(evolution_node_implementation)")
            ]
        # `config_json`/`provenance_json` are the sanctioned homes for such
        # names (as opaque versioned payloads); no COLUMN may name one.
        for column in columns:
            assert "provider" not in column.lower(), columns
            assert "model" not in column.lower(), columns

    def test_api_documents_carry_no_provider_or_model_key(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-NoModelNames")
        r = admin_client.post(
            "/evolution-nodes",
            json={"node_key": "no-model-names"},
            headers=_headers(token, system_id),
        )
        node = r.json()
        r = admin_client.post(
            f"/evolution-nodes/{node['id']}/versions",
            json={
                "mission": "m",
                "side_effect_class": "pure",
                "trust_boundary": "internal",
            },
            headers=_headers(token, system_id),
        )
        version = r.json()
        r = admin_client.post(
            f"/evolution-nodes/{node['id']}/implementations",
            json={"node_version_id": version["id"], "modality": "reasoning_llm"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        implementation = r.json()

        def _assert_no_name_keys(doc, where):
            for key in doc:
                if key in ("config", "provenance"):
                    # The versioned payloads are the one sanctioned home for
                    # provider/model names; their inner keys are out of scope.
                    continue
                assert "provider" not in key.lower(), (where, key)
                assert "model" not in key.lower(), (where, key)

        _assert_no_name_keys(implementation, "implementation response")

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}", headers=_headers(token, system_id)
        )
        projection = r.json()
        _assert_no_name_keys(projection, "projection")
        _assert_no_name_keys(projection["current_implementation"], "projection implementation")
        _assert_no_name_keys(projection["current_version"], "projection version")
