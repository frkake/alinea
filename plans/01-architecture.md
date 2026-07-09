# 実装計画 01. システムアーキテクチャ

> **対象読者と前提**: 本書は「Alinea — 論文読解ワークベンチ」を実装するエンジニア向けのアーキテクチャ定義である。機能仕様の正は `/docs/00〜12`(確定デザイン 16 画面準拠)であり、本書は docs が実装に委ねた技術詳細を**すべて確定**させる。技術スタックは確定済み(spec-decisions C 項): pnpm workspaces + Turborepo モノレポ / `apps/web`=Next.js 15(App Router)+React 19+TypeScript 5+Tailwind CSS v4 / `apps/api`=Python 3.12+FastAPI+SQLAlchemy 2+Alembic+Pydantic v2 / `apps/worker`=Python(arq) / `apps/extension`=WXT(MV3) / PostgreSQL 16+PGroonga / Redis 7 / S3 互換(dev=MinIO, prod=Cloudflare R2)/ 認証=FastAPI+authlib。DDL の完全形は `plans/02`(データモデル)、API エンドポイント一覧は `plans/03`(API 設計)に置き、本書はそれらと整合する用語(テーブル名 `jobs`・`notifications`・`sessions`・`usage_records`・`block_search_index`)を先に確定する。API パスは `plans/03` §1.1 の決定(URL バージョニングなし、全パス `/api/*` 配下)に従う。

## 1. 全体構成図

単一 VPS 上の Docker Compose 構成(§8)。太い処理(取得・パース・翻訳・生成)はすべて worker に寄せ、api はリクエスト/レスポンスと SSE 配信に徹する。

```mermaid
flowchart LR
    subgraph Client["クライアント"]
        EXT["apps/extension\nWXT MV3 (Chrome/Edge)\n取り込みの唯一の経路 (3a)"]
        BR["ブラウザ\n(ログインユーザー / 共有ページ匿名閲覧者)"]
    end

    subgraph VPS["単一 VPS (Docker Compose)"]
        CADDY["Caddy 2\nリバースプロキシ + TLS\nalinea.app"]
        WEB["apps/web\nNext.js 15 (node server)\nSSR: 共有ページ 4c のみ\n他は CSR + OpenAPI クライアント"]
        API["apps/api\nFastAPI (uvicorn)\nREST /api/* + SSE /api/events\n認証 (authlib, /api/auth/*)"]
        WI["apps/worker\nworker-interactive\n(arq, queue=alinea:interactive)"]
        WB["apps/worker\nworker-bulk\n(arq, queue=alinea:bulk)"]
        PG[("PostgreSQL 16\n+ PGroonga\n全データ + jobs + 全文検索")]
        RD[("Redis 7\narq キュー / Pub/Sub\nセッションキャッシュ")]
        S3[("S3 互換\ndev=MinIO / prod=R2\nsources / figures /\nthumbnails / renders / exports")]
    end

    subgraph Ext["外部サービス"]
        AX["arXiv\n(メタデータ API / LaTeX / HTML / PDF)"]
        LLM["LLM API\nOpenAI / Anthropic /\nGoogle / DeepSeek"]
        IMG["画像生成 API\nOpenAI / Google / xAI"]
        OAUTH["OAuth IdP\nGoogle / GitHub"]
        SMTP["SMTP\n(メールリンク認証)"]
        OGH["リソースメタデータ源\nGitHub API / YouTube oEmbed / OGP"]
    end

    EXT -->|"POST /api/ingest/arxiv (URL のみ)\nCookie 認証"| CADDY
    BR --> CADDY
    CADDY -->|"/ ・ /c/{token} ほか"| WEB
    CADDY -->|"/api/*"| API
    WEB -->|"SSR 時のみ API_INTERNAL_URL\nhttp://api:8000"| API
    API --> PG
    API --> RD
    API -->|"署名付き URL 発行"| S3
    API -->|"チャット (SSE 中継)"| LLM
    API --> OAUTH
    API --> SMTP
    WI --> PG
    WB --> PG
    WI --> RD
    WB --> RD
    WB --> AX
    WB --> S3
    WI --> LLM
    WB --> LLM
    WB --> IMG
    WB --> OGH
```

## 2. プロセスと責務分担

### 2.1 apps/web(Next.js 15, App Router)

- **BFF は置かない(決定)**。ブラウザは `packages/api-client`(OpenAPI 生成 TS クライアント、`@hey-api/openapi-ts` で生成 — plans/00 §1.3)経由で FastAPI を**直接**呼ぶ。理由: API は同一オリジン `/api/*` 配下にあり(§8.2)、中継層は遅延と二重実装を生むだけで、認証はセッションクッキーで完結するため。
- **SSR の対象は共有ページ(4c)`/c/{token}` のみ**。React Server Component が `API_INTERNAL_URL`(compose 内部 `http://api:8000`)の公開エンドポイント `GET /api/share/collections/{token}`(plans/03 §14.1)を fetch してレンダリングする。
  - レスポンスヘッダに `X-Robots-Tag: noindex` と `<meta name="robots" content="noindex">` の両方を付ける(docs/06 §5)。
  - OGP メタタグを SSR で付与する: `og:title`=コレクション名、`og:description`=説明文先頭 120 字、`og:site_name`="Alinea"、`og:image`=`/og/collection-default.png`(静的 1200×630px。個人資産を画像に出さないため動的 OG 画像は生成しない — 決定)。
  - v2 で記事公開が解禁された場合、記事ページの SSR+OGP はこの共有ページと同じ「公開データを内部 API から取得して RSC で描画」の枠組みに追加する(v1 では実装しない。docs/07 §2.8)。
  - キャッシュ: `Cache-Control: private, no-store` はログイン画面系、共有ページは `s-maxage=60, stale-while-revalidate=300`(Next.js `revalidate: 60`)。
- 上記以外の全画面(1a〜1h, 2a, 4a〜4f, 5a)は認証必須の CSR。ルートレイアウトでセッション確認(`GET /api/auth/me`)し、未認証は `/login` へ。データ取得は TanStack Query v5、クライアント状態(ビューア表示モード・サイドパネルタブ・選択状態)は Zustand。
- KaTeX は共有ページでは SSR(`katex.renderToString`)、アプリ内 CSR 画面ではクライアントレンダリング。PDF モードは PDF.js(`pdfjs-dist`)をクライアントで動かし、原文 PDF は S3 署名付き URL から取得する(§7.3)。
- デザイントークンは `packages/tokens` の CSS 変数(`--pr-a` / `--pr-as` / `--pr-am` / `--pr-ad` / `--pr-ads` / `--pr-adm` / `--pr-jp` ほか)を単一ソースとし、Tailwind v4 の `@theme` にマップする(詳細は `plans/08` デザインシステム計画)。

### 2.2 apps/api(FastAPI)

- 責務: REST API(`/api/*`)、認証(`/api/auth/*`)、SSE 配信(`/api/events`、チャットストリーミング)、S3 署名付き URL 発行、ジョブの投入と優先度繰り上げ、根拠チップ検証、クォータ・利用量計上。
- 非同期 SQLAlchemy 2.0(`asyncpg`)+ Pydantic v2。OpenAPI スキーマ(`/api/openapi.json`)が `packages/api-client` 生成の正。
- **10 秒を超えうる処理を API プロセス内で実行しない**(例外はチャット/再翻訳の LLM ストリーミング中継のみ)。それ以外の長時間処理はすべて `jobs` テーブル+arq に委譲する(docs/09 §1「長時間処理はすべて非同期ジョブ」)。
- uvicorn ワーカー数 2(`--workers 2`)。SSE 同時接続は 1 ワーカーあたり想定 200 本(小規模構成 Q1)。

### 2.3 apps/worker(Python, arq)

- 責務: 取り込みパイプライン(arXiv 取得・パース・構造化)、翻訳(セクション単位)、再翻訳、記事/概要図/解説図生成、語彙 AI 生成、リソースメタデータ取得、リアンカー、エクスポート生成、締切リマインド cron。
- **2 プロセス構成(決定)**: arq の Worker は 1 プロセス 1 キューのため、対話性の要る処理と物量処理をプロセスで分離する。
  - `worker-interactive`: Redis キュー `alinea:interactive`。オンデマンド翻訳(開いたセクション)、語彙 AI 生成、記事・図の生成、リソースメタデータ取得。`max_jobs=10`。
  - `worker-bulk`: Redis キュー `alinea:bulk`。取り込み、全文翻訳のバックグラウンド進行、エクスポート、リアンカー。`max_jobs=4`(LLM レート制限と VPS メモリを考慮)。
- Python 共有コードは 2 パッケージに分割する(決定。plans/00 §1.3・plans/04 §1 と同一): `LLMProvider` / `ImageProvider` 抽象化層・ルーティング・フォールバックは `packages/llm`(import 名 `alinea_llm`)、パーサ・アンカー処理・ジョブ共通処理・設定は `packages/py-core`(import 名 `alinea_core`)。いずれも uv workspace の path 依存で `apps/api` と `apps/worker` の両方から参照する(API のチャットと worker の翻訳が同一のルーティング/フォールバック実装を使うため)。

### 2.4 apps/extension(WXT, MV3)

- 責務: arXiv 検出、書誌プレビュー(サーバー照会)、保存(URL のみ送信)、タブ内 PDF の明示送信、パイプライン進捗表示、直近の取り込み 3 件、ツールバーバッジ(docs/08)。
- API 呼び出しは `https://alinea.app/api/*` に対する `fetch(…, { credentials: "include" })`。認証はサイトと同一のセッションクッキー(§6.4)。
- 読解機能は一切持たない(docs/08 §7)。

### 2.5 データ層

| コンポーネント | 責務 |
|---|---|
| PostgreSQL 16 + PGroonga | 全永続データ(ドメイン+`jobs`+`sessions`+`usage_records`+検索用 `block_search_index`)。全文検索は PGroonga(`pgroonga` インデックス、日本語トークナイズ+英語ステミング)で日英クロス検索(docs/06 §9)を実現 |
| Redis 7 | arq ジョブキュー(`alinea:interactive` / `alinea:bulk`)、SSE ファンアウト用 Pub/Sub(`events:user:{user_id}`)、セッションのルックアップキャッシュ(TTL 300 秒)、レートリミットカウンタ |
| S3 互換 | 原データ・図・サムネイル・生成物(§7)。**Redis と PG が消えても S3 の原データから再構築可能**な状態を保つ(docs/09 §2) |

## 3. 主要フローのシーケンス

### 3.1 拡張からの取り込み → ジョブ進行 → 通知

```mermaid
sequenceDiagram
    participant EXT as apps/extension (3a)
    participant API as apps/api
    participant PG as PostgreSQL
    participant RD as Redis (arq / PubSub)
    participant W as worker-bulk
    participant AX as arXiv
    participant S3 as S3
    participant WEB as apps/web

    EXT->>API: GET /api/ingest/check?url=... (書誌プレビュー+LaTeX有無+重複検知)
    API-->>EXT: {bibliography, latex_available: true, existing_library_item: null}
    EXT->>API: POST /api/ingest/arxiv {url, status, tags[], collection_id, quick_note}
    API->>PG: Paper upsert + LibraryItem INSERT + jobs INSERT(kind=ingest_paper, stage=queued)
    API->>RD: arq enqueue(alinea:bulk, job_id)
    API-->>EXT: 202 {paper_id, library_item_id, job_id} (保存後3秒以内にカード表示可)
    W->>PG: claim (status=running, stage=fetching)
    W->>AX: メタデータAPI → LaTeXソース > HTML > PDF の順に取得
    W->>S3: sources/{paper_id}/{source_version}/... 保存
    W->>PG: stage=parsing → structuring (DocumentRevision + Block + 図を figures/ へ)
    W->>RD: PUBLISH events:user:{uid} {type: job.progress}
    W->>PG: stage=translating_abstract (アブスト訳 + ✦3行要約)
    W->>PG: stage=readable + translate_section ジョブ群をセクション順で INSERT & enqueue(alinea:bulk)
    Note over W,PG: 全 translate_section 完了を検知した最後のジョブが
    W->>PG: ingest ジョブ stage=complete + notifications INSERT(kind=translation_complete)
    W->>RD: PUBLISH events:user:{uid} {type: notification.created}
    RD-->>API: Pub/Sub 受信
    API-->>WEB: SSE /api/events → ベル未読ドット + 「読み始める →」
    API-->>EXT: (拡張は 15 秒ポーリング GET /api/ingest/recent) → バッジ更新
```

- 拡張の進捗表示は**ポーリング(15,000ms 間隔)で確定**(決定)。理由: MV3 service worker は SSE の常時接続を維持できず(アイドル 30 秒で停止)、ポップアップ表示中のみ進捗が見えれば十分なため。ポップアップ開時は 2,000ms 間隔に短縮する。
- 状態機械は docs/02 §5.1 の逐語: `queued → fetching → parsing → structuring → translating_abstract → readable → translating_body → complete`、任意段階から `failed(stage, reason)`。この `stage` 値を `jobs.stage` にそのまま格納する。

### 3.2 翻訳ジョブ(ブロック単位・オンデマンド優先繰り上げ)

```mermaid
sequenceDiagram
    participant WEB as apps/web (ビューア)
    participant API as apps/api
    participant PG as PostgreSQL
    participant RD as Redis
    participant WI as worker-interactive
    participant WB as worker-bulk
    participant LLM as LLMProvider (translation ルート)

    Note over PG: readable 到達時に translate_section ジョブが<br/>セクション順で alinea:bulk に積まれている
    WEB->>API: PUT /api/library-items/{id}/position {block_id}(plans/03 §5.8)
    API->>PG: 該当セクションの translate_section ジョブを priority=interactive に UPDATE
    API->>RD: 同一 job_id を alinea:interactive に再 enqueue(繰り上げ)
    par 先着した worker が実行
        WI->>PG: claim(UPDATE jobs SET status=running WHERE id=? AND status=queued)
        WB->>PG: 後着側は claim 失敗 → 即 no-op 終了
    end
    loop セクション内の翻訳対象ブロック順
        WI->>PG: TranslationUnit を source_hash で照合(既訳ならスキップ = 冪等)
        WI->>LLM: ブロック+前後文脈+用語スナップショットで翻訳
        WI->>WI: プレースホルダ検証(不合格は最大2回再試行→原文フォールバック+quality_flags)
        WI->>PG: TranslationUnit UPSERT (state=machine)
        WI->>RD: PUBLISH {type: translation.unit_completed, block_ids, progress}
    end
    RD-->>API: Pub/Sub
    API-->>WEB: SSE → 「翻訳中…」ブロックを訳文に差し替え / 目次の進捗96%更新
```

- 翻訳の実行単位は **`translate_section` ジョブ(セクション単位)**、その内部処理単位はブロック(docs/03 §3)。セクション単位にする理由: ブロック単位ジョブでは 1 論文で数百ジョブになり arq/DB のオーバーヘッドが支配的になる一方、「開いたセクションを優先翻訳」(docs/02 §5.2)の繰り上げ粒度はセクションで十分なため。
- 繰り上げの実装: 同一 `jobs` 行を高優先キューへ**二重 enqueue** し、DB の claim(`status=queued → running` の条件付き UPDATE)で先着 1 実行を保証する。後着はノーオペ。二重実行が起きても `TranslationUnit.source_hash` 照合により結果は冪等。
- 直訳スタイル(オンデマンド生成)・付録のオンデマンド翻訳・「この表を翻訳」・再翻訳(`retranslate_blocks`)は常に `alinea:interactive` に投入する(ユーザーが画面の前で待つため)。

### 3.3 チャット(SSE ストリーミング)

**決定: WebSocket ではなく SSE(Server-Sent Events)を使う。** 理由: (1) 必要な通信は「サーバー→クライアントの逐次配信」のみで双方向性が不要(質問送信は通常の POST で足りる)。(2) セッションクッキー・CSRF ヘッダ・Caddy のリバースプロキシがそのまま使え、WebSocket 用の別認証・アップグレード設定・切断検知を持ち込まずに済む。(3) 単一 VPS 構成でコネクション状態の共有問題(スティッキーセッション)が発生しない。(4) 再接続は `Last-Event-ID` 準拠の自前カーソル(§5)で機械的に復旧できる。

チャットは「POST リクエストのレスポンスボディを `text/event-stream` で返す」方式で実装する(EventSource ではなく `fetch` + `ReadableStream` で読む。質問ペイロードを送る必要があるため)。

```mermaid
sequenceDiagram
    participant WEB as apps/web (サイドパネル 1a)
    participant API as apps/api
    participant PG as PostgreSQL
    participant LLM as LLMProvider (chat ルート)

    WEB->>API: POST /api/chat/threads/{thread_id}/messages<br/>{content, context_anchors[]} (Accept: text/event-stream)
    API->>PG: ユーザーメッセージ保存(白カード表示は楽観更新)
    API->>PG: 文脈構築: 構造化ドキュメント(超過時は要約+関連セクション全文+選択周辺全文)<br/>+ メタデータ + スレッド履歴 + 注釈・メモ(設定で除外可)
    API->>LLM: streaming 呼び出し(chat ルーティング設定、フォールバック連鎖)
    loop delta 到着ごと
        LLM-->>API: text delta
        API-->>WEB: event: message.delta {text}
    end
    API->>API: 根拠チップ検証(参照ブロックの実在確認。壊れた参照は除去 — docs/05 §5)
    API->>PG: アシスタントメッセージ確定保存(evidence_anchors[], 論文外の知識区分)
    API->>PG: usage_records INSERT(トークン数・コスト・使用モデル)
    API-->>WEB: event: message.completed {message_id, evidence_anchors[]}
```

- 初回トークン p50 5 秒(docs/09 §1)。LLM 呼び出しタイムアウトは初回トークンまで 30 秒・全体 180 秒。タイムアウト/レート制限/5xx でフォールバック連鎖の次プロバイダへ 1 回だけ切り替え、それも失敗したらエラーイベント `event: message.failed {reason}` を返してメッセージをエラー状態で保存する(勝手に消さない。P3)。
- 根拠チップは delta 中はプレーンテキストの `[[anchor:...]]` 記法で流し、`message.completed` 後にクライアントが検証済みアンカーでチップ描画する(検証前のチップを踏ませない — 決定)。

### 3.4 横断検索(1e ドロップダウン / 4e 全結果画面)

```mermaid
sequenceDiagram
    participant WEB as apps/web
    participant API as apps/api
    participant PG as PostgreSQL (PGroonga)

    WEB->>API: GET /api/search/preview?q=EMA+teacher (⌘K ドロップダウン。250ms デバウンス。plans/03 §15.2)
    API->>API: クエリ言語判定(CJK 文字を含む → ja / 含まない → en)
    API->>PG: block_search_index.source_text(原文)と translation_units.text_ja(訳文)ほか<br/>各資産テーブルを PGroonga &@~ で検索(plans/11 §3)
    PG-->>API: ヒット行(source_kind, block_id, anchor, スニペット位置)
    API->>API: 同一 (revision_id, block_id) の原文/訳文ヒットを FULL OUTER JOIN で 1 行に統合(docs/06 §9.2)
    API-->>WEB: 上位3件+総件数 → プレビュー表示
    WEB->>API: GET /api/search?q=...&source=chat&paper_id=...&cursor=...&limit=20 (4e 全結果。plans/03 §15.1)
    API-->>WEB: 論文単位グループ+ファセット件数+源別遷移先(anchor / thread_id / note_id / article)
```

- 検索専用の非正規化テーブル(`search_entries` 案)は**持たない**(決定 — plans/11 §1・R-4)。原文は Paper 共有の `block_search_index`(revision 単位。DDL は plans/02 §4.3)、訳文は `translation_units.text_ja`、メモ・注釈・チャット・記事・語彙は各資産テーブルを直接 PGroonga で検索し、ユーザースコープは `library_items` との JOIN で絞る。`block_search_index` の書き込みは取り込みジョブの structuring 完了時に revision 単位で DELETE→INSERT(`document_revisions` INSERT と同一トランザクション。DB トリガは使わない — アプリ層 1 箇所に集約)。
- PGroonga インデックス定義(トークナイザ・ノーマライザ・対象列の全量)は plans/11 §1〜§2 を正とする(例: 原文 `block_search_index.source_text` は `TokenBigram`+`TokenFilterStem` で英語ステミング対応)。
- 性能目標 p50 1 秒 / p95 3 秒(docs/09 §1)。

### 3.5 共有ページ(4c・匿名アクセス)

```mermaid
sequenceDiagram
    participant ANON as 匿名閲覧者
    participant CADDY as Caddy
    participant WEB as apps/web (RSC)
    participant API as apps/api
    participant PG as PostgreSQL

    ANON->>CADDY: GET https://alinea.app/c/x8Kf3qPw
    CADDY->>WEB: ルーティング(/c/* は Next.js)
    WEB->>API: GET http://api:8000/api/share/collections/x8Kf3qPw (認証なし)
    API->>PG: collections WHERE share_token=? AND share_status='active'
    Note over API: 返すのは 書誌+✦要約+include_notes が ON の許可メモのみ。<br/>訳文・注釈・チャット等の個人資産は一切含めない (docs/09 §4)
    API-->>WEB: 200 {title, description, owner_display, deadline, entries[](順序保持)}
    WEB-->>ANON: SSR HTML + X-Robots-Tag: noindex + meta robots noindex + OGP
    Note over ANON: 無効化済み/存在しない token は 404(存在を漏らさない)
```

- 公開エンドポイントは `/api/share/*` プレフィックス(plans/03 §14)に隔離し、認証ミドルウェアの対象外(`anonymous` 区分)を明示的に列挙する(決定。「認証必須が既定・公開は例外」を構造で保証)。
- トークンは 8 文字の Base58(`x8Kf3qPw` 形式、`secrets.choice` で生成、約 46bit)+ 失効管理(docs/06 §4.2)。レート制限: 同一 IP から `GET /api/share/collections/{token}` へ 120 req/min(plans/03 §1.8。§9.4)。

## 4. 非同期ジョブ基盤(arq + PostgreSQL ジョブテーブル)

### 4.1 役割分担(決定)

- **PostgreSQL `jobs` テーブルが唯一の真実**: 状態・段階・進捗・失敗理由・再試行はすべて DB。UI(拡張パイプライン表示・2a タイムライン・処理ログ)は DB を読む。
- **arq(Redis)は「起床通知」のみ**: enqueue は「この job_id を処理せよ」というシグナルに過ぎず、ペイロードは job_id のみ。Redis が消えても `jobs` の `status='queued'` 行を再 enqueue する回復コマンド(`python -m alinea_core.jobs.requeue`)で完全復旧できる。

### 4.2 `jobs` テーブル(本書のジョブ実行モデルが要求する論理列)

> 物理 DDL の所有権は plans/02 §4.13。現時点の plans/02 の `jobs` DDL には本節の `idempotency_key` / `checkpoint` / `next_retry_at` / `progress_percent`(plans/02 では `progress`)が未反映で、`kind` の列挙・ID 型(ULID/UUID)・`max_attempts` も差分がある。統合時は本節の実行モデル(claim・段階再開・冪等性キー — plans/13 M0-12 が前提とする)を plans/02 の DDL に追補する。

```sql
CREATE TYPE job_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');
CREATE TYPE job_priority AS ENUM ('interactive', 'bulk');

CREATE TABLE jobs (
    id              TEXT PRIMARY KEY,             -- ULID
    kind            TEXT NOT NULL,                -- §4.3 の列挙
    status          job_status NOT NULL DEFAULT 'queued',
    priority        job_priority NOT NULL DEFAULT 'bulk',
    stage           TEXT,                         -- kind ごとの段階名(ingest_paper は docs/02 §5.1 の逐語)
    progress_percent SMALLINT NOT NULL DEFAULT 0, -- 0..100。拡張・カードの「翻訳中 68%」の源泉
    payload         JSONB NOT NULL,               -- 入力(url, section_id 等)
    checkpoint      JSONB NOT NULL DEFAULT '{}',  -- 段階再開用(完了済み stage の出力参照)
    idempotency_key TEXT NOT NULL UNIQUE,         -- §4.4
    attempts        SMALLINT NOT NULL DEFAULT 0,
    max_attempts    SMALLINT NOT NULL DEFAULT 4,  -- 初回 + 自動リトライ3回 (docs/09 §2)
    next_retry_at   TIMESTAMPTZ,
    last_error      JSONB,                        -- {stage, code, message, provider}
    user_id         TEXT REFERENCES users(id),
    paper_id        TEXT REFERENCES papers(id),
    library_item_id TEXT REFERENCES library_items(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX idx_jobs_status_priority ON jobs (status, priority, created_at);
CREATE INDEX idx_jobs_library_item ON jobs (library_item_id, created_at DESC);
```

### 4.3 ジョブ種別(`kind` の全列挙)

| kind | キュー既定 | 段階(`stage` の遷移) | 内容 |
|---|---|---|---|
| `ingest_paper` | bulk | `queued→fetching→parsing→structuring→translating_abstract→readable→translating_body→complete`(docs/02 §5.1 逐語) | 取り込みパイプライン。`translating_body` 以降は子の `translate_section` 群の集計値を `progress_percent` に反映 |
| `translate_section` | bulk(繰り上げで interactive) | `queued→translating→complete` | セクション単位翻訳。§3.2 |
| `retranslate_blocks` | interactive | 同上 | 再翻訳・指示つき再翻訳・用語変更の影響ブロック再翻訳(上位モデルへエスカレーション — docs/03 §9) |
| `reingest_paper` | bulk | `ingest_paper` と同一 | 「再取り込み」(2a)・B→A 昇格。完了時に `reanchor_annotations` を連鎖投入 |
| `reanchor_annotations` | bulk | `queued→matching→complete` | 新 DocumentRevision への注釈リアンカー(失敗分は「未配置」) |
| `generate_article` | interactive | `queued→composing→figures→complete` | 記事モード生成・指示つき再生成(docs/07 §2) |
| `generate_overview_figure` | interactive | `queued→dsl→render→complete` | 概要図 DSL 生成→SVG 決定的レンダリング→`renders/` 保存 |
| `generate_explainer_figure` | interactive | `queued→prompting→generating→complete` | 解説図ラスター生成(ImageProvider) |
| `enrich_vocab_entry` | interactive | `queued→generating→complete` | 語彙保存時の AI 生成(語義・語源・解釈・コツ。p50 3 秒 — docs/09 §1) |
| `fetch_resource_metadata` | interactive | `queued→fetching→complete` | ResourceLink のタイトル・サムネ・種別メタ自動取得(docs/12) |
| `export_user_data` | bulk | `queued→collecting→packaging→complete` | JSON 一括/Markdown エクスポート生成 → `exports/` へ |
| `send_deadline_reminders` | bulk(cron) | — | arq cron で毎日 08:00 JST に締切リマインド通知を生成(4a の「今日 8:00」に一致) |

- ステータス変更提案(`status_suggestion` 通知)はジョブではなく、読書セッション記録 API(`POST /api/library-items/{id}/reading-sessions`、`client_session_id` による冪等 upsert — plans/03 §5.9。クライアント送信間隔 30 秒)内の同期判定で生成する(決定。3 分閾値の判定に遅延バッチは不要なため)。

### 4.4 冪等性キー(`idempotency_key` の生成規則)

| kind | キー書式 |
|---|---|
| `ingest_paper` / `reingest_paper` | `ingest:{paper_id}:{source_version}:{parser_version}:{attempt_epoch}`(再取り込みは新 epoch) |
| `translate_section` | `xlate:{translation_set_id}:{section_id}` |
| `retranslate_blocks` | `rexlate:{translation_set_id}:{blocks_hash}:{instruction_hash}` |
| `generate_article` | `article:{library_item_id}:v{version}` |
| `generate_overview_figure` | `ovfig:{article_id}:v{version}` |
| `generate_explainer_figure` | `exfig:{explainer_figure_id}:v{version}` |
| `enrich_vocab_entry` | `vocab:{vocab_entry_id}:v{generation}` |
| `fetch_resource_metadata` | `resource:{resource_link_id}` |
| `export_user_data` | `export:{export_id}` |

同一キーの再投入は既存行を返す(HTTP 200 + 既存 job_id)。**冪等性の二重防御**: キーで「同一ジョブの重複作成」を防ぎ、各ジョブ内部の UPSERT(`TranslationUnit.source_hash` 照合等)で「同一ジョブの二重実行」を無害化する。docs/09 §8 の「途中で強制終了→再実行しても二重処理・データ破損が起きない」はこの 2 層で満たす。

### 4.5 段階再開・リトライ・claim

- **claim**: `UPDATE jobs SET status='running', started_at=now(), attempts=attempts+1 WHERE id=$1 AND status IN ('queued') RETURNING *`。0 行なら即終了(重複起床・二重 enqueue 対策)。
- **段階再開**: 各 stage 完了時に `checkpoint` へ出力参照(例 `{"fetching": {"source_asset_id": "..."}}`)を書き込み `stage` を進める。リトライ時は `checkpoint` に記録済みの stage をスキップして途中から再開する。
- **リトライ**: 失敗時 `status='queued', next_retry_at = now() + interval` で指数バックオフ **30 秒 → 2 分 → 8 分**(自動 3 回。docs/09 §2)。`next_retry_at` は arq の `defer_until` で respect する。`attempts >= max_attempts` で `status='failed'` とし、UI に段階・理由・「再試行」ボタンを出す(手動再試行は attempts をリセットした新 epoch ジョブ)。
- **部分成功は正の状態**(docs/09 §2): 例. 図抽出失敗は `checkpoint.structuring.figure_errors[]` に記録して次 stage へ進み、処理ログ(2a)に明示する。ジョブ全体を fail させない。
- **進捗イベント**: stage 遷移・`progress_percent` 更新(翻訳はブロック 10 個ごと、または 5%刻みのいずれか粗い方)のたびに Redis Pub/Sub `events:user:{user_id}` へ発行する(§5)。

## 5. リアルタイム更新(SSE + ポーリングフォールバック)

**決定: 取り込み進捗・翻訳進捗・通知はユーザー単位の単一 SSE エンドポイントで配信し、SSE 不通時は TanStack Query のポーリングにフォールバックする。**

- エンドポイント: `GET /api/events`(認証必須、`text/event-stream`。ユーザー単位。plans/03 §21.2 のジョブ単位 SSE とは別に、plans/03 §23 の索引へ追補が必要)。EventSource で接続する。API は接続時に Redis Pub/Sub `events:user:{user_id}` を SUBSCRIBE し、受信メッセージをそのまま SSE フレームで流す。
- イベント型(`event:` フィールド):

| event | data(JSON) | 消費先 |
|---|---|---|
| `job.progress` | `{job_id, kind, stage, progress_percent, library_item_id}` | 1d 最近追加カード・2a タイムライン・拡張(※拡張はポーリング) |
| `job.failed` | `{job_id, kind, stage, error_code, library_item_id}` | 失敗表示+再試行ボタン |
| `translation.unit_completed` | `{library_item_id, translation_set_id, block_ids[], section_progress, total_progress}` | ビューアの部分読書(ブロック差し替え・目次 96% 更新) |
| `notification.created` | `{notification_id, kind, payload}` | ベル未読ドット(#C49432)+ポップオーバー |

- 各イベントに単調増加 `id:`(ULID)を付け、クライアントは再接続時に `Last-Event-ID` を送る。API は直近イベントを Redis Stream `events:log:{user_id}`(`MAXLEN ~1000`、TTL 1 時間)にも書き、取りこぼしを再送する。
- ハートビート: 15 秒ごとにコメント行 `: ping` を送出(Caddy のアイドルタイムアウト回避)。クライアントは 45 秒無受信で再接続。
- **ポーリングフォールバック(決定)**: EventSource が 3 回連続で接続失敗した場合、TanStack Query の `refetchInterval` を有効化して以下を回す — ビューア表示中の翻訳・ジョブ状態 `GET /api/library-items/{id}/jobs?active=true`(plans/03 §21.3)= 5,000ms、通知バッジ `GET /api/notifications`(応答の `unread` 件数。plans/03 §16.1)= 30,000ms、ダッシュボード進捗 `GET /api/dashboard`(plans/03 §5.12)= 10,000ms。SSE 復帰に成功したらポーリングを止める。SSE 接続中は `refetchInterval` を無効にし、イベント受信で `queryClient.invalidateQueries` を叩く(二重取得を避ける)。
- チャットのストリーミングは本エンドポイントを使わず、§3.3 の POST レスポンスストリームで行う(会話は要求と応答が 1:1 のため)。

## 6. セッション・認証フロー

### 6.1 認証方式(docs/00 Q3・docs/09 §4)

authlib の `OAuth` クライアントで **Google / GitHub OAuth 2.0(Authorization Code)** + **メールリンク(マジックリンク)**。パスワードは持たない(決定。保存・リセットフローの攻撃面を持ち込まない)。

```
GET  /api/auth/oauth/{provider}/start     # provider ∈ {google, github}。state+PKCE を発行しリダイレクト
GET  /api/auth/oauth/{provider}/callback  # code 交換 → users upsert → セッション発行 → / へ 302
POST /api/auth/email/request              # {email} → 署名付きトークン(有効 15 分・1 回限り)をメール送信
GET  /api/auth/email/verify?token=...     # 検証 → セッション発行 → / へ 302
POST /api/auth/logout                     # 現在セッションの失効(Origin 検証必須)
GET  /api/auth/me                         # 現在ユーザー(未認証は 401)
```

- 同一メールアドレスの OAuth / メールリンクは同一 `users` 行に統合する(email を一意キーとし、`auth_identities`(plans/02 §4.2)に provider 別 ID を持つ)。

### 6.2 セッション実装

- **サーバーサイドセッション(決定。JWT は使わない)**: 即時失効(ログアウト・アカウント削除)とセッション一覧管理を確実にするため。
- クッキー: 名前 `yk_session`、値は 256bit ランダム(base64url)。DB `sessions` テーブルには **SHA-256 ハッシュのみ**保存(DB 漏洩でセッション乗っ取り不可)。Redis に `session:{hash} → user_id` を TTL 300 秒でキャッシュ。
- クッキー属性(確定値):

```
Set-Cookie: yk_session=<token>; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000
```

  `Max-Age` 30 日のローリング更新(残り 15 日を切ったアクセスで延長)。`Domain` 属性は付けない(host-only。web と api が同一オリジンのため — §8.2)。dev(http://localhost)では `Secure` を外す。
- CSRF: **Origin ヘッダ検証方式(決定 — plans/03 §1.3 と同一。専用 CSRF トークン・`yk_csrf` クッキーは導入しない)**。非 GET リクエスト(POST/PUT/PATCH/DELETE)の `Origin` を許可リストと照合する — 許可: `https://alinea.app`、dev では `http://localhost:3000`、拡張発は `chrome-extension://{EXTENSION_ID}`(環境変数 `EXTENSION_ALLOWED_ORIGINS`、plans/10 §15-2)。不一致は 403 `origin_mismatch`。`anonymous` 区分(`/api/share/*`・OAuth コールバック・メールリンク検証)は対象外。

### 6.3 BYOK キーの保管(docs/09 §4)

- ユーザーの API キーは `byok_api_keys` テーブル(plans/02 §4.2)に **Fernet で暗号化**して保存(マスタキーは環境変数 `ALINEA_KEY_ENCRYPTION_SECRET`、44文字 urlsafe base64。カンマ区切り複数指定+MultiFernet でローテーション。plans/04 §11.2 が暗号化方式の正)。画面は末尾 4 文字(`key_hint`)のみのマスク表示・再表示 API なし(再入力のみ)。

### 6.4 拡張からの認証(同一クッキー)

- 拡張は `host_permissions: ["https://alinea.app/*"]` を宣言し、`fetch(…, { credentials: "include" })` で API を呼ぶ。Chromium はホスト権限を持つ拡張コンテキスト発のリクエストを same-site 扱いにするため、`SameSite=Lax` のままセッションクッキーが送信される。**v1 の拡張はクッキー共有のみを使う(決定)**。API 側には将来クッキーが使えない環境(Safari 等)向けのフォールバックとして拡張トークン `POST /api/auth/extension-token`(`Authorization: Bearer yk_ext_…`、スコープは plans/03 §1.2.1 に固定)を用意するが、v1 の拡張実装はこれを使用しない(plans/10 §4)。
- 401 応答時、ポップアップは「ログインしてください」状態を表示し、「ログイン」ボタンで `https://alinea.app/login?from=extension` を新規タブで開く。ログイン完了後にポップアップを開き直せばクッキーが有効になっている(コールバック連携は実装しない — 最小権限)。
- 拡張の状態変更リクエストは §6.2 の Origin 検証で保護する。拡張発リクエストの `Origin` は `chrome-extension://{EXTENSION_ID}` になるため、Chrome/Edge 各ストア配布 ID を `EXTENSION_ALLOWED_ORIGINS` に登録して許可する(dev では `chrome-extension://` スキームを一律許可 — plans/10 §15-2)。CSRF トークンの取得・保持は不要。

## 7. ストレージレイアウト(S3 キー設計)

### 7.1 バケットとキー

バケットは 1 つ: dev=`alinea`(MinIO)、prod=`alinea-prod`(R2)。**全オブジェクト非公開**(パブリックアクセス無効)。ID はすべて ULID。

```
sources/{paper_id}/{source_version}/latex.tar.gz        # arXiv LaTeX ソース(取得時のまま)
sources/{paper_id}/{source_version}/arxiv.html          # arXiv HTML フォールバック
sources/{paper_id}/{source_version}/original.pdf        # arXiv PDF / タブ内送信 PDF
sources/{paper_id}/{source_version}/metadata.json       # メタデータ API 応答スナップショット
figures/{paper_id}/{revision_id}/{block_id}.png         # 抽出図・表レンダリング(原論文由来)
figures/{paper_id}/{revision_id}/{block_id}.svg         # LaTeX 由来のベクタ図(取得できた場合)
thumbnails/{paper_id}/card.webp                          # ライブラリカード用 480×360px
thumbnails/{paper_id}/card@2x.webp                       # 960×720px
renders/overview/{article_id}/v{version}.svg             # 全体概要図(版ごとに不変オブジェクト)
renders/explainer/{explainer_figure_id}/v{version}.png   # 解説図ラスター(版ごとに不変)
exports/{user_id}/{export_id}.zip                        # エクスポート成果物(生成後 7 日で削除)
```

- `source_version` は arXiv バージョン(`v3`)、アップロード PDF は `v1` 固定。
- **不変キー原則(決定)**: 一度書いたオブジェクトは上書きしない(概要図・解説図は版番号で別キー)。版管理(「AI生成 · 版 2」・前の版に戻す — docs/07 §1.2)と CDN/ブラウザキャッシュの整合をキーだけで保証する。
- SourceAsset は破棄しない(docs/01・09。再パース・B→A 昇格の原資)。`exports/` のみライフサイクルルール(7 日)で自動削除。

### 7.2 メタデータ

各オブジェクトに S3 メタデータ `x-amz-meta-paper-visibility: public|private` を付与し、監査時に DB なしで判別可能にする(アクセス制御自体は §7.3 の API 判定が正)。

### 7.3 配信方式(決定)

- 図・サムネイル・PDF・SVG はすべて **API が権限判定して発行する署名付き GET URL(有効期限 600 秒 = 10 分。plans/03 §22.1)** で配信する: `GET /api/assets/{asset_id}` → **302 リダイレクト**(JSON では返さない — 決定。`<img src>` から直接使えるため)。private 論文(アップロード PDF 由来)は所有者のみ、public 論文の図は当該論文を LibraryItem に持つユーザーのみ、共有ページのサムネイルは共有トークン検証経由のみ。
- dev の MinIO は `S3_PUBLIC_ENDPOINT_URL`(ブラウザから到達可能な `http://localhost:9000`)で署名する(コンテナ内 endpoint と分離)。

## 8. 環境とデプロイ

### 8.1 環境一覧

| 環境 | 構成 | 用途 |
|---|---|---|
| dev | `docker compose up`(postgres+pgroonga / redis / minio)+ ホスト実行の `pnpm dev` / `uvicorn --reload` / `arq --watch` | 開発。シードデータは Rectified Flow (arXiv:2209.03003) |
| prod | 単一 VPS(目安: 4 vCPU / 8GB RAM / 160GB SSD)+ Docker Compose + R2 | Q1 暫定(個人開発ホスト型・クローズドβ→公開)の小規模構成 |

ステージング環境は置かない(決定。Q1 規模では prod と同一 compose をローカルで再現できることをもって代替。マイグレーションは deploy 前に dev で `alembic upgrade head` 検証)。

### 8.2 ドメインとルーティング(決定: 単一オリジン)

`https://alinea.app` の単一オリジンに web と api を同居させる。理由: クッキーが host-only で済み、CORS 設定・`SameSite` 例外・プリフライトが不要になり、拡張の `host_permissions` も 1 つで済む。

```caddyfile
alinea.app {
    encode zstd gzip
    @api path /api/*
    handle @api {
        reverse_proxy api:8000 {
            flush_interval -1        # SSE 即時フラッシュ
        }
    }
    handle {
        reverse_proxy web:3000
    }
}

grafana.alinea.app {
    basicauth {
        admin <bcrypt ハッシュ>      # 管理者のみ(§8.3)。Grafana 自身のログインと二段
    }
    reverse_proxy grafana:3000
}
```

### 8.3 Docker Compose(prod のサービス構成)

```yaml
services:
  caddy:    { image: caddy:2, ports: ["80:80", "443:443"] }
  web:      { build: apps/web,   command: node server.js }            # next build --output standalone
  api:      { build: apps/api,   command: uvicorn alinea_api.main:app --host 0.0.0.0 --port 8000 --workers 2 }
  worker-interactive: { build: apps/worker, command: arq alinea_worker.settings.InteractiveWorker }
  worker-bulk:        { build: apps/worker, command: arq alinea_worker.settings.BulkWorker }
  postgres: { image: groonga/pgroonga:4.0.1-alpine-16, volumes: [pgdata:/var/lib/postgresql/data] }
  redis:    { image: redis:7-alpine, command: redis-server --appendonly yes }
  prometheus: { image: prom/prometheus:v2.53.0 }
  grafana:    { image: grafana/grafana:11.1.0 }   # 管理者のみ。basic auth + 別サブドメイン grafana.alinea.app
```

- prod のオブジェクトストレージは R2(コンテナ外部)。MinIO は dev のみ。
- バックアップ: `pg_dump` を毎日 03:00 JST に取得し R2 `backups/pg/{date}.dump.zst` へ(保持 30 日)。Redis は AOF(消失しても §4.1 の再 enqueue で復旧可)。S3/R2 は R2 側の冗長性に委ねる。
- デプロイ: GitHub Actions → GHCR にイメージ push → VPS 上で `docker compose pull && docker compose up -d`。DB マイグレーションは api 起動前の one-shot コンテナ `alembic upgrade head`。web/api は先に新コンテナを起動してから旧を落とす(compose の `update_config` 相当を script で実施。SSE 切断はクライアント自動再接続で吸収)。

### 8.4 環境変数(全列挙)

| 変数名 | 例(dev) | 使用箇所 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://alinea:***@localhost:5432/alinea` | api / worker |
| `REDIS_URL` | `redis://localhost:6379/0` | api / worker |
| `S3_ENDPOINT_URL` | `http://minio:9000`(prod: R2 endpoint) | api / worker |
| `S3_PUBLIC_ENDPOINT_URL` | `http://localhost:9000`(prod: 未設定=ENDPOINT と同一) | api |
| `S3_BUCKET` | `alinea` | api / worker |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | — | api / worker |
| `SESSION_SECRET` | 64byte hex(state/メールリンク署名) | api |
| `ALINEA_KEY_ENCRYPTION_SECRET` | Fernet マスタキー(44文字 urlsafe base64、カンマ区切りでローテーション) | api / worker |
| `OAUTH_GOOGLE_CLIENT_ID` / `OAUTH_GOOGLE_CLIENT_SECRET` | — | api |
| `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_GITHUB_CLIENT_SECRET` | — | api |
| `SMTP_URL` | `smtp://user:pass@smtp.example.com:587` | api |
| `APP_BASE_URL` | `http://localhost:3000`(prod: `https://alinea.app`) | api(リダイレクト・メールリンク) |
| `API_INTERNAL_URL` | `http://localhost:8000`(prod: `http://api:8000`) | web(SSR fetch) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `DEEPSEEK_API_KEY` / `XAI_API_KEY` | 運営キー | api / worker |
| `SENTRY_DSN_API` / `SENTRY_DSN_WEB` | — | api+worker / web |
| `ARXIV_USER_AGENT` | `alinea/1.0 (contact@alinea.app)` | worker(arXiv 規約遵守 — docs/09 §5.3) |
| `EXTENSION_ALLOWED_ORIGINS` | dev: 未設定(`chrome-extension://` を一律許可)。prod: `chrome-extension://<ChromeストアID>,chrome-extension://<EdgeストアID>` | api(Origin 検証 §6.2 — plans/10 §15-2) |

モデル ID・ルーティング・フォールバック連鎖は環境変数ではなく **DB の設定テーブル `llm_route_configs`** に持つ(docs/09 §3.2「再デプロイなしで変更できる」。既定値シードは 09 §3.4 の表: 翻訳=`deepseek-v4-flash`→`gemini-3.5-flash`、チャット/要約/記事=`claude-opus-4-8`→`gpt-5.5`、語彙=`claude-haiku-4-5`→`gpt-5.4-mini`、概要図 DSL=チャットと同一、解説図=`gemini-3.1-flash-image`→`grok-imagine-image`→`gpt-image-2`)。

## 9. エラーハンドリング・ロギング・監視

### 9.1 API エラー形式(決定: RFC 9457 Problem Details — plans/03 §1.4 の RFC 7807 形式と同一。9457 は 7807 の後継)

全エラーレスポンスは `application/problem+json`。`type` は `https://alinea.app/problems/{code のケバブケース}` から決定的に導出する(plans/03 §1.4):

```json
{
  "type": "https://alinea.app/problems/quota-exceeded",
  "title": "月次クォータを超過しています",
  "status": 429,
  "code": "quota_exceeded",
  "detail": "全文翻訳の今月の残りは 0 本です。BYOK キーを設定すると制限なく利用できます。",
  "request_id": "01J1XABC..."
}
```

`code` は機械判定用の安定識別子。共通コードの全列挙は plans/03 §1.4 の表を正とする(例: `validation_error` / `unauthorized` / `origin_mismatch` / `not_found` / `duplicate` / `quota_exceeded` / `provider_error` / `rate_limited`。エンドポイント固有コードは plans/03 の各節)。フロントは `code` で分岐し、`title`/`detail` は日本語のままトースト・インライン表示に使う。

### 9.2 失敗の可視化(P3 との接続)

- ジョブ失敗は `jobs.last_error` に `{stage, code, message, provider}` を構造化保存し、UI の「処理ログ」(2a)・拡張の失敗表示・再試行ボタンの表示源とする。**ユーザーに見せる失敗文言は必ず段階名+理由+再試行手段の 3 点セット**(docs/02 §5.3)。
- LLM フォールバック発生は `usage_records.fallback_rank`(0=primary、1 以上=フォールバック)と、ジョブ処理ログの `provider_fallback` イベント(`{from, to, error_kind}` — plans/04 §9-4)として記録し、処理ログから使用モデルを判別できるようにする(docs/09 §3.5「黙って品質を落とさない」)。
- 翻訳のプレースホルダ不合格は例外ではなく正常系の分岐(原文フォールバック+`quality_flags`)。エラー監視のノイズにしない。

### 9.3 ロギング

- **構造化 JSON ログ(structlog / pino 相当)を stdout へ**。Docker の json-file ドライバ(`max-size: 50m, max-file: 5`)でローテーション。ログ集約基盤(Loki 等)は置かない(決定。Q1 規模では `docker logs` + Sentry で十分)。
- 必須フィールド: `timestamp, level, service(api|worker-interactive|worker-bulk|web), request_id, user_id, job_id, event, duration_ms`。`request_id` はミドルウェアで生成しレスポンスヘッダ `X-Request-ID` にも返す。worker は `job_id` をログコンテキストにバインドする。
- **本文・訳文・メモ・チャット内容・API キーをログに書かない**(プライバシー — docs/09 §4)。LLM 呼び出しはトークン数・モデル・所要時間のみ記録。
- エラートラッキング: Sentry(SaaS)を api / worker / web の 3 面に導入。`traces_sample_rate=0.1`。

### 9.4 監視・メトリクス

- FastAPI に `prometheus-fastapi-instrumentator`、worker はジョブ完了ごとにカスタムメトリクスを push gateway なしで `/metrics` エクスポート(ポート確定: worker-interactive=9101、worker-bulk=9102。api は 8000 の `/metrics`)。Prometheus(保持 30 日)+ Grafana ダッシュボードで docs/09 §1 の p50/p95 を常時確認できるようにする(09 §8 受け入れ基準)。
- 主要メトリクス(命名確定):

| メトリクス | 型 | ラベル | 対応する目標 |
|---|---|---|---|
| `http_request_duration_seconds` | histogram | `path, method, status` | ライブラリ 1s/3s・検索 1s/3s ほか |
| `job_duration_seconds` | histogram | `kind, stage` | readable 60s/3min・全文翻訳 5min/15min |
| `job_queue_depth` | gauge | `queue` | 詰まり検知 |
| `chat_first_token_seconds` | histogram | `provider, model` | 初回トークン 5s/15s |
| `llm_tokens_total` / `llm_cost_usd_total` | counter | `provider, model, purpose` | コスト/論文の実測(docs/10 §6) |
| `llm_fallback_total` | counter | `purpose, from_provider, to_provider` | フォールバック頻度 |
| `translation_placeholder_failures_total` | counter | `model` | 検証通過率 ≥99.9% |
| `sse_connections` | gauge | — | 接続数監視 |
| `ingest_quality_total` | counter | `level(A\|B), source_kind` | 品質 A 率 ≥99% |

- 死活監視: `GET /api/healthz`(プロセス生存)/ `GET /api/readyz`(PG・Redis・S3 疎通)。外形監視は UptimeRobot で `https://alinea.app/api/healthz` を 5 分間隔。アラートは Grafana Alerting → メール(閾値: p95 目標の 2 倍超が 15 分継続、`job_queue_depth > 100` が 10 分継続、5xx 率 > 1%)。
- レート制限(Redis 固定ウィンドウ、超過は 429 `rate_limited`。全量は plans/03 §1.8 を正とする): メールリンク要求 5 回/10分/IP+メール、OAuth 開始 20 回/10分/IP、取り込み 30 回/時/user、チャット送信 20 回/分/user、検索 60 回/分/user、共有ページ 120 回/分/IP、その他 600 回/分/user。

---

## 付記: 本書で新たに確定した実装決定の一覧

1. BFF なし・ブラウザから OpenAPI 生成クライアントで API 直叩き。SSR は共有ページ 4c のみ(OGP は静的画像、動的 OG 画像は作らない)。
2. チャット・リアルタイム更新は WebSocket ではなく SSE(チャット=POST レスポンスストリーム、進捗/通知=`GET /api/events`+Redis Pub/Sub)。フォールバックは TanStack Query ポーリング(翻訳状態 5,000ms / 通知 30,000ms)。拡張はポーリングのみ(15,000ms、ポップアップ表示中 2,000ms)。
3. ジョブ基盤は「PostgreSQL `jobs` テーブルが真実、arq は起床通知のみ」。キューは `alinea:interactive` / `alinea:bulk` の 2 本+worker 2 プロセス。優先繰り上げは同一 job_id の二重 enqueue+DB claim。冪等性キー書式と指数バックオフ(30s/2min/8min×3 回)を確定。
4. 翻訳ジョブの実行単位はセクション(`translate_section`)、内部処理単位はブロック。
5. 単一オリジン `alinea.app`(Caddy が `/api/*` を FastAPI へ)。セッションは DB 保存(ハッシュ化)+`yk_session` クッキー(HttpOnly/Secure/SameSite=Lax/30 日ローリング)、CSRF は Origin ヘッダ検証(plans/03 §1.3。トークンなし)。拡張は host_permissions による same-site 扱いで同一クッキーを使用、拡張 Origin は `EXTENSION_ALLOWED_ORIGINS` で許可。
6. パスワード認証は持たない(OAuth+メールリンクのみ)。BYOK は AES-256-GCM 暗号化。
7. S3 キーレイアウト(sources/figures/thumbnails/renders/exports)と不変キー原則、配信は `GET /api/assets/{asset_id}` の 302+署名付き URL(600 秒)。
8. prod は単一 VPS(4 vCPU/8GB)+Docker Compose、ステージングなし、R2+日次 pg_dump(30 日保持)。モデルルーティングは DB 設定テーブル `llm_route_configs`。
9. エラーは RFC 9457 Problem Details+安定 `code`。ログは構造化 JSON→stdout(集約基盤なし)+Sentry。メトリクスは Prometheus+Grafana(メトリクス名を確定)。
10. 検索は非正規化テーブルを持たず、`block_search_index`(原文・Paper 共有)+各資産テーブルを直接 PGroonga 検索(plans/11 が正。同期はアプリ層、DB トリガ不使用)。
11. ステータス変更提案は読書ハートビート API 内の同期判定(ジョブ化しない)。締切リマインドは arq cron 毎日 08:00 JST。
