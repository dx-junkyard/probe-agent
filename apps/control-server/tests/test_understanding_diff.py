"""Unit tests for Issue #136: deterministic understanding-revision diffing.

Covers added / removed / confidence-changed / summary-changed / no-change
cases, all matched by exact item ``name`` only (never semantic similarity).
"""

from __future__ import annotations

from app.understanding_diff import UNDERSTANDING_SECTIONS, diff_understanding


def _item(name, summary="summary", level="likely"):
    return {"name": name, "summary": summary, "confidence": {"level": level, "reason": ""}}


def _understanding(system_purpose=None, core_capabilities=None):
    return {
        "system_purpose": system_purpose or [],
        "core_capabilities": core_capabilities or [],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
        "probe_flow_candidates": [],
    }


def _section(sections, name):
    return next(s for s in sections if s["section"] == name)


def test_added_and_removed_items():
    previous = _understanding(system_purpose=[_item("Purpose A")])
    current = _understanding(system_purpose=[_item("Purpose B")])
    sections = diff_understanding(previous, current)
    sp = _section(sections, "system_purpose")
    assert sp["added"] == ["Purpose B"]
    assert sp["removed"] == ["Purpose A"]
    assert sp["confidence_changed"] == []
    assert sp["summary_changed"] == []


def test_confidence_change_detected():
    previous = _understanding(core_capabilities=[_item("Summarize", level="uncertain")])
    current = _understanding(core_capabilities=[_item("Summarize", level="confirmed")])
    sections = diff_understanding(previous, current)
    cc = _section(sections, "core_capabilities")
    assert cc["added"] == []
    assert cc["removed"] == []
    assert cc["confidence_changed"] == [
        {"name": "Summarize", "before": "uncertain", "after": "confirmed"}
    ]
    assert cc["summary_changed"] == []


def test_summary_change_detected():
    previous = _understanding(core_capabilities=[_item("Summarize", summary="old")])
    current = _understanding(core_capabilities=[_item("Summarize", summary="new")])
    sections = diff_understanding(previous, current)
    cc = _section(sections, "core_capabilities")
    assert cc["summary_changed"] == ["Summarize"]
    assert cc["confidence_changed"] == []


def test_no_change_when_identical():
    understanding = _understanding(system_purpose=[_item("Purpose A")])
    sections = diff_understanding(understanding, understanding)
    for s in sections:
        assert s["added"] == []
        assert s["removed"] == []
        assert s["confidence_changed"] == []
        assert s["summary_changed"] == []


def test_rename_is_removed_plus_added_never_matched():
    """A renamed item is structurally indistinguishable from remove+add
    (Principle 6: exact-name matching only, no semantic similarity)."""
    previous = _understanding(system_purpose=[_item("Old Name", summary="same summary")])
    current = _understanding(system_purpose=[_item("New Name", summary="same summary")])
    sections = diff_understanding(previous, current)
    sp = _section(sections, "system_purpose")
    assert sp["added"] == ["New Name"]
    assert sp["removed"] == ["Old Name"]


def test_all_sections_present_even_when_empty():
    sections = diff_understanding(None, None)
    assert {s["section"] for s in sections} == set(UNDERSTANDING_SECTIONS)
    for s in sections:
        assert s["added"] == []
        assert s["removed"] == []


def test_none_previous_treated_as_no_sections():
    current = _understanding(system_purpose=[_item("Purpose A")])
    sections = diff_understanding(None, current)
    sp = _section(sections, "system_purpose")
    assert sp["added"] == ["Purpose A"]
    assert sp["removed"] == []
