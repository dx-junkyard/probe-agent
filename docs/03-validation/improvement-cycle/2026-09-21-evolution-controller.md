# IR-20260921-02: Evolution Controller 0.1.0-preview.1 導入検証

**役割:** validation-record。**日付:** 2026-09-21。
**対象:** probe-agent `3530943` + 今回の導入差分。
**実行担当・環境:** コーディングエージェント / ローカルPython 3.13.3。
**計画:** [IP-20260921-02](../../02-challenges-and-decisions/improvement-plans/2026-09-21-evolution-controller.md)。
**状況・提示:** [S-20260921-01](../../../improvement/records/2026-09-21-install-situation.md)、[G-20260921-01](../../../improvement/records/2026-09-21-install-guidance.md)。

## 取り込み内容と境界

配布版 `0.1.0-preview.1`、状況 `situation/0.1`、語彙 `roles/0.1`、観点 `perspectives/0.1`、手順 `workflow/0.1`。
ローカル接続 `probe-agent-ec/0.1`。

- `vendor/evolution-controller-0.1.0-preview.1/`: 配布文書16件とmanifestを原文・原バイトで保持。
- `vendor/releases/0.1.0-preview.1/`: ユーザー指定のZIP・README・SHA256SUMS・manifest・検証CLI・個別checksumの6ファイルを保持。
- [ローカル設定](../../../improvement/local/project.md): 資料対応、権限、安定ID、5雛形の利用先、更新・復旧を定義。
- [観点対応](../../../improvement/local/perspective-map.md): 6候補観点を既存IK・Recipeへ接続。適合の限界も明記。
- [取得台帳](../../../improvement/records/2026-09-21-source-ledger.json): 既存20事例と23依存ファイルの取得版・SHA-256・分析範囲。過去の観測版は別に保持。
- CLAUDE.mdとdocsの入口、改善サイクル、計画・結果雛形へ接続。旧IP/IRと旧事例は書き換えない。

添付文書の手順は仕様として検討し、ユーザーの取り込み依頼の範囲でローカル導入した。
自動収集・自動検索・常駐実行・製品APIへの組込みは配布物に含まれず、今回も追加していない。
配布例は架空であり、IKへ実績として取り込んでいない。

## 実測

発見・選別・設計・文書実装・検証・記録を実施。製品修正と製品E2Eは対象外。

| 受入条件 | 実行・方法 | 結果・限界 |
| --- | --- | --- |
| 受領物の完全性 | SHA256SUMSの3ファイルと個別ZIP checksumをhashlibで照合 | 一致。署名による発行者認証ではない |
| 同梱内容・来歴 | 同梱release_tool.pyのverifyを実行、ZIP内外manifestを照合 | 16文書検証成功。manifestを含め17ファイル |
| 展開後の保持 | ZIPの一覧とvendor全ファイル一覧を比較し、内容をバイト比較 | 追加・欠落・改変なし |
| 導入元リポジトリへの非依存 | ZIPとCLIだけを一時ディレクトリへコピーしてverify | 成功。元プロジェクトを実行時に参照しない |
| 旧知識の保持 | git show 3530943の内容と20事例・taxonomy・dictionary・indexをバイト比較 | 23ファイルすべて一致 |
| 取得台帳の一致 | 20事例と23依存ファイルのSHA-256を再計算 | 一致。依存ファイル本文の再監査を意味しない |
| 索引の整合 | `python3 docs/tools/issue_knowledge.py --check` | `OK: 20 entries; index synchronized` |
| 検査器の回帰 | `python3 -m unittest discover -s docs/tools -p 'test_*.py'` | 11件成功 |
| 導線 | 新規Markdownと変更したMarkdownの追加行の相対リンクをローカルで照合 | 不存在0件。外部URL・アンカーは検査対象外 |
| 差分形式 | `git diff --check` | 成功 |

配布検証の再現コマンド（リポジトリルートから）:

```bash
python3 vendor/releases/0.1.0-preview.1/release_tool.py verify vendor/releases/0.1.0-preview.1/evolution-controller-0.1.0-preview.1.zip
```

展開先の改変・欠落・追加も検査する場合:

```python
from pathlib import Path
from zipfile import ZipFile
package = 'evolution-controller-0.1.0-preview.1'
base = Path('vendor') / package
with ZipFile(Path('vendor/releases/0.1.0-preview.1') / (package + '.zip')) as archive:
    expected = {n.removeprefix(package + '/') for n in archive.namelist()}
    actual = {p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()}
    assert actual == expected
    for name in expected:
        assert (base / name).read_bytes() == archive.read(package + '/' + name)
```

## 結果と次の判断

導入の受入条件は成功。OBS-EC-01の接続不足は今回差分で解消し、OBS-EC-04として
changeability / supported / current（ローカルの文書導線と完全性に限定）とする。
OBS-EC-03の一般的な解決効果はunknownのまま。

EC-P04の所有分離をローカル設定へ、EC-P02の版一致を展開内容の検証へ具体化した。
提示・選択・検証の記録は一巡したが、製品課題への効果確認・Recipeの独立成功例には数えない。
既存20件の分類と全EC観点はcandidateのまま。LR-001/LR-002の実績を増やしていない。

記録負担を抑えるためcycleテンプレートと既存IP/IRを二重管理せず、安定IDで参照した。
この選択による時間短縮は未計測。次の実開発では見逃し・誤適合・不足する検証・記録負担を別々に測る。
手順自体の変更は旧版と候補版、問題のない例と失敗例、調整外の例で比較し、根拠と採否を残す。
今回はvendorの内容を改訂していない。

外部API・本番操作・外部送信なし。時間・レビュー負担は未計測。停止・復旧なし。
後発の見逃しは確認時点で報告なし。再開条件は実際の改善課題の着手で、担当はその開発担当。
正式な効果認定の担当は未割当であり、この導入を効果確定とは扱わない。
復旧時は入口の新版参照を外し、既存手順へ戻す。使用版・取得台帳・結果は保持する。
