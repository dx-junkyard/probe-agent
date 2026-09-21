---
{
  "id": "IK-0009",
  "title": "正準未確定時の代替表示が、進行中候補を確定情報の枠に混ぜる",
  "status": "resolved",
  "recorded_at": "2026-09-21",
  "observed_at": "2026-09-13",
  "target_revision": "575a6c4（#464 第1回修正後）",
  "resolved_at": "2026-09-13",
  "sources": [
    "docs/90-history/project-intelligence.md",
    "docs/90-history/canonical-understanding-review-2026-09-13.md"
  ],
  "feature_context": {
    "realizing": "Systemの確定情報と会話の前提を安全に保持する",
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
        "representation"
      ],
      "connection": [
        "meaning"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: 必要な事実を分けるデータ表現に欠陥があり、representationとして分類。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、meaningとして分類。 governance: この出典・抽出範囲では独立した原因を認めないためnone。 根拠: #464 検証ラウンド「Overview が head 未昇格時に最新セッションを正準の枠へ入れていた」。 headが無いとき未確定を表せるか。注記を付けるだけでなく確定枠と候補枠を分けているか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "正準未確定時の代替表示が、進行中候補を確定情報の枠に混ぜる"
  },
  "pattern": "candidate-canonical-conflation",
  "discovery": {
    "perspective": [
      "invariant_audit",
      "boundary_walk"
    ],
    "note": "過去の出典「#464 検証ラウンド「Overview が head 未昇格時に最新セッションを正準の枠へ入れていた」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "first_class_state",
      "explicit_contract"
    ],
    "note": "canonical枠はnot_promotedとして空にし、candidate_state/candidate_briefを別フィールドで返す。",
    "landed_in": [
      "apps/control-server/app/overview_projection.py"
    ],
    "verification": [
      "docs/90-history/project-intelligence.md",
      "apps/control-server/tests/test_canonical_understanding.py"
    ]
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

正準未確定時の代替表示が、進行中候補を確定情報の枠に混ぜる。

出典の該当箇所: #464 検証ラウンド「Overview が head 未昇格時に最新セッションを正準の枠へ入れていた」。

## 発見の観点

headが無いとき未確定を表せるか。注記を付けるだけでなく確定枠と候補枠を分けているか。

## 解決の観点

canonical枠はnot_promotedとして空にし、candidate_state/candidate_briefを別フィールドで返す。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

正準未確定時の代替表示が、進行中候補を確定情報の枠に混ぜる。

設計・レビュー時の問い: headが無いとき未確定を表せるか。注記を付けるだけでなく確定枠と候補枠を分けているか。
