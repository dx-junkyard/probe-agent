from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["off", "trace", "shadow"]
Evaluation = Literal["better", "worse", "same", "unknown"]
GenerationVerdict = Literal["better", "worse", "same", "unsafe", "error", "unknown"]


class TraceEvent(BaseModel):
    trace_id: str
    component_id: str
    mode: Optional[str] = None
    input: Optional[Any] = None
    output: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    timestamp: float


class ShadowResult(BaseModel):
    trace_id: str
    component_id: str
    current_output: Optional[str] = None
    candidate_output: Optional[str] = None
    candidate_error: Optional[str] = None
    candidate_duration_ms: float = 0.0
    timestamp: float


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
]
DecisionMethod = Literal["deterministic", "reasoning_llm", "manual"]


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
    "feature", "component", "trace", "experiment", "probe_plan"
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
