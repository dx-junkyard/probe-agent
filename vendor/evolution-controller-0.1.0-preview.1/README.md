# Evolution Controller Starter 0.1.0-preview.1

目的・状況に応じて、課題発見・解決・検証の観点を選び、その結果から知識と改善手順を更新するための試用パッケージ。人またはエージェントが文書を読み、手動で運用する。自動検索器・常駐エージェントは含まない。個別プロジェクトの資料を必要とせず利用できる。

## 導入

1. 配布ZIPと同じ配布版の `release_tool.py` を受け取り、Python 3.10以降で `python3 release_tool.py verify evolution-controller-0.1.0-preview.1.zip` を実行する。検証は破損・同梱漏れの検出であり、署名による発行者認証ではない。
2. 空の一時ディレクトリへZIPを展開する。新規プロジェクトなら、展開された `evolution-controller-0.1.0-preview.1` ディレクトリ全体をプロジェクト内の `vendor/` に置く。同名の既存ディレクトリへ上書きしない。
3. `improvement/local/` と `improvement/records/` を導入先に作る。`templates/project.md` を `improvement/local/project.md` へコピーし、対象、資料の所在、検証方法、権限、還元範囲を記入する。空欄は権限付与を意味しない。
4. 導入先の既存エージェント入口から、本パッケージの [エージェント用入口](AGENTS.md) を参照する。入口自体を置き換えず、通常の設定変更として追記する。人が使う場合は本READMEから始める。
5. [最小利用例](examples/pipeline.md)を読み、実際の小さな課題一つで次の流れを試す。

文書の利用はMarkdownを読める環境のみで可能。Pythonは配布物の検査に使用する。ネットワーク・外部サービス・特定のリポジトリ構造は不要。

## 最小の一巡

1. [状況の表し方](situation-model.md)を使い、[状況カード](templates/situation.md)を `improvement/records/` に作る。目的、操作、期待と実際、証拠、不明点を先に残す。
2. [観点パック](knowledge/perspectives.md)から、構成・操作に関係する候補を拾う。必須条件が未確認なら「要観測」、矛盾すれば「非適合」とし、似ているだけで適用しない。
3. [改善手順](workflow.md)に従い、診断・解決・検証・停止を一組にして[提示記録](templates/guidance-feedback.md)へ記す。未解決なら原因を分ける追加観測から始める。
4. 許可された作業を実施し、[サイクル記録](templates/cycle.md)へ結果と未確認を残す。例の架空の結果を実績に転記しない。
5. 観点が外れた、足りなかった、負担が大きかった場合は、[自己改善](self-improvement.md)で変更案と比較結果を残す。

全欄の入力を毎回要求しない。既存の計画・課題・検証記録を安定IDで参照し、正本を重複させない。

## ファイルの所有と更新

- `vendor/evolution-controller-<版>/`：配布元の内容。manifestとともに保持し、直接編集しない。
- `improvement/local/`：導入先の配置対応・独自語彙・手順拡張。導入先が所有する。
- `improvement/records/`：状況・事例・選択・適用結果。導入先が所有する。

配布版、状況形式 `situation/0.1`、語彙 `roles/0.1`、観点パック `perspectives/0.1`、手順 `workflow/0.1` を各記録に残す。資料の取得版と観測版は別に記録する。

更新と既存運用からの移行は [互換性・更新方針](COMPATIBILITY.md)を参照。本版は新規の並置導入向け。既存台帳の置換や自動移行は行わない。

## 配布範囲と根拠

[来歴と限界](PROVENANCE.md)、[変更履歴](CHANGELOG.md)、[利用範囲](NOTICE.md)を参照。これは試用可能な手順の配布であり、効果確認済みの解決策集ではない。
