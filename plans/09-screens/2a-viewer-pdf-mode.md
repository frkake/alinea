# 画面 2a: ビューア PDFモード+情報パネル

> 対象読者と前提: 本書は「Alinea — 論文読解ワークベンチ」の apps/web(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4)実装者向けに、確定デザイン画面 2a(論文ビューア PDFモード+右サイドパネル=情報タブ)を 100% 忠実に実装するための仕様を確定させる。機能仕様の正は docs/04(ビューア)§5・§10.3、ピクセル値の正は抽出ファイル extract/2a.md(本書 §4 に全量転記)。共通コンポーネント名は plans/08-design-system.md、API エンドポイント名は plans/03-api.md、データは plans/02-data-model.md に準拠する。PDF レンダリングは PDF.js(`pdfjs-dist`)、状態管理は TanStack Query v5 + Zustand。本書に「決定:」と記した項目はデザイン未描画部分を本書で確定させたものである。

## 1. 概要とルート

### 1.1 ルートパス(確定)

- ルート: **`/papers/[itemId]`**(App Router: `apps/web/src/app/(app)/papers/[itemId]/page.tsx`。パラメータ名は viewer-shell.md §3.1 の `[itemId]` に統一。本書のコード例中の `libraryItemId` は同値)。値は `li_` プレフィックスの ULID(plans/03 §1.6)。plans/03 §3.4 の `viewer_url: "/papers/li_…"` と一致。
- 表示モードは検索パラメータ **`?mode=pdf`** で表現する。値域は `translation | parallel | source | pdf | article`(plans/03 `LastPosition["mode"]` と同一語彙)。決定: パスセグメントではなく検索パラメータとする。理由: モード切替はページ遷移ではなく同一画面内の状態切替であり、`router.replace`(shallow)で履歴を汚さず切り替えるため。
- 追加の検索パラメータ(本画面が読み書きするもの):
  - `page`(int, 1 起点): PDF モードの表示ページ。ページ移動のたびに `router.replace` で同期(履歴エントリは積まない)。決定: `page` は viewer-shell.md §3.1 のクエリ表にない **2a 固有の拡張クエリ**であり、`mode=pdf` のときのみ読み書きし、他モードへの切替時に URL から除去する。
  - `block`(string, `blk-…`): 他モードへの相互リンク遷移先ブロック ID(「この位置を訳文で開く →」「訳文で見る →」が付与する。受け側は viewer-shell §3.2-3 のとおり初期化時に 1 回消費し URL から除去する)。
- `mode` 省略時の既定: `viewer.last_position?.mode ?? "translation"`(docs/04 §11)。つまり本画面は「PDF セグメントをクリックした」「`?mode=pdf` 付き URL を開いた」「前回位置が PDF モードだった」のいずれかで表示される。

### 1.2 認証要否

認証必須(session)。未認証はルートレイアウトのセッション確認(`GET /api/auth/me`)で `/login` へリダイレクト(plans/01 §2.1)。CSR 画面(SSR は共有ページ 4c のみ)。

### 1.3 画面の役割

- オリジナル PDF のページレンダリング表示(レイアウト確認・品質 B 論文の主表示。docs/04 §2)。
- 構造化ビュー(訳文等)との **page+bbox 相互リンク**: ツールバーの同期インジケータ「同期: p.5 ≒ §2.2 Reflow」常時表示、bbox 段落選択→「≒ §2.2 ¶2 — 訳文で見る →」チップ、「この位置を訳文で開く →」ボタン(docs/04 §5)。
- 左サイドバー: 目次⇔ページサムネイル切替、現在ページ強調、ページ数・ファイルサイズ・原文 PDF ダウンロード。
- 右サイドパネル(情報タブ): 書誌情報・品質レベルと取り込みタイムライン・ライセンスカード・エクスポート(docs/04 §10.3)。
- 本画面はビューア 5 モードの 1 つであり、ヘッダ(§4.2.1)・サイドパネルタブ枠(§4.2.5)は 1a/1b/1c/1h/5a と共通実装(`ViewerHeader` / `SidePanelTabs`)を再利用する。

## 2. データ要件

### 2.1 使用 API エンドポイント一覧(plans/03 の名前)

| # | エンドポイント | 用途 | 取得/実行タイミング |
|---|---|---|---|
| 1 | `GET /api/library-items/{id}/viewer`(§6.1) | ビューア初期化複合(書誌・revision・toc・counts・license_card・ingest_timeline・last_position・翻訳進捗) | ルート表示時に 1 回。全モード共有 |
| 2 | `GET /api/papers/{paper_id}/pdf`(§4.4) | 原文 PDF 本体(302→署名 URL)。PDF.js の入力+「⤓ 原文PDF」ダウンロード | PDF モード初回表示時(表示用)/クリック時(DL 用) |
| 3 | `GET /api/revisions/{revision_id}/document`(§6.3) | 構造化ドキュメント全量(blocks の `page`/`bbox` を同期マッピングに使用) | PDF モード初回表示時に全量 1 回(`section_id` 指定なし)。ETag/If-None-Match 対応 |
| 4 | `GET /api/revisions/{revision_id}/search`(§6.7) | 論文内検索(`/`) | 検索クエリ入力 300ms デバウンス |
| 5 | `PATCH /api/library-items/{id}`(§5.4) | ステータスピルの変更 | ドロップダウン選択時 |
| 6 | `PUT /api/library-items/{id}/position`(§5.8) | 読書位置自動保存(`mode: "pdf"`) | ページ変更から 5 秒デバウンス |
| 7 | `POST /api/library-items/{id}/reading-sessions`(§5.9) | 読書時間計測 | 60 秒間隔+`visibilitychange`/`pagehide` 時(全モード共通機構) |
| 8 | `POST /api/papers/{paper_id}/reingest`(§4.2) | 情報パネル「再取り込み」 | クリック→確認後 |
| 9 | `GET /api/papers/{paper_id}/ingest-log`(§4.3) | 情報パネル「処理ログ」 | モーダルを開いた時 |
| 10 | `GET /api/jobs/{job_id}/events`(§21.2) | 再取り込みジョブの進捗 SSE | reingest 202 受領後に購読 |
| 11 | `GET /api/library-items/{id}/export/annotations`(§18) | 「注釈 Markdown ⤓」 | クリック時(`<a download>` ナビゲーション) |
| 12 | `POST /api/library-items/{id}/adopt-revision`(§6.8) | 「新しいバージョンがあります」バナー適用(全モード共通) | バナー CTA クリック時 |

補足(決定):
- **#2 PDF 本体の取得方式**: `fetch('/api/papers/{paper_id}/pdf', { credentials: 'include' })`(リダイレクト自動追従)→ `response.arrayBuffer()` → `pdfjs.getDocument({ data })`。Range リクエストは使わない(最大 50MB(plans/03 §3.3)で単純化を優先)。**ファイルサイズ表示「4.1 MB」は `arrayBuffer.byteLength / 1048576` を小数 1 桁丸め**で導出する(API にサイズフィールドが無いため。単位は常に「MB」固定)。総ページ数(表示・ページクランプ・サムネイル枚数)は、決定: **pdfData 解決後は PDF.js の `numPages` を正**とし、解決前は `viewer.revision.page_count` をプレースホルダとして使う(`page_count` が null の間は「…」表示。§5.2)。両者が不一致の場合は `numPages` を採用して console.warn を出す。
- **#3 を全量取得する理由**: 同期マッピング(§5.4)はページ⇔ブロックの全対応表を要するため。品質 A で PDF アセットを持つ論文もブロックに `page`/`bbox` を持つ(plans/03 §6.3 注記)。
- 「⤓ 原文PDF」「原文 PDF ⤓」のダウンロードは `<a href="/api/papers/{paper_id}/pdf" download>`(302 追従でブラウザ DL)。表示用に取得済みの ArrayBuffer は再利用しない(挙動の単純化)。

### 2.2 TanStack Query キー設計

```ts
// apps/web/src/lib/query-keys.ts(ビューア関連の抜粋)
export const qk = {
  viewer: (libraryItemId: string) => ['viewer', libraryItemId] as const,
  document: (revisionId: string) => ['document', revisionId] as const,          // staleTime: Infinity(リビジョン不変)
  pdfData: (paperId: string) => ['pdf-data', paperId] as const,                 // ArrayBuffer。staleTime: Infinity, gcTime: 10*60_000
  ingestLog: (paperId: string) => ['ingest-log', paperId] as const,            // staleTime: 0(開くたび再取得)
  inPaperSearch: (revisionId: string, q: string) => ['in-paper-search', revisionId, q] as const, // viewer-shell §2.2 と同一キー(enabled: q.length >= 2)
} as const;
```

- `viewer` は `staleTime: 30_000`。`PATCH /api/library-items/{id}` 成功時と reingest ジョブ `done` 受信時に `invalidateQueries({ queryKey: qk.viewer(id) })`。
- `pdfData` の queryFn は上記 fetch→ArrayBuffer。`PDFDocumentProxy` は Query に入れず(構造化クローン不可)、ArrayBuffer から `usePdfDocument` フック内の `useRef` で生成・`destroy()` 管理する。
- ステータス変更(#5)は楽観更新: `onMutate` で `viewer` キャッシュの `library_item.status` を書き換え、失敗時ロールバック+Toast(§5.9)。

### 2.3 リアルタイム更新

- 本画面に常設ポーリングは無い(決定)。表示データ(書誌・タイムライン・ライセンス)は静的であり、翻訳進捗の逐次更新は訳文/対訳モードの責務のため。
- 例外は「再取り込み」実行後のみ: `POST /api/papers/{paper_id}/reingest` → 202 `{ job_id }` → `GET /api/jobs/{job_id}/events` を `EventSource` で購読し、`event: progress` の `stage`+`progress_pct` をタイムライン下に進行行(§5.7)として表示。`event: done` で `viewer` を invalidate+Toast「✓ 再取り込みが完了しました」、`event: error` で Toast(kind: 'error', `title` を表示)。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`(共通)` = plans/08 §5 の共通コンポーネント。`(shell)` = viewer-shell.md が所有するビューア 5 モード共有部。無印 = 本画面固有(担当分担は viewer-shell.md §11: 2a = `PdfPane` / `PdfSidebar` / `InfoTab`)。ファイル配置: `apps/web/src/components/viewer/pdf/`(PdfPane 系・PdfSidebar 系)および `apps/web/src/components/viewer/panel/InfoTab.tsx` 配下(情報タブ系)。

```
app/(app)/papers/[itemId]/page.tsx
└─ ViewerShell (shell)                          … mode 分岐・viewer クエリ・位置保存・読書計測(viewer-shell.md)
   ├─ ViewerHeader (shell)                      … §4.2.1。内部: QualityBadge(共通)/ StatusPill(共通)/
   │                                              SegmentedControl(共通, 5モード)/ SearchBox(共通, in-paper)/
   │                                              Popover(共通, スタイル・ステータス・⋯メニュー)
   ├─ leftPane prop: PdfSidebar                 … §4.2.2(viewer-shell §5.5 の差し替え機構で注入)
   │  ├─ SegmentedControl (共通, 目次/ページ, size='sm')
   │  ├─ TocRow リスト (shell)                  … 「目次」選択時(viewer-shell §5.5: TocPane の内部リストを export して組み込む)
   │  ├─ PdfThumbnailList
   │  │  └─ PdfThumbnailItem ×N
   │  └─ PdfSidebarFooter
   ├─ children: PdfPane                         … mode==='pdf' の本文ペイン(viewer-shell §2.1 の children)
   │  ├─ PdfToolbar                             … §4.2.3
   │  │  └─ Popover (共通, フィットセレクタ)
   │  └─ PdfCanvas                              … §4.2.4
   │     ├─ PdfPageLayer ×1..2(見開き時2)
   │     ├─ PdfBboxHighlight                    … 選択 bbox のオーバーレイ
   │     └─ PdfSyncChip                         … 「≒ §2.2 ¶2 — 訳文で見る →」
   ├─ SidePanel (shell)                         … §4.2.5(枠・タブ・幅・開閉は shell 所有)
   │  ├─ SidePanelTabs (共通)
   │  ├─ InfoTab                                … 「情報」タブ本文(本書担当。SidePanel.tsx が直接 import — viewer-shell §2.1)
   │  │  ├─ InfoBibSection
   │  │  ├─ InfoQualitySection
   │  │  │  └─ IngestTimeline
   │  │  ├─ InfoLicenseCard
   │  │  └─ InfoExportSection
   │  └─ InfoPanelFooter
   ├─ IngestLogModal                            … Modal(共通) 内に処理ログ一覧
   └─ Toast (共通) / ReingestConfirm (Modal 共通)
```

### 3.2 画面固有コンポーネントの props 型

```ts
// apps/web/src/components/viewer/pdf/types.ts
import type { components } from '@alinea/api-client';
type ViewerInit = components['schemas']['ViewerInitResponse'];   // GET /api/library-items/{id}/viewer
type DocumentJson = components['schemas']['DocumentContentJson']; // GET /api/revisions/{id}/document

/** ページ⇔節/段落の同期対応(§5.4 のアルゴリズムで document から導出) */
export interface PdfSyncMap {
  pageToSection: (page: number) => { sectionId: string; display: string } | null; // display="§2.2 Reflow"
  blockAtPoint: (page: number, xPt: number, yPt: number) =>
    { blockId: string; bbox: [number, number, number, number]; display: string } | null; // display="§2.2 ¶2"
  blocksOnPage: (page: number) => { blockId: string; bbox: [number, number, number, number] }[];
  firstBlockOnPage: (page: number) => string | null; // 位置保存用
}

export type PdfFitMode = 'fit-width' | 'fit-page' | 'actual';   // 幅に合わせる / ページ全体 / 実寸

export interface PdfViewState {                 // Zustand: usePdfViewStore(libraryItemId ごとに reset)
  page: number;                                 // 1 起点
  zoom: number;                                 // 0.25–4.0(表示は round(zoom*100)+'%')
  fitMode: PdfFitMode | null;                   // null = 手動ズーム中
  spread: boolean;                              // 見開き
  selectedBlockId: string | null;               // bbox 選択中ブロック
  sidebarTab: 'toc' | 'pages';                  // 左サイドバー切替(2a 固有。既定 'pages')
  setPage(p: number): void; zoomIn(): void; zoomOut(): void;
  setFitMode(m: PdfFitMode): void; toggleSpread(): void;
  selectBlock(id: string | null): void;
  setSidebarTab(t: 'toc' | 'pages'): void;
}

export interface PdfPaneProps {                  // viewer-shell の children に入る本文ペイン
  libraryItemId: string;
  viewer: ViewerInit;
  onOpenInTranslation: (blockId: string) => void; // router.replace(`?mode=translation&block=${blockId}`)
}

export interface PdfSidebarProps {
  tab: 'toc' | 'pages';                          // 既定 'pages'(PDF モード。docs/04 §5)
  onTabChange: (t: 'toc' | 'pages') => void;
  toc: ViewerInit['toc'];
  pageCount: number;
  fileSizeMb: number | null;                     // ArrayBuffer 取得前は null → フッタは「24 ページ · …」(サイズ部のみ「…」、「MB」は付けない。決定)
  currentPage: number;
  onSelectPage: (page: number) => void;
  pdfDownloadHref: string;                       // `/api/papers/${paperId}/pdf`
  renderThumbnail: (page: number, canvas: HTMLCanvasElement) => Promise<void>; // usePdfDocument から注入
}

export interface PdfThumbnailItemProps {
  page: number;
  selected: boolean;
  onClick: () => void;
  renderThumbnail: PdfSidebarProps['renderThumbnail'];
}

export interface PdfToolbarProps {
  page: number; pageCount: number;
  zoomPct: number;                               // 128 など
  fitMode: PdfFitMode | null;
  spread: boolean;
  syncDisplay: string | null;                    // "p.5 ≒ §2.2 Reflow"(マッピング不能時 null → §5.4)
  onPageChange: (p: number) => void;
  onZoomIn: () => void; onZoomOut: () => void;
  onFitModeChange: (m: PdfFitMode) => void;
  onToggleSpread: () => void;
  onOpenInTranslation: () => void;               // 現在ページ先頭ブロックで相互リンク
}

export interface PdfCanvasProps {
  document: DocumentJson;                        // bbox 描画用
  syncMap: PdfSyncMap;
  state: PdfViewState;
  renderPage: (page: number, canvas: HTMLCanvasElement, scale: number) => Promise<void>;
  pageSizePt: { width: number; height: number }; // PDF.js page.view 由来
  onJumpToTranslation: (blockId: string) => void;
}

// ---- 情報タブ(apps/web/src/components/viewer/panel/InfoTab.tsx とその子) ----
// InfoTab 自体は props を受けない(viewer-shell §2.1・§6 の決定: 各タブ本体は
// useViewerStore() と useParams() から itemId / revisionId を取得し、viewer クエリを購読する)。
// 以下は InfoTab 内部の子コンポーネントの props。
export interface InfoBibSectionProps { paper: ViewerInit['library_item']['paper'] }
export interface InfoQualitySectionProps {
  quality: 'A' | 'B';
  timeline: ViewerInit['ingest_timeline'];       // { at, label }[]
  reingestJob: { stage: string; progressPct: number } | null; // 実行中のみ(§5.7)
  onReingest: () => void; onOpenIngestLog: () => void;
}
export interface InfoLicenseCardProps { licenseCard: ViewerInit['license_card'] }
export interface InfoExportSectionProps { annotationsExportHref: string; pdfDownloadHref: string }
```

- ステータス列挙の対応(注意): API の `Status` は `planned | up_next | reading | done | reread | on_hold`(plans/03 §1.6)。`StatusPill`(plans/08 §5.2)へは API 値をそのまま渡し、色・ラベルは `STATUS_COLORS` / `STATUS_LABELS`(plans/08 §2.4)のキーを API 値に揃えて解決する。
- Zustand ストア: `usePdfViewStore`(上記 `PdfViewState`+左サイドバーの `sidebarTab: 'toc' | 'pages'`。2a 固有)と、全モード共通状態は viewer-shell §2.3 の **`useViewerStore`**(`activeTab`(サイドパネル)・`style`・`currentBlockId` 等)を使う。`usePdfViewStore` は永続化しない(URL の `mode`/`page` が正)。

## 4. レイアウト・スタイル完全仕様

出典: extract/2a.md(全量)。色は実装時に plans/08 のトークンへ置換する(対応: #FBFAF7=`--pr-bg-app`、#FFFFFF=`--pr-bg-card`、#E6E3DA=`--pr-border-header`、#F7F6F2=`--pr-bg-pane`、#E7E4DB=`--pr-border-pane`、#DDD9CF=`--pr-border-control`、#ECE9DF=`--pr-border-soft`、#F0EDE4=`--pr-border-hair`、#EFEDE6=`--pr-bg-muted`、#EBE8E0=左バーセグメントトラック(決定: `--pr-bg-muted` ではなく実測値 #EBE8E0 を任意値 `bg-[#EBE8E0]` 相当のローカル定数 `--pr-bg-muted-pane` として tokens.css に追加はせず、コンポーネント内 style 定数で保持。ESLint の hex 禁止ルールの例外コメントを付す)、#F1EFE9=`--pr-bg-inset`、#DAD7CD=`--pr-border-keycap`、#DDDAD1=PDF キャンバス背景(決定: plans/08 の `--pr-bg-canvas`(#E8E6DF)とは実測が異なるため、#EBE8E0 と同方式のコンポーネント内 style 定数(hex 例外コメント付き)で保持し、ダークテーマでは `var(--pr-bg-canvas)`(#14171B)に切替)、テキスト系は §4.2 の各所に対応トークンを併記)。アクセント表記の注意(決定): 本書の `var(--pr-a)` / `var(--pr-as)` / `var(--pr-am)` は実装ではすべて意味エイリアス `var(--pr-acc)` / `var(--pr-acc-s)` / `var(--pr-acc-m)`(plans/08 §2.2。ダークテーマで自動的に `--pr-ad` 系へ切替)として参照する。

### 4.1 レイアウト構造

フレーム: 1440×900px 基準(実アプリではビューポート全面。plans/08 §7.1)、背景 #FBFAF7、文字色 #1E2227(`--pr-text`)。デザインキャンバスの border/radius/shadow は実装しない。

```
┌────────────────────────────────────────────────────────────────── 1440px ─┐
│ ヘッダ h=52px 背景#FFFFFF 下線1px #E6E3DA                                  │
├──────────────┬───────────────────────────────────────────┬────────────────┤
│ 左サイドバー  │ 中央: PDFキャンバス列 (flex:1)              │ 右サイドパネル │
│ w=232px      │ ┌───────────────────────────────────────┐ │ w=340px        │
│ 背景#F7F6F2  │ │ PDFツールバー h=38px 背景#FFFFFF       │ │ 背景#FFFFFF    │
│ 右線1px      │ │ 下線1px #E6E3DA                        │ │ 左線1px        │
│ #E7E4DB      │ ├───────────────────────────────────────┤ │ #E7E4DB        │
│ (サムネイル) │ │ キャンバス 背景#DDDAD1                 │ │ ・タブ列        │
│              │ │ 中央寄せ padding-top:20px              │ │ ・情報本文      │
│              │ │  PDFページ 700×906px 白               │ │ ・フッタ注記    │
│              │ └───────────────────────────────────────┘ │                │
├──────────────┘                                           └────────────────┤
└──────────────────────────────────────────────────────── 高さ計 900px ─────┘
```

- ヘッダ: height:52px、flex:none、背景 #FFFFFF、border-bottom:1px solid #E6E3DA、flex 横並び、align-items:center、gap:10px、padding:0 16px。
- 本体行: flex:1、display:flex、min-height:0。
- 左サイドバー: width:232px、flex:none、背景 #F7F6F2、border-right:1px solid #E7E4DB、flex 縦、padding:10px 8px 8px。
- 中央列: flex:1、min-width:0、flex 縦、overflow:hidden。
- 右サイドパネル: width:340px、flex:none、背景 #FFFFFF、border-left:1px solid #E7E4DB、flex 縦。
- 1440px 超では中央列のみ広がる。1440px 未満では中央列が縮小(最小 560px)、アプリ全体最小幅 1200px(plans/08 §7.2)。

### 4.2 コンポーネント詳細(上から順)

#### 4.2.1 ヘッダ(h=52px)— `ViewerHeader`

左→右の順で(gap:10px):

1. **戻る矢印**: テキスト「‹」、font-size:16px、色 #8A8E94(`--pr-text-icon`)、width:20px、text-align:center。クリック時の挙動: `router.back()`、履歴が無い場合(`window.history.length <= 1`。新規タブ・深リンク直開き)は `router.push('/library')`(viewer-shell §4.2-1 の決定に従う)。
2. **論文タイトル**: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」。font-size:13px、font-weight:600、max-width:330px、white-space:nowrap、overflow:hidden、text-overflow:ellipsis。データ: `viewer.library_item.paper.title`。
3. **品質バッジ「A」**: `QualityBadge`(共通, size=18)。inline-flex 中央揃え、18×18px、border-radius:4px、背景 var(--pr-as)、文字色 var(--pr-a)、font-size:10.5px、font-weight:700。`title="品質レベルA: LaTeXソースから完全構造化"`。データ: `viewer.revision.quality_level`。
4. **ステータスピル「読んでいる」**: `StatusPill`(共通, size='md', interactive)。inline-flex、gap:5px、height:24px、padding:0 9px、border:1px solid #DDD9CF、border-radius:999px、font-size:11.5px、font-weight:500、背景 #FFFFFF。内部: ドット(7×7px、border-radius:50%、背景 var(--pr-a)=reading 色)+テキスト「読んでいる」+キャレット「▾」(色 #9A9EA4、font-size:9px)。クリックで 6 値ドロップダウン(Popover width 180px)。
5. **スペーサー**(flex:1)。
6. **表示モード切替**: `SegmentedControl`(共通, size='md', 5 値)。外枠 flex、背景 #EFEDE6、border-radius:7px、padding:2px、gap:2px。各セグメント: height:24px、inline-flex、align-items:center、padding:0 11px、border-radius:5px、font-size:11.5px。
   - 非選択(「訳文」「対訳」「原文」「記事」): 色 #5B6067(`--pr-text-sub`)、背景なし。
   - 選択中(「PDF」): 背景 #FFFFFF、色 #1E2227、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)(`--pr-shadow-seg`)。
7. **スタイルセレクタ**: 「スタイル: 自然訳 ▾」。inline-flex、gap:5px、height:26px、padding:0 10px、border:1px solid #DDD9CF、border-radius:6px、font-size:11.5px、色 #3C4046(`--pr-text-mid`)。「▾」は色 #9A9EA4、font-size:9px。ラベルは `viewer.translation.style`(natural=「自然訳」/ literal=「直訳」)。クリックで 2 値ドロップダウン(項目「自然訳」「直訳」+各行に説明なし。切替時の挙動(POST 発火条件・localStorage 保存を含む)は viewer-shell §4.4 の確定仕様が正。実装は shell 担当)。
8. **検索ボックス**: `SearchBox`(共通, variant='in-paper')。inline-flex、gap:6px、height:26px、padding:0 10px、背景 #F1EFE9、border-radius:6px、font-size:11.5px、色 #8A8E94(プレースホルダ色)、width:150px。内部: 虫眼鏡 SVG=`MagnifierIcon`(11×11、viewBox 0 0 12 12、circle cx=5 cy=5 r=3.6 stroke currentColor 幅1.3+path M8 8l2.6 2.6 幅1.3 linecap round)、プレースホルダ「この論文内を検索」、右端(margin-left:auto)に `Keycap`「/」(border:1px solid #DAD7CD、border-radius:3px、padding:0 4px、font-size:9.5px、背景 #FFFFFF)。
9. **オーバーフローメニュー「⋯」**: font-size:15px、色 #5B6067、letter-spacing:1px。クリックで Popover(width 200px, placement 'bottom-end')。項目は viewer-shell §4.2-9 の確定リストに従う(①「サイドパネルを表示/隠す」②「注釈 Markdown ⤓」③「原文 PDF ⤓」(PDF なし論文では非表示)④「再取り込み」(確認なし即時発行+Toast)⑤「処理ログ」(情報タブを開く))。全ビューアモード共通・実装は shell 担当。本書の情報タブ内「再取り込み」(§5.7。確認モーダルあり)とは経路が異なる点に注意。

#### 4.2.2 左サイドバー(ページサムネイル、w=232px)— `PdfSidebar`

1. **目次/ページ切替**: `SegmentedControl`(共通, size='sm', 2 値)。外枠 flex、背景 #EBE8E0、border-radius:6px、padding:2px、gap:2px、margin:0 6px 10px。各セグメント flex:1、height:22px、inline-flex 中央揃え、border-radius:4px、font-size:11px。
   - 「目次」: 非選択、色 #5B6067。選択すると目次リスト(viewer-shell §5.5: `TocPane` の内部 `TocRow` リストを export して組み込む)に切替。
   - 「ページ」: 選択中(PDF モード既定)、背景 #FFFFFF、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)。
2. **サムネイルリスト** `PdfThumbnailList`: flex:1、**overflow-y:auto**(デザインは overflow:hidden の静止画。実装は縦スクロール。決定)、flex 縦、align-items:center、gap:12px、padding-top:2px。全ページ分の `PdfThumbnailItem`(各: flex 縦、中央揃え、gap:5px でサムネイル+ページ番号)。IntersectionObserver で可視範囲±2 枚のみ PDF.js レンダリング(width 112px × devicePixelRatio)、未レンダリングはスケルトン(§5.2)。
   - **通常サムネイル**(例: ページ4・6): 112×145px、背景 #FFFFFF、border:1px solid #DDD9CF、border-radius:3px、box-sizing:border-box。実装ではレンダリング済み `<canvas>` を内包(モックのスケルトン行はローディング状態として §5.2 で使用)。下にページ番号(font-size:10px、色 #9A9EA4)。
   - **選択中サムネイル**(例: ページ5): 112×145px、背景 #FFFFFF、**border:2px solid var(--pr-a)**(枠 2px 化に伴い内容は 1px 分縮小)、border-radius:3px、box-shadow:0 4px 12px rgba(28,30,34,0.12)。ページ番号はバッジ形式: inline-flex 中央揃え、min-width:20px、height:16px、border-radius:8px、背景 var(--pr-a)、文字 #FFFFFF、font-size:10px、font-weight:700。
   - モック内スケルトンの寸法(ローディング表示に流用): 行 span height:3px、背景 #E8E5DC、border-radius:1px、幅 90%/100%/70%、図ブロック height:26px 背景 #F1EFE9 border-radius:2px margin:4px 0、続き 100%/85%/92%/60%。パディング 12px 10px(選択中は 11px 9px)、行間 gap:3px。選択中スケルトンは 2 本目=var(--pr-as) 幅 100%、3 本目=var(--pr-am) 幅 88%(現在ハイライト位置の示唆)。
   - ページ変更時、選択サムネイルが可視範囲外なら `scrollIntoView({ block: 'nearest' })`(決定)。
3. **サイドバーフッタ** `PdfSidebarFooter`: padding:8px 8px 2px、border-top:1px solid #E7E4DB、flex、align-items:center、justify-content:space-between、font-size:10.5px、色 #9A9EA4。左「24 ページ · 4.1 MB」(`page_count` と §2.1 のサイズ導出。区切りは「 · 」)、右「⤓ 原文PDF」(`<a>`、色は左と同じ #9A9EA4。ホバーで色 var(--pr-a)。決定)。

#### 4.2.3 中央: PDFツールバー(h=38px)— `PdfToolbar`

背景 #FFFFFF、border-bottom:1px solid #E6E3DA、flex、align-items:center、gap:10px、padding:0 14px。左→右:

1. **ページナビゲーション**: inline-flex、gap:7px、font-size:11.5px、色 #3C4046。「‹」(色 #9A9EA4、button)+ページ番号入力欄(`<input type="text" inputmode="numeric">`、border:1px solid #DDD9CF、border-radius:4px、padding:1px 8px、背景 #FFFFFF、font-weight:600、幅は 3 桁分 `width:34px`(決定)、値「5」)+「/ 24」+「›」(色 #9A9EA4、button)。
2. **縦区切り線**: 1×16px、背景 #E2DFD5(`--pr-border-card`)。
3. **ズームコントロール**: inline-flex、gap:9px、font-size:11.5px、色 #3C4046。「−」(色 #9A9EA4、button)、「128%」(現在ズーム率)、「+」(色 #9A9EA4、button)。
4. **ズームフィットセレクタ**: 「幅に合わせる ▾」。inline-flex、gap:5px、height:24px、padding:0 9px、border:1px solid #DDD9CF、border-radius:6px、font-size:11px、色 #3C4046。「▾」は font-size:8.5px、色 #9A9EA4。クリックで Popover(width 180px, 'bottom-start')。項目(決定): 「幅に合わせる」「ページ全体」「実寸(100%)」。
5. **見開きトグル**: 「見開き」。inline-flex、height:24px、padding:0 9px、border:1px solid #DDD9CF、border-radius:6px、font-size:11px、色 #5B6067(非アクティブ)。アクティブ時(決定): border:1px solid var(--pr-am)、色 var(--pr-a)、背景 var(--pr-as)、font-weight:600(項目 8 の相互リンクボタンと同スタイル)。
6. **スペーサー**(flex:1)。
7. **同期インジケータ**: font-size:11px、色 #9A9EA4。テキスト「同期: 」+太字部分「p.5 ≒ §2.2 Reflow」(`<b>`、色 #3C4046)。値は §5.4 の `pageToSection`。
8. **相互リンクボタン**: 「この位置を訳文で開く →」。inline-flex、height:24px、padding:0 10px、border:1px solid var(--pr-am)、border-radius:6px、font-size:11px、色 var(--pr-a)、背景 var(--pr-as)、font-weight:600。

#### 4.2.4 中央: PDFキャンバス — `PdfCanvas`

flex:1、背景 #DDDAD1、**overflow:auto**(デザインは overflow:hidden の静止画。実装は両軸スクロール。決定)、flex、justify-content:center(コンテンツ幅がビューポート以下のとき)、padding-top:20px、padding-bottom:20px(決定: 下端余白を上端と対称にする)。

**PDFページ(1 枚)** `PdfPageLayer`: デザイン実測 700×906px(=幅フィット時の 1440px レイアウトでの寸法。実装ではズームに応じ可変)、背景 #FFFFFF、box-shadow:0 6px 28px rgba(28,30,34,0.22)、flex:none、position:relative。内部は PDF.js の `<canvas>`(devicePixelRatio 対応)+bbox オーバーレイ用の絶対配置 `<div>` 層。見開き時は 2 枚を gap:16px で横並び(決定。偶奇ペア: p2-3, p4-5…。p1 は単独右置き、最終ページが偶数で相方が無い場合は単独左置き)。

デザインのページ内容(2 カラム英文・図 2 ボックス・数式(7)・ランニングヘッダ「Published as a conference paper at ICLR 2023」・フッタページ番号「5」)は**実 PDF のレンダリング結果**であり、HTML では再現しない。モック逐語は §4.3 の UI 文言一覧に保持する(VRT はシード PDF で行う。§6)。

**bbox ハイライト** `PdfBboxHighlight`(選択状態): 選択ブロックの bbox(pt)を `viewport.convertToViewportRectangle` で css px に変換した矩形に、padding:3px 4px 相当の外側拡張(上下左右 +3px。決定)、背景 var(--pr-as)、outline:1.5px solid var(--pr-a)、border-radius:2px、pointer-events:none。

**フローティングチップ** `PdfSyncChip`: position:absolute、ハイライト矩形の top:-23px、right:0。inline-flex、gap:5px、height:20px、padding:0 8px、背景 var(--pr-a)、文字 #FFFFFF、border-radius:4px、font-size:10px、font-weight:600、font-family:'IBM Plex Sans JP',sans-serif(`--pr-font-ui`)、box-shadow:0 6px 16px rgba(28,30,34,0.25)。テキスト「≒ §2.2 ¶2 — 訳文で見る →」(`≒ ${display} — 訳文で見る →`)。クリック可能(cursor:pointer)。

#### 4.2.5 右サイドパネル(論文情報、w=340px)— `SidePanel`(shell)+ `InfoTab`(本書)

1. **タブ列**: `SidePanelTabs`(共通)。flex、border-bottom:1px solid #ECE9DF、padding:0 6px。各タブ padding:10px 9px 8px、font-size:12px。
   - 非選択タブ(色 #777B81): 「チャット」「メモ」「注釈 6」(カウントは `CountBadge` variant='tab': font-size:10px、色 #9A9EA4、margin-left:3px)「図表」「リソース 4」(同様)。
   - 選択タブ「情報」: font-weight:600、色 var(--pr-a)、box-shadow:inset 0 -2px var(--pr-a)。
   - カウントは `viewer.counts.annotations` / `viewer.counts.resources`。
2. **本文** `InfoTab`: flex:1、**overflow-y:auto**(決定。デザインは hidden)、padding:14px、flex 縦、gap:14px、font-size:12px。セクション間区切りは height:1px、背景 #F0EDE4 の水平線(`<div role="separator">`)。

   **(a) 書誌情報セクション** `InfoBibSection`(flex 縦、gap:6px):
   - セクション見出し「書誌情報」: font-size:10.5px、font-weight:700、色 #9A9EA4、letter-spacing:0.4px。
   - タイトル: font-size:12.5px、font-weight:600、line-height:1.55。`paper.title`。
   - 著者: font-size:11px、色 #5B6067、line-height:1.6。`paper.authors.join(', ')`(例「Xingchao Liu, Chengyue Gong, Qiang Liu」)。
   - チップ行(flex、flex-wrap:wrap、gap:5px、padding-top:2px)。各チップ: height:19px、inline-flex、align-items:center、padding:0 8px、border:1px solid #DDD9CF、border-radius:4px、font-size:10.5px。
     - venue チップ「ICLR 2023」: 色 #3C4046、非リンク。`paper.venue`(null なら非表示)。
     - 「arXiv:2209.03003 ↗」: 色 var(--pr-a)、font-weight:600。`<a target="_blank" rel="noopener noreferrer" href="https://arxiv.org/abs/{arxiv_id}{arxiv_version}">`(決定: `arxiv_version` が null なら省略し `abs/{arxiv_id}`。チップ文言も同様に `arXiv:{arxiv_id} ↗` でバージョンは付けない)。`paper.arxiv_id` null なら非表示。
     - 「DOI ↗」: 色 var(--pr-a)、font-weight:600。`href="https://doi.org/{doi}"`。`paper.doi` null なら非表示。

   **(b) 品質レベルと取り込みセクション** `InfoQualitySection`(flex 縦、gap:8px):
   - 見出し「品質レベルと取り込み」(見出しスタイル同上)。
   - 品質行(flex、gap:9px、align-items:flex-start): バッジ「A」26×26px、border-radius:6px、背景 var(--pr-as)、文字 var(--pr-a)、font-size:13px、font-weight:700、flex:none(決定: `QualityBadge` は 18/17px のみのため、この 26px 版は `InfoQualitySection` ローカルの `QualityBadgeLarge` として実装。B は `--pr-bg-inset`/`--pr-text-sub2` 配色で同寸)。説明文: font-size:11px、色 #5B6067、line-height:1.65。A=「LaTeX ソースから完全構造化。数式・相互参照・図表・脚注を保持しています。」/ B=「PDF から抽出して構造化。レイアウト由来の誤りが残る可能性があります。」(決定: B の説明文。docs/02 の品質定義に基づく最短表現)。
   - **取り込みタイムライン** `IngestTimeline`(flex 縦、gap:0、padding-left:3px)。データ: `viewer.ingest_timeline`(`{ at, label }[]`)。各行: flex、gap:9px、font-size:10.5px、色 #5B6067。左に縦型マーカー(flex 縦・align-items:center): ドット 7×7px、border-radius:50%、背景 #659471(`--pr-green`)、margin-top:3px、その下に接続線(width:1.5px、flex:1、背景 #E7E4DB。最終行は線なし)。テキスト側 padding-bottom:10px(最終行なし)。
     - 表示文言(逐語例): 「7/02 21:04 — arXiv から LaTeX ソース取得」「21:05 — 構造化・図表抽出(24p / 図8 / 表4)」「21:09 — 全文翻訳 完了(自然訳 · v3)· 付録は未翻訳」。
     - タイムスタンプ整形(決定): 1 行目は `M/DD HH:mm`(月は 0 なし・日は 2 桁 0 詰め)、2 行目以降は直前行と同一日付なら `HH:mm` のみ。区切りは「 — 」(前後半角スペース+em dash)。`label` はサーバー供給文字列をそのまま連結。
   - アクションリンク行(flex、gap:12px、font-size:10.5px、padding-left:3px): 「再取り込み」(`<button>`、色 var(--pr-a)、font-weight:600)、「処理ログ」(`<button>`、色 #8A8E94)。

   **(c) ライセンスセクション** `InfoLicenseCard`(flex 縦、gap:7px):
   - 見出し「ライセンス」。
   - 緑のカード(figure_reuse='allowed' のとき): border:1px solid rgba(101,148,113,0.4)、背景 rgba(101,148,113,0.10)、border-radius:8px、padding:9px 11px、flex 縦、gap:3px。
     - 1 行目「CC BY 4.0 — 図表転載可」: font-size:11.5px、font-weight:700、色 #4C7458。
     - 2 行目「記事への図表埋め込み時、クレジットを自動付記します。」: font-size:10px、色 #5B6067、line-height:1.6。
   - 文言は `viewer.license_card.message` をサーバー供給値のまま表示。非許可系の配色(決定): `figure_reuse` が `forbidden` のとき枠 rgba(176,104,79,0.4)・背景 rgba(176,104,79,0.10)・1 行目色 `--pr-warn`(#A05A42)、それ以外(`allowed_with_sa`/`allowed_nc`/`allowed_nd`/`unknown` 相当)は枠 #DDD9CF・背景 #F7F5EF・1 行目色 #3C4046。

   **(d) エクスポートセクション** `InfoExportSection`(flex 縦、gap:7px):
   - 見出し「エクスポート」。
   - ボタン行(flex、gap:6px)。各ボタン: flex:1、inline-flex 中央揃え、height:28px、border:1px solid #DDD9CF、border-radius:6px、font-size:11px、色 #3C4046。
     - 「注釈 Markdown ⤓」→ `<a download href="/api/library-items/{id}/export/annotations">`
     - 「原文 PDF ⤓」→ `<a download href="/api/papers/{paper_id}/pdf">`
3. **フッタ注記** `InfoPanelFooter`: padding:10px 14px、border-top:1px solid #ECE9DF、font-size:10px、色 #9A9EA4、line-height:1.6。「読書時間を記録しています(設定でオフにできます)」。設定 `reading.track_reading_time=false` のユーザーには「読書時間の記録はオフです(設定でオンにできます)」と表示(決定)。

### 4.3 全 UI 文言(逐語)

#### ヘッダ
- ‹
- Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow(データ例)
- A
- 読んでいる ▾
- 訳文 / 対訳 / 原文 / PDF / 記事
- スタイル: 自然訳 ▾
- この論文内を検索(プレースホルダ)/ キーキャップ「/」
- ⋯

#### 左サイドバー
- 目次 / ページ
- 4 / 5 / 6(ページ番号。5 は選択バッジ)
- 24 ページ · 4.1 MB
- ⤓ 原文PDF

#### PDFツールバー
- ‹ 5 / 24 ›
- − 128% +
- 幅に合わせる ▾
- 見開き
- 同期: p.5 ≒ §2.2 Reflow
- この位置を訳文で開く →

#### PDFページ上(実 PDF レンダリング+オーバーレイ。モック逐語は VRT シード用)
- Published as a conference paper at ICLR 2023
- 2.2  REFLOW: STRAIGHTENING THE FLOW
- The paths of the rectified flow may still be curved, because the flow is only guaranteed to match the marginal distributions of the linear interpolation X_t, not its paths. A natural idea is to recursively apply the rectification procedure, using the couplings generated by the previous flow as training pairs for the next one.
- Denote by Z^k = RectFlow((Z^{k−1}_0, Z^{k−1}_1)) the k-th rectified flow. Each reflow step provably reduces the convex transport costs and straightens the paths; after two or three steps the trajectories become nearly straight and can be simulated accurately with a single Euler step.(ハイライト段落)
- ≒ §2.2 ¶2 — 訳文で見る →(フローティングチップ。オーバーレイ=実装対象)
- Formally, we define the straightness measure of Z as
- S(Z) = ∫₀¹ 𝔼‖(Z₁ − Z₀) − Ż_t‖² dt  (7)
- where S(Z) = 0 implies exactly straight paths. In practice, we observe that S(Z^k) decays rapidly with k, as shown in Figure 3. Hence a small number of reflow iterations is sufficient for most generative tasks, and further iterations trade estimation error against straightness.
- 図2(原論文の画像)(プレースホルダ)
- Figure 2: Trajectories of 1-, 2-, 3-rectified flows on toy examples. The paths are straightened by each reflow step.
- Combined with distillation, the straightened flows yield one-step generators: on CIFAR-10 we obtain FID 4.85 with a single Euler step, substantially improving over prior distillation of diffusion ODEs (see Table 1).
- Note that distillation and rectification are complementary: rectification produces a new, straighter flow, while distillation approximates the map z₀ ↦ z₁ of a given flow. Applying distillation after one or two reflow steps inherits the low transport cost while removing the remaining simulation error.
- 2.3  A NONLINEAR EXTENSION
- We can generalize the linear interpolation X_t to any time-differentiable curve that connects X_0 and X_1, recovering variance-preserving and variance-exploding processes as special cases…
- 5(ページ番号フッタ)

#### 右サイドパネル
- チャット / メモ / 注釈 6 / 図表 / リソース 4 / 情報
- 書誌情報
- Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow
- Xingchao Liu, Chengyue Gong, Qiang Liu
- ICLR 2023 / arXiv:2209.03003 ↗ / DOI ↗
- 品質レベルと取り込み
- A
- LaTeX ソースから完全構造化。数式・相互参照・図表・脚注を保持しています。
- 7/02 21:04 — arXiv から LaTeX ソース取得
- 21:05 — 構造化・図表抽出(24p / 図8 / 表4)
- 21:09 — 全文翻訳 完了(自然訳 · v3)· 付録は未翻訳
- 再取り込み / 処理ログ
- ライセンス
- CC BY 4.0 — 図表転載可
- 記事への図表埋め込み時、クレジットを自動付記します。
- エクスポート
- 注釈 Markdown ⤓ / 原文 PDF ⤓
- 読書時間を記録しています(設定でオフにできます)

#### 本書追加分(デザイン未描画。決定文言)
- フィットセレクタ項目: 幅に合わせる / ページ全体 / 実寸(100%)
- 再取り込み確認モーダル: 見出し「再取り込みしますか?」/ 本文「最新のソースから構造化と翻訳をやり直します。注釈は新しいリビジョンへ自動で引き継がれます(位置を失った注釈は「未配置」として残ります)。」/ ボタン「キャンセル」「再取り込み」
- 処理ログモーダル見出し: 「処理ログ」
- Toast: 「✓ 再取り込みが完了しました」/「× 再取り込みに失敗しました」/「× PDF を読み込めませんでした」
- PDF 読み込みエラー面: 「PDF を読み込めませんでした」+「再試行」ボタン
- PDF アセット無し(404): PDF セグメント disabled、tooltip「この論文には PDF がありません」

### 4.4 データフィールド対応表

| 画面要素 | データソース |
|---|---|
| タイトル・著者・venue・arXiv・DOI | `viewer.library_item.paper`(PaperBib) |
| 品質バッジ A/B | `viewer.revision.quality_level` |
| 読書ステータス | `viewer.library_item.status`(PATCH で更新) |
| 総ページ数(24) | `viewer.revision.page_count`(pdfData 解決後は PDF.js `numPages` が正。§2.1) |
| PDF ファイルサイズ(4.1 MB) | `pdfData` ArrayBuffer の byteLength(§2.1) |
| 図数・表数(図8 / 表4) | `viewer.revision.figure_count` / `table_count`(タイムライン文言はサーバー生成) |
| 取り込みタイムライン | `viewer.ingest_timeline[]` |
| 翻訳スタイル・進捗 | `viewer.translation.style` / `.progress_pct` |
| ライセンスカード | `viewer.license_card` |
| タブ件数(注釈 6・リソース 4) | `viewer.counts.annotations` / `.resources` |
| 現在ページ・ズーム・フィット・見開き | `usePdfViewStore`(URL `page` と双方向同期) |
| 同期マッピング(p.5 ≒ §2.2 / §2.2 ¶2) | `GET /api/revisions/{revision_id}/document` の blocks(`page`/`bbox`)+`viewer.toc` から §5.4 で導出 |
| 前回位置 | `viewer.last_position`(保存は PUT position, mode:"pdf") |

## 5. 状態とインタラクション

### 5.1 デザインに描かれた状態(そのまま実装)

- 表示モード切替: 「PDF」選択中(白背景+shadow+太字)。他 4 モードは非選択。クリックで `?mode=` を書き換え(`router.replace`)。
- 左サイドバー切替: 「ページ」選択中、「目次」非選択。切替は `usePdfViewStore.sidebarTab`。PDF モード進入時の既定は 'pages'(docs/04 §5)。
- サムネイル: 現在ページ=2px アクセント枠+shadow+アクセント色番号バッジ。他ページ=1px 枠+グレー番号。クリックで該当ページへジャンプ。
- bbox 選択状態: ハイライト(背景 var(--pr-as)+outline 1.5px var(--pr-a))+直上にチップ「≒ §2.2 ¶2 — 訳文で見る →」。
- 同期インジケータ: ページ⇔節対応が確立している状態で「同期: p.5 ≒ §2.2 Reflow」。
- タブカウントバッジ: 注釈=6、リソース=4(実データ)。
- 取り込みタイムライン: 全ステップ完了(緑ドット)。部分完了は文言(「付録は未翻訳」)で表現(サーバー文字列)。
- ライセンスカード: 転載可(緑)。
- ページ番号入力欄: 編集可能 input。
- ズーム 128%・フィット「幅に合わせる」・「見開き」オフ。

### 5.2 ローディング状態(決定)

- **画面全体**(viewer クエリ未解決): ヘッダはタイトル位置に 330×13px のスケルトンバー(背景 #E8E5DC、border-radius:3px、`animation: pulse 1.6s ease-in-out infinite` opacity 1→0.55)。3 ペインの枠(サイドバー/ツールバー/パネル)は即描画し、パネル本文は 3 本のスケルトン行(高さ 12px、幅 80%/60%/70%、gap 10px、同色)。
- **PDF 本体ロード中**(pdfData 未解決): キャンバス中央に 700×906px の白ページ枠(box-shadow 同値)+中央にテキスト「PDF を読み込んでいます…」(font-size:11.5px、色 #9A9EA4)。ツールバーのズーム表示は「—%」、ページナビは「‹ {URL の page ?? 1} / {page_count ?? "…"} ›」(決定: 入力欄は disabled)。
- **サムネイル未レンダリング**: §4.2.2 のモックスケルトン(行 3px×3+図ブロック+行 4 本)をそのまま流用(静的。アニメーションなし)。
- **document クエリ未解決**: 同期インジケータは「同期: —」(太字部なし)、bbox 選択・相互リンクボタンは disabled(opacity:0.5)。

### 5.3 エラー状態(決定)

- viewer 404/403: 全面 EmptyState(共通)「論文が見つかりません」+説明「削除されたか、アクセス権がありません。」+アクション「ライブラリへ戻る」。
- PDF fetch 失敗/パース失敗: キャンバス中央に EmptyState「PDF を読み込めませんでした」+アクション「再試行」(`refetch`)。Toast「× PDF を読み込めませんでした」を併発。
- PDF アセット無し(`GET /api/papers/{paper_id}/pdf` が 404): ヘッダの「PDF」セグメントを disabled(opacity:0.5、cursor:not-allowed、`title="この論文には PDF がありません"`)にし、URL 直打ちで `mode=pdf` に来た場合は `mode=translation` へ `router.replace`(黙って壊さず遷移。P3)。
- 再取り込み 409 `conflict`(実行中): Toast「× 再取り込みは既に実行中です」。

### 5.4 同期マッピング(page+bbox 相互リンク)のアルゴリズム(決定)

`buildPdfSyncMap(document, toc)`(`apps/web/src/components/viewer/pdf/sync-map.ts`)で一度だけ構築(`useMemo`)。

1. **ページ→節**: `page` が一致するブロックを持つ各セクションについて bbox 面積 `(x1−x0)×(y1−y0)` の合計を取り、最大のセクションを対応節とする。`display` は `§{number} {titleShort}`。`titleShort` = toc の `title_en` を最初の「:」の手前で切った文字列(なければ全体)、20 文字超は 19 文字+「…」。例: 「2.2 Reflow: Straightening the Flow」→「§2.2 Reflow」。toc の `number` が null のセクション(アブストラクト等)が対応節の場合、`display` は「§」を付けず `titleShort` のみ(決定)。該当ブロックが 1 つも無いページ(表紙・参考文献等で bbox 欠落)では `null` → インジケータは「同期: —」、相互リンクボタン disabled。
2. **座標→ブロック(bbox 選択)**: クリック座標(canvas css px)を `viewport.convertToPdfPoint(x, y)` で pt に変換し、当該ページのブロックのうち bbox が点を含む最小面積のものを返す。`display` は `§{節番号} ¶{n}`(n=そのセクション内 `type==='paragraph'` ブロックの文書順序数、1 起点)。段落以外のブロック(図・数式・表等)がヒットした場合(決定): そのブロック自身を選択対象とし、`display` は ¶ 番号を付けず `§{節番号}` のみとする。
3. **位置保存用ブロック**: `firstBlockOnPage(page)` = そのページの bbox 上端(y1 最大。PDF 座標系は下原点)が最も上のブロック。

### 5.5 PDF 表示制御(決定)

- **ズーム**: `zoom` は PDF.js scale(scale=1 で 1pt=4/3 css px)。「+」「−」は ±0.1 刻み、範囲 0.25〜4.0 にクランプ。手動ズームで `fitMode=null`。表示は `round(zoom*100)%`。Ctrl/⌘+ホイールでも ±0.1(preventDefault)。デザインの「128%」は例示値。
- **フィット**: `fit-width` = `(中央列幅 − 166px) / ページ幅@scale1`(166px はデザイン実測の左右余白 83px×2)。`fit-page` = 高さも収まる方の倍率 `min(fitWidthScale, (キャンバス高 − 40px) / ページ高@scale1)`。`actual` = 1.0。フィット中はリサイズ(ResizeObserver)で再計算。既定は `fit-width`(デザイン準拠)。
- **ページ移動**: 「‹」「›」で ±1(見開き時 ±2)、範囲 1〜page_count にクランプ。入力欄は Enter で確定(数値以外・範囲外は元値に戻す)、blur でも確定。キーボード(決定: いずれも PdfPane が自前で登録する 2a 固有キー。shell の useViewerKeymap(viewer-shell §10)には追加しない。input フォーカス中は無効): `←/→` で前後ページ、`Home/End` で先頭/末尾。shell 経由の `j`/`k`(`requestScroll` 委譲)は PDF モードでは次/前ページ移動として解釈する(決定)。`Esc` は shell の優先順(viewer-shell §10)で浮遊 UI が何も開いていない場合のみ bbox 選択解除として PdfPane が処理する(決定)。
- **見開き**: トグルで 2 ページ表示。現在ページはペアの左(偶数)に正規化。
- **ページ変更の副作用**: URL `page` 更新(replace)→ サムネイル選択更新 → 同期インジケータ再計算 → `useViewerStore.setCurrentBlock(firstBlockOnPage(page), sectionId)` を呼ぶ(block 不在ページでは呼ばない)。実際の `PUT /api/library-items/{id}/position { revision_id, block_id, mode: "pdf" }` 送信(5 秒デバウンス+`pagehide` 時 sendBeacon)は shell の `useReadingPosition`(viewer-shell §8.1)が担う。

### 5.6 bbox 選択と相互リンク

- キャンバス上のクリック: `blockAtPoint` がヒット→ `selectBlock(blockId)`(ハイライト+チップ表示)。非ヒット領域クリック・`Esc`・ページ移動で `selectBlock(null)`。
- ホバー(決定): ヒット可能なブロック上では `cursor: pointer`。ホバー時のプリハイライトは描画しない(読書の邪魔をしない。P6)。
- チップ「≒ §2.2 ¶2 — 訳文で見る →」クリック: `onOpenInTranslation(selectedBlockId)` → `router.replace('/papers/{id}?mode=translation&block={blockId}')`。訳文モード側が該当段落へスクロール+2 秒の一時強調(訳文モード計画書の受け側仕様)。
- 「この位置を訳文で開く →」: 対象 = `selectedBlockId ?? firstBlockOnPage(currentPage)`。以降同上。

### 5.7 情報パネルのインタラクション

- ステータスピル: Popover(180px)で 6 値選択 → `PATCH /api/library-items/{id} { status }`(楽観更新)。例外: `done` 選択時は PATCH 前に読了フローモーダル(1g)を経由する(viewer-shell §4.2-4 の決定。モーダル実装は 1g 担当)。
- 「arXiv:2209.03003 ↗」「DOI ↗」: 新規タブ(`target="_blank" rel="noopener noreferrer"`)。
- 「再取り込み」: 確認 Modal(§4.3 文言、width 460px)→「再取り込み」で `POST /api/papers/{paper_id}/reingest` → タイムライン最下部に進行行を追加表示: ドットを var(--pr-a) の 7×7px+`animation: pulse 1.2s infinite`、テキスト「{stage 表示名} — {progress_pct}%」(stage 表示名対応。plans/03 §1.6 `PipelineState.stage` の全値: queued=待機中 / fetching=ソース取得中 / parsing=解析中 / structuring=構造化中 / translating_abstract・readable・translating_body=翻訳中 / waiting_quota=待機中(翻訳上限)/ その他未知値=処理中。決定)。SSE `done` で行を消して `viewer` invalidate+Toast。
- 「処理ログ」: `IngestLogModal`(Modal 共通、width 560px、labelledBy="ingest-log-title")。開いた時に `GET /api/papers/{paper_id}/ingest-log`。各行: flex、gap:10px、padding:7px 0、border-bottom:1px solid #F0EDE4、font-size:11px。左=時刻 `M/DD HH:mm:ss`(等幅 `--pr-font-mono`、色 #9A9EA4、幅 96px)、中=level バッジ(info=非表示 / warn=「warn」色 #C49432 / error=「error」色 #A05A42、font-size:9.5px、font-weight:700)、右=message(色 #3C4046、flex:1)。空なら EmptyState「ログはまだありません」。
- エクスポート 2 ボタン: `<a download>` ナビゲーション(§4.2.5)。ホバー(決定): 背景 #FAF9F5(`--pr-bg-hover`)。
- タブ切替: `SidePanelTabs.onChange` → `useViewerStore.setPanel(true, tab)`(viewer-shell §2.3・§6)。他タブの内容は各画面計画書(1a=チャット、1b=注釈、1c=図表、5a=リソース)に従う。

### 5.8 論文内検索(`/`)

- 検索 UI・キー操作・ドロップダウンの見た目は **viewer-shell §7(InPaperSearch)が正**: `/` でフォーカス、2 文字以上・300ms デバウンスで `GET /api/revisions/{revision_id}/search?q=&limit=50`、Popover width 300px・placement 'bottom-end'・caret なし、行=1 行目 `display`+2 行目 `snippet`(`.alinea-search-hit`)、`↓/↑/Enter/Esc`、0 件は「一致なし」。クエリキーも shell の `['in-paper-search', revisionId, query]` を使う(§2.2 参照)。
- 本書が確定するのは PDF モードでのヒット確定(`Enter`/行クリック)の挙動のみ(viewer-shell §7 末尾の規定を具体化): ヒットブロックが `page`+`bbox` を持てば該当ページへジャンプ+そのブロックを `selectBlock`(bbox ハイライト+チップ)。`bbox` 無しブロックは所属セクションの先頭ページへジャンプのみ(選択なし。viewer-shell §7 の「bbox 無しは節先頭ページ」)。

### 5.9 ホバー・フォーカス状態の網羅(決定)

| 要素 | ホバー | 備考 |
|---|---|---|
| ヘッダ「‹」「⋯」・ツールバー「‹」「›」「−」「+」 | 色を #3C4046 へ(transition 120ms) | |
| セグメント非選択 | 色 #1E2227 | 背景変化なし |
| サムネイル非選択 | border 色 var(--pr-am) | |
| 「⤓ 原文PDF」・「処理ログ」 | 色 var(--pr-a) | |
| チップ(arXiv/DOI)・エクスポートボタン・フィット/見開き/相互リンクボタン | 背景 #FAF9F5(アクセント面のものは var(--pr-as) の透過を 0.16 に濃く: `rgba(var(--pr-a-rgb),0.16)` は導入せず、`filter: brightness(0.97)` で代替。決定) | |
| フォーカス可視 | 全インタラクティブ要素 `outline:1.5px solid var(--pr-acc); outline-offset:1px`(plans/08 §5 共通) | |

- ボタンの active: `transform: translateY(0.5px)` は付けない(デザインに存在しない。決定)。
- 読書時間計測: 全モード共通機構(タブ前面+60 秒以内の操作を「アクティブ」とし、`POST /api/library-items/{id}/reading-sessions` を 60 秒ごとに upsert)。`reading.track_reading_time=false` なら送信しない。

### 5.10 状態遷移まとめ

```
[初期表示] viewer 取得 → mode 解決 → (pdf) pdfData+document 並行取得
  ├─ pdfData 解決 → ページレンダリング・サムネイル・サイズ表示
  ├─ document 解決 → syncMap 構築 → 同期インジケータ・bbox 有効化
  └─ 初期ページの優先順(決定): ① pendingScrollTarget(`?block=`/`?section=` 深リンク・モード間位置引き継ぎ。
     viewer-shell §3.2/§3.4。block の page を開き当該 block を selectBlock、section は先頭ページのみ)
     ② URL の `page` ③ last_position.mode==='pdf' の block_id が指す page ④ 1
[bbox 選択] click → selected(ハイライト+チップ) → チップ click → 訳文モードへ
                                   └ Esc / 余白 click / ページ移動 → 解除
[再取り込み] idle → 確認 → 202+SSE(進行行) → done(invalidate+Toast) / error(Toast)
```

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

シードデータ(Rectified Flow, arXiv:2209.03003。spec-decisions C10)+固定シード PDF を用い、Playwright スクリーンショット(1440×900、ライトテーマ、アクセント slate)で確定デザイン 2a と比較する。

- [ ] 3 ペイン寸法: 左 232px / 右 340px / 中央 flex、境界線 1px #E7E4DB、ヘッダ 52px 下線 #E6E3DA、ツールバー 38px
- [ ] ヘッダ要素の順序・寸法・文言が §4.2.1 と一致(タイトル max-width:330px ellipsis、品質バッジ 18×18px、ステータスピル h24px radius 999px、セグメント「PDF」選択中スタイル、スタイルセレクタ h26px、検索ボックス w150px bg #F1EFE9+Keycap「/」、「⋯」)
- [ ] 左サイドバー: 目次/ページセグメント(トラック #EBE8E0、h22px)、サムネイル 112×145px(通常 1px #DDD9CF / 選択 2px var(--pr-a)+shadow 0 4px 12px+番号バッジ 20×16px radius 8px)、フッタ「24 ページ · 4.1 MB」「⤓ 原文PDF」font 10.5px #9A9EA4
- [ ] ツールバー: ‹ 5 / 24 ›(入力欄 border #DDD9CF radius 4px 太字)、区切り線 1×16px #E2DFD5、− 128% +、「幅に合わせる ▾」h24px、「見開き」h24px #5B6067、「同期: p.5 ≒ §2.2 Reflow」(太字部 #3C4046)、「この位置を訳文で開く →」(var(--pr-as)/var(--pr-am)/var(--pr-a) 太字)
- [ ] キャンバス: 背景 #DDDAD1、padding-top 20px、ページ白面 shadow 0 6px 28px rgba(28,30,34,0.22)、幅フィット時 1440px でページ幅 700px(±1px)
- [ ] bbox ハイライト: 背景 var(--pr-as)+outline 1.5px var(--pr-a)+radius 2px、チップ top:-23px right:0 h20px bg var(--pr-a) 白字 10px/600 shadow 0 6px 16px、文言「≒ §2.2 ¶2 — 訳文で見る →」
- [ ] 情報パネル: タブ列(選択「情報」= inset 0 -2px var(--pr-a)、「注釈 6」「リソース 4」カウント 10px #9A9EA4)、見出し 10.5px/700 #9A9EA4 letter-spacing 0.4px、区切り線 1px #F0EDE4、書誌チップ h19px(venue 非リンク / arXiv・DOI アクセント太字)、品質バッジ 26×26px、タイムライン(緑ドット 7×7px+接続線 1.5px #E7E4DB、行文言逐語一致)、アクション「再取り込み」「処理ログ」、ライセンスカード(枠 rgba(101,148,113,0.4)/背景 0.10/見出し #4C7458)、エクスポートボタン h28px×2、フッタ注記 10px #9A9EA4
- [ ] ダークモード(data-theme="dark")でトークン置換により破綻しない(キャンバス背景は #DDDAD1 のダーク対応が未定義のため `--pr-bg-canvas`(#14171B)を使用。決定)
- [ ] アクセント 4 色切替でアクセント由来要素(選択サムネイル枠・バッジ・チップ・相互リンクボタン・タブ下線)が追随する

### 6.2 機能検証チェックリスト

- [ ] `/papers/{li_id}?mode=pdf&page=5` を開くと 5 ページ目が幅フィットで表示され、URL・サムネイル選択・ページ入力欄・同期インジケータが一致する
- [ ] 表示モードセグメントで 5 モードを切替でき、PDF→訳文→PDF の往復で `page` が保持される(`usePdfViewStore` 維持)
- [ ] ページナビ(‹ › / 直接入力 / ←→キー / サムネイルクリック)が範囲クランプ付きで動作し、5 秒デバウンスで `PUT /api/library-items/{id}/position`(mode:"pdf"、当該ページ先頭ブロック)が送信される
- [ ] ズーム ±(0.25〜4.0)・フィット 3 種・見開きトグルが動作し、ウィンドウリサイズでフィットが再計算される
- [ ] PDF 段落クリックで bbox ハイライト+「≒ §{節} ¶{n} — 訳文で見る →」チップが出現し、クリックで `?mode=translation&block={blk-…}` へ遷移して該当段落が強調される(docs/04 §15 の受け入れ基準)
- [ ] 「この位置を訳文で開く →」が現在ページ(選択中なら選択ブロック)から訳文モードの対応位置を開く
- [ ] 同期インジケータが現在ページの対応節を「同期: p.{n} ≒ §{num} {short}」形式で常時表示し、対応不能ページでは「同期: —」+相互リンク disabled になる
- [ ] `/` で論文内検索にフォーカスし、ヒット行クリックで該当ページ+bbox ハイライトへジャンプする(page 無しヒットは訳文モードへ)
- [ ] 「⤓ 原文PDF」「原文 PDF ⤓」が `GET /api/papers/{paper_id}/pdf` 経由で同一ファイルをダウンロードし、フッタに実ファイルサイズ(MB 小数 1 桁)と `page_count` が表示される
- [ ] ステータスピルで 6 値を変更でき(楽観更新+失敗ロールバック)、「読んでいる」のドットがアクセント色になる
- [ ] 「再取り込み」が確認モーダル→202→SSE 進行行→完了 Toast+viewer 再取得の順で動作し、実行中の再クリックは 409 Toast になる
- [ ] 「処理ログ」モーダルが `GET /api/papers/{paper_id}/ingest-log` の entries(時刻・level・message)を表示する
- [ ] 「注釈 Markdown ⤓」が `GET /api/library-items/{id}/export/annotations` の Markdown をダウンロードする
- [ ] arXiv/DOI チップが新規タブで正しい外部 URL を開く(rel="noopener noreferrer")
- [ ] タブ件数バッジが `viewer.counts` と一致し、6 タブが排他で切り替わる
- [ ] PDF アセットの無い論文で PDF セグメントが disabled になり、URL 直打ちは訳文モードへフォールバックする
- [ ] PDF 読み込み失敗時にエラー面+「再試行」が表示され、再試行で回復する(黙って壊れない。P3)
- [ ] 読書時間計測が 60 秒間隔で送信され、設定オフ時は送信されずフッタ文言が切り替わる
- [ ] キーボード: `/`(検索)、`←/→`(ページ)、`Esc`(選択解除/ポップ閉じ)、`Tab` 順序が DOM 順、全操作要素に focus-visible リングが出る
- [ ] axe による自動チェックで重大違反 0(ページ入力に `aria-label="ページ番号"`、セグメントは radiogroup、タブは `role="tablist"`)

## 付記: 本書で確定した主な決定一覧

1. ルートは `/papers/[itemId]`(viewer-shell §3.1)+`?mode=pdf&page={n}&block={blk-…}`(`page` は 2a 固有拡張、`router.replace` で同期)。
2. PDF 本体は全量 fetch(ArrayBuffer)→PDF.js。ファイルサイズ表示は byteLength から導出(MB 小数 1 桁)。
3. 同期マッピングは document 全量取得からクライアント側で構築: ページ→節は bbox 面積最大、節名短縮は「:」手前+20 字、座標→ブロックは包含最小面積、¶ 番号は節内 paragraph 順序数。
4. ズーム 0.25〜4.0・±0.1 刻み、フィット 3 種(幅/全体/実寸)、fit-width=(中央列幅−166px)/ページ幅、見開きは偶奇ペア gap 16px。
5. 再取り込みは確認モーダル+SSE 進行行+Toast。処理ログは width 560px モーダル。
6. PDF アセット無しは PDF セグメント disabled+訳文へフォールバック。ローディング/エラー/ホバー/フォーカスの各状態を §5.2・§5.3・§5.9 のとおり確定。
7. 品質 B の説明文・タイムスタンプ整形(M/DD HH:mm、同日以降は HH:mm)・ライセンス非許可系配色・フッタ注記のオフ時文言を確定。
