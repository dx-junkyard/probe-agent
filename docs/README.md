# probe-agent Documentation Map

このディレクトリは、`probe-agent` が対象システムを整理するときと同じ順序で、
このプロジェクト自身の情報を整理する。

```text
Vision / Stakeholder Value
  -> Objective / Milestone / Gap
  -> UX Journey / Requirement
  -> System Contract / Implementation Target
  -> Runtime Evidence / Evaluation / History
```

最初に「なぜ・誰のために・どの状態を目指すか」を読み、その後に「どんな体験と
能力で実現するか」、必要な場合だけ詳細な契約・検証記録・履歴へ進む。

## 最初に読む文書

1. [Vision・UX・コア機能要件](00-product/vision-and-core-requirements.md)
2. [System Understanding の理想状態](00-product/system-understanding-ideal-state.md)
3. 関心領域に対応する [システム仕様](#01-specifications--システム仕様)
4. 実装・運用時は [運用ガイド](#04-operations--運用ガイド)

## 情報の正本と読み方

同じ事実が複数文書に見える場合は、次の優先順位で扱う。

1. `01-specifications/` の該当する canonical contract
2. `00-product/` の製品方針と横断要件
3. `04-operations/` の現行手順
4. `02-challenges-and-decisions/` と `03-validation/` の時点付き記録
5. `90-history/` の過去の設計・実装ログ

上位方針と詳細仕様が矛盾する場合は、黙ってどちらかを採用せず Gap として記録する。
履歴にしか残っていない仕様を新しい実装の根拠にはしない。

## 00-product — 目指す価値と大枠

| 文書 | 役割 |
| --- | --- |
| [Vision・UX・コア機能要件](00-product/vision-and-core-requirements.md) | 製品全体の Vision、対象者、UX 原則、コア能力、横断要件の正本 |
| [System Understanding の理想状態](00-product/system-understanding-ideal-state.md) | System Understanding 領域の長期的な到達像と構造的 Gap |
| [MVP](00-product/mvp.md) | 現在までに成立した最小機能境界。将来 Vision そのものではない |

## 01-specifications — システム仕様

### Product / value lineage

| 文書 | 所有する契約 |
| --- | --- |
| [Purpose Chain](01-specifications/product/purpose-chain.md) | Problem、Change、Intervention、Capability の関係 |
| [Stakeholder Value Network](01-specifications/product/stakeholder-value-network.md) | Stakeholder、Need、Value Exchange と各 projection |
| [Product Objective Lineage](01-specifications/product/product-objective-lineage.md) | Objective、Milestone、Gap、Feature と上下流 lineage |

### UX / interaction

| 文書 | 所有する契約 |
| --- | --- |
| [UX Design Lineage](01-specifications/ux/ux-design-lineage.md) | Journey、Step、Requirement、Solution Design |
| [System Interview Workflow UX](01-specifications/ux/system-interview-workflow-ux.md) | 状態駆動のインタビュー、情報階層、復旧フロー |
| [System Understanding Navigation](01-specifications/ux/system-understanding-navigation.md) | 画面間導線、用語、次の操作、Overview projection |
| [UI Glossary](01-specifications/ux/ui-glossary.md) | 利用者向けラベル、状態語、操作ラベル |
| [共同検討UX](01-specifications/ux/decision-discussion-workflow.md) | Gapを起点にVision・UX・機能を照合し、仮説調査から設計へ還流する目標導線 |

### Core capabilities

| 文書 | 所有する契約 |
| --- | --- |
| [Evolutionary Pipeline](01-specifications/capabilities/evolutionary-pipeline.md) | 改善対象、設計・探索・固定化・監視の lifecycle |
| [Execution Modes](01-specifications/capabilities/execution-modes.md) | 実行モード、解決規則、fail-closed gate、監査 |
| [Assistant Discussion](01-specifications/capabilities/assistant-discussion.md) | 画面コンテキスト会話と変更候補化 |
| [AI Discussion Adapter](01-specifications/capabilities/ai-discussion-adapter.md) | UI adapter、未保存 draft、proposal review の拡張契約 |

### Platform / safety

| 文書 | 所有する契約 |
| --- | --- |
| [Design Notes](01-specifications/platform/design.md) | SDK、shadow、mode、永続化の基礎設計 |
| [Secret Redaction](01-specifications/platform/secret-redaction.md) | 秘密値の除去境界と漏えい時の対応契約 |

## 02-challenges-and-decisions — 課題と検討結果

| 文書 | 記録時点・用途 |
| --- | --- |
| [System Understanding UX Gap Analysis](02-challenges-and-decisions/ux-gap-analysis-system-understanding.md) | 状態矛盾・導線・用語・テスト不足の調査と改善提案 |
| [End-to-end UX Audit](02-challenges-and-decisions/end-to-end-ux-audit-2026-08-11.md) | 2026-08-11 時点の優先度付き UX 監査 |

ここにある課題は「現在も未解決」とは限らない。現行状態は Issue、コード、テスト、
canonical contract を確認する。

## 03-validation — 検証方法と証拠

| 文書 | 用途 |
| --- | --- |
| [Purpose Chain Dogfooding](03-validation/dogfooding-purpose-chain.md) | Purpose Chain の理解度検証プロトコルと結果 |
| [System Understanding Scenario](03-validation/dogfooding-system-understanding-scenario.md) | end-to-end 導線の再現可能な検証シナリオ |
| [共同検討UXの検証・引継ぎ](03-validation/decision-discussion-handoff.md) | 操作Mock、Issue分担、実装時の検証シナリオと未検証範囲 |

## 04-operations — 運用ガイド

| 文書 | 用途 |
| --- | --- |
| [AI Agent Improvement Guide](04-operations/ai-agent-improvement-guide.md) | AI エージェントが改善ループを運用する手順 |
| [Production HTTPS Deployment](04-operations/deployment-https.md) | Caddy を含む本番公開、バックアップ、復旧 |
| [GitHub App Deployment](04-operations/github-app-deployment.md) | GitHub App の登録、秘密鍵配置、ローテーション |

## 90-history — 履歴・ログ

| 文書 | 用途 |
| --- | --- |
| [Project Intelligence Journal](90-history/project-intelligence.md) | Issue ごとの設計判断・実装状態・修正追記を保存した legacy composite log |

この領域は来歴を調べるための記録であり、新しい横断仕様の追記先ではない。新しい仕様は
該当する canonical contract に置き、判断過程や変更理由だけを時点付きで残す。

## 文書を追加・更新するとき

[Documentation Governance](documentation-governance.md) に従い、文書の役割、正本、
時点、上流・下流参照を明確にする。内容を別文書へ複製せず、安定したリンクで接続する。
