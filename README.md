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

## クイックスタート

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

## 環境変数

| 名前 | 既定 | 説明 |
| --- | --- | --- |
| `PROBE_ENABLED` | `true` | SDK 全体の有効/無効 |
| `PROBE_SERVER_URL` | `http://localhost:8000` | Control Server URL |
| `PROBE_DEFAULT_MODE` | `trace` | policy 取得失敗時の fallback |
| `PROBE_POLICY_TTL` | `10` | policy キャッシュ秒数 |
| `PROBE_HTTP_TIMEOUT` | `2` | HTTP タイムアウト秒数 |
| `PROBE_DB_PATH` | `./probe.db` | Control Server の SQLite ファイル |

## ライセンス

MIT License (see [LICENSE](LICENSE)).
