---
{
  "id": "IK-0016",
  "title": "完了後も測定値を上書きでき、承認が参照した証拠を変更する",
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
        "completion"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、conditionとして分類。 governance: 判断・実行・完了の手続に欠陥があり、completionとして分類。 根拠: Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「完了済み run の改竄防止」。 complete_run後の測定値や実行参照の変更が拒否されるか。完了ゲートだけでなく後続の更新経路も状態を検査するか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "完了後も測定値を上書きでき、承認が参照した証拠を変更する"
  },
  "pattern": "finalized-evidence-mutable",
  "discovery": {
    "perspective": [
      "invariant_audit",
      "adversarial_review"
    ],
    "note": "過去の出典「Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「完了済み run の改竄防止」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "state_transition",
      "fail_closed"
    ],
    "note": "attach_execution/record_measurementに_require_open_runを適用し、完了後の変更を拒否する。",
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

完了後も測定値を上書きでき、承認が参照した証拠を変更する。

出典の該当箇所: Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「完了済み run の改竄防止」。

## 発見の観点

complete_run後の測定値や実行参照の変更が拒否されるか。完了ゲートだけでなく後続の更新経路も状態を検査するか。

## 解決の観点

attach_execution/record_measurementに_require_open_runを適用し、完了後の変更を拒否する。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

完了後も測定値を上書きでき、承認が参照した証拠を変更する。

設計・レビュー時の問い: complete_run後の測定値や実行参照の変更が拒否されるか。完了ゲートだけでなく後続の更新経路も状態を検査するか。
