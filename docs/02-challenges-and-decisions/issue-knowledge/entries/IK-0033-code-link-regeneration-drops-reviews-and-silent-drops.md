---
{
  "id": "IK-0033",
  "title": "code-link再生成が承認・却下を引き継がず未確認の行で置き換え、解決できずに落ちたlinkも記録しない",
  "status": "resolved",
  "recorded_at": "2026-11-06",
  "observed_at": "2026-11-06",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の ecbceb6",
  "resolved_at": "2026-11-06",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Featureとコードシンボルの対応をLLMで提案し、人が承認・却下する",
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
        "information"
      ],
      "governance": [
        "review"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "medium",
      "connection": "high",
      "governance": "high"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "確定: POST /repository/code-links/generateは新しいrunの行をすべてreview_status proposedで挿入し、GET /repository/code-linksは最新runの行だけを返す。E-0009で、E-0007でacceptedにしたrun 25の29件がDBに残ったまま画面・APIから見えなくなった（統制: 人の確認の記録が再生成で失われる）。code_mapper._parse_mapping_responseは解決できないlinkを個別に落とすが、一部が残ったときは何も記録しない（接続: 生成段の情報が応答・ログに届かない）。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "生成結果の再生成が人の確認状態を引き継がず、生成段で落とした候補も記録しない"
  },
  "pattern": "regeneration-drops-review-state",
  "discovery": {
    "perspective": [
      "reproduction",
      "data_inspection"
    ],
    "note": "sim-sandbox E-0009でcode-linksを3回再生成し、link数が24〜31と振れることと、承認済みrunのlinkがAPIから消えることを観測した。DBを読み取り専用で参照してrun 25の行が残っていることを確かめ、応答・error_details・ログに落ちたlinkが出ないことを確認した。"
  },
  "resolution": {
    "perspective": [
      "carry_through",
      "first_class_state"
    ],
    "note": "再生成時、System内の過去のmapping runでaccepted/rejectedになった(feature_id, path, symbol)の組を集め（最新のレビューが優先）、同じ組の新しいlinkにその状態を引き継ぐ。解決できずに落ちたlinkは{feature_id, raw_symbol, reason}として集め、1件ごとにwarningをログに出し、応答のdropped_linksに返す。dropped_linksと引き継いだ件数carried_over_reviewsはintelligence_runs.diagnostics_json（追加列）に保存し、GET /repository/code-linksでも返す。",
    "landed_in": [
      "apps/control-server/app/code_mapper.py",
      "apps/control-server/app/routes/project_intelligence.py",
      "apps/control-server/app/models.py",
      "apps/control-server/app/db.py"
    ],
    "verification": [
      "apps/control-server/tests/test_code_link_regeneration.py",
      "apps/control-server/tests/test_feature_code_mapping.py",
      "apps/control-server/tests/test_code_mapper_parse.py"
    ]
  },
  "related": [
    "IK-0027",
    "IK-0032"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-06",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。sim-sandbox E-0010の4件目として着地、関連テストpass。実機での再生成は未実施。"
    }
  ]
}
---

## 課題

症状（sim-sandbox E-0009）:
1. code-linksを再生成すると `GET /repository/code-links` は最新runのproposed 29件だけを返し、E-0007でacceptedにしたrun 25の29件（DBには残存）が画面・APIから見えなくなった。
2. link数が24〜31と振れたが、モデルが挙げなかったのか、挙げたが解決できずに落ちたのかを区別できなかった。応答（`system_id / snapshot_id / intelligence_run / links / is_mock`）、`error_details`、サーバーログのどれにも落ちたlinkが出ない。

原因（確定）: 生成エンドポイントは新しいrunの行をすべて `proposed` で挿入し、以前のレビューを参照しない。`_parse_mapping_response` は解決できないlinkを `continue` で落とし、全件が落ちたときだけ失敗にする（IK-0027の修正で個別に落とすようにした結果、一部落ちは無記録になった）。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `ecbceb6`。起票元: sim-sandbox `docs/02-experiments/E-0009-probe-agent-reproducibility.md`（本リポジトリ外）。

## 発見の観点

同じFeature集合でcode-linksを3回再生成し、（Feature, path, symbol）の組の和集合・共通部分を数えた。承認済みrunとの重なりをDBで照合した。

## 解決の観点

**解決（2026-11-06）。**

- 引き継ぎ: 再生成時、同じSystemの過去のmapping runのlinkのうち `accepted` / `rejected` のものを（feature_id, path, qualified_name）で集め（`updated_at` の新しいレビューが優先）、新しいrunの同じ組にその状態で挿入する。snapshotが変わってもpathとシンボル名が同じなら引き継ぐ。
- 落ちたlinkの記録: `_parse_mapping_response` が `{feature_id, raw_symbol, reason}`（reasonは `unknown feature_id` / `symbol not in index`）を集め、1件ごとに `logger.warning("code-link dropped: ...")`。
- 応答: `dropped_links` と `carried_over_reviews` を `FeatureCodeLinksOut` に追加。`intelligence_runs.diagnostics_json`（追加列、nullable）に保存し、`GET /repository/code-links` も同じ値を返す。

検証: `tests/test_code_link_regeneration.py`（accepted/rejectedの引き継ぎ・新規はproposed・dropped_linksの応答とログ・解析関数の収集）と既存の code-link テスト pass。

見送り: 今回のrunに出なかった過去の承認済みlinkは表示しない（最新runだけを返す仕様は維持）。差分表示（前回にあって今回に無いlink）は未実装。

## 一般化

生成結果を再生成で置き換えるとき、人の確認状態を同じ対象に引き継がないと、確認作業が再生成のたびに失われる。生成段で落とした候補は数と中身を記録し、結果の件数の振れを原因ごとに分けられるようにする。
