# Issue #464 実装レビュー（2026-09-13）

## 結論

Issue #464 の主要部品（System 単位の canonical head、Interview premise の捕捉、
stale 判定、CAS 昇格、rebase/branch API、Overview の canonical 投影）は実装され、
追加された対象テストも通過している。しかし、中心要件である「Interview は開始時の
正準 Understanding を不変の premise とし、同じ premise の場合だけ同じ会話を続ける」
は完全には成立していない。

以下の5点を修正するまで、想定どおりの完了とは判定できない。

## 確認範囲

- 実装コミット: `0187f38 feat(#464): System 正準 Understanding と Interview premise を分離する`
- canonical head / premise / migration / promotion のバックエンド実装
- Interview の作成、会話、Understanding rebuild、自動 refresh
- Overview の投影と Dashboard の premise UI
- Issue #464 の受入条件と `canonical-understanding.md` の設計契約

## テスト結果

- `apps/control-server/tests/test_canonical_understanding.py`: **22 passed**
- Dashboard の `premise-notice.test.tsx`、`overview-page.test.tsx`、
  `overview.test.tsx`: **66 passed**

テストは現実装との自己整合性を示すが、後述する要件逸脱を許容または未検証である。

## Findings

### [P1] 昇格が同じ Interview の不変 premise を上書きする

`promote_revision` は candidate を canonical head にした直後、昇格元セッションの
`base_understanding_revision_id`、`base_premise_digest`、`base_premise_json` を
新しい revision へ更新している。

- `apps/control-server/app/canonical_understanding.py:942-958`
- 設計契約は premise を「セッション開始時に固定し、会話中に暗黙更新しない」とする:
  `docs/01-specifications/product/canonical-understanding.md:54-56`
- 一方、同じ設計文書の `:178-180` は昇格元セッションの baseline を進めるとしており、
  文書内でも不変条件と矛盾している。

この更新後、既存の全メッセージは旧 premise で生成されたままなのに、セッションは
新 premise を開始時から持っていたように見える。次の dialogue/rebuild は新 premise と
旧会話履歴を混ぜて実行されるため、「premise が同じ場合のみ前の会話を続ける」という
要件を破る。また `interview_message` / turn に premise digest が保存されていないため、
後から境界を復元できない。

**必要な修正**

- 昇格時は `result_understanding_revision_id` だけを設定し、既存の base premise は変更しない。
- 昇格した revision を前提に続ける場合は、新規セッションまたは明示的 rebase を作る。
- もし同一セッション内の premise 切替を仕様として採用するなら、turn 単位の premise
  digest と明示的な境界イベントを必須にし、「セッション premise は不変」という契約を
 変更する。ただし、これは当初要件とは異なる。

**不足テスト**

1. R0 を premise とする S1 で R1 を生成・昇格する。
2. S1 の base revision/digest/json が昇格前後で同一であることを確認する。
3. R1 で続ける操作が新規セッションまたは明示的 rebase になることを確認する。

### [P1] LLM 呼び出し後、保存直前に premise を再検証していない

dialogue と Understanding rebuild は処理開始前に premise を確認するが、DB lock を
解放して LLM を呼び出した後、保存フェーズで再検証せず結果を書き込む。

- dialogue の事前ゲート: `apps/control-server/app/routes/interview.py:1412-1417`
- dialogue の LLM 後の保存開始: 同 `:1676-1686`
- rebuild の LLM 呼び出しと保存開始: 同 `:3466-3483`
- 自動 refresh も開始前判定だけで同じ `_rebuild_understanding` を呼ぶ:
  `apps/control-server/app/interview_refresh.py:312-386`

LLM 実行中は DB lock が解放されるため、同一プロセス・single worker 構成でも別リクエストが
canonical head を昇格できる。その場合、旧 premise から生成した assistant message、proposal、
Understanding candidate が stale と判定されることなく保存される。Issue で挙げた
「LLM処理後、保存直前の premise 再検証」の負債が残っている。

**必要な修正**

- 推論前に `base revision + base digest + head version` を実行トークンとして保持する。
- 保存 transaction の先頭でセッションを再読込し、同じ premise verdict と head version を
  CAS 条件として再検証する。
- 不一致なら推論結果を会話・candidateへ適用せず、有限の stale/conflict 結果と監査情報を残す。
- dialogue、手動 rebuild、自動 refresh の3経路で同じ関数を使用する。

**不足テスト**

LLM stub の実行中に別セッションから head を R1→R2 へ昇格し、旧 premise の推論結果が
`interview_message`、`interview_proposal`、`understanding_revision` に保存されないことを確認する。

### [P1] 確認済み Intent が次の昇格で失われ得る

canonical premise の Intent は、昇格対象 revision を生成した **1セッションだけ**から取得している。

- `confirmed_intent_rows` は `session_id` で絞り込む:
  `apps/control-server/app/canonical_understanding.py:427-435`
- `build_premise_bundle` は revision の `session_id` だけを渡す: 同 `:438-456`

新規セッションは前 premise の確認済み Intent を bundle 経由で読めるが、行自体はコピーしない。
その新規セッションが次の revision を昇格すると、当該セッションで再確認されていない継承 Intent は
新 bundle に入らない。そのため、System 共有であるべき Vision / goal 等が次の canonical head で
静かに消える可能性がある。既存テストは「Intent を確認した同じセッションから最初に昇格する」
ケースだけで、世代をまたぐ保持を検証していない。

**必要な修正**

- 新 revision の premise は、親 canonical premise の確認済み Intent を基底にする。
- 現セッションの明示的な訂正・supersede・not-applicable を差分として適用する。
- System 単位の Intent revision/head を独立させる場合も、セッションの最新行を直接正準にしない。

**不足テスト**

1. S0 で goal を確認して R1 を昇格する。
2. R1 を premise に S1 を開始し、goal を再入力せず R2 を昇格する。
3. R2 を premise に S2 を開始し、同じ goal/Vision が残ることを確認する。

### [P2] 新規 Interview の作成と premise 捕捉が同一 transaction ではない

`create_interview_session` は session INSERT、process run 作成、premise 捕捉を連続実行するが、
明示的な transaction を開始していない。

- 作成処理: `apps/control-server/app/routes/interview.py:764-821`
- SQLite 接続は `isolation_level=None`（autocommit）:
  `apps/control-server/app/db.py:51-55`
- `get_conn` 自体も BEGIN/COMMIT/ROLLBACK を行わない: 同 `:64-82`

したがってコメントにある「SAME transaction」は成立せず、途中で premise 捕捉が失敗すると、
premise のない session や開始済み process run が残る。プロセス内 lock は同時実行を直列化するが、
複数文の原子性や例外時 rollback は提供しない。

**必要な修正**

- snapshot 検証後に `BEGIN IMMEDIATE` を開始し、session、initial run、premise の作成をまとめる。
- 全成功時のみ COMMIT、例外時は ROLLBACK する。
- fault injection で `capture_session_premise` を失敗させ、session/run が残らないことを確認する。

### [P2] head 未作成時の Overview が最新セッションを Understanding として投影する

Overview は canonical head がなければ、最新 Interview session を選び、その内容から Brief 等を
構築する。

- fallback 選択: `apps/control-server/app/overview_projection.py:1629-1686`
- fallback を前提にした既存テスト:
  `apps/control-server/tests/test_canonical_understanding.py:758-781`

UI上は `latest_session` と明示するため canonical と偽る問題は緩和されている。しかし Issue の
受入条件「Overview は System の canonical head のみを現在の Understanding として表示し、
candidate/進行中状態は別セクションに表示する」とは一致しない。head がない場合も、Overview の
中心 Brief がセッション作成順に依存し続けるため、複数セッション間で表示内容が切り替わる構造が残る。

**必要な修正**

- head がない場合、canonical Understanding は明示的に「未確定」とする。
- 最新セッションの内容を見せる場合は、canonical Brief とは別の「進行中 candidate」領域に置く。
- Overview の canonical 系フィールド・Purpose Chain・findings が latest session に依存しないことを
  統合テストで確認する。

## 推奨する修正順

1. 昇格時の base premise 上書きを廃止し、セッション境界を正す。
2. 推論結果の保存直前 revalidation を共通化する。
3. 確認済み Intent の世代継承規則を定義・実装する。
4. 新規セッション作成を transaction 化する。
5. Overview の未昇格表示を canonical と candidate に分離する。

最初の3点はデータの意味と履歴を壊すため、UI上の改善より先に対応する必要がある。
