"""Issue #240: state/diagnostic/pipeline message catalog completeness and
regression tests.

Two guarantees:

1. **Completeness** — every key a producing module can emit resolves to a
   catalog entry, and the catalog never silently falls back to English/blank
   text (a missing key must raise a loud ``KeyError``). This is exercised
   both against the catalog's own finite key lists (``ALL_*``) and by driving
   the real ``run_system_diagnostics`` / ``build_system_state`` producers
   across representative scenarios and asserting every emitted id resolves.
2. **Regression** — representative messages keep their exact Japanese text,
   and no user-facing state copy leaks English (asserted structurally: the
   summary of every emitted item/check contains at least one non-ASCII
   character).
"""

import time

import pytest
from fastapi.testclient import TestClient

from app import state_messages as sm

# Reuse the battle-tested fixtures/helpers from the system-state suite so this
# file drives the real producers exactly as the API does.
from tests.test_system_state import (  # noqa: E402
    admin_client,
    _setup,
    _init_git_repo,
    _insert_intelligence_run,
    _insert_capability_node,
)


def _has_japanese(text: str) -> bool:
    """True if text contains at least one non-ASCII (CJK/kana) character.

    A cheap, deterministic "this is not English" guard: every user-facing
    state summary must carry Japanese, so an accidental English literal (all
    ASCII) is caught.
    """
    return any(ord(ch) > 0x2FFF for ch in text)


# ---------------------------------------------------------------------------
# Pure completeness: every catalog key resolves via its accessor with the
# required non-empty fields.
# ---------------------------------------------------------------------------


def test_every_fixed_state_id_resolves_with_summary():
    for state_id in sm.ALL_FIXED_STATE_IDS:
        msg = sm.state_message(state_id)
        assert msg.get("summary"), state_id
        assert _has_japanese(msg["summary"]), state_id


def test_every_pipeline_family_state_id_resolves_with_summary():
    for state_id in sm.ALL_PIPELINE_FAMILY_STATE_IDS:
        msg = sm.pipeline_family_message(state_id)
        assert msg.get("summary"), state_id
        assert _has_japanese(msg["summary"]), state_id


def test_every_understanding_message_resolves_with_summary():
    for state_id in sm.UNDERSTANDING_MESSAGES:
        msg = sm.understanding_message(state_id)
        assert msg.get("summary"), state_id
        assert _has_japanese(msg["summary"]), state_id


def test_every_check_id_has_a_japanese_title():
    for check_id in sm.ALL_CHECK_IDS:
        title = sm.check_title(check_id)
        assert title and _has_japanese(title), check_id


def test_every_stage_has_label_and_description():
    for stage in ("understand", "observe", "instrument", "evaluate"):
        msg = sm.stage_message(stage)
        assert _has_japanese(msg["label"]), stage
        assert _has_japanese(msg["description"]), stage


def test_every_phase_has_a_japanese_label():
    for phase in (
        "setup", "preparation", "instrumentation", "observation", "evaluation", "publish",
    ):
        assert _has_japanese(sm.phase_label(phase)), phase


def test_capability_hierarchy_empty_variants_resolve():
    for variant in ("_shared", "needs_review", "approved", "none"):
        assert sm.capability_hierarchy_empty_message(variant)


# ---------------------------------------------------------------------------
# Loud-failure contract: a missing key raises KeyError (never a silent
# fallback to English/blank text).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "accessor,arg",
    [
        (sm.state_message, "does.not.exist"),
        (sm.pipeline_family_message, "pipeline.nope.not_run"),
        (sm.understanding_message, "understanding.nope.satisfied"),
        (sm.check_title, "no_such_check"),
        (sm.stage_message, "no_such_stage"),
    ],
)
def test_missing_key_raises_keyerror(accessor, arg):
    with pytest.raises(KeyError):
        accessor(arg)


def test_phase_label_falls_back_to_token_for_validated_enum():
    # user_phase is a server-validated enum, so phase_label deliberately
    # returns the raw token (never English copy) for an unknown value rather
    # than raising -- matching the Dashboard's `?? p.phase` fallback.
    assert sm.phase_label("no_such_phase") == "no_such_phase"


def test_missing_check_message_variant_raises_keyerror():
    with pytest.raises(KeyError):
        sm.check_message("repository_roots", "no_such_variant")


# ---------------------------------------------------------------------------
# Functional completeness: drive the real producers and assert every emitted
# id resolved (no KeyError escaped) and no English leaked.
# ---------------------------------------------------------------------------


def _configure_reasoning_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-20250514")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_diagnostics_report_only_emits_cataloged_check_ids(admin_client):
    """Every check_id run_system_diagnostics emits is in the catalog, and its
    title/detail resolved to Japanese (no English fallback)."""
    from app.system_diagnostics import run_system_diagnostics

    _, sys, _ = _setup(admin_client)
    report = run_system_diagnostics(sys["id"])
    assert report.checks
    for check in report.checks:
        assert check.check_id in sm.ALL_CHECK_IDS, check.check_id
        assert check.title and _has_japanese(check.title), check.check_id
        # detail is present for every non-ok check; ok checks may omit it.
        if check.severity != "ok" and check.detail:
            assert _has_japanese(check.detail), check.check_id


def test_build_system_state_items_are_all_japanese(admin_client, tmp_path, monkeypatch):
    """Drive build_system_state through a rich scenario (repo configured,
    pipeline complete, purpose+capabilities present) and assert every emitted
    StateItem summary is non-empty Japanese -- catching a missing catalog key
    (which would KeyError) or an English leak (all-ASCII summary)."""
    _configure_reasoning_llm(monkeypatch)
    from app.system_state import build_system_state

    _, sys, hdrs = _setup(admin_client)
    repo, sha = _init_git_repo(tmp_path)
    admin_client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
        headers=hdrs,
    )
    snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
    snapshot_id = snap.json()["id"]
    _insert_intelligence_run(sys["id"], snapshot_id, "symbol_index", "completed")
    _insert_intelligence_run(sys["id"], snapshot_id, "entrypoint_index", "completed")
    run_id = _insert_intelligence_run(sys["id"], snapshot_id, "capability_hierarchy", "completed")
    _insert_capability_node(sys["id"], snapshot_id, run_id, node_type="purpose", name="Test system")
    _insert_capability_node(sys["id"], snapshot_id, run_id, node_type="capability", name="Item management")

    assessment = build_system_state(sys["id"])
    assert assessment.items
    for item in assessment.items:
        assert item.summary and _has_japanese(item.summary), item.state_id


# ---------------------------------------------------------------------------
# Snapshot regression: a few representative exact strings.
# ---------------------------------------------------------------------------


def test_representative_message_snapshots():
    assert (
        sm.state_message("snapshot.ready.missing")["summary"]
        == sm.STATE_MESSAGES["snapshot.ready.missing"]["summary"]
    )
    # A formerly-English pipeline detail is now Japanese.
    assert sm.pipeline_step_detail("documentation_indexed.no_chunks") == "ドキュメントチャンクが見つかりませんでした。"
    assert sm.pipeline_step_detail("documentation_claims_scanned.blocked") == (
        "reasoning モデルが設定されていないため実行できません。"
    )
    # Stage labels are Japanese.
    assert sm.stage_message("understand")["label"] == "理解する"
    # Success summary interpolates finite facts only.
    assert sm.success_summary(done=8, total=8, symbol_count=12, entrypoint_count=3) == (
        "分析完了 — 8/8 ステップ ・ 12 シンボル ・ 3 エントリポイント"
    )


def test_gap_title_and_note_are_japanese():
    title = sm.gap_title("unclassified_entrypoint", "handler.foo")
    assert "handler.foo" in title and _has_japanese(title)
    note = sm.gap_note("unclassified_entrypoint", entrypoint_type="http", entrypoint_id="e1")
    assert "http" in note and "e1" in note and _has_japanese(note)
