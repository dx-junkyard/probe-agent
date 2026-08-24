from typing import Any, Dict, List, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .intelligence_run_types import IntelligenceRunType

Mode = Literal["off", "trace", "shadow"]
Evaluation = Literal["better", "worse", "same", "unknown"]
GenerationVerdict = Literal["better", "worse", "same", "unsafe", "error", "unknown"]


EntityRole = Literal["source", "derived", "related"]
# Projection phases: input/output (Issue #146); shadow_* added in Issue #150.
ProjectionPhase = Literal["input", "output", "shadow_current", "shadow_candidate"]

# Replay capture (Issue #242 Phase A / #243): deterministic structural
# classification of whether a trace's structured input capture can
# mechanically restore the call inputs. Finite sets shared with
# shared/schemas/trace_event.schema.json and the SDK's replay_capture module.
Replayability = Literal["replayable", "partial", "unreplayable"]
ReplayReason = Literal[
    "unsupported_type",
    "redacted",
    "depth_limit_exceeded",
    "size_limit_exceeded",
    "round_trip_failed",
    "capture_failed",
    "redaction_blocked",
]


class TraceEntity(BaseModel):
    type: str
    id: str
    role: EntityRole = "related"


class TraceProjectionIn(BaseModel):
    """A projection extraction result attached to a trace (Issue #146)."""

    projection_name: str
    phase: ProjectionPhase = "output"
    fields: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    samples: Dict[str, Any] = Field(default_factory=dict)
    data_hash: Optional[str] = None
    truncated: bool = False
    error: Optional[str] = None


class SDKTransportSummary(BaseModel):
    """Bounded SDK queue/breaker loss summary emitted by Issue #272."""

    dropped_count: int = Field(ge=0, le=9_223_372_036_854_775_807)
    failure_count: int = Field(ge=0, le=9_223_372_036_854_775_807)
    state: Literal["closed", "open", "half_open"]


class TraceEvent(BaseModel):
    trace_id: str
    component_id: str
    mode: Optional[str] = None
    input: Optional[Any] = None
    output: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    timestamp: float
    # Phase 1 lineage (Issue #145) — all optional, backward compatible.
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    flow_id: Optional[str] = None
    correlation_id: Optional[str] = None
    entities: Optional[List[TraceEntity]] = None
    # Phase 2 projections (Issue #146) — optional extraction results.
    projections: Optional[List[TraceProjectionIn]] = None
    # Replay capture (Issue #242 Phase A / #243) — all optional, additive.
    # input_capture is the canonical JSON-encoded {"args": [...], "kwargs":
    # {...}} structure (see trace_event.schema.json for the "__probe__"
    # marker encoding); replayability/replay_reasons are enum-validated so
    # unknown values are rejected with 422.
    input_capture: Optional[Any] = None
    replayability: Optional[Replayability] = None
    replay_reasons: Optional[List[ReplayReason]] = None
    sdk_transport: Optional[SDKTransportSummary] = None
    # Issue #290 Finding 5: optional deployment provenance reported by the
    # SDK (PROBE_ENVIRONMENT / PROBE_GIT_SHA). Both additive/backward
    # compatible; None on every trace predating this capability. Feeds
    # app/runtime_reality.py's provenance envelope -- never fabricated here.
    environment: Optional[str] = None
    git_sha: Optional[str] = None


class ProjectionOut(BaseModel):
    trace_id: str
    component_id: str
    projection_name: str
    phase: str
    fields: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    samples: Dict[str, Any] = Field(default_factory=dict)
    data_hash: Optional[str] = None
    truncated: bool = False
    error: Optional[str] = None
    created_at: float


class LineageEntityOut(BaseModel):
    type: str
    id: str
    role: str = "related"


class LineageProjectionOut(BaseModel):
    projection_name: str
    phase: str
    fields: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    samples: Dict[str, Any] = Field(default_factory=dict)
    data_hash: Optional[str] = None
    truncated: bool = False
    error: Optional[str] = None


class LineageStepOut(BaseModel):
    trace_id: str
    component_id: str
    mode: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    flow_id: Optional[str] = None
    correlation_id: Optional[str] = None
    duration_ms: Optional[float] = None
    timestamp: float
    output: Optional[str] = None
    error: Optional[str] = None
    replayability: Optional[Replayability] = None
    replay_reasons: List[ReplayReason] = Field(default_factory=list)
    entities: List[LineageEntityOut] = Field(default_factory=list)
    projections: List[LineageProjectionOut] = Field(default_factory=list)


class LineageOut(BaseModel):
    query: Dict[str, Any] = Field(default_factory=dict)
    steps: List[LineageStepOut] = Field(default_factory=list)


# --- Trace analyzers (Issue #148) ------------------------------------------

AnalyzerReviewStatus = Literal["proposed", "approved", "rejected"]


class TraceAnalyzerCreate(BaseModel):
    name: str = ""
    intent: str = ""
    spec: Dict[str, Any]


class AnalyzerReviewUpdate(BaseModel):
    review_status: Literal["approved", "rejected"]


class AnalyzerProposeRequest(BaseModel):
    intent: str = Field(..., min_length=1)
    name: str = ""


class TraceAnalyzerOut(BaseModel):
    id: int
    name: str = ""
    intent: str = ""
    spec: Dict[str, Any] = Field(default_factory=dict)
    source: str = "trace_projections"
    review_status: str = "proposed"
    decision_method: str = "manual"
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    schema_version: Optional[str] = None
    is_mock: bool = False
    # Audit of the human review decision (Principle 7): set on approve/reject.
    # Always 'manual' when set — approval never comes from the LLM.
    reviewed_at: Optional[float] = None
    review_decision_method: Optional[str] = None
    created_at: float
    updated_at: float


class AnalyzerEntityOut(BaseModel):
    entity_type: str
    entity_id: str


class AnalyzerContextOut(BaseModel):
    """Candidate values for the Trace Analyzer builder (Issue #157).

    Deterministic, read-only projection of the identifiers a declarative
    analyzer spec may reference, so the dashboard can offer them as choices
    instead of asking the user to hand-write JSON. Sourced from the same
    finite sets the LLM proposal context uses (Principle 6).
    """

    components: List[str] = Field(default_factory=list)
    entity_types: List[str] = Field(default_factory=list)
    entities: List[AnalyzerEntityOut] = Field(default_factory=list)
    projection_names: List[str] = Field(default_factory=list)
    field_names: List[str] = Field(default_factory=list)
    phases: List[str] = Field(default_factory=list)
    entities_truncated: bool = False


class AnalysisRunOut(BaseModel):
    id: int
    analyzer_id: int
    status: str
    result: Optional[Dict[str, Any]] = None
    error_details: Optional[str] = None
    row_count: Optional[int] = None
    started_at: float
    completed_at: Optional[float] = None
    # Issue #152: set when a retention policy may have pruned the projection
    # data this run referenced (no reference counting; conservative by age).
    data_expired: bool = False
    data_expired_note: Optional[str] = None


RetentionTarget = Literal[
    "trace_spans", "trace_entities", "trace_projections", "trace_analysis_runs"
]


class RetentionPolicyIn(BaseModel):
    target_table: RetentionTarget
    max_age_days: Optional[float] = Field(default=None, ge=0)
    max_count: Optional[int] = Field(default=None, ge=0)


class RetentionPoliciesUpdate(BaseModel):
    policies: List[RetentionPolicyIn] = Field(default_factory=list)


class RetentionPolicyOut(BaseModel):
    target_table: str
    max_age_days: Optional[float] = None
    max_count: Optional[int] = None
    updated_at: float


class RetentionApplyResult(BaseModel):
    target_table: str
    deleted_count: int


class RetentionApplyOut(BaseModel):
    executed_at: float
    results: List[RetentionApplyResult] = Field(default_factory=list)


class RetentionAuditOut(BaseModel):
    id: int
    target_table: str
    deleted_count: int
    reason: str
    executed_at: float


class ShadowResult(BaseModel):
    trace_id: str
    component_id: str
    current_output: Optional[str] = None
    candidate_output: Optional[str] = None
    candidate_error: Optional[str] = None
    candidate_duration_ms: float = 0.0
    timestamp: float
    # Required only when this Component is governed by an Evolution Node.
    # The route derives the candidate identity from this explicit canonical
    # reference and verifies it against the approved Flow proposal.
    flow_experiment_proposal_id: Optional[int] = None
    # Governed Shadow accepts only a canonical candidate row that the server
    # can resolve to its patch digest and pinned snapshot. The legacy free-text
    # fields below are retained only for ungoverned compatibility.
    flow_experiment_candidate_kind: Optional[
        Literal["candidate_version", "replay_variant"]
    ] = None
    flow_experiment_candidate_id: Optional[int] = None
    flow_experiment_candidate_ref: Optional[str] = None
    flow_experiment_snapshot_id: Optional[int] = None
    # Phase 5 shadow projections (Issue #150): shadow_current / shadow_candidate.
    projections: Optional[List["TraceProjectionIn"]] = None


class Policy(BaseModel):
    mode: Mode = "trace"


class PolicyUpdate(BaseModel):
    mode: Mode


class ComponentSummary(BaseModel):
    component_id: str
    mode: Mode
    trace_count: int = 0
    last_seen: Optional[float] = None


class EvaluationUpdate(BaseModel):
    evaluation: Evaluation = Field(..., description="manual verdict")


CriterionType = Literal[
    "natural_language",
    "exact_match",
    "json_equal",
    "required_keys",
    "contains",
    "regex",
]
EvaluationStatus = Literal["ok", "ng", "needs_review"]


class SystemProfile(BaseModel):
    name: str = ""
    purpose: str = ""
    target_users: List[str] = Field(default_factory=list)
    stakeholder_value: str = ""
    constraints: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


class SystemProfileUpdate(BaseModel):
    name: str = ""
    purpose: str = ""
    target_users: List[str] = Field(default_factory=list)
    stakeholder_value: str = ""
    constraints: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)


class SystemCreate(BaseModel):
    name: str = Field(..., min_length=1)
    environment: str = ""
    description: str = ""


class SystemUpdate(BaseModel):
    name: str = Field(..., min_length=1)
    environment: str = ""
    description: str = ""


class SystemOut(BaseModel):
    id: int
    name: str
    environment: str = ""
    description: str = ""
    owner_user_id: Optional[int] = None
    created_at: float
    updated_at: float
    component_count: int = 0
    trace_count: int = 0
    last_seen: Optional[float] = None


# Issue #165: deterministic signal-reception facts for the connectivity
# warning badge and the setup-guide page. `state` is a finite classification;
# smoke traces are recognized by exact component_id match against the
# documented convention (Principle 6: explicit finite set, no heuristics).
SMOKE_CHECK_COMPONENT_ID = "probe-smoke-check"

ConnectivityState = Literal["no_signal", "smoke_only", "receiving"]

# Issue #370: freshness is a SECOND, independent axis. ConnectivityState above
# is a cumulative lifecycle milestone -- "this system has connected at least
# once" -- and never expires. ConnectivityFreshness answers "is it receiving
# right now", which does. Rendering the milestone as the live status is the
# bug this type exists to prevent.
ConnectivityFreshness = Literal[
    "never_received", "receiving_now", "delayed", "stale"
]


class ConnectivityStatusOut(BaseModel):
    system_id: int
    state: ConnectivityState
    total_trace_count: int
    smoke_trace_count: int
    real_trace_count: int
    first_trace_at: Optional[float] = None
    last_trace_at: Optional[float] = None
    last_trace_component_id: Optional[str] = None
    smoke_component_id: str = SMOKE_CHECK_COMPONENT_ID
    materialized_session_ids: List[int] = Field(default_factory=list)
    # --- Issue #370: live reception, separate from the milestone above -------
    #: Workload freshness, classified from the newest NON-smoke trace. A smoke
    #: check proves the transport, not that the instrumented workload runs.
    freshness: ConnectivityFreshness = "never_received"
    #: Transport freshness, classified from the newest trace of any kind.
    #: Reported separately so "the smoke check still gets through" stays
    #: visible rather than being read as workload health.
    transport_freshness: ConnectivityFreshness = "never_received"
    #: Newest non-smoke trace; None when only smoke traces have arrived.
    last_real_trace_at: Optional[float] = None
    #: Seconds since the newest non-smoke trace — the same event `freshness`
    #: judged, so the label and the relative time cannot disagree.
    seconds_since_last_trace: Optional[float] = None
    #: Seconds since the newest trace of any kind.
    seconds_since_last_any_trace: Optional[float] = None
    #: The server clock this reading was taken against, so the client can show
    #: a relative time that does not drift with its own clock.
    evaluated_at: float = 0.0
    #: Positive when the newest trace is timestamped ahead of the server.
    clock_skew_seconds: float = 0.0
    #: Windowed real-workload counts (smoke traces excluded).
    real_trace_count_5m: int = 0
    real_trace_count_1h: int = 0
    real_trace_count_24h: int = 0
    #: The thresholds this reading used, always returned so the displayed
    #: state is explainable without consulting the source.
    delayed_after_seconds: float = 0.0
    stale_after_seconds: float = 0.0
    #: True when the thresholds came from a System-specific policy row.
    thresholds_customized: bool = False


class ConnectivityFreshnessPolicyOut(BaseModel):
    system_id: int
    delayed_after_seconds: float
    stale_after_seconds: float
    customized: bool
    updated_at: Optional[float] = None


class ConnectivityFreshnessPolicyUpdate(BaseModel):
    delayed_after_seconds: float = Field(gt=0)
    stale_after_seconds: float = Field(gt=0)


class ComponentProfile(BaseModel):
    component_id: str
    purpose: str = ""
    responsibility: str = ""
    expected_input: str = ""
    expected_output: str = ""
    failure_impact: str = ""
    notes: str = ""
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


class ComponentProfileUpdate(BaseModel):
    purpose: str = ""
    responsibility: str = ""
    expected_input: str = ""
    expected_output: str = ""
    failure_impact: str = ""
    notes: str = ""


class EvaluationCriterion(BaseModel):
    id: int
    component_id: str
    name: str
    description: str = ""
    criterion_type: CriterionType
    expected_value: Optional[str] = None
    weight: float = 1.0
    enabled: bool = True
    created_at: float
    updated_at: float


class CriterionCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    criterion_type: CriterionType
    expected_value: Optional[str] = None
    weight: float = 1.0
    enabled: bool = True


class CriterionUpdate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    criterion_type: CriterionType
    expected_value: Optional[str] = None
    weight: float = 1.0
    enabled: bool = True


class EvaluationResult(BaseModel):
    id: int
    trace_id: str
    component_id: str
    criterion_id: int
    status: EvaluationStatus
    score: Optional[float] = None
    reason: str = ""
    actual_output: Optional[str] = None
    expected_value: Optional[str] = None
    created_at: float


class GenerationRunCreate(BaseModel):
    component_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)


class GenerationRun(BaseModel):
    id: int
    system_id: int
    component_id: str
    trace_id: str
    objective: str
    input_json: Optional[Any] = None
    current_output: Optional[str] = None
    generated_code: str = ""
    generation_notes: str = ""
    candidate_output: Optional[str] = None
    execution_error: Optional[str] = None
    llm_verdict: GenerationVerdict = "unknown"
    llm_reason: str = ""
    llm_risks: str = ""
    llm_recommendation: str = ""
    created_at: float


class RepositorySnapshot(BaseModel):
    repo_path: str
    commit_sha: str
    included_paths: List[str] = Field(default_factory=list)
    excluded_paths: List[str] = Field(default_factory=list)
    read_policy: Literal["committed_files_only"] = "committed_files_only"
    status: Literal["not_configured", "ready", "indexing", "failed"] = "not_configured"


SourceType = Literal["documentation", "source", "test", "configuration"]
InclusionStatus = Literal[
    "indexed", "metadata_only", "too_large", "binary", "excluded", "unsupported"
]
SnapshotStatus = Literal["not_configured", "indexing", "ready", "failed"]
# Issue #369: freshness is a SECOND, independent axis over the same snapshot.
# `SnapshotStatus` answers "did the analysis finish" (`ready`); this answers
# "does the pinned commit still equal HEAD" (`current`). Rendering one as the
# other is the bug this issue fixes -- never collapse them.
SnapshotFreshnessState = Literal["current", "stale", "unknown"]
SnapshotPreflightCheckId = Literal[
    "snapshot_processing",
    "commit_pinned",
    "freshness",
    "symbol_index",
    "understanding",
]
SnapshotPreflightCheckStatus = Literal["ok", "attention", "blocking", "unknown"]
SnapshotPreflightVerdict = Literal["ready", "attention", "blocked"]
IntelligenceRunStatus = Literal["pending", "completed", "failed"]
DecisionMethod = Literal["deterministic", "reasoning_llm", "manual"]
# How a single hierarchy claim was produced. Kept distinct from the audit
# DecisionMethod so source-authored facts stay visibly separate from
# reasoning-model interpretations (Issue #56).
ProvenanceKind = Literal["source_authored", "structural", "reasoning_llm", "manual"]


class RepositoryConfigUpdate(BaseModel):
    repo_path: str = Field(..., min_length=1)
    include_patterns: List[str] = Field(default_factory=lambda: ["README.md", "docs/**", "src/**", "tests/**"])
    exclude_patterns: List[str] = Field(default_factory=lambda: [".env", "secrets/**", "data/**", "*.pem", "*.key", "credentials.*"])


class RepositoryCandidateOut(BaseModel):
    name: str
    path: str


class RepositoryConfigOut(BaseModel):
    system_id: int
    repo_path: str
    include_patterns: List[str]
    exclude_patterns: List[str]
    created_at: float
    updated_at: float


class SnapshotFileOut(BaseModel):
    path: str
    source_type: SourceType
    size_bytes: int
    inclusion_status: InclusionStatus = "indexed"
    exclusion_reason: str = ""


class SnapshotOut(BaseModel):
    id: int
    system_id: int
    repo_path: str
    commit_sha: str
    status: SnapshotStatus
    file_count: int
    total_size: int
    indexed_size: int = 0
    metadata_only_count: int = 0
    warnings: List[str] = Field(default_factory=list)
    error_summary: Optional[str] = None
    created_at: float
    completed_at: Optional[float] = None
    files: List[SnapshotFileOut] = Field(default_factory=list)
    # Issue #369: the freshness axis, independent of `status`. Populated by the
    # list endpoint (which resolves HEAD once); `None` means "not evaluated in
    # this response", never "current".
    freshness: Optional[SnapshotFreshnessState] = None
    # Exactly one snapshot per System is the recommended one (the latest ready
    # snapshot). Every other snapshot is a reproduction-only choice.
    is_recommended: bool = False


class SnapshotRefOut(BaseModel):
    id: int
    commit_sha: str
    status: str
    created_at: float
    freshness: Optional[SnapshotFreshnessState] = None


class SnapshotPreflightCheckOut(BaseModel):
    """One finite preflight check result (Issue #369)."""

    check_id: SnapshotPreflightCheckId
    status: SnapshotPreflightCheckStatus
    summary: str
    detail: str
    remediation: str = ""


class SnapshotPreflightOut(BaseModel):
    """Shared preflight for candidate generation / Replay / Experiment.

    One server evaluation rendered by every surface, so the three cannot
    disagree about whether a snapshot may be used.
    """

    snapshot_id: Optional[int] = None
    # "Did the analysis finish" -- the existing snapshot status vocabulary.
    processing_state: Optional[SnapshotStatus] = None
    # "Does the pinned commit still equal HEAD" -- a separate axis.
    freshness: SnapshotFreshnessState = "unknown"
    commit_sha: Optional[str] = None
    head_sha: Optional[str] = None
    head_relation: Literal["same", "behind", "diverged", "unknown"] = "unknown"
    commits_behind: Optional[int] = None
    verdict: SnapshotPreflightVerdict = "blocked"
    checks: List[SnapshotPreflightCheckOut] = Field(default_factory=list)
    recommended_snapshot_id: Optional[int] = None
    recommended_snapshot_commit_sha: Optional[str] = None
    recommended_snapshot_freshness: SnapshotFreshnessState = "unknown"
    is_recommended: bool = False
    requires_stale_acknowledgement: bool = False
    stale_continuation_note: Optional[str] = None


# Issue #372: Replay readiness, evaluated before a candidate is generated.
ReplayReadinessStatus = Literal["ok", "attention", "blocking"]
ReplayReadinessVerdict = Literal["ready", "attention", "blocked"]


class ReplayReadinessCountsOut(BaseModel):
    """How a set of traces splits across the finite replayability values.

    `not_captured` (the component never opted into `replay_capture`) is kept
    separate from `unreplayable` (capture was attempted and failed): the
    remediation differs, so merging them would give the wrong instruction.
    """

    total: int
    replayable: int
    partial: int
    unreplayable: int
    not_captured: int
    #: replayable + partial — the traces that can produce a comparison at all.
    usable: int


class ReplayReadinessCheckOut(BaseModel):
    check_id: str
    status: ReplayReadinessStatus
    summary: str
    detail: str
    remediation: str


class ReplayTraceReadinessOut(BaseModel):
    trace_id: str
    replayability: Literal["replayable", "partial", "unreplayable", "not_captured"]
    primary_reason: Optional[str] = None


class ReplayReadinessOut(BaseModel):
    component_id: str
    snapshot_id: Optional[int] = None
    #: Every trace of the component.
    counts: ReplayReadinessCountsOut
    #: Only the traces a run would actually use (the auto-selected window, or
    #: the explicitly chosen ids).
    selected: ReplayReadinessCountsOut
    selection_limit: int
    selection_is_automatic: bool
    verdict: ReplayReadinessVerdict
    checks: List[ReplayReadinessCheckOut] = Field(default_factory=list)
    traces: List[ReplayTraceReadinessOut] = Field(default_factory=list)


class StaleSnapshotAck(BaseModel):
    """The developer's explicit decision to continue on a stale snapshot.

    ``decision_method: manual`` -- an LLM or heuristic never supplies this.
    The reason is persisted on the record that consumes the snapshot.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class RepositoryStatusOut(BaseModel):
    """Repository refresh hub state (Issue #158).

    Read-only summary that lets the dashboard show, in one place, whether the
    latest analysis is stale relative to the repository's current HEAD and what
    step to take next. Reading HEAD / working-tree status never mutates the
    target repository (Principle 5).
    """

    configured: bool
    repo_path: Optional[str] = None
    # Current committed HEAD of the configured repository (read-only rev-parse).
    current_head: Optional[str] = None
    head_error: Optional[str] = None
    working_tree_dirty: Optional[bool] = None
    dirty_file_count: int = 0
    dirty_sample: List[str] = Field(default_factory=list)
    # Newest snapshot regardless of index/build state.
    latest_snapshot: Optional[SnapshotRefOut] = None
    # Newest snapshot that also has a completed symbol index.
    latest_indexed_snapshot: Optional[SnapshotRefOut] = None
    # Snapshot the most recent System Understanding build ran against.
    understanding_snapshot_id: Optional[int] = None
    understanding_status: Optional[str] = None
    # True when the latest snapshot's commit differs from current HEAD, so a new
    # snapshot should be created before generating new analysis/patches.
    snapshot_stale: bool = False
    # Finite relationship of the latest ready snapshot to HEAD. A lag count is
    # available only for same/behind; failures and missing commits are unknown.
    head_relation: Literal["same", "behind", "diverged", "unknown"] = "unknown"
    commits_behind: Optional[int] = None
    # True when a ready snapshot exists but has no completed symbol index.
    symbols_stale: bool = False
    next_actions: List[str] = Field(default_factory=list)


RepositoryResyncStatus = Literal[
    "queued",
    "snapshotting",
    "indexing",
    "completed",
    "snapshot_failed",
    "index_failed",
]


class RepositoryResyncJobOut(BaseModel):
    id: int
    system_id: int
    snapshot_id: Optional[int] = None
    status: RepositoryResyncStatus
    error: Optional[str] = None
    stale_capability_count: int = 0
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class IntelligenceRunOut(BaseModel):
    id: int
    system_id: int
    snapshot_id: Optional[int]
    run_type: IntelligenceRunType
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    decision_method: DecisionMethod
    status: IntelligenceRunStatus
    error_details: Optional[str] = None
    is_mock: bool = False
    started_at: float
    completed_at: Optional[float] = None
    # Issue #286: budget usage for run_type="investigation" rows only (the
    # read-only Investigation Agent's deterministic budget accounting).
    # None for every other run_type.
    budget_files_read: Optional[int] = None
    budget_chars_read: Optional[int] = None
    budget_llm_calls: Optional[int] = None
    budget_elapsed_seconds: Optional[float] = None


class FeatureEvidence(BaseModel):
    path: str
    start_line: int = 0
    end_line: int = 0
    summary: str = ""


class FeatureCodeLink(BaseModel):
    path: str
    symbol: str
    kind: str = "function"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    decision_method: Literal["deterministic", "reasoning_llm", "manual"] = "manual"


class FeatureProfile(BaseModel):
    feature_id: str
    name: str
    summary: str
    user_value: str
    success_criteria: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    evidence: List[FeatureEvidence] = Field(default_factory=list)
    code_links: List[FeatureCodeLink] = Field(default_factory=list)
    decision_method: Literal["deterministic", "reasoning_llm", "manual"] = "manual"


class ProbePoint(BaseModel):
    component_id: str
    feature_id: str
    path: str
    symbol: str
    reason: str
    recommended_mode: Mode = "trace"
    side_effect_risk: Literal["low", "medium", "high"] = "low"
    status: Literal["proposed", "approved", "rejected"] = "proposed"


class ProbePlan(BaseModel):
    feature_id: str
    objective: str
    probe_points: List[ProbePoint] = Field(default_factory=list)
    avoid_probe_points: List[str] = Field(default_factory=list)
    decision_method: Literal["deterministic", "reasoning_llm", "manual"] = "manual"


class ExperimentVariant(BaseModel):
    variant_id: str
    label: str
    status: Literal["planned", "running", "completed", "failed"] = "planned"
    patch_summary: Optional[str] = None


class ExperimentSummary(BaseModel):
    experiment_id: str
    feature_id: str
    objective: str
    baseline_commit: str
    status: Literal["draft", "running", "completed", "failed"] = "draft"
    variants: List[ExperimentVariant] = Field(default_factory=list)
    metrics: List[str] = Field(default_factory=list)
    interpretation_method: Literal["deterministic", "reasoning_llm", "manual"] = "manual"


ExperimentStatus = Literal["draft", "running", "completed", "failed"]
ExperimentVariantStatus = Literal[
    "planned", "running", "completed", "failed", "invalid_patch", "timed_out"
]
ExperimentAnalysisStatus = Literal[
    "pending", "completed", "analysis_failed", "not_requested"
]


class ExperimentExecutionConfig(BaseModel):
    install_commands: List[str] = Field(default_factory=list)
    test_commands: List[str] = Field(..., min_length=1)
    smoke_commands: List[str] = Field(default_factory=list)
    workload_commands: List[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    network: Literal[False] = False
    env: dict[str, str] = Field(default_factory=dict)
    result_artifact_path: str = ".probe-agent/experiment-result.json"
    artifact_retention_seconds: int = Field(default=86400, ge=0)


class ExperimentVariantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=200)
    patch_text: str = Field(..., min_length=1, max_length=1_000_000)
    source: str = Field(default="manual", max_length=100)
    risk_note: str = Field(default="", max_length=2000)


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: str = Field(..., min_length=1, max_length=200)
    objective: str = Field(..., min_length=1, max_length=5000)
    snapshot_id: int
    variants: List[ExperimentVariantCreate] = Field(
        ..., min_length=2, max_length=10
    )
    # Issue #369: required when the chosen snapshot is definitively behind
    # HEAD. Continuing on an older snapshot is legitimate (reproduction runs),
    # but it is the developer's decision, so it is recorded on the experiment
    # rather than inferred. `decision_method: manual`.
    stale_snapshot_reason: Optional[str] = Field(None, min_length=1, max_length=1000)


class ExperimentCommandOut(BaseModel):
    id: int
    phase: str
    command: str
    exit_code: int
    duration_ms: float
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False


class ExperimentVariantResultOut(BaseModel):
    id: int
    variant_key: str
    label: str
    is_baseline: bool
    patch_text: str = ""
    patch_hash: str
    source: str
    risk_note: str = ""
    status: ExperimentVariantStatus
    error: Optional[str] = None
    workspace_path: Optional[str] = None
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    commands: List[ExperimentCommandOut] = Field(default_factory=list)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class ExperimentAnalysisOut(BaseModel):
    status: ExperimentAnalysisStatus
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    schema_version: Optional[str] = None
    decision_method: Optional[DecisionMethod] = None
    narrative: Optional[str] = None
    recommendation_variant_key: Optional[str] = None
    recommendation_reason: Optional[str] = None
    risks: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: Optional[float] = None


class ExperimentDecisionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["adopted", "rejected", "needs_more_data", "undecided"]
    variant_key: Optional[str] = Field(default=None, max_length=100)
    note: str = ""


class ExperimentOut(BaseModel):
    id: int
    system_id: int
    feature_id: str
    objective: str
    snapshot_id: int
    baseline_commit: str
    config_revision: str
    execution: ExperimentExecutionConfig
    status: ExperimentStatus
    error: Optional[str] = None
    human_decision: str = "undecided"
    human_decision_variant_key: Optional[str] = None
    human_decision_note: str = ""
    variants: List[ExperimentVariantResultOut] = Field(default_factory=list)
    comparison: dict[str, Any] = Field(default_factory=dict)
    analysis: ExperimentAnalysisOut
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class SystemProfileDraftOut(BaseModel):
    id: int
    system_id: int
    intelligence_run_id: int
    snapshot_id: int
    name: str = ""
    purpose: str = ""
    target_users: List[str] = Field(default_factory=list)
    stakeholder_value: str = ""
    constraints: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    evidence: List[FeatureEvidence] = Field(default_factory=list)
    is_mock: bool = False
    created_at: float


class FeatureDraftOut(BaseModel):
    id: int
    system_id: int
    intelligence_run_id: int
    snapshot_id: int
    feature_id: str
    name: str
    summary: str
    user_value: str
    success_criteria: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    evidence: List[FeatureEvidence] = Field(default_factory=list)
    decision_method: DecisionMethod = "reasoning_llm"
    is_mock: bool = False
    created_at: float


class DraftGenerationResult(BaseModel):
    intelligence_run: IntelligenceRunOut
    system_profile_draft: Optional[SystemProfileDraftOut] = None
    feature_drafts: List[FeatureDraftOut] = Field(default_factory=list)


class LatestDraftsOut(BaseModel):
    system_id: int
    snapshot: Optional[SnapshotOut] = None
    intelligence_run: Optional[IntelligenceRunOut] = None
    system_profile_draft: Optional[SystemProfileDraftOut] = None
    feature_drafts: List[FeatureDraftOut] = Field(default_factory=list)


SymbolKind = Literal["module", "class", "function", "async_function"]
LinkSource = Literal["reasoning_llm", "manual"]
LinkReviewStatus = Literal["proposed", "accepted", "rejected"]


SourceMetadataElementType = Literal[
    "system", "core", "capability", "element", "supporting", "boundary"
]
SourceMetadataOperationKind = Literal[
    "analysis", "read", "write", "mutation", "io", "orchestration",
    "validation", "other",
]


class SourceMetadataOut(BaseModel):
    """Author-written, source-anchored explanation copied from a docstring.

    These facts are deterministic / source-authored and are kept separate from
    reasoning-model interpretations.  ``origin`` is always ``source_authored``.
    """

    start_line: int
    end_line: int
    raw_block: str
    role: Optional[str] = None
    capability: Optional[str] = None
    element_type: Optional[SourceMetadataElementType] = None
    system_purpose: Optional[str] = None
    operation_kind: Optional[SourceMetadataOperationKind] = None
    consumers: List[str] = Field(default_factory=list)
    state_effects: List[str] = Field(default_factory=list)
    probe_value: Optional[str] = None
    origin: Literal["source_authored"] = "source_authored"
    # sha256 of the extracted explanation block (Issue #55); change signal only.
    explanation_hash: Optional[str] = None


class CodeSymbolOut(BaseModel):
    id: int
    snapshot_id: int
    system_id: int
    path: str
    qualified_name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    decorators: List[str] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)
    docstring: Optional[str] = None
    is_test: bool = False
    is_pydantic_model: bool = False
    route_path: Optional[str] = None
    route_method: Optional[str] = None
    component_id: Optional[str] = None
    source_metadata: Optional[SourceMetadataOut] = None
    # Source-hash provenance (Issue #55). All computed from the pinned snapshot;
    # equality is only a change signal, not semantic equivalence.
    file_content_hash: Optional[str] = None
    symbol_source_hash: Optional[str] = None
    symbol_body_hash: Optional[str] = None


class ExplanationAnchorOut(BaseModel):
    """A single source anchor an explanation depends on (Issue #55).

    Bundles the deterministic provenance (file, symbol span, and hash types)
    that downstream drift features compare against a newer snapshot.
    """

    id: int
    snapshot_id: int
    system_id: int
    metadata_id: int
    symbol_id: int
    path: str
    qualified_name: str
    start_line: int
    end_line: int
    file_content_hash: Optional[str] = None
    symbol_source_hash: Optional[str] = None
    symbol_body_hash: Optional[str] = None
    explanation_hash: Optional[str] = None


class ExplanationAnchorsOut(BaseModel):
    system_id: int
    snapshot_id: int
    anchor_count: int
    anchors: List[ExplanationAnchorOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Source-backed capability hierarchy (Issue #56)
# ---------------------------------------------------------------------------


class HierarchyProvenanceOut(BaseModel):
    """Provenance for a single hierarchy claim.

    ``provenance_kind`` distinguishes source-authored explanation, deterministic
    structural fact, and reasoning-model interpretation. ``decision_method`` is
    the audit enum. Hashes tie the claim to the pinned snapshot (#55).
    """

    provenance_kind: ProvenanceKind
    decision_method: DecisionMethod
    path: Optional[str] = None
    qualified_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    file_content_hash: Optional[str] = None
    symbol_source_hash: Optional[str] = None
    explanation_hash: Optional[str] = None
    symbol_id: Optional[int] = None
    entrypoint_id: Optional[int] = None
    # Stable logical entrypoint reference (#62). ``entrypoint_id`` above is the
    # snapshot-local DB row id and is not safe for cross-snapshot linking. These
    # carry the logical (type, id) so the dashboard can open the entrypoint in
    # Flow Explorer without re-resolving the DB id.
    entrypoint_type: Optional[str] = None
    entrypoint_ref: Optional[str] = None
    feature_id: Optional[str] = None
    system_profile_draft_id: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class SupportingElementOut(BaseModel):
    id: int
    name: str
    summary: str = ""
    supporting_kind: Optional[str] = None
    provenance: HierarchyProvenanceOut


class CapabilityElementOut(BaseModel):
    id: int
    name: str
    summary: str = ""
    element_role: Optional[str] = None
    operation_kind: Optional[str] = None
    probe_value: Optional[str] = None
    classification: Optional[str] = None  # classified | unclassified
    provenance: HierarchyProvenanceOut


class CapabilityOut(BaseModel):
    id: int
    capability_key: Optional[str] = None
    name: str
    summary: str = ""
    provenance: HierarchyProvenanceOut
    elements: List[CapabilityElementOut] = Field(default_factory=list)
    supporting_elements: List[SupportingElementOut] = Field(default_factory=list)


class CapabilityPurposeOut(BaseModel):
    id: int
    name: str
    summary: str = ""
    provenance: HierarchyProvenanceOut


class CapabilityHierarchyOut(BaseModel):
    system_id: int
    snapshot_id: int
    intelligence_run: Optional[IntelligenceRunOut] = None
    purpose: Optional[CapabilityPurposeOut] = None
    capabilities: List[CapabilityOut] = Field(default_factory=list)
    unclassified_elements: List[CapabilityElementOut] = Field(default_factory=list)
    unattached_supporting: List[SupportingElementOut] = Field(default_factory=list)
    is_mock: bool = False


# ---------------------------------------------------------------------------
# Explanation drift (Issue #57)
# ---------------------------------------------------------------------------

# Anchor-level uses fresh/stale/missing_source/unknown; aggregate levels add
# partially_stale. Hash drift is a review trigger, not a correctness verdict.
DriftStatus = Literal[
    "fresh", "partially_stale", "stale", "missing_source", "unknown"
]


class AnchorDriftOut(BaseModel):
    node_id: int
    node_type: str
    name: str
    path: Optional[str] = None
    qualified_name: Optional[str] = None
    entrypoint_id: Optional[int] = None
    status: DriftStatus
    changed_hashes: List[str] = Field(default_factory=list)
    captured_file_content_hash: Optional[str] = None
    captured_symbol_source_hash: Optional[str] = None
    captured_explanation_hash: Optional[str] = None
    current_file_content_hash: Optional[str] = None
    current_symbol_source_hash: Optional[str] = None
    current_explanation_hash: Optional[str] = None


class DriftCountsOut(BaseModel):
    total: int = 0
    fresh: int = 0
    stale: int = 0
    missing: int = 0
    unknown: int = 0
    symbol_deps_total: int = 0
    symbol_deps_changed: int = 0
    file_deps_total: int = 0
    file_deps_changed: int = 0
    explanation_blocks_total: int = 0
    explanation_blocks_changed: int = 0
    missing_anchors: int = 0
    mismatch_ratio: float = 0.0


class CapabilityDriftOut(BaseModel):
    capability_id: int
    capability_key: Optional[str] = None
    name: str
    status: DriftStatus
    counts: DriftCountsOut
    elements: List[AnchorDriftOut] = Field(default_factory=list)
    supporting_elements: List[AnchorDriftOut] = Field(default_factory=list)


class CapabilityHierarchyDriftOut(BaseModel):
    system_id: int
    base_snapshot_id: int
    target_snapshot_id: int
    intelligence_run: Optional[IntelligenceRunOut] = None
    status: DriftStatus
    counts: DriftCountsOut
    target_indexed: bool = True
    purpose: Optional[AnchorDriftOut] = None
    capabilities: List[CapabilityDriftOut] = Field(default_factory=list)
    unclassified_elements: List[AnchorDriftOut] = Field(default_factory=list)
    unattached_supporting: List[AnchorDriftOut] = Field(default_factory=list)
    is_review_recommended: bool = False
    review_note: Optional[str] = None


class SymbolIndexWarningOut(BaseModel):
    path: str
    message: str


class SymbolIndexOut(BaseModel):
    snapshot_id: int
    system_id: int
    symbol_count: int
    warning_count: int
    symbols: List[CodeSymbolOut] = Field(default_factory=list)
    warnings: List[SymbolIndexWarningOut] = Field(default_factory=list)
    intelligence_run: Optional[IntelligenceRunOut] = None


class FeatureCodeLinkOut(BaseModel):
    id: int
    system_id: int
    snapshot_id: int
    intelligence_run_id: int
    feature_id: str
    symbol: CodeSymbolOut
    relation_reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: LinkSource
    review_status: LinkReviewStatus
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    is_stale: bool = False
    created_at: float
    updated_at: float


class FeatureCodeLinksOut(BaseModel):
    system_id: int
    snapshot_id: Optional[int] = None
    intelligence_run: Optional[IntelligenceRunOut] = None
    links: List[FeatureCodeLinkOut] = Field(default_factory=list)
    is_mock: bool = False


class LinkReviewUpdate(BaseModel):
    review_status: LinkReviewStatus


ProbePointStatus = Literal["proposed", "approved", "rejected"]
ProbePlanStatus = Literal["proposed", "approved", "rejected"]


class ProbePointOut(BaseModel):
    id: int
    plan_id: int
    system_id: int
    component_id: str
    feature_id: str
    path: str
    symbol: str
    line_start: int
    line_end: int
    reason: str
    recommended_mode: str
    side_effect_risk: Literal["low", "medium", "high"]
    replayability: str
    denylist_hit: Optional[str] = None
    status: ProbePointStatus = "proposed"
    created_at: float
    updated_at: float


class ProbePlanOut(BaseModel):
    id: int
    system_id: int
    snapshot_id: int
    intelligence_run_id: int
    feature_id: str
    objective: str
    status: ProbePlanStatus
    origin: str = "manual"
    avoid_reasons: List[str] = Field(default_factory=list)
    probe_points: List[ProbePointOut] = Field(default_factory=list)
    intelligence_run: Optional[IntelligenceRunOut] = None
    is_mock: bool = False
    created_at: float
    updated_at: float


class ProbePointStatusUpdate(BaseModel):
    status: ProbePointStatus


class ProbePatchApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: Literal[True]
    expected_commit_sha: str = Field(..., min_length=7, max_length=64)


class ValidationCommandOut(BaseModel):
    id: int
    command: str
    exit_code: int
    duration_ms: float
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False


class ValidationRunOut(BaseModel):
    id: int
    patch_id: int
    system_id: int
    variant: str
    worktree_path: str
    overall_success: bool
    total_duration_ms: float
    trace_received: Optional[bool] = None
    trace_status: str = "not_checked"
    network_isolation: str = "not_requested"
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None
    commands: List[ValidationCommandOut] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: float


class ProbePatchOut(BaseModel):
    id: int
    plan_id: int
    system_id: int
    snapshot_id: int
    commit_sha: str
    diff: str
    worktree_path: str = ""
    skipped: List[str] = Field(default_factory=list)
    status: str
    error: Optional[str] = None
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None
    apply_status: str = "not_applied"
    apply_error: Optional[str] = None
    applied_at: Optional[float] = None
    applied_by_user_id: Optional[int] = None
    validation_runs: List[ValidationRunOut] = Field(default_factory=list)
    created_at: float


class ProbePlansListOut(BaseModel):
    system_id: int
    plans: List[ProbePlanOut] = Field(default_factory=list)
    is_mock: bool = False


# ---------------------------------------------------------------------------
# Probe Pattern lifecycle (Issue #168)
# ---------------------------------------------------------------------------

ProbePatternStatus = Literal["active", "stale", "archived", "superseded"]
ProbePatternOrigin = Literal["scan", "probe_plan", "manual"]
ReconcileClassification = Literal[
    "exact_match", "moved_match", "changed_signature",
    "split_or_merged", "missing", "unsafe",
]
ReconcileUserDecision = Literal["pending", "accepted", "rejected"]


class InstrumentedProbeOut(BaseModel):
    path: str
    symbol: str
    line_start: int
    line_end: int
    component_id: Optional[str] = None
    docstring: Optional[str] = None
    linked_plan_id: Optional[int] = None
    linked_feature_id: Optional[str] = None
    linked_objective: Optional[str] = None
    linked_reason: Optional[str] = None
    linked_recommended_mode: Optional[str] = None
    pattern_ids: List[int] = Field(default_factory=list)


class InstrumentationScanOut(BaseModel):
    system_id: int
    snapshot_id: int
    commit_sha: str
    probes: List[InstrumentedProbeOut] = Field(default_factory=list)


class ProbePatternPointIn(BaseModel):
    path: str
    symbol: str
    component_id: str = ""
    reason: str = ""
    recommended_mode: str = "trace"
    side_effect_risk: Literal["low", "medium", "high"] = "low"
    replayability: str = ""


class ProbePatternCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    feature_id: str = ""
    capability: str = ""
    objective: str = ""
    description: str = ""
    origin: ProbePatternOrigin = "manual"
    source_plan_id: Optional[int] = None
    points: List[ProbePatternPointIn] = Field(..., min_length=1)


class ProbePatternUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    feature_id: Optional[str] = None
    capability: Optional[str] = None
    objective: Optional[str] = None
    description: Optional[str] = None
    status: Optional[Literal["active", "archived"]] = None


class ProbePatternPointOut(BaseModel):
    id: int
    pattern_id: int
    system_id: int
    component_id: str
    path: str
    symbol: str
    line_start: int
    line_end: int
    reason: str
    recommended_mode: str
    side_effect_risk: str
    replayability: str
    signature: str = ""
    symbol_source_hash: Optional[str] = None
    symbol_body_hash: Optional[str] = None
    docstring: Optional[str] = None
    status: str = "saved"
    removed_at: Optional[float] = None
    created_at: float
    updated_at: float


class ProbePatternEventOut(BaseModel):
    id: int
    pattern_id: int
    event_type: str
    detail: dict = Field(default_factory=dict)
    created_at: float


class ReconcileEvidenceOut(BaseModel):
    path: str
    start_line: int
    end_line: int
    summary: str = ""


class PatternInvestigationOut(BaseModel):
    summary: str
    recommendation: str
    proposed_target_path: Optional[str] = None
    proposed_target_symbol: Optional[str] = None
    evidence: List[ReconcileEvidenceOut] = Field(default_factory=list)
    is_mock: bool = False
    created_at: float


class ReconcilePointOut(BaseModel):
    id: int
    reconciliation_id: int
    pattern_point_id: int
    classification: ReconcileClassification
    decision_method: Literal["deterministic", "reasoning_llm"]
    target_path: Optional[str] = None
    target_symbol: Optional[str] = None
    target_line_start: Optional[int] = None
    target_line_end: Optional[int] = None
    confidence: float = 0.0
    explanation: str = ""
    hypothesis: str = ""
    question: str = ""
    evidence: List[ReconcileEvidenceOut] = Field(default_factory=list)
    denylist_hit: Optional[str] = None
    body_changed: bool = False
    user_decision: ReconcileUserDecision = "pending"
    decided_at: Optional[float] = None
    investigation: Optional[PatternInvestigationOut] = None
    created_at: float
    updated_at: float


class ProbePatternReconciliationOut(BaseModel):
    id: int
    pattern_id: int
    system_id: int
    snapshot_id: int
    commit_sha: str
    intelligence_run_id: Optional[int] = None
    status: str
    error: Optional[str] = None
    summary: Dict[str, int] = Field(default_factory=dict)
    points: List[ReconcilePointOut] = Field(default_factory=list)
    intelligence_run: Optional[IntelligenceRunOut] = None
    is_mock: bool = False
    created_at: float


class ProbeRemovalPatchOut(BaseModel):
    id: int
    pattern_id: int
    system_id: int
    snapshot_id: int
    commit_sha: str
    diff: str
    skipped: List[str] = Field(default_factory=list)
    status: str
    error: Optional[str] = None
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None
    apply_status: str = "not_applied"
    apply_error: Optional[str] = None
    applied_at: Optional[float] = None
    applied_by_user_id: Optional[int] = None
    created_at: float


class ProbeRemovalPatchCreateRequest(BaseModel):
    point_ids: Optional[List[int]] = None


class ProbeRemovalPatchApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: Literal[True]
    expected_commit_sha: str = Field(..., min_length=7, max_length=64)


class ReconcileDecisionRequest(BaseModel):
    decision: Literal["accepted", "rejected"]


class CreatePlanFromReconcileRequest(BaseModel):
    objective: Optional[str] = None


class ProbePatternOut(BaseModel):
    id: int
    system_id: int
    name: str
    feature_id: str = ""
    capability: str = ""
    objective: str = ""
    description: str = ""
    status: ProbePatternStatus = "active"
    origin: ProbePatternOrigin = "manual"
    source_plan_id: Optional[int] = None
    source_snapshot_id: Optional[int] = None
    source_commit_sha: str = ""
    superseded_by_id: Optional[int] = None
    last_used_at: Optional[float] = None
    last_reconciled_at: Optional[float] = None
    point_count: int = 0
    removed_point_count: int = 0
    points: List[ProbePatternPointOut] = Field(default_factory=list)
    events: List[ProbePatternEventOut] = Field(default_factory=list)
    latest_reconciliation: Optional[ProbePatternReconciliationOut] = None
    pending_decision_count: int = 0
    created_at: float
    updated_at: float


class ProbePatternsListOut(BaseModel):
    system_id: int
    patterns: List[ProbePatternOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Flow graph explorer (Issue #43)
# ---------------------------------------------------------------------------


# Dispatch types accepted by the flow-graph builder, plus the category aliases
# (api/function) the API normalises for convenience (Issue #48).
FlowEntrypointType = Literal[
    "http_route", "public_function", "message_queue", "scheduled_job", "cli",
    "api", "function",
]
FlowEntrypointCategory = Literal[
    "api", "message_queue", "scheduled_job", "cli", "function",
]
FlowEdgeResolution = Literal["resolved", "inferred", "unresolved"]


class EvidenceRefOut(BaseModel):
    path: str
    start_line: int
    end_line: int
    summary: str = ""


class ProbePreviewOut(BaseModel):
    recommended_mode: str
    captured_data: List[str] = Field(default_factory=list)
    redaction: List[str] = Field(default_factory=list)
    replayability: str = ""
    estimated_event_volume: str = ""
    side_effect_risk: Literal["low", "medium", "high"] = "low"
    denylist_hit: Optional[str] = None


class FlowEntrypointOut(BaseModel):
    entrypoint_type: FlowEntrypointType
    entrypoint_id: str
    label: str
    path: str
    qualified_name: str
    line_start: int
    line_end: int
    component_id: Optional[str] = None
    route_method: Optional[str] = None
    route_path: Optional[str] = None
    # Issue #48: backend-entrypoint classification metadata.
    category: FlowEntrypointCategory = "function"
    framework: Optional[str] = None
    operation: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: List[EvidenceRefOut] = Field(default_factory=list)
    # "deterministic" (AST) or "reasoning_llm" (extracted via an LLM-generated
    # regex from "Scan API definitions").
    source: str = "deterministic"


class EntrypointCountsOut(BaseModel):
    api: int = 0
    message_queue: int = 0
    scheduled_job: int = 0
    cli: int = 0
    function: int = 0


class FlowEntrypointsOut(BaseModel):
    system_id: int
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    # Issue #51: Flow Explorer is backend-entrypoint-first. ``entrypoints``
    # carries only backend entrypoints (api/message_queue/scheduled_job/cli);
    # the public-function fallback is returned separately in ``functions`` and
    # is only populated when explicitly requested (Advanced). ``total`` is the
    # backend entrypoint count before any category/q filtering ("N of M").
    total: int = 0
    entrypoints: List[FlowEntrypointOut] = Field(default_factory=list)
    functions: List[FlowEntrypointOut] = Field(default_factory=list)
    counts: EntrypointCountsOut = Field(default_factory=EntrypointCountsOut)
    indexed_function_count: int = 0
    has_backend_entrypoints: bool = False
    frameworks: List[str] = Field(default_factory=list)
    # Deterministic reasons surfaced when backend discovery is thin, so the UI
    # never silently dumps a giant raw-function list as the intended UX.
    diagnostics: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API role cards (Issue #58) — Flow Explorer developer context.
# Consumes the #56 capability hierarchy and #57 drift; invents no new semantics.
# ---------------------------------------------------------------------------


class ApiRoleCardOut(BaseModel):
    # Identity: joins to FlowEntrypointOut by (entrypoint_type, entrypoint_id).
    entrypoint_type: str
    entrypoint_id: str
    label: str
    category: FlowEntrypointCategory = "function"
    route_method: Optional[str] = None
    route_path: Optional[str] = None
    operation: Optional[str] = None
    framework: Optional[str] = None
    source: str = "deterministic"  # deterministic (AST) | reasoning_llm (scan)
    # Whether a handler symbol resolved -> whether an executable flow graph is
    # supported. LLM-scan entries without a handler must not imply graph support.
    handler_resolved: bool = False
    classification: Literal["classified", "unclassified", "unknown"] = "unknown"
    capability_key: Optional[str] = None
    capability_name: Optional[str] = None
    element_type: Optional[str] = None  # core | element | supporting (from #54)
    role: Optional[str] = None
    operation_kind: Optional[str] = None
    probe_value: Optional[str] = None
    consumers: List[str] = Field(default_factory=list)
    state_effects: List[str] = Field(default_factory=list)
    boundaries: List[str] = Field(default_factory=list)
    flows_through: List[str] = Field(default_factory=list)
    # Distinct provenance kinds backing the card, e.g. ["source_authored",
    # "structural"] or ["reasoning_llm", "structural"].
    provenance_kinds: List[ProvenanceKind] = Field(default_factory=list)
    # Drift (#57). Capability-level for classified cards, node-level otherwise.
    drift_status: Optional[
        Literal["fresh", "partially_stale", "stale", "missing_source", "unknown"]
    ] = None
    drift_changed_anchors: int = 0
    drift_total_anchors: int = 0
    drift_review_recommended: bool = False
    # Review attention for the card itself (distinct from freshness): set when
    # an LLM-scan entry has no resolved handler/flow.
    review_needed: bool = False
    review_reason: Optional[str] = None
    node_id: Optional[int] = None


class ApiRoleCardsOut(BaseModel):
    system_id: int
    snapshot_id: Optional[int] = None
    hierarchy_run: Optional[IntelligenceRunOut] = None
    base_snapshot_id: Optional[int] = None
    target_snapshot_id: Optional[int] = None
    drift_available: bool = False
    cards: List[ApiRoleCardOut] = Field(default_factory=list)


class RefreshProposalRequest(BaseModel):
    """Identify the stale hierarchy node / API role card to refresh.

    Provide either ``node_id`` (a capability hierarchy node) or the logical
    ``(entrypoint_type, entrypoint_id)`` of an API role card.
    """

    node_id: Optional[int] = None
    entrypoint_type: Optional[str] = None
    entrypoint_id: Optional[str] = None
    target_snapshot_id: Optional[int] = None


class ExplanationRefreshProposalOut(BaseModel):
    id: Optional[int] = None
    node_id: Optional[int] = None
    node_type: str = ""
    name: str = ""
    entrypoint_type: Optional[str] = None
    entrypoint_id: Optional[str] = None
    path: Optional[str] = None
    qualified_name: Optional[str] = None
    drift_status: DriftStatus = "unknown"
    drift_reason: str = ""
    changed_hashes: List[str] = Field(default_factory=list)
    # Source-authored explanation as it exists in the target repo (unchanged).
    old_explanation: str = ""
    # Reasoning-model suggestion. Never written to the target repository.
    proposed_explanation: Optional[str] = None
    proposed_metadata: Optional[Dict[str, Any]] = None
    summary_of_changes: Optional[str] = None
    confidence: Optional[float] = None
    captured_file_content_hash: Optional[str] = None
    captured_symbol_source_hash: Optional[str] = None
    captured_explanation_hash: Optional[str] = None
    current_file_content_hash: Optional[str] = None
    current_symbol_source_hash: Optional[str] = None
    current_explanation_hash: Optional[str] = None
    status: Literal["proposed", "failed"] = "proposed"
    is_mock: bool = False
    provider: str = ""
    model: str = ""
    decision_method: str = "reasoning_llm"
    created_at: Optional[float] = None


class ExplanationRefreshOut(BaseModel):
    system_id: int
    base_snapshot_id: Optional[int] = None
    target_snapshot_id: Optional[int] = None
    intelligence_run: Optional[IntelligenceRunOut] = None
    status: Literal["proposed", "failed"] = "proposed"
    error: Optional[str] = None
    # Always true for proposals: a developer must review and apply to source.
    review_required: bool = True
    review_note: str = ""
    proposal: Optional[ExplanationRefreshProposalOut] = None


class ExplanationRefreshListOut(BaseModel):
    system_id: int
    review_note: str = ""
    proposals: List[ExplanationRefreshProposalOut] = Field(default_factory=list)


class ApiScanRequest(BaseModel):
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None


class ApiScanPatternOut(BaseModel):
    id: Optional[int] = None
    file_glob: str
    regex: str
    method_group: Optional[str] = None
    path_group: Optional[str] = None
    method_constant: Optional[str] = None
    framework: str
    language: str
    reason: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    match_count: int = 0
    examples: List[EvidenceRefOut] = Field(default_factory=list)


class ApiScanResultOut(BaseModel):
    system_id: int
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    run_id: Optional[int] = None
    status: str = "completed"
    decision_method: str = "reasoning_llm"
    provider: Optional[str] = None
    model: Optional[str] = None
    is_mock: bool = False
    error: Optional[str] = None
    # Reviewable LLM-generated regexes and the entrypoints they extracted.
    patterns: List[ApiScanPatternOut] = Field(default_factory=list)
    extracted_count: int = 0
    frameworks: List[str] = Field(default_factory=list)
    diagnostics: List[str] = Field(default_factory=list)


class FlowNodeOut(BaseModel):
    node_id: str
    node_type: str
    symbol_id: Optional[int] = None
    qualified_name: str
    path: str
    line_start: int
    line_end: int
    component_id: Optional[str] = None
    probe_capabilities: List[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "low"
    denylist_hit: Optional[str] = None
    evidence: List[EvidenceRefOut] = Field(default_factory=list)
    # Phase 2: external boundary classification.
    boundary_kind: Optional[str] = None
    is_external: bool = False
    # Phase 2/3: runtime overlay from real traces.
    trace_count: int = 0
    error_count: int = 0
    evaluation_pass: int = 0
    evaluation_fail: int = 0
    observed: bool = False
    # Issue #46: pre-selection preview metadata (None for external nodes).
    preview: Optional[ProbePreviewOut] = None


class FlowEdgeOut(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: Optional[str] = None
    edge_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    resolution: FlowEdgeResolution
    callee_name: str
    line: int
    evidence: List[EvidenceRefOut] = Field(default_factory=list)
    # Issue #46: pre-selection preview for observing this call boundary.
    preview: Optional[ProbePreviewOut] = None


class CandidateFlowOut(BaseModel):
    flow_id: str
    title: str
    summary: str
    entrypoint_node_id: str
    node_ids: List[str] = Field(default_factory=list)
    node_count: int
    max_depth: int
    confidence: float = Field(ge=0.0, le=1.0)
    unresolved_edge_count: int
    external_boundary_count: int = 0
    observed_node_count: int = 0
    unobserved_node_ids: List[str] = Field(default_factory=list)


class FlowGraphOut(BaseModel):
    system_id: int
    snapshot_id: int
    commit_sha: str
    entrypoint: FlowEntrypointOut
    nodes: List[FlowNodeOut] = Field(default_factory=list)
    edges: List[FlowEdgeOut] = Field(default_factory=list)
    candidate_paths: List[CandidateFlowOut] = Field(default_factory=list)
    diagnostics: List[str] = Field(default_factory=list)
    truncated: bool = False


class FlowGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint_type: FlowEntrypointType
    entrypoint_id: str = Field(..., min_length=1)
    max_depth: int = Field(default=8, ge=1, le=32)
    max_nodes: int = Field(default=100, ge=1, le=500)
    # Issue #46: optional pinning. When provided they must match the latest
    # ready snapshot or the request is rejected as stale (409).
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None


class FlowOverlaySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["entity", "correlation", "flow", "analyzer"]
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    correlation_id: Optional[str] = None
    flow_id: Optional[str] = None
    analyzer_id: Optional[int] = None


class FlowOverlayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint_type: FlowEntrypointType
    entrypoint_id: str = Field(..., min_length=1)
    max_depth: int = Field(default=8, ge=1, le=32)
    max_nodes: int = Field(default=100, ge=1, le=500)
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    selection: FlowOverlaySelection


class FlowOverlayNode(BaseModel):
    node_id: str
    component_id: Optional[str] = None
    observable: bool  # has a component_id (an instrumented probe point)
    observed: bool
    observation_count: int = 0
    last_observed_at: Optional[float] = None


class FlowOverlayEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: Optional[str] = None
    source_component_id: Optional[str] = None
    target_component_id: Optional[str] = None
    observed_transition: bool = False


class FlowDivergence(BaseModel):
    source_component_id: str
    target_component_id: str
    count: int


class FlowOverlayOut(BaseModel):
    selection: Dict[str, Any] = Field(default_factory=dict)
    nodes: List[FlowOverlayNode] = Field(default_factory=list)
    edges: List[FlowOverlayEdge] = Field(default_factory=list)
    divergences: List[FlowDivergence] = Field(default_factory=list)
    observed_component_ids: List[str] = Field(default_factory=list)
    unmatched_component_ids: List[str] = Field(default_factory=list)
    observed_trace_count: int = 0


class FlowProbeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Issue #46: node selections instrument a symbol; edge selections observe
    # a call boundary on the in-repo caller.
    target_type: Literal["node", "edge"] = "node"
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    observation: Literal["input", "output", "boundary"] = "output"
    mode_preference: Literal["trace", "shadow", "off"] = "trace"

    @model_validator(mode="after")
    def _check_target(self) -> "FlowProbeSelection":
        if self.target_type == "node" and not self.node_id:
            raise ValueError("node_id is required for node selections")
        if self.target_type == "edge" and not self.edge_id:
            raise ValueError("edge_id is required for edge selections")
        return self


class ProbePlanFromFlowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint_type: FlowEntrypointType
    entrypoint_id: str = Field(..., min_length=1)
    objective: str = ""
    selections: List[FlowProbeSelection] = Field(..., min_length=1)
    max_depth: int = Field(default=8, ge=1, le=32)
    max_nodes: int = Field(default=100, ge=1, le=500)
    # Issue #46: pin the plan to the graph the user actually reviewed.
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None


Role = Literal["admin", "user"]
TokenKind = Literal["session", "api"]
# Issue #368: a token's lifecycle status is a finite set decided server-side
# from `revoked` + `expires_at` + the current clock (`app/token_status.py`).
# The Dashboard renders it and never re-derives it.
TokenStatus = Literal["active", "expiring_soon", "expired", "revoked"]


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    expires_at: Optional[float] = None


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    role: Role = "user"


class UserOut(BaseModel):
    id: int
    username: str
    role: Role
    is_active: bool
    created_at: float


class MeResponse(BaseModel):
    user: Optional[UserOut] = None
    auth: str = Field(..., description="token | legacy_api_key | anonymous")
    system_id: Optional[int] = None
    transport: str = Field(
        ..., description="authorization | x_api_key | cookie | legacy_api_key | anonymous"
    )


class BootstrapStatusOut(BaseModel):
    """Issue #265: deterministic pre-login / pre-System "phase 0" facts.

    Callable without auth and without a System. Carries no secrets --
    booleans/finite tokens only, never a username, key value, path, or
    hostname.
    """

    admin_exists: bool
    auth_mode: str = Field(..., description="anonymous | user")
    llm_configured: bool
    environment: str = Field(..., description="development | production")


class TokenCreate(BaseModel):
    name: Optional[str] = None
    system_id: Optional[int] = None
    user_id: Optional[int] = Field(
        default=None, description="owner of the token; defaults to the caller"
    )
    expires_in_days: Optional[int] = Field(default=None, ge=1)


class SelfTokenCreate(BaseModel):
    """Token issuance for the caller's own account (no user_id override)."""

    name: Optional[str] = None
    system_id: Optional[int] = None
    expires_in_days: Optional[int] = Field(default=None, ge=1)


class PasswordResetRequest(BaseModel):
    password: str = Field(..., min_length=1)


class RoleUpdate(BaseModel):
    role: Role


class TokenOut(BaseModel):
    id: int
    name: Optional[str] = None
    kind: TokenKind
    user_id: int
    system_id: Optional[int] = None
    revoked: bool
    created_at: float
    expires_at: Optional[float] = None
    status: TokenStatus = Field(
        ...,
        description=(
            "active | expiring_soon | expired | revoked -- Issue #368. Decided "
            "server-side by app/token_status.classify_token_status against the "
            "response's own clock; never re-derive it client-side."
        ),
    )
    expires_in_seconds: Optional[float] = Field(
        default=None,
        description=(
            "Seconds until expiry at the moment `status` was decided, clamped "
            "at 0; None when the token has no expiry."
        ),
    )


class TokenCreateResponse(TokenOut):
    token: str = Field(..., description="raw token, shown only once")


WorkspaceMessageRole = Literal["user", "assistant", "system"]
WorkspaceContextItemType = Literal[
    "feature", "component", "trace", "experiment", "probe_plan", "analyzer_run"
]
WorkspaceProposalStatus = Literal[
    "proposed", "accepted", "rejected", "deferred", "superseded"
]
WorkspaceDecisionType = Literal["accepted", "rejected", "deferred"]


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    focus: str = Field(default="", max_length=500)
    summary: str = Field(default="", max_length=5000)


class WorkspaceOut(BaseModel):
    id: int
    system_id: int
    title: str
    focus: str
    status: str
    summary: str
    created_at: float
    updated_at: float


class WorkspaceContextItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: WorkspaceContextItemType
    item_id: str = Field(..., min_length=1, max_length=200)
    label: str = Field(default="", max_length=300)


class WorkspaceContextItemOut(BaseModel):
    id: int
    workspace_id: int
    item_type: str
    item_id: str
    label: str
    created_at: float


class WorkspaceProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_type: str = Field(..., min_length=1, max_length=100)
    title: str = Field(default="", max_length=300)
    body: dict[str, Any] = Field(default_factory=dict)


class WorkspaceMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: WorkspaceMessageRole
    content: str = Field(..., min_length=1, max_length=20_000)
    context_metadata: dict[str, Any] = Field(default_factory=dict)
    proposals: List[WorkspaceProposalInput] = Field(default_factory=list)


class WorkspaceMessageOut(BaseModel):
    id: int
    workspace_id: int
    role: str
    content: str
    context_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class WorkspaceDecisionOut(BaseModel):
    id: int
    proposal_id: int
    decision: WorkspaceDecisionType
    reason: str
    decided_by_user_id: Optional[int] = None
    created_at: float


class WorkspaceProposalOut(BaseModel):
    id: int
    workspace_id: int
    message_id: Optional[int] = None
    proposal_type: str
    title: str
    body: dict[str, Any] = Field(default_factory=dict)
    status: WorkspaceProposalStatus
    decisions: List[WorkspaceDecisionOut] = Field(default_factory=list)
    created_at: float
    updated_at: float


class WorkspaceProposalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=300)
    body: Optional[dict[str, Any]] = None


class WorkspaceDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=2000)


class WorkspaceDetailOut(WorkspaceOut):
    messages: List[WorkspaceMessageOut] = Field(default_factory=list)
    context_items: List[WorkspaceContextItemOut] = Field(default_factory=list)
    proposals: List[WorkspaceProposalOut] = Field(default_factory=list)


# --- Decision Workspace Context Pack (Issue #36) ---------------------------
#
# Deterministic, no-LLM digests of existing data, scoped to the workspace's
# pinned context items. Every digest carries enough source identifiers to
# trace a finding back to its origin row.

WorkspaceEvidenceSourceType = Literal[
    "feature_draft",
    "feature_code_link",
    "component_profile",
    "trace",
    "evaluation_result",
    "probe_point",
    "experiment_variant",
]


class WorkspaceEvidenceRef(BaseModel):
    source_type: WorkspaceEvidenceSourceType
    source_id: str
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    path: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    summary: str = ""


class WorkspaceSystemSummary(BaseModel):
    system_id: int
    name: str = ""
    environment: str = ""
    purpose: str = ""
    target_users: str = ""


class WorkspaceFocusSummary(BaseModel):
    title: str = ""
    focus: str = ""
    summary: str = ""


class WorkspaceRepositorySummary(BaseModel):
    snapshot_id: int
    commit_sha: str
    repo_path: str
    file_count: int
    status: str


class WorkspaceFeatureDigest(BaseModel):
    feature_id: str
    name: str = ""
    summary: str = ""
    user_value: str = ""
    success_criteria: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    accepted_code_link_count: int = 0
    decision_method: DecisionMethod = "reasoning_llm"
    evidence: List[WorkspaceEvidenceRef] = Field(default_factory=list)


class WorkspaceComponentDigest(BaseModel):
    component_id: str
    purpose: str = ""
    responsibility: str = ""
    expected_input: str = ""
    expected_output: str = ""
    failure_impact: str = ""
    evidence: List[WorkspaceEvidenceRef] = Field(default_factory=list)


class WorkspaceTraceDigest(BaseModel):
    component_id: str
    trace_count: int = 0
    period_start: Optional[float] = None
    period_end: Optional[float] = None
    error_count: int = 0
    eval_failed_count: int = 0
    representative_input: Optional[str] = None
    representative_output: Optional[str] = None
    evidence: List[WorkspaceEvidenceRef] = Field(default_factory=list)


class WorkspaceEvaluationDigest(BaseModel):
    component_id: str
    criterion_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    top_failure_reasons: List[str] = Field(default_factory=list)
    evidence: List[WorkspaceEvidenceRef] = Field(default_factory=list)


class WorkspaceProbePointSummary(BaseModel):
    component_id: str
    symbol: str
    path: str
    recommended_mode: str
    side_effect_risk: str
    status: str


class WorkspaceProbePlanSummary(BaseModel):
    plan_id: int
    feature_id: str
    objective: str = ""
    status: str = ""
    probe_points: List[WorkspaceProbePointSummary] = Field(default_factory=list)
    evidence: List[WorkspaceEvidenceRef] = Field(default_factory=list)


class WorkspaceExperimentVariantSummary(BaseModel):
    variant_key: str
    label: str
    is_baseline: bool
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class WorkspaceExperimentDigest(BaseModel):
    experiment_id: int
    feature_id: str
    objective: str = ""
    baseline_commit: str = ""
    status: str = ""
    variants: List[WorkspaceExperimentVariantSummary] = Field(default_factory=list)
    analysis_status: str = "not_requested"
    analysis_narrative: Optional[str] = None
    analysis_recommendation_variant_key: Optional[str] = None
    evidence: List[WorkspaceEvidenceRef] = Field(default_factory=list)


class WorkspaceHumanDecisionDigest(BaseModel):
    source_type: Literal["experiment"] = "experiment"
    source_id: str
    decision: str
    variant_key: Optional[str] = None
    note: str = ""


class WorkspaceContextPack(BaseModel):
    system: WorkspaceSystemSummary
    focus: Optional[WorkspaceFocusSummary] = None
    repository: Optional[WorkspaceRepositorySummary] = None
    features: List[WorkspaceFeatureDigest] = Field(default_factory=list)
    components: List[WorkspaceComponentDigest] = Field(default_factory=list)
    traces: List[WorkspaceTraceDigest] = Field(default_factory=list)
    evaluations: List[WorkspaceEvaluationDigest] = Field(default_factory=list)
    probe_plans: List[WorkspaceProbePlanSummary] = Field(default_factory=list)
    experiments: List[WorkspaceExperimentDigest] = Field(default_factory=list)
    human_decisions: List[WorkspaceHumanDecisionDigest] = Field(default_factory=list)
    evidence: List[WorkspaceEvidenceRef] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)


# --- Decision Workspace structured agent turn (Issue #37) ------------------


class WorkspaceContextRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WorkspaceContextItemType
    id: str = Field(..., min_length=1, max_length=200)


class WorkspaceAgentTurnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=20_000)
    context_refs: List[WorkspaceContextRef] = Field(default_factory=list, max_length=20)


class WorkspaceAgentTurnOut(BaseModel):
    user_message: WorkspaceMessageOut
    assistant_message: Optional[WorkspaceMessageOut] = None
    proposals: List[WorkspaceProposalOut] = Field(default_factory=list)
    error: Optional[str] = None


# --- Proposal-to-draft handoff (Issue #39) ----------------------------------
#
# Converts an *accepted* proposal into a deterministic prefill payload for an
# existing screen (Probe Planner or Experiments). This never creates a probe
# plan, probe point, or experiment itself -- only a small tracked record the
# destination screen reads to prefill its existing, user-driven create flow.

WorkspaceProposalDraftType = Literal["probe_plan_draft", "experiment_draft"]


class WorkspaceProposalDraftOut(BaseModel):
    id: int
    workspace_id: int
    proposal_id: int
    system_id: int
    draft_type: WorkspaceProposalDraftType
    target_screen: str
    payload: dict[str, Any] = Field(default_factory=dict)
    missing_fields: List[str] = Field(default_factory=list)
    created_at: float


# --- System-understanding interview persistence (Issue #67) -----------------
#
# A pure persistence + CRUD contract for the #66 conversational metadata/probe
# authoring flow. No LLM call and no worktree write happen here; later sibling
# issues build dialogue, approval transitions, materialization, and the UI on
# top of these models. The combined per-symbol proposal carries both the
# proposed `probe-agent:` docstring metadata block (#54 vocabulary) and the
# associated probe-plan fields (#25 model).

InterviewSessionStatus = Literal["open", "proposals_ready", "materialized", "closed"]
InterviewMessageRole = Literal["user", "assistant", "system"]

# Issue #291: answerable knowledge areas / handoff finite sets. Defined here
# (ahead of InterviewSessionOut/InterviewQaOut which reference KnowledgeArea)
# rather than down by the rest of the Issue #291 models further below.
KnowledgeArea = Literal[
    "product_intent", "domain_rule", "operations", "implementation", "security",
]
HandoffOriginKind = Literal["qa", "review_item"]
HandoffPriority = Literal["low", "normal", "high"]
HandoffStatus = Literal["pending", "answered", "returned", "cancelled"]

InterviewStage = Literal[
    "understanding_initialized",
    "purpose_confirmation",
    "capability_confirmation",
    "element_classification",
    "api_boundary_mapping",
    "probe_flow_selection",
    "proposal_generation",
]
InterviewProposalApprovalState = Literal[
    "proposed", "approved", "rejected", "edited", "needs_review"
]
# Finite #54 vocabulary for a single state_effects entry.
SourceMetadataStateEffect = Literal[
    "none",
    "database-read",
    "database-write",
    "network",
    "filesystem",
    "cache",
    "external-api",
    "queue",
]
ProbeRecommendedMode = Literal["trace", "shadow"]
ProbeSideEffectRisk = Literal["none", "low", "medium", "high"]
ProbeReplayability = Literal["safe", "caution", "unsafe"]


class InterviewSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: int
    title: str = Field(default="", max_length=200)
    focus: str = Field(default="", max_length=500)


class InterviewSessionOut(BaseModel):
    id: int
    system_id: int
    snapshot_id: int
    snapshot_commit_sha: Optional[str] = None
    title: str
    focus: str
    status: InterviewSessionStatus
    stage: Optional[InterviewStage] = "understanding_initialized"
    current_understanding: Optional[Dict[str, Any]] = None
    gap_analysis: Optional[List[Dict[str, Any]]] = None
    open_questions: Optional[List[Dict[str, Any]]] = None
    user_intent: Optional[str] = None
    last_error: Optional[str] = None
    understanding_confirmed_at: Optional[float] = None
    understanding_confirmed_by: Optional[str] = None
    # Issue #312: a later Understanding revision must be manually confirmed
    # into the canonical capability graph before it can drive scoped
    # Alignment carry-over.
    capability_graph_confirmed_revision_id: Optional[int] = None
    capability_graph_confirmation_required: bool = False
    # Issue #129: set when an answered interview_qa question is corrected.
    # Never cleared automatically by the revision itself — only a successful
    # understanding rebuild (update-understanding) clears it.
    answers_revised_at: Optional[float] = None
    # Issue #229/#263: deterministic mirror of the update-understanding 409
    # gate (`routes.interview._understanding_update_blocked`) — the single
    # source of truth for both. The Dashboard uses this instead of
    # re-deriving the confirmed-proposal-stage rebuild condition locally, so
    # UI availability can never drift from what the API will actually allow.
    understanding_update_available: bool = True
    materialization_diff: Optional[str] = None
    materialization_ref: Optional[str] = None
    materialized_at: Optional[float] = None
    # Issue #291: which knowledge areas the developer can answer RIGHT NOW
    # (no role inference). Empty means no filtering -- the pre-#291 default
    # of showing every question, never "every area selected".
    answerable_areas: List[KnowledgeArea] = Field(default_factory=list)
    created_at: float
    updated_at: float


class InterviewMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: InterviewMessageRole
    content: str = Field(..., min_length=1, max_length=20_000)
    intelligence_run_id: Optional[int] = None


class InterviewMessageOut(BaseModel):
    id: int
    session_id: int
    role: InterviewMessageRole
    content: str
    intelligence_run_id: Optional[int] = None
    created_at: float


class InterviewProposalMetadataBlock(BaseModel):
    """Proposed `probe-agent:` docstring metadata block for one symbol.

    Finite fields (``element_type`` / ``operation_kind`` / ``state_effects``)
    are validated against #54's vocabulary; the rest are free text per #54.
    This is an LLM-authored *proposal*, so unlike ``SourceMetadataOut`` it
    carries no ``origin``/``explanation_hash``.
    """

    model_config = ConfigDict(extra="forbid")

    role: Optional[str] = Field(default=None, max_length=2000)
    capability: Optional[str] = Field(default=None, max_length=2000)
    system_purpose: Optional[str] = Field(default=None, max_length=2000)
    probe_value: Optional[str] = Field(default=None, max_length=2000)
    element_type: Optional[SourceMetadataElementType] = None
    operation_kind: Optional[SourceMetadataOperationKind] = None
    consumers: List[str] = Field(default_factory=list)
    state_effects: List[SourceMetadataStateEffect] = Field(default_factory=list)


class InterviewProposalProbePlan(BaseModel):
    """Proposed probe-plan fields for the same symbol (#25 model)."""

    model_config = ConfigDict(extra="forbid")

    feature_id: str = Field(default="", max_length=200)
    objective: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=2000)
    recommended_mode: ProbeRecommendedMode = "trace"
    side_effect_risk: ProbeSideEffectRisk = "low"
    replayability: ProbeReplayability = "safe"


class InterviewProposalItem(BaseModel):
    """One combined per-symbol proposal in a create request."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=500)
    qualified_name: str = Field(..., min_length=1, max_length=500)
    symbol_id: Optional[int] = None
    metadata: InterviewProposalMetadataBlock
    probe_plan: InterviewProposalProbePlan
    graph_node_id: Optional[str] = None
    capability_name: Optional[str] = None
    evidence_summary: Optional[str] = None
    proposal_confidence: Optional[float] = None


class InterviewRunAudit(BaseModel):
    """Reasoning-run audit metadata for a batch of proposals.

    Persisted as an ``intelligence_runs`` row (the shared audit store) and
    linked from each proposal. ``decision_method`` is fixed to ``reasoning_llm``
    for this issue; ``manual`` is set only by the later approval issue.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1, max_length=200)
    model: str = Field(..., min_length=1, max_length=200)
    prompt_version: str = Field(..., min_length=1, max_length=100)
    schema_version: str = Field(..., min_length=1, max_length=100)
    is_mock: bool = False


class InterviewProposalsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit: InterviewRunAudit
    message_id: Optional[int] = None
    proposals: List[InterviewProposalItem] = Field(..., min_length=1)


class InterviewProposalOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    snapshot_id: int
    message_id: Optional[int] = None
    intelligence_run_id: int
    symbol_id: Optional[int] = None
    path: str
    qualified_name: str
    metadata: InterviewProposalMetadataBlock
    probe_plan: InterviewProposalProbePlan
    decision_method: DecisionMethod
    graph_node_id: Optional[str] = None
    capability_name: Optional[str] = None
    evidence_summary: Optional[str] = None
    proposal_confidence: Optional[float] = None
    approval_state: InterviewProposalApprovalState
    is_mock: bool = False
    intelligence_run: Optional[IntelligenceRunOut] = None
    created_at: float
    updated_at: float


class InterviewSessionDetailOut(InterviewSessionOut):
    messages: List[InterviewMessageOut] = Field(default_factory=list)
    proposals: List[InterviewProposalOut] = Field(default_factory=list)


class InterviewSnapshotRebaseRequest(BaseModel):
    """Move an existing Interview session to a newer snapshot.

    The operation preserves Q&A and understanding text, but reconciles proposal
    review state against source anchors before any later materialization.
    """

    model_config = ConfigDict(extra="forbid")

    target_snapshot_id: Optional[int] = None
    actor: str = Field(default="dashboard", max_length=200)


class InterviewSnapshotRebaseOut(BaseModel):
    session_id: int
    system_id: int
    from_snapshot_id: int
    to_snapshot_id: int
    proposals_preserved: int = 0
    proposals_marked_needs_review: int = 0
    proposals_missing_source: int = 0
    proposals_changed_source: int = 0
    message: str = ""
    session: InterviewSessionOut


# --- Interview Context Pack (Issue #68) -------------------------------------
#
# Deterministic, no-LLM context assembly for the system-understanding
# interview. Reads indexed symbols, entrypoints, existing probe-agent:
# metadata, and capability hierarchy classification from a pinned snapshot
# and flags which items are already classified vs. unclassified (blank-page).
# Every item carries a snapshot-relative evidence location. Output respects
# an LLM context budget independent of snapshot storage.

InterviewSymbolClassification = Literal["classified", "unclassified"]


class InterviewEvidenceLocation(BaseModel):
    snapshot_id: int
    path: str
    qualified_name: str
    start_line: int
    end_line: int


class InterviewSymbolItem(BaseModel):
    symbol_id: int
    path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    classification: InterviewSymbolClassification
    has_metadata: bool = False
    element_type: Optional[str] = None
    role: Optional[str] = None
    capability: Optional[str] = None
    operation_kind: Optional[str] = None
    probe_value: Optional[str] = None
    evidence: InterviewEvidenceLocation


class InterviewEntrypointItem(BaseModel):
    entrypoint_id: int
    entrypoint_type: str
    category: str
    label: str
    handler_path: str
    handler_qualified_name: str
    line_start: int
    line_end: int
    classification: InterviewSymbolClassification
    has_metadata: bool = False
    evidence: InterviewEvidenceLocation


class InterviewContextPack(BaseModel):
    system_id: int
    snapshot_id: int
    total_symbols: int
    total_entrypoints: int
    classified_count: int
    unclassified_count: int
    budget_max_chars: int
    budget_used_chars: int
    truncated: bool = False
    symbols: List[InterviewSymbolItem] = Field(default_factory=list)
    entrypoints: List[InterviewEntrypointItem] = Field(default_factory=list)
    omission_notes: List[str] = Field(default_factory=list)


# --- Interview Dialogue Turn (Issue #69) -------------------------------------
#
# Request/response models for the reasoning-model dialogue endpoint. The
# endpoint generates a structured assistant turn grounded in #68's context
# pack, optionally producing per-symbol combined proposals validated against
# #54 vocabulary and the safety denylist from probe_planner.py.


class InterviewDialogueTurnRequest(BaseModel):
    """Request body for a single interview dialogue turn."""

    model_config = ConfigDict(extra="forbid")

    user_message: str = Field(..., min_length=1, max_length=20_000)
    budget: Optional[int] = Field(default=None, ge=1000, le=500_000)
    generate_proposals: bool = False
    # Issue #123: the open question the user is answering with this turn.
    # The server removes it from the session's open_questions (exact match)
    # so the UI never re-asks an already-answered question.
    # Superseded by answered_qa_id (Issue #129); still accepted during the
    # migration period and applied in addition to it, never instead of it.
    answered_question: Optional[str] = Field(default=None, max_length=2000)
    # Issue #129: ID of the interview_qa row this turn answers. Preferred
    # over answered_question because it survives question rewording and
    # cannot silently match the wrong question.
    answered_qa_id: Optional[int] = None
    actor: str = Field(default="dashboard", min_length=1, max_length=200)
    # Issue #142: the developer answered "I don't know" (「わかりません」/不明).
    # The turn is NOT an error: the answered Q&A row is recorded as
    # 'unconfirmed' rather than 'answered', and the reasoning model is asked to
    # form an evidence-grounded hypothesis and re-confirm it, so the interview
    # continues instead of stopping.
    answer_unknown: bool = False


CapabilityEntityKind = Literal[
    "core_capability", "capability_element", "supporting_element", "api_boundary",
]


class InterviewCapabilityIdentityBinding(BaseModel):
    """Explicitly keep one canonical identity across a display-name rename."""

    model_config = ConfigDict(extra="forbid")

    entity_kind: CapabilityEntityKind
    current_name: str = Field(min_length=1, max_length=500)
    entity_id: int = Field(gt=0)


class InterviewCapabilityRelationConfirmation(BaseModel):
    """One manually confirmed many-to-many support relation."""

    model_config = ConfigDict(extra="forbid")

    supported_kind: CapabilityEntityKind
    supported_name: str = Field(min_length=1, max_length=500)
    supporting_kind: CapabilityEntityKind
    supporting_name: str = Field(min_length=1, max_length=500)
    role: str = Field(default="", max_length=1_000)
    scope: str = Field(default="", max_length=2_000)


class InterviewCapabilityNodeOut(BaseModel):
    entity_id: int
    entity_kind: CapabilityEntityKind
    name: str
    summary: str = ""
    semantic_digest: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class InterviewCapabilityRelationOut(BaseModel):
    relation_id: int
    supported_entity_id: int
    supporting_entity_id: int
    relation_kind: Literal["supports"]
    role: str = ""
    scope: str = ""
    semantic_digest: str


class InterviewCapabilityGraphOut(BaseModel):
    confirmation_id: int
    system_id: int
    session_id: int
    base_confirmation_id: Optional[int] = None
    source_revision_id: Optional[int] = None
    source_revision_at: Optional[float] = None
    composition_digest: str
    decided_by: str
    decided_by_user_id: Optional[int] = None
    decision_method: Literal["manual"]
    created_at: float
    nodes: List[InterviewCapabilityNodeOut] = Field(default_factory=list)
    relations: List[InterviewCapabilityRelationOut] = Field(default_factory=list)


class InterviewConfirmUnderstandingRequest(BaseModel):
    """Manual confirmation that the gathered interview context is sufficient.

    Issue #123: in the zero-base fallback (no structured understanding could
    be built), the developer explicitly confirms that the conversation
    contains enough context to move to proposal generation. This is a manual
    decision record, not an LLM output.
    """

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=200)
    # Optimistic lock for the System-wide canonical head. Required by the
    # API whenever a new confirmation builds on an existing head.
    capability_base_confirmation_id: Optional[int] = Field(default=None, gt=0)
    # Issue #312: absent means derive exact relations from the current
    # understanding's ``children`` lists.  An explicitly empty list confirms
    # a graph with no support relations.
    capability_relations: Optional[
        List[InterviewCapabilityRelationConfirmation]
    ] = Field(default=None, max_length=200)
    # Exact, human-supplied rename identity.  No fuzzy/name-similarity
    # inference is performed when this list omits a renamed node.
    capability_identity_bindings: List[
        InterviewCapabilityIdentityBinding
    ] = Field(default_factory=list, max_length=100)


class InterviewQuestionEvidenceRef(BaseModel):
    """Snapshot-relative code reference backing an interview question (Issue #128).

    Paths and line ranges are validated deterministically against the
    context pack / stored understanding before the turn is accepted; refs
    the model invented fail the turn closed.
    """

    path: str
    start_line: int = 0
    end_line: int = 0


class InterviewStructuredQuestion(BaseModel):
    """Hypothesis-first interview question (Issue #128).

    The model states its current hypothesis with evidence, then asks a
    focused confirmation question. Plain-string questions are still accepted
    from older prompt versions and normalized to this shape (question_text
    only) — a structural conversion, not an interpretation.
    """

    question_text: str
    hypothesis: Optional[str] = None
    evidence_refs: List[InterviewQuestionEvidenceRef] = Field(default_factory=list)
    answer_options: List[str] = Field(default_factory=list)


class InterviewDialogueProposalOut(BaseModel):
    """A single combined proposal from a dialogue turn, before persistence."""

    path: str
    qualified_name: str
    symbol_id: Optional[int] = None
    metadata: InterviewProposalMetadataBlock
    probe_plan: InterviewProposalProbePlan
    graph_node_id: Optional[str] = None
    capability_name: Optional[str] = None
    evidence_summary: Optional[str] = None
    proposal_confidence: Optional[float] = None
    denylist_hit: Optional[str] = None


class InterviewDialogueTurnOut(BaseModel):
    """Response from a single interview dialogue turn.

    Contains the structured assistant message, any generated proposals, and
    the reasoning-run audit metadata. If error is set, the turn failed closed
    and no proposals should be stored.
    """

    assistant_message: str = ""
    proposals: List[InterviewDialogueProposalOut] = Field(default_factory=list)
    # Whether this turn asked the reasoning model for proposals (the Issue
    # #83/#123 gate passed with generate_proposals set). True with an empty
    # proposals list means the model needs narrowing answers first and
    # returned next_questions instead — the dashboard must not present that
    # as a plain successful reply.
    proposals_requested: bool = False
    next_questions: List[InterviewStructuredQuestion] = Field(default_factory=list)
    intelligence_run: Optional[IntelligenceRunOut] = None
    error: Optional[str] = None
    stage: Optional[InterviewStage] = None
    current_understanding: Optional[Dict[str, Any]] = None
    gap_analysis: Optional[List[Dict[str, Any]]] = None
    open_questions_structured: Optional[List[Dict[str, Any]]] = None
    # Issue #129: the structured Q&A rows created from next_questions, so the
    # caller can navigate straight to them without re-fetching the list.
    created_qa_ids: List[int] = Field(default_factory=list)
    # Issue #130: audit of the pass-1 evidence-selection run, when it ran.
    evidence_run: Optional[IntelligenceRunOut] = None
    evidence_used: List["InterviewQaEvidenceRefOut"] = Field(default_factory=list)
    # Issue #137: the persisted intelligence_run_evidence rows for this
    # turn's evidence-selection run — every snippet actually read, whether
    # or not a question cited it. evidence_used above is unchanged.
    evidence_reads: List["IntelligenceRunEvidenceOut"] = Field(default_factory=list)
    # Issue #142: how many question evidence_refs were dropped as unverifiable
    # (not contained in any known snapshot span). A dropped ref is a graceful
    # fallback — the question is still asked — so this is surfaced for operator
    # visibility, not an error.
    evidence_refs_dropped: int = 0


# --- Evidence read audit (Issue #137) -----------------------------------------
#
# Persists every snippet pass 1 of the interview dialogue turn actually read
# from the pinned snapshot, linked to the interview_evidence_selection
# intelligence_runs row — independent of whether the resulting question
# cited it. Snippet content is never stored (Principle 5/size).


class IntelligenceRunEvidenceOut(BaseModel):
    id: int
    system_id: int
    intelligence_run_id: int
    path: str
    start_line: int
    end_line: int
    char_count: int
    truncated: bool
    created_at: float


class IntelligenceRunEvidenceListOut(BaseModel):
    intelligence_run_id: int
    system_id: int
    items: List[IntelligenceRunEvidenceOut] = Field(default_factory=list)


# --- Structured Interview Q&A (Issue #129) ------------------------------------
#
# Question/answer pairs as ID-addressable rows, replacing exact-text matching
# against the interview_session.open_questions JSON blob. Correcting an
# answer never overwrites the row; it inserts a new revision and links the
# old row forward via superseded_by_id, so every prior answer stays
# auditable (Principle 7). question_category/question_source/status are
# explicit finite sets (Principle 6).

InterviewQaCategory = Literal["purpose", "capability", "api", "probe_flow", "general"]
# Issue #135: "runtime" questions are generated by reconciling approved
# metadata/probe plans against deterministic runtime trace aggregates,
# distinct from the dialogue/reviewer/zero_base sources above.
InterviewQaSource = Literal["reviewer", "dialogue", "zero_base", "runtime"]
# Issue #142: "unconfirmed" records that the developer explicitly could not
# confirm the answer ("I don't know" / 「わかりません」). It is a valid input,
# not an error: the row is kept, its answer text stored, and it is fed back to
# the reasoning model as an open hypothesis to re-confirm — never counted as a
# confirmed/answered fact.
InterviewQaStatus = Literal["open", "answered", "revised", "skipped", "unconfirmed"]


class InterviewQaEvidenceRefOut(BaseModel):
    """Snapshot-relative code reference, optionally with what was actually read.

    ``char_count`` is populated only for evidence that Issue #130's
    evidence-gathering step read from the pinned snapshot; it is a raw fact
    about what was fetched, kept separate from any LLM interpretation of it.
    """

    path: str
    start_line: int = 0
    end_line: int = 0
    char_count: Optional[int] = None


class InterviewQaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: str = Field(..., min_length=1, max_length=2_000)
    question_category: InterviewQaCategory = "general"
    question_source: InterviewQaSource = "dialogue"
    hypothesis: Optional[str] = Field(default=None, max_length=4_000)
    evidence_refs: List[InterviewQaEvidenceRefOut] = Field(default_factory=list, max_length=10)


class InterviewQaActorRequest(BaseModel):
    """Actor for a skip/resume action (Principle 7: manual decisions record who/when)."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(..., min_length=1, max_length=200)


class InterviewQaAnswerRequest(BaseModel):
    """Answer or correct a question.

    If the current row is 'open' or 'skipped', the answer is recorded on that
    same row (first answer — nothing to supersede). If the current row is
    'answered', this is a correction: a new row is inserted with the new
    answer and the old row is marked 'revised' with superseded_by_id set.

    Issue #142: when ``answer_unknown`` is set, the developer explicitly could
    not confirm the answer. The row is recorded with status 'unconfirmed'
    rather than 'answered', ``answer_text`` may be blank, and the interview
    continues (the reasoning model re-confirms via a hypothesis question).
    """

    model_config = ConfigDict(extra="forbid")

    # min_length is 0 so an "I don't know" answer can be blank; a validator
    # below still requires non-empty text for a normal (confirmed) answer.
    answer_text: str = Field(default="", max_length=20_000)
    actor: str = Field(..., min_length=1, max_length=200)
    answer_unknown: bool = False
    # Issue #291: set when this answer is the original user's EXPLICIT
    # confirmation of a returned handoff's assignee answer (optionally
    # prefilled client-side from QuestionHandoffOut.answer_text). The
    # handoff's own answer_text/answered_by are never written here directly
    # -- the developer still submits their own answer_text/actor; this only
    # links the provenance (routes/interview.py validates the handoff
    # exists, belongs to this question, and is status='returned').
    handoff_id: Optional[int] = None

    @model_validator(mode="after")
    def _require_answer_or_unknown(self) -> "InterviewQaAnswerRequest":
        if not self.answer_unknown and not self.answer_text.strip():
            raise ValueError("answer_text is required unless answer_unknown is set")
        return self


class InterviewQaInvestigationEvidenceOut(BaseModel):
    path: str
    start_line: int
    end_line: int
    summary: str = ""


class InterviewQaInvestigationOut(BaseModel):
    """A persisted Investigation Agent result for a normal-flow question.

    Issue #286 review fix (Finding 1): populated by
    ``POST /interview/sessions/{session_id}/qa/route-and-investigate`` from
    the same ``InvestigationResult`` shape the Inquiry flow already composes
    (``app/investigation_agent.py``). Never written by anything else --
    answering/correcting a question never fabricates or edits this field,
    and this field itself never confirms an answer (#286/#284: an AI
    proposal/finding never auto-confirms).
    """

    run_id: int
    status: str  # completed | unresolved
    conclusion: str = ""
    key_points: List[str] = Field(default_factory=list)
    evidence: List[InterviewQaInvestigationEvidenceOut] = Field(default_factory=list)
    uncertainty: str = ""
    confidence: str = "uncertain"
    decision_question: Optional[str] = None


class InterviewQaOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    question_text: str
    question_category: InterviewQaCategory
    question_source: InterviewQaSource
    hypothesis: Optional[str] = None
    evidence_refs: List[InterviewQaEvidenceRefOut] = Field(default_factory=list)
    # Issue #135: raw aggregated trace facts + declared-metadata provenance
    # for question_source == "runtime" rows. Kept separate from
    # evidence_refs (code line ranges) because runtime evidence is numeric
    # aggregation, not a source-code span. Null for all other sources.
    runtime_evidence: Optional[Dict[str, Any]] = None
    answer_text: Optional[str] = None
    # Issue #309: explicit measurement provenance. None means unanswered or
    # legacy history whose unknown/known action cannot be recovered exactly.
    answer_unknown: Optional[bool] = None
    status: InterviewQaStatus
    answered_by: Optional[str] = None
    superseded_by_id: Optional[int] = None
    created_at: float
    answered_at: Optional[float] = None
    # Issue #286: Question Router classification for this question, set only
    # via POST /interview/qa/{qa_id}/route (never automatically for
    # dialogue-turn questions). None until routed.
    route_category: Optional[str] = None
    route_run_id: Optional[int] = None
    # Issue #291: knowledge area assigned by the same Question Router call
    # (question-router-v2); null until routed or when no area clearly fits.
    # Never inferred deterministically. Used only to group out-of-area
    # questions -- it never hides a question from the full list.
    knowledge_area: Optional[KnowledgeArea] = None
    # Issue #291: set when this question has been handed off to an assignee
    # (question_handoff.id). The origin row's own `status` is left
    # untouched by a handoff (Principle 2/6 -- see db.py's table docstring).
    handoff_id: Optional[int] = None
    # Issue #286 review fix (Finding 1): the Investigation Agent result for
    # this question, populated only via the batch route-and-investigate
    # endpoint. None until investigated (or for human_only/failed runs,
    # which never populate it -- see the endpoint docstring).
    investigation: Optional[InterviewQaInvestigationOut] = None


class InterviewQaAnswerOut(BaseModel):
    qa: InterviewQaOut
    previous: Optional[InterviewQaOut] = None
    # True when this session already has generated proposals, so the
    # dashboard can surface "regeneration recommended" without probe-agent
    # ever auto-invalidating or regenerating the approved/proposed set.
    regeneration_recommended: bool = False


class InterviewQaListOut(BaseModel):
    session_id: int
    system_id: int
    items: List[InterviewQaOut] = Field(default_factory=list)
    open_count: int = 0
    high_priority_open_count: int = 0
    answers_revised_at: Optional[float] = None


# --- Interview UX metrics (Issue #309) --------------------------------------

InterviewMetricEventType = Literal[
    "review_started",
    "review_completed",
    "review_abandoned",
    "evidence_available",
    "evidence_expanded",
    "unchanged_item_presented",
    "unchanged_item_reconfirmed",
    "question_presented",
]
InterviewMetricTargetKind = Literal[
    "session", "qa", "alignment_item", "inquiry_message",
]
# Issue #334: joint_understanding metrics are a SEPARATE category on purpose --
# they measure whether shared understanding improved, and must never be read
# as, or averaged with, the efficiency numbers in the other categories.
InterviewMetricCategory = Literal[
    "user_burden", "accuracy", "ux_quality", "joint_understanding",
    # Issue #338: the existing `joint_understanding` category counts
    # UTILIZATION and close labels -- how much the feature was used and what it
    # said about itself. These two answer different questions and must not be
    # averaged with it or with each other:
    #   joint_understanding_quality -- did understanding actually improve
    #     (gaps closed, hypotheses that held, decisions that stuck)
    #   joint_understanding_burden  -- what it cost the developer
    # Keeping them apart is the point: an efficiency gain must never be
    # displayed as a quality gain.
    "joint_understanding_quality", "joint_understanding_burden",
]
InterviewMetricStatus = Literal["measured", "unmeasured"]
InterviewMetricUnit = Literal[
    "ratio", "answers_per_update", "operations_per_inquiry",
    # Issue #338: per-session burden counts. A rate would hide the thing being
    # measured -- "how much work did one conversation cost" is not a ratio.
    "per_session",
]
InterviewMetricKey = Literal[
    "answers_per_understanding_update",
    "unknown_answer_rate",
    "review_abandonment_rate",
    "evidence_detail_expansion_rate",
    "operations_per_inquiry",
    "corrected_confirmed_intent_rate",
    "incorrect_answer_confirmation_rate",
    "runtime_contradiction_rate",
    "understanding_revision_recorrection_rate",
    "post_approval_rejection_rate",
    "post_approval_rollback_rate",
    "post_approval_rejection_or_rollback_rate",
    "repeated_question_rate",
    "unchanged_item_reconfirmation_rate",
    "inquiry_resolution_rate",
    "post_inquiry_confirmation_rate",
    "implementation_question_transfer_rate",
    # Epic #328 Phase F (#334): joint-understanding quality.
    "joint_understanding_from_unknown_rate",
    "joint_understanding_conclusion_rate",
    "joint_understanding_provisional_outcome_rate",
    "joint_understanding_stale_premise_close_rate",
    "joint_understanding_unknown_finding_rate",
    "joint_understanding_reflux_rate",
    "joint_understanding_investigation_answered_rate",
    "joint_understanding_developer_question_rate",
    # Issue #338: outcome-lineage quality. Every one is derived from the finite
    # lineage events (app/joint_lineage.py), never from a close label.
    "joint_understanding_unknown_resolution_rate",
    "joint_understanding_hypothesis_reversal_rate",
    "joint_understanding_hypothesis_correction_rate",
    "joint_understanding_adoption_reconfirmation_rate",
    "joint_understanding_decision_undo_rate",
    "joint_understanding_classification_correction_rate",
    # Issue #338: developer burden, per session.
    "joint_understanding_rounds_per_session",
    "joint_understanding_developer_actions_per_session",
    "joint_understanding_developer_findings_per_session",
    "joint_understanding_question_reask_rate",
]
# The same finite key set as a plain tuple, so the external attention policy
# (Issue #341) can be validated for terminal coverage at load time.
INTERVIEW_METRIC_KEYS: tuple = get_args(InterviewMetricKey)

# --- Interview metric 要確認判定 (Issue #341) --------------------------------
#
# ``guardrail`` above only designates which metrics are worth watching. Whether
# a metric is *currently* in a bad state is a separate, evaluated judgement so
# "値が悪い" and "まだ判断できない" never collapse into one warning state.
InterviewMetricAttentionState = Literal[
    "attention",         # 閾値を超えており要確認
    "ok",                # 判定可能で、要確認条件に該当しない
    "insufficient_data",  # 母数不足、またはまだ観測が無い
    "not_measurable",    # 元となる事実が記録されておらず算出手段が無い
    "criterion_unset",   # 監視対象だが閾値が未設定
    "observation_only",  # 通知対象ではない定期観測指標
]
InterviewMetricAttentionReason = Literal[
    "threshold_breached",
    "within_threshold",
    "sample_below_minimum",
    "no_observations_yet",
    "not_recorded",
    "threshold_not_configured",
    "not_a_notification_target",
]
# v1 vocabularies. See ``interview_metric_attention`` for why bounded windows,
# sustained triggers, and manual acknowledgement are deliberately absent.
ATTENTION_DIRECTIONS = ("high_is_bad", "low_is_bad")
ATTENTION_WINDOWS = ("all_time",)
ATTENTION_TRIGGERS = ("single_breach",)
ATTENTION_CLEAR_CONDITIONS = ("value_within_threshold",)
InterviewMetricAttentionDirection = Literal["high_is_bad", "low_is_bad"]
InterviewMetricAttentionWindow = Literal["all_time"]
InterviewMetricAttentionTrigger = Literal["single_breach"]
InterviewMetricAttentionClear = Literal["value_within_threshold"]
# The entry point's 取得失敗 state is client-side only: a server that cannot
# answer cannot report its own failure.
InterviewMetricsAttentionState = Literal["normal", "attention", "insufficient_data"]


class InterviewMetricAttentionOut(BaseModel):
    """One metric's evaluated 要確認 state plus the criterion it was judged by."""

    state: InterviewMetricAttentionState
    watched: bool = False
    reason: InterviewMetricAttentionReason
    direction: Optional[InterviewMetricAttentionDirection] = None
    threshold: Optional[float] = None
    min_sample: int = 0
    window: Optional[InterviewMetricAttentionWindow] = None
    trigger: Optional[InterviewMetricAttentionTrigger] = None
    clear_condition: Optional[InterviewMetricAttentionClear] = None


class InterviewMetricsAttentionSummaryOut(BaseModel):
    """The entry point's state, derived only from watched metrics."""

    state: InterviewMetricsAttentionState
    attention_count: int = 0
    insufficient_data_count: int = 0
    not_measurable_count: int = 0
    watched_count: int = 0
    policy_version: str
    policy_digest: str


class InterviewMetricEventCreate(BaseModel):
    """One bounded, content-free UI measurement event.

    ``event_key`` is generated by the client and unique within a System so a
    retry is idempotent. The route validates the finite event/target pairing
    and target ownership; arbitrary attributes and free-text analytics
    payloads are intentionally unsupported.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["interview-metric-event-v1"] = "interview-metric-event-v1"
    event_key: str = Field(..., min_length=1, max_length=128)
    session_id: int = Field(..., ge=1)
    event_type: InterviewMetricEventType
    target_kind: InterviewMetricTargetKind
    target_id: int = Field(..., ge=1)


class InterviewMetricEventOut(BaseModel):
    id: int
    schema_version: Literal["interview-metric-event-v1"] = "interview-metric-event-v1"
    event_key: str
    system_id: int
    session_id: int
    event_type: InterviewMetricEventType
    target_kind: InterviewMetricTargetKind
    target_id: int
    recorded_at: float


class InterviewMetricOut(BaseModel):
    key: InterviewMetricKey
    category: InterviewMetricCategory
    # 監視対象としての指定。実際に要確認かどうかは ``attention`` が持つ。
    guardrail: bool = False
    description: str
    formula: str
    sources: List[str] = Field(default_factory=list)
    status: InterviewMetricStatus
    value: Optional[float] = None
    unit: InterviewMetricUnit
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    sample_size: int = 0
    unmeasured_reason: Optional[str] = None
    # Evaluated after the value exists, so it is nullable during construction
    # only. ``build_interview_metrics`` always populates it before returning,
    # and the ``interview-metrics-v2`` response contract guarantees it.
    attention: Optional[InterviewMetricAttentionOut] = None


class InterviewMetricsOut(BaseModel):
    system_id: int
    schema_version: Literal["interview-metrics-v2"] = "interview-metrics-v2"
    generated_at: float
    sessions_observed: int = 0
    events_observed: int = 0
    attention: InterviewMetricsAttentionSummaryOut
    metrics: List[InterviewMetricOut] = Field(default_factory=list)


# --- Batch route-and-investigate (Issue #286 review fix, Finding 1) ----------
#
# Wires Question Router / Investigation Agent into the NORMAL Q&A flow (they
# were previously reachable only inside the Inquiry side-conversation and via
# the single-question POST .../qa/{qa_id}/route, which never investigates).
# This never writes answer_text/status/answered_by -- investigation is a
# finding to review, never an auto-confirmation (Principle 2/7; #286 AC).


class InterviewQaRouteInvestigateItemOut(BaseModel):
    qa_id: int
    route_category: Optional[str] = None
    knowledge_area: Optional[KnowledgeArea] = None
    # completed | unresolved | failed | None (not attempted this call, e.g.
    # human_only or a route failure that left the question unrouted).
    investigation_status: Optional[str] = None
    error: Optional[str] = None


class InterviewQaRouteInvestigateCountsOut(BaseModel):
    routed: int = 0
    investigated: int = 0
    failed: int = 0
    skipped_cap: int = 0


class InterviewQaRouteInvestigateBatchOut(BaseModel):
    session_id: int
    system_id: int
    results: List[InterviewQaRouteInvestigateItemOut] = Field(default_factory=list)
    counts: InterviewQaRouteInvestigateCountsOut = Field(
        default_factory=InterviewQaRouteInvestigateCountsOut
    )


# Review fix (PR #296, Finding 4): an optional, explicit subset of question
# ids to route+investigate, so a single card's 「わからない」 action can
# target just that one question instead of always triggering the whole
# session's batch selection (up to MAX_BATCH_QUESTIONS LLM investigations
# per call). Omitting qa_ids (or sending null) keeps the prior all-eligible
# behavior (backward compatible). A qa_id that does not exist, or belongs to
# a different session, is a per-question error in the response -- never a
# reason to fail or silently drop the rest of the batch (same fail-closed-
# per-question policy the existing batch endpoint already uses).
class InterviewQaRouteInvestigateBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qa_ids: Optional[List[int]] = None


# --- Intent Brief (Issue #284) ------------------------------------------------
#
# Structured user intent, kept separate from implementation-fact
# understanding: only the user can decide these values. ai_proposed items
# are drafts the reasoning model grounds in the conversation; they never
# become 'confirmed' except through the explicit confirm/correct endpoints
# (decision_method stays 'manual' for every user-driven transition,
# Principle 2). 'undecided' and 'not_applicable' are first-class answers
# ("現状把握だけが目的" / "まだ解決策を決めていない" / 「対象外」), not errors.

InterviewIntentField = Literal[
    "goal", "pain", "success_criteria", "priority", "constraints", "non_goals"
]
InterviewIntentStatus = Literal[
    "proposed", "confirmed", "needs_review", "undecided", "not_applicable"
]
# Statuses a user may set directly when creating an item. 'proposed' and
# 'needs_review' are system/AI states, never user-chosen at creation time.
InterviewIntentUserStatus = Literal["confirmed", "undecided", "not_applicable"]
InterviewIntentOrigin = Literal["user", "ai_proposed"]


class InterviewIntentItemOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    field: InterviewIntentField
    value_text: str
    status: InterviewIntentStatus
    origin: InterviewIntentOrigin
    source_statement: Optional[str] = None
    decision_method: DecisionMethod
    intelligence_run_id: Optional[int] = None
    is_mock: bool = False
    superseded_by_id: Optional[int] = None
    created_at: float
    updated_at: float


class InterviewIntentItemCreate(BaseModel):
    """User-authored intent item. Always origin='user', decision_method='manual'."""

    model_config = ConfigDict(extra="forbid")

    field: InterviewIntentField
    value_text: str = Field(..., min_length=1, max_length=4_000)
    status: InterviewIntentUserStatus = "confirmed"


class InterviewIntentCorrectRequest(BaseModel):
    """Correct an ai_proposed (or previously confirmed) item's value.

    Never overwrites: the caller inserts a new 'confirmed'/'user' row and
    marks the prior row's superseded_by_id, mirroring interview_qa's
    answer/correction pattern.
    """

    model_config = ConfigDict(extra="forbid")

    value_text: str = Field(..., min_length=1, max_length=4_000)


class InterviewIntentListOut(BaseModel):
    session_id: int
    system_id: int
    items_by_field: Dict[str, List[InterviewIntentItemOut]] = Field(default_factory=dict)


# --- Inquiry lifecycle (Issue #285) -------------------------------------------
#
# A doubt about a confirmation item (Q&A question, Intent Brief item, or --
# from Issue #287 -- a review item) is held pending while a separate Inquiry
# conversation resolves it. Resolving an Inquiry never changes the origin
# item's own state (Principle 2's "explicit user action" boundary applies to
# the origin item's own endpoint, not to closing the Inquiry).

InterviewInquiryOriginKind = Literal["qa", "intent", "review_item"]
# 'superseded' (Issue #308 / #323) is a TERMINAL, system-only status: the
# premise the conversation was answered against no longer exists, so the
# history stays readable but is never reused as current justification. It is
# not "wrong" and not "unresolved"; only the premise evaluation inside an
# Alignment rebuild ever writes it, never a user endpoint.
InterviewInquiryStatus = Literal[
    "open", "resolved", "unresolved", "cancelled", "held", "superseded"
]
# Derived (never stored) description of how comparable an Inquiry's premise
# is -- see app/inquiry_premise.py's PREMISE_TRACKING_STATES.
InquiryPremiseTrackingState = Literal["not_applicable", "untrackable", "tracked"]
# Result of the last premise evaluation; null until a rebuild evaluated it.
InquiryPremiseEvaluation = Literal["unchanged", "changed", "removed", "ambiguous"]
InterviewInquiryMessageRole = Literal["user", "assistant"]


class InterviewInquiryEvidenceOut(BaseModel):
    path: str
    start_line: int
    end_line: int
    summary: str = ""


class InterviewInquiryMessageDetailOut(BaseModel):
    """Progressive-disclosure detail for an assistant Inquiry message.

    The message's own ``content`` is always the short conclusion, shown
    first; ``detail`` is the "根拠を見る" (show evidence) expansion.
    """

    key_points: List[str] = Field(default_factory=list)
    evidence: List[InterviewInquiryEvidenceOut] = Field(default_factory=list)
    uncertainty: str = ""
    # Issue #286: which Question Router category produced this answer, and
    # (for "hybrid") the decision question the developer still needs to
    # answer themselves. Both None for messages predating Issue #286.
    route_category: Optional[str] = None
    decision_question: Optional[str] = None
    # Issue #290: runtime_fact evidence entries (provenance + finite
    # runtime_check), always in this detail layer -- never in the message's
    # short ``content`` -- so the initial answer stays conclusion-first and
    # raw trace/provenance data only appears in the collapsible detail
    # expansion (progressive disclosure).
    runtime_evidence: List["InterviewInquiryRuntimeEvidenceOut"] = Field(default_factory=list)
    # A deterministic hint (never LLM free text) that a runtime_fact came
    # back unobserved/stale -- the developer can turn this into an actual
    # POST .../observation-proposals call if they want new observation;
    # this hint alone never creates a proposal row (Principle 5/8).
    suggested_observation_proposal: Optional["SuggestedObservationProposalOut"] = None


class InterviewInquiryMessageOut(BaseModel):
    id: int
    inquiry_id: int
    system_id: int
    role: InterviewInquiryMessageRole
    content: str
    detail: Optional[InterviewInquiryMessageDetailOut] = None
    intelligence_run_id: Optional[int] = None
    is_mock: bool = False
    created_at: float


class InterviewInquiryOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    origin_kind: InterviewInquiryOriginKind
    origin_id: int
    held_draft: Optional[str] = None
    status: InterviewInquiryStatus
    status_reason: Optional[str] = None
    # Issue #308 / #320: the immutable premise this conversation was
    # answered against, captured at creation and never rebased onto a newer
    # session snapshot. snapshot/revision are audit references (they may go
    # NULL under retention); the hash/digest columns are what the premise
    # evaluation actually compares. All null for Inquiries created before
    # this migration and, apart from the snapshot/version/captured_at trio,
    # for the qa/intent origins v1 does not auto-track.
    premise_snapshot_id: Optional[int] = None
    premise_revision_id: Optional[int] = None
    premise_review_subject_id: Optional[str] = None
    premise_content_hash: Optional[str] = None
    premise_capability_digest: Optional[str] = None
    premise_intent_digest: Optional[str] = None
    premise_tracking_version: Optional[str] = None
    premise_captured_at: Optional[float] = None
    # Derived from the bundle above, so the UI never has to re-derive
    # "can this premise be compared at all?" from null checks.
    premise_tracking_state: InquiryPremiseTrackingState = "not_applicable"
    # Issue #323: the last premise verdict, the unique current successor
    # review item (only ever set when exactly one exists -- an ambiguous
    # successor is never guessed), and the moment this Inquiry became
    # 'superseded'. superseded_at is separate from closed_at, which keeps
    # meaning "the developer closed this conversation": a resolved Inquiry
    # keeps its exact resolved moment, and one the system expires while it
    # was still open/held keeps closed_at NULL rather than gaining a
    # resolved-looking timestamp.
    premise_evaluation: Optional[InquiryPremiseEvaluation] = None
    premise_successor_item_id: Optional[int] = None
    superseded_at: Optional[float] = None
    created_at: float
    updated_at: float
    closed_at: Optional[float] = None


class InterviewInquiryDetailOut(BaseModel):
    inquiry: InterviewInquiryOut
    messages: List[InterviewInquiryMessageOut] = Field(default_factory=list)


class InterviewInquiryListOut(BaseModel):
    session_id: int
    system_id: int
    items: List[InterviewInquiryOut] = Field(default_factory=list)


class InterviewInquiryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_kind: InterviewInquiryOriginKind
    origin_id: int
    question_text: str = Field(..., min_length=1, max_length=2_000)
    # Opaque to the server: the user's unconfirmed answer draft on the origin
    # item at the moment they opened the Inquiry, round-tripped back verbatim
    # via GET/resolve so the dashboard can restore it into the input without
    # the server ever interpreting or submitting it as an answer.
    held_draft: Optional[str] = Field(default=None, max_length=20_000)


class InterviewInquiryMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=2_000)


class InterviewInquiryTransitionRequest(BaseModel):
    """Optional audit fields for a hold/cancel/unresolved status change."""

    model_config = ConfigDict(extra="forbid")

    status_reason: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=200)


# --- Joint Understanding session (Epic #328 Phase A / Issue #329) -------------
#
# What 「わからない」 STARTS instead of ending. The three provenances
# (investigation / translation / developer) stay separate rows with separate
# rules -- see app/joint_understanding.py for the finite vocabularies these
# Literals mirror and shared/schemas/joint_understanding.schema.json for the
# contract itself. Nothing in this feature writes the origin confirmation
# item: 「わからない」 is an entry point into a shared investigation, never a
# recorded developer intent.

# Issue #389 adds a fifth origin: a Purpose Chain need (`app/purpose_needs.py`)
# whose developer response was 'unknown'/'investigate'. Its `origin_id` is a
# `purpose_need_response.id` -- not a row in any of the four original origin
# tables, because a need's target (an element or a relation) is a computed
# projection with a stable STRING id, not a database row `POST
# /purpose-chain/needs/{need_id}/respond` is the only writer of
# `trigger='purpose_need'`, mirroring the 'unknown_answer' rule immediately
# below: `trigger` records WHICH PATH opened the session, never a request
# body's claim.
JointUnderstandingOriginKind = Literal["qa", "intent", "review_item", "inquiry", "purpose_need"]
JointUnderstandingTrigger = Literal["unknown_answer", "explicit_request", "purpose_need"]
JointUnderstandingStatus = Literal["open", "held", "closed"]
# hypothesis_adopted is explicitly PROVISIONAL (never a fact); decided is the
# only final human value judgement. See SESSION_OUTCOMES.
JointUnderstandingOutcome = Literal[
    "understood", "doubt_resolved", "hypothesis_adopted", "decided",
    "handed_off", "abandoned",
]
JointUnderstandingClaimKind = Literal[
    "fact", "inference", "hypothesis", "unknown", "conflict"
]
JointUnderstandingOriginRole = Literal["investigation", "translation", "developer"]
JointUnderstandingActionKind = Literal[
    "request_investigation", "explain_reasoning", "compare_options",
    "adopt_hypothesis", "revise_intent", "hold", "handoff", "decide",
]
JointUnderstandingDecisionMethod = Literal["deterministic", "reasoning_llm", "manual"]
JointUnderstandingRuntimeCheck = Literal["match", "mismatch", "unobserved", "stale"]
# Issue #337: whether the premise this session was investigated against still
# holds, evaluated from the shared Issue #308 premise bundle rather than from
# the snapshot id alone. Only 'current' permits hypothesis_adopted / decided /
# reflux. 'missing' (the premise disappeared) and 'invalid' (no comparable
# bundle was ever captured) both used to report as 'fresh' -- a premise that
# cannot be found is not a premise that still holds.
JointUnderstandingPremiseState = Literal["current", "stale", "missing", "invalid"]
# The same set plus the pre-#337 value, for a premise verdict READ back from a
# row that was closed before this contract existed. Never produced anew.
JointUnderstandingRecordedPremiseState = Literal[
    "current", "stale", "missing", "invalid", "fresh",
]
# Issue #337: the single finite reason behind a non-'current' verdict. Split by
# recovery path -- stale can be re-investigated, missing cannot.
JointUnderstandingPremiseReason = Literal[
    "premise_not_captured", "premise_incomplete",
    "pinned_snapshot_removed", "origin_removed",
    "origin_superseded", "pinned_commit_changed", "origin_content_changed",
    "capability_scope_changed", "linked_intent_changed",
]
# Issue #337: WHICH code path produced a finding, as distinct from whose voice
# it speaks in (origin_role). 'legacy' is read-only -- what a row written
# before provenance was recorded reports.
JointUnderstandingProducerKind = Literal[
    "investigation_loop", "translator", "developer_api", "legacy",
]
# Issue #337: whether an authenticated human stands behind the row. Resolved
# from the request's Principal, never from its body.
JointUnderstandingActorKind = Literal["user", "system", "legacy"]
# Issue #337: the derived state of one provisionally adopted hypothesis.
JointUnderstandingAdoptionState = Literal[
    "provisional", "reconfirmation_required", "basis_withdrawn",
]
JointUnderstandingRefluxTargetKind = Literal["qa_investigation", "session_ledger"]
# Issue #336: the existing formal operation an action leads to. The three
# `*_correct` / `*_create` / `*_answer` values name an endpoint OUTSIDE this
# feature, and only that endpoint's own manual record completes them.
JointUnderstandingFormalOperation = Literal[
    "joint_investigate", "joint_translate", "joint_hold",
    "joint_close_provisional", "joint_close_decided",
    "intent_correct", "question_handoff_create", "origin_item_answer",
]


class JointUnderstandingEvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=500)
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    summary: str = Field(default="", max_length=1_000)


class JointUnderstandingEvidenceOut(BaseModel):
    path: str
    start_line: int
    end_line: int
    summary: str = ""


class JointUnderstandingRuntimeEvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(..., min_length=1, max_length=500)
    runtime_check: JointUnderstandingRuntimeCheck
    summary: str = Field(default="", max_length=1_000)


class JointUnderstandingRuntimeEvidenceOut(BaseModel):
    component_id: str
    runtime_check: JointUnderstandingRuntimeCheck
    summary: str = ""


class JointUnderstandingFindingOut(BaseModel):
    id: int
    joint_understanding_id: int
    system_id: int
    origin_role: JointUnderstandingOriginRole
    claim_kind: JointUnderstandingClaimKind
    statement: str
    evidence: List[JointUnderstandingEvidenceOut] = Field(default_factory=list)
    runtime_evidence: List[JointUnderstandingRuntimeEvidenceOut] = Field(default_factory=list)
    supports_finding_ids: List[int] = Field(default_factory=list)
    competing_explanations: List[str] = Field(default_factory=list)
    refutation_conditions: List[str] = Field(default_factory=list)
    next_investigation: Optional[str] = None
    uncertainty: str = ""
    supersedes_finding_id: Optional[int] = None
    decision_method: JointUnderstandingDecisionMethod
    intelligence_run_id: Optional[int] = None
    is_mock: bool = False
    # Issue #337: provenance on two independent axes. producer_kind is which
    # code path wrote the row; actor_kind is whether an authenticated human
    # stands behind it. Both are 'legacy' on rows written before the contract
    # existed -- unknown, and never assumed to be a human.
    producer_kind: JointUnderstandingProducerKind = "legacy"
    actor_kind: JointUnderstandingActorKind = "legacy"
    actor_username: Optional[str] = None
    created_at: float


class JointUnderstandingFindingCreate(BaseModel):
    """Append one DEVELOPER finding.

    Issue #337 made this endpoint developer-only. ``investigation`` and
    ``translation`` findings are written exclusively by their internal
    producers (``/investigate``, ``/translate``), which validate their evidence
    and their reasoning run against the pinned snapshot; accepting them here
    let a caller post an unverifiable "fact" with fabricated citations, and
    accepting ``developer`` from any caller let a request body record a
    sentence as the human's own judgement. ``origin_role`` is kept in the
    payload so the rejection is explicit rather than a silent reinterpretation.
    """

    model_config = ConfigDict(extra="forbid")

    origin_role: JointUnderstandingOriginRole
    claim_kind: JointUnderstandingClaimKind
    statement: str = Field(..., min_length=1, max_length=4_000)
    evidence: List[JointUnderstandingEvidenceIn] = Field(default_factory=list, max_length=20)
    runtime_evidence: List[JointUnderstandingRuntimeEvidenceIn] = Field(
        default_factory=list, max_length=20,
    )
    supports_finding_ids: List[int] = Field(default_factory=list, max_length=20)
    competing_explanations: List[str] = Field(default_factory=list, max_length=10)
    refutation_conditions: List[str] = Field(default_factory=list, max_length=10)
    next_investigation: Optional[str] = Field(default=None, max_length=2_000)
    uncertainty: str = Field(default="", max_length=2_000)
    supersedes_finding_id: Optional[int] = None
    decision_method: JointUnderstandingDecisionMethod
    intelligence_run_id: Optional[int] = None
    is_mock: bool = False


class JointUnderstandingActionOut(BaseModel):
    id: int
    joint_understanding_id: int
    system_id: int
    action_kind: JointUnderstandingActionKind
    # Display label only. Issue #337: `actor_kind`/`actor_username` are the
    # authenticated identity, resolved from the request's Principal.
    actor: Optional[str] = None
    actor_kind: JointUnderstandingActorKind = "legacy"
    actor_username: Optional[str] = None
    note: Optional[str] = None
    decision_method: Literal["manual"] = "manual"
    created_at: float


class JointUnderstandingActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_kind: JointUnderstandingActionKind
    # A caller-supplied label (e.g. a team name). Issue #337: it is NOT the
    # identity -- the recorded actor comes from the authenticated Principal, so
    # this can no longer be used to attribute an action to someone else.
    actor: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=2_000)


class JointUnderstandingOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    origin_kind: JointUnderstandingOriginKind
    origin_id: int
    trigger: JointUnderstandingTrigger
    question_text: str
    status: JointUnderstandingStatus
    outcome: Optional[JointUnderstandingOutcome] = None
    # Derived, never stored: true exactly for outcome='hypothesis_adopted'.
    # A provisional outcome must not be presented or reused as a fact.
    outcome_is_provisional: bool = False
    outcome_reason: Optional[str] = None
    # Issue #332: the findings the recorded outcome rests on, the premise
    # verdict evaluated when it was recorded, and the CURRENT verdict --
    # 'stale' means the interview session has moved to a newer snapshot than
    # this session pinned, so adopt/decide are refused until re-investigation.
    outcome_finding_ids: List[int] = Field(default_factory=list)
    outcome_premise_state: Optional[JointUnderstandingRecordedPremiseState] = None
    outcome_premise_reason: Optional[JointUnderstandingPremiseReason] = None
    # Issue #337: who closed the session, recorded from the authenticated
    # Principal. A close is a manual decision; an outcome whose decider cannot
    # be recovered after a reload is not an audit record.
    closed_by_actor_kind: Optional[JointUnderstandingActorKind] = None
    closed_by_username: Optional[str] = None
    # Issue #336: the origin row that is CURRENT today. For `qa` and `intent`,
    # corrections are additive -- the pinned `origin_id` becomes a superseded row
    # the moment the developer revises it -- so a consumer matching a session to
    # the live item must use this, not `origin_id`. Reported rather than
    # substituted: the session keeps pointing at the row the conversation
    # started from.
    current_origin_id: Optional[int] = None
    premise_state: JointUnderstandingPremiseState = "invalid"
    premise_reason: Optional[JointUnderstandingPremiseReason] = None
    # Issue #337: the shared Issue #308 premise bundle as captured at creation.
    # premise_commit_sha (not the snapshot id) is what decides staleness: the
    # same commit re-pinned under a new snapshot row is the same premise.
    premise_snapshot_id: Optional[int] = None
    premise_commit_sha: Optional[str] = None
    premise_revision_id: Optional[int] = None
    premise_tracking_version: Optional[str] = None
    premise_captured_at: Optional[float] = None
    schema_version: str
    created_at: float
    updated_at: float
    closed_at: Optional[float] = None


class JointUnderstandingAdoptionOut(BaseModel):
    """One provisionally adopted hypothesis (Issue #337).

    ``state`` is derived at read time from the captured premise versus the
    current one, so it can never claim `provisional` for an adoption whose
    ground has since moved.
    """

    id: int
    joint_understanding_id: int
    system_id: int
    finding_id: int
    state: JointUnderstandingAdoptionState
    adopted_by_actor_kind: JointUnderstandingActorKind
    adopted_by_username: Optional[str] = None
    adoption_reason: str = ""
    premise_snapshot_id: Optional[int] = None
    premise_commit_sha: Optional[str] = None
    premise_revision_id: Optional[int] = None
    decision_method: Literal["manual"] = "manual"
    adopted_at: float


class JointUnderstandingDetailOut(BaseModel):
    session: JointUnderstandingOut
    findings: List[JointUnderstandingFindingOut] = Field(default_factory=list)
    actions: List[JointUnderstandingActionOut] = Field(default_factory=list)
    # Issue #330: the per-round investigation audit (what each round read,
    # what it left unread, why the loop stopped). Forward-referenced because
    # the Phase B models are defined below; resolved by the model_rebuild()
    # call at the end of that block.
    investigation_rounds: List["JointUnderstandingRoundOut"] = Field(default_factory=list)
    # Issue #331: every translation pass, oldest first. The translated
    # sentences themselves are also in `findings` as origin_role='translation'
    # rows; this carries the summary/options/unknowns around them.
    translations: List["JointUnderstandingTranslationOut"] = Field(default_factory=list)
    # Issue #332: system-verified facts attached to the understanding surface
    # WITHOUT being recorded as anyone's answer.
    reflux: List["JointUnderstandingRefluxOut"] = Field(default_factory=list)
    # Issue #337: every provisionally adopted hypothesis, with its derived
    # re-confirmation state. This is what keeps a provisional adoption from
    # aging silently into something indistinguishable from a fact.
    hypothesis_adoptions: List[JointUnderstandingAdoptionOut] = Field(default_factory=list)
    # The finite next-action menu for the session's current status,
    # deterministically ordered (empty once closed/held).
    available_actions: List[JointUnderstandingActionKind] = Field(default_factory=list)


class JointUnderstandingListOut(BaseModel):
    session_id: int
    system_id: int
    items: List[JointUnderstandingOut] = Field(default_factory=list)


class JointUnderstandingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_kind: JointUnderstandingOriginKind
    origin_id: int
    trigger: JointUnderstandingTrigger
    question_text: str = Field(..., min_length=1, max_length=2_000)


class JointUnderstandingCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: JointUnderstandingOutcome
    # Issue #337: the developer's stated judgement, now REQUIRED. A close is
    # the manual decision record of this conversation; "what was decided" with
    # no text is an outcome label, not a decision anyone can audit later.
    outcome_reason: str = Field(..., min_length=1, max_length=2_000)
    # Issue #332: which findings the outcome rests on. Required for
    # 'hypothesis_adopted' and 'decided' -- an adoption or a decision that
    # cannot name its basis is not auditable. Issue #337 additionally rejects a
    # superseded, mock, or (for an adoption) non-hypothesis basis.
    outcome_finding_ids: List[int] = Field(default_factory=list, max_length=50)


class JointUnderstandingHoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(default=None, max_length=500)


# --- Iterative investigation (Epic #328 Phase B / Issue #330) -----------------

JointUnderstandingStopReason = Literal[
    "answered", "budget_exhausted", "no_new_evidence", "unresolved", "failed",
]
JointUnderstandingRoundStatus = Literal["completed", "unresolved", "failed"]
# Issue #339: the finite outcome class, so a caller never has to inspect
# `stop_reason` and guess which side of the limitation/failure split it is on.
JointUnderstandingOutcomeClass = Literal[
    "answered", "research_limitation", "execution_failure",
]
# Issue #339: WHERE an execution failure broke, because the recovery differs --
# configuration and a missing snapshot are not retries, an API/schema/timeout
# failure is.
JointUnderstandingFailureClass = Literal[
    "config_invalid", "snapshot_unavailable", "api_failure", "schema_invalid",
    "timeout",
]
# Issue #339: the finite exploration sources. The first four are Epic #328's;
# the last four are the structural breadth this issue adds.
JointUnderstandingExplorationSourceKind = Literal[
    "path_name", "symbol_index", "entrypoint_index", "file_content",
    "dependency", "call_graph", "git_history", "runtime_facts",
]


class JointUnderstandingExplorationSourceOut(BaseModel):
    """One exploration source's contribution to one round (Issue #339)."""

    id: int
    round_id: int
    system_id: int
    source_kind: JointUnderstandingExplorationSourceKind
    # The pinned commit for git history, the snapshot id for the index /
    # content / runtime sources.
    revision: str
    candidates_found: int = 0
    queries_run: int = 0
    elapsed_seconds: float = 0.0
    truncated: bool = False
    # A failed source is recorded and skipped: it never fails the round, and it
    # is never replaced by an unbounded fallback search.
    error_details: Optional[str] = None
    created_at: float


class JointUnderstandingRoundOut(BaseModel):
    """Audit of one investigation round: what it read, what it left, why."""

    id: int
    joint_understanding_id: int
    system_id: int
    round_index: int
    status: JointUnderstandingRoundStatus
    # Only the round that ended the loop carries a stop reason.
    stop_reason: Optional[JointUnderstandingStopReason] = None
    conclusion: str = ""
    # The state carried into the next round and restored on a retry.
    search_leads: List[str] = Field(default_factory=list)
    open_hypotheses: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    read_paths: List[str] = Field(default_factory=list)
    # Candidates this round selected but could not read within budget --
    # "not looked at" stays distinguishable from "not there".
    unread_candidates: List[str] = Field(default_factory=list)
    pruned_findings: int = 0
    files_read: int = 0
    chars_read: int = 0
    llm_calls: int = 0
    elapsed_seconds: float = 0.0
    intelligence_run_id: Optional[int] = None
    error_details: Optional[str] = None
    # Issue #339: set ONLY for an execution failure. A research limitation
    # (budget_exhausted / no_new_evidence / unresolved) is a real,
    # evidence-backed result and leaves this NULL -- "the system looked and
    # could not tell" must stay distinguishable from "the system could not
    # look".
    failure_class: Optional[JointUnderstandingFailureClass] = None
    outcome_class: JointUnderstandingOutcomeClass = "research_limitation"
    # One entry per exploration source this round used, each with its own
    # revision and budget consumption. Per source because "the round only read
    # the pinned revision" is a claim about each source separately.
    sources: List["JointUnderstandingExplorationSourceOut"] = Field(
        default_factory=list,
    )
    created_at: float


class JointUnderstandingInvestigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional narrowing hints; both only affect deterministic candidate
    # retrieval, never the conclusion.
    research_focus: Optional[str] = Field(default=None, max_length=1_000)
    search_keywords: Optional[List[str]] = Field(default=None, max_length=20)
    max_rounds: Optional[int] = Field(default=None, ge=1, le=5)


class JointUnderstandingInvestigateOut(BaseModel):
    joint_understanding_id: int
    system_id: int
    stop_reason: JointUnderstandingStopReason
    rounds: List[JointUnderstandingRoundOut] = Field(default_factory=list)
    # The findings this call appended (origin_role='investigation').
    findings: List[JointUnderstandingFindingOut] = Field(default_factory=list)
    error: Optional[str] = None



# --- Translation (Epic #328 Phase C / Issue #331) -----------------------------

JointUnderstandingStatementLayer = Literal[
    "purpose", "impact", "gap", "consistency", "decision"
]


class JointUnderstandingStatementOut(BaseModel):
    """One translated sentence plus the traceability it must never lose."""

    layer: JointUnderstandingStatementLayer
    claim_kind: JointUnderstandingClaimKind
    text: str
    # The investigation findings this sentence was derived from, and the
    # translation finding row that persists it. Both are always present:
    # a generalized explanation must always resolve back to the technical
    # claim and its evidence.
    supports_finding_ids: List[int] = Field(default_factory=list)
    finding_id: int


class JointUnderstandingOptionOut(BaseModel):
    label: str
    what_changes: str
    tradeoffs: str = ""
    supports_finding_ids: List[int] = Field(default_factory=list)


class JointUnderstandingActionMenuEntryOut(BaseModel):
    """A finite next action plus what choosing it actually changes.

    Assembled deterministically from the fixed server catalog -- never
    generated text, and identical for identical session state.
    """

    action_kind: JointUnderstandingActionKind
    label: str
    what_changes: str
    # Issue #336: which existing formal operation this action leads to, and
    # whether that operation is performed by an endpoint OUTSIDE this feature.
    # Recording the action never completes it -- the distinction previously had
    # no machine-readable form, so a conversation could accumulate actions that
    # never became anything.
    formal_operation: JointUnderstandingFormalOperation
    completes_outside_session: bool = False


class JointUnderstandingTranslationOut(BaseModel):
    id: int
    joint_understanding_id: int
    system_id: int
    purpose_summary: str
    statements: List[JointUnderstandingStatementOut] = Field(default_factory=list)
    options: List[JointUnderstandingOptionOut] = Field(default_factory=list)
    open_unknowns: List[str] = Field(default_factory=list)
    decision_question: Optional[str] = None
    # Deterministic gate: true only when the remaining question really is a
    # value judgement AND the developer has material to decide with.
    ask_developer: bool = False
    intelligence_run_id: Optional[int] = None
    is_mock: bool = False
    created_at: float


class JointUnderstandingTranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional context about the developer's own goal. Never invented by the
    # server and never recorded as a developer finding.
    goal_hint: Optional[str] = Field(default=None, max_length=2_000)


class JointUnderstandingTranslateOut(BaseModel):
    translation: JointUnderstandingTranslationOut
    # The finite next-action menu that accompanies the explanation.
    action_menu: List[JointUnderstandingActionMenuEntryOut] = Field(default_factory=list)


# --- Reflux (Epic #328 Phase D / Issue #332) ---------------------------------


class JointUnderstandingRefluxOut(BaseModel):
    """One system-verified fact attached to the understanding surface.

    ``decision_method`` is always ``reasoning_llm``: a refluxed fact is what
    the system established, never what the developer answered. No reflux row
    corresponds to a write of an answer, an intent value, or a decision.
    """

    id: int
    joint_understanding_id: int
    system_id: int
    finding_id: int
    target_kind: JointUnderstandingRefluxTargetKind
    target_id: Optional[int] = None
    statement: str
    evidence: List[JointUnderstandingEvidenceOut] = Field(default_factory=list)
    runtime_evidence: List[JointUnderstandingRuntimeEvidenceOut] = Field(default_factory=list)
    decision_method: Literal["reasoning_llm"] = "reasoning_llm"
    intelligence_run_id: Optional[int] = None
    premise_snapshot_id: Optional[int] = None
    created_at: float


# Issue #338: the finite lineage vocabularies. Mirrored here from
# app/joint_lineage.py so an out-of-set value is not representable in the API.
JointUnderstandingLineageEventKind = Literal[
    "unknown_created", "unknown_resolved", "unknown_remained",
    "hypothesis_created", "hypothesis_confirmed", "hypothesis_reversed",
    "hypothesis_corrected", "hypothesis_superseded",
    "question_asked", "question_reasked", "question_withdrawn",
    "decision_proposed", "decision_adopted", "decision_rejected",
    "decision_undone",
    "classification_corrected",
]
JointUnderstandingLineageSubjectKind = Literal[
    "unknown", "hypothesis", "question", "decision", "classification",
]
# Issue #338: `threshold_unset` is neither a pass nor a failure. The criterion
# is measured; what counts as enough is a decision nobody has made yet, and
# inventing a number here would be the self-reported readiness score this issue
# forbids (the same discipline #341 applies to its metric thresholds).
JointUnderstandingBulkApprovalVerdict = Literal["unmeasured", "threshold_unset"]


class JointUnderstandingLineageEventOut(BaseModel):
    event_kind: JointUnderstandingLineageEventKind
    subject_kind: JointUnderstandingLineageSubjectKind
    # A finding id for an unknown/hypothesis; a Joint Understanding session id
    # for a question/decision/classification.
    subject_id: int
    session_id: int
    joint_understanding_id: int
    at: float
    # The successor that closed this subject's lineage, where there is one. This
    # is what makes an unknown's creation and its resolution ONE lineage rather
    # than two unrelated counts.
    supersedes_subject_id: Optional[int] = None
    detail: str = ""


class JointUnderstandingSessionBurdenOut(BaseModel):
    joint_understanding_id: int
    session_id: int
    rounds: int = 0
    developer_actions: int = 0
    developer_findings: int = 0
    questions_asked: int = 0
    reasks: int = 0


class JointUnderstandingBulkApprovalCriterionOut(BaseModel):
    """One observed count behind Issue #311's start condition."""

    key: Literal["observed_sessions", "misclassification_cases", "undo_cases"]
    observed: int
    verdict: JointUnderstandingBulkApprovalVerdict
    note: str


class JointUnderstandingLineageOut(BaseModel):
    system_id: int
    schema_version: Literal["joint-understanding-lineage-v1"] = (
        "joint-understanding-lineage-v1"
    )
    generated_at: float
    events: List[JointUnderstandingLineageEventOut] = Field(default_factory=list)
    burdens: List[JointUnderstandingSessionBurdenOut] = Field(default_factory=list)
    # Observation only. Deliberately NOT a go/no-go for Issue #311.
    bulk_approval_readiness: List[JointUnderstandingBulkApprovalCriterionOut] = Field(
        default_factory=list,
    )


class JointUnderstandingRefluxResultOut(BaseModel):
    joint_understanding_id: int
    system_id: int
    target_kind: JointUnderstandingRefluxTargetKind
    premise_state: JointUnderstandingPremiseState
    # Newly attached this call; already-attached facts are not duplicated.
    refluxed: List[JointUnderstandingRefluxOut] = Field(default_factory=list)
    already_refluxed: int = 0
    # Findings that stayed inside the conversation because they are not
    # system-established facts (inference / hypothesis / unknown / conflict).
    skipped_not_fact: int = 0
    skipped_unverified: int = 0


# Issue #336: the single 「わからない」 entry point's finite next step. The
# internal route names (system_researchable / hybrid / human_only) deliberately
# do not appear here -- the Dashboard maps these to its own copy, and an
# internal classification name is not a developer-facing label.
# The Question Router's finite classification, mirrored here because this
# response is a new contract and an out-of-set value must not be representable.
# The router owns the canonical set (app/question_router.ROUTE_CATEGORIES).
JointUnderstandingRouteCategory = Literal[
    "human_only", "system_researchable", "hybrid",
]
JointUnderstandingUnknownNextStep = Literal[
    # A joint investigation ran and produced findings; the conversation has
    # material to show.
    "joint_investigation_started",
    # A session is open (investigation skipped, failed, or found nothing new).
    # The conversation is retryable and nothing was lost.
    "joint_understanding_opened",
    # Only the developer can answer this. The normal answer / handoff path is
    # what comes next; no session was opened.
    "developer_answer_required",
    # The classification itself could not be made (configuration or API
    # failure). Fail-closed: no session, no guess, and the recorded unknown
    # answer stands.
    "routing_unavailable",
]


class InterviewQaUnknownRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 「わからない」 with an optional note. Never becomes a developer finding
    # (Epic #328): "I don't know" is not a statement about the system.
    answer_text: str = Field(default="", max_length=20_000)
    actor: str = Field(..., min_length=1, max_length=200)
    # Off only for callers that want to open the session and drive the
    # investigation themselves (the JU panel's own retry does this).
    investigate: bool = True


class InterviewQaUnknownOut(BaseModel):
    session_id: int
    system_id: int
    # The question row as recorded -- committed before routing runs, so it is
    # present on every outcome including the failure ones.
    qa: "InterviewQaOut"
    route_category: Optional[JointUnderstandingRouteCategory] = None
    knowledge_area: Optional[KnowledgeArea] = None
    joint_understanding_id: Optional[int] = None
    next_step: JointUnderstandingUnknownNextStep
    investigation_stop_reason: Optional[JointUnderstandingStopReason] = None
    error: Optional[str] = None


JointUnderstandingDetailOut.model_rebuild()


# --- Alignment Review / Review Queue (Issue #287) -----------------------------
#
# Contrasts confirmed/proposed Intent Brief items against the evidence-backed
# Current System understanding to produce alignment items with a
# deterministic review classification (review_category/reason_code -- see
# app/alignment.py's rule table). Only review_category IN (must_review,
# batch_reviewable) ever surfaces as an action-required Review Queue card.

# Issue #290: defined here (ahead of the Runtime Reality Check section
# further down) because AlignmentItemOut.runtime_check needs
# RuntimeCheckState. See that section for the full provenance envelope
# model these finite sets belong to.
RuntimeFactFreshness = Literal["fresh", "stale", "unobserved"]
RuntimeCheckState = Literal["match", "mismatch", "unobserved", "stale"]

AlignmentState = Literal["aligned", "gap", "unknown", "conflict", "not_applicable"]
AlignmentRiskFlag = Literal["security", "high_risk", "core_intent"]
AlignmentConfidence = Literal["confirmed", "likely", "uncertain", "conflicting"]
AlignmentReviewCategory = Literal[
    "must_review", "batch_reviewable", "no_review_required", "unchanged", "informational",
]
AlignmentReasonCode = Literal[
    "security_related", "high_risk", "core_intent", "conflict_detected",
    "low_confidence", "runtime_mismatch", "routine_update", "no_change",
    "informational_only", "core_capability_changed",
    "unchanged_since_confirmation",
]
# Item-level user progress. 'inquiry' is set while an Inquiry
# (origin_kind='review_item') is open on this item, and reset to 'open'
# (never 'answered') when that Inquiry closes -- the developer must still
# explicitly answer via this item's own endpoint (Principle 2).
AlignmentItemStatus = Literal["open", "answered", "corrected", "held", "inquiry"]
# Issue #321: how one freshly built row relates to the previous generation of
# the same discussion point -- see app/inquiry_premise.py's SUBJECT_STATES.
# 'removed' is deliberately absent: a subject with no row in the current
# build is a premise-evaluation result (#323), not a property of a row.
AlignmentSubjectState = Literal[
    "new", "unchanged", "changed", "ambiguous", "untrackable"
]
# The three decisions POST /answer accepts as request input.
AlignmentDecisionAction = Literal["accept_current", "needs_change", "reject_interpretation"]
# The full set of actions that may appear in a persisted user_decision.action
# -- a superset of AlignmentDecisionAction covering what /correct and /hold
# each record (Principle 7: every manual write path leaves an audit action).
AlignmentUserDecisionAction = Literal[
    "accept_current", "needs_change", "reject_interpretation", "corrected", "held",
]


class AlignmentEvidenceOut(BaseModel):
    path: str
    start_line: int
    end_line: int
    summary: str = ""


class AlignmentUserDecisionOut(BaseModel):
    action: AlignmentUserDecisionAction
    note: Optional[str] = None
    decided_at: float
    decided_by: Optional[str] = None


class AlignmentCapabilityDependencyOut(BaseModel):
    """Human-reviewable canonical scope attached to one Alignment item."""

    target_kind: Literal["entity", "relation"]
    entity_id: Optional[int] = None
    relation_id: Optional[int] = None
    entity_kind: Optional[CapabilityEntityKind] = None
    entity_name: Optional[str] = None
    supported_entity_id: Optional[int] = None
    supported_entity_name: Optional[str] = None
    supporting_entity_id: Optional[int] = None
    supporting_entity_name: Optional[str] = None


class AlignmentItemOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    revision_id: Optional[int] = None
    snapshot_id: int
    intent_item_id: Optional[int] = None
    intent_summary: Optional[str] = None
    current_claim: str
    current_evidence: List[AlignmentEvidenceOut] = Field(default_factory=list)
    gap_summary: Optional[str] = None
    proposed_interpretation: Optional[str] = None
    alignment_state: AlignmentState
    risk_flags: List[AlignmentRiskFlag] = Field(default_factory=list)
    confidence: AlignmentConfidence
    review_category: AlignmentReviewCategory
    reason_code: AlignmentReasonCode
    user_reason: str
    # Issue #312: these exact canonical dependencies are visible to the
    # reviewer; accept_current confirms the claim and this scope together.
    capability_confirmation_id: Optional[int] = None
    capability_dependencies: List[AlignmentCapabilityDependencyOut] = Field(
        default_factory=list
    )
    # Issue #290: deterministic Runtime Reality Check match state, set only
    # when this item's evidence deterministically maps to a component_id
    # with runtime trace facts; null when no deterministic mapping exists
    # (never guessed from free text -- app/runtime_alignment.py).
    runtime_check: Optional[RuntimeCheckState] = None
    status: AlignmentItemStatus
    user_decision: Optional[AlignmentUserDecisionOut] = None
    # Issue #291: set when this review item has been handed off to an
    # assignee (question_handoff.id, origin_kind='review_item'). Creating
    # the handoff sets status='held' (the same value /hold already uses)
    # alongside this column.
    handoff_id: Optional[int] = None
    # Review-finding fix (Finding 4): true when a later rebuild produced a
    # fresh replacement row for the same contrast point while this row was
    # already answered/corrected. Superseded rows are history only -- never
    # an action card -- and are additionally excluded from
    # GET .../review-queue. Additive column; defaults False so pre-migration
    # rows and any DB row missing the column still validate.
    superseded: bool = False
    # Issue #295: realizes Issue #287's reserved 'unchanged' review_category.
    # content_hash is the deterministic sha256 (app/alignment.py's
    # compute_content_hash) over this item's identity-bearing fields, set on
    # every build; NULL only for rows written before this migration.
    # carried_over_from is the id of the immediately-preceding build's
    # terminal (answered/corrected) row this item's content exactly matched,
    # set only when review_category == 'unchanged'; NULL otherwise
    # (audit-only -- never a live FK join for decision-making).
    content_hash: Optional[str] = None
    carried_over_from: Optional[int] = None
    # Issue #313: every freshly classified row carries the reviewed external
    # policy version and the SHA-256 of that exact YAML artifact. Legacy rows
    # retain an explicit legacy version and no digest rather than pretending
    # they were classified by the external policy.
    policy_version: str = "legacy-code-v1"
    policy_digest: Optional[str] = None
    # Exact first-match YAML rule that produced the category/reason pair.
    # Legacy and carried-over rows may not have one.
    policy_rule_id: Optional[str] = None
    # Issue #310: the deterministic category/reason_code remains unchanged.
    # This flag only exposes an explicit human request to recheck this exact
    # item in the normal Review Queue.
    manual_recheck_required: bool = False
    # Issue #321: stable discussion-point identity and physical lineage.
    # review_subject_id is a deterministic digest over structural anchors
    # only (Intent field + confirmed Capability entity/relation ids); it is
    # null for legacy rows and for items with no stable anchor, which are
    # reported subject_state='untrackable'. replaces_item_id is set only
    # when exactly one predecessor generation row carried the same subject
    # -- a split/merge is reported 'ambiguous' and left unbound rather than
    # guessed.
    review_subject_id: Optional[str] = None
    subject_state: Optional[AlignmentSubjectState] = None
    replaces_item_id: Optional[int] = None
    intelligence_run_id: int
    is_mock: bool = False
    created_at: float
    updated_at: float


class AlignmentBuildOut(BaseModel):
    session_id: int
    system_id: int
    revision_id: Optional[int] = None
    intelligence_run_id: int
    is_mock: bool = False
    items: List[AlignmentItemOut] = Field(default_factory=list)


class AlignmentListOut(BaseModel):
    session_id: int
    system_id: int
    items_by_category: Dict[str, List[AlignmentItemOut]] = Field(default_factory=dict)
    counts: Dict[str, int] = Field(default_factory=dict)
    # 2nd review round (PR #296, Finding 3): `counts` still includes current
    # rows in a terminal status (answered/corrected, superseded=0) -- e.g.
    # right after answering a must_review item and before the next rebuild
    # marks it superseded=1, counts.must_review still counts it even though
    # GET .../review-queue no longer does. `outstanding_counts` applies the
    # EXACT SAME predicate get_review_queue uses (superseded=0 AND status NOT
    # IN ('answered','corrected')) to every category consistently, so a
    # client that wants "how many of these still need action" always agrees
    # with the Review Queue's own count. Additive; `counts` keeps its
    # original meaning ("current rows of this category") for compatibility.
    outstanding_counts: Dict[str, int] = Field(default_factory=dict)
    # Review fix (PR #296, Finding 3): superseded=1 rows (history -- a later
    # rebuild already produced a fresh replacement row for the same contrast
    # point) are additive-only here, kept fully visible for audit but split
    # out of items_by_category/counts so those two fields only ever reflect
    # CURRENT rows (superseded=0). Never used to drive Review Queue counts.
    superseded_items: List[AlignmentItemOut] = Field(default_factory=list)


class AlignmentReviewQueueOut(BaseModel):
    session_id: int
    system_id: int
    items: List[AlignmentItemOut] = Field(default_factory=list)


class AlignmentRuleObjectionOut(BaseModel):
    """Deterministic, System-scoped aggregation of sample objections."""

    reason_code: AlignmentReasonCode
    policy_version: str
    policy_digest: Optional[str] = None
    policy_rule_id: str
    objection_count: int
    pending_recheck_count: int


class AlignmentRuleObjectionListOut(BaseModel):
    system_id: int
    rules: List[AlignmentRuleObjectionOut] = Field(default_factory=list)


class AlignmentRuleRecheckRequest(BaseModel):
    """Exact reviewed rule provenance selected by the human."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = Field(..., min_length=1, max_length=200)
    policy_digest: Optional[str] = Field(default=None, max_length=128)
    policy_rule_id: str = Field(..., min_length=1, max_length=200)


class AlignmentRuleRecheckOut(BaseModel):
    system_id: int
    reason_code: AlignmentReasonCode
    policy_version: str
    policy_digest: Optional[str] = None
    policy_rule_id: str
    decision_method: Literal["manual"] = "manual"
    requested_by_user_id: int
    recheck_target_count: int


class AlignmentAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: AlignmentDecisionAction
    note: Optional[str] = Field(default=None, max_length=2_000)


class AlignmentCorrectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corrected_interpretation: str = Field(..., min_length=1, max_length=2_000)


# --- Batch answer (PR #296 review fix, Finding 5) ----------------------------
#
# One review batch (the developer clears several Review Queue cards at once)
# previously fired one Issue #288 request_refresh call PER item answered --
# de-duplicated down to at most "1 running + 1 queued" by
# interview_refresh._enqueue, but still up to 2 rebuilds for what is
# conceptually a single batch, or as many as the item count under an eager
# refresh policy. This endpoint answers every item in one request and calls
# request_refresh exactly once, only after at least one item's answer is
# durably committed.
#
# Same fields as AlignmentAnswerRequest, plus item_id so one entry can target
# any alignment_item in the session. MAX_BATCH_ANSWERS mirrors the existing
# deterministic per-call cap pattern (Issue #286's MAX_BATCH_QUESTIONS,
# app/routes/question_router.py) -- a finite, explicit bound (Principle 6),
# not a heuristic one.
MAX_BATCH_ANSWERS = 50


class AlignmentBatchAnswerItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: int
    decision: AlignmentDecisionAction
    note: Optional[str] = Field(default=None, max_length=2_000)
    # 2nd review round (PR #296, Finding 2): optional staleness guard. When
    # given, it must match the item's CURRENT alignment_item.content_hash or
    # the entry is rejected as a per-item error (the item changed -- e.g. a
    # rebuild carried it over to a new row, or another reviewer/tab already
    # answered it -- since the client staged this answer). Omitting it keeps
    # pre-fix behavior (no staleness check) for backward compatibility.
    content_hash: Optional[str] = Field(default=None, max_length=64)


class AlignmentBatchAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: List[AlignmentBatchAnswerItemRequest] = Field(
        default_factory=list, max_length=MAX_BATCH_ANSWERS,
    )


class AlignmentBatchAnswerItemResult(BaseModel):
    item_id: int
    success: bool
    # Populated only when success is True; the full updated item, exactly
    # like the single-item POST .../answer response, so the caller never
    # needs a follow-up GET for the items that saved cleanly.
    item: Optional[AlignmentItemOut] = None
    # Populated only when success is False -- a concise, structural reason
    # (not found / wrong session / Inquiry-locked / duplicate item_id in this
    # batch), never LLM free text.
    error: Optional[str] = None


class AlignmentBatchAnswerOut(BaseModel):
    session_id: int
    system_id: int
    results: List[AlignmentBatchAnswerItemResult] = Field(default_factory=list)
    # True iff at least one item in this batch was durably saved and
    # request_refresh was therefore called exactly once for the whole batch;
    # False when every item failed (refresh is never called on a total miss).
    refreshed: bool = False


# --- Answerable knowledge areas / handoff (Issue #291) ------------------------
#
# A developer picks which knowledge areas they can answer NOW (no role
# inference, Principle 6). KnowledgeArea (defined earlier, alongside
# InterviewSessionStatus) is the same finite set the Question Router
# (app/question_router.py question-router-v2) tags a question with; empty
# session.answerable_areas means "no filtering" (unchanged default
# behavior), never "all areas explicitly selected".


class AnswerableAreasUpdateRequest(BaseModel):
    """Replace the session's answerable-areas selection. Changeable anytime."""

    model_config = ConfigDict(extra="forbid")

    areas: List[KnowledgeArea] = Field(default_factory=list, max_length=5)


class QuestionHandoffEvidenceRef(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    start_line: int = 0
    end_line: int = 0
    summary: str = Field(default="", max_length=2_000)


class QuestionHandoffCreate(BaseModel):
    """Hand an out-of-area (or otherwise deferred) item off to an assignee.

    ``assignee`` is a free-text name/address -- no org auth system exists
    yet (same convention as ``understanding_confirmed_by`` /
    ``interview_qa.answered_by``).
    """

    model_config = ConfigDict(extra="forbid")

    origin_kind: HandoffOriginKind
    origin_id: int
    assignee: str = Field(..., min_length=1, max_length=200)
    background: str = Field(..., min_length=1, max_length=4_000)
    needed_decision: str = Field(..., min_length=1, max_length=2_000)
    evidence: Optional[List[QuestionHandoffEvidenceRef]] = Field(default=None, max_length=10)
    due_note: Optional[str] = Field(default=None, max_length=500)
    priority: HandoffPriority = "normal"
    created_by: Optional[str] = Field(default=None, max_length=200)


class QuestionHandoffOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    origin_kind: HandoffOriginKind
    origin_id: int
    assignee: str
    background: str
    needed_decision: str
    evidence: Optional[List[QuestionHandoffEvidenceRef]] = None
    due_note: Optional[str] = None
    priority: HandoffPriority
    status: HandoffStatus
    answer_text: Optional[str] = None
    answered_by: Optional[str] = None
    answered_at: Optional[float] = None
    created_by: Optional[str] = None
    created_at: float
    updated_at: float


class QuestionHandoffListOut(BaseModel):
    session_id: int
    system_id: int
    items: List[QuestionHandoffOut] = Field(default_factory=list)


class QuestionHandoffAnswerRequest(BaseModel):
    """The assignee's own answer.

    Never written into the origin qa/alignment row -- see the
    ``question_handoff`` table docstring in ``db.py``. The original user
    must still explicitly confirm it via ``/return`` and the origin item's
    own answer endpoint (Principle 2).
    """

    model_config = ConfigDict(extra="forbid")

    answer_text: str = Field(..., min_length=1, max_length=20_000)
    answered_by: str = Field(..., min_length=1, max_length=200)


# --- Automatic refresh after an answer batch (Issue #288) --------------------
#
# app/interview_refresh.py's request_refresh()/run_refresh_job() keep
# affected Understanding / Alignment / Review Queue state current after a Q&A
# answer, Intent confirm/correct, Alignment answer/correct, or applied change
# set, without the manual 「理解を更新」 action. trigger_kind/status are
# explicit finite sets (Principle 6).

RefreshTriggerKind = Literal[
    "qa_answer", "intent_update", "alignment_answer", "nl_change_set",
]
RefreshJobStatus = Literal["pending", "updating", "updated", "failed", "stale"]


class RefreshJobOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    trigger_kind: RefreshTriggerKind
    status: RefreshJobStatus
    error: Optional[str] = None
    intelligence_run_id: Optional[int] = None
    result_revision_id: Optional[int] = None
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class RefreshStatusOut(BaseModel):
    session_id: int
    system_id: int
    latest_job: Optional[RefreshJobOut] = None
    pending_count: int = 0


# --- Natural-language bulk correction -> structured change set (Issue #289) --
#
# app/change_sets.py turns a developer's free-text correction into a
# structured, previewed, selectively-applied change set -- NL is never
# applied to state directly (Principle 2/6). target_kind/resolution_state
# are explicit finite sets; 'forbidden' means the (target_kind, field) pair
# is outside the whitelist (app/change_sets.py's ALLOWED_TARGET_FIELDS),
# not merely unresolved.

ChangeSetStatus = Literal[
    "proposed", "previewed", "partially_applied", "applied", "discarded", "failed",
]
ChangeTargetKind = Literal["intent_item", "understanding_claim"]
ChangeResolutionState = Literal["resolved", "ambiguous", "conflict", "stale", "forbidden"]


class ChangeSetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=8_000)


class ChangeSetOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    base_revision_id: Optional[int] = None
    source_text: str
    status: ChangeSetStatus
    intelligence_run_id: int
    is_mock: bool = False
    created_at: float
    updated_at: float


class ChangeSetAffectedItemOut(BaseModel):
    """One alignment/review item a change item's edit would touch,
    determined by a deterministic structural match (Principle 6) -- never a
    reasoning decision. See ``routes/interview_change_sets.py``'s
    ``_affected_alignment_items``."""

    alignment_item_id: int
    current_claim: str
    review_category: str


class ChangeSetItemOut(BaseModel):
    id: int
    change_set_id: int
    system_id: int
    target_kind: ChangeTargetKind
    target_ref: Dict[str, Any] = Field(default_factory=dict)
    field: str
    before_value: Optional[str] = None
    after_value: str
    reason: str
    resolution_state: ChangeResolutionState
    applied: bool = False
    applied_at: Optional[float] = None
    created_at: float
    affected_items: List[ChangeSetAffectedItemOut] = Field(default_factory=list)


class ChangeSetDetailOut(BaseModel):
    change_set: ChangeSetOut
    items: List[ChangeSetItemOut] = Field(default_factory=list)
    rebuild_note: str


class ChangeSetApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ids: List[int] = Field(..., min_length=1, max_length=100)


class ChangeSetSkippedItemOut(BaseModel):
    item_id: int
    resolution_state: ChangeResolutionState
    message: str


class ChangeSetApplyResultOut(BaseModel):
    change_set: ChangeSetOut
    applied_item_ids: List[int] = Field(default_factory=list)
    skipped: List[ChangeSetSkippedItemOut] = Field(default_factory=list)
    result_revision_id: Optional[int] = None


# --- Runtime Reality Check (Issue #135) --------------------------------------
#
# Reconciles approved interview metadata/probe plans (role, probe_value,
# state_effects, recommended_mode) against deterministic runtime trace
# aggregates for the same component_id, and asks the developer to confirm or
# correct any surprising mismatch. Aggregation is numeric-only (Principle 6,
# decision_method deterministic); which mismatches are worth a question, and
# the question text/hypothesis, are reasoning_llm output persisted as
# question_source "runtime" interview_qa rows.


class RuntimeTraceFactsOut(BaseModel):
    """Deterministic trace aggregates for one component_id over a window.

    ``has_traces`` is false (and the numeric fields are null) when the
    component has an approved probe plan but zero recorded traces — the
    "0 traces" signal called out in the issue. This is a raw fact; whether
    it is worth a question is decided by the reasoning step, never here.
    """

    component_id: str
    window_days: int
    call_count: int = 0
    error_count: int = 0
    error_rate: Optional[float] = None
    duration_p50_ms: Optional[float] = None
    duration_p90_ms: Optional[float] = None
    duration_p99_ms: Optional[float] = None
    # Issue #290: earliest trace timestamp inside the aggregation window
    # (None when has_traces is False), used by the provenance envelope's
    # observed_at.first alongside last_observed_at's observed_at.last.
    first_observed_at: Optional[float] = None
    last_observed_at: Optional[float] = None
    has_traces: bool = False
    # Issue #290 Finding 5: the most recent non-empty traces.environment /
    # traces.git_sha value observed for this component in the aggregation
    # window (deterministic "latest by timestamp" pick — never a semantic
    # choice). None when no trace in the window carried the field, which is
    # the honest "unknown" signal build_provenance() relies on instead of
    # inventing a value from the caller's pinned snapshot.
    observed_environment: Optional[str] = None
    observed_git_sha: Optional[str] = None


# --- Runtime fact provenance / match state (Issue #290) ----------------------
#
# Wraps RuntimeTraceFactsOut with WHERE the facts came from and HOW current
# they are, so a fact is never silently presented as current/authoritative
# once it has gone stale (Principle 5 stale guard). ``environment`` is only
# ever populated from actual trace metadata (``RuntimeTraceFactsOut.
# observed_environment``, Issue #290 Finding 5's SDK-reported
# PROBE_ENVIRONMENT) -- never invented, and never the caller's pinned
# snapshot or expected environment. ``snapshot_ref`` follows the same rule:
# it is derived only from ``observed_git_sha`` (an actually-observed trace
# tag, resolved against ``repository_snapshots`` by exact commit-sha match
# when possible), never from the analysis session's pinned snapshot --
# fabricating it from the pinned snapshot was Finding 5(b) of the Issue
# #290 review. ``freshness`` and ``runtime_check`` are both finite sets
# (Principle 6): freshness is a pure function of RUNTIME_FACT_FRESH_SECONDS
# vs last_observed_at (app/runtime_reality.py); runtime_check is
# app/runtime_alignment.py's compare_claim_to_runtime result. NOTE:
# RuntimeFactFreshness/RuntimeCheckState themselves are defined earlier in
# this module (just above the Alignment Review section) because
# AlignmentItemOut needs RuntimeCheckState.


class RuntimeFactSnapshotRefOut(BaseModel):
    # None when the observed git_sha does not match any known
    # repository_snapshots row for the System (Issue #290 Finding 5) -- the
    # raw sha is still carried in git_sha even when it cannot be resolved.
    snapshot_id: Optional[int] = None
    git_sha: Optional[str] = None


class RuntimeFactProvenanceOut(BaseModel):
    environment: Optional[str] = None
    first_observed_at: Optional[float] = None
    last_observed_at: Optional[float] = None
    snapshot_ref: Optional[RuntimeFactSnapshotRefOut] = None
    source: Literal["trace_aggregation"] = "trace_aggregation"
    freshness: RuntimeFactFreshness


class RuntimeRealityCheckItemOut(BaseModel):
    """One approved element's declared understanding paired with its facts."""

    proposal_id: int
    decision_id: int
    path: str
    qualified_name: str
    component_id: str
    role: Optional[str] = None
    probe_value: Optional[str] = None
    state_effects: List[str] = Field(default_factory=list)
    recommended_mode: str = "trace"
    facts: RuntimeTraceFactsOut


class RuntimeRealityFactsOut(BaseModel):
    """Response for the deterministic-only facts endpoint (no LLM call)."""

    session_id: int
    system_id: int
    snapshot_id: int
    window_days: int
    items: List[RuntimeRealityCheckItemOut] = Field(default_factory=list)


class RuntimeRealityCheckRunOut(BaseModel):
    """Response for triggering the reasoning reconciliation run.

    ``skipped`` is true when generation was suppressed because unanswered
    runtime questions already exist for this session (noise control from the
    issue notes); in that case no intelligence_runs row is created.
    """

    session_id: int
    system_id: int
    snapshot_id: int
    intelligence_run: Optional[IntelligenceRunOut] = None
    items_considered: int = 0
    created_qa_ids: List[int] = Field(default_factory=list)
    skipped: bool = False
    skipped_reason: Optional[str] = None
    error: Optional[str] = None


# --- Investigation Agent runtime_fact evidence (Issue #290) ------------------
#
# Extends Issue #286's Investigation Agent with a second evidence kind
# alongside code citations: a runtime_fact entry cites a component_id (never
# invented -- must be one of the components deterministically offered to the
# model, mirroring how code evidence must cite an actually-read path) plus
# its provenance envelope and the finite runtime_check state. When the
# deterministic freshness is 'unobserved'/'stale' the persisted
# ``runtime_check`` always matches that deterministic value regardless of
# what the model said (Principle 5 stale guard); only when facts are fresh
# does the model's own match/mismatch judgement (a semantic call, Principle
# 6) get recorded as-is.


class InvestigationRuntimeEvidenceOut(BaseModel):
    kind: Literal["runtime_fact"] = "runtime_fact"
    component_id: str
    provenance: RuntimeFactProvenanceOut
    runtime_check: RuntimeCheckState
    summary: str = ""


class InterviewInquiryRuntimeEvidenceOut(InvestigationRuntimeEvidenceOut):
    pass


class SuggestedObservationProposalOut(BaseModel):
    target_component: str
    reason: Literal["unobserved", "stale"]


# --- Observation proposal (Issue #290) ---------------------------------------
#
# A developer's request to start capturing NEW runtime observation (as
# opposed to reading facts that already exist) is never auto-started
# (Principle 5/8): POST .../observation-proposals only ever records a
# proposal row; approving it (decision_method='manual') does NOT itself
# start anything -- the response only points back at the existing
# PUT /components/{component_id}/policy endpoint that already sets a
# component's trace/shadow mode.

RuntimeObservationProposalStatus = Literal["proposed", "approved", "rejected", "expired"]


class RuntimeObservationProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_component: str = Field(..., min_length=1, max_length=500)
    purpose: str = Field(..., min_length=1, max_length=2_000)
    expected_cost: Optional[str] = Field(default=None, max_length=500)
    risk_note: Optional[str] = Field(default=None, max_length=2_000)
    retention_note: Optional[str] = Field(default=None, max_length=2_000)
    origin_inquiry_id: Optional[int] = None
    origin_alignment_item_id: Optional[int] = None


class RuntimeObservationProposalOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    origin_inquiry_id: Optional[int] = None
    origin_alignment_item_id: Optional[int] = None
    target_component: str
    purpose: str
    expected_cost: Optional[str] = None
    risk_note: Optional[str] = None
    retention_note: Optional[str] = None
    status: RuntimeObservationProposalStatus
    decision_by: Optional[str] = None
    decision_at: Optional[float] = None
    created_at: float
    # Deterministic, fixed (never LLM free text): only present once
    # status='approved', pointing at the existing policy endpoint that
    # actually starts trace/shadow capture (this proposal never does).
    policy_pointer: Optional[str] = None


class RuntimeObservationProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_by: Optional[str] = Field(default=None, max_length=200)


# --- Understanding Revisions (Issue #136) ------------------------------------
#
# Each successful update-understanding call appends one row (never
# overwritten) so the Dashboard can show what changed since the previous
# revision. Diffing is deterministic (exact-name matching only, Principle 6)
# and computed on demand — no diff result is persisted.


class UnderstandingRevisionOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    snapshot_id: int
    intelligence_run_id: Optional[int] = None
    current_understanding: Optional[Dict[str, Any]] = None
    gap_analysis: Optional[List[Dict[str, Any]]] = None
    created_at: float


class UnderstandingRevisionListOut(BaseModel):
    session_id: int
    system_id: int
    items: List[UnderstandingRevisionOut] = Field(default_factory=list)


class UnderstandingDiffConfidenceChange(BaseModel):
    name: str
    before: Optional[str] = None
    after: Optional[str] = None


class UnderstandingDiffSectionOut(BaseModel):
    section: str
    added: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    confidence_changed: List[UnderstandingDiffConfidenceChange] = Field(default_factory=list)
    summary_changed: List[str] = Field(default_factory=list)


class UnderstandingDiffOut(BaseModel):
    """Structural diff between two understanding revisions.

    ``has_previous`` is false when ``to_revision_id`` is the session's first
    revision (or no revisions exist yet); ``sections`` is then empty and the
    caller must show "no comparison target" rather than an all-added diff.
    """

    session_id: int
    system_id: int
    from_revision_id: Optional[int] = None
    to_revision_id: Optional[int] = None
    has_previous: bool = False
    sections: List[UnderstandingDiffSectionOut] = Field(default_factory=list)


# --- Interview Proposal Approval (Issue #70) ----------------------------------
#
# Per-item approval gate: a developer can approve, reject, or edit each
# proposed { docstring_metadata, probe_plan } item. Decisions are persisted
# as decision_method='manual' records that reference — but do not overwrite —
# the original reasoning_llm proposal.

InterviewDecisionAction = Literal["approved", "rejected", "edited"]


class InterviewProposalApproveRequest(BaseModel):
    """Approve a proposal as-is."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=200)


class InterviewProposalRejectRequest(BaseModel):
    """Reject a proposal."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=200)


class InterviewProposalEditRequest(BaseModel):
    """Edit and approve a proposal with corrected values."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=200)
    metadata: InterviewProposalMetadataBlock
    probe_plan: InterviewProposalProbePlan


class InterviewProposalDecisionOut(BaseModel):
    """A persisted manual decision on a proposal."""

    id: int
    proposal_id: int
    session_id: int
    system_id: int
    decision: InterviewDecisionAction
    decision_method: DecisionMethod
    actor: str
    edited_metadata: Optional[InterviewProposalMetadataBlock] = None
    edited_probe_plan: Optional[InterviewProposalProbePlan] = None
    denylist_hit: Optional[str] = None
    decided_at: float


class InterviewApprovedItemOut(BaseModel):
    """An item from the approved set, ready for materialization.

    Contains the effective metadata/probe_plan: the edited values if the
    decision was 'edited', or the original proposal values if 'approved'.
    """

    proposal_id: int
    path: str
    qualified_name: str
    symbol_id: Optional[int] = None
    metadata: InterviewProposalMetadataBlock
    probe_plan: InterviewProposalProbePlan
    decision: InterviewDecisionAction
    decision_id: int
    actor: str
    decided_at: float


class InterviewApprovedSetOut(BaseModel):
    """The approved set for a session: items eligible for materialization."""

    session_id: int
    system_id: int
    snapshot_id: int
    items: List[InterviewApprovedItemOut] = Field(default_factory=list)
    total_proposals: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0


# --- Interview Materialization (Issue #71) ------------------------------------
#
# Materializes approved docstring metadata + probe instrumentation into a
# single reviewable diff from an isolated worktree. The target repo's
# tracked branches are never written to.


class InterviewMaterializeRequest(BaseModel):
    """Request to materialize the approved set into a reviewable diff."""

    model_config = ConfigDict(extra="forbid")

    worktree_base: Optional[str] = Field(
        default=None,
        description="Base directory for the temporary worktree. "
        "Defaults to system temp if not provided.",
    )


class InterviewMaterializeOut(BaseModel):
    """Result of materializing approved proposals into a diff."""

    session_id: int
    system_id: int
    snapshot_id: int
    commit_sha: Optional[str] = None
    diff: str
    files_changed: int
    items_materialized: int
    skipped: List[str] = Field(default_factory=list)
    materialized_at: float
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# System Understanding (Issue #86)
# ---------------------------------------------------------------------------

PipelineStepStatus = Literal["complete", "missing", "warning", "blocked", "failed"]


class SystemUnderstandingPipelineStepOut(BaseModel):
    step: str
    status: PipelineStepStatus
    detail: Optional[str] = None
    label: str = ""


class SystemUnderstandingGapSummaryOut(BaseModel):
    gap_type: str
    count: int


class SystemUnderstandingMetadataCoverageOut(BaseModel):
    symbol_count: int = 0
    symbols_with_source_metadata: int = 0
    entrypoint_count: int = 0
    entrypoints_with_capability_link: int = 0


class SystemUnderstandingCapabilitySummaryOut(BaseModel):
    name: str
    summary: Optional[str] = None
    provenance_kind: Optional[str] = None


class SystemUnderstandingEntrypointSummaryOut(BaseModel):
    entrypoint_type: str
    entrypoint_id: str
    category: Optional[str] = None
    label: Optional[str] = None


class SystemUnderstandingSymbolSummaryOut(BaseModel):
    path: str
    qualified_name: str
    kind: Optional[str] = None
    route_path: Optional[str] = None
    route_method: Optional[str] = None
    component_id: Optional[str] = None


class SystemUnderstandingPurposeOut(BaseModel):
    name: str
    summary: Optional[str] = None
    provenance_kind: Optional[str] = None


# Issue #94/#275: the manual system_profile purpose surfaced as a parallel
# provenance view next to the AI/source-derived purpose. `source` is a finite
# set (Principle 6): "system_profile" is the human-entered PUT /system-profile
# record; "capability_hierarchy" / "system_profile_draft" are the two
# AI/structural sources `_load_purpose` already reads, kept distinguishable so
# the Dashboard can label them separately.
SystemUnderstandingPurposeViewSource = Literal[
    "system_profile", "capability_hierarchy", "system_profile_draft"
]


class SystemUnderstandingPurposeViewOut(BaseModel):
    source: SystemUnderstandingPurposeViewSource
    provenance_kind: str
    name: str
    summary: Optional[str] = None
    updated_at: Optional[float] = None


# Finite stale reasons for a purpose confirmation, computed structurally at
# read time (Principle 6): the manual/AI sides are compared against their
# current persisted values, never inferred.
SystemUnderstandingPurposeConfirmationStaleReason = Literal[
    "profile_updated", "snapshot_changed", "ai_updated"
]


class SystemUnderstandingPurposeConfirmationOut(BaseModel):
    id: int
    snapshot_id: int
    understanding_build_id: Optional[int] = None
    decided_by_user_id: Optional[int] = None
    decision_method: str
    manual_purpose: str
    ai_purpose_name: Optional[str] = None
    ai_purpose_summary: Optional[str] = None
    ai_source: Optional[str] = None
    ai_provenance_kind: Optional[str] = None
    note: Optional[str] = None
    created_at: float
    stale: bool = False
    stale_reason: Optional[SystemUnderstandingPurposeConfirmationStaleReason] = None


class SystemUnderstandingPurposeConfirmationCreate(BaseModel):
    """Record a human 'confirmed' decision between the manual system_profile
    purpose and the current AI/source-derived purpose view.

    Both ids are optional only for wire-level backward compatibility. The
    endpoint fail-closes with 409 unless `snapshot_id` matches the latest
    ready snapshot and `understanding_build_id` matches its latest completed
    build, so a confirmation cannot race a rebuild after the view was read.
    """

    snapshot_id: Optional[int] = None
    understanding_build_id: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class SystemUnderstandingGapNextActionOut(BaseModel):
    action: str
    link: Optional[str] = None


class SystemUnderstandingGapDocRef(BaseModel):
    path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class SystemUnderstandingGapSymbolRef(BaseModel):
    path: Optional[str] = None
    qualified_name: Optional[str] = None


class SystemUnderstandingGapEntrypointRef(BaseModel):
    entrypoint_type: Optional[str] = None
    entrypoint_ref: Optional[str] = None


class IssueDraftRefOut(BaseModel):
    """Lightweight reference to an issue draft, embedded next to its gap."""

    id: int
    status: str
    external_url: Optional[str] = None
    title: str


GapTriageStatus = Literal["open", "acknowledged", "dismissed", "resolved"]
GapTriageDecisionMethod = Literal["manual", "deterministic"]
GapTriageReopenReason = Literal["content_changed", "resolved_gap_reappeared"]


class GapTriageDecisionOut(BaseModel):
    id: int
    system_id: int
    snapshot_id: Optional[int] = None
    gap_key: str
    content_fingerprint: str
    status: GapTriageStatus
    decided_by_user_id: Optional[int] = None
    decision_method: GapTriageDecisionMethod
    note: Optional[str] = None
    created_at: float


class GapTriageUpdateRequest(BaseModel):
    gap_key: str = Field(min_length=1, max_length=4000)
    content_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    status: GapTriageStatus
    note: Optional[str] = Field(default=None, max_length=1000)


class SystemUnderstandingGapOut(BaseModel):
    gap_type: Optional[str] = None
    severity: str = "info"
    title: Optional[str] = None
    node_name: Optional[str] = None
    notes: Optional[str] = None
    capability_key: Optional[str] = None
    doc_refs: List[SystemUnderstandingGapDocRef] = Field(default_factory=list)
    symbol_refs: List[SystemUnderstandingGapSymbolRef] = Field(default_factory=list)
    entrypoint_refs: List[SystemUnderstandingGapEntrypointRef] = Field(default_factory=list)
    code_refs: List[Dict[str, Any]] = Field(default_factory=list)
    next_actions: List[SystemUnderstandingGapNextActionOut] = Field(default_factory=list)
    # Issue #107: stable identity for matching drafts back to this gap, plus any
    # issue drafts already generated for it (with registered external URLs).
    # source_id is a stable per-gap identifier (graph node id / entrypoint
    # identity) folded into source_key; it round-trips so a draft created from a
    # POSTed gap resolves to the same key the display computed.
    source_id: Optional[str] = None
    source_key: Optional[str] = None
    issue_drafts: List[IssueDraftRefOut] = Field(default_factory=list)
    # Issue #276: human-readable, snapshot-stable locator and a separate
    # semantic content fingerprint. ``triage_status`` is the effective state;
    # it becomes open when a dismissed gap's fingerprint changes even before
    # the deterministic reopen audit row is materialized by the next action.
    # Optional on the shared model for backward compatibility with the
    # existing POST /issue-drafts payload, which accepts caller-supplied gaps
    # created before Issue #276. Server-generated System Understanding gaps
    # always populate both fields in `_system_understanding_to_out`.
    gap_key: Optional[str] = None
    content_fingerprint: Optional[str] = None
    triage_status: GapTriageStatus = "open"
    triage_decision: Optional[GapTriageDecisionOut] = None
    triage_reopen_reason: Optional[GapTriageReopenReason] = None


# Issue #202: finite stage completion status shown as a badge in the Hub.
# Derived purely from persisted state (system_understanding_service.
# _derive_stage_statuses); no reasoning model involved (Principle 6).
SystemUnderstandingStageStatusValue = Literal[
    "not_started", "in_progress", "blocked", "complete"
]


class SystemUnderstandingStageStatusOut(BaseModel):
    stage: str
    status: str
    counts: Dict[str, int] = Field(default_factory=dict)
    # Issue #240: server-supplied Japanese display copy (optional so existing
    # dashboard contract tests that build this object without them stay valid;
    # the Dashboard prefers these over its local STAGE_LABELS fallback).
    label: str = ""
    description: str = ""


# Issue #203: deterministic before/after comparison of gap counts between the
# two most recent settled (completed/partial) builds of the same system,
# read back from system_understanding_gap_history. A gap_type present in
# only one of the two builds has 0 on the side where it did not appear.
class SystemUnderstandingGapTrendOut(BaseModel):
    gap_type: str
    current: int
    previous: int


class SystemUnderstandingOut(BaseModel):
    system_id: int
    snapshot_id: Optional[int] = None
    understanding_build_id: Optional[int] = None
    commit_sha: Optional[str] = None
    pipeline: List[SystemUnderstandingPipelineStepOut] = Field(default_factory=list)
    purpose: Optional[SystemUnderstandingPurposeOut] = None
    capabilities: List[SystemUnderstandingCapabilitySummaryOut] = Field(default_factory=list)
    entrypoints: List[SystemUnderstandingEntrypointSummaryOut] = Field(default_factory=list)
    major_symbols: List[SystemUnderstandingSymbolSummaryOut] = Field(default_factory=list)
    gaps: List[SystemUnderstandingGapOut] = Field(default_factory=list)
    gap_summary: List[SystemUnderstandingGapSummaryOut] = Field(default_factory=list)
    metadata_coverage: Optional[SystemUnderstandingMetadataCoverageOut] = None
    # Issue #202: deterministic completion status + counts for each of the 4
    # Hub stages (understand / observe / instrument / evaluate).
    stages: List[SystemUnderstandingStageStatusOut] = Field(default_factory=list)
    # Issue #203: gap-count trend across the last two settled builds (empty
    # until 2 builds have recorded history).
    gap_trend: List[SystemUnderstandingGapTrendOut] = Field(default_factory=list)
    # Issue #201's `primary_action`, Issue #174's `next_actions`, and Issue
    # #203's `understanding_refresh_recommended` were removed in Issue #239.
    # The canonical "what should the user do next" projection is now
    # `GET /system-state`'s `primary_item` / `page_items` (Issue #238).
    # Issue #240: server-supplied Japanese summary shown when the whole
    # pipeline is complete (None otherwise); replaces the Dashboard's
    # client-assembled English success string.
    success_summary: Optional[str] = None
    # Issue #94/#275: manual system_profile purpose surfaced as a parallel
    # provenance view next to the AI/source-derived purpose (`purpose` above
    # keeps its exact existing semantics unchanged). Manual view is
    # snapshot-independent; the AI view is included only when a ready
    # snapshot exists.
    purpose_views: List[SystemUnderstandingPurposeViewOut] = Field(default_factory=list)
    # The latest human "confirmed" record reconciling the manual and AI
    # purpose views, or None if never confirmed.
    purpose_confirmation: Optional[SystemUnderstandingPurposeConfirmationOut] = None


class CapabilityContextProbePlanOut(BaseModel):
    """Issue #175: lightweight Probe Plan reference for the capability context API."""

    id: int
    feature_id: str
    objective: str
    status: ProbePlanStatus
    created_at: float
    updated_at: float


class CapabilityContextExperimentOut(BaseModel):
    """Issue #175: lightweight Experiment reference, with decision state, for the
    capability context API."""

    id: int
    feature_id: str
    objective: str
    status: str
    human_decision: str
    human_decision_variant_key: Optional[str] = None
    created_at: float


class CapabilityContextOut(BaseModel):
    """Issue #175: gaps / probe plans / experiments explicitly linked to one
    capability_key, for the Capability detail panel. Every item here is joined
    by an exact key match (capability_key or feature_id) — never a guess."""

    capability_key: str
    gaps: List[SystemUnderstandingGapOut] = Field(default_factory=list)
    probe_plans: List[CapabilityContextProbePlanOut] = Field(default_factory=list)
    experiments: List[CapabilityContextExperimentOut] = Field(default_factory=list)


class SystemUnderstandingBuildStepOut(BaseModel):
    """One orchestrated step of a System Understanding build job (Issue #109)."""

    id: int
    step: str
    status: str  # pending, running, completed, failed, blocked, cancelled
    depends_on: List[str] = Field(default_factory=list)
    reused_existing: bool = False
    cancel_requested: bool = False
    error: Optional[str] = None
    artifact_provenance: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: Optional[float] = None
    heartbeat_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class SystemUnderstandingLlmTaskSummaryOut(BaseModel):
    """Aggregate counts of chunk-level LLM tasks for a build job."""

    total: int = 0
    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    reused: int = 0


class SystemUnderstandingArtifactCountsOut(BaseModel):
    """Deterministic persisted artifact counts for the job's snapshot."""

    symbols: int = 0
    entrypoints: int = 0
    understanding_graph_claims: int = 0
    capability_hierarchy_nodes: int = 0


class SystemUnderstandingBuildOut(BaseModel):
    id: int
    job_id: int
    # Latest execution (initial enqueue or retry) of this job. None only for
    # legacy rows created before run tracking existed.
    run_id: Optional[int] = None
    system_id: int
    snapshot_id: Optional[int] = None
    # completed only when every step completed; blocked/cancelled/failed
    # steps yield partial (or failed when no step completed).
    status: str  # queued, running, completed, partial, failed, cancelled
    current_step: Optional[str] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    is_stuck: bool = False
    heartbeat_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    created_at: float
    steps: List[SystemUnderstandingBuildStepOut] = Field(default_factory=list)
    llm_tasks: Optional[SystemUnderstandingLlmTaskSummaryOut] = None
    artifact_counts: Optional[SystemUnderstandingArtifactCountsOut] = None


class SystemUnderstandingJobRetryIn(BaseModel):
    """Optional step name to retry only that step (plus its dependents)."""

    step: Optional[str] = None


# ---------------------------------------------------------------------------
# Issue drafts (Issue #107)
# ---------------------------------------------------------------------------


class IssueDraftCreateRequest(BaseModel):
    """Generate an issue draft from a System Understanding gap.

    The server renders the Markdown body deterministically and pins the current
    snapshot id / commit sha, so callers only supply the gap they are looking at.
    """

    source_type: Literal[
        "system_understanding_gap", "interview", "probe_proposal"
    ] = "system_understanding_gap"
    gap: SystemUnderstandingGapOut
    # The snapshot the gap was displayed against. When provided, the server
    # rejects the request (409) if a newer snapshot has since become ready, so a
    # draft never embeds a snapshot id / commit sha that disagrees with the gap
    # evidence the caller was looking at.
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None


class IssueDraftUpdateRequest(BaseModel):
    """Partial update of an issue draft.

    Any field left unset is preserved. `external_url` uses the model's set-ness
    (exclude_unset) so passing `""` clears a previously registered URL while
    omitting it leaves the current value untouched.
    """

    title: Optional[str] = None
    body_markdown: Optional[str] = None
    status: Optional[
        Literal["draft", "copied", "external_created", "closed", "rejected"]
    ] = None
    external_url: Optional[str] = None


class IssueDraftOut(BaseModel):
    id: int
    system_id: int
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    source_type: str
    source_key: Optional[str] = None
    gap_type: Optional[str] = None
    severity: Optional[str] = None
    node_name: Optional[str] = None
    title: str
    body_markdown: str
    status: str
    external_url: Optional[str] = None
    # Issue #158: True when the draft's originating snapshot/commit no longer
    # matches the latest ready snapshot, so the analysis behind it may be out of
    # date. Computed at read time; never persisted.
    stale: bool = False
    created_at: float
    updated_at: float


class GitHubIssueStatusOut(BaseModel):
    """Whether GitHub issue creation is available for the current system's
    configured repository (Issue #158 External Issue Loop)."""

    available: bool
    owner: Optional[str] = None
    repo: Optional[str] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# System diagnostics (Issue #101)
# ---------------------------------------------------------------------------

DiagnosticSeverity = Literal["ok", "warning", "error", "blocked", "unknown"]


class DiagnosticLastObservedErrorOut(BaseModel):
    source: str
    status: str
    error: Optional[str] = None
    observed_at: Optional[float] = None


class SystemDiagnosticCheckOut(BaseModel):
    check_id: str
    category: str
    title: str
    severity: DiagnosticSeverity
    detail: str
    impact: str = ""
    remediation: str = ""
    related_env: List[str] = Field(default_factory=list)
    related_paths: List[str] = Field(default_factory=list)
    related_pages: List[str] = Field(default_factory=list)
    related_pipeline_steps: List[str] = Field(default_factory=list)
    last_observed_error: Optional[DiagnosticLastObservedErrorOut] = None
    decision_method: Literal["deterministic"] = "deterministic"
    # Issue #115: where the user fixes the problem.
    fix_kind: Literal["navigate", "dialog"] = "dialog"
    fix_page: Optional[str] = None
    fix_anchor: Optional[str] = None


class SystemDiagnosticsOut(BaseModel):
    system_id: int
    generated_at: float
    overall_severity: DiagnosticSeverity
    severity_counts: Dict[str, int] = Field(default_factory=dict)
    checks: List[SystemDiagnosticCheckOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-screen assistant (Issue #102)
# ---------------------------------------------------------------------------


class SettingMetadataOut(BaseModel):
    key: str
    display_name: str
    category: str
    requiredness: Literal["required", "conditional", "optional"]
    description: str
    impact: str
    remediation: str
    valid_values: Optional[List[str]] = None
    validation_rule: str = ""
    related_checks: List[str] = Field(default_factory=list)
    related_pages: List[str] = Field(default_factory=list)
    related_pipeline_steps: List[str] = Field(default_factory=list)
    docs_link: str = ""
    # Settings explanations are static code-managed metadata, never LLM output.
    decision_method: Literal["deterministic"] = "deterministic"


class SettingsMetadataOut(BaseModel):
    settings: List[SettingMetadataOut] = Field(default_factory=list)


class AssistantSuggestedQuestionOut(BaseModel):
    question: str
    source: Literal["diagnostics", "static"]
    check_id: str = ""


class AssistantScreenContextOut(BaseModel):
    screen_id: str
    title: str
    route: str
    purpose: str
    primary_data_sources: List[str] = Field(default_factory=list)
    visible_sections: List[str] = Field(default_factory=list)
    common_questions: List[str] = Field(default_factory=list)
    related_settings: List[str] = Field(default_factory=list)
    related_checks: List[str] = Field(default_factory=list)
    related_pipeline_steps: List[str] = Field(default_factory=list)
    related_endpoints: List[str] = Field(default_factory=list)
    # Current deterministic state for this screen (diagnostics subset).
    state_severity: DiagnosticSeverity = "ok"
    screen_checks: List[SystemDiagnosticCheckOut] = Field(default_factory=list)
    suggested_questions: List[AssistantSuggestedQuestionOut] = Field(default_factory=list)


class AssistantAskRequest(BaseModel):
    screen_id: str = Field(..., min_length=1, max_length=100)
    question: str = Field(..., min_length=1, max_length=4000)
    route_params: Dict[str, str] = Field(default_factory=dict)
    visible_check_ids: List[str] = Field(default_factory=list, max_length=50)
    visible_state_ids: List[str] = Field(default_factory=list, max_length=50)
    focused_state_id: Optional[str] = Field(default=None, max_length=200)


# ---------------------------------------------------------------------------
# System State Assessment (Issue #193)
# ---------------------------------------------------------------------------

StateSeverity = Literal["ok", "info", "warning", "blocked", "error"]
StateStatus = Literal[
    "satisfied", "missing", "unconfirmed", "stale", "impacted",
    "blocked", "running", "failed", "ready",
]
StateUserActionKind = Literal[
    "none", "configure", "create_snapshot", "build", "confirm",
    "review", "rerun", "inspect", "wait",
]
StateInterventionTiming = Literal["now", "before_next_step", "optional", "after_build", "none"]
StateGroup = Literal[
    "repository", "snapshot", "pipeline", "understanding", "interview",
    "runtime", "proposal", "configuration",
]
# User phase (Issue #237; extended to the full 6-step improvement flow by
# Issue #256): setup -> preparation -> instrumentation -> observation ->
# evaluation -> publish (terminal display phase).
UserPhase = Literal[
    "setup", "preparation", "instrumentation", "observation", "evaluation", "publish",
]


class SystemStateTargetUiOut(BaseModel):
    route: str
    anchor: Optional[str] = None
    action_label: str = ""


class SystemStateItemOut(BaseModel):
    state_id: str
    state_group: StateGroup
    severity: StateSeverity
    status: StateStatus
    user_action_kind: StateUserActionKind
    intervention_timing: StateInterventionTiming
    subject: str
    summary: str
    detail: str
    impact: str = ""
    remediation: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    target_ui: Optional[SystemStateTargetUiOut] = None
    display_routes: List[str] = Field(default_factory=list)
    related_checks: List[str] = Field(default_factory=list)
    related_pipeline_steps: List[str] = Field(default_factory=list)
    source: str = "system_state"
    dedupe_key: str = ""
    scope: str = "global"
    # System State Assessment is deterministic and LLM-free (Issue #193 Phase 1).
    decision_method: Literal["deterministic"] = "deterministic"
    # Fixed state_group -> phase mapping plus a small explicit per-item
    # override list (Issue #237); see system_state._phase_for_item. Default
    # is the terminal display phase (Issue #256), matching
    # system_state._phase_for_item's own fallback default.
    phase: UserPhase = "publish"


class SystemStatePhaseCompletionOut(BaseModel):
    phase: UserPhase
    complete: bool
    label: str = ""


class SystemStateAssessmentOut(BaseModel):
    system_id: int
    generated_at: float
    overall_severity: StateSeverity
    severity_counts: Dict[str, int] = Field(default_factory=dict)
    items: List[SystemStateItemOut] = Field(default_factory=list)
    primary_item: Optional[SystemStateItemOut] = None
    notification_items: List[SystemStateItemOut] = Field(default_factory=list)
    page_items: Dict[str, List[SystemStateItemOut]] = Field(default_factory=dict)
    # User phase (Issue #237): the current phase plus each phase's
    # completion condition. Additive; existing fields above are unchanged.
    user_phase: UserPhase = "setup"
    phases: List[SystemStatePhaseCompletionOut] = Field(default_factory=list)


class AssistantActionOut(BaseModel):
    label: str
    kind: Literal["navigate", "configure", "operate"]
    target: str
    detail: str = ""


class AssistantCitationOut(BaseModel):
    type: Literal["setting", "diagnostic_check", "pipeline_step", "state_item"]
    id: str
    title: str = ""
    detail: str = ""


# GitHub App publish workflow (Issue #216, sub-task 1): connection
# persistence models. `status` is a finite set enforced in the route layer;
# no field here ever carries an installation token or private key material
# (Principle 5/8 -- tokens are brokered per-call and never stored).
GithubConnectionStatus = Literal["pending", "connected", "error", "disconnected"]


class GithubAppStatusOut(BaseModel):
    configured: bool
    app_id: Optional[str] = None
    api_base_url: str
    web_base_url: str
    allowed_organization: Optional[str] = None


GithubInstallationStatus = Literal["active", "disabled"]


class GithubInstallationRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: int = Field(..., gt=0)


class GithubInstallationOut(BaseModel):
    installation_id: int
    github_account_login: str
    github_account_type: str
    status: GithubInstallationStatus
    registered_by_user_id: Optional[int] = None
    verified_at: str
    disabled_by_user_id: Optional[int] = None
    disabled_at: Optional[str] = None
    created_at: str
    updated_at: str
    assigned_system_ids: List[int] = Field(default_factory=list)


class GithubConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str = Field(..., min_length=1)
    repo: str = Field(..., min_length=1)
    installation_id: int


class GithubConnectionOut(BaseModel):
    id: int
    system_id: int
    api_base_url: str
    web_base_url: str
    owner: str
    repo: str
    clone_url: str
    installation_id: int
    default_branch: Optional[str] = None
    credential_type: str
    status: GithubConnectionStatus
    last_error: Optional[str] = None
    last_synced_at: Optional[str] = None
    last_synced_commit_sha: Optional[str] = None
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    created_at: str
    updated_at: str


# Repository manager status (Issue #216, sub-task 2). `mirror_path` is
# root-relative (never the absolute host path) so it is safe to return to a
# Dashboard client.
class GithubRepositoryStatusOut(BaseModel):
    connection_id: int
    mirror_exists: bool
    mirror_path: Optional[str] = None
    default_branch: Optional[str] = None
    last_synced_at: Optional[str] = None
    last_synced_commit_sha: Optional[str] = None


# Read-only installation repository listing (Issue #216, sub-task 4) -- used by
# the Dashboard's connection-creation form to let the developer pick a repo
# instead of typing owner/repo by hand. Never carries a token.
class GithubInstallationRepositoryOut(BaseModel):
    owner: str
    name: str
    default_branch: Optional[str] = None
    private: bool


# Publish job state machine (Issue #216, sub-task 3). `status` is the finite
# ordered set enforced by app/publish_job.py; no field here ever carries an
# installation token (Principle 5/8 -- `error` is always sanitized before
# persistence, so it is safe to return verbatim).
PublishJobStatus = Literal[
    "pending",
    "authenticating",
    "fetching",
    "checking_out",
    "applying_patch",
    "validating",
    "awaiting_approval",
    "committing",
    "pushing",
    "creating_pr",
    "completed",
    "failed",
    "cancelled",
    # Issue #226: resting/active states a publish-phase failure or a retry
    # can land in. `retryable_failed` / `manual_intervention_required` are
    # not terminal -- only retry/cancel/disconnect move a job out of them.
    "retryable_failed",
    "reconciling",
    "manual_intervention_required",
]


class PublishJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_id: int


class PublishJobOut(BaseModel):
    id: int
    system_id: int
    connection_id: int
    patch_id: int
    snapshot_id: int
    base_branch: str
    base_commit_sha: Optional[str] = None
    branch_name: Optional[str] = None
    commit_sha: Optional[str] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    status: PublishJobStatus
    error: Optional[str] = None
    validation_summary: Optional[Dict[str, Any]] = None
    requested_by_user_id: Optional[int] = None
    approved_by_user_id: Optional[int] = None
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None
    created_at: float
    updated_at: float
    approved_at: Optional[float] = None
    completed_at: Optional[float] = None
    heartbeat_at: Optional[float] = None
    retry_count: int = 0
    last_attempt_at: Optional[float] = None


# Append-only audit trail entry for the GitHub publish workflow (Issues
# #227/#226) -- `detail` is parsed JSON (or None), never a raw token/path
# (Principle 5/8; `publish_audit.record_publish_audit_event` already
# enforces that at write time).
class PublishAuditEventOut(BaseModel):
    id: int
    job_id: Optional[int] = None
    connection_id: Optional[int] = None
    event_type: str
    actor_user_id: Optional[int] = None
    detail: Optional[Dict[str, Any]] = None
    created_at: float


class AssistantAskOut(BaseModel):
    screen_id: str
    answer: str
    suggested_actions: List[AssistantActionOut] = Field(default_factory=list)
    citations: List[AssistantCitationOut] = Field(default_factory=list)
    used_fallback: bool
    fallback_reason: Optional[str] = None
    decision_method: Literal["deterministic", "reasoning_llm"]
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    generated_at: float


# --- Replay engine (Issue #242 Phase B / #244) -------------------------------

# Finite classification sets (Principle 6). Kept in sync with
# app/replay_runner.py and the replay_case_results table comments.
ReplayCaseStatus = Literal["match", "mismatch", "error", "skipped"]
ReplayInputSource = Literal["structured", "repr_partial"]
ReplaySkipReason = Literal[
    "unreplayable_capture",
    "repr_parse_failed",
    "undecodable_input",
    "trace_missing",
]
ReplaySetSource = Literal["manual", "analyzer_run"]
ReplayApprovalStatus = Literal["approved", "revoked"]


class ReplayApprovalCreate(BaseModel):
    reason: str = Field(..., min_length=1)


class ReplayRiskPointOut(BaseModel):
    """A persisted probe plan point label reused as display-only risk context.

    No new reasoning run and no heuristic inference: these are verbatim
    stored labels; absent labels are returned as absent (None)."""

    point_id: int
    plan_id: int
    side_effect_risk: Optional[str] = None
    replayability: Optional[str] = None


class ReplayRiskContextOut(BaseModel):
    probe_plan_points: List[ReplayRiskPointOut] = Field(default_factory=list)
    warning: str


class ReplayApprovalOut(BaseModel):
    id: int
    system_id: int
    component_id: str
    status: ReplayApprovalStatus
    reason: str = ""
    approved_by_user_id: Optional[int] = None
    decision_method: str = "manual"
    risk_context: Optional[Dict[str, Any]] = None
    created_at: float
    revoked_at: Optional[float] = None
    revoked_by_user_id: Optional[int] = None


class ReplayApprovalStateOut(BaseModel):
    component_id: str
    active: bool
    approval: Optional[ReplayApprovalOut] = None
    risk_context: ReplayRiskContextOut


class ReplaySetCreate(BaseModel):
    component_id: str = Field(..., min_length=1)
    name: str = ""
    trace_ids: Optional[List[str]] = None
    analyzer_run_id: Optional[int] = None


class ReplaySetTraceOut(BaseModel):
    """Per-trace replay preview: recorded replayability plus the input source
    a replay would deterministically use (same rule as the runner)."""

    trace_id: str
    exists: bool
    replayability: Optional[str] = None
    replay_reasons: List[str] = Field(default_factory=list)
    input_source: Optional[ReplayInputSource] = None
    skip_reason: Optional[ReplaySkipReason] = None


class ReplaySetOut(BaseModel):
    id: int
    system_id: int
    component_id: str
    name: str = ""
    source: ReplaySetSource
    source_analyzer_run_id: Optional[int] = None
    trace_ids: List[str] = Field(default_factory=list)
    traces: List[ReplaySetTraceOut] = Field(default_factory=list)
    created_at: float


class ReplayRunCreate(BaseModel):
    replay_set_id: int
    snapshot_id: Optional[int] = None
    stale_snapshot_reason: Optional[str] = Field(None, min_length=1, max_length=1000)
    flow_experiment_proposal_id: Optional[int] = None


class ReplayCaseResultOut(BaseModel):
    id: int
    trace_id: str
    position: int
    case_status: ReplayCaseStatus
    input_source: Optional[ReplayInputSource] = None
    skip_reason: Optional[ReplaySkipReason] = None
    replay_output: Optional[str] = None
    replay_error: Optional[str] = None
    recorded_output: Optional[str] = None
    recorded_error: Optional[str] = None
    duration_ms: Optional[float] = None
    output_truncated: bool = False
    comparison_mode: str = "repr"
    created_at: float


class ReplayRunOut(BaseModel):
    id: int
    system_id: int
    replay_set_id: int
    component_id: str
    snapshot_id: int
    commit_sha: str
    symbol_path: str
    symbol_qualified_name: str
    status: Literal["running", "completed", "failed"]
    error: Optional[str] = None
    trace_set_hash: str
    sandbox_config: Dict[str, Any] = Field(default_factory=dict)
    approval_id: Optional[int] = None
    workspace_path: Optional[str] = None
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None
    summary: Dict[str, int] = Field(default_factory=dict)
    cases: List[ReplayCaseResultOut] = Field(default_factory=list)
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    snapshot_freshness: Optional[SnapshotFreshnessState] = None
    head_sha_at_creation: Optional[str] = None
    stale_ack_reason: Optional[str] = None


# --- Replay variants (Issue #242 Phase C / #245) -----------------------------

# Finite classification sets (Principle 6). Kept in sync with
# app/replay_variants.py's module docstring and the replay_variant* table
# comments in app/db.py.
ReplayVariantCaseStatus = Literal[
    "match",
    "diff",
    "candidate_error",
    "error_to_success",
    "error_to_same_error",
    "error_to_different_error",
    "skipped",
]
ReplayVariantComparisonMode = Literal["structured", "repr"]
ReplayVariantSource = Literal["manual", "pasted", "llm_draft"]
ReplayVariantApplyStatus = Literal["applied", "invalid_patch", "not_applicable"]
ReplayVariantRunStatus = Literal["running", "completed", "failed"]
ReplayVariantDraftStatus = Literal["proposed", "failed"]


class ReplayVariantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=200)
    patch_text: str = Field(..., min_length=1, max_length=1_000_000)
    source: ReplayVariantSource = "manual"


class ReplayVariantRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replay_set_id: int
    snapshot_id: Optional[int] = None
    variants: List[ReplayVariantCreate] = Field(..., min_length=1, max_length=20)
    # Issue #369: required when the resolved snapshot is definitively behind
    # HEAD. Same manual decision the Experiment records.
    stale_snapshot_reason: Optional[str] = Field(None, min_length=1, max_length=1000)
    flow_experiment_proposal_id: Optional[int] = None


class ReplayVariantCaseResultOut(BaseModel):
    id: int
    trace_id: str
    position: int
    case_status: ReplayVariantCaseStatus
    comparison_mode: Optional[ReplayVariantComparisonMode] = None
    baseline_output: Optional[str] = None
    candidate_output: Optional[str] = None
    candidate_error: Optional[str] = None
    recorded_error: Optional[str] = None
    duration_ms: Optional[float] = None
    duration_delta_ms: Optional[float] = None
    field_diffs: List[str] = Field(default_factory=list)
    output_truncated: bool = False
    created_at: float


class ReplayVariantAggregateOut(BaseModel):
    match: int = 0
    diff: int = 0
    candidate_error: int = 0
    error_to_success: int = 0
    error_to_same_error: int = 0
    error_to_different_error: int = 0
    skipped: int = 0
    total: int = 0
    avg_duration_delta_ms: Optional[float] = None
    examples: Dict[str, List[str]] = Field(default_factory=dict)


class ReplayVariantOut(BaseModel):
    id: int
    replay_run_id: int
    variant_key: str
    label: str = ""
    is_baseline: bool
    patch_text: str = ""
    patch_hash: str
    source: str = "manual"
    apply_status: ReplayVariantApplyStatus = "not_applicable"
    apply_error: Optional[str] = None
    status: ReplayVariantRunStatus = "running"
    error: Optional[str] = None
    workspace_path: Optional[str] = None
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None
    aggregate: ReplayVariantAggregateOut = Field(default_factory=ReplayVariantAggregateOut)
    cases: List[ReplayVariantCaseResultOut] = Field(default_factory=list)
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class ReplayVariantRunOut(BaseModel):
    id: int
    system_id: int
    replay_set_id: int
    component_id: str
    snapshot_id: int
    commit_sha: str
    symbol_path: str
    symbol_qualified_name: str
    status: ReplayVariantRunStatus
    error: Optional[str] = None
    trace_set_hash: str
    sandbox_config: Dict[str, Any] = Field(default_factory=dict)
    approval_id: Optional[int] = None
    variants: List[ReplayVariantOut] = Field(default_factory=list)
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class ReplayVariantDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replay_set_id: int
    trace_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1, max_length=5000)
    snapshot_id: Optional[int] = None


class ReplayVariantDraftOut(BaseModel):
    id: int
    system_id: int
    replay_set_id: int
    component_id: str
    trace_id: str
    objective: str
    snapshot_id: int
    symbol_path: str
    symbol_qualified_name: str
    generated_code: str = ""
    patch_text: str = ""
    patch_hash: str = ""
    notes: str = ""
    status: ReplayVariantDraftStatus
    error: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    schema_version: Optional[str] = None
    decision_method: Optional[DecisionMethod] = None
    is_mock: bool = False
    created_at: float


class ReplayVariantExperimentPayloadOut(BaseModel):
    """Shapes a Replay variant's patch for POST /experiments prefill
    (Issue #245). API shape only -- this never creates an experiment;
    the caller copies this into an ExperimentVariantCreate."""

    label: str
    patch_text: str
    patch_hash: str
    source: str = "replay_variant"
    risk_note: str = ""
    origin: Dict[str, Any] = Field(default_factory=dict)


class ReplayRegressionScaffoldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replay_run_id: int
    replay_variant_id: int
    trace_id: str = Field(..., min_length=1)


class ReplayRegressionScaffoldOut(BaseModel):
    id: int
    intelligence_run_id: int
    replay_run_id: int
    replay_variant_id: int
    replay_set_id: int
    trace_id: str
    snapshot_id: int
    scaffold_text: str = ""
    status: Literal["proposed", "failed"]
    error: Optional[str] = None
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    decision_method: Literal["reasoning_llm"] = "reasoning_llm"
    is_mock: bool = False
    created_at: float


# --- Replay source & diff helpers (Issue #242 Phase D / #246) ----------------
#
# Two small DETERMINISTIC helpers backing the Simulation Workbench's "Direct
# edit" flow (Principle 6 -- no judgement here, just pinned-snapshot reads and
# structural text diffing reusing the same worktree+git-diff mechanism
# app/replay_draft.py already uses for LLM drafts).


class ReplaySourceOut(BaseModel):
    """Read-only pinned-snapshot source for the resolved Replay Set symbol's
    file. ``source`` is the full file content at the pinned commit -- never
    the working tree (Principle 5); start_line/end_line locate the resolved
    symbol within it so the UI can scroll to / highlight it."""

    replay_set_id: int
    component_id: str
    snapshot_id: int
    commit_sha: str
    path: str
    qualified_name: str
    start_line: int
    end_line: int
    source: str


class ReplaySourceDiffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replay_set_id: int
    snapshot_id: Optional[int] = None
    edited_source: str = Field(..., min_length=1, max_length=2_000_000)


class ReplaySourceDiffOut(BaseModel):
    """Deterministic unified diff between the pinned-snapshot file and a
    developer-edited copy of it. No judgement -- pure structural text
    diffing (the same worktree + ``git diff`` mechanism
    ``replay_draft._diff_against_snapshot`` uses for LLM drafts)."""

    patch_text: str
    patch_hash: str


# --- AI Candidate Studio (Issue #252) ----------------------------------------
#
# A conversation + versioning layer over the existing isolated-Replay stack.
# Finite sets (Principle 6): a version's generate lifecycle terminal status,
# its replay lifecycle status, and message roles.

CandidateVersionStatus = Literal["proposed", "failed"]
CandidateReplayStatus = Literal["not_run", "running", "completed", "failed"]
CandidateMessageRole = Literal["user", "assistant"]
CandidateSessionStatus = Literal["active", "archived"]


class CandidateSessionCreate(BaseModel):
    """Start a Studio session for a component. At most one input selection
    may be supplied: an existing ``replay_set_id``, an explicit ``trace_ids``
    list, or a single ``trace_id`` (the "improve from this input" entry). With
    no selection, the component entry point uses up to 50 recent traces. When
    trace ids are selected, a Replay Set is created for them (reusing POST
    /replay-sets' validation). ``snapshot_id`` defaults to the latest ready
    snapshot."""

    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(..., min_length=1, max_length=500)
    replay_set_id: Optional[int] = None
    trace_ids: Optional[List[str]] = None
    trace_id: Optional[str] = None
    snapshot_id: Optional[int] = None
    objective: str = Field(default="", max_length=5000)
    # Issue #369: required when the resolved snapshot is definitively behind
    # HEAD. Same manual decision the Experiment records.
    stale_snapshot_reason: Optional[str] = Field(None, min_length=1, max_length=1000)


class CandidateProposal(BaseModel):
    """Structured candidate proposal (never free-form code): the reasoning
    model returns summary / assumptions / changed_symbols / generated_code /
    risks / suggested_tests; the patch itself is produced deterministically by
    splicing ``generated_code`` into the resolved symbol span and diffing
    against the pinned snapshot (app/candidate_studio.py)."""

    summary: str = ""
    assumptions: List[str] = Field(default_factory=list)
    changed_symbols: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    suggested_tests: List[str] = Field(default_factory=list)


class CandidateVersionOut(BaseModel):
    id: int
    system_id: int
    session_id: int
    parent_version_id: Optional[int] = None
    version_number: int
    instruction: str = ""
    status: CandidateVersionStatus
    summary: str = ""
    assumptions: List[str] = Field(default_factory=list)
    changed_symbols: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    suggested_tests: List[str] = Field(default_factory=list)
    generated_code: str = ""
    patch_text: str = ""
    patch_hash: str = ""
    error: Optional[str] = None
    replay_status: CandidateReplayStatus = "not_run"
    replay_run_id: Optional[int] = None
    replay_variant_id: Optional[int] = None
    promoted_at: Optional[float] = None
    # Reasoning provenance (from the linked intelligence_runs row).
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    schema_version: Optional[str] = None
    decision_method: Optional[DecisionMethod] = None
    is_mock: bool = False
    created_at: float


class CandidateMessageOut(BaseModel):
    id: int
    session_id: int
    role: CandidateMessageRole
    content: str = ""
    version_id: Optional[int] = None
    created_at: float


class CandidateSessionOut(BaseModel):
    id: int
    system_id: int
    component_id: str
    snapshot_id: int
    commit_sha: str
    symbol_path: str
    symbol_qualified_name: str
    replay_set_id: int
    objective: str = ""
    status: CandidateSessionStatus = "active"
    created_at: float
    updated_at: float
    messages: List[CandidateMessageOut] = Field(default_factory=list)
    versions: List[CandidateVersionOut] = Field(default_factory=list)


class CandidateMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=10000)


class CandidateGenerateCreate(BaseModel):
    """Generate the next immutable CandidateVersion. ``instruction`` is the
    improvement goal / constraints; ``parent_version_id`` branches off a
    selected version (None = branch off the baseline)."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(..., min_length=1, max_length=5000)
    parent_version_id: Optional[int] = None


class CandidateReplayCreate(BaseModel):
    """Replay against the session's pinned snapshot.  ``snapshot_id`` remains
    accepted for compatibility, but a different value is rejected."""
    model_config = ConfigDict(extra="forbid")

    snapshot_id: Optional[int] = None
    flow_experiment_proposal_id: Optional[int] = None


class CandidatePromotionOut(BaseModel):
    """Hands a reviewed candidate patch to the existing Experiment creation
    flow (Issue #245's variant experiment-payload shape). API shape only --
    this never creates an experiment, auto-adopts, merges, or deploys
    anything (Principle 7)."""

    candidate_version_id: int
    label: str
    patch_text: str
    patch_hash: str
    source: str = "candidate_studio"
    risk_note: str = ""
    origin: Dict[str, Any] = Field(default_factory=dict)


class CandidateEventOut(BaseModel):
    """One entry of a session's job/status timeline (polling contract for the
    events endpoint). Derived deterministically from persisted version state:
    generate transitions (``context_preparing`` -> ``generating`` ->
    ``validating_patch`` -> ``completed`` / ``failed``) collapse to the
    version's terminal ``status``; replay transitions surface
    ``replay_status``."""

    version_id: int
    version_number: int
    phase: Literal[
        "generating", "validating_patch", "completed", "failed", "replaying"
    ]
    status: str
    replay_status: CandidateReplayStatus = "not_run"
    detail: str = ""
    created_at: float


class CandidateEventsOut(BaseModel):
    session_id: int
    events: List[CandidateEventOut] = Field(default_factory=list)


# --- Probe Cell Fabric (Issue #297), Sub 1: Cell contract / Role Card /
# common state schema (Issue #298). Request bodies reuse the shared-schema
# mirror models in app/cell_fabric.py directly (AgentRoleCard,
# CellDefinitionContract) so FastAPI's own request validation enforces the
# fail-closed unknown-field / enum / schema_version rules; these are only the
# server-assigned "Out" projections (id, system_id, timestamps, audit
# fields). See docs/project-intelligence.md's "Probe Cell Fabric(Issue
# #297)" section.


class AgentRoleCardOut(BaseModel):
    id: int
    system_id: int
    role_key: str
    version: str
    status: str
    mission: str
    scope: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    model_alias: str
    tool_policy: Dict[str, Any] = Field(default_factory=dict)
    acceptance_template: List[str] = Field(default_factory=list)
    rubric_ref: Optional[str] = None
    changelog: str
    schema_version: str
    decision_method: str
    created_by: Optional[str] = None
    created_at: float


class AgentRoleCardsListOut(BaseModel):
    system_id: int
    role_cards: List[AgentRoleCardOut] = Field(default_factory=list)


class CellDefinitionOut(BaseModel):
    id: int
    system_id: int
    cell_id: str
    roster: Optional[List[str]] = None
    role_card_ref: Dict[str, str]
    status: str
    mission: str
    created_at: float
    updated_at: float


class CellsListOut(BaseModel):
    system_id: int
    cells: List[CellDefinitionOut] = Field(default_factory=list)


class CellDetailOut(BaseModel):
    definition: CellDefinitionOut
    state: Dict[str, Any]


# ---------------------------------------------------------------------------
# Cell Binding / Activation (Issue #299, Sub 2 of the Probe Cell Fabric epic)
# ---------------------------------------------------------------------------


class CellBindingCreateIn(BaseModel):
    """Exactly one of ``probe_point_id`` / ``probe_pattern_point_id`` must be
    given; provenance is read from that row server-side, never accepted from
    the caller."""

    probe_point_id: Optional[int] = None
    probe_pattern_point_id: Optional[int] = None
    feature_refs: List[str] = Field(default_factory=list)
    capability_refs: List[str] = Field(default_factory=list)
    entrypoint_refs: List[str] = Field(default_factory=list)


class CellBindingOut(BaseModel):
    id: int
    system_id: int
    cell_definition_id: int
    cell_id: str
    version: int
    snapshot_id: int
    commit_sha: str
    path: str
    qualified_symbol: str
    component_id: str
    probe_point_id: Optional[int] = None
    probe_pattern_id: Optional[int] = None
    feature_refs: List[str] = Field(default_factory=list)
    capability_refs: List[str] = Field(default_factory=list)
    entrypoint_refs: List[str] = Field(default_factory=list)
    status: str
    status_reason: str
    created_at: float


class CellBindingsListOut(BaseModel):
    system_id: int
    cell_id: str
    bindings: List[CellBindingOut] = Field(default_factory=list)


class CellActivationCreateIn(BaseModel):
    window_start: Optional[float] = None
    window_end: Optional[float] = None
    requested_by: Optional[str] = None


class CellActivationOut(BaseModel):
    id: int
    system_id: int
    cell_definition_id: int
    cell_id: str
    trigger_kind: str
    window_start: Optional[float] = None
    window_end: Optional[float] = None
    requested_by: Optional[str] = None
    used_llm: bool
    intelligence_run_id: Optional[int] = None
    status: str
    detail: str
    created_at: float
    completed_at: Optional[float] = None


class CellActivationsListOut(BaseModel):
    system_id: int
    cell_id: str
    activations: List[CellActivationOut] = Field(default_factory=list)


class CellStateOut(BaseModel):
    """The read-only pilot state document: Sub 1's ``cell_state`` (with the
    health block filled in) plus the current binding and recent activations
    as sibling fields -- the ``cell_state`` schema itself is unchanged."""

    cell_id: str
    state: Dict[str, Any]
    binding: Optional[CellBindingOut] = None
    recent_activations: List[CellActivationOut] = Field(default_factory=list)
# Goal/Task ledger + delegate/report/escalate protocol (Issue #300, Sub 3 of
# the Probe Cell Fabric epic, Issue #297). Core logic lives in
# app/cell_tasks.py; these are the request/response shapes for
# routes/cell_tasks.py.
# ---------------------------------------------------------------------------


class CellGoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1)
    description: str = ""
    parent_goal_id: Optional[int] = None
    owner_cell_id: Optional[str] = None


class CellGoalStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class CellGoalOut(BaseModel):
    id: int
    system_id: int
    parent_goal_id: Optional[int] = None
    title: str
    description: str = ""
    owner_cell_id: Optional[str] = None
    status: str
    created_at: float
    updated_at: float


class CellGoalsListOut(BaseModel):
    system_id: int
    goals: List[CellGoalOut] = Field(default_factory=list)


class CellTaskDelegateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: int
    owner_cell_id: str = Field(..., min_length=1)
    delegated_by_cell_id: Optional[str] = None
    title: str = Field(..., min_length=1)
    acceptance: List[str] = Field(..., min_length=1)
    context_refs: List[str] = Field(default_factory=list)
    budget: Optional[Dict[str, Any]] = None
    deadline: Optional[str] = None
    priority: Optional[str] = None
    idempotency_key: Optional[str] = None


class CellTaskOut(BaseModel):
    id: int
    system_id: int
    goal_id: int
    owner_cell_id: str
    delegated_by_cell_id: Optional[str] = None
    title: str
    acceptance: List[str] = Field(default_factory=list)
    context_refs: List[str] = Field(default_factory=list)
    budget: Optional[Dict[str, Any]] = None
    deadline: Optional[str] = None
    priority: str
    status: str
    retry_count: int
    retry_limit: int
    blocked_by: List[int] = Field(default_factory=list)
    acceptance_met: bool
    evidence: List[str] = Field(default_factory=list)
    returned_to_parent: bool
    idempotency_key: Optional[str] = None
    created_at: float
    updated_at: float


class CellTasksListOut(BaseModel):
    system_id: int
    tasks: List[CellTaskOut] = Field(default_factory=list)


class CellGoalDetailOut(BaseModel):
    goal: CellGoalOut
    tasks: List[CellTaskOut] = Field(default_factory=list)


class CellTaskEventOut(BaseModel):
    id: int
    task_id: int
    event_type: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    detail: str = ""
    created_at: float


class CellTaskDetailOut(BaseModel):
    task: CellTaskOut
    events: List[CellTaskEventOut] = Field(default_factory=list)


class CellTaskTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_status: str
    acceptance_met: Optional[bool] = None
    evidence_refs: Optional[List[str]] = None
    blocked_by: Optional[List[int]] = None
    detail: str = ""


class CellTaskReturnToParentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str = ""


class CellReportFactItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)
    evidence_refs: List[str] = Field(default_factory=list)


class CellReportTextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)


class CellReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cell_definition_id: str = Field(..., min_length=1)
    task_id: Optional[int] = None
    kind: str
    severity: Optional[str] = None
    fact: List[CellReportFactItem] = Field(default_factory=list)
    interpretation: List[CellReportTextItem] = Field(default_factory=list)
    ask: List[CellReportTextItem] = Field(default_factory=list)
    idempotency_key: Optional[str] = None


class CellReportOut(BaseModel):
    id: int
    system_id: int
    cell_definition_id: str
    task_id: Optional[int] = None
    kind: str
    severity: Optional[str] = None
    fact: List[Dict[str, Any]] = Field(default_factory=list)
    interpretation: List[Dict[str, Any]] = Field(default_factory=list)
    ask: List[Dict[str, Any]] = Field(default_factory=list)
    idempotency_key: Optional[str] = None
    created_at: float
    escalation_id: Optional[int] = None


class CellReportsListOut(BaseModel):
    system_id: int
    reports: List[CellReportOut] = Field(default_factory=list)


class CellEscalationOut(BaseModel):
    id: int
    system_id: int
    report_id: int
    cell_definition_id: str
    severity: str
    status: str
    summary: str
    created_at: float
    updated_at: float


class CellEscalationsListOut(BaseModel):
    system_id: int
    escalations: List[CellEscalationOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 領域オーケストレーター / domain orchestrators (Issue #301, Sub 4 of the
# Probe Cell Fabric epic, Issue #297). Core logic lives in
# app/cell_orchestrator.py; these are the request/response shapes for
# routes/cell_orchestrators.py.
# ---------------------------------------------------------------------------


class CellOrchestratorDigestOut(BaseModel):
    """The deterministic digest -- ``digest`` is returned as a plain dict
    (matching the existing ``CellStateOut.state`` / ``CellDetailOut.state``
    pattern) rather than a rigid nested model, since its shape mirrors
    ``app.cell_orchestrator.build_orchestrator_digest``'s return value."""

    system_id: int
    cell_id: str
    digest: Dict[str, Any]


class CellTriageResultOut(BaseModel):
    id: int
    system_id: int
    intelligence_run_id: int
    classification: str
    reasoning_summary: str
    affected_cell_ids: List[str] = Field(default_factory=list)
    proposed_ask: str = ""
    is_mock: bool = False
    created_at: float


class CellTriageResultsListOut(BaseModel):
    system_id: int
    cell_id: str
    results: List[CellTriageResultOut] = Field(default_factory=list)


class CellTriageRunOut(BaseModel):
    """Response of ``POST /cell-fabric/orchestrators/{cell_id}/triage``. The
    digest is always present (facts survive triage failure); ``triage`` is
    ``None`` and ``triage_error`` is set on ANY triage failure -- there is no
    heuristic classification fallback."""

    system_id: int
    cell_id: str
    digest: Dict[str, Any]
    triage: Optional[CellTriageResultOut] = None
    triage_error: Optional[str] = None
    is_mock: bool = False
    intelligence_run_id: int


class CellRosterUpdateIn(BaseModel):
    """``roster`` is always a list here (possibly empty): this endpoint only
    ever sets/changes an orchestrator's roster, it never converts a Cell
    back into a roster-less worker."""

    model_config = ConfigDict(extra="forbid")

    roster: List[str] = Field(default_factory=list)
    changed_by: Optional[str] = None


class CellRosterEventOut(BaseModel):
    id: int
    system_id: int
    cell_definition_id: int
    old_roster: Optional[List[str]] = None
    new_roster: List[str] = Field(default_factory=list)
    changed_by: Optional[str] = None
    created_at: float


# ---------------------------------------------------------------------------
# 品質サンプリング・独立監査・quality floor (Issue #302, Sub 6 of the Probe
# Cell Fabric epic, Issue #297). Core logic lives in app/cell_quality.py;
# these are the request/response shapes for routes/cell_quality.py.
# ---------------------------------------------------------------------------


class CellQualityStratumIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    task_type: Optional[str] = None
    risk: Optional[str] = None
    rare: bool = False


class CellQualityConfigIn(BaseModel):
    """Every field optional: an omitted field keeps its existing persisted
    value (or the module default on first create)."""

    model_config = ConfigDict(extra="forbid")

    sample_rate: Optional[float] = None
    strata: Optional[List[CellQualityStratumIn]] = None
    audit_rate: Optional[float] = None
    quality_floor: Optional[float] = None
    floor_window: Optional[int] = None
    daily_audit_budget: Optional[int] = None


class CellQualityConfigOut(BaseModel):
    system_id: int
    cell_definition_id: int
    sample_rate: float
    strata: List[Dict[str, Any]] = Field(default_factory=list)
    audit_rate: float
    quality_floor: float
    floor_window: int
    daily_audit_budget: int
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


class CellQualitySampleSelectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_seconds: Optional[float] = None


class CellQualitySampleOut(BaseModel):
    id: int
    system_id: int
    cell_definition_id: int
    config_id: int
    stratum: str = ""
    target_kind: str
    target_id: str
    selection_seed: str
    selected_at: float


class CellQualitySamplesListOut(BaseModel):
    system_id: int
    cell_id: str
    samples: List[CellQualitySampleOut] = Field(default_factory=list)


class CellQualityAuditRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blind: bool = False
    auditor_alias: Optional[str] = None


class CellQualityAuditOut(BaseModel):
    id: int
    system_id: int
    sample_id: int
    auditor_model_alias: str
    verdict: str
    verdict_decision_method: str
    is_blind: bool
    failed_criteria: List[int] = Field(default_factory=list)
    verbatim_example: str = ""
    explanation: str = ""
    explanation_run_id: Optional[int] = None
    is_mock: bool = False
    created_at: float


class CellQualityAuditsListOut(BaseModel):
    system_id: int
    cell_id: str
    counts: Dict[str, int] = Field(default_factory=dict)
    audits: List[CellQualityAuditOut] = Field(default_factory=list)


class CellQualityFloorEvaluateOut(BaseModel):
    system_id: int
    cell_id: str
    pass_rate: Optional[float] = None
    floor: float
    denominator: int
    suspended: bool
    escalation_id: Optional[int] = None


class CellIntakeResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


class CellIntakeStateOut(BaseModel):
    system_id: int
    cell_id: str
    intake_status: str
    reason: str = ""
    escalation_id: Optional[int] = None
    changed_at: Optional[float] = None
# Root Orchestrator と統合ダイジェスト (Issue #303, Sub 5 of the Probe Cell
# Fabric epic, Issue #297). Core logic lives in app/cell_root.py; these are
# the request/response shapes for routes/cell_root.py.
# ---------------------------------------------------------------------------


class CellRootDigestOut(BaseModel):
    """The 4-level progressive-disclosure digest -- ``digest`` is returned as
    a plain dict (same pattern as ``CellOrchestratorDigestOut.digest``) since
    its shape mirrors ``app.cell_root.build_root_digest``'s return value
    verbatim."""

    system_id: int
    digest: Dict[str, Any]
    generated_at: float


class CellAskSyncOut(BaseModel):
    system_id: int
    created: int
    deduped: int


class CellAskOut(BaseModel):
    id: int
    system_id: int
    source_kind: str
    source_id: int
    cell_definition_id: Optional[str] = None
    goal_id: Optional[int] = None
    task_id: Optional[int] = None
    ask_text: str
    severity: str
    status: str
    decision: str = ""
    decision_note: str = ""
    decision_method: str = ""
    decided_by: Optional[str] = None
    decided_at: Optional[float] = None
    execution_approved: bool = False
    dedupe_key: str
    created_at: float


class CellAsksListOut(BaseModel):
    system_id: int
    asks: List[CellAskOut] = Field(default_factory=list)


class CellAskDecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    note: str = ""


# ---------------------------------------------------------------------------
# 改善仮説・カナリア・shadow実行承認ゲート (Issue #304, Sub 7 of the Probe
# Cell Fabric epic, Issue #297). Core logic lives in app/cell_improvement.py;
# these are the request/response shapes for routes/cell_improvement.py.
# ---------------------------------------------------------------------------


class CellImprovementDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_facts_refs: List[str] = Field(default_factory=list)
    target_kind: str = "role_card"
    parent_cell_id: Optional[str] = None


class CellImprovementCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_kind: str
    hypothesis: str = ""
    expected_effect: str = ""
    risk: str = ""
    rollback_plan: str = ""
    observed_facts_refs: List[str] = Field(default_factory=list)
    parent_cell_id: Optional[str] = None


class CellImprovementOut(BaseModel):
    id: int
    system_id: int
    cell_id: str
    status: str
    target_kind: str
    hypothesis: str = ""
    expected_effect: str = ""
    risk: str = ""
    rollback_plan: str = ""
    observed_facts_refs: List[str] = Field(default_factory=list)
    proposal_run_id: Optional[int] = None
    is_mock: bool = False
    role_card_id: Optional[int] = None
    proposed_role_card_version: Optional[str] = None
    canary_evidence_refs: List[str] = Field(default_factory=list)
    parent_cell_id: Optional[str] = None
    parent_approved_by: Optional[str] = None
    parent_approved_at: Optional[float] = None
    human_approved_by: Optional[str] = None
    human_approved_at: Optional[float] = None
    suspended: bool = False
    suspension_reason: str = ""
    created_at: float
    updated_at: float


class CellImprovementsListOut(BaseModel):
    system_id: int
    cell_id: str
    improvements: List[CellImprovementOut] = Field(default_factory=list)


class CellImprovementEventOut(BaseModel):
    id: int
    system_id: int
    improvement_id: int
    event_type: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    actor: Optional[str] = None
    detail: str = ""
    created_at: float


class CellShadowDecisionOut(BaseModel):
    id: int
    system_id: int
    improvement_id: int
    kind: str
    status: str
    decided_by: Optional[str] = None
    decided_at: Optional[float] = None
    decision_method: str = ""
    note: str = ""
    created_at: float


class CellImprovementDetailOut(BaseModel):
    improvement: CellImprovementOut
    events: List[CellImprovementEventOut] = Field(default_factory=list)
    shadow_decisions: List[CellShadowDecisionOut] = Field(default_factory=list)


class CellImprovementDraftOut(BaseModel):
    """Response of ``POST /cell-fabric/cells/{cell_id}/improvements/draft``.
    ``improvement`` is ``None`` and ``draft_error`` is set on ANY LLM/JSON
    failure -- there is no heuristic hypothesis fallback."""

    system_id: int
    cell_id: str
    improvement: Optional[CellImprovementOut] = None
    draft_error: Optional[str] = None
    is_mock: bool = False
    intelligence_run_id: int


class CellImprovementTransitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_status: str
    canary_evidence_refs: Optional[List[str]] = None
    proposed_role_card_version: Optional[str] = None
    allow_major_bump: bool = False
    detail: str = ""
    actor: Optional[str] = None


class CellApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str


class CellImprovementSuspendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


class CellImprovementRollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: Optional[str] = None


class CellShadowProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""
    actor: Optional[str] = None


class CellShadowRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""
    actor: Optional[str] = None


class CellShadowDecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    decided_by: str
    note: str = ""


# --- State-driven System Interview workflow (Issue #349) ---------------------
#
# Response/request contracts for docs/system-interview-workflow-ux.md. Every
# field is either a persisted fact or a value the canonical engine
# (app/interview_workflow.py) derived from persisted facts -- the Dashboard
# never re-derives a workflow state of its own (spec principle P9).


class InterviewWorkflowFactsOut(BaseModel):
    """The exact inputs of the 13-row first-match rule table, exposed so a
    displayed state is explainable and testable without re-querying."""

    has_snapshot: bool
    has_session: bool
    session_closed: bool
    running_process_kinds: List[str] = []
    blocking_failure_states: List[str] = []
    understanding_unconfirmed: bool
    open_required_questions: int
    outstanding_alignment_items: int
    proposals_needing_review: int
    proposals_generatable: bool
    approved_proposal_count: int
    diff_matches_approval_set: bool
    diff_review_complete: bool
    pending_handoff_count: int


class InterviewProcessRunOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    process_kind: str
    status: str
    failure_class: Optional[str] = None
    target_state: Optional[str] = None
    error: Optional[str] = None
    started_at: float
    finished_at: Optional[float] = None


class InterviewBackRequestOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    cause_kind: str
    candidate_state: str
    reached_state: str
    status: str
    created_at: float


class InterviewWorkflowExceptionOut(BaseModel):
    """One currently-active exception (spec §5.2).

    `severity` is the spec's 3-way split. Only `blocking` and `degraded` are
    the `R5` role; `informational` exceptions are branches of the primary
    work card and must never be rendered as a warning band.
    """

    code: str
    severity: str
    target_state: Optional[str] = None
    message: str
    detail: Optional[str] = None
    recovery_process_kind: Optional[str] = None
    recovery_condition: Optional[str] = None


class InterviewWorkflowStateOut(BaseModel):
    system_id: int
    session_id: Optional[int] = None
    state: str
    candidate_state: str
    rule_row: int
    reached_state: Optional[str] = None
    backward_hold: bool = False
    pending_back_request: Optional[InterviewBackRequestOut] = None
    terminal_kind: Optional[str] = None
    primary_action: str
    facts: InterviewWorkflowFactsOut
    running_processes: List[InterviewProcessRunOut] = []
    unresolved_failures: List[InterviewProcessRunOut] = []
    exceptions: List[InterviewWorkflowExceptionOut] = []
    diff_materialized_at: Optional[float] = None
    latest_ready_snapshot_id: Optional[int] = None


class InterviewDiffReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewed_by: str = ""
    note: str = ""


class InterviewDiffReviewOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    diff_materialized_at: float
    diff_digest: str
    reviewed_by: str
    decision_method: str
    note: str
    created_at: float


class InterviewBackAcknowledgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = ""


class InterviewSessionCloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_kind: str = "suspended"
    reason: str = ""
    actor: str = ""


class InterviewSessionReopenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""
    actor: str = ""


class InterviewSessionStatusAuditOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    action: str
    terminal_kind: Optional[str] = None
    reason: str
    actor: str
    decision_method: str
    created_at: float


# --- Understanding Brief / Decision Readiness (Issues #351-#354) -------------
#
# Every field here is a deterministic derivation persisted facts already
# support (app/understanding_brief.py). `confirmation` and `provenance` are
# two independent finite axes on purpose: "the developer confirmed it" and
# "the developer wrote it" must stay distinguishable.
#
# The vocabularies live here as `Literal` aliases, not as bare `str` fields,
# for two reasons: the response schema then carries the enum, and
# `tests/test_interview_type_parity.py` can hold the Dashboard union to the
# same set. `app/understanding_brief.py` derives its tuples from these with
# `get_args`, so there is exactly one definition of each set.

#: 確認状態 -- how settled a claim is.
UnderstandingConfirmationState = Literal[
    "confirmed",
    "ai_hypothesis",
    "conflicting",
    "unknown",
    "recheck_required",
]

#: 出所 -- where the claim's content came from. Independent of the above.
UnderstandingProvenanceKind = Literal[
    "developer_intent",
    "implementation_fact",
    "runtime_observation",
    "ai_hypothesis",
]

UnderstandingClaimKind = Literal["vision", "system_purpose", "core_capability"]

UnderstandingReadinessState = Literal[
    "not_built",
    "building",
    "needs_confirmation",
    "ready",
    "recheck_required",
    "blocked",
]

UnderstandingReadinessSeverity = Literal["blocking", "attention", "informational"]

#: Change kinds reported against the confirmed revision. The first four come
#: from `understanding_diff`; the rest are the remaining `claim_payload`
#: fields. Every field that can decide a recheck must appear here, or a claim
#: becomes reportable as "changed" with nothing to show.
UnderstandingChangeKind = Literal[
    "added",
    "removed",
    "summary_changed",
    "confidence_changed",
    "contribution_changed",
    "evidence_changed",
    "related_docs_changed",
    "related_apis_changed",
    "composition_changed",
]


class UnderstandingBriefClaimOut(BaseModel):
    kind: UnderstandingClaimKind
    name: str
    summary: str = ""
    confirmation: UnderstandingConfirmationState
    provenance: UnderstandingProvenanceKind
    confirmation_label: str
    provenance_label: str
    reason: str = ""
    contribution: str = ""
    #: Mock LLM provenance, labelled rather than hidden (CLAUDE.md).
    is_mock: bool = False
    evidence: List[Dict[str, Any]] = []
    related_docs: List[str] = []
    related_apis: List[str] = []


class UnderstandingReadinessReasonOut(BaseModel):
    code: str
    severity: UnderstandingReadinessSeverity
    message: str
    target_kind: str = "none"
    target_name: str = ""


class UnderstandingChangeOut(BaseModel):
    change_kind: UnderstandingChangeKind
    section: str
    section_label: str
    name: str
    detail: str


class UnderstandingBriefOut(BaseModel):
    system_id: int
    session_id: Optional[int] = None
    built: bool = False
    vision: Optional[UnderstandingBriefClaimOut] = None
    vision_missing_information: List[str] = []
    system_purpose: List[UnderstandingBriefClaimOut] = []
    core_capabilities: List[UnderstandingBriefClaimOut] = []
    #: How many Core Capabilities the initial view shows; the rest belong
    #: behind progressive disclosure. A cap, never a pad.
    core_capability_initial_count: int = 0
    key_unconfirmed: List[UnderstandingBriefClaimOut] = []
    detail_counts: Dict[str, int] = {}
    readiness_state: UnderstandingReadinessState
    readiness_label: str
    readiness_description: str
    readiness_reasons: List[UnderstandingReadinessReasonOut] = []
    changes_since_confirmation: List[UnderstandingChangeOut] = []
    confirmed_at: Optional[float] = None
    confirmed_revision_id: Optional[int] = None
    revision_id: Optional[int] = None
    snapshot_id: Optional[int] = None


# --- Overview / System Intelligence Brief (Issues #380-#384) -----------------
#
# The Overview screen's canonical projection. Every vocabulary below is a
# finite `Literal` for the same two reasons the Understanding Brief's are
# (#351): the response schema then carries the enum, and
# `tests/test_interview_type_parity.py` holds the Dashboard union to the same
# set. `app/overview_projection.py` derives its tuples from these with
# `get_args`, so each set has exactly one definition.
#
# The Overview adds no new understanding model: its Brief section IS
# `UnderstandingBriefOut` (#351-#354), its loop position IS `derive_user_phase`
# (#237/#256), and its runtime numbers ARE the connectivity facts (#370).

#: What kind of thing a 「今わかったこと」 finding is. Finite by construction:
#: every kind is produced by exactly one deterministic extractor over
#: persisted rows, never by a model scoring what matters.
OverviewFindingKind = Literal[
    "claim_conflict",
    "understanding_blocked",
    "understanding_changed",
    "capability_composition_stale",
    "unconfirmed_core_claim",
    "runtime_mismatch",
    "runtime_unobserved",
    "connectivity_lost",
    "snapshot_stale",
    "evaluation_decision_pending",
    "improvement_candidate_ready",
]

#: How the finding bears on the developer's decision. This is the ranking
#: gate — a finite ladder, not a score.
OverviewFindingSeverity = Literal[
    "blocker",
    "human_decision_required",
    "material_change",
    "informative",
]

#: Whether the finding appeared AFTER the comparison baseline. `not_compared`
#: is a first-class value: 「新しい発見がない」 and 「まだ比較していない」 are
#: different statements and must never render the same (#382).
OverviewFindingStatus = Literal["new", "ongoing", "not_compared"]

#: Where the finding's content came from.
#:
#: The first four values are `UnderstandingProvenanceKind` VERBATIM, so a
#: claim's provenance carries into a finding about it without translation.
#: The first version of this set omitted `developer_intent`, and the mapping
#: collapsed it to `ai_hypothesis` — which displayed a Vision the developer
#: had written and confirmed as the AI's guess. That is the exact confusion
#: #381/#382 exist to prevent, so the two vocabularies now overlap by
#: construction rather than by a lossy map.
#:
#: The last three are finding-only, and each says something no claim can:
#:
#: * `developer_decision` — a record of the human's JUDGEMENT (adopt, confirm,
#:   defer), as opposed to `developer_intent` which is what they said they
#:   wanted. Deciding "reject this candidate" is not a statement of intent.
#: * `system_process` — a fact about probe-agent's own run records, not about
#:   the target system at all.
#: * `mixed` — an aggregated finding whose sources genuinely disagree. It
#:   exists so aggregation never has to pick one and imply the others agreed.
OverviewFindingProvenance = Literal[
    "developer_intent",
    "implementation_fact",
    "runtime_observation",
    "ai_hypothesis",
    "developer_decision",
    "system_process",
    "mixed",
]

#: The findings section as a whole. `unavailable` means the extraction itself
#: failed — never rendered as 「発見なし」.
OverviewFindingsState = Literal[
    "has_findings",
    "no_findings",
    "not_compared",
    "unavailable",
]

#: Whether the 「前回」 baseline could be read at all, kept apart from whether
#: one EXISTS. `unavailable` used to be indistinguishable from
#: `no_baseline`, so a Brief that failed to load rendered the confident
#: sentence 「まだ理解を確認していないため、比較の基準がありません」 about a
#: System whose developer may well have confirmed it yesterday.
OverviewBaselineState = Literal["has_baseline", "no_baseline", "unavailable"]

#: The single primary action. `create_system` is reachable only through the
#: Dashboard's zero-System branch (the endpoint is System-scoped, so it cannot
#: be evaluated server-side); it lives in this one vocabulary anyway so the
#: screen never grows a second action taxonomy.
OverviewActionKey = Literal[
    "create_system",
    "prepare_repository",
    "build_understanding",
    "resolve_understanding_blocker",
    "answer_interview_questions",
    "confirm_understanding",
    "connect_sdk",
    "start_observation",
    "restore_observation",
    "record_experiment_decision",
    # These are deliberately distinct identities: adopted experiment
    # artifacts versus probe-plan instrumentation patches.
    "publish_improvement",
    "publish_instrumentation",
    "create_candidate",
    "start_next_cycle",
]

#: Why there is (or is not) an action. `waiting` / `unavailable` carry no
#: action at all — a disabled catalogue of operations is explicitly forbidden
#: (#383).
OverviewActionState = Literal["available", "waiting", "complete", "unavailable"]

#: Whether the pinned understanding is reading the repository's current head.
#: Server-decided: the Dashboard must not compare snapshot ids itself, for the
#: same reason #369 moved the Snapshot verdict server-side — two definitions of
#: "current" drift. `unavailable` is a third value on purpose: an unreadable
#: head is not evidence that the snapshot is behind.
OverviewSnapshotFreshness = Literal["current", "stale", "unavailable"]

#: Whether Capability-level observation coverage could be computed at all.
#: `not_computed` is the honest value today: Trace rows carry a
#: `component_id`, and no canonical component -> Core Capability mapping is
#: persisted, so a "coverage" built from the two would be a ratio between
#: different entities (and could exceed 100%). See `OverviewRuntimeHealthOut`.
OverviewCoverageState = Literal["computed", "not_computed"]

#: The improvement loop's six stages, in order. These are the existing
#: `system_state.PHASE_ORDER` values under their developer-facing names — the
#: Overview never introduces a second phase model.
OverviewLoopStage = Literal[
    "setup",
    "preparation",
    "instrumentation",
    "observation",
    "evaluation",
    "publish",
]

OverviewLoopStageStatus = Literal["reached", "current", "future"]

#: Which composed section could not be built. A partial failure degrades one
#: section; it never blanks the screen (#384). `purpose_question` (#390/#391)
#: is its OWN section, separate from `purpose_chain`: a question failure
#: must not read as the whole Purpose Frame failing, and vice versa.
OverviewSection = Literal[
    "brief", "findings", "next_action", "loop", "runtime", "purpose_chain", "purpose_question"
]


class OverviewTargetOut(BaseModel):
    """Where a finding or an action sends the developer.

    `params` carries the destination's OWN parameter names (the #371 rule),
    so a link never navigates and then arrives with nothing selected.
    """

    route: str
    label: str
    params: Dict[str, str] = {}
    anchor: Optional[str] = None


class OverviewFindingOut(BaseModel):
    #: Deterministic and stable across reloads: derived from the kind plus the
    #: subject it is about, never from a row id that a rebuild would renumber.
    id: str
    kind: OverviewFindingKind
    kind_label: str
    severity: OverviewFindingSeverity
    severity_label: str
    status: OverviewFindingStatus
    status_label: str
    #: 短い結論 -- what was found.
    summary: str
    #: なぜ判断に重要か -- what it changes for the developer.
    decision_impact: str
    provenance: OverviewFindingProvenance
    provenance_label: str
    #: Which facts this finding was read against.
    snapshot_id: Optional[int] = None
    revision_id: Optional[int] = None
    runtime_window_seconds: Optional[float] = None
    first_seen: Optional[float] = None
    last_updated: Optional[float] = None
    target: Optional[OverviewTargetOut] = None
    evidence: List[Dict[str, Any]] = []
    #: How many same-cause findings this one represents (>= 1).
    occurrence_count: int = 1


class OverviewActionOut(BaseModel):
    key: OverviewActionKey
    label: str
    #: 選定理由 -- which fact put this action first.
    reason: str
    #: 完了条件 -- how the developer knows it is done.
    completion_condition: str
    #: 完了後に得られる価値 / 次に開く段階.
    value: str
    target: OverviewTargetOut
    #: The rule-table row that produced it, for auditing the first match.
    rule_row: int
    #: Facts and findings this action was derived from.
    source_state_ids: List[str] = []
    source_finding_ids: List[str] = []
    #: What is still missing, when the action can be started but not finished
    #: without it. Never used to render a disabled control.
    blockers: List[str] = []


class OverviewLoopStageOut(BaseModel):
    stage: OverviewLoopStage
    label: str
    status: OverviewLoopStageStatus
    #: What reaching this stage means, in the developer's terms.
    meaning: str
    #: The next semantic milestone; only set on the current stage.
    next_milestone: str = ""
    complete: bool = False


class OverviewRuntimeHealthOut(BaseModel):
    #: Cumulative lifecycle milestone (#370). Never regresses.
    state: ConnectivityState
    #: The live workload reading (#370). Regresses when traffic stops.
    freshness: ConnectivityFreshness
    freshness_label: str
    #: The any-kind transport axis, shown only when it disagrees.
    transport_freshness: ConnectivityFreshness
    last_real_trace_at: Optional[float] = None
    seconds_since_last_trace: Optional[float] = None
    last_trace_at: Optional[float] = None
    seconds_since_last_any_trace: Optional[float] = None
    evaluated_at: float
    real_trace_count_5m: int = 0
    real_trace_count_1h: int = 0
    real_trace_count_24h: int = 0
    delayed_after_seconds: float
    stale_after_seconds: float
    component_count: int = 0
    #: Cumulative totals, deliberately kept out of the first view.
    total_trace_count: int = 0
    mode_counts: Dict[str, int] = {}
    #: Bounded-window quality facts. `window_seconds` states the window they
    #: were measured over, so a count is never read as a lifetime total.
    window_seconds: float = 0
    error_count: int = 0
    #: Claims whose persisted #290 `runtime_check` is `mismatch`. Read as
    #: stored -- the Overview never re-runs a Runtime Reality Check and never
    #: invents a second definition of "the observation disagrees".
    runtime_mismatch_count: int = 0
    replayable_count: int = 0
    partial_count: int = 0
    unreplayable_count: int = 0
    not_captured_count: int = 0
    #: DISTINCT components that produced a trace in the window. Named for
    #: what it counts. It was previously called `observed_capability_count`
    #: and rendered next to `core_capability_count` as if the two formed a
    #: coverage ratio — they are different entities, and the numerator could
    #: exceed the denominator whenever one Capability had several components.
    observed_component_count: int = 0
    #: All components known to this System, so the count above has a
    #: denominator of its OWN entity.
    known_component_count: int = 0
    core_capability_count: int = 0
    #: Capability-level coverage is not computed today (no persisted
    #: component -> Capability mapping). Stated rather than approximated.
    capability_coverage_state: OverviewCoverageState = "not_computed"
    observed_capability_count: Optional[int] = None
    unmapped_component_count: Optional[int] = None


class OverviewOut(BaseModel):
    system_id: int
    generated_at: float
    #: The Interview session the Brief was read from (the System's newest), or
    #: None when no session exists yet.
    interview_session_id: Optional[int] = None
    #: The canonical Understanding Brief (#351-#354), reused verbatim.
    brief: Optional[UnderstandingBriefOut] = None
    snapshot_id: Optional[int] = None
    snapshot_commit_sha: Optional[str] = None
    #: The System's newest ready snapshot, for 「この理解はどの断面か」.
    latest_ready_snapshot_id: Optional[int] = None
    #: Server-decided (never a client id comparison). See
    #: `OverviewSnapshotFreshness`.
    snapshot_freshness: OverviewSnapshotFreshness = "unavailable"
    #: The Understanding revision the Brief was built from, and when the
    #: developer last confirmed one -- both part of the first-view context, so
    #: 「どの断面のどの版の理解か」 is answerable without opening a disclosure.
    understanding_revision_id: Optional[int] = None
    understanding_confirmed_at: Optional[float] = None
    findings: List[OverviewFindingOut] = []
    #: How many findings the initial view shows (cap 3, never a pad).
    findings_initial_count: int = 0
    findings_state: OverviewFindingsState
    #: What 「前回」 means for this System, stated rather than implied.
    findings_baseline_state: OverviewBaselineState = "unavailable"
    findings_baseline_label: str = ""
    findings_baseline_at: Optional[float] = None
    next_action: Optional[OverviewActionOut] = None
    next_action_state: OverviewActionState
    next_action_message: str = ""
    loop_stages: List[OverviewLoopStageOut] = []
    user_phase: str = "setup"
    runtime: Optional[OverviewRuntimeHealthOut] = None
    #: Sections whose derivation failed. The rest still render (#384).
    degraded_sections: List[OverviewSection] = []
    degraded_detail: Dict[str, str] = {}
    #: The canonical Purpose Frame / Purpose Chain (#388), reused verbatim.
    #: `None` only when its own guarded loader failed -- see `purpose_chain`
    #: in `degraded_sections`.
    purpose_chain: Optional["PurposeChainOut"] = None
    #: §4.5/#390's single adaptive next question over `purpose_chain` above
    #: (#391), embedded here instead of a second client query. `None` means
    #: either "no question right now" (§4.5's normal render) or "could not
    #: be derived" -- told apart by `"purpose_question" in degraded_sections`.
    purpose_question: Optional["PurposeQuestionOut"] = None


# --- Purpose Chain (Issue #387 Epic / #388) -----------------------------------
#
# docs/purpose-chain.md is the canonical design contract; §0 and §1 are the
# specification this module implements. Two things §0 makes non-negotiable:
#
# 1. **No new understanding model.** `desired_change` IS
#    `understanding_brief.BriefResult.vision`; `intervention` IS its
#    `system_purpose` claims; Capabilities ARE its `core_capabilities` claims.
#    `beneficiary_problem` is the Intent Brief `pain` field read the same way
#    Understanding Brief already reads `goal` for Vision. Purpose Chain adds
#    RELATION and LINEAGE on top of these existing rows -- it does not
#    reclassify a claim's 確認状態/出所, which is why every element reuses
#    `UnderstandingConfirmationState` / `UnderstandingProvenanceKind` and their
#    existing label dicts rather than defining a second vocabulary.
# 2. **Finite sets only, exact-name identity only.** Every vocabulary below is
#    a `Literal` defined exactly once, mirrored into `app/purpose_chain.py`
#    with `get_args`. No similarity/embedding/keyword join connects an element
#    or a relation -- `understanding_diff`'s exact-name rule is the only
#    identity rule in play.

#: The four Purpose Frame element kinds. Only three (`beneficiary_problem`,
#: `desired_change`, `intervention`) occupy the minimal Purpose Frame;
#: `core_capability` elements hang off `intervention` via
#: `intervention_to_capability` relations but are never part of the frame
#: itself (§1.2/§1.6).
PurposeElementKind = Literal[
    "beneficiary_problem", "desired_change", "intervention", "core_capability"
]

#: Whether an element's SOURCE ROW could be read and had content. Three
#: values, not two, for the same reason #380 split `unavailable` out of
#: `not_built`: `unknown` (the row was read; there is nothing there yet -- a
#: fact about the developer/system) and `unavailable` (the read itself failed
#: -- a fact about THIS request) must never render as the same sentence.
PurposeElementState = Literal["present", "unknown", "unavailable"]

#: The three fixed relation kinds of the minimal chain (§0 diagram). Outcome
#: lineage relations (#391) are a later, separate addition.
PurposeRelationKind = Literal[
    "problem_to_change", "change_to_intervention", "intervention_to_capability"
]

#: A relation's status, first-match over its endpoints and its current
#: decision (`purpose_chain.derive_purpose_chain`). `unknown` is a genuine
#: fifth value (not folded into `hypothesis`): "the connection cannot be
#: explained because an endpoint has no content" is a different fact from "the
#: connection is an unconfirmed guess", and #389's `relation_unknown` need
#: depends on being able to tell them apart.
PurposeRelationStatus = Literal["confirmed", "hypothesis", "conflicting", "unknown", "unavailable"]

#: Whether a relation's DECISION still matches its endpoints' current content.
#: Independent of `status`: a stale confirmed decision reads as `hypothesis`
#: (status) while `recheck_state` explains *why* it can no longer be trusted
#: as-is, and the decision row itself is never deleted or overwritten (§1.5).
PurposeRecheckState = Literal["current", "stale"]

#: Why a relation went stale. `upstream_changed` exists because staleness
#: propagates exactly one direction (downstream) through the fixed chain --
#: `snapshot_changed` is the one reason that comes from an ELEMENT's
#: `evidence_stale` rather than from a captured decision digest comparing
#: unequal.
PurposeStaleReason = Literal[
    "source_changed", "target_changed", "both_changed", "upstream_changed", "snapshot_changed"
]

#: 「今の判断に使えるか」, first-match over an element's own settledness and its
#: PRIMARY relation's status -- never a count, never an average (§1.4:
#: `frame_resolution_level` is the 3-slot MIN, explicitly not a mean or a
#: percentage). `L3` requires an `app/purpose_verification.py` (#391)
#: `purpose_outcome_criterion` row that TARGETS this exact element and has
#: all four of `measure` / `baseline_value` / `target_value` /
#: `observation_window` filled in -- see `purpose_chain.py`'s
#: `_resolution_level` for the exact check.
PurposeResolutionLevel = Literal["L0", "L1", "L2", "L3"]

#: Which existing table an element's content came from. `none` is a genuine
#: value (no row exists yet), not the absence of the field.
PurposeSourceKind = Literal["intent_item", "understanding_claim", "none"]

#: The Purpose Frame's overall completeness, first-match over the 3 frame
#: slots' `state`. `unavailable` is reserved for a guarded-loader failure
#: while constructing a frame slot -- never for "nothing extracted yet"
#: (that is `empty`).
PurposeFrameState = Literal["complete", "partial", "empty", "unavailable"]

#: Which composed section of the Purpose Chain failed to derive. A guarded
#: loader records the section here and degrades ONLY that section -- the
#: #380 discipline (§0 invariant 6): a relation-derivation failure must still
#: return the frame.
PurposeChainSection = Literal["frame", "relations", "capabilities"]


class PurposeElementOut(BaseModel):
    #: Stable across rebuilds: the bare kind for a frame-slot singleton
    #: (`"desired_change"`), or `kind + ":" + sha256(name)[:16]` for a kind
    #: that can repeat (`core_capability`, and any `intervention` claim beyond
    #: the frame slot). Never derived from a row id -- a row id is reassigned
    #: on every rebuild while describing the same element (#380's rule,
    #: applied here).
    id: str
    kind: PurposeElementKind
    state: PurposeElementState
    #: Level 0's 1〜2 文. The server never truncates; the Dashboard decides
    #: how much of this to show.
    display_statement: str = ""
    #: The element's full text (claim name + summary, or an Intent Brief
    #: item's `value_text`).
    statement: str = ""
    confirmation: UnderstandingConfirmationState
    confirmation_label: str
    provenance: UnderstandingProvenanceKind
    provenance_label: str
    resolution_level: PurposeResolutionLevel = "L0"
    source_kind: PurposeSourceKind = "none"
    source_ids: List[str] = []
    intent_revision_id: Optional[int] = None
    understanding_revision_id: Optional[int] = None
    snapshot_id: Optional[int] = None
    evidence: List[Dict[str, Any]] = []
    #: Can only be `True` for a `provenance == "implementation_fact"`
    #: element -- an Intent-sourced or AI-hypothesis element has no snapshot
    #: pin to go stale against.
    evidence_stale: bool = False
    #: Fixed sentences naming what is missing, only populated when
    #: `state == "unknown"`. Never model output (Principle 6).
    missing_information: List[str] = []
    is_mock: bool = False


class PurposeRelationOut(BaseModel):
    #: `f"{kind}:{source_id}->{target_id}"` -- stable because both endpoint
    #: ids are themselves stable (never a row id).
    id: str
    kind: PurposeRelationKind
    source_id: str
    target_id: str
    status: PurposeRelationStatus
    status_label: str
    recheck_state: PurposeRecheckState
    stale_reason: Optional[PurposeStaleReason] = None
    provenance: UnderstandingProvenanceKind
    provenance_label: str
    #: The current (non-superseded) `purpose_relation_decision` row, if any.
    decision_id: Optional[int] = None
    decided_at: Optional[float] = None
    decided_by: Optional[str] = None
    rationale: str = ""
    #: Carried from the TARGET element's own evidence. Never invented for the
    #: relation itself (§1.3: 捏造しない).
    evidence: List[Dict[str, Any]] = []
    #: Revisions captured when the current manual decision was created. They
    #: remain fixed when an endpoint later changes, making the decision's
    #: original scope auditable. ``None`` means this relation has no decision.
    created_intent_revision_id: Optional[int] = None
    created_understanding_revision_id: Optional[int] = None
    created_snapshot_id: Optional[int] = None
    #: Revisions of the endpoints in this freshly-derived projection. Comparing
    #: these with the captured values above explains which generation the
    #: relation currently describes without client-side source reconstruction.
    current_intent_revision_id: Optional[int] = None
    current_understanding_revision_id: Optional[int] = None
    current_snapshot_id: Optional[int] = None


class PurposeFrameOut(BaseModel):
    beneficiary_problem: Optional[PurposeElementOut] = None
    desired_change: Optional[PurposeElementOut] = None
    intervention: Optional[PurposeElementOut] = None


class PurposeChainOut(BaseModel):
    system_id: int
    session_id: Optional[int] = None
    generated_at: float
    frame: PurposeFrameOut
    #: The frame's 3 elements plus any additional `intervention` claims and
    #: every `core_capability` claim (§1.6).
    elements: List[PurposeElementOut] = []
    relations: List[PurposeRelationOut] = []
    #: The 3 frame slots' resolution level, MIN (never mean/percentage --
    #: §0 invariant 5, §1.4).
    frame_resolution_level: PurposeResolutionLevel = "L0"
    frame_state: PurposeFrameState = "empty"
    snapshot_id: Optional[int] = None
    understanding_revision_id: Optional[int] = None
    understanding_confirmed_at: Optional[float] = None
    degraded_sections: List[PurposeChainSection] = []
    degraded_detail: Dict[str, str] = {}


class PurposeRelationDecisionRequest(BaseModel):
    """The one write in this module. `decision_method` is always `manual` --
    there is no field for the caller to set it to anything else."""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    decision: Literal["confirmed", "rejected"]
    rationale: str = ""


# --- Purpose Needs / adaptive next-question (Issue #389) ----------------------
#
# `docs/purpose-chain.md` §2 is the specification. A "need" is never "this
# optional field is empty" -- every value below is derived deterministically
# from the Purpose Chain projection (`app/purpose_needs.py`): an element that
# is `unknown`, or a relation that is `unknown` / `conflicting` / `stale`.

#: The 7 fixed need codes (§2.2). Each is a classification of an ALREADY
#: system-generated signal into one of 7 buckets -- never a free-text
#: question category, which is why this stays a Principle-6 finite set rather
#: than a reasoning-model decision.
PurposeNeedCode = Literal[
    "frame_missing",
    "relation_unknown",
    "relation_conflict",
    "capability_justification_missing",
    "decision_criterion_missing",
    "human_value_judgement_required",
    "premise_recheck_required",
]

#: need_code -> answerability is a FIXED table (§2.3), not #286's
#: reasoning-model Question Router: a system-generated need already has a
#: known category by construction (Principle 6's "classification into a
#: small explicit finite set"), unlike a developer's open-ended free-text
#: question. `already_answered` / `unavailable` are never in the fixed table
#: -- they are read from response history / degraded-section state at derive
#: time (`app/purpose_needs.py.apply_response_state`).
PurposeAnswerability = Literal[
    "human_judgement", "system_researchable", "already_answered", "unavailable"
]

#: A need's own lifecycle given the developer's responses so far (§2.6/§2.7).
#: `deferred` and `waiting` are both real, auditable outcomes of an explicit
#: response -- never silently re-asked until the target's digest moves.
PurposeNeedState = Literal["available", "waiting", "answered", "deferred", "unavailable"]

#: 「分からない」 and 「今は答えない」 are answers, not errors -- each is its own
#: persisted, auditable fact (§2.6), never collapsed into a single "skipped".
PurposeResponseKind = Literal["confirm", "correct", "unknown", "defer", "investigate"]

#: Why a `need_id` deep link did not resolve to itself (§2.7). Never a 5th
#: "just show something" value -- the caller always learns which of these
#: four happened before falling back to the current question (or none).
PurposeQuestionFallbackReason = Literal["resolved", "not_found", "other_system", "deferred"]

#: What kind of Purpose Chain thing a need targets (§2.5). Distinct from
#: `PurposeSourceKind` (which existing TABLE an ELEMENT's content came from).
PurposeNeedTargetKind = Literal["element", "relation"]


class PurposeSuggestedAnswerOut(BaseModel):
    """An AI candidate built ONLY from an existing row's own text (§2.5).

    Never invented, and no LLM is called to produce it -- `text` is always
    copied verbatim from an existing element's `display_statement`, together
    with that element's own already-computed provenance/source. Absent
    (`None` on the parent) whenever there is no grounded candidate.
    """

    text: str
    provenance: UnderstandingProvenanceKind
    source_kind: PurposeSourceKind
    source_ids: List[str] = []
    is_mock: bool = False


class PurposeRoutedNeedOut(BaseModel):
    """One `system_researchable` need alongside the selected question (§2.4).

    Informational only -- routed needs never reach the developer as a
    question themselves; the Dashboard may use this to point at the Joint
    Understanding investigation that is expected to answer it instead.
    """

    need_id: str
    need_code: PurposeNeedCode
    target_kind: PurposeNeedTargetKind
    target_id: str
    target_label: str


class PurposeQuestionOut(BaseModel):
    """§2.5's question contract. `None` at the endpoint level means "質問なし"
    (rule row 7) -- there is no empty/placeholder question object."""

    need_id: str
    need_code: PurposeNeedCode
    #: The `PRIORITY_TABLE` row that chose this need (§2.4), 1-based.
    #:
    #: Every need code now carries a row, so a need derived from the current
    #: projection always has one. The field stays `Optional` for the case the
    #: type cannot rule out: a `need_id` deep link naming a code this server
    #: version does not know. Reporting `None` there is honest -- inventing a
    #: row number for a rule that did not run would forge the audit record of
    #: which rule matched.
    rule_row: Optional[int] = None
    #: Fixed server copy (Principle 6/7) -- never model output.
    prompt: str
    why_now: str
    blocked_decision: str
    unlocks: str
    defer_impact: str
    target_kind: PurposeNeedTargetKind
    target_id: str
    target_label: str
    answerability: PurposeAnswerability
    suggested_answer: Optional[PurposeSuggestedAnswerOut] = None
    state: PurposeNeedState
    source_revision_ids: List[int] = []
    #: Set only when a `need_id` deep link fell back to a different question
    #: (or to none) -- see `PurposeQuestionFallbackReason`.
    fallback_reason: Optional[PurposeQuestionFallbackReason] = None
    routed_needs: List[PurposeRoutedNeedOut] = []


class PurposeNeedRespondRequest(BaseModel):
    """`decision_method` is always `manual` on the response row itself --
    there is no field for the caller to set it to anything else. This is a
    fact about WHO responded, independent of what a downstream investigation
    (opened for `unknown`/`investigate`) later concludes with
    `decision_method='reasoning_llm'`."""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    response_kind: PurposeResponseKind
    #: The confirm/correct value, or a free-text rationale for defer/unknown/
    #: investigate. Optional -- a bare `confirm` needs no text.
    value_text: str = ""


class PurposeNeedResponseOut(BaseModel):
    id: int
    session_id: int
    system_id: int
    need_id: str
    need_code: PurposeNeedCode
    response_kind: PurposeResponseKind
    value_text: str
    target_kind: PurposeNeedTargetKind
    target_id: str
    #: The target's digest AT RESPONSE TIME -- what a `defer` reappears
    #: against once it no longer matches (§2.6).
    target_digest: str
    decision_method: str
    responded_by: Optional[str] = None
    #: Set when `confirm`/`correct` was reused through the existing Intent
    #: Brief confirm/correct/create implementation (never a second
    #: revision-chain implementation, §2.6).
    linked_intent_item_id: Optional[int] = None
    #: Set when `confirm`/`correct` was reused through
    #: `purpose_chain.record_relation_decision`.
    linked_relation_decision_id: Optional[int] = None
    #: Set when `unknown`/`investigate` opened a Joint Understanding session
    #: with `trigger='purpose_need'`.
    linked_joint_session_id: Optional[int] = None
    superseded_by_id: Optional[int] = None
    created_at: float


# --- Purpose Verification / Experience-Outcome-Reuse (Issue #391) ------------
#
# `docs/purpose-chain.md` §4 is the specification. Three OPTIONAL concepts a
# developer may attach to a Purpose Chain element or relation, by the SAME
# stable string identity `app/purpose_chain.py` already uses -- never a row
# id, and never required for every System (§4.1: "全 System へ一律に要求しな
# い"). Creation is only ever OFFERED alongside a currently-`available`
# `app/purpose_needs.py` need whose code is in the fixed
# `purpose_verification.CREATABLE_NEED_CODES` table; "the Purpose Frame is at
# L1" is explicitly not a reason (§4.1), so there is no endpoint that lets a
# caller create one without naming the justifying `need_id`.
#
# `decision_method` is hardcoded `'manual'` on every write in this area, same
# as `purpose_chain.py` / `purpose_needs.py` -- this module calls no
# reasoning model either (Principle 6), and an outcome's `observed` /
# `contradicted` verdict is never inferred from a trace: it is always the
# developer's own recorded reading of evidence they themselves curated
# (§4.2).

#: `experience_hypothesis` and `reuse_hypothesis` share this exact lifecycle
#: (`docs/purpose-chain.md` §4.1: "state は experience と同じ") -- one
#: `Literal` for both, since defining it twice would let the two drift apart
#: for no reason.  `retired` is a manual withdrawal (the developer decided
#: the hypothesis was wrong or no longer relevant); it is NEVER a synonym for
#: "not yet confirmed".
PurposeHypothesisState = Literal["proposed", "confirmed", "retired"]

#: `purpose_outcome_criterion`'s own 6-value lifecycle (§4.1/§4.2).
#: `proposed` / `confirmed` are the same manual commitment steps as a
#: hypothesis; `observed` / `contradicted` are set ONLY together with an
#: evidence write (`PurposeOutcomeResultRequest`) -- never derived from
#: silence. `not_observed` ("analytics が無ければ") and `not_computed`
#: ("canonical mapping が無ければ") are each their own explicit manual
#: recording of why a verdict could not be reached, not a value the server
#: infers from an empty column -- see `purpose_verification.py`'s module
#: docstring for why an inferred value here would violate §4.2's "runtime
#: trace だけで利用者の成功を推測しない" rule one level up.
PurposeOutcomeCriterionState = Literal[
    "proposed", "confirmed", "observed", "contradicted", "not_observed", "not_computed"
]

#: Which of the two evidence COLUMNS a result write targets. §4.2 requires
#: human-reported evidence and runtime observation to stay in separate
#: columns, never merged into one "result" -- this is the axis that picks
#: which column `record_outcome_result` writes to.
PurposeOutcomeEvidenceSource = Literal["human_reported", "runtime_observed"]
PurposeOutcomeEvidenceState = Literal[
    "observed", "contradicted", "not_observed", "not_computed"
]

#: A recorded verdict is a judgement about the evidence, always the
#: developer's own reading of it (never computed from the evidence text).
PurposeOutcomeVerdict = Literal["supports", "contradicts"]

#: Whether an `experiment_id` / `candidate_version_id` lineage column
#: resolves to a real, System-scoped row right now. `unresolved` (the id was
#: set but the row is gone) is a genuine third value -- §4.3: "対応が無けれ
#: ば「関連不明」と表示する" -- never silently downgraded to `none`, which
#: would erase the fact that a lineage claim was once made.
PurposeOutcomeLineageState = Literal["none", "linked", "unresolved"]

#: Which of the three concepts a verification prompt or a listing row is
#: about. Distinct from `PurposeNeedTargetKind` (element vs relation) -- this
#: is "what kind of verification", not "what kind of Purpose Chain node".
PurposeVerificationConceptKind = Literal[
    "experience_hypothesis", "outcome_criterion", "reuse_hypothesis"
]


class PurposeExperienceHypothesisOut(BaseModel):
    """A minimal claim about what a real user would experience if this
    element/relation's causal claim holds. Free text (§4.1) -- the developer
    states it themselves; this module invents no wording."""

    id: int
    system_id: int
    session_id: int
    target_kind: PurposeNeedTargetKind
    target_id: str
    #: Copied from the justifying need at creation time, for display without
    #: a second lookup.
    target_label: str
    #: `purpose_chain.element_digest` / the relation's own identity fields at
    #: CREATION time. Captured for audit; #391 does not re-check it against
    #: the current chain on every read (no staleness re-derivation here --
    #: an explicit non-goal for this issue, see the module docstring).
    target_digest: str
    #: The `purpose_needs` need that made this creatable (§4.1). Never a
    #: guess -- the create endpoint refuses without one.
    source_need_id: str
    source_need_code: PurposeNeedCode
    statement: str
    state: PurposeHypothesisState
    decision_method: str = "manual"
    created_by: Optional[str] = None
    created_at: float
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[float] = None
    retired_by: Optional[str] = None
    retired_at: Optional[float] = None
    retirement_reason: str = ""


class PurposeReuseHypothesisOut(BaseModel):
    """Identical shape to `PurposeExperienceHypothesisOut` -- a separate
    class (not a type alias) because the two are stored in separate tables
    and are never interchangeable, even though every field matches."""

    id: int
    system_id: int
    session_id: int
    target_kind: PurposeNeedTargetKind
    target_id: str
    target_label: str
    target_digest: str
    source_need_id: str
    source_need_code: PurposeNeedCode
    statement: str
    state: PurposeHypothesisState
    decision_method: str = "manual"
    created_by: Optional[str] = None
    created_at: float
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[float] = None
    retired_by: Optional[str] = None
    retired_at: Optional[float] = None
    retirement_reason: str = ""


class PurposeOutcomeCriterionOut(BaseModel):
    """成果証拠 (§4.1/§4.2/§4.3/§4.4). `measure` / `baseline_value` /
    `target_value` / `observation_window` are the four fields §4.4 checks for
    L3 -- all plain developer-authored text, never LLM output."""

    id: int
    system_id: int
    session_id: int
    target_kind: PurposeNeedTargetKind
    target_id: str
    target_label: str
    target_digest: str
    source_need_id: str
    source_need_code: PurposeNeedCode
    measure: str = ""
    baseline_value: str = ""
    target_value: str = ""
    observation_window: str = ""
    state: PurposeOutcomeCriterionState
    #: §4.3: explicit lineage columns only, never a System-wide existence
    #: check. `lineage_state` reports whether the id (when set) still
    #: resolves -- `unresolved` renders as 「関連不明」, never as "no lineage".
    experiment_id: Optional[int] = None
    candidate_version_id: Optional[int] = None
    lineage_state: PurposeOutcomeLineageState = "none"
    #: §4.2: two SEPARATE evidence columns, never merged into one "result".
    human_reported_evidence: Optional[str] = None
    human_reported_verdict: Optional[PurposeOutcomeVerdict] = None
    human_reported_at: Optional[float] = None
    human_reported_by: Optional[str] = None
    human_reported_state: Optional[PurposeOutcomeEvidenceState] = None
    human_reported_is_synthetic: bool = False
    runtime_observation_text: Optional[str] = None
    runtime_observation_verdict: Optional[PurposeOutcomeVerdict] = None
    runtime_observed_at: Optional[float] = None
    runtime_observed_by: Optional[str] = None
    runtime_observation_state: Optional[PurposeOutcomeEvidenceState] = None
    runtime_observation_is_synthetic: bool = False
    #: §4.2: "synthetic fixture の結果を実利用者の成果として表示しない" -- the
    #: flag a result write carries, shown alongside the result, never
    #: inferred from where the evidence came from.
    is_synthetic: bool = False
    decision_method: str = "manual"
    created_by: Optional[str] = None
    created_at: float
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[float] = None


class PurposeVerificationStateOut(BaseModel):
    """`GET /purpose-chain/verification` -- every concept currently recorded
    for one session, grouped by kind. Not paginated (§0 invariant 5: this
    Epic creates no dashboard of its own; the expected count per session is
    small, gated as it is by needs)."""

    system_id: int
    session_id: Optional[int] = None
    experience_hypotheses: List[PurposeExperienceHypothesisOut] = []
    outcome_criteria: List[PurposeOutcomeCriterionOut] = []
    reuse_hypotheses: List[PurposeReuseHypothesisOut] = []


class PurposeVerificationPromptOut(BaseModel):
    """§4.5's ONE verification prompt. At most one, chosen deterministically
    (`purpose_verification.select_verification_prompt`) -- the same
    at-most-one discipline `PurposeQuestionOut` already applies to Purpose
    Needs, extended to verification-concept creation. `None` at the endpoint
    level means 「検証条件はまだ必要ありません」 -- there is no empty
    placeholder object."""

    concept_kind: PurposeVerificationConceptKind
    need_id: str
    need_code: PurposeNeedCode
    target_kind: PurposeNeedTargetKind
    target_id: str
    target_label: str
    #: 何を検証するか. Fixed server copy (Principle 6/7), never model output.
    prompt: str
    #: なぜ今か -- reused verbatim from the justifying need's own copy
    #: (`purpose_needs._NEED_COPY`), so this prompt and the underlying need's
    #: own question never disagree about why now matters.
    why_now: str
    #: どの判断に効くか -- reused verbatim from the justifying need's
    #: `blocked_decision`.
    blocked_decision: str
    #: 最小の観測方法 -- fixed copy naming the smallest thing worth writing
    #: down for this concept kind (Principle 6: never model-authored).
    observation_hint: str


class PurposeExperienceHypothesisCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int
    #: The `purpose_needs` need this concept is being created FOR (§4.1) --
    #: required, never inferred from the target alone.
    need_id: str
    statement: str


class PurposeReuseHypothesisCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int
    need_id: str
    statement: str


class PurposeOutcomeCriterionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int
    need_id: str
    measure: str
    baseline_value: str
    target_value: str
    observation_window: str


class PurposeVerificationSessionRequest(BaseModel):
    """The bare `session_id` body every no-extra-input verification
    transition needs (confirm actions on either hypothesis table, and the
    outcome criterion's own confirm)."""

    model_config = ConfigDict(extra="forbid")

    session_id: int


class PurposeHypothesisRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int
    reason: str = ""


class PurposeOutcomeCriterionLinkRequest(BaseModel):
    """§4.3: explicit lineage only. Both fields optional, but at most one may
    be set -- an outcome criterion has at most one canonical mapping, never a
    pair of unrelated candidate/experiment ids at once."""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    experiment_id: Optional[int] = None
    candidate_version_id: Optional[int] = None

    @model_validator(mode="after")
    def exactly_zero_or_one_lineage_target(self):
        if self.experiment_id is not None and self.candidate_version_id is not None:
            raise ValueError("Specify at most one of experiment_id or candidate_version_id")
        return self


class PurposeOutcomeResultRequest(BaseModel):
    """Records a result against EXACTLY ONE of the two evidence columns
    (`source` picks which), always paired with the developer's own verdict --
    never a bare state transition with no evidence attached (§4.2)."""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    source: PurposeOutcomeEvidenceSource
    verdict: PurposeOutcomeVerdict
    evidence_text: str
    is_synthetic: bool = False


class PurposeOutcomeUnavailableRequest(BaseModel):
    """Explicitly records why one evidence source cannot yield a verdict."""

    model_config = ConfigDict(extra="forbid")

    session_id: int
    source: PurposeOutcomeEvidenceSource
    state: Literal["not_observed", "not_computed"]
    reason: str


# ---------------------------------------------------------------------------
# UX Design Lineage (Epic #405, Issues #407/#408). See
# docs/ux-design-lineage.md for the full contract -- these `Literal` aliases
# and their `*Out`/`*Request` models are re-declared here (never imported
# from `app/ux_design.py` / `app/solution_design.py`) for the same reason
# `EvolutionMaturityState` and the Purpose Chain vocabularies are: FastAPI
# needs a real enum in the OpenAPI schema, and
# `test_interview_type_parity.py`'s `FINITE_TYPE_NAMES` is what keeps the
# Dashboard's TypeScript unions from silently drifting away from these.
#
# Journey / Requirement / Solution Design are the two new PERSISTED design
# layers this Epic adds (§1: unlike Purpose Chain, this content cannot be
# re-derived from any existing row). Every Out model that reports a status
# computed by folding an append-only decision ledger (`design_status`,
# `option_status`, `link_state`, ...) exposes that status as a field but the
# UNDERLYING TABLES never store it as a column -- see the table comments in
# `app/db.py` for why. Every Request model omits `decision_method` /
# `decided_by` / `created_by` / `authored_by_kind`: those come from the
# authenticated `Principal` and the route, never from the caller
# (`routes/purpose_chain.py`'s docstring states the same rule for Purpose
# Chain; §2.10/§3's route contracts require it here identically).
# ---------------------------------------------------------------------------

#: Whether a Journey describes the system as it stands today or as it should
#: become. Lives on the IDENTITY row (`ux_journey`), never on a revision --
#: §2.3: letting a revision change perspective would splice two different
#: subjects' histories into one Journey. A `to_be` Journey names its `as_is`
#: counterpart through `baseline_journey_id` rather than becoming it.
UxJourneyPerspective = Literal["as_is", "to_be"]

#: The developer's own declaration of whether a `to_be` Journey SHOULD have
#: an `as_is` baseline (§2.3). `undecided` is the honest default -- distinct
#: from `greenfield`, which is an explicit "this is genuinely new, there is
#: no current-state Journey to compare against" statement. Confusing the two
#: would make a new system's Journey read as if the developer forgot to link
#: one, when in fact there is nothing to link.
UxJourneyBaselineMode = Literal["linked", "greenfield", "undecided"]

#: Derived at read time (never stored) by the first-match rule in §2.3:
#: `as_is` Journeys and explicit `greenfield` declarations are
#: `not_applicable`; `undecided` is `absent`; a `baseline_journey_id` that
#: currently resolves to an `as_is` Journey in the same System is `linked`;
#: anything else is `unresolved`. `not_applicable` is a genuine third answer
#: (§0 invariant 8), not a synonym for `absent` -- one means "the developer
#: said there is nothing to compare", the other means "nobody has decided".
UxJourneyBaselineState = Literal["linked", "unresolved", "absent", "not_applicable"]

#: Whose voice authored a Journey/Requirement/Solution-Design-Option
#: revision's TEXT -- independent of `decision_method` (who chose to record
#: it) and of the decision ledger (who later confirmed/adopted it). §0
#: invariant 3 / §2.5: these are three separate axes, never folded into one.
#: A `reasoning_model`-authored revision CAN later be `confirmed` -- that
#: means a human approved AI-written text, not that authorship changed.
UxDesignAuthorshipKind = Literal["developer", "reasoning_model"]

#: A Journey Step's declared EXPECTATION of where evidence for its success
#: would be found -- never an observed outcome (§0 invariant 6 / §2.4's
#: comment on `ux_journey_step`). Choosing `runtime_trace` does not make a
#: later trace count as success; `purpose_outcome_criterion` remains the only
#: place an outcome verdict is recorded. `none` is the honest default for a
#: Step nobody has thought through evidence for yet.
UxEvidenceSourceKind = Literal["runtime_trace", "human_report", "external_analytics", "none"]

#: A Requirement's kind. `out_of_scope` is a first-class member, not a
#: deletion -- "we decided not to do this" is itself worth a traceable
#: record (§2.4's comment on `ux_requirement`). An `out_of_scope` Requirement
#: can never carry acceptance criteria (422
#: `out_of_scope_requirement_not_verifiable`) or a Solution Design target
#: link (422 `out_of_scope_requirement_not_implementable`) -- a thing
#: declared out of scope having a verification/implementation target would
#: mean the kind itself had stopped meaning anything.
UxRequirementKind = Literal["functional", "non_functional", "constraint", "out_of_scope"]

#: How an Acceptance Criterion COULD be checked -- a finite classification of
#: method, never a record that it WAS checked. Verification itself happens in
#: the named existing system (Replay, Experiments, a human review), never in
#: this layer.
UxVerificationMethod = Literal[
    "manual_review", "replay", "experiment", "runtime_observation", "not_verifiable"
]

#: DERIVED (§2.5), never a stored column: the latest non-superseded
#: `ux_design_decision` row for `(system_id, subject_kind, subject_key)`, or
#: `proposed` when no such row exists. `confirm -> confirmed`, `reject ->
#: rejected`, `retire -> retired`, `reinstate -> proposed`. A stored lifecycle
#: value can drift from the rows it describes; a derived one cannot (#337 /
#: #338 / #349's discipline, applied to this layer).
UxDesignStatus = Literal["proposed", "confirmed", "rejected", "retired"]

#: The finite actions recorded in `ux_design_decision`. `reinstate` is the
#: only way back from `rejected`/`retired` to `proposed` -- there is no
#: silent un-reject; a human decides again, and the prior decision row is
#: never deleted (§0 invariant 4).
UxDesignDecisionKind = Literal["confirm", "reject", "retire", "reinstate"]

#: Whether the currently-effective `confirm` decision's `captured_digest`
#: still matches the subject's current content digest. Independent of
#: `design_status` (§2.5): a stale confirmed item STAYS `confirmed` --
#: re-confirmation is invited, not forced, and the original decision row
#: survives untouched (the same discipline `purpose_relation_decision` and
#: `PurposeRecheckState` already use one layer up).
UxDesignRecheckState = Literal["current", "stale"]

#: Whether a revision row (`ux_journey_revision` / `ux_requirement_revision`
#: / `solution_design_option`) is the current head of its append-only chain.
#: A content axis, not a judgement axis -- orthogonal to `design_status`.
UxRevisionState = Literal["current", "superseded"]

#: Which of the three canonical upstream sources a `ux_journey_upstream_ref`
#: resolves against (§2.7's table): the Purpose Chain's elements, the same
#: projection's relations, or `understanding_capability_entity`'s current
#: head. Exactly one canonical source per kind, resolved fresh at read time
#: -- never a copy of the target's content (§1).
UxRefKind = Literal["purpose_element", "purpose_relation", "capability_entity"]

#: WHO ASSERTED an upstream/downstream reference, mapped from the
#: reference's own `decision_method` through a fixed table (§2.7, the same
#: `node_design._DECISION_METHOD_TO_RELATION_STATUS` translation) --
#: `manual -> confirmed`, `reasoning_llm -> proposed`, `deterministic ->
#: derived`. This is never a second stored status column; it is read
#: straight off `decision_method`.
UxRefRelationStatus = Literal["confirmed", "proposed", "derived"]

#: Whether the referenced row could be found at all, kept apart from
#: `target_state` (§2.7): `resolved` means the canonical source was read and
#: the target exists there now; `unresolved` means the source was read but
#: the target is gone (e.g. a superseded Capability's OLD id); `unavailable`
#: means the canonical source itself could not be read for THIS request. An
#: unreadable source must never render as "the target does not exist".
UxRefTargetResolution = Literal["resolved", "unresolved", "unavailable"]

#: Whether a reference's `captured_digest` still matches the target's
#: current digest. `not_captured` is the fail-closed value for a legacy row
#: with an empty `captured_digest` (§2.7's explicit "not `current`" rule,
#: mirroring #337's `premise_not_captured`) -- an uncaptured digest can never
#: be silently treated as "nothing has changed".
UxRefRecheckState = Literal["current", "stale", "not_captured"]

#: The finite kinds of design artifact this layer can reference (never
#: store the body of, §2.8).
UxArtifactKind = Literal["wireframe", "adr", "spec", "diagram", "research_note", "other"]

#: Three DIFFERENT claims about a `content_hash`, never collapsed into two
#: (§2.8): `verified` is reachable ONLY for a `repo:<path>` URI the system
#: itself resolved via `git show <sha>:<path>` on a pinned snapshot and hash-
#: matched -- probe-agent fetches no other URI (SSRF / Principle 5).
#: `unverified` is every external URL/Wiki/Figma reference, where the hash is
#: the developer's own assertion. `unreachable` is a repo path that used to
#: verify and no longer resolves on the current snapshot -- a distinct fact
#: from having never been checked.
UxArtifactVerificationState = Literal["verified", "unverified", "unreachable"]

#: What kind of thing an artifact reference or a decision-ledger row is
#: attached to. Two separate finite sets on purpose (this one and
#: `UxDesignSubjectKind` below): an artifact can illustrate a Design Option,
#: but only a Journey/Requirement/reference/link can be
#: confirmed/rejected/retired/reinstated (§2.4's tables).
UxArtifactSubjectKind = Literal[
    "journey", "journey_step", "requirement", "solution_design", "design_option"
]

#: What kind of thing a `ux_design_decision` row judges. Journey Steps and
#: Requirement Acceptance Criteria are never independently decidable --
#: their content lives inside a revision, and confirming/rejecting the
#: revision (via `journey`/`requirement`) is the only decision that applies.
UxDesignSubjectKind = Literal[
    "journey", "requirement", "requirement_step_link", "journey_upstream_ref", "artifact_reference"
]

#: Diff vocabulary for step-by-step / criterion-by-criterion comparison.
#: Matching is EXACT `step_key`/`criterion_key` equality only (§2.4's
#: comment on `ux_journey_step`, and §0 invariant 9) -- never text
#: similarity or embeddings.
UxDiffChangeKind = Literal["added", "removed", "changed", "unchanged"]

#: Whether a diff endpoint could produce a real comparison. `not_applicable`
#: is the `baseline-diff` answer when the Journey has no baseline to compare
#: against (§2.10) -- returning an empty diff there would read as "no
#: changes" instead of "there is nothing to diff against". `unavailable` is
#: reserved for a read failure, never for "nothing to compare" (§0
#: invariant 8).
UxDiffState = Literal["available", "not_applicable", "unavailable"]

#: `GET /solution-designs/{design_key}/change-origins`' classification of
#: WHERE a non-current reference/link's change originated (§3.5) -- a
#: deterministic mapping from each link's own `stale_reason` + `ref_kind` /
#: `target_kind`, never a guess. Telling "the Capability changed" apart from
#: "the snapshot merely moved" is the first thing an existing-system
#: improvement loop needs to know.
UxChangeOrigin = Literal[
    "purpose", "capability", "journey", "requirement",
    "solution_design", "implementation_target", "snapshot",
]

#: The exclusive-choice ledger's finite actions (§3.2), deliberately
#: separate from `UxDesignDecisionKind`: `adopt` is a choice AMONG N
#: competing options, never a non-exclusive confirmation. A second `adopt`
#: while one option is already adopted is refused (409
#: `solution_design_option_already_adopted`) rather than auto-`withdraw`ing
#: the first -- the system never fabricates a human's withdrawal decision.
SolutionDesignOptionDecision = Literal["adopt", "hold", "reject", "withdraw"]

#: DERIVED (never stored) by folding `solution_design_decision` for one
#: `option_key`, the same "derived, never stored" rule `UxDesignStatus`
#: uses. `draft` is the state before any decision row exists for that option.
SolutionDesignOptionStatus = Literal["draft", "adopted", "held", "rejected", "withdrawn"]

#: The 8 kinds of existing implementation entity a Solution Design Option
#: can point at (§3.3's table), each resolved against exactly ONE canonical
#: source at read time. `static_flow` (a snapshot-pinned entry-point path)
#: and `runtime_flow` (an SDK-assigned execution correlation id) are kept
#: separate on purpose -- one word covering both facts is exactly the #366
#: defect this Epic is careful not to repeat.
SolutionTargetKind = Literal[
    "capability", "static_flow", "runtime_flow", "evolution_node",
    "component", "cell_definition", "cell_binding", "probe_point",
]

#: A target/requirement link's read-time state, first-match over §3.4's
#: 6-step table (unreadable source -> `unavailable`; target gone ->
#: `unresolved`; pinned snapshot moved -> `stale`; captured digest stale ->
#: `stale`; upstream Requirement/Journey/Purpose stale -> `stale`; else
#: `current`). `review_required` is deliberately NOT a 5th value here --
#: it is the finite consequence of `link_state != "current"`, reported
#: through its own `stale_reason` axis instead of inflating this vocabulary.
SolutionLinkState = Literal["current", "stale", "unresolved", "unavailable"]

#: WHY a link went stale -- distinct reasons so `change-origins` can tell
#: "the Requirement text changed" apart from "the pinned snapshot moved"
#: apart from "the upstream Journey/Purpose chain went stale first" (§3.4).
SolutionLinkStaleReason = Literal[
    "requirement_changed", "design_changed", "target_changed",
    "snapshot_changed", "upstream_changed",
]

#: `GET /solution-designs/{design_key}/handoff`'s top-level verdict (§3.7,
#: the same `assembly_state` idea `NodeDesignHandoffOut` already uses under
#: a name that fits THIS handoff's own read-only-reference discipline).
#: `incomplete` means at least one reference named in `unresolved_references`
#: could not be resolved -- never silently dropped.
SolutionHandoffState = Literal["complete", "incomplete", "unavailable"]


class UxJourneyStepOut(BaseModel):
    """One ordered Step of a Journey revision (§2.4's `ux_journey_step`).
    Steps have no revision chain of their own -- they ARE the content of the
    Journey revision they belong to."""

    id: int
    step_key: str
    step_order: int
    user_intent: str = ""
    system_response: str = ""
    success_criteria: str = ""
    failure_mode: str = ""
    recovery_path: str = ""
    evidence_expectation: str = ""
    evidence_source_kind: UxEvidenceSourceKind = "none"
    content_digest: str


class UxJourneyRevisionOut(BaseModel):
    """Append-only Journey content (§2.4/§2.6). `decision_method` records who
    chose to persist this revision; `authored_by_kind` records whose words
    it is -- the two are independent (§0 invariant 3)."""

    id: int
    journey_id: int
    revision_number: int
    title: str = ""
    beneficiary: str = ""
    usage_context: str = ""
    entry_trigger: str = ""
    value_arrival: str = ""
    summary: str = ""
    content_digest: str
    authored_by_kind: UxDesignAuthorshipKind = "developer"
    decision_method: Literal["manual", "reasoning_llm"] = "manual"
    intelligence_run_id: Optional[int] = None
    change_note: str = ""
    created_by: Optional[str] = None
    created_at: float
    revision_state: UxRevisionState = "current"
    superseded_by_id: Optional[int] = None
    steps: List[UxJourneyStepOut] = []


class UxJourneyUpstreamRefOut(BaseModel):
    """A Journey's reference to a Purpose element / relation / Capability
    entity, carrying the 4 independent axes §2.7 requires
    (`relation_status` / `target_state` / `target_resolution` /
    `recheck_state`) rather than one merged status. `target_state` is the
    target's OWN vocabulary value copied verbatim -- never translated
    (#380's superset rule)."""

    id: int
    journey_id: int
    ref_kind: UxRefKind
    target_ref: str
    target_row_id: Optional[int] = None
    target_name: Optional[str] = None
    relation_status: UxRefRelationStatus
    target_state: str
    target_resolution: UxRefTargetResolution
    recheck_state: UxRefRecheckState
    captured_digest: str = ""
    captured_session_id: Optional[int] = None
    note: str = ""
    decision_method: Literal["manual", "reasoning_llm", "deterministic"] = "manual"
    created_by: Optional[str] = None
    created_at: float
    superseded_by_id: Optional[int] = None


class UxArtifactReferenceOut(BaseModel):
    """A wireframe/ADR/spec/diagram/research-note reference. No body field
    exists anywhere on this model -- structurally, not by convention
    (§2.8)."""

    id: int
    subject_kind: UxArtifactSubjectKind
    subject_key: str
    artifact_kind: UxArtifactKind
    title: str = ""
    uri: str
    media_type: str = ""
    content_hash: str
    hash_algorithm: Literal["sha256"] = "sha256"
    byte_size: Optional[int] = None
    verification_state: UxArtifactVerificationState = "unverified"
    verified_snapshot_id: Optional[int] = None
    verified_commit_sha: Optional[str] = None
    verified_at: Optional[float] = None
    decision_method: Literal["manual", "reasoning_llm", "deterministic"] = "manual"
    created_by: Optional[str] = None
    created_at: float
    superseded_by_id: Optional[int] = None


class UxDesignDecisionOut(BaseModel):
    """One row of the confirm/reject/retire/reinstate ledger (§2.5).
    `decision_method` is always `manual` -- there is no path that writes
    anything else here."""

    id: int
    subject_kind: UxDesignSubjectKind
    subject_key: str
    subject_row_id: Optional[int] = None
    decision: UxDesignDecisionKind
    rationale: str = ""
    captured_digest: str = ""
    captured_revision_id: Optional[int] = None
    decision_method: Literal["manual"] = "manual"
    decided_by: Optional[str] = None
    superseded_by_id: Optional[int] = None
    created_at: float


class UxJourneyOut(BaseModel):
    """Journey summary: the identity row plus its DERIVED axes
    (`design_status`, `recheck_state`, `baseline_state`) -- none of which
    are columns on `ux_journey` (§2.5)."""

    id: int
    system_id: int
    journey_key: str
    perspective: UxJourneyPerspective
    baseline_mode: UxJourneyBaselineMode = "undecided"
    baseline_journey_id: Optional[int] = None
    baseline_journey_key: Optional[str] = None
    baseline_state: UxJourneyBaselineState
    current_revision_id: Optional[int] = None
    current_revision_number: Optional[int] = None
    title: str = ""
    design_status: UxDesignStatus = "proposed"
    recheck_state: UxDesignRecheckState = "current"
    created_by: Optional[str] = None
    created_at: float
    updated_at: float


class UxJourneyDetailOut(UxJourneyOut):
    """§2.10's `GET/POST /ux-design/journeys/{journey_key}` shape: the
    summary plus the current revision's full content and every reference/
    decision attached to it."""

    current_revision: Optional[UxJourneyRevisionOut] = None
    upstream_refs: List[UxJourneyUpstreamRefOut] = []
    artifact_references: List[UxArtifactReferenceOut] = []
    decisions: List[UxDesignDecisionOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class UxJourneyListOut(BaseModel):
    system_id: int
    generated_at: float
    journeys: List[UxJourneyOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class UxJourneyRevisionListOut(BaseModel):
    system_id: int
    journey_id: int
    journey_key: str
    generated_at: float
    revisions: List[UxJourneyRevisionOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class UxJourneyStepDiffEntryOut(BaseModel):
    """One `step_key`'s comparison between two revisions, matched by EXACT
    key equality only (§0 invariant 9)."""

    step_key: str
    change_kind: UxDiffChangeKind
    from_step: Optional[UxJourneyStepOut] = None
    to_step: Optional[UxJourneyStepOut] = None


class UxJourneyDiffOut(BaseModel):
    """`GET .../journeys/{key}/diff` and `.../baseline-diff` share this
    shape. `diff_state = "not_applicable"` (never an empty `steps` list) is
    the required response when `baseline-diff` has no baseline to compare
    against (§2.10)."""

    system_id: int
    journey_id: int
    journey_key: str
    generated_at: float
    diff_state: UxDiffState = "available"
    from_revision_id: Optional[int] = None
    from_revision_number: Optional[int] = None
    to_revision_id: Optional[int] = None
    to_revision_number: Optional[int] = None
    steps: List[UxJourneyStepDiffEntryOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class UxAcceptanceCriterionOut(BaseModel):
    """One Acceptance Criterion of a Requirement revision (§2.4's
    `ux_requirement_acceptance_criterion`) -- content of the revision, not
    an independently versioned entity, mirroring `UxJourneyStepOut`."""

    id: int
    criterion_key: str
    criterion_order: int
    statement: str = ""
    verification_method: UxVerificationMethod = "manual_review"
    verification_note: str = ""
    content_digest: str


class UxRequirementRevisionOut(BaseModel):
    id: int
    requirement_id: int
    revision_number: int
    requirement_kind: UxRequirementKind
    statement: str = ""
    rationale: str = ""
    constraint_text: str = ""
    out_of_scope_note: str = ""
    content_digest: str
    authored_by_kind: UxDesignAuthorshipKind = "developer"
    decision_method: Literal["manual", "reasoning_llm"] = "manual"
    intelligence_run_id: Optional[int] = None
    change_note: str = ""
    created_by: Optional[str] = None
    created_at: float
    revision_state: UxRevisionState = "current"
    superseded_by_id: Optional[int] = None
    acceptance_criteria: List[UxAcceptanceCriterionOut] = []


class UxRequirementStepLinkOut(BaseModel):
    """The many-to-many Requirement<->Journey-Step bridge (§2.4's
    `ux_requirement_step_link`). Resolved by `step_key` against the
    Journey's CURRENT revision at read time -- `target_resolution` /
    `recheck_state` reuse the same two axes `UxJourneyUpstreamRefOut` uses,
    because a step link faces the identical "did the target move / vanish"
    question §2.7 already answers for upstream references."""

    id: int
    requirement_id: int
    journey_id: int
    journey_key: Optional[str] = None
    step_key: str
    step_label: Optional[str] = None
    captured_journey_revision_id: Optional[int] = None
    captured_step_digest: str = ""
    target_resolution: UxRefTargetResolution
    recheck_state: UxRefRecheckState
    note: str = ""
    decision_method: Literal["manual", "reasoning_llm", "deterministic"] = "manual"
    created_by: Optional[str] = None
    created_at: float
    superseded_by_id: Optional[int] = None


class UxRequirementOut(BaseModel):
    id: int
    system_id: int
    requirement_key: str
    requirement_kind: UxRequirementKind
    current_revision_id: Optional[int] = None
    current_revision_number: Optional[int] = None
    statement: str = ""
    design_status: UxDesignStatus = "proposed"
    recheck_state: UxDesignRecheckState = "current"
    created_by: Optional[str] = None
    created_at: float
    updated_at: float


class UxRequirementDetailOut(UxRequirementOut):
    current_revision: Optional[UxRequirementRevisionOut] = None
    step_links: List[UxRequirementStepLinkOut] = []
    artifact_references: List[UxArtifactReferenceOut] = []
    decisions: List[UxDesignDecisionOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class UxRequirementListOut(BaseModel):
    system_id: int
    generated_at: float
    requirements: List[UxRequirementOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class UxRequirementRevisionListOut(BaseModel):
    system_id: int
    requirement_id: int
    requirement_key: str
    generated_at: float
    revisions: List[UxRequirementRevisionOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class UxRequirementCriterionDiffEntryOut(BaseModel):
    """One `criterion_key`'s comparison between two Requirement revisions,
    matched by EXACT key equality (§0 invariant 9)."""

    criterion_key: str
    change_kind: UxDiffChangeKind
    from_criterion: Optional[UxAcceptanceCriterionOut] = None
    to_criterion: Optional[UxAcceptanceCriterionOut] = None


class UxRequirementDiffOut(BaseModel):
    system_id: int
    requirement_id: int
    requirement_key: str
    generated_at: float
    diff_state: UxDiffState = "available"
    from_revision_id: Optional[int] = None
    from_revision_number: Optional[int] = None
    to_revision_id: Optional[int] = None
    to_revision_number: Optional[int] = None
    criteria: List[UxRequirementCriterionDiffEntryOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


# --- #407 write requests -----------------------------------------------------
# None of these accept `decision_method` / `decided_by` / `created_by` /
# `authored_by_kind` -- the route derives all four from the `Principal` and
# from which write path was called (§2.10).


class UxJourneyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    journey_key: str
    perspective: UxJourneyPerspective
    baseline_mode: UxJourneyBaselineMode = "undecided"
    baseline_journey_id: Optional[int] = None


class UxJourneyStepInput(BaseModel):
    """One Step nested inside a `UxJourneyRevisionCreateRequest` (§2.10's
    `POST .../revisions` takes the whole ordered Step list per revision --
    Steps are never created independently, matching `ux_journey_step`
    having no create endpoint of its own)."""

    model_config = ConfigDict(extra="forbid")

    step_key: str
    step_order: int
    user_intent: str = ""
    system_response: str = ""
    success_criteria: str = ""
    failure_mode: str = ""
    recovery_path: str = ""
    evidence_expectation: str = ""
    evidence_source_kind: UxEvidenceSourceKind = "none"


class UxJourneyRevisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    beneficiary: str = ""
    usage_context: str = ""
    entry_trigger: str = ""
    value_arrival: str = ""
    summary: str = ""
    change_note: str = ""
    steps: List[UxJourneyStepInput] = Field(default_factory=list)


class UxJourneyUpstreamRefCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_kind: UxRefKind
    target_ref: str
    note: str = ""


class UxRequirementCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_key: str
    requirement_kind: UxRequirementKind


class UxAcceptanceCriterionInput(BaseModel):
    """One Acceptance Criterion nested inside a
    `UxRequirementRevisionCreateRequest` -- same "no independent create
    endpoint" rule as `UxJourneyStepInput`. `out_of_scope` Requirements
    refuse a non-empty list here (422 `out_of_scope_requirement_not_verifiable`,
    §2.4)."""

    model_config = ConfigDict(extra="forbid")

    criterion_key: str
    criterion_order: int
    statement: str = ""
    verification_method: UxVerificationMethod = "manual_review"
    verification_note: str = ""


class UxRequirementRevisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = ""
    rationale: str = ""
    constraint_text: str = ""
    out_of_scope_note: str = ""
    change_note: str = ""
    acceptance_criteria: List[UxAcceptanceCriterionInput] = Field(default_factory=list)


class UxRequirementStepLinkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    journey_key: str
    step_key: str
    note: str = ""


class UxArtifactReferenceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_kind: UxArtifactSubjectKind
    subject_key: str
    artifact_kind: UxArtifactKind
    title: str = ""
    uri: str
    media_type: str = ""
    content_hash: str
    byte_size: Optional[int] = Field(default=None, ge=0)


class UxDesignDecisionCreateRequest(BaseModel):
    """`POST /ux-design/decisions` -- the one write that can move
    `design_status` (§2.5). `captured_digest` lets the caller show what
    content it is judging; the route (422 `ux_design_decision_stale_digest`)
    refuses a mismatch against the subject's CURRENT digest rather than
    silently confirming different content than the developer saw."""

    model_config = ConfigDict(extra="forbid")

    subject_kind: UxDesignSubjectKind
    subject_key: str
    decision: UxDesignDecisionKind
    rationale: str = ""
    captured_digest: str = ""


# ---------------------------------------------------------------------------
# Solution Design (Epic #405, Issue #408). docs/ux-design-lineage.md §3.
# ---------------------------------------------------------------------------


class SolutionDesignOptionOut(BaseModel):
    """One candidate approach (§3.2's `solution_design_option`).
    `option_status` is DERIVED from `solution_design_decision`, never a
    stored column (same discipline as `UxDesignStatus`)."""

    id: int
    solution_design_id: int
    option_key: str
    option_order: int
    title: str = ""
    approach: str = ""
    tradeoffs: str = ""
    risks: str = ""
    content_digest: str
    authored_by_kind: UxDesignAuthorshipKind = "developer"
    decision_method: Literal["manual", "reasoning_llm"] = "manual"
    intelligence_run_id: Optional[int] = None
    option_status: SolutionDesignOptionStatus = "draft"
    created_by: Optional[str] = None
    created_at: float
    revision_state: UxRevisionState = "current"
    superseded_by_id: Optional[int] = None


class SolutionDesignRequirementLinkOut(BaseModel):
    """A many-to-many Solution-Design<->Requirement link (§3.2).
    `link_state` / `stale_reason` are §3.4's read-time axes; the row itself
    never stores them."""

    id: int
    solution_design_id: int
    requirement_id: int
    requirement_key: Optional[str] = None
    captured_requirement_revision_id: Optional[int] = None
    captured_digest: str = ""
    link_state: SolutionLinkState
    stale_reason: Optional[SolutionLinkStaleReason] = None
    note: str = ""
    decision_method: Literal["manual", "reasoning_llm", "deterministic"] = "manual"
    created_by: Optional[str] = None
    created_at: float
    superseded_by_id: Optional[int] = None


class SolutionDesignTargetLinkOut(BaseModel):
    """An Option's link to an existing implementation target (§3.3).
    `review_required` is the finite, derived consequence of `link_state !=
    "current"` (§3.4) -- not a 5th `SolutionLinkState` value."""

    id: int
    solution_design_id: int
    option_id: int
    option_key: Optional[str] = None
    target_kind: SolutionTargetKind
    target_ref: str
    target_row_id: Optional[int] = None
    target_name: Optional[str] = None
    captured_digest: str = ""
    captured_snapshot_id: Optional[int] = None
    link_state: SolutionLinkState
    stale_reason: Optional[SolutionLinkStaleReason] = None
    review_required: bool = False
    note: str = ""
    decision_method: Literal["manual", "reasoning_llm", "deterministic"] = "manual"
    created_by: Optional[str] = None
    created_at: float
    superseded_by_id: Optional[int] = None


class SolutionDesignDecisionOut(BaseModel):
    """One row of the exclusive-choice ledger (§3.2). `decision_method` is
    always `manual` -- adoption is never automatic (§3.6)."""

    id: int
    solution_design_id: int
    option_id: int
    option_key: str
    decision: SolutionDesignOptionDecision
    rationale: str = ""
    captured_digest: str = ""
    decision_method: Literal["manual"] = "manual"
    decided_by: Optional[str] = None
    superseded_by_id: Optional[int] = None
    created_at: float


class SolutionDesignOut(BaseModel):
    """Solution Design summary. No "current option"/status column exists on
    `solution_design` (§3.2) -- `adopted_option_key` here is derived by
    folding the decision ledger."""

    id: int
    system_id: int
    design_key: str
    title: str = ""
    summary: str = ""
    adopted_option_key: Optional[str] = None
    option_count: int = 0
    created_by: Optional[str] = None
    created_at: float
    updated_at: float


class SolutionDesignDetailOut(SolutionDesignOut):
    options: List[SolutionDesignOptionOut] = []
    requirement_links: List[SolutionDesignRequirementLinkOut] = []
    target_links: List[SolutionDesignTargetLinkOut] = []
    decisions: List[SolutionDesignDecisionOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class SolutionDesignListOut(BaseModel):
    system_id: int
    generated_at: float
    designs: List[SolutionDesignOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class SolutionDesignChangeOriginEntryOut(BaseModel):
    """One non-current link classified by WHERE its change originated
    (§3.5) -- a deterministic mapping from the link's own `stale_reason` +
    `ref_kind`/`target_kind`, never a guess or a score."""

    origin: UxChangeOrigin
    link_id: int
    link_kind: Literal["requirement_link", "target_link"]
    target_kind: Optional[SolutionTargetKind] = None
    target_ref: Optional[str] = None
    requirement_key: Optional[str] = None
    stale_reason: Optional[SolutionLinkStaleReason] = None
    detail: str = ""


class SolutionDesignChangeOriginsOut(BaseModel):
    system_id: int
    solution_design_id: int
    design_key: str
    generated_at: float
    origins: List[SolutionDesignChangeOriginEntryOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


class SolutionDesignHandoffUnresolvedRefOut(BaseModel):
    """A named, un-silently-dropped reference the handoff could not resolve
    (§3.7 / #408 acceptance condition 4)."""

    kind: str
    ref: str
    reason: str


class SolutionDesignHandoffOut(BaseModel):
    """`GET /solution-designs/{design_key}/handoff` (§3.7). References are
    resolved at READ time and never copied (`node_design.get_handoff`'s
    discipline); `evaluation_policy_refs` is GROUPED BY LEVEL to keep ADR-7's
    three separate evaluation contracts structurally apart -- never one flat
    list, and never a composite score anywhere on this model."""

    system_id: int
    solution_design_id: int
    design_key: str
    generated_at: float
    handoff_state: SolutionHandoffState
    adopted_option: Optional[SolutionDesignOptionOut] = None
    target_links: List[SolutionDesignTargetLinkOut] = []
    requirements: List[UxRequirementDetailOut] = []
    node_decomposition_refs: List[Dict[str, Any]] = []
    probe_plan_refs: List[Dict[str, Any]] = []
    evaluation_policy_refs: Dict[str, List[Dict[str, Any]]] = {}
    unresolved_references: List[SolutionDesignHandoffUnresolvedRefOut] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}


# --- #408 write requests -----------------------------------------------------
# Same rule as the #407 requests above: no `decision_method` / `decided_by` /
# `created_by` / `authored_by_kind` field anywhere here.


class SolutionDesignCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design_key: str
    title: str = ""
    summary: str = ""


class SolutionDesignOptionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_key: str
    option_order: int
    title: str = ""
    approach: str = ""
    tradeoffs: str = ""
    risks: str = ""


class SolutionDesignRequirementLinkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_key: str
    note: str = ""


class SolutionDesignTargetLinkCreateRequest(BaseModel):
    """`captured_snapshot_id` is required for `target_kind="static_flow"`
    (422 `flow_target_requires_snapshot`, §3.3) -- an entry-point path with
    no pinned snapshot has no stable meaning to capture."""

    model_config = ConfigDict(extra="forbid")

    option_key: str
    target_kind: SolutionTargetKind
    target_ref: str
    captured_snapshot_id: Optional[int] = None
    note: str = ""


class SolutionDesignOptionDecisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_key: str
    decision: SolutionDesignOptionDecision
    rationale: str = ""


# ---------------------------------------------------------------------------
# Evolution Node (Epic #394 Phase 1, Issue #396)
#
# The finite vocabularies below are mirrored from `app/evolution_node.py`,
# which owns them. They are re-declared here as `Literal` aliases rather than
# imported so FastAPI puts a real enum in the OpenAPI schema and the
# Dashboard's TypeScript unions cannot silently drift from the server -- the
# same discipline `PurposeElementKind` and the #351 Brief vocabularies use.
# `test_evolution_node_api.py` asserts the two definitions stay identical.
# ---------------------------------------------------------------------------

EvolutionMaturityState = Literal[
    "exploring", "validating", "established", "monitoring", "reopened", "suspended"
]
EvolutionImplementationModality = Literal[
    "reasoning_llm", "lm_program", "retrieval", "router", "small_model",
    "rule", "deterministic_code", "workflow", "manual", "hybrid",
]
EvolutionLinkKind = Literal[
    "component", "probe_point", "cell_binding", "capability", "flow",
    "purpose_element", "feature",
]
EvolutionSideEffectClass = Literal[
    "pure", "read_only", "local_write", "external_write", "irreversible"
]
EvolutionTrustBoundary = Literal[
    "internal", "external_input", "external_output", "third_party"
]
EvolutionActorKind = Literal["developer", "system"]


class EvolutionNodeCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str
    display_name: str = ""


class EvolutionNodeVersionCreateIn(BaseModel):
    """The Node's CONTRACT -- what it promises, not how it currently keeps
    that promise (ADR-3). `evaluation_policy_refs` are refs, never inline
    criteria: Phase 2 (#397) owns the three evaluation contracts."""

    model_config = ConfigDict(extra="forbid")

    mission: str
    input_contract: Optional[Dict[str, Any]] = None
    output_contract: Optional[Dict[str, Any]] = None
    side_effect_class: EvolutionSideEffectClass
    trust_boundary: EvolutionTrustBoundary
    scope: str = ""
    out_of_scope: str = ""
    establishment_criteria: List[str] = Field(default_factory=list)
    reopen_criteria: List[str] = Field(default_factory=list)
    evaluation_policy_refs: List[str] = Field(default_factory=list)


class EvolutionNodeImplementationCreateIn(BaseModel):
    """How the Node currently keeps its contract's promise (ADR-3).

    Provider/model names belong inside `config` / `provenance` and never in a
    field that participates in the implementation's identity -- the same rule
    #298's Agent Role Card applies to model aliases. `modality` is the axis
    that makes an LLM implementation and a rule implementation of the SAME
    contract comparable.
    """

    model_config = ConfigDict(extra="forbid")

    node_version_id: int
    modality: EvolutionImplementationModality
    config: Optional[Dict[str, Any]] = None
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    environment_ref: Optional[str] = None
    provenance: Optional[Dict[str, Any]] = None


class EvolutionNodeLinkCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_kind: EvolutionLinkKind
    target_ref: str
    target_row_id: Optional[int] = None
    note: str = ""


class EvolutionNodeStablePinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation_id: int
    reason: str = ""


class EvolutionNodeTransitionIn(BaseModel):
    """A maturity transition request.

    Provenance fields (`actor`, `actor_kind`) are deliberately ABSENT: the
    route derives both from the authenticated `Principal` (#337's rule,
    ADR-9), so a caller can never record a transition as someone else's --
    or as the system's -- decision. `decision_method` is deliberately NOT
    defaulted to `manual`: which one it is decides whether a human stands
    behind this transition, and a default would let a caller record a human
    decision by omission. The full three-value Literal is kept so the route
    can refuse `deterministic` with its own finite code
    (`deterministic_via_api_not_allowed`) and let the domain layer refuse
    `reasoning_llm` with `llm_state_not_allowed` -- an LLM never emits a
    canonical state (Principle 6).
    """

    model_config = ConfigDict(extra="forbid")

    to_state: EvolutionMaturityState
    decision_method: Literal["deterministic", "reasoning_llm", "manual"]
    reason: str = ""
    reason_code: str = ""
    evidence_refs: List[str] = Field(default_factory=list)
    idempotency_key: str = ""


class EvolutionNodeSummaryOut(BaseModel):
    id: int
    system_id: int
    node_key: str
    display_name: str
    maturity: EvolutionMaturityState
    current_version_id: Optional[int] = None
    current_implementation_id: Optional[int] = None
    stable_implementation_id: Optional[int] = None
    rollback_implementation_id: Optional[int] = None
    monitoring_contract_ref: Optional[str] = None
    created_at: float
    updated_at: float


class EvolutionNodesListOut(BaseModel):
    nodes: List[EvolutionNodeSummaryOut]


class EvolutionNodeVersionOut(BaseModel):
    id: int
    version_number: int
    mission: str
    scope: str
    out_of_scope: str
    input_contract: Dict[str, Any]
    output_contract: Dict[str, Any]
    side_effect_class: str
    trust_boundary: str
    establishment_criteria: List[str]
    reopen_criteria: List[str]
    evaluation_policy_refs: List[str]
    decision_method: str
    created_by: Optional[str] = None
    created_at: float
    superseded_by_id: Optional[int] = None


class EvolutionNodeImplementationOut(BaseModel):
    id: int
    implementation_number: int
    node_version_id: int
    modality: str
    config: Dict[str, Any]
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    environment_ref: Optional[str] = None
    provenance: Dict[str, Any]
    decision_method: str
    created_by: Optional[str] = None
    created_at: float
    superseded_by_id: Optional[int] = None


class EvolutionNodeLinkOut(BaseModel):
    id: int
    link_kind: str
    target_ref: str
    target_row_id: Optional[int] = None
    note: str
    decision_method: str
    created_by: Optional[str] = None
    created_at: float


class EvolutionNodeEventOut(BaseModel):
    id: int
    event_kind: str
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    from_version_id: Optional[int] = None
    to_version_id: Optional[int] = None
    from_implementation_id: Optional[int] = None
    to_implementation_id: Optional[int] = None
    actor: Optional[str] = None
    actor_kind: str
    decision_method: str
    reason_code: str
    reason: str
    # Named `evidence` (not `evidence_refs`) to match the domain layer's own
    # event document: the stored value is a list of refs, and renaming it at
    # the boundary would leave the API and the projection describing the
    # same column under two names.
    evidence: List[str]
    # NULL for a request that opted out of idempotency entirely. An empty
    # string is deliberately not used: the partial unique index excludes it,
    # so "no key" and "the empty key" are not the same fact.
    idempotency_key: Optional[str] = None
    created_at: float


class EvolutionNodeEventsOut(BaseModel):
    node_id: int
    events: List[EvolutionNodeEventOut]


class EvolutionNodeProjectionOut(BaseModel):
    """The canonical Node document.

    `maturity`, `improvement_status` and `policy_mode` are three INDEPENDENT
    axes and no consumer may combine them into one label (ADR-6). A `null`
    on either of the latter two means "nothing of that kind is linked to
    this Node" -- never "none is in progress". The fourth axis
    (`workflow_phase`) is deliberately absent from the document rather than
    `null`; Phase 6 (#401) wires it.

    `availability[k] is False` means that block could not be read at all.
    Paired with a `null` value it is a different fact from a `null` with
    `availability[k] is True`, which is a genuine absence (#380).

    `maturity` is the stored column; `folded_maturity` is what this Node's
    transition events fold to (ADR-4) and `maturity_consistent` whether the
    two agree. A `null` fold with stored `exploring` is consistent (the Node
    has never transitioned); both are `null` only when
    `availability["maturity_lineage"]` is False.
    """

    schema_version: str
    system_id: int
    node_id: int
    node_key: str
    display_name: str
    maturity: EvolutionMaturityState
    folded_maturity: Optional[EvolutionMaturityState] = None
    maturity_consistent: Optional[bool] = None
    current_version: Optional[EvolutionNodeVersionOut] = None
    current_implementation: Optional[EvolutionNodeImplementationOut] = None
    stable_implementation: Optional[EvolutionNodeImplementationOut] = None
    rollback_implementation: Optional[EvolutionNodeImplementationOut] = None
    links: List[EvolutionNodeLinkOut]
    events: List[EvolutionNodeEventOut]
    improvement_status: Optional[str] = None
    policy_mode: Optional[str] = None
    availability: Dict[str, bool]
    updated_at: float


class EvolutionNodeLegacyProjectionOut(BaseModel):
    """ADR-8 compatibility view. Not a second canonical projection."""

    schema_version: str
    compatibility_projection: bool
    system_id: int
    node_id: int
    node_key: str
    component_id: Optional[str] = None
    probe_point_ref: Optional[str] = None
    cell_id: Optional[str] = None
    maturity: EvolutionMaturityState


class EvolutionNodeTransitionOut(BaseModel):
    """`applied` and `duplicate` are separate booleans on purpose: a retry
    that changed nothing is a success, not a failure, and the caller has to
    be able to tell the two apart. A REJECTED transition never reaches this
    model -- it is a 422 carrying the domain layer's own finite reason code.
    """

    applied: bool
    duplicate: bool
    maturity: EvolutionMaturityState
    event: Optional[EvolutionNodeEventOut] = None


# ---------------------------------------------------------------------------
# Design Studio (Epic #394 Phase 2, Issue #397)
#
# Note what is NOT here: there is no score, weight, or total field on any
# evaluation model. ADR-7 forbids compositing the three levels into one
# number, and the reliable way to enforce that is to give the number nowhere
# to live -- in the schema as well as in the table.
# ---------------------------------------------------------------------------

EvolutionEvaluationLevel = Literal["node", "flow_capability", "ux_outcome"]
DecompositionDecisionKind = Literal["adopted", "held", "rejected"]


class DecompositionProposeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_summary: str
    context: str = ""
    session_id: Optional[int] = None
    capability_ref: str = ""
    flow_ref: str = ""


class DecompositionCandidateOut(BaseModel):
    id: int
    proposal_id: int
    candidate_key: str
    summary: str
    rationale: str
    nodes: List[Dict[str, Any]]
    open_questions: List[str]
    decision: Literal["pending", "adopted", "held", "rejected"]
    decision_note: str
    decided_by: Optional[str] = None
    decided_at: Optional[float] = None
    adopted_node_ids: List[int]
    created_at: float


class DecompositionProposalOut(BaseModel):
    id: int
    system_id: int
    session_id: Optional[int] = None
    scope_summary: str
    capability_ref: str
    flow_ref: str
    snapshot_id: Optional[int] = None
    intelligence_run_id: Optional[int] = None
    status: Literal["proposed", "failed"]
    error_details: str
    is_mock: bool
    created_by: Optional[str] = None
    created_at: float
    candidates: List[DecompositionCandidateOut]


class DecompositionDecisionIn(BaseModel):
    """`pending` is deliberately not accepted: it is the initial state, not a
    decision a developer can record."""

    model_config = ConfigDict(extra="forbid")

    decision: DecompositionDecisionKind
    note: str = ""


class DecompositionDecisionOut(BaseModel):
    candidate: DecompositionCandidateOut
    created_nodes: List[Dict[str, Any]]


class EvaluationCriterionIn(BaseModel):
    """One thing that must be REACHED before establishing.

    Separate from a floor on purpose (ADR-7): the two are consumed at
    different moments, and a single list with a flag makes "we met the bar"
    and "we did not regress" indistinguishable in storage."""

    model_config = ConfigDict(extra="forbid")

    name: str
    measure: str
    target: str = ""
    note: str = ""


class EvaluationFloorIn(BaseModel):
    """One property that must not REGRESS. Never traded off against a
    criterion -- there is no weight field to trade with."""

    model_config = ConfigDict(extra="forbid")

    name: str
    measure: str
    minimum: str = ""
    note: str = ""


class EvaluationUnmeasuredIn(BaseModel):
    """Something this contract cannot currently measure, WITH its reason.

    Recorded rather than omitted: an omitted criterion reads as "nothing to
    check here", which is the #391 rule about never inferring an Outcome."""

    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str


class EvaluationPolicyCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_key: str
    level: EvolutionEvaluationLevel
    title: str = ""
    subject_ref: str = ""
    criteria: List[EvaluationCriterionIn] = Field(default_factory=list)
    floors: List[EvaluationFloorIn] = Field(default_factory=list)
    unmeasured: List[EvaluationUnmeasuredIn] = Field(default_factory=list)


class EvaluationPolicyOut(BaseModel):
    id: int
    policy_key: str
    level: EvolutionEvaluationLevel
    version_number: int
    title: str
    subject_ref: str
    criteria: List[Dict[str, Any]]
    floors: List[Dict[str, Any]]
    unmeasured: List[Dict[str, Any]]
    decision_method: str
    created_by: Optional[str] = None
    created_at: float


class EvaluationPoliciesOut(BaseModel):
    """Grouped by level, never merged into one list -- the ADR-7 separation
    made structural rather than only documented."""

    node: List[EvaluationPolicyOut]
    flow_capability: List[EvaluationPolicyOut]
    ux_outcome: List[EvaluationPolicyOut]


class NodeLineageRelationOut(BaseModel):
    """One design relation.

    `relation_status` (was this relation proposed by AI or confirmed by the
    developer), `element_state` (is the target itself confirmed) and
    `target_resolution` (does the target exist in its own canonical source)
    are three independent axes. A confirmed relation to an unconfirmed
    element is a real and common state, and so is a confirmed relation to a
    ref that resolves to nothing; collapsing any two of them would hide one.

    `target_source` names WHICH canonical source decided the resolution --
    the Purpose Frame for `purpose_element`, the #312 Capability Graph for
    `capability`, the System's observed flow ids for `flow`, the Feature Map
    for `feature`. The finite vocabularies live in `app/node_design.py`
    (`TARGET_RESOLUTIONS` / `TARGET_SOURCES` / `LINEAGE_ELEMENT_STATES`)."""

    link_id: int
    link_kind: str
    target_ref: str
    target_name: Optional[str] = None
    relation_status: Optional[str] = None
    relation_decision_method: str
    element_state: str
    target_resolution: str = "unresolved"
    target_source: Optional[str] = None
    note: str
    created_by: Optional[str] = None
    created_at: float


class NodeLineageOut(BaseModel):
    system_id: int
    node_id: int
    node_key: str
    maturity: str
    relations: List[NodeLineageRelationOut]
    confirmed_relation_count: int
    proposed_relation_count: int
    purpose_frame_supplied: bool


class NodeDesignHandoffCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: List[int]
    evaluation_policy_ids: List[int] = Field(default_factory=list)
    dataset_refs: List[str] = Field(default_factory=list)
    probe_plan_id: Optional[int] = None
    establishment_criteria_draft: List[str] = Field(default_factory=list)
    reopen_criteria_draft: List[str] = Field(default_factory=list)
    exploration_brief: str = ""


class NodeDesignHandoffOut(BaseModel):
    """References are resolved at READ time, so a Node deleted or a policy
    superseded after assembly shows up as it actually is now."""

    id: int
    system_id: int
    session_id: Optional[int] = None
    nodes: List[Dict[str, Any]]
    evaluation_policies: List[Dict[str, Any]]
    dataset_refs: List[str]
    probe_plan_id: Optional[int] = None
    establishment_criteria_draft: List[str]
    reopen_criteria_draft: List[str]
    exploration_brief: str
    assembly_state: Literal["complete", "incomplete"]
    missing_refs: List[str]
    created_by: Optional[str] = None
    created_at: float


# ---------------------------------------------------------------------------
# Exploration Workbench (Epic #394 Phase 3, Issue #398)
#
# Note what is absent, deliberately: no source, patch, or command field on any
# variant model. A variant references an implementation and the existing run
# that executed it. Accepting executable content at this boundary would let a
# caller run code outside the pinned-snapshot, network-off sandbox that Replay
# and Experiments enforce (Principle 8).
#
# Also absent: any score, weight, or total. #398 forbids compositing quality /
# latency / cost / safety, and the reliable enforcement is to give the
# combined number nowhere to live -- in the schema as well as in the table.
# ---------------------------------------------------------------------------

ExplorationDimension = Literal[
    "output_quality", "error_rate", "latency", "cost", "resource", "safety", "coverage"
]
ExplorationValueState = Literal[
    "measured", "not_applicable", "not_measured", "unsupported"
]
ExplorationExecutionState = Literal[
    "not_executed", "executed", "not_executable", "unsupported"
]
ExplorationRefKind = Literal["replay_run", "replay_variant", "experiment"]
ExplorationGenerator = Literal["manual", "reasoning_llm", "existing_implementation"]
ExplorationDatasetKind = Literal["replay_set", "golden_set", "edge_cases", "mixed"]
ExplorationVerdict = Literal[
    "better", "worse", "equal", "incomparable", "coverage_mismatch"
]


class ExplorationRunCreateIn(BaseModel):
    """Everything held CONSTANT across the run's variants lives here, so two
    variants cannot have been measured against different datasets while still
    looking like a comparison."""

    model_config = ConfigDict(extra="forbid")

    node_id: int
    node_version_id: int
    objective: str = ""
    dataset_kind: ExplorationDatasetKind = "replay_set"
    dataset_ref: str = ""
    snapshot_id: Optional[int] = None
    commit_sha: str = ""
    environment_ref: str = ""
    evaluation_policy_ids: List[int] = Field(default_factory=list)
    handoff_id: Optional[int] = None


class ExplorationVariantCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_key: str
    modality: EvolutionImplementationModality
    label: str = ""
    is_baseline: bool = False
    implementation_id: Optional[int] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    generator: ExplorationGenerator = "manual"
    applicability_envelope: Dict[str, Any] = Field(default_factory=dict)


class ExplorationExecutionIn(BaseModel):
    """`not_executable` and `unsupported` are first-class outcomes here, not
    failures: a rule variant that cannot express a case has not lost on it."""

    model_config = ConfigDict(extra="forbid")

    execution_state: ExplorationExecutionState
    execution_ref_kind: Optional[ExplorationRefKind] = None
    execution_ref_id: Optional[int] = None
    note: str = ""


class ExplorationMeasurementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: ExplorationDimension
    metric_name: str = ""
    value_state: ExplorationValueState = "measured"
    numeric_value: Optional[float] = None
    unit: str = ""
    covered_case_count: Optional[int] = None
    total_case_count: Optional[int] = None
    source: Literal["deterministic", "reasoning_llm", "manual"] = "deterministic"
    note: str = ""


class ExplorationMeasurementOut(BaseModel):
    id: int
    dimension: ExplorationDimension
    metric_name: str
    value_state: ExplorationValueState
    numeric_value: Optional[float] = None
    unit: str
    covered_case_count: Optional[int] = None
    total_case_count: Optional[int] = None
    source: str
    note: str


class ExplorationVariantOut(BaseModel):
    id: int
    variant_key: str
    label: str
    is_baseline: bool
    modality: EvolutionImplementationModality
    implementation_id: Optional[int] = None
    config: Dict[str, Any]
    provenance: Dict[str, Any]
    generator: ExplorationGenerator
    applicability_envelope: Dict[str, Any]
    execution_state: ExplorationExecutionState
    execution_ref_kind: Optional[ExplorationRefKind] = None
    execution_ref_id: Optional[int] = None
    execution_note: str
    created_by: Optional[str] = None
    created_at: float
    measurements: List[ExplorationMeasurementOut]


class ExplorationComparisonOut(BaseModel):
    """One dimension of one variant against the baseline.

    `incomparable` and `coverage_mismatch` are verdicts, not errors. They are
    what stops "this variant has no token cost" from displaying identically to
    "this variant's token cost is zero"."""

    dimension: ExplorationDimension
    metric_name: str
    verdict: ExplorationVerdict
    baseline_state: ExplorationValueState
    variant_state: ExplorationValueState
    baseline_value: Optional[float] = None
    variant_value: Optional[float] = None
    delta: Optional[float] = None
    baseline_coverage: Optional[List[int]] = None
    variant_coverage: Optional[List[int]] = None
    reason: str


class ExplorationRunOut(BaseModel):
    """The whole comparison. Carries no ranking and no overall verdict --
    which dimension matters is the developer's judgement, and at
    establishment time it is #399's gate, not this projection's."""

    id: int
    system_id: int
    node_id: int
    node_version_id: int
    handoff_id: Optional[int] = None
    objective: str
    dataset_kind: ExplorationDatasetKind
    dataset_ref: str
    snapshot_id: Optional[int] = None
    commit_sha: str
    environment_ref: str
    evaluation_policy_ids: List[int]
    status: Literal["open", "completed", "abandoned"]
    conclusion_note: str
    baseline_variant_id: Optional[int] = None
    comparable: bool
    variants: List[ExplorationVariantOut]
    comparisons: Dict[str, List[ExplorationComparisonOut]]
    created_by: Optional[str] = None
    created_at: float
    completed_at: Optional[float] = None


class ExplorationRunCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion_note: str = ""


class ExplorationRankingEntryOut(BaseModel):
    variant_id: int
    variant_key: str
    modality: EvolutionImplementationModality
    value: Optional[float] = None
    value_state: ExplorationValueState
    # Why an unranked variant is unranked (different metric_name, different
    # coverage, nothing measured). Dropping this would leave the reader with
    # a bare exclusion they cannot audit.
    reason: str = ""


class ExplorationRankingOut(BaseModel):
    """Ranked by ONE named dimension. There is no overall ranking endpoint:
    a caller that wants an order must say what it is ordering by, so a latency
    ranking can never be presented as "the best variant".

    `unranked` is a separate group rather than the tail of `ranked` --
    sorting an unmeasured variant last would read as "worst"."""

    dimension: ExplorationDimension
    # The single metric this ranking was computed over -- readings under a
    # different metric_name are in `unranked`, never silently mixed in.
    metric_name: str = ""
    ranked: List[ExplorationRankingEntryOut]
    unranked: List[ExplorationRankingEntryOut]


# ---------------------------------------------------------------------------
# Stabilization Evidence Package (Epic #394 Phase 4, Issue #399)
#
# `approved_by` is never a request field: establishment is a named human's
# decision, taken from the authenticated principal at the route (#337's
# provenance rule). And as in Phase 3, there is no score anywhere -- every
# criterion and floor is judged individually (ADR-7).
# ---------------------------------------------------------------------------

StabilizationEvidenceLevel = Literal["node", "flow_capability", "ux_outcome"]
StabilizationEvidenceKind = Literal[
    "criterion", "floor", "downstream_impact", "outcome", "stability"
]
StabilizationVerdict = Literal[
    "met", "not_met", "held", "violated", "unmeasured", "not_applicable"
]
StabilizationRefKind = Literal[
    "exploration_run", "exploration_variant", "replay_run", "experiment",
    "evaluation_policy",
]
StabilizationStatus = Literal[
    "draft", "under_review", "approved", "rejected", "superseded"
]
StabilizationParentReviewDisposition = Literal["endorsed", "declined"]


class StabilizationPackageCreateIn(BaseModel):
    """The node version, baseline and rollback target are deliberately NOT
    accepted here -- they are read from the Node's own current state, because
    letting a caller assert them would let a package claim a rollback target
    the Node does not have."""

    model_config = ConfigDict(extra="forbid")

    node_id: int
    candidate_implementation_id: int
    exploration_run_id: Optional[int] = None
    applicability_envelope: Dict[str, Any] = Field(default_factory=dict)
    known_limitations: List[str] = Field(default_factory=list)
    residual_risks: List[str] = Field(default_factory=list)
    required_case_count: int = 0
    stability_window_seconds: float = 0.0
    observed_case_count: Optional[int] = None
    observed_window_seconds: Optional[float] = None
    outcome_unmeasured_reason: str = ""
    rollback_plan: str = ""


class StabilizationEvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_level: StabilizationEvidenceLevel
    evidence_kind: StabilizationEvidenceKind
    name: str
    verdict: StabilizationVerdict
    ref_kind: Optional[StabilizationRefKind] = None
    ref_id: Optional[int] = None
    evaluation_policy_id: Optional[int] = None
    detail: str = ""
    is_mock: bool = False
    source: Literal["deterministic", "reasoning_llm", "manual"] = "deterministic"


class StabilizationEvidenceOut(BaseModel):
    id: int
    evidence_kind: StabilizationEvidenceKind
    name: str
    verdict: StabilizationVerdict
    ref_kind: Optional[StabilizationRefKind] = None
    ref_id: Optional[int] = None
    evaluation_policy_id: Optional[int] = None
    detail: str
    is_mock: bool
    source: str


class StabilizationGateOut(BaseModel):
    """Recomputed on every read, never stored: a stored verdict drifts from
    the evidence it describes."""

    allowed: bool
    reason_code: str
    message: str
    failing_evidence: List[str]


class StabilizationPackageOut(BaseModel):
    id: int
    system_id: int
    node_id: int
    node_version_id: int
    candidate_implementation_id: int
    baseline_implementation_id: Optional[int] = None
    rollback_implementation_id: Optional[int] = None
    rollback_plan: str
    exploration_run_id: Optional[int] = None
    applicability_envelope: Dict[str, Any]
    known_limitations: List[str]
    residual_risks: List[str]
    required_case_count: int
    observed_case_count: Optional[int] = None
    stability_window_seconds: float
    observed_window_seconds: Optional[float] = None
    outcome_unmeasured_reason: str
    status: StabilizationStatus
    # Which package to establish from instead, when status='superseded'.
    superseded_by_id: Optional[int] = None
    # The parent review and the human approval are two separate records with
    # their own who/when (#304). A NULL disposition means no parent has
    # reviewed the package yet -- never that they had nothing to say.
    parent_reviewed_by: Optional[str] = None
    parent_reviewed_at: Optional[float] = None
    parent_review_disposition: Optional[StabilizationParentReviewDisposition] = None
    parent_review_note: str = ""
    approved_by: Optional[str] = None
    approved_at: Optional[float] = None
    decision_note: str
    # Grouped by level, never merged: a Node-level win is not evidence that
    # the Flow it sits in improved (ADR-7).
    evidence: Dict[str, List[StabilizationEvidenceOut]]
    gate: StabilizationGateOut
    created_by: Optional[str] = None
    created_at: float


class StabilizationDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""


class StabilizationParentReviewIn(BaseModel):
    """The parent's own record, distinct from the human approval (#304).

    The disposition and the note are the caller's assertions; WHO reviewed
    comes from the authenticated principal, never the body (#337). There is
    deliberately no way to withdraw or overwrite one: a changed mind
    supersedes the package, so both judgements stay readable."""

    model_config = ConfigDict(extra="forbid")

    disposition: StabilizationParentReviewDisposition
    note: str = ""


class StabilizationSupersedeIn(BaseModel):
    """The successor package id is the one assertion the caller makes; who
    decided comes from the authenticated principal, never the body (#337)."""

    model_config = ConfigDict(extra="forbid")

    successor_package_id: int
    note: str = ""


# ---------------------------------------------------------------------------
# Operations: monitoring, drift and local reopen
# (Epic #394 Phase 5, Issue #400)
#
# The finite vocabularies are mirrored from `app/node_operations.py`, which
# owns them, for the same reason the Phase 1 aliases above are: FastAPI then
# puts a real enum in the OpenAPI schema instead of a bare string, so a
# Dashboard union cannot silently drift from the server. `_check_membership`
# in the domain layer stays the authority; `test_node_operations_api.py`
# asserts the two definitions have not diverged.
#
# ADR-5's separation is visible in `NodeOperationsProjectionOut`: `maturity`
# and `observation` are two independent readings and are never merged into one
# label. An `established` Node whose telemetry stopped reads as exactly that.
#
# `approved_by` is never a request field. Approving a reopen is a named
# human's decision and the name comes from the authenticated principal at the
# route (#337's provenance rule, ADR-9).
# ---------------------------------------------------------------------------

OperationsIndicatorKind = Literal[
    "input_distribution", "output_quality", "error_rate", "latency", "cost",
    "flow_success", "outcome", "human_correction", "compatibility",
]
OperationsObservationState = Literal[
    "within_budget", "drift_detected", "insufficient_sample", "unobserved"
]
OperationsAnomalyClassification = Literal[
    "implementation_defect",
    "input_or_environment_drift",
    "upstream_downstream_mismatch",
    "evaluation_gap",
    "new_use_case_signal",
    "purpose_or_vision_reconsideration",
    "unknown",
]
OperationsAnomalySeverity = Literal["blocking", "attention", "informational"]
OperationsReopenStatus = Literal["proposed", "approved", "rejected", "completed"]
OperationsNotificationKind = Literal[
    "anomaly_detected", "reopen_approved", "handoff_ready", "handoff_blocked"
]
OperationsHandoffStage = Literal[
    "awaiting_replay", "awaiting_offline_shadow",
    "awaiting_live_shadow_approval", "ready",
]
OperationsHandoffEvidenceKind = Literal[
    "replay_run", "offline_shadow_result", "live_shadow_approval"
]


class NodeMonitoringIndicatorIn(BaseModel):
    """One indicator this contract watches.

    Only `kind` is a domain-validated finite value; the rest describes the
    reading in the contract author's own terms. Thresholds live per contract,
    never as a global constant -- a number invented centrally would be applied
    to Nodes nobody looked at.
    """

    model_config = ConfigDict(extra="forbid")

    kind: OperationsIndicatorKind
    name: str = ""
    reference_value: Optional[float] = None
    threshold: Optional[float] = None
    note: str = ""


class NodeMonitoringContractCreateIn(BaseModel):
    """`node_id` comes from the path and `created_by` from the principal."""

    model_config = ConfigDict(extra="forbid")

    observed_environment_ref: str = ""
    deployed_commit_sha: str = ""
    sampling_note: str = ""
    # How long observation may be silent before the Node reads as `unobserved`
    # rather than healthy. Silence is never treated as health.
    freshness_budget_seconds: float = 0.0
    minimum_sample_count: int = 0
    indicators: List[NodeMonitoringIndicatorIn] = Field(default_factory=list)
    reopen_conditions: List[str] = Field(default_factory=list)
    escalation_owner: str = ""


class NodeMonitoringContractOut(BaseModel):
    id: int
    system_id: int
    node_id: int
    version_number: int
    observed_environment_ref: str
    deployed_commit_sha: str
    sampling_note: str
    freshness_budget_seconds: float
    minimum_sample_count: int
    indicators: List[Dict[str, Any]]
    reopen_conditions: List[str]
    escalation_owner: str
    active: bool
    decision_method: str
    created_by: Optional[str] = None
    created_at: float
    # Which contract replaced this one; NULL means this is the current version.
    superseded_by_id: Optional[int] = None


class NodeDriftObservationIn(BaseModel):
    """ONE deterministic reading. No interpretation belongs here -- that is an
    anomaly, and the two are separate records on purpose."""

    model_config = ConfigDict(extra="forbid")

    indicator: str
    indicator_kind: OperationsIndicatorKind
    observation_state: OperationsObservationState
    observed_value: Optional[float] = None
    reference_value: Optional[float] = None
    sample_count: Optional[int] = None
    window_seconds: Optional[float] = None
    last_observed_at: Optional[float] = None
    detail: str = ""


class NodeDriftObservationOut(BaseModel):
    id: int
    system_id: int
    node_id: int
    contract_id: int
    indicator: str
    indicator_kind: OperationsIndicatorKind
    observation_state: OperationsObservationState
    observed_value: Optional[float] = None
    reference_value: Optional[float] = None
    sample_count: Optional[int] = None
    window_seconds: Optional[float] = None
    last_observed_at: Optional[float] = None
    detail: str
    created_at: float


class NodeAnomalyRecordIn(BaseModel):
    """`decision_method` is deliberately absent.

    It records WHICH PATH produced the classification, so it cannot be
    claimed by a request body (#337): an HTTP caller is a human-driven client
    and the route records `manual`. A reasoning-model classification is
    produced by server-side code, which calls the domain layer directly and
    carries its own `intelligence_runs` provenance.

    `classification_error` may only accompany `classification='unknown'`; the
    domain layer refuses the pairing otherwise, because a specific
    classification recorded alongside a failure is exactly the heuristic
    fallback Principle 6 forbids.
    """

    model_config = ConfigDict(extra="forbid")

    classification: OperationsAnomalyClassification
    summary: str = ""
    severity: OperationsAnomalySeverity = "attention"
    contract_id: Optional[int] = None
    observation_ids: List[int] = Field(default_factory=list)
    classification_error: str = ""
    # What stops one continuing condition producing a new anomaly -- and then
    # a new reopen -- on every polling cycle.
    dedupe_key: str = ""


class NodeAnomalyOut(BaseModel):
    id: int
    system_id: int
    node_id: int
    contract_id: Optional[int] = None
    classification: OperationsAnomalyClassification
    severity: OperationsAnomalySeverity
    summary: str
    observation_ids: List[int]
    decision_method: str
    intelligence_run_id: Optional[int] = None
    classification_error: str
    dedupe_key: str
    # `open | acknowledged | resolved | superseded` -- owned by the table's
    # CHECK constraint, not by `app/node_operations.py`, so it is not mirrored
    # as a Literal here (there is no domain constant to keep it honest).
    status: str
    # Whether this says the design was aimed at the wrong thing, as opposed to
    # the implementation being wrong. The next action differs completely.
    frame_breaking: bool
    created_at: float
    resolved_at: Optional[float] = None


class NodeAnomalyRecordOut(BaseModel):
    """`created=False` means an equal, still-open anomaly already existed and
    nothing changed -- a 200, never a conflict: a repeated observation of one
    continuing condition is a normal poll, not an error."""

    anomaly: NodeAnomalyOut
    created: bool


class NodeReopenScopeRationaleOut(BaseModel):
    node_id: int
    reason: str
    # The concrete link targets shared with the origin -- a structural fact,
    # never a similarity judgement (Principle 6).
    shared: List[str] = Field(default_factory=list)


class NodeReopenScopeOut(BaseModel):
    origin_node_id: int
    scope_node_ids: List[int]
    rationale: List[NodeReopenScopeRationaleOut]
    # Every other Node in the System, listed rather than silently omitted: the
    # only way a reader can check that unrelated Nodes were left out.
    excluded_node_ids: List[int]


class NodeReopenPlanCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    anomaly_id: Optional[int] = None
    budget_note: str = ""
    include_neighbours: bool = True


class NodeReopenPlanOut(BaseModel):
    id: int
    system_id: int
    origin_node_id: int
    anomaly_id: Optional[int] = None
    scope_node_ids: List[int]
    scope_rationale: List[NodeReopenScopeRationaleOut]
    excluded_node_ids: List[int]
    reason: str
    budget_note: str
    # ADR-5's promise that production keeps running the established
    # implementation during re-exploration, asserted explicitly rather than
    # assumed.
    stable_implementation_retained: bool
    status: OperationsReopenStatus
    decision_method: str
    approved_by: Optional[str] = None
    approved_at: Optional[float] = None
    decision_note: str
    created_by: Optional[str] = None
    created_at: float


class NodeReopenDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""


class NodeReopenTransitionResultOut(BaseModel):
    """One in-scope Node's outcome. A Node that cannot legally transition is
    REPORTED with its refusal code, never forced and never silently dropped --
    a bulk action that ignores a gate is a gate that does not exist."""

    node_id: int
    applied: bool
    duplicate: bool
    reason_code: str
    message: str


class NodeReopenApprovalOut(BaseModel):
    plan: NodeReopenPlanOut
    results: List[NodeReopenTransitionResultOut]


class NodeNotificationEmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_kind: OperationsNotificationKind
    recipient: str = ""
    summary: str = ""
    dedupe_key: str
    anomaly_id: Optional[int] = None
    reopen_plan_id: Optional[int] = None
    cooldown_seconds: float = 3600.0


class NodeNotificationAcknowledgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""


class NodeNotificationOut(BaseModel):
    id: int
    system_id: int
    node_id: int
    anomaly_id: Optional[int] = None
    reopen_plan_id: Optional[int] = None
    notification_kind: OperationsNotificationKind
    recipient: str
    summary: str
    dedupe_key: str
    status: str
    cooldown_seconds: float
    last_emitted_at: float
    cooldown_until: float
    occurrence_count: int
    suppressed_count: int
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[float] = None
    created_at: float
    updated_at: float


class NodeNotificationEmitOut(BaseModel):
    notification: NodeNotificationOut
    queued: bool
    suppressed: bool


class NodeReopenHandoffCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: int


class NodeReopenHandoffAdvanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_kind: OperationsHandoffEvidenceKind
    evidence_id: int


class NodeReopenHandoffOut(BaseModel):
    id: int
    system_id: int
    reopen_plan_id: int
    node_id: int
    stage: OperationsHandoffStage
    replay_run_id: Optional[int] = None
    offline_shadow_result_id: Optional[int] = None
    live_shadow_approval_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: float
    updated_at: float


class NodeReopenHandoffCreateOut(BaseModel):
    handoff: NodeReopenHandoffOut
    created: bool


class NodeObservationHealthOut(BaseModel):
    """`unobserved` and `insufficient_sample` are their own states and are
    never rolled into "within budget" -- silence is not health."""

    state: OperationsObservationState
    reason: str
    elapsed_seconds: Optional[float] = None
    sample_count: Optional[int] = None


class NodeDriftObservationSummaryOut(BaseModel):
    id: int
    indicator: str
    indicator_kind: OperationsIndicatorKind
    observation_state: OperationsObservationState
    observed_value: Optional[float] = None
    reference_value: Optional[float] = None
    sample_count: Optional[int] = None
    last_observed_at: Optional[float] = None
    detail: str


class NodeAnomalySummaryOut(BaseModel):
    id: int
    classification: OperationsAnomalyClassification
    severity: OperationsAnomalySeverity
    summary: str
    status: str
    decision_method: str
    classification_error: str
    frame_breaking: bool
    created_at: float


class NodeOperationsProjectionOut(BaseModel):
    """One Node's operational picture.

    `maturity` and `observation` are two independent readings and are never
    merged (ADR-5): an `established` Node with dead telemetry reads as
    `maturity='established'` alongside `observation.state='unobserved'`, which
    is precisely the state Phase 5 exists to make visible.
    """

    system_id: int
    node_id: int
    node_key: str
    maturity: EvolutionMaturityState
    # `null` means no contract is wired, never "monitoring failed" -- the two
    # are different answers.
    observation: Optional[NodeObservationHealthOut] = None
    monitoring_contract_id: Optional[int] = None
    monitoring_contract_declared: bool
    observations: List[NodeDriftObservationSummaryOut]
    anomalies: List[NodeAnomalySummaryOut]


# ---------------------------------------------------------------------------
# Execution modes (Epic #412, Issue #413)
#
# Canonical contract: `docs/execution-modes.md`; the domain layer is
# `app/execution_mode.py`, which mirrors every alias below with `get_args`.
# The `Literal`s live here so FastAPI puts a real enum in the OpenAPI schema
# instead of a bare string -- a Dashboard union then cannot silently drift
# from the server (the same reason the Phase 1/Phase 5 aliases above are here).
#
# The execution mode is the FIFTH independent axis (#394 ADR-6). It is never
# derived from, and never merged into, `evolution_node.maturity`,
# `cell_improvement`, the SDK policy `components.mode`, or the Dashboard
# workflow phase. In particular the SDK policy `shadow` and the execution
# mode `shadow` are two different facts and are returned as separate fields.
#
# `actor` is never a request field on any of these models. Changing an
# execution mode is a human decision and the name comes from the
# authenticated principal at the route (#337's provenance rule); the
# `decision_method` is likewise fixed to `manual` server-side.
# ---------------------------------------------------------------------------

ExecutionMode = Literal["fixed", "observe", "propose", "shadow"]
ExecutionModeScopeKind = Literal["system", "flow", "node"]
ExecutionModeScopeState = Literal[
    "unset", "revoked", "pending", "expired", "invalid", "active", "conflicting"
]
ExecutionCapability = Literal[
    "observation_record",
    "llm_experiment_proposal",
    "candidate_execution",
    "shadow_comparison",
]
#: The ten reason codes of the ten-row resolution table of §3.3, one per row.
#: The elapsed-window rows (3, 5, 8) carry a SEPARATE code per scope rather
#: than one shared `expired_assignment`: all three clamp to `fixed`, but the
#: developer's next action differs -- re-assign the Node, the Flow, or the
#: System -- and a single code would make the reader walk `scope_trace` to
#: find out which. One displayed word, one fact (#366).
ExecutionModeReason = Literal[
    "conflicting_assignments",
    "invalid_mode_value",
    "flow_scope_not_member",
    "node_expired_assignment",
    "node_assignment",
    "flow_expired_assignment",
    "flow_scope_conflict",
    "flow_assignment",
    "system_expired_assignment",
    "system_assignment",
    "no_assignment",
]
#: `default` is the fail-closed `fixed` nobody chose; `system` with reason
#: `system_assignment` is a human who chose it. Two different facts (§4.4).
ExecutionModeSourceScope = Literal["node", "flow", "system", "none", "default"]
ExecutionModeDenialCode = Literal[
    "capability_not_permitted",
    "conflicting_assignments",
    "invalid_mode_value",
    "flow_scope_not_member",
    "node_expired_assignment",
    "flow_expired_assignment",
    "system_expired_assignment",
    "flow_scope_conflict",
    "unknown_capability",
    "node_not_found",
]
ExecutionModeDivergence = Literal["match", "divergent", "unobserved", "stale"]
ExecutionModeRecordKind = Literal["assign", "revoke"]
#: WHICH PATH produced an observation, decided by the route, never by a
#: request body (#337). `sdk` is an SDK-attested reading and no current path
#: can attest one, so it is unreachable over HTTP: `ExecutionModeObservationIn`
#: has no `source` field and the route always writes `control_server`. The
#: value stays in the vocabulary because the column already holds it and an
#: attested path would need it -- not because a caller may select it.
ExecutionModeObservationSource = Literal["control_server", "sdk"]
#: The standing of an observation's `run_ref`. Derived, never stored: nothing
#: on this path resolves the pointer against a canonical execution row, so
#: there is no `resolved` value to report. "This row cites a run" and "this
#: row's citation was checked" must stay distinguishable (#366 / Principle 7).
ExecutionModeRunRefState = Literal["absent", "uncorroborated", "corroborated"]


class ExecutionModeScopeReadingOut(BaseModel):
    """One scope's contribution to the decision.

    `state` and `mode` are separate: "a row says `shadow` but its window
    elapsed" and "there is no row" are different facts.
    """

    scope_kind: ExecutionModeScopeKind
    scope_ref: str
    state: ExecutionModeScopeState
    mode: Optional[ExecutionMode] = None
    assignment_id: Optional[int] = None
    effective_from: Optional[float] = None
    effective_until: Optional[float] = None
    open_row_count: int = 0


class ExecutionModeDecisionOut(BaseModel):
    """The resolved mode plus the trace that explains it.

    `scope_trace` lists every scope consulted, in resolution order, so a
    reader can see which setting won and which were passed over.
    """

    mode: ExecutionMode
    source_scope: ExecutionModeSourceScope
    source_ref: str
    reason: ExecutionModeReason
    permitted_capabilities: List[ExecutionCapability]
    scope_trace: List[ExecutionModeScopeReadingOut]


class ExecutionModeAssignmentOut(BaseModel):
    id: int
    system_id: int
    record_kind: ExecutionModeRecordKind
    scope_kind: ExecutionModeScopeKind
    scope_ref: str
    # NULL on a `revoke` row: a revocation ends an assignment, it does not
    # name a new mode.
    mode: Optional[ExecutionMode] = None
    # The effective mode resolved at write time, so the audit can be read
    # without recomputing "what changed" (#337).
    previous_mode: Optional[ExecutionMode] = None
    effective_from: Optional[float] = None
    effective_until: Optional[float] = None
    reason: str
    actor_kind: str
    actor: Optional[str] = None
    decision_method: str
    supersedes_id: Optional[int] = None
    superseded_by_id: Optional[int] = None
    schema_version: str
    created_at: float
    # Only present on the projection's current rows.
    scope_state: Optional[ExecutionModeScopeState] = None


class ExecutionModeAssignIn(BaseModel):
    """`actor` and `decision_method` are deliberately absent (see above)."""

    model_config = ConfigDict(extra="forbid")

    scope_kind: ExecutionModeScopeKind
    # Empty for the `system` scope. A `flow` ref may be given bare or as
    # `runtime_flow:<flow_id>`; it is always STORED prefixed (§2.1).
    scope_ref: str = ""
    mode: ExecutionMode
    # Required: an unexplained permission change cannot be reviewed (§5.1).
    reason: str
    effective_from: Optional[float] = None
    # NULL means "until a human ends it". When set, the window elapsing
    # clamps the mode to `fixed` rather than falling through to a broader
    # scope -- otherwise the deadline would stop nothing (EM-ADR-2).
    effective_until: Optional[float] = None


class ExecutionModeRevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class ExecutionModeObservationIn(BaseModel):
    """What mode a Node was ACTUALLY run under.

    `source` is deliberately absent. It used to be a request field, so any
    caller could send `source: "sdk"` and manufacture an SDK-attested reading
    -- and an observation exists precisely to expose a configured-vs-runtime
    disagreement, which a self-reported reading defeats. Provenance comes from
    the route (#337): the endpoint writes `control_server` unconditionally,
    and `extra="forbid"` makes an attempt to supply one a 422 rather than a
    silently ignored field.

    `run_ref` is accepted but NOT resolved against anything, so the response
    reports it as `uncorroborated`. It is the caller's pointer, not evidence.
    """

    model_config = ConfigDict(extra="forbid")

    node_key: str
    observed_mode: ExecutionMode
    capability: Optional[ExecutionCapability] = None
    run_ref: Optional[str] = None
    detail: str = ""


class ExecutionModeObservationOut(BaseModel):
    """One recorded observation, with the standing of its own provenance said
    out loud: `source` is the path that wrote it (an HTTP write is always
    `control_server`), and `run_ref_state` says whether the pointer was checked."""

    id: int
    system_id: int
    node_key: str
    observed_mode: ExecutionMode
    capability: Optional[ExecutionCapability] = None
    run_ref: Optional[str] = None
    #: `absent`, caller-reported `uncorroborated`, or gate-attested `corroborated`.
    run_ref_state: ExecutionModeRunRefState = "absent"
    source: ExecutionModeObservationSource
    detail: str
    recorded_at: float


class ExecutionModeDivergenceOut(BaseModel):
    """`unobserved` is never reported as `match`: not having looked is not a
    success (#380).

    `observation_source` / `run_ref_state` are a SEPARATE axis from
    `divergence`. The first answers "does the reading agree with the
    configuration?", the second "was the reading measured?" -- and no current
    path attests a runtime mode, so a `match` can be agreement with a value a
    human reported. One displayed word must not carry both facts (#366).
    Both are `null` for `unobserved`, where there is no observation.
    """

    node_key: str
    divergence: ExecutionModeDivergence
    effective_mode: ExecutionMode
    observed_mode: Optional[ExecutionMode] = None
    observed_at: Optional[float] = None
    last_assignment_at: Optional[float] = None
    observation_source: Optional[ExecutionModeObservationSource] = None
    run_ref_state: Optional[ExecutionModeRunRefState] = None


class ExecutionModeDivergenceListOut(BaseModel):
    system_id: int
    generated_at: float
    nodes: List[ExecutionModeDivergenceOut]


class ExecutionModeNodeProjectionOut(BaseModel):
    """One Node's mode reading.

    `maturity` sits beside `execution_mode` precisely because the two are
    independent axes (ADR-6) -- neither is ever derived from the other.
    """

    node_id: int
    node_key: str
    maturity: EvolutionMaturityState
    execution_mode: ExecutionMode
    mode_source: ExecutionModeSourceScope
    mode_reason: ExecutionModeReason
    source_ref: str
    flow_refs: List[str]
    divergence: ExecutionModeDivergence
    observed_mode: Optional[ExecutionMode] = None
    observed_at: Optional[float] = None
    #: The observation's own standing, beside the divergence and never folded
    #: into it (see `ExecutionModeDivergenceOut`).
    observation_source: Optional[ExecutionModeObservationSource] = None
    run_ref_state: Optional[ExecutionModeRunRefState] = None


class ExecutionModeProjectionOut(BaseModel):
    system_id: int
    schema_version: str
    generated_at: float
    system_decision: ExecutionModeDecisionOut
    assignments: List[ExecutionModeAssignmentOut]
    nodes: List[ExecutionModeNodeProjectionOut]


class ExecutionModeDenialOut(BaseModel):
    """The 409 body of a refused capability gate (§4.1).

    `decision` is `null` for `unknown_capability` and `node_not_found`: those
    describe a broken request, not a mode reading, and reporting a mode for a
    subject that could not be resolved would assert a fact about something
    that does not exist.
    """

    denial_code: ExecutionModeDenialCode
    message: str
    mode: Optional[ExecutionMode] = None
    source_scope: Optional[ExecutionModeSourceScope] = None
    reason: Optional[ExecutionModeReason] = None
    decision: Optional[ExecutionModeDecisionOut] = None


# ---------------------------------------------------------------------------
# Flow explanation projection (Epic #412, Issue #414)
#
# The domain layer is `app/flow_explanation.py`, which owns every vocabulary
# below; these `Literal` aliases mirror it so FastAPI puts a real enum in the
# OpenAPI schema (the same pairing the Execution Mode block above uses).
#
# Two rules are visible in the shapes themselves. First, the five axes of
# §1.2 are five SEPARATE fields on `FlowExplanationNodeOut` -- there is no
# combined value, no average, no completion percentage and no Flow health
# score anywhere in this response (ADR-7 / #353); `sdk_policy_mode` and
# `execution_mode` may both read `shadow` and still be two different facts.
# Second, a section that could not be built is `null` AND named in
# `degraded_sections`: an empty list means "there are none", `null` means
# "we could not read them", and #356's 0 件 ≠ 取得できていない rule forbids
# collapsing those into one display.
# ---------------------------------------------------------------------------

FlowSubjectKind = Literal["runtime_flow", "static_flow"]
FlowSubjectResolution = Literal["resolved", "unresolved", "unavailable"]
#: §6.4's five missing answers plus `present`. `present` is the absence of a
#: missing answer, never a sixth one.
FlowFactState = Literal[
    "present", "missing", "unavailable", "unmeasured", "stale", "not_applicable"
]
#: A call graph that failed tells us nothing about the Nodes it would have
#: named, so there is no "partially resolved" third value.
FlowMembershipState = Literal["resolved", "unavailable"]
FlowMembershipBasis = Literal["flow_link", "probe_point_exact_match"]
FlowEvidenceKind = Literal[
    "trace",
    "anomaly",
    "drift_observation",
    "node_event",
    "code_location",
    "execution_ref",
    "stabilization_package",
]
FlowOpenItemKind = Literal[
    "anomaly",
    "missing_fact",
    "unmeasured_observation",
    "mode_divergence",
    "unresolved_membership",
    "stale_premise",
    "maturity_drift",
]
#: Which dependency reading the `responsibility` section is showing. Runtime
#: span parentage and a pinned snapshot's call graph answer different
#: questions and are never merged into one edge list.
FlowEdgeSource = Literal[
    "runtime_span_parentage", "static_call_graph", "unavailable", "not_applicable"
]


class FlowEvidenceOut(BaseModel):
    """One referencable evidence item (§6.5): `id` is `"<kind>:<ref>"`."""

    id: str
    kind: FlowEvidenceKind
    ref: str
    label: str
    node_key: Optional[str] = None
    recorded_at: Optional[float] = None


class FlowSubjectOut(BaseModel):
    subject_kind: FlowSubjectKind
    subject_ref: str
    label: str
    resolution: FlowSubjectResolution
    #: `not_applicable` for a runtime Flow -- it carries no snapshot. That is
    #: a different answer from `missing` (a static Flow with no snapshot).
    snapshot_state: FlowFactState
    #: Two facts about a runtime Flow that a single `resolution` used to
    #: carry, and which are independently true or false: `observation_state`
    #: is whether spans have ever been observed under this `flow_id`, and
    #: `model_state` is whether any Node is currently linked to it. A Flow
    #: that Nodes are modelled onto but which has never run is
    #: `missing` + `present`, and it is a real subject -- #415 has always
    #: treated it as one. Both are `not_applicable` for a static Flow, whose
    #: Node membership is the structural `membership` reading instead.
    observation_state: FlowFactState = "not_applicable"
    model_state: FlowFactState = "not_applicable"
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    detail: str = ""


class FlowMembershipOut(BaseModel):
    state: FlowMembershipState
    node_keys: List[str] = Field(default_factory=list)
    basis: Dict[str, FlowMembershipBasis] = Field(default_factory=dict)
    detail: str = ""


class FlowExplanationNodeOut(BaseModel):
    """One Node, five independent axes, plus its observation coverage.

    Never averaged, never combined (ADR-6). Each axis that could not be read
    carries its own `*_state` rather than a default value (#380).
    """

    node_id: int
    node_key: str
    display_name: str
    membership_basis: FlowMembershipBasis

    execution_mode: ExecutionMode
    #: `default` is the fail-closed `fixed` nobody chose; `system` with reason
    #: `system_assignment` is a human who chose it (§4.4).
    mode_source: ExecutionModeSourceScope
    mode_reason: ExecutionModeReason
    mode_source_ref: str = ""
    mode_state: FlowFactState = "present"
    mode_divergence: Optional[ExecutionModeDivergence] = None
    observed_mode: Optional[ExecutionMode] = None
    #: Whether the observation behind `mode_divergence` was attested, carried
    #: through from #413's projection and never re-derived (#366): a `match`
    #: from a reported value must not read like a measured one.
    mode_observation_source: Optional[ExecutionModeObservationSource] = None
    mode_observation_run_ref_state: Optional[ExecutionModeRunRefState] = None

    maturity: Optional[EvolutionMaturityState] = None
    maturity_state: FlowFactState = "present"
    folded_maturity: Optional[EvolutionMaturityState] = None
    maturity_consistent: Optional[bool] = None

    implementation_modality: Optional[EvolutionImplementationModality] = None
    implementation_modality_state: FlowFactState = "missing"

    improvement_status: Optional[str] = None
    improvement_status_state: FlowFactState = "missing"

    sdk_policy_mode: Optional[str] = None
    sdk_policy_mode_state: FlowFactState = "missing"

    observation: Optional[Dict[str, Any]] = None
    observation_state: FlowFactState = "unmeasured"
    monitoring_contract_declared: bool = False

    evidence: List[FlowEvidenceOut] = Field(default_factory=list)
    capability_refs: List[str] = Field(default_factory=list)
    purpose_element_refs: List[str] = Field(default_factory=list)
    feature_refs: List[str] = Field(default_factory=list)


class FlowPurposeSectionOut(BaseModel):
    purpose_elements: List[Dict[str, Any]] = Field(default_factory=list)
    capabilities: List[Dict[str, Any]] = Field(default_factory=list)
    features: List[Dict[str, Any]] = Field(default_factory=list)
    purpose_chain_state: FlowFactState = "present"
    detail: str = ""


class FlowResponsibilitySectionOut(BaseModel):
    edge_source: FlowEdgeSource = "not_applicable"
    node_order: List[str] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    contracts: List[Dict[str, Any]] = Field(default_factory=list)
    external_boundaries: List[Dict[str, Any]] = Field(default_factory=list)
    entry_ref: Optional[str] = None
    entry_state: FlowFactState = "missing"
    truncated: bool = False
    diagnostics: List[str] = Field(default_factory=list)


class FlowOpenItemOut(BaseModel):
    id: str
    kind: FlowOpenItemKind
    label: str
    detail: str = ""
    node_key: Optional[str] = None
    missing_state: Optional[FlowFactState] = None
    evidence_ids: List[str] = Field(default_factory=list)


class FlowOpenItemsSectionOut(BaseModel):
    items: List[FlowOpenItemOut] = Field(default_factory=list)


class FlowExperimentSummaryOut(BaseModel):
    """One #415 proposal, summarised.

    `status` is #415's own event fold (§7.4), never re-implemented here. When
    that definition cannot be loaded the status is `null` with
    `status_state: "unavailable"` -- a guessed status would be the second
    lifecycle definition the fold exists to prevent.
    """

    proposal_id: int
    proposal_key: str
    title: str
    comparison_scope: str
    status: Optional[str] = None
    status_state: FlowFactState = "unavailable"
    target_node_keys: List[str] = Field(default_factory=list)
    evidence_refs: List[Any] = Field(default_factory=list)
    execution_refs: List[Dict[str, Any]] = Field(default_factory=list)
    isolation_strategy: str = ""
    expires_at: Optional[float] = None
    created_at: Optional[float] = None


class FlowExperimentsSectionOut(BaseModel):
    proposals: List[FlowExperimentSummaryOut] = Field(default_factory=list)
    status_source: str = "unavailable"


class FlowNodeBaselineOut(BaseModel):
    node_key: str
    stable_implementation: Optional[Dict[str, Any]] = None
    stable_state: FlowFactState = "missing"
    rollback_implementation: Optional[Dict[str, Any]] = None
    rollback_state: FlowFactState = "missing"
    #: Read as its own record, never inferred from the pin's existence (#304).
    approval: Optional[Dict[str, Any]] = None
    approval_state: FlowFactState = "missing"


class FlowBaselineSectionOut(BaseModel):
    nodes: List[FlowNodeBaselineOut] = Field(default_factory=list)


class FlowExplanationOut(BaseModel):
    """The whole §6.3 projection.

    A `null` section plus its name in `degraded_sections` means the section
    could not be read; an empty section means there is nothing in it. Those
    are two different answers (#356).
    """

    system_id: int
    schema_version: str
    generated_at: float
    subject: FlowSubjectOut
    membership: FlowMembershipOut
    purpose: Optional[FlowPurposeSectionOut] = None
    responsibility: Optional[FlowResponsibilitySectionOut] = None
    nodes: Optional[List[FlowExplanationNodeOut]] = None
    open_items: Optional[FlowOpenItemsSectionOut] = None
    experiments: Optional[FlowExperimentsSectionOut] = None
    baseline: Optional[FlowBaselineSectionOut] = None
    drilldown: Dict[str, Any] = Field(default_factory=dict)
    rollup: List[Dict[str, Any]] = Field(default_factory=list)
    degraded_sections: List[str] = Field(default_factory=list)
    degraded_detail: Dict[str, str] = Field(default_factory=dict)


class FlowSubjectListOut(BaseModel):
    """The Flow subjects available in this System, both kinds, kept apart."""

    system_id: int
    generated_at: float
    runtime_flows: List[Dict[str, Any]] = Field(default_factory=list)
    static_flows: List[Dict[str, Any]] = Field(default_factory=list)
    snapshot_id: Optional[int] = None
    snapshot_state: FlowFactState = "missing"
    degraded_sections: List[str] = Field(default_factory=list)
    degraded_detail: Dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Flow explanation projection (Epic #412, Issue #414) -- ANCHOR-414
# Insert the #414 request/response models directly above this line.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Flow experiment orchestration (Epic #412, Issue #415)
# ---------------------------------------------------------------------------
#
# Canonical contract: `docs/execution-modes.md` §7 (§8.4 for persistence, §9.3
# for the test requirements). The domain layer is `app/flow_orchestration.py`,
# which mirrors every alias below with `get_args` -- so the API vocabulary and
# the domain vocabulary can never disagree (the same shape #413's aliases use).
#
# Three boundary rules are expressed in the SHAPE of these models, not only in
# prose:
#
# * **`actor` and `decision_method` are never request fields.** Approving,
#   rejecting, withdrawing, recording an execution / result / promotion
#   candidate / rollback are all human decisions; the name comes from the
#   authenticated principal at the route and `decision_method` is fixed to
#   `manual` server-side (#337, §10). A body-supplied actor would let any
#   caller forge someone else's approval.
#
# * **`status` is not a request field and not a stored column.** It is folded
#   from `flow_experiment_event` on every read (§7.4) and appears only on the
#   response, next to the moment it was derived at.
#
# * **`comparison_scope` and `isolation_strategy` are plain `str` on the way
#   IN, on purpose.** §7.1 enumerates `comparison_scope_mismatch` and
#   `isolation_strategy_missing` as finite gate codes the developer must be
#   able to receive; typing them as `Literal` here would make Pydantic reject
#   the request first and those two enumerated codes would be unreachable
#   through the API. They are `Literal` on the way OUT, where the value has
#   already passed the gate.

#: `single_node` targets exactly one Node, `sub_pipeline` two or more. The
#: count and the declared scope must agree (§7.2).
FlowComparisonScope = Literal["single_node", "sub_pipeline"]

#: §7.3's finite isolation vocabulary. `none` and `pure` do not isolate
#: anything, which is why a Node whose `side_effect_class` is
#: `external_write` / `irreversible` may not use them (Principle 4).
FlowIsolationStrategy = Literal[
    "pure", "mock", "dry_run", "rollback_transaction", "isolated_workspace", "none"
]

#: The three evaluation contracts of ADR-7. They are held apart deliberately:
#: no level is ever computed from another and there is no combined score.
FlowEvaluationLevel = Literal["node", "flow_capability", "ux_outcome"]

#: Why a Node is attached to a proposal. The same Node may legitimately appear
#: as both, which is why the scope check counts DISTINCT Nodes (§7.2).
FlowExperimentTargetRole = Literal["baseline", "candidate_target"]

#: §7.4's append-only ledger vocabulary. `promotion_candidate_recorded` is a
#: candidate, never a promotion (§7.6).
FlowExperimentEventKind = Literal[
    "proposed",
    "approved",
    "rejected",
    "withdrawn",
    "expired",
    "execution_recorded",
    "result_recorded",
    "promotion_candidate_recorded",
    "rollback_recorded",
]

#: The DERIVED lifecycle position (§7.4). There is no `status` column behind
#: this value and there must never be one.
FlowExperimentStatus = Literal[
    "proposed", "approved", "rejected", "withdrawn", "expired", "executing", "completed"
]

#: Where an execution reference resolves. This layer runs nothing itself: the
#: execution's canonical row already exists in `replay_variants` /
#: `experiments` / `shadow_results` and is resolved again at read time (§7.6).
FlowExperimentExecutionKind = Literal["replay_variant_run", "experiment", "shadow_result"]

#: The three answers a reference resolution can give, never merged: the row is
#: there and usable, it is not there at all, or it is there but its own run
#: concluded it produced nothing usable.
FlowExecutionRefResolution = Literal["resolved", "unresolved", "stale"]

#: What a caller ASKS for. Separate from `FlowExperimentEventKind` because an
#: action is a request and an event is a recorded fact -- a refused action
#: writes no event at all.
FlowExperimentActionKind = Literal[
    "approve",
    "reject",
    "withdraw",
    "record_execution",
    "record_result",
    "record_promotion_candidate",
    "record_rollback",
]


class FlowExperimentTargetIn(BaseModel):
    """One target Node. `node_key` is the Evolution Node's durable slug
    (#394 ADR-2), resolved against this System at gate time."""

    model_config = ConfigDict(extra="forbid")

    node_key: str
    target_role: FlowExperimentTargetRole = "candidate_target"
    position: Optional[int] = None
    note: str = ""


class FlowEvaluationAxisIn(BaseModel):
    """One evaluation axis, which DECLARES the contract it belongs to.

    ADR-7 forbids computing a Flow/Capability reading out of Node readings;
    the structural guarantee against that is that every axis names its own
    level and nothing anywhere maps one level onto another.
    """

    model_config = ConfigDict(extra="forbid")

    level: FlowEvaluationLevel
    name: str
    metric: str = ""
    detail: str = ""


class FlowExperimentCreateIn(BaseModel):
    """A proposal as its author writes it (§7.1).

    Every element §7.1 requires is present here so an incomplete proposal is
    refused with the finite code that names the MISSING element, instead of a
    generic "invalid proposal" carrying twelve facts at once (#366).
    """

    model_config = ConfigDict(extra="forbid")

    proposal_key: str
    flow_subject_kind: FlowSubjectKind
    flow_subject_ref: str
    #: Mandatory for `static_flow`: an `entrypoint_id` without the snapshot it
    #: was read from names nothing (§6.2).
    captured_snapshot_id: Optional[int] = None
    #: Plain `str` so `comparison_scope_mismatch` stays reachable (see above).
    comparison_scope: str
    title: str = ""
    purpose: str = ""
    hypothesis: str = ""
    baseline_ref: str = ""
    candidate_refs: List[str] = Field(default_factory=list)
    targets: List[FlowExperimentTargetIn] = Field(default_factory=list)
    evaluation_axes: List[FlowEvaluationAxisIn] = Field(default_factory=list)
    quality_floor: Dict[str, Any] = Field(default_factory=dict)
    #: Plain `str` so `isolation_strategy_missing` stays reachable (see above).
    isolation_strategy: str = ""
    isolation_detail: str = ""
    #: Must bound something: at least one positive numeric limit (§7.1).
    cost_cap: Dict[str, Any] = Field(default_factory=dict)
    stop_conditions: List[str] = Field(default_factory=list)
    rollback_plan: str = ""
    evidence_refs: List[str] = Field(default_factory=list)
    #: The window in which a human may approve. Elapsing expires a proposal
    #: that is still awaiting a decision; it never unmakes an approval (§7.4).
    expires_at: Optional[float] = None
    reason: str = ""
    #: Provenance of the CONTENT. `reasoning_llm` + the drafting run's id is
    #: how a `POST /flow-experiments/draft` result is put into the queue by a
    #: human; the `proposed` EVENT is `manual` either way (§7.7).
    intelligence_run_id: Optional[int] = None


class FlowExperimentDecisionIn(BaseModel):
    """Approve / reject / withdraw. `actor` is deliberately absent (#337).

    `reason` is required for a rejection and a withdrawal -- both end a plan,
    and a decision recorded without a reason cannot be reviewed later.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = ""


class FlowExperimentExecutionIn(BaseModel):
    """Attach an execution that ALREADY EXISTS in its own canonical table.

    This layer creates no row in `replay_variants` / `experiments` /
    `shadow_results`; it points at one and refuses a pointer that does not
    resolve in this System (§7.6).
    """

    model_config = ConfigDict(extra="forbid")

    execution_kind: FlowExperimentExecutionKind
    execution_ref: str
    note: str = ""


class FlowExperimentResultIn(BaseModel):
    """What an execution produced. Never adopts, promotes or applies anything.

    `metrics` keys are the three evaluation contracts and nothing else: what
    was not measured stays absent, because an absent measurement and a derived
    one are different facts (ADR-7). The metrics must cover every axis the
    proposal itself declared, and the declared quality floor is evaluated and
    RECORDED against them -- a verdict, never a decision (§7.6).

    `execution_kind` / `execution_ref` name the one execution this result
    observes; they stay `Optional` so their absence is refused with the finite
    code `execution_ref_missing` rather than by an anonymous pydantic 422.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    execution_kind: Optional[FlowExperimentExecutionKind] = None
    execution_ref: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)


class FlowExperimentPromotionCandidateIn(BaseModel):
    """Recording a CANDIDATE is not a promotion (§7.6). The real promotion
    still goes through the existing Experiment adoption / Stabilization /
    publish human gates.

    It binds to three facts: a candidate the proposal itself declared, an
    execution registered on this proposal that still resolves, and a result
    recorded FOR that execution. `execution_kind` / `execution_ref` are
    `Optional` here only so their absence is refused with the finite code
    `execution_ref_missing` rather than by an anonymous pydantic 422.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_ref: str
    rationale: str
    execution_kind: Optional[FlowExperimentExecutionKind] = None
    execution_ref: Optional[str] = None


class FlowExperimentRollbackIn(BaseModel):
    """An audit fact about work performed elsewhere: this layer can revert
    nothing itself."""

    model_config = ConfigDict(extra="forbid")

    detail: str


class FlowExperimentTargetOut(BaseModel):
    id: int
    target_node_key: str
    target_role: FlowExperimentTargetRole
    position: int
    note: str = ""


class FlowExperimentEventOut(BaseModel):
    """One row of the append-only ledger that IS the lifecycle (§7.4)."""

    id: int
    event_kind: FlowExperimentEventKind
    actor_kind: str
    actor: Optional[str] = None
    reason: str = ""
    decision_method: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: float


class FlowExperimentExecutionRefOut(BaseModel):
    id: int
    execution_kind: FlowExperimentExecutionKind
    execution_ref: str
    note: str = ""
    recorded_at: float
    #: Recomputed on every read; a stored row id is never trusted on its own
    #: (#405).
    resolution: FlowExecutionRefResolution


class FlowExperimentProposalOut(BaseModel):
    """The immutable content, the derived status and the whole audit trail."""

    schema_version: str
    id: int
    system_id: int
    proposal_key: str
    flow_subject_kind: FlowSubjectKind
    flow_subject_ref: str
    captured_snapshot_id: Optional[int] = None
    comparison_scope: FlowComparisonScope
    title: str
    purpose: str
    hypothesis: str
    baseline_ref: str
    candidate_refs: List[Any] = Field(default_factory=list)
    evaluation_axes: List[Dict[str, Any]] = Field(default_factory=list)
    #: The same axes grouped by their DECLARED level -- a grouping, never a
    #: derivation, and never summed into one number (ADR-7).
    evaluation_axes_by_level: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    quality_floor: Dict[str, Any] = Field(default_factory=dict)
    isolation_strategy: FlowIsolationStrategy
    isolation_detail: str = ""
    cost_cap: Dict[str, Any] = Field(default_factory=dict)
    stop_conditions: List[Any] = Field(default_factory=list)
    rollback_plan: str
    evidence_refs: List[Any] = Field(default_factory=list)
    expires_at: Optional[float] = None
    decision_method: str
    intelligence_run_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: float
    #: DERIVED by folding `events` on this read. No column backs it (§7.4).
    status: FlowExperimentStatus
    status_derived_at: float
    targets: List[FlowExperimentTargetOut] = Field(default_factory=list)
    events: List[FlowExperimentEventOut] = Field(default_factory=list)
    executions: List[FlowExperimentExecutionRefOut] = Field(default_factory=list)
    #: The promotion-candidate events, surfaced for readability. Each one is a
    #: candidate record and carries `promotion_performed: false` (§7.6).
    promotion_candidates: List[FlowExperimentEventOut] = Field(default_factory=list)


class FlowExperimentListOut(BaseModel):
    system_id: int
    generated_at: float
    proposals: List[FlowExperimentProposalOut] = Field(default_factory=list)


class FlowExperimentRejectionOut(BaseModel):
    """The 422 body of a §7.1 refusal.

    `code` is one of the twelve completeness codes or the seven structural
    ones, and `detail` names the specific Nodes / levels / keys involved -- so
    the developer reads WHICH element is the problem without re-deriving the
    gate (the shape #413's `ExecutionModeDenialOut` uses for its own refusal).
    """

    code: str
    message: str
    detail: List[str] = Field(default_factory=list)


class FlowExperimentLifecycleRejectionOut(BaseModel):
    """The 409 body of an illegal transition (§7.4).

    A mode refusal on the same endpoint is ALSO a 409, but it carries
    `ExecutionModeDenialOut` instead: approval and execution mode are two
    independent facts (§7.5) and the developer's next action differs, so the
    two refusals never share a body.
    """

    code: str
    message: str


class FlowExperimentDraftIn(BaseModel):
    """Ask the experiment reasoning model for a DRAFT (§7.7).

    Reachable only in `propose` / `shadow`: the capability gate runs before
    the first line that could read a credential (EM-ADR-3).

    The draft is grounded in #414's projection for this Flow, and its
    `evidence_refs` are validated against the ids that projection actually
    produced -- a citation the model composed is a fabricated fact (§7.7).
    """

    model_config = ConfigDict(extra="forbid")

    flow_subject_kind: FlowSubjectKind
    flow_subject_ref: str
    #: Mandatory for `static_flow`: the projection resolves an `entrypoint_id`
    #: only against the snapshot it was read from (§6.2).
    captured_snapshot_id: Optional[int] = None
    node_keys: List[str] = Field(default_factory=list)
    goal: str


class FlowExperimentDraftOut(BaseModel):
    """A draft is NOT a proposal.

    Nothing has been persisted except the `intelligence_runs` audit row, and
    no `proposed` event exists until a human posts the content through
    `POST /flow-experiments`. `created_proposal` is therefore always `false`
    -- it is stated rather than implied, because "the model produced a plan"
    and "a plan is waiting for approval" are exactly the two facts §7.7 keeps
    apart.
    """

    draft: Dict[str, Any]
    intelligence_run_id: int
    is_mock: bool
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    decision: ExecutionModeDecisionOut
    created_proposal: bool = False


# ---------------------------------------------------------------------------
# Flow experiment orchestration (Epic #412, Issue #415) -- ANCHOR-415
# Insert the #415 request/response models directly above this line.
# ---------------------------------------------------------------------------
