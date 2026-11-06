"""Feature ids are kept by a system-owned registry (IK-0038, supersedes IK-0032's approach).

The drafts LLM never writes a feature id: it answers each feature with
``same_as`` (a handle F1..Fn of the registry shown in the prompt) or
``new: true``; the system maps handles to keys and mints keys for new ones.
"""

import pytest
from reasoning_pipeline_helpers import (  # noqa: F401  (fixtures)
    FixedClient,
    admin_client,
    draft_response,
    git_repo,
    snapshot,
    system_headers,
    use_client,
)


def feat(name, *, same_as=None, new=None, path="src/main.py", feature_id=None):
    item = {
        "name": name,
        "summary": f"{name} summary.",
        "user_value": f"{name} value.",
        "success_criteria": ["works"],
        "risks": [],
        "evidence": [{"path": path, "start_line": 1, "end_line": 5, "summary": name}],
    }
    if same_as is not None:
        item["same_as"] = same_as
    if new is not None:
        item["new"] = new
    if feature_id is not None:
        item["feature_id"] = feature_id
    return item


def _generate(client, headers, monkeypatch, features, status=201):
    fake = use_client(monkeypatch, FixedClient(draft_response(features)))
    r = client.post("/repository/drafts/generate", headers=headers)
    assert r.status_code == status, r.text
    return fake, r.json()


def _ids(body):
    return [f["feature_id"] for f in body["feature_drafts"]]


def test_prompt_schema_asks_for_handles_not_ids(admin_client, git_repo, monkeypatch):
    from app.draft_generator import PROMPT_VERSION

    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    fake, body = _generate(admin_client, h, monkeypatch, [feat("User Management", new=True)])
    prompt = fake.prompts[0][1]["content"]
    assert '"feature_id"' not in prompt
    assert '"same_as"' in prompt and '"new"' in prompt
    assert "do NOT write a feature id" in prompt
    assert "## Registered features" not in prompt
    assert PROMPT_VERSION == "v3"
    assert _ids(body) == ["user-management"]
    resolution = body["feature_id_resolution"]
    assert resolution["previous_run_id"] is None
    assert resolution["new"] == ["user-management"]
    assert resolution["minted"] == [
        {"feature_key": "user-management", "display_name": "User Management"}
    ]


def test_handles_map_back_to_registry_keys(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _, first = _generate(admin_client, h, monkeypatch, [feat("User Management", new=True)])

    # The model renames the feature; the key stays because it answered F1.
    fake, second = _generate(
        admin_client, h, monkeypatch,
        [feat("Account administration", same_as="F1"),
         feat("Mail", new=True, path="src/mailer.py")],
    )
    prompt = fake.prompts[0][1]["content"]
    assert "## Registered features" in prompt
    assert (
        "- F1 | name: User Management | summary: User Management summary. "
        "| evidence: src/main.py"
    ) in prompt
    assert _ids(second) == ["user-management", "mail"]
    resolution = second["feature_id_resolution"]
    assert resolution["previous_run_id"] == first["intelligence_run"]["id"]
    assert resolution["handle_matched"] == [{"handle": "F1", "feature_key": "user-management"}]
    assert resolution["reused"] == ["user-management"]
    assert resolution["new"] == ["mail"]
    assert resolution["resolved"] == []

    # Third run: both are registered; handles follow registry order and the
    # display name stays the one the key was minted from.
    fake, third = _generate(
        admin_client, h, monkeypatch,
        [feat("Mail notify", same_as="f2"), feat("Users", same_as="[F1]")],
    )
    assert "- F2 | name: Mail |" in fake.prompts[0][1]["content"]
    assert "- F1 | name: User Management |" in fake.prompts[0][1]["content"]
    assert _ids(third) == ["mail", "user-management"]
    assert third["feature_id_resolution"]["not_referenced"] == []


def test_unknown_and_duplicate_handles_drop_only_that_item(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _generate(admin_client, h, monkeypatch, [feat("User Management", new=True)])

    _, body = _generate(
        admin_client, h, monkeypatch,
        [feat("Users", same_as="F1"),
         feat("Users again", same_as="F1"),
         feat("Ghost", same_as="F9")],
    )
    assert _ids(body) == ["user-management"]
    dropped = body["feature_id_resolution"]["dropped"]
    assert [(d["name"], d["reason"]) for d in dropped] == [
        ("Users again", "duplicate_same_as"),
        ("Ghost", "unknown_handle"),
    ]


def test_run_with_no_valid_feature_left_fails_422(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _, body = _generate(
        admin_client, h, monkeypatch, [feat("Ghost", same_as="F4")], status=422
    )
    assert body["intelligence_run"]["status"] == "failed"
    assert "unknown_handle" in body["detail"]


def test_minted_key_collision_gets_suffix_and_is_logged(admin_client, git_repo, monkeypatch):
    from app.feature_registry import mint_feature_key

    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _generate(admin_client, h, monkeypatch, [feat("User Management", new=True)])
    # The model says "new" although the name matches a registered key: the
    # system keeps the model's decision, suffixes the key and logs it.
    _, body = _generate(
        admin_client, h, monkeypatch,
        [feat("User Management", same_as="F1"), feat("User management", new=True)],
    )
    expected = mint_feature_key("User management", {"user-management"})
    assert expected.startswith("user-management-")
    assert len(expected) == len("user-management-") + 4
    assert _ids(body) == ["user-management", expected]
    assert body["feature_id_resolution"]["slug_matches"] == [
        {"feature_key": expected, "name_slug": "user-management",
         "matches": "user-management", "kind": "registered"}
    ]


def test_mint_is_stable_and_handles_non_ascii_names():
    from app.feature_registry import mint_feature_key

    assert mint_feature_key("TOV Solver", set()) == "tov-solver"
    assert mint_feature_key("TOV Solver", set()) == mint_feature_key("TOV Solver", set())
    ja = mint_feature_key("磁場の推定", set())
    assert ja.startswith("feature-") and ja == mint_feature_key("磁場の推定", set())
    assert mint_feature_key("磁場の推定", {ja}) != ja


def test_legacy_feature_id_equal_to_a_live_key_is_a_reference(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _generate(admin_client, h, monkeypatch, [feat("User Management", new=True)])
    _, body = _generate(
        admin_client, h, monkeypatch,
        [feat("Users", feature_id="user-management"), feat("Other", feature_id="made-up-id")],
    )
    # A made-up id is never used as a key: the system mints from the name.
    assert _ids(body) == ["user-management", "other"]
    notes = [n["note"] for n in body["feature_id_resolution"]["normalized"]]
    assert any("legacy feature_id 'user-management'" in n for n in notes)


def test_existing_ids_of_the_latest_run_are_migrated(admin_client, git_repo, monkeypatch):
    from app.db import _migrate_feature_registry, get_conn

    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _, first = _generate(admin_client, h, monkeypatch, [feat("DCF synthetic field", new=True)])
    # Simulate a database from before the registry: an id the old contract
    # let the model write, and no registry rows.
    with get_conn() as conn:
        conn.execute(
            "UPDATE feature_drafts SET feature_id = 'dcf-synthetic-field-exercise' "
            "WHERE intelligence_run_id = ?",
            (first["intelligence_run"]["id"],),
        )
        conn.execute("DELETE FROM feature_registry")
        _migrate_feature_registry(conn)
        rows = conn.execute(
            "SELECT feature_key, display_name, origin FROM feature_registry"
        ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("dcf-synthetic-field-exercise", "DCF synthetic field", "migrated")
        ]
        _migrate_feature_registry(conn)  # idempotent
        assert conn.execute("SELECT COUNT(*) FROM feature_registry").fetchone()[0] == 1
        conn.execute("DELETE FROM feature_registry")

    # Seeding also happens on first use by the drafts route.
    fake, second = _generate(
        admin_client, h, monkeypatch, [feat("DCF Synthetic Field (estimation)", same_as="F1")]
    )
    assert "- F1 | name: DCF synthetic field |" in fake.prompts[0][1]["content"]
    assert _ids(second) == ["dcf-synthetic-field-exercise"]


@pytest.mark.parametrize(
    "raw, expected",
    [("F2", "b"), ("f2", "b"), (" [F2] ", "b"), ("2", "b"),
     ("a", "a"), ("A", "a"), ("F7", None), ("zzz", None)],
)
def test_reference_normalisation(raw, expected):
    from app.draft_generator import _resolve_reference
    from app.feature_registry import RegistryEntry

    entries = [RegistryEntry("a", "A", handle="F1"), RegistryEntry("b", "B", handle="F2")]
    entry = _resolve_reference(
        raw, {e.handle: e for e in entries}, {e.feature_key: e for e in entries}
    )
    assert (entry.feature_key if entry else None) == expected
