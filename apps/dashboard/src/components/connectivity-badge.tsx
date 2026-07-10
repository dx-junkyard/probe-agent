import { Link } from "react-router-dom";
import { RadioTower } from "lucide-react";
import { useConnectivityStatus } from "@/api/hooks";

// Connectivity warning badge (Issue #165).
//
// Shows an observation-based warning while the selected system has never
// received a trace, and an intermediate state while only manual smoke traces
// have arrived. Both link to the setup guide; once a real workload trace is
// received the badge disappears. The wording never claims "未設定" — the
// server only knows that nothing has been observed, not why.
export function ConnectivityBadge() {
  const { data } = useConnectivityStatus();

  if (!data || data.state === "receiving") return null;

  const isNoSignal = data.state === "no_signal";
  return (
    <Link
      to="/setup-guide"
      data-testid="connectivity-badge"
      data-state={data.state}
      title={
        isNoSignal
          ? "このシステムはまだ trace / signal を一度も受信していません。クリックで接続セットアップガイドを開きます。"
          : "手動の疎通確認 trace のみ受信しています。実 workload からの trace はまだ届いていません。"
      }
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
        isNoSignal
          ? "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
          : "border-blue-300 bg-blue-50 text-blue-800 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950/30 dark:text-blue-200"
      }`}
    >
      <RadioTower className="h-3.5 w-3.5" />
      {isNoSignal ? "シグナル未受信 — 疎通確認が必要" : "疎通確認のみ受信"}
    </Link>
  );
}
