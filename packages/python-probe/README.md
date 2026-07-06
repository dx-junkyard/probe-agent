# probe-agent (Python Probe SDK)

軽量な Python SDK。任意の関数に `@probe(component_id=...)` を付けるだけで、
入出力・エラー・実行時間を Control Server に送信できる。

```python
from probe_agent import probe, set_candidate

@probe(component_id="summarizer")
def summarize(text: str) -> str:
    ...

# 代替実装を登録すると shadow モードで比較できる
set_candidate("summarizer", summarize_v2)
```

## トレース系譜メタデータ（Issue #145 / Phase 1）

`probe_context` で一連の probe 呼び出しに **correlation_id / flow_id / entities** を
付与できる。すべて任意で、付けなければ従来どおりのトレースになる。

```python
from probe_agent import probe, probe_context, add_entity

@probe(component_id="order-validate", entities=[{"type": "order", "id": "o-123", "role": "source"}])
def validate(order):
    ...

with probe_context(correlation_id="req-abc", flow_id="checkout"):
    add_entity("tenant", "t-9")          # 以降の probe すべてに付与
    validate(order)                       # order-validate と同じ correlation を共有
```

- `probe_context(correlation_id=None, flow_id=None, entities=None)`: ブロック内の
  すべての probe が同じ `correlation_id` / `flow_id` を共有する。未指定なら外側の
  コンテキストを継承し、無ければ自動生成する。
- ネストした probe 呼び出しには `parent_span_id` が自動で設定される（各 probe は
  一意の `span_id` を持つ）。
- `add_entity(type, id, role="related")` / `@probe(entities=[...])`: エンティティは
  **呼び出し側が渡す明示値**のみ（パス式による抽出は Phase 2 / Issue #146）。
  `role` は `source` / `derived` / `related` の有限集合。
- shadow モードでは candidate スレッドに `contextvars.copy_context()` で系譜が
  引き継がれ、candidate 内のネスト probe も同じ lineage に載る。
- 系譜は Control Server の `trace_spans` / `trace_entities` に保存され、
  `GET /trace-lineage/entities/{type}/{id}`、`/correlations/{id}`、`/flows/{id}` で
  時系列に取得できる。probe が `off` / 無効のときは系譜処理は一切実行されない。

## 宣言的 Projection（Issue #146 / Phase 2）

raw payload を保存せず、宣言的な spec で **入出力の一部だけを構造化して抽出**できる。
式は安全な有限サブセット（`$.a.b` / `$.items[*].sku` / `[0]` インデックス）に限定し、
`eval` や任意コード実行は行わない。

```python
from probe_agent import probe

@probe(component_id="order-service", projection={
    "name": "orders",
    "output": {
        "fields":  {"order_id": "$.order.id", "skus": "$.items[*].sku"},
        "metrics": {"item_count": {"op": "count", "path": "$.items[*]"}},
        "samples": {"first_skus": {"path": "$.items[*].sku", "limit": 5}},
    },
    "entities": [{"type": "order", "id_path": "$.order.id", "role": "source"}],
    "redact": ["$.customer.email"],
})
def handle(order):
    ...
```

- 演算は `len` / `count` / `exists` / `sha256` の有限集合のみ（`op`）。`samples` は先頭 N 件。
- `entities[].id_path` で抽出したエンティティは Phase 1 の lineage に反映される。
- `redact` 指定のパスは保存前にプレースホルダへ置換される。
- 上限超過時は決定的に丸められ `truncated=true` になり、`data_hash` が常に付与される。
- spec は登録時に検証（**fail closed**、不正な spec は即エラー）。実行時の抽出エラーは
  **非致命**で、対象関数は動き続け projection のみ診断として落ちる。
- 入力の root は `{"args": [...], "kwargs": {...}}`、出力の root は戻り値。
- `set_projection(component_id, spec)` でも登録できる。
- shadow モードでは、projection の `output` セクションが current 出力
  (`phase=shadow_current`)と candidate 出力(`phase=shadow_candidate`)にも適用され、
  shadow スレッド内で抽出されて比較用に送信される(Issue #150)。production の返り値は
  不変で、candidate がエラーなら `shadow_candidate` は送られない。

## 環境変数

| 名前 | デフォルト | 説明 |
| --- | --- | --- |
| `PROBE_ENABLED` | `true` | `false` にすると完全に無効化 |
| `PROBE_SERVER_URL` | `http://localhost:8000` | Control Server URL |
| `PROBE_DEFAULT_MODE` | `trace` | policy 取得失敗時の既定モード (`off`/`trace`/`shadow`) |
| `PROBE_POLICY_TTL` | `10` | policy キャッシュ秒数 |
| `PROBE_HTTP_TIMEOUT` | `2` | HTTP リクエストのタイムアウト秒数 |
| `PROBE_SHUTDOWN_TIMEOUT` | `10` | atexit 時に shadow 完了を待つ最大秒数 |
| `PROBE_PROJECTION_MAX_BYTES` | `8192` | projection データの最大バイト数（超過で決定的に truncate） |
| `PROBE_PROJECTION_MAX_FIELDS` | `64` | projection の最大フィールド数 |
| `PROBE_PROJECTION_MAX_SAMPLES` | `20` | sample の最大要素数 |

## 設計メモ

- 標準ライブラリのみで動作（追加依存なし）
- Control Server が落ちていても元関数の実行は影響を受けない
- shadow 実行はバックグラウンドスレッドで行い、返値は常に元コンポーネント
- shadow 入力は呼び出し時点で `deepcopy` され、呼び出し元の事後変更の影響を受けない（deepcopy 不能な値は参照を渡す fail-safe フォールバック）
- 短命プロセスでも `atexit` フックが `flush()` を呼び、shadow 結果送信完了を待つ（最大 `PROBE_SHUTDOWN_TIMEOUT` 秒）
