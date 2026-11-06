"""IK-0032: drafts regeneration keeps feature ids stable."""

from reasoning_pipeline_helpers import (  # noqa: F401  (fixtures)
    FixedClient,
    admin_client,
    draft_response,
    feature,
    git_repo,
    snapshot,
    system_headers,
    use_client,
)


def _generate(client, headers, monkeypatch, features):
    fake = use_client(monkeypatch, FixedClient(draft_response(features)))
    r = client.post("/repository/drafts/generate", headers=headers)
    assert r.status_code == 201, r.text
    return fake, r.json()


def test_first_run_has_no_previous_list(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    fake, body = _generate(admin_client, h, monkeypatch, [feature("user-management", "User Management")])
    assert "Previously identified features" not in fake.prompts[0][1]["content"]
    assert body["feature_id_resolution"]["new"] == ["user-management"]
    assert body["feature_id_resolution"]["previous_run_id"] is None


def test_previous_features_are_offered_in_copyable_form(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _, first = _generate(admin_client, h, monkeypatch, [feature("user-management", "User Management")])

    fake, second = _generate(
        admin_client, h, monkeypatch,
        [feature("user-management", "User Management"), feature("mail-notify", "Mail", "src/mailer.py")],
    )
    prompt = fake.prompts[0][1]["content"]
    assert "## Previously identified features" in prompt
    assert "- feature_id: user-management | name: User Management | evidence: src/main.py" in prompt
    assert "copy its feature_id exactly" in prompt
    resolution = second["feature_id_resolution"]
    assert resolution["previous_run_id"] == first["intelligence_run"]["id"]
    assert resolution["reused"] == ["user-management"]
    assert resolution["new"] == ["mail-notify"]
    assert resolution["resolved"] == []


def test_renamed_id_with_same_name_resolves_to_previous_id(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    _generate(admin_client, h, monkeypatch, [feature("dcf-synthetic-field-exercise", "DCF synthetic field")])

    _, second = _generate(
        admin_client, h, monkeypatch,
        [feature("dcf-synthetic-field-estimation", "DCF Synthetic Field")],
    )
    assert [f["feature_id"] for f in second["feature_drafts"]] == ["dcf-synthetic-field-exercise"]
    assert second["feature_id_resolution"]["resolved"] == [
        {"from": "dcf-synthetic-field-estimation", "to": "dcf-synthetic-field-exercise"}
    ]


def test_previous_id_is_given_to_one_feature_only():
    from app.draft_generator import FeatureDraft, PreviousFeature, resolve_feature_ids

    def fd(fid, name):
        return FeatureDraft(fid, name, "s", "v", [], [], [])

    features = [fd("tov", "TOV solver"), fd("tov-solver", "TOV Solver")]
    resolution = resolve_feature_ids(features, [PreviousFeature("tov", "TOV solver")])
    assert [f.feature_id for f in features] == ["tov", "tov-solver"]
    assert resolution.reused == ["tov"]
    assert resolution.new == ["tov-solver"]
