from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["off", "trace", "shadow"]
Evaluation = Literal["better", "worse", "same", "unknown"]
GenerationVerdict = Literal["better", "worse", "same", "unsafe", "error", "unknown"]


EntityRole = Literal["source", "derived", "related"]
# Projection phases: input/output (Issue #146); shadow_* added in Issue #150.
ProjectionPhase = Literal["input", "output", "shadow_current", "shadow_candidate"]


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
# documented convention (Principle 6 — explicit finite set, no heuristics).
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
    # Interview sessions in this system that already produced a review diff,
    # so the Dashboard can pair "patch generated" with "no signal yet".
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
    # True when a ready snapshot exists but has no completed symbol index.
    symbols_stale: bool = False
    next_actions: List[str] = Field(default_factory=list)


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
    access_token: str
    token_type: str = "bearer"
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

InterviewStage = Literal[
    "understanding_initialized",
    "purpose_confirmation",
    "capability_confirmation",
    "element_classification",
    "api_boundary_mapping",
    "probe_flow_selection",
    "proposal_generation",
]
InterviewProposalApprovalState = Literal["proposed", "approved", "rejected", "edited"]
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
    # Issue #165: pinned snapshot's commit, populated on the detail endpoint
    # so the review-diff UI can state what the patch applies to.
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
    materialization_diff: Optional[str] = None
    materialization_ref: Optional[str] = None
    materialized_at: Optional[float] = None
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

    @model_validator(mode="after")
    def _require_answer_or_unknown(self) -> "InterviewQaAnswerRequest":
        if not self.answer_unknown and not self.answer_text.strip():
            raise ValueError("answer_text is required unless answer_unknown is set")
        return self


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
    last_observed_at: Optional[float] = None
    has_traces: bool = False


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
    # Issue #165: commit the diff was generated against, for provenance
    # display and `.patch` filenames.
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


class SystemUnderstandingNextActionOut(BaseModel):
    action: str
    reason: str
    link: Optional[str] = None


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


class SystemUnderstandingOut(BaseModel):
    system_id: int
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    pipeline: List[SystemUnderstandingPipelineStepOut] = Field(default_factory=list)
    purpose: Optional[SystemUnderstandingPurposeOut] = None
    capabilities: List[SystemUnderstandingCapabilitySummaryOut] = Field(default_factory=list)
    entrypoints: List[SystemUnderstandingEntrypointSummaryOut] = Field(default_factory=list)
    major_symbols: List[SystemUnderstandingSymbolSummaryOut] = Field(default_factory=list)
    gaps: List[SystemUnderstandingGapOut] = Field(default_factory=list)
    gap_summary: List[SystemUnderstandingGapSummaryOut] = Field(default_factory=list)
    metadata_coverage: Optional[SystemUnderstandingMetadataCoverageOut] = None
    next_actions: List[SystemUnderstandingNextActionOut] = Field(default_factory=list)


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


class AssistantActionOut(BaseModel):
    label: str
    kind: Literal["navigate", "configure", "operate"]
    target: str
    detail: str = ""


class AssistantCitationOut(BaseModel):
    type: Literal["setting", "diagnostic_check", "pipeline_step"]
    id: str
    title: str = ""
    detail: str = ""


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
