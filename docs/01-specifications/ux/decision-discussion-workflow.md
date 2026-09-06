# Vision・UX・機能を照合する共同検討UX

**文書の役割:** canonical-contract（横断する対話・画面遷移の所有者）
**状態:** current（目標契約。実装完了を意味しない）
**対象:** Gapを起点とした照合、仮説調査、設計修正への還流
**上位方針:** [Vision・UX・コア機能要件](../../00-product/vision-and-core-requirements.md) UX-1/4/6、CAP-3/4/7/8
**下位契約:** [AI Discussion Adapter](../capabilities/ai-discussion-adapter.md)、[Assistant Discussion](../capabilities/assistant-discussion.md)
**検証・引継ぎ:** [検証シナリオ](../../03-validation/decision-discussion-handoff.md)、[操作Mock](../../mockups/decision-discussion/index.html)

## 1. 利用者が達成すること

「このGapを埋めるUXと機能は、目的に寄与するか。何を調べれば判断できるか」を、
前提や証拠を画面間で転記せずに検討する。AIは照合・説明・仮説・変更候補を作り、
人間が調査への移行、保存、価値判断を行う。機能やTraceの存在だけで価値の達成を確定しない。

代表例は「初回利用者が設定を完了できない」というGap。Visionは自力で利用を開始できること、
Objectiveは初回設定の完了、Journeyは設定手順、Requirementは復旧可能な入力案内、
Featureは入力検証・再開支援。入力検証の実装があっても、途中離脱の理由は未確認であり、
案内不足と権限不足を競合仮説として扱う。これは検討用の架空データである。

## 2. 既存画面への配置と文脈

新しいdomain workspaceや第二の正本は作らない。Objective MapのGap Workbenchを入口にし、
OverviewのGapからも同じ対象へ移動できる。既存Assistantに「目的・UX・機能を照合」を追加する。

- 主領域: 選択Gapの現在状態・目標状態・根拠と、Vision → Objective → Milestone → Gap →
  Journey → Requirement → Feature → Solution / 実装対象 → Evidenceの関連帯。
- 対話領域: 照合結果、根拠、仮説、次の主操作。詳細証拠・診断は展開する。
- レビュー領域: 既存Proposalを項目別に確認し、既存フォームへ移動・反映する。
- 狭い画面: Gap概要 → 現段階の主操作 → 対話 → 関連・証拠の順。全幅の巨大グラフを要求しない。

関連帯は直列の強制階層ではなく、既存lineageの表示順である。複数の関係・欠落・対象外を
表現し、存在しない中間entityを補完しない。詳細を開いても検討起点のGapへの戻り先を保持する。
対象を変えて議論するときは対象別threadへ切り替える。同一entityでも別画面のthreadは
従来どおり別identityとし、履歴を結合しない。戻り先は参照だけを持ち、draft本文をURLに入れない。

## 3. 状態と一つの主操作

DD-UX-01: 次の状態はUI投影の状態であり、Gap lifecycle / JU outcome / Proposal statusを置換しない。
serverが既存ownerの結果から `next_action` と対象・実行可否・理由を返す。
clientは同じ判定表を本番コードへ複製しない。Mockだけは固定fixtureで模擬する。

| 段階 | 表示内容 | 主操作 | 完了時の境界 |
| --- | --- | --- | --- |
| 対象選択 | Gapと上流目的、関連先、確認時点 | 目的・UX・機能を照合 | read-only調査・AI解釈。domain無変更 |
| 照合結果 | 事実／推論／仮説／不明／矛盾、取得範囲 | 仮説を整理する | 未解決の問いをfield値へ変換しない |
| 仮説レビュー | 競合説明、反証条件、次の調査、証拠 | 選択した仮説を調査へ送る | 明示操作でJU sessionとlineageのみ作成 |
| 調査中 | 状態、保持済み成果、起点へのリンク | 調査を開く | navigate。pollで再実行しない |
| 調査結果 | finding、outcome、premise、暫定ラベル | 変更候補を整理する | currentな根拠でProposalを生成 |
| 提案レビュー | 現在値・提案値・理由、項目選択、競合 | 選択項目をフォームへ反映 | 未保存draftとprefill監査のみ |
| フォーム編集 | dirty、validation、元提案との関係 | 内容を保存する | 既存domain APIによるmanual保存 |
| 保存後 | revision、保存した項目、残った問い | Gapの検討へ戻る | 再取得・再照合。Gap解消は別の人間判断 |

DD-UX-02: 調査を必要としない内容修正は、照合結果から既存Proposalレビューへ進める。
全会話に仮説作成を強制しない。未対応操作は押せる成功ボタンにせず理由と可能な移動を表示する。

DD-UX-03: 問いは「この機能で十分か」の自由文に加え、どのGap・どの判断を対象とするかを表示する。
AIの出力は短い結論、根拠と不確実性、次の一操作の順。取得範囲の仕様はAdapter §9が所有する。

## 4. 調査と設計への還流

DD-UX-04: 仮説の型・昇格・premise評価はAdapter §6 / JUが所有する。画面は競合説明、
反証条件、次の調査の欠落を個別表示する。送信時にもserverが検証し、欠落時は全操作を拒否する。
読み取り調査と、Replay / Experiment等の実行は別。JUへの昇格が実験実行・承認を代行しない。

DD-UX-05: 結果が暫定なら「仮説を暫定採用」と表示し、confirmed pointへ移さない。
起点Gap、元thread/turn、仮説、JU session、finding、Proposalへの参照をたどれる。
field修正の根拠に暫定仮説を使った場合も、Proposalにその不確実性を保持する。

DD-UX-06: 保存はProposalの採用・Gap解消・Milestone達成を意味しない。
prefill履歴と保存結果を別に表示する。保存後は保存されたrevision参照だけを記録・再取得し、
未保存本文を監査へ複製しない。複数フォームの一括保存は初期スコープ外。
一部のみ保存された場合は保存済みと未保存を項目ごとに示し、成功済みを再送しない。

## 5. 失敗・並行編集・再開

DD-UX-07: 以下は成功シナリオと同じ画面で扱う。警告を閉じただけでgateを解除しない。

| 条件 | 表示・復旧 | 禁止すること |
| --- | --- | --- |
| rootまたは根拠revision更新 | 変更された参照を示し、最新情報で再照合 | 古い提案の反映、旧turnの事実としての自動継承 |
| 一部source取得失敗 | 読めた範囲を残す。失敗sectionだけ再取得 | 0件・機能不存在への変換 |
| 上限による省略 | 未取得件数または件数不明と終了理由、追加取得 | 全件確認済みという回答 |
| 仮説項目欠落 | 欠落fieldへfocus、入力後再送 | 不完全な仮説のJU昇格 |
| dirty競合 | field単位に現状維持／提案へ置換を選択 | 暗黙上書き・未選択field変更 |
| 保存失敗 | draft保持、errorと再試行。結果不明なら先に保存状態を再取得 | 成功と断定・重複revision追加 |
| System切替 | そのSystemのthread再取得、draft境界をリセット | 別Systemの履歴・draft・finding継承 |
| 対象削除／権限なし | 参照不可を表示し安全な一覧へ戻る | 別対象へ自動置換・他Systemの存在開示 |
| reload | 永続thread/Proposal/JUと保存結果を復元 | 未保存draft・draft引用回答の復元を約束 |

前提照合は表示時だけでなく昇格・prefill・保存の各境界で行う。依存参照のdigestが変わった場合も
再確認対象とする。操作ごとの冪等性と保存結果の照合は既存ownerに実装する。

## 6. 所有範囲・実装境界

| 所有者 | 担当 |
| --- | --- |
| 本書 / 統合UX issue | 画面配置、起点への戻り先、次の一操作、通し導線の受入 |
| Adapter §9 / context issue | boundedな関連参照、取得範囲、引用、根拠の前提bundle |
| #453 | 対象registry、live selection、resolver/context/deep link |
| #456 | 操作結果の有限状態、実際のhandlerに基づくcapability |
| #451 / #452 | draft validation、Proposalレビュー、競合、prefillと保存結果の接続 |
| #454 | child/list/受入条件のProposal schemaと二重validation |
| #455 | hypothesis/JU bridge、還流、既存の代表E2E |

新APIの目標形はAdapter §9。新規永続化は実装issueでmigration・System隔離・互換性を揃えて追加する。
この設計・Mockの引継ぎでは本番route/API/DB/LLM呼出しを追加しない。

## 7. 受入条件

DD-AC-01: 代表Gapから目的・UX・機能・証拠を確認し、仮説をJUへ送り、結果からRequirementの
受入条件を修正・保存して同じGapへ戻れる。対象名・仮説・証拠参照の手動転記は0回。

DD-AC-02: 事実／推論／仮説／不明／矛盾と取得範囲を各段階で確認できる。
関係未登録を機能不存在と断定しない。出典クリックで該当entity/revisionへ到達できる。

DD-AC-03: stale・unavailable・unsupported・dirty競合・保存失敗・別SystemでDD-UX-07を満たす。
prefillだけではdomain rowが変わらず、保存後もGapとMilestoneの判断軸が変わらない。

DD-AC-04: キーボードだけで主導線を完走し、390px幅で主要操作に横スクロールを要求しない。
状態は色だけに依存せず、操作結果を読み上げ通知し、screen readerで起点・対象・次の操作が分かる。

DD-AC-05: 本番E2Eと実LLM dogfoodingはMock検証から分離して記録する。画面移動数、手動転記数、
所要時間、根拠到達失敗、状態の誤認を実測する。未実測を0や成功として報告しない。
