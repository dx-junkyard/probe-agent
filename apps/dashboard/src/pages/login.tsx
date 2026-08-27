import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "@/api/auth";
import { useBootstrapStatus } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";

export default function LoginPage() {
  const { user, login, loading } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  // Issue #265: "phase 0" -- before any admin user exists there is no
  // credential that can ever make this form succeed (only an env-var +
  // restart bootstrap can create one), so replace the form with static
  // instructions instead of leaving a login screen that can only ever
  // reject. Callable with no session and no System (`GET
  // /auth/bootstrap-status`).
  const { data: bootstrap, isLoading: bootstrapLoading } = useBootstrapStatus();

  if (loading) return null;
  if (user) return <Navigate to="/" replace />;

  const showBootstrapGuide = !bootstrapLoading && bootstrap?.admin_exists === false;

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setPending(true);
    const fd = new FormData(e.currentTarget);
    try {
      await login(fd.get("username") as string, fd.get("password") as string);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 h-10 w-10 rounded-xl bg-primary flex items-center justify-center">
            <span className="text-lg font-bold text-primary-foreground">P</span>
          </div>
          <CardTitle className="text-xl">Probe Agent</CardTitle>
          <CardDescription>
            {showBootstrapGuide ? "初期設定が必要です" : "Sign in to your dashboard"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {showBootstrapGuide ? (
            // Issue #265: no admin exists yet, so no username/password can
            // succeed here -- the only path is the env-var + restart
            // bootstrap (`db._bootstrap_admin`). Purely client-fixed
            // copy (not server-supplied), so it is plain Japanese text
            // per the #240/#266 catalog policy, not a state_messages entry.
            <div className="space-y-3 text-sm" data-testid="login-bootstrap-guide">
              <p className="font-medium">管理者アカウントがまだ作成されていません。</p>
              {bootstrap?.environment === "production" ? (
                <p className="text-muted-foreground" data-testid="login-bootstrap-guide-production">
                  システム管理者に初回セットアップの実施を依頼してください。
                </p>
              ) : (
                <div className="space-y-2 text-muted-foreground" data-testid="login-bootstrap-guide-detail">
                  <p>
                    Control Server 起動時に環境変数{" "}
                    <code className="rounded bg-muted px-1 py-0.5 text-xs">CONTROL_ADMIN_USERNAME</code>
                    {" "}と{" "}
                    <code className="rounded bg-muted px-1 py-0.5 text-xs">CONTROL_ADMIN_PASSWORD</code>
                    {" "}を設定し、サーバーを再起動してください。
                  </p>
                  <p>再起動時に、その内容で最初の管理者アカウントが自動的に作成されます。</p>
                  {bootstrap?.llm_configured === false && (
                    <p className="text-xs">
                      あわせて、Feature Intelligence 機能を使う場合は{" "}
                      <code className="rounded bg-muted px-1 py-0.5 text-xs">LLM_PROVIDER</code>
                      {" / "}
                      <code className="rounded bg-muted px-1 py-0.5 text-xs">LLM_API_KEY</code>
                      {" "}も設定してください（未設定です）。
                    </p>
                  )}
                </div>
              )}
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              {error && (
                <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error}
                </div>
              )}
              <div className="space-y-2">
                <Label htmlFor="username">Username</Label>
                <Input id="username" name="username" required autoFocus />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input id="password" name="password" type="password" required />
              </div>
              <Button type="submit" className="w-full" disabled={pending}>
                {pending ? "Signing in..." : "Sign in"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
