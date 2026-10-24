# 課題の原因分類と語彙

[入口](README.md) · [辞書](dictionary.md) · [索引](index.md)

**役割:** 課題ナレッジの運用契約。**状態:** current。**確認時点:** 2026-10-04。
製品状態の評価軸は [状態評価](../../04-operations/improvement-cycle/state-assessment.md) が所有する。
ここでは原因の性質を記述し、製品の良し悪しや修正優先順位を点数化しない。

## 1. 座標を決める

1. 症状から遡り、原因を一文で書く。原因が分からなければ仮説と確認方法を書く。
2. 各軸について「この性質を変えなければ再発するか」を問う。場所・修正行数・修正手段で分類しない。
3. 処理は単一処理の不良、構造は表現・責務の設計、接続は段階間の受け渡し、統制は判断と実行の手続を見る。
4. 各軸は1〜2値。見て該当しないなら単独の `none`、まだ判断できないなら単独の `unknown`。
   複数軸に値を持ってよい。`none` と `unknown` を他の値と混ぜない。
5. 軸別に `high`（直接の根拠あり）/ `medium`（境界で迷う）/ `low`（材料不足）を記録。
   `unknown` に `high` は付けない。各軸の根拠は `basis` に、該当なしの理由も残す。

群は保存せず導出する。unknown があれば「未判定」、構造・接続・統制に値があれば
「構造・接続・統制」、それらが none で処理に値があれば「局所」。全軸 none は課題の再検討が必要。
座標は原因の記述であり、どの解決策を選ぶかは `resolution` に分ける。

## 2. 軸の値

| 軸 | 値 | 区別する原因 |
| --- | --- | --- |
| processing | `input_handling` | 正当な契約内での空・重複・境界値の扱い |
| processing | `logic` | 条件式・計算・分岐の誤り |
| processing | `resource` | 接続・ファイル・例外の後始末 |
| processing | `wording` | 挙動は正しいが表示文言が誤る |
| processing | `regression` | 以前正しかった単一処理の退行 |
| structure | `representation` | ID・型・状態が必要な区別を表せない |
| structure | `responsibility` | 判断・実行の責務の配置が不適切 |
| structure | `decomposition` | 分割粒度が不適切で責務が混在 |
| structure | `aggregation` | 正本・同型処理・語彙が分散 |
| connection | `information` | 段階間で情報が落ちる |
| connection | `meaning` | 同じ値の意味・単位が前後で異なる |
| connection | `condition` | 権限・実行条件・前提が後段に届かない |
| connection | `target` | System・対象・セッションを取り違える |
| connection | `version` | snapshot・revision・キャッシュ世代がずれる |
| connection | `contract` | 前段の出力と後段の入力契約が両立しない |
| governance | `ordering` | 実行・判定・記帳の順序 |
| governance | `budget` | 時間・回数・費用の制限 |
| governance | `resume` | 停止・再開・再実行・冪等性 |
| governance | `assignment` | 人・AI・システムの判断担当 |
| governance | `review` | 確認・承認の手続と記録 |
| governance | `completion` | 完了条件・済みとする証拠 |

### 境界で迷うとき

- 単なる誤表示は processing.wording、段階ごとに同じ語を別解釈するなら connection.meaning。
- 版を表せないデータ設計は structure.representation、表せる版を運ばないなら connection.version。
- 状態の正本が分散するのは structure.aggregation、何を完了と呼ぶかが不適切なら governance.completion。
- 前提の伝達漏れは connection.condition、確認後に前提が変わるのに保存前に再確認しないなら governance.ordering。
- 前後の契約が違えば connection.contract、契約が正しく局所の条件式が違えば processing.logic。

境界の相手は末尾の `neighbors` の無向ペアで管理する。全ての組合せを網羅する表ではなく、
分類で実際に迷った組を history の理由とともに追加する。

## 3. 原因・分類・課題の状態を分離する

| フィールド | 値と条件 |
| --- | --- |
| cause_status | confirmed = 対象版のコード・再現・証拠から原因を確認。hypothesis = 仮説。unknown があれば必ず hypothesis |
| review | candidate = 一次分類。confirmed = 担当が値・根拠・型を証拠と照合し reviewed_by / reviewed_at を記録（エージェント可） |
| status | open = 未解決、resolved = 対象範囲で解消証拠あり、deferred = 条件待ち、rejected = 課題不成立・意図した設計 |
| generalization.level | instance = 個別、repo_pattern = リポジトリ内で再利用、general = 機能名を外して説明可能 |

新規分類は candidate として直ちに登録・利用できる。人の分類確認を待たない。
担当エージェントが対象版の証拠・隣接型との違いを照合したら、確認者・日付とhistoryに根拠を残し
confirmedへ変更できる。分類の確認は解決策の効果確認とは別であり、候補の実適用を妨げない。
知識を実案件で使って効果・無効・逆効果を観測し、担当が分類・適用条件・手順を自律更新する。
人の介入は、顕在化した問題を自律改善できていないと人が発見した場合に限る。
登録・確認・効果評価に人の承認を要求しない。製品のmanual gateや外部実行の権限は別契約。
仮説の basis は「仮説:」で始め、確定に必要な確認を書く。resolved は resolved_at、着地先、
検証証拠が必要。文書の不具合なら文書への着地も有効だが、機能修正を設計だけで解決済みにしない。
過去の resolved は対象版での解消であり、現行版の健全性を保証しない。`observed_at` と
`target_revision` を sources とセットで保持する。移入日を修正日・観測日に使わない。

## 4. 発見・解決の観点

各1〜2値、先頭が主観点。発見者ではなく、何と何を照合したかを書く。
未解決は resolution を `[pending]`、保留は `[deferred_decision]`。
`guardrail_fix` は主観点にせず、原因を解いた見立てに添える。

| 発見値 | 観点 |
| --- | --- |
| `symptom_report` | 利用者の症状から遡る |
| `invariant_audit` | 不変条件と実装を照合 |
| `boundary_walk` | 機能の境界を歩く |
| `doc_code_diff` | 文書とコードを照合 |
| `data_inspection` | 実データ・ログを読む |
| `trace_walk` | 入力から出力まで値を追う |
| `adversarial_review` | 壊れる条件を意図的に探す |
| `inventory` | 経路・状態・語彙を棚卸し |
| `guardrail_failure` | 既存検査の失敗から遡る |
| `reproduction` | 手順を固定して再現 |
| `external_constraint` | 外部サービス等の制約から逆算 |

| 解決値 | 観点 |
| --- | --- |
| `single_point_fix` | 単一処理を修正 |
| `canonical_source` | 正本を一本化して委譲 |
| `explicit_contract` | 暗黙の契約を宣言 |
| `required_argument` | 条件・対象を必須引数にする |
| `vocabulary_table` | 語彙を有限集合にする |
| `first_class_state` | 暗黙の状態を明示的なデータにする |
| `representation_change` | ID・型・情報表現を変更 |
| `responsibility_move` | 判断・実行の担当を移す |
| `carry_through` | 情報・条件・版を後段まで運ぶ |
| `fail_closed` | 判断不能時に適用を止める |
| `state_transition` | 上書き・削除を状態遷移にする |
| `order_and_budget` | 順序・予算・再開条件を制御 |
| `guardrail_fix` | 別の解決観点に再発防止検査を添える |
| `doc_correction` | 文書の誤りを訂正 |
| `deferred_decision` | 判断・観測を待つ |
| `pending` | 解決策は未確定 |

## 5. 辞書・関係・変更履歴

辞書は族（原因の近い領域）→型（見分けられる一般形）の2段。型の典型座標は拘束ではない。
機能名・ファイル名は feature_context、型は機能名を含まない一文にする。
参照エントリがない型は作らない。証拠に基づく分類確認がある独立原因2件以上で型を「成立」とし、
それ未満は「暫定」。同一原因を `view_of` で束ねた件数は1件として数える。
`related` は関連、`view_of` は同一原因の別視点。関係はIDで記録し、自己参照は不可。
分類・確度・状態の変更は history に date / field / from / to / reason を追記する。
根拠が変わった分類は candidate に戻し reviewed_by / reviewed_at を null にする。

### 5.1 課題と解決原理の一般化候補

2026-09-27から[ローカル一般化候補](../../../improvement/local/patterns/README.md)を手動試行する。
本節が関係・成熟・改訂の規則を所有し、各候補が抽象課題と条件付きの解決原理を所有する。
既存の4軸、族→型、IKの`pattern`と`resolution.perspective`は維持する。

- 抽象課題は状況・保持条件・破綻の仕組みで定義する。座標は検索の手掛かりであり、所属の強制条件ではない。
- 候補IDは`probe-agent:EC-LP-NNNN`、1候補1文書を基本とする。同一文書で抽象課題と解決原理を分け、
  根拠IK・辞書の型・EC-P・Recipeへ参照で接続する。IKと候補は多対多でよく、事例を複製しない。
- 解決策間の関係は「派生形・選択肢」「補完」「別の課題・別の原理」を区別し、選択・併用条件を記す。
  既存観点の言い換えだけなら新設せず、既存候補へ統合するか参照を使う。
- 必須内容はID・版・状態・配布可否、役割・接続・操作、保持条件、適用・非適用・未確認条件、
  見分ける観測、解決と代替案、派生形と組合せ、検証・停止・復旧・負担、根拠IKと観測版、
  元の主張と今回の解釈、同一系譜・適用実績・未観測、改訂理由と判断者・根拠。
- 新規はcandidate。実際の独立した2回以上の適用結果（使用版・条件・実測・負担・後発指摘）を比較し、
  抽出に使っていない案件を含め、有効・非適用条件を担当エージェントが実測から確認した場合にreusableへ変更できる。
  確認者・日付・判断理由・結果記録へのリンクを残す。回数だけで自動昇格しない。
- 導入承認、IKの分類確定、候補の効果認定は別に扱う。過去の抽出元、既知例との整合確認、
  同一案件の再検証を独立適用へ数えない。同じIKを複数候補が参照しても実績を重複計上しない。
- 意味・適用条件・対策・検証を変える際は版を上げ、旧版と使用結果を保持する。
  旧版の実績は新しい版の効果へ自動転記しない。実績・指摘の追記は日付付きで行う。
- 統合・廃止では理由と移行先を記録し、新規の検索入口を切り替える。過去の参照先を消さない。

初期の文書導線は手動で管理する。機械用語彙・IKスキーマ・生成索引の拡張は効果確認後に判断する。
`issue_knowledge.py`は本層を検査しないため、導入・更新時に根拠参照、ID・版、関係と非適用条件を
別途照合する。意味の適合と効果は機械的なリンク検査だけで認定しない。

## 6. 軸と値を改善する

低・中確信の軸をレビューし、調査不足か語彙不足かを先に分ける。語彙不足の場合だけ proposals に
kind（value / axis）、target_axis（axisならnull）、neighbor_of（既存の軸.値）、statement、confidence を書く。
提案は種別・対象軸・相手の集合で束ねる。同一原因は一票とし high が独立3件以上なら設定候補。
件数だけで機械的に追加しない。新軸は既存の全軸で説明できない理由を各提案の statement に必要とする。

担当エージェントが実例で定義・隣接値との見分け方を確認したら、表と末尾の語彙へ追加し provisional_values に
`{"value":"軸.値", "neighbor_of":["軸.値"], "introduced_at":"YYYY-MM-DD"}` を置く。
相手の値を持ち、その軸の確信度が low / medium のエントリだけを再評価する。
証拠に基づき分類確認した独立原因2件で使われれば成立として provisional_values から外す。
採用・成立・統合・見送りは下の変更履歴へ理由を残す。廃止値は利用エントリを移行し、旧座標を history に残す。
再評価の連鎖は一巡につき1段。新しい提案は次の巡回へ送る。
件数はレビューを促す目安で、分類の正しさや改善の価値のスコアではない。

### 体系の変更履歴

- 2026-09-19: 添付資料を参考に4軸の初期語彙を採用。暫定追加値なし。他プロジェクトの課題・実績は移入しない。
- 2026-09-27: [導入承認](../improvement-plans/2026-09-27-knowledge-abstraction.md#7-導入承認と実施計画)に基づき一般化候補の手動試行・成熟・改訂規則を追加。4軸と機械用語彙は変更なし。

- 2026-10-04: ユーザー指示により、人の分類確認・効果認定の待機を撤廃。登録と分類確認、実適用効果を分離し、担当エージェントによる自律更新へ変更。旧記録のcandidateを一括昇格しない。

## 7. 機械用語彙

次のJSONは検査器が直接読む許可値。上の意味定義と同じ変更で更新する。
層の意味は [layers.md](layers.md)。検査器に語彙を複製しない。

```json
{
  "axes": {
    "processing": [
      "input_handling",
      "logic",
      "resource",
      "wording",
      "regression"
    ],
    "structure": [
      "representation",
      "responsibility",
      "decomposition",
      "aggregation"
    ],
    "connection": [
      "information",
      "meaning",
      "condition",
      "target",
      "version",
      "contract"
    ],
    "governance": [
      "ordering",
      "budget",
      "resume",
      "assignment",
      "review",
      "completion"
    ]
  },
  "discovery": [
    "symptom_report",
    "invariant_audit",
    "boundary_walk",
    "doc_code_diff",
    "data_inspection",
    "trace_walk",
    "adversarial_review",
    "inventory",
    "guardrail_failure",
    "reproduction",
    "external_constraint"
  ],
  "resolution": [
    "single_point_fix",
    "canonical_source",
    "explicit_contract",
    "required_argument",
    "vocabulary_table",
    "first_class_state",
    "representation_change",
    "responsibility_move",
    "carry_through",
    "fail_closed",
    "state_transition",
    "order_and_budget",
    "guardrail_fix",
    "doc_correction",
    "deferred_decision",
    "pending"
  ],
  "layers": [
    "sdk",
    "control_server",
    "dashboard",
    "shared_contract",
    "deployment",
    "documentation",
    "cycle_discovery",
    "cycle_selection",
    "cycle_design",
    "cycle_implementation",
    "cycle_validation",
    "cycle_correction",
    "cycle_recording"
  ],
  "neighbors": [
    [
      "processing.wording",
      "connection.meaning"
    ],
    [
      "structure.representation",
      "connection.version"
    ],
    [
      "structure.aggregation",
      "governance.completion"
    ],
    [
      "connection.condition",
      "governance.ordering"
    ],
    [
      "connection.contract",
      "processing.logic"
    ],
    [
      "governance.assignment",
      "structure.responsibility"
    ],
    [
      "governance.resume",
      "connection.version"
    ]
  ],
  "provisional_values": []
}
```
