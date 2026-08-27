# probe-agent Dashboard

probe-agent の Web ダッシュボード。React + TypeScript + Vite で構築されている。

Control Server と連携し、対象システムのトレース閲覧、モード制御、
リポジトリ理解、能力階層、probe 計画、実験管理を行う。

## セットアップ

```bash
cd apps/dashboard
npm install
```

## 開発

```bash
npm run dev
```

開発サーバーは `http://localhost:5173` で起動する。
Vite の proxy 設定により `/api` へのリクエストは自動的に
Control Server (`http://localhost:8000`) へ転送される。

Control Server が別のポートやホストで動いている場合は、
`vite.config.ts` の `server.proxy` を調整する。

## ビルド

```bash
npm run build
```

成果物は `dist/` に出力される。

## テスト

```bash
npm run test
```

## 主なページ

| ページ | パス | 説明 |
| --- | --- | --- |
| Overview | `/` | System Intelligence Brief / 意思決定コックピット。Vision・System Purpose・主要機能・Decision Readiness、今わかったこと(最大3件)、次にすること(1件)、改善ループの現在地、Runtime health。判定はすべて `GET /overview` が返す |
| System Understanding | `/system-understanding` | pipeline checklist、System Purpose、Capabilities、metadata coverage、docs-code gap、next actions |
| Repository | `/repository` | リポジトリ設定、snapshot 管理、symbol index、API scan |
| Capability Map | `/capability-map` | System Purpose → Core Capability → Element のツリーとドリルダウン |
| Feature Map | `/feature-map` | ユーザー価値単位の機能一覧と code mapping |
| Flow Explorer | `/flow-explorer` | entrypoint からの候補実行フロー可視化 |
| Probe Planner | `/probe-planner` | 観測点の mode・risk・承認管理 |
| Interview | `/interview` | システム理解インタビュー |
| Experiments | `/experiments` | baseline と source patch variants の比較実験 |

## 技術スタック

- React 19 + TypeScript
- Vite (dev server + build)
- TanStack Query (データフェッチ)
- Tailwind CSS 4
- React Router 7
- Vitest + Testing Library (テスト)
