# 課題ナレッジ索引

[入口](README.md) · [辞書](dictionary.md) · [分類体系](taxonomy.md)

> 機械生成。編集はentriesへ。`python3 docs/tools/issue_knowledge.py` で更新。
> 状態は各エントリの観測時点・対象版の記録。現行製品の健康状態や全課題の網羅率ではない。

## 概況

- エントリ: 4 / 独立原因: 4
- 状態: deferred 1、resolved 3
- 分類レビュー: candidate 4
- 群: 構造・接続・統制 4

## 全エントリ

| ID | 課題 | 状態 / 原因 / 分類 | 群 | 座標 | 観測日 |
| --- | --- | --- | --- | --- | --- |
| [IK-0001](entries/IK-0001-generation-contract-vocabulary.md) | 生成側へ有限語彙が伝わらず、表示名が保存用の値として提案される | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=contract+meaning / governance=none | 2026-09-12 |
| [IK-0002](entries/IK-0002-unmounted-form-delivery.md) | フォームの登録だけで受け渡し可能と見なし、未表示の反映先へ候補が届かない | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=target / governance=completion | 2026-09-12 |
| [IK-0003](entries/IK-0003-root-only-freshness.md) | 依存版の更新を鮮度判定へ含めず、rootが同じ古い提案を適用できる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=representation / connection=version / governance=none | 2026-09-12 |
| [IK-0004](entries/IK-0004-unobserved-user-outcomes.md) | 技術検証だけでは実利用者の操作負担と支援技術での利用成立を判断できない | deferred / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=none / governance=completion | 2026-09-12 |

## 型・族と実際の座標

### generation-contract-mismatch

族: contract-transfer / 暫定 / 分類確定の独立原因: 0

[IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- processing=none / structure=none / connection=contract+meaning / governance=none

### registered-but-unreachable

族: delivery-path / 暫定 / 分類確定の独立原因: 0

[IK-0002](entries/IK-0002-unmounted-form-delivery.md)
- processing=none / structure=none / connection=target / governance=completion

### dependency-version-omitted

族: version-lineage / 暫定 / 分類確定の独立原因: 0

[IK-0003](entries/IK-0003-root-only-freshness.md)
- processing=none / structure=representation / connection=version / governance=none

### proxy-evidence-for-acceptance

族: acceptance-evidence / 暫定 / 分類確定の独立原因: 0

[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- processing=none / structure=none / connection=none / governance=completion

## 軸・値

- `connection.contract`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- `connection.meaning`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- `connection.none`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `connection.target`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)
- `connection.version`: [IK-0003](entries/IK-0003-root-only-freshness.md)
- `governance.completion`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `governance.none`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)
- `processing.none`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `structure.none`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `structure.representation`: [IK-0003](entries/IK-0003-root-only-freshness.md)

## 発見観点

- `adversarial_review`: [IK-0003](entries/IK-0003-root-only-freshness.md)
- `boundary_walk`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)
- `data_inspection`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- `invariant_audit`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `inventory`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `reproduction`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)

## 解決観点

- `carry_through`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)
- `deferred_decision`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `explicit_contract`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)
- `guardrail_fix`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- `representation_change`: [IK-0003](entries/IK-0003-root-only-freshness.md)
- `vocabulary_table`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)

## 層

- `control_server`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)
- `cycle_validation`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `dashboard`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)

## 軸の組合せ

- connection + governance: 1
- connection + structure: 1

## レビュー待ち・仮説・未解決

- 分類候補: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- 原因仮説: なし
- unknownあり: なし
- 未解決・保留: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- [IK-0001](entries/IK-0001-generation-contract-vocabulary.md) 確信度low/medium: structure, governance
- [IK-0002](entries/IK-0002-unmounted-form-delivery.md) 確信度low/medium: structure, governance
- [IK-0003](entries/IK-0003-root-only-freshness.md) 確信度low/medium: structure, governance
- [IK-0004](entries/IK-0004-unobserved-user-outcomes.md) 確信度low/medium: structure, governance

## 同一原因の束

- IK-0001: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- IK-0002: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)
- IK-0003: [IK-0003](entries/IK-0003-root-only-freshness.md)
- IK-0004: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)

## 収録出典と範囲

以下は収録済みの出典のみ。未収録文書や課題数を分母にした被覆率ではない。

- [docs/01-specifications/ux/decision-discussion-audit-2026-09-12.md](../../01-specifications/ux/decision-discussion-audit-2026-09-12.md): [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)

## 軸・値の提案

提案なし。語彙不足と調査不足を分けてレビューする。

## 暫定値の再評価

暫定の追加値なし。

## 改善サイクルへの還流

層と発見月の分布は点検の入口。型の複数出現だけで「解決後の再発」と断定しない。

- サイクル層: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- 2026-09 / adversarial_review: 1
- 2026-09 / boundary_walk: 1
- 2026-09 / data_inspection: 1
- 2026-09 / invariant_audit: 1
