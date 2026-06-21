# probe-agent

開発対象システムの任意のコンポーネントに `@probe` を付け、入出力をトレース・可視化し、
代替実装と shadow 比較するための最小ツールキット。

詳細は [issue #1](https://github.com/dx-junkyard/probe-agent/issues/1) と
[`docs/mvp.md`](docs/mvp.md) を参照。

## 構成

```
probe-agent/
├── apps/
│   ├── control-server/   # FastAPI + SQLite。trace 受信と policy 配布
│   └── dashboard/        # Streamlit。component 一覧、trace 閲覧、shadow 比較
├── packages/
│   └── python-probe/     # Python SDK (probe_agent)
├── examples/
│   └── simple-pipeline/  # @probe を付けたサンプル
├── shared/schemas/       # JSON Schema 定義
└── docs/
```

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

## クイックスタート (ローカル Python)

```bash
# 0. 仮想環境推奨
python -m venv .venv && source .venv/bin/activate

# 1. SDK と Control Server を editable install
pip install -e packages/python-probe
pip install -e apps/control-server

# 2. Control Server 起動 (port 8000)
uvicorn app.main:app --app-dir apps/control-server --reload --port 8000

# 3. Dashboard 起動 (別ターミナル, port 8501)
pip install -r apps/dashboard/requirements.txt
PROBE_SERVER_URL=http://localhost:8000 streamlit run apps/dashboard/app.py

# 4. サンプル実行 (別ターミナル)
cd examples/simple-pipeline
PROBE_SERVER_URL=http://localhost:8000 python main.py
```

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
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=...
LLM_BASE_URL=
LLM_TIMEOUT=30
```

`LLM_PROVIDER=mock` はローカルの疎通確認とテスト用で、外部 API は呼ばない。
実際の評価には `openai` / `anthropic` / `gemini` のいずれかと API key を使う。

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
| `PROBE_API_KEY` | _(未設定)_ | SDK が送る API キー (`X-Api-Key` ヘッダー) |
| `CONTROL_API_KEYS` | _(未設定)_ | Control Server が受け付ける API キー（カンマ区切り複数可）。未設定時は認証なし |
| `DASHBOARD_API_KEY` | _(未設定)_ | Dashboard が Control Server に送る API キー |
| `PROBE_CLIENT_SERVER_URL` | _(未設定)_ | Dashboard の `Connect SDK` タブに表示するクライアント向け Control Server URL |
| `PROBE_SDK_INSTALL_URL` | GitHub の `packages/python-probe` | Dashboard の `Connect SDK` タブに表示する SDK install URL |
| `CONTROL_ADMIN_USERNAME` | _(未設定)_ | 起動時に作成する初期管理者ユーザー名 |
| `CONTROL_ADMIN_PASSWORD` | _(未設定)_ | 起動時に作成する初期管理者パスワード |

## ライセンス

MIT License (see [LICENSE](LICENSE)).
