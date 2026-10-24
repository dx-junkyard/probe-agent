import { AlertTriangle, CheckCircle } from "lucide-react";
import { useRequestWaterfall } from "@/api/hooks";

/**
 * One request's traced calls on a shared time axis: each bar starts at the
 * call's start and is as long as it ran; nested calls are indented.
 * Status is shown with an icon and text, not colour alone.
 */
export function RequestWaterfall({ correlationId, traceId }: { correlationId?: string | null; traceId?: string | null }) {
  const { data, isLoading, error } = useRequestWaterfall({ correlationId, traceId });
  if (isLoading) return <p className="text-xs text-muted-foreground">リクエストの呼び出しを読み込んでいます…</p>;
  if (error || !data) {
    return (
      <p className="text-xs text-muted-foreground">
        このトレースはリクエストの文脈なしで記録されています（アプリ側で probe_context を張ると、同じリクエストの呼び出しを並べて表示できます）。
      </p>
    );
  }
  const total = Math.max(data.total_ms, 1);
  return (
    <div className="space-y-1 rounded-md border p-2" data-testid="request-waterfall">
      <p className="text-xs">
        <span className="font-semibold">リクエスト:</span> <span className="font-mono">{data.request ?? "（不明）"}</span>
        <span className="ml-2 text-muted-foreground">計 {data.total_ms.toFixed(0)} ms · 呼び出し {data.calls.length} 件</span>
      </p>
      <ul className="space-y-1">
        {data.calls.map((c) => (
          <li key={c.trace_id} className="text-xs" style={{ paddingLeft: `${c.depth * 12}px` }}>
            <div className="flex items-center gap-1">
              {c.ok ? <CheckCircle className="h-3 w-3 text-emerald-600" aria-hidden /> : <AlertTriangle className="h-3 w-3 text-red-600" aria-hidden />}
              <span className="font-mono font-semibold">{c.component_id}</span>
              <span className="text-muted-foreground">{c.ok ? "成功" : `エラー: ${c.error}`}</span>
              {c.code && <span className="font-mono text-muted-foreground">{c.code.path}:{c.code.line_start}</span>}
              <span className="ml-auto text-muted-foreground">+{c.offset_ms.toFixed(0)} ms · {c.duration_ms.toFixed(0)} ms</span>
            </div>
            <div className="relative h-2 rounded bg-muted" aria-hidden>
              <div
                className={`absolute h-2 rounded ${c.ok ? "bg-emerald-500" : "bg-red-500"}`}
                style={{ left: `${(c.offset_ms / total) * 100}%`, width: `${Math.max((c.duration_ms / total) * 100, 1)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
