import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useSystemDiagnostics } from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertTriangle, Ban, CheckCircle2, HelpCircle, ShieldAlert, XCircle,
} from "lucide-react";
import type { DiagnosticSeverity, SystemDiagnosticCheck } from "@/api/types";

const SEVERITY_ORDER: DiagnosticSeverity[] = ["error", "blocked", "warning", "unknown", "ok"];

const CATEGORY_LABELS: Record<string, string> = {
  repository: "Repository",
  database: "Database / Storage",
  auth: "Auth / System scope",
  llm: "LLM / Intelligence",
  pipeline: "System Understanding pipeline",
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

export function DiagnosticCheckCard({ check }: { check: SystemDiagnosticCheck }) {
  return (
    <div className="rounded-lg border p-3 space-y-2" data-testid="diagnostic-check">
      <div className="flex items-start gap-2">
        <DiagnosticSeverityIcon severity={check.severity} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">{check.title}</p>
          <div className="flex flex-wrap items-center gap-1.5 mt-1">
            <Badge variant={severityBadgeVariant(check.severity)} className="text-xs">
              {check.severity}
            </Badge>
            <Badge variant="outline" className="text-xs">
              {CATEGORY_LABELS[check.category] ?? check.category}
            </Badge>
          </div>
        </div>
      </div>
      <p className="text-xs text-muted-foreground pl-6">{check.detail}</p>
      {check.impact && (
        <p className="text-xs pl-6">
          <span className="font-medium">Impact:</span>{" "}
          <span className="text-muted-foreground">{check.impact}</span>
        </p>
      )}
      {check.remediation && (
        <p className="text-xs pl-6" data-testid="diagnostic-remediation">
          <span className="font-medium">Fix:</span>{" "}
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
            Last observed error ({check.last_observed_error.source}
            {check.last_observed_error.observed_at
              ? ` — ${new Date(check.last_observed_error.observed_at * 1000).toLocaleString()}`
              : ""})
          </p>
          <p className="text-[11px] font-mono whitespace-pre-wrap break-all text-muted-foreground">
            {check.last_observed_error.error ?? `status: ${check.last_observed_error.status}`}
          </p>
        </div>
      )}
      {check.related_pages.length > 0 && (
        <div className="pl-6 flex flex-wrap gap-2">
          {check.related_pages.map((page) => (
            <Link key={page} to={page} className="text-xs text-primary hover:underline">
              {page}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export function DiagnosticsDialogContent({ checks }: { checks: SystemDiagnosticCheck[] }) {
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
  return (
    <div className="space-y-4">
      {problems.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          All configuration checks passed. No required settings are missing or invalid.
        </p>
      ) : (
        <div className="space-y-2" data-testid="diagnostics-problems">
          {problems.map((c) => (
            <DiagnosticCheckCard key={c.check_id} check={c} />
          ))}
        </div>
      )}
      {healthy.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-1.5">
            Passing checks
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
        All checks are deterministic (no LLM). Runtime failures are shown from
        the most recent recorded run.
      </p>
    </div>
  );
}

export function DiagnosticsBadge() {
  const { data } = useSystemDiagnostics();
  const [open, setOpen] = useState(false);

  if (!data) return null;

  const errorCount =
    (data.severity_counts["error"] ?? 0) + (data.severity_counts["blocked"] ?? 0);
  const warningCount = data.severity_counts["warning"] ?? 0;
  const attention = errorCount + warningCount;

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
        <DiagnosticsDialogContent checks={data.checks} />
      </Dialog>
    </>
  );
}
