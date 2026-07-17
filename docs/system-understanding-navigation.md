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

**日英対応・初出併記の方針（Issue #266）**: この表の用語は英語の正式名称を
そのまま用語として使い、意味・説明文は日本語で書く（初出のみ併記、以降は
英語表記のみで通す）。Dashboard の UI コピー全般に適用される日本語統一方針
（`CLAUDE.md` の「Dashboard UI言語規約」、`.claude/skills/dashboard/SKILL.md`）
は、この用語表がすでに実践しているスタイル——固有名詞・技術識別子
（System / Trace / Replay / Experiment / Snapshot / Capability / Feature 等）
は英語のまま、周辺の説明・ボタン文言・見出しは日本語——を Dashboard 画面全体
に一般化したものであり、新しい語彙規則を導入するものではない。

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
| Experiments | baseline と source patch variants の隔離実行、比較、human decision。decision 保存後は `human_decision` の値に応じた Next step カードを表示する（Issue #259、後述） |
| Components | フロー後半の観測 + 運用の画面。選択した component の Traces / Shadow Results / Profile / Criteria をタブで表示し、mode（`off` / `trace` / `shadow`）を切り替える。`off` ↔ `trace` の切替は常に許可されるが、`shadow` への切替と AI Candidate Studio への遷移（「AIで別バージョンを作る」ボタン）は `trace_count === 0` の component では disabled になる（Issue #258。理由表示は `data-testid="component-zero-traces-reason"`）。`?component=<id>&trace=<id>` で Trace Lineage / analyzer 結果からの deep link を受ける |
| Simulation Workbench | 記録済み trace の captured input を、pinned snapshot 上のコード（baseline）または編集した候補コード（patch variants、最大 N 件）に対して独立した worktree で再実行し、有限集合の diff matrix（match / diff / candidate_error / error_to_success 等）で比較する（Issue #242 Phase A-D、#243-#246）。実行には component 単位の human replay 承認（`decision_method: manual`）が前提。`?replay_set_id=<id>` で対象 Replay Set を選択する |
| AI Candidate Studio | component（+ 任意で trace / Replay Set）を起点に、開発者が自然言語で改善目標を伝える会話から候補コード（immutable `CandidateVersion`）を生成し、Simulation Workbench と同じ隔離 Replay 基盤で評価する（Issue #252）。候補生成は reasoning-model の構造化提案＋決定的 splice→diff、Replay は Simulation Workbench の実行系（human 承認・worktree サンドボックス・diff matrix）をそのまま再利用しており、新しい判断・実行・比較経路は追加しない。`?component_id=`（新規セッション prefill）/ `?trace_id=` / `?replay_set_id=` / `?session_id=`（既存セッション再開） |
| GitHub | GitHub App 接続・repository connection・publish job（commit → push → PR）を管理する。publish は常に人による承認が前提で、push 先は毎回サーバー生成の `probe/` prefix ブランチのみ（default branch への直接 push・force push は不可）。Probe Planner のパッチ適用成功後や Experiments の `adopted` decision から遷移すると、GitHub App 設定済み＋connected connection が 1 件以上の場合に限り `?patch=<id>` 付きで Publish Jobs タブが選択され、対象パッチが検証済みの場合のみ prefill される（Issue #259） |
| Probe Patterns | 承認・適用済み instrumentation（probe pattern）の一覧、リリース前の除去パッチ生成・適用、最新 snapshot に対する reconciliation（構造的に一致 / moved / split / missing の判定は決定的チェックを優先し、判別できない場合のみ reasoning model。ヒューリスティックへのフォールバックはしない）を扱う（Issue #168）。他ページからのリンクは無く、サイドバー Instrument グループへの所属が唯一の到達経路（Issue #257） |
| Generate (Legacy) | component + trace + objective から単発の候補コードを生成する旧世代ページ。内部の実行基盤は Simulation Workbench / AI Candidate Studio と同じ共有 harness（`replay_harness.run_inline_candidate`、Issue #244 で移行済み）だが、会話・複数候補比較・immutable version 管理を持たない。AI Candidate Studio に機能面で置き換えられ、サイドバーで `Legacy` バッジ付き・Other グループ末尾に降格されている（Issue #257 決定 b）。ページ自体の機能変更・削除は行われていない |

### 観測・評価まわりの使い分け（Issue #260）

- **Components vs Simulation Workbench / AI Candidate Studio**: Components は
  「今のトラフィックを見る・mode を変える」観測 + 運用の入口である。個別
  trace はそこから Replay Set 化して Simulation Workbench に送る、または
  Candidate Studio セッションを開始できる。この行アクション
  （`components/replay-row-actions.tsx` の `ReplayRowActions`）は Components
  の Traces タブ・Trace Lineage・analyzer 結果で共通利用される。
- **Simulation Workbench vs AI Candidate Studio**: 両者は同じ Replay 実行系・
  diff matrix・Experiments への promote 導線を共有する。Workbench は開発者
  自身がソースを編集して diff を確認する手動フロー、Candidate Studio は
  自然言語の改善目標から reasoning model が patch を提案する会話フロー。
  Workbench の「会話で候補を改善」ボタンで Candidate Studio に切り替えら
  れ、どちらで承認した replay 承認も component 単位のため両ページで共有
  される。
- **Generate (Legacy) vs AI Candidate Studio**: 実行基盤（harness）は共通だ
  が、Generate は単発生成のみで会話・複数候補比較・promotion を持たない。
  新規に使う場合は AI Candidate Studio を使う。

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

CTA の出所は Issue #239 で `GET /system-state` に統一された: 最初の非
`complete` step に対応する `StateItem`（`related_pipeline_steps` がその
step 名を含む項目。`page_items["/system-understanding"]` はサーバー側で
優先度順・フェーズ抑制済み）の `target_ui` / `systemStateTarget()` を
そのまま消費する。これによりバッジ・バナー・notice と同じ root cause が
このページでだけ別文言・別遷移先になることがない。

`STEP_CTA` の固定マッピング（Issue #200）は撤去された。CTA は例外なく
`GET /system-state` の `StateItem` から導出され、ハードコードのフォール
バックは持たない。以前フォールバックに落ちていた唯一の step
`repository_configured` は、ネイティブ項目 `repository.configuration.missing`
が `related_pipeline_steps: ["repository_configured"]` を持つようになった
ため、他の step と同様 `stateItemForStep` でマッチする。ある step の
最初の非 `complete` 項目が万一欠けている場合は、その step には CTA
ボタンを出さない（status バッジと「Why?」診断は従来どおり表示）—
古い固定文言・固定遷移先を出すよりも一貫性を優先する。

CTA の文言・遷移先は該当 `StateItem` の `target_ui`
（`action_label` / `systemStateTarget()`）から取る。`user_action_kind ===
"build"` の項目だけはページの `Build / Refresh` ボタンと同じ
`build.mutate()` を起動し、ビルド実行中（`build.isPending` または最新ジョブが
`queued`/`running`）は無効化される。それ以外の項目は `target_ui.route`
への遷移リンクになる。CTA には `pipeline-cta-<step>` の `data-testid`
が付く。

Pipeline Checklist の各 step ラベルは Issue #240 でサーバー正本化された:
`GET /system-understanding` の各 step が返す `label`（`state_messages.PIPELINE_STEP_LABELS`
由来の日本語）を UI はそのまま表示し、クライアント側の `STEP_LABELS` は
サーバー文言欠落時（旧サーバー）のフォールバックとしてのみ残る。

全 step が `complete` のとき、Pipeline Status カードは既定で折りたたまれ
（`data-testid="pipeline-collapsed"`、`N/N steps complete` の要約表示）、
`pipeline-expand` / `pipeline-collapse` ボタンで切り替えられる（Issue #211）。
`warning` / `blocked` / `failed` / `missing` の step が 1 つでもあれば従来
どおり展開表示される。判定は step status の有限集合への分岐のみ。

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
| Components — Traces タブの行アクション「Replay」 | Simulation Workbench | trace を新規/既存 Replay Set に追加後 `?replay_set_id=<id>` で遷移（`ReplayRowActions`。Trace Lineage / analyzer 結果でも共通利用、Issue #242/#246） |
| Components — Traces タブの行アクション「この入力から改善する」 | AI Candidate Studio | `?component_id=<id>&trace_id=<id>` |
| Components — Traces タブの行アクション「Create Experiment」 | Experiments | `?from_trace=<id>&from_component=<id>`（patch を伴わないコンテキスト prefill のみ） |
| Components — ヘッダーの「AIで別バージョンを作る」 | AI Candidate Studio | `?component_id=<id>`（新規セッション prefill。`trace_count === 0` の component では disabled） |
| Simulation Workbench — 「会話で候補を改善」 | AI Candidate Studio | `?component_id=<id>&replay_set_id=<id>` |
| Simulation Workbench — variant の Promote | Experiments | `?replay_run_id=<id>&replay_variant_id=<id>`（patch payload はサーバー側で解決。`?draft=` と同じ prefill 機構、Issue #242 Phase D） |
| AI Candidate Studio — 「Experimentへ送る」（promote） | Experiments | `?replay_run_id=<id>&replay_variant_id=<id>`（Simulation Workbench の promote と同じ機構を再利用、Issue #252） |
| Probe Planner — パッチ apply 成功 | GitHub（Publish Jobs タブ） | `?patch=<id>`。GitHub App 設定済み **かつ** connected connection が 1 件以上のときのみ表示（`data-testid="patch-publish-link"`）。それ以外は手動 git 手順を表示する（`data-testid="patch-manual-git-instructions"`、Issue #259） |
| Experiments — decision `adopted` | GitHub | 遷移リンク（`data-testid="experiment-github-publish-link"`）。`?patch=` は付与されない（Publish Jobs タブで対象パッチを手動選択する）。githubPublishAvailable のときのみ表示 |
| Experiments — decision `adopted` | Probe Planner | 「次の probe サイクルを開始」（`data-testid="experiment-probe-planner-link"`）。`?capability=<key>` があれば引き継ぐ |
| Experiments — decision `rejected` / `needs_more_data` | AI Candidate Studio | 遷移リンクのみ（`data-testid="experiment-candidate-studio-link"`）。Experiment は `component_id` を持たないため prefill はしない |
| Overview — Get Started ステップ 4「View traces in Components」 | Components | `/components`。SDK 接続後にトレースを見に行く導線。connectivity `receiving` で完了マーク（`data-done`、Issue #259） |

`?capability=` は selection state ではなく navigation context である。Flow Explorer、
Probe Planner、Experiments はこの値を使って Context Header と back link を表示し、
Capability Map から始めた探索が Evaluate stage まで途切れないようにする。

### サイドバー: フェーズ連動グループ化（Issue #257）

サイドバー（`apps/dashboard/src/components/layout/sidebar.tsx`）は、Issue
#173/#179 が導入した固定見出し（Hub / Detail views / Other）から、
`GET /system-state` の `user_phase`（6 フェーズ）の軸に沿ったグループ化へ
再編された。**ルーティング自体（URL・ページ機能）は変更していない**。

| サイドバー見出し | 対応する `phases` | 画面 |
| --- | --- | --- |
| (見出しなし) | — | Overview |
| Setup | `setup` | Setup Guide, Repository, Settings |
| Understand | `preparation` | System Understanding, Capability Map, Feature Map, Flow Explorer, Interview |
| Instrument | `instrumentation` | Probe Planner, Probe Patterns, Connect SDK |
| Observe & Evaluate | `observation`, `evaluation`（1 グループが 2 フェーズを担当） | Components / Traces, Trace Lineage, Trace Analyzers, Experiments, Simulation Workbench, AI Candidate Studio, Decision Workspace |
| Publish | `publish` | GitHub |
| Other | — | Generate（`Legacy` バッジ付き）、(admin のみ) Admin |

- 現在の `user_phase` を含むグループの見出しには「現在」バッジが付き
  （`data-phase-state="current"`）、現在フェーズより後のグループは
  `opacity-50` で dimmed 表示される。dimmed でもクリックは常に可能で、
  遷移をブロックすることはない。`user_phase` が未取得（旧サーバー・
  loading・error）のときは全グループが通常表示される
  （`phaseGroupState` の `"none"` 分岐、Principle 6 の二段階ルールに沿っ
  た graceful degradation）。
- グループの `data-testid` は `sidebar-group-<見出しの小文字・空白→ハイ
  フン>`（例: `sidebar-group-setup`, `sidebar-group-observe-&-evaluate`）。
  Overview / Other は phase を持たないため `data-phase-state` は付かな
  い。
- Generate は削除・機能変更されず、`data-testid="sidebar-legacy-badge"`
  の `Legacy` バッジ + ツールチップ（AI Candidate Studio を推奨）付きで
  Other 末尾に降格された（Issue #257 決定 b）。Probe Patterns はページ自
  体を変更せず、Instrument グループへの所属で到達経路を確保している
  （他ページからのリンクは無い）。

**#189 との分担**: この「サイドバーのフェーズ連動グループ」は、ナビゲー
ション枠（サイドバー）の並び替えに閉じている。本 doc の「目標 UI 導線」
節が説明する System Understanding ページ**内部**の 4 stage Hub
（Understand / Decide Where to Observe / Instrument / Evaluate、Issue
#173/#179 の Hub 再編）とは別の軸であり、両者の見出し文言が偶然重なる
箇所があっても同じものではない。後者（Hub 再編に伴う本 doc の既存記述の
更新）は #189 のスコープであり、本 issue (#260) では扱わない。

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

## 状態通知の構成（Issue #239 で system-state に統一済み）

かつてはユーザー向けの「次の一歩」表示が 2 系統併存していた（Issue #215
調査、`docs/ux-gap-analysis-system-understanding.md` §2.4:
`_derive_primary_action` → `primary_action` → `PrimaryActionCard` と、
`select_primary_item` → `primary_item` → `SystemStateBanner`）。Issue #239
で全通知面のデータソースは `GET /system-state` のみに統一され、面ごとの
責務は次のとおり確定した:

| 通知面 | 消費する投影 |
| --- | --- |
| ページ内バナー（`SystemStateBanner`） | `page_items[currentRoute][0]`、なければ `primary_item`。System Understanding ではビルド実行中、severity が `error` / `blocked` 以外の項目を表示保留にする（BuildJobPanel が進捗を表示しているため。決定論的条件のみ） |
| 右上バッジ（`DiagnosticsBadge`） | `notification_items`（severity・scope・フェーズ抑制済み）をそのまま消費し、監査用 `items` から通知を再導出しない。`user_action_kind=none` / `wait` では CTA を表示しない。dialog-kind の canonical item に限り、`system-diagnostics` は `related_checks` 経由で EnvFixDialog の内容と実行手段だけを供給する。`system-state` 取得失敗時は独自導出へ回帰せず、専用の縮退表示（`?` バッジ + エラーダイアログ）を出す |
| 右下常駐 notice（`assistant-panel.tsx`） | `notification_items[0]`（フェーズ抑制済み） |
| Pipeline Checklist の CTA | 該当 step を `related_pipeline_steps` に持つ `StateItem` の `target_ui`（前節参照） |
| ヘッダーのフェーズ表示（`UserPhaseIndicator`） | `user_phase` / `phases`（サーバー値のみ。クライアント側でフェーズを導出しない） |

通知の取り下げは (a) 状態解消による項目消滅と (b) フェーズ抑制のみで
行われ、ユーザー操作による dismiss フラグは存在しない（親 issue #235 の
確定事項）。

`StateItem.target_ui` は修正を実行する画面を表す。一方、問題を観測する
画面は `display_routes` で明示し、`page_items` には
`display_routes ∪ {target_ui.route}` を投影する（Issue #231）。現時点で
観測/修正が異なる item は次の有限集合であり、推測による自動付与はしない。

| StateItem | 観測画面 (`display_routes`) | 修正画面 (`target_ui.route`) |
| --- | --- | --- |
| `understanding.purpose.diff_impacted` / `.unconfirmed` / `.missing_baseline` | `/system-understanding` | `/interview` |
| `understanding.capabilities.diff_impacted` / `.unconfirmed` / `.missing_baseline` | `/system-understanding` | `/interview` |
| `snapshot.latest.stale_for_interview` | `/system-understanding` | `/interview` |
| `pipeline.capability_hierarchy.empty` | `/system-understanding` | `/interview`（session query 付きの場合を含む） |

前者は現 snapshot のみを参照し、後者は Interview baseline の snapshot
跨ぎ再利用に対応するなど、判定材料が完全には一致しない。統合（前者を
後者へ吸収し、Hub の表示も canonical `StateItem` から投影する）は
Issue #235 の Sub 3（#238）/ Sub 4（#239）が所有する（旧 #206 / #207 は
#235 に集約して閉じられた）。それまでの間、両系統の判定条件を変える
変更は必ず両方（`system_understanding_service` / `system_state` /
`system_diagnostics`）へ同時に適用する（Issue #210 の
capability_hierarchy 0 件 warning が先例）。

Issue #236 はこの「両系統」の事実取得を `app/state_facts.py` に一本化し、
`system_understanding_service._build_next_actions` / `_derive_stage_statuses`
が独自に持っていた単純な `purpose_defined` 判定（`_load_purpose` の dict
から `bool(name or summary)` を取る簡易版）を、`system_state.evaluate_understanding`
の 5 分岐（`satisfied_current | baseline_reusable | diff_impacted |
unconfirmed | missing_baseline`）の `kind == "satisfied_current"` への
縮約に置き換えた（`_purpose_defined_from_understanding_status`）。これに
より「現 snapshot で Purpose/Capabilities が定義されているか」の一次判定
は 3 モジュールで完全に共有される。ただし `_derive_primary_action` /
`_build_next_actions` 自体（baseline 再利用や diff_impacted を経路に含め
た出し分け）を `StateItem` の投影に置き換える統合は Issue #235 の
Sub 3（#238）の領分であり、#236 は対象外。

### primary_item への統合（Issue #238）— 旧フィールドは #239 で撤去済み

「次の一歩」の正本は `system_state.select_primary_item` が返す
`GET /system-state` の `primary_item` である。`GET /repository/system-understanding`
の `primary_action` / `next_actions` / `understanding_refresh_recommended`
は Issue #239 でレスポンス・型定義・UI から**撤去済み**
（`_derive_primary_action` / `_build_next_actions` も削除。
`_check_understanding_refresh_recommended` は
`interview.materialized.rebuild_required` state 項目の導出用として存続）。
クライアントは `GET /system-state` の `primary_item` / `page_items` を
参照すること。旧フィールドを含む古いレスポンスを受け取っても UI が旧投影
を復活させないことは契約テストで固定されている。

`_derive_primary_action` / `_build_next_actions` が考慮していた判定要素の
うち、`StateItem` として表現されていなかったものを Issue #238 で吸収した:

| 判定要素（旧: `_build_next_actions` / `_derive_primary_action`） | 対応する StateItem |
| --- | --- |
| repository 未設定・snapshot 未 ready（rule 1） | `repository.configuration.missing` / `snapshot.ready.*`（既存） |
| ビルド実行中は CTA を出さない（rule 2） | 新規追加なし。実行中のステップは既存の `.running`（`user_action_kind="wait"`）が `select_primary_item` の候補から除外される。ただし旧 rule 2 はビルド状態に関係なく無条件に CTA 全体を抑制するのに対し、新方式はビルドと無関係な項目（例: レビュー待ち probe plan）までは抑制しない — 既知の意図的な差分（後述） |
| symbols_indexed 等の未完了ステップ（rule 3 / 個別 NextAction） | `pipeline.symbol_index.*` / `pipeline.entrypoint_index.*` / `pipeline.documentation_index.*` / `pipeline.capability_hierarchy.*`（既存） |
| documentation_claims_scanned 未完了 | 既存の `diagnostic.pipeline_understanding_graph`（診断投影、`understanding_graph_snapshots` の有無を reasoning 要求付きでチェック）が同一条件を既にカバーしている。ネイティブ項目は追加していない |
| docs_code_reconciled 未完了（has_understanding_graph **かつ** has_code_symbols） | 新規 `pipeline.docs_code_reconcile.not_run` / `.partial`。既存の `diagnostic.pipeline_understanding_graph` は graph の有無のみを見るため、symbol 側の欠落を拾えない差分をネイティブ項目で埋めた |
| purpose 未定義 / capabilities 空（rule 3 の pipeline_complete 分岐） | `understanding.purpose.*` / `understanding.capabilities.*`（既存） |
| "Review probe plan"（proposed_plan_ids） | 新規 `proposal.probe_plans.proposed`（策定時点は `phase="preparation"` を明示上書き — 承認が preparation 完了条件の OR 経路の一つだったため。Issue #256 でその OR 経路自体が `instrumentation` へ移り、上書き先も `instrumentation` に更新された。現在の値は前節「user_phase とフェーズ抑制」参照） |
| "Generate / validate probe patch"（approved_plan_ids_without_validated_patch） | 新規 `proposal.probe_plans.approved_without_patch`（策定時点は `state_group="proposal"` の既定どおり `phase="diagnosis"`。Issue #256 で明示上書き `instrumentation` に変更 — 計装作業そのものであるため。現在の値は前節参照） |
| "Review experiment decision"（undecided_completed_experiment_ids） | `proposal.experiments.undecided`（既存、Issue #237） |
| 全完了時の "Start from Capability" 等の探索導線（rule 4 のフォールバック） | 対応する StateItem を追加しない（意図的）。`select_primary_item` は `severity != "ok"` の項目のみを候補とするため、問題が無ければ `primary_item = None` になる。旧系の「探索を促す」導線と新系の「沈黙」は意味的に異なる（前者は次にやることの提案、後者は「今は何も直すことがない」の表明）ため、統合対象は前者ではなく後者を正とする |

新旧一致の契約テストは
`apps/control-server/tests/test_next_step_parity.py`
（`TestPrimaryRecommendationParity` / `TestBuildRunningSuppression` /
`TestUnderstandingRefreshRecommendedMatchesStateItem`）にある。repository
未設定・snapshot 未 ready・単一ステップ未完了・purpose 未定義・probe plan
レビュー待ち・approved plan の patch 未生成の各代表ケースで、新旧が同じ
修正先ルートを指すことを固定している。全完了時と build 実行中時は、上表
のとおり意図的な差分があるため厳密な一致ではなく期待される挙動として
固定している。`understanding_refresh_recommended` は
`interview.materialized.rebuild_required` StateItem の存在と常に一致する
ことも同ファイルで固定した（両者とも `_check_understanding_refresh_recommended`
という同一関数を読んでいるため、構造的に一致する）。

### user_phase とフェーズ抑制（Issue #237; Issue #256 で 6 フェーズへ拡張）

`GET /system-state` は上記 `StateItem` 一覧に加えて、ユーザーが今どの
段階にいるかを表す `user_phase` と、各フェーズの完了可否
`phases: [{phase, complete, label}]` を返す。Issue #256 で、旧
`"setup" | "preparation" | "diagnosis"` の 3 フェーズから、計装・観測・
評価・公開までを表す 6 フェーズへ拡張された（旧終端フェーズ `diagnosis`
は撤去）。

| フェーズ | ラベル（`state_messages.phase_label`） | 完了条件 |
| --- | --- | --- |
| `setup` | 必要最低限の設定 | 対象リポジトリ登録済み、かつ repository / database / auth / llm 系診断（`system_diagnostics.DiagnosticCheck.category`）に `error` / `blocked` が無い |
| `preparation` | 分析準備 | setup 完了、かつ ready snapshot 存在、かつ決定的 8 ステップ Pipeline Checklist（`system_understanding_service.compute_pipeline_steps` — repository_configured / snapshot_ready / documentation_indexed / documentation_claims_scanned / symbols_indexed / entrypoints_discovered / docs_code_reconciled / capability_hierarchy_ready）が全て `complete`、かつ Purpose / Capabilities が `evaluate_understanding` の `satisfied_current` または `baseline_reusable`。Issue #256 で計装経路確立の OR 条件は `instrumentation` フェーズへ移り、preparation 自体の完了条件からは外れた |
| `instrumentation` | プローブ設定 | preparation 完了、かつ（適用済み probe patch が存在 **または** 承認済み probe plan が 1 件以上 **または** SDK 接続状態が `no_signal` でない）のいずれか |
| `observation` | 観測 | instrumentation 完了、かつ SDK 接続状態が実際に `receiving`（`no_signal` はもちろん `smoke_only` でも満たさない） |
| `evaluation` | 候補評価 | observation 完了、かつ（human_decision が記録された experiment が 1 件以上 **または** 完了した replay variant run が 1 件以上） |
| `publish` | 公開 | evaluation 完了、かつ publish job が `completed`。全フェーズ充足時の表示上の終端フェーズでもある（この場合 `user_phase` は `"publish"` を返すが、`phases[-1].complete` 自体は publish job が実際に成功するまで `false` のまま） |

現在フェーズ = 完了条件を満たさない最初のフェーズ（全て満たせば表示上の
終端 `"publish"`）。導出は `system_state.derive_user_phase(facts:
UserPhaseFacts)` という DB 非依存の純粋関数が担う（`build_system_state`
が `state_facts` / diagnostics から facts を集めて渡す）。入力が不明な場
合は常に前のフェーズに倒れる（`UserPhaseFacts` の全フィールドが「未達
成」側をデフォルトにしているため）。

**`phases[].complete` は「一度でも到達したか」を表す一方向の marker**
であり、「今それをやっている最中か」ではない。`observation` /
`evaluation` は運用上サイクリック（トレースは来続け、実験は積み重なる）
だが、一度 `true` になった `complete` が後から `false` に戻ることはな
い。`user_phase` は「次に到達すべき最初の未達成マイルストーン」を指す進
捗ポインタであり、「今この瞬間何をしているか」のライブ指標ではない。

各 `StateItem` は `phase` フィールドを持つ。既定は `state_group` → フェー
ズの固定マッピング（`repository` / `configuration` → `setup`、
`snapshot` / `pipeline` / `understanding` / `interview` → `preparation`、
`runtime` → `observation`、`proposal` → `evaluation`。Issue #256 が
`runtime` / `proposal` の既定先を、両グループが共有していた旧終端
`diagnosis` から新設の `observation` / `evaluation` へ付け替えた）だが、
`system_state.STATE_ID_PHASE_OVERRIDES` という小さな明示的辞書がこれを
上書きする場合がある。例えば `runtime.connectivity.no_signal`
（state_group は `runtime`）は SDK 接続確立が `instrumentation` の OR
条件の一つであるため `instrumentation` タグになる（Issue #256 で
`preparation` から付け替え——preparation はもう独自の OR 条件を持たな
い）。同様に `proposal.probe_plans.proposed` /
`proposal.probe_plans.approved_without_patch`（probe plan のレビュー待
ち・承認済みだが patch 未生成）も `proposal` の既定 `evaluation` ではな
く `instrumentation` に上書きされている（計装作業そのものであるため）。
診断由来の `StateItem`（`diagnostic.<check_id>`）は `_diagnostic_state_item`
が auth/database/llm/configuration 以外のカテゴリを一律 `state_group=
"runtime"` に畳み込む（Issue #193）ため、repository / pipeline /
understanding カテゴリの check だけは診断カテゴリに合わせて
`setup` / `preparation` に個別上書きしている。

フェーズ抑制（親 issue #235 の確定取り下げ規則）は通知投影の全面——
`primary_item` / `notification_items` / `page_items`——に適用され、現在
フェーズより後のフェーズの項目を除外する。フェーズスコープは確定優先度
規則（フェーズ → severity → intervention_timing → user_action_kind →
state_id）の最外殻であり、`primary_item` の選択もスコープ内で行われる。
`items`（監査用の全項目）は一切除外しない。したがって
`LLM_PROVIDER=mock`（Principle 7 のテスト/ローカル動作確認用データ）の
ように `llm` 系診断が `blocked` になる構成ではフェーズが `setup` に留ま
り、ページバナーには後フェーズの pipeline/understanding 項目ではなく
setup の解消案内が出る——これは設計どおりで、後フェーズの事実は `items`
に残り、前提未達ページへのフェーズ由来ガイドは Issue #241 が担う。

`runtime` / `proposal` グループは Issue #193 Phase 1 では宣言のみで未使用
だったが、Issue #237 で代表項目を 1 件ずつ追加した（網羅は狙わない）:
`runtime.connectivity.no_signal`（トレース未受信時の計装案内、上記のとお
り `instrumentation` タグ）と `proposal.experiments.undecided`（完了済み
だが human_decision 未記録の experiment のレビュー促し、`evaluation` タ
グ——Issue #256 で旧 `diagnosis` から付け替え）。

### 状態メッセージのカタログ化と日本語統一（Issue #240）

状態メッセージ（summary / detail / impact / remediation / action_label、
pipeline step / stage の表示名、gap のタイトル/next-action、成功サマリ）は
サーバー側の単一カタログ `app/state_messages.py` に集約され、表示言語は
日本語に統一されている。`system_state.py` / `system_diagnostics.py` /
`system_understanding_service.py` はこのカタログから文言を引き、モジュール
内に f-string の文言を持たない。

- カタログのキーは `state_id` / `check_id`（および必要な variant）を正と
  する。動的埋め込みは件数・snapshot id・raw な upstream status/error など
  の事実値のみ（`str.format` の名前付きパラメータ、Principle 6）。LLM に
  よる文言生成はしない。
- アクセサ（`state_message` / `pipeline_family_message` /
  `understanding_message` / `check_title` / `check_message` /
  `shared_check_message` / `stage_message` / `pipeline_step_detail` 等）は
  キー欠落時に `KeyError` を送出し、黙って英語/空文字へフォールバックし
  ない。`phase_label` のみ、サーバー検証済み enum のため未知値でトークンを
  返す（英語化はしない）。
- 新しい `StateItem` / `DiagnosticCheck` / pipeline step / stage を追加する
  ときは、対応するカタログキーを同時に追加する。欠落は
  `tests/test_state_messages.py`（全 `ALL_*` キーの解決検証 + 実プロデューサ
  を駆動した網羅検証 + 代表文言スナップショット）が検出する。
- Dashboard は状態文言を生成しない。stage ラベル/説明・成功サマリ
  （`SystemUnderstandingOut.success_summary`）・フェーズ表示ラベル
  （`SystemStatePhaseCompletion.label`, Issue #240）・pipeline step ラベル
  （`SystemUnderstandingPipelineStep.label`, Issue #240）・gap
  アクションはサーバー供給値を消費し、`STAGE_LABELS` /
  `USER_PHASE_LABELS` / `STEP_LABELS` 等の固定ラベルはサーバー文言欠落時の
  最終手段フォールバックとしてのみ残す（`STEP_CTA` の固定 CTA マップは
  Issue #239 で撤去済み — CTA は `StateItem` からのみ導出）。gap の「実装 issue を作成」
  アクションはラベル一致で識別するため、フロントの `CREATE_ISSUE_ACTION`
  はカタログの `GAP_CREATE_ISSUE_ACTION` と一致させる。

### フェーズ由来の前提ガイド（Issue #241）

前提が満たされていない画面に「なぜ空か・次にどこで何をするか」を示す共通
コンポーネント `PrerequisiteGuide`（`components/prerequisite-guide.tsx`）を
追加した。データソースは `GET /system-state` のみ:

- 現在フェーズ（`user_phase`）を `USER_PHASE_LABELS` で表示し、フェーズを
  進めるための「次の一歩」はフェーズ抑制済みの `primary_item`（サーバー
  計算の最上位 actionable 項目）の summary / remediation / target_ui を
  そのまま消費する。クライアントはフェーズも状態文言も導出しない。
- Issue #256 で `user_phase` が 6 フェーズへ拡張された後も、このガイドが
  描画するのは `setup` / `preparation` のときだけである
  （`SUPPORTED_PREREQUISITE_PHASES`、単一の終端フェーズ判定ではなく明示的
  な有限集合へのメンバーシップ判定）。`instrumentation` 以降（計装・観測・
  評価・公開）は setup/preparation の前提が定義上すでに満たされているため
  何も描画しない——フェーズが進むと自動的に消える。後続フェーズ向けの
  ガイド UX は #257/#258 が別途担当する。
- 配置: Overview の zero-state（コンポーネント 0 件）、Feature Map の
  features 空状態、Probe Planner の生成ダイアログ（準備未完了時）。呼び出
  し側はフェーズを判定せず常にマウントし、上記のフェーズ判定はガイド内部
  のみで行う。Probe Planner のゲートは導線であり強制ブロックではない
  （自由入力 feature id は既定折りたたみの escape hatch、プラン生成 API は
  拒否しない）。強制が必要になった場合はサーバー側バリデーションとして
  別途起票する。
- Connect SDK ↔ Setup Guide は双方向リンク（`connect-sdk-setup-guide-link`
  ／ `setup-guide-connect-sdk-link`）で相互遷移できる。
- 表示分岐は `user_phase` ・ `phases[].complete` ・既存 API の有無
  （component 件数 0、features 空 等）の決定論的判定のみ（Principle 6）。

## Next Actions

> **撤去済み（Issue #238 → #239）**: 本節が説明していたトップレベルの
> `next_actions` / `primary_action` / `understanding_refresh_recommended`
> は Issue #239 で `GET /repository/system-understanding` のレスポンスから
> 撤去された。「次の一歩」の正本は `GET /system-state` の
> `primary_item`（`system_state.select_primary_item`）。判定要素の対応表
> は「primary_item への統合（Issue #238）」節を参照。以下の 4 stage 分類
> は表示専用の `stages` と gap 単位の解決手段リンク（`GAP_NEXT_ACTIONS`、
> gap card / issue draft で使用）の語彙として存続する。

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

### Primary Action（Issue #201、Issue #239 で撤去済み）

> `primary_action` は Issue #238 で `system_state.primary_item` に統合され、
> Issue #239 でレスポンスから撤去された（`_derive_primary_action` も削除）。
> 以下は統合時に吸収した意味論の歴史的記録として残す。旧 rule 2（ビルド
> 実行中の無条件 CTA 抑制）は、System Understanding ページのバナー側で
> 「ビルド実行中は severity が `error` / `blocked` 以外の項目を表示保留」
> という決定論的表示条件として引き継がれた。

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

pipeline 全 step が `complete` かつ build 非実行のとき、primary action
カードは成功サマリ（`data-testid="build-success-summary"`、
`Analysis complete — N/N steps · X symbols · Y entrypoints`。件数は
`metadata_coverage` 由来）を CTA の上に表示し、ヘッダーの Build / Refresh
ボタンは `outline` variant に降格する（Issue #211）。同条件で System
Purpose 未定義の場合、Start from Capability / Feature カードには前提の
注記（`data-testid="entry-cards-prereq-note"`）が付く。

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

### 改善ループ: Interview → Build / Refresh → gap trend（Issue #203）

Build → gap 確認 → Interview で修正 → 再 Build → gap 減少、という改善サイクル
の「戻り」を Hub 上で可視化する。ここで扱うのは gap の**件数**の履歴のみで、
gap の中身（title/evidence 等）は従来どおり毎回 `_collect_gaps` /
`_compute_gap_summary` で再計算される（履歴化されない）。

#### gap 件数履歴テーブル

`system_understanding_gap_history`（`apps/control-server/app/db.py`、この
issue が所有する唯一の新規テーブル）は 1 行が「ある build のある
gap_type の件数」を表す。

```sql
CREATE TABLE IF NOT EXISTS system_understanding_gap_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id   INTEGER NOT NULL,
    snapshot_id INTEGER,
    build_id    INTEGER NOT NULL,
    gap_type    TEXT NOT NULL,
    count       INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (build_id) REFERENCES system_understanding_builds (id) ON DELETE CASCADE
);
```

書き込みは `apps/control-server/app/system_understanding_jobs.py` の
`_finalize_job` が build job を `completed` または `partial` に確定させた
直後（同じ `get_conn()` ブロック内、既存の build 更新 UPDATE の後）に
`_record_gap_history` を呼んで行う。`failed` / `cancelled` では書き込まない。
gap 集計は read パスと全く同じ関数（`_load_gaps_from_reconciler` +
`_compute_gap_summary`）を再利用し、独自の集計ロジックは持たない。ある
gap_type の件数が 0 の build は、その gap_type の行を書かない（「行が無い」
=「0 件」として trend 読み出し側と解釈を揃える。ダミー行は作らない）。

#### gap_trend（トレンド比較）

`GET /system-understanding` は `gap_trend`（`{gap_type, current, previous}`
の配列）を返す。導出は `system_understanding_service._load_gap_trend` で、
同一 system 内で `system_understanding_gap_history` に記録された
**直近 2 つの build_id**（`DISTINCT build_id ORDER BY build_id DESC LIMIT 2`）
を比較する。snapshot をまたいでも構わない（同一 system 内の直前 build との
比較であり、snapshot の異同は問わない）。

- 両方の build に存在する gap_type: `previous`/`current` はそれぞれの件数
- 古い build にのみ存在した gap_type（解消）: `current = 0`
- 新しい build にのみ存在した gap_type（新出）: `previous = 0`
- 履歴のある build が 1 つ以下の system は `gap_trend = []`

`gap_trend` は timestamp 比較と件数集計のみの deterministic な結果であり
（Principle 6）、reasoning model は関与しない。

#### understanding_refresh_recommended（再 Build 促し、Issue #238 により deprecated）

`GET /system-understanding` は `understanding_refresh_recommended`
（bool）も返す。導出は
`system_understanding_service._check_understanding_refresh_recommended` で、
同一 system の `interview_session.materialized_at` の最大値が、最新の
`status = 'completed'` build の `completed_at` より新しいときに `true` に
なる。materialize 済みセッションが無い、または `completed` build が一度も
無い system は `false`。単純な timestamp 比較のみで reasoning model は
関与しない。

Issue #238: このフラグの正本は `system_state.py` の
`interview.materialized.rebuild_required` StateItem であり、両者は同じ
`_check_understanding_refresh_recommended` 呼び出しから導出されるため常に
一致する（`tests/test_next_step_parity.py` の
`TestUnderstandingRefreshRecommendedMatchesStateItem` で固定）。フラグ自体は
Dashboard の消費切替（#239）と同一コミットでレスポンスから撤去される。

#### Dashboard

- `apps/dashboard/src/pages/system-understanding.tsx`: `understanding_refresh_recommended`
  が `true` のときヘッダー直下・`PrimaryActionCard` の近くに
  `data-testid="refresh-recommended-banner"` のバナーを表示する。CTA
  （`data-testid="refresh-recommended-cta"`）は既存の Build / Refresh
  ボタンと同じ `build.mutate()` を呼ぶ。build 実行中（`buildRunning`）は
  バナーごと非表示になる。
- `apps/dashboard/src/components/system-understanding/gap-worklist.tsx`:
  `gapTrend` prop（optional）を受け取り、ヘッダー付近に
  `data-testid="gap-trend"` として gap_type ごとの `previous → current`
  を表示する。件数が減少（`current < previous`）した gap_type は視覚的に
  ポジティブな配色にする。`gapTrend` が空または未指定のときは何も描画しない
  （既存レスポンス/フィクスチャとの後方互換）。

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
