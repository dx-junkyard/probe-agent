---
{
  "id": "IK-0037",
  "title": "Probe Planの生成でLLMがcomponent_idを作り、同じシンボルの再計画で別のidになりうるため、tracesとshadow_resultsの履歴が切れる",
  "status": "resolved",
  "recorded_at": "2026-11-07",
  "observed_at": "2026-11-07",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の 0de947a",
  "resolved_at": "2026-11-07",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Featureの承認済みcode-linkから観測点（@probe）を計画し、一時パッチとしてコードに入れる",
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
        "responsibility",
        "representation"
      ],
      "connection": [
        "version"
      ],
      "governance": [
        "assignment"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "high",
      "connection": "medium",
      "governance": "medium"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "確定: probe_planner.pyの出力スキーマは各probe pointに component_id（kebab文字列）を要求し、_parse_plan_responseはそれを検査せず ProbePointResult に入れ、probe_pointsに保存、patch_generatorが @probe(component_id=…) としてコードに書く。component_idは traces / shadow_results の (system_id, component_id) の主キーで、runtime履歴・coverageの結合キー。プロンプトに既存のcomponent_idは見せていたが強制していない。構造: 変わってはならない識別子の付番をLLMに任せている（responsibility）、シンボルの同一性（path, qualified_name）とcomponent_idの対応を系が持っていない（representation）。接続: 前回計画したidが次の計画に強制されない（version）。統制: 付番の担当がLLM（assignment）。処理: 局所の誤りではない。",
    "proposals": []
  },
  "generalization": {
    "level": "general",
    "general_form": "変わってはならない識別子を生成器に作らせ、系が付番・保持しないので、再生成で同じ対象に別の識別子が付く"
  },
  "pattern": "llm-assigned-stable-identifier",
  "discovery": {
    "perspective": [
      "inventory",
      "trace_walk"
    ],
    "note": "利用者の規則（2026-11-07）に照らして、LLM APIの出力がそのまま識別子・個別名として保存される経路を棚卸しし、probe plan の component_id を planner の出力スキーマ → probe_points → patch_generator → traces の主キーまで追った。"
  },
  "resolution": {
    "perspective": [
      "responsibility_move",
      "canonical_source"
    ],
    "note": "component_idをLLMの出力スキーマから外し、LLMは観測するシンボル（path, qualified_name）とmode・理由だけを返す。系がシンボルごとに付番する（app/component_ids.py）: (1) 最新のsnapshotでコードに既にある @probe(component_id)、(2) 同じ(path, symbol)に前回計画したprobe pointのid、(3) 決定論的導出 kebab(qualified_name)[:40] + '-' + sha256(f'{path}::{qualified_name}')[:8]（System内で別シンボルが使っていれば12, 16…桁に延長）。モデルが書いたcomponent_idは無視。prompt/schema_version v2。",
    "landed_in": [
      "apps/control-server/app/component_ids.py",
      "apps/control-server/app/probe_planner.py",
      "apps/control-server/app/routes/project_intelligence.py"
    ],
    "verification": [
      "apps/control-server/tests/test_component_id_assignment.py",
      "apps/control-server/tests/test_probe_plan.py"
    ]
  },
  "related": [
    "IK-0032",
    "IK-0038",
    "IK-0039"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-07",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。利用者の規則（2026-11-07）の棚卸しの1件目として着地、関連テストpass。実機（docker再ビルド後）での再計画の確認は未実施。"
    }
  ]
}
---

## 課題

症状（棚卸しで確認、実機の再現は未実施）: `/repository/probe-plans/generate` で、LLMが各probe pointの `component_id`（kebab文字列）を作る。component_idは `@probe(component_id=…)` としてコードに書かれ、`traces` / `shadow_results` の `(system_id, component_id)` の主キーになる。同じシンボルを再計画すると別のidが付きうる。適用し直すとruntime履歴・coverage・shadow比較が別のcomponentに分かれる。

原因（確定）: 出力スキーマが `component_id` を要求し、`_parse_plan_response` は空でないことしか確かめない。既存のidはプロンプトのcode-link一覧に `component_id=…` として見せていたが、使うことは強制していない。シンボルの同一性（path, qualified_name）からcomponent_idを決める正本が系に無い。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `0de947a`。起票元: sim-sandbox `docs/30-reviews/2026-11-07-llm-id-audit.md`（本リポジトリ外）。

## 発見の観点

利用者の規則「LLM APIで作るとき、変わってはならない識別子・個別名はLLMに再生成させない。系が付番・保持し、LLMはそれに紐づく内容だけを返す（または new と示す）。例外は理由を明記」に照らし、LLM出力がそのまま識別子として保存される経路を棚卸しした。component_idは出力 → `probe_points` → `patch_generator` → tracesの主キーと追えた。

## 解決の観点

**解決（2026-11-07）。**

- 出力スキーマから `component_id` を外した。LLMは観測するシンボル（`symbol_path`・`symbol_qualified_name`、承認済みlinkの中から）とmode・リスク・理由だけを返す。プロンプトは「component idを作らない、系が付番・保持する」と明記し、既にコードにあるidは `already_instrumented_as=…` として参考に見せる。モデルがcomponent_idを書いても無視する。`PROMPT_VERSION` / `SCHEMA_VERSION` を `v2` に。
- 付番（`app/component_ids.py` の `assign_component_ids`、System単位）:
  1. `in_code`: 同じ (path, qualified_name) の `code_symbols.component_id`（最新snapshotから）。
  2. `previous_plan`: 同じ (path, symbol) の最新の `probe_points.component_id`（未適用の計画）。
  3. `derived`: `kebab(qualified_name)[:40]` + `-` + `sha256(f"{path}::{qualified_name}")` の先頭8桁。乱数・時刻を使わないので実行をまたいで同じ。別ファイルの同名関数も別idになる。System内で別シンボルが同じidを使っていれば12, 16, 24…桁に延ばす（同じ呼び出しの中で2シンボルに同じidを与えない）。
- ルートは承認済みlinkの全シンボルを先に付番し、plannerに渡す。

検証: `tests/test_component_id_assignment.py`（導出の決定論・kebab・path依存、プロンプトにcomponent_idが無くモデルの値は無視、再計画で同じid、前回計画のidを優先、コードの@probeのidを優先、衝突時の桁延長とSystem分離）7件と、既存の `test_probe_plan.py` ほか計56件pass。

見送り・残り: flow explorer からの手動計画（`/repository/probe-plans/from-flow`、`_component_id_for` = 末尾名 + `-observer`）とinterviewのmaterialize（`routes/interview.py` の `_component_id_for`）は系の決定論的付番で規則には反しないが、導出式が3通りあり、同じシンボルが経路により別idになりうる（from-flowで先に計画したidは `previous_plan` として引き継がれる）。導出の一本化は別件。実機での再計画の確認は未実施。

## 一般化

利用者の規則（2026-11-07）: LLM APIで生成するとき、変わってはならない識別子・個別名はLLMに再生成させず、系が付番・保持する。LLMはそれに紐づく内容だけを返す（または new と示す）。例外は理由を明記する。runtime履歴の主キーのように後から変えられない識別子は、対象の自然キー（ここでは path と qualified_name）から系が決め、既にあるidを優先し、生成器の出力からは受け取らない。
