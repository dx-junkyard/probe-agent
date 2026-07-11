import { useLatestSnapshot, useSymbols, useLatestDrafts } from "@/api/hooks";
import { CheckCircle2, Circle } from "lucide-react";
import { cn } from "@/lib/utils";

// Required generation order (#62 follow-up, reused across empty states per
// Issue #212): a snapshot must exist before symbols can be indexed, and a
// System Profile Draft (for the purpose node) is generated separately on the
// Repository page. Surfacing this as a checklist replaces a prose explanation
// that left the order implicit. Deterministic presence checks only — no
// heuristic inference.
export function PrerequisiteChecklist() {
  const { data: snapshot } = useLatestSnapshot();
  const { data: symbols } = useSymbols();
  const { data: drafts } = useLatestDrafts();

  const steps = [
    { label: "Snapshot created", done: !!snapshot },
    { label: "Symbols indexed", done: !!symbols && symbols.symbol_count > 0 },
    { label: "System Profile Draft generated", done: !!drafts?.system_profile_draft },
  ];

  return (
    <ul className="text-sm text-left max-w-xs mx-auto space-y-1.5" data-testid="prerequisite-checklist">
      {steps.map((step) => (
        <li key={step.label} className="flex items-center gap-2">
          {step.done ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
          ) : (
            <Circle className="h-4 w-4 text-muted-foreground shrink-0" />
          )}
          <span className={cn(!step.done && "text-muted-foreground")}>{step.label}</span>
        </li>
      ))}
    </ul>
  );
}
