# 画面コンテキスト対応 AI アシスタント (Epic #436) — canonical contract

本書は Epic #436 (sub-issues #437-#441) の正本契約である。この領域に触れる前に
§0 を読むこと。#437 は実装済み (`app/assistant_discussion_context.py`)。

## §0 境界 — 後から変えるときに必ず守ること

- **新しい理解モデルを作らない。** Overview projection / Understanding Brief /
  UX Journey / Requirement / Solution Design / Journey Service Blueprint の
  正本は既存のまま。この層が持つのは「会話」「会話から整理された変更候補」
  「UI 解説」という 3 つの新しい成果物と、上下への参照だけ。上流の本文を
  列へコピーせず、参照 (`target_kind` + 安定 `target_ref` + 捕捉 digest) を
  持ち、解決は kind ごとの唯一の resolver に対して**読み取り時**に行う
  (#405 / #418 と同じ)。
- **会話は正規データを一切書き換えない。** 書き込みは、生成された
  Proposal の item を人が明示的に選んで apply したときだけで、その apply は
  常に `decision_method: manual`。
- **proposal の apply は実装ではない。** publish / policy mode / component
  mode / probe patch / deploy / experiment のどれも変えない
  (Evolution Node ADR-9 と同じ境界を会話層から守る)。
- **機能解説 (help mode) と利用者データの意味分析は別物。** help mode は
  versioned な製品管理下レジストリからの完全一致検索のみで、LLM を
  1 度も呼ばない (`decision_method: deterministic`)。説明文を捏造しない。
- **音声バイナリは永続保存しない。** STT はブラウザ内で行い、利用者の録音
  音声を Control Server へ送らない。TTS は短い読み上げ文だけを Control
  Server から OpenAI Speech API へ送り、返った生成音声をストリームする。
  入力文・生成音声は会話テーブルやファイルへ保存しない。
- **`unknown` / `unavailable` / `stale` / `not_tracked` を丸めない** (#366)。
- System scope を越えない。他 System の thread / proposal は 404。

## §1 DiscussionTarget と thread 永続化 (#438)

### 1.1 有限語彙

```
DiscussionScope      = "screen" | "entity" | "element"
DiscussionTargetKind = "screen"
                     | "interview_session"
                     | "understanding_claim"
                     | "overview_finding"
                     | "ux_journey"
                     | "ux_journey_step"
                     | "ux_requirement"
                     | "solution_design"
                     | "blueprint_lane_cell"
DiscussionTargetState = "current" | "stale" | "unresolvable" | "not_tracked"
```

scope と kind の対応は first-match の固定表。外れた組み合わせは 422
`discussion_target_scope_mismatch` で拒否する (fail-closed)。

| scope    | 許可される target_kind                                                     |
| -------- | -------------------------------------------------------------------------- |
| `screen` | `screen`                                                                   |
| `entity` | `interview_session` / `ux_journey` / `ux_requirement` / `solution_design`  |
| `element`| `understanding_claim` / `overview_finding` / `ux_journey_step` / `blueprint_lane_cell` |

`screen_id` は discussion 対応 4 画面のみ (`DISCUSSION_SCREEN_IDS` =
`overview` / `interview` / `ux-design-studio` / `journey-blueprint`)。それ以外の
画面のアシスタントは従来どおり thread を作らず、クライアント内の非永続
会話のまま動く — これが #437 以前からの**安全な移行経路**である。

### 1.2 target_ref の形 (安定 slug。行 id から導出しない)

| target_kind          | target_ref                                    | digest source |
| -------------------- | --------------------------------------------- | ------------- |
| `screen`             | `screen_id`                                   | なし → `not_tracked` |
| `interview_session`  | `str(session_id)`                             | `current_understanding` の正規化 JSON |
| `understanding_claim`| `<section>:<name>` (`vision`/`system_purpose`/`core_capabilities`) | `understanding_brief.claim_digest` |
| `overview_finding`   | finding の `dedupe_key`                       | finding の内容 digest |
| `ux_journey`         | `journey_key`                                 | 現行 revision の `content_digest` |
| `ux_journey_step`    | `<journey_key>#<step_key>`                    | step の `content_digest` |
| `ux_requirement`     | `requirement_key`                             | 現行 revision の `content_digest` |
| `solution_design`    | `design_key`                                  | 現行 revision の `content_digest` |
| `blueprint_lane_cell`| `<journey_key>#<step_key>#<lane_kind>`        | 当該 lane cell の正規化 JSON digest |

`thread_key = f"{screen_id}|{scope}|{target_kind}|{target_ref}"`、
`UNIQUE (system_id, thread_key)`。**UI の thread key は screen_id ではなく
この canonical identity** である。

### 1.3 target_state は読み取り時に導出し、保存しない

first match:

1. 対象が解決できない (削除済み / 未知 ref / 別 System) → `unresolvable`
2. kind に digest source が無い (`screen`) → `not_tracked`
3. 捕捉 digest ≠ 現在 digest → `stale`
4. → `current`

**`stale` / `unresolvable` の thread の過去 turn は LLM コンテキストへ
自動継承しない。** 履歴は読めるまま残り、応答は `target_state` と
`recheck_required: true` を返す。これが「revision 更新後の旧履歴が current
fact として扱われない」の実装である。

### 1.4 永続化

```
assistant_discussion_thread(
  id, system_id, thread_key, scope, screen_id, target_kind, target_ref,
  target_title, captured_target_revision_id, captured_target_digest,
  status ('open'|'archived'), created_by, created_at, updated_at,
  schema_version 'assistant-discussion-thread-v1',
  UNIQUE (system_id, thread_key))

assistant_discussion_turn(
  id, system_id, thread_id, turn_number, role ('user'|'assistant'),
  content, citations_json, target_revision_id, target_digest,
  used_fallback, decision_method ('manual'|'reasoning_llm'|'deterministic'),
  input_mode ('text'|'voice'), provider, model, prompt_version,
  schema_version 'assistant-discussion-turn-v1', created_by, created_at,
  UNIQUE (thread_id, turn_number))
```

user turn は `decision_method='manual'`、assistant turn は LLM が答えたなら
`reasoning_llm`、fallback なら `deterministic`。

### 1.5 API

- `POST /assistant/discussion-threads` — `{scope, screen_id, target_kind,
  target_ref}` で resolve-or-create (冪等)。`{thread, target_state, turns}`。
- `GET /assistant/discussion-threads?screen_id=&scope=&target_kind=&target_ref=`
  — System-scoped 一覧 (最大 50、新しい順)。
- `GET /assistant/discussion-threads/{thread_id}` — `{thread, target_state,
  turns}`。他 System は 404。
- `POST /assistant/ask` に `thread_id?: int` を追加。指定時:
  - 他 System の thread は 404。
  - body の `conversation` は空でなければ 422
    `conversation_not_settable_with_thread` (真実の出所を 2 つ作らない)。
  - サーバが thread から**直近 12 turn**を bounded context として組む
    (`target_state` が `current` / `not_tracked` のときだけ)。
  - thread の対象を screen data provider の route params へ注入し、その
    要素自身の canonical facts が context pack に載るようにする。
  - user turn → assistant turn を 1 トランザクションで追記する。
  - 応答に `thread_id` / `target_state` / `recheck_required` / `turn_number`。
- `POST /assistant/ask` の `input_mode` (`text` | `voice`、既定 `text`) は
  **user turn にだけ**記録する。assistant はマイクへ話していないし、その回答を
  読み上げたかどうかはクライアント側の再生選択であって turn の事実ではない —
  1 列に 2 つの意味を持たせない (#366)。

## §2 会話結論の変更候補化 (#439)

### 2.1 許可 schema (target_kind ごとの有限 registry)

`assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA` が唯一の正本。
登録されていない `field_name` / `relation_kind` / `target_kind` は
**fail-closed** で拒否する (LLM に存在しない field / kind を作らせない)。

| target_kind           | 許可 field                                              | 許可 relation |
| --------------------- | ------------------------------------------------------- | ------------- |
| `ux_journey`          | `title` / `beneficiary` / `usage_context` / `entry_trigger` / `value_arrival` / `summary` | `upstream_ref` |
| `ux_journey_step`     | `user_intent` / `system_response` / `success_criteria` / `failure_mode` / `recovery_path` / `evidence_expectation` | — |
| `ux_requirement`      | `statement` / `rationale` / `constraint_text` / `out_of_scope_note` | `journey_step_link` |
| `solution_design`     | Option の `title` / `approach` / `tradeoffs` / `risks` (`subject_ref` に `option_key`) | `requirement_link` / `target_link` |
| `blueprint_lane_cell` | —                                                       | `delivery_link` / `stakeholder_link` / `exchange_link` |
| `understanding_claim` | `summary` / `why_core` / `name`                          | — |
| `overview_finding` / `interview_session` / `screen` | — (討議のみ)          | — |

### 2.2 生成と適用は別

- 生成: `POST /assistant/discussion-threads/{id}/proposals` — reasoning model の
  structured output (`summary`, `confirmed_points`, `unresolved_questions`,
  `proposed_field_changes`, `proposed_relation_changes`, `evidence_refs`,
  `assumptions`)。schema 検証は fail-closed。LLM 未設定 / `mock` provider は
  503 `reasoning_unavailable` (mock 出力を分析結果として出さない)。
  現行 revision / digest を捕捉して保存する。
- preview: `GET /assistant/discussion-proposals/{id}` — item ごとに適用可否を
  first match の有限値で返す: `forbidden` → `stale` → `conflict` → `appliable`。
- 適用: `POST /assistant/discussion-proposals/{id}/apply`
  `{item_ids: [...], rationale}` — **選択した item だけ**を all-or-nothing で
  適用。1 つでも `appliable` でなければ 422 (`proposal_item_forbidden` /
  `proposal_item_stale` / `proposal_item_conflict`)。適用は必ず既存
  ドメインサービス経由:
  - Journey / Requirement / Solution Design の field → 既存の
    `add_*_revision(..., authored_by_kind='reasoning_model',
    decision_method='manual')`。**確定はしない** — 既存の decision API を
    人が別途叩くまで `proposed` のまま。
  - Blueprint の unknown lane → `journey_blueprint.add_delivery_link` /
    `add_stakeholder_link` / `add_exchange_link` (`decision_method='manual'`)。
  - `understanding_claim` → Intent Brief の propose 経路。`status='proposed'`
    / `origin='ai_proposed'` / `decision_method='reasoning_llm'` の新規 item
    として記録し、**自動確定はしない**。Intent Brief には claim 自身の
    `summary` / `why_core` / `name` に 1:1 対応する field が無く、開発者の
    確定済み `goal` が reviewer の Vision より上位に立つ (#351) ので、候補
    `goal` として置き、確定は既存の confirm/correct 判断に委ねる。元の claim
    field は `source_statement` に残す。
- 拒否: `POST /assistant/discussion-proposals/{id}/reject` で item を
  `rejected` にする (監査記録、`decision_method: manual`)。

### 2.3 永続化

```
assistant_discussion_proposal(
  id, system_id, thread_id, screen_id, target_kind, target_ref,
  captured_target_revision_id, captured_target_digest, summary,
  confirmed_points_json, unresolved_questions_json, assumptions_json,
  evidence_refs_json, decision_method ('reasoning_llm'),
  intelligence_run_id, provider, model, prompt_version, schema_version,
  created_by, created_at)

assistant_discussion_proposal_item(
  id, system_id, proposal_id, item_kind ('field'|'relation'),
  field_name, relation_kind, relation_target_kind, relation_target_ref,
  subject_ref,          -- 対象内のサブアドレス (Solution Design の option_key)
  current_value, proposed_value, rationale,
  status ('proposed'|'applied'|'rejected'),
  applied_ref, decided_by, decided_at,
  decision_method ('reasoning_llm'|'manual'), created_at, schema_version)
```

## §3 機能解説モード (#440)

- `app/ui_help_registry.py` が正本。`UI_HELP_REGISTRY_VERSION` を持ち、
  entry は `help_id` (完全一致キー) / `screen_id` / `scope`
  (`screen`|`section`|`element`) / `title` / `summary` / `usage` /
  `doc_refs` (リポジトリ内 `docs/*.md` への参照) / `related_actions` /
  `related_help_ids`。
- API: `GET /assistant/ui-help/{help_id}` (未知は 404)、
  `GET /assistant/ui-help?screen_id=` (一覧)。応答は常に
  `decision_method: "deterministic"` と `registry_version` を含む。
  **LLM を呼ばない。**
- Dashboard: Header の question-mark トグル (`data-testid="help-mode-toggle"`,
  `aria-pressed`)。再クリックと Escape で解除し、解除後は通常操作へ完全に
  戻る。対象要素は `data-help-id` を持ち、入れ子は `closest()` で最も近い
  help target を選ぶ。hover は debounce し、古いリクエストは破棄する。
  hover 不能端末では tap、キーボードでは focus で選べる。状態は色だけでなく
  text と `aria-live` でも伝え、`prefers-reduced-motion` では点滅しない。
- 音声モード中: hover 対象なし = screen scope、hover 対象あり = element scope
  として会話へ渡す。

## §4 音声対話 (#441)

音声対話は **turn-based**。STT adapter はブラウザ内、TTS adapter は認証済み
Control Server の `POST /assistant/speech` を介して OpenAI Speech API を使う。
API key はブラウザへ渡さない。利用者の録音音声と生成音声は永続保存しない。

- `src/lib/voice-adapter.ts`: `SpeechToTextAdapter` / `TextToSpeechAdapter` /
  `createBrowserVoiceAdapters()` / `voicePrerequisite()` →
  `"ready" | "insecure_context" | "unsupported"`。
- `POST /assistant/ask` は voice turn に完全な `answer` と、最大 3 文・180 文字の
  `spoken_answer` を返す。長い場合は概要・中核の後に「続けて詳しく説明するか」
  を尋ね、`voice_follow_up_expected: true` を返す。クライアントは再生終了後に
  自動で STT を再開し、詳細はボタン操作なしの次の利用者 turn を待つ。
- `POST /assistant/speech` は `spoken_answer` の短いテキストを受け、OpenAI
  `audio/speech` の生成音声 (`audio/mpeg`) を `Cache-Control: no-store` で返す。
  model / voice / style / timeout / base URL は `OPENAI_TTS_*` で設定する。
- 状態は有限: `idle` | `listening` | `thinking` | `speaking` | `error`。
  色だけでなく text + `aria-live` で伝える。`prefers-reduced-motion` では
  点滅・脈動しない。
- 音声モード中はメッセージ一覧を隠す (履歴自体は保持する)。
- `stop` / `mute` / `exit` は常時利用可能。再生中は「話を挟む」も表示し、
  音声取得・再生を即時 cancel して次の STT turn を開始する。
- **turn 開始時の discussion target を回答完了まで固定する** — 途中で選択が
  変わっても、その turn は捕捉した target で答える。
- element scope は `useHelpMode().target` (#440) から取り、新しい
  `DiscussionTargetKind` は増やさない (§1.1 の有限集合は閉じている)。route
  param `voice_element_help_id` として既存の経路で渡す。
- 音声 turn は `input_mode: "voice"` を送り、サーバが user turn に記録する。
- microphone 拒否 / STT 失敗 / TTS 失敗 は error 状態を示したうえで text mode
  へ安全に戻る。
- 音声会話からも正規データは変更しない (§0)。
- Realtime の WebSocket / VAD / 自動 barge-in / reconnect は対象外。明示ボタン
  ではなく発話検知で自動割り込みする場合は、transcript の可視性と保持方針を
  先に決めること。
