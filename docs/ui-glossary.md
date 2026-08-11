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

## 4. 空状態・失敗状態

空状態は3つを必ず書く。

1. 何が無いのか（事実）
2. なぜ無いのか（前提不足があれば）
3. 次にする1操作（CTA）

「まだありません」だけで終わらせない。

## 5. アクセシビリティ

- icon-only ボタンには `aria-label` を付ける（copy / revoke / close / collapse）。
- 状態を色だけで表さない。必ずテキストラベルを併記する。
- 折りたたみは `<details>` / `aria-expanded` を使い、DOM から要素を消さない。
