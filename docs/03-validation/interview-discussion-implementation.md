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

## 2026-10-17: Vision・UX・Feature の接続ヒアリング

通常の Interview 対話にはコードの context pack と Understanding は渡っていたが、
保存済みの UX / Product Feature 設計は渡っていなかった。質問指示も目的から API / probe
へ進む形だったため、体験と必要な機能の接続を既存の記録から聞く材料が不足していた。

- `interview_design_context.py` に System 隔離・件数/文字数上限付きの read-only 参照を追加。
  Objective / Milestone / Gap / Journey / Requirement / Feature の既存 domain reader を使い、
  保存済み関連と再確認状態を保持。現在の設計と固定された premise を区別する。
- Interview 対話と画面アシスタントが同じ参照を利用。質問は関心起点で未確認の接続を一つずつ
  聞く指示へ変更。監査用 prompt version は `interview-v8` / assistant `v3`、出力schemaは変更なし。
- 「会話からの補足」に相談文の入力補助を追加。起点sessionを保持し、送信・保存は行わない。
- docs目次・Workflow・canonical Understanding・Assistant Discussion・Adapterに残っていた
  補足機能の「未実装」表記を、基礎実装済み・全受入条件の達成未確認へ整合。

検証:

- Control Server: `test_interview_agent.py` / `test_interview_design_context.py` /
  `test_interview.py` / `test_assistant.py` の116件通過。
- prompt version更新後: `test_assistant_grounded_answer.py` と
  `test_interview_agent.py` の57件通過（前項との重複38件、計135件）。
- Dashboard: `interview-design-entry.test.tsx` / `assistant-discussion-thread.test.tsx` /
  `interview-cockpit.test.tsx` の73件通過。production build通過。
- 新規テストはSystem隔離、読取りによる変更なし、Requirement更新後のlink stale、
  画面側への同じcontext供給、省略と取得失敗の区別、LLMへの設計入力、相談入口の起点保持を確認。

未確認・制限:

- 実LLM・実利用者による質問の自然さ、重複質問の減少、実ブラウザ・音声の完走は未確認。
- 設計参照は各層の新しいidentity最大5件、JSON文字数6000以内。全件探索・関心による選別はしない。
- 回答による設計自動作成・自動リンク確定は追加していない。既存のレビュー・保存境界を使う。
- buildには既存の`env.js`とbundle sizeの警告がある。
