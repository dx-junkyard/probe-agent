// Issue #423 (Epic #418): the Blueprint's detail pane -- shown for a
// selected lane cell. Renders the server's own resolved refs; drill-down
// links NAVIGATE only, they never execute (§9.4/#358's rule, applied here
// one layer over from the Functional Lineage View this Epic's #424 builds).

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { BlueprintLaneCellOut } from "@/api/types";
import { BLUEPRINT_LANE_LABEL, LANE_STATE_LABEL } from "./model";

export function BlueprintDetailPane({
  stepKey,
  cell,
  onOpenRequirement,
}: {
  stepKey: string | null;
  cell: BlueprintLaneCellOut | null;
  onOpenRequirement?: (requirementKey: string) => void;
}) {
  if (!stepKey || !cell) {
    return (
      <Card data-testid="blueprint-detail-empty" data-help-id="journey-blueprint.detail_pane">
        <CardHeader>
          <CardTitle as="h2" className="text-base">
            詳細
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">セルを選択すると詳細が表示されます。</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="blueprint-detail-pane" data-help-id="journey-blueprint.detail_pane">
      <CardHeader>
        <CardTitle as="h2" className="text-base">
          {stepKey} / {BLUEPRINT_LANE_LABEL[cell.lane_kind]}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <Badge>{LANE_STATE_LABEL[cell.state]}</Badge>
        </div>
        {cell.summary ? <p className="text-muted-foreground">{cell.summary}</p> : null}

        {cell.stakeholder_links.length > 0 ? (
          <div>
            <p className="font-medium">Stakeholder</p>
            <ul className="mt-1 space-y-1">
              {cell.stakeholder_links.map((link) => (
                <li key={link.id} data-testid="blueprint-detail-stakeholder-link">
                  {link.stakeholder_name ?? link.stakeholder_key} ({link.role})
                  {link.recheck_state === "stale" ? (
                    <Badge variant="warning" className="ml-2">
                      要再確認
                    </Badge>
                  ) : null}
                  {link.target_resolution !== "resolved" ? (
                    <Badge variant="warning" className="ml-2">
                      参照先未解決
                    </Badge>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {cell.exchange_links.length > 0 ? (
          <div>
            <p className="font-medium">Value Exchange</p>
            <ul className="mt-1 space-y-1">
              {cell.exchange_links.map((link) => (
                <li key={link.id} data-testid="blueprint-detail-exchange-link">
                  {link.exchange_key}
                  {link.channel ? `(${link.channel})` : ""}
                  {link.recheck_state === "stale" ? (
                    <Badge variant="warning" className="ml-2">
                      要再確認
                    </Badge>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {cell.delivery_links.length > 0 ? (
          <div>
            <p className="font-medium">連携先</p>
            <ul className="mt-1 space-y-1">
              {cell.delivery_links.map((link) => (
                <li key={link.id} data-testid="blueprint-detail-delivery-link">
                  {link.target_kind === "not_applicable" ? (
                    "対象外として記録済み"
                  ) : (
                    <>
                      {link.target_kind}: {link.target_name ?? link.target_ref}
                      {link.target_resolution !== "resolved" ? (
                        <Badge variant="warning" className="ml-2">
                          参照先未解決
                        </Badge>
                      ) : null}
                      {link.recheck_state === "stale" ? (
                        <Badge variant="warning" className="ml-2">
                          要再確認
                        </Badge>
                      ) : null}
                      {link.target_kind === "ux_requirement" && onOpenRequirement ? (
                        <button
                          type="button"
                          className="ml-2 underline"
                          data-testid="blueprint-detail-open-requirement"
                          onClick={() => onOpenRequirement(link.target_ref)}
                        >
                          Requirement を開く
                        </button>
                      ) : null}
                    </>
                  )}
                  {link.implementation_refs.length > 0 ? (
                    <ul className="ml-4 mt-1 list-disc space-y-1">
                      {link.implementation_refs.map((ref, idx) => (
                        <li key={`${ref.design_key}-${idx}`} data-testid="blueprint-detail-implementation-ref">
                          {ref.design_key}
                          {ref.adopted_option_key ? ` / ${ref.adopted_option_key}` : "(未採用)"}
                          {ref.target_kind ? ` -> ${ref.target_kind}:${ref.target_ref}` : ""}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {cell.requirement_refs.length > 0 ? (
          <div>
            <p className="font-medium">Requirement</p>
            <ul className="mt-1 space-y-1">
              {cell.requirement_refs.map((ref) => (
                <li key={ref.requirement_key} data-testid="blueprint-detail-requirement-ref">
                  {ref.statement ?? ref.requirement_key}
                  {ref.target_resolution !== "resolved" ? (
                    <Badge variant="warning" className="ml-2">
                      参照先未解決
                    </Badge>
                  ) : null}
                  {onOpenRequirement ? (
                    <button
                      type="button"
                      className="ml-2 underline"
                      data-testid="blueprint-detail-open-requirement"
                      onClick={() => onOpenRequirement(ref.requirement_key)}
                    >
                      開く
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {cell.evidence_refs.length > 0 ? (
          <div>
            <p className="font-medium">観測済みエビデンス</p>
            <ul className="mt-1 space-y-1">
              {cell.evidence_refs.map((ev, idx) => (
                <li key={idx} data-testid="blueprint-detail-evidence-ref">
                  {ev.evidence_kind}: {ev.statement || "(本文なし)"}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
