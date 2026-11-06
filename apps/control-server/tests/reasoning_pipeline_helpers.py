"""Shared fixtures for the sim-sandbox E-0010 reasoning-run tests (IK-0029..0034).

A small repository, a logged-in admin client and fake reasoning clients that
return fixed JSON, so drafts / code-links / probe plans can be driven through
the real routes without a provider.
"""

import json
import subprocess
import textwrap

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "reasoning-runs.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.setenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", "true")
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    for args in (
        ["init", str(repo)],
        ["-C", str(repo), "config", "user.email", "test@test.com"],
        ["-C", str(repo), "config", "user.name", "Test"],
    ):
        subprocess.run(["git", *args], check=True, capture_output=True)
    (repo / "README.md").write_text("# Test Project\n\nA test project.\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text(textwrap.dedent("""\
        from fastapi import APIRouter

        router = APIRouter()


        @router.get("/users")
        def list_users():
            return []


        def helper():
            pass
    """))
    (repo / "src" / "mailer.py").write_text(textwrap.dedent("""\
        def notify(to: str) -> bool:
            return True
    """))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True, capture_output=True,
    )
    return repo


def login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def system_headers(client, name="ReasoningRuns"):
    token = login(client)
    bearer = {"Authorization": f"Bearer {token}"}
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": name},
        headers=bearer,
    )
    assert r.status_code == 201, r.text
    return {**bearer, "X-Probe-System-Id": str(r.json()["id"])}


def snapshot(client, headers, repo):
    r = client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": ["README.md", "src/**"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = client.post("/repository/snapshots", headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


class FixedClient:
    """Fake reasoning client: returns ``responses`` in order and records prompts."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []
        self.last_usage = {"input_tokens": 11, "output_tokens": 7}
        self.last_finish_reason = "stop"

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.prompts.append(messages)
        value = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return value if isinstance(value, str) else json.dumps(value)


def use_client(monkeypatch, client, module="app.routes.project_intelligence"):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr(f"{module}.create_llm_client", lambda config: client)
    return client


def draft_response(features=None):
    return {
        "system_profile": {
            "name": "Test System",
            "purpose": "Testing",
            "target_users": ["developers"],
            "stakeholder_value": "Test value",
            "constraints": [],
            "success_criteria": [],
            "evidence": [
                {"path": "README.md", "start_line": 1, "end_line": 3, "summary": "Readme"},
            ],
        },
        "features": features or [feature("user-management", "User Management")],
    }


def feature(feature_id, name, path="src/main.py"):
    return {
        "feature_id": feature_id,
        "name": name,
        "summary": f"{name} summary.",
        "user_value": f"{name} value.",
        "success_criteria": ["works"],
        "risks": [],
        "evidence": [{"path": path, "start_line": 1, "end_line": 5, "summary": name}],
    }


def link(feature_id, symbol, path="src/main.py", confidence=0.9):
    return {
        "feature_id": feature_id,
        "symbol_qualified_name": symbol,
        "symbol_path": path,
        "relation_reason": f"{symbol} implements {feature_id}.",
        "confidence": confidence,
    }
