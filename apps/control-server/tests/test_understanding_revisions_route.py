"""Route-level tests for Issue #136: Understanding Revision persistence + diff API.

Covers:
1. A successful update-understanding call appends a revision linked to its
   understanding_review intelligence_runs row.
2. The diff endpoint (default latest-two) reflects deterministic
   added/removed/confidence changes between two revisions.
3. Explicit from/to revision ids.
4. No-previous case: a session's first revision has no comparison target.
5. System isolation.
6. Retention rotation beyond the configured limit.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-understanding-revisions-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _insert_snapshot(system_id, commit_sha="abc123"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
            (system_id, commit_sha, now, now),
        )
        return cur.lastrowid


def _insert_understanding_graph(system_id, snapshot_id):
    from app.documentation_claim_scanner import ChunkScanResult, ClaimEvidence, DocumentationClaim
    from app.understanding_graph import build_understanding_graph, save_graph_snapshot
    from app.db import get_conn

    result = ChunkScanResult(
        chunk_id="chunk-1",
        chunk_content_hash="hash-1",
        prompt_version="claim-scanner-v1",
        schema_version="claim-scanner-v1",
        claims=[
            DocumentationClaim(
                claim_type="system_purpose",
                summary="System helps inspect probe agent repositories",
                evidence=ClaimEvidence(path="README.md", start_line=1, end_line=5),
                confidence=0.9,
            )
        ],
    )
    graph = build_understanding_graph([result])
    with get_conn() as conn:
        return save_graph_snapshot(conn, system_id, graph, snapshot_id=snapshot_id)


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    _insert_understanding_graph(system["id"], snapshot_id)
    return token, system["id"], snapshot_id


def _understanding_response(purpose_name="Probe repository inspection", cap_names=None):
    return json.dumps({
        "system_purpose": [{
            "name": purpose_name,
            "summary": "Inspects probe agent repositories",
            "confidence": {"level": "likely", "reason": "README evidence"},
            "evidence": [{"path": "README.md", "start_line": 1, "end_line": 5, "summary": "s"}],
            "why_core": "",
            "related_docs": ["README.md"],
            "related_apis": [],
            "children": [],
        }],
        "core_capabilities": [
            {
                "name": cap_name,
                "summary": "cap summary",
                "confidence": {"level": "likely", "reason": ""},
                "evidence": [],
                "why_core": "",
                "related_docs": [],
                "related_apis": [],
                "children": [],
            }
            for cap_name in (cap_names or [])
        ],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
        "probe_flow_candidates": [],
        "gap_analysis": [],
        "open_questions": [],
        "suggested_next_action": "confirm_purpose",
    })


def _patch_review_client(monkeypatch, response_json):
    import app.routes.interview as interview_route

    class FakeReviewClient:
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return response_json

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(interview_route, "create_llm_client", lambda config: FakeReviewClient())


def _create_session(client, headers, snapshot_id, title="Understanding revision session"):
    r = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id, "title": title}, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_revision_appended_and_linked_to_run(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _patch_review_client(monkeypatch, _understanding_response())
    r = admin_client.post(
        f"/interview/sessions/{session_id}/update-understanding", headers=headers,
    )
    assert r.status_code == 200, r.text

    revisions = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-revisions", headers=headers,
    ).json()
    assert len(revisions["items"]) == 1
    rev = revisions["items"][0]
    assert rev["current_understanding"]["system_purpose"][0]["name"] == "Probe repository inspection"

    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            "SELECT id FROM intelligence_runs WHERE system_id = ? AND run_type = 'understanding_review' "
            "ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
    assert rev["intelligence_run_id"] == run["id"]


def test_diff_defaults_to_latest_two_revisions(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _patch_review_client(monkeypatch, _understanding_response(cap_names=["Summarize"]))
    admin_client.post(f"/interview/sessions/{session_id}/update-understanding", headers=headers)

    _patch_review_client(monkeypatch, _understanding_response(cap_names=["Summarize", "Classify"]))
    admin_client.post(f"/interview/sessions/{session_id}/update-understanding", headers=headers)

    diff = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-diff", headers=headers,
    ).json()
    assert diff["has_previous"] is True
    cap_section = next(s for s in diff["sections"] if s["section"] == "core_capabilities")
    assert cap_section["added"] == ["Classify"]
    assert cap_section["removed"] == []


def test_diff_explicit_from_and_to(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _patch_review_client(monkeypatch, _understanding_response(cap_names=["A"]))
    admin_client.post(f"/interview/sessions/{session_id}/update-understanding", headers=headers)
    _patch_review_client(monkeypatch, _understanding_response(cap_names=["A", "B"]))
    admin_client.post(f"/interview/sessions/{session_id}/update-understanding", headers=headers)
    _patch_review_client(monkeypatch, _understanding_response(cap_names=["A", "B", "C"]))
    admin_client.post(f"/interview/sessions/{session_id}/update-understanding", headers=headers)

    revisions = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-revisions", headers=headers,
    ).json()["items"]
    # newest first
    rev3, rev2, rev1 = revisions[0]["id"], revisions[1]["id"], revisions[2]["id"]

    diff = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-diff"
        f"?from={rev1}&to={rev3}",
        headers=headers,
    ).json()
    cap_section = next(s for s in diff["sections"] if s["section"] == "core_capabilities")
    assert sorted(cap_section["added"]) == ["B", "C"]


def test_first_revision_has_no_previous(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _patch_review_client(monkeypatch, _understanding_response())
    admin_client.post(f"/interview/sessions/{session_id}/update-understanding", headers=headers)

    diff = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-diff", headers=headers,
    ).json()
    assert diff["has_previous"] is False
    assert diff["sections"] == []


def test_existing_understanding_seeded_as_initial_revision(admin_client, monkeypatch):
    """Issue #136: a session whose understanding predates this feature has a
    current_understanding but no revision rows. The next update seeds that
    prior state as the initial revision (intelligence_run_id NULL) before
    appending the new one, so the diff has a real previous to compare."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    # Simulate a pre-#136 session: current_understanding set directly, with no
    # understanding_revision rows.
    from app.db import get_conn

    prior_understanding = json.loads(_understanding_response(cap_names=["OldCap"]))
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET current_understanding = ? WHERE id = ?",
            (json.dumps(prior_understanding), session_id),
        )
        assert conn.execute(
            "SELECT COUNT(*) c FROM understanding_revision WHERE session_id = ?",
            (session_id,),
        ).fetchone()["c"] == 0

    _patch_review_client(monkeypatch, _understanding_response(cap_names=["OldCap", "NewCap"]))
    r = admin_client.post(
        f"/interview/sessions/{session_id}/update-understanding", headers=headers,
    )
    assert r.status_code == 200, r.text

    revisions = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-revisions", headers=headers,
    ).json()["items"]
    # Seed (prior) + new revision, newest first.
    assert len(revisions) == 2
    assert revisions[1]["intelligence_run_id"] is None  # seeded initial revision
    assert revisions[1]["current_understanding"]["core_capabilities"][0]["name"] == "OldCap"
    assert revisions[0]["intelligence_run_id"] is not None  # new revision linked to run

    diff = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-diff", headers=headers,
    ).json()
    assert diff["has_previous"] is True
    cap_section = next(s for s in diff["sections"] if s["section"] == "core_capabilities")
    assert cap_section["added"] == ["NewCap"]


def test_diff_with_no_revisions_at_all_does_not_crash(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-diff", headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["has_previous"] is False

    revisions = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-revisions", headers=headers,
    ).json()
    assert revisions["items"] == []


def test_revisions_isolated_across_systems(admin_client, monkeypatch):
    token_a, system_a, snapshot_a = _setup(admin_client, "System A")
    headers_a = _headers(token_a, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)

    token_b, system_b, snapshot_b = _setup(admin_client, "System B")
    headers_b = _headers(token_b, system_b)
    session_b = _create_session(admin_client, headers_b, snapshot_b)

    _patch_review_client(monkeypatch, _understanding_response(purpose_name="System B purpose"))
    admin_client.post(f"/interview/sessions/{session_b}/update-understanding", headers=headers_b)

    revisions_a = admin_client.get(
        f"/interview/sessions/{session_a}/understanding-revisions", headers=headers_a,
    ).json()
    assert revisions_a["items"] == []


def test_revision_rotation_respects_limit(admin_client, monkeypatch):
    monkeypatch.setenv("INTERVIEW_UNDERSTANDING_REVISION_LIMIT", "3")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    for i in range(5):
        _patch_review_client(monkeypatch, _understanding_response(cap_names=[f"cap-{i}"]))
        r = admin_client.post(
            f"/interview/sessions/{session_id}/update-understanding", headers=headers,
        )
        assert r.status_code == 200, r.text

    revisions = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-revisions", headers=headers,
    ).json()["items"]
    assert len(revisions) == 3
    # The newest three revisions survive (cap-2, cap-3, cap-4).
    names = [
        r["current_understanding"]["core_capabilities"][0]["name"] for r in revisions
    ]
    assert set(names) == {"cap-2", "cap-3", "cap-4"}


def test_diff_rejects_reversed_range(admin_client, monkeypatch):
    """from must be an earlier revision than to; a reversed range would
    silently swap added/removed (review fix)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _patch_review_client(monkeypatch, _understanding_response(cap_names=["A"]))
    admin_client.post(f"/interview/sessions/{session_id}/update-understanding", headers=headers)
    _patch_review_client(monkeypatch, _understanding_response(cap_names=["A", "B"]))
    admin_client.post(f"/interview/sessions/{session_id}/update-understanding", headers=headers)

    revisions = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-revisions", headers=headers,
    ).json()["items"]
    newer, older = revisions[0]["id"], revisions[1]["id"]

    r = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-diff"
        f"?from={newer}&to={older}",
        headers=headers,
    )
    assert r.status_code == 422, r.text
