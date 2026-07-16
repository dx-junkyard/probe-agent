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
  `redact` パスと重なる `id_path` はエンティティ化されない(fail closed)。
- `redact` 指定のパスは保存前にプレースホルダへ置換される。dict / list 構造は
  値だけを精密に置換する。オブジェクト属性など構造的に置換できない経路では、
  その redact パスと重なるパスの抽出値を**丸ごとプレースホルダに置換**する
  (fail closed — 取りこぼしより過剰マスクを選ぶ)。
- 上限超過時は決定的に丸められ `truncated=true` になり、`data_hash` が常に付与される。
- spec は登録時に検証（**fail closed**、不正な spec は即エラー）。実行時の抽出エラーは
  **非致命**で、対象関数は動き続け projection のみ診断として落ちる。
- 入力の root は `{"args": [...], "kwargs": {...}}`、出力の root は戻り値。
- `input` セクションは**関数実行前**に抽出される。関数が引数を破壊的に変更しても
  input projection は呼び出し時の値を反映し、shadow candidate が受け取る snapshot と
  同じ入力を表す(Issue #146 の deepcopy 相互作用)。
- `set_projection(component_id, spec)` でも登録できる。
- shadow モードでは、projection の `output` セクションが current 出力
  (`phase=shadow_current`)と candidate 出力(`phase=shadow_candidate`)にも適用される
  (Issue #150)。`shadow_current` は**呼び出し元スレッドで**返却直後に抽出され
  (呼び出し元による返り値の mutation と競合しない)、candidate 出力は shadow
  スレッド内で抽出される。production の返り値は不変で、candidate がエラーなら
  `shadow_candidate` は送られない。

## サンプリング（Issue #152 / Phase 7）

高頻度コンポーネントで lineage / projection の量を抑えるため、`@probe(sample_rate=...)`
で **決定的なサンプリング**ができる。

```python
@probe(component_id="hot-path", sample_rate=0.1, projection=...)
def handler(x):
    ...
```

- `sample_rate` は `trace_id` のハッシュに基づく決定的判定(seed 不要)。同じ trace の
  input / output / shadow projection と lineage は**まとめて残るか、まとめて落ちる**。
- **trace 本体は常に全件送信**され、既存挙動は変わらない。間引かれるのは lineage
  (span / correlation / flow / entities)と projection のみ。
- `None`(既定)は全件保持。`0.0` は lineage/projection を全て落とす。
- 保存済みデータの期間・件数 retention は Control Server 側の設定(`/retention/*`)で行う。

## 再実行可能な入力キャプチャ（Issue #242 Phase A / #243）

`@probe(replay_capture=...)` で **component 単位の opt-in** により、呼び出し引数を
JSON で往復（round-trip）可能な構造として記録できる。後続フェーズ（リプレイ実行・
オフライン shadow）が入力を機械的に復元するための基盤で、既存の `input` / `output`
（repr 文字列）はそのまま変わらない。

```python
from probe_agent import probe

@probe(component_id="normalizer", replay_capture=True)
def normalize(payload):
    ...

# redact 付き（パス文法は projection と同一。登録時に fail closed で検証）
@probe(component_id="auth-check", replay_capture={"redact": ["$.kwargs.password"]})
def check(user, password=None):
    ...
```

- 未指定（`None` / `False`）なら**一切のキャプチャ処理を行わず**、trace ペイロードに
  新フィールドは付かない。opt-in 済みでも失敗は常に非致命（対象関数の返値・例外・
  trace 送信に影響しない）。
- キャプチャの root は `{"args": [...], "kwargs": {...}}`。値は canonical JSON に
  エンコードされる。JSON ネイティブ型（`None`/`bool`/`int`/`str`/有限 `float`/`list`/
  文字列キー `dict`）はそのまま、非ネイティブ型は予約キー `"__probe__"` の明示
  エンコードを使う:
  - `tuple` → `{"__probe__": "tuple", "items": [...]}`
  - `set` / `frozenset` → `{"__probe__": "set"|"frozenset", "items": [...]}`
    （items は canonical JSON 表現で決定的にソート）
  - `bytes` → `{"__probe__": "bytes", "b64": "..."}`
  - 非有限 float → `{"__probe__": "float", "value": "nan"|"inf"|"-inf"}`
  - 非文字列キー dict / `"__probe__"` キーを含む dict →
    `{"__probe__": "dict", "items": [[k, v], ...]}`（decode の曖昧さを排除）
  - 上記以外 → `{"__probe__": "unsupported", "type": "<型名>"}`（raw 値や repr は
    **決して埋め込まない**）
- **replayability 分類（決定的・有限集合、Principle 6）**: 劣化なし →
  `replayable`、キャプチャは保存されたが一部の値が劣化 → `partial`、キャプチャを
  保存できない → `unreplayable`。理由コードは
  `unsupported_type` / `redacted` / `depth_limit_exceeded` / `size_limit_exceeded` /
  `round_trip_failed` / `capture_failed` / `redaction_blocked` の有限集合。
- `redact` は projection と同じパス文法・同じマスク文字列を使い、**エンコード前**に
  root へ適用する。構造的に置換できない redact パスは **fail closed でキャプチャ全体を
  破棄**し `unreplayable` / `redaction_blocked` になる（マスク漏れより破棄を選ぶ）。
- サイズ上限（`PROBE_REPLAY_CAPTURE_MAX_BYTES`、既定 65536）超過時はキャプチャ全体を
  破棄して `unreplayable` / `size_limit_exceeded`（**切り詰めた JSON は round-trip
  できないため部分保存はしない**）。ネスト深さは 20 段まで（超過ノードは
  `unsupported` マーカー化 + `depth_limit_exceeded`）。
- エンコード後に decode → 元値と構造比較する round-trip 検証を行い、不一致は
  `round_trip_failed` として `partial` に落とす（NaN は isnan で比較）。
- trace には `input_capture` / `replayability` / `replay_reasons` が追加される
  （契約は `shared/schemas/trace_event.schema.json`）。

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
| `PROBE_REPLAY_CAPTURE_MAX_BYTES` | `65536` | replay capture（canonical JSON）の最大バイト数。超過でキャプチャ全体を破棄し `unreplayable` / `size_limit_exceeded`（部分保存はしない） |

## 設計メモ

- 標準ライブラリのみで動作（追加依存なし）
- Control Server が落ちていても元関数の実行は影響を受けない
- shadow 実行はバックグラウンドスレッドで行い、返値は常に元コンポーネント
- shadow 入力は呼び出し時点で `deepcopy` され、呼び出し元の事後変更の影響を受けない（deepcopy 不能な値は参照を渡す fail-safe フォールバック）
- 短命プロセスでも `atexit` フックが `flush()` を呼び、shadow 結果送信完了を待つ（最大 `PROBE_SHUTDOWN_TIMEOUT` 秒）
