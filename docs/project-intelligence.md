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

## リポジトリ設定案

設定例は [`probe-agent.example.yml`](../probe-agent.example.yml) を参照する。
実行コマンドは自動推測せず、この設定で明示する。
