# AI アシスタントの回答品質と応答時間 (Epic #467) — canonical contract

本書は Epic #467 (子Issue #468-#473) の正本契約である。画面AIアシスタントの
通常 ask (`POST /assistant/ask`) の **計測・情報選択・根拠付き回答・失敗時応答・
待ち時間** を定める。会話 thread / 変更候補 / help mode / 音声の正本は
[assistant-discussion.md](assistant-discussion.md) (#436) と
[ai-discussion-adapter.md](ai-discussion-adapter.md) (#443) のままで、本書は
それらを置き換えない。

## §0 境界 — 後から変えるときに必ず守ること

- **新しい理解モデルも正準状態も作らない。** ask が読むのは既存の診断・
  System state・画面 canonical context・discussion context bundle だけで、
  この Epic が新しく持つのは「1 回の ask の計測記録」と「回答の構造」の 2 つ
  だけ。
- **構造検証の合格は回答品質の合格ではない** (IK-0004 / EC-P06)。source ID が
  許可集合に入っていることは「その根拠が主張を裏付けている」ことを意味しない。
  意味的な裏付けは固定課題と人間評価 (§7) で見る。
- **未測定を 0 にしない。** 実行されなかった段階は `not_run`、標本が無い集計は
  `unmeasured`。provider が token usage を返さなければ実測値は `null` で、
  推定値 (`estimated`) と同じ欄へ入れない。
- **計測は本文を複製しない。** 計測記録 (`assistant_ask_metric`) は質問本文・
  回答本文・未保存 draft の値・秘密値・provider の生応答を 1 文字も持たない。
  持つのは時間・件数・サイズ・有限コード・source の id / revision / digest だけ。
- **会話は正規データを書き換えない。** 本 Epic はどの human gate も緩めない。
  回答の構造化も失敗時応答も、提案・保存・実行・publish・policy 変更を起こさない。
- **System / thread / 対象の隔離を維持する。** 計測記録は System scoped で、
  他 System の記録は 404。request scope の共有 (§2) は 1 リクエスト内に限り、
  System をまたぐキャッシュや TTL を持たない。
- **自由文から意図を推測する分類器を置かない** (Principle 6)。情報選択 (§3) は
  thread の対象・adapter registry・完全一致の設定キー / check id など、
  すでに有限な構造的事実だけで決める。
- **失敗を正常な回答として見せない。** LLM の失敗時に、質問と無関係な画面説明・
  設定一覧を回答の代わりに返さない (§5)。

## §1 計測と来歴 (#468)

### 1.1 request_id と段階

1 回の `POST /assistant/ask` ごとに server が `request_id` (uuid4 hex) を発行し、
`AssistantAskOut.request_id` で返す。段階名は有限集合:

```
AskStage = "request_validation" | "diagnostics" | "system_state"
         | "screen_context" | "context_bundle" | "context_pack"
         | "llm" | "response_validation" | "persist"
```

各段階は `time.perf_counter()` の差分 (ms) で記録する。実行されなかった段階は
記録に**現れない** (集計では `not_run`)。同じ段階が 2 回走った場合は合計時間と
回数を記録する (重複の検出が #469 の目的なので、上書きしない)。

カウンタ: `diagnostics_runs` / `system_state_runs` / `llm_calls` / `llm_retries`。

### 1.2 入力サイズと token

- `input_sizes`: LLM へ送る payload の top-level セクションごとの文字数
  (`screen` / `settings` / `diagnostics` / `system_state_items` / `screen_data` /
  `related_context` / `ui_draft` / `coverage` / `conversation` / `question` /
  `system_prompt` …) と合計。
- `usage`: `{"input_tokens": int|null, "output_tokens": int|null,
  "source": "provider_reported" | "not_reported"}` と、別フィールドで
  `estimated_input_tokens` (`ceil(total_chars / 4)`、`estimate_method:
  "chars_div_4"`)。実測と推定は同じ欄へ入れない。
- `output_chars`: 回答本文の文字数。

### 1.3 来歴 manifest

`context_manifest` は「この回答が何を見て作られたか」:

```
{
  "sources": [{"type": "screen_data|state_item|diagnostic_check|setting|
                         pipeline_step|ui_draft|related_context",
               "id": str, "revision": str|null, "digest": str|null,
               "freshness": "current|stale|unknown|not_tracked"}],
  "coverage": [CoverageEntry ...],          # §3.2
  "omitted_sections": [{"section": str, "reason": "budget|stale_target|
                         unsupported|unavailable", "dropped_items": int|null}],
  "history_turns": int,                      # LLM へ渡した過去発言数
  "ui_draft_state": "not_provided|...",      # 値は含めない
  "budget": {"limit_chars": int, "used_chars": int, "over_budget": bool}
}
```

`ui_draft` の source は id (form id) だけを持ち、値を持たない。

### 1.4 永続化

テーブル `assistant_ask_metric` (System scoped、`system_id` FK):
`request_id` (UNIQUE) / `screen_id` / `thread_id` / `assistant_turn_id` /
`input_mode` / `started_at` / `finished_at` / `outcome` / `failure_class` /
`provider` / `model` / `prompt_version` / `schema_version` /
`stage_timings_json` / `counters_json` / `input_sizes_json` / `usage_json` /
`manifest_json` / `client_timing_json` / `created_at`。

```
AskOutcome = "answered" | "deterministic_answer" | "failed" | "rejected" | "error"
```

- `rejected`: 4xx で拒否 (未知 screen、draft 検証失敗など)。
- `error`: 想定外の例外 (500)。どちらも**記録する** — 失敗を欠測にしない。
- 計測の書き込み失敗は回答を壊さない (log して続行)。
- 保持期間: `ASSISTANT_METRIC_RETENTION_DAYS` (既定 30)。挿入時に古い行を削除する。

### 1.5 API

- `GET /assistant/ask-metrics?limit=` — 新しい順 (最大 200)。
- `GET /assistant/ask-metrics/{request_id}` — 1 件。他 System は 404。
- `POST /assistant/ask-metrics/{request_id}/client-timing` —
  `{"first_visible_ms": number|null, "complete_ms": number|null,
  "aborted": bool}`。ブラウザーの送信から初期表示 / 完了までの時間。
  1 回だけ記録でき、2 回目は 409 `client_timing_already_recorded`。
- `GET /assistant/ask-metrics-summary?window_hours=` — 段階ごとの p50/p95
  (nearest-rank、観測値そのもの) と `sample_count`、outcome 別件数、
  failure_class 別件数、失敗率 (分母明示)、`llm_calls` 合計、token の実測合計と
  推定合計 (別欄)。標本 0 の値は `null` + `"state": "unmeasured"`。
  パスを `/assistant/ask-metrics/summary` にしないのは `{request_id}` と衝突
  させないため (#338 の `/lineage` と同じ罠)。

## §2 1 リクエスト内の共有 (#469)

- `app/assistant_request_scope.py` の `AskRequestScope(system_id)` が、
  診断 (`run_system_diagnostics`) と System state (`build_system_state`) を
  **1 リクエストの間だけ** memo する。モジュールレベルのキャッシュは置かない。
- `build_system_state(system_id, *, diagnostics_report=None)` と
  `build_overview(system_id, *, system_state=None)` は、渡された値を使い、
  渡されなければ従来どおり自分で取得する (単独呼出しの契約は不変)。
- `build_screen_discussion_context(..., scope=None)` は Overview 画面で
  scope の System state を `build_overview` へ渡す。
- 同じ System・同じ時点の値だけを共有する。LLM 後の対象再解決
  (`resolve_target`) と保存時の再検証は**省略しない**。

## §3 情報選択と coverage (#470)

### 3.1 構造的な選択

追加で取得するのは次の構造的事実があるときだけ:

1. thread があり、その `target_kind` が `discussion_adapters` に登録され、
   `target_state` が `current` / `not_tracked` → その対象を root とする
   discussion context bundle (`discussion_context_bundle.build_context_bundle`)
   を ask 用の小さい予算 (`ASK_BUNDLE_BUDGET`) で取得し、top-level
   `related_context` に載せる。各 entry は source として id / digest /
   freshness を持つ。claims (#458 の LLM 生成主張) は**再利用しない**。
2. `stale` / `unresolvable` の thread は bundle を取得せず、
   `omitted_sections` に `stale_target` として記録する。
3. 未登録 kind は `unsupported`、取得失敗は `unavailable`
   (どちらも `omitted_sections`)。ask 全体は失敗させない。

### 3.2 coverage

一覧を上限で切った箇所は `CoverageEntry` を持つ:

```
{"section": str, "state": "complete|truncated|empty|unavailable|unsupported",
 "returned": int, "total": int|null, "limit": int|null,
 "more_available_via": str|null}
```

- `truncated` の一覧に無いことを「存在しない」と断定させない
  (prompt に明記する)。`empty` は「読めて 0 件」、`unavailable` は
  「読めなかった」。
- `total` は安価に数えられる場合だけ入れ、数えていなければ `null`
  (推測しない)。

### 3.3 入力予算

- `ASSISTANT_CONTEXT_BUDGET_CHARS` (既定 60000) が LLM payload 全体の上限。
- 削らないもの: question / discussion_target / ui_draft / focused state item /
  screen_context_state / coverage。
- 超過時は次の順に削る: `related_context` の entry (depth の深い順) →
  `diagnostics.checks` のうち `severity == ok` → 質問で言及されていない
  settings → focus されていない system_state_items → `screen_data` 内の一覧
  (末尾から、coverage を `truncated` / `reason=budget` に更新)。
- 削ったものは `omitted_sections` (`reason: budget`) に記録し、モデルにも
  top-level `context_budget` で伝える。削れるものを削っても超える場合は
  `over_budget: true` のまま送る (保護対象は削らない)。
- 会話履歴は既存の直近 12 発言上限と draft 除外を維持する。

## §4 根拠付き回答 (#471)

### 4.1 出力契約 (`prompt_version: v2`, `schema_version: v2`)

```
{
  "conclusion": "質問への直接の答え (1-3 文)",
  "points": [{"kind": "fact|interpretation|unknown", "text": str,
              "sources": [{"type": CitationType, "id": str}]}],
  "missing_information": [{"what": str, "how_to_get": str}],
  "suggested_actions": [...],   # v1 と同じ有限検証
  "citations": [...]            # 任意。points の sources と合わせて検証
}
```

操作説明のように事実主張が要らない質問では `points` を空にしてよい
(過剰な様式を要求しない)。

### 4.2 検証 (server、決定的)

各 point の sources を許可集合 (`ContextPack.allowed_citation_ids`) と照合する。

```
PointGrounding = "supported" | "stale" | "unsupported" | "not_required"
```

| kind | 有効 source | 判定 |
| --- | --- | --- |
| fact | 1 つ以上、うち current/unknown/not_tracked が 1 つ以上 | `supported` |
| fact | 1 つ以上、すべて freshness `stale` | `stale` |
| fact | 0 (無効 ID のみ / 引用なし) | `unsupported` |
| interpretation | 有効 source あり | `supported` |
| interpretation | なし | `not_required` |
| unknown | — | `not_required` |

- `unsupported` の fact は**事実として表示しない**。「根拠を確認できなかった
  主張」に分けて表示する。引用欄から ID を消して本文だけ残す、は禁止。
- `grounding_state`: fact が 0 → `not_required`; すべて supported →
  `grounded`; supported が 1 つ以上かつ stale/unsupported あり →
  `partially_grounded`; supported が 0 → `ungrounded`。
- `grounding_counts`: 各 PointGrounding の件数 + 除外した無効引用数
  (`rejected_citations`)。
- 存在する ID だが無関係、相反する根拠、誤読は**構造検証では検出できない**。
  §7 の固定課題と人間評価で見る。毎回別 LLM で検証することはしない。

### 4.3 回答本文の組み立て

`answer` は後方互換のため残し、server が決定的に組み立てる:

```
{conclusion}

確認済みの事実:
- …
解釈・提案:
- …
古い根拠に基づく内容 (再確認が必要):
- …
根拠を確認できなかった主張 (事実として扱わないでください):
- …
不足している情報:
- {what} — {how_to_get}
```

空のセクションは出さない。音声の読み上げ projection はこの `answer` から
作る (先頭が結論になる)。

### 4.4 永続化と後方互換

- `assistant_discussion_turn.answer_structure_json` (nullable、additive):
  `{conclusion, points(with grounding), missing_information, grounding_state,
  answer_status, failure_class}`。
- 未保存 draft を参照した turn は既存どおり中立の文言だけを保存し、
  構造も保存しない (NULL)。
- 旧行 (NULL) はそのまま本文を表示する。
- 変更候補生成 (`create_discussion_proposal`) は従来どおり turn の `content`
  を読む。

## §5 失敗時応答 (#472)

### 5.1 有限の失敗分類

```
AssistantFailureClass =
    "provider_not_configured" | "provider_test_only" | "timeout" | "network"
  | "auth" | "rate_limited" | "provider_error" | "malformed_output"
  | "truncated_output" | "context_unavailable" | "budget_exceeded"
```

`app/llm.py` の `LLMError` は `kind`
(`timeout|network|auth|rate_limited|provider_error|malformed_response|config`)
を持ち、HTTP 401/403 → `auth`、429 → `rate_limited`、その他 HTTP → 
`provider_error`、socket timeout → `timeout`、URLError/OSError → `network`。
`LLMError` は `http_status` と、生応答本文を含まない `safe_message` も持つ。
`str(exc)` は他の分析処理の失敗記録 (Principle 7) のため従来どおりだが、ask は
`str(exc)` を応答・履歴・計測のどこにも出さず、`kind` / `http_status` だけを使う。

「情報不足」は失敗ではない: 回答は `answered` のまま `missing_information`
で表す。

### 5.2 回答状態

```
AnswerStatus = "answered" | "deterministic_answer" | "failed"
```

- `answered`: LLM の回答が v2 契約を満たした。
- `deterministic_answer`: LLM は使えなかったが、**質問が有限集合に完全一致した**
  (設定キー / check id / check title / pipeline step の言及、または
  `focused_state_id`) ので、その対象だけを決定的に説明した。
- `failed`: 上記どちらでもない。本文は理由と次の操作だけの短い日本語で、
  画面の概要や設定一覧を回答の代わりに並べない。
- `used_fallback` は `answer_status != answered` と等価、`decision_method`
  は `answered` のとき `reasoning_llm`、それ以外 `deterministic`。

### 5.3 failure オブジェクト

```
{"failure_class": AssistantFailureClass,
 "message": str,          # 固定表の日本語文
 "retryable": bool,
 "recovery": [{"kind": "retry|configure|rephrase|open_settings",
               "label": str, "target": str|null}]}
```

`fallback_reason` は後方互換で残すが、固定表の日本語文だけを入れる。

### 5.4 履歴と再試行

- 失敗 turn も保存する (会話の順序を保つ) が、`answer_structure_json` に
  `answer_status: failed` を持ち、次の ask の LLM 履歴からは除く。
- server は自動再試行しない (`llm_retries` は現状常に 0)。形式修復の再試行は
  固定課題で遅延と効果を測ってから採否を決める (§6)。
- 再試行は利用者の明示操作で、同じ質問・同じ thread・同じ対象で送り直す。
  client_turn_id の冪等性契約 (Interview) は不変。

## §6 待ち時間 (#473)

- `LLMConfig.assistant_from_env()`: `ASSISTANT_LLM_PROVIDER` /
  `ASSISTANT_LLM_MODEL` / `ASSISTANT_LLM_API_KEY` / `ASSISTANT_LLM_BASE_URL` /
  `ASSISTANT_LLM_TIMEOUT` が設定されていればそれを、されていなければ
  `intelligence_from_env()` と同じ値を使う (未設定時の挙動は変更前と同一)。
  他の分析処理の設定は変えない。
- `ASSISTANT_LLM_MAX_OUTPUT_TOKENS` (既定 2048 — 変更前と同じ)。
- `ASSISTANT_REQUEST_BUDGET_SECONDS` (未設定なら無効): リクエスト全体の予算。
  LLM 呼出しの timeout は `min(socket timeout, 残り予算)`。残りが 1 秒未満なら
  呼ばずに `budget_exceeded`。socket timeout とは別の値として扱う。
- **採否を実測で決める案** (出力上限の変更、専用モデル、段階表示/streaming、
  形式修復の再試行) は、§7 の固定課題で同じモデル・同じ課題で比較するまで
  既定を変えない。採否と測定値は
  [改善結果](../../03-validation/improvement-cycle/2026-09-29-assistant-answer-quality.md)
  に記録する。不採用・保留は未実装放置と区別して記録する。

## §7 固定評価課題

- 課題: `apps/control-server/tests/fixtures/assistant_eval/questions.json`
  (20 問以上。操作説明 / 現状確認 / 設計相談 / 原因調査 / 会話継続 /
  対象版更新 / 取得失敗 / 上限超過)。各問に期待する根拠・回答可能範囲・
  禁止する断定を持つ。
- 採点表と実行手順: [docs/03-validation/assistant-answer-quality.md](../../03-validation/assistant-answer-quality.md)。
- 実行: `apps/control-server/scripts/assistant_eval.py`。既定は偽 LLM による
  **構造検証**モードで、実 LLM は `--real --max-calls N` を明示したときだけ
  呼ぶ。両者の結果を混同しない。
