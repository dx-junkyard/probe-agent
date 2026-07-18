export interface SystemOut {
  id: number;
  name: string;
  environment: string;
  description: string;
  owner_user_id: number | null;
  created_at: string;
  updated_at: string;
  component_count: number;
  trace_count: number;
  last_seen: number | null;
}

export interface ComponentSummary {
  component_id: string;
  mode: "off" | "trace" | "shadow";
  trace_count: number;
  last_seen: number | null;
}

// Replay capture (Issue #242 Phase A / #243): deterministic, finite-set
// classification of whether a trace's structured input capture can
// mechanically restore the call inputs. See docs/project-intelligence.md's
// Replay / Simulation section for the full reason-code semantics.
export type Replayability = "replayable" | "partial" | "unreplayable";
export type ReplayReason =
  | "unsupported_type"
  | "redacted"
  | "depth_limit_exceeded"
  | "size_limit_exceeded"
  | "round_trip_failed"
  | "capture_failed"
  | "redaction_blocked";

export interface TraceEvent {
  trace_id: string;
  component_id: string;
  mode: string;
  input: unknown | null;
  output: string | null;
  error: string | null;
  duration_ms: number | null;
  timestamp: number;
  // Replay capture (Issue #242 Phase A / #243) -- present since Phase A;
  // null on pre-Phase-A rows or components not opted into replay_capture.
  input_capture?: unknown | null;
  replayability?: Replayability | null;
  replay_reasons?: ReplayReason[] | null;
}

export interface Policy {
  mode: "off" | "trace" | "shadow";
}

export type ConnectivityState = "no_signal" | "smoke_only" | "receiving";

export interface ConnectivityStatusOut {
  system_id: number;
  state: ConnectivityState;
  total_trace_count: number;
  smoke_trace_count: number;
  real_trace_count: number;
  first_trace_at: number | null;
  last_trace_at: number | null;
  last_trace_component_id: string | null;
  smoke_component_id: string;
  materialized_session_ids: number[];
}

// Trace lineage (Issue #145/#146/#147)
export interface LineageEntity {
  type: string;
  id: string;
  role: string;
}

export interface LineageProjection {
  projection_name: string;
  phase: string;
  fields: Record<string, unknown>;
  metrics: Record<string, unknown>;
  samples: Record<string, unknown>;
  data_hash: string | null;
  truncated: boolean;
  error: string | null;
}

export interface LineageStep {
  trace_id: string;
  component_id: string;
  mode: string | null;
  span_id: string | null;
  parent_span_id: string | null;
  flow_id: string | null;
  correlation_id: string | null;
  duration_ms: number | null;
  timestamp: number;
  output: string | null;
  error: string | null;
  replayability: Replayability | null;
  replay_reasons: ReplayReason[];
  entities: LineageEntity[];
  projections: LineageProjection[];
}

export interface LineageOut {
  query: Record<string, unknown>;
  steps: LineageStep[];
}

// Trace analyzers (Issue #148/#149)
export interface TraceAnalyzer {
  id: number;
  name: string;
  intent: string;
  spec: Record<string, unknown>;
  source: string;
  review_status: "proposed" | "approved" | "rejected";
  decision_method: "deterministic" | "reasoning_llm" | "manual";
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  schema_version: string | null;
  is_mock: boolean;
  // Audit of the human review decision (set on approve/reject; always "manual").
  reviewed_at: number | null;
  review_decision_method: string | null;
  created_at: number;
  updated_at: number;
}

export interface AnalysisRun {
  id: number;
  analyzer_id: number;
  status: "pending" | "completed" | "failed";
  result: Record<string, unknown> | null;
  error_details: string | null;
  row_count: number | null;
  started_at: number;
  completed_at: number | null;
  data_expired?: boolean;
  data_expired_note?: string | null;
}

// Trace Analyzer builder candidate values (Issue #157)
export interface AnalyzerEntity {
  entity_type: string;
  entity_id: string;
}

export interface AnalyzerContext {
  components: string[];
  entity_types: string[];
  entities: AnalyzerEntity[];
  projection_names: string[];
  field_names: string[];
  phases: string[];
  entities_truncated: boolean;
}

// Flow Explorer runtime overlay (Issue #151)
export interface FlowOverlayNode {
  node_id: string;
  component_id: string | null;
  observable: boolean;
  observed: boolean;
  observation_count: number;
  last_observed_at: number | null;
}

export interface FlowOverlayEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string | null;
  source_component_id: string | null;
  target_component_id: string | null;
  observed_transition: boolean;
}

export interface FlowDivergence {
  source_component_id: string;
  target_component_id: string;
  count: number;
}

export interface FlowOverlayOut {
  selection: Record<string, unknown>;
  nodes: FlowOverlayNode[];
  edges: FlowOverlayEdge[];
  divergences: FlowDivergence[];
  observed_component_ids: string[];
  unmatched_component_ids: string[];
  observed_trace_count: number;
}

export interface FlowOverlayRequest {
  entrypoint_type: string;
  entrypoint_id: string;
  max_depth?: number;
  max_nodes?: number;
  snapshot_id?: number | null;
  commit_sha?: string | null;
  selection: {
    kind: "entity" | "correlation" | "flow" | "analyzer";
    entity_type?: string;
    entity_id?: string;
    correlation_id?: string;
    flow_id?: string;
    analyzer_id?: number;
  };
}

export interface ShadowResult {
  id: number;
  trace_id: string;
  component_id: string;
  current_output: string | null;
  candidate_output: string | null;
  candidate_error: string | null;
  candidate_duration_ms: number | null;
  evaluation: string;
  timestamp: number;
}

export interface ComponentProfile {
  component_id: string;
  purpose: string;
  responsibility: string;
  expected_input: string;
  expected_output: string;
  failure_impact: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface UserOut {
  id: number;
  username: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface TokenOut {
  id: number;
  name: string;
  kind: string;
  user_id: number | null;
  system_id: number | null;
  revoked: boolean;
  created_at: string;
  expires_at: string | null;
  token?: string;
}

export interface MeResponse {
  user: UserOut | null;
  auth: string;
  system_id: number | null;
  transport: "authorization" | "x_api_key" | "cookie" | "legacy_api_key" | "anonymous";
}

// Issue #265: deterministic, credential-free, System-id-free "phase 0"
// facts from `GET /auth/bootstrap-status`. Never carries a secret.
export interface BootstrapStatusOut {
  admin_exists: boolean;
  auth_mode: "anonymous" | "user";
  llm_configured: boolean;
  environment: "development" | "production";
}

export interface RepositoryConfigOut {
  system_id: number;
  repo_path: string;
  include_patterns: string[];
  exclude_patterns: string[];
  created_at: string;
  updated_at: string;
}

export interface RepositoryCandidateOut {
  name: string;
  path: string;
}

export interface SnapshotFileOut {
  path: string;
  source_type: string;
  size_bytes: number;
  inclusion_status: "indexed" | "metadata_only" | "too_large" | "binary" | "excluded" | "unsupported";
  exclusion_reason: string;
}

export interface SnapshotOut {
  id: number;
  system_id: number;
  repo_path: string;
  commit_sha: string;
  status: string;
  file_count: number;
  total_size: number;
  indexed_size: number;
  metadata_only_count: number;
  warnings: string[];
  error_summary: string | null;
  created_at: string;
  completed_at: string | null;
  files: SnapshotFileOut[];
}

// Repository refresh-hub status (Issue #158)
export interface SnapshotRef {
  id: number;
  commit_sha: string;
  status: string;
  created_at: number;
}

export type RepositoryHeadRelation = "same" | "behind" | "diverged" | "unknown";

export interface RepositoryStatus {
  configured: boolean;
  repo_path: string | null;
  current_head: string | null;
  head_error: string | null;
  working_tree_dirty: boolean | null;
  dirty_file_count: number;
  dirty_sample: string[];
  latest_snapshot: SnapshotRef | null;
  latest_indexed_snapshot: SnapshotRef | null;
  understanding_snapshot_id: number | null;
  understanding_status: string | null;
  snapshot_stale: boolean;
  head_relation: RepositoryHeadRelation;
  commits_behind: number | null;
  symbols_stale: boolean;
  next_actions: string[];
}

export type RepositoryResyncStatus =
  | "queued"
  | "snapshotting"
  | "indexing"
  | "completed"
  | "snapshot_failed"
  | "index_failed";

export interface RepositoryResyncJob {
  id: number;
  system_id: number;
  snapshot_id: number | null;
  status: RepositoryResyncStatus;
  error: string | null;
  stale_capability_count: number;
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
}

export interface SystemStateTargetUi {
  route: string;
  anchor: string | null;
  action_label: string;
}

/** User phase (Issue #237; extended to the full 6-step improvement flow by
 * Issue #256): setup -> preparation -> instrumentation -> observation ->
 * evaluation -> publish (terminal display phase). */
export type UserPhase =
  | "setup"
  | "preparation"
  | "instrumentation"
  | "observation"
  | "evaluation"
  | "publish";

export interface SystemStateItem {
  state_id: string;
  state_group: string;
  severity: "ok" | "info" | "warning" | "blocked" | "error";
  status: string;
  user_action_kind: string;
  intervention_timing: string;
  subject: string;
  summary: string;
  detail: string;
  impact: string;
  remediation: string;
  evidence: Record<string, unknown>;
  target_ui: SystemStateTargetUi | null;
  /** Pages where this item is displayed; target_ui remains the fix destination. */
  display_routes?: string[];
  related_checks: string[];
  related_pipeline_steps: string[];
  source: string;
  dedupe_key: string;
  scope: string;
  decision_method: "deterministic";
  /** Fixed state_group -> phase mapping plus a small per-item override list (Issue #237). */
  phase?: UserPhase;
}

export interface SystemStatePhaseCompletion {
  phase: UserPhase;
  complete: boolean;
  /** Issue #240: server-provided display label (Japanese) for this phase. */
  label?: string;
}

export interface SystemStateAssessment {
  system_id: number;
  generated_at: number;
  overall_severity: SystemStateItem["severity"];
  severity_counts: Record<string, number>;
  items: SystemStateItem[];
  primary_item: SystemStateItem | null;
  notification_items: SystemStateItem[];
  page_items: Record<string, SystemStateItem[]>;
  /** Current user phase and each phase's completion condition (Issue #237). */
  user_phase?: UserPhase;
  phases?: SystemStatePhaseCompletion[];
}

export type InterviewSessionStatus = "open" | "proposals_ready" | "materialized" | "closed";
export type InterviewMessageRole = "user" | "assistant" | "system";
export type InterviewStage =
  | "understanding_initialized"
  | "purpose_confirmation"
  | "capability_confirmation"
  | "element_classification"
  | "api_boundary_mapping"
  | "probe_flow_selection"
  | "proposal_generation";
export type InterviewDecisionMethod = "deterministic" | "reasoning_llm" | "manual";
export type InterviewApprovalState = "proposed" | "approved" | "rejected" | "edited" | "needs_review";
export type SourceMetadataElementType =
  | "system" | "core" | "capability" | "element" | "supporting" | "boundary";
export type SourceMetadataOperationKind =
  | "analysis" | "read" | "write" | "mutation" | "io" | "orchestration" | "validation" | "other";
export type SourceMetadataStateEffect =
  | "none" | "database-read" | "database-write" | "network" | "filesystem" | "cache" | "external-api" | "queue";
export type ProbeRecommendedMode = "trace" | "shadow";
export type ProbeSideEffectRisk = "none" | "low" | "medium" | "high";
export type ProbeReplayability = "safe" | "caution" | "unsafe";

export interface UnderstandingItem {
  name: string;
  summary: string;
  confidence: { level: string; reason: string };
  evidence: { path: string; start_line: number; end_line: number; summary: string }[];
  why_core: string;
  related_docs: string[];
  related_apis: string[];
  children: string[];
}

export interface GapItem {
  gap_type: string;
  name: string;
  summary: string;
  severity: string;
}

export interface InterviewQuestionEvidenceRef {
  path: string;
  start_line: number;
  end_line: number;
}

export interface OpenQuestion {
  question: string;
  category: string;
  priority: string;
  // Issue #128: hypothesis-first questions carry the model's working
  // hypothesis, its snapshot-grounded evidence, and quick-answer options.
  hypothesis?: string | null;
  evidence_refs?: InterviewQuestionEvidenceRef[];
  answer_options?: string[];
  // Issue #129: ID of the interview_qa row backing this question. Sent as
  // answered_qa_id with the dialogue turn so consumption is ID-based;
  // absent on entries from sessions predating the Q&A layer.
  qa_id?: number | null;
}

export interface InterviewStructuredQuestion {
  question_text: string;
  hypothesis: string | null;
  evidence_refs: InterviewQuestionEvidenceRef[];
  answer_options: string[];
}

export interface CurrentUnderstanding {
  system_purpose: UnderstandingItem[];
  core_capabilities: UnderstandingItem[];
  capability_elements: UnderstandingItem[];
  supporting_elements: UnderstandingItem[];
  api_boundaries: UnderstandingItem[];
  probe_flow_candidates: UnderstandingItem[];
}

export interface InterviewSessionOut {
  id: number;
  system_id: number;
  snapshot_id: number;
  snapshot_commit_sha?: string | null;
  title: string;
  focus: string;
  status: InterviewSessionStatus;
  stage: InterviewStage;
  current_understanding: CurrentUnderstanding | null;
  gap_analysis: GapItem[] | null;
  open_questions: OpenQuestion[] | null;
  user_intent: string | null;
  last_error: string | null;
  understanding_confirmed_at: number | null;
  understanding_confirmed_by: string | null;
  // Issue #129: set when an answered interview_qa question is corrected;
  // cleared only by a successful understanding rebuild.
  answers_revised_at: number | null;
  // Issue #229/#263: server-computed mirror of the update-understanding 409
  // gate. Use this instead of re-deriving the confirmed/no-new-QA condition
  // client-side so the disabled state can never drift from the API.
  understanding_update_available: boolean;
  materialization_diff: string | null;
  materialization_ref: string | null;
  materialized_at: number | null;
  created_at: number;
  updated_at: number;
}

export interface InterviewMessageOut {
  id: number;
  session_id: number;
  role: InterviewMessageRole;
  content: string;
  intelligence_run_id: number | null;
  created_at: number;
}

export interface InterviewProposalMetadataBlock {
  role: string | null;
  capability: string | null;
  system_purpose: string | null;
  probe_value: string | null;
  element_type: SourceMetadataElementType | null;
  operation_kind: SourceMetadataOperationKind | null;
  consumers: string[];
  state_effects: SourceMetadataStateEffect[];
}

export interface InterviewProposalProbePlan {
  feature_id: string;
  objective: string;
  reason: string;
  recommended_mode: ProbeRecommendedMode;
  side_effect_risk: ProbeSideEffectRisk;
  replayability: ProbeReplayability;
}

export interface InterviewProposalOut {
  id: number;
  session_id: number;
  system_id: number;
  snapshot_id: number;
  message_id: number | null;
  intelligence_run_id: number;
  symbol_id: number | null;
  path: string;
  qualified_name: string;
  metadata: InterviewProposalMetadataBlock;
  probe_plan: InterviewProposalProbePlan;
  graph_node_id: string | null;
  capability_name: string | null;
  evidence_summary: string | null;
  proposal_confidence: number | null;
  decision_method: InterviewDecisionMethod;
  approval_state: InterviewApprovalState;
  is_mock: boolean;
  intelligence_run: IntelligenceRunOut | null;
  created_at: number;
  updated_at: number;
}

export interface InterviewSessionDetailOut extends InterviewSessionOut {
  messages: InterviewMessageOut[];
  proposals: InterviewProposalOut[];
}

export interface InterviewSnapshotRebaseOut {
  session_id: number;
  system_id: number;
  from_snapshot_id: number;
  to_snapshot_id: number;
  proposals_preserved: number;
  proposals_marked_needs_review: number;
  proposals_missing_source: number;
  proposals_changed_source: number;
  message: string;
  session: InterviewSessionOut;
}

export interface InterviewEvidenceLocation {
  snapshot_id: number;
  path: string;
  qualified_name: string;
  start_line: number;
  end_line: number;
}

export interface InterviewSymbolItem {
  symbol_id: number;
  path: string;
  qualified_name: string;
  kind: string;
  start_line: number;
  end_line: number;
  classification: "classified" | "unclassified";
  has_metadata: boolean;
  element_type: string | null;
  role: string | null;
  capability: string | null;
  operation_kind: string | null;
  probe_value: string | null;
  evidence: InterviewEvidenceLocation;
}

export interface InterviewEntrypointItem {
  entrypoint_id: number;
  entrypoint_type: string;
  category: string;
  label: string;
  handler_path: string;
  handler_qualified_name: string;
  line_start: number;
  line_end: number;
  classification: "classified" | "unclassified";
  has_metadata: boolean;
  evidence: InterviewEvidenceLocation;
}

export interface InterviewContextPack {
  system_id: number;
  snapshot_id: number;
  total_symbols: number;
  total_entrypoints: number;
  classified_count: number;
  unclassified_count: number;
  budget_max_chars: number;
  budget_used_chars: number;
  truncated: boolean;
  symbols: InterviewSymbolItem[];
  entrypoints: InterviewEntrypointItem[];
  omission_notes: string[];
}

export interface InterviewDialogueTurnOut {
  assistant_message: string;
  proposals: {
    path: string;
    qualified_name: string;
    symbol_id: number | null;
    metadata: InterviewProposalMetadataBlock;
    probe_plan: InterviewProposalProbePlan;
    denylist_hit: string | null;
  }[];
  // Whether this turn asked the reasoning model for proposals (gate open +
  // generate_proposals). True with zero proposals means the model returned
  // narrowing questions instead — show narrowing guidance, not a plain
  // success message.
  proposals_requested: boolean;
  next_questions: InterviewStructuredQuestion[];
  intelligence_run: IntelligenceRunOut | null;
  error: string | null;
  stage: InterviewStage | null;
  current_understanding: CurrentUnderstanding | null;
  gap_analysis: GapItem[] | null;
  open_questions_structured: OpenQuestion[] | null;
  // Issue #129: IDs of the interview_qa rows created from next_questions.
  created_qa_ids: number[];
  // Issue #130: pass-1 evidence-selection audit + what was actually read.
  evidence_run: IntelligenceRunOut | null;
  evidence_used: InterviewQaEvidenceRef[];
  // Issue #137: every snippet actually read for this turn's evidence-selection
  // run, regardless of citation. evidence_used above is unchanged.
  evidence_reads: IntelligenceRunEvidenceOut[];
  // Issue #142: count of question evidence_refs dropped as unverifiable
  // (graceful fallback, not an error).
  evidence_refs_dropped: number;
}

// --- Evidence read audit (Issue #137) -----------------------------------------

export interface IntelligenceRunEvidenceOut {
  id: number;
  system_id: number;
  intelligence_run_id: number;
  path: string;
  start_line: number;
  end_line: number;
  char_count: number;
  truncated: boolean;
  created_at: number;
}

export interface IntelligenceRunEvidenceListOut {
  intelligence_run_id: number;
  system_id: number;
  items: IntelligenceRunEvidenceOut[];
}

// --- Structured Interview Q&A (Issue #129) ----------------------------------

export type InterviewQaCategory = "purpose" | "capability" | "api" | "probe_flow" | "general";
// Issue #135: "runtime" questions come from reconciling approved metadata
// against deterministic runtime trace aggregates.
export type InterviewQaSource = "reviewer" | "dialogue" | "zero_base" | "runtime";
// "unconfirmed" (Issue #142): the developer explicitly could not confirm the
// answer ("わかりません"). Recorded as valid input, re-confirmed later via a
// hypothesis question — never treated as an answered/confirmed fact.
export type InterviewQaStatus = "open" | "answered" | "revised" | "skipped" | "unconfirmed";

export interface InterviewQaEvidenceRef {
  path: string;
  start_line: number;
  end_line: number;
  // Issue #130: populated when this evidence was actually read from the
  // pinned snapshot for the question, as opposed to just cited.
  char_count: number | null;
}

export interface InterviewQaOut {
  id: number;
  session_id: number;
  system_id: number;
  question_text: string;
  question_category: InterviewQaCategory;
  question_source: InterviewQaSource;
  hypothesis: string | null;
  evidence_refs: InterviewQaEvidenceRef[];
  // Issue #135: raw aggregated trace facts + declared-metadata provenance,
  // populated only for question_source === "runtime".
  runtime_evidence: RuntimeQaEvidence | null;
  answer_text: string | null;
  status: InterviewQaStatus;
  answered_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
  answered_at: number | null;
}

export interface InterviewQaAnswerOut {
  qa: InterviewQaOut;
  previous: InterviewQaOut | null;
  regeneration_recommended: boolean;
}

export interface InterviewQaListOut {
  session_id: number;
  system_id: number;
  items: InterviewQaOut[];
  open_count: number;
  high_priority_open_count: number;
  answers_revised_at: number | null;
}

export interface InterviewProposalDecisionOut {
  id: number;
  proposal_id: number;
  session_id: number;
  system_id: number;
  decision: "approved" | "rejected" | "edited";
  decision_method: InterviewDecisionMethod;
  actor: string;
  edited_metadata: InterviewProposalMetadataBlock | null;
  edited_probe_plan: InterviewProposalProbePlan | null;
  denylist_hit: string | null;
  decided_at: number;
}

export interface InterviewApprovedSetOut {
  session_id: number;
  system_id: number;
  snapshot_id: number;
  items: {
    proposal_id: number;
    path: string;
    qualified_name: string;
    symbol_id: number | null;
    metadata: InterviewProposalMetadataBlock;
    probe_plan: InterviewProposalProbePlan;
    decision: "approved" | "edited";
    decision_id: number;
    actor: string;
    decided_at: number;
  }[];
  total_proposals: number;
  approved_count: number;
  rejected_count: number;
  pending_count: number;
}

export interface InterviewMaterializeOut {
  session_id: number;
  system_id: number;
  snapshot_id: number;
  commit_sha?: string | null;
  diff: string;
  files_changed: number;
  items_materialized: number;
  skipped: string[];
  materialized_at: number;
  error: string | null;
}

// --- Understanding Revisions (Issue #136) ------------------------------------

export interface UnderstandingRevisionOut {
  id: number;
  session_id: number;
  system_id: number;
  snapshot_id: number;
  intelligence_run_id: number | null;
  current_understanding: CurrentUnderstanding | null;
  gap_analysis: Record<string, unknown>[] | null;
  created_at: number;
}

export interface UnderstandingRevisionListOut {
  session_id: number;
  system_id: number;
  items: UnderstandingRevisionOut[];
}

export interface UnderstandingDiffConfidenceChange {
  name: string;
  before: string | null;
  after: string | null;
}

export interface UnderstandingDiffSectionOut {
  section: string;
  added: string[];
  removed: string[];
  confidence_changed: UnderstandingDiffConfidenceChange[];
  summary_changed: string[];
}

export interface UnderstandingDiffOut {
  session_id: number;
  system_id: number;
  from_revision_id: number | null;
  to_revision_id: number | null;
  has_previous: boolean;
  sections: UnderstandingDiffSectionOut[];
}

// --- Runtime Reality Check (Issue #135) --------------------------------------

export interface RuntimeTraceFactsOut {
  component_id: string;
  window_days: number;
  call_count: number;
  error_count: number;
  error_rate: number | null;
  duration_p50_ms: number | null;
  duration_p90_ms: number | null;
  duration_p99_ms: number | null;
  last_observed_at: number | null;
  has_traces: boolean;
}

export interface RuntimeRealityCheckItemOut {
  proposal_id: number;
  decision_id: number;
  path: string;
  qualified_name: string;
  component_id: string;
  role: string | null;
  probe_value: string | null;
  state_effects: string[];
  recommended_mode: string;
  facts: RuntimeTraceFactsOut;
}

export interface RuntimeRealityFactsOut {
  session_id: number;
  system_id: number;
  snapshot_id: number;
  window_days: number;
  items: RuntimeRealityCheckItemOut[];
}

export interface RuntimeQaEvidence {
  component_id: string;
  qualified_name: string;
  path: string;
  metadata_source: { proposal_id: number; decision_id: number; session_id: number };
  // Raw facts + declared-metadata provenance only; the LLM's question text
  // and hypothesis live in the interview_qa columns, never in this blob.
  declared: {
    role: string | null;
    probe_value: string | null;
    state_effects: string[];
    recommended_mode: string;
  };
  facts: RuntimeTraceFactsOut;
}

export interface RuntimeRealityCheckRunOut {
  session_id: number;
  system_id: number;
  snapshot_id: number;
  intelligence_run: IntelligenceRunOut | null;
  items_considered: number;
  created_qa_ids: number[];
  skipped: boolean;
  skipped_reason: string | null;
  error: string | null;
}

export interface IntelligenceRunOut {
  id: number;
  system_id: number;
  snapshot_id: number | null;
  run_type: string;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  schema_version: string | null;
  decision_method: string;
  status: string;
  error_details: string | null;
  is_mock: boolean;
  started_at: string;
  completed_at: string | null;
}

export interface EvidenceItem {
  file: string;
  line_start: number;
  line_end: number;
  snippet?: string;
  relevance?: string;
}

export interface SystemProfileDraftOut {
  id: number;
  system_id: number;
  intelligence_run_id: number;
  snapshot_id: number;
  name: string;
  purpose: string;
  target_users: string;
  stakeholder_value: string;
  constraints: string;
  success_criteria: string;
  evidence: EvidenceItem[];
  is_mock: boolean;
  created_at: string;
}

export interface FeatureDraftOut {
  id: number;
  system_id: number;
  intelligence_run_id: number;
  snapshot_id: number;
  feature_id: string;
  name: string;
  summary: string;
  user_value: string;
  success_criteria: string;
  risks: string;
  evidence: EvidenceItem[];
  decision_method: string;
  is_mock: boolean;
  created_at: string;
}

export interface LatestDraftsOut {
  system_id: number;
  snapshot: SnapshotOut | null;
  intelligence_run: IntelligenceRunOut | null;
  system_profile_draft: SystemProfileDraftOut | null;
  feature_drafts: FeatureDraftOut[];
}

export interface DraftGenerationResultOut {
  intelligence_run: IntelligenceRunOut;
  system_profile_draft: SystemProfileDraftOut | null;
  feature_drafts: FeatureDraftOut[];
}

export interface SourceMetadataOut {
  start_line: number;
  end_line: number;
  raw_block: string;
  role: string | null;
  capability: string | null;
  element_type:
    | "system"
    | "core"
    | "capability"
    | "element"
    | "supporting"
    | "boundary"
    | null;
  system_purpose: string | null;
  operation_kind:
    | "analysis"
    | "read"
    | "write"
    | "mutation"
    | "io"
    | "orchestration"
    | "validation"
    | "other"
    | null;
  consumers: string[];
  state_effects: string[];
  probe_value: string | null;
  origin: "source_authored";
  // sha256 of the extracted explanation block (Issue #55); change signal only.
  explanation_hash: string | null;
}

export interface CodeSymbolOut {
  id: number;
  snapshot_id: number;
  system_id: number;
  path: string;
  qualified_name: string;
  kind: string;
  start_line: number;
  end_line: number;
  decorators: string[];
  imports: string[];
  docstring: string | null;
  is_test: boolean;
  is_pydantic_model: boolean;
  route_path: string | null;
  route_method: string | null;
  component_id: string | null;
  source_metadata: SourceMetadataOut | null;
  // Source-hash provenance (Issue #55). Change signals, not semantic equality.
  file_content_hash: string | null;
  symbol_source_hash: string | null;
  symbol_body_hash: string | null;
}

export interface ExplanationAnchorOut {
  id: number;
  snapshot_id: number;
  system_id: number;
  metadata_id: number;
  symbol_id: number;
  path: string;
  qualified_name: string;
  start_line: number;
  end_line: number;
  file_content_hash: string | null;
  symbol_source_hash: string | null;
  symbol_body_hash: string | null;
  explanation_hash: string | null;
}

export interface ExplanationAnchorsOut {
  system_id: number;
  snapshot_id: number;
  anchor_count: number;
  anchors: ExplanationAnchorOut[];
}

// Source-backed capability hierarchy (Issue #56).
export type HierarchyProvenanceKind =
  | "source_authored"
  | "structural"
  | "reasoning_llm"
  | "manual";

export interface HierarchyProvenanceOut {
  provenance_kind: HierarchyProvenanceKind;
  decision_method: "deterministic" | "reasoning_llm" | "manual";
  path: string | null;
  qualified_name: string | null;
  start_line: number | null;
  end_line: number | null;
  file_content_hash: string | null;
  symbol_source_hash: string | null;
  explanation_hash: string | null;
  symbol_id: number | null;
  entrypoint_id: number | null;
  // Stable logical entrypoint reference (#62) for Flow Explorer linking. The
  // numeric entrypoint_id above is snapshot-local and not link-safe.
  entrypoint_type: string | null;
  entrypoint_ref: string | null;
  feature_id: string | null;
  system_profile_draft_id: number | null;
  provider: string | null;
  model: string | null;
}

export interface SupportingElementOut {
  id: number;
  name: string;
  summary: string;
  supporting_kind: string | null;
  provenance: HierarchyProvenanceOut;
}

export interface CapabilityElementOut {
  id: number;
  name: string;
  summary: string;
  element_role: string | null;
  operation_kind: string | null;
  probe_value: string | null;
  classification: "classified" | "unclassified" | null;
  provenance: HierarchyProvenanceOut;
}

export interface CapabilityOut {
  id: number;
  capability_key: string | null;
  name: string;
  summary: string;
  provenance: HierarchyProvenanceOut;
  elements: CapabilityElementOut[];
  supporting_elements: SupportingElementOut[];
}

export interface CapabilityPurposeOut {
  id: number;
  name: string;
  summary: string;
  provenance: HierarchyProvenanceOut;
}

export interface CapabilityHierarchyOut {
  system_id: number;
  snapshot_id: number;
  intelligence_run: IntelligenceRunOut | null;
  purpose: CapabilityPurposeOut | null;
  capabilities: CapabilityOut[];
  unclassified_elements: CapabilityElementOut[];
  unattached_supporting: SupportingElementOut[];
  is_mock: boolean;
}

// Explanation drift (Issue #57). Hash drift is a review trigger, not a verdict.
export type DriftStatus =
  | "fresh"
  | "partially_stale"
  | "stale"
  | "missing_source"
  | "unknown";

export interface AnchorDriftOut {
  node_id: number;
  node_type: string;
  name: string;
  path: string | null;
  qualified_name: string | null;
  entrypoint_id: number | null;
  status: DriftStatus;
  changed_hashes: string[];
  captured_file_content_hash: string | null;
  captured_symbol_source_hash: string | null;
  captured_explanation_hash: string | null;
  current_file_content_hash: string | null;
  current_symbol_source_hash: string | null;
  current_explanation_hash: string | null;
}

export interface DriftCountsOut {
  total: number;
  fresh: number;
  stale: number;
  missing: number;
  unknown: number;
  symbol_deps_total: number;
  symbol_deps_changed: number;
  file_deps_total: number;
  file_deps_changed: number;
  explanation_blocks_total: number;
  explanation_blocks_changed: number;
  missing_anchors: number;
  mismatch_ratio: number;
}

export interface CapabilityDriftOut {
  capability_id: number;
  capability_key: string | null;
  name: string;
  status: DriftStatus;
  counts: DriftCountsOut;
  elements: AnchorDriftOut[];
  supporting_elements: AnchorDriftOut[];
}

export interface CapabilityHierarchyDriftOut {
  system_id: number;
  base_snapshot_id: number;
  target_snapshot_id: number;
  intelligence_run: IntelligenceRunOut | null;
  status: DriftStatus;
  counts: DriftCountsOut;
  target_indexed: boolean;
  purpose: AnchorDriftOut | null;
  capabilities: CapabilityDriftOut[];
  unclassified_elements: AnchorDriftOut[];
  unattached_supporting: AnchorDriftOut[];
  is_review_recommended: boolean;
  review_note: string | null;
}

// API role cards (Issue #58) — Flow Explorer developer context.
export interface ApiRoleCardOut {
  entrypoint_type: string;
  entrypoint_id: string;
  label: string;
  category: string;
  route_method: string | null;
  route_path: string | null;
  operation: string | null;
  framework: string | null;
  source: string;
  handler_resolved: boolean;
  classification: "classified" | "unclassified" | "unknown";
  capability_key: string | null;
  capability_name: string | null;
  element_type: string | null;
  role: string | null;
  operation_kind: string | null;
  probe_value: string | null;
  consumers: string[];
  state_effects: string[];
  boundaries: string[];
  flows_through: string[];
  provenance_kinds: HierarchyProvenanceKind[];
  drift_status: DriftStatus | null;
  drift_changed_anchors: number;
  drift_total_anchors: number;
  drift_review_recommended: boolean;
  review_needed: boolean;
  review_reason: string | null;
  node_id: number | null;
}

export interface ApiRoleCardsOut {
  system_id: number;
  snapshot_id: number | null;
  hierarchy_run: IntelligenceRunOut | null;
  base_snapshot_id: number | null;
  target_snapshot_id: number | null;
  drift_available: boolean;
  cards: ApiRoleCardOut[];
}

// Issue #59: reasoning-model explanation refresh proposals. A proposal is a
// SUGGESTION only; a developer must review and apply it to the source by hand.
export interface ExplanationRefreshProposalOut {
  id: number | null;
  node_id: number | null;
  node_type: string;
  name: string;
  entrypoint_type: string | null;
  entrypoint_id: string | null;
  path: string | null;
  qualified_name: string | null;
  drift_status: DriftStatus;
  drift_reason: string;
  changed_hashes: string[];
  old_explanation: string;
  proposed_explanation: string | null;
  proposed_metadata: Record<string, unknown> | null;
  summary_of_changes: string | null;
  confidence: number | null;
  captured_file_content_hash: string | null;
  captured_symbol_source_hash: string | null;
  captured_explanation_hash: string | null;
  current_file_content_hash: string | null;
  current_symbol_source_hash: string | null;
  current_explanation_hash: string | null;
  status: "proposed" | "failed";
  is_mock: boolean;
  provider: string;
  model: string;
  decision_method: string;
  created_at: number | null;
}

export interface ExplanationRefreshOut {
  system_id: number;
  base_snapshot_id: number | null;
  target_snapshot_id: number | null;
  intelligence_run: IntelligenceRunOut | null;
  status: "proposed" | "failed";
  error: string | null;
  review_required: boolean;
  review_note: string;
  proposal: ExplanationRefreshProposalOut | null;
}

export interface RefreshProposalRequest {
  node_id?: number | null;
  entrypoint_type?: string | null;
  entrypoint_id?: string | null;
  target_snapshot_id?: number | null;
}

export interface SymbolIndexOut {
  snapshot_id: number | null;
  system_id: number;
  symbol_count: number;
  warning_count: number;
  symbols: CodeSymbolOut[];
  warnings: string[];
  intelligence_run: IntelligenceRunOut | null;
}

export interface FeatureCodeLinkOut {
  id: number;
  system_id: number;
  snapshot_id: number;
  intelligence_run_id: number;
  feature_id: string;
  symbol: string;
  relation_reason: string;
  confidence: number;
  source: string;
  review_status: string;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  schema_version: string | null;
  is_stale: boolean;
  created_at: string;
  updated_at: string;
}

export interface FeatureCodeLinksOut {
  system_id: number;
  snapshot_id: number | null;
  intelligence_run: IntelligenceRunOut | null;
  links: FeatureCodeLinkOut[];
  is_mock: boolean;
}

export interface ProbePointOut {
  id: number;
  plan_id: number;
  system_id: number;
  component_id: string | null;
  feature_id: string;
  path: string;
  symbol: string;
  line_start: number;
  line_end: number;
  reason: string;
  recommended_mode: string;
  side_effect_risk: string;
  replayability: string;
  denylist_hit: boolean;
  status: string;
  created_at: string;
  updated_at: string;
}

export type ProbePlanOrigin = "feature_map" | "capability_map" | "flow_explorer" | "interview" | "probe_pattern" | "manual";

export interface ProbePlanOut {
  id: number;
  system_id: number;
  snapshot_id: number;
  intelligence_run_id: number;
  feature_id: string;
  objective: string;
  status: string;
  origin: ProbePlanOrigin;
  avoid_reasons: string[];
  probe_points: ProbePointOut[];
  intelligence_run: IntelligenceRunOut | null;
  is_mock: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProbePlansListOut {
  system_id: number;
  plans: ProbePlanOut[];
  is_mock: boolean;
}

// ── Probe Pattern lifecycle (Issue #168) ────────────────────────────

export type ProbePatternStatus = "active" | "stale" | "archived" | "superseded";
export type ProbePatternOrigin = "scan" | "probe_plan" | "manual";
export type ReconcileClassification =
  | "exact_match" | "moved_match" | "changed_signature"
  | "split_or_merged" | "missing" | "unsafe";
export type ReconcileUserDecision = "pending" | "accepted" | "rejected";

export interface InstrumentedProbeOut {
  path: string;
  symbol: string;
  line_start: number;
  line_end: number;
  component_id: string | null;
  docstring: string | null;
  linked_plan_id: number | null;
  linked_feature_id: string | null;
  linked_objective: string | null;
  linked_reason: string | null;
  linked_recommended_mode: string | null;
  pattern_ids: number[];
}

export interface InstrumentationScanOut {
  system_id: number;
  snapshot_id: number;
  commit_sha: string;
  probes: InstrumentedProbeOut[];
}

export interface ProbePatternPointOut {
  id: number;
  pattern_id: number;
  system_id: number;
  component_id: string;
  path: string;
  symbol: string;
  line_start: number;
  line_end: number;
  reason: string;
  recommended_mode: string;
  side_effect_risk: string;
  replayability: string;
  signature: string;
  symbol_source_hash: string | null;
  symbol_body_hash: string | null;
  docstring: string | null;
  status: "saved" | "removed_from_production";
  removed_at: number | null;
  created_at: number;
  updated_at: number;
}

export interface ProbePatternEventOut {
  id: number;
  pattern_id: number;
  event_type: string;
  detail: Record<string, unknown>;
  created_at: number;
}

export interface ReconcileEvidenceOut {
  path: string;
  start_line: number;
  end_line: number;
  summary: string;
}

export interface PatternInvestigationOut {
  summary: string;
  recommendation: string;
  proposed_target_path: string | null;
  proposed_target_symbol: string | null;
  evidence: ReconcileEvidenceOut[];
  is_mock: boolean;
  created_at: number;
}

export interface ReconcilePointOut {
  id: number;
  reconciliation_id: number;
  pattern_point_id: number;
  classification: ReconcileClassification;
  decision_method: "deterministic" | "reasoning_llm";
  target_path: string | null;
  target_symbol: string | null;
  target_line_start: number | null;
  target_line_end: number | null;
  confidence: number;
  explanation: string;
  hypothesis: string;
  question: string;
  evidence: ReconcileEvidenceOut[];
  denylist_hit: string | null;
  body_changed: boolean;
  user_decision: ReconcileUserDecision;
  decided_at: number | null;
  investigation: PatternInvestigationOut | null;
  created_at: number;
  updated_at: number;
}

export interface ProbePatternReconciliationOut {
  id: number;
  pattern_id: number;
  system_id: number;
  snapshot_id: number;
  commit_sha: string;
  intelligence_run_id: number | null;
  status: string;
  error: string | null;
  summary: Record<string, number>;
  points: ReconcilePointOut[];
  intelligence_run: IntelligenceRunOut | null;
  is_mock: boolean;
  created_at: number;
}

export interface ProbeRemovalPatchOut {
  id: number;
  pattern_id: number;
  system_id: number;
  snapshot_id: number;
  commit_sha: string;
  diff: string;
  skipped: string[];
  status: string;
  error: string | null;
  cleanup_state: string;
  cleanup_error: string | null;
  apply_status: string;
  apply_error: string | null;
  applied_at: number | null;
  applied_by_user_id: number | null;
  created_at: number;
}

export interface ProbePatternOut {
  id: number;
  system_id: number;
  name: string;
  feature_id: string;
  capability: string;
  objective: string;
  description: string;
  status: ProbePatternStatus;
  origin: ProbePatternOrigin;
  source_plan_id: number | null;
  source_snapshot_id: number | null;
  source_commit_sha: string;
  superseded_by_id: number | null;
  last_used_at: number | null;
  last_reconciled_at: number | null;
  point_count: number;
  removed_point_count: number;
  points: ProbePatternPointOut[];
  events: ProbePatternEventOut[];
  latest_reconciliation: ProbePatternReconciliationOut | null;
  pending_decision_count: number;
  created_at: number;
  updated_at: number;
}

export interface ProbePatternsListOut {
  system_id: number;
  patterns: ProbePatternOut[];
}

export interface ProbePatternPointIn {
  path: string;
  symbol: string;
  component_id?: string;
  reason?: string;
  recommended_mode?: string;
  side_effect_risk?: "low" | "medium" | "high";
  replayability?: string;
}

export interface ProbePatternCreateRequest {
  name: string;
  feature_id?: string;
  capability?: string;
  objective?: string;
  description?: string;
  origin?: ProbePatternOrigin;
  source_plan_id?: number | null;
  points: ProbePatternPointIn[];
}

// ── Flow graph explorer (Issue #43) ─────────────────────────────────

export interface EvidenceRefOut {
  path: string;
  start_line: number;
  end_line: number;
  summary: string;
}

export interface ProbePreviewOut {
  recommended_mode: string;
  captured_data: string[];
  redaction: string[];
  replayability: string;
  estimated_event_volume: string;
  side_effect_risk: "low" | "medium" | "high";
  denylist_hit: string | null;
}

export type FlowEntrypointCategory =
  | "api" | "message_queue" | "scheduled_job" | "cli" | "function";

export interface FlowEntrypointOut {
  entrypoint_type:
    | "http_route" | "public_function" | "message_queue" | "scheduled_job" | "cli";
  entrypoint_id: string;
  label: string;
  path: string;
  qualified_name: string;
  line_start: number;
  line_end: number;
  component_id: string | null;
  route_method: string | null;
  route_path: string | null;
  category: FlowEntrypointCategory;
  framework: string | null;
  operation: string | null;
  confidence: number;
  evidence: EvidenceRefOut[];
  source?: string;
}

export interface ApiScanPatternOut {
  id: number | null;
  file_glob: string;
  regex: string;
  method_group: string | null;
  path_group: string | null;
  method_constant: string | null;
  framework: string;
  language: string;
  reason: string;
  confidence: number;
  match_count: number;
  examples: EvidenceRefOut[];
}

export interface ApiScanResultOut {
  system_id: number;
  snapshot_id: number | null;
  commit_sha: string | null;
  run_id: number | null;
  status: string;
  decision_method: string;
  provider: string | null;
  model: string | null;
  is_mock: boolean;
  error: string | null;
  patterns: ApiScanPatternOut[];
  extracted_count: number;
  frameworks: string[];
  diagnostics: string[];
}

export interface EntrypointCountsOut {
  api: number;
  message_queue: number;
  scheduled_job: number;
  cli: number;
  function: number;
}

export interface FlowEntrypointsOut {
  system_id: number;
  snapshot_id: number | null;
  commit_sha: string | null;
  total: number;
  entrypoints: FlowEntrypointOut[];
  functions: FlowEntrypointOut[];
  counts: EntrypointCountsOut;
  indexed_function_count: number;
  has_backend_entrypoints: boolean;
  frameworks: string[];
  diagnostics: string[];
}

export interface FlowNodeOut {
  node_id: string;
  node_type: string;
  symbol_id: number | null;
  qualified_name: string;
  path: string;
  line_start: number;
  line_end: number;
  component_id: string | null;
  probe_capabilities: string[];
  risk: "low" | "medium" | "high";
  denylist_hit: string | null;
  evidence: EvidenceRefOut[];
  boundary_kind: string | null;
  is_external: boolean;
  trace_count: number;
  error_count: number;
  evaluation_pass: number;
  evaluation_fail: number;
  observed: boolean;
  preview: ProbePreviewOut | null;
}

export interface FlowEdgeOut {
  edge_id: string;
  source_node_id: string;
  target_node_id: string | null;
  edge_type: string;
  confidence: number;
  resolution: "resolved" | "inferred" | "unresolved";
  callee_name: string;
  line: number;
  evidence: EvidenceRefOut[];
  preview: ProbePreviewOut | null;
}

export interface CandidateFlowOut {
  flow_id: string;
  title: string;
  summary: string;
  entrypoint_node_id: string;
  node_ids: string[];
  node_count: number;
  max_depth: number;
  confidence: number;
  unresolved_edge_count: number;
  external_boundary_count: number;
  observed_node_count: number;
  unobserved_node_ids: string[];
}

export interface FlowGraphOut {
  system_id: number;
  snapshot_id: number;
  commit_sha: string;
  entrypoint: FlowEntrypointOut;
  nodes: FlowNodeOut[];
  edges: FlowEdgeOut[];
  candidate_paths: CandidateFlowOut[];
  diagnostics: string[];
  truncated: boolean;
}

export interface FlowProbeSelection {
  target_type: "node" | "edge";
  node_id?: string;
  edge_id?: string;
  observation: "input" | "output" | "boundary";
  mode_preference: "trace" | "shadow" | "off";
}

export interface ValidationCommandOut {
  id: number;
  command: string;
  exit_code: number | null;
  duration_ms: number | null;
  stdout: string;
  stderr: string;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
  timed_out: boolean;
}

export interface ValidationRunOut {
  id: number;
  patch_id: number;
  system_id: number;
  variant: string;
  worktree_path: string | null;
  overall_success: boolean;
  total_duration_ms: number | null;
  trace_received: boolean;
  trace_status: string | null;
  network_isolation: boolean;
  cleanup_state: string | null;
  cleanup_error: string | null;
  commands: ValidationCommandOut[];
  error: string | null;
  created_at: string;
}

export interface ProbePatchOut {
  id: number;
  plan_id: number;
  system_id: number;
  snapshot_id: number;
  commit_sha: string;
  diff: string;
  worktree_path: string | null;
  skipped: string[];
  status: string;
  error: string | null;
  cleanup_state: string | null;
  cleanup_error: string | null;
  apply_status: string;
  apply_error: string | null;
  applied_at: string | null;
  applied_by_user_id: number | null;
  validation_runs: ValidationRunOut[];
  created_at: string;
}

export interface GenerationRun {
  id: number;
  system_id: number;
  component_id: string;
  trace_id: string;
  objective: string;
  input_json: string | null;
  current_output: string | null;
  generated_code: string | null;
  generation_notes: string | null;
  candidate_output: string | null;
  execution_error: string | null;
  llm_verdict: string | null;
  llm_reason: string | null;
  llm_risks: string | null;
  llm_recommendation: string | null;
  created_at: string;
}

export interface ExperimentVariantCreate {
  label: string;
  patch_text: string;
  source?: string;
  risk_note?: string;
}

export interface ExperimentCreate {
  feature_id: string;
  objective: string;
  snapshot_id: number;
  variants: ExperimentVariantCreate[];
}

export interface ExperimentVariantResultOut {
  id: number;
  variant_key: string;
  label: string;
  is_baseline: boolean;
  patch_text: string | null;
  patch_hash: string | null;
  source: string;
  risk_note: string;
  status: string;
  error: string | null;
  workspace_path: string | null;
  cleanup_state: string | null;
  cleanup_error: string | null;
  metrics: Record<string, unknown> | null;
  artifacts: Record<string, unknown> | null;
  commands: ValidationCommandOut[];
  started_at: string | null;
  completed_at: string | null;
}

export interface ExperimentOut {
  id: number;
  system_id: number;
  feature_id: string;
  objective: string;
  snapshot_id: number;
  baseline_commit: string | null;
  config_revision: number;
  execution: Record<string, unknown> | null;
  status: string;
  error: string | null;
  human_decision: string | null;
  human_decision_variant_key: string | null;
  human_decision_note: string | null;
  variants: ExperimentVariantResultOut[];
  comparison: Record<string, unknown> | null;
  analysis: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface EvaluationCriterion {
  id: number;
  component_id: string;
  name: string;
  description: string;
  criterion_type: string;
  expected_value: string;
  weight: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface EvaluationResult {
  id: number;
  trace_id: string;
  component_id: string;
  criterion_id: number;
  status: string;
  score: number | null;
  reason: string;
  actual_output: string | null;
  expected_value: string | null;
  created_at: string;
}

// Issue #94/#275: corrected to match the actual GET/PUT /system-profile
// contract (`app/models.py`'s `SystemProfile`/`SystemProfileUpdate`), where
// target_users/constraints/success_criteria are string arrays. This type was
// previously out of sync (typed as plain strings); left unused elsewhere in
// the dashboard prior to this issue.
export interface SystemProfile {
  name: string;
  purpose: string;
  target_users: string[];
  stakeholder_value: string;
  constraints: string[];
  success_criteria: string[];
  created_at?: number | null;
  updated_at?: number | null;
}

// --- Decision Workspace (Issues #35-#37) ------------------------------------

export type WorkspaceContextItemType = "feature" | "component" | "trace" | "experiment" | "probe_plan" | "analyzer_run";
export type WorkspaceProposalStatus = "proposed" | "accepted" | "rejected" | "deferred" | "superseded";

export interface WorkspaceOut {
  id: number;
  system_id: number;
  title: string;
  focus: string;
  status: string;
  summary: string;
  created_at: number;
  updated_at: number;
}

export interface WorkspaceContextItemOut {
  id: number;
  workspace_id: number;
  item_type: string;
  item_id: string;
  label: string;
  created_at: number;
}

export interface WorkspaceMessageOut {
  id: number;
  workspace_id: number;
  role: string;
  content: string;
  context_metadata: Record<string, unknown>;
  created_at: number;
}

export interface WorkspaceDecisionOut {
  id: number;
  proposal_id: number;
  decision: string;
  reason: string;
  decided_by_user_id: number | null;
  created_at: number;
}

export interface WorkspaceProposalOut {
  id: number;
  workspace_id: number;
  message_id: number | null;
  proposal_type: string;
  title: string;
  body: Record<string, unknown>;
  status: WorkspaceProposalStatus;
  decisions: WorkspaceDecisionOut[];
  created_at: number;
  updated_at: number;
}

export interface WorkspaceDetailOut extends WorkspaceOut {
  messages: WorkspaceMessageOut[];
  context_items: WorkspaceContextItemOut[];
  proposals: WorkspaceProposalOut[];
}

export interface WorkspaceEvidenceRef {
  source_type: string;
  source_id: string;
  snapshot_id: number | null;
  commit_sha: string | null;
  path: string | null;
  start_line: number | null;
  end_line: number | null;
  summary: string;
}

export interface WorkspaceContextPack {
  system: { system_id: number; name: string; environment: string; purpose: string; target_users: string };
  focus: { title: string; focus: string; summary: string } | null;
  repository: { snapshot_id: number; commit_sha: string; repo_path: string; file_count: number; status: string } | null;
  features: Array<{ feature_id: string; name: string; summary: string; evidence: WorkspaceEvidenceRef[] }>;
  components: Array<{ component_id: string; purpose: string; responsibility: string; evidence: WorkspaceEvidenceRef[] }>;
  traces: Array<{ component_id: string; trace_count: number; error_count: number; evidence: WorkspaceEvidenceRef[] }>;
  evaluations: Array<{ component_id: string; passed_count: number; failed_count: number; top_failure_reasons: string[]; evidence: WorkspaceEvidenceRef[] }>;
  probe_plans: Array<{ plan_id: number; feature_id: string; objective: string; status: string; evidence: WorkspaceEvidenceRef[] }>;
  experiments: Array<{ experiment_id: number; feature_id: string; objective: string; status: string; evidence: WorkspaceEvidenceRef[] }>;
  human_decisions: Array<{ source_type: string; source_id: string; decision: string; variant_key: string | null; note: string }>;
  evidence: WorkspaceEvidenceRef[];
  missing_information: string[];
}

export interface WorkspaceAgentTurnOut {
  user_message: WorkspaceMessageOut;
  assistant_message: WorkspaceMessageOut | null;
  proposals: WorkspaceProposalOut[];
  error: string | null;
}

export interface WorkspaceProposalDraftOut {
  id: number;
  workspace_id: number;
  proposal_id: number;
  system_id: number;
  draft_type: "probe_plan_draft" | "experiment_draft";
  target_screen: "probe_planner" | "experiments";
  payload: {
    system_id?: number;
    feature_id?: string | null;
    focus?: string | null;
    objective?: string;
    target_components?: string[];
    variant_summaries?: string[];
    snapshot_id?: number | null;
    constraints?: string[];
    observation_points?: string[];
    evaluation_criteria?: string[];
    context_refs?: Record<string, unknown>[];
    evidence_refs?: Record<string, unknown>[];
  };
  missing_fields: string[];
  created_at: number;
}

// System Understanding (Issue #86 / #87)

export interface SystemUnderstandingPipelineStep {
  step: string;
  status: "complete" | "missing" | "warning" | "blocked" | "failed";
  detail?: string | null;
  /** Issue #240: server-provided display label (Japanese) for this step. */
  label?: string;
}

// Still used by SystemUnderstandingStageStatus.stage (the 4 Hub stages).
// Issue #239 removed SystemUnderstandingNextAction / NextActionKind, which
// used to be this type's only other consumer (the deprecated top-level
// next_actions / primary_action fields).
export type NextActionCategory = "understand" | "observe" | "instrument" | "evaluate";

export interface SystemUnderstandingGapSummary {
  gap_type: string;
  count: number;
}

export interface SystemUnderstandingMetadataCoverage {
  symbol_count: number;
  symbols_with_source_metadata: number;
  entrypoint_count: number;
  entrypoints_with_capability_link: number;
}

export interface SystemUnderstandingCapability {
  name: string;
  summary?: string | null;
  provenance_kind?: string | null;
}

export interface SystemUnderstandingEntrypoint {
  entrypoint_type: string;
  entrypoint_id: string;
  category?: string | null;
  label?: string | null;
}

export interface SystemUnderstandingSymbol {
  path: string;
  qualified_name: string;
  kind?: string | null;
  route_path?: string | null;
  route_method?: string | null;
  component_id?: string | null;
}

export interface SystemUnderstandingPurpose {
  name: string;
  summary?: string | null;
  provenance_kind?: string | null;
}

// Issue #94/#275: side-by-side human vs AI/source-derived System Purpose.
// `system_profile` (provenance_kind "manual") is present whenever the human
// profile has a purpose, even with no snapshot; the AI view
// (capability_hierarchy or system_profile_draft) is present only when a
// ready snapshot has one.
export type SystemUnderstandingPurposeViewSource =
  | "system_profile"
  | "capability_hierarchy"
  | "system_profile_draft";

export interface SystemUnderstandingPurposeView {
  source: SystemUnderstandingPurposeViewSource;
  provenance_kind: string;
  name: string;
  summary?: string | null;
  updated_at?: number | null;
}

export type PurposeConfirmationStaleReason =
  | "profile_updated"
  | "snapshot_changed"
  | "ai_updated";

export interface PurposeConfirmationOut {
  id: number;
  snapshot_id: number;
  understanding_build_id?: number | null;
  decided_by_user_id?: number | null;
  decision_method: string;
  manual_purpose: string;
  ai_purpose_name?: string | null;
  ai_purpose_summary?: string | null;
  ai_source?: string | null;
  ai_provenance_kind?: string | null;
  note?: string | null;
  created_at: number;
  stale: boolean;
  stale_reason?: PurposeConfirmationStaleReason | null;
}

export interface PurposeConfirmationRequest {
  snapshot_id?: number;
  understanding_build_id?: number;
  note?: string;
}

export interface SystemUnderstandingGapNextAction {
  action: string;
  link?: string | null;
}

export interface SystemUnderstandingGapDocRef {
  path: string;
  start_line?: number | null;
  end_line?: number | null;
}

export interface SystemUnderstandingGapSymbolRef {
  path?: string | null;
  qualified_name?: string | null;
}

export interface SystemUnderstandingGapEntrypointRef {
  entrypoint_type?: string | null;
  entrypoint_ref?: string | null;
}

export interface IssueDraftRef {
  id: number;
  status: string;
  external_url?: string | null;
  title: string;
}

export type GapTriageStatus = "open" | "acknowledged" | "dismissed" | "resolved";
export type GapTriageDecisionMethod = "manual" | "deterministic";
export type GapTriageReopenReason = "content_changed" | "resolved_gap_reappeared";

export interface GapTriageDecision {
  id: number;
  system_id: number;
  snapshot_id?: number | null;
  gap_key: string;
  content_fingerprint: string;
  status: GapTriageStatus;
  decided_by_user_id?: number | null;
  decision_method: GapTriageDecisionMethod;
  note?: string | null;
  created_at: number;
}

export interface GapTriageUpdateRequest {
  gap_key: string;
  content_fingerprint: string;
  status: GapTriageStatus;
  note?: string | null;
}

export interface SystemUnderstandingGap {
  gap_type?: string | null;
  severity: string;
  title?: string | null;
  node_name?: string | null;
  notes?: string | null;
  capability_key?: string | null;
  doc_refs: SystemUnderstandingGapDocRef[];
  symbol_refs: SystemUnderstandingGapSymbolRef[];
  entrypoint_refs: SystemUnderstandingGapEntrypointRef[];
  code_refs: Array<Record<string, unknown>>;
  next_actions: SystemUnderstandingGapNextAction[];
  // Issue #107
  source_id?: string | null;
  source_key?: string | null;
  issue_drafts?: IssueDraftRef[];
  // Issue #276. Optional only for backward-compatible fixtures; current
  // server responses always provide both identities and effective status.
  gap_key?: string;
  content_fingerprint?: string;
  triage_status?: GapTriageStatus;
  triage_decision?: GapTriageDecision | null;
  triage_reopen_reason?: GapTriageReopenReason | null;
}

// Issue #107: issue drafts generated from System Understanding gaps.
export type IssueDraftStatus =
  | "draft"
  | "copied"
  | "external_created"
  | "closed"
  | "rejected";

export interface IssueDraft {
  id: number;
  system_id: number;
  snapshot_id?: number | null;
  commit_sha?: string | null;
  source_type: string;
  source_key?: string | null;
  gap_type?: string | null;
  severity?: string | null;
  node_name?: string | null;
  title: string;
  body_markdown: string;
  status: IssueDraftStatus;
  external_url?: string | null;
  // Issue #158: computed at read time — true when the draft's snapshot/commit is
  // behind the latest ready snapshot.
  stale?: boolean;
  created_at: number;
  updated_at: number;
}

export interface IssueDraftCreateRequest {
  source_type?: string;
  gap: SystemUnderstandingGap;
  snapshot_id?: number | null;
  commit_sha?: string | null;
}

export interface IssueDraftUpdateRequest {
  title?: string;
  body_markdown?: string;
  status?: IssueDraftStatus;
  external_url?: string;
}

// Issue #158: whether draft -> GitHub issue creation is available for the
// current system's configured repository.
export interface GitHubIssueStatus {
  available: boolean;
  owner?: string | null;
  repo?: string | null;
  reason?: string | null;
}

// Issue #202: finite completion status shown as a badge for each Hub stage.
export type SystemUnderstandingStageStatusValue =
  | "not_started"
  | "in_progress"
  | "blocked"
  | "complete";

export interface SystemUnderstandingStageStatus {
  stage: NextActionCategory;
  status: SystemUnderstandingStageStatusValue | string;
  counts: Record<string, number>;
  // Issue #240: server-supplied Japanese display copy. Optional so existing
  // fixtures/mocks that predate these fields keep working; the UI prefers
  // them over its local STAGE_LABELS/STAGE_DESCRIPTIONS fallback.
  label?: string;
  description?: string;
}

// Issue #203: before/after gap counts across the last two settled builds.
export interface SystemUnderstandingGapTrend {
  gap_type: string;
  current: number;
  previous: number;
}

export interface SystemUnderstandingOut {
  system_id: number;
  snapshot_id: number | null;
  understanding_build_id?: number | null;
  commit_sha: string | null;
  pipeline: SystemUnderstandingPipelineStep[];
  purpose: SystemUnderstandingPurpose | null;
  capabilities: SystemUnderstandingCapability[];
  entrypoints: SystemUnderstandingEntrypoint[];
  major_symbols: SystemUnderstandingSymbol[];
  gaps: SystemUnderstandingGap[];
  gap_summary: SystemUnderstandingGapSummary[];
  metadata_coverage: SystemUnderstandingMetadataCoverage | null;
  // Issue #202: per-stage completion status + counts. Optional so existing
  // fixtures/mocks that predate this field keep working (backward compat).
  stages?: SystemUnderstandingStageStatus[];
  // Issue #203: gap-count trend across the last two settled builds (empty
  // until 2 builds have recorded history). Optional for backward compat with
  // fixtures/mocks that predate this field.
  gap_trend?: SystemUnderstandingGapTrend[];
  // Issue #201's `primary_action`, Issue #174's `next_actions`, and Issue
  // #203's `understanding_refresh_recommended` were removed in Issue #239.
  // The canonical "what should the user do next" projection is now
  // `GET /system-state`'s `primary_item` / `page_items` (see useSystemState
  // in api/hooks.ts and SystemStateBanner in components/system-state.tsx).
  // Issue #240: server-supplied Japanese success summary shown when the whole
  // pipeline is complete (null/absent otherwise). Optional for backward
  // compat with fixtures that predate it.
  success_summary?: string | null;
  // Issue #94/#275: human (system_profile) and AI/source-derived
  // (capability_hierarchy or system_profile_draft) purpose views shown side
  // by side. Optional for backward compat with fixtures/mocks that predate
  // this field.
  purpose_views?: SystemUnderstandingPurposeView[];
  // Issue #94/#275: the latest human confirmation that the manual and
  // AI/source-derived purposes were checked against each other, or null if
  // none has been recorded yet. Optional for backward compat.
  purpose_confirmation?: PurposeConfirmationOut | null;
}

// Capability context: gaps / probe plans / experiments linked to one
// capability_key by exact key match only (Issue #175).

export interface CapabilityContextProbePlanOut {
  id: number;
  feature_id: string;
  objective: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface CapabilityContextExperimentOut {
  id: number;
  feature_id: string;
  objective: string;
  status: string;
  human_decision: string;
  human_decision_variant_key: string | null;
  created_at: string;
}

export interface CapabilityContextOut {
  capability_key: string;
  gaps: SystemUnderstandingGap[];
  probe_plans: CapabilityContextProbePlanOut[];
  experiments: CapabilityContextExperimentOut[];
}

// System Understanding build job orchestration (Issue #109)

export interface SystemUnderstandingBuildStep {
  id: number;
  step: string;
  status: "pending" | "running" | "completed" | "failed" | "blocked" | "cancelled";
  depends_on: string[];
  reused_existing: boolean;
  cancel_requested: boolean;
  error: string | null;
  artifact_provenance: Record<string, unknown>;
  duration_ms: number | null;
  heartbeat_at: number | null;
  started_at: number | null;
  completed_at: number | null;
}

export interface SystemUnderstandingLlmTaskSummary {
  total: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
  reused: number;
}

export interface SystemUnderstandingArtifactCounts {
  symbols: number;
  entrypoints: number;
  understanding_graph_claims: number;
  capability_hierarchy_nodes: number;
}

export interface SystemUnderstandingBuildOut {
  id: number;
  job_id: number;
  /** Latest execution (initial enqueue or retry) of this job. */
  run_id: number | null;
  system_id: number;
  snapshot_id: number | null;
  /** "completed" only when every step completed; remaining blocked/
   * cancelled/failed steps yield "partial" ("failed" when nothing completed). */
  status: "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
  current_step: string | null;
  error: string | null;
  cancel_requested: boolean;
  is_stuck: boolean;
  heartbeat_at: number | null;
  started_at: number | null;
  completed_at: number | null;
  created_at: number;
  steps: SystemUnderstandingBuildStep[];
  llm_tasks: SystemUnderstandingLlmTaskSummary | null;
  artifact_counts: SystemUnderstandingArtifactCounts | null;
}

// System settings diagnostics (Issue #101)

export type DiagnosticSeverity = "ok" | "warning" | "error" | "blocked" | "unknown";

export interface DiagnosticLastObservedError {
  source: string;
  status: string;
  error?: string | null;
  observed_at?: number | null;
}

export interface SystemDiagnosticCheck {
  check_id: string;
  category: string;
  title: string;
  severity: DiagnosticSeverity;
  detail: string;
  impact: string;
  remediation: string;
  related_env: string[];
  related_paths: string[];
  related_pages: string[];
  related_pipeline_steps: string[];
  last_observed_error?: DiagnosticLastObservedError | null;
  decision_method: "deterministic";
  // Issue #115: where the user fixes the problem.
  fix_kind: "navigate" | "dialog";
  fix_page?: string | null;
  fix_anchor?: string | null;
}

export interface SystemDiagnosticsOut {
  system_id: number;
  generated_at: number;
  overall_severity: DiagnosticSeverity;
  severity_counts: Record<string, number>;
  checks: SystemDiagnosticCheck[];
}

// Per-screen assistant (Issue #102)

export interface AssistantSettingMetadata {
  key: string;
  display_name: string;
  category: string;
  requiredness: "required" | "conditional" | "optional";
  description: string;
  impact: string;
  remediation: string;
  valid_values?: string[] | null;
  validation_rule: string;
  related_checks: string[];
  related_pages: string[];
  related_pipeline_steps: string[];
  docs_link: string;
  decision_method: "deterministic";
}

export interface AssistantSettingsMetadataOut {
  settings: AssistantSettingMetadata[];
}

export interface AssistantSuggestedQuestion {
  question: string;
  source: "diagnostics" | "static";
  check_id: string;
}

export interface AssistantScreenContext {
  screen_id: string;
  title: string;
  route: string;
  purpose: string;
  primary_data_sources: string[];
  visible_sections: string[];
  common_questions: string[];
  related_settings: string[];
  related_checks: string[];
  related_pipeline_steps: string[];
  related_endpoints: string[];
  state_severity: DiagnosticSeverity;
  screen_checks: SystemDiagnosticCheck[];
  suggested_questions: AssistantSuggestedQuestion[];
}

export interface AssistantAskRequest {
  screen_id: string;
  question: string;
  route_params?: Record<string, string>;
  visible_check_ids?: string[];
  visible_state_ids?: string[];
  focused_state_id?: string;
}

export interface AssistantAction {
  label: string;
  kind: "navigate" | "configure" | "operate";
  target: string;
  detail: string;
}

export interface AssistantCitation {
  type: "setting" | "diagnostic_check" | "pipeline_step" | "state_item";
  id: string;
  title: string;
  detail: string;
}

export interface AssistantAskOut {
  screen_id: string;
  answer: string;
  suggested_actions: AssistantAction[];
  citations: AssistantCitation[];
  used_fallback: boolean;
  fallback_reason?: string | null;
  decision_method: "deterministic" | "reasoning_llm";
  provider: string;
  model: string;
  prompt_version: string;
  schema_version: string;
  generated_at: number;
}

// ── GitHub App publish workflow (Issue #216) ────────────────────────
//
// Connection/publish-job persistence never carries an installation token,
// private key, or absolute host path (Principle 5/8) -- these types mirror
// exactly what apps/control-server/app/models.py returns.

export interface GithubAppStatusOut {
  configured: boolean;
  app_id: string | null;
  api_base_url: string;
  web_base_url: string;
  allowed_organization: string | null;
}

export type GithubInstallationStatus = "active" | "disabled";

export interface GithubInstallationOut {
  installation_id: number;
  github_account_login: string;
  github_account_type: string;
  status: GithubInstallationStatus;
  registered_by_user_id: number | null;
  verified_at: string;
  disabled_by_user_id: number | null;
  disabled_at: string | null;
  created_at: string;
  updated_at: string;
  assigned_system_ids: number[];
}

export type GithubConnectionStatus = "pending" | "connected" | "error" | "disconnected";

export interface GithubConnectionOut {
  id: number;
  system_id: number;
  api_base_url: string;
  web_base_url: string;
  owner: string;
  repo: string;
  clone_url: string;
  installation_id: number;
  default_branch: string | null;
  credential_type: string;
  status: GithubConnectionStatus;
  last_error: string | null;
  last_synced_at: string | null;
  last_synced_commit_sha: string | null;
  created_by_user_id: number | null;
  updated_by_user_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface GithubConnectionCreateRequest {
  owner: string;
  repo: string;
  installation_id: number;
  api_base_url?: string;
  web_base_url?: string;
}

export interface GithubRepositoryStatusOut {
  connection_id: number;
  mirror_exists: boolean;
  mirror_path: string | null;
  default_branch: string | null;
  last_synced_at: string | null;
  last_synced_commit_sha: string | null;
}

export interface GithubInstallationRepositoryOut {
  owner: string;
  name: string;
  default_branch: string | null;
  private: boolean;
}

export type PublishJobStatus =
  | "pending"
  | "authenticating"
  | "fetching"
  | "checking_out"
  | "applying_patch"
  | "validating"
  | "awaiting_approval"
  | "committing"
  | "pushing"
  | "creating_pr"
  | "completed"
  | "failed"
  | "cancelled"
  // Issue #226: resting/active states a publish-phase failure or a retry
  // can land in. Not terminal -- only retry/cancel/disconnect move a job
  // out of them.
  | "retryable_failed"
  | "reconciling"
  | "manual_intervention_required";

export interface PublishJobOut {
  id: number;
  system_id: number;
  connection_id: number;
  patch_id: number;
  snapshot_id: number;
  base_branch: string;
  base_commit_sha: string | null;
  branch_name: string | null;
  commit_sha: string | null;
  pr_url: string | null;
  pr_number: number | null;
  status: PublishJobStatus;
  error: string | null;
  validation_summary: Record<string, unknown> | null;
  requested_by_user_id: number | null;
  approved_by_user_id: number | null;
  cleanup_state: string;
  cleanup_error: string | null;
  created_at: number;
  updated_at: number;
  approved_at: number | null;
  completed_at: number | null;
  heartbeat_at: number | null;
  retry_count: number;
  last_attempt_at: number | null;
}

// Append-only audit trail entry (Issues #227/#226) -- never a token or path.
export interface PublishAuditEventOut {
  id: number;
  job_id: number | null;
  connection_id: number | null;
  event_type: string;
  actor_user_id: number | null;
  detail: Record<string, unknown> | null;
  created_at: number;
}

// ── Replay / Simulation (Issue #242, Phase D Workbench UI / #246) ──────────
// Field names mirror app/models.py exactly (Phases A-C: #243-#245).

export type ReplayInputSource = "structured" | "repr_partial";
export type ReplaySkipReason =
  | "unreplayable_capture"
  | "repr_parse_failed"
  | "undecodable_input"
  | "trace_missing";
export type ReplaySetSourceKind = "manual" | "analyzer_run";
export type ReplayApprovalStatus = "approved" | "revoked";

export interface ReplaySetTraceOut {
  trace_id: string;
  exists: boolean;
  replayability: Replayability | null;
  replay_reasons: string[];
  // The input_source/skip_reason a replay run would deterministically use
  // for this trace (same rule as the runner) -- drives the Workbench badges.
  input_source: ReplayInputSource | null;
  skip_reason: ReplaySkipReason | null;
}

export interface ReplaySetOut {
  id: number;
  system_id: number;
  component_id: string;
  name: string;
  source: ReplaySetSourceKind;
  source_analyzer_run_id: number | null;
  trace_ids: string[];
  traces: ReplaySetTraceOut[];
  created_at: number;
}

export interface ReplayRiskPointOut {
  point_id: number;
  plan_id: number;
  side_effect_risk: string | null;
  replayability: string | null;
}

export interface ReplayRiskContextOut {
  probe_plan_points: ReplayRiskPointOut[];
  warning: string;
}

export interface ReplayApprovalOut {
  id: number;
  system_id: number;
  component_id: string;
  status: ReplayApprovalStatus;
  reason: string;
  approved_by_user_id: number | null;
  decision_method: string;
  risk_context: Record<string, unknown> | null;
  created_at: number;
  revoked_at: number | null;
  revoked_by_user_id: number | null;
}

export interface ReplayApprovalStateOut {
  component_id: string;
  active: boolean;
  approval: ReplayApprovalOut | null;
  risk_context: ReplayRiskContextOut;
}

export type ReplayVariantCaseStatus =
  | "match"
  | "diff"
  | "candidate_error"
  | "error_to_success"
  | "error_to_same_error"
  | "error_to_different_error"
  | "skipped";
export type ReplayVariantComparisonMode = "structured" | "repr";
export type ReplayVariantSource = "manual" | "pasted" | "llm_draft";
export type ReplayVariantApplyStatus = "applied" | "invalid_patch" | "not_applicable";
export type ReplayVariantRunStatus = "running" | "completed" | "failed";
export type ReplayVariantDraftStatus = "proposed" | "failed";

export interface ReplayVariantCaseResultOut {
  id: number;
  trace_id: string;
  position: number;
  case_status: ReplayVariantCaseStatus;
  comparison_mode: ReplayVariantComparisonMode | null;
  baseline_output: string | null;
  candidate_output: string | null;
  candidate_error: string | null;
  recorded_error: string | null;
  duration_ms: number | null;
  duration_delta_ms: number | null;
  field_diffs: string[];
  output_truncated: boolean;
  created_at: number;
}

export interface ReplayVariantAggregateOut {
  match: number;
  diff: number;
  candidate_error: number;
  error_to_success: number;
  error_to_same_error: number;
  error_to_different_error: number;
  skipped: number;
  total: number;
  avg_duration_delta_ms: number | null;
  examples: Record<string, string[]>;
}

export interface ReplayVariantOut {
  id: number;
  replay_run_id: number;
  variant_key: string;
  label: string;
  is_baseline: boolean;
  patch_text: string;
  patch_hash: string;
  source: string;
  apply_status: ReplayVariantApplyStatus;
  apply_error: string | null;
  status: ReplayVariantRunStatus;
  error: string | null;
  workspace_path: string | null;
  cleanup_state: string;
  cleanup_error: string | null;
  aggregate: ReplayVariantAggregateOut;
  cases: ReplayVariantCaseResultOut[];
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
}

export interface ReplayVariantRunOut {
  id: number;
  system_id: number;
  replay_set_id: number;
  component_id: string;
  snapshot_id: number;
  commit_sha: string;
  symbol_path: string;
  symbol_qualified_name: string;
  status: ReplayVariantRunStatus;
  error: string | null;
  trace_set_hash: string;
  sandbox_config: Record<string, unknown>;
  approval_id: number | null;
  variants: ReplayVariantOut[];
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
}

export interface ReplayVariantDraftOut {
  id: number;
  system_id: number;
  replay_set_id: number;
  component_id: string;
  trace_id: string;
  objective: string;
  snapshot_id: number;
  symbol_path: string;
  symbol_qualified_name: string;
  generated_code: string;
  patch_text: string;
  patch_hash: string;
  notes: string;
  status: ReplayVariantDraftStatus;
  error: string | null;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  schema_version: string | null;
  decision_method: "deterministic" | "reasoning_llm" | "manual" | null;
  is_mock: boolean;
  created_at: number;
}

export interface ReplayVariantExperimentPayloadOut {
  label: string;
  patch_text: string;
  patch_hash: string;
  source: string;
  risk_note: string;
  origin: Record<string, unknown>;
}

export interface ReplayRegressionScaffoldOut {
  id: number;
  intelligence_run_id: number;
  replay_run_id: number;
  replay_variant_id: number;
  replay_set_id: number;
  trace_id: string;
  snapshot_id: number;
  scaffold_text: string;
  status: "proposed" | "failed";
  error: string | null;
  provider: string;
  model: string;
  prompt_version: string;
  schema_version: string;
  decision_method: "reasoning_llm";
  is_mock: boolean;
  created_at: number;
}

// Two small deterministic backend helpers for the Workbench's Direct-edit
// flow (Issue #246): read the pinned-snapshot source, then diff an edited
// copy of it. No judgement -- structural reads/text diffing only.
export interface ReplaySourceOut {
  replay_set_id: number;
  component_id: string;
  snapshot_id: number;
  commit_sha: string;
  path: string;
  qualified_name: string;
  start_line: number;
  end_line: number;
  source: string;
}

export interface ReplaySourceDiffOut {
  patch_text: string;
  patch_hash: string;
}

// ── AI Candidate Studio (Issue #252) ────────────────────────────────────────
// A conversation + candidate-versioning layer over the EXISTING isolated
// Replay stack (#243-#246): generation is a reasoning-model structured
// proposal + deterministic splice->diff (fail-closed); replaying a version
// reuses POST /replay-variant-runs verbatim (approval gate, sandbox, diff
// matrix); promotion reuses the variant experiment-payload shape and never
// creates an experiment/merges/deploys (Principle 7). Field names mirror
// app/models.py exactly.

export type CandidateSessionStatus = "active" | "archived";
export type CandidateMessageRole = "user" | "assistant";
export type CandidateVersionStatus = "proposed" | "failed";
export type CandidateReplayStatus = "not_run" | "running" | "completed" | "failed";

export interface CandidateMessageOut {
  id: number;
  session_id: number;
  role: CandidateMessageRole;
  content: string;
  version_id: number | null;
  created_at: number;
}

export interface CandidateVersionOut {
  id: number;
  system_id: number;
  session_id: number;
  parent_version_id: number | null;
  version_number: number;
  instruction: string;
  status: CandidateVersionStatus;
  summary: string;
  assumptions: string[];
  changed_symbols: string[];
  risks: string[];
  suggested_tests: string[];
  generated_code: string;
  patch_text: string;
  patch_hash: string;
  error: string | null;
  replay_status: CandidateReplayStatus;
  replay_run_id: number | null;
  replay_variant_id: number | null;
  promoted_at: number | null;
  // Reasoning provenance (from the linked intelligence_runs row).
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  schema_version: string | null;
  decision_method: "deterministic" | "reasoning_llm" | "manual" | null;
  is_mock: boolean;
  created_at: number;
}

export interface CandidateSessionOut {
  id: number;
  system_id: number;
  component_id: string;
  snapshot_id: number;
  commit_sha: string;
  symbol_path: string;
  symbol_qualified_name: string;
  replay_set_id: number;
  objective: string;
  status: CandidateSessionStatus;
  created_at: number;
  updated_at: number;
  // Empty on the list endpoint (GET /candidate-sessions); populated on the
  // single-session endpoint (GET /candidate-sessions/{id}).
  messages: CandidateMessageOut[];
  versions: CandidateVersionOut[];
}

export interface CandidateSessionCreateRequest {
  component_id: string;
  // With no replay/trace selection, the server creates a set from up to 50
  // recent traces for the component.
  replay_set_id?: number;
  trace_ids?: string[];
  trace_id?: string;
  snapshot_id?: number;
  objective?: string;
}

export interface CandidatePromotionOut {
  candidate_version_id: number;
  label: string;
  patch_text: string;
  patch_hash: string;
  source: string;
  risk_note: string;
  origin: Record<string, unknown>;
}

export interface CandidateEventOut {
  version_id: number;
  version_number: number;
  phase: "generating" | "validating_patch" | "completed" | "failed" | "replaying";
  status: string;
  replay_status: CandidateReplayStatus;
  detail: string;
  created_at: number;
}

export interface CandidateEventsOut {
  session_id: number;
  events: CandidateEventOut[];
}
