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

DB ファイルは `PROBE_DB_PATH` (既定 `./probe.db`) で切り替えられる。

## Docker での起動

リポジトリルートから:

```bash
docker compose up --build control-server
```

Compose 利用時は `PROBE_DB_PATH=/data/probe.db` がセットされ、SQLite ファイルは
名前付き volume `probe-data` に永続化される。
