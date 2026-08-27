import { useState } from "react";
import { Link } from "react-router-dom";
import { useMyTokens, useIssueToken, useRevokeMyToken } from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { TokenStatusBadge } from "@/components/token-status-badge";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import {
  formatExpiresIn,
  isApiToken,
  isTokenUsable,
  sortTokensByUsability,
} from "@/lib/token-display";
import type { TokenOut } from "@/api/types";
import { Copy, Key, Trash2, ArrowRight } from "lucide-react";
import { getClientServerUrl } from "@/lib/env";

export default function ConnectSdkPage() {
  const { systemId, systems } = useAuth();
  const { data: tokens, isLoading } = useMyTokens();
  const issueToken = useIssueToken();
  const revokeToken = useRevokeMyToken();
  const [newTokenName, setNewTokenName] = useState("");
  const [expDays, setExpDays] = useState(90);
  const [issued, setIssued] = useState<TokenOut | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<TokenOut | null>(null);

  const systemName = systems.find(s => s.id === systemId)?.name ?? null;

  const handleIssue = async () => {
    if (!newTokenName.trim() || !systemId) return;
    try {
      const t = await issueToken.mutateAsync({
        name: newTokenName.trim(),
        system_id: systemId,
        expires_in_days: expDays,
      });
      setIssued(t.token ? t : null);
      setNewTokenName("");
      toast.success("Token を発行しました");
    } catch (err) { toast.error(String(err)); }
  };

  const handleRevoke = async (token: TokenOut) => {
    setConfirmRevoke(null);
    try {
      await revokeToken.mutateAsync(token.id);
      toast.success("Token を失効させました");
    } catch (err) { toast.error(String(err)); }
  };

  const serverUrl = getClientServerUrl();

  // Issue #368: the SDK flow shows API tokens by default; login sessions are a
  // different credential (issued by /auth/login, not usable as PROBE_API_KEY)
  // and live in their own clearly-labelled section.
  const allTokens = tokens ?? [];
  const apiTokens = sortTokensByUsability(allTokens.filter(isApiToken));
  const sessionTokens = allTokens.filter(t => !isApiToken(t));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Connect SDK</h1>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">1. SDK をインストールする</CardTitle>
          </CardHeader>
          <CardContent>
            <CodeBlock lang="bash">{`pip install probe-agent`}</CodeBlock>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">2. 環境変数を設定する</CardTitle>
          </CardHeader>
          <CardContent>
            <CodeBlock lang="bash">{`export PROBE_SERVER_URL="${serverUrl}"
export PROBE_API_KEY="<発行した API token>"`}</CodeBlock>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">3. @probe デコレータを使う</CardTitle>
          </CardHeader>
          <CardContent>
            <CodeBlock lang="python">{`from probe_agent import probe

@probe(component_id="my-component")
def summarize(text: str) -> str:
    # Your function logic here
    return result`}</CodeBlock>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Key className="h-4 w-4" />
            SDK 用 API token
          </CardTitle>
          <CardDescription>
            SDK 認証(<code className="font-mono">PROBE_API_KEY</code>)に使う API token です。
            発行した token は選択中の System に紐づき、その System にだけ Trace を送信できます。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-end gap-3">
            <div className="flex-1 space-y-2">
              <Label htmlFor="new-token-name">Token 名</Label>
              <Input
                id="new-token-name"
                value={newTokenName}
                onChange={e => setNewTokenName(e.target.value)}
                placeholder="my-service"
              />
            </div>
            <div className="w-32 space-y-2">
              <Label htmlFor="new-token-days">有効期限(日)</Label>
              <Input
                id="new-token-days"
                type="number"
                value={expDays}
                onChange={e => setExpDays(Number(e.target.value))}
                min={1}
              />
            </div>
            <Button
              onClick={handleIssue}
              disabled={issueToken.isPending || !newTokenName.trim() || !systemId}
              title={!systemId ? "Token を発行する前に System を選択してください" : undefined}
            >
              Token を発行
            </Button>
          </div>

          <p className="text-xs text-muted-foreground" data-testid="issue-token-target-system">
            {systemName
              ? `発行先の System: ${systemName}(#${systemId})`
              : "発行先の System が未選択です。"}
          </p>

          {!systemId && (
            <p className="text-xs text-destructive" data-testid="issue-token-no-system-reason">
              ヘッダーから System を選択してから token を発行してください。
            </p>
          )}

          {issued?.token && (
            <div
              className="rounded-md border border-emerald-200 bg-emerald-50 dark:bg-emerald-950/20 dark:border-emerald-800 p-4 space-y-2"
              data-testid="issued-token-panel"
            >
              <p className="text-sm font-medium text-emerald-800 dark:text-emerald-200">
                Token を発行しました。この値が表示されるのはこの一度だけです。今すぐコピーしてください。
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded bg-background px-3 py-2 text-xs font-mono break-all border">
                  {issued.token}
                </code>
                <Button
                  size="icon" variant="outline"
                  aria-label="発行した token をコピー"
                  onClick={() => {
                    navigator.clipboard.writeText(issued.token as string);
                    toast.success("コピーしました");
                  }}
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
              <ul className="text-xs text-emerald-900/80 dark:text-emerald-100/80 space-y-0.5">
                <li data-testid="issued-token-expiry">
                  有効期限: {formatExpiresIn(issued)}
                  {issued.expires_at != null && `(${formatTimestamp(issued.expires_at)})`}
                </li>
                <li data-testid="issued-token-system">
                  対象 System: {systemName ? `${systemName}(#${issued.system_id})` : `#${issued.system_id}`}
                  — この token で送信した Trace はこの System にだけ記録されます。
                </li>
                <li>
                  用途: <code className="font-mono">PROBE_API_KEY</code> に設定してください。
                </li>
              </ul>
            </div>
          )}

          {isLoading ? (
            <div className="space-y-2">{[1,2].map(i => <Skeleton key={i} className="h-12 w-full" />)}</div>
          ) : !apiTokens.length ? (
            <p className="text-sm text-muted-foreground text-center py-4" data-testid="sdk-token-empty">
              SDK 用の API token はまだありません。
            </p>
          ) : (
            <TokenTable
              tokens={apiTokens}
              testIdPrefix="sdk-token"
              onRevoke={setConfirmRevoke}
            />
          )}
        </CardContent>
      </Card>

      <Card data-testid="session-token-card">
        <CardHeader>
          <CardTitle className="text-base">ログインセッション</CardTitle>
          <CardDescription>
            ダッシュボードへのログインで自動発行される <code className="font-mono">session</code> token です。
            SDK 接続には使えません。<code className="font-mono">PROBE_API_KEY</code> には上の API token を設定してください。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {sessionTokens.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="session-token-empty">
              表示できるログインセッションはありません。
            </p>
          ) : (
            <details data-testid="session-token-details">
              <summary className="cursor-pointer text-sm text-muted-foreground">
                ログインセッションを表示({sessionTokens.length} 件)
              </summary>
              <div className="mt-3">
                <TokenTable
                  tokens={sortTokensByUsability(sessionTokens)}
                  testIdPrefix="session-token"
                  onRevoke={setConfirmRevoke}
                />
              </div>
            </details>
          )}
        </CardContent>
      </Card>

      <Dialog open={confirmRevoke !== null} onOpenChange={() => setConfirmRevoke(null)}>
        <DialogHeader>
          <DialogTitle>Token を失効させますか?</DialogTitle>
        </DialogHeader>
        {confirmRevoke && (
          <div className="space-y-4" data-testid="revoke-confirm-dialog">
            <p className="text-sm">
              「{confirmRevoke.name ?? `#${confirmRevoke.id}`}」を失効させます。
              この token を使っている SDK は直ちに認証できなくなり、失効は取り消せません。
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setConfirmRevoke(null)}>
                キャンセル
              </Button>
              <Button
                variant="destructive"
                data-testid="revoke-confirm-button"
                onClick={() => handleRevoke(confirmRevoke)}
              >
                失効させる
              </Button>
            </div>
          </div>
        )}
      </Dialog>

      <Link
        to="/setup-guide"
        className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
        data-testid="connect-sdk-setup-guide-link"
      >
        Setup Guide で接続を確認する
        <ArrowRight className="h-3.5 w-3.5" />
      </Link>
    </div>
  );
}

function TokenTable({
  tokens,
  testIdPrefix,
  onRevoke,
}: {
  tokens: TokenOut[];
  testIdPrefix: string;
  onRevoke: (token: TokenOut) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 font-medium text-muted-foreground">名前</th>
            <th className="pb-2 font-medium text-muted-foreground">種別</th>
            <th className="pb-2 font-medium text-muted-foreground">作成</th>
            <th className="pb-2 font-medium text-muted-foreground">有効期限</th>
            <th className="pb-2 font-medium text-muted-foreground">状態</th>
            <th className="pb-2 font-medium text-muted-foreground text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {tokens.map(t => (
            <tr key={t.id} className="border-b last:border-0" data-testid={`${testIdPrefix}-row-${t.id}`}>
              <td className="py-2">{t.name ?? `#${t.id}`}</td>
              <td className="py-2"><Badge variant="outline">{t.kind}</Badge></td>
              <td className="py-2 text-xs text-muted-foreground">{formatTimestamp(t.created_at)}</td>
              <td className="py-2 text-xs text-muted-foreground">
                <div data-testid={`${testIdPrefix}-expires-relative-${t.id}`}>{formatExpiresIn(t)}</div>
                <div data-testid={`${testIdPrefix}-expires-absolute-${t.id}`}>
                  {t.expires_at == null ? "—" : formatTimestamp(t.expires_at)}
                </div>
              </td>
              <td className="py-2">
                <TokenStatusBadge status={t.status} data-testid={`${testIdPrefix}-status-${t.id}`} />
              </td>
              <td className="py-2 text-right">
                {isTokenUsable(t.status) && (
                  <Button
                    variant="ghost" size="icon" className="h-7 w-7"
                    aria-label={`token「${t.name ?? `#${t.id}`}」を失効させる`}
                    onClick={() => onRevoke(t)}
                  >
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CodeBlock({ children, lang }: { children: string; lang: string }) {
  return (
    <div className="relative">
      <pre className="rounded-md bg-muted p-4 overflow-x-auto text-sm font-mono">
        <code>{children}</code>
      </pre>
      <Button
        variant="ghost" size="icon" className="absolute top-2 right-2 h-7 w-7"
        onClick={() => { navigator.clipboard.writeText(children); toast.success("コピーしました"); }}
        aria-label={`${lang} のコードをコピー`}
        title={`${lang} のコードをコピー`}
      >
        <Copy className="h-3 w-3" />
      </Button>
    </div>
  );
}
