---
{
  "id": "IK-0030",
  "title": "Experiment解釈がvariantのlabelと数値だけを受け取り意味を推測するうえ、解釈は上書きされ単独で再実行できない",
  "status": "resolved",
  "recorded_at": "2026-11-06",
  "observed_at": "2026-11-06",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の ecbceb6",
  "resolved_at": "2026-11-06",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Experimentの決定的なmetricsをLLMで解釈し、variantの比較を説明する",
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
        "resume"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "medium",
      "connection": "high",
      "governance": "high"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "確定: 解釈プロンプト（routes/experiments.pyの_run_reasoning_analysis）はobjective・allowed_variant_keys・comparison（label・metrics）だけを送り、risk_note・source・patchを送らない。E-0009で「乱流の相関長を半分に」のvariant corr8を2/2回「長い／大きい」と逆に読んだ。出力言語の指示も無く、同じ日本語objectiveで日本語と英語に分かれた。接続: 前段（variant作成時の意図）が解釈段に届かない（information）。統制: experiment_analysesはexperiment_idが主キーで上書きされ、解釈だけの再実行APIが無く、直すにはmetricsごと再runするしかない（resume）。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "解釈器に比較対象の識別子と数値だけを渡し、対象が何を変えたかを渡さず、解釈は上書きされ単独で再実行できない"
  },
  "pattern": "interpretation-without-subject-meaning",
  "discovery": {
    "perspective": [
      "reproduction",
      "trace_walk"
    ],
    "note": "sim-sandbox E-0009でExperiment #3と同一variantの#4・#5を実行し、数値はbit単位で再現する一方、解釈は#3（日本語）と#5（英語）の両方でcorr8を逆読みした。解釈プロンプトの組み立てを追い、risk_noteが渡っていないことを確認した。"
  },
  "resolution": {
    "perspective": [
      "carry_through",
      "state_transition"
    ],
    "note": "(a) POST /experiments/{id}/analyzeで完了済みExperimentの解釈だけを再実行（variantは再実行しない）。(b) experiment_analysis_versions表を追加し、解釈のたびにversionを追記、experiment_analysesは現在値として残す。GET /experiments/{id}はanalysis_history（新しい順）を返す。versioning前の行は初回の書き換え前に1度だけ履歴へ写す。(c) プロンプトにvariantごとのlabel・source・risk_note・patch先頭300字を渡し、labelだけから意味を推測しないよう指示。(d) objectiveにCJK文字があれば日本語、無ければ英語で書くと明示。prompt_versionをexperiment-interpretation-v2に更新。",
    "landed_in": [
      "apps/control-server/app/routes/experiments.py",
      "apps/control-server/app/db.py",
      "apps/control-server/app/models.py"
    ],
    "verification": [
      "apps/control-server/tests/test_experiment_analysis.py",
      "apps/control-server/tests/test_experiments.py"
    ]
  },
  "related": [
    "IK-0029"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-06",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。sim-sandbox E-0010の2件目として着地、関連テストpass。実機での#4再解釈はE-0010の観測に記録。"
    }
  ]
}
---

## 課題

症状（sim-sandbox E-0009）: Experiment #3と同一variantの#4・#5で、サンドボックス実行の数値は全桁一致した。解釈は#3（2026-11-03、日本語）と#5（英語）がどちらも corr8（risk_note「乱流の相関長を半分に（視線セル数が倍）」）を「相関長を大きくする条件」「the longer correlation setting」と逆に読んだ。同じ日本語のobjectiveで出力言語が分かれた。#4は解釈だけが失敗したが、解釈だけを再実行する手段が無く、#5を作り直した。

原因（確定）:
1. 解釈プロンプトに渡るのはobjective・allowed_variant_keys・comparison（variant_key・label・metrics）だけで、variant作成時の `risk_note`・`source`・`patch_text` は渡らない。label「corr8」だけからは「8＝長い」と読むのが自然で、逆読みは入力の欠落による再現する誤り。
2. 出力言語を指示していない。
3. `experiment_analyses` はexperiment_idが主キーで、run開始時にpendingへ戻して上書きする。解釈はrunの最後でしか呼ばれない。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `ecbceb6`。起票元: sim-sandbox `docs/02-experiments/E-0009-probe-agent-reproducibility.md`（本リポジトリ外）。

## 発見の観点

LLMを通らない段（variant実行）と通る段（解釈）を分け、同じ入力で反復した。数値が一致するのに解釈が同じ方向に誤ることから、偶然ではなく入力の欠落と見て、プロンプトの組み立てを追った。

## 解決の観点

**解決（2026-11-06）。**

- (a) `POST /experiments/{id}/analyze`: status completed/failed のExperimentだけ。variantは再実行せずmetricsにも触れない。未完了は409。
- (b) `experiment_analysis_versions`（追加表）に解釈のたびに1行追記し、`experiment_analyses` は現在値（`version_id` で対応）として残す。re-runでも前回の解釈は履歴に残る。versioning前の行は、書き換える直前に1度だけ履歴へ写す（既存行は削除・改変しない）。`GET /experiments/{id}` は `analysis_history`（新しい順）を返す。
- (c) プロンプトの `variants` にvariantごとの `label`・`source`・`risk_note`・`patch_excerpt`（先頭300字）・`patch_truncated` を入れ、「labelだけから意味を推測せずrisk_note等を読む」と明示。
- (d) objectiveにCJK文字があれば `Japanese`、無ければ `English` を `output_language` として渡し、narrative・recommendation_reason・risksをその言語で書くと明示。
- prompt_version `experiment-interpretation-v2`。

検証: `tests/test_experiment_analysis.py`（再解釈で metrics 不変・履歴2件・re-runで履歴保持・未完了409・legacy行の退避・プロンプトにrisk_noteと言語）と既存 `tests/test_experiments.py` pass。

未確認: 指示を足してもモデルがlabelより risk_note を優先して読むかは実機（E-0010）で1回確かめるだけ。率は未計測。

## 一般化

比較対象の識別子と数値だけを解釈器に渡すと、識別子の字面から意味を推測させることになり、同じ方向の誤読が再現する。作成時に人が書いた意図（説明・差分）を解釈段まで運び、解釈は追記で残して単独で再実行できるようにする。
