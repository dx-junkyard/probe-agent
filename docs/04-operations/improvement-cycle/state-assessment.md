# 根拠付き製品状態の評価

[改善サイクル](README.md) · [計画様式](PLAN_TEMPLATE.md)

**役割:** 状態評価の運用契約。**状態:** current。**確認時点:** 2026-09-19。
原因分類とは独立に「目的に対して現在どんな性質が成立しているか」を見る。
各観測は評価対象・性質・判断・根拠・未確認範囲・対象版・観測日時を持つ。

| 評価軸 | 問い | probe-agentで確認する例 |
| --- | --- | --- |
| functional_coverage（機能充足） | 目的の操作を最後まで行えるか | Gapから要件へ反映・保存・復帰できるか |
| reliability（信頼性） | 障害や再試行でも期待結果を保つか | API失敗・再配送・候補の例外 |
| observability（観測可能性） | 根拠・失敗・未観測を区別できるか | mock/実LLM、Trace、出典と停止理由 |
| boundary_consistency（境界整合性） | 主体・対象・契約が境界で保たれるか | System scope、生成enum、フォーム宛先 |
| state_consistency（状態整合性） | 版と状態遷移が一貫するか | canonical head、premise、古いProposalの拒否 |
| governance（統制） | 判断担当・上限・完了条件が明確か | manual gate、API予算、採否の記録 |
| operability（運用可能性） | 導入・停止・復旧できるか | backup/restore、秘密値除去、接続断 |
| changeability（変更容易性） | 契約と影響範囲を追って変更できるか | 正本への参照、共通語彙、回帰検査 |

判断値は `supported`（指定条件で成立する証拠あり）、`gap`（不足の証拠あり）、
`unknown`（未観測または根拠不足）、`not_applicable`（対象外の理由あり）。
対象版や利用条件が変われば以前の観測を上書きせず、新しい観測を追加する。
現在版へ適用できない古い観測は `freshness: stale`、今回確認した対象版なら `current`、
版との対応自体が不明なら `unknown`。supportedでもstaleなら現在良好とは扱わない。

| 必須項目 | 記載内容 |
| --- | --- |
| observation_id / target / axis | 安定したID、具体的な対象、上表の1軸 |
| judgment / freshness | 判断と、その判断が今回の対象へ適用可能か |
| evidence | 参照と節、コマンド・実測結果。unknownなら根拠がない理由 |
| unobserved | 未確認条件。空なら確認範囲を根拠で説明 |
| target_revision / observed_at | SHAまたは識別可能な版、観測日時。特定不能ならその旨 |

全8軸を毎回測る必要はない。選ばない軸も未評価と明示し、全体評価へ広げない。
単一スコア・平均値に畳まない。新しい軸を提案するときは、既存軸で説明できない判断の差と
具体的な観測例を計画に記し、意味・観測方法・既存軸との境界をレビューしてからこの表を改訂する。
