import { UNAUTHORIZED_EVENT } from "@/lib/auth-events";

const BASE = "/api";

let currentSystemId: number | null = (() => {
  const v = localStorage.getItem("probe_system_id");
  return v ? Number(v) : null;
})();

export function setSystemId(id: number | null) {
  currentSystemId = id;
  if (id !== null) localStorage.setItem("probe_system_id", String(id));
  else localStorage.removeItem("probe_system_id");
}

export function getSystemId() {
  return currentSystemId;
}

function cookieValue(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const candidate = part.trim();
    if (candidate.startsWith(prefix)) {
      return decodeURIComponent(candidate.slice(prefix.length));
    }
  }
  return null;
}

function headers(method: string): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (currentSystemId !== null) h["X-Probe-System-Id"] = String(currentSystemId);
  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method.toUpperCase())) {
    const csrf = cookieValue("probe_csrf");
    if (csrf) h["X-CSRF-Token"] = csrf;
  }
  return h;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  code?: string;
  /** Epic #412 §4.1: a refused capability gate returns its finite code as
   * `denial_code` (never `code`), because the same 409 status is also used by
   * #415's lifecycle refusal, which carries `code`. The two refusals are
   * deliberately different bodies -- approval and execution mode are
   * independent facts (§7.5) and the developer's next action differs -- so
   * both are surfaced here rather than merged into one field. */
  denialCode?: string;
  nextAction?: string;
  /** Issue #451 (`docs/01-specifications/capabilities/ai-discussion-adapter.md` §2.8): a domain write endpoint's
   * validation diagnostic, structural and never text-derived. `fieldPath`
   * names the exact offending field (empty = "no specific field" -- the
   * caller must show it as a whole-form error rather than guess a field
   * from `detail`, Principle 6); `section` groups it (e.g. a Journey's
   * `"steps"`, a Requirement's `"acceptance_criteria"`). Both are `""`,
   * never `undefined`, when the server did not send them (an older server,
   * or a genuinely field-less failure) -- `""` is itself the documented
   * "no specific field" value, so there is nothing to distinguish. */
  fieldPath: string;
  section: string;
  constructor(status: number, detail: unknown) {
    const structured = detail && typeof detail === "object"
      ? detail as {
          message?: unknown; code?: unknown; next_action?: unknown;
          denial_code?: unknown; field_path?: unknown; section?: unknown;
        }
      : null;
    const message = typeof structured?.message === "string"
      ? structured.message
      : typeof detail === "string"
        ? detail
        : "Request failed";
    super(message);
    this.status = status;
    this.detail = message;
    this.code = typeof structured?.code === "string" ? structured.code : undefined;
    this.denialCode = typeof structured?.denial_code === "string"
      ? structured.denial_code
      : undefined;
    this.nextAction = typeof structured?.next_action === "string"
      ? structured.next_action
      : undefined;
    this.fieldPath = typeof structured?.field_path === "string" ? structured.field_path : "";
    this.section = typeof structured?.section === "string" ? structured.section : "";
  }
}

/**
 * Issue #466 (UX-05/06): サーバーに到達できなかった (応答が無い) ことを表す。
 *
 * `ApiError` は「サーバーが応答し、拒否した」事実で、こちらは「応答そのものが
 * 無かった」事実。後者を 0 件・未作成・未ログインとして扱ってはならないし、
 * 書き込みでは「保存されなかった」とも断定できない (リクエストは届いて処理
 * された後に応答だけ失われたかもしれない) — `resultUnknown` がそれを示す。
 */
export class NetworkError extends Error {
  kind: "offline" | "network" | "timeout";
  /** 書き込み要求で応答を失った: サーバー側で処理済みの可能性がある。 */
  resultUnknown: boolean;
  constructor(kind: NetworkError["kind"], method: string) {
    const unsafe = !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
    const base = kind === "timeout"
      ? "サーバーの応答が時間内にありませんでした。"
      : kind === "offline"
        ? "ネットワークに接続されていません。"
        : "サーバーに接続できませんでした。";
    super(unsafe ? `${base}処理結果は不明です。再送する前に一覧で結果を確認してください。` : base);
    this.name = "NetworkError";
    this.kind = kind;
    this.resultUnknown = unsafe;
  }
}

// 利用中のセッション失効 (401) を AuthProvider へ伝える。ページ遷移はせず、
// その場で再認証させる (未保存入力を保全するため)。認証系エンドポイント
// 自身の 401 (ログイン失敗・未ログイン判定) は失効ではないので通知しない。

function notifyUnauthorized(path: string) {
  if (path.startsWith("/auth/")) return;
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: { path } }));
}

export interface RequestOptions {
  /** 読み取り要求のタイムアウト (ms)。書き込みには付けない — 応答待ちを
   * 打ち切っても処理は止まらず、結果が不明になるだけなので。 */
  timeoutMs?: number;
}

async function request<T>(method: string, path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
  const controller = options.timeoutMs ? new AbortController() : null;
  let timedOut = false;
  const timer = controller
    ? setTimeout(() => { timedOut = true; controller.abort(); }, options.timeoutMs)
    : null;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: headers(method),
      credentials: "include",
      body: body !== undefined ? JSON.stringify(body) : undefined,
      ...(controller ? { signal: controller.signal } : {}),
    });
  } catch {
    const offline = typeof navigator !== "undefined" && navigator.onLine === false;
    throw new NetworkError(timedOut ? "timeout" : offline ? "offline" : "network", method);
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) notifyUnauthorized(path);
    throw new ApiError(res.status, data.detail ?? res.statusText);
  }
  return data as T;
}

async function requestBlob(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<Blob> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: headers(method),
    credentials: "include",
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) notifyUnauthorized(path);
    throw new ApiError(res.status, data.detail ?? res.statusText);
  }
  return res.blob();
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>("GET", path, undefined, options),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
  postBlob: (path: string, body?: unknown, signal?: AbortSignal) =>
    requestBlob("POST", path, body, signal),
};
