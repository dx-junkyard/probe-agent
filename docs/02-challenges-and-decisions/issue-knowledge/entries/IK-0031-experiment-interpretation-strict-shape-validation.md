---
{
  "id": "IK-0031",
  "title": "Experiment解釈の検証がrisksの型や推薦キーの1か所の逸脱で解釈全体を捨て、出力上限も1800に固定されている",
  "status": "resolved",
  "recorded_at": "2026-11-06",
  "observed_at": "2026-11-06",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の ecbceb6",
  "resolved_at": "2026-11-06",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Experimentの決定的なmetricsをLLMで解釈し、variantの比較を説明する",
    "layers": [
      "control_server"
    ]
  },
  "classification": {
    "axes": {
      "processing": [
        "input_handling"
      ],
      "structure": [
        "none"
      ],
      "connection": [
        "contract"
      ],
      "governance": [
        "budget"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "medium",
      "connection": "medium",
      "governance": "medium"
    },
    "cause_status": "hypothesis",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "仮説: E-0009の#4はrisks must be an arrayで失敗したが応答本文が残っておらず、モデルが返した型（文字列かobjectか）は未確認（IK-0029で以後は保存される）。確定している事実: _run_reasoning_analysisはrisksが配列でない、recommendation_reasonが文字列でない、推薦キーが許可キーに無い、のどれか1つで解釈全体をanalysis_failedにしていた（K-0001の全破棄型）。プロンプトはJSONの型を明示していなかった（connection.contract）。max_tokens=1800固定で、他経路のINTELLIGENCE_MAX_OUTPUT_TOKENSと分散（IK-0028と同型、governance.budget）。処理: 型の揺れを正規化せず即座に拒否（input_handling）。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "生成結果の1フィールドの形の逸脱で、他の有効な内容ごと結果全体を捨てる"
  },
  "pattern": "unanchored-reference-strict-validation",
  "discovery": {
    "perspective": [
      "reproduction",
      "inventory"
    ],
    "note": "#4の解釈失敗の文言から検証コードを読み、全破棄になる条件を棚卸しした。LLM呼び出し経路のmax_tokensの出所を再確認し、解釈だけが1800固定だった。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "order_and_budget"
    ],
    "note": "K-0001の「正規化 → 解決 → その項目だけ落とす」を適用。risksが文字列なら1件のリスト、objectやobjectの配列は文字列化。recommendation_reasonがリストなら連結。推薦キーは大文字小文字・labelでの指定を解決し、解決できなければ推薦だけnullにして解釈は残す。調整はanalysis.notesに記録。全体を失敗にするのはnarrativeが空のときだけ。プロンプトに各キーの型を明示。max_tokensはINTELLIGENCE_MAX_OUTPUT_TOKENS（既定128000）に揃えた。",
    "landed_in": [
      "apps/control-server/app/routes/experiments.py"
    ],
    "verification": [
      "apps/control-server/tests/test_experiment_analysis.py"
    ]
  },
  "related": [
    "IK-0026",
    "IK-0027",
    "IK-0028",
    "IK-0029",
    "IK-0030"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-06",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。sim-sandbox E-0010の2件目（失敗処理）として着地、関連テストpass。"
    }
  ]
}
---

## 課題

症状（sim-sandbox E-0009）: Experiment #4のmetricsはcompletedだったが、解釈が `analysis_failed`（`risks must be an array`）になった。同じ内容の#5は成功した。

原因: `_run_reasoning_analysis` は、`risks` が配列でない・`recommendation_reason` が文字列でない・`recommendation_variant_key` が許可キーに無い、のどれか1つで解釈全体を捨てていた（K-0001でいう全破棄型）。プロンプトにはキー名だけがあり型は書かれていなかった。`max_tokens=1800` 固定で、drafts等の `INTELLIGENCE_MAX_OUTPUT_TOKENS` と分散していた（IK-0028と同型）。モデルがrisksを実際にどの型で返したかは応答本文が残っておらず未確認（原因は仮説のまま）。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `ecbceb6`。起票元: sim-sandbox `docs/02-experiments/E-0009-probe-agent-reproducibility.md`（本リポジトリ外）。

## 発見の観点

失敗文言から検証コードを読み、全体を失敗にする条件を列挙した。LLM呼び出し経路ごとの出力上限の出所を再確認した。

## 解決の観点

**解決（2026-11-06）。** sim-sandbox K-0001（厳密検証を1件の不一致で全破棄にしない）を解釈に適用。

- 正規化: `risks` が文字列なら1件のリスト、object（またはobjectを含む配列）は `text/summary/description/risk/reason/detail` の値かJSON文字列に。`narrative` / `recommendation_reason` がリストなら改行で連結。
- 解決: `recommendation_variant_key` の大文字小文字違いと、labelで指定されたもの（"return three" → `variant-2`）を許可キーに解決。
- 落とす: 解決できない推薦キーは推薦だけ `null` にし、narrative・risksは残す。
- 全体を失敗にするのは `narrative` が空のときだけ。応答本文はIK-0029で保存される。
- すべての調整を `analysis.notes`（追加フィールド、履歴にも残る）に記録。
- プロンプトに各キーの型（risksはarray of strings、推薦キーはallowed_variant_keysのどれかかnull）を明示。
- `max_tokens` を `INTELLIGENCE_MAX_OUTPUT_TOKENS`（既定128000）に。

検証: `tests/test_experiment_analysis.py`（文字列risks・不明キー・labelでの推薦・object risks/リストreason・narrative欠落時の失敗と応答保存・出力上限）pass。

## 一般化

生成結果の1フィールドの形の揺れで結果全体を捨てると、内容は正しい解釈まで失う。形の揺れは正規化し、参照の不一致は解決を試みてその項目だけ落とし、調整を記録として残す。全体の失敗は代わりの無い中核フィールドが欠けたときに限る。
