import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle, CircleDashed, Map as MapIcon, Radio, Tag } from "lucide-react";
import { useInstrumentationCoverage, useProductFeatures } from "@/api/hooks";
import type { InstrumentationPointOut, InstrumentationStatus } from "@/api/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const STATUS: Record<InstrumentationStatus, { label: string; Icon: typeof CheckCircle; cls: string }> = {
  receiving: { label: "受信中", Icon: CheckCircle, cls: "text-emerald-700 dark:text-emerald-400" },
  errors: { label: "エラーあり", Icon: AlertTriangle, cls: "text-red-600" },
  not_received: { label: "未受信", Icon: Radio, cls: "text-amber-700 dark:text-amber-400" },
  metadata_only: { label: "説明のみ", Icon: Tag, cls: "text-muted-foreground" },
};

function Point({ p }: { p: InstrumentationPointOut }) {
  const s = STATUS[p.status];
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs">
      <span className={`inline-flex items-center gap-0.5 ${s.cls}`}><s.Icon className="h-3 w-3" aria-hidden />{s.label}</span>
      {p.route && <span className="font-mono">{p.route}</span>}
      <span className="font-mono font-semibold">{p.symbol}</span>
      <span className="font-mono text-muted-foreground">{p.path}:{p.line_start}</span>
      {p.component_id && (
        <Link className="underline" to={`/components?component=${encodeURIComponent(p.component_id)}`}>{p.component_id}</Link>
      )}
      {p.trace_count > 0 && <span className="text-muted-foreground">{p.trace_count} trace</span>}
    </li>
  );
}

/**
 * One page from intent to runtime: each registered Feature, the code that
 * says it implements it (probe-agent: metadata naming the Feature id), the
 * entrypoint it sits behind, and whether its instrumentation reports.
 */
export default function SystemMapPage() {
  const features = useProductFeatures();
  const coverage = useInstrumentationCoverage();
  if (features.isLoading || coverage.isLoading) return <Skeleton className="h-64 w-full" />;

  const points = coverage.data?.points ?? [];
  const byFeature = new Map<string, InstrumentationPointOut[]>();
  for (const p of points) for (const f of p.features) byFeature.set(f, [...(byFeature.get(f) ?? []), p]);
  const unassigned = points.filter((p) => p.features.length === 0);
  const list = [...(features.data?.features ?? [])].sort((a, b) => a.feature_key.localeCompare(b.feature_key));
  const linked = list.filter((f) => byFeature.has(f.feature_key)).length;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><MapIcon className="h-5 w-5" aria-hidden />システムマップ</CardTitle>
          <CardDescription>
            登録した Feature ごとに、それを実装しているコード（probe-agent: メタデータに Feature の番号が書かれた関数）、
            その入口（API）、計測の受信状況を並べます。Feature とコードの対応はメタデータの記述から決定的に作っており、推測はしていません。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3 text-sm">
          <span>Feature {list.length} 件</span>
          <span>コードと対応 {linked} 件</span>
          <span>計測点 {points.length} 件</span>
          {coverage.data && !coverage.data.sdk_connected && (
            <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400">
              <CircleDashed className="h-4 w-4" aria-hidden />まだ trace を受信していません（<Link className="underline" to="/connect-sdk">Connect SDK</Link>）
            </span>
          )}
          <Link className="ml-auto underline" to="/instrumentation-coverage">計測カバレッジへ</Link>
        </CardContent>
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        {list.map((f) => {
          const ps = byFeature.get(f.feature_key) ?? [];
          return (
            <Card key={f.id}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  <span className="font-mono">{f.feature_key}</span> {f.title}
                </CardTitle>
                <CardDescription className="text-xs">
                  <Link className="underline" to="/feature-map">Feature Map で詳細</Link>
                </CardDescription>
              </CardHeader>
              <CardContent>
                {ps.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    この Feature 番号を書いた probe-agent: メタデータがコードにありません（未実装か、メタデータ未記入）。
                  </p>
                ) : (
                  <ul className="space-y-1">{ps.map((p) => <Point key={`${p.path}:${p.symbol}`} p={p} />)}</ul>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {unassigned.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Feature 番号のない計測点</CardTitle>
            <CardDescription className="text-xs">メタデータの capability などに Feature 番号（例: F-05）を書くと、上の Feature に並びます。</CardDescription>
          </CardHeader>
          <CardContent><ul className="space-y-1">{unassigned.map((p) => <Point key={`${p.path}:${p.symbol}`} p={p} />)}</ul></CardContent>
        </Card>
      )}
    </div>
  );
}
