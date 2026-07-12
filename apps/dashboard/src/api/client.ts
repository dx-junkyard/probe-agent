const BASE = "/api";

let sessionToken: string | null = localStorage.getItem("probe_session_token");
let currentSystemId: number | null = (() => {
  const v = localStorage.getItem("probe_system_id");
  return v ? Number(v) : null;
})();

export function setSessionToken(token: string | null) {
  sessionToken = token;
  if (token) localStorage.setItem("probe_session_token", token);
  else localStorage.removeItem("probe_session_token");
}

export function getSessionToken() {
  return sessionToken;
}

export function setSystemId(id: number | null) {
  currentSystemId = id;
  if (id !== null) localStorage.setItem("probe_system_id", String(id));
  else localStorage.removeItem("probe_system_id");
}

export function getSystemId() {
  return currentSystemId;
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (sessionToken) h["Authorization"] = `Bearer ${sessionToken}`;
  if (currentSystemId !== null) h["X-Probe-System-Id"] = String(currentSystemId);
  return h;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  code?: string;
  nextAction?: string;
  constructor(status: number, detail: unknown) {
    const structured = detail && typeof detail === "object"
      ? detail as { message?: unknown; code?: unknown; next_action?: unknown }
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
    this.nextAction = typeof structured?.next_action === "string"
      ? structured.next_action
      : undefined;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: headers(),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, data.detail ?? res.statusText);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};
