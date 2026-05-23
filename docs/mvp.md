# probe-agent MVP

issue #1 で定義された MVP の実装メモ。

## 構成図

```
+------------------+        HTTP        +-------------------+
|  host app        |  ----------------> |  Control Server   |
|  @probe(...)     |  POST /traces      |  (FastAPI+SQLite) |
|                  |  POST /shadow-...  |                   |
|                  |  GET  /policy      |                   |
+------------------+ <----------------- +-------------------+
                                                  ^
                                                  | HTTP
                                                  |
                                          +---------------+
                                          |  Dashboard    |
                                          |  (Streamlit)  |
                                          +---------------+
```

## Phase 1: Trace MVP — 実装済み

- `@probe(component_id=...)` デコレーターが input / output / error / duration を取得し POST する
- Control Server が SQLite に保存
- Dashboard で trace を component 単位に閲覧
- `PROBE_ENABLED=false` で完全無効化

## Phase 2: Policy MVP — 実装済み

- `GET /components/{id}/policy` を SDK が TTL 付きでキャッシュ
- `PUT /components/{id}/policy` で `off` / `trace` / `shadow` を更新
- Control Server が落ちている場合、SDK は前回キャッシュまたは `PROBE_DEFAULT_MODE` で動作 → 元関数は常に実行される

## Phase 3: Shadow MVP — 実装済み

- `set_candidate(component_id, fn)` で代替実装を登録
- `mode=shadow` のとき、本番返値は current のまま。candidate はバックグラウンドスレッドで実行
- Dashboard で current / candidate / diff を確認し、`better` / `worse` / `same` / `unknown` を手動評価

## やらないこと

- 自動 replace
- リモートからの任意コード実行
- 複雑な権限制御
- LLM 評価
- CI/CD 連携
- 複数言語 SDK
