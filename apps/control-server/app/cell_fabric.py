"""Probe Cell Fabric: Cell contract / Role Card / common state schema
(Issue #298, Sub 1 of the Probe Cell Fabric epic, Issue #297).

This module is the server-side contract layer mirroring the three shared
schemas (``shared/schemas/agent_role_card.schema.json``,
``cell_definition.schema.json``, ``cell_state.schema.json``): Pydantic v2
models with ``model_config = ConfigDict(extra="forbid")`` so an unknown field
is rejected fail-closed (Principle 6), a finite task-status transition table,
a delegation payload validator, Role Card semver helpers, and model-alias
resolution.

Design notes carried over from ``docs/project-intelligence.md``'s "Probe Cell
Fabric(Issue #297)" section:

- Worker and orchestrator Cells are NOT modeled as separate kinds. The same
  ``CellDefinitionContract`` represents both; orchestrator-ness is expressed
  structurally by a non-null ``roster`` (child cell_ids).
- Agent Role Card (this module) is a distinct contract from the existing API
  Role Card (Issue #58, ``app/api_role_cards.py``-equivalent display model for
  an API's role) -- the two are never mixed into one schema or table.
- Role Cards never embed a literal provider/model name. Only ``model_alias``
  is stored; ``resolve_model_alias`` resolves it to a real provider/model via
  ``CELL_MODEL_ALIAS_<UPPER_ALIAS>`` (format ``provider:model``), falling back
  to ``LLMConfig.intelligence_from_env()`` when the env var is unset. This is
  the whole point: changing the real model name never requires a card
  revision.
- Task Goal/Task ledger persistence, Cell worker execution, and LLM-generated
  Role Cards are explicitly out of scope here (Issue #300 / later subs own
  them); this module only defines the reusable contract + validators the
  later subs build on.

probe-agent:
  role: Cell / Role Card / Cell State contract models and finite validators
  capability: probe-cell-fabric
  element_type: core
  consumers: [control-server-routes]
  operation_kind: pure
  state_effects: []
  probe_value: Verify unknown fields are rejected fail-closed, task transitions follow the explicit finite table (done requires acceptance+evidence), Role Card version compatibility is same-major-and->=-pinned only, and model alias resolution never requires a card revision when the underlying provider/model changes.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .llm import KNOWN_PROVIDERS, LLMConfig, PROVIDER_KEY_ENV

# --- Schema versions (one per shared contract) -------------------------------

AGENT_ROLE_CARD_SCHEMA_VERSION = "1.0"
CELL_DEFINITION_SCHEMA_VERSION = "1.0"
CELL_STATE_SCHEMA_VERSION = "1.0"

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class CellContractError(ValueError):
    """Fail-closed validation error for the Cell Fabric contract layer.

    A ``ValueError`` subclass so it can be raised directly, or raised inside
    a Pydantic ``model_validator`` (Pydantic v2 folds ``ValueError``
    subclasses raised in validators into the model's own ``ValidationError``,
    so the same failure surfaces as an ordinary 422 wherever one of these
    models is used as a FastAPI request body).
    """


# ---------------------------------------------------------------------------
# Agent Role Card
# ---------------------------------------------------------------------------


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_tools: List[str] = Field(default_factory=list)
    network_allowed: bool = False


class AgentRoleCard(BaseModel):
    """Mirrors ``shared/schemas/agent_role_card.schema.json``."""

    model_config = ConfigDict(extra="forbid")

    role_key: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    status: str = Field(..., description="draft | active | deprecated")
    mission: str = Field(..., min_length=1)
    scope: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    model_alias: str = Field(..., min_length=1)
    tool_policy: ToolPolicy
    acceptance_template: List[str] = Field(default_factory=list)
    rubric_ref: Optional[str] = None
    changelog: str = Field(..., min_length=1)
    schema_version: str

    @model_validator(mode="after")
    def _validate_contract(self) -> "AgentRoleCard":
        if self.status not in ROLE_CARD_STATUSES:
            raise CellContractError(f"Unknown role card status: {self.status!r}")
        if self.schema_version != AGENT_ROLE_CARD_SCHEMA_VERSION:
            raise CellContractError(
                "Unsupported agent_role_card schema_version: "
                f"{self.schema_version!r} (expected {AGENT_ROLE_CARD_SCHEMA_VERSION!r})"
            )
        # Fails closed on a malformed version even though the Field pattern
        # already constrains the shape; parse_semver is the single source of
        # truth other call sites (compatibility checks) also rely on.
        parse_semver(self.version)
        return self


ROLE_CARD_STATUSES = ("draft", "active", "deprecated")


# ---------------------------------------------------------------------------
# Cell Definition (worker AND orchestrator share this one contract)
# ---------------------------------------------------------------------------


class RoleCardRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_key: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")


CELL_DEFINITION_STATUSES = ("active", "dormant", "retired")


class CellDefinitionContract(BaseModel):
    """Mirrors ``shared/schemas/cell_definition.schema.json``.

    ``roster`` is ``None`` for a worker Cell and a (possibly empty) list of
    child cell_ids for an orchestrator Cell -- there is no separate ``kind``
    field.
    """

    model_config = ConfigDict(extra="forbid")

    cell_id: str = Field(..., min_length=1)
    roster: Optional[List[str]] = None
    role_card_ref: RoleCardRef
    status: str = Field(..., description="active | dormant | retired")
    mission: Optional[str] = None
    schema_version: str

    @model_validator(mode="after")
    def _validate_contract(self) -> "CellDefinitionContract":
        if self.status not in CELL_DEFINITION_STATUSES:
            raise CellContractError(f"Unknown cell status: {self.status!r}")
        if self.schema_version != CELL_DEFINITION_SCHEMA_VERSION:
            raise CellContractError(
                "Unsupported cell_definition schema_version: "
                f"{self.schema_version!r} (expected {CELL_DEFINITION_SCHEMA_VERSION!r})"
            )
        if self.roster is not None and len(self.roster) != len(set(self.roster)):
            raise CellContractError("roster cell_ids must be distinct")
        return self


# ---------------------------------------------------------------------------
# Cell State (common L0/L1 read-model; additive extension blocks)
# ---------------------------------------------------------------------------


class RoleCardSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_key: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    model_alias: str = Field(..., min_length=1)


TASK_STATUSES: Tuple[str, ...] = (
    "todo", "doing", "review", "done", "failed", "blocked",
)


class TaskCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo: int = 0
    doing: int = 0
    review: int = 0
    done: int = 0
    failed: int = 0
    blocked: int = 0


class TaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counts: TaskCounts = Field(default_factory=TaskCounts)


class CellHealth(BaseModel):
    """Filled by Sub 2 (#299) from traces/evaluation_results/shadow_results/
    replay_runs/experiments. Every field is nullable/optional -- absent means
    not-yet-observed, never a guessed value (Sub 1 always leaves this unset)."""

    model_config = ConfigDict(extra="forbid")

    heartbeat_at: Optional[float] = None
    last_activation_at: Optional[float] = None
    queue_length: Optional[int] = None
    queue_oldest_age_seconds: Optional[float] = None
    error_rate: Optional[float] = None
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None


class CellQuality(BaseModel):
    """Reserved for Sub 6 (#302) quality sampling / audit / quality floor
    state. Intentionally empty at Sub 1."""

    model_config = ConfigDict(extra="forbid")


class CellImprovement(BaseModel):
    """Reserved for Sub 7 (#304) improvement hypothesis / canary / adoption
    state. Intentionally empty at Sub 1."""

    model_config = ConfigDict(extra="forbid")


class CellOrchestration(BaseModel):
    """Filled by Sub 4 (#301) for roster-bearing Cells only."""

    model_config = ConfigDict(extra="forbid")

    roster: Optional[List[str]] = None
    active_count: Optional[int] = None
    dormant_count: Optional[int] = None
    retired_count: Optional[int] = None


class CellStateContract(BaseModel):
    """Mirrors ``shared/schemas/cell_state.schema.json``."""

    model_config = ConfigDict(extra="forbid")

    cell_id: str = Field(..., min_length=1)
    role_card: RoleCardSummary
    mission: str = ""
    tasks: TaskSummary = Field(default_factory=TaskSummary)
    health: Optional[CellHealth] = None
    quality: Optional[CellQuality] = None
    improvement: Optional[CellImprovement] = None
    orchestration: Optional[CellOrchestration] = None
    schema_version: str = CELL_STATE_SCHEMA_VERSION

    @model_validator(mode="after")
    def _validate_contract(self) -> "CellStateContract":
        if self.schema_version != CELL_STATE_SCHEMA_VERSION:
            raise CellContractError(
                "Unsupported cell_state schema_version: "
                f"{self.schema_version!r} (expected {CELL_STATE_SCHEMA_VERSION!r})"
            )
        return self


def build_minimal_cell_state(
    *,
    cell_id: str,
    role_key: str,
    role_version: str,
    model_alias: str,
    mission: str,
    roster: Optional[List[str]],
) -> CellStateContract:
    """Build the phase-1 ``cell_state`` document from a Cell Definition alone.

    tasks/health/quality/improvement stay empty/null; #299 is the first sub
    to fill ``health`` from real Trace/Evaluation/Shadow/Replay/Experiment
    facts, so nothing here is guessed in their place.
    """
    orchestration = None
    if roster is not None:
        orchestration = CellOrchestration(
            roster=list(roster), active_count=None, dormant_count=None,
            retired_count=None,
        )
    return CellStateContract(
        cell_id=cell_id,
        role_card=RoleCardSummary(
            role_key=role_key, version=role_version, model_alias=model_alias,
        ),
        mission=mission or "",
        tasks=TaskSummary(counts=TaskCounts()),
        health=None,
        quality=None,
        improvement=None,
        orchestration=orchestration,
        schema_version=CELL_STATE_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Task status transition table (Goal/Task ledger persistence is Issue #300's
# scope; only the reusable contract/validators live here).
# ---------------------------------------------------------------------------

TASK_TRANSITIONS: Dict[str, set] = {
    "todo": {"doing", "blocked"},
    "doing": {"review", "failed", "blocked"},
    "review": {"done", "failed", "doing"},
    "blocked": {"todo", "doing", "failed"},
    "failed": {"todo"},
    "done": set(),
}


def validate_task_transition(
    current: str,
    new: str,
    *,
    acceptance_met: bool = False,
    evidence_refs: Optional[List[str]] = None,
) -> None:
    """Validate a task status transition against the explicit finite table.

    Transitioning to ``done`` additionally requires ``acceptance_met=True``
    AND at least one evidence ref; anything else raises ``CellContractError``.
    """
    if current not in TASK_TRANSITIONS:
        raise CellContractError(f"Unknown current task status: {current!r}")
    if new not in TASK_STATUSES:
        raise CellContractError(f"Unknown target task status: {new!r}")
    allowed = TASK_TRANSITIONS[current]
    if new not in allowed:
        raise CellContractError(
            f"Task transition {current!r} -> {new!r} is not allowed"
        )
    if new == "done":
        if not acceptance_met:
            raise CellContractError(
                "Transition to 'done' requires acceptance_met=True"
            )
        if not evidence_refs:
            raise CellContractError(
                "Transition to 'done' requires at least one evidence ref"
            )


# ---------------------------------------------------------------------------
# Delegation payload (P1 delegate in Sub 3 / #300's protocol; only the
# validator lives here, not persistence)
# ---------------------------------------------------------------------------


class TaskDelegation(BaseModel):
    """A task-delegation payload. An empty/missing ``acceptance`` list is a
    validation error -- a task can never be delegated without criteria."""

    model_config = ConfigDict(extra="forbid")

    goal_ref: str = Field(..., min_length=1)
    acceptance: List[str] = Field(..., min_length=1)
    context_refs: List[str] = Field(default_factory=list)
    budget: Optional[Dict[str, Any]] = None
    deadline: Optional[str] = None
    priority: Optional[str] = None


# ---------------------------------------------------------------------------
# Role Card semver helpers
# ---------------------------------------------------------------------------


def parse_semver(version: str) -> Tuple[int, int, int]:
    match = _SEMVER_RE.match((version or "").strip())
    if not match:
        raise CellContractError(f"Invalid semver string: {version!r}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def role_card_versions_compatible(pinned_version: str, candidate_version: str) -> bool:
    """Deterministic compatibility rule (Principle 6): compatible iff the
    major version matches AND the candidate version is >= the pinned
    version. Malformed input fails closed (raises ``CellContractError``)
    rather than being treated as incompatible/compatible by guess."""
    pinned = parse_semver(pinned_version)
    candidate = parse_semver(candidate_version)
    if pinned[0] != candidate[0]:
        return False
    return candidate >= pinned


# ---------------------------------------------------------------------------
# Model alias resolution (Principle: provider/model real names never live in
# a Role Card; changing CELL_MODEL_ALIAS_<X> must never require a card
# revision).
# ---------------------------------------------------------------------------


def model_alias_env_var(alias: str) -> str:
    """Deterministic, structural alias -> env var name transform
    (Principle 6): uppercase, non-alnum runs collapsed to a single ``_``."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", (alias or "").strip()).upper().strip("_")
    return f"CELL_MODEL_ALIAS_{normalized}"


def resolve_model_alias(alias: str) -> LLMConfig:
    """Resolve a Role Card's ``model_alias`` to a real provider/model config.

    Reads ``CELL_MODEL_ALIAS_<UPPER_ALIAS>`` (format ``provider:model``); an
    unset/blank env var falls back to ``LLMConfig.intelligence_from_env()``
    unchanged. An unknown provider or a malformed value fails closed with
    ``CellContractError`` -- never silently falls back to a guessed provider.
    """
    normalized_alias = (alias or "").strip()
    if not normalized_alias:
        raise CellContractError("model_alias must not be empty")

    env_var = model_alias_env_var(normalized_alias)
    raw = (os.getenv(env_var) or "").strip()
    if not raw:
        return LLMConfig.intelligence_from_env()

    if ":" not in raw:
        raise CellContractError(
            f"{env_var} must be in 'provider:model' format, got {raw!r}"
        )
    provider, _, model = raw.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if provider not in KNOWN_PROVIDERS:
        raise CellContractError(
            f"{env_var} names an unknown provider {provider!r}; "
            f"must be one of {sorted(KNOWN_PROVIDERS)}"
        )
    if not model:
        raise CellContractError(f"{env_var} is missing a model name after ':'")

    specific_env = PROVIDER_KEY_ENV.get(provider)
    api_key = (
        (os.getenv("LLM_API_KEY") or "").strip()
        or ((os.getenv(specific_env) or "").strip() if specific_env else "")
    ) or None
    try:
        timeout = float(os.getenv("LLM_TIMEOUT", "120"))
    except ValueError:
        timeout = 120.0
    return LLMConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=os.getenv("LLM_BASE_URL") or None,
        timeout=timeout,
    )
