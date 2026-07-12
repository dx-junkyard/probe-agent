# GitHub App 秘密鍵の配置とローテーション（Issue #224）

GitHub App 公開ワークフロー（Issue #216、設計は
[`docs/project-intelligence.md`](project-intelligence.md) の
「GitHub App 公開ワークフロー（Issue #216）」参照）を有効にするための、
GitHub App の登録から本番デプロイ（`docker-compose.prod.yml`、
[`docs/deployment-https.md`](deployment-https.md) 参照）での秘密鍵の
配置・ローテーションまでの手順。

## GitHub App の登録

1. GitHub の Organization 設定 → **Developer settings** → **GitHub Apps**
   から新しい GitHub App を作成する。
2. Permissions は最小限、以下のみを付与する。
   - **Contents**: Read and write（clone/fetch と probe patch の commit に必要）
   - **Pull requests**: Read and write（Pull Request の作成・一覧に必要）
   - **Metadata**: Read（GitHub App が最低限要求する既定権限）
   - それ以外の権限（Actions、Issues、Administration 等）は付与しない。
     probe-agent はこれらを一切使わない。
3. Webhook は不要（未使用）。Where can this GitHub App be installed?
   は `Only on this account`（対象 Organization 専用）を推奨する。
4. 作成後、対象リポジトリへこの GitHub App をインストールする。
   Installation ID は Dashboard の `GitHub` ページ → Installations タブで
   admin が登録し、利用する System へ明示的に割り当てる（Issue #222）。

## 秘密鍵の生成・ダウンロード

1. GitHub App の設定ページ → **Private keys** → **Generate a private key**。
2. `<app-name>.<date>.private-key.pem` がブラウザにダウンロードされる。
   この内容は二度と再ダウンロードできないので、失くした場合は新しい鍵を
   生成し直す（後述のローテーション手順と同じ）。

## 開発用 App と本番用 App を分ける

開発環境（`docker-compose.yml`）と本番環境（`docker-compose.prod.yml`）は、
**別々の GitHub App** を使うことを推奨する。

- 開発用 App: ローカルの検証用リポジトリにのみインストールし、開発用の
  秘密鍵をローカルマシンに置く（`GITHUB_APP_PRIVATE_KEY_PATH` に直接
  ホストパスを指定すればよい。Compose secret は不要）。
- 本番用 App: 本番でインストレーションを管理する Organization
  専用に作成し、その秘密鍵は本番ホスト以外に置かない。開発用の鍵と
  本番用の鍵を混在させない（開発環境の設定ミスや漏洩が本番リポジトリへの
  書き込み権限に波及しないようにするため）。

## 本番ホストへの秘密鍵配置

`docker-compose.prod.yml` は秘密鍵を Compose secret として
`control-server` コンテナへマウントする。コンテナ内のパスは
`/run/secrets/github_app_private_key` に固定されており、
`GITHUB_APP_PRIVATE_KEY_PATH` はこの値を直接設定する（上書き不可）。
ホスト側の実ファイルパスは `GITHUB_APP_PRIVATE_KEY_HOST_PATH` で指定する。

1. 秘密鍵を、リポジトリのどのマウントにも含まれない場所に置く。
   推奨は `/etc/probe-agent/secrets/` のような、Git リポジトリの外、かつ
   `PROBE_REPOSITORY_HOST_ROOT` 配下でも `probe-data` volume 配下でもない
   ディレクトリ。

   ```bash
   sudo mkdir -p /etc/probe-agent/secrets
   sudo mv ~/Downloads/<app-name>.<date>.private-key.pem \
     /etc/probe-agent/secrets/github-app-key.pem
   sudo chmod 600 /etc/probe-agent/secrets/github-app-key.pem
   sudo chown <compose-run-user> /etc/probe-agent/secrets/github-app-key.pem
   ```

   **絶対に置いてはいけない場所**: `PROBE_REPOSITORY_HOST_ROOT` 配下
   （committed snapshot 解析用にコンテナへ read-write mount される）、
   `probe-data` named volume（`/data/probe.db` のバックアップに含まれて
   しまう）。秘密鍵はこれらのどちらとも独立した場所に置く。

2. `.env` に以下を設定する。

   ```env
   GITHUB_APP_PRIVATE_KEY_HOST_PATH=/etc/probe-agent/secrets/github-app-key.pem
   GITHUB_PUBLISH_ENABLED=true
   GITHUB_APP_ID=<GitHub App の App ID>
   GITHUB_APP_ALLOWED_ORGANIZATION=<GitHub App を所有する Organization の login>
   ```

   `GITHUB_PUBLISH_ENABLED` は publish workflow を有効化する意思表示で、
   `true` にすると起動時に `GITHUB_APP_ID` とキーの妥当性
   （読み取り可能・空でない・PEM 秘密鍵としてパース可能）を検証する。
   いずれかを満たさない場合、明確なエラーメッセージ（秘密鍵の内容や
   パスの実値は含まない）を出して起動そのものが失敗する
   （fail closed、CLAUDE.md Principle 6/8）。

   `GITHUB_APP_PRIVATE_KEY_HOST_PATH` を未設定のままにすると、Compose
   secret は `/dev/null`（空ファイル）にフォールバックする。この場合
   `github_app_configured()` は「未設定」として扱い、`GitHub` 機能全体が
   引き続き fail closed になる。publish workflow を使わない環境では
   何も設定しなくてよい。

3. 起動する。

   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

4. Dashboard の `GitHub` ページ → App status カードで `configured: true`
   になっていることを確認する。

## 鍵ローテーションの手順

GitHub App は複数の秘密鍵を同時に有効な状態で保持できる（新しい鍵を
生成しても古い鍵はすぐには失効しない）ため、ダウンタイムなしで
ローテーションできる。

1. GitHub App の設定ページ → **Private keys** →
   **Generate a private key** で新しい鍵を生成する（古い鍵はまだ有効な
   ままなので、この時点でサービスは影響を受けない）。
2. 新しい PEM をホストへ配置する（既存の鍵とは別ファイル名を推奨、
   例 `github-app-key-2.pem`）。`chmod 600` を忘れない。
3. `.env` の `GITHUB_APP_PRIVATE_KEY_HOST_PATH` を新しいファイルへ切り替える。
4. 再作成して新しい secret マウントを反映させる。

   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```

   （`GITHUB_APP_PRIVATE_KEY_HOST_PATH` の変更は secret の中身を変えるため、
   コンテナの再作成が必要。`restart` だけでは secret の再マウントは
   行われない点に注意。）

5. Dashboard の `GitHub` ページで既存の connection を `Verify` し、
   新しい鍵で Installation Token の発行・疎通確認ができることを確認する。
6. 疎通確認ができたら、GitHub App の設定ページで古い鍵を **Delete**
   （失効）する。
7. 古い PEM ファイルをホストから安全に削除する。

## 関連ドキュメント

- 本番 HTTPS デプロイ全体の手順: [`docs/deployment-https.md`](deployment-https.md)
- GitHub App 公開ワークフローの設計・状態遷移:
  [`docs/project-intelligence.md`](project-intelligence.md) の
  「GitHub App 公開ワークフロー（Issue #216）」
