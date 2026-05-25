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
