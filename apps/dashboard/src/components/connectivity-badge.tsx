import { Link } from "react-router-dom";
import { RadioTower } from "lucide-react";
import { useConnectivityStatus } from "@/api/hooks";

// Connectivity warning badge (Issues #165, #370).
//
// Shows an observation-based warning while the selected system is not
// currently receiving traces. The wording never claims "未設定" — the server
// only knows that nothing has been observed, not why.
//
// Issue #370: the badge reads `freshness` (the live axis), not `state` (the
// cumulative "has ever connected" milestone). A single trace from two weeks
// ago used to hide this badge permanently; a system that has stopped
// reporting is exactly the case it needs to surface.
type BadgeSpec = { label: string; title: string; tone: "amber" | "blue" | "red" };

const TONE_CLASSES: Record<BadgeSpec["tone"], string> = {
  amber:
    "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200",
  blue:
    "border-blue-300 bg-blue-50 text-blue-800 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950/30 dark:text-blue-200",
  red:
    "border-red-300 bg-red-50 text-red-800 hover:bg-red-100 dark:border-red-800 dark:bg-red-950/30 dark:text-red-200",
};

export function ConnectivityBadge() {
  const { data } = useConnectivityStatus();

  if (!data || data.freshness === "receiving_now") return null;

  let spec: BadgeSpec;
  if (data.freshness === "never_received") {
    spec = data.state === "smoke_only"
      ? {
          label: "疎通確認のみ受信",
          title:
            "手動の疎通確認 trace のみ受信しています。実 workload からの trace はまだ届いていません。",
          tone: "blue",
        }
      : {
          label: "シグナル未受信 — 疎通確認が必要",
          title:
            "このシステムはまだ trace / signal を一度も受信していません。クリックで接続セットアップガイドを開きます。",
          tone: "amber",
        };
  } else if (data.freshness === "delayed") {
    spec = {
      label: "受信が途絶えています",
      title:
        "過去には受信していますが、直近の trace が届いていません。クリックで確認手順を開きます。",
      tone: "amber",
    };
  } else {
    spec = {
      label: "長時間受信なし",
      title:
        "長時間 trace が届いていません。health / 認証 / network / workload を確認してください。",
      tone: "red",
    };
  }

  return (
    <Link
      to="/setup-guide"
      data-testid="connectivity-badge"
      data-state={data.state}
      data-freshness={data.freshness}
      title={spec.title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${TONE_CLASSES[spec.tone]}`}
    >
      <RadioTower className="h-3.5 w-3.5" />
      {spec.label}
    </Link>
  );
}
