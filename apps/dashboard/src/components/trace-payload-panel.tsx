import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { JsonTree } from "@/components/json-tree";
import type {
  TracePayloadShape,
  TracePayloadSummary,
  TraceRedaction,
  TraceRedactionRule,
} from "@/api/types";

// Issue #367: a trace's payload is shown behind a deliberate expand step, with
// its shape and redaction state readable first. The audited leak was a Trace
// detail that auto-expanded a JsonTree straight onto an API key -- so the
// default state here is closed, and what a reader sees before opening it is
// enough to decide whether opening it is worth doing.

const KIND_LABELS_JA: Record<string, string> = {
  none: "なし",
  boolean: "真偽値",
  string: "文字列",
  number: "数値",
  list: "配列",
  object: "オブジェクト",
  unknown: "不明",
};

// User-facing names for the finite rule vocabulary. Rule ids stay in their
// canonical form inside the detail list; these are the reading aids.
const RULE_LABELS_JA: Record<TraceRedactionRule, string> = {
  sensitive_key: "秘匿キー名",
  depth_limit: "深さ上限",
  pem_private_key: "PEM秘密鍵",
  aws_access_key_id: "AWS access key",
  github_fine_grained_pat: "GitHub PAT",
  github_token: "GitHub token",
  anthropic_api_key: "Anthropic API key",
  stripe_secret_key: "Stripe secret key",
  openai_api_key: "OpenAI API key",
  slack_token: "Slack token",
  google_api_key: "Google API key",
  jwt: "JWT",
  authorization_header: "Authorization header",
  url_userinfo: "URL内の認証情報",
  sensitive_assignment: "秘匿キーへの代入",
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function shapeText(shape: TracePayloadShape | undefined): string {
  if (!shape || shape.kind === "none") return "なし";
  const kind = KIND_LABELS_JA[shape.kind] ?? shape.kind;
  const count = shape.item_count != null ? ` · ${shape.item_count}件` : "";
  return `${kind}${count} · ${formatBytes(shape.bytes)}`;
}

/**
 * Redaction state as three distinct displays, never two.
 *
 * `null` (never scanned) is a real third state, not a synonym for "clean":
 * it marks a row stored before ingestion-time redaction existed, which is
 * exactly the population `POST /traces/redaction-rescan` is for. Collapsing
 * it into 「なし」 would tell the reader a row is verified when it is not.
 */
export function RedactionBadge({ redaction }: { redaction?: TraceRedaction | null }) {
  if (redaction == null) {
    return (
      <Badge variant="outline" title="このTraceは取り込み時のredaction導入前に保存されました。再スキャンで確認できます。">
        redaction未確認
      </Badge>
    );
  }
  if (!redaction.redacted) {
    return <Badge variant="secondary">秘匿値なし</Badge>;
  }
  return (
    <Badge variant="warning" title={redaction.rules.join(", ")}>
      redact済み {redaction.fields.length}件
    </Badge>
  );
}

interface TracePayloadPanelProps {
  input: unknown;
  output: string | null;
  error: string | null;
  summary?: TracePayloadSummary | null;
  redaction?: TraceRedaction | null;
  /** Whether the trace's replay capture is usable, for the redaction note. */
  replayAffected?: boolean;
}

export function TracePayloadPanel({
  input,
  output,
  error,
  summary,
  redaction,
  replayAffected = false,
}: TracePayloadPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const redactedFields = redaction?.redacted ? redaction.fields : [];

  return (
    <div className="space-y-3" data-testid="trace-payload-panel">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          入力: <span className="text-foreground">{shapeText(summary?.input)}</span>
        </span>
        <span>
          出力: <span className="text-foreground">{shapeText(summary?.output)}</span>
        </span>
        {error && (
          <span>
            エラー: <span className="text-destructive">{shapeText(summary?.error)}</span>
          </span>
        )}
        <RedactionBadge redaction={redaction} />
      </div>

      {redactedFields.length > 0 && (
        <div
          className="rounded-md border border-amber-500/40 bg-amber-50 p-2 text-xs dark:bg-amber-950/20"
          data-testid="trace-redaction-note"
        >
          <p className="font-medium">秘匿値をマスクして保存しています</p>
          <ul className="mt-1 space-y-0.5 text-muted-foreground">
            {redactedFields.slice(0, 6).map((f, i) => (
              <li key={`${f.field}-${f.path}-${i}`} className="font-mono">
                {f.field}
                {f.path && `.${f.path}`} — {RULE_LABELS_JA[f.rule] ?? f.rule}
              </li>
            ))}
            {redactedFields.length > 6 && (
              <li>ほか {redactedFields.length - 6} 件</li>
            )}
          </ul>
          {/* The two consequences are different and must not be merged: a
              masked *display* value is still a usable trace, a masked
              *capture* value is not restorable input. */}
          {replayAffected ? (
            <p className="mt-1 text-amber-700 dark:text-amber-400">
              マスクした値はReplayで復元できません。このTraceは入力の一部が欠けた状態で比較されます。
            </p>
          ) : (
            <p className="mt-1">
              表示上のマスクです。Replayに使う入力capture自体は影響を受けていません。
            </p>
          )}
        </div>
      )}

      <button
        type="button"
        className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs text-muted-foreground hover:bg-secondary cursor-pointer"
        aria-expanded={expanded}
        onClick={() => setExpanded(e => !e)}
      >
        <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        {expanded ? "入出力の中身を隠す" : "入出力の中身を表示"}
      </button>

      {expanded && (
        <div className="grid gap-4 lg:grid-cols-2" data-testid="trace-payload-body">
          <div className="min-w-0">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">入力</h3>
            <div className="max-h-64 overflow-auto rounded-md border bg-card p-3">
              <JsonTree data={input} defaultExpanded={false} />
            </div>
          </div>
          <div className="min-w-0">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">出力</h3>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-card p-3 font-mono text-xs">
              {output ?? "—"}
            </pre>
          </div>
        </div>
      )}

      {expanded && error && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-destructive">エラー</h3>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border border-destructive/40 bg-destructive/5 p-3 font-mono text-xs text-destructive">
            {error}
          </pre>
        </div>
      )}
    </div>
  );
}
