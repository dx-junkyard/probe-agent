---
{
  "id": "IK-0039",
  "title": "interviewの提案でLLMが書いたsymbol_idとfeature_idを、渡した候補と照合せずにinterview_proposalへ保存している",
  "status": "resolved",
  "recorded_at": "2026-11-07",
  "observed_at": "2026-11-07",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の 0de947a",
  "resolved_at": "2026-11-07",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "interviewの対話で、シンボルごとのメタデータとprobe計画の提案をLLMに作らせ、人の承認を経てmaterializeする",
    "layers": [
      "control_server",
      "dashboard"
    ]
  },
  "classification": {
    "axes": {
      "processing": [
        "input_handling"
      ],
      "structure": [
        "responsibility"
      ],
      "connection": [
        "contract"
      ],
      "governance": [
        "assignment"
      ]
    },
    "axis_confidence": {
      "processing": "medium",
      "structure": "high",
      "connection": "medium",
      "governance": "medium"
    },
    "cause_status": "confirmed",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "確定: interview_agent.pyの出力形は proposals[].symbol_id（整数、例 42）と probe_plan.feature_id を要求し、generate_interview_turnは列挙値（element_type等）とdenylistだけを検査する。routes/interview.pyはそれをそのままinterview_proposalの symbol_id・feature_id にINSERTする。path / qualified_name が文脈パックに無いシンボルでも、symbol_idが別のシンボルを指していても、feature_idが存在しないキーでも通る。プロンプトには既知のFeatureの一覧を渡していない。構造: DBのキー（symbol_id）とFeatureのキーをLLMの出力から受け取っている（responsibility）。接続: 渡した候補集合と、受け取る参照の契約が照合されない（contract）。統制: 付番の担当（assignment）。処理: 候補外の参照を扱わない（input_handling）。",
    "proposals": []
  },
  "generalization": {
    "level": "general",
    "general_form": "生成器が書いた参照（DBのid・キー）を、生成器に渡した候補と照合せずに保存する"
  },
  "pattern": "llm-assigned-stable-identifier",
  "discovery": {
    "perspective": [
      "inventory",
      "trace_walk"
    ],
    "note": "利用者の規則（2026-11-07）の棚卸しで、interview_agent.pyの出力形の symbol_id / feature_id を routes/interview.py の interview_proposal へのINSERTまで追い、間に照合が無いことを確かめた。"
  },
  "resolution": {
    "perspective": [
      "responsibility_move",
      "explicit_contract"
    ],
    "note": "出力形から symbol_id を外し（来ても無視）、シンボルは path + qualified_name で指させる。文脈パックからも symbol_id を除いて見せる。系が候補集合（文脈パックのシンボルとentrypointのhandler、current understandingがevidenceに挙げるファイルの索引済みシンボル）を (path, qualified_name) → symbol_id（snapshotのcode_symbolsから）として作り、候補外の提案はその提案だけ落とし、symbol_idは候補から付け直す（違っていれば corrected として記録）。feature_id は「## Known features」（Feature台帳 IK-0038 の生きたキー、未作成なら最新drafts run）から写させ、一致しない値は空にして提案は残す。調整は proposal_diagnostics として応答とrunの diagnostics_json に残す（K-0001）。prompt interview-v9 / schema interview-v3。",
    "landed_in": [
      "apps/control-server/app/interview_agent.py",
      "apps/control-server/app/routes/interview.py",
      "apps/control-server/app/feature_registry.py",
      "apps/control-server/app/models.py",
      "apps/dashboard/src/api/types.ts"
    ],
    "verification": [
      "apps/control-server/tests/test_interview_reference_validation.py",
      "apps/control-server/tests/test_interview_agent.py",
      "apps/control-server/tests/test_interview_type_parity.py"
    ]
  },
  "related": [
    "IK-0037",
    "IK-0038"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-07",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。利用者の規則（2026-11-07）の棚卸しの3件目として着地、関連テストpass。実機（docker再ビルド後）での対話の確認は未実施。"
    }
  ]
}
---

## 課題

症状（棚卸しで確認）: interviewの対話（`/interview/sessions/{id}/dialogue-turn`、提案生成）で、LLMが各提案に `symbol_id`（整数）と `probe_plan.feature_id` を書き、`routes/interview.py` がそのまま `interview_proposal` にINSERTする。文脈パックに無いシンボル、別のシンボルを指す `symbol_id`、存在しない `feature_id` がそのまま承認候補になり、承認・materialize・Feature別の集計で誤った対象に紐づきうる。

原因（確定）: 出力形がDBのキー（`symbol_id`）とFeatureのキーをLLMに書かせており、受け取った参照を渡した候補集合と照合する段が無い。プロンプトに既知のFeatureの一覧も無いので、`feature_id` は自由記述になっていた。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `0de947a`。起票元: sim-sandbox `docs/30-reviews/2026-11-07-llm-id-audit.md`（本リポジトリ外）。

## 発見の観点

利用者の規則（下の一般化）に照らした棚卸しで、出力形 → `InterviewProposalResult` → INSERT の間を追い、検査されるのが列挙値とdenylistだけであることを確かめた。

## 解決の観点

**解決（2026-11-07）。** K-0001（正規化 → 解決 → その項目だけ落とす）を参照の照合に適用。

- 出力形（`interview-v9` / `interview-v3`）から `symbol_id` を外し、「シンボルは path と qualified_name で指す、symbol id を書かない」と明記。旧形の `symbol_id` は受けて無視する。文脈パックはプロンプトに出すとき `symbol_id` を除く（エビデンス選択のプロンプトも同じ）。
- 候補集合（`_interview_symbol_candidates`）: 文脈パックのシンボルとentrypointのhandler、current understandingがevidenceに挙げるファイルの索引済みシンボル（system prompt がそれらも提案してよいとしているため）。値は snapshot の `code_symbols.id`。
- 照合（`validate_proposal_refs`）: `./` を除いた (path, qualified_name) が候補に無ければその提案だけ落とす（`proposal_dropped` / `symbol_not_in_candidates`）。`symbol_id` は候補から付け直し、LLMの値が違えば `symbol_id_corrected`（from / to）。`feature_id` は「## Known features」に出したキー（完全一致か大文字小文字違い）に解決し、外れたら空にして提案は残す（`feature_id_cleared`）。
- Known features: Feature台帳（IK-0038）の生きたキーと表示名。台帳が未作成なら最新completed drafts runのid（読み取りのみ）。
- 記録: `InterviewDialogueTurnOut.proposal_diagnostics` と、対話runの `intelligence_runs.diagnostics_json`（`{"proposal_diagnostics": [...]}`）。dashboardの型（`types.ts`）に任意項目として追加（型の同期テストのため。表示は変えていない）。

検証: `tests/test_interview_reference_validation.py`（プロンプトにsymbol_idが無くKnown featuresを出す、symbol_idの付け直しと候補外の提案の除去、feature_idの解決と消去と `./` の正規化、ルートがsnapshotのsymbol_idで候補を渡し診断を応答とrunに残す）4件pass。denylistのテスト4件は提案するシンボルを文脈パックに入れる形に直した。interview系テスト計597件pass。

見送り・残り: `POST /interview/sessions/{id}/proposals`（クライアントがaudit付きで提案を直接送る経路）の `symbol_id` は今回照合していない（LLM出力ではなくAPI入力のため。同じ照合を掛けるかは別件）。materialize の component_id（`_component_id_for`）は系の決定論的導出で、IK-0037の付番とは式が違う（IK-0037の残りに記載）。実機での対話の確認は未実施。

## 一般化

利用者の規則（2026-11-07）: LLM APIで生成するとき、変わってはならない識別子・個別名はLLMに再生成させず、系が付番・保持する。LLMはそれに紐づく内容だけを返す（または new と示す）。例外は理由を明記する。生成器が参照を書く必要があるなら、自然キー（path + qualified_name）か系が渡した候補の値で指させ、保存の前に渡した候補集合と照合し、DBのidは系が付け直す。外れた参照はその項目だけ落とすか空にし、何をしたかを記録に残す。
