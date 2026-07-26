import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, getSystemId } from "./client";
import type {
  SystemOut, ComponentSummary, TraceEvent, Policy,
  LineageOut, TraceAnalyzer, AnalysisRun, AnalyzerContext,
  RepositoryStatus,
  RepositoryResyncJob,
  SystemStateAssessment,
  FlowOverlayOut, FlowOverlayRequest,
  ShadowResult, ComponentProfile, UserOut, TokenOut,
  RepositoryCandidateOut, RepositoryConfigOut, SnapshotOut, LatestDraftsOut,
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
  InterviewSessionOut, InterviewSessionDetailOut, InterviewContextPack,
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
  AlignmentRuleObjectionListOut, AlignmentRuleRecheckOut,
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
    onSuccess: () => qc.invalidateQueries({ queryKey: sysKey("interviewSessions") }),
  });
}

export function useInterviewSession(sessionId: number | null) {
  return useQuery({
    queryKey: [...sysKey("interviewSession"), sessionId],
    queryFn: () => api.get<InterviewSessionDetailOut>(`/interview/sessions/${sessionId}`),
    enabled: !!sessionId && !!getSystemId(),
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
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
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
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] }),
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
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] }),
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
    onSuccess: () => _invalidateHandoffs(qc, sessionId),
  });
}

export function useAnswerQuestionHandoff(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ handoffId, answer_text, answered_by }: { handoffId: number; answer_text: string; answered_by: string }) =>
      api.post<QuestionHandoffOut>(`/interview/handoffs/${handoffId}/answer`, { answer_text, answered_by }),
    onSuccess: () => _invalidateHandoffs(qc, sessionId),
  });
}

export function useReturnQuestionHandoff(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ handoffId }: { handoffId: number }) =>
      api.post<QuestionHandoffOut>(`/interview/handoffs/${handoffId}/return`),
    onSuccess: () => _invalidateHandoffs(qc, sessionId),
  });
}

export function useCancelQuestionHandoff(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ handoffId }: { handoffId: number }) =>
      api.post<QuestionHandoffOut>(`/interview/handoffs/${handoffId}/cancel`),
    onSuccess: () => _invalidateHandoffs(qc, sessionId),
  });
}

export function useSkipInterviewQa(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ qaId, actor }: { qaId: number; actor: string }) =>
      api.post<InterviewQaOut>(`/interview/sessions/${sessionId}/qa/${qaId}/skip`, { actor }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] }),
  });
}

export function useResumeInterviewQa(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ qaId, actor }: { qaId: number; actor: string }) =>
      api.post<InterviewQaOut>(`/interview/sessions/${sessionId}/qa/${qaId}/resume`, { actor }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("interviewQa"), sessionId] }),
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
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("interviewIntent"), sessionId] }),
  });
}

export function useConfirmInterviewIntentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId }: { itemId: number }) =>
      api.post<InterviewIntentItemOut>(`/interview/intent/${itemId}/confirm`),
    onSuccess: () => {
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
    onSuccess: () => qc.invalidateQueries({ queryKey: [...sysKey("interviewIntent"), sessionId] }),
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
    onSuccess: (_result, { inquiryId }) => _invalidateInquiry(qc, sessionId, inquiryId),
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
    onSuccess: (_result, { inquiryId }) => _invalidateInquiry(qc, sessionId, inquiryId),
  });
}

export function useResumeInterviewInquiry(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId }: { inquiryId: number }) =>
      api.post<InterviewInquiryOut>(`/interview/inquiries/${inquiryId}/resume`),
    onSuccess: (_result, { inquiryId }) => _invalidateInquiry(qc, sessionId, inquiryId),
  });
}

export function useCancelInterviewInquiry(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ inquiryId }: { inquiryId: number }) =>
      api.post<InterviewInquiryOut>(`/interview/inquiries/${inquiryId}/cancel`),
    onSuccess: (_result, { inquiryId }) => _invalidateInquiry(qc, sessionId, inquiryId),
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
    mutationFn: ({ reasonCode }: { reasonCode: string }) =>
      api.post<AlignmentRuleRecheckOut>(`/interview/alignment/rules/${reasonCode}/recheck`),
    onSuccess: () => _invalidateAlignment(qc, sessionId),
  });
}

export function useBuildAlignment(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AlignmentBuildOut>(`/interview/sessions/${sessionId}/alignment/build`),
    onSuccess: () => _invalidateAlignment(qc, sessionId),
  });
}

export function useAnswerAlignmentItem(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, decision, note }: { itemId: number; decision: AlignmentDecisionAction; note?: string }) =>
      api.post<AlignmentItemOut>(`/interview/alignment/${itemId}/answer`, { decision, note }),
    onSuccess: () => {
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
    onSuccess: () => _invalidateAlignment(qc, sessionId),
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
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
      qc.invalidateQueries({ queryKey: sysKey("system-diagnostics") });
    },
  });
}

export function useConfirmInterviewUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { actor: string }) =>
      api.post<InterviewSessionOut>(
        `/interview/sessions/${sessionId}/confirm-understanding`,
        data,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
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
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
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

export function useAssistantAsk() {
  return useMutation({
    mutationFn: (data: AssistantAskRequest) =>
      api.post<AssistantAskOut>("/assistant/ask", data),
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
