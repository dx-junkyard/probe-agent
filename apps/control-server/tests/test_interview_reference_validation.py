"""IK-0039: interview proposals' symbol / feature references are checked by the system."""

import json

from test_interview import (  # noqa: F401  (fixture + helpers)
    _confirm_and_reach_proposal_generation,
    _headers,
    _insert_symbol,
    _setup,
    _stub_proposal_result,
    _stub_reasoning_turn,
    admin_client,
)
from test_interview_agent import (
    FakeLLMClient,
    _context_pack,
    _make_config,
    _valid_proposal,
    _valid_response,
)

from app.interview_agent import (
    PROMPT_VERSION,
    _SYSTEM_PROMPT,
    generate_interview_turn,
    validate_proposal_refs,
)


class _Capturing(FakeLLMClient):
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.messages = messages
        return super().generate_text(messages, temperature=temperature, max_tokens=max_tokens)


def _turn(proposals, **kwargs):
    client = _Capturing(response=_valid_response(proposals=proposals))
    result = generate_interview_turn(
        client, _make_config(),
        context_pack=_context_pack(), history=[], user_message="propose", **kwargs,
    )
    assert result.error is None, result.error
    return client, result


def test_prompt_no_longer_asks_for_symbol_ids():
    assert '"symbol_id"' not in _SYSTEM_PROMPT
    assert "never write a symbol id" in _SYSTEM_PROMPT
    assert PROMPT_VERSION == "interview-v9"
    client, _ = _turn([_valid_proposal()], known_features=[("user-management", "Users")])
    user_prompt = client.messages[1]["content"]
    assert '"symbol_id"' not in user_prompt
    assert "## Known features" in user_prompt
    assert "- feature_id: user-management | name: Users" in user_prompt


def test_symbol_id_is_rederived_and_unknown_symbol_dropped():
    wrong_id = _valid_proposal(symbol_id=999)
    unknown = _valid_proposal(path="src/ghost.py", qualified_name="ghost.run", symbol_id=1)
    _, result = _turn([wrong_id, unknown], known_features=[("summarization", "Summaries")])
    assert [(p.path, p.symbol_id) for p in result.proposals] == [("src/summarize.py", 1)]
    assert result.proposals[0].probe_plan.feature_id == "summarization"
    kinds = [(d["kind"], d.get("reason")) for d in result.proposal_diagnostics]
    assert kinds == [
        ("symbol_id_corrected", None),
        ("proposal_dropped", "symbol_not_in_candidates"),
    ]
    assert result.proposal_diagnostics[0]["from"] == 999
    assert result.proposal_diagnostics[0]["to"] == 1


def test_feature_id_must_be_a_known_key():
    from app.interview_agent import InterviewProposalResult
    from app.models import InterviewProposalMetadataBlock, InterviewProposalProbePlan

    def proposal(feature_id, path="src/a.py"):
        return InterviewProposalResult(
            path=path, qualified_name="a.f", symbol_id=None,
            metadata=InterviewProposalMetadataBlock(),
            probe_plan=InterviewProposalProbePlan(feature_id=feature_id),
        )

    candidates = {("src/a.py", "a.f"): 7}
    kept, diagnostics = validate_proposal_refs(
        [proposal("User-Management"), proposal("made-up"), proposal("", path="./src/a.py")],
        candidates,
        ["user-management"],
    )
    assert [p.probe_plan.feature_id for p in kept] == ["user-management", "", ""]
    assert [p.symbol_id for p in kept] == [7, 7, 7]
    assert [p.path for p in kept] == ["src/a.py"] * 3
    assert diagnostics == [{
        "kind": "feature_id_cleared", "reason": "feature_not_in_candidates",
        "path": "src/a.py", "qualified_name": "a.f", "feature_id": "made-up",
    }]


def test_route_passes_candidates_from_snapshot_and_records_diagnostics(
    admin_client, monkeypatch
):
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client)
    symbol_id = _insert_symbol(system_id, snapshot_id, "src/app.py", "app.handle", "h1")
    headers = _headers(token, system_id)
    sid = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "ids"},
        headers=headers,
    ).json()["id"]
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    captured = {}
    _stub_reasoning_turn(monkeypatch, proposals=[_stub_proposal_result()], captured=captured)
    from app.interview_agent import InterviewTurnResult
    from app.routes import interview as interview_routes

    original = interview_routes.generate_interview_turn

    def with_diagnostics(*args, **kwargs):
        result: InterviewTurnResult = original(*args, **kwargs)
        result.proposal_diagnostics = [{"kind": "proposal_dropped", "reason": "symbol_not_in_candidates"}]
        return result

    monkeypatch.setattr(interview_routes, "generate_interview_turn", with_diagnostics)
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={"user_message": "提案を生成してください", "generate_proposals": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposal_diagnostics"] == [
        {"kind": "proposal_dropped", "reason": "symbol_not_in_candidates"}
    ]
    assert captured["symbol_candidates"][("src/app.py", "app.handle")] == symbol_id
    assert captured["known_features"] == []
    with get_conn() as conn:
        row = conn.execute(
            "SELECT diagnostics_json FROM intelligence_runs WHERE id = ?",
            (body["intelligence_run"]["id"],),
        ).fetchone()
    assert json.loads(row["diagnostics_json"])["proposal_diagnostics"][0]["kind"] == "proposal_dropped"
