---
{
  "id": "IK-0010",
  "title": "固定した版の表示が可変行を参照し、版を変えずに内容が変わる",
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
        "version"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、versionとして分類。 governance: この出典・抽出範囲では独立した原因を認めないためnone。 根拠: #464 検証ラウンド2「正準 Brief と Purpose Chain が昇格後も可変 Intent を読んでいた」。 昇格元でgoalを編集してもheadのVisionが不変か。依存版の検査漏れ（IK-0003）ではなく読取り元の選択を検査する。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "固定した版の表示が可変行を参照し、版を変えずに内容が変わる"
  },
  "pattern": "frozen-view-live-dependency",
  "discovery": {
    "perspective": [
      "adversarial_review",
      "boundary_walk"
    ],
    "note": "過去の出典「#464 検証ラウンド2「正準 Brief と Purpose Chain が昇格後も可変 Intent を読んでいた」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "canonical_source",
      "carry_through"
    ],
    "note": "BriefとPurpose Chainへintent_overrideを渡し、revisionのpremise_jsonに凍結されたIntentを読む。引用IDと意味のdigestを分離する。",
    "landed_in": [
      "apps/control-server/app/overview_projection.py",
      "apps/control-server/app/canonical_understanding.py",
      "apps/control-server/app/purpose_chain.py"
    ],
    "verification": [
      "docs/90-history/project-intelligence.md",
      "apps/control-server/tests/test_canonical_understanding.py"
    ]
  },
  "related": [
    "IK-0003"
  ],
  "view_of": [],
  "history": []
}
---

## 課題

固定した版の表示が可変行を参照し、版を変えずに内容が変わる。

出典の該当箇所: #464 検証ラウンド2「正準 Brief と Purpose Chain が昇格後も可変 Intent を読んでいた」。

## 発見の観点

昇格元でgoalを編集してもheadのVisionが不変か。依存版の検査漏れ（IK-0003）ではなく読取り元の選択を検査する。

## 解決の観点

BriefとPurpose Chainへintent_overrideを渡し、revisionのpremise_jsonに凍結されたIntentを読む。引用IDと意味のdigestを分離する。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

固定した版の表示が可変行を参照し、版を変えずに内容が変わる。

設計・レビュー時の問い: 昇格元でgoalを編集してもheadのVisionが不変か。依存版の検査漏れ（IK-0003）ではなく読取り元の選択を検査する。
