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

   起動が失敗した場合は該当する環境変数を修正してから再実行する。

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

```bash
docker run --rm -v probe-agent_probe-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/probe-data-$(date +%Y%m%d).tar.gz -C /data .
docker run --rm -v probe-agent_caddy-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/caddy-data-$(date +%Y%m%d).tar.gz -C /data .
```

volume 名の prefix (`probe-agent_`) は compose project 名に依存する。
実際の名前は `docker volume ls` で確認する。

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
