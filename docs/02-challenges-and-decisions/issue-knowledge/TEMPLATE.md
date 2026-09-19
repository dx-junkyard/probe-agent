# 課題エントリの様式

[入口](README.md) · [分類規則](taxonomy.md)

下のコードブロックの中身をentriesへコピーする。IK-0000とプレースホルダーを置き換える。
JSON互換front matterと4つの本文見出しを維持する。日付は引用符付きISO形式。
resolvedには解決日・着地先・検証証拠、deferredには再開条件・担当（未割当も明示）、
rejectedには不成立と判断した根拠を書く。sourcesと各参照パスはリポジトリ相対。

```markdown
---
{
  "id": "IK-0000",
  "title": "原因を主語にした一文",
  "status": "open",
  "recorded_at": "YYYY-MM-DD",
  "observed_at": "YYYY-MM-DD",
  "target_revision": "確認したコミットまたは対象版",
  "resolved_at": null,
  "sources": [
    "docs/実在する調査記録.md"
  ],
  "feature_context": {
    "realizing": "実現する機能を動詞で書く",
    "layers": [
      "control_server"
    ]
  },
  "classification": {
    "axes": {
      "processing": [
        "unknown"
      ],
      "structure": [
        "unknown"
      ],
      "connection": [
        "unknown"
      ],
      "governance": [
        "unknown"
      ]
    },
    "axis_confidence": {
      "processing": "low",
      "structure": "low",
      "connection": "low",
      "governance": "low"
    },
    "cause_status": "hypothesis",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "仮説: 各軸の判断理由と、確定に必要な調査を書く。",
    "proposals": []
  },
  "generalization": {
    "level": "instance",
    "general_form": "機能名を除いた原因の型"
  },
  "pattern": "辞書に定義した型",
  "discovery": {
    "perspective": [
      "invariant_audit"
    ],
    "note": "何と何を照合したか"
  },
  "resolution": {
    "perspective": [
      "pending"
    ],
    "note": "何が分かれば解けるか",
    "landed_in": [],
    "verification": []
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

症状と原因を分ける。対象版・範囲・現行版での未確認事項を書く。

## 発見の観点

何と何を突き合わせたか。

## 解決の観点

採用案、見送り案、採用条件と検証結果。保留なら再開条件・担当。

## 一般化

機能名を外した型と、再発しうる条件。
```

履歴例（既存historyの末尾へ追加）:

```json
{"date":"2026-09-19","field":"classification.axes.connection","from":["unknown"],"to":["version"],"reason":"生成時と保存時のrevisionを比較して特定"}
```

提案例（低確信が調査不足だけなら追加しない）:

```json
{"kind":"value","target_axis":"connection","neighbor_of":["connection.version"],"statement":"既存の版の定義では区別できない原因と、その根拠を書く","confidence":"medium"}
```
