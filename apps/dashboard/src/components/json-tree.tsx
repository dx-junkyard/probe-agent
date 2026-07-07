import { useState } from "react";
import { cn } from "@/lib/utils";

// Collapsible JSON tree viewer (Issue #157). Objects and arrays can be folded
// so large analyzer results stay navigable; scalar leaves render inline. A
// `highlightKeys` set lets callers emphasise the fields of interest.

interface JsonTreeProps {
  data: unknown;
  name?: string;
  defaultExpanded?: boolean;
  depth?: number;
  highlightKeys?: Set<string>;
}

function isContainer(v: unknown): v is Record<string, unknown> | unknown[] {
  return v !== null && typeof v === "object";
}

function preview(v: unknown): string {
  if (Array.isArray(v)) return `[${v.length}]`;
  if (isContainer(v)) return `{${Object.keys(v as object).length}}`;
  return "";
}

function ScalarValue({ value }: { value: unknown }) {
  if (value === null) return <span className="text-muted-foreground">null</span>;
  if (typeof value === "string")
    return <span className="text-emerald-600 dark:text-emerald-400 break-all">"{value}"</span>;
  if (typeof value === "number")
    return <span className="text-blue-600 dark:text-blue-400">{value}</span>;
  if (typeof value === "boolean")
    return <span className="text-purple-600 dark:text-purple-400">{String(value)}</span>;
  return <span>{String(value)}</span>;
}

export function JsonTree({
  data,
  name,
  defaultExpanded = true,
  depth = 0,
  highlightKeys,
}: JsonTreeProps) {
  const [expanded, setExpanded] = useState(defaultExpanded || depth < 1);

  const label = name != null && (
    <span
      className={cn(
        "font-mono",
        highlightKeys?.has(name) ? "text-amber-600 dark:text-amber-400 font-semibold" : "text-foreground",
      )}
    >
      {name}
    </span>
  );

  if (!isContainer(data)) {
    return (
      <div className="font-mono text-xs leading-5">
        {label}
        {name != null && <span className="text-muted-foreground">: </span>}
        <ScalarValue value={data} />
      </div>
    );
  }

  const entries: [string, unknown][] = Array.isArray(data)
    ? data.map((v, i) => [String(i), v])
    : Object.entries(data as Record<string, unknown>);

  return (
    <div className="font-mono text-xs leading-5">
      <button
        type="button"
        className="inline-flex items-center gap-1 cursor-pointer hover:bg-secondary/40 rounded px-0.5"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <span className="text-muted-foreground w-3 inline-block">{expanded ? "▾" : "▸"}</span>
        {label}
        {name != null && <span className="text-muted-foreground">: </span>}
        <span className="text-muted-foreground">
          {Array.isArray(data) ? "array " : "object "}
          {preview(data)}
        </span>
      </button>
      {expanded && (
        <div className="pl-4 border-l border-border/60 ml-1.5">
          {entries.length === 0 ? (
            <div className="text-muted-foreground pl-1">empty</div>
          ) : (
            entries.map(([k, v]) => (
              <JsonTree
                key={k}
                name={k}
                data={v}
                depth={depth + 1}
                defaultExpanded={depth < 1}
                highlightKeys={highlightKeys}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}
