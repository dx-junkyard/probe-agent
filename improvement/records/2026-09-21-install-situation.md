# S-20260921-01: 新しい観点パックと既存知識の接続

**状況形式:** situation/0.1。**役割語彙:** roles/0.1。**日時:** 2026-09-21。
**配布:** 0.1.0-preview.1 / perspectives/0.1 / workflow/0.1。
**対象版:** probe-agent `3530943`。配布manifestのsource snapshotを[取得台帳](2026-09-21-source-ledger.json)に保持。

- 仕組み: pipeline。操作: migrate（手動の並置導入。既存事例の形式変換なし）。
- 構成: entries(store) → index生成(transform) → index(projection) → 開発担当(planner)。
  EC観点(source) → ローカル対応表(projection) → 開発担当(planner)。
- 目的: 状況から知識を選ぶ入口と、利用結果を戻す記録先を作る。
- 保持条件: 既存20件のID・分類・履歴・本文、既存の権限、過去の検証記録。
- 期待と実際: 新版を利用したいが、既存の入口・計画・結果に新版の版・状況・提示記録がない。
- 許可範囲: ユーザーの依頼によるローカル導入・検証。外部送信・製品実行は対象外。

| 観測ID | 対象・判断 | 根拠・時点 | 性質・適用性 |
| --- | --- | --- | --- |
| OBS-EC-01 | 旧事例20件は存在、新版との接続はgap | 基準版のdocsと配布一式を2026-09-21に照合 | 事実 / current |
| OBS-EC-02 | 新版は手動運用で、自動移行・自動収集は提供しない | 配布README・COMPATIBILITY・workflow | 配布の仕様 / current |
| OBS-EC-03 | 新観点の解決効果はunknown | 配布PROVENANCEはcandidateと明記。導入先の実適用前 | 未観測 / unknown |

未観測は実開発での検索時間、解決率、記録負担、誤った適合の頻度。
原因候補は既存入口と新しい知識の参照先の未接続。製品に不具合があるという診断ではない。
追加観測は導入後の導線・原本一致・既存事例保持の検査。実効性は後続サイクルで測る。

計画の正本: [IP-20260921-02](../../docs/02-challenges-and-decisions/improvement-plans/2026-09-21-evolution-controller.md)。
提示: [G-20260921-01](2026-09-21-install-guidance.md)。
