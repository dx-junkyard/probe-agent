---
{
  "id": "IK-0029",
  "title": "推論runがLLMの要求・応答・usageを保存せず、検証失敗時にモデルが書いた値を調べられない",
  "status": "resolved",
  "recorded_at": "2026-11-06",
  "observed_at": "2026-11-06",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の ecbceb6",
  "resolved_at": "2026-11-06",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Feature抽出・コード紐付け・probe計画・Experiment解釈をLLMで生成し、その根拠を後から確かめる",
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
        "information"
      ],
      "governance": [
        "review"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "high",
      "connection": "high",
      "governance": "medium"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "確定: intelligence_runsは結果とerror_detailsだけを持ち、要求・応答本文の列も子表も無かった（db.pyのintelligence_runs定義）。llm.pyのクライアントはlast_usage/last_finish_reasonを設定するが呼び出し側が誰も保存しない。Experiment解釈はintelligence_runすら作らずexperiment_analysesを上書きする。構造: 要求・応答を表すデータが無い（representation）。接続: 生成段から記録段へ応答本文が渡らない（information）。統制: 失敗の確認手続に必要な証拠が残らない（review）。処理の誤りは無くnone。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "生成器の入出力を保存せず、解析後の結果と失敗文言だけを残す"
  },
  "pattern": "reasoning-io-not-persisted",
  "discovery": {
    "perspective": [
      "reproduction",
      "data_inspection"
    ],
    "note": "sim-sandbox E-0009でExperiment #4の解釈が「risks must be an array」で失敗し、DBとログのどこにも応答本文が無くrisksの実際の型を確認できなかった。drafts/code-linksの揺れも同様にプロンプト側か応答側かを切り分けられなかった。"
  },
  "resolution": {
    "perspective": [
      "first_class_state",
      "carry_through"
    ],
    "note": "intelligence_run_payloads表を追加し、drafts・code-links・probe-plans・Experiment解釈の各呼び出しの要求（秘密情報のredaction後）と応答（生の全文、検証失敗時も）・usage・finish_reasonをrunに紐付けて保存する。Experiment解釈はrun_type experiment_analysisのintelligence_runとして記録し、experiment_analyses.intelligence_run_idで辿る。GET /intelligence-runs/{id}/payloadsで読む。",
    "landed_in": [
      "apps/control-server/app/intelligence_payloads.py",
      "apps/control-server/app/db.py",
      "apps/control-server/app/intelligence_run_types.py",
      "apps/control-server/app/routes/project_intelligence.py",
      "apps/control-server/app/routes/experiments.py",
      "apps/control-server/app/models.py",
      "shared/schemas/project_intelligence.schema.json"
    ],
    "verification": [
      "apps/control-server/tests/test_intelligence_run_payloads.py",
      "apps/control-server/tests/test_experiment_analysis.py"
    ]
  },
  "related": [
    "IK-0026",
    "IK-0028"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-06",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。sim-sandbox E-0010の1件目として着地、関連テストpass。実機確認はE-0010の観測に記録。"
    }
  ]
}
---

## 課題

症状: Experiment #4（sim-sandbox E-0009）の解釈が `analysis_failed`（`risks must be an array`）になったが、応答本文がどこにも残らず、モデルがrisksを文字列で返したのかobjectで返したのかを確認できなかった。drafts 5回・code-links 3回の揺れについても、どのプロンプトに対してどの応答が返ったかを後から読めなかった。

原因（確定）: `intelligence_runs` は結果の要約と `error_details` だけを持ち、LLMへの要求・応答本文を保存する場所が無かった。`llm.py` のクライアントは `last_usage` / `last_finish_reason` を設定するが、呼び出し側の誰も保存していなかった。Experiment解釈はintelligence_runを作らず `experiment_analyses` の1行を上書きしていた。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `ecbceb6`。起票元: 外部の検証サンドボックス sim-sandbox の `docs/02-experiments/E-0009-probe-agent-reproducibility.md`（本リポジトリ外のためsourcesには本リポジトリの文書を載せる）。

## 発見の観点

解釈の失敗（#4）を再現条件つきで記録し、DB（`/data/probe.db`）とサーバーログを読んで応答本文が無いことを確かめた。LLMを呼ぶ経路（drafts・code-links・probe-plans・analysis）を棚卸しし、どれも応答本文を保存しないことを確認した。

## 解決の観点

**解決（2026-11-06）。**

- 新表 `intelligence_run_payloads`（run_id, kind=request|response, prompt_version, text, usage_json, finish_reason, created_at）。既存行は書き換えない追加のみ。
- `app/intelligence_payloads.py` の `RecordingLLMClient` が生成器に渡すクライアントを包み、呼び出しごとに要求（`redact_messages` 後、送信パラメータ込み）と応答（生の全文）・usage・finish_reasonを記録する。providerの例外時は応答側に `[no response: ...]` を残す。mockクライアントは包まない（生成器の `isinstance(MockLLMClient)` 判定を保つ）。
- drafts / code-links / probe-plans はrun行の挿入直後に同じトランザクションで保存。
- Experiment解釈は `run_type: experiment_analysis`（語彙に追加、shared schemaも更新）のintelligence_runを作り、`experiment_analyses.intelligence_run_id`（追加列）で辿る。
- 読み出し: `GET /intelligence-runs/{id}/payloads`（System境界は他のrouteと同じ `X-Probe-System-Id`）。

検証: `tests/test_intelligence_run_payloads.py`（完了run・検証失敗run・provider例外・code-links/probe-plans・System境界・redaction）と `tests/test_experiment_analysis.py`（解釈runのpayload）pass。

見送り: 本文の大きさの上限は設けていない（SQLiteで全文保存、draftsの要求は最大200k文字程度）。保持期間・削除は未設計。

## 一般化

生成器を呼ぶ経路が解析後の結果だけを残すと、検証失敗や結果の揺れの原因を記録から辿れない。生成の要求・応答・usageはrunと同じ単位で保存し、失敗時も応答本文を残す。
