"""Issue #224: docker-compose.prod.yml GitHub App secret mount.

Parses the real `docker-compose.prod.yml` at the repo root (resolved
relative to this test file, not the CWD) and asserts the Compose secret
wiring the runbook depends on. Also guards Issue #225's invariant that
`CONTROL_API_KEYS` is never set for `control-server` in the prod compose
file, since both invariants live in the same file.
"""

import json
import os

import yaml

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_COMPOSE_PATH = os.path.join(_REPO_ROOT, "docker-compose.prod.yml")


def _load_compose():
    assert os.path.isfile(_COMPOSE_PATH), f"expected {_COMPOSE_PATH} to exist"
    with open(_COMPOSE_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_control_server_mounts_github_app_private_key_secret():
    compose = _load_compose()
    control_server = compose["services"]["control-server"]
    assert "github_app_private_key" in control_server.get("secrets", [])


def test_control_server_private_key_path_is_fixed_secret_path():
    compose = _load_compose()
    control_server = compose["services"]["control-server"]
    env = control_server["environment"]
    assert env["GITHUB_APP_PRIVATE_KEY_PATH"] == "/run/secrets/github_app_private_key"


def test_top_level_secret_uses_host_path_env_var():
    compose = _load_compose()
    secret = compose["secrets"]["github_app_private_key"]
    assert secret["file"] == "${GITHUB_APP_PRIVATE_KEY_HOST_PATH:-/dev/null}"


def test_control_api_keys_absent_from_control_server_environment():
    # Guards Issue #225's invariant too: legacy shared keys must never be
    # configured for the production control-server service.
    compose = _load_compose()
    control_server = compose["services"]["control-server"]
    env = control_server["environment"]
    assert "CONTROL_API_KEYS" not in env


def test_github_publish_enabled_env_present():
    compose = _load_compose()
    control_server = compose["services"]["control-server"]
    env = control_server["environment"]
    assert "GITHUB_PUBLISH_ENABLED" in env


def test_production_resource_limits_are_explicitly_required():
    env = _load_compose()["services"]["control-server"]["environment"]
    for name in (
        "CONTROL_TRACE_RATE_LIMIT_PER_SECOND",
        "CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE",
        "CONTROL_LLM_DAILY_EXECUTION_LIMIT",
        "CONTROL_TRACE_MAX_ROWS_PER_SYSTEM",
        "CONTROL_TRACE_MAX_BYTES_PER_SYSTEM",
    ):
        assert env[name] == f"${{{name}:?Set {name}}}"


def test_execution_worker_is_secretless_and_hardened():
    compose = _load_compose()
    worker = compose["services"]["execution-worker"]
    assert worker["network_mode"] == "none"
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in worker["security_opt"]
    assert "seccomp:unconfined" in worker["security_opt"]
    assert worker["user"] == "65534:65534"
    assert worker["pids_limit"] == 128
    assert worker["mem_limit"] == "512m"
    assert worker["cpus"] == "1.0"
    assert worker.get("secrets", []) == []
    assert "docker.sock" not in json.dumps(worker)
    assert set(worker["environment"]) == {
        "PROBE_EXECUTION_SPOOL_ROOT",
        "PROBE_EXECUTION_WORKSPACE_ROOT",
        "PROBE_EXECUTION_WORKER_POLL_SECONDS",
        "PROBE_EXECUTION_MAX_OUTPUT_BYTES",
    }


def test_execution_worker_mounts_only_spool_and_workspace_shared_with_control():
    compose = _load_compose()
    worker = compose["services"]["execution-worker"]
    control = compose["services"]["control-server"]
    worker_volumes = set(worker["volumes"])
    assert worker_volumes == {
        "execution-spool:/execution-spool",
        "execution-workspaces:/execution-workspaces",
    }
    assert worker_volumes.issubset(set(control["volumes"]))
    assert control["environment"]["PROBE_EXECUTION_BACKEND"] == "worker"
