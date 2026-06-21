"""Integration tests for Issue #26 Experiment Workspace Runner MVP."""

import os
import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-experiment-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.setenv(
        "PROBE_EXPERIMENT_WORKSPACE_BASE", str(tmp_path / "experiment-workspaces")
    )
    monkeypatch.setenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", "true")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def experiment_repo(tmp_path):
    repo = tmp_path / "experiment-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    (repo / "calc.py").write_text("def value():\n    return 1\n")
    (repo / "test_calc.py").write_text(
        "import calc\n\n"
        "def test_value():\n"
        "    assert calc.value() in (1, 2, 3)\n"
    )
    (repo / "workload.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "import calc\n\n"
        "path = Path('.probe-agent/experiment-result.json')\n"
        "path.parent.mkdir(exist_ok=True)\n"
        "path.write_text(json.dumps({\n"
        "  'traces': [{'output': calc.value()}],\n"
        "  'shadow_results': [],\n"
        "  'evaluations': [{'status': 'ok'}],\n"
        "  'safety_warnings': []\n"
        "}))\n"
    )
    (repo / "probe-agent.yml").write_text(
        "commands:\n"
        "  install: []\n"
        "  test:\n"
        "    - python -m pytest -q\n"
        "  smoke: []\n"
        "  workload:\n"
        "    - python workload.py\n"
        "runtime:\n"
        "  network: false\n"
        "  timeout_seconds: 30\n"
        "  env:\n"
        "    EXPERIMENT_FIXTURE: '1'\n"
        "experiment:\n"
        "  result_artifact_path: .probe-agent/experiment-result.json\n"
        "  artifact_retention_seconds: 0\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    return repo


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _setup_snapshot(client, repo):
    token = _login(client)
    system_response = client.post(
        "/systems",
        json={"name": "experiment", "environment": "test"},
        headers=_headers(token),
    )
    assert system_response.status_code == 201
    system = system_response.json()
    headers = _headers(token, system["id"])
    config_response = client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": []},
        headers=headers,
    )
    assert config_response.status_code == 200
    snapshot_response = client.post("/repository/snapshots", headers=headers)
    assert snapshot_response.status_code == 201
    return headers, snapshot_response.json()


def _patch(value):
    return (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def value():\n"
        f"-    return 1\n+    return {value}\n"
    )


def _create_payload(snapshot_id, second_patch=None):
    return {
        "feature_id": "calculator",
        "objective": "compare calculator outputs",
        "snapshot_id": snapshot_id,
        "variants": [
            {
                "label": "return two",
                "patch_text": _patch(2),
                "source": "test",
                "risk_note": "changes numeric output",
            },
            {
                "label": "return three",
                "patch_text": second_patch or _patch(3),
                "source": "test",
                "risk_note": "changes numeric output",
            },
        ],
    }


def test_runs_baseline_and_two_variants_in_isolated_workspaces(
    admin_client, experiment_repo
):
    headers, snapshot = _setup_snapshot(admin_client, experiment_repo)
    create_response = admin_client.post(
        "/experiments",
        json=_create_payload(snapshot["id"]),
        headers=headers,
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert len(created["variants"]) == 3
    assert created["variants"][0]["variant_key"] == "baseline"
    assert created["execution"]["network"] is False
    assert created["execution"]["test_commands"] == ["python -m pytest -q"]
    assert created["execution"]["workload_commands"] == ["python workload.py"]
    assert len(created["config_revision"]) == 64

    run_response = admin_client.post(
        f"/experiments/{created['id']}/run", headers=headers
    )
    assert run_response.status_code == 200, run_response.text
    result = run_response.json()
    assert result["status"] == "completed"
    assert [item["status"] for item in result["variants"]] == [
        "completed",
        "completed",
        "completed",
    ]
    assert [
        item["metrics"]["trace_outputs"] for item in result["variants"]
    ] == [[1], [2], [3]]
    assert all(
        item["metrics"]["test_pass_rate"] == 1.0 for item in result["variants"]
    )
    assert all(item["cleanup_state"] == "removed" for item in result["variants"])
    assert all(
        not os.path.exists(item["workspace_path"]) for item in result["variants"]
    )
    assert result["analysis"]["status"] == "analysis_failed"
    assert result["analysis"]["recommendation_variant_key"] is None
    assert result["human_decision"] == "undecided"
    assert (experiment_repo / "calc.py").read_text() == "def value():\n    return 1\n"

    decision_response = admin_client.put(
        f"/experiments/{created['id']}/decision",
        json={
            "decision": "adopted",
            "variant_key": "variant-1",
            "note": "human reviewed variant-1",
        },
        headers=headers,
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["human_decision"] == "adopted"
    assert (
        decision_response.json()["human_decision_variant_key"] == "variant-1"
    )
    assert decision_response.json()["analysis"]["status"] == "analysis_failed"

    cleanup_response = admin_client.post("/experiments/cleanup", headers=headers)
    assert cleanup_response.status_code == 200
    assert cleanup_response.json()["cleaned_experiments"] == 1
    after_cleanup = admin_client.get(
        f"/experiments/{created['id']}", headers=headers
    ).json()
    assert all(item["artifacts"] == {} for item in after_cleanup["variants"])
    assert all(
        command["stdout"] == "" and command["stderr"] == ""
        for item in after_cleanup["variants"]
        for command in item["commands"]
    )


def test_invalid_patch_fails_only_that_variant(admin_client, experiment_repo):
    headers, snapshot = _setup_snapshot(admin_client, experiment_repo)
    payload = _create_payload(snapshot["id"], second_patch="not a unified diff")
    created = admin_client.post(
        "/experiments", json=payload, headers=headers
    ).json()
    result = admin_client.post(
        f"/experiments/{created['id']}/run", headers=headers
    ).json()
    statuses = {item["variant_key"]: item["status"] for item in result["variants"]}
    assert statuses == {
        "baseline": "completed",
        "variant-1": "completed",
        "variant-2": "invalid_patch",
    }
    assert result["status"] == "completed"
    assert result["variants"][2]["error"]
    assert (experiment_repo / "calc.py").read_text() == "def value():\n    return 1\n"


def test_rejects_api_supplied_commands(admin_client, experiment_repo):
    headers, snapshot = _setup_snapshot(admin_client, experiment_repo)
    payload = _create_payload(snapshot["id"])
    payload["execution"] = {
        "test_commands": ["rm -rf /"],
        "network": True,
    }
    response = admin_client.post("/experiments", json=payload, headers=headers)
    assert response.status_code == 422


def test_rejects_network_enabled_pinned_config(admin_client, experiment_repo):
    config_path = experiment_repo / "probe-agent.yml"
    config_path.write_text(config_path.read_text().replace("network: false", "network: true"))
    subprocess.run(
        ["git", "-C", str(experiment_repo), "add", "probe-agent.yml"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(experiment_repo), "commit", "-m", "enable network"],
        check=True,
        capture_output=True,
    )
    headers, snapshot = _setup_snapshot(admin_client, experiment_repo)
    response = admin_client.post(
        "/experiments",
        json=_create_payload(snapshot["id"]),
        headers=headers,
    )
    assert response.status_code == 400
    assert "network must be false" in response.json()["detail"]


def test_adoption_requires_completed_run_and_note(admin_client, experiment_repo):
    headers, snapshot = _setup_snapshot(admin_client, experiment_repo)
    experiment = admin_client.post(
        "/experiments",
        json=_create_payload(snapshot["id"]),
        headers=headers,
    ).json()
    response = admin_client.put(
        f"/experiments/{experiment['id']}/decision",
        json={
            "decision": "adopted",
            "variant_key": "variant-1",
            "note": "premature",
        },
        headers=headers,
    )
    assert response.status_code == 409


def test_adoption_requires_completed_non_baseline_variant(
    admin_client, experiment_repo
):
    headers, snapshot = _setup_snapshot(admin_client, experiment_repo)
    experiment = admin_client.post(
        "/experiments",
        json=_create_payload(snapshot["id"]),
        headers=headers,
    ).json()
    completed = admin_client.post(
        f"/experiments/{experiment['id']}/run", headers=headers
    ).json()

    missing_variant = admin_client.put(
        f"/experiments/{completed['id']}/decision",
        json={"decision": "adopted", "note": "reviewed"},
        headers=headers,
    )
    assert missing_variant.status_code == 422

    baseline = admin_client.put(
        f"/experiments/{completed['id']}/decision",
        json={
            "decision": "adopted",
            "variant_key": "baseline",
            "note": "reviewed",
        },
        headers=headers,
    )
    assert baseline.status_code == 422
