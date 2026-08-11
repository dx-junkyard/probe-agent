"""Issue #367: credentials must not survive trace ingestion.

The Control Server redacts on the *write* path, so these tests assert on what
reaches the database, not only on what the API renders. A presentation-only
mask would leave the plaintext on disk, in every export, and in every
downstream consumer (Replay, Candidate Studio, Workspaces).
"""

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.trace_redaction import redact_trace_payload

# Recognisable canaries: the assertions below check these never appear
# anywhere in the stored row, the API response, or an LLM-bound payload.
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
OPENAI_KEY = "sk-proj-" + "Z" * 24
PLAIN_PASSWORD = "hunter2-canary-passphrase"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "probe-redaction.db"


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _trace(trace_id="t1", **overrides):
    event = {
        "trace_id": trace_id,
        "component_id": "summarizer",
        "mode": "trace",
        "input": {"args": [], "kwargs": {}},
        "output": None,
        "error": None,
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }
    event.update(overrides)
    return event


def _stored(db_path, trace_id="t1"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(
            conn.execute(
                "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        )
    finally:
        conn.close()


def _raw_db_text(db_path) -> str:
    """Every byte of the database file, for a blunt "is it in there" check."""
    return db_path.read_bytes().decode("utf-8", errors="ignore")


# --- storage boundary --------------------------------------------------------


def test_sensitive_kwarg_is_masked_before_storage(client, db_path):
    client.post(
        "/traces",
        json=_trace(input={"args": [], "kwargs": {"api_key": OPENAI_KEY}}),
    )
    row = _stored(db_path)
    assert OPENAI_KEY not in (row["input_json"] or "")
    assert OPENAI_KEY not in _raw_db_text(db_path)
    summary = json.loads(row["redaction_json"])
    assert summary["redacted"] is True
    assert "sensitive_key" in summary["rules"]
    assert summary["fields"][0]["path"] == "kwargs.api_key"


def test_credential_shape_in_a_repr_string_is_masked(client, db_path):
    """The audited leak: a config object's repr, under a harmless arg name."""
    client.post(
        "/traces",
        json=_trace(
            input={
                "args": [f"Config(api_key='{OPENAI_KEY}', endpoint='https://x')"],
                "kwargs": {},
            }
        ),
    )
    row = _stored(db_path)
    assert OPENAI_KEY not in (row["input_json"] or "")
    assert OPENAI_KEY not in _raw_db_text(db_path)


def test_output_and_error_are_scanned(client, db_path):
    client.post(
        "/traces",
        json=_trace(
            output=f"'creds loaded: {AWS_KEY}'",
            error=f"ValueError: bad password={PLAIN_PASSWORD}",
        ),
    )
    row = _stored(db_path)
    assert AWS_KEY not in row["output_text"]
    assert PLAIN_PASSWORD not in row["error"]
    assert AWS_KEY not in _raw_db_text(db_path)
    assert PLAIN_PASSWORD not in _raw_db_text(db_path)


def test_nested_containers_are_traversed(client, db_path):
    client.post(
        "/traces",
        json=_trace(
            input={
                "args": [],
                "kwargs": {
                    "settings": {
                        "outer": [{"inner": {"password": PLAIN_PASSWORD}}],
                        "region": "eu-west-1",
                    }
                },
            }
        ),
    )
    row = _stored(db_path)
    assert PLAIN_PASSWORD not in row["input_json"]
    # Non-sensitive siblings survive: redaction must not blind the reader.
    assert "eu-west-1" in row["input_json"]


def test_clean_trace_still_records_that_the_scan_ran(client, db_path):
    """NULL must keep meaning "never scanned", not "nothing found"."""
    client.post("/traces", json=_trace(input={"args": ["'hello'"], "kwargs": {}}))
    summary = json.loads(_stored(db_path)["redaction_json"])
    assert summary == {"redacted": False, "rules": [], "fields": []}


# --- replay consequences -----------------------------------------------------


def test_redacted_capture_is_downgraded_from_replayable(client, db_path):
    """A capture the server had to mask can no longer restore the real call."""
    client.post(
        "/traces",
        json=_trace(
            input_capture={"args": [], "kwargs": {"api_key": OPENAI_KEY}},
            replayability="replayable",
            replay_reasons=[],
        ),
    )
    row = _stored(db_path)
    assert row["replayability"] == "partial"
    assert "redacted" in json.loads(row["replay_reasons_json"])
    assert OPENAI_KEY not in row["input_capture_json"]


def test_unreplayable_capture_is_not_upgraded(client, db_path):
    client.post(
        "/traces",
        json=_trace(
            input_capture={"args": [], "kwargs": {"api_key": OPENAI_KEY}},
            replayability="unreplayable",
            replay_reasons=["size_limit_exceeded"],
        ),
    )
    assert _stored(db_path)["replayability"] == "unreplayable"


def test_clean_capture_keeps_its_classification(client, db_path):
    client.post(
        "/traces",
        json=_trace(
            input_capture={"args": [1, 2], "kwargs": {"mode": "fast"}},
            replayability="replayable",
            replay_reasons=[],
        ),
    )
    row = _stored(db_path)
    assert row["replayability"] == "replayable"
    assert json.loads(row["replay_reasons_json"]) == []


# --- API surface -------------------------------------------------------------


def test_api_response_carries_redaction_state_and_payload_summary(client):
    client.post(
        "/traces",
        json=_trace(input={"args": [], "kwargs": {"token": OPENAI_KEY}}, output="'ok'"),
    )
    traces = client.get("/components/summarizer/traces").json()
    assert len(traces) == 1
    trace = traces[0]
    assert OPENAI_KEY not in json.dumps(trace)
    assert trace["redaction"]["redacted"] is True
    # The collapsed detail view needs shape facts before anything is expanded.
    summary = trace["payload_summary"]
    assert summary["input"]["kind"] == "object"
    assert summary["input"]["item_count"] == 2
    assert summary["input"]["bytes"] > 0
    assert summary["output"]["kind"] == "string"


def test_clean_trace_reports_a_negative_redaction_verdict(client):
    client.post("/traces", json=_trace(input={"args": ["'hi'"], "kwargs": {}}))
    trace = client.get("/components/summarizer/traces").json()[0]
    assert trace["redaction"]["redacted"] is False


# --- existing data -----------------------------------------------------------


def _insert_legacy_row(db_path, trace_id, input_json, output_text=None):
    """Write a row the way a pre-#367 server would have: never scanned."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO traces
                   (system_id, trace_id, component_id, mode, input_json,
                    output_text, duration_ms, timestamp, replayability,
                    replay_reasons_json, redaction_json)
               VALUES (1, ?, 'summarizer', 'trace', ?, ?, 1.0, ?, NULL, NULL, NULL)""",
            (trace_id, input_json, output_text, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def test_audit_reports_pre_existing_plaintext_without_echoing_it(client, db_path):
    client.post("/traces", json=_trace("clean", input={"args": [], "kwargs": {}}))
    _insert_legacy_row(
        db_path, "legacy", json.dumps({"args": [f"'{AWS_KEY}'"], "kwargs": {}})
    )

    audit = client.get("/traces/redaction-audit").json()
    assert audit["unscanned_rows"] == 1
    assert audit["affected_rows"] == 1
    assert audit["findings"][0]["trace_id"] == "legacy"
    assert "aws_access_key_id" in audit["findings"][0]["rules"]
    # The report identifies the leak; it must not repeat it.
    assert AWS_KEY not in json.dumps(audit)


def test_rescan_rewrites_existing_rows_and_flags_rotation(client, db_path):
    _insert_legacy_row(
        db_path, "legacy", json.dumps({"args": [f"'{AWS_KEY}'"], "kwargs": {}})
    )

    result = client.post("/traces/redaction-rescan").json()
    assert result["rewritten_rows"] == 1
    assert result["rotation_required"] is True
    assert AWS_KEY not in _raw_db_text(db_path)

    # A second pass finds nothing left to do.
    again = client.post("/traces/redaction-rescan").json()
    assert again["scanned_rows"] == 0
    assert again["rewritten_rows"] == 0
    assert client.get("/traces/redaction-audit").json()["unscanned_rows"] == 0


def test_rescan_marks_clean_legacy_rows_as_scanned(client, db_path):
    _insert_legacy_row(
        db_path, "legacy", json.dumps({"args": ["'nothing secret'"], "kwargs": {}})
    )
    result = client.post("/traces/redaction-rescan").json()
    assert result["scanned_rows"] == 1
    assert result["rewritten_rows"] == 0
    assert result["rotation_required"] is False
    assert json.loads(_stored(db_path, "legacy")["redaction_json"])["redacted"] is False


def test_rescan_scans_a_row_whose_input_is_not_valid_json(client, db_path):
    _insert_legacy_row(db_path, "legacy", f"not-json {AWS_KEY}")
    client.post("/traces/redaction-rescan")
    assert AWS_KEY not in _raw_db_text(db_path)


# --- unit-level contract -----------------------------------------------------


def test_redaction_is_idempotent():
    payload = {"args": [f"api_key={OPENAI_KEY}"], "kwargs": {"password": "x"}}
    first = redact_trace_payload(input_value=payload, output=None, error=None)
    second = redact_trace_payload(input_value=first.input, output=None, error=None)
    assert second.input == first.input


def test_redaction_does_not_mutate_its_input():
    payload = {"args": [], "kwargs": {"api_key": OPENAI_KEY}}
    redact_trace_payload(input_value=payload, output=None, error=None)
    assert payload["kwargs"]["api_key"] == OPENAI_KEY


def test_depth_bound_cuts_off_rather_than_trusting_deep_nodes():
    from app.trace_redaction import MAX_DEPTH

    value = {"password": PLAIN_PASSWORD}
    for _ in range(MAX_DEPTH + 5):
        value = {"next": value}
    result = redact_trace_payload(input_value=value, output=None, error=None)
    assert PLAIN_PASSWORD not in json.dumps(result.input)


# --- monitoring summary (Issue #373) -----------------------------------------


def test_trace_summary_reports_deterministic_monitoring_facts(client):
    for index, duration in enumerate([10.0, 20.0, 30.0, 40.0, 1000.0]):
        client.post(
            "/traces",
            json=_trace(f"s{index}", duration_ms=duration,
                        error="Boom" if index == 4 else None),
        )

    body = client.get("/components/summarizer/trace-summary").json()
    assert body["total"] == 5
    assert body["error_count"] == 1
    assert body["error_rate"] == 0.2
    # Nearest-rank: an observed value, never an interpolated one.
    assert body["duration_p50_ms"] == 30.0
    assert body["duration_p95_ms"] == 1000.0
    assert body["duration_max_ms"] == 1000.0
    assert body["last_trace_at"] is not None


def test_trace_summary_distinguishes_no_errors_from_no_data(client):
    empty = client.get("/components/nothing/trace-summary").json()
    assert empty["total"] == 0
    # None, not 0.0: "no errors" and "no data" are different answers.
    assert empty["error_rate"] is None
    assert empty["duration_p50_ms"] is None

    client.post("/traces", json=_trace("ok", duration_ms=5.0))
    populated = client.get("/components/summarizer/trace-summary").json()
    assert populated["error_rate"] == 0.0


def test_trace_summary_counts_replayable_and_redacted(client):
    client.post(
        "/traces",
        json=_trace("a", replayability="replayable", replay_reasons=[],
                    input_capture={"args": [], "kwargs": {}}),
    )
    client.post(
        "/traces",
        json=_trace("b", replayability="partial", replay_reasons=["redacted"],
                    input_capture={"args": [], "kwargs": {}}),
    )
    client.post("/traces", json=_trace("c"))
    client.post(
        "/traces",
        json=_trace("d", input={"args": [], "kwargs": {"api_key": OPENAI_KEY}}),
    )

    body = client.get("/components/summarizer/trace-summary").json()
    assert body["total"] == 4
    # `partial` still produces a comparison, so it counts as replayable.
    assert body["replayable_count"] == 2
    assert body["redacted_count"] == 1


def test_trace_summary_is_system_scoped(client, db_path):
    client.post("/traces", json=_trace("a", duration_ms=1.0))
    _insert_legacy_row(db_path, "other", json.dumps({"args": [], "kwargs": {}}))
    # The legacy helper writes system_id=1, the same system, so the count is 2.
    assert client.get("/components/summarizer/trace-summary").json()["total"] == 2
