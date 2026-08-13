"""Mechanical parity checks for the Interview server/TypeScript contracts.

The Control Server Pydantic models are the wire-contract source of truth, but
the Dashboard keeps hand-written TypeScript interfaces for the same responses.
Historically tests on each side repeated their own field lists, so adding a
field or finite literal to only one side could leave both independent suites
green.  These tests parse the two source files directly and compare the
contracts without duplicating their fields or enum values here.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_MODELS_PATH = REPO_ROOT / "apps/control-server/app/models.py"
DASHBOARD_TYPES_PATH = REPO_ROOT / "apps/dashboard/src/api/types.ts"

# Only names are mapped here. Fields and literal members always come directly
# from the two contract sources. InterviewQaEvidenceRef predates the server's
# clearer ``Out`` suffix and is the sole intentional naming difference.
SERVER_TO_TS_INTERFACE_ALIASES = {
    "InterviewQaEvidenceRefOut": "InterviewQaEvidenceRef",
}

INTERVIEW_CONTRACT_PREFIXES = (
    "Alignment",
    "ChangeSet",
    "IntelligenceRunEvidence",
    "Interview",
    "QuestionHandoff",
    "Refresh",
    "RuntimeFact",
    "RuntimeObservation",
    "RuntimeReality",
    "RuntimeTrace",
    "SuggestedObservation",
    "Understanding",
)

# Finite sets that drive Interview decisions and UI branching. The test
# resolves TypeScript aliases such as
# ``AlignmentUserDecisionAction = AlignmentDecisionAction | ...`` and
# ``typeof INTERVIEW_INTENT_FIELDS[number]`` recursively.
FINITE_TYPE_NAMES = (
    "AlignmentConfidence",
    "AlignmentDecisionAction",
    "AlignmentItemStatus",
    "AlignmentReasonCode",
    "AlignmentReviewCategory",
    "AlignmentRiskFlag",
    "AlignmentState",
    "AlignmentSubjectState",
    "InquiryPremiseEvaluation",
    "InquiryPremiseTrackingState",
    "AlignmentUserDecisionAction",
    "HandoffOriginKind",
    "HandoffPriority",
    "HandoffStatus",
    "ChangeResolutionState",
    "ChangeSetStatus",
    "ChangeTargetKind",
    "InterviewInquiryMessageRole",
    "InterviewInquiryOriginKind",
    "InterviewInquiryStatus",
    "InterviewMetricCategory",
    "InterviewMetricEventType",
    "InterviewMetricKey",
    "InterviewMetricStatus",
    "InterviewMetricTargetKind",
    "InterviewMetricUnit",
    "InterviewDecisionAction",
    "InterviewIntentField",
    "InterviewIntentOrigin",
    "InterviewIntentStatus",
    "InterviewIntentUserStatus",
    "InterviewQaCategory",
    "InterviewQaSource",
    "InterviewQaStatus",
    "InterviewStage",
    "KnowledgeArea",
    "RefreshJobStatus",
    "RefreshTriggerKind",
    "RuntimeObservationProposalStatus",
    "RuntimeCheckState",
    "RuntimeFactFreshness",
    # Issue #351: the Understanding Brief / Decision Readiness axes. These are
    # exactly the kind of set that drifts silently -- the Dashboard does not
    # branch on `change_kind` today, so a missing member type-checks and only
    # becomes wrong when someone later writes an exhaustive switch.
    "UnderstandingChangeKind",
    "UnderstandingClaimKind",
    "UnderstandingConfirmationState",
    "UnderstandingProvenanceKind",
    "UnderstandingReadinessSeverity",
    "UnderstandingReadinessState",
    # Issue #380-#384: the Overview cockpit's vocabularies. Same risk as the
    # Brief's above -- the Dashboard branches on severity and status today,
    # but a new finding kind or action key would type-check on one side alone.
    "OverviewActionKey",
    "OverviewActionState",
    "OverviewFindingKind",
    "OverviewFindingProvenance",
    "OverviewFindingSeverity",
    "OverviewFindingStatus",
    "OverviewFindingsState",
    "OverviewLoopStage",
    "OverviewLoopStageStatus",
    "OverviewSection",
)


def _server_module() -> ast.Module:
    return ast.parse(SERVER_MODELS_PATH.read_text(encoding="utf-8"))


def _server_classes() -> Dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in _server_module().body
        if isinstance(node, ast.ClassDef)
    }


@lru_cache(maxsize=None)
def _server_model_fields(name: str) -> frozenset[str]:
    classes = _server_classes()
    node = classes[name]
    fields: Set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in classes:
            fields.update(_server_model_fields(base.id))
    fields.update(
        stmt.target.id
        for stmt in node.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    )
    return frozenset(fields)


def _matching_brace(source: str, opening: int) -> int:
    depth = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    i = opening
    while i < len(source):
        char = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            i += 1
            continue
        if char == "/" and nxt == "/":
            line_comment = True
            i += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            i += 2
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError("unterminated TypeScript interface body")


def _ts_interfaces(source: str) -> Dict[str, Tuple[Tuple[str, ...], str]]:
    interfaces: Dict[str, Tuple[Tuple[str, ...], str]] = {}
    pattern = re.compile(
        r"\bexport\s+interface\s+([A-Za-z_]\w*)"
        r"(?:\s+extends\s+([^{]+?))?\s*\{"
    )
    for match in pattern.finditer(source):
        opening = match.end() - 1
        closing = _matching_brace(source, opening)
        parents = tuple(
            part.strip()
            for part in (match.group(2) or "").split(",")
            if part.strip()
        )
        interfaces[match.group(1)] = (parents, source[opening + 1 : closing])
    return interfaces


def _strip_ts_comments(line: str) -> str:
    return line.split("//", 1)[0]


def _ts_own_fields(body: str) -> Set[str]:
    fields: Set[str] = set()
    nested_depth = 0
    field_pattern = re.compile(r"^\s*(?:readonly\s+)?([A-Za-z_]\w*)\??\s*:")
    for raw_line in body.splitlines():
        line = _strip_ts_comments(raw_line)
        if nested_depth == 0:
            match = field_pattern.match(line)
            if match:
                fields.add(match.group(1))
        # Interface field object types may span lines. Counting delimiters is
        # sufficient here because comments were removed and contract property
        # names themselves are required to start at top level.
        nested_depth += line.count("{") - line.count("}")
    return fields


def _ts_interface_fields(
    name: str,
    interfaces: Dict[str, Tuple[Tuple[str, ...], str]],
    seen: Iterable[str] = (),
) -> frozenset[str]:
    if name in seen:
        raise AssertionError(f"cyclic TypeScript interface inheritance at {name}")
    parents, body = interfaces[name]
    fields = _ts_own_fields(body)
    for parent in parents:
        if parent in interfaces:
            fields.update(_ts_interface_fields(parent, interfaces, (*seen, name)))
    return frozenset(fields)


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


def _server_finite_types() -> Dict[str, Set[str]]:
    finite: Dict[str, Set[str]] = {}
    for node in _server_module().body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name):
                values = _literal_strings(node.value)
                if values:
                    finite[target.id] = values
    return finite


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
    values = set(re.findall(r'["\']([^"\']+)["\']', expression))
    array_ref = re.fullmatch(r"typeof\s+([A-Za-z_]\w*)\[number\]", expression)
    if array_ref:
        return set(arrays[array_ref.group(1)])
    for token in re.findall(r"\b[A-Za-z_]\w*\b", expression):
        if token in expressions and token != name:
            values.update(
                _ts_finite_values(token, expressions, arrays, (*seen, name))
            )
    return values


def test_interview_response_fields_match_dashboard_types() -> None:
    """Every shared Interview response interface has the server's fields."""

    source = DASHBOARD_TYPES_PATH.read_text(encoding="utf-8")
    interfaces = _ts_interfaces(source)
    server_classes = _server_classes()
    required_outputs = {
        name
        for name in server_classes
        if name.startswith(INTERVIEW_CONTRACT_PREFIXES) and name.endswith("Out")
    }
    missing_interfaces = sorted(
        name
        for name in required_outputs
        if SERVER_TO_TS_INTERFACE_ALIASES.get(name, name) not in interfaces
    )
    assert not missing_interfaces, (
        "Dashboard interfaces missing for server Interview responses: "
        f"{missing_interfaces}"
    )

    pairs: Dict[str, str] = {}
    for server_name in server_classes:
        if not server_name.startswith(INTERVIEW_CONTRACT_PREFIXES):
            continue
        ts_name = SERVER_TO_TS_INTERFACE_ALIASES.get(server_name, server_name)
        if ts_name in interfaces:
            pairs[server_name] = ts_name

    # A broad floor prevents an accidental parser regression from silently
    # turning this into a tiny or empty check.
    assert len(pairs) >= 45

    mismatches = []
    for server_name, ts_name in sorted(pairs.items()):
        server_fields = _server_model_fields(server_name)
        ts_fields = _ts_interface_fields(ts_name, interfaces)
        if server_fields != ts_fields:
            mismatches.append(
                f"{server_name}/{ts_name}: "
                f"server_only={sorted(server_fields - ts_fields)}, "
                f"ts_only={sorted(ts_fields - server_fields)}"
            )
    assert not mismatches, "\n".join(mismatches)


def test_interview_finite_types_match_dashboard_types() -> None:
    """Decision/status literals cannot drift between Python and TypeScript."""

    source = DASHBOARD_TYPES_PATH.read_text(encoding="utf-8")
    server_types = _server_finite_types()
    ts_expressions = _ts_type_expressions(source)
    ts_arrays = _ts_const_arrays(source)

    mismatches = []
    for name in FINITE_TYPE_NAMES:
        assert name in server_types, f"{name} is not a server Literal alias"
        assert name in ts_expressions, f"{name} is not an exported TypeScript type"
        ts_values = _ts_finite_values(name, ts_expressions, ts_arrays)
        if server_types[name] != ts_values:
            mismatches.append(
                f"{name}: server_only={sorted(server_types[name] - ts_values)}, "
                f"ts_only={sorted(ts_values - server_types[name])}"
            )
    assert not mismatches, "\n".join(mismatches)
