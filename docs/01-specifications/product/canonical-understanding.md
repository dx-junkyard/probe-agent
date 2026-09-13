# System 正準 Understanding と Interview premise (Issue #464)

canonical design contract. 実装はこの文書に従う。

Overview は Vision を把握しているのに、同じ System の Interview は未把握として
扱う ── この Epic はその報告から始まっている。原因は表示の不具合ではなく、
**存在しなかった所有境界**である。

Vision / System Purpose / 確定済み Capability / 人が確認した意図は **System の
事実**だが、#464 以前はどれも「それを生み出した Interview セッション」の中に
しか保存されていなかった。そのうえで各画面が「現在の Understanding」を各自で
解釈していた ── Overview は `interview_session` の最新行から、Interview は自分の
`current_understanding` 列から。**作成順は確定順でも昇格順でもない**ので、この
2つの解釈が一致し続けることは原理的にありえない。どちらの規則も単体では間違って
いない。規則が2つあることが欠陥だった。

```text
System
  └─ canonical_understanding_revision_id (1つだけ)  ← 人の昇格でしか動かない
        │
        │ 開始時に不変の premise として固定
        ↓
  InterviewSession (base_understanding_revision_id + base_premise_digest)
        │
        │ 会話 → rebuild → candidate revision (正準を一切動かさない)
        ↓
  UnderstandingRevision(status = candidate)
        │
        │ 人の確認 + compare-and-swap
        ↓
  UnderstandingRevision(status = canonical)  ← 新しい head
```

---

## 0. 不変条件

後からこの領域を変更するとき、次は必ず維持すること。

1. **正準 head は昇格でしか動かない。** `system_understanding_head` は
   `canonical_understanding.promote_revision` からしか更新されず、その関数は
   明示的な人間の確認 (`decision_method: manual`) からしか到達できない。
   「最後に更新されたもの」を正準とみなす last-write-wins を導入しない
   (#464 非目標 3)。
2. **premise 判定は導出であり、保存しない。** `current` / `stale` / `missing` /
   `invalid` は読み取りのたびに固定 digest から再計算する (#337 / #338 / #349 と
   同じ規律 — 保存された lifecycle 値は記述対象の行から drift しうるが、導出した
   値は drift しえない)。**保存するのは開発者自身の明示的な決定 (`rebased` /
   `branched`) だけ**で、それは観測ではなく判断だからである。
3. **`invalid` を `current` へ丸めない。** 前提を記録していないセッションは
   「正しい前提の上にある」ことの証明ではない。ゲートの失敗方向は常に閉じる側で
   なければならない。同様に `missing` (前提が消えた) と `stale` (前提が動いた) は
   開発者の次の操作が違うので同じ答えにしない。
4. **premise は不変。** セッション開始時に bundle を丸ごとコピーし、会話中に
   暗黙更新しない。読み取り時に現在の head から作り直す実装にすると、それは
   もはや前提ではない。
5. **premise は正準 claim と「確認済み」Intent だけを運ぶ。** セッションごとの
   仮説・質問順・未確認 evidence を System 共通状態にしない (#464 非目標 1)。
   未確定の Intent 提案はセッションの仮説であって System の事実ではない。
6. **昇格は拒否するのであってマージしない。** 古い premise から作られた候補、
   および開発者が見ていた head と現在の head が違う昇格は 409 で拒否し、再評価を
   求める。拒否は監査行として残す ── 拒否された昇格は後から状態から再導出でき
   ないからである。
7. **Overview は正準 head だけを「現在の Understanding」として表示する。**
   昇格前の System では `understanding_source='latest_session'` と明示し、
   正準であるかのように見せない (#366 の「一つの表示語が二つの事実を運ばない」)。
8. **既存データは推測で移行しない。** 初期 canonical revision は **人間の確認が
   実在する場合にのみ**生成する。誰も確認していない System に head を作るのは、
   この Issue が削除しようとしている「最新行が勝つ」規則そのもの。baseline を
   一意に復元できないセッションは `legacy-unbased` (= `invalid`) のままにし、
   暗黙に続行させない。
9. **既存の人間ゲートを緩めない。** 理解の確認 / Alignment 項目の確定 / 提案の
   承認・編集・却下 / 差分の適用 / 観測の開始 / 採否の記録 / publish はそのまま。
   本 Epic が追加する昇格・rebase・branch・premise 採用もすべて `manual`。

---

## 1. モジュール

| file | 役割 |
| --- | --- |
| `apps/control-server/app/canonical_understanding.py` | 正本。digest / premise 捕捉 / 有限判定 / CAS 昇格 / rebase・branch / 導出メトリクス。 |
| `apps/control-server/app/routes/canonical_understanding.py` | API 境界。 |
| `apps/control-server/app/db.py` | `system_understanding_head` / `understanding_canonical_event` と追加列・backfill migration。 |
| `apps/control-server/app/models.py` | `Literal` 語彙 + `*Out` モデル。 |
| `apps/dashboard/src/components/system-understanding/premise-notice.tsx` | 前提の表示と明示選択、昇格 CTA。 |
| `apps/control-server/tests/test_canonical_understanding.py` | contract test (Issue の 6 シナリオを含む)。 |

---

## 2. データモデル

### 2.1 `system_understanding_head`

System ごとに最大 1 行。`head_version` が compare-and-swap トークン。
`revision_id` の外部キーは **ON DELETE を持たない (NO ACTION)**: retention が
正準 head を消せてしまうと、System は自分が何を理解しているか言えなくなる。

### 2.2 `understanding_revision` の追加列

| 列 | 意味 |
| --- | --- |
| `status` | `candidate` / `canonical` / `superseded`。既定は `candidate`。 |
| `parent_revision_id` | 何を基準に更新したか。セッション内の直前リビジョン、無ければ開始時の premise。昇格時は当時の head。 |
| `content_digest` | Understanding 内容の同一性。`understanding_brief.claim_payload` を再利用 (定義を二重化しない)。 |
| `premise_digest` / `premise_json` | **昇格時にだけ**書かれる、その版が表す前提。以後不変。 |
| `confirmed_by` / `confirmed_at` | 昇格した人と時刻。 |

`premise_digest` を昇格時にのみ確定するのは、後から Intent を 1 つ確認しただけで
正準 premise が静かに動かないようにするため。その変更は次に昇格される版に入る。

### 2.3 `interview_session` の追加列

`base_understanding_revision_id` / `base_premise_digest` / `base_premise_json` /
`premise_tracking_version` / `premise_captured_at` / `premise_disposition` /
`premise_origin_kind` / `premise_origin_session_id` /
`result_understanding_revision_id`。

`status` (#349 の中断/再開軸) とは **別軸**である。中断されたセッションと前提が
動いたセッションは別の事実で、開発者の次の操作も違う。

### 2.4 `understanding_canonical_event`

append-only。`head_initialized` / `promoted` / `promotion_conflict` /
`session_rebased` / `session_branched`。

**premise 不一致はここに書かない**: 読み取り時に導出されるので、記録すると画面を
見ただけで行が増える。ここに残すのは「誰かが実際に行ったこと」だけである。

---

## 3. premise 判定 (first match)

| # | 条件 | state | reason_code |
| --- | --- | --- | --- |
| 1 | `premise_tracking_version` が無い | `invalid` | `premise_not_captured` |
| 2 | `premise_tracking_version` が未知 | `invalid` | `premise_version_unsupported` |
| 3 | bundle は revision を指すが FK 列が NULL / 行が消えた | `missing` | `base_revision_missing` |
| 4 | head も base も無い | `current` | `no_canonical_head` |
| 5 | 片方だけ存在する | `stale` | `head_moved` |
| 6 | `base != head` | `stale` | `head_moved` |
| 7 | `base == head` かつ digest 不一致 | `stale` | `premise_content_changed` |
| 8 | それ以外 | `current` | `premise_matches_head` |

`continuable` は `state == 'current'`、**または** `disposition == 'branched'`。
branch は「この会話は古い前提の検討である」と開発者が記録した事実なので続行でき
るが、判定は `stale` のままで、その候補は昇格できない (昇格は `current` 必須)。

---

## 4. ゲートの位置

premise を **消費する** 2 経路にだけ 409 を置く:

- `POST /interview/sessions/{id}/dialogue-turn`
- `POST /interview/sessions/{id}/update-understanding`

開発者の入力を**記録する**経路 (Q&A 回答など) には置かない。前提の問題で人間の
回答を失うのは、#336 が 「わからない」 の入口で先に回答を確定させたのと同じ理由で
誤りである。

---

## 5. 昇格 (compare-and-swap)

1 トランザクションで: revision の status / lineage / premise、直前 head の
`superseded` 化、head ポインタと `head_version`、昇格したセッションの新しい
baseline、監査行。

3 つの独立したガード (それぞれ次の操作が違うので別コード):

- `premise_not_current` — 古い head から作られた候補。受入条件 9 はこれで成立する。
- `head_revision_mismatch` / `head_version_mismatch` — 開発者が見ていた head と
  現在の head が違う。その確認は別の内容についてのものだった。
- `revision_not_found` / `revision_not_candidate` / `revision_empty` — 候補として
  成立していない。

昇格したセッションの baseline はその版へ進む (自分が作った内容が前提になった)。
**他の open session は古い baseline のまま `stale` になる** ── それがこの機構の
目的である。

---

## 6. 移行

1. System ごとに、**人間の確認が実在する**リビジョンを初期 canonical head にする。
   優先順は (a) #312 の `understanding_capability_confirmation` が指す最新版、
   (b) `understanding_confirmed_at` を持つセッションの最新版。どちらも無ければ
   **head を作らない**。
2. 初期 head を生み出したセッションだけ baseline を付与する (推測ではなく構成上
   そうだから)。他の既存セッションは `legacy-unbased` のまま。
3. `legacy-unbased` の修復は `POST .../premise/adopt-current` という開発者の明示
   操作のみ。読み取り時に暗黙適用しない。
4. 移行前後で `understanding_revision` の件数は変わらず、昇格された版の
   `content_digest` は `compute_content_digest` で再現できる (移行手順 8)。

---

## 7. API

| method | path | 役割 |
| --- | --- | --- |
| GET | `/understanding-head` | System の正準 Understanding。`revision_id: null` は「まだ誰も昇格していない」。 |
| GET | `/interview/sessions/{id}/premise` | 前提判定 + 有限の選択肢。読み取りは何も書かない。 |
| POST | `/interview/sessions/{id}/premise/rebase` | 現在の head 上に**新しいセッション**を作る。元の会話と前提は不変。 |
| POST | `/interview/sessions/{id}/premise/branch` | 古い前提の検討として明示的に維持する。 |
| POST | `/interview/sessions/{id}/premise/adopt-current` | legacy セッションを明示的に現在の前提へ結び直す。 |
| POST | `/interview/sessions/{id}/promote-understanding` | 候補を正準 head へ昇格 (CAS)。 |
| GET | `/understanding-canonical-events` | 監査。 |
| GET | `/understanding-canonical-metrics` | premise 不一致 / stale / 昇格競合の観測。 |

---

## 8. 会話コンテキスト

`system_understanding_reviewer` (`understanding-review-v8`) と `interview_agent`
(`interview-v7`) はどちらも、セッションが固定した premise bundle を **独立した
最初のセクション**として受け取る。graph は code から導出した仮説、premise は人が
既に確定した内容であり、混ぜると rebuild がどちらか言えなくなる。premise 用の
prompt 予算を別に持つのは、**切り詰められた premise は確定済みの Vision が静かに
消える経路**だからである。
