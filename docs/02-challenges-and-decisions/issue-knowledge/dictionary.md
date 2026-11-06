# 課題の型の辞書

[入口](README.md) · [索引・成立状態](index.md) · [分類規則](taxonomy.md)

族→型の2段。下記は分類確認前の暫定型。成立状態は索引が証拠に基づく分類確認と独立原因数から導出する。
典型座標は検索の手掛かりで、個別エントリへ強制しない。型を変える前に相手との見分け方を確認する。

### contract-transfer

契約の伝達に関する原因をまとめる。

#### generation-contract-mismatch

| 項目 | 内容 |
| --- | --- |
| 一般形 | 生成側と消費側が共有すべき有限契約を共有せず、解釈できない値を受け渡す |
| 典型座標 | connection.contract + connection.meaning |
| 発見観点 | data_inspection / boundary_walk |
| 解決観点 | vocabulary_table / guardrail_fix |
| 見分け方・設計時の問い | 生成例が正規値か、表示ラベルか。生成時と適用時に同じ語彙を使うか。 |

#### downstream-record-assembled-by-hand

| 項目 | 内容 |
| --- | --- |
| 一般形 | 後段（外部の台帳）が受け取る記録の形を前段が出さず、人が詳細のJSONから毎回組み立てて写す |
| 典型座標 | connection.contract + connection.information |
| 発見観点 | external_constraint / boundary_walk |
| 解決観点 | explicit_contract / carry_through |
| 見分け方・設計時の問い | 後段の入力の項目（必須・任意）を前段の出力だけで埋められるか。同じ実行から誰が何度組み立てても同じ記録になるか（LLMや時刻を挟まない）。記録が未完（判断待ち等）であることを記録自身が言うか。 |

### delivery-path

対象への到達に関する原因をまとめる。

#### registered-but-unreachable

| 項目 | 内容 |
| --- | --- |
| 一般形 | 受け口の登録を実際の配送と混同し、対象が未準備の経路で受け渡せない |
| 典型座標 | connection.target + governance.completion |
| 発見観点 | boundary_walk / reproduction |
| 解決観点 | carry_through / explicit_contract |
| 見分け方・設計時の問い | 対象が閉じている状態から開始し、到達・応答・保存・復帰をたどれるか。 |

#### projection-target-mismatch

| 項目 | 内容 |
| --- | --- |
| 一般形 | 状態と本文を別々に選び、異なる候補の情報を一つに表示する |
| 典型座標 | connection.target |
| 発見観点 | adversarial_review / boundary_walk |
| 解決観点 | canonical_source / carry_through |
| 見分け方・設計時の問い | 旧セッションに候補を残して新規セッションを作っても、状態と本文が同一revisionに属するか。 |


### version-lineage

依存する版の保持に関する原因をまとめる。

#### dependency-version-omitted

| 項目 | 内容 |
| --- | --- |
| 一般形 | 主対象だけの鮮度で判断し、依存先の変更後も古い候補を適用する |
| 典型座標 | structure.representation + connection.version |
| 発見観点 | adversarial_review / boundary_walk |
| 解決観点 | representation_change / carry_through |
| 見分け方・設計時の問い | 主対象を変えず依存先だけ変えたとき、古い候補を拒否できるか。 |

#### historical-premise-overwritten

| 項目 | 内容 |
| --- | --- |
| 一般形 | 昇格結果で会話開始時の前提を上書きし、旧履歴と新前提を混在させる |
| 典型座標 | structure.representation + connection.version |
| 発見観点 | invariant_audit / doc_code_diff |
| 解決観点 | first_class_state / state_transition |
| 見分け方・設計時の問い | 昇格前後でbase revision/digest/jsonが不変か。結果を記録するために入力の履歴を書き換えていないか。 |

#### frozen-view-live-dependency

| 項目 | 内容 |
| --- | --- |
| 一般形 | 固定した版の表示が可変行を参照し、版を変えずに内容が変わる |
| 典型座標 | connection.version |
| 発見観点 | adversarial_review / boundary_walk |
| 解決観点 | canonical_source / carry_through |
| 見分け方・設計時の問い | 昇格元でgoalを編集してもheadのVisionが不変か。依存版の検査漏れ（IK-0003）ではなく読取り元の選択を検査する。 |


### acceptance-evidence

受入証拠の対象範囲に関する原因をまとめる。

#### proxy-evidence-for-acceptance

| 項目 | 内容 |
| --- | --- |
| 一般形 | 部品の技術検証だけで利用者にとっての成立まで判断しようとする |
| 典型座標 | governance.completion |
| 発見観点 | invariant_audit / inventory |
| 解決観点 | deferred_decision |
| 見分け方・設計時の問い | 自動テストが通った以外に、目的とする利用の実測があるか。未観測は何か。 |

#### unfinished-execution-as-evidence

| 項目 | 内容 |
| --- | --- |
| 一般形 | 参照先が存在するだけで実行済みと認め、未完了runを証拠に使う |
| 典型座標 | governance.completion |
| 発見観点 | invariant_audit / boundary_walk |
| 解決観点 | explicit_contract / fail_closed |
| 見分け方・設計時の問い | 参照の存在だけでなく完了状態も確認しているか。実利用を測っていない問題（IK-0004）とは証拠の欠陥が異なる。 |

#### finalized-evidence-mutable

| 項目 | 内容 |
| --- | --- |
| 一般形 | 完了後も測定値を上書きでき、承認が参照した証拠を変更する |
| 典型座標 | governance.completion + connection.condition |
| 発見観点 | invariant_audit / adversarial_review |
| 解決観点 | state_transition / fail_closed |
| 見分け方・設計時の問い | complete_run後の測定値や実行参照の変更が拒否されるか。完了ゲートだけでなく後続の更新経路も状態を検査するか。 |


### execution-order

2026-09-21に過去の修正記録から抽出。

#### precheck-only-validation

| 項目 | 内容 |
| --- | --- |
| 一般形 | 推論開始時だけ前提を確認し、推論中の変更後も古い結果を保存する |
| 典型座標 | governance.ordering |
| 発見観点 | boundary_walk / adversarial_review |
| 解決観点 | order_and_budget / fail_closed |
| 見分け方・設計時の問い | LLM待機中にheadを変更すると旧結果の保存を拒否するか。チェックする情報の不足（IK-0003）とチェック時機の不足を分ける。 |

#### multiwrite-without-atomicity

| 項目 | 内容 |
| --- | --- |
| 一般形 | 接続ロックをトランザクションと見なし、作成途中の例外で不完全な行を残す |
| 典型座標 | governance.ordering |
| 発見観点 | doc_code_diff / adversarial_review |
| 解決観点 | explicit_contract / order_and_budget |
| 見分け方・設計時の問い | premise捕捉を例外にしてsession/runが残らないか。プロセス内lockをrollback保証と取り違えていないか。 |

#### check-write-not-atomic

| 項目 | 内容 |
| --- | --- |
| 一般形 | 保存前再検証と書込みが別トランザクションで、検証後の競合を許す |
| 典型座標 | governance.ordering |
| 発見観点 | adversarial_review / boundary_walk |
| 解決観点 | order_and_budget / explicit_contract |
| 見分け方・設計時の問い | 再検証の直後に別DB接続から昇格を試みて拒否されるか。推論中だけの競合テスト（IK-0006）ではこの窓を覆えない。 |

#### concurrent-retry-not-idempotent

| 項目 | 内容 |
| --- | --- |
| 一般形 | 同一キーの並行再送を通常の重複と扱えず、競合が失敗になる |
| 典型座標 | governance.resume |
| 発見観点 | reproduction / adversarial_review |
| 解決観点 | order_and_budget / guardrail_fix |
| 見分け方・設計時の問い | 同一keyの二接続が競合してもeventが一つで、敗者が500やillegal_transitionにならないか。 |

### information-lineage

2026-09-21に過去の修正記録から抽出。

#### inherited-information-dropped

| 項目 | 内容 |
| --- | --- |
| 一般形 | 現セッションの行だけで次世代を構成し、継承済みの確定情報を落とす |
| 典型座標 | connection.information |
| 発見観点 | boundary_walk / invariant_audit |
| 解決観点 | carry_through / explicit_contract |
| 見分け方・設計時の問い | 再入力しないまま二世代昇格しても確定したgoalが残るか。読むだけの継承を次の保存へ運んでいるか。 |

### state-semantics

2026-09-21に過去の修正記録から抽出。

#### candidate-canonical-conflation

| 項目 | 内容 |
| --- | --- |
| 一般形 | 正準未確定時の代替表示が、進行中候補を確定情報の枠に混ぜる |
| 典型座標 | structure.representation + connection.meaning |
| 発見観点 | invariant_audit / boundary_walk |
| 解決観点 | first_class_state / explicit_contract |
| 見分け方・設計時の問い | headが無いとき未確定を表せるか。注記を付けるだけでなく確定枠と候補枠を分けているか。 |

### decision-authority

2026-09-21に過去の修正記録から抽出。

#### caller-controlled-provenance

| 項目 | 内容 |
| --- | --- |
| 一般形 | 呼出元が申告したactorを採用し、人間の操作がsystem判断を名乗れる |
| 典型座標 | governance.assignment + connection.condition |
| 発見観点 | boundary_walk / invariant_audit |
| 解決観点 | responsibility_move / explicit_contract |
| 見分け方・設計時の問い | bodyのactor_kindを変えて判断主体を偽装できないか。認証された主体と自己申告の来歴を分けているか。 |

#### review-approval-conflation

| 項目 | 内容 |
| --- | --- |
| 一般形 | 事前レビューと最終承認を区別せず、同一人物の自己承認を許す |
| 典型座標 | structure.representation + governance.review |
| 発見観点 | invariant_audit / boundary_walk |
| 解決観点 | first_class_state / explicit_contract |
| 見分け方・設計時の問い | 事前レビューの存在と最終承認者の独立性をそれぞれ検査するか。承認経路を分けても判断者が同じになっていないか。 |

#### alternate-path-gate-bypass

| 項目 | 内容 |
| --- | --- |
| 一般形 | 通常の状態遷移APIが証拠付き固定化ゲートを迂回できる |
| 典型座標 | connection.condition + governance.review |
| 発見観点 | boundary_walk / invariant_audit |
| 解決観点 | canonical_source / fail_closed |
| 見分け方・設計時の問い | 同じ確立状態へ入れる全公開経路を列挙し、証拠・承認ゲートを迂回できないか。 |

### comparison-contract

2026-09-21に過去の修正記録から抽出。

#### incomparable-measurements-ranked

| 項目 | 内容 |
| --- | --- |
| 一般形 | 指標名と測定範囲を無視し、異なる条件の測定値を同じ順位表で比較する |
| 典型座標 | connection.meaning + connection.condition |
| 発見観点 | invariant_audit / adversarial_review |
| 解決観点 | explicit_contract / carry_through |
| 見分け方・設計時の問い | p50とp95、異なるcoverageを同順位表に入れないか。測定値だけでなく比較条件も保持しているか。 |

### local-evaluation

2026-09-21に過去の修正記録から抽出。

#### unknown-direction-default

| 項目 | 内容 |
| --- | --- |
| 一般形 | 未知の評価軸に既定の大小方向を与え、不明な優劣を確定する |
| 典型座標 | processing.logic |
| 発見観点 | invariant_audit / adversarial_review |
| 解決観点 | single_point_fix / fail_closed |
| 見分け方・設計時の問い | 未知dimensionを渡したとき、勝手にhigher-is-betterにならず拒否されるか。語彙伝達の問題ではなく局所分岐の既定値が原因。 |

### context-scope

実際の作用範囲と判断材料の範囲の不一致。

#### viewport-used-for-container-layout

| 項目 | 内容 |
| --- | --- |
| 一般形 | 画面幅だけでレイアウトを選び、パネル併設時の領域縮小を見落とす |
| 典型座標 | connection.condition |
| 発見観点 | reproduction / boundary_walk |
| 解決観点 | explicit_contract / carry_through |
| 見分け方・設計時の問い | viewportを変えず隣接パネルを開いたとき、実領域に合わせて配置できるか。単なる狭幅の閾値不良と区別する。 |

### repeated-work

処理段をまたいで同じ仕事の固定費を重ねる原因。

#### repeated-resolution-connection-overhead

| 項目 | 内容 |
| --- | --- |
| 一般形 | 同一リクエストで解決済みの対象を後段で再解決し、接続開設の固定費を増やす |
| 典型座標 | structure.responsibility + connection.information |
| 発見観点 | data_inspection / reproduction |
| 解決観点 | carry_through / responsibility_move |
| 見分け方・設計時の問い | 遅延はクエリ自体か、接続開設と対象再解決か。共有範囲をリクエスト内に限定し、待機後の必要な再検証を保持できるか。 |

### knowledge-feedback

知識の登録・利用・検証から次の判断への還流を扱う。

#### classification-gates-capture

| 項目 | 内容 |
| --- | --- |
| 一般形 | 分類の確定を知識登録の開始条件と取り違え、観測済み知見を索引外に滞留させる |
| 典型座標 | governance.ordering + governance.assignment |
| 発見観点 | invariant_audit / inventory |
| 解決観点 | explicit_contract / responsibility_move |
| 見分け方・設計時の問い | 分類未確定でも根拠付きcandidateとして検索・試行できるか。単なる証拠不足による未登録とは区別する。 |

### execution-environment

隔離実行の環境が実際に使えるか、実行に要るものを揃えられるかを扱う。2026-11-03に外部検証の実施記録から追加。

#### sandbox-presence-as-capability

| 項目 | 内容 |
| --- | --- |
| 一般形 | 隔離実行ツールの存在を可用性と見なし、実環境での起動可否を事前に検査しない |
| 典型座標 | connection.condition + governance.completion |
| 発見観点 | reproduction / inventory |
| 解決観点 | fail_closed / explicit_contract |
| 見分け方・設計時の問い | ツールが存在するが権限不足で動かない環境で、診断が失敗を示すか。ツール不在時のfail closed（既存）とは検出対象が異なる。 |

#### isolated-runtime-dependency-unprovisioned

| 項目 | 内容 |
| --- | --- |
| 一般形 | 隔離実行で外部取得を禁じたまま、実行に要る依存の供給経路と担当を定めない |
| 典型座標 | connection.contract + structure.responsibility |
| 発見観点 | boundary_walk / external_constraint |
| 解決観点 | explicit_contract / responsibility_move |
| 見分け方・設計時の問い | network=falseのまま第三者ライブラリ依存の対象を、文書化された手順で再作成後も実行できるか。隔離自体が動かない問題（IK-0024）とは別に、隔離が動いても依存が無い点を問う。 |

### generated-reference

2026-11-03に外部検証（sim-sandbox E-0002）から暫定追加。

#### unanchored-reference-strict-validation

| 項目 | 内容 |
| --- | --- |
| 一般形 | 生成器に参照座標の手がかりを渡さずに座標を要求し、1件の逸脱で生成結果全体を破棄する |
| 典型座標 | connection.contract + connection.information |
| 発見観点 | reproduction / trace_walk |
| 解決観点 | carry_through / explicit_contract |
| 見分け方・設計時の問い | 生成器が座標（行番号等）を数えずに読み取れる形で入力を受け取るか。座標の逸脱1件で他の有効な結果まで捨てていないか。語彙の不一致（generation-contract-mismatch）ではなく、座標の算出に要る情報が渡らない点を問う。識別子（IK-0027）では派生形が3つ出た: (1)名前のslug化（列挙idでなく名前からidを自作）、(2)一覧表記の装飾の転写（`(kind)` を名前に写す）、(3)経路由来のモジュール接頭辞の付加（`pkg.mod.Name`、索引は `Name`）。提示形式の明示に加え、既知の変形を照合側で正規化してから落とすかを問う。 |

#### hardcoded-output-budget-diverges

| 項目 | 内容 |
| --- | --- |
| 一般形 | 同じ生成器を呼ぶ経路の一部が出力上限を共有設定でなく小さい固定値で持ち、上限内で本文が残らず空応答になる |
| 典型座標 | governance.budget + structure.aggregation |
| 発見観点 | reproduction / inventory |
| 解決観点 | canonical_source / order_and_budget |
| 見分け方・設計時の問い | 同じモデルを呼ぶ全経路が上限を同じ設定から読むか。推論に出力枠を先に使うモデルで、上限が本文に足りるか。応答の形式不一致（unanchored-reference-strict-validation）ではなく、本文自体が空である点を問う。 |

### reasoning-run-record

2026-11-06に外部検証（sim-sandbox E-0009/E-0010）から暫定追加。推論runの記録・再実行・状態の扱いに関する原因をまとめる。

#### reasoning-io-not-persisted

| 項目 | 内容 |
| --- | --- |
| 一般形 | 生成器の入出力（要求・応答・usage）を保存せず、解析後の結果と失敗文言だけを残す |
| 典型座標 | connection.information + governance.review |
| 発見観点 | reproduction / data_inspection |
| 解決観点 | first_class_state / carry_through |
| 見分け方・設計時の問い | 検証で落ちた応答について、モデルが実際に書いた値を後から読めるか。揺れの原因がプロンプトか応答かを記録だけで切り分けられるか。応答の形式不一致そのもの（unanchored-reference-strict-validation）ではなく、不一致を調べる材料が残らない点を問う。 |

#### interpretation-without-subject-meaning

| 項目 | 内容 |
| --- | --- |
| 一般形 | 解釈器に比較対象の識別子と数値だけを渡し、対象が何を変えたか（意図・差分）を渡さないので、名前から意味を推測させる。解釈は上書きされ再実行もできない |
| 典型座標 | connection.information + governance.resume |
| 発見観点 | reproduction / trace_walk |
| 解決観点 | carry_through / state_transition |
| 見分け方・設計時の問い | 解釈プロンプトに各対象の意図（risk_note・source・差分の抜粋）が入っているか。出力言語を入力（目的文）から決めて明示しているか。解釈だけを再実行でき、前回の解釈が履歴に残るか。 |

#### regenerated-identifier-drift

| 項目 | 内容 |
| --- | --- |
| 一般形 | 再生成のたびに生成器が識別子を新しく作り、前回の識別子を参照する後段（実験・計画・紐付け）と切れる |
| 典型座標 | connection.version + structure.representation |
| 発見観点 | reproduction / data_inspection |
| 解決観点 | carry_through / representation_change |
| 見分け方・設計時の問い | 再生成時に前回の識別子一覧を写せる形で生成器に渡しているか。生成後に名前の正規形で前回の識別子へ解決しているか。どの識別子を引き継ぎ、どれを新規にしたかが応答に残るか。2026-11-07以降、写させる方式は llm-assigned-stable-identifier（系の台帳とハンドル、IK-0038）に置き換えた。 |

#### llm-assigned-stable-identifier

| 項目 | 内容 |
| --- | --- |
| 一般形 | 変わってはならない識別子（後段の主キー・参照先）を生成器に作らせ、または生成器が書いた識別子を候補と照合せずに保存し、系が付番・保持していない |
| 典型座標 | structure.responsibility + governance.assignment |
| 発見観点 | inventory / trace_walk |
| 解決観点 | responsibility_move / canonical_source |
| 見分け方・設計時の問い | 利用者の規則（2026-11-07）: 識別子・個別名は系が付番・保持し、LLMはそれに紐づく内容だけを返す（または new と示す）。例外は理由を明記。生成器の出力スキーマに、保存後に主キーや参照先になる文字列・整数が含まれていないか。含まれるなら、系が渡した候補（ハンドル）への参照に置き換えたか、受け取った値を渡した候補集合と照合して落とした記録を残すか。前回の識別子を写させて照合する方式（regenerated-identifier-drift の IK-0032）は生成器が新しいidを作る余地を残すので、系の台帳（registry）とハンドルへ移す。 |

#### regeneration-drops-review-state

| 項目 | 内容 |
| --- | --- |
| 一般形 | 生成結果の再生成が人の確認状態（承認・却下）を引き継がずに新しい未確認の行で置き換え、生成段で落とした候補も記録しない |
| 典型座標 | governance.review + connection.information |
| 発見観点 | reproduction / data_inspection |
| 解決観点 | carry_through / first_class_state |
| 見分け方・設計時の問い | 再生成後も、同じ対象への人の判断が見えるか。生成器が挙げたが解決できずに落ちた候補の数と中身が応答・ログに出るか。件数の振れを「挙げなかった」と「落ちた」に分けられるか。 |

#### failure-reported-as-created

| 項目 | 内容 |
| --- | --- |
| 一般形 | 失敗を記録した結果行を作れたことを成功とみなし、生成の失敗に2xx（作成）を返す |
| 典型座標 | connection.contract + governance.completion |
| 発見観点 | doc_code_diff / boundary_walk |
| 解決観点 | explicit_contract / single_point_fix |
| 見分け方・設計時の問い | 呼び出し側がHTTPステータスだけで失敗を判定できるか。失敗の本文（記録済みの行と理由）が非2xxでも読めるか。出力の検証失敗（422）と上流の失敗（502）を区別しているか。 |

型の追加時は上記の項目を揃え、同じ変更で少なくとも1つのエントリから参照する。
単純な局所処理の不良も対象。該当例が得られる前に想像で辞書の型を増やさない。
