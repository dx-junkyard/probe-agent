"""Regression: a table rebuild must not leave child tables referencing the
dropped `_legacy` table.

SQLite's default ``ALTER TABLE x RENAME TO x_legacy`` rewrites the stored
foreign-key clause of every OTHER table that references ``x`` so it follows
the table to its new name. A rebuild migration (rename -> recreate -> copy ->
drop) therefore leaves every child referencing ``x_legacy`` and then drops it.
Nothing fails at migration time -- the connection runs with
``PRAGMA foreign_keys=ON``, so the damage only surfaces later, as
``sqlite3.OperationalError: no such table: main.x_legacy`` on the CHILD's next
INSERT.

That is exactly how ``_migrate_assistant_discussion_thread_target_kinds``
(Issue #453) made ``POST /assistant/ask`` return 500: nine tables were left
pointing at ``assistant_discussion_thread_legacy``.

Two things are verified here, because fixing only one leaves real databases
broken:

* the rebuild migrations no longer cause the damage (``PRAGMA
  legacy_alter_table = ON`` for the rename alone), and
* ``_repair_dangling_foreign_key_targets`` heals a database that already ran
  the broken migration -- preserving every row, id, index and trigger, and
  restoring the cascade that the dangling reference had silently disabled.
"""

from __future__ import annotations

import re
import sqlite3

import pytest


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-fk-repair.db"))
    from app import db
    from app.interview_discussion_schema import SCHEMA as interview_discussion_schema

    with db.get_conn() as c:
        c.executescript(db.SCHEMA)
        c.executescript(interview_discussion_schema)
        c.execute(
            "INSERT INTO systems (id, name, created_at, updated_at) VALUES (1, 's', 0, 0)"
        )
        yield c


def _tables(conn):
    return {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _dangling(conn):
    """Every (table, missing FK target) pair the schema currently declares."""
    existing = _tables(conn)
    found = set()
    for table in existing:
        for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
            if fk["table"] not in existing:
                found.add((table, fk["table"]))
    return found


def _break_assistant_discussion_thread(conn):
    """Rebuild the table the way the broken migration did (default rename)."""
    from app import db

    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        ALTER TABLE assistant_discussion_thread
            RENAME TO assistant_discussion_thread_legacy;
        DROP INDEX IF EXISTS idx_assistant_discussion_thread_system;
        """
    )
    conn.executescript(db._ASSISTANT_DISCUSSION_DDL)
    conn.executescript(
        """
        DROP TABLE assistant_discussion_thread_legacy;
        PRAGMA foreign_keys = ON;
        """
    )


def _insert_thread(conn, thread_id=1, target_kind="product_feature", key="k"):
    conn.execute(
        """INSERT INTO assistant_discussion_thread
           (id, system_id, thread_key, scope, screen_id, target_kind, target_ref,
            target_title, captured_target_revision_id, captured_target_digest,
            status, created_by, created_at, updated_at, schema_version)
           VALUES (?, 1, ?, 'entity', 'interview', ?, 'r', 't', NULL, 'd',
                   'open', 'u', 0, 0, 'v1')""",
        (thread_id, key, target_kind),
    )


def _insert_turn(conn, thread_id=1, turn_number=1):
    conn.execute(
        """INSERT INTO assistant_discussion_turn
           (system_id, thread_id, turn_number, role, content, citations_json,
            target_digest, used_fallback, decision_method, input_mode, provider,
            model, prompt_version, created_at, schema_version)
           VALUES (1, ?, ?, 'user', 'hi', '[]', '', 0, 'manual', 'text', '', '',
                   '', 0, 'v1')""",
        (thread_id, turn_number),
    )


def test_default_rename_reproduces_the_reported_500(conn):
    """The exact failure from the bug report, so the repair below is not
    verified against a hypothetical."""
    _break_assistant_discussion_thread(conn)
    _insert_thread(conn)

    with pytest.raises(sqlite3.OperationalError) as excinfo:
        _insert_turn(conn)
    assert "no such table: main.assistant_discussion_thread_legacy" in str(excinfo.value)


def test_repair_heals_every_damaged_child_and_preserves_data(conn):
    from app import db

    _break_assistant_discussion_thread(conn)
    _insert_thread(conn)
    damaged = {table for table, target in _dangling(conn)}
    # The nine tables that actually carry the FK -- asserted as a non-trivial
    # set so a repair that fixed only the one in the traceback would fail.
    assert "assistant_discussion_turn" in damaged
    assert "joint_understanding_session" in damaged
    assert "interview_discussion_turn_request" in damaged
    assert len(damaged) >= 9

    indexes_before = {
        table: sorted(
            r["name"] for r in conn.execute(f'PRAGMA index_list("{table}")')
        )
        for table in damaged
    }

    db._repair_dangling_foreign_key_targets(conn)

    assert _dangling(conn) == set()
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    for table, names in indexes_before.items():
        assert sorted(
            r["name"] for r in conn.execute(f'PRAGMA index_list("{table}")')
        ) == names, table

    # The child INSERT that raised is now accepted, and the cascade the
    # dangling reference had silently disabled works again.
    _insert_turn(conn)
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM assistant_discussion_turn"
    ).fetchone()["c"] == 1
    conn.execute("DELETE FROM assistant_discussion_thread WHERE id = 1")
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM assistant_discussion_turn"
    ).fetchone()["c"] == 0


def test_repair_is_idempotent_and_a_no_op_on_a_healthy_database(conn):
    from app import db

    before = {
        row["name"]: row["sql"]
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
    }
    db._repair_dangling_foreign_key_targets(conn)
    after = {
        row["name"]: row["sql"]
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
    }
    assert after == before

    _break_assistant_discussion_thread(conn)
    db._repair_dangling_foreign_key_targets(conn)
    healed = {
        row["name"]: row["sql"]
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
    }
    db._repair_dangling_foreign_key_targets(conn)
    assert {
        row["name"]: row["sql"]
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
    } == healed
    assert _dangling(conn) == set()


def test_repair_never_invents_a_target_it_cannot_resolve(conn):
    """A dangling target that maps to no existing table is left alone.

    An unrepairable reference is a fact worth keeping visible; guessing a
    substitute would silently re-point a foreign key at the wrong rows.
    """
    from app import db

    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        CREATE TABLE fk_orphan (
            id INTEGER PRIMARY KEY,
            other_id INTEGER REFERENCES nothing_here_legacy (id)
        );
        PRAGMA foreign_keys = ON;
        """
    )
    before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'fk_orphan'"
    ).fetchone()["sql"]

    db._repair_dangling_foreign_key_targets(conn)

    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'fk_orphan'"
    ).fetchone()["sql"] == before
    assert ("fk_orphan", "nothing_here_legacy") in _dangling(conn)


def test_rewrite_only_touches_the_identifier_after_references():
    """A column or table whose NAME merely contains the dangling name is not
    rewritten -- the repair edits foreign-key targets, not text."""
    from app import db

    sql = (
        'CREATE TABLE t (\n'
        '  thing_legacy_note TEXT,\n'
        '  a INTEGER REFERENCES "thing_legacy" (id),\n'
        '  b INTEGER REFERENCES thing_legacy (id) ON DELETE CASCADE,\n'
        '  c INTEGER REFERENCES thing_legacy_other (id)\n'
        ')'
    )
    out = db._rewrite_references(sql, "thing_legacy", "thing")

    assert 'REFERENCES "thing" (id),' in out
    assert 'REFERENCES "thing" (id) ON DELETE CASCADE' in out
    assert "thing_legacy_note TEXT" in out
    assert "REFERENCES thing_legacy_other (id)" in out


@pytest.mark.parametrize(
    "dangling, base",
    [
        ("widget_legacy", "widget"),
        ("_old_widget", "widget"),
        ("_widget_old", "widget"),
    ],
)
def test_wrapper_forms_map_back_to_their_base_table(dangling, base):
    """The finite, explicitly enumerated set of wrapper names db.py's rebuilds
    have ever produced (Principle 6) -- not a pattern that would match any
    table whose name happens to end in a suffix."""
    from app import db

    assert base in db._legacy_wrapper_base(dangling)
    assert db._legacy_wrapper_base("unrelated_table") == []


def test_fixed_rebuild_migration_leaves_no_dangling_reference(conn):
    """``_migrate_assistant_discussion_thread_target_kinds`` on a pre-Issue
    #453 database: widens the vocabulary, keeps every row and its index, and
    leaves every child table pointing at the real table."""
    from app import db

    # Downgrade to the pre-#453 shape so the migration's structural detection
    # (`'product_feature' in sql`) actually fires.
    narrowed = re.sub(
        r"(CHECK \(target_kind IN \()(.*?)(\)\))",
        r"\1'ux_journey'\3",
        db._ASSISTANT_DISCUSSION_DDL,
        count=1,
        flags=re.S,
    )
    assert "'product_feature'" not in narrowed.split("CREATE TABLE IF NOT EXISTS assistant_discussion_turn")[0]
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        PRAGMA legacy_alter_table = ON;
        ALTER TABLE assistant_discussion_thread RENAME TO _pre453;
        PRAGMA legacy_alter_table = OFF;
        DROP INDEX IF EXISTS idx_assistant_discussion_thread_system;
        """
    )
    conn.executescript(narrowed)
    conn.executescript("DROP TABLE _pre453; PRAGMA foreign_keys = ON;")
    _insert_thread(conn, thread_id=7, target_kind="ux_journey")

    db._migrate_assistant_discussion_thread_target_kinds(conn)

    assert _dangling(conn) == set()
    row = conn.execute(
        "SELECT id, target_kind FROM assistant_discussion_thread"
    ).fetchone()
    assert (row["id"], row["target_kind"]) == (7, "ux_journey")
    assert "idx_assistant_discussion_thread_system" in {
        r["name"] for r in conn.execute("PRAGMA index_list(assistant_discussion_thread)")
    }
    # The widening the migration exists for, and a child INSERT that would
    # have raised before the fix.
    _insert_thread(conn, thread_id=8, target_kind="product_feature", key="k2")
    _insert_turn(conn, thread_id=7)
