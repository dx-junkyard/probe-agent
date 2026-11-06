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
