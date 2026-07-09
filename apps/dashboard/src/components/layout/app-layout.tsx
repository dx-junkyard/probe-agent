import { Outlet, Navigate } from "react-router-dom";
import { useAuth } from "@/api/auth";
import { useRepositoryStatus } from "@/api/hooks";
import { Sidebar } from "./sidebar";
import { Header } from "./header";
import { Skeleton } from "@/components/ui/skeleton";
import { AssistantPanel } from "@/components/assistant-panel";
import { useRef } from "react";

export function AppLayout() {
  const { user, loading } = useAuth();
  const mainRef = useRef<HTMLElement>(null);
  const { data: repositoryStatus } = useRepositoryStatus();

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
        <main ref={mainRef} className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
      <AssistantPanel
        snapshotNotice={
          repositoryStatus?.snapshot_stale
            ? "HEAD が最新 snapshot より進んでいます。snapshot を作成してください。"
            : repositoryStatus?.working_tree_dirty
              ? "未コミット差分があります。snapshot の前提を確認してください。"
              : null
        }
        onSnapshotNoticeClick={() => {
          mainRef.current?.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />
    </div>
  );
}
