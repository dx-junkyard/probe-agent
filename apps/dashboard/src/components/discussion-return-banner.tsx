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

/** Appends `returnTo=<url>` to a deep link, used ONLY for other same-app
 * relative paths -- callers pass `null` when there is nothing to return to
 * (e.g. no adapter deep link for the current thread's own target). */
export function appendReturnTo(url: string, returnTo: string | null): string {
  if (!returnTo) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}returnTo=${encodeURIComponent(returnTo)}`;
}

export function ReturnToBanner() {
  const location = useLocation();
  const returnTo = new URLSearchParams(location.search).get("returnTo");
  if (!returnTo || !returnTo.startsWith("/") || returnTo.startsWith("//")) return null;
  return (
    <div
      className="flex items-center gap-2 border-b bg-muted/50 px-4 py-1.5 text-xs"
      role="status"
      data-testid="discussion-return-banner"
    >
      <ArrowLeft className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>もとの検討から移動しました。</span>
      <Link to={returnTo} className="font-medium text-primary hover:underline" data-testid="discussion-return-banner-link">
        元の検討へ戻る
      </Link>
    </div>
  );
}
