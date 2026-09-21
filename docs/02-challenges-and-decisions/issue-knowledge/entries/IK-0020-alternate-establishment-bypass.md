---
{
  "id": "IK-0020",
  "title": "通常の状態遷移APIが証拠付き固定化ゲートを迂回できる",
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
        "condition"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、conditionとして分類。 governance: 判断・実行・完了の手続に欠陥があり、reviewとして分類。 根拠: Epic #394 検証ラウンド(2026-08-17) / Phase 1「固定化ゲートの迂回閉鎖」。 同じ確立状態へ入れる全公開経路を列挙し、証拠・承認ゲートを迂回できないか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "通常の状態遷移APIが証拠付き固定化ゲートを迂回できる"
  },
  "pattern": "alternate-path-gate-bypass",
  "discovery": {
    "perspective": [
      "boundary_walk",
      "invariant_audit"
    ],
    "note": "過去の出典「Epic #394 検証ラウンド(2026-08-17) / Phase 1「固定化ゲートの迂回閉鎖」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "canonical_source",
      "fail_closed"
    ],
    "note": "公開transitionsではmonitoring以外からestablishedへの遷移を拒否し、stabilization approveへ集約する。monitoring停止の経路は維持する。",
    "landed_in": [
      "apps/control-server/app/routes/evolution_nodes.py"
    ],
    "verification": [
      "docs/90-history/project-intelligence.md",
      "apps/control-server/tests/test_evolution_node_api.py"
    ]
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

通常の状態遷移APIが証拠付き固定化ゲートを迂回できる。

出典の該当箇所: Epic #394 検証ラウンド(2026-08-17) / Phase 1「固定化ゲートの迂回閉鎖」。

## 発見の観点

同じ確立状態へ入れる全公開経路を列挙し、証拠・承認ゲートを迂回できないか。

## 解決の観点

公開transitionsではmonitoring以外からestablishedへの遷移を拒否し、stabilization approveへ集約する。monitoring停止の経路は維持する。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

通常の状態遷移APIが証拠付き固定化ゲートを迂回できる。

設計・レビュー時の問い: 同じ確立状態へ入れる全公開経路を列挙し、証拠・承認ゲートを迂回できないか。
