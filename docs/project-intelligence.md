Warning: truncated output (original token count: 77275)
Total output lines: 4368

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
（[shared/schemas/project_intelligence.schema.json](../shared…47275 tokens truncated…しい runtime 事実をどう解釈するか」は reasoning モデルの仕事のまま
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
  提示(全件3件以下ならサンプル節なし)。誤り発見時の分類ルール自動
  再評価は未実装(残課題)。
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
- **Inquiry の前提追跡(#295 §5.6 拡張 → #308)**: Inquiry 行への
  snapshot/revision 参照列・`superseded` 状態・前提変化時の再確認復帰
  は未実装。DB migration を含む独立した issue として設計すべき規模。
- **評価指標(#295 §9 → #309)**: 疑問解消率・誤った回答確定率などの計測基盤
  は未実装。指標定義が UI 実装の安定後に確定するため見送り。
- **サンプル誤り発見時の分類ルール再評価(§5.4 後半 → #310)**: 疑問導線まで。
- **再確認カスケードの範囲(§5.5 → #312)**: goal / 確定済み Intent の変更時の
  carry-over 無効化までを実装。Core Capability レベルは決定的判定源が無いため
  対象外(下記「Core Capability は…」参照)。実装レビューで、この限界が
  本節に明記されていなかったため追記した。
- **no_review_required ポリシーの外部化(§7.3 → #313)**: 分類ルールは
  `app/alignment.py` の `_RULES`(決定的な first-match 表)として実装しており
  Principle 6 は満たすが、コード変更なしにレビュー・調整できる独立成果物には
  なっていない。
- **提案 §7 のフィールド名・status 集合との差異**: 既存実装のフィールド名
  (`non_goals`、`status` 等)を維持し、#295 記載の名称
  (`out_of_scope`、`confirmation_state` 等)への改名は行わない
  (スキーマ契約の互換性優先)。同様に Inquiry status は現行の 5 値
  (`open` / `resolved` / `unresolved` / `cancelled` / `held`)を維持する。
  #295 §7.5 の `investigating` / `answered` / `insufficient_evidence` は
  メッセージ内容と固定テンプレートで同等の区別を実現しており機能差がない。
  `superseded` のみ機能追加を伴うため #308 で扱う。

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
   Core Capability は per-capability の確定タイムスタンプ列が存在せず
   決定的判定源が無いため今回は対象外(ヒューリスティック差分は
   Principle 6 で禁止のため実装しない)。
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
- sampling / audit / model cost に System-scoped 上限
  (`resource_limits` の既存機構と同型の日次上限)。

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
