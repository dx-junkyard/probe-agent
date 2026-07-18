# probe-agent

開発対象システムの任意のコンポーネントに `@probe` を付け、入出力をトレース・可視化し、
代替実装と shadow 比較するための最小ツールキット。

関数単位の `Component` の上にユーザー価値単位の `Feature` を置く
**Feature Intelligence Layer** と、source patch を隔離環境で比較する
**Experiment Workspace Layer** を提供する。設計は
[`docs/project-intelligence.md`](docs/project-intelligence.md) を参照。

詳細は [issue #1](https://github.com/dx-junkyard/probe-agent/issues/1) と
[`docs/mvp.md`](docs/mvp.md) を参照。

## 構成

```
probe-agent/
├── apps/
│   ├── control-server/   # FastAPI + SQLite。trace 受信と policy 配布
│   └── dashboard/        # React/Vite。component 一覧、trace 閲覧、shadow 比較
├── packages/
│   └── python-probe/     # Python SDK (probe_agent)
├── examples/
│   └── simple-pipeline/  # @probe を付けたサンプル
├── shared/schemas/       # JSON Schema 定義
├── probe-agent.example.yml # 対象repoの読み取り・実行設定例
└── docs/
```

## Feature Intelligence / Experiment Workspace

- committed files only の Repository Snapshot
- evidence 付きの `SystemProfile` / `FeatureProfile`
- Feature と source symbol を結ぶ `FeatureCodeLink`
- 副作用リスクを持つ `ProbePlan`
- baseline と source patch variants を比較する Experiment Workspace

Dashboard には `Repository` / `Feature Map` / `Probe Planner` / `Experiments`
タブがある。実行command・env・timeoutはpinned snapshot内の
`probe-agent.yml`からのみ読み、APIやDashboardから任意commandを受け取らない。
network accessは許可せず、workspace実行は利用可能なsandbox backendがない場合に
fail closedする。

判断ロジックは、少数の明示的な有限集合への分類だけ決定的ルールを許可する。
Feature 抽出、Feature-to-Code mapping、Probe Plan、実験結果の解釈など自由度の
ある推論には reasoning model の LLM API を必須とし、失敗時に heuristic へ
フォールバックしない。

## クイックスタート (Docker Compose)

Control Server と Dashboard をまとめて起動する最短手順:

```bash
cp .env.example .env
docker compose up --build
```

- Control Server: <http://localhost:8000> (`/health` で確認)
- Dashboard:      <http://localhost:8501>
- SQLite DB は名前付き volume `probe-data` (`/data/probe.db`) に永続化される

サンプルはホスト側の Python から Compose 内の Control Server に向けて実行できる:

```bash
pip install -e packages/python-probe
cd examples/simple-pipeline
PROBE_SERVER_URL=http://localhost:8000 python main.py
```

停止と DB の破棄:

```bash
docker compose down          # コンテナのみ停止
docker compose down -v       # volume (DB) も削除
```

### 本番 HTTPS 公開

上記の `docker-compose.yml` はローカル開発用（control-server / dashboard を
ホストへ直接公開）。インターネットから単一 FQDN + HTTPS で公開するには、
Caddy が Automatic HTTPS で証明書取得・更新まで行う独立構成
`docker-compose.prod.yml` を使う。手順・前提条件・公開前チェックリストは
[`docs/deployment-https.md`](docs/deployment-https.md) を参照。

### 依存関係とコンテナイメージの固定

外部レジストリから取得する `python` / `node` / `nginx` / `caddy` の各 base
image は、可読な tag と OCI image index digest の組み合わせで固定している。
依存解決は Dashboard では `package-lock.json` + `npm ci`、Control Server では
hash 付きの `requirements.lock` / `requirements-dev.lock` +
`pip install --require-hashes` を使う。CI は両アプリを build し、それぞれの
SPDX JSON SBOM を artifact として保存する。

一方、Compose の `probe-agent/control-server:latest` と
`probe-agent/dashboard:latest` は、その場で `build:` が生成するローカル出力名
であり、build 前には固定できる registry digest が存在しない。このため架空の
digest は付けない。本番で再現可能な配布単位が必要な場合は、CI が build した
image を registry へ publish し、得られた digest または immutable release tag
を deployment manifest に記録する。

Python lock と base image digest の更新手順、検証範囲と残余リスクは
[`docs/deployment-https.md`](docs/deployment-https.md#サプライチェーン更新手順)
を参照。

## クイックスタート (ローカル Python)

```bash
# 0. 仮想環境推奨
python -m venv .venv && source .venv/bin/activate

# 1. SDK と Control Server を editable install
pip install -e packages/python-probe
pip install -e apps/control-server

# 2. Control Server 起動 (port 8000)
uvicorn app.main:app --app-dir apps/control-server --reload --port 8000

# 3. Dashboard 起動 (別ターミナル, port 5173)
cd apps/dashboard
npm install
npm run dev

# 4. サンプル実行 (別ターミナル)
cd examples/simple-pipeline
PROBE_SERVER_URL=http://localhost:8000 python main.py
```

開発サーバーは `http://localhost:5173` で起動する。Vite の proxy 設定により
`/api` へのリクエストは自動的に Control Server (`http://localhost:8000`) へ転送される。

テストは venv を activate した状態（venv の `bin` が PATH にある状態）で
`python -m pytest apps/control-server/tests packages/python-probe/tests` を実行する。
実験ワークスペースのコマンドはサーバープロセスの PATH から `python` を解決するため、
venv 非アクティブだと test_experiments が失敗する。CI（`.github/workflows/ci.yml`）も
同じ前提で実行している。

Dashboard で `summarizer` / `classifier` の mode を `shadow` に切り替えてから
サンプルを再実行すると、候補実装との比較結果が確認できる。

## Docker コンテナ内の対象アプリに probe を入れる

probe を設置する対象アプリが Docker コンテナで動いている場合、対象アプリの
image に Python SDK (`packages/python-probe`) を install する必要がある。

対象コンテナには最低限以下の環境変数を渡す。

```yaml
environment:
  PROBE_ENABLED: "true"
  PROBE_SERVER_URL: http://control-server:8000
  PROBE_API_KEY: ${PROBE_API_KEY:-}
```

Compose 内のコンテナから Control Server に接続する場合、`localhost:8000`
ではなく service 名の `http://control-server:8000` を使う。ホスト上で直接
Python を実行する場合だけ `http://localhost:8000` を使う。

### 方法 1: リポジトリ内の SDK を COPY して install

対象アプリをこのリポジトリルートを build context にしてビルドできる場合は、
SDK を image にコピーして install する。

```yaml
services:
  target-app:
    build:
      context: .
      dockerfile: path/to/target-app/Dockerfile
    environment:
      PROBE_ENABLED: "true"
      PROBE_SERVER_URL: http://control-server:8000
      PROBE_API_KEY: ${PROBE_API_KEY:-}
    depends_on:
      control-server:
        condition: service_healthy
```

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY packages/python-probe /opt/probe-agent/packages/python-probe
RUN pip install -e /opt/probe-agent/packages/python-probe

COPY path/to/target-app /app

CMD ["python", "main.py"]
```

### 方法 2: Git URL の subdirectory install を使う

対象アプリが別リポジトリにある場合は、Docker build 中に GitHub から
`packages/python-probe` だけを pip install できる。

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install \
  "git+https://github.com/dx-junkyard/probe-agent.git@main#subdirectory=packages/python-probe"

COPY . /app

CMD ["python", "main.py"]
```

本番寄りでは `main` ではなく tag や commit SHA に固定する。

```dockerfile
ARG PROBE_AGENT_REF=<commit-sha>

RUN pip install \
  "git+https://github.com/dx-junkyard/probe-agent.git@${PROBE_AGENT_REF}#subdirectory=packages/python-probe"
```

```bash
docker build --build-arg PROBE_AGENT_REF=<commit-sha> .
```

### 方法 3: git clone して install

clone したリポジトリから install する形でもよい。

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ARG PROBE_AGENT_REF=main

RUN git clone --depth 1 --branch ${PROBE_AGENT_REF} \
      https://github.com/dx-junkyard/probe-agent.git /opt/probe-agent \
    && pip install -e /opt/probe-agent/packages/python-probe

COPY . /app

CMD ["python", "main.py"]
```

private repository から取得する場合は、Dockerfile に token を直書きしない。
BuildKit secret や deploy key を使う。

### 将来的な方法: package registry から install

SDK を PyPI や GitHub Packages に publish できるようにした場合は、対象
Dockerfile は以下のように簡略化できる。

```dockerfile
RUN pip install probe-agent
```

現状は registry publish していないため、上の COPY / Git URL / git clone
のいずれかを使う。

### 対象コードへの probe 設定例

```python
from probe_agent import probe


@probe(component_id="summarizer")
def summarize(text: str) -> str:
    return text[:80]
```

shadow 比較を使う場合は candidate を登録する。

```python
from probe_agent import probe, set_candidate


def summarize_v2(text: str) -> str:
    return text.split(".")[0]


set_candidate("summarizer", summarize_v2)


@probe(component_id="summarizer")
def summarize(text: str) -> str:
    return text[:80]
```

Dashboard の `Connect SDK` タブでは、選択中 System 用の API token 発行、
SDK install command、クライアント環境変数、最小サンプルソース、
Dockerfile サンプルをまとめて確認・ダウンロードできる。

## Generate & Evaluate

Dashboard の `Generate & Evaluate` タブでは、転送済み trace の入力
パラメーターを使って候補 Python コードを生成し、同じ入力で実行した結果を
LLM で評価できる。生成されたコードは保存・ダウンロードできるが、対象
システムへ自動適用はしない。

Control Server は LLM 呼び出しを `apps/control-server/app/llm.py` に集約しており、
プロバイダ差分はこの層で吸収する。Compose では `.env` に以下を設定する。

```env
LLM_PROVIDER=openai   # openai / anthropic / gemini / mock
LLM_MODEL=gpt-5.4-mini
LLM_API_KEY=...
LLM_BASE_URL=
LLM_TIMEOUT=120
```

`LLM_PROVIDER=mock` はローカルの疎通確認とテスト用で、外部 API は呼ばない。
実際の評価には `openai` / `anthropic` / `gemini` のいずれかと API key を使う。

Repository Understanding では、Control Server が読み取れる host 側の repository
root を `.env` の秘密情報として指定する。Compose はこのrootをcontainer内の
`/repositories`へmountする。AI解析はcommit済みGit objectだけを読み、未commit変更や
untracked fileを入力に含めない。検証済みProbe PatchはDashboardで明示承認した場合だけ
元Repositoryのworking treeへ適用できる。

```env
PROBE_REPOSITORY_HOST_ROOT=/path/to/repositories
INTELLIGENCE_LLM_MODEL=gpt-5.4
```

DashboardではControl Serverが`/repositories`配下から検出したGit Repositoryを
選択する。自由度のある draft 生成では reasoning model
以外を拒否し、LLM failure 時に heuristic へフォールバックしない。

## GitHub 連携（Publish workflow, Issue #216）

Probe Planner で承認・validate 済みの probe patch を、実際の GitHub
リポジトリへ commit / push / Pull Request 作成できる。probe-agent が対象
リポジトリの default ブランチへ直接書き込むことは一切なく、常に人間の
明示承認を経てから `probe/` 接頭辞のサーバー生成ブランチにのみ push し、
Pull Request のマージ・クローズは開発者が GitHub 上で行う（設計の詳細は
[`docs/project-intelligence.md`](docs/project-intelligence.md) の
「GitHub App 公開ワークフロー（Issue #216）」を参照）。

有効化するには、対象リポジトリに GitHub App をインストールし、Control
Server に以下を設定する。

```env
GITHUB_APP_ID=<GitHub App の App ID>
GITHUB_APP_PRIVATE_KEY_PATH=/absolute/path/to/github-app-private-key.pem
GITHUB_APP_ALLOWED_ORGANIZATION=<GitHub App を所有する Organization の login>
GIT_REPOSITORY_ROOT=/path/to/managed-git-root
```

いずれか未設定の場合、GitHub 機能全体が fail closed になる（Dashboard の
`GitHub` ページの App status カードに設定手順が表示される）。両方とも
未設定なら probe-agent の他機能には一切影響しない。

`docker-compose.prod.yml` を使った本番デプロイでは、秘密鍵は Compose
secret としてマウントし、`GITHUB_PUBLISH_ENABLED=true` を明示的に設定した
場合のみ起動時にキーの妥当性を検証する（Issue #224）。GitHub App の登録
手順、秘密鍵のホスト配置・ローテーション手順は
[`docs/github-app-deployment.md`](docs/github-app-deployment.md) を参照。

Dashboard の `GitHub` ページでの操作フロー:

1. admin が **Installations** タブで Installation ID を登録する。Control
   Server は GitHub から account login/type を読み、設定された Organization
   と一致するものだけを登録できる。続けて利用を許可する System へ明示割当する。
2. **Connections** タブでは、その System に割り当て済みの Installation だけを
   選択し、`Load repositories` 相当の repository picker から connection を作成する。
   未登録・無効化済み・別 System 専用の Installation は利用できない。
3. 作成した connection を `Verify`（Installation Token で疎通確認・
   `default_branch` を取得）し、必要なら `Sync`（managed mirror を最新化）する。
4. **Publish Jobs** タブで connection と validate 済み(baseline/probed とも
   green)の probe patch を選び、publish job を作成する。job は自動で
   `awaiting_approval` まで進み、そこで停止する。
4. job 詳細で publish 先（owner/repo・base branch・base commit SHA・
   生成される branch 名）と patch diff を確認したうえで `Approve` する。
   承認後、job は自動で commit → push → Pull Request 作成まで進む。
   `awaiting_approval` の間は `Cancel` もできる。
5. 作成された Pull Request のレビュー・マージは GitHub 上で行う。

## 認証と Dashboard のログイン方式

現状の Dashboard にはブラウザ上のログイン画面はない。Dashboard は起動時に
`DASHBOARD_API_KEY`（未設定時は `PROBE_API_KEY`）を読み、この token を
`X-Api-Key` ヘッダーとして Control Server に送る。

admin 用のユーザー管理画面を表示するには、`DASHBOARD_API_KEY` に
**admin ユーザーが発行した API token** を設定する必要がある。
`CONTROL_API_KEYS` の固定キーは legacy key として認証されるため、admin
ユーザーとは見なされず、User Management は表示されない。

### 1. 初期 admin を設定して起動

`.env.example` をコピーし、少なくとも以下を設定する。

```env
PROBE_SERVER_URL=http://control-server:8000
CONTROL_ADMIN_USERNAME=admin
CONTROL_ADMIN_PASSWORD=admin-pass-
```

Compose で起動する。

```bash
docker compose up -d
```

初回起動時に `CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` から
admin ユーザーが作成される。既に DB volume が存在し、admin が作成されない
場合は、必要に応じて `docker compose down -v` で DB を初期化してから起動する。

### 2. admin でログインして API token を発行

ホストから Control Server にログインする。

```bash
ADMIN_TOKEN=$(curl -sS -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin-pass-"}' \
  | sed -E 's/.*"access_token":"([^"]+)".*/\1/')
```

Dashboard 用の API token を発行する。

```bash
curl -sS -X POST http://localhost:8000/tokens \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"dashboard-admin-token"}'
```

レスポンスの `token` を `.env` に設定する。

```env
DASHBOARD_API_KEY=<発行された token>
```

Dashboard コンテナを再作成する。

```bash
docker compose up -d --force-recreate dashboard
```

再読み込み後、Dashboard 上部に
`User Management（管理者用：ユーザーの作成・停止・削除）` が表示される。

## 環境変数

Docker Compose はリポジトリルートの `.env` を読み込む。ローカルの値は
`.env.example` をコピーして編集する。

| 名前 | 既定 | 説明 |
| --- | --- | --- |
| `PROBE_ENABLED` | `true` | SDK 全体の有効/無効 |
| `PROBE_SERVER_URL` | `http://localhost:8000` | Control Server URL |
| `PROBE_DEFAULT_MODE` | `trace` | policy 取得失敗時の fallback |
| `PROBE_POLICY_TTL` | `10` | policy キャッシュ秒数 |
| `PROBE_HTTP_TIMEOUT` | `2` | HTTP タイムアウト秒数 |
| `PROBE_DB_PATH` | `./probe.db` | Control Server の SQLite ファイル |
| `PROBE_EXECUTION_BACKEND` | development: `inprocess` / production: `worker` | 検証・replay・experiment・candidate の実行backend。有限値は `inprocess` / `worker`。production は `worker` 以外を起動時拒否 |
| `PROBE_EXECUTION_SPOOL_ROOT` | _(未設定)_ | worker backend のatomic job spool共有root。production必須。DB・secret・repository rootとは別volumeにする |
| `PROBE_EXECUTION_WORKSPACE_ROOT` | _(未設定)_ | Control Serverとexecution workerが同じパスでmountするworktree共有root。production必須 |
| `PROBE_WORKTREE_BASE` | `/tmp/probe-worktrees` | validation worktree base。worker利用時は共有workspace root配下が必須 |
| `PROBE_REPLAY_WORKSPACE_BASE` | `/tmp/probe-replays` | replay/candidate worktree base。worker利用時は共有workspace root配下が必須 |
| `PROBE_EXPERIMENT_WORKSPACE_BASE` | `/tmp/probe-experiments` | experiment worktree base。worker利用時は共有workspace root配下が必須 |
| `PROBE_API_KEY` | _(未設定)_ | SDK が送る API キー (`X-Api-Key` ヘッダー) |
| `CONTROL_API_KEYS` | _(未設定)_ | Control Server が受け付ける API キー（カンマ区切り複数可）。未設定時は認証なし |
| `DASHBOARD_API_KEY` | _(未設定)_ | Dashboard が Control Server に送る API キー |
| `PROBE_CLIENT_SERVER_URL` | _(未設定)_ | Dashboard の `Connect SDK` タブに表示するクライアント向け Control Server URL |
| `PROBE_SDK_INSTALL_URL` | GitHub の `packages/python-probe` | Dashboard の `Connect SDK` タブに表示する SDK install URL |
| `CONTROL_ADMIN_USERNAME` | _(未設定)_ | 起動時に作成する初期管理者ユーザー名 |
| `CONTROL_ADMIN_PASSWORD` | _(未設定)_ | 起動時に作成する初期管理者パスワード |
| `CONTROL_REQUIRE_AUTH` | `false` | `true` で、認証を有効化できない状態（admin 未作成かつ `CONTROL_API_KEYS` 空）なら起動を失敗させる |
| `PUBLIC_HOST` | _(未設定)_ | `docker-compose.prod.yml` の Caddy が HTTPS で公開する FQDN |
| `GITHUB_APP_ID` | _(未設定)_ | Publish workflow (#216) が使う GitHub App の App ID。未設定時は GitHub App 機能全体が fail closed |
| `GITHUB_APP_PRIVATE_KEY_PATH` | _(未設定)_ | コンテナ内から見た GitHub App 秘密鍵 PEM のパス。`docker-compose.prod.yml` では `/run/secrets/github_app_private_key` に固定（Compose外実行時のみ本変数を使う）。本番でのキー配置・ローテーション手順は [`docs/github-app-deployment.md`](docs/github-app-deployment.md) 参照 |
| `GITHUB_APP_PRIVATE_KEY_HOST_PATH` | _(未設定 = `/dev/null`)_ | `docker-compose.prod.yml` が secret にマウントするホスト側 PEM の絶対パス。未設定なら空ファイルがマウントされ publish workflow は無効のまま |
| `GITHUB_PUBLISH_ENABLED` | `false` | GitHub App publish workflow を有効化する意思表示 (`true`/`false` 等の有限集合)。`true` の場合、起動時に App ID とキーの妥当性を検証し、不備があれば起動失敗 |
| `GITHUB_APP_ALLOWED_ORGANIZATION` | _(未設定)_ | private GitHub App を所有する単一 Organization の login。Installation 登録時に GitHub から得た account login/type と照合し、不一致は拒否する |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub REST API のベース URL（GitHub Enterprise Server 向けの上書き） |
| `GITHUB_WEB_BASE_URL` | `https://github.com` | GitHub の web/clone URL のベース（GitHub Enterprise Server 向けの上書き） |
| `GIT_REPOSITORY_ROOT` | _(未設定)_ | Publish workflow (#216) の managed clone / worktree 領域のルート。未設定時は repository manager 機能全体が fail closed |
| `GIT_CLONE_TIMEOUT` | `300` | managed mirror の `git clone` タイムアウト秒数 |
| `GIT_FETCH_TIMEOUT` | `120` | managed mirror の `git fetch` / `git ls-remote` タイムアウト秒数 |
| `GIT_JOB_RETENTION_HOURS` | `24` | この時間を超えて放置された job worktree を `cleanup_expired_jobs` が削除するまでの猶予時間 |
| `GIT_BRANCH_PREFIX` | `probe/` | Publish job が push する branch 名の接頭辞。この接頭辞のブランチにのみ push する |
| `GIT_ALLOW_DIRECT_PUSH` | `false` | 読み取るだけで `false` 以外の値でも常に fail closed（direct push は MVP 非実装） |
| `GIT_ALLOW_FORCE_PUSH` | `false` | 読み取るだけで `false` 以外の値でも常に fail closed（force push は MVP 非実装） |
| `GIT_ALLOW_WORKFLOW_CHANGES` | `false` | `true` の場合のみ patch diff 内の `.github/workflows/` ファイルを stage 許可 |
| `GITHUB_APP_BOT_NAME` | `probe-agent[bot]` | publish job が作る commit の author/committer name |
| `GITHUB_APP_BOT_EMAIL` | `probe-agent[bot]@users.noreply.github.com` | publish job が作る commit の author/committer email |

## ライセンス

MIT License (see [LICENSE](LICENSE)).
