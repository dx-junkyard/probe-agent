# IR-20260929-01: AIアシスタントの回答品質と応答時間（Epic #467）

**役割:** validation-record。**日付:** 2026-09-29。
**対象:** 基準版 `50bdd05`（origin/main）と PR #474 の変更版（branch `ura`）。
**実行担当・環境:** コーディングエージェント（統括 1、実装サブエージェント）／コンテナ Linux、Python 3.11、
問ごとの一時 SQLite DB、FastAPI TestClient、並行度 1。
**計画:** [IP-20260929-01](../../02-challenges-and-decisions/improvement-plans/2026-09-29-assistant-answer-quality.md)。
**契約:** [assistant-answer-quality.md](../../01-specifications/capabilities/assistant-answer-quality.md)。
**固定課題・採否基準・変更前 baseline:** [assistant-answer-quality.md](../assistant-answer-quality.md)。

## 何を測ったか（と測っていないもの）

| 評価 | 状態 |
| --- | --- |
| 構造検証（台本付き偽 LLM、固定 31 問のうち自動 30 問） | 実施。本書の数値はすべてこれ |
| 実 LLM での回答品質（5 軸の人間採点、重大な誤り・境界違反） | **未実施**。API キーと費用の承認が無い |
| 実 LLM での時間・token 実測 | **未実施** |
| 代表利用者評価（#463） | **未実施**。本 Epic の完了を実利用改善とは報告しない |

構造検証の合格は回答品質の合格ではない（IK-0004 / EC-P06）。偽 LLM は固定文を返し、1 つの根拠 ID を引用するだけである。

## 子 Issue ごとの結果

| Issue | 実装 | 検証 | 残る制限 |
| --- | --- | --- | --- |
| #468 計測・評価基盤 | `assistant_ask_metric`（本文なし）、ask-metrics API 4 本、client timing、固定課題 31 問、harness、採点表、採否基準 | 計測テスト（本文・draft・秘密値が全列に無いことの走査、System 隔離、保持期間、失敗も記録）、課題構造テスト | 実 LLM baseline 未測定。課題は合成で統計的有意差は示せない |
| #469 診断共有 | `AskRequestScope`（1 リクエスト内だけの memo） | 呼出し回数テスト、単独 `/system-state` `/overview` の回帰、別 System 非共有 | — |
| #470 情報選択・coverage | coverage（truncated / empty / unavailable / unsupported）、thread 対象の関連 bundle（`related_context`、版・digest・鮮度付き、引用可能）、入力予算と削減記録 | 14 件 + 接続数テスト。`expected_sources_missing_from_context` 0 件 | `objective-map` / `stakeholder-value-network` は ask 画面でないため 404。そこでしか扱えない kind は ask の関連文脈に届かない（別 Issue 候補） |
| #471 根拠付き回答 | v2 契約（conclusion / points / missing_information）、決定的な根拠検証、server 組み立ての `answer`、`answer_structure_json` | 19 件。無効引用・引用なしの事実は「根拠を確認できなかった主張」へ | 存在する無関係 ID・相反する根拠は構造検証で検出できない（テストで限界として明記） |
| #472 失敗時応答 | 11 分類・固定日本語文・復旧操作、`answer_status`、失敗 turn の履歴除外、quota の分類 | 18 件。秘密値・provider 本文が応答・履歴・計測に出ない | `context_unavailable` は定義のみで ask 経路に発生源が無い |
| #473 待ち時間 | assistant 専用設定・出力上限・リクエスト予算（すべて既定は変更前と同一） | 7 件。未設定時は intelligence 設定と同一、予算切れで LLM を呼ばない | 採否（出力量・専用モデル・streaming・形式修復）は実 LLM 比較まで**保留** |
| Dashboard | 構造化回答・失敗カード・復旧操作・参照範囲（遅延取得）・client timing | vitest 1270 件、型検査、lint 0 error | 履歴の失敗 turn は復旧操作を出さない（保存構造が分類しか持たない） |

## 構造検証の比較（同一条件・同一時間帯で連続実行）

基準版と変更版を同じコンテナで交互に 2 ラウンド × `--repeat 5` 実行した（各 300 run、warm 298、pre-LLM 標本 288）。
nearest-rank。生データ: `docs/03-validation/assets/assistant-eval/compare-2026-09-29-{base,head}-r{1,2}.json`。

| 指標 | 基準版 p50 / p95 (ms) | 変更版 p50 / p95 (ms) | 判定 |
| --- | --- | --- | --- |
| pre-LLM（採否基準の対象） | 51.8 / 65.6 | 39.1 / 65.8 | p95 比 1.003（基準 ≤ 1.10）を満たす |
| 総時間（wall） | 48.8 / 79.0 | 47.6 / 87.5 | p50 改善、p95 +11%（下記） |
| LLM 後の処理 | 1.0 / 14.9 | 8.7 / 22.6 | 計測行の書き込みで増加（下記） |

| 1 ask あたり | 基準版 | 変更版 |
| --- | --- | --- |
| `run_system_diagnostics` | 2.267（Overview 3） | 1.0 |
| `build_system_state` | 1.267（Overview 2） | 1.0 |
| LLM 呼出し | 0.967 | 0.967（追加呼出しなし） |

| 台本 | 基準版 | 変更版 |
| --- | --- | --- |
| grounded（230 run） | 通常回答 | `answered` / `grounded` |
| invalid_citation / no_citation_fact（各 10） | **通常回答として表示**（根拠なしを検出しない） | `answered` / `ungrounded`。事実は「根拠を確認できなかった主張」 |
| malformed_json / truncated（各 10） | 200 の定型回答（失敗と区別不能） | `failed` / `malformed_output` / `truncated_output` |
| timeout / http_429（各 10） | 200 の定型回答 | 設定キーに完全一致する問なので `deterministic_answer`（`timeout` / `rate_limited`） |
| no_llm（10） | 画面概要の定型回答 | `failed` / `provider_test_only` |

HTTP エラーは両版 0 / 300。

### 途中で見つけて直した回帰

最初の比較で pre-LLM p95 が 67.6 → 79.6 ms（+18%）と**採否基準を満たさなかった**。基準は緩めていない。
UX Design Studio の thread 質問 4 問だけが +12〜13 ms で、段階別時間から #470 の関連 bundle（約 17.8 ms）が原因と特定した。
その 9 割は SQLite 接続の開設（1 回約 5 ms）で、request 検証で解決済みの対象を bundle が再解決していた。
同じリクエストの解決結果を渡し、snapshot と root 取得の接続を共有して、bundle の接続数を 6 → 3 にした（`5441ca0`）。
単独の context-bundle endpoint の出力は不変（`bundle_to_dict` 一致をテスト）。上表はこの修正後の値である。

### 残った時間の増分（採否基準の対象外として記録）

- 総時間 p95 +11% と LLM 後 p50 +7.7 ms は、計測行 `assistant_ask_metric` の書き込み（接続 1 回と保持期間の削除）が
  応答前に同期実行されるためと判断した。これは #468 の計測そのものの費用である。
- 偽 LLM は 0.1 ms で返るので比率が大きく見える。実 LLM の応答は秒単位のため相対的な影響は小さいと見込むが、**未測定**。
- 応答後への移動（background task）は、client timing の POST が計測行より先に届く競合を生むため今回は採らなかった。
  実 LLM 評価で総時間が問題になれば再検討する。

## 採否

| 候補 | 判断 | 根拠 |
| --- | --- | --- |
| #469〜#472 の変更 | 採用 | 構造検証で重複処理の削減、根拠なし事実・失敗の誤表示の解消を確認。pre-LLM p95 の基準を満たす |
| 出力上限の変更 | 保留 | 既定 2048 のまま。同一モデル・同一課題の実 LLM 比較が未実施 |
| assistant 専用モデル | 保留 | 設定点のみ。既定は分析系と同一 |
| 段階表示 / streaming | 保留 | 初期表示と総時間を実 LLM で測っていない。未検証の引用・操作を確定扱いしない設計が前提 |
| 形式不正時の自動再修復 | 保留 | 追加呼出しの効果と遅延が未測定。`llm_retries` は常に 0 |

保留は未実装の放置ではない。実 LLM 評価（対象データ・最大呼出し回数・費用上限を記した計画の承認後）で採否を決める。

## 回帰検証

- control-server: `tests/test_assistant*.py`、`test_discussion*.py`、`test_interview_discussion*.py`、`test_overview.py`、
  `test_llm*.py`、`test_interview_type_parity.py`、全体テスト（結果は PR #474 に記載）。
- System 隔離、未保存 draft の非保存、human gate、会話が正規データを変更しないことは既存テストと新規テストで確認。
- Dashboard: `npm test`、`tsc -b --noEmit`、`npm run lint`。

## 手順へのフィードバック

- EC-P06（IK-0004）により、構造検証・実 LLM・代表利用者を最初から 3 つに分け、構造検証の結果を品質の結果として書かなかった。
- EC-P05 により採否基準を比較前に固定したことで、#470 の p95 回帰を「誤差」として通さず原因特定と修正に進めた。
  同時刻に交互実行しない比較（初回 baseline は別時間帯）では揺れが大きく、判断には同一条件の連続実行が必要だった。
- 新しい IK 候補: 「新しい読み取り段が、同じリクエストで既に解決した対象を再解決し、接続開設の固定費で tail を悪化させる」。
  分類の判断に担当レビューが要るため候補として残す。

## 2026-10-04追補: 候補の登録

上記の「担当レビューが必要」のため未登録としていた知見を、[IKへ登録](../../02-challenges-and-decisions/issue-knowledge/entries/IK-0022-repeated-resolution-connection-overhead.md)した。
人の分類確認は登録・利用の開始条件ではない。旧記述は当時の判断として残し、新規運用では適用しない。
過去の修正効果と、知識として別案件に適用する効果は区別する。
