# Dashboard

Streamlit 製の最小ダッシュボード。Control Server と HTTP で会話する。

## 起動

```bash
cd apps/dashboard
pip install -r requirements.txt
PROBE_SERVER_URL=http://localhost:8000 streamlit run app.py
```

## 機能

- component 一覧（trace 数 / last seen）
- trace 一覧（input / output / error / duration）
- shadow 比較（current vs candidate, 手動評価）
- `off` / `trace` / `shadow` モードの切り替え
