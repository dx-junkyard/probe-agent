---
{
  "id": "IK-0022",
  "title": "同一リクエストの対象再解決で接続開設の固定費を重ねる",
  "status": "resolved",
  "recorded_at": "2026-10-04",
  "observed_at": "2026-09-29",
  "target_revision": "5441ca0（#470の性能是正）",
  "resolved_at": "2026-09-29",
  "sources": [
    "docs/03-validation/improvement-cycle/2026-09-29-assistant-answer-quality.md"
  ],
  "feature_context": {
    "realizing": "同一リクエストの対象再解決で接続開設の固定費を重ねる",
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
        "responsibility"
      ],
      "connection": [
        "information"
      ],
      "governance": [
        "none"
      ]
    },
    "axis_confidence": {
      "processing": "high",
      "structure": "medium",
      "connection": "medium",
      "governance": "high"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "処理: 接続の解放漏れではない。構造: 同じ対象解決を複数段が重複して担当。接続: 検証済みの解決結果を後段へ共有せず再取得。統制: 評価基準の欠如ではなく処理構成が原因。",
    "proposals": []
  },
  "generalization": {
    "level": "instance",
    "general_form": "同一リクエストの対象再解決で接続開設の固定費を重ねる"
  },
  "pattern": "repeated-resolution-connection-overhead",
  "discovery": {
    "perspective": [
      "data_inspection",
      "reproduction"
    ],
    "note": "固定課題の比較でpre-LLM p95が67.6→79.6msと悪化し、段階計測から関連bundleの接続開設を特定した。"
  },
  "resolution": {
    "perspective": [
      "carry_through",
      "responsibility_move"
    ],
    "note": "同一リクエストの対象解決結果と接続を共有して接続数6→3。永続キャッシュは採らず、LLM後の再解決は維持した。出典の修正後比較はp95比1.003（基準1.10以下）。単独endpointの出力同等性も検証。今回製品テストは再実行していない。",
    "landed_in": [
      "apps/control-server/app/discussion_context_bundle.py"
    ],
    "verification": [
      "docs/03-validation/improvement-cycle/2026-09-29-assistant-answer-quality.md",
      "apps/control-server/tests/test_assistant_context_selection.py"
    ]
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

固定課題の比較でpre-LLM p95が67.6→79.6msと悪化し、段階計測から関連bundleの接続開設を特定した。

2026-10-04に滞留候補を登録。過去の修正効果と、この知識を新規案件へ適用した効果は別。現行版の再現・健全性は今回未確認。

## 発見の観点

処理: 接続の解放漏れではない。構造: 同じ対象解決を複数段が重複して担当。接続: 検証済みの解決結果を後段へ共有せず再取得。統制: 評価基準の欠如ではなく処理構成が原因。

## 解決の観点

同一リクエストの対象解決結果と接続を共有して接続数6→3。永続キャッシュは採らず、LLM後の再解決は維持した。出典の修正後比較はp95比1.003（基準1.10以下）。単独endpointの出力同等性も検証。今回製品テストは再実行していない。

## 一般化

同一リクエストの対象再解決で接続開設の固定費を重ねる。分類はcandidateのまま利用できる。次の同条件の案件で期待・実測・無効条件を記録し、担当エージェントが更新する。人の分類確認は開始条件ではない。
