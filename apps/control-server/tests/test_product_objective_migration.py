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


# ---------------------------------------------------------------------------
# 6. `ux_journey` is dropped from product_gap_artifact_link.link_kind
# ---------------------------------------------------------------------------


# The pre-§5.11 shape, reproduced verbatim: the only difference from the
# current DDL is the extra `'ux_journey'` member of the CHECK.
_LEGACY_ARTIFACT_LINK_DDL = """
CREATE TABLE product_gap_artifact_link (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id        INTEGER NOT NULL,
    gap_id           INTEGER NOT NULL,
    link_kind        TEXT NOT NULL CHECK (link_kind IN
                         ('issue_draft', 'ux_journey', 'ux_requirement',
                          'product_feature', 'solution_design')),
    target_ref       TEXT NOT NULL,
    target_row_id    INTEGER,
    captured_digest  TEXT NOT NULL DEFAULT '',
    note             TEXT NOT NULL DEFAULT '',
    decision_method  TEXT NOT NULL DEFAULT 'manual'
                         CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by       TEXT,
    created_at       REAL NOT NULL,
    superseded_by_id INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (gap_id) REFERENCES product_gap (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_gap_artifact_link (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_product_gap_artifact_link_system
    ON product_gap_artifact_link (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_product_gap_artifact_link_gap
    ON product_gap_artifact_link (gap_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_product_gap_artifact_current
    ON product_gap_artifact_link (gap_id, link_kind, target_ref)
    WHERE superseded_by_id IS NULL;
"""


def _downgrade_artifact_link_table(conn):
    """Put `product_gap_artifact_link` back into its pre-§5.11 shape, for the
    same reason `_downgrade_upstream_ref_table` exists: `init_db()` has
    already run, so the narrowed table is what a test would otherwise find."""
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS product_gap_artifact_link;
        DROP INDEX IF EXISTS idx_product_gap_artifact_link_system;
        DROP INDEX IF EXISTS idx_product_gap_artifact_link_gap;
        DROP INDEX IF EXISTS ux_product_gap_artifact_current;
        """
    )
    conn.executescript(_LEGACY_ARTIFACT_LINK_DDL)
    conn.executescript("PRAGMA foreign_keys = ON;")


class TestArtifactLinkJourneyKindRemoval:
    """§5.11 narrowed `link_kind` in the schema, the API `Literal` and the
    Dashboard, but `CREATE TABLE IF NOT EXISTS` cannot repair an existing
    table. Without the migration an existing database keeps the old CHECK AND
    its `ux_journey` rows: those rows are outside
    `ProductGapArtifactLinkKind`, so the Gap detail fails response validation
    on them, and the connection they record is invisible to every reader that
    moved to the canonical table. A fresh-database suite sees none of it.
    """

    def _seed(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "Artifact Link Migration")
        headers = _headers(token, system_id)
        repo, sha = _init_repo(tmp_path, "artifact-link-repo")
        _insert_snapshot(system_id, repo, sha)

        assert admin_client.post(
            "/product-objectives", json={"objective_key": "o1"}, headers=headers,
        ).status_code == 201
        assert admin_client.post(
            "/product-milestones",
            json={"objective_key": "o1", "milestone_key": "m1"},
            headers=headers,
        ).status_code == 201
        for gap_key in ("g-resolvable", "g-unresolved", "g-duplicate"):
            assert admin_client.post(
                "/product-gaps",
                json={"milestone_key": "m1", "gap_key": gap_key},
                headers=headers,
            ).status_code == 201

        r = admin_client.post(
            "/ux-design/journeys",
            json={"journey_key": "checkout", "perspective": "to_be", "baseline_mode": "undecided"},
            headers=headers,
        )
        assert r.status_code in (200, 201), r.text
        journey_id = r.json()["id"]
        return token, system_id, headers, journey_id

    def _insert_legacy_rows(self, conn, system_id, journey_id):
        now = time.time()
        gap_ids = {
            r["gap_key"]: r["id"]
            for r in conn.execute(
                "SELECT id, gap_key FROM product_gap WHERE system_id = ?", (system_id,)
            )
        }
        _downgrade_artifact_link_table(conn)

        def _insert(gap_key, link_kind, target_ref):
            return conn.execute(
                """INSERT INTO product_gap_artifact_link
                       (system_id, gap_id, link_kind, target_ref, captured_digest,
                        note, decision_method, created_by, created_at)
                   VALUES (?, ?, ?, ?, 'd0', 'legacy note', 'manual', 'root', ?)""",
                (system_id, gap_ids[gap_key], link_kind, target_ref, now),
            ).lastrowid

        ids = {
            "resolvable": _insert("g-resolvable", "ux_journey", "checkout"),
            "unresolved": _insert("g-unresolved", "ux_journey", "no-such-journey"),
            "duplicate": _insert("g-duplicate", "ux_journey", "checkout"),
            "issue_draft": _insert("g-resolvable", "issue_draft", "42"),
        }
        # The `duplicate` Gap's connection is ALREADY in the canonical table.
        conn.execute(
            """INSERT INTO ux_journey_upstream_ref
                   (system_id, journey_id, ref_kind, target_ref, captured_digest,
                    note, decision_method, created_by, created_at)
               VALUES (?, ?, 'product_gap', 'g-duplicate', '', '', 'manual', 'root', ?)""",
            (system_id, journey_id, now),
        )
        return ids

    def _run(self, admin_client, tmp_path):
        from app.db import get_conn, init_db

        token, system_id, headers, journey_id = self._seed(admin_client, tmp_path)
        with get_conn() as conn:
            ids = self._insert_legacy_rows(conn, system_id, journey_id)
            assert "'ux_journey'" in conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'product_gap_artifact_link'"
            ).fetchone()["sql"]
        init_db()
        return token, system_id, headers, ids

    def test_the_check_is_rebuilt_and_journey_rows_are_gone(self, admin_client, tmp_path):
        from app.db import get_conn

        _token, _system_id, _headers_, _ids = self._run(admin_client, tmp_path)
        with get_conn() as conn:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'product_gap_artifact_link'"
            ).fetchone()["sql"]
            assert "'ux_journey'" not in sql
            kinds = {r["link_kind"] for r in conn.execute("SELECT link_kind FROM product_gap_artifact_link")}
            assert "ux_journey" not in kinds

    def test_a_uniquely_resolvable_row_moves_to_the_canonical_table(self, admin_client, tmp_path):
        from app.db import get_conn

        _token, system_id, _headers_, _ids = self._run(admin_client, tmp_path)
        with get_conn() as conn:
            refs = [
                dict(r)
                for r in conn.execute(
                    """SELECT * FROM ux_journey_upstream_ref
                       WHERE system_id = ? AND ref_kind = 'product_gap' AND target_ref = 'g-resolvable'""",
                    (system_id,),
                )
            ]
        assert len(refs) == 1
        # The developer's own note and authorship travel with the connection:
        # the row records a human decision, and the migration moves it rather
        # than re-authoring it.
        assert refs[0]["note"] == "legacy note"
        assert refs[0]["created_by"] == "root"
        assert refs[0]["decision_method"] == "manual"

    def test_non_journey_rows_survive_with_their_ids(self, admin_client, tmp_path):
        from app.db import get_conn

        _token, _system_id, _headers_, ids = self._run(admin_client, tmp_path)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM product_gap_artifact_link WHERE id = ?", (ids["issue_draft"],)
            ).fetchone()
        assert row is not None
        assert row["link_kind"] == "issue_draft"
        assert row["target_ref"] == "42"
        assert row["note"] == "legacy note"

    def test_unresolved_and_duplicate_rows_are_reported_never_guessed(self, admin_client, tmp_path):
        from app.db import get_conn

        _token, system_id, _headers_, _ids = self._run(admin_client, tmp_path)
        with get_conn() as conn:
            report = {
                r["gap_key"]: dict(r)
                for r in conn.execute(
                    "SELECT * FROM product_gap_artifact_migration_report WHERE system_id = ?",
                    (system_id,),
                )
            }
            # Nothing was invented for the unresolvable key.
            assert conn.execute(
                """SELECT COUNT(*) AS n FROM ux_journey_upstream_ref
                   WHERE system_id = ? AND target_ref = 'g-unresolved'""",
                (system_id,),
            ).fetchone()["n"] == 0
            # And the already-recorded connection was not duplicated.
            assert conn.execute(
                """SELECT COUNT(*) AS n FROM ux_journey_upstream_ref
                   WHERE system_id = ? AND ref_kind = 'product_gap' AND target_ref = 'g-duplicate'""",
                (system_id,),
            ).fetchone()["n"] == 1

        assert report["g-resolvable"]["outcome"] == "moved"
        assert report["g-unresolved"]["outcome"] == "unresolved"
        assert report["g-duplicate"]["outcome"] == "duplicate"
        # The report carries enough to finish the move by hand.
        assert report["g-unresolved"]["target_ref"] == "no-such-journey"
        assert report["g-unresolved"]["note"] == "legacy note"

    def test_the_gap_detail_reads_after_the_upgrade(self, admin_client, tmp_path):
        """The response-validation failure this migration exists to prevent:
        a `ux_journey` row is outside the narrowed `Literal`."""
        _token, _system_id, headers, _ids = self._run(admin_client, tmp_path)
        for gap_key in ("g-resolvable", "g-unresolved", "g-duplicate"):
            r = admin_client.get(f"/product-gaps/{gap_key}", headers=headers)
            assert r.status_code == 200, r.text
            assert all(
                link["link_kind"] != "ux_journey" for link in r.json()["artifact_links"]
            )

    def test_the_migration_is_idempotent(self, admin_client, tmp_path):
        from app.db import get_conn, init_db

        _token, system_id, _headers_, _ids = self._run(admin_client, tmp_path)
        with get_conn() as conn:
            before = _dump(conn, "product_gap_artifact_link")
            report_before = _dump(conn, "product_gap_artifact_migration_report")
        init_db()
        init_db()
        with get_conn() as conn:
            assert _dump(conn, "product_gap_artifact_link") == before
            # A second run must not re-report rows it already moved.
            assert _dump(conn, "product_gap_artifact_migration_report") == report_before

    def test_a_retry_after_a_partial_failure_does_not_duplicate(self, admin_client, tmp_path):
        """The row move happens before the table rebuild, so a process death
        between the two leaves the OLD schema in place and this migration runs
        again. Neither the canonical connection nor the report may accumulate."""
        from app.db import get_conn, init_db

        token, system_id, headers, journey_id = self._seed(admin_client, tmp_path)
        with get_conn() as conn:
            self._insert_legacy_rows(conn, system_id, journey_id)

        # Simulate the interrupted attempt: move the rows, then leave the old
        # table exactly as it was.
        from app.db import _migrate_product_gap_artifact_link_kinds

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM product_gap_artifact_link WHERE link_kind = 'ux_journey' LIMIT 1"
            ).fetchone()
            conn.execute(
                """INSERT INTO ux_journey_upstream_ref
                       (system_id, journey_id, ref_kind, target_ref, captured_digest,
                        note, decision_method, created_by, created_at)
                   VALUES (?, ?, 'product_gap', 'g-resolvable', '', '', 'manual', 'root', ?)""",
                (system_id, journey_id, time.time()),
            )
            assert row is not None
            _ = _migrate_product_gap_artifact_link_kinds  # imported for clarity

        init_db()

        with get_conn() as conn:
            # The connection exists exactly once, not twice.
            assert conn.execute(
                """SELECT COUNT(*) AS n FROM ux_journey_upstream_ref
                   WHERE system_id = ? AND ref_kind = 'product_gap' AND target_ref = 'g-resolvable'""",
                (system_id,),
            ).fetchone()["n"] == 1
            # And the report describes each legacy row once.
            legacy_ids = [
                r["legacy_id"]
                for r in conn.execute(
                    "SELECT legacy_id FROM product_gap_artifact_migration_report WHERE system_id = ?",
                    (system_id,),
                )
            ]
            assert len(legacy_ids) == len(set(legacy_ids))

    def test_a_fresh_database_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "fresh-artifact.db"))
        from app.db import get_conn, init_db

        init_db()
        init_db()
        with get_conn() as conn:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'product_gap_artifact_link'"
            ).fetchone()["sql"]
            assert "'ux_journey'" not in sql
            # The report table is only created when there is something to
            # migrate, so a fresh database never grows one.
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM sqlite_master "
                "WHERE name = 'product_gap_artifact_migration_report'"
            ).fetchone()["n"] == 0
