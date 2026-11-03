---
{
  "id": "IK-0024",
  "title": "隔離実行ツールの存在を動作可能と見なし、Compose既定構成で名前空間を作れない失敗を診断が検知しない",
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
    "realizing": "対象リポジトリのtest・smoke・experimentを隔離して実行する",
    "layers": [
      "control_server",
      "deployment"
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
        "condition"
      ],
      "governance": [
        "completion"
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
    "basis": "仮説: 処理: bwrap引数の組立て自体は意図どおりで単一処理の誤りではない。構造: バックエンド選択の責務配置は既存のままで説明できるためnone（medium）。接続: 実行に必要な前提（非特権ユーザー名前空間を作れる権限）が、Compose既定のcontrol-serverコンテナへ届いていない／確認されない。governance: execution_backend.pyはbwrap不在ならfail closedするが『存在するが動かない』を検出せず、GET /system-diagnosticsの検査項目にも実行バックエンドの可否がないため、診断が通っても実行可能と言えない（準備完了の条件の不足）。Operation not permittedの原因がDocker既定のseccomp/capabilityであることは、seccomp:unconfined等を付けた再試験で確定する必要がある。",
    "proposals": []
  },
  "generalization": {
    "level": "repo_pattern",
    "general_form": "隔離実行ツールの存在を可用性と見なし、実環境での起動可否を事前に検査しない"
  },
  "pattern": "sandbox-presence-as-capability",
  "discovery": {
    "perspective": [
      "reproduction",
      "inventory"
    ],
    "note": "InProcessExecutionBackendと同じbwrap引数をcontrol-serverコンテナ内で実行して失敗を再現し、system-diagnosticsの検査項目を棚卸しして実行バックエンドの項目がないことを確認した。"
  },
  "resolution": {
    "perspective": [
      "pending"
    ],
    "note": "未実行の候補: 起動時または診断でbwrap自己試験（bwrap --unshare-net true相当）を行い診断項目に出す／Composeクイックスタートの説明にinprocess+Dockerではbwrapが動かずworker backendか権限付与が要ると明記する。採否はprobe-agent側の判断。",
    "landed_in": [],
    "verification": []
  },
  "related": [],
  "view_of": [],
  "history": []
}
---

## 課題

症状: `docker compose up --build` のクイックスタート構成（`PROBE_EXECUTION_BACKEND=inprocess`、control-serverコンテナはroot、`bwrap`・`unshare`は/usr/binに存在）で、`apps/control-server/app/execution_backend.py` の `InProcessExecutionBackend` と同じ引数（`bwrap --die-with-parent --new-session --unshare-net --ro-bind / / --tmpfs /tmp --tmpfs /data --tmpfs /root --tmpfs /repositories --bind <worktree> /workspace --chdir /workspace /bin/sh -c ...`）をコンテナ内で実行すると `bwrap: Creating new namespace failed: Operation not permitted` で失敗する。

原因（仮説）: Docker既定のseccomp/capabilityでは非特権ユーザー名前空間を作れない。`execution_backend.py` はbwrapもsandbox-execも無い場合に "no supported sandbox backend is available" でfail closedするが、bwrapが「存在するが動かない」場合は検出しない。`GET /system-diagnostics` の検査項目（repository_roots, database_storage, llm_base_config, intelligence_llm_config, repository_config, snapshot_status, auth_scope, pipeline_*, system_purpose, system_capabilities, llm_last_run）にも実行バックエンドの可否がなく、test/smoke/experimentを実際に走らせるまで分からない。

対象版: 2026-11-03時点のローカルcheckout（Composeクイックスタート構成）。起票元: 外部の検証サンドボックス sim-sandbox の実施記録 `sim-sandbox/docs/02-experiments/E-0002-p01-tov-probe-agent.md`（probe-agent リポジトリ外のためsourcesには載せず、関連する本リポジトリの運用・仕様文書を載せる）。未確認: seccomp:unconfined等の付与で解消するか、worker backend構成での再現有無。

## 発見の観点

製品と同じbwrap引数をコンテナ内で再現実行し、診断の検査項目を棚卸しして実行バックエンドの項目がないことを照合した。

## 解決の観点

未実行（pending）。候補: (1) 起動時または診断でbwrapの自己試験（`bwrap --unshare-net true` 相当）を行い、結果を診断項目に出す。(2) Composeクイックスタートの説明に「inprocess + Dockerではbwrapが動かない。worker backendか、コンテナへの権限付与が要る」と明記する。なお `PROBE_UNSAFE_ALLOW_HOST_EXECUTION`（隔離なしでコンテナ内で直接実行）という逃げ道が存在するが、composeのenvironmentでは渡されておらず、採否は利用者判断。

## 一般化

隔離ツールのバイナリ存在を可用性と見なすと、権限の異なる実行環境（コンテナ既定のseccomp等）で、診断が通っても実行段で初めて失敗する。存在検査に加えて実環境での起動試験を準備完了の条件に含めるかを問う。
