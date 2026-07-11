# System Understanding UX ギャップ調査と改善提案

Status: 調査・提案のみ（実装は未着手）。
Scope: 「Build / Refresh 実行後に次に何をすべきか分からない」問題の根本原因の特定と、
ダッシュボード全体の同種の UX ギャップの棚卸し・改善提案。

## 1. 結論（要約）

報告された画面（Pipeline は `Capability hierarchy ready = complete`、上部警告は
「capability 階層を生成してください」、さらに `Define System Purpose` カード）は
**表示バグ 1 件と状態モデルの構造問題 2 件が重なった結果**であり、いずれも
コード上で再現経路を特定済み。

1. **フロントのキャッシュ無効化漏れ（表示バグ）** — ビルド完了時に警告バナーの
   データソース `/system-state` だけが再取得されない。
2. **「capability 階層がある」の判定基準が 2 系統ある（構造問題）** — 実行完了
   ベース（`intelligence_runs`）とノード件数ベース（`capability_hierarchy_nodes`）
   が別モジュールで別々に判定され、「実行は完了したが capability が 0 件」のとき
   恒常的に complete と警告が同時に出る。
3. **「次の一歩」の導出が 2 系統ある（構造問題）** — `primary_action`
   （`system_understanding_service._derive_primary_action`）と
   `SystemStateBanner`（`system_state.select_primary_item`）が別のアルゴリズムで
   同一画面に同時表示される。設計ドキュメント自体が「将来 #193 に統合、今はしない」
   と明記している未統合箇所。

したがって「警告バナーとチェックリストは同じ状態モデルから表示すべき」という
先行調査の指摘は正しく、その実現には**フロント修正だけでなくバックエンドの
状態モデル統合（Issue #193 系）が必要**。

## 2. 添付画面の矛盾の根本原因

### 2.1 直接原因 A: `system-state` のキャッシュ無効化漏れ

ビルド完了検知時の再取得は 2 つの query key のみ:

- `apps/dashboard/src/pages/system-understanding.tsx:484-492` —
  `sysKey("system-understanding")` と `sysKey("system-diagnostics")` を invalidate。
  警告バナー（`SystemStateBanner`）のソースである `sysKey("system-state")` は
  invalidate されない。
- `sysKey("system-state")` の invalidate は `useCreateSnapshot`
  （`apps/dashboard/src/api/hooks.ts:373`）の 1 箇所のみ。
  `useBuildSystemUnderstanding` / retry / cancel では行われない。
- `useSystemState` は `staleTime: 30_000`（`hooks.ts:348-355`）。

結果: ビルド完了直後、チェックリストは新状態（complete）、バナーはビルド前の
古い状態（「生成してください」）を表示する。

### 2.2 直接原因 B: 「階層あり」判定の 2 系統（0 件 complete 問題）

- Pipeline checklist / `system_state` の pipeline 項目は
  **実行完了ベース**: `intelligence_runs` に `run_type='capability_hierarchy'`
  かつ `status='completed'` の行があれば complete
  （`system_understanding_service.py:264-279`、`system_state.py:899-920`）。
- 一方、警告系（`system_state.evaluate_understanding` →
  `understanding.capabilities` 項目、`system_diagnostics._check_system_capabilities`）
  は **ノード件数ベース**: 現 snapshot の `capability_hierarchy_nodes` で
  `node_type='capability'` が 0 件なら `missing_baseline` →
  「Core Capabilities が未定義です」警告（`system_state.py:459-503, 597-614`）。

`capability_hierarchy` の生成は `probe-agent:` docstring メタデータ由来のみ
（`capability_hierarchy.py:208-273`）のため、メタデータ未付与のリポジトリでは
「実行は completed、capability は 0 件」が正常系として発生し、
**キャッシュが新鮮でも complete と警告が恒常的に同時表示される**。
これが添付画面の最有力の再現経路。

補足の不整合経路:

- **snapshot に symbol が 0 件**のとき `_run_capability_hierarchy` は
  `intelligence_runs` への insert 前に early return する
  （`system_understanding_jobs.py:1332-1334`）が、build step は completed に
  マークされる。BuildJobPanel（job step 由来）と Pipeline checklist
  （`intelligence_runs` 由来）が同一ビルドで矛盾しうる。
- `documentation_indexed` は chunk 0 件で `warning` に落とす特別処理がある
  （`system_understanding_service.py:159-203`）が、`capability_hierarchy_ready`
  に同等の処理がない（0 件でも緑の complete）。
- **baseline 再利用の非対称**: `/system-state` は Interview で確認済みの
  Purpose/Capabilities を snapshot 跨ぎで再利用する
  （`system_state.py:200-327`）が、Hub の
  `_load_purpose` / `_load_capabilities`（`system_understanding_service.py:296-329`）
  は現 snapshot のみ参照。新 snapshot 直後は Hub 側だけ purpose 未定義扱いになる。
- 完了済み run は LLM 設定が後から mock/未設定に変わっても complete のまま
  （`system_state.py:899-920` は行が無いときだけ `blocked_by_reasoning` を評価）。

### 2.3 `Define System Purpose` は設計どおりだが文脈説明がない

pipeline 全 complete かつ purpose 未定義のとき `Define System Purpose`
（link=/interview）を出すのは意図された挙動でテストもある
（`system_understanding_service.py:822-844`、
`tests/test_system_understanding.py:1256-1268, 1384-1396`）。
問題は挙動でなく提示で、「解析は完了した。次はこれ。理由と所要時間はこう」
という成功文脈なしに、警告バナーと並んで出るため「まだ何か壊れている」ように読める。

### 2.4 「次の一歩」導出の 2 系統併存

- `system_understanding_service._derive_primary_action`
  （`system_understanding_service.py:942-990`）→ `PrimaryActionCard`。
- `system_state.select_primary_item`（`system_state.py:989-1034`）→
  `SystemStateBanner` と ヘッダーの `DiagnosticsBadge`。

前者は baseline 非対応、後者は対応、優先順位付けも別。docstring と
`docs/system-understanding-navigation.md`（Issue #201 節）自身が
「将来 `system_state.py`（#193）へ統合する。本 issue ではしない」と明記しており、
今回の症状はこの未統合の負債が表面化したもの。

なお `refresh-recommended-banner` には既に「canonical バナーと同一原因なら
自分は出ない」抑制がある（`system-understanding.tsx:552-553`）。同じ抑制原則を
全カード/バナーに広げるのが統合の方向性。

## 3. その他の UX ギャップ（画面横断調査）

### System Understanding 画面内

- **Build を起動するコントロールが最大 4 つ同時に並ぶ**: ヘッダーの
  Build / Refresh、`PrimaryActionCard`（build kind）、checklist の step CTA、
  `refresh-recommended-cta`。すべて同じ `build.mutate()`。
- ~~**`Generate capability hierarchy` Next Action の行き止まりリンク**~~
  【訂正】当初「Capability Map は閲覧専用で生成操作がない」と報告したが誤り。
  Capability Map には `Generate capability hierarchy` ボタンが実在する
  （`capability-map.tsx:568, 615`、`docs/project-intelligence.md` にも記載）。
  `link="/capability-map"` は機能する導線であり、行き止まりではない。
  checklist の step CTA（全体 Build）と Capability Map の個別生成という
  2 経路が併存する点は #206/#207 の一本化で扱う。
- **汎用「修正する」ボタン**: `system_state.py:1162`（`action_label="修正する"`）、
  `system-state.tsx:44`（fallback `対応する`）、`diagnostics-badge.tsx:384`。
  遷移先が押す前に分からず、`target_ui=null` の項目では見た目が同じボタンが
  ダイアログを開くなど挙動も分岐する。
- **ヘッダーの DiagnosticsBadge と画面内バナーの重複**: 同じ根本原因が
  別 dedupe ロジックで 2 箇所に出て、件数・文言が食い違いうる。
- **build 実行中は `primary_action=None`（rule 2）**だが、実行中 job が別
  snapshot に pin されている場合でも現 snapshot の未完了状態が隠れる。

### 画面横断

- **Overview が新規ユーザーの行き止まり**（`overview.tsx:71-74`）:
  ログイン直後の画面に Repository / System Understanding / Connect SDK への
  導線が一切ない。
- **Probe Planner に上流ゲートがない**（`probe-planner.tsx:79-94, 372-383`）:
  Feature Map 未生成時は自由入力の feature id でプラン生成でき、
  System Purpose / capability / entrypoint と無関係なプランが作れてしまう。
  意図された「capability 確認 → flow 選択 → plan 作成」順序の最大の抜け道。
- **Capability Map の詳細パネル内 gap リンクが `?capability=` を失う**
  （`capability-map.tsx:379-388`）: すべて素の `/system-understanding` に戻り、
  ユーザーは同じ gap を探し直す。ナビ設計ドキュメントの
  「`?capability=` は途切れない navigation context」原則に違反。
- **Feature Map の空状態に前提条件への導線がない**
  （`feature-map.tsx:108, 148, 235`）: Capability Map の空状態
  （`capability-map.tsx:611-646`、PrerequisiteChecklist あり）と非対称。
- **Connect SDK → Setup Guide が一方通行**（`setup-guide.tsx:416` は
  connect-sdk へリンクするが逆方向がない）。

### 表示言語

i18n 機構は不在（i18next 等の import ゼロ）。言語の境界は
**どのバックエンドモジュールが文字列を生成したかに一致**している:

- 日本語: `system_state.py` / `system_diagnostics.py` の全メッセージ、
  Interview ページ全体、Setup Guide ページ全体、
  `system-understanding.tsx:558-561` のインライン日本語バナー。
- 英語: pipeline checklist、`system_understanding_service.py` の
  NextAction 文言、Capability Map / Flow Explorer / Probe Planner 等。

結果として同一画面内で「Capability hierarchy ready — complete」（英）と
「…capability 階層を生成してください。」（日）が並ぶ。

### ドキュメント負債

- CLAUDE.md の「Repository / Feature Map / Probe Planner / Experiments タブは
  explicit mocks」という記述は実装より古い。4 ページとも実エンドポイントを
  呼んでおり、`is_mock` バッジは LLM 応答単位の provenance 表示に変わっている。

### テスト不足

- 「run completed だが capability 0 件」「symbol 0 件で step completed かつ
  `intelligence_runs` 行なし」のシナリオは
  `test_system_state.py` / `test_system_diagnostics.py` /
  `test_system_understanding_jobs.py` のいずれにもない。

## 4. 改善提案（優先順位つき・実装は別途）

### P1 — 状態矛盾の解消（単一の状態モデル）

1. **即効修正**: ビルド settle 時に `sysKey("system-state")` も invalidate する
   （`system-understanding.tsx:484-492`）。retry / cancel の mutation も同様。
2. **0 件 complete の解消**: `capability_hierarchy_ready` に
   `documentation_indexed` と同じ「completed だが 0 件 → `warning` + 理由」
   パターンを導入し、`system_state._capability_hierarchy_item` /
   `system_diagnostics` と判定条件を共有する。symbol 0 件の early return は
   `intelligence_runs` 行を残す（または step を completed にしない）よう修正。
3. **「次の一歩」導出の一本化（Issue #193 統合の実施）**:
   `_derive_primary_action` を `system_state` へ統合し、
   `SystemStateBanner` / `PrimaryActionCard` / DiagnosticsBadge が
   同一の canonical item から表示する。矛盾時は具体的な失敗内容を持つ側のみ表示
   （`refresh-recommended-banner` の既存抑制パターンを一般化）。

### P2 — 成功後の「次にすること」を一つに絞る

4. 全 pipeline complete 時は**成功サマリ（8/8 完了・symbols/entrypoints 件数）
   ＋ 主 CTA 1 枚**を最上部に。`Define System Purpose` を主 CTA とする場合は
   「なぜ必要か / 完了すると何ができるか / 所要目安」を添える
   （データは既存 `metadata_coverage` / `stages` で賄える）。
5. **Build / Refresh をヘッダーの保守操作に降格**し、build 起動コントロールを
   主 CTA + ヘッダーの 2 つまでに減らす。
6. **完了済み Pipeline Status は折りたたむ**（8/8 のとき collapsed、
   失敗・警告時のみ該当行を展開）。
7. ~~`Generate capability hierarchy` Next Action を `action_kind="build"` に修正~~
   【取り下げ】§3 の訂正どおり Capability Map に生成操作が実在するため、
   navigate リンクは妥当。変更しない。
8. 汎用「修正する」を廃し、対象＋操作の具体ラベル（例:
   「Capability 階層を再生成する」）と原因表示に置き換える。
9. **Purpose 未定義の間は Start from Capability / Feature を補助導線に降格**
   し、「目的を定義した後に利用できます」を明示する。

### P3 — 画面横断の導線

10. Overview に zero-state の get-started 導線（Repository 設定 / Connect SDK /
    System Understanding）を追加。
11. Probe Planner に上流ゲート（capability hierarchy / entrypoint 由来の選択を
    前提にし、自由入力 feature id は明示の escape hatch に降格）。
12. Capability Map 詳細パネルの gap リンクに `?capability=` を付与。
13. Feature Map 空状態に PrerequisiteChecklist 相当を追加。
14. Connect SDK → Setup Guide の順方向リンクを追加。

### P4 — 言語ポリシー

15. 表示言語を 1 つに決め（現状の利用者に合わせるなら日本語）、
    固有概念は初出のみ併記（例: システム目的（System Purpose））。
    バックエンド発の文言（`system_state` / `system_diagnostics` / NextAction）を
    同一言語に揃えることが先決。フロントの i18n 基盤導入はその後でよい。

### 付随タスク

- CLAUDE.md の「explicit mocks」記述の更新。
- `docs/system-understanding-navigation.md` への状態モデル統合方針の追記。
- §3 テスト不足シナリオの回帰テスト追加。

## 5. 提案する issue 分割

| # | 内容 | 対応提案 |
| --- | --- | --- |
| 1 | 状態矛盾の解消（cache invalidate + 0 件 complete + primary 一本化 = #193 統合） | P1-1〜3 |
| 2 | Build 成功後の単一 CTA 化と Pipeline 折りたたみ | P2-4〜9 |
| 3 | 画面横断導線（Overview / Probe Planner ゲート / リンク欠落） | P3-10〜14 |
| 4 | 表示言語の統一 | P4-15 |
| 5 | ドキュメント・テスト追随 | 付随タスク |

1 と 2 は同じ画面を触るが、1 はバックエンド状態モデル、2 は表示・情報設計であり
レビュー観点が異なるため分割を推奨。着手順は 1 → 2 → 3 →（4, 5 は並行可）。
