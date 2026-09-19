---
{
  "id": "IK-0003",
  "title": "依存版の更新を鮮度判定へ含めず、rootが同じ古い提案を適用できる",
  "status": "resolved",
  "recorded_at": "2026-09-19",
  "observed_at": "2026-09-12",
  "target_revision": "2026-09-12監査修正後（最終SHAは出典未記載）",
  "resolved_at": "2026-09-12",
  "sources": [
    "docs/01-specifications/ux/decision-discussion-audit-2026-09-12.md"
  ],
  "feature_context": {
    "realizing": "複数の根拠に依存するProposalを安全に適用する",
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
      "processing": "high",
      "structure": "medium",
      "connection": "high",
      "governance": "medium"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "処理: 入力契約が妥当な単一計算の誤りではない。構造: rootだけでは依存根拠の版を表せない。接続: 生成時と適用時の依存版の対応が欠けていた。統制: この課題は承認担当の変更ではない。",
    "proposals": []
  },
  "generalization": {
    "level": "general",
    "general_form": "依存版の更新を鮮度判定へ含めず、rootが同じ古い提案を適用できる"
  },
  "pattern": "dependency-version-omitted",
  "discovery": {
    "perspective": [
      "adversarial_review",
      "boundary_walk"
    ],
    "note": "出典の#458監査修正はdurable manifestからroot不変の依存更新もstale判定したと記録している。"
  },
  "resolution": {
    "perspective": [
      "representation_change",
      "carry_through"
    ],
    "note": "依存版をmanifestへ保持し、適用時まで運ぶ。root以外だけを更新する条件で拒否を確認する。",
    "landed_in": [
      "apps/control-server/app/assistant_discussion_proposal.py"
    ],
    "verification": [
      "docs/01-specifications/ux/decision-discussion-audit-2026-09-12.md",
      "apps/control-server/tests/test_assistant_discussion_proposals.py"
    ]
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

出典の#458監査修正はdurable manifestからroot不変の依存更新もstale判定したと記録している。

初期移入は過去の監査記録からの抽出。対象版に対する状態であり、2026-09-19の製品動作の再検証ではない。

## 発見の観点

出典の#458監査修正はdurable manifestからroot不変の依存更新もstale判定したと記録している。 同じ条件を次回の調査でも再現する。

## 解決の観点

依存版をmanifestへ保持し、適用時まで運ぶ。root以外だけを更新する条件で拒否を確認する。

検証欄の出典は過去の実行記録、コード・テストのパスは追跡先。今回これらの製品テストは実行していない。

## 一般化

依存版の更新を鮮度判定へ含めず、rootが同じ古い提案を適用できる。辞書の `dependency-version-omitted` を同様の変更に当て、条件が違えば別の型として扱う。
