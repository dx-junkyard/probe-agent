import { Routes, Route } from "react-router-dom";
import { AppLayout } from "./components/layout/app-layout";
import LoginPage from "./pages/login";
import OverviewPage from "./pages/overview";
import RepositoryPage from "./pages/repository";
import FeatureMapPage from "./pages/feature-map";
import CapabilityMapPage from "./pages/capability-map";
import InterviewPage from "./pages/interview";
import ProbePlannerPage from "./pages/probe-planner";
import FlowExplorerPage from "./pages/flow-explorer";
import ExperimentsPage from "./pages/experiments";
import ConnectSdkPage from "./pages/connect-sdk";
import GenerationPage from "./pages/generation";
import ComponentsPage from "./pages/components";
import SettingsPage from "./pages/settings";
import AdminPage from "./pages/admin";
import WorkspacesPage from "./pages/workspaces";
import SystemUnderstandingPage from "./pages/system-understanding";

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
        <Route path="probe-planner" element={<ProbePlannerPage />} />
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="connect-sdk" element={<ConnectSdkPage />} />
        <Route path="generation" element={<GenerationPage />} />
        <Route path="components" element={<ComponentsPage />} />
        <Route path="workspaces" element={<WorkspacesPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="admin" element={<AdminPage />} />
      </Route>
    </Routes>
  );
}
