import { useMemo, useState } from "react";
import { useSystemDiagnostics, useSystemState } from "@/api/hooks";
import { useNavigate } from "react-router-dom";
import { useDiagnosticActivate } from "@/components/diagnostic-fix";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertTriangle, Ban, CheckCircle2, ChevronRight, HelpCircle, ShieldAlert, XCircle,
} from "lucide-react";
import type { DiagnosticSeverity, SystemDiagnosticCheck } from "@/api/types";
import { systemStateTarget } from "@/components/system-state";

const SEVERITY_ORDER: DiagnosticSeverity[] = ["error", "blocked", "warning", "unknown", "ok"];

interface GroupedCheck {
  representative: SystemDiagnosticCheck;
  titles: string[];
  check_ids: string[];
}

function groupChecksByRootCause(checks: SystemDiagnosticCheck[]): GroupedCheck[] {
  const groups: GroupedCheck[] = [];
  const keyMap = new Map<string, number>();
  for (const c of checks) {
    const key = `${c.detail}\0${c.impact}\0${c.remediation}\0${c.fix_page ?? ""}\0${c.fix_anchor ?? ""}`;
    const idx = keyMap.get(key);
    if (idx !== undefined) {
      groups[idx].titles.push(c.title);
      groups[idx].check_ids.push(c.check_id);
    } else {
      keyMap.set(key, groups.length);
      groups.push({ representative: c, titles: [c.title], check_ids: [c.check_id] });
    }
  }
  return groups;
}

const CATEGORY_LABELS: Record<string, string> = {
  repository: "リポジトリ",
  database: "データベース / ストレージ",
  auth: "認証 / システムスコープ",
  llm: "LLM / Intelligence",
  pipeline: "System Understanding パイプライン",
  understanding: "System Purpose / 主な機能",
};

const SEVERITY_LABELS: Record<string, string> = {
  ok: "正常",
  warning: "警告",
  error: "エラー",
  blocked: "ブロック",
  unknown: "未確認",
};

export function DiagnosticSeverityIcon({ severity, className = "h-4 w-4" }: {
  severity: string;
  className?: string;
}) {
  switch (severity) {
    case "ok":
      return <CheckCircle2 className={`${className} text-green-600`} />;
    case "warning":
      return <AlertTriangle className={`${className} text-yellow-600`} />;
    case "error":
      return <XCircle className={`${className} text-red-600`} />;
    case "blocked":
      return <Ban className={`${className} text-orange-500`} />;
    default:
      return <HelpCircle className={`${className} text-muted-foreground`} />;
  }
}

function severityBadgeVariant(severity: string): "default" | "secondary" | "destructive" | "outline" {
  switch (severity) {
    case "error": return "destructive";
    case "blocked": return "destructive";
    case "warning": return "secondary";
    default: return "outline";
  }
}

export function DiagnosticCheckCard({ check, onActivate, groupedTitles }: {
  check: SystemDiagnosticCheck;
  onActivate?: (check: SystemDiagnosticCheck) => void;
  groupedTitles?: string[];
}) {
  const interactive = !!onActivate;
  const actionLabel =
    check.fix_kind === "navigate" ? "修正画面を開く" : "対処方法を表示";
  const titles = groupedTitles && groupedTitles.length > 1 ? groupedTitles : null;

  const body = (
    <>
      <div className="flex items-start gap-2">
        <DiagnosticSeverityIcon severity={check.severity} />
        <div className="flex-1 min-w-0">
          {titles ? (
            <>
              <p className="text-sm font-medium">{titles.length} 件のステップが同じ原因でブロックされています</p>
              <ul className="mt-1 pl-4 list-disc text-xs text-muted-foreground">
                {titles.map((t) => <li key={t}>{t}</li>)}
              </ul>
            </>
          ) : (
            <p className="text-sm font-medium">{check.title}</p>
          )}
          <div className="flex flex-wrap items-center gap-1.5 mt-1">
            <Badge variant={severityBadgeVariant(check.severity)} className="text-xs">
              {SEVERITY_LABELS[check.severity] ?? check.severity}
            </Badge>
            <Badge variant="outline" className="text-xs">
              {CATEGORY_LABELS[check.category] ?? check.category}
            </Badge>
          </div>
        </div>
        {interactive && (
          <span className="flex items-center gap-1 text-xs text-primary shrink-0" data-testid="diagnostic-fix-action">
            {actionLabel}
            <ChevronRight className="h-3.5 w-3.5" />
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground pl-6">
        <span className="font-medium text-foreground">原因: </span>{check.detail}
      </p>
      {check.impact && (
        <p className="text-xs pl-6">
          <span className="font-medium">影響:</span>{" "}
          <span className="text-muted-foreground">{check.impact}</span>
        </p>
      )}
      {check.remediation && (
        <p className="text-xs pl-6" data-testid="diagnostic-remediation">
          <span className="font-medium">対処:</span>{" "}
          <span className="text-muted-foreground">{check.remediation}</span>
        </p>
      )}
      {check.related_env.length > 0 && (
        <div className="pl-6 flex flex-wrap gap-1">
          {check.related_env.map((env) => (
            <code key={env} className="text-[11px] bg-muted px-1.5 py-0.5 rounded font-mono">
              {env}
            </code>
          ))}
        </div>
      )}
      {check.related_paths.length > 0 && (
        <div className="pl-6 space-y-0.5">
          {check.related_paths.map((p) => (
            <p key={p} className="text-[11px] font-mono text-muted-foreground break-all">{p}</p>
          ))}
        </div>
      )}
      {check.last_observed_error && (
        <div
          className="ml-6 rounded border border-destructive/40 bg-destructive/5 p-2 space-y-1"
          data-testid="diagnostic-last-error"
        >
          <p className="text-[11px] font-medium">
            直近のエラー ({check.last_observed_error.source}
            {check.last_observed_error.observed_at
              ? ` — ${new Date(check.last_observed_error.observed_at * 1000).toLocaleString()}`
              : ""})
          </p>
          <p className="text-[11px] font-mono whitespace-pre-wrap break-all text-muted-foreground">
            {check.last_observed_error.error ?? `status: ${check.last_observed_error.status}`}
          </p>
        </div>
      )}
    </>
  );

  if (interactive) {
    return (
      <div
        role="button"
        tabIndex={0}
        onClick={() => onActivate!(check)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onActivate!(check);
          }
        }}
        className="w-full text-left rounded-lg border p-3 space-y-2 cursor-pointer hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-testid="diagnostic-check"
      >
        {body}
      </div>
    );
  }

  return (
    <div className="rounded-lg border p-3 space-y-2" data-testid="diagnostic-check">
      {body}
    </div>
  );
}

export function EnvFixDialog({ check, onClose }: { check: SystemDiagnosticCheck | null; onClose: () => void }) {
  return (
    <Dialog open={!!check} onOpenChange={(o) => { if (!o) onClose(); }}>
      {check && (
        <>
          <DialogHeader>
            <DialogTitle>環境設定の対応が必要です</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm" data-testid="diagnostic-env-dialog">
            <div className="flex items-center gap-2">
              <DiagnosticSeverityIcon severity={check.severity} />
              <p className="font-medium">{check.title}</p>
            </div>
            <p className="text-muted-foreground">
              <span className="font-medium text-foreground">原因: </span>{check.detail}
            </p>
            {check.impact && (
              <p className="text-muted-foreground">
                <span className="font-medium text-foreground">影響: </span>{check.impact}
              </p>
            )}
            {check.remediation && (
              <p className="text-muted-foreground">
                <span className="font-medium text-foreground">対処: </span>{check.remediation}
              </p>
            )}
            {check.related_env.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs font-medium">設定が必要な環境変数</p>
                <div className="flex flex-wrap gap-1">
                  {check.related_env.map((env) => (
                    <code key={env} className="text-[11px] bg-muted px-1.5 py-0.5 rounded font-mono">
                      {env}
                    </code>
                  ))}
                </div>
              </div>
            )}
            <p className="text-xs text-muted-foreground rounded border bg-muted/40 p-2">
              この問題は画面上では修正できません。上記の環境変数を <code className="font-mono">.env</code>{" "}
              などで設定し、Control Server を再起動してから、該当する処理（snapshot 作成や
              System Understanding のビルドなど）を再実行してください。
            </p>
            {check.last_observed_error && (
              <div className="rounded border border-destructive/40 bg-destructive/5 p-2 space-y-1">
                <p className="text-[11px] font-medium">
                  直近のエラー ({check.last_observed_error.source})
                </p>
                <p className="text-[11px] font-mono whitespace-pre-wrap break-all text-muted-foreground">
                  {check.last_observed_error.error ?? `status: ${check.last_observed_error.status}`}
                </p>
              </div>
            )}
          </div>
        </>
      )}
    </Dialog>
  );
}

export function DiagnosticsDialogContent({ checks, onActivate }: {
  checks: SystemDiagnosticCheck[];
  onActivate?: (check: SystemDiagnosticCheck) => void;
}) {
  const sorted = useMemo(
    () =>
      [...checks].sort(
        (a, b) =>
          SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
      ),
    [checks],
  );
  const problems = sorted.filter((c) => c.severity !== "ok");
  const healthy = sorted.filter((c) => c.severity === "ok");
  const grouped = useMemo(() => groupChecksByRootCause(problems), [problems]);
  return (
    <div className="space-y-4">
      {problems.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          すべての設定チェックに合格しました。不足・不正な必須設定はありません。
        </p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            各項目をクリックすると、修正できる問題は該当画面へ移動します。画面で直接
            修正できない設定はダイアログで対応手順を表示します。
          </p>
          <div className="space-y-2" data-testid="diagnostics-problems">
            {grouped.map((g) => (
              <DiagnosticCheckCard
                key={g.check_ids.join(",")}
                check={g.representative}
                onActivate={onActivate}
                groupedTitles={g.titles}
              />
            ))}
          </div>
        </>
      )}
      {healthy.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1.5">
            正常なチェック
          </p>
          <ul className="space-y-1">
            {healthy.map((c) => (
              <li key={c.check_id} className="flex items-center gap-2 text-xs text-muted-foreground">
                <DiagnosticSeverityIcon severity="ok" className="h-3.5 w-3.5" />
                {c.title}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        すべてのチェックは決定的（LLM 不使用）です。実行時の失敗は直近の実行記録から
        そのまま表示しています。
      </p>
    </div>
  );
}

export function DiagnosticsBadge() {
  const { data } = useSystemDiagnostics();
  const { data: systemState } = useSystemState();
  const [open, setOpen] = useState(false);
  const { activate, envCheck, closeEnv } = useDiagnosticActivate();
  const navigate = useNavigate();

  // system-state is the canonical projection. Keep the legacy endpoint as a
  // fallback while older control servers are being rolled out.
  if (systemState) {
    const items = Array.from(
      new Map(
        systemState.items
          .filter((item) => item.severity !== "ok")
          .map((item) => [item.dedupe_key || item.state_id, item]),
      ).values(),
    );
    const errorCount = items.filter((item) => item.severity === "error" || item.severity === "blocked").length;
    const warningCount = items.filter((item) => item.severity === "warning").length;
    const attention = items.length;

    return (
      <>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOpen(true)}
          title="System state"
          data-testid="diagnostics-badge"
          className="relative gap-1.5"
        >
          <ShieldAlert className={`h-4 w-4 ${errorCount > 0 ? "text-red-600" : warningCount > 0 ? "text-yellow-600" : "text-muted-foreground"}`} />
          {attention > 0 && <span data-testid="diagnostics-badge-count" className={`text-xs font-semibold ${errorCount > 0 ? "text-red-600" : "text-yellow-600"}`}>{attention}</span>}
        </Button>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogHeader><DialogTitle>System State</DialogTitle></DialogHeader>
          <div className="space-y-3">
            {items.length === 0 ? <p className="text-sm text-muted-foreground">対応が必要な状態はありません。</p> : items.map((item) => {
              // Items projected from system_diagnostics whose underlying check has
              // fix_kind "dialog" (env-var fixes) carry target_ui === null because
              // those checks have no fix_page. Fall back to the diagnostic check
              // (via related_checks[0]) so those items still get an action that
              // opens the env fix dialog, matching the legacy badge's behavior.
              const relatedCheck =
                !item.target_ui && item.source === "system_diagnostics"
                  ? data?.checks.find((c) => c.check_id === item.related_checks[0])
                  : undefined;
              return (
                <div key={item.dedupe_key || item.state_id} className="flex items-start justify-between gap-3 rounded-lg border p-3" data-testid={`system-state-item-${item.state_id}`}>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2"><DiagnosticSeverityIcon severity={item.severity} /><p className="text-sm font-medium">{item.summary}</p></div>
                    {item.remediation && <p className="mt-1 text-xs text-muted-foreground">{item.remediation}</p>}
                  </div>
                  {item.target_ui && <Button size="sm" onClick={() => {
                    const target = systemStateTarget(item);
                    setOpen(false);
                    if (target) navigate(target);
                  }}>{item.target_ui.action_label || "対応する"}</Button>}
                  {!item.target_ui && relatedCheck && <Button size="sm" onClick={() => {
                    setOpen(false);
                    activate(relatedCheck);
                  }}>修正する</Button>}
                </div>
              );
            })}
          </div>
        </Dialog>
        <EnvFixDialog check={envCheck} onClose={closeEnv} />
      </>
    );
  }

  if (!data) return null;

  const errorCount =
    (data.severity_counts["error"] ?? 0) + (data.severity_counts["blocked"] ?? 0);
  const warningCount = data.severity_counts["warning"] ?? 0;
  const attention = errorCount + warningCount;

  // Close the list dialog before routing or opening the env dialog so the two
  // modals never stack.
  const handleActivate = (check: SystemDiagnosticCheck) => {
    setOpen(false);
    activate(check);
  };

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen(true)}
        title="System settings diagnostics"
        data-testid="diagnostics-badge"
        className="relative gap-1.5"
      >
        <ShieldAlert
          className={`h-4 w-4 ${
            errorCount > 0
              ? "text-red-600"
              : warningCount > 0
                ? "text-yellow-600"
                : "text-muted-foreground"
          }`}
        />
        {attention > 0 && (
          <span
            data-testid="diagnostics-badge-count"
            className={`text-xs font-semibold ${
              errorCount > 0 ? "text-red-600" : "text-yellow-600"
            }`}
          >
            {attention}
          </span>
        )}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogHeader>
          <DialogTitle>System Settings Diagnostics</DialogTitle>
        </DialogHeader>
        <DiagnosticsDialogContent checks={data.checks} onActivate={handleActivate} />
      </Dialog>
      <EnvFixDialog check={envCheck} onClose={closeEnv} />
    </>
  );
}
