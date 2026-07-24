from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
IntelligenceRunStatus = Literal["pending", "completed", "failed"]
IntelligenceRunType = Literal[
    "repository_drafts",
    "system_profile_draft",
    "feature_map_draft",
    "symbol_index",
    "feature_code_mapping",
    "probe_plan",
    "probe_plan_from_flow",
    "capability_hierarchy",
    "explanation_refresh",
    "interview_proposal",
    "interview_dialogue",
    # Issue #130: pass 1 of the dialogue turn (choose evidence to read, or
    # declare no evidence needed) is audited separately from pass 2 (question
    # generation, run_type "interview_dialogue" above) so the two reasoning
    # calls stay distinguishable in the audit trail.
    "interview_evidence_selection",
    # Issue #127/#123: the system-understanding review behind
    # update-understanding (system_understanding_reviewer.py). Recorded for
    # both success and failure so the reviewer's prompt_version stays
    # auditable (Principle 7).
    "understanding_review",
    # Issue #135: reconciling approved metadata/probe plans against
    # deterministic runtime trace aggregates. The aggregation itself is
    # deterministic and not separately audited; only the reasoning
    # reconciliation step that picks confirmation questions is.
    "runtime_reality_check",
    # Issue #168: Probe Pattern lifecycle. pattern_reconcile classifies saved
    # pattern points against the latest snapshot (deterministic structural
    # checks, escalating to reasoning for moved/split/missing);
    # pattern_investigate is the "I don't know" investigation assistance;
    # probe_plan_from_pattern records the manual decision that turned an
    # approved reconciliation into a Probe Plan.
    "pattern_reconcile",
    "pattern_investigate",
    "probe_plan_from_pattern",
    # Issue #284: Intent Brief. Proposes missing goal/pain/success_criteria/
    # priority/constraints/non_goals items from the session conversation and
    # user_intent free text. Proposed items are never auto-confirmed.
    "intent_proposal",
    # Issue #285: Inquiry side-conversation answer generation (the overall
    # composed outcome; superseded internals split into "question_route" and
    # "investigation" below by Issue #286, each audited separately).
    "inquiry_answer",
    # Issue #286: Question Router classifies a question into
    # human_only | system_researchable | hybrid before any investigation.
    "question_route",
    # Issue #286: read-only Investigation Agent research over the pinned
    # snapshot, budget-bounded, for system_researchable/hybrid questions.
    "investigation",
    # Issue #290 Finding 5 (Part 2): semantic match/mismatch judge over
    # alignment items whose deterministic runtime baseline is 'match'
    # (app/runtime_match_judge.py). Never runs for stale/unobserved/
    # environment-mismatch items -- those keep their deterministic value.
    "runtime_match",
]
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


class SnapshotRefOut(BaseModel):
    id: int
    commit_sha: str
    status: str
    created_at: float


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
    snapshot_id: int
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


class InterviewConfirmUnderstandingRequest(BaseModel):
    """Manual confirmation that the gathered interview context is sufficient.

    Issue #123: in the zero-base fallback (no structured understanding could
    be built), the developer explicitly confirms that the conversation
    contains enough context to move to proposal generation. This is a manual
    decision record, not an LLM output.
    """

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=200)


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
InterviewInquiryStatus = Literal["open", "resolved", "unresolved", "cancelled", "held"]
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
    "informational_only", "unchanged_since_confirmation",
]
# Item-level user progress. 'inquiry' is set while an Inquiry
# (origin_kind='review_item') is open on this item, and reset to 'open'
# (never 'answered') when that Inquiry closes -- the developer must still
# explicitly answer via this item's own endpoint (Principle 2).
AlignmentItemStatus = Literal["open", "answered", "corrected", "held", "inquiry"]
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
