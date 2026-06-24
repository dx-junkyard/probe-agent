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

## リポジトリ設定案

設定例は [`probe-agent.example.yml`](../probe-agent.example.yml) を参照する。
実行コマンドは自動推測せず、この設定で明示する。
