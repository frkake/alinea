# 設計: 完全データ移行(エクスポート/インポート)

- 日付: 2026-07-16
- 対象: `apps/api`(export/import ルータ)・`apps/worker`(export/import ジョブ)・`apps/web`(設定「データ」カテゴリ)・`packages/py-core`(シリアライズ・ストレージ)
- ステータス: レビュー待ち
- 関連: S2(監査 tier 1)。後続の S3(論文単位スタンドアロンエクスポート)とは別スペック。

## Context(背景)

ユーザー要望:「別の PC に移動してもデータを完全に移行できる」データのインポート/エクスポート。

現状の実態(コード確認済み):

- **全量 JSON エクスポートは存在するが不完全**。`apps/worker/src/alinea_worker/tasks/export_user_data.py` の `build_export_payload` が出力するのは DB 行の一部のみ — library(paper 書誌込) / notes / annotations / chat_threads+messages / vocab / resources / articles+blocks / collections / settings。
- **含まれていない**: 論文本文(`document_revisions`)、翻訳(`translation_sets` / `translation_units`)、用語集(`glossaries` / `glossary_terms`)、保存フィルタ(`saved_filters`)、読書セッション(`reading_sessions`)、通知(`notifications`)、図メタ(`overview_figures` / `explainer_figures` / `source_assets`)、そして **S3 バイナリ全部**(元 PDF・翻訳/対訳 PDF・図 PNG・概要 SVG・explainer PNG・LaTeX tar・サムネイル)。
- **インポート(復元)経路は皆無**(リポジトリ全体で 0 件)。

したがって現状の「JSON 一括」で別 PC に移しても、論文本文・翻訳・PDF・図はすべて失われ、全論文を再取り込み・再翻訳する羽目になる(時間 + LLM コスト)。本タスクはこれを **再取り込み不要の完全スナップショット**に引き上げ、**インポートで復元**できるようにする。

期待する成果: エクスポートした 1 つの zip を別 PC のインポートに読み込ませれば、論文本文・翻訳・PDF・図・メモ・注釈・チャット・語彙・記事・コレクション・設定・通知履歴まで、サーバー再取り込みなしで即座に読める状態に復元される。

## 決定事項(ユーザー確認済み)

1. **生成物も全部含む完全スナップショット**。本文・翻訳・元 PDF/翻訳 PDF/対訳 PDF・図・概要 SVG・explainer PNG・LaTeX ソース・サムネイルを zip に同梱する。
2. **インポートはマージ追加(冪等)**。復元先の既存データは残し、`arxiv_id` / 元 id で重複検知してスキップ、新規のみ取り込む。何度実行しても壊れない。置換(リストア)モードは作らない。
3. **生成は既存の非同期 Job(`export_full`)を拡張**。worker で zip 化 → S3(assets バケット)→ 署名 URL(24h)。既存のポーリング UI を流用。インポートも対称に非同期 Job(`import`)。
4. **BYOK 鍵は移行に含めない**。zip に平文キーが漏れないよう除外。復元後に「別 PC では BYOK を再登録してください」と UI で案内。
5. **`block_search_index` は移行に含めず、インポート時に `document_revisions` から決定的に再構築**。`notifications` / `reading_sessions` は履歴として価値があるので含める。
6. **設定左ナビのカテゴリ名を「エクスポート」→「データ」に変更**(インポート/エクスポート両方を含むため)。画面全体のタイトル「設定」は既存のまま変更不要。

## データモデル: 移行対象テーブルの分類

`packages/py-core/src/alinea_core/db/models.py` の全 31 テーブルを 3 分類する。

**(A) スナップショットに含める(ユーザー所有データ)**

| テーブル | 備考 |
|---|---|
| `papers` | 共有エンティティ。`arxiv_id`(なければ `content_sha` 系)で名寄せ |
| `library_items` | ユーザー所有。paper への参照 |
| `source_assets`(メタ) | storage_key/sha256/byte_size。バイナリは `assets/` へ |
| `document_revisions` | **本文(構造化ドキュメント)。新規追加** |
| `translation_sets` / `translation_units` | **翻訳。新規追加** |
| `glossaries` / `glossary_terms` | **用語集。新規追加** |
| `chat_threads` / `chat_messages` | 既存(text のみ → 全カラムへ拡張) |
| `notes` / `annotations` | 既存 |
| `vocab_entries` | 既存(SRS 状態込) |
| `resource_links` | 既存 |
| `articles` / `article_blocks` | 既存 |
| `overview_figures` / `explainer_figures` | **図メタ。新規追加**(SVG/PNG バイナリは `assets/`) |
| `collections` / `collection_entries` / `collection_share_tokens` | 既存(share_tokens は新規追加) |
| `saved_filters` | **新規追加** |
| `reading_sessions` | **履歴として追加** |
| `notifications` | **履歴として追加** |
| `users`(settings のみ) | 設定 JSONB。email/display_name は表示用に含めるが復元では現ユーザーにリマップ |

**(B) 含めない・再構築する**

| テーブル | 理由 |
|---|---|
| `block_search_index` | `document_revisions` から決定的に再構築(既存インデックス構築経路を再利用) |
| `byok_api_keys` | セキュリティ(平文キー漏洩防止) |
| `auth_identities` | 認証情報。移行先の現ユーザーに紐づけ直すため不要 |

**(C) 一時・運用データ(含めない)**

`jobs` / `usage_records` / `quota_limits` — 運用状態であり移行対象外。

## S3 アセットの同梱

`packages/py-core/src/alinea_core/storage/s3.py` `StorageKeys` の全種別を対象:

- `sources/{paper}/{ver}/latex.tar.gz` / `arxiv.html` / `original.pdf` / `translated-{style}.pdf` / `bilingual-{style}.pdf` / `metadata.json`
- `figures/{paper}/{rev}/{block}.{ext}`
- `thumbnails/{paper}/.../card.webp`(retina 兄弟含む)
- `renders/overview/{article}/v{n}.svg`
- `renders/explainer/{explainer}/v{n}.png`

方式:

- エクスポート側は `source_assets` 行 + `overview_figures`/`explainer_figures`/`document_revisions`(図参照)から到達可能な storage_key を集約し、`S3Storage.get` でストリーム取得して zip の `assets/<storage_key>` に格納。`manifest.json` に `{storage_key, sha256, byte_size, content_type}` を記録。
- インポート側は manifest の各 asset について、S3 に同 key が無ければアップロード、有れば sha256 一致で skip(冪等)。

## アーキテクチャ

### エクスポート(`export_full` Job 拡張)

- API: 既存 `POST /api/export/full`(`apps/api/src/alinea_api/routers/export.py`)。変更なし(ジョブ作成のみ)。
- worker: `apps/worker/src/alinea_worker/tasks/export_user_data.py` を拡張。
  - `build_export_payload` に (A) の新規テーブルのシリアライザを追加。
  - zip 構成を `alinea-export.json` 単体 → **`manifest.json` + `data.json` + `assets/**`** の構造へ変更(schema_version で旧新判別)。
  - アセットはメモリ展開せずストリームで zip へ書き込む(大容量対策)。
- 結果は従来通り `jobs.result.download_url`(S3 署名 URL 24h)。

### インポート(新規 `import` Job + アップロード API)

- API: 新規 `POST /api/import/full`(multipart で zip 受領 → S3 の一時 key へ保存 → `import` Job 作成 → job_id 返却)。`GET /api/import/full/{job_id}` で進捗ポーリング(export と対称)。
- worker: 新規 `apps/worker/src/alinea_worker/tasks/import_user_data.py`。
  - zip 検証(manifest スキーマ・schema_version)。不正は fail、理由を `jobs.result` へ。
  - `data.json` を依存順(papers → library_items → document_revisions → translation_* → 注釈系 → articles → collections …)に復元。各行は元 id で存在チェック → 無ければ INSERT、有れば skip(冪等・マージ追加)。
  - `user_id` は復元先の現ユーザーへリマップ。`papers` は `arxiv_id` で名寄せ(既存 paper があれば再利用、無ければ新規)。
  - アセットを S3 へ復元(sha256 照合)。
  - 復元完了後、対象 paper の `block_search_index` を `document_revisions` から再構築。
  - `HANDLERS["import"] = run_import_full_job` を `apps/worker/src/alinea_worker/tasks/__init__.py` に登録。

### UI(設定「データ」カテゴリ)

- `apps/web/src/components/settings/SettingsClient.tsx` の `CATEGORIES` の `export` ラベルを「データ」に変更(id はそのまま `export` で可)。
- `ExportSettings.tsx` を「データ」カテゴリの本体に拡張(またはファイル名/コンポーネント名を `DataSettings` にリネーム):
  - 既存の論文単位 Markdown / BibTeX・CSV カードは維持。
  - **完全バックアップ(エクスポート)** カード: 「全データ(論文本文・翻訳・PDF・図・メモ等)を 1 つの zip に。別 PC への移行に使えます」。実行 → ポーリング → 自動 DL(既存フロー流用)。
  - **インポート(復元)** カード: zip ファイル選択 → アップロード → 進捗ポーリング → 完了トースト。「既存データはマージ追加され、上書きされません」「BYOK(API キー)は移行されないため復元後に再登録してください」の注記。
  - モバイル `readOnly` 時はインポート/エクスポート実行を非描画(既存方針踏襲)。

## エラー処理(P3: 黙って壊れない)

- 壊れた zip・manifest スキーマ不一致・schema_version 非互換は明確にエラー化し、`jobs.result.error` に理由を格納。UI はトーストで理由提示。
- 部分的失敗(1 論文のアセット欠落等)は当該のみ skip してログに残し、可能な範囲で復元を継続。復元サマリ(取り込み件数 / skip 件数 / 失敗件数)を `jobs.result` に残す。
- インポートはトランザクション境界を paper 単位に区切り、途中失敗しても既に復元済みの paper は保持。

## テスト

- **ラウンドトリップ**(PY): seed 済みユーザーを export → 空 DB の別ユーザーへ import → 全カテゴリの件数一致、本文/翻訳/注釈/語彙/記事の内容一致、アセット sha256 一致。
- **冪等性**(PY): 同じ zip を 2 回 import → 2 回目は全件 skip、重複行が生じない。
- **マージ**(PY): 既存データありユーザーへ import → 既存は不変、新規のみ追加。
- **BYOK 除外**(PY): export した zip に平文 API キーが含まれないことを検査。
- **検索再構築**(PY): import 後に横断検索・論文内検索がヒットする(block_search_index 再構築の確認)。
- **不正入力**(PY): 壊れた zip / 非互換 schema_version が fail し理由が入る。
- **UI**(web/vitest): 「データ」カテゴリにエクスポート/インポート両カードが描画され、インポートで zip 選択 → アップロード → ポーリングが動く。ラベルが「データ」。
- **E2E(任意)**: 設定「データ」からエクスポート実行 → DL リンク出現、インポートでファイル選択 → 完了トースト。

## スコープ外

- 置換(リストア)モード・自動同期・クラウドバックアップ。
- BYOK 鍵の移行。
- 論文単位のスタンドアロンエクスポート(別スペック S3)。
- 差分/増分エクスポート。
