---
{
  "id": "IK-0027",
  "title": "code-link生成が識別子を写す元として明示せずに提示し、1件の未知識別子で応答全体を破棄する",
  "status": "resolved",
  "recorded_at": "2026-11-03",
  "observed_at": "2026-11-03",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026（uraから分岐、2026-11-03）。起票時は2026-11-03時点のローカルcheckout",
  "resolved_at": "2026-11-03",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Featureと対象リポジトリのコードシンボルを根拠付きで対応づける",
    "layers": [
      "control_server"
    ]
  },
  "classification": {
    "axes": {
      "processing": [
        "none"
      ],
      "structure": [
        "none"
      ],
      "connection": [
        "contract",
        "information"
      ],
      "governance": [
        "none"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "medium",
      "connection": "high",
      "governance": "medium"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "接続（コードと2回の失敗応答で確認）: プロンプトはFeatureを `- Feature: <名前> (id: <feature_id>)`、シンボルを `<名前> (<kind>) in <path>:<行>` で提示し、どの部分をそのまま写すべき識別子かを示さない。モデルは名前からkebab-caseのidを自作し（use-polytropic-or-tabulated-eos、正はselect-equation-of-state）、`(function)` をシンボル名に写した。_parse_mapping_responseは1件の不一致でMappingValidationErrorを投げ、有効なlinkも含め応答全体を破棄する。写す元が生成側に明示されず（information）、生成側の出力と厳密照合の受入契約が両立しない（contract）。処理: 照合の条件式は意図どおりでnone（中確信）。構造: all-or-nothing破棄は設計方針でありnone（中確信）。統制: 落としたlinkの非記録は観測性の残課題でnone（中確信）。型はIK-0026と同じunanchored-reference-strict-validationとしたが、座標（行番号）ではなく識別子である点が異なり、型の一般形の拡張かgeneration-contract-mismatchとの境界かは未確認。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "生成器に写すべき識別子を装飾と区別せずに提示し、1件の未知識別子で生成結果全体を破棄する"
  },
  "pattern": "unanchored-reference-strict-validation",
  "discovery": {
    "perspective": [
      "reproduction",
      "trace_walk"
    ],
    "note": "code-links生成を2回再現し、error_detailsの未知識別子とプロンプトの提示形式・_parse_mapping_responseの照合を突き合わせた。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "carry_through"
    ],
    "note": "プロンプトで feature_id を先頭に置き「列挙値をそのまま写す」契約を明示し、パーサは別名解決・(kind)除去後も解決できないlinkだけを落とす。コミット880a1eaで着地。不明として落としたlinkが成功応答にもログにも出ない点は残課題。 2026-11-03の3例目（モジュール接頭辞の付加）に対し、経路由来の接頭辞除去と最後の`.`以降での照合をコミット103f47eで追加。",
    "landed_in": [
      "apps/control-server/app/code_mapper.py"
    ],
    "verification": [
      "apps/control-server/tests/test_code_mapper_parse.py",
      "apps/control-server/tests/test_feature_code_mapping.py",
      "apps/control-server/tests/test_probe_plan.py"
    ]
  },
  "related": [
    "IK-0026"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-03",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。ブランチ sim-sandbox/ik-0024-0026 のコミット 880a1ea で着地。同日の現在の実行でテスト75 passed、実機code-links生成completed（link 14件）。"
    },
    {
      "date": "2026-11-03",
      "field": "resolution.perspective",
      "from": [
        "pending"
      ],
      "to": [
        "explicit_contract",
        "carry_through"
      ],
      "reason": "写すべき識別子の契約をプロンプトで明示し、Feature名の別名表で識別子を照合側まで運んだ。"
    },
    {
      "date": "2026-11-03",
      "field": "resolution.note",
      "from": "880a1eaのみ",
      "to": "880a1ea + 103f47e",
      "reason": "sim-sandbox E-0007で3例目（全linkのsymbol_qualified_nameにファイル経路由来のモジュール接頭辞）を観測。880a1eaの落とした一覧付きエラーで原因が即座に判明し、103f47eで接頭辞除去と末尾名照合を追加。テスト関連39件pass、実機再試行でlink 29件completed。statusはresolvedのまま。"
    }
  ]
}
---

## 課題

症状: `POST /repository/code-links/generate`（`apps/control-server/app/code_mapper.py`、provider openai、model gpt-6-luna）が2回連続で `failed`。

- 1回目: `unknown feature_id: use-polytropic-or-tabulated-eos`。プロンプトは `- Feature: <名前> (id: <feature_id>)` の形でidを渡していたが、モデルはFeature名からkebab-caseのidを自作した（正しいidは `select-equation-of-state`）。
- 2回目: `unknown symbol: sims/p01-tov/tov/sequence.py:mass_radius_sequence (function)`。シンボル一覧の表記 `<名前> (<kind>) in <path>:<行>` の `(function)` を名前に写した。

原因（コードで確認済み）: どちらも `_parse_mapping_response` が1件の不一致で `MappingValidationError` を投げ、有効なlinkも含めて応答全体を破棄する。プロンプトは写すべき識別子を名前や装飾と区別して示していなかった。IK-0026（evidenceの行範囲）と同型の「厳密な識別子照合で応答全体を破棄」。

対象版: ブランチ `sim-sandbox/ik-0024-0026`（2026-11-03）。起票元: 外部の検証サンドボックス sim-sandbox の実施記録 `sim-sandbox/docs/02-experiments/E-0002-p01-tov-probe-agent.md` の「Feature-to-Code link 生成」節（probe-agentリポジトリ外のためsourcesには載せず、IK-0026と同じ本リポジトリの文書を載せる。根拠コードは `apps/control-server/app/code_mapper.py`）。

## 発見の観点

code-links生成を2回再現し、error_detailsが示す未知のfeature_id・symbolと、プロンプトのFeature・シンボルの提示形式、`_parse_mapping_response` の照合と全体破棄を突き合わせた。

## 解決の観点

**解決（2026-11-03、対象版: ブランチ `sim-sandbox/ik-0024-0026`）。**

着地先: `apps/control-server/app/code_mapper.py`（コミット `880a1ea`）。

- プロンプト: Feature提示を `- feature_id: <id>` を先頭にした形に変え、「feature_idは列挙された値をそのまま写す。名前から作らない」と明示。
- パーサ: (1) 未知のfeature_idをFeature名のkebab-case別名表で解決、(2) symbol名末尾の `(kind)` を除去して照合、(3) それでも解決できないlinkは**その1件だけ落とし**、全linkが落ちたときだけ失敗（エラーにunknown feature_id / unknown symbolの一覧）。

検証（すべて2026-11-03の現在の実行）:

- テスト: `apps/control-server/tests/test_code_mapper_parse.py`（新規5件）、`tests/test_feature_code_mapping.py` と `tests/test_probe_plan.py` を含めて75 passed（イメージの使い捨てコンテナで実行）。
- 実機: 再ビルド後の `code-links/generate` が completed、link 14件（5 Feature全部に対応、confidence 0.85〜1.0）。

**追加の事例と解決（2026-11-03、sim-sandbox E-0007）。** 3例目: 135 symbolsの一覧に対し、`code-links/generate`（gpt-6-luna）が全linkの `symbol_qualified_name` をファイル経路由来のモジュール接頭辞つき（`sims.p01-tov.tov.eos.Polytrope`、索引は `Polytrope`）で書き、全linkが落ちてfailed。上記(3)の「全部落ちたときだけ失敗し、落ちた一覧をエラーに出す」により原因が即座に読めた。起票元: `sim-sandbox/docs/02-experiments/E-0007-p03-dcf-synthetic-probe-agent.md`（本リポジトリ外）。

- 修正（コミット `103f47e`）: `_parse_mapping_response` で、経路（`tov/eos.py` → `tov.eos.`）から作った接頭辞を剥がし、それでも解決しなければ最後の `.` 以降で照合してから落とす。
- 検証: `tests/test_code_mapper_parse.py` に2件追加し、関連39件pass。実機: 再試行でlink 29件completed。

残課題（未対応）: 不明として落としたlinkの一覧が成功応答に出ず、ログにも残らない。

## 一般化

生成器に識別子を返させるなら、写す元の値を名前・種別などの装飾と区別して提示し、「そのまま写す」契約を明示する必要がある。そのうえで1件の未知識別子で全体を捨てると、有効な結果まで失われる。IK-0026の座標（行番号）と同じく、生成器が自力で作れない参照値を厳密照合する箇所で再発しうる。識別子のずれは「名前のslug化」「一覧表記 `(kind)` の転写」「モジュール経路の付加」の3型が出た。提示の明示だけでは防ぎきれず、既知の変形を照合側で正規化してから落とすことが有効だった。
