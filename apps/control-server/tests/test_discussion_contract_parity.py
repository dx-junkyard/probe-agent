"""Mechanical parity checks for the AI Discussion UI Adapter contracts
(Issue #444, Epic #443 Phase 1).

`docs/ai-discussion-adapter.md` §1.8 is the canonical contract. Historically
a finite vocabulary here could drift between the server `Literal`
(`app/models.py`), the Dashboard union (`src/api/types.ts`), and the shared
JSON Schema (`shared/schemas/assistant_discussion.schema.json`) without any
test noticing -- exactly the "forgot one of N parallel tables" failure mode
`docs/ai-discussion-adapter.md` §1.1 describes for the adapter registry
itself. This file parses the three source files directly (imitating
`test_interview_type_parity.py`'s AST/regex approach) instead of restating
the vocabularies here, so a value present on only one side fails loudly.

It also covers the target_kind REGISTRY set (Python module vs. the
Dashboard's own `DISCUSSION_ADAPTERS` object) and the §1.9 `purpose_need`
fix across its four contracts, so that regresses loudly too.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Dict, Iterable, Set


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_MODELS_PATH = REPO_ROOT / "apps/control-server/app/models.py"
SERVER_JOINT_UNDERSTANDING_PATH = REPO_ROOT / "apps/control-server/app/joint_understanding.py"
DASHBOARD_TYPES_PATH = REPO_ROOT / "apps/dashboard/src/api/types.ts"
DASHBOARD_ADAPTERS_PATH = REPO_ROOT / "apps/dashboard/src/lib/discussion-adapters.ts"
ASSISTANT_DISCUSSION_SCHEMA_PATH = REPO_ROOT / "shared/schemas/assistant_discussion.schema.json"
JOINT_UNDERSTANDING_SCHEMA_PATH = REPO_ROOT / "shared/schemas/joint_understanding.schema.json"

# name -> the JSON Schema $def key carrying the same enum.
ASSISTANT_DISCUSSION_SCHEMA_DEFS = {
    "DiscussionScope": "discussion_scope",
    "DiscussionTargetKind": "discussion_target_kind",
    "DiscussionTargetState": "discussion_target_state",
    "DiscussionCapability": "discussion_capability",
    "DiscussionProposalItemKind": "discussion_proposal_item_kind",
    "DiscussionProposalItemStatus": "discussion_proposal_item_status",
    "DiscussionProposalItemEligibility": "discussion_proposal_item_eligibility",
    "UiDraftState": "ui_draft_state",
}


# --- server-side (Python) parsing --------------------------------------------


def _literal_strings(node: ast.AST) -> Set[str]:
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        if node.value.id == "Literal":
            elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return {
                element.value
                for element in elements
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    return set()


def _server_finite_types(path: Path) -> Dict[str, Set[str]]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    finite: Dict[str, Set[str]] = {}
    for node in module.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name):
                values = _literal_strings(node.value)
                if values:
                    finite[target.id] = values
    return finite


def _server_tuple_values(path: Path, name: str) -> Set[str]:
    """Plain `NAME = ("a", "b", ...)` module-level assignments (not a
    `Literal` alias) -- e.g. `joint_understanding.TRIGGERS`."""
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Tuple):
                return {
                    element.value
                    for element in node.value.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
    raise AssertionError(f"{name} not found as a top-level tuple literal in {path}")


# --- Dashboard-side (TypeScript) parsing -------------------------------------


def _ts_type_expressions(source: str) -> Dict[str, str]:
    return {
        match.group(1): match.group(2).strip()
        for match in re.finditer(
            r"\bexport\s+type\s+([A-Za-z_]\w*)\s*=\s*(.*?);",
            source,
            flags=re.DOTALL,
        )
    }


def _ts_const_arrays(source: str) -> Dict[str, Set[str]]:
    arrays: Dict[str, Set[str]] = {}
    for match in re.finditer(
        r"\bexport\s+const\s+([A-Za-z_]\w*)\s*=\s*\[(.*?)\]\s*as\s+const\s*;",
        source,
        flags=re.DOTALL,
    ):
        arrays[match.group(1)] = set(re.findall(r'["\']([^"\']+)["\']', match.group(2)))
    return arrays


def _ts_finite_values(
    name: str,
    expressions: Dict[str, str],
    arrays: Dict[str, Set[str]],
    seen: Iterable[str] = (),
) -> Set[str]:
    if name in seen:
        raise AssertionError(f"cyclic TypeScript type alias at {name}")
    expression = expressions[name]
    # Strip `//` line comments before extracting string literals -- a
    # comment mentioning an unrelated quoted word must not be read as a
    # union member.
    stripped = "\n".join(line.split("//", 1)[0] for line in expression.splitlines())
    values = set(re.findall(r'["\']([^"\']+)["\']', stripped))
    array_ref = re.fullmatch(r"typeof\s+([A-Za-z_]\w*)\[number\]", stripped.strip())
    if array_ref:
        return set(arrays[array_ref.group(1)])
    for token in re.findall(r"\b[A-Za-z_]\w*\b", stripped):
        if token in expressions and token != name:
            values.update(_ts_finite_values(token, expressions, arrays, (*seen, name)))
    return values


def _dashboard_type_values(name: str) -> Set[str]:
    source = DASHBOARD_TYPES_PATH.read_text(encoding="utf-8")
    expressions = _ts_type_expressions(source)
    arrays = _ts_const_arrays(source)
    assert name in expressions, f"{name} is not an exported TypeScript type in {DASHBOARD_TYPES_PATH}"
    return _ts_finite_values(name, expressions, arrays)


def _dashboard_adapter_target_kinds() -> Set[str]:
    """The Dashboard `DISCUSSION_ADAPTERS` registry's own key set -- parsed
    from the object literal rather than imported, so this test does not
    depend on a TypeScript runtime."""
    source = DASHBOARD_ADAPTERS_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"export const DISCUSSION_ADAPTERS\s*:\s*Record<[^>]*>\s*=\s*\{(.*?)\n\};",
        source,
        flags=re.DOTALL,
    )
    assert match, "DISCUSSION_ADAPTERS object literal not found in discussion-adapters.ts"
    body = match.group(1)
    return set(re.findall(r"^\s*([A-Za-z_]\w*)\s*:\s*[A-Za-z_]\w*Adapter\s*,?\s*$", body, flags=re.MULTILINE))


def _dashboard_adapter_screen_ids() -> Dict[str, Set[str]]:
    """Each Dashboard adapter's declared `screenIds`, parsed from the source
    rather than imported. Keyed by the adapter's own `targetKind` literal, so
    a renamed const cannot silently drop a kind from the comparison."""
    source = DASHBOARD_ADAPTERS_PATH.read_text(encoding="utf-8")
    out: Dict[str, Set[str]] = {}
    for block in re.finditer(
        r"targetKind:\s*\"(?P<kind>[\w]+)\"(?P<body>.*?)\n\};",
        source,
        flags=re.DOTALL,
    ):
        screens = re.search(
            r"screenIds:\s*(?P<value>\[[^\]]*\]|Object\.keys\(\w+\)|\w+)",
            block.group("body"),
        )
        assert screens is not None, f"no screenIds for {block.group('kind')!r}"
        out[block.group("kind")] = _resolve_screen_ids_expression(source, screens.group("value"))
    return out


def _resolve_screen_ids_expression(source: str, expression: str) -> Set[str]:
    """Resolve a `screenIds` right-hand side to its literal set.

    Three forms appear today: an inline array, a shared `const X = [...]`,
    and `Object.keys(SCREEN_PATH)`. Resolving them here rather than requiring
    inline arrays keeps the parity check from dictating how the source is
    written -- and asserting on an unrecognised form (instead of returning an
    empty set) is what stops a future fourth form from silently passing this
    test with nothing compared."""
    expression = expression.strip()
    inline = re.match(r"^\[(?P<ids>[^\]]*)\]", expression)
    if inline is not None:
        return set(re.findall(r"\"([^\"]+)\"", inline.group("ids")))
    keys_of = re.match(r"^Object\.keys\((?P<name>\w+)\)$", expression)
    if keys_of is not None:
        record = re.search(
            rf"const {keys_of.group('name')}\s*:[^=]*=\s*\{{(?P<body>.*?)\n\}};",
            source,
            flags=re.DOTALL,
        )
        assert record is not None, f"const {keys_of.group('name')} not found"
        return set(re.findall(r"^\s*\"?([\w-]+)\"?\s*:", record.group("body"), flags=re.MULTILINE))
    named = re.match(r"^(?P<name>\w+)$", expression)
    if named is not None:
        const = re.search(
            rf"const {named.group('name')}\s*=\s*\[(?P<ids>[^\]]*)\]",
            source,
        )
        assert const is not None, f"const {named.group('name')} not found"
        return set(re.findall(r"\"([^\"]+)\"", const.group("ids")))
    raise AssertionError(f"unrecognised screenIds expression: {expression!r}")


# --- shared JSON Schema parsing -----------------------------------------------


def _schema_defs(path: Path) -> Dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["$defs"]


def _schema_enum_values(path: Path, def_name: str) -> Set[str]:
    defs = _schema_defs(path)
    assert def_name in defs, f"$defs.{def_name} not found in {path}"
    enum = defs[def_name].get("enum")
    assert enum is not None, f"$defs.{def_name} has no 'enum' in {path}"
    return set(enum)


# --- §1.8: server Literal <-> Dashboard union <-> shared JSON Schema --------


def test_discussion_vocabularies_match_across_server_dashboard_and_schema():
    server_types = _server_finite_types(SERVER_MODELS_PATH)
    mismatches = []
    for name, def_name in ASSISTANT_DISCUSSION_SCHEMA_DEFS.items():
        assert name in server_types, f"{name} is not a server Literal alias in models.py"
        server_values = server_types[name]
        ts_values = _dashboard_type_values(name)
        schema_values = _schema_enum_values(ASSISTANT_DISCUSSION_SCHEMA_PATH, def_name)
        if not (server_values == ts_values == schema_values):
            mismatches.append(
                f"{name}: server={sorted(server_values)}, ts={sorted(ts_values)}, "
                f"schema={sorted(schema_values)}"
            )
    assert not mismatches, "\n".join(mismatches)


# --- registry target_kind set <-> DiscussionTargetKind <-> Dashboard registry -


def test_registry_target_kind_set_matches_discussion_target_kind_literal():
    from app import discussion_adapters

    server_types = _server_finite_types(SERVER_MODELS_PATH)
    assert set(discussion_adapters.DISCUSSION_TARGET_KINDS) == server_types["DiscussionTargetKind"]


def test_dashboard_adapter_registry_matches_server_registry():
    from app import discussion_adapters

    dashboard_kinds = _dashboard_adapter_target_kinds()
    assert dashboard_kinds, "failed to parse any target_kind out of discussion-adapters.ts"
    assert dashboard_kinds == set(discussion_adapters.DISCUSSION_TARGET_KINDS)


# --- server adapter fields/relations <-> Dashboard adapter's declared set ---


def test_every_kind_with_server_proposable_fields_or_relations_has_a_dashboard_adapter():
    """A `target_kind` the server can propose a field/relation change for
    must also be a `target_kind` the Dashboard registry knows how to route a
    thread to -- otherwise a generated proposal item could never reach a
    screen that can show it. In Phase 1 the Dashboard adapter carries no
    per-field allowlist of its own (prefill does not exist until #446), so
    the checkable claim is the target_kind universe, not a field list."""
    from app import discussion_adapters

    dashboard_kinds = _dashboard_adapter_target_kinds()
    proposable_kinds = {
        kind
        for kind, adapter in discussion_adapters.DISCUSSION_ADAPTERS.items()
        if adapter.fields or adapter.relations
    }
    assert proposable_kinds, "expected at least one proposable target_kind"
    assert proposable_kinds <= dashboard_kinds


def _dashboard_adapter_forms() -> Dict[str, Dict[str, Set[str]]]:
    """Each Dashboard adapter's declared `forms`: `form_id -> field set`,
    parsed from the source (never imported -- no TypeScript runtime here).
    Relies on the same fixed member order every adapter object literal in
    this file uses (`forms`, then `invalidateKeys`, then `deepLink`)."""
    source = DASHBOARD_ADAPTERS_PATH.read_text(encoding="utf-8")
    out: Dict[str, Dict[str, Set[str]]] = {}
    for block in re.finditer(
        r"targetKind:\s*\"(?P<kind>[\w]+)\"(?P<body>.*?)\n\};",
        source,
        flags=re.DOTALL,
    ):
        forms: Dict[str, Set[str]] = {}
        forms_match = re.search(
            r"forms:\s*\[(?P<body>.*?)\]\s*,\s*\n\s*invalidateKeys",
            block.group("body"),
            flags=re.DOTALL,
        )
        if forms_match:
            for entry in re.finditer(
                r"formId:\s*\"(?P<form_id>[^\"]+)\"\s*,\s*fields:\s*\[(?P<fields>[^\]]*)\]",
                forms_match.group("body"),
            ):
                forms[entry.group("form_id")] = set(
                    re.findall(r"\"([^\"]+)\"", entry.group("fields"))
                )
        out[block.group("kind")] = forms
    return out


def test_ui_draft_forms_match_between_server_and_dashboard():
    """Issue #445 (Epic #443 Phase 2): the server's `ui_draft_forms` field
    allowlist and the Dashboard's declared `forms` bindings must agree --
    the same reasoning as `test_adapter_screen_ids_match_between_server_and_
    dashboard` above. If they disagree, the client either sends fields the
    server refuses (422 `ui_draft_field_unregistered`) or withholds fields
    the server would have accepted, and neither side's own tests can see it
    because each only exercises its own declared set."""
    from app import discussion_adapters

    dashboard_forms = _dashboard_adapter_forms()
    assert dashboard_forms, "failed to parse any forms out of discussion-adapters.ts"
    server_forms = {
        kind: {spec.form_id: set(spec.fields) for spec in adapter.ui_draft_forms}
        for kind, adapter in discussion_adapters.DISCUSSION_ADAPTERS.items()
    }
    assert any(server_forms.values()), "expected at least one target_kind with ui_draft_forms"
    assert dashboard_forms == server_forms


def test_adapter_screen_ids_match_between_server_and_dashboard():
    """`screen_ids` is a GATE on the server (§1.7's
    `discussion_target_screen_mismatch`) and a routing fact on the Dashboard.
    If the two disagree, the client offers a thread the server refuses, or
    hides one the server would accept -- and neither side's own tests can see
    it. This is the drift the parity suite exists to catch, so it is checked
    directly rather than left to the target_kind universe."""
    from app import discussion_adapters

    dashboard_screens = _dashboard_adapter_screen_ids()
    assert dashboard_screens, "failed to parse any screenIds out of discussion-adapters.ts"
    server_screens = {
        kind: set(adapter.screen_ids)
        for kind, adapter in discussion_adapters.DISCUSSION_ADAPTERS.items()
    }
    assert dashboard_screens == server_screens


# --- §1.9: purpose_need across all four contracts ---------------------------


def test_purpose_need_widened_consistently_across_all_four_contracts():
    server_types = _server_finite_types(SERVER_MODELS_PATH)
    server_origin_kind = server_types["JointUnderstandingOriginKind"]
    server_trigger = server_types["JointUnderstandingTrigger"]

    joint_understanding_triggers = _server_tuple_values(
        SERVER_JOINT_UNDERSTANDING_PATH, "TRIGGERS"
    )

    ts_origin_kind = _dashboard_type_values("JointUnderstandingOriginKind")
    ts_trigger = _dashboard_type_values("JointUnderstandingTrigger")

    schema_origin_kind = set(
        _schema_defs(JOINT_UNDERSTANDING_SCHEMA_PATH)["session"]["properties"]["origin_kind"]["enum"]
    )
    schema_trigger = set(
        _schema_defs(JOINT_UNDERSTANDING_SCHEMA_PATH)["session"]["properties"]["trigger"]["enum"]
    )

    assert "purpose_need" in server_origin_kind
    assert "purpose_need" in server_trigger

    mismatches = []
    if server_origin_kind != ts_origin_kind:
        mismatches.append(
            f"JointUnderstandingOriginKind: server={sorted(server_origin_kind)} "
            f"ts={sorted(ts_origin_kind)}"
        )
    if server_origin_kind != schema_origin_kind:
        mismatches.append(
            f"origin_kind schema: server={sorted(server_origin_kind)} "
            f"schema={sorted(schema_origin_kind)}"
        )
    if server_trigger != ts_trigger:
        mismatches.append(
            f"JointUnderstandingTrigger: server={sorted(server_trigger)} ts={sorted(ts_trigger)}"
        )
    if server_trigger != schema_trigger:
        mismatches.append(
            f"trigger schema: server={sorted(server_trigger)} schema={sorted(schema_trigger)}"
        )
    # `joint_understanding.TRIGGERS` is a plain tuple (not itself a Literal
    # alias) that must never lag `JointUnderstandingTrigger` -- this is the
    # exact gap #444 §1.9 closes.
    if server_trigger != joint_understanding_triggers:
        mismatches.append(
            f"joint_understanding.TRIGGERS: server_literal={sorted(server_trigger)} "
            f"TRIGGERS={sorted(joint_understanding_triggers)}"
        )
    assert not mismatches, "\n".join(mismatches)
