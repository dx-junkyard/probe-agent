// UX audit 2026-11-17 C2 / D8: every human gate on the Objective Map and the
// Requirement -> Feature surface (Objective / Milestone / Gap / Feature
// decisions, the Milestone achievement assessment, the Gap resolution choice
// and priority band, the Objective parent detach) goes through ONE explicit
// confirmation, and a refusal is shown in Japanese.
//
// The dialog says WHAT is being decided, ABOUT WHICH entity, FROM WHICH
// current state (the server's own value, never re-derived), and that it is
// recorded as a human decision. It does not decide whether the transition is
// legal: there is no client-side transition table. A transition the server
// refuses comes back as its own finite code and is rendered by
// `RejectedNotice` with the current state alongside, so the developer can see
// why it was refused.

import { useId, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ApiError } from "@/api/client";

export type Rejection = { code: string; message: string };

/** The server already returns a Japanese `message` for every coded refusal
 * (`routes/product_*.py`'s `_MESSAGES`). Uncoded ones (404, a plain
 * validation 422) may carry an English detail, so those get a Japanese
 * sentence of their own; `context` (e.g. the current state) is appended to a
 * `*_not_decidable` refusal so 「この状態からは記録できません」 names the state. */
export function rejectionFromError(error: unknown, fallback: string, context?: string): Rejection {
  const apiError = error as ApiError | undefined;
  const code = apiError?.code ?? "";
  if (code) {
    const suffix = context && code.endsWith("_not_decidable") ? `(${context})` : "";
    return { code, message: `${apiError?.detail || fallback}${suffix}` };
  }
  if (apiError?.status === 404) {
    return { code: "not_found", message: "対象が見つかりませんでした。画面を読み込み直してから、もう一度操作してください。" };
  }
  if (apiError?.status === 422) {
    return { code: "validation_error", message: `入力内容を受け付けられませんでした。${apiError.detail ? `(${apiError.detail})` : ""}` };
  }
  return { code: "unknown", message: apiError?.detail || fallback };
}

export function RejectedNotice({ code, message, testId = "product-decision-rejected" }: Rejection & { testId?: string }) {
  return (
    <div className="rounded border border-destructive p-2 text-xs" role="alert" data-testid={testId}>
      <span>{message}</span>
      <span className="ml-2 font-mono text-[10px] text-muted-foreground">{code}</span>
    </div>
  );
}

export interface DecisionConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** e.g. 「Objective「o1」を確定する」 */
  title: string;
  /** e.g. 「現在の状態: 未確定」 -- the server's own value. */
  currentStateText: string;
  /** What recording this does, and what it does NOT do. */
  consequence: ReactNode;
  confirmLabel: string;
  reasonRequired?: boolean;
  reasonLabel?: string;
  pending: boolean;
  onConfirm: (reason: string) => void;
  children?: ReactNode;
}

/** Mounted only while open, so the reason starts empty every time -- one
 * decision's wording is never carried into the next. */
export function DecisionConfirmDialog(props: DecisionConfirmDialogProps) {
  if (!props.open) return null;
  return <DecisionConfirmBody {...props} />;
}

function DecisionConfirmBody({
  open, onOpenChange, title, currentStateText, consequence, confirmLabel,
  reasonRequired = false, reasonLabel = "理由", pending, onConfirm, children,
}: DecisionConfirmDialogProps) {
  const [reason, setReason] = useState("");
  const reasonId = useId();
  // Initial focus is the shared Dialog's job (`useModalSurface`): it lands on
  // 「やめる」 (`data-autofocus`), so an accidental Enter never records.
  const blocked = reasonRequired && reason.trim().length === 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange} closeOnOverlayClick={false}>
      <div data-testid="decision-confirm-dialog">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{currentStateText}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="text-xs text-muted-foreground">{consequence}</div>
          {children}
          <div className="space-y-1">
            <Label htmlFor={reasonId}>
              {reasonLabel}
              {reasonRequired ? "(必須)" : "(任意)"}
            </Label>
            <Textarea
              id={reasonId}
              aria-label={reasonLabel}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              data-testid="decision-confirm-reason"
            />
            <p className="text-xs text-muted-foreground">
              人の判断(decision_method: manual)として追記のみの記録に残ります。実行者はログイン中のユーザーです。
            </p>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} data-autofocus="" data-testid="decision-confirm-cancel">
              やめる
            </Button>
            <Button disabled={blocked || pending} onClick={() => onConfirm(reason.trim())} data-testid="decision-confirm-submit">
              {pending ? "記録中…" : confirmLabel}
            </Button>
          </div>
          {blocked && (
            <p className="text-xs text-destructive">理由を入力するまで記録できません。</p>
          )}
        </div>
      </div>
    </Dialog>
  );
}

/** A finite decision kind offered as a button that opens the confirmation.
 * Small helper so each form keeps one `pending` dialog at a time. */
export function useDecisionDialog<K extends string>() {
  const [pendingKind, setPendingKind] = useState<K | null>(null);
  return {
    pendingKind,
    open: (kind: K) => setPendingKind(kind),
    close: () => setPendingKind(null),
  };
}
