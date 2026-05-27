import pytest


@pytest.fixture(autouse=True)
def _reset_modules():
    import sys
    for mod in ["probe_agent.client", "probe_agent.config"]:
        sys.modules.pop(mod, None)
    yield
    for mod in ["probe_agent.client", "probe_agent.config"]:
        sys.modules.pop(mod, None)


def test_headers_include_api_key_when_set(monkeypatch):
    monkeypatch.setenv("PROBE_API_KEY", "my-secret-key")
    from probe_agent.client import ControlClient

    client = ControlClient(base_url="http://localhost:9999")
    headers = client._headers()
    assert headers["X-Api-Key"] == "my-secret-key"
    assert headers["Content-Type"] == "application/json"


def test_headers_omit_api_key_when_not_set(monkeypatch):
    monkeypatch.delenv("PROBE_API_KEY", raising=False)
    from probe_agent.client import ControlClient

    client = ControlClient(base_url="http://localhost:9999")
    headers = client._headers()
    assert "X-Api-Key" not in headers
    assert headers["Content-Type"] == "application/json"


def test_headers_omit_api_key_when_empty_string(monkeypatch):
    monkeypatch.setenv("PROBE_API_KEY", "")
    from probe_agent.client import ControlClient

    client = ControlClient(base_url="http://localhost:9999")
    headers = client._headers()
    assert "X-Api-Key" not in headers
