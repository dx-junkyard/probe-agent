import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, getSystemId } from "./client";
import type {
  SystemOut, ComponentSummary, TraceEvent, Policy,
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
} from "./types";

function sysKey(base: string, ...extra: unknown[]) {
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

export function useComponents() {
  return useQuery({
    queryKey: sysKey("components"),
    queryFn: () => api.get<ComponentSummary[]>("/components"),
    enabled: !!getSystemId(),
  });
}

export function useTraces(componentId: string | null, limit = 50) {
  return useQuery({
    queryKey: [...sysKey("traces"), componentId, limit],
    queryFn: () => api.get<TraceEvent[]>(`/components/${componentId}/traces?limit=${limit}`),
    enabled: !!componentId && !!getSystemId(),
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
    mutationFn: (data: { user_message: string; budget?: number }) =>
      api.post<InterviewDialogueTurnOut>(`/interview/sessions/${sessionId}/dialogue-turn`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
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
    },
  });
}

export function useUpdateInterviewUnderstanding(sessionId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<InterviewSessionOut>(
        `/interview/sessions/${sessionId}/update-understanding`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...sysKey("interviewSession"), sessionId] });
      qc.invalidateQueries({ queryKey: sysKey("interviewSessions") });
    },
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

export function useLogin() {
  return useMutation({
    mutationFn: (data: { username: string; password: string }) =>
      api.post<{ access_token: string; token_type: string; expires_at: string }>("/auth/login", data),
  });
}

export function useLogout() {
  return useMutation({ mutationFn: () => api.post("/auth/logout") });
}
