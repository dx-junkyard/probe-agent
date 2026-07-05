import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Code, Workflow, Target } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { PROVENANCE_LABEL, PROVENANCE_STYLE } from "@/components/api-role-card";
import type { CapabilityElementOut } from "@/api/types";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-muted-foreground shrink-0 w-28">{label}</dt>
      <dd className="break-words min-w-0">{children}</dd>
    </div>
  );
}

export function FunctionDetailCard({ element }: { element: CapabilityElementOut }) {
  const prov = element.provenance;
  const flowLink = prov.entrypoint_type && prov.entrypoint_ref
    ? `/flow-explorer?entrypoint_type=${encodeURIComponent(prov.entrypoint_type)}&entrypoint_id=${encodeURIComponent(prov.entrypoint_ref)}`
    : null;

  return (
    <Card data-testid="function-detail-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
          <Code className="h-4 w-4 shrink-0" />
          <span className="break-words font-mono">{element.name}</span>
          {element.classification && (
            <Badge variant="outline" className="text-[10px]">{element.classification}</Badge>
          )}
        </CardTitle>
        {element.summary && (
          <CardDescription className="text-xs break-words">{element.summary}</CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <dl className="space-y-1.5">
          {element.element_role && (
            <Field label="Role">{element.element_role}</Field>
          )}
          {element.operation_kind && (
            <Field label="Operation kind">{element.operation_kind}</Field>
          )}
          {element.probe_value && (
            <Field label="Probe value">{element.probe_value}</Field>
          )}
          {prov.path && (
            <Field label="Source">
              <span className="font-mono">
                {prov.path}
                {prov.start_line != null && prov.end_line != null &&
                  `:${prov.start_line}-${prov.end_line}`}
              </span>
            </Field>
          )}
          {prov.qualified_name && (
            <Field label="Symbol">
              <span className="font-mono">{prov.qualified_name}</span>
            </Field>
          )}
        </dl>

        <div className="flex flex-wrap items-center gap-1 pt-1">
          <span className="text-muted-foreground text-[11px]">Provenance:</span>
          <Badge variant="outline" className={`text-[10px] ${PROVENANCE_STYLE[prov.provenance_kind] ?? ""}`}>
            {PROVENANCE_LABEL[prov.provenance_kind] ?? prov.provenance_kind}
          </Badge>
        </div>

        <div className="flex gap-2 pt-1">
          {flowLink && (
            <Link
              to={flowLink}
              className={cn(buttonVariants({ size: "sm", variant: "outline" }), "h-7 text-[11px] gap-1")}
            >
              <Workflow className="h-3 w-3" /> Flow Explorer
            </Link>
          )}
          {prov.feature_id && (
            <Link
              to={`/feature-map?feature=${encodeURIComponent(prov.feature_id)}`}
              className={cn(buttonVariants({ size: "sm", variant: "outline" }), "h-7 text-[11px] gap-1")}
            >
              <Target className="h-3 w-3" /> Feature Map
            </Link>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
