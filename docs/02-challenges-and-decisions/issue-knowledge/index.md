# 課題ナレッジ索引

[入口](README.md) · [辞書](dictionary.md) · [分類体系](taxonomy.md)

> 機械生成。編集はentriesへ。`python3 docs/tools/issue_knowledge.py` で更新。
> 状態は各エントリの観測時点・対象版の記録。現行製品の健康状態や全課題の網羅率ではない。

## 概況

- エントリ: 23 / 独立原因: 23
- 状態: deferred 1、resolved 22
- 分類レビュー: candidate 22、confirmed 1
- 群: 局所 1、構造・接続・統制 22

## 全エントリ

| ID | 課題 | 状態 / 原因 / 分類 | 群 | 座標 | 観測日 |
| --- | --- | --- | --- | --- | --- |
| [IK-0001](entries/IK-0001-generation-contract-vocabulary.md) | 生成側へ有限語彙が伝わらず、表示名が保存用の値として提案される | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=contract+meaning / governance=none | 2026-09-12 |
| [IK-0002](entries/IK-0002-unmounted-form-delivery.md) | フォームの登録だけで受け渡し可能と見なし、未表示の反映先へ候補が届かない | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=target / governance=completion | 2026-09-12 |
| [IK-0003](entries/IK-0003-root-only-freshness.md) | 依存版の更新を鮮度判定へ含めず、rootが同じ古い提案を適用できる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=representation / connection=version / governance=none | 2026-09-12 |
| [IK-0004](entries/IK-0004-unobserved-user-outcomes.md) | 技術検証だけでは実利用者の操作負担と支援技術での利用成立を判断できない | deferred / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=none / governance=completion | 2026-09-12 |
| [IK-0005](entries/IK-0005-immutable-premise-overwritten.md) | 昇格結果で会話開始時の前提を上書きし、旧履歴と新前提を混在させる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=representation / connection=version / governance=none | 2026-09-13 |
| [IK-0006](entries/IK-0006-missing-post-inference-check.md) | 推論開始時だけ前提を確認し、推論中の変更後も古い結果を保存する | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=none / governance=ordering | 2026-09-13 |
| [IK-0007](entries/IK-0007-inherited-intent-dropped.md) | 現セッションの行だけで次世代を構成し、継承済みの確定情報を落とす | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=information / governance=none | 2026-09-13 |
| [IK-0008](entries/IK-0008-partial-session-creation.md) | 接続ロックをトランザクションと見なし、作成途中の例外で不完全な行を残す | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=none / governance=ordering | 2026-09-13 |
| [IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md) | 正準未確定時の代替表示が、進行中候補を確定情報の枠に混ぜる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=representation / connection=meaning / governance=none | 2026-09-13 |
| [IK-0010](entries/IK-0010-frozen-view-reads-live-data.md) | 固定した版の表示が可変行を参照し、版を変えずに内容が変わる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=version / governance=none | 2026-09-14 |
| [IK-0011](entries/IK-0011-validation-write-race.md) | 保存前再検証と書込みが別トランザクションで、検証後の競合を許す | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=none / governance=ordering | 2026-09-14 |
| [IK-0012](entries/IK-0012-candidate-state-content-mismatch.md) | 状態と本文を別々に選び、異なる候補の情報を一つに表示する | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=target / governance=none | 2026-09-14 |
| [IK-0013](entries/IK-0013-caller-controlled-provenance.md) | 呼出元が申告したactorを採用し、人間の操作がsystem判断を名乗れる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=condition / governance=assignment | 2026-08-17 |
| [IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md) | 同一キーの並行再送を通常の重複と扱えず、競合が失敗になる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=none / governance=resume | 2026-08-17 |
| [IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md) | 参照先が存在するだけで実行済みと認め、未完了runを証拠に使う | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=none / governance=completion | 2026-08-17 |
| [IK-0016](entries/IK-0016-completed-evidence-overwritten.md) | 完了後も測定値を上書きでき、承認が参照した証拠を変更する | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=condition / governance=completion | 2026-08-17 |
| [IK-0017](entries/IK-0017-incomparable-metrics-ranked.md) | 指標名と測定範囲を無視し、異なる条件の測定値を同じ順位表で比較する | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=meaning+condition / governance=none | 2026-08-17 |
| [IK-0018](entries/IK-0018-unknown-metric-direction-default.md) | 未知の評価軸に既定の大小方向を与え、不明な優劣を確定する | resolved / confirmed / candidate | 局所 | processing=logic / structure=none / connection=none / governance=none | 2026-08-17 |
| [IK-0019](entries/IK-0019-review-approval-conflation.md) | 事前レビューと最終承認を区別せず、同一人物の自己承認を許す | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=representation / connection=none / governance=review | 2026-08-17 |
| [IK-0020](entries/IK-0020-alternate-establishment-bypass.md) | 通常の状態遷移APIが証拠付き固定化ゲートを迂回できる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=condition / governance=review | 2026-08-17 |
| [IK-0021](entries/IK-0021-viewport-used-for-container-layout.md) | 画面幅だけでレイアウトを選び、パネル併設時の領域縮小を見落とす | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=none / connection=condition / governance=none | 2026-09-27 |
| [IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md) | 同一リクエストの対象再解決で接続開設の固定費を重ねる | resolved / confirmed / candidate | 構造・接続・統制 | processing=none / structure=responsibility / connection=information / governance=none | 2026-09-29 |
| [IK-0023](entries/IK-0023-classification-gates-capture.md) | 分類確定を知識登録の条件と取り違え、観測済み知見が索引外に滞留する | resolved / confirmed / confirmed | 構造・接続・統制 | processing=none / structure=none / connection=none / governance=ordering+assignment | 2026-10-04 |

## 型・族と実際の座標

### generation-contract-mismatch

族: contract-transfer / 暫定 / 分類確定の独立原因: 0

[IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- processing=none / structure=none / connection=contract+meaning / governance=none

### registered-but-unreachable

族: delivery-path / 暫定 / 分類確定の独立原因: 0

[IK-0002](entries/IK-0002-unmounted-form-delivery.md)
- processing=none / structure=none / connection=target / governance=completion

### projection-target-mismatch

族: delivery-path / 暫定 / 分類確定の独立原因: 0

[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)
- processing=none / structure=none / connection=target / governance=none

### dependency-version-omitted

族: version-lineage / 暫定 / 分類確定の独立原因: 0

[IK-0003](entries/IK-0003-root-only-freshness.md)
- processing=none / structure=representation / connection=version / governance=none

### historical-premise-overwritten

族: version-lineage / 暫定 / 分類確定の独立原因: 0

[IK-0005](entries/IK-0005-immutable-premise-overwritten.md)
- processing=none / structure=representation / connection=version / governance=none

### frozen-view-live-dependency

族: version-lineage / 暫定 / 分類確定の独立原因: 0

[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)
- processing=none / structure=none / connection=version / governance=none

### proxy-evidence-for-acceptance

族: acceptance-evidence / 暫定 / 分類確定の独立原因: 0

[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- processing=none / structure=none / connection=none / governance=completion

### unfinished-execution-as-evidence

族: acceptance-evidence / 暫定 / 分類確定の独立原因: 0

[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)
- processing=none / structure=none / connection=none / governance=completion

### finalized-evidence-mutable

族: acceptance-evidence / 暫定 / 分類確定の独立原因: 0

[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)
- processing=none / structure=none / connection=condition / governance=completion

### precheck-only-validation

族: execution-order / 暫定 / 分類確定の独立原因: 0

[IK-0006](entries/IK-0006-missing-post-inference-check.md)
- processing=none / structure=none / connection=none / governance=ordering

### multiwrite-without-atomicity

族: execution-order / 暫定 / 分類確定の独立原因: 0

[IK-0008](entries/IK-0008-partial-session-creation.md)
- processing=none / structure=none / connection=none / governance=ordering

### check-write-not-atomic

族: execution-order / 暫定 / 分類確定の独立原因: 0

[IK-0011](entries/IK-0011-validation-write-race.md)
- processing=none / structure=none / connection=none / governance=ordering

### concurrent-retry-not-idempotent

族: execution-order / 暫定 / 分類確定の独立原因: 0

[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)
- processing=none / structure=none / connection=none / governance=resume

### inherited-information-dropped

族: information-lineage / 暫定 / 分類確定の独立原因: 0

[IK-0007](entries/IK-0007-inherited-intent-dropped.md)
- processing=none / structure=none / connection=information / governance=none

### candidate-canonical-conflation

族: state-semantics / 暫定 / 分類確定の独立原因: 0

[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)
- processing=none / structure=representation / connection=meaning / governance=none

### caller-controlled-provenance

族: decision-authority / 暫定 / 分類確定の独立原因: 0

[IK-0013](entries/IK-0013-caller-controlled-provenance.md)
- processing=none / structure=none / connection=condition / governance=assignment

### review-approval-conflation

族: decision-authority / 暫定 / 分類確定の独立原因: 0

[IK-0019](entries/IK-0019-review-approval-conflation.md)
- processing=none / structure=representation / connection=none / governance=review

### alternate-path-gate-bypass

族: decision-authority / 暫定 / 分類確定の独立原因: 0

[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)
- processing=none / structure=none / connection=condition / governance=review

### incomparable-measurements-ranked

族: comparison-contract / 暫定 / 分類確定の独立原因: 0

[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)
- processing=none / structure=none / connection=meaning+condition / governance=none

### unknown-direction-default

族: local-evaluation / 暫定 / 分類確定の独立原因: 0

[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)
- processing=logic / structure=none / connection=none / governance=none

### viewport-used-for-container-layout

族: context-scope / 暫定 / 分類確定の独立原因: 0

[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)
- processing=none / structure=none / connection=condition / governance=none

### repeated-resolution-connection-overhead

族: repeated-work / 暫定 / 分類確定の独立原因: 0

[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)
- processing=none / structure=responsibility / connection=information / governance=none

### classification-gates-capture

族: knowledge-feedback / 暫定 / 分類確定の独立原因: 1

[IK-0023](entries/IK-0023-classification-gates-capture.md)
- processing=none / structure=none / connection=none / governance=ordering+assignment

## 軸・値

- `connection.condition`: [IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)
- `connection.contract`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- `connection.information`: [IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)
- `connection.meaning`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)
- `connection.none`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `connection.target`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)
- `connection.version`: [IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)
- `governance.assignment`: [IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `governance.completion`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)
- `governance.none`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)
- `governance.ordering`: [IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `governance.resume`: [IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)
- `governance.review`: [IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)
- `processing.logic`: [IK-0018](entries/IK-0018-unknown-metric-direction-default.md)
- `processing.none`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `structure.none`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `structure.representation`: [IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)
- `structure.responsibility`: [IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)

## 発見観点

- `adversarial_review`: [IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)
- `boundary_walk`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)
- `data_inspection`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)
- `doc_code_diff`: [IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)
- `invariant_audit`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `inventory`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `reproduction`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)

## 解決観点

- `canonical_source`: [IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)
- `carry_through`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)
- `deferred_decision`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `explicit_contract`: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `fail_closed`: [IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)
- `first_class_state`: [IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)
- `guardrail_fix`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)
- `order_and_budget`: [IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)
- `representation_change`: [IK-0003](entries/IK-0003-root-only-freshness.md)
- `responsibility_move`: [IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- `single_point_fix`: [IK-0018](entries/IK-0018-unknown-metric-direction-default.md)
- `state_transition`: [IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)
- `vocabulary_table`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)

## 層

- `control_server`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)
- `cycle_recording`: [IK-0023](entries/IK-0023-classification-gates-capture.md)
- `cycle_selection`: [IK-0023](entries/IK-0023-classification-gates-capture.md)
- `cycle_validation`: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- `dashboard`: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)

## 軸の組合せ

- connection + governance: 4
- connection + structure: 4
- governance + structure: 1

## レビュー待ち・仮説・未解決

- 分類候補: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)、[IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)、[IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)
- 原因仮説: なし
- unknownあり: なし
- 未解決・保留: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- [IK-0001](entries/IK-0001-generation-contract-vocabulary.md) 確信度low/medium: structure, governance
- [IK-0002](entries/IK-0002-unmounted-form-delivery.md) 確信度low/medium: structure, governance
- [IK-0003](entries/IK-0003-root-only-freshness.md) 確信度low/medium: structure, governance
- [IK-0004](entries/IK-0004-unobserved-user-outcomes.md) 確信度low/medium: structure, governance
- [IK-0005](entries/IK-0005-immutable-premise-overwritten.md) 確信度low/medium: processing, structure, governance
- [IK-0006](entries/IK-0006-missing-post-inference-check.md) 確信度low/medium: processing, structure, connection
- [IK-0007](entries/IK-0007-inherited-intent-dropped.md) 確信度low/medium: processing, structure, governance
- [IK-0008](entries/IK-0008-partial-session-creation.md) 確信度low/medium: processing, structure, connection
- [IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md) 確信度low/medium: processing, structure, governance
- [IK-0010](entries/IK-0010-frozen-view-reads-live-data.md) 確信度low/medium: processing, structure, governance
- [IK-0011](entries/IK-0011-validation-write-race.md) 確信度low/medium: processing, structure, connection
- [IK-0012](entries/IK-0012-candidate-state-content-mismatch.md) 確信度low/medium: processing, structure, governance
- [IK-0013](entries/IK-0013-caller-controlled-provenance.md) 確信度low/medium: processing, structure
- [IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md) 確信度low/medium: processing, structure, connection
- [IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md) 確信度low/medium: processing, structure, connection
- [IK-0016](entries/IK-0016-completed-evidence-overwritten.md) 確信度low/medium: processing, structure
- [IK-0017](entries/IK-0017-incomparable-metrics-ranked.md) 確信度low/medium: processing, structure, governance
- [IK-0018](entries/IK-0018-unknown-metric-direction-default.md) 確信度low/medium: structure, connection, governance
- [IK-0019](entries/IK-0019-review-approval-conflation.md) 確信度low/medium: processing, structure, connection
- [IK-0020](entries/IK-0020-alternate-establishment-bypass.md) 確信度low/medium: processing, structure
- [IK-0021](entries/IK-0021-viewport-used-for-container-layout.md) 確信度low/medium: connection
- [IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md) 確信度low/medium: structure, connection

## 同一原因の束

- IK-0001: [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)
- IK-0002: [IK-0002](entries/IK-0002-unmounted-form-delivery.md)
- IK-0003: [IK-0003](entries/IK-0003-root-only-freshness.md)
- IK-0004: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- IK-0005: [IK-0005](entries/IK-0005-immutable-premise-overwritten.md)
- IK-0006: [IK-0006](entries/IK-0006-missing-post-inference-check.md)
- IK-0007: [IK-0007](entries/IK-0007-inherited-intent-dropped.md)
- IK-0008: [IK-0008](entries/IK-0008-partial-session-creation.md)
- IK-0009: [IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)
- IK-0010: [IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)
- IK-0011: [IK-0011](entries/IK-0011-validation-write-race.md)
- IK-0012: [IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)
- IK-0013: [IK-0013](entries/IK-0013-caller-controlled-provenance.md)
- IK-0014: [IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)
- IK-0015: [IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)
- IK-0016: [IK-0016](entries/IK-0016-completed-evidence-overwritten.md)
- IK-0017: [IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)
- IK-0018: [IK-0018](entries/IK-0018-unknown-metric-direction-default.md)
- IK-0019: [IK-0019](entries/IK-0019-review-approval-conflation.md)
- IK-0020: [IK-0020](entries/IK-0020-alternate-establishment-bypass.md)
- IK-0021: [IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)
- IK-0022: [IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)
- IK-0023: [IK-0023](entries/IK-0023-classification-gates-capture.md)

## 収録出典と範囲

以下は収録済みの出典のみ。未収録文書や課題数を分母にした被覆率ではない。

- [docs/01-specifications/ux/decision-discussion-audit-2026-09-12.md](../../01-specifications/ux/decision-discussion-audit-2026-09-12.md): [IK-0001](entries/IK-0001-generation-contract-vocabulary.md)、[IK-0002](entries/IK-0002-unmounted-form-delivery.md)、[IK-0003](entries/IK-0003-root-only-freshness.md)、[IK-0004](entries/IK-0004-unobserved-user-outcomes.md)
- [docs/02-challenges-and-decisions/ux-audit-2026-09-26.md](../../02-challenges-and-decisions/ux-audit-2026-09-26.md): [IK-0021](entries/IK-0021-viewport-used-for-container-layout.md)
- [docs/03-validation/improvement-cycle/2026-09-29-assistant-answer-quality.md](../../03-validation/improvement-cycle/2026-09-29-assistant-answer-quality.md): [IK-0022](entries/IK-0022-repeated-resolution-connection-overhead.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- [docs/03-validation/improvement-cycle/2026-10-04-autonomous-knowledge-loop.md](../../03-validation/improvement-cycle/2026-10-04-autonomous-knowledge-loop.md): [IK-0023](entries/IK-0023-classification-gates-capture.md)
- [docs/90-history/canonical-understanding-review-2026-09-13.md](../../90-history/canonical-understanding-review-2026-09-13.md): [IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)
- [docs/90-history/project-intelligence.md](../../90-history/project-intelligence.md): [IK-0005](entries/IK-0005-immutable-premise-overwritten.md)、[IK-0006](entries/IK-0006-missing-post-inference-check.md)、[IK-0007](entries/IK-0007-inherited-intent-dropped.md)、[IK-0008](entries/IK-0008-partial-session-creation.md)、[IK-0009](entries/IK-0009-candidate-as-canonical-fallback.md)、[IK-0010](entries/IK-0010-frozen-view-reads-live-data.md)、[IK-0011](entries/IK-0011-validation-write-race.md)、[IK-0012](entries/IK-0012-candidate-state-content-mismatch.md)、[IK-0013](entries/IK-0013-caller-controlled-provenance.md)、[IK-0014](entries/IK-0014-concurrent-idempotency-conflict.md)、[IK-0015](entries/IK-0015-unfinished-execution-as-evidence.md)、[IK-0016](entries/IK-0016-completed-evidence-overwritten.md)、[IK-0017](entries/IK-0017-incomparable-metrics-ranked.md)、[IK-0018](entries/IK-0018-unknown-metric-direction-default.md)、[IK-0019](entries/IK-0019-review-approval-conflation.md)、[IK-0020](entries/IK-0020-alternate-establishment-bypass.md)

## 軸・値の提案

提案なし。語彙不足と調査不足を分けてレビューする。

## 暫定値の再評価

暫定の追加値なし。

## 改善サイクルへの還流

層と発見月の分布は点検の入口。型の複数出現だけで「解決後の再発」と断定しない。

- サイクル層: [IK-0004](entries/IK-0004-unobserved-user-outcomes.md)、[IK-0023](entries/IK-0023-classification-gates-capture.md)
- 2026-08 / boundary_walk: 2
- 2026-08 / invariant_audit: 5
- 2026-08 / reproduction: 1
- 2026-09 / adversarial_review: 4
- 2026-09 / boundary_walk: 3
- 2026-09 / data_inspection: 2
- 2026-09 / doc_code_diff: 1
- 2026-09 / invariant_audit: 3
- 2026-09 / reproduction: 1
- 2026-10 / invariant_audit: 1
