# 本番 HTTPS 公開 (Caddy)

issue #217 で追加した、Caddy を唯一の外部公開入口とする本番構成の手順。
`docker-compose.yml`（ローカル開発用、control-server / dashboard をホストへ
直接公開）とは別の、独立した `docker-compose.prod.yml` を使う。

```
internet --80/443--> Caddy --8501--> Dashboard (nginx) --/api/*--> Control Server
                       (Automatic HTTPS)         (/ 以外は static assets)
```

- Dashboard: `https://<PUBLIC_HOST>/`
- Control API: `https://<PUBLIC_HOST>/api/`（`/api/*` prefix の除去は既存の
  `apps/dashboard/nginx.conf` が行う。Caddyfile は追加のパス書き換えをしない）
- SDK: `PROBE_SERVER_URL=https://<PUBLIC_HOST>/api`
- TLS 証明書は Caddy の Automatic HTTPS が ACME で自動取得・更新する

## 前提条件

- 公開する FQDN の DNS A レコードを、デプロイ先 VM の **静的外部 IPv4** へ
  向けておく。証明書取得（ACME HTTP-01 / TLS-ALPN-01）には、Caddy 起動前に
  80/443 への到達性が必要。DNS 反映前に起動すると証明書取得に失敗する。
- GCP 等のクラウドファイアウォールでは **80/443 のみ** インターネットに許可
  する。`8000`（Control Server）と `8501`（Dashboard）はインターネットに
  公開しない（`docker-compose.prod.yml` は両サービスに `ports:` を持たせず
  `expose:` のみにしてこれを構成レベルでも強制している）。SSH は管理元 IP
  への制限、または Identity-Aware Proxy (IAP) 経由に限定する。
- `.env` は Git 管理しない（`.gitignore` 済み）。加えてファイル権限を
  制限する（例: `chmod 600 .env`）。secrets やパスワードを含むため、
  デプロイ先 VM 上でも読み取り権限を絞る。

## 初回デプロイ手順

1. リポジトリを VM へ配置し、`.env.example` を `.env` としてコピーして
   値を設定する。最低限:

   ```env
   PUBLIC_HOST=probe.example.com
   PROBE_CLIENT_SERVER_URL=https://probe.example.com/api
   CONTROL_ENV=production
   CONTROL_ADMIN_USERNAME=admin
   CONTROL_ADMIN_PASSWORD=<強いパスワード（16文字以上）>
   CONTROL_API_KEYS=
   CONTROL_REQUIRE_AUTH=true
   CONTROL_TRACE_RATE_LIMIT_PER_SECOND=1000
   CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE=600
   CONTROL_LLM_DAILY_EXECUTION_LIMIT=1000
   CONTROL_TRACE_MAX_ROWS_PER_SYSTEM=1000000
   CONTROL_TRACE_MAX_BYTES_PER_SYSTEM=1073741824
   PROBE_REPOSITORY_HOST_ROOT=/absolute/path/to/repositories
   ```

   `change-me` や `.env.example` に書かれた開発用の初期値
   (`CONTROL_ADMIN_PASSWORD=change-me` 相当、`dev-secret-key` など) を
   そのまま使わない。`docker-compose.prod.yml` は `CONTROL_ENV=production`
   を固定で設定しているため、下記の起動時チェック (Issue #225) が必ず
   働く。

2. 起動する。

   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

   `control-server` は起動時に `CONTROL_ENV=production`（Issue #225）を
   検証し、以下のいずれかに該当すると明確なエラーメッセージを出して
   **起動そのものが失敗する**（認証なし、または sample secret のまま
   インターネットへ公開されることを防ぐフェイルセーフ）。

   - `CONTROL_REQUIRE_AUTH` が明示的に `false`（または `0`/`no`/`off`）に
     設定されている（production は認証必須と矛盾するため）
   - `CONTROL_API_KEYS` が空でない（production では legacy shared key は
     禁止。system 単位の API token を使う）
   - `CONTROL_ADMIN_USERNAME` と `CONTROL_ADMIN_PASSWORD` を設定していて、
     そのパスワードが 16 文字未満、`change-me` / `dev-secret-key` /
     `password` / `admin` / `example`（大文字小文字を区別しない）などの
     サンプル値と一致する、またはユーザー名と一致する -- 同名の admin が
     既に存在していても失敗する（環境変数に sample secret が残っている
     こと自体が問題のため）
   - 上記を満たして起動した後も、アクティブな admin ユーザーが 1 人も
     存在しない（`CONTROL_ADMIN_USERNAME`/`CONTROL_ADMIN_PASSWORD` を
     設定していない、かつ他の手段でも admin を作っていない場合など）
   - `PROBE_EXECUTION_BACKEND` が `worker` でない、job spool/workspace共有root
     が存在しない・書き込めない、またはvalidation/replay/experimentの
     worktree baseが共有workspace root外を指している
   - 上記5つの `CONTROL_*_LIMIT` / `CONTROL_TRACE_MAX_*` resource limit が
     未設定、0以下、または整数でない

   起動が失敗した場合は該当する環境変数を修正してから再実行する。

   `execution-worker` は `network_mode: none`、read-only root filesystem、
   capability全drop、no-new-privileges、CPU/memory/pids上限付きで起動する。
   mountはjob spoolとexecution workspaceだけで、`/data`、`/repositories`、
   GitHub App secret、LLM/admin環境変数、Docker socketは渡さない。手動確認:

   workerはnon-root UID 65534で動作する。Docker既定seccompはcapabilityなしの
   `unshare(2)` も遮断するためworkerだけ`seccomp:unconfined`とし、bwrapが
   unprivileged user/mount/PID/network namespaceを作る。これはsyscall filterの
   弱化だが、外側のnon-root・capability全drop・no-new-privileges・read-only・
   no-network・secretなしmountと、内側bwrapのfilesystem境界を同時に維持する。
   bwrap/user namespaceを利用できないhostでは対象commandを直接実行せず、
   sandbox errorとしてfail closedする。

   ```bash
   docker compose -f docker-compose.prod.yml config | sed -n '/execution-worker:/,/^[^ ]/p'
   docker inspect probe-execution-worker --format '{{json .HostConfig}}'
   ```

## サプライチェーン更新手順

### Base image とローカル build 出力

外部レジストリから取得する base image は `tag@sha256:<OCI image index digest>`
で固定する。更新時は release note と upstream Dockerfile を確認し、対象 platform
を含む index であることを registry に問い合わせてから digest を置換する。

```bash
docker buildx imagetools inspect python:3.11-slim
docker buildx imagetools inspect node:22-slim
docker buildx imagetools inspect nginx:alpine
docker buildx imagetools inspect caddy:2-alpine
```

tag は人が意図した release line を確認するために残し、再取得内容は digest で
固定する。digest 更新は dependency lock 更新と同様に独立した review 対象とし、
CI の supply-chain job、両 image build、テスト、SBOM artifact を確認する。

`probe-agent/control-server:latest` と `probe-agent/dashboard:latest` は Compose が
同じ source tree から build するローカル出力名である。build 前には registry
digest が存在しないため、架空の digest を記述しない。本番で複数 host へ同一
image を配る場合は、CI で一度だけ build して registry へ publish し、registry
が返した digest または immutable release tag を deployment manifest に記録する。

### Python dependency lock

`apps/control-server/requirements.lock` は production、
`requirements-dev.lock` は CI/test 用であり、いずれも Python 3.9 で解決して
Python 3.9 以上の対応範囲を維持する。依存を更新するときだけ、repository root
から次を実行する（通常の image build や CI では再解決しない）。

```bash
python3.9 -m venv /tmp/probe-lock-venv
/tmp/probe-lock-venv/bin/pip install pip-tools==7.5.2
cd apps/control-server
/tmp/probe-lock-venv/bin/pip-compile --upgrade --generate-hashes --allow-unsafe \
  --strip-extras --resolver=backtracking --output-file requirements.lock \
  pyproject.toml requirements-build.in
/tmp/probe-lock-venv/bin/pip-compile --upgrade --generate-hashes --allow-unsafe \
  --strip-extras --resolver=backtracking --extra dev \
  --output-file requirements-dev.lock pyproject.toml requirements-build.in
```

生成結果は diff review し、Python 3.9 と production image の Python 3.11 の両方で
`pip install --require-hashes` が成功することを確認する。Control Server 本体と
同梱 SDK は lock 済み依存を再解決させないため、`--no-build-isolation --no-deps`
で install する。Dashboard は `package-lock.json` を review し、常に `npm ci`
で install する。

### SBOM と残余リスク

CI は build 済みの Control Server / Dashboard image から SPDX JSON SBOM を
生成し artifact に保存する。SBOM は構成把握と脆弱性調査の入力であり、image の
署名、provenance attestation、脆弱性がないことの証明ではない。production へ
publish する場合は、組織の registry policy に従って image signing、attestation、
定期 vulnerability scan を追加する。

Control Server image が導入する `bubblewrap` と `git` は Debian の移動する apt
repository から取得しており、package version と repository snapshot までは固定
していない。base image digest により更新前の filesystem は固定されるが、build
時の apt package は完全再現可能ではない。より高い保証が必要な環境では Debian
snapshot repository と package version を固定するか、検証済み package を内部
repository に保管し、更新・脆弱性対応の責任者と期限を定める。

## GitHub App 公開ワークフローを有効化する場合（任意）

GitHub App 公開ワークフロー（Issue #216）を使う場合は、秘密鍵を
Compose secret としてマウントする必要がある（Issue #224）。GitHub App の
登録手順、秘密鍵のホスト配置、`GITHUB_PUBLISH_ENABLED` の起動時検証、
鍵ローテーションの runbook は
[`docs/github-app-deployment.md`](github-app-deployment.md) を参照。
使わない場合はこの節は無視してよい（`GITHUB_APP_PRIVATE_KEY_HOST_PATH` /
`GITHUB_PUBLISH_ENABLED` とも未設定のままで安全に起動できる）。

## 証明書発行の確認

```bash
docker compose -f docker-compose.prod.yml logs caddy
```

`certificate obtained successfully` 相当のログが出れば取得成功。DNS が
まだ VM の IP を指していない、または 80/443 に到達できない場合は
`solving challenge` 付近でリトライを繰り返す。DNS 伝搬を待ってから
`docker compose -f docker-compose.prod.yml restart caddy` で再試行する。

## HTTPS ヘルスチェック

```bash
curl -sS https://<PUBLIC_HOST>/api/health
# => {"ok": true}
```

## Dashboard ログイン確認 / SDK からの trace 送信確認

1. ブラウザで `https://<PUBLIC_HOST>/` を開き、Dashboard が表示されることを
   確認する（現状 Dashboard にブラウザ上のログイン画面はない。admin
   ユーザーでの API token 発行手順は README の
   [認証と Dashboard のログイン方式](../README.md#認証と-dashboard-のログイン方式)
   を参照）。
2. SDK から trace を送る場合は `PROBE_SERVER_URL` を公開 URL に向ける。

   ```bash
   PROBE_SERVER_URL=https://<PUBLIC_HOST>/api \
   PROBE_API_KEY=<system 単位の API token> \
   python main.py
   ```

   Dashboard の該当 System の Traces 画面に反映されれば疎通確認完了。

## バックアップ

以下の named volume が永続データを持つ。定期的にバックアップする。

- `probe-data`（`docker-compose.prod.yml` の `probe-data` volume、
  `/data/probe.db`）: trace / policy / users / Feature Intelligence など
  すべての永続データ
- `caddy-data`（Caddy の `caddy-data` volume、`/data`）: ACME account key
  と発行済み証明書。失うと次回起動時に証明書を再取得する（レート制限に
  注意）が、データ自体は失われない

Control Server は SQLite を WAL mode で使用する。稼働中の `probe.db`、
`probe.db-wal`、`probe.db-shm` を `tar` / `cp` で別々に読み取る方法では、
同じ時点のファイルが得られる保証がない。`probe-data` volume を live のまま
tar archive にしてはならない。SQLite の online backup API を使う次の CLI
なら、Control Server を稼働させたまま committed transaction を含む整合した
単一の database file を作成できる。

```bash
mkdir -p "$PWD/backups"
chmod 700 "$PWD/backups"
docker compose -f docker-compose.prod.yml run --rm --no-deps \
  -v "$PWD/backups:/backup" \
  control-server \
  python -m app.sqlite_backup backup \
    /data/probe.db \
    /backup/probe-$(date -u +%Y%m%dT%H%M%SZ).db
```

出力 file は mode `0600` になり、publish 前後に `integrity_check`、`fsync`、
同一 filesystem 内での atomic publish を行う。既存の出力 file は既定では
上書きしない。同じ file を意図的に更新する場合に限り、対象 path を再確認して
`backup ... --overwrite` を指定する。作成済み backup は次でも再検証できる。

```bash
docker compose -f docker-compose.prod.yml run --rm --no-deps \
  -v "$PWD/backups:/backup:ro" \
  control-server \
  python -m app.sqlite_backup verify /backup/<BACKUP_FILE>.db
```

`caddy-data` は SQLite ではないため、従来どおり volume archive を取得できる。

```bash
docker run --rm -v probe-agent_caddy-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/caddy-data-$(date +%Y%m%d).tar.gz -C /data .
```

volume 名の prefix (`probe-agent_`) は compose project 名に依存する。
実際の名前は `docker volume ls` で確認する。

### SQLite の復元

復元中に Control Server が一度でも DB を開くと、置換後の main database に
古い WAL が再生されるおそれがある。`execution-worker` は `probe-data` を
mount しないため、通常は `control-server` service を停止すればよいが、同じ
volume を開く臨時 container がないことも確認する。停止確認を省略して復元
してはならない。

```bash
docker compose -f docker-compose.prod.yml stop control-server

docker compose -f docker-compose.prod.yml run --rm --no-deps \
  -v "$PWD/backups:/backup:ro" \
  control-server \
  python -m app.sqlite_backup restore \
    /backup/<BACKUP_FILE>.db \
    /data/probe.db \
    --confirm-control-server-stopped

docker compose -f docker-compose.prod.yml up -d control-server
docker compose -f docker-compose.prod.yml ps control-server
curl --fail --silent --show-error "https://${PUBLIC_HOST}/api/health"
```

restore CLI は次の順で処理する。

1. backup の `integrity_check` を行う。破損していれば現 DB に触れず終了する。
2. 現 DB がある場合は、WAL の committed data も含む整合した safety copy を
   `/data/probe.db.pre-restore-<UTC timestamp>` に作る。
3. backup を同じ directory の一時 file に復元して再検証し、現 DB の mode と
   ownership を可能な範囲で引き継ぐ。維持できなければ replace 前に失敗する。
4. safety copy の作成後、明示された停止状態のもとで現 DB を checkpoint し、
   置換対象に対応する `probe.db-wal` と `probe.db-shm` だけを削除して directory
   を `fsync` する。削除に失敗した場合は main DB を置換せず終了する。その後に
   一時 file を atomic replace し、復元後 DB を再検証する。

起動後は health check だけで完了扱いにせず、Dashboard にログインし、既知の
System、trace、設定が backup 取得時点の値になっていることを確認する。確認が
終わるまで safety copy を保持する。失敗した場合は何度も restore を繰り返さず、
表示された safety copy と container log を保全して原因を調べる。

### 復元 drill と保管 policy

少なくとも定期的に、production volume ではなく disposable volume へ backup
を復元する drill を行う。drill では以下を記録する。

1. 空の disposable volume の `/data/probe.db` へ restore CLI で復元する。
2. `python -m app.sqlite_backup verify /data/probe.db` が成功することを確認する。
3. production と同じ image version で一時 Control Server を起動し、`/health`
   と既知の System / trace を確認する。
4. 所要時間、backup 日時、検証した既知 data、実施者を記録して disposable
   container / volume を削除する。

backup には user 情報、repository metadata、trace input/output が含まれる。
mode `0600` は暗号化ではない。次を運用 policy として定める。

- backup directory 自体を暗号化 disk 上に置き、off-host 転送は TLS と
  server-side encryption/KMS、または組織標準の client-side encryption を使う。
- production host 障害に備え、暗号化した copy を別 failure domain に置く。
- 例として daily 7 世代、weekly 4 世代、monthly 12 世代など、組織要件に合う
  有限の retention と削除責任者を決める。restore safety copy も確認後に同じ
  policy で処分する。
- backup/restore の成功・失敗を監視し、定期 restore drill が未実施なら
  backup 成功とは見なさない。

## 証明書更新失敗時の確認手順

1. `docker compose -f docker-compose.prod.yml logs caddy` でエラー内容を
   確認する。
2. DNS が引き続き VM の静的外部 IPv4 を指しているか確認する
   (`dig +short <PUBLIC_HOST>`)。
3. ファイアウォールで 80/443 が塞がれていないか確認する。
4. ACME レート制限に達している場合は、Let's Encrypt のレート制限が
   解除されるまで待つ（`caddy-data` volume を消さずに保持していれば、
   同一証明書の再利用・自動リトライは Caddy 側で行われる）。
5. 上記で解決しない場合は `docker compose -f docker-compose.prod.yml
   restart caddy` で再試行する。

## 公開前チェック

- [ ] `CONTROL_ENV=production` になっている（`docker-compose.prod.yml` は
      固定でこれを設定する）。確認方法:
      `docker compose -f docker-compose.prod.yml exec control-server env |
      grep CONTROL_ENV` で `production` が返ること
- [ ] 初期管理者ユーザーが作成済み（`CONTROL_ADMIN_USERNAME` /
      `CONTROL_ADMIN_PASSWORD` で起動し、admin でログインできることを
      確認した）
- [ ] `.env` の値が `change-me` や `dev-secret-key` などの開発用初期値の
      ままになっていない（`CONTROL_ENV=production` はこれらのサンプル値を
      パスワードとして拒否し起動そのものを失敗させるので、そのまま使うと
      デプロイできない）
- [ ] SDK からの接続には system 単位で発行した API token
      (`PROBE_API_KEY`) を使い、`CONTROL_API_KEYS` の共有 legacy key を
      本番の SDK 接続に使い回していない
- [ ] `CONTROL_API_KEYS` は空にしている（`CONTROL_ENV=production` では
      legacy key は起動時に明示的に禁止されており、空でなければ起動が
      失敗する）
- [ ] `CONTROL_REQUIRE_AUTH=true` になっている。確認方法:
      `docker compose -f docker-compose.prod.yml exec control-server env |
      grep CONTROL_REQUIRE_AUTH` で `true` が返ること、かつ
      admin ユーザーも `CONTROL_API_KEYS` も未設定の状態でこの構成を
      起動すると意図的に起動が失敗すること（=フェイルセーフが効いている
      ことの逆確認）
- [ ] SDK token 秒間、管理 user 分間、System 日次 LLM、System Trace 行数・
      bytes の5上限を workload と保存期間に合わせて明示設定し、
      `docker compose -f docker-compose.prod.yml config` が未設定エラーなしで
      完了すること
