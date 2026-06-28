"""Tests for Issue #71: worktree materialization of approved interview proposals.

Covers:
1. Target repository remains unchanged after worktree materialization.
2. A symbol with no prior metadata gets a valid probe-agent: block that
   #54's extractor re-parses to the approved values (round-trip).
3. Docstring edit + @probe instrumentation appear together in a single diff.
4. Re-materializing an already-applied approved set is idempotent.
5. Only #70-approved items are materialized; non-approved items never
   appear in the diff.
"""

from __future__ import annotations

import ast
import os
import subprocess
import tempfile
import textwrap

import pytest

from app.code_indexer import _parse_source_metadata
from app.docstring_writer import MetadataValues, _strip_module_prefix
from app.interview_materializer import (
    MaterializationItem,
    materialize_approved_set,
)
from app.patch_generator import _find_function_node


def _init_repo(tmp_path, files: dict[str, str]) -> tuple[str, str]:
    """Create a git repo in tmp_path with the given files and return (path, sha)."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    for path, content in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(textwrap.dedent(content))
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", repo, "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def _make_item(
    path="src/summarize.py",
    qualified_name="summarize.summarize_text",
    role="Summarize free text",
    capability="summarization",
    element_type="core",
    operation_kind="analysis",
    consumers=None,
    state_effects=None,
    recommended_mode="trace",
) -> MaterializationItem:
    return MaterializationItem(
        path=path,
        qualified_name=qualified_name,
        metadata=MetadataValues(
            role=role,
            capability=capability,
            element_type=element_type,
            operation_kind=operation_kind,
            consumers=consumers or ["api"],
            state_effects=state_effects or ["none"],
            probe_value="Validate latency",
        ),
        component_id=qualified_name.replace(".", "_"),
        recommended_mode=recommended_mode,
        line_start=0,
        line_end=0,
    )


SAMPLE_SOURCE = """\
def summarize_text(text):
    \"\"\"Summarize the given text.\"\"\"
    return text[:100]
"""


# --- Test 1: Target repo unchanged -----------------------------------------


def test_target_repo_unchanged_after_materialization(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": SAMPLE_SOURCE})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    item = _make_item()
    result = materialize_approved_set(repo, sha, [item], worktree_base)

    assert result.error is None
    assert result.diff != ""
    assert result.files_changed == 1

    current_sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert current_sha == sha

    status = subprocess.run(
        ["git", "-C", repo, "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert status == ""

    with open(os.path.join(repo, "src/summarize.py")) as f:
        assert f.read() == textwrap.dedent(SAMPLE_SOURCE)

    assert result.cleanup_state == "removed"


# --- Test 2: Round-trip through #54 extractor -------------------------------


def test_round_trip_metadata_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": SAMPLE_SOURCE})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    item = _make_item(
        role="Summarize free text",
        capability="summarization",
        element_type="core",
        operation_kind="analysis",
        consumers=["api", "dashboard"],
        state_effects=["none"],
    )

    worktree_path = None
    from app.patch_generator import create_worktree, cleanup_worktree
    from app.docstring_writer import apply_docstring_edits, _strip_module_prefix
    from app.patch_generator import instrument_file, ApprovedPoint

    try:
        worktree_path = create_worktree(repo, sha, worktree_base)
        full = os.path.join(worktree_path, "src/summarize.py")
        with open(full) as f:
            source = f.read()

        in_file = _strip_module_prefix("summarize.summarize_text", "src/summarize.py")
        source, _ = apply_docstring_edits(source, [(in_file, item.metadata)])
        source, _ = instrument_file(source, [ApprovedPoint(
            component_id="test",
            path="src/summarize.py",
            symbol=in_file,
            recommended_mode="trace",
            line_start=0,
            line_end=0,
        )])

        tree = ast.parse(source)
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "summarize_text":
                func_node = node
                break

        assert func_node is not None
        meta, warnings = _parse_source_metadata(func_node)
        assert meta is not None
        assert meta.role == "Summarize free text"
        assert meta.capability == "summarization"
        assert meta.element_type == "core"
        assert meta.operation_kind == "analysis"
        assert meta.consumers == ["api", "dashboard"]
        assert meta.state_effects == ["none"]
        assert meta.probe_value == "Validate latency"
    finally:
        if worktree_path:
            cleanup_worktree(repo, worktree_path)


# --- Test 3: Combined diff -------------------------------------------------


def test_docstring_and_probe_in_single_diff(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": SAMPLE_SOURCE})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    item = _make_item()
    result = materialize_approved_set(repo, sha, [item], worktree_base)

    assert result.error is None
    assert "probe-agent:" in result.diff
    assert "@probe" in result.diff
    assert "from probe_agent import probe" in result.diff

    diff_lines = result.diff.splitlines()
    diff_files = [l for l in diff_lines if l.startswith("diff --git")]
    assert len(diff_files) == 1


# --- Test 4: Idempotency ---------------------------------------------------


def test_idempotent_materialization(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))

    already_applied = """\
from probe_agent import probe

@probe(component_id="summarize_summarize_text")
def summarize_text(text):
    \"\"\"Summarize the given text.

    probe-agent:
      role: Summarize free text
      capability: summarization
      probe_value: Validate latency
      element_type: core
      operation_kind: analysis
      consumers: [api]
      state_effects: [none]
    \"\"\"
    return text[:100]
"""
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": already_applied})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    item = _make_item()
    result = materialize_approved_set(repo, sha, [item], worktree_base)

    assert result.error is None
    assert result.diff.strip() == ""
    assert result.files_changed == 0


# --- Test 5: Only approved items materialized --------------------------------


def test_only_approved_items_materialized(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))

    source_a = """\
def summarize_text(text):
    \"\"\"Summarize the given text.\"\"\"
    return text[:100]
"""
    source_b = """\
def classify_text(text):
    \"\"\"Classify the given text.\"\"\"
    return "positive"
"""
    repo, sha = _init_repo(tmp_path, {
        "src/summarize.py": source_a,
        "src/classifier.py": source_b,
    })
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    approved_item = _make_item(
        path="src/summarize.py",
        qualified_name="summarize.summarize_text",
    )

    result = materialize_approved_set(repo, sha, [approved_item], worktree_base)

    assert result.error is None
    assert result.files_changed == 1
    assert "summarize.py" in result.diff
    assert "classifier.py" not in result.diff
    assert "classify_text" not in result.diff


# --- Test 6: Worktree cleanup -----------------------------------------------


def test_worktree_cleaned_up(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": SAMPLE_SOURCE})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    item = _make_item()
    result = materialize_approved_set(repo, sha, [item], worktree_base)

    assert result.cleanup_state == "removed"
    assert not os.path.exists(result.worktree_path)


# --- Test 7: Module prefix stripping ----------------------------------------


def test_strip_module_prefix():
    assert _strip_module_prefix("summarize.summarize_text", "src/summarize.py") == "summarize_text"
    assert _strip_module_prefix("classifier.Cls.method", "src/classifier.py") == "Cls.method"
    assert _strip_module_prefix("top_func", "src/other.py") == "top_func"
    assert _strip_module_prefix("summarize", "src/summarize.py") == ""


# --- Test 8: No approved items returns error ---------------------------------


def test_empty_items_returns_error():
    result = materialize_approved_set("/fake", "abc", [], "/tmp")
    assert result.error is not None
    assert "No approved items" in result.error


# --- Test 9: Multiple symbols in one file ------------------------------------


def test_multiple_symbols_in_single_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))

    source = """\
def func_a():
    return 1

def func_b():
    return 2
"""
    repo, sha = _init_repo(tmp_path, {"src/multi.py": source})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    items = [
        _make_item(
            path="src/multi.py",
            qualified_name="multi.func_a",
            role="Function A",
            element_type="core",
        ),
        _make_item(
            path="src/multi.py",
            qualified_name="multi.func_b",
            role="Function B",
            element_type="element",
        ),
    ]

    result = materialize_approved_set(repo, sha, items, worktree_base)

    assert result.error is None
    assert result.files_changed == 1
    assert "probe-agent:" in result.diff
    assert "func_a" in result.diff
    assert "func_b" in result.diff
    assert "@probe" in result.diff


# --- Test 10: Failed items produce error ------------------------------------


def test_failed_items_produce_error(tmp_path, monkeypatch):
    """When a symbol is not found, the result reports an error with the count."""
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": SAMPLE_SOURCE})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    items = [
        _make_item(
            path="src/summarize.py",
            qualified_name="summarize.nonexistent_func",
        ),
    ]

    result = materialize_approved_set(repo, sha, items, worktree_base)

    assert result.error is not None
    assert "1 item(s) failed" in result.error
    assert result.items_applied == 0
    assert result.items_total == 1


def test_partial_failure_reports_items_applied(tmp_path, monkeypatch):
    """When some items succeed and others fail, items_applied reflects only successes."""
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": SAMPLE_SOURCE})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    items = [
        _make_item(
            path="src/summarize.py",
            qualified_name="summarize.summarize_text",
        ),
        _make_item(
            path="src/missing.py",
            qualified_name="missing.no_such_func",
        ),
    ]

    result = materialize_approved_set(repo, sha, items, worktree_base)

    assert result.error is not None
    assert result.items_applied == 1
    assert result.items_total == 2


# --- Test 11: Module-level docstring ----------------------------------------


def test_module_docstring_materialization(tmp_path, monkeypatch):
    """A module-level proposal writes a probe-agent: block in the module docstring."""
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))

    source = """\
def helper():
    return 1
"""
    repo, sha = _init_repo(tmp_path, {"src/utils.py": source})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    from app.patch_generator import create_worktree, cleanup_worktree
    from app.docstring_writer import write_metadata_to_source, MetadataValues

    worktree_path = None
    try:
        worktree_path = create_worktree(repo, sha, worktree_base)
        full = os.path.join(worktree_path, "src/utils.py")
        with open(full) as f:
            src = f.read()

        values = MetadataValues(role="Utility module", element_type="supporting")
        result, err = write_metadata_to_source(src, "", values)
        assert err is None
        assert "probe-agent:" in result
        assert "role: Utility module" in result

        tree = ast.parse(result)
        meta, warnings = _parse_source_metadata(tree)
        assert meta is not None
        assert meta.role == "Utility module"
        assert meta.element_type == "supporting"
    finally:
        if worktree_path:
            cleanup_worktree(repo, worktree_path)


def test_module_with_existing_docstring(tmp_path, monkeypatch):
    """Module-level probe-agent: block is added to an existing module docstring."""
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))

    source = '\"\"\"Utility helpers.\"\"\"\n\ndef helper():\n    return 1\n'
    repo, sha = _init_repo(tmp_path, {"src/utils.py": source})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    from app.docstring_writer import write_metadata_to_source, MetadataValues

    values = MetadataValues(role="Utility module", element_type="supporting")
    result, err = write_metadata_to_source(textwrap.dedent(source), "", values)
    assert err is None
    assert "Utility helpers." in result
    assert "probe-agent:" in result

    tree = ast.parse(result)
    meta, warnings = _parse_source_metadata(tree)
    assert meta is not None
    assert meta.role == "Utility module"


# --- Test 12: YAML-safe free text ------------------------------------------


def test_yaml_special_chars_round_trip(tmp_path, monkeypatch):
    """Free text with YAML-special characters round-trips through the extractor."""
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))

    source = """\
def tricky():
    return 1
"""
    repo, sha = _init_repo(tmp_path, {"src/tricky.py": source})
    worktree_base = str(tmp_path / "worktrees")
    os.makedirs(worktree_base)

    from app.docstring_writer import write_metadata_to_source, MetadataValues

    values = MetadataValues(
        role="Handle input: parse & validate [items]",
        capability="data-processing",
        system_purpose="Parse config #values with special: chars",
        probe_value="Check {throughput} & latency",
        element_type="core",
        operation_kind="validation",
    )
    result, err = write_metadata_to_source(textwrap.dedent(source), "tricky", values)
    assert err is None

    tree = ast.parse(result)
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "tricky":
            func_node = node
            break

    assert func_node is not None
    meta, warnings = _parse_source_metadata(func_node)
    assert meta is not None
    assert meta.role == "Handle input: parse & validate [items]"
    assert meta.system_purpose == "Parse config #values with special: chars"
    assert meta.probe_value == "Check {throughput} & latency"
