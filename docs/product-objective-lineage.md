# Product Objective / Milestone / Gap(Epic #427 / #428-#433)

canonical design contract. 実装はこの文書に従う。

probe-agent は Purpose Chain(#387-#391)で「対象者と現在の課題 → 望ましい変化(Vision)
→ システムの介入(System Purpose) → Capability」を保持でき、UX Design Lineage
(#405-#409)と Stakeholder Value Network / Functional Lineage(#418-#424)でその
下流を Journey / Requirement / Solution Design / Flow / Node / Component / Trace /
Outcome まで接続できる。

しかし **Vision と Journey の間に、「Vision へ近づくための中間目標」「その検証可能な
到達点」「現状と目標状態の差」を第一級の正本として保持する層が無い**。現在の
「目標」と「Gap」は次のように分散している。

| 今どこにあるか | 何であって、何でないか |
| --- | --- |
| Intent Brief `goal` | Vision の**入力**。中間目標ではない |
| Capability | システムが**備える能力**。達成すべき中間状態ではない |
| Overview `next_milestone` | `overview_projection.LOOP_STAGE_NEXT_MILESTONE` の**静的表示文**。stable identity も revision も無い(§7.3) |
| `cell_goals` | Probe Cell の**実行・委譲用** Goal Tree(#300)。Product Objective の正本ではない(§1.4) |
| System Understanding Gap | docs / code / metadata の**不足** |
| Functional Lineage Gap | lineage 上の**構造的欠落** |
| as-is / to-be Journey diff、runtime mismatch、stale link / evidence | それぞれ**個別の差分・異常** |

そのため個別の不足は検出できても、**「どの Vision の、どの中間目標に対する Gap で、
その Gap を解消するためにどの UX・Feature・実装・評価が必要か」を一続きに説明できない**。

この Epic はその一層だけを足す。

```text
Stakeholder / Need / Environment          ← 既存正本(#418-#424)。複製しない
        ↓ 参照
Vision                                    ← 既存正本(#351 BriefResult.vision)。複製しない
        ↓ 参照 (product_objective_upstream_ref)
Product Objective                         ← #429 で新設
        ↓ 所属 (product_milestone.objective_id)
Milestone                                 ← #429 で新設
        ↓ 所属 (product_gap.milestone_id)
Gap                                       ← #429 で新設
        ↓ 参照 (product_gap_source_ref)     … 既存 Gap 検出器へ(#430)
        ↓ 参照 (ux_journey_upstream_ref)    … 下流へ(#431)
UX Journey / Journey Step                 ← 既存正本(#405-#409)
        ↓ 参照 (ux_requirement_step_link)
Requirement (+ Acceptance Criterion)      ← 既存正本
        ↓ 参照 (product_feature_requirement_link)
Feature                                   ← #431 で新設
        ↓ 参照 (product_feature_target_link)
Solution Design / Flow / Evolution Node / ← 既存正本。identity を借りるだけ
Component / Probe Cell / Probe Point
        ↓
Trace / Replay / Experiment / Outcome     ← 既存正本
```

---

## 0. 全 sub-issue に共通する不変条件

1. **新しい理解モデルを作らない。** Vision / System Purpose / Capability /
   Stakeholder / Need / Journey / Requirement / Solution Design / Flow /
   Evolution Node / Component / Probe Cell / Outcome の正本は既存のまま。この層が
   持つのは **Objective / Milestone / Gap / Feature という新しく著述される成果物**と、
   その上下への **参照 (ref / link)** だけである。上流・下流の内容を列へコピーしない
   — コピーした Capability 名は元が superseded された後も current として読めてしまう
   (#397 handoff が踏んだ轍、#405 §0-1 と同じ規律)。
2. **Objective / Milestone / Gap / Feature / Capability / Cell Goal / Issue Draft は
   別 entity。** 一つへ畳まない(§1)。特に:
   - Capability を Milestone として扱わない — Capability は**能力**、Milestone は
     **到達状態**。
   - Gap を Issue Draft として扱わない — Issue Draft は Gap の**外部化・実行候補**
     であって Gap の identity ではない(§5.6)。
   - Feature を Flow / Component / Capability として扱わない — Feature は
     **Requirement を満たす機能単位**。
   - `cell_goals` を Product Objective へ流用しない(§1.4)。
3. **AI は Objective / Milestone / Gap / link を提案できるが、確認・採用・解消・
   却下・優先判断は人間の明示判断だけ。** `decision_method: manual` の追記行として
   のみ記録し、AI 生成物の `decision_method: reasoning_llm` を人間の承認として
   読ませない。**執筆者 (`authored_by_kind`)・決定経路 (`decision_method`)・
   承認 (決定台帳の行) は 3 つの独立した軸**であり、1 列に畳まない
   (#337 の `origin_role` / `producer_kind` / `actor_kind` と同じ規律)。
4. **訂正は append-only revision。** 削除・上書きで監査を失わない。旧 revision は
   `superseded_by_id` を張るだけで残し、「あの時点の内容に対して人がこう判断した」
   という事実を保存する。**revision の追加は過去の human decision を削除せず、
   `recheck_state` を `stale` にするだけ**(#388 / #405 §2.5 と同じ)。
5. **Gap の 6 つの事実を 1 列へ畳まない。** 「検出元 (source)」「現在状態
   (current state)」「目標状態 (target state)」「解釈 (interpretation)」
   「優先判断 (priority)」「解消状態 (lifecycle)」は別軸である(§5.1)。
   一つの表示語に二つの事実を持たせない(#366)。
6. **runtime trace だけで Milestone 達成・Gap 解消・UX 成功・Outcome 達成を
   自動確定しない。** 達成判定は `product_milestone_assessment` の manual 行だけが
   作る。source が消えたことも、trace が届いたことも、Experiment が成功したことも、
   Design Option が採用されたことも、**それ自体では何も達成・解消しない**(§6)。
7. **合成 score を作らない。** 件数・centrality・confidence・severity を単一の
   優先度 score へ合成しない。**score 列は存在しない — 規約ではなく構造で禁じる**
   (#397 が同じ手法を採ったのと同じ)。件数は返してよいが、件数を重要度として
   扱わない。「完成度」「充足率」「confidence percentage」を返さない。
   優先度は人間が置く有限バンド (`ProductGapPriorityBand`) だけである。
8. **`unknown` / `unavailable` / `not_applicable` / `stale` / `contradicted` を
   同じ空値へ丸めない。**
   - `unknown` — 読めたが記録がない(開発者がまだ決めていない)
   - `unavailable` — 読み取り自体が失敗した(この request の事実)
   - `not_applicable` — 構造上その概念が当てはまらない
   - `stale` — 読めて存在するが、捕捉した時点から意味が動いた
   - `contradicted` — 読めて存在するが、検出元自身が「もう成り立たない」と言っている
   5 つは別の文言で表示する。1 つに畳むと、開発者が決めていないのか、システムが
   読めなかったのか、そもそも不要なのか、古いのか、否定されたのかが区別できない。
9. **既存 Gap を本文コピーで移行しない。** `source_kind + source_ref +
   captured_digest` で参照し、**読み取り時に kind ごとの唯一の resolver で解決する**
   (§5.4)。本文・severity・evidence の列を Gap 側に持たない — 構造で禁じる。
10. **判定は server canonical projection。** Dashboard は状態・staleness・優先度・
    次の 1 操作を再導出しない(#351 / #380 / #387 / #405 と同じ)。有限語彙は
    `app/models.py` の `Literal` で一度だけ定義し、`test_interview_type_parity.py`
    の `FINITE_TYPE_NAMES` で TS union を拘束する。
11. **既存の human gate を一切緩めない。** 理解の確認 / Alignment 項目の確定 /
    提案の承認・編集・却下 / 差分の適用 / 観測の開始 / 採否の記録 / publish /
    Replay approval / 固定化承認 / reopen 承認 / Journey・Requirement の確定 /
    Design Option の採用 は不変。この層が追加する Objective・Milestone・Gap・
    Feature の確定、Milestone 達成判定、Gap 解消・reopen、優先バンドの設定も
    すべて `decision_method: manual`。
12. **layout 座標を canonical state にしない。** Objective Map / Gap Workbench の
    描画は canonical data から生成する read-only projection である。保存してよいのは
    filter / collapsed refs / pinned refs のような表示設定だけ(#418 と同じ)。
13. **既存正本へ書き込まない。** `interview_*` / `purpose_*` / `understanding_*` /
    `ux_*` / `solution_design*` / `stakeholder_*` / `evolution_node*` / `cell_*` /
    `components` / `probe_points` / `feature_drafts` のどの行も、この Epic のコードは
    UPDATE / INSERT しない。唯一の例外は §7.1 が明記する
    `ux_journey_upstream_ref.ref_kind` の CHECK 拡張(テーブル再構築 migration)で、
    これは**語彙の拡張であって行の書き換えではない**。テストで守る。

---

## 1. 責務境界 — 何がどれを所有するか

### 1.1 用語

| 用語 | 問い | 正本 |
| --- | --- | --- |
| **Vision** | 誰の状態をどう変えたいか | 既存。`understanding_brief._resolve_vision`(確定 Intent `goal` > reviewer `vision`)。この層は**参照するだけ** |
| **System Purpose** | システムはどう介入するか | 既存。`BriefResult.system_purpose` |
| **Capability** | システムは何ができるか | 既存。`understanding_capability_entity`(#312) |
| **Product Objective** | Vision へ寄与する中間目標は何か | **新設**。`product_objective` |
| **Milestone** | その Objective が達成へ向かったと**観測・判断できる状態**は何か | **新設**。`product_milestone` |
| **Gap** | 明示された現状と、その Milestone の目標状態との差は何か | **新設**。`product_gap` |
| **UX Journey** | その Gap を解消するために対象者はどの経路で価値へ到達するか | 既存(#405)。`ux_journey` |
| **Requirement** | その体験が成り立つために何が満たされるべきか | 既存(#405)。`ux_requirement` |
| **Feature** | その Requirement を満たす機能単位は何か | **新設**。`product_feature` |
| **Solution Design** | その Feature をどう実現するか | 既存(#408)。`solution_design` |
| **Outcome** | 利用者に何が起きたか | 既存(#391)。`purpose_outcome_criterion` |

### 1.2 Objective と Capability

Capability は**価値を実現する能力**であり、既存正本を維持する。Objective /
Feature とは `product_objective_upstream_ref(ref_kind='capability_entity')` /
`product_feature_capability_link` で**明示 link** し、Objective や Milestone の
代替にはしない。

* 「決済ができる」は Capability。
* 「決済の離脱率を、初回利用者が 1 回で完了できる水準まで下げる」は Objective。
* 「初回利用者の決済完了が、手戻りなしで観測できる」は Milestone。

Capability が増えても Objective は達成しない。Objective が達成しても Capability は
増えない。これが両者を別 entity にする理由である。

### 1.3 Milestone と「工程進捗」

Milestone は**観測・判断可能な目標状態**であって、工程名でも進捗率でもない。
したがって:

* Milestone に「進捗 %」列は**存在しない**(§0-7)。
* Milestone の**定義が確定したか** (`design_status`) と、**達成したか**
  (`achievement`) は**別の 2 軸**である(§4.2)。定義を確定しただけで達成にはならず、
  達成判定をしても定義の確定は取り消されない。
* 達成判定は `product_milestone_assessment` の manual 行だけが作る。deadline や
  数値 KPI を全 Milestone へ強制しない。ただし**検証可能性は必須**で、
  `verification_method` が `unavailable` の Milestone は
  `achievement='unassessed'` のまま `assessability='unavailable'` として読める
  (§4.3)。

### 1.4 Product Objective と `cell_goals`(#300)

**完全に分離する。** 同じ「Goal」という語だが、所有する事実が違う。

| | `cell_goals` / `cell_tasks`(#300) | `product_objective`(#429) |
| --- | --- | --- |
| 何のため | Probe Cell の**実行責任の委譲**。単一 owner / 単一 `parent_goal` / acceptance+evidence 必須 | **製品としての中間目標**。Vision への寄与を説明する |
| 誰が作る | orchestrator(Cell Fabric) | 開発者(AI 提案可、確認は人間) |
| 生存期間 | Cell の稼働単位 | 製品の中間目標の単位(Cell より長い) |
| identity | `(system_id, cell_id, goal_id)` | `(system_id, objective_key)` |

**`cell_goals` を Product Objective へ自動移行しない**(#433 非目標)。両者を
link することも、この Epic では行わない — 実行責任と製品目標を link したくなった
時点で、その link の意味(委譲? 貢献? 由来?)を先に決める必要があり、それは
この Epic の範囲外である。

### 1.5 Gap と Issue Draft

`issue_drafts` は Gap の**外部化・実行候補**である。Gap の identity にはしない。

* Gap → Issue Draft は `product_gap_source_ref(source_kind='issue_draft')`
  **ではない**。Issue Draft は Gap の**下流**であって検出元ではない。
  `product_gap_artifact_link(link_kind='issue_draft')` で下流として持つ。
* 例外: 既存の Issue Draft から Gap を起こした場合に限り、`source_kind='issue_draft'`
  を検出元として使える(§5.5 の表に含む)。同じ行が上流にも下流にもなり得るので、
  **どちらとして記録したかを保存する**(link 側の kind で区別する)。
* Issue Draft が close されても Gap は自動 resolve されない(§6)。

### 1.6 Feature と `feature_drafts` / Feature Map

既存の `feature_drafts` は **snapshot に束縛された** Feature Intelligence(#23-#26)
の成果物である。snapshot を作り直せば行は作り直される。

したがって:

* **`product_feature` は System-scoped stable identity `(system_id, feature_key)`**
  を持つ新しい行であり、`feature_drafts` を置換しない。
* 両者は `product_feature_draft_link` で結ぶ。link は
  `feature_drafts.feature_id` と `captured_snapshot_id` と `captured_digest` を
  持ち、**draft の本文を `product_feature` へコピーしない**。
* snapshot を作り直しても `product_feature` の identity と履歴は切れない。link が
  `unresolved`(その snapshot の draft がもう無い)になるだけである。
* Feature Map の画面と `feature_drafts` の生成経路は**変更しない**。

---

## 2. なぜ「保存する」のか — Purpose Chain との違い

Purpose Chain(#387)は行をほとんど保存しない projection である。要素は
`interview_intent_item` と `current_understanding` から毎回導出でき、保存するのは
「システムでは再導出できない人間の判断」だけだった。

この層は UX Design Lineage(#405)と同じく**内容を保存する**。
**Objective / Milestone / Gap / Feature は、どの既存行からも導出できない、新しく
著述される内容**だからである。「この Vision に対して今どの中間目標へ寄せるか」は
コードにも trace にも書かれていない。

その代わり、**上流(Vision / Purpose / Capability / Need)の内容も、下流
(Journey / Requirement / Solution / Flow / Node / Component)の内容も、既存 Gap
検出器の本文も保存しない**。参照 + 捕捉 digest だけを持ち、解決は読み取り時に
kind ごとの正本 1 つに対して行う(`node_design._LINK_KIND_TARGET_SOURCE` /
`ux_design._resolve_upstream_target` と同じ設計)。

この非対称が、この Epic が「Purpose Chain の複製」でも「Gap 検出器の複製」でも
ない理由である。

---

## 3. モジュールと責務分担

| file | 役割 | issue |
| --- | --- | --- |
| `docs/product-objective-lineage.md` | 本書。canonical contract | #428 |
| `apps/control-server/app/product_objective.py` | Objective / Milestone / Gap の決定的 domain service。LLM を呼ばない | #429 |
| `apps/control-server/app/product_gap_sources.py` | `source_kind` ごとの唯一の resolver。既存検出ロジックを**再実装しない** | #430 |
| `apps/control-server/app/product_feature.py` | Feature identity と link | #431 |
| `apps/control-server/app/product_objective_projection.py` | Objective Map / Gap Workbench / Overview 断片の canonical projection | #432 |
| `apps/control-server/app/routes/product_objectives.py` | `APIRouter(prefix="/product-objectives", tags=["product-objective"])` | #429 |
| `apps/control-server/app/routes/product_gaps.py` | `APIRouter(prefix="/product-gaps", tags=["product-gap"])` | #429 / #430 |
| `apps/control-server/app/routes/product_features.py` | `APIRouter(prefix="/product-features", tags=["product-feature"])` | #431 |
| `apps/control-server/app/routes/product_lineage.py` | `GET /objective-map` / `GET /gap-workbench` の projection route | #432 |
| `apps/control-server/app/models.py` | `Literal` 語彙 + `*Out` / `*Request` モデル | #429-#432 |
| `apps/control-server/app/db.py` | 下記テーブル(`SCHEMA` 末尾へ追記) | #429-#431 |
| `apps/dashboard/src/components/product-objective/model.ts` | pure module(React も API client も無し) | #432 |
| `apps/control-server/tests/test_product_objective.py` 他 | contract test | #429-#433 |

**projection route を `/product-objectives/{objective_key}` の下に置かない。**
`GET /product-objectives/{objective_key}` が先に登録されると `/product-objectives/map`
が 422 になる(#338 が `/joint-understanding/lineage` で踏んだ轍)。したがって
`GET /objective-map` / `GET /gap-workbench` は独立した top-level path とする。

---

## 4. #429 — Objective / Milestone の永続化

### 4.1 identity

**identity は `(system_id, <kind>_key)`** — 開発者が与える安定 slug。
Evolution Node ADR-2 / #405 §2.2 と同じ理由で、**上流の id からも行 id からも
決して導出しない**:

* Purpose 要素の id (`core_capability:<sha256(name)[:16]>`) は claim の**名前の
  hash** であり、名前を直せば別 id になる。
* Understanding の再構築は `alignment_item` / `understanding_revision` の行 id を
  振り直す(#380)。
* LLM 生成の hash も使わない — 同じ Objective を 2 度提案させると別 id になる。

| entity | identity | 備考 |
| --- | --- | --- |
| Product Objective | `(system_id, objective_key)` UNIQUE | |
| Milestone | `(system_id, milestone_key)` UNIQUE | 所属 Objective は identity 行の `objective_id`(§4.4) |
| Gap | `(system_id, gap_key)` UNIQUE | 所属 Milestone は identity 行の `milestone_id`(§5.2) |
| Feature | `(system_id, feature_key)` UNIQUE | |

空文字は 422 (`product_objective_key_required` / `product_milestone_key_required` /
`product_gap_key_required` / `product_feature_key_required`)、重複は 409
(`*_key_conflict`)。DB の `UNIQUE (system_id, <key>)` が最終保証。
`ux_design.py` / `stakeholder_network.py` と同じ `KeyRequired` / `KeyConflict` の
typed exception を使う(slug の正規表現検証は**行わない** — 既存の兄弟 Epic の
どれも行っていない)。

### 4.2 状態は 5 つの独立した軸

一つの表示語に二つの事実を持たせない(#366)。

```python
ProductDesignStatus     = Literal["proposed", "confirmed", "rejected", "retired"]
ProductObjectiveState   = Literal["proposed", "confirmed", "active", "achieved",
                                  "rejected", "retired"]
ProductRecheckState     = Literal["current", "stale", "not_captured"]
ProductRevisionState    = Literal["current", "superseded"]
ProductAuthorshipKind   = Literal["developer", "reasoning_model"]
```

* **`objective_state`** — `product_objective_decision` の
  `(system_id, objective_key)` について最新の非 superseded 行から**導出**する。
  行が無ければ `proposed`。列に保存しない理由: 保存した lifecycle 値はそれが
  記述する行から drift しうるが、導出した値はしえない(#337 / #338 / #349 と
  同じ規律)。
* **`recheck_state`** — 現在有効な確定行の `captured_digest` が現在の
  `content_digest` と食い違えば `stale`。**`objective_state` は変えない**。
  確定を取り消すのではなく「あの内容に対して人が確定した」事実を残したまま
  再確認を促す。`captured_digest` が空の行は `not_captured` で、
  **`current` へ昇格させない**(#337 `premise_not_captured` と同じ fail-closed)。
* **`revision_state`** — `superseded_by_id IS NULL` かどうか。内容の版であり、
  判断の状態ではない。
* **`authored_by_kind`** — 誰の声か。上のどれとも独立。`reasoning_model` が
  書いた revision が `confirmed` になることはあり得る — それは「AI が書いた文を
  人が確認した」であって、執筆者が `developer` に変わるのではない。
* **`decision_method`** — どの経路で作られたか。決定台帳の行は常に `manual`
  (CHECK で強制)。

**Milestone は 2 軸に分かれる**(§1.3):

```python
ProductMilestoneAchievement = Literal["unassessed", "met", "not_met", "indeterminate"]
ProductMilestoneAssessability = Literal["assessable", "unavailable", "not_applicable"]
ProductMilestoneVerificationMethod = Literal["manual_review", "runtime_observation",
                                             "external_report", "unavailable"]
```

* `design_status`(`ProductDesignStatus`)— Milestone の**定義**が確定したか。
  `product_milestone_decision` から導出。
* `achievement` — **達成したか**。`product_milestone_assessment` の最新非 superseded
  行から導出。行が無ければ `unassessed`。`indeterminate` は「人が評価したが
  判定できなかった」— `unassessed`(まだ誰も見ていない)と別の事実である。
* `assessability` — `verification_method` が `unavailable` なら `unavailable`、
  `design_status` が `rejected`/`retired` なら `not_applicable`、それ以外
  `assessable`。**achievement を代替しない** — 評価できないことは
  「達成していない」ではない。

### 4.3 決定の有限語彙と遷移

```python
ProductObjectiveDecisionKind = Literal["confirm", "activate", "achieve",
                                       "reject", "retire", "reinstate"]
ProductMilestoneDecisionKind = Literal["confirm", "reject", "retire", "reinstate"]
ProductMilestoneAssessmentKind = Literal["met", "not_met", "indeterminate", "withdraw"]
```

`objective_state` への畳み込み(first match、最新非 superseded 行):

| decision | 結果 | 前提となる直前 state(それ以外は 422 `product_objective_not_decidable`) |
| --- | --- | --- |
| `confirm` | `confirmed` | `proposed` |
| `activate` | `active` | `confirmed`, `active` |
| `achieve` | `achieved` | `active` |
| `reject` | `rejected` | `proposed`, `confirmed` |
| `retire` | `retired` | `confirmed`, `active`, `achieved` |
| `reinstate` | `proposed` | `rejected`, `retired` |

`achieve` の前提が `active` だけであることが重要である。**確定しただけの
Objective を達成にはできない**。また `achieve` は Milestone の `achievement` を
参照しない — Milestone が全部 `met` でも Objective は自動達成しないし、逆に
Milestone が `unassessed` でも人間は Objective を `achieve` できる(理由は
`rationale` に残る)。これは §6 の「自動確定しない」の裏返しで、
**人間の判断を機械が代行も否定もしない**という一つの規律である。

Milestone の `design_status` は `ProductDesignStatus` へ同様に畳み込む
(`confirm→confirmed` / `reject→rejected` / `retire→retired` /
`reinstate→proposed`、`reinstate` の前提は `rejected`, `retired`、それ以外の
decision の前提は `proposed`, `confirmed`)。

`achievement` の畳み込み: `met→met` / `not_met→not_met` /
`indeterminate→indeterminate` / `withdraw→unassessed`。
**`design_status` が `confirmed` でない Milestone は assessment を受け付けない**
(422 `product_milestone_not_assessable`)— 定義が確定していない到達状態について
「達成した」と記録することは、何に対する達成なのかが言えない。

### 4.4 Objective の親子 / Milestone の所属・依存

**Objective の親は append-only の link テーブル、Milestone の所属は identity 行の
列**である。この非対称は意図的で、理由は次の 2 つ:

* Objective の親は**任意**(root が存在する)であり、製品が育つにつれ「単独の
  目標だったものが、より大きな目標の一部になる」ことが正常に起きる。それは
  Objective が別物になることではないので、identity を切らずに再親付けでき、かつ
  監査に残る必要がある → `product_objective_parent_link`(append-only)。
* Milestone の所属 Objective は**必須**であり、所属が変われば「何に向かう到達点
  なのか」という主題自体が変わる。revision が所属を変えられると、一つの Milestone
  の revision 履歴が二つの別主題の記録になる(`ux_journey.perspective` が identity
  行にある理由と同じ、#405 §2.3) → `product_milestone.objective_id`(NOT NULL、
  同一 System 検査、変更不可)。

**root へ戻すことも記録する。** `parent_objective_id` は NULL 可で、
NULL の行は「この Objective を意図的に切り離して root に戻した」という
**決定の記録**である(`rationale` / `created_by` / `created_at` を持つ)。
親を外すことは製品上の判断なので、親を付け替えたときと同じ重みで残す。
行を削除して同じ読み(現在の親が無い)を作らない — それは誰がなぜ切り離した
かの記録を消すことであり、§0-4 が禁じている。**削除は安全でもない**:
`superseded_by_id` は `ON DELETE SET NULL` の自己参照 FK なので、tip の行
だけ消すと直前の親が current として復活する。
**一度も親を持ったことがない root は行が無いままである** — §4.4 の
「NULL の親行を作らない」はその場合を指す。この列が NULL 可なのは、
**戻ったことを記録できるようにするため**だけである。

**循環は決定的に拒否する。**

* 自己参照 → 422 `product_objective_parent_self`
* 現在の親 link グラフ上の循環 → 422 `product_objective_parent_cycle`
* cross-System の親 → 404(参照先が見つからない)

判定は書き込み時に、**現在有効な (`superseded_by_id IS NULL`) 親 link だけ**を
辿って行う。深さ制限は設けない(製品目標の階層は Cell の span-of-control とは
違い、構造的な上限を主張できる根拠がない)。ただし循環検査は必ず訪問済み集合を
持つ反復で行い、再帰で書かない。

**循環検査と INSERT は同じ排他 transaction の中で行う** (`BEGIN IMMEDIATE`)。
検査の後に transaction を開くと、逆向きの 2 本を同時に要求した 2 つの writer が
どちらも「循環なし」と判定してから両方 commit でき、**DB に循環が残る**。
重複検査と対象行の再取得も同じ transaction の中へ入れる — 検査したのと違う
状態へ書き込まないため。Milestone の依存も同じ規律に従う。

Milestone の依存 (`product_milestone_dependency`) も同じ規律:
append-only、自己参照 422 `product_milestone_dependency_self`、循環 422
`product_milestone_dependency_cycle`、cross-System は 404、重複した現在有効な
link は 409 `product_milestone_dependency_duplicate`。

**依存は順序であって前提条件ではない。** `depends_on` の Milestone が
`met` でなくても、依存する側を `met` にできる。依存は表示順と影響範囲の説明に
使うだけで、達成判定のゲートにしない(§6)。

### 4.5 テーブル(#429)

すべて新規テーブルであり、既存テーブルを 1 つも変更しない
(§7.1 の `ux_journey_upstream_ref` を除く)。`db.py` の `SCHEMA` 末尾へ
`CREATE TABLE IF NOT EXISTS` を追記する。

```sql
-- product_objective: identity row. `(system_id, objective_key)` は開発者が与える
-- 安定 slug (§4.1)。`objective_state` の列は存在しない -- 決定台帳から導出する
-- (§4.2)。`current_revision_id` は denormalize した pointer で、それが指す
-- revision を insert するのと同じ transaction の中でだけ書く。
CREATE TABLE IF NOT EXISTS product_objective (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    objective_key       TEXT NOT NULL,
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'product-objective-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (current_revision_id)
        REFERENCES product_objective_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, objective_key)
);

-- product_objective_revision: 内容。append-only。
-- `content_digest` は意味を持つ列だけ (§8)。`created_by` / `created_at` /
-- `revision_number` / `change_note` は digest に入れない。
CREATE TABLE IF NOT EXISTS product_objective_revision (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    objective_id        INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    revision_number     INTEGER NOT NULL,
    title               TEXT NOT NULL DEFAULT '',
    intent              TEXT NOT NULL DEFAULT '',   -- 何を目指すか
    contribution        TEXT NOT NULL DEFAULT '',   -- Vision へどう寄与するか
    scope_note          TEXT NOT NULL DEFAULT '',   -- 含む / 含まない
    summary             TEXT NOT NULL DEFAULT '',
    content_digest      TEXT NOT NULL,
    authored_by_kind    TEXT NOT NULL DEFAULT 'developer'
                            CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method     TEXT NOT NULL DEFAULT 'manual'
                            CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id INTEGER,
    change_note         TEXT NOT NULL DEFAULT '',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    superseded_by_id    INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'product-objective-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (objective_id) REFERENCES product_objective (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_objective_revision (id) ON DELETE SET NULL,
    UNIQUE (objective_id, revision_number)
);

-- product_objective_parent_link: 再親付けを監査に残すための append-only link
-- (§4.4)。current は `superseded_by_id IS NULL` の行。root は行が無い状態で
-- 表す -- NULL の親行を作らない。
CREATE TABLE IF NOT EXISTS product_objective_parent_link (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    objective_id        INTEGER NOT NULL,
    parent_objective_id INTEGER NOT NULL,
    rationale           TEXT NOT NULL DEFAULT '',
    decision_method     TEXT NOT NULL DEFAULT 'manual'
                            CHECK (decision_method IN ('manual', 'reasoning_llm')),
    created_by          TEXT,
    created_at          REAL NOT NULL,
    superseded_by_id    INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (objective_id) REFERENCES product_objective (id) ON DELETE CASCADE,
    FOREIGN KEY (parent_objective_id)
        REFERENCES product_objective (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_objective_parent_link (id) ON DELETE SET NULL
);

-- 現在有効な親は Objective ごとに高々 1 本。append-only の訂正と両立させるため
-- table-level UNIQUE ではなく partial unique index を使う
-- (`ux_solution_design_option_current` と同じ理由)。
CREATE UNIQUE INDEX IF NOT EXISTS ux_product_objective_parent_current
    ON product_objective_parent_link (objective_id)
    WHERE superseded_by_id IS NULL;

-- product_objective_upstream_ref: Vision / Purpose / Capability / Stakeholder Need
-- への参照。内容はコピーせず `captured_digest` だけ持つ (§0-1, §8)。
CREATE TABLE IF NOT EXISTS product_objective_upstream_ref (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    objective_id        INTEGER NOT NULL,
    ref_kind            TEXT NOT NULL CHECK (ref_kind IN
                            ('vision_claim', 'purpose_element', 'purpose_relation',
                             'capability_entity', 'stakeholder_need')),
    target_ref          TEXT NOT NULL,
    target_row_id       INTEGER,
    captured_digest     TEXT NOT NULL DEFAULT '',
    captured_session_id INTEGER,
    note                TEXT NOT NULL DEFAULT '',
    decision_method     TEXT NOT NULL DEFAULT 'manual'
                            CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by          TEXT,
    created_at          REAL NOT NULL,
    superseded_by_id    INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (objective_id) REFERENCES product_objective (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_objective_upstream_ref (id) ON DELETE SET NULL
);

-- product_objective_decision: 人間の判断だけの台帳。`decision_method` は
-- CHECK で `manual` に固定する -- AI が自分の提案を確認できないことを
-- 規約ではなく構造で禁じる。
CREATE TABLE IF NOT EXISTS product_objective_decision (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    objective_id         INTEGER NOT NULL,
    objective_key        TEXT NOT NULL,
    decision             TEXT NOT NULL CHECK (decision IN
                             ('confirm', 'activate', 'achieve',
                              'reject', 'retire', 'reinstate')),
    rationale            TEXT NOT NULL DEFAULT '',
    captured_digest      TEXT NOT NULL DEFAULT '',
    captured_revision_id INTEGER,
    decision_method      TEXT NOT NULL DEFAULT 'manual'
                             CHECK (decision_method = 'manual'),
    decided_by           TEXT,
    superseded_by_id     INTEGER,
    created_at           REAL NOT NULL,
    schema_version       TEXT NOT NULL DEFAULT 'product-objective-decision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (objective_id) REFERENCES product_objective (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_objective_decision (id) ON DELETE SET NULL
);

-- product_milestone: identity row。所属 Objective は identity の属性で、
-- あとから変えられない (§4.4)。
CREATE TABLE IF NOT EXISTS product_milestone (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    milestone_key       TEXT NOT NULL,
    objective_id        INTEGER NOT NULL,
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'product-milestone-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (objective_id) REFERENCES product_objective (id) ON DELETE CASCADE,
    FOREIGN KEY (current_revision_id)
        REFERENCES product_milestone_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, milestone_key)
);

-- product_milestone_revision: 内容。`target_state` が「何が成り立っていれば
-- 到達したと言えるか」、`verification_method` が「それをどう確かめるか」。
-- 進捗率の列は存在しない (§1.3)。
CREATE TABLE IF NOT EXISTS product_milestone_revision (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    milestone_id        INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    revision_number     INTEGER NOT NULL,
    title               TEXT NOT NULL DEFAULT '',
    target_state        TEXT NOT NULL DEFAULT '',   -- 到達状態
    verification_method TEXT NOT NULL DEFAULT 'unavailable'
                            CHECK (verification_method IN
                                ('manual_review', 'runtime_observation',
                                 'external_report', 'unavailable')),
    verification_note   TEXT NOT NULL DEFAULT '',
    sequence_hint       INTEGER NOT NULL DEFAULT 0,  -- 表示順のみ。達成の前提ではない
    summary             TEXT NOT NULL DEFAULT '',
    content_digest      TEXT NOT NULL,
    authored_by_kind    TEXT NOT NULL DEFAULT 'developer'
                            CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method     TEXT NOT NULL DEFAULT 'manual'
                            CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id INTEGER,
    change_note         TEXT NOT NULL DEFAULT '',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    superseded_by_id    INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'product-milestone-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (milestone_id) REFERENCES product_milestone (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_milestone_revision (id) ON DELETE SET NULL,
    UNIQUE (milestone_id, revision_number)
);

-- product_milestone_dependency: 順序の説明であって達成のゲートではない (§4.4)。
CREATE TABLE IF NOT EXISTS product_milestone_dependency (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                INTEGER NOT NULL,
    milestone_id             INTEGER NOT NULL,
    depends_on_milestone_id  INTEGER NOT NULL,
    rationale                TEXT NOT NULL DEFAULT '',
    decision_method          TEXT NOT NULL DEFAULT 'manual'
                                 CHECK (decision_method IN ('manual', 'reasoning_llm')),
    created_by               TEXT,
    created_at               REAL NOT NULL,
    superseded_by_id         INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (milestone_id) REFERENCES product_milestone (id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_milestone_id)
        REFERENCES product_milestone (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_milestone_dependency (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_product_milestone_dependency_current
    ON product_milestone_dependency (milestone_id, depends_on_milestone_id)
    WHERE superseded_by_id IS NULL;

-- product_milestone_decision: 定義の確定 (§4.3)。達成判定ではない。
CREATE TABLE IF NOT EXISTS product_milestone_decision (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    milestone_id         INTEGER NOT NULL,
    milestone_key        TEXT NOT NULL,
    decision             TEXT NOT NULL CHECK (decision IN
                             ('confirm', 'reject', 'retire', 'reinstate')),
    rationale            TEXT NOT NULL DEFAULT '',
    captured_digest      TEXT NOT NULL DEFAULT '',
    captured_revision_id INTEGER,
    decision_method      TEXT NOT NULL DEFAULT 'manual'
                             CHECK (decision_method = 'manual'),
    decided_by           TEXT,
    superseded_by_id     INTEGER,
    created_at           REAL NOT NULL,
    schema_version       TEXT NOT NULL DEFAULT 'product-milestone-decision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (milestone_id) REFERENCES product_milestone (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_milestone_decision (id) ON DELETE SET NULL
);

-- product_milestone_assessment: 達成判定。定義の確定とは別テーブルであることが
-- 契約である (§1.3 / §4.2)。`evidence_note` は人が見たものの記述で、
-- runtime trace がここへ自動で行を作ることはない (§6)。
CREATE TABLE IF NOT EXISTS product_milestone_assessment (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    milestone_id         INTEGER NOT NULL,
    milestone_key        TEXT NOT NULL,
    assessment           TEXT NOT NULL CHECK (assessment IN
                             ('met', 'not_met', 'indeterminate', 'withdraw')),
    rationale            TEXT NOT NULL DEFAULT '',
    evidence_note        TEXT NOT NULL DEFAULT '',
    captured_digest      TEXT NOT NULL DEFAULT '',
    captured_revision_id INTEGER,
    decision_method      TEXT NOT NULL DEFAULT 'manual'
                             CHECK (decision_method = 'manual'),
    assessed_by          TEXT,
    superseded_by_id     INTEGER,
    created_at           REAL NOT NULL,
    schema_version       TEXT NOT NULL DEFAULT 'product-milestone-assessment-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (milestone_id) REFERENCES product_milestone (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_milestone_assessment (id) ON DELETE SET NULL
);
```

各テーブルに `CREATE INDEX IF NOT EXISTS idx_<table>_system ON <table> (system_id, id DESC)`
を付ける(既存テーブルと同じ)。親子・依存・ref・decision には
`(<owner>_id, id DESC)` の index も付ける。

### 4.6 上流参照の解決 — 4 つの独立した軸

`ux_design._resolve_upstream_target` / `node_design._LINK_KIND_TARGET_SOURCE` を
踏襲し、**kind ごとに正本を 1 つだけ**持つ。

| `ref_kind` | 正本 | `target_ref` |
| --- | --- | --- |
| `vision_claim` | `understanding_brief.build_understanding_brief(...).vision` | claim の `name`。digest は `claim_digest` |
| `purpose_element` | `purpose_chain.derive_purpose_chain(...).elements` | 要素の stable id(例 `core_capability:ab12…`) |
| `purpose_relation` | 同 projection の `relations` | `f"{kind}:{src}->{tgt}"` |
| `capability_entity` | `understanding_capability_entity`(#312 の current head) | `understanding_capability_entity.id` の 10 進文字列 |
| `stakeholder_need` | `stakeholder_network` の Need identity | `need_key` |

**`vision_claim` の digest は `understanding_brief.claim_digest` を使う。**
`claim_digest` は生の understanding item(dict)を受け取るので、resolver は
`BriefClaim` ではなく `current_understanding['vision']`(確定 Intent `goal` 由来の
場合は `interview_intent_item` の該当行)から digest を取る。`claim_payload` は
**名前を除いた**意味だけを hash するので、名前の変更は「別の claim」として
`unresolved` に、本文の変更は同じ claim の `stale` になる — これは
`understanding_diff` / #380 が既に採っている区別で、この層で作り直さない。

**Vision には行 identity が無い。** `BriefResult.vision` は
`understanding_brief` が毎回導出する claim であり、その identity は
`understanding_diff` と同じ **exact name equality** である。したがって
`vision_claim` 参照は「Vision の文言を言い直すと `unresolved` になる」という
弱さを構造的に持つ。これを隠さない — その弱さは `target_resolution` に正直に出る。
Vision を安定 id で参照したい場合は、確定した Intent Brief `goal` を
`purpose_element` として参照する経路が別に存在する。**Vision の本文を
`product_objective_revision` へコピーして「安定させる」ことは禁止する**
(§0-1 / #397 handoff の轍)。

報告する 4 軸(1 つに畳まない、#405 §2.7 と同じ):

```python
ProductRefRelationStatus  = Literal["confirmed", "proposed", "derived"]
ProductRefTargetResolution = Literal["resolved", "unresolved", "unavailable"]
ProductRefRecheckState     = Literal["current", "stale", "not_captured"]
```

* `relation_status` — **その参照を誰が張ったか**。`decision_method` から
  `manual→confirmed` / `reasoning_llm→proposed` / `deterministic→derived`
  (`node_design._DECISION_METHOD_TO_RELATION_STATUS` と同じ表)。2 つ目の保存軸を
  作らない。
* `target_state` — **参照先自身の状態**。各正本の語彙をそのまま運ぶ。翻訳しない
  (#380 superset 規則)。
* `target_resolution` — `resolved` / `unresolved`(正本は読めたが対象が無い) /
  `unavailable`(正本自体が読めなかった)。
* `recheck_state` — `captured_digest` と現在の digest の比較。`not_captured` は
  fail-closed。

---

## 5. #429 / #430 — Gap

### 5.1 Gap の 6 軸

Gap は「明示された現状」と「Milestone の目標状態」との差である。**6 つの事実を
別々に持つ**(§0-5)。一つでも畳むと、開発者が何を決めたのか、システムが何を
検出したのか、誰がどう解釈したのかが読めなくなる。

| 軸 | どこに持つか | 誰が書くか |
| --- | --- | --- |
| **検出元 (source)** | `product_gap_source_ref`(有限 `source_kind` + `source_ref` + `captured_digest`) | 検出器。本文は持たず読み取り時に解決(§5.4) |
| **現在状態 (current state)** | `product_gap_revision.current_state` | 人間 / AI 提案(`authored_by_kind`) |
| **目標状態 (target state)** | `product_gap_revision.target_state` + `target_state_mode` | 人間。Milestone から継承もできる(§5.3) |
| **解釈 (interpretation)** | `product_gap_revision.interpretation` | 人間 / AI 提案 |
| **優先判断 (priority)** | `product_gap_decision(decision='prioritize', priority_band=…)` | **人間のみ**。有限バンド。score ではない(§5.7) |
| **解消状態 (lifecycle)** | `product_gap_decision` から導出 | **人間のみ**(§5.6) |

`severity` の列は Gap に**存在しない**。severity は検出元がそれぞれの語彙で
持っており、読み取り時に**出所付きで**そのまま運ぶ(#380 superset 規則)。
複数 source の severity を一つへ正規化しない(#430 非目標)。

### 5.2 identity と所属

* identity は `(system_id, gap_key)`(§4.1)。
* 所属 Milestone は identity 行の `milestone_id`(NOT NULL、同一 System、変更不可)。
  Milestone が Objective に所属するのと同じ理由 — 「何との差か」が変われば主題が
  変わる。
* **同じ検出元を複数の Objective / Milestone へ関連付けられる**(#430 要件)。
  これは Gap を複数作ることで表す — 同じ `source_kind + source_ref` を持つ
  `product_gap_source_ref` 行が、別 Milestone の別 `product_gap` にぶら下がる。
  同一 Gap 内での同一 `(source_kind, source_ref)` の重複は 409
  `product_gap_source_duplicate` で拒否する。
* この設計の含意を明記する: **同じ検出元が 2 つの Milestone で別々に議論・解消
  され得る**。それは重複ではなく、「同じ事実が二つの目標に対して別の意味を持つ」
  という正しい表現である。projection は `source_kind + source_ref` で束ねた
  「この検出元を参照している Gap 一覧」を返す(§9.2)。

### 5.3 目標状態の継承

```python
ProductGapTargetMode = Literal["own", "inherited_from_milestone", "unknown"]
```

* `own` — Gap が自分の `target_state` を持つ。
* `inherited_from_milestone` — Milestone の `target_state` をそのまま目標とする。
  **本文はコピーせず**、読み取り時に Milestone の current revision から解決する。
  Milestone が動けば Gap の `recheck_state` が `stale` になる(§6)。
* `unknown` — まだ決めていない。`own` で空文字を入れることと区別する
  (§0-8)。

**継承した目標は response で見えなければならない。** `inherited_from_milestone`
の Gap は目標本文を保存しないので、API は読み取り時に解決した本文と、その
解決の可否を返す:

```
effective_target_state: Optional[str]        # 解決できた本文。できなければ None
effective_target_availability: Literal["own", "resolved", "unavailable", "unknown"]
```

* `own` — Gap 自身の `target_state` がそのまま目標。
* `resolved` — Milestone の current revision から解決できた。
* `unavailable` — Milestone またはその current revision が読めなかった。
  **空文字として返さない**(§0-8)。
* `unknown` — `target_state_mode='unknown'`。

**画面はこの本文を出す。** 出さなければ開発者は「何との差なのか」を見ないまま
Gap を解消できてしまい、この層の存在理由が消える。

**`decision_digest` も同じ区別を持つ。** Milestone が読めない場合と目標が
正当に空文字である場合を同じ digest 入力へ丸めない — 丸めると、読めなかった
Milestone が後から空の目標として現れても「変わっていない」と読める。
実装は前者を専用の sentinel として digest へ入れる。

### 5.4 検出元の federation(#430)

**既存検出ロジックは置換も再実装もしない。** `product_gap_source_ref` は
`source_kind` ごとに**唯一の resolver**へ dispatch し、本文・severity・evidence・
snapshot / revision の pin を**読み取り時に**解決する。

```python
ProductGapSourceKind = Literal[
    "manual",
    "system_understanding_gap",
    "understanding_review_gap",
    "understanding_claim_change",
    "functional_lineage_gap",
    "value_network_notice",
    "journey_baseline_diff",
    "requirement_diff",
    "capability_drift",
    "runtime_alignment_mismatch",
    "node_anomaly",
    "joint_understanding_open",
    "inquiry_unresolved",
    "issue_draft",
]

ProductGapSourceState = Literal[
    "current", "changed", "contradicted", "disappeared", "unavailable",
]
```

* `current` — 解決でき、捕捉 digest と一致する。
* `changed` — 解決できたが、内容の digest が動いた。**Gap は自動で解消も再開も
  しない**。`recheck_required` として読む。
* `contradicted` — 解決でき、**検出元自身が「その条件はもう成り立たない」と
  言っている**(triage が `resolved`、runtime が `match`、diff が `unchanged`、
  anomaly が `resolved`、Inquiry が `answered` など)。これは「解消した」では
  なく「**reopen ではなく close を検討すべき候補**」であり、Gap の lifecycle は
  人間の `resolve` 決定でしか動かない(§6)。
* `disappeared` — 正本は読めたが、その `source_ref` がもう無い。
* `unavailable` — 正本の読み取り自体が失敗した。この request の事実であって、
  Gap についての事実ではない。`disappeared` へ丸めない。

**source_kind ごとの唯一の resolver**(`app/product_gap_sources.py`)。
`source_ref` は必ず**再計算に耐える安定参照**であり、再構築で振り直される行 id を
使わない。

| `source_kind` | 正本 / 呼び出す既存関数 | `source_ref` | 追加 pin | `contradicted` の条件 | 画面 |
| --- | --- | --- | --- | --- | --- |
| `manual` | なし(開発者が直接書いた Gap) | `''` | — | なし | — |
| `system_understanding_gap` | `system_understanding_service` の gaps + `gap_triage.annotate_gaps` | `gap_triage.gap_key(gap)` = `"{gap_type}\|{target_kind}\|{sorted_targets}"` | `captured_snapshot_id` | triage 状態が `resolved` | `/system-understanding` |
| `understanding_review_gap` | `understanding_revision.gap_analysis`(reviewer の LLM 自己申告) | `f"{gap_type}\|{node_name}"` | `captured_revision_id` | **到達不能**(§5.4.1) | `/interview` |
| `understanding_claim_change` | `understanding_diff.diff_understanding` | `f"{section}\|{name}"` | `captured_revision_id` | 該当 claim が現在 `unchanged` | `/interview` |
| `functional_lineage_gap` | `functional_lineage.build_functional_lineage` | `f"{code}\|{subject_kind}\|{subject_ref}"` | — | **到達不能**(§5.4.1) | `/functional-lineage` |
| `value_network_notice` | `stakeholder_value_network.build_value_network` | `f"{code}\|{subject_kind}\|{subject_key}"` | — | **到達不能**(§5.4.1) | `/stakeholder-value-network` |
| `journey_baseline_diff` | `ux_design.baseline_diff_journey` | `f"{journey_key}\|{step_key}"` | — | `change_kind` が `unchanged` | `/ux-design-studio` |
| `requirement_diff` | `ux_design.diff_requirement_revisions` | `f"{requirement_key}\|{criterion_key}"` | `captured_revision_id` | `change_kind` が `unchanged` | `/ux-design-studio` |
| `capability_drift` | `drift.compute_anchor_drift` | `f"{path}\|{qualified_name}"` または `f"entrypoint:{entrypoint_id}"` | `captured_snapshot_id` + `captured_run_id` | status が `fresh` | `/capability-map` |
| `runtime_alignment_mismatch` | `alignment_item.runtime_check` | `alignment_item.review_subject_id` | — | `runtime_check` が `match` | `/interview` |
| `node_anomaly` | `node_anomaly` | `f"{node_key}\|{dedupe_key}"` | — | `status` が `resolved` | **なし**(§5.8) |
| `joint_understanding_open` | `joint_understanding_session` | `id` の 10 進文字列 | — | `status` が `closed` | `/interview` |
| `inquiry_unresolved` | `interview_inquiry` | `id` の 10 進文字列 | — | `status` が `answered` / `superseded` | `/interview` |
| `issue_draft` | `issue_drafts` | `id` の 10 進文字列 | — | `status` が `closed` / `rejected` | `/system-understanding` |

#### 5.4.1 `contradicted` に到達できない検出元がある

`contradicted` は「検出元自身が『その条件はもう成り立たない』と言っている」
状態である。したがって **検出元がそれを言える語彙を持っていなければ到達不能**
であり、その 3 kind では合成しない:

| kind | なぜ到達不能か |
| --- | --- |
| `understanding_review_gap` | reviewer の `gap_analysis` は JSON 配列の要素で、status 欄が無い |
| `functional_lineage_gap` | projection が毎回作り直され、「解消した」ではなく**出てこない**という形でしか消えない |
| `value_network_notice` | 同上 |

これらで条件が解消したときに出る答えは `disappeared` である。**`disappeared` を
`contradicted` として報告しない** — 前者は「検出元にもう無い」、後者は
「検出元が否定している」であり、開発者が次に取る操作が違う(§0-8)。
`manual` も同様に、外部正本を持たないので常に `current` で、
`disappeared` にも `contradicted` にも到達しない。

**この 4 kind について、テストは到達不能を到達不能として記録する。** 5 状態を
全部埋めるために合成した信号を作らない。

**`capability_drift` は `capability_hierarchy_nodes.id` を使わない。** その行 id は
hierarchy を作り直すたび振り直される。`drift.py` 自身が現在の事実を突き合わせる
のに使っているのと同じ `(path, qualified_name)` / `entrypoint_id` を参照にする。

**`runtime_alignment_mismatch` は `alignment_item.id` を使わない。** その行は
Alignment build のたび作り直される。#321 の `review_subject_id`(構造的 anchor
だけから作る決定的 identity)を参照にする。

**`understanding_review_gap` の弱さを隠さない。** reviewer の `gap_analysis` は
JSON 配列の中の要素で、行 identity を持たない。`(gap_type, node_name)` は
`node_name` を言い直せば別参照になる — その弱さは `source_state='disappeared'`
として正直に出る。この参照を「安定化」するために本文を Gap 側へコピーしない
(§0-9)。

**resolver は既存の検出ロジックを呼ぶだけで、判断を二重に持たない**
(#430 非目標)。triage の判断は `gap_triage_decisions` のまま、Functional
Lineage の判断は無いまま、Inquiry の状態は `interview_inquiry` のまま。
この層はそれを**読む**だけである。

### 5.5 resolver の失敗は section 単位で degrade する

resolver は 1 つでも `raise` してよい。呼び出し側は kind ごとに guard し、
失敗した kind だけ `source_state='unavailable'` + `degraded_sections` に記録して、
**解決できた Gap は表示する**(#430 完了条件)。失敗を `disappeared` や
「0 件」へ丸めない。

### 5.6 lifecycle は人間の決定からだけ導出する

```python
ProductGapLifecycle = Literal[
    "open", "acknowledged", "deferred", "resolved", "rejected", "obsolete",
]
ProductGapDecisionKind = Literal[
    "acknowledge", "defer", "resolve", "reject", "retire", "reopen", "prioritize",
]
```

畳み込み(最新の非 superseded 行、`prioritize` は lifecycle を動かさない):

| decision | 結果 | 前提(それ以外は 422 `product_gap_not_decidable`) |
| --- | --- | --- |
| `acknowledge` | `acknowledged` | `open` |
| `defer` | `deferred` | `open`, `acknowledged` |
| `resolve` | `resolved` | `open`, `acknowledged`, `deferred` |
| `reject` | `rejected` | `open`, `acknowledged`, `deferred` |
| `retire` | `obsolete` | `open`, `acknowledged`, `deferred` |
| `reopen` | `open` | `resolved`, `rejected`, `obsolete`, `deferred` |
| `prioritize` | 変化なし(`priority_band` だけを動かす) | `open`, `acknowledged`, `deferred`(終端状態では 422 — 片付いた Gap に優先度を置く意味が無い) |

* `rejected` — 人が「これは Gap ではない」と判断した。
* `obsolete` — 人が「この Gap はもう問いとして成り立たない」と判断した
  (前提が消えた)。`resolved`(差を埋めた)と別の事実である。
* **`source_state` は lifecycle を一切動かさない。** source が `disappeared` に
  なっても `contradicted` になっても、Gap は `open` のままである。動かせるのは
  `reopen_candidate` / `recheck_required` という**読み取り時の注意フラグ**だけ
  (§6)。source の消失で自動 resolve するのは、検出器の不調と問題の解決を同じ
  表示にすることであり、#430 が明示的に禁じている。

### 5.7 優先度は有限バンドであって score ではない

```python
ProductGapPriorityBand = Literal["unset", "watch", "next", "now"]
```

* 人間が `prioritize` 決定で置く。AI は提案できるが、提案は
  `decision_method='reasoning_llm'` の**別テーブル**(`product_gap_source_ref` でも
  `product_gap_decision` でもなく、`product_gap_revision.suggested_priority_note`
  というただのテキスト)にしか書けない。決定台帳の `decision_method` は
  CHECK で `manual` に固定されている。
* **件数・centrality・confidence・severity から計算しない。** score 列は存在
  しない(§0-7)。
* Objective Map は Gap 件数を表示してよいが、**件数の多い Objective を
  「重要」として並べ替えない**(#432 UI 要件)。並び順は
  `priority_band` → `lifecycle` → `milestone.sequence_hint` → `gap_key` の
  有限段階で、すべて有限語彙か開発者が置いた値である。

### 5.8 deep link が無い検出元

`node_anomaly` は API(`GET /nodes/{node_id}`)はあるが Dashboard 画面が無い
(Epic #394 Phase 5 の cockpit 画面は #401 の未着手分)。したがって:

* `deep_link` は `None` を返し、`deep_link_state='unavailable'` を添える。
* **偽の URL を組み立てない。** 「画面が無い」と「リンクが壊れている」は別の
  事実で、前者は正直に表示する。
* #401 が画面を作った時点で表を 1 行直せば済む。

### 5.11 Gap → Journey の正本は 1 つだけ

「この Gap を解消する体験はどれか」を書ける場所は **`ux_journey_upstream_ref`
(`ref_kind='product_gap'`) だけ**である。`product_gap_artifact_link` に
`ux_journey` を置かない。

二か所に書けると、片方だけ登録した System で Overview は「Journey 接続済み」、
Functional Lineage は `gap_without_journey` と答える。**同じ問いに 2 つの答えが
出る状態を構造的に作れないようにする**のがこの Epic の中心的な規律であり
(§0-1)、これはその規律を自分自身の中で破った例である。

`ux_journey_upstream_ref` を正本に選ぶ理由:

* #405 の upstream ref は既に「その Journey は何のために存在するのか」を
  Journey 側が所有するモデルであり、Objective / Milestone / Gap を同じ形で
  参照できる(§7.1 が既に 3 kind を足している)。
* Functional Lineage は既にこの表を読んでいる。
* Gap 側から引くときは逆引きするだけで、新しい保存は要らない。

Objective Map / Gap Workbench の「この Gap を解消する Journey」も、この表を
**逆引き**して表示する。Gap 側に link 行を作らない。

### 5.10 resolver の呼び出し契約

`app/product_gap_sources.py` は Gap の CRUD から独立した pure な解決層である。
`product_objective.py` はこの 1 つの関数だけを呼び、`source_kind` ごとの分岐を
自分で持たない。

```python
SOURCE_KINDS: Tuple[str, ...]          # get_args(ProductGapSourceKind)
SOURCE_STATES: Tuple[str, ...]         # get_args(ProductGapSourceState)

@dataclass(frozen=True)
class ResolvedSource:
    source_state: str                  # ProductGapSourceState
    title: str                         # 検出元が持つ見出し。無ければ code から作る
    detail: str                        # 1-2 文の説明
    severity: Optional[str]            # 検出元の語彙のまま。無ければ None
    severity_vocabulary: Optional[str] # 例 "gap_triage" / "functional_lineage" / "node_anomaly"
    current_digest: str                # captured_digest と比較する対象
    deep_link: Optional[str]           # 画面が無ければ None
    deep_link_state: str               # ProductDeepLinkState
    # resolver が**正本から決めた** pin (§5.4 の「追加 pin」列)。作成時に
    # そのまま保存する。
    resolved_snapshot_id: Optional[int]
    resolved_run_id: Optional[int]
    resolved_revision_id: Optional[int]
    extra: Dict[str, Any]              # kind 固有の表示用事実。判定には使わない

def resolve_source(
    conn, *, system_id: int, source_kind: str, source_ref: str,
    captured_digest: str = "",
    captured_snapshot_id: Optional[int] = None,
    captured_run_id: Optional[int] = None,
    captured_revision_id: Optional[int] = None,
) -> ResolvedSource: ...
```

* `source_state` の決定は resolver 内で **first match**:
  1. 正本の読み取りが例外 → `unavailable`
  2. `source_ref` が現在の正本に無い → `disappeared`
  3. §5.4 の表の `contradicted` 条件に当たる → `contradicted`
  4. `captured_digest` が空でなく `current_digest` と食い違う → `changed`
  5. それ以外 → `current`
* **`resolve_source` は例外を外へ投げない。** 読めなかったことは
  `source_state='unavailable'` という**結果**であり、呼び出し側の失敗ではない
  (§5.5)。ただし `source_kind` が語彙外なら `ValueError` を投げる — それは
  データではなくプログラムの誤りである。
* `severity` を翻訳・正規化しない。`severity_vocabulary` を必ず添えて、
  どの語彙の値なのかを表示側が言えるようにする(#380 superset 規則)。
* `extra` は表示のためだけにある。**projection はここから状態を決めない。**
* **pin は resolver が決め、caller は受け取らない。** `source_kind` ごとに
  どの snapshot / run / revision を指すべきかを知っているのは resolver だけで
  ある(§5.4 の表)。`add_gap_source_ref` は request body から pin を受け取らず
  (受け取ると「開発者が申告した時点」と「システムが読んだ時点」が混ざる)、
  `resolve_source` が返した pin を **digest と同じ transaction で**保存する。
* **pin を保存しないと解決できない kind がある。** `capability_drift` は
  base run と対象 snapshot の両方が無ければ drift を計算できないので、pin が
  無いままだと公開 API から作った source は**常に** `unavailable` になる。
  これは「読めなかった」ではなく「そもそも記録していない」であり、
  §0-8 が禁じている取り違えである。
* pin の必須性は kind ごとに有限:

  | `source_kind` | 必須 pin | 任意 pin |
  | --- | --- | --- |
  | `capability_drift` | `snapshot_id` + `run_id` | — |
  | `system_understanding_gap` | `snapshot_id` | — |
  | `understanding_review_gap` / `understanding_claim_change` / `requirement_diff` | `revision_id` | — |
  | 上記以外 | なし | なし |

  必須 pin を resolver が決められなかった場合は、その source を作成した時点で
  `source_state='unavailable'` として読める。**pin を推測して埋めない。**

### 5.9 テーブル(#429 / #430)

```sql
-- product_gap: identity row。所属 Milestone は identity の属性 (§5.2)。
CREATE TABLE IF NOT EXISTS product_gap (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    gap_key             TEXT NOT NULL,
    milestone_id        INTEGER NOT NULL,
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'product-gap-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (milestone_id) REFERENCES product_milestone (id) ON DELETE CASCADE,
    FOREIGN KEY (current_revision_id)
        REFERENCES product_gap_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, gap_key)
);

-- product_gap_revision: 内容。severity 列も score 列も存在しない (§5.1 / §0-7)。
-- `suggested_priority_note` は AI の提案を置く「ただのテキスト」であって、
-- 優先度そのものではない (§5.7)。
CREATE TABLE IF NOT EXISTS product_gap_revision (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_id                 INTEGER NOT NULL,
    system_id              INTEGER NOT NULL,
    revision_number        INTEGER NOT NULL,
    title                  TEXT NOT NULL DEFAULT '',
    current_state          TEXT NOT NULL DEFAULT '',
    target_state           TEXT NOT NULL DEFAULT '',
    target_state_mode      TEXT NOT NULL DEFAULT 'unknown'
                               CHECK (target_state_mode IN
                                   ('own', 'inherited_from_milestone', 'unknown')),
    interpretation         TEXT NOT NULL DEFAULT '',
    suggested_priority_note TEXT NOT NULL DEFAULT '',
    content_digest         TEXT NOT NULL,
    authored_by_kind       TEXT NOT NULL DEFAULT 'developer'
                               CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method        TEXT NOT NULL DEFAULT 'manual'
                               CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id    INTEGER,
    change_note            TEXT NOT NULL DEFAULT '',
    created_by             TEXT,
    created_at             REAL NOT NULL,
    superseded_by_id       INTEGER,
    schema_version         TEXT NOT NULL DEFAULT 'product-gap-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (gap_id) REFERENCES product_gap (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_gap_revision (id) ON DELETE SET NULL,
    UNIQUE (gap_id, revision_number)
);

-- product_gap_source_ref: 検出元への参照 (§5.4)。本文・severity・evidence の列は
-- 存在しない -- 構造で複製を禁じる。`captured_snapshot_id` / `captured_run_id` は
-- 内容ではなく PIN であり、resolver が同じ地点を読み直すために要る。
CREATE TABLE IF NOT EXISTS product_gap_source_ref (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    gap_id              INTEGER NOT NULL,
    source_kind         TEXT NOT NULL CHECK (source_kind IN
                            ('manual', 'system_understanding_gap',
                             'understanding_review_gap', 'understanding_claim_change',
                             'functional_lineage_gap', 'value_network_notice',
                             'journey_baseline_diff', 'requirement_diff',
                             'capability_drift', 'runtime_alignment_mismatch',
                             'node_anomaly', 'joint_understanding_open',
                             'inquiry_unresolved', 'issue_draft')),
    source_ref          TEXT NOT NULL DEFAULT '',
    captured_digest     TEXT NOT NULL DEFAULT '',
    captured_snapshot_id INTEGER,
    captured_run_id     INTEGER,
    captured_revision_id INTEGER,
    note                TEXT NOT NULL DEFAULT '',
    decision_method     TEXT NOT NULL DEFAULT 'manual'
                            CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by          TEXT,
    created_at          REAL NOT NULL,
    superseded_by_id    INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (gap_id) REFERENCES product_gap (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_gap_source_ref (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_product_gap_source_current
    ON product_gap_source_ref (gap_id, source_kind, source_ref)
    WHERE superseded_by_id IS NULL;

-- product_gap_evidence_ref: 人が見た根拠へのポインタ。本文は持たない。
CREATE TABLE IF NOT EXISTS product_gap_evidence_ref (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id        INTEGER NOT NULL,
    gap_id           INTEGER NOT NULL,
    evidence_kind    TEXT NOT NULL CHECK (evidence_kind IN
                         ('trace', 'experiment', 'replay_run', 'human_report',
                          'external_report', 'repository_path', 'other')),
    evidence_ref     TEXT NOT NULL,
    captured_snapshot_id INTEGER,
    note             TEXT NOT NULL DEFAULT '',
    decision_method  TEXT NOT NULL DEFAULT 'manual'
                         CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by       TEXT,
    created_at       REAL NOT NULL,
    superseded_by_id INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (gap_id) REFERENCES product_gap (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_gap_evidence_ref (id) ON DELETE SET NULL
);

-- product_gap_artifact_link: Gap の下流の外部化・実行候補 (§1.5)。
-- Issue Draft はここに来る -- 検出元ではない。
CREATE TABLE IF NOT EXISTS product_gap_artifact_link (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id        INTEGER NOT NULL,
    gap_id           INTEGER NOT NULL,
    -- `ux_journey` is deliberately NOT here (§5.11). A Gap's Journey lives
    -- in `ux_journey_upstream_ref(ref_kind='product_gap')`, and two
    -- writable homes for one relation is the twin-canon this Epic forbids.
    link_kind        TEXT NOT NULL CHECK (link_kind IN
                         ('issue_draft', 'ux_requirement',
                          'product_feature', 'solution_design')),
    target_ref       TEXT NOT NULL,
    target_row_id    INTEGER,
    captured_digest  TEXT NOT NULL DEFAULT '',
    note             TEXT NOT NULL DEFAULT '',
    decision_method  TEXT NOT NULL DEFAULT 'manual'
                         CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by       TEXT,
    created_at       REAL NOT NULL,
    superseded_by_id INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (gap_id) REFERENCES product_gap (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_gap_artifact_link (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_product_gap_artifact_current
    ON product_gap_artifact_link (gap_id, link_kind, target_ref)
    WHERE superseded_by_id IS NULL;

-- product_gap_decision: 人間の判断だけの台帳 (§5.6 / §5.7)。
-- `priority_band` は `prioritize` のときだけ意味を持つ。
CREATE TABLE IF NOT EXISTS product_gap_decision (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    gap_id               INTEGER NOT NULL,
    gap_key              TEXT NOT NULL,
    decision             TEXT NOT NULL CHECK (decision IN
                             ('acknowledge', 'defer', 'resolve', 'reject',
                              'retire', 'reopen', 'prioritize')),
    priority_band        TEXT NOT NULL DEFAULT 'unset'
                             CHECK (priority_band IN ('unset', 'watch', 'next', 'now')),
    rationale            TEXT NOT NULL DEFAULT '',
    captured_digest      TEXT NOT NULL DEFAULT '',
    captured_revision_id INTEGER,
    decision_method      TEXT NOT NULL DEFAULT 'manual'
                             CHECK (decision_method = 'manual'),
    decided_by           TEXT,
    superseded_by_id     INTEGER,
    created_at           REAL NOT NULL,
    schema_version       TEXT NOT NULL DEFAULT 'product-gap-decision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (gap_id) REFERENCES product_gap (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES product_gap_decision (id) ON DELETE SET NULL
);
```

**`priority_band` の導出**: 最新の非 superseded な `prioritize` 行の
`priority_band`。行が無ければ `unset`。lifecycle と独立した軸であり、
`resolved` になっても最後に置かれたバンドは読める(監査)。

---

## 6. 変更伝播 — 下流方向のみ、達成は伝播しない

| 変化 | 効果 |
| --- | --- |
| Vision claim の内容が動く | その `product_objective_upstream_ref` が `stale`。Objective の内容は変えない |
| Vision claim の名前が動く | その参照が `unresolved`(§4.6 の弱さ) |
| Purpose 要素 / relation / Capability / Need が動く | 同上 |
| Capability entity が current head から外れる | `target_state='superseded'`、`target_resolution` は `resolved` のまま |
| Objective revision が動く | その Objective の確定が `recheck_state='stale'`。**`objective_state` は変えない**。配下 Milestone の内容は変えない |
| Milestone revision が動く | その Milestone の確定と**達成判定**が `recheck_state='stale'`。`achievement` は変えない。`target_state_mode='inherited_from_milestone'` の Gap が `stale` |
| Gap revision が動く | その Gap の決定が `recheck_state='stale'`。`lifecycle` は変えない |
| Gap の source が `changed` | `source_state='changed'` + Gap に `recheck_required` フラグ。lifecycle は変えない |
| Gap の source が `contradicted` / `disappeared` | Gap に `reopen_candidate` / `close_candidate` フラグ。**lifecycle は変えない**(§5.6) |
| Milestone が全部 `met` になった | **Objective に何も起こらない**(§4.3) |
| Gap が全部 `resolved` になった | **Milestone に何も起こらない** |
| Journey / Requirement / Feature / Solution が動く | その `*_upstream_ref` / link が `stale`。**Gap は変わらない** |
| Design Option の採用 | **下流に何も起こらない**(#405 §3.6 のまま) |
| Experiment 成功 / Trace 受信 / Replay 一致 | **何も確定しない**(§0-6) |
| snapshot が動く | `captured_snapshot_id` を持つ source ref と evidence ref だけ |

**上流方向へは伝播しない。** Gap を直しても Milestone は `stale` にならず、
Milestone を直しても Objective は `stale` にならない。ここを対称にすると、下流の
作業が上流の確定を勝手に無効化する(#388 / #405 §2.9 と同じ理由)。

**「候補」と「状態」を分ける。** `reopen_candidate` / `close_candidate` /
`recheck_required` は projection が返す**読み取り時のフラグ**であり、
`ProductGapLifecycle` の値ではない。人がそのフラグを見て `reopen` / `resolve` /
新しい revision を作ったときだけ、状態が動く。

---

## 7. #431 — 下流 lineage(UX Journey / Feature / 実装)

### 7.1 Journey の upstream に Objective / Milestone / Gap を足す

既存の `ux_journey_upstream_ref.ref_kind` の CHECK を
`('purpose_element', 'purpose_relation', 'capability_entity')` から
`(… , 'product_objective', 'product_milestone', 'product_gap')` へ拡張する。

SQLite は table 制約をその場で変更できず、`CREATE TABLE IF NOT EXISTS` も既存 DB を
直せないので、**テーブルを 1 度だけ再構築する** migration
`db._migrate_ux_journey_upstream_ref_kinds` を追加する
(`_migrate_solution_design_option_unique` と同じ手法):

1. `PRAGMA table_info` / `sql` を読み、CHECK に `product_gap` が含まれていれば
   **no-op**(構造的検出。version flag を持たない)。
2. `ALTER TABLE ux_journey_upstream_ref RENAME TO ux_journey_upstream_ref_legacy`
3. 旧 index を drop(名前を解放)
4. 現行 DDL で作り直す
5. `INSERT INTO ux_journey_upstream_ref SELECT * FROM ux_journey_upstream_ref_legacy`
6. legacy を drop

**これは語彙の拡張であって既存行の書き換えではない** — 全行がそのまま入り、
`ref_kind` の値は 1 つも変わらない。`init_db()` の migration 列へ、
`executescript(SCHEMA)` の後に加える。

新 3 kind の resolver(`ux_design._resolve_upstream_target` へ追加):

| `ref_kind` | 正本 | `target_ref` |
| --- | --- | --- |
| `product_objective` | `product_objective`(current revision) | `objective_key` |
| `product_milestone` | `product_milestone`(current revision) | `milestone_key` |
| `product_gap` | `product_gap`(current revision) | `gap_key` |

`target_state` は各々 `objective_state` / `design_status` / `lifecycle` を
そのまま運ぶ(翻訳しない、#380 superset 規則)。

**後方互換**: 既存の Purpose / Capability direct ref は読み書きとも不変。
Objective を経由しない Journey は今までどおり有効で、この Epic はそれを
「gap がある」とも「不正」とも言わない。

### 7.2 Feature

```python
ProductFeatureLinkKind = Literal[
    "solution_design", "evolution_node", "component", "probe_point",
    "static_flow", "runtime_flow", "experiment", "replay_run",
    "purpose_outcome_criterion",
]
```

* identity `(system_id, feature_key)` + append-only revision(§4.1 / §4.5 と同形)。
* `product_feature_requirement_link` — `ux_requirement` との**多対多**。
  `captured_requirement_revision_id` + `captured_digest` を持ち、Requirement が
  動けば `stale`。
* `product_feature_capability_link` — `understanding_capability_entity.id` への
  明示 link。**Feature と Capability は別 entity**(§1.2)であり、UI 上も
  別セクションに出す(#431 完了条件)。
* `product_feature_target_link` — 上表の `link_kind` へ。resolver は
  `solution_design._resolve_target` / `node_design._resolve_capability` /
  `_resolve_flow` を**再利用する**(再実装しない)。
* `product_feature_draft_link` — `feature_drafts.feature_id` + `captured_snapshot_id`
  + `captured_digest`(§1.6)。draft の本文はコピーしない。
* `product_feature_decision` — `ProductDesignStatus` の確定台帳(`manual` 固定)。

**Feature 名や説明の類似から自動 link しない**(#431 原則)。link は
`decision_method='manual'`(人)か `reasoning_llm`(AI 提案)で、後者は
`relation_status='proposed'` として読める(§4.6)。**確認は manual decision だけ**。

**Design Option の採用を Feature 実装完了や Gap 解消として扱わない**
(§6 / #405 §3.6)。

### 7.3 Functional Lineage への追加

`functional_lineage.build_functional_lineage` の node kind / edge kind /
gap code へ次を足す。既存 code の意味は**変えない**。

* node kind: `product_objective`, `product_milestone`, `product_gap`,
  `product_feature`
* 新 gap code(`LineageGapSeverity` は既存の `blocking` / `attention` /
  `informational`、code ごとに**固定**。件数から計算しない):

| code | severity | 意味 |
| --- | --- | --- |
| `objective_without_vision_ref` | `attention` | Objective が Vision / Purpose / Capability / Need のどれも参照していない |
| `objective_without_milestone` | `attention` | Objective に Milestone が 1 つも無い |
| `milestone_without_gap` | `informational` | Milestone に Gap が 1 つも無い |
| `milestone_without_verification` | `attention` | `verification_method='unavailable'` |
| `gap_without_journey` | `attention` | Gap を解消する Journey が無い |
| `gap_source_unresolved` | `attention` | `source_state` が `disappeared` |
| `gap_source_unavailable` | `informational` | `source_state` が `unavailable` |
| `gap_source_contradicted` | `informational` | `source_state` が `contradicted`(close 候補) |
| `requirement_without_feature` | `attention` | Requirement を満たす Feature が無い |
| `feature_without_implementation_target` | `attention` | Feature に実装対象 link が無い |
| `feature_without_capability` | `informational` | Feature が Capability を参照していない |

**参照は解決できたものだけが edge になる。** Objective の upstream ref も
Feature の requirement / capability / target link も、`target_resolution` が
`resolved` の行だけを graph の edge として足す。`unresolved` / `unavailable` /
`stale` は既存の `unresolved_reference` / `unavailable_reference` /
`stale_link` として出す(この 3 つは既に `_GAP_SEVERITY` にあり、
`add_reference_gaps` が他の hop で使っている形をそのまま使う)。

解決できない参照を通常の edge として足すと、**消えた対象・別 System の対象が
phantom node として図に出て**、`feature_without_implementation_target` も
消える。lineage の完成度を過大評価することになり、この Epic が答えたい
「どこが繋がっていないか」を隠す。

**`objective_without_vision_ref` は「ref 行があるか」では判定しない。**
Vision / Purpose(`vision_claim` / `purpose_element` / `purpose_relation`)への
**解決できた**参照があるかで判定する。Capability や Need への参照は
Vision 参照の代わりにならない — Capability は「何ができるか」であって
「何のためか」ではない(§1.2)。

### 7.4 Overview `next_milestone` の扱い

現行の `overview_projection.LOOP_STAGE_NEXT_MILESTONE` は改善ループ stage に
紐づく**静的表示文**であり、canonical Milestone ではない。

* **その表示文は残す**(改善ループ rail の説明として意味がある)が、
  **フィールド名から Milestone という語を外す**。`OverviewLoopStageOut` の
  `next_milestone` を `stage_completion_hint` へ改名し、TS 側も追随する
  (#432 の一部。`test_interview_type_parity.py` が守る)。
* canonical Milestone は Overview の**別セクション** `objective` に出る(§9.1)。
* 改名は破壊的変更なので、#433 の migration 節で「旧フィールドを読む consumer は
  Dashboard だけ」であることを確認したうえで一度に行う。

---

## 8. digest

```python
def content_digest(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

`purpose_chain.element_digest` / `understanding_brief.claim_digest` /
`ux_design` と同じ canonicalization。**意味を持つ列だけ**を入れる:

| 対象 | digest 入力 |
| --- | --- |
| Objective revision | `title, intent, contribution, scope_note, summary` |
| Milestone revision | `title, target_state, verification_method, verification_note, summary`(`sequence_hint` は**入れない** — 並べ替えは表示順を変えるだけで、その Milestone の意味を変えない) |
| Gap revision | `title, current_state, target_state, target_state_mode, interpretation`(`suggested_priority_note` は**入れない** — AI の提案メモが動いても人の確定を stale にしない) |
| Feature revision | `title, statement, rationale, scope_note, summary` |

`created_by` / `created_at` / `revision_number` / `change_note` は**入れない** —
再確認は**意味の変化**で促すのであって、記録の存在で促すのではない
(#308 が `confirmation_id` を、#337 が Intent の `status` を除外したのと同じ理由)。

---

## 9. #432 — projection と Dashboard

### 9.1 canonical projection

判定はすべて server が行う。Dashboard は再導出しない(§0-10)。

```
GET /objective-map    -> ObjectiveMapOut
GET /gap-workbench    -> GapWorkbenchOut
```

`GET /overview` には **`objective` セクションを 1 つだけ**足す
(`OverviewObjectiveOut`)。中身:

* `vision`(既存 `brief.vision` の参照。二重に導出しない)
* `active_objective` — `objective_state='active'` の Objective。複数あれば
  `active_objective_count` を添えて**最新の確定順で 1 件**を出し、
  「他に N 件」と明示する。**件数や Gap 数で「重要な方」を選ばない**(§0-7)。
* `next_milestone` — その Objective 配下で `design_status='confirmed'` かつ
  `achievement='unassessed'` のうち `sequence_hint` 昇順の先頭。**canonical
  Milestone の識別子を持つ**(§7.4 の表示文とは別物)。
* `primary_gap` — その Milestone 配下で
  `priority_band`(`now` > `next` > `watch` > `unset`)→ `lifecycle`(`open` >
  `acknowledged` > `deferred`)→ `gap_key` の順で先頭 1 件。**すべて有限段階の
  first match** であり、score ではない。
* `objective_state` / `readiness`(§9.3)

**Overview は何も書かない**(#380 と同じ)。`build_objective_overview` は
`evaluate_session_workflow` のような永続化を伴う関数を呼ばない。

### 9.2 Gap Workbench

* Objective 別 / Milestone 別の open / stale / resolved 一覧
* `source_kind` 別内訳(件数。重要度ではない)
* 「同じ検出元を参照している Gap 一覧」(§5.2 の多対多を読む向き)
* Gap → 元の検出画面 / Journey / Requirement / Feature / 実装 / evidence への
  deep link。deep link が無い kind は `deep_link_state='unavailable'`(§5.8)
* 確認 / 関連付け / 保留 / 解消 / reopen / 優先バンド設定を **manual action**
  として提供する。**Gap を選んでも何も自動実行しない**(#432 非目標)

### 9.3 「次の 1 操作」

Overview の既存 15 行 first-match 表(`overview_projection.decide_next_action`)は
**置き換えない**。この Epic は同じ規律で `objective` セクション内の
**独立した first-match 表**を持ち、`OverviewObjectiveOut.next_step` として返す。
両者は別フィールドであり、片方が他方を上書きしない — Overview の
`next_action` は「システムを動かすために次に何をするか」、Objective の
`next_step` は「目標に向けて次に何を決めるか」であり、別の問いである。

`ProductObjectiveNextStepKey`(first match、0 件または 1 件):

| # | 条件 | key |
| --- | --- | --- |
| 1 | Brief / Objective のどちらかが読めない | `unavailable`(action 無し) |
| 2 | Vision claim が無い、または `BriefClaim.confirmation` が `confirmed` 以外(`understanding_brief` の判定をそのまま読む。再導出しない) | `confirm_vision` |
| 3 | Objective が 1 つも無い | `create_objective` |
| 4 | `proposed` の Objective がある | `confirm_objective` |
| 5 | `active` な Objective が無い | `activate_objective` |
| 6 | その Objective に Milestone が無い | `create_milestone` |
| 7 | `proposed` の Milestone がある | `confirm_milestone` |
| 8 | `recheck_state='stale'` の確定がある | `recheck_stale_decision` |
| 9 | `source_state` が `changed`/`contradicted`/`disappeared` の Gap がある | `review_gap_source` |
| 10 | その Milestone に Gap が無い | `create_gap` |
| 11 | `priority_band='unset'` の open Gap がある | `prioritize_gap` |
| 12 | `now`/`next` の open Gap に Journey link が無い | `link_gap_to_journey` |
| 13 | Journey はあるが Requirement→Feature が繋がっていない | `link_requirement_to_feature` |
| 14 | Milestone が `unassessed` で、配下 Gap が全部 `resolved` | `assess_milestone` |
| 15 | 上のどれでもない | `none`(action 無し、`state='complete'`) |

* **`waiting` / `unavailable` は action を持たない**(#380 の規律)。
  永久に disabled な CTA を出さず、理由の文を出す。
* CTA は **navigate であって execute ではない**(#432 完了条件)。

### 9.4 情報設計 — サイドバーに 3 項目足さない

現在のサイドバー(`components/layout/sidebar.tsx` の `NAV_GROUPS`)は 28 項目
ある。#432 は「28 項目のサイドバーへ安易に 3 項目追加しない情報設計」を
完了条件にしている。したがって:

* **新規サイドバー項目は 1 つだけ**: `Understand` グループへ
  **「Objective Map」**(`/objective-map`、subtitle 付き)。
* **Gap Workbench は独立ページを作らない。** Objective Map 内の
  第 2 レーンとして実装し、`/objective-map?view=gaps&gap=<gap_key>` で
  deep link 可能にする。既存の `readSharedSelection` / `writeSharedSelection`
  の selection context 共有に参加する。
* Overview の `objective` セクションから Objective Map への lead を 1 本張る。
* Purpose Chain / UX Design Studio / Functional Lineage からは、既存の
  `ref_kind` / `ref` URL パラメータ経由で相互に飛ぶ。

### 9.5 表示規律

* Objective 階層は progressive disclosure。全ツリーを常時展開しない。
* Milestone は工程進捗と達成判定を混同しない(§1.3)。`design_status` と
  `achievement` を**別のラベルで**出す。
* `unknown` / `unavailable` / `not_applicable` / `stale` / `contradicted` は
  **色だけでなく文言・label で**区別する(§0-8)。
* Objective Map は Gap 件数を出してよいが、件数で並べ替えない(§5.7)。
* loading が長引く場合は対象 section・待機理由・再試行を出す。無期限 skeleton に
  しない。
* 新規作成(Objective が 0 件)と既存 System 改善(Objective はあるが Gap が
  未整理)の空状態を分ける。
* desktop / 狭幅 / keyboard / screen reader で同じ意味順序を保つ。
* UI copy は日本語(#266)。`Objective` / `Milestone` / `Gap` / `Feature` /
  `Capability` は技術用語として原形のまま、初出のみ併記する。
* **既存の `gap` 語彙と衝突させない**: `components/functional-lineage/model.ts` の
  `GAP_CODE_LABEL` / `GAP_SEVERITY_LABEL` / `GAP_SEVERITY_MARKER` は
  Functional Lineage 用である。Product Gap の label は
  `components/product-objective/model.ts` に別名で置く。

---

## 10. API

```
GET    /product-objectives                                  -> ProductObjectiveListOut
POST   /product-objectives                                  -> ProductObjectiveOut
GET    /product-objectives/{objective_key}                  -> ProductObjectiveDetailOut
POST   /product-objectives/{objective_key}/revisions        -> ProductObjectiveOut
POST   /product-objectives/{objective_key}/parent           -> ProductObjectiveOut
DELETE /product-objectives/{objective_key}/parent           -> ProductObjectiveOut
POST   /product-objectives/{objective_key}/upstream-refs    -> ProductObjectiveRefOut
POST   /product-objectives/{objective_key}/decisions        -> ProductObjectiveDecisionOut

GET    /product-objectives/{objective_key}/milestones       -> ProductMilestoneListOut
POST   /product-milestones                                  -> ProductMilestoneOut
GET    /product-milestones/{milestone_key}                  -> ProductMilestoneDetailOut
POST   /product-milestones/{milestone_key}/revisions        -> ProductMilestoneOut
POST   /product-milestones/{milestone_key}/dependencies     -> ProductMilestoneOut
POST   /product-milestones/{milestone_key}/decisions        -> ProductMilestoneDecisionOut
POST   /product-milestones/{milestone_key}/assessments      -> ProductMilestoneAssessmentOut

GET    /product-gaps                                        -> ProductGapListOut
POST   /product-gaps                                        -> ProductGapOut
GET    /product-gaps/{gap_key}                              -> ProductGapDetailOut
POST   /product-gaps/{gap_key}/revisions                    -> ProductGapOut
POST   /product-gaps/{gap_key}/source-refs                  -> ProductGapSourceOut
POST   /product-gaps/{gap_key}/evidence-refs                -> ProductGapEvidenceOut
POST   /product-gaps/{gap_key}/artifact-links               -> ProductGapArtifactOut
POST   /product-gaps/{gap_key}/decisions                    -> ProductGapDecisionOut

GET    /product-features                                    -> ProductFeatureListOut
POST   /product-features                                    -> ProductFeatureOut
GET    /product-features/{feature_key}                      -> ProductFeatureDetailOut
POST   /product-features/{feature_key}/revisions            -> ProductFeatureOut
POST   /product-features/{feature_key}/requirement-links    -> ProductFeatureLinkOut
POST   /product-features/{feature_key}/capability-links     -> ProductFeatureLinkOut
POST   /product-features/{feature_key}/target-links         -> ProductFeatureLinkOut
POST   /product-features/{feature_key}/draft-links          -> ProductFeatureLinkOut
POST   /product-features/{feature_key}/decisions            -> ProductFeatureDecisionOut

GET    /objective-map                                       -> ObjectiveMapOut
GET    /gap-workbench                                       -> GapWorkbenchOut
```

規律(既存 route と同じ):

* router 登録は `app.include_router(product_objectives.router, dependencies=_auth)`。
* GET は `system_id: int = Depends(get_system_id)` のみ。**GET は書き込まない**。
* 書き込みは `Depends(require_user)` を必ず伴い、actor は `Principal` から
  導出する。**request body から `created_by` / `decided_by` を受け取らない**
  (全 Request モデルは `ConfigDict(extra="forbid")`)。
* 例外は `ux_design.py` / `stakeholder_network.py` 式の typed exception
  (`KeyRequired` / `KeyConflict` / `NotFound` / `StaleDigest` / `NotDecidable` /
  `CycleRejected` / `ValidationError`)を `isinstance` dispatch で
  `(code, status)` へ写す。
* cross-System 参照は 404(存在を漏らさない)。
* 全 list / detail は `degraded_sections` / `degraded_detail` を返す(§5.5)。

### 10.1 エラーコード(有限)

| code | status |
| --- | --- |
| `product_objective_key_required` / `product_milestone_key_required` / `product_gap_key_required` / `product_feature_key_required` | 422 |
| `product_objective_key_conflict` / `product_milestone_key_conflict` / `product_gap_key_conflict` / `product_feature_key_conflict` | 409 |
| `product_objective_parent_self` / `product_objective_parent_cycle` | 422 |
| `product_milestone_dependency_self` / `product_milestone_dependency_cycle` | 422 |
| `product_milestone_dependency_duplicate` | 409 |
| `product_gap_source_duplicate` / `product_gap_artifact_duplicate` | 409 |
| `product_objective_decision_stale_digest` / `product_milestone_decision_stale_digest` / `product_gap_decision_stale_digest` / `product_feature_decision_stale_digest` | 409 |
| `product_objective_not_decidable` / `product_milestone_not_decidable` / `product_gap_not_decidable` / `product_feature_not_decidable` | 422 |
| `product_milestone_not_assessable` | 422 |
| `product_ref_kind_invalid` / `product_source_kind_invalid` / `product_link_kind_invalid` | 422 |

**stale digest は fail closed**: 決定 request が `captured_digest` を送り、それが
現在の `content_digest` と食い違えば 409。空文字なら検査しない(その決定は
`recheck_state='not_captured'` として読める)。

---

## 11. Migration / backward compatibility / rollback

* **新規テーブルはすべて `CREATE TABLE IF NOT EXISTS` を `SCHEMA` 末尾へ追記する
  だけ**。既存行に対応物が無いので backfill も legacy 行の概念も無い。
* **既存テーブルへの変更は 1 つだけ**: §7.1 の
  `db._migrate_ux_journey_upstream_ref_kinds`(`ref_kind` CHECK の拡張、
  テーブル再構築)。構造的検出で冪等、全行保存。`init_db()` の migration 列へ
  `executescript(SCHEMA)` の後に追加する。
* **`OverviewLoopStageOut.next_milestone` → `stage_completion_hint` の改名**
  (§7.4)。server と TS を同時に直し、`test_interview_type_parity.py` が守る。
  この 1 フィールドを読む consumer は Dashboard だけであることを #433 で確認する。
* **Product Objective 未導入 System は graceful empty state**。
  `GET /overview` の `objective` セクションは `objective_state=null` +
  `next_step='create_objective'` を返し、`degraded` にはしない — 「まだ作って
  いない」は失敗ではない。`not_started` という値を `ProductObjectiveState` へ
  足さないのは、それが**Objective の状態ではなく Objective の不在**だからである。
  読めなかった (`next_step_state='unavailable'`) との区別は `next_step` 側の軸が
  付ける(§0-8)。Objective Map は「新規作成」の空状態を出す。
* **既存 Purpose / Capability → Journey direct ref は読み書きとも不変**(§7.1)。
* **既存 `feature_drafts` / Feature Map の snapshot lineage は不変**(§1.6)。
* **`cell_goals` を自動移行しない**(§1.4)。
* **既存 Gap を bulk import しない。** 検出元を参照する `product_gap` を作るのは
  常に人間の明示操作である。「全 Gap を一括で Gap テーブルへ流し込む」migration は
  書かない — どの Milestone に属する Gap なのかは機械には決められない。
* **rollback**: この Epic のテーブルを読む既存 consumer は
  `ux_journey_upstream_ref` の 3 kind と Overview の 1 セクションだけである。
  route の登録を外し、Overview の section を落とせば機能全体が無効になる。
  テーブルの DROP は不要かつ非推奨(監査記録が消える)。
  `_migrate_ux_journey_upstream_ref_kinds` は語彙を**広げる**だけなので、
  rollback しても既存行は読める。

---

## 12. テスト方針(#429-#433 が実装する)

* **type parity** — 新しい `Literal` を全部 `models.py` に定義し、
  `test_interview_type_parity.py` の `FINITE_TYPE_NAMES` へ追加する。
  TS union は `apps/dashboard/src/api/types.ts` に手書きで対応させる。
* **cycle rejection** — Objective 親の自己参照 / 2 段 / 3 段の循環、
  Milestone 依存の同上。訪問済み集合を持つ反復であること(深い階層で
  RecursionError にならない)。
* **System isolation** — 同じ `objective_key` / `milestone_key` / `gap_key` /
  `feature_key` を 2 つの System に作れ、互いに見えないこと。cross-System の
  親 / 依存 / link / 参照が 404 になること。
* **append-only** — revision 追加で過去の decision 行が消えないこと、
  `recheck_state` が `stale` になり `objective_state` / `lifecycle` /
  `achievement` は動かないこと。
* **stale digest** — 古い digest を送った決定が 409 になること。
* **forbidden transition** — §4.3 / §5.6 の前提表の外の遷移が 422 になること。
* **no auto success** — Milestone が全部 `met` でも Objective が `achieved` に
  ならないこと。Gap が全部 `resolved` でも Milestone の `achievement` が動かない
  こと。source が `disappeared` / `contradicted` になっても lifecycle が動かない
  こと。trace / experiment / replay を投入しても何も確定しないこと。
* **no weighted score** — API response のどのフィールドにも数値の
  priority / severity / completeness / confidence が現れないこと
  (レスポンス全体を走査するテストで守る)。
* **no LLM fact** — decision / assessment テーブルの `decision_method` が
  `manual` 以外を受け付けないこと(CHECK と API 両方)。
* **source resolver contract** — `source_kind` ごとに fixture を 1 つ持ち、
  `current` / `changed` / `contradicted` / `disappeared` / `unavailable` の
  5 状態すべてを到達可能にする。1 つの resolver が raise しても他が解決される
  こと(partial failure)。
* **no write to existing 正本** — この Epic の全 API を一巡させた後、
  `interview_*` / `purpose_*` / `understanding_*` / `ux_*`(§7.1 の migration を
  除く) / `solution_design*` / `stakeholder_*` / `evolution_node*` / `cell_*` /
  `components` / `probe_points` / `feature_drafts` の行数と内容が変わらないこと。
* **migration 冪等** — `init_db()` を 2 回実行しても
  `_migrate_ux_journey_upstream_ref_kinds` が no-op になり、既存行が保存される
  こと。空 DB でも通ること。
* **Dashboard** — `components/product-objective/model.ts` の pure 関数の
  unit test。desktop / 狭幅 / keyboard / a11y。deep link + reload は
  `apps/dashboard/browser-tests/` の既存ハーネスを再利用する。
* **backward compatibility** — 既存の `test_ux_design.py` / `test_solution_design.py`
  / `test_stakeholder_network.py` / `test_functional_lineage.py` /
  `test_purpose_chain.py` / Overview 系が無変更で通ること(§7.4 の改名で
  Overview のテストだけは 1 行変わる)。

---

## 13. 非目標(Epic 全体)

* 汎用 OKR / KPI / project management / Gantt / roadmap chart
* AI による Objective 優先順位・Milestone 達成・Gap 解消の自動確定
* 全 Gap を一つの severity または completeness score でランキングすること
* Cell Goal / Task ledger を Product Objective へ流用すること
* 既存 Purpose Chain / UX Design Studio / Feature Map / Functional Lineage の
  全面置換
* 既存 Gap 検出ロジックの再実装
* 既存 Gap を本文コピーで bulk import すること
* LLM による Gap 同一性の自動確定
* graph layout の canonical 保存
* Gap 選択による自動修正・自動実装・自動採用・自動 publish
* 本番データの自動優先順位付け
* synthetic test の成功を利用者 Outcome として扱うこと
