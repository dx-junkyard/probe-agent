# AI Discussion UI Adapter (Epic #443) — canonical contract

本書は Epic #443 (sub-issues #444-#449) の正本契約である。この領域に触れる前に
§0 を読むこと。上流の会話・Proposal・音声の契約は
`docs/assistant-discussion.md` (Epic #436) が正本であり、本書はそれを**置き換え
ない**。#436 が「1 つの対象について会話し、変更候補を作り、適用する」を定義した
のに対し、#443 は次の 3 つだけを足す。

1. 画面ごとの `if` 分岐を、**1 つの有限 adapter registry** へ置き換える (#444)。
2. 会話が **未保存フォーム (UI draft)** を、正本と混同せずに参照できるようにする
   (#445)。
3. 変更候補を **フォーム draft へ反映**し、人がフォーム上で直してから既存の
   保存 API で保存する導線を作る (#446)。

そのうえで対象を Vision〜Feature へ広げ (#447)、nested/list な項目まで候補化でき
るようにし (#448)、証拠不足の論点を Joint Understanding へ昇格する (#449)。

---

## §0 境界 — 後から変えるときに必ず守ること

`docs/assistant-discussion.md` §0 の境界はすべてそのまま有効である。本 Epic が
足す境界は次のとおり。

- **adapter は UI state の読み書き境界であって、domain rule を所有しない。**
  adapter が持ってよいのは「どの対象をどう指すか」「どの canonical service を
  読むか」「どの field をフォームのどこへ渡すか」だけ。lifecycle 判定・
  次の操作・validation・確定可否は既存の canonical module のまま。client は
  server が決めた値を再導出しない (#349 / #380 と同じ規律)。
- **canonical facts と未保存 UI draft を絶対に混ぜない。** 別フィールド、別
  provenance、prompt 内でも別セクション。回答も監査も、どちらを根拠にしたかを
  言えなければならない。UI draft は「まだ誰も保存していない文字列」であって、
  System についての事実ではない。
- **`prefill` は保存ではない。** フォームへ値を入れることは canonical row を
  1 行も作らない。保存は人がフォーム上で確認したうえで既存 domain endpoint を
  叩いたときだけ起き、そこが `decision_method: manual` の境界である。
  **Proposal item の status と、実際に保存されたかどうかは別の事実**であり、
  1 つの列に持たせない (#366)。
- **提案の生成・prefill・保存・確定・publish は 5 つの別操作。** どれかが他を
  自動的に起こしてはならない。とくに prefill も apply も、design confirmation /
  achievement / priority / resolved / adopted のどれも動かさない。
- **AI proposal と human decision は別記録。** 提案の著者は reasoning model、
  適用・prefill・保存を決めたのは人。1 行に畳まない (#337 の
  `origin_role` / `producer_kind` / `actor_kind` と同じ 3 軸の考え方)。
- **未対応を暗黙 fallback しない。** `unsupported` (この adapter はその機能を
  持たない) / `unavailable` (持っているが今は読めなかった) /
  `not_applicable` (その対象には構造上存在しない) は 3 つの別の答えであり、
  `stale` / `conflict` / `unknown` / `validation_error` とも丸めない。
- **新しい正本を作らない。** Vision / Purpose / Capability / Stakeholder /
  Need / Objective / Milestone / Gap / Journey / Requirement / Feature /
  Solution Design / Flow / Node / Component / Cell / Outcome の正本は既存の
  まま。この層が持つのは会話・変更候補・prefill 監査・昇格 lineage だけで、
  上流の本文を列へコピーせず、参照 (`target_kind` + 安定 `target_ref` +
  捕捉 digest) を持ち、解決は kind ごとの唯一の resolver に対して**読み取り時**
  に行う。
- **System scope を越えない。** 他 System の thread / proposal / draft /
  finding は 404。
- **未保存 draft を永続化しない** (#445 非目標)。監査に残すのは「draft を
  参照した」という事実と、その形 (form id / field 名 / digest) だけで、
  **値そのものは保存しない**。

---

## §1 DiscussionAdapter registry (#444)

### 1.1 なぜ registry なのか

#436 時点で、1 つの `target_kind` を足すには 6 つの並行した per-kind 表を同時に
直す必要があった。

| 表 | 場所 |
| --- | --- |
| `SCOPE_TARGET_KINDS` | `app/assistant_discussion.py` |
| `_TARGET_RESOLVERS` | `app/assistant_discussion.py` |
| `route_params_for_target` | `app/assistant_discussion.py` |
| `PROPOSAL_TARGET_SCHEMA` | `app/assistant_discussion_proposal.py` |
| `gather_target_context` | `app/assistant_discussion_proposal.py` |
| `_apply_field` / `_apply_relation` | `app/assistant_discussion_proposal.py` |

6 つのうち 1 つを忘れても型検査は通り、テストも「その kind を試していない」限り
緑のままになる。**忘れた場合の壊れ方が、拒否ではなく黙った縮退**であることが問題
である — 例えば resolver だけ足すと、その対象は常に `digest=""` で
`stale` にならない thread になる。

`app/discussion_adapters.py` はこの 6 つを 1 つの `DiscussionAdapter` へまとめ、
`tests/test_discussion_adapter_registry.py` が
「`DISCUSSION_TARGET_KINDS` のすべてに adapter がちょうど 1 つある」ことと
「registry の外に per-kind 分岐が残っていない」ことを直接表明する。

### 1.2 adapter identity

adapter の identity は `target_kind` である。`thread_key` は #436 §1.2 のまま
`screen_id|scope|target_kind|target_ref` で変更しない — 既存 thread の履歴と
stale 判定を壊さないため (#444 受け入れ条件)。

1 つの `target_kind` は複数の `screen_id` から開かれうる。どの画面から開けるかは
adapter の `screen_ids` が持つ。**同じ entity を別画面から開いた thread は別
thread** であり、これは仕様である (§1.6)。

### 1.3 有限 capability

```
DiscussionCapability =
    "read_canonical"            -- canonical facts を context に載せられる
  | "read_ui_draft"             -- 未保存フォーム draft を受け取れる (#445)
  | "propose_fields"            -- field 変更候補を作れる
  | "propose_relations"         -- relation 変更候補を作れる
  | "prefill_form"              -- 候補をフォーム draft へ反映できる (#446)
  | "promote_joint_understanding" -- 仮説を JU へ昇格できる (#449)
```

capability は adapter が実際に持つ登録内容から**導出**する。列にも定数にも
二重に書かない (#337 / #338 / #349 と同じ規律 — 保存した lifecycle 値は記述対象
から drift しうる)。

| capability | 導出条件 |
| --- | --- |
| `read_canonical` | `context_provider is not None` |
| `read_ui_draft` | `ui_draft_forms` が空でない |
| `propose_fields` | `fields` が空でない、または `children` が空でない |
| `propose_relations` | `relations` が空でない |
| `prefill_form` | `ui_draft_forms` が空でなく、かつ `propose_fields` か `propose_relations` |
| `promote_joint_understanding` | `joint_understanding_bridge` が真 |

### 1.4 server 側 `DiscussionAdapter`

`app/discussion_adapters.py` (新規) が唯一の正本。

```python
@dataclass(frozen=True)
class DiscussionAdapter:
    target_kind: str
    scope: str                       # "screen" | "entity" | "element"
    screen_ids: Tuple[str, ...]      # この kind を開ける画面
    label: str                       # 日本語表示名 (単数形)
    resolver: Callable[[int, str], ResolvedTarget]
    context_provider: Optional[Callable[[Connection, int, str], Dict[str, Any]]]
    route_params: Callable[[str], Dict[str, str]]
    fields: Tuple[str, ...]
    relations: Tuple[str, ...]
    children: Tuple[ChildSpec, ...]  # #448。無ければ空
    ui_draft_forms: Tuple[UiDraftFormSpec, ...]  # #445/#446。無ければ空
    field_applier: Optional[FieldApplier]
    relation_applier: Optional[RelationApplier]
    joint_understanding_bridge: bool
```

- `scope` は kind ごとに 1 つに固定する。#436 の `SCOPE_TARGET_KINDS` は
  `scope -> kinds` の逆写像として registry から導出し、手書きの表は消す。
- `resolver` は #436 の resolver をそのまま移設する。**絶対に raise しない**
  (削除済み / 未知 ref / 別 System は `resolution="unresolved"`)。
- `context_provider` は `gather_target_context` の per-kind 分岐を移設したもの。
  `None` は「討議のみ (canonical context を持たない)」。
- 既存 4 画面の 9 kind は挙動を 1 ビットも変えずに移設する。移設が挙動を変えて
  いないことは既存テスト (`test_assistant_discussion_threads.py` /
  `test_assistant_discussion_proposals.py`) が緑のままであることで示す。

### 1.5 Dashboard 側 registry

`src/lib/discussion-adapters.ts` (新規) が唯一の正本。

```ts
export interface DashboardDiscussionAdapter {
  targetKind: DiscussionTargetKind;
  scope: DiscussionScope;
  screenIds: readonly string[];
  label: string;
  /** URL + 画面選択から最も具体的な対象を解決する。null = 対象なし。 */
  resolveFromRoute(screenId: string, params: URLSearchParams): DiscussionCandidate | null;
  /** prefill 先のフォーム。空なら prefill 不可。 */
  forms: readonly UiDraftFormBinding[];
  /** 反映後に無効化する React Query key の prefix。 */
  invalidateKeys(targetRef: string): readonly (readonly unknown[])[];
  /** 対象を画面上で開く URL (navigate 用。execute しない)。 */
  deepLink(targetRef: string): string | null;
}
```

**Assistant Panel 本体に `if (screenId === ...)` を書かない**ことが #444 の
受け入れ条件である。`deriveDiscussionCandidate` の画面別分岐は registry の
`resolveFromRoute` へ移す。

### 1.6 同じ entity を複数画面から開いたとき

`thread_key` に `screen_id` が入っている以上、Overview から開いた Objective の
会話と Objective Map から開いた同じ Objective の会話は**別 thread** になる。
これを統合しない理由は 2 つある。

1. 統合すると既存 4 画面の thread key が変わり、履歴が切れる (#444 受け入れ
   条件に反する)。
2. 画面が違えば手元にある canonical context も UI draft も違う。同じ行に
   まとめると、どちらの文脈で言われたことなのかが読めなくなる。

代わりに、`GET /assistant/discussion-threads?target_kind=&target_ref=` (既存)
で同じ対象の別画面 thread を列挙できることを UI 契約とし、Assistant Panel は
「他の画面での会話 N 件」として提示する。**黙って別の会話を混ぜない。**

### 1.7 fail-closed

- 未登録の `target_kind` → 422 `discussion_target_kind_unregistered`
- `screen_id` が adapter の `screen_ids` に無い → 422
  `discussion_target_screen_mismatch`
- scope 不一致 → 既存の 422 `discussion_target_scope_mismatch`
- capability を持たない操作 → 422 で、`code` は操作ごとに区別する
  (`discussion_prefill_unsupported` / `discussion_ui_draft_unsupported` /
  `discussion_promotion_unsupported`)。**黙って screen thread へ縮退させない**
  (#447 受け入れ条件)。

### 1.8 parity

`tests/test_discussion_contract_parity.py` が次を機械的に照合する。

- server `Literal` (`app/models.py`) ↔ Dashboard union (`src/api/types.ts`) ↔
  共有 JSON Schema (`shared/schemas/assistant_discussion.schema.json`)
- registry の `target_kind` 集合 ↔ `DISCUSSION_TARGET_KINDS`
- server adapter の `fields` / `relations` ↔ Dashboard adapter が prefill
  できる field 集合 (Dashboard 側が知らない field を server が提案できると、
  その item は永久に prefill されない)

既存の `tests/test_interview_type_parity.py` の `FINITE_TYPE_NAMES` 方式に
そろえる。

### 1.9 `purpose_need` の不一致解消

現状:

| 契約 | `origin_kind` | `trigger` |
| --- | --- | --- |
| `app/models.py` | 5 値 (`purpose_need` あり) | 3 値 (`purpose_need` あり) |
| `app/joint_understanding.py` | 5 値 | **2 値** (`purpose_need` なし) |
| `src/api/types.ts` | **4 値** | **2 値** |
| `shared/schemas/joint_understanding.schema.json` | **4 値** | **2 値** |

正解は `app/models.py` 側 (Issue #389 が実際に `origin_kind='purpose_need'` /
`trigger='purpose_need'` の行を書いている) なので、残り 3 契約をそれに合わせる。
`joint_understanding.TRIGGERS` に `purpose_need` を足し、TS union と JSON Schema
の enum を 5 値 / 3 値へ広げる。**狭い側に合わせて server を狭めてはならない** —
既存行が読めなくなる (#427 の「狭めた語彙には upgrade migration が要る」)。

---

## §2 UiDraftContext — 未保存フォームの安全な参照 (#445)

### 2.1 なぜ別 provenance なのか

canonical facts は「保存され、System について確定している事実」である。未保存
フォームの中身は「1 人がいま打ち込んでいる途中の文字列」で、まだ誰の判断でも
ない。これを同じ `screen_data` に混ぜると、アシスタントの回答も監査も、どちらを
根拠にしたか言えなくなる — #366 の「一つの表示語が二つの事実を運ぶ」欠陥の、
prompt 側での再発である。

### 2.2 契約

`POST /assistant/ask` に `ui_draft?: UiDraftContextIn` を足す。client-only の
値であり、**サーバはこれを保存しない**。

```
UiDraftContextIn {
  target_kind: DiscussionTargetKind
  target_ref: str
  form_id: str                      -- adapter の ui_draft_forms に登録済み
  fields: [ UiDraftFieldIn ]        -- 最大 40
  selected_item_ref: str            -- "" は「選択なし」
  active_tab: str
  comparison_target: str
  captured_at: float                -- client 時刻。表示にのみ使う
  local_revision_token: str         -- draft 内容の client 側 digest
}

UiDraftFieldIn {
  field_name: str                   -- form spec の allowlist 内のみ
  value: str                        -- 最大 4000 文字
  dirty: bool
  validation_error: str             -- "" は「エラーなし」
}
```

### 2.3 allowlist と bound

- `field_name` は adapter の `UiDraftFormSpec.fields` に**完全一致**するものだけ。
  外れた名前は 422 `ui_draft_field_unregistered` で**リクエスト全体を拒否**する。
  黙って落とすと、client は送ったつもりのまま回答を読むことになる。
- `target_kind` / `target_ref` が thread の対象と一致しなければ 422
  `ui_draft_target_mismatch`。**別対象の draft を文脈に混ぜない。**
- bound: field 数 ≤ 40、1 field ≤ 4000 文字、全体 ≤ 32KB。超過は 422
  `ui_draft_payload_too_large` (切り詰めない — 切り詰めた draft は、利用者が
  見ている draft ではない)。
- secret: Principle 9 の 2 層 (key 名 + 値の形) を draft にも適用する。
  redaction は LLM へ渡す前。password / token / secret を含む field 名は
  そもそも allowlist に登録しない。
- **DOM scraping 禁止。** adapter が明示的に登録した form の、明示的に登録した
  field だけ。ページ全体の state・非表示データ・他 target の draft は送らない。

### 2.4 prompt での分離

`screen_data` とは別のトップレベルキー `ui_draft` として渡し、prompt では
**「未保存の下書き (まだ保存されていません)」**という明示ラベルの独立セクション
に置く。canonical facts のセクションへ混ぜない。回答が draft を根拠にした場合は
citation の `type: "ui_draft"` で示す。

### 2.5 turn 中の固定

turn 開始時に draft snapshot を取り、その turn の `/assistant/ask` はその
snapshot で答える。途中でフォームが変わっても差し替えない (#436 §4 が音声で
すでに確立した規律を、text にも同じ形で適用する)。text / voice で同じ contract
を使う。

### 2.6 有限状態

```
UiDraftState = "not_provided"        -- client が送らなかった
             | "applied"             -- 参照した
             | "no_unsaved_changes"  -- form はあるが dirty が 1 つも無い
             | "unsupported"         -- adapter が read_ui_draft を持たない
             | "unreadable"          -- client が読めなかったと申告した
```

`no_unsaved_changes` と `not_provided` と `unreadable` は 3 つの別の答えで、
丸めない。応答は `ui_draft_state` と、直前 turn と `local_revision_token` が
変わったかを示す `ui_draft_changed` を返す。`ui_draft_changed` が真なら
`recheck_required` も真になる — 前回の回答は、いまの下書きについてのものでは
ない。

### 2.7 監査に残すもの / 残さないもの

`assistant_discussion_turn` に足すのは 3 列だけ。

| 列 | 意味 |
| --- | --- |
| `ui_draft_state` | §2.6 の有限値 |
| `ui_draft_form_id` | どのフォームの下書きか |
| `ui_draft_digest` | client の `local_revision_token` |

**値そのものは保存しない** (#445 非目標)。監査が答えるべき問いは「この回答は
未保存の下書きを見ていたか」であって、「その下書きに何と書いてあったか」では
ない。後者を保存すると、保存されていないはずの内容が DB に残る。

---

## §3 Proposal review UI と form draft 反映 (#446)

### 3.1 二つの経路を持つ理由

#436 は `POST /assistant/discussion-proposals/{id}/apply` を持ち、これは選択した
item を既存 domain service 経由で直接 revision にする。この API は互換のため
**残す**が、**Dashboard の標準導線は prefill-first にする**。

理由: apply は revision を 1 版足す。会話から出てきた文言をそのまま版にすると、
「人が読んで直す」機会が保存の後になる。prefill なら、人はフォーム上で直して
から保存でき、保存された版は最初から人が確認したものになる。

### 3.2 prefill の形

`proposalToDraft(adapter, proposal, itemIds)` → `FormDraftPatch`:

```ts
interface FormDraftPatch {
  patchToken: string;               // 二重反映を防ぐ冪等トークン
  targetKind: DiscussionTargetKind;
  targetRef: string;
  formId: string;
  fields: { fieldName: string; value: string; rationale: string }[];
  childOps: {
    childKind: string; childKey: string;
    intent: "add" | "update" | "remove";
    order: number | null;
    fields: { fieldName: string; value: string }[];
  }[];
  relations: { relationKind: string; targetKind: string; targetRef: string; note: string }[];
}
```

配送は `src/lib/form-draft-inbox.ts` の CustomEvent (`assistant-control.ts` の
`OPEN_ASSISTANT_EVENT` と同じ形)。フォーム側は
`useFormDraftInbox(formId, targetRef)` で購読する。**target が一致しない patch は
受け取らない** — 別 Requirement の候補を、開いているフォームへ入れない。

### 3.3 dirty との衝突

フォームの field が dirty で、かつ現在値が提案値と異なるとき、**黙って上書き
しない**。field 単位の衝突プレビューを出し、`このまま` / `提案で置き換える` を
人が選ぶ。選ばれなかった field は元のまま残る。

### 3.4 prefill の監査 (item status とは別)

```
assistant_discussion_proposal_prefill(
  id, system_id, proposal_id, item_id, form_id, patch_token,
  decision_method 'manual', created_by, created_at,
  UNIQUE (proposal_id, patch_token, item_id))
```

**item の `status` は `proposed` のまま**である。prefill は意図であって完了では
なく、保存されたかどうかはこの層が観測できる事実ではない (#412 の
「記録は昇格ではない」と同じ)。detail 応答は item ごとに `prefill_count` /
`last_prefilled_at` を返す。reload 後も Proposal と prefill 監査が読めることが
#446 の受け入れ条件である。

`patch_token` の UNIQUE が二重反映を防ぐ。

### 3.5 適用不可の理由表示

item の `eligibility` (`forbidden` / `stale` / `conflict` / `appliable`) に加え、
prefill 可否は adapter capability から決まる。両方を別々に表示する —
「この対象は prefill に対応していない」と「この item は stale で反映できない」は
別の答えであり、開発者の次の操作も違う。

### 3.6 反映後

`adapter.invalidateKeys(targetRef)` で対象 query を、thread/proposal query を
それぞれ無効化する。対象フォームへ navigate + focus する。**navigate であって
execute ではない** (#358 / #427 の CTA 規則)。

---

## §4 対象拡張 (#447)

### 4.1 追加する target_kind

| target_kind | scope | target_ref | digest source | canonical module |
| --- | --- | --- | --- | --- |
| `purpose_element` | `element` | Purpose element id | `purpose_chain.element_digest` | `app/purpose_chain.py` |
| `purpose_relation` | `element` | `relation_id(kind, source, target)` | relation の正規化 digest | `app/purpose_chain.py` |
| `stakeholder` | `entity` | `stakeholder_key` | 現行 revision の `content_digest` | `app/stakeholder_value_network.py` |
| `stakeholder_need` | `entity` | `need_key` | 現行 revision の `content_digest` | `app/stakeholder_value_network.py` |
| `product_objective` | `entity` | `objective_key` | `_objective_current_digest` | `app/product_objective.py` |
| `product_milestone` | `entity` | `milestone_key` | `_milestone_current_digest` | `app/product_objective.py` |
| `product_gap` | `entity` | `gap_key` | `_gap_current_digest` | `app/product_objective.py` |
| `product_feature` | `entity` | `feature_key` | 現行 revision の `content_digest` | `app/product_feature.py` |

追加する `screen_id`: `objective-map` / `stakeholder-value-network` /
`capability-map`。既存 4 画面 (`overview` / `interview` / `ux-design-studio` /
`journey-blueprint`) の挙動は変えない。

### 4.2 守ること

- 各 context provider は **既存の canonical service / projection を読む**。
  複製正本を作らない。
- upstream / downstream link と、その未解決・stale 状態を context に載せる —
  「この Gap はどの Milestone に属し、どの Journey へつながっているか」は
  回答の根拠になる。ただし **本文をコピーせず参照と digest で持つ**。
- 対象が削除・supersede・snapshot 更新された場合は #436 §1.3 の first match
  (`unresolvable` → `not_tracked` → `stale` → `current`) をそのまま使う。
- 別 System / 同名 target / 削除済み target を混同しない。
- Purpose element の id は `purpose_chain` が既に定義している安定 id を使い、
  ここで新しい id を作らない。

---

## §5 nested / list な変更候補 (#448)

### 5.1 ChildSpec

```python
@dataclass(frozen=True)
class ChildSpec:
    child_kind: str        # "acceptance_criterion" | "journey_step" | "solution_option"
    key_field: str         # "criterion_key" / "step_key" / "option_key"
    order_field: str       # "criterion_order" / "step_order" / "option_order"
    fields: Tuple[str, ...]
```

proposal item に 3 列を足す。

| 列 | 意味 |
| --- | --- |
| `child_kind` | どの子コレクションか。空なら親自身への変更 |
| `child_key` | 安定キー。行 id ではない |
| `child_intent` | `add` / `update` / `remove` |
| `child_order` | 並び替えの意図。`NULL` は「順序は変えない」 |

**順序変更と本文変更を区別する。** `child_order` だけが変わった item と、
`fields` が変わった item は別の item であり、片方だけを採ることができる。

### 5.2 人間判断軸は registry に入れない

`priority_band` / `achievement` / `lifecycle` / `design_status` /
`option_status` / `resolved` / `adopted` は **`fields` にも `relations` にも
登録しない**。登録しなければ LLM は提案できず、prefill 先も存在しない —
構造で禁じる (#427 が Gap に severity 列を作らなかったのと同じ)。

### 5.3 二重 validation

生成時 (`generate_proposal`) と反映時 (`proposalToDraft` / `apply_items`) の
両方で registry 照合する。間に人間の編集と時間の経過が入るので、生成時に有効
だった child key が反映時には消えていることがある (#412 §7.1.3 と同じ理由)。

### 5.4 未知の値は全体を拒否する

LLM が未知の `field_name` / `relation_kind` / `child_kind` / `child_key` を
返したら、**その item だけを落とさず、提案全体を失敗させる**。1 つ捏造された
field を含む提案は「部分的に正しい提案」ではない (#436 §2.1 が既に確立した
規律を child にも広げる)。

### 5.5 Proposal に変換しないもの

未解決質問・仮説・反証条件は field change へ**無理に変換しない**。それらは
`unresolved_questions` / `assumptions` / §6 の hypothesis として別の型のまま
保持する。答えの出ていない問いを「提案された値」にすると、提案を受け入れた
だけで問いが消える。

---

## §6 Joint Understanding への昇格 (#449)

### 6.1 hypothesis は field change ではない

Proposal に hypothesis を独立した型として足す。

```
assistant_discussion_proposal_hypothesis(
  id, system_id, proposal_id, statement,
  competing_explanations_json, refutation_conditions_json,
  next_investigation, evidence_refs_json, uncertainty,
  status ('proposed'|'promoted'|'rejected'),
  created_at, schema_version)
```

`competing_explanations` か `refutation_conditions` が空の hypothesis は
**生成時にも昇格時にも拒否する** (#449 受け入れ条件)。反証条件の無い仮説は
仮説ではなく、ただの主張である。

### 6.2 bridge

`POST /assistant/discussion-proposals/{id}/hypotheses/{hid}/promote` は、
Joint Understanding session を `origin_kind='discussion'` /
`trigger='discussion_promotion'` で開く。

- **6 つ目の origin を足す**理由: #337 の premise 契約は origin ごとの content
  hash を要求する。既存 4 origin のどれかに偽装すると、Journey についての会話が
  「Q&A の premise」を名乗ることになり、premise 評価が意味を失う。
  `discussion` origin の content hash は
  `target_kind` + `target_ref` + 昇格時の `captured_target_digest` で、
  `premise_commit_sha` は #337 のまま。
- 昇格は **利用者の明示操作** (`decision_method: manual`)。元の domain item の
  回答・decision・status は 1 つも変えない (#329 の境界)。
- lineage:

```
assistant_discussion_hypothesis_promotion(
  id, system_id, hypothesis_id, thread_id,
  first_turn_number, last_turn_number,
  captured_target_kind, captured_target_ref, captured_target_digest,
  joint_understanding_session_id,
  decision_method 'manual', created_by, created_at)
```

元 thread / turn 範囲 / target premise から調査 finding・outcome まで辿れる。

### 6.3 還流

`GET /assistant/discussion-threads/{id}/joint-understanding` は昇格済み session
とその現在の findings / outcome を返す。守ること:

- **provisional を confirmed fact として返さない。** `hypothesis_adopted` は
  #337 が明示的に provisional と定めており、Discussion 側でも
  `outcome_is_provisional` をそのまま運ぶ。confirmed point へ昇格させない。
- premise が `current` のときだけ、その finding を Discussion の最新 context へ
  参照として載せる。`stale` / `missing` / `invalid` は再確認を要求する
  (#337 の verdict をそのまま読む。ここで再定義しない)。
- Discussion と Joint Understanding のテーブルは**統合しない** (#449 非目標)。

---

## §7 責務境界 (#436 / #33 / #394 / #401)

| 層 | 所有するもの | 所有しないもの |
| --- | --- | --- |
| #436 会話層 | thread / turn / proposal / help registry / voice | 対象の解決規則、prefill、UI draft |
| #443 adapter 層 | target ↔ 画面 ↔ フォームの写像、UI draft 境界、prefill 監査、昇格 lineage | domain rule、lifecycle、validation、確定判断 |
| #33 改善方針 workspace | 改善方針の検討記録 | Discussion の thread identity |
| #394 / #401 Evolution Node | Node の成熟度・固定化・監視 | 会話・提案 |

adapter 層は #394 の maturity / #304 の Cell Improvement / SDK policy mode /
Dashboard workflow phase のどれも読まず、書かない。navigation contract として
#401 の統合先画面が決まったときは、adapter の `screen_ids` と `deepLink` を
足すだけで済むようにしておく。

---

## §8 実装順と rollback

| Phase | Issue | 主な追加物 |
| --- | --- | --- |
| 1 | #444 | `app/discussion_adapters.py` / `src/lib/discussion-adapters.ts` / parity テスト / `purpose_need` 不一致解消 |
| 2 | #445 | `UiDraftContextIn` / `/assistant/ask` 拡張 / turn の 3 列 / client の draft 収集 |
| 3 | #446 | Proposal review UI / `form-draft-inbox` / prefill 監査テーブル |
| 4 | #447 | 8 個の target_kind と 3 画面の追加 |
| 5 | #448 | ChildSpec / proposal item の 4 列 / Requirement 受入条件と Product Feature |
| 6 | #449 | hypothesis 型 / JU bridge / 還流 / E2E |

**互換性 / rollback。** 各 Phase は追加のみで、既存 API のレスポンスから
フィールドを削らない。`ui_draft` を送らない client は #436 と同じ挙動になり、
prefill を使わない client は既存の `apply` を使い続けられる。DB 変更はすべて
additive (新テーブルと NULL 許容列) で、旧行は
`ui_draft_state = NULL` / `child_kind = ''` として読める — **旧行を「満たした」
ことにはしない** (#337 の互換性規則)。ある Phase を戻す場合は、その Phase が
足したテーブル・列・registry entry を使う UI を外せば、残りは動き続ける。
