"""Experiment interpretation (sim-sandbox E-0010: IK-0029..IK-0031)."""

import json

import pytest

from test_experiments import (  # noqa: F401  (fixtures)
    _create_payload,
    _setup_snapshot,
    admin_client,
    experiment_repo,
)


class AnalysisClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []
        self.max_tokens = []
        self.last_usage = {"input_tokens": 5, "output_tokens": 3}
        self.last_finish_reason = "stop"

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.prompts.append(messages)
        self.max_tokens.append(max_tokens)
        value = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def use_analysis_client(monkeypatch, client):
    from app.llm import LLMConfig

    monkeypatch.setattr(
        "app.routes.experiments.LLMConfig.intelligence_from_env",
        classmethod(lambda cls: LLMConfig(provider="openai", model="gpt-5", api_key="unused", base_url=None, timeout=30.0)),
    )
    monkeypatch.setattr("app.routes.experiments.create_llm_client", lambda config: client)
    return client


GOOD = {
    "narrative": "variant-1 returns two.",
    "recommendation_variant_key": "variant-1",
    "recommendation_reason": "closest to objective",
    "risks": ["only one trace"],
}


@pytest.fixture
def ran_experiment(admin_client, experiment_repo, monkeypatch):
    headers, snapshot = _setup_snapshot(admin_client, experiment_repo)
    created = admin_client.post(
        "/experiments", json=_create_payload(snapshot["id"]), headers=headers
    )
    assert created.status_code == 201, created.text
    experiment_id = created.json()["id"]
    use_analysis_client(monkeypatch, AnalysisClient(GOOD))
    ran = admin_client.post(f"/experiments/{experiment_id}/run", headers=headers)
    assert ran.status_code == 200, ran.text
    return headers, ran.json()


def test_analysis_is_recorded_as_a_run_with_payloads(admin_client, ran_experiment):
    headers, experiment = ran_experiment
    assert experiment["analysis"]["status"] == "completed"

    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT intelligence_run_id FROM experiment_analyses WHERE experiment_id = ?",
            (experiment["id"],),
        ).fetchone()
    run_id = row["intelligence_run_id"]
    assert run_id is not None
    stored = admin_client.get(f"/intelligence-runs/{run_id}/payloads", headers=headers).json()
    assert stored["intelligence_run"]["run_type"] == "experiment_analysis"
    assert [p["kind"] for p in stored["payloads"]] == ["request", "response"]
    assert "compare calculator outputs" in stored["payloads"][0]["text"]
    assert json.loads(stored["payloads"][1]["text"])["narrative"] == GOOD["narrative"]


def _variant_state(experiment):
    return [
        (v["variant_key"], v["status"], v["metrics"], v["completed_at"])
        for v in experiment["variants"]
    ]


def test_analyze_reinterprets_without_rerunning_and_keeps_history(
    admin_client, ran_experiment, monkeypatch
):
    headers, experiment = ran_experiment
    second = dict(GOOD, narrative="second reading", recommendation_variant_key="variant-2")
    use_analysis_client(monkeypatch, AnalysisClient(second))

    r = admin_client.post(f"/experiments/{experiment['id']}/analyze", headers=headers)
    assert r.status_code == 200, r.text
    analyzed = r.json()

    assert _variant_state(analyzed) == _variant_state(experiment)
    assert analyzed["analysis"]["narrative"] == "second reading"
    history = analyzed["analysis_history"]
    assert [h["narrative"] for h in history] == ["second reading", GOOD["narrative"]]
    assert history[0]["version_id"] == analyzed["analysis"]["version_id"]
    assert history[0]["version_id"] > history[1]["version_id"]
    assert history[0]["intelligence_run_id"] != history[1]["intelligence_run_id"]

    fetched = admin_client.get(f"/experiments/{experiment['id']}", headers=headers).json()
    assert len(fetched["analysis_history"]) == 2


def test_rerun_keeps_previous_analysis_in_history(admin_client, ran_experiment, monkeypatch):
    headers, experiment = ran_experiment
    use_analysis_client(monkeypatch, AnalysisClient(dict(GOOD, narrative="after rerun")))
    rerun = admin_client.post(f"/experiments/{experiment['id']}/run", headers=headers).json()
    assert [h["narrative"] for h in rerun["analysis_history"]] == [
        "after rerun",
        GOOD["narrative"],
    ]


def test_analyze_requires_a_finished_experiment(
    admin_client, experiment_repo, monkeypatch
):
    headers, snapshot = _setup_snapshot(admin_client, experiment_repo)
    created = admin_client.post(
        "/experiments", json=_create_payload(snapshot["id"]), headers=headers
    ).json()
    use_analysis_client(monkeypatch, AnalysisClient(GOOD))
    r = admin_client.post(f"/experiments/{created['id']}/analyze", headers=headers)
    assert r.status_code == 409
    assert admin_client.post("/experiments/99999/analyze", headers=headers).status_code == 404


def test_legacy_unversioned_analysis_is_archived_before_reanalysis(
    admin_client, ran_experiment, monkeypatch
):
    headers, experiment = ran_experiment
    from app.db import get_conn

    # Shape of an analysis written before versioning existed.
    with get_conn() as conn:
        conn.execute("DELETE FROM experiment_analysis_versions WHERE experiment_id = ?", (experiment["id"],))
        conn.execute(
            """UPDATE experiment_analyses
               SET status = 'analysis_failed', narrative = NULL,
                   error = 'risks must be an array', version_id = NULL,
                   intelligence_run_id = NULL, notes_json = NULL
               WHERE experiment_id = ?""",
            (experiment["id"],),
        )
    use_analysis_client(monkeypatch, AnalysisClient(GOOD))
    analyzed = admin_client.post(
        f"/experiments/{experiment['id']}/analyze", headers=headers
    ).json()
    history = analyzed["analysis_history"]
    assert [h["status"] for h in history] == ["completed", "analysis_failed"]
    assert history[1]["error"] == "risks must be an array"
    assert history[1]["intelligence_run_id"] is None


def test_prompt_carries_variant_meaning_and_output_language(
    admin_client, ran_experiment, monkeypatch
):
    headers, experiment = ran_experiment
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET objective = ? WHERE id = ?",
            ("相関長を変えたときの比を比べる", experiment["id"]),
        )
    client = use_analysis_client(monkeypatch, AnalysisClient(GOOD))
    admin_client.post(f"/experiments/{experiment['id']}/analyze", headers=headers)

    system, user = client.prompts[0]
    assert "Japanese" in system["content"]
    assert "do not infer the meaning of a variant from its label alone" in system["content"]
    payload = json.loads(user["content"])
    assert payload["output_language"] == "Japanese"
    variants = {v["variant_key"]: v for v in payload["variants"]}
    assert variants["variant-1"]["risk_note"] == "changes numeric output"
    assert variants["variant-1"]["source"] == "test"
    assert "+    return 2" in variants["variant-1"]["patch_excerpt"]
    assert variants["baseline"]["is_baseline"] is True


def test_output_language_detection():
    from app.routes.experiments import _output_language

    assert _output_language("compare calculator outputs") == "English"
    assert _output_language("[E-0009 再現] DCF 比の比較") == "Japanese"
    assert _output_language("") == "English"
