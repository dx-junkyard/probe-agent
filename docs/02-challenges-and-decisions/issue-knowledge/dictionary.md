# 課題の型の辞書

[入口](README.md) · [索引・成立状態](index.md) · [分類規則](taxonomy.md)

族→型の2段。下記は初期の暫定型。成立状態は索引が人の分類確定と独立原因数から導出する。
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

型の追加時は上記の項目を揃え、同じ変更で少なくとも1つのエントリから参照する。
単純な局所処理の不良も対象。該当例が得られる前に想像で辞書の型を増やさない。
