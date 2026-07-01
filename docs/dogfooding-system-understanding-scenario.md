# probe-agent Dogfooding: System Understanding 導線検証シナリオ

## 概要

probe-agent 自身を対象リポジトリとして使い、System Understanding の導線が実際に成立するかを検証する。

Issue #89 で追加された 15 ファイルの `probe-agent:` source-authored metadata を入力として、
Pipeline の各ステップが正しく動作し、Dashboard 上の画面遷移が途切れないことを確認する。

## 前提条件

- Control Server と Dashboard が起動していること
- probe-agent リポジトリがローカルに存在すること
- Python 仮想環境がアクティベートされていること

## 検証ステップ

### Step 1: Control Server と Dashboard を起動する

ターミナルを 2 つ開き、それぞれで起動する。

**Control Server** (port 8000):

```bash
cd apps/control-server
uvicorn app.main:app --reload --port 8000
```

**Dashboard** (port 5173):

```bash
cd apps/dashboard
npm run dev
```

Dashboard が `http://localhost:5173` で表示されることを確認する。

### Step 2: probe-agent リポジトリを設定する

Dashboard の Repository ページで probe-agent 自身のリポジトリパスを設定する。

```bash
curl -X PUT http://localhost:8000/repository \
  -H "Content-Type: application/json" \
  -d '{
    "repo_path": "/path/to/probe-agent",
    "include_patterns": ["apps/control-server/**/*.py"],
    "exclude_patterns": ["**/tests/**", "**/__pycache__/**"]
  }'
```

`repo_path` は実際のローカルパスに置き換える。
応答で `repo_path` が設定されていることを確認する。

### Step 3: Snapshot を作成する

現在の commit SHA を pin して snapshot を作成する。

```bash
# 現在の HEAD の commit SHA を取得
COMMIT_SHA=$(git rev-parse HEAD)

curl -X POST http://localhost:8000/repository/snapshots \
  -H "Content-Type: application/json" \
  -d "{\"commit_sha\": \"$COMMIT_SHA\"}"
```

応答で `status: "ready"` と `commit_sha` が返ることを確認する。

### Step 4: Code Symbols を index する

```bash
curl -X POST http://localhost:8000/repository/symbols/index
```

deterministic な symbol index が実行される。以下を確認する:

- 応答に `symbols` 配列が含まれること
- source-authored `probe-agent:` metadata が抽出されていること
  - `capability`, `element_type`, `role` 等のフィールドが含まれるシンボルがあること
- `symbols_with_source_metadata` が 0 より大きいこと

### Step 5: System Understanding を Build する

```bash
curl -X POST http://localhost:8000/repository/system-understanding/build
```

Pipeline checklist の各ステップ status を確認する:

| Pipeline Step | 期待 status | 備考 |
| --- | --- | --- |
| `repository_configured` | `complete` | Step 2 で設定済み |
| `snapshot_ready` | `complete` | Step 3 で作成済み |
| `symbols_indexed` | `complete` | Step 4 で実行済み |
| `entrypoints_discovered` | `complete` | API route handler が検出される |
| `documentation_indexed` | `complete` or `blocked` | reasoning model 依存 |
| `documentation_claims_scanned` | `complete` or `blocked` | reasoning model 依存 |
| `docs_code_reconciled` | `complete` or `warning` | gap がある場合 `warning` |
| `capability_hierarchy_ready` | `complete` or `blocked` | reasoning model 依存 |

### Step 6: Pipeline Checklist を確認する

Dashboard の System Understanding ページを開く (`http://localhost:5173` > System Understanding)。

確認事項:

- Pipeline の各ステップが Step 5 の期待通りの status で表示されること
- reasoning model 未設定の場合、`documentation_indexed` と `documentation_claims_scanned` が `blocked` であること
- `blocked` のステップには detail に理由が表示されること
- Next Actions に未完了ステップへの対処が示されること

### Step 7: Capability Map を開く

System Understanding ページの capabilities セクションから Capability Map ページへ遷移する
(クロスページリンク `?capability=<name>` で自動選択される)。

source-authored provenance の capability が表示されることを確認する。
期待される capability 一覧:

| capability key | 主要ファイル |
| --- | --- |
| `repository-understanding` | `system_understanding_service.py`, `routes/project_intelligence.py` |
| `documentation-understanding` | `documentation_indexer.py`, `documentation_chunker.py`, `documentation_claim_scanner.py`, `understanding_graph.py` |
| `code-intelligence` | `code_indexer.py` |
| `docs-code-reconciliation` | `docs_code_reconciler.py` |
| `capability-mapping` | `capability_hierarchy.py` |
| `entrypoint-discovery` | `entrypoint_discovery.py`, `api_scan.py` |
| `execution-flow-understanding` | `flow_graph.py` |
| `probe-planning` | (probe plan 関連モジュール) |
| `variant-evaluation` | `experiment_runner.py` |
| `interactive-system-understanding` | `routes/interview.py` |

### Step 8: Capability を選択して詳細を確認する

例えば `documentation-understanding` を選択し、Capability Node Detail で以下を確認する:

- **関連する API Boundary (entrypoint) の一覧**: documentation indexing に関連する route handler
- **関連する Major Function の一覧**: `documentation_indexer.py`, `documentation_chunker.py`, `documentation_claim_scanner.py`, `understanding_graph.py` の主要関数
- **Source anchor**: ファイルパス + 行番号 (例: `apps/control-server/app/documentation_indexer.py:1`)
- **Provenance kind**: `source_authored` (手動で記述された `probe-agent:` メタデータに基づく)
- **Probe Flow Candidates**: この capability を観測するための probe 候補

### Step 9: 関連 API / 関数を開く

Capability detail から関連 entrypoint を選択し、API Role Card で以下を確認する:

| 項目 | 確認内容 |
| --- | --- |
| method/path | 例: `POST /repository/system-understanding/build` |
| capability | `repository-understanding` |
| role | route handler の役割 |
| operation_kind | `read` / `write` / `side_effect` 等 |
| state_effects | DB 書き込み等の副作用 |
| provenance | `source_authored` |

### Step 10: Flow Explorer へ遷移する

API Role Card または Capability detail の "Open in Flow Explorer" リンクから Flow Explorer へ遷移する。

確認事項:

- entrypoint が自動選択されること (`?entrypoint_type=...&entrypoint_id=...` パラメータ)
- 選択された entrypoint から呼び出される関数の flow graph が構築されること
- node と edge が可視化されること

### Step 11: Node/Edge を選んで Probe Plan draft を作る

Flow Explorer で node を選択し、probe 候補として以下を確認する:

| 項目 | 確認内容 |
| --- | --- |
| selected target | 選択した関数名とファイルパス |
| recommended mode | `trace` / `shadow` / `off` |
| side-effect risk | `none` / `low` / `medium` / `high` |

"Create Probe Plan" ボタンから draft を作成する。

### Step 12: Probe Planner で Plan を確認する

Probe Planner ページ (`GET /repository/probe-plans`) で作成した plan が表示されることを確認する:

| 項目 | 確認内容 |
| --- | --- |
| objective | probe の目的 |
| feature_id | 関連する feature (設定されている場合) |
| status | `proposed` |
| probe points | 選択した node に対応する probe point 一覧 |

## 期待される結果

### Source Metadata Coverage

- 15+ ファイルの module-level メタデータが symbol index に抽出される
- 主要 route handler の boundary メタデータが抽出される
- `symbols_with_source_metadata` > 0

### Capability Map

- 各 capability が `source_authored` provenance で表示される
- 関連 API / 関数 / probe candidates が capability ごとにグループ化される
- capability 間の関係 (element_type: core / element) が視覚的に区別される

### Gap Worklist

- メタデータ未付与の entrypoint が `unclassified_entrypoint` として検出される
- 各 gap に `next_actions` が設定されている
- Capability フィルタで絞り込みができる
- gap の `entrypoint_refs` から Flow Explorer へのリンクが機能する

### 導線の完成度

以下の導線が途切れずに辿れること:

```text
System Understanding
  → Capability Map
    → Capability Detail (関連 API / 関数)
      → Flow Explorer (entrypoint 自動選択)
        → Probe Plan draft
          → Probe Planner
```

## 確認観点

このシナリオ完了時に、以下の問いに答えられること:

1. **このシステムは何を目指しているか?** → System Purpose が表示される
2. **中核能力は何か?** → Core Capability 一覧が Capability Map に表示される
3. **各能力はどの API / 関数で支えられているか?** → Capability detail に関連 API Boundary / Major Function が表示される
4. **どこを probe すれば能力を観測できるか?** → Flow Explorer から Probe Plan draft を作成できる
5. **docs と code のズレはどこにあるか?** → Gap Worklist に gap と next_actions が表示される

## トラブルシューティング

### reasoning model 未設定で `blocked` になるステップがある

`documentation_indexed`, `documentation_claims_scanned`, `capability_hierarchy_ready` は reasoning model を必要とする。
これらが `blocked` の場合でも、決定的ステップ (`symbols_indexed`, `entrypoints_discovered`) と source-authored metadata に基づく Capability Map は検証可能である。

### symbols_with_source_metadata が 0 になる

- `include_patterns` が `apps/control-server/**/*.py` を含んでいるか確認する
- snapshot の `commit_sha` が Issue #89 以降のコミットを指しているか確認する
- `exclude_patterns` でメタデータ付きファイルが除外されていないか確認する

### Capability Map に capability が表示されない

- Step 4 (symbols/index) が正常に完了しているか確認する
- Step 5 (system-understanding/build) の応答で `capabilities` 配列が空でないか確認する
- API レスポンスの `provenance_kind` が `source_authored` であることを確認する
