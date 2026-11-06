---
{
  "id": "IK-0032",
  "title": "drafts再生成のたびにFeatureのidが作り直され、既存のExperiment・Probe Plan・code-linkが持つfeature_idと切れる",
  "status": "resolved",
  "recorded_at": "2026-11-06",
  "observed_at": "2026-11-06",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の ecbceb6",
  "resolved_at": "2026-11-06",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "リポジトリからFeature Mapを抽出し、Experiment・Probe Plan・code-linkの参照先にする",
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
      "governance": "low"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "確定: Feature抽出プロンプト（draft_generator.pyの_DRAFT_PROMPT_TEMPLATE）は前回のFeature一覧を受け取らず、idの付け方の指示も「kebab-case」だけ。E-0009で同一snapshotのdrafts 5回で、DCFのidが4種類（estimation / magnetic-field-test / test / exercise）、Experiment #3のfeature_idは5回中1回しか出なかった。接続: 前回の生成結果（id）が次の生成に運ばれない（version）。構造: feature_idは生成のたびにLLMが作る文字列で、同一性を表す別の表現が無い（representation）。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "再生成のたびに生成器が識別子を新しく作り、前回の識別子を参照する後段と切れる"
  },
  "pattern": "regenerated-identifier-drift",
  "discovery": {
    "perspective": [
      "reproduction",
      "data_inspection"
    ],
    "note": "sim-sandbox E-0009でsnapshot #6のdraftsを5回再生成し、概念（TOV/BBH/DCF/評価）は毎回出るのにidのJaccardが連続回で0.14〜0.60しかないことを表にした。"
  },
  "resolution": {
    "perspective": [
      "carry_through",
      "representation_change"
    ],
    "note": "drafts再生成時に、Systemの最新completed drafts runのFeature一覧（feature_id・name・evidenceの先頭3ファイル）をプロンプトに写せる形で渡し、同じ利用者価値なら同じidをそのまま写し、本当に新しいFeatureだけ新しいidを作るよう指示する。解析後、前回に無いidはidとnameのslugで前回のidとnameに照合し、一致すれば前回のidに置き換える（各前回idは1つのFeatureにだけ与える）。応答のfeature_id_resolutionにreused / resolved（from→to）/ newとprevious_run_idを返す。prompt_versionをv2に。",
    "landed_in": [
      "apps/control-server/app/draft_generator.py",
      "apps/control-server/app/routes/project_intelligence.py",
      "apps/control-server/app/models.py"
    ],
    "verification": [
      "apps/control-server/tests/test_feature_id_stability.py"
    ]
  },
  "related": [
    "IK-0026",
    "IK-0027"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-06",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。sim-sandbox E-0010の3件目として着地、関連テストpass。実機での再生成による安定度の計測は未実施。"
    }
  ]
}
---

## 課題

症状（sim-sandbox E-0009）: snapshot #6でdraftsを5回再生成すると、概念は毎回同じ（TOV / BBH / DCF / 評価・成果物）なのにidが回ごとに変わった（idのJaccardは連続回で0.29, 0.14, 0.33, 0.60）。DCFのidは5回で4種類。Experiment #3の `feature_id: dcf-synthetic-field-exercise` は5回中1回しか出ず、再生成後の最新Featureと既存のExperiment・Probe Planが繋がらなくなる。

原因（確定）: Feature抽出プロンプトは前回のFeature一覧を受け取らず、idの指示は「kebab-case」だけ。idは生成のたびにモデルが作る文字列で、前回と同じFeatureかを判定する仕組みが無い。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `ecbceb6`。起票元: sim-sandbox `docs/02-experiments/E-0009-probe-agent-reproducibility.md`（本リポジトリ外）。

## 発見の観点

入力（snapshot）を固定した反復で、概念単位とid単位の一致率を分けて表にした。概念が安定しid・名前だけが揺れることから、生成器に前回の結果が渡っていないことを疑い、プロンプトを確認した。

## 解決の観点

**解決（2026-11-06）。** sim-sandbox K-0001（写せる形で提示 → 正規化 → 解決）を識別子の引き継ぎに適用。

- 提示: 再生成時、Systemの最新completed drafts run（snapshotは問わない）のFeatureを `- feature_id: X | name: Y | evidence: a, b, c` の形で「Previously identified features」節としてプロンプトに入れ、同じ利用者価値ならidをそのまま写す、新しいFeatureだけ新idを作る、必要なければ分割・統合しない、と指示する。
- 解決: 前回に無いidは、idとnameのslugを前回のidとnameのslugに照合し、一致すれば前回のidに置き換える。前回の各idは1つのFeatureにだけ与える（2つのFeatureが1つに潰れない）。
- 記録: 応答の `feature_id_resolution`（`previous_run_id`, `reused`, `resolved: [{from, to}]`, `new`）。
- prompt_version `v2`。

検証: `tests/test_feature_id_stability.py`（初回は一覧なし・2回目に写せる形で提示・名前一致でのid解決・前回idの重複割当なし）pass。

未確認・残り: 実機で5回再生成したときのidの一致率は未計測（E-0009と同じ条件での再計測が要る）。名前もidも変わったFeature（意味は同じ）は新規扱いになる。前回のFeatureが消えたことは応答に出していない。

## 一般化

生成器に識別子を作らせ、再生成で前回の識別子を渡さないと、同じ対象に別の識別子が付き、後段の参照が切れる。前回の識別子を写せる形で渡し、生成後に正規形で前回へ解決し、引き継ぎと新規の区別を記録する。
