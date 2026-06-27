# Feature Intelligence / Experiment Workspace 設計

## 目的

既存の `Component`（`@probe` を付ける関数）の上に、ユーザー価値や業務フローを
表す `Feature` を置く。対象リポジトリを理解してから観測点と実験を提案し、
元リポジトリへ自動適用せずに改善判断を支援する。

```text
System
  └─ Feature
       └─ Probe Point
            └─ Component Trace
                 └─ Candidate / Variant Evaluation
```

## 全体構成

```text
Committed Git Snapshot
  ↓
Repository Snapshot Manager
  ↓
System Profile Draft / Feature Map / Feature-to-Code Mapping
  ↓
Probe Plan
  ↓
Isolated Experiment Workspace
  ↓
Trace / Test / Shadow / Evaluation comparison
```

既存の trace / shadow / evaluation / Generate & Evaluate は維持する。
追加レイヤーは、その前段で観測対象を決め、後段で source patch variant を比較する。

## 安全境界

- 読み取り対象は特定 commit に含まれるファイルだけとする。
- `README.md` と `docs/**` は設計意図、source は実装状態、tests は期待動作として区別する。
- System Profile と Feature の主張には path と行範囲の evidence を付ける。
- secrets、untracked files、working tree の未コミット変更は読まない。
- 対象リポジトリへ自動適用しない。検証済みProbe Patchはユーザーの明示承認後に限り、
  SnapshotとHEADの一致およびclean working treeを確認して適用できる。
- patch 実行は一時 worktree / sandbox 内で行い、network は既定で無効にする。
- LLM 評価だけで採用しない。テスト、複数 trace、人間レビューを併用する。

## 判断エンジンの原則

heuristic / rule-based 判定は、出力候補が少数かつ明示された有限集合に閉じる場合だけ
許可する。例は次の通り。

- file kind を `documentation | source | test | configuration` に分類する
- status を `proposed | approved | rejected` に遷移する
- 既知 decorator の有無を判定する
- command の exit code を success / failure に分類する

以下のような自由度のある判断には heuristic、keyword score、単純 similarity を
最終判定として使わず、外部 API の reasoning model を必須とする。

- System Profile / Feature の抽出と要約
- evidence からの設計意図の解釈
- Feature と code symbol の対応付け
- probe point、観測理由、副作用 risk の提案
- experiment variant の比較解釈と推奨

reasoning model が設定されていない、API call が失敗した、または structured output の
検証に失敗した場合は、heuristic にフォールバックせず処理を失敗させる。テストと
ローカル UI smoke では deterministic mock provider を利用できるが、production result
として保存・表示する際は mock であることを明示する。

各推論結果には provider、model、prompt/schema version、decision method
(`deterministic | reasoning_llm | manual`) を監査情報として保存する。

Control Server が読み取れる repository は container 内の `/repositories` 配下に限定する。
Docker Compose では `PROBE_REPOSITORY_HOST_ROOT` を `/repositories` へ mount し、
Dashboard ではこの配下から検出されたGit Repositoryを選択する。通常の解析は `git ls-tree` と
`git show <commit>:<path>` のみを使うため、未commitの変更やuntracked fileはAI入力に
含めない。元Repositoryへの書き込みは、検証済みProbe Patchをユーザーが明示承認した
場合に限る。

## データ契約

- `RepositorySnapshot`: repo path、commit SHA、include/exclude、read policy
- `FeatureProfile`: user value、success criteria、risk、evidence
- `FeatureCodeLink`: path、symbol、kind、confidence
- `ProbePlan` / `ProbePoint`: 観測理由、mode、副作用リスク、承認状態
- `ExperimentSummary` / `ExperimentVariant`: baseline、variant、metrics、状態

JSON Schema は [`shared/schemas/project_intelligence.schema.json`](../shared/schemas/project_intelligence.schema.json)
を参照する。

## 実装状態

Repository、Feature Map、Probe Planner、Experiments は実データAPIへ接続されている。
旧 `GET /project-intelligence` Mock endpoint は廃止した。LLM mock providerは自動テスト
とlocal smoke用途に限定し、reasoning必須処理では実結果を生成しない。

## 実装フェーズ

### Phase 6: Repository Understanding MVP

- System ごとに repository 設定を保存する。
- `git ls-tree <sha>` / `git show <sha>:<path>` で committed files のみ読む。
- evidence 付き System Profile Draft と Feature Map Draft を生成・保存する。
- draft 生成は reasoning model の LLM API を必須とする。

### Phase 7: Feature-to-Code Mapping MVP

- Python AST から module / class / function / decorator / route / test を抽出する。
- AST 抽出は決定的に行い、FeatureCodeLink の推論は reasoning model で行う。
- confidence とレビュー状態を保存する。

### Phase 8: Probe Plan / Temporary Patch MVP

- Feature ごとの probe 候補と副作用リスクを提示する。
- probe 候補、理由、risk の提案は reasoning model で行う。
- 承認された plan だけ一時 worktree に適用する。
- baseline / probed のテストと smoke command を比較する。

### Phase 9: Experiment Runner MVP

- baseline と source patch variants を隔離 workspace で実行する。
- command、env、timeout、artifact設定はpinned snapshot内の
  `probe-agent.yml`からのみ読み込む。
- networkは常に無効とし、sandboxを確立できない場合は実行しない。
- test、trace、shadow、evaluation、duration を同じ条件で比較する。
- 数値集計は決定的に行い、自由記述の比較解釈・推奨は reasoning model で行う。
- 採用候補 patch と根拠を提示するが、対象 repo には自動適用しない。人間が採用する場合は、
  完了済みの非baseline variantと判断根拠を明示して記録する。

## Flow Explorer（Issue #43, Phase 1）

API endpoint 等の入口から候補実行フローを決定的に構築し、ユーザーがノードを
選択して Probe Plan draft へ引き継ぐ UX。

- 入力は pinned snapshot の `code_symbols` と indexed Python source のみ。
  working tree / untracked / 秘密情報は新たに読まない。
- call edge は最小限の Python AST 解析（direct call / `self.method()` /
  module-qualified call / `await`）で抽出する。
- 静的に一意確定できない呼び出しは `unresolved`（`target_node_id=None`）として
  保持し、確定経路として扱わない。external/builtin 呼び出しは graph に含めない。
- node / edge の ID と並び順は入力順に依存せず安定。LLM は使わず要約・タイトルも
  決定的に生成する（`decision_method` は実質 deterministic、from-flow plan は
  `manual`）。
- safety denylist に一致する node は `risk=high` / `denylist_hit` を付与し、
  Probe Plan draft でも承認不可（既存の probe point 承認ガードを再利用）。

エンドポイント:

- `GET  /repository/flow-entrypoints` — snapshot 単位で http route / public
  function の入口を列挙する。
- `POST /repository/flow-graphs` — `entrypoint_type` / `entrypoint_id` /
  `max_depth` / `max_nodes` を受け取り flow graph を構築する。
- `POST /repository/probe-plans/from-flow` — 選択した node と observation /
  mode preference を既存の `probe_plans` / `probe_points` へ
  `decision_method=manual` で変換する。新規テーブルは追加しない。

フロー選択・Plan 作成だけでは patch 生成・適用・実行は開始しない。承認以降は
既存の Probe Planner（Approve → Patch → Validate → Apply）へ接続する。
新しい環境変数は追加していない。

### Phase 2: 外部境界と runtime overlay

- **外部境界の明示的分類**: `dispatch`（`delay` / `apply_async` / `enqueue` /
  `add_task` / `send_task` / `publish` / `produce` / `schedule` /
  `create_task` 等の明示的な非同期/queue API）、`http`（`requests` / `httpx` /
  `aiohttp` / `urllib`(3)）、`database`（`sqlalchemy` / `psycopg(2)` /
  `sqlite3` / `pymongo` / `redis` / `asyncpg` / `cursor` / `db` / `conn` /
  `connection`）、`filesystem`（`shutil` / `pathlib` / `open`）。これらは
  route decorator や safety denylist と同じく**明示的な有限列挙集合**で判定し、
  未知の外部呼び出しは推測せず drop する。dispatch は `resolved`、I/O は base名
  ベースのため `inferred`。外部境界ノードは leaf として表示し、in-repo シンボル
  ではないため直接 instrument できない（from-flow で選択すると 400）。
- **runtime overlay**: `component_id` を持つ（既に instrument 済みの）ノードに
  実 trace / evaluation の集計（trace 件数、error 件数、ok/ng 件数）を重ねる。
  payload は露出せず集計のみ。
- **edge 境界 / 複数 node 選択**: in-repo caller に対して observation=boundary を
  指定でき、複数ノード選択時は latency breakdown 用途のヒントを表示する。

### Phase 3: observed-path overlay と多言語拡張

- **observed-path overlay**: 実 trace を持つノードを observed として静的候補フロー
  に重ね、各候補の observed / unobserved ノードを diff 表示する。trace schema は
  call chain を保持しないため、ここでの「observed」は「runtime で観測済みの
  ノード」を意味し、完全な実行系列の再構成ではない。
- **多言語拡張の seam**: call-site 抽出を拡張子→parser の registry
  （`register_parser` / `parse_call_sites` / `supported_extensions`）に分離した。
  現状は Python のみ登録。symbol / entrypoint 抽出の多言語化は将来課題。

これらは追加の DB テーブル・環境変数を必要としない。

### edge 選択・snapshot 固定・選択前プレビュー（#46）

- **node / edge 両対応の選択**: `FlowProbeSelection` は `target_type`(`node` /
  `edge`) を持ち、`node_id` または stable `edge_id` で対象を指す。`FlowEdgeOut`
  には入力順非依存の `edge_id` を付与。edge selection は in-repo caller を patch
  対象とし、reason に呼び出し境界（before/after）と callee / edge_type / line を
  記録する。external boundary node は直接 instrument せず、その呼び出し edge を
  介して caller を観測する。external 境界をまたぐ edge は side-effect risk を
  最低 medium に引き上げる。
- **snapshot / commit 固定**: `FlowGraphRequest` / `ProbePlanFromFlowRequest` は
  任意で `snapshot_id` / `commit_sha` を受け取り、現在の latest ready snapshot と
  一致しなければ 409（stale）を返す。Dashboard は表示中 graph の
  snapshot_id / commit_sha を Plan 作成時に送り、409 を検知して再読み込みを促す。
- **選択前プレビュー**: 各 node / edge に決定的な preview metadata
  （recommended mode・captured data・redaction・replayability・estimated event
  volume・side-effect risk・denylist hit）を `ProbePreviewOut` として付与する。
  estimated volume は runtime trace 件数から導出。external node は
  instrument 不可のため preview を持たない。LLM 推論は用いない。

### backend entrypoint の種類別検出とフィルター（#48）

Flow Explorer の入口を HTTP route と public function だけでなく、backend として
意味のある種類に分類して列挙・フィルターする。分類は `code_symbols` に既に保存
済みの decorator / route 情報のみから決定的に行い、新しい DB テーブルや環境変数は
追加しない。

- **category（UI 表示・フィルター語彙）**: `api` / `message_queue` /
  `scheduled_job` / `cli` / `function`。各 `FlowEntrypointOut` は `category`・
  `framework`・`operation`・`confidence`・`evidence` を持つ。`entrypoint_type` は
  graph builder の dispatch key（`http_route` / `public_function` /
  `message_queue` / `scheduled_job` / `cli`）として従来通り保持し、後方互換を保つ。
- **決定的な検出（有限列挙集合）**: route decorator や safety denylist と同じく、
  既知の framework decorator のみを根拠にする。
  - API: 既存の `route_path` / `route_method`（FastAPI/Starlette のメソッド
    decorator、Flask の `route`）。`operation` は `METHOD path`。
  - Message Queue: Celery（`@app.task` / `@shared_task`）、Dramatiq（`@actor`）、
    Huey（`@huey.task`）、RQ（`@job`、generic 名のため confidence を下げる）。
  - Scheduled Job: APScheduler（`@scheduled_job`）、Celery/Huey の
    `@periodic_task`、`@cron`（framework 未確定は confidence を下げる）。
  - CLI: Click / Typer（`@command` / `@group`）。
  - 上記いずれにも該当しない module-level public function は `function`。
  decorator を伴わない命名だけの推測（例: `consume_*`）は確定 entrypoint にせず、
  通常の public function 扱いにとどめる。不確実な一致は `confidence` を下げ、
  `evidence` に判定理由を残す。
- **API**: `GET /repository/flow-entrypoints` は `category`（または別名
  `entrypoint_type`。`api` などの category 語彙、`http_route` などの dispatch 型の
  両方を受理）と `q`（部分一致）で絞り込める。フィルター一致は**全件返す**。
  `total` は未フィルター総数で、サーバー側で固定上限により黙って欠落させない。
- **graph builder**: `message_queue` / `scheduled_job` / `cli` は handler symbol を
  起点に既存と同じ BFS で graph を構築する。`api` / `function` は alias として
  正規化する。未対応 type は `FlowEntrypointType` の Literal 検証で 422 になる。
- **Dashboard**: 左ペインに `All / API / Message Queue / Scheduled Job / CLI /
  Function` の種類別フィルターと件数表示（`N of M`）を追加。symbol 名ではなく
  入口として意味のある label（`POST /documents/analyze`、`Celery: analyze_task`、
  `CLI: import-documents` 等）を主表示し、フィルター結果はスクロール可能な一覧で
  全件確認できる。

### backend-entrypoint-first への再設計（#51）

#48 の種類別フィルターは「全 public function の一覧 + 種類フィルター」のままで、
backend entrypoint が薄い repository では function の素のリストが事実上の主表示に
なってしまっていた。#51 で Flow Explorer を backend entrypoint 起点に再設計する。

- **`app/entrypoint_discovery.py`（新規）**: FastAPI/Starlette の
  `APIRouter(prefix=...)` + `app.include_router(router, prefix=...)`、Flask の
  `Blueprint(url_prefix=...)` + `app.register_blueprint(bp)` を AST 上で解決し、
  同一ファイル内・モジュール間の import を解決して router の mount prefix を合成
  する（`discover_api_routes`）。route 自体の decorator のみでは捉えられない
  「実際に公開される URL」を決定的に組み立てる。decorator は読めるが router
  variable を解析できなかった route は、handler シンボル単位で重複排除した上で
  decorator-only の `entrypoint_id` のまま fallback として残す。
  Message Queue / Scheduled Job / CLI の検出は #48 の
  `enumerate_symbol_entrypoints` をそのまま再利用する。
- **`EntrypointDiscovery`**: `entrypoints`（api/message_queue/scheduled_job/cli =
  backend entrypoint）と `functions`（public function、Advanced fallback 専用）を
  分離して保持する。`backend_total`、`counts`（種類別件数）、
  `indexed_function_count`、検出framework一覧、`diagnostics`
  （backend entrypoint が0件のとき "No backend entrypoints detected..."、
  Python indexer のみであること、OpenAPI spec が見つからないこと等を決定的な
  固定メッセージで通知）を返す。
- **`code_entrypoints`（新規 system-scoped テーブル）**: 検出結果を snapshot 単位
  で永続化する。`GET /repository/flow-entrypoints` が呼ばれた際、その
  `snapshot_id` に対する `intelligence_runs(run_type='entrypoint_index')` が
  存在しなければ deterministic 判定として 1 度だけ INSERT する（`decision_method=
  'deterministic'`、`is_mock=0`）。2 回目以降の GET は再計算結果を返すのみで
  重複 INSERT しない。`code_entrypoints` は `system_id` でスコープし、他 system の
  行を返さない（isolation test あり）。discovery 自体は読み取り専用で対象
  repository には書き込まない。
- **API 契約変更**: `FlowEntrypointsOut.entrypoints` は backend entrypoint のみを
  返すようになった（function は含まれない）。function は `functions` フィールドに
  分離し、`include_functions=true` または `category=function` を明示しない限り
  空配列のままにする（Advanced 専用、デフォルト非表示）。`counts` /
  `indexed_function_count` / `has_backend_entrypoints` / `frameworks` /
  `diagnostics` を追加。`total` は backend entrypoint の総数（function を含まない）。
- **`POST /repository/flow-graphs` / `POST /repository/probe-plans/from-flow`**:
  graph builder には `discover_entrypoints` が返す composed entrypoint 一覧
  （backend + function）を渡し、合成済みの URL（例: `POST:/api/documents/analyze`）
  で entrypoint を解決できるようにした。
- **Dashboard**: 左ペインの種類フィルターから Function を外し、既定では backend
  entrypoint のみを表示する。function は "Show Advanced" トグルでのみ表示され、
  「raw function の利用は discovery が不完全であることのシグナル」と明示する。
  backend entrypoint が 0 件のときは diagnostics をそのまま表示し、function の
  一覧を黒幕的な代替表示として出さない。

### LLM 支援によるフレームワーク非依存の API 検出（Scan API definitions）

決定的 AST 検出は FastAPI/Starlette/Flask しか認識しないため、Django/DRF・
Express/NestJS・Go・Rails 等を使う repository では route が 0 件になる。これを
補うため、Repository ページに **「Scan API definitions」** を追加する。reasoning
model が snapshot を見て「どこに API 定義があるか」を判断し、**API 定義を抽出する
正規表現**を生成する。正規表現は pinned snapshot に対して決定的に適用され、
具体的な entrypoint（method/path/file/line）を抽出する。

CLAUDE.md 原則 6 / reasoning-llm skill に従う:

- 開放的な判断（どのファイルが API を定義し、どの正規表現が一致するか）は LLM が
  行い、**正規表現は決定的なフィルター**として適用する。
- mock / 非 reasoning model は **fail closed**（heuristic fallback なし）。
- 生成された正規表現は **レビュー可能な成果物**として永続化し、決定的 AST の事実
  とは `source` で分離する。

実装:

- **`app/api_scan.py`（新規）**: `build_snapshot_digest`（file inventory + API を
  定義しそうなファイルの先頭サンプルを文字数上限付きで送る決定的な digest）、
  `generate_api_scan`（reasoning model 呼び出し・mock fail closed）、
  `parse_scan_response`（構造化出力の厳密検証: 正規表現の compile・長さ上限・
  named group 整合・glob は repository 相対・ReDoS シグネチャ拒否）、
  `apply_patterns`（**ReDoS 安全**: 行単位・行長上限付きで matching し、最悪
  backtracking を 1 行に限定。`(?P<path>…)` を route path、`(?P<method>…)` /
  `method_constant` を HTTP method として抽出）。
- **永続化（system-scoped・追加のみ）**: `code_entrypoint_patterns`（生成された
  正規表現と framework/language/reason/confidence/match_count/examples）、および
  `code_entrypoints` に `source`（`deterministic` / `reasoning_llm`）と
  `pattern_id` 列を追加（既存 DB には `ALTER TABLE` で後方互換マイグレーション）。
- **API**: `POST /repository/api-scan`（`intelligence_runs(run_type='api_scan',
  decision_method='reasoning_llm')` を記録し、pattern と抽出 entrypoint を 1
  トランザクションで保存。再スキャンは当該 snapshot の `reasoning_llm` 行のみを
  置換し、決定的行には触れない）、`GET /repository/api-scan`（最新スキャン取得）。
  `GET /repository/flow-entrypoints` は永続化済みの LLM 由来 API entrypoint を
  `api` カテゴリへマージし、`source` を返す（決定的 route と衝突する id は
  決定的側を優先）。LLM 由来 entrypoint は handler symbol を持たないため、
  flow graph 構築時は 422 を返し「可視化のための一覧表示のみ」と明示する。
- **Dashboard**: Repository ページに「API Scan」タブを追加し、明示ボタンでのみ
  実行する。生成された正規表現・framework・match 件数・抽出件数・fail closed
  エラーを表示し、「LLM 生成のため要レビュー」と明記する。Flow Explorer では
  LLM 由来 API entrypoint に「LLM」バッジを付ける。
- **環境変数**: `API_SCAN_DIGEST_MAX_CHARS`（任意・既定 40000）で digest の文字数
  上限を調整する。reasoning model の選択は既存の `INTELLIGENCE_LLM_PROVIDER` /
  `INTELLIGENCE_LLM_MODEL`（未設定時は `LLM_PROVIDER` / `LLM_MODEL`）に従う。

## ソース由来の説明メタデータ（Issue #54）

Flow Explorer は API を probe 設定の候補として列挙できるようになったが、
ソースコードと「システムの目的・中核能力・補助/境界要素・probe 価値」を結ぶ
共有の説明レイヤーが欠けていた。#54 では、その説明の**原本を対象リポジトリの
ソース側（docstring）に置く**ための最小フォーマットと、pinned snapshot からの
**決定的な抽出規則**を定義する。`probe-agent` は説明をリポジトリ側に書き戻さず、
スナップショットから抽出したコピーを索引するだけである（原本の authoring 場所
にはならない）。

このメタデータは**著者が書いた事実（source-authored）**であり、CLAUDE.md 原則 7
に従って reasoning-model の解釈とは**保存・API の両方で分離**する。`origin` は
常に `source_authored` で、symbol index run の `decision_method` は
`deterministic` のままにする。自由文から意味を推測してはならない。

### フォーマット

module / class / function の docstring 内に、`probe-agent:` 行で始まる小さな
構造化ブロックを埋め込む。ブロック本体は marker よりも深くインデントした
YAML マッピングで、PEP 257 で正規化された docstring に対して解釈する。

```python
def build_flow_graph(...):
    """
    Build a candidate execution flow from a backend entrypoint.

    probe-agent:
      role: API endpoint for deterministic flow graph construction
      capability: execution-flow-understanding
      element_type: core
      consumers: [dashboard]
      operation_kind: analysis
      state_effects: [database-read]
      probe_value: Validate graph shape, unresolved edges, and external-boundary detection.
    """
```

すべての symbol で**任意**であり、ブロックが無ければメタデータは生成されない。

### 語彙

| キー | 型 | 説明 |
| --- | --- | --- |
| `role` | string（自由文） | API / backend entrypoint としての役割。原文のままコピーする。 |
| `capability` | string（自由文） | この symbol が属する中核能力の識別子。 |
| `element_type` | enum | 階層上の位置。`system` / `core` / `capability` / `element` / `supporting` / `boundary`。 |
| `system_purpose` | string（自由文） | 通常 module docstring に置く、システム全体の目的。 |
| `operation_kind` | enum | `analysis` / `read` / `write` / `mutation` / `io` / `orchestration` / `validation` / `other`。 |
| `consumers` | list[string]（自由文） | この能力の利用者（例: `[dashboard]`）。 |
| `state_effects` | list[enum] | 各要素は `none` / `database-read` / `database-write` / `network` / `filesystem` / `cache` / `external-api` / `queue`。 |
| `probe_value` | string（自由文） | probe する価値の説明。 |

enum / enum list は CLAUDE.md 原則 6 に沿って**明示的な有限集合**に限定し、
自由文フィールドは検証せずそのままコピーする。

### 抽出規則（決定的）

- 対象コードを**実行しない**。docstring は AST 上の文字列リテラルとして読む。
- pinned snapshot の committed files のみを対象とし、working tree は読まない。
- `probe-agent:` ブロックを検出し、YAML として `yaml.safe_load` する。
- 既知キーは型 / enum を検証し、`start_line` / `end_line`（snapshot 上のブロック
  行範囲）と原文 `raw_block` を保持する。
- **不正・未知のメタデータは決定的な index warning** として記録し、symbol index
  全体を失敗させない。
  - YAML パース失敗、マッピングでない、空ブロック → メタデータ無し + warning。
  - 未知キー、型不一致、enum 範囲外 → 当該フィールドを破棄して warning。妥当な
    フィールドは保持する。
  - 妥当なフィールドが 1 つも無い → メタデータ無し + warning。

### 永続化と API

- `symbol_source_metadata`（system-scoped・追加のみの新規テーブル）に、
  `snapshot_id` / `system_id` / `symbol_id` / `path` / `qualified_name` /
  ブロック行範囲 / 各フィールド / `raw_block` / `origin='source_authored'` を
  保存する。symbol index run の中で deterministic 事実として 1 トランザクション
  で書き込み、reasoning 出力テーブルとは分離する。
- `GET /repository/symbols` と `POST /repository/symbols/index` の
  `CodeSymbolOut.source_metadata` として typed に公開する。これにより次の
  hierarchy issue が型付きで参照できる。
- 不正メタデータは `symbol_index_warnings` に
  `"<qualified_name>: probe-agent metadata: <detail>"` 形式で残す。

### 非対象（#54）

- ソースの自動改変、リポジトリへのメタデータ書き戻し。
- LLM 生成メタデータ。
- drift スコアリングや完全な階層・refresh ワークフロー。
- 自由文からのヒューリスティックな最終分類。

## ソースハッシュによる来歴（Issue #55）

開発者向けの説明（#54 のソース由来メタデータや、後続 issue が作る能力/機能の
説明階層）は、実装が変わると drift する。「いつ説明を見直すべきか」を後続 issue が
判定できるように、説明が依存するソース事実に**決定的なハッシュ来歴**を付与する。
対象リポジトリは原本の source of truth のままで、`probe-agent` は **pinned
snapshot のコミット済み内容からのみ**ハッシュと抽出コピーを保存する（working tree
は読まない）。ハッシュは CLAUDE.md 原則 7 に従い reasoning-model の解釈とは分離する。

### ハッシュ種別

1 個の過負荷な値ではなく、用途別に明示的なハッシュ種別を使う。すべて sha256。

| ハッシュ | 対象 | 意味 | 変わる/変わらない |
| --- | --- | --- | --- |
| `file_content_hash` | ファイル | コミット済みファイル内容のハッシュ（snapshot が既に保持）。 | ファイル内のどの変更でも変わる。 |
| `symbol_source_hash` | symbol | symbol の正確なソース span（decorator + signature + body, コミット時のまま）のハッシュ。decorator がある場合は span 開始を先頭 decorator 行にする（API entrypoint の `@router.get(...)` 等は外部から観測される役割の一部のため）。`start_line` は表示・下流の行範囲用に def/class 行のまま。 | decorator・コメント・docstring・空白を含む span 内のどの変更でも変わる。 |
| `symbol_body_hash` | symbol | docstring を除去し `ast.dump`（属性なし）で正規化した構造のハッシュ。コメント・docstring・整形・行番号を**除外**。 | 構造的なコード変更でのみ変わる。コメント/docstring だけの変更では変わらない。 |
| `explanation_hash` | 説明ブロック | #54 の抽出済み `probe-agent:` ブロック文字列のハッシュ。 | 説明文の変更で変わる。 |

`symbol_body_hash` の正規化は決定的で、テストで保証する（コメントのみ変更・
docstring のみ変更で安定、実装変更で変化）。

### ハッシュが証明しないこと

- ハッシュの一致は**意味的な等価ではなく、変更シグナルにすぎない**。
- `symbol_body_hash` が等しくても挙動が同じとは限らない（呼び出し先の変更、
  グローバル状態、外部 I/O などは捉えられない）。逆に等価な書き換え（変数名変更等）
  でもハッシュは変わる。
- ハッシュの不一致は「見直しの候補」を示すだけで、drift の有無や程度は後続 issue が
  判断する（本 issue は drift スコアを計算しない）。

### 説明→ソース依存（source anchors）

各説明は、依存するソース事実を**source anchor の集合**として記録する:
`path` / 任意の `symbol` / 行範囲 / `file_content_hash` / `symbol_source_hash` /
`symbol_body_hash` / `explanation_hash`。#54 では説明はちょうど 1 つの symbol に
紐づくため anchor は 1 件だが、後続の階層的説明が複数 symbol に依存する場合に
備えて first-class なテーブルにしておく。

### 永続化と API

- `code_symbols` に `symbol_source_hash` / `symbol_body_hash` を追加（既存 DB は
  `ALTER TABLE` で後方互換マイグレーション）。`file_content_hash` は
  `snapshot_files.content_hash` を読み出しで合成する。
- `symbol_source_metadata` に `explanation_hash` を追加。
- `explanation_source_anchors`（system-scoped・追加のみの新規テーブル）に anchor
  集合を保存する。
- symbol index run を `schema_version='provenance-v1'` でバージョン管理する。
  #54/#55 以前に index 済みの snapshot は、`code_symbols` を作り直さず
  （feature-code link を cascade 削除しないため）にハッシュ・メタデータ・anchor を
  **決定的・追加のみ・冪等**にバックフィルする。アップグレードは
  `POST /repository/symbols/index` だけでなく、**read 経路でも**実行する
  （`GET /repository/symbols` / `GET /repository/explanation-anchors`）。
  これにより Dashboard は明示的な再 index なしに古い snapshot のハッシュ／anchor を
  得られる（flow-entrypoint discovery と同じ決定的 INSERT-on-read パターン）。
  schema_version が一致した以降は再計算しない。
- API: `GET /repository/symbols` と `POST /repository/symbols/index` の
  `CodeSymbolOut` に `file_content_hash` / `symbol_source_hash` /
  `symbol_body_hash` を、`SourceMetadataOut` に `explanation_hash` を公開する。
  `GET /repository/explanation-anchors` で anchor 集合を返す。

## ソース由来の能力階層（Issue #56）

System Profile / Feature Map draft と Flow Explorer の backend entrypoint に加え、
開発者が「このシステムは何のためにあり、どの中核能力が価値を生み、各能力をどの
実装要素が構成し、どの API/job/queue/file/外部境界が補助要素か」を理解するための
**ソース由来の能力階層**を追加する。#54 のソース由来説明メタデータと #55 のハッシュ
来歴を監査可能な土台として保つ。

```text
System Purpose
  Core Capability
    Capability Element  -> source symbol / API entrypoint
    Supporting Element  -> DB / filesystem / external HTTP / queue / scheduled job / CLI
```

### 構築方針（決定的優先・fail closed）

- **決定的ビルダー**は #54 の著者記述 `capability` フィールドだけで group 化し、
  自由文からは推測しない。`capability` を持たない symbol / API entrypoint は
  推測せず `unclassified` にする。
- **System Purpose**: module の `system_purpose` メタデータ（source_authored）を
  優先し、無ければ最新 System Profile draft を構造的に link する（structural）。
- **Capability Element**: `capability` を持つ symbol。`element_type` core/element
  は capability element、supporting/boundary は supporting element。
- **Supporting Element**: `state_effects`（database/filesystem/external-http/
  cache/queue）や、message_queue/scheduled_job/cli の backend entrypoint。
- **API entrypoint**: handler symbol が `capability` を持てば該当 capability の
  element として classified、無ければ `unclassified`。
- **reasoning model** は「unclassified な API entrypoint を既存 capability に
  振り分ける」open-ended grouping だけに使う。非 reasoning model・API 失敗・
  構造化出力の検証失敗は **fail closed**（heuristic fallback なし、run を failed に
  記録）。決定的な source-authored 事実は failed でも保持する。

### provenance と decision method

各ノードは由来を明示する。CLAUDE.md 原則 7 に従い `decision_method` は
`deterministic`/`reasoning_llm`/`manual` のいずれかに限定し、由来の区別は別フィールド
`provenance_kind` で表す:

| provenance_kind | 意味 | decision_method |
| --- | --- | --- |
| `source_authored` | #54 著者記述の説明から決定的に抽出 | `deterministic` |
| `structural` | 決定的な構造事実（entrypoint 境界、draft link 等） | `deterministic` |
| `reasoning_llm` | reasoning model による grouping 解釈 | `reasoning_llm` |
| `manual` | 将来の手動上書き（本 issue 未実装） | `manual` |

各ノードは source anchor（path/symbol/行範囲）と #55 のハッシュ
（file_content_hash/symbol_source_hash/explanation_hash）、reasoning 使用時は
provider/model も持つ。

### 永続化と API

- `capability_hierarchy_nodes`（system + snapshot scoped・新規テーブル）に
  `node_type`（purpose/capability/element/supporting）と `parent_id` で階層を保存。
  各 hierarchy run は `intelligence_runs(run_type='capability_hierarchy')` として
  監査記録する（reasoning 使用時は decision_method=reasoning_llm、provider/model/
  status/error を保存）。
- `POST /repository/capability-hierarchy/generate?use_reasoning=true|false` で生成、
  `GET /repository/capability-hierarchy` で最新階層を取得する。

### 既存概念との関係

- **System Profile / Feature Map draft（#23）** は reasoning model が生成する
  「外から見たシステム/機能」の draft。能力階層はこれを置き換えず、purpose の
  fallback ソースとして link するだけ（既存 Feature Map の挙動は変更しない）。
- **FeatureCodeLink（#24）** は Feature draft と code symbol の reasoning による
  対応付け。能力階層は **source-authored メタデータ起点**で symbol/entrypoint を
  capability に構成する点が異なり、決定的事実と reasoning 解釈を `provenance_kind`
  で分離する。両者は補完的で、後続の API role card・probe 選択コンテキスト・
  refresh 推奨の意味層となる。`review_status='accepted'` の FeatureCodeLink が
  symbol を Feature に結びつけている場合は、その `feature_id` を該当ノードの
  provenance に決定的に付与して Feature Map と接続する（複数候補は confidence 最大）。
- **ハッシュ来歴の網羅性**: capability element だけでなく、message_queue /
  scheduled_job / cli の supporting 境界も handler symbol が解決できれば
  `symbol_id` と #55 ハッシュ（file/source/explanation）を持ち、後続の drift 検出に
  参加できる。

## 説明の drift 検出（Issue #57）

ソース由来の説明（#56 の能力階層、API role、probe 推奨）は実装が変わると stale に
なる。「いつ説明を見直すべきか」を **#55 の決定的ハッシュ来歴**だけに基づいて通知する。
意味的な推測・embedding・heuristic 類似は使わない。**ハッシュの drift は「見直しの
トリガー」であり、「説明が間違っている」という判定ではない。**

### 仕組み

階層を生成した時点（base snapshot）でノードに記録した
`file_content_hash` / `symbol_source_hash` / `explanation_hash` を、より新しい
pinned snapshot（target）の事実と比較する。anchor の対応付けは安定識別子
（`path` + `qualified_name`）で行い、source 行範囲は弱い証拠としてのみ扱い照合には
使わない。

### ステータス

- `fresh` — 記録した全ハッシュが target でも一致
- `stale` — いずれかのハッシュが変化（anchor 単位は changed/unchanged の二値）
- `partially_stale` — （集約レベルのみ）依存の一部だけが変化
- `missing_source` — 依存していた file または symbol が消えた（削除/rename）
- `unknown` — 比較可能なハッシュを持たないノード（draft 由来の purpose 等）

### drift スコア（保守的・文書化済み）

ある capability/system の drift は依存集合から導く（二値ではなく比率と影響 anchor を返す）:

- `symbol_deps_changed / symbol_deps_total`（symbol ソースハッシュの変化）
- `file_deps_changed / file_deps_total`（file 内容ハッシュの変化・**distinct path** で計上）
- `explanation_blocks_changed / explanation_blocks_total`（説明ブロックの変化）
- `missing_anchors / total`（消えた anchor）
- `mismatch_ratio = (stale + missing) / comparable`、ここで
  `comparable = fresh + stale + missing`

集約ステータスは保守的に決定する: `comparable=0` なら `unknown`、変化ゼロなら
`fresh`、全 comparable が missing なら `missing_source`、全 comparable が変化なら
`stale`、それ以外（一部変化）なら `partially_stale`。
変化したハッシュは「review needed」を意味し、「説明が誤り」ではない。

### API

- `GET /repository/capability-hierarchy/drift?target_snapshot_id=`（任意・既定は
  最新 ready snapshot）。最新の能力階層 run を base とし、target と比較した
  system / capability / anchor 各レベルの drift（counts・ratio・影響 anchor・
  `is_review_recommended`・任意の `review_note`）を返す。drift は決定的な再計算で
  あり新規テーブルは持たない（永続化された階層ノードと snapshot 事実から導出）。

本 issue は決定的に留める。reasoning model が説明を更新する作業は、別 issue として
run metadata 永続化と fail-closed 付きで明示的に行う（本 issue では非対象）。

## リポジトリ設定案

設定例は [`probe-agent.example.yml`](../probe-agent.example.yml) を参照する。
実行コマンドは自動推測せず、この設定で明示する。
