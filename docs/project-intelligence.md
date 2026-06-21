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
- 対象リポジトリへ probe や改善 patch を自動適用しない。
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

Control Server が読み取れる repository は `PROBE_REPOSITORY_ROOTS` 配下に限定する。
Docker Compose では `PROBE_REPOSITORY_HOST_ROOT` を `/repositories` へ read-only
mount し、Dashboard には container 側の path を設定する。

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

## リポジトリ設定案

設定例は [`probe-agent.example.yml`](../probe-agent.example.yml) を参照する。
実行コマンドは自動推測せず、この設定で明示する。
