# 03. REST API 完全仕様(apps/api = FastAPI)

> **対象読者と前提**: 本書は「Alinea」のバックエンド(apps/api: Python 3.12 + FastAPI + SQLAlchemy 2 + Pydantic v2)と、その消費者(apps/web / apps/extension / packages/api-client)の実装者向け。機能仕様は docs/00〜12 を正とし、本書は HTTP インターフェースの完全定義(パス・スキーマ・エラー・SSE イベント形式)を確定させる。エンティティ名・列挙値は docs/01(ドメインモデル)と一致させ、DB スキーマは plans/02、LLM 抽象化層は plans/04、取り込みパイプライン(ジョブ実装)は plans/05、翻訳パイプラインは plans/06 に委譲する(ジョブ基盤・キュー構成は plans/01)。本書に列挙のないエンドポイントは v1 に存在しない。

## 1. 共通仕様

### 1.1 ベース URL・バージョニング

- すべてのパスは `/api` プレフィックス配下。**URL バージョニングは行わない**(決定: v1 はクライアントを全て自社管理(web/extension)しており、OpenAPI 生成クライアントで同期デプロイするため。破壊的変更が必要になった場合はエンドポイント追加+旧パス据え置きで移行する)。
- 本番: `https://alinea.app/api/*`(apps/web の Next.js から同一オリジン。`next.config.ts` の `rewrites` で FastAPI へプロキシ)。拡張機能は `https://alinea.app/api/*` を直接呼ぶ。
- 開発: web=`http://localhost:3000` → rewrite → api=`http://localhost:8000`。
- 共有ページの公開 URL `https://alinea.app/c/{token}` は apps/web のページであり、データは §14 の匿名 API から取得する。
- 全レスポンスに `X-Request-Id`(ULID)ヘッダを付与する(処理ログ・障害調査用)。

### 1.2 認証区分

各エンドポイントは次の 3 区分のいずれか。区分は各エンドポイント定義に明記する。

| 区分 | 意味 | 実装 |
|---|---|---|
| `session` | ログイン必須 | HTTPOnly セッションクッキー `yk_session`。無効なら **401** `unauthorized` |
| `session\|ext` | セッションまたは拡張トークン | クッキー、または `Authorization: Bearer yk_ext_…`。拡張トークンで有効なのは §1.2.1 のスコープのみ(他エンドポイントに使うと **403** `token_scope_exceeded`) |
| `anonymous` | 認証不要 | 共有ページ・OAuth コールバック・メールリンク検証のみ |

#### 1.2.1 拡張トークンのスコープ(固定)

`GET /api/ingest/check` / `POST /api/ingest/arxiv` / `POST /api/ingest/pdf` / `GET /api/ingest/recent` / `GET /api/auth/me` / `PATCH /api/library-items/{id}` / `GET /api/jobs/{id}`。
拡張は第一候補としてセッションクッキー共有(`host_permissions: https://alinea.app/*` + `credentials: "include"`)を使い、Safari 等でクッキーが使えない将来環境のフォールバックとして拡張トークンを使う(docs/08 §1)。

### 1.3 セッションと CSRF

- クッキー: `yk_session`。属性 `HttpOnly; Secure; SameSite=Lax; Path=/`。値は 256bit ランダムのセッション ID。実体は Redis `session:{sid}`(JSON: `user_id`, `created_at`, `last_seen_at`)。有効期限 **30 日スライディング**(アクセスごとに延長)。
- CSRF 対策(決定): SameSite=Lax に加え、**非 GET リクエストで `Origin` ヘッダを検証**する(許可: `https://alinea.app`、開発時 `http://localhost:3000`)。加えて、拡張ページ発のリクエストの `Origin` は `chrome-extension://{EXTENSION_ID}` になるため、環境変数 **`EXTENSION_ALLOWED_ORIGINS`**(カンマ区切り。Chrome/Edge 各ストアの配布 ID を `chrome-extension://{配布ID}` 形式で登録 — Edge も Chromium のためスキームは同じ)の値を許可 Origin リストに追加する。`APP_ENV=development` では `chrome-extension://` スキームを一律許可する(plans/10 §15-2)。`Authorization: Bearer` 認証時は Origin 検証を免除(トークン自体が CSRF 不能なため)。専用 CSRF トークンは導入しない(Cookie+Origin 検証で十分であり、SSE・multipart を含む全経路で実装が単純になるため)。
- ログインセッションは 1 ユーザー複数可(デバイスごと)。`POST /api/auth/logout` は現在セッションのみ破棄。

### 1.4 エラーフォーマット(RFC 7807 準拠)

**決定**: エラーは全エンドポイント共通で RFC 7807 `application/problem+json` とし、独自拡張フィールド `code`(機械判定用スネークケース)と `errors[]`(バリデーション詳細)を追加する。

```json
{
  "type": "https://alinea.app/problems/quota-exceeded",
  "title": "月間クォータを超過しました",
  "status": 429,
  "detail": "チャットメッセージの月間クォータ(500件)を使い切りました。設定画面で自分の API キー(BYOK)を登録すると制限なく利用できます。",
  "instance": "/api/chat/threads/th_01JZK3/messages",
  "code": "quota_exceeded",
  "errors": []
}
```

- `type` は `https://alinea.app/problems/{code のケバブケース}` で決定的に導出。`errors[]` は 422 のときのみ `[{ "field": "body.deadline", "message": "日付形式(YYYY-MM-DD)ではありません" }]` 形式。
- FastAPI 実装: `RequestValidationError` / `HTTPException` を exception handler で本形式に変換する。Pydantic の生エラーをそのまま返さない。

#### 共通エラーコード表

| HTTP | code | 意味 |
|---|---|---|
| 400 | `bad_request` | 構文不正(JSON 破損等) |
| 401 | `unauthorized` | 未ログイン・セッション失効 |
| 403 | `forbidden` | 他ユーザーの資産・private 論文へのアクセス |
| 403 | `token_scope_exceeded` | 拡張トークンでスコープ外 API を呼んだ |
| 403 | `origin_mismatch` | Origin 検証失敗 |
| 404 | `not_found` | リソース不存在(ID 不正含む) |
| 409 | `duplicate` | 一意制約違反(重複保存・重複語彙・重複 URL) |
| 409 | `conflict` | 状態競合(発行済み共有リンクの再発行等) |
| 413 | `payload_too_large` | アップロード上限超過(PDF 50MB) |
| 415 | `unsupported_media_type` | PDF 以外のアップロード等 |
| 422 | `validation_error` | スキーマ・値域違反 |
| 429 | `rate_limited` | §1.8 のレート制限超過。`Retry-After` 付与 |
| 429 | `quota_exceeded` | 月間クォータ超過(§17.4) |
| 502 | `provider_error` | LLM/画像/外部 API の失敗(フォールバック連鎖も全滅) |
| 503 | `service_unavailable` | メンテナンス・過負荷 |

各エンドポイントの定義では上記以外の固有エラーのみ記載する。

### 1.5 ページング(cursor 方式に決定)

**決定**: 一覧系はすべて **cursor 方式**(offset は採用しない。追加・削除で行がずれるライブラリ一覧・通知・チャット履歴で安定し、PGroonga 検索とも相性がよいため)。

- リクエスト: `?cursor={opaque}&limit={n}`。`cursor` 省略時は先頭ページ。`limit` 既定値・最大値はエンドポイントごとに明記(既定 50 / 最大 100 が基本)。
- カーソル実体: `base64url(JSON: {"k": <ソートキー値>, "id": "<tiebreaker ID>"})`。クライアントには不透明文字列として扱わせる(OpenAPI 上も `string`)。
- レスポンス封筒:

```json
{ "items": [], "next_cursor": "eyJrIjoi...", "total": 41 }
```

- `next_cursor` は次ページが無ければ `null`。`total` は件数表示が仕様にある一覧(ライブラリ・検索・通知)のみ返す(それ以外は省略)。

### 1.6 ID・日時・列挙値の規約

- **ID**: ULID + 種別プレフィックス。`usr_` User / `pap_` Paper / `rev_` DocumentRevision / `li_` LibraryItem / `ts_` TranslationSet / `tu_` TranslationUnit / `ann_` Annotation / `note_` Note / `th_` ChatThread / `msg_` ChatMessage / `vcb_` VocabEntry / `res_` ResourceLink / `col_` Collection / `ce_` CollectionEntry / `sf_` SavedFilter / `ntf_` Notification / `term_` GlossaryTerm / `art_` Article / `ovf_` OverviewFigure / `exf_` ExplainerFigure / `job_` Job / `ast_` Asset。例外: ブロック ID はドキュメント内安定 ID(`blk-3-p2-a1f9`、docs/01 §4.3)、セクション ID は `sec-3` 形式、記事ブロック ID は `ablk_` + ULID(§19.1)。
- **日時**: ISO 8601 UTC(`2026-07-06T12:04:00Z`)。日付のみは `YYYY-MM-DD`(締切・読了日)。相対表示(「昨日 21:52」)はクライアント側整形。
- **列挙値**(API 全体で共通。日本語ラベルとの対応):

| 列挙 | 値 |
|---|---|
| ステータス `Status` | `planned`(読む予定) / `up_next`(すぐ読む) / `reading`(読んでいる) / `done`(読んだ) / `reread`(あとで再読) / `on_hold`(保留) |
| クイックフィルタ `Quick` | `all` / `unread`(=planned+up_next) / `in_progress`(=reading+on_hold) / `done`(=done) / `recheck`(=reread) |
| 優先度 `Priority` | `high` / `mid` / `low` |
| 重要度 `Importance` | `low` / `mid` / `high` |
| 品質 `Quality` | `A` / `B` |
| 翻訳スタイル `Style` | `natural` / `literal` |
| 注釈色 `AnnColor` | `important`(#C49432) / `question`(#5884AA) / `idea`(#659471) / `term`(#82827E) |
| 語彙種別 `VocabKind` | `word` / `collocation` / `idiom` |
| リソース種別 `ResKind` | `github` / `youtube` / `slides` / `article` |
| 通知種別 `NtfKind` | `translation_complete` / `status_suggestion` / `deadline_reminder` |
| 記事プリセット `Preset` | `beginner` / `implementer` / `researcher` / `reading_group` |
| SRS 評価 `ReviewResult` | `again`(まだあやしい) / `good`(✓ 覚えた) |
| ライセンス `License` | `cc-by-4.0` / `cc-by-sa-4.0` / `cc-by-nc-4.0` / `cc-by-nc-sa-4.0` / `cc-by-nd-4.0` / `cc0` / `arxiv-nonexclusive` / `unknown` |

### 1.7 共通オブジェクトスキーマ

以降のエンドポイント定義から参照する型。TypeScript 記法(生成 TS クライアントの型と一致させる)。

```ts
type Anchor = {
  revision_id: string;              // rev_…
  block_id: string;                 // blk-…
  start: number | null;             // ブロック内文字オフセット(全体参照は null)
  end: number | null;
  quote: string | null;             // 選択スナップショット(最大500字)
  side: "source" | "translation";
};
type AnchorRef = Anchor & {
  display: string;                  // サーバー導出の短縮表記 "§2.2 ¶3" / "式(5)" / "図2" / "表1"
};

type PaperBib = {
  id: string;                       // pap_…
  title: string;
  authors: string[];
  authors_short: string;            // "Liu, Gong, Liu"
  venue: string | null;             // "ICLR 2023"
  year: number | null;
  arxiv_id: string | null;          // "2209.03003"(バージョン抜き)
  arxiv_version: string | null;     // "v3"
  doi: string | null;
  license: License;
  visibility: "public" | "private";
  abstract: string;
};

type LastPosition = {
  revision_id: string;
  block_id: string;
  mode: "translation" | "parallel" | "source" | "pdf" | "article";
  section_display: string;          // "§2.1 整流フロー"
  saved_at: string;
};

type PipelineState = {              // 取り込み・翻訳の進行状態(1d/3a のカード表示用)
  job_id: string;
  stage: "queued" | "fetching" | "parsing" | "structuring"
       | "translating_abstract" | "readable" | "translating_body"
       | "complete";                // DB jobs.stage(kind=ingest)の 8 値と同一(plans/02 §4.13)
  status: "queued" | "running" | "waiting_quota" | "succeeded" | "failed";  // Job.status と同値
  progress_pct: number;             // 0–100(translating_body 中は翻訳済ブロック比)
  readable_upto: string | null;     // "§3"(部分読書可能範囲。readable 以降)
  failed_reason: string | null;     // 非 null = 失敗(status="failed")
};
// 写像規則(決定): stage は DB jobs.stage(kind=ingest)の 8 値のみを取る。「失敗」「クォータ待ち」は
// stage ではなく status で表す — 失敗は status="failed"(+failed_reason 非 null。stage は失敗時点の
// 到達段階を保持)、クォータ待ちは status="waiting_quota"(stage は "translating_body" のまま)。

type LibraryItemSummary = {
  id: string;                       // li_…
  paper: PaperBib;
  status: Status;
  priority: Priority | null;
  deadline: string | null;          // "2026-07-16"
  tags: string[];
  suggested_tags: string[];         // 承認式提案(docs/02 §7)
  quality_level: Quality;
  source: "arxiv" | "upload";       // upload = 「PDF 取り込み」バッジ
  progress_pct: number;             // 読書位置由来の導出値(docs/01)
  comprehension: number | null;     // 1–5
  importance: Importance | null;
  reading_seconds_total: number;    // ReadingSession 集計
  one_line_note: string | null;
  summary_3line: string[] | null;   // ✦3行要約(要素3、①②③は表示側)
  thumbnail_url: string | null;
  pipeline: PipelineState | null;   // 処理完了後は null
  last_position: LastPosition | null;
  added_at: string;
  updated_at: string;
  finished_at: string | null;       // 読了日(status→done で自動記録)
};

type Job = {
  id: string;                       // job_…
  kind: "ingest" | "reingest" | "translation_set" | "section_translate"
      | "retranslate_unit" | "glossary_apply" | "article_generate"
      | "article_block_rewrite" | "overview_figure" | "explainer_figure"
      | "vocab_generate" | "export_full" | "account_delete";
  status: "queued" | "running" | "waiting_quota" | "succeeded" | "failed";
  stage: string | null;             // kind=ingest のとき PipelineState.stage と同値
  progress_pct: number;
  detail: string | null;            // 「§3 まで読めます · 開いたセクションを優先翻訳」等
  error: Problem | null;            // RFC7807 オブジェクト
  library_item_id: string | null;
  result: object | null;            // kind 固有(export_full: {download_url}, retranslate_unit: {unit_id} 等)
  created_at: string;
  updated_at: string;
};

type Problem = {
  type: string; title: string; status: number;
  detail?: string; instance?: string; code: string;
  errors?: { field: string; message: string }[];
};
```

### 1.8 レート制限

Redis 固定ウィンドウ(1 分粒度)。超過時 **429** `rate_limited` + `Retry-After` 秒。全レスポンスに `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset`(epoch 秒)を付与する。

| 対象 | 制限 | キー |
|---|---|---|
| `POST /api/auth/email/request` | 5 回/10分 | IP+メール |
| OAuth 開始 | 20 回/10分 | IP |
| `POST /api/ingest/*` | 30 回/時 | ユーザー |
| `POST /api/chat/**/messages`・`regenerate` | 20 回/分 | ユーザー |
| 生成系 POST(記事・図・再翻訳・語彙再生成) | 30 回/分 | ユーザー |
| `GET /api/search*` | 60 回/分 | ユーザー |
| `GET /api/share/collections/{token}` | 120 回/分 | IP |
| その他すべて | 600 回/分 | ユーザー(匿名は IP) |

### 1.9 SSE 共通仕様

- `Content-Type: text/event-stream; charset=utf-8`、`Cache-Control: no-store`、`X-Accel-Buffering: no`。
- イベントは `event:` 行+`data:`(1 行 JSON)。**15 秒ごと**にコメント行 `: ping` を送出(プロキシ切断防止)。
- 異常終了は必ず `event: error`(data は Problem 形式)を送ってからクローズする(黙って切らない。P3)。
- ジョブ SSE(§21)は `Last-Event-ID` による再接続再開に対応(イベント `id:` = 単調増加の連番)。チャット SSE は再開非対応(切断時はメッセージ取得 API で確定結果を取る)。

### 1.10 OpenAPI 生成と TS クライアント生成手順(確定)

1. FastAPI アプリは全ルートに `tags`(本書の節名: `auth`, `ingest`, …)と `operation_id` を付与する。命名規約: `{tag}_{動詞}_{対象}`(例 `libraryItems_list`, `chat_send_message`)。`app.openapi()` は Pydantic モデル名をそのままスキーマ名にする。
2. エクスポート: `apps/api/scripts/export_openapi.py` が `app.openapi()` を JSON ダンプする。
   ```bash
   cd apps/api && uv run python scripts/export_openapi.py --out ../../packages/api-client/openapi.json
   ```
3. TS 生成: `packages/api-client` は **openapi-typescript v7**(型)+ **openapi-fetch v0.13**(ランタイム)を使う。
   ```bash
   pnpm --filter @alinea/api-client generate
   # = openapi-typescript openapi.json -o src/schema.d.ts
   ```
   `src/index.ts` は `createClient<paths>({ baseUrl: "/api", credentials: "include" })` をエクスポートし、SSE 用に `sseFetch()`(fetch + ReadableStream パーサ)を同梱する。
4. Turborepo: ルート `turbo.json` にタスク `codegen` を定義(`inputs: ["apps/api/app/**"]`, `outputs: ["packages/api-client/openapi.json", "packages/api-client/src/schema.d.ts"]`)。`apps/web#build` と `apps/extension#build` は `dependsOn: ["@alinea/api-client#codegen"]`。
5. CI は `pnpm codegen && git diff --exit-code packages/api-client` で生成物の同期を強制する。

## 2. auth — 認証

### 2.1 GET /api/auth/oauth/{provider}/start

- 認証: `anonymous` / パス: `provider ∈ {google, github}` / クエリ: `next`(ログイン後のアプリ内パス。既定 `/`。オープンリダイレクト防止のため `/` 始まりのみ許可)
- 動作: authlib で認可 URL を生成し **302**。`state` は Redis(10 分)に保存。
- エラー: 422 `validation_error`(provider 不正)。

### 2.2 GET /api/auth/oauth/{provider}/callback

- 認証: `anonymous` / クエリ: `code`, `state`
- 動作: トークン交換 → プロフィール取得 → メールアドレスで User を作成または紐付け → セッション発行(Set-Cookie)→ `next` へ **302**。
- エラー: 失敗時は `/login?error=oauth_failed` へ 302(API エラーを画面に出さない)。

### 2.3 POST /api/auth/email/request

- 認証: `anonymous`
```ts
Request: { email: string; next?: string }
Response 202: { sent: true }   // アカウント有無に関わらず同一応答(列挙攻撃対策)
```
- 動作: 32 バイト URL-safe トークン(**有効 15 分・単回**)を発行し、`https://alinea.app/api/auth/email/verify?token=…` をメール送信。

### 2.4 GET /api/auth/email/verify

- 認証: `anonymous` / クエリ: `token`
- 動作: 検証成功でユーザー作成(初回)+セッション発行 → `next` へ 302。失敗は `/login?error=link_expired` へ 302。

### 2.5 POST /api/auth/logout

- 認証: `session` → **204**。現在セッションを破棄し `yk_session` を失効させる。

### 2.6 GET /api/auth/me

- 認証: `session|ext`
```ts
Response 200: {
  user: { id: string; email: string; display_name: string; avatar_url: string | null;
          providers: ("google" | "github" | "email")[]; created_at: string };
  unread_notifications: number;   // 拡張の琥珀ドット・ヘッダのベルドットに使用
}
```

### 2.7 POST /api/auth/extension-token

- 認証: `session`(クッキーのみ。トークンでの再発行不可)
```ts
Response 201: { token: string; expires_at: string }  // "yk_ext_" + base64url 32byte、有効180日
```
- 決定: 1 ユーザー 1 トークン。再発行で旧トークンは即時失効。値はハッシュ(SHA-256)のみ DB 保存し、平文はこのレスポンスでのみ返す。

### 2.8 DELETE /api/auth/account

- 認証: `session`
```ts
Request: { confirm: "delete" }   // 誤操作防止の合言葉
Response 202: { job_id: string } // kind="account_delete"。個人データ完全削除ジョブ(docs/01 §13)。全セッション即時失効
```

## 3. ingest — 取り込み(拡張専用経路)

取り込みはブラウザ拡張が唯一の経路(docs/02 §1)。例外として「+この論文も取り込む」(参考文献からの芋づる取り込み、docs/04 §10.2)は web からも `POST /api/ingest/arxiv` を呼ぶ。

### 3.1 GET /api/ingest/check

- 認証: `session|ext` / クエリ: `url`(必須。現在タブの URL)
- 用途: 拡張ポップアップの状態判定(保存前/既にライブラリ/一般 PDF)+ LaTeX 有無判定(「品質レベル A 見込み」)。

```ts
Response 200: {
  kind: "arxiv" | "pdf" | "unsupported";   // URL 種別判定
  arxiv_id: string | null;                 // "2209.03003"(正規化済み)
  arxiv_version: string | null;            // "v3"(URL 指定があれば)
  bib: {                                   // kind=arxiv のみ(メタデータ API から)。pdf/unsupported は null
    title: string; authors_short: string; venue: string | null; year: number | null;
  } | null;
  latex_available: boolean | null;         // kind=arxiv のみ。true=「✓ LaTeX ソースあり — 品質レベル A 見込み」
  suggested_tags: string[];                // 「提案: distillation +」(arXiv カテゴリ+共起)
  saved: {                                 // 既にライブラリにあれば非 null → 拡張は状態3へ
    library_item_id: string;
    status: Status;
    added_at: string;
    progress_pct: number;
    last_position: LastPosition | null;    // 「前回: §2.1 整流フロー · 昨日 21:52」
    pipeline: PipelineState | null;
  } | null;
}
```
- 決定: LaTeX 有無は arXiv abs ページの e-print(TeX Source)存在確認で判定し、結果を Redis に 24 時間キャッシュする(arXiv レート制限遵守。docs/09 §5.3)。
- エラー: 422(url 欠落)。到達不能・非対応 URL はエラーにせず `kind: "unsupported"` を返す。

### 3.2 POST /api/ingest/arxiv

- 認証: `session|ext` / ヘッダ: `Idempotency-Key`(任意。拡張の再試行キュー用。Redis 24h 保持、同キー再送は初回レスポンスを再生)

```ts
Request: {
  url: string;                       // arXiv abs/pdf/html URL または "arXiv:2209.03003v3"
  status?: Status;                   // 既定 "planned"。拡張 UI は planned|up_next|reading の3択
  tags?: string[];
  collection_id?: string | null;
  quick_note?: string | null;        // ひとことメモ(LibraryItem.one_line_note)
}
Response 202: {
  paper_id: string;
  library_item_id: string;
  job_id: string;                    // 取り込みパイプライン(§21 で進捗購読)
  duplicate: false;
}
```
- 既に同一 Paper の LibraryItem を持つ場合: **409** `duplicate` + `errors` なし、拡張が状態 3 を描くための本文:

```json
{ "type": "https://alinea.app/problems/duplicate", "title": "既にライブラリにあります",
  "status": 409, "code": "duplicate",
  "existing": { "library_item_id": "li_01JZ…", "status": "reading", "added_at": "2026-07-02T12:00:00Z",
                "progress_pct": 42, "last_position": { "…": "…" } } }
```
- 別バージョン(v1 所持中に v2)は 409 にせず 202 で受け、同一 Paper の新 `source_version` として取り込む(docs/02 §6)。
- エラー: 422(URL が arXiv でない)、429 `rate_limited`。クォータ超過は取り込み自体を失敗させない(§17.4: 翻訳段のみ `waiting_quota` で停止)。

### 3.3 POST /api/ingest/pdf

- 認証: `session|ext` / `Content-Type: multipart/form-data` / `Idempotency-Key` 対応

| フィールド | 型 | 内容 |
|---|---|---|
| `file` | file | `application/pdf`、**最大 50MB** |
| `meta` | JSON 文字列 | `{ source_url: string; title_guess: string | null; status?: Status; tags?: string[]; collection_id?: string | null; quick_note?: string | null }` |

```ts
Response 202: { paper_id: string; library_item_id: string; job_id: string; duplicate: false }
```
- Paper は `visibility: "private"`・`pdf_sha256` で重複検知(同一ユーザーの同一 SHA-256 は 409 `duplicate`、他ユーザーとは共有しない)。
- エラー: 413 `payload_too_large`、415 `unsupported_media_type`、422(meta 不正)。テキストレイヤ無し PDF はジョブ側で `failed(parsing, "テキストが抽出できません")`(docs/02 §3)。

### 3.4 GET /api/ingest/recent

- 認証: `session|ext` / クエリ: `limit`(既定 **3**、最大 10。拡張フッタ「直近の取り込み」)
```ts
Response 200: { items: {
  library_item_id: string; title: string;
  pipeline: PipelineState;           // 処理中: stage+progress_pct / 完了: stage="complete"
  completed_at: string | null;       // 「今日 8:02」「7/01」表示の元
  viewer_url: string;                // "/papers/li_…"(履歴行クリックの遷移先)
}[] }
```

## 4. papers — 論文実体

### 4.1 GET /api/papers/{paper_id}

- 認証: `session` → `Response 200: PaperBib & { official_impl_candidate: string | null; thumbnail_url: string | null }`
- private 論文は所有者以外 **404**(存在自体を隠す)。

### 4.2 POST /api/papers/{paper_id}/reingest

- 認証: `session`(情報パネル「再取り込み」。B→A 昇格の手動実行を含む)
```ts
Request: {}    // ボディなし
Response 202: { job_id: string }   // kind="reingest"。新 DocumentRevision+注釈リアンカー
```
- エラー: 409 `conflict`(同一 Paper の reingest 実行中)。

### 4.3 GET /api/papers/{paper_id}/ingest-log

- 認証: `session`(情報パネル「処理ログ」)。決定: `at` 昇順(古→新)・ページングなし(1 取り込みあたり数十件想定)。
```ts
Response 200: { entries: {
  at: string; stage: string; level: "info" | "warn" | "error";
  message: string;                   // 「arXiv HTML にフォールバック(LaTeX 取得失敗: 404)」等
}[] }
```

### 4.4 GET /api/papers/{paper_id}/pdf

- 認証: `session` → **302**(オブジェクトストレージの署名付き URL、有効 10 分)。「⤓ 原文PDF」用。PDF アセットが無い場合 404。

## 5. library-items — ライブラリ・ダッシュボード・保存フィルタ

### 5.1 GET /api/library-items(一覧: フィルタ・ソート・ページング完全定義)

- 認証: `session`

| クエリ | 型/値 | 意味 |
|---|---|---|
| `quick` | `Quick`(既定 `all`) | クイックフィルタ(ステータス合成。docs/06 §1) |
| `status` | `Status` 複数可 | 属性フィルタ「ステータス」。複数指定は **OR** |
| `tag` | string 複数可 | 属性フィルタ「タグ」。複数指定は OR |
| `collection_id` | `col_…` | 属性フィルタ「コレクション」 |
| `quality` | `A` \| `B` | 属性フィルタ「品質」 |
| `year` | int 複数可 | 属性フィルタ「年」(発表年)。OR |
| `filter_id` | `sf_…` | 保存フィルタを適用(明示クエリが同項目を上書き) |
| `q` | string | 書誌の簡易絞り込み(タイトル・著者の部分一致。横断検索 §15 とは別) |
| `sort` | `updated_at`(既定) \| `added_at` \| `title` \| `deadline` \| `reading_time` \| `comprehension` \| `priority` | テーブル列ヘッダ・「並び: 更新日 ▾」 |
| `order` | `desc`(既定) \| `asc` | |
| `cursor` / `limit` | §1.5。limit 既定 50・最大 100 | |

- フィルタ結合規則(**決定**): 同一属性内 OR・異なる属性間 AND。`quick` と `status` を同時指定した場合は **積集合**。
- NULL ソート(**決定**): `deadline` / `comprehension` の未設定(`—` 表示)は昇順・降順とも**常に末尾**。

```ts
Response 200: { items: LibraryItemSummary[]; next_cursor: string | null; total: number }
```

### 5.2 GET /api/library-items/facets

- 認証: `session` / クエリ: §5.1 と同じフィルタ群(`quick` は無視)
- 用途: クイックフィルタピルの件数(「すべて 41 / 未読 12 / 途中 4 / 読了 23 / 要再確認 2」)と属性フィルタドロップダウンの選択肢+件数。

```ts
Response 200: {
  quick: { all: number; unread: number; in_progress: number; done: number; recheck: number };
  status: Record<Status, number>;
  tags: { tag: string; count: number }[];        // 件数降順・上位100
  collections: { id: string; name: string; count: number }[];
  quality: { A: number; B: number };
  years: { year: number; count: number }[];
}
```

### 5.3 GET /api/library-items/{id}

- 認証: `session` → `Response 200: LibraryItemSummary`(単体は常に全フィールド)

### 5.4 PATCH /api/library-items/{id}

- 認証: `session|ext`(拡張の「ステータス変更 ▾」を含む)

```ts
Request: {   // すべて任意。指定フィールドのみ更新
  status?: Status;
  priority?: Priority | null;
  deadline?: string | null;          // "YYYY-MM-DD"
  tags?: string[];                   // 全置換(提案タグの承認 = 提案値を加えた配列を送る)
  one_line_note?: string | null;
  comprehension?: number | null;     // 1–5(読了フロー 1g)
  importance?: Importance | null;    // 読了フロー
}
Response 200: LibraryItemSummary
```
- サーバー挙動(**決定**): `status` が初めて `done` になった時点で `finished_at` を自動記録(以後のステータス変更で消さない・上書きしない)。`tags` に `suggested_tags` の値が含まれたら当該提案は消化、含まれない提案は残る。ステータス変更はユーザー操作のみで行われ、本 API 以外がステータスを変えることはない(P6。提案通知の「変更する」も内部的に本 API 相当の処理)。

### 5.5 DELETE /api/library-items/{id}

- 認証: `session` → **204**。配下(注釈・メモ・チャット・語彙・リソース・記事・図・セッション)を削除。private Paper で他参照が無ければ Paper ごと削除(docs/01 §13)。

### 5.6 POST /api/library-items/bulk(一括操作バー)

- 認証: `session`
```ts
Request: {
  ids: string[];                     // 最大 100
  op: "set_status" | "add_tags" | "add_to_collection";
  status?: Status;                   // op=set_status
  tags?: string[];                   // op=add_tags(既存タグに追加)
  collection_id?: string;            // op=add_to_collection(末尾に追加)
}
Response 200: { updated: number }
```
- 決定: 全 ID を事前検証し、不存在・他ユーザー所有の ID が 1 件でも含まれる場合は **404** `not_found` で全体を失敗させる(部分適用しない。単一トランザクションで一括適用)。`op=add_to_collection` で既にコレクションにある項目はスキップし `updated` に数えない。

### 5.7 PUT /api/library-items/queue-order(すぐ読むキューの並び)

- 認証: `session`
```ts
Request: { library_item_ids: string[] }   // status=up_next の全 ID を新順序で
Response 200: { ok: true }
```
- エラー: 422(up_next でない ID が混在/不足)。

### 5.8 PUT /api/library-items/{id}/position(読書位置の自動保存)

- 認証: `session`
```ts
Request: { revision_id: string; block_id: string; mode: LastPosition["mode"] }
Response 200: { saved_at: string }
```
- 呼び出し頻度はクライアント側で 5 秒デバウンス(plans/09-screens/viewer-shell.md)。デバイス間同期は本 API の最新値が正。

### 5.9 POST /api/library-items/{id}/reading-sessions(読書時間計測)

- 認証: `session`
```ts
Request: {
  client_session_id: string;         // クライアント生成 UUID。同一 ID は upsert(冪等)
  started_at: string;
  last_activity_at: string;
  active_seconds: number;            // アクティブ秒数の累計(タブ前面+操作あり)
}
Response 200: { reading_seconds_total: number; today_reading_minutes: number }
```
- 設定 `reading.track_reading_time=false` のユーザーからの呼び出しは記録せず 200 `{...同フィールド(増分なし)}` を返す(クライアント側も送信停止するが、競合時に黙って 4xx にしない)。

### 5.10 DELETE /api/library-items/{id}/tag-suggestions/{tag}

- 認証: `session` → **204**。提案タグの却下(再提案しない)。

### 5.11 POST /api/library-items/{id}/duplicate-resolution(ファジー一致の統合確認)

- 認証: `session`
```ts
Request: { action: "merge" | "dismiss"; other_paper_id?: string }  // merge 時必須
Response 200: { library_item: LibraryItemSummary }
```
- `merge`: 2 つの Paper を統合し(arXiv 側を残す)、B→A 昇格フローに乗せる(docs/02 §6)。`dismiss`: 「同一の可能性」カードを消し再提示しない。

### 5.12 GET /api/dashboard(1d)

- 認証: `session`
```ts
Response 200: {
  continue_reading: LibraryItemSummary[];        // status=reading、位置保存の新しい順、最大3
  up_next_queue: LibraryItemSummary[];           // status=up_next、§5.7 の手動順。6件以上で UI が「積みすぎかも?」
  deadlines: {
    collections: { id: string; name: string; deadline: string; days_left: number;
                   done_count: number; total_count: number }[];
    items: { library_item_id: string; title: string; deadline: string;
             assignee_self: boolean; status: Status }[];   // 「担当発表 · 締切 7/16 · 未着手」
  };
  recent: { week_count: number; items: LibraryItemSummary[] };  // 今週追加、最大6
  stats: {
    week: { finished_count: number; reading_hours: number };    // 「3 本 読了」「4.2 時間」
    weekly_hours: number[];                                     // 直近12週(古→新)。棒グラフ
  };
}
```

### 5.13 GET /api/tags

- 認証: `session` / クエリ: `q`(前方一致補完)、`limit`(既定・最大 **20**) → `Response 200: { items: { tag: string; count: number }[] }`(件数降順・同数はタグ名昇順)

### 5.14 保存フィルタ(SavedFilter)

```
GET    /api/saved-filters                認証: session
POST   /api/saved-filters
PATCH  /api/saved-filters/{id}
DELETE /api/saved-filters/{id}           → 204
```
```ts
type SavedFilterConditions = {           // §5.1 のクエリと同じ語彙
  quick?: Quick; status?: Status[]; tags?: string[];
  collection_id?: string; quality?: Quality; years?: number[];
};
POST/PATCH Request: { name: string; conditions: SavedFilterConditions;
                      sort: { key: "updated_at" | "added_at" | "title" | "deadline"
                                 | "reading_time" | "comprehension" | "priority";   // §5.1 の sort と同一語彙
                              order: "asc" | "desc" } }
Response 200/201: { id: string; name: string; conditions: SavedFilterConditions;
                    sort: {...}; count: number }   // count はクエリ実行時の導出値(保存しない)
GET Response 200: { items: SavedFilter[] }         // サイドバー「締切あり 3」等
```

## 6. viewer — リビジョン・ドキュメント・進捗

### 6.1 GET /api/library-items/{id}/viewer(ビューア初期化の複合エンドポイント)

- 認証: `session`。ビューア初期表示 p50 2 秒(docs/09 §1)のため 1 リクエストで初期描画に必要な軽量データを返す(本文ブロックは含めない)。

```ts
Response 200: {
  library_item: LibraryItemSummary;
  revision: { id: string; quality_level: Quality; source_version: string | null;
              parser_version: string; page_count: number | null;
              figure_count: number; table_count: number; created_at: string };
  newer_revision: { id: string; reason: "arxiv_update" | "parser_upgrade" | "promotion" } | null;
                                        // 「新しいバージョンがあります」バナー(docs/02 §6)
  toc: TocNode[];
  translation: { style: Style; set_id: string; status: "pending" | "partial" | "complete";
                 progress_pct: number };            // 「翻訳 96%」
  counts: { annotations: number; resources: number; figures: number; notes: number };  // タブバッジ
  last_position: LastPosition | null;
  license_card: { license: License;
                  figure_reuse: "allowed" | "allowed_with_sa" | "allowed_nc" | "allowed_nd" | "forbidden";
                  message: string };                // 「CC BY 4.0 — 図表転載可」等の表示文
  ingest_timeline: { at: string; label: string }[]; // 3段(docs/02 §5.3)
  today_reading_minutes: number;                    // 目次フッタ「今日の読書 42分」
}

type TocNode = {
  section_id: string; number: string | null;        // "2.1"
  title_ja: string | null; title_en: string;        // 原題併記
  translated: boolean;                              // 節ごとの ✓
  in_progress_denominator: boolean;                 // false=参考文献等(淡色・分母外)
  on_demand: boolean;                               // true=未翻訳付録「開くと翻訳します(オンデマンド)」
  annotation_count: number; bookmarked: boolean;
  children: TocNode[];                              // 2階層
};
```

### 6.2 GET /api/papers/{paper_id}/revisions

- 認証: `session` → `Response 200: { items: { id, quality_level, source_version, parser_version, created_at, is_current }[] }`

### 6.3 GET /api/revisions/{revision_id}/document

- 認証: `session` / クエリ: `section_id`(任意。指定セクションのみ返す部分取得)
- 構造化ドキュメント(docs/01 §4)をそのまま返す:

```json
{
  "revision_id": "rev_01JZ…",
  "quality_level": "A",
  "sections": [{
    "id": "sec-3",
    "heading": { "number": "3", "title": "Method" },
    "blocks": [
      { "id": "blk-3-p2-a1f9", "type": "paragraph",
        "inlines": [
          { "t": "text", "v": "We train the model with " },
          { "t": "citation", "ref": "ref-12" },
          { "t": "text", "v": " using the loss in " },
          { "t": "ref", "kind": "equation", "ref": "eq-5" },
          { "t": "text", "v": "." } ],
        "page": 5, "bbox": [72.0, 340.2, 523.5, 402.8] },
      { "id": "blk-3-eq5-77c2", "type": "equation",
        "latex": "\\mathcal{L} = ...", "number": "5", "label": "eq:loss" }
    ]
  }]
}
```
- `page` / `bbox` は品質 B(および PDF アセットを持つ A)のみ。block `type` は docs/01 §4.1 の **12 種**(`paragraph` / `heading` / `figure` / `table` / `equation` / `code` / `list` / `quote` / `theorem` / `algorithm` / `footnote` / `reference_entry`)。
- ETag 対応: `ETag: "rev_01JZ…:sec-3"`。`If-None-Match` 一致で **304**(リビジョンは不変データ)。

### 6.4 GET /api/revisions/{revision_id}/blocks/{block_id}

- 認証: `session` → 単一ブロック+訳(根拠ジャンプ・引用プレビュー・語彙の文脈解決用)
```ts
Response 200: { block: Block; section_id: string; display: string;   // "§2.2 ¶3"
                translation: { text_ja: string; state: "machine" | "edited" | "protected" } | null }
```

### 6.5 GET /api/revisions/{revision_id}/figures(図表タブ)

- 認証: `session`
```ts
Response 200: { items: {
  block_id: string; kind: "figure" | "table";
  label: string | null;              // "fig:overview"
  display: string;                   // "図2" / "表1"
  caption_en: string; caption_ja: string | null;
  image_url: string | null;          // /api/assets/… 経由
  position: { section_display: string; page: number | null };   // 「§2.2 · p.5」
}[] }
```

### 6.6 GET /api/revisions/{revision_id}/references(参考文献)

- 認証: `session`
```ts
Response 200: { items: {
  ref_id: string;                    // "ref-12"
  number: string;                    // "[12]"
  authors: string | null; title: string | null; venue_year: string | null;
  arxiv_id: string | null; doi: string | null; url: string | null;
  in_library: { library_item_id: string } | null;   // 「ライブラリに有り ✓」
}[] }
```
- 「+ この論文も取り込む」はクライアントが `POST /api/ingest/arxiv { url: "https://arxiv.org/abs/…" }` を呼ぶ(専用エンドポイントは設けない)。

### 6.7 GET /api/revisions/{revision_id}/search(論文内検索 `/`)

- 認証: `session` / クエリ: `q`(必須)、`limit`(既定 50・最大 100)
```ts
Response 200: { items: {
  block_id: string; section_id: string; display: string;
  matched_in: ("source" | "translation")[];   // 訳文ヒットは原文ブロックと同一視して1件
  snippet: string;                            // <mark class="alinea-search-hit"> 付き HTML エスケープ済み
}[] }
```

### 6.8 POST /api/library-items/{id}/adopt-revision(新リビジョンへの切替)

- 認証: `session`(「新しいバージョンがあります」バナー・昇格提案通知の適用)
```ts
Request: { revision_id: string }
Response 200: { library_item: LibraryItemSummary;
                reanchor: { moved: number; unplaced: number } }  // 注釈リアンカー結果
```
- 自動切替はしない(P6)。本 API がユーザー操作の唯一の適用経路。

## 7. translations — 翻訳・再翻訳・用語

### 7.1 GET /api/revisions/{revision_id}/translations

- 認証: `session` → 存在する TranslationSet の一覧(スタイル切替 UI の状態)
```ts
Response 200: { items: {
  set_id: string; style: Style; scope: "shared" | "personal";
  status: "pending" | "partial" | "complete"; progress_pct: number;
  glossary_snapshot_id: string;
}[] }
```

### 7.2 GET /api/revisions/{revision_id}/translations/{style}/units

- 認証: `session` / パス: `style ∈ {natural, literal}` / クエリ: `section_id`(必須。セクション単位で取得し本文と合成する)。該当スタイルの TranslationSet が未生成なら **404** `not_found`。
```ts
Response 200: { set_id: string; items: {
  unit_id: string;                   // tu_…
  block_id: string;
  text_ja: string | null;            // null=未翻訳(UI は原文+「翻訳中…」)
  state: "machine" | "edited" | "protected";
  quality_flags: ("placeholder_mismatch" | "number_mismatch" | "length_outlier"
                 | "glossary_violation" | "untranslated")[];
  proposal: { text_ja: string; generated_at: string; model: string } | null;  // 再翻訳の未採用案
}[] }
```
- `placeholder_mismatch` のブロックは `text_ja: null` で返す(壊れた訳を配信しない。docs/03 §4)。

### 7.3 POST /api/revisions/{revision_id}/translations(直訳のオンデマンド生成開始)

- 認証: `session`
```ts
Request: { style: "literal"; priority_section_id?: string }  // 表示中セクション優先(docs/03 §5)
Response 202: { set_id: string; job_id: string }             // 既に complete なら 200 { set_id, job_id: null }
```

### 7.4 POST /api/translation-sets/{set_id}/prioritize(開いたセクションを優先翻訳)

- 認証: `session` → `Request: { section_id: string }` → **202** `{ ok: true }`。キュー先頭への繰り上げ(docs/02 §5.2)。

### 7.5 POST /api/translation-sets/{set_id}/sections/{section_id}/translate(付録等のオンデマンド翻訳)

- 認証: `session` → **202** `{ job_id: string }`(kind=`section_translate`)。「この表を翻訳」もブロック指定版として同ジョブ: `Request: { block_id?: string }`(指定時はそのブロックのみ)。

### 7.6 POST /api/translation-units/{unit_id}/retranslate(再翻訳・指示つき再翻訳)

- 認証: `session`
```ts
Request: {
  instruction?: string;              // 「もっと簡潔に」等。省略=通常の再翻訳
  discard_edit?: boolean;            // state=edited のとき true 必須(「編集を破棄して再翻訳」)
}
Response 202: { job_id: string }     // kind=retranslate_unit。上位モデルへエスカレーション(docs/03 §9)
```
- 完了後、結果は `proposal` に入る(§7.2)。直接上書きしない(差分表示→採用の UI 前提)。
- エラー: 409 `conflict`(state=edited かつ discard_edit 無し。code は `edit_protected` を detail に明記した `conflict`)。

### 7.7 PUT /api/translation-units/{unit_id}(手動編集)

- 認証: `session`
```ts
Request: { text_ja: string }
Response 200: { unit_id: string; state: "edited"; text_ja: string }
```
- 共有セット(scope=shared)のユニットへの書き込みは自動的に personal フォークを作ってから適用する(docs/03 §8。レスポンスの `set_id` が変わるため `Response` に `set_id: string` を含める)。

### 7.8 proposal の採用・破棄

```
POST   /api/translation-units/{unit_id}/proposal/accept   → 200 { unit_id, text_ja, state: "machine" }
DELETE /api/translation-units/{unit_id}/proposal           → 204
```

### 7.9 用語集(Glossary。訳語統一の内部機構 — 語彙帳とは別物)

```
GET    /api/glossary/terms?scope={user|paper}&library_item_id={li_…}   認証: session
POST   /api/glossary/terms
PATCH  /api/glossary/terms/{term_id}?dry_run={true|false}   // dry_run 省略時は false
DELETE /api/glossary/terms/{term_id}                                    → 204
POST   /api/glossary/terms/{term_id}/promote                            // 論文ローカル→ユーザー昇格
```
```ts
type GlossaryTerm = {
  id: string; scope: "global" | "user" | "paper";
  library_item_id: string | null;    // scope=paper のみ
  source_term: string; target_term: string;
  pos_label: string | null;
  policy: "translate" | "keep_original" | "both";   // 訳す|原語のまま|併記
  auto_extracted: boolean;           // 取り込み時の自動抽出候補(「この論文の用語」パネル)
};
GET Response 200: { items: GlossaryTerm[] }          // scope=global は読み取り専用で含める。source_term 昇順(大文字小文字無視)・ページングなし
POST Request: { scope: "user" | "paper"; library_item_id?: string;
                source_term: string; target_term: string; policy: GlossaryTerm["policy"] }
PATCH Request: { target_term?: string; policy?: GlossaryTerm["policy"] }
PATCH Response 200 (dry_run=true):  { affected_block_count: number }     // 「12 段落を再翻訳します」
PATCH Response 202 (dry_run=false): { term: GlossaryTerm; affected_block_count: number;
                                      job_id: string | null }            // 影響ブロックのみ再翻訳
```
- `promote` → **201** `{ term: GlossaryTerm }`(scope=user の複製を作成。元の paper term は残る)。
- scope=global への書き込みは 403。

### 7.10 POST /api/translation-sets/{set_id}/section-selection(セクション選択の確定)

- 認証: `session`(4f 設定「30 ページ超の論文はセクション選択を提案」の確定操作。plans/05 §16-6)
```ts
Request: { section_ids: string[] }   // 翻訳対象として選択したセクション
Response 200: { ok: true; canceled_jobs: number }
```
- 動作: 選外セクションの **queued な翻訳ジョブを canceled 化**する(running のジョブは対象外)。選択済みセクションは通常どおり翻訳を継続。後から §7.5 のオンデマンド翻訳で追加可能。
- エラー: 422(存在しない section_id を含む)。

## 8. annotations — 注釈

### 8.1 GET /api/library-items/{id}/annotations

- 認証: `session` / クエリ: `color`(AnnColor 複数可)、`has_comment`(bool)、`placed`(bool。`false`=未配置のみ)、`kind`(`highlight|bookmark`)
- 並び順(決定): 文書内出現順(セクション順→ブロック順→`start` 昇順)。未配置(`placed: false`)は末尾に作成降順で置く。ページングなし。
```ts
Response 200: { items: Annotation[]; counts: { all: number; important: number; question: number;
                idea: number; term: number; with_comment: number; unplaced: number } }
type Annotation = {
  id: string; kind: "highlight" | "bookmark";
  color: AnnColor | null;            // bookmark は null
  anchor: AnchorRef;                 // bookmark はセクション参照(start/end=null)
  comment: string | null;            // 「コメント」= highlight + comment
  placed: boolean;                   // false=リアンカー失敗(未配置)
  created_at: string; updated_at: string;
};
```

### 8.2 POST /api/library-items/{id}/annotations

- 認証: `session`
```ts
Request: { kind: "highlight" | "bookmark"; color?: AnnColor;   // highlight は必須
           anchor: Anchor; comment?: string }
Response 201: Annotation
```
- エラー: 422(highlight で color 欠落 / anchor のブロック不存在)。

### 8.3 PATCH /api/annotations/{annotation_id} / 8.4 DELETE

```ts
PATCH Request: { color?: AnnColor; comment?: string | null }
PATCH Response 200: Annotation
DELETE → 204
```

## 9. notes — メモ

```
GET    /api/library-items/{id}/notes     認証: session
POST   /api/library-items/{id}/notes
PATCH  /api/notes/{note_id}
DELETE /api/notes/{note_id}              → 204
```
```ts
type Note = {
  id: string; content_md: string;
  source: { chat_message_id: string } | null;   // チャット昇格(「↑ メモに保存」)の出自
  anchors: AnchorRef[];                          // 昇格時に根拠アンカーを引き継ぐ
  created_at: string; updated_at: string;
};
GET Response 200: { items: Note[] }              // 更新降順・ページングなし(1論文あたり少数想定)
POST Request: { content_md: string; source_message_id?: string; anchors?: Anchor[] }
POST Response 201: Note
PATCH Request: { content_md: string } → 200: Note
```
- `source_message_id` 指定時、`anchors` 省略ならメッセージの根拠アンカーをサーバーが複写する。

## 10. chat — 読解チャット(SSE)

### 10.1 スレッド

```
GET    /api/library-items/{id}/chat/threads          認証: session
POST   /api/library-items/{id}/chat/threads          Request: { title: string } → 201
PATCH  /api/chat/threads/{thread_id}                 Request: { title: string } → 200
DELETE /api/chat/threads/{thread_id}                 → 204(メインスレッドは削除不可 409)
```
```ts
type ChatThread = { id: string; title: string; is_main: boolean;
                    message_count: number; last_message_at: string | null };
GET Response 200: { items: ChatThread[] }   // メイン先頭・以後更新降順
```
- LibraryItem 作成時に `is_main: true` の「メイン」スレッドを自動作成する。

### 10.2 GET /api/chat/threads/{thread_id}/messages

- 認証: `session` / `cursor`/`limit`(既定 50。新しい方から遡る。`next_cursor` は過去方向)
```ts
Response 200: { items: ChatMessage[]; next_cursor: string | null }
type ChatMessage = {
  id: string; role: "user" | "assistant";
  blocks: MessageBlock[];
  context_anchors: AnchorRef[];      // 選択質問の引用チップ(user)
  quick_action: QuickAction | null;
  status: "complete" | "error";      // 失敗回答も残す(P3)
  error: Problem | null;
  created_at: string;
};
type MessageBlock =
  | { type: "markdown"; text: string;          // 本文。インライン根拠は "[[ev:1]]" トークン
      evidence: { ref: number; display: string; anchor: AnchorRef }[] }
  | { type: "aside"; label: "outside_knowledge" | "speculation"; text: string };
                                     // 「論文外の知識」「推測」ボックス
type QuickAction = "summary_3line" | "beginner_explain" | "contributions_limits"
  | "experiment_setup" | "implementation_points"          // 常設チップ5種
  | "detailed_summary" | "explain_equation" | "explain_figure"
  | "expert_summary" | "related_work_position";           // 導線・入力候補
```

### 10.3 POST /api/chat/threads/{thread_id}/messages(送信 — SSE ストリーミング)

- 認証: `session` / リクエストは JSON、レスポンスは `text/event-stream`(クライアントは fetch + ReadableStream で受信)

```ts
Request: {
  content: string;                   // quick_action 指定時は空文字可
  context_anchors?: Anchor[];        // 選択質問(複数積み可)。図・数式はブロック全体参照
  quick_action?: QuickAction;
}
```

SSE イベント形式(**確定**):

```
event: start
data: {"message_id":"msg_01JZKA…","thread_id":"th_01JZK3…","user_message_id":"msg_01JZK9…"}

event: delta
data: {"block_index":0,"block_type":"markdown","text":"整流フローの学習目的は、結局 [[ev:1]] の最小二乗回帰に帰着します。"}

event: evidence
data: {"ref":1,"display":"式(5) · §2.1","anchor":{"revision_id":"rev_01JZ…","block_id":"blk-3-eq5-77c2","start":null,"end":null,"quote":null,"side":"source"}}

event: delta
data: {"block_index":1,"block_type":"aside","label":"outside_knowledge","text":"実装では t を [0,1] から一様サンプリングし、"}

event: done
data: {"message_id":"msg_01JZKA…","finish_reason":"stop"}

event: error
data: {"type":"https://alinea.app/problems/provider-error","title":"回答の生成に失敗しました","status":502,"code":"provider_error"}
```

- 規則: `delta` は `block_index` 昇順。同一 `block_index` の `text` は連結。`block_type: "aside"` の初回 delta に `label` を含む。`evidence` は該当 `[[ev:n]]` トークンの初出 delta の**後**に必ず 1 回送る。サーバーは送出前に根拠ブロックの実在を検証し、実在しないチップは `[[ev:n]]` トークンごと除去する(docs/05 §5)。
- `done` 後の確定メッセージは §10.2 で再取得できる(SSE 切断時の回復経路)。
- エラー: 429 `quota_exceeded` / `rate_limited`(SSE 開始前は通常の JSON エラー、開始後は `event: error`)。

### 10.4 POST /api/chat/messages/{message_id}/regenerate

- 認証: `session` / `Request: { content?: string }`(編集した質問の再送信に対応。省略=同一質問)
- レスポンス: §10.3 と同一の SSE。旧回答は履歴に残し、新回答を新規メッセージとして追加する(**決定**: 上書きせず追記。P3 と履歴検索の一貫性のため)。

### 10.5 POST /api/chat/threads/{thread_id}/summarize-to-note(まとめてメモ化)

- 認証: `session` → **201** `{ note: Note }`(**同期実行**。ジョブ化しない — plans/07 §12-7 の決定。スレッド全体を LLM 要約して Note を作成し、そのまま返す)。
- クォータ: `chat_messages` を 1 消費(task='summary'・job_id なしで記録。plans/07 §9.2)。超過は 429 `quota_exceeded`。LLM 失敗は 502 `provider_error`。

## 11. vocab — 語彙帳(英語学習)

### 11.1 GET /api/vocab

- 認証: `session`

| クエリ | 値 |
|---|---|
| `kind` | `VocabKind` 複数可 |
| `due` | `true`(復習期のみ: `next_review_at <= now`) |
| `q` | 語彙帳内検索(見出し語・語義。「語彙を検索」) |
| `library_item_id` | 出典論文で絞る |
| `sort` | `added_at`(既定・降順) \| `term`(昇順) |
| `cursor` / `limit` | 既定 50・最大 100 |

```ts
Response 200: { items: VocabEntrySummary[]; next_cursor: string | null; total: number;
                counts: { all: number; word: number; collocation: number; idiom: number; due: number } }
type VocabEntrySummary = {
  id: string; kind: VocabKind; term: string;
  meaning_short: string | null;      // 「文脈での語義」短形(生成中は null)
  source: { library_item_id: string; paper_title: string; display: string };  // "Rectified Flow · §2.1"
  added_at: string;
  generation: "pending" | "done" | "failed";
};
```

### 11.2 POST /api/vocab(「語彙に追加」)

- 認証: `session`
```ts
Request: {
  library_item_id: string;
  term: string;                      // 選択テキスト
  anchor: Anchor;                    // side="source" 必須(原文選択のみ。docs/04 §8)
  context_sentence: string;          // 選択を含む原文センテンス全体
  highlight: { start: number; end: number };   // センテンス内の対象語範囲
}
Response 201: { entry: VocabEntryDetail; generation_job_id: string }  // AI 生成はバックグラウンド
```
- エラー: **409** `duplicate`(同一見出し語が既存。判定は `term` の正規化一致 = trim+小文字化、`kind` は不問(決定。docs/11 の「同一見出し語は重複エントリを作らない」を具体化)。本文に `existing: { vocab_id }` を含め、UI は「既に語彙帳にあります」トーストから既存を開く)/ 422(`anchor.side != "source"`)。

### 11.3 GET /api/vocab/{vocab_id}

```ts
Response 200: VocabEntryDetail
type VocabEntryDetail = VocabEntrySummary & {
  pos_label: string | null; ipa: string | null;
  anchor: AnchorRef;                 // 「原文で見る →」
  context_sentence: string; highlight: { start: number; end: number };
  ai: {
    context_meaning: { short: string; long: string } | null;
    interpretation: string | null;   // 解釈のしかた
    etymology: string | null;        // 語源メモ
    mnemonic: string | null;         // ✦ 覚えるコツ
    related_expressions: string | null;  // よく出る形・近い表現
    edited_fields: string[];         // 編集済みフィールド名(再生成で上書きしない)
    generation_error: string | null; // 失敗理由+「生成を再試行」の表示条件
  };
  srs: { stage: 1 | 2 | 3 | 4 | 5; next_review_at: string | null;  // null=習得済み
         review_count: number;
         history: { result: ReviewResult; at: string }[] };
};
```

### 11.4 PATCH /api/vocab/{vocab_id}(フィールド編集)

```ts
Request: {   // 指定フィールドのみ。ai.* の編集は edited_fields に自動追加
  kind?: VocabKind; term?: string; pos_label?: string; ipa?: string;
  ai?: { context_meaning?: { short: string; long: string }; interpretation?: string;
         etymology?: string; mnemonic?: string; related_expressions?: string };
}
Response 200: VocabEntryDetail
```

### 11.5 DELETE /api/vocab/{vocab_id} → 204(クライアントは取り消しトースト表示。docs/11 §6.3)

### 11.6 POST /api/vocab/{vocab_id}/regenerate

```ts
Request: { fields?: string[] }   // 省略=未編集フィールド全部。edited_fields は常にスキップ
Response 202: { job_id: string }
```

### 11.7 GET /api/vocab/review-queue(復習セッション)

```ts
Response 200: { items: VocabEntryDetail[]; total: number }   // next_review_at <= now、次回復習日の古い順、最大100
```

### 11.8 POST /api/vocab/{vocab_id}/review(自己評価)

```ts
Request: { result: ReviewResult }
Response 200: { srs: VocabEntryDetail["srs"]; next_review_display: string }  // 「次の復習: 明日(2 回目)」
```
- スケジュール規則は docs/11 §7.1(固定段階 1/3/7/14/30 日。`good`=段階+1、段階 5 通過で習得済み `next_review_at: null`。`again`=段階 1 リセット・翌日)。

### 11.9 GET /api/vocab/export/markdown

- クエリ: §11.1 と同じフィルタ → **200** `text/markdown; charset=utf-8` + `Content-Disposition: attachment; filename="alinea-vocab-20260706.md"`。

## 12. resources — 外部リソース

### 12.1 GET /api/library-items/{id}/resources

- 認証: `session`
```ts
Response 200: {
  items: ResourceLink[];             // 追加日時昇順(新規が末尾)
  suggestion: {                      // 公式実装の提案(破線カード)。無ければ null
    url: string;                     // "https://github.com/gnobitab/RectifiedFlow"
    detected_from: "arxiv_page";
  } | null;
  count: number;                     // タブバッジ(提案は数えない)
}
type ResourceLink = {
  id: string; kind: ResKind; url: string;   // 正規化済み
  official: boolean;                 // 「公式実装」バッジ(github かつ提案経由のみ true)
  title: string; source_label: string;      // "GitHub" / "YouTube" / "iclr.cc" / "zenn.dev"
  thumbnail_url: string | null;      // youtube のみ表示
  meta:                              // 種類別(取得失敗時は {})
    | { language: string | null; stars: number | null; updated_at: string | null }   // github
    | { duration_seconds: number | null }                                            // youtube
    | { format: "pdf"; pages: number | null }                                        // slides
    | { reading_minutes: number | null }                                             // article
    | {};
  meta_fetched: boolean;             // false=「タイトル・メタ取得不可」の控えめ表示
  note: string | null;               // ひとことメモ。§参照は "[[sec:sec-3|§2.2]]" 埋め込み記法
  created_at: string;
};
```

### 12.2 POST /api/library-items/{id}/resources(URL 貼り付け追加)

```ts
Request: { url: string; note?: string }
Response 201: ResourceLink          // kind は docs/12 §2 の規則でサーバー判定。メタは同期取得(3秒タイムアウト、失敗時 meta_fetched=false で成立)
```
- エラー: **409** `duplicate`(正規化後 URL が既存。`existing: { resource_id }` を含める)/ 422(URL 形式不正)。

### 12.3 PATCH /api/resources/{resource_id}

```ts
Request: { title?: string; kind?: ResKind; note?: string | null }
Response 200: ResourceLink
```

### 12.4 DELETE /api/resources/{resource_id} → 204(クライアント側「元に戻す」トーストは §12.2 の再作成で実現)

### 12.5 POST /api/resources/{resource_id}/refresh-meta → 200: ResourceLink(メタ再取得)

### 12.6 公式実装提案の確定・却下

```
POST /api/library-items/{id}/resource-suggestion/accept   → 201: ResourceLink(official: true)
POST /api/library-items/{id}/resource-suggestion/dismiss  → 204(無視リストに永続記録。再取り込みでも再提案しない)
```
- 提案が存在しない場合はどちらも 404。

## 13. collections — コレクション

### 13.1 CRUD

```
GET    /api/collections                 認証: session
POST   /api/collections                 Request: { name: string; description?: string; deadline?: string }
GET    /api/collections/{collection_id}
PATCH  /api/collections/{collection_id} Request: { name?, description?, deadline?: string | null }
DELETE /api/collections/{collection_id} → 204(エントリのみ削除。LibraryItem は消さない)
```
```ts
GET(一覧) Response 200: { items: {
  id: string; name: string; deadline: string | null; days_left: number | null;
  item_count: number; done_count: number;            // 「3/5 読了」・サイドバーミニバッジ
}[] }                                                 // 作成日時昇順(新規は末尾)・ページングなし

GET(詳細) Response 200: {
  id: string; name: string; description: string | null;
  deadline: string | null; days_left: number | null;
  progress: { done: number; total: number };
  share: { status: "none" | "active" | "revoked";
           token: string | null; url: string | null;   // "https://alinea.app/c/x8Kf3qPw"
           include_notes: boolean; included_note_count: number };
  entries: CollectionEntry[];
}
type CollectionEntry = {
  id: string;                        // ce_…
  order: number;                     // 1 始まり
  library_item: LibraryItemSummary;
  assignee: string | null;           // 表示名文字列。"自分" はクライアント判定用に is_self
  assignee_is_self: boolean;
  presentation_minutes: number | null;   // 「発表 25 分」
  note: string | null;               // 「予備(時間があれば)」
};
```

### 13.2 エントリ操作

```
POST   /api/collections/{collection_id}/entries      Request: { library_item_id: string } → 201: CollectionEntry(末尾に追加)
PATCH  /api/collection-entries/{entry_id}            Request: { assignee?: string | null; assignee_is_self?: boolean;
                                                                presentation_minutes?: number | null; note?: string | null } → 200: CollectionEntry
DELETE /api/collection-entries/{entry_id}            → 204
PUT    /api/collections/{collection_id}/entries/order Request: { entry_ids: string[] } → 200 { ok: true }
```
- 重複追加(同一 LibraryItem)は 409 `duplicate`。`entries/order` は全 ID 必須(不足は 422)。

### 13.3 共有リンク

```
POST   /api/collections/{collection_id}/share     → 201 { token, url, status: "active", include_notes: false }
PATCH  /api/collections/{collection_id}/share     Request: { include_notes: boolean } → 200(§13.1 詳細の share オブジェクトと同形)
DELETE /api/collections/{collection_id}/share     → 204(status: "revoked"。token は失効)
```
- token: **8 文字の英数**(`[A-Za-z0-9]`、生成は CSPRNG。例 `x8Kf3qPw`)。revoke 後の再発行(POST)は**新しい token** を生成する。発行済み(active)状態での POST は 409 `conflict`。
- `include_notes` を ON にした場合に共有されるメモは「対象 LibraryItem の Note のうち共有フラグを立てた 1 件」ではなく、**LibraryItem の `one_line_note`(ひとことメモ)のみ**とする(決定: 4c の「共有者のメモ」ボックスは 1 カード 1 ボックスであり、自由メモ全公開は 09 §4 の非公開原則に反するため。件数表示 `included_note_count` は one_line_note 非空のエントリ数)。

## 14. share — 匿名共有ページ

### 14.1 GET /api/share/collections/{token}

- 認証: `anonymous`。レスポンスヘッダ `X-Robots-Tag: noindex`(ページ側も `<meta name="robots" content="noindex">`)。
```ts
Response 200: {
  collection: { name: string; description: string | null;
                shared_by: string;               // 表示名(「YK さんが共有」)
                updated_at: string; deadline: string | null; item_count: number };
  include_notes: boolean;
  items: {
    order: number;
    title: string; authors_short: string; venue_year: string | null;
    arxiv_url: string | null;
    summary_3line: string[] | null;              // ✦ 要約
    shared_note: string | null;                  // include_notes=true のときのみ
  }[];
}
```
- revoked・不存在 token は **404**(区別しない)。個人資産(進捗・注釈・リソース等)は一切含めない(docs/09 §4)。

## 15. search — 横断検索

### 15.1 GET /api/search(全結果画面 4e)

- 認証: `session`

| クエリ | 値 |
|---|---|
| `q` | 必須。1〜200 字 |
| `source` | `all`(既定) \| `body` \| `notes` \| `chat` \| `article`(notes=メモ・注釈) |
| `library_item_id` | 「論文で絞る」ファセット |
| `sort` | `relevance`(既定) \| `recency` |
| `cursor` / `limit` | limit はグループ数。既定 10・最大 20 |

```ts
Response 200: {
  query: string; total: number; paper_count: number;   // 「12 件 · 3 論文」
  facets: {
    source: { all: number; body: number; notes: number; chat: number; article: number };
    papers: { library_item_id: string; title: string; count: number }[];
  };
  groups: {                          // 論文単位グループ化
    library_item: LibraryItemSummary;
    hit_count: number;               // グループ内総ヒット数(4e グループヘッダ「7 件」。plans/11 R-2)
    article: { article_id: string; title: string; generated_at: string } | null;
                                     // 記事ヒットを含む場合のみ。記事のみのグループは
                                     // ヘッダを「記事: {title}」「記事(自動構成) · M/D」表記に切替
    hits: SearchHit[];               // グループ内上位5件(plans/11 §3.5)
  }[];
  next_cursor: string | null;
}
type SearchHit = {
  source: "body" | "note" | "annotation" | "chat" | "article";
  matched_in: ("source" | "translation")[] | null;   // body のみ。訳文ヒットは原文と同一視し1件に統合
  display: string;                   // "§3.2" / "メモ" / "スレッド: メイン" / "記事 · 第2節"
  snippet: string;                   // <mark class="alinea-search-hit">検索語</mark> 付き(サニタイズ済み HTML)
  snippet_lang: "en" | "ja";         // スニペット書体切替(原文=Source Serif 4 / 訳文=Noto Serif JP)
  target:                            // 源別の遷移先
    | { kind: "viewer"; library_item_id: string; anchor: AnchorRef | null }   // 「該当位置へ →」。
                                     // anchor: null = 書誌ヒット(論文の先頭を開く。plans/11 R-2)
    | { kind: "note"; library_item_id: string; note_id: string }              // 「メモを開く →」
    | { kind: "chat"; library_item_id: string; thread_id: string; message_id: string }  // 「スレッドを開く →」
    | { kind: "article"; library_item_id: string; article_block_id: string }; // 「記事モードで開く →」
};
```
- 日英クロス: PGroonga で原文(英語ステミング)・訳文(日本語トークナイズ)両インデックスを同時検索。同一ブロックで原文・訳文の両方にヒットした場合は 1 件に統合し `matched_in: ["source","translation"]`(4e「原文ヒットと同一視」)。
- エラー: 422(q 欠落・201 字以上)。

### 15.2 GET /api/search/preview(1e ドロップダウン)

- 認証: `session` / クエリ: `q` のみ
```ts
Response 200: { total: number; items: SearchHit_with_paper[] }   // 上位3件
type SearchHit_with_paper = SearchHit & { library_item: { id: string; title: string } };
```
- 目標 p50 300ms(インクリメンタル用)。`limit` 固定 3。

## 16. notifications — 通知

### 16.1 GET /api/notifications

- 認証: `session` / `cursor`/`limit`(既定 20・最大 50)。並び順: `created_at` 降順(新しい順)。
```ts
Response 200: { items: Notification[]; next_cursor: string | null; unread: number }
type Notification = {
  id: string; kind: NtfKind; read: boolean; created_at: string;
  payload:                           // plans/02 §3.7 NotificationPayloadJson と同形
    | { kind: "translation_complete"; library_item_id: string; paper_title: string;
        job_id: string }
    | { kind: "status_suggestion"; library_item_id: string; paper_title: string;
        suggested_status: Status;                    // "reading" | "done"
        reason: "read_3min" | "reached_end";
        resolved: "applied" | "dismissed" | null }   // 2択の消化状態
    | { kind: "status_suggestion"; library_item_id: string; paper_title: string;
        reason: "promotion_b_to_a";                  // B→A 昇格提案(docs/02 §4)
        action: "promote_revision"; revision_id: string;   // suggested_status は持たない
        resolved: "applied" | "dismissed" | null }
    | { kind: "deadline_reminder"; collection_id: string; collection_name: string;
        days_left: number; unstarted_count: number };   // フィールド名は plans/02 §3.7 と一致
};
```

### 16.2 PATCH /api/notifications/{notification_id}

```ts
Request: { read: true } → 200: Notification
```

### 16.3 POST /api/notifications/read-all → 200 `{ updated: number }`

### 16.4 POST /api/notifications/{notification_id}/action(提案の 2 択)

```ts
Request: { action: "apply" | "dismiss" }   // 「変更する」/「そのまま」
Response 200: { notification: Notification; library_item: LibraryItemSummary | null }
```
- `apply` は status_suggestion のみ有効(それ以外 422)。ステータス変更は §5.4 と同一の内部処理(= ユーザー操作。P6)。`action: "promote_revision"` バリアント(reason=promotion_b_to_a)の `apply` は §6.8 adopt-revision と同一の内部処理。既に resolved のものは 409 `conflict`。

## 17. settings — 設定(4f・キー名確定)

### 17.1 GET /api/settings

- 認証: `session`。全設定オブジェクトを返す(既定値を含む完全形):

```json
{
  "display": {
    "theme": "system",
    "accent": "#3E5C76",
    "body_font": "serif",
    "font_size_px": 16.5,
    "line_height": 2.15,
    "content_width_px": 720
  },
  "translation": {
    "default_style": "natural",
    "auto_translate_appendix": false,
    "translate_table_cells": false,
    "suggest_section_selection_over_30_pages": true
  },
  "reading": {
    "track_reading_time": true,
    "status_transition": "suggest"
  },
  "chat": {
    "include_annotations_and_notes": true
  },
  "notifications": {
    "translation_complete": true,
    "status_suggestion": true,
    "deadline_reminder": true
  },
  "extension": {
    "arxiv_inline_button": false
  },
  "llm_routing": {
    "translation":   { "provider": "deepseek",  "model": "deepseek-v4-flash" },
    "retranslation": { "provider": "anthropic", "model": "claude-opus-4-8" },
    "chat":          { "provider": "anthropic", "model": "claude-opus-4-8" },
    "summary":       { "provider": "anthropic", "model": "claude-opus-4-8" },
    "article":       { "provider": "anthropic", "model": "claude-opus-4-8" },
    "vocab":         { "provider": "anthropic", "model": "claude-haiku-4-5" },
    "figure_dsl":    { "provider": "anthropic", "model": "claude-opus-4-8" },
    "figure_image":  { "provider": "google",    "model": "gemini-3.1-flash-image" },
    "overview_figure_raster_mode": false
  }
}
```

- 値域: `theme ∈ light|dark|system` / `accent ∈ #3E5C76|#4A6B57|#6E5A7E|#7A5C48` / `body_font ∈ serif|sans`(serif=Noto Serif JP、sans=IBM Plex Sans JP) / `font_size_px ∈ 14–20(0.5刻み)` / `line_height ∈ 1.6–2.4(0.05刻み)` / `content_width_px ∈ 600–840(20刻み)` / `status_transition ∈ auto|suggest|off`。
- UI 文言との対応: 4f トグル「付録(Appendix)を自動翻訳しない」ON = `auto_translate_appendix: false`、「表のセル内テキストを翻訳しない」ON = `translate_table_cells: false`(UI 表示が否定形、API キーは肯定形。変換は web 層)。
- `llm_routing.*.provider ∈ openai|anthropic|google|deepseek`(`figure_image` のみ `openai|google|xai`)。`model` は自由文字列(モデル ID をハードコードしない。docs/09 §3.2)。設定可能なモデル一覧は運営設定 API ではなく `GET /api/settings` の付帯フィールド `available_models: Record<provider, {model, label}[]>` で配信する。

### 17.2 PATCH /api/settings

- 部分更新(deep merge。指定キーのみ)→ **200** 全設定オブジェクト。値域違反は 422。

### 17.3 BYOK API キー

```
GET    /api/settings/api-keys        → 200 { items: { provider, masked, created_at }[] }   // masked 例 "sk-…3fA"
PUT    /api/settings/api-keys/{provider}   Request: { api_key: string } → 200 { provider, masked, created_at }
DELETE /api/settings/api-keys/{provider}   → 204
```
- `provider ∈ openai|anthropic|google|deepseek|xai`。保存時に AES-256-GCM で暗号化(鍵は KMS/環境変数 `API_KEY_ENC_KEY`)。**平文の再表示 API は存在しない**(docs/09 §4)。PUT は上書き(再入力のみ)。

### 17.4 GET /api/settings/quota

```ts
Response 200: {
  period: string;                    // "2026-07"(月境界は JST(Asia/Tokyo)の暦月)
  byok_active: { text: boolean; image: boolean };   // BYOK 設定済みならクォータ非消費
  usage: {                           // 5 カウンタ制(plans/07 §9.2 が正)
    translation_papers:  { used: number; limit: number };   // 全文翻訳本数
    chat_messages:       { used: number; limit: number };
    images:              { used: number; limit: number };   // 解説図・ラスター概要図
    article_generations: { used: number; limit: number };   // 記事生成・再生成・ブロック書き直し・概要図書き直し
    vocab_generations:   { used: number; limit: number };   // 語彙の初回生成・再生成
  };
}
```
- 超過時挙動(**決定**): チャット・画像・語彙・記事・再翻訳は **429** `quota_exceeded`。取り込みパイプラインの全文翻訳のみジョブを `waiting_quota` で停止し(取り込み自体は成功・書誌と構造化は完了)、通知とジョブ detail で明示する。BYOK 登録で待機ジョブは自動再開する。

## 18. export — エクスポート(P5)

| エンドポイント | 認証 | レスポンス |
|---|---|---|
| `GET /api/library-items/{id}/export/markdown` | session | **200** `text/markdown` 添付。論文単位(Obsidian 互換): 書誌+メモ+注釈+チャット履歴+リソース一覧(種類・タイトル・URL・メモ。§チップは `§2.2` テキスト化)。ファイル名(決定): arXiv 論文は `{arxiv_id}.md`(例 `2209.03003.md`)、それ以外はタイトルの ASCII slug(小文字・ハイフン区切り・最大 80 字)`.md` |
| `GET /api/library-items/{id}/export/annotations` | session | **200** `text/markdown` 添付。注釈のみ(1b「⤓ Markdown エクスポート」) |
| `GET /api/export/bibtex` | session | **200** `application/x-bibtex` 添付。クエリは §5.1 と同一のフィルタ群(無指定=全件) |
| `GET /api/export/csv` | session | **200** `text/csv`(UTF-8 BOM 付き)。列: `title,authors,year,venue,arxiv_id,doi,status,priority,deadline,tags,quality,added_at,finished_at,reading_hours,comprehension,importance` |
| `POST /api/export/full` | session | **202** `{ job_id }`(kind=`export_full`。全量 JSON: ライブラリ・注釈・メモ・チャット・語彙(SRS 含む)・リソース・記事・コレクション・設定) |
| `GET /api/export/full/{job_id}` | session | **200** `{ job: Job; download_url: string | null }`(署名 URL、有効 24 時間) |

## 19. articles — 記事ビュー

### 19.1 GET /api/library-items/{id}/article

- 認証: `session` → 未生成なら **404** `not_found`(UI は生成 CTA を出す)

```ts
Response 200: Article
type Article = {
  id: string; library_item_id: string;
  title: string;                     // AI 生成日本語タイトル
  preset: Preset; include_math: boolean;
  version: number; generated_at: string;
  disclaimer: string;                // 逐語「訳文・メモ・チャット履歴から自動構成 · 2026-07-06 · 元の論文とは別物です — 根拠チップから原文へ」
  overview_figure: OverviewFigureRef | null;    // §20.1
  blocks: ArticleBlock[];
};
type ArticleBlock = {
  id: string;                        // ablk_… 安定 ID(書き直し対象の指定に使う)
  type: "heading" | "paragraph" | "quote_source" | "figure_embed"
      | "explainer_figure" | "discussion" | "attribution";
  content: {
    heading?: { level: 2 | 3; text: string };
    markdown?: string;               // paragraph
    quote?: { text_en: string; anchor: AnchorRef };            // 原文引用+「原文で見る →」
    figure?: { figure_block_id: string; image_url: string; caption_ja: string;
               credit: string;       // 「出典: Liu et al., …(arXiv:2209.03003)」自動付記
               license_badge: string };                        // 「CC BY 4.0 — 転載可」
    figure_link_card?: { figure_display: string; message: string };  // 転載不可時の代替リンクカード
    explainer?: { figure_id: string; image_url: string; caption: string };  // §20.2
    discussion?: { items: { text: string; origin: "ai" | "user_highlight" }[] };
    attribution?: { text: string };  // 末尾出典。削除不可
  };
  evidence: { ref: number; display: string; anchor: AnchorRef }[];
  origin: "ai" | "user_highlight";
  locked: boolean;                   // attribution のみ true(書き直し・削除不可)
};
```

### 19.2 POST /api/library-items/{id}/article(初回生成)

```ts
Request: { preset: Preset; include_math?: boolean }   // include_math 既定はプリセット属性(決定: beginner=false / implementer=true / researcher=true / reading_group=false。docs/07 §2.6)
Response 202: { job_id: string }                       // kind=article_generate。完了で version=1
```
- 既に記事が存在する場合は 409 `conflict`(再生成は §19.3)。クォータ超過 429。

### 19.3 POST /api/articles/{article_id}/regenerate(✦ 指示つき再生成)

```ts
Request: { instruction?: string; preset?: Preset; include_math?: boolean }
Response 202: { job_id: string }    // 完了で version+1(旧版は保持)
```

### 19.4 版管理

```
GET  /api/articles/{article_id}/versions            → 200 { items: { version, generated_at, preset, instruction }[] }
POST /api/articles/{article_id}/versions/{version}/restore → 200: Article(指定版を最新版として複製。version は+1)
```

### 19.5 POST /api/articles/{article_id}/blocks/{block_id}/rewrite(ブロック書き直し・再生成)

```ts
Request: { instruction?: string }    // 省略=指示なし再生成
Response 202: { job_id: string }     // kind=article_block_rewrite。完了時 result: { block: ArticleBlock }(該当ブロックのみ差替。記事 version は変えない)
```
- `locked: true`(出典ブロック)は 403 `forbidden`。「根拠を表示」は API 不要(`evidence` を既に保持)。

## 20. figures — 概要図・解説図

### 20.1 全体概要図(OverviewFigure)

```
GET  /api/articles/{article_id}/overview-figure
POST /api/articles/{article_id}/overview-figure/rewrite
POST /api/articles/{article_id}/overview-figure/versions/{version}/restore
GET  /api/overview-figures/{figure_id}/versions/{version}/svg
```
```ts
type OverviewFigureRef = {
  id: string; version: number; generated_at: string;
  svg_url: string;                   // = /api/overview-figures/{id}/versions/{v}/svg
  raster_url: string | null;         // ラスター生成モード時のみ(設定 llm_routing.overview_figure_raster_mode)
  evidence: { display: string; anchor: AnchorRef }[];   // フッタ「根拠: §1 / §2.2 / 表1」
  dsl: OverviewFigureDsl;
};
type OverviewFigureDsl = {           // 構造化図データ(SVG 決定的レンダリングの入力)
  layout: "flow-3";
  cards: { role: "problem" | "proposal" | "result";
           label: string;            // 「課題」「提案 — RECTIFIED FLOW」「結果」
           heading: string; body: string;
           tone: "neutral" | "accent" | "green" }[];
  connectors: { from: number; to: number }[];   // カード index
  footer: { generated_by: string; date: string };  // 「✦ AI 生成 · Alinea · 2026-07-06」
};

GET Response 200: OverviewFigureRef & { versions: { version: number; generated_at: string }[] }
POST rewrite Request: { instruction?: string } → 202 { job_id }   // kind=overview_figure。完了で version+1
POST restore → 200: OverviewFigureRef
GET svg → 200 image/svg+xml(Content-Disposition: attachment は ?download=true 時)
```
- SVG は同一 DSL から**バイト同一**で再生成できる決定的レンダラ(実装は plans/07 §5.4 の `alinea_figures.overview_svg`)。SVG 内にフッタ(AI 生成表記)を含む。

### 20.2 解説図(ExplainerFigure)

```
POST /api/explainer-figures/{figure_id}/regenerate
```
```ts
Request: { instruction?: string }
Response 202: { job_id: string }     // kind=explainer_figure。設定 llm_routing.figure_image のプロバイダで生成
```
- 解説図の新規生成は記事生成・再生成(§19.2/19.3)に付随し、単体の新規作成 API は持たない(docs/07 §1.4)。図メタ(provider/model/prompt/version)は記事ブロックの `explainer` 経由で参照するアセットに記録する。

## 21. jobs — 非同期ジョブと進捗 SSE

### 21.1 GET /api/jobs/{job_id}

- 認証: `session|ext` → `Response 200: Job`(他ユーザーのジョブは 404)

### 21.2 GET /api/jobs/{job_id}/events(進捗 SSE)

- 認証: `session`(EventSource。クッキーのみ)/ `Last-Event-ID` 再開対応

```
id: 12
event: progress
data: {"job_id":"job_01JZ…","status":"running","stage":"translating_body","progress_pct":68,"detail":"§3 まで読めます · 開いたセクションを優先翻訳","readable_upto":"§3"}

id: 13
event: done
data: {"job_id":"job_01JZ…","status":"succeeded","result":{"library_item_id":"li_01JZ…"}}

event: error
data: {"type":"https://alinea.app/problems/ingest-failed","title":"取り込みに失敗しました","status":502,"code":"ingest_failed","detail":"stage=parsing: テキストが抽出できません","retryable":true}
```

- `progress` はステージ遷移時+進捗 5% 刻みで送出。ジョブが既に終了済みなら接続直後に `done`/`error` を 1 回送って閉じる。
- `waiting_quota` は `event: progress` の `status: "waiting_quota"` として通知する。

### 21.3 GET /api/library-items/{id}/jobs

- 認証: `session` / クエリ: `active=true`(queued/running/waiting_quota のみ) → `Response 200: { items: Job[] }`

## 22. assets / telemetry — 画像・ファイル配信と品質テレメトリ

### 22.1 GET /api/assets/{asset_id}

- 認証: `session`(所有チェック: public Paper 由来は全ログインユーザー可、private・個人生成物は所有者のみ) → **302** 署名付き URL(有効 10 分)。図画像・サムネイル・生成ラスター・SVG 原データの配信を一元化する。
- クエリ: `download=true` で `Content-Disposition: attachment` を署名 URL に含める。

### 22.2 POST /api/telemetry(品質テレメトリ)

- 認証: `session`(「訳がおかしい?」— 1b 対訳ポップのフッター。docs/03 §9、plans/06 §9)
```ts
Request: { kind: "translation_doubt"; unit_id: string }   // v1 の kind はこの 1 値のみ
Response 204
```
- 保存先(決定): DB には保存せず、構造化アプリログ(stdout JSON)への記録のみ(v1 は集計をログ基盤で行う。DB 保存は v2)。unit_id の実在検証は行わない(fire-and-forget)。

## 23. エンドポイント索引(全 135 エンドポイント)

| # | メソッド+パス | 認証 | 節 |
|---|---|---|---|
| 1 | GET /api/auth/oauth/{provider}/start | anonymous | 2.1 |
| 2 | GET /api/auth/oauth/{provider}/callback | anonymous | 2.2 |
| 3 | POST /api/auth/email/request | anonymous | 2.3 |
| 4 | GET /api/auth/email/verify | anonymous | 2.4 |
| 5 | POST /api/auth/logout | session | 2.5 |
| 6 | GET /api/auth/me | session\|ext | 2.6 |
| 7 | POST /api/auth/extension-token | session | 2.7 |
| 8 | DELETE /api/auth/account | session | 2.8 |
| 9 | GET /api/ingest/check | session\|ext | 3.1 |
| 10 | POST /api/ingest/arxiv | session\|ext | 3.2 |
| 11 | POST /api/ingest/pdf | session\|ext | 3.3 |
| 12 | GET /api/ingest/recent | session\|ext | 3.4 |
| 13 | GET /api/papers/{paper_id} | session | 4.1 |
| 14 | POST /api/papers/{paper_id}/reingest | session | 4.2 |
| 15 | GET /api/papers/{paper_id}/ingest-log | session | 4.3 |
| 16 | GET /api/papers/{paper_id}/pdf | session | 4.4 |
| 17 | GET /api/library-items | session | 5.1 |
| 18 | GET /api/library-items/facets | session | 5.2 |
| 19 | GET /api/library-items/{id} | session | 5.3 |
| 20 | PATCH /api/library-items/{id} | session\|ext | 5.4 |
| 21 | DELETE /api/library-items/{id} | session | 5.5 |
| 22 | POST /api/library-items/bulk | session | 5.6 |
| 23 | PUT /api/library-items/queue-order | session | 5.7 |
| 24 | PUT /api/library-items/{id}/position | session | 5.8 |
| 25 | POST /api/library-items/{id}/reading-sessions | session | 5.9 |
| 26 | DELETE /api/library-items/{id}/tag-suggestions/{tag} | session | 5.10 |
| 27 | POST /api/library-items/{id}/duplicate-resolution | session | 5.11 |
| 28 | GET /api/dashboard | session | 5.12 |
| 29 | GET /api/tags | session | 5.13 |
| 30 | GET /api/saved-filters | session | 5.14 |
| 31 | POST /api/saved-filters | session | 5.14 |
| 32 | PATCH /api/saved-filters/{id} | session | 5.14 |
| 33 | DELETE /api/saved-filters/{id} | session | 5.14 |
| 34 | GET /api/library-items/{id}/viewer | session | 6.1 |
| 35 | GET /api/papers/{paper_id}/revisions | session | 6.2 |
| 36 | GET /api/revisions/{revision_id}/document | session | 6.3 |
| 37 | GET /api/revisions/{revision_id}/blocks/{block_id} | session | 6.4 |
| 38 | GET /api/revisions/{revision_id}/figures | session | 6.5 |
| 39 | GET /api/revisions/{revision_id}/references | session | 6.6 |
| 40 | GET /api/revisions/{revision_id}/search | session | 6.7 |
| 41 | POST /api/library-items/{id}/adopt-revision | session | 6.8 |
| 42 | GET /api/revisions/{revision_id}/translations | session | 7.1 |
| 43 | GET /api/revisions/{revision_id}/translations/{style}/units | session | 7.2 |
| 44 | POST /api/revisions/{revision_id}/translations | session | 7.3 |
| 45 | POST /api/translation-sets/{set_id}/prioritize | session | 7.4 |
| 46 | POST /api/translation-sets/{set_id}/sections/{section_id}/translate | session | 7.5 |
| 47 | POST /api/translation-units/{unit_id}/retranslate | session | 7.6 |
| 48 | PUT /api/translation-units/{unit_id} | session | 7.7 |
| 49 | POST /api/translation-units/{unit_id}/proposal/accept | session | 7.8 |
| 50 | DELETE /api/translation-units/{unit_id}/proposal | session | 7.8 |
| 51 | GET /api/glossary/terms | session | 7.9 |
| 52 | POST /api/glossary/terms | session | 7.9 |
| 53 | PATCH /api/glossary/terms/{term_id} | session | 7.9 |
| 54 | DELETE /api/glossary/terms/{term_id} | session | 7.9 |
| 55 | POST /api/glossary/terms/{term_id}/promote | session | 7.9 |
| 56 | GET /api/library-items/{id}/annotations | session | 8.1 |
| 57 | POST /api/library-items/{id}/annotations | session | 8.2 |
| 58 | PATCH /api/annotations/{annotation_id} | session | 8.3 |
| 59 | DELETE /api/annotations/{annotation_id} | session | 8.4 |
| 60 | GET /api/library-items/{id}/notes | session | 9 |
| 61 | POST /api/library-items/{id}/notes | session | 9 |
| 62 | PATCH /api/notes/{note_id} | session | 9 |
| 63 | DELETE /api/notes/{note_id} | session | 9 |
| 64 | GET /api/library-items/{id}/chat/threads | session | 10.1 |
| 65 | POST /api/library-items/{id}/chat/threads | session | 10.1 |
| 66 | PATCH /api/chat/threads/{thread_id} | session | 10.1 |
| 67 | DELETE /api/chat/threads/{thread_id} | session | 10.1 |
| 68 | GET /api/chat/threads/{thread_id}/messages | session | 10.2 |
| 69 | POST /api/chat/threads/{thread_id}/messages (SSE) | session | 10.3 |
| 70 | POST /api/chat/messages/{message_id}/regenerate (SSE) | session | 10.4 |
| 71 | POST /api/chat/threads/{thread_id}/summarize-to-note | session | 10.5 |
| 72 | GET /api/vocab | session | 11.1 |
| 73 | POST /api/vocab | session | 11.2 |
| 74 | GET /api/vocab/{vocab_id} | session | 11.3 |
| 75 | PATCH /api/vocab/{vocab_id} | session | 11.4 |
| 76 | DELETE /api/vocab/{vocab_id} | session | 11.5 |
| 77 | POST /api/vocab/{vocab_id}/regenerate | session | 11.6 |
| 78 | GET /api/vocab/review-queue | session | 11.7 |
| 79 | POST /api/vocab/{vocab_id}/review | session | 11.8 |
| 80 | GET /api/vocab/export/markdown | session | 11.9 |
| 81 | GET /api/library-items/{id}/resources | session | 12.1 |
| 82 | POST /api/library-items/{id}/resources | session | 12.2 |
| 83 | PATCH /api/resources/{resource_id} | session | 12.3 |
| 84 | DELETE /api/resources/{resource_id} | session | 12.4 |
| 85 | POST /api/resources/{resource_id}/refresh-meta | session | 12.5 |
| 86 | POST /api/library-items/{id}/resource-suggestion/accept | session | 12.6 |
| 87 | POST /api/library-items/{id}/resource-suggestion/dismiss | session | 12.6 |
| 88 | GET /api/collections | session | 13.1 |
| 89 | POST /api/collections | session | 13.1 |
| 90 | GET /api/collections/{collection_id} | session | 13.1 |
| 91 | PATCH /api/collections/{collection_id} | session | 13.1 |
| 92 | DELETE /api/collections/{collection_id} | session | 13.1 |
| 93 | POST /api/collections/{collection_id}/entries | session | 13.2 |
| 94 | PATCH /api/collection-entries/{entry_id} | session | 13.2 |
| 95 | DELETE /api/collection-entries/{entry_id} | session | 13.2 |
| 96 | PUT /api/collections/{collection_id}/entries/order | session | 13.2 |
| 97 | POST /api/collections/{collection_id}/share | session | 13.3 |
| 98 | PATCH /api/collections/{collection_id}/share | session | 13.3 |
| 99 | DELETE /api/collections/{collection_id}/share | session | 13.3 |
| 100 | GET /api/share/collections/{token} | anonymous | 14.1 |
| 101 | GET /api/search | session | 15.1 |
| 102 | GET /api/search/preview | session | 15.2 |
| 103 | GET /api/notifications | session | 16.1 |
| 104 | PATCH /api/notifications/{notification_id} | session | 16.2 |
| 105 | POST /api/notifications/read-all | session | 16.3 |
| 106 | POST /api/notifications/{notification_id}/action | session | 16.4 |
| 107 | GET /api/settings | session | 17.1 |
| 108 | PATCH /api/settings | session | 17.2 |
| 109 | GET /api/settings/api-keys | session | 17.3 |
| 110 | PUT /api/settings/api-keys/{provider} | session | 17.3 |
| 111 | DELETE /api/settings/api-keys/{provider} | session | 17.3 |
| 112 | GET /api/settings/quota | session | 17.4 |
| 113 | GET /api/library-items/{id}/export/markdown | session | 18 |
| 114 | GET /api/library-items/{id}/export/annotations | session | 18 |
| 115 | GET /api/export/bibtex | session | 18 |
| 116 | GET /api/export/csv | session | 18 |
| 117 | POST /api/export/full | session | 18 |
| 118 | GET /api/export/full/{job_id} | session | 18 |
| 119 | GET /api/library-items/{id}/article | session | 19.1 |
| 120 | POST /api/library-items/{id}/article | session | 19.2 |
| 121 | POST /api/articles/{article_id}/regenerate | session | 19.3 |
| 122 | GET /api/articles/{article_id}/versions | session | 19.4 |
| 123 | POST /api/articles/{article_id}/versions/{version}/restore | session | 19.4 |
| 124 | POST /api/articles/{article_id}/blocks/{block_id}/rewrite | session | 19.5 |
| 125 | GET /api/articles/{article_id}/overview-figure | session | 20.1 |
| 126 | POST /api/articles/{article_id}/overview-figure/rewrite | session | 20.1 |
| 127 | POST /api/articles/{article_id}/overview-figure/versions/{version}/restore | session | 20.1 |
| 128 | GET /api/overview-figures/{figure_id}/versions/{version}/svg | session | 20.1 |
| 129 | POST /api/explainer-figures/{figure_id}/regenerate | session | 20.2 |
| 130 | GET /api/jobs/{job_id} | session\|ext | 21.1 |
| 131 | GET /api/jobs/{job_id}/events (SSE) | session | 21.2 |
| 132 | GET /api/library-items/{id}/jobs | session | 21.3 |
| 133 | GET /api/assets/{asset_id} | session | 22.1 |
| 134 | POST /api/translation-sets/{set_id}/section-selection | session | 7.10 |
| 135 | POST /api/telemetry | session | 22.2 |

## 24. 受け入れ基準(API 層)

- [ ] OpenAPI スキーマから生成した TS クライアントが型エラーなしで apps/web / apps/extension をビルドできる(§1.10 の codegen が CI で強制されている)
- [ ] 全エラーレスポンスが `application/problem+json`(RFC 7807+`code`)で返り、Pydantic の生エラーが露出しない
- [ ] 拡張トークンでスコープ外 API を呼ぶと 403 `token_scope_exceeded` になる
- [ ] `GET /api/ingest/check` が保存済み判定(前回位置・進捗つき)と `latex_available`(品質 A 見込み)を 1 リクエストで返す
- [ ] `POST /api/ingest/arxiv` の重複が 409+既存レコード情報で返り、`Idempotency-Key` 再送で二重登録が起きない
- [ ] ライブラリ一覧のクイックフィルタ・属性フィルタ 5 種・保存フィルタ・10 列ソートが §5.1 のクエリ仕様どおりに動き、facets の件数合計が総数と一致する
- [ ] チャット送信 SSE が start/delta/evidence/done/error のイベント形式に従い、実在しない根拠チップが配信前に除去される
- [ ] 匿名で `GET /api/share/collections/{token}` を取得でき、`X-Robots-Tag: noindex` が付与され、書誌+要約+許可メモ以外の個人資産が含まれない
- [ ] クォータ超過時、チャット等は 429 `quota_exceeded`、取り込みの翻訳段のみ `waiting_quota` で待機し、BYOK 登録で自動再開する
- [ ] 横断検索で同一ブロックの原文・訳文ヒットが 1 件に統合され(`matched_in`)、源別 target で該当位置へ遷移できる
