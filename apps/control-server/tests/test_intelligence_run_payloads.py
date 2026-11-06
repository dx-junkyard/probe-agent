"""IK-0029: reasoning runs keep their exact LLM request/response."""

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


def _payloads(client, headers, run_id):
    r = client.get(f"/intelligence-runs/{run_id}/payloads", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_completed_drafts_run_stores_request_response_and_usage(
    admin_client, git_repo, monkeypatch
):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    use_client(monkeypatch, FixedClient(draft_response()))

    body = admin_client.post("/repository/drafts/generate", headers=h).json()
    run = body["intelligence_run"]
    assert run["status"] == "completed"

    stored = _payloads(admin_client, h, run["id"])
    assert stored["intelligence_run"]["id"] == run["id"]
    kinds = [p["kind"] for p in stored["payloads"]]
    assert kinds == ["request", "response"]
    request, response = stored["payloads"]
    assert "Repository contents" in request["text"]
    assert '"max_tokens"' in request["text"]
    assert '"user-management"' in response["text"]
    assert response["usage"] == {"input_tokens": 11, "output_tokens": 7}
    assert response["finish_reason"] == "stop"
    assert request["prompt_version"] == run["prompt_version"]


def test_response_that_fails_validation_is_still_stored_verbatim(
    admin_client, git_repo, monkeypatch
):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    use_client(monkeypatch, FixedClient('{"system_profile": "not an object"}'))

    body = admin_client.post("/repository/drafts/generate", headers=h).json()
    run = body["intelligence_run"]
    assert run["status"] == "failed"

    stored = _payloads(admin_client, h, run["id"])
    response = [p for p in stored["payloads"] if p["kind"] == "response"][0]
    assert response["text"] == '{"system_profile": "not an object"}'


def test_code_link_and_probe_plan_runs_store_payloads(
    admin_client, git_repo, monkeypatch
):
    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    use_client(monkeypatch, FixedClient(draft_response()))
    admin_client.post("/repository/drafts/generate", headers=h)
    admin_client.post("/repository/symbols/index", headers=h)

    use_client(monkeypatch, FixedClient({"links": [link("user-management", "list_users")]}))
    links = admin_client.post("/repository/code-links/generate", headers=h).json()
    link_run = links["intelligence_run"]
    assert link_run["status"] == "completed"
    assert [p["kind"] for p in _payloads(admin_client, h, link_run["id"])["payloads"]] == [
        "request",
        "response",
    ]
    for item in links["links"]:
        admin_client.put(
            f"/repository/code-links/{item['id']}/review",
            json={"review_status": "accepted"},
            headers=h,
        )

    use_client(
        monkeypatch,
        FixedClient(
            {
                "objective": "Observe listing.",
                "probe_points": [
                    {
                        "component_id": "list-observer",
                        "symbol_qualified_name": "list_users",
                        "symbol_path": "src/main.py",
                        "reason": "Central path.",
                        "recommended_mode": "trace",
                        "side_effect_risk": "none",
                        "replayability": "safe",
                    }
                ],
                "avoid_reasons": [],
            }
        ),
    )
    plan = admin_client.post(
        "/repository/probe-plans/generate?feature_id=user-management", headers=h
    ).json()
    plan_run_id = plan["intelligence_run_id"]
    stored = _payloads(admin_client, h, plan_run_id)["payloads"]
    assert [p["kind"] for p in stored] == ["request", "response"]
    assert "list_users" in stored[0]["text"]


def test_provider_error_records_request_and_error_marker(
    admin_client, git_repo, monkeypatch
):
    from app.llm import LLMError

    class Failing(FixedClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            raise LLMError("upstream exploded")

    h = system_headers(admin_client)
    snapshot(admin_client, h, git_repo)
    use_client(monkeypatch, Failing("unused"))
    run = admin_client.post("/repository/drafts/generate", headers=h).json()[
        "intelligence_run"
    ]
    assert run["status"] == "failed"
    stored = _payloads(admin_client, h, run["id"])["payloads"]
    assert [p["kind"] for p in stored] == ["request", "response"]
    assert "upstream exploded" in stored[1]["text"]


def test_payloads_are_system_scoped_and_404_for_unknown_runs(
    admin_client, git_repo, monkeypatch
):
    h = system_headers(admin_client, "A")
    other = system_headers(admin_client, "B")
    snapshot(admin_client, h, git_repo)
    use_client(monkeypatch, FixedClient(draft_response()))
    run_id = admin_client.post("/repository/drafts/generate", headers=h).json()[
        "intelligence_run"
    ]["id"]

    assert admin_client.get(f"/intelligence-runs/{run_id}/payloads", headers=other).status_code == 404
    assert admin_client.get("/intelligence-runs/999999/payloads", headers=h).status_code == 404


def test_mock_provider_is_not_wrapped():
    from app.intelligence_payloads import RecordingLLMClient, wrap_for_recording
    from app.llm import MockLLMClient

    mock = MockLLMClient()
    assert wrap_for_recording(mock) is mock
    assert isinstance(wrap_for_recording(FixedClient("x")), RecordingLLMClient)


def test_request_is_stored_after_secret_redaction():
    from app.intelligence_payloads import RecordingLLMClient

    client = RecordingLLMClient(FixedClient("ok"))
    client.generate_text(
        [{"role": "user", "content": "key ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}],
        max_tokens=10,
    )
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in client.calls[0].request_text
