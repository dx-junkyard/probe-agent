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

export interface TraceEvent {
  trace_id: string;
  component_id: string;
  mode: string;
  input: string | null;
  output: string | null;
  error: string | null;
  duration_ms: number | null;
  timestamp: number;
}

export interface Policy {
  mode: "off" | "trace" | "shadow";
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
export type InterviewApprovalState = "proposed" | "approved" | "rejected" | "edited";
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

export interface OpenQuestion {
  question: string;
  category: string;
  priority: string;
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
  title: string;
  focus: string;
  status: InterviewSessionStatus;
  stage: InterviewStage;
  current_understanding: CurrentUnderstanding | null;
  gap_analysis: GapItem[] | null;
  open_questions: OpenQuestion[] | null;
  user_intent: string | null;
  last_error: string | null;
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
  next_questions: string[];
  intelligence_run: IntelligenceRunOut | null;
  error: string | null;
  stage: InterviewStage | null;
  current_understanding: CurrentUnderstanding | null;
  gap_analysis: GapItem[] | null;
  open_questions_structured: OpenQuestion[] | null;
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
  diff: string;
  files_changed: number;
  items_materialized: number;
  skipped: string[];
  materialized_at: number;
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

export interface ProbePlanOut {
  id: number;
  system_id: number;
  snapshot_id: number;
  intelligence_run_id: number;
  feature_id: string;
  objective: string;
  status: string;
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

export interface SystemProfile {
  name: string;
  purpose: string;
  target_users: string;
  stakeholder_value: string;
  constraints: string;
  success_criteria: string;
  created_at: string;
  updated_at: string;
}

// --- Decision Workspace (Issues #35-#37) ------------------------------------

export type WorkspaceContextItemType = "feature" | "component" | "trace" | "experiment" | "probe_plan";
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
