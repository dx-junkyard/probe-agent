# 設計メモ

## SDK の落ちない原則

SDK は host application の実行を絶対にブロックしない。

- Control Server への HTTP は短い timeout (`PROBE_HTTP_TIMEOUT`、既定 2 秒)
- 通信失敗は全て握りつぶし、`logger.debug` でのみログを出す
- policy 取得失敗時はキャッシュ済み policy、なければ `PROBE_DEFAULT_MODE` を使う
- shadow 実行は別スレッドで動かし、candidate 例外も握りつぶす（current の返値は影響を受けない）

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
