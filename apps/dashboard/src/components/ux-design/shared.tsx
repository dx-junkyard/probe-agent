// Issue #409: small display primitives shared across the Journey /
// Requirement / Solution Design panels. Every one of these is presentation
// only -- no field here is computed from anything but props the caller
// already read from a server response.

import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import {
  useCreateUxArtifactReference,
  useRecordUxDesignDecision,
} from "@/api/hooks";
import { ApiError } from "@/api/client";
import type {
  UxArtifactKind,
  UxArtifactReferenceOut,
  UxArtifactSubjectKind,
  UxDesignDecisionKind,
  UxDesignDecisionOut,
  UxDesignSubjectKind,
} from "@/api/types";
import { ARTIFACT_KIND_LABEL, ARTIFACT_VERIFICATION_LABEL } from "./model";

/**
 * A failed query's card: 「取得できませんでした」 plus a retry, never an empty
 * page body (#356 / #380 / #409 受入条件 not applicable directly, but the
 * same discipline every other screen in this codebase follows).
 */
export function LoadErrorCard({
  title = "取得できませんでした",
  detail,
  onRetry,
  testId = "ux-design-load-error",
}: {
  title?: string;
  detail?: string;
  onRetry: () => void;
  testId?: string;
}) {
  return (
    <div
      className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm"
      data-testid={testId}
      role="alert"
    >
      <p className="font-medium text-destructive">{title}</p>
      {detail && <p className="mt-1 text-muted-foreground">{detail}</p>}
      <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
        再試行
      </Button>
    </div>
  );
}

export function LoadingBlock({ testId }: { testId?: string }) {
  return (
    <div data-testid={testId ?? "ux-design-loading"} className="space-y-2" aria-busy="true">
      <Skeleton className="h-6 w-full" />
      <Skeleton className="h-6 w-3/4" />
    </div>
  );
}

export function EmptyNote({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <p className="text-sm text-muted-foreground" data-testid={testId ?? "ux-design-empty"}>
      {children}
    </p>
  );
}

/**
 * Pairs a colour with a text marker -- #358 / #387 accessibility rule: no
 * state may be signalled by colour alone. `label` is the caller's own fixed
 * copy for the state (from `model.ts`), never re-derived here.
 */
export function StateBadge({
  label,
  tone = "outline",
}: {
  label: string;
  tone?: "outline" | "secondary" | "success" | "warning" | "destructive";
}) {
  return <Badge variant={tone}>{label}</Badge>;
}

export function SectionHeading({
  as: Tag = "h3",
  children,
}: {
  as?: "h2" | "h3" | "h4";
  children: ReactNode;
}) {
  return <Tag className="text-sm font-semibold">{children}</Tag>;
}

/** A degraded-section note: names WHICH section could not be read, never
 * substitutes a blank or a zero for it (#380's "unreadable fact is never a
 * fact's default value", applied to this layer). */
export function DegradedNote({ sections, detail }: { sections: string[]; detail: Record<string, string> }) {
  if (sections.length === 0) return null;
  return (
    <div
      className="rounded border border-amber-400/50 bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200"
      data-testid="ux-design-degraded"
    >
      一部の情報を取得できませんでした: {sections.join(", ")}
      {sections.some((s) => detail[s]) && (
        <span> ({sections.filter((s) => detail[s]).map((s) => `${s}: ${detail[s]}`).join(" / ")})</span>
      )}
    </div>
  );
}

const ARTIFACT_KINDS: UxArtifactKind[] = [
  "wireframe", "adr", "spec", "diagram", "research_note", "other",
];

/** Read-only rendering of a subject's `ux_design_artifact_reference` rows.
 * Verification state keeps its own sentence (`ARTIFACT_VERIFICATION_LABEL`)
 * -- `verified` only ever means "hashed against the pinned snapshot's own
 * `git show`", never "the developer said so" (§2.8). */
export function ArtifactReferencesCard({
  artifacts,
  subjectKind,
  subjectKey,
}: {
  artifacts: UxArtifactReferenceOut[];
  subjectKind: UxArtifactSubjectKind;
  subjectKey: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-2" data-testid="ux-design-artifacts">
      <SectionHeading>関連資料(ワイヤーフレーム・ADR 等)</SectionHeading>
      {artifacts.length === 0 ? (
        <EmptyNote>関連資料は登録されていません。</EmptyNote>
      ) : (
        <ul className="space-y-1">
          {artifacts.map((a) => (
            <li key={a.id} className="rounded border p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{ARTIFACT_KIND_LABEL[a.artifact_kind]}</Badge>
                <span className="font-mono">{a.title || a.uri}</span>
              </div>
              <div className="mt-1 text-muted-foreground">{ARTIFACT_VERIFICATION_LABEL[a.verification_state]}</div>
            </li>
          ))}
        </ul>
      )}
      <Button variant="ghost" size="sm" onClick={() => setOpen((v) => !v)}>
        {open ? "資料の追加を閉じる" : "資料を追加する"}
      </Button>
      {open && <AddArtifactForm subjectKind={subjectKind} subjectKey={subjectKey} onDone={() => setOpen(false)} />}
    </div>
  );
}

function AddArtifactForm({
  subjectKind,
  subjectKey,
  onDone,
}: {
  subjectKind: UxArtifactSubjectKind;
  subjectKey: string;
  onDone: () => void;
}) {
  const create = useCreateUxArtifactReference();
  const [artifactKind, setArtifactKind] = useState<UxArtifactKind>("spec");
  const [title, setTitle] = useState("");
  const [uri, setUri] = useState("");
  const [contentHash, setContentHash] = useState("");

  function submit() {
    create.mutate(
      {
        subject_kind: subjectKind,
        subject_key: subjectKey,
        artifact_kind: artifactKind,
        title,
        uri,
        content_hash: contentHash,
      },
      {
        onSuccess: () => {
          toast.success("資料を登録しました");
          setTitle("");
          setUri("");
          setContentHash("");
          onDone();
        },
        onError: (error) => toast.error((error as ApiError).detail || "登録できませんでした"),
      },
    );
  }

  return (
    <div className="space-y-2 rounded border p-2" data-testid="ux-design-add-artifact-form">
      <Select value={artifactKind} onChange={(e) => setArtifactKind(e.target.value as UxArtifactKind)}>
        {ARTIFACT_KINDS.map((k) => (
          <option key={k} value={k}>
            {ARTIFACT_KIND_LABEL[k]}
          </option>
        ))}
      </Select>
      <Input placeholder="タイトル" value={title} onChange={(e) => setTitle(e.target.value)} />
      <Input
        placeholder="uri(例: repo:docs/wireframe.png または外部URL)"
        value={uri}
        onChange={(e) => setUri(e.target.value)}
      />
      <Input
        placeholder="content_hash(sha256)"
        value={contentHash}
        onChange={(e) => setContentHash(e.target.value)}
      />
      <Button size="sm" disabled={!uri.trim() || !contentHash.trim() || create.isPending} onClick={submit}>
        {create.isPending ? "登録中…" : "登録する"}
      </Button>
    </div>
  );
}

const DECISION_KINDS: UxDesignDecisionKind[] = ["confirm", "reject", "retire", "reinstate"];

const DECISION_KIND_LABEL: Record<UxDesignDecisionKind, string> = {
  confirm: "確定する",
  reject: "却下する",
  retire: "廃止する",
  reinstate: "再提案に戻す",
};

/**
 * Confirm / reject / retire / reinstate for a Journey or a Requirement.
 * §3.6-adjacent rule for THIS layer: recording a decision changes only
 * `ux_design_decision` (and, downstream, `design_status`) -- it does not
 * apply, deploy, or execute anything. The Studio holds no legality table of
 * its own (same discipline `evolution-nodes.tsx` documents for maturity
 * transitions): every target is offered, and an illegal one comes back as
 * the server's own finite rejection code.
 */
export function DesignDecisionControls({
  subjectKind,
  subjectKey,
  capturedDigest,
  decisions,
}: {
  subjectKind: UxDesignSubjectKind;
  subjectKey: string;
  capturedDigest: string;
  decisions: UxDesignDecisionOut[];
}) {
  const record = useRecordUxDesignDecision();
  const [rationale, setRationale] = useState("");
  const [rejection, setRejection] = useState<{ code: string; message: string } | null>(null);

  function submit(decision: UxDesignDecisionKind) {
    setRejection(null);
    record.mutate(
      { subject_kind: subjectKind, subject_key: subjectKey, decision, rationale, captured_digest: capturedDigest },
      {
        onSuccess: () => {
          setRationale("");
          toast.success(`「${DECISION_KIND_LABEL[decision]}」を記録しました`);
        },
        onError: (error) => {
          const apiError = error as ApiError;
          setRejection({ code: apiError.code ?? "unknown", message: apiError.detail || "記録できませんでした" });
        },
      },
    );
  }

  return (
    <div className="space-y-2" data-testid="ux-design-decisions">
      <SectionHeading>確定・却下の判断(人間の判断として記録されます)</SectionHeading>
      <Textarea
        placeholder="理由(任意)"
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        rows={2}
      />
      <div className="flex flex-wrap gap-2">
        {DECISION_KINDS.map((d) => (
          <Button key={d} size="sm" variant="outline" disabled={record.isPending} onClick={() => submit(d)}>
            {DECISION_KIND_LABEL[d]}
          </Button>
        ))}
      </div>
      {rejection && (
        <div className="rounded border border-destructive p-2 text-xs" data-testid="ux-design-decision-rejected">
          <span className="font-mono text-destructive">{rejection.code}</span>
          <span className="ml-2">{rejection.message}</span>
        </div>
      )}
      {decisions.length > 0 && (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {decisions.map((d) => (
            <li key={d.id}>
              {DECISION_KIND_LABEL[d.decision]} — {d.decided_by ?? "(記録なし)"} / {formatTimestamp(d.created_at)}
              {d.rationale ? `:${d.rationale}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
