---
{
  "id": "IK-0038",
  "title": "drafts生成でLLMがfeature_idを書き、前回runとの名前照合（IK-0032）でしか引き継がれないため、新しいidの発行と同一run内の重複を系が止められない",
  "status": "resolved",
  "recorded_at": "2026-11-07",
  "observed_at": "2026-11-07",
  "target_revision": "ブランチ sim-sandbox/ik-0024-0026 の 0de947a",
  "resolved_at": "2026-11-07",
  "sources": [
    "docs/90-history/project-intelligence.md"
  ],
  "feature_context": {
    "realizing": "リポジトリからFeature Mapを抽出し、Experiment・Probe Plan・code-linkの参照先として同じFeatureを同じキーで保つ",
    "layers": [
      "control_server"
    ]
  },
  "classification": {
    "axes": {
      "processing": [
        "input_handling"
      ],
      "structure": [
        "responsibility",
        "representation"
      ],
      "connection": [
        "version"
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
    "basis": "確定: draft_generator.pyの出力スキーマは各Featureに feature_id（kebab文字列）を要求し、IK-0032のresolve_feature_idsは前回（最新completed）runのid・nameとslug照合して置き換えるだけで、照合できなければLLMが書いたidをそのまま新しいキーにする。照合先は最新runだけなので、1回でもFeatureが抜けたrunを挟むと以前のidは候補から消える。同一run内で2つのFeatureが同じidを書いても検査しない（feature_draftsに一意制約なし）。構造: キーの発行がLLMの責務（responsibility）、run・snapshotに依存しないFeatureの同一性の表現が無い（representation）。接続: 照合の相手が最新runだけ（version）。統制: 付番の担当がLLM（assignment）。処理: 同一run内の重複を扱わない（input_handling）。",
    "proposals": []
  },
  "generalization": {
    "level": "general",
    "general_form": "変わってはならない識別子を生成器に作らせ、前回の生成結果との照合で引き継ごうとするので、照合から漏れた分は生成器の作ったidが正本になる"
  },
  "pattern": "llm-assigned-stable-identifier",
  "discovery": {
    "perspective": [
      "inventory",
      "invariant_audit"
    ],
    "note": "利用者の規則（2026-11-07）に照らした棚卸しで、IK-0032の解決（写させる → slugで前回へ解決）がなお「LLMが新しいidを作る」余地を残すこと、照合先が最新runだけであること、run内の一意性を検査しないことを、draft_generator.py と routes/project_intelligence.py の保存処理で確かめた。"
  },
  "resolution": {
    "perspective": [
      "responsibility_move",
      "first_class_state"
    ],
    "note": "Systemごとにrunから独立したFeature台帳 feature_registry（system_id, feature_key, display_name, summary, origin, created_at, updated_at, last_run_id, retired_at、UNIQUE(system_id, feature_key)）を置く。プロンプトは生きている台帳をハンドル F1..Fn（handle | name | summary | evidence）で見せ、LLMは各Featureに same_as: \"F3\" か new: true だけを返す（feature_idは書かない）。系がハンドルを feature_key に戻し、new には最初の表示名のkebab（衝突時だけ sha256(表示名) 先頭4桁の接尾辞）でキーを発行する。重複same_as・不明ハンドルはその項目だけ落として記録し、全項目が落ちたらrunを失敗（422）にする（K-0001）。new の名前のslugが既存（登録・退役・過去のrun）のキーと一致したら slug_matches に記録するだけ（安全網）。初回利用時と起動時の移行で、最新completed runのidを台帳に移す（origin=migrated）。prompt v3 / schema v2。",
    "landed_in": [
      "apps/control-server/app/feature_registry.py",
      "apps/control-server/app/draft_generator.py",
      "apps/control-server/app/routes/project_intelligence.py",
      "apps/control-server/app/models.py",
      "apps/control-server/app/db.py"
    ],
    "verification": [
      "apps/control-server/tests/test_feature_id_stability.py",
      "apps/control-server/tests/test_repository_intelligence.py"
    ]
  },
  "related": [
    "IK-0032",
    "IK-0037"
  ],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-07",
      "field": "status",
      "from": "open",
      "to": "resolved",
      "reason": "起票と同時に記帳。利用者の規則（2026-11-07）の棚卸しの2件目として着地、関連テストpass。IK-0032の方式（前回idを写させてslugで解決）を置き換える。実機（docker再ビルド後）での再生成の確認は未実施。"
    }
  ]
}
---

## 課題

症状（棚卸しで確認）: `/repository/drafts/generate` で、LLMが各Featureの `feature_id` を書く。IK-0032以降は前回runのFeature一覧を写せる形で渡し、照合できないidは名前のslugで前回へ寄せるが、(a) 照合できなければLLMが書いたidがそのまま新しいキーになる、(b) 照合先は最新のcompleted runだけで、あるFeatureが1回抜けると以前のidは二度と候補に出ない、(c) 同じrunで2つのFeatureが同じidを書いても通る。feature_idはExperiment・Probe Plan・code-linkの参照先なので、どれもExperimentとFeatureの対応が切れる・混ざる原因になる。

原因（確定）: キーの発行がLLMの出力に残っており、run・snapshotから独立したFeatureの同一性（台帳）が系に無い。

対象版: ブランチ `sim-sandbox/ik-0024-0026` の `0de947a`。起票元: sim-sandbox `docs/30-reviews/2026-11-07-llm-id-audit.md`（本リポジトリ外）。前史: IK-0032（同じ症状に対する、前回idを写させる方式。履歴として残す）。

## 発見の観点

利用者の規則（下の一般化）を不変条件とし、IK-0032の解決がそれを満たすかを確かめた。写させる方式は「LLMに新しいidを作らせない」ことを保証しない（照合漏れはLLMのidが正本になる）。

## 解決の観点

**解決（2026-11-07）。** IK-0032の「写させて照合する」をやめ、系の台帳とハンドルに置き換えた。

- 台帳 `feature_registry`（System単位、runから独立）: `feature_key`（= 従来の feature_id。Experiment・Probe Plan・code-linkはこの文字列を使い続ける）、`display_name`（キーを発行したときの名前、以後変えない）、`summary`（最新runの要約、プロンプト用）、`origin`（minted / migrated）、`last_run_id`、`retired_at`（NULL = 生きている）。`UNIQUE(system_id, feature_key)`。
- プロンプト（v3 / schema v2）: 出力スキーマから `feature_id` を外し、`same_as`（ハンドル）と `new` を置いた。「## Registered features」に生きている台帳を `- F1 | name: … | summary: … | evidence: a, b` で並べる（ハンドルは台帳の登録順、プロンプトごと）。「feature idを書かない、系が付番・保持する」と明記。
- 解決（K-0001: 正規化 → 解決 → その項目だけ落とす）: `same_as` は `f3` / `[F3]` / `3`、またはキーそのもの（slug一致を含む）を受ける。不明ハンドルと、同じハンドルを2つ目に使った項目はその項目だけ落とし `dropped`（理由 unknown_handle / duplicate_same_as）に記録。全項目が落ちたらrunを失敗（422、IK-0034）。`same_as` も `new` も無い項目は new 扱い（`normalized` に記録）。旧契約の `feature_id` が生きているキーと一致する場合だけ参照として扱い、それ以外のLLM製idはキーに使わない。
- 発行: 最初の表示名のkebab（48字まで）。台帳（登録・退役）と過去のrunで使われた全キーと衝突する場合だけ `-` + sha256(表示名) 先頭4桁（なお衝突すれば6, 8…桁）。名前が非ASCIIでslugが空なら `feature-` + 4桁。同じ入力からは同じキー。
- 安全網（主ではない）: `new` の名前のslugが既存キーと一致したら `slug_matches`（kind: registered / retired / historical）に記録する。LLMの「new」は覆さない。
- 記録: `feature_id_resolution` を残し、`handle_matched`・`minted`・`slug_matches`・`dropped`・`normalized`・`not_referenced` を追加。IK-0032の `reused` / `new` / `previous_run_id` は同じ意味で残し、`resolved` は常に空。
- 移行: 起動時（`db._migrate_feature_registry`）とdrafts生成の初回利用時に、台帳が空のSystemへ最新completed runのidをそのまま登録（origin=migrated）。既存のExperiment等の参照は変わらない。

検証: `tests/test_feature_id_stability.py` を書き直し16件pass（プロンプトにfeature_idが無くハンドルを要求、ハンドルからキーへ・改名してもキー維持・登録順、不明・重複ハンドルはその項目だけ落とす、全滅で422、衝突接尾辞と slug_matches、発行の決定論と非ASCII、旧契約のid、最新runからの移行と冪等・初回利用時の移行、ハンドル正規化8通り）。prompt/schema版を見ていた `test_repository_intelligence.py` の2件を更新。drafts / code-link / probe plan / product系の既存テスト計352件pass。下流（experiments.feature_id、probe_plans、feature_code_links）は文字列キーを読むだけで変更なし（grepで確認）。dashboardは feature_id_resolution を表示しておらず変更不要。

見送り・残り: 退役（`retired_at`）を設定する口は作っていない。runで言及されなかった生きたキーは `not_referenced` に出すだけで自動退役しない（1回抜けたことでキーを失わないため）。台帳の閲覧・統合（slug_matches の人による統合）・退役のAPI/画面は別件。実機での再生成の安定度は未計測。

## 一般化

利用者の規則（2026-11-07）: LLM APIで生成するとき、変わってはならない識別子・個別名はLLMに再生成させず、系が付番・保持する。LLMはそれに紐づく内容だけを返す（または new と示す）。例外は理由を明記する。前回の識別子を写させて照合する方式は、照合漏れでLLMの識別子が正本になる。系の台帳を正本にし、LLMにはプロンプト内だけのハンドルで参照させ、ハンドル → キーの対応と発行を系が持つ。
