import { createContext, useContext, useState, useCallback, useEffect, useRef, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { setSystemId, getSystemId, api } from "./client";
import { UNAUTHORIZED_EVENT } from "@/lib/auth-events";
import { describeRequestFailure } from "@/lib/request-state";
import type { MeResponse, UserOut, SystemOut } from "./types";

// Issue #466 (UX-06): 起動時と利用中の失敗を別々の状態で持つ。
//
//   - 認証を確認できなかった (サーバー到達不能・タイムアウト・5xx) は
//     `authError`。未ログインと同じ扱いにしてログイン画面へ送らない —
//     正しい資格情報を持っていてもログインできないため。
//   - `/systems` の取得失敗は `systemsStatus: "error"`。以前は握りつぶして
//     空配列のままにしていたので、「System が作成されていません」が
//     表示されていた。成功した 0 件だけが「未作成」である。
//   - 利用中の 401 は `sessionExpired`。ページを離れずに再認証させ、画面上の
//     未保存入力を保全する (AppLayout の再認証ダイアログ)。多数の要求が同時に
//     401 を返しても、状態は 1 つの boolean なので再認証は 1 回だけ出る。

/** 起動時の読み取りのタイムアウト。これを超えたら説明と再試行を出す。 */
export const AUTH_BOOTSTRAP_TIMEOUT_MS = 15_000;

export type SystemsStatus = "loading" | "ready" | "error";

interface AuthState {
  user: UserOut | null;
  isAdmin: boolean;
  loading: boolean;
  systemId: number | null;
  systems: SystemOut[];
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  selectSystem: (id: number) => void;
  refreshSystems: () => Promise<void>;
  /** 認証状態を確認できなかった理由。null なら確認できている。 */
  authError?: string | null;
  /** `/systems` の取得状態。`ready` の 0 件だけが「System 未作成」。 */
  systemsStatus?: SystemsStatus;
  systemsError?: string | null;
  /** 利用中に 401 を受けた (セッション失効)。 */
  sessionExpired?: boolean;
  /** 起動時の確認をやり直す。 */
  retryBootstrap?: () => void;
  /** 利用者が自分でログアウトした直後か (ログイン後の復帰先を持たせない)。 */
  explicitLogout?: boolean;
}

const AuthContext = createContext<AuthState>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<UserOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [systemId, setSystemIdState] = useState<number | null>(getSystemId());
  const [systems, setSystems] = useState<SystemOut[]>([]);
  const [systemsStatus, setSystemsStatus] = useState<SystemsStatus>("loading");
  const [systemsError, setSystemsError] = useState<string | null>(null);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [explicitLogout, setExplicitLogout] = useState(false);
  // 一度でも System 一覧の取得が決着したか (成功または失敗)。最初の決着までは
  // 空配列を「0 件」として描かない。以後の再取得では画面を読み込み表示へ
  // 戻さない (開いているダイアログ等を消さないため)。
  const [systemsSettledOnce, setSystemsSettledOnce] = useState(false);
  // イベントハンドラー (401 通知) と login から最新のユーザーを読むための参照。
  // setUser と同じ場所で更新する。
  const userRef = useRef<UserOut | null>(null);

  /** 確認できたユーザー (未ログインなら null) を返す。確認できなかった場合も null。 */
  const fetchMe = useCallback(async (): Promise<UserOut | null> => {
    try {
      const me = await api.get<MeResponse>("/auth/me", { timeoutMs: AUTH_BOOTSTRAP_TIMEOUT_MS });
      setUser(me.user);
      userRef.current = me.user;
      setAuthError(null);
      return me.user;
    } catch (err) {
      const failure = describeRequestFailure(err);
      setUser(null);
      userRef.current = null;
      // 401 は「未ログイン」。到達不能・タイムアウト・5xx は未ログインではなく
      // 「確認できなかった」なので、ログイン画面へは送らない。
      setAuthError(
        failure.kind === "unauthorized"
          ? null
          : failure.summary.replace("取得できませんでした", "ログイン状態を確認できませんでした"),
      );
      return null;
    }
  }, []);

  const refreshSystems = useCallback(async () => {
    setSystemsStatus("loading");
    try {
      const s = await api.get<SystemOut[]>("/systems", { timeoutMs: AUTH_BOOTSTRAP_TIMEOUT_MS });
      setSystems(s);
      setSystemsStatus("ready");
      setSystemsSettledOnce(true);
      setSystemsError(null);
      if (s.length > 0 && !getSystemId()) {
        setSystemId(s[0].id);
        setSystemIdState(s[0].id);
      }
    } catch (err) {
      // 既に持っている一覧は消さない (再試行の失敗で画面を空にしない)。
      const failure = describeRequestFailure(err);
      setSystemsStatus("error");
      setSystemsSettledOnce(true);
      setSystemsError(failure.detail ? `${failure.summary}(${failure.detail})` : failure.summary);
    }
  }, []);

  // 起動時の確認。ユーザーが確定したら続けて System 一覧を取る。
  const bootstrap = useCallback(async () => {
    setLoading(true);
    const me = await fetchMe();
    if (me) await refreshSystems();
    setLoading(false);
  }, [fetchMe, refreshSystems]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const me = await fetchMe();
      if (me && !cancelled) await refreshSystems();
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [fetchMe, refreshSystems]);

  // 利用中の 401 (client が通知する)。ログインしていない間の 401 は失効ではない。
  useEffect(() => {
    const onUnauthorized = () => {
      if (userRef.current) setSessionExpired(true);
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const previousUserId = userRef.current?.id ?? null;
    await api.post<{ expires_at: number }>("/auth/login", { username, password });
    const next = await fetchMe();
    setSessionExpired(false);
    setExplicitLogout(false);
    if (next && next.id !== previousUserId) await refreshSystems();
    // 再認証で同じ利用者に戻ったときは、失敗していた取得をやり直すだけで
    // よい。別の利用者なら、前の利用者の cache を持ち越さない。
    if (previousUserId != null && next?.id !== previousUserId) qc.clear();
    else qc.invalidateQueries();
  }, [fetchMe, qc, refreshSystems]);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch { /* ignore */ }
    setExplicitLogout(true);
    setSystemId(null);
    setUser(null);
    userRef.current = null;
    setSystems([]);
    setSystemsStatus("loading");
    setSystemsSettledOnce(false);
    setSystemIdState(null);
    setSessionExpired(false);
    qc.clear();
  }, [qc]);

  const selectSystem = useCallback((id: number) => {
    setSystemId(id);
    setSystemIdState(id);
    qc.invalidateQueries();
  }, [qc]);

  const retryBootstrap = useCallback(() => { void bootstrap(); }, [bootstrap]);

  // System 一覧をまだ一度も取れていない間は読み込み中として扱う (ログイン済みの
  // 場合のみ)。「取得前の空配列」を「0 件」として描かないため。
  const initialSystemsPending = user != null && !systemsSettledOnce;

  return (
    <AuthContext.Provider
      value={{
        user,
        isAdmin: user?.role === "admin",
        loading: loading || initialSystemsPending,
        systemId,
        systems,
        login,
        logout,
        selectSystem,
        refreshSystems,
        authError,
        systemsStatus,
        systemsError,
        sessionExpired,
        retryBootstrap,
        explicitLogout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
