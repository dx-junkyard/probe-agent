# 状況観点と既存ナレッジの対応（probe-agent-ec/0.1）

配布版 `0.1.0-preview.1` / `perspectives/0.1`。2026-09-21のローカルな候補照合。
[配布元の条件](../../vendor/evolution-controller-0.1.0-preview.1/knowledge/perspectives.md)を先に読む。
以下は検索入口であり、4軸との一対一変換・適用認定・効果の独立証拠ではない。

| 観点 | 参照候補 | 一致する条件／限界 |
| --- | --- | --- |
| EC-P01 再実行と部分成功 | [IK-0014](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0008](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0008-partial-session-creation.md) | 保存作用・並行再送・部分保存。外部サービス作用の補償実績には拡張しない |
| EC-P02 対象・依存版 | [IK-0003](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0003-root-only-freshness.md)、[IK-0006](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0006-missing-post-inference-check.md)、[IK-0011](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0011-validation-write-race.md)、[LR-001](../../docs/04-operations/improvement-cycle/recipes/boundary-and-version.md) | 依存版・検証時機・検証と保存の原子性は別の確認項目 |
| EC-P03 判断の保全 | [IK-0007](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0007-inherited-intent-dropped.md)、[IK-0005](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0005-immutable-premise-overwritten.md) | 確認済みIntent・開始前提の保持。判断消失と表示不整合は診断で分ける |
| EC-P04 正本・派生物の鮮度 | [IK-0010](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0012](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0012-candidate-state-content-mismatch.md) | 正本と表示対象を追う。固定版は可変の最新行に置き換えない。agent memoryでの効果は未確認 |
| EC-P05 評価・反復・共有予算 | [IK-0019](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0019-review-approval-conflation.md)、[IK-0020](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0020-alternate-establishment-bypass.md) | 採用判断の分離という一部分のみ。反復・共有予算の必須条件をこの2件だけで満たすとは判断しない。要観測 |
| EC-P06 利用成立 | [IK-0002](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0002-unmounted-form-delivery.md)、[IK-0004](../../docs/02-challenges-and-decisions/issue-knowledge/entries/IK-0004-unobserved-user-outcomes.md)、[LR-002](../../docs/04-operations/improvement-cycle/recipes/user-journey.md) | 到達・保存・実利用の証拠を分ける。IK-0004は保留中で成功例ではない |

未掲載のIKも既存索引から検索する。対応しない事例を6観点へ無理に割り当てない。
原資料の解決済み状態を観点パックの成功へ転記しない。対応表の変更は根拠・旧版・判断を結果記録に残す。
