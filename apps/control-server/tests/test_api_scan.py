"""Tests for LLM-assisted, framework-agnostic API definition scanning.

Covers the reasoning-llm contract (structured output, fail-closed on
mock/non-reasoning/error, audit persistence), the deterministic + bounded
application of model-authored regexes, System-scoped persistence of patterns and
extracted entrypoints, merge into the backend-entrypoint-first listing, and
target-repository safety.
"""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Unit tests: regex parsing / application (no LLM, no HTTP)
# ---------------------------------------------------------------------------


class TestParseScanResponse:
    def test_valid_pattern(self):
        from app.api_scan import parse_scan_response

        raw = json.dumps({"patterns": [{
            "file_glob": "**/urls.py",
            "regex": r"path\(\s*['\"](?P<path>[^'\"]+)['\"]",
            "path_group": "path",
            "method_constant": None,
            "framework": "django", "language": "python",
            "reason": "Django path() registration", "confidence": 0.8,
            "examples": [{"path": "app/urls.py", "line": 3}],
        }]})
        pats = parse_scan_response(raw)
        assert len(pats) == 1
        assert pats[0].framework == "django"
        assert pats[0].path_group == "path"

    def test_accepts_fenced_json_response(self):
        from app.api_scan import parse_scan_response

        raw = "```json\n" + json.dumps({"patterns": []}) + "\n```"
        assert parse_scan_response(raw) == []

    def test_accepts_trailing_text_after_json_object(self):
        from app.api_scan import parse_scan_response

        raw = json.dumps({"patterns": []}) + "\nNo API declarations were visible."
        assert parse_scan_response(raw) == []

    def test_accepts_preface_before_json_object(self):
        from app.api_scan import parse_scan_response

        raw = "Here is the JSON:\n" + json.dumps({"patterns": []})
        assert parse_scan_response(raw) == []

    def test_invalid_regex_rejected(self):
        from app.api_scan import ApiScanValidationError, parse_scan_response

        raw = json.dumps({"patterns": [{
            "file_glob": "*.py", "regex": "(unclosed", "path_group": "path",
            "framework": "x", "language": "y", "reason": "r", "confidence": 0.5,
        }]})
        with pytest.raises(ApiScanValidationError):
            parse_scan_response(raw)

    def test_missing_path_group_rejected(self):
        from app.api_scan import ApiScanValidationError, parse_scan_response

        raw = json.dumps({"patterns": [{
            "file_glob": "*.py", "regex": r"(?P<method>GET)", "method_group": "method",
            "framework": "x", "language": "y", "reason": "r", "confidence": 0.5,
        }]})
        with pytest.raises(ApiScanValidationError):
            parse_scan_response(raw)

    def test_unknown_named_group_rejected(self):
        from app.api_scan import ApiScanValidationError, parse_scan_response

        raw = json.dumps({"patterns": [{
            "file_glob": "*.py", "regex": r"(?P<route>/x)", "path_group": "path",
            "framework": "x", "language": "y", "reason": "r", "confidence": 0.5,
        }]})
        with pytest.raises(ApiScanValidationError):
            parse_scan_response(raw)

    def test_redos_signature_rejected(self):
        from app.api_scan import ApiScanValidationError, parse_scan_response

        raw = json.dumps({"patterns": [{
            "file_glob": "*.py", "regex": r"(?P<path>(a+)+)", "path_group": "path",
            "framework": "x", "language": "y", "reason": "r", "confidence": 0.5,
        }]})
        with pytest.raises(ApiScanValidationError):
            parse_scan_response(raw)

    def test_absolute_glob_rejected(self):
        from app.api_scan import ApiScanValidationError, parse_scan_response

        raw = json.dumps({"patterns": [{
            "file_glob": "/etc/passwd", "regex": r"(?P<path>/x)", "path_group": "path",
            "framework": "x", "language": "y", "reason": "r", "confidence": 0.5,
        }]})
        with pytest.raises(ApiScanValidationError):
            parse_scan_response(raw)


class TestApplyPatterns:
    def test_extracts_and_dedupes(self):
        from app.api_scan import ApiScanPattern, apply_patterns

        files = [(
            "app/urls.py",
            "urlpatterns = [\n"
            "    path('users/', views.list_users),\n"
            "    path('users/', views.list_users),\n"
            "    path('orders/', views.orders),\n"
            "]\n",
        )]
        pat = ApiScanPattern(
            file_glob="**/urls.py",
            regex=r"path\(\s*['\"](?P<path>[^'\"]+)['\"]",
            reason="r", confidence=0.8, framework="django", language="python",
            path_group="path",
        )
        eps, diags = apply_patterns([pat], files)
        ids = {e.entrypoint_id for e in eps}
        assert ids == {"ANY:/users", "ANY:/orders"}
        assert all(e.source == "reasoning_llm" and e.category == "api" for e in eps)

    def test_method_group_captured(self):
        from app.api_scan import ApiScanPattern, apply_patterns

        files = [("routes.js",
                  "app.get('/health', h)\napp.post('/login', h)\n")]
        pat = ApiScanPattern(
            file_glob="*.js",
            regex=r"app\.(?P<method>get|post)\(\s*['\"](?P<path>[^'\"]+)['\"]",
            reason="r", confidence=0.7, framework="express", language="javascript",
            method_group="method", path_group="path",
        )
        eps, _ = apply_patterns([pat], files)
        ids = {e.entrypoint_id for e in eps}
        assert ids == {"GET:/health", "POST:/login"}

    def test_glob_miss_diagnostic(self):
        from app.api_scan import ApiScanPattern, apply_patterns

        pat = ApiScanPattern(
            file_glob="**/nope.py", regex=r"(?P<path>/x)", reason="r",
            confidence=0.5, framework="x", language="y", path_group="path",
        )
        eps, diags = apply_patterns([pat], [("a.py", "x = 1\n")])
        assert eps == []
        assert any("matched no files" in d for d in diags)


class TestGenerateApiScanFailsClosed:
    def test_mock_provider_fails_closed(self):
        from app.api_scan import generate_api_scan
        from app.llm import LLMConfig, MockLLMClient

        cfg = LLMConfig(provider="mock", api_key=None, model="mock",
                        base_url=None, timeout=5)
        res = generate_api_scan(MockLLMClient(), cfg, "digest")
        assert res.is_mock is True
        assert res.patterns == []
        assert res.error and "mock" in res.error.lower()


# ---------------------------------------------------------------------------
# Integration: HTTP endpoint, persistence, isolation, repo safety
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "api-scan-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


class _ReasoningScanClient:
    """Returns a Django-style API regex as structured output."""

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps({"patterns": [{
            "file_glob": "**/urls.py",
            "regex": r"path\(\s*['\"](?P<path>[^'\"]+)['\"]",
            "path_group": "path",
            "method_constant": None,
            "framework": "django", "language": "python",
            "reason": "Django path() URL registration",
            "confidence": 0.85,
            "examples": [{"path": "myapp/urls.py", "line": 2}],
        }]})


def _enable_reasoning_scan(monkeypatch, client_cls=_ReasoningScanClient):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr(
        "app.routes.project_intelligence.create_llm_client",
        lambda config: client_cls(),
    )


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _git_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"],
                   check=True, capture_output=True)
    app_dir = path / "myapp"
    app_dir.mkdir()
    (app_dir / "urls.py").write_text(
        "urlpatterns = [\n"
        "    path('users/', views.list_users),\n"
        "    path('orders/', views.create_order),\n"
        "]\n"
    )
    (app_dir / "views.py").write_text(
        "def list_users(request):\n"
        "    return []\n"
        "\n"
        "\n"
        "def create_order(request):\n"
        "    return {}\n"
    )
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return path


def _setup_snapshot(client, token, system_id, repo):
    h = _headers(token, system_id)
    client.put("/repository", json={"repo_path": str(repo),
               "include_patterns": ["*.py"]}, headers=h)
    assert client.post("/repository/snapshots", headers=h).status_code == 201
    assert client.post("/repository/symbols/index", headers=h).status_code == 201
    return h


def _repo_head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class TestApiScanEndpoint:
    def test_successful_scan_persists_and_merges(self, admin_client, tmp_path, monkeypatch):
        repo = _git_repo(tmp_path / "repo")
        before = _repo_head(repo)
        _enable_reasoning_scan(monkeypatch)
        token = _login(admin_client)
        system = _create_system(admin_client, token, "s")
        h = _setup_snapshot(admin_client, token, system["id"], repo)

        r = admin_client.post("/repository/api-scan", headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "completed"
        assert data["is_mock"] is False
        assert data["decision_method"] == "reasoning_llm"
        assert "django" in data["frameworks"]
        assert len(data["patterns"]) == 1
        assert data["extracted_count"] == 2
        assert data["patterns"][0]["match_count"] == 2

        # Audit run + persisted rows.
        from app.db import get_conn

        with get_conn() as conn:
            run = conn.execute(
                "SELECT * FROM intelligence_runs WHERE system_id = ? "
                "AND run_type = 'api_scan'", (system["id"],),
            ).fetchone()
            eps = conn.execute(
                "SELECT * FROM code_entrypoints WHERE system_id = ? "
                "AND source = 'reasoning_llm'", (system["id"],),
            ).fetchall()
        assert run["decision_method"] == "reasoning_llm"
        assert {e["entrypoint_id"] for e in eps} == {"ANY:/users", "ANY:/orders"}

        # Merged into the backend-entrypoint-first listing as api/source=llm.
        flow = admin_client.get("/repository/flow-entrypoints", headers=h).json()
        api = [e for e in flow["entrypoints"] if e["category"] == "api"]
        assert {e["entrypoint_id"] for e in api} == {"ANY:/users", "ANY:/orders"}
        assert all(e["source"] == "reasoning_llm" for e in api)
        assert flow["has_backend_entrypoints"] is True

        # Target repository unchanged.
        assert _repo_head(repo) == before

    def test_rescan_replaces_prior_llm_rows(self, admin_client, tmp_path, monkeypatch):
        repo = _git_repo(tmp_path / "repo")
        _enable_reasoning_scan(monkeypatch)
        token = _login(admin_client)
        system = _create_system(admin_client, token, "s")
        h = _setup_snapshot(admin_client, token, system["id"], repo)

        admin_client.post("/repository/api-scan", headers=h)
        admin_client.post("/repository/api-scan", headers=h)

        from app.db import get_conn

        with get_conn() as conn:
            eps = conn.execute(
                "SELECT COUNT(*) AS c FROM code_entrypoints WHERE system_id = ? "
                "AND source = 'reasoning_llm'", (system["id"],),
            ).fetchone()
        assert eps["c"] == 2  # not duplicated

    def test_mock_provider_fails_closed(self, admin_client, tmp_path, monkeypatch):
        repo = _git_repo(tmp_path / "repo")
        token = _login(admin_client)  # LLM_PROVIDER stays "mock"
        system = _create_system(admin_client, token, "s")
        h = _setup_snapshot(admin_client, token, system["id"], repo)

        r = admin_client.post("/repository/api-scan", headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "failed"
        assert data["is_mock"] is True
        assert data["extracted_count"] == 0

        from app.db import get_conn

        with get_conn() as conn:
            eps = conn.execute(
                "SELECT COUNT(*) AS c FROM code_entrypoints WHERE system_id = ? "
                "AND source = 'reasoning_llm'", (system["id"],),
            ).fetchone()
        assert eps["c"] == 0

    def test_non_reasoning_model_rejected(self, admin_client, tmp_path, monkeypatch):
        repo = _git_repo(tmp_path / "repo")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")  # not a reasoning model
        monkeypatch.setenv("LLM_API_KEY", "unused")
        token = _login(admin_client)
        system = _create_system(admin_client, token, "s")
        h = _setup_snapshot(admin_client, token, system["id"], repo)

        r = admin_client.post("/repository/api-scan", headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "failed"
        assert "reasoning model" in (data["error"] or "")

    def test_stale_snapshot_after_repository_config_change_is_rejected(
        self, admin_client, tmp_path, monkeypatch,
    ):
        repo = _git_repo(tmp_path / "repo")
        _enable_reasoning_scan(monkeypatch)
        token = _login(admin_client)
        system = _create_system(admin_client, token, "s")
        h = _setup_snapshot(admin_client, token, system["id"], repo)

        # Updating include/exclude patterns does not mutate the already-created
        # snapshot. API scan must not silently inspect the stale file set.
        r = admin_client.put(
            "/repository",
            json={
                "repo_path": str(repo),
                "include_patterns": ["*.py", "backend/**"],
                "exclude_patterns": [],
            },
            headers=h,
        )
        assert r.status_code == 200, r.text

        r = admin_client.post("/repository/api-scan", headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "failed"
        assert "updated after the latest snapshot" in (data["error"] or "")
        assert data["extracted_count"] == 0

    def test_malformed_response_fails_without_persisting(self, admin_client, tmp_path, monkeypatch):
        class _Bad:
            def generate_text(self, messages, *, temperature=None, max_tokens=None):
                return "not json at all"

        repo = _git_repo(tmp_path / "repo")
        _enable_reasoning_scan(monkeypatch, client_cls=_Bad)
        token = _login(admin_client)
        system = _create_system(admin_client, token, "s")
        h = _setup_snapshot(admin_client, token, system["id"], repo)

        r = admin_client.post("/repository/api-scan", headers=h)
        data = r.json()
        assert data["status"] == "failed"
        assert data["extracted_count"] == 0
        assert data["patterns"] == []

    def test_scan_is_system_scoped(self, admin_client, tmp_path, monkeypatch):
        repo = _git_repo(tmp_path / "repo")
        _enable_reasoning_scan(monkeypatch)
        token = _login(admin_client)
        s1 = _create_system(admin_client, token, "one")
        h1 = _setup_snapshot(admin_client, token, s1["id"], repo)
        admin_client.post("/repository/api-scan", headers=h1)

        s2 = _create_system(admin_client, token, "two")
        h2 = _headers(token, s2["id"])
        got = admin_client.get("/repository/api-scan", headers=h2).json()
        assert got["status"] == "none"
        assert got["patterns"] == []

        from app.db import get_conn

        with get_conn() as conn:
            other = conn.execute(
                "SELECT COUNT(*) AS c FROM code_entrypoint_patterns WHERE system_id = ?",
                (s2["id"],),
            ).fetchone()
        assert other["c"] == 0
