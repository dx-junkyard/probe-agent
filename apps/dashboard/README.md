# Dashboard

Streamlit 製の最小ダッシュボード。Control Server と HTTP で会話する。

## 起動

```bash
cd apps/dashboard
pip install -r requirements.txt
PROBE_SERVER_URL=http://localhost:8000 streamlit run app.py
```

## 環境変数

- `PROBE_SERVER_URL`: Control Server の URL（既定 `http://localhost:8000`）。
- `DASHBOARD_API_KEY`: Control Server が認証必須のときに使う API キー。
  設定すると全 API リクエストに `X-Api-Key` ヘッダーを付与する。
- `PROBE_API_KEY`: `DASHBOARD_API_KEY` 未設定時の fallback（SDK と共有）。

どちらのキーも未設定なら、従来どおり認証なしでアクセスする。

## Docker での起動

リポジトリルートから:

```bash
docker compose up --build
```

Compose 内では `PROBE_SERVER_URL=http://control-server:8000` が設定され、
同じネットワークの Control Server コンテナを参照する。

## 機能

- component 一覧（trace 数 / last seen）
- trace 一覧（input / output / error / duration）
- shadow 比較（current vs candidate, 手動評価）
- `off` / `trace` / `shadow` モードの切り替え
- ユーザー管理（admin のみ）

## ユーザー管理（管理者用）

`DASHBOARD_API_KEY` / `PROBE_API_KEY` に **admin ユーザーの API token**
（`POST /tokens` で発行）を設定すると、ダッシュボード上部に
「User Management」セクションが表示される。

判定は `/auth/me` を使って行う。現在の認証ユーザーが admin の場合のみ
表示され、`user` ロール・匿名・legacy API key では非表示になる。

このセクションでできること:

- ユーザー一覧表示（id / username / role / active 状態 / created_at）
- 新規ユーザー作成（username / password / role）
- アカウント停止（`POST /users/{id}/deactivate`、token も失効）
- アカウント削除（`DELETE /users/{id}`、関連 token も削除）

停止・削除はそれぞれ確認チェックボックスを挟んでから実行する。

安全のための制約（Control Server 側で強制）:

- 最後の active admin は停止・削除できない（409）。
- admin は自分自身のアカウントを削除できない（409）。
- 停止・削除されたユーザーの既存 token は使えなくなる。
