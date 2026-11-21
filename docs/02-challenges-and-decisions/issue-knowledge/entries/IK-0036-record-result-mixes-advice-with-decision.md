---
{
  "id": "IK-0036",
  "title": "Experimentの台帳用の記録で、結果の文にLLMの推奨（助言）と人の判断が混ざり、台帳では助言が判断として読まれうる",
  "status": "resolved",
  "recorded_at": "2026-11-07",
  "observed_at": "2026-11-07",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の 99cdf2f",
  "resolved_at": "2026-11-07",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Experimentの実行・解釈・人の判断を、外部の検証記録（疑義台帳の外部検証の線）へ写せる形で渡す",
    "layers": [
      "control_server",
      "dashboard"
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
        "meaning"
      ],
      "governance": [
        "assignment"
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
    "basis": "確定: IK-0035のrecordは result_summary を「解釈（narrative）→推奨（助言）→人の判断→判断メモ」の順に1つの文へ連結していた。台帳の result_summary は学習者向けに事実文として投影される。接続: 組み立て側では見出しで区別したつもりの助言が、読む側では結果の一部として読まれる（meaning）。統制: AIの助言と人の判断という判断主体の違いが同じ枠に入る（assignment）。Experiment #2 では助言 n600（variant-2）と、判断メモの「baseline を採る」が食い違う。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "AIの助言（候補）と人の判断（確定）を、外部に写す結果の枠へ同じ文として混ぜる"
  },
  "pattern": "candidate-canonical-conflation",
  "discovery": {
    "perspective": [
      "data_inspection",
      "boundary_walk"
    ],
    "note": "IK-0035の実機確認で返った Experiment #2 の record を読み、結果の文の中で推奨（n600）と判断メモ（baseline を採る）が並ぶことに気付いた。台帳の学習者向け投影（tool / conditions / result_summary を事実文として出す）と突き合わせた。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "first_class_state"
    ],
    "note": "result_summary は人の判断（採用ならvariant）と判断メモだけで組み立てる。判断が無い（undecided）なら「人の判断: 未」（英語は not yet）とだけ書き、メモも写さず complete=false のまま。LLMの解釈と推奨（助言）は別キー analysis_summary に移す（台帳の external_ref のキーではないので、写さなければ台帳に入らない）。dashboardは解釈と推奨を判断と別の枠に出す。",
    "landed_in": [
      "apps/control-server/app/routes/experiments.py",
      "apps/control-server/app/models.py",
      "apps/dashboard/src/api/types.ts",
      "apps/dashboard/src/pages/experiments.tsx"
    ],
    "verification": [
      "apps/control-server/tests/test_experiment_record.py"
    ]
  },
  "related": [
    "IK-0035",
    "IK-0030"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-07",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。0de947a で着地、関連テストpass。実機（control-server・dashboardを再ビルド）で GET /experiments/1・2・4/record が200（#4 は「人の判断: 未」）、/999/record が404、GET /experiments/2・/4 が再ビルド前と一致、dashboard の配信バンドルに「台帳用の記録」とrecordの取得・analysis_summary が入っていることをcurlで確認（ブラウザでの表示は未確認）。"
    }
  ]
}
---

## 課題

症状: IK-0035の `GET /experiments/{id}/record` は、`result_summary` に「解釈（narrative）」「推奨（助言）」「人の判断」「判断メモ」を1つの文として連結していた。台帳の `result_summary` は学習者向けに事実文として投影されるため、見出しで「助言」と書いてもLLMの推奨が結果（判断）として読まれうる。Experiment #2 では推奨が n600（variant-2）、判断メモが「baseline を採る」で、食い違いがそのまま1つの結果の文に並んでいた。

原因（確定）: 記録の組み立てで、AIの助言と人の判断という判断主体の違う2つを同じ枠に入れていた。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `99cdf2f`（IK-0035の着地直後）。

## 発見の観点

実機の Experiment #2 の record を読み、台帳側の投影（`tool` / `conditions` / `result_summary` を事実文として出す）と突き合わせた。

## 解決の観点

**解決（2026-11-07、`0de947a`）。**

- `result_summary`: 人の判断だけ。`人の判断: 追加データが必要` / `人の判断: 採用（K235（variant-3））` と、判断メモがあれば `判断メモ: …`。判断が無ければ `人の判断: 未`（英語は `Human decision: not yet`）とだけ書き、メモは写さない。`complete` は従来どおり false。
- `analysis_summary`（新しいキー）: LLMの解釈（narrative、版があれば v番号）と `推奨（助言）: label（key）: 理由`。解釈が無ければ `解釈: なし（status）`。台帳の `external_ref` のキーではないので、写さなければ台帳に入らない。
- dashboard: 「台帳用の記録」の枠は条件と人の判断だけを出し、解釈と推奨は別の枠（枠線・薄い文字色）に出す。

検証: `tests/test_experiment_record.py` を更新（結果の文に推奨の variant・「Recommendation」が入らない、未判断は「未」だけでメモを写さない、の確認を追加）、7件pass、Experiment系あわせて28件pass。実機: Experiment #1（採用）・#2（追加データ）・#4（未判断で「人の判断: 未」）の record が200、同じ記録を2回取って同一バイト列。dashboardはコンテナを再ビルドし、配信されたバンドルに「台帳用の記録」と `/record` の取得・`analysis_summary` が入っていることをcurlで確認した（ブラウザでの表示は未確認）。

見送り: 推奨と判断が食い違うときに記録へ注記を出すこと（食い違いの判定は判断メモの自由文を読む必要があり、決定論で書けないため）。

## 一般化

AIの助言と人の判断は、同じ記録に載せるときも別のキー・別の枠に置く。見出しで区別しても、外部に写された文は見出しごと読まれるとは限らない。判断が無いときは「未」と書き、助言で埋めない。
