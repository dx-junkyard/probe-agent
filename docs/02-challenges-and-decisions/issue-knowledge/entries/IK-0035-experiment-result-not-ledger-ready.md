---
{
  "id": "IK-0035",
  "title": "Experimentの結果を外部の台帳に写す形で出す口がなく、教員が詳細のJSONから条件と結果の文を毎回手で組み立てる",
  "status": "resolved",
  "recorded_at": "2026-11-07",
  "observed_at": "2026-11-07",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の d2e0917",
  "resolved_at": "2026-11-07",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Experimentの実行・解釈・人の判断を、外部の検証記録（疑義台帳の外部検証の線）へ写せる形で渡す",
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
    "basis": "確定: 外部検証の線（episteme-graph backend/core/doubt/schema.py の EXTERNAL_REF_KEYS = tool / run_ref / url / conditions / result_summary / executed_at、うち tool / run_ref / conditions / result_summary は必須）に対し、probe-agentは GET /experiments/{id} の詳細（variantごとのtrace・commands・metricsを含む数百KB〜2MBのJSON）しか返さない。接続: 後段の入力契約（文字列6項目）に合う出力がない（contract）。条件の文・結果の文に要る objective・snapshot・variantの意味（risk_note）・最新の解釈・人の判断とメモは揃っているが、記録の形へ運ばれず人の組み立てで落ちうる（information）。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "後段（外部の台帳）が受け取る記録の形を前段が出さず、人が詳細のJSONから毎回組み立てて写す"
  },
  "pattern": "downstream-record-assembled-by-hand",
  "discovery": {
    "perspective": [
      "external_constraint",
      "boundary_walk"
    ],
    "note": "sim-sandbox 第3周の計画（2026-11-07）で、episteme-graphの根拠の線に外部検証の external_ref を足す設計と、probe-agentの GET /experiments/{id} の返却を突き合わせた。第2周では教員がExperiment #2の結果を scope の evidence_quote に手で書き写していた。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "carry_through"
    ],
    "note": "GET /experiments/{id}/record を追加。台帳の external_ref と同じキー（値はすべて文字列）に complete と構造化の控え detail を足して返す。LLM・乱数・現在時刻を使わずExperimentの行から組み立てるので、同じ行からは同じバイト列になる。条件は objective・snapshot #id（baseline_commit 先頭7字）・variant（label・key・risk_note、baselineは基準と明記）、結果は最新の完了した解釈（narrativeと推奨のlabel・key・理由、版があれば v番号）と人の判断（採用ならvariant）・判断メモ。文の言語はIK-0030の _output_language（objectiveのCJK判定）に従う。解釈か判断がまだなら complete=false。url は PROBE_DASHBOARD_URL があるときだけ dashboard の /experiments?experiment={id} を組み立てる（composeの既定 http://localhost:8501）。dashboardのExperiment詳細に「台帳用の記録」とJSONのコピーを足す。",
    "landed_in": [
      "apps/control-server/app/routes/experiments.py",
      "apps/control-server/app/models.py",
      "docker-compose.yml",
      "apps/dashboard/src/api/hooks.ts",
      "apps/dashboard/src/api/types.ts",
      "apps/dashboard/src/pages/experiments.tsx"
    ],
    "verification": [
      "apps/control-server/tests/test_experiment_record.py"
    ]
  },
  "related": [
    "IK-0029",
    "IK-0030",
    "IK-0031"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-07",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。sim-sandbox 第3周 §2.3 として b8828f5 で着地、関連テストpass。実機（再ビルド後）で GET /experiments/2/record・/4/record が200、/999/record が404、同じ記録を2回取って同一バイト列、GET /experiments/2・/4 が再ビルド前と一致。dashboardはホストにnodeが無くコンテナのビルド（tsc -b && vite build）とvitestで確認、dashboardコンテナは再ビルドしていない。"
    }
  ]
}
---

## 課題

症状: Experimentの結果を疑義台帳の外部検証の線（`tool` / `run_ref` / `url` / `conditions` / `result_summary` / `executed_at`）へ記帳するとき、教員は `GET /experiments/{id}` の詳細（variantごとのtrace・commands・metricsを含み、Experiment #2では約1.9MB）から objective・snapshot・variantの意味・最新の解釈・人の判断とメモを拾い、条件の文と結果の文を毎回手で組み立てる。組み立て方は人ごと・回ごとに揺れ、variantの意味（risk_note）や判断メモが落ちうる。第2周では結果を scope の `evidence_quote` に書き写すしかなく、論文の引用と見分けが付かなかった。

原因（確定）: 後段（台帳）の入力契約に合う出力がprobe-agentに無い。必要な情報はExperimentの行に揃っているが、記録の形へ運ぶ口が無い。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `d2e0917`。起票元: sim-sandbox `docs/30-reviews/2026-11-07-round3-plan.md` §2.3（本リポジトリ外）。

## 発見の観点

episteme-graphの `EXTERNAL_REF_KEYS` / `EXTERNAL_REF_REQUIRED_KEYS` と、`GET /experiments/{id}` の返却項目を突き合わせ、必須4項目のうち `tool` / `run_ref` 以外の2つ（条件と結果の文）が出力に無いことを確かめた。

## 解決の観点

**解決（2026-11-07、`b8828f5`）。**

- `GET /experiments/{id}/record`（読み取り専用、他のExperimentの口と同じ認証、存在しない・別Systemは404）。上位6項目は台帳の `external_ref` と同じキーで値はすべて文字列。`complete` と `detail`（experiment_id・feature_id・snapshot_id・baseline_commit・status・variantごとの status / test_pass_rate / criteria_pass_rate・analysis_version・human_decision・human_decision_variant_key）を足す。
- 決定論: LLM・乱数・現在時刻を使わない。`executed_at` は `completed_at` のUTC ISO（秒）、`run_ref` は `experiment:{id}`。同じ行からは同じバイト列になる（テストと実機で確認）。
- 条件の文: objective、snapshot #id（baseline_commit 先頭7字）、variantごとに label（key）と risk_note（baselineは「基準」と明記）。
- 結果の文: 現在の解釈が完了していればそれ、失敗していれば履歴の最新の完了版（narrative と推奨の label（key）・理由、版があれば v番号）。人の判断（採用ならそのvariant）と判断メモ。推奨は助言であることを見出しで示す。
- 言語: IK-0030の `_output_language`（objectiveにCJKがあれば日本語）。見出し・判断の語・括弧を日英で切り替える。
- `complete`: Experimentが completed、完了した解釈があり、人の判断が adopted / rejected / needs_more_data のときだけ true。
- `url`: control serverはdashboardの公開先を知らないので、`PROBE_DASHBOARD_URL` があるときだけ `/experiments?experiment={id}` を組み立てる（composeの既定 `http://localhost:8501`）。無ければ空文字（台帳では任意）。
- dashboard: Experiment詳細に「台帳用の記録」を足し、条件と結果の文を表示、未完ならバッジ、JSONのコピー（`copyText`）を置く。判断の保存で invalidate される `experiments` の下にqueryKeyを置いた。

検証: `tests/test_experiment_record.py`（未判断で complete=false と英語の文・決定論と採用の文・日本語のobjective・解釈失敗で complete=false・PROBE_DASHBOARD_URL・404）6件pass、既存のExperimentテストと合わせ27件pass。dashboardはコンテナのビルド（`tsc -b && vite build`）が通り、vitestは1300 pass / 3 fail（3件は d2e0917 でも同じく落ちる candidate-studio・simulation workbench のテストで、本変更と無関係）。実機: 再ビルド後に Experiment #2 / #4 の記録が200（#2 は complete=true、#4 は未判断で false）、#999 は404、`GET /experiments/2`・`/4` は再ビルド前と同一。

見送り: 台帳への自動記帳（台帳の線は人間の記帳専用という episteme-graph 側の方針に従う）。条件の文に実行コマンド（test / smoke）を含めること（台帳の一行が長くなるため、detail と `GET /experiments/{id}` に任せた）。

## 一般化

外部の記録に写す作業が人の組み立てに残ると、同じ実行から違う記録ができ、意味（variantが何を変えたか、判断の理由）が落ちる。後段の入力契約と同じキーの記録を前段が決定論的に出し、未完であることも記録自身に持たせる。記帳の判断（写すかどうか）は人に残す。
