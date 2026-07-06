import sys
from typing import List

import pytest

from probe_agent import projection as P
from probe_agent.projection import ProjectionError, compile_spec, extract


# --- spec validation (fail-closed) -----------------------------------------

def test_invalid_spec_rejected_at_registration():
    with pytest.raises(ProjectionError):
        compile_spec({"name": "x", "output": {"metrics": {"m": {"op": "sum", "path": "$.a"}}}})
    with pytest.raises(ProjectionError):
        compile_spec({"output": {}})  # missing name
    with pytest.raises(ProjectionError):
        compile_spec({"name": "x", "output": {"fields": {"f": "not-a-path"}}})
    with pytest.raises(ProjectionError):
        compile_spec({"name": "x", "bogus": 1})  # unknown top-level key


def test_valid_spec_compiles():
    spec = compile_spec({
        "name": "ok",
        "output": {"fields": {"id": "$.order.id"}},
    })
    assert spec.name == "ok"


# --- extraction ------------------------------------------------------------

def _big_payload():
    return {
        "order": {"id": "o-1", "secret": "sk-XYZ"},
        "items": [{"sku": f"s{i}", "qty": i} for i in range(50)],
        "tags": ["a", "b", "c"],
    }


def test_fields_metrics_samples_extracted_no_raw():
    spec = compile_spec({
        "name": "orders",
        "output": {
            "fields": {"order_id": "$.order.id", "skus": "$.items[*].sku"},
            "metrics": {"n_items": {"op": "count", "path": "$.items[*]"},
                        "n_tags": {"op": "len", "path": "$.tags"},
                        "has_order": {"op": "exists", "path": "$.order"}},
            "samples": {"first_skus": {"path": "$.items[*].sku", "limit": 3}},
        },
    })
    payloads, entities = extract(spec, output_root=_big_payload())
    assert len(payloads) == 1
    p = payloads[0]
    assert p["phase"] == "output"
    assert p["fields"]["order_id"] == "o-1"
    assert p["metrics"]["n_items"] == 50
    assert p["metrics"]["n_tags"] == 3
    assert p["metrics"]["has_order"] is True
    assert p["samples"]["first_skus"] == ["s0", "s1", "s2"]
    assert p["data_hash"]
    # No raw payload leaked (secret not requested).
    assert "sk-XYZ" not in str(p)


def test_wildcard_field_returns_list():
    spec = compile_spec({"name": "w", "output": {"fields": {"skus": "$.items[*].sku"}}})
    p = extract(spec, output_root=_big_payload())[0][0]
    assert p["fields"]["skus"][:2] == ["s0", "s1"]


def test_redaction_replaces_value_before_save():
    spec = compile_spec({
        "name": "r",
        "output": {"fields": {"secret": "$.order.secret", "id": "$.order.id"}},
        "redact": ["$.order.secret"],
    })
    p = extract(spec, output_root=_big_payload())[0][0]
    assert p["fields"]["secret"] == P._REDACTED
    assert p["fields"]["id"] == "o-1"
    assert "sk-XYZ" not in str(p)


def test_sample_limit_capped_by_env(monkeypatch):
    monkeypatch.setenv("PROBE_PROJECTION_MAX_SAMPLES", "2")
    spec = compile_spec({"name": "s", "output": {"samples": {"skus": {"path": "$.items[*].sku"}}}})
    p = extract(spec, output_root=_big_payload())[0][0]
    assert len(p["samples"]["skus"]) == 2


def test_truncation_marker_and_hash(monkeypatch):
    monkeypatch.setenv("PROBE_PROJECTION_MAX_BYTES", "80")
    spec = compile_spec({
        "name": "big",
        "output": {"fields": {"all_skus": "$.items[*].sku", "id": "$.order.id"}},
    })
    p = extract(spec, output_root=_big_payload())[0][0]
    assert p["truncated"] is True
    assert p["data_hash"]  # hash still present over the truncated data


def test_max_fields_limit(monkeypatch):
    monkeypatch.setenv("PROBE_PROJECTION_MAX_FIELDS", "1")
    spec = compile_spec({
        "name": "mf",
        "output": {"fields": {"a": "$.order.id", "b": "$.tags"}},
    })
    p = extract(spec, output_root=_big_payload())[0][0]
    assert len(p["fields"]) == 1
    assert p["truncated"] is True


def test_runtime_extraction_error_is_non_fatal():
    class Boom:
        @property
        def x(self):
            raise RuntimeError("nope")

    spec = compile_spec({"name": "e", "output": {"fields": {"v": "$.x"}}})
    # A broken property must not raise; field resolves to None (skipped).
    payloads, _ = extract(spec, output_root=Boom())
    assert payloads[0]["fields"]["v"] is None


def test_unknown_operator_rejected():
    with pytest.raises(ProjectionError):
        compile_spec({"name": "x", "output": {"metrics": {"m": {"op": "avg", "path": "$.a"}}}})


def test_id_path_entity_extraction():
    spec = compile_spec({
        "name": "ent",
        "entities": [
            {"type": "order", "id_path": "$.order.id", "role": "source"},
            {"type": "sku", "id_path": "$.items[*].sku", "role": "related"},
        ],
    })
    _, entities = extract(spec, output_root=_big_payload())
    assert {"type": "order", "id": "o-1", "role": "source"} in entities
    assert {"type": "sku", "id": "s0", "role": "related"} in entities


def test_input_and_output_sections():
    spec = compile_spec({
        "name": "io",
        "input": {"fields": {"first_arg": "$.args[0]"}},
        "output": {"fields": {"result": "$.value"}},
    })
    payloads, _ = extract(
        spec,
        input_root={"args": [42], "kwargs": {}},
        output_root={"value": "done"},
    )
    phases = {p["phase"]: p for p in payloads}
    assert phases["input"]["fields"]["first_arg"] == 42
    assert phases["output"]["fields"]["result"] == "done"


# --- decorator integration: id_path entities feed lineage ------------------

@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setenv("PROBE_ENABLED", "true")
    monkeypatch.setenv("PROBE_DEFAULT_MODE", "trace")
    monkeypatch.setenv("PROBE_POLICY_TTL", "0.0")
    for mod in [
        "probe_agent.decorator", "probe_agent.policy", "probe_agent.client",
        "probe_agent.config", "probe_agent.context", "probe_agent.projection",
        "probe_agent",
    ]:
        sys.modules.pop(mod, None)
    import probe_agent  # noqa: F401
    from probe_agent import decorator as decorator_mod
    from probe_agent.policy import PolicyCache

    sent: List[dict] = []

    class FakeClient:
        def send_trace(self, t):
            sent.append(t)

        def send_shadow_result(self, s):
            pass

        def get_policy(self, _cid):
            return {"mode": "trace"}

    fake = FakeClient()
    decorator_mod._client = fake
    decorator_mod._policy_cache = PolicyCache(client=fake, ttl=0.0)
    decorator_mod._candidates.clear()
    decorator_mod._projections.clear()
    return {"probe_agent": probe_agent, "decorator_mod": decorator_mod, "traces": sent}


def test_projection_attached_to_trace_and_entities(sdk):
    probe = sdk["decorator_mod"].probe

    @probe(component_id="orderc", projection={
        "name": "orders",
        "output": {"fields": {"id": "$.order_id"}, "metrics": {"n": {"op": "count", "path": "$.items[*]"}}},
        "entities": [{"type": "order", "id_path": "$.order_id", "role": "source"}],
    })
    def handle(_):
        return {"order_id": "o-42", "items": [1, 2, 3]}

    handle("x")
    t = sdk["traces"][0]
    proj = t["projections"][0]
    assert proj["fields"]["id"] == "o-42"
    assert proj["metrics"]["n"] == 3
    # id_path entity flows into Phase 1 lineage entities.
    assert {"type": "order", "id": "o-42", "role": "source"} in t["entities"]


def test_invalid_projection_fails_closed_at_decoration(sdk):
    probe = sdk["decorator_mod"].probe
    # Use the reloaded module's ProjectionError (fixture reloads the package).
    reloaded_error = sdk["probe_agent"].ProjectionError
    with pytest.raises(reloaded_error):
        @probe(component_id="bad", projection={"name": "b", "output": {"fields": {"f": "bad"}}})
        def f(_):
            return 1
