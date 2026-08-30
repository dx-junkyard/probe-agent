"""Migration and backward-compatibility verification for Epic #427
(Product Objective / Milestone / Gap), Issue #433.

`docs/product-objective-lineage.md` §11 is the canonical contract. Its
claims are narrow and checkable, and each one is a way the Epic could
quietly break an existing installation:

* every table this Epic adds is new, so `init_db()` stays re-runnable;
* the ONE existing table it touches (`ux_journey_upstream_ref`) has its
  `ref_kind` CHECK *widened*, which SQLite cannot do in place -- the table
  is rebuilt once, and every existing row must survive with its `ref_kind`
  value unchanged. A widening that silently dropped or rewrote a row would
  be indistinguishable from a working migration until someone opened an
  old Journey;
* a System that never adopts this layer must keep working and must not be
  told it is "missing" anything -- the Objective layer is optional (§0-1),
  so `GET /functional-lineage` emits none of §7.3's codes for it;
* `feature_drafts` keeps its snapshot lineage, and a Feature's own identity
  survives a snapshot rebuild that takes the draft away (§1.6);
* `cell_goals` is a different feature with a different owner (§1.4) and is
  never touched.

This file adds no source changes; it is read-only verification.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/test_ux_design.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-product-objective-migration.db"))
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
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _init_repo(tmp_path, name):
    repo = str(tmp_path / name)
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "T"], check=True, capture_output=True)
    with open(os.path.join(repo, "app.py"), "w", encoding="utf-8") as f:
        f.write("def run():\n    return 1\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def _insert_snapshot(system_id, repo_path, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, repo_path, commit_sha, now, now),
        )
        return cur.lastrowid


def _dump(conn, table):
    """Every row of `table`, ordered by id, as plain dicts.

    Used to assert a migration preserved rows BYTE FOR BYTE rather than
    merely leaving the right number of them behind.
    """
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]  # noqa: S608


# The pre-#427 shape of the table, reproduced verbatim so the migration can
# be exercised against a database that predates this Epic. The only
# difference from the current DDL is the three missing `ref_kind` values.
_LEGACY_UPSTREAM_REF_DDL = """
CREATE TABLE ux_journey_upstream_ref (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id          INTEGER NOT NULL,
    journey_id         INTEGER NOT NULL,
    ref_kind           TEXT NOT NULL CHECK (ref_kind IN
                           ('purpose_element', 'purpose_relation', 'capability_entity')),
    target_ref         TEXT NOT NULL,
    target_row_id      INTEGER,
    captured_digest    TEXT NOT NULL DEFAULT '',
    captured_session_id INTEGER,
    note               TEXT NOT NULL DEFAULT '',
    decision_method    TEXT NOT NULL DEFAULT 'manual'
                           CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by         TEXT,
    created_at         REAL NOT NULL,
    superseded_by_id   INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_journey_upstream_ref (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_ux_journey_upstream_ref_journey
    ON ux_journey_upstream_ref (journey_id, id DESC);
"""


def _downgrade_upstream_ref_table(conn):
    """Put `ux_journey_upstream_ref` back into its pre-#427 shape.

    There is no other way to test the migration: `init_db()` has already
    run by the time any fixture exists, so the widened table is what a test
    would otherwise find. Rebuilding the legacy shape here is what lets the
    next `init_db()` call exercise the real migration path.
    """
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS ux_journey_upstream_ref;
        DROP INDEX IF EXISTS idx_ux_journey_upstream_ref_journey;
        """
    )
    conn.executescript(_LEGACY_UPSTREAM_REF_DDL)
    conn.executescript("PRAGMA foreign_keys = ON;")


# ---------------------------------------------------------------------------
# 1. init_db() is re-runnable
# ---------------------------------------------------------------------------


class TestInitDbIsRerunnable:
    def test_init_db_twice_on_a_fresh_database_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "fresh.db"))
        from app.db import get_conn, init_db

        init_db()
        with get_conn() as conn:
            first = sorted(
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        init_db()
        with get_conn() as conn:
            second = sorted(
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        assert first == second
        # All 23 of this Epic's tables exist after the first run.
        product_tables = [name for name in first if name.startswith("product_")]
        assert len(product_tables) == 23, product_tables

    def test_init_db_on_a_populated_database_changes_no_rows(self, admin_client, tmp_path):
        from app.db import get_conn, init_db

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "Migration Populated")
        headers = _headers(token, system_id)
        admin_client.post("/product-objectives", json={"objective_key": "obj-a"}, headers=headers)
        admin_client.post(
            "/product-objectives/obj-a/revisions",
            json={"title": "A", "intent": "i", "contribution": "c", "scope_note": "", "summary": ""},
            headers=headers,
        )

        with get_conn() as conn:
            before = {
                table: _dump(conn, table)
                for table in ("product_objective", "product_objective_revision", "systems")
            }
        init_db()
        with get_conn() as conn:
            after = {
                table: _dump(conn, table)
                for table in ("product_objective", "product_objective_revision", "systems")
            }
        assert before == after


# ---------------------------------------------------------------------------
# 2. The ux_journey_upstream_ref widening (§7.1 / §11)
# ---------------------------------------------------------------------------


class TestUpstreamRefWidening:
    """The single existing table this Epic changes.

    A vocabulary widening must be exactly that: every row keeps its
    `ref_kind`, and no row is dropped. That is the property that cannot be
    checked by reading the migration -- only by running it over real rows.
    """

    def _seed_journey_with_legacy_refs(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "Migration Widening")
        headers = _headers(token, system_id)
        repo, sha = _init_repo(tmp_path, "widening-repo")
        _insert_snapshot(system_id, repo, sha)

        r = admin_client.post(
            "/ux-design/journeys",
            json={"journey_key": "legacy", "perspective": "as_is", "baseline_mode": "undecided"},
            headers=headers,
        )
        assert r.status_code in (200, 201), r.text
        journey_id = r.json()["id"]

        with get_conn() as conn:
            _downgrade_upstream_ref_table(conn)
            now = time.time()
            for kind, ref in (
                ("purpose_element", "core_capability:abc123"),
                ("purpose_relation", "supports:a->b"),
                ("capability_entity", "7"),
            ):
                conn.execute(
                    """INSERT INTO ux_journey_upstream_ref
                           (system_id, journey_id, ref_kind, target_ref, captured_digest,
                            note, decision_method, created_by, created_at)
                       VALUES (?, ?, ?, ?, 'd0', 'n', 'manual', 'root', ?)""",
                    (system_id, journey_id, kind, ref, now),
                )
        return system_id, headers

    def test_widening_preserves_every_row_and_every_ref_kind(self, admin_client, tmp_path):
        from app.db import get_conn, init_db

        self._seed_journey_with_legacy_refs(admin_client, tmp_path)

        with get_conn() as conn:
            before = _dump(conn, "ux_journey_upstream_ref")
            legacy_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'ux_journey_upstream_ref'"
            ).fetchone()["sql"]
        assert "'product_gap'" not in legacy_sql, "fixture failed to reach the pre-#427 shape"
        assert len(before) == 3

        init_db()

        with get_conn() as conn:
            after = _dump(conn, "ux_journey_upstream_ref")
            widened_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'ux_journey_upstream_ref'"
            ).fetchone()["sql"]

        # Byte-for-byte, not merely "the same count".
        assert after == before
        assert [row["ref_kind"] for row in after] == [
            "purpose_element",
            "purpose_relation",
            "capability_entity",
        ]
        for value in ("product_objective", "product_milestone", "product_gap"):
            assert f"'{value}'" in widened_sql

    def test_widening_is_idempotent(self, admin_client, tmp_path):
        from app.db import get_conn, init_db

        self._seed_journey_with_legacy_refs(admin_client, tmp_path)
        init_db()
        with get_conn() as conn:
            once = _dump(conn, "ux_journey_upstream_ref")
        init_db()
        init_db()
        with get_conn() as conn:
            thrice = _dump(conn, "ux_journey_upstream_ref")
        assert thrice == once

    def test_the_rebuilt_table_keeps_its_index(self, admin_client, tmp_path):
        """A rename carries indexes with it, so their NAMES stay taken and a
        later `CREATE INDEX IF NOT EXISTS` silently does nothing. The
        migration drops them first; this asserts it actually worked, since
        the failure mode is a silently unindexed table."""
        from app.db import get_conn, init_db

        self._seed_journey_with_legacy_refs(admin_client, tmp_path)
        init_db()
        with get_conn() as conn:
            indexes = {
                r["name"]
                for r in conn.execute("PRAGMA index_list(ux_journey_upstream_ref)")
            }
        assert "idx_ux_journey_upstream_ref_journey" in indexes


# ---------------------------------------------------------------------------
# 3. Graceful empty state -- the Objective layer is OPTIONAL
# ---------------------------------------------------------------------------


class TestSystemWithoutObjectivesKeepsWorking:
    """§0-1 / §11: a System that never adopts this layer is not "missing"
    anything, and must not be told that it is."""

    def _bare_system(self, admin_client, tmp_path, name):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, name)
        headers = _headers(token, system_id)
        repo, sha = _init_repo(tmp_path, name.replace(" ", "-").lower())
        _insert_snapshot(system_id, repo, sha)
        return headers

    def test_overview_reports_no_objective_rather_than_a_degraded_section(
        self, admin_client, tmp_path
    ):
        headers = self._bare_system(admin_client, tmp_path, "Bare Overview")
        r = admin_client.get("/overview", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        objective = body.get("objective")
        assert objective is not None
        # "none exists yet" is not one of an Objective's states, and it is
        # not a read failure either (§11).
        assert objective["objective_state"] is None
        # §9.3's table is FIRST MATCH, and an unconfirmed Vision (row 2)
        # sits above "no Objective exists" (row 3) on purpose: there is no
        # point asking someone to write an intermediate goal before the
        # goal it is meant to serve is settled. A bare System therefore
        # answers `confirm_vision`, not `create_objective`.
        assert objective["next_step"] == "confirm_vision"
        # The distinction §11 actually protects: "none exists yet" is not a
        # read failure.
        assert objective["next_step_state"] != "unavailable"
        assert "objective" not in body.get("degraded_sections", [])

    def test_objective_map_and_gap_workbench_render_empty(self, admin_client, tmp_path):
        headers = self._bare_system(admin_client, tmp_path, "Bare Map")
        for path in ("/objective-map", "/gap-workbench"):
            r = admin_client.get(path, headers=headers)
            assert r.status_code == 200, f"{path}: {r.text}"
            assert r.json().get("degraded_sections") == []

    def test_functional_lineage_emits_no_product_objective_gap_codes(
        self, admin_client, tmp_path
    ):
        headers = self._bare_system(admin_client, tmp_path, "Bare Lineage")
        r = admin_client.get("/functional-lineage", headers=headers)
        assert r.status_code == 200, r.text
        codes = {gap["code"] for gap in r.json().get("gaps", [])}
        product_codes = {
            "objective_without_vision_ref",
            "objective_without_milestone",
            "milestone_without_gap",
            "milestone_without_verification",
            "gap_without_journey",
            "gap_source_unresolved",
            "gap_source_unavailable",
            "gap_source_contradicted",
            "requirement_without_feature",
            "feature_without_implementation_target",
            "feature_without_capability",
        }
        assert codes & product_codes == set(), sorted(codes & product_codes)


# ---------------------------------------------------------------------------
# 4. Backward compatibility for the pre-existing Journey ref kinds
# ---------------------------------------------------------------------------


class TestExistingJourneyRefsAreUnchanged:
    def test_purpose_and_capability_refs_still_read_and_write(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "Compat Journey")
        headers = _headers(token, system_id)
        repo, sha = _init_repo(tmp_path, "compat-repo")
        _insert_snapshot(system_id, repo, sha)

        admin_client.post(
            "/ux-design/journeys",
            json={"journey_key": "compat", "perspective": "as_is", "baseline_mode": "undecided"},
            headers=headers,
        )

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO understanding_capability_entity
                       (system_id, entity_kind, created_at)
                   VALUES (?, 'core_capability', ?)""",
                (system_id, time.time()),
            )
            capability_id = cur.lastrowid

        for kind, ref in (
            ("purpose_element", "core_capability:deadbeef"),
            ("capability_entity", str(capability_id)),
        ):
            r = admin_client.post(
                "/ux-design/journeys/compat/upstream-refs",
                json={"ref_kind": kind, "target_ref": ref, "note": ""},
                headers=headers,
            )
            assert r.status_code in (200, 201), f"{kind}: {r.text}"

        detail = admin_client.get("/ux-design/journeys/compat", headers=headers).json()
        kinds = {ref["ref_kind"] for ref in detail["upstream_refs"]}
        assert kinds == {"purpose_element", "capability_entity"}


# ---------------------------------------------------------------------------
# 5. feature_drafts keeps its snapshot lineage (§1.6)
# ---------------------------------------------------------------------------


class TestFeatureDraftLineage:
    def test_draft_link_degrades_while_the_feature_survives(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "Draft Lineage")
        headers = _headers(token, system_id)
        repo, sha = _init_repo(tmp_path, "draft-repo")
        snapshot_id = _insert_snapshot(system_id, repo, sha)

        with get_conn() as conn:
            run = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method, status,
                        started_at, completed_at)
                   VALUES (?, ?, 'repository_drafts', 'mock', 'mock', 'v1', 'v1',
                           'reasoning_llm', 'succeeded', ?, ?)""",
                (system_id, snapshot_id, time.time(), time.time()),
            ).lastrowid
            draft_id = conn.execute(
                """INSERT INTO feature_drafts
                       (system_id, intelligence_run_id, snapshot_id, feature_id,
                        name, summary, user_value, created_at)
                   VALUES (?, ?, ?, 'feature-checkout', 'Checkout', 's', 'v', ?)""",
                (system_id, run, snapshot_id, time.time()),
            ).lastrowid

        admin_client.post("/product-features", json={"feature_key": "checkout"}, headers=headers)
        admin_client.post(
            "/product-features/checkout/revisions",
            json={"title": "v1", "statement": "first", "rationale": "", "scope_note": "", "summary": ""},
            headers=headers,
        )
        r = admin_client.post(
            "/product-features/checkout/draft-links",
            json={"feature_draft_id": draft_id, "note": ""},
            headers=headers,
        )
        assert r.status_code in (200, 201), r.text
        assert r.json()["target_resolution"] == "resolved"

        # The snapshot is rebuilt; feature_drafts cascades away with it.
        with get_conn() as conn:
            conn.execute("DELETE FROM repository_snapshots WHERE id = ?", (snapshot_id,))

        detail = admin_client.get("/product-features/checkout", headers=headers).json()
        # The Feature's own identity and revision history are untouched...
        assert detail["feature_key"] == "checkout"
        assert detail["current_revision"]["title"] == "v1"
        # ...and the link degrades honestly rather than pointing at a
        # different draft (§1.6).
        link = detail["draft_links"][0]
        assert link["target_resolution"] == "unresolved"
        assert link["feature_draft_id"] is None
        assert link["feature_draft_ref"] == "feature-checkout"


# ---------------------------------------------------------------------------
# 6. cell_goals is a different feature and is never touched (§1.4)
# ---------------------------------------------------------------------------


class TestCellGoalsAreNotMigrated:
    def test_creating_objectives_leaves_cell_goals_alone(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "Cell Goals Untouched")
        headers = _headers(token, system_id)

        with get_conn() as conn:
            before = _dump(conn, "cell_goals")

        admin_client.post("/product-objectives", json={"objective_key": "obj-x"}, headers=headers)
        admin_client.post(
            "/product-objectives/obj-x/revisions",
            json={"title": "X", "intent": "i", "contribution": "c", "scope_note": "", "summary": ""},
            headers=headers,
        )

        with get_conn() as conn:
            after = _dump(conn, "cell_goals")
        assert after == before
