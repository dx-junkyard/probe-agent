# probe-agent Dogfooding: System Understanding 導線検証シナリオ

## 概要

probe-agent 自身を対象リポジトリとして使い、System Understanding の導線が実際に成立するかを検証する。

Issue #89 で追加された 15 ファイルの `probe-agent:` source-authored metadata を入力として、
Pipeline の各ステップが正しく動作し、Dashboard 上の画面遷移が途切れないことを確認する。

Step 1-12 は Probe Plan の draft 作成までをカバーする。Step 13 以降(Issue
#260 で追加)は、パッチ生成・検証・適用 → SDK 接続 → トレース観測 →
Replay 承認 / AI Candidate Studio での候補評価 → Experiment decision →
GitHub publish(任意)まで、フロー後半の導線を検証する。

## 前提条件

- Control Server と Dashboard が起動していること
- probe-agent リポジトリがローカルに存在すること
- Python 仮想環境がアクティベートされていること
- Step 13 以降を通しで検証する場合: ログインユーザーを作成できること
  (`CONTROL_ADMIN_USERNAME` / `CONTROL_ADMIN_PASSWORD` を設定できる環境)。
  Step 23(GitHub publish)まで検証する場合はさらに、書き込んでよい GitHub
  リポジトリに対して設定済みの GitHub App インストールが必要(任意)

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

`POST /repository/system-understanding/build` は非同期ジョブとして実行され、
build 完了を待たずに `202` と build id を即座に返す(Issue #106: 同期実行だと
LLM 呼び出し中に `/health` `/auth/me` `/systems` まで応答不能になっていた)。

```bash
curl -X POST http://localhost:8000/repository/system-understanding/build
# => {"id": 1, "status": "queued", ...}

# 完了するまで status をポーリングする
curl http://localhost:8000/repository/system-understanding/build/1
# => status が "completed" または "failed" になるまで繰り返す

# 集約結果を取得する
curl http://localhost:8000/repository/system-understanding
```

Pipeline checklist の各ステップ status を確認する:

| Pipeline Step | 期待 status | 備考 |
| --- | --- | --- |
| `repository_configured` | `complete` | Step 2 で設定済み |
| `snapshot_ready` | `complete` | Step 3 で作成済み |
| `symbols_indexed` | `complete` | Step 4 で実行済み |
| `entrypoints_discovered` | `complete` | API route handler が検出される |
| `documentation_indexed` | `complete` or `warning` | deterministic chunk index |
| `documentation_claims_scanned` | `complete` or `blocked` | reasoning model 依存 |
| `docs_code_reconciled` | `complete` or `warning` | gap がある場合 `warning` |
| `capability_hierarchy_ready` | `complete` or `blocked` | reasoning model 依存 |

### Step 6: Pipeline Checklist を確認する

Dashboard の System Understanding ページを開く (`http://localhost:5173` > System Understanding)。

確認事項:

- Pipeline の各ステップが Step 5 の期待通りの status で表示されること
- reasoning model 未設定の場合、`documentation_claims_scanned` が `blocked` であること
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

以降の Step 14 以降で `PLAN_ID` / `POINT_ID` として参照する id は、このページ
または次のコマンドの応答から控える。

```bash
curl http://localhost:8000/repository/probe-plans
# => {"plans": [{"id": <PLAN_ID>, "status": "proposed", ...}], ...}

curl http://localhost:8000/repository/probe-plans/<PLAN_ID>
# => {..., "probe_points": [{"id": <POINT_ID>, "status": "proposed", "denylist_hit": null, ...}, ...]}
```

## Step 13 以降: パッチ適用 → SDK 接続 → 観測 → 候補評価 → publish（Issue #260）

Step 1-12 は Probe Plan の draft 作成までを検証する。ここから先は、実際に
instrumentation patch を生成・検証・適用し、SDK でトレースを送り、Simulation
/ AI Candidate Studio で候補を評価して Experiment の decision を記録し、最後に
GitHub publish（任意）まで導線を辿る。#189（Hub 再編追随のドキュメント更新）
とは重複しない、フロー後半（4→6 ステップ目）の検証手順を追加する。

### Step 13: ログインユーザーを準備する（Step 17 以降の前提）

Step 1-12 の curl 例はすべて認証なし（anonymous モード: `CONTROL_ADMIN_USERNAME`
/ `CONTROL_ADMIN_PASSWORD` も `CONTROL_API_KEYS` も未設定）で動作する
——`apps/control-server/app/auth.py` の `get_principal` は、ユーザーも legacy
key も存在しない場合 `Principal(auth="anonymous")` を返し、`get_system_id` は
anonymous 呼び出しを常に自動生成済みの `Legacy System` に解決する
(`_legacy_system_id`)。

一方、以下は `require_user`（ログインセッション必須。SDK API token・legacy
key・anonymous はすべて 403 になる）で保護されている:

| 対象 | 理由 |
| --- | --- |
| `POST /repository/probe-patches/{patch_id}/apply` | 実リポジトリの working tree を書き換えるため |
| `POST /tokens/me`（SDK トークン発行）、`GET /tokens/me` | 自分の token の発行・一覧は要ログイン（README 参照） |
| `apps/control-server/app/routes/replay.py` の全 route（`/replay-sets`, `/replay-runs`, `/replay-variant-runs`, `/replay-variant-drafts`, `/replay-source-diff`, `/components/{id}/replay-approval` 等） | router 定義そのものに `dependencies=[Depends(require_user)]` が付与されている |
| `apps/control-server/app/routes/github_connections.py` / `publish_jobs.py` の全 route | GitHub App 接続・publish job はすべて要ログイン（installation 管理は要 admin） |

これらを curl だけで検証するにはログインユーザーが必要になる。Step 1 で
起動した Control Server を一度停止し、以下の環境変数を設定してから再起動
する（`apps/control-server/README.md` の「2. admin でログインして API token
を発行」と同じ仕組み）。

```bash
export CONTROL_ADMIN_USERNAME=dogfood-admin
export CONTROL_ADMIN_PASSWORD=<any strong local password>
# Step 1 と同じコマンドで再起動
cd apps/control-server
uvicorn app.main:app --reload --port 8000
```

起動時にこの admin ユーザーが作成される。ログインして token を取得する。

```bash
ADMIN_TOKEN=$(curl -sS -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"dogfood-admin","password":"<any strong local password>"}' \
  | sed -E 's/.*"access_token":"([^"]+)".*/\1/')
```

ログインセッションは(SDK token と違って)system を token に紐づけないため、
`X-Probe-System-Id` ヘッダーで対象 system を明示する必要がある
(`get_system_id`)。Step 1-12 の anonymous 呼び出しが操作していたのは
`Legacy System` なので、その id を控えて以降のログイン必須リクエストすべて
に付与する。

```bash
curl -sS http://localhost:8000/systems -H "Authorization: Bearer ${ADMIN_TOKEN}"
# => [{"id": <SYSTEM_ID>, "name": "Legacy System", ...}, ...]
export SYSTEM_ID=<応答の Legacy System の id>
```

以降、ヘッダー付きの例には `-H "Authorization: Bearer ${ADMIN_TOKEN}" -H "X-Probe-System-Id: ${SYSTEM_ID}"` を付ける。ヘッダーの無い例は Step 1-12 と同様 anonymous のまま(`get_system_id` が Legacy System に解決する)で構わない。

### Step 14: Probe Point を承認する（anonymous のまま）

`PUT /repository/probe-points/{point_id}/status` は `get_system_id` のみに
依存し、ログイン不要(#255/#258 のゲートは Dashboard 側の disabled 表示であり、
このエンドポイント自体は無条件に受け付ける)。

```bash
curl -X PUT http://localhost:8000/repository/probe-points/<POINT_ID>/status \
  -H "Content-Type: application/json" \
  -d '{"status": "approved"}'
```

承認済み・denylist に該当しない probe point が 1 件以上ないと、次の patch
生成は 400 になる(`generate_patch_endpoint` の
`"No approved probe points. Approve points before generating a patch."`)。

### Step 15: Probe Patch を生成する（anonymous のまま）

```bash
curl -X POST http://localhost:8000/repository/probe-plans/<PLAN_ID>/patch
# => {"id": <PATCH_ID>, "status": "generated" | "failed", "diff": "...", ...}
```

### Step 16: Probe Patch を検証する（Validate、anonymous のまま）

```bash
curl -X POST http://localhost:8000/repository/probe-patches/<PATCH_ID>/validate
```

`status` が `failed` の patch を validate しようとすると 400
(`"Cannot validate a failed patch"`、Issue #255)。`validation_runs` に
`baseline` / `probed` variant の実行結果が追加される。

### Step 17: Probe Patch を適用する（Apply、ログイン必須）

適用前に、現在の repository HEAD を確認する(patch が古い commit 向けだと
apply は stale として拒否される、Issue #255)。

```bash
curl http://localhost:8000/repository/status
# => {"current_head": "<HEAD_SHA>", ...}
```

```bash
curl -X POST http://localhost:8000/repository/probe-patches/<PATCH_ID>/apply \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "X-Probe-System-Id: ${SYSTEM_ID}" \
  -H "Content-Type: application/json" \
  -d "{\"confirmed\": true, \"expected_commit_sha\": \"<HEAD_SHA>\"}"
```

適用はリポジトリの working tree のみを書き換え、commit は作らない
(Principle 5/8。Probe Planner ページの表示文言 "Applied to the repository
working tree (no commit created)" と同じ)。対象 repository の tracked
branch はこの適用によって変更されない。

### Step 18: SDK を接続してトレースを送る（ログイン必須はトークン発行のみ）

SDK 用トークンを発行する(`POST /tokens/me` は要ログイン。`system_id` に
Step 13 で控えた `SYSTEM_ID` を明示し、Step 1-12 と同じ system にトレースが
入るようにする)。

```bash
curl -X POST http://localhost:8000/tokens/me \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"dogfood-sdk-token\", \"system_id\": ${SYSTEM_ID}, \"expires_in_days\": 30}"
# => {"token": "<RAW_TOKEN>", ...}
```

発行された token を使い、既存のサンプルアプリ
(`examples/simple-pipeline`、`summarizer` / `classifier` / `json-normalizer`
の 3 component。`json-normalizer` は `replay_capture=True` で構造化 input
capture 済み)を実行してトレースを送る。SDK は `PROBE_API_KEY` を
`X-Api-Key` として送るため、これ以降のトレース送信・ポリシー変更は
SDK 側のトークン(`token_kind="api"`。system に紐づいており
`X-Probe-System-Id` 不要)経由になる。

```bash
pip install -e packages/python-probe
cd examples/simple-pipeline
PROBE_SERVER_URL=http://localhost:8000 PROBE_API_KEY=<応答の token> python main.py
```

### Step 19: Components でトレースを観測する（anonymous のまま）

```bash
curl http://localhost:8000/connectivity/status
# => {"state": "receiving", ...}（smoke ではない実トレースを受信すると "receiving"）

curl http://localhost:8000/components
# => summarizer / classifier / json-normalizer が trace_count > 0 で表示される

curl "http://localhost:8000/components/json-normalizer/traces?limit=5"
# => trace_id を控える（Step 21 で使用）
```

Dashboard の Components ページ (`/components?component=json-normalizer`) で
同じ内容を Traces タブで確認する。connectivity が `receiving` になったこと
は Overview の Get Started ステップ 4「View traces in Components」の完了
マーク(`data-done`)にも反映される(Issue #259)。

### Step 20: Shadow mode で候補実装を観測する（anonymous のまま、任意）

`examples/simple-pipeline/main.py` は `summarizer` / `classifier` にあらか
じめ候補実装(`summarize_v2` / `classify_v2`)を `set_candidate` で登録して
いるため、mode を `shadow` に切り替えて再実行するだけで shadow 比較を観測
できる。

```bash
curl -X PUT http://localhost:8000/components/summarizer/policy \
  -H "Content-Type: application/json" \
  -d '{"mode": "shadow"}'

# main.py を再実行 (PROBE_SERVER_URL=... PROBE_API_KEY=... python main.py)

curl http://localhost:8000/components/summarizer/shadow-results
```

`shadow` mode は本番の返り値に影響しない(Principle 1)。`trace_count > 0`
の component なので Dashboard の `shadow` ボタンは disabled にならない
(Issue #258 のゲートは `trace_count === 0` の component だけを対象にする)。

### Step 21: Replay を承認し、AI Candidate Studio で候補を生成・評価する

`json-normalizer` の replay 実行には component 単位の human 承認が必要
(`decision_method: manual`)。承認 API は `replay.py` の router 全体が
`require_user` を課しているため、ログインユーザーで呼ぶ。

```bash
curl -X POST http://localhost:8000/components/json-normalizer/replay-approval \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "X-Probe-System-Id: ${SYSTEM_ID}" \
  -H "Content-Type: application/json" \
  -d '{"reason": "dogfooding walkthrough"}'
```

以降の AI Candidate Studio 自体の API(`/candidate-sessions`,
`/candidate-versions/*`)は `get_system_id` のみに依存し、ログイン不要
(anonymous のままでよい)。

```bash
curl -X POST http://localhost:8000/candidate-sessions \
  -H "Content-Type: application/json" \
  -d '{"component_id": "json-normalizer", "trace_id": "<TRACE_ID>", "objective": "Reject non-object JSON payloads instead of silently re-serializing them"}'
# => {"id": <SESSION_ID>, "commit_sha": "...", "symbol_path": "...", ...}

curl -X POST http://localhost:8000/candidate-sessions/<SESSION_ID>/generate \
  -H "Content-Type: application/json" \
  -d '{"instruction": "Raise a clear error when the parsed JSON payload is not an object, instead of re-serializing lists/scalars unchanged."}'
# => {"id": <VERSION_ID>, "status": "proposed", "patch_text": "...", "is_mock": false, ...}
```

候補生成は reasoning-model 必須(fail-closed、Principle 6/7)。ローカルで
実際の LLM API キーを設定していない場合は `LLM_PROVIDER=mock` を設定して
smoke 確認する(応答の `is_mock: true` がテスト/ローカル動作確認用データで
あることを示す。Principle 7)。

```bash
curl -X POST http://localhost:8000/candidate-versions/<VERSION_ID>/replay \
  -H "Content-Type: application/json" \
  -d '{}'
# => {"replay_status": "completed" | "failed", "replay_run_id": <RUN_ID>, "replay_variant_id": <VARIANT_ID>, ...}
```

`replay-approval` が無い、または `revoked` の状態でこれを呼ぶと拒否される
(Step 21 冒頭の承認が前提)。

### Step 22: 候補を Experiment に送り、decision を記録する

```bash
curl -X POST http://localhost:8000/candidate-versions/<VERSION_ID>/promote
# => {"replay_run_id": <RUN_ID>, "replay_variant_id": <VARIANT_ID>, ...}
```

promote は Experiment を作成しない(Principle 7)。Dashboard が
`/experiments?replay_run_id=<RUN_ID>&replay_variant_id=<VARIANT_ID>` を開き、
prefill された Create Experiment ダイアログを表示する。プリフィル内容
(`label` / `patch_text` / `risk_note`)を curl で確認する場合は、この GET
も `replay.py` の router に属するためログインが必要:

```bash
curl -sS "http://localhost:8000/replay-variant-runs/<RUN_ID>/variants/<VARIANT_ID>/experiment-payload" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "X-Probe-System-Id: ${SYSTEM_ID}"
```

内容を確認し、Dashboard の Experiments ページで `Create` をクリックして
Experiment を作成する(`POST /experiments`、anonymous のままでよい)。作成後
baseline / candidate variant を実行する。

```bash
curl -X POST http://localhost:8000/experiments/<EXPERIMENT_ID>/run
# => {"status": "completed" | "failed", "variants": [...], ...}
```

結果を見て decision を記録する。

```bash
curl -X PUT http://localhost:8000/experiments/<EXPERIMENT_ID>/decision \
  -H "Content-Type: application/json" \
  -d '{"decision": "adopted", "note": "candidate improved robustness with no regression"}'
```

decision 保存後、Experiments ページに Next step カードが表示される
(Issue #259): `adopted` なら GitHub publish リンク(利用可能な場合)+ Probe
Planner への次サイクル導線、`rejected` / `needs_more_data` なら AI
Candidate Studio への再生成導線。

### Step 23: GitHub Publish Job を作成する（任意、GitHub App 設定が前提）

この Step は Issue #216/#222 の GitHub App publish workflow が前提で、
組織単位の GitHub App インストールを admin が登録し、対象 System に割り当て
済みであることが必要(`github_connections.py` の全 route が `require_user`、
installation 管理は `require_admin`)。実際の GitHub リポジトリへ commit /
push / PR 作成が発生するため、ローカルの使い捨て検証用リポジトリなど、書き
込んでよい対象で試すこと。

```bash
curl http://localhost:8000/github/app-status \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"
# => {"configured": true, ...} でなければ以降はスキップしてよい

curl -X POST http://localhost:8000/github/connections \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "X-Probe-System-Id: ${SYSTEM_ID}" \
  -H "Content-Type: application/json" \
  -d '{"owner": "<github-owner>", "repo": "<github-repo>", "installation_id": <INSTALLATION_ID>}'
# => {"id": <CONNECTION_ID>, "status": "connected" | ..., ...}

curl -X POST http://localhost:8000/github/connections/<CONNECTION_ID>/publish-jobs \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "X-Probe-System-Id: ${SYSTEM_ID}" \
  -H "Content-Type: application/json" \
  -d "{\"patch_id\": <PATCH_ID>}"
# => {"id": <JOB_ID>, "status": "pending" | "awaiting_approval" | ..., ...}
```

Probe Planner のパッチ適用成功後(Step 17)や Experiments の `adopted`
decision(Step 22)から Dashboard で GitHub へ遷移すると、この Publish Jobs
タブが選択され、対象パッチが `?patch=<id>` で prefill される(GitHub App
設定済み + connected connection が 1 件以上のときのみ、Issue #259)。承認
(`POST .../publish-jobs/{job_id}/approve`)を経て初めて commit → push →
PR 作成が進む。push 先は常にサーバー生成の `probe/` prefix ブランチのみで、
default branch への直接 push・force push・probe-agent 自身による merge は
行われない(Principle 5/8)。

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

### Instrumentation / 観測 / 評価 / Publish（Step 13-23、Issue #260）

- 承認済み・denylist 非該当の probe point から patch が生成され
  (`status: "generated"`)、validate で baseline / probed 両 variant が
  `PASS` になる
- apply 後、対象リポジトリの working tree にのみ変更が反映され、tracked
  branch や commit 履歴は変化しない(`git status` で確認できる差分のみ)
- SDK 接続後、`GET /connectivity/status` が `receiving` になり、
  `summarizer` / `classifier` / `json-normalizer` の `trace_count` が
  増える
- `summarizer` を `shadow` mode にして再実行すると `shadow-results` に
  candidate(`summarize_v2`)との比較結果が記録され、本番の戻り値
  (`trace` mode 時と同じ `summarize` の出力)は変化しない
- `json-normalizer` の replay 承認後、AI Candidate Studio で生成した候補が
  replay 実行され、`replay_status: "completed"` になる(reasoning model
  未設定・mock 構成では `is_mock: true` が付く)
- promote → Create Experiment → run → decision 記録まで、Experiment が
  自動作成・自動 adopt されることなく毎回人の操作を経由する
- decision 保存後、Experiments ページに `human_decision` に応じた Next
  step カードが表示される
- (任意)GitHub App 設定済みの場合、publish job が `pending` →
  `awaiting_approval` → 承認後に commit/push/PR 作成まで進み、push 先が
  常に `probe/` prefix の新規ブランチであること

### 導線の完成度

以下の導線が途切れずに辿れること:

```text
System Understanding
  → Capability Map
    → Capability Detail (関連 API / 関数)
      → Flow Explorer (entrypoint 自動選択)
        → Probe Plan draft
          → Probe Planner
            → Probe Patch (generate → validate → apply)
              → Connect SDK (token 発行 → トレース送信)
                → Components (観測 / mode 切替 / shadow 比較)
                  → Replay 承認 → AI Candidate Studio (候補生成・評価)
                    → Experiments (promote → create → run → decision)
                      → GitHub (publish job, 任意)
```

## 確認観点

このシナリオ完了時に、以下の問いに答えられること:

1. **このシステムは何を目指しているか?** → System Purpose が表示される
2. **中核能力は何か?** → Core Capability 一覧が Capability Map に表示される
3. **各能力はどの API / 関数で支えられているか?** → Capability detail に関連 API Boundary / Major Function が表示される
4. **どこを probe すれば能力を観測できるか?** → Flow Explorer から Probe Plan draft を作成できる
5. **docs と code のズレはどこにあるか?** → Gap Worklist に gap と next_actions が表示される
6. **承認した観測点は実際にコードへ反映されるか?** → Probe Patch が生成・検証・適用され、working tree にのみ変更が入る(Step 15-17)
7. **観測は実際にトレースを生むか?** → SDK 接続後、Components で `trace_count` が増え connectivity が `receiving` になる(Step 18-19)
8. **候補コードは安全に評価できるか?** → 隔離された Replay(承認必須・network off・毎回 worktree cleanup)で候補が実行され、本番には影響しない(Step 21)
9. **評価結果は人が決めて次に繋がるか?** → Experiment の decision(`adopted` / `rejected` / `needs_more_data`)に応じた Next step 導線が出る(Step 22)
10. **承認された変更は安全に外部リポジトリへ出せるか?** → 承認必須・`probe/` prefix ブランチのみ・force push なしの publish job で確認できる(Step 23、任意)

## トラブルシューティング

### reasoning model 未設定で `blocked` になるステップがある

`documentation_claims_scanned`, `capability_hierarchy_ready` は reasoning model を必要とする。
これらが `blocked` の場合でも、決定的ステップ (`documentation_indexed`, `symbols_indexed`, `entrypoints_discovered`) と source-authored metadata に基づく Capability Map は検証可能である。

### symbols_with_source_metadata が 0 になる

- `include_patterns` が `apps/control-server/**/*.py` を含んでいるか確認する
- snapshot の `commit_sha` が Issue #89 以降のコミットを指しているか確認する
- `exclude_patterns` でメタデータ付きファイルが除外されていないか確認する

### Capability Map に capability が表示されない

- Step 4 (symbols/index) が正常に完了しているか確認する
- Step 5 (system-understanding/build) の応答で `capabilities` 配列が空でないか確認する
- API レスポンスの `provenance_kind` が `source_authored` であることを確認する

### Step 17 以降で `403 A user account is required` になる

Step 13 のログイン(`ADMIN_TOKEN` 取得)を飛ばしている、もしくは
`Authorization: Bearer` ヘッダーを付け忘れている。SDK API token
(`token_kind="api"`)・legacy key・anonymous はいずれも `require_user` を
満たさない(`apps/control-server/app/auth.py`)。

### Step 17 で apply が stale として拒否される

`expected_commit_sha` が対象リポジトリの現在の HEAD と一致していない
(Issue #255)。`GET /repository/status` の `current_head` を取り直してから
再試行する。

### Step 21 で replay/candidate の実行が拒否される

`json-normalizer` に対する `POST /components/json-normalizer/replay-approval`
を先に実行しているか確認する。承認が無い、または `revoke` 済みだと
replay・候補 replay のどちらも拒否される。

### 候補生成が reasoning model エラーで失敗する

実際の LLM API キーを未設定のままローカルで一通り確認したい場合は
`LLM_PROVIDER=mock` を設定する(応答は `is_mock: true` になり、テスト/ロー
カル動作確認用データとして扱う。Principle 7)。ヒューリスティックへの
フォールバックはしないため(Principle 6)、reasoning model 呼び出し自体が
失敗する構成では候補生成は必ず失敗する。

### Step 23 で `configured: false` / connection が作れない

GitHub App がこの Control Server に設定されていない、または対象 System に
installation が割り当てられていない(Issue #216/#222)。この Step は任意
であり、未設定のままスキップしてよい。
