---
{
  "id": "IK-0006",
  "title": "推論開始時だけ前提を確認し、推論中の変更後も古い結果を保存する",
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: この出典・抽出範囲では独立した原因を認めないためnone。 governance: 判断・実行・完了の手続に欠陥があり、orderingとして分類。 根拠: #464 検証ラウンド「推論後・保存直前の再検証が無かった」。 LLM待機中にheadを変更すると旧結果の保存を拒否するか。チェックする情報の不足（IK-0003）とチェック時機の不足を分ける。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "推論開始時だけ前提を確認し、推論中の変更後も古い結果を保存する"
  },
  "pattern": "precheck-only-validation",
  "discovery": {
    "perspective": [
      "boundary_walk",
      "adversarial_review"
    ],
    "note": "過去の出典「#464 検証ラウンド「推論後・保存直前の再検証が無かった」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "order_and_budget",
      "fail_closed"
    ],
    "note": "推論前にpremise tokenを取得し、保存transaction先頭で再検証する。不一致ではモデル出力を保存せずrunを失敗として記録する。人間の入力は区別して保存する。",
    "landed_in": [
      "apps/control-server/app/canonical_understanding.py",
      "apps/control-server/app/routes/interview.py"
    ],
    "verification": [
      "docs/90-history/project-intelligence.md",
      "apps/control-server/tests/test_canonical_understanding.py"
    ]
  },
  "related": [
    "IK-0011"
  ],
  "view_of": [],
  "history": []
}
---

## 課題

推論開始時だけ前提を確認し、推論中の変更後も古い結果を保存する。

出典の該当箇所: #464 検証ラウンド「推論後・保存直前の再検証が無かった」。

## 発見の観点

LLM待機中にheadを変更すると旧結果の保存を拒否するか。チェックする情報の不足（IK-0003）とチェック時機の不足を分ける。

## 解決の観点

推論前にpremise tokenを取得し、保存transaction先頭で再検証する。不一致ではモデル出力を保存せずrunを失敗として記録する。人間の入力は区別して保存する。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

推論開始時だけ前提を確認し、推論中の変更後も古い結果を保存する。

設計・レビュー時の問い: LLM待機中にheadを変更すると旧結果の保存を拒否するか。チェックする情報の不足（IK-0003）とチェック時機の不足を分ける。
