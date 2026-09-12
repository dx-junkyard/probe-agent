# Epic #457 / PR #462 実装監査（2026-09-12）

## 対象と判断

対象は #451〜#456、#458、#459、#461 と親 #457。
監査開始時の `ura` は `c9859ac556497d8a62cb677f8926268ec010ba3c`。
PR #462 は `ura` → `main` の統合PRで、依存する #460 の基準
`93ab9e1f4ab1b724c4bcc1d5d674c25999290806` を含む。今回の監査修正を同PRに追加した。
Issueの整理とPRのマージは別操作であり、この監査ではマージしない。

製品Visionから判断できる不具合は修正した。実利用者・課題・支援技術環境の決定と
人間による評価の残件は [#463](https://github.com/dx-junkyard/probe-agent/issues/463) へ移管する。
その条件付きで元Issueを整理する。未実測の受入項目を実測済みと読み替えない。

## 監査修正と証拠

| Issue | 確認・修正 | 主な回帰証拠 |
| --- | --- | --- |
| #451 | field/section診断とvalidation_stateのdraft伝搬、診断redaction/32KiB、編集後の古いvalidation応答の遮断 | `test_ui_draft_context.py`、`ui-draft-form-validation.test.tsx`、既存text/voice snapshot suite |
| #456 | operation resultと有限handler登録を確認。form定義やmountだけで対応済みにしない | `test_discussion_operation_result.py`、registry/contract parity |
| #461 | discussion ownerと共有premise、旧JU/Interview consumer、ID/FKとbackup/restore互換を確認 | joint_understanding/discussion/premiseとmigration suite |
| #452 | 未mountのRequirementフォームを開いてACKできるよう接続。対象付きACK、同token再配送の再反映防止、競合解決、保存後の新編集を遅延成功で閉じない。receiptをURLの参照で復元 | `test_discussion_prefill.py`、`ux-design-studio.test.tsx`、実API保存・reload |
| #453 | Gapのartifact/source/evidence参照をcontextに含める。Requirement/Solutionを正しいtabで選択し起点Gapを保持 | registry/bundle tests、実ブラウザGap→Requirementリンク |
| #454 | 受入条件のchild操作を実フォームへ接続。field単位競合、reserved key、順序、検証メモ、有限verification_method。Gapの許可された参照を既存domain handlerへ委譲 | `acceptance-criteria-draft.test.ts`、`test_assistant_discussion_proposals.py` |
| #458 | 追加取得結果を初期pageと併合して表示。Proposalのdurable manifestからroot不変の依存更新もstale判定。claimsのroot解決をDB書込transactionの外へ移動 | bundle/claims tests、Objective依存更新→Proposal拒否、追加取得page保持テスト |
| #455 | Discussion所属JUをその場で再表示・調査。任意research_focusと停止理由、現在findingの同thread Proposalへの還流、暫定結果の別表示 | hypothesis/JU suite、Interviewなしの実LLM調査と出典表示 |
| #459 | server next_actionへdraft/保存不明を渡す。既存リンクとSystem切替の未保存離脱確認。パネルのスクロール領域を修正しProposal操作へ到達。保存receipt付きでGapへ復帰 | next_action/返り先/フォームtests、390pxブラウザ、明示保存後の未確定・未対応維持 |

Gap参照拡張のSQLite migrationは旧child列を保持してatomicに再構築する。
旧proposal item/prefill ID、FK整合、再実行、backup/restoreと新参照種別を
`test_discussion_gap_relation_migration.py` で確認した。

## 自動検証

- Backend: `LLM_PROVIDER=mock python -m pytest apps/control-server/tests -q -k 'discussion or premise or joint_understanding or ux_design or solution_design'` — **606 passed, 4007 deselected**, 383.43秒。
- migration関連の独立確認 — **24 passed**。追加のGap参照migrationも上記606件に含む。
- Dashboard: `npm --prefix apps/dashboard test` — **56 files / 1172 passed**。
- Dashboard: `npm --prefix apps/dashboard run build` — 成功。既存の大きなchunk警告あり。
- Dashboard: `npm --prefix apps/dashboard run lint` — **0 errors / 58 warnings**（既存warning）。
- `git diff --check` — 成功。

テストのLLMはmock/固定応答で、下記の外部LLM利用とは別の証拠。
Backend全4613件を実行したという意味ではない。

## 実API・実LLM・ブラウザの観察

通常データから隔離したSystem `PR462 acceptance audit / test` を作成し、本番実装の
ローカルAPI/Dashboardを利用した。Gap `save-uncertainty`、Requirement
`safe-save-requirement` を既存domain APIで登録し、artifact linkで関連付けた。
本番運用環境へのデプロイ検証ではない。

利用者が承認した範囲で既存OpenAI設定（api.openai.com、gpt-5.4 / gpt-5.4-mini）を使用。
provider/model/status/is_mockの監査で実応答を区別した。対象は検証用Gap・要件・会話と
このrepoの関連コード/文書。秘密情報や検証DBはコミットしない。

1. Gapの照合でfact/inference/unknownと出典を生成した。
2. 仮説を明示昇格し、Interviewを作らずDiscussion所属JUを取得した。
3. 最初の調査は候補不足で `unresolved`、findingなし。成功と扱わず、任意の
   `research_focus` に関連語を入れて再実行した。実LLMによる出典付きfindingを表示し、
   コード/文書の参照と、未観測のruntime動作が別であることを確認した。
4. Gap contextの関連Requirementリンクから同要件を選択し、`returnTo` が起点Gapを保持した。
   別targetは別threadであり、要件文の具体化を指示した。元JUは元threadに残る。
5. 実LLMによる要件文と4受入条件のProposalをレビューした。初回はモデルが
   verification_methodに表示名「手動検証」を出したため、生成promptへ有限enumを提示し、
   生成/適用で不正値を拒否するよう修正。再生成は正規値 `manual_review` で成功した。
6. 9選択項目をフォームへ反映し、ACK後の未保存表示と正本revision 1を確認した。
   Requirementを明示保存するとrevision 2とreceiptを取得した。要件は未確定のまま。
7. receiptを保持したリンクで同じGapへ戻り、Gapは未対応のままであることを確認した。
   390×844へ設定してreloadし、保存成功/版参照が復元され、documentのscrollWidthは390だった。
   保存済み要件を再度開いてもreceiptと起点Gapが保持された。

実LLM生成候補の本文をフォームへ手入力する操作は0回。参照・reserved keyはAPI/リンク/
prefillで渡った。要件具体化の指示と調査対象を絞る語は入力した。これはJUの全findingが
別targetの新threadへ自動伝搬したという意味ではない。

390pxのProposal選択/フォーム反映と、保存後のGap/Requirement往復を確認した。
途中のタスク再開でviewport設定が標準幅へ戻ったため、最初のrevision 2保存操作そのものは
標準幅だった。キーボードのEnterによる反映・保存・復帰を確認したが、全手順を連続した
Tab操作だけで完走した計測とは区別する。実スクリーンリーダーの発話は未測定。
狭幅の離脱confirmの追加操作はブラウザ自動化がtimeoutしたため、成功の証拠に含めない。

開発中の修正・再起動・再試行を含むため、全体所要時間、代表利用者の画面移動数、
状態誤認率、出典到達失敗率は未測定。個別のリンク到達成功を全出典成功率としない。
必要な実利用評価を#463で行う。CIやmock、アクセシビリティツリーをその代用としない。

## 維持する境界

prefillはdomain保存ではない。保存は確定・採用・Gap解消・Milestone達成・publishではない。
JU昇格は調査実行ではない。調査の候補不足/失敗、暫定仮説、staleな根拠はfactへ変えない。
全targetへのprefill展開や未保存draftの永続復元は今回実装済みと約束しない。
