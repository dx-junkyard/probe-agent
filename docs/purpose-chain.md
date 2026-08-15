# Purpose Chain (Issue #387 / #388-#391)

canonical design contract. 実装はこの文書に従う。

probe-agent は Vision と System Purpose を「並んだ2つの文章」として表示できるが、
**Vision から System Purpose を説明・検証できる状態ではない**。この Epic は両者を
追跡可能な因果連鎖 (Purpose Chain) として扱う。同時に、利用者へ初回に求める入力は
**最小3要素 (Purpose Frame)** だけに保つ。

```text
対象者と現在の課題 (beneficiary_problem)
        ↓ problem_to_change
望ましい変化 (desired_change)
        ↓ change_to_intervention
システムの介入 (intervention)
        ↓ intervention_to_capability
Capabilities
        ↓ 必要時のみ (#391)
UX 成功体験 → 成果証拠 → 再利用契機
```

---

## 0. 全 sub-issue に共通する不変条件

1. **新しい理解モデルを作らない。** Purpose Frame の要素は既存行の projection で
   ある。`interview_intent_item` (#284) と `understanding_brief.build_understanding_brief`
   (#351-#354) が正本であり、Purpose Chain はそこへ **relation と lineage を足すだけ**。
   確認状態・出所の語彙は `UnderstandingConfirmationState` / `UnderstandingProvenanceKind`
   を **そのまま再利用** する (#380 が `OverviewFindingProvenance` を superset にした
   のと同じ理由 — 変換を挟むと開発者が確定した Vision が AI の推測として表示される)。
2. **判定は server canonical projection。** client は Purpose 要約・relation 状態・
   解像度・次の質問・recheck 理由を再導出しない (#387 UX原則7)。
3. **有限集合のみ。** すべての語彙は `app/models.py` の `Literal` で一度だけ定義し、
   Python 側は `get_args` で導出、Dashboard 側は `test_interview_type_parity.py` の
   `FINITE_TYPE_NAMES` で union を拘束する。文章類似度・embedding・keyword score で
   要素や relation を接続しない (Principle 6)。同名一致は `understanding_diff` と
   同じ **完全一致** のみ。
4. **人間ゲートは一切緩めない。** AI 候補は `decision_method: reasoning_llm` のまま。
   確認・訂正・却下・保留はすべて `manual`。page view を確認・承認として保存しない。
5. **合成 score を作らない。** completion rate、confidence percentage、System 全体の
   平均解像度を返さない。解像度は要素ごとの有限段階のみ。
6. **部分失敗は他を巻き込まない。** section ごとの guarded loader (#380 の規律)。
   取得失敗を「未入力」「missing」へ丸めない。`unknown` (読めたが記録がない) と
   `unavailable` (読めなかった) は別の値であり、同じ文言で表示しない。

---

## 1. #388 — canonical Purpose Frame 契約と lineage

### 1.1 モジュール

| file | 役割 |
| --- | --- |
| `apps/control-server/app/purpose_chain.py` | 決定的な projection 本体。LLM を呼ばない。 |
| `apps/control-server/app/routes/purpose_chain.py` | `GET /purpose-chain` と relation 決定の write。 |
| `apps/control-server/app/models.py` | `Literal` 語彙 + `*Out` モデル。 |
| `apps/control-server/app/db.py` | `purpose_relation_decision` テーブル。 |
| `apps/control-server/tests/test_purpose_chain.py` | contract test。 |

### 1.2 要素 (element)

`PurposeElementKind = Literal["beneficiary_problem", "desired_change", "intervention", "core_capability"]`

出所は既存行のみ。**first match**:

| kind | source (first match) |
| --- | --- |
| `beneficiary_problem` | Intent Brief `pain` の最新非 superseded 行。無ければ `unknown`。 |
| `desired_change` | `BriefResult.vision` をそのまま使う (= confirmed Intent `goal` → reviewer `vision` claim → 未確定 Intent `goal` の first match は `_resolve_vision` が既に実装済み)。`None` なら `unknown` で、`vision_missing_information` を `missing_information` として返す。 |
| `intervention` | `BriefResult.system_purpose` の各 claim。frame slot は先頭 1 件、残りは `elements` に `additional` として残す。 |
| `core_capability` | `BriefResult.core_capabilities` の各 claim。 |

`pain` のテキストを対象者と課題へ **分解しない**。分解は自由記述の解釈であり
Principle 6 に反する。「対象者が書かれていない」ことを検出したい欲求は #389 の
`frame_missing` need では扱わず、開発者自身の訂正操作に委ねる。

要素が持つフィールド (すべて必須):

```
id                     stable。kind 単独 (単数要素) か kind + ":" + sha256(name)[:16]。
                       行 id からは決して導出しない (#380: 再構築で振り直される)。
kind                   PurposeElementKind
state                  PurposeElementState = present | unknown | unavailable
display_statement      Level 0 が表示する 1〜2 文。claim なら summary、空なら name。
                       Intent 由来なら value_text。server は truncate しない。
statement              全文 (claim の name + summary、Intent なら value_text)。
confirmation           UnderstandingConfirmationState (再利用)
confirmation_label     既存 CONFIRMATION_LABELS
provenance             UnderstandingProvenanceKind (再利用)
provenance_label       既存 PROVENANCE_LABELS
resolution_level       PurposeResolutionLevel = L0 | L1 | L2 | L3
source_kind            PurposeSourceKind = intent_item | understanding_claim | none
source_ids             list[str] 例: ["intent_item:12"], ["claim:core_capabilities:名前"]
intent_revision_id     Optional[int]  (interview_intent_item.id)
understanding_revision_id Optional[int]
snapshot_id            Optional[int]
evidence               list[dict] (claim の evidence をそのまま)
evidence_stale         bool  — provenance が implementation_fact の要素でのみ true に
                       なりうる。判定は `gather_facts(...).snapshot_stale` の再利用
                       (git を呼ばない)。
missing_information    list[str] — state=unknown のとき何が足りないか (固定文)。
is_mock                bool
```

`state`:
* `present` — source 行が読めて内容がある
* `unknown` — source は読めたが行が無い / 値が空
* `unavailable` — source の読み取り自体が失敗した (guarded loader が捕捉)

### 1.3 relation

`PurposeRelationKind = Literal["problem_to_change", "change_to_intervention", "intervention_to_capability"]`

```
id            f"{relation_kind}:{source_element_id}->{target_element_id}" (stable)
kind          PurposeRelationKind
source_id / target_id
status        PurposeRelationStatus = confirmed | hypothesis | conflicting | unknown | unavailable
status_label
recheck_state PurposeRecheckState = current | stale
stale_reason  PurposeStaleReason | None
              = source_changed | target_changed | both_changed | upstream_changed
              | snapshot_changed
provenance    UnderstandingProvenanceKind — 決定があれば developer_decision ではなく
              developer_intent (関係を人が確定した) / 無ければ両端点の弱い方
decision_id   Optional[int]  purpose_relation_decision.id
decided_at / decided_by      Optional
rationale     str  (人が書いた理由。無ければ "")
evidence      list[dict]  (target 要素の evidence を引き継ぐ。捏造しない)
```

**status は first match:**

1. どちらかの端点が `unavailable` → `unavailable`
2. どちらかの端点が `unknown` → `unknown`
   (`unknown` を第5の値として持つ理由: 「接続を説明できない」は #389 の
   `relation_unknown` need の入力であり、relation を単に返さないと「関係がない」
   と区別できない。)
3. どちらかの端点の `confirmation == "conflicting"`、または現在有効な
   `rejected` 決定がある → `conflicting`
4. 現在有効な `confirmed` 決定があり、その捕捉 digest が現在の両端点 digest と
   一致する → `confirmed`
5. それ以外 → `hypothesis`

**recheck_state:** 決定は存在するが捕捉 digest が現在と食い違う → `stale` +
`source_changed` / `target_changed` / `both_changed`。上流 relation が `stale`
なら下流も `stale` + `upstream_changed` (伝播は **下流方向のみ**)。要素が
`evidence_stale` なら `snapshot_changed`。

これで #388 の change propagation 表がそのまま満たされる:

| 変化 | 効果 |
| --- | --- |
| desired_change 変更 | `change_to_intervention` が `source_changed`、`intervention_to_capability` が `upstream_changed` |
| intervention 変更 | 自 relation が `target_changed`、下流が `upstream_changed` |
| Capability 変更 | `intervention_to_capability` のみ `target_changed`。intervention 自体は変えない |
| snapshot 変更 | `implementation_fact` 由来要素の `evidence_stale` のみ |
| Intent 変更 | intent 由来要素の digest が動くので、その relation が stale |
| runtime 変化 | user outcome を自動確定しない (#391 まで何もしない) |

`element_digest(element)` は `statement` + `confirmation` + `provenance` +
`source_ids` の canonical JSON の sha256。`claim_digest` と同じ発想で、意味の
変化だけを見る。

### 1.4 resolution level

要素ごと、first match。**件数ではなく「今の判断に使えるか」**:

* `L3` — その要素に紐づく outcome criterion (#391) が measure/baseline/target/
  observation window を持つ (#391 が実装されるまで到達不能。理由を docstring に
  明記する)
* `L2` — 要素が `confirmed` で、主要 relation も `confirmed`
* `L1` — 要素が `present` で、主要 relation が `unknown`/`unavailable` でない
* `L0` — それ以外 (statement はあるが接続を説明できない、または unknown)

`frame_resolution_level` は 3 slot の **min** (有限段階の最小値であって平均でも
percentage でもない。docstring にそう書く)。System 平均は返さない。

### 1.5 永続化

```sql
CREATE TABLE IF NOT EXISTS purpose_relation_decision (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    session_id          INTEGER NOT NULL,
    relation_id         TEXT NOT NULL,
    relation_kind       TEXT NOT NULL,
    decision            TEXT NOT NULL,        -- 'confirmed' | 'rejected'
    rationale           TEXT NOT NULL DEFAULT '',
    source_element_id   TEXT NOT NULL,
    target_element_id   TEXT NOT NULL,
    source_digest       TEXT NOT NULL,        -- 決定時に捕捉
    target_digest       TEXT NOT NULL,
    understanding_revision_id INTEGER,
    intent_revision_id  INTEGER,
    snapshot_id         INTEGER,
    decision_method     TEXT NOT NULL DEFAULT 'manual',
    decided_by          TEXT,                 -- Principal の識別子
    superseded_by_id    INTEGER,
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES purpose_relation_decision (id) ON DELETE SET NULL
);
```

追記のみ。訂正は新しい行 + 旧行の `superseded_by_id` (Intent Brief と同じ規律)。
**決定は上書きされず、digest が動いても削除されない** — `stale` として読まれる
だけで、監査上は「あの時点の両端点に対して人が確定した」事実が残る。

### 1.6 API

```
GET  /purpose-chain?session_id=<int|omitted>       -> PurposeChainOut
POST /purpose-chain/relations/{relation_id}/decision  -> PurposeRelationOut
     body: {session_id, decision: "confirmed"|"rejected", rationale?}
```

`session_id` 省略時は System の最新 session (Overview と同じ `ORDER BY id DESC`
規則)。別 System の session_id は「未選択」と同じ扱い (Brief と同じ)。
`relation_id` が現在の projection に存在しない → 404。端点が `unknown` /
`unavailable` の relation への決定 → 422 `purpose_relation_not_decidable`
(存在しない前提を人に確定させない)。

`PurposeChainOut`:

```
system_id, session_id, generated_at
frame: {beneficiary_problem, desired_change, intervention}   各 Optional[element]
elements: list[element]        (frame の3件 + 追加 intervention + capabilities)
relations: list[relation]
frame_resolution_level
frame_state: PurposeFrameState = complete | partial | empty | unavailable
snapshot_id, understanding_revision_id, understanding_confirmed_at
degraded_sections: list[PurposeChainSection]  = frame | relations | capabilities
degraded_detail: dict[str, str]
```

`GET /overview` に `purpose_chain: Optional[PurposeChainOut]` を **guarded** に
埋め込む (`OverviewSection` に `purpose_chain` を追加)。Overview は既存の
canonical projection を合成するだけという #380 の規律を維持する。

### 1.7 test (最低限)

* 4 element kind すべてが到達可能で、source が既存行だけであること
* confirmed Intent `goal` が reviewer の Vision より優先されること (Brief 再利用)
* AI 候補が確認なしに `confirmed` にならないこと
* `unknown` / `conflicting` / `unavailable` / missing が別々に区別されること
* relation status 5 値すべての到達、first-match 順序
* change propagation 表の各行 (下流方向のみ伝播すること、Capability 変更が
  intervention を変えないこと)
* legacy: 決定行が無い System が `confirmed` にならないこと
* relation 決定後に端点を変えると `stale` になり、決定行は残ること
* System 分離 (別 System の session_id / relation_id が漏れない)
* guarded loader: relations の導出が失敗しても frame は返ること
* `element_digest` が再読込で同一 (reload 再現性)

---

## 2. #389 — 判断必要性に基づく適応的インタビュー

### 2.1 モジュール

`apps/control-server/app/purpose_needs.py` + `routes/purpose_chain.py` へ追加、
`tests/test_purpose_needs.py`。

### 2.2 need code (有限)

```
frame_missing
relation_unknown
relation_conflict
capability_justification_missing
decision_criterion_missing
human_value_judgement_required
premise_recheck_required
```

**「optional field が空」は need ではない。** need は Purpose Chain の projection
から決定的に導出される: 要素が `unknown`、relation が `unknown` / `conflicting` /
`stale` 等。

need id は原因から導出する stable id: `f"{need_code}:{target_id}"`。

### 2.3 answerability

```
PurposeAnswerability = human_judgement | system_researchable | already_answered | unavailable
```

need code → answerability は **固定表**。自由記述の質問を分類する #286 の
reasoning router とは別物であり、置き換えない (system 生成 need は構造上どちらか
が既知)。

| need code | answerability |
| --- | --- |
| `frame_missing` (desired_change / beneficiary_problem) | `human_judgement` |
| `frame_missing` (intervention) | `system_researchable` — System Purpose は code/docs から調べられる |
| `relation_unknown` | `human_judgement` |
| `relation_conflict` | `human_judgement` |
| `capability_justification_missing` | `system_researchable` |
| `decision_criterion_missing` | `human_judgement` |
| `human_value_judgement_required` | `human_judgement` |
| `premise_recheck_required` | `human_judgement` |

`already_answered` は現在有効な `purpose_need_response` / relation 決定 /
confirmed Intent 行があるとき。`unavailable` は対象 section が degraded のとき。
`system_researchable` は **人へ聞かず**、Joint Understanding の調査へ送る
(#387 UX原則4)。

### 2.4 質問選択

`select_question(facts) -> Optional[PurposeQuestion]` — server canonical rule table、
**0 または 1 件**。優先順 (first match):

1. 現在の安全な判断を止める `relation_conflict`
2. relation を持たない要素自体の矛盾 `human_value_judgement_required`
3. 最小 Purpose Frame を成立させる human-only `frame_missing`
4. 現在の next action / 改善評価を止める `decision_criterion_missing`
5. stale になった確認済み前提 `premise_recheck_required`
6. 下流 Capability の重要 relation `relation_unknown`
7. `capability_justification_missing`
8. それ以外 → 質問なし

**全 need code が行を持つ。** 行を持たない code は明示的な `need_id` deep link
でしか到達できないが、システムが提示しない質問への link は誰も生成しないので、
「導出され、判断を止め、一度も聞かれない」need になる。これは導出しないのと
区別できない。2 行目が典型例で、2 件目以降の `intervention` 要素には relation が
作られないため、その矛盾は relation 経由では表に出ない。重複する場合は
first-match により上位の行だけが質問になる (同じ対象を二度聞かない)。

同順位は `(rule_row, need_code の定義順, need_id)` で決定的に解決する。LLM の
自由 score も client heuristic も使わない。`answerability != human_judgement` の
need は質問として返さず、`routed` として別に返す。

### 2.5 質問契約

```
need_id, need_code, rule_row
prompt          固定文 + 対象名 (LLM を呼ばない)
why_now         なぜ今必要か
blocked_decision どの判断が止まっているか
unlocks         回答すると何が可能になるか
defer_impact    保留した場合の影響
target_kind: element | relation ; target_id ; target_label
answerability
suggested_answer: Optional[{text, provenance, source_kind, source_ids, is_mock}]
                  — 既存行 (proposed Intent item / AI claim) からのみ。
                    根拠が無ければ候補を作らない。ここで LLM を呼ばない。
state: available | waiting | answered | deferred | unavailable
source_revision_ids
```

### 2.6 回答操作と永続化

```sql
CREATE TABLE IF NOT EXISTS purpose_need_response (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    session_id        INTEGER NOT NULL,
    need_id           TEXT NOT NULL,
    need_code         TEXT NOT NULL,
    response_kind     TEXT NOT NULL,   -- confirm|correct|unknown|defer|investigate
    value_text        TEXT NOT NULL DEFAULT '',
    target_kind       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    target_digest     TEXT NOT NULL,   -- 回答時点の対象 digest
    decision_method   TEXT NOT NULL DEFAULT 'manual',
    responded_by      TEXT,
    linked_intent_item_id INTEGER,
    linked_relation_decision_id INTEGER,
    linked_joint_session_id INTEGER,
    superseded_by_id  INTEGER,
    created_at        REAL NOT NULL,
    ... FK, System-scoped index
);
```

* `confirm` / `correct` — 対象が Intent 由来なら **既存の Intent confirm/correct
  実装を再利用** (重複実装しない)。relation 対象なら #388 の relation decision を
  呼ぶ。`purpose_need_response` はその監査行を linked id で指す。
* `unknown` — エラーではない。#286 の router ではなく、この need の固定
  answerability に従い調査へ送る。Joint Understanding session を
  `trigger='purpose_need'` で開く。この trigger 値は **この経路だけが書ける**
  (公開 create endpoint は `explicit_request` を強制する #336 の規律をそのまま
  踏襲し、422 `joint_understanding_trigger_not_settable` を維持)。
* `defer` — 保留も永続事実。対象 digest が変わるまで同じ need を再提示しない。
  digest が変わったら再提示される (決定的)。
* `investigate` — 明示的に調査を要求。`unknown` と同じ経路だが別の記録。

**回答・保留・調査 routing は別々の永続事実として監査可能**であること。

### 2.7 API

```
GET  /purpose-chain/next-question?session_id=&need_id=   -> PurposeQuestionOut
POST /purpose-chain/needs/{need_id}/respond              -> PurposeQuestionAnswerOut
```

`need_id` を指定した deep link で、その need が既に解決済み / 別 System / 不明な
場合は **安全に現在の質問または「質問なし」へ fallback** し、有限の
`fallback_reason` (`resolved` | `not_found` | `other_system` | `deferred`) を返す。
page view は回答として保存しない。

### 2.8 test

rule table 全行、tie-break、routing (system_researchable が人へ来ないこと)、
回答状態 5 種の永続化、AI 候補が確認なしに confirmed にならないこと、部分失敗時
に推測質問を出さないこと、deep link fallback、System 分離。

---

## 3. #390 — Overview / Interview の段階的開示 UX

Dashboard のみ。新しい endpoint を追加しない。server 判定を再導出しない。

### 3.1 Overview (Level 0)

主列の先頭に Purpose Frame。順序は契約:

1. 誰のどんな現状を変えるか (`beneficiary_problem`)
2. どの状態へ変えたいか (`desired_change`)
3. システムがどう介入するか (`intervention`)
4. 最重要 unknown があるときだけ、文脈付き質問 **1 件**

各要素 1〜2 文 (`display_statement`)。表示しないもの: optional field 一覧、完成率、
未入力件数、disabled 質問一覧、relation graph 全体、AI が補完した架空の対象者。
#380 の既存 order (現在地 → 発見 → 次にすること → ループ → runtime) は保持し、
Purpose Frame は System Brief の **上** に入る (Epic の問い「何のためのシステムか」
が最初に答えられるべき順序)。

質問カードは `question.why_now` と `unlocks` を必ず表示し、`[1つの質問に答える]`
は Interview の該当 need へ deep link (`/interview?purpose_need=<need_id>`)。
Overview では実行しない (#358 の「summary の CTA は navigate、execute しない」)。

### 3.2 Interview (Level 1)

`components/system-understanding/purpose-frame-panel.tsx`。3 要素と relation を
意味順の **縦方向** に表示 (graph を強制しない)。各要素で:
statement / confirmation / provenance / relation 状態 / source revision / evidence /
changed・stale・conflict / 確認・訂正・疑問を開く。

Level 2/3 の属性 (対象ユーザー優先順位、利用文脈、根本障壁、成功判定、critical
journey、time-to-value、制約、再利用 trigger、outcome measurement) は **現在の
need に関係するときだけ** 表示し、「詳細を追加」ではなく
「現在の判断を進めるために確認」として提示する。

### 3.3 状態別 UX (すべて component test を書く)

1. 3要素すべて unknown / 2. AI 候補のみ / 3. Vision confirmed・Purpose hypothesis /
4. 3要素 confirmed・relation unknown / 5. relation conflict / 6. recheck /
7. 質問不要 / 8. 質問1件 / 9. deferred / 10. system research へ routing /
11. 部分 API 失敗 / 12. narrow desktop・mobile

### 3.4 アクセシビリティ

heading 順 = 因果順、色だけで状態を表さない (テキストマーカー併記)、relation を
矢印だけに依存しない、質問 CTA に理由入り accessible description、keyboard で
詳細・確認・訂正・保留へ到達可能、loading / empty / unknown / unavailable /
conflict を区別、animation を必須にしない。

---

## 4. #391 — 必要時だけの検証 (Experience / Outcome / Reuse)

### 4.1 概念と状態

* `purpose_experience_hypothesis` — 最小の成功体験。
  state: `proposed | confirmed | retired`
* `purpose_outcome_criterion` — 成果証拠。
  state: `proposed | confirmed | observed | contradicted | not_observed | not_computed`
* `purpose_reuse_hypothesis` — 再利用契機。state は experience と同じ。

いずれも **relation id / element id に stable identity で接続**する。全 System へ
一律に要求しない。作成は need (`decision_criterion_missing` 等) が発生したときだけ
提案する。「Purpose Frame が L1 だから」を作成理由にしない。

### 4.2 安全性

runtime trace だけで利用者の成功・満足・継続意思を推測しない。analytics が無ければ
`not_observed`、canonical mapping が無ければ `not_computed`。human-reported evidence
と runtime observation を別カラムで保持し、synthetic fixture の結果を実利用者の
成果として表示しない。AI 提案を観測済み outcome として扱わない。

### 4.3 改善候補との接続

`purpose_outcome_criterion.experiment_id` / `candidate_version_id` を **明示的な
lineage 列**として持つ。System 全体の存在 check で結び付けない。対応が無ければ
「関連不明」と表示する。

### 4.4 L3 の到達

outcome criterion が measure / baseline / target / observation window をすべて
持つとき、その target 要素の `resolution_level` が `L3` になる (#388 §1.4)。

### 4.5 dogfooding

`docs/dogfooding-purpose-chain.md` に、実装者以外の確認者へ Overview を提示し、
事前説明なしで 4 問 (対象者と課題 / 望ましい変化 / システムの介入 / 最重要 unknown)
に答えてもらった結果を記録する。「viewport に入った」を「理解できた」の代替に
しない。誤読・迷った要素・回答時間を残し、失敗したら情報階層を直して再試験する。

---

## 5. 非目標 (Epic 全体)

* 初回に事業計画・ペルソナ・ジャーニーを入力させる
* System Purpose を長大な自由記述にする
* 全 System へ一律の retention 指標
* AI による人間の価値判断の自動確定
* profile completion score
* client-side heuristic による質問・readiness 判定
* code/docs gap を Vision gap と同一視する
* 既存 Intent Brief / Understanding Brief / Joint Understanding を別モデルで置換する
