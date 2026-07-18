"""Tests for Issue #245 (Offline shadow simulation — Phase C of #242).

Covers the deterministic variant classification unit
(``app/replay_variants.classify_variant_case`` — the full 7-member finite
case_status set plus structured/repr comparison modes), the harness v2
``structured_output`` addition, and the end-to-end variant-run API:
baseline + patch variants executed in independent worktrees against the same
Replay Set, per-variant patch apply (valid / invalid isolated per variant),
the diff matrix (match / diff / candidate_error / error_to_success), the
recorded-error execution path (the offline decorator.py:272 fix),
determinism + order independence, the LLM candidate-draft source
(reasoning_llm provenance, is_mock, fail-closed), the experiment-promotion
payload, worktree cleanup + target repo unchanged, System isolation for the
new tables, and additive migration.

The shared field-equality library (``app/comparison.py``) has its own unit
coverage in ``test_comparison.py``; ``test_shadow_diff.py`` proves the #150
analyzer still passes after the extraction.
"""

import json
import os
import sqlite3
import subprocess
import textwrap
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models import ReplayVariantRunCreate
from app.replay_variants import (
    CASE_CANDIDATE_ERROR,
    CASE_DIFF,
    CASE_ERROR_TO_DIFFERENT_ERROR,
    CASE_ERROR_TO_SAME_ERROR,
    CASE_ERROR_TO_SUCCESS,
    CASE_MATCH,
    CASE_SKIPPED,
    COMPARISON_REPR,
    COMPARISON_STRUCTURED,
    classify_variant_case,
    summarize_variant_cases,
)


# ---------------------------------------------------------------------------
# Unit: classify_variant_case (the deterministic finite matrix)
# ---------------------------------------------------------------------------


def test_variant_run_accepts_up_to_twenty_candidates():
    candidate = {"label": "candidate", "patch_text": "diff", "source": "manual"}
    payload = ReplayVariantRunCreate(replay_set_id=1, variants=[candidate] * 20)
    assert len(payload.variants) == 20
    with pytest.raises(ValidationError):
        ReplayVariantRunCreate(replay_set_id=1, variants=[candidate] * 21)


def _planned():
    return {"input_kind": "structured", "capture": {}}


def _ok(output, structured="__none__", duration=1.0):
    case = {"status": "ok", "output": output, "error": None, "duration_ms": duration}
    if structured != "__none__":
        case["structured_output"] = structured
    return case


def _err(first_line, duration=1.0):
    return {
        "status": "error",
        "output": None,
        "error": first_line + "\n  File ...",
        "duration_ms": duration,
    }


def test_classify_match_structured_dict():
    result = classify_variant_case(
        "t1", 0, _planned(),
        _ok("{'k': 'a'}", structured={"k": "a", "n": 1}),
        _ok("{'k': 'a'}", structured={"k": "a", "n": 1}),
    )
    assert result["case_status"] == CASE_MATCH
    assert result["comparison_mode"] == COMPARISON_STRUCTURED
    assert result["field_diffs"] == []


def test_classify_diff_structured_reports_changed_field():
    result = classify_variant_case(
        "t1", 0, _planned(),
        _ok("x", structured={"kind": "a", "n": 1}),
        _ok("y", structured={"kind": "b", "n": 1}),
    )
    assert result["case_status"] == CASE_DIFF
    assert result["comparison_mode"] == COMPARISON_STRUCTURED
    assert result["field_diffs"] == ["kind"]


def test_classify_diff_structured_missing_key_vs_null():
    # Candidate drops a key entirely (missing) vs baseline null — NOT equal.
    result = classify_variant_case(
        "t1", 0, _planned(),
        _ok("x", structured={"a": None, "b": 2}),
        _ok("y", structured={"b": 2}),
    )
    assert result["case_status"] == CASE_DIFF
    assert result["field_diffs"] == ["a"]


def test_classify_diff_structured_nan_never_equal():
    nan = float("nan")
    result = classify_variant_case(
        "t1", 0, _planned(),
        _ok("x", structured={"v": nan}),
        _ok("x", structured={"v": nan}),
    )
    assert result["case_status"] == CASE_DIFF
    assert result["field_diffs"] == ["v"]


def test_classify_repr_mode_when_no_structured_output():
    result = classify_variant_case(
        "t1", 0, _planned(),
        _ok("(1, 2)"),  # no structured_output -> repr fallback
        _ok("(1, 2)"),
    )
    assert result["case_status"] == CASE_MATCH
    assert result["comparison_mode"] == COMPARISON_REPR


def test_classify_repr_mode_diff():
    result = classify_variant_case(
        "t1", 0, _planned(), _ok("(1, 2)"), _ok("(1, 3)")
    )
    assert result["case_status"] == CASE_DIFF
    assert result["comparison_mode"] == COMPARISON_REPR


def test_classify_candidate_error():
    result = classify_variant_case(
        "t1", 0, _planned(), _ok("ok", structured="ok"), _err("ValueError: boom")
    )
    assert result["case_status"] == CASE_CANDIDATE_ERROR
    assert result["candidate_error"] == "ValueError: boom"
    assert result["comparison_mode"] is None


def test_classify_error_to_success_is_the_rescue_case():
    result = classify_variant_case(
        "t1", 0, _planned(), _err("ValueError: boom"), _ok("fixed", structured="fixed")
    )
    assert result["case_status"] == CASE_ERROR_TO_SUCCESS
    assert result["recorded_error"] == "ValueError: boom"
    assert result["candidate_output"] == "fixed"


def test_classify_error_to_same_and_different_error():
    same = classify_variant_case(
        "t1", 0, _planned(), _err("ValueError: boom"), _err("ValueError: boom")
    )
    assert same["case_status"] == CASE_ERROR_TO_SAME_ERROR
    diff = classify_variant_case(
        "t1", 0, _planned(), _err("ValueError: boom"), _err("KeyError: 'x'")
    )
    assert diff["case_status"] == CASE_ERROR_TO_DIFFERENT_ERROR


def test_classify_server_side_skip_is_skipped_for_variant():
    result = classify_variant_case("t1", 0, None, None, None)
    assert result["case_status"] == CASE_SKIPPED
    assert result["comparison_mode"] is None


def test_classify_harness_skip_propagates_as_skipped():
    skip = {"status": "skipped", "skip_reason": "undecodable_input"}
    result = classify_variant_case("t1", 0, _planned(), skip, skip)
    assert result["case_status"] == CASE_SKIPPED


def test_classify_duration_delta():
    result = classify_variant_case(
        "t1", 0, _planned(),
        _ok("x", structured="x", duration=10.0),
        _ok("x", structured="x", duration=4.0),
    )
    assert result["duration_delta_ms"] == pytest.approx(-6.0)


def test_summarize_counts_examples_and_avg_delta():
    rows = [
        {"trace_id": "a", "case_status": CASE_MATCH, "duration_delta_ms": 2.0},
        {"trace_id": "b", "case_status": CASE_DIFF, "duration_delta_ms": 4.0},
        {"trace_id": "c", "case_status": CASE_DIFF, "duration_delta_ms": None},
    ]
    summary, examples, avg = summarize_variant_cases(rows, max_examples=5)
    assert summary["match"] == 1
    assert summary["diff"] == 2
    assert summary["total"] == 3
    assert examples["diff"] == ["b", "c"]
    assert avg == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Integration fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def unsafe_test_execution(monkeypatch):
    monkeypatch.setenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", "true")


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "replay-variants-test.db"


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
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client
    get_llm_client.cache_clear()


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


SVC_SOURCE = '''\
def probe(component_id=None):
    def deco(fn):
        return fn
    return deco


@probe(component_id="norm")
def normalize(payload):
    return {"kind": "a", "size": len(payload)}


@probe(component_id="cls")
def classify(x):
    if x == "boom":
        raise ValueError("boom")
    return "class-" + str(x)


@probe(component_id="setcomp")
def as_set(x):
    return {x}
'''


@pytest.fixture
def replay_repo(tmp_path):
    repo = tmp_path / "variant-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "svc.py").write_text(SVC_SOURCE)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _make_patch(repo, rel, transform):
    """Build a real unified diff by editing the file, capturing `git diff`,
    then reverting so the committed (pinned) state is unchanged."""
    path = repo / rel
    original = path.read_text()
    path.write_text(transform(original))
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--", rel],
        check=True, capture_output=True, text=True,
    ).stdout
    _git(repo, "checkout", "--", rel)
    assert diff.strip(), "transform produced no diff"
    return diff


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    )
    assert response.status_code == 200, response.text
    return response.cookies.get("probe_session")


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _create_system(client, token, name):
    response = client.post(
        "/systems", json={"name": name, "environment": "test"}, headers=_headers(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _prepare(client, repo, name="variant-system"):
    token = _login(client)
    system = _create_system(client, token, name)
    headers = _headers(token, system["id"])
    assert client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": []},
        headers=headers,
    ).status_code == 200
    snapshot = client.post("/repository/snapshots", headers=headers)
    assert snapshot.status_code == 201, snapshot.text
    assert client.post("/repository/symbols/index", headers=headers).status_code == 201
    return token, system, headers, snapshot.json()


def _capture(args=(), kwargs=None):
    from probe_agent.replay_capture import capture_input, compile_spec

    return capture_input(tuple(args), dict(kwargs or {}), compile_spec(True))


def _post_trace(client, headers, trace_id, component_id, *, args=(), kwargs=None,
                output=None, error=None):
    capture, replayability, reasons = _capture(args, kwargs)
    event = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": [repr(a) for a in args],
                  "kwargs": {k: repr(v) for k, v in (kwargs or {}).items()}},
        "output": output,
        "error": error,
        "duration_ms": 1.0,
        "timestamp": time.time(),
        "input_capture": capture,
        "replayability": replayability,
        "replay_reasons": reasons,
    }
    response = client.post("/traces", json=event, headers=headers)
    assert response.status_code == 201, response.text


def _approve(client, headers, component_id):
    response = client.post(
        f"/components/{component_id}/replay-approval",
        json={"reason": "test"}, headers=headers,
    )
    assert response.status_code == 201, response.text


def _create_set(client, headers, trace_ids, component_id, name="set"):
    response = client.post(
        "/replay-sets",
        json={"component_id": component_id, "name": name, "trace_ids": trace_ids},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _run_variants(client, headers, replay_set_id, variants):
    return client.post(
        "/replay-variant-runs",
        json={"replay_set_id": replay_set_id, "variants": variants},
        headers=headers,
    )


def _assert_repo_clean(repo):
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        check=True, capture_output=True, text=True,
    )
    assert status.stdout.strip() == ""
    worktrees = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        check=True, capture_output=True, text=True,
    )
    assert worktrees.stdout.count("worktree ") == 1


def _variant(run_json, variant_key):
    return next(v for v in run_json["variants"] if v["variant_key"] == variant_key)


# ---------------------------------------------------------------------------
# Integration: variant runs
# ---------------------------------------------------------------------------


def test_variant_match_and_diff_structured(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")

    # variant-1: no-op body change (adds a comment) -> identical output -> match
    noop_patch = _make_patch(
        replay_repo, "svc.py",
        lambda s: s.replace(
            'return {"kind": "a", "size": len(payload)}',
            '# unchanged behavior\n    return {"kind": "a", "size": len(payload)}',
        ),
    )
    # variant-2: change the "kind" field -> structured diff on that field
    diff_patch = _make_patch(
        replay_repo, "svc.py",
        lambda s: s.replace('"kind": "a"', '"kind": "b"'),
    )

    response = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "noop", "patch_text": noop_patch, "source": "manual"},
        {"label": "kind-b", "patch_text": diff_patch, "source": "pasted"},
    ])
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "completed"

    noop = _variant(run, "variant-1")
    assert noop["apply_status"] == "applied"
    assert noop["aggregate"]["match"] == 1
    assert noop["cases"][0]["case_status"] == "match"
    assert noop["cases"][0]["comparison_mode"] == "structured"

    changed = _variant(run, "variant-2")
    assert changed["apply_status"] == "applied"
    assert changed["aggregate"]["diff"] == 1
    case = changed["cases"][0]
    assert case["case_status"] == "diff"
    assert case["comparison_mode"] == "structured"
    assert case["field_diffs"] == ["kind"]
    _assert_repo_clean(replay_repo)


def test_variant_invalid_patch_isolated_from_others(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hi",),
                output="{'kind': 'a', 'size': 2}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")

    good = _make_patch(replay_repo, "svc.py",
                       lambda s: s.replace('"kind": "a"', '"kind": "z"'))
    bad = (
        "--- a/svc.py\n+++ b/svc.py\n@@ -1,1 +1,1 @@\n"
        "-this context does not exist in the file at all\n"
        "+neither does this\n"
    )

    response = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "bad", "patch_text": bad, "source": "manual"},
        {"label": "good", "patch_text": good, "source": "manual"},
    ])
    run = response.json()
    assert run["status"] == "completed"

    bad_variant = _variant(run, "variant-1")
    assert bad_variant["apply_status"] == "invalid_patch"
    assert bad_variant["status"] == "failed"
    assert bad_variant["apply_error"]
    assert bad_variant["cases"] == []
    denied = admin_client.get(
        f"/replay-variant-runs/{run['id']}/variants/{bad_variant['id']}"
        "/experiment-payload",
        headers=headers,
    )
    assert denied.status_code == 422

    # The good variant and the baseline are entirely unaffected.
    good_variant = _variant(run, "variant-2")
    assert good_variant["apply_status"] == "applied"
    assert good_variant["aggregate"]["diff"] == 1
    baseline = _variant(run, "baseline")
    assert baseline["is_baseline"] is True
    _assert_repo_clean(replay_repo)


def test_variant_error_trace_rescued_is_error_to_success(admin_client, replay_repo):
    """A recorded-error trace is executed against the candidate (the offline
    decorator.py:272 fix): a patch that stops it raising is error_to_success."""
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "boom", "cls", args=("boom",),
                error="ValueError: boom\n  File ...")
    replay_set = _create_set(admin_client, headers, ["boom"], "cls")
    _approve(admin_client, headers, "cls")

    # Remove the raise so classify("boom") now returns a value.
    rescue = _make_patch(
        replay_repo, "svc.py",
        lambda s: s.replace(
            '    if x == "boom":\n        raise ValueError("boom")\n', ""
        ),
    )
    response = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "rescue", "patch_text": rescue, "source": "manual"},
    ])
    run = response.json()
    assert run["status"] == "completed"
    variant = _variant(run, "variant-1")
    assert variant["aggregate"]["error_to_success"] == 1
    case = variant["cases"][0]
    assert case["case_status"] == "error_to_success"
    assert "boom" in (case["recorded_error"] or "")
    assert case["candidate_output"] == "'class-boom'"


def test_variant_candidate_error(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "ok", "cls", args=("hi",), output="'class-hi'")
    replay_set = _create_set(admin_client, headers, ["ok"], "cls")
    _approve(admin_client, headers, "cls")

    # Make classify raise unconditionally -> baseline ok, candidate errors.
    breaker = _make_patch(
        replay_repo, "svc.py",
        lambda s: s.replace(
            '    return "class-" + str(x)',
            '    raise RuntimeError("broken")',
        ),
    )
    response = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "break", "patch_text": breaker, "source": "manual"},
    ])
    run = response.json()
    variant = _variant(run, "variant-1")
    assert variant["aggregate"]["candidate_error"] == 1
    case = variant["cases"][0]
    assert case["case_status"] == "candidate_error"
    assert "RuntimeError" in (case["candidate_error"] or "")


def test_variant_repr_comparison_mode_for_non_json_output(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "setcomp", args=("x",), output="{'x'}")
    replay_set = _create_set(admin_client, headers, ["t1"], "setcomp")
    _approve(admin_client, headers, "setcomp")

    noop = _make_patch(
        replay_repo, "svc.py",
        lambda s: s.replace("    return {x}", "    # note\n    return {x}"),
    )
    response = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "noop", "patch_text": noop, "source": "manual"},
    ])
    run = response.json()
    case = _variant(run, "variant-1")["cases"][0]
    # A tuple is not JSON-native -> harness omits structured_output -> repr mode.
    assert case["comparison_mode"] == "repr"
    assert case["case_status"] == "match"


def test_variant_run_determinism_and_order_independence(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("abc",),
                output="{'kind': 'a', 'size': 3}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")

    patch_a = _make_patch(replay_repo, "svc.py",
                          lambda s: s.replace('"kind": "a"', '"kind": "p"'))
    patch_b = _make_patch(replay_repo, "svc.py",
                          lambda s: s.replace('"size": len(payload)', '"size": 0'))
    va = {"label": "a", "patch_text": patch_a, "source": "manual"}
    vb = {"label": "b", "patch_text": patch_b, "source": "manual"}

    run1 = _run_variants(admin_client, headers, replay_set["id"], [va, vb]).json()
    run2 = _run_variants(admin_client, headers, replay_set["id"], [vb, va]).json()

    def by_label(run):
        return {v["label"]: (v["aggregate"]["match"], v["aggregate"]["diff"],
                             _first_field_diffs(v)) for v in run["variants"]
                if not v["is_baseline"]}

    assert by_label(run1) == by_label(run2)


def test_twenty_candidate_run_meets_ten_second_target(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("abc",),
                output="{'kind': 'a', 'size': 3}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(
        replay_repo,
        "svc.py",
        lambda source: source.replace('"kind": "a"', '"kind": "b"'),
    )
    variants = [
        {"label": f"candidate-{index}", "patch_text": patch, "source": "manual"}
        for index in range(20)
    ]

    started = time.perf_counter()
    response = _run_variants(admin_client, headers, replay_set["id"], variants)
    elapsed = time.perf_counter() - started

    assert response.status_code == 201, response.text
    assert len([v for v in response.json()["variants"] if not v["is_baseline"]]) == 20
    assert elapsed < 10.0, f"20-candidate replay took {elapsed:.2f}s"


def _first_field_diffs(variant):
    return tuple(variant["cases"][0]["field_diffs"]) if variant["cases"] else ()


def test_variant_run_requires_approval(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    # No approval.
    patch = _make_patch(replay_repo, "svc.py",
                        lambda s: s.replace('"kind": "a"', '"kind": "b"'))
    response = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "x", "patch_text": patch, "source": "manual"},
    ])
    assert response.status_code == 403
    assert "not approved" in response.json()["detail"]


def test_variant_baseline_import_failure_is_audited_not_500(admin_client, replay_repo):
    source = replay_repo / "svc.py"
    source.write_text("import module_that_does_not_exist\n" + source.read_text())
    _git(replay_repo, "add", "svc.py")
    _git(replay_repo, "commit", "-m", "broken import")

    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(
        replay_repo, "svc.py", lambda text: text.replace('"kind": "a"', '"kind": "b"')
    )

    response = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "candidate", "patch_text": patch, "source": "manual"},
    ])
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "failed"
    assert "ModuleNotFoundError" in (run["error"] or "")
    assert _variant(run, "baseline")["status"] == "failed"
    candidate = _variant(run, "variant-1")
    assert candidate["status"] == "failed"
    assert "baseline replay failed" in (candidate["error"] or "")
    assert candidate["cases"] == []


def test_variant_experiment_promotion_payload(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(replay_repo, "svc.py",
                        lambda s: s.replace('"kind": "a"', '"kind": "b"'))
    run = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "x", "patch_text": patch, "source": "manual"},
    ]).json()
    variant = _variant(run, "variant-1")

    payload = admin_client.get(
        f"/replay-variant-runs/{run['id']}/variants/{variant['id']}"
        "/experiment-payload",
        headers=headers,
    )
    assert payload.status_code == 200, payload.text
    body = payload.json()
    assert body["patch_text"] == patch
    assert body["source"] == "replay_variant"
    assert body["origin"]["replay_variant_run_id"] == run["id"]

    # The baseline has no patch to promote.
    baseline = _variant(run, "baseline")
    denied = admin_client.get(
        f"/replay-variant-runs/{run['id']}/variants/{baseline['id']}"
        "/experiment-payload",
        headers=headers,
    )
    assert denied.status_code == 422


# ---------------------------------------------------------------------------
# LLM candidate drafts
# ---------------------------------------------------------------------------


def test_llm_draft_success_records_provenance_and_is_mock(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "cls", args=("hi",), output="'class-hi'")
    replay_set = _create_set(admin_client, headers, ["t1"], "cls")
    _approve(admin_client, headers, "cls")

    response = admin_client.post(
        "/replay-variant-drafts",
        json={"replay_set_id": replay_set["id"], "trace_id": "t1",
              "objective": "uppercase the input"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "proposed"
    assert draft["is_mock"] is True
    assert draft["decision_method"] == "reasoning_llm"
    assert draft["provider"] == "mock"
    assert "def classify" in draft["patch_text"] or draft["patch_text"].strip()

    # The drafted patch is a real applicable diff: run it as a variant.
    run = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "llm", "patch_text": draft["patch_text"], "source": "llm_draft"},
    ]).json()
    variant = _variant(run, "variant-1")
    assert variant["apply_status"] == "applied"
    # mock candidate uppercases -> "HI" vs baseline "class-hi" -> diff
    assert variant["aggregate"]["diff"] == 1


def test_llm_draft_trace_must_belong_to_replay_set(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "in-set", "cls", args=("a",), output="'class-a'")
    _post_trace(admin_client, headers, "outside", "cls", args=("b",), output="'class-b'")
    replay_set = _create_set(admin_client, headers, ["in-set"], "cls")

    response = admin_client.post(
        "/replay-variant-drafts",
        json={"replay_set_id": replay_set["id"], "trace_id": "outside",
              "objective": "change behavior"},
        headers=headers,
    )
    assert response.status_code == 422
    assert "belong" in response.json()["detail"]


def test_regression_scaffold_uses_reasoning_llm_and_persists_provenance(
    admin_client, replay_repo, db_file
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(
        replay_repo, "svc.py", lambda text: text.replace('"kind": "a"', '"kind": "b"')
    )
    run = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "candidate", "patch_text": patch, "source": "manual"},
    ]).json()
    variant = _variant(run, "variant-1")

    response = admin_client.post(
        "/replay-regression-scaffolds",
        json={"replay_run_id": run["id"], "replay_variant_id": variant["id"],
              "trace_id": "t1"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    scaffold = response.json()
    assert scaffold["status"] == "proposed"
    assert scaffold["decision_method"] == "reasoning_llm"
    assert scaffold["is_mock"] is True
    assert "def test_" in scaffold["scaffold_text"]

    conn = sqlite3.connect(str(db_file))
    try:
        audit = conn.execute(
            "SELECT run_type, status, decision_method FROM intelligence_runs WHERE id = ?",
            (scaffold["intelligence_run_id"],),
        ).fetchone()
        persisted = conn.execute(
            "SELECT scaffold_text, status FROM replay_regression_scaffolds WHERE id = ?",
            (scaffold["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert audit == ("replay_regression_scaffold", "completed", "reasoning_llm")
    assert persisted == (scaffold["scaffold_text"], "proposed")


def test_llm_draft_failure_fails_closed_and_raw_results_survive(
    admin_client, replay_repo, monkeypatch
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "cls", args=("hi",), output="'class-hi'")
    replay_set = _create_set(admin_client, headers, ["t1"], "cls")
    _approve(admin_client, headers, "cls")

    # A prior deterministic variant run whose raw results must remain readable.
    patch = _make_patch(replay_repo, "svc.py",
                        lambda s: s.replace('"class-"', '"CLASS-"'))
    prior = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "manual", "patch_text": patch, "source": "manual"},
    ]).json()

    # Now force the reasoning model to fail.
    from app.llm import MockLLMClient

    def _boom(self, *a, **k):
        from app.llm import LLMError
        raise LLMError("mock provider forced failure")

    monkeypatch.setattr(MockLLMClient, "generate_text", _boom)

    response = admin_client.post(
        "/replay-variant-drafts",
        json={"replay_set_id": replay_set["id"], "trace_id": "t1",
              "objective": "anything"},
        headers=headers,
    )
    # Fail closed: no heuristic fallback candidate is synthesized.
    assert response.status_code in (502, 201)
    if response.status_code == 201:
        assert response.json()["status"] == "failed"
        assert not response.json()["patch_text"]

    # The prior run's deterministic raw results are still fully readable.
    reread = admin_client.get(
        f"/replay-variant-runs/{prior['id']}", headers=headers
    ).json()
    assert reread["status"] == "completed"
    assert _variant(reread, "variant-1")["aggregate"]["diff"] == 1


def test_llm_draft_client_creation_failure_is_persisted(
    admin_client, replay_repo, monkeypatch, db_file
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "cls", args=("hi",), output="'class-hi'")
    replay_set = _create_set(admin_client, headers, ["t1"], "cls")

    from app.llm import LLMError
    from app.routes import replay as replay_routes

    def fail_to_create_client():
        raise LLMError("missing provider credential")

    monkeypatch.setattr(replay_routes, "get_llm_client", fail_to_create_client)
    response = admin_client.post(
        "/replay-variant-drafts",
        json={"replay_set_id": replay_set["id"], "trace_id": "t1",
              "objective": "change behavior"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "failed"
    assert "missing provider credential" in draft["error"]
    assert draft["patch_text"] == ""

    conn = sqlite3.connect(str(db_file))
    try:
        audit = conn.execute(
            "SELECT status, error_details FROM intelligence_runs "
            "WHERE run_type = 'replay_variant_draft' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert audit == ("failed", "missing provider credential")


# ---------------------------------------------------------------------------
# Isolation, cleanup, migration
# ---------------------------------------------------------------------------


def test_variant_worktree_cleanup_and_target_repo_unchanged(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(replay_repo, "svc.py",
                        lambda s: s.replace('"kind": "a"', '"kind": "b"'))
    run = _run_variants(admin_client, headers, replay_set["id"], [
        {"label": "x", "patch_text": patch, "source": "manual"},
    ]).json()
    for variant in run["variants"]:
        assert variant["cleanup_state"] == "removed"
        assert not (variant["workspace_path"] and os.path.exists(variant["workspace_path"]))
    _assert_repo_clean(replay_repo)


def test_variant_tables_system_isolation(admin_client, replay_repo):
    token, system_a, headers_a, _ = _prepare(admin_client, replay_repo, "sys-a")
    _post_trace(admin_client, headers_a, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    replay_set = _create_set(admin_client, headers_a, ["t1"], "norm")
    _approve(admin_client, headers_a, "norm")
    patch = _make_patch(replay_repo, "svc.py",
                        lambda s: s.replace('"kind": "a"', '"kind": "b"'))
    run = _run_variants(admin_client, headers_a, replay_set["id"], [
        {"label": "x", "patch_text": patch, "source": "manual"},
    ]).json()

    # A second system must not see system A's variant run.
    system_b = _create_system(admin_client, token, "sys-b")
    headers_b = _headers(token, system_b["id"])
    assert admin_client.get(
        f"/replay-variant-runs/{run['id']}", headers=headers_b
    ).status_code == 404
    assert admin_client.get(
        "/replay-variant-runs", headers=headers_b
    ).json() == []


def test_fresh_db_has_variant_tables(admin_client, db_file):
    _login(admin_client)  # forces init_db
    conn = sqlite3.connect(str(db_file))
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert {"replay_variants", "replay_variant_case_results",
            "replay_variant_drafts", "replay_regression_scaffolds"} <= names


def test_existing_db_gains_variant_tables_idempotent(tmp_path, monkeypatch):
    # A legacy DB predating Phase C: a bare `traces` table, no replay_* tables.
    db_path = tmp_path / "pre-245.db"
    conn = sqlite3.connect(str(db_path))
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
    conn.commit()
    conn.close()

    monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    import app.db as db_module

    db_module.init_db()
    db_module.init_db()  # idempotent (CREATE TABLE IF NOT EXISTS, no ALTER needed)

    conn = sqlite3.connect(str(db_path))
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert {"replay_variants", "replay_variant_case_results",
            "replay_variant_drafts", "replay_regression_scaffolds"} <= names
