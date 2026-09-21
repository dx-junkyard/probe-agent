---
{
  "id": "IK-0018",
  "title": "未知の評価軸に既定の大小方向を与え、不明な優劣を確定する",
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
        "logic"
      ],
      "structure": [
        "none"
      ],
      "connection": [
        "none"
      ],
      "governance": [
        "none"
      ]
    },
    "axis_confidence": {
      "processing": "high",
      "structure": "medium",
      "connection": "medium",
      "governance": "medium"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "processing: 単一処理の判定ロジックに欠陥があり、logicとして分類。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: この出典・抽出範囲では独立した原因を認めないためnone。 governance: この出典・抽出範囲では独立した原因を認めないためnone。 根拠: Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「方向表の fail-closed 化」。 未知dimensionを渡したとき、勝手にhigher-is-betterにならず拒否されるか。語彙伝達の問題ではなく局所分岐の既定値が原因。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "未知の評価軸に既定の大小方向を与え、不明な優劣を確定する"
  },
  "pattern": "unknown-direction-default",
  "discovery": {
    "perspective": [
      "invariant_audit",
      "adversarial_review"
    ],
    "note": "過去の出典「Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「方向表の fail-closed 化」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "single_point_fix",
      "fail_closed"
    ],
    "note": "HIGHER_IS_BETTER.get(dim, True)の既定値を廃止し、有限語彙外はExplorationValidationErrorにする。",
    "landed_in": [
      "apps/control-server/app/exploration_workbench.py"
    ],
    "verification": [
      "docs/90-history/project-intelligence.md",
      "apps/control-server/tests/test_exploration_workbench.py"
    ]
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

未知の評価軸に既定の大小方向を与え、不明な優劣を確定する。

出典の該当箇所: Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「方向表の fail-closed 化」。

## 発見の観点

未知dimensionを渡したとき、勝手にhigher-is-betterにならず拒否されるか。語彙伝達の問題ではなく局所分岐の既定値が原因。

## 解決の観点

HIGHER_IS_BETTER.get(dim, True)の既定値を廃止し、有限語彙外はExplorationValidationErrorにする。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

未知の評価軸に既定の大小方向を与え、不明な優劣を確定する。

設計・レビュー時の問い: 未知dimensionを渡したとき、勝手にhigher-is-betterにならず拒否されるか。語彙伝達の問題ではなく局所分岐の既定値が原因。
