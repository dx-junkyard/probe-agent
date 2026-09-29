// Issue #466 (UX-05/06): 取得失敗の種類を、利用者の次の操作が分かる言葉へ
// 写す。判定はエラーの型と HTTP status (有限集合) だけで行い、業務状態の
// 意味は推測しない。
//
// 重要なのは「失敗」を「0 件」「未作成」「対象が存在しない」と同じ表示に
// しないこと。500・オフライン・権限エラーのどれでも、対象がまだ無いのか
// どうかは分からない。

// `@/api/client` のクラスを instanceof で判定しない: 多くのテストが client を
// モックしており、モックに無い export へ触れると失敗する。形 (name / status)
// で判定すれば、実物でもモックの ApiError でも同じ答えになる。

export type RequestFailureKind =
  | "offline"
  | "network"
  | "timeout"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "server"
  | "unknown";

export interface RequestFailure {
  kind: RequestFailureKind;
  /** 何が起きたか (利用者向けの 1 文)。 */
  summary: string;
  /** サーバーが返した理由などの詳細。無ければ null。 */
  detail: string | null;
}

const SUMMARY: Record<RequestFailureKind, string> = {
  offline: "ネットワークに接続されていないため取得できませんでした。",
  network: "サーバーに接続できないため取得できませんでした。",
  timeout: "サーバーの応答が時間内に無かったため取得できませんでした。",
  unauthorized: "ログインの有効期限が切れているため取得できませんでした。",
  forbidden: "この操作を行う権限がないため取得できませんでした。",
  not_found: "取得先が見つかりませんでした。",
  server: "サーバーでエラーが発生したため取得できませんでした。",
  unknown: "取得できませんでした。",
};

export function describeRequestFailure(error: unknown): RequestFailure {
  const e = error as { name?: unknown; kind?: unknown; status?: unknown; detail?: unknown } | null;
  if (e && e.name === "NetworkError" && (e.kind === "offline" || e.kind === "network" || e.kind === "timeout")) {
    return { kind: e.kind, summary: SUMMARY[e.kind], detail: null };
  }
  if (e && typeof e.status === "number" && e.status > 0) {
    const status = e.status;
    const kind: RequestFailureKind =
      status === 401 ? "unauthorized"
        : status === 403 ? "forbidden"
          : status === 404 ? "not_found"
            : status >= 500 ? "server"
              : "unknown";
    const detail = typeof e.detail === "string" && e.detail ? e.detail : null;
    return { kind, summary: SUMMARY[kind], detail };
  }
  const detail = error instanceof Error ? error.message : error != null ? String(error) : null;
  return { kind: "unknown", summary: SUMMARY.unknown, detail };
}
