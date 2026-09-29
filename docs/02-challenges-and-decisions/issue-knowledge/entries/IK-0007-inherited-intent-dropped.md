---
{
  "id": "IK-0007",
  "title": "現セッションの行だけで次世代を構成し、継承済みの確定情報を落とす",
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
        "information"
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
    "basis": "processing: この出典・抽出範囲では独立した原因を認めないためnone。 structure: この出典・抽出範囲では独立した原因を認めないためnone。 connection: 段階間で保持すべき対象・情報・意味・条件・版に欠陥があり、informationとして分類。 governance: この出典・抽出範囲では独立した原因を認めないためnone。 根拠: #464 検証ラウンド「確認済み Intent が次の昇格で消え得た」。 再入力しないまま二世代昇格しても確定したgoalが残るか。読むだけの継承を次の保存へ運んでいるか。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "現セッションの行だけで次世代を構成し、継承済みの確定情報を落とす"
  },
  "pattern": "inherited-information-dropped",
  "discovery": {
    "perspective": [
      "boundary_walk",
      "invariant_audit"
    ],
    "note": "過去の出典「#464 検証ラウンド「確認済み Intent が次の昇格で消え得た」」から抽出。今回の新規再現ではない。"
  },
  "resolution": {
    "perspective": [
      "carry_through",
      "explicit_contract"
    ],
    "note": "親の正準Intentを基底にし、confirmedだけ置換、not_applicableだけ削除する。未確定な再検討では継承値を残す。",
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

現セッションの行だけで次世代を構成し、継承済みの確定情報を落とす。

出典の該当箇所: #464 検証ラウンド「確認済み Intent が次の昇格で消え得た」。

## 発見の観点

再入力しないまま二世代昇格しても確定したgoalが残るか。読むだけの継承を次の保存へ運んでいるか。

## 解決の観点

親の正準Intentを基底にし、confirmedだけ置換、not_applicableだけ削除する。未確定な再検討では継承値を残す。

解決状態・日付は過去の修正記録に基づく。今回（2026-09-21）は実装・テストの追跡先を静的に照合したが、製品テストは再実行していない。現行版での稼働保証ではない。

## 一般化

現セッションの行だけで次世代を構成し、継承済みの確定情報を落とす。

設計・レビュー時の問い: 再入力しないまま二世代昇格しても確定したgoalが残るか。読むだけの継承を次の保存へ運んでいるか。
