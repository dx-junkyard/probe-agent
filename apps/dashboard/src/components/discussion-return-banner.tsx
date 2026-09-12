// Issue #459 (Epic #457, docs/01-specifications/ux/decision-discussion-workflow.md §2): "詳細を開いても検討起点の
// Gapへの戻り先を保持する...戻り先は参照だけを持ち、draft本文をURLに入れ
// ない". A citation clicked from `DiscussionContextPanel` can land on a
// DIFFERENT screen than the one the developer started the conversation on
// (e.g. a Gap's related Requirement, opened on `ux-design-studio`). This is
// the ONE place that renders the "戻る" affordance for that hop, mounted
// once in `AppLayout` so it survives whichever screen the citation landed
// on -- it never re-derives WHERE to go, only reads the `returnTo` query
// param a citation link already carried (`appendReturnTo` below is the only
// producer).
//
// Safety: `returnTo` is untrusted request data (a query param), so this
// only ever navigates to a same-origin relative path (`startsWith("/")`) --
// never an absolute URL, which would be an open-redirect surface.

import { Link, useLocation } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useDiscussionSaveRequest } from "@/api/hooks";
import { Button } from "@/components/ui/button";

/** Appends `returnTo=<url>` to a deep link, used ONLY for other same-app
 * relative paths -- callers pass `null` when there is nothing to return to
 * (e.g. no adapter deep link for the current thread's own target). */
export function appendReturnTo(url: string, returnTo: string | null): string {
  if (!returnTo || !isLocalPath(returnTo)) return url;
  return withQuery(url, "returnTo", returnTo);
}

function withQuery(path: string, key: string, value: string) {
  const hashAt = path.indexOf("#");
  const hash = hashAt < 0 ? "" : path.slice(hashAt);
  const base = hashAt < 0 ? path : path.slice(0, hashAt);
  const queryAt = base.indexOf("?");
  const params = new URLSearchParams(queryAt < 0 ? "" : base.slice(queryAt + 1));
  params.set(key, value);
  return `${queryAt < 0 ? base : base.slice(0, queryAt)}?${params}${hash}`;
}

function isLocalPath(value: string) {
  return value.startsWith("/") && !value.startsWith("//") && !value.includes("\\") && !/[\r\n]/.test(value);
}

function SaveReceipt({ id }: { id: string }) {
  const receipt = useDiscussionSaveRequest(id);
  const location = useLocation();
  const data = receipt.data;
  const origin = new URLSearchParams(location.search).get("returnTo");
  const returnTarget = origin && isLocalPath(origin) ? origin : `${location.pathname}${location.search}`;
  const resultLink = data?.target_kind === "ux_requirement"
    ? `/ux-design-studio?tab=requirements&requirement=${encodeURIComponent(data.target_ref)}&save_request=${encodeURIComponent(id)}` : null;
  return <div role="status" className="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-xs">
    <span>{receipt.isError ? "保存結果を確認できませんでした。" : !data ? "保存結果を確認中…"
      : data.status === "succeeded" ? `${data.target_ref} の保存完了（revision ${data.revision_id}）`
      : "前回の保存は完了していません。"}</span>
    <Button size="sm" variant="outline" onClick={() => void receipt.refetch()}>保存結果を再取得する</Button>
    {resultLink && data?.status === "succeeded" && <Link to={appendReturnTo(resultLink, returnTarget)}>保存した要件を開く</Link>}
  </div>;
}

export function ReturnToBanner() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const returnTo = params.get("returnTo");
  const saveRequest = params.get("save_request");
  const validReturn = returnTo && isLocalPath(returnTo);
  const returnLink = validReturn && saveRequest
    ? withQuery(returnTo, "save_request", saveRequest) : returnTo;
  return (
    <>
    {saveRequest && <SaveReceipt key={saveRequest} id={saveRequest} />}
    {validReturn &&
    <div
      className="flex items-center gap-2 border-b bg-muted/50 px-4 py-1.5 text-xs"
      role="status"
      data-testid="discussion-return-banner"
    >
      <ArrowLeft className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>もとの検討から移動しました。</span>
      <Link to={returnLink!} className="font-medium text-primary hover:underline" data-testid="discussion-return-banner-link">
        元の検討へ戻る
      </Link>
    </div>
    }
    </>
  );
}
