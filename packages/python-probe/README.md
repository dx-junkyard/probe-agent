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

## 環境変数

| 名前 | デフォルト | 説明 |
| --- | --- | --- |
| `PROBE_ENABLED` | `true` | `false` にすると完全に無効化 |
| `PROBE_SERVER_URL` | `http://localhost:8000` | Control Server URL |
| `PROBE_DEFAULT_MODE` | `trace` | policy 取得失敗時の既定モード (`off`/`trace`/`shadow`) |
| `PROBE_POLICY_TTL` | `10` | policy キャッシュ秒数 |
| `PROBE_HTTP_TIMEOUT` | `2` | HTTP リクエストのタイムアウト秒数 |
| `PROBE_SHUTDOWN_TIMEOUT` | `10` | atexit 時に shadow 完了を待つ最大秒数 |

## 設計メモ

- 標準ライブラリのみで動作（追加依存なし）
- Control Server が落ちていても元関数の実行は影響を受けない
- shadow 実行はバックグラウンドスレッドで行い、返値は常に元コンポーネント
- shadow 入力は呼び出し時点で `deepcopy` され、呼び出し元の事後変更の影響を受けない（deepcopy 不能な値は参照を渡す fail-safe フォールバック）
- 短命プロセスでも `atexit` フックが `flush()` を呼び、shadow 結果送信完了を待つ（最大 `PROBE_SHUTDOWN_TIMEOUT` 秒）
