# Interview Discussion Contributions — データ・API・反映設計

**文書の役割:** canonical-contract（関心起点の会話を補足記録と既存更新経路へ接続する契約）
**状態:** current（目標契約。基礎実装を追加済み、全受入条件の達成は未確認）
**設計日・調査対象:** 2026-09-16、`4b815a3` のコード
**上位UX・受入条件:** [関心起点の対話](../ux/interest-led-interview-discussion.md)
**依存契約:** [Assistant Discussion](assistant-discussion.md)、[Adapter](ai-discussion-adapter.md)、[正準Understanding](../product/canonical-understanding.md)

実装と未完了範囲は [検証記録](../../03-validation/interview-discussion-implementation.md) を参照。
以下の契約は目標を含み、APIの存在だけで全体の完了を意味しない。

## 0. 所有境界と設計判断

IID-D-01: 会話の起点は既存 `interview_session` threadで固定する。identityは引き続き
`screen_id|scope|target_kind|target_ref`。関連するInterviewを増やしてもidentityは変更しない。
本機能の関連探索は同一System内のInterviewに限り、他threadの履歴を自動取得しない。
同じ起点からの再開は既存threadを使う。独立した複数会話を同じ起点に作る拡張は対象外。

IID-D-02: 新規に所有するのは「整理候補」「関連提案」「人が確認した補足」「反映receipt」。
補足は人間入力の来歴であり、Understandingの第二の正本ではない。質問、回答、Intent、
Understanding revision、正準headの所有者は既存モジュールのまま。

IID-D-03: 補足を保存することと既存項目を更新することを分ける。会話や候補生成で既存行を
変更しない。保存された補足は次回の明示的な理解更新の入力になり、保存自体は自動refreshを
起動しない。生成候補はhuman-confirmedな正準事実として扱わない。

IID-D-04: 既存Discussion Proposalの単一targetを複数targetへ変更しない。補足には独自の
候補集合を持たせ、既存domainへの反映時に対象別の操作へ変換する。既存の
`understanding_claim` applyはIntentのgoal候補を作る経路なので、PurposeやCapabilityの
要約訂正をそこへ流用しない。既存Change Setのclaim summary更新を再利用する。

IID-D-05: 保存・承認・反映・正準昇格は独立する。LLMは分類と文案・関連候補を提案し、
適用可否、対象解決、権限、競合判定はserverが決める。単一targetの既存Proposal仕様は不変。

## 1. 読み取りと会話

起点Interviewの固定premise、選択candidate revision、Intent、Q&A、保存済み補足を
provenance別セクションでcontextに載せる。System正準headは比較対象として別に載せる。
「保存済み補足」はユーザーの申告であり、正準・コードからの観測・AI仮説と区別する。

IID-D-06: 既存Adapter §9のbounded context・coverage・cursor・durable auditを拡張し、
sessionを指定できるresolverを実装する。最新セッションを暗黙選択するclaim resolverを
横断反映のaddressとして使わない。serverが発行した候補target IDだけをLLMに渡す。

関連探索は起点と保存済み関連から開始する。必要なら同一SystemのInterview一覧を50件ずつ
cursor取得し、タイトル・focus・版付きの短い抜粋から候補を絞る。1回の意味的評価は20候補、
本文取得は5セッションまで。既存context budgetを超える場合はcoverageをpartialにし、
未読件数と「関連をさらに探す」を返す。上限内で全件見たと偽らない。意味的選定はreasoning
modelで行い、未設定ならunsupportedではなく推論利用不可として返す。

LLMへ送る各turnにはrole、turn ID、参照manifestを付す。抽出は保存済みturnだけを入力とし、
未保存フォームを参照した一時的な応答・その値を抽出結果経由で永続化しない。既存redactionを
発言、候補文、引用、手入力、ログにも適用する。引用は同threadの存在するturnに限る。

IID-D-07: 会話回答後に非同期で整理候補を更新する。回答遅延に直結させず、失敗しても会話は
成功として保持する。抽出範囲は最大24 turn、候補20件、本文4,000字/件、根拠turn12件/候補。
長い会話は連続した範囲ごとのjobで処理し、未処理範囲を返す。過去の要約は探索用であり、
新候補の根拠は原turnを再取得する。ユーザーの「ここまでを整理」は同じjob経路を用いる。

## 2. データモデル

すべてsystem_id、作成者・日時、schema_versionを持つ。IDはserver発行。FKで来歴を保護し、
polymorphicなtargetは有限resolverで同一System・session所属を検査する。

| 新規table | 主な列・制約 |
| --- | --- |
| `interview_discussion_extraction` | thread_id、job_kind（extract/target_search）、from/to_turn_number、candidate_id/revision（探索時）、cursor、input_digest、prompt_version、state、result_json、intelligence_run_id、error_code。UNIQUE(thread_id, job_kind, input_digest, prompt_version)。stateはqueued/running/succeeded/failed/cancelled。lease・attemptを持ちクラッシュ後再開可能 |
| `interview_discussion_candidate` | extraction_id（manual時NULL）、thread_id、candidate_key、revision、kind、text、supersedes_candidate_id、author_kind、decision。UNIQUE(thread_id, candidate_key, revision)。kindはsupplement/correction/open_question/hypothesis。decisionはpending/held/rejected/saved。編集は新revisionを追加し旧版不変 |
| `interview_discussion_candidate_source` | candidate_id、turn_id、role、発言内の引用位置・引用digest。ユーザーのレビュー追記には別のmanual source行。単なるAI生成文をuser sourceに偽装しない |
| `interview_discussion_target` | candidate_id、session_id、target_kind、target_ref_json、captured_revision_id/digest、reason、origin、selection。originはreasoning_llm/manual、selectionはsuggested/selected/rejected。変更は候補revisionを更新 |
| `interview_discussion_contribution` | thread_id、candidate_id/revision、kind、確認済みtext、reviewed_by/at、source_manifest、supersedes_contribution_id。内容不変、修正は追記。UNIQUE(candidate_id, revision) |
| `interview_discussion_contribution_link` | contribution_id、session_id、target_kind/ref、captured_revision/digest。1補足に複数リンク。UNIQUE(contribution_id, session_id, target_kind, normalized_ref)。保存時の選択先だけを追加 |
| `interview_discussion_operation` | system_id、actor_id、request_id、operation_kind、payload_digest、result_json、created_at。UNIQUE(system_id, actor_id, request_id)。保存・反映の冪等receipt |
| `interview_discussion_application` | contribution_link_id、operation_id、domain_kind/ref、result_revision_id、applied_by/at。UNIQUE(contribution_link_id)。複数回反映したい訂正は新補足としてレビュー |
| `interview_discussion_preview` | token_hash、system_id、actor_id、session_id、manifest_json、effects_json、payload_digest、expires_at。token_hashはUNIQUE。期限切れは削除可能、反映receiptには採用manifestを保持 |
| `interview_discussion_outbox` | operation_id、session_id、event_kind、payload_json、delivery_state、attempt、lease、next_attempt_at。UNIQUE(operation_id, session_id, event_kind)。refreshのcommit後配送を再試行 |

IID-D-08: 抽出結果はappend-only。AIの再抽出で人の編集・保留・却下を上書きしない。
同一input digestの再実行は同じ結果を返す。異なる抽出の意味的重複は候補として提示し、
類似度だけで削除・統合しない。人がsavedにした候補は再編集せず補足の訂正版を作る。

候補編集はcandidate_keyの最新revisionをexpected_revisionでCASする。同じ版から二人が
編集した場合は片方を409にする。pending/held/rejectedの変更は新revisionとして残し、
savedへの遷移だけは補足保存transactionが担当する。saved候補からの訂正版は新candidate_keyを
発行し、supersedes_candidate_idを必須にする。元補足は削除せず訂正版への参照を表示する。
関連先の一部だけを保存する場合、残りをheldの別candidate_keyへ明示分割してから保存する。
UIはこの分割を「残りを保留」として提供し、保存後の候補に未保存targetを隠して残さない。
revisions APIには任意の `split_held_targets` を許可し、この分割を同一transactionで行う。

住所の有限集合は `session`、`qa`、`intent_item`、`claim`。session refはsession ID、qa/intent
refはそのsession所属の行ID。claim refは `{session_id, revision_id, section, name}` と
捕捉digest。nameが同版内で重複する場合はambiguousで、配列位置による代用はしない。
claimの改名・revision変更はstaleとし、再選択・再レビューで新refを得る。

freshnessは保存列にしない。`current/stale/unresolvable`を対象ごとに読み取りで導出する。
resolutionの`resolved/ambiguous/unsupported`、取得の`available/unavailable`、人間判断の
decisionを独立させる。supplementの保存済みとdomainへ反映済みは別の事実である。

## 3. API契約（新規）

全APIは認証とSystem scopeを適用し、他System・権限外IDは404。書込みは既存Interview更新
権限に従う。GETは読み取りのみ。mutationにはrequest_id、編集にはexpected_revisionを要求する。
未知キー・enum・上限超過は422。本文や引用をURLに入れない。

| 操作 | method / path | 入出力・効果 |
| --- | --- | --- |
| 整理 | POST `/assistant/discussion-threads/{id}/interview-extractions` | `{request_id, through_turn_number}`。202 job。起点がInterview以外なら422 unsupported。再送は同job |
| 進捗 | GET `/assistant/interview-extractions/{id}` | state、covered_turn_range、未処理範囲、candidate_ids、error_code |
| 候補一覧 | GET `/assistant/discussion-threads/{id}/interview-candidates?cursor=` | revision・根拠・関連候補・操作可否。最大50件、cursor pagination |
| 編集・判断 | POST `/assistant/interview-candidates/{id}/revisions` | `{request_id, expected_revision, text, kind, decision, selected_targets}`。decisionはpending/held/rejectedのみ。新revision |
| 関連探索 | POST `/assistant/interview-candidates/{id}/target-search` | `{request_id, expected_revision, cursor?}`。202 job。同じ進捗APIで取得。候補の選択状態は変更しない |
| 補足保存 | POST `/assistant/discussion-threads/{id}/interview-contributions` | `{request_id, items:[{candidate_id, expected_revision}]}` 最大20件。各候補の選択済みtargetを再検査し全件atomic保存。receipt・補足ID・リンクを返す |
| 手入力 | POST `/interview/sessions/{id}/discussion-contribution-drafts` | `{request_id, kind, text}`。LLM不要のmanual候補を作り、同じレビュー・保存経路へ進む。起点threadをresolve-or-create |
| 補足一覧 | GET `/interview/sessions/{id}/discussion-contributions?cursor=` | 最大50件。来歴・起点URL・反映receipt・freshness・未解決を返す |
| 反映プレビュー | POST `/interview/sessions/{id}/discussion-applications/preview` | `{request_id, contribution_link_ids}`。現在値・提案値・effect・期限10分のpreview_token。domain変更なし |
| 反映 | POST `/interview/sessions/{id}/discussion-applications` | `{request_id, preview_token, selected_link_ids}`。選択した操作のみall-or-nothing。結果参照・revision・receipt |
| 結果復元 | GET `/assistant/interview-discussion-operations/{request_id}` | 同一actorの結果。未記録は404で未完了を意味し、失敗確定とはみなさない |

preview_tokenはserver側にmanifest・選択可能なリンク・after値・payload digest・期限を保存し、
予測不能な参照tokenだけを返す。反映時の再生成はしない。文言修正は補足の新revisionを保存して
previewし直す。全レスポンスにserver導出のallowed_actionsと理由を返す。

IID-API-01: 409コードは `candidate_revision_mismatch`、`target_stale`、`target_ambiguous`、
`target_unresolvable`、`session_closed`、`preview_expired`、`application_conflict`、
`idempotency_payload_mismatch`。推論不可は503 `reasoning_unavailable`。
premise起因の拒否は既存canonical-understandingのコードを維持する。
失敗時は問題のcandidate/link IDと回復操作を返し、別Systemの存在を漏らさない。

IID-API-02: Interview起点の `POST /assistant/ask` に任意のclient_turn_idを追加する。
新UIでは必須とし、UNIQUE(thread_id, client_turn_id)でuser turnを推論前に保存する。
同じID・同じ本文の再送は同turnを再利用し、異なる本文は409。既存clientの省略時の挙動は維持。
推論失敗・参照更新時にもuser turnは残し、失敗をassistantの発言として根拠にしない。
assistant応答はreply_to_turn_idに一度だけ確定する。応答の配送先はturn開始時のthread IDである。
thread単位で回答を直列化し、先行応答待ちは409 `turn_in_progress`と再試行先を返す。
既存turn tableへの追加列はnullableとし、legacy行を推測で関連付けない。

## 4. 保存・反映と既存サービス

IID-D-09: 補足保存はBEGIN IMMEDIATE内で候補revision、全targetの存在・所属・選択・digest、
sessionの開閉を再検証し、補足・リンク・decision・receiptをまとめてcommitする。
補足の記録だけならpremiseがstaleでも可能だが、その事実を表示する。候補が参照したtarget
自体が変わった場合は409で再レビューを求める。部分保存はしない。

保存済みsession補足は既存understanding rebuildの入力collectorに追加する。補足ごとのID、
kind、発言元、human review、未解決フラグを渡す。明示更新後のcandidateには使用した補足IDを
lineageとして持たせる。再読込・一覧取得・単なる会話でrebuildしない。

| 反映先 | 許可効果 | 既存owner / 制約 |
| --- | --- | --- |
| session | 補足記録と次回の明示理解更新への入力 | 新collector + 既存update-understanding。直接JSON patchなし |
| qa | 明示回答または訂正 | 既存Q&A answer/correctのdomain処理を再利用。open_question/hypothesisは回答に変換不可 |
| intent_item | 値の訂正と明示確認 | 既存Intent correct。実際にconfirmedになる操作であることをpreviewに明記 |
| claim | 既存summaryの訂正、candidate revision追加 | Change Setの許可sectionとsummaryだけ。Vision、新規claim、name/why_core変更はv1対象外でsession補足に保存 |

IID-D-10: 反映は1 session単位。複数sessionの補足保存は可能だが、domain更新は各sessionで
個別確認する。Q&A回答とIntent確認とclaim訂正の異なる効果もpreviewで別グループにし、
1回の反映要求は同じeffectのグループに限定する。claim複数件は1 candidate revisionを作る。
2件が同じfieldに異なる値を提案するとapplication_conflict。黙って順番適用しない。

previewのafter値はレビュー済み補足textをそのまま用いる。既存summaryへ追記するか置き換えるかは
候補レビューで決め、補足textに更新後の全文を保存する。previewで新たなLLM要約や連結はしない。
全文として不適切ならレビューへ戻す。回答・Intent値も同じ規則で、AIによる隠れた言い換えを防ぐ。
hypothesisのJU昇格は本APIのapplicationに含めず、既存Discussion Proposalへ明示的に候補化し、
競合説明・反証条件・調査案をレビューして既存昇格APIを使う。補足IDを来歴として保持する。

既存Change Set applyは項目単位の部分成功、Intent内部処理は独自transactionを持つ。
そのHTTP handlerを新atomic処理から呼ばない。まずconnを受け取るdomainサービスへ抽出し、
既存endpointの部分成功仕様を保つwrapperと、新規all-or-nothing wrapperに分ける。
domain書込みとapplication・receiptを同transactionに置き、更新済み・receipt未記録を防ぐ。

再検証対象はsession内容revision、target digest、Intent/Q&Aの現在行、preview内容、実行済みlink。
premiseを消費する推論は既存の事前tokenとcommit直前再検証を使う。stale premiseの人間入力は
保持し、refreshは既存規則でスキップする。自動refreshが必要な既存回答/訂正はcommit後の
永続outboxから既存refresh jobへ渡し、enqueue失敗を保存失敗と混同しない。

IID-D-11: operationのrequest_idとpayload_digestはtransaction内で先に確認する。同一payload
再送は元のreceiptを返し、異なるpayloadは409。別request_idでも同じlinkの再反映は元結果を返す。
複数workerの二重実行はUNIQUEとtransactionで排除する。外部LLM呼出し中はDB接続を保持しない。
failed jobはattempt履歴を保持し再試行し、lease切れrunningを回収する。generation inputが
変わった場合は旧結果を公開せず、再抽出が必要と返す。

## 5. 履歴・権限・復旧

IID-D-12: source turn、起点thread、補足、反映されたrevisionは参照中のretention削除を禁止する。
threadのarchiveは参照を壊さない。System削除は既存削除ポリシーに新tableを追加する。
backup/restoreで候補・補足・receipt・参照manifestを同じSystemとして扱い、FK整合性を検証する。
ユーザーの会話編集は過去turnの書換えとして扱わず訂正turnで追記する。

closed sessionへの参照候補は保持できるが保存・適用は再開が必要。関連先を変えても旧候補の
来歴は保持する。rebaseで新sessionになった場合もリンクの自動付替えは行わず、新refで人が
再レビューする。複数sessionの矛盾はopen_questionとして残し、正準昇格は既存CASに委ねる。

## 6. 実装分割・移行・検証

新規tableは空で追加する。過去の発言からの抽出はユーザーの「ここまでを整理」で開始し、
既存会話を一括で確定情報へ移行しない。新APIを提供しないserverではUI入口を非表示にし、
既存Bot会話は継続する。serverの機能フラグを配信し、client版だけで有効化しない。

| 順序 | 実装単位（予定） | 完了条件 |
| --- | --- | --- |
| 1 | `interview_discussion.py`、db/models/schema、routes、単一session resolver | ID・有限語彙parity、migration、権限、来歴、冪等性。既存thread identity不変 |
| 2 | extraction worker、context collector、manual draft | 引用検証、質問/仮定の区別、境界付き取得、job復旧。失敗でも原発言保持 |
| 3 | Interview入口、Assistantの起点固定、候補レビュー、補足一覧 | 同一Interviewの自由対話→保存→再開。IID-AC-01/02/03/07/08/09 |
| 4 | domainサービス抽出、preview/application、outbox、既存画面再取得 | transaction rollback、競合、正準head不変。IID-AC-05/06 |
| 5 | 関連探索・複数sessionリンク・版比較 | IID-AC-04/06。段階1〜4の単一session版を退行させない |
| 6 | 利用者評価・運用検証 | IID-AC-10、backup/restore、失敗復旧。実測証拠を03-validationに記録 |

IID-T-01: fixtureで同名claim、旧版、削除、別System、closed sessionを用意し、間違った保存0件を確認。
IID-T-02: 同時編集・二重click・commit直後切断・worker crash・outbox再送を注入し、結果が1件で復元可能。
IID-T-03: 実LLM評価セットに質問、仮定、相づち、皮肉、自己訂正、AIだけの推測、複数関連先を含める。
自動テストは偽sourceや不正targetを決定的に拒否し、意味的な抽出品質は実LLMで別途測定する。
IID-T-04: 既存Discussion Proposal、Change Setの部分成功、Intent confirm/correct、Q&A、canonical
promotionの回帰テストを実施し、新経路のatomic仕様を既存APIへ無断で広げない。
IID-T-05: APIとbrowserで全受入条件を検証し、未実測の理解度・音声・支援技術は達成済みにしない。

本設計の完了範囲は仕様と実装境界の決定まで。実装、データ移行の実行、外部Issue作成、
本番への公開は本書の追加では発生しない。
