---
{
  "id": "IK-0001",
  "title": "生成側へ有限語彙が伝わらず、表示名が保存用の値として提案される",
  "status": "resolved",
  "recorded_at": "2026-09-19",
  "observed_at": "2026-09-12",
  "target_revision": "c9859ac556497d8a62cb677f8926268ec010ba3c を起点とする2026-09-12監査修正後（最終SHAは出典未記載）",
  "resolved_at": "2026-09-12",
  "sources": [
    "docs/01-specifications/ux/decision-discussion-audit-2026-09-12.md"
  ],
  "feature_context": {
    "realizing": "対話から受入条件を生成してフォームへ反映する",
    "layers": [
      "control_server",
      "dashboard"
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
        "contract",
        "meaning"
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
    "basis": "処理: 正規値の処理不良ではない。構造: 表現を変える必要はない。接続: 生成側と適用側で語彙契約と表示名の意味が一致していなかった。統制: 担当や完了手続の変更は根拠にない。",
    "proposals": []
  },
  "generalization": {
    "level": "general",
    "general_form": "生成側へ有限語彙が伝わらず、表示名が保存用の値として提案される"
  },
  "pattern": "generation-contract-mismatch",
  "discovery": {
    "perspective": [
      "data_inspection",
      "boundary_walk"
    ],
    "note": "出典の実API・実LLM観察5で、verification_methodに表示名が生成され、有限enum提示と不正値拒否の後に正規値で再生成された。"
  },
  "resolution": {
    "perspective": [
      "vocabulary_table",
      "guardrail_fix"
    ],
    "note": "生成と適用の両方が同じ有限語彙を参照する。ローカル単体テストだけでは外部生成の語彙逸脱を見逃しうる。",
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

出典の実API・実LLM観察5で、verification_methodに表示名が生成され、有限enum提示と不正値拒否の後に正規値で再生成された。

初期移入は過去の監査記録からの抽出。対象版に対する状態であり、2026-09-19の製品動作の再検証ではない。

## 発見の観点

出典の実API・実LLM観察5で、verification_methodに表示名が生成され、有限enum提示と不正値拒否の後に正規値で再生成された。 同じ条件を次回の調査でも再現する。

## 解決の観点

生成と適用の両方が同じ有限語彙を参照する。ローカル単体テストだけでは外部生成の語彙逸脱を見逃しうる。

検証欄の出典は過去の実行記録、コード・テストのパスは追跡先。今回これらの製品テストは実行していない。

## 一般化

生成側へ有限語彙が伝わらず、表示名が保存用の値として提案される。辞書の `generation-contract-mismatch` を同様の変更に当て、条件が違えば別の型として扱う。
