---
{
  "id": "IK-0005",
  "title": "昇格結果で会話開始時の前提を上書きし、旧履歴と新前提を混在させる",
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
        "representation"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: 必要な事実を分けるデータ表現に欠陥があり、representationとして分類。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、versionとして分類。 governance: この出典・抽出範囲では独立した原因を認めないためnone。 根拠: #464 検証ラウンド「昇格が同じセッションの不変 premise を上書きしていた」。 昇格前後でbase revision/digest/jsonが不変か。結果を記録するために入力の履歴を書き換えていないか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "昇格結果で会話開始時の前提を上書きし、旧履歴と新前提を混在させる"
  },
  "pattern": "historical-premise-overwritten",
  "discovery": {
    "perspective": [
      "invariant_audit",
      "doc_code_diff"
    ],
    "note": "過去の出典「#464 検証ラウンド「昇格が同じセッションの不変 premise を上書きしていた」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "first_class_state",
      "state_transition"
    ],
    "note": "base premiseは不変に保ち、昇格結果だけをresult revisionへ記録する。新前提で続ける場合は明示的rebaseで別セッションにする。",
    "landed_in": [
      "apps/control-server/app/canonical_understanding.py"
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

昇格結果で会話開始時の前提を上書きし、旧履歴と新前提を混在させる。

出典の該当箇所: #464 検証ラウンド「昇格が同じセッションの不変 premise を上書きしていた」。

## 発見の観点

昇格前後でbase revision/digest/jsonが不変か。結果を記録するために入力の履歴を書き換えていないか。

## 解決の観点

base premiseは不変に保ち、昇格結果だけをresult revisionへ記録する。新前提で続ける場合は明示的rebaseで別セッションにする。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

昇格結果で会話開始時の前提を上書きし、旧履歴と新前提を混在させる。

設計・レビュー時の問い: 昇格前後でbase revision/digest/jsonが不変か。結果を記録するために入力の履歴を書き換えていないか。
