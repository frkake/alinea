# 13. 実装タスク分解(WBS)— マイルストーン別チケット一覧

> **対象読者と前提**: 本書は「Alinea」の実装者(バックエンド・フロントエンド・拡張の全担当)と進行管理者向けの作業分解構造(WBS)である。機能仕様は docs/00〜12、実装詳細は plans/00〜12(09-screens/ 含む)を正とし、本書はそれらを **着手可能なチケット粒度のタスク**に分解し、依存関係・実装順序・各マイルストーンの完成判定(DoD と受け入れテスト)を確定する。マイルストーン定義は docs/10、テスト ID は plans/12 を唯一の正とする。見積もり時間は書かない(規模 S/M/L のみ)。

## 1. WBS の規約

### 1.1 タスク ID と粒度

- タスク ID は `M{マイルストーン}-{連番2桁}`(例 `M0-17`)。連番は**推奨着手順**(トポロジカル順)で振る。
- 1 タスク = 1 PR 系列で完結し、完了時に「主な成果物」列の全ファイル・テーブル・コンポーネントが存在し、対応するテスト(§1.3)がグリーンであること。
- 規模の定義(見積もりではなく複雑度の目安): **S** = 単一モジュール・既存パターンの踏襲で閉じる / **M** = 複数モジュールにまたがる 1 機能 / **L** = サブシステム(新しい設計判断・複数レイヤ貫通)。

### 1.2 依存関係の表記

- 「依存」列のタスクが**すべて完了するまで着手しない**(部分着手も不可。インターフェース定義だけ先行したい場合は依存元タスクを分割せず、依存元の完了を待つ)。
- 依存列が同一の複数タスクは**並列着手可**。M0 は §2.2 のレーン図で直列・並列関係を明示する。

### 1.3 テストの割付規則

- pytest(PY-\*/HP-\*)・Vitest(VT-\*)は**実装タスク自身の成果物**に含める(実装 PR と同時にマージ)。マイルストーン末尾のテストタスク(M0-38 形式)は、スイート全体の通し実行・トレーサビリティ確認・取りこぼしの補完を担う。
- Playwright(PW-\*)・ビジュアルリグレッション(VR-\*)・拡張 E2E(XT-\*)は各マイルストーンの E2E タスクで有効化する。
- **決定**: 複数マイルストーンにまたがるアサーションを含む E2E(例 PW-05 の「5 モード切替」、PW-08 の「↑メモに保存」)は、未実装スコープのアサーションを `test.fixme()` ブロックで明示して先にマージし、担当マイルストーンで解除する。最終有効化マイルストーンは §7 の割付表を正とする。理由: テストファイルの所在を 1 箇所に固定し、スコープ解除漏れを fixme 残数(CI レポート)で機械検出できるため。

### 1.4 スキーマ投入方針(本書で確定)

- **決定**: PostgreSQL の DDL は plans/02 §4 の**全量(全テーブル・全インデックス・PGroonga 索引・トリガ)を M0 の初期マイグレーション 1 本**(`apps/api/alembic/versions/0001_initial_schema.py`)で投入する。機能の実装はマイルストーンに従って段階的に進めるが、スキーマは最初から完成形とする。理由: PY-DB-01(全テーブル存在検証)を M0 から常時グリーンに保て、マイルストーン境界での ALTER 連鎖と Alembic 履歴の複雑化を避けられるため。以後のマイグレーションは計画からの逸脱(バグ修正)のみ。

### 1.5 UI の未実装スコープの扱い(本書で確定)

- **決定**: 未実装マイルストーンの UI 要素(サイドパネルのタブ・表示モードスイッチャーの項目・設定カテゴリ)は**非表示**とする(グレーアウトの無効表示にしない)。理由: 押せない UI は P2(1 アクション)と P3(黙って壊れない)に反し、E2E の否定検査(PW-02 方式)とも整合するため。
  - M0 のサイドパネルは チャット/図表/情報 の 3 タブのみ表示(メモ・注釈 = M1、リソース = M2 で追加)。
  - M0 の表示モードは 訳文/対訳/原文 の 3 つのみ表示(PDF = M1、記事 = M2 で追加)。
- **決定**: ライブラリテーブル(1e)の 10 列は M0 から全列描画し、データ供給機能が未実装の列(優先度・締切・読書時間・理解度)は「—」を表示する。理由: F2「テーブルは 10 列固定」のレイアウトを M0 から確定デザインと一致させ、VR 基準画像の差し替えを防ぐため。
- **決定**: M0 のログイン後リダイレクト先は `/library` とし、M1(ダッシュボード実装)で `/dashboard` に切り替える。理由: M0 に 1d が存在しないため。

## 2. M0: コア読解体験(MVP)

**DoD(docs/10 §2)**: 「拡張から arXiv 論文を保存すると 1 分以内に読み始められ、数式・図表が崩れていない日本語で論文を読み通せ、選択して質問すると根拠つきで答えが返り、読みかけの位置が保存される。」

### 2.1 M0 のスコープ境界(docs/10 §2 の確定を再掲)

- 取り込みは拡張の arXiv URL 経路のみ(品質 A、arXiv HTML パーサ主経路)。一般 PDF・ピルは M1。
- ビューアは 3 モード+サイドパネル 3 タブ(チャット/図表/情報)。選択メニューは「✦AIに質問」「コピー」の 2 項目(4 色ハイライト・コメント = M1、語彙に追加 = M2)。
- 対訳ポップの「訳がおかしい?」→再翻訳(指示なし)は M0。指示つき再翻訳・proposal・手動編集は M1。
- 通知は M0 に含めない(拡張は 2,000ms ポーリングで進捗取得 — plans/10 §7.2)。取り込みパイプラインの通知発火コード(plans/05 §12)は M1-07 で実装する。
- 参考文献一覧の表示は M0(図表タブ内)、「+この論文も取り込む」導線は M1(docs/10 §2 の決定)。
- 拡張ポップアップ保存前フォームの「コレクション」欄は M2 まで非表示(docs/10 §2 の決定)。

### 2.2 M0 実装順序(レーンと直列・並列関係)

```
フェーズ P0(基盤・直列)
  M0-01 → M0-03 ─┬→ M0-04(CI)
  M0-02(並行可) └→ M0-06 → M0-07
フェーズ P1(3 レーン並列。P0 完了後)
  レーン LLM   : M0-08 → M0-09 → M0-13
  レーン API基盤: M0-10 → M0-11 → M0-12
  レーン FE基盤 : M0-05 → M0-26 → M0-27
フェーズ P2(パイプライン・直列が主)
  M0-14 → M0-15 ─┐
  M0-16 ─────────┴→ M0-17 → M0-18(クリティカルパス)
フェーズ P3(API 面。M0-18 後に並列)
  M0-19 / M0-20 / M0-21 / M0-22 / M0-23 → M0-24(全ルート確定後)
  M0-25(シード。M0-07 後いつでも)
フェーズ P4(画面。M0-24 と M0-27 後に並列)
  M0-28 → M0-29 → M0-30 / M0-31(1c は 1b 後)
  M0-32 / M0-33(並列)
フェーズ P5(拡張。M0-24 後に直列)
  M0-34 → M0-35 → M0-36 → M0-37(ストア申請)
フェーズ P6(検証・リリース)
  M0-38(P3 完了後。P4・P5 と並走) / M0-39(P4・P5 後) / M0-40(P0 後いつでも) → M0-41
```

### 2.3 M0 タスク表

| ID | タスク名 | 内容 | 主な成果物 | 規模 | 依存 | 参照 |
|---|---|---|---|---|---|---|
| M0-01 | モノレポ初期化 | pnpm workspaces + Turborepo + uv workspace の骨格、ルート設定ファイル一式、`.env.example` 全変数 | `package.json` `pnpm-workspace.yaml` `turbo.json` `pyproject.toml`(uv workspace)`.node-version`(24.4.0)`.python-version`(3.12.11)`.env.example` `eslint.config.mjs` `.prettierrc.json` `renovate.json` | S | — | plans/00 §1, §2, §5 |
| M0-02 | 計画書間の ⚠ 不整合の確定反映 | plans/12 §15 の 9 件および plans/05 §13・06 §16・07 §12・10 §15・11 §11 の ⚠ 全項目(決定: 対象はこれら 6 節に列挙された項目の全部であり、これ以外を含めない)を、plans/12 §15 の「本書の暫定」を正として plans/00〜04 に反映する(jobs の kind/status 統一、ステータス列挙の DB↔API 対応表、LLM 層は `packages/llm`、シード入口は `python -m alinea_api.seed`、CSRF は Origin 検証、ベース URL 上書き環境変数の追加) | plans/00〜04 の修正コミット(コード変更なし) | S | — | plans/12 §15 |
| M0-03 | docker-compose 開発環境 | PostgreSQL 16 + PGroonga / Redis 7 / MinIO / Mailpit の完全形とローカル起動手順の成立 | `docker-compose.yml`(完全形)`README` 起動手順(plans/00 §9 の 8 ステップ) | S | M0-01 | plans/00 §3, §9 |
| M0-04 | CI 骨格 | GitHub Actions の 5 ジョブ(js / python / openapi-drift / e2e / extension)を空実装でもグリーンになる形で構築。以降の各タスクが自ジョブにテストを追加する | `.github/workflows/ci.yml` | M | M0-01 | plans/00 §8; plans/12 §13.1 |
| M0-05 | packages/tokens | `tokens.json`(_global.md 全値)→ `tokens.css` / `tokens.ts` 生成、アクセント 4 色導出(`--pr-as/-am/-ads/-adm`)、フォント読込 CSS、Tailwind v4 テーマ、VT-TOK-01 | `packages/tokens/src/tokens.json` `build.mjs` `css/tokens.css` `css/fonts.css` `css/theme.css` `src/accent.ts` `src/tokens.ts`、テスト VT-TOK-01 | M | M0-01 | plans/08 §1〜§4; extract/_global.md |
| M0-06 | DB モデルと完全 DDL | packages/py-core の雛形、SQLAlchemy 2 モデル全量、Alembic 初期マイグレーション 1 本(§1.4 の決定どおり plans/02 §4 の全テーブル: users / auth_identities / sessions / byok_api_keys / papers / source_assets / document_revisions / block_search_index / translation_sets / translation_units / glossaries / glossary_terms / library_items / chat_threads / chat_messages / notes / annotations / vocab_entries / resource_links / collections / collection_entries / collection_share_tokens / saved_filters / notifications / articles / article_blocks / overview_figures / explainer_figures / reading_sessions / jobs / usage_records+PGroonga 索引)。PY-DB-01〜12 | `packages/py-core/pyproject.toml` `src/alinea_core/db/`(models・session.py・ids.py)`apps/api/alembic/versions/0001_initial_schema.py`、テスト PY-DB-01〜12 | L | M0-01, M0-03 | plans/02 §4, §7; plans/11 §2.2 |
| M0-07 | py-core ドメイン基盤 | 構造化ドキュメント中間表現(Block/Inline/Section/Anchor の Pydantic モデル=plans/02 §3 の JSONB 契約)、ULID、S3 ラッパとキー設計、ライセンスマトリクス判定、`block_search_index` 再構築関数。PY-DB-13〜14・PY-LIC-01 | `alinea_core/document/`(blocks.py, inlines.py, anchor.py, stable_id.py)`storage/s3.py` `licenses.py`、テスト PY-DB-13, PY-DB-14, PY-LIC-01 | M | M0-06 | plans/02 §3; plans/01 §7; docs/09 §5.2 |
| M0-08 | packages/llm 抽象化層 | `LLMProvider`/`ImageProvider` プロトコル、共通型・エラー分類、5 社アダプタ(OpenAI/Anthropic/Google/DeepSeek/xAI)+画像 3 社、`models.yaml`/`routing.yaml`、ModelRegistry、LLMRouter(リトライ・フォールバック)、structured output 互換、count_tokens、`FakeLLMProvider`/`FakeImageProvider` | `packages/llm/src/alinea_llm/`(types.py, errors.py, protocols.py, registry.py, routing.py, router.py, structured.py, caching.py, tokens.py, providers/ 一式, testing/fake_provider.py)`models.yaml` `routing.yaml`、テスト PY-LLM-01〜07 | L | M0-01 | plans/04 全章; spec-decisions G |
| M0-09 | E2E 用モック LLM/外部サーバ | FakeLLM と同一の決定的応答を HTTP で提供(5 社エンドポイント+arXiv abs/e-print/Atom+GitHub/YouTube oEmbed 相当)。ベース URL 上書き環境変数(`ALINEA_OPENAI_BASE_URL` / `ALINEA_ANTHROPIC_BASE_URL` / `ALINEA_GOOGLE_BASE_URL` / `ALINEA_DEEPSEEK_BASE_URL` / `ALINEA_XAI_BASE_URL` の 5 種+`ALINEA_ARXIV_BASE_URL` — plans/12 §15-2, §15-3 の逐語)の実装 | `packages/llm/src/alinea_llm/testing/mock_server.py`(ポート 8090) | M | M0-08 | plans/12 §8.4, §15-2, §15-3 |
| M0-10 | API 共通基盤 | FastAPI アプリ生成、RFC 9457 Problem Details、cursor ページング、レート制限、SSE 共通、構造化ログ(structlog)、`/metrics`(plans/01 §9.4 の全メトリクス)。PF-01 | `apps/api/src/alinea_api/main.py` `settings.py` `deps.py` 共通スキーマ(plans/03 §1.7)、テスト PF-01 | M | M0-06 | plans/03 §1; plans/01 §9 |
| M0-11 | 認証 | authlib OAuth(Google/GitHub)+メールリンク(Mailpit 開発経路)、HTTPOnly セッションクッキー、Origin 検証 CSRF、拡張トークン(スコープ 7 エンドポイント)、アカウント削除カスケード | `routers/auth.py` `/api/auth/*` 8 エンドポイント、テスト PY-AUTH-01〜03, PY-DB-03 | M | M0-10 | plans/03 §2; plans/01 §6 |
| M0-12 | ジョブ基盤+SSE | `jobs` テーブル運用(claim・checkpoint 段階再開・指数バックオフ 30s→2min→8min・冪等性キー)、arq ワーカー骨格(InteractiveWorker/BulkWorker)、回復コマンド、ユーザー単位 SSE `GET /api/v1/events`(Redis Pub/Sub+Last-Event-ID 再送)、ジョブ API | `apps/worker/src/alinea_worker/`(main.py, settings.py)`python -m alinea_core.jobs.requeue` `routers/jobs.py`(plans/03 §21)、テスト PY-JOB-01, PY-JOB-03 | L | M0-10, M0-03 | plans/01 §4, §5; plans/03 §21 |
| M0-13 | LLM ルーティング・BYOK・クォータ | ルーティング設定テーブル(llm_models / llm_task_routes / user_task_model_overrides)、`DbKeyStore`(Fernet 暗号化・マスク表示)、`DbMeterHook`(usage_records)、月次クォータ判定と 429 `quota_exceeded`、`waiting_quota` 停止・再開 | `apps/api/src/alinea_api/llm/`(deps.py, key_store.py, meter.py, route_store.py。決定: plans/04 §10 の表記 `apps/api/app/llm/` は plans/00 §2 の src レイアウトに読み替える — M0-02 の統一対象)、テスト PY-SET-02〜04 の LLM 側 | M | M0-08, M0-11, M0-12 | plans/04 §10, §11, §15; plans/07 §9 |
| M0-14 | arXiv 解決 | URL/ID 正規化(新旧形式全パターン)、メタデータ API・OAI-PMH ライセンス取得と正規化表、LaTeX ソース有無判定(「品質レベル A 見込み」・Redis 24h キャッシュ)、取得レート制限 | `alinea_core/arxiv/`(ids.py, metadata.py, licenses.py, fetch.py)、テスト PY-ING-03 の判定部 | M | M0-07, M0-09 | plans/05 §3 |
| M0-15 | arXiv HTML パーサ | DOM→11 ブロック型+インライン 8 種の変換、ブロック安定 ID 生成、リビジョン間 carryover、品質 A の page+bbox 同期。KaTeX 数式コーパス検証 | `alinea_core/parsing/`(html_parser.py, block_ids.py, carryover.py, pdf_sync.py)、テスト PY-PARSE-01, PY-PARSE-04 | L | M0-07, M0-14 | plans/05 §4; docs/01 §4; docs/02 §3 |
| M0-16 | プレースホルダプロトコル | protect / restore / validate(全トークンちょうど 1 回)、source_hash、検証失敗時のプロンプト再構成再試行。Hypothesis プロパティテスト 4 種 | `alinea_core/translation/placeholder.py`、テスト PY-TR-01, HP-01〜04 | M | M0-07 | plans/06 §4; plans/12 §7; docs/03 §4 |
| M0-17 | 翻訳パイプライン | 自然訳プロンプト(system 2 層+バッチ user)、文脈パッキング、structured output スキーマ、自動品質検査 5 種、共有キャッシュ(shared/personal 解決)、用語スナップショット凍結、`translate_section` ジョブ、進捗計算(96% 表示への写像)、翻訳対象スコープ判定(付録・表セル・参考文献除外)と設定 4 項目の反映 | `alinea_core/translation/`(pipeline.py, glossary.py の snapshot 部, prompts/)`apps/worker/src/alinea_worker/tasks/translate.py`、テスト PY-TR-02〜07, PY-TR-10, PY-GLS の snapshot 部 | L | M0-16, M0-13 | plans/06 §2〜§7, §9, §12, §13; docs/03 |
| M0-18 | 取り込みステートマシン | `ingest_paper` の 8 段階(queued→…→complete)駆動、readable 段の先頭セクション直接翻訳、translating_abstract 段(アブスト訳+✦3行要約+提案タグ)、重複検知(完全一致+ファジー)、サムネイル、処理ログ・タイムライン 3 段、優先繰り上げ。通知発火は M1-07 に委譲 | `apps/worker/src/alinea_worker/tasks/ingest.py` `pipeline.py` `alinea_core/ingest/`(dedupe.py, thumbnail.py, joblog.py, progress.py)、テスト PY-ING-02, PY-ING-05, PY-JOB-02 | L | M0-12, M0-15, M0-17 | plans/05 §2, §7, §8, §10, §11; plans/07 §3.1; docs/02 §5 |
| M0-19 | ingest / papers API | `GET /api/ingest/check`(3 分岐)`POST /api/ingest/arxiv`(202+Idempotency-Key)`GET /api/ingest/recent`、papers 系(reingest・ingest-log・pdf 配信)、assets 配信 | `routers/ingest.py` `routers/papers.py` `routers/assets.py`(plans/03 §3, §4, §22)、テスト PY-ING-01, PY-ING-03, PY-ING-06 | M | M0-18, M0-11 | plans/03 §3, §4, §22; plans/05 §1.2 |
| M0-20 | viewer / translations API | ビューア初期化複合エンドポイント、document・blocks・figures・references、翻訳セット取得・units・prioritize・オンデマンドセクション翻訳・指示なし `retranslate`(「訳がおかしい?」)。読書位置 PUT | `routers/viewer.py`(決定: ファイル名はこれで確定。plans/03 §6 のエンドポイント群)`routers/translations.py`(§7.1〜7.6 の M0 分)`PUT /api/library-items/{id}/position`、テスト PY-LIB-03 | M | M0-18, M0-11 | plans/03 §6, §7, §5.8; plans/06 §10 |
| M0-21 | チャットバックエンド+API | 文脈ビルダー、ストリーム変換パイプライン(`[[ev:n]]`)、根拠実在検証、システムプロンプト、定型アクション 5 種+入力候補 2 種、SSE 送信 API、スレッド CRUD(メイン自動作成)、regenerate | `apps/api/src/alinea_api/chat/`(context_builder.py, stream_pipeline.py, evidence.py)`routers/chat.py`(plans/03 §10)、テスト PY-CHAT-01〜06 | L | M0-13, M0-15, M0-11 | plans/07 §2; plans/03 §10; docs/05 |
| M0-22 | ライブラリ API(M0 分) | 一覧(フィルタ結合規則・ソート・cursor)、facets、単体 GET/PATCH/DELETE、tags、タグ提案削除、ファジー統合確認 | `routers/library_items.py`(plans/03 §5.1〜5.5, 5.10, 5.11, 5.13)、テスト PY-LIB-01, PY-LIB-02 | M | M0-11, M0-06 | plans/03 §5; plans/11 §8.1, §8.2; docs/06 |
| M0-23 | 設定 API | `GET/PATCH /api/settings`(plans/03 §17.1 の全キー・deep merge・値域検証)、BYOK PUT/DELETE(マスク応答・平文再表示 API 不在)、`GET /api/settings/quota` | `routers/settings.py` `routers/llm_settings.py`、テスト PY-SET-01〜04 | M | M0-13 | plans/03 §17; plans/04 §11 |
| M0-24 | packages/api-client | OpenAPI 生成 TS クライアント+fetch ラッパ(`credentials: "include"`)、CI の openapi-drift ジョブ接続 | `packages/api-client/`(openapi-ts.config.ts, src/generated/, src/index.ts) | S | M0-19, M0-20, M0-21, M0-22, M0-23 | plans/03 §1.10; plans/00 §2, §8 |
| M0-25 | シードデータ | Rectified Flow(arXiv:2209.03003)フィクスチャ一式と投入コマンド(`python -m alinea_api.seed --sample rectified-flow [--reset] [--scale N] [--full]` — plans/12 §14.1 の逐語)。以後の全テスト・VR・開発の共通データ源 | `apps/api/src/alinea_api/seed_data/rectified_flow/`(bib.json, document.json, translation_natural.json ほか plans/12 §14.1 の全ファイル)`python -m alinea_api.seed` | M | M0-07, M0-06 | plans/12 §14; plans/00 §9(C10 シード) |
| M0-26 | web アプリシェル | Next.js App Router 骨格(`(app)`/`(public)`/`(auth)` セグメント)、layout(next/font・ThemeProvider・QueryClientProvider)、ログイン画面、AppHeader、SidebarNav、SSE クライアント(`/api/v1/events` 購読+ポーリングフォールバック) | `apps/web/src/app/layout.tsx` `(auth)/login/page.tsx` `components/`(AppHeader, SidebarNav)`lib/sse.ts` `styles/globals.css`、テスト VT-UI-01 | M | M0-05, M0-01 | plans/00 §2; plans/01 §5; plans/08 §4.2 |
| M0-27 | UI 共通コンポーネント(M0 分) | plans/08 §5 の 22 種のうち M0 で使う 17 種: SegmentedControl / StatusPill / QualityBadge / FilterChip / CountBadge / Toggle / Card / Popover / Modal / ProgressBar / SearchBox / SidebarNav / Table(LibraryTable)/ SidePanelTabs / EvidenceChip / AIBadge / Toast / EmptyState+SVG アイコン基盤(数え方の都合上 SidebarNav は M0-26 と共同所有)。残り(HighlightMark=M1-02、PriorityBadge・DeadlineBadge=M1-10)は該当タスクで追加。決定: plans/08 §5.22 の共通片は使用画面の初出タスクで実装する — Keycap・TagChip=M0-27、SelectionMenu・ResumeBanner=M0-29、SourceBadge=M1-13、BulkActionBar=M2-14 | `apps/web/src/components/ui/` 17 コンポーネント、テスト VT-UI-02, VT-UI-03 | L | M0-05, M0-26 | plans/08 §5, §6 |
| M0-28 | ビューアシェル | ViewerHeader、表示モード URL 契約(`?mode=`)、左レール 44px⇄目次ペイン 232px(進捗 96%・節 ✓・未翻訳付録「開くと翻訳します(オンデマンド)」)、サイドパネル(M0 は 3 タブ)、読書位置・読書セッションフック(位置保存のみ。時間集計は M1-05)、キーボードショートカット、SSE によるブロック差し替え(部分読書) | `components/viewer/`(ViewerShell, ViewerHeader, TocTree, SidePanel)`hooks/useReadingPosition.ts` `stores/viewer-store.ts`、テスト VT-VIEW-01, VT-VIEW-04 | L | M0-24, M0-27 | plans/09-screens/viewer-shell 全章 |
| M0-29 | 訳文モード(1b・M0 分) | 前回位置バナー(「続きから↓」)、✦3行要約カード(詳細要約→)、見出し原題併記、KaTeX ブロック(「✦この式を説明」「LaTeXをコピー」)、段落ホバー「対」+キー `t` 対訳ポップ(「訳がおかしい?」→再翻訳導線)、選択メニュー(M0 は ✦AIに質問/コピー の 2 項目)、ゆったり組版(16.5px・行間 2.15・720px) | `components/viewer/`(TranslationPane, SectionHeading, ParallelPopover, SelectionMenu, SummaryCard, ResumeBanner)`lib/katex-render.ts`、テスト VT-VIEW-02, VT-VIEW-05 | L | M0-28, M0-20 | plans/09-screens/1b; docs/04 §2, §3 |
| M0-30 | 対訳モード+チャットパネル(1a) | 段落単位 2 カラム+「段落対応⇄」、チャットタブ(スレッド切替・定型チップ 5 種・「AI生成」バッジ・「論文外の知識」ボックス・根拠チップ ¶ 粒度・本文側「✦チャットの根拠」双方向同期・再生成・コピー・免責文固定)。「↑メモに保存」ボタンは M1-04 で追加 | `components/viewer/`(BilingualPane, TranslationColumnHeader)`components/chat/`(ChatPanel, ChatMessage, ChatComposer, QuickActionChips, EvidenceHighlight)、テスト VT-VIEW-03, VT-VIEW-07, VT-VIEW-09〜12 | L | M0-28, M0-21, M0-29 | plans/09-screens/1a; docs/05 |
| M0-31 | ダーク+図表(1c)+情報タブ(M0 分) | ダークモード(`data-theme` 切替・FOUC 防止)、図表参照のその場ポップオーバー(両言語キャプション・「図の位置へ移動→」「拡大」「✦この図を説明」)、図表タブ(図表一覧+参考文献一覧表示)、情報タブの M0 要素(書誌・品質バッジ説明文・ライセンスカード) | `components/viewer/`(FigureRefPopover, FiguresPanel, ReferencesList, InfoPanel)`ThemeToggle`、テスト VT-VIEW-06 | M | M0-28, M0-29 | plans/09-screens/1c; plans/08 §8 |
| M0-32 | ライブラリ画面(M0 分) | 1e テーブルビュー(10 列固定・§1.5 の「—」規則・クイックフィルタ 5 種件数付き・基本ソート)+4a カードビュー(✦3行要約・パイプライン進捗・タグ提案チップ・「読み始める」部分読書導線)+ビュー切替。属性フィルタ・保存フィルタ・一括操作は M2-14、検索ドロップダウンは M1-13、通知は M1-08 | `app/(app)/library/page.tsx` `components/library/`(LibraryTable, LibraryCard, QuickFilterBar, ViewSwitch)、テスト VT-LIB-01, VT-LIB-02 の 10 列部 | L | M0-27, M0-22, M0-24 | plans/09-screens/1e §3〜5; 4a §3〜4; docs/06 |
| M0-33 | 設定画面(M0 分) | 4f のカテゴリ骨格と「アカウント」カテゴリ(BYOK 登録・マスク表示・プロバイダ/モデル選択=llm_routing)+「翻訳」カテゴリの LLM 節。他カテゴリの実装は M1-17 | `app/(app)/settings/` レイアウト+account ページ、BYOK フォーム | M | M0-23, M0-27 | plans/09-screens/4f; plans/04 §11.3; docs/10 §2 |
| M0-34 | 拡張基盤 | WXT プロジェクト、manifest(permissions: activeTab+storage、optional_host_permissions: arxiv.org)、セッションクッキー共有 API クライアント、未ログイン UI、E2E フック(`WXT_E2E=1` 時の `?tab_url=`) | `apps/extension/`(wxt.config.ts, entrypoints/popup/App.tsx, lib/api.ts, lib/storage.ts, lib/arxiv.ts, lib/e2e-hooks.ts)、テスト VT-XTU-01 | M | M0-24, M0-11 | plans/10 §2〜§5; docs/08 |
| M0-35 | ポップアップ 3 状態 | 保存前(書誌プレビュー・「品質レベル A 見込み」・ステータス 3 択・タグ提案・ひとことメモ・Enter 保存)/保存直後(パイプライン 3 行表示・2,000ms ポーリング)/既にライブラリ(進捗・前回位置・続きから開く・ステータス変更)。コレクション欄は M2 まで非表示 | `entrypoints/popup/states/`(SaveForm.tsx, Saved.tsx, Existing.tsx)`lib/pipeline.ts` `lib/format.ts` | L | M0-34, M0-19 | plans/10 §5〜§8; plans/09-screens/3a |
| M0-36 | 拡張バッジ・直近 3 件 | ツールバー琥珀ドット(#C49432)の状態機械と MV3 ライフサイクル対応ポーリング、フッタ「直近の取り込み」3 件 | `entrypoints/background.ts` `RecentIngests.tsx`、アイコンアセット | M | M0-35 | plans/10 §8, §9 |
| M0-37 | ストア申請(Chrome/Edge) | zip ビルド・掲載文・スクリーンショット・プライバシー記載を提出。**審査待ち期間はクリティカルパスに計上**し、XT-01〜05 通過直後に提出する(リジェクト時の再申請余地を確保) | Chrome Web Store / Edge Add-ons の申請完了 | S | M0-36, M0-39(決定: §1.2 の例外として、M0-39 のうち XT-01〜05 のグリーンのみを待てば提出してよい。M0-39 の他成果物の完了は待たない — §6.1) | plans/10 §12; docs/10 §7 |
| M0-38 | pytest M0 スイート確定 | M0 実装タスク付随テストの通し実行、pytest 基盤(fixtures・factories・カバレッジ 80%+重点 2 モジュール 100%)、トレーサビリティ網羅チェックスクリプト、カセット秘匿 grep | `apps/api/tests/`(conftest.py, factories.py)`tools/traceability/check.py`、§7 の M0 列の PY-\*/HP-\* 全件グリーン | M | M0-19〜M0-23(完了後に着手。P4・P5 のタスクとは並走可) | plans/12 §2, §6, §8 |
| M0-39 | E2E・VR(M0 分) | Playwright 基盤(config・global.setup メールリンク認証・モックサーバ接続)、PW-01/02/03/09 全量+PW-05/07/08 の M0 スコープ、拡張 E2E XT-01〜05/07/09、VR-1a/1b/1c/3a-1〜3、perf.yml(PF-01/03/05/07)+llm-smoke.yml(SM-01/02) | `apps/web/playwright.config.ts` `e2e/`(global.setup.ts, specs/, vr/)`.github/workflows/`(perf.yml, llm-smoke.yml)`tools/perf/k6/` | L | M0-29〜M0-36, M0-25, M0-09 | plans/12 §4, §5, §9, §12, §13.2 |
| M0-40 | 本番環境・デプロイ | Caddy 単一オリジン構成、prod Docker Compose、deploy.yml、Sentry・Grafana 接続、環境変数の本番投入 | `.github/workflows/deploy.yml` prod compose 定義、稼働する staging/prod | M | M0-03, M0-04 | plans/01 §8, §9.4 |
| M0-41 | M0 DoD 判定 | §2.4 の受け入れテスト全通過+品質指標の初回計測(拡張保存→readable p50 ≤ 60 秒は実プロバイダで手動計測し記録)。未達ならリリースしない(docs/10 §1) | DoD 判定記録(リリースタグ PR) | S | M0-37, M0-38, M0-39, M0-40 | docs/10 §2, §6, §8 |

### 2.4 M0 の DoD と受け入れテスト

| DoD 項目(docs/10 §8) | 受け入れテスト(plans/12) |
|---|---|
| AC-10-01 拡張保存→1 分以内に読める(状態表示含む) | XT-02, XT-03, XT-04, XT-05, PF-03(+実プロバイダ手動計測) |
| AC-10-02 3 モードで数式・図表が崩れない+品質バッジ常時 | PW-05(M0 スコープ=3 モード), VR-1a, VR-1b, VR-1c, PY-PARSE-04 |
| AC-10-03 選択質問→根拠チップ+「AI生成」「論文外の知識」区別 | PW-08(「↑メモに保存」以外) |
| AC-10-04 閉じても「続きから↓」で復帰 | PW-09 |
| 取り込み経路が拡張のみ(docs/00・02・08) | PW-02, XT-01 |

- 品質指標ゲート(docs/10 §6): arXiv 品質 A 率 ≥ 99%(処理ログ集計)、プレースホルダ検証通過率 ≥ 99.9%(SM-02+usage 集計)、数式レンダリング成功率 ≥ 99%(PY-PARSE-04)。

## 3. M1: 記録と日常運用

**DoD(docs/10 §3)**: 「毎朝のトリアージと読了後の記録が習慣として回り、数ヶ月前に読んだ内容を横断検索で思い出せる。arXiv にない PDF も拡張から送って読める。」

### 3.1 M1 実装順序

並列 4 レーン: **記録系**(M1-01→M1-02→M1-03 / M1-04 / M1-05→M1-06。ただし M1-05 は運用系 M1-07 の完了後に着手 — 表の依存列が正)、**運用系**(M1-07→M1-08 / M1-09→M1-10)、**検索系**(M1-11→M1-12→M1-13)、**PDF 系**(M1-18→M1-19 / M1-18→M1-20→M1-21→M1-22)。翻訳系(M1-14→M1-15)・横断系(M1-16 / M1-17)・モバイル縮退(M1-26。M0-41 後いつでも着手可、M1-24 の PW-22 有効化前に完了)はレーン間の空きで進める。クリティカルパスは PDF 系(M1-18→M1-20→M1-21→M1-22→M1-24)。

### 3.2 M1 タスク表

| ID | タスク名 | 内容 | 主な成果物 | 規模 | 依存 | 参照 |
|---|---|---|---|---|---|---|
| M1-01 | 注釈 API | annotations CRUD(kind×color×body の形状制約)、一覧フィルタ(color / has_comment / placed=false)、counts 集計 | `routers/annotations.py`(plans/03 §8)、テスト PY-ANN-01, PY-ANN-03 の API 部 | M | M0-41 | plans/03 §8; docs/04 §5 |
| M1-02 | 選択メニュー完全化+ハイライト描画 | 4 色ハイライト(重要 #C49432/疑問 #5884AA/アイデア #659471/用語 #82827E)・コメント・ブックマークを選択メニューに追加、本文ハイライト描画、HighlightMark コンポーネント | `components/ui/HighlightMark.tsx` SelectionMenu 拡張、テスト VT 相当は PW-10 に委譲 | M | M1-01 | plans/09-screens/1b §3, §5; plans/08 §5.17 |
| M1-03 | 注釈一覧パネル | フィルタ(すべて/重要/疑問/アイデア/コメントのみ)+未配置の保全表示+Markdown エクスポート導線(実体は M1-16) | `components/viewer/AnnotationListPanel.tsx`(注釈タブ追加)、テスト VT-VIEW-08 | M | M1-02 | plans/09-screens/1b §3; docs/04 §5 |
| M1-04 | メモ | notes API、メモタブ、チャット回答の「↑メモに保存」昇格(根拠アンカー複写)、まとめてメモ化 | `routers/notes.py`(plans/03 §9, §10.5)`components/viewer/NotesPanel.tsx`、テスト PY-NOTE-01 | M | M0-41 | plans/03 §9; docs/05 §7 |
| M1-05 | 読書時間計測+ステータス提案 | reading_sessions 記録 API・ハートビート(30 秒間隔・アクティブ 3 分ルール)・読了提案(最終セクション付近)・設定 3 分岐(auto/suggest/off)。通知生成は M1-07 の `status_suggestion` を使用 | `POST /api/library-items/{id}/reading-sessions` heartbeat 実装 `hooks/useReadingSession.ts`、テスト PY-LIB-05 | M | M1-07 | plans/07 §8; plans/03 §5.9; docs/06 §5 |
| M1-06 | 読了フロー(1g) | 中央モーダル 460px(読了日・累計時間の自動記録表示、理解度 1〜5、重要度 低/中/高、ひとことメモ、「✦要約をメモに保存」、すべてスキップ可)。「記事モードで読み返す→」カードは M2-07 まで非表示(docs/10 §3 の決定)。finished_at 初回不変 | `components/library/FinishReadingDialog.tsx`、テスト PY-LIB-06 | M | M1-05, M1-04 | plans/09-screens/1g; docs/06 §3 |
| M1-07 | 通知バックエンド | notifications API(一覧・既読・read-all・提案 2 択 action)、発火 3 点の実装: `translation_complete`(取り込み完了)・`status_suggestion`(3 分ルール/B→A 昇格提案)。`deadline_reminder` cron は M2-09 | `routers/notifications.py`(plans/03 §16)plans/05 §12 の発火コード、テスト PY-NTF-01(締切以外), PY-NTF-02 | M | M0-41 | plans/03 §16; plans/05 §12; docs/06 §6 |
| M1-08 | 通知 UI(4a) | ヘッダのベルアイコン+未読ドット(#C49432)+ポップオーバー(翻訳完了「読み始める→」/提案「変更する・そのまま」)+「すべて既読にする」。SSE `notification.created` 連動 | `components/notifications/`(NotificationBell, NotificationPopover) | M | M1-07 | plans/09-screens/4a §3〜5; docs/06 §6 |
| M1-09 | ダッシュボード API | `GET /api/dashboard`(続きを読む ≤3・すぐ読むキュー・最近追加・統計 12 週)+`PUT /api/library-items/queue-order` | plans/03 §5.12, §5.7 の実装、テスト PY-LIB-04 | M | M0-41 | plans/03 §5.7, §5.12; docs/06 §4 |
| M1-10 | ダッシュボード UI(1d) | 続きを読む/すぐ読むキュー(ドラッグ順序・優先度・締切表示・6 本で「積みすぎかも?」)/最近追加(パイプライン進捗・✦3行要約・部分読書)/控えめ統計(今週読了・時間・12 週棒グラフ)。締切カードは M2-09 まで非表示。PriorityBadge・DeadlineBadge を追加実装。ログイン後遷移先を `/dashboard` に切替 | `app/(app)/dashboard/page.tsx` `components/library/`(ContinueReading, UpNextQueue, RecentlyAdded, StatsPanel)`components/ui/`(PriorityBadge, DeadlineBadge)、テスト VT-LIB-03 | L | M1-09, M1-08 | plans/09-screens/1d; plans/08 §5.4, §5.5 |
| M1-11 | 検索インデックス実装 | PGroonga 検索の実装系: 平文導出関数(api/worker 共有)、インデックス更新フック一覧、`rebuild_block_search_index`(索引 DDL 自体は M0-06 で投入済み) | `alinea_core/search/pgroonga_query.py` 平文導出+フック、PY-DB-14 の運用有効化 | M | M0-41 | plans/11 §2, §9 |
| M1-12 | 横断検索 API | 源別ヒット SQL(body/note/annotation/chat。article は M2-15)、日英クロス・同一ブロック統合、論文単位グループ化、スニペット `<mark>`、`GET /api/search` / `/api/search/preview` / 論文内検索 `GET /api/revisions/{id}/search` | `routers/search.py`(plans/03 §15)検索コーパスフィクスチャ、テスト PY-SRCH-01〜05 | L | M1-11 | plans/11 §3〜§6; plans/03 §15 |
| M1-13 | 検索 UI | 1e ヘッダ検索ドロップダウン(プレビュー 3 件+すべての結果を表示)、4e 全結果画面(源バッジ 4 色・論文グループ・源別遷移)、ビューア内検索 `/`(InPaperSearch) | `app/(app)/search/page.tsx` `components/search/`(SearchDropdown, SearchResults, SourceBadge)`components/viewer/InPaperSearch.tsx` | L | M1-12 | plans/09-screens/4e; 1e §3; viewer-shell §7; plans/11 §7 |
| M1-14 | 用語集 3 層+訳語変更 | glossary API(3 層 CRUD・promote・global 書込 403)、訳語変更の dry_run 影響数→影響ブロックのみ部分再翻訳、逆引きインデックス、UI(対訳ポップ・設定からの用語編集導線) | plans/03 §7.9 の実装 `alinea_core/translation/glossary.py` 完成、テスト PY-GLS-01, PY-GLS-02, PF-08 | L | M0-41 | plans/06 §8; plans/03 §7.9; docs/03 §8 |
| M1-15 | 指示つき再翻訳・proposal・手動編集 | `retranslate` の instruction 対応(上位モデルエスカレーション)、proposal 差分表示→採用/破棄、手動編集(state=edited の保全・`discard_edit` 409) | plans/03 §7.6〜7.8 の実装+対訳ポップの UI 拡張、テスト PY-TR-09 | M | M0-41 | plans/06 §11; plans/03 §7.6〜7.8; docs/03 §9 |
| M1-16 | エクスポート(M1 分) | 論文単位 Markdown(Obsidian 互換 front-matter・メモ・注釈・チャット・リソース枠)、BibTeX、注釈 Markdown。CSV/JSON 一括は M2-15 | `routers/export.py`(plans/03 §18 の M1 分)、テスト PY-EXP-01, PY-EXP-02, PY-ANN-03 | M | M1-04, M1-01 | plans/03 §18; docs/00 P5; docs/06 §11 |
| M1-17 | 設定画面 4f 完成(M1 分) | 8 カテゴリの UI 骨格完成: 表示(アクセント 4 色・書体・文字サイズ)/翻訳(スタイル既定・付録・表セル・30 ページ提案)/読書の計測と提案/チャット/通知/ブラウザ拡張。エクスポートカテゴリの CSV/JSON は M2-15 | `app/(app)/settings/` 全カテゴリページ、テスト PY-SET-01 の UI 反映(PW-17) | L | M1-05, M0-33 | plans/09-screens/4f; spec-decisions F9 |
| M1-18 | PDF パーサ(品質 B)+受け口 | PyMuPDF+pdfplumber によるテキスト・bbox 抽出、段組み・読み順復元、見出し検出、図領域切り出し、書誌推定、`POST /api/ingest/pdf`(private 既定・50MB/415/テキストレイヤ無し failed) | `alinea_core/parsing/pdf_parser.py` `ingest/bib_estimate.py` plans/03 §3.3 実装、テスト PY-PARSE-03, PY-ING-04 | L | M0-41 | plans/05 §6, §9; docs/02 §4 |
| M1-19 | 拡張: 一般 PDF+ピル | 状態 4(GenericPdf: 警告+「このタブのPDFを送信」明示クリック+「書誌は推定」)、送信失敗キュー(storage.local 永続・指数リトライ)、arXiv ページ内「A 保存」ピル(オプトイン・SettingsView) | `states/GenericPdf.tsx` `lib/queue.ts` `lib/pdf-detect.ts` `entrypoints/arxiv-pill.content.ts` `FailedQueueBanner.tsx`、テスト VT-XTU-02, VT-XTU-03 | L | M1-18 | plans/10 §10, §11; docs/08 §5, §6 |
| M1-20 | PDF モード(2a) | PDF.js ペイン、「同期: p.5 ≒ §2.2」常時表示、bbox 選択→「≒ §2.2 ¶2 — 訳文で見る→」チップ、「この位置を訳文で開く→」、左バー目次/ページサムネイル、ズーム/フィット/見開き/ページ入力 | `components/viewer/PdfPane.tsx` ほか 2a §3 のコンポーネント群 | L | M1-18 | plans/09-screens/2a; docs/04 §2 |
| M1-21 | 情報パネル完全版 | 取り込みタイムライン 3 段(タイムスタンプ)、処理ログ表示、「再取り込み」実行(reingest)、品質 A/B 説明文(逐語)、エクスポート導線(注釈 Markdown/原文 PDF) | InfoPanel 拡張(2a §3 の情報パネル仕様)、テスト PY-ING-06 の UI 経路 | M | M1-20, M1-16 | plans/09-screens/2a §3〜4; plans/05 §10 |
| M1-22 | B→A 昇格提案+リアンカー | `check_quality_promotions` cron(HTML/LaTeX 出現検知→`status_suggestion` 通知・自動適用しない)、`adopt-revision`、`reanchor_annotations` ジョブ(注釈・位置・語彙・記事アンカーの追従、失敗分は未配置) | `apps/worker/src/alinea_worker/cron.py` `tasks/reanchor.py` 実装 plans/03 §6.8、テスト PY-ING-07, PY-ANN-02 | L | M1-18, M1-07 | plans/05 §12.3; plans/02 §5.3; docs/02 §7 |
| M1-23 | pytest M1 スイート確定 | M1 付随テストの通し実行と補完(§7 の M1 列全件) | PY-ANN/NOTE/NTF/SRCH/GLS/EXP-01,02/ING-04,07/PARSE-03/TR-09/LIB-04〜06 グリーン | M | M1-01〜M1-22(完了後に着手。M1-24 とは並走可) | plans/12 §2.4 |
| M1-24 | E2E・VR(M1 分) | PW-06/10/11/12/14/17/18/19/22 有効化+PW-08 の「↑メモに保存」fixme 解除、XT-06/08/10、VR-1d/1g/2a/4a/4e/4f/3a-4、PF-02/04/06/08 追加。PW-22(viewport 390×844)は M1-26 のモバイル縮退レイアウトを前提とする | e2e specs・vr 追加、perf.yml 拡張 | L | M1-02〜M1-22, M1-26 | plans/12 §4, §5, §9, §12 |
| M1-25 | M1 DoD 判定 | §3.3 の受け入れテスト全通過+品質指標計測(横断検索 p50 1 秒/p95 3 秒を PF-06 で確認) | DoD 判定記録 | S | M1-23, M1-24 | docs/10 §3, §8 |
| M1-26 | モバイル縮退レイアウト | <768px の閲覧+ステータス変更 UI(plans/09-screens/mobile.md が正)。ブレークポイント <768px のメディアクエリ、目次・サイドパネルのドロワー化、ステータス変更のボトムシート、モバイル対象外の操作系(注釈・編集・一括操作等)の条件レンダリング(非表示)。PW-22(viewport 390×844)の検証対象 | メディアクエリ(tokens/theme のブレークポイント)`components/viewer/` `components/library/` のドロワー・ボトムシート・条件レンダリング実装 | M | M0-41(ビューア M0-28〜M0-31・ライブラリ M0-32 の完了を前提) | plans/09-screens/mobile.md; docs/00 S1 |

### 3.3 M1 の DoD と受け入れテスト

| DoD 項目(docs/10 §8) | 受け入れテスト(plans/12) |
|---|---|
| AC-10-05 朝のトリアージ+読了フローが回り、ステータスは提案のみ(P6) | PW-18, PW-19, PY-LIB-05, PY-NTF-02 |
| AC-10-06 横断検索(日英クロス・ヒット源明示)で 2 分以内に発見(S5) | PW-14, PF-06, PY-SRCH-01, PY-SRCH-02 |
| AC-10-07 arXiv にない PDF を品質 B バッジつきで読め、PDF モードで突き合わせ | PY-ING-04, PW-12, XT-06 |
| リビジョン昇格が提案のみで自動適用されない | PW-11, PY-ING-07 |
| モバイルは閲覧・ステータス変更のみ(docs/00 S1) | PW-22 |

## 4. M2: 理解の深化とアウトプット

**DoD(docs/10 §4)**: 「読了した論文を概要図つきの記事モードで読み返し、コレクションを URL 共有して輪読会に臨める。LaTeX ソース由来の最高品質(品質 A 主経路)で読め、拾った単語が復習で定着する。」

### 4.1 M2 実装順序

並列 5 レーン: **記事・図**(M2-03→M2-04→M2-05/M2-06→M2-07→M2-08)、**共有**(M2-09→M2-10)、**語彙**(M2-11→M2-12)、**リソース**(M2-13)、**品質 A**(M2-01→M2-02)。ライブラリ・検索強化(M2-14 / M2-15)はレーン間の空きで進める。クリティカルパスは記事・図レーン(M2-03→M2-05→M2-07→M2-17)。

### 4.2 M2 タスク表

| ID | タスク名 | 内容 | 主な成果物 | 規模 | 依存 | 参照 |
|---|---|---|---|---|---|---|
| M2-01 | LaTeX パーサ | arXiv e-print(LaTeX ソース)→ 品質 A 構造化、相互参照解決(`\ref`/`\cite`)、取得優先順位を LaTeX > HTML > PDF に切替(主経路化) | `alinea_core/parsing/latex_parser.py`(parser_version 'latex-1.2.0')fetch 優先順位変更、テスト PY-PARSE-02 | L | M1-25 | plans/05 §5, §1.3; docs/02 §3 |
| M2-02 | 既存 B 論文の A 昇格運用 | M1-22 の昇格提案 cron を LaTeX 経路に接続し、既存 B 論文の再取り込み→A 昇格→リアンカーを本運用化 | cron の LaTeX 判定拡張、PW-11 の LaTeX 経路再実行 | S | M2-01 | plans/05 §12.3; docs/10 §4 |
| M2-03 | 記事生成バックエンド | `generate_article` ジョブ(素材収集=訳文・メモ・チャット履歴、記事構造 JSON スキーマ、プリセット 4 種、「議論したい点」疑問ハイライト由来、出典ブロック自動挿入・削除不可)、版管理(instructions_history)、ブロック書き直し/再生成 | `apps/worker/src/alinea_worker/tasks/generate_article.py` plans/07 §4 の実装、テスト PY-ART-01, PY-ART-02, PY-ART-04 | L | M1-25 | plans/07 §4; docs/07 §2 |
| M2-04 | 記事 API | `GET/POST /api/library-items/{id}/article`、regenerate(✦指示つき再生成)、版一覧・restore、blocks/rewrite | `routers/articles.py`(plans/03 §19) | M | M2-03 | plans/03 §19 |
| M2-05 | 全体概要図 | 図データ DSL(課題→提案→結果 3 カード)生成、決定的 SVG レンダラ(バイト同一・ゴールデン照合)、版管理・指示つき書き直し・SVG ダウンロード、ラスター生成モード(設定キー `llm_routing.overview_figure_raster_mode`、既定 OFF — plans/09-screens/4f) | `packages/figures/src/alinea_figures/overview_svg.py` `generate_overview_figure` ジョブ `routers/figures.py`(plans/03 §20.1)、テスト PY-FIG-01〜04, HP-05 | L | M2-03 | plans/07 §5; plans/03 §20.1; docs/07 §3 |
| M2-06 | 解説図(ラスター) | ImageRouter 経由の画像生成(google→xai→openai フォールバック)、画像プロンプト構成規則(画像内に文字を描かない・重要テキストはキャプション側)、slot/version 管理・S3 保存 | `generate_explainer_figure` ジョブ plans/03 §20.2 実装、テスト PY-FIG-05, PY-FIG-06 | M | M2-05 | plans/07 §6; plans/03 §20.2; spec-decisions A8 |
| M2-07 | 記事モード UI(1h) | 5 つ目の表示モードとして追加。メタ行(AI生成・生成日付・免責逐語)、概要図フレーム(版管理・SVG ⤓)、ブロックホバー(✦書き直し指示/再生成/根拠を表示)、根拠チップ、原文引用ブロック、「議論したい点」、出典ブロック、ヘッダ「✦指示つき再生成」、レベル/テンプレート選択、1g の「記事モードで読み返す→」カード表示化 | `components/viewer/ArticlePane.tsx` ほか 1h §3 の群(OverviewFigureFrame, ArticleMetaRow, ArticleBlockHover, DiscussionList)、テスト VT-VIEW-13〜16 | L | M2-04, M2-05 | plans/09-screens/1h; docs/07 |
| M2-08 | 図表転載ライセンス判定 | 記事内 figure_embed の可否判定(マトリクス全 8 行: cc-by-4.0=クレジット自動付記+バッジ、cc-by-nd=キャプション分離、arxiv-nonexclusive/unknown=リンクカード代替) | plans/07 §4.5 の転載判定実装(licenses.py 接続)、テスト PY-ART-03 | M | M2-07 | docs/09 §5.2; plans/07 §4.5 |
| M2-09 | コレクション | collections API(CRUD・entries position ドラッグ・締切残日数・担当者・発表時間・予備注記・進捗集計)、4b 画面、ダッシュボード締切カード有効化、`send_deadline_reminders` cron(毎日 08:00 JST)と締切リマインド通知 | `routers/collections.py`(plans/03 §13)`app/(app)/collections/` 4b コンポーネント群 cron、テスト PY-COL-01, PY-NTF-01 の締切部 | L | M1-25 | plans/09-screens/4b; plans/03 §13; docs/06 §8 |
| M2-10 | 共有ページ(4c) | 共有トークン発行/無効化(base62 8 文字・active 1 本)・「共有ページにメモを含める」トグル、匿名 API、`/c/[token]` ページ(閲覧専用・noindex・縮退ヘッダ・書誌+✦要約+許可メモのみ・ライセンス不明は書誌のみ縮退) | plans/03 §13.3, §14 実装 `app/(public)/c/[token]/page.tsx`、テスト PY-COL-02, PY-SHR-01〜03 | M | M2-09 | plans/09-screens/4c; plans/03 §14; docs/09 §5 |
| M2-11 | 語彙帳バックエンド | vocab API(追加・一覧・詳細・PATCH・regenerate・重複 409)、`enrich_vocab_entry` ジョブ(8 フィールド AI 生成・失敗時 generation_status=failed 保存)、SRS(段階 1〜5・間隔 1/3/7/14/30 日・review-queue・自己評価) | `routers/vocab.py`(plans/03 §11)`tasks/generate_vocab_ai.py` `services/srs_service.py`、テスト PY-VOC-01〜09 | L | M1-25 | plans/03 §11; plans/07 §7; docs/11 |
| M2-12 | 語彙帳 UI(4d) | 選択メニューに「語彙に追加」追加、マスター/ディテール一覧(種別 3 分類チップ・復習期チップ・語彙帳内検索)、詳細(語義・語源・解釈のしかた・覚えるコツ、編集可)、復習セッション(「まだあやしい」/「✓覚えた」・次の復習表示)、「原文で見る→」、サイドバーバッジ(総語数) | `app/(app)/vocab/page.tsx` `components/vocab/` 群(VocabList, VocabDetail, ReviewFooter, VocabSearchBox)、テスト VT-VOC-01〜04 | L | M2-11 | plans/09-screens/4d; docs/11 |
| M2-13 | リソース(5a) | resources API(URL 貼り付け→種別自動判定 4 種・正規化・二重登録 409)、`fetch_resource_metadata` ジョブ(タイトル・サムネ・kind 別メタ、失敗でも登録完了)、公式実装自動検出(提案カード→追加/無視永続)、5a タブ UI(件数バッジ=active のみ・§参照チップ・「開く↗」) | `routers/resources.py`(plans/03 §12)`tasks/fetch_resource_meta.py` `components/viewer/ResourcesPanel.tsx` 群、テスト PY-RES-01〜06, VT-VIEW-17〜19 | L | M1-25 | plans/09-screens/5a; plans/03 §12; docs/12 |
| M2-14 | ライブラリ強化 | 属性フィルタ(ステータス/タグ/コレクション/品質/年)+「この条件を保存」(保存フィルタ CRUD・サイドバー件数表示)、複数選択→フローティング一括操作バー(ステータス変更/タグ追加/コレクションへ)、優先度・締切列の実データ化 | plans/03 §5.6, §5.14 実装 `components/library/`(AttributeFilterBar, BulkActionBar, SavedFilterList)、テスト PY-LIB-07, VT-LIB-02 完全化 | L | M2-09 | plans/09-screens/1e §3, §7; plans/03 §5.6, §5.14; docs/06 §10 |
| M2-15 | 直訳スタイル+検索・エクスポート完成 | 直訳プロンプト差分+オンデマンド生成+ビューアのスタイル切替(段落・セクション単位)、横断検索ヒット源に「記事」追加(バッジ=グレー)、CSV(UTF-8 BOM・16 列)/JSON 一括(`export_user_data` ジョブ)、4f エクスポート節完成 | plans/06 §5.2, §10.2 実装、plans/11 §3.2 の article SQL 有効化、plans/03 §18 完成、テスト PY-TR-08, PY-EXP-03, PY-EXP-04, PY-SRCH-02 の article 部 | L | M2-04, M1-25 | plans/06 §5.2, §10.2; plans/11 §3, §4; plans/03 §18 |
| M2-16 | pytest M2 スイート確定 | M2 付随テストの通し実行と補完(§7 の M2 列全件) | PY-ART/FIG/COL/SHR/VOC/RES/PARSE-02/TR-08/LIB-07/EXP-03,04 グリーン | M | M2-01〜M2-15(完了後に着手。M2-17 とは並走可) | plans/12 §2.4 |
| M2-17 | E2E・VR(M2 分) | PW-04(完全化)/13/15/16/20/21 有効化+PW-05 の 5 モード fixme 解除+XT-03 のコレクション欄アサーション解除、VR-1e/1h/4b/4c/4d/5a、SM-03/04 追加 | e2e specs・vr 追加 | L | M2-07〜M2-15 | plans/12 §4, §5, §9 |
| M2-18 | M2 DoD 判定 | §4.3 の受け入れテスト全通過+品質指標計測(LLM コスト/論文の用途別実測を usage_records 集計で確認) | DoD 判定記録 | S | M2-16, M2-17 | docs/10 §4, §6, §8 |

### 4.3 M2 の DoD と受け入れテスト

| DoD 項目(docs/10 §8) | 受け入れテスト(plans/12) |
|---|---|
| AC-10-08 記事モードで読み返せ、概要図(SVG・版管理)がダウンロードできる | PW-13, PY-FIG-01〜03, PY-ART-01 |
| AC-10-09 共有リンクをアカウント不要・noindex の共有ページで開ける | PW-15, PY-SHR-01〜03 |
| AC-10-10 「語彙に追加」した語が SRS 復習に現れ「原文で見る→」で戻れる | PW-20, PY-VOC-06, PY-VOC-07 |
| AC-10-11 LaTeX 由来(品質 A 主経路)で取り込まれ、既存 B が A へ昇格 | PY-PARSE-02, PY-ING-07, PW-11(LaTeX 経路) |
| 記事の公開 UI が存在しない(A17・v2 送り) | PW-13 の否定検査 |

## 5. M3: 広がり

**DoD(docs/10 §5)**: 「arXiv 以外の主要ソースの論文も同じ体験で読め、学習・再読のループがツールの外(Anki・オフライン)まで届く。」

M3 の実装詳細は plans/00〜12 に v1 スコープ外として未確定の部分が多い。**決定**: M3 の各タスクは着手前に plans への追補(設計節の追加)と plans/12 §6.12 への受け入れテスト追補を成果物に含める(AC-10-12 は「M3 実装時に追加」と定義済みのため)。

| ID | タスク名 | 内容 | 主な成果物 | 規模 | 依存 | 参照 |
|---|---|---|---|---|---|---|
| M3-01 | 他サイトアダプタ | OpenReview / ACL Anthology / PubMed の解決アダプタ+拡張からの認証ページ送信 | `alinea_core/arxiv/` を汎化した `sources/` アダプタ 3 種、拡張の URL 判定拡張、plans/05 への追補節 | L | M2-18 | docs/02 §8; docs/08; docs/10 §5 |
| M3-02 | OCR(品質 B 内部拡張) | スキャン PDF のテキスト化。品質レベルは新設せず B の内部拡張 | pdf_parser.py の OCR 分岐、plans/05 §6 への追補 | L | M3-01 | docs/10 §5; docs/02 §4 |
| M3-03 | arXiv バージョン差分表示 | v1→v2 の変更点提示とリアンカー(M1-22 の carryover 基盤を利用) | 差分ビュー UI+API、plans への追補節 | M | M2-18 | docs/10 §5; plans/05 §4.5 |
| M3-04 | Anki エクスポート | 語彙帳からのカード書き出し | `GET /api/vocab/export/anki`(apkg 生成)、docs/11 対応 | S | M2-18 | docs/11; docs/10 §5 |
| M3-05 | やさしい訳スタイル | 第 3 の翻訳スタイル(スタイル切替基盤 M2-15 に追加) | プロンプト差分+`style` 列挙拡張 | S | M2-18 | docs/03; docs/10 §5 |
| M3-06 | セマンティック検索 | 類似手持ち論文・クエリ翻訳のクロス検索強化 | 埋め込み索引+検索 API 拡張、plans/11 への追補節 | L | M2-18 | docs/10 §5; plans/11 |
| M3-07 | PWA オフライン閲覧 | Service Worker+ローカルキャッシュでの閲覧 | apps/web の PWA 化、plans への追補節 | L | M2-18 | docs/10 §5 |
| M3-08 | 輪読会スペース | グループ共有(docs/00 Q4 の判断後にのみ着手。判断が「見送り」なら本タスクは削除し、コレクション共有リンク+各自の記事モードで代替を継続) | 判断記録+(実施時)設計追補と実装 | L | M2-18 | docs/10 §5; docs/00 §7 Q4 |
| M3-09 | M3 DoD 判定 | AC-10-12(M3-01 着手時に plans/12 へ追補したテスト)全通過+全期間品質指標の確認 | DoD 判定記録 | S | M3-01〜M3-08 | docs/10 §5, §8 |

## 6. クリティカルパス

### 6.1 M0(製品が始まるまで)

```
M0-01 → M0-03 → M0-06 → M0-07 → M0-14 → M0-15 → M0-17* → M0-18 → M0-19 → M0-24
      → M0-34 → M0-35 → M0-36 → M0-39(XT-01〜05)→ M0-37(ストア審査 = 外部待ち)→ M0-41
```

- `*` M0-17 は M0-16(プレースホルダ)と M0-13(ルーティング)の合流点。M0-16 は M0-07 直後から、M0-08→M0-13 は P1 レーンで先行着手し、合流遅延を防ぐ。
- **ストア審査(M0-37)は自チームで短縮できない唯一の区間**。docs/10 §7 のとおり、XT-01〜05 が通った時点(全 M0 完了を待たず)で提出する。審査中に M0-38〜M0-40 を消化する。
- ビューア系(M0-28→M0-29→M0-30)は上記と独立に長いレーンを成すため、フロントエンド担当は P1 の M0-05→M0-26→M0-27 から途切れなく着手する。

### 6.2 全体(M0→M2)

```
M0(取り込み・翻訳基盤)→ M1-18(PDF パーサ)→ M1-20〜22(2a・昇格)→ M2-01(LaTeX)→ M2-02
                        → M2-03(記事)→ M2-05(概要図)→ M2-07(1h)→ M2-17 → M2-18
```

- M1 では PDF 系レーン、M2 では記事・図レーンが最長。語彙(M2-11〜12)・リソース(M2-13)・コレクション(M2-09〜10)は互いに独立で、人員があれば完全並列にできる。
- 全マイルストーンで「DoD を満たさない限り次へ進まない」(docs/10 §1)。並列レーンの先行着手は認めるが、**リリースは DoD 判定タスク(M0-41 / M1-25 / M2-18 / M3-09)の完了のみで行う**。

## 7. 受け入れテストのタスク割付表(網羅の担保)

docs/00〜12 の全受け入れ基準 158 項目は plans/12 §6 でテスト ID に割付済みである。本表は**全テスト ID がいずれかの WBS タスクの成果物に含まれること**を示す(= docs 受け入れ基準を本 WBS が漏れなくカバーすることの証明)。網羅チェックは `tools/traceability/check.py`(M0-38)を CI で常時実行する。

| テスト ID | 有効化タスク(最終) | 備考 |
|---|---|---|
| PY-DB-01〜12 | M0-06 | 全 DDL が M0 初期マイグレーションのため M0 で全件実行可 |
| PY-DB-13, PY-DB-14 | M0-07(14 の運用フックは M1-11) | |
| PY-AUTH-01〜03 | M0-11 | |
| PY-ING-01, 03, 06 | M0-19 | |
| PY-ING-02, 05 | M0-18 | |
| PY-ING-04 | M1-18 | PDF 送信 |
| PY-ING-07 | M1-22(LaTeX 経路の再実行は M2-02) | |
| PY-PARSE-01, 04 | M0-15 | |
| PY-PARSE-03 | M1-18 | |
| PY-PARSE-02 | M2-01 | |
| PY-TR-01 | M0-16 | |
| PY-TR-02〜07, 10 | M0-17 | |
| PY-TR-08 | M2-15 | 直訳 |
| PY-TR-09 | M1-15 | |
| PY-GLS-01, 02 | M1-14 | |
| PY-CHAT-01〜06 | M0-21 | |
| PY-ANN-01 | M1-01 | |
| PY-ANN-02 | M1-22 | |
| PY-ANN-03 | M1-16 | API 部の先行実装は M1-01 |
| PY-NOTE-01 | M1-04 | |
| PY-LIB-01, 02 | M0-22 | |
| PY-LIB-03 | M0-20 | |
| PY-LIB-04 | M1-09 | |
| PY-LIB-05 | M1-05 | |
| PY-LIB-06 | M1-06 | |
| PY-LIB-07 | M2-14 | |
| PY-COL-01 | M2-09 | |
| PY-COL-02 | M2-10 | |
| PY-SHR-01〜03 | M2-10 | |
| PY-SRCH-01〜05 | M1-12(article 源のみ M2-15) | |
| PY-NTF-01, 02 | M1-07(締切リマインド部のみ M2-09) | |
| PY-SET-01〜04 | M0-23 | 02〜04 の LLM 側実装分は M0-13 に付随 |
| PY-EXP-01, 02 | M1-16 | |
| PY-EXP-03, 04 | M2-15 | |
| PY-VOC-01〜09 | M2-11 | |
| PY-RES-01〜06 | M2-13 | |
| PY-ART-01, 02, 04 | M2-03 | |
| PY-ART-03 | M2-08 | |
| PY-FIG-01〜04 | M2-05 | |
| PY-FIG-05, 06 | M2-06 | |
| PY-LIC-01 | M0-07 | |
| PY-JOB-01, 03 | M0-12 | |
| PY-JOB-02 | M0-18 | |
| PY-LLM-01〜07 | M0-08 | |
| HP-01〜04(+HP-05) | M0-16(HP-05 は M2-05) | |
| VT-TOK-01 | M0-05 | |
| VT-UI-01 | M0-26 | |
| VT-UI-02, 03 | M0-27 | |
| VT-VIEW-01〜07, 09〜12 | M0-28〜M0-31 | |
| VT-VIEW-08 | M1-03 | |
| VT-VIEW-13〜16 | M2-07 | |
| VT-VIEW-17〜19 | M2-13 | |
| VT-LIB-01, 02 | M0-32(02 の一括バーは M2-14) | |
| VT-LIB-03 | M1-10 | |
| VT-VOC-01〜04 | M2-12 | |
| VT-XTU-01 | M0-34 | |
| VT-XTU-02, 03 | M1-19 | |
| PW-01, 02, 03, 09 | M0-39 | |
| PW-05, 07, 08 | M0-39(fixme 解除: PW-05=M2-17、PW-07 の指示つき/直訳=M1-24/M2-17、PW-08 のメモ保存=M1-24) | §1.3 の規則 |
| PW-04 | M1-24(保存フィルタ・一括操作の解除は M2-17) | |
| PW-10, 11, 12, 14, 17, 18, 19, 22 | M1-24 | PW-22(390×844)はモバイル縮退レイアウト(M1-26)後 |
| PW-06 | M1-24 | 情報パネル完全版(M1-21)後 |
| PW-13, 15, 16, 20, 21 | M2-17 | |
| XT-01〜05, 07, 09 | M0-39 | XT-03 のコレクション欄アサーションの fixme 解除は M2-17(§4.2) |
| XT-06, 08, 10 | M1-24 | |
| VR-1a, 1b, 1c, 3a-1〜3 | M0-39 | |
| VR-1d, 1g, 2a, 4a, 4e, 4f, 3a-4 | M1-24 | |
| VR-1e, 1h, 4b, 4c, 4d, 5a | M2-17 | VR-1e は一括バー表示が前提のため M2 |
| PF-01, 03, 05, 07 | M0-39 | |
| PF-02, 04, 06, 08 | M1-24 | |
| SM-01, 02 | M0-39 | |
| SM-03, 04 | M2-17 | |
| REV-01〜04 | 各 DoD 判定タスク(M0-41 / M1-25 / M2-18 / M3-09)の PR テンプレートで実施 | plans/12 §6.1 |

## 8. 本書で新たに確定した実装決定の一覧

1. DDL は plans/02 §4 の全量を M0 の初期マイグレーション 1 本(`0001_initial_schema.py`)で投入する(§1.4)。
2. 未実装マイルストーンの UI 要素(タブ・表示モード・設定カテゴリ)は非表示とし、無効表示にしない(§1.5)。
3. ライブラリテーブル 10 列は M0 から全列描画し、未実装列は「—」表示(§1.5)。
4. M0 のログイン後遷移先は `/library`、M1-10 で `/dashboard` に切替(§1.5)。
5. マイルストーンをまたぐ E2E アサーションは `test.fixme()` で先にマージし、§7 の割付表のタスクで解除する(§1.3)。
6. 取り込みパイプラインの通知発火コードは M0-18 に含めず M1-07 で実装する(M0 は通知なし — docs/10 §2)。
7. 計画書間の ⚠ 不整合(plans/12 §15 の 9 件+plans/05 §13・06 §16・07 §12・10 §15・11 §11 の全項目)は plans/12 §15 の「本書の暫定」を正として M0-02 で最優先解消する。
8. 拡張ストア申請(M0-37)は XT-01〜05 通過直後(全 M0 完了前)に提出し、審査待ちをクリティカルパスとして扱う。
9. 論文内検索(`/`・InPaperSearch)は検索基盤と同じ M1(M1-12/M1-13)に置く(M0 DoD が要求しないため)。
10. PriorityBadge・DeadlineBadge はダッシュボード(M1-10)で、HighlightMark は注釈(M1-02)で実装する(共通 22 コンポーネントのマイルストーン分割)。
11. M3 の各タスクは着手前に plans への設計追補と plans/12 §6.12 への受け入れテスト追補を成果物に含める(§5)。
