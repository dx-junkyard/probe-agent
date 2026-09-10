// Gap discussion context + 照合 panel (Issue #459, Epic #457).
//
// docs/01-specifications/ux/decision-discussion-workflow.md is the UX contract this implements (§3's
// 対象選択/照合結果 stages, DD-UX-01/03); docs/01-specifications/capabilities/ai-discussion-adapter.md §9 is the
// data contract (#458's context bundle) and §9.2 (this Issue's own
// `app/discussion_claims.py`).
//
// Two independent reads, one action:
//   - `next_action` comes straight off `thread.next_action` (server-decided,
//     `app/discussion_next_action.py`) -- this component NEVER re-derives
//     the priority order (DD-UX-01: "clientは同じ判定表を本番コードへ複製
//     しない"). It renders the server's own `reason` sentence and offers
//     exactly one button, and only for the two kinds THIS panel's own
//     operation (照合) can advance (`match` / `evidence_stale`); every other
//     kind is text-only here because its own action lives elsewhere on this
//     same screen (Proposal/Hypothesis review below, §6 of this doc).
//   - The context bundle (#458, read-only, `useDiscussionContextBundle`) is
//     shown as a progressive-disclosure summary (`<details>` per section,
//     §DD-AC-04's "390px幅で...段階表示" -- a native disclosure needs no
//     extra keyboard wiring and collapses cleanly on a narrow screen).
//   - "目的・UX・機能を照合" runs `POST .../context-claims` (this Issue's
//     new endpoint) and renders the returned fact/inference/hypothesis/
//     unknown/conflict claims with their citations resolved against the
//     SAME bundle's `sources[]` deep links -- never a client-side guess at
//     a URL from a source id string.
//
// Recovery (DD-UX-07): a failed 照合 call keeps the last question text in
// place (nothing is cleared) and can be retried with the same button; the
// component is remounted (via a `key={threadId}` the caller supplies) on a
// target switch, which is the explicit "discard on target switch" this
// Issue's own report calls for -- there is deliberately no cross-target
// memory to reset by hand.

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useCreateDiscussionContextClaims, useDiscussionContextBundle, useExpandDiscussionContext,
} from "@/api/hooks";
import { ApiError } from "@/api/client";
import type {
  AssistantDiscussionThreadDetailOut, DiscussionContextClaimKind, DiscussionContextClaimsResultOut,
  DiscussionContextEntry, DiscussionContextSection, DiscussionOperationResult, GapDiscussionNextActionKind,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { appendReturnTo } from "@/components/discussion-return-banner";
import { DISCUSSION_ADAPTERS } from "@/lib/discussion-adapters";
import { ExternalLink, Loader2 } from "lucide-react";

// Fixed presentation labels only -- never a decision. DD-UX-01 forbids
// re-deriving WHICH kind wins; mapping an already-decided kind to Japanese
// copy is the same kind of rendering `objective.tsx`'s
// `NEXT_STEP_CTA_LABEL[objective.next_step]` already does for #427/#380.
const NEXT_ACTION_LABEL: Record<GapDiscussionNextActionKind, string> = {
  target_error: "対象を確認できません",
  evidence_stale: "根拠が更新されています",
  processing: "調査が進行中です",
  save_unknown: "保存結果が未確認です",
  unsaved_edit: "未保存の編集があります",
  review_proposal: "変更候補のレビュー待ちです",
  investigation_result: "調査結果があります",
  review_hypothesis: "仮説のレビュー待ちです",
  match: "目的・UX・機能の照合",
};

// This panel owns the 照合 operation, so it offers a button only for the two
// kinds that operation can actually advance. Every other kind's own action
// lives in the sections already rendered elsewhere on this screen (Proposal
// review, hypothesis review) -- duplicating a second button here would be a
// second, competing "primary action" (§3's one-主操作 rule).
const MATCH_ACTIONABLE_KINDS = new Set<GapDiscussionNextActionKind>(["match", "evidence_stale"]);

const OPERATION_STATE_LABEL: Record<DiscussionOperationResult, string> = {
  available: "取得済み",
  unsupported: "未登録",
  unavailable: "取得失敗",
  not_applicable: "対象外",
};

const CLAIM_KIND_LABEL: Record<DiscussionContextClaimKind, string> = {
  fact: "事実",
  inference: "推論",
  hypothesis: "仮説",
  unknown: "不明",
  conflict: "矛盾",
};

// Never color alone (DD-AC-04): every badge also carries this fixed text
// marker, so kind is legible without perceiving color.
const CLAIM_KIND_MARKER: Record<DiscussionContextClaimKind, string> = {
  fact: "[事実]",
  inference: "[推論]",
  hypothesis: "[仮説]",
  unknown: "[不明]",
  conflict: "[矛盾]",
};

const CLAIM_ERROR_MESSAGE: Record<string, string> = {
  unavailable: "reasoning modelの設定がないため照合できません。",
  call_error: "照合の呼び出しに失敗しました。再試行してください。",
  invalid_response: "照合結果の形式が不正でした。再試行してください。",
  invalid_citation: "根拠として存在しない引用が含まれていたため、再生成後も確定できませんでした。",
};

function sectionEntryLabel(entry: DiscussionContextEntry): string {
  return entry.title || `${entry.target_kind}:${entry.target_ref}`;
}

function ContextSectionDisclosure({
  section, onExpand, expanding,
}: {
  section: DiscussionContextSection;
  onExpand: (section: DiscussionContextSection) => void;
  expanding: boolean;
}) {
  const { coverage } = section;
  const totalText = coverage.total_count === null ? "不明" : String(coverage.total_count);
  return (
    <details className="rounded border p-2" data-testid="discussion-context-section">
      <summary className="cursor-pointer text-xs font-medium">
        {section.section_id} — {OPERATION_STATE_LABEL[section.operation_state]}
        {section.operation_state === "available" && (
          <span className="ml-1 text-muted-foreground">
            ({coverage.returned_count}/{totalText}件・{coverage.completeness})
          </span>
        )}
      </summary>
      <div className="mt-2 space-y-1">
        {section.operation_state !== "available" ? (
          <p className="text-[11px] text-muted-foreground" data-testid="discussion-context-section-note">
            {section.operation_state === "unsupported" && "この対象について、この関連の種類は正本に登録されていません。"}
            {section.operation_state === "not_applicable" && "この対象にはこの区分の関連情報がありません。"}
            {section.operation_state === "unavailable" && "この範囲は取得に失敗し、確認できていません。"}
          </p>
        ) : section.facts.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">この区分に該当する項目はありません。</p>
        ) : (
          <ul className="space-y-0.5">
            {section.facts.map((entry) => (
              <li key={`${entry.target_kind}:${entry.target_ref}`} className="text-[11px]">
                {sectionEntryLabel(entry)}
                {entry.truncated && <span className="ml-1 text-muted-foreground">(一部省略)</span>}
              </li>
            ))}
          </ul>
        )}
        {coverage.continuation && (
          <Button
            size="sm" variant="outline" disabled={expanding}
            onClick={() => onExpand(section)}
            data-testid="discussion-context-section-expand"
          >
            {expanding ? <Loader2 className="h-3 w-3 animate-spin" /> : "追加取得"}
          </Button>
        )}
      </div>
    </details>
  );
}

function ClaimsResult({
  result, bundleSources, returnTo,
}: {
  result: DiscussionContextClaimsResultOut;
  bundleSources: Map<string, { deepLink: string | null; deepLinkState: string; title: string }>;
  returnTo: string | null;
}) {
  return (
    <div className="space-y-2" data-testid="discussion-context-claims-result" role="status" aria-live="polite">
      {result.scope_note && (
        <p className="text-[11px] text-muted-foreground" data-testid="discussion-context-scope-note">
          確認範囲: {result.scope_note}
          {result.as_of_snapshot_commit && ` (時点: ${result.as_of_snapshot_commit.slice(0, 12)})`}
        </p>
      )}
      {result.error && (
        <p className="text-xs text-destructive" data-testid="discussion-context-claims-error">
          {CLAIM_ERROR_MESSAGE[result.error_kind ?? ""] ?? result.error}
        </p>
      )}
      <ul className="space-y-1.5">
        {result.claims.map((claim, i) => (
          <li key={i} className="rounded border p-2 text-xs" data-testid="discussion-context-claim">
            <Badge variant="outline" className="mr-1.5">
              {CLAIM_KIND_MARKER[claim.kind]} {CLAIM_KIND_LABEL[claim.kind]}
            </Badge>
            <span>{claim.statement}</span>
            <div className="mt-1 flex flex-wrap gap-1">
              {claim.cited_source_ids.map((sid) => {
                const source = bundleSources.get(sid);
                if (!source || !source.deepLink || source.deepLinkState !== "selected") {
                  return (
                    <span key={sid} className="text-[10px] text-muted-foreground" data-testid="discussion-context-citation-unavailable">
                      {source?.title || sid}
                    </span>
                  );
                }
                // Issue #459 (docs/01-specifications/ux/decision-discussion-workflow.md §2): a citation may land
                // on a DIFFERENT screen than this thread's own -- carry a
                // reference back (never draft content) so `ReturnToBanner`
                // can offer the way back once there.
                return (
                  <Link
                    key={sid} to={appendReturnTo(source.deepLink, returnTo)}
                    className="inline-flex items-center gap-0.5 text-[10px] text-primary hover:underline"
                    data-testid="discussion-context-citation"
                  >
                    {source.title || sid} <ExternalLink className="h-2.5 w-2.5" />
                  </Link>
                );
              })}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Renders inside `assistant-panel.tsx` for a `focus`-scoped discussion
 * thread. Pass a stable `key={thread.thread.id}` from the caller so a
 * target switch remounts this component instead of carrying over a
 * previous target's in-flight question/result (DD-UX-07's "対象切替時は
 * 破棄を明示"). */
export function DiscussionContextPanel({ thread }: { thread: AssistantDiscussionThreadDetailOut }) {
  const threadId = thread.thread.id;
  const bundleQuery = useDiscussionContextBundle(threadId);
  const expand = useExpandDiscussionContext(threadId);
  const claims = useCreateDiscussionContextClaims(threadId);
  const [question, setQuestion] = useState("目的・UX・機能を照合");
  const [expandingSection, setExpandingSection] = useState<string | null>(null);
  const [expandError, setExpandError] = useState<string | null>(null);
  const [claimsCallError, setClaimsCallError] = useState<string | null>(null);

  const nextAction = thread.next_action;
  const bundle = bundleQuery.data ?? null;
  // The current thread's OWN deep link -- the reference a citation's
  // destination carries back if it lands on a different screen. `null` when
  // this target's adapter has none (a whole-screen thread has no single
  // deep link of its own), in which case a citation simply navigates with
  // no return reference rather than a guessed one.
  const originDeepLink = DISCUSSION_ADAPTERS[thread.thread.target_kind].deepLink(thread.thread.target_ref);

  const bundleSources = new Map(
    (bundle?.sources ?? []).map((s) => [
      s.source_id,
      { deepLink: s.deep_link, deepLinkState: s.deep_link_state, title: `${s.target_kind}:${s.target_ref}` },
    ]),
  );

  function handleExpand(section: DiscussionContextSection) {
    if (!bundle || !section.coverage.continuation) return;
    setExpandingSection(section.section_id);
    setExpandError(null);
    expand.mutate(
      { bundle_digest: bundle.bundle_digest, continuation: section.coverage.continuation },
      {
        onSettled: () => setExpandingSection(null),
        onError: (err) => {
          const code = err instanceof ApiError ? err.code : undefined;
          if (code === "discussion_context_continuation_stale") {
            setExpandError("根拠が更新されています。最新の内容で再照合してください。");
          } else if (code === "discussion_context_continuation_expired") {
            setExpandError("取得の続きが期限切れです。パネルを開き直してください。");
          } else {
            setExpandError("追加取得に失敗しました。再試行してください。");
          }
        },
      },
    );
    // The merged bundle is not written back into `useDiscussionContextBundle`'s
    // cache on purpose (that hook's own docstring: doing so would silently
    // discard this exact "just the new batch" result and refetch a brand new
    // initial bundle instead). `bundleQuery.refetch()` re-derives from scratch
    // with a fresh cursor, which is the safe way to show the union again --
    // the tradeoff this Issue accepts is that a second click before the first
    // completes may issue a redundant fetch, never a lost one.
  }

  function handleMatch() {
    setClaimsCallError(null);
    claims.mutate(
      { question },
      {
        onSuccess: () => bundleQuery.refetch(),
        onError: (err) => {
          setClaimsCallError(
            err instanceof ApiError ? err.detail : "照合の呼び出しに失敗しました。再試行してください。",
          );
        },
      },
    );
  }

  return (
    <div className="border-t p-3 space-y-3" data-testid="discussion-context-panel">
      {nextAction && (
        <div
          className="rounded-lg border bg-card p-3 space-y-1.5"
          role="status" aria-live="polite"
          data-testid="discussion-next-action"
        >
          <p className="text-[11px] font-medium text-muted-foreground">次にすること</p>
          <p className="text-sm font-medium" data-testid="discussion-next-action-label">
            {NEXT_ACTION_LABEL[nextAction.kind]}
          </p>
          {nextAction.reason && <p className="text-xs text-muted-foreground">{nextAction.reason}</p>}
          {nextAction.degraded_sections.length > 0 && (
            <p className="text-[11px] text-amber-700" data-testid="discussion-next-action-degraded">
              一部の情報を確認できませんでした({nextAction.degraded_sections.join("・")})。
              理由と復旧: 時間をおいて開き直してください。表示できる範囲では続けられます。
            </p>
          )}
          {MATCH_ACTIONABLE_KINDS.has(nextAction.kind) && (
            <div className="flex items-center gap-2">
              <input
                className="h-8 flex-1 rounded-md border bg-background px-2 text-xs"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="この機能で十分か、など"
                data-testid="discussion-context-question"
              />
              <Button size="sm" onClick={handleMatch} disabled={claims.isPending} data-testid="discussion-context-match">
                {claims.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "照合"}
              </Button>
            </div>
          )}
        </div>
      )}

      <details className="rounded-lg border bg-card p-3" data-testid="discussion-context-bundle">
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
          関連する Vision・UX・機能（確認範囲）
        </summary>
        <div className="mt-2 space-y-2">
          {bundleQuery.isLoading && (
            <p className="text-[11px] text-muted-foreground">取得しています…</p>
          )}
          {bundleQuery.isError && (
            <p className="text-xs text-destructive" data-testid="discussion-context-bundle-error">
              関連情報を取得できませんでした。開き直して再試行してください。
            </p>
          )}
          {bundle && (
            <>
              {bundle.snapshot.commit_sha && (
                <p className="text-[11px] text-muted-foreground">
                  時点: {bundle.snapshot.commit_sha.slice(0, 12)}
                </p>
              )}
              {expandError && (
                <p className="text-xs text-destructive" data-testid="discussion-context-expand-error">
                  {expandError}
                </p>
              )}
              <div className="space-y-1.5">
                {bundle.sections.map((section) => (
                  <ContextSectionDisclosure
                    key={section.section_id}
                    section={section}
                    onExpand={handleExpand}
                    expanding={expandingSection === section.section_id}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      </details>

      {claimsCallError && (
        <p className="text-xs text-destructive" data-testid="discussion-context-match-error">
          {claimsCallError}
        </p>
      )}
      {claims.data && (
        <ClaimsResult result={claims.data} bundleSources={bundleSources} returnTo={originDeepLink} />
      )}
    </div>
  );
}
