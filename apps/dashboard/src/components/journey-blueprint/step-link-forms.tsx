// UX audit 2026-11-17 C1: associate a Journey Step with existing
// Stakeholders, Value Exchanges and delivery targets
// (`docs/01-specifications/product/stakeholder-value-network.md` §8.2).
//
// Every target is CHOSEN from rows that already exist -- never typed as a
// key -- so a typo can never be saved as an unresolved reference. Each link
// is a new append-only row recorded as `decision_method: manual`; the actor
// comes from the Principal on the server. The lane states the Blueprint then
// shows are the server's, re-read after the write; nothing is derived here.
//
// The caller remounts this component per `(journey, step, lane)`, so a
// half-filled form for one Step can never be submitted against another.

import { useState, type FormEvent } from "react";
import { getSystemId } from "@/api/client";
import {
  useAddJourneyStepDeliveryLink, useAddJourneyStepExchangeLink, useAddJourneyStepStakeholderLink,
  useUxRequirements, useValueNetwork,
} from "@/api/hooks";
import type {
  BlueprintDeliveryTargetKind, BlueprintLaneKind, BlueprintStakeholderRole, JourneyDeliveryKind,
} from "@/api/types";
import { QueryErrorState } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useUnsavedChangesGuard } from "@/lib/ui-draft";
import { WriteError } from "@/components/stakeholder-network/editors";
import {
  DESIGN_STATUS_LABEL, EXCHANGE_KIND_LABEL, STAKEHOLDER_ROLE_LABEL,
} from "@/components/stakeholder-network/model";

const ROLE_VALUES: BlueprintStakeholderRole[] = [
  "actor", "beneficiary", "payer", "operator", "approver", "supplier", "regulator", "observer",
];

const DELIVERY_KIND_VALUES: JourneyDeliveryKind[] = ["frontstage", "backstage", "support", "external"];

const DELIVERY_KIND_LABEL: Record<JourneyDeliveryKind, string> = {
  frontstage: "フロントステージ",
  backstage: "バックステージ",
  support: "サポート業務",
  external: "外部連携",
};

const DELIVERY_TARGET_KIND_LABEL: Record<BlueprintDeliveryTargetKind, string> = {
  ux_requirement: "Requirement",
  stakeholder: "Stakeholder",
  value_exchange: "Value Exchange",
  not_applicable: "このレーンは対象外として記録する",
};

function isDeliveryKind(lane: BlueprintLaneKind): lane is JourneyDeliveryKind {
  return (DELIVERY_KIND_VALUES as string[]).includes(lane);
}

function guardId(...parts: string[]): string {
  return ["journey-blueprint-link", String(getSystemId() ?? ""), ...parts].join(":");
}

function SubmitRow({ pending, disabled, label }: { pending: boolean; disabled: boolean; label: string }) {
  return (
    <Button type="submit" size="sm" disabled={pending || disabled}>
      {pending ? "保存しています…" : label}
    </Button>
  );
}

function StakeholderLinkForm({ journeyKey, stepKey }: { journeyKey: string; stepKey: string }) {
  const network = useValueNetwork();
  const add = useAddJourneyStepStakeholderLink();
  const [stakeholderKey, setStakeholderKey] = useState("");
  const [role, setRole] = useState<BlueprintStakeholderRole>("actor");
  const [note, setNote] = useState("");
  useUnsavedChangesGuard(guardId("stakeholder", journeyKey, stepKey), stakeholderKey !== "" || note.trim() !== "");
  const nodes = network.data?.nodes ?? [];

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!stakeholderKey) return;
    add.mutate(
      { journey_key: journeyKey, step_key: stepKey, stakeholder_key: stakeholderKey, role, note: note.trim() },
      { onSuccess: () => { setStakeholderKey(""); setNote(""); } },
    );
  }

  return (
    <form onSubmit={submit} className="space-y-2" data-testid="blueprint-link-stakeholder-form">
      <h4 className="text-sm font-semibold">Stakeholder を関連付ける</h4>
      {network.isError && !network.data ? (
        <QueryErrorState target="Stakeholder 一覧" error={network.error} onRetry={() => { void network.refetch(); }} retrying={network.isFetching} />
      ) : nodes.length === 0 && !network.isLoading ? (
        <p className="text-xs text-muted-foreground">
          Stakeholder がまだありません。Stakeholder Value Network 画面で追加してください。
        </p>
      ) : (
        <fieldset disabled={add.isPending} className="grid gap-2 sm:grid-cols-2">
          <FormField label="Stakeholder" required>
            {(field) => (
              <Select {...field} value={stakeholderKey} onChange={(e) => setStakeholderKey(e.target.value)}>
                <option value="">(選択してください)</option>
                {nodes.map((n) => (
                  <option key={n.stakeholder_key} value={n.stakeholder_key}>
                    {n.display_name || n.stakeholder_key}({DESIGN_STATUS_LABEL[n.design_status]})
                  </option>
                ))}
              </Select>
            )}
          </FormField>
          <FormField label="この Step での役割">
            {(field) => (
              <Select {...field} value={role} onChange={(e) => setRole(e.target.value as BlueprintStakeholderRole)}>
                {ROLE_VALUES.map((r) => <option key={r} value={r}>{STAKEHOLDER_ROLE_LABEL[r]}</option>)}
              </Select>
            )}
          </FormField>
          <FormField label="メモ(任意)" className="sm:col-span-2">
            {(field) => <Input {...field} value={note} onChange={(e) => setNote(e.target.value)} />}
          </FormField>
        </fieldset>
      )}
      <WriteError error={add.error} testId="blueprint-link-stakeholder-error" />
      <SubmitRow pending={add.isPending} disabled={!stakeholderKey} label="関連付ける" />
    </form>
  );
}

function ExchangeLinkForm({ journeyKey, stepKey }: { journeyKey: string; stepKey: string }) {
  const network = useValueNetwork();
  const add = useAddJourneyStepExchangeLink();
  const [exchangeKey, setExchangeKey] = useState("");
  const [note, setNote] = useState("");
  useUnsavedChangesGuard(guardId("exchange", journeyKey, stepKey), exchangeKey !== "" || note.trim() !== "");
  const edges = network.data?.edges ?? [];

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!exchangeKey) return;
    add.mutate(
      { journey_key: journeyKey, step_key: stepKey, exchange_key: exchangeKey, note: note.trim() },
      { onSuccess: () => { setExchangeKey(""); setNote(""); } },
    );
  }

  return (
    <form onSubmit={submit} className="space-y-2" data-testid="blueprint-link-exchange-form">
      <h4 className="text-sm font-semibold">Value Exchange を関連付ける</h4>
      {network.isError && !network.data ? (
        <QueryErrorState target="Value Exchange 一覧" error={network.error} onRetry={() => { void network.refetch(); }} retrying={network.isFetching} />
      ) : edges.length === 0 && !network.isLoading ? (
        <p className="text-xs text-muted-foreground">
          Value Exchange がまだありません。Stakeholder Value Network 画面で追加してください。
        </p>
      ) : (
        <fieldset disabled={add.isPending} className="space-y-2">
          <FormField label="Value Exchange" required>
            {(field) => (
              <Select {...field} value={exchangeKey} onChange={(e) => setExchangeKey(e.target.value)}>
                <option value="">(選択してください)</option>
                {edges.map((x) => (
                  <option key={x.exchange_key} value={x.exchange_key}>
                    {x.provider_stakeholder_key} → {x.receiver_stakeholder_key}:
                    {x.exchange_kind ? EXCHANGE_KIND_LABEL[x.exchange_kind] : "不明"}({x.exchange_key})
                  </option>
                ))}
              </Select>
            )}
          </FormField>
          <FormField label="メモ(任意)">
            {(field) => <Input {...field} value={note} onChange={(e) => setNote(e.target.value)} />}
          </FormField>
        </fieldset>
      )}
      <WriteError error={add.error} testId="blueprint-link-exchange-error" />
      <SubmitRow pending={add.isPending} disabled={!exchangeKey} label="関連付ける" />
    </form>
  );
}

function DeliveryLinkForm({
  journeyKey,
  stepKey,
  defaultDeliveryKind,
}: {
  journeyKey: string;
  stepKey: string;
  defaultDeliveryKind: JourneyDeliveryKind;
}) {
  const network = useValueNetwork();
  const requirements = useUxRequirements();
  const add = useAddJourneyStepDeliveryLink();
  const [deliveryKind, setDeliveryKind] = useState<JourneyDeliveryKind>(defaultDeliveryKind);
  const [targetKind, setTargetKind] = useState<BlueprintDeliveryTargetKind>("ux_requirement");
  const [targetRef, setTargetRef] = useState("");
  const [note, setNote] = useState("");
  useUnsavedChangesGuard(guardId("delivery", journeyKey, stepKey), targetRef !== "" || note.trim() !== "");

  const options: { value: string; label: string }[] =
    targetKind === "stakeholder"
      ? (network.data?.nodes ?? []).map((n) => ({ value: n.stakeholder_key, label: n.display_name || n.stakeholder_key }))
      : targetKind === "value_exchange"
        ? (network.data?.edges ?? []).map((x) => ({
          value: x.exchange_key,
          label: `${x.provider_stakeholder_key} → ${x.receiver_stakeholder_key}(${x.exchange_key})`,
        }))
        : targetKind === "ux_requirement"
          ? (requirements.data?.requirements ?? []).map((r) => ({
            value: r.requirement_key,
            label: r.statement ? `${r.statement}(${r.requirement_key})` : r.requirement_key,
          }))
          : [];
  const needsTarget = targetKind !== "not_applicable";
  const sourceFailed = targetKind === "ux_requirement"
    ? requirements.isError && !requirements.data
    : needsTarget && network.isError && !network.data;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (needsTarget && !targetRef) return;
    add.mutate(
      {
        journey_key: journeyKey, step_key: stepKey, delivery_kind: deliveryKind, target_kind: targetKind,
        target_ref: needsTarget ? targetRef : "", note: note.trim(),
      },
      { onSuccess: () => { setTargetRef(""); setNote(""); } },
    );
  }

  return (
    <form onSubmit={submit} className="space-y-2" data-testid="blueprint-link-delivery-form">
      <h4 className="text-sm font-semibold">提供の担い手を関連付ける</h4>
      <fieldset disabled={add.isPending} className="grid gap-2 sm:grid-cols-2">
        <FormField label="レーン">
          {(field) => (
            <Select {...field} value={deliveryKind} onChange={(e) => setDeliveryKind(e.target.value as JourneyDeliveryKind)}>
              {DELIVERY_KIND_VALUES.map((k) => <option key={k} value={k}>{DELIVERY_KIND_LABEL[k]}</option>)}
            </Select>
          )}
        </FormField>
        <FormField label="関連付け先の種類">
          {(field) => (
            <Select
              {...field}
              value={targetKind}
              onChange={(e) => { setTargetKind(e.target.value as BlueprintDeliveryTargetKind); setTargetRef(""); }}
            >
              {(Object.keys(DELIVERY_TARGET_KIND_LABEL) as BlueprintDeliveryTargetKind[]).map((k) => (
                <option key={k} value={k}>{DELIVERY_TARGET_KIND_LABEL[k]}</option>
              ))}
            </Select>
          )}
        </FormField>
        {needsTarget && (
          <FormField label="関連付け先" required className="sm:col-span-2">
            {(field) => (
              <Select {...field} value={targetRef} onChange={(e) => setTargetRef(e.target.value)}>
                <option value="">(選択してください)</option>
                {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
            )}
          </FormField>
        )}
        <FormField label="メモ(任意)" className="sm:col-span-2">
          {(field) => <Input {...field} value={note} onChange={(e) => setNote(e.target.value)} />}
        </FormField>
      </fieldset>
      {sourceFailed && (
        <p className="text-xs text-destructive" role="alert">関連付け先の一覧を取得できませんでした。画面を再読み込みしてください。</p>
      )}
      <WriteError error={add.error} testId="blueprint-link-delivery-error" />
      <SubmitRow pending={add.isPending} disabled={needsTarget && !targetRef} label="関連付ける" />
    </form>
  );
}

/** The forms that fit the selected lane come first; the others stay
 * reachable behind a disclosure so any Step can be fully linked from here. */
export function StepLinkForms({
  journeyKey,
  stepKey,
  laneKind,
}: {
  journeyKey: string;
  stepKey: string;
  laneKind: BlueprintLaneKind;
}) {
  const primary =
    laneKind === "stakeholder_action" ? "stakeholder"
      : laneKind === "touchpoint" ? "exchange"
        : isDeliveryKind(laneKind) ? "delivery"
          : null;
  const forms = {
    stakeholder: <StakeholderLinkForm journeyKey={journeyKey} stepKey={stepKey} />,
    exchange: <ExchangeLinkForm journeyKey={journeyKey} stepKey={stepKey} />,
    delivery: (
      <DeliveryLinkForm
        journeyKey={journeyKey}
        stepKey={stepKey}
        defaultDeliveryKind={isDeliveryKind(laneKind) ? laneKind : "frontstage"}
      />
    ),
  } as const;
  const others = (Object.keys(forms) as (keyof typeof forms)[]).filter((k) => k !== primary);
  return (
    <div className="space-y-3 border-t pt-3" data-testid="blueprint-step-link-forms">
      {primary && forms[primary]}
      <details className="rounded border p-2" open={primary === null}>
        <summary className="cursor-pointer text-sm">この Step への他の関連付け</summary>
        <div className="mt-2 space-y-4">
          {others.map((k) => <div key={k}>{forms[k]}</div>)}
        </div>
      </details>
    </div>
  );
}
