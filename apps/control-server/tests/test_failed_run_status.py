"""IK-0034: failed reasoning runs answer 422/502, not 201."""

from reasoning_pipeline_helpers import (  # noqa: F401  (fixtures)
    FixedClient,
    admin_client,
    draft_response,
    git_repo,
    link,
    snapshot,
    system_headers,
    use_client,
)


def test_status_rule():
    from app.routes.project_intelligence import failed_run_status

    assert failed_run_status("Failed to parse LLM response: features must be a non-empty array") == 422
    assert failed_run_status("upstream exploded") == 502
    assert failed_run_status(None) == 502


def test_invalid_draft_output_is_422_with_the_failed_run(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    use_client(monkeypatch, FixedClient('{"system_profile": {}}'))
    r = admin_client.post("/repository/drafts/generate", headers=h)
    assert r.status_code == 422
    body = r.json()
    assert body["intelligence_run"]["status"] == "failed"
    assert body["detail"] == body["intelligence_run"]["error_details"]
    assert body["detail"].startswith("Failed to parse LLM response")
    # The failed run is persisted and readable like before.
    latest = admin_client.get(
        f"/intelligence-runs/{body['intelligence_run']['id']}/payloads", headers=h
    )
    assert latest.status_code == 200


def test_provider_error_is_502(admin_client, git_repo, monkeypatch):
    from app.llm import LLMError

    class Failing(FixedClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            raise LLMError("upstream exploded")

    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    use_client(monkeypatch, Failing("unused"))
    r = admin_client.post("/repository/drafts/generate", headers=h)
    assert r.status_code == 502
    assert "upstream exploded" in r.json()["detail"]


def test_code_links_and_probe_plan_failures(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    use_client(monkeypatch, FixedClient(draft_response()))
    assert admin_client.post("/repository/drafts/generate", headers=h).status_code == 201
    admin_client.post("/repository/symbols/index", headers=h)

    use_client(monkeypatch, FixedClient({"links": [link("user-management", "ghost", "src/nope.py")]}))
    r = admin_client.post("/repository/code-links/generate", headers=h)
    assert r.status_code == 422
    assert r.json()["links"] == []
    assert r.json()["dropped_links"][0]["reason"] == "symbol not in index"

    use_client(monkeypatch, FixedClient({"links": [link("user-management", "list_users")]}))
    ok = admin_client.post("/repository/code-links/generate", headers=h)
    assert ok.status_code == 201
    for item in ok.json()["links"]:
        admin_client.put(
            f"/repository/code-links/{item['id']}/review",
            json={"review_status": "accepted"},
            headers=h,
        )
    use_client(monkeypatch, FixedClient("not json"))
    r = admin_client.post(
        "/repository/probe-plans/generate?feature_id=user-management", headers=h
    )
    assert r.status_code == 422
    assert r.json()["intelligence_run"]["status"] == "failed"
    assert r.json()["status"] == "rejected"


def test_mock_provider_code_links_is_502(admin_client, git_repo):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    admin_client.post("/repository/drafts/generate", headers=h)
    admin_client.post("/repository/symbols/index", headers=h)
    r = admin_client.post("/repository/code-links/generate", headers=h)
    assert r.status_code == 502
    assert "prohibited" in r.json()["detail"]
