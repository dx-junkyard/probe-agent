"""Tests for Issue #455 (Epic #443 §6): the hypothesis -> Joint Understanding
bridge and its reflux read.

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §6 is the canonical contract. Acceptance
criteria under test:

1. A hypothesis missing competing_explanations/refutation_conditions/
   next_investigation is refused both at generation time
   (``TestHypothesisGenerationValidation``) and at promotion time
   (``TestPromotionValidation``), with nothing persisted either way.
2. A retried promotion with the same ``request_id`` returns the SAME Joint
   Understanding session (``TestPromotionIdempotency``); the same
   ``request_id`` reused for a different hypothesis is 409.
3. Promotion works without any owning Interview session, binding to the
   correct System/thread/premise (``TestPromotionWithoutInterview``).
4. The reflux read never surfaces findings from a non-``current`` premise,
   never promotes ``hypothesis_adopted`` past provisional, and reports
   stale/missing distinctly (``TestReflux``).
5. A representative chain (Gap -> Journey -> Requirement -> Feature) proves
   the dependency manifest actually detects a change several hops away from
   the discussion root (``TestRepresentativeChainE2E``) -- the shared fixture
   `#459` reuses.
6. System isolation (``TestSystemIsolation``) and promotion never touching
   the origin hypothesis's proposal/thread/turns or starting an
   investigation on its own (``TestNoSideEffects``).

Fixture style mirrors `tests/test_assistant_discussion_proposals.py`, whose
plain helper functions this file imports directly rather than duplicating.
"""

from __future__ import annotations

import json
import time

import pytest

from test_assistant_discussion_proposals import (  # noqa: F401 - fixture reuse
    _create_design,
    _create_journey,
    _create_requirement,
    _create_thread,
    _enable_real_llm,
    _FixedResponseClient,
    _generate_proposal,
    _get_proposal,
    _headers,
    _login,
)
from test_assistant_discussion_proposals import _create_system as _create_system_raw


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-discussion-hypothesis-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL", "CONTROL_API_KEYS",
        "LLM_MODEL", "LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def _create_system(client, token, name="discussion-hypothesis-sys"):
    system_id = _create_system_raw(client, token, name=name)["id"]
    _insert_ready_snapshot(system_id)
    return system_id


def _insert_ready_snapshot(system_id, commit_sha="commitA"):
    """A `current` premise requires a pinned COMMIT (§6.2/#337's
    `is_complete`) -- every representative-chain/reflux test needs a ready
    snapshot to exist before it can ever read as `current`."""
    import time as _time

    from app.db import get_conn

    now = _time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, "/tmp/x", commit_sha, now, now),
        )


_HYPOTHESIS_RESPONSE = {
    "summary": "リトライ回数の根拠が会話で明らかになっていない",
    "confirmed_points": [], "unresolved_questions": [], "assumptions": [], "evidence_refs": [],
    "field_changes": [], "relation_changes": [], "child_changes": [],
    "hypothesis_changes": [
        {
            "statement": "リトライは外部APIのレート制限に由来する",
            "competing_explanations": ["単なる実装都合かもしれない"],
            "refutation_conditions": ["設定ファイルにレート制限の記載が無ければ誤り"],
            "next_investigation": "外部APIクライアントの設定を調査する",
            "evidence_refs": [], "uncertainty": "中",
        }
    ],
}


def _generate_with_hypothesis(client, headers, thread_id, monkeypatch, response=None, expect=201):
    fake = _FixedResponseClient(response or _HYPOTHESIS_RESPONSE)
    _enable_real_llm(monkeypatch, fake)
    return _generate_proposal(client, headers, thread_id, expect=expect)


def _promote(client, headers, proposal_id, hypothesis_id, request_id, expect=200):
    r = client.post(
        f"/assistant/discussion-proposals/{proposal_id}/hypotheses/{hypothesis_id}/promote",
        json={"request_id": request_id},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _reflux(client, headers, thread_id, expect=200):
    r = client.get(f"/assistant/discussion-threads/{thread_id}/joint-understanding", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


# ---------------------------------------------------------------------------
# 1. Generation-time validation
# ---------------------------------------------------------------------------


class TestHypothesisGenerationValidation:
    def test_complete_hypothesis_is_persisted_as_an_independent_type(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        assert proposal["items"] == []
        assert len(proposal["hypotheses"]) == 1
        hyp = proposal["hypotheses"][0]
        assert hyp["status"] == "proposed"
        assert hyp["competing_explanations"] == ["単なる実装都合かもしれない"]
        assert hyp["refutation_conditions"] == ["設定ファイルにレート制限の記載が無ければ誤り"]
        assert hyp["next_investigation"] == "外部APIクライアントの設定を調査する"

    @pytest.mark.parametrize("missing_field", ["competing_explanations", "refutation_conditions", "next_investigation"])
    def test_incomplete_hypothesis_fails_the_whole_generation(self, admin_client, monkeypatch, missing_field):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        response = json.loads(json.dumps(_HYPOTHESIS_RESPONSE))
        if missing_field == "next_investigation":
            response["hypothesis_changes"][0][missing_field] = ""
        else:
            response["hypothesis_changes"][0][missing_field] = []
        # An incomplete hypothesis fails the WHOLE generation call (502
        # discussion_proposal_generation_failed), never a partial proposal.
        _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch, response, expect=502)
        proposals = admin_client.get(f"/assistant/discussion-threads/{thread['thread']['id']}/proposals", headers=headers)
        assert proposals.json()["proposals"] == [], "an incomplete hypothesis must persist nothing"


# ---------------------------------------------------------------------------
# 2/3. Promotion: validation, idempotency, no owning Interview
# ---------------------------------------------------------------------------


class TestPromotionValidation:
    def test_promote_rejects_an_incomplete_hypothesis_with_no_state_change(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]

        # Corrupt the persisted row directly (bypassing generation's own
        # validation) to prove promotion re-validates independently.
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "UPDATE assistant_discussion_proposal_hypothesis SET refutation_conditions_json = '[]' "
                "WHERE id = ?",
                (hyp_id,),
            )

        r = _promote(admin_client, headers, proposal["id"], hyp_id, "req-1", expect=422)
        assert r.json()["detail"]["code"] == "hypothesis_incomplete_refutation_conditions"
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status FROM assistant_discussion_proposal_hypothesis WHERE id = ?", (hyp_id,),
            ).fetchone()
            assert row["status"] == "proposed"
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM assistant_discussion_hypothesis_promotion WHERE hypothesis_id = ?",
                (hyp_id,),
            ).fetchone()["n"]
            assert count == 0

    def test_promote_unknown_hypothesis_is_404(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        _promote(admin_client, headers, proposal["id"], 999999, "req-1", expect=404)


class TestPromotionIdempotency:
    def test_same_request_id_returns_the_same_ju_session_without_duplicating(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]

        first = _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")
        assert first["reused"] is False
        second = _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")
        assert second["reused"] is True
        assert second["joint_understanding_session_id"] == first["joint_understanding_session_id"]

        from app.db import get_conn

        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM joint_understanding_session WHERE origin_id = ? AND origin_kind='discussion'",
                (hyp_id,),
            ).fetchone()["n"]
            assert count == 1

    def test_reusing_a_request_id_for_a_different_hypothesis_is_409(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        response_two = json.loads(json.dumps(_HYPOTHESIS_RESPONSE))
        response_two["hypothesis_changes"].append(dict(_HYPOTHESIS_RESPONSE["hypothesis_changes"][0]))
        response_two["hypothesis_changes"][1]["statement"] = "別の仮説"
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch, response_two)
        hyp_ids = [h["id"] for h in proposal["hypotheses"]]
        assert len(hyp_ids) == 2

        _promote(admin_client, headers, proposal["id"], hyp_ids[0], "shared-token")
        r = _promote(admin_client, headers, proposal["id"], hyp_ids[1], "shared-token", expect=409)
        assert r.json()["detail"]["code"] == "discussion_hypothesis_promotion_request_conflict"

    def test_a_deliberate_new_request_id_opens_a_second_investigation(self, admin_client, monkeypatch):
        """§6.2: re-promoting the SAME hypothesis under a NEW request_id is a
        deliberate second investigation, not blocked."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        first = _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")
        second = _promote(admin_client, headers, proposal["id"], hyp_id, "req-B")
        assert second["reused"] is False
        assert second["joint_understanding_session_id"] != first["joint_understanding_session_id"]


class TestPromotionWithoutInterview:
    def test_promoted_session_has_owner_scope_discussion_and_no_session_id(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        result = _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")

        from app.db import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM joint_understanding_session WHERE id = ?",
                (result["joint_understanding_session_id"],),
            ).fetchone()
        assert row["owner_scope"] == "discussion"
        assert row["session_id"] is None
        assert row["discussion_thread_id"] == thread["thread"]["id"]
        assert row["trigger"] == "discussion_promotion"
        assert row["system_id"] == system_id


# ---------------------------------------------------------------------------
# 4. Reflux
# ---------------------------------------------------------------------------


class TestReflux:
    def test_reflux_reports_current_with_no_findings_when_nothing_investigated(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")

        data = _reflux(admin_client, headers, thread["thread"]["id"])
        assert len(data["links"]) == 1
        link = data["links"][0]
        assert link["session"]["premise_state"] == "current"
        assert link["reconfirmation_required"] is False
        assert link["current_findings"] == []

    def test_reflux_goes_stale_when_the_discussion_root_changes_and_requires_reconfirmation(
        self, admin_client, monkeypatch,
    ):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")

        r = admin_client.post(
            "/ux-design/requirements/req-1/revisions",
            json={"statement": "changed", "rationale": "", "constraint_text": "",
                  "out_of_scope_note": "", "change_note": "edited", "acceptance_criteria": []},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        data = _reflux(admin_client, headers, thread["thread"]["id"])
        link = data["links"][0]
        assert link["session"]["premise_state"] == "stale"
        assert link["session"]["premise_reason"] == "origin_content_changed"
        assert link["reconfirmation_required"] is True
        assert link["current_findings"] == []

    def test_hypothesis_adopted_close_stays_provisional_in_reflux(self, admin_client, monkeypatch):
        from joint_understanding_helpers import insert_producer_finding

        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        promoted = _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")
        ju_id = promoted["joint_understanding_session_id"]

        from app.db import get_conn

        now = time.time()
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, run_type, provider, model, prompt_version,
                        schema_version, decision_method, status, is_mock,
                        started_at, completed_at)
                   VALUES (?, 'joint_investigation', 'anthropic', 'claude-sonnet-4-5',
                           'v1', 'v1', 'reasoning_llm', 'succeeded', 0, ?, ?)""",
                (system_id, now, now),
            )
            run_id = cur.lastrowid

        finding_id = insert_producer_finding(
            ju_id=ju_id, system_id=system_id, origin_role="investigation", claim_kind="hypothesis",
            intelligence_run_id=run_id,
        )
        r = admin_client.post(
            f"/joint-understanding/{ju_id}/close",
            json={
                "outcome": "hypothesis_adopted", "outcome_finding_ids": [finding_id],
                "outcome_reason": "仮説を暫定採用して次の調査を進める",
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["outcome_is_provisional"] is True

        data = _reflux(admin_client, headers, thread["thread"]["id"])
        link = data["links"][0]
        assert link["session"]["outcome"] == "hypothesis_adopted"
        assert link["session"]["outcome_is_provisional"] is True


# ---------------------------------------------------------------------------
# 5. Representative chain: Gap -> Journey -> Requirement -> Feature
# ---------------------------------------------------------------------------


class TestRepresentativeChainE2E:
    def _build_chain(self, client, headers):
        # Every related entity carries a real revision -- a resolved but
        # revision-less entity has an empty digest, which
        # `normalize_premise_manifest` refuses outright (a manifest-building
        # defect one layer down in #458's `build_context_bundle`, worked
        # around defensively in `discussion_hypothesis.promote_hypothesis`
        # but deliberately not exercised as "expected" behaviour here).
        client.post("/product-objectives", json={"objective_key": "obj-1"}, headers=headers)
        client.post(
            "/product-objectives/obj-1/revisions",
            json={"title": "Objective 1", "intent": "", "contribution": "", "scope_note": "", "summary": "", "change_note": ""},
            headers=headers,
        )
        client.post("/product-milestones", json={"objective_key": "obj-1", "milestone_key": "ms-1"}, headers=headers)
        client.post(
            "/product-milestones/ms-1/revisions",
            json={"title": "Milestone 1", "target_state": "", "verification_method": "unavailable",
                  "verification_note": "", "sequence_hint": 0, "summary": "", "change_note": ""},
            headers=headers,
        )
        r = client.post(
            "/product-gaps", json={"milestone_key": "ms-1", "gap_key": "gap-1"}, headers=headers,
        )
        assert r.status_code == 201, r.text
        client.post(
            "/product-gaps/gap-1/revisions",
            json={"title": "Gap 1", "current_state": "", "target_state": "", "target_state_mode": "unknown",
                  "interpretation": "", "suggested_priority_note": "", "change_note": ""},
            headers=headers,
        )
        _create_journey(client, headers, "journey-1")
        client.post(
            "/ux-design/journeys/journey-1/revisions",
            json={"title": "Journey 1", "beneficiary": "", "usage_context": "", "entry_trigger": "",
                  "value_arrival": "", "summary": "", "change_note": "", "steps": []},
            headers=headers,
        )
        r = client.post(
            "/ux-design/journeys/journey-1/upstream-refs",
            json={"ref_kind": "product_gap", "target_ref": "gap-1", "note": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        _create_requirement(client, headers, "req-1")
        r = client.post("/product-features", json={"feature_key": "feat-1"}, headers=headers)
        assert r.status_code == 201, r.text
        r = client.post(
            "/product-features/feat-1/requirement-links",
            json={"requirement_key": "req-1", "note": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text

    def test_dependency_two_hops_away_from_the_gap_root_is_captured_and_detected(
        self, admin_client, monkeypatch,
    ):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        self._build_chain(admin_client, headers)

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref="gap-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        promoted = _promote(admin_client, headers, proposal["id"], hyp_id, "chain-1")

        from app.db import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT premise_dependency_manifest_json FROM joint_understanding_session WHERE id = ?",
                (promoted["joint_understanding_session_id"],),
            ).fetchone()
        manifest = json.loads(row["premise_dependency_manifest_json"])
        deps = {(d["target_kind"], d["target_ref"]) for d in manifest}
        assert ("ux_journey", "journey-1") in deps, deps

        # Still current: nothing about the journey has changed yet.
        data = _reflux(admin_client, headers, thread["thread"]["id"])
        assert data["links"][0]["session"]["premise_state"] == "current"

        # Edit the Journey (two hops from the Gap root) and confirm the
        # dependency manifest -- not the root digest -- is what goes stale.
        r = admin_client.post(
            "/ux-design/journeys/journey-1/revisions",
            json={"title": "changed", "beneficiary": "", "usage_context": "", "entry_trigger": "",
                  "value_arrival": "", "summary": "", "change_note": "edited", "steps": []},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        data = _reflux(admin_client, headers, thread["thread"]["id"])
        link = data["links"][0]
        assert link["session"]["premise_state"] == "stale"
        assert link["session"]["premise_reason"] == "dependency_content_changed"
        assert link["reconfirmation_required"] is True


# ---------------------------------------------------------------------------
# 6. System isolation / no side effects
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_reflux_and_promotion_are_invisible_from_another_system(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, name="sys-A")
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")

        other_system_id = _create_system(admin_client, token, name="sys-B")
        other_headers = _headers(token, other_system_id)
        r = admin_client.get(
            f"/assistant/discussion-threads/{thread['thread']['id']}/joint-understanding", headers=other_headers,
        )
        assert r.status_code == 404, r.text
        r = admin_client.post(
            f"/assistant/discussion-proposals/{proposal['id']}/hypotheses/{hyp_id}/promote",
            json={"request_id": "req-B"}, headers=other_headers,
        )
        assert r.status_code == 404, r.text


class TestNoSideEffects:
    def test_promotion_never_touches_the_proposal_thread_or_other_items(self, admin_client, monkeypatch):
        response = json.loads(json.dumps(_HYPOTHESIS_RESPONSE))
        response["field_changes"] = [
            {"field_name": "statement", "subject_ref": "", "current_value": "",
             "proposed_value": "changed", "rationale": ""}
        ]
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch, response)
        hyp_id = proposal["hypotheses"][0]["id"]
        field_item = next(i for i in proposal["items"] if i["item_kind"] == "field")
        assert field_item["status"] == "proposed"

        _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")

        after = _get_proposal(admin_client, headers, proposal["id"])
        after_field = next(i for i in after["items"] if i["item_kind"] == "field")
        assert after_field["status"] == "proposed"
        assert after["hypotheses"][0]["status"] == "promoted"

        requirement = admin_client.get("/ux-design/requirements/req-1", headers=headers).json()
        assert requirement["current_revision"] is None or requirement["current_revision"]["statement"] != "changed"


# ---------------------------------------------------------------------------
# 7. Premise must fail CLOSED, never OPEN, for an unsupported or unresolved
#    root (review-round fix: `_target_digest_with_conn`'s `_UNSUPPORTED`
#    branch used to feed a CONSTANT sentinel string into the content hash,
#    which made premise permanently `current` regardless of real drift).
# ---------------------------------------------------------------------------


class TestPremiseFailClosedForUnsupportedRoot:
    def test_target_digest_with_conn_reports_unsupported_for_an_uncovered_kind(self):
        from app import discussion_hypothesis as dh
        from app.db import get_conn

        with get_conn() as conn:
            result = dh._target_digest_with_conn(conn, 1, "some_future_kind", "ref-1")
        assert result is dh._UNSUPPORTED

    def test_origin_provider_reports_no_content_hash_for_an_unsupported_root(
        self, admin_client, monkeypatch,
    ):
        """The provider must return `content_hash=None` (never a constant
        string) when the root's digest cannot be verified -- `None` makes
        `PremiseBundle.is_complete` False FOREVER for this session, which is
        the finite `invalid` verdict, never `current`."""
        from app import discussion_hypothesis as dh

        monkeypatch.setattr(dh, "_target_digest_with_conn", lambda *a, **k: dh._UNSUPPORTED)
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        promoted = _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")

        from app.db import get_conn

        with get_conn() as conn:
            ju_row = conn.execute(
                "SELECT * FROM joint_understanding_session WHERE id = ?",
                (promoted["joint_understanding_session_id"],),
            ).fetchone()
            facts = dh._discussion_origin_provider(conn, origin_id=hyp_id, system_id=system_id)
        assert facts.content_hash is None
        assert ju_row["premise_content_hash"] is None

    def test_reflux_never_reads_current_when_the_root_kind_cannot_be_verified(
        self, admin_client, monkeypatch,
    ):
        """Regression for the fail-open defect: forcing `_target_digest_
        with_conn` to always report `_UNSUPPORTED` must make the premise
        `invalid` from the FIRST read onward, never `current` -- not even
        once, and not only after some later "change" is detected (there is
        nothing to compare against at all)."""
        from app import discussion_hypothesis as dh

        monkeypatch.setattr(dh, "_target_digest_with_conn", lambda *a, **k: dh._UNSUPPORTED)
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")

        for _ in range(2):
            data = _reflux(admin_client, headers, thread["thread"]["id"])
            link = data["links"][0]
            assert link["session"]["premise_state"] != "current"
            assert link["session"]["premise_state"] == "invalid"
            assert link["session"]["premise_reason"] == "premise_incomplete"
            assert link["reconfirmation_required"] is True
            assert link["current_findings"] == []


class TestJointUnderstandingBridgeCapability:
    def test_overview_finding_bridge_is_false_and_promotion_is_refused(self, admin_client, monkeypatch):
        """Issue #455 review: a kind whose premise this bridge cannot verify
        (`overview_finding` -- `_resolve_overview_finding` calls
        `build_overview(system_id)`, not conn-parametrized) must declare
        `joint_understanding_bridge=False`, and the bridge ENDPOINT must
        actually enforce it -- a capability flag with no backing check would
        be the same "declares supported, cannot back it" defect one layer
        further out. Nothing is persisted on refusal."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        thread = _create_thread(
            admin_client, headers, scope="element", screen_id="overview",
            target_kind="overview_finding", target_ref="finding-x",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]

        r = _promote(admin_client, headers, proposal["id"], hyp_id, "req-A", expect=422)
        assert r.json()["detail"]["code"] == "discussion_hypothesis_bridge_unsupported"

        from app.db import get_conn

        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM assistant_discussion_hypothesis_promotion WHERE hypothesis_id = ?",
                (hyp_id,),
            ).fetchone()["n"]
            assert count == 0
            hyp_row = conn.execute(
                "SELECT status FROM assistant_discussion_proposal_hypothesis WHERE id = ?", (hyp_id,),
            ).fetchone()
            assert hyp_row["status"] == "proposed"

    def test_every_covered_kind_except_overview_finding_declares_the_bridge(self):
        from app import discussion_adapters

        for kind, adapter in discussion_adapters.DISCUSSION_ADAPTERS.items():
            if kind == "overview_finding":
                assert adapter.joint_understanding_bridge is False, kind
            else:
                assert adapter.joint_understanding_bridge is True, kind


# ---------------------------------------------------------------------------
# 8. Root cause fix: a resolved-but-revision-less related entity's empty
#    digest must never make `normalize_premise_manifest` reject the
#    dependency manifest (and must never be silently dropped either).
# ---------------------------------------------------------------------------


class TestNoRevisionDigestSentinel:
    def test_a_revision_less_related_entity_gets_a_stable_sentinel_not_empty(self, admin_client):
        """`_build_chain` in `TestRepresentativeChainE2E` always adds a
        revision to every fixture entity -- this test proves the OPPOSITE
        case (no revision yet) no longer crashes `build_context_bundle` and
        no longer needs a silent empty-manifest fallback."""
        from app import discussion_context_bundle

        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        admin_client.post("/product-objectives", json={"objective_key": "obj-1"}, headers=headers)
        admin_client.post(
            "/product-milestones", json={"objective_key": "obj-1", "milestone_key": "ms-1"}, headers=headers,
        )
        r = admin_client.post(
            "/product-gaps", json={"milestone_key": "ms-1", "gap_key": "gap-1"}, headers=headers,
        )
        assert r.status_code == 201, r.text
        # Deliberately NO revision on "ms-1" -- it resolves (identity
        # exists) but has no comparable content yet.

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref="gap-1",
        )
        bundle = discussion_context_bundle.build_context_bundle(
            system_id, "product_gap", "gap-1", thread_id=thread["thread"]["id"],
        )
        deps = {(d.target_kind, d.target_ref): d.digest for d in bundle.dependencies}
        assert deps.get(("product_milestone", "ms-1")) == discussion_context_bundle.NO_REVISION_DIGEST

    def test_promotion_captures_the_dependency_and_a_later_revision_makes_it_stale(
        self, admin_client, monkeypatch,
    ):
        """The sentinel is a REAL, comparable value: once "ms-1" gets its
        first revision, the dependency digest changes away from the
        sentinel and premise correctly goes stale -- proving the fix is not
        merely "does not crash" but "still detects the eventual change"."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token)
        headers = _headers(token, system_id)
        admin_client.post("/product-objectives", json={"objective_key": "obj-1"}, headers=headers)
        admin_client.post(
            "/product-milestones", json={"objective_key": "obj-1", "milestone_key": "ms-1"}, headers=headers,
        )
        admin_client.post(
            "/product-gaps", json={"milestone_key": "ms-1", "gap_key": "gap-1"}, headers=headers,
        )
        admin_client.post(
            "/product-gaps/gap-1/revisions",
            json={"title": "Gap 1", "current_state": "", "target_state": "", "target_state_mode": "unknown",
                  "interpretation": "", "suggested_priority_note": "", "change_note": ""},
            headers=headers,
        )
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref="gap-1",
        )
        proposal = _generate_with_hypothesis(admin_client, headers, thread["thread"]["id"], monkeypatch)
        hyp_id = proposal["hypotheses"][0]["id"]
        promoted = _promote(admin_client, headers, proposal["id"], hyp_id, "req-A")

        data = _reflux(admin_client, headers, thread["thread"]["id"])
        assert data["links"][0]["session"]["premise_state"] == "current"

        admin_client.post(
            "/product-milestones/ms-1/revisions",
            json={"title": "Milestone 1", "target_state": "", "verification_method": "unavailable",
                  "verification_note": "", "sequence_hint": 0, "summary": "", "change_note": ""},
            headers=headers,
        )
        data = _reflux(admin_client, headers, thread["thread"]["id"])
        link = data["links"][0]
        assert link["session"]["premise_state"] == "stale"
        assert link["session"]["premise_reason"] == "dependency_content_changed"
        assert link["promotion"]["joint_understanding_session_id"] == promoted["joint_understanding_session_id"]
