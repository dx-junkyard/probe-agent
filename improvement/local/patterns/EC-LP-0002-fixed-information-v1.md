# EC-LP-0002: 固定情報の意味を後続操作から守る

**Pattern ID:** `probe-agent:EC-LP-0002`。**版:** 1。**状態:** candidate。
**作成日:** 2026-09-27。**配布可否:** ローカル利用。外部配布の判断は未実施。
[入口](README.md) · [運用契約](../../../docs/02-challenges-and-decisions/issue-knowledge/taxonomy.md#51-課題と解決原理の一般化候補)

## 抽象課題と保持条件

固定した履歴・証拠を参照しているはずなのに、後続操作でその内容・意味が変化する。
構成は、編集可能な情報(store) → 固定・完了(transform) → 履歴・証拠(store) → 表示・判断(projection/evaluator)。
操作は昇格、固定、完了後の編集、履歴参照。
保持条件は「固定対象の同じID・版が指す内容と意味が、後続操作によって暗黙に変わらないこと」。

症状は履歴と前提の混在、head不変での表示変更、完了後の証拠の書換え。
原因候補は固定対象への上書き、読取り側からの可変情報の混入、完了状態を見ない更新である。
何をいつ固定し、どこを編集でき、利用側が何を読んでいるかを観測して見分ける。

## 適用・非適用・未確認

- 適用候補: セッション開始前提、昇格済み版、完了証拠など、固定する内容・時点が契約で定まっている。
- 非適用: 常に最新情報を表示する契約、明示的に破棄して作り直す使い捨てデータ。
  固定対象がなく保存前の鮮度だけが問題なら[EC-LP-0001](EC-LP-0001-premise-validity-v1.md)で調べる。
- 要観測: 固定すべき範囲、合法な訂正・再開の方法、旧データの移行方針が不明。
  外部の参照先が変わる場合や分散した証拠の保全は、この事例群だけでは保証できない。

## 解決原理と派生形

固定対象と編集対象の境界を定め、書込みと読取りの双方で固定対象の意味を保つ。
更新禁止を全データへ一律に広げず、対象の契約に応じて次の派生形を選ぶ。

| 派生形ID | 選択条件 | 実例の具体策 | 固有の検証・根拠 |
| --- | --- | --- | --- |
| V1 入力と結果の分離 | 開始時の入力と、その後に生まれた結果の両方を残す | base premiseを不変にし、結果はresult revisionへ記録。新前提で続けるなら別セッション | 昇格前後でbase revision/digest/jsonが不変。IK-0005 |
| V2 固定版からの読取り | 元行の編集は合法だが、固定した版の表示は変わってはいけない | revision内の凍結IntentをBrief・Purpose Chainへ渡す | 元行を編集してもheadと固定表示が不変。IK-0010 |
| V3 完了後の更新拒否 | 完了したrun自身の測定値・実行参照は変更不可 | 更新経路へopen状態の検査を入れる | 完了後のattach_execution/record_measurementが拒否される。IK-0016 |

V1〜V3は状況に応じた派生形であり、排他的な三択ではない。
同じ機能に複数の条件があるなら補完として併用し、選択した組合せと理由を記録する。
V2では元行の編集を止めない。V3の検査だけをV2へ当てても、正しい編集まで禁止してしまう。
引用元の識別子と内容の意味を表すdigestの扱いはIK-0010の契約を参照し、一律に同一視しない。

## 検証・停止・復旧・負担

共通の受入条件は、後続の合法な操作を行っても固定対象の内容・意味が保たれること。
固定前の合法な編集も成功することを確かめる。V1〜V3の固有反例を選択内容に応じて実施する。
V2では固定版の不変と元行の編集成功の両方を検証し、単なる全面更新禁止を合格にしない。

固定境界が不明、または過去の意味を変える可能性がある場合は適用を止め、対象の契約へ戻る。
訂正は契約が定める別セッション・新revision等へ分ける。新runの作成規則は対象仕様で確認する。
移行・復旧では元の固定値を保持し、既存証拠を暗黙に書き換えない。復旧先と手順は案件の計画へ記す。
負担はsnapshotの保存量、版の参照管理、訂正導線、移行と検証。上限は案件ごとに定める。

## 根拠・型・観点・同一系譜

| 根拠IK（Project IDはprobe-agent） | 辞書の型 | 観測日・対象版 |
| --- | --- | --- |
| [IK-0005](../../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0005-immutable-premise-overwritten.md) | historical-premise-overwritten | 2026-09-13、`575a6c4`（#464第1回修正後） |
| [IK-0010](../../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0010-frozen-view-reads-live-data.md) | frozen-view-live-dependency | 対象版・観測日はリンク先のメタデータを参照 |
| [IK-0016](../../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0016-completed-evidence-overwritten.md) | finalized-evidence-mutable | 2026-08-17、#394検証ラウンド修正後。最終SHAは出典未記載 |

典型座標は`structure.representation + connection.version`、`connection.version`、
`connection.condition + governance.completion`。所属は固定対象の契約と観測から判断する。
[EC-P03との対応](../perspective-map.md)は人の判断・開始前提を守る場合に限る。
固定表示の診断では正本と参照先を追うが、EC-P04の「鮮度」を理由に最新行へ置き換えない。
[LR-001](../../../docs/04-operations/improvement-cycle/recipes/boundary-and-version.md)へ選択した派生形の反例を加える。

元の主張は各対象版の個別修正。固定境界を共通原理として整理したことは今回の解釈である。
IK-0005と0010は同じ#464の連続レビュー、0016は#394の別の機能に由来する。
異なる機能の根拠があっても、この新候補を利用した独立実績とは数えない。
取得版・出典と依存資料のハッシュは[導入台帳](../../records/2026-09-27-abstraction-source-ledger.json)に保持する。

## 適用実績・改訂履歴

適用実績: 0件。効率・誤適合・別環境への有効性は未観測。
[導入時の整合確認](../../../docs/03-validation/improvement-cycle/2026-09-27-knowledge-abstraction.md)は実績件数に含めない。
後続の利用では計画・結果・候補版・派生形ID・同一系譜・後発の見逃しを日付付きで追記する。

- 2026-09-27 v1: コーディングエージェントが提案Bを具体化。ユーザーの導入承認に基づきcandidateとして登録。
  根拠は[導入判断](../../../docs/02-challenges-and-decisions/improvement-plans/2026-09-27-knowledge-abstraction.md#7-導入承認と実施計画)。旧版なし。
  承認は導入方針に対するもの。IKの分類確定や候補の効果認定は行っていない。

## 2026-10-04追記: 参照記録

[#467提示記録](../../records/2026-09-29-assistant-answer-quality-guidance.md)で、リクエスト内共有へ
永続版管理を過剰適用しない判断に参照した。派生形の実行・効果比較はなく、独立適用実績0件を維持する。
担当はコーディングエージェント。次の固定情報保持の実案件で適用条件を再照合する。
[還流結果](../../../docs/03-validation/improvement-cycle/2026-10-04-autonomous-knowledge-loop.md)。
