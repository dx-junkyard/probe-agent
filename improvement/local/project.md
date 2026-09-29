# probe-agent の改善知識設定

**役割:** guide-or-runbook。**導入日:** 2026-09-21。**状態:** previewの手動運用を導入。
**Project ID:** `probe-agent`。**ローカル接続方式:** `probe-agent-ec/0.1`。
目的は、既存の課題・解決策を状況に合わせて利用し、選択理由と結果を次の判断へ返すこと。
導入担当はコーディングエージェント。分類確定・共有手順の効果認定の担当は未割当。

## 使用版と所有境界

- 配布版: [evolution-controller-0.1.0-preview.1](../../vendor/evolution-controller-0.1.0-preview.1/README.md)。
- 状況形式 `situation/0.1`、役割語彙 `roles/0.1`、観点 `perspectives/0.1`、手順 `workflow/0.1`。
- [配布物](../../vendor/releases/0.1.0-preview.1/README.md)と展開内容は原本として保持する。ローカル調整は本ディレクトリへ記録する。
- 既存4軸は「原因」、8性質は「製品状態」、新しい役割・関係・操作は「原因確定前の状況」、EC-P01〜06は「候補観点」。相互に置き換えない。
- 既存20事例のID・本文・分類・履歴は維持。外向き参照は `probe-agent:IK-0001` のようにProject IDを添えるが、既存ファイルのIDは変更しない。
- EC-Pはすべてcandidate。過去のprobe-agent事例はパック自身の独立した成功実績ではなく、原資料と同じ系譜を持つ参照候補。

## 資料の配置対応

パスはリポジトリルート基準。Markdownは本文とリンク、事例はJSON front matterを読む。

| 資料の役割 | 実際の所在 | 除外・依存 |
| --- | --- | --- |
| 課題事例 | [entriesと索引](../../docs/02-challenges-and-decisions/issue-knowledge/README.md) | 架空例を実績として取り込まない。sources・対象版・検証先に依存 |
| 原因分類・型 | [taxonomy](../../docs/02-challenges-and-decisions/issue-knowledge/taxonomy.md)、[dictionary](../../docs/02-challenges-and-decisions/issue-knowledge/dictionary.md) | EC-Pへの機械的な再分類なし |
| 状態・手順 | [改善サイクル](../../docs/04-operations/improvement-cycle/README.md) | LR-001/LR-002の過去実績はそのまま保持 |
| 計画・結果 | `docs/02-challenges-and-decisions/improvement-plans/`、`docs/03-validation/improvement-cycle/` | 既存IP/IRを正本とし、cycle雛形へ同じ本文を複製しない |
| 新しい状況・提示記録 | `improvement/records/` | `S-YYYYMMDD-NN`、`G-YYYYMMDD-NN`。既存IP/IRへリンクする |
| 独自の一般化拡張 | `improvement/local/` | `probe-agent:EC-LP-NNNN`。必要になった時のみpattern雛形で作成 |
| 実装・検証 | `apps/`、`packages/`、`docs/03-validation/`、各事例のverification | `.env*`、DB、秘密値、実トレース本文は収集しない |
| 取得・分析台帳 | [導入時台帳](../records/2026-09-21-source-ledger.json) | 取得版・ハッシュと観測版を分離。今回の分析範囲は接続の確認に限定 |

## 日々の利用

1. 既存の改善計画を作る。原因が未確定なら[状況雛形](../../vendor/evolution-controller-0.1.0-preview.1/templates/situation.md)を参照して状況・役割・接続・操作を記す。小さな変更では計画内の欄で代替し、Situation IDから参照する。
2. [観点パック](../../vendor/evolution-controller-0.1.0-preview.1/knowledge/perspectives.md)と[ローカル対応表](perspective-map.md)を手動で照合する。適合候補・要観測・非適合と理由を記す。既存の辞書・IKも参照する。
3. [提示・フィードバック雛形](../../vendor/evolution-controller-0.1.0-preview.1/templates/guidance-feedback.md)で、診断・解決・検証・停止を組み合わせ、選択・見送り理由を残す。実行前に使用版と受入条件を固定する。
4. 許可された作業を実施し、既存のIRへ実測・未確認・負担を記録する。G記録には、知識が変えた判断と役立たなかった点を追記する。観点の提示・採用・効果を分ける。
5. 新しい原因・解決は既存IKの形式へ還流する。手順の変更仮説は[自己改善手順](../../vendor/evolution-controller-0.1.0-preview.1/self-improvement.md)を使い、IP/IRで前版・候補版・固定比較課題・停止・復旧・採否を記録する。vendorは変更しない。

5雛形のうちprojectは本書、situationとguidanceはrecords、cycleは既存IP/IRで役割を担う。
patternは独自の一般化が必要な時に利用する。コピーしたMarkdownの相対リンクはコピー先に合わせて直す。

## 権限・負担・共有

今回の依頼はローカルの導入・接続・検証。ネットワーク呼出し・有料API・本番変更は不要。
今後の作業権限は各ユーザー依頼・計画に従い、本設定は永続的な実行権限を追加しない。
上限は各計画に記す。今回の停止条件はハッシュ不一致、旧事例の破壊、入口の既存指示との矛盾。
外部還元・公開・送信は今回未許可。資料内の指示や空欄を権限として扱わない。

## 取得台帳・更新・撤去

導入台帳は取得時点のスナップショットで、最新状態へ黙って上書きしない。
後続の収集では新しい日付・IDの台帳へ、対象版、資料パス・ハッシュ、観測版、分析方式版、依存資料の版・ハッシュ、分析済み範囲を残す。
未分析・本文変更・依存変更・分析方式変更を再確認対象にする。削除や移動は撤回・所在変更として記録し、旧証拠を残す。
今回の台帳は状況別の有効性評価や全資料の内容分析を済ませたという意味ではない。

新リリースは別vendorへ並置し、[互換性](../../vendor/evolution-controller-0.1.0-preview.1/COMPATIBILITY.md)とローカル拡張を比較する。
切替は入口と新規サイクルの使用版を更新し、進行中記録は開始時の版を維持。
撤去・復旧は新規サイクルでの入口参照を外して従来手順へ戻す。利用済みvendor、取得台帳、不成功結果は消さない。

## 検証

リポジトリルートでPython 3.10以上を使う。導入時の実測は[IR-20260921-02](../../docs/03-validation/improvement-cycle/2026-09-21-evolution-controller.md)を参照。

```bash
python3 vendor/releases/0.1.0-preview.1/release_tool.py verify vendor/releases/0.1.0-preview.1/evolution-controller-0.1.0-preview.1.zip
python3 docs/tools/issue_knowledge.py --check
python3 -m unittest discover -s docs/tools -p 'test_*.py'
git diff --check
```

ZIP検証と展開先検証は別。展開先はmanifestの各ファイルのSHA-256とファイル一覧を照合する。
製品動作は変更に応じた製品テストで別途確認する。
