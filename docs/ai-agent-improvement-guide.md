# AIエージェント向け probe-agent 改善サイクルガイド

## 1. このシステムの目的

`probe-agent` は、開発対象システムの関数単位の実行データを収集し、現在の実装と
候補実装を同じ入力で比較するための仕組みである。

開発を担当するAIエージェントは、このシステムを単なるログ収集基盤ではなく、次の
改善ループを回すための観測・評価基盤として使用する。

1. システムとコンポーネントの目的を定義する
2. 実際の入力、出力、例外、実行時間を収集する
3. 失敗例や改善余地のあるケースを特定する
4. 評価基準に基づいて候補実装を作る
5. 本番の返値を変えずに候補実装を shadow 実行する
6. 複数の実トレースで品質、エラー、速度を比較する
7. テストを追加してから対象コードへ反映する
8. 反映後のトレースを確認し、次の改善点を探す

重要なのは、1件の成功例やLLMの評価だけで変更を採用しないことである。実トレース、
決定的な評価基準、既存テスト、shadow 比較を組み合わせて判断する。

## 2. 全体構成

```text
対象Pythonアプリ
  └─ @probe(component_id="...")
       ├─ GET  /components/{id}/policy
       ├─ POST /traces
       └─ POST /components/{id}/shadow-results
                    |
                    v
              Control Server
              FastAPI + SQLite
                    ^
                    |
                    v
                Dashboard
       設定、観測、評価、候補生成
```

主な要素は以下の3つである。

| 要素 | 役割 |
| --- | --- |
| Python Probe SDK | 対象関数の入力、出力、例外、実行時間を収集する |
| Control Server | トレース、policy、profile、評価結果を保存する |
| Dashboard | 接続設定、コンポーネント設定、比較・評価結果を表示する |

API token は1つの System に紐づく。同じ `component_id` が別Systemに存在しても、
トレース、policy、profile、評価結果は混在しない。

## 3. AIエージェントが最初に理解すべき概念

### System

観測対象となる独立したアプリケーションまたは環境である。開発、ステージング、
本番など、データを混在させるべきでない対象には別Systemを使う。

System Profileには以下を記録する。

- システムの目的
- 対象ユーザー
- 提供価値
- セキュリティ、互換性、性能などの制約
- システム全体の成功条件

### Component

`@probe(component_id="...")` を付けた関数を指す。`component_id` は長期的に安定し、
責務が分かる名前にする。

良い例:

```python
@probe(component_id="invoice-total-calculator")
def calculate_invoice_total(invoice):
    ...
```

避ける例:

```python
@probe(component_id="func1")
def calculate_invoice_total(invoice):
    ...
```

Component Profileには、目的、責務、期待する入力と出力、失敗時の影響を記録する。
この情報は候補コード生成とLLM評価のコンテキストにも使用される。

### Trace

元の関数を1回実行した記録である。以下が保存される。

- 引数とキーワード引数
- 出力、または例外とスタックトレース
- 実行時間
- 実行時刻
- 実行時のmode

入力と出力は安全な `repr()` 文字列として保存され、長い値は4,000文字で切り詰められる。
したがって、巨大データ、バイナリ、ファイルハンドル、秘密情報をそのまま観測対象に
しないこと。

### Policy

コンポーネントごとの動作モードである。

| mode | 動作 |
| --- | --- |
| `off` | 元の関数だけを実行し、トレースを送らない |
| `trace` | 元の関数を実行し、トレースを保存する |
| `shadow` | 元の関数を返値として採用し、候補実装もバックグラウンドで実行する |

Control Serverに接続できない場合も、SDKは元の関数を実行する。policyはTTL付きで
キャッシュされ、取得できない場合はキャッシュまたは `PROBE_DEFAULT_MODE` を使う。

### Evaluation Criteria

コンポーネントの出力を判定する基準である。

| 種別 | 用途 |
| --- | --- |
| `exact_match` | 出力全体が期待値と一致する |
| `contains` | 出力が指定文字列を含む |
| `regex` | 出力が正規表現に一致する |
| `json_equal` | JSONとして期待値と一致する |
| `required_keys` | JSONに必要なキーが存在する |
| `natural_language` | 人間またはAIによるレビューが必要な意味的基準 |

決定的に判定できる基準を優先する。`natural_language` は自動評価では
`needs_review` となるため、それだけに依存しない。

現行の決定的なEvaluation Criteria評価は、保存済みTraceのcurrent outputに対して
実行される。shadow candidateへ同じルールを自動適用する機能ではない。candidateの
決定的な判定は、対象リポジトリへ同じ条件のテストを追加して実行する。

## 4. 対象アプリへの導入

### SDKのインストール

同じリポジトリからビルドする場合:

```dockerfile
COPY packages/python-probe /opt/probe-agent/packages/python-probe
RUN pip install /opt/probe-agent/packages/python-probe
```

別リポジトリの場合:

```dockerfile
ARG PROBE_AGENT_REF=<commit-sha>
RUN pip install \
  "git+https://github.com/dx-junkyard/probe-agent.git@${PROBE_AGENT_REF}#subdirectory=packages/python-probe"
```

再現可能なビルドにするため、`main` ではなくtagまたはcommit SHAへ固定する。

### Docker Composeの設定

```yaml
services:
  target-app:
    environment:
      PROBE_ENABLED: "true"
      PROBE_SERVER_URL: http://control-server:8000
      PROBE_API_KEY: ${PROBE_API_KEY}
      PROBE_DEFAULT_MODE: trace
    depends_on:
      control-server:
        condition: service_healthy
```

同じComposeネットワークでは `localhost` ではなくControl Serverのservice名を使う。
Control Serverがホストで動いている場合、MacとWindowsでは通常
`http://host.docker.internal:8000` を指定する。

API tokenをDockerfileやソースコードへ埋め込んではならない。実行時環境変数、
Docker Secret、または利用中のSecret Managerから渡す。

### 対象コードへの設定

```python
from probe_agent import probe


@probe(component_id="summarizer")
def summarize(text: str) -> str:
    return text[:80]
```

プローブは、次の条件を満たす境界へ設置する。

- 入出力が改善判断に利用できる
- 責務が明確である
- 同じ入力で候補実装を再実行できる
- 外部副作用がない、または候補実装から副作用を除去できる

DB更新、メール送信、課金、キュー投入などの副作用を持つ関数をそのままshadow実行しては
ならない。その場合は、純粋な判断・変換部分を別関数へ分離し、そこへプローブを設置する。

## 5. AIエージェントの標準改善サイクル

### Step 1: ベースラインを固定する

変更前に以下を記録する。

- 対象のcommit SHA
- Systemと実行環境
- 対象 `component_id`
- 現在のテスト結果
- 現在のpolicy
- 対象期間または対象トレース集合

比較対象が変動しないよう、改善中に無関係な仕様変更を混ぜない。

### Step 2: 目的と判定基準を定義する

DashboardのSettingsまたはComponents画面で、System ProfileとComponent Profileを
登録する。次に、評価基準を登録する。

評価基準の例:

```text
Component: json-normalizer

Purpose:
  JSON文字列を意味を変えずに正規化する

Expected output:
  有効なJSON文字列。キー順序と空白差を除けば入力と同値

Criteria:
  - json_equal: 入力JSONと意味的に同値
  - required_keys: name,tags
  - natural_language: 不要な情報を追加しない
```

「改善する」だけでは判定できない。正確性、互換性、速度、可読性、エラー率など、
今回の変更で何を改善し、何を維持するかを明文化する。

### Step 3: `trace` modeで実データを集める

最初は `trace` modeを使い、正常系だけでなく以下を含む入力を集める。

- 典型的な入力
- 境界値
- 空値や欠損値
- 大きな入力
- Unicodeや特殊文字
- 過去に失敗した入力
- 低頻度だが影響の大きい入力

AIエージェントは、1件だけを見て一般化しない。入力パターンごとにトレースを分類し、
失敗率、出力差、実行時間の分布を確認する。

### Step 4: 問題を再現テストへ変換する

改善対象とするトレースを選び、その入力と期待結果を対象リポジトリのテストへ追加する。

トレースは観測データであり、テストそのものではない。採用前に、再現可能で決定的な
テストケースへ変換する。

秘密情報や個人情報が含まれる場合は、構造と失敗条件を維持した匿名データへ置き換える。

### Step 5: 候補実装を作る

候補は次のいずれかで作成できる。

1. AIエージェントが対象リポジトリ内で直接実装する
2. Dashboardの `Generate & Evaluate` で候補コードを生成する

`Generate & Evaluate` は、選択した1件のトレース、System Profile、
Component Profile、Evaluation Criteria、改善目的をLLMへ渡す。生成コードは制限付きの
subprocessで同じ入力に対して実行される。

ただし、生成結果は1件の入力に対する探索支援であり、そのまま採用してはならない。
生成コードは対象リポジトリの設計、型、依存関係、テスト規約に合わせて移植・レビューする。

現行の自動生成候補には以下の制約がある。

- `candidate(*args, **kwargs)` というPython関数のみ
- import、ファイルI/O、ネットワーク、subprocess、環境変数アクセスは使用不可
- 実行timeoutは5秒
- 出力は `repr()` で比較される
- 対象システムへ自動適用されない
- 制限付きsubprocessは防御層ではあるが、強固なセキュリティsandboxではない

### Step 6: 候補をshadow登録する

```python
from probe_agent import probe, set_candidate


def summarize_candidate(text: str) -> str:
    return text.split(".")[0].strip()


set_candidate("summarizer", summarize_candidate)


@probe(component_id="summarizer")
def summarize(text: str) -> str:
    return text[:80]
```

Dashboardで対象コンポーネントを `shadow` modeへ切り替える。

shadow中も呼び出し元へ返るのは常に現在の実装の結果である。候補は入力のスナップショットを
使ってバックグラウンド実行される。ただし、外部サービスや共有状態へアクセスする候補は
副作用や競合を起こし得るため、純粋関数として実装する。

短命なバッチやCLIでは、終了前に明示的に `flush()` を呼ぶ。

```python
from probe_agent import flush

try:
    run_job()
finally:
    flush()
```

### Step 7: 複数トレースで評価する

以下を確認する。

- currentとcandidateの出力差
- candidateの例外
- currentとcandidateの実行時間
- current outputに対する決定的なEvaluation Criteriaの結果
- `better` / `worse` / `same` / `unknown` の手動評価
- LLM評価のreason、risks、recommendation
- candidateにも同じ期待条件を適用した既存テストと新規回帰テスト

採用判断の最低条件:

- 代表的な複数入力で評価している
- 既知の失敗トレースが改善している
- 正常トレースを悪化させていない
- candidate errorがない
- 必須の決定的評価基準を満たす
- 対象リポジトリの全テストが通る
- 副作用、セキュリティ、互換性をコードレビューしている

LLMの `better` 判定は補助情報であり、採用条件を単独では満たさない。

### Step 8: 対象実装へ反映する

採用する場合は、候補を直接差し替えるのではなく、通常の開発変更として反映する。

1. 対象実装を変更する
2. 回帰テストを維持する
3. lint、型検査、単体・統合テストを実行する
4. コードレビュー可能な差分にする
5. 段階的にデプロイする
6. 反映後は一度 `trace` modeへ戻して観測する

このシステムは自動replaceや対象システムへのリモートコード適用を行わない。

### Step 9: 結果を記録する

AIエージェントは変更ごとに、最低限以下を作業記録へ残す。

```markdown
## Improvement evidence

- System:
- Component:
- Baseline commit:
- Candidate commit:
- Objective:
- Evaluated trace IDs:
- Added regression tests:
- Criteria results:
- Shadow results:
- Performance change:
- Known risks:
- Adoption decision:
```

これにより、変更理由をトレースと評価結果へ結びつけられる。

## 6. Dashboardを使う最短手順

1. `Connect SDK` で対象System用のAPI tokenを発行する
2. 表示されたSDK install方法と環境変数を対象アプリへ設定する
3. 対象関数へ `@probe` を追加する
4. 対象アプリを実行し、Overviewでトレース受信を確認する
5. SettingsでSystem Profileを設定する
6. ComponentsでComponent ProfileとEvaluation Criteriaを設定する
7. `trace` modeで代表入力を収集する
8. 必要なら `Generate & Evaluate` で候補を探索する
9. 対象アプリへ候補関数と `set_candidate()` を追加する
10. `shadow` modeへ変更し、複数入力を実行する
11. shadow結果、評価基準、テスト結果を確認する
12. 採用後は候補登録を除去し、通常実装として反映する

## 7. AIエージェント向けの運用ルール

### 必ず行うこと

- 変更前にSystem Profile、Component Profile、評価基準を確認する
- 実トレースを再現テストへ変換する
- 複数の入力カテゴリで比較する
- shadow候補を純粋かつ決定的に保つ
- tokenや観測データに秘密情報を含めない
- 採用理由と不採用理由を記録する
- 変更後もトレースを確認する

### 行ってはいけないこと

- LLM評価だけで候補を採用する
- 1件のトレースだけに過剰適合する
- 課金、送信、永続化などの副作用をshadowで二重実行する
- 本番の秘密情報や個人情報を無加工で記録する
- `component_id` を理由なく変更して履歴を分断する
- `PROBE_API_KEY` をソースやDocker imageへ埋め込む
- 生成されたコードをレビューせず対象システムへ反映する
- 性能比較を単発の実行時間だけで判断する

## 8. 現行実装の制約

- Probe SDKはPythonのみ
- 関数デコレーター方式であり、コード変更なしの自動計装ではない
- 入出力は構造化JSONではなく主に `repr()` として保存される
- 値は4,000文字で切り詰められる
- shadowはバックグラウンドスレッドであり、CPU負荷や共有状態へ影響し得る
- `deepcopy` できない入力は元の参照へフォールバックする
- Control Serverへの送信はbest-effortで、失敗しても対象処理を止めない
- 自動生成・LLM評価は1トレース単位
- 決定的なEvaluation Criteriaの自動評価対象はcurrent trace outputである
- 生成コードの実行環境は強固なセキュリティsandboxではない
- 入出力の自動マスキングや秘密情報検出は行わない
- 自動replace、CI/CD連携、リモートデプロイは行わない
- `natural_language` 基準は決定的に自動評価されない

これらの制約を前提に、probe-agentを「変更の正しさを証明する唯一の仕組み」ではなく、
実データに基づく仮説形成と安全な比較を支援する仕組みとして使う。

## 9. 完了条件チェックリスト

改善作業は、以下を満たした時点で完了とする。

- [ ] SystemとComponentの目的を理解した
- [ ] 変更目的と非回帰条件を明文化した
- [ ] 代表的な実トレースを複数選定した
- [ ] 問題トレースを回帰テストへ変換した
- [ ] 候補実装を対象リポジトリの規約に合わせた
- [ ] shadowでcurrentとcandidateを比較した
- [ ] candidate errorがないことを確認した
- [ ] 決定的評価基準を満たした
- [ ] LLM評価のリスク指摘を確認した
- [ ] 全テスト、lint、型検査を通した
- [ ] 秘密情報と副作用を確認した
- [ ] 採用判断と根拠を記録した
- [ ] 反映後のトレースを確認した

## 10. 関連資料

- プロジェクト全体とDocker導入: [`../README.md`](../README.md)
- MVPの機能境界: [`mvp.md`](mvp.md)
- Python SDK: [`../packages/python-probe/README.md`](../packages/python-probe/README.md)
- Control Server API: [`../apps/control-server/README.md`](../apps/control-server/README.md)
- 最小サンプル: [`../examples/simple-pipeline/README.md`](../examples/simple-pipeline/README.md)
