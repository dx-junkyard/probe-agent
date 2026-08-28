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
  // Issue #367: what the Control Server masked at ingestion. `null` means the
  // row predates ingestion-time redaction and has never been scanned -- which
  // is NOT the same as `{redacted: false}` (scanned, nothing found).
  redaction?: TraceRedaction | null;
  payload_summary?: TracePayloadSummary | null;
}

// Finite rule vocabulary, mirroring probe_agent.secret_patterns.RULE_NAMES
// plus the structural rules the server applies (`sensitive_key`, a denylisted
// mapping key; `depth_limit`, a node past the traversal bound).
export type TraceRedactionRule =
  | "sensitive_key"
  | "depth_limit"
  | "pem_private_key"
  | "aws_access_key_id"
  | "github_fine_grained_pat"
  | "github_token"
  | "anthropic_api_key"
  | "stripe_secret_key"
  | "openai_api_key"
  | "slack_token"
  | "google_api_key"
  | "jwt"
  | "authorization_header"
  | "url_userinfo"
  | "sensitive_assignment";

export interface TraceRedactionField {
  field: "input" | "output" | "error" | "input_capture";
  path: string;
  rule: TraceRedactionRule;
}

export interface TraceRedaction {
  redacted: boolean;
  rules: TraceRedactionRule[];
  fields: TraceRedactionField[];
}

export type TracePayloadKind =
  | "none" | "boolean" | "string" | "number" | "list" | "object" | "unknown";

export interface TracePayloadShape {
  kind: TracePayloadKind;
  item_count: number | null;
  bytes: number;
}

// Shape facts shown before a reader expands a trace's payload (Issue #367 AC:
// 型・件数・サイズ・redaction有無を先に確認できる).
export interface TracePayloadSummary {
  input: TracePayloadShape;
  output: TracePayloadShape;
  error: TracePayloadShape;
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
  // Issue #370: the live reception axis. `state` above is a cumulative
  // milestone that never regresses ("has connected at least once"); this is
  // the operational reading and does regress. Never render one as the other.
  /** Workload freshness, from the newest NON-smoke trace. */
  freshness: ConnectivityFreshness;
  /** Transport freshness, from the newest trace of any kind. */
  transport_freshness: ConnectivityFreshness;
  last_real_trace_at: number | null;
  /** Seconds since the newest non-smoke trace — the event `freshness` judged. */
  seconds_since_last_trace: number | null;
  seconds_since_last_any_trace: number | null;
  /** Server clock at evaluation, so relative times do not drift with ours. */
  evaluated_at: number;
  clock_skew_seconds: number;
  real_trace_count_5m: number;
  real_trace_count_1h: number;
  real_trace_count_24h: number;
  delayed_after_seconds: number;
  stale_after_seconds: number;
  thresholds_customized: boolean;
}

export type ConnectivityFreshness =
  | "never_received"
  | "receiving_now"
  | "delayed"
  | "stale";

export interface ConnectivityFreshnessPolicyOut {
  system_id: number;
  delayed_after_seconds: number;
  stale_after_seconds: number;
  customized: boolean;
  updated_at: number | null;
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

/**
 * Issue #368: the token's lifecycle status is decided by the Control Server
 * (`app/token_status.py`) from `revoked` + `expires_at` + its own clock.
 * Mirrors `TokenStatus` in `apps/control-server/app/models.py`; the dashboard
 * renders it and must never re-derive it from `revoked`/`expires_at`.
 */
export type TokenStatus = "active" | "expiring_soon" | "expired" | "revoked";

export interface TokenOut {
  id: number;
  name: string;
  kind: string;
  user_id: number | null;
  system_id: number | null;
  revoked: boolean;
  created_at: string;
  expires_at: string | null;
  status: TokenStatus;
  /** Seconds until expiry at the instant `status` was decided; null = 無期限. */
  expires_in_seconds?: number | null;
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
  // Issue #369: freshness is a SECOND axis over the same row. `status`
  // ("ready") answers whether the analysis finished; this answers whether the
  // pinned commit still equals HEAD. A ready snapshot can be stale, and a
  // failed one can be current — never render one as the other.
  freshness?: SnapshotFreshnessState | null;
  is_recommended?: boolean;
}

// Issue #369: shared Snapshot preflight, decided server-side by
// `app/snapshot_preflight.py` so candidate generation / Replay / Experiment
// cannot disagree. The Dashboard renders it and never re-derives it.
export type SnapshotFreshnessState = "current" | "stale" | "unknown";
export type SnapshotPreflightCheckId =
  | "snapshot_processing"
  | "snapshot_freshness"
  | "symbol_index"
  | "understanding";
export type SnapshotPreflightCheckStatus = "ok" | "attention" | "blocking" | "unknown";
export type SnapshotPreflightVerdict = "ready" | "attention" | "blocked";

export interface SnapshotPreflightCheckOut {
  check_id: SnapshotPreflightCheckId;
  status: SnapshotPreflightCheckStatus;
  summary: string;
  detail: string;
  remediation: string;
}

export interface SnapshotPreflightOut {
  snapshot_id: number | null;
  /** "Did the analysis finish" — the existing snapshot status vocabulary. */
  processing_state: string | null;
  /** "Does the pinned commit still equal HEAD" — a separate axis. */
  freshness: SnapshotFreshnessState;
  commit_sha: string | null;
  head_sha: string | null;
  head_relation: "same" | "behind" | "diverged" | "unknown";
  commits_behind: number | null;
  verdict: SnapshotPreflightVerdict;
  checks: SnapshotPreflightCheckOut[];
  recommended_snapshot_id: number | null;
  recommended_snapshot_commit_sha: string | null;
  recommended_snapshot_freshness: SnapshotFreshnessState;
  is_recommended: boolean;
  requires_stale_acknowledgement: boolean;
  stale_continuation_note: string | null;
}

// Issue #373: deterministic monitoring summary over ALL of a component's
// traces (never just the loaded page). `error_rate` is null when there is no
// data — which is not the same as 0%.
export interface TraceSummaryOut {
  component_id: string;
  total: number;
  error_count: number;
  error_rate: number | null;
  last_trace_at: number | null;
  replayable_count: number;
  redacted_count: number;
  duration_p50_ms: number | null;
  duration_p95_ms: number | null;
  duration_max_ms: number | null;
}

export interface TracePageOut {
  items: TraceEvent[];
  total: number;
  offset: number;
  limit: number;
}

// Issue #372: Replay readiness, evaluated before a candidate is generated.
export type ReplayReadinessStatus = "ok" | "attention" | "blocking";
export type ReplayReadinessVerdict = "ready" | "attention" | "blocked";

export interface ReplayReadinessCountsOut {
  total: number;
  replayable: number;
  partial: number;
  unreplayable: number;
  /** Never opted into `replay_capture` — distinct from a failed capture. */
  not_captured: number;
  /** replayable + partial: traces that can produce a comparison at all. */
  usable: number;
}

export interface ReplayReadinessCheckOut {
  check_id: string;
  status: ReplayReadinessStatus;
  summary: string;
  detail: string;
  remediation: string;
}

export interface ReplayTraceReadinessOut {
  trace_id: string;
  replayability: "replayable" | "partial" | "unreplayable" | "not_captured";
  primary_reason: string | null;
}

export interface ReplayReadinessOut {
  component_id: string;
  snapshot_id?: number | null;
  counts: ReplayReadinessCountsOut;
  selected: ReplayReadinessCountsOut;
  selection_limit: number;
  selection_is_automatic: boolean;
  verdict: ReplayReadinessVerdict;
  checks: ReplayReadinessCheckOut[];
  traces?: ReplayTraceReadinessOut[];
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

// Issue #291: answerable knowledge areas / handoff finite sets.
export type KnowledgeArea =
  | "product_intent" | "domain_rule" | "operations" | "implementation" | "security";
export type HandoffOriginKind = "qa" | "review_item";
export type HandoffPriority = "low" | "normal" | "high";
export type HandoffStatus = "pending" | "answered" | "returned" | "cancelled";
export type InterviewStage =
  | "understanding_initialized"
  | "purpose_confirmation"
  | "capability_confirmation"
  | "element_classification"
  | "api_boundary_mapping"
  | "probe_flow_selection"
  | "proposal_generation";
export type InterviewDecisionMethod = "deterministic" | "reasoning_llm" | "manual";
export type InterviewDecisionAction = "approved" | "rejected" | "edited";
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
  // Issue #352: added after the other sections, so a session built by an
  // older Control Server (no `vision` key) still type-checks and renders.
  vision?: UnderstandingItem[];
  system_purpose: UnderstandingItem[];
  core_capabilities: UnderstandingItem[];
  capability_elements: UnderstandingItem[];
  supporting_elements: UnderstandingItem[];
  api_boundaries: UnderstandingItem[];
  probe_flow_candidates: UnderstandingItem[];
}

export type CapabilityEntityKind =
  | "core_capability" | "capability_element" | "supporting_element" | "api_boundary";

export interface InterviewCapabilityNodeOut {
  entity_id: number;
  entity_kind: CapabilityEntityKind;
  name: string;
  summary: string;
  semantic_digest: string;
  payload: Record<string, unknown>;
}

export interface InterviewCapabilityRelationOut {
  relation_id: number;
  supported_entity_id: number;
  supporting_entity_id: number;
  relation_kind: "supports";
  role: string;
  scope: string;
  semantic_digest: string;
}

export interface InterviewCapabilityGraphOut {
  confirmation_id: number;
  system_id: number;
  session_id: number;
  base_confirmation_id: number | null;
  source_revision_id: number | null;
  source_revision_at: number | null;
  composition_digest: string;
  decided_by: string;
  decided_by_user_id: number | null;
  decision_method: "manual";
  created_at: number;
  nodes: InterviewCapabilityNodeOut[];
  relations: InterviewCapabilityRelationOut[];
}

export interface InterviewCapabilityIdentityBinding {
  entity_kind: CapabilityEntityKind;
  current_name: string;
  entity_id: number;
}

export interface InterviewCapabilityRelationConfirmation {
  supported_kind: CapabilityEntityKind;
  supported_name: string;
  supporting_kind: CapabilityEntityKind;
  supporting_name: string;
  role?: string;
  scope?: string;
}

export interface InterviewConfirmUnderstandingRequest {
  actor: string;
  capability_base_confirmation_id?: number | null;
  capability_relations?: InterviewCapabilityRelationConfirmation[] | null;
  capability_identity_bindings?: InterviewCapabilityIdentityBinding[];
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
  capability_graph_confirmed_revision_id?: number | null;
  capability_graph_confirmation_required?: boolean;
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
  // Issue #291: which knowledge areas the developer can answer RIGHT NOW
  // (no role inference). Empty means no filtering, never "every area".
  answerable_areas: KnowledgeArea[];
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

export interface InterviewDialogueProposalOut {
  path: string;
  qualified_name: string;
  symbol_id: number | null;
  metadata: InterviewProposalMetadataBlock;
  probe_plan: InterviewProposalProbePlan;
  graph_node_id: string | null;
  capability_name: string | null;
  evidence_summary: string | null;
  proposal_confidence: number | null;
  denylist_hit: string | null;
}

export interface InterviewDialogueTurnOut {
  assistant_message: string;
  proposals: InterviewDialogueProposalOut[];
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

// Issue #286 review fix (Finding 1): the Investigation Agent result for a
// normal-flow question, populated only via the batch route-and-investigate
// endpoint below. Mirrors InterviewInquiryMessageDetailOut's evidence shape.
export interface InterviewQaInvestigationEvidenceOut {
  path: string;
  start_line: number;
  end_line: number;
  summary: string;
}

export type InterviewQaInvestigationStatus = "completed" | "unresolved";

export interface InterviewQaInvestigationOut {
  run_id: number;
  status: InterviewQaInvestigationStatus;
  conclusion: string;
  key_points: string[];
  evidence: InterviewQaInvestigationEvidenceOut[];
  uncertainty: string;
  confidence: "confirmed" | "likely" | "uncertain";
  decision_question: string | null;
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
  answer_unknown: boolean | null;
  status: InterviewQaStatus;
  answered_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
  answered_at: number | null;
  // Issue #286: Question Router classification, set only via the on-demand
  // route endpoint. Null until routed.
  route_category?: string | null;
  route_run_id?: number | null;
  // Issue #291: knowledge area assigned by the same router call
  // (question-router-v2); null until routed or when no area clearly fits.
  // Never hides the question -- used only for out-of-area grouping.
  knowledge_area?: KnowledgeArea | null;
  // Issue #291: set once this question has been handed off to an assignee.
  handoff_id?: number | null;
  // Issue #286 review fix (Finding 1): populated only by the batch
  // route-and-investigate endpoint. Never written by answering/correcting a
  // question, and never itself confirms an answer.
  investigation?: InterviewQaInvestigationOut | null;
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

// --- Batch route-and-investigate (Issue #286 review fix, Finding 1) ----------

export interface InterviewQaRouteInvestigateItemOut {
  qa_id: number;
  route_category?: InquiryRouteCategory | null;
  knowledge_area?: KnowledgeArea | null;
  investigation_status?: InterviewQaInvestigationStatus | "failed" | null;
  error?: string | null;
}

export interface InterviewQaRouteInvestigateCountsOut {
  routed: number;
  investigated: number;
  failed: number;
  skipped_cap: number;
}

export interface InterviewQaRouteInvestigateBatchOut {
  session_id: number;
  system_id: number;
  results: InterviewQaRouteInvestigateItemOut[];
  counts: InterviewQaRouteInvestigateCountsOut;
}

// --- Intent Brief (Issue #284) ------------------------------------------------
//
// User intent (only the user can decide), kept structurally separate from
// the implementation-fact "current understanding" above. ai_proposed items
// only become "confirmed" through an explicit confirm/correct call — never
// automatically (Principle 2).

export const INTERVIEW_INTENT_FIELDS = [
  "goal", "pain", "success_criteria", "priority", "constraints", "non_goals",
] as const;
export type InterviewIntentField = typeof INTERVIEW_INTENT_FIELDS[number];

export type InterviewIntentStatus =
  | "proposed" | "confirmed" | "needs_review" | "undecided" | "not_applicable";
// Statuses a user may set directly when creating an item. "proposed" /
// "needs_review" are system/AI states, never user-chosen at creation time.
export type InterviewIntentUserStatus = "confirmed" | "undecided" | "not_applicable";
export type InterviewIntentOrigin = "user" | "ai_proposed";

export interface InterviewIntentItemOut {
  id: number;
  session_id: number;
  system_id: number;
  field: InterviewIntentField;
  value_text: string;
  status: InterviewIntentStatus;
  origin: InterviewIntentOrigin;
  source_statement: string | null;
  decision_method: "deterministic" | "reasoning_llm" | "manual";
  intelligence_run_id: number | null;
  is_mock: boolean;
  superseded_by_id: number | null;
  created_at: number;
  updated_at: number;
}

export interface InterviewIntentListOut {
  session_id: number;
  system_id: number;
  items_by_field: Record<string, InterviewIntentItemOut[]>;
}

// --- Inquiry lifecycle (Issue #285) --------------------------------------
//
// A doubt about a confirmation item (Q&A question, Intent Brief item, or —
// from Issue #287 — a review item) holds the original item pending and
// starts a separate Inquiry conversation. Resolving the Inquiry never
// changes the origin item's own state; the developer still submits that
// item's own answer/confirm action afterward.

export type InterviewInquiryOriginKind = "qa" | "intent" | "review_item";
// Issue #308 / #323: 'superseded' is a TERMINAL, system-only status written
// exclusively by the server's premise evaluation during an Alignment
// rebuild. It means "the premise this conversation was answered against no
// longer exists" — the history stays readable, but it is never reused as a
// current justification. It is NOT "the answer was wrong" and NOT
// "unresolved"/"cancelled". /message, /resolve and /resume all 409 on a
// superseded Inquiry, so the UI must never offer those actions for one.
export type InterviewInquiryStatus =
  | "open" | "resolved" | "unresolved" | "cancelled" | "held" | "superseded";
export type InterviewInquiryMessageRole = "user" | "assistant";

// Issue #320: derived (never stored) description of how comparable an
// Inquiry's captured premise is. 'not_applicable' = the origin is not a
// review item (nothing special to show); 'untrackable' = legacy row or
// unknown content hash, so no automatic comparison is possible; 'tracked' =
// a full comparable premise bundle.
export type InquiryPremiseTrackingState = "not_applicable" | "untrackable" | "tracked";
// Issue #323: the result of the last premise evaluation; null until a
// rebuild evaluated it. 'unchanged' never supersedes.
export type InquiryPremiseEvaluation = "unchanged" | "changed" | "removed" | "ambiguous";

export interface InterviewInquiryEvidenceOut {
  path: string;
  start_line: number;
  end_line: number;
  summary: string;
}

export type InquiryRouteCategory = "human_only" | "system_researchable" | "hybrid";

// Issue #290: runtime_fact evidence provenance envelope + finite match
// state. `environment` / `snapshot_ref` are populated only from what was
// actually observed on traces (SDK PROBE_ENVIRONMENT / PROBE_GIT_SHA) --
// never the pinned analysis snapshot (Finding 5). Both are `null` for
// traces that never reported the field.
export type RuntimeFactFreshness = "fresh" | "stale" | "unobserved";

export interface RuntimeFactSnapshotRefOut {
  // null when the observed git_sha does not match any known snapshot for
  // the System; git_sha is still carried verbatim in that case.
  snapshot_id: number | null;
  git_sha: string | null;
}

export interface RuntimeFactProvenanceOut {
  environment: string | null;
  first_observed_at: number | null;
  last_observed_at: number | null;
  snapshot_ref: RuntimeFactSnapshotRefOut | null;
  source: "trace_aggregation";
  freshness: RuntimeFactFreshness;
}

export interface InterviewInquiryRuntimeEvidenceOut {
  kind: "runtime_fact";
  component_id: string;
  provenance: RuntimeFactProvenanceOut;
  runtime_check: RuntimeCheckState;
  summary: string;
}

export interface SuggestedObservationProposalOut {
  target_component: string;
  reason: "unobserved" | "stale";
}

export interface InterviewInquiryMessageDetailOut {
  key_points: string[];
  evidence: InterviewInquiryEvidenceOut[];
  uncertainty: string;
  // Issue #286: which Question Router category produced this assistant
  // answer, and (for "hybrid") the decision question the developer still
  // needs to answer themselves. Both null for messages predating Issue
  // #286 or for the user's own messages.
  route_category?: InquiryRouteCategory | null;
  decision_question?: string | null;
  // Issue #290: runtime_fact evidence + a deterministic observation-
  // proposal hint, both progressive-disclosure only (never in `content`).
  runtime_evidence?: InterviewInquiryRuntimeEvidenceOut[];
  suggested_observation_proposal?: SuggestedObservationProposalOut | null;
}

export interface InterviewInquiryMessageOut {
  id: number;
  inquiry_id: number;
  system_id: number;
  role: InterviewInquiryMessageRole;
  content: string;
  detail: InterviewInquiryMessageDetailOut | null;
  intelligence_run_id: number | null;
  is_mock: boolean;
  created_at: number;
}

export interface InterviewInquiryOut {
  id: number;
  session_id: number;
  system_id: number;
  origin_kind: InterviewInquiryOriginKind;
  origin_id: number;
  held_draft: string | null;
  status: InterviewInquiryStatus;
  status_reason: string | null;
  // Issue #308 / #320: the immutable premise this conversation was answered
  // against, captured at creation and never rebased onto a newer snapshot.
  // snapshot/revision are audit references; the hash/digest columns are what
  // the server's premise evaluation actually compares. All null for
  // Inquiries created before that migration and (apart from the
  // snapshot/version/captured_at trio) for the qa/intent origins v1 does not
  // auto-track.
  premise_snapshot_id: number | null;
  premise_revision_id: number | null;
  premise_review_subject_id: string | null;
  premise_content_hash: string | null;
  premise_capability_digest: string | null;
  premise_intent_digest: string | null;
  premise_tracking_version: string | null;
  premise_captured_at: number | null;
  premise_tracking_state: InquiryPremiseTrackingState;
  // Issue #323: the last premise verdict, the unique current successor
  // review item (set ONLY when exactly one exists — an ambiguous successor
  // is never guessed, and the front end must never infer one either), and
  // the moment this Inquiry became 'superseded'. superseded_at is separate
  // from closed_at so an already-resolved Inquiry keeps both timestamps.
  premise_evaluation: InquiryPremiseEvaluation | null;
  premise_successor_item_id: number | null;
  superseded_at: number | null;
  created_at: number;
  updated_at: number;
  closed_at: number | null;
}

export interface InterviewInquiryDetailOut {
  inquiry: InterviewInquiryOut;
  messages: InterviewInquiryMessageOut[];
}

export interface InterviewInquiryListOut {
  session_id: number;
  system_id: number;
  items: InterviewInquiryOut[];
}

// --- Alignment Review / Review Queue (Issue #287) -----------------------------
//
// Contrasts confirmed/proposed Intent Brief items against the evidence-backed
// Current System understanding to produce alignment items with a
// deterministic review classification. Only review_category IN (must_review,
// batch_reviewable) ever surfaces as an action-required Review Queue card;
// the rest are collapsed/informational.

export type AlignmentState = "aligned" | "gap" | "unknown" | "conflict" | "not_applicable";
export type AlignmentRiskFlag = "security" | "high_risk" | "core_intent";
export type AlignmentConfidence = "confirmed" | "likely" | "uncertain" | "conflicting";
export type AlignmentReviewCategory =
  | "must_review" | "batch_reviewable" | "no_review_required" | "unchanged" | "informational";
export type AlignmentReasonCode =
  | "security_related" | "high_risk" | "core_intent" | "conflict_detected"
  | "low_confidence" | "runtime_mismatch" | "routine_update" | "no_change"
  | "informational_only" | "core_capability_changed" | "unchanged_since_confirmation";
// Issue #290: deterministic Runtime Reality Check match state, set only
// when this item's evidence deterministically maps to a component_id with
// runtime trace facts; null when no deterministic mapping exists.
export type RuntimeCheckState = "match" | "mismatch" | "unobserved" | "stale";
// 'inquiry' is set while an Inquiry (origin_kind='review_item') is open on
// this item, and reset to 'open' (never 'answered') when it closes.
export type AlignmentItemStatus = "open" | "answered" | "corrected" | "held" | "inquiry";
// Issue #321: how one freshly built row relates to the previous generation
// of the same discussion point. 'removed' is deliberately absent — a subject
// with no row in the current build is a premise-evaluation result (#323),
// not a property of a row that exists.
export type AlignmentSubjectState =
  | "new" | "unchanged" | "changed" | "ambiguous" | "untrackable";
export type AlignmentDecisionAction = "accept_current" | "needs_change" | "reject_interpretation";
export type AlignmentUserDecisionAction = AlignmentDecisionAction | "corrected" | "held";

export interface AlignmentEvidenceOut {
  path: string;
  start_line: number;
  end_line: number;
  summary: string;
}

export interface AlignmentUserDecisionOut {
  action: AlignmentUserDecisionAction;
  note: string | null;
  decided_at: number;
  decided_by: string | null;
}

export interface AlignmentCapabilityDependencyOut {
  target_kind: "entity" | "relation";
  entity_id: number | null;
  relation_id: number | null;
  entity_kind: CapabilityEntityKind | null;
  entity_name: string | null;
  supported_entity_id: number | null;
  supported_entity_name: string | null;
  supporting_entity_id: number | null;
  supporting_entity_name: string | null;
}

export interface AlignmentItemOut {
  id: number;
  session_id: number;
  system_id: number;
  revision_id: number | null;
  snapshot_id: number;
  intent_item_id: number | null;
  intent_summary: string | null;
  current_claim: string;
  current_evidence: AlignmentEvidenceOut[];
  gap_summary: string | null;
  proposed_interpretation: string | null;
  alignment_state: AlignmentState;
  risk_flags: AlignmentRiskFlag[];
  confidence: AlignmentConfidence;
  review_category: AlignmentReviewCategory;
  reason_code: AlignmentReasonCode;
  user_reason: string;
  capability_confirmation_id?: number | null;
  capability_dependencies?: AlignmentCapabilityDependencyOut[];
  runtime_check?: RuntimeCheckState | null;
  status: AlignmentItemStatus;
  user_decision: AlignmentUserDecisionOut | null;
  // Issue #291: set once this review item has been handed off to an
  // assignee (creating the handoff also sets status='held').
  handoff_id?: number | null;
  // Review-finding fix (Finding 4): true when a later rebuild produced a
  // fresh replacement row for this contrast point while this row was
  // already answered/corrected. GET .../review-queue always excludes
  // superseded=1 rows; the full GET .../alignment listing (grouped by
  // review_category) can still surface one, so any rendering of that full
  // listing should treat superseded=true as history, not a current item.
  superseded?: boolean;
  // Issue #295 (ST-2) defensive additions: not yet returned by the server
  // as of this UI change (tracked separately under ST-1's `unchanged`
  // materialization work). Optional so the UI degrades gracefully to "not
  // shown" until the backend adds them, rather than assuming a shape the
  // API doesn't send yet.
  carried_over_from?: number | null;
  content_hash?: string | null;
  // Issue #313: exact reviewed YAML policy provenance for this
  // classification. Legacy rows have `legacy-code-v1` and no digest.
  policy_version: string;
  policy_digest: string | null;
  policy_rule_id?: string | null;
  manual_recheck_required?: boolean;
  // Issue #321: stable discussion-point identity and physical lineage.
  // review_subject_id is a deterministic digest over structural anchors only
  // (Intent field + confirmed Capability entity/relation ids); null for
  // legacy rows and for items with no stable anchor, which are reported
  // subject_state='untrackable'. replaces_item_id is set only when exactly
  // one predecessor generation row carried the same subject — a split/merge
  // is reported 'ambiguous' and left unbound rather than guessed.
  review_subject_id: string | null;
  subject_state: AlignmentSubjectState | null;
  replaces_item_id: number | null;
  intelligence_run_id: number;
  is_mock: boolean;
  created_at: number;
  updated_at: number;
}

export interface AlignmentBuildOut {
  session_id: number;
  system_id: number;
  revision_id: number | null;
  intelligence_run_id: number;
  is_mock: boolean;
  items: AlignmentItemOut[];
}

export interface AlignmentListOut {
  session_id: number;
  system_id: number;
  items_by_category: Record<string, AlignmentItemOut[]>;
  counts: Record<string, number>;
  // Review fix (PR #296, Finding 3): superseded=1 rows (history -- a later
  // rebuild already produced a fresh replacement row for the same contrast
  // point), split out of items_by_category/counts so those two only ever
  // reflect CURRENT rows. Optional so a response predating this fix (or an
  // older Control Server) degrades to "no history shown" instead of
  // breaking the page.
  superseded_items?: AlignmentItemOut[];
  // Review fix (PR #296, 2nd pass, Finding 3/5b): per-category count of
  // items NOT YET resolved (superseded=0 AND status NOT IN
  // (answered, corrected)) -- matches the Review Queue's card count
  // exactly, unlike `counts` which stays a total. Field name confirmed
  // against apps/control-server/app/models.py's
  // `AlignmentListOut.outstanding_counts`. Optional so a response predating
  // this field (or an older Control Server) falls back to `counts` instead
  // of breaking.
  outstanding_counts?: Record<string, number>;
}

// --- Interview UX evaluation metrics (Issue #309) ---------------------------
//
// These are deterministic System-level aggregates. `status` is deliberately
// separate from `value`: a measured zero is meaningful, while an unmeasured
// metric must keep `value=null` and explain why it cannot yet be calculated.

export type InterviewMetricStatus = "measured" | "unmeasured";
export type InterviewMetricUnit =
  | "ratio"
  | "answers_per_update"
  | "operations_per_inquiry"
  // Issue #338: how much work one conversation cost is not a ratio, and
  // expressing it as one would hide the thing being measured.
  | "per_session";
// Issue #334: joint_understanding は共同理解の質を測る別カテゴリ。効率化指標
// (確認件数・承認速度)と同じ数値へまとめない。
export type InterviewMetricCategory =
  | "user_burden"
  | "accuracy"
  | "ux_quality"
  | "joint_understanding"
  // Issue #338: `joint_understanding` counts UTILIZATION and close labels.
  // These two answer different questions and must not be averaged with it or
  // with each other — an efficiency gain must never read as a quality gain.
  | "joint_understanding_quality"
  | "joint_understanding_burden";
export type InterviewMetricKey =
  | "answers_per_understanding_update"
  | "unknown_answer_rate"
  | "review_abandonment_rate"
  | "evidence_detail_expansion_rate"
  | "operations_per_inquiry"
  | "corrected_confirmed_intent_rate"
  | "incorrect_answer_confirmation_rate"
  | "runtime_contradiction_rate"
  | "understanding_revision_recorrection_rate"
  | "post_approval_rejection_rate"
  | "post_approval_rollback_rate"
  | "post_approval_rejection_or_rollback_rate"
  | "repeated_question_rate"
  | "unchanged_item_reconfirmation_rate"
  | "inquiry_resolution_rate"
  | "post_inquiry_confirmation_rate"
  | "implementation_question_transfer_rate"
  | "joint_understanding_from_unknown_rate"
  | "joint_understanding_conclusion_rate"
  | "joint_understanding_provisional_outcome_rate"
  | "joint_understanding_stale_premise_close_rate"
  | "joint_understanding_unknown_finding_rate"
  | "joint_understanding_reflux_rate"
  | "joint_understanding_investigation_answered_rate"
  | "joint_understanding_developer_question_rate"
  // Issue #338: outcome-lineage quality, derived from the finite lineage
  // events rather than from a close label.
  | "joint_understanding_unknown_resolution_rate"
  | "joint_understanding_hypothesis_reversal_rate"
  | "joint_understanding_hypothesis_correction_rate"
  | "joint_understanding_adoption_reconfirmation_rate"
  | "joint_understanding_decision_undo_rate"
  | "joint_understanding_classification_correction_rate"
  // Issue #338: developer burden, per session.
  | "joint_understanding_rounds_per_session"
  | "joint_understanding_developer_actions_per_session"
  | "joint_understanding_developer_findings_per_session"
  | "joint_understanding_question_reask_rate";

// Issue #341: `guardrail` only designates which metrics are worth watching.
// Whether a metric is *currently* in a bad state is a separate evaluated
// judgement, so 「値が悪い」 and 「まだ判断できない」 never share one state.
export type InterviewMetricAttentionState =
  | "attention"
  | "ok"
  | "insufficient_data"
  | "not_measurable"
  | "criterion_unset"
  | "observation_only";
export type InterviewMetricAttentionReason =
  | "threshold_breached"
  | "within_threshold"
  | "sample_below_minimum"
  | "no_observations_yet"
  | "not_recorded"
  | "threshold_not_configured"
  | "not_a_notification_target";
export type InterviewMetricAttentionDirection = "high_is_bad" | "low_is_bad";

export interface InterviewMetricAttentionOut {
  state: InterviewMetricAttentionState;
  watched: boolean;
  reason: InterviewMetricAttentionReason;
  direction: InterviewMetricAttentionDirection | null;
  threshold: number | null;
  min_sample: number;
  window: "all_time" | null;
  trigger: "single_breach" | null;
  clear_condition: "value_within_threshold" | null;
}

// 取得失敗 is deliberately absent: a server that cannot answer cannot report
// its own failure, so the entry point derives that state client-side.
export type InterviewMetricsAttentionState =
  | "normal"
  | "attention"
  | "insufficient_data";

export interface InterviewMetricsAttentionSummaryOut {
  state: InterviewMetricsAttentionState;
  attention_count: number;
  insufficient_data_count: number;
  not_measurable_count: number;
  watched_count: number;
  policy_version: string;
  policy_digest: string;
}

export interface InterviewMetricOut {
  key: InterviewMetricKey;
  category: InterviewMetricCategory;
  guardrail: boolean;
  description: string;
  formula: string;
  sources: string[];
  status: InterviewMetricStatus;
  value: number | null;
  unit: InterviewMetricUnit;
  numerator: number | null;
  denominator: number | null;
  sample_size: number;
  unmeasured_reason: string | null;
  attention: InterviewMetricAttentionOut;
}

export interface InterviewMetricsOut {
  system_id: number;
  schema_version: "interview-metrics-v2";
  generated_at: number;
  sessions_observed: number;
  events_observed: number;
  attention: InterviewMetricsAttentionSummaryOut;
  metrics: InterviewMetricOut[];
}

export type InterviewMetricEventType =
  | "review_started"
  | "review_completed"
  | "review_abandoned"
  | "evidence_available"
  | "evidence_expanded"
  | "unchanged_item_presented"
  | "unchanged_item_reconfirmed"
  | "question_presented";
export type InterviewMetricTargetKind =
  | "session"
  | "qa"
  | "alignment_item"
  | "inquiry_message";

export interface InterviewMetricEventCreate {
  schema_version: "interview-metric-event-v1";
  event_key: string;
  session_id: number;
  event_type: InterviewMetricEventType;
  target_kind: InterviewMetricTargetKind;
  target_id: number;
}

export interface InterviewMetricEventOut extends InterviewMetricEventCreate {
  id: number;
  system_id: number;
  recorded_at: number;
}

// --- Batch answer (PR #296 review fix, Finding 5) ----------------------------

export interface AlignmentBatchAnswerItemRequest {
  item_id: number;
  decision: AlignmentDecisionAction;
  note?: string;
  // Review fix (PR #296, 2nd pass, Finding 2): when provided (read from this
  // item's AlignmentItemOut.content_hash at the time it was staged), the
  // server validates the item has not changed since, failing only this
  // entry (never the whole batch) on a mismatch, with an already-Japanese
  // `error` message. Field name confirmed against
  // apps/control-server/app/models.py's
  // `AlignmentBatchAnswerItemRequest.content_hash`. Optional/omit-safe so
  // older Control Servers that don't validate it simply ignore the field.
  content_hash?: string | null;
}

export interface AlignmentBatchAnswerItemResult {
  item_id: number;
  success: boolean;
  // Populated only when success is true.
  item?: AlignmentItemOut | null;
  // Populated only when success is false -- a concise structural reason
  // (not found / wrong session / Inquiry-locked / duplicate item_id),
  // never LLM free text.
  error?: string | null;
}

export interface AlignmentBatchAnswerOut {
  session_id: number;
  system_id: number;
  results: AlignmentBatchAnswerItemResult[];
  refreshed: boolean;
}

export interface AlignmentReviewQueueOut {
  session_id: number;
  system_id: number;
  items: AlignmentItemOut[];
}

export interface AlignmentRuleObjectionOut {
  reason_code: AlignmentReasonCode;
  policy_version: string;
  policy_digest: string | null;
  policy_rule_id: string;
  objection_count: number;
  pending_recheck_count: number;
}

export interface AlignmentRuleObjectionListOut {
  system_id: number;
  rules: AlignmentRuleObjectionOut[];
}

export interface AlignmentRuleRecheckOut {
  system_id: number;
  reason_code: AlignmentReasonCode;
  policy_version: string;
  policy_digest: string | null;
  policy_rule_id: string;
  decision_method: "manual";
  requested_by_user_id: number;
  recheck_target_count: number;
}

// --- Answerable knowledge areas / handoff (Issue #291) ------------------------
//
// A developer picks which knowledge areas they can answer NOW; out-of-area
// items (interview_qa) or Review Queue items (alignment_item) can be handed
// off to an assignee. The assignee's own answer is never written into the
// origin row -- the original developer must explicitly confirm it via
// /return + the origin item's own existing answer endpoint.

export interface QuestionHandoffEvidenceRef {
  path: string;
  start_line: number;
  end_line: number;
  summary: string;
}

export interface QuestionHandoffOut {
  id: number;
  session_id: number;
  system_id: number;
  origin_kind: HandoffOriginKind;
  origin_id: number;
  assignee: string;
  background: string;
  needed_decision: string;
  evidence: QuestionHandoffEvidenceRef[] | null;
  due_note: string | null;
  priority: HandoffPriority;
  status: HandoffStatus;
  answer_text: string | null;
  answered_by: string | null;
  answered_at: number | null;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

export interface QuestionHandoffListOut {
  session_id: number;
  system_id: number;
  items: QuestionHandoffOut[];
}

// --- Observation proposal (Issue #290) ----------------------------------------
//
// Approval-gated request to start NEW runtime observation. Approving never
// starts observation itself -- `policy_pointer` is a fixed, server-authored
// string pointing at the existing PUT /components/{id}/policy endpoint.

export type RuntimeObservationProposalStatus = "proposed" | "approved" | "rejected" | "expired";

export interface RuntimeObservationProposalOut {
  id: number;
  session_id: number;
  system_id: number;
  origin_inquiry_id: number | null;
  origin_alignment_item_id: number | null;
  target_component: string;
  purpose: string;
  expected_cost: string | null;
  risk_note: string | null;
  retention_note: string | null;
  status: RuntimeObservationProposalStatus;
  decision_by: string | null;
  decision_at: number | null;
  created_at: number;
  policy_pointer: string | null;
}

export interface RuntimeObservationProposalCreate {
  target_component: string;
  purpose: string;
  expected_cost?: string | null;
  risk_note?: string | null;
  retention_note?: string | null;
  origin_inquiry_id?: number | null;
  origin_alignment_item_id?: number | null;
}

// --- Automatic refresh after an answer batch (Issue #288) --------------------

export type RefreshTriggerKind =
  | "qa_answer" | "intent_update" | "alignment_answer" | "nl_change_set";
export type RefreshJobStatus = "pending" | "updating" | "updated" | "failed" | "stale";

export interface RefreshJobOut {
  id: number;
  session_id: number;
  system_id: number;
  trigger_kind: RefreshTriggerKind;
  status: RefreshJobStatus;
  error: string | null;
  intelligence_run_id: number | null;
  result_revision_id: number | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface RefreshStatusOut {
  session_id: number;
  system_id: number;
  latest_job: RefreshJobOut | null;
  pending_count: number;
}

// --- Natural-language bulk correction -> structured change set (Issue #289) -
//
// A free-text correction covering multiple Understanding items is never
// applied directly — a reasoning LLM turns it into a structured, itemized
// change set; the developer previews field-level diffs + a deterministic
// impact preview and selectively applies only resolved items.
// 'forbidden' means the (target_kind, field) pair is outside the server's
// whitelist (e.g. an attempt to touch alignment user_decision), not merely
// unresolved.

export type ChangeSetStatus =
  | "proposed" | "previewed" | "partially_applied" | "applied" | "discarded" | "failed";
export type ChangeTargetKind = "intent_item" | "understanding_claim";
export type ChangeResolutionState = "resolved" | "ambiguous" | "conflict" | "stale" | "forbidden";

export interface ChangeSetOut {
  id: number;
  session_id: number;
  system_id: number;
  base_revision_id: number | null;
  source_text: string;
  status: ChangeSetStatus;
  intelligence_run_id: number;
  is_mock: boolean;
  created_at: number;
  updated_at: number;
}

export interface ChangeSetAffectedItemOut {
  alignment_item_id: number;
  current_claim: string;
  review_category: string;
}

export interface ChangeSetItemOut {
  id: number;
  change_set_id: number;
  system_id: number;
  target_kind: ChangeTargetKind;
  target_ref: Record<string, unknown>;
  field: string;
  before_value: string | null;
  after_value: string;
  reason: string;
  resolution_state: ChangeResolutionState;
  applied: boolean;
  applied_at: number | null;
  created_at: number;
  affected_items: ChangeSetAffectedItemOut[];
}

export interface ChangeSetDetailOut {
  change_set: ChangeSetOut;
  items: ChangeSetItemOut[];
  rebuild_note: string;
}

export interface ChangeSetSkippedItemOut {
  item_id: number;
  resolution_state: ChangeResolutionState;
  message: string;
}

export interface ChangeSetApplyResultOut {
  change_set: ChangeSetOut;
  applied_item_ids: number[];
  skipped: ChangeSetSkippedItemOut[];
  result_revision_id: number | null;
}

export interface InterviewProposalDecisionOut {
  id: number;
  proposal_id: number;
  session_id: number;
  system_id: number;
  decision: InterviewDecisionAction;
  decision_method: InterviewDecisionMethod;
  actor: string;
  edited_metadata: InterviewProposalMetadataBlock | null;
  edited_probe_plan: InterviewProposalProbePlan | null;
  denylist_hit: string | null;
  decided_at: number;
}

export interface InterviewApprovedItemOut {
  proposal_id: number;
  path: string;
  qualified_name: string;
  symbol_id: number | null;
  metadata: InterviewProposalMetadataBlock;
  probe_plan: InterviewProposalProbePlan;
  decision: InterviewDecisionAction;
  decision_id: number;
  actor: string;
  decided_at: number;
}

export interface InterviewApprovedSetOut {
  session_id: number;
  system_id: number;
  snapshot_id: number;
  items: InterviewApprovedItemOut[];
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
  first_observed_at: number | null;
  last_observed_at: number | null;
  has_traces: boolean;
  observed_environment: string | null;
  observed_git_sha: string | null;
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
  /**
   * Issue #369: required when the server's preflight reports the resolved
   * snapshot is definitively behind HEAD. `decision_method: manual`.
   */
  stale_snapshot_reason?: string;
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

// ── Probe Cell Fabric: Root Orchestrator digest / Ask lifecycle (Issue #303) ──
// The digest shape mirrors app.cell_root.build_root_digest's return value
// verbatim (same pattern as the Sub 4 orchestrator digest); it is
// deliberately loosely typed here since it is display-only and the Control
// Server is the single source of truth for its exact contents.

export interface CellAskDigestEntry {
  kind: "escalation";
  dedupe_key: string;
  cell_id: string;
  severity: "sev1" | "sev2" | "sev3";
  summary: string;
  evidence_refs: string[];
  sources: number[];
  created_at: number;
}

export interface CellOrchestratorKeyPoint {
  type: "orchestrator";
  cell_id: string;
  progress: { by_cell: Record<string, Record<string, number>>; total: Record<string, number> };
  quality?: Array<{
    cell_id: string;
    pass_rate: number | null;
    audited_count: number | null;
    intake_status: "accepting" | "suspended" | null;
    sample_rate: number | null;
  }>;
  topology?: Array<{
    cell_id: string;
    feature_refs: string[];
    capability_refs: string[];
    entrypoint_refs: string[];
  }>;
  escalations_open_by_severity: Record<string, number>;
  bottleneck_candidates: Array<Record<string, unknown>>;
  binding_stale: boolean;
}

export interface CellEscalationPendingKeyPoint extends CellAskDigestEntry {
  type: "escalation_pending_decision";
}

export type CellRootKeyPoint = CellOrchestratorKeyPoint | CellEscalationPendingKeyPoint;

export interface CellRootConclusion {
  overall_severity: "ok" | "info" | "warning" | "blocked" | "error";
  counts: {
    orchestrators: number;
    tasks: Record<string, number>;
    escalations_open_by_severity: Record<string, number>;
    asks_open: number;
  };
  top_risks: CellAskDigestEntry[];
}

export interface CellRootEvidence {
  sev3_escalations: CellAskDigestEntry[];
  tasks: Array<{ task_id: number; cell_id: string; evidence_refs: string[] }>;
}

export interface CellRootAudit {
  generated_at: number;
  system_state: {
    generated_at: number;
    overall_severity: string;
    user_phase?: UserPhase;
    primary_item: SystemStateItem | null;
    items: SystemStateItem[];
  };
  is_stale: boolean;
  stale_orchestrator_cell_ids: string[];
  decision_method_notes: string;
}

export interface CellRootDigest {
  generated_at: number;
  conclusion: CellRootConclusion;
  key_points: CellRootKeyPoint[];
  evidence: CellRootEvidence;
  audit: CellRootAudit;
}

export interface CellRootDigestOut {
  system_id: number;
  digest: CellRootDigest;
  generated_at: number;
}

export type CellAskStatus = "open" | "accepted" | "held" | "rejected";
export type CellAskDecision = "accepted" | "held" | "rejected";

export interface CellAskOut {
  id: number;
  system_id: number;
  source_kind: "escalation" | "triage" | "report";
  source_id: number;
  cell_definition_id: string | null;
  goal_id: number | null;
  task_id: number | null;
  ask_text: string;
  severity: "sev1" | "sev2" | "sev3";
  status: CellAskStatus;
  decision: string;
  decision_note?: string;
  decision_method: string;
  decided_by: string | null;
  decided_at: number | null;
  execution_approved: boolean;
  dedupe_key: string;
  created_at: number;
}

export interface CellAsksListOut {
  system_id: number;
  asks: CellAskOut[];
}

export interface CellAskSyncOut {
  system_id: number;
  created: number;
  deduped: number;
}

// --- 共同理解セッション(Epic #328 / Issue #329-#332)------------------------
//
// 「わからない」を終端回答ではなく共同で状況理解を作る工程の開始点として扱う。
// 三つの来歴(investigation / translation / developer)は 1 つの回答へ混ぜない。

export type JointUnderstandingOriginKind = "qa" | "intent" | "review_item" | "inquiry";
export type JointUnderstandingTrigger = "unknown_answer" | "explicit_request";
export type JointUnderstandingStatus = "open" | "held" | "closed";
export type JointUnderstandingOutcome =
  | "understood"
  | "doubt_resolved"
  | "hypothesis_adopted"
  | "decided"
  | "handed_off"
  | "abandoned";
export type JointUnderstandingClaimKind =
  | "fact"
  | "inference"
  | "hypothesis"
  | "unknown"
  | "conflict";
export type JointUnderstandingOriginRole = "investigation" | "translation" | "developer";
export type JointUnderstandingActionKind =
  | "request_investigation"
  | "explain_reasoning"
  | "compare_options"
  | "adopt_hypothesis"
  | "revise_intent"
  | "hold"
  | "handoff"
  | "decide";
export type JointUnderstandingStatementLayer =
  | "purpose"
  | "impact"
  | "gap"
  | "consistency"
  | "decision";
// Issue #337: the finite premise verdict, evaluated from the shared Issue #308
// premise bundle rather than from the pinned snapshot id alone. Only "current"
// permits hypothesis_adopted / decided / reflux. "missing" (a premise that was
// captured and has since disappeared) and "invalid" (no comparable bundle was
// ever captured) both used to report as "fresh".
export type JointUnderstandingPremiseState =
  | "current"
  | "stale"
  | "missing"
  | "invalid";
// The same set plus the pre-#337 value, for a verdict READ BACK from a session
// closed before this contract existed. Never produced anew.
export type JointUnderstandingRecordedPremiseState =
  | JointUnderstandingPremiseState
  | "fresh";
export type JointUnderstandingPremiseReason =
  | "premise_not_captured"
  | "premise_incomplete"
  | "pinned_snapshot_removed"
  | "origin_removed"
  | "origin_superseded"
  | "pinned_commit_changed"
  | "origin_content_changed"
  | "capability_scope_changed"
  | "linked_intent_changed";
// Issue #337: WHICH code path produced a finding, as distinct from whose voice
// it speaks in (origin_role). "legacy" is read-only.
export type JointUnderstandingProducerKind =
  | "investigation_loop"
  | "translator"
  | "developer_api"
  | "legacy";
// Issue #337: whether an authenticated human stands behind the row.
export type JointUnderstandingActorKind = "user" | "system" | "legacy";
export type JointUnderstandingAdoptionState =
  | "provisional"
  | "reconfirmation_required"
  | "basis_withdrawn";
export type JointUnderstandingStopReason =
  | "answered"
  | "budget_exhausted"
  | "no_new_evidence"
  | "unresolved"
  | "failed";
// Issue #339: the finite outcome class, so a caller never has to inspect
// `stop_reason` and guess which side of the limitation/failure split it is on.
export type JointUnderstandingOutcomeClass =
  | "answered"
  | "research_limitation"
  | "execution_failure";
// Issue #339: WHERE an execution failure broke, because the recovery differs.
export type JointUnderstandingFailureClass =
  | "config_invalid"
  | "snapshot_unavailable"
  | "api_failure"
  | "schema_invalid"
  | "timeout";
export type JointUnderstandingExplorationSourceKind =
  | "path_name"
  | "symbol_index"
  | "entrypoint_index"
  | "file_content"
  | "dependency"
  | "call_graph"
  | "git_history"
  | "runtime_facts";

export interface JointUnderstandingEvidenceOut {
  path: string;
  start_line: number;
  end_line: number;
  summary: string;
}

export interface JointUnderstandingRuntimeEvidenceOut {
  component_id: string;
  runtime_check: "match" | "mismatch" | "unobserved" | "stale";
  summary: string;
}

export interface JointUnderstandingFindingOut {
  id: number;
  joint_understanding_id: number;
  system_id: number;
  origin_role: JointUnderstandingOriginRole;
  claim_kind: JointUnderstandingClaimKind;
  statement: string;
  evidence: JointUnderstandingEvidenceOut[];
  runtime_evidence: JointUnderstandingRuntimeEvidenceOut[];
  supports_finding_ids: number[];
  competing_explanations: string[];
  refutation_conditions: string[];
  next_investigation: string | null;
  uncertainty: string;
  supersedes_finding_id: number | null;
  decision_method: string;
  intelligence_run_id: number | null;
  is_mock: boolean;
  producer_kind: JointUnderstandingProducerKind;
  actor_kind: JointUnderstandingActorKind;
  actor_username: string | null;
  created_at: number;
}

export interface JointUnderstandingActionOut {
  id: number;
  joint_understanding_id: number;
  system_id: number;
  action_kind: JointUnderstandingActionKind;
  // Display label only; actor_kind/actor_username are the authenticated
  // identity the server resolved from the request's Principal (Issue #337).
  actor: string | null;
  actor_kind: JointUnderstandingActorKind;
  actor_username: string | null;
  note: string | null;
  decision_method: "manual";
  created_at: number;
}

export interface JointUnderstandingAdoptionOut {
  id: number;
  joint_understanding_id: number;
  system_id: number;
  finding_id: number;
  state: JointUnderstandingAdoptionState;
  adopted_by_actor_kind: JointUnderstandingActorKind;
  adopted_by_username: string | null;
  adoption_reason: string;
  premise_snapshot_id: number | null;
  premise_commit_sha: string | null;
  premise_revision_id: number | null;
  decision_method: "manual";
  adopted_at: number;
}

export interface JointUnderstandingRoundOut {
  id: number;
  joint_understanding_id: number;
  system_id: number;
  round_index: number;
  status: "completed" | "unresolved" | "failed";
  stop_reason: JointUnderstandingStopReason | null;
  conclusion: string;
  search_leads: string[];
  open_hypotheses: string[];
  missing_evidence: string[];
  read_paths: string[];
  unread_candidates: string[];
  pruned_findings: number;
  files_read: number;
  chars_read: number;
  llm_calls: number;
  elapsed_seconds: number;
  intelligence_run_id: number | null;
  error_details: string | null;
  // Issue #339: set ONLY for an execution failure. A research limitation
  // (budget_exhausted / no_new_evidence / unresolved) is a real,
  // evidence-backed result and leaves this null -- "the system looked and could
  // not tell" must stay distinguishable from "the system could not look".
  failure_class: JointUnderstandingFailureClass | null;
  outcome_class: JointUnderstandingOutcomeClass;
  sources: JointUnderstandingExplorationSourceOut[];
  created_at: number;
}

export interface JointUnderstandingExplorationSourceOut {
  id: number;
  round_id: number;
  system_id: number;
  source_kind: JointUnderstandingExplorationSourceKind;
  // The pinned commit for git history, the snapshot id for the index /
  // content / runtime sources.
  revision: string;
  candidates_found: number;
  queries_run: number;
  elapsed_seconds: number;
  truncated: boolean;
  // A failed source is recorded and skipped: it never fails the round, and it
  // is never replaced by an unbounded fallback search.
  error_details: string | null;
  created_at: number;
}

export interface JointUnderstandingStatementOut {
  layer: JointUnderstandingStatementLayer;
  claim_kind: JointUnderstandingClaimKind;
  text: string;
  supports_finding_ids: number[];
  finding_id: number;
}

export interface JointUnderstandingOptionOut {
  label: string;
  what_changes: string;
  tradeoffs: string;
  supports_finding_ids: number[];
}

export interface JointUnderstandingActionMenuEntryOut {
  action_kind: JointUnderstandingActionKind;
  label: string;
  what_changes: string;
}

export interface JointUnderstandingTranslationOut {
  id: number;
  joint_understanding_id: number;
  system_id: number;
  purpose_summary: string;
  statements: JointUnderstandingStatementOut[];
  options: JointUnderstandingOptionOut[];
  open_unknowns: string[];
  decision_question: string | null;
  ask_developer: boolean;
  intelligence_run_id: number | null;
  is_mock: boolean;
  created_at: number;
}

export interface JointUnderstandingRefluxOut {
  id: number;
  joint_understanding_id: number;
  system_id: number;
  finding_id: number;
  target_kind: "qa_investigation" | "session_ledger";
  target_id: number | null;
  statement: string;
  evidence: JointUnderstandingEvidenceOut[];
  runtime_evidence: JointUnderstandingRuntimeEvidenceOut[];
  decision_method: "reasoning_llm";
  intelligence_run_id: number | null;
  premise_snapshot_id: number | null;
  created_at: number;
}

export interface JointUnderstandingOut {
  id: number;
  session_id: number;
  system_id: number;
  origin_kind: JointUnderstandingOriginKind;
  origin_id: number;
  trigger: JointUnderstandingTrigger;
  question_text: string;
  status: JointUnderstandingStatus;
  outcome: JointUnderstandingOutcome | null;
  outcome_is_provisional: boolean;
  outcome_reason: string | null;
  outcome_finding_ids: number[];
  outcome_premise_state: JointUnderstandingRecordedPremiseState | null;
  outcome_premise_reason: JointUnderstandingPremiseReason | null;
  closed_by_actor_kind: JointUnderstandingActorKind | null;
  closed_by_username: string | null;
  // Issue #336: the origin row that is CURRENT today. `interview_qa` and
  // `interview_intent_item` correct additively, so `origin_id` becomes a
  // superseded row the moment the developer revises the item -- matching a
  // session to the live item must use this.
  current_origin_id: number | null;
  premise_state: JointUnderstandingPremiseState;
  premise_reason: JointUnderstandingPremiseReason | null;
  premise_snapshot_id: number | null;
  premise_commit_sha: string | null;
  premise_revision_id: number | null;
  premise_tracking_version: string | null;
  premise_captured_at: number | null;
  schema_version: string;
  created_at: number;
  updated_at: number;
  closed_at: number | null;
}

export interface JointUnderstandingDetailOut {
  session: JointUnderstandingOut;
  findings: JointUnderstandingFindingOut[];
  actions: JointUnderstandingActionOut[];
  investigation_rounds: JointUnderstandingRoundOut[];
  translations: JointUnderstandingTranslationOut[];
  reflux: JointUnderstandingRefluxOut[];
  hypothesis_adoptions: JointUnderstandingAdoptionOut[];
  available_actions: JointUnderstandingActionKind[];
}

export interface JointUnderstandingListOut {
  session_id: number;
  system_id: number;
  items: JointUnderstandingOut[];
}

export interface JointUnderstandingInvestigateOut {
  joint_understanding_id: number;
  system_id: number;
  stop_reason: JointUnderstandingStopReason;
  rounds: JointUnderstandingRoundOut[];
  findings: JointUnderstandingFindingOut[];
  error: string | null;
}

export interface JointUnderstandingTranslateOut {
  translation: JointUnderstandingTranslationOut;
  action_menu: JointUnderstandingActionMenuEntryOut[];
}

// Issue #336: the single 「わからない」 entry point. The internal route names
// (system_researchable / hybrid / human_only) deliberately do not appear in
// `next_step` -- an internal classification name is not a developer-facing
// label, so the page maps these four values to its own copy.
export type JointUnderstandingUnknownNextStep =
  | "joint_investigation_started"
  | "joint_understanding_opened"
  | "developer_answer_required"
  | "routing_unavailable";
export type JointUnderstandingRouteCategory =
  | "human_only"
  | "system_researchable"
  | "hybrid";

export interface InterviewQaUnknownOut {
  session_id: number;
  system_id: number;
  // Committed before routing runs, so it is present on every outcome
  // including the failure ones.
  qa: InterviewQaOut;
  route_category: JointUnderstandingRouteCategory | null;
  knowledge_area: KnowledgeArea | null;
  joint_understanding_id: number | null;
  next_step: JointUnderstandingUnknownNextStep;
  investigation_stop_reason: JointUnderstandingStopReason | null;
  error: string | null;
}

export interface JointUnderstandingRefluxResultOut {
  joint_understanding_id: number;
  system_id: number;
  target_kind: "qa_investigation" | "session_ledger";
  premise_state: JointUnderstandingPremiseState;
  refluxed: JointUnderstandingRefluxOut[];
  already_refluxed: number;
  skipped_not_fact: number;
  skipped_unverified: number;
}

// --- State-driven System Interview workflow (Issue #349) ---------------------
//
// The canonical developer-facing workflow contract of
// docs/system-interview-workflow-ux.md. The dashboard renders these values;
// it never re-derives a workflow state of its own (spec principle P9).

export type InterviewWorkflowState =
  | "W0-A" | "W0-B" | "W1" | "W2" | "W3" | "W4" | "W5" | "W6" | "W7";

export type InterviewWorkflowPrimaryAction =
  | "open_repository"
  | "start_session"
  | "none"
  | "confirm_understanding"
  | "submit_answer"
  | "confirm_alignment_item"
  | "approve_proposal"
  | "record_diff_review"
  | "open_connection_setup"
  | "view_handoff_status"
  | "resume_session";

export type InterviewWorkflowTerminalKind = "completed" | "handoff" | "suspended";

export type InterviewProcessKind =
  | "understanding_build"
  | "understanding_update"
  | "alignment_build"
  | "question_routing"
  | "intent_candidates"
  | "proposal_generation"
  | "diff_generation"
  | "runtime_reality_check";

export interface InterviewWorkflowFactsOut {
  has_snapshot: boolean;
  has_session: boolean;
  session_closed: boolean;
  running_process_kinds: InterviewProcessKind[];
  blocking_failure_states: string[];
  understanding_unconfirmed: boolean;
  open_required_questions: number;
  outstanding_alignment_items: number;
  proposals_needing_review: number;
  proposals_generatable: boolean;
  approved_proposal_count: number;
  diff_matches_approval_set: boolean;
  diff_review_complete: boolean;
  pending_handoff_count: number;
}

export interface InterviewProcessRunOut {
  id: number;
  session_id: number;
  system_id: number;
  process_kind: InterviewProcessKind;
  status: "running" | "succeeded" | "failed";
  failure_class: "blocking" | "degraded" | null;
  target_state: string | null;
  error: string | null;
  started_at: number;
  finished_at: number | null;
}

export interface InterviewBackRequestOut {
  id: number;
  session_id: number;
  system_id: number;
  cause_kind: string;
  candidate_state: InterviewWorkflowState;
  reached_state: InterviewWorkflowState;
  status: "pending" | "acknowledged" | "resolved";
  created_at: number;
}

export interface InterviewWorkflowExceptionOut {
  code: string;
  severity: "blocking" | "degraded" | "informational";
  target_state: string | null;
  message: string;
  detail: string | null;
  recovery_process_kind: InterviewProcessKind | null;
  recovery_condition: string | null;
}

export interface InterviewWorkflowStateOut {
  system_id: number;
  session_id: number | null;
  state: InterviewWorkflowState;
  candidate_state: InterviewWorkflowState;
  rule_row: number;
  reached_state: InterviewWorkflowState | null;
  backward_hold: boolean;
  pending_back_request: InterviewBackRequestOut | null;
  terminal_kind: InterviewWorkflowTerminalKind | null;
  primary_action: InterviewWorkflowPrimaryAction;
  facts: InterviewWorkflowFactsOut;
  running_processes: InterviewProcessRunOut[];
  unresolved_failures: InterviewProcessRunOut[];
  exceptions: InterviewWorkflowExceptionOut[];
  diff_materialized_at: number | null;
  latest_ready_snapshot_id: number | null;
}

export interface InterviewDiffReviewOut {
  id: number;
  session_id: number;
  system_id: number;
  diff_materialized_at: number;
  diff_digest: string;
  reviewed_by: string;
  decision_method: string;
  note: string;
  created_at: number;
}

export interface InterviewSessionStatusAuditOut {
  id: number;
  session_id: number;
  system_id: number;
  action: "close" | "reopen";
  terminal_kind: InterviewWorkflowTerminalKind | null;
  reason: string;
  actor: string;
  decision_method: string;
  created_at: number;
}

// --- Understanding Brief / Decision Readiness (Issues #351-#354) -------------
//
// `GET /interview/understanding-brief`. The server derives all of this from
// persisted facts; the Dashboard renders it and never recomputes a
// confirmation state, a provenance, or a readiness verdict of its own.

/** 確認状態 — how settled a claim is. */
export type UnderstandingConfirmationState =
  | "confirmed"
  | "ai_hypothesis"
  | "conflicting"
  | "unknown"
  | "recheck_required";

/** 出所 — where the claim's content came from. A separate axis from the above. */
export type UnderstandingProvenanceKind =
  | "developer_intent"
  | "implementation_fact"
  | "runtime_observation"
  | "ai_hypothesis";

export type UnderstandingReadinessState =
  | "not_built"
  | "building"
  | "needs_confirmation"
  | "ready"
  | "recheck_required"
  | "blocked";

export type UnderstandingReadinessSeverity = "blocking" | "attention" | "informational";

export type UnderstandingClaimKind = "vision" | "system_purpose" | "core_capability";

/**
 * 前回確認時からの変更種別。前 4 つはリビジョン差分 (名前・説明・確信度)、
 * 残りは `claim_payload` の各フィールド。再確認を決めるフィールドは必ずここに
 * 現れる -- サーバー側の `UnderstandingChangeKind` と同一集合で、
 * `test_interview_type_parity.py` が両者の乖離を禁止している。
 */
export type UnderstandingChangeKind =
  | "added"
  | "removed"
  | "summary_changed"
  | "confidence_changed"
  | "contribution_changed"
  | "evidence_changed"
  | "related_docs_changed"
  | "related_apis_changed"
  | "composition_changed";

export interface UnderstandingBriefClaimOut {
  kind: UnderstandingClaimKind;
  name: string;
  summary: string;
  confirmation: UnderstandingConfirmationState;
  provenance: UnderstandingProvenanceKind;
  confirmation_label: string;
  provenance_label: string;
  reason: string;
  contribution: string;
  /** モック LLM 由来。隠さずに明示する (CLAUDE.md)。 */
  is_mock: boolean;
  evidence: { path: string; start_line: number; end_line: number; summary?: string }[];
  related_docs: string[];
  related_apis: string[];
}

export interface UnderstandingReadinessReasonOut {
  code: string;
  severity: UnderstandingReadinessSeverity;
  message: string;
  target_kind: string;
  target_name: string;
}

export interface UnderstandingChangeOut {
  change_kind: UnderstandingChangeKind;
  section: string;
  section_label: string;
  name: string;
  detail: string;
}

export interface UnderstandingBriefOut {
  system_id: number;
  session_id: number | null;
  built: boolean;
  vision: UnderstandingBriefClaimOut | null;
  vision_missing_information: string[];
  system_purpose: UnderstandingBriefClaimOut[];
  core_capabilities: UnderstandingBriefClaimOut[];
  core_capability_initial_count: number;
  key_unconfirmed: UnderstandingBriefClaimOut[];
  detail_counts: Record<string, number>;
  readiness_state: UnderstandingReadinessState;
  readiness_label: string;
  readiness_description: string;
  readiness_reasons: UnderstandingReadinessReasonOut[];
  changes_since_confirmation: UnderstandingChangeOut[];
  confirmed_at: number | null;
  confirmed_revision_id: number | null;
  /** 表示中の理解のリビジョン。更新中に「いま見ているもの」を特定するため。 */
  revision_id: number | null;
  snapshot_id: number | null;
}

// --- Overview / System Intelligence Brief (Issues #380-#384) -----------------
//
// Every union below mirrors a server `Literal` in `app/models.py` one-for-one;
// `test_interview_type_parity.py` fails if the two drift. The Overview renders
// this projection and never re-derives a readiness verdict, a finding's
// importance, or the next action from client state (#380 principle 6).

export type OverviewFindingKind =
  | "claim_conflict"
  | "understanding_blocked"
  | "understanding_changed"
  | "capability_composition_stale"
  | "unconfirmed_core_claim"
  | "runtime_mismatch"
  | "runtime_unobserved"
  | "connectivity_lost"
  | "snapshot_stale"
  | "evaluation_decision_pending"
  | "improvement_candidate_ready";

export type OverviewFindingSeverity =
  | "blocker"
  | "human_decision_required"
  | "material_change"
  | "informative";

/** `not_compared` は「新しい発見がない」とは別の答え。同じ表示にしてはならない。 */
export type OverviewFindingStatus = "new" | "ongoing" | "not_compared";

/**
 * 先頭4つは `UnderstandingProvenanceKind` と同一の値。claim の出所は変換せず
 * そのまま finding に引き継ぐ（`developer_intent` を落として `ai_hypothesis`
 * にすると、開発者が確定した Vision が AI の推測として表示される）。
 * 残り3つは finding 固有: 判断の記録 / probe-agent 自身の処理記録 / 出所が
 * 複数に割れた集約。
 */
export type OverviewFindingProvenance =
  | "developer_intent"
  | "implementation_fact"
  | "runtime_observation"
  | "ai_hypothesis"
  | "developer_decision"
  | "system_process"
  | "mixed";

/** 固定した snapshot が HEAD の断面かどうか。サーバーが決める。 */
export type OverviewSnapshotFreshness = "current" | "stale" | "unavailable";

/** Capability 単位の観測カバレッジを算出できたか。今日は `not_computed`。 */
export type OverviewCoverageState = "computed" | "not_computed";

export type OverviewFindingsState =
  | "has_findings"
  | "no_findings"
  | "not_compared"
  | "unavailable";

/** 「前回」の基準を読めたか。読めないことと、存在しないことは別。 */
export type OverviewBaselineState = "has_baseline" | "no_baseline" | "unavailable";

export type OverviewActionKey =
  | "create_system"
  | "prepare_repository"
  | "build_understanding"
  | "resolve_understanding_blocker"
  | "answer_interview_questions"
  | "confirm_understanding"
  | "connect_sdk"
  | "start_observation"
  | "restore_observation"
  | "record_experiment_decision"
  | "publish_improvement"
  /** 計測 patch の公開。改善変更の公開とは別の lineage。 */
  | "publish_instrumentation"
  | "create_candidate"
  | "start_next_cycle";

export type OverviewActionState = "available" | "waiting" | "complete" | "unavailable";

export type OverviewLoopStage =
  | "setup"
  | "preparation"
  | "instrumentation"
  | "observation"
  | "evaluation"
  | "publish";

export type OverviewLoopStageStatus = "reached" | "current" | "future";

export type OverviewSection =
  | "brief"
  | "findings"
  | "next_action"
  | "loop"
  | "runtime"
  | "purpose_chain"
  | "purpose_question";

export interface OverviewTargetOut {
  route: string;
  label: string;
  /** 遷移先が実際に読むパラメータ名で入る (#371 の規則)。 */
  params: Record<string, string>;
  anchor: string | null;
}

export interface OverviewFindingOut {
  id: string;
  kind: OverviewFindingKind;
  kind_label: string;
  severity: OverviewFindingSeverity;
  severity_label: string;
  status: OverviewFindingStatus;
  status_label: string;
  summary: string;
  decision_impact: string;
  provenance: OverviewFindingProvenance;
  provenance_label: string;
  snapshot_id: number | null;
  revision_id: number | null;
  runtime_window_seconds: number | null;
  first_seen: number | null;
  last_updated: number | null;
  target: OverviewTargetOut | null;
  evidence: Record<string, unknown>[];
  occurrence_count: number;
}

export interface OverviewActionOut {
  key: OverviewActionKey;
  label: string;
  reason: string;
  completion_condition: string;
  value: string;
  target: OverviewTargetOut;
  rule_row: number;
  source_state_ids: string[];
  source_finding_ids: string[];
  blockers: string[];
}

export interface OverviewLoopStageOut {
  stage: OverviewLoopStage;
  label: string;
  status: OverviewLoopStageStatus;
  meaning: string;
  /** Renamed from `next_milestone` (Issue #427 §7.4): a static per-stage
   * display sentence, never a canonical Product Milestone -- that identity
   * lives in `OverviewObjectiveOut.next_milestone` instead. */
  stage_completion_hint: string;
  complete: boolean;
}

export interface OverviewRuntimeHealthOut {
  state: ConnectivityState;
  freshness: ConnectivityFreshness;
  freshness_label: string;
  transport_freshness: ConnectivityFreshness;
  last_real_trace_at: number | null;
  seconds_since_last_trace: number | null;
  last_trace_at: number | null;
  seconds_since_last_any_trace: number | null;
  evaluated_at: number;
  real_trace_count_5m: number;
  real_trace_count_1h: number;
  real_trace_count_24h: number;
  delayed_after_seconds: number;
  stale_after_seconds: number;
  component_count: number;
  total_trace_count: number;
  mode_counts: Record<string, number>;
  window_seconds: number;
  error_count: number;
  runtime_mismatch_count: number;
  replayable_count: number;
  partial_count: number;
  unreplayable_count: number;
  not_captured_count: number;
  /** window 内に trace を出した component 数。Capability 数とは別 entity。 */
  observed_component_count: number;
  known_component_count: number;
  core_capability_count: number;
  capability_coverage_state: OverviewCoverageState;
  observed_capability_count: number | null;
  unmapped_component_count: number | null;
}

export interface OverviewOut {
  system_id: number;
  generated_at: number;
  interview_session_id: number | null;
  brief: UnderstandingBriefOut | null;
  snapshot_id: number | null;
  snapshot_commit_sha: string | null;
  latest_ready_snapshot_id: number | null;
  snapshot_freshness: OverviewSnapshotFreshness;
  understanding_revision_id: number | null;
  understanding_confirmed_at: number | null;
  findings: OverviewFindingOut[];
  findings_initial_count: number;
  findings_state: OverviewFindingsState;
  findings_baseline_state: OverviewBaselineState;
  findings_baseline_label: string;
  findings_baseline_at: number | null;
  next_action: OverviewActionOut | null;
  next_action_state: OverviewActionState;
  next_action_message: string;
  loop_stages: OverviewLoopStageOut[];
  user_phase: string;
  runtime: OverviewRuntimeHealthOut | null;
  degraded_sections: OverviewSection[];
  degraded_detail: Record<string, string>;
  /** The canonical Purpose Frame / Purpose Chain (#388), reused verbatim.
   * `null` only when its own guarded loader failed -- see `purpose_chain`
   * in `degraded_sections`, never a synonym for "not derived yet". */
  purpose_chain?: PurposeChainOut | null;
  /** §4.5/#391's single adaptive next question over `purpose_chain` above,
   * embedded here instead of a second client query
   * (`usePurposeNextQuestion`, removed from the Overview page). `null`
   * means either "no question right now" or "could not be derived" -- told
   * apart by `"purpose_question" in degraded_sections`. */
  purpose_question?: PurposeQuestionOut | null;
}

// --- Purpose Chain (Issue #387 Epic / #388 / #390) --------------------------
//
// `docs/purpose-chain.md` is the canonical design contract; §0 and §1 are the
// server specification this mirrors. Two rules carry over unchanged from the
// server (`app/models.py`'s own comment) and bind the Dashboard too:
//
// 1. **No new understanding model.** Every element/relation below reuses
//    `UnderstandingConfirmationState` / `UnderstandingProvenanceKind`
//    VERBATIM -- Purpose Chain adds relation + lineage on top of the exact
//    same `understanding_brief` / Intent Brief rows the Overview System Brief
//    and the Interview's Understanding Brief already render. This file must
//    never define a second 確認状態/出所 vocabulary for the same claims.
// 2. **Finite sets only, exact-name identity only.** Every union below is
//    `test_interview_type_parity.py`'s `FINITE_TYPE_NAMES`-checked against
//    the matching server `Literal` in `app/models.py`. No similarity/
//    embedding/keyword join connects an element or a relation on this side
//    either (Principle 6) -- the server has already decided identity by the
//    time any of this reaches the Dashboard.

/** The four Purpose Frame element kinds. Only three occupy the minimal
 * Purpose Frame (`beneficiary_problem` / `desired_change` / `intervention`);
 * `core_capability` elements hang off `intervention` via
 * `intervention_to_capability` relations but are never a frame slot. */
export type PurposeElementKind =
  | "beneficiary_problem"
  | "desired_change"
  | "intervention"
  | "core_capability";

/** Whether an element's SOURCE ROW could be read and had content -- three
 * values because "the row was read; nothing is there yet" (`unknown`) and
 * "the read itself failed" (`unavailable`) are different facts and must
 * never render as the same sentence (the same split #380 made for
 * `OverviewFindingsState`). */
export type PurposeElementState = "present" | "unknown" | "unavailable";

/** The three fixed relation kinds of the minimal chain. Outcome-lineage
 * relations (#391) are a later, separate addition -- do not add a 4th value
 * here for them ahead of that issue. */
export type PurposeRelationKind =
  | "problem_to_change"
  | "change_to_intervention"
  | "intervention_to_capability";

/** A relation's status, first-match over its endpoints and its current
 * decision (server: `purpose_chain.derive_purpose_chain`). `unknown` is a
 * genuine fifth value, not folded into `hypothesis`: "the connection cannot
 * be explained because an endpoint has no content" is a different fact from
 * "the connection is an unconfirmed guess". */
export type PurposeRelationStatus =
  | "confirmed"
  | "hypothesis"
  | "conflicting"
  | "unknown"
  | "unavailable";

/** Whether a relation's DECISION still matches its endpoints' current
 * content -- independent of `status`: a stale confirmed decision reads as
 * `hypothesis` (status) while `recheck_state` explains *why*. The decision
 * row itself is never deleted or overwritten. */
export type PurposeRecheckState = "current" | "stale";

/** Why a relation went stale. `upstream_changed` exists because staleness
 * propagates exactly one direction (downstream) through the fixed chain;
 * `snapshot_changed` is the one reason that comes from an ELEMENT's
 * `evidence_stale` rather than from a captured decision digest mismatch. */
export type PurposeStaleReason =
  | "source_changed"
  | "target_changed"
  | "both_changed"
  | "upstream_changed"
  | "snapshot_changed";

/** 「今の判断に使えるか」, first-match over an element's own settledness and
 * its PRIMARY relation's status -- never a count, never an average
 * (`frame_resolution_level` is the 3-slot MIN, never a mean or a
 * percentage). `L3` needs an Outcome Criterion (#391) and is structurally
 * unreachable until that lands. */
export type PurposeResolutionLevel = "L0" | "L1" | "L2" | "L3";

/** Which existing table an element's content came from. `none` is a genuine
 * value (no row exists yet), not the absence of the field. */
export type PurposeSourceKind = "intent_item" | "understanding_claim" | "none";

/** The Purpose Frame's overall completeness, first-match over the 3 frame
 * slots' `state`. `unavailable` is reserved for a guarded-loader failure
 * while constructing a frame slot -- never for "nothing extracted yet"
 * (that is `empty`). */
export type PurposeFrameState = "complete" | "partial" | "empty" | "unavailable";

/** Which composed section of the Purpose Chain failed to derive. A guarded
 * loader records the section here and degrades ONLY that section -- a
 * relation-derivation failure must still return the frame. */
export type PurposeChainSection = "frame" | "relations" | "capabilities";

export interface PurposeElementOut {
  /** Stable across rebuilds: the bare kind for a frame-slot singleton, or
   * `kind + ":" + sha256(name)[:16]` for a kind that can repeat
   * (`core_capability`, and any `intervention` claim beyond the frame
   * slot). Never a row id -- a row id is reassigned on every rebuild while
   * describing the same element. */
  id: string;
  kind: PurposeElementKind;
  state: PurposeElementState;
  /** Level 0's 1〜2 文. The server never truncates; the Dashboard decides
   * how much of this to show. */
  display_statement: string;
  /** The element's full text (claim name + summary, or an Intent Brief
   * item's `value_text`). */
  statement: string;
  confirmation: UnderstandingConfirmationState;
  confirmation_label: string;
  provenance: UnderstandingProvenanceKind;
  provenance_label: string;
  resolution_level: PurposeResolutionLevel;
  source_kind: PurposeSourceKind;
  source_ids: string[];
  intent_revision_id: number | null;
  understanding_revision_id: number | null;
  snapshot_id: number | null;
  evidence: Record<string, unknown>[];
  /** Can only be `true` for a `provenance == "implementation_fact"`
   * element -- an Intent-sourced or AI-hypothesis element has no snapshot
   * pin to go stale against. */
  evidence_stale: boolean;
  /** Fixed sentences naming what is missing, only populated when
   * `state == "unknown"`. Never model output (Principle 6). */
  missing_information: string[];
  is_mock: boolean;
}

export interface PurposeRelationOut {
  /** `f"{kind}:{source_id}->{target_id}"` -- stable because both endpoint
   * ids are themselves stable. */
  id: string;
  kind: PurposeRelationKind;
  source_id: string;
  target_id: string;
  status: PurposeRelationStatus;
  status_label: string;
  recheck_state: PurposeRecheckState;
  stale_reason: PurposeStaleReason | null;
  provenance: UnderstandingProvenanceKind;
  provenance_label: string;
  /** The current (non-superseded) `purpose_relation_decision` row, if any. */
  decision_id: number | null;
  decided_at: number | null;
  decided_by: string | null;
  rationale: string;
  /** Carried from the TARGET element's own evidence. Never invented for the
   * relation itself. */
  evidence: Record<string, unknown>[];
  created_intent_revision_id: number | null;
  created_understanding_revision_id: number | null;
  created_snapshot_id: number | null;
  current_intent_revision_id: number | null;
  current_understanding_revision_id: number | null;
  current_snapshot_id: number | null;
}

export interface PurposeFrameOut {
  beneficiary_problem: PurposeElementOut | null;
  desired_change: PurposeElementOut | null;
  intervention: PurposeElementOut | null;
}

export interface PurposeChainOut {
  system_id: number;
  session_id: number | null;
  generated_at: number;
  frame: PurposeFrameOut;
  /** The frame's 3 elements plus any additional `intervention` claims and
   * every `core_capability` claim. */
  elements: PurposeElementOut[];
  relations: PurposeRelationOut[];
  /** The 3 frame slots' resolution level, MIN (never mean/percentage). */
  frame_resolution_level: PurposeResolutionLevel;
  frame_state: PurposeFrameState;
  snapshot_id: number | null;
  understanding_revision_id: number | null;
  understanding_confirmed_at: number | null;
  degraded_sections: PurposeChainSection[];
  degraded_detail: Record<string, string>;
}

/** The one write `POST /purpose-chain/relations/{relation_id}/decision`
 * performs. `decision_method` is always `manual` server-side -- there is no
 * field here for the caller to set it to anything else. */
export interface PurposeRelationDecisionRequest {
  session_id: number;
  decision: "confirmed" | "rejected";
  rationale?: string;
}

// --- Issue #389 need/question contract --------------------------------------
//
// Pinned in `docs/purpose-chain.md` §2 and binding for both #389 (server) and
// #390 (this Dashboard) regardless of implementation order -- #390's own
// component tests mock `fetch` directly, so they do not depend on the server
// module landing first. `app/purpose_needs.py` is the server-side owner.

/** Which need code fired. "Optional field is empty" is never a need code --
 * every value here is derived deterministically from the Purpose Chain
 * projection (an element `unknown`, a relation `unknown`/`conflicting`/
 * stale, etc.), never from a free-text absence check. */
export type PurposeNeedCode =
  | "frame_missing"
  | "relation_unknown"
  | "relation_conflict"
  | "capability_justification_missing"
  | "decision_criterion_missing"
  | "human_value_judgement_required"
  | "premise_recheck_required";

/** need code -> answerability is a FIXED table server-side, not the #286
 * free-text question router -- a system-generated need's answerability is
 * structurally known at creation, never guessed. `system_researchable` means
 * the Dashboard must never render a text-answer form for it: the need is
 * routed to investigation instead (§2.3). */
export type PurposeAnswerability =
  | "human_judgement"
  | "system_researchable"
  | "already_answered"
  | "unavailable";

export type PurposeNeedState = "available" | "waiting" | "answered" | "deferred" | "unavailable";

/** What a `POST .../respond` call records. `unknown` and `investigate` both
 * route to investigation but are audited as separate response kinds (§2.6). */
export type PurposeResponseKind = "confirm" | "correct" | "unknown" | "defer" | "investigate";

/** Why a deep-linked `need_id` could not be shown as-is. A deep link must
 * degrade SAFELY to the current question (or "no question"), never to an
 * error page -- the need it named may have been answered, may belong to
 * another System, or may since have been deferred. */
export type PurposeQuestionFallbackReason = "resolved" | "not_found" | "other_system" | "deferred";

export type PurposeNeedTargetKind = "element" | "relation";

/** An existing-row-only candidate answer. Never LLM-generated at this call
 * (§2.5: 根拠が無ければ候補を作らない, ここで LLM を呼ばない). */
export interface PurposeSuggestedAnswerOut {
  text: string;
  provenance: UnderstandingProvenanceKind;
  source_kind: PurposeSourceKind;
  source_ids: string[];
  is_mock: boolean;
}

/** One `system_researchable` need alongside the selected question (§2.4).
 * Informational only -- a routed need never reaches the developer as a
 * question itself; the Dashboard may use this to point at the Joint
 * Understanding investigation expected to answer it instead. */
export interface PurposeRoutedNeedOut {
  need_id: string;
  need_code: PurposeNeedCode;
  target_kind: PurposeNeedTargetKind;
  target_id: string;
  target_label: string;
}

/** `GET /purpose-chain/next-question`. At most ONE question -- the server's
 * `select_question` rule table (§2.4) returns 0 or 1, never a list, and the
 * Dashboard must not assemble a second one from any other source. */
export interface PurposeQuestionOut {
  need_id: string;
  need_code: PurposeNeedCode;
  /** The `PRIORITY_TABLE` row that chose this need, 1-based. Every need code
   * carries a row, so a need derived from the current projection always has
   * one; `null` only for a deep link naming a code the server does not know
   * (a forged row number would falsify which rule actually matched). */
  rule_row: number | null;
  prompt: string;
  why_now: string;
  blocked_decision: string;
  unlocks: string;
  defer_impact: string;
  target_kind: PurposeNeedTargetKind;
  target_id: string;
  target_label: string;
  answerability: PurposeAnswerability;
  suggested_answer: PurposeSuggestedAnswerOut | null;
  state: PurposeNeedState;
  source_revision_ids: number[];
  fallback_reason: PurposeQuestionFallbackReason | null;
  routed_needs: PurposeRoutedNeedOut[];
}

/** `POST /purpose-chain/needs/{need_id}/respond`. Mirrors the
 * `purpose_need_response` audit row (§2.6) -- confirm/correct/unknown/defer/
 * investigate are separately audited facts, never collapsed into one. */
export interface PurposeNeedResponseOut {
  id: number;
  session_id: number;
  system_id: number;
  need_id: string;
  need_code: PurposeNeedCode;
  response_kind: PurposeResponseKind;
  value_text: string;
  target_kind: PurposeNeedTargetKind;
  target_id: string;
  /** The target's digest AT RESPONSE TIME -- what a `defer` reappears
   * against once it no longer matches (§2.6). */
  target_digest: string;
  decision_method: string;
  responded_by: string | null;
  /** Set when `confirm`/`correct` was reused through the EXISTING Intent
   * Brief confirm/correct/create implementation (never a second
   * revision-chain implementation). */
  linked_intent_item_id: number | null;
  /** Set when `confirm`/`correct` was reused through
   * `purpose_chain.record_relation_decision`. */
  linked_relation_decision_id: number | null;
  /** Set when `unknown`/`investigate` opened a Joint Understanding session
   * with `trigger='purpose_need'`. */
  linked_joint_session_id: number | null;
  superseded_by_id: number | null;
  created_at: number;
}

/** `decision_method` is always `manual` server-side -- there is no field
 * here for the caller to set it to anything else, and (unlike the relation
 * decision request) there is no `rationale` field: a `correct` response's
 * free text IS `value_text`, whether it is a corrected value (an element
 * target) or a rejection reason (a relation target). */
export interface PurposeNeedRespondRequest {
  session_id: number;
  response_kind: PurposeResponseKind;
  value_text?: string;
}

// --- Purpose Verification: Experience / Outcome / Reuse (Issue #391) --------
//
// `docs/purpose-chain.md` §4 is the specification. Three OPTIONAL concepts a
// developer may attach to a Purpose Chain element/relation, by the SAME
// stable string identity (`target_kind`/`target_id`) #388 already uses --
// never a row id. Creation is offered only alongside a currently-available
// `app/purpose_needs.py` need (never because a resolution level is low), so
// every create request below carries a `need_id`.

/** `experience_hypothesis` and `reuse_hypothesis` share this exact lifecycle
 * (server: `app/purpose_verification.py`'s `PurposeHypothesisState`). */
export type PurposeHypothesisState = "proposed" | "confirmed" | "retired";

/** `purpose_outcome_criterion`'s own 6-value lifecycle. `observed` /
 * `contradicted` are set only together with a recorded verdict -- never
 * inferred from silence or from evidence text. `not_observed` ("analytics
 * が無ければ") / `not_computed` ("canonical mapping が無ければ") are their
 * own explicit facts, not a default value. */
export type PurposeOutcomeCriterionState =
  | "proposed"
  | "confirmed"
  | "observed"
  | "contradicted"
  | "not_observed"
  | "not_computed";

/** Which of the two evidence COLUMNS a result write targets (§4.2: human-
 * reported evidence and runtime observation stay in separate columns,
 * never merged into one "result"). */
export type PurposeOutcomeEvidenceSource = "human_reported" | "runtime_observed";
export type PurposeOutcomeEvidenceState =
  | "observed" | "contradicted" | "not_observed" | "not_computed";

/** A recorded verdict is always the developer's OWN reading of the
 * evidence -- never computed from the evidence text itself. */
export type PurposeOutcomeVerdict = "supports" | "contradicts";

/** Whether an `experiment_id` / `candidate_version_id` lineage column
 * resolves to a real, System-scoped row right now. `unresolved` (the id was
 * set but the row is gone) is a genuine third value -- §4.3: 対応が無けれ
 * ば「関連不明」と表示する -- never silently downgraded to `none`. */
export type PurposeOutcomeLineageState = "none" | "linked" | "unresolved";

/** Which of the three concepts a verification prompt or listing row is
 * about. Distinct from `PurposeNeedTargetKind` (element vs relation). */
export type PurposeVerificationConceptKind =
  | "experience_hypothesis"
  | "outcome_criterion"
  | "reuse_hypothesis";

export interface PurposeExperienceHypothesisOut {
  id: number;
  system_id: number;
  session_id: number;
  target_kind: PurposeNeedTargetKind;
  target_id: string;
  target_label: string;
  target_digest: string;
  source_need_id: string;
  source_need_code: PurposeNeedCode;
  statement: string;
  state: PurposeHypothesisState;
  decision_method: string;
  created_by: string | null;
  created_at: number;
  confirmed_by: string | null;
  confirmed_at: number | null;
  retired_by: string | null;
  retired_at: number | null;
  retirement_reason: string;
}

/** Identical shape to `PurposeExperienceHypothesisOut` -- a separate type
 * because the two live in separate tables and are never interchangeable. */
export interface PurposeReuseHypothesisOut {
  id: number;
  system_id: number;
  session_id: number;
  target_kind: PurposeNeedTargetKind;
  target_id: string;
  target_label: string;
  target_digest: string;
  source_need_id: string;
  source_need_code: PurposeNeedCode;
  statement: string;
  state: PurposeHypothesisState;
  decision_method: string;
  created_by: string | null;
  created_at: number;
  confirmed_by: string | null;
  confirmed_at: number | null;
  retired_by: string | null;
  retired_at: number | null;
  retirement_reason: string;
}

export interface PurposeOutcomeCriterionOut {
  id: number;
  system_id: number;
  session_id: number;
  target_kind: PurposeNeedTargetKind;
  target_id: string;
  target_label: string;
  target_digest: string;
  source_need_id: string;
  source_need_code: PurposeNeedCode;
  measure: string;
  baseline_value: string;
  target_value: string;
  observation_window: string;
  state: PurposeOutcomeCriterionState;
  /** §4.3: explicit lineage columns only, never a System-wide existence
   * check. */
  experiment_id: number | null;
  candidate_version_id: number | null;
  lineage_state: PurposeOutcomeLineageState;
  /** §4.2: two SEPARATE evidence columns, never merged into one "result". */
  human_reported_evidence: string | null;
  human_reported_verdict: PurposeOutcomeVerdict | null;
  human_reported_at: number | null;
  human_reported_by: string | null;
  human_reported_state: PurposeOutcomeEvidenceState | null;
  human_reported_is_synthetic: boolean;
  runtime_observation_text: string | null;
  runtime_observation_verdict: PurposeOutcomeVerdict | null;
  runtime_observed_at: number | null;
  runtime_observed_by: string | null;
  runtime_observation_state: PurposeOutcomeEvidenceState | null;
  runtime_observation_is_synthetic: boolean;
  /** §4.2: a synthetic fixture's result is never displayed as a real user's
   * outcome -- this flag must be shown alongside the result. */
  is_synthetic: boolean;
  decision_method: string;
  created_by: string | null;
  created_at: number;
  confirmed_by: string | null;
  confirmed_at: number | null;
}

export interface PurposeVerificationStateOut {
  system_id: number;
  session_id: number | null;
  experience_hypotheses: PurposeExperienceHypothesisOut[];
  outcome_criteria: PurposeOutcomeCriterionOut[];
  reuse_hypotheses: PurposeReuseHypothesisOut[];
}

/** §4.5: AT MOST ONE verification prompt. `null` at the endpoint level means
 * 「検証条件はまだ必要ありません」 -- there is no empty placeholder object. */
export interface PurposeVerificationPromptOut {
  concept_kind: PurposeVerificationConceptKind;
  need_id: string;
  need_code: PurposeNeedCode;
  target_kind: PurposeNeedTargetKind;
  target_id: string;
  target_label: string;
  prompt: string;
  why_now: string;
  blocked_decision: string;
  observation_hint: string;
}

export interface PurposeExperienceHypothesisCreateRequest {
  session_id: number;
  need_id: string;
  statement: string;
}

export interface PurposeReuseHypothesisCreateRequest {
  session_id: number;
  need_id: string;
  statement: string;
}

export interface PurposeOutcomeCriterionCreateRequest {
  session_id: number;
  need_id: string;
  measure: string;
  baseline_value: string;
  target_value: string;
  observation_window: string;
}

export interface PurposeVerificationSessionRequest {
  session_id: number;
}

export interface PurposeHypothesisRetireRequest {
  session_id: number;
  reason?: string;
}

export interface PurposeOutcomeCriterionLinkRequest {
  session_id: number;
  experiment_id?: number | null;
  candidate_version_id?: number | null;
}

export interface PurposeOutcomeResultRequest {
  session_id: number;
  source: PurposeOutcomeEvidenceSource;
  verdict: PurposeOutcomeVerdict;
  evidence_text: string;
  is_synthetic?: boolean;
}

export interface PurposeOutcomeUnavailableRequest {
  session_id: number;
  source: PurposeOutcomeEvidenceSource;
  state: "not_observed" | "not_computed";
  reason: string;
}

// --- Evolution Node (Epic #394 Phase 1, Issue #396) -------------------------
//
// These unions mirror the server's finite vocabularies
// (`app/evolution_node.py`, re-declared as Literals in `app/models.py`).
// They are the display vocabulary only -- the client never DECIDES any of
// them. `apps/control-server/tests/test_evolution_node_api.py` holds the two
// server-side definitions together; a drift between server and client shows
// up here as a compile error the first time an unknown value is handled.

export type EvolutionMaturityState =
  | "exploring" | "validating" | "established" | "monitoring" | "reopened" | "suspended";

export type EvolutionImplementationModality =
  | "reasoning_llm" | "lm_program" | "retrieval" | "router" | "small_model"
  | "rule" | "deterministic_code" | "workflow" | "manual" | "hybrid";

export type EvolutionLinkKind =
  | "component" | "probe_point" | "cell_binding" | "capability" | "flow"
  | "purpose_element" | "feature";

export interface EvolutionNodeSummary {
  id: number;
  system_id: number;
  node_key: string;
  display_name: string;
  maturity: EvolutionMaturityState;
  current_version_id: number | null;
  current_implementation_id: number | null;
  stable_implementation_id: number | null;
  rollback_implementation_id: number | null;
  monitoring_contract_ref: string | null;
  created_at: number;
  updated_at: number;
}

export interface EvolutionNodesListOut {
  nodes: EvolutionNodeSummary[];
}

export interface EvolutionNodeVersionOut {
  id: number;
  version_number: number;
  mission: string;
  scope: string;
  out_of_scope: string;
  input_contract: Record<string, unknown>;
  output_contract: Record<string, unknown>;
  side_effect_class: string;
  trust_boundary: string;
  establishment_criteria: string[];
  reopen_criteria: string[];
  evaluation_policy_refs: string[];
  decision_method: string;
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface EvolutionNodeImplementationOut {
  id: number;
  implementation_number: number;
  node_version_id: number;
  modality: EvolutionImplementationModality;
  config: Record<string, unknown>;
  snapshot_id: number | null;
  commit_sha: string | null;
  environment_ref: string | null;
  provenance: Record<string, unknown>;
  decision_method: string;
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface EvolutionNodeLinkOut {
  id: number;
  link_kind: EvolutionLinkKind;
  target_ref: string;
  target_row_id: number | null;
  note: string;
  decision_method: string;
  created_by: string | null;
  created_at: number;
}

export interface EvolutionNodeEventOut {
  id: number;
  event_kind: string;
  from_state: EvolutionMaturityState | null;
  to_state: EvolutionMaturityState | null;
  from_version_id: number | null;
  to_version_id: number | null;
  from_implementation_id: number | null;
  to_implementation_id: number | null;
  actor: string | null;
  actor_kind: string;
  decision_method: string;
  reason_code: string;
  reason: string;
  evidence: string[];
  idempotency_key: string | null;
  created_at: number;
}

export interface EvolutionNodeEventsOut {
  node_id: number;
  events: EvolutionNodeEventOut[];
}

/** The canonical Node document.
 *
 * `maturity`, `improvement_status` and `policy_mode` are three INDEPENDENT
 * axes (ADR-6) and must never be combined into one displayed label -- that
 * conflation is the #366 "one displayed word carrying two facts" defect. The
 * fourth axis (`workflow_phase`) is deliberately ABSENT from the document
 * rather than null; Phase 6 (#401) wires it.
 *
 * `availability[k] === false` means the block could not be read at all,
 * which is a different fact from a null value with `availability[k] === true`
 * (a genuine absence). The two get different copy. */
export interface EvolutionNodeProjectionOut {
  schema_version: string;
  system_id: number;
  node_id: number;
  node_key: string;
  display_name: string;
  maturity: EvolutionMaturityState;
  current_version: EvolutionNodeVersionOut | null;
  current_implementation: EvolutionNodeImplementationOut | null;
  stable_implementation: EvolutionNodeImplementationOut | null;
  rollback_implementation: EvolutionNodeImplementationOut | null;
  links: EvolutionNodeLinkOut[];
  events: EvolutionNodeEventOut[];
  improvement_status: string | null;
  policy_mode: string | null;
  availability: Record<string, boolean>;
  updated_at: number;
}

export interface EvolutionNodeLegacyProjectionOut {
  schema_version: string;
  compatibility_projection: boolean;
  system_id: number;
  node_id: number;
  node_key: string;
  component_id: string | null;
  probe_point_ref: string | null;
  cell_id: string | null;
  maturity: EvolutionMaturityState;
}

export interface EvolutionNodeTransitionOut {
  applied: boolean;
  duplicate: boolean;
  maturity: EvolutionMaturityState;
  event: EvolutionNodeEventOut | null;
}

// --- UX Design Lineage (Epic #405, Issues #407/#408) --------------------------
//
// TypeScript mirror of app/models.py's "UX Design Lineage" section. See
// docs/ux-design-lineage.md for the contract. Journey / Requirement /
// Solution Design are the two new PERSISTED design layers this Epic adds;
// every derived axis (design_status, option_status, link_state, ...) is
// computed server-side and rendered here, never recomputed by the
// Dashboard (§0 invariant 9).

export type UxJourneyPerspective = "as_is" | "to_be";

export type UxJourneyBaselineMode = "linked" | "greenfield" | "undecided";

export type UxJourneyBaselineState = "linked" | "unresolved" | "absent" | "not_applicable";

export type UxDesignAuthorshipKind = "developer" | "reasoning_model";

export type UxEvidenceSourceKind =
  | "runtime_trace"
  | "human_report"
  | "external_analytics"
  | "none";

export type UxRequirementKind = "functional" | "non_functional" | "constraint" | "out_of_scope";

export type UxVerificationMethod =
  | "manual_review"
  | "replay"
  | "experiment"
  | "runtime_observation"
  | "not_verifiable";

export type UxDesignStatus = "proposed" | "confirmed" | "rejected" | "retired";

export type UxDesignDecisionKind = "confirm" | "reject" | "retire" | "reinstate";

export type UxDesignRecheckState = "current" | "stale";

export type UxRevisionState = "current" | "superseded";

// Extended by Product Objective Lineage (Issue #427 §7.1) with
// "product_objective" / "product_milestone" / "product_gap" via a one-time
// table-rebuild migration that widens the CHECK without rewriting rows.
export type UxRefKind =
  | "purpose_element"
  | "purpose_relation"
  | "capability_entity"
  | "product_objective"
  | "product_milestone"
  | "product_gap";

export type UxRefRelationStatus = "confirmed" | "proposed" | "derived";

export type UxRefTargetResolution = "resolved" | "unresolved" | "unavailable";

export type UxRefRecheckState = "current" | "stale" | "not_captured";

export type UxArtifactKind =
  | "wireframe"
  | "adr"
  | "spec"
  | "diagram"
  | "research_note"
  | "other";

export type UxArtifactVerificationState = "verified" | "unverified" | "unreachable";

export type UxArtifactSubjectKind =
  | "journey"
  | "journey_step"
  | "requirement"
  | "solution_design"
  | "design_option";

export type UxDesignSubjectKind =
  | "journey"
  | "requirement"
  | "requirement_step_link"
  | "journey_upstream_ref"
  | "artifact_reference";

export type UxDiffChangeKind = "added" | "removed" | "changed" | "unchanged";

export type UxDiffState = "available" | "not_applicable" | "unavailable";

export type UxChangeOrigin =
  | "purpose"
  | "capability"
  | "journey"
  | "requirement"
  | "solution_design"
  | "implementation_target"
  | "snapshot";

export type SolutionDesignOptionDecision = "adopt" | "hold" | "reject" | "withdraw";

export type SolutionDesignOptionStatus =
  | "draft"
  | "adopted"
  | "held"
  | "rejected"
  | "withdrawn";

export type SolutionTargetKind =
  | "capability"
  | "static_flow"
  | "runtime_flow"
  | "evolution_node"
  | "component"
  | "cell_definition"
  | "cell_binding"
  | "probe_point";

export type SolutionLinkState = "current" | "stale" | "unresolved" | "unavailable";

export type SolutionLinkStaleReason =
  | "requirement_changed"
  | "design_changed"
  | "target_changed"
  | "snapshot_changed"
  | "upstream_changed";

export type SolutionHandoffState = "complete" | "incomplete" | "unavailable";

export interface UxJourneyStepOut {
  id: number;
  step_key: string;
  step_order: number;
  user_intent: string;
  system_response: string;
  success_criteria: string;
  failure_mode: string;
  recovery_path: string;
  evidence_expectation: string;
  evidence_source_kind: UxEvidenceSourceKind;
  content_digest: string;
}

export interface UxJourneyRevisionOut {
  id: number;
  journey_id: number;
  revision_number: number;
  title: string;
  beneficiary: string;
  usage_context: string;
  entry_trigger: string;
  value_arrival: string;
  summary: string;
  content_digest: string;
  authored_by_kind: UxDesignAuthorshipKind;
  decision_method: "manual" | "reasoning_llm";
  intelligence_run_id: number | null;
  change_note: string;
  created_by: string | null;
  created_at: number;
  revision_state: UxRevisionState;
  superseded_by_id: number | null;
  steps: UxJourneyStepOut[];
}

export interface UxJourneyUpstreamRefOut {
  id: number;
  journey_id: number;
  ref_kind: UxRefKind;
  target_ref: string;
  target_row_id: number | null;
  target_name: string | null;
  relation_status: UxRefRelationStatus;
  target_state: string;
  target_resolution: UxRefTargetResolution;
  recheck_state: UxRefRecheckState;
  captured_digest: string;
  captured_session_id: number | null;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface UxArtifactReferenceOut {
  id: number;
  subject_kind: UxArtifactSubjectKind;
  subject_key: string;
  artifact_kind: UxArtifactKind;
  title: string;
  uri: string;
  media_type: string;
  content_hash: string;
  hash_algorithm: "sha256";
  byte_size: number | null;
  verification_state: UxArtifactVerificationState;
  verified_snapshot_id: number | null;
  verified_commit_sha: string | null;
  verified_at: number | null;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface UxDesignDecisionOut {
  id: number;
  subject_kind: UxDesignSubjectKind;
  subject_key: string;
  subject_row_id: number | null;
  decision: UxDesignDecisionKind;
  rationale: string;
  captured_digest: string;
  captured_revision_id: number | null;
  decision_method: "manual";
  decided_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
}

export interface UxJourneyOut {
  id: number;
  system_id: number;
  journey_key: string;
  perspective: UxJourneyPerspective;
  baseline_mode: UxJourneyBaselineMode;
  baseline_journey_id: number | null;
  baseline_journey_key: string | null;
  baseline_state: UxJourneyBaselineState;
  current_revision_id: number | null;
  current_revision_number: number | null;
  title: string;
  design_status: UxDesignStatus;
  recheck_state: UxDesignRecheckState;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

export interface UxJourneyDetailOut extends UxJourneyOut {
  current_revision: UxJourneyRevisionOut | null;
  upstream_refs: UxJourneyUpstreamRefOut[];
  artifact_references: UxArtifactReferenceOut[];
  decisions: UxDesignDecisionOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface UxJourneyListOut {
  system_id: number;
  generated_at: number;
  journeys: UxJourneyOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface UxJourneyRevisionListOut {
  system_id: number;
  journey_id: number;
  journey_key: string;
  generated_at: number;
  revisions: UxJourneyRevisionOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface UxJourneyStepDiffEntryOut {
  step_key: string;
  change_kind: UxDiffChangeKind;
  from_step: UxJourneyStepOut | null;
  to_step: UxJourneyStepOut | null;
}

export interface UxJourneyDiffOut {
  system_id: number;
  journey_id: number;
  journey_key: string;
  generated_at: number;
  diff_state: UxDiffState;
  from_revision_id: number | null;
  from_revision_number: number | null;
  to_revision_id: number | null;
  to_revision_number: number | null;
  steps: UxJourneyStepDiffEntryOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface UxAcceptanceCriterionOut {
  id: number;
  criterion_key: string;
  criterion_order: number;
  statement: string;
  verification_method: UxVerificationMethod;
  verification_note: string;
  content_digest: string;
}

export interface UxRequirementRevisionOut {
  id: number;
  requirement_id: number;
  revision_number: number;
  requirement_kind: UxRequirementKind;
  statement: string;
  rationale: string;
  constraint_text: string;
  out_of_scope_note: string;
  content_digest: string;
  authored_by_kind: UxDesignAuthorshipKind;
  decision_method: "manual" | "reasoning_llm";
  intelligence_run_id: number | null;
  change_note: string;
  created_by: string | null;
  created_at: number;
  revision_state: UxRevisionState;
  superseded_by_id: number | null;
  acceptance_criteria: UxAcceptanceCriterionOut[];
}

export interface UxRequirementStepLinkOut {
  id: number;
  requirement_id: number;
  journey_id: number;
  journey_key: string | null;
  step_key: string;
  step_label: string | null;
  captured_journey_revision_id: number | null;
  captured_step_digest: string;
  target_resolution: UxRefTargetResolution;
  recheck_state: UxRefRecheckState;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface UxRequirementOut {
  id: number;
  system_id: number;
  requirement_key: string;
  requirement_kind: UxRequirementKind;
  current_revision_id: number | null;
  current_revision_number: number | null;
  statement: string;
  design_status: UxDesignStatus;
  recheck_state: UxDesignRecheckState;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

export interface UxRequirementDetailOut extends UxRequirementOut {
  current_revision: UxRequirementRevisionOut | null;
  step_links: UxRequirementStepLinkOut[];
  artifact_references: UxArtifactReferenceOut[];
  decisions: UxDesignDecisionOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface UxRequirementListOut {
  system_id: number;
  generated_at: number;
  requirements: UxRequirementOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface UxRequirementRevisionListOut {
  system_id: number;
  requirement_id: number;
  requirement_key: string;
  generated_at: number;
  revisions: UxRequirementRevisionOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface UxRequirementCriterionDiffEntryOut {
  criterion_key: string;
  change_kind: UxDiffChangeKind;
  from_criterion: UxAcceptanceCriterionOut | null;
  to_criterion: UxAcceptanceCriterionOut | null;
}

export interface UxRequirementDiffOut {
  system_id: number;
  requirement_id: number;
  requirement_key: string;
  generated_at: number;
  diff_state: UxDiffState;
  from_revision_id: number | null;
  from_revision_number: number | null;
  to_revision_id: number | null;
  to_revision_number: number | null;
  criteria: UxRequirementCriterionDiffEntryOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

// --- #407 write requests -------------------------------------------------

export interface UxJourneyCreateRequest {
  journey_key: string;
  perspective: UxJourneyPerspective;
  baseline_mode?: UxJourneyBaselineMode;
  baseline_journey_id?: number | null;
}

export interface UxJourneyStepInput {
  step_key: string;
  step_order: number;
  user_intent?: string;
  system_response?: string;
  success_criteria?: string;
  failure_mode?: string;
  recovery_path?: string;
  evidence_expectation?: string;
  evidence_source_kind?: UxEvidenceSourceKind;
}

export interface UxJourneyRevisionCreateRequest {
  title?: string;
  beneficiary?: string;
  usage_context?: string;
  entry_trigger?: string;
  value_arrival?: string;
  summary?: string;
  change_note?: string;
  steps?: UxJourneyStepInput[];
}

export interface UxJourneyUpstreamRefCreateRequest {
  ref_kind: UxRefKind;
  target_ref: string;
  note?: string;
}

export interface UxRequirementCreateRequest {
  requirement_key: string;
  requirement_kind: UxRequirementKind;
}

export interface UxAcceptanceCriterionInput {
  criterion_key: string;
  criterion_order: number;
  statement?: string;
  verification_method?: UxVerificationMethod;
  verification_note?: string;
}

export interface UxRequirementRevisionCreateRequest {
  statement?: string;
  rationale?: string;
  constraint_text?: string;
  out_of_scope_note?: string;
  change_note?: string;
  acceptance_criteria?: UxAcceptanceCriterionInput[];
}

export interface UxRequirementStepLinkCreateRequest {
  journey_key: string;
  step_key: string;
  note?: string;
}

export interface UxArtifactReferenceCreateRequest {
  subject_kind: UxArtifactSubjectKind;
  subject_key: string;
  artifact_kind: UxArtifactKind;
  title?: string;
  uri: string;
  media_type?: string;
  content_hash: string;
  byte_size?: number | null;
}

export interface UxDesignDecisionCreateRequest {
  subject_kind: UxDesignSubjectKind;
  subject_key: string;
  decision: UxDesignDecisionKind;
  rationale?: string;
  captured_digest?: string;
}

// --- Solution Design (Issue #408) -----------------------------------------

export interface SolutionDesignOptionOut {
  id: number;
  solution_design_id: number;
  option_key: string;
  option_order: number;
  title: string;
  approach: string;
  tradeoffs: string;
  risks: string;
  content_digest: string;
  authored_by_kind: UxDesignAuthorshipKind;
  decision_method: "manual" | "reasoning_llm";
  intelligence_run_id: number | null;
  option_status: SolutionDesignOptionStatus;
  created_by: string | null;
  created_at: number;
  revision_state: UxRevisionState;
  superseded_by_id: number | null;
}

export interface SolutionDesignRequirementLinkOut {
  id: number;
  solution_design_id: number;
  requirement_id: number;
  requirement_key: string | null;
  captured_requirement_revision_id: number | null;
  captured_digest: string;
  link_state: SolutionLinkState;
  stale_reason: SolutionLinkStaleReason | null;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface SolutionDesignTargetLinkOut {
  id: number;
  solution_design_id: number;
  option_id: number;
  option_key: string | null;
  target_kind: SolutionTargetKind;
  target_ref: string;
  target_row_id: number | null;
  target_name: string | null;
  captured_digest: string;
  captured_snapshot_id: number | null;
  link_state: SolutionLinkState;
  stale_reason: SolutionLinkStaleReason | null;
  review_required: boolean;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface SolutionDesignDecisionOut {
  id: number;
  solution_design_id: number;
  option_id: number;
  option_key: string;
  decision: SolutionDesignOptionDecision;
  rationale: string;
  captured_digest: string;
  decision_method: "manual";
  decided_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
}

export interface SolutionDesignOut {
  id: number;
  system_id: number;
  design_key: string;
  title: string;
  summary: string;
  adopted_option_key: string | null;
  option_count: number;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

export interface SolutionDesignDetailOut extends SolutionDesignOut {
  options: SolutionDesignOptionOut[];
  requirement_links: SolutionDesignRequirementLinkOut[];
  target_links: SolutionDesignTargetLinkOut[];
  decisions: SolutionDesignDecisionOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface SolutionDesignListOut {
  system_id: number;
  generated_at: number;
  designs: SolutionDesignOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface SolutionDesignChangeOriginEntryOut {
  origin: UxChangeOrigin;
  link_id: number;
  link_kind: "requirement_link" | "target_link";
  target_kind: SolutionTargetKind | null;
  target_ref: string | null;
  requirement_key: string | null;
  stale_reason: SolutionLinkStaleReason | null;
  detail: string;
}

export interface SolutionDesignChangeOriginsOut {
  system_id: number;
  solution_design_id: number;
  design_key: string;
  generated_at: number;
  origins: SolutionDesignChangeOriginEntryOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface SolutionDesignHandoffUnresolvedRefOut {
  kind: string;
  ref: string;
  reason: string;
}

export interface SolutionDesignHandoffOut {
  system_id: number;
  solution_design_id: number;
  design_key: string;
  generated_at: number;
  handoff_state: SolutionHandoffState;
  adopted_option: SolutionDesignOptionOut | null;
  target_links: SolutionDesignTargetLinkOut[];
  requirements: UxRequirementDetailOut[];
  node_decomposition_refs: Record<string, unknown>[];
  probe_plan_refs: Record<string, unknown>[];
  evaluation_policy_refs: Record<string, Record<string, unknown>[]>;
  unresolved_references: SolutionDesignHandoffUnresolvedRefOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

// --- #408 write requests -------------------------------------------------

export interface SolutionDesignCreateRequest {
  design_key: string;
  title?: string;
  summary?: string;
}

export interface SolutionDesignOptionCreateRequest {
  option_key: string;
  option_order: number;
  title?: string;
  approach?: string;
  tradeoffs?: string;
  risks?: string;
}

export interface SolutionDesignRequirementLinkCreateRequest {
  requirement_key: string;
  note?: string;
}

export interface SolutionDesignTargetLinkCreateRequest {
  option_key: string;
  target_kind: SolutionTargetKind;
  target_ref: string;
  captured_snapshot_id?: number | null;
  note?: string;
}

export interface SolutionDesignOptionDecisionCreateRequest {
  option_key: string;
  decision: SolutionDesignOptionDecision;
  rationale?: string;
}

// --- Execution modes / Flow agents (Epic #412, Issues #413/#414/#415) -------
//
// These unions mirror the server's finite vocabularies verbatim
// (`app/execution_mode.py`, `app/flow_explanation.py`,
// `app/flow_orchestration.py`, re-declared as `Literal`s in `app/models.py`).
// They are DISPLAY vocabulary only. The client never decides an execution
// mode, a proposal status, a section's availability or a Node's axis value —
// every one of those arrives already decided (#349's "one canonical state
// engine"), and a new value appearing on the server surfaces here as a
// compile error rather than as a silently mislabelled badge.
//
// Two collapses are forbidden by the shapes themselves:
//   * `ExecutionMode` (control-plane permission) and a Component's SDK policy
//     mode are separate fields, and both may read "shadow" while meaning two
//     different facts (§1.2).
//   * `mode_source: "default"` (the fail-closed `fixed` nobody chose) is a
//     different value from `"system"` + reason `"system_assignment"` (a human
//     who chose `fixed`) — §4.4.

export type ExecutionMode = "fixed" | "observe" | "propose" | "shadow";

export type ExecutionModeScopeKind = "system" | "flow" | "node";

export type ExecutionModeScopeState =
  | "unset" | "revoked" | "pending" | "expired" | "invalid" | "active" | "conflicting";

export type ExecutionCapability =
  | "observation_record"
  | "llm_experiment_proposal"
  | "candidate_execution"
  | "shadow_comparison";

/** The ten reason codes of §3.3's ten-row resolution table, one per row. The
 * three elapsed-window rows carry SEPARATE codes on purpose: all clamp to
 * `fixed`, but the developer's next action differs per scope (#366). */
export type ExecutionModeReason =
  | "conflicting_assignments"
  | "invalid_mode_value"
  | "flow_scope_not_member"
  | "node_expired_assignment"
  | "node_assignment"
  | "flow_expired_assignment"
  | "flow_scope_conflict"
  | "flow_assignment"
  | "system_expired_assignment"
  | "system_assignment"
  | "no_assignment";

export type ExecutionModeSourceScope = "node" | "flow" | "system" | "none" | "default";

export type ExecutionModeDenialCode =
  | "capability_not_permitted"
  | "conflicting_assignments"
  | "invalid_mode_value"
  | "flow_scope_not_member"
  | "node_expired_assignment"
  | "flow_expired_assignment"
  | "system_expired_assignment"
  | "flow_scope_conflict"
  | "unknown_capability"
  | "node_not_found";

export type ExecutionModeDivergence = "match" | "divergent" | "unobserved" | "stale";

export type ExecutionModeRecordKind = "assign" | "revoke";

export type ExecutionModeObservationSource = "control_server" | "sdk";

/** The standing of an observation's `run_ref`. Derived, never stored:
 * nothing on this path resolves the pointer against a canonical execution
 * row, so there is no `resolved` value to report. "This row cites a run" and
 * "this row's citation was checked" must stay distinguishable (#366). */
export type ExecutionModeRunRefState = "absent" | "uncorroborated" | "corroborated";

export interface ExecutionModeScopeReadingOut {
  scope_kind: ExecutionModeScopeKind;
  scope_ref: string;
  state: ExecutionModeScopeState;
  mode: ExecutionMode | null;
  assignment_id: number | null;
  effective_from: number | null;
  effective_until: number | null;
  open_row_count: number;
}

export interface ExecutionModeDecisionOut {
  mode: ExecutionMode;
  source_scope: ExecutionModeSourceScope;
  source_ref: string;
  reason: ExecutionModeReason;
  permitted_capabilities: ExecutionCapability[];
  scope_trace: ExecutionModeScopeReadingOut[];
}

export interface ExecutionModeAssignmentOut {
  id: number;
  system_id: number;
  record_kind: ExecutionModeRecordKind;
  scope_kind: ExecutionModeScopeKind;
  scope_ref: string;
  /** NULL on a `revoke` row: a revocation ends an assignment, it does not
   * name a new mode. */
  mode: ExecutionMode | null;
  previous_mode: ExecutionMode | null;
  effective_from: number | null;
  effective_until: number | null;
  reason: string;
  actor_kind: string;
  actor: string | null;
  decision_method: string;
  supersedes_id: number | null;
  superseded_by_id: number | null;
  schema_version: string;
  created_at: number;
  scope_state: ExecutionModeScopeState | null;
}

export interface ExecutionModeAssignRequest {
  scope_kind: ExecutionModeScopeKind;
  scope_ref?: string;
  mode: ExecutionMode;
  /** Required by the server: an unexplained permission change cannot be
   * reviewed (§5.1). `actor` is deliberately NOT a request field. */
  reason: string;
  effective_from?: number | null;
  effective_until?: number | null;
}

export interface ExecutionModeRevokeRequest {
  reason: string;
}

export interface ExecutionModeDivergenceOut {
  node_key: string;
  divergence: ExecutionModeDivergence;
  effective_mode: ExecutionMode;
  observed_mode: ExecutionMode | null;
  observed_at: number | null;
  last_assignment_at: number | null;
  /** A SEPARATE axis from `divergence`: the first says whether the reading
   * agrees with the configuration, these say whether the reading was
   * measured. No current path attests a runtime mode, so a `match` can be
   * agreement with a value a human reported. Both null for `unobserved`. */
  observation_source: ExecutionModeObservationSource | null;
  run_ref_state: ExecutionModeRunRefState | null;
}

export interface ExecutionModeDivergenceListOut {
  system_id: number;
  generated_at: number;
  nodes: ExecutionModeDivergenceOut[];
}

export interface ExecutionModeNodeProjectionOut {
  node_id: number;
  node_key: string;
  maturity: EvolutionMaturityState;
  execution_mode: ExecutionMode;
  mode_source: ExecutionModeSourceScope;
  mode_reason: ExecutionModeReason;
  source_ref: string;
  flow_refs: string[];
  divergence: ExecutionModeDivergence;
  observed_mode: ExecutionMode | null;
  observed_at: number | null;
  observation_source: ExecutionModeObservationSource | null;
  run_ref_state: ExecutionModeRunRefState | null;
}

export interface ExecutionModeProjectionOut {
  system_id: number;
  schema_version: string;
  generated_at: number;
  system_decision: ExecutionModeDecisionOut;
  assignments: ExecutionModeAssignmentOut[];
  nodes: ExecutionModeNodeProjectionOut[];
}

/** The 409 body of a refused capability gate (§4.1). `decision` is null for
 * `unknown_capability` / `node_not_found`: those describe a broken request,
 * not a mode reading. */
export interface ExecutionModeDenialOut {
  denial_code: ExecutionModeDenialCode;
  message: string;
  mode: ExecutionMode | null;
  source_scope: ExecutionModeSourceScope | null;
  reason: ExecutionModeReason | null;
  decision: ExecutionModeDecisionOut | null;
}

// --- Flow explanation projection (Issue #414) ------------------------------

export type FlowSubjectKind = "runtime_flow" | "static_flow";

export type FlowSubjectResolution = "resolved" | "unresolved" | "unavailable";

/** §6.4's five missing answers plus `present`. `present` is the ABSENCE of a
 * missing answer, never a sixth one, and the five never share copy. */
export type FlowFactState =
  | "present" | "missing" | "unavailable" | "unmeasured" | "stale" | "not_applicable";

export type FlowMembershipState = "resolved" | "unavailable";

export type FlowMembershipBasis = "flow_link" | "probe_point_exact_match";

export type FlowEvidenceKind =
  | "trace"
  | "anomaly"
  | "drift_observation"
  | "node_event"
  | "code_location"
  | "execution_ref"
  | "stabilization_package";

export type FlowOpenItemKind =
  | "anomaly"
  | "missing_fact"
  | "unmeasured_observation"
  | "mode_divergence"
  | "unresolved_membership"
  | "stale_premise"
  | "maturity_drift";

export type FlowEdgeSource =
  | "runtime_span_parentage" | "static_call_graph" | "unavailable" | "not_applicable";

export interface FlowEvidenceOut {
  id: string;
  kind: FlowEvidenceKind;
  ref: string;
  label: string;
  node_key: string | null;
  recorded_at: number | null;
}

export interface FlowSubjectOut {
  subject_kind: FlowSubjectKind;
  subject_ref: string;
  label: string;
  resolution: FlowSubjectResolution;
  /** `not_applicable` for a runtime Flow — it carries no snapshot. That is a
   * different answer from `missing` (a static Flow with no snapshot). */
  snapshot_state: FlowFactState;
  /** The two independent facts behind a runtime Flow's `resolution`:
   * `observation_state` is whether spans have ever been seen under this
   * `flow_id`, `model_state` whether any Node is currently linked to it. A
   * Flow modelled onto Nodes but never run is `resolved` + `missing` +
   * `present` — never render one of the pair as the other (#366). Both are
   * `not_applicable` for a static Flow. Optional only because an older
   * Control Server does not send them. */
  observation_state?: FlowFactState;
  model_state?: FlowFactState;
  snapshot_id: number | null;
  commit_sha: string | null;
  detail: string;
}

export interface FlowMembershipOut {
  state: FlowMembershipState;
  node_keys: string[];
  basis: Record<string, FlowMembershipBasis>;
  detail: string;
}

export interface FlowExplanationNodeOut {
  node_id: number;
  node_key: string;
  display_name: string;
  membership_basis: FlowMembershipBasis;

  execution_mode: ExecutionMode;
  mode_source: ExecutionModeSourceScope;
  mode_reason: ExecutionModeReason;
  mode_source_ref: string;
  mode_state: FlowFactState;
  mode_divergence: ExecutionModeDivergence | null;
  observed_mode: ExecutionMode | null;
  mode_observation_source: ExecutionModeObservationSource | null;
  mode_observation_run_ref_state: ExecutionModeRunRefState | null;

  maturity: EvolutionMaturityState | null;
  maturity_state: FlowFactState;
  folded_maturity: EvolutionMaturityState | null;
  maturity_consistent: boolean | null;

  implementation_modality: EvolutionImplementationModality | null;
  implementation_modality_state: FlowFactState;

  improvement_status: string | null;
  improvement_status_state: FlowFactState;

  /** The SDK's `off`/`trace`/`shadow` policy — a DIFFERENT fact from
   * `execution_mode`, even when both read "shadow" (§1.2). */
  sdk_policy_mode: string | null;
  sdk_policy_mode_state: FlowFactState;

  observation: Record<string, unknown> | null;
  observation_state: FlowFactState;
  monitoring_contract_declared: boolean;

  evidence: FlowEvidenceOut[];
  capability_refs: string[];
  purpose_element_refs: string[];
  feature_refs: string[];
}

export interface FlowPurposeRefOut {
  ref: string;
  label: string;
  resolution?: string;
  state?: string;
  node_keys: string[];
}

export interface FlowPurposeSectionOut {
  purpose_elements: FlowPurposeRefOut[];
  capabilities: FlowPurposeRefOut[];
  features: FlowPurposeRefOut[];
  purpose_chain_state: FlowFactState;
  detail: string;
}

export interface FlowResponsibilityContractOut {
  node_key: string;
  state: FlowFactState;
  mission?: string;
  scope?: string;
  out_of_scope?: string;
  input_contract?: Record<string, unknown>;
  output_contract?: Record<string, unknown>;
  side_effect_class?: string | null;
  trust_boundary?: string | null;
}

export interface FlowResponsibilityEdgeOut {
  source: string;
  target: string;
  edge_kind: string;
  trace_id?: string;
  /** Static call-graph edges only. `resolution` keeps an INFERRED callee
   * distinguishable from a verified one -- a guessed dependency must never
   * read as an observed fact. Absent on runtime (trace-derived) edges. */
  resolution?: "resolved" | "inferred" | "unresolved";
  callee_name?: string;
  line?: number;
}

export interface FlowExternalBoundaryOut {
  node_id: string;
  boundary_kind: string;
  qualified_name: string;
}

export interface FlowResponsibilitySectionOut {
  edge_source: FlowEdgeSource;
  node_order: string[];
  edges: FlowResponsibilityEdgeOut[];
  contracts: FlowResponsibilityContractOut[];
  external_boundaries: FlowExternalBoundaryOut[];
  entry_ref: string | null;
  entry_state: FlowFactState;
  truncated: boolean;
  diagnostics: string[];
}

export interface FlowOpenItemOut {
  id: string;
  kind: FlowOpenItemKind;
  label: string;
  detail: string;
  node_key: string | null;
  missing_state: FlowFactState | null;
  evidence_ids: string[];
}

export interface FlowOpenItemsSectionOut {
  items: FlowOpenItemOut[];
}

export interface FlowExperimentSummaryOut {
  proposal_id: number;
  proposal_key: string;
  title: string;
  comparison_scope: string;
  /** #415's own event fold, never re-implemented client-side. `null` with
   * `status_state: "unavailable"` when that definition could not be loaded. */
  status: FlowExperimentStatus | null;
  status_state: FlowFactState;
  target_node_keys: string[];
  evidence_refs: unknown[];
  execution_refs: Record<string, unknown>[];
  isolation_strategy: string;
  expires_at: number | null;
  created_at: number | null;
}

export interface FlowExperimentsSectionOut {
  proposals: FlowExperimentSummaryOut[];
  status_source: string;
}

export interface FlowNodeBaselineOut {
  node_key: string;
  stable_implementation: Record<string, unknown> | null;
  stable_state: FlowFactState;
  rollback_implementation: Record<string, unknown> | null;
  rollback_state: FlowFactState;
  approval: Record<string, unknown> | null;
  approval_state: FlowFactState;
}

export interface FlowBaselineSectionOut {
  nodes: FlowNodeBaselineOut[];
}

/** A `null` section PLUS its name in `degraded_sections` means "could not be
 * read"; an empty section means "there is nothing in it". Two answers (#356). */
export interface FlowExplanationOut {
  system_id: number;
  schema_version: string;
  generated_at: number;
  subject: FlowSubjectOut;
  membership: FlowMembershipOut;
  purpose: FlowPurposeSectionOut | null;
  responsibility: FlowResponsibilitySectionOut | null;
  nodes: FlowExplanationNodeOut[] | null;
  open_items: FlowOpenItemsSectionOut | null;
  experiments: FlowExperimentsSectionOut | null;
  baseline: FlowBaselineSectionOut | null;
  drilldown: Record<string, unknown>;
  rollup: Record<string, unknown>[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

/** A runtime Flow is listed when spans were observed under it OR a Node
 * currently declares it belongs to it — the same disjunction #415 uses to
 * decide a Flow subject is known. `trace_count: 0` is NOT what says "nothing
 * ran here"; `observation_state` is. */
export interface FlowRuntimeSubjectOut {
  subject_kind: "runtime_flow";
  subject_ref: string;
  label: string;
  trace_count: number;
  first_at: number | null;
  last_at: number | null;
  linked_node_count: number;
  /** Optional only because an older Control Server does not send them. */
  observation_state?: FlowFactState;
  model_state?: FlowFactState;
}

export interface FlowStaticSubjectOut {
  subject_kind: "static_flow";
  subject_ref: string;
  label: string;
  entrypoint_type: string | null;
  category: string | null;
  handler_path: string | null;
  handler_qualified_name: string | null;
  snapshot_id: number;
}

/** The two kinds are two lists on purpose — merging them would be exactly the
 * one-word-two-facts collapse §2.1 forbids. */
export interface FlowSubjectListOut {
  system_id: number;
  generated_at: number;
  runtime_flows: FlowRuntimeSubjectOut[];
  static_flows: FlowStaticSubjectOut[];
  snapshot_id: number | null;
  snapshot_state: FlowFactState;
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

// --- Flow experiment orchestration (Issue #415) ----------------------------

export type FlowComparisonScope = "single_node" | "sub_pipeline";

export type FlowIsolationStrategy =
  | "pure" | "mock" | "dry_run" | "rollback_transaction" | "isolated_workspace" | "none";

export type FlowEvaluationLevel = "node" | "flow_capability" | "ux_outcome";

export type FlowExperimentTargetRole = "baseline" | "candidate_target";

export type FlowExperimentEventKind =
  | "proposed"
  | "approved"
  | "rejected"
  | "withdrawn"
  | "expired"
  | "execution_recorded"
  | "result_recorded"
  | "promotion_candidate_recorded"
  | "rollback_recorded";

/** DERIVED by folding `flow_experiment_event` on every read (§7.4). There is
 * no `status` column behind this value and the client must never compute one. */
export type FlowExperimentStatus =
  | "proposed" | "approved" | "rejected" | "withdrawn" | "expired" | "executing" | "completed";

export type FlowExperimentExecutionKind =
  | "replay_variant_run" | "experiment" | "shadow_result";

export type FlowExecutionRefResolution = "resolved" | "unresolved" | "stale";

/** What a caller ASKS for, as opposed to what gets recorded: a refused action
 * writes no event at all. The Flow・エージェント群 screen deliberately offers
 * only the three human decisions (`approve` / `reject` / `withdraw`) — the
 * four recording actions belong to the paths that actually ran something. */
export type FlowExperimentActionKind =
  | "approve"
  | "reject"
  | "withdraw"
  | "record_execution"
  | "record_result"
  | "record_promotion_candidate"
  | "record_rollback";

export interface FlowExperimentTargetOut {
  id: number;
  target_node_key: string;
  target_role: FlowExperimentTargetRole;
  position: number;
  note: string;
}

export interface FlowExperimentEventOut {
  id: number;
  event_kind: FlowExperimentEventKind;
  actor_kind: string;
  actor: string | null;
  reason: string;
  decision_method: string;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface FlowExperimentExecutionRefOut {
  id: number;
  execution_kind: FlowExperimentExecutionKind;
  execution_ref: string;
  note: string;
  recorded_at: number;
  resolution: FlowExecutionRefResolution;
}

export interface FlowEvaluationAxisOut {
  level: FlowEvaluationLevel;
  name: string;
  metric?: string;
  detail?: string;
}

export interface FlowExperimentProposalOut {
  schema_version: string;
  id: number;
  system_id: number;
  proposal_key: string;
  flow_subject_kind: FlowSubjectKind;
  flow_subject_ref: string;
  captured_snapshot_id: number | null;
  comparison_scope: FlowComparisonScope;
  title: string;
  purpose: string;
  hypothesis: string;
  baseline_ref: string;
  candidate_refs: unknown[];
  evaluation_axes: FlowEvaluationAxisOut[];
  /** A GROUPING of the same axes by their declared level — never a
   * derivation, and never summed into one number (ADR-7). */
  evaluation_axes_by_level: Record<string, FlowEvaluationAxisOut[]>;
  quality_floor: Record<string, unknown>;
  isolation_strategy: FlowIsolationStrategy;
  isolation_detail: string;
  cost_cap: Record<string, unknown>;
  stop_conditions: unknown[];
  rollback_plan: string;
  evidence_refs: unknown[];
  expires_at: number | null;
  decision_method: string;
  intelligence_run_id: number | null;
  created_by: string | null;
  created_at: number;
  status: FlowExperimentStatus;
  status_derived_at: number;
  targets: FlowExperimentTargetOut[];
  events: FlowExperimentEventOut[];
  executions: FlowExperimentExecutionRefOut[];
  /** Each one is a CANDIDATE record and carries `promotion_performed: false`
   * (§7.6). Recording a candidate is not a promotion. */
  promotion_candidates: FlowExperimentEventOut[];
}

export interface FlowExperimentListOut {
  system_id: number;
  generated_at: number;
  proposals: FlowExperimentProposalOut[];
}

export interface FlowExperimentDecisionRequest {
  reason?: string;
}


// === Epic #418 / Issue #422 — Stakeholder Value Network types ===
// (Issue #422 owns everything between this marker and the #423 marker below.)
//
// `GET /stakeholder-value-network` (`docs/stakeholder-value-network.md`
// §7.1). Read-only, deterministic, no LLM; the Dashboard renders this
// exactly as returned and re-derives nothing (§0 invariant 9). No
// coordinate/layout field exists on any type below (invariant 10), and no
// score/percentage/centrality field exists either (invariant 7). These
// types are self-contained -- Issues #420/#421 have not yet added their own
// Dashboard-side types, so nothing here assumes or depends on one.

export type ValueNetworkStakeholderKind =
  | "end_user" | "customer_organization" | "internal_operator"
  | "provider_team" | "partner" | "regulator" | "other";

export type ValueNetworkStakeholderRole =
  | "actor" | "beneficiary" | "payer" | "operator"
  | "approver" | "supplier" | "regulator" | "observer";

export type ValueNetworkDesignStatus = "proposed" | "confirmed" | "rejected" | "retired";

export type ValueNetworkRecheckState = "current" | "stale";

export type ValueNetworkAuthorshipKind = "developer" | "reasoning_model";

export type ValueNetworkEvidenceState = "available" | "missing" | "stale" | "unavailable";

export type ValueNetworkExchangeKind =
  | "experience" | "service" | "information" | "money" | "authority" | "obligation" | "risk";

export type ValueNetworkConsiderationState = "present" | "none" | "unknown";

export type ValueNetworkCadence = "one_time" | "recurring" | "continuous" | "on_demand" | "unknown";

export type ValueNetworkValidityState = "not_started" | "active" | "ended" | "unbounded";

export type ValueNetworkRefKind =
  | "purpose_element" | "purpose_relation" | "capability_entity"
  | "ux_journey" | "ux_journey_step" | "ux_requirement"
  | "purpose_outcome_criterion" | "stakeholder" | "stakeholder_need" | "value_exchange";

export type ValueNetworkRefTargetResolution = "resolved" | "unresolved" | "unavailable";

export type ValueNetworkRefRecheckState = "current" | "stale" | "not_captured";

export type ValueNetworkRefRelationStatus = "confirmed" | "proposed" | "derived";

/** §7.2's eleven structural notice codes -- observations about an absent
 * link, never a judgement of importance or value. Held in parity with the
 * server's `ValueNetworkNoticeCode` by
 * `test_interview_type_parity.py`'s `FINITE_TYPE_NAMES`. */
export type ValueNetworkNoticeCode =
  | "stakeholder_without_exchange"
  | "stakeholder_without_role"
  | "stakeholder_without_need"
  | "payer_differs_from_beneficiary"
  | "exchange_without_need"
  | "exchange_without_journey"
  | "exchange_without_outcome"
  | "confirmed_without_evidence"
  | "feedback_path_missing"
  | "stale_link"
  | "stale_confirmation";

export interface ValueNetworkRelatedRefOut {
  id: number;
  source_kind: string;
  source_key: string;
  ref_kind: ValueNetworkRefKind;
  target_ref: string;
  target_row_id: number | null;
  relation_status: ValueNetworkRefRelationStatus;
  target_resolution: ValueNetworkRefTargetResolution;
  recheck_state: ValueNetworkRefRecheckState;
  captured_digest: string;
  note: string;
  decision_method: string;
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ValueNetworkNodeOut {
  stakeholder_key: string;
  display_name: string;
  stakeholder_kind: ValueNetworkStakeholderKind;
  /** System-scope role assignments only -- a Journey/Step/Exchange-scoped
   * role belongs to the Service Blueprint (#423), not to this graph. */
  roles: ValueNetworkStakeholderRole[];
  design_status: ValueNetworkDesignStatus;
  recheck_state: ValueNetworkRecheckState;
  authored_by_kind: ValueNetworkAuthorshipKind;
  evidence_state: ValueNetworkEvidenceState;
}

export interface ValueNetworkConsiderationOut {
  consideration_state: ValueNetworkConsiderationState;
  consideration_kind: ValueNetworkExchangeKind | null;
  consideration_statement: string;
}

export interface ValueNetworkEdgeOut {
  exchange_key: string;
  provider_stakeholder_key: string;
  receiver_stakeholder_key: string;
  exchange_kind: ValueNetworkExchangeKind | null;
  value_statement: string;
  consideration: ValueNetworkConsiderationOut;
  channel: string;
  trigger: string;
  cadence: ValueNetworkCadence;
  design_status: ValueNetworkDesignStatus;
  recheck_state: ValueNetworkRecheckState;
  validity_state: ValueNetworkValidityState;
  evidence_state: ValueNetworkEvidenceState;
  related_refs: ValueNetworkRelatedRefOut[];
}

export interface ValueNetworkNoticeOut {
  code: ValueNetworkNoticeCode;
  subject_kind: "stakeholder" | "value_exchange";
  subject_key: string;
}

export interface ValueNetworkOut {
  system_id: number;
  generated_at: number;
  nodes: ValueNetworkNodeOut[];
  edges: ValueNetworkEdgeOut[];
  notices: ValueNetworkNoticeOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}


// === Epic #418 / Issue #423 — Journey Service Blueprint types ===
// (Issue #423 owns everything below this marker.)
//
// `GET /journey-blueprint` (`docs/stakeholder-value-network.md` §8).
// Read-only, deterministic, no LLM; the Dashboard renders this exactly as
// returned and re-derives nothing (§0 invariant 9). These types are
// self-contained, mirroring the `ValueNetwork*` section above's own
// approach of not depending on #420/#421 Dashboard types that don't exist
// yet.

export type BlueprintLaneKind =
  | "stakeholder_action" | "touchpoint" | "frontstage" | "backstage"
  | "support" | "external" | "requirement" | "evidence" | "failure_recovery";

/** `unknown` ("nobody has described this yet") and `not_applicable` ("this
 * lane structurally does not apply") are DISTINCT and neither is
 * auto-filled; `unavailable` is reserved for a guarded loader's own read
 * failure, never "nothing recorded yet" (§8.1). */
export type BlueprintLaneState = "present" | "unknown" | "not_applicable" | "unavailable";

export type JourneyDeliveryKind = "frontstage" | "backstage" | "support" | "external";

export type BlueprintDeliveryTargetKind = "ux_requirement" | "stakeholder" | "value_exchange" | "not_applicable";

/** §8.3's Step diff kind -- deliberately includes `reordered`, which the
 * pre-existing `UxDiffChangeKind` above does not. */
export type BlueprintDiffChangeKind = "added" | "removed" | "changed" | "reordered" | "unchanged";

export type BlueprintStakeholderRole =
  | "actor" | "beneficiary" | "payer" | "operator"
  | "approver" | "supplier" | "regulator" | "observer";

export type BlueprintRefTargetResolution = "resolved" | "unresolved" | "unavailable";

export type BlueprintRefRecheckState = "current" | "stale" | "not_captured";

export interface JourneyStepStakeholderLinkOut {
  id: number;
  journey_id: number;
  journey_key: string;
  step_key: string;
  step_label: string | null;
  stakeholder_key: string;
  stakeholder_name: string | null;
  role: BlueprintStakeholderRole;
  target_resolution: BlueprintRefTargetResolution;
  recheck_state: BlueprintRefRecheckState;
  note: string;
  decision_method: string;
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface BlueprintImplementationRefOut {
  design_key: string;
  title: string;
  adopted_option_key: string | null;
  target_kind: string | null;
  target_ref: string | null;
}

export interface JourneyStepDeliveryLinkOut {
  id: number;
  journey_id: number;
  journey_key: string;
  step_key: string;
  step_label: string | null;
  delivery_kind: JourneyDeliveryKind;
  target_kind: BlueprintDeliveryTargetKind;
  target_ref: string;
  target_name: string | null;
  target_resolution: BlueprintRefTargetResolution;
  recheck_state: BlueprintRefRecheckState;
  /** Lane 4 (backstage) enrichment only -- resolved through #405's existing
   * Requirement -> Solution Design chain, never a second Flow/Node
   * reference (§5.2). */
  implementation_refs: BlueprintImplementationRefOut[];
  note: string;
  decision_method: string;
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface JourneyStepExchangeLinkOut {
  id: number;
  journey_id: number;
  journey_key: string;
  step_key: string;
  step_label: string | null;
  exchange_key: string;
  exchange_kind: string | null;
  channel: string | null;
  target_resolution: BlueprintRefTargetResolution;
  recheck_state: BlueprintRefRecheckState;
  note: string;
  decision_method: string;
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface BlueprintRequirementRefOut {
  requirement_key: string;
  statement: string | null;
  target_resolution: BlueprintRefTargetResolution;
  design_status: string | null;
}

export interface BlueprintEvidenceRefOut {
  exchange_key: string;
  evidence_kind: string;
  statement: string;
  created_at: number;
}

export interface BlueprintLaneCellOut {
  lane_kind: BlueprintLaneKind;
  state: BlueprintLaneState;
  summary: string;
  stakeholder_links: JourneyStepStakeholderLinkOut[];
  delivery_links: JourneyStepDeliveryLinkOut[];
  exchange_links: JourneyStepExchangeLinkOut[];
  requirement_refs: BlueprintRequirementRefOut[];
  evidence_refs: BlueprintEvidenceRefOut[];
}

export interface BlueprintStepOut {
  step_key: string;
  step_order: number;
  user_intent: string;
  system_response: string;
  lanes: Record<string, BlueprintLaneCellOut>;
}

export interface BlueprintOut {
  journey_key: string;
  perspective: UxJourneyPerspective;
  baseline_state: UxJourneyBaselineState;
  current_revision_number: number | null;
  steps: BlueprintStepOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface BlueprintDiffStepEntryOut {
  step_key: string;
  change_kind: BlueprintDiffChangeKind;
  from_step_order: number | null;
  to_step_order: number | null;
  from_content_digest: string | null;
  to_content_digest: string | null;
  from_user_intent: string | null;
  to_user_intent: string | null;
}

export interface BlueprintDiffOut {
  journey_key: string;
  diff_state: UxDiffState;
  from_revision_number: number | null;
  to_revision_number: number | null;
  steps: BlueprintDiffStepEntryOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface JourneyStepStakeholderLinkCreateRequest {
  journey_key: string;
  step_key: string;
  stakeholder_key: string;
  role: BlueprintStakeholderRole;
  note?: string;
}

export interface JourneyStepDeliveryLinkCreateRequest {
  journey_key: string;
  step_key: string;
  delivery_kind: JourneyDeliveryKind;
  target_kind: BlueprintDeliveryTargetKind;
  target_ref?: string;
  note?: string;
}

export interface JourneyStepExchangeLinkCreateRequest {
  journey_key: string;
  step_key: string;
  exchange_key: string;
  note?: string;
}


// === Epic #418 / Issue #424 — Functional Lineage View + Gap/Impact Overlay ===
// (Issue #424 owns everything below this marker.)
//
// `GET /functional-lineage` (`docs/stakeholder-value-network.md` §9).
// Read-only, deterministic, no LLM; the Dashboard renders this exactly as
// returned and re-derives nothing (§0 invariant 9). No score, no
// completeness percentage, no ranking field exists here, structurally.

/** §9.1's chain. Static Flow and runtime Flow are never one entity;
 * Capability, Flow, and Node are never folded together. */
export type FunctionalLineageKind =
  | "stakeholder" | "stakeholder_need" | "purpose_element" | "purpose_relation"
  | "capability" | "value_exchange" | "ux_journey" | "ux_journey_step"
  | "ux_requirement" | "solution_design" | "static_flow" | "runtime_flow"
  | "evolution_node" | "component" | "cell_definition" | "cell_binding"
  | "probe_point" | "purpose_outcome_criterion";

/** §9.2's 23 gap codes. Held in parity with the server's `LineageGapCode`
 * by `test_interview_type_parity.py`'s `FINITE_TYPE_NAMES`. */
export type LineageGapCode =
  | "stakeholder_without_role" | "stakeholder_without_need" | "need_without_purpose"
  | "need_without_exchange" | "need_without_journey" | "exchange_without_journey"
  | "exchange_without_outcome" | "journey_step_without_requirement"
  | "requirement_without_acceptance_criterion" | "requirement_without_design"
  | "adopted_design_without_implementation_target" | "flow_without_node"
  | "node_without_flow" | "subject_without_evaluation_policy"
  | "confirmed_without_evidence" | "stale_upstream" | "stale_link" | "stale_evidence"
  | "conflicting_dependency" | "rejected_dependency" | "feedback_path_missing"
  | "unresolved_reference" | "unavailable_reference";

/** §9.2: fixed per gap CODE, never per instance -- a per-instance severity
 * would be the importance score this Epic forbids everywhere. */
export type LineageGapSeverity = "blocking" | "attention" | "informational";

export interface FunctionalLineageNodeOut {
  kind: FunctionalLineageKind;
  ref: string;
  name: string | null;
}

/** Always points from the UPSTREAM entity to the DOWNSTREAM entity it
 * feeds (§9.3: impact traversal is downstream only, through explicit
 * links). */
export interface FunctionalLineageEdgeOut {
  from_kind: FunctionalLineageKind;
  from_ref: string;
  to_kind: FunctionalLineageKind;
  to_ref: string;
}

export interface FunctionalLineageGapOut {
  code: LineageGapCode;
  severity: LineageGapSeverity;
  subject_kind: FunctionalLineageKind;
  subject_ref: string;
}

export interface FunctionalLineageOut {
  system_id: number;
  generated_at: number;
  nodes: FunctionalLineageNodeOut[];
  edges: FunctionalLineageEdgeOut[];
  gaps: FunctionalLineageGapOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

// --- Product Objective / Milestone / Gap (Epic #427, Issues #429-#432) -------
// See docs/product-objective-lineage.md. Mirrors app/models.py's Product*
// Literal aliases and *Out/*Request models field-for-field; kept in sync via
// test_interview_type_parity.py's FINITE_TYPE_NAMES.

export type ProductDesignStatus = "proposed" | "confirmed" | "rejected" | "retired";

export type ProductObjectiveState =
  | "proposed"
  | "confirmed"
  | "active"
  | "achieved"
  | "rejected"
  | "retired";

export type ProductRecheckState = "current" | "stale" | "not_captured";

export type ProductRevisionState = "current" | "superseded";

export type ProductAuthorshipKind = "developer" | "reasoning_model";

export type ProductMilestoneAchievement = "unassessed" | "met" | "not_met" | "indeterminate";

export type ProductMilestoneAssessability = "assessable" | "unavailable" | "not_applicable";

export type ProductMilestoneVerificationMethod =
  | "manual_review"
  | "runtime_observation"
  | "external_report"
  | "unavailable";

export type ProductObjectiveDecisionKind =
  | "confirm"
  | "activate"
  | "achieve"
  | "reject"
  | "retire"
  | "reinstate";

export type ProductMilestoneDecisionKind = "confirm" | "reject" | "retire" | "reinstate";

export type ProductMilestoneAssessmentKind = "met" | "not_met" | "indeterminate" | "withdraw";

export type ProductRefKind =
  | "vision_claim"
  | "purpose_element"
  | "purpose_relation"
  | "capability_entity"
  | "stakeholder_need";

export type ProductRefRelationStatus = "confirmed" | "proposed" | "derived";

export type ProductRefTargetResolution = "resolved" | "unresolved" | "unavailable";

export type ProductRefRecheckState = "current" | "stale" | "not_captured";

export type ProductGapTargetMode = "own" | "inherited_from_milestone" | "unknown";

export type ProductGapSourceKind =
  | "manual"
  | "system_understanding_gap"
  | "understanding_review_gap"
  | "understanding_claim_change"
  | "functional_lineage_gap"
  | "value_network_notice"
  | "journey_baseline_diff"
  | "requirement_diff"
  | "capability_drift"
  | "runtime_alignment_mismatch"
  | "node_anomaly"
  | "joint_understanding_open"
  | "inquiry_unresolved"
  | "issue_draft";

export type ProductGapSourceState =
  | "current"
  | "changed"
  | "contradicted"
  | "disappeared"
  | "unavailable";

export type ProductGapLifecycle =
  | "open"
  | "acknowledged"
  | "deferred"
  | "resolved"
  | "rejected"
  | "obsolete";

export type ProductGapDecisionKind =
  | "acknowledge"
  | "defer"
  | "resolve"
  | "reject"
  | "retire"
  | "reopen"
  | "prioritize";

export type ProductGapPriorityBand = "unset" | "watch" | "next" | "now";

export type ProductGapEvidenceKind =
  | "trace"
  | "experiment"
  | "replay_run"
  | "human_report"
  | "external_report"
  | "repository_path"
  | "other";

export type ProductGapArtifactLinkKind =
  | "issue_draft"
  | "ux_journey"
  | "ux_requirement"
  | "product_feature"
  | "solution_design";

export type ProductFeatureLinkKind =
  | "solution_design"
  | "evolution_node"
  | "component"
  | "probe_point"
  | "static_flow"
  | "runtime_flow"
  | "experiment"
  | "replay_run"
  | "purpose_outcome_criterion";

export type ProductDeepLinkState = "available" | "unavailable";

/** Read-time-only advisory flags (§6) -- NEVER ProductGapLifecycle values,
 * never persisted. */
export type ProductGapReadFlag = "recheck_required" | "reopen_candidate" | "close_candidate";

export type ProductObjectiveNextStepKey =
  | "unavailable"
  | "confirm_vision"
  | "create_objective"
  | "confirm_objective"
  | "activate_objective"
  | "create_milestone"
  | "confirm_milestone"
  | "recheck_stale_decision"
  | "review_gap_source"
  | "create_gap"
  | "prioritize_gap"
  | "link_gap_to_journey"
  | "link_requirement_to_feature"
  | "assess_milestone"
  | "none";

export type ProductObjectiveNextStepState = "available" | "waiting" | "complete" | "unavailable";

export interface ProductObjectiveRevisionOut {
  id: number;
  objective_id: number;
  revision_number: number;
  title: string;
  intent: string;
  contribution: string;
  scope_note: string;
  summary: string;
  content_digest: string;
  authored_by_kind: ProductAuthorshipKind;
  decision_method: "manual" | "reasoning_llm";
  intelligence_run_id: number | null;
  change_note: string;
  created_by: string | null;
  created_at: number;
  revision_state: ProductRevisionState;
  superseded_by_id: number | null;
}

export interface ProductObjectiveParentLinkOut {
  id: number;
  objective_id: number;
  parent_objective_id: number;
  parent_objective_key: string | null;
  rationale: string;
  decision_method: "manual" | "reasoning_llm";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductObjectiveRefOut {
  id: number;
  objective_id: number;
  ref_kind: ProductRefKind;
  target_ref: string;
  target_row_id: number | null;
  target_name: string | null;
  relation_status: ProductRefRelationStatus;
  target_state: string;
  target_resolution: ProductRefTargetResolution;
  recheck_state: ProductRefRecheckState;
  captured_digest: string;
  captured_session_id: number | null;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductObjectiveDecisionOut {
  id: number;
  objective_id: number;
  objective_key: string;
  decision: ProductObjectiveDecisionKind;
  rationale: string;
  captured_digest: string;
  captured_revision_id: number | null;
  decision_method: "manual";
  decided_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
}

export interface ProductObjectiveOut {
  id: number;
  system_id: number;
  objective_key: string;
  current_revision_id: number | null;
  current_revision_number: number | null;
  title: string;
  objective_state: ProductObjectiveState;
  recheck_state: ProductRecheckState;
  parent_objective_id: number | null;
  parent_objective_key: string | null;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

export interface ProductObjectiveDetailOut extends ProductObjectiveOut {
  current_revision: ProductObjectiveRevisionOut | null;
  upstream_refs: ProductObjectiveRefOut[];
  decisions: ProductObjectiveDecisionOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface ProductObjectiveListOut {
  system_id: number;
  generated_at: number;
  objectives: ProductObjectiveOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

// --- #429 Objective write requests -------------------------------------------

export interface ProductObjectiveCreateRequest {
  objective_key: string;
}

export interface ProductObjectiveRevisionCreateRequest {
  title?: string;
  intent?: string;
  contribution?: string;
  scope_note?: string;
  summary?: string;
  change_note?: string;
}

export interface ProductObjectiveParentSetRequest {
  parent_objective_key: string;
  rationale?: string;
}

export interface ProductObjectiveRefCreateRequest {
  ref_kind: ProductRefKind;
  target_ref: string;
  note?: string;
}

export interface ProductObjectiveDecisionCreateRequest {
  decision: ProductObjectiveDecisionKind;
  rationale?: string;
  captured_digest?: string;
}

export interface ProductMilestoneRevisionOut {
  id: number;
  milestone_id: number;
  revision_number: number;
  title: string;
  target_state: string;
  verification_method: ProductMilestoneVerificationMethod;
  verification_note: string;
  sequence_hint: number;
  summary: string;
  content_digest: string;
  authored_by_kind: ProductAuthorshipKind;
  decision_method: "manual" | "reasoning_llm";
  intelligence_run_id: number | null;
  change_note: string;
  created_by: string | null;
  created_at: number;
  revision_state: ProductRevisionState;
  superseded_by_id: number | null;
}

export interface ProductMilestoneDependencyOut {
  id: number;
  milestone_id: number;
  depends_on_milestone_id: number;
  depends_on_milestone_key: string | null;
  rationale: string;
  decision_method: "manual" | "reasoning_llm";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductMilestoneDecisionOut {
  id: number;
  milestone_id: number;
  milestone_key: string;
  decision: ProductMilestoneDecisionKind;
  rationale: string;
  captured_digest: string;
  captured_revision_id: number | null;
  decision_method: "manual";
  decided_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
}

export interface ProductMilestoneAssessmentOut {
  id: number;
  milestone_id: number;
  milestone_key: string;
  assessment: ProductMilestoneAssessmentKind;
  rationale: string;
  evidence_note: string;
  captured_digest: string;
  captured_revision_id: number | null;
  decision_method: "manual";
  assessed_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
}

export interface ProductMilestoneOut {
  id: number;
  system_id: number;
  milestone_key: string;
  objective_id: number;
  objective_key: string | null;
  current_revision_id: number | null;
  current_revision_number: number | null;
  title: string;
  design_status: ProductDesignStatus;
  achievement: ProductMilestoneAchievement;
  assessability: ProductMilestoneAssessability;
  recheck_state: ProductRecheckState;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

export interface ProductMilestoneDetailOut extends ProductMilestoneOut {
  current_revision: ProductMilestoneRevisionOut | null;
  dependencies: ProductMilestoneDependencyOut[];
  decisions: ProductMilestoneDecisionOut[];
  assessments: ProductMilestoneAssessmentOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface ProductMilestoneListOut {
  system_id: number;
  objective_id: number | null;
  objective_key: string | null;
  generated_at: number;
  milestones: ProductMilestoneOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

// --- #429 Milestone write requests -------------------------------------------

export interface ProductMilestoneCreateRequest {
  objective_key: string;
  milestone_key: string;
}

export interface ProductMilestoneRevisionCreateRequest {
  title?: string;
  target_state?: string;
  verification_method?: ProductMilestoneVerificationMethod;
  verification_note?: string;
  sequence_hint?: number;
  summary?: string;
  change_note?: string;
}

export interface ProductMilestoneDependencyCreateRequest {
  depends_on_milestone_key: string;
  rationale?: string;
}

export interface ProductMilestoneDecisionCreateRequest {
  decision: ProductMilestoneDecisionKind;
  rationale?: string;
  captured_digest?: string;
}

export interface ProductMilestoneAssessmentCreateRequest {
  assessment: ProductMilestoneAssessmentKind;
  rationale?: string;
  evidence_note?: string;
  captured_digest?: string;
}

export interface ProductGapRevisionOut {
  id: number;
  gap_id: number;
  revision_number: number;
  title: string;
  current_state: string;
  target_state: string;
  target_state_mode: ProductGapTargetMode;
  interpretation: string;
  suggested_priority_note: string;
  content_digest: string;
  authored_by_kind: ProductAuthorshipKind;
  decision_method: "manual" | "reasoning_llm";
  intelligence_run_id: number | null;
  change_note: string;
  created_by: string | null;
  created_at: number;
  revision_state: ProductRevisionState;
  superseded_by_id: number | null;
}

export interface ProductGapSourceOut {
  id: number;
  gap_id: number;
  source_kind: ProductGapSourceKind;
  source_ref: string;
  source_state: ProductGapSourceState;
  title: string | null;
  detail: string | null;
  severity: string | null;
  severity_vocabulary: string | null;
  deep_link: string | null;
  deep_link_state: ProductDeepLinkState;
  captured_digest: string;
  captured_snapshot_id: number | null;
  captured_run_id: number | null;
  captured_revision_id: number | null;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductGapEvidenceOut {
  id: number;
  gap_id: number;
  evidence_kind: ProductGapEvidenceKind;
  evidence_ref: string;
  captured_snapshot_id: number | null;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductGapArtifactOut {
  id: number;
  gap_id: number;
  link_kind: ProductGapArtifactLinkKind;
  target_ref: string;
  target_row_id: number | null;
  captured_digest: string;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductGapDecisionOut {
  id: number;
  gap_id: number;
  gap_key: string;
  decision: ProductGapDecisionKind;
  priority_band: ProductGapPriorityBand;
  rationale: string;
  captured_digest: string;
  captured_revision_id: number | null;
  decision_method: "manual";
  decided_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
}

export interface ProductGapOut {
  id: number;
  system_id: number;
  gap_key: string;
  milestone_id: number;
  milestone_key: string | null;
  objective_id: number | null;
  objective_key: string | null;
  current_revision_id: number | null;
  current_revision_number: number | null;
  title: string;
  lifecycle: ProductGapLifecycle;
  priority_band: ProductGapPriorityBand;
  recheck_state: ProductRecheckState;
  read_flags: ProductGapReadFlag[];
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

export interface ProductGapDetailOut extends ProductGapOut {
  current_revision: ProductGapRevisionOut | null;
  source_refs: ProductGapSourceOut[];
  evidence_refs: ProductGapEvidenceOut[];
  artifact_links: ProductGapArtifactOut[];
  decisions: ProductGapDecisionOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface ProductGapListOut {
  system_id: number;
  milestone_id: number | null;
  milestone_key: string | null;
  generated_at: number;
  gaps: ProductGapOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

// --- #429/#430 Gap write requests ---------------------------------------------

export interface ProductGapCreateRequest {
  milestone_key: string;
  gap_key: string;
}

export interface ProductGapRevisionCreateRequest {
  title?: string;
  current_state?: string;
  target_state?: string;
  target_state_mode?: ProductGapTargetMode;
  interpretation?: string;
  suggested_priority_note?: string;
  change_note?: string;
}

export interface ProductGapSourceRefCreateRequest {
  source_kind: ProductGapSourceKind;
  source_ref?: string;
  note?: string;
}

export interface ProductGapEvidenceRefCreateRequest {
  evidence_kind: ProductGapEvidenceKind;
  evidence_ref: string;
  note?: string;
}

export interface ProductGapArtifactLinkCreateRequest {
  link_kind: ProductGapArtifactLinkKind;
  target_ref: string;
  note?: string;
}

export interface ProductGapDecisionCreateRequest {
  decision: ProductGapDecisionKind;
  priority_band?: ProductGapPriorityBand;
  rationale?: string;
  captured_digest?: string;
}

export interface ProductFeatureRevisionOut {
  id: number;
  feature_id: number;
  revision_number: number;
  title: string;
  statement: string;
  rationale: string;
  scope_note: string;
  summary: string;
  content_digest: string;
  authored_by_kind: ProductAuthorshipKind;
  decision_method: "manual" | "reasoning_llm";
  intelligence_run_id: number | null;
  change_note: string;
  created_by: string | null;
  created_at: number;
  revision_state: ProductRevisionState;
  superseded_by_id: number | null;
}

export interface ProductFeatureRequirementLinkOut {
  id: number;
  feature_id: number;
  requirement_id: number;
  requirement_key: string | null;
  captured_requirement_revision_id: number | null;
  captured_digest: string;
  recheck_state: ProductRecheckState;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductFeatureCapabilityLinkOut {
  id: number;
  feature_id: number;
  capability_entity_id: number;
  capability_name: string | null;
  target_state: string | null;
  target_resolution: ProductRefTargetResolution;
  recheck_state: ProductRecheckState;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductFeatureTargetLinkOut {
  id: number;
  feature_id: number;
  link_kind: ProductFeatureLinkKind;
  target_ref: string;
  target_row_id: number | null;
  target_state: string | null;
  target_resolution: ProductRefTargetResolution;
  recheck_state: ProductRecheckState;
  captured_digest: string;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductFeatureDraftLinkOut {
  id: number;
  feature_id: number;
  /** null when the pinned draft is gone -- never 0 and never a different
   * draft's id. Consult `target_resolution`, and use `feature_draft_ref`
   * for the identity that survives a snapshot rebuild. */
  feature_draft_id: number | null;
  feature_draft_ref: string;
  captured_snapshot_id: number | null;
  captured_digest: string;
  target_resolution: ProductRefTargetResolution;
  note: string;
  decision_method: "manual" | "reasoning_llm" | "deterministic";
  created_by: string | null;
  created_at: number;
  superseded_by_id: number | null;
}

export interface ProductFeatureDecisionOut {
  id: number;
  feature_id: number;
  feature_key: string;
  decision: ProductMilestoneDecisionKind;
  rationale: string;
  captured_digest: string;
  captured_revision_id: number | null;
  decision_method: "manual";
  decided_by: string | null;
  superseded_by_id: number | null;
  created_at: number;
}

export interface ProductFeatureOut {
  id: number;
  system_id: number;
  feature_key: string;
  current_revision_id: number | null;
  current_revision_number: number | null;
  title: string;
  design_status: ProductDesignStatus;
  recheck_state: ProductRecheckState;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

export interface ProductFeatureDetailOut extends ProductFeatureOut {
  current_revision: ProductFeatureRevisionOut | null;
  requirement_links: ProductFeatureRequirementLinkOut[];
  capability_links: ProductFeatureCapabilityLinkOut[];
  target_links: ProductFeatureTargetLinkOut[];
  draft_links: ProductFeatureDraftLinkOut[];
  decisions: ProductFeatureDecisionOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface ProductFeatureListOut {
  system_id: number;
  generated_at: number;
  features: ProductFeatureOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

// --- #431 Feature write requests ----------------------------------------------

export interface ProductFeatureCreateRequest {
  feature_key: string;
}

export interface ProductFeatureRevisionCreateRequest {
  title?: string;
  statement?: string;
  rationale?: string;
  scope_note?: string;
  summary?: string;
  change_note?: string;
}

export interface ProductFeatureRequirementLinkCreateRequest {
  requirement_key: string;
  note?: string;
}

export interface ProductFeatureCapabilityLinkCreateRequest {
  capability_entity_id: number;
  note?: string;
}

export interface ProductFeatureTargetLinkCreateRequest {
  link_kind: ProductFeatureLinkKind;
  target_ref: string;
  note?: string;
}

export interface ProductFeatureDraftLinkCreateRequest {
  feature_draft_id: number;
  note?: string;
}

export interface ProductFeatureDecisionCreateRequest {
  decision: ProductMilestoneDecisionKind;
  rationale?: string;
  captured_digest?: string;
}

// --- #432 projection: Objective Map / Gap Workbench / Overview section -------
// Best-effort mechanical rendering of §9.1/§9.2's prose (no DDL of its own);
// confirm against app/product_objective_projection.py before relying on
// exact shape.

export interface ObjectiveMapGapSummaryOut {
  open_count: number;
  acknowledged_count: number;
  deferred_count: number;
  resolved_count: number;
  rejected_count: number;
  obsolete_count: number;
  recheck_required_count: number;
  reopen_candidate_count: number;
  close_candidate_count: number;
}

export interface ObjectiveMapMilestoneOut {
  id: number;
  milestone_key: string;
  title: string;
  design_status: ProductDesignStatus;
  achievement: ProductMilestoneAchievement;
  assessability: ProductMilestoneAssessability;
  recheck_state: ProductRecheckState;
  sequence_hint: number;
  gap_summary: ObjectiveMapGapSummaryOut;
}

export interface ObjectiveMapNodeOut {
  id: number;
  objective_key: string;
  title: string;
  objective_state: ProductObjectiveState;
  recheck_state: ProductRecheckState;
  parent_objective_id: number | null;
  parent_objective_key: string | null;
  child_objective_ids: number[];
  milestones: ObjectiveMapMilestoneOut[];
}

export interface ObjectiveMapOut {
  system_id: number;
  generated_at: number;
  nodes: ObjectiveMapNodeOut[];
  root_objective_ids: number[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface GapWorkbenchSourceBucketOut {
  source_kind: ProductGapSourceKind;
  gap_count: number;
}

export interface GapWorkbenchSharedSourceOut {
  source_kind: ProductGapSourceKind;
  source_ref: string;
  gap_ids: number[];
  gap_keys: string[];
}

export interface GapWorkbenchEntryOut {
  id: number;
  gap_key: string;
  milestone_id: number;
  milestone_key: string | null;
  objective_id: number | null;
  objective_key: string | null;
  title: string;
  lifecycle: ProductGapLifecycle;
  priority_band: ProductGapPriorityBand;
  recheck_state: ProductRecheckState;
  read_flags: ProductGapReadFlag[];
  deep_links: OverviewTargetOut[];
}

export interface GapWorkbenchOut {
  system_id: number;
  generated_at: number;
  entries: GapWorkbenchEntryOut[];
  source_kind_breakdown: GapWorkbenchSourceBucketOut[];
  shared_sources: GapWorkbenchSharedSourceOut[];
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}

export interface OverviewObjectiveOut {
  vision: UnderstandingBriefClaimOut | null;
  active_objective: ProductObjectiveOut | null;
  active_objective_count: number;
  next_milestone: ProductMilestoneOut | null;
  primary_gap: ProductGapOut | null;
  /** `null` for a System with no Product Objective yet (§11's graceful
   * empty-state rule) -- read as "not started". Deliberately not a member
   * of ProductObjectiveState itself, which describes one Objective's own
   * lifecycle, never "no Objective exists". */
  objective_state: ProductObjectiveState | null;
  next_step: ProductObjectiveNextStepKey;
  next_step_state: ProductObjectiveNextStepState;
  next_step_reason: string;
  next_step_completion: string;
  next_step_value: string;
  degraded_sections: string[];
  degraded_detail: Record<string, string>;
}
