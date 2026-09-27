import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/api/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

// Issue #466 (UX-06): 認証・System 取得の失敗から再開するための表示。
//
// 「認証失効」「サーバー到達不能」「System 取得失敗」は次の操作が違うので、
// 3 つを別の表示にする:
//   - 到達不能 (AuthBootstrapError): ログインしても直らない。再試行が主操作。
//   - System 取得失敗 (SystemsLoadErrorBanner): ログインは有効。一覧だけ再取得。
//   - 認証失効 (ReauthDialog): 画面をそのまま残して再ログインし、入力を保全する。

/** 起動時の読み込み表示。長引いたら「待っている」ことを文章で伝える。 */
const AUTH_SLOW_NOTICE_MS = 5_000;

export function AuthLoadingScreen() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), AUTH_SLOW_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <div className="flex h-screen items-center justify-center p-4" data-testid="auth-loading">
      <div className="space-y-4 w-64" role="status" aria-live="polite">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <p className="text-sm text-muted-foreground">
          {slow
            ? "サーバーの応答を待っています。しばらく応答がない場合は、接続できない理由と再試行を表示します。"
            : "ログイン状態を確認しています…"}
        </p>
      </div>
    </div>
  );
}

export function AuthBootstrapError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md" role="alert" data-testid="auth-bootstrap-error">
        <CardHeader>
          <CardTitle as="h1" className="text-lg">Control Server に接続できません</CardTitle>
          <CardDescription>{message}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            ログインが切れたのではなく、サーバーから応答を得られていません。Control Server が起動しているか、
            ネットワークに接続されているかを確認してから再試行してください。
          </p>
          <Button onClick={onRetry} data-testid="auth-bootstrap-retry">再試行</Button>
        </CardContent>
      </Card>
    </div>
  );
}

export function SystemsLoadErrorBanner() {
  const { systemsStatus, systemsError, systems, refreshSystems } = useAuth();
  const [retrying, setRetrying] = useState(false);
  if (systemsStatus !== "error") return null;
  return (
    <div
      role="alert"
      data-testid="systems-load-error"
      className="flex flex-wrap items-center justify-between gap-2 border-b border-destructive/40 bg-destructive/5 px-4 py-2 text-sm"
    >
      <span>
        System 一覧を取得できませんでした。{systemsError ?? ""}
        {systems.length > 0
          ? " 表示中の一覧は前回取得した内容です。"
          : " System が未作成かどうかは、まだ分かりません。"}
      </span>
      <Button
        size="sm"
        variant="outline"
        disabled={retrying}
        onClick={async () => {
          setRetrying(true);
          try { await refreshSystems(); } finally { setRetrying(false); }
        }}
      >
        {retrying ? "再試行中…" : "System 一覧を再取得"}
      </Button>
    </div>
  );
}

/**
 * 利用中の 401 に対する再認証。ページは裏にマウントしたままなので、
 * フォームの未保存入力は失われない。Escape や背景クリックでは閉じない
 * (閉じても次の要求がまた 401 になるだけなので、再ログインかログアウトの
 * どちらかを選んでもらう)。
 */
export function ReauthDialog() {
  const { sessionExpired, user, login, logout } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  if (!sessionExpired) return null;

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    setPending(true);
    setError(null);
    try {
      await login(String(fd.get("username") ?? ""), String(fd.get("password") ?? ""));
    } catch (err) {
      setError(err instanceof Error ? err.message : "ログインできませんでした");
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open onOpenChange={() => { /* 選択を求める: 閉じない */ }} closeOnOverlayClick={false}>
      <DialogHeader>
        <DialogTitle>ログインの有効期限が切れました</DialogTitle>
        <DialogDescription>
          画面の入力内容はそのまま残っています。もう一度ログインすると、この画面で作業を続けられます。
        </DialogDescription>
      </DialogHeader>
      <form onSubmit={handleSubmit} className="space-y-4" data-testid="reauth-form">
        {error && (
          <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>
        )}
        <FormField label="ユーザー名">
          {(field) => (
            <Input {...field} name="username" defaultValue={user?.username ?? ""} autoComplete="username" required />
          )}
        </FormField>
        <FormField label="パスワード">
          {(field) => (
            <Input {...field} name="password" type="password" autoComplete="current-password" required data-autofocus="" />
          )}
        </FormField>
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => { void logout(); }}>
            ログアウトする(入力は破棄されます)
          </Button>
          <Button type="submit" disabled={pending} data-testid="reauth-submit">
            {pending ? "ログイン中…" : "再ログインして続ける"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
