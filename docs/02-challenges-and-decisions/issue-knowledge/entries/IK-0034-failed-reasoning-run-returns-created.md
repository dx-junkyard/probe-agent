---
{
  "id": "IK-0034",
  "title": "drafts・code-links・probe-plansの生成が失敗したrunにもHTTP 201を返し、失敗は本文のstatusを読むまで分からない",
  "status": "resolved",
  "recorded_at": "2026-11-06",
  "observed_at": "2026-11-06",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の ecbceb6",
  "resolved_at": "2026-11-06",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "Feature抽出・コード紐付け・probe計画をAPIから生成し、成否を呼び出し側に伝える",
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
        "contract"
      ],
      "governance": [
        "completion"
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
    "basis": "確定: 3つの生成エンドポイントはstatus_code=201で宣言され、LLMの失敗・出力の検証失敗でもfailedのintelligence_runを記録してそのまま返す（IK-0026残課題(d)、IK-0028でも同じ指摘）。統制: 失敗を記録できたことを作成成功と扱っている（completion）。接続: HTTPの成否という呼び出し側との契約と本文のstatusが食い違う（contract）。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "失敗を記録した結果行を作れたことを成功とみなし、生成の失敗に2xxを返す"
  },
  "pattern": "failure-reported-as-created",
  "discovery": {
    "perspective": [
      "doc_code_diff",
      "boundary_walk"
    ],
    "note": "sim-sandbox E-0002/E-0007で失敗したdrafts・code-linksが201で返り、curlの終了コードやHTTPステータスでは失敗を検知できなかった。round 2の事前調査でエンドポイントのstatus_code宣言と失敗時の返却を突き合わせた。"
  },
  "resolution": {
    "perspective": [
      "explicit_contract",
      "single_point_fix"
    ],
    "note": "失敗したrunは、出力の検証失敗（error_detailsが「Failed to parse LLM response」で始まる）なら422、それ以外（provider失敗、reasoningモデル未設定・mock）なら502で返す。本文は成功時と同じ形（failedのintelligence_runとerror_details、保存された行）に、error_detailsを写したdetailを加える。dashboardはdetailをApiErrorとして表示し、生成hookのinvalidateをonSuccessからonSettledにして失敗runも一覧に反映する。",
    "landed_in": [
      "apps/control-server/app/routes/project_intelligence.py",
      "apps/dashboard/src/api/hooks.ts",
      "apps/dashboard/src/pages/feature-map.tsx"
    ],
    "verification": [
      "apps/control-server/tests/test_failed_run_status.py",
      "apps/control-server/tests/test_feature_code_mapping.py",
      "apps/control-server/tests/test_repository_intelligence.py",
      "apps/control-server/tests/test_probe_plan.py"
    ]
  },
  "related": [
    "IK-0026",
    "IK-0028",
    "IK-0029"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-06",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。sim-sandbox E-0010の5件目として着地、関連テストpass。dashboardはホストにnodeが無くテスト未実行、コンテナのビルド（tsc -b && vite build）で型検査のみ確認。"
    }
  ]
}
---

## 課題

症状: `POST /repository/drafts/generate`・`/repository/code-links/generate`・`/repository/probe-plans/generate` は、LLMの呼び出し失敗・出力の検証失敗・reasoningモデル未設定のどれでも、`status: failed` のintelligence_runを記録してHTTP 201で返す。HTTPステータスだけを見る呼び出し側（curlスクリプト、汎用クライアント）は失敗を成功と取り違える。IK-0026の残課題(d)、IK-0028でも同じ点を指摘済み。

原因（確定）: 3エンドポイントは `status_code=201` で宣言され、失敗時も同じモデルを返していた。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `ecbceb6`。起票元: sim-sandbox `docs/30-reviews/2026-11-06-round2-plan.md` §2（本リポジトリ外）。

## 発見の観点

エンドポイントのstatus_code宣言と失敗時の返却値を突き合わせ、3経路とも同型であることを確かめた。

## 解決の観点

**解決（2026-11-06）。**

- 失敗runのステータス: `error_details` が `Failed to parse LLM response` で始まる（モデルは答えたが出力が検証で落ちた）なら **422**、それ以外（providerの失敗、reasoningモデル未設定、mock禁止）なら **502**。成功は従来どおり201。
- 本文: 成功時と同じ形（`intelligence_run` は status failed + error_details、probe planは `status: rejected` の行）に、`detail`（= error_details）を足す。失敗runは従来どおり保存され、`GET /intelligence-runs/{id}/payloads` でも読める。
- dashboard: `ApiError` は `detail` を表示するので、非2xxでもerror_detailsが出る。生成hook（drafts / code-links / probe-plans）のキャッシュ無効化を `onSuccess` から `onSettled` に変え、失敗runも一覧に反映する。feature-mapのdrafts生成の失敗toastは `Error:` 接頭辞を付けずにメッセージを出す。

検証: `tests/test_failed_run_status.py`（判定規則・draftsの422/502・code-linksの422・probe planの422・mockの502）pass。既存テストの201期待（失敗系6か所）を422/502に更新。dashboardはホストにnodeが無いためvitest未実行、コンテナの再ビルド（`tsc -b && vite build`）で型検査のみ確認。

見送り: reasoningモデル未設定・mockは「上流から有効な応答を得られない」として502に含めた（設定の誤りを503等に分ける案は採らなかった）。

## 一般化

失敗を記録した行を作れたことを「作成成功」と扱うと、HTTPステータスと本文の成否が食い違う。失敗は非2xxで返し、記録済みの内容と理由は本文に残す。出力の検証失敗と上流の失敗は呼び出し側の次の行動が違うので別のステータスにする。
