import { useSyncExternalStore } from "react";

// メディアクエリの現在値を購読する。`matchMedia` が無い環境 (jsdom など) では
// `fallback` を返す。
export function useMediaQuery(query: string, fallback = false): boolean {
  const supported = typeof window !== "undefined" && typeof window.matchMedia === "function";
  return useSyncExternalStore(
    (onChange) => {
      if (!supported) return () => {};
      const list = window.matchMedia(query);
      list.addEventListener?.("change", onChange);
      return () => list.removeEventListener?.("change", onChange);
    },
    () => (supported ? window.matchMedia(query).matches : fallback),
    () => fallback,
  );
}

/**
 * Issue #466 (UX-17): アシスタントを本文と並べて表示できる幅。サイドバーの
 * レール (w-56) + パネル (max-w-md) を差し引いても本文に十分な幅が残る境界。
 */
export const ASSISTANT_SIDE_BY_SIDE_QUERY = "(min-width: 1280px)";
