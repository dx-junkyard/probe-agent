"""Tests for Issue #289: natural-language bulk correction -> structured
change set (preview + selective apply).

Covers:
1. pure resolution logic (app.change_sets.resolve_change_set_items /
   effective_resolution_state): resolved/ambiguous/conflict/stale/forbidden
   each produced deterministically, same input -> same output.
2. create -> preview (with deterministic impact preview) -> partial
   selection -> apply end-to-end (mocked reasoning call): only
   selected+resolved items are applied; unselected items are untouched;
   an intent_item target creates a supersede-row trail; an
   understanding_claim target creates a new understanding_revision;
   applying triggers the Issue #288 refresh job with trigger_kind
   'nl_change_set'.
3. negative paths: LLM failure -> change set 'failed', nothing applied; a
   stale base revision at apply time -> item skipped as 'stale'; a
   forbidden (target_kind, field) pair is rejected and stays unappliable;
   applying the same item twice is a no-op the second time (applied flag
   guard).
4. revision lineage + audit contract: change_set -> intelligence_runs ->
   understanding_revision are all linked with the same run id.
5. System isolation: change sets cannot be previewed/applied/discarded from
   another System, and an item id cannot be applied through another System's
   otherwise-valid change set.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-change-sets-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    # Deterministic tests for the refresh trigger this flow enqueues.
    monkeypatch.setenv("PROBE_REFRESH_EAGER", "1")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


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


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _insert_intent_item(session_id, system_id, field, value_text):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO interview_intent_item
                (session_id, system_id, field, value_text, status, origin,
                 decision_method, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'confirmed', 'user', 'manual', ?, ?)""",
            (session_id, system_id, field, value_text, now, now),
        )
        return cur.lastrowid


def _default_understanding():
    return {
        "system_purpose": [{
            "name": "トレース管理", "summary": "旧: プローブのトレースを収集する",
            "confidence": {"level": "likely", "reason": ""}, "evidence": [],
            "why_core": "", "related_docs": [], "related_apis": [], "children": [],
        }],
        "core_capabilities": [],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
    }


def _insert_understanding_revision(session_id, system_id, snapshot_id, current_understanding):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO understanding_revision
                (session_id, system_id, snapshot_id, intelligence_run_id,
                 current_understanding, gap_analysis, created_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?)""",
            (
                session_id, system_id, snapshot_id,
                json.dumps(current_understanding, ensure_ascii=False),
                json.dumps([]), now,
            ),
        )
        return cur.lastrowid


def _insert_intelligence_run(system_id, snapshot_id):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 is_mock, started_at, completed_at)
            VALUES (?, ?, 'alignment_build', 'anthropic', 'claude-sonnet-4-5',
                    'alignment-v1', 'alignment-v1', 'reasoning_llm', 'completed', 0, ?, ?)""",
            (system_id, snapshot_id, now, now),
        )
        return cur.lastrowid


def _insert_alignment_item(session_id, system_id, snapshot_id, *, intent_item_id=None, current_claim="claim"):
    from app.db import get_conn

    run_id = _insert_intelligence_run(system_id, snapshot_id)
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO alignment_item
                (session_id, system_id, revision_id, snapshot_id, intent_item_id,
                 intent_summary, current_claim, current_evidence, gap_summary,
                 proposed_interpretation, alignment_state, risk_flags, confidence,
                 review_category, reason_code, user_reason, status, user_decision,
                 intelligence_run_id, is_mock, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?, NULL, ?, '[]', NULL, NULL, 'gap', '[]', 'likely',
                    'batch_reviewable', 'routine_update', '軽微な差分です。まとめて確認してください',
                    'open', NULL, ?, 0, ?, ?)""",
            (session_id, system_id, snapshot_id, intent_item_id, current_claim, run_id, now, now),
        )
        return cur.lastrowid


def _stub_propose(monkeypatch, *, items=None, error=None, is_mock=False):
    from app.routes import interview_change_sets as routes
    from app.change_sets import ChangeSetProposalItem, ChangeSetProposalResult

    def fake_create_llm_client(config):
        return object()

    def fake_generate_change_set_proposal(client, config, **kwargs):
        return ChangeSetProposalResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=is_mock,
            items=[ChangeSetProposalItem(**i) for i in (items or [])],
            error=error,
        )

    monkeypatch.setattr(routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(routes, "generate_change_set_proposal", fake_generate_change_set_proposal)


# --- 1. pure resolution logic ------------------------------------------------


def test_resolve_change_set_items_is_deterministic_across_states():
    from app.change_sets import ChangeSetProposalItem, resolve_change_set_items

    candidates = [
        {"kind": "intent_item", "hint": "goal", "current_value": "旧ゴール", "id": 1},
        {
            "kind": "understanding_claim", "hint": "system_purpose::トレース管理",
            "current_value": "旧説明", "section": "system_purpose", "name": "トレース管理",
        },
        {
            "kind": "understanding_claim", "hint": "core_capabilities::重複名",
            "current_value": "A", "section": "core_capabilities", "name": "重複名",
        },
        {
            "kind": "understanding_claim", "hint": "core_capabilities::重複名",
            "current_value": "B", "section": "core_capabilities", "name": "重複名",
        },
    ]
    items = [
        ChangeSetProposalItem(target_kind="intent_item", target_hint="goal", field="value_text", after_value="新ゴール", reason="ユーザーの発言による"),
        ChangeSetProposalItem(target_kind="understanding_claim", target_hint="system_purpose::トレース管理", field="summary", after_value="新説明", reason="現状に合わせて修正"),
        ChangeSetProposalItem(target_kind="understanding_claim", target_hint="core_capabilities::重複名", field="summary", after_value="X", reason="重複ヒントによりあいまい"),
        ChangeSetProposalItem(target_kind="understanding_claim", target_hint="core_capabilities::存在しない", field="summary", after_value="Y", reason="存在しない対象"),
        ChangeSetProposalItem(target_kind="intent_item", target_hint="goal", field="status", after_value="confirmed", reason="不正フィールド"),
        ChangeSetProposalItem(target_kind="alignment_item", target_hint="anything", field="user_decision", after_value="accept_current", reason="対象外種別"),
    ]

    resolved = resolve_change_set_items(items, candidates)
    assert [r.resolution_state for r in resolved] == [
        "resolved", "resolved", "ambiguous", "ambiguous", "forbidden", "forbidden",
    ]

    # Same input -> same output (Principle 6 determinism).
    resolved_again = resolve_change_set_items(items, candidates)
    assert [r.resolution_state for r in resolved_again] == [r.resolution_state for r in resolved]

    assert resolved[0].target_ref == {"intent_item_id": 1}
    assert resolved[0].before_value == "旧ゴール"
    assert resolved[1].target_ref == {"section": "system_purpose", "name": "トレース管理"}
    assert resolved[1].before_value == "旧説明"
    assert resolved[4].resolution_state == "forbidden"
    assert resolved[5].resolution_state == "forbidden"


@pytest.mark.parametrize("stored_state", ["ambiguous", "forbidden"])
def test_effective_resolution_state_never_changes_ambiguous_or_forbidden(stored_state):
    from app.change_sets import effective_resolution_state

    assert effective_resolution_state(
        stored_state=stored_state, target_kind="understanding_claim", before_value="x",
        base_revision_id=1, latest_revision_id=99, current_value="different",
    ) == stored_state


def test_effective_resolution_state_stale_applies_only_to_understanding_claim():
    from app.change_sets import effective_resolution_state

    assert effective_resolution_state(
        stored_state="resolved", target_kind="understanding_claim", before_value="v",
        base_revision_id=1, latest_revision_id=2, current_value="v",
    ) == "stale"
    # intent_item is not revisioned this way -- a base-revision mismatch is
    # irrelevant to it.
    assert effective_resolution_state(
        stored_state="resolved", target_kind="intent_item", before_value="v",
        base_revision_id=1, latest_revision_id=2, current_value="v",
    ) == "resolved"


def test_effective_resolution_state_conflict_when_value_changed_or_missing():
    from app.change_sets import effective_resolution_state

    assert effective_resolution_state(
        stored_state="resolved", target_kind="intent_item", before_value="v",
        base_revision_id=None, latest_revision_id=None, current_value="changed",
    ) == "conflict"
    assert effective_resolution_state(
        stored_state="resolved", target_kind="intent_item", before_value="v",
        base_revision_id=None, latest_revision_id=None, current_value=None,
    ) == "conflict"


# --- 1b. generate_change_set_proposal fail-closed (mirrors alignment/intent) -


def _llm_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


class _FakeChangeSetClient(LLMClient):
    def __init__(self, *, response=None, raw=None, error=None):
        self._response = response
        self._raw = raw
        self._error = error

    def generate_text(self, messages, *, temperature=None, max_tokens=None) -> str:
        if self._error:
            raise LLMError(self._error)
        if self._raw is not None:
            return self._raw
        return json.dumps(self._response)


_SAMPLE_CANDIDATES = [{"kind": "intent_item", "hint": "goal", "current_value": "旧ゴール", "id": 1}]


def test_generate_change_set_proposal_fails_closed_on_mock_client():
    from app.change_sets import generate_change_set_proposal

    result = generate_change_set_proposal(
        MockLLMClient(), _llm_config(provider="mock", model="mock"),
        source_text="ゴールを直して", candidates=_SAMPLE_CANDIDATES,
    )
    assert result.error is not None
    assert result.items == []


def test_generate_change_set_proposal_fails_closed_on_non_reasoning_model():
    from app.change_sets import generate_change_set_proposal

    result = generate_change_set_proposal(
        _FakeChangeSetClient(response={"items": []}), _llm_config(model="claude-3-5-haiku-latest"),
        source_text="ゴールを直して", candidates=_SAMPLE_CANDIDATES,
    )
    assert result.error is not None


def test_generate_change_set_proposal_fails_closed_on_llm_error():
    from app.change_sets import generate_change_set_proposal

    result = generate_change_set_proposal(
        _FakeChangeSetClient(error="boom"), _llm_config(),
        source_text="ゴールを直して", candidates=_SAMPLE_CANDIDATES,
    )
    assert result.error == "boom"


def test_generate_change_set_proposal_fails_closed_on_invalid_json():
    from app.change_sets import generate_change_set_proposal

    result = generate_change_set_proposal(
        _FakeChangeSetClient(raw="not json"), _llm_config(),
        source_text="ゴールを直して", candidates=_SAMPLE_CANDIDATES,
    )
    assert result.error is not None


def test_generate_change_set_proposal_fails_closed_on_invalid_target_kind():
    """An out-of-finite-set target_kind fails the WHOLE proposal closed,
    exactly like Alignment's invalid alignment_state/confidence handling --
    never a partial/best-effort batch."""
    from app.change_sets import generate_change_set_proposal

    item = {
        "target_kind": "alignment_item", "target_hint": "anything",
        "field": "user_decision", "after_value": "x", "reason": "y",
    }
    result = generate_change_set_proposal(
        _FakeChangeSetClient(response={"items": [item]}), _llm_config(),
        source_text="意思決定を直して", candidates=_SAMPLE_CANDIDATES,
    )
    assert result.error is not None
    assert result.items == []


def test_generate_change_set_proposal_parses_valid_response():
    from app.change_sets import generate_change_set_proposal

    item = {
        "target_kind": "intent_item", "target_hint": "goal", "field": "value_text",
        "after_value": "新ゴール", "reason": "発言に基づき修正",
    }
    result = generate_change_set_proposal(
        _FakeChangeSetClient(response={"items": [item]}), _llm_config(),
        source_text="ゴールを直して", candidates=_SAMPLE_CANDIDATES,
    )
    assert result.error is None
    assert len(result.items) == 1
    assert result.items[0].target_hint == "goal"


def test_generate_change_set_proposal_returns_no_items_when_no_candidates():
    from app.change_sets import generate_change_set_proposal

    result = generate_change_set_proposal(
        _FakeChangeSetClient(response={"items": []}), _llm_config(),
        source_text="何か直して", candidates=[],
    )
    assert result.error is None
    assert result.items == []


# --- 2. create -> preview -> partial apply end-to-end ------------------------


def test_create_change_set_resolves_and_previews_diff_with_impact(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    intent_id = _insert_intent_item(session_id, system_id, "goal", "旧ゴール")
    _insert_understanding_revision(session_id, system_id, snapshot_id, _default_understanding())
    _insert_alignment_item(session_id, system_id, snapshot_id, intent_item_id=intent_id, current_claim="旧ゴールに基づく現状の説明")

    _stub_propose(monkeypatch, items=[
        {"target_kind": "intent_item", "target_hint": "goal", "field": "value_text", "after_value": "新ゴール", "reason": "発言に基づき修正"},
        {"target_kind": "understanding_claim", "target_hint": "system_purpose::トレース管理", "field": "summary", "after_value": "新: 自動でトレースを収集する", "reason": "実態に合わせて修正"},
    ])

    r = admin_client.post(
        f"/interview/sessions/{session_id}/change-sets",
        json={"text": "実はゴールはAではなくBです。あとトレース管理の説明も直してください"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["change_set"]["status"] == "proposed"
    assert body["change_set"]["is_mock"] is False
    assert len(body["items"]) == 2
    intent_item = next(i for i in body["items"] if i["target_kind"] == "intent_item")
    claim_item = next(i for i in body["items"] if i["target_kind"] == "understanding_claim")
    assert intent_item["resolution_state"] == "resolved"
    assert intent_item["before_value"] == "旧ゴール"
    assert intent_item["after_value"] == "新ゴール"
    assert intent_item["target_ref"] == {"intent_item_id": intent_id}
    assert len(intent_item["affected_items"]) == 1
    assert claim_item["resolution_state"] == "resolved"
    assert claim_item["before_value"] == "旧: プローブのトレースを収集する"

    change_set_id = body["change_set"]["id"]

    # GET preview: status advances proposed -> previewed; items still resolved.
    r = admin_client.get(f"/interview/change-sets/{change_set_id}", headers=headers)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["change_set"]["status"] == "previewed"
    assert preview["rebuild_note"]
    assert all(i["resolution_state"] == "resolved" for i in preview["items"])


def _create_change_set(client, headers, session_id, text, items, monkeypatch):
    _stub_propose(monkeypatch, items=items)
    r = client.post(
        f"/interview/sessions/{session_id}/change-sets", json={"text": text}, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_partial_selection_applies_only_selected_resolved_items(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    intent_id = _insert_intent_item(session_id, system_id, "goal", "旧ゴール")
    _insert_understanding_revision(session_id, system_id, snapshot_id, _default_understanding())

    body = _create_change_set(
        admin_client, headers, session_id, "ゴールを直して。あと不正な項目も混ぜる",
        items=[
            {"target_kind": "intent_item", "target_hint": "goal", "field": "value_text", "after_value": "新ゴール", "reason": "修正"},
            {"target_kind": "intent_item", "target_hint": "goal", "field": "status", "after_value": "confirmed", "reason": "不正フィールド(forbidden)"},
            {"target_kind": "understanding_claim", "target_hint": "system_purpose::存在しない項目", "field": "summary", "after_value": "X", "reason": "存在しない(ambiguous)"},
        ],
        monkeypatch=monkeypatch,
    )
    items = body["items"]
    resolved_item = next(i for i in items if i["resolution_state"] == "resolved")
    forbidden_item = next(i for i in items if i["resolution_state"] == "forbidden")
    ambiguous_item = next(i for i in items if i["resolution_state"] == "ambiguous")
    change_set_id = body["change_set"]["id"]

    r = admin_client.post(
        f"/interview/change-sets/{change_set_id}/apply",
        json={"item_ids": [resolved_item["id"], forbidden_item["id"], ambiguous_item["id"]]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["applied_item_ids"] == [resolved_item["id"]]
    skipped_ids = {s["item_id"]: s["resolution_state"] for s in result["skipped"]}
    assert skipped_ids == {forbidden_item["id"]: "forbidden", ambiguous_item["id"]: "ambiguous"}
    assert result["change_set"]["status"] == "partially_applied"

    # Intent target creates a supersede-row trail; old row's superseded_by_id set.
    listing = admin_client.get(f"/interview/sessions/{session_id}/intent", headers=headers).json()
    goal_items = listing["items_by_field"]["goal"]
    assert len(goal_items) == 1
    assert goal_items[0]["value_text"] == "新ゴール"
    assert goal_items[0]["origin"] == "user"
    assert goal_items[0]["decision_method"] == "manual"

    listing_all = admin_client.get(
        f"/interview/sessions/{session_id}/intent?include_superseded=true", headers=headers,
    ).json()
    all_goal_items = listing_all["items_by_field"]["goal"]
    assert len(all_goal_items) == 2
    old = next(i for i in all_goal_items if i["id"] == intent_id)
    assert old["superseded_by_id"] == goal_items[0]["id"]

    # Unselected/skipped items untouched.
    from app.db import get_conn
    with get_conn() as conn:
        rows = {
            r["id"]: r["applied"]
            for r in conn.execute(
                "SELECT id, applied FROM understanding_change_item WHERE change_set_id = ?",
                (change_set_id,),
            ).fetchall()
        }
    assert rows[forbidden_item["id"]] == 0
    assert rows[ambiguous_item["id"]] == 0
    assert rows[resolved_item["id"]] == 1

    # Refresh job enqueued with trigger_kind='nl_change_set' (Issue #288 wiring).
    with get_conn() as conn:
        job = conn.execute(
            """SELECT * FROM interview_refresh_job
               WHERE session_id = ? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
    assert job is not None
    assert job["trigger_kind"] == "nl_change_set"


def test_understanding_claim_apply_creates_new_revision_never_edits_in_place(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    base_revision_id = _insert_understanding_revision(session_id, system_id, snapshot_id, _default_understanding())

    body = _create_change_set(
        admin_client, headers, session_id, "トレース管理の説明を直して",
        items=[{
            "target_kind": "understanding_claim", "target_hint": "system_purpose::トレース管理",
            "field": "summary", "after_value": "新: 自動でトレースを収集する", "reason": "実態に合わせて修正",
        }],
        monkeypatch=monkeypatch,
    )
    change_set_id = body["change_set"]["id"]
    run_id = body["change_set"]["intelligence_run_id"]
    item_id = body["items"][0]["id"]

    from app.db import get_conn
    with get_conn() as conn:
        revision_count_before = conn.execute(
            "SELECT COUNT(*) FROM understanding_revision WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    assert revision_count_before == 1

    r = admin_client.post(
        f"/interview/change-sets/{change_set_id}/apply",
        json={"item_ids": [item_id]}, headers=headers,
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["applied_item_ids"] == [item_id]
    assert result["change_set"]["status"] == "applied"
    new_revision_id = result["result_revision_id"]
    assert new_revision_id is not None
    assert new_revision_id != base_revision_id

    with get_conn() as conn:
        revision_count_after = conn.execute(
            "SELECT COUNT(*) FROM understanding_revision WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        old_revision = conn.execute(
            "SELECT * FROM understanding_revision WHERE id = ?", (base_revision_id,)
        ).fetchone()
        new_revision = conn.execute(
            "SELECT * FROM understanding_revision WHERE id = ?", (new_revision_id,)
        ).fetchone()
    # Previous revision remains (append-only, never edited in place).
    assert revision_count_after == 2
    old_understanding = json.loads(old_revision["current_understanding"])
    assert old_understanding["system_purpose"][0]["summary"] == "旧: プローブのトレースを収集する"
    new_understanding = json.loads(new_revision["current_understanding"])
    assert new_understanding["system_purpose"][0]["summary"] == "新: 自動でトレースを収集する"
    # Linked to the change set's own intelligence run, not a new one.
    assert new_revision["intelligence_run_id"] == run_id


# --- 3. negative paths --------------------------------------------------------


def test_llm_failure_creates_failed_change_set_and_applies_nothing(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_propose(monkeypatch, error="reasoning call failed")

    r = admin_client.post(
        f"/interview/sessions/{session_id}/change-sets",
        json={"text": "何か直して"}, headers=headers,
    )
    assert r.status_code == 502, r.text

    from app.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM understanding_change_set WHERE session_id = ?", (session_id,)
        ).fetchone()
        item_count = conn.execute(
            "SELECT COUNT(*) FROM understanding_change_item WHERE change_set_id = ?", (row["id"],)
        ).fetchone()[0]
    assert row["status"] == "failed"
    assert item_count == 0


def test_stale_base_revision_at_apply_skips_item_as_stale(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _insert_understanding_revision(session_id, system_id, snapshot_id, _default_understanding())

    body = _create_change_set(
        admin_client, headers, session_id, "トレース管理の説明を直して",
        items=[{
            "target_kind": "understanding_claim", "target_hint": "system_purpose::トレース管理",
            "field": "summary", "after_value": "新しい説明", "reason": "修正",
        }],
        monkeypatch=monkeypatch,
    )
    change_set_id = body["change_set"]["id"]
    item_id = body["items"][0]["id"]

    # Simulate the understanding being rebuilt after this change set was
    # proposed (a newer revision now exists) -- the change set's
    # base_revision_id is no longer the latest.
    _insert_understanding_revision(session_id, system_id, snapshot_id, _default_understanding())

    preview = admin_client.get(f"/interview/change-sets/{change_set_id}", headers=headers).json()
    assert preview["items"][0]["resolution_state"] == "stale"

    r = admin_client.post(
        f"/interview/change-sets/{change_set_id}/apply",
        json={"item_ids": [item_id]}, headers=headers,
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["applied_item_ids"] == []
    assert result["skipped"] == [{"item_id": item_id, "resolution_state": "stale", "message": result["skipped"][0]["message"]}]

    from app.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT resolution_state, applied FROM understanding_change_item WHERE id = ?", (item_id,)
        ).fetchone()
    assert row["resolution_state"] == "stale"
    assert row["applied"] == 0


def test_forbidden_target_is_rejected_at_creation_and_skipped_at_apply(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_intent_item(session_id, system_id, "goal", "旧ゴール")

    body = _create_change_set(
        admin_client, headers, session_id, "ゴールのステータスを直して",
        items=[{
            "target_kind": "intent_item", "target_hint": "goal",
            "field": "status", "after_value": "confirmed", "reason": "対象外フィールドへの変更依頼",
        }],
        monkeypatch=monkeypatch,
    )
    item = body["items"][0]
    assert item["resolution_state"] == "forbidden"
    change_set_id = body["change_set"]["id"]

    r = admin_client.post(
        f"/interview/change-sets/{change_set_id}/apply",
        json={"item_ids": [item["id"]]}, headers=headers,
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["applied_item_ids"] == []
    assert result["skipped"][0]["resolution_state"] == "forbidden"


def test_apply_twice_is_noop_second_time(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_intent_item(session_id, system_id, "goal", "旧ゴール")

    body = _create_change_set(
        admin_client, headers, session_id, "ゴールを直して",
        items=[{"target_kind": "intent_item", "target_hint": "goal", "field": "value_text", "after_value": "新ゴール", "reason": "修正"}],
        monkeypatch=monkeypatch,
    )
    change_set_id = body["change_set"]["id"]
    item_id = body["items"][0]["id"]

    r1 = admin_client.post(
        f"/interview/change-sets/{change_set_id}/apply",
        json={"item_ids": [item_id]}, headers=headers,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["applied_item_ids"] == [item_id]

    r2 = admin_client.post(
        f"/interview/change-sets/{change_set_id}/apply",
        json={"item_ids": [item_id]}, headers=headers,
    )
    assert r2.status_code == 200, r2.text
    result2 = r2.json()
    assert result2["applied_item_ids"] == []
    assert result2["skipped"][0]["item_id"] == item_id

    listing_all = admin_client.get(
        f"/interview/sessions/{session_id}/intent?include_superseded=true", headers=headers,
    ).json()
    # Exactly one supersede happened, not two.
    assert len(listing_all["items_by_field"]["goal"]) == 2


# --- 4. revision lineage + audit contract ------------------------------------


def test_audit_lineage_change_set_run_and_revision_are_linked(admin_client, monkeypatch):
    from app.change_sets import PROMPT_VERSION, SCHEMA_VERSION

    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_understanding_revision(session_id, system_id, snapshot_id, _default_understanding())

    body = _create_change_set(
        admin_client, headers, session_id, "トレース管理の説明を直して",
        items=[{
            "target_kind": "understanding_claim", "target_hint": "system_purpose::トレース管理",
            "field": "summary", "after_value": "新しい説明", "reason": "修正",
        }],
        monkeypatch=monkeypatch,
    )
    run_id = body["change_set"]["intelligence_run_id"]

    from app.db import get_conn
    with get_conn() as conn:
        run = conn.execute("SELECT * FROM intelligence_runs WHERE id = ?", (run_id,)).fetchone()
    assert run is not None
    assert run["run_type"] == "nl_change_set"
    assert run["decision_method"] == "reasoning_llm"
    assert run["prompt_version"] == PROMPT_VERSION
    assert run["schema_version"] == SCHEMA_VERSION
    assert run["status"] == "completed"


def test_discard_marks_change_set_and_blocks_further_apply(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_intent_item(session_id, system_id, "goal", "旧ゴール")

    body = _create_change_set(
        admin_client, headers, session_id, "ゴールを直して",
        items=[{"target_kind": "intent_item", "target_hint": "goal", "field": "value_text", "after_value": "新ゴール", "reason": "修正"}],
        monkeypatch=monkeypatch,
    )
    change_set_id = body["change_set"]["id"]
    item_id = body["items"][0]["id"]

    r = admin_client.post(f"/interview/change-sets/{change_set_id}/discard", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "discarded"

    r2 = admin_client.post(
        f"/interview/change-sets/{change_set_id}/apply",
        json={"item_ids": [item_id]}, headers=headers,
    )
    assert r2.status_code == 409, r2.text


# --- 5. System isolation ------------------------------------------------------


def test_change_sets_and_items_are_isolated_across_systems(admin_client, monkeypatch):
    token, system_a, snapshot_a = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")["id"]
    snapshot_b = _insert_snapshot(system_b, commit_sha="bbb")

    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b)
    session_a = _create_session(admin_client, headers_a, snapshot_a)
    session_b = _create_session(admin_client, headers_b, snapshot_b)
    intent_a = _insert_intent_item(session_a, system_a, "goal", "System A goal")
    intent_b = _insert_intent_item(session_b, system_b, "goal", "System B goal")

    proposal = [{
        "target_kind": "intent_item",
        "target_hint": "goal",
        "field": "value_text",
        "after_value": "intruder goal",
        "reason": "System isolation regression",
    }]
    change_a = _create_change_set(
        admin_client, headers_a, session_a, "System A goal を修正", proposal, monkeypatch,
    )
    change_b = _create_change_set(
        admin_client, headers_b, session_b, "System B goal を修正", proposal, monkeypatch,
    )
    change_set_a = change_a["change_set"]["id"]
    change_set_b = change_b["change_set"]["id"]
    item_a = change_a["items"][0]["id"]
    item_b = change_b["items"][0]["id"]

    # A foreign System cannot read (or trigger the proposed -> previewed
    # mutation on) a change set, apply one of its items, or discard it.
    assert admin_client.get(
        f"/interview/change-sets/{change_set_a}", headers=headers_b,
    ).status_code == 404
    assert admin_client.post(
        f"/interview/change-sets/{change_set_a}/apply",
        json={"item_ids": [item_a]},
        headers=headers_b,
    ).status_code == 404
    assert admin_client.post(
        f"/interview/change-sets/{change_set_a}/discard", headers=headers_b,
    ).status_code == 404

    # Item ids are scoped independently too: a caller cannot smuggle an item
    # from another System through one of its own otherwise-valid change sets.
    assert admin_client.post(
        f"/interview/change-sets/{change_set_b}/apply",
        json={"item_ids": [item_a]},
        headers=headers_b,
    ).status_code == 404
    assert admin_client.post(
        f"/interview/change-sets/{change_set_a}/apply",
        json={"item_ids": [item_b]},
        headers=headers_a,
    ).status_code == 404

    # Every rejected operation is side-effect free for both the parent rows
    # and items, including the original intent targets.
    from app.db import get_conn
    with get_conn() as conn:
        change_sets = {
            row["id"]: row["status"]
            for row in conn.execute(
                """SELECT id, status FROM understanding_change_set
                   WHERE id IN (?, ?)""",
                (change_set_a, change_set_b),
            ).fetchall()
        }
        items = {
            row["id"]: row["applied"]
            for row in conn.execute(
                """SELECT id, applied FROM understanding_change_item
                   WHERE id IN (?, ?)""",
                (item_a, item_b),
            ).fetchall()
        }
        intents = {
            row["id"]: (row["value_text"], row["superseded_by_id"])
            for row in conn.execute(
                """SELECT id, value_text, superseded_by_id
                   FROM interview_intent_item WHERE id IN (?, ?)""",
                (intent_a, intent_b),
            ).fetchall()
        }

    assert change_sets == {change_set_a: "proposed", change_set_b: "proposed"}
    assert items == {item_a: 0, item_b: 0}
    assert intents == {
        intent_a: ("System A goal", None),
        intent_b: ("System B goal", None),
    }
