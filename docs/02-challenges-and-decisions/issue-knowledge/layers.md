# 場所の語彙

[入口](README.md) · [許可値の正本](taxonomy.md)

分類の原因とは別に、どの実装・改善段で起きたかを記録する。

| 値 | 範囲 |
| --- | --- |
| sdk | packages/python-probe の観測・policy・shadow |
| control_server | apps/control-server のAPI・永続化・推論 |
| dashboard | apps/dashboard の画面・状態・操作 |
| shared_contract | shared/schemas と実装間の契約 |
| deployment | deploy・起動・バックアップ・復旧 |
| documentation | docsの正本・導線・文書管理 |
| cycle_discovery | 課題を見つける段 |
| cycle_selection | 対象と改善手順を選ぶ段 |
| cycle_design | 契約・受入条件を設計する段 |
| cycle_implementation | 実装する段 |
| cycle_validation | 証拠を集めて検証する段 |
| cycle_correction | レビュー指摘を是正する段 |
| cycle_recording | 結果・知識・次の判断を記録する段 |

新しい機能ごとに層を増やさない。必要ならここで範囲を定義してtaxonomyの許可値を更新する。
