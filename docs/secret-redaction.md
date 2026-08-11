# 秘密値の redaction と漏えい時の対応 (Issue #367)

Trace / Replay / 候補生成の経路で秘密値を保持しないための契約と、既に保存
されてしまった秘密値への対応手順をまとめる。

## 1. 二層の redaction

秘密値は「キー名で分かるもの」と「値の形でしか分からないもの」の2種類があり、
どちらか一方だけでは塞げない。そのため層を2つ持つ。

| 層 | 実装 | 判定対象 | 例 |
| --- | --- | --- | --- |
| 構造 (キー名) | `probe_agent/redaction.py` | dict のキー、関数引数名、**オブジェクトの属性名** | `{"api_key": ...}` / `Config(api_key=...)` |
| 値の形 | `probe_agent/secret_patterns.py` | 文字列中の既知credential形式 | `AKIA...` / `ghp_...` / `Bearer ...` / `postgres://u:pw@h` |

いずれも Core Design Principle 6 の「明示的な有限集合」に限定している。
エントロピー推定や「ランダムに見える」といった推測は行わない。未知形式の
秘密値は、キー名か代入形 (`api_key=...`) のどちらかに現れた場合のみ検出される。

### 1.1 構造層がオブジェクトまで降りる理由

`redact_sensitive` は dict/list/tuple/set のみを走査し、ユーザー定義
オブジェクトは走査しない（`replay_capture` では未知オブジェクトは
`unsupported` marker になり値を持たないため、これで十分だった）。

しかし repr 経路は逆で、`repr(config)` はそのオブジェクトが持つ属性を
すべて印字する。これが Issue #367 で観測された実際の漏えい経路である。
そのため repr 経路専用に `redact_for_repr` を用意し、

- dataclass / `__dict__` / `__slots__` を持つオブジェクトは型名を保った
  代理オブジェクトに置き換え、秘匿属性名の値をマスクする
- 状態を読み取れないオブジェクトは repr を一切描画せず `<TypeName>` にする
- 循環参照と深さ超過は打ち切る

という決定的な規則で走査する。カスタム `__repr__` は迂回されるが、型名は
残るので「何が渡されたか」は読み取れる。

## 2. redaction が適用される境界

| 境界 | 実装 | 目的 |
| --- | --- | --- |
| SDK 送信前 | `probe_agent/decorator.py` の `_payload_repr` / `redact_text` | 秘密値をプロセス外へ出さない |
| Control Server 保存前 | `app/trace_redaction.py` (`POST /traces`) | 旧SDK・非SDKクライアント・`PROBE_PAYLOAD_MODE=full` に対する多層防御 |

**保存前**であることが重要で、表示時のマスクでは平文がディスクとエクスポート、
および Replay / Candidate Studio / Workspaces のすべての下流に残ってしまう。
保存前に落としているため、下流は「マスクを忘れる」ことができない。

`PROBE_PAYLOAD_MODE=full` は詳細度の選択であり、秘密値送信への同意ではない。
full でも両層は必ず適用される。

### 2.1 LLM 境界との関係

`app/llm_secret_redaction.py` は別モジュールのままにしてある。統合しない
理由は、要求が逆向きだからである。

- **保存境界**は過剰マスク側に倒す。マスクしすぎても失うのは可読性だけで、
  漏らせば取り返せない。
- **prompt 境界**はマスクしすぎると推論品質が落ちる。`password` や `token`
  という語が出てくるだけの正常な文章をマスクしてしまうと、モデルに渡る
  意味が変わる。

そのため prompt 境界は AWS / GitHub / PEM という誤検知のほぼ無い4規則に
限定している。候補生成が読む Trace は保存時点で既にマスク済みなので、
Trace 由来の秘密値が prompt 境界に到達することはない。

## 3. Replay への影響

`input_capture` は Replay で実際に候補実装へ渡される値なので、ここが
マスクされた場合は「元の呼び出しを再現できない」ことを意味する。

- Control Server がマスクした場合、`replayability` を `replayable` →
  `partial` に落とし、理由コード `redacted` を付与する。
- 分類は一方向にしか動かない。`unreplayable` が `partial` に上がることはない。
- Dashboard は「表示上のマスク」と「Replay 入力が欠けたマスク」を別の文言で
  表示する。両者を1つの警告にまとめてはならない。

## 4. 既存データへの対応手順

取り込み時 redaction の導入前に保存された行は一度もスキャンされていない。
`traces.redaction_json` が `NULL` の行がそれにあたる。
`{"redacted": false}` （スキャン済み・検出なし）とは別状態である。

### 手順

1. **影響範囲を読み取り専用で確認する**

   ```
   GET /traces/redaction-audit
   ```

   `unscanned_rows` / `affected_rows` と、行ごとの規則名・フィールド名が返る。
   **一致した値そのものは返さない。**

2. **漏えいした credential をローテーションする**

   これを先に行う。データを消しても、保存されていた間に露出していた事実は
   消えない。`findings[].rules` がどの種類の credential かを示す。
   probe-agent 側から自動化することはできない（発行元がプロダクト外にある）。

3. **保存済みデータを書き換える**

   ```
   POST /traces/redaction-rescan
   ```

   `redaction_json IS NULL` の行のみを再スキャンして書き換える。冪等であり、
   検出のなかった行にも「スキャン済み」を記録するので、2回目以降は
   `scanned_rows: 0` になる。`rotation_required: true` は手順2が必要である
   ことを示す。

4. **確認する**

   `GET /traces/redaction-audit` の `unscanned_rows` が 0 になる。

### 消したくない場合

再スキャンは破壊的である（それが目的）。監査のために平文を残す必要がある
場合は、先に DB のバックアップを取得し、そのバックアップをアクセス制限下に
置くこと。Dashboard の閲覧権限があることは、秘密値を表示してよい理由には
ならない（Issue #367 非目標）。

## 5. 追加のキー名・形式

- キー名: `probe_agent/redaction.py` の `SENSITIVE_KEYS`。完全一致・小文字比較
  なので、追加は過剰マスクにしかならず、漏れを生むことはない。
- 値の形: `probe_agent/secret_patterns.py` の `_RULES`。ベンダーが公開して
  いる prefix / 構造マーカーを持つものだけを追加する。長さやランダム性だけを
  根拠にした規則は追加しない。

どちらも Control Server が同じモジュールを import しているため、片側だけが
更新されることはない。
