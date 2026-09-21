# 状況の表現 — situation/0.1、roles/0.1

状況は「仕組みの種類 → 部品」の階層に、関係、目的、操作、時点を重ねて表す。原因が分かる前から使い、未知を一つの確定状態に押し込まない。

| 層 | 記録例 |
|---|---|
| 仕組みの種類 | pipeline / agent / other。複数可。これは検索の入口 |
| 目的・保持条件 | 最新化する、人の判断を保持する、予算内で完了する |
| 部品の役割 | source（入力元）、transform（変換）、store（保存）、projection（派生表示）、planner（計画）、executor（実行）、evaluator（評価）、memory（記憶）、human_decision（人の判断）、external_service（外部サービス） |
| 接続・構造 | reads / writes / derives_from / approves / depends_on / shares。分岐、合流、循環、共有対象も記録 |
| 操作・変化 | initial_run / incremental_update / rerun / concurrent_run / approve_then_apply / migrate |
| 条件と観測 | 対象版・依存版、途中失敗、既存データ、権限、費用、症状と順序、証拠・観測時点 |

役割は製品名・実装名と分ける。一つの部品が複数の役割を担ってもよい。未知の部品は `local:<役割>` として説明を添える。共通語彙に無理に寄せない。

例：`generator(transform) --writes--> artifact(store)`、`review(human_decision) --depends_on--> artifact`、`screen(projection) --derives_from--> artifact`。

「画面で消えた」から「保存データが削除された」と推定で確定しない。証拠が支持する事実、解釈、未観測、該当なしを別に残す。矛盾は出典・時点を保ち確認対象にする。解決後の原因は発見時の状況と分ける。

パターンの照合結果は **適合候補 / 要観測 / 非適合**。条件の記載がないことを汎用的な適合としない。役割と接続が似ていても、目的・保持条件・実行権限が違えば支援を変える。

状態表現に項目を増やすのは、その違いが診断・解決・検証・停止判断を変える場合。区別できなかった二例と必要な観測を変更提案に添える。
