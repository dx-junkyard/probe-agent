---
{
  "id": "IK-0012",
  "title": "状態と本文を別々に選び、異なる候補の情報を一つに表示する",
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
        "target"
      ],
      "governance": [
        "none"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "medium",
      "connection": "high",
      "governance": "medium"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、targetとして分類。 governance: この出典・抽出範囲では独立した原因を認めないためnone。 根拠: #464 検証ラウンド2「candidate の状態と内容が別セッションを指し得た」。 旧セッションに候補を残して新規セッションを作っても、状態と本文が同一revisionに属するか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "状態と本文を別々に選び、異なる候補の情報を一つに表示する"
  },
  "pattern": "projection-target-mismatch",
  "discovery": {
    "perspective": [
      "adversarial_review",
      "boundary_walk"
    ],
    "note": "過去の出典「#464 検証ラウンド2「candidate の状態と内容が別セッションを指し得た」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "canonical_source",
      "carry_through"
    ],
    "note": "先に候補revisionを一意に選び、その所有セッションから状態と本文を出す。same_as_headの場合は候補のsession/briefをNoneにする。",
    "landed_in": [
      "apps/control-server/app/overview_projection.py"
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

状態と本文を別々に選び、異なる候補の情報を一つに表示する。

出典の該当箇所: #464 検証ラウンド2「candidate の状態と内容が別セッションを指し得た」。

## 発見の観点

旧セッションに候補を残して新規セッションを作っても、状態と本文が同一revisionに属するか。

## 解決の観点

先に候補revisionを一意に選び、その所有セッションから状態と本文を出す。same_as_headの場合は候補のsession/briefをNoneにする。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

状態と本文を別々に選び、異なる候補の情報を一つに表示する。

設計・レビュー時の問い: 旧セッションに候補を残して新規セッションを作っても、状態と本文が同一revisionに属するか。
