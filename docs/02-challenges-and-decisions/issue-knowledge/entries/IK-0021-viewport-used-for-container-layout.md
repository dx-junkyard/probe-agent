---
{
  "id": "IK-0021",
  "title": "画面幅だけでレイアウトを選び、パネル併設時の領域縮小を見落とす",
  "status": "resolved",
  "recorded_at": "2026-10-04",
  "observed_at": "2026-09-27",
  "target_revision": "a21403a（#466修正記録の対象版）",
  "resolved_at": "2026-09-27",
  "sources": [
    "docs/02-challenges-and-decisions/ux-audit-2026-09-26.md"
  ],
  "feature_context": {
    "realizing": "画面幅だけでレイアウトを選び、パネル併設時の領域縮小を見落とす",
    "layers": [
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
        "condition"
      ],
      "governance": [
        "none"
      ]
    },
    "axis_confidence": {
      "processing": "high",
      "structure": "high",
      "connection": "medium",
      "governance": "high"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "処理・構造: 単独分岐の計算や状態表現ではなく評価対象の条件の不足。接続: 実際のヘッダー幅をレイアウト選択へ渡していない。統制: 手続の順序や承認は原因ではない。",
    "proposals": []
  },
  "generalization": {
    "level": "instance",
    "general_form": "画面幅だけでレイアウトを選び、パネル併設時の領域縮小を見落とす"
  },
  "pattern": "viewport-used-for-container-layout",
  "discovery": {
    "perspective": [
      "reproduction",
      "boundary_walk"
    ],
    "note": "UX-14の修正結果は、1280pxでもアシスタント併設で重なることを観測し、ヘッダー幅のcontainer queryへ修正した。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "carry_through"
    ],
    "note": "画面全幅を代理にせず、実際に配置される領域の幅を判断条件にする。viewport依存のまま閾値だけ増やす案では併設条件を覆えない。出典では320/390/768/1024/1280pxとパネル併設で重なり0を確認。今回製品テストは再実行していない。",
    "landed_in": [
      "apps/dashboard/src/components/layout/header.tsx"
    ],
    "verification": [
      "docs/02-challenges-and-decisions/ux-audit-2026-09-26.md",
      "apps/dashboard/src/__tests__/ux-audit-466-batch3.test.tsx"
    ]
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

UX-14の修正結果は、1280pxでもアシスタント併設で重なることを観測し、ヘッダー幅のcontainer queryへ修正した。

2026-10-04に滞留候補を登録。過去の修正効果と、この知識を新規案件へ適用した効果は別。現行版の再現・健全性は今回未確認。

## 発見の観点

処理・構造: 単独分岐の計算や状態表現ではなく評価対象の条件の不足。接続: 実際のヘッダー幅をレイアウト選択へ渡していない。統制: 手続の順序や承認は原因ではない。

## 解決の観点

画面全幅を代理にせず、実際に配置される領域の幅を判断条件にする。viewport依存のまま閾値だけ増やす案では併設条件を覆えない。出典では320/390/768/1024/1280pxとパネル併設で重なり0を確認。今回製品テストは再実行していない。

## 一般化

画面幅だけでレイアウトを選び、パネル併設時の領域縮小を見落とす。分類はcandidateのまま利用できる。次の同条件の案件で期待・実測・無効条件を記録し、担当エージェントが更新する。人の分類確認は開始条件ではない。
