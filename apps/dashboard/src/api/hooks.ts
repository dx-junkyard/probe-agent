import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueries, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, getSystemId } from "./client";
import type {
  SystemOut, ComponentSummary, TraceEvent, Policy,
  LineageOut, TraceAnalyzer, AnalysisRun, AnalyzerContext,
  RepositoryStatus,
  RepositoryResyncJob,
  SystemStateAssessment,
  FlowOverlayOut, FlowOverlayRequest,
  ShadowResult, ComponentProfile, UserOut, TokenOut,
  RepositoryCandidateOut, RepositoryConfigOut, SnapshotOut, SnapshotPreflightOut,
  ReplayReadinessOut, TraceSummaryOut, TracePageOut, LatestDraftsOut,
  DraftGenerationResultOut,
  SymbolIndexOut, FeatureCodeLinksOut, ProbePlansListOut, ApiScanResultOut,
  FlowEntrypointsOut, FlowGraphOut, FlowProbeSelection, ProbePlanOut,
  ApiRoleCardsOut, ExplanationRefreshOut, RefreshProposalRequest,
  CapabilityHierarchyOut, CapabilityHierarchyDriftOut,
  ProbePatchOut, GenerationRun, ExperimentOut, MeResponse,
  EvaluationCriterion,
  SystemProfile,
  WorkspaceOut, WorkspaceDetailOut, WorkspaceContextItemOut,
  WorkspaceContextPack, WorkspaceAgentTurnOut, WorkspaceProposalOut,
  WorkspaceProposalDraftOut,
  EvolutionNodesListOut, EvolutionNodeSummary, EvolutionNodeProjectionOut,
  EvolutionNodeEventsOut, EvolutionNodeTransitionOut, EvolutionMaturityState,
  UxJourneyListOut, UxJourneyOut, UxJourneyDetailOut, UxJourneyDiffOut,
  UxJourneyRevisionListOut, UxRequirementRevisionListOut, UxRequirementDiffOut,
  UxJourneyCreateRequest, UxJourneyRevisionCreateRequest, UxJourneyUpstreamRefCreateRequest,
  UxJourneyUpstreamRefOut,
  UxRequirementListOut, UxRequirementOut, UxRequirementDetailOut,
  UxRequirementCreateRequest, UxRequirementRevisionCreateRequest,
  UxRequirementStepLinkCreateRequest, UxRequirementStepLinkOut,
  UxArtifactReferenceCreateRequest, UxArtifactReferenceOut,
  UxDesignDecisionCreateRequest, UxDesignDecisionOut,
  SolutionDesignListOut, SolutionDesignOut, SolutionDesignDetailOut,
  SolutionDesignCreateRequest, SolutionDesignOptionCreateRequest, SolutionDesignOptionOut,
  SolutionDesignRequirementLinkCreateRequest, SolutionDesignRequirementLinkOut,
  SolutionDesignTargetLinkCreateRequest, SolutionDesignTargetLinkOut,
  SolutionDesignOptionDecisionCreateRequest, SolutionDesignDecisionOut,
  SolutionDesignChangeOriginsOut, SolutionDesignHandoffOut,
  ExecutionModeProjectionOut, ExecutionModeAssignmentOut, ExecutionModeAssignRequest,
  ExecutionModeRevokeRequest, ExecutionModeDivergenceListOut, ExecutionModeScopeKind,
  FlowSubjectListOut, FlowSubjectKind, FlowExplanationOut,
  FlowExperimentListOut, FlowExperimentProposalOut, FlowExperimentDecisionRequest,
  InterviewSessionOut, InterviewSessionDetailOut, InterviewContextPack,
  InterviewCapabilityGraphOut, InterviewConfirmUnderstandingRequest,
  InterviewDialogueTurnOut, InterviewProposalDecisionOut,
  InterviewProposalMetadataBlock, InterviewProposalProbePlan,
  InterviewApprovedSetOut, InterviewMaterializeOut,
  InterviewSnapshotRebaseOut,
  InterviewQaListOut, InterviewQaOut, InterviewQaAnswerOut,
  InterviewQaRouteInvestigateBatchOut,
  InterviewIntentListOut, InterviewIntentItemOut,
  InterviewIntentField, InterviewIntentUserStatus,
  InterviewInquiryListOut, InterviewInquiryDetailOut, InterviewInquiryOut,
  InterviewInquiryOriginKind,
  AlignmentBuildOut, AlignmentListOut, AlignmentReviewQueueOut, AlignmentItemOut,
  AlignmentDecisionAction,
  AlignmentRuleObjectionListOut, AlignmentRuleObjectionOut, AlignmentRuleRecheckOut,
  AlignmentBatchAnswerItemRequest, AlignmentBatchAnswerOut,
  InterviewMetricsOut, InterviewMetricEventCreate, InterviewMetricEventOut,
  RuntimeObservationProposalOut, RuntimeObservationProposalCreate,
  RefreshStatusOut, RefreshJobOut,
  ChangeSetDetailOut, ChangeSetApplyResultOut, ChangeSetOut,
  RuntimeRealityFactsOut, RuntimeRealityCheckRunOut,
  UnderstandingRevisionListOut, UnderstandingDiffOut,
  SystemUnderstandingOut,
  SystemUnderstandingBuildOut,
  PurposeConfirmationOut,
  PurposeConfirmationRequest,
  GapTriageDecision,
  GapTriageUpdateRequest,
  IssueDraft,
  IssueDraftCreateRequest,
  GitHubIssueStatus,
  IssueDraftUpdateRequest,
  SystemDiagnosticsOut,
  CapabilityContextOut,
  AssistantScreenContext, AssistantAskRequest, AssistantAskOut,
  AssistantSettingsMetadataOut,
  AssistantDiscussionTargetIn, AssistantDiscussionThreadDetailOut, AssistantDiscussionThreadsListOut,
  UiHelpEntriesOut, UiHelpEntry,
  ConnectivityStatusOut,
  InstrumentationScanOut, ProbePatternsListOut, ProbePatternOut,
  ProbePatternCreateRequest, ProbePatternReconciliationOut,
  ProbeRemovalPatchOut, ReconcilePointOut,
  GithubAppStatusOut, GithubConnectionOut, GithubConnectionCreateRequest,
  GithubRepositoryStatusOut, GithubInstallationRepositoryOut, GithubInstallationOut,
  PublishJobOut, PublishAuditEventOut,
  ReplaySetOut, ReplaySourceOut, ReplaySourceDiffOut,
  ReplayVariantRunOut, ReplayVariantDraftOut,
  ReplayApprovalStateOut, ReplayApprovalOut,
  ReplayVariantExperimentPayloadOut, ReplayRegressionScaffoldOut,
  CandidateSessionOut, CandidateSessionCreateRequest, CandidateVersionOut,
  CandidatePromotionOut,
  BootstrapStatusOut,
  KnowledgeArea, HandoffOriginKind, HandoffPriority, HandoffStatus,
  QuestionHandoffListOut, QuestionHandoffOut, QuestionHandoffEvidenceRef,
  CellRootDigestOut, CellAsksListOut, CellAskOut, CellAskSyncOut, CellAskDecision,
  PurposeChainOut, PurposeRelationOut, PurposeQuestionOut, PurposeNeedResponseOut,
  PurposeResponseKind,
  PurposeVerificationPromptOut, PurposeVerificationConceptKind,
  PurposeExperienceHypothesisOut, PurposeReuseHypothesisOut, PurposeOutcomeCriterionOut,
  PurposeVerificationStateOut, PurposeOutcomeEvidenceSource, PurposeOutcomeVerdict,
  ValueNetworkOut,
  BlueprintOut, BlueprintDiffOut,
  JourneyStepStakeholderLinkCreateRequest, JourneyStepStakeholderLinkOut,
  JourneyStepDeliveryLinkCreateRequest, JourneyStepDeliveryLinkOut,
  JourneyStepExchangeLinkCreateRequest, JourneyStepExchangeLinkOut,
  FunctionalLineageOut,
  ObjectiveMapOut, GapWorkbenchOut,
  ProductGapDetailOut, ProductGapDecisionCreateRequest, ProductGapDecisionOut,
  ProductGapArtifactLinkCreateRequest, ProductGapArtifactOut,
  ProductGapOut, ProductGapCreateRequest, ProductGapRevisionCreateRequest,
  ProductObjectiveOut, ProductObjectiveDetailOut, ProductObjectiveCreateRequest,
  ProductObjectiveRevisionCreateRequest, ProductObjectiveDecisionCreateRequest,
  ProductObjectiveDecisionOut,
  ProductMilestoneOut, ProductMilestoneDetailOut, ProductMilestoneCreateRequest,
  ProductMilestoneRevisionCreateRequest, ProductMilestoneDecisionCreateRequest,
  ProductMilestoneDecisionOut, ProductMilestoneAssessmentCreateRequest,
  ProductMilestoneAssessmentOut,
  ProductFeatureListOut, ProductFeatureDetailOut, ProductFeatureCreateRequest,
  ProductFeatureRequirementLinkCreateRequest, ProductFeatureRequirementLinkOut,
} from "./types";

export function sysKey(base: string, ...extra: unknown[]) {
  return [base, getSystemId(), ...extra];
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<MeResponse>("/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

// Issue #265: pre-login / zero-System "phase 0" facts. Deliberately not
// gated on `getSystemId()` (unlike almost every other query in this file) --
// the whole point is that it must resolve before any System is selected or
// any session token exists.
export function useBootstrapStatus() {
  return useQuery({
    queryKey: ["bootstrap-status"],
    queryFn: () => api.get<BootstrapStatusOut>("/auth/bootstrap-status"),
    retry: false,
    staleTime: 30_000,
  });
}

export function useSystems() {
  return useQuery({
    queryKey: ["systems"],
    queryFn: () => api.get<SystemOut[]>("/systems"),
  });
}

export function useCreateSystem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; environment?: string; description?: string }) =>
      api.post<SystemOut>("/systems", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["systems"] }),
  });
}

export function useUpdateSystem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: { id: number; name: string; environment?: string; description?: string }) =>
      api.put<SystemOut>(`/systems/${id}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["systems"] }),
  });
}

export function useComponents(refetchInterval?: number) {
  return useQuery({
    queryKey: sysKey("components"),
    queryFn: () => api.get<ComponentSummary[]>("/components"),
    enabled: !!getSystemId(),
    refetchInterval,
  });
}

export function useTraces(componentId: string | null, limit = 50, refetchInterval?: number) {
  return useQuery({
    queryKey: [...sysKey("traces"), componentId, limit],
    queryFn: () => api.get<TraceEvent[]>(`/components/${componentId}/traces?limit=${limit}`),
    enabled: !!componentId && !!getSystemId(),
    refetchInterval,
  });
}

export interface TracePageParams {
  status: string;
  mode: string;
  replay: string;
  window: string;
  sort: string;
  query: string;
  offset: number;
  limit?: number;
}

export function useTracePage(
  componentId: string | null,
  params: TracePageParams,
  refetchInterval?: number,
) {
  const search = new URLSearchParams({
    status: params.status,
    mode: params.mode,
    replay: params.replay,
    window: params.window,
    sort: params.sort,
    query: params.query,
    offset: String(params.offset),
    limit: String(params.limit ?? 50),
  });
  return useQuery({
    queryKey: [...sysKey("tracePage"), componentId, ...Array.from(search.entries())],
    queryFn: () => api.get<TracePageOut>(
      `/components/${encodeURIComponent(componentId!)}/trace-page?${search}`,
    ),
    enabled: !!componentId && !!getSystemId(),
    refetchInterval,
    placeholderData: previous => previous,
  });
}

export function useUpdatePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ componentId, mode }: { componentId: string; mode: string }) =>
      api.put<Policy>(`/components/${componentId}/policy`, { mode }),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("components") }),
  });
}

// Trace lineage (Issue #147). kind selects the query dimension; start/end are
// an optional time window (unix seconds).
export type LineageQuery =
  | { kind: "entity"; entityType: string; entityId: string; start?: number; end?: number }
  | { kind: "correlation"; id: string; start?: number; end?: number }
  | { kind: "flow"; id: string; start?: number; end?: number };

function lineagePath(q: LineageQuery): string {
  let path: string;
  if (q.kind === "entity") {
    path = `/trace-lineage/entities/${encodeURIComponent(q.entityType)}/${encodeURIComponent(q.entityId)}`;
  } else if (q.kind === "correlation") {
    path = `/trace-lineage/correlations/${encodeURIComponent(q.id)}`;
  } else {
    path = `/trace-lineage/flows/${encodeURIComponent(q.id)}`;
  }
  const params = new URLSearchParams();
  if (q.start != null) params.set("start", String(q.start));
  if (q.end != null) params.set("end", String(q.end));
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

function lineageReady(q: LineageQuery | null): boolean {
  if (!q) return false;
  if (q.kind === "entity") return !!q.entityType && !!q.entityId;
  return !!q.id;
}

export function useLineage(query: LineageQuery | null) {
  return useQuery({
    queryKey: [...sysKey("trace-lineage"), query],
    queryFn: () => api.get<LineageOut>(lineagePath(query as LineageQuery)),
    enabled: lineageReady(query) && !!getSystemId(),
  });
}

// Trace analyzers (Issue #148)
export function useAnalyzers() {
  return useQuery({
    queryKey: sysKey("trace-analyzers"),
    queryFn: () => api.get<TraceAnalyzer[]>("/trace-analyzers"),
    enabled: !!getSystemId(),
  });
}

// Trace Analyzer builder candidate values (Issue #157)
export function useAnalyzerContext() {
  return useQuery({
    queryKey: sysKey("trace-analyzer-context"),
    queryFn: () => api.get<AnalyzerContext>("/trace-analyzers/context"),
    enabled: !!getSystemId(),
  });
}

export function useAnalyzerRuns(analyzerId: number | null) {
  return useQuery({
    queryKey: [...sysKey("trace-analyzer-runs"), analyzerId],
    queryFn: () => api.get<AnalysisRun[]>(`/trace-analyzers/${analyzerId}/runs`),
    enabled: !!analyzerId && !!getSystemId(),
  });
}

export function useCreateAnalyzer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; intent?: string; spec: unknown }) =>
      api.post<TraceAnalyzer>("/trace-analyzers", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("trace-analyzers") }),
  });
}

export function useReviewAnalyzer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, review_status }: { id: number; review_status: "approved" | "rejected" }) =>
      api.put<TraceAnalyzer>(`/trace-analyzers/${id}/review`, { review_status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("trace-analyzers") }),
  });
}

export function useRunAnalyzer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<AnalysisRun>(`/trace-analyzers/${id}/runs`),
    onSuccess: (_d, id) =>
      qc.invalidateQueries({ queryKey: [...sysKey("trace-analyzer-runs"), id] }),
  });
}

// Flow Explorer runtime overlay (Issue #151)
export function useFlowOverlay() {
  return useMutation({
    mutationFn: (body: FlowOverlayRequest) =>
      api.post<FlowOverlayOut>("/repository/flow-overlay", body),
  });
}

// LLM-assisted proposal (Issue #149)
export function useProposeAnalyzer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { intent: string; name?: string }) =>
      api.post<TraceAnalyzer>("/trace-analyzers/propose", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("trace-analyzers") }),
  });
}

export function useShadowResults(componentId: string | null, limit = 50) {
  return useQuery({
    queryKey: [...sysKey("shadow"), componentId, limit],
    queryFn: () => api.get<ShadowResult[]>(`/components/${componentId}/shadow-results?limit=${limit}`),
    enabled: !!componentId && !!getSystemId(),
  });
}

export function useUpdateEvaluation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ resultId, evaluation }: { resultId: number; evaluation: string }) =>
      api.put(`/shadow-results/${resultId}/evaluation`, { evaluation }),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("shadow") }),
  });
}

export function useComponentProfile(componentId: string | null) {
  return useQuery({
    queryKey: [...sysKey("profile"), componentId],
    queryFn: () => api.get<ComponentProfile>(`/components/${componentId}/profile`),
    enabled: !!componentId && !!getSystemId(),
  });
}

export function useUpdateComponentProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ComponentProfile) =>
      api.put<ComponentProfile>(`/components/${data.component_id}/profile`, data),
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: [...sysKey("profile"), v.component_id] }),
  });
}

export function useSystemProfile() {
  return useQuery({
    queryKey: sysKey("system-profile"),
    queryFn: () => api.get<SystemProfile>("/system-profile"),
    enabled: !!getSystemId(),
  });
}

// Issue #94/#275: PUT replaces the whole profile, so callers must merge the
// current profile (from useSystemProfile) with the one field they changed.
// Invalidates system-profile, system-understanding (its purpose_views'
// system_profile entry), and system-state (facts derived from the profile),
// so the System Understanding page reflects the change without a reload.
export function useUpdateSystemProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      name: string;
      purpose: string;
      target_users: string[];
      stakeholder_value: string;
      constraints: string[];
      success_criteria: string[];
    }) => api.put<SystemProfile>("/system-profile", data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("system-profile") });
      qc.invalidateQueries({ queryKey: sysKey("system-understanding") });
      qc.invalidateQueries({ queryKey: sysKey("system-state") });
    },
  });
}

export function useCriteria(componentId: string | null) {
  return useQuery({
    queryKey: [...sysKey("criteria"), componentId],
    queryFn: () => api.get<EvaluationCriterion[]>(`/components/${componentId}/criteria`),
    enabled: !!componentId && !!getSystemId(),
  });
}

export function useUsers() {
  return useQuery({ queryKey: ["users"], queryFn: () => api.get<UserOut[]>("/users") });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { username: string; password: string; role?: string }) =>
      api.post<UserOut>("/users", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useMyTokens() {
  return useQuery({ queryKey: ["myTokens"], queryFn: () => api.get<TokenOut[]>("/tokens/me") });
}

export function useIssueToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; system_id: number; expires_in_days?: number }) =>
      api.post<TokenOut>("/tokens/me", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["myTokens"] }),
  });
}

export function useRevokeMyToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tokenId: number) => api.post(`/tokens/me/${tokenId}/revoke`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["myTokens"] }),
  });
}

export function useAllTokens() {
  return useQuery({ queryKey: ["allTokens"], queryFn: () => api.get<TokenOut[]>("/tokens") });
}

export function useRepositoryConfig() {
  return useQuery({
    queryKey: sysKey("repoConfig"),
    queryFn: () => api.get<RepositoryConfigOut | null>("/repository"),
    enabled: !!getSystemId(),
  });
}

export function useRepositoryCandidates() {
  return useQuery({
    queryKey: ["repositoryCandidates"],
    queryFn: () => api.get<RepositoryCandidateOut[]>("/repository-candidates"),
    staleTime: 30_000,
  });
}

export function useUpdateRepositoryConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { repo_path: string; include_patterns?: string[]; exclude_patterns?: string[] }) =>
      api.put<RepositoryConfigOut>("/repository", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("repoConfig") }),
  });
}

export function useSnapshots() {
  return useQuery({
    queryKey: sysKey("snapshots"),
    queryFn: () => api.get<SnapshotOut[]>("/repository/snapshots"),
    enabled: !!getSystemId(),
  });
}

// Shared Snapshot preflight (Issue #369). One server evaluation, rendered by
// candidate generation / Replay / Experiment alike, so the three surfaces
// cannot disagree about whether a snapshot may be used. `snapshotId` omitted
// evaluates the recommended (latest ready) snapshot -- the same one a run
// resolves by default.
export function useSnapshotPreflight(snapshotId?: number | null) {
  const query = snapshotId != null ? `?snapshot_id=${snapshotId}` : "";
  return useQuery({
    queryKey: sysKey("snapshotPreflight", snapshotId ?? "recommended"),
    queryFn: () => api.get<SnapshotPreflightOut>(`/snapshot-preflight${query}`),
    enabled: !!getSystemId(),
  });
}

// Component monitoring summary (Issue #373).
export function useTraceSummary(componentId: string | null, refetchInterval?: number) {
  return useQuery({
    queryKey: sysKey("traceSummary", componentId ?? ""),
    queryFn: () =>
      api.get<TraceSummaryOut>(`/components/${encodeURIComponent(componentId!)}/trace-summary`),
    enabled: !!getSystemId() && !!componentId,
    refetchInterval,
  });
}

// Replay readiness preflight (Issue #372). Evaluated before a candidate is
// generated so an all-`not captured` component cannot burn an LLM call.
export function useReplayReadiness(
  componentId: string | null,
  traceIds?: string[],
  snapshotId?: number | null,
) {
  const params = new URLSearchParams();
  if (componentId) params.set("component_id", componentId);
  (traceIds ?? []).forEach(id => params.append("trace_ids", id));
  if (snapshotId != null) params.set("snapshot_id", String(snapshotId));
  return useQuery({
    queryKey: sysKey("replayReadiness", componentId ?? "", (traceIds ?? []).join(","), snapshotId ?? "recommended"),
    queryFn: () => api.get<ReplayReadinessOut>(`/replay-readiness?${params.toString()}`),
    enabled: !!getSystemId() && !!componentId,
  });
}

// Repository refresh-hub status (Issue #158)
export function useRepositoryStatus() {
  return useQuery({
    queryKey: sysKey("repositoryStatus"),
    queryFn: () => api.get<RepositoryStatus>("/repository/status"),
    enabled: !!getSystemId(),
  });
}

export function useStartRepositoryResync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<RepositoryResyncJob>("/repository/resync"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("repositoryResync") });
    },
  });
}

export function useLatestRepositoryResync() {
  return useQuery({
    queryKey: sysKey("repositoryResync"),
    queryFn: () => api.get<RepositoryResyncJob | null>("/repository/resync/latest"),
    enabled: !!getSystemId(),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "snapshotting" || status === "indexing"
        ? 1000
        : false;
    },
  });
}

// Canonical user-facing state and notification projection (Issue #206).
export function useSystemState() {
  return useQuery({
    queryKey: sysKey("system-state"),
    queryFn: () => api.get<SystemStateAssessment>("/system-state"),
    enabled: !!getSystemId(),
    staleTime: 30_000,
  });
}

export function useLatestSnapshot() {
  return useQuery({
    queryKey: sysKey("latestSnapshot"),
    queryFn: () => api.get<SnapshotOut | null>("/repository/snapshots/latest"),
    enabled: !!getSystemId(),
  });
}

export function useCreateSnapshot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SnapshotOut>("/repository/snapshots"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("snapshots") });
      qc.invalidateQueries({ queryKey: sysKey("latestSnapshot") });
      qc.invalidateQueries({ queryKey: sysKey("repositoryStatus") });
      qc.invalidateQueries({ queryKey: sysKey("system-state") });
    },
  });
}

export function useLatestDrafts() {
  return useQuery({
    queryKey: sysKey("drafts"),
    queryFn: () => api.get<LatestDraftsOut>("/repository/drafts/latest"),
    enabled: !!getSystemId(),
  });
}

export function useGenerateDrafts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<DraftGenerationResultOut>("/repository/drafts/generate"),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("drafts") }),
  });
}

export function useSymbols() {
  return useQuery({
    queryKey: sysKey("symbols"),
    queryFn: () => api.get<SymbolIndexOut>("/repository/symbols"),
    enabled: !!getSystemId(),
  });
}

export function useIndexSymbols() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SymbolIndexOut>("/repository/symbols/index"),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("symbols") }),
  });
}

export function useApiScanResult() {
  return useQuery({
    queryKey: sysKey("apiScan"),
    queryFn: () => api.get<ApiScanResultOut>("/repository/api-scan"),
    enabled: !!getSystemId(),
  });
}

export function useRunApiScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ApiScanResultOut>("/repository/api-scan"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("apiScan") });
      qc.invalidateQueries({ queryKey: sysKey("flowEntrypoints") });
    },
  });
}

export function useCodeLinks() {
  return useQuery({
    queryKey: sysKey("codeLinks"),
    queryFn: () => api.get<FeatureCodeLinksOut>("/repository/code-links"),
    enabled: !!getSystemId(),
  });
}

export function useGenerateCodeLinks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/repository/code-links/generate"),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("codeLinks") }),
  });
}

export function useReviewCodeLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ linkId, review_status }: { linkId: number; review_status: string }) =>
      api.put(`/repository/code-links/${linkId}/review`, { review_status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("codeLinks") }),
  });
}

export function useProbePlans() {
  return useQuery({
    queryKey: sysKey("probePlans"),
    queryFn: () => api.get<ProbePlansListOut>("/repository/probe-plans"),
    enabled: !!getSystemId(),
  });
}

export function useGenerateProbePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ featureId, objective }: { featureId: string; objective?: string }) => {
      const query = new URLSearchParams({ feature_id: featureId });
      if (objective?.trim()) query.set("objective", objective.trim());
      return api.post(`/repository/probe-plans/generate?${query.toString()}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("probePlans") }),
  });
}

export function useUpdateProbePointStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pointId, status }: { pointId: number; status: string }) =>
      api.put(`/repository/probe-points/${pointId}/status`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("probePlans") }),
  });
}

export function useFlowEntrypoints(
  params?: { category?: string; q?: string; includeFunctions?: boolean },
) {
  const category = params?.category && params.category !== "all" ? params.category : "";
  const q = params?.q?.trim() ?? "";
  const includeFunctions = !!params?.includeFunctions;
  return useQuery({
    queryKey: sysKey("flowEntrypoints", category, q, includeFunctions),
    queryFn: () => {
      const search = new URLSearchParams();
      if (category) search.set("category", category);
      if (q) search.set("q", q);
      if (includeFunctions) search.set("include_functions", "true");
      const qs = search.toString();
      return api.get<FlowEntrypointsOut>(
        `/repository/flow-entrypoints${qs ? `?${qs}` : ""}`,
      );
    },
    enabled: !!getSystemId(),
  });
}

export function useApiRoleCards() {
  return useQuery({
    queryKey: sysKey("apiRoleCards"),
    queryFn: () => api.get<ApiRoleCardsOut>("/repository/api-role-cards"),
    enabled: !!getSystemId(),
  });
}

// Capability Map (Issue #62) — navigate from system purpose to APIs/probe flows.
export function useCapabilityHierarchy() {
  return useQuery({
    queryKey: sysKey("capabilityHierarchy"),
    queryFn: () => api.get<CapabilityHierarchyOut>("/repository/capability-hierarchy"),
    enabled: !!getSystemId(),
  });
}

export function useCapabilityHierarchyDrift() {
  return useQuery({
    queryKey: sysKey("capabilityHierarchyDrift"),
    queryFn: () =>
      api.get<CapabilityHierarchyDriftOut>("/repository/capability-hierarchy/drift"),
    enabled: !!getSystemId(),
    // The endpoint 400s until a hierarchy exists; surface that as "no drift".
    retry: false,
  });
}

export function useGenerateCapabilityHierarchy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (useReasoning?: boolean) =>
      api.post<CapabilityHierarchyOut>(
        `/repository/capability-hierarchy/generate${useReasoning ? "?use_reasoning=true" : ""}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("capabilityHierarchy") });
      qc.invalidateQueries({ queryKey: sysKey("capabilityHierarchyDrift") });
      qc.invalidateQueries({ queryKey: sysKey("apiRoleCards") });
    },
  });
}

export function useRequestExplanationRefresh() {
  return useMutation({
    mutationFn: (body: RefreshProposalRequest) =>
      api.post<ExplanationRefreshOut>("/repository/explanation-refresh", body),
  });
}

export function useBuildFlowGraph() {
  return useMutation({
    mutationFn: (body: {
      entrypoint_type: string;
      entrypoint_id: string;
      max_depth?: number;
      max_nodes?: number;
      snapshot_id?: number;
      commit_sha?: string;
    }) => api.post<FlowGraphOut>("/repository/flow-graphs", body),
  });
}

export function useCreatePlanFromFlow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      entrypoint_type: string;
      entrypoint_id: string;
      objective?: string;
      selections: FlowProbeSelection[];
      snapshot_id?: number;
      commit_sha?: string;
    }) => api.post<ProbePlanOut>("/repository/probe-plans/from-flow", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("probePlans") }),
  });
}

export function useProbePatches() {
  return useQuery({
    queryKey: sysKey("probePatches"),
    queryFn: () => api.get<ProbePatchOut[]>("/repository/probe-patches"),
    enabled: !!getSystemId(),
  });
}

export function useGeneratePatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (planId: number) => api.post<ProbePatchOut>(`/repository/probe-plans/${planId}/patch`),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("probePatches") }),
  });
}

export function useValidatePatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patchId: number) => api.post<ProbePatchOut>(`/repository/probe-patches/${patchId}/validate`),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("probePatches") }),
  });
}

export function useApplyProbePatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ patchId, expectedCommitSha }: { patchId: number; expectedCommitSha: string }) =>
      api.post<ProbePatchOut>(`/repository/probe-patches/${patchId}/apply`, {
        confirmed: true,
        expected_commit_sha: expectedCommitSha,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("probePatches") }),
  });
}

// Issue #349: the interview screen's state, its single primary action and
// its exceptions all come from ONE server query. Any mutation that changes a
// fact the 13-row rule table reads must therefore re-read it -- otherwise the
// screen keeps rendering the previous state and, because the spec removed the
// manual 「差分を生成」 CTA, the flow can wedge until a reload. Call this from
// every such mutation; a missing call is a stuck screen, not a stale badge.
function _invalidateWorkflow(qc: ReturnType<typeof useQueryClient>, sessionId: number | null) {
  qc.invalidateQueries({ queryKey: [...sysKey("interviewWorkflowState"), sessionId] });
  // Issue #351: the Brief is derived from the same persisted facts, so every
  // mutation that can move the workflow state can also change what the Brief
  // says. Refreshing them together keeps 「現在の理解」 from lagging one
  // action behind the state shown right above it.
  qc.invalidateQueries({ queryKey: [...sysKey("understandingBrief"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
  qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
}

export function useInterviewSessions() {
  return useQuery({
    queryKey: sysKey("interviewSessions"),
    queryFn: () => api.get<InterviewSessionOut[]>("/interview/sessions"),
    enabled: !!getSystemId(),
  });
}

export function useCreateInterviewSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { snapshot_id: number; title?: string; focus?: string }) =>
      api.post<InterviewSessionOut>("/interview/sessions", data),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
      // The new session's own workflow state: creating it opens the initial
      // build's run record server-side, so the very first evaluation is `W1`.
      _invalidateWorkflow(qc, created.id);
    },
  });
}

export function useInterviewSession(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewSession"), sessionId],
    queryFn: () => api.get<InterviewSessionDetailOut>(`/interview/sessions/${sessionId}`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useInterviewCapabilityGraph(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewCapabilityGraph"), sessionId],
    queryFn: () =>
      api.get<InterviewCapabilityGraphOut>(
        `/interview/sessions/${sessionId}/capability-graph`,
      ),
    enabled: !!sessionId && !!getSystemId(),
    retry: false,
  });
}

export function useInterviewContextPack(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewContextPack"), sessionId],
    queryFn: () => api.get<InterviewContextPack>(`/interview/sessions/${sessionId}/context-pack`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useInterviewDialogueTurn(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      user_message: string;
      budget?: number;
      generate_proposals?: boolean;
      answered_question?: string;
      answered_qa_id?: number;
      actor?: string;
      // Issue #142: mark this turn as an explicit "I don't know" answer so the
      // consumed Q&A row is recorded as 'unconfirmed' and the model forms a
      // hypothesis to re-confirm instead of treating it as an answered fact.
      answer_unknown?: boolean;
    }) =>
      api.post<InterviewDialogueTurnOut>(`/interview/sessions/${sessionId}/dialogue-turn`, data),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewCapabilityGraph"), sessionId] });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
    },
  });
}

// --- Structured Interview Q&A (Issue #129) ----------------------------------

export function useInterviewQaList(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewQa"), sessionId],
    queryFn: () => api.get<InterviewQaListOut>(`/interview/sessions/${sessionId}/qa`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useCreateInterviewQa(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      question_text: string;
      question_category?: string;
      question_source?: string;
      hypothesis?: string;
    }) => api.post<InterviewQaOut>(`/interview/sessions/${sessionId}/qa`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useAnswerInterviewQa(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      qaId, answer_text, actor, answer_unknown, handoff_id,
    }: {
      qaId: number; answer_text: string; actor: string; answer_unknown?: boolean;
      // Issue #291: set only when this answer is the original developer's
      // explicit confirmation of a returned handoff's assignee answer.
      handoff_id?: number;
    }) =>
      api.post<InterviewQaAnswerOut>(
        `/interview/sessions/${sessionId}/qa/${qaId}/answer`,
        { answer_text, actor, answer_unknown: answer_unknown ?? false, handoff_id },
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      // Issue #291: confirming a handoff's answer changes handoff state too.
      qc.invalidateQueries({ queryKey: [...sysKey("questionHandoffs"), sessionId] });
      // Issue #288: the server enqueues an automatic refresh right after
      // this answer commits; refetch its status so the chip updates
      // promptly instead of waiting for the next poll tick.
      _invalidateAfterAnswerBatch(qc, sessionId);
    },
  });
}

// Issue #286 review fix (Finding 1): wires Question Router / Investigation
// Agent into the normal Q&A flow. Never writes answer_text/status, so (unlike
// useAnswerInterviewQa) this does not trigger the #288 auto-refresh -- only
// route_category/knowledge_area/investigation are affected, and only the QA
// list needs to be refetched.
//
// PR #296 review fix (Finding 4): accepts an optional explicit qa_ids subset
// so a single question's 「わからない」 can scope the batch to just itself
// instead of always investigating every eligible open question in the
// session. Omitting qa_ids (undefined, or an empty array) keeps the prior
// whole-session batch behavior used by the 「AIに先に調査させる」 button.
export function useRouteAndInvestigateQa(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    // The unrestricted call keeps posting with no body at all (matching the
    // endpoint's documented `payload=None` default) rather than an explicit
    // `{ qa_ids: undefined }` -- a scoped call is the only case that ever
    // sends a body.
    mutationFn: (qaIds?: number[]) =>
      qaIds && qaIds.length > 0
        ? api.post<InterviewQaRouteInvestigateBatchOut>(
            `/interview/sessions/${sessionId}/qa/route-and-investigate`, { qa_ids: qaIds },
          )
        : api.post<InterviewQaRouteInvestigateBatchOut>(
            `/interview/sessions/${sessionId}/qa/route-and-investigate`,
          ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

// Issue #336: the single 「わからない」 entry point. One call records the
// unknown answer, classifies the question, and -- only for the classifications
// where the code can actually help -- opens a joint understanding session and
// investigates. It replaces the two unrelated calls the page used to make
// (route-and-investigate, then the #142 answer flow as a fallback), and the
// server guarantees the recorded answer survives any later failure.
export function useAnswerQaUnknown(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ qaId, answerText, actor }: {
      qaId: number;
      answerText: string;
      actor: string;
    }) =>
      api.post<import("@/api/types").InterviewQaUnknownOut>(
        `/interview/sessions/${sessionId}/qa/${qaId}/unknown`,
        { answer_text: answerText, actor },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("jointUnderstandingList"), sessionId] });
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

// Issue #295 §4.8 / PR #296 review fix (Finding 4): a single shared
// auto-investigation controller so the 「わからない」 auto-investigate
// wiring is implemented once and reused verbatim by both the normal Q&A list
// (QaItemCard, via QaPanel) and the focused-question card in
// pages/interview.tsx -- never duplicated per call site. Callers are
// expected to create ONE instance per session (in InterviewPage) and pass it
// down, so the two call sites share a single isPending flag: an in-flight
// investigate call from either place blocks the other from firing a second,
// overlapping request for the same underlying batch endpoint.
// `runForQuestion` always scopes the call to a single question
// (qa_ids=[qaId]); `runBulk` is the unrestricted whole-session batch used by
// the existing 「AIに先に調査させる」 button, unchanged.
export interface QaAutoInvestigateController {
  isPending: boolean;
  // The qa_id currently being auto-investigated via runForQuestion, or null.
  // Never set for runBulk (the whole-session batch has no single target).
  investigatingQaId: number | null;
  // Resolves true iff THIS question was investigated (completed/unresolved,
  // no error) by the batch call; false on any other outcome (API failure,
  // human_only routing, cap/skip) so the caller can fall back safely.
  runForQuestion: (qaId: number) => Promise<boolean>;
  runBulk: () => Promise<InterviewQaRouteInvestigateBatchOut>;
}

export function useQaAutoInvestigate(sessionId: number | null): QaAutoInvestigateController {
  const routeAndInvestigate = useRouteAndInvestigateQa(sessionId);
  const [investigatingQaId, setInvestigatingQaId] = useState<number | null>(null);

  const runForQuestion = async (qaId: number): Promise<boolean> => {
    if (routeAndInvestigate.isPending) return false;
    setInvestigatingQaId(qaId);
    try {
      const batch = await routeAndInvestigate.mutateAsync([qaId]);
      const item = batch.results.find(r => r.qa_id === qaId);
      return !!item && !item.error
        && (item.investigation_status === "completed" || item.investigation_status === "unresolved");
    } catch {
      return false;
    } finally {
      setInvestigatingQaId(null);
    }
  };

  const runBulk = () => routeAndInvestigate.mutateAsync(undefined);

  return { isPending: routeAndInvestigate.isPending, investigatingQaId, runForQuestion, runBulk };
}

// --- Answerable knowledge areas / handoff (Issue #291) ------------------------

export function useUpdateAnswerableAreas(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (areas: KnowledgeArea[]) =>
      api.put<InterviewSessionOut>(`/interview/sessions/${sessionId}/answerable-areas`, { areas }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
      // Area filtering changes which questions are askable.
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
    },
  });
}

export function useQuestionHandoffs(sessionId: number | null, status?: HandoffStatus) {
  return useQuery({
    queryKey: [...sysKey("questionHandoffs"), sessionId, status ?? "all"],
    queryFn: () => api.get<QuestionHandoffListOut>(
      `/interview/sessions/${sessionId}/handoffs${status ? `?status=${status}` : ""}`,
    ),
    enabled: !!sessionId && !!getSystemId(),
  });
}

function _invalidateHandoffs(qc: ReturnType<typeof useQueryClient>, sessionId: number | null) {
  qc.invalidateQueries({ queryKey: [...sysKey("questionHandoffs"), sessionId] });
  // A handoff creation/transition can also touch the origin qa/alignment row
  // (handoff_id link, or alignment_item.status='held').
  qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("alignment"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("reviewQueue"), sessionId] });
}

export function useCreateQuestionHandoff(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      origin_kind: HandoffOriginKind;
      origin_id: number;
      assignee: string;
      background: string;
      needed_decision: string;
      evidence?: QuestionHandoffEvidenceRef[];
      due_note?: string;
      priority?: HandoffPriority;
      created_by?: string;
    }) => api.post<QuestionHandoffOut>(`/interview/sessions/${sessionId}/handoffs`, data),
    onSuccess: () => {
      _invalidateHandoffs(qc, sessionId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useAnswerQuestionHandoff(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ handoffId, answer_text, answered_by }: { handoffId: number; answer_text: string; answered_by: string }) =>
      api.post<QuestionHandoffOut>(`/interview/handoffs/${handoffId}/answer`, { answer_text, answered_by }),
    onSuccess: () => {
      _invalidateHandoffs(qc, sessionId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useReturnQuestionHandoff(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ handoffId }: { handoffId: number }) =>
      api.post<QuestionHandoffOut>(`/interview/handoffs/${handoffId}/return`),
    onSuccess: () => {
      _invalidateHandoffs(qc, sessionId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useCancelQuestionHandoff(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ handoffId }: { handoffId: number }) =>
      api.post<QuestionHandoffOut>(`/interview/handoffs/${handoffId}/cancel`),
    onSuccess: () => {
      _invalidateHandoffs(qc, sessionId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useSkipInterviewQa(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ qaId, actor }: { qaId: number; actor: string }) =>
      api.post<InterviewQaOut>(`/interview/sessions/${sessionId}/qa/${qaId}/skip`, { actor }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useResumeInterviewQa(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ qaId, actor }: { qaId: number; actor: string }) =>
      api.post<InterviewQaOut>(`/interview/sessions/${sessionId}/qa/${qaId}/resume`, { actor }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

// --- Intent Brief (Issue #284) ------------------------------------------------

export function useInterviewIntentList(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewIntent"), sessionId],
    queryFn: () => api.get<InterviewIntentListOut>(`/interview/sessions/${sessionId}/intent`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useCreateInterviewIntentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      field: InterviewIntentField;
      value_text: string;
      status?: InterviewIntentUserStatus;
    }) => api.post<InterviewIntentItemOut>(`/interview/sessions/${sessionId}/intent`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewIntent"), sessionId] });
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useConfirmInterviewIntentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId }: { itemId: number }) =>
      api.post<InterviewIntentItemOut>(`/interview/intent/${itemId}/confirm`),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewIntent"), sessionId] });
      // Issue #288: see useAnswerInterviewQa's comment above.
      _invalidateAfterAnswerBatch(qc, sessionId);
    },
  });
}

export function useCorrectInterviewIntentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, value_text }: { itemId: number; value_text: string }) =>
      api.post<InterviewIntentItemOut>(`/interview/intent/${itemId}/correct`, { value_text }),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewIntent"), sessionId] });
      _invalidateAfterAnswerBatch(qc, sessionId);
    },
  });
}

export function useDeclineInterviewIntentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId }: { itemId: number }) =>
      api.post<InterviewIntentItemOut>(`/interview/intent/${itemId}/decline`),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewIntent"), sessionId] });
      _invalidateAfterAnswerBatch(qc, sessionId);
    },
  });
}

export function useProposeInterviewIntentItems(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<InterviewIntentItemOut[]>(`/interview/sessions/${sessionId}/intent/propose`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewIntent"), sessionId] });
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

// --- Inquiry lifecycle (Issue #285) --------------------------------------

export function useInterviewInquiryList(sessionId: number | null, status?: string) {
  return useQuery({
    queryKey: [...sysKey("interviewInquiries"), sessionId, status ?? "all"],
    queryFn: () => api.get<InterviewInquiryListOut>(
      `/interview/sessions/${sessionId}/inquiries${status ? `?status=${status}` : ""}`,
    ),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useInterviewInquiryDetail(inquiryId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewInquiry"), inquiryId],
    queryFn: () => api.get<InterviewInquiryDetailOut>(`/interview/inquiries/${inquiryId}`),
    enabled: !!inquiryId && !!getSystemId(),
  });
}

// Refresh/resume (Issue #285): on page load / any list refetch, re-attach a
// still-active (open or held) Inquiry to its origin card by
// `${origin_kind}:${origin_id}` so a reload never "forgets" an in-progress
// Inquiry — the server already persists everything needed
// (GET /interview/inquiries/{id}); this just re-derives which origin each
// active Inquiry belongs to from the existing list endpoint. 'open' is
// preferred over 'held' for the same origin, then the most recently created
// one, though in practice a single origin has at most one active Inquiry at
// a time.
export function activeInquiryByOrigin(
  items: InterviewInquiryOut[],
): Map<string, InterviewInquiryOut> {
  const map = new Map<string, InterviewInquiryOut>();
  for (const item of items) {
    // Allow-list, not a deny-list: only 'open'/'held' are active. Every
    // terminal status — including Issue #323's system-written 'superseded'
    // — is excluded here, so no caller can offer resume/continue for one
    // (the server 409s those calls anyway).
    if (item.status !== "open" && item.status !== "held") continue;
    const key = `${item.origin_kind}:${item.origin_id}`;
    const current = map.get(key);
    if (!current) {
      map.set(key, item);
      continue;
    }
    const itemBetter = (current.status !== "open" && item.status === "open") || item.id > current.id;
    if (itemBetter) map.set(key, item);
  }
  return map;
}

export function useActiveInquiriesByOrigin(sessionId: number | null) {
  const { data } = useInterviewInquiryList(sessionId);
  return useMemo(() => activeInquiryByOrigin(data?.items ?? []), [data]);
}

// Issue #322 (server #323): 'superseded' is a terminal, system-written
// status — the premise the conversation was answered against no longer
// exists. It is deliberately NOT active and NOT resumable (the status filter
// in activeInquiryByOrigin above only admits 'open'/'held', and the server
// 409s on /message, /resolve and /resume for a superseded Inquiry), so it
// must never be counted as an in-progress Inquiry or offered for resume.
// It stays fully readable as history through this separate selector.
// Sorted by id ascending for a deterministic, refresh-stable order.
export function supersededInquiries(
  items: InterviewInquiryOut[],
): InterviewInquiryOut[] {
  return items
    .filter(item => item.status === "superseded")
    .sort((a, b) => a.id - b.id);
}

export function useSupersededInquiries(sessionId: number | null) {
  const { data } = useInterviewInquiryList(sessionId);
  return useMemo(() => supersededInquiries(data?.items ?? []), [data]);
}

function _invalidateInquiry(qc: ReturnType<typeof useQueryClient>, sessionId: number | null, inquiryId: number) {
  qc.invalidateQueries({ queryKey: [...sysKey("interviewInquiries"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("interviewInquiry"), inquiryId] });
  // Issue #287: an Inquiry with origin_kind='review_item' mutates the
  // origin alignment_item's status server-side (open <-> inquiry). The
  // mutation payload/response here doesn't always carry origin_kind, so
  // invalidate the alignment queries unconditionally -- harmless extra
  // refetch when the origin was qa/intent instead.
  qc.invalidateQueries({ queryKey: [...sysKey("alignment"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("reviewQueue"), sessionId] });
}

export function useCreateInterviewInquiry(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      origin_kind: InterviewInquiryOriginKind;
      origin_id: number;
      question_text: string;
      held_draft?: string;
    }) => api.post<InterviewInquiryDetailOut>(`/interview/sessions/${sessionId}/inquiries`, data),
    onSuccess: result => _invalidateInquiry(qc, sessionId, result.inquiry.id),
  });
}

export function useSendInterviewInquiryMessage(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId, content }: { inquiryId: number; content: string }) =>
      api.post<InterviewInquiryDetailOut>(`/interview/inquiries/${inquiryId}/message`, { content }),
    onSuccess: (_result, { inquiryId }) => _invalidateInquiry(qc, sessionId, inquiryId),
  });
}

export function useResolveInterviewInquiry(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId }: { inquiryId: number }) =>
      api.post<InterviewInquiryOut>(`/interview/inquiries/${inquiryId}/resolve`),
    onSuccess: (_result, { inquiryId }) => {
      _invalidateInquiry(qc, sessionId, inquiryId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useMarkInterviewInquiryUnresolved(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId, status_reason }: { inquiryId: number; status_reason?: string }) =>
      api.post<InterviewInquiryOut>(`/interview/inquiries/${inquiryId}/unresolved`, { status_reason }),
    onSuccess: (_result, { inquiryId }) => _invalidateInquiry(qc, sessionId, inquiryId),
  });
}

export function useHoldInterviewInquiry(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId }: { inquiryId: number }) =>
      api.post<InterviewInquiryOut>(`/interview/inquiries/${inquiryId}/hold`),
    onSuccess: (_result, { inquiryId }) => {
      _invalidateInquiry(qc, sessionId, inquiryId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useResumeInterviewInquiry(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId }: { inquiryId: number }) =>
      api.post<InterviewInquiryOut>(`/interview/inquiries/${inquiryId}/resume`),
    onSuccess: (_result, { inquiryId }) => {
      _invalidateInquiry(qc, sessionId, inquiryId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useCancelInterviewInquiry(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId }: { inquiryId: number }) =>
      api.post<InterviewInquiryOut>(`/interview/inquiries/${inquiryId}/cancel`),
    onSuccess: (_result, { inquiryId }) => {
      _invalidateInquiry(qc, sessionId, inquiryId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useReopenInterviewInquiryDoubt(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId }: { inquiryId: number }) =>
      api.post<InterviewInquiryOut>(`/interview/inquiries/${inquiryId}/reopen-doubt`),
    onSuccess: (_result, { inquiryId }) => _invalidateInquiry(qc, sessionId, inquiryId),
  });
}

// --- Alignment Review / Review Queue (Issue #287) -----------------------------

export function useAlignmentList(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("alignment"), sessionId],
    queryFn: () => api.get<AlignmentListOut>(`/interview/sessions/${sessionId}/alignment`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

// Issue #309: deterministic System-level Interview / Alignment UX metrics.
// The selected System is part of the query key even though the API path uses
// the standard request context to resolve it.
export function useInterviewMetrics() {
  return useQuery({
    queryKey: sysKey("interviewMetrics"),
    queryFn: () => api.get<InterviewMetricsOut>("/interview/metrics"),
    enabled: !!getSystemId(),
  });
}

// Telemetry must never block or alter the review interaction it observes.
// Event keys are caller-generated and idempotent within a System, so React
// StrictMode remounts or repeated renders are harmless.
export async function recordInterviewMetricEventBestEffort(
  data: InterviewMetricEventCreate,
): Promise<void> {
  try {
    await api.post<InterviewMetricEventOut>("/interview/metric-events", data);
  } catch {
    // Intentionally ignored: metric collection is observability, not a gate.
  }
}

export function useReviewQueue(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("reviewQueue"), sessionId],
    queryFn: () => api.get<AlignmentReviewQueueOut>(`/interview/sessions/${sessionId}/review-queue`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useAlignmentRuleObjections() {
  return useQuery({
    queryKey: sysKey("alignmentRuleObjections"),
    queryFn: () => api.get<AlignmentRuleObjectionListOut>("/interview/alignment/rule-objections"),
    enabled: !!getSystemId(),
  });
}

function _invalidateAlignment(qc: ReturnType<typeof useQueryClient>, sessionId: number | null) {
  qc.invalidateQueries({ queryKey: [...sysKey("alignment"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("reviewQueue"), sessionId] });
  qc.invalidateQueries({ queryKey: sysKey("alignmentRuleObjections") });
}

export function useRequestAlignmentRuleRecheck(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rule: AlignmentRuleObjectionOut) =>
      api.post<AlignmentRuleRecheckOut>(
        `/interview/alignment/rules/${rule.reason_code}/recheck`,
        {
          policy_version: rule.policy_version,
          policy_digest: rule.policy_digest,
          policy_rule_id: rule.policy_rule_id,
        },
      ),
    onSuccess: () => {
      _invalidateAlignment(qc, sessionId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useBuildAlignment(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AlignmentBuildOut>(`/interview/sessions/${sessionId}/alignment/build`),
    onSuccess: () => {
      _invalidateAlignment(qc, sessionId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

export function useAnswerAlignmentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, decision, note }: { itemId: number; decision: AlignmentDecisionAction; note?: string }) =>
      api.post<AlignmentItemOut>(`/interview/alignment/${itemId}/answer`, { decision, note }),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      _invalidateAlignment(qc, sessionId);
      // Issue #288: see useAnswerInterviewQa's comment above.
      _invalidateAfterAnswerBatch(qc, sessionId);
    },
  });
}

// PR #296 review fix (Finding 5): answers several alignment_item rows in one
// call instead of the dashboard sequentially calling POST .../answer once
// per staged item -- the server now triggers the #288 refresh exactly once
// for the whole batch. Reuses the exact same invalidation as the single-item
// answer above; a partial failure still leaves `refreshed: true` when at
// least one item saved, so invalidating unconditionally on success here is
// safe (the caller only reads `response.results` to decide what to re-stage).
export function useAnswerAlignmentItemsBatch(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (answers: AlignmentBatchAnswerItemRequest[]) =>
      api.post<AlignmentBatchAnswerOut>(
        `/interview/sessions/${sessionId}/alignment/answers-batch`, { answers },
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      _invalidateAlignment(qc, sessionId);
      // Issue #288: see useAnswerInterviewQa's comment above.
      _invalidateAfterAnswerBatch(qc, sessionId);
    },
  });
}

export function useCorrectAlignmentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, corrected_interpretation }: { itemId: number; corrected_interpretation: string }) =>
      api.post<AlignmentItemOut>(`/interview/alignment/${itemId}/correct`, { corrected_interpretation }),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      _invalidateAlignment(qc, sessionId);
      _invalidateAfterAnswerBatch(qc, sessionId);
    },
  });
}

export function useHoldAlignmentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId }: { itemId: number }) =>
      api.post<AlignmentItemOut>(`/interview/alignment/${itemId}/hold`),
    onSuccess: () => {
      _invalidateAlignment(qc, sessionId);
      _invalidateWorkflow(qc, sessionId);
    },
  });
}

// --- Observation proposal (Issue #290) ----------------------------------------
//
// Approval-gated: creating/approving/rejecting a proposal never starts
// observation itself (see `policy_pointer` on the approved response).

export function useObservationProposals(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("observationProposals"), sessionId],
    queryFn: () =>
      api.get<RuntimeObservationProposalOut[]>(`/interview/sessions/${sessionId}/observation-proposals`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

function _invalidateObservationProposals(qc: ReturnType<typeof useQueryClient>, sessionId: number | null) {
  qc.invalidateQueries({ queryKey: [...sysKey("observationProposals"), sessionId] });
}

export function useCreateObservationProposal(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: RuntimeObservationProposalCreate) =>
      api.post<RuntimeObservationProposalOut>(
        `/interview/sessions/${sessionId}/observation-proposals`, payload,
      ),
    onSuccess: () => _invalidateObservationProposals(qc, sessionId),
  });
}

export function useApproveObservationProposal(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, decision_by }: { proposalId: number; decision_by?: string }) =>
      api.post<RuntimeObservationProposalOut>(
        `/interview/observation-proposals/${proposalId}/approve`, { decision_by },
      ),
    onSuccess: () => _invalidateObservationProposals(qc, sessionId),
  });
}

export function useRejectObservationProposal(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, decision_by }: { proposalId: number; decision_by?: string }) =>
      api.post<RuntimeObservationProposalOut>(
        `/interview/observation-proposals/${proposalId}/reject`, { decision_by },
      ),
    onSuccess: () => _invalidateObservationProposals(qc, sessionId),
  });
}

// --- Automatic refresh after an answer batch (Issue #288) --------------------
//
// Polls while a job is pending/updating so the status chip near 現在の理解 /
// レビューキュー reflects progress without the user reloading; every answer/
// decision mutation also invalidates this query directly (see
// `_invalidateAfterAnswerBatch` below) so the chip updates promptly instead
// of waiting for the next poll tick.

const _handledTerminalRefreshJobs = new WeakMap<
  ReturnType<typeof useQueryClient>,
  Set<string>
>();

function _claimTerminalRefreshJob(
  qc: ReturnType<typeof useQueryClient>,
  terminalKey: string,
): boolean {
  let handled = _handledTerminalRefreshJobs.get(qc);
  if (!handled) {
    handled = new Set();
    _handledTerminalRefreshJobs.set(qc, handled);
  }
  if (handled.has(terminalKey)) return false;
  handled.add(terminalKey);
  // A QueryClient normally lives for the whole app session; keep this
  // observer-only dedupe bounded without affecting server-side job history.
  if (handled.size > 100) {
    const oldest = handled.values().next().value;
    if (oldest) handled.delete(oldest);
  }
  return true;
}

export function useRefreshStatus(sessionId: number | null) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: [...sysKey("refreshStatus"), sessionId],
    queryFn: () => api.get<RefreshStatusOut>(`/interview/sessions/${sessionId}/refresh-status`),
    enabled: !!sessionId && !!getSystemId(),
    refetchInterval: (query) => {
      const status = query.state.data?.latest_job?.status;
      return status === "pending" || status === "updating" ? 2000 : false;
    },
  });

  const terminalJobId = query.data?.latest_job?.id ?? null;
  const terminalJobStatus = query.data?.latest_job?.status ?? null;
  useEffect(() => {
    if (
      terminalJobId == null
      || terminalJobStatus == null
      || terminalJobStatus === "pending"
      || terminalJobStatus === "updating"
    ) return;
    const terminalKey = `${sessionId}:${terminalJobId}:${terminalJobStatus}`;
    if (!_claimTerminalRefreshJob(qc, terminalKey)) return;

    // The mutation's immediate invalidation can race ahead of the
    // background refresh and fetch the old revision/alignment. Re-fetch the
    // complete derived view once the polled job actually reaches a terminal
    // state. This also covers jobs that complete before the first poll.
    _invalidateAfterRefreshCompletion(qc, sessionId);
  }, [terminalJobId, terminalJobStatus, qc, sessionId]);

  return query;
}

function _invalidateAfterRefreshCompletion(
  qc: ReturnType<typeof useQueryClient>,
  sessionId: number | null,
) {
  qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
  qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
  qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("understandingRevisions"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("understandingDiff"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("alignment"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("reviewQueue"), sessionId] });
}

function _invalidateAfterAnswerBatch(qc: ReturnType<typeof useQueryClient>, sessionId: number | null) {
  qc.invalidateQueries({ queryKey: [...sysKey("refreshStatus"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("understandingRevisions"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("understandingDiff"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("alignment"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("reviewQueue"), sessionId] });
}

export function useRetryRefreshJob(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId }: { jobId: number }) =>
      api.post<RefreshJobOut>(`/interview/sessions/${sessionId}/refresh-jobs/${jobId}/retry`),
    onSuccess: () => _invalidateAfterAnswerBatch(qc, sessionId),
  });
}

// --- Natural-language bulk correction -> structured change set (Issue #289) --

export function useCreateChangeSet(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (text: string) =>
      api.post<ChangeSetDetailOut>(`/interview/sessions/${sessionId}/change-sets`, { text }),
    onSuccess: result => {
      qc.setQueryData([...sysKey("changeSet"), result.change_set.id], result);
    },
  });
}

export function useChangeSet(changeSetId: number | null) {
  return useQuery({
    queryKey: [...sysKey("changeSet"), changeSetId],
    queryFn: () => api.get<ChangeSetDetailOut>(`/interview/change-sets/${changeSetId}`),
    enabled: !!changeSetId && !!getSystemId(),
  });
}

export function useApplyChangeSet(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ changeSetId, itemIds }: { changeSetId: number; itemIds: number[] }) =>
      api.post<ChangeSetApplyResultOut>(`/interview/change-sets/${changeSetId}/apply`, { item_ids: itemIds }),
    onSuccess: (result, { changeSetId }) => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("changeSet"), changeSetId] });
      if (result.applied_item_ids.length > 0) {
        // Issue #288: see useAnswerInterviewQa's comment above.
        _invalidateAfterAnswerBatch(qc, sessionId);
      }
    },
  });
}

export function useDiscardChangeSet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (changeSetId: number) =>
      api.post<ChangeSetOut>(`/interview/change-sets/${changeSetId}/discard`),
    onSuccess: (_result, changeSetId) => {
      qc.invalidateQueries({ queryKey: [...sysKey("changeSet"), changeSetId] });
    },
  });
}

export function useApproveInterviewProposal(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, actor }: { proposalId: number; actor: string }) =>
      api.post<InterviewProposalDecisionOut>(
        `/interview/sessions/${sessionId}/proposals/${proposalId}/approve`,
        { actor },
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewApprovedSet"), sessionId] });
    },
  });
}

export function useRejectInterviewProposal(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, actor }: { proposalId: number; actor: string }) =>
      api.post<InterviewProposalDecisionOut>(
        `/interview/sessions/${sessionId}/proposals/${proposalId}/reject`,
        { actor },
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewApprovedSet"), sessionId] });
    },
  });
}

export function useEditInterviewProposal(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      proposalId: number;
      actor: string;
      metadata: InterviewProposalMetadataBlock;
      probe_plan: InterviewProposalProbePlan;
    }) =>
      api.post<InterviewProposalDecisionOut>(
        `/interview/sessions/${sessionId}/proposals/${data.proposalId}/edit`,
        { actor: data.actor, metadata: data.metadata, probe_plan: data.probe_plan },
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewApprovedSet"), sessionId] });
    },
  });
}

export function useInterviewApprovedSet(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewApprovedSet"), sessionId],
    queryFn: () => api.get<InterviewApprovedSetOut>(`/interview/sessions/${sessionId}/approved-set`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useAdvanceInterviewStage(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { stage: string; user_intent?: string }) =>
      api.post<InterviewSessionOut>(
        `/interview/sessions/${sessionId}/advance-stage`,
        data,
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({
        queryKey: [...sysKey("interviewCapabilityGraph"), sessionId],
      });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
      qc.invalidateQueries({ queryKey: sysKey("system-diagnostics") });
    },
  });
}

export function useConfirmInterviewUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: InterviewConfirmUnderstandingRequest) =>
      api.post<InterviewSessionOut>(
        `/interview/sessions/${sessionId}/confirm-understanding`,
        data,
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({
        queryKey: [...sysKey("interviewCapabilityGraph"), sessionId],
      });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
      qc.invalidateQueries({ queryKey: sysKey("system-diagnostics") });
    },
  });
}

export function useRebaseInterviewSnapshot(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { target_snapshot_id?: number; actor: string }) =>
      api.post<InterviewSnapshotRebaseOut>(
        `/interview/sessions/${sessionId}/rebase-snapshot`,
        data,
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewApprovedSet"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("interviewContextPack"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("understandingDiff"), sessionId] });
    },
  });
}

export function useUpdateInterviewUnderstanding() {
  const qc = useQueryClient();
  return useMutation({
    // Takes the target session id explicitly so it can run right after
    // session creation, before the URL/search-param session id updates.
    mutationFn: (sessionId: number) =>
      api.post<InterviewSessionOut>(
        `/interview/sessions/${sessionId}/update-understanding`,
        {},
      ),
    onSuccess: (_data, sessionId) => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("understandingRevisions"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("understandingDiff"), sessionId] });
    },
  });
}

// --- Understanding Revisions (Issue #136) ------------------------------------

export function useUnderstandingRevisions(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("understandingRevisions"), sessionId],
    queryFn: () => api.get<UnderstandingRevisionListOut>(
      `/interview/sessions/${sessionId}/understanding-revisions`,
    ),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useUnderstandingDiff(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("understandingDiff"), sessionId],
    queryFn: () => api.get<UnderstandingDiffOut>(
      `/interview/sessions/${sessionId}/understanding-diff`,
    ),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useMaterializeInterview(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<InterviewMaterializeOut>(`/interview/sessions/${sessionId}/materialize`, {}),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
    },
  });
}

// --- Runtime Reality Check (Issue #135) --------------------------------------

export function useRuntimeRealityFacts(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("runtimeRealityFacts"), sessionId],
    queryFn: () => api.get<RuntimeRealityFactsOut>(`/interview/sessions/${sessionId}/runtime-facts`),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useRunRuntimeRealityCheck(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<RuntimeRealityCheckRunOut>(
        `/interview/sessions/${sessionId}/runtime-reality-check`, {},
      ),
    onSuccess: () => {
      _invalidateWorkflow(qc, sessionId);
      qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] });
      qc.invalidateQueries({ queryKey: [...sysKey("runtimeRealityFacts"), sessionId] });
    },
  });
}

export function useGenerationRuns(componentId?: string, limit = 20) {
  const params = new URLSearchParams();
  if (componentId) params.set("component_id", componentId);
  params.set("limit", String(limit));
  return useQuery({
    queryKey: [...sysKey("generationRuns"), componentId, limit],
    queryFn: () => api.get<GenerationRun[]>(`/generation-runs?${params}`),
    enabled: !!getSystemId(),
  });
}

export function useCreateGenerationRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { component_id: string; trace_id: string; objective: string }) =>
      api.post<GenerationRun>("/generation-runs", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("generationRuns") }),
  });
}

export function useExperiments() {
  return useQuery({
    queryKey: sysKey("experiments"),
    queryFn: () => api.get<ExperimentOut[]>("/experiments"),
    enabled: !!getSystemId(),
  });
}

export function useExperiment(id: number | null) {
  return useQuery({
    queryKey: [...sysKey("experiment"), id],
    queryFn: () => api.get<ExperimentOut>(`/experiments/${id}`),
    enabled: !!id && !!getSystemId(),
  });
}

export function useCreateExperiment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { feature_id: string; objective: string; snapshot_id: number; variants: { label: string; patch_text: string; risk_note?: string }[] }) =>
      api.post<ExperimentOut>("/experiments", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("experiments") }),
  });
}

export function useRunExperiment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<ExperimentOut>(`/experiments/${id}/run`),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("experiments") }),
  });
}

export function useExperimentDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: { id: number; decision: string; variant_key?: string; note?: string }) =>
      api.put<ExperimentOut>(`/experiments/${id}/decision`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("experiments") }),
  });
}

export function useWorkspaces() {
  return useQuery({
    queryKey: sysKey("workspaces"),
    queryFn: () => api.get<WorkspaceOut[]>("/workspaces"),
    enabled: !!getSystemId(),
  });
}

export function useWorkspace(id: number | null) {
  return useQuery({
    queryKey: [...sysKey("workspace"), id],
    queryFn: () => api.get<WorkspaceDetailOut>(`/workspaces/${id}`),
    enabled: !!id && !!getSystemId(),
  });
}

export function useCreateWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { title: string; focus?: string; summary?: string }) =>
      api.post<WorkspaceOut>("/workspaces", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("workspaces") }),
  });
}

export function useWorkspaceContextPack(workspaceId: number | null) {
  return useQuery({
    queryKey: [...sysKey("workspaceContextPack"), workspaceId],
    queryFn: () => api.get<WorkspaceContextPack>(`/workspaces/${workspaceId}/context-pack`),
    enabled: !!workspaceId && !!getSystemId(),
  });
}

export function useAddWorkspaceContextItem(workspaceId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { item_type: string; item_id: string; label?: string }) =>
      api.post<WorkspaceContextItemOut>(`/workspaces/${workspaceId}/context`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("workspace"), workspaceId] });
      qc.invalidateQueries({ queryKey: [...sysKey("workspaceContextPack"), workspaceId] });
    },
  });
}

export function useDeleteWorkspaceContextItem(workspaceId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (contextItemId: number) =>
      api.delete(`/workspaces/${workspaceId}/context/${contextItemId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("workspace"), workspaceId] });
      qc.invalidateQueries({ queryKey: [...sysKey("workspaceContextPack"), workspaceId] });
    },
  });
}

export function useCreateWorkspaceAgentTurn(workspaceId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { message: string; context_refs?: { type: string; id: string }[] }) =>
      api.post<WorkspaceAgentTurnOut>(`/workspaces/${workspaceId}/agent-turns`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("workspace"), workspaceId] }),
  });
}

export function useAcceptWorkspaceProposal(workspaceId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, reason }: { proposalId: number; reason?: string }) =>
      api.post<WorkspaceProposalOut>(`/workspaces/${workspaceId}/proposals/${proposalId}/accept`, { reason: reason ?? "" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("workspace"), workspaceId] }),
  });
}

export function useRejectWorkspaceProposal(workspaceId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, reason }: { proposalId: number; reason?: string }) =>
      api.post<WorkspaceProposalOut>(`/workspaces/${workspaceId}/proposals/${proposalId}/reject`, { reason: reason ?? "" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("workspace"), workspaceId] }),
  });
}

export function useDeferWorkspaceProposal(workspaceId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, reason }: { proposalId: number; reason?: string }) =>
      api.post<WorkspaceProposalOut>(
        `/workspaces/${workspaceId}/proposals/${proposalId}/defer`,
        { reason: reason ?? "" },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("workspace"), workspaceId] }),
  });
}

export function useCreateWorkspaceProposalDraft(workspaceId: number) {
  return useMutation({
    mutationFn: (proposalId: number) =>
      api.post<WorkspaceProposalDraftOut>(
        `/workspaces/${workspaceId}/proposals/${proposalId}/draft`,
      ),
  });
}

export function useWorkspaceProposalDraft(draftId: number | null) {
  return useQuery({
    queryKey: [...sysKey("workspaceDraft"), draftId],
    queryFn: () => api.get<WorkspaceProposalDraftOut>(`/workspace-drafts/${draftId}`),
    enabled: !!draftId && !!getSystemId(),
  });
}

export function useSystemUnderstanding() {
  return useQuery({
    queryKey: sysKey("system-understanding"),
    queryFn: () => api.get<SystemUnderstandingOut>("/repository/system-understanding"),
    enabled: !!getSystemId(),
  });
}

// Issue #94/#275: records that a human checked the manual (System Profile)
// and AI/source-derived purpose views against each other. Errors are 409
// (no/stale snapshot) or 422 (either side missing) -- surfaced by the caller
// via toast, not swallowed here. Invalidates system-understanding (so
// purpose_confirmation reflects immediately) and system-state.
export function useConfirmPurposeAlignment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: PurposeConfirmationRequest) =>
      api.post<PurposeConfirmationOut>(
        "/repository/system-understanding/purpose-confirmation",
        data,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("system-understanding") });
      qc.invalidateQueries({ queryKey: sysKey("system-state") });
    },
  });
}

export function useUpdateGapTriage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: GapTriageUpdateRequest) =>
      api.post<GapTriageDecision>(
        "/repository/system-understanding/gap-triage",
        data,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("system-understanding") });
      qc.invalidateQueries({ queryKey: sysKey("system-state") });
    },
  });
}

export function useCapabilityContext(capabilityKey: string | null) {
  return useQuery({
    queryKey: sysKey("capability-context", capabilityKey),
    queryFn: () =>
      api.get<CapabilityContextOut>(
        `/repository/capabilities/${encodeURIComponent(capabilityKey!)}/context`,
      ),
    enabled: !!capabilityKey && !!getSystemId(),
  });
}

export function useBuildSystemUnderstanding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<SystemUnderstandingBuildOut>("/repository/system-understanding/build"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("system-understanding-build") });
    },
  });
}

/** Polls the latest system understanding build job until it settles, so the
 * dashboard can show step-level progress instead of blocking on one long
 * request (Issues #106/#109). The job (including per-step status, errors,
 * LLM chunk task counts, and artifact counts) is persisted server-side, so
 * reopening the browser restores the active/last job. Callers should
 * invalidate `system-understanding` and `system-diagnostics` once `status`
 * settles. */
export function useLatestSystemUnderstandingBuild() {
  return useQuery({
    queryKey: sysKey("system-understanding-build"),
    queryFn: () =>
      api.get<SystemUnderstandingBuildOut | null>("/repository/system-understanding/build/latest"),
    enabled: !!getSystemId(),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "queued" || status === "running") return 2000;
      return false;
    },
  });
}

export function useActiveSystemUnderstandingJobs() {
  return useQuery({
    queryKey: sysKey("system-understanding-jobs-active"),
    queryFn: () =>
      api.get<SystemUnderstandingBuildOut[]>("/repository/system-understanding/jobs/active"),
    enabled: !!getSystemId(),
  });
}

export function useCancelSystemUnderstandingJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) =>
      api.post<SystemUnderstandingBuildOut>(
        `/repository/system-understanding/jobs/${jobId}/cancel`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("system-understanding-build") });
    },
  });
}

/** Retry/resume a settled or stuck job. With `step`, only that step (plus
 * its non-completed dependents) is reset; completed steps never re-run. */
export function useRetrySystemUnderstandingJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, step }: { jobId: number; step?: string }) =>
      step
        ? api.post<SystemUnderstandingBuildOut>(
            `/repository/system-understanding/jobs/${jobId}/steps/${encodeURIComponent(step)}/retry`,
          )
        : api.post<SystemUnderstandingBuildOut>(
            `/repository/system-understanding/jobs/${jobId}/retry`,
          ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("system-understanding-build") });
    },
  });
}

export function useCancelSystemUnderstandingStep() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, step }: { jobId: number; step: string }) =>
      api.post<SystemUnderstandingBuildOut>(
        `/repository/system-understanding/jobs/${jobId}/steps/${encodeURIComponent(step)}/cancel`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("system-understanding-build") });
    },
  });
}

// Issue drafts (Issue #107)

export function useIssueDrafts() {
  return useQuery({
    queryKey: sysKey("issue-drafts"),
    queryFn: () => api.get<IssueDraft[]>("/issue-drafts"),
    enabled: !!getSystemId(),
  });
}

export function useIssueDraft(id: number | null) {
  return useQuery({
    queryKey: sysKey("issue-draft", id),
    queryFn: () => api.get<IssueDraft>(`/issue-drafts/${id}`),
    enabled: !!getSystemId() && id != null,
  });
}

export function useCreateIssueDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: IssueDraftCreateRequest) =>
      api.post<IssueDraft>("/issue-drafts", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("issue-drafts") });
      qc.invalidateQueries({ queryKey: sysKey("system-understanding") });
    },
  });
}

export function useUpdateIssueDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: IssueDraftUpdateRequest }) =>
      api.patch<IssueDraft>(`/issue-drafts/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("issue-drafts") });
      qc.invalidateQueries({ queryKey: sysKey("system-understanding") });
    },
  });
}

// External Issue Loop: draft -> GitHub issue (Issue #158)

export function useGitHubIssueStatus() {
  return useQuery({
    queryKey: sysKey("issue-drafts-github-status"),
    queryFn: () => api.get<GitHubIssueStatus>("/issue-drafts/github-status"),
    enabled: !!getSystemId(),
  });
}

export function useCreateGitHubIssue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api.post<IssueDraft>(`/issue-drafts/${id}/create-github-issue`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sysKey("issue-drafts") });
      qc.invalidateQueries({ queryKey: sysKey("system-understanding") });
    },
  });
}

// Per-screen assistant (Issue #102)

export function useAssistantScreenContext(screenId: string | null, enabled = true) {
  return useQuery({
    queryKey: [...sysKey("assistant-screen-context"), screenId],
    queryFn: () => api.get<AssistantScreenContext>(`/assistant/screen-context/${screenId}`),
    enabled: !!screenId && !!getSystemId() && enabled,
    staleTime: 30_000,
  });
}

export function useAssistantSettingsMetadata() {
  return useQuery({
    queryKey: ["assistant-settings-metadata"],
    queryFn: () => api.get<AssistantSettingsMetadataOut>("/assistant/settings-metadata"),
    staleTime: 5 * 60_000,
  });
}

// UI 機能解説モード (Issue #440, Epic #436). Static, code-managed registry --
// no System scoping (same as settings metadata above), long staleTime since
// the registry only changes with a deploy.

export function useUiHelpEntries(screenId: string | null) {
  return useQuery({
    queryKey: ["ui-help-entries", screenId],
    queryFn: () =>
      api.get<UiHelpEntriesOut>(
        screenId ? `/assistant/ui-help?screen_id=${encodeURIComponent(screenId)}` : "/assistant/ui-help",
      ),
    staleTime: 5 * 60_000,
  });
}

export function useUiHelpEntry(helpId: string | null) {
  return useQuery({
    queryKey: ["ui-help-entry", helpId],
    queryFn: () => api.get<UiHelpEntry>(`/assistant/ui-help/${encodeURIComponent(helpId ?? "")}`),
    enabled: !!helpId,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useAssistantAsk() {
  return useMutation({
    mutationFn: (data: AssistantAskRequest) =>
      api.post<AssistantAskOut>("/assistant/ask", data),
  });
}

// Assistant discussion threads (Issue #438, Epic #436): target-scoped
// conversation persistence for the 4 discussion-enabled screens
// (overview/interview/ux-design-studio/journey-blueprint). `thread_key`
// (screen_id|scope|target_kind|target_ref), not `screen_id` alone, is the
// identity -- switching the selected entity/element switches thread without
// mixing another target's history (§1.2). `retry: false` so a caller can
// detect a failed resolve and fall back to the pre-#438 in-memory
// conversation (the safe migration path for non-discussion screens and for
// any failure of this endpoint).

export function useAssistantDiscussionThread(target: AssistantDiscussionTargetIn | null) {
  return useQuery({
    queryKey: [
      ...sysKey("assistant-discussion-thread"),
      target?.screen_id, target?.scope, target?.target_kind, target?.target_ref,
    ],
    queryFn: () =>
      api.post<AssistantDiscussionThreadDetailOut>("/assistant/discussion-threads", target),
    enabled: !!target && !!getSystemId(),
    staleTime: 0,
    retry: false,
  });
}

export function useAssistantDiscussionThreadDetail(threadId: number | null) {
  return useQuery({
    queryKey: [...sysKey("assistant-discussion-thread-detail"), threadId],
    queryFn: () =>
      api.get<AssistantDiscussionThreadDetailOut>(`/assistant/discussion-threads/${threadId}`),
    enabled: threadId !== null && !!getSystemId(),
    staleTime: 0,
    retry: false,
  });
}

export function useAssistantDiscussionThreads(filters: {
  screenId?: string;
  scope?: string;
  targetKind?: string;
  targetRef?: string;
} = {}) {
  const params = new URLSearchParams();
  if (filters.screenId) params.set("screen_id", filters.screenId);
  if (filters.scope) params.set("scope", filters.scope);
  if (filters.targetKind) params.set("target_kind", filters.targetKind);
  if (filters.targetRef) params.set("target_ref", filters.targetRef);
  const query = params.toString();
  return useQuery({
    queryKey: [...sysKey("assistant-discussion-threads"), query],
    queryFn: () =>
      api.get<AssistantDiscussionThreadsListOut>(
        query ? `/assistant/discussion-threads?${query}` : "/assistant/discussion-threads",
      ),
    enabled: !!getSystemId(),
  });
}

export function useSystemDiagnostics() {
  return useQuery({
    queryKey: sysKey("system-diagnostics"),
    queryFn: () => api.get<SystemDiagnosticsOut>("/system-diagnostics"),
    enabled: !!getSystemId(),
    staleTime: 30_000,
  });
}

export function useConnectivityStatus(refetchInterval?: number) {
  return useQuery({
    queryKey: sysKey("connectivity-status"),
    queryFn: () => api.get<ConnectivityStatusOut>("/connectivity/status"),
    enabled: !!getSystemId(),
    refetchInterval,
    staleTime: 10_000,
  });
}

export function useLogin() {
  return useMutation({
    mutationFn: (data: { username: string; password: string }) =>
      api.post<{ expires_at: number }>("/auth/login", data),
  });
}

export function useLogout() {
  return useMutation({ mutationFn: () => api.post("/auth/logout") });
}

// ── Probe Pattern lifecycle (Issue #168) ────────────────────────────

export function useInstrumentationScan(enabled: boolean) {
  return useQuery({
    queryKey: sysKey("probeInstrumentation"),
    queryFn: () => api.get<InstrumentationScanOut>("/repository/probe-instrumentation"),
    enabled: enabled && !!getSystemId(),
    retry: false,
  });
}

export function useProbePatterns() {
  return useQuery({
    queryKey: sysKey("probePatterns"),
    queryFn: () => api.get<ProbePatternsListOut>("/repository/probe-patterns"),
    enabled: !!getSystemId(),
  });
}

export function useProbePattern(patternId: number | null) {
  return useQuery({
    queryKey: sysKey("probePattern", patternId),
    queryFn: () => api.get<ProbePatternOut>(`/repository/probe-patterns/${patternId}`),
    enabled: patternId !== null && !!getSystemId(),
  });
}

function invalidatePatterns(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: sysKey("probePatterns") });
  qc.invalidateQueries({ queryKey: [ "probePattern" ], exact: false });
  qc.invalidateQueries({ queryKey: sysKey("probeInstrumentation") });
}

export function useCreateProbePattern() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ProbePatternCreateRequest) =>
      api.post<ProbePatternOut>("/repository/probe-patterns", data),
    onSuccess: () => invalidatePatterns(qc),
  });
}

export function useUpdateProbePattern() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ patternId, ...data }: {
      patternId: number;
      name?: string;
      feature_id?: string;
      capability?: string;
      objective?: string;
      description?: string;
      status?: "active" | "archived";
    }) => api.patch<ProbePatternOut>(`/repository/probe-patterns/${patternId}`, data),
    onSuccess: () => invalidatePatterns(qc),
  });
}

export function useReconcileProbePattern() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patternId: number) =>
      api.post<ProbePatternReconciliationOut>(
        `/repository/probe-patterns/${patternId}/reconcile`,
      ),
    onSuccess: () => invalidatePatterns(qc),
  });
}

export function useReconcilePointDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pointId, decision }: { pointId: number; decision: "accepted" | "rejected" }) =>
      api.put<ReconcilePointOut>(
        `/repository/pattern-reconcile-points/${pointId}/decision`,
        { decision },
      ),
    onSuccess: () => invalidatePatterns(qc),
  });
}

export function useInvestigateReconcilePoint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pointId: number) =>
      api.post<ReconcilePointOut>(
        `/repository/pattern-reconcile-points/${pointId}/investigate`,
      ),
    onSuccess: () => invalidatePatterns(qc),
  });
}

export function usePatternRemovalPatches(patternId: number | null) {
  return useQuery({
    queryKey: sysKey("patternRemovalPatches", patternId),
    queryFn: () => api.get<ProbeRemovalPatchOut[]>(
      `/repository/probe-patterns/${patternId}/removal-patches`,
    ),
    enabled: patternId !== null && !!getSystemId(),
  });
}

export function useGenerateRemovalPatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ patternId, pointIds }: { patternId: number; pointIds?: number[] }) =>
      api.post<ProbeRemovalPatchOut>(
        `/repository/probe-patterns/${patternId}/removal-patches`,
        pointIds ? { point_ids: pointIds } : {},
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: sysKey("patternRemovalPatches", vars.patternId) });
      invalidatePatterns(qc);
    },
  });
}

export function useApplyRemovalPatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ patchId, expectedCommitSha }: { patchId: number; expectedCommitSha: string }) =>
      api.post<ProbeRemovalPatchOut>(
        `/repository/probe-removal-patches/${patchId}/apply`,
        { confirmed: true, expected_commit_sha: expectedCommitSha },
      ),
    onSuccess: () => {
      invalidatePatterns(qc);
    },
  });
}

// ── GitHub App publish workflow (Issue #216) ────────────────────────

export function useGithubAppStatus() {
  return useQuery({
    queryKey: ["github-app-status"],
    queryFn: () => api.get<GithubAppStatusOut>("/github/app-status"),
    staleTime: 60_000,
  });
}

export function useGithubConnections() {
  return useQuery({
    queryKey: sysKey("githubConnections"),
    queryFn: () => api.get<GithubConnectionOut[]>("/github/connections"),
    enabled: !!getSystemId(),
  });
}

export function useSystemGithubInstallations() {
  return useQuery({
    queryKey: sysKey("githubSystemInstallations"),
    queryFn: () => api.get<GithubInstallationOut[]>("/github/system-installations"),
    enabled: !!getSystemId(),
  });
}

export function useGithubInstallations() {
  return useQuery({
    queryKey: ["githubInstallations"],
    queryFn: () => api.get<GithubInstallationOut[]>("/github/installations"),
    retry: false,
  });
}

export function useRegisterGithubInstallation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (installationId: number) => api.post<GithubInstallationOut>(
      "/github/installations", { installation_id: installationId },
    ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["githubInstallations"] }),
  });
}

export function useDisableGithubInstallation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (installationId: number) => api.post<GithubInstallationOut>(
      `/github/installations/${installationId}/disable`,
    ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["githubInstallations"] });
      qc.invalidateQueries({ queryKey: sysKey("githubSystemInstallations") });
    },
  });
}

export function useAssignGithubInstallation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ installationId, systemId }: { installationId: number; systemId: number }) =>
      api.post<GithubInstallationOut>(`/github/installations/${installationId}/systems/${systemId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["githubInstallations"] });
      qc.invalidateQueries({ queryKey: sysKey("githubSystemInstallations") });
    },
  });
}

export function useGithubConnection(connectionId: number | null) {
  return useQuery({
    queryKey: sysKey("githubConnection", connectionId),
    queryFn: () => api.get<GithubConnectionOut>(`/github/connections/${connectionId}`),
    enabled: connectionId !== null && !!getSystemId(),
  });
}

export function useCreateGithubConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: GithubConnectionCreateRequest) =>
      api.post<GithubConnectionOut>("/github/connections", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("githubConnections") }),
  });
}

export function useVerifyGithubConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: number) =>
      api.post<GithubConnectionOut>(`/github/connections/${connectionId}/verify`),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("githubConnections") }),
  });
}

export function useSyncGithubConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: number) =>
      api.post<GithubConnectionOut>(`/github/connections/${connectionId}/sync`),
    onSuccess: (_d, connectionId) => {
      qc.invalidateQueries({ queryKey: sysKey("githubConnections") });
      qc.invalidateQueries({ queryKey: sysKey("githubRepositoryStatus", connectionId) });
    },
  });
}

export function useGithubRepositoryStatus(connectionId: number | null) {
  return useQuery({
    queryKey: sysKey("githubRepositoryStatus", connectionId),
    queryFn: () => api.get<GithubRepositoryStatusOut>(
      `/github/connections/${connectionId}/repository-status`,
    ),
    enabled: connectionId !== null && !!getSystemId(),
  });
}

export function useDeleteGithubConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: number) =>
      api.delete<GithubConnectionOut>(`/github/connections/${connectionId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("githubConnections") }),
  });
}

export function useInstallationRepositories(installationId: number | null) {
  return useQuery({
    queryKey: sysKey("githubInstallationRepositories", installationId),
    queryFn: () => api.get<GithubInstallationRepositoryOut[]>(
      `/github/installations/${installationId}/repositories`,
    ),
    enabled: installationId !== null && installationId > 0 && !!getSystemId(),
    retry: false,
  });
}

function publishJobInProgress(status: PublishJobOut["status"] | undefined): boolean {
  return status === "pending" || status === "authenticating" || status === "fetching"
    || status === "checking_out" || status === "applying_patch" || status === "validating"
    || status === "committing" || status === "pushing" || status === "creating_pr"
    || status === "reconciling";
}

export function usePublishJobs(connectionId: number | null) {
  return useQuery({
    queryKey: sysKey("publishJobs", connectionId),
    queryFn: () => {
      const qs = connectionId !== null ? `?connection_id=${connectionId}` : "";
      return api.get<PublishJobOut[]>(`/github/publish-jobs${qs}`);
    },
    enabled: !!getSystemId(),
    refetchInterval: (query) => {
      const jobs = query.state.data ?? [];
      return jobs.some(j => publishJobInProgress(j.status)) ? 2000 : false;
    },
  });
}

export function usePublishJob(jobId: number | null) {
  return useQuery({
    queryKey: sysKey("publishJob", jobId),
    queryFn: () => api.get<PublishJobOut>(`/github/publish-jobs/${jobId}`),
    enabled: jobId !== null && !!getSystemId(),
    refetchInterval: (query) => (publishJobInProgress(query.state.data?.status) ? 2000 : false),
  });
}

export function useCreatePublishJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ connectionId, patchId }: { connectionId: number; patchId: number }) =>
      api.post<PublishJobOut>(`/github/connections/${connectionId}/publish-jobs`, { patch_id: patchId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("publishJobs") }),
  });
}

export function useApprovePublishJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => api.post<PublishJobOut>(`/github/publish-jobs/${jobId}/approve`),
    onSuccess: (_d, jobId) => {
      qc.invalidateQueries({ queryKey: sysKey("publishJobs") });
      qc.invalidateQueries({ queryKey: sysKey("publishJob", jobId) });
    },
  });
}

export function useCancelPublishJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => api.post<PublishJobOut>(`/github/publish-jobs/${jobId}/cancel`),
    onSuccess: (_d, jobId) => {
      qc.invalidateQueries({ queryKey: sysKey("publishJobs") });
      qc.invalidateQueries({ queryKey: sysKey("publishJob", jobId) });
    },
  });
}

export function useRetryPublishJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => api.post<PublishJobOut>(`/github/publish-jobs/${jobId}/retry`),
    onSuccess: (_d, jobId) => {
      qc.invalidateQueries({ queryKey: sysKey("publishJobs") });
      qc.invalidateQueries({ queryKey: sysKey("publishJob", jobId) });
    },
  });
}

// Append-only audit trail for a publish job (Issues #226/#227), surfaced in
// the Dashboard by Issue #267 item 5 -- previously the retry/recovery
// history existed server-side (publish_recovery.py) with no UI.
export function usePublishJobEvents(jobId: number | null) {
  return useQuery({
    queryKey: sysKey("publishJobEvents", jobId),
    queryFn: () => api.get<PublishAuditEventOut[]>(`/github/publish-jobs/${jobId}/events`),
    enabled: jobId !== null && !!getSystemId(),
  });
}

// Issue #267 item 6: `DELETE /github/installations/{id}/systems/{sid}`
// existed server-side (Issue #216) with no Dashboard hook/UI.
export function useUnassignGithubInstallation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ installationId, systemId }: { installationId: number; systemId: number }) =>
      api.delete<void>(`/github/installations/${installationId}/systems/${systemId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["githubInstallations"] });
      qc.invalidateQueries({ queryKey: sysKey("githubSystemInstallations") });
    },
  });
}

export function useCreatePlanFromReconciliation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ patternId, reconciliationId, objective }: {
      patternId: number;
      reconciliationId: number;
      objective?: string;
    }) =>
      api.post<ProbePlanOut>(
        `/repository/probe-patterns/${patternId}/reconciliations/${reconciliationId}/create-plan`,
        objective ? { objective } : {},
      ),
    onSuccess: () => {
      invalidatePatterns(qc);
      qc.invalidateQueries({ queryKey: sysKey("probePlans") });
    },
  });
}

// ── Replay / Simulation Workbench (Issue #242 Phase D / #246) ──────────────
// Display + composition only: every judgement/execution/comparison decision
// is made by the Phase A-C APIs below. The two source/diff endpoints are
// the only new backend surface (deterministic, Principle 6).

export function useReplaySets(componentId?: string | null) {
  return useQuery({
    queryKey: sysKey("replaySets", componentId ?? null),
    queryFn: () => {
      const qs = componentId ? `?component_id=${encodeURIComponent(componentId)}` : "";
      return api.get<ReplaySetOut[]>(`/replay-sets${qs}`);
    },
    enabled: !!getSystemId(),
  });
}

export function useCreateReplaySet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { component_id: string; name?: string; trace_ids: string[] }) =>
      api.post<ReplaySetOut>("/replay-sets", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("replaySets") }),
  });
}

export function useReplaySet(id: number | null) {
  return useQuery({
    queryKey: sysKey("replaySet", id),
    queryFn: () => api.get<ReplaySetOut>(`/replay-sets/${id}`),
    enabled: id !== null && !!getSystemId(),
  });
}

export function useReplaySetSource(replaySetId: number | null, snapshotId?: number | null) {
  return useQuery({
    queryKey: sysKey("replaySetSource", replaySetId, snapshotId ?? null),
    queryFn: () => {
      const qs = snapshotId ? `?snapshot_id=${snapshotId}` : "";
      return api.get<ReplaySourceOut>(`/replay-sets/${replaySetId}/source${qs}`);
    },
    enabled: replaySetId !== null && !!getSystemId(),
  });
}

export function useReplaySourceDiff() {
  return useMutation({
    mutationFn: (data: { replay_set_id: number; snapshot_id?: number | null; edited_source: string }) =>
      api.post<ReplaySourceDiffOut>("/replay-source-diff", data),
  });
}

export function useCreateReplayVariantRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      replay_set_id: number;
      snapshot_id?: number | null;
      variants: { label: string; patch_text: string; source?: string }[];
    }) => api.post<ReplayVariantRunOut>("/replay-variant-runs", data),
    onSuccess: (_d, vars) =>
      qc.invalidateQueries({ queryKey: sysKey("replayVariantRuns", vars.replay_set_id) }),
  });
}

export function useReplayVariantRun(id: number | null) {
  return useQuery({
    queryKey: sysKey("replayVariantRun", id),
    queryFn: () => api.get<ReplayVariantRunOut>(`/replay-variant-runs/${id}`),
    enabled: id !== null && !!getSystemId(),
    refetchInterval: false,
  });
}

export function useReplayVariantRuns(replaySetId: number | null) {
  return useQuery({
    queryKey: sysKey("replayVariantRuns", replaySetId),
    queryFn: () => {
      const qs = replaySetId ? `?replay_set_id=${replaySetId}` : "";
      return api.get<ReplayVariantRunOut[]>(`/replay-variant-runs${qs}`);
    },
    enabled: replaySetId !== null && !!getSystemId(),
  });
}

export function useCreateReplayVariantDraft() {
  return useMutation({
    mutationFn: (data: {
      replay_set_id: number;
      trace_id: string;
      objective: string;
      snapshot_id?: number | null;
    }) => api.post<ReplayVariantDraftOut>("/replay-variant-drafts", data),
  });
}

export function useCreateReplayRegressionScaffold() {
  return useMutation({
    mutationFn: (data: {
      replay_run_id: number;
      replay_variant_id: number;
      trace_id: string;
    }) => api.post<ReplayRegressionScaffoldOut>("/replay-regression-scaffolds", data),
  });
}

export function useReplayApproval(componentId: string | null) {
  return useQuery({
    queryKey: sysKey("replayApproval", componentId),
    queryFn: () => api.get<ReplayApprovalStateOut>(`/components/${componentId}/replay-approval`),
    enabled: !!componentId && !!getSystemId(),
  });
}

export function useApproveReplay() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ componentId, reason }: { componentId: string; reason: string }) =>
      api.post<ReplayApprovalOut>(`/components/${componentId}/replay-approval`, { reason }),
    onSuccess: (_d, vars) =>
      qc.invalidateQueries({ queryKey: sysKey("replayApproval", vars.componentId) }),
  });
}

export function useRevokeReplayApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (componentId: string) =>
      api.post<ReplayApprovalOut>(`/components/${componentId}/replay-approval/revoke`),
    onSuccess: (_d, componentId) =>
      qc.invalidateQueries({ queryKey: sysKey("replayApproval", componentId) }),
  });
}

export function useVariantExperimentPayload(runId: number | null, variantId: number | null) {
  return useQuery({
    queryKey: sysKey("replayVariantExperimentPayload", runId, variantId),
    queryFn: () => api.get<ReplayVariantExperimentPayloadOut>(
      `/replay-variant-runs/${runId}/variants/${variantId}/experiment-payload`,
    ),
    enabled: runId !== null && variantId !== null && !!getSystemId(),
  });
}

// ── AI Candidate Studio (Issue #252) ────────────────────────────────
// A conversation + versioning layer over the existing isolated-Replay stack
// above -- reuses useReplayApproval/useReplayVariantRun for the approval
// gate and evaluation matrix, so no new judgement/execution path is added
// here (see api/types.ts's Candidate* section for the full contract).

export function useCandidateSessions(componentId?: string | null) {
  return useQuery({
    queryKey: sysKey("candidateSessions", componentId ?? null),
    queryFn: () => {
      const qs = componentId ? `?component_id=${encodeURIComponent(componentId)}` : "";
      return api.get<CandidateSessionOut[]>(`/candidate-sessions${qs}`);
    },
    enabled: !!getSystemId(),
  });
}

export function useCandidateSession(id: number | null) {
  return useQuery({
    queryKey: sysKey("candidateSession", id),
    queryFn: () => api.get<CandidateSessionOut>(`/candidate-sessions/${id}`),
    enabled: id !== null && !!getSystemId(),
  });
}

export function useCreateCandidateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CandidateSessionCreateRequest) =>
      api.post<CandidateSessionOut>("/candidate-sessions", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("candidateSessions") }),
  });
}

export function useSendCandidateMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, content }: { sessionId: number; content: string }) =>
      api.post<CandidateSessionOut>(`/candidate-sessions/${sessionId}/messages`, { content }),
    onSuccess: (_d, vars) =>
      qc.invalidateQueries({ queryKey: sysKey("candidateSession", vars.sessionId) }),
  });
}

export function useGenerateCandidateVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, instruction, parent_version_id }: {
      sessionId: number;
      instruction: string;
      parent_version_id?: number | null;
    }) =>
      api.post<CandidateVersionOut>(`/candidate-sessions/${sessionId}/generate`, {
        instruction,
        parent_version_id: parent_version_id ?? undefined,
      }),
    onSuccess: (_d, vars) =>
      qc.invalidateQueries({ queryKey: sysKey("candidateSession", vars.sessionId) }),
  });
}

export function useCandidateVersions(sessionId: number | null) {
  return useQuery({
    queryKey: sysKey("candidateVersions", sessionId),
    queryFn: () => api.get<CandidateVersionOut[]>(`/candidate-sessions/${sessionId}/versions`),
    enabled: sessionId !== null && !!getSystemId(),
  });
}

export function useCandidateVersion(versionId: number | null) {
  return useQuery({
    queryKey: sysKey("candidateVersion", versionId),
    queryFn: () => api.get<CandidateVersionOut>(`/candidate-versions/${versionId}`),
    enabled: versionId !== null && !!getSystemId(),
  });
}

export function useReplayCandidateVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ versionId }: {
      versionId: number;
      sessionId: number;
    }) =>
      api.post<CandidateVersionOut>(`/candidate-versions/${versionId}/replay`, {}),
    onSuccess: (_d, vars) =>
      qc.invalidateQueries({ queryKey: sysKey("candidateSession", vars.sessionId) }),
  });
}

export function usePromoteCandidateVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ versionId }: { versionId: number; sessionId: number }) =>
      api.post<CandidatePromotionOut>(`/candidate-versions/${versionId}/promote`),
    onSuccess: (_d, vars) =>
      qc.invalidateQueries({ queryKey: sysKey("candidateSession", vars.sessionId) }),
  });
}

// ── Probe Cell Fabric: Root Orchestrator digest / Ask lifecycle (Issue #303) ──

export function useCellRootDigest() {
  return useQuery({
    queryKey: sysKey("cellRootDigest"),
    queryFn: () => api.get<CellRootDigestOut>("/cell-fabric/root-digest"),
    enabled: !!getSystemId(),
    staleTime: 10_000,
  });
}

export function useCellAsks(status?: string) {
  return useQuery({
    queryKey: sysKey("cellAsks", status ?? "all"),
    queryFn: () => api.get<CellAsksListOut>(
      status ? `/cell-fabric/asks?status=${encodeURIComponent(status)}` : "/cell-fabric/asks",
    ),
    enabled: !!getSystemId(),
  });
}

function invalidateCellFabric(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: sysKey("cellRootDigest") });
  qc.invalidateQueries({ queryKey: ["cellAsks"], exact: false });
}

export function useSyncCellAsks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<CellAskSyncOut>("/cell-fabric/asks/sync"),
    onSuccess: () => invalidateCellFabric(qc),
  });
}

export function useDecideCellAsk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ askId, decision, note }: {
      askId: number;
      decision: CellAskDecision;
      note?: string;
    }) => api.post<CellAskOut>(`/cell-fabric/asks/${askId}/decide`, { decision, note: note ?? "" }),
    onSuccess: () => invalidateCellFabric(qc),
  });
}

// --- 共同理解セッション(Epic #328 / Issue #329-#332)------------------------
//
// 「わからない」から始まる共同理解の対話。どのフックも元の確認項目
// (Q&A / Intent / Review item / Inquiry)へは書き込まない — 項目の確定は
// 引き続き項目自身のエンドポイントだけが行う。
//
// 型は `@/api/types` の Joint* 定義を使う(サーバの有限語彙をそのまま保持し、
// 表示ラベルだけコンポーネント側で日本語化する)。

function _invalidateJointUnderstanding(
  qc: ReturnType<typeof useQueryClient>,
  sessionId: number | null,
  juId: number,
) {
  qc.invalidateQueries({ queryKey: [...sysKey("jointUnderstanding"), juId] });
  if (sessionId) {
    qc.invalidateQueries({ queryKey: [...sysKey("jointUnderstandingList"), sessionId] });
  }
}

export function useJointUnderstandingList(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("jointUnderstandingList"), sessionId],
    queryFn: () =>
      api.get<import("@/api/types").JointUnderstandingListOut>(
        `/interview/sessions/${sessionId}/joint-understanding`,
      ),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useJointUnderstandingDetail(juId: number | null) {
  return useQuery({
    queryKey: [...sysKey("jointUnderstanding"), juId],
    queryFn: () =>
      api.get<import("@/api/types").JointUnderstandingDetailOut>(
        `/joint-understanding/${juId}`,
      ),
    enabled: !!juId && !!getSystemId(),
  });
}

export function useCreateJointUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      origin_kind: import("@/api/types").JointUnderstandingOriginKind;
      origin_id: number;
      trigger: import("@/api/types").JointUnderstandingTrigger;
      question_text: string;
    }) =>
      api.post<import("@/api/types").JointUnderstandingDetailOut>(
        `/interview/sessions/${sessionId}/joint-understanding`,
        data,
      ),
    onSuccess: result => _invalidateJointUnderstanding(qc, sessionId, result.session.id),
  });
}

export function useInvestigateJointUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ juId, maxRounds }: { juId: number; maxRounds?: number }) =>
      api.post<import("@/api/types").JointUnderstandingInvestigateOut>(
        `/joint-understanding/${juId}/investigate`,
        maxRounds ? { max_rounds: maxRounds } : {},
      ),
    onSuccess: (_result, { juId }) => _invalidateJointUnderstanding(qc, sessionId, juId),
  });
}

export function useTranslateJointUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ juId, goalHint }: { juId: number; goalHint?: string }) =>
      api.post<import("@/api/types").JointUnderstandingTranslateOut>(
        `/joint-understanding/${juId}/translate`,
        goalHint ? { goal_hint: goalHint } : {},
      ),
    onSuccess: (_result, { juId }) => _invalidateJointUnderstanding(qc, sessionId, juId),
  });
}

export function useRecordJointUnderstandingAction(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ juId, actionKind, note }: {
      juId: number;
      actionKind: import("@/api/types").JointUnderstandingActionKind;
      note?: string;
    }) =>
      api.post<import("@/api/types").JointUnderstandingDetailOut>(
        `/joint-understanding/${juId}/actions`,
        { action_kind: actionKind, note: note ?? null },
      ),
    onSuccess: (_result, { juId }) => _invalidateJointUnderstanding(qc, sessionId, juId),
  });
}

export function useRefluxJointUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ juId }: { juId: number }) =>
      api.post<import("@/api/types").JointUnderstandingRefluxResultOut>(
        `/joint-understanding/${juId}/reflux`,
        {},
      ),
    onSuccess: (_result, { juId }) => _invalidateJointUnderstanding(qc, sessionId, juId),
  });
}

export function useCloseJointUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    // Issue #337: `reason` is the developer's stated judgement and is REQUIRED
    // by the server -- a close is the manual decision record of the
    // conversation, and an outcome label with no text is not auditable.
    mutationFn: ({ juId, outcome, outcomeFindingIds, reason }: {
      juId: number;
      outcome: import("@/api/types").JointUnderstandingOutcome;
      outcomeFindingIds?: number[];
      reason: string;
    }) =>
      api.post<import("@/api/types").JointUnderstandingOut>(
        `/joint-understanding/${juId}/close`,
        {
          outcome,
          outcome_finding_ids: outcomeFindingIds ?? [],
          outcome_reason: reason,
        },
      ),
    onSuccess: (_result, { juId }) => _invalidateJointUnderstanding(qc, sessionId, juId),
  });
}

export function useHoldJointUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ juId }: { juId: number }) =>
      api.post<import("@/api/types").JointUnderstandingOut>(
        `/joint-understanding/${juId}/hold`,
        {},
      ),
    onSuccess: (_result, { juId }) => _invalidateJointUnderstanding(qc, sessionId, juId),
  });
}

export function useResumeJointUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ juId }: { juId: number }) =>
      api.post<import("@/api/types").JointUnderstandingOut>(
        `/joint-understanding/${juId}/resume`,
        {},
      ),
    onSuccess: (_result, { juId }) => _invalidateJointUnderstanding(qc, sessionId, juId),
  });
}

// --- State-driven System Interview workflow (Issue #349) ---------------------
//
// One query owns the developer-facing state: the server evaluates
// docs/system-interview-workflow-ux.md §2.2 and returns the state, its single
// primary action, and the currently-active exceptions. The dashboard must not
// compute a workflow state from a mutation's `isPending` or any other
// client-only value -- those disappear on reload (spec §2.6).

export function useInterviewWorkflowState(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewWorkflowState"), sessionId],
    queryFn: () =>
      api.get<import("@/api/types").InterviewWorkflowStateOut>(
        sessionId
          ? `/interview/workflow-state?session_id=${sessionId}`
          : "/interview/workflow-state",
      ),
    enabled: !!getSystemId(),
    // A system process' running record is a server-side fact; poll while one
    // is in flight so `W1` clears by itself when it finishes.
    refetchInterval: (query) =>
      query.state.data?.running_processes?.length ? 2000 : false,
  });
}

// --- Understanding Brief / Decision Readiness (Issues #351-#354) ------------
//
// A second read-only query alongside the workflow state. It is deliberately
// NOT merged into `useInterviewWorkflowState`: the workflow state decides
// where the developer is and what the single primary action is, the Brief
// says what the system is understood to be and whether that understanding can
// be trusted. Keeping them apart is what stops the screen from reading a
// readiness verdict as a workflow position (#353).

export function useUnderstandingBrief(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("understandingBrief"), sessionId],
    queryFn: () =>
      api.get<import("@/api/types").UnderstandingBriefOut>(
        sessionId
          ? `/interview/understanding-brief?session_id=${sessionId}`
          : "/interview/understanding-brief",
      ),
    enabled: !!getSystemId(),
    // Mirrors the workflow query: while the server says a process is running,
    // poll so 「更新しています」 clears by itself. Nothing invalidates this
    // query when a background rebuild finishes on its own.
    refetchInterval: (query) =>
      query.state.data?.readiness_state === "building" ? 2000 : false,
  });
}

/** `W6` の主操作: 差分を確認したという明示記録 (永続事実 A)。 */
export function useRecordDiffReview(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reviewed_by?: string; note?: string }) =>
      api.post<import("@/api/types").InterviewDiffReviewOut>(
        `/interview/sessions/${sessionId}/diff-review`,
        body,
      ),
    onSuccess: () => _invalidateWorkflow(qc, sessionId),
  });
}

/** 戻り要求への明示承諾 (永続事実 D)。承諾は要求ごとに独立。 */
export function useAcknowledgeBackRequest(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ requestId, actor }: { requestId: number; actor?: string }) =>
      api.post<import("@/api/types").InterviewWorkflowStateOut>(
        `/interview/sessions/${sessionId}/back-requests/${requestId}/acknowledge`,
        { actor: actor ?? "" },
      ),
    onSuccess: () => _invalidateWorkflow(qc, sessionId),
  });
}

/** `OP-D14`: 中断 / 引き継ぎ終端へ安全に抜ける。 */
export function useCloseInterviewSession(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { terminal_kind: "suspended" | "handoff"; reason?: string; actor?: string }) =>
      api.post<import("@/api/types").InterviewWorkflowStateOut>(
        `/interview/sessions/${sessionId}/close`,
        body,
      ),
    onSuccess: () => _invalidateWorkflow(qc, sessionId),
  });
}

/** `OP-D14`: 終端からの再開。未解消の失敗は再び表示される。 */
export function useReopenInterviewSession(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { reason?: string; actor?: string }) =>
      api.post<import("@/api/types").InterviewWorkflowStateOut>(
        `/interview/sessions/${sessionId}/reopen`,
        body,
      ),
    onSuccess: () => _invalidateWorkflow(qc, sessionId),
  });
}

export function useInterviewProcessRuns(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewProcessRuns"), sessionId],
    queryFn: () =>
      api.get<import("@/api/types").InterviewProcessRunOut[]>(
        `/interview/sessions/${sessionId}/process-runs`,
      ),
    enabled: !!sessionId && !!getSystemId(),
  });
}

export function useInterviewSessionStatusAudit(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewStatusAudit"), sessionId],
    queryFn: () =>
      api.get<import("@/api/types").InterviewSessionStatusAuditOut[]>(
        `/interview/sessions/${sessionId}/status-audit`,
      ),
    enabled: !!sessionId && !!getSystemId(),
  });
}

/**
 * How long the Overview may keep showing a reception state that has since
 * expired. The Overview's freshness reading, its relative "N分前", its
 * connectivity finding and its CTA are all time-dependent: a screen left open
 * while traffic stops must notice, and nothing pushes that event to us.
 *
 * 5 minutes is the worst-case detection lag this introduces, and it is stated
 * here rather than left implicit.
 */
export const OVERVIEW_MAX_STALENESS_MS = 5 * 60_000;

/** A running process is the one case worth watching closely. */
export const OVERVIEW_RUNNING_POLL_MS = 3_000;

/**
 * When the next freshness threshold is crossed, in ms from now.
 *
 * The server sends the elapsed seconds it measured plus the System's own
 * thresholds, so this lands on the real boundary without trusting the browser
 * clock for anything but a duration. Refetching just after it means
 * `receiving_now → delayed → stale` (and the CTA that follows freshness)
 * update on their own instead of at the next reload.
 */
export function overviewBoundaryDelay(
  data: import("@/api/types").OverviewOut | undefined,
): number | null {
  const runtime = data?.runtime;
  if (!runtime) return null;
  const elapsed = runtime.seconds_since_last_trace;
  if (elapsed == null) return null;
  const next = [runtime.delayed_after_seconds, runtime.stale_after_seconds]
    .filter((threshold) => threshold > elapsed)
    .sort((a, b) => a - b)[0];
  if (next == null) return null;
  // +1s so the refetch lands after the boundary rather than exactly on it.
  return Math.max(1_000, (next - elapsed + 1) * 1_000);
}

// --- Overview / System Intelligence Brief (Issues #380-#384) ----------------
//
// One query for the whole Overview. It is deliberately NOT assembled from the
// Brief / workflow-state / connectivity / system-state queries on the client:
// the Overview's job is to say what is settled and what to do next, and a CTA
// stitched together from four independently-refetching caches can contradict
// itself between two renders. The server composes it once (#380 principle 6).
export function useOverview() {
  return useQuery({
    queryKey: sysKey("overview"),
    queryFn: () => api.get<import("@/api/types").OverviewOut>("/overview"),
    enabled: !!getSystemId(),
    // Three cadences, in priority order:
    //   1. a running process -> watch it closely so 「作成しています」 clears;
    //   2. the next freshness boundary -> so a system going quiet is noticed
    //      at the moment it happens rather than on the next reload;
    //   3. a bounded ceiling -> so an external change (a decision recorded on
    //      another screen, a publish completing) cannot sit unnoticed forever.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.next_action_state === "waiting") return OVERVIEW_RUNNING_POLL_MS;
      const boundary = overviewBoundaryDelay(data);
      if (boundary != null) return Math.min(boundary, OVERVIEW_MAX_STALENESS_MS);
      return OVERVIEW_MAX_STALENESS_MS;
    },
    // A tab the developer comes back to must not show yesterday's reading.
    //
    // `staleTime: 0` is load-bearing, not a default repeated for show. The
    // app-wide default is 30s (`main.tsx`), and a focus/reconnect refetch is
    // SKIPPED while data is still within it — so these two flags were inert
    // for the first 30 seconds of every visit. A browser test caught it: the
    // interval transitions worked (`refetchInterval` ignores `staleTime`)
    // while returning to the tab showed the old reading.
    staleTime: 0,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  });
}

// --- Purpose Chain (Issue #387 Epic / #388 / #389 / #390) -------------------
//
// `docs/purpose-chain.md` §0 invariant 2: the client re-derives no Purpose
// Chain judgement. These queries fetch the server's canonical projection
// verbatim; `components/purpose-chain/model.ts` only orders and labels what
// the server already decided.

/**
 * `GET /purpose-chain`. `sessionId=null` reads the System's newest session
 * (the same `ORDER BY id DESC` rule the Overview and the Understanding Brief
 * already use), so the three screens can never disagree about "the current
 * session". A `session_id` belonging to another System reads exactly like
 * "unselected" -- decided server-side, never re-checked here.
 */
export function usePurposeChain(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("purposeChain"), sessionId],
    queryFn: () =>
      api.get<PurposeChainOut>(
        sessionId ? `/purpose-chain?session_id=${sessionId}` : "/purpose-chain",
      ),
    enabled: !!getSystemId() && sessionId != null,
  });
}

/**
 * `GET /purpose-chain/next-question`. At most one question -- `needId`
 * (when supplied) is a DEEP-LINK hint only; the server decides the actual
 * fallback (§2.7's `fallback_reason`) rather than the Dashboard guessing
 * whether the named need is still current.
 */
export function usePurposeNextQuestion(sessionId: number | null, needId?: string | null) {
  return useQuery({
    queryKey: [...sysKey("purposeNextQuestion"), sessionId, needId ?? null],
    queryFn: () => {
      const params = new URLSearchParams();
      if (sessionId != null) params.set("session_id", String(sessionId));
      if (needId) params.set("need_id", needId);
      const qs = params.toString();
      return api.get<PurposeQuestionOut | null>(
        `/purpose-chain/next-question${qs ? `?${qs}` : ""}`,
      );
    },
    enabled: !!getSystemId() && sessionId != null,
  });
}

/**
 * Any mutation below that changes a Purpose Chain fact must invalidate every
 * consumer of it, not just this feature's own query -- the same #349 rule
 * `_invalidateWorkflow` exists for: a missed invalidation here freezes the
 * Purpose Frame panel until a reload. A relation decision or a need response
 * can also move the Understanding Brief / workflow state (§2.6: `confirm`/
 * `correct` on an Intent-sourced element reuses the EXISTING Intent
 * confirm/correct machinery), and always changes what the Overview's
 * embedded `purpose_chain` shows.
 */
function _invalidatePurposeChain(qc: ReturnType<typeof useQueryClient>, sessionId: number | null) {
  qc.invalidateQueries({ queryKey: [...sysKey("purposeChain"), sessionId] });
  qc.invalidateQueries({ queryKey: sysKey("purposeNextQuestion") });
  qc.invalidateQueries({ queryKey: sysKey("overview") });
  _invalidateWorkflow(qc, sessionId);
}

/** The one write #388 performs: a human confirming or rejecting ONE relation. */
export function useDecidePurposeRelation(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      relationId, decision, rationale,
    }: {
      relationId: string;
      decision: "confirmed" | "rejected";
      rationale?: string;
    }) =>
      api.post<PurposeRelationOut>(
        `/purpose-chain/relations/${encodeURIComponent(relationId)}/decision`,
        { session_id: sessionId, decision, rationale: rationale ?? "" },
      ),
    onSuccess: () => _invalidatePurposeChain(qc, sessionId),
  });
}

/** `POST /purpose-chain/needs/{need_id}/respond` -- confirm / correct /
 * unknown / defer / investigate, each a separately audited response kind
 * (§2.6). This is the ONLY write path for a need response; it never infers
 * one from a relation decision or an Intent confirm made elsewhere.
 *
 * No `rationale` field -- unlike the relation decision request, the server's
 * `PurposeNeedRespondRequest` accepts only `session_id` / `response_kind` /
 * `value_text` (`extra="forbid"`); a `correct` response's free text IS
 * `value_text`, whether that means a corrected value (an element target) or
 * a rejection reason (a relation target, where `correct` maps to
 * `record_relation_decision(..., decision="rejected", rationale=value_text)`
 * server-side). */
export function useRespondPurposeNeed(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      needId, responseKind, valueText,
    }: {
      needId: string;
      responseKind: PurposeResponseKind;
      valueText?: string;
    }) =>
      api.post<PurposeNeedResponseOut>(
        `/purpose-chain/needs/${encodeURIComponent(needId)}/respond`,
        { session_id: sessionId, response_kind: responseKind, value_text: valueText ?? "" },
      ),
    onSuccess: () => _invalidatePurposeChain(qc, sessionId),
  });
}

// --- Purpose Verification: Experience / Outcome / Reuse (Issue #391) --------
//
// §4.5's restraint: ONE query for the at-most-one prompt, ONE mutation to
// create the concept the prompt named. There is no listing/dashboard hook
// here on purpose -- `docs/purpose-chain.md` §4.5 and this Epic's non-goals
// explicitly rule out an outcome dashboard or a retention chart; the only
// UI surface is the single prompt inside the Purpose Frame panel.

/** `GET /purpose-chain/verification-prompt`. At most one prompt -- `null`
 * means 「検証条件はまだ必要ありません」 (§4.5's normal render). */
export function usePurposeVerificationPrompt(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("purposeVerificationPrompt"), sessionId],
    queryFn: () => {
      const qs = sessionId != null ? `?session_id=${sessionId}` : "";
      return api.get<PurposeVerificationPromptOut | null>(
        `/purpose-chain/verification-prompt${qs}`,
      );
    },
    enabled: !!getSystemId() && sessionId != null,
  });
}

export function usePurposeVerificationState(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("purposeVerificationState"), sessionId],
    queryFn: () => api.get<PurposeVerificationStateOut>(
      `/purpose-chain/verification?session_id=${sessionId}`,
    ),
    enabled: !!getSystemId() && sessionId != null,
  });
}

function _invalidatePurposeVerification(qc: ReturnType<typeof useQueryClient>, sessionId: number | null) {
  qc.invalidateQueries({ queryKey: [...sysKey("purposeVerificationState"), sessionId] });
  qc.invalidateQueries({ queryKey: [...sysKey("purposeVerificationPrompt"), sessionId] });
  _invalidatePurposeChain(qc, sessionId);
}

export function useConfirmVerificationConcept(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, id }: { kind: PurposeVerificationConceptKind; id: number }) => {
      const segment = kind === "experience_hypothesis" ? "experience-hypotheses"
        : kind === "reuse_hypothesis" ? "reuse-hypotheses" : "outcome-criteria";
      return api.post(`/${"purpose-chain"}/${segment}/${id}/confirm`, { session_id: sessionId });
    },
    onSuccess: () => _invalidatePurposeVerification(qc, sessionId),
  });
}

export function useLinkOutcomeCriterion(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, experimentId, candidateVersionId }: { id: number; experimentId?: number; candidateVersionId?: number }) =>
      api.post(`/purpose-chain/outcome-criteria/${id}/link`, {
        session_id: sessionId,
        experiment_id: experimentId ?? null,
        candidate_version_id: candidateVersionId ?? null,
      }),
    onSuccess: () => _invalidatePurposeVerification(qc, sessionId),
  });
}

export function useRecordOutcomeResult(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, source, verdict, evidenceText, isSynthetic }: {
      id: number; source: PurposeOutcomeEvidenceSource; verdict: PurposeOutcomeVerdict;
      evidenceText: string; isSynthetic: boolean;
    }) => api.post(`/purpose-chain/outcome-criteria/${id}/result`, {
      session_id: sessionId, source, verdict, evidence_text: evidenceText, is_synthetic: isSynthetic,
    }),
    onSuccess: () => _invalidatePurposeVerification(qc, sessionId),
  });
}

export function useRecordOutcomeUnavailable(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, source, state, reason }: {
      id: number; source: PurposeOutcomeEvidenceSource;
      state: "not_observed" | "not_computed"; reason: string;
    }) => api.post(`/purpose-chain/outcome-criteria/${id}/unavailable`, {
      session_id: sessionId, source, state, reason,
    }),
    onSuccess: () => _invalidatePurposeVerification(qc, sessionId),
  });
}

const _VERIFICATION_CREATE_PATH: Record<PurposeVerificationConceptKind, string> = {
  experience_hypothesis: "/purpose-chain/experience-hypotheses",
  outcome_criterion: "/purpose-chain/outcome-criteria",
  reuse_hypothesis: "/purpose-chain/reuse-hypotheses",
};

/** Creates ONE verification concept from an active prompt. `fields` carries
 * exactly what that concept kind's create request needs -- `statement` for
 * the two hypothesis kinds, the four Outcome Criterion fields for that one
 * -- the caller (the prompt's own minimal form) decides which. */
export function useCreateVerificationConcept(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      conceptKind, needId, fields,
    }: {
      conceptKind: PurposeVerificationConceptKind;
      needId: string;
      fields: Record<string, string>;
    }) =>
      api.post<
        PurposeExperienceHypothesisOut | PurposeReuseHypothesisOut | PurposeOutcomeCriterionOut
      >(_VERIFICATION_CREATE_PATH[conceptKind], {
        session_id: sessionId, need_id: needId, ...fields,
      }),
    onSuccess: () => {
      _invalidatePurposeVerification(qc, sessionId);
    },
  });
}

// --- Evolution Node (Epic #394 Phase 1, Issue #396) -------------------------

export function useEvolutionNodes() {
  const systemId = getSystemId();
  return useQuery<EvolutionNodesListOut>({
    queryKey: ["evolution-nodes", systemId],
    queryFn: () => api.get<EvolutionNodesListOut>("/evolution-nodes"),
  });
}

export function useEvolutionNode(nodeId: number | null) {
  const systemId = getSystemId();
  return useQuery<EvolutionNodeProjectionOut>({
    queryKey: ["evolution-node", systemId, nodeId],
    queryFn: () => api.get<EvolutionNodeProjectionOut>(`/evolution-nodes/${nodeId}`),
    enabled: nodeId !== null,
  });
}

export function useEvolutionNodeEvents(nodeId: number | null) {
  const systemId = getSystemId();
  return useQuery<EvolutionNodeEventsOut>({
    queryKey: ["evolution-node-events", systemId, nodeId],
    queryFn: () => api.get<EvolutionNodeEventsOut>(`/evolution-nodes/${nodeId}/events`),
    enabled: nodeId !== null,
  });
}

export function useCreateEvolutionNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { node_key: string; display_name?: string }) =>
      api.post<EvolutionNodeSummary>("/evolution-nodes", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["evolution-nodes"] });
    },
  });
}

/** Requests a maturity transition.
 *
 * A REJECTED transition arrives as an `ApiError` whose `code` is the server's
 * own finite rejection code; the caller shows that code plus the server's
 * message. The client never decides whether a transition is legal -- it has
 * no copy of the transition table, on purpose. */
export function useTransitionEvolutionNode(nodeId: number | null) {
  const qc = useQueryClient();
  const systemId = getSystemId();
  return useMutation({
    mutationFn: (body: {
      to_state: EvolutionMaturityState;
      decision_method: "manual" | "deterministic" | "reasoning_llm";
      reason?: string;
      evidence_refs?: string[];
      idempotency_key?: string;
    }) => api.post<EvolutionNodeTransitionOut>(
      `/evolution-nodes/${nodeId}/transitions`, body,
    ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["evolution-nodes"] });
      qc.invalidateQueries({ queryKey: ["evolution-node", systemId, nodeId] });
      qc.invalidateQueries({ queryKey: ["evolution-node-events", systemId, nodeId] });
    },
  });
}

// --- UX Design Lineage (Epic #405, Issues #407/#408/#409) --------------------
//
// `docs/ux-design-lineage.md` §0 invariant 9: the client re-derives no
// state. Every `Ux*Out` / `SolutionDesign*Out` field below (design_status,
// recheck_state, revision_state, every ref/link state, diffs, and the
// change-origin classification) arrives already decided by these endpoints;
// `components/ux-design/model.ts` only orders and labels what is returned.
//
// Mutations invalidate broadly across the whole UX Design Lineage query
// namespace rather than only the single row a write touched. A Journey
// revision can make an unrelated Requirement's step link `stale`, and a
// Requirement revision can make an unrelated Solution Design's requirement
// link `stale` (§2.9 downstream-only propagation) -- narrower invalidation
// would let those staleness reads go undetected until an unrelated refetch.

const UX_DESIGN_QUERY_BASES = [
  "ux-journeys", "ux-journey", "ux-journey-baseline-diff",
  "ux-journey-revisions", "ux-journey-diff",
  "ux-requirement-revisions", "ux-requirement-diff",
  "ux-requirements", "ux-requirement",
  "solution-designs", "solution-design",
  "solution-design-change-origins", "solution-design-handoff",
] as const;

function invalidateUxDesign(qc: ReturnType<typeof useQueryClient>) {
  for (const base of UX_DESIGN_QUERY_BASES) {
    qc.invalidateQueries({ queryKey: [base] });
  }
}

// --- Journey ------------------------------------------------------------

export function useUxJourneys() {
  return useQuery<UxJourneyListOut>({
    queryKey: sysKey("ux-journeys"),
    queryFn: () => api.get<UxJourneyListOut>("/ux-design/journeys"),
  });
}

export function useUxJourneyDetail(journeyKey: string | null) {
  return useQuery<UxJourneyDetailOut>({
    queryKey: [...sysKey("ux-journey"), journeyKey],
    queryFn: () => api.get<UxJourneyDetailOut>(`/ux-design/journeys/${encodeURIComponent(journeyKey ?? "")}`),
    enabled: journeyKey !== null,
  });
}

/** `GET /ux-design/journeys/{key}/baseline-diff`. `diff_state` is
 * `not_applicable` (never an empty diff) when the Journey has no linked
 * as-is baseline -- §2.10 / §4.3. */
export function useUxJourneyBaselineDiff(journeyKey: string | null) {
  return useQuery<UxJourneyDiffOut>({
    queryKey: [...sysKey("ux-journey-baseline-diff"), journeyKey],
    queryFn: () =>
      api.get<UxJourneyDiffOut>(`/ux-design/journeys/${encodeURIComponent(journeyKey ?? "")}/baseline-diff`),
    enabled: journeyKey !== null,
  });
}

/** `GET /ux-design/journeys/{key}/revisions` -- the append-only history.
 * A correction never overwrites its predecessor (contract §2.5), so the
 * older revisions stay readable here with their own `revision_state`. */
export function useUxJourneyRevisions(journeyKey: string | null) {
  return useQuery<UxJourneyRevisionListOut>({
    queryKey: [...sysKey("ux-journey-revisions"), journeyKey],
    queryFn: () =>
      api.get<UxJourneyRevisionListOut>(
        `/ux-design/journeys/${encodeURIComponent(journeyKey ?? "")}/revisions`,
      ),
    enabled: journeyKey !== null,
  });
}

/** `GET /ux-design/journeys/{key}/diff` -- one revision against another of the
 * SAME Journey, which is a different question from `baseline-diff`'s as-is vs
 * to-be. Steps are matched on exact `step_key` equality by the server. */
export function useUxJourneyRevisionDiff(
  journeyKey: string | null, fromRevision: number | null, toRevision: number | null,
) {
  return useQuery<UxJourneyDiffOut>({
    queryKey: [...sysKey("ux-journey-diff"), journeyKey, fromRevision, toRevision],
    queryFn: () =>
      api.get<UxJourneyDiffOut>(
        `/ux-design/journeys/${encodeURIComponent(journeyKey ?? "")}/diff`
        + `?from_revision=${fromRevision}&to_revision=${toRevision}`,
      ),
    enabled: journeyKey !== null && fromRevision !== null && toRevision !== null,
  });
}

/** `GET /ux-design/requirements/{key}/revisions`. */
export function useUxRequirementRevisions(requirementKey: string | null) {
  return useQuery<UxRequirementRevisionListOut>({
    queryKey: [...sysKey("ux-requirement-revisions"), requirementKey],
    queryFn: () =>
      api.get<UxRequirementRevisionListOut>(
        `/ux-design/requirements/${encodeURIComponent(requirementKey ?? "")}/revisions`,
      ),
    enabled: requirementKey !== null,
  });
}

/** `GET /ux-design/requirements/{key}/diff` -- acceptance criteria matched on
 * exact `criterion_key` equality by the server. */
export function useUxRequirementRevisionDiff(
  requirementKey: string | null, fromRevision: number | null, toRevision: number | null,
) {
  return useQuery<UxRequirementDiffOut>({
    queryKey: [...sysKey("ux-requirement-diff"), requirementKey, fromRevision, toRevision],
    queryFn: () =>
      api.get<UxRequirementDiffOut>(
        `/ux-design/requirements/${encodeURIComponent(requirementKey ?? "")}/diff`
        + `?from_revision=${fromRevision}&to_revision=${toRevision}`,
      ),
    enabled: requirementKey !== null && fromRevision !== null && toRevision !== null,
  });
}

export function useCreateUxJourney() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UxJourneyCreateRequest) => api.post<UxJourneyOut>("/ux-design/journeys", body),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

export function useAddUxJourneyRevision(journeyKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UxJourneyRevisionCreateRequest) =>
      api.post<UxJourneyDetailOut>(
        `/ux-design/journeys/${encodeURIComponent(journeyKey ?? "")}/revisions`, body,
      ),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

export function useAddUxJourneyUpstreamRef(journeyKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UxJourneyUpstreamRefCreateRequest) =>
      api.post<UxJourneyUpstreamRefOut>(
        `/ux-design/journeys/${encodeURIComponent(journeyKey ?? "")}/upstream-refs`, body,
      ),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

// --- Requirement ----------------------------------------------------------

export function useUxRequirements() {
  return useQuery<UxRequirementListOut>({
    queryKey: sysKey("ux-requirements"),
    queryFn: () => api.get<UxRequirementListOut>("/ux-design/requirements"),
  });
}

export function useUxRequirementDetail(requirementKey: string | null) {
  return useQuery<UxRequirementDetailOut>({
    queryKey: [...sysKey("ux-requirement"), requirementKey],
    queryFn: () =>
      api.get<UxRequirementDetailOut>(`/ux-design/requirements/${encodeURIComponent(requirementKey ?? "")}`),
    enabled: requirementKey !== null,
  });
}

/**
 * The batched read behind "Requirements linked to a Step" (§4.2 level 3):
 * there is no reverse `GET` from a Step to its Requirements, so the Studio
 * reads every Requirement's own detail (each individually cached and
 * reused by `components/ux-design/model.ts`'s `requirementsForStep`, a pure
 * structural join over the results -- no state is re-derived here).
 */
export function useUxRequirementDetailsBatch(keys: readonly string[]) {
  return useQueries({
    queries: keys.map((key) => ({
      queryKey: [...sysKey("ux-requirement"), key],
      queryFn: () => api.get<UxRequirementDetailOut>(`/ux-design/requirements/${encodeURIComponent(key)}`),
    })),
  });
}

export function useCreateUxRequirement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UxRequirementCreateRequest) =>
      api.post<UxRequirementOut>("/ux-design/requirements", body),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

export function useAddUxRequirementRevision(requirementKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UxRequirementRevisionCreateRequest) =>
      api.post<UxRequirementDetailOut>(
        `/ux-design/requirements/${encodeURIComponent(requirementKey ?? "")}/revisions`, body,
      ),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

export function useAddUxRequirementStepLink(requirementKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UxRequirementStepLinkCreateRequest) =>
      api.post<UxRequirementStepLinkOut>(
        `/ux-design/requirements/${encodeURIComponent(requirementKey ?? "")}/step-links`, body,
      ),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

// --- Artifact reference / decision (shared by Journey and Requirement) ----

export function useCreateUxArtifactReference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UxArtifactReferenceCreateRequest) =>
      api.post<UxArtifactReferenceOut>("/ux-design/artifact-references", body),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

/** Records a `confirm` / `reject` / `retire` / `reinstate` decision on a
 * Journey or a Requirement. `decision_method` is not part of the request
 * body -- the server derives it from the authenticated Principal (§2.10). */
export function useRecordUxDesignDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UxDesignDecisionCreateRequest) =>
      api.post<UxDesignDecisionOut>("/ux-design/decisions", body),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

// --- Solution Design (Issue #408) ------------------------------------------

export function useSolutionDesigns() {
  return useQuery<SolutionDesignListOut>({
    queryKey: sysKey("solution-designs"),
    queryFn: () => api.get<SolutionDesignListOut>("/solution-designs"),
  });
}

export function useSolutionDesignDetail(designKey: string | null) {
  return useQuery<SolutionDesignDetailOut>({
    queryKey: [...sysKey("solution-design"), designKey],
    queryFn: () => api.get<SolutionDesignDetailOut>(`/solution-designs/${encodeURIComponent(designKey ?? "")}`),
    enabled: designKey !== null,
  });
}

/** The batched read behind "which Solution Design targets this
 * Requirement" (§4.2 level 4's entry point) -- same discipline as
 * `useUxRequirementDetailsBatch`: no reverse `GET` exists, so every design's
 * own detail is read and joined client-side by
 * `model.ts`'s `solutionDesignsForRequirement`. */
export function useSolutionDesignDetailsBatch(keys: readonly string[]) {
  return useQueries({
    queries: keys.map((key) => ({
      queryKey: [...sysKey("solution-design"), key],
      queryFn: () => api.get<SolutionDesignDetailOut>(`/solution-designs/${encodeURIComponent(key)}`),
    })),
  });
}

export function useCreateSolutionDesign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SolutionDesignCreateRequest) =>
      api.post<SolutionDesignOut>("/solution-designs", body),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

export function useAddSolutionDesignOption(designKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SolutionDesignOptionCreateRequest) =>
      api.post<SolutionDesignOptionOut>(
        `/solution-designs/${encodeURIComponent(designKey ?? "")}/options`, body,
      ),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

export function useAddSolutionDesignRequirementLink(designKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SolutionDesignRequirementLinkCreateRequest) =>
      api.post<SolutionDesignRequirementLinkOut>(
        `/solution-designs/${encodeURIComponent(designKey ?? "")}/requirement-links`, body,
      ),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

export function useAddSolutionDesignTargetLink(designKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SolutionDesignTargetLinkCreateRequest) =>
      api.post<SolutionDesignTargetLinkOut>(
        `/solution-designs/${encodeURIComponent(designKey ?? "")}/target-links`, body,
      ),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

/** Records `adopt` / `hold` / `reject` / `withdraw` for one Option. §3.6: a
 * successful `adopt` mutates nothing outside `solution_design_decision` and
 * that Option's own `option_status` -- the Studio's copy for it must not
 * imply anything was applied, deployed, or approved for execution. */
export function useRecordSolutionDesignDecision(designKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SolutionDesignOptionDecisionCreateRequest) =>
      api.post<SolutionDesignDecisionOut>(
        `/solution-designs/${encodeURIComponent(designKey ?? "")}/decisions`, body,
      ),
    onSuccess: () => invalidateUxDesign(qc),
  });
}

export function useSolutionDesignChangeOrigins(designKey: string | null) {
  return useQuery<SolutionDesignChangeOriginsOut>({
    queryKey: [...sysKey("solution-design-change-origins"), designKey],
    queryFn: () =>
      api.get<SolutionDesignChangeOriginsOut>(
        `/solution-designs/${encodeURIComponent(designKey ?? "")}/change-origins`,
      ),
    enabled: designKey !== null,
  });
}

/** `GET /solution-designs/{key}/handoff` -- the adopted Option, its target
 * links, linked Requirements, and evaluation policy refs GROUPED BY LEVEL
 * (§3.7). Read-only; never composes a score. */
export function useSolutionDesignHandoff(designKey: string | null) {
  return useQuery<SolutionDesignHandoffOut>({
    queryKey: [...sysKey("solution-design-handoff"), designKey],
    queryFn: () =>
      api.get<SolutionDesignHandoffOut>(`/solution-designs/${encodeURIComponent(designKey ?? "")}/handoff`),
    enabled: designKey !== null,
  });
}

// --- Execution modes / Flow agents (Epic #412, #413/#414/#415) --------------
//
// Every GET below is a read-only projection the server recomputes on each
// call: `GET /flow-explanation`, `GET /execution-modes` and
// `GET /flow-experiments` write nothing, so opening the screen can never
// change a mode, a proposal's lifecycle or an Interview position (#380).
//
// The mutations are the human decisions of §10 (assign / revoke / approve /
// reject / withdraw). `actor` and `decision_method` are deliberately absent
// from every request body — the server takes the name from the authenticated
// principal and fixes `decision_method` to `manual` (#337). A refusal comes
// back as an `ApiError` carrying the server's own finite code
// (`ExecutionModeDenialOut.denial_code` or #415's lifecycle code); the client
// holds no copy of either gate and never pre-empts one.

export function useFlowSubjects(snapshotId?: number | null) {
  return useQuery<FlowSubjectListOut>({
    queryKey: sysKey("flow-subjects", snapshotId ?? null),
    queryFn: () =>
      api.get<FlowSubjectListOut>(
        `/flow-explanation/subjects${snapshotId ? `?snapshot_id=${snapshotId}` : ""}`,
      ),
  });
}

/** One Flow's §6.3 projection. A subject that does not resolve is NOT an
 * error — the response still carries its `resolution` and whatever sections
 * could be read, so the query stays successful and the page reports the
 * subject's own state. */
export function useFlowExplanation(
  subjectKind: FlowSubjectKind | null,
  subjectRef: string | null,
  snapshotId?: number | null,
) {
  return useQuery<FlowExplanationOut>({
    queryKey: sysKey("flow-explanation", subjectKind, subjectRef, snapshotId ?? null),
    queryFn: () => {
      const params = new URLSearchParams({
        subject_kind: subjectKind ?? "",
        subject_ref: subjectRef ?? "",
      });
      if (snapshotId) params.set("snapshot_id", String(snapshotId));
      return api.get<FlowExplanationOut>(`/flow-explanation?${params.toString()}`);
    },
    enabled: subjectKind !== null && !!subjectRef,
  });
}

export function useExecutionModeProjection() {
  return useQuery<ExecutionModeProjectionOut>({
    queryKey: sysKey("execution-mode-projection"),
    queryFn: () => api.get<ExecutionModeProjectionOut>("/execution-modes"),
  });
}

export function useExecutionModeAssignments(scopeKind?: ExecutionModeScopeKind | null) {
  return useQuery<ExecutionModeAssignmentOut[]>({
    queryKey: sysKey("execution-mode-assignments", scopeKind ?? null),
    queryFn: () =>
      api.get<ExecutionModeAssignmentOut[]>(
        `/execution-modes/assignments${scopeKind ? `?scope_kind=${scopeKind}` : ""}`,
      ),
  });
}

export function useExecutionModeDivergence() {
  return useQuery<ExecutionModeDivergenceListOut>({
    queryKey: sysKey("execution-mode-divergence"),
    queryFn: () => api.get<ExecutionModeDivergenceListOut>("/execution-modes/divergence"),
  });
}

function invalidateExecutionModes(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["execution-mode-projection"] });
  qc.invalidateQueries({ queryKey: ["execution-mode-assignments"] });
  qc.invalidateQueries({ queryKey: ["execution-mode-divergence"] });
  // The Flow projection embeds every Node's effective mode, so a changed
  // assignment makes it stale too.
  qc.invalidateQueries({ queryKey: ["flow-explanation"] });
}

export function useAssignExecutionMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ExecutionModeAssignRequest) =>
      api.post<ExecutionModeAssignmentOut>("/execution-modes/assignments", body),
    onSuccess: () => invalidateExecutionModes(qc),
  });
}

/** Ending an assignment explicitly. Deliberately NOT the same as letting an
 * `effective_until` window elapse: a revocation resumes normal inheritance,
 * an elapsed window clamps to `fixed` because nobody has decided what happens
 * next (EM-ADR-2). */
export function useRevokeExecutionModeAssignment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { assignmentId: number; reason: string }) =>
      api.post<ExecutionModeAssignmentOut>(
        `/execution-modes/assignments/${vars.assignmentId}/revoke`,
        { reason: vars.reason } satisfies ExecutionModeRevokeRequest,
      ),
    onSuccess: () => invalidateExecutionModes(qc),
  });
}

/** `flow_subject_kind` is sent alongside the ref because a proposal's identity
 * is the PAIR. Filtering on the ref alone let a runtime Flow and a static Flow
 * that happen to share a ref show their proposals in one list -- the two are
 * different subjects, and #405's rule that they are never collapsed into one
 * word applies to filtering just as much as to display. */
export function useFlowExperiments(
  flowSubjectRef?: string | null,
  flowSubjectKind?: FlowSubjectKind | null,
) {
  return useQuery<FlowExperimentListOut>({
    queryKey: sysKey(
      "flow-experiments",
      flowSubjectRef ?? null,
      flowSubjectKind ?? null,
    ),
    queryFn: () => {
      const params = new URLSearchParams();
      if (flowSubjectRef) params.set("flow_subject_ref", flowSubjectRef);
      if (flowSubjectKind) params.set("flow_subject_kind", flowSubjectKind);
      const query = params.toString();
      return api.get<FlowExperimentListOut>(
        `/flow-experiments${query ? `?${query}` : ""}`,
      );
    },
  });
}

/** Approve / reject / withdraw — three human decisions on one endpoint shape.
 * Approval permits nothing on its own: an execution additionally needs the
 * target Nodes' effective mode to permit `candidate_execution`, and the two
 * facts are never derived from each other (§7.5). */
export function useFlowExperimentDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      proposalId: number;
      action: "approve" | "reject" | "withdraw";
      reason: string;
    }) =>
      api.post<FlowExperimentProposalOut>(
        `/flow-experiments/${vars.proposalId}/${vars.action}`,
        { reason: vars.reason } satisfies FlowExperimentDecisionRequest,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flow-experiments"] });
      qc.invalidateQueries({ queryKey: ["flow-explanation"] });
    },
  });
}


// === Epic #418 / Issue #422 — Stakeholder Value Network hooks ===
// (Issue #422 owns everything between this marker and the #423 marker below.)

/** `GET /stakeholder-value-network` (§7.1). Read-only, deterministic; the
 * page renders this response as-is and re-derives nothing. */
export function useValueNetwork() {
  return useQuery<ValueNetworkOut>({
    queryKey: sysKey("stakeholder-value-network"),
    queryFn: () => api.get<ValueNetworkOut>("/stakeholder-value-network"),
    enabled: !!getSystemId(),
  });
}


// === Epic #418 / Issue #423 — Journey Service Blueprint hooks ===
// (Issue #423 owns everything below this marker.)

const JOURNEY_BLUEPRINT_QUERY_BASES = ["journey-blueprint", "journey-blueprint-diff"] as const;

function invalidateJourneyBlueprint(qc: ReturnType<typeof useQueryClient>) {
  for (const base of JOURNEY_BLUEPRINT_QUERY_BASES) {
    qc.invalidateQueries({ queryKey: sysKey(base) });
  }
}

/** `GET /journey-blueprint` (§8). Read-only, deterministic, no LLM; the
 * page renders this response as-is and re-derives nothing (§0 invariant 9). */
export function useJourneyBlueprint(journeyKey: string | null) {
  return useQuery<BlueprintOut>({
    queryKey: [...sysKey("journey-blueprint"), journeyKey],
    queryFn: () =>
      api.get<BlueprintOut>(`/journey-blueprint?journey_key=${encodeURIComponent(journeyKey ?? "")}`),
    enabled: journeyKey !== null,
  });
}

/** `GET /journey-blueprint/diff` (§8.3). `diff_state` is `not_applicable`
 * (never an empty diff) when no as-is baseline is linked. */
export function useJourneyBlueprintDiff(journeyKey: string | null) {
  return useQuery<BlueprintDiffOut>({
    queryKey: [...sysKey("journey-blueprint-diff"), journeyKey],
    queryFn: () =>
      api.get<BlueprintDiffOut>(`/journey-blueprint/diff?journey_key=${encodeURIComponent(journeyKey ?? "")}`),
    enabled: journeyKey !== null,
  });
}

export function useAddJourneyStepStakeholderLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JourneyStepStakeholderLinkCreateRequest) =>
      api.post<JourneyStepStakeholderLinkOut>("/journey-blueprint/stakeholder-links", body),
    onSuccess: () => invalidateJourneyBlueprint(qc),
  });
}

export function useAddJourneyStepDeliveryLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JourneyStepDeliveryLinkCreateRequest) =>
      api.post<JourneyStepDeliveryLinkOut>("/journey-blueprint/delivery-links", body),
    onSuccess: () => invalidateJourneyBlueprint(qc),
  });
}

export function useAddJourneyStepExchangeLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JourneyStepExchangeLinkCreateRequest) =>
      api.post<JourneyStepExchangeLinkOut>("/journey-blueprint/exchange-links", body),
    onSuccess: () => invalidateJourneyBlueprint(qc),
  });
}


// === Epic #418 / Issue #424 — Functional Lineage View hook ===

/** `GET /functional-lineage` (§9). Read-only, deterministic, no LLM; the
 * page renders this response as-is and re-derives nothing (§0 invariant 9). */
export function useFunctionalLineage() {
  return useQuery<FunctionalLineageOut>({
    queryKey: sysKey("functional-lineage"),
    queryFn: () => api.get<FunctionalLineageOut>("/functional-lineage"),
    enabled: !!getSystemId(),
  });
}

// === Epic #427 / Issue #431 — Product Feature hooks ===
//
// `docs/product-objective-lineage.md` §7.2. The Feature layer had a complete
// server (`/product-features`) and no editing surface at all, which left the
// Overview's `link_requirement_to_feature` next step with nowhere to be
// completed. These hooks back the Requirement -> Feature control on the UX
// Design Studio's Requirement detail: the Requirement is the subject the
// developer has open, while §7.2 stores the link on the FEATURE, so the
// surface and the endpoint sit at opposite ends of the same one link.
//
// Every write is `decision_method: manual` server-side; nothing here adopts,
// applies, or publishes anything.

const PRODUCT_FEATURE_QUERY_BASES = [
  "product-features", "product-feature", "functional-lineage", "overview",
] as const;

function invalidateProductFeature(qc: ReturnType<typeof useQueryClient>) {
  for (const base of PRODUCT_FEATURE_QUERY_BASES) {
    qc.invalidateQueries({ queryKey: [base] });
  }
}

/** `GET /product-features` (§7.2). */
export function useProductFeatures() {
  return useQuery<ProductFeatureListOut>({
    queryKey: sysKey("product-features"),
    queryFn: () => api.get<ProductFeatureListOut>("/product-features"),
    enabled: !!getSystemId(),
  });
}

/** The Feature details for a set of keys. §7.2 stores the Requirement link on
 * the Feature, so "which Features does this Requirement have" is only
 * answerable by reading each Feature's own links -- the same shape
 * `useSolutionDesignDetailsBatch` uses for the same question one layer over. */
export function useProductFeatureDetailsBatch(keys: readonly string[]) {
  return useQueries({
    queries: keys.map((key) => ({
      queryKey: [...sysKey("product-feature"), key],
      queryFn: () =>
        api.get<ProductFeatureDetailOut>(`/product-features/${encodeURIComponent(key)}`),
    })),
  });
}

/** `POST /product-features` (§7.2). A Feature identity is a developer-given
 * slug, never derived from a Requirement/Capability key (§1's identity rule). */
export function useCreateProductFeature() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductFeatureCreateRequest) =>
      api.post<ProductFeatureDetailOut>("/product-features", body),
    onSuccess: () => invalidateProductFeature(qc),
  });
}

/** `POST /product-features/{feature_key}/requirement-links` (§7.2) -- the one
 * write that completes the Overview's `link_requirement_to_feature` step. */
export function useAddProductFeatureRequirementLink(featureKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductFeatureRequirementLinkCreateRequest) =>
      api.post<ProductFeatureRequirementLinkOut>(
        `/product-features/${encodeURIComponent(featureKey ?? "")}/requirement-links`, body,
      ),
    onSuccess: () => invalidateProductFeature(qc),
  });
}

// === Epic #427 / Issue #432 — Objective Map / Gap Workbench hooks ===
//
// `docs/product-objective-lineage.md` §9. Both projections are read-only and
// re-derive nothing (§0 invariant 10); `components/product-objective/model.ts`
// is the only place that filters, labels, or walks the Objective tree. Write
// mutations below invalidate both projections AND the Overview, whose
// `objective` section reads the same underlying rows (§9.1).

const PRODUCT_OBJECTIVE_QUERY_BASES = [
  "objective-map", "gap-workbench", "product-gap", "product-objective", "product-milestone", "overview",
] as const;

function invalidateProductObjective(qc: ReturnType<typeof useQueryClient>) {
  for (const base of PRODUCT_OBJECTIVE_QUERY_BASES) {
    qc.invalidateQueries({ queryKey: [base] });
  }
}

/** `GET /objective-map` (§9.1). */
export function useObjectiveMap() {
  return useQuery<ObjectiveMapOut>({
    queryKey: sysKey("objective-map"),
    queryFn: () => api.get<ObjectiveMapOut>("/objective-map"),
    enabled: !!getSystemId(),
  });
}

/** `GET /gap-workbench` (§9.2). */
export function useGapWorkbench() {
  return useQuery<GapWorkbenchOut>({
    queryKey: sysKey("gap-workbench"),
    queryFn: () => api.get<GapWorkbenchOut>("/gap-workbench"),
    enabled: !!getSystemId(),
  });
}

/** `GET /product-gaps/{gap_key}` -- the Gap detail the Workbench's own list
 * entry does not carry (current/target state, interpretation, source refs,
 * evidence, artifact links, decision history; §5.1's six axes). */
export function useProductGapDetail(gapKey: string | null) {
  return useQuery<ProductGapDetailOut>({
    queryKey: [...sysKey("product-gap"), gapKey],
    queryFn: () => api.get<ProductGapDetailOut>(`/product-gaps/${encodeURIComponent(gapKey ?? "")}`),
    enabled: gapKey !== null,
  });
}

/** Records a `acknowledge`/`defer`/`resolve`/`reject`/`retire`/`reopen`/
 * `prioritize` decision on a Gap (§5.6/§5.7). Every one is
 * `decision_method: manual` on the server; this hook offers every kind and
 * lets an illegal transition come back as the server's own finite rejection
 * code (§10.1) rather than holding a second legality table here (same
 * discipline `useRecordUxDesignDecision` documents). */
export function useRecordProductGapDecision(gapKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductGapDecisionCreateRequest) =>
      api.post<ProductGapDecisionOut>(`/product-gaps/${encodeURIComponent(gapKey ?? "")}/decisions`, body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

/** 関連付け (§9.2): links a Gap to an Issue Draft / UX Journey / UX
 * Requirement / Feature / Solution Design. Selecting a Gap never triggers
 * this on its own -- it is only ever the result of the developer submitting
 * this form (§9.2 non-goal: no automatic execution on selection). */
export function useAddProductGapArtifactLink(gapKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductGapArtifactLinkCreateRequest) =>
      api.post<ProductGapArtifactOut>(`/product-gaps/${encodeURIComponent(gapKey ?? "")}/artifact-links`, body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

/** §5.11: a Gap's Journey connection has exactly ONE writable home,
 * `ux_journey_upstream_ref(ref_kind='product_gap')` -- never
 * `product_gap_artifact_link`. This is the SAME endpoint
 * `useAddUxJourneyUpstreamRef` calls; a dedicated hook exists here (rather
 * than reusing that one directly from the Gap Workbench) only so the
 * success handler invalidates the Objective Map / Gap Workbench projections
 * TOO -- `link_gap_to_journey`'s §9.3 next-step reads this exact relation,
 * so writing it must not leave those two screens showing the pre-link
 * state until an unrelated refetch. */
export function useLinkProductGapToJourney(gapKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ journeyKey, note }: { journeyKey: string; note?: string }) =>
      api.post<UxJourneyUpstreamRefOut>(
        `/ux-design/journeys/${encodeURIComponent(journeyKey)}/upstream-refs`,
        { ref_kind: "product_gap", target_ref: gapKey ?? "", note: note ?? "" } satisfies UxJourneyUpstreamRefCreateRequest,
      ),
    onSuccess: () => {
      invalidateProductObjective(qc);
      invalidateUxDesign(qc);
    },
  });
}

/** `POST /product-gaps` (§4.1/§10): creates the Gap identity row under a
 * Milestone. Content (current/target state, interpretation) is a SEPARATE
 * revision write (`useAddProductGapRevision`), matching every sibling
 * identity/content split in this Epic (§2). */
export function useCreateProductGap() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductGapCreateRequest) => api.post<ProductGapOut>("/product-gaps", body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

/** `POST /product-gaps/{gap_key}/revisions` -- append-only content (§5.1/§5.9).
 * `authored_by_kind`/`decision_method` are always `developer`/`manual` for a
 * Dashboard-submitted revision; the server never accepts them from the
 * request body. */
export function useAddProductGapRevision(gapKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductGapRevisionCreateRequest) =>
      api.post<ProductGapOut>(`/product-gaps/${encodeURIComponent(gapKey ?? "")}/revisions`, body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

// --- #429 Objective ---------------------------------------------------------

/** `GET /product-objectives/{objective_key}` -- the full detail (current
 * revision's `content_digest`, upstream refs, decision history) the
 * Objective Map's tree node does not carry. Every Objective decision must
 * send this `current_revision`'s `content_digest` as `captured_digest`
 * (§4.2/§10.1's stale-digest gate; unlike a Gap, an Objective/Milestone
 * decision is judged against its OWN revision, never an inherited one). */
export function useProductObjectiveDetail(objectiveKey: string | null) {
  return useQuery<ProductObjectiveDetailOut>({
    queryKey: [...sysKey("product-objective"), objectiveKey],
    queryFn: () => api.get<ProductObjectiveDetailOut>(`/product-objectives/${encodeURIComponent(objectiveKey ?? "")}`),
    enabled: objectiveKey !== null,
  });
}

export function useCreateProductObjective() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductObjectiveCreateRequest) => api.post<ProductObjectiveOut>("/product-objectives", body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

export function useAddProductObjectiveRevision(objectiveKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductObjectiveRevisionCreateRequest) =>
      api.post<ProductObjectiveOut>(`/product-objectives/${encodeURIComponent(objectiveKey ?? "")}/revisions`, body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

/** Records a `confirm`/`activate`/`achieve`/`reject`/`retire`/`reinstate`
 * decision (§4.3). Every kind is offered; an illegal transition comes back
 * as the server's own finite `product_objective_not_decidable` rejection
 * rather than a second legality table here (same discipline
 * `useRecordProductGapDecision` documents). */
export function useRecordProductObjectiveDecision(objectiveKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductObjectiveDecisionCreateRequest) =>
      api.post<ProductObjectiveDecisionOut>(`/product-objectives/${encodeURIComponent(objectiveKey ?? "")}/decisions`, body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

// --- #429 Milestone ----------------------------------------------------------

/** `GET /product-milestones/{milestone_key}` -- current revision's
 * `content_digest` for the same reason `useProductObjectiveDetail`
 * documents. */
export function useProductMilestoneDetail(milestoneKey: string | null) {
  return useQuery<ProductMilestoneDetailOut>({
    queryKey: [...sysKey("product-milestone"), milestoneKey],
    queryFn: () => api.get<ProductMilestoneDetailOut>(`/product-milestones/${encodeURIComponent(milestoneKey ?? "")}`),
    enabled: milestoneKey !== null,
  });
}

export function useCreateProductMilestone() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductMilestoneCreateRequest) => api.post<ProductMilestoneOut>("/product-milestones", body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

export function useAddProductMilestoneRevision(milestoneKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductMilestoneRevisionCreateRequest) =>
      api.post<ProductMilestoneOut>(`/product-milestones/${encodeURIComponent(milestoneKey ?? "")}/revisions`, body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

/** Records a `confirm`/`reject`/`retire`/`reinstate` decision on the
 * Milestone's DEFINITION (§4.2 `design_status`) -- separate from
 * `useRecordProductMilestoneAssessment`, which judges ACHIEVEMENT. */
export function useRecordProductMilestoneDecision(milestoneKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductMilestoneDecisionCreateRequest) =>
      api.post<ProductMilestoneDecisionOut>(`/product-milestones/${encodeURIComponent(milestoneKey ?? "")}/decisions`, body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}

/** Records a `met`/`not_met`/`indeterminate`/`withdraw` assessment (§4.3).
 * The server refuses (422 `product_milestone_not_assessable`) while
 * `design_status !== "confirmed"` -- this hook does not pre-check that
 * client-side (§0 invariant 10). */
export function useRecordProductMilestoneAssessment(milestoneKey: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductMilestoneAssessmentCreateRequest) =>
      api.post<ProductMilestoneAssessmentOut>(`/product-milestones/${encodeURIComponent(milestoneKey ?? "")}/assessments`, body),
    onSuccess: () => invalidateProductObjective(qc),
  });
}
