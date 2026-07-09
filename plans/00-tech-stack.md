# 00. 技術スタックと開発基盤

> 対象読者と前提: 「Alinea — 論文読解ワークベンチ」を docs/00〜12(機能仕様の正)と確定デザイン16画面に100%忠実に実装する開発者。本書はリポジトリ構成・開発環境・ツールチェーン・規約・CI を確定させる基盤文書であり、以降の plans 全文書はここで定義した識別子(ディレクトリ・環境変数・コマンド)を前提とする。技術選定の正は spec-decisions C 項。機能仕様と矛盾した場合は docs が正。

## 1. スタック一覧(バージョン固定)

すべて 2026-07-06 時点の安定版で固定する。バージョン更新は Renovate の PR 経由でのみ行う(§7)。

### 1.1 ランタイム・言語

| レイヤ | 技術 | 固定バージョン | 採用理由 |
|---|---|---|---|
| Node.js | Node.js | 24.4.0(`.node-version` で固定) | 2026-07 時点のアクティブ LTS。Next.js 15 / WXT / Turborepo の要求を満たす |
| パッケージ管理(JS) | pnpm | 10.13.1(`packageManager` フィールドで固定) | workspaces によるモノレポ、厳密な node_modules 分離、ディスク効率 |
| タスクランナー | Turborepo(`turbo`) | 2.5.4 | ビルド・lint・test の依存グラフ実行とリモートキャッシュ。CI 時間短縮 |
| Python | CPython | 3.12.11(`.python-version` で固定) | FastAPI / SQLAlchemy 2 / PyMuPDF の安定動作実績。3.13 は C 拡張(PyMuPDF)の検証コストを避けて見送り |
| パッケージ管理(Python) | uv | 0.7.20 | ワークスペース機能で apps/api・apps/worker・packages/py-core・packages/llm を単一ロックファイル(`uv.lock`)管理。pip 比で高速 |

### 1.2 フロントエンド(apps/web)

| 技術 | 固定バージョン | 採用理由 |
|---|---|---|
| Next.js(App Router) | 15.4.2 | RSC による初期表示高速化(docs/09 §1: ビューア初期表示 p50 2秒)。共有ページ(4c)・記事モードの SSR(KaTeX サーバーレンダリング要件、docs/09 §7.2) |
| React | 19.1.0 | Next.js 15 の標準。Server Components / Actions |
| TypeScript | 5.8.3 | 全 JS コードで必須。`strict: true` |
| Tailwind CSS | 4.1.11 | v4 の CSS ファーストな `@theme` に packages/tokens の CSS 変数をマップ。確定デザイントークンとの1対1対応が作りやすい |
| TanStack Query | 5.81.5 | サーバー状態(ライブラリ一覧・ジョブ進捗ポーリング・通知)。キャッシュ無効化を系統立てる |
| Zustand | 5.0.6 | クライアント UI 状態(ビューア表示モード・サイドパネルタブ・選択メニュー)。Redux 級の重さを避ける |
| KaTeX | 0.16.22 | 数式描画。SSR 可(docs/09 §7.2)。MathJax は速度で不採用 |
| PDF.js(`pdfjs-dist`) | 5.3.31 | PDF モード(2a)の原紙面描画+bbox オーバーレイ |
| dnd-kit(`@dnd-kit/core`) | 6.3.1 | すぐ読むキュー(1d)・コレクション順序(4b)のドラッグ並べ替え |

### 1.3 バックエンド(apps/api, apps/worker, packages/py-core, packages/llm)

| 技術 | 固定バージョン | 採用理由 |
|---|---|---|
| FastAPI | 0.115.14 | OpenAPI 自動生成(packages/api-client の源泉)、Pydantic v2 ネイティブ、async |
| Uvicorn | 0.35.0 | ASGI サーバー。`uvicorn[standard]` |
| SQLAlchemy | 2.0.41 | async ORM(asyncpg)。型付き `Mapped[]` スタイル |
| asyncpg | 0.30.0 | PostgreSQL async ドライバ |
| Alembic | 1.16.2 | マイグレーション。自動生成+手動レビュー |
| Pydantic | 2.11.7 | API スキーマ・設定(`pydantic-settings` 2.10.1) |
| arq | 0.26.3 | Redis ベース非同期ジョブ。ジョブの正は DB のジョブテーブルに持ち、arq は実行トリガに徹する(段階再開・優先度繰り上げは plans/01 §4〜5 で定義) |
| Authlib | 1.6.0 | OAuth(Google / GitHub)クライアント |
| httpx | 0.28.1 | arXiv API・外部書誌 API・リソースメタ取得の HTTP クライアント |
| selectolax | 0.3.29 | arXiv HTML パース(品質 A)。lxml 比で高速・省メモリ |
| PyMuPDF(`pymupdf`) | 1.26.3 | PDF パース(品質 B)・ページ画像化・bbox 抽出 |
| pdfplumber | 0.11.7 | PDF の表検出補助(PyMuPDF と併用) |
| cryptography | 45.0.5 | BYOK API キーの AES-256-GCM 暗号化(docs/09 §4) |
| openai SDK | 1.93.0 | GPT 系テキスト+`gpt-image-2`。DeepSeek(OpenAI 互換)・xAI(OpenAI 互換)にも同 SDK を `base_url` 差し替えで使用 |
| anthropic SDK | 0.57.1 | Claude 系(structured outputs `output_config.format`・adaptive thinking 対応版) |
| google-genai SDK | 1.24.0 | Gemini テキスト+画像(`gemini-3.1-flash-image` 等) |
| aioboto3 | 13.5.0 | S3 互換ストレージ(MinIO / R2)への async アクセス |
| ulid-py(`python-ulid`) | 3.0.0 | 主キー ULID 生成(§6.4) |

### 1.4 拡張機能(apps/extension)

| 技術 | 固定バージョン | 採用理由 |
|---|---|---|
| WXT | 0.20.7 | MV3 対応の拡張フレームワーク。Chrome / Edge を単一コードベースでビルド(docs/08)。HMR 付き開発 |
| UI フレームワーク | React 19.1.0 | ポップアップ UI。apps/web と知識・コンポーネント規約を共有(WXT の React モジュール `@wxt-dev/module-react` 1.1.3) |

### 1.5 データ基盤

| 技術 | 固定バージョン | 採用理由 |
|---|---|---|
| PostgreSQL | 16(イメージ: `groonga/pgroonga:4.0.1-alpine-16`) | 単一 DB で全エンティティ+ジョブテーブル+全文検索を賄う |
| PGroonga | 4.0.1(同イメージに同梱) | 日本語形態素+英語ステミングの全文検索(docs/09 §7.2: 日英クロス・ヒット源区別)。Elasticsearch 等の別プロセスを持たない(運用単純化) |
| Redis | 7.4(イメージ: `redis:7.4-alpine`) | arq キュー・セッションキャッシュ・レート制限カウンタ |
| オブジェクトストレージ | S3 互換(開発: MinIO `RELEASE.2025-04-22T22-12-26Z` / 本番: Cloudflare R2) | SourceAsset 原本・図アセット・SVG 原データ・生成ラスターの保持(docs/09 §7.2)。R2 は egress 無料で PDF 配信コストを抑える |

### 1.6 認証・その他

| 技術 | 決定 | 採用理由 |
|---|---|---|
| 認証 | FastAPI + Authlib による OAuth(Google / GitHub)+メールマジックリンク。セッションは HTTPOnly + Secure + SameSite=Lax クッキー(名称 `ykd_session`、サーバー側セッションストアは PostgreSQL) | docs/09 §4(CSRF/XSS 対策)。JWT アクセストークンは採用しない(失効制御と削除要件のためサーバーセッション) |
| メール送信 | SMTP(開発: Mailpit / 本番: Amazon SES の SMTP エンドポイントを既定とし、`SMTP_*` 環境変数の差し替えのみで他プロバイダへ移行可能にする。コードはプロバイダ非依存の素の SMTP のみ) | マジックリンク・将来の締切リマインドメールは v2。v1 のメール用途はログインリンクのみ |
| API クライアント | packages/api-client — FastAPI の openapi.json から `@hey-api/openapi-ts` 0.78.3 で TS クライアント生成 | 型のドリフトを CI で検出(§8 ジョブ `openapi-drift`)。手書きフェッチ層を持たない |
| デザイントークン | packages/tokens — 確定デザインの値を単一ソースで管理(§3.6) | docs/09 §7.2「トークンを単一ソースで管理し、デザインとの差分を作らない」 |

## 2. モノレポ構成

リポジトリルートは `paper-reader/`(GitHub リポジトリ名 `alinea`)。pnpm workspaces + Turborepo(JS)と uv workspace(Python)を同居させる。

```
paper-reader/
├── README.md
├── docs/                          # 機能仕様(00〜12)— 本計画の上位仕様
├── plans/                         # 実装計画書(本書ほか)
├── .node-version                  # 24.4.0
├── .python-version                # 3.12.11
├── package.json                   # ルート。packageManager: pnpm@10.13.1、turbo/prettier を devDependencies に持つ
├── pnpm-workspace.yaml            # packages: ["apps/*", "packages/*"]
├── pnpm-lock.yaml
├── turbo.json                     # build/lint/typecheck/test タスクのパイプライン定義
├── pyproject.toml                 # uv workspace ルート([tool.uv.workspace] members = ["apps/api", "apps/worker", "packages/py-core", "packages/llm"])
├── uv.lock                        # Python 全体の単一ロックファイル
├── docker-compose.yml             # §4 の完全形
├── .env.example                   # §5 の全変数の雛形(秘匿値は空)
├── .gitignore
├── .prettierrc.json               # §5 参照
├── eslint.config.mjs              # ルート flat config(各 app が extends)
├── renovate.json                  # 依存更新 Bot 設定
├── .github/
│   └── workflows/
│       ├── ci.yml                 # §8 の完全形
│       └── deploy.yml             # 本番デプロイ(plans/01 §8 で定義)
│
├── apps/
│   ├── web/                       # Next.js 15(App Router)
│   │   ├── package.json           # name: "@alinea/web"
│   │   ├── next.config.ts
│   │   ├── tsconfig.json          # paths: {"@/*": ["./src/*"]}
│   │   ├── postcss.config.mjs     # Tailwind v4(@tailwindcss/postcss)
│   │   ├── playwright.config.ts   # E2E(§7.7)
│   │   ├── vitest.config.ts
│   │   ├── public/
│   │   │   └── fonts/             # セルフホストしない。Google Fonts を next/font/google で読込(_global.md の指定 weight)
│   │   └── src/
│   │       ├── app/               # ルーティング(URL 設計は plans/09-screens/ の画面計画で確定)
│   │       │   ├── layout.tsx     # next/font 読込・ThemeProvider・QueryClientProvider
│   │       │   ├── (app)/         # 認証必須領域: dashboard, library, collections, vocab, search, settings, papers/[itemId]
│   │       │   ├── (public)/c/[token]/page.tsx   # 共有ページ(4c)。noindex
│   │       │   └── (auth)/login/page.tsx
│   │       ├── components/
│   │       │   ├── ui/            # 汎用(Button, Badge, Popover, Modal, SegmentedControl …)
│   │       │   ├── viewer/        # 1a/1b/1c/2a/1h(TranslationPane, BilingualPane, PdfPane, ArticlePane, SidePanel …)
│   │       │   ├── library/       # 1d/1e/4a/4b(LibraryTable, LibraryCard, StatusPill, QuickFilterBar …)
│   │       │   ├── vocab/         # 4d
│   │       │   ├── search/        # 4e
│   │       │   └── notifications/ # 4a ポップオーバー
│   │       ├── hooks/             # useReadingSession, useAnchorNavigation …
│   │       ├── stores/            # Zustand(viewer-store.ts, selection-store.ts …)
│   │       ├── lib/               # katex-render.ts, anchor-format.ts, date-format.ts …
│   │       └── styles/globals.css # @import "@alinea/tokens/css"; @theme マッピング
│   │
│   ├── api/                       # FastAPI
│   │   ├── pyproject.toml         # name: "alinea-api"。依存に alinea-core(workspace)
│   │   ├── alembic.ini
│   │   ├── alembic/
│   │   │   ├── env.py
│   │   │   └── versions/          # マイグレーション(命名: {rev}_{slug}.py)
│   │   └── src/alinea_api/
│   │       ├── main.py            # FastAPI アプリ生成・ルータ登録・ミドルウェア
│   │       ├── settings.py        # pydantic-settings(§5 の変数を型付きで読む)
│   │       ├── deps.py            # DB セッション・認証ユーザーの Depends
│   │       ├── routers/           # リソース別(パスは `/api` 直下、plans/03 §1.1)(auth.py, library_items.py, papers.py, translations.py, chat.py, annotations.py, notes.py, vocab.py, resources.py, articles.py, collections.py, share.py, search.py, notifications.py, settings.py, jobs.py, extension.py)
│   │       ├── services/          # ドメインロジック(ingest_service.py, translation_service.py, srs_service.py …)
│   │       └── schemas/           # Pydantic リクエスト/レスポンス(routers と 1:1)
│   │
│   ├── worker/                    # arq ワーカー
│   │   ├── pyproject.toml         # name: "alinea-worker"。依存に alinea-core(workspace)
│   │   └── src/alinea_worker/
│   │       ├── main.py            # arq WorkerSettings(キュー: ingest, translate, generate)
│   │       ├── tasks/             # fetch_source.py, parse_document.py, translate_blocks.py, generate_summary.py, generate_article.py, render_overview_svg.py, generate_explainer_image.py, generate_vocab_ai.py, fetch_resource_meta.py, srs_scheduler.py, deadline_reminder.py
│   │       └── pipeline.py        # ジョブステートマシン(docs/02 §5.1)の駆動
│   │
│   └── extension/                 # WXT(MV3, Chrome/Edge)
│       ├── package.json           # name: "@alinea/extension"
│       ├── wxt.config.ts          # manifest(permissions: ["activeTab", "storage"], host_permissions: ["https://arxiv.org/*"](ピル有効時のみ実行))
│       ├── tsconfig.json
│       └── src/
│           ├── entrypoints/
│           │   ├── popup/         # 4状態ポップアップ(App.tsx, states/SaveForm.tsx, Saved.tsx, Existing.tsx, GenericPdf.tsx)
│           │   ├── background.ts  # バッジ管理・送信キュー(chrome.storage.local 永続、docs/08 §6)
│           │   └── arxiv-pill.content.ts  # arXiv 限定「A 保存」ピル(既定オフ、docs/08 §5)
│           ├── lib/api.ts         # packages/api-client を利用
│           └── lib/pdf-detect.ts  # タブ内 PDF 判定・書誌ローカル推定
│
└── packages/
    ├── tokens/                    # デザイントークン(単一ソース)
    │   ├── package.json           # name: "@alinea/tokens"。exports: {"./css": "./dist/tokens.css", "./js": "./dist/tokens.js"}
    │   ├── src/tokens.json        # _global.md の全値(色・書体・導出規則・注釈4色・ステータス6色)
    │   └── build.mjs              # tokens.json → tokens.css(CSS変数)/ tokens.ts(型付き定数)を生成
    │
    ├── api-client/                # OpenAPI 生成 TS クライアント
    │   ├── package.json           # name: "@alinea/api-client"
    │   ├── openapi-ts.config.ts   # 入力: apps/api の openapi.json、出力: src/generated/
    │   └── src/
    │       ├── generated/         # 自動生成(手編集禁止。CI でドリフト検出)
    │       └── index.ts           # fetch クライアント設定(credentials: "include")の薄いラッパ
    │
    ├── py-core/                   # Python 共有パッケージ(api と worker が共用)
    │   ├── pyproject.toml         # name: "alinea-core"
    │   └── src/alinea_core/
    │       ├── db/                # SQLAlchemy モデル(models/*.py。テーブル定義は plans/02)、session.py、ids.py(ULID 生成)
    │       ├── document/          # 構造化ドキュメント中間表現(blocks.py, inlines.py, anchor.py, stable_id.py)
    │       ├── parsers/           # arxiv_html.py, latex.py(M2), pdf.py
    │       ├── translation/       # placeholder.py(保護・復元・検証), pipeline.py
    │       ├── figures/           # overview_dsl.py(概要図 JSON DSL)、svg_renderer.py(決定的 SVG)
    │       ├── storage/           # s3.py(aioboto3 ラッパ、バケット定数)
    │       ├── search/            # pgroonga_query.py(日英クロス検索クエリ構築)
    │       └── licenses.py        # arXiv ライセンスマトリクス判定(docs/09 §5.2)
    │
    └── llm/                       # LLM / 画像プロバイダ抽象化層(完全定義は plans/04 §2)
        ├── pyproject.toml         # name: "alinea-llm"(import 名 alinea_llm)
        ├── models.yaml            # モデルレジストリのシード(plans/04 §7)
        ├── routing.yaml           # タスクルーティングのシード(plans/04 §8)
        └── src/alinea_llm/      # types.py, protocols.py, registry.py, routing.py, router.py, providers/(openai_provider.py, anthropic_provider.py, google_provider.py, deepseek_provider.py, xai_provider.py, images/)
```

構成上の決定:

- 決定: Python 共有コードは `packages/py-core`(パッケージ名 `alinea_core`)に一本化し、apps/api と apps/worker は同パッケージに依存する。理由: モデル・パーサ・翻訳層を api/worker で二重定義すると必ず乖離するため。uv workspace のパス依存で編集は即時反映される。
- 決定: LLM / 画像プロバイダ抽象化層のみは `packages/llm`(パッケージ名 `alinea_llm`)に分離する(plans/04 §2 と一致)。apps/api・apps/worker の双方が path 依存で参照する。理由: 依存(各社 SDK・tiktoken)とリリースサイクルが py-core と異なり、plans/04 のテストスイートを独立実行するため。
- 決定: apps/web から apps/api への型共有は packages/api-client(OpenAPI 生成)のみを経路とする。理由: 手書き型の二重管理を排除し、CI のドリフト検出(§8)で契約破壊を機械検出するため。
- 決定: 拡張(apps/extension)の permissions は `activeTab` + `storage` の2つ、`host_permissions` は `https://arxiv.org/*` のみ(ページ内ピル用。既定オフのため `optional_host_permissions` として宣言し、設定オンで要求)。理由: docs/08 §1「権限は最小」+ストア審査。

## 3. 開発環境: docker-compose.yml(完全形)

開発でコンテナ化するのはデータ基盤のみ。web / api / worker / extension はホストで直接実行する(HMR・デバッガの都合)。

```yaml
# docker-compose.yml(リポジトリルート)
name: alinea

services:
  db:
    image: groonga/pgroonga:4.0.1-alpine-16
    container_name: alinea-db
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: alinea
      POSTGRES_PASSWORD: alinea
      POSTGRES_DB: alinea
      POSTGRES_INITDB_ARGS: "--locale=C --encoding=UTF8"
    volumes:
      - db-data:/var/lib/postgresql/data
      - ./docker/db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U alinea -d alinea"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7.4-alpine
    container_name: alinea-redis
    ports:
      - "6379:6379"
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  minio:
    image: minio/minio:RELEASE.2025-04-22T22-12-26Z
    container_name: alinea-minio
    ports:
      - "9000:9000"   # S3 API
      - "9001:9001"   # 管理コンソール
    environment:
      MINIO_ROOT_USER: alinea
      MINIO_ROOT_PASSWORD: alinea-dev-secret
    command: ["server", "/data", "--console-address", ":9001"]
    volumes:
      - minio-data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 3s
      retries: 10

  minio-init:
    image: minio/mc:RELEASE.2025-04-16T18-13-26Z
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 alinea alinea-dev-secret &&
      mc mb --ignore-existing local/alinea-sources &&
      mc mb --ignore-existing local/alinea-assets &&
      exit 0
      "

  mailpit:
    image: axllent/mailpit:v1.27.0
    container_name: alinea-mailpit
    ports:
      - "1025:1025"   # SMTP
      - "8025:8025"   # Web UI(受信メール確認 = ログインリンクの取得)
    environment:
      MP_SMTP_AUTH_ACCEPT_ANY: 1
      MP_SMTP_AUTH_ALLOW_INSECURE: 1

volumes:
  db-data:
  redis-data:
  minio-data:
```

```sql
-- docker/db/init.sql(初回起動時に実行)
CREATE EXTENSION IF NOT EXISTS pgroonga;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

決定事項:

- 決定: バケットは `alinea-sources`(SourceAsset 原本: LaTeX ソース tar・PDF 原本)と `alinea-assets`(派生物: 図画像・サムネイル・概要図 SVG/DSL スナップショット・解説図ラスター)の2つ。理由: 原本(削除不可・再処理原資、docs/09 §2)と派生物(再生成可能)でライフサイクル・バックアップポリシーを分けるため。
- 決定: Mailpit を開発標準の SMTP とする。理由: 認証がメールマジックリンクを含む(C5)ため、開発でメール受信 UI が必須。
- 決定: web/api/worker はコンテナ化しない(ホスト実行)。理由: Next.js Turbopack と uvicorn --reload の HMR 性能、デバッガ接続の単純さ。イメージ化は本番デプロイ(plans/01 §8)でのみ行う。

## 4. ツールチェーン(バージョンと設定方針)

### 4.1 一覧

| ツール | バージョン | 対象 | 実行コマンド |
|---|---|---|---|
| Ruff(lint+format) | 0.12.2 | Python 全体 | `uv run ruff check .` / `uv run ruff format .` |
| mypy | 1.16.1 | Python 全体 | `uv run mypy .` |
| pytest | 8.4.1(+ pytest-asyncio 1.0.0, hypothesis 6.135.26) | apps/api, apps/worker, packages/py-core, packages/llm | `uv run pytest` |
| ESLint | 9.30.1(flat config) | apps/web, apps/extension, packages/* | `pnpm turbo lint` |
| Prettier | 3.6.2 | JS/TS/CSS/MD/JSON/YAML | `pnpm format` |
| Vitest | 3.2.4 | apps/web, apps/extension, packages/tokens | `pnpm turbo test` |
| Playwright | 1.53.2 | apps/web(E2E) | `pnpm --filter @alinea/web e2e` |
| tsc(typecheck) | TypeScript 5.8.3 | 全 TS パッケージ | `pnpm turbo typecheck` |

### 4.2 Ruff 設定(ルート pyproject.toml)

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "ASYNC", "S", "RUF"]
ignore = ["S101"]  # pytest の assert を許可

[tool.ruff.lint.per-file-ignores]
"**/tests/**" = ["S"]
```

### 4.3 mypy 設定(ルート pyproject.toml)

```toml
[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
exclude = ["alembic/versions/"]
```

- 決定: mypy は `strict = true` を初日から適用する。理由: ドメインモデル(Anchor・Block・TranslationUnit)の型崩れは全機能に波及するため(docs/01「本書のモデルが崩れると全機能が崩れる」)。

### 4.4 ESLint / Prettier 方針

- ESLint はルート `eslint.config.mjs` に共通ルール(`typescript-eslint` strict、`eslint-plugin-react-hooks`、`import/no-relative-parent-imports` 相当を `no-restricted-imports` で実装)を置き、各 app が拡張する。
- Prettier 設定(`.prettierrc.json`): `{"printWidth": 100, "singleQuote": false, "semi": true, "trailingComma": "all"}`。ESLint とはルール衝突しない(`eslint-config-prettier` 10.1.5 適用)。

### 4.5 テスト方針(詳細は plans/12 テスト計画)

- pytest: サービス層・パーサ・翻訳プレースホルダを対象。プレースホルダ保護・復元(docs/03)は hypothesis によるプロパティテスト必須(C9。往復不変: `restore(protect(x)) == x`、プレースホルダ数一致)。DB を使うテストは docker-compose の実 PostgreSQL に対して実行する(SQLite 代替は PGroonga 依存のため禁止)。
- Vitest: コンポーネント単体(Testing Library)+アンカー表記導出・SRS 間隔計算などの純関数。
- Playwright: M0 DoD 相当のクリティカルパス E2E(保存→readable→ビューア→チャット→位置復元)。Chromium のみ(基準ビューポート 1440×900 を `viewport` に固定)。

### 4.6 packages/tokens のビルド

`src/tokens.json` を単一ソースとし、`node build.mjs` で以下を生成する(生成物はコミットしない。turbo の `build` タスクで生成):

- `dist/tokens.css` — `:root` と `[data-theme="dark"]` の CSS 変数(`--pr-a`, `--pr-as`, `--pr-am`, `--pr-ad`, `--pr-ads`, `--pr-adm`, `--pr-jp`, 注釈4色 `--ann-important: #C49432` / `--ann-question: #5884AA` / `--ann-idea: #659471` / `--ann-term: #82827E`, ステータス6色 `--st-toread: #9AA0A6` / `--st-next: #C49432` / `--st-reading: var(--pr-a)` / `--st-done: #659471` / `--st-reread: #8E7AA6` / `--st-hold: #B0ACA2` ほか _global.md の全値)。アクセント4色(#3E5C76 / #4A6B57 / #6E5A7E / #7A5C48)の導出(rgba 0.10 / 0.32、ダーク 0.14 / 0.40)は build.mjs 内で計算し、`[data-accent="green"]` 等の属性セレクタで出力する。
- `dist/tokens.ts` — 同値の型付き定数(拡張・SVG レンダラ・チャートで使用)。

受け入れ条件: docs/09 §8「実装の CSS トークン値が確定デザインの値と一致する」を、tokens.json と _global.md 由来の期待値を突き合わせる Vitest スナップショットテストで担保する。

## 5. 環境変数

### 5.1 命名規約

1. 大文字 SNAKE_CASE。値の意味が一意になる名詞で終える(`_URL`, `_KEY`, `_SECRET`, `_ID`)。
2. ブラウザに露出する変数は `NEXT_PUBLIC_`(apps/web)/ `WXT_`(apps/extension、ビルド時埋め込み)接頭辞のみ。**秘匿値にこの接頭辞を付けることを禁止**する(レビュー・CI の grep で検査)。
3. 接続文字列は URL 形式に統一(`DATABASE_URL`, `REDIS_URL`)。
4. LLM 各社の API キーは各社 SDK の既定環境変数名をそのまま使う(独自名を作らない)。

### 5.2 一覧(.env.example の完全形)

```bash
# --- 実行環境 ---
APP_ENV=development                # development | production(挙動分岐は settings.py に集約)
APP_BASE_URL=http://localhost:3000 # Web の公開 URL(本番: https://alinea.app)。共有リンク・メールリンク生成に使用
API_BASE_URL=http://localhost:8000 # API の公開 URL

# --- DB / Redis / S3 ---
DATABASE_URL=postgresql+asyncpg://alinea:alinea@localhost:5432/alinea
REDIS_URL=redis://localhost:6379/0
S3_ENDPOINT_URL=http://localhost:9000        # 本番(R2): https://<accountid>.r2.cloudflarestorage.com
S3_REGION=us-east-1                          # R2 では auto
S3_ACCESS_KEY_ID=alinea
S3_SECRET_ACCESS_KEY=alinea-dev-secret
S3_BUCKET_SOURCES=alinea-sources
S3_BUCKET_ASSETS=alinea-assets

# --- 認証・セッション ---
SESSION_SECRET=change-me-64-hex              # セッションクッキー署名鍵(openssl rand -hex 32)
ALINEA_KEY_ENCRYPTION_SECRET=change-me-fernet-key # BYOK APIキーの Fernet マスタキー(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
OAUTH_GOOGLE_CLIENT_ID=
OAUTH_GOOGLE_CLIENT_SECRET=
OAUTH_GITHUB_CLIENT_ID=
OAUTH_GITHUB_CLIENT_SECRET=

# --- メール(マジックリンク) ---
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=login@alinea.app

# --- LLM / 画像生成(運営キー。BYOK はユーザーごとに DB 暗号化保存) ---
OPENAI_API_KEY=                    # GPT テキスト + gpt-image-2
ANTHROPIC_API_KEY=                 # Claude(claude-opus-4-8 / claude-sonnet-5 / claude-haiku-4-5)
GOOGLE_API_KEY=                    # Gemini テキスト + gemini-3.1-flash-image / gemini-3-pro-image
DEEPSEEK_API_KEY=                  # deepseek-v4-flash / v4-pro(OpenAI互換 base_url=https://api.deepseek.com)
XAI_API_KEY=                       # grok-4.3 / grok-imagine-image(OpenAI互換 base_url=https://api.x.ai/v1)

# --- 外部サービス ---
ARXIV_USER_AGENT="alinea/1.0 (contact: admin@alinea.app)"  # docs/09 §5.3 の規約遵守
GITHUB_API_TOKEN=                  # リソースメタ取得(スター数等)のレート制限緩和(任意)
YOUTUBE_API_KEY=                   # リソースメタ取得(再生時間)(任意)

# --- 計測(docs/09 §8 テレメトリ)---
OTEL_EXPORTER_OTLP_ENDPOINT=       # 空なら無効。本番で設定

# --- apps/web(ブラウザ露出可のみ) ---
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

# --- apps/extension(ビルド時埋め込み。露出可のみ) ---
WXT_API_BASE_URL=http://localhost:8000
WXT_APP_BASE_URL=http://localhost:3000
```

- 決定: LLM のモデル ID・用途別ルーティング・フォールバック連鎖は環境変数に置かず、DB の設定テーブル(plans/04 §15 の `llm_task_routes`。ユーザー上書きは `users.settings` の `llm_routing`)で管理する。理由: docs/09 §3.2「モデル ID は設定で変更可能(再デプロイなしで変更)」の受け入れ基準を満たすため。環境変数は認証情報のみを持つ。
- 決定: 秘匿値の読み込みは apps/api・apps/worker とも `alinea_api/settings.py` と同形の pydantic-settings クラス(`alinea_core.settings.CoreSettings`)経由に限定し、`os.environ` 直接参照を Ruff の禁止ルール(カスタム flake8-tidy-imports 相当の `banned-api`)で防ぐ。

## 6. コーディング規約

### 6.1 命名

| 対象 | 規約 | 例 |
|---|---|---|
| TS 変数・関数 | camelCase | `resolveAnchorLabel` |
| React コンポーネント | PascalCase(ファイル名も PascalCase.tsx) | `StatusPill.tsx` |
| React フック | `use` 接頭辞、ファイル名 kebab-case | `use-reading-session.ts` → `useReadingSession` |
| TS 非コンポーネントファイル | kebab-case | `anchor-format.ts` |
| Python モジュール・関数 | snake_case | `stable_id.py` / `derive_block_id()` |
| Python クラス | PascalCase | `TranslationUnit` |
| DB テーブル | snake_case 複数形 | `library_items`, `translation_units` |
| DB カラム | snake_case。boolean は `is_` / `has_` 接頭辞 | `is_official`, `next_review_at` |
| API パス | `/api` 接頭辞+kebab-case 複数形リソース(URL バージョニングなし、plans/03 §1.1) | `GET /api/library-items`, `POST /api/vocab-entries/{entryId}/reviews` |
| JSON フィールド(API) | snake_case(FastAPI/Pydantic の素の形。TS 側は生成クライアントが型で吸収) | `quality_level` |
| CSS 変数 | packages/tokens の定義名のみ使用(`--pr-*`, `--ann-*`, `--st-*`)。アプリ側での新規色定義禁止 | `var(--pr-as)` |
| 環境変数 | §5.1 | — |
| ブランチ | `feat/`, `fix/`, `chore/`, `docs/` + kebab-case | `feat/viewer-bilingual-pop` |
| コミット | Conventional Commits 1.0 | `feat(viewer): add paragraph-level bilingual popover` |

### 6.2 ディレクトリ・レイヤ規約(Python)

- 依存方向は `routers → services → (alinea_core の db / document …、および alinea_llm)` の一方向のみ。routers から SQLAlchemy モデルを直接返さない(必ず schemas の Pydantic に詰め替える)。
- worker の tasks は services を呼ばず alinea_core を直接使う(api の services は HTTP 文脈を含むため)。ジョブ投入は api 側 `services/jobs.py` の 1 モジュールに集約。
- 例外は alinea_core.errors の型付き例外(`SourceFetchError`, `PlaceholderMismatchError` …)を投げ、routers 側の exception handler で HTTP ステータスへ変換する(黙って壊れない: 失敗理由はジョブテーブルの `failure_reason` に構造化保存)。

### 6.3 import ルール

- TS: 相対 import は同一ディレクトリ(`./`)と 1 階層下のみ許可。親方向(`../`)は禁止し、`@/` エイリアスを使う(ESLint `no-restricted-imports` で強制)。apps 間の import は禁止。packages からの import は公開 exports(`@alinea/tokens/js` 等)のみ。
- Python: import は絶対パス(`from alinea_core.document import anchor`)のみ。相対 import 禁止(Ruff `TID252` 相当を有効化)。apps/api から apps/worker への import(逆も)は禁止 — 共有物は必ず packages/py-core(LLM 層は packages/llm)へ。
- 生成物(`packages/api-client/src/generated/`, `packages/tokens/dist/`)への手編集禁止。

### 6.4 識別子(ID)規約

- 全テーブルの主キーは接頭辞付き ULID 文字列(TEXT)。例: `usr_01J…`(users)、`pap_`(papers)、`li_`(library_items)、`rev_`(document_revisions)、`blk-` はブロック安定 ID で別規則(docs/01 §4.3 の決定的生成)。接頭辞の全対応表は plans/03 §1.6 を正とする。
- 理由: docs/01 の例示(`rev_01H...`)に一致し、ログ・URL 上で型が自明になる。時系列ソート可能で連番リークもない。

### 6.5 UI 文言

- UI 文言は日本語のみ(Q5)。デザイン抽出ファイル(extract/<画面ID>.md)の逐語を正とし、勝手な言い換えを禁止する。文言は各画面コンポーネントにハードコードしてよい(i18n 層は v1 では導入しない。理由: UI 言語が日本語のみで抽象化の受益者が不在)。

## 7. 依存更新

- Renovate(`renovate.json`)で週次(月曜 06:00 JST)にグループ化 PR を作成。メジャー更新は自動マージしない。LLM SDK(openai / anthropic / google-genai)は `schedule` を毎日にする(モデル世代交代への追随、docs/09 §3.2)。

## 8. CI(GitHub Actions)

`.github/workflows/ci.yml` の完全形。トリガは PR と main への push。Turborepo のキャッシュは `actions/cache` で `.turbo` を保存する。

```yaml
name: ci
on:
  pull_request:
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

env:
  DATABASE_URL: postgresql+asyncpg://alinea:alinea@localhost:5432/alinea
  REDIS_URL: redis://localhost:6379/0

jobs:
  js:
    name: JS lint / typecheck / unit test / build
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4          # pnpm は package.json の packageManager を読む
      - uses: actions/setup-node@v4
        with:
          node-version-file: .node-version
          cache: pnpm
      - uses: actions/cache@v4
        with:
          path: .turbo
          key: turbo-${{ runner.os }}-${{ github.sha }}
          restore-keys: turbo-${{ runner.os }}-
      - run: pnpm install --frozen-lockfile
      - run: pnpm turbo build lint typecheck test
      - run: pnpm prettier --check .

  python:
    name: Python lint / typecheck / test
    runs-on: ubuntu-24.04
    services:
      db:
        image: groonga/pgroonga:4.0.1-alpine-16
        env:
          POSTGRES_USER: alinea
          POSTGRES_PASSWORD: alinea
          POSTGRES_DB: alinea
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U alinea" --health-interval 5s
          --health-timeout 3s --health-retries 10
      redis:
        image: redis:7.4-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.7.20"
      - run: uv python install
      - run: uv sync --all-packages --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy .
      - run: psql "postgresql://alinea:alinea@localhost:5432/alinea" -c "CREATE EXTENSION IF NOT EXISTS pgroonga; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
      - run: uv run alembic upgrade head
        working-directory: apps/api
      - run: uv run pytest --cov=alinea_core --cov=alinea_llm --cov=alinea_api --cov=alinea_worker

  openapi-drift:
    name: OpenAPI クライアントのドリフト検出
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.7.20"
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version-file: .node-version
          cache: pnpm
      - run: uv sync --all-packages --frozen
      - run: pnpm install --frozen-lockfile
      - run: uv run python -m alinea_api.export_openapi > packages/api-client/openapi.json
      - run: pnpm --filter @alinea/api-client generate
      - run: git diff --exit-code packages/api-client   # 差分があれば失敗 = 生成し直してコミットせよ

  e2e:
    name: Playwright E2E(Chromium, 1440x900)
    runs-on: ubuntu-24.04
    needs: [js, python]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.7.20"
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version-file: .node-version
          cache: pnpm
      - run: docker compose up -d --wait db redis minio minio-init mailpit
      - run: uv sync --all-packages --frozen
      - run: pnpm install --frozen-lockfile
      - run: uv run alembic upgrade head
        working-directory: apps/api
      - run: uv run python -m alinea_api.seed --sample rectified-flow   # C10 シード(arXiv:2209.03003)
      - run: pnpm --filter @alinea/web exec playwright install --with-deps chromium
      - run: pnpm --filter @alinea/web e2e     # webServer 設定で api(uvicorn)+worker(arq)+web を起動
        env:
          OPENAI_API_KEY: test-stub              # E2E は LLM をスタブする(plans/12)
      - uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: playwright-report
          path: apps/web/playwright-report

  extension:
    name: 拡張ビルド(Chrome / Edge zip)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version-file: .node-version
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm --filter @alinea/extension zip          # Chrome 用
      - run: pnpm --filter @alinea/extension zip:edge     # Edge 用(同一コード、名前のみ)
      - uses: actions/upload-artifact@v4
        with:
          name: extension-zips
          path: apps/extension/.output/*.zip
```

- 決定: E2E での LLM 呼び出しは全プロバイダをスタブ(決め打ちレスポンスのローカル HTTP モック)にする。理由: CI コスト・フレーク排除。実プロバイダ疎通は夜間の別ワークフロー(plans/12 で定義)で検証する。
- 決定: `alinea_api.export_openapi` モジュール(FastAPI アプリから openapi.json を標準出力する 5 行のスクリプト)を api 側に置き、クライアント生成の入力を常にコードから導出する(サーバー起動不要)。

## 9. ローカル起動手順

前提: Git / Docker(Compose v2)/ [mise](https://mise.jdx.dev) または手動で Node 24.4.0・uv 0.7.20 を導入済み。

```bash
# 1. 取得と環境変数
git clone git@github.com:<org>/alinea.git && cd alinea
cp .env.example .env            # 使うプロバイダの API キーのみ設定(未設定のプロバイダはルーティング対象から自動除外される。plans/04 §8)

# 2. ランタイム(mise 利用時。手動なら .node-version / .python-version に従う)
mise install                    # node 24.4.0 / python 3.12.11 / uv 0.7.20

# 3. データ基盤
docker compose up -d --wait     # db(5432) redis(6379) minio(9000/9001) mailpit(1025/8025)

# 4. 依存インストール
pnpm install                    # JS 全ワークスペース
uv sync --all-packages          # Python 全ワークスペース(.venv をルートに作成)

# 5. DB マイグレーションとシードデータ(C10: Rectified Flow arXiv:2209.03003)
(cd apps/api && uv run alembic upgrade head)
uv run python -m alinea_api.seed --sample rectified-flow

# 6. 生成物の初回ビルド(tokens CSS と APIクライアント)
pnpm turbo build --filter=@alinea/tokens --filter=@alinea/api-client

# 7. 開発サーバー一括起動(turbo が並列実行)
pnpm dev
#   ├─ @alinea/web        → http://localhost:3000(Next.js dev)
#   ├─ alinea-api         → http://localhost:8000(uvicorn --reload。OpenAPI UI は /docs)
#   ├─ alinea-worker      → arq ワーカー(watchfiles で自動再起動)
#   └─ @alinea/extension  → wxt dev(Chrome を拡張ロード済みで起動)

# 8. 動作確認
#   - http://localhost:3000/login → メールアドレス入力 → http://localhost:8025 でリンクを開く
#   - wxt が開いた Chrome で https://arxiv.org/abs/2209.03003 を開き、ツールバー「A」→保存
#   - MinIO コンソール http://localhost:9001(alinea / alinea-dev-secret)
```

`pnpm dev` の実体はルート package.json の `"dev": "turbo dev"` で、turbo.json の `dev` タスク(`persistent: true, cache: false`)が各 app の `dev` スクリプトを並列起動する。Python 側の dev スクリプトは package.json を持つ薄いラッパ(`apps/api/package.json` の `"dev": "uv run uvicorn alinea_api.main:app --reload --port 8000"`、`apps/worker/package.json` の `"dev": "uv run arq alinea_worker.main.WorkerSettings --watch src"`)として turbo に参加させる。

- 決定: apps/api と apps/worker にも最小の package.json(`name`, `scripts.dev`, `scripts.lint` 等が uv コマンドを呼ぶだけ)を置き、turbo のタスクグラフに Python を統合する。理由: 起動・lint・test の入口を `pnpm turbo <task>` の 1 系統に統一し、CI とローカルのコマンド差をなくすため。

## 10. 受け入れ基準

- [ ] `docker compose up -d --wait` 後、§9 の手順どおりに新規メンバーが 30 分以内に「拡張から保存→ビューアで閲覧」まで到達できる
- [ ] 全バージョンがロックファイル(pnpm-lock.yaml / uv.lock)と本書の表で一致し、Renovate 以外の暗黙アップデートが発生しない
- [ ] CI の 5 ジョブ(js / python / openapi-drift / e2e / extension)が main と全 PR で実行され、openapi-drift が API 契約変更のコミット漏れを検出する
- [ ] 秘匿環境変数に `NEXT_PUBLIC_` / `WXT_` 接頭辞が付いていない(CI の grep 検査)
- [ ] packages/tokens の生成 CSS が _global.md のトークン値・導出規則(rgba 0.10/0.32/0.14/0.40、アクセント4色、注釈4色、ステータス6色)と一致するテストが通る
- [ ] LLM のモデル ID がコード・環境変数のどこにもハードコードされず、DB 設定テーブル参照のみである(docs/09 §8)
