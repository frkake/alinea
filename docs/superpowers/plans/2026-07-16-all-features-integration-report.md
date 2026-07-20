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
