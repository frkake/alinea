# 設計: 論文単位スタンドアロンエクスポート(Feature S3)

- 日付: 2026-07-16
- 対象: `apps/api`(新エンドポイント + 純レンダラ)、`apps/worker`(zip 束ね + PDF 注釈埋め込み)、`apps/web`(ビューアヘッダのエクスポート導線)、`packages/api-client`(SDK 再生成)
- ステータス: レビュー待ち(**下記「ユーザー確認が必要な決定」を要承認**)

## Context(背景)

ユーザー要望(逐語の意図):

> 1 論文の生成済み成果物 — **訳文 / 対訳 / 原文 / PDF(原文・日本語・対訳) / 記事** — を、
> 何を出力するか**複数選択**でエクスポートしたい。まだ生成されていない成果物は選択不可。
> エクスポートは**完全にスタンドアロン(サーバを起動しなくても開ける)**であること。
> チャットは不要。PDF については**注釈を PDF 内に埋め込む**こと。

既存のエクスポートは以下のとおりで、いずれも本要望を満たさない:

- `GET /api/library-items/{id}/export/markdown` — 論文単位 Markdown(書誌+メモ+注釈+チャット+リソース)。本文・訳文・PDF・記事は含まない。
- `GET /api/library-items/{id}/export/annotations` — 注釈のみ Markdown。
- `GET /api/export/bibtex` / `/csv` — ライブラリ単位の書誌。
- `POST /api/export/full`(worker `export_user_data.py`)— 全ユーザーデータ JSON+アセット zip。復元用であり「単一論文を読める形」ではない。

本 spec は「1 論文の読める成果物を、サーバ非依存で開ける形で束ねる」新機能を定義する。

### 調査で確定した前提(コードベース根拠)

1. **ドキュメント IR は Python 側に 1:1 で存在**する。`packages/py-core/src/alinea_core/document/blocks.py`(12 ブロック種)・`inlines.py`(8 インライン種)・`plaintext.py`(型別の平文導出)。ビューアの `SourcePane.tsx` / `InlineRenderer.tsx` の分岐と対応する。→ **HTML レンダラは Python 側で `DocumentContent` を直接消費して書ける。**
2. **数式描画は現状 100% クライアント側 KaTeX**(`apps/web/src/lib/katex-render.ts`、npm `katex` 0.16.22、`output:"html"`)。**Python / サーバ側の KaTeX・MathML 生成経路は存在しない**(`packages/py-core` にも `apps/api` にも katex/mathml/mathjax/sympy 依存なし)。→ **スタンドアロン HTML の数式描画は新規の設計判断が必要**(本 spec の決定 A)。
3. **PDF 書き込み能力は既に存在**する。`pymupdf`(`fitz`)と `pillow` が `packages/py-core/pyproject.toml` の一次依存であり、worker の `latex_pdf.py` が `fitz` で PDF を検証している。→ **「PDF に注釈を埋め込む」は新規依存の追加なしで実装できる。**(当初の「no-casual-dependency で PDF ライブラリを足せない」懸念は**解消**。)
4. **注釈は `block_id`+文字オフセットにアンカーする**(`docs/01` §5、page+bbox ではない)。ただし品質 B(PDF 由来)のブロックは `block_search_index.page/bbox` を持ち、品質 A も `pdf_sync.py`(`sync_block_positions`)でページ/bbox を導出できる。→ **PDF への注釈埋め込みは「ブロック→ページ+bbox」写像が前提**(決定 C)。
5. **成果物の生成有無(readiness)は既存ヘルパで判定可能**:
   - 原文: `get_latest_paper_revision(db, paper)` が非 None かつ `content` にブロックあり。
   - 訳文/対訳: `find_effective_set(db, revision_id, "natural", user_id)` が存在し `status=="complete"`。
   - 記事: `Article` 行が `library_item_id` に存在(`_article_for_item`)。
   - PDF(原文): `SourceAsset kind IN ('pdf','extension_capture')` が `original.pdf` キーに存在(`papers.py` の variant=source と同じ)。
   - PDF(日本語): 有効 natural セット + `SourceAsset kind='translated_pdf'` が正規キーに存在(variant=translated と同じ)。`revision.stats["translated_pdf"]` にも記録あり。
   - PDF(対訳): **サーバ側で `bilingual_pdf` を生成するコードは存在しない**(キー定義のみ)。フロントは原文/訳文 PDF をクライアント合成している。→ 決定 D。
6. **図の画像**は API 経由(`/api/assets/{base64url(asset_key)}`)で配信され、`Block.asset_key` に S3 キーを持つ。→ **スタンドアロン化には S3 から実バイトを取得し data URI 化する。**
7. 図の追加取り込み・オンデマンドは `apps/worker/src/alinea_worker/figure_assets.py`。エクスポート時に未取得(deferred)図は「未取得」プレースホルダのままとする(黙って失敗しない)。

## Goal / Non-Goal

**Goal**

- 1 ライブラリ項目(=ユーザー×論文)について、選択した成果物を**サーバ非依存で開ける**形でエクスポートする。
- 成果物の生成有無を返す readiness API を提供し、UI が未生成成果物を選択不可にできるようにする。
- HTML 成果物(原文/訳文/対訳/記事)は**単一 HTML ファイル**として自己完結する(inline CSS・図は data URI・数式は決定 A に従う)。
- PDF 成果物は既存の生成済み PDF に**注釈を埋め込んだ**単一 PDF とする。
- 複数選択の結果を 1 つの **zip** に束ねて配布する。

**Non-Goal**

- チャット履歴のエクスポート(要望どおり不要。`export/markdown` に既にある)。
- 語彙(SRS)・メモ・書誌 CSV/BibTeX(既存機能で対応済み)。
- 記事内の概要図(OverviewFigure SVG)・解説図の完全再現は best-effort(画像 data URI 埋め込み。DSL 再レンダラは移植しない)。
- 双方向の再インポート(全量 JSON エクスポートの責務)。

## 全体アーキテクチャ

```
[Web] ViewerHeader ⋯メニュー「エクスポート」
   └─ 選択モーダル (readiness API で可否を出し分け、複数選択)
        └─ POST /api/library-items/{id}/export/standalone { artifacts:[...] }
             → jobs.kind='paper_export'(bulk キュー。既存 export.full と同型)
                  worker: build_paper_export_archive(...)
                    ├─ 原文/訳文/対訳/記事 → 純レンダラ(schemas/standalone_html.py)で単一 HTML
                    │     · inline CSS · 図は S3→data URI · 数式は決定 A
                    ├─ PDF(原文/日本語) → pymupdf で注釈オーバーレイを埋め込み
                    └─ すべてを zip → S3(assets) → 署名 URL(24h)
   └─ GET /api/library-items/{id}/export/standalone/{job_id} で download_url をポーリング
```

- **なぜジョブ(非同期)か:** 図バイト取得・data URI 化・PDF 注釈埋め込みは重く、`POST /api/export/full` の既存パターン(202 → ポーリング → 署名 URL)に揃えるのが自然。純レンダラ自体は同期・DB 非依存の純関数として単体テストする(`schemas/export.py` の `render_paper_markdown` と同方針)。
- **同期エンドポイントも用意する(段階導入):** 単一 HTML 成果物は S3 の署名 URL を挟まず即時ダウンロードできると UX がよい。**本タスクでは原文/訳文/対訳/記事の同期 HTML エンドポイントと readiness API を先行実装**し(下記「実装スコープ」)、zip 束ね + PDF 埋め込みはジョブとして plan に残す(決定 A/C/D の承認待ちのため)。

## 純レンダラ設計(`apps/api/src/alinea_api/schemas/standalone_html.py`)

`schemas/export.py` と同じ「DB から解決済みの値を受け取り文字列を返す純関数」方針。pytest から DB なしで直接テストできる。

### 入力(DB 非依存の値オブジェクト)

```python
@dataclass(frozen=True)
class TranslationView:
    """1 ブロックの訳(translation_units 由来。DB 非依存)。"""
    content_ja: list[dict] | dict | None  # インライン列 or typed table or None
    text_ja: str
    displayable: bool                     # BLOCKING_FLAGS を除外済みか(呼び出し側で計算)

@dataclass(frozen=True)
class StandaloneMeta:
    title: str
    authors: list[str]
    arxiv_id: str | None
    generated_at: str          # ISO8601
    mode_label: str            # 「原文」「訳文」「対訳」「記事」
    quality_level: str         # "A" | "B"
```

### 公開関数

- `escape_html(s: str) -> str`
- `render_inline(inline: dict) -> str` — `InlineRenderer.tsx` を写す:
  - `text` → `escape_html(v)`(注: 訳文の `text` はプレースホルダ復元済み。原文の `text` は IR のまま。ビューアの `cleanLatexDisplayText` 相当のクリーニングは**移植しない**=決定 B)。
  - `emphasis` → `<em>`(children 再帰 or `v`)。
  - `code_inline` → `<code>`。
  - `math_inline` → 数式マークアップ(決定 A)。
  - `citation` → `[ref]` の上付きリンク風 `<span>`(ジャンプ先はないので非リンク)。
  - `ref` → `kind` 別ラベル(`Fig. N` 等)の `<span>`。
  - `url` → `<a href rel="noopener" target="_blank">`。
  - `footnote_ref` → `<sup>`。
- `render_block(block: Block, *, tv: TranslationView | None, image_data_uris: dict[str,str]) -> str` — `SourcePane.SourceBlock` を写す(全 12 種)。figure/table は `asset_key` を `image_data_uris` で解決し `<img src="data:...">`、無ければ「画像を表示できません」プレースホルダ。equation は数式マークアップ(latex 無しなら asset の data URI 画像)。
- `render_document_html(content: DocumentContent, *, mode: Literal["source","translation","bilingual"], units: dict[str, TranslationView], image_data_uris: dict[str,str], meta: StandaloneMeta) -> str` — 完全な単一 HTML。`<head>` に inline CSS(`--pr-*` を素の値に落とした最小テーマ)+ 数式ランタイム注入点。`source` は原文のみ、`translation` は訳優先(訳が無い/未 displayable は原文フォールバック)、`bilingual` は 2 カラムグリッド。
- `render_article_html(blocks: list[ArticleBlockView], *, image_data_uris, meta) -> str` — 記事 wire(`heading/paragraph/quote_source/figure_embed/explainer_figure/discussion/attribution`)を写す。`markdown.tsx` の最小 Markdown サブセット(`**bold**`/`*italic*`/`` `code` ``/リンク/`$...$`)を Python で再実装。

### 数式マークアップの共通契約(決定 A の実装座)

数式は常に次の**意味づけされたマークアップ**で出力し、描画方式は `<head>` 注入で切替可能にする:

```html
<span class="alinea-math" data-display="false">\(\frac{d}{dt} z_t = v(z_t,t)\)</span>
```

- `render_*_html(..., math_runtime: str = "")` 引数で `<head>` に KaTeX ランタイム(CSS+JS+フォント data URI)を注入できる。
- `math_runtime` 未指定時は inline CSS のみで、`.alinea-math` を等幅・淡色ボックスの**LaTeX ソース表示**にフォールバックする(欠損ではなく「読める劣化」)。
- これにより純レンダラは数式方式に非依存でテスト可能。KaTeX 埋め込みの実体は決定 A の承認後に `math_runtime` を実装して差し込む。

## API 設計

### readiness(可否)API — **本タスクで実装**

```
GET /api/library-items/{item_id}/export/standalone/availability
→ 200 StandaloneAvailability {
     source_html: bool,
     translation_html: bool,       # 有効 natural セット status=="complete"
     bilingual_html: bool,         # == translation_html
     article_html: bool,           # Article 存在
     pdf_original: bool,           # 原文 PDF 資産あり
     pdf_translated: bool,         # 訳文 PDF 資産あり(natural)
     pdf_bilingual: bool           # 決定 D(暫定: pdf_original && pdf_translated)
   }
```

読み取り専用で副作用なし。UI の選択不可判定に使う。最新リビジョン基準(ビューアと一致)。

### 単一 HTML エンドポイント — **本タスクで実装**

```
GET /api/library-items/{item_id}/export/standalone/source.html
GET /api/library-items/{item_id}/export/standalone/translation.html
GET /api/library-items/{item_id}/export/standalone/bilingual.html
GET /api/library-items/{item_id}/export/standalone/article.html
```

- いずれも `Content-Disposition: attachment` の `text/html; charset=utf-8`。
- 図は S3(assets バケット)から取得し data URI 化(best-effort、欠損はプレースホルダ)。
- 未生成成果物は 404(readiness と整合)。

### zip 束ね + PDF 埋め込み(非同期ジョブ)— **plan に記載、承認後実装**

```
POST /api/library-items/{item_id}/export/standalone
   body { artifacts: ["source_html","translation_html","bilingual_html","article_html",
                      "pdf_original","pdf_translated","pdf_bilingual"] }
→ 202 { job_id }         # jobs.kind='paper_export', bulk キュー
GET  /api/library-items/{item_id}/export/standalone/{job_id}
→ 200 { job: JobOut, download_url: str | None }
```

worker `apps/worker/src/alinea_worker/tasks/export_paper.py`(新規)が zip を生成し S3 へ。HANDLERS 登録は `tasks/__init__.py`(共有ファイル)への followup。

## ユーザー確認が必要な決定(要承認)

### 決定 A(最重要): スタンドアロン HTML の数式描画方式

要望「サーバを起動しなくても開ける」を満たす前提で、次から選ぶ:

- **A-1(推奨): KaTeX ランタイムを HTML に inline 埋め込み。** `katex.min.css`(24KB)+ `katex.min.js`(272KB)+ WOFF2 フォント(約 1.2MB、20 ファイル)を data URI 化して 1 ファイルに畳み込み、`renderMathInElement` を `DOMContentLoaded` で実行。ビューアと**同一バージョン・同一前処理**(`\notag`/`\label` 除去、display の top-level `&` を `aligned` 包み、3 マクロ、未定義コマンドの `\operatorname` 化)で**見た目が一致**。ブラウザで開くだけで描画(JS 必要・サーバ不要)。
  - 代償: 1 HTML あたり約 1.5MB 増(gzip 後は数百 KB)。KaTeX アセットの入手元(API コンテナに `node_modules` は無い)を決める必要 → **`apps/api` パッケージに KaTeX アセットを vendoring**(パッケージデータとして同梱)し、レンダラが読み込む。
- **A-2: MathML 事前生成。** Python に LaTeX→MathML ライブラリ(例 `latex2mathml`)を追加。JS 不要で最軽量だが、**新規依存の追加**(no-casual-dependency 規範に抵触)+ ビューア(KaTeX)と描画差が出る。
- **A-3: LaTeX ソース表示(フォールバックのみ)。** 依存ゼロ・実装ゼロだが、論文読解ツールとしては明確な劣化。

**推奨 = A-1。** 「サーバ不要」の要件は JS 実行を否定しない(ローカル HTML は JS を実行できる)。ビューアと同一 KaTeX を使うのが見た目一致・実装再利用の両面で最良。**確認事項:** (1) 1 ファイル約 1.5MB を許容するか、(2) KaTeX アセットの `apps/api` への vendoring を許容するか。
※ 純レンダラは本タスクで A-3 フォールバック込みで実装し、A-1 の `math_runtime` 実体は承認後に追加(実装座は用意済み)。

### 決定 B: 原文テキストのクリーニング

ビューアは `cleanLatexDisplayText`(`\alpha`→α 等の Unicode 化、`\textbf{}` 展開、`LABEL:` 整形)を原文 `text` に適用する。エクスポートでも同等の見た目にするには**このロジックを Python へ移植**する必要がある。

- **推奨: 移植する**(見た目一致のため)。ただし約 200 行の regex ロジックの移植でありテスト負荷が高い。
- 代替: 本タスクでは**未移植**(IR の `text` をそのまま escape)。多くの論文で許容範囲だが、一部で生 LaTeX 記号が残る。**確認事項:** 初版は未移植で妥協してよいか(推奨)、それとも移植必須か。

### 決定 C: PDF への注釈埋め込み方式(依存問題は解消済み)

`pymupdf` は既存依存のため**新規依存なしで実装可能**。埋め込み方式を選ぶ:

- **C-1(推奨): ブロック→ページ+bbox 写像でハイライト矩形+コメント註釈(popup annotation)を PDF に追加。** 品質 B は `block_search_index.page/bbox` を、品質 A は `pdf_sync.sync_block_positions`(原文 PDF の単語 bbox 列が要る)を使う。オフセット単位の精密ハイライトは困難なので**ブロック粒度の矩形**とし、コメントは PyMuPDF の `add_text_annot`(付箋)で本文に格納する。訳文/対訳 PDF はレイアウトが原文と異なり bbox 写像が無いため、**注釈埋め込みは原文 PDF に対してのみ**行う。
- **C-2: 注釈は PDF に埋め込まず、別途 HTML 注釈ビューを同梱。** 実装は軽いが「PDF 内に埋め込む」という明示要望に反する。

**推奨 = C-1(原文 PDF のみ、ブロック粒度)。確認事項:** (1) ブロック粒度のハイライトで可か(文字オフセット精度は品質 A の bbox 同期精度に依存し完全ではない)、(2) 訳文/対訳 PDF には注釈を埋め込まない(bbox 写像が無いため)で可か。

### 決定 D: 対訳 PDF の扱い

サーバ側に `bilingual_pdf` を生成するコードは無い(フロントがクライアント合成)。スタンドアロン対訳 PDF を出すには:

- **D-1(推奨): 原文 PDF と日本語 PDF を pymupdf でページ交互 or 見開き結合してサーバ生成。** 追加依存なし。
- **D-2: 対訳 PDF は当面提供しない**(readiness で常に false)。

**推奨 = D-1。確認事項:** 交互(原文 p1 → 訳 p1 → …)で可か、それとも別レイアウトを望むか。

### 決定 E: 束ね形式と UI 導線

- **束ね:** 複数選択 → **1 つの zip**(各 HTML は個別にも開けるスタンドアロン、PDF は注釈埋め込み済み単体)。**推奨。** 単一成果物のみ選択時は zip を介さず直接その 1 ファイルを返す簡略化も可(確認事項)。
- **UI 導線:** **ビューアヘッダの ⋯ オーバーフローメニューに「エクスポート」**を追加 → 選択モーダル(readiness で出し分け・複数選択)。`ViewerHeader.tsx` に既存の `⋯` Popover があり自然。ライブラリ設定にも既存の `ExportSettings` があるが、単一論文の文脈はビューアが妥当。**推奨 = ビューアヘッダ。確認事項:** ライブラリ一覧の行メニューにも出すか。

## テスト戦略

- **純レンダラ(DB 非依存):** `apps/api/tests/test_standalone_html.py`。全ブロック種・全インライン種・エスケープ・図 data URI・数式フォールバックマークアップ・訳優先フォールバック・対訳 2 カラム・記事 Markdown サブセットを検証。
- **readiness / HTML エンドポイント(実 PG + fake storage):** `apps/api/tests/test_standalone_export.py`。`test_export.py` と同型(専用アプリに export+関連ルータをマウント、`factories` でシード)。生成済み/未生成の可否、404、data URI 埋め込みを検証。
- **worker(承認後):** zip 構造・PDF 注釈埋め込み(`fitz` で読み戻して annot 数を検証)。
- コマンド: `uv run pytest apps/api -q`、UI は `pnpm --filter @alinea/web test`。

## 受け入れ基準

- [ ] readiness API が 7 成果物の可否を最新リビジョン基準で正しく返す(未生成=false)。
- [ ] 原文/訳文/対訳/記事の単一 HTML が、サーバなしでブラウザで開け、図が data URI で表示され、数式が(決定 A の方式で)描画される。
- [ ] 訳文 HTML は訳優先・未訳は原文フォールバック、対訳 HTML は原文/訳の 2 カラム。
- [ ] 純レンダラが全ブロック種・全インライン種を出力し、HTML エスケープが漏れない。
- [ ] (承認後)複数選択が 1 zip に束ねられ、原文 PDF に注釈が埋め込まれている(`fitz` で検証)。
- [ ] 新エンドポイントに対し SDK(`packages/api-client`)が再生成されている。
