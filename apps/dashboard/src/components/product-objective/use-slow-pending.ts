// Issue #432 (Epic #427) P2 review fix §3.3: a lane must never show an
// indefinite bare skeleton (`docs/product-objective-lineage.md` §9.5).
//
// `useSlowPending` starts returning `false` (a plain loading skeleton is
// enough) and flips to `true` once `pending` has been true for longer than
// `thresholdMs`, so the caller can switch to naming the section, saying why
// it is waiting, and offering 再試行. A fresh pending period (pending goes
// false, then true again -- e.g. after a retry) starts its own timer.

import { useEffect, useState } from "react";

const DEFAULT_THRESHOLD_MS = 4000;

export function useSlowPending(pending: boolean, thresholdMs: number = DEFAULT_THRESHOLD_MS): boolean {
  const [slow, setSlow] = useState(false);
  // Reset during render, not in the effect: a synchronous setState inside an
  // effect renders the stale value first, so a finished-then-restarted
  // pending period would flash the slow message before clearing it. The
  // effect below owns only the timer, which is a real subscription.
  const [lastPending, setLastPending] = useState(pending);
  if (lastPending !== pending) {
    setLastPending(pending);
    if (slow) setSlow(false);
  }

  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => setSlow(true), thresholdMs);
    return () => window.clearTimeout(timer);
  }, [pending, thresholdMs]);

  return slow;
}
