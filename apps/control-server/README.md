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
| GET  | `/auth/me` | 認証中ユーザーの情報 |
| GET  | `/users` | ユーザー一覧 (admin) |
| POST | `/users` | ユーザー作成 (admin) |
| POST | `/users/{id}/deactivate` | ユーザー無効化 + token 失効 (admin) |
| GET  | `/tokens` | token 一覧 (admin) |
| POST | `/tokens` | SDK/API token 発行 (admin) |
| POST | `/tokens/{id}/revoke` | token 失効 (admin) |

DB ファイルは `PROBE_DB_PATH` (既定 `./probe.db`) で切り替えられる。

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
- SDK は `PROBE_API_KEY` を `X-Api-Key` に付与するため、admin が発行した
  API token を `PROBE_API_KEY` に設定すればそのまま利用できる。
- 失効済み・期限切れ・無効化ユーザーの token は 401 で拒否される。

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
