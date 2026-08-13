# UI 用語集とラベル規則 (Issue #374)

同じ改善ループの中に `candidate version` / `variant` / `Replay Set` /
`Replay run` / `shadow` / `Experiment` が短時間で登場し、どれが何を指すのかが
画面から読み取れなかった。ここを用語の基準にする。

## 1. 利用者向けの言い換え

内部データモデル名は主導線に出さない。出す場合は、この対応で言い換える。

| 内部の名前 | 利用者向けの言い方 | 何を指すか |
| --- | --- | --- |
| Candidate Session | 改善セッション | 1つの component に対する改善作業のまとまり |
| Candidate Version | 候補 v1, v2 … | 生成された差分1つ。immutable |
| Variant | 比較する実装 | 1回の比較で並べる baseline / 候補 |
| Replay Set | 評価に使う Trace の集合 | 比較の母数になる、記録済み入力の集合 |
| Replay Run | 比較の実行 | 隔離環境での1回の実行 |
| Shadow | 本番並行実行 | 本番トラフィックで候補も動かして記録する（戻り値は変えない） |
| Experiment | 実験 | 複数 variant を隔離ワークスペースで実行し、採否を記録する単位 |
| Snapshot | Snapshot | 固定した commit のコード一式（固有概念なのでそのまま） |

「Replay」「Trace」「Snapshot」「Experiment」「System」「Capability」は
固有の製品概念なのでそのまま使う（CLAUDE.md の Dashboard UI言語規約）。

## 2. 操作ラベルの規則

操作名は「何が起きるか」ではなく「**何が作られるか**」が分かる形にする。
監査で最も強く出た指摘がこれで、「送信」「promote」「Experimentへ送る」は
どれも結果を説明していなかった。

| 悪い例 | 良い例 | 作られるもの |
| --- | --- | --- |
| 送信 | 候補を生成 | 候補 version（差分） |
| promote | Experiment 作成へ進む | Experiment の下書き（作成はまだ） |
| 実行 | baseline と比較する | Replay の実行結果 |
| 適用 | 差分をダウンロード | ローカルの .patch ファイル |

改善ループの各段階が「その操作が作るもの」を画面上で述べる責任を持つ
(`components/improvement-loop/model.ts` の `produces`)。

Overview の「次にすること」は 1 件だけで、操作名に加えて**選定理由・完了条件・
完了後に得られる価値**を必ず併記する(Issue #383)。押せない操作を disabled で
並べない -- 操作が無い状態は「処理中です」「判定できませんでした」という
文章で表す。

## 3. 状態語の規則

**1つの語に2つの事実を持たせない。** Issue #366 の不具合はすべてこの形だった。

| 語 | 意味すること | 意味しないこと |
| --- | --- | --- |
| `ready`（Snapshot） | 解析処理が完了した | HEAD と一致している |
| `current`（Snapshot） | HEAD と一致している | 解析が完了している |
| `receiving`（接続） | 一度でも受信した（累積） | いま受信している |
| `receiving_now`（鮮度） | いま受信している | 過去に受信した |
| `active`（token） | いま認証に使える | 失効していないだけ |
| `partial`（Replay） | 一部マスクされた入力で復元できる | 復元できない |
| `not_captured` | capture 設定自体がない | capture に失敗した |
| `new`（Overview finding） | 前回の理解確認より後に発生した | 重要度が高い |
| `ongoing`（Overview finding） | 前回の確認時点から続いている | 解消済み |
| `not_compared`（Overview finding） | 比較の基準がまだ無い | 新しい発見が無い |
| `no_findings`（Overview） | 比較した結果、重要な新規発見が無い | 比較していない |
| `unavailable`（Overview） | 取得に失敗した | 該当が無い |
| `waiting`（Overview 主操作） | システムが処理中で、押す操作が無い | 操作が禁止されている |
| `unavailable`（Overview 主操作） | 判定に必要な事実を読めなかった | まだその段階に達していない |
| `observed_component_count` | window 内に trace を出した component 数 | 観測済み Capability 数 |
| `not_computed`（coverage） | 算出していない | カバレッジが 0 |
| `developer_intent` | 開発者が表明した意図・目標 | 開発者が下した採否判断 |
| `developer_decision` | 採否・確認などの判断記録 | 開発者が表明した意図 |
| `mixed`（provenance） | 集約した出所が複数に割れた | 出所が不明 |
| `publish_instrumentation` | 計測 patch を公開する | 採用した改善変更を公開する |
| `no_baseline`（Overview） | 開発者がまだ理解を確認していない | 基準を読めなかった |
| `unavailable`（baseline） | 基準を読めなかった | 開発者が確認していない |

## 4. 空状態・失敗状態

空状態は3つを必ず書く。

1. 何が無いのか（事実）
2. なぜ無いのか（前提不足があれば）
3. 次にする1操作（CTA）

「まだありません」だけで終わらせない。

**「無い」と「分からない」を同じ文言にしない。** Overview の「今わかったこと」
はこの区別を 3 つの空状態として持つ(Issue #382): 比較した結果として発見が無い /
比較の基準がまだ無い / 取得に失敗した。Runtime health も同じで、「取得できま
せんでした」を「受信が止まっています」と書いてはならない -- 前者は観測できて
いないという事実で、後者はシステムについての主張である。

## 5. アクセシビリティ

- icon-only ボタンには `aria-label` を付ける（copy / revoke / close / collapse）。
- 状態を色だけで表さない。必ずテキストラベルを併記する。
- 折りたたみは `<details>` / `aria-expanded` を使い、DOM から要素を消さない。
