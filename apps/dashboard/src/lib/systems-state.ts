// Issue #466 (UX-06): 「System が作成されていない」と言ってよいか。
//
// 取得に失敗した空配列や、まだ取得中の空配列は「0 件」ではない。成功した
// 0 件だけが「未作成」である。`systemsStatus` を持たない呼び出し (古い
// AuthState を返すテストのモック等) では従来どおり配列の長さだけで判断する。
//
// `@/api/auth` ではなくここに置くのは、多くのテストが `@/api/auth` をモック
// しており、モックに無い export を読むと失敗するため。

export function systemsKnownEmpty(auth: {
  systems: readonly unknown[];
  systemsStatus?: "loading" | "ready" | "error";
}): boolean {
  return auth.systems.length === 0 && (auth.systemsStatus === undefined || auth.systemsStatus === "ready");
}

/** A successful list can prove a saved selection is no longer available.
 * Do not replace it automatically: a background refresh must preserve drafts. */
export function systemSelectionUnavailable(auth: {
  systemId: number | null;
  systems: readonly { id: number }[];
  systemsStatus?: "loading" | "ready" | "error";
}): boolean {
  return auth.systemsStatus === "ready" && auth.systemId !== null
    && !auth.systems.some((system) => system.id === auth.systemId);
}
