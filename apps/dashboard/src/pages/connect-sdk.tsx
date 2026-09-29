import { useRef, useState } from "react";
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
import { copyText, selectElementText } from "@/lib/clipboard";

// Issue #466 (UX-01): `/tokens/me` は利用者の全 token を System を問わず返す。
// 「発行先の System」を表示している同じカードに一覧を並べると、一覧も同じ
// System の token だと読めてしまい、別 System の同名 token を失効させうる。
// 一覧は既定で選択中の System に絞り、「全 System」は明示的な切り替えにする。
// System に紐づかない token は System の token ではないので、どちらの表示でも
// 専用の区分に分ける。
type TokenScope = "current" | "all";

type SystemLookup = (systemId: number | null) => string;

function useSystemLabel(): SystemLookup {
  const { systems } = useAuth();
  return (systemId) => {
    if (systemId == null) return "System 未関連";
    const name = systems.find((s) => s.id === systemId)?.name;
    return name ? `${name}(#${systemId})` : `System #${systemId}`;
  };
}

export default function ConnectSdkPage() {
  const { systemId, systems } = useAuth();
  const { data: tokens, isLoading } = useMyTokens();
  const issueToken = useIssueToken();
  const revokeToken = useRevokeMyToken();
  const [newTokenName, setNewTokenName] = useState("");
  const [expDays, setExpDays] = useState(90);
  const [issued, setIssued] = useState<TokenOut | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<TokenOut | null>(null);
  const [scope, setScope] = useState<TokenScope>("current");
  // UX-08: コピーの結果。成功は「コピーした」事実、失敗は手動コピーの導線。
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const issuedTokenRef = useRef<HTMLElement>(null);
  const systemLabel = useSystemLabel();

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
      setCopyState("idle");
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
  const allApiTokens = allTokens.filter(isApiToken);
  // 選択 System が無いときは「現在の System」に絞る対象が存在しないので、
  // 全 System 表示に倒す (空一覧を「token がない」と読ませない)。
  const effectiveScope: TokenScope = systemId == null ? "all" : scope;
  const systemBoundApiTokens = allApiTokens.filter(t => t.system_id != null);
  const apiTokens = sortTokensByUsability(
    effectiveScope === "current"
      ? systemBoundApiTokens.filter(t => t.system_id === systemId)
      : systemBoundApiTokens,
  );
  const unboundApiTokens = sortTokensByUsability(allApiTokens.filter(t => t.system_id == null));
  const otherSystemCount = systemBoundApiTokens.filter(t => t.system_id !== systemId).length;
  const sessionTokens = allTokens.filter(t => !isApiToken(t));

  const handleCopyIssued = async () => {
    if (!issued?.token) return;
    if (await copyText(issued.token)) {
      setCopyState("copied");
      toast.success("コピーしました");
    } else {
      // token は表示したまま保持する。失敗時に消すと二度と取り出せない。
      setCopyState("failed");
      selectElementText(issuedTokenRef.current);
      toast.error("コピーできませんでした。表示中の token を手動でコピーしてください。");
    }
  };

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
                <code
                  ref={issuedTokenRef}
                  className="flex-1 rounded bg-background px-3 py-2 text-xs font-mono break-all border select-all"
                  data-testid="issued-token-value"
                >
                  {issued.token}
                </code>
                <Button
                  size="icon" variant="outline"
                  aria-label="発行した token をコピー"
                  data-testid="issued-token-copy"
                  onClick={handleCopyIssued}
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
              <p
                role="status"
                aria-live="polite"
                data-testid="issued-token-copy-status"
                className={copyState === "failed" ? "text-xs text-destructive" : "text-xs text-emerald-900/80 dark:text-emerald-100/80"}
              >
                {copyState === "copied" && "クリップボードにコピーしました。"}
                {copyState === "failed" && (
                  <>
                    クリップボードにコピーできませんでした(ブラウザーが許可していない可能性があります)。
                    token は選択状態にしてあります。Ctrl+C(Mac は ⌘+C)でコピーしてから画面を離れてください。
                    <button
                      type="button"
                      className="ml-1 underline cursor-pointer"
                      data-testid="issued-token-select"
                      onClick={() => selectElementText(issuedTokenRef.current)}
                    >
                      もう一度選択する
                    </button>
                  </>
                )}
              </p>
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

          <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-4">
            <h3 className="text-sm font-medium" data-testid="sdk-token-list-heading">
              {effectiveScope === "current" && systemId != null
                ? `${systemLabel(systemId)} の API token`
                : "全 System の API token"}
            </h3>
            {systemId != null && (
              <div role="group" aria-label="表示する token の範囲" className="inline-flex rounded-md border p-0.5">
                <Button
                  size="sm"
                  variant={effectiveScope === "current" ? "default" : "ghost"}
                  aria-pressed={effectiveScope === "current"}
                  data-testid="sdk-token-scope-current"
                  onClick={() => setScope("current")}
                >
                  選択中の System
                </Button>
                <Button
                  size="sm"
                  variant={effectiveScope === "all" ? "default" : "ghost"}
                  aria-pressed={effectiveScope === "all"}
                  data-testid="sdk-token-scope-all"
                  onClick={() => setScope("all")}
                >
                  全 System{otherSystemCount > 0 ? `(他 ${otherSystemCount} 件)` : ""}
                </Button>
              </div>
            )}
          </div>

          {isLoading ? (
            <div className="space-y-2">{[1,2].map(i => <Skeleton key={i} className="h-12 w-full" />)}</div>
          ) : !apiTokens.length ? (
            <p className="text-sm text-muted-foreground text-center py-4" data-testid="sdk-token-empty">
              {effectiveScope === "current"
                ? "この System の SDK 用 API token はまだありません。"
                : "SDK 用の API token はまだありません。"}
            </p>
          ) : (
            <TokenTable
              tokens={apiTokens}
              testIdPrefix="sdk-token"
              onRevoke={setConfirmRevoke}
              systemLabel={systemLabel}
            />
          )}

          {!isLoading && unboundApiTokens.length > 0 && (
            <div className="space-y-2" data-testid="sdk-token-unbound">
              <h3 className="text-sm font-medium">System 未関連の API token</h3>
              <p className="text-xs text-muted-foreground">
                どの System にも紐づいていない token です。選択中の System の token ではありません。
              </p>
              <TokenTable
                tokens={unboundApiTokens}
                testIdPrefix="sdk-token-unbound"
                onRevoke={setConfirmRevoke}
                systemLabel={systemLabel}
              />
            </div>
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
                  systemLabel={systemLabel}
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
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-md border bg-muted/40 p-3 text-sm">
              <dt className="text-muted-foreground">Token</dt>
              <dd data-testid="revoke-confirm-name">
                {confirmRevoke.name ?? `#${confirmRevoke.id}`}(#{confirmRevoke.id})
              </dd>
              <dt className="text-muted-foreground">対象 System</dt>
              <dd data-testid="revoke-confirm-system">{systemLabel(confirmRevoke.system_id)}</dd>
              <dt className="text-muted-foreground">作成</dt>
              <dd>{formatTimestamp(confirmRevoke.created_at)}</dd>
            </dl>
            <p className="text-sm">
              {confirmRevoke.system_id != null
                ? `${systemLabel(confirmRevoke.system_id)} に紐づく「${confirmRevoke.name ?? `#${confirmRevoke.id}`}」を失効させます。`
                : `System に紐づかない「${confirmRevoke.name ?? `#${confirmRevoke.id}`}」を失効させます。`}
              この token を使っている SDK は直ちに認証できなくなり、失効は取り消せません。
            </p>
            <div className="flex justify-end gap-2">
              {/* 破壊的操作の確認: 初期フォーカスは取り消し側に置く。 */}
              <Button variant="outline" data-autofocus="" onClick={() => setConfirmRevoke(null)}>
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
  systemLabel,
}: {
  tokens: TokenOut[];
  testIdPrefix: string;
  onRevoke: (token: TokenOut) => void;
  systemLabel: SystemLookup;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 font-medium text-muted-foreground">名前</th>
            <th className="pb-2 font-medium text-muted-foreground">System</th>
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
              <td className="py-2 text-xs" data-testid={`${testIdPrefix}-system-${t.id}`}>
                {systemLabel(t.system_id)}
              </td>
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
                    aria-label={`${systemLabel(t.system_id)} の token「${t.name ?? `#${t.id}`}」を失効させる`}
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
        onClick={async () => {
          if (await copyText(children)) toast.success("コピーしました");
          else toast.error("コピーできませんでした。コードを選択して手動でコピーしてください。");
        }}
        aria-label={`${lang} のコードをコピー`}
        title={`${lang} のコードをコピー`}
      >
        <Copy className="h-3 w-3" />
      </Button>
    </div>
  );
}
