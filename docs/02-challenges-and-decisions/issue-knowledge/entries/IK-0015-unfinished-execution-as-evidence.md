---
{
  "id": "IK-0015",
  "title": "参照先が存在するだけで実行済みと認め、未完了runを証拠に使う",
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
        "completion"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: この出典・抽出範囲では独立した原因を認めないためnone。 governance: 判断・実行・完了の手続に欠陥があり、completionとして分類。 根拠: Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「実行参照の完了状態検証」。 参照の存在だけでなく完了状態も確認しているか。実利用を測っていない問題（IK-0004）とは証拠の欠陥が異なる。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "参照先が存在するだけで実行済みと認め、未完了runを証拠に使う"
  },
  "pattern": "unfinished-execution-as-evidence",
  "discovery": {
    "perspective": [
      "invariant_audit",
      "boundary_walk"
    ],
    "note": "過去の出典「Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「実行参照の完了状態検証」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "fail_closed"
    ],
    "note": "attach_executionで参照先Replay/Experimentのcompletedを必須とし、draft/running/failedを409で拒否する。",
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

参照先が存在するだけで実行済みと認め、未完了runを証拠に使う。

出典の該当箇所: Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「実行参照の完了状態検証」。

## 発見の観点

参照の存在だけでなく完了状態も確認しているか。実利用を測っていない問題（IK-0004）とは証拠の欠陥が異なる。

## 解決の観点

attach_executionで参照先Replay/Experimentのcompletedを必須とし、draft/running/failedを409で拒否する。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

参照先が存在するだけで実行済みと認め、未完了runを証拠に使う。

設計・レビュー時の問い: 参照の存在だけでなく完了状態も確認しているか。実利用を測っていない問題（IK-0004）とは証拠の欠陥が異なる。
