# probe-agent Vision・UX・コア機能要件

**文書の役割:** 製品方針と横断要件の正本
**対象:** 製品全体
**詳細仕様:** [`../01-specifications/`](../01-specifications/) 配下

## 1. Vision

`probe-agent` が目指すのは、ソフトウェアを静的なコードや一度きりの要約としてではなく、
**目的、利用者価値、設計、実装、実行時の現実、判断履歴がつながった、継続的に検証・改訂
できるシステム像**として扱うための基盤である。

人間が行いたい判断を起点に、AI と決定的な解析がコード、文書、設定、データ、運用情報、
runtime evidence を横断して調査する。得られた事実、推論、仮説、不明点、矛盾を区別し、
判断に必要な文脈と次の一手を提示する。さらに、改善案を隔離環境で生成・比較・評価し、
人間の明示判断を経て、安全にシステムの進化へつなげる。

```text
Stakeholder / Need / Problem
  -> Vision / System Purpose / Capability
  -> Product Objective / Milestone / Gap
  -> UX Journey / Requirement / Solution Design
  -> Flow / Evolution Node / Component / Probe Point
  -> Trace / Replay / Experiment / Outcome
  -> 新しい証拠と判断による理解・設計の改訂
```

この連鎖を単一の巨大モデルへ潰さず、それぞれの概念を独立した正本として保持し、参照と
lineage で接続することが製品の中心思想である。

## 2. 対象者と提供価値

主な利用者は、既存システムを理解し、変更の是非を判断し、安全に改善する責任を持つ
開発者、アーキテクト、運用担当者、プロダクト責任者である。

利用者が得る価値は次のとおり。

- 何のためのシステムで、誰のどの課題をどう変えようとしているかを説明できる。
- Vision から現在の Gap、必要な UX、要件、実装対象、評価証拠まで遡及できる。
- 「分からない」「調べていない」「古い」「矛盾している」を、欠落として隠さず判断材料にできる。
- コードで確認できる問いを人へ転嫁せず、人にしか決められない重要な問いへ集中できる。
- 改善案を本番変更から分離して比較し、根拠とリスクを見た上で採否を決められる。
- 新しい snapshot、回答、trace、評価結果に応じて理解と設計を継続的に改訂できる。

## 3. 実現する UX

### UX-1 判断を起点にする

利用者は内部機能の一覧から探索を始めるのではなく、「何を理解・判断したいか」と現在の
状態から始める。Overview は、現在分かっていること、判断を妨げる Gap、次の一操作を示す。

### UX-2 次の一操作を明確にする

各状態には主操作を一つ定める。複数画面が別々の「次の一歩」を再計算せず、server が返す
canonical projection を表示する。実行を伴う操作と、別画面への移動は明確に区別する。

### UX-3 段階的に詳しくする

最初は目的、現在地、主要な差分、次の判断を示し、証拠、履歴、診断、内部識別子は必要時に
展開する。情報を隠すのではなく、判断に適した順序にする。

### UX-4 根拠と不確実性へ戻れる

重要な claim、要件、提案、評価は、snapshot、source、trace、decision へ遡れる。
`unknown`、`unavailable`、`not_applicable`、`stale`、`contradicted` を同じ空状態へ丸めない。

### UX-5 AI と人間の責務を混同しない

AI は調査、説明、仮説、変更候補を提示できるが、確認、採用、適用、観測開始、publish、
達成・解消の確定は人間の明示判断として表示・記録する。提案生成と適用は必ず別操作にする。

### UX-6 文脈を保ったまま共同検討する

利用者は対象 entity、現在の画面状態、未保存 draft を文脈として AI と議論できる。
会話の結論は型付けされた変更候補としてレビューし、フォームや正本へ無断で反映しない。

### UX-7 失敗しても安全に再開できる

長時間処理、部分失敗、stale snapshot、権限不足では、失敗箇所、保持された成果、再開条件、
次に可能な操作を示す。診断情報の失敗を本処理の失敗へ拡大しない。

## 4. コア機能要件

### CAP-1 Evidence-backed System Understanding

- リポジトリ、コードシンボル、API、データ、設定、文書、テスト、運用定義、runtime trace を
  snapshot に固定された証拠として収集できること。
- 読んだ証拠だけでなく、対象外、解析失敗、予算による省略も coverage として記録すること。
- Context、Topology、Capability、Behavior、Data、Runtime、Operations、Trust の複数ビューを、
  共通の identity と evidence で接続できること。
- 追加調査を反復でき、探索予算、調査経路、終了理由を監査できること。

### CAP-2 Purpose and Stakeholder Value

- Stakeholder、Need、Problem、望ましい Change、System Intervention、Capability を別概念として
  表現し、関係を追跡できること。
- Value Exchange と環境上の制約を、金銭処理そのものとは分離して記述できること。
- 人間が確認した内容と AI が提案した内容を区別できること。

### CAP-3 Objective, Milestone, Gap, and Feature Lineage

- Vision へ寄与する Product Objective と、検証可能な Milestone を保持できること。
- 現在状態、目標状態、検出元、解釈、優先判断、解消状態を独立した軸として Gap を扱えること。
- Gap から UX Journey、Requirement、Feature、Solution、実装対象、評価証拠まで接続できること。
- trace や実装完了だけから Milestone 達成や Gap 解消を自動確定しないこと。

### CAP-4 UX and Requirement Design Lineage

- as-is / to-be Journey、Journey Step、Requirement、Acceptance Criterion、Solution Design、Design
  Option を revision 付きで管理できること。
- Capability、Flow、Evolution Node、Component など既存の正本を複製せず参照できること。
- 設計案の採用と、コードへの適用・本番反映を別の判断として扱うこと。

### CAP-5 Runtime Observation and Safe Evaluation

- component 単位で input、output、error、duration と lineage を観測できること。
- `off`、`trace`、`shadow` 等の実行モードを有限語彙と決定的な解決規則で制御できること。
- 固定 snapshot と承認済み入力を使い、候補を隔離環境で Replay / Simulation できること。
- baseline と候補を決定的な指標と明示された評価基準で比較し、結果を採否判断へ渡せること。

### CAP-6 Evolutionary Improvement Lifecycle

- 改善対象を安定した identity で管理し、contract version、implementation、maturity、monitoring
  を独立した状態として追跡できること。
- Design、Exploration、Stabilization、Establishment、Monitoring の証拠を接続できること。
- 自動的な置換や maturity 遷移を行わず、人間の gate を通すこと。

### CAP-7 Contextual AI Collaboration

- 画面上の対象と現在状態を特定した discussion thread を永続化できること。
- 対象種別ごとの有限 schema に従って会話から変更候補を生成できること。
- 未保存 UI draft は保存済み正本と provenance を分け、allowlist と size bound を適用すること。
- 未知の対象、capability、field、値は fail closed で拒否すること。

### CAP-8 Decision-oriented Projections and Navigation

- 各画面が独自に状態や次の操作を導出せず、server の canonical projection を利用すること。
- Vision から evidence までを俯瞰する表示と、各 entity の詳細へ進む drill-down を提供すること。
- 同じ概念に複数の表示名や状態語を割り当てず、利用者向け語彙を一貫させること。

## 5. 全機能に適用する横断要件

### REQ-1 正本を一つにする

各 entity と判定規則の owner を一つにする。他領域は内容をコピーせず、安定した identity、
参照、捕捉 digest で接続する。

### REQ-2 provenance と時点を失わない

source、snapshot、生成者、decision method、revision、staleness を独立した事実として保持する。
訂正は原則 append-only とし、過去に何を根拠に判断したかを再現できるようにする。

### REQ-3 決定的処理を優先する

構造抽出、状態解決、validation、比較、権限判定は可能な限り決定的に行う。LLM は意味解釈や
候補生成に限定し、出力を有限 schema で検証する。heuristic fallback で成功したように見せない。

### REQ-4 安全境界を保つ

対象 repository、本番環境、外部副作用から実験を隔離する。承認、適用、publish、実行モード変更、
秘密情報へのアクセスは明示的な権限と監査を必要とする。

### REQ-5 privacy と秘密情報を境界で保護する

秘密値は取り込み、永続化、表示、LLM、Replay の各境界で多層に redaction する。漏えいが判明した
場合に既存データを再走査・隔離・復旧できること。

### REQ-6 失敗と欠損を有限状態で表す

空値、自由文、合成 score に複数の意味を詰め込まない。部分失敗は section 単位で degrade し、
取得不能を対象不存在として扱わない。

### REQ-7 判断を自動化しすぎない

AI の confidence や trace 数だけで、価値、重要度、達成、採用、安全性を自動確定しない。
システムは判断材料と候補を整理し、責任を持つ人間が決められる状態を作る。

## 6. 非目標

- repository や文書を一度だけ要約する汎用チャットボット
- 本番コードを AI が無承認で自動置換・deploy する仕組み
- すべての概念を一つの confidence / readiness / priority score に畳む仕組み
- runtime trace だけからユーザー価値や Outcome 達成を断定する仕組み
- 既存の issue tracker、repository、CI/CD、監視基盤を無条件に置き換えること

## 7. 要件から詳細仕様への入口

| 要件領域 | 詳細仕様 |
| --- | --- |
| CAP-1, CAP-8 | [System Understanding Navigation](../01-specifications/ux/system-understanding-navigation.md)、[System Interview Workflow UX](../01-specifications/ux/system-interview-workflow-ux.md) |
| CAP-2 | [Purpose Chain](../01-specifications/product/purpose-chain.md)、[Stakeholder Value Network](../01-specifications/product/stakeholder-value-network.md) |
| CAP-3 | [Product Objective Lineage](../01-specifications/product/product-objective-lineage.md) |
| CAP-4 | [UX Design Lineage](../01-specifications/ux/ux-design-lineage.md) |
| CAP-5, CAP-6 | [Evolutionary Pipeline](../01-specifications/capabilities/evolutionary-pipeline.md)、[Execution Modes](../01-specifications/capabilities/execution-modes.md) |
| CAP-7 | [Assistant Discussion](../01-specifications/capabilities/assistant-discussion.md)、[AI Discussion Adapter](../01-specifications/capabilities/ai-discussion-adapter.md) |
| REQ-4, REQ-5 | [Platform Design](../01-specifications/platform/design.md)、[Secret Redaction](../01-specifications/platform/secret-redaction.md) |
