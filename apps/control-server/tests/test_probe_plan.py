"""Tests for Issue #25: Probe Plan and temporary instrumentation patch MVP.

Covers: probe plan generation with reasoning model, mock provider rejection,
non-reasoning model rejection, safety denylist enforcement, point approval/
rejection, patch generation from approved points only, worktree isolation,
validation runner with baseline/probed comparison, system scoping, and audit
trail persistence.
"""

import json
import os
import subprocess
import textwrap
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-plan-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )

    (repo / "README.md").write_text("# Test Project\n\nA test project.\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text(textwrap.dedent("""\
        from pydantic import BaseModel
        from fastapi import APIRouter

        router = APIRouter()


        class UserRequest(BaseModel):
            name: str
            email: str


        @router.get("/users")
        def list_users():
            return []


        @router.post("/users")
        async def create_user(req: UserRequest):
            return {"name": req.name}


        def helper():
            pass
    """))
    (repo / "src" / "utils.py").write_text(textwrap.dedent("""\
        def summarize(text: str) -> str:
            return text[:80]


        def classify(item: dict) -> str:
            return item.get("type", "unknown")
    """))
    (repo / "src" / "mailer.py").write_text(textwrap.dedent("""\
        def send_email(to: str, subject: str, body: str) -> bool:
            return True
    """))
    (repo / "tests").mkdir()
    (repo / "tests" / "test_main.py").write_text(textwrap.dedent("""\
        def test_list_users():
            assert True
    """))
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("# Design\n\nFeature overview.\n")

    (repo / "probe-agent.yml").write_text(textwrap.dedent("""\
        commands:
          install:
            - echo "installing"
          test:
            - echo "tests pass"
          smoke:
            - echo "smoke ok"
        runtime:
          timeout_seconds: 30
          network: true
    """))

    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True, capture_output=True,
    )
    return repo


class _ReasoningProbePlanClient:
    """Fake LLM client that returns a valid probe plan response."""

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps({
            "objective": "Observe user listing for latency and correctness.",
            "probe_points": [
                {
                    "component_id": "user-list-observer",
                    "symbol_qualified_name": "list_users",
                    "symbol_path": "src/main.py",
                    "reason": "Central query path for the user feature.",
                    "recommended_mode": "trace",
                    "side_effect_risk": "none",
                    "replayability": "safe",
                },
            ],
            "avoid_reasons": [
                "helper() is a trivial utility with no observability value.",
            ],
        })


class _ReasoningDenylistHitClient:
    """Fake LLM client that returns a probe point hitting the denylist.

    Uses the symbol ``send_email`` which exists in the git_repo_with_denylist
    fixture and maps to a denylist keyword.
    """

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps({
            "objective": "Observe email sending.",
            "probe_points": [
                {
                    "component_id": "email-observer",
                    "symbol_qualified_name": "send_email",
                    "symbol_path": "src/mailer.py",
                    "reason": "Core email logic.",
                    "recommended_mode": "shadow",
                    "side_effect_risk": "none",
                    "replayability": "safe",
                },
            ],
            "avoid_reasons": [],
        })


class _ReasoningCodeMappingClient:
    """Fake LLM client for generating code links."""

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps({
            "links": [
                {
                    "feature_id": "user-management",
                    "symbol_qualified_name": "list_users",
                    "symbol_path": "src/main.py",
                    "relation_reason": "Implements user listing.",
                    "confidence": 0.9,
                },
                {
                    "feature_id": "user-management",
                    "symbol_qualified_name": "send_email",
                    "symbol_path": "src/mailer.py",
                    "relation_reason": "Sends notification emails.",
                    "confidence": 0.8,
                },
            ]
        })


class _ReasoningDraftClient:
    """Fake LLM client for generating drafts."""

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps({
            "system_profile": {
                "name": "Test System",
                "purpose": "Testing",
                "target_users": ["developers"],
                "stakeholder_value": "Test value",
                "constraints": [],
                "success_criteria": [],
                "evidence": [
                    {"path": "README.md", "start_line": 1, "end_line": 3, "summary": "Project readme"},
                ],
            },
            "features": [{
                "feature_id": "user-management",
                "name": "User Management",
                "summary": "Manages users.",
                "user_value": "User CRUD.",
                "success_criteria": ["Users can be listed"],
                "risks": [],
                "evidence": [
                    {"path": "src/main.py", "start_line": 1, "end_line": 10, "summary": "User routes"},
                ],
            }],
        })


def _enable_reasoning(monkeypatch, client_class):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr(
        "app.routes.project_intelligence.create_llm_client",
        lambda config: client_class(),
    )


def _setup_full_pipeline(client, token, system_id, repo_path, monkeypatch):
    """Configure repo, snapshot, drafts, symbols, code links, accept links."""
    h = _headers(token, system_id)

    client.put(
        "/repository",
        json={
            "repo_path": str(repo_path),
            "include_patterns": ["README.md", "src/**", "docs/**", "tests/**"],
        },
        headers=h,
    )
    client.post("/repository/snapshots", headers=h)

    _enable_reasoning(monkeypatch, _ReasoningDraftClient)
    from app.llm import get_llm_client
    get_llm_client.cache_clear()
    client.post("/repository/drafts/generate", headers=h)

    _enable_reasoning(monkeypatch, _ReasoningCodeMappingClient)
    get_llm_client.cache_clear()
    client.post("/repository/symbols/index", headers=h)

    r = client.post("/repository/code-links/generate", headers=h)
    assert r.status_code == 201, r.text
    links_data = r.json()
    for link in links_data.get("links", []):
        client.put(
            f"/repository/code-links/{link['id']}/review",
            json={"review_status": "accepted"},
            headers=h,
        )
    return h


# ---------------------------------------------------------------------------
# Unit tests: probe_planner
# ---------------------------------------------------------------------------


class TestSafetyDenylist:
    def test_known_keywords_are_caught(self):
        from app.probe_planner import check_denylist

        assert check_denylist("process_payment", None) is not None
        assert check_denylist("send_email", None) is not None
        assert check_denylist("delete_file", None) is not None
        assert check_denylist("db_write_handler", None) is not None

    def test_safe_symbols_pass(self):
        from app.probe_planner import check_denylist

        assert check_denylist("list_users", None) is None
        assert check_denylist("summarize_text", None) is None
        assert check_denylist("classify_item", None) is None
        assert check_denylist("display_user", None) is None

    def test_docstring_is_checked(self):
        from app.probe_planner import check_denylist

        assert check_denylist("do_thing", "calls send_email internally") is not None
        assert check_denylist("do_thing", "handles charge for subscription") is not None

    def test_denylist_patterns(self):
        from app.probe_planner import check_denylist

        assert check_denylist("stripe_charge", None) is not None
        assert check_denylist("paypal_refund", None) is not None


# ---------------------------------------------------------------------------
# Unit tests: patch_generator
# ---------------------------------------------------------------------------


class TestInstrumentFile:
    def test_inserts_probe_decorator(self):
        from app.patch_generator import ApprovedPoint, instrument_file

        source = textwrap.dedent("""\
            def my_func():
                return 42
        """)
        point = ApprovedPoint(
            component_id="test-comp",
            path="test.py",
            symbol="my_func",
            recommended_mode="trace",
            line_start=1,
            line_end=2,
        )
        patched, skipped = instrument_file(source, [point])
        assert '@probe(component_id="test-comp")' in patched
        assert "from probe_agent import probe" in patched
        assert not skipped

    def test_skips_already_decorated(self):
        from app.patch_generator import ApprovedPoint, instrument_file

        source = textwrap.dedent("""\
            from probe_agent import probe

            @probe(component_id="existing")
            def my_func():
                return 42
        """)
        point = ApprovedPoint(
            component_id="test-comp",
            path="test.py",
            symbol="my_func",
            recommended_mode="trace",
            line_start=4,
            line_end=5,
        )
        patched, skipped = instrument_file(source, [point])
        assert patched == source
        assert len(skipped) == 1
        assert "already has @probe" in skipped[0]

    def test_handles_class_method(self):
        from app.patch_generator import ApprovedPoint, instrument_file

        source = textwrap.dedent("""\
            class MyClass:
                def my_method(self):
                    return 42
        """)
        point = ApprovedPoint(
            component_id="cls-comp",
            path="test.py",
            symbol="MyClass.my_method",
            recommended_mode="trace",
            line_start=2,
            line_end=3,
        )
        patched, skipped = instrument_file(source, [point])
        assert '@probe(component_id="cls-comp")' in patched
        assert not skipped

    def test_skips_missing_symbol(self):
        from app.patch_generator import ApprovedPoint, instrument_file

        source = "def existing():\n    pass\n"
        point = ApprovedPoint(
            component_id="missing-comp",
            path="test.py",
            symbol="nonexistent",
            recommended_mode="trace",
            line_start=1,
            line_end=2,
        )
        patched, skipped = instrument_file(source, [point])
        assert patched == source
        assert len(skipped) == 1
        assert "not found" in skipped[0]

    def test_does_not_duplicate_import(self):
        from app.patch_generator import ApprovedPoint, instrument_file

        source = textwrap.dedent("""\
            from probe_agent import probe

            def my_func():
                return 42
        """)
        point = ApprovedPoint(
            component_id="test-comp",
            path="test.py",
            symbol="my_func",
            recommended_mode="trace",
            line_start=3,
            line_end=4,
        )
        patched, skipped = instrument_file(source, [point])
        assert patched.count("from probe_agent import probe") == 1

    def test_plain_module_import_does_not_count_as_probe_binding(self):
        from app.patch_generator import ApprovedPoint, instrument_file

        source = "import probe_agent\n\ndef my_func():\n    return 42\n"
        point = ApprovedPoint(
            component_id="test-comp",
            path="test.py",
            symbol="my_func",
            recommended_mode="trace",
            line_start=3,
            line_end=4,
        )
        patched, _ = instrument_file(source, [point])
        assert "from probe_agent import probe" in patched

    def test_preserves_module_docstring_position(self):
        from app.patch_generator import ApprovedPoint, instrument_file

        source = '"""module docs"""\n\ndef my_func():\n    return 42\n'
        point = ApprovedPoint(
            component_id="test-comp",
            path="test.py",
            symbol="my_func",
            recommended_mode="trace",
            line_start=3,
            line_end=4,
        )
        patched, _ = instrument_file(source, [point])
        assert patched.startswith('"""module docs"""\n')


# ---------------------------------------------------------------------------
# Unit tests: validation_runner
# ---------------------------------------------------------------------------


class TestValidationConfig:
    def test_loads_valid_config(self, tmp_path):
        from app.validation_runner import load_validation_config

        config_file = tmp_path / "probe-agent.yml"
        config_file.write_text(textwrap.dedent("""\
            commands:
              install:
                - pip install -r requirements.txt
              test:
                - pytest
              smoke:
                - echo ok
            runtime:
              timeout_seconds: 120
              network: false
        """))
        config = load_validation_config(str(config_file))
        assert config.install_commands == ["pip install -r requirements.txt"]
        assert config.test_commands == ["pytest"]
        assert config.smoke_commands == ["echo ok"]
        assert config.timeout_seconds == 120
        assert config.network is False

    def test_timeout_capped_at_300(self, tmp_path):
        from app.validation_runner import load_validation_config

        config_file = tmp_path / "probe-agent.yml"
        config_file.write_text(textwrap.dedent("""\
            commands:
              test:
                - pytest
            runtime:
              timeout_seconds: 999
        """))
        config = load_validation_config(str(config_file))
        assert config.timeout_seconds == 300

    def test_missing_file_raises(self, tmp_path):
        from app.validation_runner import load_validation_config
        from app.git_ops import GitError

        with pytest.raises(GitError, match="not found"):
            load_validation_config(str(tmp_path / "missing.yml"))


class TestRunCommand:
    def test_runs_successful_command(self, tmp_path):
        from app.validation_runner import _run_command

        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        result = _run_command("echo hello", str(tmp_path), env, 30)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_captures_failing_command(self, tmp_path):
        from app.validation_runner import _run_command

        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        result = _run_command("exit 1", str(tmp_path), env, 30)
        assert result.exit_code == 1


class TestRunValidation:
    def test_runs_all_phases(self, tmp_path):
        from app.validation_runner import ValidationConfig, run_validation

        config = ValidationConfig(
            install_commands=["echo install"],
            test_commands=["echo test"],
            smoke_commands=["echo smoke"],
            timeout_seconds=30,
            network=True,
        )
        result = run_validation("baseline", str(tmp_path), config)
        assert result.overall_success is True
        assert len(result.results) == 3
        assert result.variant == "baseline"

    def test_stops_on_test_failure(self, tmp_path):
        from app.validation_runner import ValidationConfig, run_validation

        config = ValidationConfig(
            install_commands=["echo install"],
            test_commands=["exit 1"],
            smoke_commands=["echo smoke"],
            timeout_seconds=30,
            network=True,
        )
        result = run_validation("probed", str(tmp_path), config)
        assert result.overall_success is False
        assert len(result.results) == 2  # install + failed test, no smoke

    def test_requires_test_commands(self, tmp_path):
        from app.validation_runner import ValidationConfig, run_validation

        config = ValidationConfig(
            install_commands=["echo install"],
            test_commands=[],
            timeout_seconds=30,
            network=True,
        )
        result = run_validation("baseline", str(tmp_path), config)
        assert result.error is not None
        assert "No test commands" in result.error


# ---------------------------------------------------------------------------
# API tests: probe plan generation
# ---------------------------------------------------------------------------


class TestProbePlanGeneration:
    def test_mock_provider_fails(self, admin_client, git_repo, tmp_path, monkeypatch):
        """Mock provider must not fabricate probe plans."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "mock-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )

        monkeypatch.setenv("LLM_PROVIDER", "mock")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        from app.llm import MockLLMClient, get_llm_client
        get_llm_client.cache_clear()
        monkeypatch.setattr(
            "app.routes.project_intelligence.create_llm_client",
            lambda config: MockLLMClient(),
        )

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        assert r.status_code == 201
        data = r.json()
        run = data.get("intelligence_run", {})
        assert run.get("status") == "failed"
        assert run.get("is_mock") is True

    def test_generates_plan_with_reasoning_model(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "reasoning-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        assert r.status_code == 201
        data = r.json()
        assert data["feature_id"] == "user-management"
        assert data["objective"] != ""
        assert len(data["probe_points"]) >= 1
        assert len(data["avoid_reasons"]) >= 1

        run = data["intelligence_run"]
        assert run["status"] == "completed"
        assert run["decision_method"] == "reasoning_llm"
        assert run["run_type"] == "probe_plan"
        assert run["is_mock"] is False

        for point in data["probe_points"]:
            assert point["status"] == "proposed"
            assert point["recommended_mode"] in ("trace", "shadow")

    def test_denylist_overrides_llm_risk(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        """Safety denylist must override LLM's side_effect_risk assessment."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "denylist-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )

        _enable_reasoning(monkeypatch, _ReasoningDenylistHitClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        assert r.status_code == 201
        data = r.json()
        points = data["probe_points"]
        assert len(points) == 1
        point = points[0]
        assert point["denylist_hit"] is not None
        assert point["side_effect_risk"] == "high"

    def test_lists_plans(self, admin_client, git_repo, tmp_path, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "list-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )

        r = admin_client.get("/repository/probe-plans", headers=h)
        assert r.status_code == 200
        data = r.json()
        assert len(data["plans"]) >= 1

    def test_get_single_plan(self, admin_client, git_repo, tmp_path, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "single-plan-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        plan_id = r.json()["id"]

        r = admin_client.get(f"/repository/probe-plans/{plan_id}", headers=h)
        assert r.status_code == 200
        assert r.json()["id"] == plan_id


# ---------------------------------------------------------------------------
# API tests: probe point approval/rejection
# ---------------------------------------------------------------------------


class TestProbePointApproval:
    def test_approve_and_reject_points(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "approval-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        plan = r.json()
        points = plan["probe_points"]
        assert len(points) >= 1
        assert all(p["status"] == "proposed" for p in points)

        point_id = points[0]["id"]
        r = admin_client.put(
            f"/repository/probe-points/{point_id}/status",
            json={"status": "approved"},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

        r = admin_client.put(
            f"/repository/probe-points/{point_id}/status",
            json={"status": "rejected"},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_invalid_point_id_404(self, admin_client, tmp_path, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "invalid-point-test")
        h = _headers(token, system["id"])

        r = admin_client.put(
            "/repository/probe-points/999/status",
            json={"status": "approved"},
            headers=h,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# API tests: patch generation
# ---------------------------------------------------------------------------


class TestPatchGeneration:
    def test_generates_patch_from_approved_points(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "patch-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )
        monkeypatch.setenv("PROBE_WORKTREE_BASE", str(tmp_path / "worktrees"))

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        plan = r.json()

        for point in plan["probe_points"]:
            admin_client.put(
                f"/repository/probe-points/{point['id']}/status",
                json={"status": "approved"},
                headers=h,
            )

        r = admin_client.post(
            f"/repository/probe-plans/{plan['id']}/patch",
            headers=h,
        )
        assert r.status_code == 201
        patch = r.json()
        assert patch["status"] == "generated"
        assert "@probe" in patch["diff"]
        assert "probe_agent" in patch["diff"]

    def test_requires_approved_points(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "no-approval-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        plan = r.json()

        r = admin_client.post(
            f"/repository/probe-plans/{plan['id']}/patch",
            headers=h,
        )
        assert r.status_code == 400
        assert "approved" in r.json()["detail"].lower()

    def test_target_repo_unchanged(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        """Patch generation must not modify the target repository."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "safety-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )
        monkeypatch.setenv("PROBE_WORKTREE_BASE", str(tmp_path / "worktrees"))

        original_sha = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        original_diff = subprocess.run(
            ["git", "-C", str(git_repo), "diff"],
            capture_output=True, text=True,
        ).stdout.strip()

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        plan = r.json()

        for point in plan["probe_points"]:
            admin_client.put(
                f"/repository/probe-points/{point['id']}/status",
                json={"status": "approved"},
                headers=h,
            )

        admin_client.post(
            f"/repository/probe-plans/{plan['id']}/patch",
            headers=h,
        )

        after_sha = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        after_diff = subprocess.run(
            ["git", "-C", str(git_repo), "diff"],
            capture_output=True, text=True,
        ).stdout.strip()

        assert after_sha == original_sha
        assert after_diff == original_diff


# ---------------------------------------------------------------------------
# API tests: validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validates_patch(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "validate-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )
        monkeypatch.setenv("PROBE_WORKTREE_BASE", str(tmp_path / "worktrees"))

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        plan = r.json()

        for point in plan["probe_points"]:
            admin_client.put(
                f"/repository/probe-points/{point['id']}/status",
                json={"status": "approved"},
                headers=h,
            )

        r = admin_client.post(
            f"/repository/probe-plans/{plan['id']}/patch",
            headers=h,
        )
        patch = r.json()

        r = admin_client.post(
            f"/repository/probe-patches/{patch['id']}/validate",
            headers=h,
        )
        assert r.status_code == 201
        result = r.json()
        runs = result.get("validation_runs", [])
        assert len(runs) >= 1
        baseline = [r for r in runs if r["variant"] == "baseline"]
        probed = [r for r in runs if r["variant"] == "probed"]
        assert len(baseline) == 1
        assert len(probed) == 1
        assert baseline[0]["overall_success"] is True
        assert baseline[0]["cleanup_state"] == "removed"
        assert probed[0]["trace_status"] == "missing"
        for cmd in baseline[0]["commands"]:
            assert cmd["exit_code"] == 0

    def test_cannot_validate_failed_patch(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "failed-patch-test")
        h = _headers(token, system["id"])

        r = admin_client.post(
            "/repository/probe-patches/999/validate",
            headers=h,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# API tests: system scoping
# ---------------------------------------------------------------------------


class TestSystemScoping:
    def test_plans_are_system_scoped(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system1 = _create_system(admin_client, token, "system-1")
        system2 = _create_system(admin_client, token, "system-2")
        h1 = _setup_full_pipeline(
            admin_client, token, system1["id"], git_repo, monkeypatch,
        )

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h1,
        )

        h2 = _headers(token, system2["id"])
        r = admin_client.get("/repository/probe-plans", headers=h2)
        assert r.status_code == 200
        assert len(r.json()["plans"]) == 0

        r = admin_client.get("/repository/probe-plans", headers=h1)
        assert r.status_code == 200
        assert len(r.json()["plans"]) >= 1


# ---------------------------------------------------------------------------
# API tests: audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_intelligence_run_persisted(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "audit-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        data = r.json()
        run = data["intelligence_run"]

        assert run["run_type"] == "probe_plan"
        assert run["provider"] != ""
        assert run["model"] != ""
        assert run["decision_method"] == "reasoning_llm"
        assert run["prompt_version"] != ""
        assert run["schema_version"] != ""
        assert run["started_at"] > 0
        assert run["completed_at"] is not None
        assert run["completed_at"] >= run["started_at"]


# ---------------------------------------------------------------------------
# API tests: patches list endpoint
# ---------------------------------------------------------------------------


class TestPatchesList:
    def test_list_patches(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "patches-list-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )
        monkeypatch.setenv("PROBE_WORKTREE_BASE", str(tmp_path / "worktrees"))

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        r = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        )
        plan = r.json()

        for point in plan["probe_points"]:
            admin_client.put(
                f"/repository/probe-points/{point['id']}/status",
                json={"status": "approved"},
                headers=h,
            )

        admin_client.post(
            f"/repository/probe-plans/{plan['id']}/patch",
            headers=h,
        )

        r = admin_client.get("/repository/probe-patches", headers=h)
        assert r.status_code == 200
        patches = r.json()
        assert len(patches) >= 1
        assert patches[0]["plan_id"] == plan["id"]

    def test_download_patch(
        self, admin_client, git_repo, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "patch-download-test")
        h = _setup_full_pipeline(
            admin_client, token, system["id"], git_repo, monkeypatch,
        )
        monkeypatch.setenv("PROBE_WORKTREE_BASE", str(tmp_path / "worktrees"))

        _enable_reasoning(monkeypatch, _ReasoningProbePlanClient)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()

        plan = admin_client.post(
            "/repository/probe-plans/generate?feature_id=user-management",
            headers=h,
        ).json()
        admin_client.put(
            f"/repository/probe-points/{plan['probe_points'][0]['id']}/status",
            json={"status": "approved"},
            headers=h,
        )
        patch = admin_client.post(
            f"/repository/probe-plans/{plan['id']}/patch",
            headers=h,
        ).json()

        r = admin_client.get(
            f"/repository/probe-patches/{patch['id']}/download",
            headers=h,
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/x-diff")
        assert "@probe" in r.text
