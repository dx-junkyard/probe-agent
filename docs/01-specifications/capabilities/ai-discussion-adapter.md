# AI Discussion UI Adapter (Epic #443) — canonical contract

## 実装状況（2026-09-06 監査）

本書は目標契約を含み、記載された機能がすべて実装済みという意味ではない。
PR #450 の Phase 1 は旧9 target kind / 4画面の registry と契約 parity を実装した。
監査では、Proposal apply が登録 handler を迂回する不具合と、step/lane の
deep link が対象 identity を失う不具合を修正した。apply は現在の registry の
allowlist と handler を全選択 item について検査してから、登録 handler を呼ぶ。
未登録・handler 欠落時は canonical revision / relation / item status を変更しない。

| 元 issue | 実装と残件 | 引き継ぎ先 |
| --- | --- | --- |
| #444 | registry / parity は実装。操作結果の3状態と、実行handlerに基づくprefill capabilityの判定は実装済み (#456) | - |
| #445 | Phase 2 (`fea4fe1`) を統合。draft の保存防止・System分離・変更警告を修正。実フォームのvalidation診断連携(§2.8)は #451 で実装済み | - |
| #446 | Proposal review UI / prefill / 保存との接続は未実装 | #452 |
| #447 | 追加8 kind と live selection / context は未実装 | #453 |
| #448 | nested item / Acceptance Criteria / Feature Proposal は未実装 | #454 |
| #449 | 仮説の JU 昇格・還流と代表 E2E は未実装。Issue #461 が所属・premise 基盤を先行実装済み: `joint_understanding_session.owner_scope`/`discussion_thread_id`（既存 `session_id` は interview 所属時のみ必須）、`origin_kind='discussion'`、`app/joint_premise.py` の discussion origin provider registry（`register_discussion_origin_provider`）と依存参照 manifest（`normalize_premise_manifest` / `compute_premise_manifest_digest` / `EMPTY_DEPENDENCY_MANIFEST_DIGEST`、`evaluate_joint_premise` が root 不変でも依存更新で stale と判定）。実際の hypothesis テーブル・昇格 endpoint は未実装のまま | #455 |

元 issue は実装完了と残件移管を区別して整理する。prefill、JU bridge
は拡張用定義だけであり、代表 E2E や screen reader / narrow viewport の
実利用検証が完了したとは扱わない。既存の backend direct apply は利用可能だが、
フォームへの prefill ではない。Blueprint の表示選択・focus との接続も #452 で扱う。

検証: server の registry / parity / thread / Proposal / Assistant / UI draft /
DB lock / interview parity は119 passed、DB lock の3ケースはreasoning呼び出し前に
fail-closedするためskip。Dashboardの関連7ファイルは62 passed。
追加実装を取り込む前の既存JU関連5ファイルは106 passed。
実LLM・音声機器・screen readerによるdogfoodingの完了証明ではない。

本書は Epic #443 (sub-issues #444-#449) の正本契約である。この領域に触れる前に
§0 を読むこと。上流の会話・Proposal・音声の契約は
`docs/01-specifications/capabilities/assistant-discussion.md` (Epic #436) が正本であり、本書はそれを**置き換え
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

`docs/01-specifications/capabilities/assistant-discussion.md` §0 の境界はすべてそのまま有効である。本 Epic が
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
登録された target / scope / proposal schema の整合を直接表明する。
既存の domain service を呼ぶ `_apply_*` の分岐は登録 handler の内側に残るが、
適用可否と handler の選択は registry を経由する。

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
| `prefill_form` | `ui_draft_forms` が空でなく、提案可能で、`prefill_handler_id` が非 `None` |
| `promote_joint_understanding` | `joint_understanding_bridge` が真 |

`prefill_form` は `DiscussionAdapter.prefill_handler_id`(versioned な実装 handler の id)が
非 `None` の場合のみ true になる(Issue #456)。`ui_draft_forms` の宣言は「反映先のフォームが
存在する」宣言にすぎず、配送できる handler の存在を意味しない — 2026-09 時点で
`prefill_handler_id` は全 target_kind で `None` のままであり、これが正しい完了状態である
(実 handler は #452 が最初の接続を行う)。Dashboard 側は `src/lib/discussion-adapters.ts` の
各 adapter が持つ `prefillHandlerId` で、`tests/test_discussion_contract_parity.py` の
`test_prefill_handler_id_parity_between_server_and_dashboard` が server/Dashboard 双方の
宣言一致(現状は両方とも空)を機械的に固定する。**client の自己申告だけでは有効化しない** —
`capabilities_for` は server 側の `prefill_handler_id` だけを読み、リクエストのどのフィールド
からも導出しない。`tests/test_discussion_adapter_registry.py` の
`test_prefill_form_becomes_true_only_once_a_handler_is_registered` が、
`dataclasses.replace` で作った fixture 接続時にのみ true になること(実登録は false のまま)
を証明する。

対象が対応済みでも一時的に配送できなければ操作結果は `unavailable`。未対応と一時失敗を
同一の capability boolean へ畳まない — `app/discussion_adapters.resolve_prefill_operation_state`
が `not_applicable`(screen scope の対象 / 未登録 form_id)/ `unsupported`(forms 自体が無い /
handler 未登録)/ `available` を個別に返す(§9 参照)。

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
    prefill_handler_id: Optional[str]  # #456。実装済み handler の id。無ければ None
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
  /** #456。実装済み handler の id。無ければ null (server 側 parity 必須)。 */
  prefillHandlerId: string | null;
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
  (`ui_draft_unsupported` / `prefill_unsupported` / `promotion_unsupported`)。
  code は操作の family 接頭辞に合わせる — 同じ操作の他の拒否コード
  (`ui_draft_target_mismatch` / `ui_draft_field_unregistered` …) と並べて
  読めることのほうが、全部に `discussion_` を付ける一貫性より有用である。
  **黙って screen thread へ縮退させない** (#447 受け入れ条件)。

`GET /assistant/discussion-threads/{id}` および thread 作成応答は
`capabilities: DiscussionCapability[]` を返す (Issue #456)。**現在の registry
から毎回読み直し、thread 行には保存しない** — narrowed/widened された registry
は次の読み取りへ即座に反映される。`target_state` (freshness) とは別 field で
あり、混ぜない (#366): `current` な thread が `prefill_form` を持たないことも、
`stale` な thread が `prefill_form` を持つことも、どちらも起こりうる。

Issue #456 はさらに、discussion-adapter の READ 操作 (canonical context の
取得、prefill readiness の判定) 自体の成否を、facts と別 field で報告する
finite 契約を導入する (`app/models.py` の `DiscussionOperationResult`:
`available` / `unsupported` / `unavailable` / `not_applicable`)。

- **割り当て規則 (first-match)**: 未登録 adapter/handler は `unsupported`
  (現在の catalog の構造的な欠落)、登録済み handler の一時的な読み取り失敗は
  `unavailable`、その種類の対象には構造上決して存在しえない操作は
  `not_applicable`。対象削除/別 System は既存の resolver/404 契約
  (`DiscussionTargetState.unresolvable` や 404) のままで、この 4 値へ
  再分類しない。
- **`app/discussion_adapters.gather_context`** が canonical context 取得の
  正本実装。旧 `assistant_discussion_proposal.gather_target_context` は
  「adapter 不在・provider 不在・provider 例外」の 3 つを区別なく `{}` へ
  丸めていたが、いまはこの関数へ委譲し、`{unsupported, unavailable,
  available}` を個別に返した上で `.facts` だけを取り出す (LLM プロンプトの
  facts 欄へ operation_state を混ぜ込まない -- 別レイヤーで読みたい呼び出し元は
  `gather_context` を直接呼ぶ)。
- **`app/discussion_adapters.resolve_prefill_operation_state(adapter,
  form_id=None)`** が prefill readiness の正本判定
  (`scope=="screen"` → `not_applicable`、`ui_draft_forms` 無し →
  `unsupported`、`form_id` が対象の登録フォームでない → `not_applicable`
  (`prefill_form_unregistered`)、`prefill_handler_id` 未登録 → `unsupported`
  (`prefill_handler_not_registered`)、それ以外 → `available`)。フォームが
  **mount されているか** はこの判定条件に含まれない (#456 決定事項: 「mount
  状態を capability 条件にしない」) — 一時的な配送失敗は #452 が扱う
  `unavailable` の領域である。
- **`app/assistant_discussion_context.build_screen_discussion_context`** の
  各画面 provider は try/except で保護されており、`_overview_context` /
  `_interview_context` / ... が例外を送出しても `/assistant/ask` 全体が
  500 で壊れることはなく、`ScreenDiscussionContext(operation_state=
  "unavailable")` へ縮退する (§1.3 の「診断取得失敗で会話全体を不要に壊さ
  ない」の実装)。未登録の `screen_id` は従来どおり `None` (画面レベルの
  `unsupported` に相当し、この変更の対象ではない)。
- **operation_state は server の外まで届く (レビュー対応)。** 最初の実装は
  `TargetContextResult` / `ScreenDiscussionContext` の operation_state を
  内部で導出するだけで、`/assistant/ask` の応答にも LLM prompt にも届いて
  いなかった — 「取得に失敗した空 context」と「成功して空だった context」が
  呼び出し側から区別できないままで、#456 が直そうとした欠陥が 1 階層外側に
  残っていた。
  - `AssistantAskOut.screen_context_state` (`DiscussionOperationResult`) /
    `.screen_context_reason` が毎回の応答に乗る。`discussion is None`
    (画面が discussion 非対応) は `unsupported`、`ScreenDiscussionContext.
    operation_state` をそのまま運ぶ場合は `available` / `unavailable`。
    `facts` (`screen_data`) とは別 field で、`target_state` (freshness) とも
    混ぜない。
  - prompt 側は `app/assistant.py` の `ContextPack.screen_data_state` /
    `.screen_data_reason` を経由し、`to_llm_payload()` が `unavailable` の
    ときだけ `screen_context_state` という**別のトップレベル key**を足す
    (`screen_data` の中には入れない)。`unsupported`(この画面は元々
    canonical context を持たない、既存の非対応画面の shape そのもの)や
    `available` では何も足さない — 既存 client の prompt 形は不変。
  - `assistant_discussion_proposal.generate_proposal` は
    `context_operation_state` / `context_reason` を受け取り、
    `unavailable` のときは **LLM を一度も呼ばずに** `error_kind=
    "context_unavailable"` で fail-closed する (503
    `discussion_context_unavailable`、`reasoning_unavailable` とは別 code)。
    根拠を読めなかった上で生成された提案は「根拠があるように見えて実は
    無い」提案になるため。`unsupported`(`screen` / `interview_session` /
    `overview_finding` のように元々 canonical context を持たない kind)は
    fail-closed の対象外 — これらの kind は #456 以前から会話 turn だけを
    根拠に提案してきており、それは失敗ではなく通常運用のため。
    `routes/assistant.py` の `create_discussion_proposal` は
    `assistant_discussion_proposal.gather_target_context` (facts のみを
    返す互換 shim) ではなく `discussion_adapters.gather_context` を直接
    呼び、`TargetContextResult` 全体を `generate_proposal` へ渡す。
  - 該当テストは `tests/test_discussion_operation_result.py` の
    `TestScreenContextStateReachesTheWire` (「失敗したが空」と「成功したが
    空」が応答上で区別できることを直接表明) と
    `TestProposalGenerationFailsClosedOnUnavailableContext`。
- Dashboard 側 (`src/lib/discussion-adapters.ts`) は
  `classifyDiscussionError(err)` が §1.7 の 422 code を
  `DiscussionOperationResult` + 日本語メッセージ + `retryable` へ変換する。
  `unsupported` / `not_applicable` と判定された code は `retryable: false`
  (再試行しても解決しない理由を示す) で、未知の code / ネットワーク由来の
  失敗は `unavailable` + `retryable: true`。`components/assistant-panel.tsx`
  のエラー表示 (`data-testid="assistant-error"`) がこれを使い、retryable な
  場合のみ「再試行」ボタン (`assistant-error-retry`) を出す。
  **これは `screen_context_state` とは別軸である**: `classifyDiscussionError`
  は「リクエスト自体が失敗した (例外/HTTPエラー)」ことの分類、
  `screen_context_state` は「200 で成功した応答の中の、この画面の canonical
  context 取得状態」で、`AnswerMessage` コンポーネントが個別に表示する
  (`assistant-screen-context-unsupported` / `assistant-screen-context-
  unavailable` + `assistant-screen-context-retry`)。`unsupported` は
  「他の画面への移動」を示さない — discussion-adapter の 422 (対象が別画面に
  ある) と異なり、画面自身の canonical context 能力には移動先の別画面が
  存在しないため、理由の提示のみに留める。`unavailable` は同じ質問を
  再送する「再試行」ボタンを出す。

### 1.8 parity

`tests/test_discussion_contract_parity.py` が次を機械的に照合する。

- server `Literal` (`app/models.py`) ↔ Dashboard union (`src/api/types.ts`) ↔
  共有 JSON Schema (`shared/schemas/assistant_discussion.schema.json`)
  (`DiscussionOperationResult` を含む、Issue #456)
- registry の `target_kind` 集合 ↔ `DISCUSSION_TARGET_KINDS`
- server adapter の `fields` / `relations` ↔ Dashboard adapter が prefill
  できる field 集合 (Dashboard 側が知らない field を server が提案できると、
  その item は永久に prefill されない)
- server adapter の `prefill_handler_id` ↔ Dashboard adapter の
  `prefillHandlerId` (Issue #456)。現状は双方とも全 kind で未登録 (`None` /
  `null`) であることも `test_prefill_handler_id_parity_between_server_and_
  dashboard` が固定する — 片方だけが handler を宣言した状態を、実装が半分
  しか繋がっていないまま出荷させない。

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
  readable: bool                    -- 既定 true。false は §2.6 の `unreadable`
}

`readable=false` は「この対象のフォームは開いているが、client がその状態を
読めなかった」であり、**`fields` を伴ってはならない** (422
`ui_draft_unreadable_with_fields`)。空の `fields` から推論せず独立した wire
field にしてあるのは、空の `fields` が既に `no_unsaved_changes` の形だから
である。

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
  見ている draft ではない)。「全体」は draft 由来の文字列の UTF-8 byte 合計で
  あって JSON 直列化後のサイズではない — 数えているのは利用者が打ち込んだ
  内容の量であって、構造上の overhead ではない。3 つのどれに触れても同じ code
  を返す (client が知りたいのは拒否されたことであって、どのカウンタかでは
  ない)。
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
応答時に client の現在の snapshot token とも照合し、turn 中の変更は再確認の
案内を表示する。System / user 切替時はフォーム・registry・Assistantをまとめて
再マウントし、同じtarget_refでも前のSystemの下書きを引き継がない。

### 2.6 有限状態

```
UiDraftState = "not_provided"        -- client が送らなかった
             | "applied"             -- 参照した
             | "no_unsaved_changes"  -- form はあるが dirty が 1 つも無い
             | "unsupported"         -- adapter が read_ui_draft を持たない
             | "unreadable"          -- client が読めなかったと申告した
```

`no_unsaved_changes` と `not_provided` と `unreadable` は 3 つの別の答えで、
丸めない。**5 値すべてが到達可能でなければならない。** `unreadable` を
`not_provided` へ畳むと、「フォームは開いているが読めなかった」ときに
「何も編集していない」と答えることになり、利用者が見ていない画面を説明する
ことになる。client は登録済みフォームの getter が throw したときに
`readable=false` を送る。応答は `ui_draft_state` と、直前 turn と server が導出した
draft digest が変わったかを示す `ui_draft_changed` を返す。`ui_draft_changed` が真なら
`recheck_required` も真になる — 前回の回答は、いまの下書きについてのものでは
ない。

### 2.7 監査に残すもの / 残さないもの

`assistant_discussion_turn` に足すのは 3 列だけ。

| 列 | 意味 |
| --- | --- |
| `ui_draft_state` | §2.6 の有限値 |
| `ui_draft_form_id` | どのフォームの下書きか |
| `ui_draft_digest` | server が draft 内容から導出した一方向 digest |

**値そのものは保存しない** (#445 非目標)。監査が答えるべき問いは「この回答は
未保存の下書きを見ていたか」であって、「その下書きに何と書いてあったか」では
ない。後者を保存すると、保存されていないはずの内容が DB に残る。

client の `local_revision_token` は非信頼入力であり、そのまま監査へ保存しない。
フォーム値の JSON を token として送る client があっても、server が検証済み
draft 内容から一方向 digest を作る。changed 判定もこの digest を比較する。
対象・form・値・dirty・validation・readable・選択情報を digest に含め、
captured timestamp と client token 自体は除外する。client は内容変更時だけ変わる
短い opaque token を送り、フォーム値の JSON を送らない。
LLM には field 値と別に dirty / validation error / readable を明示し、
validation の自由文字列にも既存の redaction を適用する。

draft を含む prompt への回答は値を引用・言い換えする可能性があるため、
現在の応答としてのみ表示し、永続 assistant turn には固定の案内文を記録する。
reload 後は監査情報と案内文を表示し、回答本文を復元しない。利用者自身が明示的に
送った質問は通常どおり保存する。過去の draft 参照 turn は次の LLM context へ
自動継承しない。これは digest 列だけを安全にしても回答本文から未保存値が
永続化することを防ぐための境界である。

`ui_draft_state` が `NULL` の行は **4 つ目の別の意味**である —「この server は
それを記録できなかった」(#445 以前の行) であって `not_provided` (client が
明示的に送らなかった) ではない。Principle 9 の `traces.redaction_json` の
`NULL` と同じ規律で、丸めない。

---

### 2.8 実フォームへの validation error 接続 (#451)

§2.2/§2.6/§2.7 は「draft を AI へどう見せるか」を定義した。ここまでは
`UiDraftFieldIn.validation_error` を Journey / Requirement / Solution Design
の実フォームが実際に埋めることを前提にしていたが、#445 実装時点ではどの
フォームも `validation_error: ""` を固定で送っていた(保存失敗は toast の
みで、draft へは何も伝わらない)。#451 はこの欠落を接続する。

#### 2.8.1 wire 契約: code / field_path / section / message

`POST /ux-design/...` / `POST /solution-designs/...` の書き込みが返す
422/404/409 は、既存の `{code, message}` に **`field_path` / `section`**
を必ず加える(4 キーとも常に存在し、無関係なキーが省略されることはない)。

```
{
  "code": string,          -- 既存の finite reject code (§1.7 等)
  "message": string,       -- 人間向け日本語文言 (redaction 済み)
  "field_path": string,    -- "" = 特定の field なし
  "section": string        -- "" = 特定の section なし
}
```

`field_path`/`section` は **有限 code からの構造的な写像**であり、
`message` の文言から推測しない (Principle 6)。`routes/ux_design.py` /
`routes/solution_design.py` それぞれが持つ `_FIELD_PATH_BY_CODE` がその
唯一の写像表で、表にない code は `("", "")` を返す(「未対応診断」も同じ
形で表現され、フォーム全体のエラーとして扱われる — §2.8.7)。`message` に
developer 入力値を埋め込む箇所(重複した `step_key` の値など)は埋め込み前
に Principle 9 の redaction を通す(`_redact_offender`。
`ui_draft_context._build_redacted_payload._redact_meta` と同じ
`trace_redaction` の再利用で、二重実装しない)。

`src/api/client.ts` の `ApiError` は `fieldPath: string` /
`section: string` を持つ。サーバが送らなかった場合(旧サーバ、または本当に
field を持たない失敗)は `undefined` ではなく `""` を格納する — `""` 自体が
「特定の field なし」の正規値であり、区別すべき第三の状態はない。

#### 2.8.2 未知 field_path はフォーム全体のエラー

`field_path` は **そのフォームの `knownFields`(実際にレンダリングして
いる入力欄の名前の集合)に対してのみ**解決する。この集合は
`UiDraftFormSpec.fields`(AI へ送る draft の allowlist)と必ずしも同じでは
ない — 例えば Journey revision フォームは `step_key` を編集できる
`<Input>` を持つが、`step_key` は `ux_journey.revision` の draft フィールド
(title/beneficiary/usage_context/entry_trigger/value_arrival/summary)には
含まれない。そのため `journey_step_key_duplicated`
(`field_path="step_key"`)はこのフォームの `knownFields` に含まれず、
**フォーム全体のエラー**として扱われ、`section`(`"steps"`)を使って画面上
の該当セクション見出しの近くに表示する。`field_path` がそもそも `""` の
code(例: `out_of_scope_requirement_not_verifiable`)も同じ経路を通る。
**文言から field を推測することは決してしない** — 一致しなければ機械的に
フォーム全体のエラーになる。

#### 2.8.3 状態: idle / validating / invalid と readable / dirty

2 つの軸は独立である。

| 軸 | 値 | 意味 |
| --- | --- | --- |
| 保存 validation の lifecycle | `idle` / `validating` / `invalid` | 直近の保存試行が無い/実行中/拒否された |
| フィールドの入力状態 (§2.2 の既存契約) | `readable` / `dirty` | draft 全体を読めたか / このフィールドが読み込み時の値から変わっているか |

`idle`/`validating`/`invalid` は `lib/ui-draft.tsx` の `useFormValidation`
フックが保持する **client-only** の状態で、`UiDraftContextIn` の wire には
含まれない — draft 自体は「いま何が入力されているか」であり、「直前の保存が
どうなったか」は別の事実だからである(#366 と同じ「一語二事実」の回避)。
`useUiDraftSource` へ渡す `fields` の `validationError` は、この
`useFormValidation` の `fieldErrors` から該当 field 名を引いた文字列を
そのまま使う(§2.2 の wire 定義どおり、"" はエラーなし)。

#### 2.8.4 編集による解除と未変更 field の保持

`useFormValidation` の `fieldErrors` は field 名をキーとする map で、
`resolveError` は名指しされた field(または未知 field_path の場合は
`formError`)だけを更新し、**他の field の診断はそのまま残す**。developer
がエラーの出た field を編集した瞬間、呼び出し側は `clearField(fieldName)`
を呼んで**その field だけ**の診断を消す(「解除」を採用する — 値が変わった
時点で古い診断はいまの内容について何も言っておらず、表示を残す「再検証待ち」
より安全側に倒れる)。編集していない field の診断は保持されたままになる。

#### 2.8.5 遅延応答の bind

`begin()` は保存試行ごとに増分 token を発行し、状態を `validating` にする。
`resolveError(token, error, knownFields)` / `resolveSuccess(token)` は、
渡された token が **その hook インスタンスの最新の token と一致する場合の
み**状態を書き換える — 一致しなければ無条件に無視する。これは 2 つの意味で
「遅延応答が新しい draft を汚さない」を保証する:

1. 同じ対象へ 2 回連続で保存した場合、古い方の応答が後から届いても新しい
   試行の結果を上書きしない(再試行の安全性)。
2. 別の対象へ切り替えた場合、Journey/Requirement/Solution Design 各画面は
   `key={selectedKey}` で detail コンポーネントごと再マウントする既存の
   idiom(`JourneyDetail`/`RequirementDetail`/`SolutionDesignDetail`)を
   使っているため、`useFormValidation` の state は対象ごとに完全に独立した
   インスタンスになる。token 照合はその上に載る防御的な二重の保証であり、
   何らかの理由で同じ hook インスタンスが複数の対象をまたいで使い回されて
   も、遅延応答が新しい対象の diagnostic を汚すことはない。

#### 2.8.6 redaction と 32KB budget

`validation_error` は `UiDraftFieldIn` の一部として既に redaction
(`ui_draft_context._build_redacted_payload` の `_redact_meta`)と 32KB
budget(`UiDraftContextIn.validate_ui_draft_bounds`)の対象である — 診断
文字列も他の draft 由来の文字列と同じ UTF-8 byte 合計に算入され、超過は
切り詰めずに 422 `ui_draft_payload_too_large` で拒否される(§2.3 と同じ
契約。`tests/test_ui_draft_context.py` の
`validation-error-counts-toward-total` ケースがこれを固定する)。

#### 2.8.7 未対応診断は成功にしない

`useFormValidation` を保存操作の呼び出し箇所へ配線している限り、**未知の
code / field_path であっても必ず `formError`(またはその `section`)へ入り、
黙って消えたり成功表示になったりすることはない**(§2.8.2)。入力は保持
され(フォームの state は保存失敗時に一切リセットしない)、フォーム全体
エラーの概要から該当 field へ focus できる。

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

---

## §9 判断のための横断contextと確認範囲（目標契約）

利用者の導線・状態・受入条件は[共同検討UX](../ux/decision-discussion-workflow.md)が所有する。
本節は新規実装予定であり、既存の画面contextが既に横断調査できるという意味ではない。
既存 `/assistant/ask` に渡すcontextを拡張し、別の汎用チャットAPIや理解モデルを作らない。
本節の `discussion_context.sections[].operation_state` が使う予定の
`available` / `unsupported` / `unavailable` / `not_applicable` 語彙自体は
Issue #456 が `DiscussionOperationResult` として先行実装済みで、
`app/discussion_adapters.gather_context` / `resolve_prefill_operation_state`
がその最初の 2 つの適用先である (§1.3/§1.7 参照)。§9 の bundle 形状・
continuation・複数正本更新 (DD-CTX-01〜05) 自体はまだ実装されていない。

### 9.1 Context bundle

DD-CTX-01: serverは選択対象と既存canonical serviceを起点に、必要な上下流参照を取得する。
Overviewでは画面の `objective` projectionも文脈へ含める。登録されたrelation resolverだけを
使い、名前一致による結合、別System探索、LLMが指定した任意SQL・URL・ファイル読出しは禁止する。

目標wire shape（未実装。Python / TS / JSON Schemaを実装issueで同時に追加）:

```text
discussion_context {
  schema_version, bundle_digest,
  root: {target_kind, target_ref, revision_id, digest},
  snapshot: {id, commit_sha},
  sections: [{
    section_id,
    operation_state: available | unsupported | unavailable | not_applicable,
    facts: [...],
    coverage: {
      returned_count, total_count: integer | null,
      completeness: complete | partial | unknown,
      stop_reason: complete | item_budget | byte_budget | depth_budget | provider_error | unsupported | not_applicable,
      continuation: opaque_token | null
    }
  }],
  sources: [{source_id, target_kind, target_ref, revision_id, digest,
             snapshot_id, freshness, deep_link, deep_link_state}],
  dependencies: [{target_kind, target_ref, digest}],
  next_action: {kind, target, enabled, reason}
}
```

factsは一時的な正本の読取結果。参照・digestを監査に持ち、正本本文を新テーブルへ複製しない。
`operation_state` は取得処理の成否、`freshness` は正本resolverの状態であり、別軸。
取得成功でも古い証拠はあり得る。取得失敗時の `total_count=null` は0件を意味しない。
未対応・対象外は対応する終了理由を返し、完全取得と偽らない。

DD-CTX-02: 初期budgetは関連方向ごと深さ2、section最大50件、全体200件・UTF-8 JSON 64KiB。
selected rootを優先し、登録relation順・stable key順で再現可能に探索し、cycleはidentityで打ち切る。
巨大rootも無制限に通さず本文詳細を参照に置換してpartialとする。暗黙切捨ては禁止する。
budget値はversion付きserver設定とし、UIで独自上限を再計算しない。

DD-CTX-03: 「関連情報を追加確認」は、server発行のcontinuationで未取得範囲を限定して取得する。
cursorはSystem、root、snapshot、bundleの前提、relation範囲にbindし、有効期限を持つ。
未知・改ざん・別Systemは拒否、premise変更は409相当の再確認。再取得も予算内で行う。
初期リリースは利用者の明示操作のみ。Agentの自律的な無制限探索は非目標。

API案は `POST /assistant/discussion-threads/{id}/context-expansions`。
入力は `bundle_digest` と `continuation`、返却は更新bundleとsectionごとの結果。
既存askは未指定なら従来動作。追加contextを後続turnへ渡す際もthread/System/bundleをserverで再検証する。
UIはレスポンス順序ではなく要求時のroot/bundleにbindし、遅延した別対象の応答を表示しない。
必要な監査・短期bundle保持はcontext実装issueが所有し、期限とcleanup・互換性を定義する。
LLM呼出しをDB connection内で行わない。

### 9.2 ギャップの読み違いを防ぐ

DD-CTX-04: 出力では次を区別する。既存Product Gapに新しいlifecycleやseverityを加えず、
照合結果の解釈として参照元を添える。

| 観察・解釈 | 言えること | 次の確認 |
| --- | --- | --- |
| relation未登録 | 正本に関連付けがない | 対象の対応関係を確認 |
| evidence不足 | 実現を裏付ける証拠が不足 | コード・テスト・観測を調査 |
| 実装不足の仮説 | 確認範囲では必要な動作が見つからない | 競合説明と反証条件を置く |
| unavailable / 省略 | その範囲を確認できていない | 再取得・範囲の追加確認 |
| 期待との矛盾 | どの期待とどの観察が食い違うか | premiseと再現条件を確認 |

取得結果だけで機能不存在を確定しない。未知と対象外も区別する。構造的な未登録は決定的に導出し、
意味的な不足はAI解釈として `fact / inference / hypothesis / unknown / conflict` と根拠参照で返す。
source IDを許可集合で照合し、存在しない引用がある重要claimは再生成または明示失敗とする。
引用を落として根拠のない断言を成功扱いしない。最低一つのsourceと、確認範囲・時点を照合結果に付ける。

### 9.3 複数正本の更新

DD-CTX-05: rootだけでなく回答に使った各dependency digestをbundleに固定する。
読出し時点が揃わない場合は再確認を要求し、整合したsnapshotだと偽らない。
根拠側の更新でもProposal生成・JU昇格・prefill前に再検証する。旧turnは履歴として保持するが
最新の事実として自動継承しない。JU側のpremise verdictを再定義しない。
引用先は正本のdeep linkを使い、削除・権限・stale時の理由を表示する。

## §10 横断導線の接続境界（目標契約）

既存§3/5/6の型、保存責務、gateを変えず、[共同検討UX](../ux/decision-discussion-workflow.md)へ
接続する。統合層が保持するのは起点target、thread/proposal/JU/保存revisionの参照のみ。
独立した「総合進捗」DBやAIによるGap解消処理を追加しない。

DD-INT-01: prefill結果の成功、対象formの受領、domain保存成功は別々に表示する。
フォーム未mount・配送失敗は成功扱いしない。保存応答不明時はdomain側の結果を照合してから再試行。
既存§3.4のitem statusは変更せず、保存先revisionへの参照で実際の保存を説明する。
永続的な保存参照が必要なら#452がadditiveな監査拡張・System隔離・冪等性を所有する。

DD-INT-02: 旧clientは新しいcontext/参照を送らなくても既存の機能を利用できる。
新clientは未対応serverに架空の成功を返さず、利用可能な既存画面への移動を示す。
新経路を無効化しても既存thread、Proposal direct apply、JUの履歴はそのまま読める。
