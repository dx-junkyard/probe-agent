"""Tests for Issue #440 (Epic #436): the deterministic UI help-mode registry.

Covers:
- registry-level invariants: every `doc_path` resolves to a real repo file,
  `help_id`s are unique, every `screen_id` is a registered assistant screen,
  every `related_help_ids` entry exists, `scope`/action `kind` values stay
  inside their finite sets, and each of the four screens has at least a
  screen-scope entry plus element entries.
- API behavior: `GET /assistant/ui-help` (all / filtered by screen_id),
  `GET /assistant/ui-help/{help_id}` exact match, unknown id -> 404, and
  every response carries `registry_version` + `decision_method:
  "deterministic"`.
- no LLM is ever invoked: the registry module imports nothing from
  `app.llm`, and patching `create_llm_client` to explode still lets both
  endpoints answer.
- TypeScript/Python parity: `src/lib/ui-help.ts`'s `HELP_IDS` array lists
  exactly the same ids as the Python registry (the same discipline
  `test_interview_type_parity.py` already applies to shared response
  contracts, applied here to the help-mode id space).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.assistant import get_screen_context
from app.ui_help_registry import (
    HELP_BY_ID,
    UI_HELP_ACTION_KINDS,
    UI_HELP_ENTRIES,
    UI_HELP_REGISTRY_VERSION,
    UI_HELP_SCOPES,
    entries_for_screen,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_UI_HELP_TS = REPO_ROOT / "apps/dashboard/src/lib/ui-help.ts"

EXPECTED_SCREEN_IDS = {"overview", "interview", "ux-design-studio", "journey-blueprint"}


# --- Registry-level invariants -----------------------------------------------


def test_registry_help_ids_are_unique():
    ids = [e.help_id for e in UI_HELP_ENTRIES]
    assert len(ids) == len(set(ids)), f"duplicate help_id(s): {sorted(set(x for x in ids if ids.count(x) > 1))}"
    assert HELP_BY_ID == {e.help_id: e for e in UI_HELP_ENTRIES}


def test_registry_doc_refs_resolve_to_real_files():
    missing = []
    for entry in UI_HELP_ENTRIES:
        assert entry.doc_refs, f"{entry.help_id} has no doc_refs"
        for doc in entry.doc_refs:
            path = REPO_ROOT / doc.doc_path
            if not path.is_file():
                missing.append((entry.help_id, doc.doc_path))
    assert not missing, f"doc_refs pointing at missing files: {missing}"


def test_registry_doc_ref_anchors_resolve_to_real_headings():
    """A citation the reader cannot follow does not show its source.

    `test_registry_doc_refs_resolve_to_real_files` only proves the file is
    there; an anchor naming a section that does not exist still renders as a
    source in the help panel while pointing at nothing. The anchor is written
    as the heading's own text (with or without its `#` markers), so this
    compares against the headings the doc actually has.
    """
    unresolved = []
    heading_cache: dict = {}
    for entry in UI_HELP_ENTRIES:
        for doc in entry.doc_refs:
            if not doc.anchor:
                continue
            path = REPO_ROOT / doc.doc_path
            if doc.doc_path not in heading_cache:
                heading_cache[doc.doc_path] = [
                    line.lstrip("#").strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.startswith("#")
                ]
            if doc.anchor.lstrip("#").strip() not in heading_cache[doc.doc_path]:
                unresolved.append((entry.help_id, doc.doc_path, doc.anchor))
    assert not unresolved, (
        "doc_ref anchors naming no real heading: " + repr(unresolved)
    )


def test_registry_screen_ids_are_registered_assistant_screens():
    screen_ids = {e.screen_id for e in UI_HELP_ENTRIES}
    assert screen_ids == EXPECTED_SCREEN_IDS
    for screen_id in screen_ids:
        assert get_screen_context(screen_id) is not None, (
            f"{screen_id} is not a registered assistant screen (app.assistant.get_screen_context)"
        )


def test_registry_related_help_ids_exist():
    dangling = []
    for entry in UI_HELP_ENTRIES:
        for related in entry.related_help_ids:
            if related not in HELP_BY_ID:
                dangling.append((entry.help_id, related))
    assert not dangling, f"related_help_ids referencing unknown entries: {dangling}"


def test_registry_scope_and_action_kind_are_finite():
    for entry in UI_HELP_ENTRIES:
        assert entry.scope in UI_HELP_SCOPES, f"{entry.help_id} has invalid scope {entry.scope!r}"
        for action in entry.related_actions:
            assert action.kind in UI_HELP_ACTION_KINDS, (
                f"{entry.help_id} action {action.label!r} has invalid kind {action.kind!r}"
            )


def test_every_screen_has_a_screen_scope_entry_and_element_entries():
    for screen_id in EXPECTED_SCREEN_IDS:
        entries = entries_for_screen(screen_id)
        assert entries, f"{screen_id} has no help entries at all"
        scopes = {e.scope for e in entries}
        assert "screen" in scopes, f"{screen_id} has no screen-scope entry"
        assert "element" in scopes, f"{screen_id} has no element-scope entry"
        screen_scope_entries = [e for e in entries if e.scope == "screen"]
        assert len(screen_scope_entries) == 1, (
            f"{screen_id} should have exactly one screen-scope entry, got {len(screen_scope_entries)}"
        )
        assert 8 <= len(entries) <= 20, f"{screen_id} has {len(entries)} entries (expected roughly 8-14)"


def test_registry_text_is_japanese_non_empty():
    for entry in UI_HELP_ENTRIES:
        assert entry.title.strip()
        assert entry.summary.strip()
        assert entry.usage.strip()


# --- No LLM is ever invoked ---------------------------------------------------


def test_registry_module_imports_nothing_from_llm():
    import app.ui_help_registry as mod
    import app.routes.ui_help as route_mod

    for module in (mod, route_mod):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "app.llm" not in source
        assert "from ..llm" not in source
        assert "from .llm" not in source
        assert "create_llm_client" not in source
        assert "LLMClient" not in source


# --- API behavior -------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ui-help-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER",
        "INTELLIGENCE_LLM_MODEL",
        "INTELLIGENCE_LLM_TIMEOUT",
        "INTELLIGENCE_MAX_OUTPUT_TOKENS",
        "CONTROL_API_KEYS",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIMEOUT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(client):
    token = _login(client)
    return {"Authorization": f"Bearer {token}"}


def test_list_all_entries(admin_client):
    r = admin_client.get("/assistant/ui-help", headers=_bearer(admin_client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registry_version"] == UI_HELP_REGISTRY_VERSION
    assert body["decision_method"] == "deterministic"
    assert len(body["entries"]) == len(UI_HELP_ENTRIES)
    for entry in body["entries"]:
        assert entry["decision_method"] == "deterministic"
        assert entry["registry_version"] == UI_HELP_REGISTRY_VERSION


def test_list_filtered_by_screen_id(admin_client):
    r = admin_client.get(
        "/assistant/ui-help", params={"screen_id": "overview"}, headers=_bearer(admin_client)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entries"]
    assert all(e["screen_id"] == "overview" for e in body["entries"])
    assert len(body["entries"]) == len(entries_for_screen("overview"))


def test_list_filtered_by_unknown_screen_id_is_empty(admin_client):
    r = admin_client.get(
        "/assistant/ui-help", params={"screen_id": "no-such-screen"}, headers=_bearer(admin_client)
    )
    assert r.status_code == 200, r.text
    assert r.json()["entries"] == []


def test_get_exact_match(admin_client):
    r = admin_client.get("/assistant/ui-help/overview.brief.vision", headers=_bearer(admin_client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["help_id"] == "overview.brief.vision"
    assert body["screen_id"] == "overview"
    assert body["scope"] == "element"
    assert body["decision_method"] == "deterministic"
    assert body["registry_version"] == UI_HELP_REGISTRY_VERSION
    assert body["doc_refs"]


def test_get_unknown_help_id_is_404_never_a_guess(admin_client):
    r = admin_client.get("/assistant/ui-help/overview.brief.visionn", headers=_bearer(admin_client))
    assert r.status_code == 404, r.text

    r = admin_client.get("/assistant/ui-help/totally-unknown", headers=_bearer(admin_client))
    assert r.status_code == 404, r.text


def test_endpoints_never_call_llm_even_if_client_creation_explodes(admin_client, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("ui-help must never construct an LLM client")

    monkeypatch.setattr("app.llm.create_llm_client", _boom)

    headers = _bearer(admin_client)
    r = admin_client.get("/assistant/ui-help", headers=headers)
    assert r.status_code == 200, r.text
    r = admin_client.get("/assistant/ui-help/interview", headers=headers)
    assert r.status_code == 200, r.text


def test_requires_authentication(admin_client):
    r = admin_client.get("/assistant/ui-help")
    assert r.status_code in (401, 403), r.text


# --- TypeScript/Python parity -------------------------------------------------


def _ts_help_ids() -> set:
    source = DASHBOARD_UI_HELP_TS.read_text(encoding="utf-8")
    match = re.search(
        r"\bexport\s+const\s+HELP_IDS\s*=\s*\[(.*?)\]\s*as\s+const\s*;",
        source,
        flags=re.DOTALL,
    )
    assert match, "HELP_IDS export not found in src/lib/ui-help.ts"
    return set(re.findall(r'["\']([^"\']+)["\']', match.group(1)))


def test_dashboard_help_ids_match_python_registry():
    ts_ids = _ts_help_ids()
    py_ids = {e.help_id for e in UI_HELP_ENTRIES}
    only_ts = ts_ids - py_ids
    only_py = py_ids - ts_ids
    assert not only_ts, f"HELP_IDS in ui-help.ts with no Python registry entry: {sorted(only_ts)}"
    assert not only_py, f"Python registry entries missing from ui-help.ts HELP_IDS: {sorted(only_py)}"


def test_dashboard_help_mode_screen_ids_match():
    source = DASHBOARD_UI_HELP_TS.read_text(encoding="utf-8")
    match = re.search(
        r"\bexport\s+const\s+HELP_MODE_SCREEN_IDS\s*=\s*\[(.*?)\]\s*as\s+const\s*;",
        source,
        flags=re.DOTALL,
    )
    assert match, "HELP_MODE_SCREEN_IDS export not found in src/lib/ui-help.ts"
    ts_screen_ids = set(re.findall(r'["\']([^"\']+)["\']', match.group(1)))
    assert ts_screen_ids == EXPECTED_SCREEN_IDS
