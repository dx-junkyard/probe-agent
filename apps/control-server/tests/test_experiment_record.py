"""台帳に写す Experiment の記録（IK-0035）。"""

import pytest

from test_experiment_analysis import GOOD, AnalysisClient, use_analysis_client
from test_experiments import (  # noqa: F401  (fixtures)
    _create_payload,
    _headers,
    _login,
    _setup_snapshot,
    admin_client,
    experiment_repo,
)


def _run(admin_client, headers, payload, monkeypatch, analysis=GOOD):
    created = admin_client.post("/experiments", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    experiment_id = created.json()["id"]
    use_analysis_client(monkeypatch, AnalysisClient(analysis))
    ran = admin_client.post(f"/experiments/{experiment_id}/run", headers=headers)
    assert ran.status_code == 200, ran.text
    return ran.json()


@pytest.fixture
def setup(admin_client, experiment_repo):
    return _setup_snapshot(admin_client, experiment_repo)


def test_record_is_incomplete_until_a_human_decides(admin_client, setup, monkeypatch):
    headers, snapshot = setup
    experiment = _run(admin_client, headers, _create_payload(snapshot["id"]), monkeypatch)

    r = admin_client.get(f"/experiments/{experiment['id']}/record", headers=headers)
    assert r.status_code == 200, r.text
    record = r.json()
    assert record["tool"] == "probe-agent"
    assert record["run_ref"] == f"experiment:{experiment['id']}"
    assert record["url"] == ""
    assert record["executed_at"].endswith("+00:00")
    assert record["complete"] is False
    assert record["conditions"].splitlines() == [
        "Objective: compare calculator outputs",
        f"Snapshot: #{snapshot['id']} (baseline_commit {experiment['baseline_commit'][:7]})",
        "Variants:",
        "- Baseline (baseline, baseline variant)",
        "- return two (variant-1): changes numeric output",
        "- return three (variant-2): changes numeric output",
    ]
    assert "Interpretation" in record["result_summary"]
    assert GOOD["narrative"] in record["result_summary"]
    assert (
        "Recommendation (advisory): return two (variant-1): closest to objective"
        in record["result_summary"]
    )
    assert record["result_summary"].splitlines()[-1] == "Human decision: undecided"

    detail = record["detail"]
    assert detail["experiment_id"] == experiment["id"]
    assert detail["status"] == "completed"
    assert [v["variant_key"] for v in detail["variants"]] == [
        "baseline",
        "variant-1",
        "variant-2",
    ]
    assert all(v["test_pass_rate"] is not None for v in detail["variants"])
    assert detail["analysis_version"] == experiment["analysis"]["version_id"]
    assert detail["human_decision"] == "undecided"


def test_record_carries_decision_and_is_deterministic(admin_client, setup, monkeypatch):
    headers, snapshot = setup
    experiment = _run(admin_client, headers, _create_payload(snapshot["id"]), monkeypatch)
    decided = admin_client.put(
        f"/experiments/{experiment['id']}/decision",
        json={"decision": "adopted", "variant_key": "variant-2", "note": "three is right"},
        headers=headers,
    )
    assert decided.status_code == 200, decided.text

    first = admin_client.get(f"/experiments/{experiment['id']}/record", headers=headers)
    second = admin_client.get(f"/experiments/{experiment['id']}/record", headers=headers)
    assert first.status_code == 200, first.text
    assert first.content == second.content

    record = first.json()
    assert record["complete"] is True
    lines = record["result_summary"].splitlines()
    assert lines[-2:] == [
        "Human decision: adopted (return three (variant-2))",
        "Decision note: three is right",
    ]
    assert record["detail"]["human_decision_variant_key"] == "variant-2"


def test_record_text_follows_japanese_objective(admin_client, setup, monkeypatch):
    headers, snapshot = setup
    payload = _create_payload(snapshot["id"])
    payload["objective"] = "計算結果を比べる"
    payload["variants"][0]["risk_note"] = "戻り値を 2 に"
    experiment = _run(admin_client, headers, payload, monkeypatch)
    admin_client.put(
        f"/experiments/{experiment['id']}/decision",
        json={"decision": "needs_more_data", "note": "シードを増やす"},
        headers=headers,
    )

    record = admin_client.get(
        f"/experiments/{experiment['id']}/record", headers=headers
    ).json()
    conditions = record["conditions"].splitlines()
    assert conditions[0] == "目的: 計算結果を比べる"
    assert conditions[3] == "- Baseline（baseline・基準）"
    assert conditions[4] == "- return two（variant-1）: 戻り値を 2 に"
    summary = record["result_summary"].splitlines()
    assert summary[1] == "推奨（助言）: return two（variant-1）: closest to objective"
    assert summary[-2:] == ["人の判断: 追加データが必要", "判断メモ: シードを増やす"]
    assert record["complete"] is True


def test_record_without_completed_analysis_is_incomplete(admin_client, setup, monkeypatch):
    headers, snapshot = setup
    experiment = _run(
        admin_client, headers, _create_payload(snapshot["id"]), monkeypatch, analysis="not json"
    )
    assert experiment["analysis"]["status"] != "completed"
    admin_client.put(
        f"/experiments/{experiment['id']}/decision",
        json={"decision": "rejected", "note": "no"},
        headers=headers,
    )
    record = admin_client.get(
        f"/experiments/{experiment['id']}/record", headers=headers
    ).json()
    assert record["complete"] is False
    assert record["detail"]["analysis_version"] is None
    assert record["result_summary"].splitlines()[0].startswith("Interpretation: none (")


def test_record_url_uses_dashboard_base_when_configured(admin_client, setup, monkeypatch):
    headers, snapshot = setup
    experiment = _run(admin_client, headers, _create_payload(snapshot["id"]), monkeypatch)
    monkeypatch.setenv("PROBE_DASHBOARD_URL", "http://localhost:8501/")
    record = admin_client.get(
        f"/experiments/{experiment['id']}/record", headers=headers
    ).json()
    assert record["url"] == f"http://localhost:8501/experiments?experiment={experiment['id']}"


def test_record_is_404_for_missing_or_other_system(admin_client, setup, monkeypatch):
    headers, snapshot = setup
    experiment = _run(admin_client, headers, _create_payload(snapshot["id"]), monkeypatch)
    assert admin_client.get("/experiments/999999/record", headers=headers).status_code == 404

    token = _login(admin_client)
    other = admin_client.post(
        "/systems", json={"name": "other", "environment": "test"}, headers=_headers(token)
    )
    assert other.status_code == 201, other.text
    other_headers = _headers(token, other.json()["id"])
    r = admin_client.get(f"/experiments/{experiment['id']}/record", headers=other_headers)
    assert r.status_code == 404
