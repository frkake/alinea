# 論文単位スタンドアロンエクスポート 実装計画(Feature S3)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development。各タスクは失敗するテスト→実装→緑の順。チェックボックス(`- [ ]`)で進捗管理。

**Goal:** 1 ライブラリ項目の生成済み成果物(原文/訳文/対訳/記事の単一 HTML、PDF(原文・日本語・対訳)に注釈埋め込み)を複数選択でエクスポートし、サーバ非依存で開ける zip にまとめる。未生成成果物は選択不可。

**Spec:** `docs/superpowers/specs/2026-07-16-standalone-paper-export-design.md`

## Global Constraints

- Python: `uv run pytest apps/api -q`(実 PostgreSQL/Redis)。純関数は DB 非依存で単体テスト。lint: `uv run ruff check`、型: `uv run mypy`。行長 100。相対 import 禁止。
- **新規 pip 依存は追加しない**(`pymupdf`・`pillow` は `packages/py-core` に既存、`yaml` は uvicorn 経由)。
- API は `alinea_api.errors.ProblemException` でエラー、`Response` で添付返却(`_attachment` 参照)。所有チェックは `export.py::_get_owned_item`。
- Web: `pnpm --filter @alinea/web test`(vitest/jsdom)。UI コピーは日本語。SDK は `@alinea/api-client`、生成は `pnpm --filter @alinea/api-client generate`(OpenAPI は `apps/api` から出力)。
- **変更禁止:** 既存 `export.py` の他エンドポイント挙動、ビューアの他機能、全量 JSON エクスポート。

## フェーズ分割(承認ゲート)

- **フェーズ 1(本計画で即実装・曖昧さ無し):** 純 HTML レンダラ + readiness API + 単一 HTML 同期エンドポイント(原文/訳文/対訳/記事)。数式は決定 A 未承認のため **A-3 フォールバックマークアップ**(意味づけ済み `.alinea-math`)で出力し、KaTeX 注入座(`math_runtime` 引数)を用意しておく。
- **フェーズ 2(決定 A 承認後):** KaTeX ランタイムの vendoring + inline 埋め込み(`math_runtime` 実装)。
- **フェーズ 3(決定 C/D/E 承認後):** zip 束ねジョブ + PDF 注釈埋め込み + 対訳 PDF 結合 + Web UI 導線。

各フェーズ末で `uv run pytest apps/api -q` を緑にしてコミット。

---

## フェーズ 1

### Task 1: 純 HTML レンダラ(DB 非依存)

**Files:**
- Create: `apps/api/src/alinea_api/schemas/standalone_html.py`
- Test: `apps/api/tests/test_standalone_html.py`

**Interfaces(spec「純レンダラ設計」参照):**
- `escape_html(s) -> str`
- 値オブジェクト `TranslationView`、`StandaloneMeta`、`ArticleBlockView(type:str, content:dict)`。
- `render_inline(inline: dict) -> str`(8 インライン種、`InlineRenderer.tsx` を写す)。
- `render_block(block: Block, *, tv: TranslationView | None, image_data_uris: dict[str,str]) -> str`(12 ブロック種、`SourcePane.SourceBlock` を写す)。
- `render_document_html(content: DocumentContent, *, mode, units: dict[str,TranslationView], image_data_uris, meta, math_runtime: str = "") -> str`
- `render_article_html(blocks: list[ArticleBlockView], *, image_data_uris, meta, math_runtime="") -> str`
- 数式は常に `<span class="alinea-math" data-display="true|false">…LaTeX…</span>` で出力。`math_runtime=""` の時は inline CSS が等幅ボックス表示にフォールバック。

- [ ] **Step 1: 失敗するテスト**
  - 各インライン種: `text`(HTML エスケープ確認: `<`,`&`,`"`)、`emphasis`(children 再帰)、`code_inline`、`math_inline`(`.alinea-math` + display フラグ)、`citation`、`ref`(kind 別ラベル `Fig. 1` 等)、`url`(`<a href`)、`footnote_ref`(`<sup>`)。
  - 各ブロック種: paragraph/heading(番号+タイトル)/figure(data URI 画像 & 欠損プレースホルダ)/table/equation(latex→`.alinea-math`、latex 無し+asset→img)/code(`<pre><code>`)/list(ordered/unordered)/quote/theorem/algorithm/footnote/reference_entry。
  - `render_document_html`: mode=source は原文のみ、mode=translation は訳優先+未訳フォールバック(`displayable=False` は原文)、mode=bilingual は 2 カラム(原文セル+訳セル)。`<!doctype html>`・inline `<style>`・`meta.title` が出る。
  - `render_article_html`: heading/paragraph(Markdown サブセット: `**b**`→`<b>`,`*i*`→`<i>`,`` `c` ``→`<code>`,`[t](u)`→`<a>`)/quote_source(英原文)/figure_embed(data URI)/discussion(番号付き)/attribution。
  - 数式フォールバック: `math_runtime=""` の出力に `<script` を含まない。
- [ ] **Step 2: 実装**(`schemas/export.py` の純関数スタイルを踏襲)。
- [ ] **Step 3: 緑 + `ruff`/`mypy` 通過。**

### Task 2: readiness API + 素材解決ヘルパ

**Files:**
- Modify: `apps/api/src/alinea_api/routers/export.py`(新エンドポイント追加)
- Create: `apps/api/src/alinea_api/schemas/standalone.py`(Pydantic レスポンス `StandaloneAvailability`)
- Test: `apps/api/tests/test_standalone_export.py`

**エンドポイント:**
```
GET /api/library-items/{item_id}/export/standalone/availability
   → StandaloneAvailability { source_html, translation_html, bilingual_html,
                              article_html, pdf_original, pdf_translated, pdf_bilingual }
```

**readiness 判定(spec 前提 5 の根拠ヘルパを再利用):**
- `item = _get_owned_item(db, user.id, item_id)`; `paper = db.get(Paper, item.paper_id)`。
- `revision = get_latest_paper_revision(db, paper)`(`alinea_core.db.revisions`)。None なら全 false。
- `source_html` = revision あり かつ `DocumentContent.model_validate(revision.content).iter_blocks()` が非空。
- `translation_html = bilingual_html` = `find_effective_set(db, str(revision.id), "natural", str(user.id))` が存在し `status=="complete"`(`alinea_core.translation`)。
- `article_html` = `Article` 行が `library_item_id==item.id` に存在(`select(Article.id).where(...).limit(1)`)。
- `pdf_original` = `SourceAsset kind IN ('pdf','extension_capture')` が `source_version==revision.source_version` に存在(`papers.py` の variant=source と同一条件。正規キー `StorageKeys.original_pdf`)。
- `pdf_translated` = 有効 natural セットあり かつ `SourceAsset kind='translated_pdf'` が `StorageKeys.translated_pdf(paper_id, source_version, "natural", translation_set_id=set.id if personal)` に存在。
- `pdf_bilingual` = 決定 D 暫定: `pdf_original and pdf_translated`。

- [ ] **Step 1: 失敗するテスト**(`test_export.py` の `_build_app`/`auth` フィクスチャを流用。`export.router` + `annotations.router` をマウント)。
  - 何も生成していない項目 → 全 false(revision あり原文のみ true になるケースも: `make_revision` で原文 true / 訳 false / 記事 false / PDF false)。
  - `make_translation_set(status="complete", style="natural")` + units → `translation_html/bilingual_html` true。
  - `make_article` → `article_html` true。
  - `SourceAsset(kind="pdf")` シード + 正規キー → `pdf_original` true。他人の項目 → 404。
- [ ] **Step 2: 実装。** `find_effective_set` は `alinea_core.translation` から import。SourceAsset 判定は `select(SourceAsset.id).where(...).limit(1)` の存在チェックで軽量に。
- [ ] **Step 3: 緑。**

### Task 3: 単一 HTML 同期エンドポイント(原文/訳文/対訳/記事)

**Files:**
- Modify: `apps/api/src/alinea_api/routers/export.py`
- Test: `apps/api/tests/test_standalone_export.py`(Task 2 と同ファイル)

**エンドポイント(すべて `text/html; charset=utf-8` の attachment):**
```
GET /api/library-items/{item_id}/export/standalone/source.html
GET /api/library-items/{item_id}/export/standalone/translation.html
GET /api/library-items/{item_id}/export/standalone/bilingual.html
GET /api/library-items/{item_id}/export/standalone/article.html
```

**共通処理:**
- 所有 item → paper → `get_latest_paper_revision`。未生成成果物(readiness false)は `ProblemException("not_found")`。
- `content = DocumentContent.model_validate(revision.content)`。
- 訳文/対訳: 有効 natural セット → `resolve_translation_set_units(db, tset)`(`alinea_core.translation`)で `dict[block_id, TranslationUnit]` を取得し `TranslationView` に写す(`displayable` は `_unit_is_displayable_for_block` 相当の判定を移植 or util 化。BLOCKING_FLAGS 除外)。
- 図: `content.iter_blocks()` から figure/table の `asset_key` を集め、`storage.get(assets_bucket, key)` → `data:{mime};base64,` に data URI 化(best-effort、失敗は欠損プレースホルダ)。`StorageDep`(`from alinea_api.routers.papers import StorageDep`)を使う。
- 記事: `_article_for_item` で Article + `ArticleBlock`(position 昇順)を取得し `article_block_wire`(`alinea_core.article.wire`)相当で `ArticleBlockView` へ。記事図の画像も data URI 化。
- `render_*_html(...)` を呼び `_attachment` で返す。ファイル名は `export_filename(paper_bib, suffix="-source")` 等の HTML 版(`.html`)。

- [ ] **Step 1: 失敗するテスト**
  - source.html: 200・`text/html`・`<!doctype html>`・原文テキスト含有・図の `data:image` 含有(fake storage が図バイトを返す)。
  - translation.html: 訳文含有・未訳ブロックは原文フォールバック。
  - bilingual.html: 原文と訳の両方含有。
  - article.html: 記事ブロックのテキスト含有・attribution 含有。
  - 未生成 → 404。他人の項目 → 404。
- [ ] **Step 2: 実装。** fake storage は既存テストの流儀に合わせる(`test_ingest_pdf.py`/`test_resources.py` の storage スタブ、または `StorageDep` の dependency_overrides)。
- [ ] **Step 3: 緑 + ruff/mypy。**

### Task 4: SDK 再生成 + フェーズ 1 コミット

- [ ] `pnpm --filter @alinea/api-client generate`(OpenAPI 反映)→ `packages/api-client/openapi.json` と `dist/generated/*` を更新。生成物 diff を確認。
- [ ] `uv run pytest apps/api -q` 緑、`uv run ruff check`、`uv run mypy` 通過を確認。
- [ ] コミット: `feat(api): standalone single-paper HTML export + readiness API`。

---

## フェーズ 2(決定 A 承認後)

### Task 5: KaTeX ランタイムの vendoring と inline 埋め込み

- [ ] `apps/api` にパッケージデータとして KaTeX アセットを同梱(`katex.min.css`・`katex.min.js`・`fonts/*.woff2`。出所は web の `node_modules/.pnpm/katex@0.16.22/.../dist`)。`hatch.build` の `force-include` 等で wheel に含める。
- [ ] `math_runtime` 実体: CSS+JS+フォントを data URI 化し `<head>` に inline。`auto-render`(`renderMathInElement`)を `DOMContentLoaded` で `\(...\)`/`\[...\]` に適用。前処理は `katex-render.ts` と一致(`\notag`/`\label` 除去、display top-level `&`→`aligned`、`\bm`/`\mathbbm`/`\student` マクロ、未定義→`\operatorname`)。
- [ ] 単一 HTML エンドポイントに `?math=katex`(既定)/`?math=source` を追加、または常に KaTeX 注入。
- [ ] テスト: `math_runtime` 注入時に `<style>`(katex css)と `renderMathInElement` 呼び出しが出る。サイズ回帰(生成 HTML が想定上限内)。
- [ ] コミット。

---

## フェーズ 3(決定 C/D/E 承認後)

### Task 6: zip 束ねジョブ(worker)

- [ ] Create `apps/worker/src/alinea_worker/tasks/export_paper.py`: `build_paper_export_archive(session, storage, *, item_id, artifacts) -> bytes`。純レンダラ(HTML)+ PDF 埋め込み(Task 7)+ 対訳結合(Task 8)を呼び、zip 化。
- [ ] `run_export_paper_job(ctx, store, job)`: `jobs.kind='paper_export'`。S3(assets)へ put + `presign_get`(24h)→ `jobs.result.download_url`。`StorageKeys` に `paper_export(user_id, job_id)` を追加。
- [ ] API: `POST /api/library-items/{id}/export/standalone`(202+job_id、`artifacts` 検証)+ `GET .../standalone/{job_id}`(`export.full` と同型)。`_bulk_queue` 起床は既存 `_default_export_wakeup` を流用。
- [ ] followup: `tasks/__init__.py` に `HANDLERS["paper_export"] = run_export_paper_job`(共有ファイル・所有範囲外)。
- [ ] テスト `apps/worker/tests/test_export_paper.py`: zip エントリ・成果物選択の反映。

### Task 7: 原文 PDF への注釈埋め込み(pymupdf)

- [ ] Create `apps/worker/src/alinea_worker/pdf_annotate.py`: `embed_annotations(pdf_bytes, *, block_positions, annotations) -> bytes`。ブロック→ページ+bbox(品質 B は `block_search_index.page/bbox`、品質 A は `pdf_sync.sync_block_positions` に原文 PDF の単語 bbox 列を渡して導出)。`fitz` で該当ページに `add_highlight_annot`(矩形)+ コメントは `add_text_annot`(付箋)。色は注釈色(important/question/idea/term)→ RGB。
- [ ] 訳文/対訳 PDF には bbox 写像が無いため注釈を埋め込まない(決定 C-1)。
- [ ] テスト: 埋め込み後 PDF を `fitz.open` し `page.annots()` 数・種別を検証。

### Task 8: 対訳 PDF 結合(pymupdf)

- [ ] `merge_bilingual_pdf(original_bytes, translated_bytes) -> bytes`: `fitz` でページ交互 or 見開き結合(決定 D-1 の承認レイアウトに従う)。
- [ ] テスト: ページ数・順序。

### Task 9: Web UI 導線

- [ ] `ViewerHeader.tsx` の ⋯ Popover に「エクスポート」項目を追加 → 選択モーダル `StandaloneExportModal.tsx`(新規)。
- [ ] `availability` を SDK でフェッチし、未生成成果物のチェックボックスを disabled + tooltip「まだ生成されていません」。
- [ ] 選択 → `POST .../export/standalone` → job ポーリング(`ExportSettings.tsx` の JSON 一括パターンを流用)→ `download_url` で `triggerDownload`。
- [ ] テスト `StandaloneExportModal.test.tsx`(SDK モック、readiness による disabled、選択→POST→ダウンロード)。
- [ ] SDK 再生成、コミット。

---

## リスクと未確定

- **数式(決定 A)** が最大の未確定。フェーズ 1 は方式非依存で先行、承認後にフェーズ 2 で確定。
- **PDF 注釈精度(決定 C):** 品質 A の bbox 同期は原文 PDF の単語 bbox 列が必要(worker が `page.get_text("words")` で抽出)。同期率が低い論文ではハイライト位置が粗くなる。
- **対訳 PDF(決定 D):** サーバ生成が無いため新規実装。承認レイアウト次第。
- **図 data URI:** 大きい図が多い論文で HTML が肥大化しうる(1 論文数 MB)。zip 圧縮で緩和。deferred 図は未取得プレースホルダ。
