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
    "note": "部分着地（statusはopenのまま）: Compose既定構成でbwrapが動かない点は docker-compose.yml の control-server に cap_add [SYS_ADMIN, NET_ADMIN] と security_opt [seccomp=unconfined] を追加（コミット8f30c3c）して解消し、2026-11-03の現在の実行で確認。未実施: /system-diagnostics に実行バックエンドの自己試験項目を足す案（存在を可用性と見なす型の本体）。",
    "landed_in": [
      "docker-compose.yml",
      "apps/control-server/app/execution_backend.py"
    ],
    "verification": []
  },
  "related": [],
  "view_of": [],
  "history": [
    {
      "date": "2026-11-03",
      "field": "resolution.note",
      "from": "未実行の候補: 起動時または診断でbwrap自己試験（bwrap --unshare-net true相当）を行い診断項目に出す／Composeクイックスタートの説明にinprocess+Dockerではbwrapが動かずworker backendか権限付与が要ると明記する。採否はprobe-agent側の判断。",
      "to": "部分着地（statusはopenのまま）: Compose既定構成でbwrapが動かない点は docker-compose.yml の control-server に cap_add [SYS_ADMIN, NET_ADMIN] と security_opt [seccomp=unconfined] を追加（コミット8f30c3c）して解消し、2026-11-03の現在の実行で確認。未実施: /system-diagnostics に実行バックエンドの自己試験項目を足す案（存在を可用性と見なす型の本体）。",
      "reason": "ブランチ sim-sandbox/ik-0024-0026 のコミット8f30c3c（Compose権限付与）とf18116f（bwrap --dev /dev）で実行可否は解消。診断の自己試験は未実施のためopen・pendingを維持（taxonomyのstatus語彙に部分解決はなく、resolvedは対象範囲の解消証拠が要る）。"
    },
    {
      "date": "2026-11-03",
      "field": "resolution.landed_in",
      "from": [],
      "to": [
        "docker-compose.yml",
        "apps/control-server/app/execution_backend.py"
      ],
      "reason": "部分着地の記録。"
    }
  ]
}
---

## 課題

症状: `docker compose up --build` のクイックスタート構成（`PROBE_EXECUTION_BACKEND=inprocess`、control-serverコンテナはroot、`bwrap`・`unshare`は/usr/binに存在）で、`apps/control-server/app/execution_backend.py` の `InProcessExecutionBackend` と同じ引数（`bwrap --die-with-parent --new-session --unshare-net --ro-bind / / --tmpfs /tmp --tmpfs /data --tmpfs /root --tmpfs /repositories --bind <worktree> /workspace --chdir /workspace /bin/sh -c ...`）をコンテナ内で実行すると `bwrap: Creating new namespace failed: Operation not permitted` で失敗する。

原因（仮説）: Docker既定のseccomp/capabilityでは非特権ユーザー名前空間を作れない。`execution_backend.py` はbwrapもsandbox-execも無い場合に "no supported sandbox backend is available" でfail closedするが、bwrapが「存在するが動かない」場合は検出しない。`GET /system-diagnostics` の検査項目（repository_roots, database_storage, llm_base_config, intelligence_llm_config, repository_config, snapshot_status, auth_scope, pipeline_*, system_purpose, system_capabilities, llm_last_run）にも実行バックエンドの可否がなく、test/smoke/experimentを実際に走らせるまで分からない。

対象版: 2026-11-03時点のローカルcheckout（Composeクイックスタート構成）。起票元: 外部の検証サンドボックス sim-sandbox の実施記録 `sim-sandbox/docs/02-experiments/E-0002-p01-tov-probe-agent.md`（probe-agent リポジトリ外のためsourcesには載せず、関連する本リポジトリの運用・仕様文書を載せる）。未確認: seccomp:unconfined等の付与で解消するか、worker backend構成での再現有無。

## 発見の観点

製品と同じbwrap引数をコンテナ内で再現実行し、診断の検査項目を棚卸しして実行バックエンドの項目がないことを照合した。

## 解決の観点

**部分着地（2026-11-03、対象版: ブランチ `sim-sandbox/ik-0024-0026`、`ura` から分岐）。status は open のまま。**

着地: `docker-compose.yml` の control-server に `cap_add: [SYS_ADMIN, NET_ADMIN]` と `security_opt: [seccomp=unconfined]` を追加（コミット `8f30c3c`）。bwrap 引数への `--dev /dev` 追加は IK-0026 のコミット `f18116f` に含まれる（`apps/control-server/app/execution_backend.py`）。

検証（すべて 2026-11-03 の現在の実行）:

- 権限試験（イメージ `probe-agent/control-server:latest` の使い捨てコンテナ）: 既定 → `Creating new namespace failed`。SYS_ADMIN のみ → `loopback: Failed RTM_NEWADDR`。SYS_ADMIN+NET_ADMIN → `pivot_root: Operation not permitted`。SYS_ADMIN+NET_ADMIN+seccomp=unconfined → 完走し、`wget http://example.com` は失敗（ネットワーク遮断を維持）。`--privileged` は不要。これにより原因仮説（Docker 既定の seccomp/capability）を裏づけた。
- 実機: 再ビルドした probe-control-server 内で、`execution_backend.py` と同じ引数（`--dev /dev` 追加後）の bwrap が `bwrap-ok` を出力し、ネットワークは遮断。

未実施（open の理由）: 候補 (1) の `/system-diagnostics` への実行バックエンド自己試験項目。これが無い限り「存在するが動かない」構成を診断で検知できない。worker backend 構成での再現有無も未確認。

起票時の候補（参考、2026-11-03 起票時点）:候補: (1) 起動時または診断でbwrapの自己試験（`bwrap --unshare-net true` 相当）を行い、結果を診断項目に出す。(2) Composeクイックスタートの説明に「inprocess + Dockerではbwrapが動かない。worker backendか、コンテナへの権限付与が要る」と明記する。なお `PROBE_UNSAFE_ALLOW_HOST_EXECUTION`（隔離なしでコンテナ内で直接実行）という逃げ道が存在するが、composeのenvironmentでは渡されておらず、採否は利用者判断。

## 一般化

隔離ツールのバイナリ存在を可用性と見なすと、権限の異なる実行環境（コンテナ既定のseccomp等）で、診断が通っても実行段で初めて失敗する。存在検査に加えて実環境での起動試験を準備完了の条件に含めるかを問う。
