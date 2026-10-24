import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle, CircleDashed, FileCode, Pause, Play, Plug, Radio, Tag } from "lucide-react";
import { useInstrumentationCoverage, useRecentTraces } from "@/api/hooks";
import type { InstrumentationPointOut, InstrumentationStatus, RecentTraceOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTimestamp } from "@/lib/utils";
import { RequestWaterfall } from "@/components/request-waterfall";

const LIVE_INTERVAL_MS = 3000;

// Status is never shown by colour alone: every badge carries an icon and a label.
const STATUS_META: Record<InstrumentationStatus, {
  label: string; hint: string; variant: "success" | "destructive" | "warning" | "outline"; Icon: typeof CheckCircle;
}> = {
  receiving: { label: "受信中", hint: "trace が届いています。", variant: "success", Icon: CheckCircle },
  errors: { label: "エラーあり", hint: "最新の trace がエラーでした。", variant: "destructive", Icon: AlertTriangle },
  not_received: {
    label: "未受信", hint: "計測は入っていますが、まだ一度も trace が届いていません。この処理を通る操作をアプリで行ってください。",
    variant: "warning", Icon: CircleDashed,
  },
  metadata_only: {
    label: "説明のみ", hint: "probe-agent: メタデータはありますが、component id が無いため trace と結び付きません。",
    variant: "outline", Icon: Tag,
  },
};

const KIND_LABEL = { probe: "@probe", wrapper: "ラッパー", metadata_only: "—" } as const;

function ago(ts: number | null): string {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)} 秒前`;
  if (s < 3600) return `${Math.round(s / 60)} 分前`;
  if (s < 86400) return `${Math.round(s / 3600)} 時間前`;
  return `${Math.round(s / 86400)} 日前`;
}

function StatusBadge({ status }: { status: InstrumentationStatus }) {
  const m = STATUS_META[status];
  return (
    <Badge variant={m.variant} title={m.hint} className="gap-1 whitespace-nowrap">
      <m.Icon className="h-3 w-3" aria-hidden />
      {m.label}
    </Badge>
  );
}

/** Rows whose trace count grew since the previous refresh, for the live highlight. */
function useFreshRows(points: InstrumentationPointOut[] | undefined): Set<string> {
  // Derived during render from the previous points (no effect), so a refresh
  // never renders once with a stale highlight before correcting itself.
  const [tracked, setTracked] = useState<{
    points: InstrumentationPointOut[] | undefined;
    counts: Map<string, number>;
    fresh: Set<string>;
  }>({ points: undefined, counts: new Map(), fresh: new Set() });
  if (points && points !== tracked.points) {
    const counts = new Map<string, number>();
    const fresh = new Set<string>();
    for (const p of points) {
      const key = `${p.path}:${p.symbol}`;
      const before = tracked.counts.get(key);
      if (before !== undefined && p.trace_count > before) fresh.add(key);
      counts.set(key, p.trace_count);
    }
    // Keep counts of rows absent from this refresh, as the ref-based version did.
    for (const [key, n] of tracked.counts) if (!counts.has(key)) counts.set(key, n);
    setTracked({ points, counts, fresh });
  }
  return tracked.fresh;
}

export default function InstrumentationCoveragePage() {
  const [live, setLive] = useState(false);
  const interval = live ? LIVE_INTERVAL_MS : false;
  const coverage = useInstrumentationCoverage(interval);
  const recent = useRecentTraces(interval);
  const [filter, setFilter] = useState<InstrumentationStatus | "all">("all");
  const fresh = useFreshRows(coverage.data?.points);

  if (coverage.isLoading) return <Skeleton className="h-64 w-full" />;
  if (coverage.error || !coverage.data) {
    return (
      <Card>
        <CardHeader><CardTitle>計測カバレッジ</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          シンボル索引がまだありません。<Link className="underline" to="/repository">Repository</Link> で snapshot とシンボル索引を作成してください。
        </CardContent>
      </Card>
    );
  }
  const data = coverage.data;
  const rows = data.points.filter((p) => filter === "all" || p.status === filter);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>計測カバレッジ</CardTitle>
            <CardDescription>
              コードに入れた計測点（@probe・宣言したラッパー・probe-agent: メタデータ）ごとに、trace が届いているかを示します。
              snapshot #{data.snapshot_id} のシンボル索引と、受信済みの trace を突き合わせています。
            </CardDescription>
          </div>
          <Button variant={live ? "default" : "outline"} size="sm" onClick={() => setLive(!live)} aria-pressed={live}>
            {live ? <Pause className="mr-1 h-4 w-4" /> : <Play className="mr-1 h-4 w-4" />}
            {live ? "ライブ表示を止める" : "ライブ表示"}
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {!data.sdk_connected && (
            <div role="status" className="flex items-center gap-2 rounded-md border border-dashed p-3 text-sm">
              <Plug className="h-4 w-4" aria-hidden />
              <span>まだ trace を一度も受信していません。アプリ側で SDK を接続し、計測を有効にしてください。</span>
              <Link className="ml-auto underline" to="/connect-sdk">Connect SDK</Link>
            </div>
          )}
          <div className="flex flex-wrap gap-2" role="group" aria-label="状態で絞り込む">
            <Button size="sm" variant={filter === "all" ? "default" : "outline"} onClick={() => setFilter("all")}>
              すべて {data.points.length}
            </Button>
            {(Object.keys(STATUS_META) as InstrumentationStatus[]).map((s) => (
              <Button key={s} size="sm" variant={filter === s ? "default" : "outline"} onClick={() => setFilter(s)} title={STATUS_META[s].hint}>
                {STATUS_META[s].label} {data.counts[s]}
              </Button>
            ))}
          </div>
          {data.last_trace_at && <p className="text-xs text-muted-foreground">最後に受信: {formatTimestamp(data.last_trace_at)}（{ago(data.last_trace_at)}）</p>}
        </CardContent>
      </Card>

      {live && <LiveFeed items={recent.data?.items ?? []} />}

      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="p-2">状態</th>
                <th className="p-2">計測点（コード）</th>
                <th className="p-2">component</th>
                <th className="p-2">入口</th>
                <th className="p-2">Feature</th>
                <th className="p-2 text-right">trace</th>
                <th className="p-2">最後に受信</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const key = `${p.path}:${p.symbol}`;
                return (
                  <tr key={key} className={`border-b align-top ${fresh.has(key) ? "bg-emerald-50 dark:bg-emerald-950" : ""}`}>
                    <td className="p-2">
                      <StatusBadge status={p.status} />
                      {fresh.has(key) && <span className="ml-1 text-xs"><Radio className="inline h-3 w-3" aria-hidden /> 今届いた</span>}
                    </td>
                    <td className="p-2">
                      <div className="font-mono text-xs font-semibold">{p.symbol}</div>
                      <div className="flex items-center gap-1 font-mono text-xs text-muted-foreground">
                        <FileCode className="h-3 w-3" aria-hidden />{p.path}:{p.line_start}
                      </div>
                      {p.role && <div className="mt-1 max-w-md text-xs text-muted-foreground">{p.role}</div>}
                    </td>
                    <td className="p-2 font-mono text-xs">
                      {p.component_id ? (
                        <Link className="underline" to={`/components?component=${encodeURIComponent(p.component_id)}`}>{p.component_id}</Link>
                      ) : "—"}
                      <div className="text-muted-foreground">{KIND_LABEL[p.instrumentation]}</div>
                    </td>
                    <td className="p-2 font-mono text-xs">
                      {p.route ?? (p.entry_flows?.length ? (
                        <>
                          <span className="font-sans text-muted-foreground">呼び出し元:</span>
                          {p.entry_flows.map((f) => <div key={f}>{f}</div>)}
                        </>
                      ) : "—")}
                    </td>
                    <td className="p-2 text-xs">{p.features.length ? p.features.join(", ") : "—"}</td>
                    <td className="p-2 text-right text-xs">
                      {p.trace_count}
                      {p.recent_errors > 0 && <div className="text-destructive">直近のエラー {p.recent_errors}</div>}
                      {p.avg_duration_ms != null && <div className="text-muted-foreground">平均 {p.avg_duration_ms.toFixed(1)} ms</div>}
                    </td>
                    <td className="p-2 text-xs">{ago(p.last_seen)}</td>
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr><td colSpan={7} className="p-4 text-center text-sm text-muted-foreground">該当する計測点はありません。</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {data.unmatched_components.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">コードに見つからない component</CardTitle>
            <CardDescription>trace は届いていますが、最新の snapshot に対応する計測点がありません（名前の変更・削除、または未コミットのコード）。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {data.unmatched_components.map((u) => (
              <Link key={u.component_id} to={`/components?component=${encodeURIComponent(u.component_id)}`}>
                <Badge variant="secondary">{u.component_id} · {u.trace_count} 件 · {ago(u.last_seen)}</Badge>
              </Link>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function LiveFeed({ items }: { items: RecentTraceOut[] }) {
  const [openRequest, setOpenRequest] = useState<string | null>(null);
  return (
    <Card aria-live="polite">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base"><Radio className="h-4 w-4" aria-hidden />ライブ表示（{LIVE_INTERVAL_MS / 1000} 秒ごとに更新）</CardTitle>
        <CardDescription>アプリを操作すると、その操作で動いた計測点が新しい順に流れます。入出力は受信時に秘匿処理された値の先頭だけを表示します。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {items.length === 0 && <p className="text-sm text-muted-foreground">まだ trace がありません。</p>}
        {items.slice(0, 15).map((t) => (
          <div key={t.trace_id} className="rounded-md border p-2 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={t.ok ? "success" : "destructive"} className="gap-1">
                {t.ok ? <CheckCircle className="h-3 w-3" aria-hidden /> : <AlertTriangle className="h-3 w-3" aria-hidden />}
                {t.ok ? "成功" : "エラー"}
              </Badge>
              <span className="font-mono font-semibold">{t.component_id}</span>
              {t.code && <span className="font-mono text-muted-foreground">{t.code.path}:{t.code.line_start}</span>}
              {t.code?.route && <span className="font-mono">{t.code.route}</span>}
              <span className="ml-auto text-muted-foreground">{t.duration_ms != null ? `${t.duration_ms.toFixed(1)} ms · ` : ""}{ago(t.timestamp)}</span>
            </div>
            {t.request && (
              <div className="mt-1 flex items-center gap-2">
                <span className="text-muted-foreground">リクエスト:</span>
                <span className="font-mono">{t.request}</span>
                {t.correlation_id && (
                  <button type="button" className="underline" aria-expanded={openRequest === t.correlation_id}
                    onClick={() => setOpenRequest(openRequest === t.correlation_id ? null : t.correlation_id!)}>
                    {openRequest === t.correlation_id ? "呼び出しを閉じる" : "このリクエストの呼び出し"}
                  </button>
                )}
              </div>
            )}
            {t.correlation_id && openRequest === t.correlation_id && <div className="mt-1"><RequestWaterfall correlationId={t.correlation_id} /></div>}
            {t.error && <div className="mt-1 text-destructive">{t.error}</div>}
            {t.input_preview && <div className="mt-1 truncate font-mono text-muted-foreground">入力: {t.input_preview}</div>}
            {t.output_preview && <div className="truncate font-mono text-muted-foreground">出力: {t.output_preview}</div>}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
