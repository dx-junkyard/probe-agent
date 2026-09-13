// Idempotent save-request ids for the AI Discussion Adapter's prefill-to-save
// flow (Issue #452, Epic #443 §3.6/§3.7).
//
// docs/01-specifications/capabilities/ai-discussion-adapter.md §3.6 is the canonical contract. A form's own
// "保存する" submit binds a `save_request_id` to the CONTENT it is about to
// send. This module is the single place that decides when to reuse that id
// (a retry of the SAME content -- a lost response, or a deliberate "もう一度
// 試す" click) versus mint a fresh one (the developer edited something since
// the last attempt, per the Decisions: "ユーザーが編集を変えたら新しい保存
// 要求として扱う"). A form never generates its own id inline -- that is how
// two forms would end up with two different reuse policies.

import { useRef, useState } from "react";

function generateSaveRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `save-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export interface UseIdempotentSaveRequestResult {
  /**
   * Call at submit time with a digest of the form's CURRENT content
   * (everything the domain write will persist -- never including the id
   * itself). Returns the `save_request_id` to send: the SAME one as the
   * previous call when the digest is unchanged (a retry), a FRESH one when
   * it differs (a new attempt, because the developer edited the form).
   */
  idFor(digest: string): string;
  /** The id bound to the form's last-submitted content, or `null` before
   * any submit -- used to enable a "状態を確認する" query after a response
   * is lost, and to know whether a "再試行" click would reuse an id. */
  activeId: string | null;
  /** Clears the binding after a CONFIRMED outcome (a successful save, or a
   * confirmed-lost-then-found success via the result-query API). The next
   * submit always mints a fresh id -- there is nothing left to retry. */
  clear(): void;
}

export function useIdempotentSaveRequest(): UseIdempotentSaveRequestResult {
  const [activeId, setActiveId] = useState<string | null>(null);
  const lastDigest = useRef<string | null>(null);

  function idFor(digest: string): string {
    if (activeId !== null && lastDigest.current === digest) return activeId;
    const id = generateSaveRequestId();
    lastDigest.current = digest;
    setActiveId(id);
    return id;
  }

  function clear() {
    setActiveId(null);
    lastDigest.current = null;
  }

  return { idFor, activeId, clear };
}
