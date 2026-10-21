

def test_configured_wrapper_decorator_carries_the_component_id():
    """A repository that wraps the SDK (observe.observed("x")) so it runs
    without it was invisible as instrumentation: component_id stayed empty, so
    nothing linked its code to the traces it sends (System 7)."""
    from app.code_indexer import index_snapshot_files

    yml = b"instrumentation:\n  decorators:\n    - name: observe.observed\n      component_arg: 0\n"
    src = (
        b"from .observe import observed\n\n"
        b"@observed(\"thread-summary-agent\")\n"
        b"def summarize(title, posts):\n    return {}\n\n"
        b"@probe(component_id=\"legacy\")\n"
        b"def legacy():\n    pass\n\n"
        b"@other(\"not-instrumentation\")\n"
        b"def plain():\n    pass\n"
    )
    by_name = {s.qualified_name: s.component_id for s in
               index_snapshot_files([("probe-agent.yml", yml), ("app/agents/x.py", src)]).symbols}
    assert by_name["summarize"] == "thread-summary-agent"
    assert by_name["legacy"] == "legacy"
    assert by_name["plain"] is None
    # Without the declaration only the built-in @probe is recognised.
    plain_index = {s.qualified_name: s.component_id for s in index_snapshot_files([("app/agents/x.py", src)]).symbols}
    assert plain_index["summarize"] is None and plain_index["legacy"] == "legacy"


def test_broken_instrumentation_config_falls_back_to_builtin():
    from app.code_indexer import BUILTIN_INSTRUMENTATION, instrumentation_from_config

    assert instrumentation_from_config(":\n- [") == BUILTIN_INSTRUMENTATION
    assert instrumentation_from_config("instrumentation: 3") == BUILTIN_INSTRUMENTATION
