# Control Server

`probe-agent` の SDK から送られるトレースを受け取り、SQLite に保存する FastAPI サーバー。

## 起動

```bash
cd apps/control-server
pip install -e .
uvicorn app.main:app --reload --port 8000
```

## API

| Method | Path | 用途 |
| --- | --- | --- |
| GET  | `/health` | ヘルスチェック |
| POST | `/traces` | trace 受信 |
| GET  | `/components` | component 一覧 + 集計 |
| GET  | `/components/{id}/traces` | trace 一覧 |
| GET  | `/components/{id}/policy` | policy 取得 |
| PUT  | `/components/{id}/policy` | policy 更新 (`off`/`trace`/`shadow`) |
| POST | `/components/{id}/shadow-results` | shadow 実行結果の保存 |
| GET  | `/components/{id}/shadow-results` | shadow 実行結果一覧 |
| PUT  | `/shadow-results/{id}/evaluation` | 手動評価 (`better`/`worse`/`same`/`unknown`) |
| POST | `/auth/login` | username/password でログインし token を取得 |
| POST | `/auth/logout` | 呼び出しに使った token を失効 |
| GET  | `/auth/me` | 認証中ユーザーの情報 |
| GET  | `/users` | ユーザー一覧 (admin) |
| POST | `/users` | ユーザー作成 (admin) |
| POST | `/users/{id}/deactivate` | ユーザー無効化 + token 失効 (admin) |
| DELETE | `/users/{id}` | ユーザー削除 + token 削除 (admin) |
| POST | `/users/{id}/password` | パスワードリセット + login session 失効 (admin) |
| PUT  | `/users/{id}/role` | role 変更 (admin) |
| GET  | `/tokens/me` | 自分の token 一覧 (要ユーザーアカウント) |
| POST | `/tokens/me` | 自分の SDK/API token 発行 (要ユーザーアカウント) |
| POST | `/tokens/me/{id}/revoke` | 自分の token 失効 (要ユーザーアカウント) |
| GET  | `/tokens` | 全 token 一覧 (admin) |
| POST | `/tokens` | 任意ユーザーの SDK/API token 発行 (admin) |
| POST | `/tokens/{id}/revoke` | 任意の token 失効 (admin) |
| GET  | `/systems` | 自分が利用できる system 一覧 |
| POST | `/systems` | system 作成 |
| PUT  | `/systems/{id}` | system の名前・環境・説明を更新 |
| DELETE | `/systems/{id}` | system と観測データを削除 |
| POST | `/generation-runs` | trace 入力から候補コードを生成・実行・LLM 評価 |
| GET  | `/generation-runs` | 生成・評価結果一覧 |
| GET  | `/generation-runs/{id}` | 生成・評価結果詳細 |

DB ファイルは `PROBE_DB_PATH` (既定 `./probe.db`) で切り替えられる。

## LLM 設定

Generate & Evaluate は `app.llm` の抽象化層だけを通して LLM を呼び出す。
アプリケーションコードはプロバイダ固有の request / response 形式を直接扱わない。

| 変数 | 用途 |
| --- | --- |
| `LLM_PROVIDER` | `openai` / `anthropic` / `gemini` / `mock` |
| `LLM_MODEL` | 使用するモデル名 |
| `INTELLIGENCE_MAX_OUTPUT_TOKENS` | Repository Draft生成の最大出力token数（既定値: `128000`） |
| `LLM_API_KEY` | 各プロバイダ共通の API key |
| `LLM_BASE_URL` | 互換 API やプロキシを使う場合の base URL |
| `LLM_TIMEOUT` | HTTP timeout 秒 |

`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` も後方互換として読まれる。
`mock` はテストとローカルUI確認用で、外部 API は呼ばない。

## 認証とユーザー管理

実運用向けに、管理者が管理するユーザーアカウントとトークン発行に対応する。

### 環境変数

| 変数 | 用途 |
| --- | --- |
| `CONTROL_ADMIN_USERNAME` | 起動時に作成する初期管理者のユーザー名 |
| `CONTROL_ADMIN_PASSWORD` | 初期管理者のパスワード (起動時にハッシュ化して保存) |
| `CONTROL_API_KEYS` | 旧来の固定 API キー (後方互換のため残置、カンマ区切り) |

- `CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` が設定されていて同名ユーザーが
  まだ存在しない場合、起動時に `admin` ロールのユーザーを作成する。
- パスワードは平文保存されず、PBKDF2-HMAC-SHA256 (ソルト付き) でハッシュ化される。

### 認証の有効化条件

ユーザーが1人以上存在するか `CONTROL_API_KEYS` が設定されている場合に認証が有効になる。
どちらもなければ MVP 互換で認証なし(全許可)で動作する。

### トークンの使い方

- ログインで得た token、または admin が発行した API token を
  `Authorization: Bearer <token>` もしくは `X-Api-Key: <token>` で送る。
- SDK は `PROBE_API_KEY` を `X-Api-Key` に付与するため、発行した
  API token を `PROBE_API_KEY` に設定すればそのまま利用できる。
- API token は発行時に 1 つの system へ紐づき、component、trace、policy、
  profile、評価結果はその system 内だけで参照・更新される。
- Dashboard のログイン session は `X-Probe-System-Id` で選択中 system を指定する。
  SDK の API token では system が token から決まるため、このヘッダーは不要。
- 一般ユーザーは `/tokens/me` 系 API で自分の token を発行・一覧・失効できる
  (Dashboard の「My Tokens」タブが使用)。legacy API key や匿名アクセスでは
  使えない (403)。他ユーザーの token の失効は 404 になる。
- 失効済み・期限切れ・無効化ユーザーの token は 401 で拒否される。

### ユーザーの停止・削除に関する安全制約

- `POST /users/{id}/deactivate`: 対象を inactive にし、その token をすべて失効させる。
- `DELETE /users/{id}`: 対象ユーザーと、その token を削除する。
- `POST /users/{id}/password`: パスワードを変更し、対象の login session token を
  失効させる (API token は有効のまま)。
- `PUT /users/{id}/role`: role を変更する。
- 最後の active admin は停止・削除・降格できない (409)。
- admin は自分自身のアカウントを削除できない (409)。

### CONTROL_API_KEYS からの移行

1. `CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` を設定して起動し管理者を作成。
2. `/auth/login` で token を取得し、`/tokens` で SDK 用 API token を発行。
3. 各 SDK / クライアントの `PROBE_API_KEY` を発行した token に置き換える。
4. 移行完了後に `CONTROL_API_KEYS` を削除する。

## Docker での起動

リポジトリルートから:

```bash
docker compose up --build control-server
```

Compose 利用時は `PROBE_DB_PATH=/data/probe.db` がセットされ、SQLite ファイルは
名前付き volume `probe-data` に永続化される。
