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

## Phase 4: Evaluation Context MVP — 実装済み (issue #9)

component の出力を、システム全体の目的・component の責務・評価基準に紐づけて
評価できるようにする。LLM 評価は使わず、決定的なルールのみで判定する。

3 階層のデータモデル:

- **System Profile**: システムの目的・対象ユーザー・提供価値・制約（シングルトン）
- **Component Profile**: component ごとの責務・入出力期待・失敗時の影響
- **Evaluation Criteria / Results**: component ごとの評価観点と、trace 単位の評価結果

API:

- `GET/PUT /system-profile`
- `GET/PUT /components/{component_id}/profile`
- `GET/POST /components/{component_id}/criteria`
- `PUT /criteria/{criterion_id}`
- `POST /traces/{trace_id}/evaluate`
- `GET /traces/{trace_id}/evaluations`

評価ロジック（rule-based）:

- `exact_match` / `contains` / `regex` / `json_equal` / `required_keys` は自動判定
- `natural_language` は自動判定せず `needs_review`
- 結果は `ok` / `ng` / `needs_review` と reason 付きで保存
- 再評価は同一 trace の過去結果を置き換える（冪等）

Dashboard で system profile / component profile / criteria の編集と、
trace 単位の評価実行・結果表示ができる。

## やらないこと

- 自動 replace
- リモートからの任意コード実行
- 複雑な権限制御
- LLM 評価
- CI/CD 連携
- 複数言語 SDK
