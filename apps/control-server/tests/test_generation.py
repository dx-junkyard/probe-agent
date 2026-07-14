"""Regression tests for the candidate generation API (`POST /generation-runs`).

Written as a prerequisite of Issue #244 (Replay engine, Phase B of #242):
these pin the CURRENT observable behavior of the candidate execution path
before it is migrated onto the shared replay harness
(``app/replay_harness.py``), and must pass unchanged before and after that
refactor.

Pinned behavior:
  * happy path via the deterministic mock LLM provider (candidate code is
    executed in a subprocess and its repr output stored);
  * candidate execution error (a Python traceback string is stored in
    ``execution_error``);
  * the timeout message ("candidate execution timed out");
  * the restricted SAFE_BUILTINS jail (``open`` is not available);
  * non-callable ``candidate`` produces the RuntimeError message;
  * repr inputs that fail ``ast.literal_eval`` fall back to the raw string
    (legacy generation semantics — distinct from the replay engine's strict
    ``repr_parse_failed`` skip);
  * verdict normalization to the finite verdict set.
"""

import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-generation-test.db"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.delenv("CONTROL_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("CONTROL_ADMIN_PASSWORD", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_llm_client.cache_clear()


def _post_trace(
    client,
    trace_id="g1",
    component_id="gen-comp",
    input_value=None,
    output="'HELLO'",
    error=None,
):
    event = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": (
            input_value
            if input_value is not None
            else {"args": ["'hello'"], "kwargs": {}}
        ),
        "output": output,
        "error": error,
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }
    response = client.post("/traces", json=event)
    assert response.status_code == 201, response.text


def _create_run(client, trace_id="g1", component_id="gen-comp"):
    return client.post(
        "/generation-runs",
        json={
            "component_id": component_id,
            "trace_id": trace_id,
            "objective": "improve the output",
        },
    )


class _StubLLM:
    """Stub transport: fixed generation/evaluation responses.

    Mocks the LLM transport, not the business decision — the endpoint's own
    parsing, execution, and normalization logic stays fully exercised.
    """

    def __init__(self, generated_code=None, verdict="better"):
        self.generated_code = generated_code or (
            "def candidate(*args, **kwargs):\n"
            "    text = args[0] if args else ''\n"
            "    return str(text).upper()\n"
        )
        self.verdict = verdict

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        joined = "\n".join(m.get("content", "") for m in messages)
        if "EVALUATION_RESPONSE_JSON" in joined:
            return json.dumps(
                {
                    "verdict": self.verdict,
                    "reason": "stub reason",
                    "risks": "stub risks",
                    "recommendation": "stub recommendation",
                }
            )
        return json.dumps(
            {"generated_code": self.generated_code, "notes": "stub notes"}
        )


def test_happy_path_with_mock_provider(client):
    _post_trace(client)
    response = _create_run(client)
    assert response.status_code == 201, response.text
    run = response.json()
    assert "def candidate" in run["generated_code"]
    # The mock candidate uppercases the first positional argument; the stored
    # repr input "'hello'" is literal-eval'd back to the string 'hello'.
    assert run["candidate_output"] == "'HELLO'"
    assert run["execution_error"] is None
    assert run["llm_verdict"] == "better"
    assert run["current_output"] == "'HELLO'"

    fetched = client.get(f"/generation-runs/{run['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["candidate_output"] == "'HELLO'"

    listing = client.get("/generation-runs", params={"component_id": "gen-comp"})
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [run["id"]]


def test_execution_error_stores_traceback(client, monkeypatch):
    _post_trace(client)
    # NOTE: exception classes are not part of SAFE_BUILTINS, so the failure
    # must be interpreter-raised (a `raise ValueError(...)` would itself fail
    # with NameError inside the jail — also pinned behavior).
    stub = _StubLLM(
        generated_code=(
            "def candidate(*args, **kwargs):\n"
            "    return 1 // 0\n"
        )
    )
    monkeypatch.setattr("app.routes.generation.get_llm_client", lambda: stub)
    response = _create_run(client)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["candidate_output"] is None
    assert "Traceback (most recent call last):" in run["execution_error"]
    assert "ZeroDivisionError" in run["execution_error"]
    # Evaluation still runs and is stored even when execution failed.
    assert run["llm_verdict"] == "better"


def test_timeout_produces_fixed_message(client, monkeypatch):
    _post_trace(client)
    monkeypatch.setattr(
        "app.routes.generation.get_llm_client", lambda: _StubLLM()
    )

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="python", timeout=5)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    response = _create_run(client)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["candidate_output"] is None
    assert run["execution_error"] == "candidate execution timed out"


def test_safe_builtins_jail_blocks_open(client, monkeypatch):
    _post_trace(client)
    stub = _StubLLM(
        generated_code=(
            "def candidate(*args, **kwargs):\n"
            "    return open('x.txt', 'w')\n"
        )
    )
    monkeypatch.setattr("app.routes.generation.get_llm_client", lambda: stub)
    run = _create_run(client).json()
    assert run["candidate_output"] is None
    assert "open" in run["execution_error"]
    assert "is not defined" in run["execution_error"]


def test_non_callable_candidate_reports_runtime_error(client, monkeypatch):
    _post_trace(client)
    stub = _StubLLM(
        generated_code=(
            "def candidate_maker():\n"
            "    return 1\n"
            "candidate = candidate_maker()\n"
        )
    )
    monkeypatch.setattr("app.routes.generation.get_llm_client", lambda: stub)
    run = _create_run(client).json()
    assert run["candidate_output"] is None
    assert "generated code must define callable candidate" in run["execution_error"]


def test_code_without_candidate_def_is_rejected(client, monkeypatch):
    _post_trace(client)
    stub = _StubLLM(generated_code="print(1)\n")
    monkeypatch.setattr("app.routes.generation.get_llm_client", lambda: stub)
    response = _create_run(client)
    assert response.status_code == 502
    assert "did not define candidate" in response.json()["detail"]


def test_unparseable_repr_falls_back_to_raw_string(client):
    # Legacy generation semantics: a repr string that ast.literal_eval cannot
    # parse is passed through verbatim (the replay engine instead skips such
    # cases with reason repr_parse_failed).
    _post_trace(
        client,
        trace_id="g-raw",
        input_value={"args": ["<raw object>"], "kwargs": {}},
    )
    run = _create_run(client, trace_id="g-raw").json()
    assert run["candidate_output"] == "'<RAW OBJECT>'"
    assert run["execution_error"] is None


def test_non_dict_trace_input_becomes_single_argument(client):
    _post_trace(client, trace_id="g-plain", input_value="plain")
    run = _create_run(client, trace_id="g-plain").json()
    assert run["candidate_output"] == "'PLAIN'"
    assert run["execution_error"] is None


def test_kwargs_are_restored_from_reprs(client, monkeypatch):
    stub_input = {"args": [], "kwargs": {"text": "'mixed Case'"}}
    _post_trace(client, trace_id="g-kw", input_value=stub_input)
    code = (
        "def candidate(*args, **kwargs):\n"
        "    return str(kwargs.get('text', '')).upper()\n"
    )
    monkeypatch.setattr(
        "app.routes.generation.get_llm_client",
        lambda: _StubLLM(generated_code=code),
    )
    run = _create_run(client, trace_id="g-kw").json()
    assert run["candidate_output"] == "'MIXED CASE'"
    assert run["execution_error"] is None


@pytest.mark.parametrize(
    "raw_verdict,expected",
    [
        ("better", "better"),
        ("Worse", "worse"),
        ("SAME", "same"),
        ("unsafe", "unsafe"),
        ("error", "error"),
        ("AMAZING", "unknown"),
        ("", "unknown"),
    ],
)
def test_verdict_normalization(client, monkeypatch, raw_verdict, expected):
    _post_trace(client, trace_id=f"g-v-{raw_verdict or 'empty'}")
    monkeypatch.setattr(
        "app.routes.generation.get_llm_client",
        lambda: _StubLLM(verdict=raw_verdict),
    )
    run = _create_run(client, trace_id=f"g-v-{raw_verdict or 'empty'}").json()
    assert run["llm_verdict"] == expected


def test_missing_trace_returns_404(client):
    response = _create_run(client, trace_id="does-not-exist")
    assert response.status_code == 404
