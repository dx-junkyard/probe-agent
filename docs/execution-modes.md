# Execution Modes と説明可能なエージェント群 (Epic #412)

Parent epic: #394(進化型パイプライン制御基盤)
子 Issue: #413(実行モードの正本契約と fail-closed 制御) / #414(Flow・
エージェント群の説明可能な集約 projection) / #415(提案・Shadow 実験・
人間承認のオーケストレーション統合)

この文書が Epic #412 の canonical contract である。この領域のコードに触る前に
§0 を読むこと。

---

## 0. この文書の位置づけ

### 0.1 何を確定するか

1. 実行モード `fixed / observe / propose / shadow` の正本と、既存の 4 軸との
   責務境界(§1)
2. モード適用粒度 (system / Flow / Node) の identity と、決定的な解決規則
   (§2〜§3)
3. fail-closed 制御境界 — 「LLM を呼ばない」が設定上の期待ではなく到達不能で
   あることをどう保証するか(§4)
4. append-only audit と runtime/設定の乖離観測(§5)
5. Flow・エージェント群の読み取り専用 projection(§6)
6. 提案・実験・人間承認のオーケストレーション(§7)

### 0.2 何を作らないか

- LLM による自動採用・source 変更・policy 変更・publish・deploy
- 既存の Node maturity / Cell Improvement attempt / SDK policy
  `off`/`trace`/`shadow` を一つの状態変数へ統合すること
- LLM 自由文を canonical state や事実として保存すること
- 人格を持つ常駐エージェントという新しい正本
- 既存 component / Node の一括移行

### 0.3 上位関係

`CLAUDE.md` の Core Design Principles、`docs/evolutionary-pipeline.md` の
ADR-1〜ADR-9、`docs/purpose-chain.md`、`docs/ux-design-lineage.md` が引き続き
正本である。この文書はそれらと矛盾しない。矛盾して見える箇所があれば #412 の
側が誤りであり、既存の安全境界を優先する。

とくに ADR-6(四つの軸は決して互いから導出しない)と ADR-9(maturity の自動
遷移は存在しない)はこの Epic でも一切緩めない。実行モードは **5 本目の独立軸**
であり、既存 4 軸のどれかの言い換えではない(§1.2)。

---

## 1. 実行モードの正本

### 1.1 モードの意味

| モード | 通常処理 | 実験用 LLM | 候補実行 | 本番出力 |
| --- | --- | --- | --- | --- |
| `fixed` | 固定実装だけを実行 | 到達不能 | 不可 | 固定実装のみ |
| `observe` | 固定実装を実行し観測 | 到達不能 | 不可 | 固定実装のみ |
| `propose` | 固定実装を実行 | 候補・実験計画の**提案のみ** | 不可 | 固定実装のみ |
| `shadow` | baseline と候補を比較 | 提案可 | 承認済み経路のみ | baseline のみ |

`EXECUTION_MODES = ("fixed", "observe", "propose", "shadow")` は有限語彙で
あり、序列 `fixed < observe < propose < shadow` を持つ(permissiveness
ordering)。この序列は **fail-closed 時にどこへ落とすか**を決めるためだけに
存在し、スコアでも進捗でもない。

`fixed` は「LLM を使わない設定」ではない。**資格情報・クライアント構築経路・
候補実行権限のいずれにも到達できない状態**である(§4)。

### 1.2 五本目の軸であること

| 軸 | 正本 | 何を語るか |
| --- | --- | --- |
| Node maturity | `evolution_node.maturity` (#396) | その処理をどれだけ分かっているか |
| Cell Improvement status | `cell_improvement` (#304) | 個々の改善試行がどこまで進んだか |
| SDK policy mode | `components.mode` (`off`/`trace`/`shadow`) | SDK が何を計装・送信するか |
| Dashboard user phase | #237/#256/#349 | 開発者が画面上のどこにいるか |
| **実行モード** | `execution_mode_assignment` (#413) | **いま LLM と候補実行をどこまで許すか** |

五つは互いから導出しない。API は五つを別フィールドで返す。`null` は
「リンクされた対象がない」であって「進行中のものがない」ではない。

とくに SDK policy の `shadow` と実行モードの `shadow` は**別の事実**である。
前者は SDK が候補関数を実行して `shadow_results` を送るかどうか、後者は
control plane がその Node に対して候補比較を許すかどうかを表す。片方だけが
`shadow` である状態は正当であり、projection はそれを二つの読みとして並べて
表示しなければならない(#366 の「一つの表示語が二つの事実を運ばない」)。

### 1.3 capability 語彙

モードは直接使わず、**capability** へ翻訳してから権限判定に使う。

```
EXECUTION_CAPABILITIES = (
    "observation_record",       # 観測結果を記録してよい
    "llm_experiment_proposal",  # 実験用 LLM を呼んで提案を作ってよい
    "candidate_execution",      # 候補実装を隔離環境で実行してよい
    "shadow_comparison",        # baseline と候補を比較してよい
)
```

固定の許可表(first match ではなく完全表):

| モード | 許可される capability |
| --- | --- |
| `fixed` | (なし) |
| `observe` | `observation_record` |
| `propose` | `observation_record`, `llm_experiment_proposal` |
| `shadow` | 全 4 つ |

`propose` が `candidate_execution` を持たないのは意図的である。提案は計画で
あって実行ではない。実行へ進むには **人間の承認 + `shadow` への昇格** という
二つの独立した事実が要る(§7.4)。

---

## 2. スコープと identity

### 2.1 三つのスコープ

| scope_kind | scope_ref | 正本 |
| --- | --- | --- |
| `system` | `""`(空文字列) | `systems.id`(行そのもの) |
| `flow` | `runtime_flow:<flow_id>` | `trace_spans.flow_id` |
| `node` | `<node_key>` | `evolution_node.(system_id, node_key)` |

`scope_ref` は必ず前置詞付きで保存する。`static_flow` と `runtime_flow` を
一語にまとめないという #405 の規律をここでも守る。

### 2.2 なぜ static flow はモードスコープにならないか (EM-ADR-1)

`static_flow`(`code_entrypoints.entrypoint_id`、snapshot scoped)は #414 の
**表示 subject** ではあるが、**モードスコープではない**。

理由: モードスコープは fail-closed ゲートの入力である。ゲートは「読めなかった
場合は `fixed`」でなければならず、**失敗しうる導出に依存してはならない**。
static flow の Node 所属を知るには、pin した snapshot に対して
`flow_graph.build_flow_graph` を走らせ、call graph を再計算する必要がある。
これは (a) 呼び出しごとに高価で、(b) snapshot 欠損・parse 失敗で落ちうる。
落ちうる導出をゲートの入力に置くと、ゲートの失敗が「停止」になり「安全側へ
倒れる」にならない。

したがって **モードスコープは永続行だけから解決できるものに限る**。runtime
flow は `evolution_node_link(link_kind='flow')` という既存正本の 1 本の
indexed query で Node 所属が決まるので、この条件を満たす。

`flow_id` は SDK の `probe_flow()` に開発者が渡した論理名であるとき安定である
(`context.py`: `flow = flow_id or parent_flow or new_span_id()`)。名前を
渡していない場合は実行ごとの ID になり、モードスコープとしては無意味だが
有害ではない — 誰も割り当てないので単に使われない。

### 2.3 Node の Flow 所属

Node `N` が flow `F` に属するとは、`evolution_node_link` に
`link_kind='flow'`、`target_ref=<flow_id>`、`superseded_by_id IS NULL` の行が
存在することをいう。これは #397 が既に採用している正本であり、この Epic は
新しい所属テーブルを作らない。

一つの Node は複数の Flow に属しうる。これが §3.3 の衝突規則の理由である。

---

## 3. 決定的な解決規則

### 3.1 assignment レコード

`execution_mode_assignment` は **append-only** で、行は 2 種類ある。

| record_kind | 意味 |
| --- | --- |
| `assign` | このスコープにこのモードをこの期間割り当てる |
| `revoke` | このスコープの割り当てを人間が明示的に終了した |

行を UPDATE しない。あるスコープの「今の状態」は、そのスコープの
`superseded_by_id IS NULL` の行のうち `id` 最大の 1 行である。新しい行を
書くとき、同じスコープの直前の行に `superseded_by_id` を張る(これは
append-only の連鎖ポインタであり、内容の書き換えではない — 既存の
`evolution_node_version` / `ux_design_decision` と同じ形)。

`assign` 行は `effective_from`(必須)と `effective_until`(NULL 可)を持つ。

### 3.2 スコープごとの状態

あるスコープの最新行から、次の有限状態を決める(first match)。

| # | 条件 | scope_state |
| --- | --- | --- |
| 1 | 行が無い | `unset` |
| 2 | 最新行が `revoke` | `revoked` |
| 3 | 最新行が `assign` かつ `now < effective_from` | `pending` |
| 4 | 最新行が `assign` かつ `effective_until` があり `now >= effective_until` | `expired` |
| 5 | 最新行が `assign` かつ mode が有限語彙に無い | `invalid` |
| 6 | それ以外 | `active` |

同一スコープに `superseded_by_id IS NULL` の行が 2 行以上ある場合(supersede
規律が破れているデータ不整合)は `conflicting`。読み取り側は防御的にこれを
検出する。

### 3.3 実効モードの決定 (first match)

入力は `ModeFacts`(system / flow(複数可) / node の scope_state と mode、
および `now`)。出力は `ExecutionModeDecision`
(`mode`, `source_scope`, `source_ref`, `reason`, `scope_trace`)。

| # | 条件 | 結果 mode | source_scope | reason |
| --- | --- | --- | --- | --- |
| 1 | どれかのスコープが `conflicting` | `fixed` | `none` | `conflicting_assignments` |
| 2 | どれかのスコープが `invalid` | `fixed` | `none` | `invalid_mode_value` |
| 3 | caller が指定した flow に node が属していない | `fixed` | `none` | `flow_scope_not_member` |
| 4 | node が `expired` | `fixed` | `none` | `node_expired_assignment` |
| 5 | node が `active` | その mode | `node` | `node_assignment` |
| 6 | flow のうち `expired` が 1 つ以上 | `fixed` | `none` | `flow_expired_assignment` |
| 7 | flow の `active` が複数あり mode が一致しない | `fixed` | `none` | `flow_scope_conflict` |
| 8 | flow が `active`(全て同一 mode) | その mode | `flow` | `flow_assignment` |
| 9 | system が `expired` | `fixed` | `none` | `system_expired_assignment` |
| 10 | system が `active` | その mode | `system` | `system_assignment` |
| 11 | 上記いずれでもない | `fixed` | `default` | `no_assignment` |

`REASON_CODES` はこの 11 個の有限集合であり、これ以外を返さない。

### 3.3.1 caller の主張はスコープの証拠ではない (EM-ADR-4)

`node_key` が与えられているとき、resolver が見る flow 集合は
**その Node が `evolution_node_link(link_kind='flow')` で実際に属している
flow だけ**である。caller が渡した `flow_ref` はその集合への**追加ではなく、
照合される主張**にすぎない。

当初の実装は両者を union していた。その結果、Flow A に `propose` を割り当て、
Flow A に属さない Node B を指定して A を名乗るだけで、単独では `fixed` である
Node B が LLM に到達できた。スコープは永続行から決まらなければならないという
EM-ADR-1 の理由そのものに反する、fail-closed の破れである。

属していない flow を名乗った場合、それは**黙って無視するのではなく行 3 で
拒否する**。黙って落とすと、caller は「Flow A の権限が効いた」と信じたまま
になり、同じ欠陥を裏側から作ることになる。`ModeFacts.rejected_flow_claims`
が拒否された主張を運び、`scope_trace` からは実際に見た scope が読める。

`node_key` が無い場合(純粋な flow スコープの問い合わせ)は、caller が名乗った
flow が**主語そのもの**なので、この規則は適用されない。

行 3 / 5 / 8 は同じ `fixed` へ落ちるが、**別々のコードを持つ**。三つとも
「期限が切れた」だが、開発者の次の操作は「Node を割り当て直す」「Flow を
割り当て直す」「System を割り当て直す」で別々であり、一つのコードにすると
どのスコープが切れたのかを `scope_trace` まで辿らないと分からない。
一つの表示語が二つの事実を運ばない(#366)。

行 7 で複数の flow が同じ mode で `active` のとき、`source_ref` は
**辞書順で最小のもの**を名乗る(再現可能であるため)。該当する flow の
全体は `scope_trace` にある。

`assign` 行の `effective_from` が NULL の場合は「下限なし」として扱い、
`pending` にはならない。`assign_mode` は常に値を書くので、NULL は旧データ
または直接 INSERT された行にのみ現れる。

### 3.4 expired と revoked を分けること (EM-ADR-2)

行 3 / 5 / 8 が **上位スコープへ継承させずに `fixed` へ落とす**のは意図的で
ある。

`effective_until` は人間が設定した実験の期限である。期限が切れたときに
system スコープの `propose` が代わりに効いてしまうと、その期限は何も止めて
いない。期限切れは「人間がまだ次の判断をしていない」状態であり、最も安全な
側 = `fixed` へ倒すのが fail-closed の定義そのものである。

一方 `revoke` は **人間が明示的に「この割り当ては終わり」と記録した**事実で
ある。この場合は通常の継承が再開する(行 2 の `revoked` はどの行にも
マッチせず、次のスコープへ落ちる)。

期限切れの Node を通常運用へ戻す手順は、`revoke` を記録するか、新しい
`assign` を記録するかのどちらかで、どちらも `decision_method: manual` の
人間の記録である。**時間の経過だけで権限が復活する経路は存在しない。**

### 3.5 純粋関数であること

`resolve_execution_mode(facts: ModeFacts) -> ExecutionModeDecision` は
純粋関数である。ルート、Dashboard、projection、orchestrator のいずれも
この判定を再導出しない(#349 の「canonical state engine は 1 つ」)。

DB から `ModeFacts` を組み立てるのは `load_mode_facts(conn, ...)` の役割で、
そこには判断を置かない。

---

## 4. fail-closed 制御境界

### 4.1 capability ゲート

```python
require_capability(
    conn, *, system_id, capability, node_key=None, flow_ref=None, now=None
) -> ExecutionModeDecision
```

許可されない場合は `ExecutionModeDenied` を送出する。例外は有限の
`denial_code` を持つ:
`capability_not_permitted` / `conflicting_assignments` /
`invalid_mode_value` / `node_expired_assignment` /
`flow_expired_assignment` / `system_expired_assignment` /
`flow_scope_conflict` / `unknown_capability` / `node_not_found`。

このうち `unknown_capability` と `node_not_found` は**壊れた要求**が主語で
あってモードの読みではないので、409 の本文の `decision` は `null` になる
(読めなかった事実に既定値を代入しない、#380)。残りは必ず decision を伴う。

HTTP 境界では 409 を返し、`denial_code`・実効モード・`source_scope` を
本文に含める。開発者が「なぜ拒否されたか」と「どこの設定が効いているか」を
一度で読めることが要件である。

### 4.2 adapter 分離 (EM-ADR-3)

実験用 LLM クライアントは **`build_experiment_llm_adapter` 経由でしか
構築できない**。

```python
def build_experiment_llm_adapter(
    conn, *, system_id, node_key=None, flow_ref=None, purpose, now=None
) -> Tuple[LLMClient, ExecutionModeDecision]
```

この関数の本体の順序が契約である。

1. `require_capability(..., capability="llm_experiment_proposal")`
2. **その後で初めて** `LLMConfig.intelligence_from_env()` を呼ぶ
3. `create_llm_client(config)`

`fixed` / `observe` では 1 で例外になるため、**資格情報を読む行に到達しない**。
「設定で LLM を無効にしている」のではなく「資格情報を読む経路が実行されない」
という違いがここにある。テストはこの順序を直接検証する(§9.1)。

`propose` / `shadow` 以外のモードでこの関数を回避して `create_llm_client` を
直接呼ぶ新しい経路を、この Epic の対象 Node に対して作ってはならない。

### 4.3 候補実行ゲート

`candidate_execution` / `shadow_comparison` は Replay variant run、offline
shadow、sub-pipeline 比較の入口で `require_capability` を通す。ここでも
**承認の有無とモードは別の事実**である(§7.4): モードが `shadow` でも未承認の
提案は実行できず、承認済みでもモードが `shadow` でなければ実行できない。

### 4.4 既存経路への影響

未設定の既存 Node・既存 component は行 10(`no_assignment`)により `fixed` と
して読まれる。これは**既存の実行経路を止めない**: このゲートは
`execution_mode` を明示的に呼ぶ新しい経路(#415 の提案生成・実行記録)にのみ
掛かる。既存の Candidate Studio / Replay / Interview の LLM 経路の意味は
変えない(#413 の非目標「既存 SDK policy の意味変更」と同じ理由)。

互換読み取りのために、projection は `mode_source: "default"` を
「既定の `fixed`」として明示し、「人間が `fixed` を選んだ」(`system_assignment`)
と区別する。これも二つの事実である。

---

## 5. audit と runtime 乖離

### 5.1 append-only audit

`execution_mode_assignment` の 1 行がそのまま audit である。保持する項目:
`record_kind` / `scope_kind` / `scope_ref` / `mode` / `previous_mode` /
`effective_from` / `effective_until` / `reason`(必須) / `actor_kind` /
`actor` / `decision_method`(常に `manual`) / `created_at` /
`supersedes_id`。

`previous_mode` は書き込み時点で解決された実効モードを記録する。後から
「何から何へ変わったか」を再計算しなくても読めるようにするためである
(#337 の「close は audit record である」と同じ discipline)。

`actor` は認証された principal から取る。**body から受け取らない**(#337)。
`decision_method` は常に `manual`: HTTP でモードを変える主体は人間である。

### 5.2 runtime と設定の乖離

`execution_mode_observation` は「実際にその Node がどのモードで動いたか」を
記録する。`GET /execution-modes/divergence` は Node ごとに次の有限状態を
返す。

| divergence | 意味 |
| --- | --- |
| `match` | 直近の観測が実効モードと一致 |
| `divergent` | 一致しない(観測値と実効値を両方返す) |
| `unobserved` | 観測が 1 件も無い |
| `stale` | 観測はあるが、最後の観測より後にモードが変わった |

`unobserved` を `match` として扱わない。「観測できていない」は成功では
ない(#380 の「読めなかった事実は既定値ではない」)。

---

## 6. Flow・エージェント群の説明 projection (#414)

### 6.1 位置づけ

読み取り専用。**何も書かない。** `app/flow_explanation.py` が唯一の正本で、
Dashboard も LLM も状態を再導出しない。新しい理解モデルを作らず、既存の
Purpose / Capability / Flow / Node / evidence を集約して説明するだけである。

### 6.2 subject identity

| subject_kind | subject_ref | 解決先 | 追加入力 |
| --- | --- | --- | --- |
| `runtime_flow` | `flow_id` | `trace_spans.flow_id` | なし |
| `static_flow` | `entrypoint_id` | `code_entrypoints.entrypoint_id` | `snapshot_id` 必須 |

`flow_graph` の `flow-1` / `flow-2` は 1 回の導出内でしか安定しないので、
**恒久 ID として保存も返却もしない**(#405 と同じ規律)。

Node 所属:
- `runtime_flow`: `evolution_node_link(link_kind='flow')`(§2.3)
- `static_flow`: Node → `evolution_node_link(link_kind='probe_point')` →
  `probe_points` の `(path, qualified_name)` を、その snapshot の
  `flow_graph.build_flow_graph` が返す node 集合と**完全一致**で突き合わせる。
  類似度・キーワード・埋め込みを使わない(Principle 6)。flow graph が
  組めない場合は所属は空集合ではなく `membership: "unavailable"` である。

### 6.3 返す軸(合成しない)

section ごとに独立して返し、**平均値・単一スコア・完成度・confidence
percentage を作らない**(ADR-7 / #353)。

1. `purpose` — この Flow が支える Purpose 要素・Capability・Feature。
   Capability は #312 の `understanding_capability_entity.id` を使う。
2. `responsibility` — Flow の責務、入出力境界、構成 Node と依存関係。
3. `nodes` — Node ごとに **5 軸を別フィールドで**:
   `execution_mode`(+ `mode_source` / `mode_reason`)、`maturity`、
   `implementation_modality`、`improvement_status`、`sdk_policy_mode`。
   さらに観測カバレッジ(#400 の `observation`)。
4. `open_items` — 現在の anomaly、未解決事項、`stale` / `missing` /
   `unmeasured` の一覧。
5. `experiments` — 進行中・提案中・承認済みの実験(#415)と、その根拠・
   影響範囲。
6. `baseline` — baseline 実装、rollback 先、人間承認の状態。

### 6.4 欠損の語彙

`missing`(存在しない) / `unavailable`(読めなかった) / `unmeasured`(測る
仕組みが無い) / `stale`(前提が動いた) / `not_applicable`(構造上不要)は
**5 つの別の答え**であり、丸めない。

section 単位で `degraded_sections` に落とす(#380 の overview projection と
同じ形)。一つの section の失敗が projection 全体を 500 にしない。失敗した
section は表示を落とすだけで、推測値を代入しない。

### 6.5 drill-down と逆方向

Purpose → Capability → Flow → Node → evidence の下向きと、
evidence → Node → Flow → Capability → Purpose の上向きの両方を返す。
evidence は必ず参照可能な ID(trace_id / anomaly id / run id / revision id)
を持つ。

### 6.6 LLM 要約を足す場合

構造化された証拠カードが先で、自然言語要約は**任意の追加**である。追加する
場合は引用可能な evidence ID を必須とし、事実・提案・不確実性を区別し、
`decision_method: reasoning_llm` を記録し、**canonical state を置き換え
ない**。要約の失敗は projection の失敗ではない。

---

## 7. オーケストレーション (#415)

### 7.1 提案の完全性ゲート (fail closed)

`flow_experiment_proposal` は次を**すべて**持たなければ作成できない。欠けたら
有限の拒否コードで 422 を返す。

| 必須項目 | 拒否コード |
| --- | --- |
| 目的 `purpose` | `purpose_missing` |
| 仮説 `hypothesis` | `hypothesis_missing` |
| 対象範囲(1 件以上の対象 Node) | `scope_missing` |
| baseline 参照 | `baseline_missing` |
| 候補(1 件以上) | `candidates_missing` |
| 評価軸(1 件以上) | `evaluation_axes_missing` |
| quality floor | `quality_floor_missing` |
| 副作用隔離戦略 | `isolation_strategy_missing` |
| コスト上限 | `cost_cap_missing` |
| 停止条件(1 件以上) | `stop_conditions_missing` |
| rollback 計画 | `rollback_plan_missing` |
| 根拠(1 件以上の evidence 参照) | `evidence_missing` |

さらに構造検証:
`unknown_flow_subject` / `node_not_in_flow` / `unresolved_node` /
`comparison_scope_mismatch` / `isolation_required_for_side_effects` /
`evaluation_contract_missing` / `duplicate_proposal_key`。

### 7.2 single_node と sub_pipeline を混同しない

`comparison_scope` は `single_node` か `sub_pipeline` の 2 値。

- `single_node`: 対象 Node がちょうど 1 つ。既存の Replay / offline shadow
  (#242〜#246)へ渡す。指標は Node 指標。
- `sub_pipeline`: 対象 Node が 2 つ以上。Flow / Capability 指標と Node 指標を
  **別々に**保持する(ADR-7)。片方をもう片方から導出しない。

対象 Node 数と `comparison_scope` が一致しなければ
`comparison_scope_mismatch` で拒否する。

### 7.3 副作用の隔離

`isolation_strategy` は有限:
`pure` / `mock` / `dry_run` / `rollback_transaction` / `isolated_workspace` /
`none`。

対象 Node の `evolution_node_version.side_effect_class` が
`external_write` または `irreversible` のとき、`isolation_strategy` が
`none` または `pure` の提案は `isolation_required_for_side_effects` で
拒否する(Principle 4 の「payment / email / DB write / auth を shadow 対象に
しない」を、提案の入口で構造的に効かせる)。

### 7.4 lifecycle は event fold で導出する

`flow_experiment_event` が append-only の正本で、`status` 列は**保存しない**。
`derive_proposal_status(events)` が畳み込む(#337/#338/#349/#405 と同じ
discipline: 保存された lifecycle 値は行から drift するが、導出された値は
drift しない)。

event_kind(有限):
`proposed` / `approved` / `rejected` / `withdrawn` / `expired` /
`execution_recorded` / `result_recorded` / `promotion_candidate_recorded` /
`rollback_recorded`。

導出される status(有限):
`proposed` / `approved` / `rejected` / `withdrawn` / `expired` /
`executing` / `completed`。

遷移規則(first match、拒否は有限コード):
- `approve` / `reject` は `proposed` からのみ。それ以外は
  `not_awaiting_decision`。
- `execution_recorded` は `approved` / `executing` からのみ。未承認なら
  `not_approved`。
- `promotion_candidate_recorded` は `result_recorded` が 1 件以上ある場合の
  み。無ければ `no_result_recorded`。
- 実行記録が 1 件も無い提案に対する結果・rollback の主張は
  `no_execution_recorded`。結果は実行の観測であって、実行していないものの
  結果は存在しない。
- 期限切れ(`expires_at` 経過)の提案は承認できない。`proposal_expired`。

### 7.5 承認は 2 つの別記録

**モードが `shadow` であること**と**人間が承認したこと**は独立した事実で
あり、実行にはその両方が要る(§4.3)。

- 承認: `approved` event、`decision_method: manual`、actor は認証された
  principal。
- モード: `require_capability(..., "candidate_execution")`。

承認済み提案があっても、モードが `propose` に戻されていれば実行記録は
409 で拒否される。逆にモードが `shadow` でも未承認なら 409 で拒否される。

### 7.6 本番を書き換える経路を持たない

`app/flow_orchestration.py` は次のどれも行わない。
`evolution_node.maturity` の変更 / `components.mode` の変更 / patch 適用 /
publish job の作成 / worktree への書き込み / target repo への書き込み /
Cell Improvement 状態の変更。

実行の実体は**既存の正本**(`replay_variants`、`experiments`、`shadow_results`)
であり、この層は `flow_experiment_execution_ref` でそれらを**参照するだけ**
である。参照は read 時に解決し、保存した row id を単独で信用しない
(#405 の規律)。

昇格候補(`promotion_candidate_recorded`)は**候補の記録であって昇格では
ない**。実際の昇格は既存の Experiment 採否 / Stabilization / publish の
人間ゲートを通る。

### 7.7 LLM による提案生成

`propose_flow_experiment(...)` は `build_experiment_llm_adapter` 経由で
のみ動く(= `propose` / `shadow` のみ)。structured output の検証に失敗したら
**run を失敗させる**。heuristic fallback を作らない(Principle 6)。
`intelligence_runs` に provider / model / prompt version / schema version /
decision_method / snapshot / 失敗詳細を残す(Principle 7)。

生成された提案は `decision_method: reasoning_llm` の **draft** であり、
`proposed` event を書くのは人間の操作である。LLM の出力だけで承認待ちの列に
入らない。

---

## 8. 永続化

すべて System scoped。既存正本テーブルへの UPDATE / INSERT は行わない。

### 8.1 `execution_mode_assignment` (#413)

append-only。`record_kind` (`assign`|`revoke`)、`scope_kind`、`scope_ref`、
`mode`(revoke 行は NULL)、`previous_mode`、`effective_from`、
`effective_until`、`reason`、`actor_kind`、`actor`、`decision_method`、
`supersedes_id`、`superseded_by_id`、`schema_version`、`created_at`。

index: `(system_id, scope_kind, scope_ref, id DESC)`。

### 8.2 `execution_mode_observation` (#413)

`node_key`、`observed_mode`、`capability`、`run_ref`、`source`
(`control_server`|`sdk`)、`detail`、`recorded_at`。
index: `(system_id, node_key, id DESC)`。

### 8.3 #414

**テーブルを追加しない。** projection は既存行からの導出である。

### 8.4 `flow_experiment_proposal` / `_target` / `_event` / `_execution_ref` (#415)

`flow_experiment_proposal` は不変の内容だけを持ち、`status` 列を持たない
(§7.4)。`proposal_key` は `(system_id, proposal_key)` で一意。

`flow_experiment_target`: `target_node_key`、`target_role`
(`baseline`|`candidate_target`)、`position`。

`flow_experiment_event`: append-only。§7.4 の event_kind、`actor_kind`、
`actor`、`reason`、`decision_method`、`payload_json`。

`flow_experiment_execution_ref`: `execution_kind`
(`replay_variant_run`|`experiment`|`shadow_result`)、`execution_ref`、
`recorded_at`。

---

## 9. テスト要件

### 9.1 #413

- 10 行の解決表の各行が個別に検証される
- `expired` が上位スコープへ継承せず `fixed` になる / `revoked` は継承する
- 同一 Node が mode の異なる 2 つの flow に属すると `flow_scope_conflict`
- `fixed` / `observe` で `build_experiment_llm_adapter` が
  **資格情報を読む前に**例外になる(`LLMConfig.intelligence_from_env` と
  `create_llm_client` を落とし穴に差し替え、呼ばれないことを表明する)
- `propose` で `candidate_execution` が拒否される
- assignment の actor が body ではなく principal から来る
- System 分離(別 System の割り当てが漏れない)
- 乖離 4 状態(`match`/`divergent`/`unobserved`/`stale`)

### 9.2 #414

- Purpose → Capability → Flow → Node → evidence の双方向到達
- 単一スコアを返さないこと、5 軸が別フィールドであること
- `missing` / `unavailable` / `unmeasured` / `stale` / `not_applicable` の
  区別
- 1 section の失敗が `degraded_sections` に落ち、他 section が返ること
- `mode_source` が `default` と `system_assignment` を区別すること
- static flow の Node 所属が完全一致であること、flow graph 不能時に
  `unavailable` になること
- projection が何も書かないこと(前後で全テーブルの行数が不変)

### 9.3 #415

- §7.1 の各拒否コードが個別に検証される
- `comparison_scope` と対象 Node 数の不一致が拒否される
- 副作用クラスと隔離戦略の不整合が拒否される
- 未承認での実行記録が 409、承認済み + `propose` モードでも 409
- status が event fold で導出され、列に保存されていないこと
- 承認・実行・結果・昇格候補・rollback が audit から追跡できること
- 提案の作成・承認が `components.mode` / `evolution_node.maturity` /
  patch / publish のどれも変えないこと(前後比較)
- 代表 fixture で `fixed → observe → propose → shadow` を通し、
  **本番出力(baseline の戻り値)が不変**であること

---

## 10. 人間ゲート(変更なし)

理解の確認 / Alignment 項目の確定 / 提案の承認・編集・却下 / 差分の適用 /
観測の開始 / 採否の記録 / publish / Replay approval / 固定化承認 /
reopen 承認 は一切緩めない。この Epic が追加する

- 実行モードの割り当てと revoke
- Flow 実験提案の承認・却下・撤回
- 昇格候補の記録

もすべて `decision_method: manual` である。
