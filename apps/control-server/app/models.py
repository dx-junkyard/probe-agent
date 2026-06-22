from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
InclusionStatus = Literal["indexed", "metadata_only", "too_large", "binary"]
SnapshotStatus = Literal["not_configured", "indexing", "ready", "failed"]
IntelligenceRunStatus = Literal["pending", "completed", "failed"]
IntelligenceRunType = Literal[
    "repository_drafts",
    "system_profile_draft",
    "feature_map_draft",
    "symbol_index",
    "feature_code_mapping",
    "probe_plan",
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
WorkspaceProposalStatus = Literal["proposed", "accepted", "rejected", "superseded"]
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
