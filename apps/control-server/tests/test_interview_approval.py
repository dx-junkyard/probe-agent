"""Tests for Issue #70: per-item approval gate with manual decision record.

Covers:
1. Partial approval: approving some items leaves others non-eligible; only
   approved items appear in the approved set.
2. An approval/edit creates a manual record without erasing the original
   reasoning_llm proposal.
3. An edit that introduces a denylisted probe target is rejected.
4. Rejected items never become materialization-eligible.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-approval-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
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


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    return token, system["id"], snapshot_id


def _valid_proposal_item(
    path="src/summarize.py",
    qualified_name="summarize.summarize_text",
    element_type="core",
    operation_kind="analysis",
):
    return {
        "path": path,
        "qualified_name": qualified_name,
        "metadata": {
            "role": "Summarize free text",
            "capability": "summarization",
            "system_purpose": "Document processing",
            "probe_value": "Validate latency",
            "element_type": element_type,
            "operation_kind": operation_kind,
            "consumers": ["api"],
            "state_effects": ["none"],
        },
        "probe_plan": {
            "feature_id": "summarization",
            "objective": "Trace summarizer",
            "reason": "Safe to trace",
            "recommended_mode": "trace",
            "side_effect_risk": "low",
            "replayability": "safe",
        },
    }


def _valid_audit():
    return {
        "provider": "mock",
        "model": "mock-reasoner",
        "prompt_version": "interview-v1",
        "schema_version": "1",
        "is_mock": True,
    }


def _create_session_with_proposals(client, headers, snapshot_id, proposals=None):
    """Create a session and populate it with proposals."""
    r = client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "Test session"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    session = r.json()
    sid = session["id"]

    if proposals is None:
        proposals = [
            _valid_proposal_item(),
            _valid_proposal_item(
                path="src/classifier.py",
                qualified_name="classifier.classify_text",
            ),
            _valid_proposal_item(
                path="src/extractor.py",
                qualified_name="extractor.extract_entities",
            ),
        ]

    r = client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": proposals},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return sid, r.json()


# --- Test 1: Partial approval ------------------------------------------------


def test_partial_approval_only_approved_in_set(admin_client):
    """Approving some items leaves others non-eligible; only approved items
    appear in the approved set."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, proposals = _create_session_with_proposals(admin_client, headers, snapshot_id)

    p0, p1, p2 = proposals[0], proposals[1], proposals[2]

    # Approve first proposal.
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p0['id']}/approve",
        json={"actor": "dev@example.com"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    decision = r.json()
    assert decision["decision"] == "approved"
    assert decision["decision_method"] == "manual"
    assert decision["actor"] == "dev@example.com"

    # Reject second proposal.
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p1['id']}/reject",
        json={"actor": "dev@example.com"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    # Third proposal stays as 'proposed'.

    # Check approved set.
    r = admin_client.get(f"/interview/sessions/{sid}/approved-set", headers=headers)
    assert r.status_code == 200, r.text
    approved_set = r.json()

    assert approved_set["total_proposals"] == 3
    assert approved_set["approved_count"] == 1
    assert approved_set["rejected_count"] == 1
    assert approved_set["pending_count"] == 1
    assert len(approved_set["items"]) == 1
    assert approved_set["items"][0]["proposal_id"] == p0["id"]
    assert approved_set["items"][0]["qualified_name"] == "summarize.summarize_text"


# --- Test 2: Manual record preserves original reasoning_llm proposal ----------


def test_approval_creates_manual_record_preserving_original(admin_client):
    """An approval creates a decision_method='manual' record without erasing
    the original reasoning_llm proposal."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, proposals = _create_session_with_proposals(admin_client, headers, snapshot_id)

    p0 = proposals[0]
    original_metadata = p0["metadata"]
    original_probe_plan = p0["probe_plan"]

    # Approve.
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p0['id']}/approve",
        json={"actor": "reviewer"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    decision = r.json()
    assert decision["decision_method"] == "manual"
    assert decision["proposal_id"] == p0["id"]

    # Verify original proposal still has reasoning_llm metadata.
    r = admin_client.get(f"/interview/sessions/{sid}", headers=headers)
    assert r.status_code == 200
    detail = r.json()
    original_p = next(p for p in detail["proposals"] if p["id"] == p0["id"])
    assert original_p["metadata"]["role"] == original_metadata["role"]
    assert original_p["metadata"]["element_type"] == original_metadata["element_type"]
    assert original_p["probe_plan"]["objective"] == original_probe_plan["objective"]
    # approval_state is updated for convenience, but the content is unchanged.
    assert original_p["approval_state"] == "approved"
    assert original_p["decision_method"] == "reasoning_llm"


def test_edit_creates_manual_record_with_corrected_values(admin_client):
    """An edit creates a manual record with the developer's corrected values,
    separate from the original."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, proposals = _create_session_with_proposals(admin_client, headers, snapshot_id)

    p0 = proposals[0]

    edited_metadata = {
        "role": "Summarize documents with AI",
        "capability": "document-processing",
        "system_purpose": "AI document pipeline",
        "probe_value": "Track quality metrics",
        "element_type": "element",
        "operation_kind": "io",
        "consumers": ["dashboard", "api"],
        "state_effects": ["network"],
    }
    edited_probe_plan = {
        "feature_id": "doc-processing",
        "objective": "Trace document pipeline",
        "reason": "IO-bound, worth tracing",
        "recommended_mode": "shadow",
        "side_effect_risk": "medium",
        "replayability": "caution",
    }

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p0['id']}/edit",
        json={
            "actor": "senior-dev@example.com",
            "metadata": edited_metadata,
            "probe_plan": edited_probe_plan,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    decision = r.json()
    assert decision["decision"] == "edited"
    assert decision["decision_method"] == "manual"
    assert decision["edited_metadata"]["role"] == "Summarize documents with AI"
    assert decision["edited_metadata"]["element_type"] == "element"
    assert decision["edited_probe_plan"]["recommended_mode"] == "shadow"

    # Original proposal retains its reasoning_llm values.
    r = admin_client.get(f"/interview/sessions/{sid}", headers=headers)
    detail = r.json()
    original_p = next(p for p in detail["proposals"] if p["id"] == p0["id"])
    assert original_p["metadata"]["role"] == "Summarize free text"
    assert original_p["metadata"]["element_type"] == "core"
    assert original_p["probe_plan"]["recommended_mode"] == "trace"

    # Approved set contains the edited values.
    r = admin_client.get(f"/interview/sessions/{sid}/approved-set", headers=headers)
    approved_set = r.json()
    assert len(approved_set["items"]) == 1
    item = approved_set["items"][0]
    assert item["metadata"]["role"] == "Summarize documents with AI"
    assert item["metadata"]["element_type"] == "element"
    assert item["probe_plan"]["recommended_mode"] == "shadow"
    assert item["decision"] == "edited"


# --- Test 3: Edit with denylisted symbol is rejected --------------------------


def test_edit_with_denylisted_symbol_is_rejected(admin_client):
    """An edit that targets a denylisted symbol (payment/auth/email) is
    rejected even if the developer tries to set a low risk."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)

    # Create a proposal for a denylisted symbol.
    proposals = [
        _valid_proposal_item(
            path="src/billing.py",
            qualified_name="billing.process_payment",
        ),
    ]
    sid, created = _create_session_with_proposals(
        admin_client, headers, snapshot_id, proposals=proposals,
    )
    p = created[0]

    # Try to edit it.
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/edit",
        json={
            "actor": "dev",
            "metadata": {
                "role": "Process payment",
                "capability": "billing",
                "element_type": "core",
                "operation_kind": "write",
                "state_effects": ["external-api"],
            },
            "probe_plan": {
                "feature_id": "billing",
                "objective": "Trace payments",
                "reason": "Need visibility",
                "recommended_mode": "trace",
                "side_effect_risk": "low",
                "replayability": "safe",
            },
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "denylist" in r.json()["detail"].lower()

    # Proposal remains 'proposed' — not materialization-eligible.
    r = admin_client.get(f"/interview/sessions/{sid}/approved-set", headers=headers)
    assert len(r.json()["items"]) == 0
    assert r.json()["pending_count"] == 1


def test_edit_with_denylisted_content_in_fields_is_rejected(admin_client):
    """An edit that smuggles denylisted intent into edited text fields
    (e.g. objective, reason, probe_value) is caught even when the symbol
    name itself is safe."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)

    proposals = [
        _valid_proposal_item(
            path="src/processor.py",
            qualified_name="processor.run_task",
        ),
    ]
    sid, created = _create_session_with_proposals(
        admin_client, headers, snapshot_id, proposals=proposals,
    )
    p = created[0]

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/edit",
        json={
            "actor": "dev",
            "metadata": {
                "role": "Handle refund requests",
                "capability": "billing",
                "element_type": "core",
                "operation_kind": "write",
                "state_effects": ["external-api"],
            },
            "probe_plan": {
                "feature_id": "billing",
                "objective": "Trace refund flow",
                "reason": "Need visibility into charge operations",
                "recommended_mode": "trace",
                "side_effect_risk": "low",
                "replayability": "safe",
            },
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "denylist" in r.json()["detail"].lower()


def test_approve_denylisted_symbol_is_allowed(admin_client):
    """Approving a denylisted symbol as-is is allowed — the original proposal
    may already carry a denylist_hit from #69. Approval is the developer's
    conscious choice (manual decision)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)

    proposals = [
        _valid_proposal_item(
            path="src/billing.py",
            qualified_name="billing.process_payment",
        ),
    ]
    sid, created = _create_session_with_proposals(
        admin_client, headers, snapshot_id, proposals=proposals,
    )
    p = created[0]

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/approve",
        json={"actor": "dev"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "approved"


# --- Test 4: Rejected items never become materialization-eligible -------------


def test_rejected_items_never_in_approved_set(admin_client):
    """Rejected items are never materialization-eligible."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, proposals = _create_session_with_proposals(admin_client, headers, snapshot_id)

    # Reject all.
    for p in proposals:
        r = admin_client.post(
            f"/interview/sessions/{sid}/proposals/{p['id']}/reject",
            json={"actor": "dev"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    r = admin_client.get(f"/interview/sessions/{sid}/approved-set", headers=headers)
    approved_set = r.json()
    assert len(approved_set["items"]) == 0
    assert approved_set["rejected_count"] == 3
    assert approved_set["pending_count"] == 0
    assert approved_set["approved_count"] == 0


# --- Additional edge cases ---------------------------------------------------


def test_double_approval_is_rejected(admin_client):
    """Cannot approve an already-approved proposal (409)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, proposals = _create_session_with_proposals(admin_client, headers, snapshot_id)

    p = proposals[0]
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/approve",
        json={"actor": "dev"},
        headers=headers,
    )
    assert r.status_code == 200

    # Second approval fails.
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/approve",
        json={"actor": "dev"},
        headers=headers,
    )
    assert r.status_code == 409


def test_reject_then_approve_is_rejected(admin_client):
    """Cannot approve a rejected proposal (409)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, proposals = _create_session_with_proposals(admin_client, headers, snapshot_id)

    p = proposals[0]
    admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/reject",
        json={"actor": "dev"},
        headers=headers,
    )

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/approve",
        json={"actor": "dev"},
        headers=headers,
    )
    assert r.status_code == 409


def test_edit_validates_enum_fields(admin_client):
    """Edited metadata with invalid enum values is rejected (422)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, proposals = _create_session_with_proposals(admin_client, headers, snapshot_id)

    p = proposals[0]
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/edit",
        json={
            "actor": "dev",
            "metadata": {
                "element_type": "INVALID",
                "operation_kind": "analysis",
                "state_effects": [],
            },
            "probe_plan": {
                "recommended_mode": "trace",
                "side_effect_risk": "low",
                "replayability": "safe",
            },
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text


def test_empty_actor_is_rejected(admin_client):
    """Actor must be non-empty (min_length=1)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, proposals = _create_session_with_proposals(admin_client, headers, snapshot_id)

    p = proposals[0]
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/approve",
        json={"actor": ""},
        headers=headers,
    )
    assert r.status_code == 422, r.text


def test_proposal_not_found_returns_404(admin_client):
    """Approving a non-existent proposal returns 404."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    sid, _ = _create_session_with_proposals(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/99999/approve",
        json={"actor": "dev"},
        headers=headers,
    )
    assert r.status_code == 404


def test_system_isolation_for_approvals(admin_client):
    """Proposals from one system cannot be approved via another system's header."""
    token, sys_a, snap_a = _setup(admin_client, "System A")
    sys_b_data = _create_system(admin_client, token, "System B")
    sys_b = sys_b_data["id"]

    headers_a = _headers(token, sys_a)
    headers_b = _headers(token, sys_b)

    sid, proposals = _create_session_with_proposals(admin_client, headers_a, snap_a)
    p = proposals[0]

    # Try to approve from system B — should 404.
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{p['id']}/approve",
        json={"actor": "dev"},
        headers=headers_b,
    )
    assert r.status_code == 404


def test_approved_set_empty_when_no_proposals(admin_client):
    """Approved set for a session with no proposals is empty."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)

    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "Empty"},
        headers=headers,
    )
    sid = r.json()["id"]

    r = admin_client.get(f"/interview/sessions/{sid}/approved-set", headers=headers)
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total_proposals"] == 0
