---
{
  "id": "IK-0028",
  "title": "code-links・probe-plans生成が出力上限を4096に固定し、推論に枠を使われて応答本文が空になる",
  "status": "resolved",
  "recorded_at": "2026-11-03",
  "observed_at": "2026-11-03",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026（2026-11-03時点のローカルcheckout）",
  "resolved_at": "2026-11-03",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Featureとコードシンボルの対応づけとprobe計画をLLMで生成する",
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
        "aggregation"
      ],
      "connection": [
        "none"
      ],
      "governance": [
        "budget"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "medium",
      "connection": "medium",
      "governance": "medium"
    },
    "cause_status": "hypothesis",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "仮説: 再現1回、同条件の再試行はcompletedで一過性の可能性あり。統制（中確信）: code_mapper.pyとprobe_planner.pyはmax_tokens=4096固定で、reasoningモデルが出力枠を推論に先に使うと本文が残らない。応答本文が空（Expecting value: line 1 column 1）だった事実とは整合するが、推論トークン消費量は未計測。構造（中確信）: draft_generator.pyはINTELLIGENCE_MAX_OUTPUT_TOKENS（既定128,000）を読み、上限の正本が経路ごとに分散していた。処理・接続: 解析処理と前後契約に誤りは見当たらずnone。確定には応答のusage（reasoning_tokens）記録と4096での再現が要る。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "同じ生成器を呼ぶ経路の一部が出力上限を共有設定でなく小さい固定値で持ち、上限内で本文が残らず空応答になる"
  },
  "pattern": "hardcoded-output-budget-diverges",
  "discovery": {
    "perspective": [
      "reproduction",
      "inventory"
    ],
    "note": "空応答の解析失敗を観測し、LLM呼び出し経路ごとのmax_tokensの出所を棚卸しした（4096固定2箇所と環境変数1箇所）。"
  },
  "resolution": {
    "perspective": [
      "canonical_source",
      "order_and_budget"
    ],
    "note": "code_mapper.pyとprobe_planner.pyのmax_tokensをINTELLIGENCE_MAX_OUTPUT_TOKENS（既定128000）に揃えた（コミット74d7a73）。関連テスト77件pass。再ビルド後の実機確認は未実施。空応答の失敗にもHTTP 201を返す点はIK-0026残課題(d)と同じで未対応。",
    "landed_in": [
      "apps/control-server/app/code_mapper.py",
      "apps/control-server/app/probe_planner.py"
    ],
    "verification": [
      "apps/control-server/tests/test_code_mapper_parse.py",
      "apps/control-server/tests/test_feature_code_mapping.py",
      "apps/control-server/tests/test_probe_plan.py"
    ]
  },
  "related": [
    "IK-0026",
    "IK-0027"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-03",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。コミット74d7a73で着地、関連テスト77件pass。実機は再ビルド後未検証。"
    },
    {
      "date": "2026-11-03",
      "field": "resolution.perspective",
      "from": [
        "pending"
      ],
      "to": [
        "canonical_source",
        "order_and_budget"
      ],
      "reason": "出力上限の正本を共有の環境変数に一本化し、予算を推論込みで足りる値にした。"
    }
  ]
}
---

## 課題

症状: `POST /repository/code-links/generate`（provider openai、model gpt-6-luna）が `Failed to parse LLM response: Expecting value: line 1 column 1 (char 0)` でfailed。応答本文が空だった。同条件の再試行はcompleted（link 29件）。

原因（仮説）: `apps/control-server/app/code_mapper.py` と `probe_planner.py` は `max_tokens=4096` 固定、一方 `draft_generator.py` は `INTELLIGENCE_MAX_OUTPUT_TOKENS`（既定128,000）を使う。reasoningモデルは出力トークンを推論に先に使うため、小さい上限では本文が残らないことがある。再現は1回で、再試行で通ったため一過性の可能性もある。推論トークン消費量は未計測。

周辺: 空応答の失敗にもHTTP 201が返る（IK-0026の残課題(d)と同じ）。

対象版: ブランチ `sim-sandbox/ik-0024-0026`（2026-11-03）。起票元: 外部の検証サンドボックス sim-sandbox の実施記録 `sim-sandbox/docs/02-experiments/E-0007-p03-dcf-synthetic-probe-agent.md`（本リポジトリ外のためsourcesには本リポジトリの文書を載せる）。

## 発見の観点

空応答による解析失敗を再現条件とともに記録し、LLMを呼ぶ経路ごとの `max_tokens` の出所を棚卸しした。固定値4096が2箇所、共有の環境変数が1箇所と分散していた。

## 解決の観点

**解決（2026-11-03、対象版: ブランチ `sim-sandbox/ik-0024-0026`）。**

着地先: `code_mapper.py` と `probe_planner.py` の `max_tokens` を `int(os.getenv("INTELLIGENCE_MAX_OUTPUT_TOKENS", "128000"))` に揃えた（コミット `74d7a73`）。

検証: 関連テスト77件pass（2026-11-03の現在の実行）。

unverified: 再ビルド後の実機確認は未実施。原因が仮説のため、実機で空応答が再発しないことの確認が残る。

残課題（未対応）: 空応答の失敗にHTTP 201を返す点（IK-0026 (d)）。応答のusageを記録しておらず、推論による枠消費を確かめられない点。

## 一般化

同じ生成器を呼ぶ経路のうち一部だけが出力上限を小さい固定値で持つと、推論に出力枠を先に使うモデルでは本文が空になりうる。上限は共有設定を正本とし、経路ごとの固定値を置かない。
