"""Issue #471 (Epic #467): grounded, structured assistant answers.

Contract: docs/01-specifications/capabilities/assistant-answer-quality.md §4.

STRUCTURAL LIMIT (contract §0 / §4.2): validation here only proves that a cited
id is inside the context pack.  It cannot tell that an existing id is
irrelevant to the claim or that two cited claims contradict each other; those
are judged by the fixed evaluation tasks and human review, not by these tests.
"""
from __future__ import annotations

import json

import pytest

from app.assistant import (
    ContextPack,
    _RawAssistantResponse,
    get_screen_context,
    ground_response,
)
from app.db import get_conn
from app.llm import LLMError
from tests.test_assistant_context_selection import (  # noqa: F401  (fixtures + helpers)
    _create_system,
    _feature_thread,
    _headers,
    _login,
    _seed_feature_with_requirement,
    admin_client,
)

SCREEN = "system-understanding"
QUESTION = "What should INTELLIGENCE_LLM_MODEL be set to?"
VALID = {"type": "setting", "id": "INTELLIGENCE_LLM_MODEL"}
OTHER_VALID = {"type": "setting", "id": "INTELLIGENCE_LLM_PROVIDER"}
FACT_HEADING = "確認済みの事実:"
UNSUPPORTED_HEADING = "根拠を確認できなかった主張 (事実として扱わないでください):"
STALE_HEADING = "古い根拠に基づく内容 (再確認が必要):"
INTERP_HEADING = "解釈・提案:"
MISSING_HEADING = "不足している情報:"


class _Fake:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.last_usage = None
        self.last_finish_reason = None

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.calls.append(messages)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)


def _v2(conclusion="結論です。", points=(), missing=(), citations=()):
    return {
        "conclusion": conclusion,
        "points": list(points),
        "missing_information": list(missing),
        "suggested_actions": [],
        "citations": list(citations),
    }


def _fact(text, *sources):
    return {"kind": "fact", "text": text, "sources": list(sources)}


def _enable(monkeypatch, fake):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr("app.routes.assistant.create_llm_client", lambda config: fake)


@pytest.fixture
def env(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "grounded-sys")
    return admin_client, token, system_id, _headers(token, system_id)


def _ask(client, headers, question=QUESTION, screen_id=SCREEN, expect=200, **extra):
    r = client.post(
        "/assistant/ask", json={"screen_id": screen_id, "question": question, **extra}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json()


def _section(answer: str, heading: str) -> str:
    """The text of one heading's section ('' when absent)."""
    blocks = answer.split("\n\n")
    for block in blocks:
        if block.startswith(heading):
            return block
    return ""


# --- §4.2 deterministic validation ------------------------------------------


def test_supported_fact_is_grounded_and_conclusion_comes_first(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(_v2("モデル名は設定で決まります。", [_fact("この設定は必須です", VALID)])))
    body = _ask(client, headers)
    assert body["prompt_version"] == "v2" and body["schema_version"] == "v2"
    assert body["answer_status"] == "answered" and body["failure"] is None
    assert body["conclusion"] == "モデル名は設定で決まります。"
    assert body["answer"].startswith("モデル名は設定で決まります。")
    assert body["points"][0]["grounding"] == "supported"
    assert body["grounding_state"] == "grounded"
    assert body["grounding_counts"] == {
        "supported": 1, "stale": 0, "unsupported": 0, "not_required": 0, "rejected_citations": 0,
    }
    assert "この設定は必須です" in _section(body["answer"], FACT_HEADING)
    assert [c["id"] for c in body["citations"]] == ["INTELLIGENCE_LLM_MODEL"]


def test_fact_with_invalid_citation_is_unsupported_and_not_shown_as_fact(env, monkeypatch):
    client, _, _, headers = env
    bogus = {"type": "screen_data", "id": "scripted:does-not-exist"}
    _enable(monkeypatch, _Fake(_v2(points=[_fact("存在しない根拠の主張", bogus)])))
    body = _ask(client, headers)
    assert body["points"][0]["grounding"] == "unsupported"
    assert body["points"][0]["sources"] == []
    assert body["grounding_state"] == "ungrounded"
    assert body["grounding_counts"]["rejected_citations"] == 1
    assert "存在しない根拠の主張" not in _section(body["answer"], FACT_HEADING)
    assert "存在しない根拠の主張" in _section(body["answer"], UNSUPPORTED_HEADING)
    # The invalid id is neither kept in the structure nor written to citations.
    assert body["citations"] == []
    assert "scripted:does-not-exist" not in json.dumps(body["points"])


def test_fact_without_sources_is_unsupported(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(_v2(points=[_fact("引用のない断定")])))
    body = _ask(client, headers)
    assert body["points"][0]["grounding"] == "unsupported"
    assert body["grounding_counts"]["rejected_citations"] == 0
    assert "引用のない断定" in _section(body["answer"], UNSUPPORTED_HEADING)
    assert FACT_HEADING not in body["answer"]


def test_existing_but_unrelated_id_is_supported_structural_limit(env, monkeypatch):
    """KNOWN LIMIT (contract §4.2): an id that exists in the pack but says
    nothing about the claim passes structural validation.  Only human
    evaluation can catch it -- do not 'fix' this by asserting `unsupported`."""
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(_v2(points=[_fact("まったく無関係な主張", OTHER_VALID)])))
    body = _ask(client, headers)
    assert body["points"][0]["grounding"] == "supported"
    assert body["grounding_state"] == "grounded"


def test_conflicting_points_with_valid_ids_are_both_kept_structural_limit(env, monkeypatch):
    """KNOWN LIMIT: contradiction between two validly cited facts is not
    detectable structurally; both stay `supported`."""
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(_v2(points=[
        _fact("この設定は必須です", VALID), _fact("この設定は不要です", VALID),
    ])))
    body = _ask(client, headers)
    assert [p["grounding"] for p in body["points"]] == ["supported", "supported"]
    section = _section(body["answer"], FACT_HEADING)
    assert "必須です" in section and "不要です" in section


def test_grounding_state_covers_all_four_values(env, monkeypatch):
    client, _, _, headers = env
    bogus = {"type": "setting", "id": "NOT_A_SETTING"}
    cases = [
        ([_fact("a", VALID)], "grounded"),
        ([_fact("a", VALID), _fact("b", bogus)], "partially_grounded"),
        ([_fact("b", bogus)], "ungrounded"),
        ([], "not_required"),
        ([{"kind": "interpretation", "text": "解釈", "sources": []}], "not_required"),
    ]
    for points, expected in cases:
        _enable(monkeypatch, _Fake(_v2(points=points)))
        assert _ask(client, headers)["grounding_state"] == expected


def test_interpretation_without_sources_is_not_required_and_unknown_goes_to_missing(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(_v2(
        points=[
            {"kind": "interpretation", "text": "おそらく設定漏れです", "sources": []},
            {"kind": "unknown", "text": "過去の実行履歴は分かりません", "sources": []},
        ],
        missing=[{"what": "直近の実行ログ", "how_to_get": "Runs 画面から取得してください"}],
    )))
    body = _ask(client, headers)
    assert [p["grounding"] for p in body["points"]] == ["not_required", "not_required"]
    assert "おそらく設定漏れです" in _section(body["answer"], INTERP_HEADING)
    missing = _section(body["answer"], MISSING_HEADING)
    assert "過去の実行履歴は分かりません" in missing
    assert "直近の実行ログ — Runs 画面から取得してください" in missing
    assert body["missing_information"] == [
        {"what": "直近の実行ログ", "how_to_get": "Runs 画面から取得してください"}
    ]
    # Missing information is not a failure.
    assert body["answer_status"] == "answered"


def test_operation_help_with_empty_points_is_not_required(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(_v2("Build / Refresh を押してください。")))
    body = _ask(client, headers, question="どうやって再実行しますか?")
    assert body["points"] == [] and body["grounding_state"] == "not_required"
    assert body["answer"] == "Build / Refresh を押してください。"


def test_empty_sections_are_omitted(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(_v2(points=[_fact("a", VALID)])))
    answer = _ask(client, headers)["answer"]
    for heading in (INTERP_HEADING, STALE_HEADING, UNSUPPORTED_HEADING, MISSING_HEADING):
        assert heading not in answer


def test_v1_shaped_output_is_malformed_not_silently_accepted(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake({"answer": "旧形式の回答", "suggested_actions": [], "citations": []}))
    body = _ask(client, headers, question="設計について相談したい")
    assert body["answer_status"] == "failed"
    assert body["failure"]["failure_class"] == "malformed_output"
    assert "旧形式の回答" not in body["answer"]


# --- stale sources ------------------------------------------------------------


def _pack(**kw):
    ctx = get_screen_context("overview")
    return ContextPack(
        screen=ctx, settings=[], checks=[], pipeline_steps=[],
        mentioned_setting_keys=[], mentioned_check_ids=[], **kw,
    )


def _parsed(points):
    return _RawAssistantResponse.model_validate(_v2(points=points))


def test_stale_related_context_source_makes_the_fact_stale():
    pack = _pack(related_context=[
        {"source_id": "ux_requirement:r1", "freshness": "stale", "title": "R1"},
        {"source_id": "ux_requirement:r2", "freshness": "current", "title": "R2"},
    ])
    out = ground_response(pack, _parsed([
        _fact("古い根拠", {"type": "related_context", "id": "ux_requirement:r1"}),
        _fact("新しい根拠", {"type": "related_context", "id": "ux_requirement:r2"}),
        _fact("混在", {"type": "related_context", "id": "ux_requirement:r1"},
              {"type": "related_context", "id": "ux_requirement:r2"}),
    ]))
    assert [p["grounding"] for p in out["points"]] == ["stale", "supported", "supported"]
    assert out["grounding_state"] == "partially_grounded"
    assert "古い根拠" in _section(out["answer"], STALE_HEADING)
    assert "古い根拠" not in _section(out["answer"], FACT_HEADING)


def test_stale_thread_target_source_makes_the_fact_stale(env, monkeypatch):
    client, _, _, headers = env
    fkey, _ = _seed_feature_with_requirement(client, headers)
    thread_id = _feature_thread(client, headers, fkey)
    r = client.post(
        f"/product-features/{fkey}/revisions",
        json={"title": "変わった", "statement": "", "rationale": "", "scope_note": "", "summary": "",
              "change_note": ""},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    target = {"type": "screen_data", "id": f"discussion_target:product_feature:{fkey}"}
    _enable(monkeypatch, _Fake(_v2(points=[_fact("対象についての主張", target)])))
    body = _ask(client, headers, screen_id="ux-design-studio", thread_id=thread_id)
    assert body["target_state"] == "stale"
    assert body["points"][0]["grounding"] == "stale"
    assert "対象についての主張" in _section(body["answer"], STALE_HEADING)


# --- persistence --------------------------------------------------------------


def _turn_rows(thread_id):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM assistant_discussion_turn WHERE thread_id = ? ORDER BY turn_number", (thread_id,)
        ).fetchall()]


def _thread(client, headers, jkey="j1"):
    r = client.post("/ux-design/journeys",
                    json={"journey_key": jkey, "perspective": "to_be", "baseline_mode": "undecided"},
                    headers=headers)
    assert r.status_code == 201, r.text
    r = client.post(
        "/assistant/discussion-threads",
        json={"scope": "entity", "screen_id": "ux-design-studio",
              "target_kind": "ux_journey", "target_ref": jkey},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["thread"]["id"]


def test_answer_structure_is_persisted_and_listed_on_the_thread(env, monkeypatch):
    client, _, _, headers = env
    thread_id = _thread(client, headers)
    _enable(monkeypatch, _Fake(_v2("結論A", points=[_fact("事実A", {"type": "screen_data", "id": "bogus"})])))
    _ask(client, headers, screen_id="ux-design-studio", thread_id=thread_id, question="この Journey は?")
    rows = _turn_rows(thread_id)
    assert rows[0]["answer_structure_json"] is None  # user turn
    stored = json.loads(rows[1]["answer_structure_json"])
    assert stored["conclusion"] == "結論A" and stored["answer_status"] == "answered"
    assert stored["points"][0]["grounding"] == "unsupported"
    assert stored["grounding_state"] == "ungrounded" and stored["failure_class"] is None
    detail = client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers).json()
    turns = detail["turns"]
    assert turns[0]["answer_structure"] is None
    assert turns[1]["answer_structure"]["conclusion"] == "結論A"
    assert turns[1]["answer_structure"]["points"][0]["grounding"] == "unsupported"


def test_draft_turn_persists_null_structure_and_neutral_body(env, monkeypatch):
    client, _, _, headers = env
    thread_id = _thread(client, headers, "j2")
    _enable(monkeypatch, _Fake(_v2("下書きに基づく結論", points=[_fact("DRAFTVALUE-471", VALID)])))
    draft = {
        "target_kind": "ux_journey", "target_ref": "j2", "form_id": "ux_journey.revision",
        "fields": [{"field_name": "title", "value": "DRAFTVALUE-471", "dirty": True, "validation_error": ""}],
        "selected_item_ref": "", "active_tab": "", "comparison_target": "",
        "captured_at": 1_700_000_000.0, "local_revision_token": "token-1",
    }
    body = _ask(client, headers, screen_id="ux-design-studio", thread_id=thread_id,
                question="下書きを見て", ui_draft=draft)
    assert body["ui_draft_state"] == "applied"
    assert body["conclusion"] == "下書きに基づく結論"  # live response still structured
    rows = _turn_rows(thread_id)
    assert rows[1]["answer_structure_json"] is None
    assert "DRAFTVALUE-471" not in rows[1]["content"]


def test_null_structure_rows_still_list(env):
    client, _, system_id, headers = env
    thread_id = _thread(client, headers, "j3")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO assistant_discussion_turn (system_id, thread_id, turn_number, role, content, "
            "decision_method, created_at) VALUES (?, ?, 1, 'assistant', '旧い回答', 'deterministic', 1.0)",
            (system_id, thread_id),
        )
    detail = client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers).json()
    assert detail["turns"][0]["content"] == "旧い回答"
    assert detail["turns"][0]["answer_structure"] is None


def test_voice_spoken_answer_starts_with_the_conclusion(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(_v2(
        "要点は、設定が必要なことです。",
        points=[_fact("詳細な事実その一", VALID), _fact("詳細な事実その二", VALID)],
    )))
    body = _ask(client, headers, input_mode="voice")
    assert body["spoken_answer"].startswith("要点は、設定が必要なことです。")


def test_other_system_cannot_read_the_thread_structure(env, monkeypatch):
    client, token, _, headers = env
    thread_id = _thread(client, headers, "j4")
    _enable(monkeypatch, _Fake(_v2("結論")))
    _ask(client, headers, screen_id="ux-design-studio", thread_id=thread_id, question="?")
    other = _create_system(client, token, "grounded-other")
    r = client.get(f"/assistant/discussion-threads/{thread_id}", headers=_headers(token, other))
    assert r.status_code == 404


def test_proposal_generation_still_reads_turn_content(env, monkeypatch):
    """The stored answer `content` is the composed `answer`, which the
    proposal generator keeps reading (contract §4.4)."""
    client, _, _, headers = env
    thread_id = _thread(client, headers, "j5")
    _enable(monkeypatch, _Fake(_v2("PROPOSALBASIS-471")))
    _ask(client, headers, screen_id="ux-design-studio", thread_id=thread_id, question="?")
    rows = _turn_rows(thread_id)
    assert rows[1]["content"] == "PROPOSALBASIS-471"


def test_llm_error_type_is_not_confused_with_answer():
    # sanity: LLMError import used by sibling failure tests stays valid.
    assert LLMError("x", kind="timeout").kind == "timeout"
