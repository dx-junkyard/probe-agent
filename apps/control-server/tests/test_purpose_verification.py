"""Tests for Issue #391 -- Purpose Verification (Experience Hypothesis /
Outcome Criterion / Reuse Hypothesis).

`docs/purpose-chain.md` §4 is the acceptance list this file is organized
around:

* creation is only ever offered alongside a currently-available justifying
  need, never because a resolution level happens to be low;
* `decision_criterion_missing` is what makes an Outcome Criterion creatable;
  `capability_justification_missing` is what makes an Experience/Reuse
  Hypothesis creatable;
* an outcome's `observed`/`contradicted` verdict is always the developer's
  own recorded judgement, never inferred from evidence text;
* human-reported evidence and runtime-observed evidence stay in separate
  columns;
* a synthetic result carries an explicit, visible flag;
* lineage is an explicit id, resolved fresh at read time (`none` / `linked`
  / `unresolved`), never a System-wide existence check;
* `L3` becomes reachable once, and only once, an Outcome Criterion with all
  four fields targets an already-`L2` element;
* §4.5's at-most-one verification prompt, and its priority order;
* System / session isolation.

Reuses `test_purpose_chain.py`'s HTTP fixtures.
"""

from __future__ import annotations

import time

import pytest

from app import purpose_verification
from app.purpose_needs import PurposeNeed
from app.purpose_verification import (
    CREATABLE_NEED_CODES,
    NotCreatable,
    NotFound,
    creatable_need,
    select_verification_prompt,
)

from test_purpose_chain import (  # noqa: F401  (reuse the same HTTP fixtures)
    _by_kind,
    _chain,
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
        id="decision_criterion_missing:problem_to_change:beneficiary_problem->desired_change",
        code="decision_criterion_missing",
        target_kind="relation",
        target_id="problem_to_change:beneficiary_problem->desired_change",
        target_label="課題 → 望ましい変化",
        answerability="human_judgement",
        state="available",
        target_digest="d",
        source_revision_ids=(),
        section="relations",
    )
    base.update(overrides)
    return PurposeNeed(**base)


def test_creatable_need_codes_are_the_documented_design_decision():
    assert CREATABLE_NEED_CODES == {
        "outcome_criterion": ("decision_criterion_missing",),
        "experience_hypothesis": ("capability_justification_missing",),
        "reuse_hypothesis": ("capability_justification_missing",),
    }


def test_creatable_need_accepts_the_matching_code():
    need = _need()
    resolved = creatable_need([need], "outcome_criterion", need.id)
    assert resolved is need


def test_creatable_need_rejects_wrong_code():
    need = _need(code="relation_conflict", id="relation_conflict:r1")
    with pytest.raises(NotCreatable):
        creatable_need([need], "outcome_criterion", need.id)


def test_creatable_need_rejects_unavailable_state():
    need = _need(state="deferred")
    with pytest.raises(NotCreatable):
        creatable_need([need], "outcome_criterion", need.id)


def test_creatable_need_rejects_unknown_id():
    with pytest.raises(NotCreatable):
        creatable_need([], "outcome_criterion", "nope:nope")


def test_select_verification_prompt_priority_outcome_before_experience():
    outcome_need = _need()
    capability_need = _need(
        id="capability_justification_missing:r2",
        code="capability_justification_missing",
        target_kind="relation",
        target_id="intervention_to_capability:intervention->cap1",
        target_label="機能",
    )
    candidate = select_verification_prompt(
        [outcome_need, capability_need], existing_targets={}
    )
    assert candidate is not None
    assert candidate.concept_kind == "outcome_criterion"
    assert candidate.need is outcome_need


def test_select_verification_prompt_experience_before_reuse_on_the_same_need():
    capability_need = _need(
        id="capability_justification_missing:r2",
        code="capability_justification_missing",
        target_kind="relation",
        target_id="intervention_to_capability:intervention->cap1",
        target_label="機能",
    )
    candidate = select_verification_prompt([capability_need], existing_targets={})
    assert candidate is not None
    assert candidate.concept_kind == "experience_hypothesis"


def test_select_verification_prompt_skips_a_target_that_already_has_a_row():
    capability_need = _need(
        id="capability_justification_missing:r2",
        code="capability_justification_missing",
        target_kind="relation",
        target_id="intervention_to_capability:intervention->cap1",
        target_label="機能",
    )
    existing = {"experience_hypothesis": {"relation:intervention_to_capability:intervention->cap1"}}
    candidate = select_verification_prompt([capability_need], existing_targets=existing)
    # experience_hypothesis is suppressed for this target, so reuse_hypothesis
    # (creatable from the SAME need) is offered instead.
    assert candidate is not None
    assert candidate.concept_kind == "reuse_hypothesis"


def test_select_verification_prompt_returns_none_when_nothing_creatable():
    unrelated = _need(code="relation_unknown", id="relation_unknown:r3")
    assert select_verification_prompt([unrelated], existing_targets={}) is None


def test_select_verification_prompt_ignores_non_available_needs():
    need = _need(state="deferred")
    assert select_verification_prompt([need], existing_targets={}) is None


# ---------------------------------------------------------------------------
# Part 2: the HTTP API
# ---------------------------------------------------------------------------


def _verification(client, headers, session_id=None):
    params = {} if session_id is None else {"session_id": session_id}
    r = client.get("/purpose-chain/verification", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _prompt(client, headers, session_id=None):
    params = {} if session_id is None else {"session_id": session_id}
    r = client.get("/purpose-chain/verification-prompt", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _create_outcome(client, headers, session_id, need_id, *, expect=201, **fields):
    body = {
        "session_id": session_id,
        "need_id": need_id,
        "measure": fields.get("measure", "継続率"),
        "baseline_value": fields.get("baseline_value", "0%"),
        "target_value": fields.get("target_value", "20%"),
        "observation_window": fields.get("observation_window", "30日間"),
    }
    r = client.post("/purpose-chain/outcome-criteria", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 400 else r


def _create_experience(client, headers, session_id, need_id, *, expect=201, statement="最初の成功体験"):
    r = client.post(
        "/purpose-chain/experience-hypotheses",
        json={"session_id": session_id, "need_id": need_id, "statement": statement},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 400 else r


def _create_reuse(client, headers, session_id, need_id, *, expect=201, statement="再訪のきっかけ"):
    r = client.post(
        "/purpose-chain/reuse-hypotheses",
        json={"session_id": session_id, "need_id": need_id, "statement": statement},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 400 else r


def _setup_with_decision_criterion_need(client, tmp_path, name):
    """A session whose `problem_to_change` relation is `hypothesis` (pain +
    goal both set, never decided) -- the ONLY thing #391 says makes an
    Outcome Criterion creatable."""
    token, system_id, snapshot_id = _setup(client, tmp_path, name)
    headers = _headers(token, system_id)
    session_id = _create_session(client, headers, snapshot_id)
    _set_pain(client, headers, session_id, "課題")
    _set_goal(client, headers, session_id, "変化")
    chain = _chain(client, headers, session_id)
    rel = _relation(chain, "problem_to_change", "beneficiary_problem", "desired_change")
    need_id = f"decision_criterion_missing:{rel['id']}"
    return token, system_id, headers, session_id, need_id, rel


def _setup_with_capability_need(client, tmp_path, name):
    """A session with a Capability whose `intervention_to_capability`
    relation is `hypothesis` -- what makes Experience/Reuse Hypothesis
    creatable."""
    token, system_id, snapshot_id = _setup(client, tmp_path, name)
    headers = _headers(token, system_id)
    session_id = _create_session(client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的")], core_capabilities=[_item("機能")]),
    )
    chain = _chain(client, headers, session_id)
    capability = _by_kind(chain, "core_capability")[0]
    rel = _relation(chain, "intervention_to_capability", "intervention", capability["id"])
    need_id = f"capability_justification_missing:{rel['id']}"
    return token, system_id, headers, session_id, need_id, capability, rel


# --- creation is need-gated, never "resolution level is low" ----------------


def test_outcome_criterion_not_creatable_without_a_matching_need(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Verify NoNeed")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    resp = _create_outcome(
        admin_client, headers, session_id, "decision_criterion_missing:nope->nope", expect=422
    )
    assert resp.json()["detail"]["code"] == "purpose_verification_not_creatable"


def test_outcome_criterion_not_creatable_from_a_wrong_code_need(admin_client, tmp_path):
    """A real, currently-available need whose CODE is not
    `decision_criterion_missing` must still be refused -- e.g. a
    `capability_justification_missing` need must not justify an Outcome
    Criterion (design decision 1)."""
    _t, _s, headers, session_id, cap_need_id, _cap, _rel = _setup_with_capability_need(
        admin_client, tmp_path, "System Verify WrongCode"
    )
    resp = _create_outcome(admin_client, headers, session_id, cap_need_id, expect=422)
    assert resp.json()["detail"]["code"] == "purpose_verification_not_creatable"


def test_outcome_criterion_creatable_via_decision_criterion_missing(admin_client, tmp_path):
    _t, _s, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify Outcome"
    )
    created = _create_outcome(admin_client, headers, session_id, need_id)
    assert created["state"] == "proposed"
    assert created["source_need_id"] == need_id
    assert created["source_need_code"] == "decision_criterion_missing"
    assert created["decision_method"] == "manual"
    assert created["lineage_state"] == "none"


def test_experience_and_reuse_hypothesis_creatable_via_capability_justification_missing(
    admin_client, tmp_path
):
    _t, _s, headers, session_id, need_id, _cap, _rel = _setup_with_capability_need(
        admin_client, tmp_path, "System Verify CapNeed"
    )
    experience = _create_experience(admin_client, headers, session_id, need_id)
    assert experience["state"] == "proposed"
    assert experience["source_need_code"] == "capability_justification_missing"

    reuse = _create_reuse(admin_client, headers, session_id, need_id)
    assert reuse["state"] == "proposed"
    assert reuse["source_need_code"] == "capability_justification_missing"


def test_experience_hypothesis_not_creatable_via_decision_criterion_missing(
    admin_client, tmp_path
):
    _t, _s, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify ExpWrong"
    )
    resp = _create_experience(admin_client, headers, session_id, need_id, expect=422)
    assert resp.json()["detail"]["code"] == "purpose_verification_not_creatable"


# --- confirm / retire ---------------------------------------------------------


def test_confirm_and_retire_experience_hypothesis(admin_client, tmp_path):
    _t, _s, headers, session_id, need_id, _cap, _rel = _setup_with_capability_need(
        admin_client, tmp_path, "System Verify ConfirmRetire"
    )
    created = _create_experience(admin_client, headers, session_id, need_id)

    confirmed = admin_client.post(
        f"/purpose-chain/experience-hypotheses/{created['id']}/confirm",
        json={"session_id": session_id},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "confirmed"
    assert confirmed.json()["confirmed_by"] == "root"

    retired = admin_client.post(
        f"/purpose-chain/experience-hypotheses/{created['id']}/retire",
        json={"session_id": session_id, "reason": "前提が変わった"},
        headers=headers,
    )
    assert retired.status_code == 200, retired.text
    body = retired.json()
    assert body["state"] == "retired"
    assert body["retirement_reason"] == "前提が変わった"
    assert body["retired_by"] == "root"


def test_confirm_unknown_hypothesis_id_is_404(admin_client, tmp_path):
    _t, _s, headers, session_id, _need_id, _cap, _rel = _setup_with_capability_need(
        admin_client, tmp_path, "System Verify 404"
    )
    resp = admin_client.post(
        "/purpose-chain/experience-hypotheses/999999/confirm",
        json={"session_id": session_id},
        headers=headers,
    )
    assert resp.status_code == 404


# --- outcome result: never inferred, always the developer's own verdict -----


def test_outcome_result_records_the_developers_own_verdict_never_infers_it(
    admin_client, tmp_path
):
    _t, _s, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify Result"
    )
    created = _create_outcome(admin_client, headers, session_id, need_id)
    admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/confirm",
        json={"session_id": session_id},
        headers=headers,
    )

    resp = admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/result",
        json={
            "session_id": session_id,
            "source": "human_reported",
            "verdict": "supports",
            "evidence_text": "ユーザーインタビューで確認できた",
            "is_synthetic": False,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "observed"
    assert body["human_reported_verdict"] == "supports"
    assert body["human_reported_evidence"] == "ユーザーインタビューで確認できた"
    assert body["human_reported_by"] == "root"
    # The separate runtime-observation columns are untouched.
    assert body["runtime_observation_text"] is None
    assert body["runtime_observation_verdict"] is None
    assert body["is_synthetic"] is False


def test_human_reported_and_runtime_observed_evidence_stay_in_separate_columns(
    admin_client, tmp_path
):
    _t, _s, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify SeparateColumns"
    )
    created = _create_outcome(admin_client, headers, session_id, need_id)

    admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/result",
        json={
            "session_id": session_id, "source": "human_reported",
            "verdict": "contradicts", "evidence_text": "人による確認では否定的だった",
        },
        headers=headers,
    )
    after_human = admin_client.get(
        "/purpose-chain/verification", params={"session_id": session_id}, headers=headers
    ).json()
    row = after_human["outcome_criteria"][0]
    assert row["state"] == "contradicted"
    assert row["human_reported_evidence"] == "人による確認では否定的だった"
    assert row["runtime_observation_text"] is None

    resp = admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/result",
        json={
            "session_id": session_id, "source": "runtime_observed",
            "verdict": "supports", "evidence_text": "実行時の観測は肯定的だった",
            "is_synthetic": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The runtime write does not erase the earlier human-reported column --
    # they are genuinely separate facts, not one shared "result" field.
    assert body["human_reported_evidence"] == "人による確認では否定的だった"
    assert body["human_reported_verdict"] == "contradicts"
    assert body["runtime_observation_text"] == "実行時の観測は肯定的だった"
    assert body["runtime_observation_verdict"] == "supports"
    assert body["is_synthetic"] is True
    # A later supporting runtime observation must not hide contradictory
    # human-reported evidence.
    assert body["state"] == "contradicted"
    assert body["human_reported_state"] == "contradicted"
    assert body["runtime_observation_state"] == "observed"
    assert body["human_reported_is_synthetic"] is False
    assert body["runtime_observation_is_synthetic"] is True


def test_not_observed_and_not_computed_are_explicit_source_scoped_facts(admin_client, tmp_path):
    _t, _s, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify Unavailable"
    )
    created = _create_outcome(admin_client, headers, session_id, need_id)
    first = admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/unavailable",
        json={
            "session_id": session_id, "source": "runtime_observed",
            "state": "not_observed", "reason": "analytics がない",
        }, headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["runtime_observation_state"] == "not_observed"
    assert first.json()["runtime_observation_text"] == "analytics がない"

    second = admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/unavailable",
        json={
            "session_id": session_id, "source": "human_reported",
            "state": "not_computed", "reason": "canonical mapping がない",
        }, headers=headers,
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["human_reported_state"] == "not_computed"
    assert body["runtime_observation_state"] == "not_observed"
    assert body["state"] == "not_observed"


# --- lineage: explicit id, resolved fresh, never a System-wide check --------


def _insert_experiment(system_id, snapshot_id, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO experiments
                   (system_id, feature_id, objective, snapshot_id, baseline_commit,
                    config_revision, execution_config, status, created_at)
               VALUES (?, 'feature', 'improve', ?, ?, 'v1', '{}', 'draft', ?)""",
            (system_id, snapshot_id, commit_sha, now),
        )
        return cur.lastrowid


def test_outcome_lineage_none_then_linked_then_unresolved(admin_client, tmp_path):
    token, system_id, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify Lineage"
    )
    created = _create_outcome(admin_client, headers, session_id, need_id)
    assert created["lineage_state"] == "none"

    from app.db import get_conn

    with get_conn() as conn:
        snap = conn.execute(
            "SELECT id, repo_path, commit_sha FROM repository_snapshots WHERE system_id = ?",
            (system_id,),
        ).fetchone()
    experiment_id = _insert_experiment(system_id, snap["id"], snap["commit_sha"])

    linked = admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/link",
        json={"session_id": session_id, "experiment_id": experiment_id},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["lineage_state"] == "linked"
    assert linked.json()["experiment_id"] == experiment_id

    with get_conn() as conn:
        conn.execute("DELETE FROM experiments WHERE id = ?", (experiment_id,))

    after_delete = admin_client.get(
        "/purpose-chain/verification", params={"session_id": session_id}, headers=headers
    ).json()
    row = after_delete["outcome_criteria"][0]
    # The id is still recorded (never silently cleared) but no longer
    # resolves -- §4.3's genuine third value, rendered as 「関連不明」.
    assert row["experiment_id"] == experiment_id
    assert row["lineage_state"] == "unresolved"


def test_link_rejects_an_experiment_id_from_another_system(admin_client, tmp_path):
    _t, sys_a, headers_a, session_a, need_a, _rel_a = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify LinkA"
    )
    created = _create_outcome(admin_client, headers_a, session_a, need_a)

    token_b, sys_b, snapshot_b = _setup(admin_client, tmp_path, "System Verify LinkB")
    experiment_b = _insert_experiment(sys_b, snapshot_b, "deadbeef")

    resp = admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/link",
        json={"session_id": session_a, "experiment_id": experiment_b},
        headers=headers_a,
    )
    assert resp.status_code == 404


def test_link_rejects_two_competing_lineage_targets(admin_client, tmp_path):
    _t, system_id, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify TwoLinks"
    )
    created = _create_outcome(admin_client, headers, session_id, need_id)
    resp = admin_client.post(
        f"/purpose-chain/outcome-criteria/{created['id']}/link",
        json={"session_id": session_id, "experiment_id": 1, "candidate_version_id": 2},
        headers=headers,
    )
    assert resp.status_code == 422


# --- L3 reachability (§4.4) --------------------------------------------------


def test_l3_is_reachable_once_a_full_outcome_criterion_targets_an_l2_element(
    admin_client, tmp_path
):
    token, system_id, headers, session_id, need_id, rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify L3"
    )
    # The `decision_criterion_missing` need exists only while the relation is
    # still `hypothesis` (unconfirmed) -- create the criterion FIRST, while
    # it is still creatable. The row itself persists after the relation is
    # later confirmed and the underlying need disappears; L3's own check
    # (`full_outcome_criterion_target_ids`) only cares that a qualifying row
    # exists now, never whether its justifying need still does.
    created = _create_outcome(admin_client, headers, session_id, need_id)
    assert created["state"] == "proposed"

    # Confirm the `problem_to_change` relation so `beneficiary_problem`
    # reaches L2 -- its PRIMARY relation is `problem_to_change` alone (§1.4
    # design decision 1: a source-side frame slot uses its own OUTGOING
    # relation), independent of `intervention`'s own state, so this does not
    # require ever populating System Purpose.
    _decide(admin_client, headers, rel["id"], session_id, "confirmed")

    after = _chain(admin_client, headers, session_id)
    assert after["frame"]["beneficiary_problem"]["resolution_level"] == "L3"


def test_l3_never_reached_by_an_incomplete_outcome_criterion(admin_client, tmp_path):
    token, system_id, headers, session_id, need_id, rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify L3 Partial"
    )
    # baseline_value deliberately left empty.
    _create_outcome(
        admin_client, headers, session_id, need_id, baseline_value="",
    )
    _decide(admin_client, headers, rel["id"], session_id, "confirmed")

    after = _chain(admin_client, headers, session_id)
    assert after["frame"]["beneficiary_problem"]["resolution_level"] == "L2"


# --- §4.5 verification prompt endpoint ---------------------------------------


def test_verification_prompt_none_when_nothing_creatable(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Verify PromptNone")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    assert _prompt(admin_client, headers, session_id) is None


def test_verification_prompt_offers_outcome_criterion_and_carries_need_copy(
    admin_client, tmp_path
):
    _t, _s, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify PromptOutcome"
    )
    prompt = _prompt(admin_client, headers, session_id)
    assert prompt is not None
    assert prompt["concept_kind"] == "outcome_criterion"
    assert prompt["need_id"] == need_id
    assert prompt["prompt"]
    assert prompt["why_now"]
    assert prompt["blocked_decision"]
    assert prompt["observation_hint"]


def test_verification_prompt_disappears_once_a_row_exists_for_that_target(
    admin_client, tmp_path
):
    _t, _s, headers, session_id, need_id, _rel = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify PromptGone"
    )
    assert _prompt(admin_client, headers, session_id) is not None
    _create_outcome(admin_client, headers, session_id, need_id)
    assert _prompt(admin_client, headers, session_id) is None


# --- System / session isolation ----------------------------------------------


def test_verification_rows_are_system_scoped(admin_client, tmp_path):
    _t, sys_a, headers_a, session_a, need_a, _rel_a = _setup_with_decision_criterion_need(
        admin_client, tmp_path, "System Verify IsoA"
    )
    _create_outcome(admin_client, headers_a, session_a, need_a)

    token_b, sys_b, snapshot_b = _setup(admin_client, tmp_path, "System Verify IsoB")
    headers_b = _headers(token_b, sys_b)
    session_b = _create_session(admin_client, headers_b, snapshot_b)

    listed_b = _verification(admin_client, headers_b, session_b)
    assert listed_b["outcome_criteria"] == []

    # A cross-System `need_id` (the id string reuses the same relation kind
    # but its underlying digest belongs to a DIFFERENT session/System) is
    # refused exactly like an unknown one -- resolving needs happens WITHIN
    # this System's own session, never against another System's chain.
    resp = _create_outcome(admin_client, headers_b, session_b, need_a, expect=422)
    assert resp.json()["detail"]["code"] == "purpose_verification_not_creatable"
