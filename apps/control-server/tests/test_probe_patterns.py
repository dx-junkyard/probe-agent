"""Tests for Issue #168: Probe Pattern lifecycle.

Covers: deterministic instrumentation scan, pattern persistence with
pinned-snapshot facts, pre-release removal patches (isolation + explicit
apply boundary), reconciliation (deterministic classifications, reasoning
model requirement without heuristic fallback, audit persistence),
reconcile decisions, investigation assistance, plan creation from a
reconciliation, and system isolation.
"""

import json
import subprocess
import textwrap

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-pattern-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.setenv("PROBE_WORKTREE_BASE", str(tmp_path / "worktrees"))
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
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


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


V1_APP = textwrap.dedent("""\
    from probe_agent import probe


    @probe(component_id="total-calculator")
    def calculate_total(items: list) -> int:
        \"\"\"Sum item prices with tax.\"\"\"
        total = sum(i["price"] for i in items)
        return int(total * 1.1)


    @probe(component_id="receipt-formatter")
    def format_receipt(total: int) -> str:
        return f"Total: {total}"


    def helper():
        pass
""")

V1_UTIL = textwrap.dedent("""\
    from probe_agent import probe


    @probe(component_id="name-normalizer")
    def normalize_name(name: str) -> str:
        cleaned = name.strip()
        return cleaned.lower().replace("  ", " ")


    @probe(component_id="legacy-lookup")
    def legacy_lookup(key: str) -> str:
        table = {"a": "alpha", "b": "beta"}
        return table.get(key, "unknown")
""")


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text(V1_APP)
    (repo / "src" / "util.py").write_text(V1_UTIL)
    (repo / "README.md").write_text("# Target\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v1")
    return repo


def _setup_repo(client, h, repo):
    r = client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": ["src/**", "README.md"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    r = client.post("/repository/snapshots", headers=h)
    assert r.status_code == 201, r.text
    snapshot = r.json()
    r = client.post("/repository/symbols/index", headers=h)
    assert r.status_code in (200, 201), r.text
    return snapshot


def _fresh_snapshot(client, h):
    r = client.post("/repository/snapshots", headers=h)
    assert r.status_code == 201, r.text
    snapshot = r.json()
    r = client.post("/repository/symbols/index", headers=h)
    assert r.status_code in (200, 201), r.text
    return snapshot


def _make_pattern(client, h, points, name="checkout-observation", **kwargs):
    payload = {
        "name": name,
        "feature_id": kwargs.get("feature_id", "checkout"),
        "objective": kwargs.get("objective", "Observe checkout totals"),
        "origin": kwargs.get("origin", "scan"),
        "points": points,
    }
    r = client.post("/repository/probe-patterns", json=payload, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Instrumentation scan
# ---------------------------------------------------------------------------


def test_scan_lists_probe_instrumented_symbols(admin_client, git_repo):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "scan-sys")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)

    r = admin_client.get("/repository/probe-instrumentation", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    symbols = {(p["path"], p["symbol"]) for p in data["probes"]}
    assert ("src/app.py", "calculate_total") in symbols
    assert ("src/app.py", "format_receipt") in symbols
    assert ("src/util.py", "normalize_name") in symbols
    # Non-instrumented functions are never reported.
    assert not any(p["symbol"] == "helper" for p in data["probes"])
    by_symbol = {p["symbol"]: p for p in data["probes"]}
    assert by_symbol["calculate_total"]["component_id"] == "total-calculator"


def test_scan_requires_snapshot(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "scan-empty")
    h = _headers(token, system["id"])
    r = admin_client.get("/repository/probe-instrumentation", headers=h)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Pattern persistence
# ---------------------------------------------------------------------------


def test_create_pattern_captures_snapshot_facts(admin_client, git_repo):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "pattern-sys")
    h = _headers(token, system["id"])
    snapshot = _setup_repo(admin_client, h, git_repo)

    pattern = _make_pattern(admin_client, h, [
        {
            "path": "src/app.py",
            "symbol": "calculate_total",
            "component_id": "total-calculator",
            "reason": "Central total computation",
        },
    ])
    assert pattern["status"] == "active"
    assert pattern["origin"] == "scan"
    assert pattern["source_commit_sha"] == snapshot["commit_sha"]
    assert pattern["source_snapshot_id"] == snapshot["id"]
    point = pattern["points"][0]
    assert point["signature"].startswith("(items: list)")
    assert point["symbol_source_hash"]
    assert point["symbol_body_hash"]
    assert point["line_start"] > 0
    assert [e["event_type"] for e in pattern["events"]] == ["created"]


def test_pattern_update_rename_archive_events(admin_client, git_repo):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "update-sys")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total"},
    ])

    r = admin_client.patch(
        f"/repository/probe-patterns/{pattern['id']}",
        json={"name": "invoice-total-observation"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "invoice-total-observation"

    r = admin_client.patch(
        f"/repository/probe-patterns/{pattern['id']}",
        json={"status": "archived"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
    event_types = [e["event_type"] for e in r.json()["events"]]
    assert "renamed" in event_types
    assert "archived" in event_types

    # Archived patterns refuse reconciliation.
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 409


def test_system_isolation(admin_client, git_repo):
    token = _login(admin_client)
    sys_a = _create_system(admin_client, token, "iso-a")
    sys_b = _create_system(admin_client, token, "iso-b")
    ha = _headers(token, sys_a["id"])
    hb = _headers(token, sys_b["id"])
    _setup_repo(admin_client, ha, git_repo)
    pattern = _make_pattern(admin_client, ha, [
        {"path": "src/app.py", "symbol": "calculate_total"},
    ])

    r = admin_client.get("/repository/probe-patterns", headers=hb)
    assert r.status_code == 200
    assert r.json()["patterns"] == []
    r = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=hb,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Removal patches
# ---------------------------------------------------------------------------


def test_removal_patch_generates_diff_and_keeps_repo_unchanged(
    admin_client, git_repo,
):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "removal-sys")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total"},
        {"path": "src/app.py", "symbol": "format_receipt"},
    ])

    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/removal-patches",
        json={},
        headers=h,
    )
    assert r.status_code == 201, r.text
    patch = r.json()
    assert patch["status"] == "generated"
    assert '-@probe(component_id="total-calculator")' in patch["diff"]
    assert '-@probe(component_id="receipt-formatter")' in patch["diff"]
    # Both probes in the file are removed, so the import goes too.
    assert "-from probe_agent import probe" in patch["diff"]

    # Target repository is untouched until explicit apply.
    content = (git_repo / "src" / "app.py").read_text()
    assert '@probe(component_id="total-calculator")' in content
    status = subprocess.run(
        ["git", "-C", str(git_repo), "status", "--porcelain"],
        check=True, capture_output=True,
    )
    assert status.stdout.decode().strip() == ""


def test_removal_patch_keeps_import_when_other_probes_remain(
    admin_client, git_repo,
):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "removal-partial")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total"},
    ])

    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/removal-patches",
        json={},
        headers=h,
    )
    assert r.status_code == 201, r.text
    diff = r.json()["diff"]
    assert '-@probe(component_id="total-calculator")' in diff
    # format_receipt is still probed, so the import must survive.
    assert "-from probe_agent import probe" not in diff


def test_removal_patch_apply_boundary(admin_client, git_repo):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "apply-sys")
    h = _headers(token, system["id"])
    snapshot = _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total"},
    ])
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/removal-patches",
        json={},
        headers=h,
    )
    patch = r.json()

    # Wrong confirmation commit is refused.
    r = admin_client.post(
        f"/repository/probe-removal-patches/{patch['id']}/apply",
        json={"confirmed": True, "expected_commit_sha": "0" * 40},
        headers=h,
    )
    assert r.status_code == 409

    # Dirty working tree is refused.
    (git_repo / "scratch.txt").write_text("wip\n")
    r = admin_client.post(
        f"/repository/probe-removal-patches/{patch['id']}/apply",
        json={"confirmed": True, "expected_commit_sha": snapshot["commit_sha"]},
        headers=h,
    )
    assert r.status_code == 409
    (git_repo / "scratch.txt").unlink()

    # Clean tree + matching commit applies and marks points removed.
    r = admin_client.post(
        f"/repository/probe-removal-patches/{patch['id']}/apply",
        json={"confirmed": True, "expected_commit_sha": snapshot["commit_sha"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["apply_status"] == "applied"
    content = (git_repo / "src" / "app.py").read_text()
    assert '@probe(component_id="total-calculator")' not in content
    assert '@probe(component_id="receipt-formatter")' in content

    r = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=h,
    )
    detail = r.json()
    assert detail["removed_point_count"] == 1
    assert detail["points"][0]["status"] == "removed_from_production"
    assert "removal_applied" in [e["event_type"] for e in detail["events"]]

    # Double apply is refused.
    r = admin_client.post(
        f"/repository/probe-removal-patches/{patch['id']}/apply",
        json={"confirmed": True, "expected_commit_sha": snapshot["commit_sha"]},
        headers=h,
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Reconciliation — deterministic classifications
# ---------------------------------------------------------------------------


V2_APP = textwrap.dedent("""\
    from probe_agent import probe


    @probe(component_id="total-calculator")
    def calculate_total(items: list) -> int:
        \"\"\"Sum item prices with tax.\"\"\"
        total = sum(i["price"] for i in items)
        return int(total * 1.1)


    @probe(component_id="receipt-formatter")
    def format_receipt(total: int, currency: str = "JPY") -> str:
        return f"Total: {total} {currency}"


    def helper():
        pass
""")

V2_UTIL = textwrap.dedent("""\
    def unrelated():
        return 1
""")

V2_STRINGS = textwrap.dedent("""\
    from probe_agent import probe


    @probe(component_id="name-normalizer")
    def normalize_name(name: str) -> str:
        cleaned = name.strip()
        return cleaned.lower().replace("  ", " ")
""")


def _evolve_to_v2(repo):
    (repo / "src" / "app.py").write_text(V2_APP)
    (repo / "src" / "util.py").write_text(V2_UTIL)
    (repo / "src" / "strings.py").write_text(V2_STRINGS)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v2")


def test_reconcile_deterministic_classifications(admin_client, git_repo):
    """exact_match / changed_signature / moved_match resolve without any LLM.

    LLM_PROVIDER stays mock; if any point needed reasoning the run would
    fail, so completion proves the classifications were deterministic.
    """
    token = _login(admin_client)
    system = _create_system(admin_client, token, "recon-det")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total"},
        {"path": "src/app.py", "symbol": "format_receipt"},
        {"path": "src/util.py", "symbol": "normalize_name"},
    ])

    _evolve_to_v2(git_repo)
    _fresh_snapshot(admin_client, h)

    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "completed"
    assert rec["intelligence_run"]["decision_method"] == "deterministic"

    by_point = {}
    point_symbols = {p["id"]: p["symbol"] for p in pattern["points"]}
    for rp in rec["points"]:
        by_point[point_symbols[rp["pattern_point_id"]]] = rp

    assert by_point["calculate_total"]["classification"] == "exact_match"
    assert by_point["calculate_total"]["decision_method"] == "deterministic"

    changed = by_point["format_receipt"]
    assert changed["classification"] == "changed_signature"
    assert "currency" in changed["explanation"]
    assert changed["question"]
    assert changed["evidence"]

    moved = by_point["normalize_name"]
    assert moved["classification"] == "moved_match"
    assert moved["target_path"] == "src/strings.py"
    assert moved["target_symbol"] == "normalize_name"

    # Non-exact findings mark the pattern stale.
    r = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=h,
    )
    assert r.json()["status"] == "stale"
    assert r.json()["last_reconciled_at"] is not None


def test_reconcile_all_exact_keeps_pattern_active(admin_client, git_repo):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "recon-active")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total"},
    ])

    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "completed"
    assert rec["summary"] == {"exact_match": 1}
    r = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=h,
    )
    assert r.json()["status"] == "active"


def test_reconcile_denylisted_symbol_is_unsafe(admin_client, git_repo):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "recon-unsafe")
    h = _headers(token, system["id"])
    (git_repo / "src" / "billing.py").write_text(textwrap.dedent("""\
        from probe_agent import probe


        @probe(component_id="charger")
        def charge_customer(amount: int) -> bool:
            return True
    """))
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "billing")
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/billing.py", "symbol": "charge_customer"},
    ])

    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "completed"
    point = rec["points"][0]
    assert point["classification"] == "unsafe"
    assert point["denylist_hit"]

    # Unsafe points can never be accepted.
    r = admin_client.put(
        f"/repository/pattern-reconcile-points/{point['id']}/decision",
        json={"decision": "accepted"},
        headers=h,
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Reconciliation — reasoning model requirement (no heuristic fallback)
# ---------------------------------------------------------------------------


def test_reconcile_missing_symbol_fails_closed_with_mock(admin_client, git_repo):
    """Open-ended classification must fail with the mock provider, while the
    deterministic classifications from the same run remain persisted."""
    token = _login(admin_client)
    system = _create_system(admin_client, token, "recon-mock")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total"},
        {"path": "src/util.py", "symbol": "legacy_lookup"},
    ])

    _evolve_to_v2(git_repo)  # legacy_lookup disappears entirely
    _fresh_snapshot(admin_client, h)

    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "failed"
    assert "reasoning model" in (rec["error"] or "")
    assert rec["intelligence_run"]["status"] == "failed"
    assert rec["intelligence_run"]["is_mock"] is True
    # Deterministic results are still available.
    classifications = [p["classification"] for p in rec["points"]]
    assert classifications == ["exact_match"]
    # No heuristic guess was recorded for the missing point.
    assert len(rec["points"]) == 1


class _ReasoningMissingClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        prompt = messages[-1]["content"]
        point_id = None
        for line in prompt.splitlines():
            if line.strip().startswith("- id="):
                point_id = int(line.split("id=")[1].split(" ")[0])
        return json.dumps({
            "points": [{
                "pattern_point_id": point_id,
                "classification": "missing",
                "targets": [],
                "hypothesis": "legacy_lookup was deleted with no replacement.",
                "explanation": "No current symbol covers static key lookup.",
                "question": "",
                "confidence": 0.7,
                "evidence": [{
                    "path": "src/util.py",
                    "start_line": 1,
                    "end_line": 2,
                    "summary": "util.py no longer defines legacy_lookup",
                }],
            }],
        })


def _enable_reasoning(monkeypatch, client_class):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr(
        "app.routes.probe_patterns.create_llm_client",
        lambda config: client_class(),
    )


def test_reconcile_missing_with_reasoning_model(
    admin_client, git_repo, monkeypatch,
):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "recon-llm")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/util.py", "symbol": "legacy_lookup"},
    ])

    _evolve_to_v2(git_repo)
    _fresh_snapshot(admin_client, h)

    _enable_reasoning(monkeypatch, _ReasoningMissingClient)
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "completed"
    assert rec["intelligence_run"]["decision_method"] == "reasoning_llm"
    point = rec["points"][0]
    assert point["classification"] == "missing"
    assert point["decision_method"] == "reasoning_llm"
    assert point["hypothesis"]
    assert point["evidence"][0]["path"] == "src/util.py"

    # A missing point can never be accepted (nothing to re-attach).
    r = admin_client.put(
        f"/repository/pattern-reconcile-points/{point['id']}/decision",
        json={"decision": "accepted"},
        headers=h,
    )
    assert r.status_code == 409


class _ReasoningMovedClient:
    """Classifies the unresolved point as moved to src/report.py:load_report."""

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        prompt = messages[-1]["content"]
        point_id = None
        for line in prompt.splitlines():
            if line.strip().startswith("- id="):
                point_id = int(line.split("id=")[1].split(" ")[0])
        return json.dumps({
            "points": [{
                "pattern_point_id": point_id,
                "classification": "moved_match",
                "targets": [{"path": "src/report.py", "symbol": "load_report"}],
                "hypothesis": "fetch_report was renamed to load_report.",
                "explanation": "Same report loading responsibility and callers.",
                "question": "Move the probe to load_report?",
                "confidence": 0.85,
                "evidence": [{
                    "path": "src/report.py",
                    "start_line": 1,
                    "end_line": 6,
                    "summary": "load_report implements report retrieval",
                }],
            }],
        })


def test_reconcile_moved_then_create_plan(admin_client, git_repo, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "recon-plan")
    h = _headers(token, system["id"])

    (git_repo / "src" / "report.py").write_text(textwrap.dedent("""\
        from probe_agent import probe


        @probe(component_id="report-fetcher")
        def fetch_report(id: str) -> dict:
            return {"id": id}
    """))
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "report v1")
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total",
         "component_id": "total-calculator", "reason": "Observe totals"},
        {"path": "src/report.py", "symbol": "fetch_report",
         "component_id": "report-fetcher", "reason": "Observe report loads"},
    ])

    # Rename with a body change so no deterministic path can resolve it.
    (git_repo / "src" / "report.py").write_text(textwrap.dedent("""\
        def load_report(report_id: str) -> dict:
            report = {"id": report_id, "status": "loaded"}
            return report
    """))
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "report v2")
    _fresh_snapshot(admin_client, h)

    _enable_reasoning(monkeypatch, _ReasoningMovedClient)
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "completed"
    moved = next(
        p for p in rec["points"] if p["classification"] == "moved_match"
    )
    assert moved["target_symbol"] == "load_report"
    assert moved["question"]

    # Plan creation before accepting the moved point only includes exact
    # matches.
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}"
        f"/reconciliations/{rec['id']}/create-plan",
        json={},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["probe_points"]) == 1

    # Accept the moved point; a new plan now carries both.
    r = admin_client.put(
        f"/repository/pattern-reconcile-points/{moved['id']}/decision",
        json={"decision": "accepted"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["user_decision"] == "accepted"

    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}"
        f"/reconciliations/{rec['id']}/create-plan",
        json={"objective": "Re-observe checkout after refactor"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["origin"] == "probe_pattern"
    assert plan["objective"] == "Re-observe checkout after refactor"
    assert plan["status"] == "proposed"
    symbols = {(p["path"], p["symbol"]) for p in plan["probe_points"]}
    assert ("src/app.py", "calculate_total") in symbols
    assert ("src/report.py", "load_report") in symbols
    moved_point = next(
        p for p in plan["probe_points"] if p["symbol"] == "load_report"
    )
    assert moved_point["status"] == "proposed"
    assert "moved_match" in moved_point["reason"]
    assert moved_point["component_id"] == "report-fetcher"

    # Pattern records the plan creation.
    r = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=h,
    )
    detail = r.json()
    assert detail["last_used_at"] is not None
    assert "plan_created" in [e["event_type"] for e in detail["events"]]


# ---------------------------------------------------------------------------
# Investigation assistance
# ---------------------------------------------------------------------------


class _InvestigationClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps({
            "summary": (
                "Report loading is now centralized in load_report. The old "
                "fetch_report was removed; no external API calls or DB "
                "writes are present."
            ),
            "recommendation": "Re-attach the probe on load_report.",
            "proposed_target": {"path": "src/report.py", "symbol": "load_report"},
            "evidence": [{
                "path": "src/report.py",
                "start_line": 1,
                "end_line": 3,
                "summary": "load_report builds and returns the report",
            }],
        })


def _reconcile_moved_setup(admin_client, git_repo, monkeypatch, h):
    (git_repo / "src" / "report.py").write_text(textwrap.dedent("""\
        from probe_agent import probe


        @probe(component_id="report-fetcher")
        def fetch_report(id: str) -> dict:
            return {"id": id}
    """))
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "report v1")
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/report.py", "symbol": "fetch_report"},
    ])
    (git_repo / "src" / "report.py").write_text(textwrap.dedent("""\
        def load_report(report_id: str) -> dict:
            report = {"id": report_id, "status": "loaded"}
            return report
    """))
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "report v2")
    _fresh_snapshot(admin_client, h)

    _enable_reasoning(monkeypatch, _ReasoningMovedClient)
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 201, r.text
    return pattern, r.json()


def test_investigate_persists_summary(admin_client, git_repo, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "investigate-sys")
    h = _headers(token, system["id"])
    pattern, rec = _reconcile_moved_setup(admin_client, git_repo, monkeypatch, h)
    point = rec["points"][0]

    _enable_reasoning(monkeypatch, _InvestigationClient)
    r = admin_client.post(
        f"/repository/pattern-reconcile-points/{point['id']}/investigate",
        headers=h,
    )
    assert r.status_code == 200, r.text
    inv = r.json()["investigation"]
    assert "load_report" in inv["summary"]
    assert inv["recommendation"]
    assert inv["proposed_target_symbol"] == "load_report"
    assert inv["evidence"][0]["path"] == "src/report.py"
    # Decision remains with the user after investigation.
    assert r.json()["user_decision"] == "pending"


def test_investigate_fails_closed_with_mock(
    admin_client, git_repo, monkeypatch,
):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "investigate-mock")
    h = _headers(token, system["id"])
    pattern, rec = _reconcile_moved_setup(admin_client, git_repo, monkeypatch, h)
    point = rec["points"][0]

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(
        "app.routes.probe_patterns.create_llm_client",
        None,
    )
    # Restore the real factory so the mock provider path is exercised.
    from app.llm import create_llm_client as real_factory
    monkeypatch.setattr(
        "app.routes.probe_patterns.create_llm_client", real_factory,
    )

    r = admin_client.post(
        f"/repository/pattern-reconcile-points/{point['id']}/investigate",
        headers=h,
    )
    assert r.status_code == 502
    assert "reasoning model" in r.json()["detail"]
    # No investigation content was fabricated.
    r = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=h,
    )
    latest = r.json()["latest_reconciliation"]
    assert latest["points"][0]["investigation"] is None


# ---------------------------------------------------------------------------
# Lifecycle loop: re-attachment returns points to 'saved' (P1)
# ---------------------------------------------------------------------------


def test_reattachment_returns_points_to_saved_for_next_removal(
    admin_client, git_repo,
):
    """After removal+apply, creating a plan from a reconciliation must return
    the pattern points to 'saved' and re-anchor them, so a second pre-release
    removal can run from the same pattern."""
    token = _login(admin_client)
    system = _create_system(admin_client, token, "lifecycle-loop")
    h = _headers(token, system["id"])
    snapshot = _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total"},
    ])

    # Round 1: remove before release.
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/removal-patches",
        json={}, headers=h,
    )
    patch = r.json()
    r = admin_client.post(
        f"/repository/probe-removal-patches/{patch['id']}/apply",
        json={"confirmed": True, "expected_commit_sha": snapshot["commit_sha"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    detail = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=h,
    ).json()
    assert detail["points"][0]["status"] == "removed_from_production"

    # Re-development: reconcile (exact against the pinned snapshot) then create
    # a plan. This must re-attach: the point returns to 'saved'.
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    rec = r.json()
    assert rec["status"] == "completed"
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}"
        f"/reconciliations/{rec['id']}/create-plan",
        json={}, headers=h,
    )
    assert r.status_code == 201, r.text

    detail = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=h,
    ).json()
    assert detail["points"][0]["status"] == "saved"
    assert detail["points"][0]["removed_at"] is None
    assert "reattached" in [e["event_type"] for e in detail["events"]]

    # Round 2: the pre-release removal can run again from the same pattern.
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/removal-patches",
        json={}, headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "generated"
    assert '-@probe(component_id="total-calculator")' in r.json()["diff"]


# ---------------------------------------------------------------------------
# split_or_merged re-attaches a probe point per target (P2a)
# ---------------------------------------------------------------------------


V2_SPLIT_APP = textwrap.dedent("""\
    from probe_agent import probe


    def compute_subtotal(items: list) -> int:
        return sum(i["price"] for i in items)


    def apply_tax(subtotal: int) -> int:
        return int(subtotal * 1.1)
""")


class _ReasoningSplitClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        prompt = messages[-1]["content"]
        point_id = None
        for line in prompt.splitlines():
            if line.strip().startswith("- id="):
                point_id = int(line.split("id=")[1].split(" ")[0])
        return json.dumps({
            "points": [{
                "pattern_point_id": point_id,
                "classification": "split_or_merged",
                "targets": [
                    {"path": "src/app.py", "symbol": "compute_subtotal"},
                    {"path": "src/app.py", "symbol": "apply_tax"},
                ],
                "hypothesis": "calculate_total was split into subtotal and tax.",
                "explanation": "Two functions now cover the old responsibility.",
                "question": "Re-attach a probe to each?",
                "confidence": 0.8,
                "evidence": [{
                    "path": "src/app.py",
                    "start_line": 1,
                    "end_line": 9,
                    "summary": "subtotal and tax split apart",
                }],
            }],
        })


def test_split_or_merged_reattaches_every_target(
    admin_client, git_repo, monkeypatch,
):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "recon-split")
    h = _headers(token, system["id"])
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/app.py", "symbol": "calculate_total",
         "component_id": "total-calculator", "reason": "Observe totals"},
    ])

    (git_repo / "src" / "app.py").write_text(V2_SPLIT_APP)
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "split totals")
    _fresh_snapshot(admin_client, h)

    _enable_reasoning(monkeypatch, _ReasoningSplitClient)
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    point = rec["points"][0]
    assert point["classification"] == "split_or_merged"
    assert point["target_symbol"] == "compute_subtotal"
    assert [t["symbol"] for t in point["additional_targets"]] == ["apply_tax"]

    # Accept, then create a plan: both split targets get a probe point.
    r = admin_client.put(
        f"/repository/pattern-reconcile-points/{point['id']}/decision",
        json={"decision": "accepted"}, headers=h,
    )
    assert r.status_code == 200, r.text
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}"
        f"/reconciliations/{rec['id']}/create-plan",
        json={}, headers=h,
    )
    assert r.status_code == 201, r.text
    symbols = {(p["path"], p["symbol"]) for p in r.json()["probe_points"]}
    assert ("src/app.py", "compute_subtotal") in symbols
    assert ("src/app.py", "apply_tax") in symbols

    # The pattern now tracks both current locations as saved points.
    detail = admin_client.get(
        f"/repository/probe-patterns/{pattern['id']}", headers=h,
    ).json()
    saved = {(p["path"], p["symbol"]) for p in detail["points"]
             if p["status"] == "saved"}
    assert ("src/app.py", "compute_subtotal") in saved
    assert ("src/app.py", "apply_tax") in saved


# ---------------------------------------------------------------------------
# Investigated 'missing' candidate can be accepted and planned (P2b)
# ---------------------------------------------------------------------------


def test_investigated_missing_can_be_accepted_and_planned(
    admin_client, git_repo, monkeypatch,
):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "missing-plan")
    h = _headers(token, system["id"])

    (git_repo / "src" / "report.py").write_text(textwrap.dedent("""\
        from probe_agent import probe


        @probe(component_id="report-fetcher")
        def fetch_report(id: str) -> dict:
            return {"id": id}
    """))
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "report v1")
    _setup_repo(admin_client, h, git_repo)
    pattern = _make_pattern(admin_client, h, [
        {"path": "src/report.py", "symbol": "fetch_report",
         "component_id": "report-fetcher", "reason": "Observe report loads"},
    ])

    (git_repo / "src" / "report.py").write_text(textwrap.dedent("""\
        def load_report(report_id: str) -> dict:
            report = {"id": report_id, "status": "loaded"}
            return report
    """))
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "report v2")
    _fresh_snapshot(admin_client, h)

    _enable_reasoning(monkeypatch, _ReasoningMissingClient)
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}/reconcile", headers=h,
    )
    rec = r.json()
    point = rec["points"][0]
    assert point["classification"] == "missing"

    # Without investigation there is no target, so accept is refused.
    r = admin_client.put(
        f"/repository/pattern-reconcile-points/{point['id']}/decision",
        json={"decision": "accepted"}, headers=h,
    )
    assert r.status_code == 409

    # Investigate: the reasoning model proposes load_report.
    _enable_reasoning(monkeypatch, _InvestigationClient)
    r = admin_client.post(
        f"/repository/pattern-reconcile-points/{point['id']}/investigate",
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["investigation"]["proposed_target_symbol"] == "load_report"

    # Now the proposal can be accepted; the point is promoted with a target.
    r = admin_client.put(
        f"/repository/pattern-reconcile-points/{point['id']}/decision",
        json={"decision": "accepted"}, headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["target_symbol"] == "load_report"

    # And the promoted point flows into a Probe Plan.
    r = admin_client.post(
        f"/repository/probe-patterns/{pattern['id']}"
        f"/reconciliations/{rec['id']}/create-plan",
        json={}, headers=h,
    )
    assert r.status_code == 201, r.text
    symbols = {(p["path"], p["symbol"]) for p in r.json()["probe_points"]}
    assert ("src/report.py", "load_report") in symbols


# ---------------------------------------------------------------------------
# Unit tests: instrumentation remover
# ---------------------------------------------------------------------------


def test_remove_instrumentation_preserves_other_code():
    from app.instrumentation_remover import RemovalPoint, remove_instrumentation

    patched, skipped = remove_instrumentation(V1_APP, [
        RemovalPoint(path="src/app.py", symbol="calculate_total"),
    ])
    assert skipped == []
    assert '@probe(component_id="total-calculator")' not in patched
    assert '@probe(component_id="receipt-formatter")' in patched
    assert "from probe_agent import probe" in patched
    assert "def calculate_total(items: list) -> int:" in patched


def test_remove_instrumentation_drops_unused_import():
    from app.instrumentation_remover import RemovalPoint, remove_instrumentation

    patched, skipped = remove_instrumentation(V1_APP, [
        RemovalPoint(path="src/app.py", symbol="calculate_total"),
        RemovalPoint(path="src/app.py", symbol="format_receipt"),
    ])
    assert skipped == []
    assert "@probe" not in patched
    assert "from probe_agent import probe" not in patched
    compile(patched, "app.py", "exec")


def test_remove_instrumentation_skips_unknown_symbol():
    from app.instrumentation_remover import RemovalPoint, remove_instrumentation

    patched, skipped = remove_instrumentation(V1_APP, [
        RemovalPoint(path="src/app.py", symbol="does_not_exist"),
    ])
    assert patched == V1_APP
    assert skipped == ["does_not_exist: not found in AST"]
