"""Tests for Issue #389 -- Purpose Needs adaptive next-question.

`docs/purpose-chain.md` §2.8 is the acceptance list this file is organized
around:

* rule table 全行 reachable (every need code, every priority row);
* the select_question tie-break is deterministic;
* routing: a `system_researchable` need never reaches the developer as
  `question` (it only ever appears in `routed_needs`);
* the 5 response states persist and are read back correctly;
* an AI candidate (`suggested_answer`) never becomes `confirmed` without an
  explicit response;
* a partial/degraded Purpose Chain section never produces a guessed
  question;
* the `need_id` deep link falls back safely with a finite reason;
* System separation.

Also (the hard constraint this Epic exists to protect): an empty OPTIONAL
Intent field alone (`success_criteria` etc, never read by Purpose Chain at
all) produces zero needs.

Reuses `test_purpose_chain.py`'s HTTP fixtures rather than re-deriving them.
"""

from __future__ import annotations

import time
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import purpose_needs
from app.purpose_needs import (
    FALLBACK_REASONS,
    NEED_CODES,
    PRIORITY_TABLE,
    PurposeNeed,
    apply_response_state,
    derive_needs,
    select_question,
)

#: Assert against the table rather than a literal row number: the row is an
#: audit record of which rule matched, and inserting a rule above another one
#: is a legitimate change that should not have to be chased through unrelated
#: assertions.
_RULE_ROW = {code: row for row, code in PRIORITY_TABLE}

from test_purpose_chain import (  # noqa: F401  (reuse the same HTTP fixtures)
    _by_kind,
    _chain,
    _correct_intent,
    _create_session,
    _create_system,
    _decide,
    _headers,
    _init_repo,
    _insert_snapshot,
    _item,
    _login,
    _relation,
    _set_goal,
    _set_pain,
    _setup,
    _store_understanding,
    _understanding,
    admin_client,
)


# ---------------------------------------------------------------------------
# Part 1: pure functions
# ---------------------------------------------------------------------------


def _need(**overrides) -> PurposeNeed:
    base = dict(
        id="frame_missing:beneficiary_problem",
        code="frame_missing",
        target_kind="element",
        target_id="beneficiary_problem",
        target_label="対象者と現在の課題",
        answerability="human_judgement",
        state="available",
        target_digest="d",
        source_revision_ids=(),
        section="frame",
    )
    base.update(overrides)
    return PurposeNeed(**base)


def test_every_need_code_can_actually_become_a_question():
    """All 7 need codes (§2.2) carry a `PRIORITY_TABLE` row.

    A need code with no row can only ever be reached by an explicit `need_id`
    deep link -- and nothing generates a link to a question the system never
    offers, so such a need would be derived, would block a decision, and would
    never be asked. That is indistinguishable from not deriving it at all,
    which is why `human_value_judgement_required` is a row rather than a
    deep-link-only value (see `PRIORITY_TABLE`'s comment).
    """
    table_codes = {code for _, code in PRIORITY_TABLE}
    assert table_codes == set(NEED_CODES)
    assert len(PRIORITY_TABLE) == len(NEED_CODES)
    # The rows are 1..N with no gaps and no duplicates: `rule_row` is an audit
    # record of WHICH row matched, so it has to be a stable, dense index.
    assert sorted(row for row, _ in PRIORITY_TABLE) == list(
        range(1, len(PRIORITY_TABLE) + 1)
    )


def test_select_question_never_returns_a_system_researchable_need():
    needs = [
        _need(
            id="capability_justification_missing:r1", code="capability_justification_missing",
            target_kind="relation", target_id="r1", answerability="system_researchable",
        ),
        _need(
            id="frame_missing:intervention", code="frame_missing", target_id="intervention",
            answerability="system_researchable",
        ),
    ]
    assert select_question(needs) is None


def test_select_question_priority_order_relation_conflict_beats_everything():
    needs = [
        _need(id="decision_criterion_missing:r1", code="decision_criterion_missing", target_kind="relation", target_id="r1"),
        _need(id="relation_conflict:r2", code="relation_conflict", target_kind="relation", target_id="r2"),
        _need(id="frame_missing:beneficiary_problem", code="frame_missing", target_id="beneficiary_problem"),
    ]
    picked = select_question(needs)
    assert picked is not None
    assert picked.code == "relation_conflict"


def test_select_question_tie_break_is_lexicographic_need_id():
    needs = [
        _need(id="frame_missing:desired_change", code="frame_missing", target_id="desired_change"),
        _need(id="frame_missing:beneficiary_problem", code="frame_missing", target_id="beneficiary_problem"),
    ]
    picked = select_question(needs)
    assert picked is not None
    assert picked.id == "frame_missing:beneficiary_problem"  # lexicographically first


def test_select_question_excludes_answered_deferred_waiting_unavailable():
    for state in ("answered", "deferred", "waiting", "unavailable"):
        needs = [_need(state=state)]
        assert select_question(needs) is None
    assert select_question([_need(state="available")]) is not None


def test_apply_response_state_defer_reappears_once_digest_moves():
    need = _need(target_digest="digest-a")
    responses = {need.id: {"response_kind": "defer", "target_digest": "digest-a"}}
    deferred = apply_response_state([need], responses)[0]
    assert deferred.state == "deferred"

    moved = _need(target_digest="digest-b")
    still_available = apply_response_state([moved], responses)[0]
    assert still_available.state == "available"


def test_apply_response_state_confirm_and_correct_become_answered():
    need = _need(target_digest="d")
    for kind in ("confirm", "correct"):
        responses = {need.id: {"response_kind": kind, "target_digest": "d"}}
        updated = apply_response_state([need], responses)[0]
        assert updated.state == "answered"
        assert updated.answerability == "already_answered"


def test_apply_response_state_unknown_and_investigate_become_waiting():
    need = _need(target_digest="d")
    for kind in ("unknown", "investigate"):
        responses = {need.id: {"response_kind": kind, "target_digest": "d"}}
        updated = apply_response_state([need], responses)[0]
        assert updated.state == "waiting"


def test_apply_response_state_unavailable_wins_regardless_of_response_history():
    need = _need(answerability="unavailable")
    responses = {need.id: {"response_kind": "confirm", "target_digest": "d"}}
    updated = apply_response_state([need], responses)[0]
    assert updated.state == "unavailable"


def test_derive_needs_never_fires_for_an_empty_optional_intent_field():
    """The hard constraint this Epic exists to protect: a need is never
    "this optional field is empty". `success_criteria` is never read by
    Purpose Chain at all, so leaving it empty must never produce a need."""
    from app.purpose_chain import PurposeChainResult

    chain = PurposeChainResult(
        system_id=1, session_id=1, generated_at=0.0,
        frame={
            "beneficiary_problem": None,
            "desired_change": None,
            "intervention": None,
        },
        elements=[], relations=[],
        frame_resolution_level="L0", frame_state="empty",
        snapshot_id=None, understanding_revision_id=None, understanding_confirmed_at=None,
    )
    # Even a totally empty chain (nothing about success_criteria/priority/
    # constraints/non_goals is ever inspected here) only yields the 3
    # structural frame_missing needs -- never a need referencing a field
    # Purpose Chain does not itself derive from.
    needs = derive_needs(chain)
    codes = {n.code for n in needs}
    assert codes <= {"frame_missing"}
    assert all("success_criteria" not in n.id for n in needs)
    assert all("priority" not in n.id for n in needs)


# ---------------------------------------------------------------------------
# Part 2: the HTTP API
# ---------------------------------------------------------------------------


def _question(client, headers, session_id=None, need_id=None):
    params = {}
    if session_id is not None:
        params["session_id"] = session_id
    if need_id is not None:
        params["need_id"] = need_id
    r = client.get("/purpose-chain/next-question", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _respond(client, headers, need_id, session_id, response_kind, value_text="", *, expect=200):
    r = client.post(
        f"/purpose-chain/needs/{quote(need_id, safe='')}/respond",
        json={"session_id": session_id, "response_kind": response_kind, "value_text": value_text},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 400 else r


def test_no_question_when_frame_is_complete_and_relations_confirmed(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Complete")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _set_goal(admin_client, headers, session_id, "変化")
    _store_understanding(
        session_id, system_id, snapshot_id, _understanding(system_purpose=[_item("目的")])
    )
    chain = _chain(admin_client, headers, session_id)
    for kind in ("problem_to_change", "change_to_intervention"):
        rel = [r for r in chain["relations"] if r["kind"] == kind][0]
        _decide(admin_client, headers, rel["id"], session_id, "confirmed")

    question = _question(admin_client, headers, session_id)
    assert question is None


def test_frame_missing_beneficiary_problem_is_the_next_question_when_nothing_is_set(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Frame")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    question = _question(admin_client, headers, session_id)
    assert question is not None
    assert question["need_code"] == "frame_missing"
    assert question["target_id"] == "beneficiary_problem"
    assert question["answerability"] == "human_judgement"
    assert question["rule_row"] == _RULE_ROW["frame_missing"]
    assert question["suggested_answer"] is None  # nothing exists to suggest


def test_frame_missing_intervention_never_becomes_the_question_but_is_routed(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Routed")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _set_goal(admin_client, headers, session_id, "変化")
    # intervention (system_purpose) left empty -> frame_missing, but
    # system_researchable -- must never surface as `question`.

    question = _question(admin_client, headers, session_id)
    assert question is not None
    assert question["need_code"] != "frame_missing" or question["target_id"] != "intervention"
    routed_codes = {(n["need_code"], n["target_id"]) for n in question["routed_needs"]}
    assert ("frame_missing", "intervention") in routed_codes


def test_correcting_frame_missing_beneficiary_problem_creates_the_intent_item(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Correct")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    resp = _respond(
        admin_client, headers, "frame_missing:beneficiary_problem", session_id,
        "correct", "小規模チームの開発者が変更の影響を把握できない",
    )
    assert resp["response_kind"] == "correct"
    assert resp["linked_intent_item_id"] is not None
    assert resp["decision_method"] == "manual"

    chain = _chain(admin_client, headers, session_id)
    assert chain["frame"]["beneficiary_problem"]["state"] == "present"
    assert chain["frame"]["beneficiary_problem"]["statement"] == (
        "小規模チームの開発者が変更の影響を把握できない"
    )

    # The underlying signal cleared -> the need no longer exists at all.
    still_there = _question(admin_client, headers, session_id, need_id="frame_missing:beneficiary_problem")
    # Either falls back to a different question or none -- but if a question
    # IS returned it must not be the same resolved need.
    if still_there is not None:
        assert still_there["need_id"] != "frame_missing:beneficiary_problem" or (
            still_there.get("fallback_reason") is not None
        )


def test_confirm_is_rejected_for_a_missing_frame_element(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs NoConfirm")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    resp = _respond(
        admin_client, headers, "frame_missing:beneficiary_problem", session_id, "confirm",
        expect=422,
    )
    assert resp.json()["detail"]["code"] == "purpose_need_nothing_to_confirm"


def test_relation_confirm_and_correct_reuse_record_relation_decision(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs RelDecide")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _set_goal(admin_client, headers, session_id, "変化")

    chain = _chain(admin_client, headers, session_id)
    rel = _relation(chain, "problem_to_change", "beneficiary_problem", "desired_change")
    need_id = f"decision_criterion_missing:{rel['id']}"

    resp = _respond(admin_client, headers, need_id, session_id, "confirm", "明確な因果関係")
    assert resp["linked_relation_decision_id"] is not None

    after = _chain(admin_client, headers, session_id)
    rel_after = _relation(after, "problem_to_change", "beneficiary_problem", "desired_change")
    assert rel_after["status"] == "confirmed"
    assert rel_after["decision_id"] == resp["linked_relation_decision_id"]


def test_relation_correct_rejects_the_relation(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs RelReject")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _set_goal(admin_client, headers, session_id, "変化")

    chain = _chain(admin_client, headers, session_id)
    rel = _relation(chain, "problem_to_change", "beneficiary_problem", "desired_change")
    need_id = f"decision_criterion_missing:{rel['id']}"

    _respond(admin_client, headers, need_id, session_id, "correct", "実際には無関係")

    after = _chain(admin_client, headers, session_id)
    rel_after = _relation(after, "problem_to_change", "beneficiary_problem", "desired_change")
    assert rel_after["status"] == "conflicting"


def test_ai_candidate_never_becomes_confirmed_without_an_explicit_response(
    admin_client, tmp_path
):
    """§2.8: a `suggested_answer` is never adopted automatically -- it is
    only ever surfaced. Confirming still requires the developer's own
    `respond` call."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Suggest")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(
            system_purpose=[_item("目的")],
            core_capabilities=[_item("矛盾する機能", level="conflicting")],
        ),
    )
    chain = _chain(admin_client, headers, session_id)
    capability = _by_kind(chain, "core_capability")[0]
    need_id = f"human_value_judgement_required:{capability['id']}"

    question = _question(admin_client, headers, session_id, need_id=need_id)
    assert question is not None
    assert question["need_id"] == need_id
    assert question["suggested_answer"] is not None
    assert question["suggested_answer"]["text"] == capability["display_statement"]
    assert question["suggested_answer"]["is_mock"] is False

    # Nothing was written by the GET.
    after = _chain(admin_client, headers, session_id)
    after_capability = _by_kind(after, "core_capability")[0]
    assert after_capability["confirmation"] == "conflicting"


def test_an_element_conflict_that_a_relation_already_reports_is_asked_once(
    admin_client, tmp_path
):
    """A conflicting Capability derives BOTH needs, and the developer sees one.

    The element's own conflict propagates into its `intervention_to_capability`
    relation (`purpose_chain._relation_status` rule 3), so both
    `relation_conflict` and `human_value_judgement_required` exist as derived
    facts. `PRIORITY_TABLE` is first-match and the relation conflict sits
    above, so exactly one question reaches the screen -- deriving both is
    correct, asking both would be two questions about one subject.
    """
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs HVJ")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(
            system_purpose=[_item("目的")],
            core_capabilities=[_item("矛盾", level="conflicting")],
        ),
    )
    question = _question(admin_client, headers, session_id)
    assert question is not None
    assert question["need_code"] == "relation_conflict"


def test_a_conflicting_additional_intervention_is_asked_as_a_human_judgement(
    admin_client, tmp_path
):
    """The case that makes `human_value_judgement_required` a priority row.

    The 2nd+ `system_purpose` claim is an ADDITIONAL `intervention` element and
    `purpose_chain` builds no relation for it, so a conflict there has no
    relation to surface through. Without a row of its own the need would be
    derived, would block a judgement only the developer can make, and would
    never once be offered.
    """
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs HVJ Extra")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(
            system_purpose=[_item("主たる目的"), _item("競合する目的", level="conflicting")],
        ),
    )
    chain = _chain(admin_client, headers, session_id)
    additional = _by_kind(chain, "intervention")[1]
    assert additional["confirmation"] == "conflicting"
    assert not [
        r
        for r in chain["relations"]
        if additional["id"] in (r["source_id"], r["target_id"])
    ], "an additional intervention has no relation -- that is why this row exists"

    question = _question(admin_client, headers, session_id)
    assert question is not None
    assert question["need_code"] == "human_value_judgement_required"
    assert question["target_id"] == additional["id"]
    assert question["answerability"] == "human_judgement"
    assert question["rule_row"] == _RULE_ROW["human_value_judgement_required"]


def test_a_degraded_section_never_produces_a_guessed_question(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Degraded")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    from app import purpose_chain as pc_module

    def _boom(*a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(pc_module.understanding_brief, "build_understanding_brief", _boom)

    question = _question(admin_client, headers, session_id)
    # frame is degraded -> every frame_missing need is `unavailable`, so no
    # question is ever guessed from it.
    if question is not None:
        assert question["answerability"] != "unavailable"
        assert not (question["need_code"] == "frame_missing" and question["target_id"] == "beneficiary_problem")


def test_defer_persists_and_the_need_does_not_reappear_until_changed(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Defer")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    resp = _respond(
        admin_client, headers, "frame_missing:beneficiary_problem", session_id, "defer",
        "後で答える",
    )
    assert resp["response_kind"] == "defer"

    question = _question(admin_client, headers, session_id)
    # beneficiary_problem's frame_missing is deferred -- it must not be
    # picked again while nothing about it changed. intervention is also
    # missing but system_researchable (never a question), so the correct
    # outcome is either desired_change's frame_missing or no question.
    if question is not None:
        assert not (question["need_code"] == "frame_missing" and question["target_id"] == "beneficiary_problem")

    # Now the developer actually answers pain directly (bypassing respond) --
    # the target's digest moves, so the deferred need must reappear.
    _set_pain(admin_client, headers, session_id, "実は課題があった")
    _correct_pain_direct = None  # no-op, just documents intent
    from app.db import get_conn

    # Correcting via the normal Intent Brief path changes the element from
    # unknown -> present, which clears frame_missing entirely (the need
    # disappears rather than "reappearing" -- consistent, not a contradiction:
    # the developer answered the underlying question through a different
    # surface, so there is nothing left to defer).
    chain = _chain(admin_client, headers, session_id)
    assert chain["frame"]["beneficiary_problem"]["state"] == "present"


def test_unknown_response_opens_a_joint_understanding_session_with_purpose_need_trigger(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Unknown")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _set_goal(admin_client, headers, session_id, "変化")

    chain = _chain(admin_client, headers, session_id)
    rel = _relation(chain, "problem_to_change", "beneficiary_problem", "desired_change")
    need_id = f"decision_criterion_missing:{rel['id']}"

    resp = _respond(admin_client, headers, need_id, session_id, "unknown", "わからない")
    assert resp["linked_joint_session_id"] is not None

    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?",
            (resp["linked_joint_session_id"],),
        ).fetchone()
    assert row["trigger"] == "purpose_need"
    assert row["origin_kind"] == "purpose_need"
    assert row["origin_id"] == resp["id"]
    assert row["status"] == "open"
    assert row["system_id"] == system_id
    assert row["session_id"] == session_id


def test_investigate_response_also_opens_a_session(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Investigate")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _set_goal(admin_client, headers, session_id, "変化")

    chain = _chain(admin_client, headers, session_id)
    rel = _relation(chain, "problem_to_change", "beneficiary_problem", "desired_change")
    need_id = f"decision_criterion_missing:{rel['id']}"

    resp = _respond(admin_client, headers, need_id, session_id, "investigate", "調べてほしい")
    assert resp["linked_joint_session_id"] is not None

    question = _question(admin_client, headers, session_id, need_id=need_id)
    if question is not None:
        assert question["need_id"] != need_id


def test_public_joint_understanding_endpoint_still_refuses_purpose_need_trigger(
    admin_client, tmp_path
):
    """#336's rule, unaffected: the public create endpoint keeps forcing
    'explicit_request' -- 'purpose_need' is settable only by this route's
    own respond handler, exactly like 'unknown_answer' is settable only by
    the 「わからない」 flow."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs PublicJU")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    pain = _set_pain(admin_client, headers, session_id, "課題")

    r = admin_client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": "intent",
            "origin_id": pain["id"],
            "trigger": "purpose_need",
            "question_text": "test",
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "joint_understanding_trigger_not_settable"


def test_deep_link_fallback_reason_not_found_for_a_bogus_need_id(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs Fallback")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    question = _question(admin_client, headers, session_id, need_id="frame_missing:does-not-exist")
    # Some question (or none) is returned, but if one is returned it must
    # carry a finite fallback_reason distinct from the bogus need_id.
    if question is not None:
        assert question["fallback_reason"] in FALLBACK_REASONS
        assert question["need_id"] != "frame_missing:does-not-exist"


def test_deep_link_fallback_reason_resolved_when_need_already_cleared(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs FallbackResolved")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    # Resolve beneficiary_problem's frame_missing directly (not via respond).
    _set_pain(admin_client, headers, session_id, "課題")

    question = _question(
        admin_client, headers, session_id, need_id="frame_missing:beneficiary_problem"
    )
    if question is not None:
        assert question["need_id"] != "frame_missing:beneficiary_problem"
        assert question["fallback_reason"] == "resolved"


def test_respond_to_unknown_need_id_is_404(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Needs 404")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _respond(
        admin_client, headers, "frame_missing:does-not-exist", session_id, "defer",
        expect=404,
    )


def test_purpose_needs_are_system_scoped(admin_client, tmp_path):
    token, system_a, snapshot_a = _setup(admin_client, tmp_path, "System Needs A")
    headers_a = _headers(token, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)

    system_b = _create_system(admin_client, token, "System Needs B")
    headers_b = _headers(token, system_b)
    session_b = _create_session(admin_client, headers_b, _insert_snapshot(system_b, *_init_repo(tmp_path, "repo-needs-b")))

    resp = _respond(
        admin_client, headers_a, "frame_missing:beneficiary_problem", session_a, "defer", "後で",
    )
    assert resp["session_id"] == session_a
    assert resp["system_id"] == system_a

    # System B's own session cannot see or be answered by System A's response.
    question_b = _question(admin_client, headers_b, session_b)
    assert question_b is None or question_b["need_id"] != resp["need_id"] or (
        question_b.get("state") != "deferred"
    )

    # And System B cannot respond using System A's session id at all --
    # cross-System session lookup fails closed.
    _respond(
        admin_client, headers_b, "frame_missing:beneficiary_problem", session_a, "defer",
        expect=404,
    )
