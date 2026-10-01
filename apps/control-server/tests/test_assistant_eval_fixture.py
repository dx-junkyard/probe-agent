"""Issue #468 (Epic #467): the fixed assistant evaluation set is structurally
valid, and the harness runs end to end in structural mode.

A structural pass is NOT an answer-quality pass (IK-0004 / EC-P06): these
tests only hold the fixture to the harness's finite vocabularies and prove the
harness itself works.  See docs/03-validation/assistant-answer-quality.md.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = SERVER_ROOT / "tests" / "fixtures" / "assistant_eval" / "questions.json"
SCRIPT = SERVER_ROOT / "scripts" / "assistant_eval.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("assistant_eval", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _load_harness()


@pytest.fixture(scope="module")
def questions():
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return data["questions"]


REQUIRED_KEYS = {
    "id", "category", "screen_id", "route_params", "fixture", "question",
    "expected_sources", "answerable_scope", "must_mention",
    "forbidden_assertions", "llm_script", "harness_support",
}


def test_ids_unique_and_entries_complete(questions):
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids))
    for q in questions:
        assert REQUIRED_KEYS <= set(q), q["id"]
        assert q["question"].strip()
        assert q["forbidden_assertions"], f"{q['id']} must name what must not be asserted"
        for src in q["expected_sources"]:
            assert set(src) == {"type", "id"}


def test_categories_are_finite_and_each_has_at_least_two(questions, harness):
    counts = Counter(q["category"] for q in questions)
    assert set(counts) <= set(harness.CATEGORIES)
    for category in harness.CATEGORIES:
        assert counts[category] >= 2, category
    assert len(questions) >= 20


def test_automation_and_manual_reasons(questions, harness):
    automated = [q for q in questions if q["harness_support"] == "automated"]
    assert len(automated) >= 18
    for q in questions:
        assert q["harness_support"] in harness.HARNESS_SUPPORT
        if q["harness_support"] == "manual_only":
            assert q.get("harness_reason"), f"{q['id']} needs a reason"


def test_screens_scripts_fixtures_are_known(questions, harness):
    from app.assistant import SCREENS_BY_ID

    for q in questions:
        assert q["screen_id"] in SCREENS_BY_ID, q["id"]
        assert q["llm_script"] in harness.KNOWN_SCRIPTS, q["id"]
        assert q["fixture"] in harness.KNOWN_FIXTURES, q["id"]
        assert q["fixture"] in harness.FIXTURE_SETUP
        if q.get("after_setup"):
            assert q["after_setup"] in harness.KNOWN_MUTATIONS
            assert q.get("thread"), "a revision-change question needs a thread"


def test_required_scenarios_are_covered(questions):
    scripts = {q["llm_script"] for q in questions}
    assert {"timeout", "http_429", "malformed_json", "truncated",
            "invalid_citation", "no_citation_fact", "no_llm"} <= scripts
    assert any(q.get("ui_draft") for q in questions), "unsaved-draft case"
    assert any(q.get("after_setup") for q in questions), "stale-thread case"
    assert any(q.get("conversation_setup") for q in questions), "follow-up case"
    assert any(q["fixture"] == "journeys_many" for q in questions), "truncated list case"
    assert any(q["fixture"] == "overview_read_failure" for q in questions), "read failure case"
    assert any("無視" in q["question"] for q in questions), "injected instruction case"


def test_nearest_rank_and_unmeasured(harness):
    assert harness.nearest_rank([], 95) is None
    assert harness.nearest_rank([5, 1, 3, 2, 4], 50) == 3
    assert harness.nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert harness.dist([])["state"] == "unmeasured"
    assert harness.dist([])["p95_ms"] is None


def test_real_mode_refuses_without_max_calls(harness, capsys):
    assert harness.main(["--mode", "real"]) == 2
    assert "--max-calls" in capsys.readouterr().err


def test_scoring_template_columns(harness, questions, tmp_path):
    out = tmp_path / "scoring.csv"
    harness.write_scoring_template(questions, out)
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == harness.SCORING_COLUMNS
    assert len(rows) == len(questions) + 1


def test_structural_smoke_run(harness, questions, tmp_path):
    """Two grounded questions + one failing-LLM question end to end."""
    picked = [q for q in questions if q["id"] in ("Q05", "Q26")]
    assert len(picked) == 2
    out = tmp_path / "report.json"
    subset = tmp_path / "subset.json"
    subset.write_text(json.dumps({"questions": picked}, ensure_ascii=False), encoding="utf-8")
    assert harness.main(["--questions", str(subset), "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["mode"] == "structural"
    assert report["llm"] == "scripted_fake"
    assert report["summary"]["runs"] == 2
    by_id = {r["id"]: r for r in report["results"]}
    assert by_id["Q05"]["http_status"] == 200
    assert by_id["Q05"]["diagnostics_calls"] >= 1
    assert by_id["Q26"]["llm_calls"] == 1
    assert by_id["Q26"]["used_fallback"] is True
