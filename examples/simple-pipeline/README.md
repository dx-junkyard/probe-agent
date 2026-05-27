# simple-pipeline

`@probe` を使った最小サンプル。3 つの component (`summarizer` / `classifier` / `json-normalizer`) を実行する。

## 使い方

```bash
# 1. Control Server を起動
cd apps/control-server && uvicorn app.main:app --port 8000

# 2. Dashboard を起動 (別ターミナル)
cd apps/dashboard && streamlit run app.py

# 3. SDK をインストールしてサンプル実行 (別ターミナル)
pip install -e packages/python-probe
cd examples/simple-pipeline
PROBE_SERVER_URL=http://localhost:8000 python main.py
```

Dashboard で `summarizer` / `classifier` の mode を `shadow` に切り替えてからもう一度 `main.py` を実行すると、候補実装 (`summarize_v2` / `classify_v2`) の出力と比較できる。
