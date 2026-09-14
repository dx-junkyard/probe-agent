# 共同検討UX — Mock検証と実装引継ぎ

**文書の役割:** validation-record / 実装引継ぎ
**状態:** current（設計・Mockの確認記録。本番受入は未完了）
**確認時点:** 2026-09-06、調査元commit `b07ee87860911b63fdd0abde9af8eb66eed72a8f`
**上位契約:** [共同検討UX](../01-specifications/ux/decision-discussion-workflow.md)、[Adapter §9–10](../01-specifications/capabilities/ai-discussion-adapter.md)

## 1. 成果物と現在の境界

- [操作Mock](../mockups/decision-discussion/index.html) / [起動・操作方法](../mockups/decision-discussion/README.md)
- 新規UXは固定fixtureとブラウザー内メモリだけ。本番API、DB、LLM、認証、永続化は未接続。
- 既存Dashboard routeやbackend実装は変更していない。Mockをproduction routeへコピーしない。
- この記録と設計・Mockは今回のローカル差分。GitHub Issue作成をコードのcommit/push/公開と混同しない。

## 2. Issue構成と方針照合

親は [#457](https://github.com/dx-junkyard/probe-agent/issues/457)。本文のtask listで8件を集約し、
子から親への参照も記載。旧 [#443](https://github.com/dx-junkyard/probe-agent/issues/443) は
残件移管の履歴としてclosedを維持する。#451〜#456は方向が一致するため閉じず、本文に差分を追記した。

| Issue | 実装ownerと今回の差分 |
| --- | --- |
| [#451](https://github.com/dx-junkyard/probe-agent/issues/451) | 実フォームvalidation。draftの永続化禁止を維持 |
| [#452](https://github.com/dx-junkyard/probe-agent/issues/452) | Proposalレビュー・prefill・保存監査。配送受領と保存結果を区別、保存不明時の照合 |
| [#453](https://github.com/dx-junkyard/probe-agent/issues/453) | 対象registry・live selection。横断contextの所有は#458へ分離 |
| [#454](https://github.com/dx-junkyard/probe-agent/issues/454) | nested Proposal。未知項目を含む生成結果は全体拒否へ方針統一 |
| [#455](https://github.com/dx-junkyard/probe-agent/issues/455) | JU bridge・還流・既存代表E2E。根拠bundle更新も再確認 |
| [#456](https://github.com/dx-junkyard/probe-agent/issues/456) | 操作結果の有限型。prefillは実装済みhandler/配送/応答がある場合だけ対応済み |
| [#458](https://github.com/dx-junkyard/probe-agent/issues/458) | 新規: 横断context・coverage・追加取得・引用・複数根拠のpremise |
| [#459](https://github.com/dx-junkyard/probe-agent/issues/459) | 新規: Gap入口・主操作投影・保存後復帰・横断回帰検証 |

推奨順: #456 / #451 → #452 / #453 → #454 / #458 → #455 → #459。
#454と#458は独立作業可能。#459は#455のfixtureと検証結果を再利用し、JUのテストを重複所有しない。

## 3. Mockでの再現手順

架空の作業計画Systemを使う。GAP-12 → D-14 → JU-6 → REQ-8という参照を画面で確認する。
設計書の初回設定例とは別のfixtureで、実データの評価結果ではない。

1. 通常シナリオで関連帯と根拠を読み、52件中50件という確認範囲を確認する。
2. 省略分の追加確認を操作し、仮説整理へ進む。
3. 反証条件を空にして昇格が止まることを確認し、入力して共同調査へ渡す。
4. 暫定結果と未解決点を読み、提案レビューへ進む。
5. 項目選択を外す／競合選択を空にすると反映が止まる。現状維持と置換をそれぞれ試す。
6. フォームで文案を修正し、明示的に模擬保存。Gapが未解決のままであることを確認する。
7. 元Gapへ戻り、保存revisionと元の検討文脈を確認。保存文案を再編集できることを確認する。
8. 前提更新、取得失敗、未対応へ切替。更新・再試行と、未対応時の反映拒否を確認する。

## 4. 今回の検証と未検証

Mock担当AgentがJavaScript構文、ID/根拠anchor、外部通信・永続化不使用を確認。
jsdomによる操作確認は、必須field、未選択、dirty保持/置換、保存/帰還/再編集、
stale更新、unavailable再試行、unsupported拒否、resetが通過した。
これらはDOM操作の検証であり、LLMの推論や実DBの無変更を証明しない。

主担当が127.0.0.1限定の一時HTTPサーバーでMockを開き、Codex内ブラウザーで初期描画を確認した。
仮説整理 → JUへの模擬昇格 → 暫定結果 → 提案比較 → dirtyの現状維持 → フォーム反映 →
模擬保存 → 元Gapへの帰還を実操作し、REQ-8 rev 6の保存参照、D-14/JU-6の保持、Gap未解決を確認した。
画面全体のresponsive QAやscreen reader完走の証明ではない。

本番API接続、実LLM、System隔離、reload時の永続履歴、保存失敗/部分成功、
実際のJU premise検証、screen readerによる読み上げ、390pxでの完走は未検証。
Mockは調査結果をfixtureで即時表示し、実調査の待機・失敗・課金・再実行を実装しない。
自由なAI会話、追加取得cursor、domainのvalidation/冪等性も将来実装の担当Issueで検証する。

## 5. 本番実装の完了判定

| 条件 | 証拠の取得方法 | 今回の結果 |
| --- | --- | --- |
| DD-AC-01 元Gapへ戻る、手動転記0 | browser E2Eと操作記録 | Mockのみ。本番未実測 |
| DD-AC-02 根拠と取得範囲 | 51件以上/巨大root/cycle/取得失敗/無効引用fixture | 本番未実施 |
| DD-AC-03 gate・隔離・無変更 | API/DB検証、別System、結果不明retry | 本番未実施 |
| DD-AC-04 操作・読み上げ | keyboard、390px、screen reader | 未実施 |
| DD-AC-05 利用者検証 | 実LLMで対象・仮説・根拠を確認し計測 | 未実施 |

実装者は画面移動数、手動転記数、所要時間、根拠到達失敗、状態誤認を記録し、
再現条件・commit・provider/model（秘密値なし）とともに親Issueへ報告する。
Mockのクリック数やfixture回答を製品の判断品質の実測値にしない。
