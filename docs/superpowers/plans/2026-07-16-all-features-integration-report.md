# 全機能 S1–S14 統合レポート

- 日付: 2026-07-16
- ブランチ: `integration/all-features`(base: main)
- 手法: 14 機能を isolation:worktree で並列実装 → integration ブランチへ順次マージ → alembic 線形化 → SDK 単一再生成 → 全体検証。

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

## S14(記事の公開/コメント)— 設計のみ

コード未実装。docs/10 §5 で v1 スコープ外・v2 検討と明記された項目であり、**全文翻訳の公開が著作権原則 P7 と衝突する**ため。spec に P7 整合案(公開レベル=要約のみ/二次創作ブロックのみ/CC限定)とトークンモデル設計を記載し、ユーザー判断待ち。

## 統合作業

- **Alembic 線形化**: S2 `0010_import_job_kind` / S7 `0010_vocab_candidates` / S11 `0011_easy_translation_style` が 0009 から分岐 → 単一 head `0011_easy_translation_style` に線形化、`ck_jobs_kind` を union({import, vocab_extract})。
- **共有ファイル衝突解決**: export.py(S3∪S2)、VocabHeader(S5∪S9)、context_builder(S1∪S6)、tasks/__init__.py HANDLERS(S2∪S7)、viewer.py(S3∪S10)、SettingsClient/ExportSettings(S1∪S2)。
- **SDK**: 全マージ後に一度だけ再生成、ドリフトなし(検証で確認)。
- **Lint 修正**(commit c7f1098): 既存の worker RUF001/RUF003(意図的な全角文字)・S112・E501・I001 を修正(main 由来の pre-existing 含む)。

## 検証結果

- **apps/api**: 661 passed(+ `test_seed.py` 5 件は共有DB汚染で full-run 時のみ失敗、単独では 6/6 pass = 既存のテスト分離問題であり統合起因ではない)。
- **packages/py-core**: 1090 passed。
- **apps/worker**: 統合対象コード(export_bulk/import_bulk/ingest)34 passed。フル worker スイートは LaTeX/PDF/OCR 統合テストが非常に低速で完走確認できず(S11 も同報告。統合起因ではない)。
- **JS(turbo build/lint/typecheck/test)**: green(worker#lint は c7f1098 で修正済み)。
- **alembic**: 単一 head。**SDK**: ドリフトなし。

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
