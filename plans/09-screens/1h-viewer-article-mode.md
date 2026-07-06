# 画面 1h: ビューア 記事モード+概要図

> 対象読者と前提: 本書は「訳読 / YAKUDOKU — 論文読解ワークベンチ」のビューア「記事」表示モード(確定デザイン 1h)を実装するフロントエンドエンジニア向けの完全仕様である。機能仕様の正は docs/04(ビューア)§6 と docs/07(概要図と記事モード)、ピクセル仕様の正は抽出ファイル extract/1h.md(本書 §4 に全量取り込み済み)。共通コンポーネント名は plans/08-design-system.md、API 名は plans/03-api.md、データは plans/02-data-model.md に従う。ビューア共通骨格(ヘッダ・目次レール/ペイン・サイドパネル枠・URL 契約・キーマップ・論文内検索・読書位置/時間フック・viewer-store)は plans/09-screens/viewer-shell.md が実装・仕様の正であり、本書での再掲はピクセル照合用の転記である(値が食い違う場合は viewer-shell.md を正とし本書を改訂する)。本書が所有するのは viewer-shell §11 の「1h」行 — `ArticlePane`(記事ブロック・概要図・ホバーツールバー・出典ブロック)と `ArticleRegenerateButton` — である。技術スタック: Next.js 15(App Router)+ React 19 + TypeScript 5 + Tailwind CSS v4 + TanStack Query v5 + Zustand。基準ビューポート 1440×900px(plans/08 §7)。

## 1. 概要とルート

- **ルートパス(確定)**: `/papers/[itemId]`(ファイル: `apps/web/src/app/(app)/papers/[itemId]/page.tsx`。viewer-shell §3.1・plans/03 §3.4 の `viewer_url: "/papers/li_…"` と同一)。※本書のコード例中に現れる `libraryItemId` 変数はルートパラメータ `itemId` と同値である(パラメータ名の正は `[itemId]`)。表示モードはクエリパラメータ `?mode=` で表現し、値域は API の `LastPosition["mode"]` と同一の `translation | parallel | source | pdf | article`。本画面は `?mode=article`。理由: 5 モードは同一データ文脈(同一 LibraryItem)の切替であり、モード切替でページ遷移(アンマウント)を起こさないため単一ルート+クエリとする。
- `mode` 省略時は `last_position.mode`(なければ `translation`)へ `router.replace` で正規化する(viewer-shell §3.2)。
- **認証: 必須**(CSR。plans/01 §2.1 のとおりルートレイアウトでセッション確認、未認証は `/login` へ)。
- **画面の役割**: 論文を訳文・メモ・チャット履歴から AI がブログ風に再構成した読み物として表示する(独立エディタではない。docs/07 §2.1)。冒頭に全体概要図(課題→提案→結果の 3 カードフロー、版管理・指示付き書き直し・SVG ダウンロード)。各ブロックにホバーツールバー(✦ 書き直し指示 / 再生成 / 根拠を表示)。出典・ライセンス表記は自動付記され削除不可。
- モード切替・トップバー・左レールはビューア共通シェル `ViewerShell`(plans/09-screens/viewer-shell.md。画面 1a/1b/1c/2a/5a と共用)に属し、本書は記事モード時の差分(「✦ 指示つき再生成」ボタン=`ArticleRegenerateButton`、レール既定=折畳 44px。viewer-shell §4.3・§5.1)を確定する。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 用途 | 取得タイミング |
|---|---|---|---|
| 1 | `GET /api/library-items/{id}/viewer`(§6.1) | トップバー(タイトル・品質 A バッジ・ステータス)、目次 `toc`、`revision.id`、ライセンスカード | ルートマウント時(全モード共通。モード切替で再取得しない) |
| 2 | `GET /api/library-items/{id}/article`(§19.1) | 記事本体(`Article`: タイトル・免責・`overview_figure`・`blocks`) | `mode=article` 進入時。404 = 未生成 → 生成 CTA(§5.2) |
| 3 | `POST /api/library-items/{id}/article`(§19.2) | 初回生成(プリセット選択) | 生成 CTA の「✦ 記事を生成」クリック |
| 4 | `POST /api/articles/{article_id}/regenerate`(§19.3) | ヘッダ「✦ 指示つき再生成」 | 指示ポップオーバーの送信 |
| 5 | `GET /api/articles/{article_id}/versions`(§19.4) | 記事の版一覧 | 版ポップオーバー開時 |
| 6 | `POST /api/articles/{article_id}/versions/{version}/restore`(§19.4) | 版の復元 | 版ポップオーバー「この版に戻す」 |
| 7 | `POST /api/articles/{article_id}/blocks/{block_id}/rewrite`(§19.5) | ブロック「✦ 書き直し指示」「再生成」 | ホバーツールバー操作 |
| 8 | `GET /api/articles/{article_id}/overview-figure`(§20.1) | 概要図の版一覧(`versions` 付き) | 概要図の版ポップオーバー開時(初期表示は Article 内 `overview_figure` で足りる) |
| 9 | `POST /api/articles/{article_id}/overview-figure/rewrite`(§20.1) | 概要図「✦ 書き直し指示」 | 同ポップオーバー送信 |
| 10 | `POST /api/articles/{article_id}/overview-figure/versions/{version}/restore`(§20.1) | 概要図の版復元 | 版ポップオーバー |
| 11 | `GET /api/overview-figures/{figure_id}/versions/{version}/svg?download=true`(§20.1) | 「SVG ⤓」ダウンロード | クリック時(`<a download>` で直接) |
| 12 | `POST /api/explainer-figures/{figure_id}/regenerate`(§20.2) | 解説図ブロックの再生成 | 解説図ブロックのツールバー |
| 13 | `GET /api/jobs/{job_id}` / `GET /api/jobs/{job_id}/events`(§21) | 生成・再生成・書き直しジョブの進捗 | 202 受領後 |
| 14 | `PATCH /api/library-items/{id}`(§5.4) | ステータスピル「読んだ ▾」の変更 | ドロップダウン選択時 |
| 15 | `PUT /api/library-items/{id}/position`(§5.8) | 読書位置の自動保存(`mode:"article"`)※呼び出しはシェル所有 `useReadingPosition`(viewer-shell §8.1) | `currentBlockId` 変化の 5,000ms デバウンス+`pagehide` 時 sendBeacon |
| 16 | `GET /api/revisions/{revision_id}/search`(§6.7) | 論文内検索(`/`)※シェル所有 `InPaperSearch`(viewer-shell §7) | 2 文字以上・300ms デバウンス |
| 17 | `GET /api/revisions/{revision_id}/blocks/{block_id}`(§6.4) | 「根拠を表示」の原文プレビュー | 根拠ポップオーバー開時 |
| 18 | `POST /api/library-items/{id}/reading-sessions`(§5.9) | 読書時間計測ハートビート※シェル所有 `useReadingSession`(viewer-shell §8.2) | 60,000ms ごと+visibilitychange(hidden)+pagehide(全モード共通) |

- 決定: `mode=article` の位置保存(#15)では `block_id` に最上部可視 `ArticleBlock` の id(`ablk_…`)を送る。plans/03 §5.8 の `block_id: string` は mode=article のとき `ablk_` ID と解釈する。実装上は `ArticlePane` が IntersectionObserver で先頭可視 `ablk_` を検出し `viewer-store.setCurrentBlock()` を呼ぶ(送信自体はシェルの `useReadingPosition`。viewer-shell §5.4・§8.1 の契約)。再訪時(`last_position.mode === 'article'` かつ `block_id` が `ablk_` で現行記事に存在する場合)は前回位置バナーを出さず該当ブロック先頭へ即時スクロールする。存在しない(記事再生成で ID が変わった)場合は記事先頭から表示する(決定)。

### 2.2 TanStack Query キー設計(確定)

```ts
// apps/web/src/components/viewer/article/queries.ts(1h 所有分。viewer-shell §1.2 の配置規約に従い components/viewer/article/ 配下 — 決定)
// キー文字列は viewer-shell §2.2 と同一(['viewer', liId] / ['in-paper-search', revId, q] はシェル所有。ここは参照用の再掲)
export const viewerKeys = {
  viewer:   (liId: string) => ['viewer', liId] as const,               // #1  staleTime 30_000ms
  article:  (liId: string) => ['article', liId] as const,              // #2  staleTime Infinity(ジョブ完了 invalidate でのみ更新)
  articleVersions: (articleId: string) => ['article-versions', articleId] as const,   // #5 staleTime 0
  overviewFigure:  (articleId: string) => ['overview-figure', articleId] as const,    // #8 staleTime 0
  inPaperSearch: (revId: string, q: string) => ['in-paper-search', revId, q] as const, // #16 staleTime 30_000ms・enabled: q.length >= 2(viewer-shell §2.2 と同一キー・同一値)
  blockPreview:  (revId: string, blockId: string) => ['block-preview', revId, blockId] as const, // #17 staleTime Infinity(リビジョン不変)
};
```

- `#2` の 404 は `retry: false` とし、`error.status === 404` を「未生成」状態として扱う(例外にしない)。
- ミューテーション成功時の無効化: 記事再生成/版復元 → `article` + `articleVersions`。ブロック書き直し完了 → `setQueryData(article)` で該当ブロックのみ差替(§2.3)。概要図書き直し/復元 → `article` + `overviewFigure`。ステータス変更 → `viewer` + `['library']` プレフィックス一括(1e §2 の無効化規則と同一。list と facets の両方が落ちる。楽観更新: ピルは即時反映、失敗時ロールバック+Toast)。

### 2.3 リアルタイム更新(SSE)

- 生成系 202 `{ job_id }` 受領後、`GET /api/jobs/{job_id}/events` に `EventSource` で接続する共通フック `useJobEvents(jobId, { onProgress, onDone, onError })`(ファイル: `apps/web/src/hooks/useJobEvents.ts` — 決定。`packages/api-client` の `sseFetch()` は使わず素の EventSource。クッキー認証のみで足りるため)。
- `event: progress` → 進捗 UI 更新(`progress_pct`)。`event: done` → 種別ごとの invalidate/差替。`event: error` → Toast(§5.9)。
- ブロック書き直し(kind=`article_block_rewrite`)の `done` は `result.block: ArticleBlock` を含むため、`queryClient.setQueryData(viewerKeys.article(liId), draft => blocks 内の同 id を差替)` とし記事全体は再取得しない(plans/03 §19.5)。
- ポーリングフォールバック(決定。plans/01 §5 の「3 回連続失敗で切替」パターンをジョブ単位 SSE に適用): EventSource が 3 回連続で接続失敗した場合、`GET /api/jobs/{job_id}` を `refetchInterval: 2000ms` でポーリングし、ジョブ終端(succeeded / failed)で停止する。ポーリング中に EventSource 再接続は試みない(単発ジョブのため)。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`共通` = plans/08 §5・§6 の共通コンポーネント。`◆` = viewer-shell 所有(実装・仕様の正は plans/09-screens/viewer-shell.md — 本書の該当記述はピクセル照合用転記)。`固有` = 本書(1h)所有。

```
PapersPage (app/(app)/papers/[libraryItemId]/page.tsx) ◆
└─ ViewerShell ◆(ビューア6画面共通シェル。1a/1b/1c/2a/5a と共用)
   ├─ ViewerHeader ◆(viewer-shell §4)
   │  ├─ 戻る「‹」(テキストグリフ)◆
   │  ├─ 論文タイトル(1行省略)◆
   │  ├─ QualityBadge 共通(A, 18px)
   │  ├─ StatusPill 共通(interactive, ドロップダウン=Popover 共通 width 180)
   │  ├─ SegmentedControl 共通(訳文|対訳|原文|PDF|記事, size='md')
   │  ├─ ArticleRegenerateButton 固有(mode=article のみこのスロットに表示。他モードはスタイルセレクタ — viewer-shell §4.3。ファイル: components/viewer/article/ArticleRegenerateButton.tsx)
   │  │  └─ RegeneratePopover 固有(Popover 共通 width 360)
   │  ├─ InPaperSearch ◆(SearchBox 共通 variant='in-paper' + 結果 Popover width 300。viewer-shell §7)
   │  └─ ViewerOverflowMenu ◆(⋯。Popover width 200、項目は viewer-shell §4.2-9)
   ├─ TocRail ◆(w44 折畳レール: ☰ / BookmarkIcon 共通 / MagnifierIcon 共通。viewer-shell §5.2)
   │  └─ TocPane ◆(☰ で展開する w232 論文目次。viewer-shell §5.3。しおりアイコンは TocPane を bookmarkFilter=true で開く — 専用ポップオーバーは存在しない)
   └─ ArticlePane 固有(mode=article の中央領域。viewer-shell §11 の「1h」行)
      ├─ ArticleGenerateCTA 固有(未生成 404 時。EmptyState 共通 + プリセット選択)
      ├─ ArticleSkeleton 固有(ローディング)
      ├─ ArticleRegenBanner 固有(再生成ジョブ進行中の進捗行)
      └─ ArticleBody 固有(760px カラム)
         ├─ ArticleTitle 固有(h1 27px)
         ├─ ArticleMetaRow 固有(AIBadge 共通 'generated' + 免責文)
         ├─ OverviewFigureBlock 固有
         │  ├─ OverviewFigureSvg 固有(DSL→SVG は API 配信の svg_url を <img> 表示)
         │  ├─ FigureVersionPopover 固有(Popover 共通 width 220)
         │  ├─ RewriteInstructionPopover 固有(Popover 共通 width 320。ブロックと共用)
         │  └─ EvidenceChip 共通 ×N(フッタ「根拠:」)
         └─ ArticleBlockList 固有 — blocks.map(b => ArticleBlockItem)
            ├─ ArticleBlockItem 固有(position:relative ラッパ+ホバー検知)
            │  ├─ BlockHoverToolbar 固有(ダーク浮動バー)
            │  │  ├─ RewriteInstructionPopover 固有
            │  │  └─ EvidencePopover 固有(Popover 共通 width 320)
            │  └─ type 別レンダラ:
            │     ├─ HeadingBlock 固有(19px 見出し)
            │     ├─ ParagraphBlock 固有(Markdown→HTML+KaTeX。EvidenceChip 共通 inline)
            │     ├─ QuoteSourceBlock 固有(原文引用。EvidenceChip 共通 + 原文で見る →)
            │     ├─ FigureEmbedBlock 固有(転載図+出典+ライセンスバッジ)
            │     ├─ FigureLinkCardBlock 固有(転載不可時の代替リンクカード)
            │     ├─ ExplainerFigureBlock 固有(解説図。AIBadge 共通)
            │     ├─ DiscussionBlock 固有(議論したい点)
            │     └─ AttributionBlock 固有(出典。locked)
            └─ ArticleSelectionMenu 固有(SelectionMenu 共通の縮退版。§5.8)
```

### 3.2 画面固有コンポーネントの props 型

```ts
// apps/web/src/components/viewer/article/types.ts
import type { Article, ArticleBlock, OverviewFigureRef, Preset, ReadingStatus } from '@yakudoku/api-client';

interface ArticlePaneProps {
  libraryItemId: string;
  revisionId: string;              // viewer クエリの revision.id(根拠ジャンプ・検索に使用)
}

interface ArticleBodyProps {
  article: Article;
  revisionId: string;
  onJumpToAnchor: (anchor: AnchorRef) => void;  // mode=source へ遷移+スクロール(§5.6)
}

interface ArticleBlockItemProps {
  block: ArticleBlock;
  articleId: string;
  rewriting: boolean;              // 書き直しジョブ進行中(§5.5)
  onRewrite: (blockId: string, instruction?: string) => void;
  onShowEvidence: (blockId: string) => void;
  onJumpToAnchor: (anchor: AnchorRef) => void;
}

interface BlockHoverToolbarProps {
  visible: boolean;
  locked: boolean;                 // attribution: true → ツールバー自体を出さない
  onRewriteClick: () => void;      // RewriteInstructionPopover を開く
  onRegenerate: () => void;        // instruction なしで onRewrite
  onShowEvidence: () => void;
  hasEvidence: boolean;            // false なら「根拠を表示」を非表示
}

interface RewriteInstructionPopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement>;
  target: 'article' | 'block' | 'overview_figure';
  placeholder: string;             // block: '例: もっと平易に / 式を使って'
  onSubmit: (instruction: string) => void;
  pending: boolean;
}

interface RegeneratePopoverProps {   // ヘッダ「✦ 指示つき再生成」
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement>;
  currentPreset: Preset;
  currentIncludeMath: boolean;
  onSubmit: (req: { instruction?: string; preset?: Preset; include_math?: boolean }) => void;
  pending: boolean;
}

interface OverviewFigureBlockProps {
  figure: OverviewFigureRef;
  articleId: string;
  rewriting: boolean;
  onJumpToAnchor: (anchor: AnchorRef) => void;
}

interface FigureVersionPopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement>;
  versions: { version: number; generated_at: string }[];
  currentVersion: number;
  onRestore: (version: number) => void;
}

interface EvidencePopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement>;
  revisionId: string;
  evidence: { ref: number; display: string; anchor: AnchorRef }[];
  onJumpToAnchor: (anchor: AnchorRef) => void;
}

interface ArticleGenerateCTAProps {
  libraryItemId: string;
  onGenerated: () => void;         // job done → article invalidate
}

interface ArticleRegenBannerProps {
  progressPct: number;             // 「✦ 記事を再生成しています… 42%」
  kind: 'generate' | 'regenerate'; // 版復元(§19.4 restore)は同期 200 応答のためバナー不要(invalidate のみ — 決定)
}
```

- Zustand ストア: シェル所有 `viewer-store`(完全形は viewer-shell §2.3。本書で再定義しない)。記事モードで使うフィールドは `tocOpen`(記事モード既定 false=44px レール。viewer-shell §5.1)/ `panelOpen`・`activeTab`(記事モード初回既定 `panelOpen=false`=閉。viewer-shell §6.3。開閉可能、パネル仕様は viewer-shell §6 と各タブ担当画面に委譲)/ `currentBlockId`・`pendingScrollTarget`(§5.6)。表示モードは URL `?mode=` のみが正でストアに持たない(viewer-shell §2.3)。
- 記事モード固有のローカル状態(useState): ホバー中ブロック id、開いているポップオーバー種別、進行中ジョブ(`{ scope: 'article'|'overview'|blockId, jobId }` の Map)。

## 4. レイアウト・スタイル完全仕様

出典: extract/1h.md(逐語)。色はカッコ内に plans/08 のトークン名を併記する(実装はトークン参照で書く。hex 直書きは ESLint で禁止 — plans/08 §4.3)。

### 4.0 デザイナー注記(フレーム上部。UI の一部ではない — 実装しない)

- バッジ「1h」/ タイトル「論文ビューア — 記事モード」/ 説明「表示モードの1つとして論文をブログ風の読み物に再構成 / ブロックにホバーで書き直し指示 / 出典・ライセンス表記は自動」。これらはデザインキャンバス上の注記であり実アプリには描画しない。

### 4.1 レイアウト構造

フレーム外枠: 1440×900px、背景 #F4F3EF(--pr-bg-app-alt)、border 1px solid #D6D3C9、border-radius 10px、box-shadow 0 20px 44px rgba(28,30,34,0.12)、overflow:hidden、縦 flex、文字色 #1E2227(--pr-text)。※フレームの枠・影・radius はデザインキャンバス表現であり、実アプリではビューポート全面描画(plans/08 §7.1)。

```
┌────────────────────────────────────────────────────────── 1440×900 ─┐
│ トップバー h52 #FFFFFF border-bottom 1px #E6E3DA                     │
│ [‹][論文タイトル][A][●読んだ▾] …flex1… [訳文|対訳|原文|PDF|記事]     │
│                          [✦指示つき再生成][🔍この論文内を検索 /][⋯]  │
├──────┬───────────────────────────────────────────────────────────────┤
│ 折畳 │ 記事レンダリング領域(flex:1、背景 #FBFAF7、中央寄せ)         │
│ 目次 │  ┌── 本文カラム width:760px、padding:34px 0 0、縦flex gap16 ─┐│
│ レール│  │ 記事タイトル(27px 太字)                                 ││
│ w44  │  │ メタ行(AI生成バッジ + 説明 + 日付)                       ││
│ 背景 │  │ 概要図ブロック(3カード + → + →、ヘッダ/フッタ付き)        ││
│#F7F6F2│ │ 見出し「なぜ「直線」なのか」+ ホバーツールバー(黒)+ 段落 ││
│右境界│  │ 原文引用ブロック(左3pxアクセント線)                      ││
│1px   │  │ 図表埋め込みブロック(プレースホルダ画像 + キャプション)   ││
│#E7E4DB│ │ 「議論したい点」番号付きリスト×3                          ││
│      │  │ 出典ブロック(自動挿入 · 削除不可)                        ││
│      │  └──────────────────────────────────────────────────────────┘│
└──────┴───────────────────────────────────────────────────────────────┘
```

- トップバー: height:52px、flex:none、背景 #FFFFFF(--pr-bg-card)、border-bottom:1px solid #E6E3DA(--pr-border-header)、display:flex、align-items:center、gap:10px、padding:0 16px。
- 本体: flex:1、display:flex、min-height:0。
- 左レール: width:44px、flex:none、背景 #F7F6F2(--pr-bg-pane)、border-right:1px solid #E7E4DB(--pr-border-pane)、縦 flex、align-items:center、padding:12px 0、gap:14px。
- 記事領域: flex:1、min-width:0、display:flex、justify-content:center、背景 #FBFAF7(--pr-bg-app)。決定: 実装では overflow-y:auto(デザインは静止フレームのため overflow:hidden。実アプリは縦スクロール必須)。
- 本文カラム: width:760px、padding:34px 0 0、縦 flex、gap:16px。決定: カラム末尾に padding-bottom:64px を足す(最終ブロックがビューポート下端に張り付かないため。デザイン未描画の補完)。
- 1440px 超・未満のフルード規則は plans/08 §7.2 に従う(760px カラムは中央寄せ維持、記事領域の背景面が左右に伸縮。最小アプリ幅 1200px)。

### 4.2 トップバー(h52、白)

左から順に:

1. 戻り矢印「‹」: font-size:16px、color:#8A8E94(--pr-text-icon)、width:20px、text-align:center。
2. 論文タイトル: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」— font-size:13px、font-weight:600、max-width:330px、white-space:nowrap、overflow:hidden、text-overflow:ellipsis。データ: `viewer.library_item.paper.title`。
3. 「A」バッジ = QualityBadge(size 18): 18×18px、border-radius:4px、背景 var(--pr-as)、文字色 var(--pr-a)、font-size:10.5px、font-weight:700、中央寄せ。`title="品質レベルA: LaTeXソースから完全構造化"`。
4. ステータスピル「読んだ ▾」= StatusPill(md, interactive): inline-flex、gap:5px、height:24px、padding:0 9px、border:1px solid #DDD9CF(--pr-border-control)、border-radius:999px、font-size:11.5px、font-weight:500、背景 #FFFFFF(--pr-bg-control)。内部に緑ドット(7×7px、border-radius:50%、背景 #659471=--pr-status-done)+テキスト「読んだ」+「▾」(color:#9A9EA4=--pr-text-muted、font-size:9px)。
5. スペーサー(flex:1)。
6. 表示モード切替 = SegmentedControl(md): トラック背景 #EFEDE6(--pr-bg-muted)、border-radius:7px、padding:2px、gap:2px。タブ 5 個、各 height:24px、padding:0 11px、border-radius:5px、font-size:11.5px。
   - 非選択「訳文」「対訳」「原文」「PDF」: color:#5B6067(--pr-text-sub)、背景なし。
   - 選択中「記事」: 背景 #FFFFFF(--pr-bg-seg-selected)、color:#1E2227(--pr-text)、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)(--pr-shadow-seg)。
7. ボタン「✦ 指示つき再生成」(ArticleRegenerateButton): inline-flex、gap:5px、height:26px、padding:0 10px、border:1px solid #DDD9CF(--pr-border-control)、border-radius:6px、font-size:11.5px、color:var(--pr-a)、font-weight:600。「✦」= AiMark。※記事モードのみ。他モードではこの位置に「スタイル: 自然訳 ▾」(docs/04 §2)。
8. 検索フィールド = SearchBox(variant='in-paper'): inline-flex、gap:6px、height:26px、padding:0 10px、背景 #F1EFE9(--pr-bg-inset)、border-radius:6px、font-size:11.5px、color:#8A8E94(--pr-text-icon)、width:150px。
   - MagnifierIcon 11×11(viewBox 0 0 12 12: 中心(5,5) 半径 3.6 の円 stroke currentColor 1.3 + (8,8)→(10.6,10.6) の柄線 stroke-linecap:round)。
   - プレースホルダ「この論文内を検索」。
   - 右端 Keycap「/」: margin-left:auto、border:1px solid #DAD7CD(--pr-border-keycap)、border-radius:3px、padding:0 4px、font-size:9.5px、背景 #FFFFFF(--pr-bg-control)。
9. オーバーフローメニュー「⋯」: font-size:15px、color:#5B6067(--pr-text-sub)、letter-spacing:1px。

### 4.3 折畳目次レール(w44)

縦に 3 アイコン(gap:14px、上から):

1. ハンバーガー「☰」: font-size:13px、color:#5B6067(--pr-text-sub)。
2. BookmarkIcon 10×12: color:#9A9EA4(--pr-text-muted)。形状 = 下端が V 字に切れ込む栞(path `M1 1h8v10L5 8.5 1 11V1z`、fill currentColor)。
3. MagnifierIcon 12×12: color:#9A9EA4(--pr-text-muted)(4.2-8 と同形状)。

### 4.4 記事タイトル(ArticleTitle)

「Rectified Flow を読む: 生成経路を「直線」にして 1 ステップ生成へ」— font-size:27px、font-weight:700、line-height:1.5、letter-spacing:-0.2px。フォントは UI 標準 = IBM Plex Sans JP(--pr-font-ui)。データ: `article.title`。

### 4.5 メタ行(ArticleMetaRow)

display:flex、align-items:center、gap:8px、font-size:11px、color:#9A9EA4(--pr-text-muted)。

- バッジ「AI生成」= AIBadge(variant='generated'): inline-flex、height:15px、padding:0 5px、border:1px solid #DDD9CF(--pr-border-control)、border-radius:3px、font-size:9px、color:#8A8E94(--pr-text-icon)、font-weight:600。
- テキスト: 「訳文・メモ・チャット履歴から自動構成 · 2026-07-06 · 元の論文とは別物です — 根拠チップから原文へ」。データ: `article.disclaimer`(サーバーが日付込みで逐語生成。クライアントで組み立てない)。

### 4.6 概要図ブロック(OverviewFigureBlock)

外枠: border:1px solid #E2DFD5(--pr-border-card)、border-radius:10px、背景 #FFFFFF(--pr-bg-card)、overflow:hidden。

ヘッダ行: display:flex、align-items:center、gap:8px、padding:8px 12px、border-bottom:1px solid #F0EDE4(--pr-border-hair)。

- 「✦ 全体概要図」: font-size:10.5px、font-weight:700、color:var(--pr-a)。
- バッジ「AI生成 · 版 2」: height:15px、padding:0 5px、border:1px solid #DDD9CF(--pr-border-control)、border-radius:3px、font-size:9px、color:#8A8E94(--pr-text-icon)、font-weight:600。テキストは `AI生成 · 版 ${figure.version}`。クリックで版ポップオーバー(§5.4)。
- スペーサー(flex:1)。
- 「✦ 書き直し指示」: font-size:10.5px、color:var(--pr-a)、font-weight:600。クリックで RewriteInstructionPopover。
- 「SVG ⤓」: font-size:10.5px、color:#5B6067(--pr-text-sub)。

図本体: padding:18px 20px、display:flex、align-items:stretch、gap:0。3 カード+矢印 2 つ。※本体は API 配信 SVG(`figure.svg_url`)を `<img>` で表示する(docs/07 §1.2: SVG 決定的レンダリング。SVG 内部が下記スタイルを 100% 再現する — レンダラ実装は plans/04-llm-providers)。ラスター生成モード時(`raster_url != null`)は raster を表示。以下は SVG が満たすべき描画仕様:

1. カード「課題」(role='problem', tone='neutral'): flex:1、縦 flex gap:7px、border:1px solid #E2DFD5(--pr-border-card)、border-top:3px solid #B0ACA2(--pr-status-on-hold と同値)、border-radius:8px、padding:12px 14px、背景 #FBFAF7(--pr-bg-app)。
   - ラベル「課題」: font-size:9.5px、font-weight:700、color:#8A8E94(--pr-text-icon)、letter-spacing:0.6px。
   - 見出し「拡散モデルの生成経路は曲がっている」: font-size:12px、font-weight:600、line-height:1.6。
   - 本文「SDE/ODE の数値解法に数十〜数百ステップが必要で、生成が遅い」: font-size:10.5px、color:#5B6067(--pr-text-sub)、line-height:1.65。
2. 矢印「→」: display:flex、align-items:center、padding:0 8px、color:#B0B4BA(--pr-text-thumb)、font-size:16px。
3. カード「提案 — RECTIFIED FLOW」(role='proposal', tone='accent'。強調・中央、やや幅広): flex:1.2、border:1px solid var(--pr-am)、border-top:3px solid var(--pr-a)、border-radius:8px、padding:12px 14px、背景 #FFFFFF(--pr-bg-card)。
   - ラベル「提案 — RECTIFIED FLOW」: font-size:9.5px、font-weight:700、color:var(--pr-a)、letter-spacing:0.6px。
   - 見出し「直線補間の向きを回帰し、ODE を学習」: font-size:12px、font-weight:600、line-height:1.6。
   - 本文「v ← (X₁−X₀) の最小二乗回帰(式5)。reflow の反復で経路を再直線化」: font-size:10.5px、color:#5B6067、line-height:1.65。
4. 矢印「→」(2. と同一)。
5. カード「結果」(role='result', tone='green'): flex:1、border:1px solid #E2DFD5(--pr-border-card)、border-top:3px solid #659471(--pr-green)、border-radius:8px、padding:12px 14px、背景 #FBFAF7(--pr-bg-app)。
   - ラベル「結果」: font-size:9.5px、font-weight:700、color:#4C7458(--pr-src-note-fg と同値)、letter-spacing:0.6px。
   - 見出し「1 ステップ生成で FID 4.85」: font-size:12px、font-weight:600、line-height:1.6。
   - 本文「CIFAR-10。蒸留と併用で従来の拡散 ODE 蒸留を上回る」: font-size:10.5px、color:#5B6067、line-height:1.65。

フッタ行: display:flex、align-items:center、gap:8px、padding:7px 12px、border-top:1px solid #F0EDE4(--pr-border-hair)、背景 #FBFAF7(--pr-bg-app)。

- 「✦ AI 生成 · 訳読 · 2026-07-06」: font-size:9.5px、color:#9A9EA4(--pr-text-muted)。データ: `figure.dsl.footer`。
- スペーサー(flex:1)。
- 「根拠:」: font-size:9.5px、color:#9A9EA4。
- 根拠チップ×3(「§1」「§2.2」「表1」)= EvidenceChip の概要図フッタ変種: inline-flex、height:15px、padding:0 6px、border:1px solid var(--pr-am)、color:var(--pr-a)、border-radius:3px、font-size:9px、font-weight:600。決定: EvidenceChip に `size='figure-footer'`(h15px/9px/radius 3px、bg なし)を追加する(plans/08 §5.18 の inline/header と寸法が異なる 1h 実測値のため)。データ: `figure.evidence[]`。

### 4.7 見出し+段落ブロック(HeadingBlock + ParagraphBlock、ホバーツールバー付き)

コンテナ(ArticleBlockItem): position:relative。

- 見出し「なぜ「直線」なのか」: font-size:19px、font-weight:700、margin-bottom:8px。(`heading.level: 2`。決定: level 3 は font-size:15.5px、font-weight:700、margin-bottom:6px — デザイン未描画のため 19px と本文 14.5px の中間で確定)
- ホバーツールバー(BlockHoverToolbar。ブロック右上に重なる): position:absolute、top:-14px、right:0、display:flex、align-items:center、gap:2px、背景 #26292E(--pr-elev-bg)、border-radius:7px、padding:4px 6px、box-shadow:0 8px 22px rgba(20,22,26,0.30)。項目 3 つ、各 font-size:10.5px、color:#E8E6E1(--pr-elev-fg)、padding:0 6px:
  1. 「✦ 書き直し指示」
  2. 「再生成」
  3. 「根拠を表示」
- 段落本文: font-family:var(--pr-jp,'Noto Serif JP'),serif、font-size:14.5px、line-height:2.0、color:#24272B(--pr-text-body)。テキスト:「直線の経路は 1 回のオイラーステップで厳密にシミュレートできる。つまり「経路をどれだけ直線に近づけられるか」が、そのまま推論コストの削減につながる。Rectified Flow はこの一点に賭けた設計である。」データ: `block.content.markdown`(Markdown → HTML)。決定: 許容 Markdown サブセットは強調(`**`/`*`)・インラインコード・リスト・リンク(`target="_blank" rel="noopener"`)のみとし、生 HTML はエスケープする(サニタイズ必須)。数式は `include_math=true` の場合のみ `$…$` / `$$…$$` を KaTeX でレンダリングし、`include_math=false` の記事では数式記法をレンダリングしない(サーバーが数式を含めない保証 — 万一含まれてもプレーンテキスト表示)。
- 段落末尾の根拠チップ(`block.evidence`)= EvidenceChip(size='inline'、h16px)。デザイン 1h では段落内チップは非描画だが、docs/07 §2.4「AI 生成ブロックには根拠チップが付く」に従い evidence が非空なら段落末尾にインライン表示する(決定)。

### 4.8 原文引用ブロック(QuoteSourceBlock)

border-left:3px solid var(--pr-a)、背景 #FFFFFF(--pr-bg-card)、border-radius:0 8px 8px 0、padding:12px 16px、縦 flex gap:6px。

- 引用文(英語): font-family:'Source Serif 4',Georgia,serif(--pr-font-en)、font-style:italic、font-size:13px、line-height:1.75、color:#33373C(--pr-text-en)。テキスト: `"Straight paths are computationally attractive: they can be simulated exactly with a single Euler step."` データ: `block.content.quote.text_en`(前後のダブルクォート含め表示はデータ値のまま)。
- メタ行: display:flex、align-items:center、gap:7px、font-size:10px、color:#9A9EA4(--pr-text-muted)。
  - テキスト「原文引用」。
  - チップ「§2.2 ¶3」: inline-flex、height:15px、padding:0 6px、border:1px solid var(--pr-am)、color:var(--pr-a)、border-radius:3px、font-weight:600(EvidenceChip size='figure-footer' と同寸)。データ: `quote.anchor.display`。
  - リンク「原文で見る →」: color:var(--pr-a)、font-weight:600。
```ts
interface QuoteSourceBlockProps {
  quote: { text_en: string; anchor: AnchorRef };
  onJumpToAnchor: (anchor: AnchorRef) => void;
}
```

### 4.9 図表埋め込みブロック(FigureEmbedBlock)

外枠: border:1px solid #E2DFD5(--pr-border-card)、border-radius:10px、背景 #FFFFFF(--pr-bg-card)、overflow:hidden。

- 画像: margin:14px 16px 0、border-radius:6px、border:1px solid #E0DDD3(--pr-border-thumb)。実データは `figure.image_url` の `<img>`(width 100%、height auto)。読込中・失敗時プレースホルダ: height:150px、背景 #EFEDE6(--pr-bg-thumb)、中央寄せ、color:#B0B4BA(--pr-text-thumb)、font-size:11px、テキスト「図2(原論文の画像)」(`figure_display` を埋め込む。デザインのプレースホルダ表現をそのまま採用 — 決定)。
- キャプション領域: padding:10px 16px 12px、縦 flex gap:5px。
  - キャプション: font-family:var(--pr-jp,'Noto Serif JP'),serif、font-size:12px、line-height:1.75、color:#24272B(--pr-text-body)。テキスト「図2: reflow の反復で軌道が直線化していく様子。交差する経路が「平均の向き」に置き換わるため、軌道は交わらない。」データ: `figure.caption_ja`。
  - 出典行: display:flex、align-items:center、gap:7px、font-size:9.5px、color:#9A9EA4(--pr-text-muted)。
    - 「出典: Liu et al., *Flow Straight and Fast* (arXiv:2209.03003) · 」(書名はイタリック `<i>`)。データ: `figure.credit`(サーバー自動付記。イタリック範囲は credit 文字列内の `*…*` を `<i>` に変換 — 決定)。
    - ライセンスバッジ「CC BY 4.0 — 転載可」: inline-flex、height:15px、padding:0 6px、border-radius:3px、背景 rgba(101,148,113,0.16)(--pr-src-note-bg)、color:#4C7458(--pr-src-note-fg)、font-weight:700。データ: `figure.license_badge`。
    - 「クレジット自動付記」(固定文言)。
- 転載不可時(`content.figure_link_card`)= FigureLinkCardBlock(決定。デザイン未描画): 同外枠(border-card/radius 10px/bg card)、padding:14px 16px、縦 flex gap:6px。1 行目 `figure_link_card.message`(font-size:12px、color:#5B6067、line-height:1.7)、2 行目リンク「原文で{figure_display}を見る →」(font-size:11px、color:var(--pr-a)、font-weight:600。クリックで mode=source の該当図へジャンプ)。
- 解説図(`content.explainer`)= ExplainerFigureBlock(決定。1h には非描画、docs/07 §2.3 の構成ブロック): FigureEmbedBlock と同外枠・同画像マージン。キャプション領域の出典行を「AIBadge 'generated'(「AI生成」)+ キャプション」に置き換える(ライセンスバッジ・クレジットなし)。

### 4.10 議論したい点(DiscussionBlock)

縦 flex gap:8px。

- 見出し行: display:flex、align-items:center、gap:8px。「議論したい点」(font-size:19px、font-weight:700)+ バッジ「✦ AI構成」(inline-flex、height:16px、padding:0 7px、border-radius:3px、背景 #F1EFE9(--pr-bg-inset)、color:#777B81(--pr-text-sub2)、font-size:9.5px、font-weight:600)。
- 番号付き項目×3: 各 display:flex、gap:9px、font-family:var(--pr-jp,'Noto Serif JP'),serif、font-size:13.5px、line-height:1.9、color:#24272B(--pr-text-body)。番号(「1.」「2.」「3.」)は color:#9A9EA4(--pr-text-muted)。
  1. 「reflow を重ねると周辺分布の誤差は蓄積しないか(§2.2 の仮定の妥当性)」+ 由来バッジ「あなたの疑問ハイライトから」(inline-flex、height:15px、padding:0 6px、border-radius:3px、背景 rgba(88,132,170,0.16)(--pr-ann-question の 0.16 面。専用値)、color:#4A6E8E、font-size:9.5px、font-weight:600、font-family:'IBM Plex Sans JP',sans-serif(--pr-font-ui)、vertical-align:2px、margin-left:4px)。`origin === 'user_highlight'` の項目のみ表示。
  2. 「Flow Matching(Lipman+ 2023)との理論的な差分はどこか」
  3. 「蒸留を reflow 後に行う場合、教師の品質劣化はどう影響するか」
- データ: `block.content.discussion.items[]`(`{ text, origin: 'ai' | 'user_highlight' }`)。

### 4.11 出典ブロック(AttributionBlock)

border:1px solid #E2DFD5(--pr-border-card)、border-radius:8px、背景 #F1EFE9(--pr-bg-inset)、padding:10px 14px、display:flex、align-items:center、gap:10px。

- 出典テキスト: font-size:10.5px、line-height:1.7、color:#5B6067(--pr-text-sub)、flex:1。テキスト「出典: Xingchao Liu, Chengyue Gong, Qiang Liu. "Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow." ICLR 2023. arXiv:2209.03003 · ライセンス CC BY 4.0」。データ: `block.content.attribution.text`。
- バッジ「自動挿入 · 削除不可」: inline-flex、gap:4px、height:18px、padding:0 8px、border-radius:4px、背景 #E4E1D7、color:#8A8E94(--pr-text-icon)、font-size:9.5px、font-weight:600。※背景 #E4E1D7 = `--pr-bg-locked-badge`(ダーク #2C313A)— plans/08 §2 に定義済み。
- `block.locked === true`: ホバーツールバーを一切表示しない(§5.5)。

### 4.12 全 UI 文言(逐語リスト)

トップバー: `‹` / `Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow` / `A` / `読んだ` / `▾` / `訳文` / `対訳` / `原文` / `PDF` / `記事` / `✦ 指示つき再生成` / `この論文内を検索` / `/` / `⋯`

左レール: `☰`(+ブックマークアイコン、虫眼鏡アイコン)

本文: `Rectified Flow を読む: 生成経路を「直線」にして 1 ステップ生成へ` / `AI生成` / `訳文・メモ・チャット履歴から自動構成 · 2026-07-06 · 元の論文とは別物です — 根拠チップから原文へ` / `✦ 全体概要図` / `AI生成 · 版 2` / `✦ 書き直し指示` / `SVG ⤓` / `課題` / `拡散モデルの生成経路は曲がっている` / `SDE/ODE の数値解法に数十〜数百ステップが必要で、生成が遅い` / `→` / `提案 — RECTIFIED FLOW` / `直線補間の向きを回帰し、ODE を学習` / `v ← (X₁−X₀) の最小二乗回帰(式5)。reflow の反復で経路を再直線化` / `→` / `結果` / `1 ステップ生成で FID 4.85` / `CIFAR-10。蒸留と併用で従来の拡散 ODE 蒸留を上回る` / `✦ AI 生成 · 訳読 · 2026-07-06` / `根拠:` / `§1` / `§2.2` / `表1` / `なぜ「直線」なのか` / `✦ 書き直し指示` / `再生成` / `根拠を表示` / `直線の経路は 1 回のオイラーステップで厳密にシミュレートできる。つまり「経路をどれだけ直線に近づけられるか」が、そのまま推論コストの削減につながる。Rectified Flow はこの一点に賭けた設計である。` / `"Straight paths are computationally attractive: they can be simulated exactly with a single Euler step."` / `原文引用` / `§2.2 ¶3` / `原文で見る →` / `図2(原論文の画像)` / `図2: reflow の反復で軌道が直線化していく様子。交差する経路が「平均の向き」に置き換わるため、軌道は交わらない。` / `出典: Liu et al., Flow Straight and Fast (arXiv:2209.03003) · ` / `CC BY 4.0 — 転載可` / `クレジット自動付記` / `議論したい点` / `✦ AI構成` / `1.` / `reflow を重ねると周辺分布の誤差は蓄積しないか(§2.2 の仮定の妥当性)` / `あなたの疑問ハイライトから` / `2.` / `Flow Matching(Lipman+ 2023)との理論的な差分はどこか` / `3.` / `蒸留を reflow 後に行う場合、教師の品質劣化はどう影響するか` / `出典: Xingchao Liu, Chengyue Gong, Qiang Liu. "Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow." ICLR 2023. arXiv:2209.03003 · ライセンス CC BY 4.0` / `自動挿入 · 削除不可`

※上記のうち論文固有の値(タイトル・本文・出典等)はシードデータ(Rectified Flow, arXiv:2209.03003 — spec-decisions C10)での期待値であり、固定文言は「原文引用」「原文で見る →」「クレジット自動付記」「根拠:」「✦ 全体概要図」「✦ 書き直し指示」「SVG ⤓」「✦ AI構成」「あなたの疑問ハイライトから」「自動挿入 · 削除不可」「✦ 指示つき再生成」「この論文内を検索」の 12 個+モードタブ 5 個。

### 4.13 データフィールド対応表

| 表示 | データソース |
|---|---|
| 論文タイトル(英語) | `viewer.library_item.paper.title` |
| 品質グレード「A」 | `viewer.revision.quality_level`(QualityBadge) |
| 読書ステータス「読んだ」(緑 #659471) | `viewer.library_item.status`(StatusPill) |
| 記事タイトル(AI 生成日本語) | `article.title` |
| 生成日・生成ソース種別・免責 | `article.disclaimer`(逐語) |
| 概要図: 版・生成日・生成主体・カード(ラベル/見出し/本文/tone)・根拠 | `article.overview_figure`(`version` / `dsl.footer` / `dsl.cards[]` / `evidence[]`) |
| 記事ブロック(見出し・段落・引用・図・議論・出典) | `article.blocks[]`(`ArticleBlock.type` + `content`) |
| 原文引用の位置チップ・リンク | `content.quote.anchor.display` / `anchor` |
| 図の出典・ライセンス・転載可否 | `content.figure.credit` / `license_badge`(不可時 `figure_link_card`) |
| 議論項目の由来 | `content.discussion.items[].origin` |
| 出典ブロックのロック | `block.locked === true` |

## 5. 状態とインタラクション

### 5.1 ローディング(決定 — デザイン未描画)

- `viewer` 未取得: トップバーはタイトル位置に 330×13px・ピル位置に 64×24px のスケルトン(背景 #EFEDE6=--pr-bg-muted、border-radius:4px/999px、pulse 1200ms)。レール・背景は即描画。
- `article` 取得中(ArticleSkeleton): 760px カラム内に上から (1) 27px 相当バー w520×27px、(2) メタ行バー w420×11px、(3) 概要図枠(実寸の外枠+高さ 180px の無地面)、(4) 段落バー w760×14px ×5 本(gap 14px)。すべて背景 #EFEDE6、border-radius:6px、pulse 1200ms。スピナーは使わない。

### 5.2 未生成(404)と初回生成

- `GET …/article` が 404 の場合、760px カラム中央(margin-top:120px)に生成 CTA を表示する。決定(デザイン未描画。コンテナは縦 flex・align-items:center・gap:14px、以下の順):
  - EmptyState(共通): タイトル「この論文の記事はまだありません」/ 説明「訳文・メモ・チャット履歴から、AI がブログ風の読み物を構成します。」
  - プリセット選択 = SegmentedControl(size='lg'、4 値): 「初学者向け」(`beginner`)/「実装者向け」(`implementer`)/「研究者向け」(`researcher`)/「輪読会向け」(`reading_group`)。既定 `beginner`。
  - Toggle(共通)+ラベル「数式を含める」(font-size:11.5px、color:--pr-text-mid)。初期値はプリセット属性(beginner=OFF、implementer=ON、researcher=ON、reading_group=OFF — plans/03 §19.2 の既定と同一。docs/07 §2.6)。プリセット切替のたびにトグルを属性値へリセットし、ユーザーがトグルを触った後は切替でも上書きしない(決定)。
  - 生成ボタン「✦ 記事を生成」: height:30px、padding:0 16px、background:var(--pr-a)、color:#FFFFFF、border-radius:7px、font-size:12px、font-weight:600。
  - クリック → `POST /api/library-items/{id}/article` → 202 → ジョブ SSE。CTA が進捗表示に置き換わる: 「✦ 記事を生成しています… {progress_pct}%」(font-size:12px、color:var(--pr-a))+ ProgressBar(共通、width 260px、color='accent')。`done` → `invalidateQueries(article)`。
- 読了フロー(1g)「記事モードで読み返す →」から未生成で遷移した場合もこの CTA が初回生成のトリガー(docs/07 §2.7)。

### 5.3 記事全体の指示つき再生成

- 「✦ 指示つき再生成」クリック → RegeneratePopover(Popover 共通、width 360、placement='bottom-end'、アンカー=ボタン)。内容(決定 — デザイン未描画):
  - 見出し「✦ 指示つき再生成」(font-size:12px、font-weight:700、padding:12px 14px 0)。
  - textarea: width 100%(padding 内)、height 72px、border 1px --pr-border-control、border-radius:6px、font-size:11.5px、placeholder「例: 実験の部分を削って手法を厚く」。
  - プリセット SegmentedControl(sm、4 値、現在値選択)+「数式を含める」Toggle。
  - フッタ右寄せ: ボタン「✦ 再生成」(h26px、bg var(--pr-a)、白、radius 6px、font 11.5px/600)。instruction 空でも送信可(プリセット変更のみの再生成)。
- 送信 → `POST /api/articles/{article_id}/regenerate` → 202。ポップオーバーを閉じ、メタ行直下に ArticleRegenBanner を表示: 「✦ 記事を再生成しています… {pct}%」(font-size:11px、color:var(--pr-a)、height:24px)。本文は現行版を表示し続ける(決定: ブロッキングしない)。
- `done` → `invalidateQueries(article)` + Toast(success)「✓ 記事を再生成しました(版 {n})」。進行中はヘッダボタンを opacity:0.5・pointer-events:none。
- 429 の扱い(決定): `code === 'quota_exceeded'`(月間クォータ超過。plans/03 §17.4)→ Toast(error)「今月の生成クォータを使い切りました」。`code === 'rate_limited'`(生成系 30 回/分。plans/03 §1.8)→ Toast(error)「操作が多すぎます。しばらく待って再試行してください」。いずれも本文は現行版のまま。

### 5.4 概要図の操作

- 「✦ 書き直し指示」→ RewriteInstructionPopover(width 320、textarea h56px、placeholder「例: 実験の部分を削って手法を厚く」、送信ボタン「✦ 書き直す」)→ `POST …/overview-figure/rewrite` → 202 → 進行中は図本体に半透明オーバーレイ(background:rgba(251,250,247,0.7))+中央「✦ 書き直し中… {pct}%」(font-size:11px、color:var(--pr-a))。`done` → `invalidateQueries(article)`(version+1 が反映)。
- バッジ「AI生成 · 版 N」クリック → FigureVersionPopover(width 220): `GET …/overview-figure` の `versions[]` を新しい順に行表示(「版 2 · 2026-07-06」font 11px。現在版は weight 600+「現在」ラベル)。各行ホバーで「この版に戻す」リンク(font 10.5px、color var(--pr-a))→ `POST …/versions/{v}/restore` → invalidate。決定(docs/07 §1.2「前の版に戻せる」の UI 未描画分の確定)。
- 「SVG ⤓」→ `<a href={svg_url + '?download=true'} download>`。追加確認なし。エクスポート SVG にも AI 生成フッタが含まれる(サーバー保証、docs/07 §1.2)。
- 根拠チップ(§1 / §2.2 / 表1)クリック → §5.6 のアンカージャンプ。

### 5.5 ブロックホバーツールバーと書き直し

- ホバー(mouseenter)から 80ms 遅延で表示、mouseleave+ポップオーバー非表示で即時非表示(決定。ちらつき防止)。同時に 1 ブロックのみ。キーボード: ブロックラッパは `tabIndex=0` とし、focus-visible でも表示(a11y 補完 — 決定)。
- 対象: heading / paragraph / quote_source / figure_embed / explainer_figure / discussion。`locked: true`(attribution)はツールバー非表示(「操作対象外」の明示はバッジ「自動挿入 · 削除不可」自体が担う)。
- 「✦ 書き直し指示」→ RewriteInstructionPopover(ツールバー直下、placement='bottom-end')→ `POST …/blocks/{block_id}/rewrite { instruction }`。
- 「再生成」→ 即時 `POST …/blocks/{block_id}/rewrite {}`(instruction なし。確認ダイアログなし — 決定: 版は変わらず何度でも実行できるため)。
- 例外(決定): `explainer_figure` ブロックのみ「✦ 書き直し指示」「再生成」は `POST /api/explainer-figures/{figure_id}/regenerate { instruction? }`(§2.1 #12。kind=explainer_figure)を使う。この `done` は `result.block` を持たないため `invalidateQueries(article)` で反映する(blocks/rewrite の部分差替は適用しない)。
- 進行中(rewriting): ブロックを opacity:0.55、ツールバーを「✦ 書き直し中…」単一表示(操作不能)。`done` → `result.block` で該当ブロックのみ差替(§2.3)+ 差替ブロックに 1200ms の背景ハイライト(background:var(--pr-as) からフェードアウト — 決定)。`error` → 元表示に戻し Toast(error)「× ブロックの書き直しに失敗しました」+ 再試行はユーザー操作。
- 「根拠を表示」→ EvidencePopover(width 320、placement='bottom-end'): `block.evidence[]` を行表示。各行 = EvidenceChip(display)+ 原文プレビュー(`GET /api/revisions/{revision_id}/blocks/{block_id}` の `block` 冒頭 120 字、font-size:11px、color:--pr-text-sub、line-height:1.7、2 行省略)+「原文で見る →」。evidence が空のブロック(データ都合)では「根拠を表示」項目自体を非表示(決定)。

### 5.6 根拠チップ・「原文で見る →」のアンカージャンプ(決定)

- 遷移: `viewer-store.requestScroll({ kind: 'block', blockId: anchor.block_id })` を積んでから `router.replace('/papers/{libraryItemId}?mode=source', { scroll: false })`(viewer-shell §3.3/§3.4 の機構。docs/07「根拠チップから原文へ」— 原文モード)。anchor.type が figure/table の場合も同様に該当ブロックへ。ハッシュフラグメントは使わない(URL 契約は viewer-shell §3.1 の `?block=` / store 経由のみ)。
- 遷移後、原文ペインが `pendingScrollTarget` を消費して対象ブロックへ `scrollIntoView({ block: 'center' })`+背景 `var(--pr-selection)` を 1600ms フェードアウト(原文側画面の共通ジャンプ挙動。`onJumpToAnchor` は本書所有のこのラッパ関数)。

### 5.7 トップバー共通インタラクション

※本節はすべてシェル所有機能の転記+記事モード差分の確定である。実装・仕様の正は viewer-shell §4・§5・§7。

- 「‹」: `router.back()`。履歴が無い(`window.history.length <= 1`。新規タブ・深リンク直開き)場合は `router.push('/library')`(viewer-shell §4.2-1)。
- StatusPill「読んだ ▾」: クリックで 6 値ドロップダウン(Popover width 180、placement bottom-start。各行 = 色ドット+ラベル、h30px、padding 0 12px、font 11.5px、ホバー bg --pr-bg-hover、現在値 bg var(--pr-acc-s)+weight 600)。選択 → `PATCH /api/library-items/{id} { status }`(楽観更新+失敗ロールバック+Toast)。`done` 選択時は PATCH 前に読了フローモーダル(1g)を経由する(viewer-shell §4.2-4。モーダル実装は 1g 担当)。
- モードセグメント: 選択で `?mode=` を `router.replace`。記事未生成でも「記事」タブは常時有効(遷移先で CTA 表示)。
- 検索: シェル所有 `InPaperSearch`(viewer-shell §7)をそのまま使う(「/」キーまたはクリックでフォーカス、2 文字以上・300ms デバウンス、結果 Popover width 300・placement bottom-end・caret なし、`limit=50`、0 件時「一致なし」、`↓`/`↑`/`Enter`/`Esc` 操作)。記事モード差分(viewer-shell §7 の決定): ヒット先は原文位置のため、行選択で `?mode=translation` の該当ブロックへ遷移する(§5.6 の根拠ジャンプ(mode=source)とは別動作。記事本文自体は検索対象外)。
- 「⋯」: シェル所有 `ViewerOverflowMenu`(Popover width 200。項目 5 つは viewer-shell §4.2-9 で確定。1h 固有の追加項目なし — 決定)。
- 左レール: 「☰」→ 目次ペイン TocPane(w232px。viewer-shell §5.3)をレール位置に展開(レールと排他)。記事モードでの節クリック(決定 — 記事は独自目次を持たない): `requestScroll({ kind: 'section', sectionId })` を積んで `?mode=translation` へ `router.replace`。BookmarkIcon → TocPane を `bookmarkFilter=true` で展開(非該当行 opacity 0.45。viewer-shell §5.2 — 専用ポップオーバーは存在しない)。MagnifierIcon → 検索フィールドへフォーカス(「/」と同じ)。

### 5.8 テキスト選択(決定)

- 記事本文の選択で SelectionMenu(共通)を表示するが、記事モード変種は **「✦ AIに質問」「コピー」の 2 項目のみ**(注釈色ドット・「コメント」・「語彙に追加」は非表示)。理由: 注釈・語彙のアンカー(`Anchor.block_id` + `side`)は原文/訳文ブロック前提であり、AI 生成テキストには張れない(plans/03 §1.7)。「✦ AIに質問」はサイドパネルのチャットタブを開き、選択文を引用としてコンポーザに挿入する(挙動は画面 1a 計画と共通)。

### 5.9 エラー(決定 — デザイン未描画)

- `viewer` / `article` の 5xx・ネットワークエラー: 760px カラム中央に EmptyState(タイトル「読み込みに失敗しました」/ 説明 = Problem.title / アクション「再試行」= refetch)。黙って壊れない(P3)。
- 生成ジョブ `event: error`: Toast(error)「× {Problem.title}」+ `retryable: true` なら CTA/バナーに「再試行」ボタンを残す。
- SVG 画像(`svg_url`)の読み込み失敗: 図本体領域に高さ 180px のプレースホルダ「概要図を読み込めませんでした · 再読み込み」(font 11px、--pr-text-muted、リンク部 var(--pr-a))。「再読み込み」クリックで `<img>` の `src` にキャッシュバスター(`?r={Date.now()}`)を付けて再読込する(決定)。

### 5.10 状態一覧(デザイン描画済み+補完)

| 状態 | 出所 |
|---|---|
| 「記事」タブ選択中(白背景・太字・薄影)、他 4 タブ非選択 | 1h 描画 |
| ステータスピル「読んだ」(緑ドット #659471)+▾ | 1h 描画 |
| 「なぜ「直線」なのか」ブロックにホバーツールバー表示(ダーク #26292E、top:-14px) | 1h 描画(ホバー状態) |
| 概要図「版 2」 | 1h 描画(版管理) |
| 議論項目に由来バッジ混在(user_highlight / ai) | 1h 描画 |
| 出典ブロック「自動挿入 · 削除不可」ロック | 1h 描画 |
| ローディング(スケルトン)/ 未生成 CTA / 生成・再生成進行中 / 書き直し進行中 / エラー / 検索 0 件 | 本書 §5.1〜5.9 で決定 |
| ダークモード | トークン自動追随(plans/08 §8。1h 専用分岐なし。--pr-bg-locked-badge も plans/08 §2 に定義済み — §4.11) |

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

シードデータ(Rectified Flow, arXiv:2209.03003)+記事フィクスチャ(§4 の全文言)で 1440×900 スクリーンショットを取り、確定デザイン 1h とピクセル比較する(Playwright VRT。plans/08 §9 と同一基盤)。

- [ ] トップバー: h52 / 白 / border-bottom #E6E3DA、要素順序・寸法(タイトル max-width 330px 省略、A バッジ 18×18、ピル h24、セグメント 5 タブ h24、再生成ボタン h26、検索 w150 h26、⋯)が §4.2 と一致
- [ ] 「記事」タブのみ選択スタイル(白背景・#1E2227・600・shadow-seg)
- [ ] 左レール w44 / #F7F6F2 / border-right #E7E4DB、アイコン 3 つが gap14 で縦並び
- [ ] 本文カラム w760・padding-top 34px・ブロック間 gap16、中央寄せ、背景 #FBFAF7
- [ ] 記事タイトル 27px/700/lh1.5/ls-0.2px(IBM Plex Sans JP)
- [ ] メタ行: AI生成バッジ(h15/9px/枠 #DDD9CF)+免責文言逐語(11px #9A9EA4)
- [ ] 概要図: 外枠 radius10、ヘッダ行(✦全体概要図 10.5px 700 アクセント / 版バッジ / ✦書き直し指示 / SVG ⤓)、3 カード(flex 1:1.2:1、border-top 3px = #B0ACA2 / var(--pr-a) / #659471、ラベル 9.5px ls0.6、見出し 12px、本文 10.5px)、矢印 → 16px #B0B4BA、フッタ行(9.5px+根拠チップ h15 9px)がデザインと一致
- [ ] 段落: Noto Serif JP 14.5px / line-height 2.0 / #24272B
- [ ] ホバーツールバー: top:-14px right:0、bg #26292E、radius7、padding 4px 6px、shadow 0 8px 22px rgba(20,22,26,0.30)、項目 3 つ 10.5px #E8E6E1
- [ ] 原文引用: 左 3px var(--pr-a)、radius 0 8 8 0、Source Serif 4 italic 13px/1.75 #33373C、メタ行(原文引用 / §2.2 ¶3 チップ / 原文で見る →)
- [ ] 図表埋め込み: 画像マージン 14 16 0、キャプション 12px Noto Serif JP、出典行 9.5px(イタリック書名)、ライセンスバッジ(bg rgba(101,148,113,0.16) / #4C7458 / 700)、「クレジット自動付記」
- [ ] 議論したい点: 見出し 19px + ✦AI構成バッジ(h16 bg #F1EFE9)、項目 13.5px/1.9 Noto Serif JP、番号 #9A9EA4、由来バッジ(bg rgba(88,132,170,0.16) / #4A6E8E / IBM Plex Sans JP / vertical-align 2px)
- [ ] 出典ブロック: bg #F1EFE9 / radius8 / 10.5px/1.7、バッジ「自動挿入 · 削除不可」(h18 bg #E4E1D7)
- [ ] §4.12 の固定文言 17 個が逐語一致(1 字も違わない)
- [ ] ダークモード・アクセント 4 色でトークン追随(ハードコード hex ゼロ。ESLint ルール通過)

### 6.2 機能検証

- [ ] `/papers/{id}?mode=article` で本画面が開き、セグメントで 5 モードをワンクリック往復できる(ページ全体の再マウントなし)
- [ ] 記事未生成(404)で生成 CTA が出て、プリセット 4 種+「数式を含める」を選んで `POST …/article` → SSE 進捗 → 完了で記事が表示される
- [ ] 「✦ 指示つき再生成」で instruction/preset/include_math を送って version+1 の記事に更新され、進行中も現行版が読める
- [ ] 各ブロック(出典を除く)ホバーで「✦ 書き直し指示 / 再生成 / 根拠を表示」が使え、書き直し完了で該当ブロックのみ差し替わる(記事全体の再取得が発生しない — ネットワークアサーション)
- [ ] 出典ブロック(locked)にツールバーが出ず、API 直叩きでも 403(サーバー側)
- [ ] 概要図: 版バッジに現在版が表示され、書き直しで版が増え、版ポップオーバーから前の版に戻せる。「SVG ⤓」で `Content-Disposition: attachment` の SVG が落ち、AI 生成フッタを含む
- [ ] 根拠チップ(§1 / §2.2 / 表1 / §2.2 ¶3)・「原文で見る →」で `mode=source` の該当ブロックへジャンプし 1600ms ハイライトされる
- [ ] 「根拠を表示」ポップオーバーに evidence 一覧+原文プレビューが出る
- [ ] ライセンス転載不可の論文フィクスチャで図埋め込みが FigureLinkCardBlock に置き換わる。転載可ではクレジット+ライセンスバッジが自動付記される
- [ ] 議論したい点で `origin='user_highlight'` の項目にのみ由来バッジが付く
- [ ] ステータスピルで 6 値を変更でき(楽観更新+失敗ロールバック)、「/」で検索フォーカス → 入力で論文内ヒットが出て該当位置へ飛べる
- [ ] スクロールで 5 秒デバウンスの位置保存(`mode:"article"`)が飛び、再訪時に前回位置から表示される
- [ ] 生成系エラー(SSE `event: error`・429)が Toast で通知され、黙って壊れない(P3)
- [ ] テキスト選択で「✦ AIに質問 / コピー」の 2 項目メニューが出て、AIに質問がチャットタブを開く
- [ ] 読了フロー(1g)「記事モードで読み返す →」から本画面へ遷移できる
- [ ] キーボード: ブロック focus でツールバー表示、Popover は Esc/外側クリックで閉じる、セグメントは矢印キー移動(plans/08 §5.1)
