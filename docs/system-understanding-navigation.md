# System Understanding 導線と用語定義

## 用語定義

| 用語 | 意味 |
| --- | --- |
| System Purpose | 対象システム全体が何を達成するためのものか |
| Core Capability | System Purpose を支える中核能力 |
| Capability Element | Core Capability を構成する主要な実装単位 |
| Supporting Element | 補助的な実装・設定・運用要素（DB / filesystem / external HTTP / queue / scheduled job / CLI 等） |
| API Boundary / Entrypoint | 外部から処理が入る API、CLI、job、queue handler 等 |
| Major Function / Source Symbol | 実装上の関数・クラス・module |
| Probe Flow | 観測点の候補、観測理由、mode、risk、次の実験への接続 |
| Feature | ユーザー価値・業務フローとして見た機能 |

### Feature と Capability の違い

```text
Capability = システムが持つ構造的な能力（実装寄りの視点）
Feature    = ユーザー価値・業務フローとして見た機能（利用者寄りの視点）
```

Capability はソースコードの構造から決定的に導出できる（`probe-agent:` メタデータの
`capability` フィールドなど）。Feature はユーザーにとっての価値や業務フローを
表し、reasoning model による抽出・対応付けを必要とする。両者は補完的であり、
Capability Map と Feature Map が異なるページとして存在する理由でもある。

## System Understanding の階層構造

```text
System Purpose
  → Core Capability
    → Capability Element / Supporting Element
      → API Boundary / Entrypoint
        → Major Function / Source Symbol
          → Probe Flow
            → Experiment / Evaluation
```

## 目標 UI 導線

Dashboard における画面遷移の基本導線:

```text
System Understanding landing
  ↓
Capability Map
  ↓
Capability Node Detail
  ↓
API Role Card / Function Detail
  ↓
Flow Explorer
  ↓
Probe Planner
  ↓
Experiment Workspace
```

System Understanding は現在、上記の詳細画面への入口を 4 stage Hub として
まとめる。

```text
Understand
  → Repository / System Understanding / Capability Map / Feature Map
Decide Where to Observe
  → Capability Map / Flow Explorer / Probe Planner
Instrument
  → Probe Planner / Repository Refresh Hub
Evaluate
  → Experiments / Workspaces
```

### 各画面の役割

| 画面 | 役割 |
| --- | --- |
| System Understanding | 4 stage Hub、Pipeline checklist、System Purpose、Core Capabilities 一覧、metadata coverage、docs-code gap、Next Actions |
| Capability Map | System Purpose → Core Capability → Element のツリー表示とドリルダウン。選択 capability の gaps / probe plans / experiments も表示 |
| Capability Node Detail | 選択したノードの provenance、freshness/drift、source anchor |
| API Role Card | backend entrypoint の所属 capability、role、consumers、state effects、probe value |
| Flow Explorer | entrypoint からの候補実行フローの可視化とノード/エッジ選択 |
| Probe Planner | 選択した観測点の mode・risk・承認状態、`?plan=` deep link、patch generation / validation / apply の管理 |
| Experiments | baseline と source patch variants の隔離実行、比較、human decision |

## Context Header

主要な intelligence ページ（System Understanding、Capability Map、Feature Map、
Flow Explorer、Probe Planner、Experiments）は共通の Context Header を表示する。
これは「今見ている分析対象」を明示する read-only strip であり、値は既存 API /
URL params から取得する。

| 項目 | 取得元 |
| --- | --- |
| System | 現在選択中の system |
| Snapshot | System Understanding summary の `commit_sha`。未構築時のみ latest snapshot の `commit_sha` |
| Capability | URL の `?capability=` |
| Entrypoint | URL の `?entrypoint_type=` + `?entrypoint_id=` |
| Status | 完了済み pipeline step と gap 件数 |

`Snapshot:` は repository の現在 HEAD ではなく、分析済み snapshot の commit を
表示する。HEAD が進んだ場合は Repository Refresh Hub の stale 表示で扱い、
Context Header は pinned snapshot の文脈を崩さない。

## Pipeline Step 名

以後の issue で使う完了チェックリストの語彙:

```text
repository_configured      # リポジトリが設定されている
snapshot_ready             # pinned snapshot が作成されている
documentation_indexed      # README/docs が index されている
documentation_claims_scanned  # docs 内の主張が抽出されている
symbols_indexed            # code symbol が index されている
entrypoints_discovered     # API/CLI/queue 等の entrypoint が発見されている
docs_code_reconciled       # docs と code の差分が照合されている
capability_hierarchy_ready # 能力階層が生成されている
probe_plans_reviewed       # probe plan がレビューされている
```

各ステップのステータス:

| ステータス | 意味 |
| --- | --- |
| `complete` | 正常に完了 |
| `missing` | 未実行 |
| `warning` | 完了したが注意事項あり（例: docs-code gap が見つかった） |
| `blocked` | 前提条件が不足（例: reasoning model 未設定） |
| `failed` | 実行したがエラーで失敗 |

### Pipeline Checklist の Next-Step CTA（Issue #200）

初回ユーザーが Hub を開いたとき（pipeline が全 `missing`）でも、専用の空状態
UI は表示しない。`PipelineChecklist`
（`apps/dashboard/src/components/system-understanding/pipeline-checklist.tsx`）
が常時表示され、配列先頭から見て最初の非 `complete` step にのみ「次の一歩」の
CTA ボタンが付く。2 つ目以降の未完了 step には CTA を出さない。

step → CTA の対応は固定マッピング（`STEP_CTA`、CLAUDE.md Principle 6 の範囲内）
であり、推論やヒューリスティックではない:

| step | CTA | 遷移/操作 |
| --- | --- | --- |
| `repository_configured` | Configure repository | `/repository` へ遷移 |
| `snapshot_ready` | Create snapshot | `/repository` へ遷移 |
| 上記以外の全 step（`symbols_indexed` / `documentation_indexed` /
  `documentation_claims_scanned` / `entrypoints_discovered` /
  `docs_code_reconciled` / `capability_hierarchy_ready`） | Run Build / Refresh | ページの `Build / Refresh` ボタンと同じ
  `build.mutate()` を起動 |

Build 系 CTA はビルド実行中（`build.isPending` または最新ジョブが
`queued`/`running`）は無効化される。CTA には `pipeline-cta-<step>` の
`data-testid` が付く。

## ページ間ナビゲーション

クロスページリンク:

| From | To | Mechanism |
| --- | --- | --- |
| System Understanding — capabilities | Capability Map | `?capability=<name>` で自動選択 |
| System Understanding — entrypoints | Flow Explorer | `?entrypoint_type=...&entrypoint_id=...` で自動オープン |
| System Understanding — symbols (route) | Flow Explorer | route path で entrypoint を指定 |
| System Understanding — gap capability_key | Capability Map | `?capability=<key>` で自動選択 |
| System Understanding — gap entrypoint_refs | Flow Explorer | `?entrypoint_type=...&entrypoint_id=...` |
| System Understanding — gap `Create implementation issue` | Issue draft dialog | gap から issue draft を生成・編集・Markdown コピー・外部 URL 登録（#107, probe-agent DB が正本、外部 tracker 連携なし） |
| Capability Map — element/boundary | Flow Explorer | entrypoint_type + entrypoint_ref で指定 |
| Capability Map — Related APIs | Flow Explorer | `?entrypoint_type=...&entrypoint_id=...&capability=<key>` |
| Capability Map — Probe Plans | Probe Planner | `?plan=<id>&capability=<key>` |
| Capability Map — Experiments | Experiments | `?capability=<key>` |
| Capability Map — feature_id | Feature Map | `?feature=<id>` でハイライト＋スクロール |
| Feature Map — related capabilities | Capability Map | `?capability=<key>` で自動選択 |
| Feature Map — code links (feature_id) | Feature Map | `?feature=<id>` でハイライト |
| Flow Explorer — Back to Capability | Capability Map | `?capability=<key>` を保持 |
| Flow Explorer — Create Probe Plan draft | Probe Planner | `?plan=<id>&capability=<key>` |
| Probe Planner — Back to Capability | Capability Map | `?capability=<key>` を保持 |
| Probe Planner — Experiments | Experiments | `?capability=<key>` を保持 |
| Experiments — Back to Capability | Capability Map | `?capability=<key>` を保持 |
| System Understanding — Next Action `Review probe plan` | Probe Planner | `?plan=<id>` |

`?capability=` は selection state ではなく navigation context である。Flow Explorer、
Probe Planner、Experiments はこの値を使って Context Header と back link を表示し、
Capability Map から始めた探索が Evaluate stage まで途切れないようにする。

## Capability Context API

Capability Map の detail panel は
`GET /repository/capabilities/{capability_key}/context` を使い、選択 capability に
紐づく gaps / probe plans / experiments を表示する。

この API の結合ルールはすべて exact-key equality である。

| データ | 基準 |
| --- | --- |
| gaps | System Understanding の gap list を `capability_key` でフィルタ |
| probe plans | latest ready snapshot の `capability_hierarchy_nodes.feature_id` と `probe_plans.feature_id` |
| experiments | 同じ latest ready snapshot 上の plan feature_id と `experiments.feature_id` |

同一レスポンス内で snapshot 規約を混在させない。新しい snapshot が `indexing` /
`failed` の間も、context API は latest ready snapshot を基準にして、gaps /
probe plans / experiments を同じ分析文脈で返す。

## Next Actions

System Understanding の Next Actions は 4 stage に分類される。

| Category | Stage | 例 |
| --- | --- | --- |
| `understand` | Understand | Configure repository、Create snapshot、Review docs-code gaps |
| `observe` | Decide Where to Observe | Unclassified API found、Probe candidate available、Review probe plan |
| `instrument` | Instrument | Generate / validate probe patch |
| `evaluate` | Evaluate | Review experiment decision |

`Unclassified API found` は `unclassified_entrypoint` gap summary から生成され、
Interview 経由の分類作業へ誘導する。`Probe candidate available`
は `missing_probe_flow` gap summary から生成され、Flow Explorer / Probe Planner
経由で観測計画を作る導線を示す。

### gap 種別 → 解決手段の対応（Issue #199）

トップレベル Next Action と gap card の解決手段リンクは、
`apps/control-server/app/system_understanding_service.py` の
`GAP_NEXT_ACTIONS`（dict、モジュールレベル）を単一ソースとして導出される。
各 gap 種別のリストの先頭要素が主導線（primary resolution）であり、gap 種別由来の
トップレベル Next Action の link は必ずこの先頭要素と一致する
（`_build_next_actions` 内で `GAP_NEXT_ACTIONS[gap_type][0]["link"]` を参照）。

役割の原則は固定されている:

- **状態を修正・補完する作業（分類する、metadata を追加する、曖昧な所有権を
  明確にする）は Interview** が担当する。例: `unclassified_entrypoint` の
  主導線は `/interview`（Interview で分類し、結果は Capability Map で確認する）。
- **既存の状態を確認・閲覧する作業は Capability Map / Flow Explorer** が担当
  する。例: `missing_probe_flow` の主導線は `/flow-explorer`（観測点を探索し、
  そこから Probe Planner で plan を作る）。

| gap 種別 | 主導線（`GAP_NEXT_ACTIONS[gap_type][0]`） | 役割 |
| --- | --- | --- |
| `unclassified_entrypoint` | Open Interview (`/interview`) | 修正・補完 |
| `missing_probe_flow` | Open Flow Explorer (`/flow-explorer`) | 確認・閲覧 |
| `code_only` | Open source symbol (`/repository`) | 確認・閲覧 |
| `source_doc_mismatch` / `stale_explanation` | Propose explanation refresh (`/capability-map`) | 確認・閲覧 |
| `missing_evidence` | Improve documentation index (`/repository`) | 確認・閲覧 |
| `ambiguous_ownership` | Clarify ownership in Interview (`/interview`) | 修正・補完 |

Hub の Understand stage と Decide Where to Observe stage の Related pages には
どちらも Interview へのリンクが並ぶ。Interview のステージ構成
（purpose_confirmation / capability_confirmation / element_classification /
api_boundary_mapping / probe_flow_selection）が Understand と Decide Where to
Observe の両方に対応するためである。

### Primary Action（Issue #201）

`GET /system-understanding`（`GET /repository/system-understanding`）は
`next_actions`（stage 別リスト）に加えて、優先度最上位の action を単一の
`primary_action` として返す。Hub のページヘッダー（タイトル + Build /
Refresh ボタン）の直下に、`primary_action` を表示する単一カードが常時
1 枚だけ表示される（`data-testid="primary-action"`）。`next_actions` の
内容・順序はこの導入によって変わらない。

`primary_action`（および `next_actions` の各要素）は `action_kind` を持つ:
有限集合 `{"navigate", "build"}`（既定 `"navigate"`）。`navigate` は
`link` へのページ遷移、`build` は Build / Refresh ジョブを直接起動する
ボタンを意味する。

導出は `apps/control-server/app/system_understanding_service.py` の
`_derive_primary_action(pipeline, next_actions, latest_build)` という純粋関数
（`_build_next_actions` の結果と最新 build job の状態のみを入力に取る）が
担い、以下のルールを上から順に評価し、最初に該当したものを返す
（Principle 6: 有限集合に対する明示的分岐のみ、推論なし）:

| # | 条件 | 結果 |
| --- | --- | --- |
| 1 | `repository_configured` または `snapshot_ready` が `complete` でない | `next_actions[0]`（Configure repository / Create snapshot、`action_kind="navigate"`） |
| 2 | 最新 build job が `queued` / `running` | `primary_action = None`（BuildJobPanel が進捗を表示するため CTA を出さない） |
| 3 | pipeline に `complete` でない step がある（#1 で対象外の repository/snapshot 以外） | `action="Build system understanding"`, `action_kind="build"`, `link=None`、reason に未完了 step 数を含める。個別 step の修復 action（Index code symbols 等）は従来どおり `next_actions` に残る |
| 4 | 上記いずれにも該当しない | `next_actions[0]`（現行の生成順のまま。全充足時は `Start from Capability`） |

ルール 2 の build 実行状態は、既存の最新 build job 取得関数
（`system_understanding_jobs.get_latest_job`）をそのまま再利用する。DB ロックは
非再入なので、この呼び出しは `get_system_understanding` の
`with get_conn()` ブロックを抜けたあとに行う。

将来的に `apps/control-server/app/system_state.py`（System State Assessment、
Issue #193）へ統合される可能性があるが、本 issue ではまだ統合しない
（`_derive_primary_action` のコメント参照）。

### Stage Status（Issue #202）

`GET /system-understanding` は `next_actions` / `primary_action` に加えて、
4 stage それぞれの完了ステータスと件数サマリを `stages`（`stage` /
`status` / `counts` の配列）として返す。Hub の各 stage 見出しはこの
`status` をバッジとして表示し（`data-testid="stage-status-<stage>"`）、
Instrument / Evaluate stage は `counts` を件数サマリとして表示する
（`data-testid="stage-summary-instrument"` / `"stage-summary-evaluate"`。
counts が全て 0 のときは従来の説明テキストにフォールバックする）。

導出は `apps/control-server/app/system_understanding_service.py` の
`_derive_stage_statuses(...)` という純粋関数が担う。入力は `pipeline` と、
`get_system_understanding` が既に集めている purpose / capabilities /
gap_summary / plan・experiment id リストだけで、reasoning model は関与し
ない（Principle 6）。`status` は有限集合
`not_started | in_progress | blocked | complete` のみ。

各 stage のルールは上から順に評価し、最初に該当したものを採用する:

| stage | `not_started` | `blocked` | `complete` | それ以外 | `counts` |
| --- | --- | --- | --- | --- | --- |
| `understand` | 全 pipeline step が `missing` | pipeline に `blocked` / `failed` の step がある（`not_started` 判定より先に評価） | 全 step `complete` かつ purpose 定義済み（`_build_next_actions` と同じ判定）かつ capabilities が 1 件以上 | `in_progress` | `{"gaps": gap 総数}` |
| `observe` | `entrypoints_discovered` step が `complete` でない | （なし） | entrypoint が 1 件以上 かつ `unclassified_entrypoint` gap が 0 件 | `in_progress` | `{"entrypoints": ..., "unclassified": ...}` |
| `instrument` | probe plan が 0 件（system 内の総数） | （なし） | approved かつ validated patch 済みの plan が 1 件以上 かつ approved-without-patch が 0 件 | `in_progress` | `{"proposed": ..., "approved_without_patch": ..., "validated": ...}` |
| `evaluate` | experiment が 0 件（system 内の総数） | （なし） | decision 記録済みの completed experiment が 1 件以上 かつ undecided-completed experiment が 0 件 | `in_progress` | `{"undecided": ..., "decided": ...}` |

`understand` の `blocked` 判定は `not_started` より先に評価される（1 つで
も blocked/failed step があれば、他が全 missing であっても blocked を優先
する）。`observe` / `instrument` / `evaluate` に `blocked` はない（有限集合
上、この 3 stage は入力データに blocked/failed 相当の状態を持たないため）。

`instrument` / `evaluate` の件数取得は、`get_system_understanding` が
Next Actions 用に既に集めている `proposed_plan_ids` /
`approved_plan_ids_without_validated_patch` /
`undecided_completed_experiment_ids` をそのまま再利用し、不足分（plan 総数、
approved plan 総数、experiment 総数、decision 記録済み experiment 数）だけ
`probe_plans` / `experiments` テーブルへの追加 COUNT クエリで補う。
`validated` count は `approved plan 総数 - approved_without_patch 件数` で
導出し、`decided` count は `undecided_completed_experiment_ids` と同じ
`status = 'completed' AND human_decision`条件を反転させたクエリ
（`!= 'undecided'`）で求める。新しい判定基準を発明しない。

## Feature Map から始める場合

Feature Map は「ユーザー価値」を起点とする探索パスを提供する。

1. **Feature Map ページを開く**: System Understanding の "Start from Feature" カードまたはサイドバーから遷移
2. **Feature を選択**: ドキュメントから抽出された Feature 一覧から対象を選ぶ
3. **Code Links を確認**: Feature に紐づく `accepted` 状態の FeatureCodeLink を確認。各リンクの `symbol_qualified_name` がコード上の実装単位を示す
4. **Related Capabilities を確認**: Feature カード内の Capability リンクから Capability Map に遷移
   - 優先順位: (1) accepted FeatureCodeLink の symbol → source metadata の capability, (2) capability hierarchy node の feature_id, (3) docs-code gap refs
5. **Probe Plan を作成**: Capability の element を選び、Flow Explorer 経由で probe plan を作成する

## Capability Map から始める場合

Capability Map は「実装構造」を起点とする探索パスを提供する。

1. **Capability Map ページを開く**: System Understanding の "Start from Capability" カードまたはサイドバーから遷移
2. **Capability を選択**: ツリーから Core Capability を選ぶ
3. **Detail パネルを確認**:
   - **Related APIs**: この capability に属する API entrypoint の一覧。クリックで Flow Explorer に遷移
   - **Major Functions**: capability を構成する element の一覧（role, probe value 付き）
   - **Related Features**: この capability に紐づく Feature の一覧。クリックで Feature Map に遷移
   - **Probe Flow Candidates**: probe_value が設定された element。観測対象の候補
4. **Flow Explorer に遷移**: Related APIs のリンクまたは "Open in Flow Explorer" ボタンから遷移
5. **Probe Plan を作成**: Flow Explorer でノード/エッジを選択し、plan を submit すると自動的に Probe Planner に遷移

## Dogfooding: probe-agent 自身への System Understanding 適用

probe-agent は自身の `probe-agent:` source-authored metadata を使って
System Understanding パイプラインを検証できる（dogfooding）。

### メタデータが付与されているファイル

Issue #89 で以下の 15 ファイルに module-level `probe-agent:` メタデータを追加:

| ファイル | capability | element_type |
| --- | --- | --- |
| `system_understanding_service.py` | repository-understanding | core |
| `documentation_indexer.py` | documentation-understanding | core |
| `documentation_chunker.py` | documentation-understanding | element |
| `documentation_claim_scanner.py` | documentation-understanding | element |
| `understanding_graph.py` | documentation-understanding | element |
| `docs_code_reconciler.py` | docs-code-reconciliation | core |
| `system_understanding_reviewer.py` | repository-understanding | element |
| `code_indexer.py` | code-intelligence | core |
| `capability_hierarchy.py` | capability-mapping | core |
| `entrypoint_discovery.py` | entrypoint-discovery | core |
| `api_scan.py` | entrypoint-discovery | element |
| `flow_graph.py` | execution-flow-understanding | core |
| `experiment_runner.py` | variant-evaluation | core |
| `routes/project_intelligence.py` | repository-understanding | element |
| `routes/interview.py` | interactive-system-understanding | element |

### 検証手順

1. **Repository 設定**: Dashboard で probe-agent リポジトリを追加
2. **Snapshot 作成**: commit SHA を pin して snapshot を作成
3. **Build / Refresh**: System Understanding ページの Build ボタンを実行。
   Build は step 単位で orchestration される非同期ジョブ (Issue #109) として
   実行され、ページはジョブを polling して step ごとの進捗・エラー・
   retry / cancel 操作・artifact 件数を表示する。ブラウザを閉じても
   ジョブ状態は DB から復元される
4. **Pipeline 確認**: 決定的ステップ（symbols_indexed, entrypoints_discovered）が complete であることを確認
5. **Metadata coverage 確認**: `symbols_with_source_metadata` が 0 より大きいことを確認
6. **Capability Map 確認**: source-authored provenance で capability が表示されることを確認
7. **Gap worklist 確認**: unclassified entrypoint がない、または期待通りの gap が表示されることを確認
8. **ナビゲーション確認**: System Understanding → Capability Map → Flow Explorer の導線が機能することを確認

### 期待される結果

- 15 ファイルの module-level メタデータが symbol index に抽出される
- 各 capability（documentation-understanding, code-intelligence 等）が Capability Map に表示される
- API route 型の entrypoint が Flow Explorer で表示可能
- Gap worklist にメタデータ未付与の entrypoint が `unclassified_entrypoint` として表示される
