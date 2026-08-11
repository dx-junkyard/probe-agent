"""Issue #339: exploration breadth, the router-aware question gate, hard budgets.

Epic #328's loop retrieved candidates from four sources — file paths, the symbol
index, the entrypoint index, and a content scan — and all four answer the same
question: "which file MENTIONS this?". Three things a developer routinely needs
were therefore unreachable: who depends on this, who calls this, and when it
changed. Two further contracts were missing outright: `failed` lumped a research
LIMITATION (the system looked and could not tell) together with an EXECUTION
FAILURE (the system could not look), and the time budget was only checked
between rounds — never applied to the call that actually spends the time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sqlite3
import time
from typing import List, Optional

import pytest

from app.investigation_loop import (
    EXECUTION_FAILURE_CLASSES,
    OUTCOME_CLASSES,
    RESEARCH_LIMITATIONS,
    STOP_REASONS,
    outcome_class_for,
)
from app.snapshot_explorers import (
    EXPLORATION_SOURCE_KINDS,
    ExplorerResult,
    call_graph_candidates,
    dependency_candidates,
    git_history_candidates,
    merge_candidates,
    runtime_discovery_candidates,
)
from app.understanding_translator import TranslatedStatement, ask_developer


# --- The limitation / failure split --------------------------------------------


def test_every_stop_reason_maps_to_exactly_one_outcome_class():
    assert set(RESEARCH_LIMITATIONS) | {"answered", "failed"} == set(STOP_REASONS)
    for stop_reason in STOP_REASONS:
        assert outcome_class_for(stop_reason) in OUTCOME_CLASSES


def test_a_research_limitation_is_never_an_execution_failure():
    """They mean opposite things and have opposite consequences: a limitation is
    a real evidence-backed result, a failure is the ABSENCE of one."""
    for stop_reason in RESEARCH_LIMITATIONS:
        assert outcome_class_for(stop_reason) == "research_limitation"
    assert outcome_class_for("failed") == "execution_failure"
    assert outcome_class_for("answered") == "answered"
    # The failure classes name WHERE it broke, because the recovery differs.
    assert set(EXECUTION_FAILURE_CLASSES) == {
        "config_invalid", "snapshot_unavailable", "api_failure",
        "schema_invalid", "timeout",
    }


# --- The router-aware question gate --------------------------------------------


def _decision_statement() -> TranslatedStatement:
    return TranslatedStatement(
        layer="decision", claim_kind="inference",
        text="3回で打ち切るか5回まで待つかは運用判断です", supports_finding_ids=[1],
    )


def _purpose_statement() -> TranslatedStatement:
    return TranslatedStatement(
        layer="purpose", claim_kind="inference",
        text="失敗を早く見せるための仕組みです", supports_finding_ids=[1],
    )


def test_a_system_researchable_question_is_never_handed_to_the_developer():
    """By the router's own classification the answer is in the code, so an
    unanswered question means the investigation is not finished. Asking would
    be asking the developer to do the reading."""
    assert not ask_developer(
        statements=[_decision_statement()],
        decision_question="どちらにしますか",
        open_unknowns=[],
        route_category="system_researchable",
        research_exhausted=True,
    )


def test_a_question_is_held_back_while_research_could_still_answer_it():
    """The `hybrid` contract: consume the researchable part first, then ask only
    what is left."""
    assert not ask_developer(
        statements=[_purpose_statement()],
        decision_question="どちらにしますか",
        open_unknowns=[],
        route_category="hybrid",
        research_exhausted=False,
    )
    # Once there IS decision material, the remaining question is genuinely the
    # developer's even if more reading is possible.
    assert ask_developer(
        statements=[_purpose_statement(), _decision_statement()],
        decision_question="どちらにしますか",
        open_unknowns=[],
        route_category="hybrid",
        research_exhausted=False,
    )


def test_human_only_still_requires_decision_material():
    """The classification says the developer must decide. It does not say they
    can decide with nothing -- asking anyway is the empty hand-back Epic #328
    removed."""
    assert not ask_developer(
        statements=[],
        decision_question="優先度はどちらですか",
        open_unknowns=["どのコンポーネントが影響するか不明"],
        route_category="human_only",
        research_exhausted=True,
    )
    assert ask_developer(
        statements=[_decision_statement()],
        decision_question="優先度はどちらですか",
        open_unknowns=[],
        route_category="human_only",
        research_exhausted=True,
    )


def test_no_decision_question_never_asks():
    assert not ask_developer(
        statements=[_decision_statement()],
        decision_question=None,
        open_unknowns=[],
        route_category="hybrid",
        research_exhausted=True,
    )


# --- The structural sources ----------------------------------------------------


@pytest.fixture
def indexed_snapshot(tmp_path, monkeypatch):
    """A minimal pinned snapshot with a symbol index and file content."""
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-explorers-test.db"))
    from app.db import get_conn, init_db

    init_db()
    now = time.time()
    with get_conn() as conn:
        system_id = conn.execute(
            "INSERT INTO systems (name, environment, created_at, updated_at) "
            "VALUES ('S', 'test', ?, ?)",
            (now, now),
        ).lastrowid
        snapshot_id = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
            (system_id, now, now),
        ).lastrowid

        files = {
            "app/retry.py": "MAX_RETRIES = 3\n\ndef should_retry(attempt):\n    return attempt < MAX_RETRIES\n",
            "app/caller.py": "from app.retry import should_retry\n\ndef handle():\n    return should_retry(1)\n",
            "app/unrelated.py": "def other():\n    return 1\n",
            "docs/retry.md": "retry is mentioned here but this is not a dependency\n",
        }
        for path, text in files.items():
            conn.execute(
                """INSERT INTO snapshot_files
                    (snapshot_id, path, source_type, size_bytes, content, inclusion_status)
                   VALUES (?, ?, 'source', ?, ?, 'indexed')""",
                (snapshot_id, path, len(text), text.encode("utf-8")),
            )
        symbols = [
            ("app/retry.py", "app.retry", "module", 1, 4, json.dumps([]), None),
            ("app/retry.py", "app.retry.should_retry", "function", 3, 4, json.dumps([]), "retry_component"),
            ("app/caller.py", "app.caller", "module", 1, 4, json.dumps(["app.retry:should_retry"]), None),
            ("app/caller.py", "app.caller.handle", "function", 3, 4, json.dumps([]), None),
            ("app/unrelated.py", "app.unrelated", "module", 1, 2, json.dumps([]), None),
        ]
        for path, qname, kind, start, end, imports, component_id in symbols:
            conn.execute(
                """INSERT INTO code_symbols
                    (snapshot_id, system_id, path, qualified_name, kind,
                     start_line, end_line, imports, component_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, system_id, path, qname, kind, start, end, imports, component_id),
            )
    return system_id, snapshot_id


def test_dependency_source_finds_the_importer_not_the_mention(indexed_snapshot):
    """The distinction a content scan cannot make: `docs/retry.md` mentions
    retry, `app/caller.py` depends on it."""
    system_id, snapshot_id = indexed_snapshot
    from app.db import get_conn

    with get_conn() as conn:
        result = dependency_candidates(
            conn, snapshot_id=snapshot_id, system_id=system_id,
            seed_paths=["app/retry.py"],
        )
    assert result.ok
    assert result.source_kind == "dependency"
    assert result.revision == str(snapshot_id)
    assert "app/caller.py" in result.candidates
    assert "docs/retry.md" not in result.candidates
    # The seed itself is never proposed back.
    assert "app/retry.py" not in result.candidates
    assert result.queries_run >= 1


def test_call_graph_source_finds_the_call_site(indexed_snapshot):
    system_id, snapshot_id = indexed_snapshot
    from app.db import get_conn

    with get_conn() as conn:
        result = call_graph_candidates(
            conn, snapshot_id=snapshot_id, system_id=system_id,
            seed_paths=["app/retry.py"],
        )
    assert result.ok
    assert "app/caller.py" in result.candidates
    assert "app/unrelated.py" not in result.candidates


def test_runtime_facts_discover_candidates_rather_than_only_annotating(
    indexed_snapshot,
):
    """Epic #328 used runtime facts only to annotate files it had ALREADY
    chosen, so a component failing in production could not bring its own source
    into the candidate set."""
    system_id, snapshot_id = indexed_snapshot
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        for index, error in enumerate((None, "ValueError: boom")):
            conn.execute(
                """INSERT INTO traces
                    (system_id, trace_id, component_id, mode, duration_ms,
                     error, timestamp)
                   VALUES (?, ?, 'retry_component', 'trace', 1, ?, ?)""",
                (system_id, f"t-{index}", error, now),
            )
        result = runtime_discovery_candidates(
            conn, snapshot_id=snapshot_id, system_id=system_id,
        )
    assert result.ok
    assert result.source_kind == "runtime_facts"
    assert result.candidates == ["app/retry.py"]


def test_runtime_discovery_orders_by_traces_not_by_symbol_count(indexed_snapshot):
    """One path can define several symbols sharing a component_id, so the join
    yields one row per (trace, symbol) pair. Counting those would weight a path
    by how many symbols it contains -- a large file jumping ahead of one that is
    actually failing more often, while still looking like a count of calls."""
    system_id, snapshot_id = indexed_snapshot
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        # `app/retry.py` already defines TWO symbols; give the second one the
        # same component_id so the join multiplies its rows.
        conn.execute(
            "UPDATE code_symbols SET component_id = 'retry_component' "
            "WHERE snapshot_id = ? AND path = 'app/retry.py'",
            (snapshot_id,),
        )
        conn.execute(
            "UPDATE code_symbols SET component_id = 'caller_component' "
            "WHERE snapshot_id = ? AND path = 'app/caller.py' AND kind = 'module'",
            (snapshot_id,),
        )
        # retry: one error. caller: two errors -- it must come first.
        conn.execute(
            """INSERT INTO traces
                (system_id, trace_id, component_id, mode, duration_ms, error, timestamp)
               VALUES (?, 'r-1', 'retry_component', 'trace', 1, 'boom', ?)""",
            (system_id, now),
        )
        for index in range(2):
            conn.execute(
                """INSERT INTO traces
                    (system_id, trace_id, component_id, mode, duration_ms, error,
                     timestamp)
                   VALUES (?, ?, 'caller_component', 'trace', 1, 'boom', ?)""",
                (system_id, f"c-{index}", now),
            )
        result = runtime_discovery_candidates(
            conn, snapshot_id=snapshot_id, system_id=system_id,
        )
    assert result.candidates[:2] == ["app/caller.py", "app/retry.py"]


def test_a_failing_source_is_skipped_not_escalated(indexed_snapshot):
    """A broken source must not fail the round, and must not be replaced by an
    unbounded fallback search."""
    failed = ExplorerResult(
        source_kind="git_history", revision="abc123", error="git log failed",
    )
    good = ExplorerResult(
        source_kind="dependency", revision="1", candidates=["app/caller.py"],
    )
    merged = merge_candidates(
        [failed, good],
        tracked_paths={"app/caller.py"}, already_read=set(), limit=5,
    )
    assert merged == ["app/caller.py"]


def test_merge_interleaves_so_one_source_cannot_starve_the_others():
    """Concatenation would let a single prolific source consume the whole read
    budget, and the sources added for breadth would contribute nothing."""
    prolific = ExplorerResult(
        source_kind="file_content", revision="1",
        candidates=[f"a{i}.py" for i in range(10)],
    )
    narrow = ExplorerResult(
        source_kind="git_history", revision="abc123", candidates=["history.py"],
    )
    tracked = {f"a{i}.py" for i in range(10)} | {"history.py"}
    merged = merge_candidates(
        [prolific, narrow], tracked_paths=tracked, already_read=set(), limit=3,
    )
    assert "history.py" in merged


def test_merge_drops_paths_outside_the_pinned_snapshot():
    """A candidate that is not in the pinned commit's tree cannot be read from
    it, so it is dropped here rather than failing later."""
    result = ExplorerResult(
        source_kind="dependency", revision="1",
        candidates=["app/inside.py", "app/deleted-since.py"],
    )
    merged = merge_candidates(
        [result], tracked_paths={"app/inside.py"}, already_read=set(), limit=5,
    )
    assert merged == ["app/inside.py"]


def test_already_read_paths_are_never_reproposed():
    result = ExplorerResult(
        source_kind="call_graph", revision="1", candidates=["a.py", "b.py"],
    )
    merged = merge_candidates(
        [result], tracked_paths={"a.py", "b.py"}, already_read={"a.py"}, limit=5,
    )
    assert merged == ["b.py"]


def test_source_kinds_are_a_closed_set():
    assert EXPLORATION_SOURCE_KINDS == (
        "path_name", "symbol_index", "entrypoint_index", "file_content",
        "dependency", "call_graph", "git_history", "runtime_facts",
    )


# --- The git history source's read-only, pinned boundary -----------------------


def _git(repo: str, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    result = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, env=env, check=True,
    )
    return result.stdout.decode()


@pytest.fixture
def history_repo(tmp_path):
    """Two commits, plus a third made AFTER the snapshot would be pinned."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    (tmp_path / "repo" / "retry.py").write_text("MAX = 3\n", encoding="utf-8")
    (tmp_path / "repo" / "caller.py").write_text("import retry\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    (tmp_path / "repo" / "retry.py").write_text("MAX = 5\n", encoding="utf-8")
    (tmp_path / "repo" / "config.py").write_text("RETRIES = 5\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second: change retry with config")
    pinned = _git(repo, "rev-parse", "HEAD").strip()
    # A commit that exists in the repository but is NOT an ancestor of `pinned`.
    (tmp_path / "repo" / "future.py").write_text("LATER = 1\n", encoding="utf-8")
    (tmp_path / "repo" / "retry.py").write_text("MAX = 9\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "third: after the snapshot")
    return repo, pinned


def test_git_history_finds_co_changed_files_in_the_pinned_history(history_repo):
    repo, pinned = history_repo
    result = git_history_candidates(
        repo_path=repo, commit_sha=pinned, seed_paths=["retry.py"],
    )
    assert result.ok, result.error
    assert result.source_kind == "git_history"
    # The revision is recorded per source, so the boundary is checkable.
    assert result.revision == pinned
    assert "config.py" in result.candidates
    assert "retry.py" not in result.candidates


def test_git_history_can_never_see_past_the_pinned_commit(history_repo):
    """The pinned commit is the log tip, so a commit made after the snapshot was
    pinned is unreachable -- and the working tree is never consulted. That is
    what keeps this source inside Principle 5's boundary."""
    repo, pinned = history_repo
    # An uncommitted working-tree change must not appear either.
    with open(os.path.join(repo, "dirty.py"), "w", encoding="utf-8") as handle:
        handle.write("DIRTY = 1\n")

    result = git_history_candidates(
        repo_path=repo, commit_sha=pinned, seed_paths=["retry.py"],
    )
    assert result.ok, result.error
    assert "future.py" not in result.candidates
    assert "dirty.py" not in result.candidates


def test_git_history_reports_its_own_failure_without_raising(tmp_path):
    result = git_history_candidates(
        repo_path=str(tmp_path / "not-a-repo"), commit_sha="abc123",
        seed_paths=["retry.py"],
    )
    assert not result.ok
    assert result.candidates == []


def test_git_history_needs_a_seed():
    result = git_history_candidates(
        repo_path="/tmp", commit_sha="abc123", seed_paths=[],
    )
    assert result.ok
    assert result.candidates == []
    assert result.queries_run == 0


# --- The hard LLM deadline ------------------------------------------------------


def test_the_llm_call_receives_the_remaining_time_budget(monkeypatch, tmp_path):
    """Checking the clock between rounds bounds the loop's own bookkeeping, not
    the round trip that spends the time. A single hung call could overrun the
    whole budget while every between-round check passed."""
    from app.investigation_loop import InvestigationLoopBudget, run_investigation_loop
    from app.llm import LLMClient, LLMConfig
    import app.investigation_loop as loop_module

    seen: List[Optional[float]] = []

    class _Client(LLMClient):
        def generate_text(
            self, messages, *, temperature=None, max_tokens=None, timeout=None,
        ) -> str:
            seen.append(timeout)
            return json.dumps({
                "status": "unresolved", "conclusion": "not determined",
                "findings": [], "search_leads": [], "open_hypotheses": [],
                "missing_evidence": [],
            })

    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    (tmp_path / "repo" / "retry.py").write_text("MAX = 3\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    pinned = _git(repo, "rev-parse", "HEAD").strip()

    monkeypatch.setattr(loop_module, "is_reasoning_model", lambda provider, model: True)
    config = LLMConfig(
        provider="anthropic", api_key="k", model="claude-sonnet-5",
        base_url=None, timeout=300.0,
    )
    result = run_investigation_loop(
        _Client(), config,
        repo_path=repo, commit_sha=pinned, question="retry の上限は?",
        search_keywords=["retry"],
        budget=InvestigationLoopBudget(max_rounds=1, timeout_seconds=30),
    )
    assert result.rounds, result.error
    assert seen, "the loop made no call"
    # A real, decreasing deadline -- never the provider's full configured
    # timeout, which would make the loop budget advisory.
    assert seen[0] is not None
    assert 0 < seen[0] <= 30


def test_a_spent_time_budget_stops_the_loop_instead_of_calling(monkeypatch, tmp_path):
    from app.investigation_loop import InvestigationLoopBudget, run_investigation_loop
    from app.llm import LLMClient, LLMConfig
    import app.investigation_loop as loop_module

    calls: List[int] = []

    class _Client(LLMClient):
        def generate_text(
            self, messages, *, temperature=None, max_tokens=None, timeout=None,
        ) -> str:
            calls.append(1)
            return "{}"

    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    (tmp_path / "repo" / "retry.py").write_text("MAX = 3\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    pinned = _git(repo, "rev-parse", "HEAD").strip()

    monkeypatch.setattr(loop_module, "is_reasoning_model", lambda provider, model: True)
    # A budget whose whole allowance is below the floor for making a call.
    monkeypatch.setattr(loop_module, "_MIN_CALL_SECONDS", 10_000.0)
    config = LLMConfig(
        provider="anthropic", api_key="k", model="claude-sonnet-5",
        base_url=None, timeout=300.0,
    )
    result = run_investigation_loop(
        _Client(), config,
        repo_path=repo, commit_sha=pinned, question="retry の上限は?",
        search_keywords=["retry"],
        budget=InvestigationLoopBudget(max_rounds=1, timeout_seconds=30),
    )
    assert calls == []
    # No round ran at all, so the loop reports `unresolved` rather than
    # `budget_exhausted` (the pre-existing rule: a budget too small to run one
    # round investigated nothing). Either way it is a RESEARCH LIMITATION -- the
    # system did not get to look -- and never an execution failure.
    assert result.stop_reason == "unresolved"
    assert result.outcome_class == "research_limitation"
    assert result.failure_class is None


def test_a_mock_configuration_is_an_execution_failure_with_no_findings(tmp_path):
    from app.investigation_loop import InvestigationLoopBudget, run_investigation_loop
    from app.llm import LLMConfig, MockLLMClient

    config = LLMConfig(
        provider="mock", api_key=None, model="mock", base_url=None, timeout=10.0,
    )
    result = run_investigation_loop(
        MockLLMClient(), config,
        repo_path=str(tmp_path), commit_sha="abc123", question="x",
        budget=InvestigationLoopBudget(max_rounds=1),
    )
    assert result.stop_reason == "failed"
    assert result.outcome_class == "execution_failure"
    assert result.failure_class == "config_invalid"
    # An execution failure asserts nothing about the system.
    assert result.findings == []
    assert result.rounds == []


# --- Structural sources are lead-driven, not a second keyword guess ------------


def test_structural_sources_are_skipped_until_something_is_established(
    monkeypatch, tmp_path,
):
    """They answer "what is AROUND these files", so seeding them from a keyword
    guess would make them a second guess about the same guess -- and on a small
    per-round read budget it would cost the file the developer asked about.

    A fresh round 1 therefore uses the direct matches only; round 2 onward is
    seeded from what round 1 actually read.
    """
    from app.investigation_loop import (
        InvestigationLoopBudget,
        LoopCarryOver,
        run_investigation_loop,
    )
    from app.llm import LLMClient, LLMConfig
    import app.investigation_loop as loop_module

    seeds_seen: List[List[str]] = []

    def _record_git(*, repo_path, commit_sha, seed_paths, **kwargs):
        seeds_seen.append(list(seed_paths))
        return ExplorerResult(source_kind="git_history", revision=commit_sha)

    monkeypatch.setattr(loop_module, "git_history_candidates", _record_git)
    monkeypatch.setattr(loop_module, "is_reasoning_model", lambda provider, model: True)

    class _Client(LLMClient):
        def generate_text(
            self, messages, *, temperature=None, max_tokens=None, timeout=None,
        ) -> str:
            return json.dumps({
                "status": "unresolved", "conclusion": "まだ特定できていません",
                "findings": [{
                    "claim_kind": "fact",
                    "statement": "retry は 3 回で打ち切る",
                    "evidence": [{"path": "retry.py", "start_line": 1, "end_line": 1,
                                  "summary": "上限定義"}],
                    "runtime_evidence": [], "competing_explanations": [],
                    "refutation_conditions": [], "next_investigation": None,
                    "uncertainty": "",
                }],
                "search_leads": ["config"], "open_hypotheses": [],
                "missing_evidence": ["設定の読み出し箇所"],
            })

    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    (tmp_path / "repo" / "retry.py").write_text("MAX = 3\n", encoding="utf-8")
    (tmp_path / "repo" / "config.py").write_text("RETRIES = 3\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    pinned = _git(repo, "rev-parse", "HEAD").strip()

    config = LLMConfig(
        provider="anthropic", api_key="k", model="claude-sonnet-5",
        base_url=None, timeout=60.0,
    )
    result = run_investigation_loop(
        _Client(), config,
        repo_path=repo, commit_sha=pinned, question="retry の上限は?",
        search_keywords=["retry"],
        budget=InvestigationLoopBudget(max_rounds=2, max_files_per_round=1),
    )
    assert result.rounds, result.error
    # Round 1 ran without consulting a structural source at all.
    assert seeds_seen == [["retry.py"]] or seeds_seen == []
    if seeds_seen:
        # If a second round ran, its seed is what round 1 actually read.
        assert seeds_seen[0] == ["retry.py"]

    # A RESUMED run has carried-over reads, so its very first round is
    # lead-driven and does consult them.
    seeds_seen.clear()
    run_investigation_loop(
        _Client(), config,
        repo_path=repo, commit_sha=pinned, question="retry の上限は?",
        search_keywords=["config"],
        carry_over=LoopCarryOver(read_paths=["retry.py"], search_leads=["config"]),
        budget=InvestigationLoopBudget(max_rounds=1, max_files_per_round=1),
    )
    assert seeds_seen == [["retry.py"]]
