---
{
  "id": "IK-0013",
  "title": "呼出元が申告したactorを採用し、人間の操作がsystem判断を名乗れる",
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
        "assignment"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、conditionとして分類。 governance: 判断・実行・完了の手続に欠陥があり、assignmentとして分類。 根拠: Epic #394 検証ラウンド(2026-08-17) / Phase 1「provenance の偽装不能化」。 bodyのactor_kindを変えて判断主体を偽装できないか。認証された主体と自己申告の来歴を分けているか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "呼出元が申告したactorを採用し、人間の操作がsystem判断を名乗れる"
  },
  "pattern": "caller-controlled-provenance",
  "discovery": {
    "perspective": [
      "boundary_walk",
      "invariant_audit"
    ],
    "note": "過去の出典「Epic #394 検証ラウンド(2026-08-17) / Phase 1「provenance の偽装不能化」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "responsibility_move",
      "explicit_contract"
    ],
    "note": "actor/actor_kindを入力schemaから除きrouteでPrincipalから導出する。HTTP経由のdeterministic申告を拒否する。",
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

呼出元が申告したactorを採用し、人間の操作がsystem判断を名乗れる。

出典の該当箇所: Epic #394 検証ラウンド(2026-08-17) / Phase 1「provenance の偽装不能化」。

## 発見の観点

bodyのactor_kindを変えて判断主体を偽装できないか。認証された主体と自己申告の来歴を分けているか。

## 解決の観点

actor/actor_kindを入力schemaから除きrouteでPrincipalから導出する。HTTP経由のdeterministic申告を拒否する。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

呼出元が申告したactorを採用し、人間の操作がsystem判断を名乗れる。

設計・レビュー時の問い: bodyのactor_kindを変えて判断主体を偽装できないか。認証された主体と自己申告の来歴を分けているか。
