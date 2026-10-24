# Vision → UX → Feature の調査手法（外部資料の収集・現状調査・仮説構築）

**文書の役割:** canonical-contract（手法の契約。対象システムに依存しない）
**状態:** draft（目標契約。実装完了を意味しない。§9 の不足は未対応）
**対象:** 公開資料などの外部資料から Vision を集め、あるべき UX を導き、現状調査と仮説構築を経て Feature を提案する手順
**確認時点:** 2026-10-24
**上位方針:** [System Understanding の理想状態](../../00-product/system-understanding-ideal-state.md)、[Purpose Chain](purpose-chain.md)
**下位仕様:** [Stakeholder Value Network](stakeholder-value-network.md)、[Product Objective Lineage](product-objective-lineage.md)、[UX Design Lineage](../ux/ux-design-lineage.md)、[共同検討UX](../ux/decision-discussion-workflow.md)、[AI Discussion Adapter](../capabilities/ai-discussion-adapter.md)
**適用例:** [渋谷共創ダッシュボード docs/vision-sourcing.md](https://github.com/dx-junkyard/OpenDataHackathon2025_shibuya_dashboard/blob/main/docs/vision-sourcing.md)（ドメイン設定の具体例）

この文書は **手法** だけを所有する。Vision・Journey・Feature の entity と状態は下位仕様が所有し、
ここでは再定義しない。特定の組織・地域・業種の資料名や目標は書かない。それらは各プロジェクトの
**ドメイン設定**（§7）が所有する。

---

## 1. 手法が答える問い

> 対象システムの上位にある組織（Sponsor）が外部資料で示している目指す姿に対して、
> 受益者はどんな体験ができればよく、現状には何が足りないか。その差はなぜ生じていて、
> どの機能（または機能以外の手段）で埋めるか。

```text
外部資料（引用・出典付き）
  → Sponsor の目指す姿・政策/戦略目標・指標（Sponsor の言葉）
    → 製品の Vision / Objective / Milestone（製品の解釈。Sponsor 目標への寄与を relation で持つ）
      → あるべき体験（to-be Journey / Requirement）
        ↔ 現状（3 種類。§4）
          → Gap（目標状態と現状の差）
            → 原因の仮説と調査（§5）
              → 埋め方（Feature 新規・拡張 / 運用 / 対象外 / 保留）
```

Sponsor は、例えば自治体、企業の経営層、事業部、資金提供者など、対象システムの目的に上位の
方針を与える主体である。受益者（利用者・住民・顧客など）とは別の Stakeholder として扱う。

## 2. 5 つの区別（VF-D）

手法の品質は、次の区別を最後まで保つことで決まる。どの手順の出力にも、どちらに属するかを持たせる。

| ID | 区別 | 混ぜたときの失敗 |
| --- | --- | --- |
| VF-D1 | **資料の言葉（引用）** と **製品の解釈** | 製品の都合を Sponsor の方針として提示する |
| VF-D2 | **Sponsor の意図** と **受益者のニーズ** | 上位方針の言い換えだけで UX を作り、当事者の困りごとが抜ける |
| VF-D3 | **あるべき姿（to-be）** と **現状（as-is）** | 欲しい機能から逆算した Gap になる |
| VF-D4 | **3 種類の現状**（§4） | 実装済みであることを、受益者の状態が変わったことと読む |
| VF-D5 | **書かれていない** と **探していない** | 未収集・未読の資料が「方針がない」「課題がない」に化ける |

あわせて [System Understanding の理想状態 §6](../../00-product/system-understanding-ideal-state.md#6-今後の設計が外してはならない原則) の原則
（事実・推論・仮説・不明・矛盾の区別、人に聞く前に調べる、AI 提案は人の確認まで確定しない、
未計測を成果に見せない）をすべての手順に適用する。

## 3. あるべき姿の調査 — 外部資料の収集と構造化

### 3.1 資料の層（権威の順位）

資料はドメインを問わず次の層に分ける。各層の具体的な資料名はドメイン設定が与える。

| 層 | 一般名 | 取り出すもの | 時点 |
| --- | --- | --- | --- |
| SL-1 | 最上位方針（構想・理念・長期ビジョン） | 目指す姿、領域の分け方 | 長期。改定まで有効 |
| SL-2 | 中長期計画 | 領域ごとの目標・施策・成果指標 | 計画期間あり |
| SL-3 | 領域別計画・戦略 | 施策、対象者、指標と目標値 | 計画ごとに期間が違う |
| SL-4 | 実行計画・予算・事業 | 事業、担当、規模 | 年度・四半期単位 |
| SL-5 | 評価・統計・調査 | 現状値、達成状況、受益者の評価 | 調査時点。**現状側の証拠**（§4） |

- 上位と下位、新版と旧版が食い違うときは黙って選ばず **矛盾** として残し、人が扱いを決める。
- SL-5 はあるべき姿ではなく現状の証拠として使う（VF-D3）。
- 素案・パブリックコメント版など確定前の資料は `draft` として区別する。

### 3.2 主張の型

資料から取り出す主張は次の型のいずれかにし、資料の位置（ページ・行）を必須にする。
ドメイン設定は型を **追加** できるが、削除・意味の変更はしない。

| 型 | 内容 | 主な行き先 |
| --- | --- | --- |
| `aspiration` | 目指す姿 | Sponsor の目標（§3.4）→ Vision の上流 |
| `policy_goal` | 領域ごとの目標・方向性 | Sponsor の目標 → Objective の上流 |
| `indicator` | 指標名・基準値・目標値・年度 | Milestone の目標状態の候補 |
| `initiative` | 施策・事業・担当・対象者 | 現状 C-2（既存の手段）、連携先 |
| `problem_statement` | Sponsor の課題認識 | 現状の証拠（Sponsor の認識として。受益者の声とは別。VF-D2） |
| `target_group` | 対象者 | Stakeholder の候補 |
| `constraint` | 制約・原則・禁止事項 | 非目標、横断 Requirement |

### 3.3 外部資料台帳（External Source Registry）

probe-agent はコミット済みファイルだけを読み、外部ネットワークに出ない。これは安全境界であり変えない。
外部資料は **対象リポジトリに固定してから** 読ませる。

台帳の 1 資料あたりの項目:

| 項目 | 意味 |
| --- | --- |
| `source_id` | 主張・relation から参照する安定 ID。URL や行 id から導出しない |
| `layer` | SL-1〜SL-5 |
| `title` / `publisher` / `url` | 原典へ戻るため |
| `issued` / `period` / `edition` | 策定日・計画期間・版（`final` / `draft`） |
| `retrieved` / `content_digest` | 取得日と本文のハッシュ。同一性と変更検知 |
| `coverage` | 取り込んだ範囲（全文 / 章）。未読部分を明示する（VF-D5） |
| `status` | `current` / `superseded` / `draft` / `unavailable`（取得できなかった。理由を必ず書く） |
| `license_note` | 転載条件。本文を置けない資料は要約と位置参照だけを置く |

本文はページ境界を保った Markdown にする（例: `<!-- page: 12 -->`）。表形式の指標は CSV にしてよい
（同じ `source_id` を持たせる）。**取得できなかった資料も台帳に行を持つ**。

### 3.4 Sponsor の目標と製品 Vision の接続

Sponsor の目標は製品の Vision ではない（VF-D1/D2）。製品の Vision・Objective が Sponsor の
どの目標に寄与するかを relation として持つ。

- relation は AI が仮説として提案し、人が確定・却下する。初期状態は必ず仮説。
- 製品 Objective 側: 「寄与先あり（確定）」「寄与先なし（確定）」「仮説」「未検討」を区別する。
- Sponsor 目標側: 製品のどの Objective にもつながらないものを「対象外（確定）」か「未検討」かに分ける。
  未検討の目標は新しい Objective の発見経路になる。

### 3.5 時点と改訂

新しい版の資料が入ったら、旧版から導いた主張・relation・Milestone を **自動では書き換えず** 要再確認にする。
変化は下流方向だけに伝える（[Purpose Chain §1.3](purpose-chain.md) の伝播規則と同じ）。
計画期間の終了が近い資料はドメイン設定で明示し、次版の有無を最初の調査対象にする。

## 4. 現状調査 — 3 種類の現状

| ID | 種類 | 問い | 主な証拠 | 調べる手段 |
| --- | --- | --- | --- | --- |
| C-1 | 環境・受益者の現状 | 受益者と対象領域は今どういう状態か | 統計、調査、公開データ、評価結果（SL-5） | 資料の取り込み、データの入手見込みの確認 |
| C-2 | 既存の手段と当事者の行動 | 受益者は今どう行動し、既存のサービス・業務・他システムはどう応えているか | 既存サービスの資料、聞き取り、ペルソナ検証、利用ログ | 資料の取り込み、人への質問、利用者評価 |
| C-3 | 対象システムの現状 | 対象システムは今どこまでできるか | コード、テスト、計測、実行時観測 | System Understanding、Feature とコードの対応、計測カバレッジ、Runtime Reality |

規律:

- C-3 の「実装済み」は C-1 / C-2 の変化を意味しない。Gap の解消は人の判断で、根拠は C-1 / C-2 の観測で示す。
- C-2 は対象システムがない状態の経路を **as-is Journey** として書く。to-be Journey はそれを baseline として指す。
  新規の体験なら greenfield と明示し、未決定と区別する（[UX Design Lineage §2.3](../ux/ux-design-lineage.md)）。
- 「データがない」は `not_published`（公開されていない）/ `not_retrieved`（公開されているが未取得）/
  `not_aggregated`（取得したが未集計）/ `unavailable`（取得を試みて失敗）に分ける。

## 5. 仮説構築 — Gap から Feature へ

Gap を直接 Feature へ変換しない。**なぜ差があるのか** を仮説として立て、調査で絞ってから埋め方を選ぶ。

### 5.1 原因の仮説

1 つの Gap に対して、少なくとも 2 つの競合する原因仮説を立てる（例: 「知られていない」と「使いにくい」、
「データがない」と「データはあるが届いていない」）。各仮説は
[AI Discussion Adapter §6](../capabilities/ai-discussion-adapter.md) の仮説項目を満たす:

| 項目 | 内容 |
| --- | --- |
| 説明 | 何が原因で現状と目標がずれているか |
| 支持する証拠 | `source_id` / 現状証拠への参照 |
| 競合する説明 | 同じ現状を説明する別の仮説 |
| 予測 | 正しければ観察されるはずのこと |
| 反証条件 | 何が観察されたら棄却するか |
| 次の調査 | 競合仮説を区別する調査と、その answerability（`system_researchable` / `human_judgement`） |

### 5.2 調査

- `system_researchable` な調査（資料・コード・データ・計測で確かめられるもの）は人に聞かずに先に行う。
- `human_judgement`（Sponsor の優先、受益者の価値判断、現場の事実で資料にないもの）だけを人に聞く。
  質問には「どの判断を変えるか」「何が止まっているか」を付ける。
- 調査結果で仮説を「支持」「棄却」「区別できず（未解決）」のいずれかにする。区別できないまま
  Feature を決める場合は、その不確実性を Feature 候補に残す。

### 5.3 埋め方の選択

| 選択 | 意味 |
| --- | --- |
| `feature_new` | 新しい Feature で埋める |
| `feature_extend` | 既存 Feature の拡張で埋める（どの Feature か必須） |
| `operation` | 機能ではなく運用・人の取り組みで埋める |
| `sponsor_decision` | Sponsor の判断・施策でしか埋まらない（製品は判断材料を出すまで） |
| `out_of_scope` | 製品の非目標として扱う（理由必須） |
| `deferred` | 保留（再検討の条件を書く） |

`feature_new` / `feature_extend` を選ぶときは、Requirement → Feature の link と、その Feature が
どの原因仮説に効くかを持たせる。仮説が棄却されたら、その Feature 候補は要再確認になる。

## 6. 手順（VF-S0〜S9）と probe-agent の担い手

| 手順 | 内容 | 担い手（既存） | 置き場所 | 人のゲート |
| --- | --- | --- | --- | --- |
| S0 調査目的 | 何の判断のための調査か、対象範囲を決める | Interview の Intent Brief（`interview_intent_agent`） | Intent `goal` / `constraints` / `non_goals` | Intent の確定 |
| S1 資料の収集と固定 | 台帳を作り、本文をコミットする | **probe-agent の外**（開発者・コーディングエージェント・取り込みスクリプト）→ Repository Snapshot | 対象リポジトリの台帳ディレクトリ（§7） | 取り込み範囲 |
| S2 主張の抽出 | §3.2 の型の主張を位置付きで抽出する | `documentation_claim_scanner` | Understanding claim | なし（誤りは S3 で扱う） |
| S3 構造化 | 目指す姿 → 領域 → 目標 → 施策 → 指標 の木と、矛盾・欠落を示す | `system_understanding_reviewer`、Interview の Alignment Review | Sponsor Stakeholder の Need（`expectation` / `constraint`、根拠 `document`）。資料の新版は Environment Observation | 矛盾の扱い |
| S4 Vision との接続 | Sponsor 目標と製品 Vision・Objective の relation を提案する | `purpose_chain` / `purpose_needs`、AI 対話の変更候補（対象 `purpose_relation` / `product_objective`） | Purpose relation、Objective の上流参照 | relation の確定 |
| S5 あるべき体験 | 受益者ごとの to-be Journey と Requirement を導く | AI 対話の変更候補（対象 `ux_journey` / `ux_journey_step` / `ux_requirement`） | to-be Journey、Requirement | 採用・編集 |
| S6 現状調査 | C-1 / C-2 / C-3 を集める | C-1/C-2: 取り込み資料 + Interview。C-3: Feature とコードの対応、計測カバレッジ、Runtime Reality | as-is Journey、Service Blueprint、Need の根拠、Capability | 人にしか答えられない現状への回答 |
| S7 Gap | Milestone の目標状態と現状の差を登録する | `product_gap_sources`、AI 対話の変更候補（対象 `product_milestone` / `product_gap`） | Gap（現状・目標・解釈・検出元） | 優先度バンド、解消 |
| S8 仮説と埋め方 | §5 の仮説構築・調査・埋め方の選択 | 共同検討（Gap → 照合 → 仮説 → Joint Understanding）、Investigation Loop、AI 対話（対象 `product_feature`） | 仮説、調査結果、Product Feature | 仮説の暫定採用、Feature の採否 |
| S9 再評価 | 資料の新版・新しい現状観測・実装変化で要再確認を示す | snapshot 差分、Purpose relation の stale、Gap の recheck | 要再確認の一覧 | 再確認 |

Sponsor の目標を「Sponsor Stakeholder の Need」として持つのは、VF-D2 を既存の型のまま表すため
（[Stakeholder Value Network §1.2](stakeholder-value-network.md)）。受益者のニーズは受益者 Stakeholder の Need とし、
根拠の種類（`human_report` / `external_analytics` / `document`）を分けて持つ。

### 6.1 終了条件

- S2: 取り込んだ全範囲について、抽出済み・抽出対象外（理由）のどちらかが記録されている。
- S4: 全 Objective が「寄与先あり（確定）」「寄与先なし（確定）」「仮説」のいずれか。
- S6: 全 Milestone について、現状が「観測あり」「未観測」「観測不能」のいずれか。
- S8: 全 Gap について §5.3 の選択が決まり、Feature 候補には効く原因仮説が付いている。

### 6.2 エージェントへの指示の形

Interview の質問、Joint Understanding の調査目的、AI 対話の依頼のいずれにも、次の 5 項目で指示する。

```text
目的:     この手順で決めたい判断
入力:     読んでよい資料（source_id と範囲）と、既に確定した事項
出力の型: §3.2 / §4 / §5 の型、または probe-agent の対象（purpose_relation 等）
根拠:     各出力に source_id と位置を必須。根拠のない出力は作らない
不明時:   「記載なし（読んだ範囲）」「未取得」「矛盾」「未解決」を区別して返し、推測で埋めない
```

## 7. ドメイン設定（各プロジェクトが与えるもの）

手法をプロジェクトに当てはめるとき、対象リポジトリの文書で次を定める。probe-agent はこれを
コミット済み文書として読む。

| 項目 | 内容 |
| --- | --- |
| Sponsor | 上位方針を出す主体と、その Stakeholder key |
| 受益者 | 受益者 Stakeholder の一覧（既存の Vision 文書を参照） |
| 資料の層の具体名 | SL-1〜SL-5 に当たる資料名と入手先、計画期間の終了が近い資料 |
| 主張の型の追加 | §3.2 に追加する型（例: Sponsor 独自の分類軸） |
| 現状の証拠源 | C-1 / C-2 / C-3 それぞれの具体的な情報源 |
| 台帳の場所 | 台帳と本文を置くディレクトリ（`probe-agent.yml` の `repository.include` に含める） |
| 最初の調査目的 | S0 の初期値 |

## 8. 受入条件

- VF-AC-01: 任意の Feature 候補から、Gap → Milestone → Objective → Sponsor 目標 → 資料の位置まで、
  手動の転記なしにたどれる。途中の relation が仮説ならそう表示される。
- VF-AC-02: Sponsor の言葉・製品の解釈・受益者のニーズが、どの画面・出力でも区別して表示される。
- VF-AC-03: 取得できなかった資料、読んでいない範囲、未観測の現状が、0 件や「なし」と区別して表示される。
- VF-AC-04: 1 つの Gap に競合する原因仮説が 2 つ以上あり、調査結果と埋め方の選択がそれに結びつく。
- VF-AC-05: 資料の新版を取り込むと、旧版に依存する relation・Milestone・Gap・Feature 候補が要再確認になり、
  自動では書き換わらない。
- VF-AC-06: ドメイン設定を差し替えるだけで、別のプロジェクトに同じ手順を適用できる（手法の本文に
  特定ドメインの語を含まない）。

## 9. 現在の probe-agent に不足しているもの

| ID | 不足 | 影響 | 対応方針 |
| --- | --- | --- | --- |
| VF-G1 | 外部ネットワークに出ない（安全境界） | 資料の収集ができない | 変えない。S1 は外で行いコミットする |
| VF-G2 | 文書の索引が Markdown のみ（`documentation_indexer`） | PDF などをそのまま読めない | 取り込み時にページ境界付き Markdown にする。索引側の対応は別途判断 |
| VF-G3 | 根拠に発行者・策定日・計画期間・版を持つ欄がない | 新旧資料の優先や期限切れを機械的に扱えない | 当面は台帳を文書としてレビューに読ませる。根拠への資料属性の追加を Issue 化する |
| VF-G4 | 主張抽出が §3.2 の型を区別しない | 指標を Milestone 候補へ渡しにくい | 抽出スキーマに主張の型を追加する Issue を起こす |
| VF-G5 | Stakeholder / Objective / Journey の作成は決定的処理で、AI 対話の変更候補は既存対象 1 件ずつ | 資料から多数の候補を一括で起こせない | 当面は候補を対象リポジトリの文書に書き、Interview に読ませる。一括候補の経路を Issue 化する |
| VF-G6 | AI 対話の対象に Environment Observation / Value Exchange がない | 資料の新版を対話で登録できない | 当面は API で登録。対象追加を Issue 化する |
| VF-G7 | 原因仮説の「埋め方の選択」（§5.3）を持つ欄がない | Gap の埋め方が Feature 以外のとき記録できない | Gap の決定語彙への追加を検討する |
| VF-G8 | 外部資料台帳の項目（§3.3）を検査する仕組みがない | 台帳の欠落（取得失敗の理由なし等）が見逃される | 台帳の構造検査を snapshot 取り込み時の診断に加える案を検討する |

## 10. 非目標

- probe-agent が外部サイトから資料を自動取得すること
- Sponsor の目標や受益者のニーズを AI が確定すること
- 資料の記述量や言及回数を、重要度や優先度の score にすること
- 特定ドメイン（自治体、企業など）専用の entity を作ること
- 既存の Purpose Chain・Stakeholder Value Network・Objective Lineage・UX Design Lineage を作り直すこと
