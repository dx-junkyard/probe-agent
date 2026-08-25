# Issue #412 / #415 最終修正・クローズ確認報告

- 対象: [#412](https://github.com/dx-junkyard/probe-agent/issues/412), [#415](https://github.com/dx-junkyard/probe-agent/issues/415)
- 確認日: 2026-08-25
- 対象branch: `codex/issue-415-final-fixes`
- 判定: **クローズ可能**

## 結論

再レビューで残っていた、承認済みFlow proposalと実際のcandidate executionの
拘束不足、事後の実行参照付け替え、DB並行実行時の二重拘束、draft provenance、
runtime観測の自己申告性、既存LLM入口のmode gate不足を修正した。

今回確認した範囲では、#412 / #415 の完了条件を妨げる既知の問題は残っていない。

## 修正確認

### 1. 実行入口でproposal承認を必須化

- governedなExperiment、Replay、Replay variant、Candidate Studio replay/promote、
  live Shadowは、実行前に`flow_experiment_proposal_id`を照合する。
- proposalなし、未承認・withdrawn・期限切れ、対象Node不一致を有限codeで拒否する。
- mode capabilityとproposal承認を独立した必要条件として再検査する。
- multi-Node proposalでは全target Nodeを実行直前に再検査する。

### 2. candidate / snapshot / isolationを実行内容へ拘束

- Replay・Experiment・Candidate Studioのcandidateはcallerの名称ではなく
  `patch_sha256:<hex>`で照合する。
- runtime/static Flowの別を問わず、candidate executionではproposalのcaptured
  snapshotを必須とし、実行snapshotと完全一致で照合する。
- live Shadowのcandidateはrequestの自由文ではなく、Control Server上の
  `candidate_version`または`replay_variant`正本IDから解決する。
- 承認後にside-effect分類またはisolation条件が変化した場合はfail closedにする。

### 3. 事後付け替えを禁止

- canonical execution行に、実行入口で照合したproposal ID、candidate ref、snapshot
  を固定する。
- 事後のexecution APIは、実行時authorizationがない行、別proposal、別candidate、
  別snapshotを拒否する。
- 実行後のFlow link変更や、同一Nodeを共有する別Flowの実行では代用できない。
- actual execution作成時に`flow_experiment_execution_ref`とappend-only
  `execution_recorded` eventを自動記録する。

### 4. DB一意性と並行実行

- `(system_id, execution_kind, execution_ref)`をDBのUNIQUE indexで保証する。
- 1 canonical executionを2 proposalへ同時登録するmulti-worker raceをDB制約で拒否する。
- 1 `intelligence_run`を複数proposalのprovenanceへ使うこともpartial UNIQUE indexで拒否する。
- 既存DBに重複audit行がある場合は黙って削除せず、migration時に停止して通知する。
- lifecycle/mode/link再検査、canonical pin、ledger insertを同一の
  `BEGIN IMMEDIATE` transactionで行い、check-to-write raceを防止する。
- 一度実行したgoverned Experiment行は再実行不可とし、別attemptによる
  proposal pin・結果・ledgerの上書きを防止する。

### 5. draft provenance / evidence

- draft runをFlow subject kind/ref、captured snapshot、target Node集合へ拘束する。
- subject・target・evidence envelopeのdigestをproposal作成時に再計算し、改変を拒否する。
- evidence refはFlow projectionが生成したallowlistに対してdraft時とproposal作成時に検証する。

### 6. runtime観測のattestation

- canonical executionを作成した実行gateが、適用したmodeと正本run refを自動記録する。
- この観測は`run_ref_state: corroborated`として、HTTP自己申告の
  `uncorroborated`と区別する。
- `execution_gate:`予約prefixは公開観測APIから指定できず、attestationの偽装を拒否する。

### 7. 既存LLM入口のfail-closed化

- Candidate Studio generation、Replay variant draft、Replay regression scaffoldを
  `llm_experiment_proposal` gateへ接続した。
- governed Nodeの`fixed` / `observe`では、LLM client構築・資格情報読取より前に拒否する。
- `propose` / `shadow`のみproposal用LLM capabilityへ到達できる。

## 検証結果

### Backend

最終結果は以下の関連suiteをDocker内で実行して確認した。

- `test_execution_mode.py`
- `test_execution_target.py`
- `test_flow_explanation.py`
- `test_flow_orchestration.py`
- `test_interview_type_parity.py`
- `test_replay_variants.py`
- `test_candidate_studio.py`
- `test_experiments.py`
- `test_schemas.py`

結果: **337 passed / 133.93秒**

### Dashboard

- Flow Agents対象: **2 files / 56 tests passed**
- ESLint: **0 errors / 48 warnings**
- production build: **成功**（1,949 modules）

48 warningsは既存のFast Refreshルールによるもので、今回の変更によるerrorはない。
build時の`/env.js`非module警告と500 kB超chunk警告も既存の非失敗警告である。

### 静的確認

- Python対象ファイルの`py_compile`: 成功
- `git diff --check`: 成功
- API / TypeScript有限型parity: Backend関連suiteに含めて確認

## 未実施（クローズ阻害ではない範囲）

- 実provider資格情報を使う外部LLM接続
- live docker-compose環境でのブラウザE2E
- 負荷試験・長時間実行

これらは外部環境を必要とする運用検証であり、今回修正したauthorization、audit、
provenance、型契約は自動テストとDB制約で検証している。
