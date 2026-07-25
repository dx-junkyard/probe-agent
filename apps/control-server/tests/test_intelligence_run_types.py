"""Finite-vocabulary contract for ``intelligence_runs.run_type`` (Issue #315)."""

import re
import sqlite3
import time
from pathlib import Path

import pytest

from app import db
from app.intelligence_run_types import (
    INTELLIGENCE_RUN_TYPES,
    IntelligenceRunType,
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PROBE_DB_PATH", str(tmp_path / "intelligence-run-types.db")
    )
    db.init_db()
    with db.get_conn() as connection:
        now = time.time()
        system_id = connection.execute(
            """INSERT INTO systems
                   (name, environment, description, created_at, updated_at)
               VALUES ('run-type-contract', 'test', '', ?, ?)""",
            (now, now),
        ).lastrowid
        yield connection, system_id


def _insert_run(connection, system_id: int, run_type: str) -> int:
    now = time.time()
    return connection.execute(
        """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model,
                prompt_version, schema_version, decision_method, status,
                is_mock, started_at, completed_at)
           VALUES (?, NULL, ?, 'test', 'test', 'test', 'test',
                   'deterministic', 'completed', 0, ?, ?)""",
        (system_id, run_type, now, now),
    ).lastrowid


def test_every_declared_run_type_is_accepted(conn):
    connection, system_id = conn

    for run_type in IntelligenceRunType:
        _insert_run(connection, system_id, run_type.value)

    persisted = {
        row["run_type"]
        for row in connection.execute(
            "SELECT run_type FROM intelligence_runs WHERE system_id = ?",
            (system_id,),
        )
    }
    assert persisted == INTELLIGENCE_RUN_TYPES


def test_unknown_run_type_insert_is_rejected_without_a_row(conn):
    connection, system_id = conn

    with pytest.raises(
        sqlite3.IntegrityError, match=r"invalid intelligence_runs\.run_type"
    ):
        _insert_run(connection, system_id, "accidental_new_run")

    assert connection.execute(
        "SELECT COUNT(*) FROM intelligence_runs WHERE system_id = ?",
        (system_id,),
    ).fetchone()[0] == 0


def test_run_type_cannot_be_changed_to_an_unknown_value(conn):
    connection, system_id = conn
    run_id = _insert_run(
        connection, system_id, IntelligenceRunType.SYMBOL_INDEX.value
    )

    with pytest.raises(
        sqlite3.IntegrityError, match=r"invalid intelligence_runs\.run_type"
    ):
        connection.execute(
            "UPDATE intelligence_runs SET run_type = ? WHERE id = ?",
            ("accidental_new_run", run_id),
        )

    assert connection.execute(
        "SELECT run_type FROM intelligence_runs WHERE id = ?", (run_id,)
    ).fetchone()["run_type"] == IntelligenceRunType.SYMBOL_INDEX.value


def test_all_literal_production_writers_use_the_authoritative_vocabulary():
    """Catch a newly hard-coded writer value before its runtime path is hit."""

    app_dir = Path(__file__).resolve().parents[1] / "app"
    literal_writer = re.compile(
        r"INSERT\s+INTO\s+intelligence_runs\s*"
        r"\([^)]*\brun_type\b[^)]*\)\s*"
        r"VALUES\s*\([^,]+,[^,]+,\s*'([^']+)'",
        re.IGNORECASE | re.DOTALL,
    )
    found = set()
    for source_path in app_dir.rglob("*.py"):
        found.update(literal_writer.findall(source_path.read_text()))

    # ``analyzer_proposal`` is the sole parameterised production writer:
    # routes/trace_analyzers.py funnels its three callers through _record_run.
    # Every other current value is written as a SQL literal and must be
    # represented here, so the audit detects both unknown writers and stale
    # enum entries.
    assert found == INTELLIGENCE_RUN_TYPES - {
        IntelligenceRunType.ANALYZER_PROPOSAL.value
    }
    trace_analyzer_source = (
        app_dir / "routes" / "trace_analyzers.py"
    ).read_text()
    parameterized_values = set(
        re.findall(
            r"_record_run\(\s*conn,\s*system_id,\s*['\"]([^'\"]+)['\"]",
            trace_analyzer_source,
        )
    )
    assert parameterized_values == {
        IntelligenceRunType.ANALYZER_PROPOSAL.value
    }
