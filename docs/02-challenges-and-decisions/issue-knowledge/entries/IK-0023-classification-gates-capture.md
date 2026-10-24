---
{
  "id": "IK-0023",
  "title": "分類確定を知識登録の条件と取り違え、観測済み知見が索引外に滞留する",
  "status": "resolved",
  "recorded_at": "2026-10-04",
  "observed_at": "2026-10-04",
  "target_revision": "48c52540f6220c57606a986b84e50b8236f725b0 + 着手時未コミット文書（IP-20261004-01）",
  "resolved_at": "2026-10-04",
  "sources": [
    "docs/03-validation/improvement-cycle/2026-10-04-autonomous-knowledge-loop.md",
    "docs/03-validation/improvement-cycle/2026-09-29-assistant-answer-quality.md"
  ],
  "feature_context": {
    "realizing": "新知見を検索・試行・効果検証へ戻す",
    "layers": [
      "cycle_recording",
      "cycle_selection"
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
        "ordering",
        "assignment"
      ]
    },
    "axis_confidence": {
      "processing": "high",
      "structure": "high",
      "connection": "high",
      "governance": "high"
    },
    "cause_status": "confirmed",
    "review": "confirmed",
    "reviewed_by": "coding-agent (2026-10-04 local cycle)",
    "reviewed_at": "2026-10-04",
    "basis": "処理・構造・接続: 既存candidate形式と生成索引は利用可能で、値や参照の伝達不良ではない。統制: #466/#467記録が担当レビュー待ちを登録保留の理由にしており、登録と分類確定の順序・担当を取り違えた。proxy-evidence-for-acceptanceの証拠不足とも、製品のreview-approval-conflationの承認主体混同とも異なる。",
    "proposals": []
  },
  "generalization": {
    "level": "instance",
    "general_form": "分類確定を登録の前提にすると、観測済みの知見が検索・試行へ還流しない"
  },
  "pattern": "classification-gates-capture",
  "discovery": {
    "perspective": [
      "invariant_audit",
      "inventory"
    ],
    "note": "taxonomyのcandidate登録規則と、レビュー待ちで未登録の2件および索引20件を照合した。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "responsibility_move"
    ],
    "note": "登録・分類確認・実適用効果を分離し、担当エージェントへ自律更新を割り当てた。滞留2件をIK-0021/0022として登録。解消範囲は今回の滞留と規則矛盾。長期の再発率・判断時間の改善は未確認。",
    "landed_in": [
      "AGENTS.md",
      "docs/02-challenges-and-decisions/issue-knowledge/taxonomy.md"
    ],
    "verification": [
      "docs/03-validation/improvement-cycle/2026-10-04-autonomous-knowledge-loop.md",
      "docs/tools/test_issue_knowledge.py"
    ]
  },
  "related": [
    "IK-0021",
    "IK-0022"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-10-04",
      "field": "classification.review",
      "from": "candidate",
      "to": "confirmed",
      "reason": "担当エージェントが2件の保留理由と登録規則を照合し、隣接型の証拠不足・製品の承認混同と区別。人の分類確認を待たないユーザー指示に従う。"
    }
  ]
}
---

## 課題

#466/#467で新知見を発見・修正しても、分類の担当レビュー待ちとしてIKへ登録されなかった。

## 発見の観点

候補登録が可能な運用契約と未登録理由を照合した。知識の件数不足を原因としたものではない。

## 解決の観点

人の確認待ちを撤廃してcandidate登録を実施。分類確認は担当が根拠を照合して行い、解決策の効果は実際の適用結果で別に検証する。今回の結果記録が証拠。

## 一般化

登録と成熟を別の状態として扱う。次の滞留・反例を見つけた担当が条件と手順を是正し、人の確認待ちへ戻さない。今回の自律更新を独立した複数回の効果実績と数えない。
