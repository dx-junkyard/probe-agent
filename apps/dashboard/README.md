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
- `DASHBOARD_API_KEY`: service / fallback 用の API キー。
  設定すると（ログインしていない間）API リクエストに `X-Api-Key` ヘッダーを付与する。
- `PROBE_API_KEY`: `DASHBOARD_API_KEY` 未設定時の fallback（SDK と共有）。
- `DASHBOARD_COOKIE_SECURE`: `true` にするとログインCookieをHTTPS通信時だけ送信する。
  HTTPSで公開する環境では有効にする。ローカルのHTTP開発環境では未設定にする。

どちらのキーも未設定なら、従来どおり認証なしでアクセスする。

## 認証（Login / Logout）

サイドバーの Login フォームから username / password でログインできる
（Control Server の `/auth/login` を使用）。

- 取得した session token は `st.session_state` とブラウザCookieに保持する。
  Cookieの有効期限はControl Serverが返すsession tokenの有効期限（現在は7日間）に合わせる。
- ブラウザのreloadや新しいStreamlitセッションではCookieからsession tokenを復元する。
- ログイン中は `Authorization: Bearer <session token>` が
  環境変数の API キーより優先される。
- コンポーネント、トレース、設定画面はユーザーがログインしている場合だけ表示する。
  `DASHBOARD_API_KEY`によるfallback認証だけではDashboardの内容を表示しない。
- サイドバーに現在のユーザー名と role が表示される。
- Logout で `/auth/logout` を呼び session token をサーバー側でも失効させ、
  `st.session_state` とブラウザCookieから破棄する。
- session tokenが期限切れ・失効済みの場合も、保存されたCookieを削除してLogin表示に戻る。

Cookieはブラウザ側のJavaScriptから扱うため `HttpOnly` ではない。公開環境ではHTTPSを使用し、
`DASHBOARD_COOKIE_SECURE=true` を設定する。

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
- login / logout（username / password）
- My Tokens（自分の API token の発行・一覧・失効）
- User Management タブ（admin のみ）

## My Tokens タブ

ログイン中のユーザー（admin / user どちらも）が自分の token を管理できる。
Control Server の self-service API（`GET/POST /tokens/me`、
`POST /tokens/me/{id}/revoke`）を使用する。

- 自分の token 一覧表示（id / name / kind / status / created_at / expires_at）
- 新規 API token 発行（name と有効期限は任意）
- 自分の token の失効

raw token は **発行直後に一度だけ** 表示され、`PROBE_API_KEY=...` の
貼り付け用 snippet も併せて表示される。「表示を閉じる」を押すか
別の操作をすると再表示はできない。

## User Management タブ（管理者用）

`/auth/me` の role が `admin` のときのみ表示される。
（login session・admin の API token のどちらで認証していてもよい。）
`user` ロール・匿名・legacy API key では非表示になる。

このタブでできること:

- ユーザー一覧表示（id / username / role / active 状態 / created_at）
- 新規ユーザー作成（username / password / role）
- アカウント停止（`POST /users/{id}/deactivate`、token も失効）
- アカウント削除（`DELETE /users/{id}`、関連 token も削除）
- パスワードリセット（`POST /users/{id}/password`、対象の login session は失効）
- role 変更（`PUT /users/{id}/role`）
- 全ユーザーの token 一覧・失効（`GET /tokens`、`POST /tokens/{id}/revoke`）

停止・削除はそれぞれ確認チェックボックスを挟んでから実行する。

安全のための制約（Control Server 側で強制）:

- 最後の active admin は停止・削除・降格できない（409）。
- admin は自分自身のアカウントを削除できない（409）。
- 停止・削除されたユーザーの既存 token は使えなくなる。
- パスワードリセットで既存の login session は失効する（API token は有効のまま）。
