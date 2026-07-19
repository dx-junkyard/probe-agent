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

System Understanding パイプラインが実装されている（Issues #77–#92）:
- Unified System Understanding API (`GET/POST /repository/system-understanding`)
- Pipeline checklist: 8 ステップの完了状態追跡
- Docs-code gap worklist: severity, structured refs, next actions
- Metadata coverage: symbol/entrypoint 単位のメタデータ付与率
- Cross-page navigation: System Understanding ↔ Capability Map ↔ Feature Map ↔ Flow Explorer
- Source-authored `probe-agent:` metadata による dogfooding（15 ファイル）
- 詳細は [`docs/system-understanding-navigation.md`](system-understanding-navigation.md) を参照

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

- #54 単体としてのソース自動改変、リポジトリへのメタデータ書き戻し。
  （CLAUDE.md 原則8で許可される対話的interview→隔離worktree→reviewable diff/PR
  というフローは別issueで扱う。#54はあくまで「既存メタデータの決定的抽出」のみ。）
- LLM 生成メタデータをそのまま `source_authored` として保存すること。
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
- `GET /repository/capabilities/{capability_key}/context`（Issue #175）は
  capability detail パネル向けに gap / probe plan / experiment を集約して返す。
  新しい表現は発明せず、既存の System Understanding gap 表現（`capability_key`
  一致でフィルタ）をそのまま再利用する。probe plan / experiment は
  `capability_hierarchy_nodes.feature_id`（その capability_key を持つ行）との
  等値結合のみで拾い、experiment はそこで見つかった plan の `feature_id` に
  等値結合する。曖昧一致・推測マッチはしない（CLAUDE.md 原則 6）。observed
  traces の集約は component_id ↔ capability の deterministic な対応が未整備の
  ため対象外。

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
  最新の **symbol-indexed** な ready snapshot）。最新の能力階層 run を base とし、
  target と比較した system / capability / anchor 各レベルの drift（counts・ratio・
  影響 anchor・`is_review_recommended`・任意の `review_note`）を返す。drift は
  決定的な再計算であり新規テーブルは持たない（永続化された階層ノードと snapshot
  事実から導出）。
- **target は symbol index 済みに限定する**。snapshot は index 前に `ready` になる
  ため、未 index の snapshot を target にすると symbol 事実が空になり、各 symbol
  anchor が `missing_source`（削除/rename）と誤判定され false-positive な review
  推奨が出る。これを避けるため、既定 target は最新の index 済み snapshot（無ければ
  base に fallback）とし、明示指定した target が未 index の場合は 409 を返す。

本 issue は決定的に留める。reasoning model が説明を更新する作業は、別 issue として
run metadata 永続化と fail-closed 付きで明示的に行う（本 issue では非対象）。

## Flow Explorer の API Role Card（Issue #58）

API は probe 設定の entrypoint として選べるようになったが、開発者が「どこを probe
するか」を選ぶ前に各 API の**システム内での役割**を理解できる文脈が必要だった。#58
は Flow Explorer に **API Role Card** を追加し、#56 の能力階層と #57 の drift を
そのまま消費して entrypoint 選択時に表示する。UI で新しい階層意味論を発明しない。

### カード内容（backend entrypoint ごと）

- 所属 capability と分類（classified / unclassified / unknown）
- element type（core / element / supporting）・role・operation kind
- consumers・state effects・boundaries（state effects から導出）・probe value
- 同じ capability の他の実装要素（flows through）
- **provenance**（source-authored / deterministic AST / reasoning-model
  interpretation / unknown を可視のバッジで区別）
- **freshness**（#57 の drift status と「N of M source anchors changed」）。
  drift はグラフ/probe 操作を**ブロックしない**。
- LLM scan 由来で handler が解決できない entrypoint は **review-needed** を明示し、
  実行可能なグラフを示唆しない（`handler_resolved=false`）。

### API

- `GET /repository/api-role-cards` が backend entrypoint（api / message_queue /
  scheduled_job / cli）ごとの role card を返す。各カードは
  `(entrypoint_type, entrypoint_id)` で `FlowEntrypoint` と join できる。
- 分類は階層ノード（reasoning grouping を反映）を優先し、無ければ handler の #54
  メタデータに fallback する。drift は #57 と同じく **symbol-index 済みの最新
  snapshot** を target にし、classified カードは capability 集約 drift、それ以外は
  ノード単位 drift を表示する。
- 階層 entrypoint ノードは base snapshot の `code_entrypoints` 行 id を参照する
  （snapshot 間で不安定）ため、論理 `(entrypoint_type, entrypoint_id)` に変換して
  現 snapshot の entrypoint と対応付ける。
- snapshot/symbol が無ければ空のカード集合を返す（エラーにしない）。

非対象: メタデータ authoring UI、自動 refresh/再生成、ソース書き換え、既存
Feature Map ページの置き換え。

## 説明の refresh 提案（Issue #59）

#57 は説明が古くなった（hash が drift した）ことを**検出**するだけで、説明レイヤ
を更新する助けにはならない。#59 はこのメンテナンスループを明示化する: 古くなった
階層ノード / API Role Card に対し、reasoning model が**更新案（提案）**を生成する。
提案は**あくまで suggestion** であり、probe-agent は対象リポジトリを書き換えない。
開発者がレビューしてソースの docstring を手で更新し、次の snapshot が更新後の説明を
再 index する。

### コンテキストパック（決定的に構築）

提案生成のために以下を集めて reasoning model に渡す:

- 旧説明ブロック（`symbol_source_metadata.raw_block` の逐語コピー）と旧パース済み
  メタデータ
- 変化した source anchor と、捕捉時・現在の hash（#55）
- pin された snapshot から読んだ**現在のソース断片**（symbol 範囲。symbol が消えて
  いれば空 → 「ソースが無い」と提案に明記）
- 決定的な構造ファクト（route method/path・operation・category・capability 等）

### fail closed と語彙の制約

- mock / 非 reasoning モデルは**閉じて失敗**し、推測は永続化しない（reasoning-llm
  skill）。失敗 run は `intelligence_runs` に残り可視化される。
- 提案メタデータの enum フィールド（`element_type` / `operation_kind` /
  `state_effects`）は #54 と同じ有限語彙で検証する。未知の enum 値やキーを含む提案は
  **拒否**する（決定的判断は有限集合に閉じる、CLAUDE.md 原則 6）。

### API

- `POST /repository/explanation-refresh` が `node_id` か論理
  `(entrypoint_type, entrypoint_id)` で対象ノードを指定して提案を生成する。drift が
  stale / missing_source のときのみ生成し、fresh なら 409 を返す。target snapshot は
  #57 と同じく symbol-index 済みのものに限る（未 index は 409）。
- `GET /repository/explanation-refresh` が直近の提案一覧を返す。
- レスポンスは常に `review_required=true` と review note を含み、「開発者がレビュー
  してソースへ適用する必要がある」ことを明示する。提案は
  `explanation_refresh_proposals`（system scope）に旧説明・提案説明・変化 anchor・
  drift 理由・provider/model/prompt/schema・捕捉/現在 hash と共に永続化する。
- Flow Explorer の Role Card に「Propose explanation refresh」操作を追加し、drift が
  review 推奨のときに提案（旧説明 vs 提案説明 vs 提案メタデータ）と review note を
  その場で表示する。

非対象: 自動ソース編集、コミット作成、バックグラウンドでの暗黙 refresh、reasoning
モデル不在時の heuristic fallback。

## Capability Map（Issue #62）

#54-#59 で構築した source-backed な能力階層（System Purpose → Core Capability →
Capability Element / Supporting Element）は、これまで Flow Explorer の API Role
Card（entrypoint を選んだ後のローカル文脈）からしか見えなかった。#62 は逆方向の
ナビゲーション、つまり「システムの目的・中核能力から、それを実装する API / 関数 /
境界 / probe フローへドリルダウンする」体験をダッシュボードに追加する。

- ダッシュボードに **Capability Map** ページ（`/capability-map`）を追加する。左側に
  System Purpose と Core Capability でグルーピングしたツリー、右側に選択ノードの
  詳細パネル（provenance バッジ、freshness/drift、source anchor の path + line range、
  受理済み `FeatureCodeLink` の feature id、reasoning model 情報）を表示する。
- 階層が未生成のときは、前提条件（snapshot 作成・symbol index・System Profile
  Draft 生成）を順序付きチェックリストとして表示する（`useLatestSnapshot()` /
  `useSymbols()` / `useLatestDrafts()` の既存状態をそのまま再利用し、新しい判断や
  API は追加しない）。実行順序が文章説明だけでは伝わりにくかったため、各項目の完了
  状態を可視化して `Repository` ページへ誘導する。その上で
  `Generate capability hierarchy`（`use_reasoning` 任意）操作を提供する。
- フロントエンドのフック: `useCapabilityHierarchy()` /
  `useCapabilityHierarchyDrift()` / `useGenerateCapabilityHierarchy()`。
- 永続化済み階層ノードは snapshot-local な `code_entrypoints` の DB row id を保持して
  おりリンクに使えない。`GET /repository/capability-hierarchy` の各ノード provenance に
  安定した論理 entrypoint（`entrypoint_type` / `entrypoint_ref`）を付与し、API /
  message_queue / scheduled_job / CLI に紐づく要素から Flow Explorer を開けるように
  する。これは決定的な構造リンクで、新しい主張ではない。
- Flow Explorer は `entrypoint_type` / `entrypoint_id` クエリパラメータを受け取り、
  一致する entrypoint を自動選択してフローグラフを構築する。そこから既存の
  node/edge 選択と Probe Plan draft ワークフローへ継続できる。
- drift が review 推奨のノードでは #59 の「Propose explanation refresh」を再利用し、
  提案のみ（ソースは書き換えない）であることを明示する。
- source-authored / structural / reasoning_llm / manual の provenance は視覚的に区別
  したまま維持する。

非対象（初版）: Capability Map ページ上での `probe-agent:` メタデータの直接編集、
このページからの自動ソース書換、Feature Map ページの置換、自由文からの heuristic
な能力グルーピング。（CLAUDE.md 原則8の対話的interview→隔離worktree→reviewable
diff/PRフローは別issueのスコープであり、#62 のCapability Mapページ自体には含まない。）

## システム理解インタビューの永続化（Issue #67）

#66 の「開発者と推論モデルがシステムの目的/能力を会話し、その流れで対象シンボルの
`probe-agent:` docstring メタデータ（#54 語彙）と Probe Plan（#25 モデル）を一緒に
提案する」フローのための、純粋な永続化＋契約レイヤ。Decision Workspace の #35 と同じ
位置づけで、対話・コンテキスト構築・worktree への materialize は別の #66 子issueが担う。
**この issue は LLM 呼び出しも worktree 書き込みも一切行わない。**

新しい System スコープのテーブル（すべて additive）:

- `interview_session` — `system_id` と pin された `snapshot_id` に紐づく。status は
  `open` / `proposals_ready` / `materialized` / `closed`。
- `interview_message` — 順序付き会話ターン（role / content と、任意の
  `intelligence_run_id` 参照）。
- `interview_proposal` — 提案シンボル 1 件につき 1 行。提案された `probe-agent:`
  メタデータブロック（#54 語彙）と Probe Plan フィールド（#25）を持ち、`decision_method`
  と項目ごとの承認状態 `approval_state`（`proposed` / `approved` / `rejected` /
  `edited`）を持つ。

ルール:

- メタデータブロックの有限フィールド（`element_type` / `operation_kind` /
  `state_effects`）は #54 と同じ有限語彙で検証する。`role` / `capability` /
  `system_purpose` / `probe_value` は自由文。未知の enum 値や未知キーを含む提案は 422
  で拒否する。
- 監査メタデータ（provider / model / prompt_version / schema_version / source
  snapshot / timestamps / failure detail）は既存の reasoning-run 監査ストア
  `intelligence_runs`（run_type=`interview_proposal`）に格納し、各 message / proposal
  から `intelligence_run_id` で参照する。並行する監査ストアは作らない。
- 新規に保存される proposal の `decision_method` は `reasoning_llm` 既定。この issue は
  `manual` を設定しない（承認遷移は別 issue）。
- CRUD/read API: セッション作成（system + pin された snapshot に束縛）、メッセージ追加、
  proposal の一覧/読み取り、セッション読み取り。proposal の生成（LLM）と承認遷移、
  worktree materialize は対象外。

合わせた proposal ペイロードの共有スキーマは
[`shared/schemas/project_intelligence.schema.json`](../shared/schemas/project_intelligence.schema.json)
の `InterviewCombinedProposal` / `InterviewProposal` / `InterviewSession` などを参照。

## インタビュー用コンテキストパック（Issue #68）

Decision Workspace の #36（Context Pack Builder）と同じ位置づけ。pin された snapshot の
既存データを決定的に集約し、LLM コンテキスト予算の範囲内で interview に渡すコンテキスト
パックを構築する。**LLM 呼び出しも worktree 読み書きも一切行わない。**

データソース（すべて既存テーブルの読み取りのみ）:

- `code_symbols` (#24 シンボルインデックス)
- `code_entrypoints` (#48/#51 エントリポイント発見)
- `symbol_source_metadata` (#54 抽出済み `probe-agent:` メタデータ)
- `capability_hierarchy_nodes` (#56 能力階層 — 分類済みかどうか)

出力の構造:

- `InterviewContextPack` — system_id + snapshot_id に紐づく。
- `InterviewSymbolItem` — 各シンボルに `classification` (`classified` / `unclassified`)
  と `has_metadata` を付与。既存メタデータフィールド（`element_type` / `role` /
  `capability` / `operation_kind` / `probe_value`）を含む。
- `InterviewEntrypointItem` — 発見されたエントリポイントも同様に分類。
- すべての項目に `InterviewEvidenceLocation`（snapshot_id + path + qualified_name +
  line span）を添付。
- `budget_max_chars` / `budget_used_chars` / `truncated` でコンテキスト予算の遵守状況
  を示す。

ルール:

- 未分類項目を先にソート（blank-page 領域を会話で優先するため）。
- 同一 snapshot + budget に対しては同じ出力を返す（決定的・再現可能）。
- 予算超過時はテール（分類済み優先）から決定的に切り詰め、`omission_notes` に記録。
- エンドポイント: `GET /interview/sessions/{id}/context-pack?budget=60000`。
- 共有スキーマ: `InterviewContextPack` / `InterviewSymbolItem` / `InterviewEntrypointItem`
  / `InterviewEvidenceLocation`
  （[shared/schemas/project_intelligence.schema.json](../shared/schemas/project_intelligence.schema.json)）。

## インタビューの構造化 Q&A（Issue #129）

対話ターン（Issue #69）の `interview_message` は会話ログの生記録であり、質問と回答の
対応関係を持たない。回答は `interview_session.open_questions` という JSON blob 上で
質問文の完全一致マッチにより消費されており、後から回答を訂正する手段がなかった。
本 issue は ID を持つ Q&A ペアの層を、既存の会話ログ・`open_questions` の**上に**追加する
（`interview_message` / `open_questions` は移行期間中そのまま残す）。

新テーブル `interview_qa`（System-scoped、この issue が所有）:

- `question_text` / `question_category`（`purpose|capability|api|probe_flow|general`）/
  `question_source`（`reviewer|dialogue|zero_base`）— いずれも有限集合（Principle 6）。
- `hypothesis` / `evidence_refs`（#128 の仮説・根拠と同形。#130 が実際に読んだ範囲の
  `char_count` を追加で保持する）。
- `answer_text` / `status`（`open|answered|revised|skipped`）/ `answered_by` /
  `superseded_by_id`。

**回答の修正は UPDATE ではなく新リビジョン行の追加。** 最初の回答は `open`/`skipped` の
行に直接記録するが（供養する対象がまだ無い）、既に `answered` の行を訂正する場合は
新しい行を `answered` として挿入し、旧行を `revised` にして `superseded_by_id` で新行へ
リンクする。旧回答は削除・上書きされず監査可能なまま残る（Principle 7）。

副作用として `interview_session.answers_revised_at` を立てる。これは Dashboard に
「理解を再構築してください」バナーを出すためのフラグで、**理解の再構築が成功した時のみ**
クリアされる（修正そのものでは自動的にクリアしない）。同様に、そのセッションに
生成済み提案（`interview_proposal`）が存在する場合は回答レスポンスに
`regeneration_recommended: true` を返すが、既存の提案は自動では無効化・再生成されない。

対話ターン（`POST /interview/sessions/{id}/dialogue-turn`）は次を行う:

1. モデルが返した `next_questions` を `question_source: "dialogue"` の `interview_qa`
   行として作成し、新規作成された ID を `created_qa_ids` として返す。既存の現行行と
   質問文が完全一致する場合は再挿入せず既存 ID を再利用する（構造的な完全一致
   dedupe、Principle 6）。移行期間中も残る `open_questions` JSON の各エントリには
   対応する `qa_id` を持たせ、Dashboard は ID で回答対象を指定する。
2. `answered_question`（テキスト完全一致、#123）に加えて `answered_qa_id`(ID 参照)を
   受け付ける。ID 参照は言い換えに強く、存在しない/既に回答済みの ID は無視されターン
   自体は失敗しない。テキスト一致は移行期間中は併存するが(その場合も一致した
   エントリの `qa_id` 行が answered に同期される)、いずれ削除される。
3. 回答済み Q&A の最新リビジョン一覧を対話プロンプトに
   「確定事実として再質問しない」指示付きで注入する(prompt `interview-v5` 以降)。
   意味レベルの重複質問の抑止は reasoning model への指示で行い、
   類似度ヒューリスティックでは行わない(Principle 6)。

理解構築(`POST .../update-understanding`)は reviewer の `open_questions` を
`question_source: "reviewer"` の `interview_qa` 行として登録し(同じく完全一致
dedupe)、`open_questions` JSON のエントリに `qa_id` を付与する。また理解レビュー
自体を `run_type: understanding_review` の `intelligence_runs` 行として成功・失敗
ともに記録し、生成/失敗メッセージを run にリンクする(Principle 7)。

エンドポイント: `GET/POST /interview/sessions/{id}/qa`,
`POST .../qa/{qa_id}/answer|skip|resume`。旧セッション（`interview_qa` 行が無い）は
一覧が空になるだけで、バックフィルは行わない。

**含まない:** 回答内容の自動解釈、質問の類似度マッチングによる自動重複排除、提案の
自動再生成・自動無効化。

共有スキーマ: `InterviewQA` / `InterviewQaEvidenceRef` / `InterviewQaAnswerOut`
（[shared/schemas/project_intelligence.schema.json](../shared/schemas/project_intelligence.schema.json)）。

## Q&A パネル回答の理解レビューへの還流とゲート拡張(Issue #263)

#129 の Q&A パネル(`interview_qa`)経由の回答は、対話ターン
(`generate_interview_turn`)のプロンプトには `answered_qa` / `unconfirmed_qa`
として注入されていたが、`update-understanding` が呼ぶ理解レビュー
(`generate_understanding_review`)には一切渡っていなかった。パネルでのみ
回答された質問は `interview_message` に書かれないため、対話ターンを経由
しない限り理解に反映されない欠落があった。

- **共有ヘルパー**: `routes/interview.py::_load_qa_pairs(conn, session_id,
  system_id)` が、対話ターンにインライン実装されていた `answered_qa` /
  `unconfirmed_qa` の取得・整形(#129/#142 と同じ SQL・同じ shape)を1箇所に
  切り出し、対話ターンと `update-understanding` の両方が同じ関数を呼ぶ。
  整形ルール自体は変更しない。
- **理解レビューへの注入**: `generate_understanding_review` が
  `answered_qa` / `unconfirmed_qa` を任意引数として受け取り、
  `interview_agent._trim_json` と同じ JSON-trim 方式(新設の
  `system_understanding_reviewer._trim_json` /
  `QA_PROMPT_MAX_CHARS = 4_000`、`interview_agent.GAP_AND_QUESTION_MAX_CHARS`
  と同じ予算)でプロンプトに追加する。プロンプトが変わるため
  `PROMPT_VERSION` を `understanding-review-v3` から `understanding-review-v4`
  に上げる(Principle 7 の監査可能性)。
- **確定後ゲートの拡張**: `update-understanding` の 409 ゲートは、これまで
  `answers_revised_at`(訂正パスでのみセット)だけを見ていたため、確定後の
  **初回**回答や、確定後に新規発行された Runtime Reality Check 質問への回答は
  ゲートを開かなかった。決定的な構造チェック(Principle 6、日時/行存在の
  比較のみ)として、そのセッションの `interview_qa` に
  `created_at > understanding_confirmed_at` または
  `answered_at > understanding_confirmed_at` の行が1件でもあれば
  `answers_revised_at` と同様に再構築を許可する。該当行が無ければ
  従来どおり 409 のまま(ヒューリスティックや自由文解釈は行わない)。

**含まない:** 409 のレスポンス形状変更、無効化ボタンなどの UI 変更、ターン
ごとの `current_understanding` 差分更新、`understanding_graph._is_similar_name`
の変更(いずれも Issue #229 のスコープ)。

## 状態に応じた無効操作抑止の完了(Issue #229)

Issue #229 の大半(409 構造化・`confirm-understanding` の role 分離・
`理解を更新`/`差分を生成`/`実態チェックを実行`/`差分を開く` の disabled+理由
表示)は commit `35f1bbc`ですでに実装済みだった。#263 が確定後ゲートを
「`answers_revised_at` のみ」から「確認済み時刻以降に作成/回答された
`interview_qa` 行の有無」へ拡張した際、Dashboard 側の disabled 条件
(`answers_revised_at` のみを見るローカル判定)は追随していなかった —
Q&A パネルのみで初回回答した場合や新規 Runtime Reality Check 質問に回答した
場合、サーバーは 200 を返すのに UI は無効のまま、というズレが残っていた。

- **単一の判定関数**: `routes/interview.py::_understanding_update_blocked(conn,
  session, system_id)` が、`update-understanding` の 409 チェックと
  `InterviewSessionOut.understanding_update_available` の両方から呼ばれる
  唯一の判定になった(以前は 409 チェック内にインライン実装されていた
  #263 拡張後の SQL をそのまま関数へ抽出しただけで、判定ルール自体は変更
  していない)。決定的な stage/timestamp/行存在チェックのみ(Principle 6)。
- **セッションシリアライザへの反映**: `_session_out` は `conn` を受け取るように
  なり(全 14 箇所の呼び出しを更新)、`understanding_update_available: bool`
  を返す。これにより Dashboard は 409 になるかどうかをローカルで再計算せず、
  サーバーが計算した同じ値を読むだけになる。
- **Dashboard**: `pages/interview.tsx` の `canRefreshUnderstanding` は
  `session.understanding_update_available` を直接使う(以前の
  `answers_revised_at` のみのローカル判定を置き換え)。理由文言・title は
  「新しい回答(修正・追加回答)がある場合にのみ、理解を再構築できます」に
  更新し、修正だけでなく初回回答/Reality Check 回答でも開くことを示す。
- **エラー表面化(項目C)**: `generate_understanding_review` はスキーマ検証
  失敗時、リトライ後も失敗した場合は catalog メッセージ
  (`invalid_review_response`)を返す実装がすでに存在し、生の Pydantic
  `ValidationError` 文字列が session の `last_error` に漏れることはない
  (回帰テストを追加して固定)。409 応答はすでに構造化されている
  (`code` / `message` / `next_action`)。

**含まない:** `understanding_update_not_available` 応答へ新しいフィールドを
追加すること、`ApiError.code`/`nextAction` を使った専用のエラー UI(現状は
disabled 化で 409 パスにほぼ到達しないため、既存のトースト表示のままで
充分と判断)。

## サーバー生成固定文言の INTERVIEW_LANGUAGE 対応(Issue #138)

#127 は LLM 生成テキストの出力言語を `INTERVIEW_LANGUAGE`(既定 `ja`)に従わせたが、
サーバー自身が `interview_message` / `interview_session` に書き込む固定文言(LLM 出力
ではない、例: 「理解の更新に失敗しました: …」「これまでの回答内容を確定し、提案生成
に進みます。」)は日本語固定のままだった。本 issue は `interview_language.py` に
有限のメッセージキー × 言語のテーブル `INTERVIEW_MESSAGES` と、テーブル参照のみで
文言を選ぶ `interview_message(key, language, **kwargs)` を追加し、
`routes/interview.py` の対象文言をすべて置き換える。

- 対象: 理解更新の失敗(LLM設定エラー / 理解グラフ未構築 / レビュー失敗)・成功時の
  要約メッセージ(ラベル「システムの目的」「主要機能」「主な確認事項」「推奨される
  次のステップ」を含む)・confirm-understanding の挿入メッセージ。
- 文言選択は決定的なテーブル参照のみ(Principle 6)。翻訳 API・推測は使わない。
  `INTERVIEW_MESSAGES` の全キーが `ja`/`en` 両方を持つことをテストで網羅チェックする。
- **不正な `INTERVIEW_LANGUAGE` への対処**: `resolve_message_language()` は
  `get_interview_language()` が `ValueError` を投げた場合、固定文言の組み立てに限り
  `ja` へ決定的にフォールバックする。これは reasoning 呼び出し
  (`generate_understanding_review` 内の `get_interview_language()`)の fail-closed
  挙動(#127 実装済み)を変更しない——設定不備はこれまでどおり `review.error` として
  報告され続けるが、その**報告メッセージ自体の組み立て**が同じ設定不備で壊れないように
  するための例外的なフォールバックである。

**含まない:** Dashboard 側文言の多言語化、`ja`/`en` 以外の言語追加。

## 質問前の軽量エビデンス調査（Issue #130）

#128 で対話ターンは仮説を持つが、確信度が低い論点ではシンボル名と行範囲だけを根拠に
質問してしまう。本 issue は対話ターンを決定的な2パス構成に分割し、質問を出す前に
pinned snapshot のソース断片を有界に読めるようにする。

- **パス1（`interview_agent.select_evidence_targets`, reasoning_llm）**: 「次の質問の
  ために読みたい `(path, start_line, end_line)` を最大 `MAX_EVIDENCE_TARGETS`(10) 件選ぶ、
  または `need_evidence: false` で不要と宣言する」構造化出力を要求する。`path` は
  context pack / current understanding に存在するものに限定し、範囲外の参照は
  `interview_agent._allowed_evidence_spans` と同じ許可集合で検証エラーとして fail する。
- **取得（`interview_evidence.read_evidence_snippets`, deterministic）**: 検証済みの
  対象を `git_ops.read_file_at_commit`（pinned commit からのみ、Principle 5）で読み出す。
  1ファイルあたりの行数上限（`INTERVIEW_EVIDENCE_MAX_LINES_PER_FILE`、既定 200）と
  合計文字数バジェット（`INTERVIEW_EVIDENCE_MAX_CHARS`、既定 20000）、ファイル数上限
  （`INTERVIEW_EVIDENCE_MAX_FILES`、既定 5）で抑制する。読み出し失敗（存在しない
  パス・git エラー）はターン全体を fail-closed にする — スニペット無しでの続行は
  存在しない（パス1が明示的に「不要」と宣言した場合のみスニペット無しで正常に進む）。
- **パス2（既存の `interview_agent.generate_interview_turn`）**: 読んだスニペットを
  プロンプトに追加し、#128 の仮説+確認質問を生成する。質問の `evidence_refs` は
  スニペットの範囲も引用可能になり、実際に読んだ範囲と一致すれば `interview_qa` の
  `evidence_refs[].char_count` に反映される。
- **監査**: パス1・パス2は別々の `intelligence_runs` 行として記録する
  (`run_type`: `interview_evidence_selection` / `interview_dialogue`)。
  対話ターンのレスポンスにも `evidence_run` / `evidence_used` として両方を返す。

判断区分: どこを読むか・質問内容は reasoning_llm。読めるか（実在・範囲・バジェット）は
deterministic。読んだスニペットは raw fact として保存され、LLM の解釈とは分離される。

**含まない:** 自由なツールユースループ、working tree・未コミット内容の読み出し、
読んだ内容に基づく提案の自動生成、対象リポジトリへの書き込み。

## 理解のリビジョンと差分レビュー(Issue #136)

`interview_session.current_understanding` は「理解を更新」のたびに**上書き**されており、
回答が理解にどう反映されたかが見えなかった。本 issue は新テーブル
`understanding_revision`(System-scoped、この issue が所有)を追加し、
`update-understanding` が成功するたびに1行追記する(上書きしない)。各行は
`intelligence_runs`(`run_type: understanding_review`)の該当 run に
`intelligence_run_id` でリンクし、「どの reasoning run が生んだ理解か」を
監査可能にする(Principle 7)。

- **決定的な構造差分(deterministic, `app/understanding_diff.py`)**: 6セクション
  (`system_purpose` / `core_capabilities` / `capability_elements` /
  `supporting_elements` / `api_boundaries` / `probe_flow_candidates`)ごとに、
  項目 `name` の完全一致のみで対応付け、追加 / 削除 / `confidence.level` の変化 /
  `summary` の変化有無を算出する。リネームは「削除+追加」として現れる
  (意味的な同一性判定はしない、Principle 6)。差分は常にオンデマンド計算で
  保存しない(常に再現可能)。
- **エンドポイント**: `GET /interview/sessions/{id}/understanding-revisions`
  (新しい順の一覧)、`GET /interview/sessions/{id}/understanding-diff?from=&to=`
  (`to` 省略時は最新リビジョン、`from` 省略時は `to` の直前リビジョン)。
  比較対象となる前リビジョンが無い場合(そのセッションの初回リビジョン、または
  リビジョンが1件も無い場合)は `has_previous: false` かつ `sections: []` を返す
  ——「全項目が追加された」という誤った差分にはしない。
- **既存セッションの初回リビジョン化**: この機能より前に理解を構築済みで
  リビジョン行が1件も無いセッションは、次の `update-understanding` 成功時に、
  上書き前の `current_understanding` を「初回リビジョン」として先に追記してから
  新しいリビジョンを追記する(この初回行に対応する reasoning run は無いため
  `intelligence_run_id` は NULL)。これにより「今回の更新でどこが変わったか」を
  失わずに差分できる。全既存セッションの一括バックフィルはしない。
- **保持上限**: `INTERVIEW_UNDERSTANDING_REVISION_LIMIT`(既定 20)を超えた古い
  リビジョンは追記のたびに決定的にローテーション削除される。削除は
  `intelligence_runs` に記録しない(監査対象は生成イベントであって、保持
  ローテーションではないため)。
- Dashboard: 「理解を更新」実行後、直前リビジョンとの差分サマリー(追加/削除/
  確信度変化の件数)をトースト表示し、詳細差分(セクションごとの追加=緑・
  削除=赤・確信度変化バッジ)を展開できる。回答修正(#129
  `answers_revised_at`)からの再構築後は、その文脈を明示するメッセージを添える。

**含まない:** LLM による差分の要約・解釈、理解の巻き戻し(閲覧のみ)、
提案・メタデータへの影響伝播の自動化。

共有スキーマ: `UnderstandingRevision` / `UnderstandingRevisionList` /
`UnderstandingDiff` / `UnderstandingDiffSection` / `UnderstandingDiffConfidenceChange`
([shared/schemas/project_intelligence.schema.json](../shared/schemas/project_intelligence.schema.json))。

## Runtime Reality Check（Issue #135）

#129/#130 までの Q&A 層は静的な情報(docs / code / 会話)しか見ておらず、`@probe`
が集めた実行時トレース(入出力・エラー・実行時間)は理解のレイヤーに一切還流して
いなかった。本 issue は承認済みの `probe-agent:` メタデータ / probe plan(role /
probe_value / state_effects / recommended_mode)と、同じ component_id のトレース
決定的集計を突合し、ズレの可能性がある論点を確認質問として `interview_qa`
(`question_source: "runtime"`)に返す。

判断区分(Principle 6):

- **集計(deterministic, `app/runtime_reality.py` の `aggregate_component_facts`)**:
  対象 System の `traces` テーブルから、直近 `RUNTIME_REALITY_CHECK_WINDOW_DAYS`
  (既定 7)日分の呼び出し数・エラー率・duration の p50/p90/p99・最終観測時刻を
  数値集計するだけ。トレースが1件も無い場合は `has_traces: false` で数値項目は
  null になる(「承認済み probe plan にトレースが無い」の生の事実)。解釈は
  一切行わない。System 分離は `system_id` での WHERE 句のみで保証する。
- **突合(reasoning_llm, `generate_runtime_reality_check`)**: 集計事実 + 承認済み
  メタデータを入力に、「ズレの可能性があり確認する価値のある論点」を最大
  `MAX_RUNTIME_QUESTIONS`(5)件、構造化出力で選ばせる。プロンプトに注入する
  集計+メタデータの JSON は `RUNTIME_REALITY_CHECK_MAX_CHARS`(既定 8000)で
  明示的にバジェット制限する(既存の `INTERVIEW_*_MAX_CHARS` パターン)。応答が参照する
  `component_id`/`qualified_name` は入力に存在するものへの完全一致でなければ
  fail-closed(存在しないシンボルの捏造を防ぐ、Principle 6)。モック/非推論
  モデル・API エラー・構造化出力検証失敗は、いずれもヒューリスティックな
  代替質問を生成せず run failed として記録される。

対象 component_id と承認済みメタデータの対応付けは、materialization(#71)が
書き込むのと同じ決定的変換(`routes/interview.py` の `_component_id_for`、
`qualified_name.replace(".", "_")`)を両者で共有して行う。曖昧マッチはしない。

エンドポイント:

- `GET /interview/sessions/{id}/runtime-facts` — 集計のみ(reasoning 呼び出しなし、
  `intelligence_runs` にも記録しない)。承認済み要素ごとの declared メタデータと
  facts を返す。
- `POST /interview/sessions/{id}/runtime-reality-check` — 手動実行のみ(定期実行や
  自動スケジューリングはしない)。実行のたびに成功・失敗を問わず
  `run_type: "runtime_reality_check"` の `intelligence_runs` 行を記録する
  (Principle 7)。失敗時は `interview_qa` 行を一切作らない(fail-closed)。
  そのセッションに未回答の `question_source: "runtime"` 質問が既に存在する場合は
  ノイズ抑制のため実行を抑止し(`skipped: true`)、`intelligence_runs` 行も作らない。

生成された質問は通常の `interview_qa` 行(`question_source: "runtime"`)として
登録され、回答は他の Q&A と同様に扱われる(理解の反映は「理解を更新」を通じてのみ
発生し、自動では反映されない)。根拠は `evidence_refs`(コード行範囲、runtime 質問
では空)とは別に新しい `runtime_evidence` カラム(JSON)に保存し、参照した集計値
(生の数値)とメタデータの出典(`path` / `qualified_name` / `proposal_id` /
`decision_id`)をそのまま質問カードに表示できるようにする。

**含まない:** メタデータ・probe plan・policy の自動更新、トレースの自動評価・
スコアリング(#9 の領分)、定期実行・自動スケジューリング、閾値ヒューリスティック
による「ズレ確定」判定。

共有スキーマ: `RuntimeTraceFacts` / `RuntimeRealityCheckItem` / `RuntimeRealityFacts`
/ `RuntimeRealityCheckRun`、`InterviewQA.question_source` への `"runtime"` の追加、
`IntelligenceRun.run_type` への `"runtime_reality_check"` の追加
([shared/schemas/project_intelligence.schema.json](../shared/schemas/project_intelligence.schema.json))。

## パス1で読んだエビデンス全件の監査永続化(Issue #137)

#130 の2パス方式では、パス1が選んだエビデンスをサーバーが pinned snapshot から読み、
パス2の質問生成に添えるが、従来は「モデルが質問の `evidence_refs` に引用した範囲」
だけが `interview_qa.evidence_refs[].char_count` として残り、**引用されなかった
エビデンス**は監査記録に残らなかった。本 issue は読んだ全スニペットを新テーブル
`intelligence_run_evidence`(System-scoped、この issue が所有)に永続化する。

- 1行 = 読んだ1スニペット(`path` / `start_line` / `end_line` / `char_count` /
  `truncated`)。スニペット本文は保存しない(サイズ・機密性の観点、Principle 5)。
  パス1の `intelligence_runs`(`run_type: interview_evidence_selection`)行に
  `intelligence_run_id` でリンクする。
- 書き込みは対話ターンの既存トランザクション内で行い、質問への引用の有無に関わらず
  記録する(raw fact と interpretation の分離)。引用の有無は
  `interview_qa.evidence_refs` 側の既存表示のまま変更しない。
- 読み出しが部分的に失敗した場合(`interview_evidence.EvidenceReadError`)は、
  失敗した対象より前に読めたスニペットを `partial_snippets` として保持し、
  ターン自体は従来どおり fail-closed のまま、読めた分だけを監査に残す。
- API: `GET /interview/evidence-runs/{run_id}/evidence`(System 分離、
  `interview_evidence_selection` run のみ対象、他 run_type/他 System は 404)。
  対話ターンのレスポンスにも `evidence_reads` として同じ内容を含める
  (既存の `evidence_used` は変更しない)。

**含まない:** スニペット本文の永続化・再表示、引用されなかったエビデンスの
`interview_qa.evidence_refs` への混入。

共有スキーマ: `IntelligenceRunEvidence` / `IntelligenceRunEvidenceList`、
`InterviewDialogueTurnOut.evidence_reads`
([shared/schemas/project_intelligence.schema.json](../shared/schemas/project_intelligence.schema.json))。

## 不明回答の継続と仮説の再確認(Issue #142)

インタビューで質問された開発者が「わかりません」「不明」など確定回答を持たない
ことがある。従来はその後の対話ターンで、LLM が仮説のために生成した `evidence_refs`
の検証に失敗すると `Question evidence validation failed` でターン全体が fail-closed
となり、会話が停止してしまうことがあった。本 issue は「不明回答」を有効な入力として
扱い、会話を止めずに仮説→再確認へ回す。

- **不明回答の記録(deterministic / manual)**: 「わからない」かどうかは自由文の
  ヒューリスティック判定ではなく、UI からの明示フラグ `answer_unknown`(有限の
  真偽値)で受け取る(Principle 6)。対話ターン(`answered_qa_id` + `answer_unknown`)
  および `POST .../qa/{qa_id}/answer`(`answer_unknown`)で指定でき、該当 `interview_qa`
  行は `answered` ではなく **新 status `unconfirmed`** で記録される。回答本文は空でも
  よい。これは「確定回答なし」という開発者の明示入力であり、エラーではない。
- **推論コンテキストへの還元(reasoning_llm)**: `unconfirmed` 行は後続ターンの
  プロンプトに「確定回答なし。確定事実として扱わず、根拠から仮説を立てて確認質問を
  出す」ブロックとして注入される。`answered`(確定事実、再質問禁止)とは別枠で、
  混同しない。LLM の仮説は従来どおり `next_questions` の `hypothesis` として
  `status: "open"` の `interview_qa` 行になり、`current_understanding` の確定理解には
  ならない(Principle 7: 仮説は `reasoning_llm`、確定は人手の確認で `open` → 回答)。
- **evidence_refs の graceful fallback(deterministic)**: 質問の `evidence_refs` が
  既知スパンに含まれない場合、従来はターン全体を fail させていたが、本 issue では
  **該当参照だけを落として**質問(と仮説)は残す。落とした件数は
  `InterviewTurnResult.evidence_refs_dropped` に記録する。既知スパンへの包含判定は
  有限集合に対する構造チェックであり、参照の作り直し(ヒューリスティック解釈)は
  行わない。1つの不正な行範囲が会話全体を止めることはなくなる。

判断区分: 「不明かどうか」は UI の明示フラグ(deterministic/manual)、不正参照の除去は
構造チェック(deterministic)、仮説生成と確認質問は reasoning_llm。開発者の確認
(`open` → 回答)が確定であり、LLM 出力単体では確定にならない(Principle 6/7)。

**含まない:** 自由文からの「不明」自動判定、`unconfirmed` を確定理解として
`current_understanding` に反映すること、不正 `evidence_refs` の作り直し。

共有スキーマ: `InterviewQA.status` への `"unconfirmed"` の追加
([shared/schemas/project_intelligence.schema.json](../shared/schemas/project_intelligence.schema.json))。

## 提案生成ターンの空提案と絞り込み継続

`proposal_generation` 段階で `generate_proposals: true` のターンを送っても、従来は
reasoning model が `proposals: []` の通常回答だけを返すことがあり、レスポンスは正常
(`completed`)なのに提案が 1 件も作られず、UI 上は「提案レビューが空のまま進展しない」
ように見えた。本改善は「提案生成を依頼されたターン」を明示的な契約にする。

- **依頼の明示(deterministic)**: 提案ゲート(`proposal_generation` 段階 +
  `generate_proposals` + 理解構築済みまたは手動確認済み — Issues #83/#123 と同一条件)を
  LLM 呼び出し**前**に判定し、`generate_interview_turn` に `proposals_requested` として
  渡す。プロンプト(`interview-v6`)には「開発者が提案生成を依頼した」セクションが
  入り、モデルは (a) 根拠のあるシンボルへの提案を返すか、(b) 提案できるだけの情報が
  ない場合は不足内容を `assistant_message` で説明し、仮説(`hypothesis`)と候補
  (`answer_options`)付きの絞り込み質問を `next_questions` で返すことを要求される。
- **fail-closed の構造チェック(deterministic)**: 依頼されたターンで提案も
  `next_questions` も空の応答は構造化出力の契約違反としてターンを fail させ、
  `intelligence_runs` に失敗として記録する(Principle 6/7: 「なぜ提案できないか」の
  判断は reasoning_llm、「両方空か」は有限の構造チェック)。
- **レスポンスへの可視化**: `InterviewDialogueTurnOut.proposals_requested` を追加し、
  Dashboard は「依頼したが提案 0 件(=絞り込み継続)」と「そもそも依頼していない
  ターン」を区別できる。絞り込み質問は従来どおり `open_questions` / `interview_qa`
  (`question_source: "dialogue"`)として永続化されるため、Dashboard の
  `ready_for_proposals` 状態は未回答の open question があればそれを提示し、回答の
  たびに提案生成を再試行する(回答は `answered_qa_id` で消費、「わからない」は
  #142 の `answer_unknown` で継続)。

**含まない:** 提案の自動リトライ・自動生成ループ、絞り込み質問のヒューリスティック
生成、空提案時のモック/ヒューリスティック代替。

## 大規模トレースのリネージと動的分析(Issue #144)

大きな payload・バッチ・多数の派生レコードを扱うシステム向けに、component 単位の
トレース閲覧を「エンティティ単位・フロー単位の観測と分析」へ拡張する。設計の全体像は
Issue #144 に集約し、実装は以下の sub-issue に依存順で分割した(#144 の Phase 案を
ベースに一部再構成している)。

| Sub-issue | 内容 | 依存 |
| --- | --- | --- |
| #145 | 系譜メタデータ基盤: `span_id` / `parent_span_id` / `flow_id` / `correlation_id` / entity refs(明示値のみ)、`trace_spans` / `trace_entities`、lineage クエリ API | なし |
| #146 | 宣言的 Projection: 制約付きパス式 + 有限演算(`len`/`count`/`exists`/`sha256`/`sample`)、SDK 抽出エンジン、`trace_projections`、`id_path` による entity 抽出 | #145 |
| #147 | Trace Lineage Explorer(Dashboard MVP): entity/correlation/flow 検索、ステップ表示、field 変化ハイライト | #145, #146 |
| #148 | Analyzer 手動作成と read-only 実行基盤: spec スキーマ、`trace_analyzers` / `trace_analysis_runs`、review ゲート、実行上限 | #145, #146 |
| #149 | LLM 支援 Analyzer 提案: 自然言語 → reasoning model → schema 検証 → `proposed`。fail closed、監査メタデータ必須 | #148 |
| #150 | Shadow 対応 projection と subset diff 集計: `shadow_current` / `shadow_candidate` phase、analyzer filter 下の決定的差分集計 | #146, #148 |
| #151 | Flow Explorer への runtime lineage overlay: 観測済み/未観測 probe point、静的フローとの乖離表示 | #145, #147 |
| #152 | sampling と retention: trace_id ハッシュベースの決定的 sampling、期間・件数 retention、監査付き削除 | #145, #146, #148 |

#144 原文からの主な設計調整:

- **パス式による entity 抽出は Phase 1 から Phase 2(#146)へ移動**: `id_path` 評価は
  projection の式エンジンを必要とするため、#145 では明示値のエンティティ付与のみとし、
  式エンジンの所有権を #146 に一本化した。
- **Phase 4 を手動(#148)と LLM 提案(#149)に分割**: 決定的な spec 検証・実行基盤と
  reasoning_llm による提案(Principle 6/7 の監査・fail closed 要件)を別 issue にした。
- **`caused_by` フィールドと `record_derivation` は初期実装から除外**: 派生エンティティは
  生成元トレースへ `role="derived"` で付与すれば lineage クエリが成立するため、独立した
  entity 間派生グラフは必要になった時点で別 issue とする。
- **sampling / retention を独立 issue(#152)に分離**: 初期実装はサイズ・件数上限のみを
  持ち、時間軸の運用ポリシーは分けて設計する。

判断区分: 系譜の保存/検索、projection 抽出、analyzer の検証/実行、diff 集計、overlay の
突き合わせはすべて deterministic。自然言語からの analyzer spec 生成のみ reasoning_llm
(承認は manual)。LLM は ingest のホットパスには置かない。

### Phase 1 実装状態(#145)

- **スキーマ**: `shared/schemas/trace_event.schema.json` に optional の `span_id` /
  `parent_span_id` / `flow_id` / `correlation_id` / `entities`(`{type,id,role}`、
  `role` は `source|derived|related` の有限集合)を追加。既存ペイロードは無変更で受理。
- **SDK**: `probe_context(correlation_id, flow_id, entities)` コンテキストマネージャ
  (`contextvars` ベース)、probe 毎の `span_id` 生成とネスト呼び出しの
  `parent_span_id` 自動設定、`@probe(entities=[...])` / `add_entity(type,id,role)` に
  よる明示値エンティティ付与。shadow スレッドへは `contextvars.copy_context()` で系譜を
  引き渡し、candidate 内のネスト probe も同じ lineage に載る。probe が `off` / 無効の
  ときは系譜処理を一切実行しない(early-return の後でのみ contextvars を読む)。
- **永続化**: system-scoped の `trace_spans`(PK `(system_id, trace_id)`、
  `(correlation_id)` / `(flow_id)` インデックス)と `trace_entities`
  (`(entity_type, entity_id)` / `(trace_id)` インデックス)。既存の `traces` 書き込みは
  変更せず、系譜は `input_json` に重ねない。エンティティは再 POST で置換(冪等)。
- **API**: `GET /trace-lineage/entities/{entity_type}/{entity_id}`、
  `/trace-lineage/correlations/{correlation_id}`、`/trace-lineage/flows/{flow_id}` が
  該当トレース + span + entity を timestamp 昇順で返す(System isolation)。
- Dashboard は未変更(Phase 3 / #147 の領分)。

### Phase 2 実装状態(#146)

- **スキーマ**: `shared/schemas/projection_spec.schema.json`。パス式は安全な有限
  サブセット(`$.a.b` / `$.items[*].sku` / `[i]`)、演算は `len` / `count` / `exists` /
  `sha256` の有限集合、出力は `fields` / `metrics` / `samples`。`entities[].id_path` と
  `redact` を持つ。
- **SDK 抽出エンジン**(`probe_agent/projection.py`): `eval` なしの決定的パス評価。
  `@probe(projection=...)` / `set_projection(component_id, spec)` は登録時に **fail
  closed** で検証。実行時抽出エラーは非致命で projection のみ落として診断に残す。
  上限(`PROBE_PROJECTION_MAX_BYTES` / `_MAX_FIELDS` / `_MAX_SAMPLES`)超過で決定的に
  truncate し `truncated` マーカーと `data_hash` を付与。`redact` パスは保存前に
  置換(copy-on-write で元データを非破壊)。dict / list は値単位で精密に置換し、
  オブジェクト属性など構造的に置換できない経路では、その redact パスと重なる抽出値を
  丸ごとプレースホルダに置換する(fail closed)。redact パスと重なる `id_path` は
  エンティティ化しない。`id_path` エンティティは Phase 1 の lineage entities に
  マージされる。入力 root は `{args, kwargs}`、出力 root は戻り値。**input セクションは
  関数実行前に抽出**され、関数が引数を破壊的に変更しても呼び出し時の値(= shadow
  candidate が受け取る snapshot と同じ入力)を反映する。
- **永続化**: system-scoped の `trace_projections`(`projection_name` / `phase` /
  `data_json` / `data_hash` / `truncated` / `extract_error` / `created_at`。`phase` は
  当面 `input | output`、`shadow_*` は Phase 5/#150 が所有)。raw payload は保存しない。
  `(system_id, trace_id, component_id, projection_name, phase)` UNIQUE で再 POST 冪等。
- **API**: `POST /traces` が optional の `projections` を受理、
  `GET /traces/{trace_id}/projections` と `GET /components/{component_id}/projections`。
  trace ペイロードに載る `projections` は `trace_event.schema.json` にも契約として
  定義されている(Principle 3)。
- 新環境変数は README / この節に記載。

### Phase 3 実装状態(#147)

- **サーバー**: 可視化用に別 endpoint を足すのではなく、既存 lineage API
  (`/trace-lineage/{entities,correlations,flows}`)の各 step に projection を **同梱**
  する方針を採用した(`LineageStepOut.projections`)。UI が追加リクエストなしで
  projected fields を描画できる。決定は本節に記録(issue #147 の実装時判断)。
  3 endpoint とも optional の `start` / `end`(unix 秒)クエリで時間窓を絞り込める。
- **Dashboard**: 新ページ `Trace Lineage Explorer`(`/trace-lineage`、サイドバー導線)。
  entity type + ID / correlation ID / flow ID で検索し、時間窓(From / To)と
  コンポーネント絞り込みを併用できる。timestamp・parent_span 準拠の
  縦タイムラインで表示する。各ノードは component_id、projected `fields` / `metrics`、
  entity バッジ(source/derived/related)を表示し、**前ステップとの field 値変化を
  決定的な等値比較でハイライト**する(大きい値は文字数 digest 表示)。ノードから
  Components(`/components?component=<id>` で該当コンポーネントを直接選択)/
  Flow Explorer へ遷移でき、lineage が無いシステムでは
  `probe_context` / projection 設定を案内する空状態を出す。フック `useLineage`。
  `/trace-lineage?kind=…&type=…&id=…` の deep link で検索済み状態を開ける
  (Flow Explorer overlay からの遷移に使用)。
- 比較・整列・変化判定はすべて deterministic(等値比較のみ、意味解釈はしない)。
- テスト: dashboard contracts(検索→ステップ表示→変化ハイライト→空状態)、
  サーバー lineage への projection 同梱テスト。

### Phase 4a 実装状態(#148)

- **スキーマ**: `shared/schemas/trace_analyzer_spec.schema.json`。`source`
  (当面 `trace_projections`)/ `filter`(entity / components / projection_name /
  phases / time_window)/ `select`(name + path)/ `group_by` / `order_by` /
  `compare`(phase + fields の**契約のみ**、実行は Phase 5)。パス式は #146 の
  projection サブセットを再利用。
- **エンジン**(`app/trace_analyzer.py`): fail-closed な spec 検証と **read-only**
  実行。`trace_projections` / `trace_entities` を SELECT し宣言的に評価するのみで
  対象データへ書き込まない。上限(`ANALYZER_MAX_INPUT_ROWS` /
  `ANALYZER_MAX_OUTPUT_BYTES` / `ANALYZER_MAX_SECONDS`)超過は run を failed にして
  `error_details` に記録(部分結果は保存しない)。行の整列は決定的な全順序。
- **永続化**: system-scoped の `trace_analyzers`(`spec_json` / `source` /
  `review_status` / `decision_method` / provider·model·prompt_version·
  schema_version〔#149 が書く〕/ `is_mock` / `reviewed_at` /
  `review_decision_method` / timestamps)と
  `trace_analysis_runs`(`status` / `result_json` / `error_details` / `row_count` /
  timestamps)。監査契約(Principle 7)として本 issue がテーブルを所有。
- **API**: `POST /trace-analyzers`(手動作成、schema 検証 fail-closed で 422)、
  `GET /trace-analyzers` / `GET /trace-analyzers/{id}`、
  `PUT /trace-analyzers/{id}/review`(proposed→approved/rejected の有限遷移)、
  `POST /trace-analyzers/{id}/runs`(**approved のみ**実行可、それ以外 409)、
  `GET /trace-analyzers/{id}/runs[/{run_id}]`。手動作成は `decision_method=manual`。
  review 操作は常に人間の決定であり、`reviewed_at` + `review_decision_method='manual'`
  として analyzer 行に監査記録される(`decision_method` は spec の作成主体
  〔manual / reasoning_llm〕、`review_decision_method` は承認/却下の主体を表す)。
- **Dashboard**: `Trace Analyzers` ページ(JSON spec 作成→review→run→結果表示)と、
  Trace Lineage Explorer の入力ソースに「保存済み analyzer(entity filter)」を追加。
- 新環境変数は README / この節に記載。`compare` の実行が Phase 5 の非目標である
  ことをコードコメントとスキーマ description で明示。

### Phase 4b 実装状態(#149)

- **提案**(`app/trace_analyzer_proposer.py`): 自然言語 intent + 決定的コンテキスト
  (対象 system の component_id / entity type / projection 名 / field 名 / phase の
  実在一覧)を reasoning model に渡し、structured output を
  `trace_analyzer_spec` として検証 → **実在チェック**(存在しない component /
  entity type / projection name / field を参照する提案は決定的に reject)→ 合格した
  spec を `review_status=proposed` / `decision_method=reasoning_llm` で保存。
- **fail closed**: reasoning model 未設定(API key 欠如で client 生成失敗)・API
  失敗・JSON でない・schema 検証失敗・実在チェック失敗はすべて **422 で失敗**し
  heuristic フォールバックしない。失敗も含め監査記録を残す。
- **mock**: `LLM_PROVIDER=mock` は smoke 用の明示経路。コンテキストから決定的に
  spec を生成し `is_mock=1` を全面に付与(UI / API で mock と明示)。
- **監査**: 成功・失敗とも `intelligence_runs`(`run_type='analyzer_proposal'`、
  `decision_method='reasoning_llm'`、provider / model / prompt_version /
  schema_version / status / error_details / is_mock)に永続化。analyzer 提案は
  repository snapshot に紐づかないため `intelligence_runs.snapshot_id` を
  **nullable 化**(既存 DB は安全な table-rebuild で移行、fresh DB は SCHEMA から)。
  成功時は `trace_analyzers` にも provider/model/prompt/schema/is_mock を保存。
- **承認ゲート**: 提案は既存 `PUT /trace-analyzers/{id}/review` でのみ approved に
  なり、LLM 出力単体では実行可能にならない(#148 のゲートを再利用)。
- **Dashboard**: Trace Analyzers ページに「Propose from natural language」入力を
  追加。提案の provenance(reasoning_llm / mock バッジ)と review 必須を明示。
- **API**: `POST /trace-analyzers/propose`。既存 `app/llm.py` の provider 抽象と
  mock を再利用。prompt/schema version は `analyzer-propose-v1` /
  `trace-analyzer-spec-v1`。

### Phase 5 実装状態(#150)

- **SDK**: shadow モード時、output projection spec を current 出力
  (`phase=shadow_current`)と candidate 出力(`phase=shadow_candidate`)に適用し、
  既存 shadow スレッド内で抽出して shadow-results ペイロードに同梱。production の
  返り値・例外挙動は不変(Principle 1)、projection 失敗は非致命。projection 未設定の
  コンポーネントは追加コストゼロ(spec があるときのみ抽出)。
- **サーバー**: `ShadowResult` に optional `projections` を追加、
  `POST /components/{id}/shadow-results` が `trace_projections` に
  `shadow_current` / `shadow_candidate` phase で保存(trace_id で紐付け、raw 非保存)。
- **差分集計**: analyzer 実行エンジンに `compare` 実行を実装(#148 では契約のみ)。
  フィールド単位の決定的等値比較で `entity_count` / `diff_entity_count` /
  `diff_fields`(フィールド別件数)/ `candidate_error_count` / `components_with_diff`
  を算出し、diff クラス(フィールド×コンポーネント)ごとに例示トレース ID を最大
  `ANALYZER_MAX_EXAMPLES` 件保持。**キー欠落 vs null は非等価**、**NaN は常に非等価**、
  candidate エラーは diff ではなく `candidate_error_count` に分類(仕様化・テスト済み)。
- **Dashboard**: analyzer run 結果に compare サマリ + 例示トレースを表示、run を
  workspace context(`analyzer_run`)に pin 可能。Trace Lineage Explorer に
  「Shadow compare」トグルを追加し、ノード上で current / candidate の projected
  fields 差分をハイライト。
- **非目標**: 数値許容誤差・意味的同等判定、差分の原因解釈(等値比較のみ)。

### Phase 6 実装状態(#151)

Flow Explorer(Issue #43 / #58 の静的フロー)に **runtime lineage overlay** を追加する。

- **API**: `POST /repository/flow-overlay`。既存の flow graph builder で静的フローを
  構築し、entity / correlation / flow / 保存済み analyzer(entity filter)から lineage
  ステップを解決して突き合わせる。突き合わせは **component_id の完全一致のみ**
  (有限の構造的判定、Principle 6)。
- **出力**(`app/flow_overlay.py` の決定的計算): ノードごとの `observable`
  (component_id を持つか)/ `observed` / 観測回数 / 直近観測時刻、静的エッジに対応する
  実行時遷移の有無(`observed_transition`)、静的グラフに無い実行時遷移の一覧
  (`divergences`、parent_span 経由で再構成)、静的グラフに現れない観測 component
  (`unmatched_component_ids`)。probe を持たないノードは「観測対象外」として `observed`
  と区別する。乖離の原因解釈はしない(決定的事実のみ)。
- **Dashboard**: Flow Explorer に「Runtime overlay」パネルを追加。entity /
  correlation / flow / 保存済み analyzer(entity filter 持ちのみ列挙)を選ぶと
  各 probe 点の observed / unobserved / no-probe をバッジで区別し、乖離遷移を
  強調表示する。適用中の選択は「Trace Lineage →」リンクで Trace Lineage Explorer の
  同じ検索(deep link)に遷移できる。overlay 未選択時の既存挙動は不変。
- **DB 所有権なし**(毎回決定的に突き合わせて算出、新規テーブルなし)。

### Phase 7 実装状態(#152)

運用ハードニング: sampling と retention。

- **SDK sampling**: `@probe(sample_rate=...)` による **trace_id ハッシュベースの決定的
  サンプリング**。trace 本体は常に全件送信し、lineage(span/correlation/flow/entities)
  と projection(input/output/shadow)のみをまとめて間引く(同一 trace は全採用か全棄却)。
  `None`=全採用、`0.0`=lineage/projection を全棄却。
- **retention 設定**: system-scoped の `retention_policies`(target=`trace_spans` /
  `trace_entities` / `trace_projections` / `trace_analysis_runs`、軸=`max_age_days` /
  `max_count`)。**既定は「削除しない」**(ポリシー行が無ければ何もしない)。
- **適用**: `POST /retention/apply` の明示トリガーで、古い順・ポリシー範囲内のみ削除。
  `RETENTION_BATCH_SIZE`(既定 1000)で rowid バッチ削除し長時間ロックを回避。System-
  scoped(他 system に触れない、isolation テストあり)。適用結果(対象・件数・時刻)を
  `retention_audit` に監査記録。
- **削除済み参照の明示**: 参照カウントはせず、analyzer run の `started_at` が
  projection の age retention cutoff より古ければ `data_expired=true` +
  note を返す(保守的・決定的)。
- **API**: `GET/PUT /retention/policies`、`POST /retention/apply`、
  `GET /retention/audit`。新環境変数は README に記載。
- **非目標**: traces / shadow_results 本体の retention、自動アーカイブ、LLM による
  削除判断(ポリシーは常に人間設定の決定的ルール)。

## Probe Pattern ライフサイクル(Issue #168)

本番リリース前に probe を外し、再開発時に「何を・なぜ観測していたか」を実装差分と
突き合わせながら復元するためのレイヤー。Probe Pattern は保存済み設定ではなく
「特定機能を観測するための再利用可能な認識単位」として扱い、ユーザーの認識と
probe-agent の認識を同期させることを最優先にする。

実装: `routes/probe_patterns.py` / `instrumentation_remover.py` /
`pattern_reconciler.py`、Dashboard `pages/probe-patterns.tsx`。
テーブル: `probe_patterns` / `probe_pattern_points` / `probe_pattern_events` /
`probe_pattern_reconciliations` / `probe_pattern_reconcile_points` /
`probe_removal_patches`(すべて system-scoped)。

### 設計上の決定

- **保存時に構造的事実を確定する**: pattern point は pinned snapshot から
  抽出したシグネチャ・`symbol_source_hash` / `symbol_body_hash`・docstring・
  行範囲・commit を保持する。これにより再開発時の reconcile で
  「同一 path+symbol かつ同一シグネチャ → `exact_match`」
  「同一 path+symbol でシグネチャ相違 → `changed_signature`」
  「正規化 AST ハッシュが唯一の別位置に一致 → verbatim 移動の `moved_match`」
  「denylist 一致 → `unsafe`」を決定的(Principle 6 の構造検証)に判定できる。
- **開放的な判定だけを reasoning model に委ねる**: 上記で解決しない
  移動・rename・分割/統合・消滅(`moved_match` / `split_or_merged` /
  `missing`)は reasoning_llm。候補シンボル(同名末尾・同ファイル・同
  ディレクトリ)は決定的 retrieval としてプロンプトに渡すだけで、最終判定には
  しない。LLM 出力は有限集合の classification・index 済みシンボルへの target・
  snapshot 実在パスへの evidence を厳密検証し、失敗時は run を failed として
  永続化する(決定的分類の結果は残す。ヒューリスティック代替は禁止)。
- **監査**: reconcile は `pattern_reconcile`、調査補助は `pattern_investigate`、
  reconcile からの plan 化は `probe_plan_from_pattern` の intelligence run として
  provider / model / prompt_version / decision_method を永続化。全点が決定的に
  解決した reconcile は `decision_method: deterministic`(LLM 呼び出しなし)。
- **削除は既存 patch と同じ書き込み境界**: 削除 patch は隔離 worktree で
  `@probe` デコレータ(と未使用になった import)だけを AST 編集して生成した
  reviewable diff。適用は commit SHA 確認 + clean tree 必須の明示エンドポイント
  のみで、成功時に該当 point を `removed_from_production` に更新し、履歴
  イベントを残す。
- **再装着は既存ゲートを再利用する**: 承認済み reconcile から作る Probe Plan は
  origin `probe_pattern` の通常 plan(point は `proposed`)。`exact_match` は
  自動で、非 exact は accepted のみが plan point になり、`missing` / `unsafe` /
  denylist は決して含めない。その先の approve → patch → validate → apply は
  Issue #25 の既存フローそのままで、ショートカット適用経路は作らない。
- **ヒアリング UX**: 非 exact の各 point には短い仮説 + evidence + yes/no で
  答えられる確認質問が付く。「わからないので調べる」は pinned snapshot の
  関連ファイル(対象ファイル・旧ファイル・evidence・シンボル名を含む
  テスト)を bounded に読み、現在の実装状況の短い要約と推奨を返す
  reasoning run(失敗時は fail closed)。
- **status 遷移は有限集合**: reconcile 完了時に全点 `exact_match` なら
  `active`、それ以外は `stale`。`archived` / `superseded` は手動操作のみ。

### 非目標(Issue #168 のとおり)

- 完全自動での probe 復元・古い pattern の無条件適用はしない。
- ユーザー確認なしの対象リポジトリ書き換えはしない(Principle 5/8 維持)。

## GitHub App 公開ワークフロー（Issue #216）

Issue #25 で承認・validate 済みの probe patch を、明示的な人間承認を経て
実際のリモートリポジトリへ commit / push / Pull Request 作成する経路。
Principle 5/8 の「対象リポジトリへ直接書き込まない」原則の唯一の例外で、
GitHub App の短命 Installation Token を使い、常に承認ゲートを通し、常に
`probe/` 接頭辞のサーバー生成ブランチにのみ push し、force push は一切しない。
probe-agent は Pull Request を自らマージ・クローズしない。

実装:

- `app/github_app.py` — App-level JWT 署名(RS256)・Installation Access
  Token の交換・`get_repository` / `create_pull_request` /
  `list_open_pull_requests_for_branch` / `list_installation_repositories`。
  token・JWT・private key は一切永続化しない。`_sanitize` が既知の GitHub
  token 形状(`gh[a-z]_...` / `github_pat_...`)と JWT 形状を正規表現で
  エラーメッセージから除去してから `GitHubAppError` に載せる。
  `github_app_configured()` が構成の有無を判定する唯一の場所(fail-closed、
  Principle 6 の精神)。
- `app/repo_manager.py` — `GIT_REPOSITORY_ROOT` 配下に connection ごとの
  managed mirror(bare clone 相当)を保持し、publish job ごとに独立した
  job worktree を作る/消す。`connection_lock(connection_id)` が同一
  connection に対するすべての git/publish 操作を直列化する。
- `app/publish_job.py` + `app/publish_guards.py` — 2 フェーズの publish job
  状態機械(下記)。`publish_guards` が branch 名生成・push 先検証・commit
  message/PR 本文の構造化テンプレート・diff 内ファイルパスの安全検証
  (`.git/`・path traversal・symlink・workflow ファイル・secret 名候補の
  拒否)・`assert_no_unsafe_push_config` を提供する。
- ルーティング: `routes/github_connections.py`
  (`GET /github/app-status`、connection の CRUD + verify/sync、
  読み取り専用の `GET /github/installations/{installation_id}/repositories`)
  と `routes/publish_jobs.py`(publish job の作成/一覧/取得/approve/cancel)。
  認可は他の system-scoped 管理操作と同じ `_require_manage`
  (admin または System owner のみ)。
- Dashboard: `pages/github.tsx`(App status・Connections・Publish Jobs の
  3 セクション。ナビゲーションは Sidebar の「GitHub」)。

### Publish job の状態遷移

2 フェーズ、間に人間承認を挟む一本道の有限状態機械(per-step テーブルは
持たず `publish_jobs.status` 自体が state)。

```
prepare（job 作成時に自動開始）:
  pending → authenticating → fetching → checking_out
          → applying_patch → validating → awaiting_approval

publish（明示的な approve 呼び出しでのみ開始）:
  awaiting_approval → committing → pushing → creating_pr → completed

どちらのフェーズでも失敗時・cancel 時: failed / cancelled
```

- `create_publish_job` は、connection が `status=connected` かつ
  `default_branch` を持つこと、patch が failed でなく diff が空でないこと、
  patch の最新 `validation_runs` で `baseline` と `probed` の両方が
  `overall_success=true` であることを要求する(Issue #25 の validation
  ゲートをそのまま再利用。満たさなければ 409)。
- prepare フェーズは `fetching` で一度、publish フェーズは `pushing` の
  直前でもう一度、リモート `base_branch` の SHA を解決し、patch が
  ピン留めした commit SHA と比較する。一致しなければ stale patch として
  fail closed(自動 rebase はしない。新しい snapshot からの再生成・
  再 validate を促すエラーメッセージを返す)。
- push 先ブランチは常に `publish_guards.generate_branch_name` が生成する
  `probe/` 接頭辞のブランチで、`validate_push_target` が base/default
  ブランチと一致しないことを検証する。push は
  `git push origin HEAD:refs/heads/<branch>` の明示 refspec のみで、
  `--force` は使わない。
- commit 時は patch diff に含まれるファイルパスのみを構造検証してから
  `git add` する(diff 外ファイル・`.git/`・secret 名候補・symlink・
  path traversal を拒否。workflow ファイルは既定で拒否、
  `GIT_ALLOW_WORKFLOW_CHANGES=true` の時のみ許可)。
- Installation Token は各フェーズ内でその都度発行し、ローカル変数にのみ
  保持する。`publish_jobs` テーブルにも `error` にも一切書き込まれない
  (`github_app._sanitize` を経由してから persist)。
- **Installation allowlist (Issue #222)**: GitHub App は単一 Organization
  所有の private App とし、admin が GitHub API から取得した account
  login/type を `GITHUB_APP_ALLOWED_ORGANIZATION` と照合して Installation を
  登録する。登録済みかつ active で、対象 System への明示割当がある場合だけ
  token を発行できる。この割当は repository 一覧・connection 作成・verify・sync・
  publish のそれぞれで再検証する。
- 完了・失敗・cancel のいずれの終端状態でも job worktree を cleanup し、
  `cleanup_state` / `cleanup_error` を記録する。
- PR 作成はべき等: 同じブランチに対して open な PR が既にあれば
  再利用し、重複 PR を作らない。

### 安全境界のまとめ

- **承認ゲート**: `awaiting_approval` から先へは `POST
  /github/publish-jobs/{id}/approve` の明示呼び出しでしか進まない。
  Dashboard はこの承認の前に publish 先(owner/repo・base branch・
  base commit SHA・生成される branch 名)と patch diff を必ず表示する。
- **`probe/` ブランチ強制・force push 不可**: 上記のとおり
  `publish_guards` が構造的に強制する。
- **stale fail-closed**: prepare・publish の両方でリモート base branch の
  SHA を再確認し、ずれていれば失敗させる。自動リベースは実装しない。
- **token 非永続**: Installation Token・App JWT・private key はいかなる
  テーブル・ログ・API レスポンスにも現れない。`GithubConnectionOut` /
  `PublishJobOut` はどちらも token フィールドを持たない。
- **監査**: `requested_by_user_id` / `approved_by_user_id` /
  `created_at` / `approved_at` / `completed_at` / `heartbeat_at` /
  `validation_summary` / sanitized `error` を `publish_jobs` に永続化する。
- **Issue #25 ゲートとの関係**: publish job は Issue #25 が生成・
  validate した `probe_patches` / `validation_runs` をそのまま参照するだけで、
  独自の instrumentation や patch 生成経路は持たない。probe-agent は
  常に Pull Request を作るところで止まり、マージ・クローズは開発者が
  GitHub 上で行う。

### 非目標(Issue #216 のとおり)

- default/base ブランチへの直接 push、force push、承認なしの push はしない。
- probe-agent 自身による PR のマージ・クローズはしない。
- Installation Token・private key の永続化はしない。

### Disconnect 時の即時失効(Issue #227)

`DELETE /github/connections/{id}` による明示的な Disconnect は、connection
を `status='disconnected'` にする soft delete であると同時に、その
connection に紐づく publish 権限を即座に失効させる。承認済みだが未 push の
job が Disconnect 後も approve → token 発行 → push まで進めてしまわないよう、
以下をすべて `routes/github_connections.py::delete_connection` の 1 つの
トランザクション内で行う:

- 404: connection が存在しない。
- 409: 既に `disconnected`。
- 409: この connection の publish job が `committing` / `pushing` /
  `creating_pr`(in-flight な publish フェーズ)にある場合、push 途中で
  中断させるのではなく Disconnect 自体を拒否する。
- それ以外は connection を compare-and-set で `disconnected` にし、同じ
  connection の `pending` / `authenticating` / `fetching` /
  `checking_out` / `applying_patch` / `validating` / `awaiting_approval`
  にある job をすべて `cancelled` にする(`error` は固定文言)。実行中の
  prepare フェーズスレッドには追加のシグナルを送らない -- `_set_status` が
  終端状態(`cancelled` を含む)を上書きしないという既存の仕組みだけで、次に
  スレッドが状態遷移しようとした時点で自然に停止する。
- worktree の cleanup はトランザクションコミット後にベストエフォートで行う
  (`publish_job._safe_cleanup_worktree` を再利用。単体 job の cancel と同じ
  経路)。
- リモートブランチや既に作成済みの Pull Request は削除・クローズしない。
  `pr_url` / `branch_name` は job 行に残るので、Dashboard から既存 PR への
  リンクは Disconnect 後も参照できる。

再検証ポイント(token 発行直前・push 直前に必ず接続状態を見る):

- `approve_publish_job` の UPDATE は
  `WHERE id = ? AND status = 'awaiting_approval' AND (SELECT status FROM
  github_connections WHERE id = publish_jobs.connection_id) = 'connected'`
  という compare-and-set を正としており、承認と Disconnect が競合しても
  どちらか一方だけが成立する。
- `_require_publish_installation_assignment` (prepare/publish 両フェーズの
  token 発行直前に必ず呼ばれる)は installation 割当だけでなく connection の
  `status='connected'` も再確認する。
- `_require_connection_still_connected` を prepare/publish 両フェーズの
  開始時、および publish フェーズの実際の push 直前にも呼び、Disconnect が
  間に割り込んだケースを fail closed にする。

監査: 新しい追記専用テーブル `publish_audit_events`
(`app/publish_audit.py::record_publish_audit_event`)に
`connection_disconnected`(cancelled_job_ids・件数)、job ごとの
`publish_job_cancelled`(reason)、cleanup 完了後の `publish_job_cleanup`
(cleanup_state のみ、path は含めない)を記録する。token・path を `detail`
に書かないのは Principle 5/8 のまま。このテーブルは Issue #226 で
publish job のステータス遷移一般の記録にもそのまま拡張される想定。

Reconnect は既存どおり「同じ owner/repo で新しい connection 行を作る」で
実現する(`idx_github_connections_active_unique` が `status != 'disconnected'`
のみを対象にした部分ユニークインデックスなので、disconnect 済みの行がある
状態でも新規作成が通る)。`verify` / `sync` / 新規 publish job 作成は
Disconnect 後の connection に対しては 409 で拒否する。

### Publish job の retry / recovery(Issue #226)

承認後(post-approval)の失敗は、これまで一律で終端 `failed` になり
worktree も削除されていたため、push 後・PR 作成前にクラッシュすると
リモートに孤立ブランチだけが残り、同じ job で再開する手段がなかった。
Issue #226 はこれを、同じ job・同じ(サーバー生成済みの)ブランチのまま
retry できるようにする。新しい auto-rebase やコンフリクト解消、default
ブランチへの直接 push、孤立リモートブランチの自動削除は非目標のまま。

**新しい状態**: `retryable_failed` / `reconciling` /
`manual_intervention_required`。`_TERMINAL_STATUSES` は
`("completed", "failed", "cancelled")` のまま変えず、この 3 つは「休止/
作業中」状態として扱う -- 抜けるのは retry・cancel・disconnect の
compare-and-set のみ。

**承認後の失敗分類**(`_run_publish_phase` / `_run_reconcile_phase` の
except 節): stale base branch(既存の "Base branch moved..." ケース)と、
接続が Disconnect された場合(`ConnectionRevokedError` -- reconnect は
常に新しい connection 行を作るので、同じ `connection_id` は二度と
`connected` に戻らない)は、そのまま終端 `failed`。それ以外の
`GitHubAppError` / `RepoManagerError` / push 失敗 / PR 作成失敗 / 想定外
例外はすべて `retryable_failed` になる。準備フェーズ(prepare)の失敗は
今までどおり `failed` のまま(まだ何も push していないので reconcile する
対象がない)。`retryable_failed` になった時点でも worktree の cleanup は
今までどおり実行する -- reconcile 時に `base_commit_sha` + patch diff から
決定的に再作成できるため。

**Reconcile フェーズ**(`_run_reconcile_phase`、retry から
`reconciling` になった job に対してのみ実行): まず DB 上の
`publish_connection_leases`(connection ごとに 1 行、owner・
acquired_at・expires_at)を取得する -- `repo_manager.connection_lock` は
プロセス内 RLock でしかないため、複数プロセス/レプリカが同じ connection
を同時に retry しないための追加ガード。取得できなければ git/API 操作を
一切行わずに `reconciling -> retryable_failed`(監査 reason
`lease_held`)に戻して終了する。取得できたら:

1. connection が `connected` であること・installation 割当を再確認する。
2. リモートの base branch SHA を解決し、`base_commit_sha` と比較する。
   ずれていれば stale として終端 `failed`(既存の prepare/publish と同じ
   メッセージ様式)。
3. ブランチ名(既存の `branch_name`、なければ生成)のリモート SHA を解決し、
   決定表に従って分岐する:

| リモートブランチ | job.commit_sha | 挙動 |
| --- | --- | --- |
| 存在し、SHA が一致 | 設定済み | commit/push は一切やり直さない。同じ head の open PR を検索し、あれば `pr_url`/`pr_number` を回収、なければ PR を新規作成して `completed` |
| 存在するが SHA が不一致、または `commit_sha` が未設定 | -- | `manual_intervention_required`。上書き・force push は絶対にしない |
| 存在しない | -- | ローカル worktree が(前回の cleanup で)無ければ `base_commit_sha` から再作成して patch diff を再適用し(適用失敗は `manual_intervention_required`)、`reconciling -> committing` として通常の commit/push/creating_pr/completed シーケンス(`_publish_steps`、承認直後の publish フェーズと共通)を実行する |

**auto retry**: `app/publish_recovery.py::auto_retry_eligible_jobs` が
`retryable_failed` かつ `retry_count < PUBLISH_AUTO_RETRY_MAX`(既定 3)の
job を 1 件ずつ同期的に retry する。`manual_intervention_required` は
自動 retry の対象に絶対にしない。手動 retry
(`POST /github/publish-jobs/{id}/retry`)は `retry_count` の上限を無視する
-- 上限は自動 retry にのみ適用される。

**起動時 / 定期リカバリ**(`app/publish_recovery.py`、`app/main.py` の
lifespan から起動): サーバー再起動やクラッシュでワーカースレッドが
失われた job を、`heartbeat_at` が `PUBLISH_STUCK_THRESHOLD_SECONDS`
(既定 900 秒)より古いことを条件に fail over する -- 準備フェーズの
in-flight 状態は終端 `failed`、公開フェーズの in-flight 状態と
`reconciling` は `retryable_failed`。起動時に一度同期的に実行した後、
`PUBLISH_RECOVERY_INTERVAL_SECONDS`(既定 300 秒、0 以下でワーカー自体を
無効化)ごとに動くバックグラウンドスレッドが同じ repair と auto retry を
繰り返す。

**監査**: job のあらゆる状態遷移が、その遷移を行うのと同じトランザクション
内で `publish_audit_events` に `publish_job_status_transition`
(`{"from": ..., "to": ..., "reason"?: ...}`)として追記される
(`_set_status` と、approve/cancel/retry の明示的な compare-and-set の
両方から)。加えてアクターイベント `publish_job_requested` /
`publish_job_approved` / `publish_job_retry_requested`
(手動 retry の呼び出し元)/ `publish_job_auto_retry`
(自動 retry、actor は NULL、detail に `retry_count`)を記録する。
`GET /github/publish-jobs/{id}/events` でこの job の監査ログをそのまま
参照できる。

**#227 との統合**: `_IN_FLIGHT_PUBLISH_STATUSES` に `reconciling` を追加し
(reconcile 中の disconnect は 409 で拒否)、`_CANCELLABLE_JOB_STATUSES` と
`cancel_publish_job` の許可集合に `retryable_failed` /
`manual_intervention_required` を追加した(disconnect はこれらの job も
自動 cancel し、手動 cancel でも諦められる。リモートブランチ・PR は
どちらの場合も一切変更しない)。

## Replay / Simulation（Issue #242）

トレースされた入力を後から機械的に復元し、リプレイ実行やオフライン shadow に使う
ための track。Phase A（#243）が再実行可能キャプチャ基盤、Phase B 以降（リプレイ
実行・パッチ variant・Workbench UI）は未実装の非目標。

### Phase A 実装状態（#243: 再実行可能キャプチャ基盤）

- **SDK**（`probe_agent/replay_capture.py`）: `@probe(..., replay_capture=True |
  {"redact": [...]})` による **component 単位 opt-in** の構造化入力キャプチャ。
  root は `{"args": [...], "kwargs": {...}}`、値は canonical JSON にエンコードする。
  JSON 非ネイティブ型は予約キー `"__probe__"` の明示エンコード
  （`tuple` / `set` / `frozenset`〔items は canonical JSON でソート〕/ `bytes`(b64) /
  非有限 `float` / 非文字列キー dict / `unsupported`〔型名のみ、raw 値や repr は
  埋め込まない〕）。デコードの曖昧さを排除するため、`"__probe__"` キーを含む dict は
  常に `dict` マーカーでエンコードする。既存の repr ベース `input` / `output` は不変。
  キャプチャは fn 実行**前**（pre-mutation、shadow snapshot と同じ根拠）に行い、
  失敗は常に非致命（返値・例外・trace 送信に影響しない）。opt-out 時は None チェック
  1 回のみで新フィールドも付かない。
- **replayability 分類（決定的・有限集合、Principle 6）**: 劣化フラグなし →
  `replayable`、キャプチャ保存済みで一部劣化 → `partial`、使用可能なキャプチャなし →
  `unreplayable`。理由コードは `unsupported_type` / `redacted` /
  `depth_limit_exceeded` / `size_limit_exceeded` / `round_trip_failed` /
  `capture_failed` / `redaction_blocked` の有限集合。エンコード後に decode → 構造
  比較の round-trip 検証を行う（NaN は isnan 比較）。LLM・ヒューリスティックは
  一切使わない。
- **redact / サイズ上限**: `redact` は projection と同じパス文法・マスク文字列を
  再利用し、エンコード前に root へ適用。構造的に置換できないパスは **fail closed で
  キャプチャ全体を破棄**（`redaction_blocked`）。`PROBE_REPLAY_CAPTURE_MAX_BYTES`
  （既定 65536）超過もキャプチャ全体を破棄（切り詰めた JSON は round-trip 不能な
  ため部分保存しない）。ネスト深さ上限は 20。
- **スキーマ（Principle 3、同時更新）**: `trace_event.schema.json` に additive の
  `input_capture` / `replayability` / `replay_reasons` を追加。また既存サーバー
  モデル `ShadowResult` と SDK の shadow ペイロードを契約化した
  `shared/schemas/shadow_result.schema.json` を新設（手動 `evaluation` は
  サーバー側状態でありイベントには含まれない）。
- **Control Server**: `traces` テーブルに additive の `input_capture_json` /
  `replayability` / `replay_reasons_json` カラム（既存 DB は ALTER TABLE で移行、
  既存行は NULL のまま = pre-Phase-A。一括再分類はしない）。`POST /traces` が
  新フィールドを検証（`replayability` / `replay_reasons` は有限 enum、未知値は
  422）して保存し、`GET /components/{id}/traces` が返す。
- **非目標（後続フェーズ）**: リプレイ実行（Phase B）、パッチ variant（Phase C）、
  Workbench UI（Phase D）、構造化 output キャプチャ、旧トレースの一括 backfill、
  live-shadow の SDK 挙動変更。

### Phase B 実装状態（#244: Replay Engine）

人間が replay を承認したコンポーネントについて、Replay Set（捕捉入力の集合）を
pinned snapshot の実関数に対して隔離 sandbox で再実行し、記録出力との
一致/不一致/エラー/skip を per-trace で確認できる（API レベル。UI は Phase D）。
Phase B に reasoning run は存在しない — すべて決定的な有限集合分類
（Principle 6）。

- **共有 harness（`app/replay_harness.py`、`REPLAY_HARNESS_VERSION = "1"`）**:
  スタンドアロンな Python スクリプト文字列。payload JSON を `argv[1]` から読み、
  結果 JSON を `argv[2]` に書く（結果は常にファイル経由。stdout/stderr は診断
  のみで runner が切り詰める）。target kind は有限:
  `{"kind": "symbol", "path", "qualified_name"}`（workspace root を
  `sys.path[0]` に挿入して `importlib.util.spec_from_file_location` でロード →
  getattr 連鎖。隔離は sandbox 側が担うため builtins は実環境）と
  `{"kind": "inline_code", "code"}`（generation が従来使ってきた
  `SAFE_BUILTINS` jail を維持。`candidate` を定義しなければ RuntimeError）。
  case input kind も有限: `structured`（Phase A の `"__probe__"` エンコードを
  harness 内蔵デコーダで復元。`unsupported` マーカー・未知マーカー・不正
  エンコードは関数を**呼ばずに** `undecodable_input` で skip）、`repr`
  （文字列値を harness 内で `ast.literal_eval`。パース失敗は
  `repr_parse_failed` で skip — 黙って劣化しない）、`values`
  （デコード済み値をそのまま渡す。generation 移行専用で replay は使わない）。
  per-case で出力を SDK `_safe_repr` と同一規則で正規化（repr / unrepr-able
  マーカー / 4000 文字 + `...<truncated>`）、error は `"Type: msg"` の
  first-line 形式、`traceback` は generation 互換用に保持、`duration_ms` を
  記録。case ごとの try/except で 1 case の例外が batch を殺さない。target
  解決失敗（import error / SyntaxError / シンボル欠落 / 非 callable）は
  **run-level 失敗**（`target_error` / `target_traceback`、case は実行しない）。
- **replay runner（`app/replay_runner.py` + `routes/replay.py`）**: snapshot
  解決（payload の `snapshot_id`、省略時は repo_path を持つ最新 ready
  snapshot）→ `code_symbols` からの決定的 component→symbol 解決
  （`snapshot_id` + `system_id` + `component_id`、function 系 kind。0 件 /
  複数件 / async は明示的 409）→ `patch_generator.create_worktree` で
  `PROBE_REPLAY_WORKSPACE_BASE`（既定 `/tmp/probe-replays`）`/<run_id>` 配下に
  一時 worktree → `.probe-replay/` に harness/payload を書き
  `validation_runner._run_command`（`python3 -I ...`）で実行 —
  bwrap/network-off/sandbox 不在時 fail-closed の意味論をそのまま継承。env は
  `_build_env` の最小 allowlist + `PROBE_ENABLED=false`（対象リポジトリの
  @probe を replay 中は不活性化）+ `PYTHONHASHSEED=0`（set の repr 順序を
  replay 間で再現的に）。timeout は `PROBE_REPLAY_TIMEOUT_SECONDS`（既定 60、
  1..300 に clamp）。結果ファイルは experiment と同じ safe-path + 1 MiB cap で
  読み戻し、欠落/不正時は run 失敗としてコマンド stderr を記録。worktree は
  finally で必ず cleanup（cleanup_state / cleanup_error を記録）。
- **入力復元（server 側の決定的規則、Replay Set のプレビューと同一）**:
  trace 行に `input_capture_json` があり `replayability` が
  `replayable`/`partial` → `structured`（`input_source='structured'`）;
  `replayability='unreplayable'`（または分類のみ残った不整合行 — fail
  closed）→ skip `unreplayable_capture`; capture カラムが NULL（pre-Phase-A /
  未 opt-in）→ 記録済み repr 入力から `input_source='repr_partial'` で実行
  （harness 側 literal_eval 失敗は `repr_parse_failed` で skip）; trace 行
  自体が消えている → skip `trace_missing`。
- **決定的比較（有限集合、`comparison_mode='repr'` 固定）**: 記録 error は
  FIRST LINE（保存形式 "Type: msg\ntraceback" の 1 行目）だけを比較根拠に
  し、その first line を `replay_case_results.recorded_error` に永続化する。
  マトリクス: 両方成功 → repr 文字列一致で `match`/`mismatch`; 記録成功 +
  replay 例外 → `error`; 記録 error + replay 例外 → first line 一致で
  `match` そうでなければ `mismatch`; 記録 error + replay 成功 → `mismatch`。
  skip は `skipped` + 有限 `skip_reason`（`unreplayable_capture` /
  `repr_parse_failed` / `undecodable_input` / `trace_missing`）。
  `output_truncated` フラグは切り詰め済み repr の一致が **prefix 有界の
  正直さ**でしかないこと（4000 文字プレフィクスの比較）を明示する。
- **承認ゲート（`replay_approvals`）**: `POST
  /components/{component_id}/replay-approval` が human 承認を
  `decision_method='manual'`（Principle 7）で永続化し、承認時に表示された
  決定的リスクコンテキスト（そのコンポーネントの最新 probe plan point の
  `side_effect_risk` / `replayability` ラベルの転記 — 表示のみ、新規
  reasoning run なし、ラベル欠落は欠落のまま — と固定の Principle-4 警告文:
  pure-ish コンポーネント限定、payment/email/DB write/auth は承認があっても
  強く非推奨）を snapshot として保存する。`GET .../replay-approval` は現在
  状態 + リスクコンテキストを返し、`POST .../replay-approval/revoke` で失効。
  `POST /replay-runs` は active な承認がなければ 403（revoked も同様に拒否）。
  **所有権ノート**: Issue #244 の DB 所有リストは replay_sets / replay_runs /
  replay_case_results の 3 テーブルを挙げるが、`replay_approvals` は本フェーズ
  の受け入れ条件（manual 承認の永続化）そのものを担う承認ゲート永続化であり、
  後続フェーズ用の投機的テーブルではない。
- **テーブル（すべて System-scoped、additive、SCHEMA の CREATE TABLE IF NOT
  EXISTS のみで移行完了）**: `replay_sets`（trace_ids_json は JSON 配列、
  API で 50 件 cap = `MAX_REPLAY_SET_SIZE`。source は有限
  `'manual'|'analyzer_run'`）、`replay_runs`（commit_sha / 解決シンボル /
  trace_set_hash〔順序付き trace id + 各入力 payload の sha256〕/
  sandbox_config_json〔timeout / network / harness_version / env keys〕/
  approval_id / cleanup 状態 / タイムスタンプ / 失敗詳細 — Principle 7 の
  監査メタデータ）、`replay_case_results`（上記有限分類の per-trace 行）、
  `replay_approvals`（上記）。
- **API**: `POST /replay-sets`（手動 trace 選択 または trace analyzer 実行の
  保存済み結果からの決定的 trace id 抽出 — `compare.examples` → `rows` →
  `groups[].rows` の保存済み id のみ、再計算なし。手動リストは存在 /
  component 一致 / 重複 / 50 件 cap を 422 で検証）、`GET /replay-sets` /
  `GET /replay-sets/{id}`（per-trace の replayability / replay_reasons と、
  runner と同一規則で決定した使用予定 input_source / skip_reason を返す —
  Phase D のバッジ用）、`POST /replay-runs`（N≤50 の同期実行。承認なし 403、
  シンボル未解決/曖昧/async・snapshot 不備は 409）、`GET /replay-runs/{id}`
  （cases 付き）、`GET /replay-runs?replay_set_id=&component_id=`（一覧、
  cases なし + summary）。
- **generation.py の移行**: `POST /generation-runs` の候補コード実行は共有
  harness の `inline_code` + `values` 入力経路
  （`replay_harness.run_inline_candidate`）に移行した。観測可能な挙動は不変:
  `python -I -S` 直接 subprocess（bwrap ではない）、`SAFE_BUILTINS` jail、
  5 秒 timeout、legacy エラー文字列（"candidate execution timed out" など）、
  そして repr パース失敗時に生文字列へフォールバックする legacy 意味論
  （server 側 `_trace_call_args` に残置 — replay の厳格な `repr_parse_failed`
  skip とは別物）。移行前に `tests/test_generation.py` の回帰テストで現行
  挙動をピン留めし、移行後も同一テストが無変更で通る。
- **非目標（Phase B）**: パッチ variant / variant 比較（Phase C）、Dashboard
  UI（Phase D）、自動承認・LLM のみの承認、分散実行、長寿命 worktree、
  オンライン依存インストール、async 関数の replay、構造化 output 比較
  （比較は repr のみ）。

### Phase C 実装状態（#245: オフライン shadow シミュレーション）

unified diff patch で表現された候補実装を、baseline と同一の Replay Set・
同一 sandbox 条件で再実行し、per-trace／集計の差分マトリクスを返す。記録上
error だった trace も候補に対して実行するため「候補は失敗入力を救えるか」が
答えられる（live SDK shadow の `decorator.py` の
`if run_shadow and raised is None` 非対称を**オフライン側で**解消する。SDK
自体は変更しない — 範囲外）。Phase C の決定的判定はすべて有限集合（Principle
6）。

- **共有比較ライブラリ（`app/comparison.py`）**: #150 の `_field_equal` /
  `_ABSENT` を `trace_analyzer.py` から抽出し、`field_equal` / `value_equal` /
  `diff_fields` として一本化（欠落キー vs null は不一致、二重欠落は一致、NaN
  は常に不一致、それ以外は `==`）。`trace_analyzer.py` はこれを import する
  だけになり、`tests/test_shadow_diff.py`（#150）は無変更で通る。replay の
  baseline 出力 vs candidate 出力のフィールド比較も同じ規約を再利用する。
- **variant 概念**: 1 回の variant run は baseline（patch なし）+ 1..N の patch
  variant を、**それぞれ独立した worktree** で同一 Replay Set・同一 sandbox
  設定で実行する（実行順序が結果に影響しない、#26 と同じ規約）。patch 適用は
  `experiment_runner._apply_patch` の `git apply --check` → `git apply` 規約を
  再利用し、`replay_runner.execute_harness` に `patch_text` を渡す形で
  worktree 生成後・harness 書き込み前に適用。適用失敗は
  `status='invalid_patch'` で harness を実行せずに返り、その variant だけが
  `apply_status='invalid_patch'` として記録される（baseline・他 variant は
  一切影響を受けない）。patch_hash（sha256）を監査に残す。
- **harness v2（`REPLAY_HARNESS_VERSION="2"`）**: `ok` case に additive で
  `structured_output` を付与する — `json.loads(json.dumps(output,
  sort_keys=True))` による best-effort な JSON ネイティブ形（`"__probe__"`
  エンコードは使わない）。JSON 化できない値（set・カスタムオブジェクト等）は
  **キー自体を省略**（None を入れない）ので、呼び出し側はキーの有無で「構造化
  形なし」と「正当な null/0/false」を区別する。Phase B は本キーを読まないので
  挙動は不変。
- **決定的な case 分類（有限 7 要素）**: baseline REPLAY 出力 vs candidate
  REPLAY 出力を比較する（記録済み production トレースではなく、同じ run 内で
  無改変スナップショットを再実行した baseline replay）。`match`（両成功・出力
  一致）/ `diff`（両成功・出力相違）/ `candidate_error`（baseline 成功・
  candidate 例外）/ `error_to_success`（baseline 例外・candidate 成功 = 救済）
  / `error_to_same_error` / `error_to_different_error`（両例外・first line の
  異同）/ `skipped`（server 側 skip または harness skip）。`comparison_mode`
  は `match`/`diff` のみで意味を持つ有限集合: `structured`（両側に
  `structured_output` あり。dict は top-level キーの和集合で `diff_fields`、
  非 dict は `value_equal`）/ `repr`（片側に構造化形なし → Phase B の repr
  文字列一致にフォールバック）。server 側 skip は baseline と全 variant で同一
  の `harness_cases` を実行するため、両側で同一に skip される。
- **集計**: variant ごとに各 case_status の件数 + 例示 trace id（`max_examples`
  規約を共有）+ 平均 `duration_delta_ms`（candidate − baseline）。
- **LLM candidate 下書き（`app/replay_draft.py`）**: 既存 Generate & Evaluate
  の候補生成プロンプトを「candidate draft」ソースとして接続する。「候補コード
  はどうあるべきか」は reasoning model のみ（`LLM_PROVIDER=mock` は
  generation.py 同様に許容し、`is_mock=true` を可視化。LLM 設定/呼び出し/
  parse 失敗は draft を fail-closed にする — heuristic fallback なし、Principle
  6）。「そのコードをどう diff にするか」は決定的な構造的テキスト差し込み
  （`code_symbols` の `[start_line, end_line]` を生成関数本体で置換し、
  一時 worktree で `git diff` を取る — 手書き diff 形式ではない）。provenance
  は共有の `intelligence_runs`（provider/model/prompt_version/schema_version/
  decision_method/is_mock）に記録し、決定的な raw 結果（各 case の出力・diff・
  集計）とは別テーブルに分離する。draft は何も実行しない — 返した
  `patch_text` を呼び出し側が variant として実行する。
- **Experiment 昇格（API のみ）**: `GET /replay-variant-runs/{id}/variants/
  {variant_id}/experiment-payload` が variant の patch を既存 `POST
  /experiments` の variant prefill 形で返す（experiment を自動作成・自動採用は
  しない。UI 導線は Phase D）。
- **テーブル（System-scoped、additive、cascade FK）**: `replay_variants`
  （baseline + variant 行、apply_status/patch_hash/cleanup 状態）、
  `replay_variant_case_results`（上記有限分類の per-trace 行、field_diffs_json /
  comparison_mode / duration_delta_ms）、`replay_variant_drafts`（LLM 下書きの
  結果、provenance は `intelligence_run_id` 経由）。Phase B の
  `replay_case_results`（baseline vs 記録出力）はそのまま残り、variant 側は
  baseline replay vs candidate replay を持つ。
- **API**: `POST /replay-variant-runs`（baseline + variants を同時実行、Phase B
  の承認ゲート 403・シンボル解決 409 を再利用）、`GET /replay-variant-runs`
  /`/{id}`、`GET .../variants/{variant_id}/experiment-payload`、`POST
  /replay-variant-drafts`（LLM 下書き、fail-closed）、`GET
  /replay-variant-drafts` /`/{id}`。Phase B の `POST /replay-runs`（baseline
  のみ）は無変更。
- **非目標（Phase C）**: 意味的同等性・許容誤差比較（決定的等値のみ）、自動
  採用・rollout・replace、live shadow SDK 変更、Workbench UI（Phase D）、分散
  実行。

### Phase D 実装状態（#246: Simulation Workbench UI）

観測点（トレース）から一クリックで検証に入り、「トレース選択 → ソース編集
（diff 自動生成）→ Run → diff マトリクス確認 → Experiment へ昇格」を
Dashboard 上で完結させる。Replay の判定・実行・比較はすべて Phase A〜C の既存
API を呼ぶだけで、新しい判定/実行ロジックは追加しない。例外は review-only の
regression-test scaffold 下書きで、独立した `reasoning_llm` 境界として扱う。

- **トレース行アクション（Components の Traces タブ、
  `components/replay-row-actions.tsx`）**: 各トレース行に
  `replayability`/`replay_reasons`（Phase A 由来、`GET /components/{id}/traces`
  が既に返す）から決定的に導く replayability バッジ（reason コードは
  tooltip）、「▶ Replay」（新規または既存 Replay Set にこのトレースを追加して
  Workbench へ遷移）、「+ Add to Replay Set」（同上だが遷移しない）、
  「Create Experiment from this trace」（Experiments へ `?from_trace=&
  from_component=` でコンテキストのみ prefill――patch は渡さない）を表示する。
  Replay Set は既存 `POST /replay-sets` の外に "trace を追加する" API がない
  ため（Replay Set 自体を更新する mutation endpoint はない）、既存セットへの
  追加は「その Set の `trace_ids` を読み取り、新規 trace id を足して同じ
  `component_id` で `POST /replay-sets` を再実行する」という構成のみの操作
  として実現している（Replay Set は不変の軽量スナップショットである前提と
  整合）。`AddToWorkspaceButton itemType="trace"` もここで配線する
  （型は #35 で追加済みだが UI 未配線だった）。同じアクションを Components、
  Trace Lineage、analyzer example trace rows に配置し、観測点から Workbench への
  導線を画面によって欠落させない。
- **Simulation Workbench（`pages/simulation-workbench.tsx`、
  `/simulation-workbench`、サイドバー "Detail views" に `Beaker` アイコン）**:
  3ペイン構成。左は Replay Set のトレース一覧（`GET /replay-sets/{id}` が返す
  per-trace の `replayability`/`replay_reasons`/`input_source`/`skip_reason`
  をそのままバッジ表示 — runner と同一規則、Phase B からの再利用）で、
  展開すると `input_capture`（`JsonTree`）と録画済み output/error を
  `GET /components/{id}/traces` との突き合わせで表示する（新規 API なし、
  既存レスポンスの構成のみ）。中央は解決済みシンボルの pinned snapshot ソース
  （新設 `GET /replay-sets/{id}/source`）を Textarea で表示・編集するタブ:
  「Direct edit」（編集 → Run で新設 `POST /replay-source-diff` が決定的に
  unified diff を生成 — 手書き diff ではない）、「Paste patch」（貼り付けた
  diff をそのまま variant として実行）、「LLM draft」（`POST
  /replay-variant-drafts` を呼び、provenance + is_mock バッジ付きで返却された
  patch を確認してから Run できる）。右/下は結果マトリクス:
  `POST /replay-variant-runs` + `GET /replay-variant-runs/{id}` を消費し、
  行=trace、列=recorded/baseline replay/各 candidate。`case_status`
  （match/diff/candidate_error/error_to_success 等）を色分けバッジで区別し、
  `field_diffs`・`duration_delta_ms`・variant ごとの集計
  （`aggregate.match`/`diff`/`candidate_error`/`error_to_success`/…/
  `avg_duration_delta_ms`）を表示する。結果の直上には「これはシミュレーション
  であり本番相当ではない（環境・外部状態の差異があり得る）」という常設の
  注意書きを置く。
- **状態ガイダンス**: 対象コンポーネントの replay 承認が無い場合、実行不可の
  理由と次の操作（承認レビュー）を明示し、`GET /components/{id}/replay-approval`
  のリスクコンテキスト（probe plan point の `side_effect_risk`/`replayability`
  転記 + 固定 Principle-4 警告文）を承認確認ダイアログに表示してから
  `POST /components/{id}/replay-approval` を呼ぶ（Revoke も配線）。
  `unreplayable`/skip 対象のトレースには有限 `skip_reason` ごとの次の操作
  （別トレースを選ぶ／replay_capture を opt-in する等）をインラインで示す。
- **エスカレーション（人間ゲートは既存のまま、Phase D は導線のみ）**:
  (a) variant を Experiment へ昇格 — `GET .../variants/{variant_id}/
  experiment-payload` を呼び、その patch を Experiments の作成フローへ
  引き渡す。導線は既存の `?draft=` prefill パターンを踏襲した
  `?replay_run_id=&replay_variant_id=` で、Experiments 側がその id から
  改めて payload を取得して prefill する（自動で Experiment を作成・採用は
  しない）。
  (b) regression-test scaffold — 選択した trace の捕捉入力、記録結果、解決済み
  symbol と採用候補 patch を `reasoning_llm` に渡し、pytest 雛形を下書きする。
  LLM 由来であること、provider/model/prompt/schema/intelligence run の provenance、
  `is_mock` を明示し、設定・呼び出し・応答検証の失敗は fail-closed の失敗結果として
  `intelligence_runs` と `replay_regression_scaffolds` に監査行を残す。生成物を対象
  リポジトリへ自動書き込みしない。
  (c) live-shadow 誘導 — `from probe_agent import set_candidate; set_candidate(...)`
  のスニペットと、Components タブでの mode 切り替え手順を静的テキストで示す
  （バックエンド呼び出しなし、SDK 挙動は無変更）。
- **新規 DETERMINISTIC バックエンド追加（Principle 6）**:
  `GET /replay-sets/{id}/source`（`?snapshot_id=` 省略時は最新 ready
  snapshot。`_resolve_snapshot` + `_resolve_component_symbol` を再利用して
  シンボルを解決し、`read_file_at_commit` で pinned commit のファイル内容を
  読む — working tree は一切読まない、Principle 5）と `POST
  /replay-source-diff`（`{replay_set_id, snapshot_id?, edited_source}` →
  同じシンボル解決 → 一時 worktree に `edited_source` を書いて `git diff`
  — `replay_draft._diff_against_snapshot` をそのまま再利用。`edited_source`
  が Python として parse できない場合は 422、既存スナップショットと差分が
  無い場合も 422）。どちらも承認ゲート不要（コードを実行しない）。
  `ReplaySourceOut`/`ReplaySourceDiffCreate`/`ReplaySourceDiffOut`
  （`app/models.py`）+ 対応する react-query hooks
  (`useReplaySetSource`/`useReplaySourceDiff`) を追加。テストは
  `tests/test_replay_source.py`（pinned ファイル内容+span の取得、
  適用可能な diff の生成、非 Python 入力の 422、System 分離）。これらを含む replay
  管理 API は user session 必須であり、SDK API token からは呼び出せない。
- **warm-start（任意、見送り）**: worktree/sandbox セッションの使い回しは
  本フェーズでは実装しない。Run のたびに独立した worktree を作る Phase B/C
  の規約をそのまま使うほうが、隔離・後始末の保証を壊さず正しさを優先できる
  ため。API は最大20候補を受け付け、20候補・1 trace の統合テストで10秒目標を
  ガードする。対象リポジトリや sandbox 環境によって安定して満たせない場合は
  warm-start をフォローアップとする。
- **非目標（Phase D）**: 新しい判定・実行・比較ロジック（Phase A〜C の呼び出し
  のみ）、対象リポジトリの追跡ブランチへの書き込みを伴う UI（エスカレーションは
  既存の human gate 経由のみ — Experiment decision / #216 publish）、本格的な
  IDE/LSP エディタ、live-shadow の SDK/UI 変更。

## リポジトリ設定案

設定例は [`probe-agent.example.yml`](../probe-agent.example.yml) を参照する。
実行コマンドは自動推測せず、この設定で明示する。

## AI Candidate Studio（Issue #252）

会話（チャット）で、プローブ済み関数の baseline コードを基に AI が候補実装を
生成し、既存の隔離 Replay で評価する **AI Candidate Studio**。対象コンポーネント
または具体的な Trace から開始し、自然言語で改善目的・制約を伝えるだけで、候補
生成 → 差分確認 → Replay 評価 → 反復修正 → Experiment への引き渡しまで一つの
画面で進められる。

### 設計原則: 新しい判定・実行・比較ロジックを足さない

Studio は #242（Phase A〜C）と #245 の既存基盤の上に立つ **会話 + バージョン
管理レイヤ** であり、判定/実行/比較の新規ロジックは追加しない。

- **候補生成（reasoning_llm）**: `app/candidate_studio.py`。固定 snapshot の
  対象シンボルソース・最小限の近傍情報（Component/System Profile・Evaluation
  Criteria・選択 Trace の記録入出力）を文脈に、reasoning model が **構造化
  proposal**（`summary` / `assumptions` / `changed_symbols` / `generated_code`
  / `risks` / `suggested_tests`、`shared/schemas/candidate_proposal.schema.json`、
  prompt/schema version `candidate-studio-proposal-v1`）を返す。自由形式コードは
  受け取らない。patch 自体は #245 と同じ **決定的な splice→git diff**
  （`replay_draft.splice_symbol_source` + `_diff_against_snapshot` を再利用）で
  生成し、決定的な scope 検証で「対象シンボルのファイルのみ変更」を強制する
  （範囲拡大は既定で不可）。LLM 設定・呼び出し・parse・scope/size 検証の失敗は
  すべて fail-closed（heuristic fallback なし、Principle 6）で、失敗も
  `intelligence_runs`（`run_type='candidate_studio_proposal'`）に監査記録する
  （Principle 7）。`LLM_PROVIDER=mock` は許可（`is_mock` を明示）。
- **候補の Replay 評価**: `POST /candidate-versions/{id}/replay` は既存の
  `POST /replay-variant-runs` を **そのまま関数として呼ぶ**。人手の replay 承認
  ゲート（`GET/POST /components/{id}/replay-approval`）、network-off の独立
  worktree sandbox、最小環境変数、timeout、必ず cleanup、有限 diff マトリクス
  （match / diff / candidate_error / error_to_success / …）をすべて継承する。
  未承認・sandbox 未確立は fail closed（403）。legacy の inline-code 実行経路は
  Studio では一切使わない。
- **Experiment への昇格**: `POST /candidate-versions/{id}/promote` は #245 の
  variant experiment-payload（`get_replay_variant_experiment_payload`）を再利用し、
  レビュー済み patch を既存の Experiment 作成フローへ渡す payload を返すのみ。
  Experiment の自動作成・自動採用・PR 作成/マージ・本番配布・live shadow の
  自動有効化は行わない（Principle 7）。評価済み（replay completed）候補のみ昇格
  可能。

### バージョンと会話の扱い

- `candidate_sessions` は component・固定 baseline snapshot（commit + 解決済み
  シンボル）・Replay Set・会話をまとめる。入口は「component」「trace_ids」
  「単一 trace_id」のいずれか一つ（trace 指定時は既存 `POST /replay-sets` の検証
  を再利用して Replay Set を作成）。
- `candidate_versions` は **patch が実際に生成された場合のみ** 作られる不変
  バージョン。`parent_version_id` により選択中バージョンを親にして分岐でき
  （セッションごとに木構造）、追加指示は親候補の `generated_code` を出発点として
  LLM に見せるが、生成される patch は常に **pinned snapshot に対する差分** なので
  各バージョンは独立に適用・Replay できる。会話メッセージ自体はバージョンを
  作らない。生成失敗は version 行を作らず、`intelligence_runs` の監査行 + assistant
  メッセージ + 502 で fail closed。
- `candidate_messages` は会話ターン（`user` / `assistant`）。assistant の「理解した
  条件」echo は決定的な表示テキスト（Principle 6）で推論ではない。AI の実際の
  理解は候補の `summary` / `assumptions` に現れる。

### API

```
POST /candidate-sessions                     セッション開始（対象情報を自動設定）
GET  /candidate-sessions            一覧
GET  /candidate-sessions/{id}       会話 + 候補バージョンつき詳細
POST /candidate-sessions/{id}/messages       会話ターン追加（版は作らない）
POST /candidate-sessions/{id}/generate       候補バージョン生成（reasoning_llm）
GET  /candidate-sessions/{id}/events         状態タイムライン（ポーリング）
GET  /candidate-sessions/{id}/versions       候補一覧
GET  /candidate-versions/{id}                候補詳細
POST /candidate-versions/{id}/replay         隔離 Replay 評価（承認ゲート）
POST /candidate-versions/{id}/promote        Experiment 引き渡し payload
```

生成・Replay は同期実行（Replay スタック全体と同じ規約）。`events` は永続化した
バージョン状態から決定的に導く状態タイムラインで、生成の
`context_preparing`/`generating`/`validating_patch` は terminal な version
`status`（`proposed`/`failed`）に、Replay 進行は `replay_status`
（`not_run`/`running`/`completed`/`failed`）に対応する。

### UI（`pages/candidate-studio.tsx`、`/candidate-studio?session_id=...`）

- 左ペイン: 会話履歴と入力欄。右ペイン: 選択中 Candidate の
  `差分 / 全コード / 評価結果` タブ。上部に component・baseline commit・Replay
  承認状態・候補バージョン・現在状態。
- 状態ごとの主操作は1つ: 条件入力済み→**候補を生成**、生成済み→**Replayで確認**、
  評価失敗→**AIに修正を依頼**、評価済み→**Experimentへ送る**。高度な設定
  （生 patch・Replay Set 選択・ソース全文）は折りたたむ。
- 入口: Components の component 詳細 / Trace 行、Simulation Workbench から
  「会話で候補を改善」。

### 非目標

LLM のみでの自動採用、任意コードの本番プロセスへの動的配布・実行、対象リポジトリの
自動編集、本番相当性の保証、live shadow SDK の動的候補ロード。

## フェーズ0(System 作成前)の状態案内(Issue #265)

`GET /system-state` を土台にした案内スタック(6 フェーズ、`PrerequisiteGuide`、
`DiagnosticsBadge`)は、いずれも `X-Probe-System-Id` を要求するため、ログイン前
および「System が 0 件」の状態では一切機能しない。0 admin(初回ブートでまだ
`CONTROL_ADMIN_USERNAME`/`CONTROL_ADMIN_PASSWORD` が設定・再起動されていない)
状態からは UI/API 経由で管理者を作成する手段が無く(`POST /users` は admin 専用)、
ログイン画面はその案内すら出していなかった。

- **`GET /auth/bootstrap-status`**(`app/bootstrap_status.py`、
  `routes/auth.py`): 認証なし・System なしで呼べる、唯一の決定的な事実だけを
  返すエンドポイント。`admin_exists`(`role='admin' AND is_active=1` の行の
  有無。`system_diagnostics._check_auth_scope` の「0 admin」判定は system_id
  付きで *任意ユーザー* の有無を見るのに対し、こちらは system_id を取らない
  独立ヘルパーで、判定対象も admin ロールに絞っている)、`auth_mode`
  (`auth.auth_enabled()` と同じ判定を `"anonymous" | "user"` で表現)、
  `llm_configured`(`LLM_PROVIDER` が既知の値か、`mock` か、鍵環境変数が
  *存在するか* だけを見る -- 値の検証はしない)、`environment`
  (`environment.control_env()` -- Issue #225 の本番判定をそのまま流用)の
  4 つの bool/finite token のみを返す。ユーザー名・鍵の値・パス・ホスト名は
  一切含まない。`KNOWN_PROVIDERS` は `system_diagnostics.py` の重複定義を
  `llm.py` に一本化し、両方がそこから import する。
- **ログイン画面**(`pages/login.tsx`): `admin_exists=false` のとき、ログイン
  フォームの代わりに(すでに存在しない admin では絶対に成功しない画面を出して
  も意味が無いため)、環境変数 `CONTROL_ADMIN_USERNAME`/`CONTROL_ADMIN_PASSWORD`
  を設定して Control Server を再起動する、という静的な案内を表示する。この
  文言は純粋にクライアント側固定文字列(サーバーから配信される copy ではない)
  なので #240/#266 の方針どおり `state_messages.py` には追加せず、そのまま
  日本語で `login.tsx` に置く。Issue #225 の fail-closed 本番方針との整合として、
  `environment=production` のときは具体的な環境変数名を出さず
  (`login-bootstrap-guide-production`)、「システム管理者に問い合わせてください」
  という一般化した文言のみを出す -- 本番で未認証の任意の呼び出し元に、内部の
  設定手順(変数名)を晒さないため。`llm_configured=false` の場合は開発環境の
  案内の中に、LLM_PROVIDER/LLM_API_KEY も合わせて設定する旨の補足を出す
  (blocking ではない)。
- **0 System の空状態**: ヘッダー(`components/layout/header.tsx`)は
  `systems.length === 0` のとき、アイコンのみの「+」ボタン(見落としやすい
  死角だった)をやめ、「System が未作成です」というテキストと「System を作成」
  というラベル付きボタンを表示する。Overview(`pages/overview.tsx`)は
  Components カード内で、0-*components*(既存、Issue #212)とは別の分岐として
  `systems.length === 0` を先に判定し、System 作成へ誘導する文言のみを表示する
  (System スコープの get-started リストは System が無ければ無意味なため)。
  Settings(`pages/settings.tsx`)は、これまで見出しだけの空白画面だったのを、
  `connect-sdk.tsx` の no-System 案内文と同じパターン(`<p>` + `data-testid`)
  で修正し、System が 1 件以上あるが未選択の場合の文言も追加した。
- ここでの分岐はすべて決定的な有無判定(Principle 6)であり、新しい
  `user_phase` 値は追加しない -- `user_phase`/`phases` は System 選択後にしか
  評価できない既存の仕組みのままで、フェーズ0はこのエンドポイントとクライアント
  側の表示分岐だけで完結する。

**含まない:** UI からの管理者作成・signup フロー(env + restart のブート方式は
そのまま)、LLM 設定を書き込む UI(表示・診断のみ)、新しい DB テーブル。

## 手動 System Profile と AI 理解の突き合わせ(Issue #275、旧 #94)

probe-agent の世界観は「人の認識と AI に任せたシステム構築を同期させる」こと
だが、手動入力の `system_profile`(`PUT /system-profile`)は System
Understanding の表示にも比較にも登場していなかった(`_load_purpose` は
capability hierarchy の purpose ノード → `system_profile_drafts` の単一
fallback チェーンのみ)。本 Issue は「人の認識」を第一級の provenance として
並置し、人が突き合わせを確認した事実を `decision_method: manual` で記録する。

- **`purpose_views`(並置ビュー)**: `GET /repository/system-understanding` に
  追加。手動 profile 由来のビュー(`source: system_profile`,
  `provenance_kind: manual`)は snapshot に依存せず、purpose が入力されて
  いれば常に含まれる。AI/ソース由来のビューは ready snapshot がある場合のみ、
  capability hierarchy の purpose ノード(行の `provenance_kind` をそのまま)
  → `system_profile_drafts`(従来どおり `structural`)の順で 1 件。既存の
  `purpose` フィールドの意味は変えない(後方互換)。
- **確認記録**: 新テーブル `system_purpose_confirmations`(System-scoped、
  append-only の監査行。UPDATE/DELETE しない)。`POST
  /repository/system-understanding/purpose-confirmation` が最新 ready
  snapshot に対して両ビューの内容を逐語でキャプチャして 1 行 INSERT する
  (`decision_method` は常に `manual`)。どちらか一方が欠けていれば 422、
  snapshot 不一致・snapshot 無しは 409(issue-drafts と同じ staleness
  パターン)。
- **staleness(決定的な構造等価のみ、Principle 6)**: 読み出し時に最新確認行を
  現在状態と比較し、`snapshot_changed` → `profile_updated` → `ai_updated` の
  順で判定した `stale_reason` を返す。一致/不一致の「解釈」はしない --
  類似度・heuristic 判定は導入せず、差分の解釈は人に委ねる。
- **StateItem**: 両ビューが存在し有効な確認が無いとき
  `understanding.purpose.manual_profile_unconfirmed`(severity `info`、
  `user_action_kind: confirm`、`display_routes: [/system-understanding]`、
  anchor `purpose-views`)。確認後(非 stale)は消える。文言は
  `state_messages.py` のカタログに追加。
- **共有リーダー**: profile 行・AI purpose ビュー・最新確認行・staleness の
  読み出しは `state_facts.py` の純関数に置き、
  `system_understanding_service` と `system_state` が同一実装を共有する
  (Issue #236 の方針)。
- **Dashboard**: System Understanding ページの purpose セクションを
  「人の認識(System Profile)」/「AI/ソース由来の理解」の並置カードに改修。
  手動側が空ならその場入力(既存 `PUT /system-profile` へ purpose のみ
  マージ更新)、両方あれば「一致を確認した」ボタン → 確認 POST。確認済みは
  バッジ + 日時で表示し、stale 時は理由別の日本語注記を出す(#266 の言語規約)。

**含まない:** LLM による purpose の自動マージ・書き換え(#59 の領分)、
`probe-agent:` docstring への書き戻し(Principle 8 の interview 系 issue の
領分)、`system_profile` スキーマ拡張、一致/不一致の自動判定。

## Interview 現在の理解の段階表示(Issue #283)

`pages/interview.tsx` の「現在の理解」パネル(旧 `UnderstandingPanel`)は、
`confidence.level` などの内部状態・確信度の理由・根拠コードを一度にフラット
表示しており、何が分かっていて何をユーザーに確認してほしいかが読み取り
にくかった。本 Issue は Dashboard 表示のみを再設計する(API・スキーマ・
理解生成ロジックは変更しない)。

- **`components/system-understanding/understanding-overview.tsx`
  (`UnderstandingOverview`)**: `current_understanding` /
  `gap_analysis` / `open_questions` と、呼び出し側が既に算出している
  next-action の文言(`nextActionText`、ロジックは再実装せず prop で
  受け取るだけ)から、初期表示を3グループのサマリーに絞る。
  - 「分かったこと」: `confidence.level` が `confirmed`/`likely` の
    `UnderstandingItem`。
  - 「確認したいこと」: `confidence.level` が `uncertain`/`conflicting`
    (未知の値もこちら側にフォールバック)の `UnderstandingItem` +
    `gap_analysis` の各項目 + `open_questions` の各項目。
  - 「次にすること」: `nextAction` prop をそのまま表示。
- **claim とその根拠の分離**: 各アイテムはまず見出し(`name`/`question`)・
  1–3文の要約・要対応バッジのみを表示する。`confidence.reason` ・
  `evidence`(path:line)・`related_docs`/`related_apis`/`children`・
  `hypothesis`/`evidence_refs`/`answer_options` は「根拠を見る」トグルで
  折りたたみ、初期表示では連結表示しない。
- **表示ラベルの単一マッピングテーブル**: `confidence.level` /
  `severity` / `gap_type` / `priority` の canonical enum 値はデータ上
  変更せず、`understanding-overview.tsx` 内の1つのテーブル
  (`CONFIDENCE_LABELS` 等)だけを通して日本語ラベルへ変換する。未知の値は
  常に安全側(要確認寄り)のラベルへフォールバックし、生の enum 文字列を
  画面に出さない。
- **要対応/参考情報の区別**: 色だけに依存せず、バッジ内の視覚的に隠した
  テキスト(`sr-only` の「要確認: 」/「参考情報: 」)と
  `data-action-required` 属性の両方で伝える。「分かったこと」側の項目は
  「要確認」カードとして提示しない(要件4)。
- **「詳細をすべて見る」**: 旧 `UnderstandingPanel` のカテゴリ別グルーピング
  表示は、既定で折りたたまれた詳細レイヤー(`UnderstandingCategoryDetail`)
  として残し、確信度バッジは翻訳済みラベルのみを表示する(生の enum 値は
  出さない)。
- 冗長になっていた旧「残りの質問」カード・「ギャップ分析」カードは、同じ
  情報が「確認したいこと」グループに翻訳付きで統合されたため削除した。
  Q&A の個別編集・スキップ・実態チェックなど既存の操作は `QaPanel` に残る
  (本 Issue はそれらの操作系・ライフサイクルを変更しない)。

**含まない:** 理解生成ロジック・API・スキーマの変更、Intent Brief /
Alignment Review / Investigation Agent(後続 Issue の領分)、長い調査ログ
(Q&A の逐次編集など)を初期表示に持ち込むこと。

## Intent Brief(Issue #284)

「現在の理解」(実装事実の理解)とは別に、ユーザーの意図(本人だけが決めら
れる)を構造化して保持する。両者を混在させない。

- **テーブル `interview_intent_item`**(System-scoped、additive、
  `interview_session` に `ON DELETE CASCADE`): `id` / `session_id` /
  `system_id` / `field`(`goal | pain | success_criteria | priority |
  constraints | non_goals`、API 側で有限集合を検証)/ `value_text` /
  `status`(`proposed | confirmed | needs_review | undecided |
  not_applicable`、既定 `proposed`)/ `origin`(`user | ai_proposed`)/
  `source_statement`(AI 提案の根拠となったユーザー発言、nullable)/
  `decision_method`(`manual` | `reasoning_llm`、確定操作は常に `manual`)/
  `intelligence_run_id`(`ai_proposed` 行のみ、FK `intelligence_runs`)/
  `is_mock` / `superseded_by_id`(自己 FK、訂正は新規行を追加し旧行を
  supersede — `interview_qa` の回答訂正チェーンと同じ設計)/
  `created_at` / `updated_at`。
- **エンドポイント**(`app/routes/interview_intent.py`。`interview.py` を
  これ以上肥大化させないため新規モジュールに分離し、`main.py` に登録):
  - `GET /interview/sessions/{id}/intent` — 6フィールド固定キーで
    グルーピングした現在値(非 superseded)の一覧。
    `?include_superseded=true` で訂正履歴も返す。
  - `POST /interview/sessions/{id}/intent` — ユーザーが直接作成。常に
    `origin='user'` / `decision_method='manual'`。`status` は既定
    `confirmed`、`undecided` / `not_applicable` も明示的に指定可能
    (「現状把握だけが目的」「対象外」は正規の回答であり、エラーではな
    い)。`field` / `status` は Pydantic の有限 `Literal` で検証し、
    不正値は 422。
  - `POST /interview/intent/{item_id}/confirm` — `ai_proposed` 項目を
    ユーザーが確認 → `status='confirmed'` / `decision_method='manual'`。
    `ai_proposed` 行がこの明示的な呼び出し以外で `confirmed` になる経路
    はない(Principle 2)。
  - `POST /interview/intent/{item_id}/correct`(body `{value_text}`)—
    新規行を追加(`origin='user'` / `status='confirmed'` /
    `decision_method='manual'`)し、旧行の `superseded_by_id` を設定。
    旧行の `value_text` は上書きされない(監査用に保持)。
  - `POST /interview/intent/{item_id}/decline` — `status='not_applicable'`
    に変更する manual decision。行は削除せず保持。
  - `POST /interview/sessions/{id}/intent/propose` — セッションの会話
    履歴 + `user_intent` 自由記述から、まだ値が存在しないフィールドだけ
    を対象に reasoning LLM(`app/interview_intent_agent.py`、
    `prompt_version`/`schema_version` = `intent-brief-v1`)が候補を提案
    する。fail-closed(mock/非推論モデル・API 失敗・構造化出力検証失敗は
    すべて `intelligence_runs`(`run_type='intent_proposal'`)に失敗行を
    記録した上で HTTP 502、行は一切作成しない — ヒューリスティック
    フォールバックなし)。成功時に作成される行は常に `status='proposed'`
    / `origin='ai_proposed'` / `decision_method='reasoning_llm'`。モック
    出力は `is_mock=1` を必ず伝播する。
  - すべて既存の `require_system`(`X-Probe-System-Id` + `get_system_id`)
    パターンで System-scoped。
- **UI**(`components/system-understanding/intent-brief-panel.tsx`、
  `pages/interview.tsx` の「現在の理解」カードの隣に独立した Card として
  配置、タイトル「Intent Brief(目標と成功条件)」): 6フィールドをそれ
  ぞれ表示し、`proposed` 項目には「AI 提案(未確認)」バッジ +
  「確認する」/「修正する」/「対象外」の3操作、`confirmed` 項目には
  「確認済み」バッジのみを表示する。一度にすべての未入力フィールドを
  尋ねない: 現在値が1件もない最初のフィールド(固定順 goal → pain →
  success_criteria → priority → constraints → non_goals)だけを「次に
  確認したい項目」として提示し、自由記述の入力に加えて「未定」
  「対象外」のクイック選択肢を用意する。canonical enum の値は変更せず、
  このコンポーネント内の単一マッピングテーブルのみを通して日本語ラベル
  に変換する(生の enum 文字列は画面に出さない)。

**含まない:** 対話ターン(`interview_agent.py`)からの自動抽出・Intent
Brief の自動確定・Alignment Review / Investigation Agent との接続(後続
Issue の領分)。
