import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Activity, AlertTriangle, CheckCircle2, FileDiff, LifeBuoy, RadioTower,
  RefreshCw, Wrench,
} from "lucide-react";
import { useConnectivityStatus, useInterviewSession } from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CodeBlock } from "@/components/code-block";
import { getClientServerUrl } from "@/lib/env";
import { formatTimestamp } from "@/lib/utils";
import {
  FRESHNESS_LABELS,
  FreshnessBadge,
  FreshnessDetail,
  LifecycleMilestone,
} from "@/components/connectivity-freshness";

// 実行形態は明示的な有限集合。開発者は自分に近いパターンを選ぶ。
type RuntimePattern = "host" | "compose" | "image" | "external";

const PATTERN_LABELS: Record<RuntimePattern, string> = {
  host: "ホストで直接実行",
  compose: "同じ Docker Compose 内",
  image: "既存イメージに SDK を追加",
  external: "別リポジトリ / 外部エンドポイント",
};

// 全体の流れ(Issue #165 の 9 項目を一続きの導線として提示する)。
const FLOW_STEPS: Array<{ title: string; detail: string }> = [
  {
    title: "パッチの目的を理解する",
    detail:
      "レビュー用差分は、承認済みのインタビュー提案から生成された patch です。対象リポジトリは自動では変更されていません。",
  },
  {
    title: "パッチの内容を確認する",
    detail:
      "変更は probe-agent: docstring メタデータと @probe 計装です。インタビュー画面で差分を確認し、.patch としてダウンロードできます。",
  },
  {
    title: "監視対象側の設定を確認する",
    detail:
      "SDK の導入、PROBE_SERVER_URL、認証トークンなど、patch とは別に必要な設定要素をこのページで確認します。",
  },
  {
    title: "自分の実行形態のサンプルを選ぶ",
    detail:
      "ホスト直接実行 / Docker Compose / 既存イメージ / 外部エンドポイントから、自分の状況に近いパターンを選びます。",
  },
  {
    title: "パッチと設定を適用する",
    detail:
      "git apply --check で確認してから git apply で適用し、環境変数・compose 設定を反映します。",
  },
  {
    title: "疎通確認を実行する",
    detail:
      "health check、手動 smoke trace、実 workload の順に確認します。結果はこのページの接続ステータスに反映されます。",
  },
  {
    title: "届かない場合は切り分ける",
    detail:
      "設定不足 / 認証失敗 / ネットワーク不達 / イベント未送信 / workload 未実行の観点で下のトラブルシューティングを確認します。",
  },
  {
    title: "問題なければ commit / PR へ進む",
    detail:
      "通常の開発フローで変更をレビューし、commit / pull request を作成します。probe-agent が対象リポジトリへ直接 push することはありません。",
  },
];

function ConnectivityStatusCard() {
  const { data, isFetching, refetch } = useConnectivityStatus(15_000);

  return (
    <Card data-testid="connectivity-status-card">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm flex items-center gap-2">
              <Activity className="h-4 w-4" /> 接続ステータス
            </CardTitle>
            <CardDescription>
              選択中のシステムが Control Server で受信した trace の観測事実です(自動更新)。
            </CardDescription>
          </div>
          <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={`h-4 w-4 mr-1 ${isFetching ? "animate-spin" : ""}`} />
            更新
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {!data ? (
          <p className="text-sm text-muted-foreground">読み込み中...</p>
        ) : (
          <>
            {/* Issue #370: the headline is the LIVE reading (freshness).
                `state` is a cumulative milestone and is shown below as
                history — a system silent for two weeks must not read 受信中
                just because it once connected. */}
            <div className="flex items-center gap-2">
              {data.freshness === "receiving_now" ? (
                <CheckCircle2 className="h-5 w-5 text-green-600" />
              ) : (
                <AlertTriangle
                  className={`h-5 w-5 ${data.freshness === "stale" ? "text-red-500" : data.freshness === "delayed" ? "text-amber-500" : "text-blue-500"}`}
                />
              )}
              <span className="font-medium" data-testid="connectivity-state-label">
                {FRESHNESS_LABELS[data.freshness]}
              </span>
              <FreshnessBadge freshness={data.freshness} />
            </div>
            <FreshnessDetail data={data} />
            <LifecycleMilestone data={data} />
            <div className="grid grid-cols-3 gap-2 text-sm">
              <div className="rounded-md border p-2">
                <div className="text-lg font-semibold">{data.real_trace_count}</div>
                <div className="text-xs text-muted-foreground">実 workload trace（累計）</div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-lg font-semibold">{data.smoke_trace_count}</div>
                <div className="text-xs text-muted-foreground">smoke trace（累計）</div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-lg font-semibold">{data.total_trace_count}</div>
                <div className="text-xs text-muted-foreground">合計（累計）</div>
              </div>
            </div>
            {data.state === "no_signal" && (
              <p className="text-xs text-amber-700 dark:text-amber-300">
                このシステムはまだ一度も trace / signal を受信していません。
                {data.materialized_session_ids.length > 0 && (
                  <> レビュー用差分は生成済みです(セッション {data.materialized_session_ids.join(", ")})。
                  patch の適用と下記の設定・疎通確認を進めてください。</>
                )}
              </p>
            )}
            {data.state === "smoke_only" && (
              <p className="text-xs text-blue-700 dark:text-blue-300">
                手動の smoke trace は届いています(受信経路は正常)。実 workload からの trace は
                まだ届いていないため、@probe を適用したアプリを実際に動かして確認してください。
              </p>
            )}
            <p className="text-[11px] text-muted-foreground">
              smoke trace は component_id が <code className="font-mono">{data.smoke_component_id}</code> の
              trace として区別されます(完全一致)。
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function SessionContextBanner({ sessionId }: { sessionId: number }) {
  const { data: session } = useInterviewSession(sessionId);
  if (!session) return null;
  const hasDiff = !!session.materialization_diff;
  return (
    <Card className="border-primary/40" data-testid="setup-guide-session-context">
      <CardContent className="pt-4 space-y-1 text-sm">
        <div className="flex items-center gap-2 flex-wrap">
          <FileDiff className="h-4 w-4" />
          <span className="font-medium">インタビューセッション #{session.id} からの続きです</span>
          <Badge variant={hasDiff ? "success" : "outline"}>
            {hasDiff ? "レビュー用差分 生成済み" : "差分は未生成"}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          Snapshot {session.snapshot_id}
          {session.snapshot_commit_sha && (
            <> · commit <code className="font-mono">{session.snapshot_commit_sha.slice(0, 8)}</code></>
          )}
          {session.materialized_at && <> · 差分生成 {formatTimestamp(session.materialized_at)}</>}
        </p>
        <p className="text-xs text-muted-foreground">
          patch のダウンロードと差分の確認は{" "}
          <Link className="underline" to={`/interview?session=${session.id}`}>
            インタビュー画面のレビュー用差分カード
          </Link>{" "}
          から行えます。このページでは、patch 適用後に監視対象を probe-agent と疎通させる手順を案内します。
        </p>
      </CardContent>
    </Card>
  );
}

export default function SetupGuidePage() {
  const [searchParams] = useSearchParams();
  const sessionParam = Number(searchParams.get("session"));
  const sessionId = Number.isFinite(sessionParam) && sessionParam > 0 ? sessionParam : null;
  const { systemId } = useAuth();
  const [pattern, setPattern] = useState<RuntimePattern>("compose");

  const serverUrl = getClientServerUrl();

  const smokeTraceCurl = useMemo(
    () => `# Control Server の受信経路だけを切り分ける手動 smoke trace。
# component_id は probe-smoke-check 固定(Dashboard が実 workload と区別します)。
# トークンは環境変数で渡してください(コマンドやファイルに実値を書かない)。
# 認証なしローカル開発の場合は X-Api-Key の行を削除して構いません。
curl -sS -w "\\nHTTP %{http_code}\\n" -X POST "$PROBE_SERVER_URL/traces" \\
  -H "Content-Type: application/json" \\
  -H "X-Api-Key: $PROBE_API_KEY" \\
  -d "{
    \\"trace_id\\": \\"smoke-$(date +%s)\\",
    \\"component_id\\": \\"probe-smoke-check\\",
    \\"mode\\": \\"trace\\",
    \\"input\\": {\\"args\\": [], \\"kwargs\\": {}},
    \\"output\\": \\"'smoke ok'\\",
    \\"error\\": null,
    \\"duration_ms\\": 0.1,
    \\"timestamp\\": $(date +%s)
  }"
# 期待するレスポンス: HTTP 201`,
    [],
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
          <LifeBuoy className="h-6 w-6" /> 接続セットアップガイド
        </h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          レビュー用 patch の適用から、監視対象アプリの設定、probe-agent との疎通確認までを一続きで案内します。
          自分の実行形態に近いパターンを選んで進めてください。
        </p>
        {/* Issue #241: bidirectional Connect SDK ↔ Setup Guide navigation.
            Connect SDK links here ("Verify the connection in the Setup
            Guide"); this is the reverse direction — back to token issuance. */}
        <Link
          to="/connect-sdk"
          data-testid="setup-guide-connect-sdk-link"
          className="mt-2 inline-block text-sm text-primary underline"
        >
          ← Connect SDK でトークンを発行する
        </Link>
      </div>

      {sessionId && <SessionContextBanner sessionId={sessionId} />}

      <ConnectivityStatusCard />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">全体の流れ</CardTitle>
          <CardDescription>
            instrumentation patch・設定変更・疎通確認は「監視対象を probe-agent で観測可能にする」一連の作業です。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="space-y-2">
            {FLOW_STEPS.map((s, i) => (
              <li key={i} className="flex items-start gap-3 text-sm">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-semibold">
                  {i + 1}
                </span>
                <div>
                  <span className="font-medium">{s.title}</span>
                  <p className="text-xs text-muted-foreground">{s.detail}</p>
                </div>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <FileDiff className="h-4 w-4" /> パッチの適用
          </CardTitle>
          <CardDescription>
            ダウンロードした .patch は、対象リポジトリで差分生成時と同じ commit をベースに適用します。
            コマンドは代表的な手順の案内であり、リポジトリの運用ルールに合わせて調整してください。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <CodeBlock lang="bash">{`# 差分生成時の commit(インタビュー画面に表示)をベースにする
git switch -c probe-instrumentation <commit-sha>

# 適用できるか事前に確認する
git apply --check path/to/probe-agent-….patch

# patch を適用する
git apply path/to/probe-agent-….patch

# 変更内容を確認する
git diff

# 必要なテストや疎通確認を実行する
# 例: pytest / docker compose up / このページの smoke trace など

# 問題なければ通常の開発フローで commit / PR へ進む
git status
git add -p
git commit`}</CodeBlock>
          <p className="text-xs text-muted-foreground">
            patch は probe-agent: docstring メタデータと @probe 計装のみを追加します。適用の判断と
            commit / merge は開発者が行います。probe-agent が対象リポジトリのブランチへ直接書き込むことはありません。
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <RadioTower className="h-4 w-4" /> 実行形態を選ぶ
          </CardTitle>
          <CardDescription>
            監視対象アプリがどこで動いているかで、設定する接続先と確認手順が変わります。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs value={pattern} onValueChange={v => setPattern(v as RuntimePattern)}>
            <TabsList className="flex-wrap h-auto">
              {(Object.keys(PATTERN_LABELS) as RuntimePattern[]).map(p => (
                <TabsTrigger key={p} value={p}>{PATTERN_LABELS[p]}</TabsTrigger>
              ))}
            </TabsList>

            <TabsContent value="host" className="space-y-3">
              <p className="text-sm text-muted-foreground">
                ホスト上で直接 Python アプリを実行し、同じホストの Control Server(ポート 8000)へ送るパターンです。
              </p>
              <CodeBlock lang="bash">{`pip install probe-agent

export PROBE_SERVER_URL="http://localhost:8000"
# 認証を有効にしている場合のみ(Connect SDK で発行したトークン)
export PROBE_API_KEY="<your-token>"

python your_app.py`}</CodeBlock>
            </TabsContent>

            <TabsContent value="compose" className="space-y-3">
              <p className="text-sm text-muted-foreground">
                probe-agent と同じ Docker Compose プロジェクト内の対象アプリから、service 名で Control Server へ送るパターンです。
                接続先はコンテナ間で解決できる <code className="font-mono">http://control-server:8000</code> です
                (<code className="font-mono">localhost</code> はコンテナ自身を指すため届きません)。
              </p>
              <CodeBlock lang="yaml">{`# docker-compose.yml(監視対象サービスに追記する要素の例)
services:
  your-app:
    # ...既存の設定...
    environment:
      PROBE_SERVER_URL: http://control-server:8000
      # 認証を有効にしている場合のみ。実値は .env に置き、compose には参照だけ書く
      PROBE_API_KEY: \${PROBE_API_KEY:-}
    depends_on:
      control-server:
        condition: service_healthy
    # control-server と別 network を使っている場合は同じ network に参加させる
    # networks: [probe-net]`}</CodeBlock>
              <CodeBlock lang="bash">{`# .env(compose と同じディレクトリ。リポジトリには .env.example のみを commit する)
PROBE_API_KEY=<your-token>`}</CodeBlock>
            </TabsContent>

            <TabsContent value="image" className="space-y-3">
              <p className="text-sm text-muted-foreground">
                既存の対象アプリ image に SDK を追加して送るパターンです。イメージへ SDK を焼き込み、接続情報は実行時の環境変数で渡します。
              </p>
              <CodeBlock lang="dockerfile">{`# Dockerfile(既存イメージへの追記例)
FROM your-app-base:latest
RUN pip install probe-agent
# トークンなどの秘密値は ENV で焼き込まず、実行時に渡す`}</CodeBlock>
              <CodeBlock lang="bash">{`docker run \\
  -e PROBE_SERVER_URL="http://control-server:8000" \\
  -e PROBE_API_KEY="$PROBE_API_KEY" \\
  your-app:probe`}</CodeBlock>
            </TabsContent>

            <TabsContent value="external" className="space-y-3">
              <p className="text-sm text-muted-foreground">
                対象アプリが別リポジトリ / 別 compose プロジェクト / 別ホストにあり、外部公開した endpoint へ送るパターンです。
                この Dashboard が使っている Control Server は <code className="font-mono">{serverUrl}</code> です。
                対象アプリのネットワークから到達できる URL を設定してください。
              </p>
              <CodeBlock lang="bash">{`# 対象アプリ側(別リポジトリ / 別ホスト)
pip install probe-agent

export PROBE_SERVER_URL="${serverUrl}"   # 対象アプリから到達できる URL に読み替える
export PROBE_API_KEY="<your-token>"       # 外部からの送信では発行済みトークンを推奨

# まず到達性だけを確認する
curl -sS "$PROBE_SERVER_URL/health"   # => {"ok":true}`}</CodeBlock>
              <p className="text-xs text-muted-foreground">
                別 compose プロジェクトの場合、ホスト側に公開したポート(例: <code className="font-mono">http://host.docker.internal:8000</code>{" "}
                や ホストの IP)を使うか、共有 network を作成して service 名で解決します。
              </p>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">認証トークンの設定</CardTitle>
          <CardDescription>
            Control Server の認証状態に応じて、SDK / curl が送るトークンの扱いが変わります。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="rounded-md border p-3 space-y-1">
            <div className="font-medium">1. 認証なしローカル開発(ブートストラップ前のみ)</div>
            <p className="text-xs text-muted-foreground">
              管理者ユーザーが 1 人も存在しない場合、認証は無効です。
              <code className="font-mono">PROBE_API_KEY</code> は不要ですが、trace は system に紐づかない
              <code className="font-mono">Legacy System</code> に記録されます。これは初回セットアップ前の
              一時的な状態であり、通常運用の送信経路ではありません。まず管理者ユーザーを作成してください。
            </p>
          </div>
          <div className="rounded-md border p-3 space-y-1">
            <div className="font-medium">2. 発行済み API トークン(唯一の通常導線)</div>
            <p className="text-xs text-muted-foreground">
              <Link className="underline" to="/connect-sdk">Connect SDK ページ</Link> でシステムに紐づく
              トークンを発行し、<code className="font-mono">PROBE_API_KEY</code> に設定します。トークンは
              発行時に一度だけ表示され、特定のシステムに恒久的に紐づきます(送信側でのシステム指定は不要)。
            </p>
          </div>
          <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 p-3 text-xs text-amber-900 dark:text-amber-100">
            秘密値の扱い: トークンなどの実値を patch・リポジトリ・compose ファイルに直接書かないでください。
            リポジトリには <code className="font-mono">.env.example</code> にプレースホルダ
            (例: <code className="font-mono">PROBE_API_KEY=</code>)だけを commit し、実値は
            <code className="font-mono">.env</code> や secret 管理の仕組みで配布します。
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Activity className="h-4 w-4" /> 疎通確認
          </CardTitle>
          <CardDescription>
            外側から順に確認します: (1) HTTP 到達性 → (2) 受信経路(手動 smoke trace)→ (3) 実 workload。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="text-sm font-medium">1. health check(ネットワーク到達性)</div>
            <CodeBlock lang="bash">{`# 対象アプリと同じ場所(同じコンテナ / ホスト)から実行する
curl -sS "$PROBE_SERVER_URL/health"
# => {"ok":true} が返れば HTTP で到達できています`}</CodeBlock>
          </div>
          <div className="space-y-2">
            <div className="text-sm font-medium">2. 手動 smoke trace(受信経路の切り分け)</div>
            <CodeBlock lang="bash">{smokeTraceCurl}</CodeBlock>
            <p className="text-xs text-muted-foreground">
              201 が返り、このページの接続ステータスが「疎通確認のみ受信」になれば、endpoint・認証・ネットワーク
              設定は正しく、残る確認は対象アプリ側(@probe の適用と workload 実行)だけです。
            </p>
          </div>
          <div className="space-y-2">
            <div className="text-sm font-medium">3. 実 workload からの trace</div>
            <p className="text-xs text-muted-foreground">
              patch を適用したアプリを起動し、@probe を付けた関数が実際に実行される操作(リクエスト送信、
              バッチ実行など)を行ってください。届いた trace は{" "}
              <Link className="underline" to="/components">Components ページ</Link>{" "}
              に component 単位で表示され、このページの接続ステータスが「受信中」になります。
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Wrench className="h-4 w-4" /> トラブルシューティング(届かないときの切り分け)
          </CardTitle>
          <CardDescription>
            「まだシグナルを受信していない」状態のよくある原因と、それぞれの確認方法です。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {[
            {
              title: "設定不足",
              detail:
                "PROBE_SERVER_URL が未設定の場合、SDK は http://localhost:8000 に送ります(コンテナ内では自分自身)。対象アプリのプロセスに環境変数が渡っているかを確認してください。",
              check: "対象アプリ内で: python -c \"import os; print(os.getenv('PROBE_SERVER_URL'))\"",
            },
            {
              title: "認証失敗",
              detail:
                "手動 curl で 401 が返る場合、PROBE_API_KEY が未設定・失効・別システムのトークンです。Connect SDK ページでトークンの状態を確認してください。",
              check: "smoke trace の curl のレスポンスコードを確認(201 以外は本文にエラー理由)",
            },
            {
              title: "ネットワーク不達",
              detail:
                "connection refused / timeout は接続先の問題です。Compose 内は http://control-server:8000、コンテナからホストは localhost 不可、外部からは公開 URL、という使い分けと network 設定を確認してください。",
              check: "対象アプリと同じ場所から: curl -sS $PROBE_SERVER_URL/health",
            },
            {
              title: "イベント未送信",
              detail:
                "PROBE_ENABLED=false になっている、または @probe を付けた関数がまだ一度も呼ばれていない可能性があります。tracing の失敗はアプリを壊さないよう黙って握りつぶされるため、まず設定と呼び出しの有無を確認します。",
              check: "PROBE_ENABLED の値と、@probe 付き関数が実際に実行されるコードパスかを確認",
            },
            {
              title: "対象 workload 未実行",
              detail:
                "patch と設定が正しくても、計装した関数を通る処理が動いていなければ trace は発生しません。該当機能へのリクエストやバッチを実際に流してください。",
              check: "smoke trace は届く(smoke_only)のに実 trace が 0 のままなら、ほぼこのケースです",
            },
          ].map(item => (
            <div key={item.title} className="rounded-md border p-3 space-y-1">
              <div className="font-medium">{item.title}</div>
              <p className="text-xs text-muted-foreground">{item.detail}</p>
              <p className="text-xs">
                <span className="font-medium">確認: </span>
                <code className="font-mono text-[11px]">{item.check}</code>
              </p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">SDK 環境変数リファレンス</CardTitle>
          <CardDescription>監視対象アプリ側で使う主な環境変数です。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left">
                  <th className="pb-2 font-medium text-muted-foreground">変数</th>
                  <th className="pb-2 font-medium text-muted-foreground">既定値</th>
                  <th className="pb-2 font-medium text-muted-foreground">説明</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["PROBE_SERVER_URL", "http://localhost:8000", "Control Server の URL。実行形態に合わせて設定"],
                  ["PROBE_API_KEY", "(なし)", "認証有効時の API トークン。X-Api-Key ヘッダで送信される"],
                  ["PROBE_ENABLED", "true", "false で probe を完全に無効化(元の関数のみ実行)"],
                  ["PROBE_DEFAULT_MODE", "trace", "ポリシー未取得時の既定モード(off / trace / shadow)"],
                  ["PROBE_POLICY_TTL", "10", "ポリシーのキャッシュ秒数"],
                  ["PROBE_HTTP_TIMEOUT", "2", "Control Server への送信タイムアウト秒数"],
                ].map(([name, def, desc]) => (
                  <tr key={name} className="border-b last:border-0">
                    <td className="py-2 font-mono text-xs">{name}</td>
                    <td className="py-2 font-mono text-xs text-muted-foreground">{def}</td>
                    <td className="py-2 text-xs text-muted-foreground">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-muted-foreground mt-3">
            システム: {systemId != null ? `#${systemId}` : "未選択"} · Control Server: {serverUrl}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
