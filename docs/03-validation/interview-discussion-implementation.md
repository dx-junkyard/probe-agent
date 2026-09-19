# Interview自由対話・補足の実装記録

**役割:** validation-record
**確認日:** 2026-09-16
**仕様:** [UX](../01-specifications/ux/interest-led-interview-discussion.md)、[技術設計](../01-specifications/capabilities/interview-discussion-contributions.md)

## 今回の実装範囲

- 起点Interviewを保持したBot会話、再送時のuser turn/reply重複防止、起点へのリンク。
- 永続jobによる会話の整理、引用の実在検査、関連Interviewの探索、未確定の候補表示。
- 手入力、候補編集、保留・却下、関連先の選択、補足保存、一覧と再読込。
- 補足・来歴と正準Understandingの分離。次回理解更新への入力とrevision lineage。
- Q&A回答、Intent確認、claim summary候補版へのpreview/手動反映。
- 保存・反映のtransaction、対象再検証、冪等receipt、refresh outbox。

## 自動検証

- 新規API/ドメインテスト11件: 複数Interviewリンク、再送、claim反映、正準head不変、
  stale一括保存のrollback、System隔離、closed、編集CAS、引用捏造拒否、入力collector、
  会話turnの冪等性、Q&A/Intent反映。
- 既存discussion thread・adapter registryの37件と、Change Set・canonical Understandingの
  55件を実行し通過。新規8件を含めた実行単位は45件・63件、追加新規3件も通過。
- Dashboardの既存会話・未保存draftテスト21件を通過。TypeScript検査通過。
- lintはエラー0件（既存のFast Refresh警告あり）。
- Dashboard production build通過（既存のenv.js・bundle size警告あり）。

## 未完了・未検証

仕様全体の完了を宣言しない。以下は追加実装・検証が必要。

- 実LLMでの分類・関連探索品質、IID-AC-10の代表利用者評価、実ブラウザ390px・支援技術・音声完走。
- 横断context bundleの既存auditへの統合、抽出のintelligence_runs監査、語彙の共有JSON Schema parity。
- 関連探索は20件ずつのタイトル・focusとsession参照を対象とし、追加本文を使う段階的検索は未接続。
- モバイルの内容／会話／整理候補タブ切替、UIでの関連先一部保存と残り保留の一括分割。
- 保存した仮説からJUへの専用導線、serverのallowed_actionsによる全操作可否投影。
- backup/restore・System削除における新テーブルの統合検証と、workerクラッシュ・複数worker競合の障害注入。

基礎フローの実装と上記回帰確認を保存するcommitであり、公開・デプロイや実データ更新は行わない。
