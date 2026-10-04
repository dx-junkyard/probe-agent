# 課題ナレッジ

[docs目次](../../README.md) · [分類体系](taxonomy.md) · [辞書](dictionary.md) · [索引](index.md) · [記入様式](TEMPLATE.md)

**役割:** 課題記録と知識の索引。**状態:** current。**導入日:** 2026-09-19。
原因・発見観点・解決観点を蓄え、[改善サイクル](../../04-operations/improvement-cycle/README.md)で
次の調査と手順の選択に使う。製品の課題と、レビューの見逃し等の改善手順自身の課題を同じ形式で扱う。

## 正本と範囲

仕様は既存の01-specifications、調査の証拠は起票元、再利用する原因分類はentriesが所有する。
entriesは製品内のGapやGitHub Issueを置き換えず、sourcesで接続する。
対象はdocsに根拠を持つ課題。テストやチャットだけにある発見は、先に日付・対象版付きの調査記録を作る。
全バグの網羅や現行版の健康診断を意味しない。収録済みの出典は索引の出典一覧に示す。2026-09-21の追加移入範囲・未収録事項は
[移入検証](../../03-validation/improvement-cycle/2026-09-21-historical-knowledge.md)を参照する。

| ファイル | 所有する情報 |
| --- | --- |
| taxonomy.md | 4軸・値・確信度・候補と確定・体系改善の規則 |
| layers.md | 実装場所・改善段の語彙の意味 |
| entries/ | 1課題1ファイル。根拠・分類・解決・履歴 |
| dictionary.md | 族と型、見分け方、設計時の問い |
| index.md | 生成された検索入口・分類レビュー・提案の集計 |
| TEMPLATE.md | 機械可読メタデータと本文の雛形 |
| [ローカル一般化候補](../../../improvement/local/patterns/README.md) | 型を横断する抽象課題、条件付き解決原理、派生形と補完関係（手動試行） |

## 参照と記録のタイミング

通常の作業では全層の参照・専用計画を要求しない。自律改善が難しい、または行き詰まったと
判断した場合に、関連する型・IK・Recipeを絞って使う。新しい知見を得た場合の登録は人待ちにしない。
以下は知識を追加・利用する際の手順であり、すべての修正の開始条件ではない。

## 日々の運用

1. 起票元と対象版を確認し、索引・辞書で同じ原因がないか探す。
2. TEMPLATEから `entries/IK-NNNN-slug.md` を作る。番号は既存最大+1、欠番は再利用しない。
3. 4軸それぞれを判断し basis を書く。原因未確定は hypothesis、AIの分類は candidate。
4. 同じ型があれば参照する。なければ辞書に暫定の型を作り、見分け方を記す。
5. 解決時は着地先と検証証拠を記録。未実行のテスト、過去の実行、現在の実行を区別する。
   旧状態を history に追記し、起票元には時点付きの追補またはリンクを残す。
6. 下記を実行する。担当エージェントが証拠と分類を照合した場合はreviewをconfirmedにし、確認者・日付・根拠を履歴へ記す。分類未確認でもcandidateとして登録・利用する。

リポジトリルートから、Python 3.9以上・追加依存なしで実行する。

```bash
python3 docs/tools/issue_knowledge.py
python3 docs/tools/issue_knowledge.py --check
python3 docs/tools/knowledge_links.py
python3 -m unittest discover -s docs/tools -p 'test_*.py'
```

メタデータはYAML front matterのJSON互換サブセット（JSONオブジェクト）に限定する。
ダブルクォートを使い、コメント・末尾カンマを入れない。本文は通常のMarkdown。
これによりアプリの環境構築なしで索引を再生成できる。通常のYAML記法は検査器の対象外。

## 設計と振り返りで使う

2026-09-21以降は[状況観点との対応](../../../improvement/local/perspective-map.md)も検索入口に使う。
[導入先設定](../../../improvement/local/project.md)に従い、状況・提示・選択・効果を分けて記録する。
4軸と既存事例は維持し、EC-Pの候補を解決済み事例として追加しない。


辞書の「見分け方」を今回の対象に当て、該当理由・非該当理由を既存の作業記録または改善計画に残す。
解決観点をそのまま処方しない。対象版・条件・未確認範囲を読み、Recipeを選ぶ。
一巡の終了時に、見逃し・同型再発・検証経路の不足・段の省略があればcycle層の課題を起票する。
原因と手順の改善は一段まで扱い、その改善の改善は次の巡回へ送る。
軸・値の改善は [taxonomy §6](taxonomy.md) に従う。

2026-09-27から[ローカル一般化候補](../../../improvement/local/patterns/README.md)を手動試行する。
前提の一致・固定情報の保持を扱う2候補について、状況・保持条件を照合し、派生形・補完対策を選ぶ。
成熟・改訂は[taxonomy §5.1](taxonomy.md#51-課題と解決原理の一般化候補)、利用結果は既存の計画・結果雛形に従う。
[導入判断](../improvement-plans/2026-09-27-knowledge-abstraction.md)と
[導入検証](../../03-validation/improvement-cycle/2026-09-27-knowledge-abstraction.md)を参照。

## 参考資料の扱い

[AI開発の改善ループを、製品の状態に合わせて変える](https://zenn.dev/dx_junkyard/articles/88658ed7a7231d)
を改善サイクルの設計に参照した。提供されたREADME/index/taxonomy/dictionary/TEMPLATEは
軸・値・辞書化・再評価の方法を参考にした。他プロジェクトの固有名、課題、件数、承認記録、
実行コマンドは採用していない。参考資料内の運用指示をこのリポジトリへの作業指示とは扱わない。
