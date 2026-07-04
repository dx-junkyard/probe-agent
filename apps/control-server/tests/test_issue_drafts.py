"""Tests for Issue #107: issue draft generation, persistence, and external URL.

Covers:
- generating a draft from a System Understanding gap
- deterministic Markdown body embedding snapshot id / commit sha / evidence
- persistence and re-read
- editing title / body / status
- registering an external issue URL (with http(s) validation)
- surfacing registered drafts back on the originating gap
- System isolation between systems
"""

import subprocess
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-idraft-test.db"))
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


def _init_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# Test Project\nA test project.\n")
    src = repo / "src"
    src.mkdir()
    (src / "main.py").write_text(
        'from fastapi import APIRouter\n\nrouter = APIRouter()\n\n'
        '@router.get("/items")\ndef list_items():\n    """List all items."""\n    return []\n\n'
        '@router.get("/widgets")\ndef list_widgets():\n    """List all widgets."""\n    return []\n'
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo), check=True, capture_output=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def _build_and_wait(client, hdrs, timeout=15.0):
    r = client.post("/repository/system-understanding/build", headers=hdrs)
    assert r.status_code == 202, r.text
    build_id = r.json()["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        status_r = client.get(
            f"/repository/system-understanding/build/{build_id}", headers=hdrs
        )
        assert status_r.status_code == 200, status_r.text
        build = status_r.json()
        if build["status"] in ("completed", "partial", "failed", "cancelled"):
            return build
        time.sleep(0.05)
    pytest.fail(f"Build {build_id} did not settle: {build}")


def _setup_system_with_gaps(client, tmp_path, name):
    token = _login(client)
    sys = _create_system(client, token, name)
    hdrs = _headers(token, sys["id"])
    repo, sha = _init_git_repo(tmp_path)
    client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
        headers=hdrs,
    )
    client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
    _build_and_wait(client, hdrs)
    r = client.get("/repository/system-understanding", headers=hdrs)
    assert r.status_code == 200
    return hdrs, sha, r.json()


class TestIssueDraftGeneration:
    def test_generate_draft_from_gap(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-gen")
        assert understanding["gaps"], "expected at least one gap for the test"
        gap = understanding["gaps"][0]

        r = admin_client.post(
            "/issue-drafts",
            json={"source_type": "system_understanding_gap", "gap": gap},
            headers=hdrs,
        )
        assert r.status_code == 201, r.text
        draft = r.json()

        assert draft["id"] > 0
        assert draft["status"] == "draft"
        assert draft["external_url"] is None
        assert draft["source_type"] == "system_understanding_gap"
        assert draft["source_key"] == gap["source_key"]
        assert draft["commit_sha"] == sha
        assert draft["snapshot_id"] == understanding["snapshot_id"]

        # Body embeds snapshot id, commit sha, and the gap classification.
        body = draft["body_markdown"]
        assert sha in body
        assert str(understanding["snapshot_id"]) in body
        assert gap["gap_type"] in body
        assert (gap["node_name"] or "") in body

    def test_body_includes_evidence(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-evidence")
        # Find a gap that carries symbol or entrypoint evidence.
        gap = next(
            (g for g in understanding["gaps"] if g["symbol_refs"] or g["entrypoint_refs"]),
            understanding["gaps"][0],
        )
        r = admin_client.post(
            "/issue-drafts",
            json={"gap": gap},
            headers=hdrs,
        )
        assert r.status_code == 201, r.text
        body = r.json()["body_markdown"]
        assert "## Code evidence" in body
        assert "## Observation snapshot" in body

    def test_draft_persisted_and_relisted(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-persist")
        gap = understanding["gaps"][0]
        create = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs)
        draft_id = create.json()["id"]

        list_r = admin_client.get("/issue-drafts", headers=hdrs)
        assert list_r.status_code == 200
        assert any(d["id"] == draft_id for d in list_r.json())

        get_r = admin_client.get(f"/issue-drafts/{draft_id}", headers=hdrs)
        assert get_r.status_code == 200
        assert get_r.json()["id"] == draft_id


class TestSourceKeyDisambiguation:
    def test_same_type_and_name_gaps_get_distinct_keys(self):
        """Two gaps with the same type/name but different evidence must not collide."""
        from app.issue_drafts import gap_source_key

        gap_a = {
            "gap_type": "docs_only",
            "node_name": "Auth",
            "capability_key": "security",
            "doc_refs": [{"path": "docs/a.md", "start_line": 1, "end_line": 5}],
            "symbol_refs": [],
            "entrypoint_refs": [],
        }
        gap_b = {
            "gap_type": "docs_only",
            "node_name": "Auth",
            "capability_key": "billing",
            "doc_refs": [{"path": "docs/b.md", "start_line": 9, "end_line": 12}],
            "symbol_refs": [],
            "entrypoint_refs": [],
        }
        assert gap_source_key(gap_a) != gap_source_key(gap_b)

    def test_empty_evidence_gaps_disambiguated_by_source_id(self):
        """missing_evidence gaps carry no evidence/capability; source_id must disambiguate."""
        from app.issue_drafts import gap_source_key

        base = {
            "gap_type": "missing_evidence",
            "node_name": "Auth",
            "capability_key": None,
            "doc_refs": [],
            "symbol_refs": [],
            "entrypoint_refs": [],
        }
        gap_a = {**base, "source_id": "node:claim-1"}
        gap_b = {**base, "source_id": "node:claim-2"}
        assert gap_source_key(gap_a) != gap_source_key(gap_b)
        # Same source_id -> same key (stable re-attachment).
        assert gap_source_key(gap_a) == gap_source_key({**base, "source_id": "node:claim-1"})

    def test_key_is_stable_and_order_independent(self):
        from app.issue_drafts import gap_source_key

        gap1 = {
            "gap_type": "code_only",
            "node_name": "list_items",
            "capability_key": None,
            "doc_refs": [],
            "symbol_refs": [
                {"path": "a.py", "qualified_name": "f"},
                {"path": "b.py", "qualified_name": "g"},
            ],
            "entrypoint_refs": [],
        }
        gap2 = {  # same evidence, reordered
            "gap_type": "code_only",
            "node_name": "list_items",
            "capability_key": None,
            "doc_refs": [],
            "symbol_refs": [
                {"path": "b.py", "qualified_name": "g"},
                {"path": "a.py", "qualified_name": "f"},
            ],
            "entrypoint_refs": [],
        }
        assert gap_source_key(gap1) == gap_source_key(gap2)

    def test_distinct_drafts_do_not_cross_attach(self, admin_client, tmp_path):
        """Drafts for different gaps must attach only to their own gap."""
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-distinct")
        gaps = understanding["gaps"]
        if len(gaps) < 2:
            pytest.skip("need at least two distinct gaps")
        assert gaps[0]["source_key"] != gaps[1]["source_key"]

        d0 = admin_client.post("/issue-drafts", json={"gap": gaps[0]}, headers=hdrs).json()
        d1 = admin_client.post("/issue-drafts", json={"gap": gaps[1]}, headers=hdrs).json()
        assert d0["source_key"] != d1["source_key"]

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        by_key = {g["source_key"]: g for g in r.json()["gaps"]}
        assert [x["id"] for x in by_key[gaps[0]["source_key"]]["issue_drafts"]] == [d0["id"]]
        assert [x["id"] for x in by_key[gaps[1]["source_key"]]["issue_drafts"]] == [d1["id"]]


class TestSnapshotPinning:
    def test_matching_snapshot_accepted(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-snap-ok")
        gap = understanding["gaps"][0]
        r = admin_client.post(
            "/issue-drafts",
            json={
                "gap": gap,
                "snapshot_id": understanding["snapshot_id"],
                "commit_sha": understanding["commit_sha"],
            },
            headers=hdrs,
        )
        assert r.status_code == 201, r.text
        assert r.json()["snapshot_id"] == understanding["snapshot_id"]
        assert r.json()["commit_sha"] == sha

    def test_stale_snapshot_rejected(self, admin_client, tmp_path):
        """A gap displayed against an older snapshot is refused (409) once a new one is ready."""
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-snap-stale")
        gap = understanding["gaps"][0]
        stale_snapshot_id = understanding["snapshot_id"]

        # A newer snapshot becomes ready (same commit is fine; the id differs).
        new_snap = admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )
        assert new_snap.status_code == 201
        assert new_snap.json()["id"] != stale_snapshot_id

        r = admin_client.post(
            "/issue-drafts",
            json={"gap": gap, "snapshot_id": stale_snapshot_id},
            headers=hdrs,
        )
        assert r.status_code == 409

    def test_omitted_snapshot_falls_back_to_latest(self, admin_client, tmp_path):
        """Backward compatible: without a pinned snapshot, the latest ready one is used."""
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-snap-omit")
        gap = understanding["gaps"][0]
        r = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs)
        assert r.status_code == 201
        assert r.json()["snapshot_id"] == understanding["snapshot_id"]


class TestIssueDraftEditing:
    def test_edit_title_and_body(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-edit")
        gap = understanding["gaps"][0]
        draft_id = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs).json()["id"]

        r = admin_client.patch(
            f"/issue-drafts/{draft_id}",
            json={"title": "Custom title", "body_markdown": "custom body"},
            headers=hdrs,
        )
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "Custom title"
        assert r.json()["body_markdown"] == "custom body"

    def test_status_transitions(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-status")
        gap = understanding["gaps"][0]
        draft_id = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs).json()["id"]

        for status in ("copied", "external_created", "closed", "rejected"):
            r = admin_client.patch(
                f"/issue-drafts/{draft_id}", json={"status": status}, headers=hdrs
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == status

    def test_invalid_status_rejected(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-badstatus")
        gap = understanding["gaps"][0]
        draft_id = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs).json()["id"]

        r = admin_client.patch(
            f"/issue-drafts/{draft_id}", json={"status": "merged"}, headers=hdrs
        )
        assert r.status_code == 422


class TestExternalUrl:
    def test_register_external_url(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-url")
        gap = understanding["gaps"][0]
        draft_id = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs).json()["id"]

        r = admin_client.patch(
            f"/issue-drafts/{draft_id}",
            json={"external_url": "https://github.com/acme/repo/issues/1", "status": "external_created"},
            headers=hdrs,
        )
        assert r.status_code == 200, r.text
        assert r.json()["external_url"] == "https://github.com/acme/repo/issues/1"
        assert r.json()["status"] == "external_created"

    def test_reject_non_http_url(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-badurl")
        gap = understanding["gaps"][0]
        draft_id = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs).json()["id"]

        r = admin_client.patch(
            f"/issue-drafts/{draft_id}",
            json={"external_url": "ftp://example.com/x"},
            headers=hdrs,
        )
        assert r.status_code == 422

    def test_clear_external_url(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-clearurl")
        gap = understanding["gaps"][0]
        draft_id = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs).json()["id"]
        admin_client.patch(
            f"/issue-drafts/{draft_id}",
            json={"external_url": "https://x.test/1"},
            headers=hdrs,
        )
        r = admin_client.patch(
            f"/issue-drafts/{draft_id}", json={"external_url": ""}, headers=hdrs
        )
        assert r.status_code == 200
        assert r.json()["external_url"] is None


class TestGapDraftSurfacing:
    def test_registered_draft_surfaces_on_gap(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-surface")
        gap = understanding["gaps"][0]
        source_key = gap["source_key"]
        assert source_key

        draft_id = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs).json()["id"]
        admin_client.patch(
            f"/issue-drafts/{draft_id}",
            json={"external_url": "https://tracker.test/42", "status": "external_created"},
            headers=hdrs,
        )

        # Re-read system understanding; the matching gap now carries the draft.
        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        matching = [g for g in r.json()["gaps"] if g["source_key"] == source_key]
        assert matching
        drafts = matching[0]["issue_drafts"]
        assert any(
            d["id"] == draft_id and d["external_url"] == "https://tracker.test/42"
            for d in drafts
        )

    def test_understanding_unaffected_without_drafts(self, admin_client, tmp_path):
        hdrs, sha, understanding = _setup_system_with_gaps(admin_client, tmp_path, "idraft-nodraft")
        for gap in understanding["gaps"]:
            assert gap["issue_drafts"] == []
            assert gap["source_key"]


class TestSystemIsolation:
    def test_drafts_scoped_to_system(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "idraft-iso-a")
        sys_b = _create_system(admin_client, token, "idraft-iso-b")
        hdrs_a = _headers(token, sys_a["id"])
        hdrs_b = _headers(token, sys_b["id"])

        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs_a,
        )
        admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs_a)
        _build_and_wait(admin_client, hdrs_a)
        understanding = admin_client.get("/repository/system-understanding", headers=hdrs_a).json()
        gap = understanding["gaps"][0]
        draft_id = admin_client.post("/issue-drafts", json={"gap": gap}, headers=hdrs_a).json()["id"]

        # System B cannot see or fetch System A's draft.
        assert admin_client.get("/issue-drafts", headers=hdrs_b).json() == []
        assert admin_client.get(f"/issue-drafts/{draft_id}", headers=hdrs_b).status_code == 404
        assert (
            admin_client.patch(
                f"/issue-drafts/{draft_id}", json={"status": "closed"}, headers=hdrs_b
            ).status_code
            == 404
        )
