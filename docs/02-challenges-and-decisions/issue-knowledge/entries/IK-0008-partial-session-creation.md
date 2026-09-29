---
{
  "id": "IK-0008",
  "title": "接続ロックをトランザクションと見なし、作成途中の例外で不完全な行を残す",
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: この出典・抽出範囲では独立した原因を認めないためnone。 governance: 判断・実行・完了の手続に欠陥があり、orderingとして分類。 根拠: #464 検証ラウンド「セッション作成が 1 トランザクションでなかった」。 premise捕捉を例外にしてsession/runが残らないか。プロセス内lockをrollback保証と取り違えていないか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "接続ロックをトランザクションと見なし、作成途中の例外で不完全な行を残す"
  },
  "pattern": "multiwrite-without-atomicity",
  "discovery": {
    "perspective": [
      "doc_code_diff",
      "adversarial_review"
    ],
    "note": "過去の出典「#464 検証ラウンド「セッション作成が 1 トランザクションでなかった」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "order_and_budget"
    ],
    "note": "session・initial run・premiseをBEGIN IMMEDIATE/COMMIT/ROLLBACKの一単位にする。",
    "landed_in": [
      "apps/control-server/app/routes/interview.py"
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

接続ロックをトランザクションと見なし、作成途中の例外で不完全な行を残す。

出典の該当箇所: #464 検証ラウンド「セッション作成が 1 トランザクションでなかった」。

## 発見の観点

premise捕捉を例外にしてsession/runが残らないか。プロセス内lockをrollback保証と取り違えていないか。

## 解決の観点

session・initial run・premiseをBEGIN IMMEDIATE/COMMIT/ROLLBACKの一単位にする。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

接続ロックをトランザクションと見なし、作成途中の例外で不完全な行を残す。

設計・レビュー時の問い: premise捕捉を例外にしてsession/runが残らないか。プロセス内lockをrollback保証と取り違えていないか。
