# Documentation Governance

この文書は、`docs/` が再び「仕様・議論・実装報告の混ざった時系列ファイル」へ戻らないための
管理規則を定める。

## 1. 文書の役割を一つ選ぶ

| Role | 配置先 | 内容 |
| --- | --- | --- |
| `product-direction` | `00-product/` | Vision、対象者、提供価値、UX 原則、横断要件 |
| `canonical-contract` | `01-specifications/` | entity、状態、API、gate、受入条件など現行仕様 |
| `decision-or-gap-record` | `02-challenges-and-decisions/` | 課題、選択肢、決定理由、未解決事項。必ず時点を持つ |
| `validation-record` | `03-validation/` | 再現手順、期待結果、実測結果、制約 |
| `guide-or-runbook` | `04-operations/` | 導入、操作、deploy、backup、recovery |
| `historical-journal` | `90-history/` | 過去の時系列ログ。現行仕様の正本にしない |

一つの文書が複数 role を持ち始めたら分割する。特に canonical contract に実装日誌を追記せず、
history に現行仕様を追加しない。

## 2. 情報をコピーせず lineage でつなぐ

- 各概念の定義は owning document に一度だけ書く。
- 他文書は短い文脈と安定したリンクを持つ。仕様本文を複製しない。
- 上流は「なぜ」、下流は「どう実現するか」を所有する。下流が上流の意味を再定義しない。
- 要件には安定した ID を付け、仕様・テスト・検証記録から参照する。

## 3. 現在状態と履歴を分ける

- 現行仕様は現在形で書き、変更理由や廃止経緯は decision/history へ移す。
- 監査、検証結果、課題調査には日付または対象 revision を明記する。
- 過去の記録は上書きして現在風に直さない。訂正文または superseded の参照を追加する。
- 「実装済み」という時点依存情報は、可能ならテストや Issue を正本とし、文書には確認時点を付ける。

## 4. 状態を曖昧にしない

新規文書の冒頭には、必要に応じて次を記載する。

```markdown
**文書の役割:** canonical contract | product direction | validation record | ...
**状態:** current | draft | superseded | historical
**対象:** 対象 capability / Epic / 運用範囲
**確認時点:** YYYY-MM-DD または commit / snapshot
**上位方針:** リンク
**下位仕様・検証:** リンク
```

`draft` を current specification として参照しない。`historical` の内容を再利用するときは、現行コードと
canonical contract に照らして再検証する。

## 5. 変更時のチェック

文書を追加・更新する前に確認する。

1. この情報を所有する既存文書はないか。
2. これは方針、現行仕様、判断過程、検証証拠、手順、履歴のどれか。
3. 上流の目的と、下流の仕様・テストへリンクできるか。
4. AI 提案、システム導出、人間判断を区別しているか。
5. `unknown`、`unavailable`、`stale`、`superseded` を単なる欠落にしていないか。
6. 時点依存の記述に日付または revision があるか。
7. `docs/README.md` の索引を更新したか。

## 6. 既存の複合ログの扱い

[`90-history/project-intelligence.md`](90-history/project-intelligence.md) は、多数の Issue の設計、
実装状況、修正履歴が一つに蓄積された legacy composite log である。過去の来歴を保つため削除・
全面改稿はしないが、今後の新しい横断仕様の正本にはしない。

既存セクションを変更する必要がある場合は、まず該当する `01-specifications/` の正本を更新し、
ログには変更理由、対象 Issue、正本へのリンクだけを残す。正本がまだない領域では、必要な契約を
独立文書へ抽出してから実装を進める。
