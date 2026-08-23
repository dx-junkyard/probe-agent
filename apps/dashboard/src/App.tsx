import { Routes, Route } from "react-router-dom";
import { AppLayout } from "./components/layout/app-layout";
import LoginPage from "./pages/login";
import OverviewPage from "./pages/overview";
import RepositoryPage from "./pages/repository";
import FeatureMapPage from "./pages/feature-map";
import CapabilityMapPage from "./pages/capability-map";
import InterviewPage from "./pages/interview";
import ProbePlannerPage from "./pages/probe-planner";
import ProbePatternsPage from "./pages/probe-patterns";
import FlowExplorerPage from "./pages/flow-explorer";
import TraceLineagePage from "./pages/trace-lineage";
import TraceAnalyzersPage from "./pages/trace-analyzers";
import ExperimentsPage from "./pages/experiments";
import ConnectSdkPage from "./pages/connect-sdk";
import SetupGuidePage from "./pages/setup-guide";
import GenerationPage from "./pages/generation";
import ComponentsPage from "./pages/components";
import SimulationWorkbenchPage from "./pages/simulation-workbench";
import CandidateStudioPage from "./pages/candidate-studio";
import SettingsPage from "./pages/settings";
import AdminPage from "./pages/admin";
import WorkspacesPage from "./pages/workspaces";
import SystemUnderstandingPage from "./pages/system-understanding";
import GithubPage from "./pages/github";
import CellFabricPage from "./pages/cell-fabric";
import EvolutionNodesPage from "./pages/evolution-nodes";
import UxDesignStudioPage from "./pages/ux-design-studio";
import StakeholderValueNetworkPage from "./pages/stakeholder-value-network";
import FlowAgentsPage from "./pages/flow-agents";
import JourneyBlueprintPage from "./pages/journey-blueprint";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppLayout />}>
        <Route index element={<OverviewPage />} />
        <Route path="system-understanding" element={<SystemUnderstandingPage />} />
        <Route path="repository" element={<RepositoryPage />} />
        <Route path="feature-map" element={<FeatureMapPage />} />
        <Route path="capability-map" element={<CapabilityMapPage />} />
        <Route path="interview" element={<InterviewPage />} />
        <Route path="flow-explorer" element={<FlowExplorerPage />} />
        <Route path="trace-lineage" element={<TraceLineagePage />} />
        <Route path="trace-analyzers" element={<TraceAnalyzersPage />} />
        <Route path="probe-planner" element={<ProbePlannerPage />} />
        <Route path="probe-patterns" element={<ProbePatternsPage />} />
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="connect-sdk" element={<ConnectSdkPage />} />
        <Route path="setup-guide" element={<SetupGuidePage />} />
        <Route path="generation" element={<GenerationPage />} />
        <Route path="components" element={<ComponentsPage />} />
        <Route path="simulation-workbench" element={<SimulationWorkbenchPage />} />
        <Route path="candidate-studio" element={<CandidateStudioPage />} />
        <Route path="workspaces" element={<WorkspacesPage />} />
        <Route path="github" element={<GithubPage />} />
        <Route path="cell-fabric" element={<CellFabricPage />} />
        <Route path="evolution-nodes" element={<EvolutionNodesPage />} />
        <Route path="flow-agents" element={<FlowAgentsPage />} />
        <Route path="ux-design-studio" element={<UxDesignStudioPage />} />
        <Route path="journey-blueprint" element={<JourneyBlueprintPage />} />
        <Route path="stakeholder-value-network" element={<StakeholderValueNetworkPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="admin" element={<AdminPage />} />
      </Route>
    </Routes>
  );
}
