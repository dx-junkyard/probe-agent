// Issue #466 (UX-09): ログインを挟んでも、共有リンクの目的地へ戻す。
//
// 復帰先はアプリ内部のパスに限る。`next` は URL から来る値なので、外部 URL
// (`https://...`)、プロトコル相対 (`//evil`)、バックスラッシュ混じり、
// ログイン画面自身は受け付けない (open redirect とループの防止)。

export const RETURN_TO_PARAM = "next";

export function safeReturnPath(raw: string | null | undefined): string | null {
  if (!raw) return null;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return null;
  // 制御文字を含むものは拒否する。
  for (let i = 0; i < raw.length; i += 1) {
    const code = raw.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) return null;
  }
  let url: URL;
  try {
    url = new URL(raw, "http://app.invalid");
  } catch {
    return null;
  }
  if (url.origin !== "http://app.invalid") return null;
  if (url.pathname === "/login") return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** ログイン画面への遷移先 (現在地を `next` に持たせる)。 */
export function loginPathFor(location: { pathname: string; search: string; hash: string }): string {
  const current = `${location.pathname}${location.search}${location.hash}`;
  const safe = safeReturnPath(current);
  if (!safe || safe === "/") return "/login";
  return `/login?${RETURN_TO_PARAM}=${encodeURIComponent(safe)}`;
}
