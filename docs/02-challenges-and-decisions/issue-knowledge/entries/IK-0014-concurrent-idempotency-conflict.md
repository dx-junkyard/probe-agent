---
{
  "id": "IK-0014",
  "title": "同一キーの並行再送を通常の重複と扱えず、競合が失敗になる",
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
        "none"
      ],
      "connection": [
        "none"
      ],
      "governance": [
        "resume"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: この出典・抽出範囲では独立した原因を認めないためnone。 governance: 判断・実行・完了の手続に欠陥があり、resumeとして分類。 根拠: Epic #394 検証ラウンド(2026-08-17) / Phase 1「idempotency の競合窓」、ラウンド2「transition の原子性」。 同一keyの二接続が競合してもeventが一つで、敗者が500やillegal_transitionにならないか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "同一キーの並行再送を通常の重複と扱えず、競合が失敗になる"
  },
  "pattern": "concurrent-retry-not-idempotent",
  "discovery": {
    "perspective": [
      "reproduction",
      "adversarial_review"
    ],
    "note": "過去の出典「Epic #394 検証ラウンド(2026-08-17) / Phase 1「idempotency の競合窓」、ラウンド2「transition の原子性」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "order_and_budget",
      "guardrail_fix"
    ],
    "note": "競合時は勝者eventをduplicateとして返し、transaction内でも重複を再確認する。逐次再試行と並行再送を同じ結果へ収束させる。",
    "landed_in": [
      "apps/control-server/app/evolution_node.py"
    ],
    "verification": [
      "docs/90-history/project-intelligence.md",
      "apps/control-server/tests/test_evolution_node.py"
    ]
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

同一キーの並行再送を通常の重複と扱えず、競合が失敗になる。

出典の該当箇所: Epic #394 検証ラウンド(2026-08-17) / Phase 1「idempotency の競合窓」、ラウンド2「transition の原子性」。

## 発見の観点

同一keyの二接続が競合してもeventが一つで、敗者が500やillegal_transitionにならないか。

## 解決の観点

競合時は勝者eventをduplicateとして返し、transaction内でも重複を再確認する。逐次再試行と並行再送を同じ結果へ収束させる。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

同一キーの並行再送を通常の重複と扱えず、競合が失敗になる。

設計・レビュー時の問い: 同一keyの二接続が競合してもeventが一つで、敗者が500やillegal_transitionにならないか。
