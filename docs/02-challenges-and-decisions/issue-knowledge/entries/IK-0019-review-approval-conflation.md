---
{
  "id": "IK-0019",
  "title": "事前レビューと最終承認を区別せず、同一人物の自己承認を許す",
  "status": "resolved",
  "recorded_at": "2026-09-21",
  "observed_at": "2026-08-17",
  "target_revision": "2026-08-17 Epic #394 検証ラウンド修正後（出典に最終SHAなし）",
  "resolved_at": "2026-08-17",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "証拠に基づく処理の比較・承認・状態遷移を成立させる",
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
        "none"
      ],
      "governance": [
        "review"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "medium",
      "connection": "medium",
      "governance": "high"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: 必要な事実を分けるデータ表現に欠陥があり、representationとして分類。 connection: この出典・抽出範囲では独立した原因を認めないためnone。 governance: 判断・実行・完了の手続に欠陥があり、reviewとして分類。 根拠: Epic #394 検証ラウンド2(2026-08-17) / Phase 4「parent / human 承認の分離」。 事前レビューの存在と最終承認者の独立性をそれぞれ検査するか。承認経路を分けても判断者が同じになっていないか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "事前レビューと最終承認を区別せず、同一人物の自己承認を許す"
  },
  "pattern": "review-approval-conflation",
  "discovery": {
    "perspective": [
      "invariant_audit",
      "boundary_walk"
    ],
    "note": "過去の出典「Epic #394 検証ラウンド2(2026-08-17) / Phase 4「parent / human 承認の分離」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "first_class_state",
      "explicit_contract"
    ],
    "note": "parent reviewer・日時・dispositionを別記録にし、gateはendorsedを要求する。approveではreviewerとapproverの同一人物を拒否する。拒否操作にはreviewを要求しない。",
    "landed_in": [
      "apps/control-server/app/stabilization.py"
    ],
    "verification": [
      "docs/90-history/project-intelligence.md",
      "apps/control-server/tests/test_stabilization.py"
    ]
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

事前レビューと最終承認を区別せず、同一人物の自己承認を許す。

出典の該当箇所: Epic #394 検証ラウンド2(2026-08-17) / Phase 4「parent / human 承認の分離」。

## 発見の観点

事前レビューの存在と最終承認者の独立性をそれぞれ検査するか。承認経路を分けても判断者が同じになっていないか。

## 解決の観点

parent reviewer・日時・dispositionを別記録にし、gateはendorsedを要求する。approveではreviewerとapproverの同一人物を拒否する。拒否操作にはreviewを要求しない。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

事前レビューと最終承認を区別せず、同一人物の自己承認を許す。

設計・レビュー時の問い: 事前レビューの存在と最終承認者の独立性をそれぞれ検査するか。承認経路を分けても判断者が同じになっていないか。
