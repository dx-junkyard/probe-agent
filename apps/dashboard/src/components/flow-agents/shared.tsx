// Epic #412 (#414 / #415): the small display primitives the Flow・エージェント群
// screen is built from.
//
// They exist so the rules that must never be broken are enforced in ONE place
// rather than re-typed per panel:
//
//   * a `FlowFactState` always renders its own sentence (six sentences, never
//     rounded together — §6.4);
//   * colour is never the only marker — every tone is paired with its text
//     label (#414 accessibility criteria);
//   * a degraded section drops its display and SAYS SO, and never substitutes
//     a guessed value (§6.4 / #380);
//   * a human decision is never presented as automatic: `ConfirmActionDialog`
//     always requires an explicit confirmation and, where the API requires
//     one, a reason (§10).

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { FlowFactState } from "@/api/types";
import {
  FACT_STATE_DESCRIPTION,
  FACT_STATE_LABEL,
  FACT_STATE_TONE,
  type SectionAvailability,
} from "./model";

type Tone = "success" | "muted" | "destructive" | "warning" | "neutral";

const TONE_VARIANT: Record<Tone, "success" | "outline" | "destructive" | "warning" | "secondary"> = {
  success: "success",
  muted: "outline",
  destructive: "destructive",
  warning: "warning",
  neutral: "secondary",
};

/** A status badge. `text` is mandatory: the colour never carries the meaning
 * on its own. */
export function StatusBadge({
  tone,
  text,
  title,
}: {
  tone: Tone;
  text: string;
  title?: string;
}) {
  return (
    <Badge variant={TONE_VARIANT[tone]} title={title}>
      {text}
    </Badge>
  );
}

/** One of §6.4's six answers, always as its own sentence. */
export function FactStateBadge({ state }: { state: FlowFactState }) {
  return (
    <StatusBadge
      tone={FACT_STATE_TONE[state]}
      text={`${state} / ${FACT_STATE_LABEL[state]}`}
      title={FACT_STATE_DESCRIPTION[state]}
    />
  );
}

/** A value plus the reason it is missing, kept apart.
 *
 * When `state` is not `present` the VALUE is not shown at all — an unreadable
 * or unmeasured fact must never be rendered as a default value (#380). */
export function FactValue({
  state,
  value,
  emptyHint,
}: {
  state: FlowFactState;
  value: ReactNode;
  emptyHint?: string;
}) {
  if (state !== "present") {
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        <FactStateBadge state={state} />
        <span className="text-xs text-muted-foreground">
          {emptyHint ?? FACT_STATE_DESCRIPTION[state]}
        </span>
      </span>
    );
  }
  if (value === null || value === undefined || value === "") {
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        <FactStateBadge state="missing" />
        <span className="text-xs text-muted-foreground">
          {FACT_STATE_DESCRIPTION.missing}
        </span>
      </span>
    );
  }
  return <span className="text-sm">{value}</span>;
}

/** A projection section with its own read failure.
 *
 * A degraded section drops its content and states that it could not be read;
 * every other section still renders. One failed section never blanks the
 * page (§6.4). */
export function SectionCard({
  availability,
  description,
  headingLevel = "h3",
  children,
  actions,
  id,
}: {
  availability: SectionAvailability;
  description?: ReactNode;
  headingLevel?: "h2" | "h3";
  children: ReactNode;
  actions?: ReactNode;
  id?: string;
}) {
  return (
    <Card id={id}>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <CardTitle as={headingLevel} className="text-base">
            {availability.label}
          </CardTitle>
          {actions}
        </div>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>
        {availability.available ? (
          children
        ) : (
          <div className="rounded border border-destructive p-3 text-sm">
            <div className="font-medium text-destructive">
              このセクションは取得できませんでした
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              表示を落としています。推測値は入れません。「該当なし」ではありません。
            </p>
            {availability.detail && (
              <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                {availability.detail}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** An empty list. Deliberately NOT the same component as the degraded notice:
 * 「0 件」 and 「取得できませんでした」 are different sentences (#356). */
export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

export function ReadFailure({
  what,
  onRetry,
}: {
  what: string;
  onRetry?: () => void;
}) {
  return (
    <p className="text-sm text-destructive">
      {what}を取得できませんでした。
      {onRetry && (
        <Button variant="link" size="sm" onClick={onRetry}>
          再試行
        </Button>
      )}
    </p>
  );
}

/** The server's own finite refusal code, rendered verbatim.
 *
 * The screen holds no copy of the capability gate or the §7.4 transition
 * gate, so it can neither paraphrase a refusal nor pre-empt one. */
export function RefusalNotice({
  code,
  message,
  hint,
}: {
  code: string;
  message: string;
  hint?: string;
}) {
  return (
    <div className="rounded border border-destructive p-3 text-sm" role="alert">
      <div className="font-mono text-xs text-destructive">{code}</div>
      <div className="mt-1">{message}</div>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** A human decision, never an automatic one.
 *
 * Every mode assignment / revocation and every proposal approval, rejection
 * and withdrawal is `decision_method: manual` on the server (§10). The dialog
 * makes that explicit on the client too: the developer confirms in a separate
 * step, and where the API requires a reason the confirm button stays disabled
 * until one is written. */
interface ConfirmActionDialogProps {
  open: boolean;
  title: string;
  summary: ReactNode;
  reasonRequired: boolean;
  reasonLabel?: string;
  reasonHint?: string;
  confirmLabel: string;
  pending: boolean;
  onConfirm: (reason: string) => void;
  onOpenChange: (open: boolean) => void;
  children?: ReactNode;
}

/** Mounted only while open, so the reason field starts empty on every open
 * without an effect resetting it -- a previous decision's wording must never
 * be carried into the next one. */
export function ConfirmActionDialog(props: ConfirmActionDialogProps) {
  if (!props.open) return null;
  return <ConfirmActionDialogBody {...props} />;
}

function ConfirmActionDialogBody({
  open,
  title,
  summary,
  reasonRequired,
  reasonLabel = "理由",
  reasonHint,
  confirmLabel,
  pending,
  onConfirm,
  onOpenChange,
  children,
}: ConfirmActionDialogProps) {
  const [reason, setReason] = useState("");
  const reasonId = useId();
  const firstFieldRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // Keyboard reachability: focus lands inside the dialog when it opens.
    const timer = window.setTimeout(() => firstFieldRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onOpenChange(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onOpenChange]);

  const blocked = reasonRequired && reason.trim().length === 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
      </DialogHeader>
      <div className="space-y-3 text-sm">
        <div>{summary}</div>
        {children}
        <div className="space-y-1">
          <Label htmlFor={reasonId}>
            {reasonLabel}
            {reasonRequired ? "（必須）" : "（任意）"}
          </Label>
          <Textarea
            id={reasonId}
            ref={firstFieldRef}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
          />
          <p className="text-xs text-muted-foreground">
            {reasonHint ??
              "この操作は人の判断（decision_method: manual）として追記のみの監査に記録されます。実行者は認証されたユーザー名です。"}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            やめる
          </Button>
          <Button disabled={blocked || pending} onClick={() => onConfirm(reason.trim())}>
            {pending ? "記録中…" : confirmLabel}
          </Button>
        </div>
        {blocked && (
          <p className="text-xs text-destructive">
            理由を入力するまで記録できません。理由の無い判断は後から検証できないためです。
          </p>
        )}
      </div>
    </Dialog>
  );
}
