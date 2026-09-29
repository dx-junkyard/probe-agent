---
{
  "id": "IK-0017",
  "title": "指標名と測定範囲を無視し、異なる条件の測定値を同じ順位表で比較する",
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
        "meaning",
        "condition"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、meaning,conditionとして分類。 governance: この出典・抽出範囲では独立した原因を認めないためnone。 根拠: Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「ranking の整合」。 p50とp95、異なるcoverageを同順位表に入れないか。測定値だけでなく比較条件も保持しているか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "指標名と測定範囲を無視し、異なる条件の測定値を同じ順位表で比較する"
  },
  "pattern": "incomparable-measurements-ranked",
  "discovery": {
    "perspective": [
      "invariant_audit",
      "adversarial_review"
    ],
    "note": "過去の出典「Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「ranking の整合」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "carry_through"
    ],
    "note": "比較キーをdimension/metric_nameで統一し、複数指標では明示選択を要求する。coverage不一致は理由付きunrankedにする。",
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

指標名と測定範囲を無視し、異なる条件の測定値を同じ順位表で比較する。

出典の該当箇所: Epic #394 検証ラウンド(2026-08-17) / Phase 2/3「ranking の整合」。

## 発見の観点

p50とp95、異なるcoverageを同順位表に入れないか。測定値だけでなく比較条件も保持しているか。

## 解決の観点

比較キーをdimension/metric_nameで統一し、複数指標では明示選択を要求する。coverage不一致は理由付きunrankedにする。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

指標名と測定範囲を無視し、異なる条件の測定値を同じ順位表で比較する。

設計・レビュー時の問い: p50とp95、異なるcoverageを同順位表に入れないか。測定値だけでなく比較条件も保持しているか。
