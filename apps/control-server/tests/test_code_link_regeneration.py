"""IK-0033: code-link regeneration keeps reviews and reports dropped links."""

import logging

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


def _prepare(client, headers, repo, monkeypatch):
    snapshot(client, headers, repo)
    use_client(monkeypatch, FixedClient(draft_response()))
    client.post("/repository/drafts/generate", headers=headers)
    client.post("/repository/symbols/index", headers=headers)


def _generate(client, headers, monkeypatch, links):
    use_client(monkeypatch, FixedClient({"links": links}))
    r = client.post("/repository/code-links/generate", headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _status(body):
    return {
        (l["feature_id"], l["symbol"]["path"], l["symbol"]["qualified_name"]): l["review_status"]
        for l in body["links"]
    }


def test_reviews_carry_over_to_identical_links(admin_client, git_repo, monkeypatch):
    h = system_headers(admin_client)
    _prepare(admin_client, h, git_repo, monkeypatch)
    first = _generate(
        admin_client, h, monkeypatch,
        [link("user-management", "list_users"), link("user-management", "helper")],
    )
    ids = {l["symbol"]["qualified_name"]: l["id"] for l in first["links"]}
    admin_client.put(f"/repository/code-links/{ids['list_users']}/review", json={"review_status": "accepted"}, headers=h)
    admin_client.put(f"/repository/code-links/{ids['helper']}/review", json={"review_status": "rejected"}, headers=h)

    second = _generate(
        admin_client, h, monkeypatch,
        [
            link("user-management", "list_users"),
            link("user-management", "helper"),
            link("user-management", "notify", "src/mailer.py"),
        ],
    )
    assert _status(second) == {
        ("user-management", "src/main.py", "list_users"): "accepted",
        ("user-management", "src/main.py", "helper"): "rejected",
        ("user-management", "src/mailer.py", "notify"): "proposed",
    }
    assert second["carried_over_reviews"] == 2
    latest = admin_client.get("/repository/code-links", headers=h).json()
    assert _status(latest) == _status(second)
    assert latest["carried_over_reviews"] == 2


def test_dropped_links_are_reported_and_logged(admin_client, git_repo, monkeypatch, caplog):
    h = system_headers(admin_client)
    _prepare(admin_client, h, git_repo, monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.code_mapper"):
        body = _generate(
            admin_client, h, monkeypatch,
            [
                link("user-management", "list_users"),
                link("user-management", "invented", "src/missing.py"),
                link("no-such-feature", "helper"),
            ],
        )
    assert body["intelligence_run"]["status"] == "completed"
    assert len(body["links"]) == 1
    assert body["dropped_links"] == [
        {"feature_id": "user-management", "raw_symbol": "src/missing.py:invented", "reason": "symbol not in index"},
        {"feature_id": "no-such-feature", "raw_symbol": "src/main.py:helper", "reason": "unknown feature_id"},
    ]
    assert sum("code-link dropped" in r.message for r in caplog.records) == 2
    assert admin_client.get("/repository/code-links", headers=h).json()["dropped_links"] == body["dropped_links"]


def test_parse_collects_drops_without_failing_the_valid_links():
    from app.code_mapper import _parse_mapping_response
    import json

    dropped = []
    results = _parse_mapping_response(
        json.dumps({"links": [link("f", "a"), link("f", "ghost")]}),
        {"f"},
        {("src/main.py", "a")},
        {},
        dropped,
    )
    assert [r.symbol_qualified_name for r in results] == ["a"]
    assert dropped == [{"feature_id": "f", "raw_symbol": "src/main.py:ghost", "reason": "symbol not in index"}]
