---
{
  "id": "IK-0025",
  "title": "隔離実行がネットワークを禁じる一方で依存の供給経路を定めず、第三者ライブラリに依存する対象を実行できない",
  "status": "open",
  "recorded_at": "2026-11-03",
  "observed_at": "2026-11-03",
  "target_revision": "2026-11-03 時点のローカル checkout（Compose クイックスタート構成、PROBE_EXECUTION_BACKEND=inprocess）",
  "resolved_at": null,
  "sources": [
    "docs/04-operations/deployment-https.md",
    "docs/01-specifications/capabilities/execution-modes.md"
  ],
  "feature_context": {
    "realizing": "第三者ライブラリに依存する対象リポジトリのtest・smoke・experimentを隔離して実行する",
    "layers": [
      "control_server",
      "deployment",
      "documentation"
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
        "contract"
      ],
      "governance": [
        "none"
      ]
    },
    "axis_confidence": {
      "processing": "high",
      "structure": "medium",
      "connection": "medium",
      "governance": "medium"
    },
    "cause_status": "hypothesis",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "仮説: 処理: validation_runner.pyがruntime.network=trueを拒否するのは意図した制約で、単一処理の誤りではない。接続: probe-agent.ymlのcommands.install（依存導入の受け口）とruntime.network=false（隔離契約）が、オフライン導入手段なしでは両立しない。構造: 依存を誰が用意するか（対象リポジトリのvendored wheels、control-server image、worker image）の責務が定まっていない。統制: 手順の順序・完了条件の問題ではないためnone。コンテナへdocker exec pip installで先入れする抜け道は再作成で消え、製品手順として文書化されていない。どの供給経路が設計に合うかはprobe-agent側の判断で確定する。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "隔離実行で外部取得を禁じたまま、実行に要る依存の供給経路と担当を定めない"
  },
  "pattern": "isolated-runtime-dependency-unprovisioned",
  "discovery": {
    "perspective": [
      "boundary_walk",
      "external_constraint"
    ],
    "note": "numpy/scipyを使うTOV積分コードを対象に、network=falseの制約・commands.install・control-serverのpython環境（import numpyがModuleNotFoundError）を突き合わせた。"
  },
  "resolution": {
    "perspective": [
      "pending"
    ],
    "note": "未実行の候補: 対象リポジトリ側のvendored wheelsをcommands.installからpip install --no-index --find-linksする経路を案内する／control-server imageへ依存を追加するbuild arg／worker backendでのイメージ指定。採否はprobe-agent側の判断。",
    "landed_in": [],
    "verification": []
  },
  "related": [
    "IK-0024"
  ],
  "view_of": [],
  "history": []
}
---

## 課題

症状: `probe-agent.yml` の `runtime.network` は `validation_runner.py` がtrueを拒否するため、`commands.install` で `pip install` できない。control-serverコンテナのpythonにはnumpy/scipy/pytestが入っておらず（`python -c "import numpy"` がModuleNotFoundError）、numpy/scipyを使うTOV積分コードのような対象リポジトリはprobe-agentのtest/smoke/experimentを走らせられない。

原因（仮説）: 隔離実行で外部取得を禁じる契約に対し、依存をオフラインで持ち込む経路と担当が定まっていない。bwrapは `--ro-bind / /` でコンテナのファイルシステムを見せるため、`docker exec ... pip install` でコンテナのpythonへ先入れすれば隔離実行からimportできる想定だが、コンテナ再作成で消える運用上の抜け道で、製品の手順として文書化されていない。

対象版: 2026-11-03時点のローカルcheckout（Composeクイックスタート構成）。起票元: 外部の検証サンドボックス sim-sandbox の実施記録 `sim-sandbox/docs/02-experiments/E-0002-p01-tov-probe-agent.md`（probe-agent リポジトリ外のためsourcesには載せず、関連する本リポジトリの運用・仕様文書を載せる）。未確認: 先入れ方式で実際に隔離実行からimportできるか（同構成ではIK-0024によりbwrap自体が動かない）、worker backend構成での挙動。

## 発見の観点

依存を持つ対象リポジトリを登録する境界で、network=falseの制約、commands.installの受け口、control-serverのpython環境を突き合わせた。

## 解決の観点

未実行（pending）。候補: (1) 対象リポジトリ側のvendored wheelsを `commands.install` から `pip install --no-index --find-links` する経路をREADMEで案内する。(2) control-server imageへ依存を追加するbuild arg。(3) worker backendでのイメージ指定。どれが設計に合うかはprobe-agent側の判断。

## 一般化

隔離のために外部取得を禁じるなら、実行に要る依存を誰がどの経路で供給するかを同時に契約しないと、隔離契約を守る対象ほど実行できなくなる。
