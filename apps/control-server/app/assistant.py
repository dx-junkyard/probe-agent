"""Per-screen assistant: screen contexts and grounded Q&A (Issue #102).

Answers "what is this screen for / why is this check failing / what should I
set" questions from three deterministic sources only:

- the static screen-context registry below,
- the static settings metadata (`settings_metadata.py`),
- the current deterministic diagnostics report (`system_diagnostics.py`).

When a real LLM provider is configured, the question plus this limited
context pack is sent through the provider-neutral `llm.py` adapter and the
strict-JSON response is validated structurally; citations or navigation
targets outside the supplied pack are dropped. When the provider is `mock`,
missing, or the call/validation fails, a rule-based fallback composes an
answer verbatim from the same sources and is visibly marked
`used_fallback: true`. The fallback never interprets free text beyond
finite-set matching against known setting keys, check ids/titles, and
pipeline step ids (Principle 6); it never guesses.

probe-agent:
  role: Screen-context registry and grounded assistant answer engine
  capability: system-configuration-help
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: orchestration
  state_effects: [database-read, external-api]
  probe_value: Verify answers stay grounded in screen context, settings metadata, and diagnostics, and that LLM failure switches to a visibly marked deterministic fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .llm import LLMClient, LLMConfig, LLMError, PROVIDER_KEY_ENV
from .settings_metadata import SETTINGS_BY_KEY, SettingMetadata
from .system_diagnostics import DiagnosticCheck, SystemDiagnosticsReport

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"

MAX_QUESTION_CHARS = 4000

# Real providers the assistant may call. `mock` output is test data and must
# never be presented as an assistant answer, so mock uses the fallback.
REAL_PROVIDERS = {"openai", "anthropic", "gemini"}


# --- Screen context registry -------------------------------------------------


@dataclass(frozen=True)
class ScreenContext:
    screen_id: str
    title: str
    route: str
    purpose: str
    primary_data_sources: List[str] = field(default_factory=list)
    visible_sections: List[str] = field(default_factory=list)
    common_questions: List[str] = field(default_factory=list)
    related_settings: List[str] = field(default_factory=list)
    related_checks: List[str] = field(default_factory=list)
    related_pipeline_steps: List[str] = field(default_factory=list)
    related_endpoints: List[str] = field(default_factory=list)


_LLM_SETTINGS = ["LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_TIMEOUT", "LLM_BASE_URL"]
_INTELLIGENCE_SETTINGS = [
    "INTELLIGENCE_LLM_PROVIDER",
    "INTELLIGENCE_LLM_MODEL",
    "INTELLIGENCE_LLM_TIMEOUT",
    "INTELLIGENCE_MAX_OUTPUT_TOKENS",
]
_PIPELINE_CHECKS = [
    "pipeline_symbol_index",
    "pipeline_entrypoint_index",
    "pipeline_documentation_index",
    "pipeline_understanding_graph",
    "pipeline_capability_hierarchy",
]

SCREEN_CONTEXTS: List[ScreenContext] = [
    ScreenContext(
        screen_id="overview",
        title="Overview",
        route="/",
        purpose=(
            "Summarize probe activity for the selected system: components, "
            "total traces, last seen time, and active policy modes."
        ),
        primary_data_sources=["components", "traces"],
        visible_sections=["Components", "Total Traces", "Last Seen", "Active Modes"],
        common_questions=[
            "Why are no components listed?",
            "What should I do next to get traces flowing?",
        ],
        related_settings=["PROBE_DB_PATH", "CONTROL_API_KEYS"],
        related_checks=["database_storage", "auth_scope"],
        related_endpoints=["GET /components", "GET /system-diagnostics"],
    ),
    ScreenContext(
        screen_id="system-understanding",
        title="System Understanding",
        route="/system-understanding",
        purpose=(
            "Track the progress and artifacts of the repository understanding "
            "pipeline: repository snapshot, documentation graph, code symbols, "
            "entrypoints, and capability hierarchy."
        ),
        primary_data_sources=[
            "repository snapshots",
            "intelligence runs",
            "understanding graph",
            "system diagnostics",
        ],
        visible_sections=[
            "Repository configured",
            "Snapshot ready",
            "Documentation indexed",
            "Documentation claims scanned",
            "Code symbols indexed",
            "Entrypoints discovered",
            "Docs-code reconciled",
            "Capability hierarchy ready",
        ],
        common_questions=[
            "Why is Documentation indexed missing?",
            "What should INTELLIGENCE_LLM_MODEL be set to?",
            "What does Build / Refresh run?",
        ],
        related_settings=["PROBE_REPOSITORY_ROOTS", *_LLM_SETTINGS, *_INTELLIGENCE_SETTINGS],
        related_checks=[
            "repository_roots",
            "repository_config",
            "snapshot_status",
            "llm_base_config",
            "intelligence_llm_config",
            "llm_last_run",
            *_PIPELINE_CHECKS,
        ],
        related_pipeline_steps=[
            "repository_configured",
            "snapshot_ready",
            "symbols_indexed",
            "entrypoints_discovered",
            "documentation_indexed",
            "documentation_claims_scanned",
            "docs_code_reconciled",
            "capability_hierarchy_ready",
        ],
        related_endpoints=[
            "GET /repository/system-understanding",
            "POST /repository/system-understanding/build",
            "GET /system-diagnostics",
        ],
    ),
    ScreenContext(
        screen_id="repository",
        title="Repository",
        route="/repository",
        purpose=(
            "Configure the analyzed Git repository for this system, create "
            "committed-files-only snapshots, and run drafts/symbol/API scans."
        ),
        primary_data_sources=["repository config", "snapshots", "drafts", "symbols"],
        visible_sections=["Repository Configuration", "Snapshots", "Code Symbols", "API Scan"],
        common_questions=[
            "Why is my repository not listed as a candidate?",
            "Why did snapshot creation fail?",
        ],
        related_settings=["PROBE_REPOSITORY_ROOTS", *_INTELLIGENCE_SETTINGS],
        related_checks=["repository_roots", "repository_config", "snapshot_status"],
        related_pipeline_steps=["repository_configured", "snapshot_ready"],
        related_endpoints=[
            "GET /repository",
            "PUT /repository",
            "POST /repository/snapshots",
            "POST /repository/drafts/generate",
        ],
    ),
    ScreenContext(
        screen_id="capability-map",
        title="Capability Map",
        route="/capability-map",
        purpose=(
            "Navigate from the system's purpose down to capabilities, API "
            "boundaries, and probe-worthy flows generated by the reasoning model."
        ),
        primary_data_sources=["capability hierarchy", "API role cards", "drift"],
        visible_sections=["Capability hierarchy", "API role cards", "Drift"],
        common_questions=[
            "Why is the capability hierarchy empty?",
            "What does drift mean here?",
        ],
        related_settings=[*_INTELLIGENCE_SETTINGS, "LLM_API_KEY"],
        related_checks=["intelligence_llm_config", "pipeline_capability_hierarchy", "llm_last_run"],
        related_pipeline_steps=["capability_hierarchy_ready"],
        related_endpoints=[
            "GET /repository/capability-hierarchy",
            "POST /repository/capability-hierarchy/generate",
        ],
    ),
    ScreenContext(
        screen_id="feature-map",
        title="Feature Map",
        route="/feature-map",
        purpose=(
            "Review evidence-backed System Profile and Feature drafts and "
            "link Features to code symbols."
        ),
        primary_data_sources=["drafts", "feature-code links", "symbols"],
        visible_sections=["System Profile Draft", "Feature drafts", "Code links"],
        common_questions=[
            "Why are there no drafts?",
            "How do I link a feature to code?",
        ],
        related_settings=["PROBE_REPOSITORY_ROOTS", *_INTELLIGENCE_SETTINGS],
        related_checks=["snapshot_status", "intelligence_llm_config", "pipeline_documentation_index"],
        related_pipeline_steps=["snapshot_ready", "documentation_indexed"],
        related_endpoints=[
            "GET /repository/drafts/latest",
            "POST /repository/drafts/generate",
            "GET /repository/code-links",
        ],
    ),
    ScreenContext(
        screen_id="flow-explorer",
        title="Flow Explorer",
        route="/flow-explorer",
        purpose=(
            "Explore call flows from discovered entrypoints and select probe "
            "targets along a flow."
        ),
        primary_data_sources=["entrypoints", "flow graphs", "symbols"],
        visible_sections=["Entrypoints", "Flow graph", "Probe selection"],
        common_questions=["Why are no entrypoints listed?"],
        related_settings=["PROBE_REPOSITORY_ROOTS"],
        related_checks=["snapshot_status", "pipeline_entrypoint_index", "pipeline_symbol_index"],
        related_pipeline_steps=["entrypoints_discovered", "symbols_indexed"],
        related_endpoints=[
            "GET /repository/flow-entrypoints",
            "POST /repository/flow-graphs",
        ],
    ),
    ScreenContext(
        screen_id="probe-planner",
        title="Probe Planner",
        route="/probe-planner",
        purpose=(
            "Generate reasoning-model probe plans for a Feature, produce "
            "instrumentation patches in an isolated worktree, and validate them."
        ),
        primary_data_sources=["probe plans", "probe patches", "validation runs"],
        visible_sections=["Plans", "Patches", "Validation"],
        common_questions=[
            "Why did plan generation fail?",
            "What does validation run?",
        ],
        related_settings=[*_INTELLIGENCE_SETTINGS, "LLM_API_KEY"],
        related_checks=["intelligence_llm_config", "llm_last_run", "snapshot_status"],
        related_pipeline_steps=["snapshot_ready"],
        related_endpoints=[
            "GET /repository/probe-plans",
            "POST /repository/probe-plans/generate",
        ],
    ),
    ScreenContext(
        screen_id="interview",
        title="Interview",
        route="/interview",
        purpose=(
            "Run a conversational system-understanding interview that proposes "
            "probe-agent docstring metadata and probe instrumentation for "
            "human approval."
        ),
        primary_data_sources=["interview sessions", "proposals", "context packs"],
        visible_sections=["Understanding", "Proposals", "Approved set"],
        common_questions=["Why can't the interview generate proposals?"],
        related_settings=[*_INTELLIGENCE_SETTINGS, "LLM_API_KEY"],
        related_checks=["intelligence_llm_config", "llm_last_run", "snapshot_status"],
        related_pipeline_steps=["snapshot_ready"],
        related_endpoints=["GET /interview/sessions", "POST /interview/sessions"],
    ),
    ScreenContext(
        screen_id="experiments",
        title="Experiments",
        route="/experiments",
        purpose=(
            "Create and run isolated experiment workspaces comparing baseline "
            "and source-patch variants, then record a manual decision."
        ),
        primary_data_sources=["experiments", "variants", "metrics"],
        visible_sections=["Experiments", "Variants", "Metrics", "Decision"],
        common_questions=["Why did my experiment fail to run?"],
        related_settings=["PROBE_REPOSITORY_ROOTS", *_INTELLIGENCE_SETTINGS],
        related_checks=["snapshot_status", "intelligence_llm_config"],
        related_pipeline_steps=["snapshot_ready"],
        related_endpoints=["GET /experiments", "POST /experiments"],
    ),
    ScreenContext(
        screen_id="connect-sdk",
        title="Connect SDK",
        route="/connect-sdk",
        purpose=(
            "Show how to install the Python probe SDK, configure its "
            "environment, and decorate functions with @probe."
        ),
        primary_data_sources=["API tokens", "server URL configuration"],
        visible_sections=["Install the SDK", "Configure Environment", "Use the @probe Decorator"],
        common_questions=["Which API key does the SDK need?"],
        related_settings=["CONTROL_API_KEYS"],
        related_checks=["auth_scope"],
        related_endpoints=["POST /tokens/me", "POST /traces"],
    ),
    ScreenContext(
        screen_id="generation",
        title="Generate & Evaluate",
        route="/generation",
        purpose=(
            "Generate candidate implementations from a trace with the "
            "configured LLM and evaluate them before any adoption decision."
        ),
        primary_data_sources=["generation runs", "traces"],
        visible_sections=["New Generation Run", "Generation Runs"],
        common_questions=[
            "Why did generation fail?",
            "Which model does generation use?",
        ],
        related_settings=_LLM_SETTINGS,
        related_checks=["llm_base_config"],
        related_endpoints=["POST /generation-runs", "GET /generation-runs"],
    ),
    ScreenContext(
        screen_id="components",
        title="Components",
        route="/components",
        purpose=(
            "Inspect probed components: traces, policy mode (off/trace/shadow), "
            "shadow comparisons, profiles, and evaluation criteria."
        ),
        primary_data_sources=["components", "traces", "policies", "shadow results"],
        visible_sections=["Components", "Component Profile", "Evaluation Criteria", "Traces"],
        common_questions=[
            "What does shadow mode do?",
            "Why is my component not sending traces?",
        ],
        related_settings=["CONTROL_API_KEYS", "PROBE_DB_PATH"],
        related_checks=["auth_scope", "database_storage"],
        related_endpoints=[
            "GET /components",
            "PUT /components/{component_id}/policy",
        ],
    ),
    ScreenContext(
        screen_id="workspaces",
        title="Decision Workspace",
        route="/workspaces",
        purpose=(
            "Discuss findings with the grounded workspace agent and "
            "accept/reject proposals with a recorded reason."
        ),
        primary_data_sources=["workspaces", "context packs", "proposals"],
        visible_sections=["Workspaces", "Conversation", "Context", "Proposals"],
        common_questions=["Why did the agent turn fail?"],
        related_settings=[*_INTELLIGENCE_SETTINGS, "LLM_API_KEY"],
        related_checks=["intelligence_llm_config", "llm_last_run"],
        related_endpoints=["GET /workspaces", "POST /workspaces/{id}/agent-turns"],
    ),
    ScreenContext(
        screen_id="settings",
        title="Settings",
        route="/settings",
        purpose=(
            "Edit the system configuration profile used as evaluation context "
            "for this system."
        ),
        primary_data_sources=["system profile"],
        visible_sections=["System Configuration"],
        common_questions=["Where do I configure the LLM API key?"],
        related_settings=[*_LLM_SETTINGS, *_INTELLIGENCE_SETTINGS, "PROBE_REPOSITORY_ROOTS"],
        related_checks=["llm_base_config", "intelligence_llm_config", "repository_roots"],
        related_endpoints=["GET /system-profile", "PUT /system-profile"],
    ),
    ScreenContext(
        screen_id="admin",
        title="Administration",
        route="/admin",
        purpose="Manage users, roles, passwords, and API tokens (admin only).",
        primary_data_sources=["users", "tokens"],
        visible_sections=["User Management", "Tokens"],
        common_questions=["How do I create the first admin user?"],
        related_settings=["CONTROL_ADMIN_USERNAME", "CONTROL_ADMIN_PASSWORD", "CONTROL_API_KEYS"],
        related_checks=["auth_scope"],
        related_endpoints=["GET /users", "POST /users", "GET /tokens"],
    ),
]

SCREENS_BY_ID = {s.screen_id: s for s in SCREEN_CONTEXTS}

KNOWN_ROUTES = {s.route for s in SCREEN_CONTEXTS}

# Human labels for the pipeline-step vocabulary used by diagnostics and the
# System Understanding page.
PIPELINE_STEP_LABELS: Dict[str, str] = {
    "repository_configured": "Repository configured",
    "snapshot_ready": "Snapshot ready",
    "symbols_indexed": "Code symbols indexed",
    "entrypoints_discovered": "Entrypoints discovered",
    "documentation_indexed": "Documentation indexed",
    "documentation_claims_scanned": "Documentation claims scanned",
    "docs_code_reconciled": "Docs-code reconciled",
    "capability_hierarchy_ready": "Capability hierarchy ready",
}

# Explicit finite mapping from a failing check to the in-app operation that
# addresses it (label + route). Used for suggested actions.
CHECK_OPERATIONS: Dict[str, tuple] = {
    "repository_config": ("Configure the repository", "/repository"),
    "snapshot_status": ("Create a snapshot", "/repository"),
    "pipeline_symbol_index": ("Run Build / Refresh", "/system-understanding"),
    "pipeline_entrypoint_index": ("Run Build / Refresh", "/system-understanding"),
    "pipeline_documentation_index": ("Run Build / Refresh", "/system-understanding"),
    "pipeline_understanding_graph": ("Run Build / Refresh", "/system-understanding"),
    "pipeline_capability_hierarchy": ("Run Build / Refresh", "/system-understanding"),
    "llm_last_run": ("Run a System Understanding build", "/system-understanding"),
}


def get_screen_context(screen_id: str) -> Optional[ScreenContext]:
    return SCREENS_BY_ID.get(screen_id)


def checks_for_screen(
    ctx: ScreenContext, report: SystemDiagnosticsReport
) -> List[DiagnosticCheck]:
    """Diagnostics checks related to a screen (by id or by page route)."""
    related_ids = set(ctx.related_checks)
    return [
        c
        for c in report.checks
        if c.check_id in related_ids or ctx.route in c.related_pages
    ]


def suggested_questions(
    ctx: ScreenContext, screen_checks: List[DiagnosticCheck]
) -> List[Dict[str, str]]:
    """Failing-check questions first (Issue #102 UI requirement), then static ones."""
    questions: List[Dict[str, str]] = []
    for check in screen_checks:
        if check.severity in ("error", "blocked", "warning"):
            questions.append(
                {
                    "question": f"Why is '{check.title}' {check.severity}?",
                    "source": "diagnostics",
                    "check_id": check.check_id,
                }
            )
    for q in ctx.common_questions:
        questions.append({"question": q, "source": "static", "check_id": ""})
    return questions


# --- Deterministic retrieval -------------------------------------------------


def _normalize(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.lower()).strip()


def match_settings(question: str) -> List[SettingMetadata]:
    """Settings whose exact key appears in the question (finite-set match)."""
    normalized = _normalize(question)
    matched = []
    for key, meta in SETTINGS_BY_KEY.items():
        if _normalize(key) in normalized:
            matched.append(meta)
    return matched


def match_checks(question: str, report: SystemDiagnosticsReport) -> List[DiagnosticCheck]:
    """Checks whose id or exact title appears in the question (finite-set match)."""
    normalized = _normalize(question)
    matched = []
    for check in report.checks:
        if _normalize(check.check_id) in normalized or _normalize(check.title) in normalized:
            matched.append(check)
    return matched


def match_pipeline_steps(question: str) -> List[str]:
    """Pipeline step ids whose id or label appears in the question."""
    normalized = _normalize(question)
    matched = []
    for step_id, label in PIPELINE_STEP_LABELS.items():
        if _normalize(step_id) in normalized or _normalize(label) in normalized:
            matched.append(step_id)
    return matched


def checks_for_pipeline_steps(
    steps: List[str], report: SystemDiagnosticsReport
) -> List[DiagnosticCheck]:
    step_set = set(steps)
    return [
        c for c in report.checks if step_set.intersection(c.related_pipeline_steps)
    ]


# --- Context pack ------------------------------------------------------------


def _setting_dict(meta: SettingMetadata) -> Dict[str, Any]:
    return {
        "key": meta.key,
        "display_name": meta.display_name,
        "category": meta.category,
        "requiredness": meta.requiredness,
        "description": meta.description,
        "impact": meta.impact,
        "remediation": meta.remediation,
        "valid_values": meta.valid_values,
        "validation_rule": meta.validation_rule,
        "related_checks": meta.related_checks,
        "related_pages": meta.related_pages,
        "related_pipeline_steps": meta.related_pipeline_steps,
    }


def _check_dict(check: DiagnosticCheck) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "check_id": check.check_id,
        "title": check.title,
        "severity": check.severity,
        "detail": check.detail,
        "impact": check.impact,
        "remediation": check.remediation,
        "related_env": check.related_env,
        "related_pages": check.related_pages,
        "related_pipeline_steps": check.related_pipeline_steps,
    }
    if check.last_observed_error is not None:
        data["last_observed_error"] = {
            "source": check.last_observed_error.source,
            "status": check.last_observed_error.status,
            "error": check.last_observed_error.error,
        }
    return data


@dataclass
class ContextPack:
    """The limited, deterministic data an answer may be grounded in."""

    screen: ScreenContext
    settings: List[SettingMetadata]
    checks: List[DiagnosticCheck]
    pipeline_steps: List[str]
    mentioned_setting_keys: List[str]
    mentioned_check_ids: List[str]

    def allowed_citation_ids(self) -> Dict[str, set]:
        return {
            "setting": {s.key for s in self.settings},
            "diagnostic_check": {c.check_id for c in self.checks},
            "pipeline_step": set(self.pipeline_steps) | set(PIPELINE_STEP_LABELS),
        }

    def to_llm_payload(self, report: SystemDiagnosticsReport) -> Dict[str, Any]:
        return {
            "screen": {
                "screen_id": self.screen.screen_id,
                "title": self.screen.title,
                "route": self.screen.route,
                "purpose": self.screen.purpose,
                "primary_data_sources": self.screen.primary_data_sources,
                "visible_sections": self.screen.visible_sections,
                "related_endpoints": self.screen.related_endpoints,
            },
            "settings": [_setting_dict(s) for s in self.settings],
            "diagnostics": {
                "overall_severity": report.overall_severity,
                "checks": [_check_dict(c) for c in self.checks],
            },
            "pipeline_steps": [
                {"id": step, "label": PIPELINE_STEP_LABELS.get(step, step)}
                for step in self.pipeline_steps
            ],
            "navigable_routes": sorted(KNOWN_ROUTES),
        }


def build_context_pack(
    ctx: ScreenContext,
    question: str,
    report: SystemDiagnosticsReport,
    visible_check_ids: Optional[List[str]] = None,
) -> ContextPack:
    mentioned_settings = match_settings(question)
    mentioned_checks = match_checks(question, report)
    mentioned_steps = match_pipeline_steps(question)
    step_checks = checks_for_pipeline_steps(mentioned_steps, report)
    screen_checks = checks_for_screen(ctx, report)

    visible = set(visible_check_ids or [])
    visible_checks = [c for c in report.checks if c.check_id in visible]

    checks: List[DiagnosticCheck] = []
    seen_check_ids = set()
    for check in [*mentioned_checks, *step_checks, *visible_checks, *screen_checks]:
        if check.check_id not in seen_check_ids:
            seen_check_ids.add(check.check_id)
            checks.append(check)

    settings: List[SettingMetadata] = []
    seen_keys = set()
    related_env = {env for c in checks for env in c.related_env}
    for meta in [
        *mentioned_settings,
        *(SETTINGS_BY_KEY[k] for k in ctx.related_settings if k in SETTINGS_BY_KEY),
        *(SETTINGS_BY_KEY[k] for k in sorted(related_env) if k in SETTINGS_BY_KEY),
    ]:
        if meta.key not in seen_keys:
            seen_keys.add(meta.key)
            settings.append(meta)

    steps: List[str] = []
    for step in [*mentioned_steps, *ctx.related_pipeline_steps]:
        if step not in steps:
            steps.append(step)

    mentioned_check_ids: List[str] = []
    for check in [*mentioned_checks, *step_checks]:
        if check.check_id not in mentioned_check_ids:
            mentioned_check_ids.append(check.check_id)

    return ContextPack(
        screen=ctx,
        settings=settings,
        checks=checks,
        pipeline_steps=steps,
        mentioned_setting_keys=[s.key for s in mentioned_settings],
        mentioned_check_ids=mentioned_check_ids,
    )


# --- Answer types ------------------------------------------------------------


@dataclass
class SuggestedAction:
    label: str
    kind: str  # navigate | configure | operate
    target: str
    detail: str = ""


@dataclass
class Citation:
    type: str  # setting | diagnostic_check | pipeline_step
    id: str
    title: str = ""
    detail: str = ""


@dataclass
class AssistantAnswer:
    answer: str
    suggested_actions: List[SuggestedAction]
    citations: List[Citation]
    used_fallback: bool
    decision_method: str  # deterministic | reasoning_llm
    provider: str
    model: str
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    fallback_reason: Optional[str] = None


def _citation_title(pack: ContextPack, ctype: str, cid: str) -> tuple:
    if ctype == "setting":
        meta = SETTINGS_BY_KEY.get(cid)
        return (meta.display_name if meta else cid, "")
    if ctype == "diagnostic_check":
        for check in pack.checks:
            if check.check_id == cid:
                return (check.title, f"{check.severity}: {check.detail}")
        return (cid, "")
    return (PIPELINE_STEP_LABELS.get(cid, cid), "")


# --- LLM path ----------------------------------------------------------------


class _RawAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=200)
    kind: str = Field(..., pattern="^(navigate|configure|operate)$")
    target: str = Field(..., min_length=1, max_length=300)
    detail: str = Field(default="", max_length=1000)


class _RawCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., pattern="^(setting|diagnostic_check|pipeline_step)$")
    id: str = Field(..., min_length=1, max_length=200)


class _RawAssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(..., min_length=1, max_length=20_000)
    suggested_actions: List[_RawAction] = Field(default_factory=list, max_length=10)
    citations: List[_RawCitation] = Field(default_factory=list, max_length=20)


_SYSTEM_PROMPT = """You are the in-app assistant for the probe-agent dashboard.
You answer questions about one dashboard screen using ONLY the JSON context
provided by the server: the screen's purpose and sections, static settings
metadata, the current deterministic diagnostics checks (including verbatim
last observed run errors), and pipeline steps. Never invent settings, checks,
file paths, or behavior that is not in the context. Ground explanations in
the diagnostics results and settings metadata, and prefer concrete next
actions. Answer in the same language as the user's question.

Respond with ONLY a JSON object (no markdown fence) of this shape:
{
  "answer": "explanation grounded in the provided context",
  "suggested_actions": [
    {"label": "short action (for operate: the operation name, e.g. 'Run Build / Refresh')",
     "kind": "navigate|configure|operate",
     "target": "a setting key for configure; a route from navigable_routes for navigate and for operate (the screen where the operation is performed)",
     "detail": "optional how-to detail"}
  ],
  "citations": [
    {"type": "setting|diagnostic_check|pipeline_step", "id": "key or id from the context"}
  ]
}
Cite every setting, diagnostics check, and pipeline step you rely on.
"""


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def _llm_answer(
    client: LLMClient,
    config: LLMConfig,
    pack: ContextPack,
    report: SystemDiagnosticsReport,
    question: str,
) -> AssistantAnswer:
    payload = {
        "context": pack.to_llm_payload(report),
        "question": question,
    }
    raw = client.generate_text(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.2,
        max_tokens=2048,
    )
    try:
        data = json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError as exc:
        raise LLMError(f"Assistant response was not valid JSON: {raw[:300]}") from exc
    try:
        parsed = _RawAssistantResponse.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"Assistant response failed schema validation: {exc}") from exc

    allowed = pack.allowed_citation_ids()
    citations = []
    for c in parsed.citations:
        if c.id in allowed.get(c.type, set()):
            title, detail = _citation_title(pack, c.type, c.id)
            citations.append(Citation(type=c.type, id=c.id, title=title, detail=detail))

    setting_keys = {s.key for s in pack.settings}
    actions = []
    for a in parsed.suggested_actions:
        # navigate/operate targets are routes the UI navigates to; anything
        # outside the finite route set is dropped (structural validation).
        if a.kind in ("navigate", "operate") and a.target not in KNOWN_ROUTES:
            continue
        if a.kind == "configure" and a.target not in setting_keys:
            continue
        actions.append(
            SuggestedAction(label=a.label, kind=a.kind, target=a.target, detail=a.detail)
        )

    return AssistantAnswer(
        answer=parsed.answer,
        suggested_actions=actions,
        citations=citations,
        used_fallback=False,
        decision_method="reasoning_llm",
        provider=config.provider,
        model=config.model,
    )


# --- Deterministic fallback --------------------------------------------------


def _describe_setting(meta: SettingMetadata, related_checks: List[DiagnosticCheck]) -> str:
    lines = [
        f"{meta.key} ({meta.display_name}, {meta.requiredness}): {meta.description}",
        f"Affects: {meta.impact}",
        f"Fix: {meta.remediation}",
    ]
    if meta.valid_values:
        lines.append(f"Valid values: {', '.join(meta.valid_values)}")
    if meta.validation_rule:
        lines.append(f"Validation: {meta.validation_rule}")
    for check in related_checks:
        if meta.key in check.related_env and check.severity != "ok":
            lines.append(
                f"Current status — '{check.title}' is {check.severity}: {check.detail}"
            )
    return "\n".join(lines)


def _describe_check(check: DiagnosticCheck) -> str:
    lines = [f"'{check.title}' is currently {check.severity}: {check.detail}"]
    if check.impact:
        lines.append(f"Impact: {check.impact}")
    if check.remediation:
        lines.append(f"Fix: {check.remediation}")
    if check.related_env:
        lines.append(f"Related settings: {', '.join(check.related_env)}")
    if check.related_pipeline_steps:
        labels = [PIPELINE_STEP_LABELS.get(s, s) for s in check.related_pipeline_steps]
        lines.append(f"Related pipeline steps: {', '.join(labels)}")
    if check.last_observed_error is not None:
        err = check.last_observed_error
        lines.append(
            "Last observed error"
            + (f" ({err.source})" if err.source else "")
            + f": {err.error or err.status}"
        )
    return "\n".join(lines)


def _fallback_actions(
    checks: List[DiagnosticCheck], settings: List[SettingMetadata]
) -> List[SuggestedAction]:
    actions: List[SuggestedAction] = []
    seen = set()

    def add(action: SuggestedAction):
        key = (action.kind, action.target, action.label)
        if key not in seen:
            seen.add(key)
            actions.append(action)

    for check in checks:
        if check.severity == "ok":
            continue
        operation = CHECK_OPERATIONS.get(check.check_id)
        if operation:
            label, route = operation
            add(SuggestedAction(label=label, kind="operate", target=route, detail=check.remediation))
        for page in check.related_pages:
            if page in KNOWN_ROUTES:
                add(SuggestedAction(label=f"Open {page}", kind="navigate", target=page))
        for env in check.related_env:
            meta = SETTINGS_BY_KEY.get(env)
            if meta:
                add(
                    SuggestedAction(
                        label=f"Review {env}",
                        kind="configure",
                        target=env,
                        detail=meta.remediation,
                    )
                )
    if not actions:
        for meta in settings:
            add(
                SuggestedAction(
                    label=f"Review {meta.key}",
                    kind="configure",
                    target=meta.key,
                    detail=meta.remediation,
                )
            )
    return actions[:8]


def _fallback_answer(
    pack: ContextPack,
    report: SystemDiagnosticsReport,
    config: LLMConfig,
    reason: str,
) -> AssistantAnswer:
    """Compose an answer verbatim from static metadata and diagnostics.

    Only finite-set matches (setting keys, check ids/titles, pipeline steps)
    select content; free text is never interpreted heuristically.
    """
    mentioned_settings = [
        SETTINGS_BY_KEY[k] for k in pack.mentioned_setting_keys if k in SETTINGS_BY_KEY
    ]
    mentioned_checks = [
        c for c in pack.checks if c.check_id in set(pack.mentioned_check_ids)
    ]

    sections: List[str] = []
    citations: List[Citation] = []
    action_checks: List[DiagnosticCheck] = []

    for meta in mentioned_settings:
        sections.append(_describe_setting(meta, pack.checks))
        citations.append(
            Citation(type="setting", id=meta.key, title=meta.display_name)
        )
        for check in pack.checks:
            if meta.key in check.related_env and check.severity != "ok":
                action_checks.append(check)

    for check in mentioned_checks:
        sections.append(_describe_check(check))
        citations.append(
            Citation(
                type="diagnostic_check",
                id=check.check_id,
                title=check.title,
                detail=f"{check.severity}: {check.detail}",
            )
        )
        action_checks.append(check)

    if not sections:
        failing = [c for c in pack.checks if c.severity != "ok"]
        overview_lines = [
            f"{pack.screen.title}: {pack.screen.purpose}",
        ]
        if pack.screen.visible_sections:
            overview_lines.append(
                f"Main sections: {', '.join(pack.screen.visible_sections)}"
            )
        if failing:
            overview_lines.append("Current problems on this screen:")
            for check in failing:
                overview_lines.append(
                    f"- '{check.title}' is {check.severity}: {check.detail}"
                )
                citations.append(
                    Citation(
                        type="diagnostic_check",
                        id=check.check_id,
                        title=check.title,
                        detail=f"{check.severity}: {check.detail}",
                    )
                )
            action_checks.extend(failing)
        else:
            overview_lines.append(
                "All diagnostics checks related to this screen are passing."
            )
        overview_lines.append(
            "The reasoning model is not available, so this rule-based fallback "
            "can only explain known settings and diagnostics checks. Mention a "
            "setting key (e.g. "
            + ", ".join(pack.screen.related_settings[:3] or ["LLM_PROVIDER"])
            + ") or a check name for details."
        )
        sections.append("\n".join(overview_lines))

    return AssistantAnswer(
        answer="\n\n".join(sections),
        suggested_actions=_fallback_actions(
            action_checks or pack.checks, mentioned_settings or pack.settings
        ),
        citations=citations,
        used_fallback=True,
        decision_method="deterministic",
        provider=config.provider,
        model=config.model,
        fallback_reason=reason,
    )


# --- Entry point -------------------------------------------------------------


def answer_question(
    ctx: ScreenContext,
    question: str,
    report: SystemDiagnosticsReport,
    config: LLMConfig,
    client: Optional[LLMClient],
    visible_check_ids: Optional[List[str]] = None,
) -> AssistantAnswer:
    """Answer a screen question; LLM when available, marked fallback otherwise.

    `client` is None when no real provider is usable (mock provider or missing
    API key); the caller decides that so tests can inject fake clients.
    """
    pack = build_context_pack(ctx, question, report, visible_check_ids)
    if client is None:
        if config.provider == "mock":
            reason = (
                "LLM provider 'mock' is test-only data and is never used for "
                "assistant answers."
            )
        elif config.provider in REAL_PROVIDERS and not config.api_key:
            key_env = PROVIDER_KEY_ENV.get(config.provider, "")
            reason = (
                f"No API key for provider '{config.provider}' "
                f"(set LLM_API_KEY{f' or {key_env}' if key_env else ''})."
            )
        else:
            reason = f"No usable LLM configuration (provider '{config.provider}')."
        return _fallback_answer(pack, report, config, reason)
    try:
        return _llm_answer(client, config, pack, report, question)
    except LLMError as exc:
        return _fallback_answer(pack, report, config, f"LLM call failed: {exc}")
