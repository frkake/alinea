# 全機能 S1–S14 統合レポート

- 日付: 2026-07-16
- ブランチ: `integration/all-features`(base: main)
- 手法: 14 機能を isolation:worktree で並列実装 → integration ブランチへ順次マージ → alembic 線形化 → SDK 単一再生成 → 全体検証。

> **⚠️ 履歴レポート(S1–S14 統合時点)。** 本レポートは S1–S14 統合時点(migration head `0011_easy_translation_style`)のスナップショットである。その後の `remaining-features-completion`(Task 1–30)で記事公開・コメント(S14 相当)、論文スライド生成(PPTX)、Hugging Face 関連ソース収集、GitHub コード対応解析などが追加され、**現在の migration head は `0021_huggingface_resources`(単一線形チェーン)**である。以下の「Phase 1 完了」「N passed」等は S1–S14 統合時点の記録であり、**現在のブランチ全体の受け入れ基準通過を意味しない**。DB マイグレーション適用後の全体 E2E と品質指標計測は **Task 32 の統合検証に委ねる(本タスクでは未実行)**。各機能を「完了」と扱うのは、その機能の受け入れ基準が実測で通った項目に限る。

## サマリ

未実装ギャップ監査で洗い出した 14 項目(ユーザー依頼のデータ移行・論文エクスポート含む)を並列実装した。**13 機能がコード実装+マージ済み、S14 のみ設計書のみ**(著作権 P7 との整合が必要なため意図的にコード化せず)。

## マージ済み機能

| ID | 機能 | 状態 | 主なテスト |
|---|---|---|---|
| S1 | 設定の実効化(LLM routing 配線/チャットトグル/テーマ切替UI/アカウント設定+クォータ/拡張トグル) | 実装+マージ | api 600 / web 36 |
| S2 | 完全データ移行(生成物込みエクスポート+冪等マージインポート、カテゴリ名「データ」) | 実装+マージ | export_bulk+import_bulk 含む worker 34 |
| S3 | 論文単位スタンドアロンエクスポート(原文/訳文/対訳/記事の自己完結HTML+readiness API) | Phase1 実装+マージ | api 37 |
| S4 | 公式実装(GitHub)自動検出のingest配線 | 実装+マージ | py-core 14 / ingest |
| S5 | 語彙Markdownエクスポート導線(UI) | 実装+マージ | web 336 |
| S6 | チャット・記事の圧縮モード(長大論文の後半欠落解消) | 実装+マージ | packages 圧縮テスト |
| S7 | AI単語抽出(候補ジョブ+API+accept/dismiss、vocab_candidates テーブル) | backend 実装+マージ | 13 |
| S8 | 他サイトアダプタ(ACL Anthology adapter + サイト非依存コア) | Phase1 実装+マージ | 9 |
| S9 | Ankiエクスポート(TSV形式、依存なし) | 実装+マージ | web 741 / vocab 17 |
| S10 | arXivバージョン差分(block単位diffエンジン+API) | Phase1 実装+マージ | 15 |
| S11 | やさしい訳スタイル(easy、natural/literalと同経路) | 実装+マージ | api 594 / web 748 |
| S12 | セマンティック検索(embedding抽象+RRF融合、フラグoff) | Phase A 実装+マージ | 1121+43 |
| S13 | PWAオフライン(installable + app-shell SW、依存なし) | v1 実装+マージ | web 745 |

## S14(記事の公開/コメント)— 後続タスクで実装済み

S1–S14 統合時点では設計のみ(**全文翻訳の公開が著作権原則 P7 と衝突する**ため保留)。その後 `remaining-features-completion` の後続タスクで、P7 整合のサニタイズ(公開スナップショットに原文引用本文・原論文図・訳文・メモ・チャット・discussion を含めず、evidence は「論文タイトル+セクションラベル」に縮約)を前提に **実装+マージ済み**(migration `0015_article_publications` / `0017_publication_comments`、`apps/api/src/alinea_api/routers/publications.py`)。詳細は [07-figures-and-articles.md](../../07-figures-and-articles.md) §2.8。受け入れ基準の実測は Task 32 に委ねる。

## 統合作業

- **Alembic 線形化**(S1–S14 統合時点): S2 `0010_import_job_kind` / S7 `0010_vocab_candidates` / S11 `0011_easy_translation_style` が 0009 から分岐 → 当時の単一 head `0011_easy_translation_style` に線形化、`ck_jobs_kind` を union({import, vocab_extract})。**現在の head は後続タスク追加分を含め `0021_huggingface_resources`(単一線形チェーン)**。
- **共有ファイル衝突解決**: export.py(S3∪S2)、VocabHeader(S5∪S9)、context_builder(S1∪S6)、tasks/__init__.py HANDLERS(S2∪S7)、viewer.py(S3∪S10)、SettingsClient/ExportSettings(S1∪S2)。
- **SDK**: 全マージ後に一度だけ再生成、ドリフトなし(検証で確認)。
- **Lint 修正**(commit c7f1098): 既存の worker RUF001/RUF003(意図的な全角文字)・S112・E501・I001 を修正(main 由来の pre-existing 含む)。

## 検証結果(S1–S14 統合時点の記録)

> 以下の数値は S1–S14 統合時点(head `0011`)のもの。後続タスク(記事公開・PPTX・Hugging Face・コード対応解析ほか)の追加後の全体スイート・全体 E2E・品質指標計測は **Task 32 の統合検証に委ねる(本タスクでは未再実行)**。したがってこれらの数値を現在のブランチの通過証跡として引用しない。

- **apps/api**: 661 passed(S1–S14 時点)(+ `test_seed.py` 5 件は共有DB汚染で full-run 時のみ失敗、単独では 6/6 pass = 既存のテスト分離問題であり統合起因ではない)。
- **packages/py-core**: 1090 passed(S1–S14 時点)。
- **apps/worker**: 統合対象コード(export_bulk/import_bulk/ingest)34 passed(S1–S14 時点)。フル worker スイートは LaTeX/PDF/OCR 統合テストが非常に低速で完走確認できず(S11 も同報告。統合起因ではない)。
- **JS(turbo build/lint/typecheck/test)**: green(S1–S14 時点。worker#lint は c7f1098 で修正済み)。
- **alembic**: S1–S14 時点は単一 head `0011`。現在は単一 head `0021_huggingface_resources`(Task 32 で適用・検証)。**SDK**: S1–S14 時点でドリフトなし。本タスク(Task 31)で easy translation の operation_id を `translations_start_easy` に改名し OpenAPI+SDK を再生成、2 回再生成で追加ドリフトなしを確認済み。

## 既知の要ユーザー判断(各 spec に詳細)

1. **S3**: スタンドアロンHTMLの数式描画(KaTeXランタイムinline ≈1.5MB/HTML の可否)、PDF注釈埋め込みのブロック粒度、対訳PDF生成レイアウト、UI導線。
2. **S12**: 埋め込みプロバイダ(推奨 OpenAI text-embedding-3-small)、pgvector 同梱の自前Dockerイメージ可否、埋め込み粒度、クォータ計上。
3. **S13**: SW方式(手書き vs @serwist/next)、オフライン時 401 vs キャッシュfallback規則、キャッシュ上限。
4. **S8**: サイト取り込み論文の既定visibility、SourceAsset.kind、アダプタ実装順、PMC JATS品質Aをスコープに含めるか。
5. **S14**: 公開機能を実装するか(P7整合)、公開レベル、コメントモデル。
6. **S7**: 抽出トリガ(on-demand 採用、auto-at-ingest への切替可否)。
7. **S1 補足**: LLM routing overrides は現状 chat/summary のみ実効(worker経路タスクは per-user router 未対応、follow-up)。

## 残タスク(follow-up)

- S1: worker 経路タスク(翻訳/記事/vocab/figure)の per-user router 対応。
- S3/S8/S10/S12/S13: 各 Phase 2 以降(上記ユーザー判断後)。
- S7: 抽出結果を表示する SidePanel タブ UI。
- worker フルスイートの CI 安定化(LaTeX/OCR テストの高速化 or 分離)、test_seed/test_viewer_api のDB分離改善(pre-existing)。

## Task 32 コード成果物(自動検証完了・ユーザー受け入れ待ち)

> 本節は Task 32 の **コード成果物**(DB 分離・UAT シード・E2E fixme 解消)の記録。全体回帰
> コマンドの実測値・skip 件数・最終判定は controller の統合検証で別途記録する(本節は成果物と
> 既知制約のみ)。この時点の状態は **「自動検証完了、ユーザー受け入れ待ち」**。

- **テスト DB 分離(worker 単位)**: `alinea_core.testing.testdb` が pytest セッション
  (pytest-xdist の `PYTEST_XDIST_WORKER`。単一実行は `master`)ごとに専用 DB
  `<base>_test_<worker>` を **作成してマイグレーション適用**し、suite 終了時に drop する。
  0002 のシード(llm_models / llm_task_routes / quota_limits)はマイグレーションが再投入する
  ため保持される(`TRUNCATE CASCADE` は使わない)。api/worker の各 conftest がセッション
  autouse fixture で呼ぶ。既存 fixture 名(`db_session` 等)は不変。これにより「seed テストと
  通常テストの実行順を入れ替えても同一結果」を満たす(検証: test_seed_user_acceptance ⇄
  test_publications を両順で実行し 21 passed)。
- **pgvector 非同梱環境の扱い(偽装しない)**: `vector` 拡張が `pg_available_extensions` に
  無い環境では、0015 まで `upgrade` → 0016 を `stamp`(vector 依存 DDL を作らない)→ 0017〜head
  を `upgrade` の経路で head へ到達させる。埋め込みテーブルは **意図的に未作成**とし
  `pgvector_enabled()==False` として公開、`@pytest.mark.requires_pgvector` を **理由付き skip**
  にする(silently pass ではない)。`vector` 利用可能環境では素直に `upgrade head`。既存の
  埋め込みテスト群は in-memory ストア/InMemorySemanticIndex を使うため両経路で決定的に通る。
- **UAT シード**(`apps/api/scripts/seed_user_acceptance.py --reset --output <path>`): review
  環境(`APP_ENV=review`)専用。予約済み UAT-A / UAT-B のみを初期化(UAT-A=OpenAI `gpt-5.5` /
  UAT-B=Anthropic `claude-opus-4-8` の presentation ルート、両者 code_analysis 予算 5.00 USD)。
  ワンタイムのメールリンクトークン(single-use・15 分)を実行時生成し **mode 0600 の JSON に
  だけ**書く(ログ・stdout・repo に残さない)。固定 URL/期待値は
  `2026-07-17-user-acceptance-fixtures.json` から読み、schema version・source id 重複・URL 形式・
  期待識別子を検証する。予約外ユーザーは 1 件も変更しない(--reset も予約分のみ)。
  テスト `apps/api/tests/test_seed_user_acceptance.py`(9 passed)で検証。
- **E2E fixme の実操作化**: PW-05(図表参照クリック→対象ブロックへスクロール+
  `.alinea-block-flash`)、PW-12(PDF ページ層クリック→同期チップ「訳文で見る →」→
  mode=translation+block 遷移)、PW-14(記事生成→「記事」バッジ+「記事モードで開く →」)、
  PW-17(未翻訳=オンデマンドセクションの「— 未翻訳」+「未翻訳セクションを一括翻訳」)、
  PW-18(「記事モードで読み返す →」→ mode=article)を実 UI 操作+assertion へ置換。
- **release-env-gated のまま残す E2E**(assertion は完成形で保持):
  - PW-PRESENTATION: 成功経路が `vendor/ppt-master` submodule(承認 commit 0c0bdaf)+
    `.venv-ppt-master` に依存。未初期化環境では PresentationRunner が exporting 段で決定的に
    失敗するため `describe.skip`。再生成失敗経路の失敗注入フックは未実装(follow-up)。
  - XT-08(拡張ピル 設定オン): `browser.permissions.request`(optional host 権限)が実ユーザー
    ジェスチャーを要し Playwright で付与不能。SW 直登録での注入/非注入 assertion を完成形で
    書いたうえで `test.fixme`(権限付与手段が整えば `.fixme` を外すだけで通る形)。
- **既知の非整合(follow-up 候補)**: 検索の記事ヒット href は `view=article&article_block=...`
  だが、viewer ルートは `mode` を読み `view` を消費しない。PW-14 は両表記を許容する assertion
  にしてあるが、記事モードが実際に開くかは要確認。

## Task 32 統合検証 実測結果(controller 記録・自動検証完了/ユーザー受け入れ待ち)

対象コミット: `feat/remaining-features-completion`(main から 91 commits、作業ツリー clean、
`.superpowers/brainstorm/` のみ未追跡=仕様どおり不変)。全 32 タスク(Task 1〜32)実装・
レビュー・マージ済み。各タスクは spec 準拠+コード品質の 2 観点でタスクレビューを通過し、
Critical/Important 指摘は修正後に再検証済み。

### 自動検証(この環境で実測)
- **Python 型検査(mypy)**: 4 パッケージすべて 0 エラー(apps/worker 72 / apps/api 147 /
  packages/py-core 120 / packages/llm 33 = 計 372 ソース)。
- **TypeScript**: `pnpm --filter @alinea/web typecheck` 0 エラー。`pnpm --filter @alinea/web build`
  全ルート(offline / papers/[itemId] / search / settings / vocab 等)コンパイル成功。
- **Web ユニット**: `pnpm --filter @alinea/web test` 907 passed / 0 failed(131 files)。
  hey-api client の相対 URL を undici が拒否する既存問題を vitest.setup の baseUrl 設定で解消。
- **API pytest**(worker 単位 DB 分離 + マイグレーション/シード適用): 796 passed /
  2 skipped(`requires_pgvector`)。認証スイープは公開記事/コメント read の匿名許可を
  ANONYMOUS_PATHS へ追加して 17/17。
- **Worker pytest(機能スイート・バッチ実行)**: バックアップ往復・サイト取り込み・per-user
  ルーティング・埋め込み索引・コード対応解析・プレゼン生成・図アセット・記事/語彙生成など
  約 430 passed。`object.__new__(IngestRun)` 経路で `is_jats` 属性が未設定になる統合不具合を
  クラス属性既定値で修正。
- **マイグレーション**: 単一 head `0021_huggingface_resources`(base→0021 線形、分岐なし)。
  ダウングレード/アップグレード往復(0015⇄0011)をライブ PostgreSQL で検証し可逆性を確認。
- **ppt-master**: submodule を承認 commit `0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f` に固定。
- **生成 SDK**: マージ後 API から再生成(決定的・2 回目で差分なし)。

### リリース環境で実施(この環境の制約により委譲。偽装せず明示)
本サンドボックスは Docker ビルド/コンテナともに apt/DNS ネットワークが無く、
`postgresql-16-pgvector` を導入できない。このため `vector` 拡張を要する以下は
**リリース環境(pgvector 同梱 DB イメージを `docker/db/Dockerfile` からビルド)で実施**する:
- `alembic upgrade head`(0016 の `CREATE EXTENSION vector` + 埋め込みテーブル/HNSW 実体化)。
- 実ベクトルでのセマンティック検索 ANN / 「似た論文」の統合実行。
- Playwright E2E フルスイート(全スタック起動 + `vendor/ppt-master` の `.venv-ppt-master`)と、
  拡張の XT-08 権限付与経路。各 spec は assertion 完成形で保持済み(skip/fixme 理由付き)。
- `seed_user_acceptance.py --reset` の実行(review 環境限定)。

### 判定
自動検証済みの範囲は上記のとおりグリーン。最終マージは、この最終コミットから作った
リリース候補が[最終ユーザー受け入れチェックリスト](./2026-07-17-user-acceptance-checklist.md)で
`GO` となり、確認後の変更が無い場合に行う。現時点の状態は **「自動検証完了、ユーザー受け入れ待ち」**。
