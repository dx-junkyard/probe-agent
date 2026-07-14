"""Tests for Issue #244 (Replay engine — Phase B of #242).

Covers: the standalone harness (determinism, structured "__probe__" decode
round-trip against the actual SDK encoder, repr literal_eval restoration,
undecodable-input fail-closed skip, per-case isolation, output truncation,
inline-code jail), the replay runner (finite match/mismatch/error/skip
classification matrix, symbol resolution failures, import failure, timeout,
worktree cleanup on success and failure, no-sandbox fail-closed), the human
approval gate (403 without approval, revoke, decision_method='manual', stored
risk context), replay-set creation (manual validation, cap, analyzer-run
source), System isolation for all four new tables, and the additive SQLite
migration.
"""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def unsafe_test_execution(monkeypatch):
    # bwrap is not available in CI; the one fail-closed test below removes
    # this again to assert execution refuses instead of running unsandboxed.
    monkeypatch.setenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", "true")


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "replay-engine-test.db"


@pytest.fixture
def admin_client(tmp_path, db_file, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(db_file))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.setenv(
        "PROBE_REPLAY_WORKSPACE_BASE", str(tmp_path / "replay-workspaces")
    )
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.delenv("PROBE_REPLAY_TIMEOUT_SECONDS", raising=False)
    from app.main import app

    with TestClient(app) as client:
        yield client


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def replay_repo(tmp_path):
    repo = tmp_path / "replay-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "target.py").write_text(
        textwrap.dedent(
            '''\
            import time as _time


            def probe(component_id=None):
                def deco(fn):
                    return fn
                return deco


            @probe(component_id="replay-target")
            def classify(x):
                if x == "boom":
                    raise ValueError("boom")
                return "class-" + str(x)


            @probe(component_id="tuple-comp")
            def roundtrip(*args, **kwargs):
                return (args, sorted(kwargs.items()))


            @probe(component_id="slow-comp")
            def slow(seconds=5):
                _time.sleep(seconds)
                return "done"
            '''
        )
    )
    (repo / "dup.py").write_text(
        textwrap.dedent(
            '''\
            def probe(component_id=None):
                def deco(fn):
                    return fn
                return deco


            @probe(component_id="dup-comp")
            def first(x):
                return x


            @probe(component_id="dup-comp")
            def second(x):
                return x
            '''
        )
    )
    (repo / "broken.py").write_text(
        textwrap.dedent(
            '''\
            import missing_module_for_probe_replay_test


            def probe(component_id=None):
                def deco(fn):
                    return fn
                return deco


            @probe(component_id="broken-comp")
            def broken_entry(x):
                return x
            '''
        )
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _create_system(client, token, name):
    response = client.post(
        "/systems",
        json={"name": name, "environment": "test"},
        headers=_headers(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _prepare(client, repo, name="replay-system"):
    """Login, create a system, pin a snapshot, index symbols."""
    token = _login(client)
    system = _create_system(client, token, name)
    headers = _headers(token, system["id"])
    response = client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": []},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    snapshot = client.post("/repository/snapshots", headers=headers)
    assert snapshot.status_code == 201, snapshot.text
    index = client.post("/repository/symbols/index", headers=headers)
    assert index.status_code == 201, index.text
    return token, system, headers, snapshot.json()


def _post_trace(
    client,
    headers,
    trace_id,
    component_id="replay-target",
    *,
    input_value=None,
    output=None,
    error=None,
    capture="__omit__",
    replayability="__omit__",
    reasons="__omit__",
    projections=None,
):
    event = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": input_value,
        "output": output,
        "error": error,
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }
    if capture != "__omit__":
        event["input_capture"] = capture
    if replayability != "__omit__":
        event["replayability"] = replayability
    if reasons != "__omit__":
        event["replay_reasons"] = reasons
    if projections is not None:
        event["projections"] = projections
    response = client.post("/traces", json=event, headers=headers)
    assert response.status_code == 201, response.text


def _approve(client, headers, component_id="replay-target", reason="test approval"):
    response = client.post(
        f"/components/{component_id}/replay-approval",
        json={"reason": reason},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_set(client, headers, trace_ids, component_id="replay-target", name="set"):
    response = client.post(
        "/replay-sets",
        json={"component_id": component_id, "name": name, "trace_ids": trace_ids},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _capture(args=(), kwargs=None):
    """Build a structured capture with the actual SDK encoder."""
    from probe_agent.replay_capture import capture_input, compile_spec

    capture, replayability, reasons = capture_input(
        tuple(args), dict(kwargs or {}), compile_spec(True)
    )
    return capture, replayability, reasons


def _assert_repo_clean(repo):
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""
    worktrees = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    # Only the primary checkout remains.
    assert worktrees.stdout.count("worktree ") == 1


# ---------------------------------------------------------------------------
# Standalone harness tests (script executed directly)
# ---------------------------------------------------------------------------


def _run_harness(workdir, target, cases, cwd=None, timeout=30):
    from app.replay_harness import HARNESS_SCRIPT

    workdir = str(workdir)
    os.makedirs(workdir, exist_ok=True)
    harness = os.path.join(workdir, "harness.py")
    payload = os.path.join(workdir, "payload.json")
    result = os.path.join(workdir, "result.json")
    with open(harness, "w", encoding="utf-8") as f:
        f.write(HARNESS_SCRIPT)
    with open(payload, "w", encoding="utf-8") as f:
        json.dump({"target": target, "cases": cases}, f)
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    proc = subprocess.run(
        [sys.executable, "-I", harness, payload, result],
        capture_output=True,
        text=True,
        cwd=str(cwd or workdir),
        timeout=timeout,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    with open(result, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def harness_module_dir(tmp_path):
    module_dir = tmp_path / "harness-target"
    module_dir.mkdir()
    (module_dir / "mod.py").write_text(
        textwrap.dedent(
            '''\
            import helper


            def classify(x):
                if x == "boom":
                    raise ValueError("boom")
                return helper.wrap(x)


            def touch(name):
                with open(name, "w") as f:
                    f.write("called")
                return name
            '''
        )
    )
    (module_dir / "helper.py").write_text(
        "def wrap(x):\n    return 'class-' + str(x)\n"
    )
    return module_dir


def test_harness_symbol_target_resolves_intra_repo_imports(
    tmp_path, harness_module_dir
):
    capture, replayability, _ = _capture(args=("a",))
    assert replayability == "replayable"
    result = _run_harness(
        tmp_path / "h1",
        {"kind": "symbol", "path": "mod.py", "qualified_name": "classify"},
        [{"input_kind": "structured", "capture": capture}],
        cwd=harness_module_dir,
    )
    assert result["target_error"] is None
    assert result["harness_version"] == "2"
    case = result["cases"][0]
    assert case["status"] == "ok"
    assert case["output"] == "'class-a'"
    assert case["duration_ms"] >= 0


def test_harness_is_deterministic_across_runs(tmp_path, harness_module_dir):
    capture_ok, _, _ = _capture(args=("a",))
    capture_boom, _, _ = _capture(args=("boom",))
    cases = [
        {"input_kind": "structured", "capture": capture_ok},
        {"input_kind": "structured", "capture": capture_boom},
        {"input_kind": "repr", "args": ["'r'"], "kwargs": {}},
    ]
    target = {"kind": "symbol", "path": "mod.py", "qualified_name": "classify"}
    first = _run_harness(tmp_path / "d1", target, cases, cwd=harness_module_dir)
    second = _run_harness(tmp_path / "d2", target, cases, cwd=harness_module_dir)

    def _strip(run):
        return [
            {k: v for k, v in case.items() if k not in ("duration_ms", "traceback")}
            for case in run["cases"]
        ]

    assert _strip(first) == _strip(second)
    assert [case["status"] for case in first["cases"]] == ["ok", "error", "ok"]


def test_harness_structured_round_trip_of_sdk_encoded_types(tmp_path):
    args = ((1, 2), {3, 4}, frozenset({5}), b"xy", float("nan"))
    kwargs = {"flag": True}
    capture, replayability, reasons = _capture(args=args, kwargs=kwargs)
    assert replayability == "replayable"
    assert reasons == []
    code = (
        "def candidate(*args, **kwargs):\n"
        "    return (args, sorted(kwargs.items()))\n"
    )
    result = _run_harness(
        tmp_path / "h-round",
        {"kind": "inline_code", "code": code},
        [{"input_kind": "structured", "capture": capture}],
    )
    case = result["cases"][0]
    assert case["status"] == "ok"
    expected = repr((args, sorted(kwargs.items())))
    assert case["output"] == expected


def test_harness_undecodable_input_skips_without_calling(
    tmp_path, harness_module_dir
):
    before = sorted(os.listdir(harness_module_dir))
    result = _run_harness(
        tmp_path / "h-undec",
        {"kind": "symbol", "path": "mod.py", "qualified_name": "touch"},
        [
            {
                "input_kind": "structured",
                "capture": {
                    "args": [{"__probe__": "unsupported", "type": "Socket"}],
                    "kwargs": {},
                },
            },
            {
                # Unknown / future marker kinds carry no restorable data
                # either — fail closed the same way.
                "input_kind": "structured",
                "capture": {"args": [{"__probe__": "hologram"}], "kwargs": {}},
            },
        ],
        cwd=harness_module_dir,
    )
    for case in result["cases"]:
        assert case["status"] == "skipped"
        assert case["skip_reason"] == "undecodable_input"
        assert case["output"] is None
    # The target function must never have been called with a sentinel.
    after = sorted(
        name for name in os.listdir(harness_module_dir) if not name.endswith(".pyc")
        and name != "__pycache__"
    )
    assert after == before


def test_harness_repr_inputs_literal_eval_and_parse_failure(
    tmp_path, harness_module_dir
):
    result = _run_harness(
        tmp_path / "h-repr",
        {"kind": "symbol", "path": "mod.py", "qualified_name": "classify"},
        [
            {"input_kind": "repr", "args": ["'ok'"], "kwargs": {}},
            {"input_kind": "repr", "args": ["<not a literal>"], "kwargs": {}},
            {"input_kind": "repr", "args": [], "kwargs": {"x": "'kw'"}},
        ],
        cwd=harness_module_dir,
    )
    ok, bad, kw = result["cases"]
    assert ok["status"] == "ok" and ok["output"] == "'class-ok'"
    assert bad["status"] == "skipped"
    assert bad["skip_reason"] == "repr_parse_failed"
    assert kw["status"] == "ok" and kw["output"] == "'class-kw'"


def test_harness_per_case_exception_isolation(tmp_path, harness_module_dir):
    result = _run_harness(
        tmp_path / "h-iso",
        {"kind": "symbol", "path": "mod.py", "qualified_name": "classify"},
        [
            {"input_kind": "repr", "args": ["'boom'"], "kwargs": {}},
            {"input_kind": "repr", "args": ["'fine'"], "kwargs": {}},
        ],
        cwd=harness_module_dir,
    )
    boom, fine = result["cases"]
    assert boom["status"] == "error"
    assert boom["error"] == "ValueError: boom"
    assert "ValueError: boom" in boom["traceback"]
    assert fine["status"] == "ok"
    assert fine["output"] == "'class-fine'"


def test_harness_output_truncated_at_4000(tmp_path):
    code = "def candidate(n):\n    return 'x' * n\n"
    result = _run_harness(
        tmp_path / "h-trunc",
        {"kind": "inline_code", "code": code},
        [{"input_kind": "values", "args": [5000], "kwargs": {}}],
    )
    case = result["cases"][0]
    assert case["status"] == "ok"
    assert case["output"].endswith("...<truncated>")
    assert len(case["output"]) == 4000 + len("...<truncated>")


def test_harness_inline_code_keeps_safe_builtins_jail(tmp_path):
    code = "def candidate(*args, **kwargs):\n    return open('x.txt', 'w')\n"
    result = _run_harness(
        tmp_path / "h-jail",
        {"kind": "inline_code", "code": code},
        [{"input_kind": "values", "args": [], "kwargs": {}}],
    )
    case = result["cases"][0]
    assert case["status"] == "error"
    assert "open" in case["error"]
    assert "is not defined" in case["error"]


def test_harness_target_resolution_failure_is_run_level(tmp_path):
    result = _run_harness(
        tmp_path / "h-syntax",
        {"kind": "inline_code", "code": "def candidate(:\n"},
        [{"input_kind": "values", "args": [], "kwargs": {}}],
    )
    assert result["target_error"]
    assert "SyntaxError" in result["target_error"]
    assert result["cases"] == []


# ---------------------------------------------------------------------------
# Replay runs (API level)
# ---------------------------------------------------------------------------


RECORDED_BOOM_ERROR = (
    "ValueError: boom\n"
    "Traceback (most recent call last):\n"
    '  File "app.py", line 10, in run\n'
    "ValueError: boom\n"
)


def _post_matrix_traces(client, headers):
    cap_a, _, _ = _capture(args=("a",))
    cap_p, _, _ = _capture(args=("p",))
    cap_b, _, _ = _capture(args=("b",))
    cap_boom, _, _ = _capture(args=("boom",))
    cap_ok, _, _ = _capture(args=("ok",))
    _post_trace(client, headers, "t-match", output="'class-a'",
                capture=cap_a, replayability="replayable", reasons=[])
    _post_trace(client, headers, "t-partial", output="'class-p'",
                capture=cap_p, replayability="partial", reasons=["redacted"])
    _post_trace(client, headers, "t-mismatch", output="'class-OTHER'",
                capture=cap_b, replayability="replayable", reasons=[])
    _post_trace(client, headers, "t-error", output="'class-boom'",
                capture=cap_boom, replayability="replayable", reasons=[])
    _post_trace(client, headers, "t-err-match", output=None,
                error=RECORDED_BOOM_ERROR,
                capture=cap_boom, replayability="replayable", reasons=[])
    _post_trace(client, headers, "t-err-mismatch", output=None,
                error="TypeError: something else\n  trace line\n",
                capture=cap_boom, replayability="replayable", reasons=[])
    _post_trace(client, headers, "t-err-replay-success", output=None,
                error="ValueError: recorded only",
                capture=cap_ok, replayability="replayable", reasons=[])
    _post_trace(client, headers, "t-unreplayable", output="'x'",
                capture=None, replayability="unreplayable",
                reasons=["size_limit_exceeded"])
    _post_trace(client, headers, "t-undec", output="'y'",
                capture={"args": [{"__probe__": "unsupported", "type": "Socket"}],
                         "kwargs": {}},
                replayability="partial", reasons=["unsupported_type"])
    _post_trace(client, headers, "t-legacy", output="'class-legacy'",
                input_value={"args": ["'legacy'"], "kwargs": {}})
    _post_trace(client, headers, "t-repr-bad", output="'whatever'",
                input_value={"args": ["<not a literal>"], "kwargs": {}})
    _post_trace(client, headers, "t-missing", output="'z'",
                input_value={"args": ["'gone'"], "kwargs": {}})
    return [
        "t-match", "t-partial", "t-mismatch", "t-error", "t-err-match",
        "t-err-mismatch", "t-err-replay-success", "t-unreplayable", "t-undec",
        "t-legacy", "t-repr-bad", "t-missing",
    ]


EXPECTED_MATRIX = {
    # trace_id: (case_status, input_source, skip_reason)
    "t-match": ("match", "structured", None),
    "t-partial": ("match", "structured", None),
    "t-mismatch": ("mismatch", "structured", None),
    "t-error": ("error", "structured", None),
    "t-err-match": ("match", "structured", None),
    "t-err-mismatch": ("mismatch", "structured", None),
    "t-err-replay-success": ("mismatch", "structured", None),
    "t-unreplayable": ("skipped", None, "unreplayable_capture"),
    "t-undec": ("skipped", "structured", "undecodable_input"),
    "t-legacy": ("match", "repr_partial", None),
    "t-repr-bad": ("skipped", "repr_partial", "repr_parse_failed"),
    "t-missing": ("skipped", None, "trace_missing"),
}


def test_replay_run_classification_matrix(admin_client, replay_repo, db_file):
    _, _, headers, snapshot = _prepare(admin_client, replay_repo)
    trace_ids = _post_matrix_traces(admin_client, headers)
    replay_set = _create_set(admin_client, headers, trace_ids)
    approval = _approve(admin_client, headers)

    # Simulate a trace pruned after set creation (retention etc.).
    raw = sqlite3.connect(db_file)
    raw.execute("DELETE FROM traces WHERE trace_id = 't-missing'")
    raw.commit()
    raw.close()

    response = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert response.status_code == 201, response.text
    run = response.json()

    assert run["status"] == "completed"
    assert run["error"] is None
    assert run["component_id"] == "replay-target"
    assert run["snapshot_id"] == snapshot["id"]
    assert len(run["commit_sha"]) == 40
    assert run["symbol_path"] == "target.py"
    assert run["symbol_qualified_name"] == "classify"
    assert len(run["trace_set_hash"]) == 64
    assert run["approval_id"] == approval["id"]
    assert run["sandbox_config"]["harness_version"] == "2"
    assert run["sandbox_config"]["network"] is False
    assert run["sandbox_config"]["timeout_seconds"] == 60
    assert "PROBE_ENABLED" in run["sandbox_config"]["env_keys"]
    assert run["cleanup_state"] == "removed"
    assert run["started_at"] is not None and run["completed_at"] is not None

    assert [case["position"] for case in run["cases"]] == list(range(len(trace_ids)))
    observed = {
        case["trace_id"]: (
            case["case_status"],
            case["input_source"],
            case["skip_reason"],
        )
        for case in run["cases"]
    }
    assert observed == EXPECTED_MATRIX
    by_id = {case["trace_id"]: case for case in run["cases"]}
    assert by_id["t-match"]["replay_output"] == "'class-a'"
    assert by_id["t-match"]["recorded_output"] == "'class-a'"
    assert by_id["t-error"]["replay_error"] == "ValueError: boom"
    # Recorded error is persisted as its FIRST LINE (comparison basis).
    assert by_id["t-err-match"]["recorded_error"] == "ValueError: boom"
    assert by_id["t-err-match"]["replay_error"] == "ValueError: boom"
    assert all(case["comparison_mode"] == "repr" for case in run["cases"])
    assert all(case["output_truncated"] is False for case in run["cases"])
    assert run["summary"] == {
        "match": 4, "mismatch": 3, "error": 1, "skipped": 4, "total": 12,
    }

    # Isolated execution left no worktree and no repository change behind.
    assert not os.path.exists(run["workspace_path"])
    _assert_repo_clean(replay_repo)

    # Deterministic: a second run classifies identically.
    second = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    ).json()
    assert second["trace_set_hash"] == run["trace_set_hash"]
    assert {
        case["trace_id"]: case["case_status"] for case in second["cases"]
    } == {case["trace_id"]: case["case_status"] for case in run["cases"]}

    # Fetch endpoints agree.
    fetched = admin_client.get(f"/replay-runs/{run['id']}", headers=headers).json()
    assert fetched["summary"] == run["summary"]
    assert len(fetched["cases"]) == 12
    listing = admin_client.get(
        "/replay-runs",
        params={"replay_set_id": replay_set["id"]},
        headers=headers,
    ).json()
    assert [item["id"] for item in listing] == [second["id"], run["id"]]
    assert listing[0]["cases"] == []  # list view omits cases
    assert listing[0]["summary"]["total"] == 12


def test_replay_run_structured_sdk_types_end_to_end(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    args = ((1, 2), {3, 4}, frozenset({5}), b"xy")
    kwargs = {"flag": True}
    capture, replayability, reasons = _capture(args=args, kwargs=kwargs)
    expected = repr((args, sorted(kwargs.items())))
    _post_trace(
        admin_client, headers, "t-tuple", component_id="tuple-comp",
        output=expected, capture=capture, replayability=replayability,
        reasons=reasons,
    )
    replay_set = _create_set(
        admin_client, headers, ["t-tuple"], component_id="tuple-comp"
    )
    _approve(admin_client, headers, component_id="tuple-comp")
    run = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    ).json()
    assert run["status"] == "completed"
    case = run["cases"][0]
    assert case["case_status"] == "match"
    assert case["replay_output"] == expected


def test_replay_run_symbol_not_resolved_is_409(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t-nosym", component_id="no-symbol-comp",
                input_value={"args": [], "kwargs": {}}, output="'x'")
    replay_set = _create_set(
        admin_client, headers, ["t-nosym"], component_id="no-symbol-comp"
    )
    _approve(admin_client, headers, component_id="no-symbol-comp")
    response = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert response.status_code == 409
    assert "component symbol not resolved" in response.json()["detail"]


def test_replay_run_ambiguous_symbol_is_409_with_candidates(
    admin_client, replay_repo
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t-dup", component_id="dup-comp",
                input_value={"args": [], "kwargs": {}}, output="'x'")
    replay_set = _create_set(
        admin_client, headers, ["t-dup"], component_id="dup-comp"
    )
    _approve(admin_client, headers, component_id="dup-comp")
    response = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "multiple symbols" in detail
    assert "dup.py:first" in detail and "dup.py:second" in detail


def test_replay_run_import_failure_fails_run_with_details(
    admin_client, replay_repo
):
    """A target file importing a missing module is a RUN-level failure (the
    chosen behavior): the run is failed with the harness's resolution error
    recorded, no case results are persisted, and cleanup still happens."""
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    cap, _, _ = _capture(args=("x",))
    _post_trace(admin_client, headers, "t-broken", component_id="broken-comp",
                output="'x'", capture=cap, replayability="replayable", reasons=[])
    replay_set = _create_set(
        admin_client, headers, ["t-broken"], component_id="broken-comp"
    )
    _approve(admin_client, headers, component_id="broken-comp")
    response = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "failed"
    assert "replay target could not be resolved" in run["error"]
    assert "missing_module_for_probe_replay_test" in run["error"]
    assert run["cases"] == []
    assert run["cleanup_state"] == "removed"
    assert not os.path.exists(run["workspace_path"])
    _assert_repo_clean(replay_repo)


def test_replay_run_timeout_fails_run_and_cleans_up(
    admin_client, replay_repo, monkeypatch
):
    monkeypatch.setenv("PROBE_REPLAY_TIMEOUT_SECONDS", "1")
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    cap, _, _ = _capture(args=(5,))
    _post_trace(admin_client, headers, "t-slow", component_id="slow-comp",
                output="'done'", capture=cap, replayability="replayable",
                reasons=[])
    replay_set = _create_set(
        admin_client, headers, ["t-slow"], component_id="slow-comp"
    )
    _approve(admin_client, headers, component_id="slow-comp")
    run = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    ).json()
    assert run["status"] == "failed"
    assert "timed out" in run["error"]
    assert run["sandbox_config"]["timeout_seconds"] == 1
    assert run["cleanup_state"] == "removed"
    assert not os.path.exists(run["workspace_path"])
    _assert_repo_clean(replay_repo)


def test_replay_run_fails_closed_without_sandbox(
    admin_client, replay_repo, monkeypatch
):
    """No sandbox backend and no explicit PROBE_UNSAFE_ALLOW_HOST_EXECUTION:
    execution refuses instead of running on the host."""
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    cap, _, _ = _capture(args=("a",))
    _post_trace(admin_client, headers, "t-fc", output="'class-a'",
                capture=cap, replayability="replayable", reasons=[])
    replay_set = _create_set(admin_client, headers, ["t-fc"])
    _approve(admin_client, headers)

    monkeypatch.delenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", raising=False)
    # Ensure no sandbox backend is discoverable regardless of the host.
    import app.validation_runner as validation_runner

    monkeypatch.setattr(validation_runner.shutil, "which", lambda name: None)

    run = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    ).json()
    assert run["status"] == "failed"
    assert "sandbox backend is available" in run["error"]
    assert run["cases"] == []
    assert run["cleanup_state"] == "removed"
    _assert_repo_clean(replay_repo)


def test_replay_run_unready_snapshot_and_unknown_set(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    cap, _, _ = _capture(args=("a",))
    _post_trace(admin_client, headers, "t-snap", output="'class-a'",
                capture=cap, replayability="replayable", reasons=[])
    replay_set = _create_set(admin_client, headers, ["t-snap"])
    _approve(admin_client, headers)

    unknown_set = admin_client.post(
        "/replay-runs", json={"replay_set_id": 99999}, headers=headers
    )
    assert unknown_set.status_code == 404

    unknown_snapshot = admin_client.post(
        "/replay-runs",
        json={"replay_set_id": replay_set["id"], "snapshot_id": 99999},
        headers=headers,
    )
    assert unknown_snapshot.status_code == 404


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


def test_approval_gate_403_then_approve_then_revoke(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    cap, _, _ = _capture(args=("a",))
    _post_trace(admin_client, headers, "t-gate", output="'class-a'",
                capture=cap, replayability="replayable", reasons=[])
    replay_set = _create_set(admin_client, headers, ["t-gate"])

    # Unapproved -> 403 with an explicit message.
    refused = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert refused.status_code == 403
    assert "not approved" in refused.json()["detail"]
    assert "replay-target" in refused.json()["detail"]

    # State endpoint shows inactive with the fixed Principle-4 warning.
    state = admin_client.get(
        "/components/replay-target/replay-approval", headers=headers
    ).json()
    assert state["active"] is False
    assert state["approval"] is None
    assert "pure-ish" in state["risk_context"]["warning"]
    assert state["risk_context"]["probe_plan_points"] == []

    approval = _approve(admin_client, headers, reason="reviewed: pure function")
    assert approval["status"] == "approved"
    assert approval["decision_method"] == "manual"
    assert approval["approved_by_user_id"] is not None
    assert approval["reason"] == "reviewed: pure function"
    # The risk context shown at approval time is stored with the decision.
    assert "warning" in approval["risk_context"]

    # Approving again while active is a conflict.
    again = admin_client.post(
        "/components/replay-target/replay-approval",
        json={"reason": "again"},
        headers=headers,
    )
    assert again.status_code == 409

    # Approved -> run allowed.
    allowed = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert allowed.status_code == 201
    assert allowed.json()["status"] == "completed"
    assert allowed.json()["approval_id"] == approval["id"]

    # Revoke -> 403 again.
    revoked = admin_client.post(
        "/components/replay-target/replay-approval/revoke", headers=headers
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["revoked_at"] is not None
    refused_again = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert refused_again.status_code == 403

    state = admin_client.get(
        "/components/replay-target/replay-approval", headers=headers
    ).json()
    assert state["active"] is False
    assert state["approval"]["status"] == "revoked"

    # Revoking twice is a conflict; re-approval creates a fresh record.
    assert (
        admin_client.post(
            "/components/replay-target/replay-approval/revoke", headers=headers
        ).status_code
        == 409
    )
    reapproved = _approve(admin_client, headers, reason="second review")
    assert reapproved["id"] != approval["id"]


def test_approval_risk_context_reuses_probe_plan_labels(
    admin_client, replay_repo, db_file
):
    _, system, headers, snapshot = _prepare(admin_client, replay_repo)
    now = time.time()
    raw = sqlite3.connect(db_file)
    cur = raw.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model, prompt_version,
             schema_version, decision_method, status, is_mock, started_at)
        VALUES (?, ?, 'probe_plan', 'mock', 'mock', 'v1', 'v1',
                'reasoning_llm', 'completed', 1, ?)
        """,
        (system["id"], snapshot["id"], now),
    )
    run_id = cur.lastrowid
    cur = raw.execute(
        """
        INSERT INTO probe_plans
            (system_id, snapshot_id, intelligence_run_id, feature_id,
             objective, status, created_at, updated_at)
        VALUES (?, ?, ?, 'feat', 'obj', 'approved', ?, ?)
        """,
        (system["id"], snapshot["id"], run_id, now, now),
    )
    plan_id = cur.lastrowid
    raw.execute(
        """
        INSERT INTO probe_points
            (plan_id, system_id, component_id, feature_id, path, symbol,
             line_start, line_end, reason, recommended_mode, side_effect_risk,
             replayability, status, created_at, updated_at)
        VALUES (?, ?, 'replay-target', 'feat', 'target.py', 'classify',
                1, 3, 'test', 'trace', 'low', 'safe', 'approved', ?, ?)
        """,
        (plan_id, system["id"], now, now),
    )
    raw.commit()
    raw.close()

    state = admin_client.get(
        "/components/replay-target/replay-approval", headers=headers
    ).json()
    points = state["risk_context"]["probe_plan_points"]
    assert len(points) == 1
    assert points[0]["plan_id"] == plan_id
    assert points[0]["side_effect_risk"] == "low"
    assert points[0]["replayability"] == "safe"

    approval = _approve(admin_client, headers)
    stored_points = approval["risk_context"]["probe_plan_points"]
    assert stored_points[0]["side_effect_risk"] == "low"
    assert stored_points[0]["replayability"] == "safe"


# ---------------------------------------------------------------------------
# Replay sets
# ---------------------------------------------------------------------------


def test_replay_set_manual_validation(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    cap, _, _ = _capture(args=("a",))
    _post_trace(admin_client, headers, "t-set", output="'class-a'",
                capture=cap, replayability="replayable", reasons=[])
    _post_trace(admin_client, headers, "t-other", component_id="other-comp",
                input_value={"args": [], "kwargs": {}}, output="'x'")

    def _attempt(trace_ids, component_id="replay-target"):
        return admin_client.post(
            "/replay-sets",
            json={"component_id": component_id, "trace_ids": trace_ids},
            headers=headers,
        )

    # Exactly one source is required.
    both = admin_client.post(
        "/replay-sets",
        json={
            "component_id": "replay-target",
            "trace_ids": ["t-set"],
            "analyzer_run_id": 1,
        },
        headers=headers,
    )
    assert both.status_code == 422
    neither = admin_client.post(
        "/replay-sets", json={"component_id": "replay-target"}, headers=headers
    )
    assert neither.status_code == 422

    assert _attempt([]).status_code == 422
    unknown = _attempt(["t-set", "t-ghost"])
    assert unknown.status_code == 422
    assert "t-ghost" in unknown.json()["detail"]
    # A trace of a different component does not belong in this set.
    wrong_component = _attempt(["t-set", "t-other"])
    assert wrong_component.status_code == 422
    assert "t-other" in wrong_component.json()["detail"]
    assert _attempt(["t-set", "t-set"]).status_code == 422
    over_cap = _attempt([f"t-{i}" for i in range(51)])
    assert over_cap.status_code == 422
    assert "at most 50" in over_cap.json()["detail"]

    created = _attempt(["t-set"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "manual"
    assert body["trace_ids"] == ["t-set"]
    # Per-trace replay preview for Phase D badges.
    assert body["traces"][0]["replayability"] == "replayable"
    assert body["traces"][0]["input_source"] == "structured"
    assert body["traces"][0]["skip_reason"] is None

    fetched = admin_client.get(
        f"/replay-sets/{body['id']}", headers=headers
    ).json()
    assert fetched["trace_ids"] == ["t-set"]
    listing = admin_client.get(
        "/replay-sets", params={"component_id": "replay-target"}, headers=headers
    ).json()
    assert [item["id"] for item in listing] == [body["id"]]


def test_replay_set_preview_reports_input_sources(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    trace_ids = _post_matrix_traces(admin_client, headers)
    replay_set = _create_set(admin_client, headers, trace_ids)
    previews = {item["trace_id"]: item for item in replay_set["traces"]}
    assert previews["t-match"]["input_source"] == "structured"
    assert previews["t-legacy"]["input_source"] == "repr_partial"
    assert previews["t-legacy"]["replayability"] is None
    assert previews["t-unreplayable"]["input_source"] is None
    assert previews["t-unreplayable"]["skip_reason"] == "unreplayable_capture"
    assert previews["t-unreplayable"]["replay_reasons"] == ["size_limit_exceeded"]
    assert previews["t-repr-bad"]["input_source"] == "repr_partial"


def test_replay_set_from_analyzer_run(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    cap_a, _, _ = _capture(args=("a",))
    cap_b, _, _ = _capture(args=("b",))
    projections = [
        {"projection_name": "probe", "phase": "output", "fields": {"v": 1}}
    ]
    _post_trace(admin_client, headers, "t-an-1", output="'class-a'",
                capture=cap_a, replayability="replayable", reasons=[],
                projections=projections)
    _post_trace(admin_client, headers, "t-an-2", output="'class-b'",
                capture=cap_b, replayability="replayable", reasons=[],
                projections=projections)

    analyzer = admin_client.post(
        "/trace-analyzers",
        json={
            "name": "replay source",
            "intent": "collect trace ids",
            "spec": {
                "source": "trace_projections",
                "filter": {"components": ["replay-target"]},
                "select": [{"name": "trace_id", "path": "$.trace_id"}],
            },
        },
        headers=headers,
    )
    assert analyzer.status_code == 201, analyzer.text
    analyzer_id = analyzer.json()["id"]
    review = admin_client.put(
        f"/trace-analyzers/{analyzer_id}/review",
        json={"review_status": "approved"},
        headers=headers,
    )
    assert review.status_code == 200
    run = admin_client.post(
        f"/trace-analyzers/{analyzer_id}/runs", headers=headers
    )
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "completed"
    run_id = run.json()["id"]

    created = admin_client.post(
        "/replay-sets",
        json={"component_id": "replay-target", "analyzer_run_id": run_id},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "analyzer_run"
    assert body["source_analyzer_run_id"] == run_id
    assert sorted(body["trace_ids"]) == ["t-an-1", "t-an-2"]

    # The stored result has no traces for another component -> 422.
    empty = admin_client.post(
        "/replay-sets",
        json={"component_id": "tuple-comp", "analyzer_run_id": run_id},
        headers=headers,
    )
    assert empty.status_code == 422

    missing_run = admin_client.post(
        "/replay-sets",
        json={"component_id": "replay-target", "analyzer_run_id": 99999},
        headers=headers,
    )
    assert missing_run.status_code == 404


# ---------------------------------------------------------------------------
# System isolation (all four tables)
# ---------------------------------------------------------------------------


def test_system_isolation_for_replay_tables(admin_client, replay_repo):
    token, system_a, headers_a, _ = _prepare(admin_client, replay_repo)
    system_b = _create_system(admin_client, token, "other-system")
    headers_b = _headers(token, system_b["id"])

    cap, _, _ = _capture(args=("a",))
    _post_trace(admin_client, headers_a, "t-iso", output="'class-a'",
                capture=cap, replayability="replayable", reasons=[])
    replay_set = _create_set(admin_client, headers_a, ["t-iso"])
    approval = _approve(admin_client, headers_a)
    run = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers_a
    ).json()
    assert run["status"] == "completed"

    # replay_sets
    assert admin_client.get("/replay-sets", headers=headers_b).json() == []
    assert (
        admin_client.get(
            f"/replay-sets/{replay_set['id']}", headers=headers_b
        ).status_code
        == 404
    )
    # Traces of system A are invisible when building a set in system B.
    cross_set = admin_client.post(
        "/replay-sets",
        json={"component_id": "replay-target", "trace_ids": ["t-iso"]},
        headers=headers_b,
    )
    assert cross_set.status_code == 422

    # replay_runs (+ replay_case_results through the run payload)
    assert admin_client.get("/replay-runs", headers=headers_b).json() == []
    assert (
        admin_client.get(f"/replay-runs/{run['id']}", headers=headers_b).status_code
        == 404
    )
    cross_run = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers_b
    )
    assert cross_run.status_code == 404

    # replay_approvals: A's approval does not leak into B.
    state_b = admin_client.get(
        "/components/replay-target/replay-approval", headers=headers_b
    ).json()
    assert state_b["active"] is False
    assert state_b["approval"] is None
    assert (
        admin_client.post(
            "/components/replay-target/replay-approval/revoke", headers=headers_b
        ).status_code
        == 409
    )
    # A's approval is still intact and active.
    state_a = admin_client.get(
        "/components/replay-target/replay-approval", headers=headers_a
    ).json()
    assert state_a["active"] is True
    assert state_a["approval"]["id"] == approval["id"]


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


REPLAY_TABLES = {
    "replay_approvals",
    "replay_sets",
    "replay_runs",
    "replay_case_results",
}


def test_fresh_db_has_replay_tables(admin_client, db_file):
    raw = sqlite3.connect(db_file)
    tables = {
        row[0]
        for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    raw.close()
    assert REPLAY_TABLES <= tables


def test_existing_db_gains_replay_tables_and_init_is_idempotent(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "pre-244.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE traces (
            system_id    INTEGER NOT NULL,
            trace_id     TEXT NOT NULL,
            component_id TEXT NOT NULL,
            mode         TEXT,
            input_json   TEXT,
            output_text  TEXT,
            error        TEXT,
            duration_ms  REAL,
            timestamp    REAL NOT NULL,
            PRIMARY KEY (system_id, trace_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO traces
            (system_id, trace_id, component_id, mode, input_json, output_text,
             error, duration_ms, timestamp)
        VALUES (1, 'old-1', 'legacy-comp', 'trace', NULL, "'X'", NULL, 1.0, ?)
        """,
        (time.time(),),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
    from app.db import init_db
    from app.main import app

    with TestClient(app):
        check = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert REPLAY_TABLES <= tables
        # Pre-existing rows are untouched.
        assert (
            check.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 1
        )
        check.close()
        # init_db is idempotent (CREATE TABLE IF NOT EXISTS; no ALTER needed).
        init_db()
        init_db()


# ---------------------------------------------------------------------------
# Runner unit details
# ---------------------------------------------------------------------------


def test_build_replay_env_is_minimal_and_disables_probe():
    from app.replay_runner import build_replay_env

    env = build_replay_env("/tmp/some-workspace")
    assert env["PROBE_ENABLED"] == "false"
    assert env["PYTHONHASHSEED"] == "0"
    # Minimal allowlist only — no ambient secrets leak into the sandbox.
    assert set(env.keys()) == {
        "HOME", "PATH", "LANG", "TERM", "PWD", "PROBE_ENABLED", "PYTHONHASHSEED",
    }


def test_replay_timeout_seconds_clamped(monkeypatch):
    from app.replay_runner import replay_timeout_seconds

    monkeypatch.delenv("PROBE_REPLAY_TIMEOUT_SECONDS", raising=False)
    assert replay_timeout_seconds() == 60
    monkeypatch.setenv("PROBE_REPLAY_TIMEOUT_SECONDS", "0")
    assert replay_timeout_seconds() == 1
    monkeypatch.setenv("PROBE_REPLAY_TIMEOUT_SECONDS", "9999")
    assert replay_timeout_seconds() == 300
    monkeypatch.setenv("PROBE_REPLAY_TIMEOUT_SECONDS", "not-a-number")
    assert replay_timeout_seconds() == 60


def test_output_truncation_flag_marks_prefix_bounded_comparison():
    from app.replay_runner import CasePlan, classify_case

    marker = "x" * 10 + "...<truncated>"
    plan = CasePlan(
        trace_id="t", position=0, input_source="structured", skip_reason=None,
        recorded_output=marker, recorded_error_first_line=None,
        harness_case={"input_kind": "structured", "capture": {}},
    )
    classified = classify_case(
        plan, {"status": "ok", "output": marker, "duration_ms": 1.0}
    )
    assert classified["case_status"] == "match"
    assert classified["output_truncated"] is True
