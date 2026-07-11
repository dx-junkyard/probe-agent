import { Outlet, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "@/api/auth";
import { useSystemState } from "@/api/hooks";
import { Sidebar } from "./sidebar";
import { Header } from "./header";
import { Skeleton } from "@/components/ui/skeleton";
import { AssistantPanel } from "@/components/assistant-panel";
import { systemStateTarget } from "@/components/system-state";

export function AppLayout() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const { data: systemState } = useSystemState();
  const primaryNotice = systemState?.notification_items[0] ?? null;

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="space-y-4 w-64">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      </div>
    );
  }

  if (!user) return <Navigate to="/login" replace />;

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
      <AssistantPanel
        snapshotNotice={primaryNotice?.summary ?? null}
        onSnapshotNoticeClick={() => {
          const target = primaryNotice ? systemStateTarget(primaryNotice) : null;
          if (target) navigate(target);
        }}
      />
    </div>
  );
}
