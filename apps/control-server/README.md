# Control Server

`probe-agent` の SDK から送られるトレースを受け取り、SQLite に保存する FastAPI サーバー。

## 起動

```bash
cd apps/control-server
pip install -e .
uvicorn app.main:app --reload --port 8000
```

## API

| Method | Path | 用途 |
| --- | --- | --- |
| GET  | `/health` | ヘルスチェック |
| POST | `/traces` | trace 受信 |
| GET  | `/components` | component 一覧 + 集計 |
| GET  | `/components/{id}/traces` | trace 一覧 |
| GET  | `/components/{id}/policy` | policy 取得 |
| PUT  | `/components/{id}/policy` | policy 更新 (`off`/`trace`/`shadow`) |
| POST | `/components/{id}/shadow-results` | shadow 実行結果の保存 |
| GET  | `/components/{id}/shadow-results` | shadow 実行結果一覧 |
| PUT  | `/shadow-results/{id}/evaluation` | 手動評価 (`better`/`worse`/`same`/`unknown`) |
| POST | `/auth/login` | username/password でログインし token を取得 |
| POST | `/auth/logout` | 呼び出しに使った token を失効 |
| GET  | `/auth/me` | 認証中ユーザーの情報 |
| GET  | `/users` | ユーザー一覧 (admin) |
| POST | `/users` | ユーザー作成 (admin) |
| POST | `/users/{id}/deactivate` | ユーザー無効化 + token 失効 (admin) |
| DELETE | `/users/{id}` | ユーザー削除 + token 削除 (admin) |
| POST | `/users/{id}/password` | パスワードリセット + login session 失効 (admin) |
| PUT  | `/users/{id}/role` | role 変更 (admin) |
| GET  | `/tokens/me` | 自分の token 一覧 (要ユーザーアカウント) |
| POST | `/tokens/me` | 自分の SDK/API token 発行 (要ユーザーアカウント) |
| POST | `/tokens/me/{id}/revoke` | 自分の token 失効 (要ユーザーアカウント) |
| GET  | `/tokens` | 全 token 一覧 (admin) |
| POST | `/tokens` | 任意ユーザーの SDK/API token 発行 (admin) |
| POST | `/tokens/{id}/revoke` | 任意の token 失効 (admin) |
| GET  | `/systems` | 自分が利用できる system 一覧 |
| POST | `/systems` | system 作成 |
| PUT  | `/systems/{id}` | system の名前・環境・説明を更新 |
| DELETE | `/systems/{id}` | system と観測データを削除 |
| POST | `/generation-runs` | trace 入力から候補コードを生成・実行・LLM 評価 |
| GET  | `/generation-runs` | 生成・評価結果一覧 |
| GET  | `/generation-runs/{id}` | 生成・評価結果詳細 |
| GET  | `/system-diagnostics` | 必須設定の静的ヘルスチェック (LLM 不使用、Issue #101) |
| GET  | `/assistant/settings-metadata` | 設定項目の静的説明メタデータ (コード管理、Issue #102) |
| GET  | `/assistant/screen-context/{screen_id}` | 画面コンテキスト + 現在の診断状態 + 提案質問 |
| POST | `/assistant/ask` | 画面コンテキスト/設定メタデータ/診断結果に根拠づけた Q&A |
| GET  | `/trace-lineage/entities/{type}/{id}` | entity 単位の系譜(#145) |
| GET  | `/trace-lineage/correlations/{id}` | correlation 単位の系譜(#145) |
| GET  | `/trace-lineage/flows/{id}` | flow 単位の系譜(#145) |
| GET  | `/traces/{trace_id}/projections` | trace の projection 取得(#146) |
| GET  | `/components/{id}/projections` | component の projection 一覧(#146) |
| POST | `/trace-analyzers` | analyzer 手動作成(schema 検証 fail-closed、#148) |
| POST | `/trace-analyzers/propose` | 自然言語 → reasoning model → schema+実在検証 → proposed 保存(#149) |
| GET  | `/trace-analyzers` / `/trace-analyzers/{id}` | analyzer 一覧・取得(#148) |
| PUT  | `/trace-analyzers/{id}/review` | proposed→approved/rejected(#148) |
| POST | `/trace-analyzers/{id}/runs` | approved のみ read-only 実行(#148) |
| GET  | `/trace-analyzers/{id}/runs[/{run_id}]` | run 一覧・取得(#148) |

DB ファイルは `PROBE_DB_PATH` (既定 `./probe.db`) で切り替えられる。

### Trace lineage / analyzer の環境変数

| 変数 | 既定 | 説明 |
| --- | --- | --- |
| `ANALYZER_MAX_INPUT_ROWS` | `10000` | analyzer 実行時にスキャンする projection 行の上限(超過で run 失敗) |
| `ANALYZER_MAX_OUTPUT_BYTES` | `200000` | analyzer 結果 JSON の最大バイト数(超過で run 失敗) |
| `ANALYZER_MAX_SECONDS` | `10` | analyzer 実行の最大秒数(超過で run 失敗) |
| `ANALYZER_MAX_EXAMPLES` | `5` | shadow diff の diff クラスごとに保持する例示トレース数(#150) |

SDK 側の projection 上限(`PROBE_PROJECTION_MAX_*`)は `packages/python-probe/README.md` を参照。

## LLM 設定

Generate & Evaluate は `app.llm` の抽象化層だけを通して LLM を呼び出す。
アプリケーションコードはプロバイダ固有の request / response 形式を直接扱わない。

| 変数 | 用途 |
| --- | --- |
| `LLM_PROVIDER` | `openai` / `anthropic` / `gemini` / `mock` |
| `LLM_MODEL` | 使用するモデル名 |
| `INTELLIGENCE_LLM_PROVIDER` | Feature Intelligence 用 provider (未設定なら `LLM_PROVIDER` を使用) |
| `INTELLIGENCE_LLM_MODEL` | Feature Intelligence 用 reasoning model (未設定なら `LLM_MODEL` を使用) |
| `INTELLIGENCE_LLM_TIMEOUT` | Feature Intelligence の HTTP timeout 秒（既定値: `120`） |
| `INTELLIGENCE_MAX_OUTPUT_TOKENS` | Repository Draft生成の最大出力token数（既定値: `128000`） |
| `INTERVIEW_LANGUAGE` | System Interview の出力言語 `ja` / `en`（既定値: `ja`）。JSON キーと enum 値は常に英語。不正値は fail-closed |
| `INTERVIEW_CONTEXT_MAX_CHARS` | インタビュー context pack の文字数バジェット（既定値: `60000`） |
| `INTERVIEW_UNDERSTANDING_MAX_CHARS` | 対話プロンプトに注入する構築済み理解の文字数バジェット（既定値: `20000`） |
| `INTERVIEW_EVIDENCE_MAX_FILES` | 対話ターンのパス1(証拠選定)が1ターンで読めるファイル数の上限(既定値: `5`) |
| `INTERVIEW_EVIDENCE_MAX_LINES_PER_FILE` | 証拠として読む1ファイルあたりの最大行数(既定値: `200`) |
| `INTERVIEW_EVIDENCE_MAX_CHARS` | 証拠として読む全ファイル合計の最大文字数バジェット(既定値: `20000`) |
| `LLM_API_KEY` | 各プロバイダ共通の API key |
| `LLM_BASE_URL` | 互換 API やプロキシを使う場合の base URL |
| `LLM_TIMEOUT` | HTTP timeout 秒 |

`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` も後方互換として読まれる。
`mock` はテストとローカルUI確認用で、外部 API は呼ばない。

## System Understanding build ジョブ (Issue #109)

`POST /repository/system-understanding/build` は step 単位で orchestration
される非同期ジョブを enqueue し、即座に `job_id` / `run_id` を返す
(`run_id` は初回実行・retry ごとに発番される実行単位の識別子)。進捗は
`GET /repository/system-understanding/jobs/{job_id}` /
`GET /repository/system-understanding/jobs/active` で polling する。
step ごとに status / started_at / completed_at / duration / error /
artifact provenance が永続化され、completed step は再実行されない。
claim scan は chunk 単位の LLM task として retry / backoff / cancel を統一管理する。
job status は全 step 完了時のみ `completed` になり、failed / blocked /
cancelled の step が残る場合は `partial`(1 つも完了していなければ `failed`)
として区別される。

| 変数 | 用途 |
| --- | --- |
| `SYSTEM_UNDERSTANDING_STUCK_AFTER_SECONDS` | heartbeat がこの秒数更新されない active job を stuck と判定（既定値: `300`） |
| `SYSTEM_UNDERSTANDING_LLM_MAX_ATTEMPTS` | claim scan chunk task の最大試行回数（既定値: `3`） |
| `SYSTEM_UNDERSTANDING_LLM_BACKOFF_SECONDS` | chunk task retry の指数 backoff 基準秒（既定値: `2`） |

## 設定診断 (System Diagnostics)

`GET /system-diagnostics` は必須設定の静的・決定的ヘルスチェックを返す (Issue #101)。

- 環境変数の有無、enum 値、パスの存在と read/write 権限、provider と model
  family の整合、reasoning-capable かどうかを LLM を使わずに検査する。
- 実行しないと分からない失敗 (LLM の timeout / auth / invalid model、snapshot
  失敗など) は、直近の `intelligence_runs.error_details` / snapshot 状態を
  `last_observed_error` としてそのまま返す。エラーメッセージの解釈・分類は
  行わない (Principle 6)。
- severity は `ok | warning | error | blocked | unknown`。各 check は
  impact・remediation・関連 env/path/画面/pipeline step を持ち、Dashboard の
  alert badge と System Understanding の pipeline 行から参照される。
- すべての check は `decision_method: deterministic`。

## 画面アシスタント (Per-page Assistant)

各画面のエージェントボタンから使う画面コンテキスト付き Q&A (Issue #102)。

- `GET /assistant/settings-metadata`: 設定項目の説明 (目的・影響・修正方法・
  valid values・関連 check/画面/pipeline step)。`app/settings_metadata.py` の
  静的データで、LLM 生成ではない。診断 check が `related_env` で参照する
  env var は必ずエントリを持つ (テストで強制)。
- `GET /assistant/screen-context/{screen_id}`: 画面の目的・セクション・関連
  設定/チェック/エンドポイントの静的定義 (`app/assistant.py`) に、その画面に
  関連する現在の診断 check と提案質問 (失敗中 check 由来を先頭) を付けて返す。
- `POST /assistant/ask`: 質問に対し、画面コンテキスト + 設定メタデータ +
  決定的診断結果だけを根拠に回答する。実 provider (openai/anthropic/gemini +
  API key) があればその限定コンテキストのみを LLM に渡し
  (`decision_method: reasoning_llm`)、citation と navigate 先はコンテキスト
  外のものを構造的に除去する。provider が `mock`・key 無し・LLM 失敗時は
  静的メタデータと診断結果をそのまま組み立てた fallback 回答を返し、
  `used_fallback: true` と `fallback_reason` を明示する。fallback は既知の
  設定 key / check 名 / pipeline step 名との有限マッチのみで内容を選び、
  自由文をヒューリスティックに解釈しない (Principle 6)。
- Q&A は永続化しない。監査メタデータ (provider/model/prompt/schema version、
  decision method、失敗詳細) はレスポンスに含めて返す。

## 認証とユーザー管理

実運用向けに、管理者が管理するユーザーアカウントとトークン発行に対応する。

### 環境変数

| 変数 | 用途 |
| --- | --- |
| `CONTROL_ADMIN_USERNAME` | 起動時に作成する初期管理者のユーザー名 |
| `CONTROL_ADMIN_PASSWORD` | 初期管理者のパスワード (起動時にハッシュ化して保存) |
| `CONTROL_API_KEYS` | 旧来の固定 API キー (後方互換のため残置、カンマ区切り) |

- `CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` が設定されていて同名ユーザーが
  まだ存在しない場合、起動時に `admin` ロールのユーザーを作成する。
- パスワードは平文保存されず、PBKDF2-HMAC-SHA256 (ソルト付き) でハッシュ化される。

### 認証の有効化条件

ユーザーが1人以上存在するか `CONTROL_API_KEYS` が設定されている場合に認証が有効になる。
どちらもなければ MVP 互換で認証なし(全許可)で動作する。

### トークンの使い方

- ログインで得た token、または admin が発行した API token を
  `Authorization: Bearer <token>` もしくは `X-Api-Key: <token>` で送る。
- SDK は `PROBE_API_KEY` を `X-Api-Key` に付与するため、発行した
  API token を `PROBE_API_KEY` に設定すればそのまま利用できる。
- API token は発行時に 1 つの system へ紐づき、component、trace、policy、
  profile、評価結果はその system 内だけで参照・更新される。
- Dashboard のログイン session は `X-Probe-System-Id` で選択中 system を指定する。
  SDK の API token では system が token から決まるため、このヘッダーは不要。
- 一般ユーザーは `/tokens/me` 系 API で自分の token を発行・一覧・失効できる
  (Dashboard の「My Tokens」タブが使用)。legacy API key や匿名アクセスでは
  使えない (403)。他ユーザーの token の失効は 404 になる。
- 失効済み・期限切れ・無効化ユーザーの token は 401 で拒否される。

### ユーザーの停止・削除に関する安全制約

- `POST /users/{id}/deactivate`: 対象を inactive にし、その token をすべて失効させる。
- `DELETE /users/{id}`: 対象ユーザーと、その token を削除する。
- `POST /users/{id}/password`: パスワードを変更し、対象の login session token を
  失効させる (API token は有効のまま)。
- `PUT /users/{id}/role`: role を変更する。
- 最後の active admin は停止・削除・降格できない (409)。
- admin は自分自身のアカウントを削除できない (409)。

### CONTROL_API_KEYS からの移行

1. `CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` を設定して起動し管理者を作成。
2. `/auth/login` で token を取得し、`/tokens` で SDK 用 API token を発行。
3. 各 SDK / クライアントの `PROBE_API_KEY` を発行した token に置き換える。
4. 移行完了後に `CONTROL_API_KEYS` を削除する。

## Docker での起動

リポジトリルートから:

```bash
docker compose up --build control-server
```

Compose 利用時は `PROBE_DB_PATH=/data/probe.db` がセットされ、SQLite ファイルは
名前付き volume `probe-data` に永続化される。
