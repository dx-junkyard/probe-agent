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

## Inquiry lifecycle(Issue #285)

確認項目(Q&A の質問・Intent Brief の項目・将来の review item)に疑問が
あるとき、元の項目はいったん保留し、別の Inquiry 会話で疑問だけを解消す
る。「疑問は解消した」(resolve)は元の項目への回答・確認とは厳密に別物
であり、Inquiry の作成・発言・resolve/unresolved/hold/cancel はいずれも
`interview_qa` / `interview_intent_item` の状態を一切変更しない。解消後
もユーザーは元の項目自身の回答/確認エンドポイントを明示的に呼ぶ必要があ
る(「解消を同意と誤認しない」)。

### 状態機械(有限集合、Principle 6)

`status`: `open | resolved | unresolved | cancelled | held | superseded`。

```
open       -> resolved | unresolved | held | cancelled | open(no-op)
held       -> open (resume)
open|held|resolved -> superseded (system のみ、Issue #308/#323)
それ以外   -> 409 { code: "invalid_inquiry_transition", message }
```

`superseded` は Issue #308(#323)で追加した終端状態で、ユーザー
エンドポイントからは決して到達しない。Alignment 再ビルド内の決定的な前提
評価だけが書き込み、`superseded` からの遷移は存在しない(`/message` /
`/resolve` / `/resume` / `/reopen-doubt` はすべて 409)。詳細は
「Inquiry の前提追跡と superseded(Issue #308)」節。

`open -> open` は `/reopen-doubt`(「解消していない」)専用の no-op 遷移
で、既に `open` のときだけ許可される(held からの再開は `/resume` を使
う)。すべての遷移は `interview_inquiry_transition` に監査行として記録
される(from_status / to_status / actor / reason)。`resolved` /
`unresolved` / `cancelled` は `closed_at` を刻む終端状態、`held` は
`closed_at` を刻まない再開可能な状態。

### テーブル(System-scoped、additive、`interview_session` に
`ON DELETE CASCADE`)

- **`interview_inquiry`**: `id` / `session_id` / `system_id` /
  `origin_kind`(`qa | intent | review_item`、`review_item` は Issue #287
  以降にしか行が現れないが enum は今から存在する)/ `origin_id`(`qa` /
  `intent` は作成時に存在確認、`review_item` は対応テーブルがまだ無いの
  で検証しない)/ `held_draft`(ユーザーの未確定な回答下書き、サーバー
  にとって不透明な文字列としてそのまま GET/resolve で往復させる)/
  `status` / `status_reason` / `created_at` / `updated_at` / `closed_at`。
- **`interview_inquiry_message`**: `id` / `inquiry_id` / `system_id` /
  `role`(`user | assistant`)/ `content` / `detail`(JSON
  `{key_points, evidence, uncertainty}`、assistant 行のみ、段階的開示用
  — `content` が結論、`detail` が「根拠を見る」の展開)/
  `intelligence_run_id` / `is_mock` / `created_at`。
- **`interview_inquiry_transition`**: `id` / `inquiry_id` / `system_id` /
  `from_status` / `to_status` / `actor` / `reason` / `created_at`(監査専
  用、追記のみ)。

### エンドポイント(`app/routes/interview_inquiry.py`、`main.py` に登録)

- `POST /interview/sessions/{id}/inquiries` `{origin_kind, origin_id,
  question_text, held_draft?}` — Inquiry を作成し(`status='open'`)、
  ユーザーの質問を最初のメッセージとして保存、初回の assistant 回答を
  即座に生成する。元の項目は一切更新しない。LLM 失敗時も Inquiry と
  ユーザーメッセージ自体は保存され(再試行できるように)、レスポンスは
  502(`detail.inquiry_id` に作成済み ID を含む)で assistant メッセー
  ジだけが欠ける。**同一 origin(session_id/system_id/origin_kind/
  origin_id)につき同時に活性な Inquiry は1件まで**(レビュー指摘修
  正): 同じ origin に対して `status IN ('open', 'held')` の Inquiry が
  既に存在する場合、新規作成は 409
  `{code: "inquiry_already_active", message, inquiry_id}` で拒否する。
  チェックは INSERT と同じトランザクション内で行う — `db.py` の
  `get_conn()` はプロセス全体で単一のグローバル接続ロックを取るため書き
  込みは常に直列化されており、この in-transaction check-then-insert で
  競合は起きない(事前データが原因で失敗しうる部分ユニークインデックス
  は追加しない)。
- `GET /interview/sessions/{id}/inquiries?status=...` — 一覧。
- `GET /interview/inquiries/{id}` — メッセージ全件を含む詳細
  (`held_draft` / `origin_kind` / `origin_id` を含み、リフレッシュ後の
  UI 復元に必要な情報をすべて返す)。
- `POST /interview/inquiries/{id}/message` `{content}` — 追加の質問と
  新しい assistant 回答。`status='open'` のときのみ許可(409)。
- `POST /interview/inquiries/{id}/resolve` — `status='resolved'` /
  `closed_at` 設定。レスポンスは `origin_kind` / `origin_id` /
  `held_draft` を含む(`InterviewInquiryOut` の一部としてそのまま返る)
  ので、UI はここから元の項目に戻って下書きを復元できる。
- `POST /interview/inquiries/{id}/unresolved` `{status_reason?}` —
  assistant が回答できなかった場合などに `status='unresolved'`。
- `POST /interview/inquiries/{id}/hold` `{status_reason?}` — 「今回は
  保留する」。`POST /interview/inquiries/{id}/resume` で `open` に戻せ
  る。
- `POST /interview/inquiries/{id}/cancel` `{status_reason?}`。
- `POST /interview/inquiries/{id}/reopen-doubt` — 「解消していない」。
  `open` のときだけ許可、監査行だけ追加して `open` のまま。

### 回答生成(`app/inquiry_answering.py: generate_inquiry_answer`)

（Issue #286 で内部実装を Question Router / Investigation Agent / Response
Composer の3段構成に差し替え済み。`prompt_version`/`schema_version` は
`inquiry-answer-v2` に更新した。以下は Issue #285 時点の単一パス設計の記
述で、歴史的経緯として残す。詳細は次の「Question Router / Investigation
Agent(Issue #286)」節を参照。）

`interview_context.py` の `build_interview_context` が返すスナップショッ
ト由来のコンテキストパックだけを根拠に、reasoning LLM を1回呼ぶ
(`prompt_version`/`schema_version` = `inquiry-answer-v1`、#286 以降は
`inquiry-answer-v2`)。
`interview_agent.py` の2段階(エビデンス選定→読込)とは異なり、この呼び
出しは単一パスで、コンテキストパックに既出の `(path, start_line,
end_line)` だけを引用させ、範囲外の引用は Issue #142 と同じ「致命的では
なく破棄」ルールで落とす。

- fail-closed(Principle 6): mock/非推論モデル・API 失敗・構造化出力検
  証失敗はすべて `intelligence_runs`(`run_type='inquiry_answer'`)に失
  敗行を記録した上でエラーを返す。assistant メッセージは一切作成しない
  (Inquiry は `open` のまま、ユーザーの質問だけが残る)。
- `answerable=false` はエラーではない: モデルが「根拠不足で回答不能」
  と判断した場合の正常系。この場合は LLM の文面を一切使わず、
  `interview_language.py` の固定メッセージキー
  `inquiry_insufficient_information`(ja/en 両方定義)だけを
  `interview_inquiry_message.content` に保存する(絶対に LLM の文章を
  捏造して使わない)。
- `answerable=true` のときは `conclusion` を `content` に、
  `{key_points, evidence, uncertainty}` を `detail` に保存する
  (段階的開示: UI はまず結論だけを見せ、「根拠を見る」で `detail` を
  展開する)。
- モック出力は `is_mock=1` を伝播し、UI の `is_mock` バッジ規約(既存)
  で可視化する。

**#286 への差し替え口:** 回答生成は `generate_inquiry_answer` 一箇所に
閉じてあるので、Issue #286(Question Router / Investigation Agent)は
このライフサイクル/遷移ロジックに触れずに内部実装だけを差し替えられる。

### UI(`components/system-understanding/inquiry-panel.tsx`)

Q&A パネル(`QaItemCard`)と Intent Brief パネル(`IntentItemRow`)の両
方に「疑問がある」ボタンを追加。押すと元のカードは「保留中(疑問を解消
してから回答)」の表示に切り替わり、`InquiryPanel` が質問入力→会話
(assistant の結論を先に表示し「根拠を見る」で `key_points` /
`evidence` / `uncertainty` を展開)→「疑問は解消した」/「解消していな
い(追加で質問)」/「今回は保留する」の3操作を提供する。「疑問は解消し
た」を押すと元のカードに戻り、resolve レスポンスの `held_draft` を入力
欄に復元するが、送信は自動では行わない(ユーザーが明示的に保存/確認す
るまで元の項目は更新されない)。「今回は保留する」を押すと元のカードに
「保留中の疑問があります」マーカーが残る。`api/client.ts` は無変更
(`{code, message}` 形式の構造化エラーは既存の `ApiError` がそのまま解
釈する)。`api/types.ts` / `api/hooks.ts` に型と対応するフックを追加。

**リフレッシュ/再開(refresh/resume)の UI 復元:** サーバーは Inquiry の
状態を常時永続化しているが、これをページリロード後も見失わないよう、
`QaPanel` / `IntentBriefPanel` はセッション単位で
`useActiveInquiriesByOrigin`(`api/hooks.ts`、既存の
`GET /interview/sessions/{id}/inquiries` を素の一覧取得のまま使い、追加
のサーバー変更なしでクライアント側だけで `status ∈ {open, held}` の行を
`origin_kind:origin_id` キーの Map に組み直す)を呼び、各カードへ
`existingInquiry` として渡す。カード側の描画は以下の3状態を区別する:
`status='open'` の既存 Inquiry があれば「疑問がある」の代わりに「疑問を
再開する」ボタンを表示し、押すと新規作成せずその Inquiry の会話をその
まま再取得する(`InquiryPanel` に `existingInquiryId` を渡し、質問入力
フォームを飛ばして会話ビューへ直行させる)。`status='held'` なら「保留
中の疑問があります」マーカーの隣に「疑問を再開する」ボタンを出し、押す
と `/resume` を呼んでから同じ Inquiry に再接続する。どちらのケースも
新しい Inquiry 行は作られない。

**含まない:** `review_item` origin からの Inquiry 作成 UI(Issue #287
の Review Queue が対応するまで導線がない)、質問ルーティング/調査エー
ジェント(Issue #286)。

## Question Router / Investigation Agent(Issue #286)

Inquiry の質問応答を「分類 → 調査 → 組み立て」の3段に分割した:
`app/question_router.py`(質問を有限カテゴリに分類する reasoning 呼び出
し1回)→ `app/investigation_agent.py`(pin 済み snapshot だけを read-only
で調べる reasoning 呼び出し、system_researchable/hybrid のみ)→
`app/response_composer.py`(LLM を呼ばない決定的な組み立て)。
`app/inquiry_answering.py` の `generate_inquiry_answer` はこの3段を束ね
るオーケストレーションのみを行う純関数(DB に触れない)で、
Issue #285 が確立した Inquiry ライフサイクル/遷移ロジック
(`routes/interview_inquiry.py`)には手を入れていない — 呼び出し引数
(`repo_path`/`commit_sha` の追加)とレスポンス組み立て(`route`/
`investigation` サブ結果の追加)だけを差し替えた。

### エージェント構成

- **Question Router**(`route_question`、`prompt_version`/
  `schema_version` = `question-router-v3`。当初 `v1`、Issue #291 の
  `knowledge_area` 追加で `v2`、後続のレビュー指摘修正(下記
  `search_keywords`)で `v3` まで additive にバンプしてきた):質問文 +
  短い文脈(Inquiry なら元項目の要約と直近の会話、`interview_qa` 単体
  ルーティングなら question_category/hypothesis)を渡し、構造化出力
  `{category, reason, research_focus, knowledge_area, search_keywords}`
  を1回の reasoning 呼び出しで得る。`category` は有限集合
  `human_only | system_researchable | hybrid`(Principle 6 — 自由文から
  の意味分類なので reasoning LLM 必須、キーワードヒューリスティックでは
  代替しない)。`research_focus` / `search_keywords` はどちらも
  `human_only` では常に `null` / `[]` に強制する(調査対象がないため)。
- **Investigation Agent**(`investigate`、`prompt_version`/
  `schema_version` = `investigation-v1`): `system_researchable` /
  `hybrid` のときだけ呼ぶ。候補ファイルの**取得**は `git ls-files`(pin
  済み commit)に対する決定的キーワード一致(質問文 + `research_focus` を
  トークン化してパス文字列に部分一致させ、一致数降順・パス昇順でソート)
  — これはあくまで候補を絞る足切りで、実際に**何が関連するか**の選択と
  結論は常に reasoning LLM が行う(Principle 6)。読み込みは
  `git_ops.read_file_at_commit` / `list_tree_entries` のみで、pin 済み
  commit 以外(作業ツリー・未追跡ファイル)には一切触れない。ファイル書
  き込み・パッチ・任意のサブプロセス実行・LLM 呼び出し以外のネットワー
  クはしない(Principle 5・8)。**トークン化とキーワードヒント(レ
  ビュー指摘修正):** `_keywords()` は ASCII トークン(`[A-Za-z0-9_]+`、
  従来どおり長さ3以上・ストップワード除外)に加えて CJK(日本語含む)
  の連続文字列も長さ2以上で抽出する。ただしリポジトリのファイルパス/
  識別子はほぼ ASCII なので、日本語だけの質問はそれ単独では依然として
  候補ファイルにほぼマッチしない。そこで Question Router が返す
  `search_keywords`(ASCII のコード識別子/シンボル名/パス断片の推測、
  例: 「認証」についての質問 → `["auth", "login", "token", "session"]`)
  を `investigate()` の追加引数として受け取り、同じ正規表現でトークン化
  した上で(ただし最小長2・ストップワード除外なし — 明示的なヒントの
  ため)、質問文/`research_focus` から得たキーワードより**先頭に**連結
  する。これが無いと、日本語のみの質問は候補ゼロで
  `status="unresolved"` のまま reasoning LLM 呼び出しに到達できなかった
  (Issue #286 実装時点のレビュー指摘)。
- **Response Composer**(`compose_human_only` /
  `compose_system_researchable` / `compose_hybrid`): reasoning を一切呼
  ばない決定的な組み立てのみ。文面は固定サーバーテンプレート
  (`interview_language.py` の `inquiry_human_only_answer` /
  `inquiry_hybrid_unresolved_note` / `inquiry_hybrid_decision_heading` /
  `inquiry_hybrid_default_decision_question`)か、reasoning ステップが既
  に検証済みの出力(Investigation の `conclusion`/`key_points`/
  `evidence`/`uncertainty`、Router の `reason`)のいずれかであり、ここで
  新しい文章を作らない。

### Budget(`investigation_agent.InvestigationBudget`)

明示的な有限上限をデータクラスで持ち、`__post_init__` で常にハード上限
にクランプする(呼び出し側が上書きしても範囲外にはならない):

| フィールド | デフォルト | ハード上限 |
| --- | --- | --- |
| `max_files` | 20 | 1–50 |
| `max_snippet_chars` | 40,000 | 1,000–200,000 |
| `max_llm_calls` | 3(現状の実装は常に高々1回だけ実際に呼ぶ) | 1–5 |
| `max_evidence_items` | 10 | 1–20 |
| `timeout_seconds` | 60 | 1–300 |

- 候補選定は `max_files` 件までしか候補に残さないため、実際に読んだファ
  イル数(`files_read`)は構造的に `max_files` を超えない。
- 文字数は候補ファイルを順に読みながら `max_snippet_chars` の残り予算を
  消費し、1行も収まらないファイルはスキップして次の(より小さい)候補
  を試す。
- 経過時間(`time.monotonic()`)がタイムアウトを超えた時点で以降の候補
  読み込みを打ち切る。
- 候補が1件も見つからない・タイムアウトで1件も読めなかった場合
  (`files_read == 0`)は reasoning LLM 呼び出しを一切行わず(予算の節約
  と「根拠なしからの捏造」防止)、`status="unresolved"` を返す。
- 予算の使用量(`files_read` / `chars_read` / `llm_calls` /
  `elapsed_seconds`)は `intelligence_runs` の追加カラム
  `budget_files_read` / `budget_chars_read` / `budget_llm_calls` /
  `budget_elapsed_seconds`(すべて additive ALTER、`run_type='investigation'`
  行のみ設定、他の `run_type` は常に `NULL`)に監査記録する。

### read-only 境界(Principle 5・8)

Investigation Agent はファイル書き込み・パッチ適用・任意コード実行を一
切行わない。すべての読み込みは pin 済み `commit_sha` に対する
`git show` / `git ls-tree` 相当(`git_ops.read_file_at_commit` /
`list_tree_entries`)のみで、作業ツリーの未コミット/未追跡の変更を読む
経路は存在しない。テストは `git status --porcelain` が調査前後で空のま
まであることを確認する(`tests/test_investigation_agent.py`)。

### fail-closed のルール(Principle 6・7)

- Question Router: mock クライアント・非 reasoning モデル・API 失敗・構
  造化出力の検証失敗・`category` が有限集合外、のいずれも `error` を返
  し、呼び出し元は `intelligence_runs`(`run_type='question_route'`)に
  失敗行を記録するだけで質問のルーティング結果を確定させない。
- Investigation Agent: 同様の失敗条件に加えて、`status` が
  `completed | unresolved` の集合外、`intelligence_runs`
  (`run_type='investigation'`)は常に記録し、成功時は
  `intelligence_run_evidence` に実際に読んだ全スニペット(引用されたか
  どうかに関わらず、Issue #137 のパス1監査パターンを踏襲)を記録する。
- Evidence の検証: モデルが返した `evidence` は「実際に読んだ excerpt の
  path・行範囲に収まっているか」を決定的にチェックする。範囲外の引用は
  「有効な引用が1件以上残るなら破棄して警告付きで続行」
  (`pruned_evidence` に記録、`uncertainty` にも件数を追記)、「全件が無
  効なら `status="failed"` で fail-closed」の二択(Issue #142 のパター
  ンを踏襲しつつ、全滅ケースは新規追加)。
- 根拠ゼロの `"completed"` を却下する(レビュー指摘修正): 上記の
  evidence 検証・pruning と `runtime_evidence` の検証(Issue #290)の両方
  が終わった**後**、`validated.status == "completed"` かつ検証済みの
  `evidence` と `runtime_evidence` が**両方とも空**であれば、モデルが
  一切検証可能な根拠を挙げずに完了を主張したということなので、
  `status` を決定的に `"unresolved"` へ格下げし、固定の英語ノート
  (`"Model reported completion without any verifiable evidence citation;
  demoted to unresolved."`)を `uncertainty` に追記する。`evidence` は空
  でも `runtime_evidence` が有効な値を1件以上持つ場合(コード根拠は無い
  がランタイム根拠だけで裏付けられている場合)は格下げしない —
  `"completed"` のまま返す。
- Inquiry 統合(`generate_inquiry_answer`)は Router の失敗、
  `system_researchable`/`hybrid` で pin 済み snapshot の
  `repo_path`/`commit_sha` が無い場合、Investigation の
  `status="failed"` のいずれでも `error` を伝播し、呼び出し元
  (`routes/interview_inquiry.py`)は assistant メッセージを一切作成しな
  い(Inquiry は `open` のまま)。

### カテゴリ別の応答組み立て

- **`human_only`**: Investigation を呼ばない。固定テンプレート
  (`inquiry_human_only_answer`)で質問文をそのままエコーし、Router の
  `reason` を「AI 判断根拠」として明示した上でユーザー自身の判断を促
  す。常に `answerable=true`(「情報不足」ではなく正常な完了状態)。
- **`system_researchable`**: Investigation の `status="completed"` ならそ
  の `conclusion`/`key_points`/`evidence`/`uncertainty` をそのまま
  `answerable=true` で使う。`status="unresolved"` なら `answerable=false`
  にして、呼び出し元が既存の固定メッセージキー
  `inquiry_insufficient_information`(Issue #285)に差し替える — Investigation
  の生の文言を絶対に使わない。
- **`hybrid`**: 常に `answerable=true`(片方が人間の判断である以上、
  「情報不足」で全体を差し止めない)。Investigation が完了していればその
  結論を、未解決なら固定ノート(`inquiry_hybrid_unresolved_note`)を土台
  にし、末尾に「確認したいこと:」見出し + `decision_question`(モデルが
  返した検証済みの値、無ければ固定テンプレート
  `inquiry_hybrid_default_decision_question` で質問文をエコー)を付加す
  る。`decision_question` は `interview_inquiry_message.detail` にも別
  フィールドとして保存し、UI が強調表示できるようにする。

### `interview_qa` への単体ルーティング

`POST /interview/qa/{qa_id}/route`(`app/routes/question_router.py`、
system-scoped)は `interview_qa` の1問を単独でルーティングし、
`route_category` / `route_run_id`(additive ALTER)に結果を保存する。
`interview_agent.py` のダイアログターンには一切手を入れていない(スコー
プを絞る、Issue #286 のブリーフどおり)。当初は Inquiry 内の自動ルーティ
ングとは完全に独立していたが、後続のレビュー指摘修正(下記「通常の Q&A
フローへの接続」)で同じ通常フローの一部として、バッチ版のエンドポイン
トからも(未ルーティングの質問に対して)呼び出されるようになった —
歴史的経緯として残す。単体エンドポイント自体の挙動(1問だけをルーティ
ングし、調査は一切行わない)は変わっていない。

### 通常の Q&A フローへの接続(レビュー指摘修正、Finding 1)

Issue #286 実装時点では、Question Router / Investigation Agent は Inquiry
の会話(上記)からしか自動では呼ばれず、`interview_qa` の通常フローでは
単体ルーティングエンドポイントが「分類だけして調査しない」ため、実装で
回答可能な質問がそのままユーザーに丸投げされ、`interview_qa.knowledge_area`
も通常フローでは常に null のままだった(#291 の対象外グルーピングが弱
まる)。これを埋めるのが `POST
/interview/sessions/{session_id}/qa/route-and-investigate`
(`app/routes/question_router.py`)で、Question Router と Investigation
Agent を1回のバッチ呼び出しで通常フローに接続する。

- **対象の選定**: 現在行(`superseded_by_id IS NULL AND status = 'open'`)
  のうち、未ルーティング、または `route_category IN
  ('system_researchable', 'hybrid')` かつ未調査
  (`investigation_run_id IS NULL`)の質問を `id` 昇順に最大10件
  (`MAX_BATCH_QUESTIONS`、決定的な定数)まで処理する。超過分は
  `counts.skipped_cap` に計上され、次回呼び出しで続きから処理される。
- **fail-closed はリクエスト全体ではなく質問単位(Principle 6/7)**:
  設定ゲート(mock クライアント・非 reasoning モデル)だけはバッチ全体
  の前提なので、呼び出しの最初に1回だけチェックし、失敗時は
  1行も書き換えずに 502(単体エンドポイントと同じ
  `question_route_failed` 形状)を返す。個別の質問のルーティング失敗・
  調査失敗はそれぞれ失敗した `intelligence_runs` 監査行だけを残してその
  質問を未処理のまま次の質問へ進む — 1問の失敗がバッチ全体を中断しない。
- **永続化ヘルパーの共有**: `app/investigation_persistence.py`
  (`persist_route_run` / `persist_investigation_run`)に
  `routes/interview_inquiry.py` の元実装を抽出し、単体ルーティングエン
  ドポイント・バッチエンドポイント・Inquiry フローの3箇所が同じ
  `intelligence_runs` / `intelligence_run_evidence` 書き込みコードを共有
  する(重複実装しない)。
- **`interview_qa` への調査結果の永続化(additive)**:
  `investigation_run_id`(`intelligence_runs` 参照)/
  `investigation_json`(`{status, conclusion, key_points, evidence,
  uncertainty, confidence, decision_question}` の JSON、Inquiry の
  `InvestigationResult` と同じ形)。調査が `status="failed"` の場合は両方
  とも `NULL` のまま(監査行だけ残る)。回答の訂正(`answer_interview_qa`
  の correction 経路)は `runtime_evidence` と同様、この2列も新しいリビ
  ジョン行へ引き継ぐ。
- **調査は絶対に回答を確定しない**: このエンドポイントは
  `answer_text`/`status`/`answered_by` を一切書き込まない。調査結果は
  「レビューする材料」であって「回答」ではない(#286 受け入れ基準
  「調査完了だけで元のユーザー回答が確定しない」、#284 の「AI 提案は自
  動確定しない」と同じ原則)。ユーザーは Dashboard の「調査結果を回答欄
  に転記」ボタンで結論をテキストエリアへコピーできるが、これは送信しな
  い — 既存の回答エンドポイントを明示的に呼ぶまで何も確定しない。
- **`models.py`**: `InterviewQaOut.investigation:
  Optional[InterviewQaInvestigationOut]`(永続化済みの調査結果を
  `_qa_out` が組み立てる)、`InterviewQaRouteInvestigateBatchOut`
  (`results: [{qa_id, route_category, knowledge_area,
  investigation_status, error}]` + `counts: {routed, investigated, failed,
  skipped_cap}`)を additive に追加。

### Dashboard(バッチ接続)

`pages/interview.tsx` の Q&A パネルヘッダーに「AIに先に調査させる」ボタ
ン(未回答の質問が1件以上あるときだけ表示、成功時に「分類 n 件・調査 n
件が完了しました」という日本語トーストを表示)を追加した。各質問カード
は `route_category` を(`inquiry-panel.tsx` の
`ROUTE_CATEGORY_LABELS` を再エクスポートして再利用した)同じ日本語ラベ
ルのバッジで表示し、`investigation` があれば結論を「AIの調査結果: …」
として強調表示し、`key_points` を箇条書き、`hybrid` なら
「確認したいこと: …」を強調表示し、「根拠を見る」で evidence/uncertainty
を折りたたみ表示する(いずれも Inquiry パネルの表示パターンを踏襲)。
`status === "unresolved"` のときは結論ブロックの代わりに「AIの調査では
特定できませんでした」という控えめな注記だけを出す。「調査結果を回答欄
に転記」ボタンは回答用テキストエリアに結論を書き込むだけで、送信は一切
行わない。raw な enum はどこにも出さない(Issue #266)。

### テーブル/スキーマ変更(additive)

- `interview_qa`: `route_category TEXT NULL` / `route_run_id INTEGER NULL
  REFERENCES intelligence_runs(id) ON DELETE SET NULL`。
- `intelligence_runs`: `budget_files_read` / `budget_chars_read` /
  `budget_llm_calls INTEGER NULL` / `budget_elapsed_seconds REAL NULL`
  (`run_type='investigation'` のみ設定)。`run_type` の有限集合に
  `question_route` / `investigation` / `inquiry_answer`(#285 時点で未登
  録だったものを今回追加)を追加。
- `interview_inquiry_message.detail` に `route_category` /
  `decision_question`(どちらも optional、旧行は `null`)を追加。
- (レビュー指摘修正、Finding 1)`interview_qa`: `investigation_run_id
  INTEGER NULL REFERENCES intelligence_runs(id) ON DELETE SET NULL` /
  `investigation_json TEXT NULL`(`runtime_evidence` と同じ JSON カラム
  の慣習)。バッチ route-and-investigate エンドポイントだけが書き込み、
  調査失敗時は両方とも `NULL` のまま。

### UI(`components/system-understanding/inquiry-panel.tsx`)

assistant メッセージの先頭行に、`detail.route_category` を日本語ラベル
に変換したバッジを表示する(Issue #266 規約: canonical enum は英語のま
ま保持し、画面には出さない):
`system_researchable` → 「AI が調査して回答」、`human_only` →
「あなたの判断が必要」、`hybrid` → 「調査 + あなたの判断」。`hybrid` の
`decision_question` は「確認したいこと: …」として、結論本文の直後(「根
拠を見る」の展開を待たず)に強調表示する。コンポーネントテストは
`tests/__tests__/inquiry-panel.test.tsx` の `route category badge` 節
(バッジのラベルマッピングと raw な enum 文字列が画面に出ないことを確
認)。

### テスト

- `tests/test_question_router.py`: 各カテゴリのパース、`human_only` で
  `research_focus` が強制的に `null` になること、mock/非 reasoning モデ
  ル/API 失敗/不正 JSON/カテゴリ外の値それぞれの fail-closed、
  `POST /interview/qa/{qa_id}/route` の永続化・失敗時の未ルーティング維
  持・System 分離。加えて(レビュー指摘修正)`question-router-v3` への
  バンプ、`search_keywords` のパース、`human_only` で強制的に `[]` に
  なること、フィールド省略時に `[]` にデフォルトすること(後方互換)。
  さらに(Finding 1)バッチエンドポイント
  `POST /interview/sessions/{id}/qa/route-and-investigate` を追加テスト:
  未ルーティングの質問をルーティングし `knowledge_area` を永続化するこ
  と、fake reasoning クライアントで `system_researchable`/`hybrid` を調
  査し `investigation_json` + budget カラム付きの完了 `investigation`
  行 + evidence 行を永続化すること、1問のルーティング失敗がバッチを中
  断しないこと、調査失敗は `investigation_json`/`investigation_run_id`
  を `NULL` のまま失敗行だけ残すこと、`human_only` は調査されないこと、
  `answer_text`/`status`/`answered_by` を一切書き換えないこと、10件の
  上限、System 分離(他 System のセッションは 404・行は無変更)、
  mock 設定時は 502 かつ1行も変更されないこと。
- `tests/test_investigation_agent.py`: pin 済み snapshot のみを読むこと
  (未コミット/新規ファイルが結果に一切現れない)、budget 上限の遵守
  (`files_read <= max_files`、文字数予算の枯渇、タイムアウトで
  `status="unresolved"` かつ LLM 呼び出しゼロ)、read-only 境界
  (`git status --porcelain` が空のまま)、evidence の破棄/全滅
  fail-closed、hybrid の `decision_question` 伝播、監査メタデータ
  (`prompt_version`/`schema_version`/`llm_calls`/`elapsed_seconds`)。加え
  て(レビュー指摘修正)`_keywords()` が日本語質問から CJK トークンを抽
  出すること、日本語の質問 + Router の `search_keywords=["auth", ...]`
  が(フィクスチャの `auth.py` のような)候補ファイルを選択・読み込める
  こと(ヒント無しでは候補ゼロで `unresolved` のままであることも回帰確
  認として対比)、`status="completed"` かつ evidence/runtime_evidence が
  両方空のとき `"unresolved"` へ格下げされ固定ノートが付き
  `status="completed"` を一切報告しないこと、有効な
  `runtime_evidence` のみで裏付けられた `"completed"` は格下げされずそ
  のまま維持されること。
- `tests/test_interview_inquiry.py`: Issue #286 のオーケストレーション単
  体テスト(3カテゴリそれぞれの経路、investigation 失敗時の fail-closed、
  pin 済み snapshot が無い場合の fail-closed)を追加しつつ、Issue #285
  のライフサイクル/遷移テスト(30件、`generate_inquiry_answer` を丸ごと
  スタブする方式)はそのまま維持した(`investigate()` へのシグネチャ追
  加は additive/optional のため無改修で green のまま)。

## Alignment Review / Review Queue(Issue #287)

Intent Brief(確定/提案済みの意図、Issue #284)と、証拠付きの「現在の理
解」(最新の `understanding_revision`、Issue #136)を突き合わせ、
**alignment item**(突き合わせ結果1件)を生成する。各 item は
reasoning モデルが提案した内容(claim・evidence・alignment_state・
risk_flags・confidence など)と、そこから **決定的に** 導出される
review_category/reason_code を持つ。Review Queue には
`must_review`/`batch_reviewable` の item だけが「要対応」として現れ、
残りは折りたたみ表示にとどまる。さらに(レビュー指摘修正)
`status IN ('answered', 'corrected')` の終端行と `superseded = 1` の行
は、たとえ `review_category` が `must_review`/`batch_reviewable` のまま
でも Review Queue には一切現れない — 「action required の item だけが
Review Queue の主導線に現れる」という #287 の受け入れ基準どおり、回答
済み/修正済みの行は履歴として残るだけで、二度とアクションカードになら
ない。

### テーブル(additive): `alignment_item`

| カラム | 型 | 説明 |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `session_id` / `system_id` | INTEGER NOT NULL | System-scoped、`interview_session` に紐づく |
| `revision_id` | INTEGER NULL | この item を計算した基準の `understanding_revision` |
| `snapshot_id` | INTEGER NOT NULL | `revision_id` が指すスナップショット |
| `intent_item_id` | INTEGER NULL | 関連する `interview_intent_item`(構造的に解決。下記参照) |
| `intent_summary` | TEXT NULL | reasoning モデルが付けた意図側の短い要約(表示専用) |
| `current_claim` | TEXT NOT NULL | 「現在の理解」側のクレーム本文 |
| `current_evidence` | TEXT NOT NULL (JSON) | `[{path,start_line,end_line,summary}]`。pin 済みスナップショットに対して検証済み |
| `gap_summary` / `proposed_interpretation` | TEXT NULL | |
| `alignment_state` | TEXT NOT NULL | `aligned\|gap\|unknown\|conflict\|not_applicable` |
| `risk_flags` | TEXT NOT NULL (JSON) DEFAULT `[]` | 有限集合 `security\|high_risk\|core_intent` の部分集合 |
| `confidence` | TEXT NOT NULL | `confirmed\|likely\|uncertain\|conflicting`(既存の水準を再利用) |
| `review_category` | TEXT NOT NULL | `must_review\|batch_reviewable\|no_review_required\|unchanged\|informational` |
| `reason_code` | TEXT NOT NULL | 下記ルール表参照 |
| `user_reason` | TEXT NOT NULL | `reason_code` ごとの固定日本語テンプレート(LLM 自由文ではない) |
| `status` | TEXT NOT NULL DEFAULT `'open'` | `open\|answered\|corrected\|held\|inquiry` |
| `user_decision` | TEXT NULL (JSON) | `{action, note, decided_at, decided_by}`。サーバーは絶対に自動セットしない |
| `superseded` | INTEGER NOT NULL DEFAULT 0 | additive(レビュー指摘修正)。再ビルド時点で終端状態(`answered`/`corrected`)だった行に `1` を立て、同じ突き合わせ対象の新しい行と区別する履歴フラグ。既存行は 0 にバックフィルされる |
| `intelligence_run_id` | INTEGER NOT NULL | この item を生成した `intelligence_runs` 行 |
| `is_mock` | INTEGER DEFAULT 0 | |
| `created_at` / `updated_at` | REAL NOT NULL | |

`unchanged` は将来のビルド間差分検出(前回ビルドと同一内容)のために予
約された値で、現時点のルール表からは到達しない(#287 の決定的ルール表
は brief の記述どおり実装されており、`unchanged` を出力する分岐を持た
ない)。Dashboard 側は未知/未到達の値でも折りたたみ扱いに倒すため、実
装上の問題にはならない。

### 生成(`app/alignment.py`)

`build_alignment`(実体は `routes/interview_alignment.py` の
`POST .../alignment/build` ハンドラ。DB オーケストレーションはルート層、
reasoning 呼び出しと決定的検証は `app/alignment.py` という、Issue
#284/#285 と同じ責務分割)は次の手順で動く:

1. セッションの現在(`superseded_by_id IS NULL`)の Intent Brief item 全
   件と、最新の `understanding_revision`(`current_understanding` /
   `gap_analysis`)を読み込む。`understanding_revision` が1件も無い場合
   は 409(reasoning 呼び出しは一切行わず、`intelligence_runs` 行も作ら
   ない — 「先に System Understanding を構築してください」という前提条
   件の欠落であり、reasoning の失敗ではないため)。
2. reasoning モデル(`generate_alignment_proposal`、`prompt_version`/
   `schema_version` = `alignment-v1`、run_type
   `alignment_build`)が alignment item 候補を提案する:
   `{items: [{intent_field, intent_ref_hint, current_claim, evidence[],
   alignment_state, risk_flags, confidence, gap_summary,
   proposed_interpretation}]}`。`alignment_state`/`confidence`/
   `risk_flags`/`intent_field` はすべて有限集合に対してスキーマ検証さ
   れ、外れた値が1つでもあればビルド全体を fail-closed する(Issue
   #286 の Question Router と同じ扱い)。
   - `intent_item_id` はモデルの自由文からは決めない。モデルが返すのは
     `intent_field`(6値の enum、または関連する意図が無ければ `null`)
     だけで、実際の `interview_intent_item.id` への解決はサーバー側で
     「そのセッションの、その `field` の現在(非supersede)行」への完
     全一致検索という決定的な構造チェックで行う(Principle 6 — 自由文
     の fuzzy match は一切しない)。
3. `evidence` は Issue #286 の Investigation Agent と同じ規律で pin 済
   みスナップショットに対して検証する: path がそのコミットに存在し、
   `1 <= start_line <= end_line <= (そのファイルの実際の行数)` を満た
   すことを `git_ops.read_file_at_commit` で確認する(決定的、reasoning
   ではない)。無効な evidence は item から取り除かれる(記録される)。
   取り除いた結果その item の evidence が0件になった item はその item
   ごと破棄する。**モデルが1件以上の item を提案したにもかかわらず、
   有効な item が1件も残らなかった場合はビルド全体を fail-closed する**
   (「有効な引用が0件なら失敗」の単位を、Issue #286 の1メッセージ単位
   ではなく、このビルド1回の単位に広げたもの)。モデルが最初から
   `items: []` を返した場合(比較する新事実が無い、という結論)は失敗
   ではない。
4. `review_category` + `reason_code` は前段の**決定的な**ルール表
   (`app/alignment.py` の `_RULES`、先勝ちのデータ列で実装)から導出す
   る。数値スコアの合算や LLM による並べ替えは一切行わない:

   ```
   'security' in risk_flags                                          -> must_review, security_related
   'high_risk' in risk_flags                                         -> must_review, high_risk
   'core_intent' in risk_flags OR (intent_field == 'goal' AND
       alignment_state in (gap, conflict))                           -> must_review, core_intent
   alignment_state == 'conflict'                                     -> must_review, conflict_detected
   confidence in (uncertain, conflicting)                            -> must_review, low_confidence
   alignment_state == 'unknown'                                      -> must_review, low_confidence
   alignment_state == 'gap'                                          -> batch_reviewable, routine_update
   alignment_state == 'aligned'                                      -> no_review_required, no_change
   alignment_state == 'not_applicable'                               -> informational, informational_only
   ```

   有効な `alignment_state`(5値)は必ずこの表のどれか1行に一致する
   (先勝ち)ため、スキーマ検証済みの入力に対してこの関数が例外を投げる
   ことはない。`user_reason` は `reason_code` ごとの固定辞書
   (`USER_REASON_TEMPLATES`)からそのまま引く(例:
   `security_related` → 「セキュリティに関わるため個別確認が必要です」)。

5. **キューの並び順**は `review_sort_key` によって決定的に決まる:
   `(review_category のランク, reason_code のランク, id 昇順)`。ランク
   は `REVIEW_CATEGORIES`/`REASON_CODES` タプルの並び順(must_review が
   最優先、reason_code は `security_related < high_risk < core_intent <
   conflict_detected < low_confidence < runtime_mismatch <
   routine_update < no_change < informational_only`)そのもの。LLM によ
   る並べ替えや数値スコアの掛け算は一切しない。

### 再ビルド(rebuild-merge)のルール

`POST .../alignment/build` を再度呼ぶと、そのセッションの
`status = 'open' AND user_decision IS NULL` の行だけを **削除して作り直
す**(未対応・ユーザー操作が一切入っていない提案のみが対象)。それ以外
の行 —`answered`/`corrected`/`held`/`inquiry` のいずれか、または
`user_decision` が記録済み — は、基準リビジョンがどれだけ新しくなって
も再ビルドで削除・上書きされない(Principle 2: 再ビルドが人間の決定を
失わせてはならない)。この非対称性はテーブルコメントと
`tests/test_interview_alignment.py` の
`test_rebuild_preserves_items_with_user_progress_and_refreshes_untouched_open`
/ `test_held_item_is_also_preserved_across_rebuild` で固定化している。

**superseded マーキング(レビュー指摘修正):** 上記で削除されずに残る
行のうち終端状態(`answered`/`corrected`)のものだけを、新しい行を挿入
する**前**に `superseded = 1` へ更新する(`held`/`inquiry` は対象外 —
まだ進行中で「現在の行」であり続ける)。
これにより、同じ突き合わせ対象について再ビルド後は「新しい未対応行
(`superseded = 0`)」と「古い回答済み/修正済み行(`superseded = 1`、履
歴)」が共存しても、後者が二度と Review Queue のアクションカードとして
復活しない(削除ベースの merge だけでは、行そのものは残っても
`review_category` が `must_review`/`batch_reviewable` のままだと再度
アクションカードに見えてしまっていた回帰)。

### ルート(`routes/interview_alignment.py`、`main.py` に登録)

- `POST /interview/sessions/{id}/alignment/build` — 上記の生成 + 再ビル
  ド。前提条件欠落は 409、reasoning/検証の失敗は 502(いずれも
  `alignment_item` 行は一切変更されない)。
- `GET /interview/sessions/{id}/alignment` — 全 item を `review_category`
  ごとにグルーピングして返す(`items_by_category` + `counts`)。
- `GET /interview/sessions/{id}/review-queue` — `must_review` /
  `batch_reviewable` の item だけを `review_sort_key` の順で返す。
  さらに(レビュー指摘修正)`status NOT IN ('answered', 'corrected')`
  と `superseded = 0` を条件に加える —
  終端状態(回答済み/修正済み)の行と、再ビルドで履歴化された行は、
  `review_category` が `must_review`/`batch_reviewable` のままであって
  も二度とこのキューに現れない。`held`/`inquiry` はここでは除外しない
  (`held` は一時停止であって対応不要ではなく、`inquiry` はダッシュボー
  ドが「疑問を確認中」としてブロック表示する対象であり、いずれも「まだ
  action が必要」な状態のため)。
- `POST /interview/alignment/{item_id}/answer` —
  `{decision: accept_current|needs_change|reject_interpretation, note?}`。
  `status='answered'` + `user_decision` を記録する(`decision_method` は
  常に人間の明示操作、Principle 2)。
- `POST /interview/alignment/{item_id}/correct` —
  `{corrected_interpretation}`。`status='corrected'`。
- `POST /interview/alignment/{item_id}/hold` — `status='held'`。
- 上記3エンドポイントは、対象 item の `status == 'inquiry'`(下記)の
  間は 409 で拒否する — 疑問を解消してから回答させるため。
- サーバーが `user_decision` を自動でセットする経路は存在しない
  (`test_build_never_auto_sets_user_decision` で固定化)。

### Inquiry 統合(Issue #285 の `origin_kind='review_item'` 拡張)

`routes/interview_inquiry.py` の `_validate_origin_exists` は
`review_item` を受け取ると `alignment_item` の実在を確認するようになっ
た(存在しなければ 404 — Issue #285/#286 時点の「テーブルが無いので存
在チェックをスキップする」プレースホルダ挙動から変更)。

`review_item` は qa/intent と異なり、Inquiry の開閉が origin の
`alignment_item.status` に反映される唯一のケースとして明示的に許容され
ている(Principle 2 の「origin テーブルを書き換えない」という原則の中
での、ドキュメント化された例外— 変更するのは `status` だけで、
`user_decision` には一切触れない):

- Inquiry を作成した瞬間、対象 `alignment_item.status` を `'inquiry'`
  にする。
- Inquiry が **閉じる**(`resolved`/`unresolved`/`cancelled` —
  `_CLOSED_STATUSES` をそのまま再利用)と `'open'` に戻す。**
  `'answered'` には絶対にしない** — 開発者は改めて `/answer` 等を明示
  的に呼ぶ必要がある(brief が明示するリグレッションテスト:
  `test_review_item_inquiry_resolve_sets_item_back_to_open_not_answered`)。
  ただし(レビュー指摘修正)`'open'` へ戻すのは、同じ origin
  (session_id/system_id/origin_kind/origin_id)に対する**他の** Inquiry
  が `status IN ('open', 'held')` で一切残っていない場合に限る — 同一
  origin に対する Inquiry は作成時点で1件までに制限した(上記
  `POST /interview/sessions/{id}/inquiries` の 409
  `inquiry_already_active`)ので通常は発生しないが、この修正より前に作
  られた重複行が万一残っていた場合の defense in depth として、
  `_apply_transition` は閉じる直前に同一 origin の他の活性 Inquiry の
  有無を確認してから `alignment_item.status` を戻す。
- `held`(一時停止であって終了ではない)は対象外: Inquiry が再開待ちの
  間、item は `'inquiry'` のままブロックされ続ける
  (`test_review_item_inquiry_held_keeps_item_status_inquiry`)。

### Dashboard(`components/system-understanding/review-queue.tsx`)

interview ページに新設した Review Queue パネル:

- `must_review`/`batch_reviewable` の item だけをアクションカードとして
  表示する(意図/現状/ギャップの短い対比 + `user_reason` バッジ +
  「根拠を見る」でパス:行番号とスナップショット参照を展開)。
- `no_review_required`/`unchanged`/`informational` は「対応不要の項目
  (n)」という折りたたみセクションの中にだけ表示し、アクションボタンは
  一切持たない。
- `must_review` の item は破壊的トーンの枠線 + 「要確認」バッジ(色だ
  けに依存せず `sr-only` テキストも付与)で視覚的に区別する。
- 「疑問がある」は既存の `InquiryPanel`(Issue #285)を
  `origin_kind="review_item"` でそのまま再利用する。`status='inquiry'`
  の間、回答/修正/保留ボタンは非表示になり、代わりに「疑問を確認中で
  す」という案内を出す。
- canonical enum(`alignment_state`/`risk_flags`)は本ファイル内の単一
  マッピングテーブルのみを通して日本語ラベルに変換する。
- `superseded`(レビュー指摘修正): `GET .../review-queue` は既に
  `superseded = 1` の行を返さないためアクションカード側の対応は不要だ
  が、`GET .../alignment` の全件リスト(折りたたみ「対応不要の項目」な
  ど)は `review_category` 単位のグルーピングをそのまま流用しているた
  め、履歴化された行が理論上そこに混ざりうる。`InformationalItemRow` は
  `item.superseded` が真のとき「履歴」バッジを1つ追加するだけの最小限
  の対応にとどめ、除外はしない(監査性を優先し、is_mock バッジと同じ
  「隠さず可視化する」方針)。

### テスト

- `tests/test_interview_alignment.py`: ルール表の全分岐を網羅する
  table-driven テスト + 決定性の確認、must_review リグレッション集合
  (security/high_risk/core_intent/conflict/unknown/uncertain)、
  `review_sort_key` の固定フィクスチャによる並び順契約テスト、
  `generate_alignment_proposal` の fail-closed(mock/非 reasoning モデ
  ル/API 失敗/不正 JSON/有限集合外の値それぞれ)、
  `validate_evidence_against_snapshot` の実 git フィクスチャ検証、ビル
  ド API の 409(前提条件欠落)/502(reasoning 失敗・全 evidence 無効)、
  再ビルドの保護/更新境界、review-queue のフィルタ+順序、
  answer/correct/hold、`user_decision` 自動セット無し、review_item
  Inquiry の実ビルド経由エンドツーエンド往復、System 分離。加えて(レ
  ビュー指摘修正)answered/corrected 項目が review-queue から消えるこ
  と(held/inquiry は残ること)、再ビルドで終端行が `superseded=1` にな
  り review-queue から消える一方で新しい代替行(`superseded=0`)がちょ
  うど1件だけ現れること、held/inquiry の保持行は再ビルドを跨いでも
  `superseded=0` のままであること、`superseded` 列についても System 分
  離が保たれること、`superseded` 列の追加マイグレーションで既存行が0に
  バックフィルされること。
- `tests/test_interview_inquiry.py`: `review_item` の存在チェック(未知
  の id は 404 に変更)、Inquiry 開始で `alignment_item.status` が
  `'inquiry'` になること、resolve/cancel/unresolved で `'open'` に戻る
  こと(`'answered'` にはならないこと)、hold では `'inquiry'` のまま
  であること、`status='inquiry'` の間は `/answer` が 409 で拒否される
  こと。加えて(レビュー指摘修正)同一 origin への2件目の Inquiry 作成
  が `open`/`held` いずれの場合も 409
  `inquiry_already_active` になること、最初の Inquiry が閉じた後は再度
  作成できること、origin が異なれば影響しないこと、SQL で直接2件の活
  性 Inquiry を仕込んだ場合(修正前データのシミュレーション)に1件目
  を閉じても `alignment_item.status='inquiry'` のままで、最後の1件を閉
  じて初めて `'open'` に戻ること。
- Dashboard: `src/__tests__/review-queue-panel.test.tsx`(アクションカ
  ードが actionable なカテゴリだけに出ること、informational が折りた
  たまれ操作を持たないこと、`status='inquiry'` でアクションが隠れるこ
  と、raw enum が画面に出ないこと、answer/hold/build 各アクションが対
  応する API を呼ぶこと)。加えて(レビュー指摘修正)`superseded=true`
  の informational 行にだけ「履歴」バッジが表示され、通常の行には表示
  されないこと。

## 回答バッチ後の自動更新(Issue #288)

Q&A 回答 / Intent の confirm・correct・decline / Alignment の
answer・correct のいずれかが保存された直後、手動の「理解を更新」を押さ
なくても、入力種別に応じて Understanding を再構築し、Alignment /
Review Queue を自動で最新化する。
保存済みの回答を失わないこと(Principle 1)、冪等であること、古い(=
実行順序が入れ替わって後から書き込もうとした)結果が新しい結果を上書き
しないことを満たす。

### テーブル(additive): `interview_refresh_job`

| カラム | 型 | 説明 |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `session_id` / `system_id` | INTEGER NOT NULL | System-scoped、`interview_session` に紐づく |
| `trigger_kind` | TEXT NOT NULL | `qa_answer\|intent_update\|alignment_answer\|nl_change_set`(`nl_change_set` は #289 が使う予約値。本 issue では発行しない) |
| `base_revision_id` | INTEGER NULL | enqueue 時点の最新 `understanding_revision.id`(まだ無ければ NULL) |
| `base_answer_marker` | REAL NOT NULL | enqueue 時刻(このトリガー入力のデデュープキー) |
| `status` | TEXT NOT NULL DEFAULT `'pending'` | `pending\|updating\|updated\|failed\|stale` |
| `error` | TEXT NULL | `failed` 時は失敗理由、`updated`/`stale` 時は固定の日本語注記(何もすることがなかった/より新しい結果に破棄された)。LLM 自由文ではない |
| `intelligence_run_id` | INTEGER NULL | この job が生成した `understanding_review` の `intelligence_runs` 行 |
| `result_revision_id` | INTEGER NULL | この job が生成した `understanding_revision.id` |
| `created_at` / `started_at` / `finished_at` | REAL | |

インデックス: `(session_id, id DESC)` / `(system_id, session_id)`。

### オーケストレーション(`app/interview_refresh.py`)

- `request_refresh(session_id, system_id, trigger_kind)`: 回答/決定の
  コミットが**完了した後**に呼ぶ(回答の永続化成功と自動更新の成否を結
  合しない、Principle 1)。デデュープ規則:
  - そのセッションに `pending` job が既にあれば、新しい行は作らない
    (既存の `pending` job が実行される時点で、その時点までに保存され
    ているすべての回答を拾う — 束ねられたバッチ)。必要処理を失わない
    よう trigger は `alignment_answer > qa_answer >
    intent_update/nl_change_set` の順に昇格する。
  - `updating` job があり `pending` が無ければ、`pending` job を1件だ
    け作る(実行中の job には今回のトリガーが間に合わないため)。
  - どちらも無ければ `pending` job を作って即座にディスパッチする。
  - 結果として、1セッションにつき常に「`pending` 高々1件 + `updating`
    高々1件」までしか積み上がらない(連続した回答が revision の乱発を
    生まない)。
- `run_refresh_job(job_id)`:
  1. セッションと同じ `session_id` に紐づく in-process lock
     (`_lock_for_session`)を取得し、このセッションの job 実行を直列化
     する。
  2. job が `pending` でなければ即座に no-op で返る(同じ job を2回実
     行しても2回目は何もしない — 冪等性)。
  3. 「このセッションで、より新しい(`id` が大きい)job が既に
     `updated` で完了している」場合は、この job を `status='stale'` に
     し、何も書き込まずに返る(実行順序が入れ替わって古い job が後から
     動いても、新しい結果を上書きしない)。
  4. `qa_answer` だけは `_understanding_update_blocked` が真(確定済み
     で新しい Q&A が無い)なら再構築をスキップし、
     `status='updated'` + 固定注記で終える。`alignment_answer` は Q&A
     watermark と別の人間入力なので gate を迂回する。
  5. `qa_answer` / `alignment_answer` は
     `routes/interview._rebuild_understanding` を呼ぶ
     (`update_interview_understanding` と同じ reasoning・永続化コード)。
     `intent_update` は desired state のみ、`nl_change_set` は選択された
     revision edit を永続化済みなので Understanding を再生成せず、
     最新 revision をそのまま次の Alignment build に渡す。rebuild が
     失敗すれば `status='failed'` + エラー内容 + reasoning run id を
     記録して終了し、保存済み回答は変更しない。
  6. 成功したら続けて `routes/interview_alignment.run_alignment_build`
     を呼ぶ(`POST .../alignment/build` と同じコードパス)。Alignment
     側の失敗は Understanding の成功を無効化しない — job は `updated`
     のまま、`error` に Alignment 失敗のメモを残す(Understanding は既
     に永続化済みであり、それを握りつぶして `failed` にする方が
     Principle 1 に反するため)。
  7. job の `intelligence_run_id`/`result_revision_id` を記録して
     `status='updated'` で終える。この2列と `understanding_revision`
     の `intelligence_run_id` を辿ることで job → intelligence_run →
     revision の監査系列が常に問い合わせ可能(Principle 7)。
  8. `run_refresh_job` はこの job を終えた後、同じセッションに
     `pending` job が残っていればそれを続けて実行する(ドレイン)。バ
     ックグラウンドスレッド1本の中で完結するため、追加のディスパッチ
     やポーリングを別途必要としない。

現在の実装は「セッション単位の直列実行」を in-process lock で保証して
いる前提で、`stale` 判定を job 開始前の1回のチェックに単純化している
(ブリーフが示す「reasoning 呼び出し後・書き込み前」の再チェックまでは
実装していない)。`db.get_conn()` がプロセス全体で単一のグローバルロッ
クを介して DB アクセスを直列化する既存設計のもとでは、同一セッション
の2 job が本当に同時に書き込むことは構造的に起こり得ないため、この単
純化は安全側に倒れている。

### 実行モデル・eager モード

`system_understanding_jobs.py` の「バックグラウンドスレッドで実行し、
状態は DB 行に永続化する」という既存パターンに倣う。ただし #288 の
job は単一ステップなので、専用のステップ/ハートビート/キャンセルの仕
組みは持たない(必要になれば #109 のパターンへ寄せる余地を残す)。

環境変数 `PROBE_REFRESH_EAGER`(デフォルト `0`/`false`)を `1` にする
と、`request_refresh` がディスパッチする最初の job をバックグラウンド
スレッドではなく呼び出し元のスレッドで同期的に実行する。テストで決定
的にアサーションするための切り替えで、`tests/test_interview_refresh.py`
がテストごとに `monkeypatch.setenv("PROBE_REFRESH_EAGER", "1")` してい
る。

### ルート(`routes/interview_refresh.py`、`main.py` に登録)

- `GET /interview/sessions/{id}/refresh-status` — `{latest_job: {status,
  trigger_kind, error, created_at, finished_at, result_revision_id, ...},
  pending_count}`。Dashboard のステータスチップが参照する。
- `POST /interview/sessions/{id}/refresh-jobs/{job_id}/retry` —
  `failed` の job だけを再試行できる(それ以外は 409)。既存の手動
  「理解を更新」エンドポイントと同様、障害復旧・診断用の明示操作。
  内部的には `request_refresh` と同じデデュープ経路を通る新しい
  `pending` job を発行する(失敗した行自体は書き換えない — 監査履歴と
  して残す)。

既存の手動「理解を更新」(`POST .../update-understanding`)はそのまま
残る(障害復旧・診断用)。その内部実装は本 issue で
`routes/interview._rebuild_understanding` として抽出し、409 ゲート
(`_understanding_update_blocked`)を持つのはこのエンドポイントだけ、
という既存の契約はそのまま維持している。

### Dashboard

- `RefreshStatusChip`(`components/system-understanding/refresh-status-chip.tsx`)
  を「現在の理解」カードと「レビューキュー」カードの両方のヘッダーに表
  示する。`pending|updating|updated|failed|stale` を
  「更新待ち…/更新中…/更新済み/更新に失敗しました/古い結果を破棄しま
  した」に日本語マップし(Issue #266 の規約どおりクライアント側の固定
  マッピング)、`failed` のときだけ「再試行」ボタンを表示する。
  `useRefreshStatus` は job が `pending`/`updating` の間だけ2秒間隔で
  ポーリングし、回答/決定の各 mutation フックは成功時にこのクエリを直
  接無効化する(ポーリングの次の tick を待たずに反映するため)。
- 「理解を更新」ボタンは残すが、二次的な操作(`variant="ghost"`)に位
  置づけ、カード説明文に「通常は回答後に自動で更新されます」という補
  足を添える。回答後に押す必要がある、という UI 上の要求は取り除いた。

### テスト

- `tests/test_interview_refresh.py`: 回答 → job 実行 → 新 revision +
  Alignment 再構築 + `refresh-status` 反映のエンドツーエンド(eager
  モード)、reasoning 失敗時も回答自体は残ること + 失敗 job の retry が
  回復すること、`updating` 中の複数回トリガーが `pending` 1件までしか
  積まないこと、古い job が新しい job の後に実行されると `stale` にな
  り revision を上書きしないこと、同じ job の2回実行が冪等であること
  (revision/alignment_item が重複しないこと)、Alignment が生成した
  `must_review` item が Review Queue に現れること、job →
  intelligence_run → revision の監査系列が辿れること(prompt/schema
  version の契約チェック込み)、2スレッドが同じ job を同時に実行して
  も再構築が1回しか起きないこと、確定済みで新しい回答が無いときは
  rebuild をスキップして `updated` + 注記になること。
- Dashboard: `src/__tests__/refresh-status-chip.test.tsx`(job が無け
  れば何も出さないこと、各 status の日本語ラベル、`failed` のときだけ
  再試行ボタンが出て retry API を呼ぶこと)。

## 自然文一括修正(Issue #289)

開発者が複数の理解項目にまたがる修正をまとめて自由文で書いても、その自
由文が直接状態に反映されることは一切ない(Principle 2/6)。reasoning
LLM(fail-closed)が構造化された change item に変換し、決定的な post-
pass が各項目の対象を有限候補リストに対して解決し、開発者がフィールド
単位の diff + 決定的な影響プレビューを確認したうえで選択した項目だけを
適用する。

### テーブル(additive)

`understanding_change_set`(1回の投稿につき1行):

| カラム | 型 | 説明 |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `session_id` / `system_id` | INTEGER NOT NULL | |
| `base_revision_id` | INTEGER NULL | 提案時点の最新 `understanding_revision.id`(understanding_claim 系項目の古さ判定の基準) |
| `source_text` | TEXT NOT NULL | 開発者が入力した自由文そのもの(監査用。状態には決して直接反映しない) |
| `status` | TEXT NOT NULL DEFAULT `'proposed'` | `proposed\|previewed\|partially_applied\|applied\|discarded\|failed` |
| `intelligence_run_id` | INTEGER NOT NULL | この提案を生成した `intelligence_runs` 行(`run_type='nl_change_set'`) |
| `is_mock` | INTEGER | |
| `created_at` / `updated_at` | REAL | |

`understanding_change_item`(1提案項目につき1行):

| カラム | 型 | 説明 |
| --- | --- | --- |
| `id` / `change_set_id` / `system_id` | INTEGER | |
| `target_kind` | TEXT NOT NULL | `intent_item\|understanding_claim`(有限集合。構造上これ以外は存在し得ない — LLM の生スキーマ自体がこの2値の Literal で検証される) |
| `target_ref` | TEXT NOT NULL(JSON) | `{"intent_item_id": ...}` または `{"section": ..., "name": ...}`(未解決時は `{"hint": ...}` を保持し、何を狙っていたかの監査だけ残す) |
| `field` | TEXT NOT NULL | `value_text`(intent_item 用)または `summary`(understanding_claim 用)。この2ペア以外は構造的に `forbidden` になる |
| `before_value` / `after_value` | TEXT | 解決できた場合のみ `before_value` が入る |
| `reason` | TEXT NOT NULL | LLM が付けた短い理由(自由文だが、状態そのものではなく提案の説明に過ぎない) |
| `resolution_state` | TEXT NOT NULL | `resolved\|ambiguous\|conflict\|stale\|forbidden` |
| `applied` / `applied_at` | INTEGER / REAL | 二重適用防止フラグ |
| `created_at` | REAL | |

### 決定的な対象解決(`app/change_sets.py`)

- `generate_change_set_proposal`: Intent Brief / Alignment と同じ
  fail-closed パターン(mock・非 reasoning モデル・API 失敗・スキーマ
  検証失敗はすべて `error` 付きの結果を返し、何も永続化しない)。プロ
  ンプトには「候補リスト」(`{kind, hint, current_value}` の有限配列)
  を渡し、モデルは `target_hint` にその `hint` を**一字一句そのまま**
  返すことしか許されない — 曖昧な言い換えやファジーマッチは一切な
  い。生レスポンスの `target_kind` は `Literal["intent_item",
  "understanding_claim"]` でスキーマ検証される(Alignment の
  `alignment_state`/`confidence` などと同じく、範囲外の値はバッチ全体
  を失敗させる)。`field` はここでは検証しない自由文字列のまま
  — 「有効な `target_kind` に対して許されていない `field` を狙う」と
  いう、まさに `forbidden` が拾うべき現実的なケースを個別項目の判定に
  残すため。
- `resolve_change_set_items`(純粋関数、DB 非依存): `(target_kind,
  field)` が `ALLOWED_TARGET_FIELDS` のホワイトリスト(`intent_item` →
  `value_text`、`understanding_claim` → `summary` の2エントリのみ)と
  完全一致しなければ `forbidden`。一致すれば `(target_kind,
  target_hint)` を候補リストに対して**完全一致**で引き、一致がちょう
  ど1件なら `resolved`(`before_value`/`target_ref` を確定)、0件また
  は複数件なら `ambiguous`(タイポで一致しない場合も、本当に複数候補
  がある場合も、人が入力し直すべき点は同じなので同じ状態にまとめる)。
  同じ入力からは常に同じ出力(決定性のテスト契約)。
- `effective_resolution_state`: `resolved` 項目だけを対象に、呼ばれる
  たびに現在の状態へ再検証する。`ambiguous`/`forbidden` は作成時点で
  確定した構造的事実で変化しない。再検証が拾う2つの退行:
  - `stale`(`understanding_claim` のみ): この change set の
    `base_revision_id` が、その時点の最新 `understanding_revision.id`
    と一致しない(理解がその後リビルドされた)。`intent_item` はこの
    方式でリビジョン管理されないため対象外。
  - `conflict`: 対象の現在値が記録済み `before_value` と一致しない、
    または(`intent_item`)対象行が既に superseded/削除された。
  このロジックはプレビュー(`GET`、表示のみで永続化しない)と適用
  (`POST .../apply`、実際にスキップした項目だけ永続化する遷移)の両方
  から同じ関数で呼ばれるため、プレビューと適用が食い違うことはない。

### ルート(`routes/interview_change_sets.py`、`main.py` に登録)

1. `POST /interview/sessions/{id}/change-sets` `{text}` — 候補リストを
   構築(現在の非 superseded `interview_intent_item` + 最新
   `understanding_revision.current_understanding` の5セクション)し、
   reasoning LLM を呼び、`understanding_change_set` + 解決済み
   `understanding_change_item` 群を作成する。失敗時も
   `status='failed'` の change set 行だけは監査のために残し(項目は
   0件)、502 を返す — NL は絶対に直接適用されない。
2. `GET /interview/change-sets/{id}` — フィールド単位の diff
   (`before_value → after_value`)、`effective_resolution_state` で再検
   証した `resolution_state`、決定的な影響プレビュー
   (`_affected_alignment_items`: `intent_item` 対象は
   `alignment_item.intent_item_id` 一致、`understanding_claim` 対象は
   `current_claim`/`intent_summary` への構造的な部分一致で拾う)、固定
   の日本語注記(`rebuild_note`、`state_messages.change_set_message`)
   を返す。初回参照時に change set の `status` を `proposed` →
   `previewed` に進める(項目の解決状態自体は書き換えない読み取り専用
   の遷移)。
3. `POST /interview/change-sets/{id}/apply` `{item_ids}` — 指定された
   項目だけを、この瞬間に再検証してから適用する(バッチ全体ではなく項
   目単位の fail-closed)。`intent_item` 対象は Issue #284 の
   `correct_intent_item` と同じ supersede 行パターン
   (`origin='user'`, `decision_method='manual'`,
   `source_statement=<投稿した自由文>`)。`understanding_claim` 対象は
   同じ apply 呼び出し内のすべての claim 編集をまとめて1つの新しい
   `understanding_revision`(最新リビジョンをディープコピーして編集、
   既存行は絶対に書き換えない)にする — 個別にリビジョンを作ると、先
   に適用した編集が後続の編集を誤って `stale` にしてしまうため。適用
   後は `interview_refresh.request_refresh(trigger_kind='nl_change_set')`
   を呼ぶ(Issue #288 の自動更新に接続)。`change_set.status` は「選択
   した項目が全部適用できた」なら `applied`、一部だけなら
   `partially_applied`。既に `applied=1` の項目を再度指定してもスキッ
   プされるだけで再適用はしない(冪等)。
4. `POST /interview/change-sets/{id}/discard` — `status='discarded'`。
   以降の apply は 409。

### Dashboard

- `ChangeSetPanel`(`components/system-understanding/change-set-panel.tsx`)
  を Interview ページのレビューキュー直下に配置。「まとめて修正」の
  テキストエリア + 送信ボタンで変更案を作成し、フィールド単位の diff
  一覧をプレビュー表示する。`resolution_state` を「適用可能/あいまい/
  競合/古い前提/変更不可」に日本語マップ(Issue #266 の規約どおりクラ
  イアント側の固定マッピング、canonical enum はログ/ペイロードのみ)。
  `resolved` かつ未適用の項目だけ既定でチェック済み、それ以外は既定で
  未選択かつ操作不能(誤って選択できない)。「選択した変更を適用」は
  チェック済みの `resolved` 項目 id だけを送る。適用後は
  `RefreshStatusChip`(Issue #288)が自動更新の反映を示す。

### テスト

- `tests/test_change_sets.py`: `resolve_change_set_items` /
  `effective_resolution_state` の決定性(resolved/ambiguous/conflict/
  stale/forbidden の各状態を fixture から再現)、
  `generate_change_set_proposal` の fail-closed(mock・非 reasoning モ
  デル・API 失敗・不正 JSON・範囲外 `target_kind` の各ケース)、作成→
  プレビュー(影響プレビュー込み)→部分選択→適用のエンドツーエンド
  (選択+resolved のみ適用、未選択/未解決項目は無変更、intent 対象は
  supersede 行トレイルを作る、understanding_claim 対象は新しい
  revision を作り旧 revision は変更されずに残る、apply が Issue #288
  の refresh job を発行すること)、LLM 失敗時は `failed` になり何も適
  用されないこと、apply 時点で base revision が古くなっていれば
  `stale` としてスキップされること、ホワイトリスト外の `field` は
  `forbidden` として拒否されること、同じ項目への2回目の apply が
  no-op になること、change_set → intelligence_run → revision の監査系
  列。
- Dashboard: `src/__tests__/change-set-panel.test.tsx`(diff プレビュー
  表示、非 resolved 項目のチェックボックスが既定で外れ無効化されるこ
  と、apply が選択済み id だけを送ること、discard が discard API を呼
  ぶこと)。

## Runtime Reality Check 統合(Issue #290)

既存の Runtime Reality Check(Issue #135、`app/runtime_reality.py`)の
決定的トレース集計を、Investigation Agent(#286)の証拠種別・Alignment
Review(#287)の Review Queue 判定・新規の観測提案フローへ接続する。
「新しい runtime 事実をどう解釈するか」は reasoning モデルの仕事のまま
だが、「その事実が今なお現在のものと呼べるか(鮮度・有無・環境)」は
決定的ルールだけで判定する(Principle 6)。

**Finding 5 の修正(レビュー指摘)**: 初期実装は (a) `traces` に
environment/version 情報が無く `compare_claim_to_runtime` の environment
mismatch 分岐が実データで到達不能、(b) `build_provenance` がセッション
の pinned snapshot を `snapshot_ref` として渡していた(トレースを送った
デプロイが実際にどの snapshot かは分からないのに)、(c) claim テキスト
が意味的に一切判定されず新鮮なトレースは常に `match` になり
`runtime_mismatch → must_review` ルールが実データで到達不能、という 3
つの欠陥を持っていた。以下はその修正後の仕様。

### SDK からの provenance 取得(`PROBE_ENVIRONMENT` / `PROBE_GIT_SHA`)

`packages/python-probe/probe_agent/config.py` に `ENV_ENVIRONMENT`
(`PROBE_ENVIRONMENT`)/`ENV_GIT_SHA`(`PROBE_GIT_SHA`)を追加。両方とも
未設定/空文字なら `None`(捏造しない)。設定されている場合のみ
`decorator.py` が trace ペイロードに `environment`/`git_sha` を追加する
(read が失敗しても対象関数・trace 送信は壊れない、既存の replay
capture と同じ best-effort パターン)。`shared/schemas/trace_event.schema.json`
・`TraceEvent`(server)・`traces` テーブル(`environment TEXT`,
`git_sha TEXT`、additive `ALTER TABLE`)がこれを additive に受け取る。

### 出所エンベロープ(`RuntimeFactProvenanceOut`)

`aggregate_component_facts` が返す `RuntimeTraceFactsOut` に
`observed_environment` / `observed_git_sha` を additive に追加: 集計ウィ
ンドウ内で `environment`/`git_sha` が非 NULL・非空文字列の行のうち
`timestamp` が最大のものを1件ずつ選ぶ(決定的な「最新観測値」ピック。
列ごとに1クエリ)。1件も無ければ `None`。

`app/runtime_reality.py` の `build_provenance(fact, *, conn=None,
system_id=None, now=None)` がこれを次の形にラップする:

```
{
  environment: str | null,       # fact.observed_environment そのまま(捏造しない)
  first_observed_at: float | null,
  last_observed_at: float | null,
  snapshot_ref: {snapshot_id: int | null, git_sha} | null,
  source: "trace_aggregation",   # 固定値
  freshness: "fresh" | "stale" | "unobserved",
}
```

`snapshot_ref` は `fact.observed_git_sha` が無ければ常に `null`
(Finding 5(b) の再発防止 — **セッションの pinned snapshot を絶対に代入
しない**)。sha が観測されていれば、`conn`/`system_id` が両方渡された時
だけ `repository_snapshots`(その System)を commit_sha 完全一致で検索
し、見つかった `id` を `snapshot_id` に入れる。見つからなくても
`git_sha` は生の観測値のまま保持し(`snapshot_id` は `null`)、`conn`/
`system_id` を渡さなかった呼び出し元でも同様に `git_sha` だけは保持され
る。呼び出し元(`routes/interview_alignment.py::run_alignment_build`、
`investigation_agent.py::_gather_runtime_candidates`)はどちらも
pinned snapshot_id/commit_sha を渡さなくなった。

`freshness` は `freshness_for()` が決定的に計算する: トレースが1件も
無ければ `unobserved`、`last_observed_at` が `RUNTIME_FACT_FRESH_SECONDS`
(環境変数、デフォルト 7 日 = 604800 秒)以内なら `fresh`、それ以外は
`stale`。鮮度が古い事実が「最新」として提示されることは無い(stale
guard)。

### 有限マッチ状態(`app/runtime_alignment.py`)

- `resolve_component_for_evidence(conn, snapshot_id, evidence)` —
  evidence の `{path, start_line, end_line}` を `code_symbols`(Issue
  #24 の Feature-to-Code index)に対して構造的に突き合わせ、
  `component_id` を決定的に1つだけ解決する。0件または複数件の異なる
  `component_id` にマッチした場合は `None`(推測しない)。
- `compare_claim_to_runtime(claim, fact, provenance, *,
  expected_environment=None)` — `match | mismatch | unobserved | stale`
  を返す。`claim`(自由文)は監査上の引数として受け取るだけで一切解析
  しない。判定は `provenance.freshness` のみ(`unobserved`/`stale` は
  そのまま返す)と、`expected_environment`(呼び出し元が渡す Systemの
  `environment` 列。空文字は未知として扱い None 同等)と
  `provenance.environment`(実際にトレースで観測された値)が両方既知
  かつ不一致のときの `mismatch` だけ。SDK が `PROBE_ENVIRONMENT` を送る
  ようになったことで、この分岐は今や実データ(構築した
  `RuntimeFactProvenanceOut` に頼らないエンドツーエンドの trace 挿入)
  で到達できる。それ以外は `match`。「振る舞いが意味的に一致している
  か」の判断は下記の Runtime Match Judge(Alignment Review 側)や
  Investigation Agent の仕事で、この関数の仕事ではない。

### Investigation Agent への統合(#286 拡張)

`investigate()` に `conn`/`system_id`/`snapshot_id`(すべて省略可、既定
`None` — 省略時は #286 と完全に同じ read-only/git-only 挙動)を追加。
指定された場合、コード証拠として既に選ばれた候補ファイル(`candidates`)
と同じパス集合から `code_symbols.component_id` を引き、
`InvestigationBudget.max_runtime_facts`(既定 10、範囲 0〜20)件までの
runtime fact 候補をプロンプトに追加提示する。モデルは
`runtime_evidence: [{component_id, runtime_check, summary}]` として引用
できる(コード証拠と同じく、提示された `component_id` 以外は引用できな
い — 未知の引用は破棄されるだけで、コード証拠と違いこの拡張だけでは
実行全体を fail-closed にしない)。**stale guard**: 決定的ベースライン
(鮮度のみで計算、claim 抜き)が `unobserved`/`stale` の場合、モデルが
何と言おうと必ずその値で上書きして永続化する — モデルが古い/未観測の
事実を `match` と主張することは構造的にできない。ベースラインが
`match`(=新鮮なデータがある)のときだけ、モデル自身の
`match`/`mismatch` という意味的判断を採用する。この意味的判断は
Investigation Agent 自身の会話フロー内に限定される(引用として提示され
るだけで `alignment_item.runtime_check` には反映されない)。Alignment
Review 側の同種の判断は下記の Runtime Match Judge という別のコード経路
が担当する。

### 段階的開示(Progressive disclosure)

Inquiry の assistant メッセージの `content`(最初に見える結論)には
runtime の生データ・出所情報は一切含めない。`runtime_evidence` は既存
の `detail` JSON 展開レイヤー(`InterviewInquiryMessageDetailOut`)に
だけ入る。同じ detail に `suggested_observation_proposal`
(`{target_component, reason: "unobserved"|"stale"}` または `null`)も
入る — これは固定テンプレートによるヒントであり、これ自体は提案レコー
ドを一切作らない。

### Alignment Review / Review Queue への統合(#287 拡張)

`alignment_item` に additive カラム `runtime_check TEXT NULL`
(`match|mismatch|unobserved|stale`)を追加。`run_alignment_build` は
各 item の検証済み evidence から `resolve_component_for_evidence` で
`component_id` を解決できたときだけ `aggregate_component_facts` +
`build_provenance` + `compare_claim_to_runtime`(`expected_environment`
は Systemの `environment` 列)を呼び、決定的ベースラインの
`runtime_check` を決定する。決定的マッピングが無ければ `null`(推測し
ない)。

`app/alignment.py` の `_RULES`(先勝ちルール表)に、`conflict_detected`
の直後・`low_confidence` の直前として次を追加:

```
runtime_check == 'mismatch' -> must_review, runtime_mismatch
```

`stale`/`unobserved` はそれ単独では must_review を強制しない。
`user_reason_for('runtime_mismatch')` の固定文言は
「コード上の理解と実行時の観測が一致していません」。

### Runtime Match Judge(Finding 5 Part 2、reasoning モデルによる意味判定)

決定的ベースラインは鮮度と環境一致しか見ないため、claim の *内容* が新
鮮なトレースと意味的に矛盾していても検出できなかった(`runtime_mismatch
→ must_review` ルールが実データで到達不能という Finding 5(c))。
`app/runtime_match_judge.py` の `judge_runtime_match(client, config,
items, *, language)` がこの意味的判定を追加する:

- 対象は決定的ベースラインが **`match`(新鮮・構造的な矛盾なし)の item
  だけ**。`stale`/`unobserved`/`mismatch`(environment)は絶対に judge
  に渡さない — stale guard と同じ思想で、モデルはこれらの決定を一切上
  書きできない(Principle 6)。
- 1バッチで全 eligible item をまとめて1回だけ呼ぶ。
  `PROMPT_VERSION`/`SCHEMA_VERSION` は `"runtime-match-v1"`。
- 構造化出力 `{items: [{index, runtime_check: "match"|"mismatch",
  note}]}` を厳密検証: 集合外の `runtime_check`、未知/重複/欠落した
  `index` はどれか1つでもあれば **レスポンス全体を無効**とする
  (fail-closed、部分採用しない)。
- mock/非 reasoning モデル・LLM エラー・不正な構造化出力はいずれも
  `error` を返す。`run_alignment_build` はこのとき対象 item の
  `runtime_check` を `NULL` として永続化する(**推測せず、決定的な
  `match` ベースラインへフォールバックもしない**)。
- `intelligence_runs` に必ず1行記録する(`run_type='runtime_match'`、
  `decision_method='reasoning_llm'`、成功/失敗どちらも
  `status`/`error_details`/prompt・schema version/`is_mock` を保存)。
  eligible item が 0 件のときは LLM 呼び出し自体をスキップし、この行も
  作らない。
- judge の `note` は監査目的の一時情報であり、`alignment_item` には永続
  化しない(決定的事実と reasoning 出力を分離して保存する原則どおり、
  永続化されるのは finite enum の `runtime_check` だけ)。
- `run_alignment_build` は各 item の `classify_alignment_item` 呼び出し
  を judge 実行後まで遅延させる: 決定的ベースラインと judge 判定のどち
  らが最終的な `runtime_check` になっても、`_RULES` の
  `runtime_mismatch` ルールが正しく評価される。

### 観測提案(承認ゲート、新規テーブル `runtime_observation_proposal`)

新しい runtime 観測を「開始する」ことは、この Issue のどのコード経路
からも自動実行されない(Principle 5/8)。開発者の依頼は additive な
`runtime_observation_proposal` テーブルに `status='proposed'` として記
録されるだけ:

| カラム | 型 | 説明 |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `session_id` / `system_id` | INTEGER NOT NULL | System-scoped |
| `origin_inquiry_id` / `origin_alignment_item_id` | INTEGER NULL | 任意の監査リンク |
| `target_component` | TEXT NOT NULL | |
| `purpose` / `expected_cost` / `risk_note` / `retention_note` | TEXT | 開発者が入力 |
| `status` | TEXT NOT NULL DEFAULT `'proposed'` | `proposed\|approved\|rejected\|expired` |
| `decision_by` / `decision_at` | TEXT / REAL NULL | 手動承認のみ(`decision_method: manual`) |
| `created_at` | REAL NOT NULL | |

ルート(`routes/interview_observation.py`):

- `POST /interview/sessions/{id}/observation-proposals` — 提案を作成
  するだけ。
- `GET /interview/sessions/{id}/observation-proposals` — 一覧
  (`status` で絞り込み可)。
- `POST /interview/observation-proposals/{id}/approve` /
  `.../reject` — `status='proposed'` の行だけを遷移できる(409 それ以
  外)。**承認しても観測は開始されない** — レスポンスの `policy_pointer`
  は固定テンプレート文言(`interview_language.py` の
  `observation_proposal_policy_hint`)で、既存の
  `PUT /components/{component_id}/policy` を指し示すだけ。interview/
  investigation 系のどのコードパスも `components` テーブル(ポリシー)
  へは一切書き込まない — これは回帰テストで固定する。

新しい runtime 事実が届いた後(テストではトレースを直接 INSERT して模
擬する)、Issue #288 の自動 refresh を再実行すると `run_alignment_build`
が再度呼ばれ、`runtime_check` が更新される(revision の系列は既存の
リビジョン/監査の仕組みでそのまま追跡できる)。

### 環境変数

- `RUNTIME_FACT_FRESH_SECONDS`(デフォルト `604800` = 7日)— runtime
  fact が `fresh` とみなされる上限秒数。`RUNTIME_REALITY_CHECK_*` と同
  じ正の整数パーサーを使い、不正な値は 422 で fail-closed する。
- `PROBE_ENVIRONMENT` / `PROBE_GIT_SHA`(SDK、`packages/python-probe`)—
  デプロイの provenance タグ。両方とも既定は未設定(`None`)で、trace
  ペイロードに新フィールドは付かない。設定した場合のみ
  `traces.environment`/`traces.git_sha` として永続化され、
  `aggregate_component_facts`/`build_provenance` の唯一の入力になる。

### テスト

- `tests/test_runtime_reality.py` 拡張: `aggregate_component_facts` の
  `observed_environment`/`observed_git_sha`(最新観測値ピック、空文字/
  NULL の除外、後続の値なしトレースが以前の観測値を上書きしないこと、
  System 分離)。
- `tests/test_runtime_alignment.py`: `freshness_for`/`build_provenance`
  の時刻固定フィクスチャ(fresh/stale/unobserved の境界、環境変数上書
  き)、`compare_claim_to_runtime` の全分岐(unobserved 優先 > stale
  優先 > environment mismatch > match、stale な事実が絶対に `match` に
  ならないこと)、`resolve_component_for_evidence` の一意/曖昧/無マッ
  チ、`build_provenance` の新シグネチャ(`conn`/`system_id` 省略時は生
  の sha のみ保持、sha 未観測なら pinned snapshot があっても
  `snapshot_ref` が絶対に `null` になる捏造防止回帰テスト、commit_sha
  完全一致での解決/非解決)。
- `tests/test_runtime_match_judge.py`(新規): `judge_runtime_match` の
  mock/非 reasoning モデル拒否、LLM エラー、不正 JSON、集合外
  `runtime_check`、未知/重複/欠落 index のいずれもレスポンス全体を無効
  にすること、成功バッチの検証済み結果。
- `tests/test_interview_alignment.py` 拡張: `classify_alignment_item`
  への `runtime_check='mismatch'` が `must_review`/`runtime_mismatch`
  になること(conflict_detected より後・low_confidence より前の優先順
  位を含む)、`run_alignment_build` 経由の統合テスト(evidence を
  `code_symbols` にマッピングして runtime_check を決定的に付与、
  environment mismatch は実際に `environment` 付きトレースを INSERT し
  て到達させる)、Runtime Match Judge の統合(成功バッチの mismatch 判
  定が must_review に反映される、judge 失敗時は `runtime_check=NULL` +
  失敗した `runtime_match` 監査行を残しつつ build 自体は成功する、
  stale/unobserved item は judge に一切送られず監査行も作られないこと)。
- `tests/test_investigation_agent.py` 拡張: runtime_evidence の
  budget/schema 検証、stale/unobserved の上書きガード、未提示
  `component_id` の引用が破棄されること、`conn` 省略時は従来どおり
  runtime_evidence が空であること。
- `tests/test_interview_observation.py`(新規): 提案の作成/一覧/承認/
  却下のライフサイクル、`proposed` 以外からの承認・却下が 409 になる
  こと、承認後も `components` テーブルが一切変化しないこと(否定テス
  ト)。
- 進行的開示: assistant メッセージの `content` に生トレース/出所デー
  タが含まれないこと(`detail.runtime_evidence` にだけ入ること)を
  コンポーネント/ユニットテストで固定。
- `tests/test_schemas.py` 拡張: `trace_event.schema.json` が
  `environment`/`git_sha` を受理すること、SDK が実際に組み立てた
  `PROBE_ENVIRONMENT`/`PROBE_GIT_SHA` 付き payload がスキーマとサーバ
  モデルの両方を通ること。
- `tests/test_api.py` 拡張: `POST /traces` が `environment`/`git_sha` を
  永続化すること(設定時/未設定時の両方)。
- `packages/python-probe/tests/test_decorator.py` 拡張: 設定時に trace
  payload へ `environment`/`git_sha` が追加されること、未設定/空文字な
  ら追加されないこと、読み取り失敗時も対象関数の返り値・trace 送信が
  壊れないこと。

## 回答可能領域と担当者引き継ぎ(Issue #291)

開発者が「今回自分が答えられる領域」を明示的に選び(ロールからの推論は
しない、Principle 6)、担当外の質問・Review Queue 項目は非表示にせず別
グループへ回す。引き継いだ回答は必ず元のユーザーの明示確認を経てから
本来の回答として確定する(Principle 2)。

### 回答可能領域(`interview_session.answerable_areas`)

- 有限集合 `KnowledgeArea`: `product_intent | domain_rule | operations |
  implementation | security`。`app/models.py`(`InterviewSessionOut`
  より前)と `app/question_router.py`(`KNOWLEDGE_AREAS`)の双方に定義
  し、値を一致させる。
- `interview_session.answerable_areas`(additive、`TEXT NOT NULL DEFAULT
  '[]'`、JSON 配列)。`PUT /interview/sessions/{id}/answerable-areas
  {areas: [...]}` でいつでも変更できる。**空配列は「フィルタなし」**
  (#291 以前と同じ全件表示)であって「全領域を選択した」ではない —
  この違いは UI・API どちらでも明示する。

### 質問への領域タグ付け(`interview_qa.knowledge_area`)

- `interview_qa.knowledge_area`(additive、`TEXT NULL`)。Question
  Router(#286、`app/question_router.py`)の reasoning モデル呼び出しで
  のみ設定される。プロンプト/スキーマへ `knowledge_area`
  (finite enum または null)を additive に追加し、当時の
  `PROMPT_VERSION` / `SCHEMA_VERSION` を `question-router-v2` へバンプ
  (Principle 7。後続のレビュー指摘修正で `search_keywords` が additive
  に加わり、現在は `question-router-v3`)。fail-closed: 集合外の値はエ
  ラー、`null` は正当な「どの領域にも当てはまらない」判定として受理す
  る。タイトルやリポジトリ情報からの決定的推論は行わない(Principle
  6)。未ルーティング(`null`)の質問は絶対に非表示にしない。
- (レビュー指摘修正、Finding 1)本 issue 実装時点では `knowledge_area`
  は Inquiry の自動ルーティングと単体ルーティングエンドポイントからしか
  設定されず、通常の Q&A フローで質問を作成しただけでは null のまま
  だった。バッチ版の `POST
  /interview/sessions/{session_id}/qa/route-and-investigate`(「Question
  Router / Investigation Agent(Issue #286)」節参照)が通常フローにも
  ルーティングを接続したことで、開発者が明示的にバッチ実行すれば
  `knowledge_area` が通常フローでも埋まるようになった。それでも自動実
  行ではなく開発者の明示操作(ボタン押下)が起点である点は変わらない。

### 決定的な対象外判定

`routes/interview.py::_is_out_of_area` — 質問が現在のユーザーにとって
「対象外」なのは、`session.answerable_areas` が空でなく **かつ**
`question.knowledge_area` が非 null **かつ** その領域が
`answerable_areas` に含まれない場合のみ。対象外の質問は非表示にせず、
別グループへ分類し 後で回答 / 保留 / 引き継ぐ の操作を提供する。
「わからない」を低確信度の Yes/No などへ変換することは決してしない。

### 引き継ぎ(`question_handoff` テーブル、`routes/interview_handoff.py`)

System スコープの additive テーブル。`origin_kind` は有限集合 `qa |
review_item`(#285/#287 と同じ finite origin_kind パターン)。
`assignee` / `created_by` / `answered_by` は自由文の担当者名/連絡先
(組織的な認証システムは無いため、既存の `understanding_confirmed_by` /
`interview_qa.answered_by` と同じ慣習を踏襲)。

引き継ぎの作成は元の項目の回答・判断フィールドへは一切書き込まない:

- `origin_kind='qa'`: `interview_qa.status` はそのまま変更しない
  (既存の finite set を上書きしない、#285/#287 の方針を踏襲)。
  additive な `interview_qa.handoff_id` だけを設定する。
- `origin_kind='review_item'`: `alignment_item.status` を
  `'held'`(既存の `/hold` エンドポイントと同じ値)にし、additive な
  `alignment_item.handoff_id` を設定する(`user_decision` は書き込ま
  ない — 引き継ぎは判断ではない)。

ライフサイクル(有限遷移表、Principle 6):

```
pending -> answered | cancelled
answered -> returned
それ以外 -> 409
```

- `POST /interview/sessions/{id}/handoffs` — 作成、`pending` で開始。
  対象項目に既に in-flight(`pending`/`answered`)な引き継ぎがある場合
  は `409 handoff_already_in_flight`。
- `GET /interview/sessions/{id}/handoffs?status=` — 一覧・状態フィルタ。
- `POST /interview/handoffs/{id}/answer {answer_text, answered_by}` —
  担当者自身の回答を **引き継ぎ行にだけ** 記録する(`answered` へ遷
  移)。元の `interview_qa`/`alignment_item` 行は一切変更しない。
- `POST /interview/handoffs/{id}/return` — `returned` へ遷移。UI は
  担当者の回答を元の項目に「引き継ぎ先の回答(未確定)」として表示す
  る。ここでも元の行への書き込みは無い。
- `POST /interview/handoffs/{id}/cancel` — `pending` からのみ
  `cancelled` へ。

### 明示確認による確定

`return` された引き継ぎは、元のユーザーが **既存の回答エンドポイント**
経由で明示的に確定する。`interview_qa` の
`POST .../qa/{id}/answer` を additive に拡張し、任意の `handoff_id` を
受け付ける:サーバーは当該 handoff が存在し、この質問に属し、
`status='returned'` であることを検証したうえで、通常どおり
`answer_text`/`actor` を記録する(担当者の回答をそのまま「元ユーザーの
回答」として書き込むことは絶対にしない — 開発者自身が
`answer_text`/`actor` を送信する)。検証失敗(未 return / 対象質問不一
致 / 存在しない)は 409/404。Alignment 側の `/answer`・`/correct` は本
issue では拡張しない(brief が明示的に要求していないため) — 引き継ぎ
の来歴は `alignment_item.handoff_id` と `status='held'` だけで追跡でき
る。

### 重複抑止(決定的)

`GET /interview/sessions/{id}/qa?view=askable` — 「次に回答する質問」
の一次フローが共有する単一のサーバー側フィルタ。除外条件:
回答済み(`status == 'answered'`)、in-flight な引き継ぎあり
(`handoff_id` が `pending`/`answered` の引き継ぎを指す)、対象外
(`_is_out_of_area`)。`view` を指定しない既存の一覧は挙動不変。

### Dashboard

- セッションヘッダーの「今回回答できる領域」チップ(5 領域、日本語ラ
  ベル: 事業・目的 / 業務ルール / 運用 / 実装 / セキュリティ)、
  `PUT` で即時反映、セッション中いつでも変更可能。
- Q&A パネル: 対象外の質問を「担当外の質問」として別グループ表示し、
  後で回答(既存 skip 再利用) / 担当者へ引き継ぐ(モーダル: 担当者・
  背景・決めてほしいこと・優先度・期限メモ)を提供する。
- Review Queue: 各項目に「担当者へ引き継ぐ」操作を追加(alignment_item
  は領域タグを持たないため対象外グルーピングは行わない — 引き継ぎ機能
  のみ)。
- 引き継ぎ一覧パネル: pending/answered/returned を日本語で表示。
  `returned` の項目は担当者の回答を「引き継ぎ先の回答(未確定)」と
  マークし、「この内容で回答を確定する」ボタンから通常の回答エンドポ
  イントを呼び出す(明示確定)。
- 生の enum 値をそのまま表示しない(Issue #266 規約)。

### テスト

- `tests/test_interview_handoff.py`(新規): 領域選択の検証・随時変更・
  空配列でのフィルタなし、askable ビューの回答済み/引き継ぎ中/対象外
  除外と null 領域の非表示なし、引き継ぎのライフサイクル・不正遷移
  409、`/answer` が元行に一切書き込まないこと(回帰)、
  `return` 後の明示確認で確定ユーザーと `handoff_id` 来歴が記録される
  こと、キャンセル経路、System 分離。
- `tests/test_question_router.py` 拡張: 当時の `question-router-v2` への
  バンプ、`knowledge_area` の null/各 enum 値の受理、集合外値の
  fail-closed、ルーティング結果の永続化(その後 `search_keywords` 追加
  で `question-router-v3` までバンプ済み。「Question Router /
  Investigation Agent(Issue #286)」節参照)。

## Interview Alignment UX 差分改善(Issue #295)

Issue #295 は Interview Alignment UX の元提案であり、その大部分は Issue
#282(サブイシュー #283-#291)で実装済み。本節は #295 のうち #283-#291
でカバーされていなかった差分として実装した内容と、意図的に見送った残課
題を記録する。

### unchanged 分類の実体化(#295 §4.3 / §7.1、control-server)

#287 で予約値だった `review_category='unchanged'` を決定的ロジックで到
達可能にした。

- `app/alignment.py` の `compute_content_hash()`: `current_claim` +
  正規化した `current_evidence`(path/start_line/end_line/summary をソー
  ト)+ ルール表 `classify_alignment_item` の入力になる全フィールド
  (`alignment_state` / `risk_flags`(ソート)/ `confidence` /
  `intent_field` / `runtime_check`)の canonical JSON を sha256 する。
  分類に影響する変化(Runtime Reality Check の反転を含む)が
  「変更なし」と誤判定されることはない。完全一致のみで、類似度・
  LLM 判断は使わない(Principle 6)。
- 再ビルド(`run_alignment_build`)時、直前ビルドの **`accept_current`
  回答済み行**(`status='answered'` かつ `user_decision.action=
  'accept_current'`、`superseded=0`。加えて多世代 `unchanged` チェーン)と
  `content_hash` が一致する新項目は `unchanged` /
  `reason_code='unchanged_since_confirmation'` に分類し、
  `carried_over_from` に引き継ぎ元 id を記録する(監査専用の参照。
  ON DELETE SET NULL)。`needs_change` / `reject_interpretation` /
  `corrected` は人間の異議・修正なので carry-over 対象外とし(3回目
  レビュー指摘1)、次の Understanding rebuild の reviewer prompt に
  還流する。新しい理解でもなお差分が残る場合はルール表で再分類され
  actionable のまま残る。unchanged 項目は `GET .../review-queue` の
  主導線(must_review/batch_reviewable フィルタ)に現れない。
- §5.5 の狭い決定的版: goal intent(System Purpose 相当)が直前ビルド
  以降に確定・変更された場合、そのビルドでは引き継ぎを行わず全項目を
  ルール表で再分類する。`trigger_kind` は `interview_refresh` の
  dedupe(pending ジョブへの合流)によりバッチ全体を代表しないため、
  goal 行の `updated_at` と直前ビルドの `alignment_item.created_at`
  最大値の比較で判定する。
- additive migration: `alignment_item.content_hash TEXT` /
  `carried_over_from INTEGER`(既存行は NULL のまま)。
- テスト: `tests/test_interview_alignment.py` に hash の決定性・順序
  非依存性、引き継ぎ、内容変化時の非引き継ぎ、goal 変更によるブロック
  と過剰ブロックの回帰、旧 DB からの migration を追加(98件)。

### Review Queue の表示・操作(#295 §4.1 / §5.3 / §5.4 / §4.4、dashboard)

`review-queue.tsx` のみの変更。分類・優先度はバックエンド値をそのまま
使い、フロントで再分類しない。

- カテゴリ別件数サマリ: 要確認 / 一括レビュー可 / 確認不要 / 前回から
  変更なし / 参考情報 の5固定区分を `counts` から表示(欠損は0扱い)。
- まとめて回答モード(既定OFF): 回答をローカルに保留し「まとめて送信」
  で既存 `/answer` を項目ごとに順次呼ぶ。#288 の refresh dedupe が
  バッチを1回の再ビルドにまとめるため一括 API は追加しない。失敗項目
  は保留のまま残し「N件送信、M件失敗しました」を表示。1件即時送信の
  従来 UX は不変。
- 確認不要/参考情報/unchanged 行の監査詳細展開(§5.3): 応答に存在する
  フィールドのみ(state・confidence・evidence・snapshot/revision・
  updated_at・intelligence run・carried_over_from/content_hash)を表示。
- サンプル確認(§5.4 最小版): no_review_required + informational から
  id 昇順で最大3件を決定的に選び、展開済み+「疑問がある」導線付きで
  提示(全件3件以下ならサンプル節なし)。誤り発見時は #310 の明示的な
  手動再確認へ接続し、分類ルールの自動調整は行わない。
- 根拠の先出し例外(§4.4): conflict / security・high_risk フラグ /
  runtime_check mismatch・stale / 根拠1件のみ、のとき EvidenceList を
  初期展開する(応答の有限フィールドからの決定的判定のみ)。

### Inquiry 段階開示の4段階化と「わからない」自動調査(#295 §4.4 / §4.8 / §4.10、dashboard)

- `inquiry-panel.tsx`: 従来の「結論 → detail 一括トグル」2段階を、
  結論(常時)/「理由を見る」(`key_points`)/「根拠を見る」
  (evidence の docs/code/test/Runtime 種別+要約+uncertainty)/
  「調査詳細を見る」(path:行番号・runtime provenance・調査 run 参照)
  の4段階に分割。種別はパス文字列からの構造的分類(file kind、
  Principle 6 の許容例)。バックエンド・スキーマ変更なし(既存
  detail payload の表示分割のみ)。
- 例外の先出し: `runtime_evidence[].runtime_check=='mismatch'` または
  `detail.uncertainty` 非空のとき第2〜3層を初期展開(第4層は自動展開
  しない)。
- `interview.tsx`: Q&A の「わからない」選択時、既存の
  route-and-investigate バッチ調査(#286/#291 で整備済みのエンドポイ
  ント)を自動起動する。調査中は「関連コードとテストを確認しています」
  の短い状態表示のみ。API 失敗・バッチ対象外時は従来の #142 フロー
  (`answer_unknown: true`)へ無条件フォールバックし、回答機会を失わ
  せない。実行中の重複発火は防止。`investigation_status='unresolved'`
  は調査成功(特定できず)として扱い、既存の表示に委ねる。

### 実装しない・見送った残課題

Issue #295 は実装レビュー完了によりクローズ済み。以下の残課題は
Epic #307 の子 Issue として起票し直した。

- **低リスク提案の一括承認(旧 #292 → #311)**: 引き続き実装しない
  (開始条件の観測データが未取得。計測手段は #309)。本節の「まとめて回答」は
  ユーザーが1件ずつ選んだ回答の送信バッチ化であり、AI 分類による
  自動承認ではない — `decision_method: manual` は項目単位で維持。
- **Inquiry の前提追跡(#295 §5.6 拡張 → #308)**: 完了。作成時に固定する
  前提 bundle(snapshot / revision / content hash / Capability digest /
  linked Intent digest / tracking version)、構造的 anchor だけで作る
  Review item の論点 identity と世代 lineage、Alignment 再ビルド内の決定的
  な前提評価と `superseded` 遷移、後継の再確認導線までを実装した。詳細は
  「Inquiry の前提追跡と superseded(Issue #308)」節。
- **評価指標(#295 §9 → #309)**: 集計基盤と接続済み収集点は実装済み。
  既存の永続データと有限な UI 計測イベントだけから System 単位に集計し、
  算出不能な指標は 0 や推定値ではなく `unmeasured` として返す。途中離脱と
  unchanged 再確認の収集意味は下記のとおり仕様判断を残す。
- **サンプル誤り発見時の分類ルール再評価(§5.4 後半 → #310)**: 完了。
  `no_review_required` の決定的サンプル（確認不要/参考情報の id 昇順先頭3件、
  全体4件以上）から既存 Inquiry を開くと、対象 `alignment_item` の
  `policy_rule_id` / policy version / policy digest / `reason_code` を
  `alignment_rule_objection` に不変の監査事実として記録する。System 単位の
  API はこの正確なルール来歴ごとに異議数を決定的に集計し、人間が選択した
  同じルール版の現行 `no_review_required` 項目だけを再確認対象へ戻せる。
  対象は session と物理 item ごとに分離し、同一 content hash でも潰さない。
  再確認化は決定的分類を変更せず、実行者と `decision_method: manual` を保存し、
  通常の手動回答/修正だけが現行対象を解消する。rebuild で内容が変わった旧対象は
  監査行を `superseded` として残し、現行の pending 件数には含めない。
- **再確認カスケードの範囲(§5.5 → #312)**: 完了。既存の goal / 確定済み
  Intent ガードに加え、reasoning model の `current_understanding` を提案、
  `confirm-understanding` を人間による正準構成の確定として分離した。
  Core Capability / Capability Element / Supporting Element / API Boundary は
  表示名と独立した System-scoped の安定 entity id を持ち、rename は人間が
  明示 binding した場合だけ同一 identity を引き継ぐ。支援関係も安定 relation
  id の多対多グラフなので、同じ下位機能を複数 Core Capability が共有できる。
  確定履歴は System-wide の canonical head と `base_confirmation_id` で直列化し、
  確定requestは表示時のhead idをoptimistic lockとして照合するため、
  同時更新後の古い構成を上書きしない。
  別 Interview session でも同名 identity を継承する。古い head に紐づく
  session は新しい Understanding revision を確定するまで Alignment build を
  409 で拒否する。確定APIはログインユーザー専用で、Principal の user id/名前を
  保存し、graph・session stage・監査messageを単一transactionで確定する。
  Dashboard の再確定dialogでは rename binding と採用する多対多relationを
  人間が確認・編集できる。
  Alignment item は、生成時に提示された現行の確定グラフに存在する entity /
  relation id だけを依存先として受理し、`accept_current` の監査範囲と一緒に
  sidecar tableへ保存し、Review Queueにも名称付きで表示する。次回 build は
  `base_content_hash` が同じ候補について確定
  グラフ間の依存 digest を決定的に比較し、変更関係を参照する項目だけを
  `must_review` / `reason_code='core_capability_changed'` へ戻す。共有機能の
  非変更側relation、明示 binding 済みの純粋rename、無関係な追加は
  `unchanged` carry-overを維持する。この有限分類は
  `alignment-review-policy-v2` の `core-capability-changed` ruleとして監査する。
  旧DBの既存 `content_hash` は推測なしで `base_content_hash` にcopyする一方、
  名前からidentity/依存関係は推測backfillせず、最初の人間確定後から正準履歴を
  開始する。
- **no_review_required ポリシーの外部化(§7.3 → #313)**: 完了。
  `app/policies/alignment_review.yaml` に、有限条件だけを受け付ける
  first-match ポリシーとして切り出した。読み込み時に schema version、全
  reason template、許可済み有限値、重複キー、全入力組合せの終端到達を検証し、
  不正・欠損時は既定値へフォールバックせず fail-closed で起動を拒否する。
  各 `alignment_item` には policy version と YAML 本文の SHA-256 を保存し、
  policy 変更は content hash を変えて `unchanged` carry-over を安全側に無効化する。
- **提案 §7 のフィールド名・status 集合との差異**: 既存実装のフィールド名
  (`non_goals`、`status` 等)を維持し、#295 記載の名称
  (`out_of_scope`、`confirmation_state` 等)への改名は行わない
  (スキーマ契約の互換性優先)。同様に Inquiry status は現行の 5 値
  (`open` / `resolved` / `unresolved` / `cancelled` / `held`)を維持する。
  #295 §7.5 の `investigating` / `answered` / `insufficient_evidence` は
  メッセージ内容と固定テンプレートで同等の区別を実現しており機能差がない。
  `superseded` のみ機能追加を伴うため #308 で扱う。

### Alignment / Inquiry UX 評価指標パイプライン(Issue #309)

UX 改善の効果を、LLM の解釈や外部分析基盤を使わず、選択中の
System に属する永続データだけから監査できるようにした。

- **API 契約**:
  - `GET /interview/metrics` は固定 schema でユーザー負担・精度・UX 品質の
    指標を返す(#309 時点は `interview-metrics-v1`、#341 で要確認判定を
    追加した現行は `interview-metrics-v2`)。
    各指標は `description` / `formula` / `sources`、分子・分母、
    `measured|unmeasured` を持つ。分母 0、因果 lineage 不足、未収集は
    `value: null` と固定理由を返し、計測済みの 0 と区別する。
  - `POST /interview/metric-events` は
    `interview-metric-event-v1` の有限 event/target 組だけを受け付ける。
    対象 session・QA・Alignment item・Inquiry message の System 所有権を
    検証する。`event_key` は転送再送キーとして扱い、さらに
    system/session/event/target の意味的同一性で別キーの重複も冪等化する。
    展開・完了・離脱・再確認は対応する提示/開始イベントが先に存在しなければ
    409 で拒否する。自由文や画面内容は保存せず、外部サービスにも送信しない。
- **additive persistence**:
  - `interview_metric_event` は根拠詳細の提示・展開、質問提示など、
    domain table から復元できない UI 事実だけを append-only で保持する。
  - `interview_qa.answer_unknown` は回答操作時の「わからない」を nullable
    で保持する。既存 `answered` / `unconfirmed` は決定的に backfill するが、
    旧 `revised` 行は元状態を復元できないため NULL のまま分母から除外する。
- **決定的に算出する指標**:
  明示記録 cohort の「わからない」率、確認済み Intent の後日修正率、
  non-mock・non-superseded な Runtime contradiction 率、承認後却下率、
  同一 session 内の同一質問文再出現率、Inquiry 解消率、解消後に元項目へ
  明示回答した割合を既存行から算出する。根拠展開率・途中離脱率・変更なし
  項目再確認率・実装質問転嫁率は、対応する有限イベントが実際に記録された
  cohort に限って算出する。
- **推定しない指標**:
  回答 ID と Understanding Revision の対応がない
  「理解更新 1 回あたり回答数」、全 UI 操作を覆えない
  「Inquiry 1 件あたり追加操作数」、意味的な誤回答確定率、
  field-level lineage がない Revision 再修正率、未実装の rollback、
  却下と rollback の合成率は常に `unmeasured` とする。
- **仕様判断を残す収集点**:
  `review_started/completed/abandoned` と `unchanged_item_reconfirmed` は有限
  schema と fail-closed な前提関係まで定義したが、Dashboard の本番 writer は
  未接続。画面遷移・tab close・一定時間無操作のどれを「途中離脱」とするか、
  対応不要行に独立した「再確認」操作を追加するかは UX の意味を変えるため
  自動推定しない。決定するまでは該当 cohort を `unmeasured` のまま扱う。
- **Dashboard**:
  Interview 画面に System 集計パネルを追加し、guardrail
  (疑問解消率、誤回答確定率、実装質問転嫁率)を他の指標から分離する。
  未計測は理由付きで表示し、計測済み 0 と同じ表示にしない。
- **安全性**:
  指標は表示と監査にだけ使い、分類ルール、自動承認、publish、policy
  変更を起動しない。全 query・event target 検証に `system_id` を含め、
  System 間の分離を回帰テストで固定する。

### 評価指標の段階的開示と要確認判定(Issue #341)

評価指標は、インタビュー中に常に見る一次情報ではなく、運用が健全か・意味の
あるデータを取れているかを定期的に確認するための二次情報である。#309 では
ページ見出し直後に全カードが常時展開されていたため、セッション開始・回答・
共同理解の更新より監視情報の視覚的優先度が高くなっていた。これを、状態を
持つ入口の背後へ畳んだ。

- **`guardrail` と要確認判定の分離**: `InterviewMetricOut.guardrail` は
  「監視対象として指定されているか」だけを表し、実際に異常かどうかは
  新しい `attention` オブジェクトが持つ。両者を1つの boolean に混ぜない。
  `attention.state` は有限6値 — `attention`(閾値超過)/ `ok` /
  `insufficient_data`(母数不足・観測なし)/ `not_measurable`(元の事実が
  記録されておらず算出手段が無い)/ `criterion_unset`(監視対象だが閾値
  未設定)/ `observation_only`(通知対象ではない定期観測)。未計測が
  `ok` や `attention` になることはなく、0 で補うこともない。
- **判定条件の外部化**: `app/policies/interview_metric_attention.yaml` に、
  指標ごとの `watch` / `direction`(high_is_bad|low_is_bad)/ `threshold` /
  `min_sample` / `window` / `trigger` / `clear` を持つ成果物として切り出した
  (#313 と同じ fail-closed 方式、共通の strict loader は
  `app/policy_loader.py`)。読み込み時に schema version、全 metric key の
  終端カバレッジ、有限値、重複キー、`watch: false` 側の余分なフィールドを
  検証し、不正・欠損時は既定値へフォールバックせず起動を拒否する。
  `policy_version` と YAML 本文の SHA-256 を応答に含める。
  `watch: true` にできるのは `guardrail` 指定のある指標だけで、
  違反は `apply_attention` が例外にする。
- **v1 語彙の意図的な制限**: `window` は `all_time`、`trigger` は
  `single_breach`、`clear` は `value_within_threshold` のみ。継続時点灯・
  期間限定集計・手動の「確認済み」は評価履歴の永続化かスケジュール実行が
  前提であり、GET 呼び出し(=画面表示回数)を観測回数として数えるのは
  無意味なため、黙って無視するのではなく語彙から除外して拒否する。
- **閾値の数値は未設定**: 具体的な閾値は各指標の意味と実データを見て別途
  決めるため(#341 対象外)、出荷時の全 `threshold` は `null`。この状態の
  監視対象は `criterion_unset` となり入口を点灯させない。母数チェックは
  閾値と独立に効くため、「データ不足」の判定は最初から機能する。
- **通知対象と定期観測の分離**: 出荷時の `watch: true` は #309/#334 で
  guardrail 指定済みの6指標のみ。「調査だけで答えに到達した割合」や
  「わからない選択率」のように単純な高低で良否を決められない指標は
  `watch: false` の定期観測とし、入口を点灯させない。
- **Dashboard**: カード群は既定で閉じ、見出し付近にラベル付きの常設導線を
  置く。導線は 正常 / 要確認 N件 / データ不足 / 取得失敗 を色だけでなく
  テキストで示し、「値が悪い」と「まだ判断できない」に同じ警告表現を
  使わない。取得失敗はサーバーが自分の失敗を報告できないためクライアント
  側で導出し、インタビュー操作は従来どおり継続できる。展開時は
  要確認事項 → データの評価可能性 → 全指標 の順で段階表示する。導線は
  ネイティブ `<button>` + `aria-expanded` / `aria-controls`、本体は
  ラベル付き `region`。
- **契約**: 応答 schema は `interview-metrics-v2`(`attention` の追加と
  System 単位の集計 summary)。

### PR #296 レビュー対応(#295 実装の修正)

初回実装へのレビュー指摘5件とUX評価に対する修正。

1. **content hash の対象拡張**: `compute_content_hash` に
   `intent_summary` / `gap_summary` / `proposed_interpretation` と、
   evidence 参照範囲の実テキスト digest(`source_digest`: pin 済み
   commit から `read_file_at_commit` で読んだ start〜end 行の sha256、
   ビルド単位キャッシュ)を追加。同じ行範囲のコード変更や制約・scope
   の変化が unchanged と誤判定される穴を塞いだ。evidence が読めない
   項目は hash を None とし carry-over 対象外(安全側)。旧形式 hash
   とは一致しなくなるが再確認へ戻る方向なので移行処理は不要。
2. **carry-over の多世代持続**: carry 候補に
   `review_category='unchanged'` の行を追加し、`carried_over_from` は
   チェーンを辿った元の人間回答行(answered/corrected)の id を伝播。
   3世代目以降も引き継ぎが持続し、監査参照は常に実際の人間回答を指す。
3. **superseded 履歴の分離**: `GET /alignment` の `items_by_category` /
   `counts` は superseded=0 の現行行のみを対象にし、履歴行は additive な
   `superseded_items` で返す。UI は「履歴 N件」の折りたたみで監査閲覧
   可能にし、件数サマリ・サンプル確認への履歴混入を解消。
4. **調査の単件スコープ化と主導線接続**: `route-and-investigate` に
   optional な `qa_ids` body を追加(省略時は従来の全対象バッチ)。
   フロントは `useQaAutoInvestigate` 共通コントローラに調査呼び出しを
   一本化し、focused question の「わからない」も自動調査へ接続。
   1操作で複数件の LLM 調査が走らない。
5. **回答バッチ API**: `POST .../alignment/answers-batch` を追加。
   単体 `/answer` と同じ書き込みロジックを項目単位で再利用
   (`decision_method: manual` は項目単位のまま)、`request_refresh` は
   バッチ全体で一度だけ。部分失敗は項目ごとの成否で応答し、UI は失敗
   項目を保留に残す。
6. **画面再構成(UX評価対応)**: Interview 画面の中央主領域を
   「Alignment Review / 会話」の2タブにし、Alignment build 済み
   セッションは Alignment Review(Intent/現状/gap サマリ +
   ReviewQueuePanel)を既定表示。サイドバーの Review Queue 二重表示を
   撤去し、`qa.investigation` の結論表示を focused question と同じ
   ビューでも表示する(`QaInvestigationBlock` 共有)。

### PR #296 2回目レビュー対応

初回レビュー対応後の再レビューで残った P1×2・P2×3 への修正。

1. **carry-over が Intent/上位概念変更を検知(指摘1, P1)**:
   `compute_content_hash` に `intent_item_id`(リンク先 Intent の生 FK)と
   `linked_intent_digest`(リンク先 `interview_intent_item` の
   field/value_text/status の sha256)を追加。`/correct` は新 id を発行、
   `/confirm`・`/decline` は同 id で status が変わるため、どちらも
   ハッシュが変化し「LLM が同じ要約を返しても依存 Intent が変わった」
   ケースを項目単位で検知する。加えて goal 専用だった再ビルドガードを
   **確定済み(confirmed/not_applicable)Intent フィールドのいずれかが
   直前ビルド以降に更新された場合**へ一般化(全項目を再分類)。
   当時は Core Capability に per-capability の確定履歴が無かったため対象外
   とした。後続 #312 で、安定entity id・人間確定composition・多対多support
   relation・Alignment依存参照を追加し、ヒューリスティックを使わない限定
   カスケードを実装した。
2. **回答対象の検証強化(指摘2, P1)**: batch の項目検証に
   `superseded=1` 拒否・回答可能 status(open/held)以外の拒否・
   actionable category(must_review/batch_reviewable)以外の拒否・
   optional な `content_hash` 不一致拒否を追加。単体 answer/correct/hold
   にも `_reject_if_superseded`(409, `alignment_item_superseded`)を追加。
   別タブや自動更新で履歴化した項目を古い判断で上書きできない。
3. **未対応件数の分離(指摘3, P2)**: `AlignmentListOut` に
   `outstanding_counts` を追加(get_review_queue と同一述語 = superseded=0
   かつ status NOT IN answered/corrected)。`counts`(現行行総数)は互換
   維持。UI の要確認・一括レビュー可の件数は outstanding_counts を優先
   使用し Review Queue のカード数と一致させる。
4. **既定タブの必須操作優先(指摘4, P2)**: 既定タブを
   「会話タブに必須アクションが残る間は会話」優先に変更(alignmentBuilt
   だけで alignment を既定にしない)。会話タブの必須アクションがある間は
   NextActionBanner に「会話タブへ移動」導線を出し、どのタブからも必須
   操作へ到達できるようにした。
5. **gap サマリの内容表示とサンプルの情報量抑制(指摘5, P2)**:
   AlignmentSummaryHeader のギャップ欄に最重要 gap の要約
   (gap_summary || current_claim、未対応のみ、Review Queue と同じ決定的
   順序)を主表示し件数を補助に。確認不要サンプルの根拠・content_hash は
   初期折りたたみ(主張のみ表示)にして確認疲れを抑制。

### PR #296 3回目レビュー対応

2回目レビュー対応をマージ後の再レビューで残った P1×2・P2×2 への修正。

1. **carry-over の起点を accept_current に限定(指摘1, P1)**:
   これまで carry-over 候補は `status IN ('answered','corrected')` の終端行
   全てだったため、`needs_change` / `reject_interpretation` の回答や
   `corrected` 行も、再生成内容が同一なら `unchanged`(対応不要)になり、
   人間が明示した異議・修正が Review Queue から消えていた。これらは
   Understanding へ反映する処理も無いため、実質的に異議が隠れる。carry
   候補を **`accept_current` の `answered` 行のみ**(＋その id を辿る
   多世代 `unchanged` チェーン)に限定し、否定・修正判断はルール表での
   再分類に落として actionable のまま残す。FK 安全性(carried_over_from
   の参照先は DELETE 対象の status='open' 行に決してならない)も維持。
2. **未確認 goal を確定 Intent として表示しない(指摘2, P1)**:
   `AlignmentSummaryHeader` は確認済み goal が無いとき `goalItems[0]` を
   「あなたが実現したいこと」として表示していたため、`status='proposed'`
   の AI 提案が人間の確定 Intent に見えていた。確認済み goal のみを確定
   表示し、未確認の AI 提案しか無い場合は「AI提案・未確認」ラベル付きの
   未確認候補として明示、どちらも無ければ未入力表示にする(#295 の
   「AI推定と人間確認済み情報の区別」)。
3. **proposal_review を会話タブの必須操作として扱う(指摘3, P2)**:
   提案の承認・却下・差分生成の UI は会話タブ内にあるのに、
   `conversationHasRequiredAction` が `proposal_review` を「必須操作なし」
   と判定していたため、build 済みだと Alignment タブが既定表示され、
   NextActionBanner の指示先と表示タブが食い違い「会話タブへ移動」導線も
   出なかった。`proposal_review` も会話タブの必須操作として扱い、既定を
   会話タブにする(Alignment はタブ切替で常時到達可能、切替時はバナー
   導線を表示)。
4. **監査詳細に人間の判断を表示(指摘4, P2)**:
   `AuditDetail` が `user_decision` の action・note・日時を表示せず、
   承認・変更要求・却下・修正・保留のどれだったか判別不能だった。
   `USER_DECISION_LABELS`(5 action の日本語ラベル)を追加し、判断内容と
   メモを監査詳細に表示。carried_over_from / superseded 履歴と合わせて
   #295 の監査可能性を満たす。

### PR #296 4回目レビュー対応

3回目レビュー対応後に残った動作上の指摘6件への修正。

1. **Alignment 判断を Understanding に還流(P1)**:
   `answered` / `corrected` の終端 `alignment_item.user_decision` を
   `routes/interview.py::_load_alignment_feedback` で新旧履歴から読み、
   `generate_understanding_review(..., alignment_feedback=...)` に渡す。
   reviewer prompt では人間の判断を graph 仮説より優先し、最新判断を
   先頭に配置する。`needs_change` / `reject_interpretation` /
   `corrected` が同じ提案の再表示抑止だけでなく、次の Understanding
   自体へ反映される。prompt 変更の監査用に
   `PROMPT_VERSION='understanding-review-v5'` とした。`held` は作業状態で
   内容判断ではないため注入しない。
2. **refresh job をトリガー別に実行(P1)**:
   確定後の `_understanding_update_blocked` は Q&A watermark の gate
   なので `qa_answer` の「新規回答なし」判定にだけ使う。
   `alignment_answer` は gate を迂回して Alignment 判断込みで
   Understanding を再構築する。`intent_update` は desired state の変更、
   `nl_change_set` は選択済み claim edit を revision に永続化済みなので、
   最新 revision を再生成せずそのまま Alignment build へ進む。
   pending job へ別 trigger が合流した場合は
   `alignment_answer > qa_answer > intent/change-set` の必要処理順に
   `trigger_kind` を昇格し、強い入力を dedupe で失わない。
3. **単体判断 API の上書き防止(P1)**:
   `/answer` / `/correct` / `/hold` も batch と同じく actionable category
   だけを受け付け、`answered` / `corrected` への再判断を 409 にする。
   `/hold` の再送は既存行をそのまま返す冪等処理とし、判断時刻を
   書き換えない。`held` から明示的な answer/correct への遷移は維持する。
4. **非同期 refresh 完了後の再取得(P1)**:
   Dashboard の `useRefreshStatus` は pending/updating の polling に加え、
   job が terminal になった時点で session/Q&A/understanding revision・
   diff/alignment/review queue を再取得する。mutation 直後の早すぎる
   invalidation が旧状態を取得しても、完了後に新 revision が必ず反映される。
5. **Alignment 自動既定の到達可能化(P2)**:
   `conversationHasRequiredAction` を `uiState` の truthiness ではなく
   実際の CTA から導出する。proposal review 中は proposed/needs_review
   または approved/edited が残る間だけ会話タブを優先し、全提案却下済み
   かつ Alignment build 済みなら Alignment Review が自動既定になる。
6. **却下済み AI goal の除外(P2)**:
   未確認候補は `status='proposed' AND origin='ai_proposed'` の行だけ。
   `not_applicable` へ却下済みの AI goal は「AI提案・未確認」として
   再表示しない。

## Inquiry の前提追跡と superseded(Issue #308)

#285 の Inquiry は「どの Snapshot / Understanding Revision / Review item
の内容を前提に回答されたか」を保持していなかったため、前提が変わった後も
`resolved` のまま現行の判断根拠として残り続けた。#308 はこれを、作成時に
固定する**前提 bundle**、Review item の**論点 identity**、再ビルド時の
**決定的な前提評価**、そして Dashboard 表示の4つに分けて実装する
(sub-issue #320 / #321 / #323 / #322)。

`superseded` は「回答が誤り」でも「未解決」でもない。**当時の前提に対する
会話は履歴として残すが、現行の判断には流用しない**という意味だけを持つ。

### 前提 bundle(#320)

`interview_inquiry` に additive 列として追加する。既存行はすべて NULL の
まま移行し、過去の snapshot / revision / hash を推測して backfill しない。

| 列 | 意味 |
| --- | --- |
| `premise_snapshot_id` | 回答生成が固定して使う Snapshot。全 origin で記録 |
| `premise_revision_id` | 元 Review item がビルドされた Understanding Revision |
| `premise_review_subject_id` | 論点 identity(#321)。後継の探索に使う |
| `premise_content_hash` | 元 Review item の `base_content_hash` |
| `premise_capability_digest` | 確定 Capability scope の digest(#312 の entity/relation id + captured digest) |
| `premise_intent_digest` | 紐づく Intent 行の `compute_intent_item_digest` |
| `premise_tracking_version` | 前提追跡契約自体の版(`inquiry-premise-v1`) |
| `premise_captured_at` | 固定した時刻 |

snapshot / revision は監査参照(retention 時は `ON DELETE SET NULL`)、
hash / digest / tracking version は**意味内容の比較に使う監査事実**として
retention 後も残る。

`premise_capability_digest` の入力は `alignment_item_capability_dependency`
の `(entity_id | relation_id, captured_digest)` 対だけで、所属する
`alignment_item_capability_scope.confirmation_id` は**意図的に含めない**。
Alignment は新しい Understanding revision に対して確定 Capability graph を
要求するため、確定し直すたびに `confirmation_id` は必ず変わる。これを
digest に入れると、自分が引用している entity / relation が1つも動いていない
Inquiry まで次のビルドで `capability_scope_changed` として失効してしまい、
「ID が変わっただけでは失効させない」(#323)に反する。#312 の
`_capability_scope_changed` も同じ理由で、`confirmation_id` の相違だけでは
変更と見なさず、その item 自身の entity / relation id が変更集合に入って
いるかで判定している。`captured_digest` は entity / relation の
`semantic_digest` であり、id は確定をまたいで安定なので、この対そのものが
scope の「意味」である。

`premise_tracking_state`(API のみ、保存しない決定的導出値)は有限集合
`not_applicable | untrackable | tracked`。

- `not_applicable`: `origin_kind` が `review_item` 以外。v1 の自動前提追跡
  対象は `review_item` のみ。
- `untrackable`: legacy 行、元 item の `content_hash` が NULL、安定
  anchor が無い、または論点 identity が `ambiguous`。**評価対象にしない**。
- `tracked`: 比較可能な bundle が揃っている。

初回回答も follow-up も `premise_snapshot_id` を使う。session の
`snapshot_id` が会話の途中で進んでも、1つの Inquiry の前提は暗黙に rebase
されない(legacy 行だけ従来どおり session の snapshot にフォールバック)。

### Review item の論点 identity と世代 lineage(#321)

`alignment_item.content_hash` は「完全一致か」しか判定できず、内容が変わっ
た後も同じ論点であることを示せない。Alignment rebuild は未回答の open 行を
削除して新しい物理行を作るため、identity が無いと後継を決定的に選べない。

`review_subject_id` は **構造的 anchor だけ**から作る sha256:

- System / session id(System 分離を identity 自体に含める)
- `intent_field`(#284 の有限な Intent Brief フィールド。1 session 1 field
  につき現在行は1つなので、field 名自体が安定した Intent identity)
- 確定 Capability の entity id / relation id(#312 の rename を跨ぐ安定 id)

claim 本文・エビデンス引用・要約・confidence は**入力にしない**。これらは
reasoning model の言い回しであり、それで論点を結ぶのは #321 が禁じる類似度
一致そのものになる。anchor がまったく無い item は identity を持たず
(`review_subject_id IS NULL`)、明示的に `untrackable` になる。

`subject_state`(有限集合)と `replaces_item_id` を各行に記録する。

| 値 | 条件 |
| --- | --- |
| `new` | この論点の先行世代が無い |
| `unchanged` | 先行世代が一意で `base_content_hash` が完全一致 |
| `changed` | 先行世代が一意で内容が異なる |
| `ambiguous` | 同一 build 内、または先行世代側で同じ anchor が複数(split / merge) |
| `untrackable` | 安定 anchor が無い |

`ambiguous` / `untrackable` では `replaces_item_id` を結ばない。「一意に
対応できない場合は後継を推測しない」ため、`ambiguous` な item から開いた
Inquiry は前提 bundle に subject を記録せず `untrackable` 扱いにする
(記録すると、その行を一意に指していなかった identity から後継を1つ選ぶ
ことになり、まさに禁止された推測になる)。

`removed` はこの集合に含めない。「現行 build に行が無い論点」は行の属性で
はなく前提評価(#323)の結果だからである。

`content_hash` / `base_content_hash` / `carried_over_from` による #295 の
unchanged carry-over とは独立で、既存挙動を変えない。前者は「人間が
accept_current した行と完全一致か」、後者は「どの旧行が同じ論点か」という
別の問いに答える。古い物理行を current/open に戻すことはなく、常に新しい
物理行が現行版である。

### 前提評価と superseded 化(#323)

`run_alignment_build` の**書き込みトランザクション内**、現行 Review item
集合が確定した後に評価する。build が失敗すれば遷移も一緒に rollback する。

対象は `origin_kind='review_item'` かつ `status IN (open, held, resolved)`
かつ bundle が揃っている Inquiry のみ。`cancelled` / `unresolved` /
既に `superseded` の行は再訪しない。

後継は `premise_review_subject_id` と**この build が作った行**
(`created_at = build の completed_at`、`superseded = 0`)を突き合わせて
構造的に決める。snapshot / revision の id が変わっただけでは失効させない
(同じソース本文を新しい snapshot に貼り直しても hash は一致する)。

判定結果は有限集合で、first-match:

| 後継 | 比較 | 結果 | reason code |
| --- | --- | --- | --- |
| 0件 | — | `removed` | `origin_removed` |
| 2件以上 | — | `ambiguous` | `successor_ambiguous` |
| 1件 | hash / capability digest / intent digest がすべて一致 | `unchanged` | (遷移なし) |
| 1件 | intent digest が異なる | `changed` | `linked_intent_changed` |
| 1件 | capability digest が異なる | `changed` | `capability_scope_changed` |
| 1件 | それ以外 | `changed` | `review_item_content_changed` |

reason code は根本原因を優先して選ぶ(content hash は linked intent digest
を含むため、intent の変更は必ず hash も変える)。

`unchanged` 以外は `superseded` へ遷移させ、
`interview_inquiry_transition` に `actor='system'` と有限 reason code を
記録する。`superseded` は終端で、`/message` / `/resolve` / `/resume` /
`/reopen-doubt` はすべて 409 になる(古い前提のまま会話を継続したり、
現行の判断として確定したりできない = fail-closed)。再確認は現行 Review
item から新しい Inquiry を開いて行う。

- `closed_at` は**書き換えない**。`closed_at` は「開発者がこの会話を閉じ
  た時刻」を意味し続け、`resolved` だった Inquiry は解消時刻をそのまま保
  持する。open/held のまま失効した Inquiry は `closed_at` を NULL のまま
  にし、解消したように見える時刻を後から与えない。失効時刻は
  `superseded_at` に別途記録する。
- 後継が一意(`changed`)のときだけ、その後継 item に
  `manual_recheck_required = 1` を立てて Review Queue に戻し、旧物理行を
  `superseded = 1` の履歴にする。`user_decision` には一切触れない
  (Principle 2 — 表示や遷移が回答・承認になってはいけない)。
- 元 item が `status='inquiry'` でロックされたままにならないよう、他に
  active な Inquiry が無ければ `open` に戻す(`answered` にはしない。
  #287 の release 規則と同じ)。
- `removed` / `ambiguous` では旧物理行を `superseded = 1` に**しない**。
  一意な後継が無い以上、その論点を履歴化してよいかは決定的に言えず、
  行を隠すと未回答の確認項目が黙って消える。ロックだけ外して `open` の
  まま Review Queue に残し、判断は開発者に委ねる(後継の推測をしないのと
  同じ理由で、消滅の推測もしない)。
- `superseded` は終端なので、同じ rebuild を何度実行しても2つ目の遷移行や
  重複した recheck 対象は作られない(冪等)。

### Dashboard(#322)

`superseded` は active な Inquiry として数えず、resume 導線も出さない
(`activeInquiryByOrigin` は `open` / `held` だけを許可する allow-list)。
履歴としては読める。表示はすべてサーバーのフィールドからの決定的な写像で、
クライアント側で後継を推測しない。

- 失効バッジ「前提が変わったため再確認が必要」+ 「未解決・取り消しとは異
  なる」ことを明示する固定説明文。
- 理由ごとの固定文言4種: `changed`(内容が変わった) / `removed`(論点が
  無くなった) / `ambiguous`(後継を一意に特定できない) /
  `untrackable`(旧データまたは hash 不明で自動比較できない)。
- 監査詳細に作成時の snapshot / revision、失効理由、tracking version、
  解消日時と失効日時の両方。
- `premise_successor_item_id` があるときだけ後継 Review item カードへの
  導線を出し、無いときは固定文言だけを出す。新しい Inquiry の開始と元
  Review item への明示回答は別操作のまま。
- `untrackable` は失効ではないので、失効バッジは付けず固定文言だけを出す。

### 実装しない(#308 の non-goals)

- Inquiry 回答の自動再生成(検知と再確認対象化まで)。
- `answered` / `investigating` / `insufficient_evidence` への status 追加。
  現行実装はメッセージ内容と固定テンプレートでこれらを表現しており、命名
  だけの変更は行わない。
- qa / intent origin の自動前提追跡(snapshot の固定だけは全 origin で行う)。
- ambiguous な後継の自動選択、Review item の自動回答・自動承認。

## Probe Cell Fabric(Issue #297)

自律改善エージェント組織(Cell Fabric)の思想を取り込み、承認済みの各
Probe Point / Component に論理的な Probe Cell を割り当て、Feature・UX・
API/Flow 単位のオーケストレーターが状態を集約し、Root Orchestrator が
ユーザーとのやり取りを一本化する。sub-issue は依存順に
#298 → (#299 ∥ #300) → #301 → (#303 ∥ #302) → #304。

### 全体アーキテクチャ決定

1. **三つの構造の分離**。
   - System Topology Graph: Feature / Capability / API / UX / Symbol /
     Component の多対多関係。既存の understanding graph / feature_code_links
     / capability_hierarchy を正本とし、Cell Fabric は参照のみ行う。
   - Goal / Accountability Tree: `cell_goals` / `cell_tasks`(#300)。各 task
     は単一の owner Cell と単一の `parent_goal` を持つ。Topology を Goal
     Tree として流用しない。
   - Cell Runtime State: mission / tasks / quality / health / improvement /
     role card version。`cell_state` は読み取り時に既存の Trace /
     Evaluation / Shadow Result / Replay / Experiment 参照から決定的に構築
     し、キャッシュ用の別テーブルを持たない。
2. **1 probe = 1 論理 Probe Cell**。Cell ID は原則
   `system_id + component_id`。常駐 LLM プロセスや trace ごとの LLM 呼び出し
   にはしない。モデル起動は明示要求または集約窓/イベント単位のみで、起動は
   `cell_activations` に監査記録される。dormant Cell は LLM を消費しない。
3. **Agent Role Card は既存の API Role Card(#58)と別物**。API Role Card は
   API のシステム内役割の表示モデル。Agent Role Card(#298)は Cell の
   mission / scope / model alias / tool policy / acceptance template /
   rubric ref を宣言する versioned 契約で、同一 schema へ混在させない。
4. **provider/model の実体名は Role Card に直書きしない**。Role Card は
   `model_alias`(例 `worker-default` / `auditor-default`)のみを持ち、
   alias → provider/model の解決は環境変数
   `CELL_MODEL_ALIAS_<UPPER_ALIAS>`(値は `provider:model` 形式、未設定時は
   既存 `LLMConfig.intelligence_from_env()` に委譲)で行う。実モデル名変更が
   card 改版を要求しない。
5. **shadow の「提案」と「実行」の分離**。Cell は `recommended_mode: shadow`
   や候補・Replay Set・Experiment plan を提案できるが、policy 切替・
   candidate 登録/配備・live shadow・patch 適用・採用・publish は既存の
   人間ゲート(#25 / #216 / #242 / #252)を通る。#304 は提案 status と実行
   承認 decision record を別レコードとして持つ。
6. **判断境界(Principle 6)**。状態集約・優先度・ゲート・bottleneck 候補
   抽出・sampling 選択・quality floor はすべて決定的。系統的問題の切り分け
   (#301)、監査 verdict の根拠説明(#302)、改善仮説の生成(#304)は
   reasoning_llm で fail-closed。承認・採用は常に `decision_method: manual`。

### Sub 1: Cell 契約・Role Card・共通状態 schema(Issue #298)

- shared schemas: `shared/schemas/cell_definition.schema.json` /
  `cell_state.schema.json` / `agent_role_card.schema.json`。すべて
  `additionalProperties: false` で unknown field を fail-closed 拒否。
- サーバ契約層は `app/cell_fabric.py`。Pydantic モデルは
  `model_config = ConfigDict(extra="forbid")`。
- worker と orchestrator は別種類にせず、`roster`(子 Cell ID 配列、
  nullable)の有無で表現する共通 Cell contract。
- task 状態は有限集合 `todo | doing | review | done | failed | blocked`。
  遷移規則は明示的な遷移表(`TASK_TRANSITIONS`)で検証し、`done` への遷移は
  acceptance 充足フラグと evidence ref(1件以上)を必須とする。違反は
  validation error。
- Role Card は semver。`role_key` ごとに versioned 行を追加し(上書き禁止)、
  changelog 必須。互換性検証は決定的: major 一致かつ以上のバージョンのみ
  互換、schema_version 不一致・未知 enum・非互換 version は fail-closed。
- テーブル(System-scoped、additive CREATE のみ):
  `agent_role_cards`(role_key, version, status `draft|active|deprecated`,
  mission, scope_json, out_of_scope_json, model_alias, tool_policy_json,
  acceptance_template_json, rubric_ref, changelog, created_at, created_by,
  decision_method)と `cell_definitions`(cell_id, roster_json nullable,
  role_card_id + pinned card version, status `active|dormant|retired`,
  mission override)。
- API: `POST/GET /cell-fabric/role-cards`(+`/{id}`)、
  `POST/GET /cell-fabric/cells`(+`/{cell_id}`)。routes は
  `routes/cell_fabric.py`。
- Goal/Task の永続化・Cell worker の起動・LLM による Role Card 自動生成は
  非スコープ。

### Sub 2: versioned Cell Binding と read-only pilot(Issue #299)

- `cell_bindings`(cell FK, version 連番, snapshot_id, commit_sha, path,
  qualified_symbol, probe_point_id / probe_pattern_id provenance,
  feature/capability/entrypoint refs json, status
  `active|stale|review_required|superseded`)。同一 Cell の source 移動・
  snapshot 更新は新 version 行として保持し、上書きしない。
- binding は承認済み Probe Point(status `approved`)または Probe Pattern
  由来のみ作成可能。未承認は 409/422 で拒否。
- read-only cell state: traces / evaluation_results / shadow_results /
  replay_runs / experiments から heartbeat(最終 trace 時刻)、error rate、
  duration 統計、直近 window 件数を決定的に集約(`GET
  /cell-fabric/cells/{cell_id}/state`)。
- drift 検出は構造的判定のみ: 最新 ready snapshot の `code_symbols` に同一
  path + qualified symbol が存在しなければ `review_required`、存在するが
  binding の snapshot が古ければ `stale`。推測で別 symbol へ再接続しない
  (再接続は #168 の reconcile フローの領分)。
- activation: `POST /cell-fabric/cells/{cell_id}/activations`(明示)または
  集約窓条件。`cell_activations` に trigger 種別・窓・LLM 使用有無・
  intelligence_run 参照を監査記録。trace ごとの LLM 呼び出し経路は存在
  しない。
- Probe SDK は変更しない。host isolation / non-blocking の回帰テストを維持。

### Sub 3: Goal/Task 台帳と protocol(Issue #300)

- テーブル: `cell_goals`(parent_goal_id nullable=root、循環拒否)、
  `cell_tasks`(goal_id 必須、owner_cell_id 必須、acceptance_json 必須、
  context_refs_json、budget_json、deadline/priority、retry_count/limit、
  blocked_by_json、idempotency_key、evidence_json、returned_to_parent)、
  `cell_reports`(kind `digest|escalation` は schema 検証、fact_json /
  interpretation_json / ask_json を別 field、idempotency_key)、
  `cell_escalations`(severity `sev1|sev2|sev3`、status
  `open|acknowledged|resolved`)。
- P1 delegate = task 作成(goal / acceptance / context_refs / budget /
  deadline)。P2 report = digest / escalation の二形式のみで、契約外の
  自由形式 payload は fail-closed 拒否。P3(quality sample event)と
  P4(improvement proposal)は参照契約(ref 形式)のみ定義し、実装は
  #302 / #304。
- evidence は決定的に解決検証する: `trace:<id>` / `evaluation:<id>` /
  `shadow_result:<id>` / `replay_run:<id>` / `experiment:<id>` /
  `snapshot_file:<snapshot_id>:<path>` 形式のみ受け付け、実在しない参照は
  422。
- 同一 idempotency_key の再送は既存行を返し重複しない。

### Sub 4: 領域オーケストレーター(Issue #301)

- orchestrator は roster 付き `cell_definitions` 行。context lens
  (feature/ux/api/flow)ごとの参照は多対多だが task owner は常に一意。
- guardrail: roster は span of control 上限 7(5±2 の上限)、Goal Tree
  深さ上限 3、構成は静的(API 経由の明示更新のみ)。違反は validation
  error。
- digest(`GET /cell-fabric/orchestrators/{cell_id}/digest`)は決定的
  集約: 子 Cell の task 進捗、health、queue length、cycle time、WIP age、
  blocked_by graph、critical path から bottleneck 候補を有限規則で列挙し、
  各候補に根拠 fact と対処 task 参照を付ける。
- 個別 Cell 問題か系統的/上流問題かの切り分けは reasoning_llm
  (`run_type: cell_triage`、fail-closed)。失敗時も fact digest は返り、
  推測で ask を確定しない。結果は fact と分離して
  `cell_triage_results` に永続化。

### Sub 5: Root Orchestrator と統合ダイジェスト(Issue #303)

- `GET /cell-fabric/root-digest`: canonical deterministic facts は
  `GET /system-state`(#235)の実装(`build_system_state`)を正本として
  再利用し、Cell Fabric 由来の進捗・品質・escalation・Ask を統合。
- severity routing: sev1 は即時 surface、sev2 は判断要求として集約、
  sev3 は詳細格納。同一 root cause(決定的 dedupe key = 対象 Cell +
  escalation 種別 + 根拠 evidence 集合のハッシュ)は一つに集約。
- progressive disclosure: conclusion → key_points → evidence/uncertainty →
  audit detail の 4 段を応答構造として持ち、UI はユーザー操作で展開。
- Ask(`cell_asks`): 回答 `accept | hold | reject` は
  `decision_method: manual` で記録し、元 Goal/Task へ還流(task の
  blocked 解除/failed 化)。提案 accept と実行承認は別レコード・別状態
  (実行承認は #304 のゲートおよび既存 #25/#216 ゲートの領分)。
- Dashboard: `/cell-fabric` ページ(日本語 UI)。digest 表示・drill-down
  (Feature → Cell → Trace/evidence)・Ask 回答。stale snapshot /
  provenance / decision_method を明示。

### Sub 6: 品質サンプリング・独立監査・quality floor(Issue #302)

- SDK の lineage/projection `sample_rate` とは独立した quality sampling
  契約。`cell_quality_configs`(sample_rate 既定 0.05〜0.10、strata_json
  = task type / risk / rare case、audit_rate、quality_floor、budget)。
- サンプル選択は決定的(安定ハッシュによる層化選択)。希少 stratum は
  最低 1 件保証。選択結果は `cell_quality_samples`。
- worker 実行モデルと auditor モデルは model alias で分離
  (`auditor-default`)。監査は `cell_quality_audits`(verdict
  `pass|fail` は golden set / Evaluation Criteria の決定的判定を優先し、
  根拠説明のみ reasoning_llm・fail-closed。blind re-audit フラグ)。
- pass/fail 集計・逐語例・fact・hypothesis は別 field で混在させない。
- quality floor 割れ: 対象 Cell のみ `cell_intake_states` を
  `suspended` にし sev1 escalation を発行。host app や無関係 Cell は
  止めない。回復は floor 回復の決定的判定+明示操作。
- sampling / audit に System-scoped 上限を持つ。`daily_audit_budget` の単位は
  **UTC日ごとの監査呼び出し回数**で、許可された `run_audit` 1回につき
  verdict や任意の説明 LLM 呼び出し有無にかかわらず1消費する。token 数や
  通貨金額ではない。説明 LLM を実際に呼ぶ場合は、これとは別に
  `CONTROL_LLM_DAILY_EXECUTION_LIMIT` の実行回数上限も適用される。
- 現行プロダクトは billing/原価配賦をスコープに持たず、provider 横断で
  token usage と時点別価格表を正規化する台帳もない。そのため Issue #315
  時点では金額ベースの追跡を追加せず、別 Issue も起票しない。請求額表示、
  chargeback、または通貨建て hard limit がプロダクト要件になった時点で、
  provider usage の永続化・価格 version/provenance・currency・rounding・
  retry/cache の課金規則を独立 Issue として設計する。

### Sub 7: 改善仮説・カナリア・承認ゲート(Issue #304)

- `cell_improvements`: lifecycle
  `observed → proposed → canary_ready → canary_running → adopted |
  rejected | blocked`(有限遷移表)。hypothesis / 対象(role_card |
  candidate_patch)/ 期待効果 / risk / rollback_plan / canary evidence
  refs(golden set・Replay run・offline shadow・Experiment の参照のみ)/
  parent 承認 / 人間承認 / suspension。`cell_improvement_events` は
  append-only 履歴で rejected も削除しない。
- 仮説文面の生成は reasoning_llm(fail-closed、intelligence_runs 監査)。
  遷移はすべて決定的ゲート+manual 承認。
- Role Card 変更はカナリア evidence + 直属親承認なしに `adopted` に
  ならない。shared protocol/schema 変更は Root 承認 + 互換性テスト、
  harness 変更は人間レビュー必須。rubric は親所有で自己変更不可。
- shadow 提案(proposal record)と live shadow 実行承認(decision
  record)は別テーブル列・別 status。live shadow 承認だけでは candidate
  配備や policy 変更を実行しない。candidate 採用・patch 適用・publish は
  既存人間ゲート(#25 / #216 / #242 / #252)へ handoff し、迂回経路を
  作らない。
- 連続失敗(閾値)・quality 悪化・契約違反で改善権を `suspended` にし、
  rollback(Role Card は前 version へ pin)できる。

### 非スコープ(Epic 全体)

- `@probe` 内または trace 受信ごとの LLM 呼び出し
- 既存 Component の一括 Cell 化、動的な無制限 Cell 生成・無限の入れ子
- reasoning model だけによる承認・採用・publish
- live shadow・source 変更・外部副作用の無承認実行
- #282 Interview / #242 Replay 基盤の別系統再実装

### 2〜5 Cell read-only pilot の end-to-end 実証(Issue #314)

Issue #297 と sub-issue #298-#304 の実装レビュー後に残った運用上の実証を、
`tests/test_cell_read_only_pilot.py` の 3-Cell 統合 fixture で完了した。

- 1 つの承認済み Probe Plan / Feature に属する 3 件の Probe Point から、
  versioned Cell Binding を API 経由で作成する。
- 3 Cell すべてを `GET /cell-fabric/cells/{id}/state` で読み出し、領域
  orchestrator digest と Root digest が同じ roster と
  `feature_refs` / `capability_refs` / `entrypoint_refs` を集約する。
- binding の参照先が実在する Feature / API entrypoint に解決し、
  component trace API と Root digest の task evidence
  (`trace:<id>`)まで drill-down できることを確認する。
- read-only pilot 実行前後で、対象 repository の全ファイル hash と
  component policy を含む保護対象 DB 行が完全一致することを確認する。
  `create_llm_client` は呼ばれた時点でテストを失敗させ、
  `intelligence_runs` に新規行が増えないことも同じ DB snapshot で保証する。
- 同一 Cell ID / roster を持つ別 System を positive-collision fixture
  とし、binding / trace / topology / task evidence が一切漏れないことを
  end-to-end で確認する。

---

## 共同理解セッション(Epic #328)

Epic #328 は、開発者が「わからない」と答えた地点を**終端**ではなく**共同で状況
理解を作る工程の開始点**として扱うための Epic である。既存の Interview(#282
系)、Inquiry(#285/#286)、Alignment Review(#287)、前提追跡(#308)、評価
基盤(#309)を置き換えず、その上に「調査 → 通訳 → 開発者の判断」を分離したまま
往復できる 1 本の循環を足す。

Phase 分割と sub-issue:

| Phase | Issue | 内容 |
| --- | --- | --- |
| A | #329 | 受け渡し契約・対話状態の基盤(決定的、LLM 呼び出し無し) |
| B | #330 | 反復調査ループと探索継続性 |
| C | #331 | 通訳(目的・影響への翻訳)と選択肢提示 |
| D | #332 | システム理解への還流と確定状態の分離 |
| E | #333 | Dashboard 共同理解パネル |
| F | #334 | 共同理解の質を測る評価枠組み |

### Phase A: 受け渡し契約と対話状態(Issue #329)

#### 共有スキーマ

`shared/schemas/joint_understanding.schema.json`(`schema_version:
joint-understanding-v1`)が、1 セッション分の受け渡し契約 —
`session` / `findings` / `actions` — を定義する。契約の要点は「三つの来歴を
1 つの回答に混ぜない」ことにある。

- `origin_role`(**誰が**書いたか): `investigation` / `translation` / `developer`
- `claim_kind`(**何の種類**の主張か): `fact` / `inference` / `hypothesis` /
  `unknown` / `conflict`

この 2 軸は独立している。`unknown` は失敗ではなく第一級の結果であり、
もっともらしさで埋めない。`conflict` は両立しない情報源を記録する。

#### 有限語彙(`app/joint_understanding.py`)

すべて Principle 6 の明示的な有限集合で、未知値は 422(推測補完しない)。

- `ORIGIN_KINDS`: `qa` / `intent` / `review_item` / `inquiry`
- `TRIGGERS`: `unknown_answer` / `explicit_request`
- `SESSION_STATUSES`: `open` / `held` / `closed`
- `SESSION_TRANSITIONS`: `open → {held, closed}`、`held → {open}`、
  `closed` は終端(表に無い遷移は 409)
- `SESSION_OUTCOMES`: `understood` / `doubt_resolved` /
  `hypothesis_adopted` / `decided` / `handed_off` / `abandoned`
  - `PROVISIONAL_OUTCOMES = ("hypothesis_adopted",)` は**暫定**であり、
    API 応答の `outcome_is_provisional` で常に判別できる。事実として再利用しない。
  - `decided` だけが人間の最終的な価値判断。
- `ACTION_KINDS`: `request_investigation` / `explain_reasoning` /
  `compare_options` / `adopt_hypothesis` / `revise_intent` / `hold` /
  `handoff` / `decide`

#### 役割ごとの契約(`validate_finding`)

| 役割 | decision_method | evidence | supports_finding_ids |
| --- | --- | --- | --- |
| `investigation` | `reasoning_llm` / `deterministic` | 可 | 任意 |
| `translation` | `reasoning_llm` | **不可** | **1 件以上必須** |
| `developer` | `manual` のみ | **不可** | 任意 |

- `developer` の Finding は `intelligence_run_id` を持てず `is_mock` にもできない
  — reasoning model が開発者の名前で発言する経路を機構的に塞ぐ。
- `translation` は証拠を作れない。一般化した説明から必ず元の技術的主張と証拠へ
  戻れるように、同一セッション内の Finding id 参照のみを許す(他セッション・
  存在しない id は 422)。
- `investigation` の `hypothesis` は競合説明と反証条件を必須にする(仮説は
  単なる低 confidence claim ではない — `docs/system-understanding-ideal-state.md`
  §3.4)。
- `reasoning_llm` の Finding は必ず `intelligence_run_id` を持つ(Principle 7)。
- Finding は**追記のみ**。更新・削除エンドポイントは存在せず、訂正は
  `supersedes_finding_id` を持つ新しい Finding で表現する。

#### テーブル(additive、System スコープ)

`joint_understanding_session` / `joint_understanding_finding` /
`joint_understanding_action`。`premise_snapshot_id` は作成時に interview session
の snapshot を固定する(#308 の premise bundle と同じ規律。Phase B の追加調査が
新しい snapshot へ暗黙に乗り換えないため)。

#### API(`routes/joint_understanding.py`)

- `POST /interview/sessions/{session_id}/joint-understanding`(201)
- `GET /interview/sessions/{session_id}/joint-understanding`(`status` /
  `origin_kind` フィルタ、未知値は 422)
- `GET /joint-understanding/{ju_id}`(findings + actions + `available_actions`)
- `POST /joint-understanding/{ju_id}/findings`(追記)
- `POST /joint-understanding/{ju_id}/actions`(開発者が選んだ次の行動)
- `POST /joint-understanding/{ju_id}/hold` / `/resume` / `/close`

`available_actions` は現在の status から決定的に導出する(`open` のときだけ
`ACTION_KINDS` 全件、`held` / `closed` では空)。

#### 「わからない」を意図として混入させない境界

Phase A のテーブル群が存在する最大の理由がこれである。

- どのエンドポイントも origin 行(`interview_qa` / `interview_intent_item` /
  `alignment_item` / `interview_inquiry`)に**書き込まない**。読むのは存在確認の
  SELECT だけ。#287 の Inquiry が行う `alignment_item.status='inquiry'` の
  ミラーリングも**行わない**(両フローの統合方法は Phase D / #332 で決める。
  それまで共同理解セッションは既存フローに触れない並走系統として保つ)。
- `question_text` はセッション行にのみ保持し、回答欄へコピーしない。
- `trigger='unknown_answer'` でセッションを作っても developer Finding は
  1 件も作られない(「わからない」はシステムについての主張ではない)。
- `outcome='decided'` で閉じても origin 行は変わらない。項目の確定は
  引き続き項目自身の回答・確認エンドポイントだけが行う。

#### Phase A の非スコープ

reasoning model 呼び出し(Phase B / C)、理解への還流(Phase D)、Dashboard
(Phase E)、評価指標(Phase F)。Phase A の `decision_method` は `manual` か、
呼び出し側が検証済みで渡す値のみで、この層は LLM を呼ばない。

#### テスト

`apps/control-server/tests/test_joint_understanding.py`:
契約の単体テスト(遷移表・暫定 outcome・役割別ルール・未知語彙)、共有スキーマの
検証(translation の evidence 拒否・未知 enum・hypothesis 構造)、API ライフサイクル、
**origin 行不変**(qa / intent / review_item)、追記のみ、他セッション参照の拒否、
有限遷移、System 分離。

### Phase B: 反復調査ループと探索継続性(Issue #330)

`app/investigation_loop.py`。#286 の `investigate()`(1 ラウンド・1 回の
reasoning 呼び出し)はそのまま残し、その読み取り専用機構の上に「まだ分かって
いないことを持ち越しながら続ける」ループを足す。

#### 横断的な候補取得(決定的、候補の絞り込みのみ)

`_index_candidates` が pin された snapshot に対して次を横断する。

- `code_symbols` の `qualified_name` / `docstring`(重み 2)
- `code_entrypoints` の `label` / `route_path` / `handler_qualified_name`(重み 2)
- `snapshot_files.content` の内容一致(重み 1)

これに #286 の**パス名**一致(`_path_name_candidates`)を後続で足し、既読パスを
除外して 1 ラウンド分の候補にする。並び順は (一致数 降順, path 昇順) で固定。
「ファイル名や冒頭部分だけに依存しない探索」(Epic #328)を、最終判断を
reasoning に残したまま満たす(Principle 6)。

#### ラウンド間・再試行間の継続性

各ラウンドの構造化出力が `search_leads` / `open_hypotheses` /
`missing_evidence` を返し、次ラウンドの候補取得とプロンプトへ入る。
`read_paths` は既読集合として累積し、`no_new_evidence` 判定に使う。
`POST /joint-understanding/{ju_id}/investigate` を再度呼ぶと
`_restore_carry_over` が永続化済みラウンドから復元するため、**再試行は質問から
やり直さず、すでに得た手がかりから再開する**(失敗ラウンドは leads を返さないので、
その前のラウンドの手がかりを消さない)。

#### 有限な停止理由(`STOP_REASONS`)

`answered` / `budget_exhausted` / `no_new_evidence` / `unresolved` / `failed`。
新しく読めるファイルが 1 件も無いラウンドは決定的に `no_new_evidence` で止める
(モデルの自己申告で継続しない)。予算 `InvestigationLoopBudget` は
`max_rounds` / `max_llm_calls` / `max_files` / `max_snippet_chars` /
`max_files_per_round` / `timeout_seconds` をハードクランプする。

#### Phase A 契約の適用点

ラウンド出力は Phase A の `claim_kind` そのままで返り、次を満たさない Finding は
**破棄**する(`pruned_findings` に計上、書き換えない)。

- `claim_kind` が有限集合外
- `hypothesis` なのに競合説明・反証条件が無い
- 引用が全て snapshot 検証に失敗、または引用が無いのに `unknown` 以外
  (「調べたが分からなかった」だけは証拠なしで残す)

`completed` なのに有効な Finding が 0 件のラウンドは `unresolved` へ決定的に降格
(#286 と同じ規律)。

#### 永続化と監査

- `joint_understanding_investigation_round`(additive): ラウンドごとの
  status / stop_reason / conclusion / carry-over / 未読候補 / 予算使用量 /
  `intelligence_run_id` / エラー。
- `intelligence_runs` に 1 ラウンド 1 行(`run_type='joint_investigation'`、
  新規に `IntelligenceRunType` と `shared/schemas/project_intelligence.schema.json`
  へ追加)。読んだ抜粋は `intelligence_run_evidence` に全件記録する。
- Finding は `origin_role='investigation'` / `decision_method='reasoning_llm'` /
  そのラウンドの `intelligence_run_id` 付きで追記される。

#### fail-closed

mock / 非 reasoning モデル / git 失敗 / API 失敗 / 構造化出力検証失敗はすべて
`stop_reason='failed'`。ラウンド 1 の失敗は Finding を 1 件も作らず、ラウンド N の
失敗は**それまでに検証済みの Finding を残したまま**停止する。Finding が 0 件のまま
`failed` になった呼び出しは 502(監査行は永続化済み)。

#### DB ロック

`run_investigation_loop` は index / runtime facts 用の接続を自分で開き、各ラウンドの
LLM 呼び出し**前に**閉じる。ルートは read → reason → persist の 3 フェーズ。

#### テスト

`apps/control-server/tests/test_joint_understanding_investigation.py`:
複数ラウンドと持ち越し、carry-over 指定での再開、`no_new_evidence` /
`budget_exhausted` / `failed` の停止、mock・非 reasoning・LLM 例外・不正 JSON の
fail-closed、ラウンド N 失敗時の Finding 保持、hypothesis 構造・引用剪定・
根拠なし主張の破棄、pin された commit のみ読むこと(作業ツリー汚染後も不変)、
API での監査行・Finding 永続化、再試行での leads 復元、open 以外での 409、System 分離。

### Phase C: 通訳(目的・影響への翻訳)と選択肢提示(Issue #331)

`app/understanding_translator.py` + `POST /joint-understanding/{ju_id}/translate`。

#### 入力と絶対条件

入力は**同一セッションに記録済みの investigation Finding だけ**(snapshot の
抜粋は渡さない)。したがって通訳は新しい技術的事実を作れない。出力の各文は
`supports_finding_ids` を必須とし、渡していない id を 1 つでも参照したら
**呼び出し全体を fail-closed**(部分保存しない)。

#### 説明の層(`STATEMENT_LAYERS`)

`purpose` / `impact` / `gap` / `consistency` / `decision`。第 1 層(purpose /
impact)の主語は目的・利用者・観測可能な振る舞いであり、内部変数・関数・API・
列名ではない。内部名称は隠蔽せず、Finding → evidence の層で必ず開示される
(Epic #328 の説明の原則)。

#### 通訳が主張できる種類(`TRANSLATION_CLAIM_KINDS`)

`inference` / `unknown` / `conflict` のみ。`fact` と `hypothesis` は不可 —
新しい事実の断定も、競合説明・反証条件を伴う新仮説の提示も investigation の
責務であり、通訳が肩代わりしない。`hypothesis` / `unknown` の Finding を
確定事項へ格上げすることも禁止(プロンプト規則 + 検証)。

#### 追跡性の永続化

翻訳文は `origin_role='translation'` の Finding として追記される(evidence を
持てず `supports_finding_ids` 必須 = Phase A 契約)。要約・選択肢・未解決点・
判断質問は `joint_understanding_translation` 行に入り、`statements_json` の
各要素が対応する translation Finding の `finding_id` を保持する。
これにより「一般化した説明 → Finding → evidence(path:行)」が常に辿れる。

#### 選択肢メニューは決定的

`build_action_menu()` は `ACTION_KINDS` の順に、`interview_language` の固定
カタログ(`joint_action_<kind>_label` / `_effect`)から「何が変わるか」を組み立てる。
モデル出力ではないので、同じ状態なら常に同じメニューになる。
`adopt_hypothesis` の説明文は「暫定であり事実にならない」ことを明示する。

#### 質問ゲート(`ask_developer`)

「人間にしか決められないと分類した直後、判断材料を示さずユーザーへ返す流れ」
(Epic #328 が置き換える対象)を防ぐ決定的ゲート。

- `decision_question` が無ければ聞かない
- `decision` 層の文があれば聞く
- 判断材料が無く、まだ調べられること(`open_unknowns`)が残っているなら**聞かない**
  — それは追加調査の理由であって質問の理由ではない

`ask_developer=false` のとき `decision_question` は永続化されない。

#### 監査と fail-closed

`intelligence_runs`(`run_type='joint_translation'`)を成功・失敗とも 1 行記録。
mock / 非 reasoning モデル / Finding 0 件 / LLM 例外 / 不正 JSON / 未知 id 参照 /
未知 layer / 禁止 claim_kind はすべて 502 で、翻訳行も translation Finding も作らない。

#### テスト

`apps/control-server/tests/test_joint_understanding_translation.py`:
参照検証(未知 id・参照なし・選択肢の未知 id)、禁止 claim_kind、未知 layer、
fail-closed 一式、メニューの決定性と両言語、質問ゲートの 4 分岐、
API での translation Finding 永続化と `finding_id` 対応、失敗時に監査行のみ、
origin 行不変、open 以外 409、System 分離。

### Phase D: システム理解への還流と確定状態の分離(Issue #332)

#### 還流(reflux)— 回答へ転記しなくても理解へ反映する

`POST /joint-understanding/{ju_id}/reflux`。Epic #328 が置き換える
「調査結果を回答欄へ転記しないと理解へ反映されない流れ」の代替経路。

- 還流対象は `origin_role='investigation'` かつ `claim_kind='fact'` のみ
  (`REFLUXABLE_CLAIM_KINDS`)。inference / hypothesis / unknown / conflict は
  会話内に留まる — confidence だけで仮説を事実へ昇格させない。
- 訂正済み(後続 Finding に `supersedes_finding_id` で置き換えられた)Finding は
  還流しない。
- `decision_method` は常に `reasoning_llm`。**`manual` にはならない**
  (誰も決定していない = 人間の回答ではない)。
- 反映先(`REFLUX_TARGET_KINDS`、既存構造のみ):
  - `qa_investigation`: `interview_qa.investigation_json` /
    `investigation_run_id`。#286 の route-and-investigate が書くのと同じ
    **回答ではないスロット**。`answer_text` / `status` / `answered_by` は書かない。
  - `session_ledger`: intent / review_item / inquiry 由来。これらの行は
    Alignment / Understanding の再ビルドが所有しており、事実を書き込んでも
    次のビルドで黙って消えるため、台帳行そのものを反映先とする
    (第三の理解モデルを新設しない、という制約の下での明示的な設計判断)。
- 同じ Finding は二重に還流しない(`UNIQUE (joint_understanding_id, finding_id)`、
  再呼び出しは `already_refluxed` に計上)。
- policy / 実行系(`components` の mode、experiments、probe_plans、publish_jobs)には
  一切触れない。理解の更新と実行権限は別(Principle 7)。

#### 前提整合性(premise state)

`_premise_state()` は決定的・構造的判定:セッション作成時に固定した
`premise_snapshot_id` と、現在の interview session の `snapshot_id` が異なれば
`stale`。`stale` のとき

- 還流は 409(古い調査結果を現在の理解として貼らない)
- `hypothesis_adopted` / `decided` での close は 409
- 何も断定しない outcome(`understood` / `doubt_resolved` / `handed_off` /
  `abandoned`)は許可し、`outcome_premise_state='stale'` として記録する

#### 確定状態の分離(`validate_outcome_basis`)

`OUTCOMES_REQUIRING_BASIS = ("hypothesis_adopted", "decided")` は
`outcome_finding_ids`(同一セッションの Finding)を必須にする。根拠を示せない
採用・決定は監査できないため 422。`hypothesis_adopted` は
`outcome_is_provisional=true` を返し続け、事実として還流されることもない
(仮説は還流対象外)。

追加列(additive、既存 DB は `_add_column_if_missing` / ALTER で追従):
`joint_understanding_session.outcome_finding_ids` / `outcome_premise_state`。
新規テーブルは `joint_understanding_reflux` のみ。

#### 既存フローとの関係

Phase D は Inquiry(#285)/ Review Queue(#287)を置き換えず、origin 行への
書き込みも増やさない。共同理解セッションが書くのは、既存の「回答ではない」
調査スロットと自身の台帳だけである。

#### テスト

`apps/control-server/tests/test_joint_understanding_reflux.py`:
還流可否の有限規則、反映先の決定性、outcome basis 規則(不足・他セッション・
stale)、QA 調査スロットへの反映と回答欄不変、fact 以外の非還流、superseded の
除外、冪等性、非 QA 由来の台帳のみ反映、stale 前提での 409、4 つの終端状態の
区別と根拠記録、暫定採用が還流されないこと、policy / 実行系テーブル不変、System 分離。

### Phase E: Dashboard 共同理解パネル(Issue #333)

`apps/dashboard/src/components/system-understanding/joint-understanding-panel.tsx`。
既存の Inquiry パネル(#295 の 4 段階開示)と同じ規約に合わせ、別系統の表示規則を
作らない。

#### 4 段階表示

1. **目的と影響**: 通訳の `purpose_summary` + `purpose` / `impact` 層の文。
   ここに内部名称(path:行)は出さない。
2. **理由**: `gap` / `consistency` / `decision` 層、未解決点、選択肢
   (「変わること」「代償」)、`ask_developer=true` のときだけ判断質問。
3. **根拠**: Finding 一覧(claim_kind バッジ、競合説明、反証条件、未確認、
   `path:開始-終了`)。**内部名称はここで必ず開示する**(隠蔽しない)。
4. **調査詳細**: ラウンドごとの読了ファイル・未読候補・不足証拠・停止理由・
   調査 run、還流済み件数。

例外の先出し: 前提が `stale`、未解決点あり、`conflict` Finding ありのいずれかで
第 2 層を初期表示する(第 4 層は自動展開しない)。

#### 行動メニューと視覚的区別

`available_actions`(サーバ)をボタン化し、翻訳済みならサーバの `action_menu`
のラベル/効果説明を優先、未翻訳ならローカルの日本語ラベルにフォールバックする
(Issue #266: クライアント側フォールバックも日本語)。
`hypothesis_adopted` は琥珀色 +「(暫定・事実ではありません)」、`decided` は
緑系で表示し、暫定と確定を取り違えられないようにする。閉じたセッションでは
メニューを出さない。

調査中は「関連するコードとテストを確認しています…」の短い状態表示のみ。
API 失敗時もパネルとメニューは残り、回答機会を失わせない。

#### 既存機能との住み分け

パネルの「他の人に引き継ぐ」は共同理解セッションを `handed_off` で閉じる
**記録**であり、実際の担当者割り当ては既存の引き継ぎ機能(#291 の
`question_handoff`)で行う。「意図を修正する」も同様に行動の記録で、
Intent Brief 自体の編集は既存の Intent パネルで行う — 共同理解セッションは
どちらの元データにも書き込まない。

#### 起動導線

`pages/interview.tsx` の Q&A カードに「一緒に確かめる」を追加。既存の #142 /
#295 の「わからない」フロー(自動調査 → 既存導線へ戻す)は変更せず、その後段の
任意手段として `trigger='unknown_answer'` のセッションを開始する。開いても
元の質問には一切回答しない。既に open/held のセッションがあればそれを再表示する。

#### テスト

`apps/dashboard/src/__tests__/joint-understanding-panel.test.tsx`(8 件):
第 1 層に内部名称が出ず第 3 層で開示されること、有限メニューの表示と送信、
暫定採用と確定の視覚的区別、前提 stale の警告と第 2 層先出し、調査失敗後も
対話が残ること、`is_mock` バッジ、未翻訳時の日本語案内。

### Phase F: 共同理解の質を測る評価枠組み(Issue #334)

#309 の指標パイプライン(`app/interview_metrics.py`)に**別カテゴリ**
`joint_understanding` として追加する。効率化指標(確認件数・承認速度)と
同じ数値へまとめない — 「効率化を共同理解の質より先に最適化する流れ」を
置き換えるという Epic #328 の目的上、両者は独立して読めなければならない。

すべて永続化済みの事実に対する決定的な集計で、モデルの自己申告による品質
スコアは導入しない。分母が 0 の指標は 0 ではなく `unmeasured`
(`unmeasured_reason='no_observations'`)を返す(#309 の方針を踏襲)。

| key | guardrail | 意味 |
| --- | --- | --- |
| `joint_understanding_from_unknown_rate` | – | 「わからない」から始まった割合(終端ではなく開始点として使われているか) |
| `joint_understanding_conclusion_rate` | – | 閉じたセッションのうち理解・疑問解消・暫定採用・正式判断へ到達した割合 |
| `joint_understanding_provisional_outcome_rate` | ✓ | 暫定採用で終わった割合(高止まり = 確かめきれずに前へ進めている) |
| `joint_understanding_stale_premise_close_rate` | ✓ | 前提が変わった状態で閉じた割合 |
| `joint_understanding_unknown_finding_rate` | ✓ | 調査 Finding のうち `unknown` の割合(証拠不足を埋めていないか) |
| `joint_understanding_reflux_rate` | – | 確認できた事実のうち、回答へ転記せず理解へ反映された割合 |
| `joint_understanding_investigation_answered_rate` | – | 反復調査が人へ聞かずに答えへ到達した割合 |
| `joint_understanding_developer_question_rate` | – | 通訳のうち実際に判断質問まで到達した割合(少ないほど良い指標ではない) |

`InterviewMetricCategory` に `joint_understanding` を、`InterviewMetricKey` に
上記 8 key を追加(いずれも既存の有限集合の拡張)。

#### #311 との関係

本 Phase の観測項目は #311(低リスク提案の一括承認)の開始条件
「実利用の誤分類・取り消し・理解低下を観測できること」を判断する材料になるが、
#311 自体は引き続き実装しない。

#### テスト

`test_joint_understanding_reflux.py` 末尾の 3 件: カテゴリ分離と決定性、
観測ゼロでの `unmeasured`、System 分離。

### 2026-07-31 完了監査と後続スコープ

#328 と #329〜#334 の完了監査で、既存実装に対して次を補強した。

- Investigation Finding のコード/実行時証拠と run provenance を保存時に検証する。
- 再実行時のラウンド番号・空の carry state を正しく引き継ぎ、検証済み証拠が
  増えない場合だけ `no_new_evidence` とする。
- 通訳の目的/影響と選択肢を Finding 参照で根拠付ける。
- reflux に runtime evidence を保持し、未検証 fact を除外し、増分実行でも
  過去の有効 fact を失わない。
- Dashboard の実トリガー、action audit、保留からの再開、終了理由、実行時証拠
  表示を整合させる。
- supersede 済み Finding を reflux 率の分母から除外する。

一方、既存 Interview / Inquiry / System Understanding と並走する独立系統を
「共同理解の単一フロー」に変える作業は仕様判断を伴う。このため元 Epic と
Phase issue は閉じ、残件を以下へ限定して移管した。

- #336: 実運用フローへの統合、origin 別導線、reflux 後の canonical rebuild。
- #337: #308 の前提 bundle、actor/provenance、判断根拠の監査契約。
- #338: 不明解消・仮説反転・訂正・負担を outcome lineage で測る品質指標。
- #339: 依存/変更履歴/実行時候補を含む探索と Question Router の統合。

## システムインタビューの状態駆動ワークフロー UX(Issue #342)

Issue #342(サブイシュー #343-#346)は、システムインタビュー画面を
「すべての機能へ常にアクセスできる画面」から「現在の判断だけを、必要な
根拠とともに順番に提示するワークフロー」へ再設計するための **UX 仕様策定**
Issue である。4 つの sub-issue はいずれも 対象外 に「Dashboard コンポーネント
の変更」「API・DB・状態管理の具体設計」「テスト実装」を明記しており、
成果物は仕様そのものになる。

仕様本体は `docs/system-interview-workflow-ux.md`。本節はその要点と、
仕様策定の過程で確定した設計判断だけを記録する。

### 決めたこと

- **開発者向け状態 `W0`-`W7`**(#343): 開始条件の確認 / システム調査中 /
  理解の要点確認 / 判断への回答 / 意図とのズレ確認 / 提案レビュー /
  差分レビュー / 完了・次の作業。内部 stage(`STAGE_ORDER`)と
  `deriveUiState` の値は開発者に露出せず、対応表としてのみ保持する。
- **first-match の状態決定ルール表**(#343 §2.2): #287 の Alignment 分類
  ルール表と同じ方式で、永続化された事実に対する 8 行の順序付きルールから
  状態を一意に決める。スコア・類似度・LLM 判断は使わない(Principle 6)。
- **自動遷移と明示確認の 4 基準**(#343 §2.5): システム処理の開始・完了(A)と
  開発者の確定操作による前進(B)は自動、**既に完了した状態への後退は常に
  明示確認**(C)、ブロッキング例外による中断は自動(D)。後退を自動化すると
  「さっきまでの作業が消えた」体験になり、#342 が問題視する探索コストを
  再生産するため。
- **情報役割 `R1`-`R6`**(#344): 現在地 / 必須作業 / 根拠 / 詳細 /
  ブロッキング警告 / 履歴・監査。1 要素 1 役割。現行画面の主要 68 要素を
  棚卸しし、状態 × 役割のマトリクスに割り当てた。
- **自動化の有限ゲート `A1`-`A4`**(#345 §4.1): 対象リポジトリを変更しない /
  承認状態を変えない / 失敗が既存の確定を壊さない / 結果が監査記録として
  残り人間の確定を待つ。4 条件すべてを満たす処理のみ自動化してよい。
  Principle 5 / 7 と #288 を操作分類の判定基準に言い換えたもので、新しい
  安全境界は導入していない。
- **例外 `E1`-`E14`**(#346): ブロッキング / 劣化 / 情報 の 3 区分。
  `R3`/`R4`/`R6` 要素の取得失敗は必ず劣化であり主作業を止めない。

### 現行実装に対して仕様が指摘した構造的な差分

1. `InterviewUiState` は理解構築フェーズに 5 状態、承認以降に 1 状態という
   非対称な粒度で、`W4`(意図とのズレ確認)・`W6`(差分レビュー)・`W7`(終端)
   に対応する状態が存在しない。
2. `W4` が状態として無いため「どちらのタブを既定にするか」という本来不要な
   判断が生まれ、`conversationHasRequiredAction` / `manualMainTab` /
   「会話タブへ移動」導線という 3 つの補償機構を必要としている
   (`interview.tsx:1636-1666`, `509-546`)。状態モデルを直せば 3 つとも不要。
3. `zero_base` は「理解構築の失敗」という例外を状態として主フローへ混ぜて
   いる。仕様では `W3` の一形態 + `E3` の劣化表示として扱う。
4. 状態判定にクライアント限定の事実(`manualMainTabState` /
   `lastMaterialization` / `lastEvidenceReadsState` /
   `answerRevisionReflectedState`、`interview.tsx:1538-1556`)が混ざっており、
   再読込前後で表示状態が変わりうる。
5. 「未到達の操作を disabled + 説明文で見せる」パターンが、操作 6 件
   (差分を生成 / 理解を構築 / 理解を更新 / 突き合わせを実行 /
   AI に先に調査させる / 実態チェックを実行)と、その前提を説明する
   固定文 4 件の計 10 箇所ある。操作はいずれも §4.1 のゲートを満たすため
   自動側へ移り、`E3`/`E4`/`E5` の復旧操作としてのみ残る。説明文は
   ボタンを常設しなくなるため不要になる。

### 明示的に変えなかったこと

- 人間のゲート(理解の確認・Alignment 項目の確定・提案の承認/編集/却下・
  差分の適用・観測の開始)は 1 つも緩めない。差分生成(`OP-S7`)を自動側に
  置くのは、その前(承認)と後(確認と製品外での適用)にゲートがあり、変換
  そのものはゲートではないため。隔離 worktree の境界も不変。
- #341 の指標パネル(段階的開示・`guardrail` designation と要確認判定の分離)
  は変更しない。仕様が追加するのは配置(主フロー最上部から「履歴と監査情報」
  入口の中へ)と、指標は常に `R6` でありルール表に現れない、の 2 点のみ。
- #295 の根拠先出し条件(conflict / high_risk / runtime mismatch・stale /
  根拠 1 件)、Inquiry の 4 段階開示、`accept_current` のみ carry-over は
  そのまま維持する。

### 実装前提として新たに必要な永続事実

`W6` → `W7` の判定に必要な「差分レビューの完了」1 点のみ。現行はクライアント
限定状態でしか表現されておらず再読込で失われる。設計は後続の実装 Issue が行う
(#343-#346 の対象外)。それ以外の状態は既存の永続事実から決定的に導出できる。
