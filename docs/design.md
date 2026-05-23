# 設計メモ

## SDK の落ちない原則

SDK は host application の実行を絶対にブロックしない。

- Control Server への HTTP は短い timeout (`PROBE_HTTP_TIMEOUT`、既定 2 秒)
- 通信失敗は全て握りつぶし、`logger.debug` でのみログを出す
- policy 取得失敗時はキャッシュ済み policy、なければ `PROBE_DEFAULT_MODE` を使う
- shadow 実行は別スレッドで動かし、candidate 例外も握りつぶす（current の返値は影響を受けない）

## shadow の入力スナップショット

shadow candidate には `copy.deepcopy` で複製した args/kwargs を渡す。
これにより:

- current 実行中に fn が引数を mutate しても candidate には元の値が渡る
- current 呼び出し後に host 側が引数を mutate しても candidate には元の値が渡る

deepcopy できない値（file handle / lock / socket など）に対しては元参照を
そのまま渡す fail-safe フォールバックを用意。host application は壊さない。

## 短命プロセスでの shadow 配送

shadow スレッドは `daemon=True` だが、初回 spawn 時に `atexit.register(flush)`
を行い、インタプリタ終了時に最大 `PROBE_SHUTDOWN_TIMEOUT` 秒だけ in-flight な
shadow スレッドを join する。

これにより CLI / バッチ系の短命プロセスでも shadow 結果が落ちにくい。
タイムアウトに達した場合は join を諦めて終了するので、ハングしたスレッドが
プロセス終了を無限に阻害することはない。

## モードの意味

| mode | 元関数 | trace 送信 | candidate 実行 | 返値 |
| --- | --- | --- | --- | --- |
| off    | ✅ | ❌ | ❌ | current |
| trace  | ✅ | ✅ | ❌ | current |
| shadow | ✅ | ✅ | ✅ (背後で) | current |

## SQLite スキーマ

`apps/control-server/app/db.py` に集約。テーブルは `components` / `traces` / `shadow_results` の 3 つ。

- `components.mode` が現在の policy
- `traces.input_json` は JSON 文字列で保存（input は `{args: [...], kwargs: {...}}` の安全な repr）
- `shadow_results.evaluation` は手動評価結果（NULL は未評価）

## 入出力のシリアライズ

任意オブジェクトを `repr()` で文字列化し、4 KB を超える場合は切り詰める。
これは MVP として「副作用の少ない pure-ish 関数」を対象にしているため、
構造化シリアライズは将来課題。

## 今後の検討

- バイナリ / 大きなテキストの扱い（圧縮、別ストア）
- candidate を `set_candidate` 以外（プラグインや別プロセス）から登録する経路
- LLM ベースの自動評価
- CI 上で shadow をまとめて回すためのバッチランナー
