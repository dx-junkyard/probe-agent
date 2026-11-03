---
{
  "id": "IK-0026",
  "title": "draft生成が行番号なしの本文から1-basedの行範囲を要求し、1件の範囲超過でdraft全体を破棄する",
  "status": "open",
  "recorded_at": "2026-11-03",
  "observed_at": "2026-11-03",
  "target_revision": "2026-11-03 時点のローカル checkout（Compose クイックスタート構成、PROBE_EXECUTION_BACKEND=inprocess）",
  "resolved_at": null,
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "対象リポジトリからsystem profileとfeatureのdraftを根拠付きで生成する",
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
    "cause_status": "hypothesis",
    "review": "candidate",
    "reviewed_by": null,
    "reviewed_at": null,
    "basis": "仮説: 接続（仕組みはコードで確認済み）: _build_file_contextは本文を行番号なしで連結する一方、プロンプトはevidenceに1-basedのstart_line/end_lineを要求し、_parse_evidenceはend_line > line_counts[path]を1件でも見つけるとDraftValidationErrorでdraft全体を破棄する。行番号を算出する情報が生成側に渡らず（information）、生成側の出力と検証側の受入契約が両立しない（contract）。処理: 検証の条件式自体は意図どおりでlogicの誤りではないためnone（中確信）。構造: all-or-nothing破棄は設計方針（heuristic fallbackなし）であり、責務配置の誤りかは未判断のためnone（中確信）。統制: 失敗時の201応答・不正値の未記録は観測性の問題で、順序・予算・完了条件の問題とは判断せずnone（中確信）。未確認: LLMが実際に書いた行番号の値（記録されない）、他のreasoningモデルでの再現性。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "生成器に参照座標の手がかりを渡さずに座標を要求し、1件の逸脱で生成結果全体を破棄する"
  },
  "pattern": "unanchored-reference-strict-validation",
  "discovery": {
    "perspective": [
      "reproduction",
      "trace_walk"
    ],
    "note": "drafts生成を対象範囲を変えて3回再現し、error_detailsのpathと_build_file_context・プロンプト・_parse_evidence・line_counts算出を突き合わせた。"
  },
  "resolution": {
    "perspective": [
      "pending"
    ],
    "note": "未実行の候補: (a)行番号付き提示 (b)draft全体を捨てない検証 (c)検証失敗をLLMへ返して1回修正 (d)失敗runを201以外で返す (e)不正範囲の値をerror_detailsへ残す (f)上限で落としたファイルをメタデータに出す。採否はprobe-agent側の判断。",
    "landed_in": [],
    "verification": []
  },
  "related": [
    "IK-0024",
    "IK-0025"
  ],
  "view_of": [],
  "history": []
}
---

## 課題

症状: `POST /repository/drafts/generate`（run_type=repository_drafts、provider=openai、model=gpt-6-luna、decision_method=reasoning_llm）が3回連続で `status=failed`、`error_details="Failed to parse LLM response: evidence line range exceeds snapshot content: <path>"` となった。pathは試行ごとに異なる（`sims/p01-tov/tov/units.py`（61行）、`docs/10-personas/P-01-m1-neutron-star-first-sim.md`、`sims/p01-tov/tests/test_tov.py`）。対象を37 filesから16 files（includeを `sims/**` に絞る）にしても失敗した。

原因（仕組みはコードで確認済み）: `apps/control-server/app/draft_generator.py` の `_build_file_context` は各ファイル本文を `### File: <path>` と `<repository_file>…</repository_file>` で**行番号を付けずに**連結する（documentation→source→test→configurationの順、合計200,000字を超えるファイルは黙ってスキップ）。一方 `_SYSTEM_PROMPT` / `_DRAFT_PROMPT_TEMPLATE` はevidenceに1-basedの `start_line` / `end_line` を要求し、`_parse_evidence` は `end_line > line_counts[path]` を1件でも見つけると `DraftValidationError` を投げ、system_profile_draftとfeature_draftsの全体が破棄される（設計方針どおりheuristic fallbackなし）。LLMは行を自分で数える必要があり、1件の数え違いで全体が失われる。

周辺: 失敗した実行でもHTTPは201 Created。error_detailsにもcontrol-serverログにも、LLMが書いた実際の `start_line` / `end_line` は残らず、どれだけ外れたかを確認できない。

影響: この経路が通らないとFeature-to-Code link（`POST /repository/code-links/generate` は「先にdraftsを生成せよ」で400）、Probe Plan、Experimentに進めない。

対象版: 2026-11-03時点のローカルcheckout。起票元: 外部の検証サンドボックス sim-sandbox の実施記録 `sim-sandbox/docs/02-experiments/E-0002-p01-tov-probe-agent.md` の「drafts生成」節（probe-agentリポジトリ外のためsourcesには載せず、drafts生成の経緯を記す本リポジトリの文書を載せる。根拠コードは `apps/control-server/app/draft_generator.py`）。未確認: 他のreasoningモデル（例: gpt-5.4）で通るか（別途試す予定）。

## 発見の観点

drafts生成を対象範囲を変えて再現し、error_detailsが指すpathの実際の行数と、本文の提示形式（行番号なし）・プロンプトの要求（1-based行範囲）・検証（範囲超過で全体破棄）・`line_counts` の算出（`splitlines()` の件数、最小1）を突き合わせた。

## 解決の観点

未実行（pending）。候補:

- (a) プロンプトで各行に行番号を付けて提示する（LLMが数えなくてよい）。
- (b) 超過したevidenceは切り詰める・その項目だけ落とすなど、draft全体を捨てない検証にする。
- (c) 検証失敗の内容をLLMに返して1回修正させる。
- (d) 失敗したintelligence_runは201でなく失敗が分かる応答にする。
- (e) 不正だった範囲の値をerror_detailsに残す。
- (f) 200,000字の上限で落としたファイルを応答のメタデータに出す。

どれが設計（fallbackなしの方針を含む）に合うかはprobe-agent側の判断。

## 一般化

生成器に座標（行番号・オフセット等）を返させるなら、座標を数えずに読める形で入力を渡す契約を同時に定める必要がある。渡さないまま厳格に検証し1件の逸脱で全体を捨てると、入力が大きくなるほど成功率が下がり、失敗の値も残らないため原因が追えない。
