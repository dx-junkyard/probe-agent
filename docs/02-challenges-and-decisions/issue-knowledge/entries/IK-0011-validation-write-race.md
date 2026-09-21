---
{
  "id": "IK-0011",
  "title": "保存前再検証と書込みが別トランザクションで、検証後の競合を許す",
  "status": "resolved",
  "recorded_at": "2026-09-21",
  "observed_at": "2026-09-14",
  "target_revision": "18dfa26（#464 第2回修正後）",
  "resolved_at": "2026-09-14",
  "sources": [
    "docs/90-history/project-intelligence.md"
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
        "none"
      ],
      "connection": [
        "none"
      ],
      "governance": [
        "ordering"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: この出典・抽出範囲では独立した原因を認めないためnone。 governance: 判断・実行・完了の手続に欠陥があり、orderingとして分類。 根拠: #464 検証ラウンド2「rebuild の再検証と保存が同一トランザクションでなかった」。 再検証の直後に別DB接続から昇格を試みて拒否されるか。推論中だけの競合テスト（IK-0006）ではこの窓を覆えない。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "保存前再検証と書込みが別トランザクションで、検証後の競合を許す"
  },
  "pattern": "check-write-not-atomic",
  "discovery": {
    "perspective": [
      "adversarial_review",
      "boundary_walk"
    ],
    "note": "過去の出典「#464 検証ラウンド2「rebuild の再検証と保存が同一トランザクションでなかった」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "order_and_budget",
      "explicit_contract"
    ],
    "note": "再検証と書込みをdb.write_transactionのBEGIN IMMEDIATEで囲む。遅延BEGINやプロセス内lockだけに依存しない。",
    "landed_in": [
      "apps/control-server/app/db.py",
      "apps/control-server/app/routes/interview.py"
    ],
    "verification": [
      "docs/90-history/project-intelligence.md",
      "apps/control-server/tests/test_canonical_understanding.py"
    ]
  },
  "related": [
    "IK-0006"
  ],
  "view_of": [],
  "history": []
}
---

## 課題

保存前再検証と書込みが別トランザクションで、検証後の競合を許す。

出典の該当箇所: #464 検証ラウンド2「rebuild の再検証と保存が同一トランザクションでなかった」。

## 発見の観点

再検証の直後に別DB接続から昇格を試みて拒否されるか。推論中だけの競合テスト（IK-0006）ではこの窓を覆えない。

## 解決の観点

再検証と書込みをdb.write_transactionのBEGIN IMMEDIATEで囲む。遅延BEGINやプロセス内lockだけに依存しない。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

保存前再検証と書込みが別トランザクションで、検証後の競合を許す。

設計・レビュー時の問い: 再検証の直後に別DB接続から昇格を試みて拒否されるか。推論中だけの競合テスト（IK-0006）ではこの窓を覆えない。
