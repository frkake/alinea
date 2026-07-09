# 画面 1c: ビューア ダーク+図表参照ポップオーバー+図表/参考文献パネル

> 対象読者と前提: 本書は「Alinea — 論文読解ワークベンチ」のフロントエンド実装者向けに、確定デザイン画面 1c(論文ビューア ダークモード。図表参照ポップオーバー+サイドパネル図表/参考文献タブ)を 100% 忠実に実装するための計画書である。機能仕様は docs/04(ビューア)§7・§10.2・§14 を正とし、ピクセル値は抽出ファイル extract/1c.md の実測値を正とする。共通コンポーネント名・トークン名は plans/08、API 名は plans/03、データ型は plans/02 に完全準拠する。技術スタック: Next.js 15(App Router)+ React 19 + TypeScript 5 + Tailwind CSS v4 + TanStack Query v5 + Zustand。1c は独立画面ではなく、ビューア画面(1a と同一ルート)の「ダークテーマ+対訳モード+図表タブ+図参照ポップオーバー表示」という状態の組み合わせである。本書はその状態群と、1c で初出の要素(ダークトークン適用・図参照ポップオーバー・図表/参考文献パネル)の実装を確定させる。ヘッダ・目次・サイドパネル枠・URL 契約・読書位置/時間はビューアシェル計画書(plans/09-screens/viewer-shell.md)が正であり、対訳ビュー(ParallelView 系)は画面 1a の計画書(plans/09-screens/1a-viewer-parallel-chat.md)と共有する。本書では 1c 実測値との対応と 1c 固有部分(図参照ポップオーバー・図表タブ)を定義する。シェルと 1a 計画書が矛盾する場合は viewer-shell.md が正(決定)。

## 1. 概要とルート

- **ルートパス(確定)**: `/papers/[itemId]`。ファイルは `apps/web/src/app/(app)/papers/[itemId]/page.tsx`(viewer-shell §1.2・§3.1 と同一。パラメータ名は `itemId` に統一)。plans/03 §3.4 の `viewer_url: "/papers/li_…"` と一致させる。パスパラメータは `li_` プレフィックスの LibraryItem ID。
- **1c の状態を再現する URL**: ダークモードは URL ではなく `<html data-theme="dark">`(plans/08 §8)で表現する。URL 契約は viewer-shell §3 が正: `?mode=` のみ URL に持続(`mode ∈ translation|parallel|source|pdf|article`。欠落・不正値は `last_position.mode`、それも無ければ `translation` へ `router.replace` で正規化)。`?panel=`(`chat|notes|annotations|figures|resources|info`)は**初期化時に 1 回消費して URL から除去する一時クエリ**であり、以後のタブ状態は `sessionStorage`(viewer-shell §2.3・§6.3)で復元する。1c の再現エントリ URL は `?mode=parallel&panel=figures`。ポップオーバー開閉・参考文献展開は URL にも sessionStorage にも載せない(一時 UI 状態)。
- **認証**: 必須(session)。ルートグループ `(app)` のレイアウトが `GET /api/auth/me`(plans/03 §2.6)で未認証を検出したら `/login` へリダイレクト(plans/01 §2.1)。
- **画面の役割**: 読書中の参照(図表・引用)を「スクロールせずその場で」解決する(docs/04 §7)。(1) 本文中の図表参照クリックでポップオーバー表示、(2) サイドパネル図表タブで図表一覧と参考文献を提示、(3) 参考文献からのワンクリック取り込み(芋づるライブラリ拡張)、(4) ダークモードの全面適用の 4 点が 1c の検証対象。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 用途 | 取得タイミング |
|---|---|---|---|
| 1 | `GET /api/library-items/{id}/viewer`(§6.1) | 初期化複合: 書誌・目次・翻訳進捗 96%・タブ件数(注釈 6/リソース 4)・今日の読書 42分 | ページマウント時(初回描画のブロッカー) |
| 2 | `GET /api/revisions/{revision_id}/document?section_id={sec}`(§6.3) | 本文ブロック(原文・インライン参照含む) | 表示セクション+前後 1 セクションを先読み |
| 3 | `GET /api/revisions/{revision_id}/translations/{style}/units?section_id={sec}`(§7.2) | 訳文ユニット | #2 と同時(並列) |
| 4 | `GET /api/revisions/{revision_id}/figures`(§6.5) | 図表タブ「図表一覧」+ポップオーバーの図データ | ビューア初期化直後にプリフェッチ(タブを開く前。ポップオーバーが本文クリックで即必要になるため) |
| 5 | `GET /api/revisions/{revision_id}/references`(§6.6) | 図表タブ「参考文献」+ `in_library` 判定 | 図表タブ初回表示時(`enabled: activeTab === 'figures'`。viewer-store の値)。引用リンククリック時は強制フェッチ |
| 6 | `POST /api/ingest/arxiv`(§3.2) | 「+ この論文も取り込む」 | ボタンクリック時 |
| 7 | `PATCH /api/library-items/{id}`(§5.4) | ステータスピル「読んでいる ▾」の変更 | ドロップダウン選択時 |
| 8 | `PUT /api/library-items/{id}/position`(§5.8) | 読書位置の自動保存(フッタ「位置は自動保存」) | 先頭可視ブロック(`currentBlockId`)変化から 5,000ms デバウンス+`pagehide` 時 sendBeacon(viewer-shell §8.1) |
| 9 | `POST /api/library-items/{id}/reading-sessions`(§5.9) | 読書時間計測(「今日の読書 42分」の元) | 60,000ms 間隔+`visibilitychange`(hidden)/`pagehide` 時(viewer-shell §8.2) |
| 10 | `GET /api/revisions/{revision_id}/search?q=`(§6.7) | 論文内検索(`/`) | 検索実行時 |
| 11 | `GET /api/jobs/{job_id}/events`(§21.2) | 翻訳進捗のリアルタイム更新(96% → 100%) | `viewer.translation.status !== 'complete'` の間のみ SSE 購読。`job_id` は `GET /api/library-items/{id}/jobs?active=true` で解決(1a 計画書 §2.3 と同一) |
| 12 | `GET /api/assets/{asset_id}`(§22.1) | 図画像・サムネイル(`image_url` の実体。302 署名 URL) | `<img src>` として遅延ロード |
| 13 | `GET /api/library-items/{id}/chat/threads`(§10.1) | 「✦ この図を説明」のメインスレッド解決 | ボタンクリック時(キャッシュ済みなら再利用) |
| 14 | `POST /api/chat/threads/{thread_id}/messages`(§10.3) | 「✦ この図を説明」送信(SSE) | ボタンクリック時 |

### 2.2 TanStack Query キー設計(確定)

`apps/web/src/features/viewer/queryKeys.ts`(1a 計画書 §2.2 と同一ファイル。別ファイルは作らない — 決定)に集約する。キーは配列リテラルで、第 1 要素がドメイン名。命名はケバブケースで統一し、`viewer` / `in-paper-search` は viewer-shell §2.2 の確定キーと同一形(シェル所有。1c が新規に定義するのは `figures` / `references` のみで、`document` / `translation-units` / `chat-threads` は 1a 計画書と共有 — 1a 側のキャメルケース表記はケバブケースに読み替える。決定)。

```ts
export const viewerKeys = {
  viewer: (liId: string) => ['viewer', liId] as const,
  document: (revId: string, sectionId: string) => ['document', revId, sectionId] as const,
  units: (revId: string, style: 'natural' | 'literal', sectionId: string) =>
    ['translation-units', revId, style, sectionId] as const,
  figures: (revId: string) => ['figures', revId] as const,
  references: (revId: string) => ['references', revId] as const,
  inPaperSearch: (revId: string, q: string) => ['in-paper-search', revId, q] as const,
  chatThreads: (liId: string) => ['chat-threads', liId] as const,
};
```

- **staleTime(確定)**: `document` / `figures` / `references` はリビジョン不変データのため `staleTime: Infinity` + HTTP ETag(§6.3)。`viewer` は `staleTime: 30_000`。`translation-units` は `status === 'complete'` なら `Infinity`、それ以外は `30_000`。
- **無効化規則**: (a) 翻訳ジョブ SSE の `done` で `['viewer', liId]` と `['translation-units', …]` を invalidate。(b) 「+ この論文も取り込む」のジョブ完了で `['references', revId]` を invalidate(→ `in_library` が付き「ライブラリに有り ✓」へ遷移)。(c) ステータス変更(#7)は `onSuccess` で `['viewer', liId]` のキャッシュを直接書き換え(setQueryData)。

### 2.3 リアルタイム更新

- **SSE は翻訳ジョブ進捗のみ**(#11)。`event: progress` の `progress_pct` を `setQueryData(['viewer', liId])` で目次ヘッダの「翻訳 96%」に反映する。ポーリングは行わない。
- 図表・参考文献・注釈件数は静的(リビジョン不変)。ポーリング不要。
- 「+ この論文も取り込む」実行後: レスポンスの `job_id` で `GET /api/jobs/{job_id}/events` を購読し、`done` 受信で `['references', revId]` を invalidate する(決定。§5.6 の状態遷移参照)。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`(共通)` = plans/08 §5・§6 のコンポーネント。`(シェル)` = ビューアシェル(viewer-shell.md で定義、`apps/web/src/components/viewer/` 配下)。`(1a共有)` = 対訳ビュー(画面 1a 計画書で定義)。`(1c固有)` = 本書で定義 — 図表タブ本体は viewer-shell §6.5 の契約どおり `apps/web/src/components/viewer/panel/FiguresTab.tsx`、その下位コンポーネントとポップオーバー系は `apps/web/src/components/viewer/panel/figures/` 配下(決定)。

```
page.tsx  /papers/[itemId]
└─ ViewerShell                         (シェル) 3ペインシェル
   ├─ ViewerHeader                     (シェル §4) h52px ヘッダ
   │  ├─ ‹ 戻るグリフ                  (共通 §6.2 テキストグリフ)
   │  ├─ QualityBadge                  (共通 §5.3) 「A」18×18
   │  ├─ StatusPill                    (共通 §5.2) 「読んでいる ▾」interactive
   │  ├─ SegmentedControl              (共通 §5.1) 訳文|対訳|原文|PDF|記事
   │  ├─ StyleSelector                 (シェル §4.2-7) 「スタイル: 自然訳 ▾」
   │  ├─ InPaperSearch                 (シェル §7、SearchBox 共通 §5.13) 「この論文内を検索」+ Keycap「/」
   │  └─ ViewerOverflowMenu ⋯          (シェル §4.2-9)
   ├─ TocPane                          (シェル §5.3) w232px 目次
   │  ├─ TocRow                        (シェル) + BookmarkIcon (共通 §6.1)
   │  └─ ペインフッタ                   (シェル §5.3) 「今日の読書 42分」/「位置は自動保存」
   ├─ ParallelView                     (1a共有) 対訳 2 カラム
   │  ├─ ParagraphPair                 (1a共有) grid 1fr 1fr
   │  │  └─ InlineRenderer             (1a共有) text/bold/citation/ref の描画
   │  │     ├─ FigureRefLink           (1c固有) 「Figure 2」「図2」参照リンク
   │  │     └─ CitationLink            (1c固有) 「[29]」引用リンク
   │  └─ FigureRefPopover              (1c固有) = Popover (共通 §5.10) width 400
   ├─ SidePanel                        (シェル §6) w340px
   │  ├─ SidePanelTabs                 (共通 §5.16) 図表アクティブ + CountBadge 'tab'
   │  └─ FiguresTab                    (1c固有) 図表タブ本体(図表一覧+参考文献)
   │     ├─ PanelSectionHeading        (1c固有) 「図表一覧」「参考文献」
   │     ├─ FigureCard ×N              (1c固有) サムネ+キャプション+位置
   │     ├─ PanelDivider               (1c固有) 1px 区切り線
   │     └─ ReferenceRow ×N            (1c固有) 折りたたみ/展開
   └─ FigureLightbox                   (1c固有) = Modal (共通 §5.11)「拡大」
```

### 3.2 1c 固有コンポーネントの props 型(確定)

型の要素は plans/03 のレスポンス型(生成クライアント `@alinea/api-client` の型)を使う。

```ts
// apps/web/src/components/viewer/panel/figures/types.ts
import type { components } from '@alinea/api-client';
export type FigureItem = components['schemas']['FigureItem'];
// = { block_id; kind: 'figure'|'table'; label; display: '図2'|'表1'; caption_en;
//     caption_ja: string|null; image_url: string|null;
//     position: { section_display: string; page: number|null } }
export type ReferenceItem = components['schemas']['ReferenceItem'];
// = { ref_id; number: '[12]'; authors; title; venue_year; arxiv_id; doi; url;
//     in_library: { library_item_id: string } | null }
```

```ts
// FigureRefLink.tsx — 本文中の図表参照(原文「Figure 2」/訳文「図2」)
interface FigureRefLinkProps {
  refBlockId: string;              // 参照先の図ブロック ID(inline { t:'ref', kind:'figure'|'table', ref })
  label: string;                   // 原文側 'Figure 2' / 訳文側 '図2'(ドキュメント由来の逐語)
  active: boolean;                 // ポップオーバー表示中のクリックされたアンカーのみ true(判定規則は §5.2)
  onOpen: (refBlockId: string, anchorEl: HTMLElement) => void;
}

// CitationLink.tsx — 本文中の引用「[29]」
interface CitationLinkProps {
  refId: string;                   // 'ref-29'
  label: string;                   // '[29]'
  onOpen: (refId: string) => void; // 図表タブ参考文献の該当行を展開表示(docs/04 §7 決定)
}

// FigureRefPopover.tsx — 図参照ポップオーバー(共通 Popover のインスタンス)
interface FigureRefPopoverProps {
  figure: FigureItem | undefined;  // undefined = figures 未着(ローディング)
  anchorRef: React.RefObject<HTMLElement>;   // クリックされた参照 span
  open: boolean;
  onClose: () => void;
  onJumpToFigure: (blockId: string) => void; // 「図の位置へ移動 →」
  onZoom: (figure: FigureItem) => void;      // 「拡大」
  onExplain: (figure: FigureItem) => void;   // 「✦ この図を説明」
}

// FiguresTab.tsx — 図表タブ本体(viewer-shell §6.5 の契約どおり props を受けない。
// itemId は useParams()、revisionId は viewer-store、図表状態は §3.3 の figures ストアから取得)
// FiguresTab が下位へ渡す内部値: activeFigureBlockId(「(表示中)」同期)、
// expandedRefId(展開中の参考文献。常に最大 1 件)、onSelectFigure / onToggleReference。

// FigureCard.tsx
interface FigureCardProps {
  figure: FigureItem;
  selected: boolean;               // 選択中=「(表示中)」付記+アクセント面
  onClick: () => void;
}

// ReferenceRow.tsx
interface ReferenceRowProps {
  reference: ReferenceItem;
  expanded: boolean;
  importState: 'idle' | 'importing' | 'imported'; // §5.6 の取り込み状態
  onToggle: () => void;
  onImport: () => void;            // POST /api/ingest/arxiv
  onOpenInLibrary: (libraryItemId: string) => void; // 「ライブラリに有り ✓」クリック
}

// PanelSectionHeading.tsx
interface PanelSectionHeadingProps { label: string } // '図表一覧' | '参考文献'

// FigureLightbox.tsx — 「拡大」(共通 Modal のインスタンス)
interface FigureLightboxProps {
  figure: FigureItem | null;       // null = 閉
  onClose: () => void;
}
```

### 3.3 クライアント状態(Zustand)

シェルの `stores/viewer-store.ts` は viewer-shell §2.3 で「完全形」確定済みのため変更しない(決定)。1c の図表状態は独立ストア `apps/web/src/stores/viewer-figures-store.ts` に置き、ページマウント時(`itemId` 変化時)に全フィールドを初期値(すべて null)へリセットする:

```ts
interface ViewerFigureState {
  figurePopover: { refBlockId: string; anchorEl: HTMLElement } | null; // 開いているポップオーバー(同時 1 個)
  activeFigureBlockId: string | null;   // 本文参照アクティブ&図表カード「(表示中)」の同期キー
  expandedRefId: string | null;         // 展開中の参考文献行(排他)
  lightboxFigure: FigureItem | null;    // 「拡大」表示中
  openFigurePopover(refBlockId: string, anchorEl: HTMLElement): void; // activeFigureBlockId も設定
  closeFigurePopover(): void;           // activeFigureBlockId は保持(決定: カード「(表示中)」はポップオーバーを閉じても直近参照として残す。デザインの同期仕様の自然な持続)
  setExpandedRef(refId: string | null): void;
  setLightbox(f: FigureItem | null): void;
}
```

## 4. レイアウト・スタイル完全仕様

出典: extract/1c.md(元 HTML 行 1628〜1755、`<div id="1c">`)。値はすべて実測逐語。トークン名(plans/08 §2)を併記する — 実装はトークン経由で書き、ダーク値はトークンの `html[data-theme="dark"]` ブロックが供給する(個別 `dark:` 分岐は書かない)。

### 4.0 デザイナー注記(デザインカタログ上の注記。アプリには実装しない)

- バッジ「1c」/ タイトル「論文ビューア — ダークモード」/ 説明「図表参照ポップオーバー(「図2」をクリック・スクロールしない) / サイドパネル=図表・参考文献」。
- フレーム内に注記ボックスなし。ポップオーバーはフレーム内に絶対配置で描画済み(=別状態パネルなし)。

### 4.1 フレーム全体

- 1440×900px(基準ビューポート。実アプリはビューポート全面、plans/08 §7.1)。背景 `#181B20`(`--pr-bg-app`)、基準文字色 `#E8E6E1`(`--pr-text`)、flex 縦並び、position:relative。
- デザインフレームの border 1px `#101216`・radius 10px・shadow `0 20px 44px rgba(28,30,34,0.20)` はカタログ表現であり実アプリでは描画しない(plans/08 §7.1)。
- ヘッダ下の本体行: `flex:1; display:flex; min-height:0`。
- 3 ペイン: 左目次 w232px(flex:none)/ 中央 flex:1(1440px 時 ≈866px)/ 右パネル w340px(flex:none)。

### 4.2 ヘッダーバー(1a 共有。1c ダーク実測値)

h52px、flex:none、bg `#1E2228`(`--pr-bg-card`)、border-bottom 1px `#2A2F37`(`--pr-border-header`)、flex 横、align-items:center、gap:10px、padding 0 16px。左から:

1. 戻る「‹」: span、font 16px、色 `#7A7F87`(`--pr-text-icon`)、width 20px、text-align:center。
2. 論文タイトル: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」— font 13px/600、max-width 330px、white-space:nowrap、overflow:hidden、text-overflow:ellipsis、色 `#E8E6E1`(`--pr-text`)。
3. QualityBadge「A」: 18×18px、radius 4px、bg `var(--pr-ads)`=rgba(143,174,203,0.14)(ダークでは `--pr-acc-s`→`--pr-ads`)、文字色 `var(--pr-ad)`=#8FAECB(`--pr-acc`)、font 10.5px/700、inline-flex 中央。`title="品質レベルA: LaTeXソースから完全構造化"`。
4. StatusPill「読んでいる ▾」: inline-flex、gap:5px、h24px、padding 0 9px、border 1px `#333942`(`--pr-border-control`)、radius 999px、font 11.5px/500、bg `#22262D`(`--pr-bg-control`)、文字色 `#C9CCD1`(`--pr-text-mid`)。先頭ドット 7×7px 円、bg `var(--pr-ad)`(status=reading → `--pr-status-reading`=`--pr-acc`)。末尾「▾」色 `#7A7F87`、font 9px。
5. スペーサー(flex:1)。
6. SegmentedControl(表示モード): トラック bg `#14171B`(`--pr-bg-muted`)、radius 7px、padding 2px、gap 2px。各セグメント h24px、inline-flex、padding 0 11px、radius 5px、font 11.5px。非選択「訳文」「原文」「PDF」「記事」: 色 `#9BA1A9`(`--pr-text-sub`)、背景なし。選択中「対訳」: bg `#2C313A`(`--pr-bg-seg-selected`)、色 `#F0EEE9`、font-weight 600、shadow なし(ダーク。plans/08 §5.1)。
7. StyleSelector「スタイル: 自然訳 ▾」: inline-flex、gap:5px、h26px、padding 0 10px、border 1px `#333942`、radius 6px、font 11.5px、色 `#C9CCD1`。「▾」は `#7A7F87` / 9px。
8. SearchBox(in-paper)「この論文内を検索」: inline-flex、gap:6px、h26px、padding 0 10px、bg `#14171B`(`--pr-bg-inset`)、radius 6px、font 11.5px、色 `#7A7F87`、width 150px。先頭 MagnifierIcon 11×11(viewBox 0 0 12 12、circle cx5 cy5 r3.6 + path M8 8 l2.6 2.6、stroke currentColor 1.3、線端 round)。右端 Keycap「/」: margin-left:auto、border 1px `#333942`(`--pr-border-keycap`)、radius 3px、padding 0 4px、font 9.5px、bg `#22262D`(`--pr-bg-control`)。
9. オーバーフロー「⋯」: font 15px、色 `#9BA1A9`、letter-spacing 1px。

### 4.3 左サイドバー: 目次(w232px。1a 共有)

bg `#1B1E24`(`--pr-bg-pane`)、border-right 1px `#2A2F37`(`--pr-border-pane`)、flex 縦、padding 10px 8px 8px。

- ヘッダー行: flex、align-items:center、justify-content:space-between、padding 0 8px 8px。「目次」font 11px/600 色 `#7A7F87`(`--pr-text-muted`)。「翻訳 96%」font 10.5px 色 `#7A7F87`(値は `viewer.translation.progress_pct`)。
- 目次リスト: flex 縦、gap 1px、font 12.3px、色 `#C9CCD1`(`--pr-text-nav`)、flex:1、overflow:hidden(実装は overflow-y:auto)。章レベル項目 padding 4px 8px / 節レベル padding 4px 8px 4px 22px(左インデント 22px)。
- 項目(逐語・上から): 「アブストラクト」「1 はじめに」「2 手法」「2.1 整流フロー」(節)、**アクティブ「2.2 Reflow: 経路の直線化」**(節): flex、align-items:center、gap:6px、radius 5px、bg `var(--pr-ads)`(`--pr-acc-s`)、色 `var(--pr-ad)`(`--pr-acc`)、font-weight 600、`box-shadow: inset 2px 0 var(--pr-ad)`(左端 2px アクセントバー)。テキスト flex:1。右端 BookmarkIcon 9×11(viewBox 0 0 10 12、path `M1 1h8v10L5 8.5 1 11V1z`、fill currentColor)= ブックマーク済み。
- 続き: 「2.3 蒸留との関係」(節)「3 実験」「3.1 CIFAR-10 画像生成」(節)「3.2 高解像度画像への拡張」(節)「3.3 ドメイン転移」(節)「4 関連研究」「5 結論」「参考文献」(色 `#7A7F87` で弱表示 = `in_progress_denominator: false` の TocNode)。
- フッター: padding 8px 8px 2px、border-top 1px `#2A2F37`、font 10.5px、色 `#7A7F87`。文言は viewer-shell §5.3 の決定に従い**両テーマ共通で 2 項目レイアウト**(flex、space-between): 左「今日の読書 42分」(42分 = `viewer.today_reading_minutes` の整形)/ 右「位置は自動保存」。1c デザインカタログの 1 行連結「今日の読書 42分 · 自動保存」は採用しない(同一情報の簡略描画差。テーマでレイアウトを分岐させない — viewer-shell §5.3 決定を正とする)。

### 4.4 中央: 対訳ビュー(1a 共有。1c ダーク実測値)

flex:1、min-width:0、padding 18px 34px 0、flex 縦、overflow:hidden、position:relative。

- カラム見出し行: grid、`grid-template-columns: 1fr 1fr`、column-gap 34px、padding-bottom 8px、border-bottom 1px `#262B32`(`--pr-border-soft`)。左「原文 — ENGLISH」: font 10.5px/600、色 `#7A7F87`、letter-spacing 0.4px。右「訳文 — 自然訳 ✦ AI翻訳」: 同スタイル、「✦ AI翻訳」部分のみ色 `var(--pr-ad)`(`--pr-acc`)。
- 本文領域: flex:1、overflow:hidden(実装は overflow-y:auto)、padding-top 16px。
- セクション見出し「2.2 Reflow: 経路の直線化 — Reflow: Straightening the Flow」: 和文 font 16.5px/700、margin-bottom 14px、色 `#F0EEE9`。英語部分 span「— Reflow: Straightening the Flow」: 色 `#7A7F87`、weight 400、font 13.5px、font-family `'Source Serif 4', Georgia, serif`(`--pr-font-en`)、italic。
- 段落グリッド: grid `1fr 1fr`、column-gap 34px、row-gap 18px。原文段落(左列): font-family `--pr-font-en`、font 13.8px、line-height 1.72、色 `#C4C7CC`(`--pr-text-en`)。訳文段落(右列): font-family `var(--pr-jp)`、font 14.8px、line-height 2.0、色 `#DEDCD7`(`--pr-text-body`)。

段落ペア(サンプル本文逐語。シードデータ arXiv:2209.03003 で再現):

- **ペア1** 原文: "The paths of the rectified flow may still be curved, because the flow is only guaranteed to match the marginals of the linear interpolation. We propose to **recursively apply the rectification**: the couplings generated by the previous flow are used as the training pairs for the next one."(太字=`<b>recursively apply the rectification</b>`)/ 訳文: 「整流フローの経路は、線形補間の周辺分布に一致することのみが保証されるため、依然として曲がっている場合がある。そこで我々は**整流の再帰的な適用**を提案する。すなわち、前段のフローが生成したカップリングを、次段の学習ペアとして用いる。」(太字=「整流の再帰的な適用」。インライン強調は原文・訳文で対応保持)
- **ペア2(図参照)** 原文: "As shown in Figure 2, each reflow step provably reduces the transport cost and straightens the paths; after 2–3 steps the trajectories become nearly straight, enabling accurate simulation with **a single Euler step**." — 「Figure 2」は FigureRefLink: 色 `var(--pr-ad)`、`border-bottom: 1px dotted var(--pr-ad)`、weight 600。/ 訳文(position:relative — ポップオーバーの基準): 「図2 に示すように、各 reflow ステップは輸送コストを確実に減少させ、経路を直線化する。2〜3 ステップの後には軌道はほぼ直線となり、**1回のオイラーステップ**での正確なシミュレーションが可能になる。」— 「図2」はクリック済みアクティブ状態: 外側 span に bg `var(--pr-ads)`、`outline: 1px solid var(--pr-adm)`(rgba(143,174,203,0.40))、radius 2px。内側 span は色 `var(--pr-ad)`、border-bottom 1px dotted、weight 600。
- **ペア3(引用)** 原文: "Combined with distillation [29], the straightened flow yields one-step generators with FID 4.85 on CIFAR-10, substantially improving over prior distillation of diffusion ODEs." — 「[29]」は CitationLink: 色 `var(--pr-ad)`、weight 600、**下線なし**。/ 訳文: 「蒸留 [29] と組み合わせることで、直線化されたフローは CIFAR-10 において FID 4.85 の1ステップ生成器を与え、従来の拡散 ODE の蒸留を大きく上回る。」(「[29]」同スタイル)

### 4.5 図参照ポップオーバー(1c 固有)

共通 Popover(plans/08 §5.10)のインスタンス。width **400px**、bg `#22262D`(`--pr-bg-pop`)、border 1px `#3A404A`(`--pr-border-pop`)、radius 10px、shadow `0 22px 52px rgba(8,10,13,0.55)`(`--pr-shadow-pop` ダーク値)、`z-index: var(--z-inline-popover)`(=5)、`font-family: var(--pr-font-ui)`(本文セリフを明示的に上書き — 'IBM Plex Sans JP')。

- **配置(決定)**: デザイン実測は訳文段落(position:relative)基準で `top:40px; left:-20px`、キャレット `left:44px`。一般化規則: アンカー=クリックされた参照 span。ポップオーバー top = 参照 span の bottom + 10px、left = 参照 span の左端 − 20px、キャレット left = 44px(≒参照テキスト中央)。ビューポート右端からのはみ出しは left を `min(left, viewportRight − 400 − 12px)` にクランプし、キャレット left を「参照 span 中心 − ポップオーバー left − 4.5px」で再計算する。下端はみ出し時の上方向への反転は**行わない**(plans/08 §5.10 の決定「上方向・横方向の自動反転は実装しない」に従う。下端で切れる場合はユーザーのスクロールに委ね、本文スクロールでポップオーバーはアンカーに追随せず閉じる — 決定: スクロールイベントで `closeFigurePopover()`)。理由: 実測値(基準段落先頭の参照)をアンカー相対に写像した最小規則。
- 吹き出し矢印(上向き): position:absolute、top:-5px、left:44px、9×9px、bg `#22262D`、border-left+border-top 1px `#3A404A`、`transform: rotate(45deg)`(共通 Popover の caret、`caretOffset: { side: 'left', px: 44 }`)。
- 画像領域ラッパー: padding 12px 14px 0。画像: `figure.image_url` の `<img>`(width 100%、height 170px、object-fit:contain、radius 6px、bg `#191C21`、border 1px `#2E343D`)。`image_url: null` 時のプレースホルダ: height 170px、radius 6px、bg `#191C21`(`--pr-bg-thumb`)、border 1px `#2E343D`(`--pr-border-thumb`)、flex 中央、色 `#5C626B`(`--pr-text-thumb`)、font 11.5px、letter-spacing 0.4px、文言「図2(原論文の画像)」(=`${display}(原論文の画像)`)。
- 下部: padding 10px 14px 12px、flex 縦、gap 6px。
  - 原文キャプション: font-family `--pr-font-en`、italic、font 10.5px、line-height 1.6、色 `#9BA1A9`(`--pr-text-sub2`。plans/08 §2.1 でダーク値 #9BA1A9 に確定)— "Figure 2: Trajectories of 1-, 2-, 3-rectified flows on toy examples. Reflow straightens the paths."(=`caption_en`)
  - 訳キャプション: font-family `var(--pr-jp)`、font 12px、line-height 1.8、色 `#DEDCD7`(`--pr-text-body`)— 「図2: トイ例における 1・2・3-整流フローの軌道。reflow が経路を直線化する。」(=`caption_ja`)。`caption_ja: null` の場合はこの行を描画しない(原文キャプションのみ。決定)。
  - ボタン行: flex、gap 6px、padding-top 2px。3 ボタンすべて h23px、inline-flex、align-items:center、padding 0 10px、radius 5px、font 10.5px。
    1. プライマリ「図の位置へ移動 →」: bg `var(--pr-ad)`(`--pr-acc`)、文字色 `#181B20`、weight 700。
    2. セカンダリ「拡大」: border 1px `#3A404A`(`--pr-border-pop`)、色 `#C9CCD1`(`--pr-text-mid`)。
    3. AI ボタン「✦ この図を説明」: border 1px `#3A404A`、色 `var(--pr-ad)`(`--pr-acc`)、weight 600。

### 4.6 右サイドパネル: 図表タブ(w340px。1c 固有)

bg `#1E2228`(`--pr-bg-card`)、border-left 1px `#2A2F37`(`--pr-border-pane`)、flex 縦。

- **タブ行**(共通 SidePanelTabs): flex、border-bottom 1px `#262B32`(`--pr-border-soft`)、padding 0 6px。各タブ padding 10px 9px 8px、font 12px。非選択: 色 `#9BA1A9`(`--pr-text-sub2`。plans/08 §2.1 でダーク `--pr-text-sub2` = #9BA1A9 に確定済み — plans/08 §5.16・viewer-shell §6.1 の指定と 1c 実測が一致する)—「チャット」「メモ」「注釈 6」「リソース 4」「情報」。件数(CountBadge `tab`): font 10px、色 `#7A7F87`(`--pr-text-muted`)、値は `viewer.counts.annotations`(6)・`viewer.counts.resources`(4)。件数バッジはこの 2 タブのみ・0 件時は非表示(viewer-shell §6.1)。選択タブ「図表」: weight 600、色 `var(--pr-ad)`(`--pr-acc`)、`box-shadow: inset 0 -2px var(--pr-ad)`。
- **パネル本体**: flex:1、overflow:hidden(実装は overflow-y:auto)、padding 12px、flex 縦、gap 8px。

#### 図表一覧セクション

- 見出し「図表一覧」(PanelSectionHeading): font 10.5px/600、色 `#7A7F87`(`--pr-text-muted`)、letter-spacing 0.4px。
- FigureCard(共通スタイル): flex、gap 10px、padding 8px、radius 7px。
  - サムネイル: 52×38px、radius 4px、bg `#191C21`(`--pr-bg-thumb`)、border 1px `#2E343D`(`--pr-border-thumb`)、flex:none、flex 中央、色 `#5C626B`(`--pr-text-thumb`)、font 9px。`image_url` があれば `<img>`(object-fit:cover)、null ならラベル「図1」「図2」「表1」(=`display`)。
  - 説明テキスト: font 11px、line-height 1.6、overflow:hidden。位置サブテキスト: font 9.5px、色 `#7A7F87`(`--pr-text-muted`)。
  - 1: 非選択「図1」: border 1px transparent、テキスト色 `#C9CCD1`(`--pr-text-mid`)— 「図1: 整流フローの概観。直線補間の因果化」/ サブ「§1 · p.2」。
  - 2: **選択中「図2」**: bg `var(--pr-ads)`(`--pr-acc-s`)、border 1px `var(--pr-adm)`(`--pr-acc-m`)、テキスト色 `#F0EEE9` — 「図2: reflow による経路の直線化(表示中)」/ サブ「§2.2 · p.5」。「(表示中)」は `selected` 時にキャプション末尾へ付記するクライアント側文字列(API の `caption_ja` には含まれない)。
  - 3: 非選択「表1」: 図1と同スタイル — 「表1: CIFAR-10 での FID / ステップ数比較」/ サブ「§3.1 · p.7」。
  - キャプション表示規則(決定): `caption_ja` があれば `caption_ja` を、なければ `caption_en` を 2 行まで表示(`display:-webkit-box; -webkit-line-clamp:2; overflow:hidden`)。位置サブは `${position.section_display} · p.${position.page}`(page null なら「§2.2」のみ)。
- 区切り線(PanelDivider): height 1px、bg `#262B32`(`--pr-border-soft`)、margin 6px 0。

#### 参考文献セクション

- 見出し「参考文献」: 「図表一覧」と同スタイル。
- リスト: flex 縦、gap 2px、font 11px、line-height 1.55、色 `#C9CCD1`(`--pr-text-mid`)。各行 padding 6px 8px、radius 6px。番号ラベル(「[8]」等)は色 `#7A7F87`(`--pr-text-muted`)、font-family `--pr-font-mono`('IBM Plex Mono')。会議名は `<i>` で色 `#9BA1A9`(`--pr-text-sub2`。plans/08 §2.1 でダーク値 #9BA1A9 に確定)。
  - 1(通常行): 「[8] Goodfellow et al. Generative Adversarial Nets. *NeurIPS 2014*」
  - 2(**展開状態**「[12]」): bg `#22262D`(`--pr-bg-pop`)、border 1px `#333942`(`--pr-border-control`)、flex 縦、gap 6px。
    - 本文: 「[12] Song et al. Score-Based Generative Modeling through SDEs. *ICLR 2021* · arXiv」— 「arXiv」は色 `var(--pr-ad)`(`--pr-acc`)の外部リンク(`arxiv_id` から `https://arxiv.org/abs/{arxiv_id}`、`target="_blank" rel="noopener"`)。
    - ボタン行: flex、gap 6px。両ボタン h22px、inline-flex、align-items:center、padding 0 9px、radius 5px、font 10.5px。
      - プライマリ「+ この論文も取り込む」: bg `var(--pr-ad)`(`--pr-acc`)、文字色 `#181B20`、weight 700(`in_library === null` のとき表示)。
      - セカンダリ「ライブラリに有り ✓」: border 1px `#3A404A`(`--pr-border-pop`)、色 `#9BA1A9`(`--pr-text-sub`)(`in_library !== null` のとき表示。デザインは両ボタン併記だがこれは 2 状態のカタログ提示であり、実装は排他表示と決定。理由: 同一文献が「未取り込み」かつ「ライブラリに有り」であることはデータ上あり得ない)。
  - 3(通常行): 「[29] Salimans & Ho. Progressive Distillation. *ICLR 2022*」
  - 4(通常行): 「[31] Ho et al. Denoising Diffusion Probabilistic Models. *NeurIPS 2020*」
  - 行の組み立て規則(決定): `{number} {authors}. {title}. <i>{venue_year}</i>`。`authors`/`title`/`venue_year` が null のフィールドは省略し区切りピリオドも出さない。展開行のみ末尾に「 · arXiv」(`arxiv_id` 非 null 時)。

### 4.7 全 UI 文言(逐語チェックリスト)

ヘッダ: `‹` / `Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow` / `A` / `読んでいる ▾` / `訳文` `対訳` `原文` `PDF` `記事` / `スタイル: 自然訳 ▾` / `この論文内を検索` / `/` / `⋯`
目次: `目次` / `翻訳 96%` / `アブストラクト` / `1 はじめに` / `2 手法` / `2.1 整流フロー` / `2.2 Reflow: 経路の直線化` / `2.3 蒸留との関係` / `3 実験` / `3.1 CIFAR-10 画像生成` / `3.2 高解像度画像への拡張` / `3.3 ドメイン転移` / `4 関連研究` / `5 結論` / `参考文献` / `今日の読書 42分` `位置は自動保存`(フッタ 2 項目。viewer-shell §5.3 決定 — カタログの 1 行連結表記は不採用)
本文: `原文 — ENGLISH` / `訳文 — 自然訳 ✦ AI翻訳` / `2.2 Reflow: 経路の直線化 — Reflow: Straightening the Flow` / (段落 3 ペアは §4.4 逐語)
ポップオーバー: `図2(原論文の画像)` / `Figure 2: Trajectories of 1-, 2-, 3-rectified flows on toy examples. Reflow straightens the paths.` / `図2: トイ例における 1・2・3-整流フローの軌道。reflow が経路を直線化する。` / `図の位置へ移動 →` / `拡大` / `✦ この図を説明`
右パネル: `チャット` `メモ` `注釈 6` `図表` `リソース 4` `情報` / `図表一覧` / `図1: 整流フローの概観。直線補間の因果化` `§1 · p.2` / `図2: reflow による経路の直線化(表示中)` `§2.2 · p.5` / `表1: CIFAR-10 での FID / ステップ数比較` `§3.1 · p.7` / `参考文献` / `[8] Goodfellow et al. Generative Adversarial Nets. NeurIPS 2014` / `[12] Song et al. Score-Based Generative Modeling through SDEs. ICLR 2021 · arXiv` / `+ この論文も取り込む` / `ライブラリに有り ✓` / `[29] Salimans & Ho. Progressive Distillation. ICLR 2022` / `[31] Ho et al. Denoising Diffusion Probabilistic Models. NeurIPS 2020`

### 4.8 データフィールド対応表

| UI 要素 | API フィールド(plans/03) |
|---|---|
| タイトル / A バッジ / ステータスピル | `viewer.library_item.paper.title` / `viewer.library_item.quality_level` / `viewer.library_item.status` |
| 翻訳 96% / スタイル: 自然訳 | `viewer.translation.progress_pct` / `viewer.translation.style`(`natural`→「自然訳」、`literal`→「直訳」) |
| 目次項目・階層・ブックマーク・弱表示 | `viewer.toc[]`: `number`+`title_ja`、`children`、`bookmarked`、`in_progress_denominator:false` |
| 今日の読書 42分 | `viewer.today_reading_minutes`(`${Math.floor(min)}分`) |
| タブ件数 注釈 6 / リソース 4 | `viewer.counts.annotations` / `viewer.counts.resources` |
| 段落・太字・図参照・引用 | `document.sections[].blocks[].inlines[]`(`t: 'text'|'bold'|'ref'|'citation'`)+ `units[].text_ja` |
| ポップオーバー・図表カード | `figures.items[]`: `display` / `caption_en` / `caption_ja` / `image_url` / `position` |
| 参考文献行・展開・arXiv・在庫判定 | `references.items[]`: `number` / `authors` / `title` / `venue_year` / `arxiv_id` / `in_library` |

## 5. 状態とインタラクション

### 5.1 ダークモード適用

- `<html data-theme="dark">` で全トークンが plans/08 §2.1 ダークブロックへ切替。アクセント意味エイリアス `--pr-acc/-s/-m` が `--pr-ad/-ads/-adm` を指す(ライト用 `--pr-a` 系の流用禁止 — 1c の中核ファクト)。コンポーネント側にダーク分岐コードは書かない(例外: SegmentedControl 選択 shadow none)。
- テーマ切替 UI はビューアヘッダに置かない。切替は設定画面 4f のみから行う(viewer-shell §4.2-9 の ⋯ メニュー項目 5 件にテーマ切替は含まれない — 決定。適用機構は plans/08 §8)。

### 5.2 図表参照リンク(FigureRefLink)

| 状態 | スタイル | 遷移 |
|---|---|---|
| 通常 | 色 `--pr-acc`、border-bottom 1px dotted `--pr-acc`、weight 600 | — |
| ホバー(決定) | `background: var(--pr-acc-s)`、radius 2px(アクティブ面の予告。カーソル pointer) | — |
| アクティブ(表示中) | 外側 span: bg `--pr-acc-s`、outline 1px solid `--pr-acc-m`、radius 2px(デザイン実測) | ポップオーバー開時に付与、閉時に解除 |

- クリック: `openFigurePopover(refBlockId, anchorEl)`。**スクロールしない**(preventDefault、位置固定)。同一参照の再クリックはトグル(閉じる)。別の参照クリックは既存を閉じて新規を開く(同時 1 個)。
- 原文側「Figure 2」・訳文側「図2」は同一 `refBlockId` を指し、どちらをクリックしても同じポップオーバーが開く(アンカーはクリックした側)。
- アクティブ様式の判定(決定): `figurePopover !== null && figurePopover.anchorEl === 自要素` — つまり**ポップオーバー表示中の、クリックされたアンカー span のみ**に付与する(デザインどおり、原文側「Figure 2」は通常様式のまま)。ポップオーバーを閉じたら参照 span のアクティブ様式は解除する。閉後も保持される `activeFigureBlockId` は図表カードの「(表示中)」表示にのみ作用し、本文参照 span の様式には影響しない(§3.3 の `active` prop の意味を本項で確定)。
- CitationLink のホバー(決定): FigureRefLink と同一 — `background: var(--pr-acc-s)`、radius 2px、cursor pointer。通常時は下線なし(§4.4 実測)。

### 5.3 図参照ポップオーバー(FigureRefPopover)

- 開: §5.2。開いた時 `activeFigureBlockId = refBlockId` → 本文参照アクティブ様式+図表タブ「(表示中)」カードが同期点灯。
- 閉: 外側クリック / Esc / 再クリック / 別ポップオーバー開。閉じても `activeFigureBlockId` は保持(§3.3 決定)。
- ローディング(決定): `figures` クエリ未着でクリックされた場合、画像領域と同寸(400×170px 部分)のプレースホルダに文言「読み込み中…」(色 `--pr-text-thumb`、font 11.5px)を出し、キャプション行はスケルトン 2 本(高さ 10px と 12px、幅 92%/70%、bg `--pr-bg-thumb`、radius 3px、パルスは §5.7 と同一: opacity 0.6⇄1.0、1200ms)。ボタン行は非表示。データ着で置換。
- エラー(決定): `figures` クエリ失敗時は画像領域に「図を読み込めませんでした」+ セカンダリボタン様式(§4.5-2)で「再試行」(クリックで refetch)。黙って閉じない(P3)。
- データ不一致(決定): `figures` 到着済みで `refBlockId` に一致する `FigureItem` が無い場合(抽出漏れ)、ポップオーバーは開かず「図の位置へ移動 →」と同じスクロールジャンプにフォールバックする(参照リンクを死なせない)。
- **「図の位置へ移動 →」**: ポップオーバーを閉じ、`document.getElementById(blockId)` へ `scrollIntoView({ behavior: 'smooth', block: 'center' })`。対象ブロックのセクションが未ロード(要素が DOM に無い)場合は viewer-store の `requestScroll({ kind: 'block', blockId })` に委譲する(viewer-shell §3.4 の `pendingScrollTarget` 機構。セクションロード後にスクロール+ハイライト。決定。§5.4 のカードクリックも同じフォールバック)。着地後、図ブロックに `bg: var(--pr-acc-s)` を 1200ms かけてフェードアウトするハイライトを付与(決定。ジャンプ先の視認性確保。1a のチャット根拠ジャンプと同一挙動)。
- **「拡大」**: `setLightbox(figure)` → FigureLightbox(§5.5)。
- **「✦ この図を説明」**: (1) ポップオーバーを閉じる、(2) サイドパネルタブを `chat` に切替(viewer-store の `setPanel(true, 'chat')`。URL には載せない — viewer-shell §2.3 の決定どおりタブは URL 非同期)、(3) メインスレッド(`is_main: true`)に対し `POST /api/chat/threads/{thread_id}/messages` を `{ content: "", quick_action: "explain_figure", context_anchors: [{ revision_id, block_id: 図ブロックID, start: null, end: null, quote: null, side: "source" }] }` で送信し SSE 受信を開始(docs/05・plans/03 §10.3。図はブロック全体参照)。
- ホバー(決定): プライマリ/AI ボタンは `filter: brightness(1.08)`、セカンダリは `background: var(--pr-bg-hover)`(#22262D 上で #22262D のため実質 border 明度で判別 → セカンダリのみ `border-color: #4A4E55` に変化)。transition 120ms ease-out。

### 5.4 図表カード(FigureCard)

| 状態 | スタイル |
|---|---|
| 非選択 | border 1px transparent、テキスト `--pr-text-mid` |
| ホバー(決定) | bg `var(--pr-bg-hover)`(#22262D)、カーソル pointer |
| 選択中 | bg `--pr-acc-s`、border 1px `--pr-acc-m`、テキスト #F0EEE9(`--pr-text` 強)、キャプション末尾「(表示中)」 |

- クリック(docs/04 §10.2「クリックで該当図を表示」の解釈確定): `activeFigureBlockId` を設定し、**本文中の当該図ブロックへスクロールして表示**する(`scrollIntoView` center + 1200ms ハイライト)。ポップオーバーは開かない。開いているポップオーバーがあれば閉じる(決定: スクロールを伴うため §4.5 のスクロール時クローズと同一)。理由: パネル側からの操作は「図の実体を見る」意図であり、ポップオーバーの再現(参照テキスト基準)はアンカーが存在しないため。
- 選択中カードは 1 枚のみ(`activeFigureBlockId` 単一値)。

### 5.5 図の拡大ライトボックス(FigureLightbox)

共通 Modal(plans/08 §5.11)のインスタンス。決定仕様:

- width: `min(画像natural幅 + 48px, 1080px)`(決定。共通 Modal の `width` prop に算出値を渡す — plans/08 §5.11 の既定 460 は使わない)。radius 14px、bg `--pr-bg-card`、shadow `--pr-shadow-modal`、スクリム `--pr-scrim`、`z-index: var(--z-modal)`。
- 内容: padding 24px。画像(width 100%、radius 6px、bg `--pr-bg-thumb`。`image_url` はポップオーバー/カードと同一 URL — §22.1 にサイズ違いアセットは存在せず、ブラウザキャッシュで再利用される。モーダル開時に `<img>` を初めてマウントする、の意)。natural 幅判明前(読み込み中)のモーダル width は 640px 固定とし、onLoad 後に上式で確定する(決定)→ 下に原文キャプション(§4.5 と同スタイル、font 11.5px)→ 訳キャプション(font 13px)。キャプション対訳併記(docs/04 §7「原寸・キャプション対訳」)。
- 閉: Esc / スクリムクリック / 右上「×」(12px、`--pr-text-muted`、padding 8px、位置 top:10px right:12px。決定)。

### 5.6 参考文献行(ReferenceRow)

| 状態 | スタイル / 挙動 |
|---|---|
| 折りたたみ(既定) | §4.6 の通常行。1 行表示 |
| ホバー(決定) | bg `var(--pr-bg-hover)`、カーソル pointer |
| 展開 | bg `--pr-bg-pop`(#22262D)、border 1px `--pr-border-control`(#333942)、flex 縦 gap 6px、ボタン行表示 |

- 展開行内ボタンのホバー(決定): §5.3 のボタンホバー規則と同一(プライマリ `filter: brightness(1.08)`、セカンダリ `border-color: #4A4E55`、transition 120ms ease-out)。

- 行クリックでトグル展開。展開は**排他**(常に最大 1 行。`expandedRefId` 単一値。決定。理由: デザインは 1 行のみ展開状態を提示しており、縦スペースの限られたパネルで多重展開は一覧性を壊す)。
- 本文の引用リンク「[29]」クリック(CitationLink): サイドパネルタブを `figures` へ切替え、該当 ReferenceRow を展開し、パネル内で該当行へ `scrollIntoView({ block: 'nearest' })`(docs/04 §7 の決定)。本文はスクロールしない。
- **「+ この論文も取り込む」**(`in_library === null` かつ `arxiv_id !== null` のとき表示。`arxiv_id === null` の文献ではボタン自体を出さず arXiv リンクも出さない): クリックで `POST /api/ingest/arxiv { url: "https://arxiv.org/abs/{arxiv_id}" }`。
  - 送信中〜ジョブ完了まで(決定): ボタンを disabled にしラベルを「取り込み中…」(bg `--pr-acc-s`、色 `--pr-acc`、weight 600)へ置換。レスポンス 202 の `job_id` で SSE 購読、`done` 受信 → `['references', revId]` invalidate → 行が「ライブラリに有り ✓」表示に変わる。Toast(plans/08 §5.20)`{ kind: 'success', message: '✓ ライブラリに追加しました' }` を表示。
  - 409 `duplicate`(既存だった場合): エラー扱いにせず `['references', revId]` を invalidate(→「ライブラリに有り ✓」へ)。決定。
  - その他エラー: Toast `{ kind: 'error', message: '取り込みに失敗しました' }`+ボタンを元に戻す(P3: 黙って壊れない)。
- **「ライブラリに有り ✓」**(`in_library !== null`): クリックで `router.push('/papers/' + in_library.library_item_id)`(同タブ遷移。決定。理由: 芋づる読みの主動線であり、アプリ内遷移は同タブが慣習)。
- **「arXiv」リンク**: `https://arxiv.org/abs/{arxiv_id}` を新規タブ(`target="_blank" rel="noopener noreferrer"`)。

### 5.7 パネル全体のローディング・空・エラー(決定)

- **ローディング(図表タブ初回)**: 見出し「図表一覧」は即時描画。カード部にスケルトン 3 枚: 各 flex gap10px padding8px、左に 52×38px 矩形(bg `--pr-bg-thumb`、radius 4px)、右にバー 2 本(高さ 10px、幅 88%/40%、bg `--pr-bg-thumb`、radius 3px)。参考文献部にバー 4 本(高さ 12px、幅 96/92/88/90%、gap 8px)。パルスアニメーション(opacity 0.6⇄1.0、1200ms)。
- **空状態**: 図表 0 件(品質 B で抽出失敗等)→ EmptyState(plans/08 §5.21)`{ title: '図表がありません', description: 'この論文からは図表を抽出できませんでした。' }`。参考文献 0 件 → `{ title: '参考文献がありません', description: '参考文献リストを抽出できませんでした。' }`。セクション見出しと区切り線は空でも表示する(構造の安定)。
- **エラー**: クエリ失敗時、該当セクションに EmptyState `{ title: '読み込みに失敗しました', action: { label: '再試行', onClick: refetch } }`。

### 5.8 その他ヘッダ・目次のインタラクション(1a 共有分の参照)

「‹」戻る(`/library` へ)/ ステータスドロップダウン(Popover width 180、6 値、選択で PATCH)/ 表示モードセグメント切替(`?mode=` を `router.replace` で同期 — viewer-shell §3.3)/ スタイルドロップダウン(natural⇄literal、literal 未生成なら plans/03 §7.3 POST)/ 検索 `/` フォーカス / 目次クリックでセクションジャンプ — いずれも仕様は viewer-shell §4・§5・§7(補足は 1a 計画書)に定義。1c では**同一コンポーネントがダークトークンで正しく描画されること**のみが検証対象。

### 5.9 キーボード・フォーカス

- Esc の優先順位(決定): ライトボックス(Modal 自身が処理、plans/08 §5.11)> シェル keymap の順序(選択メニュー → 検索ドロップダウン → Popover → 図表ポップオーバー。viewer-shell §10)。開いている最前面 1 つだけを閉じる。検索 input フォーカス中の Esc はフォーカス解除(viewer-shell §7)。
- FigureRefLink / CitationLink / FigureCard / ReferenceRow は `tabIndex=0` + Enter/Space で発火(`role="button"`。FigureRefLink のみ `aria-expanded` を付与)。focus-visible リングは共通規約(plans/08 §5 共通事項: outline 1.5px `--pr-acc`、offset 1px)。
- ポップオーバー開時にフォーカスをポップオーバー先頭ボタンへ移さない(決定: 読書位置のフォーカスを奪わない。Esc で閉じられることを `aria-haspopup="dialog"` で補償)。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Storybook + Playwright VRT(plans/08 §9)。フィクスチャ: シードデータ(arXiv:2209.03003、spec-decisions C10)+ `data-theme="dark"` + `?mode=parallel&panel=figures` + 図2 ポップオーバー開 + [12] 展開の完全再現ストーリー `Screens/1c-ViewerDarkFigures` を 1440×900 で撮影し、確定デザイン画像と比較する。

- [ ] フレーム全体: bg #181B20、3 ペイン幅 232 / flex:1 / 340px、ヘッダ h52px bg #1E2228 border-bottom #2A2F37
- [ ] ヘッダ 9 要素の寸法・色・逐語(タイトル max-width 330px ellipsis、A バッジ 18×18 rgba(143,174,203,0.14)/#8FAECB、ピル h24 #22262D/#333942、セグメント選択「対訳」#2C313A/#F0EEE9・shadow なし、検索 w150 #14171B、Keycap「/」)
- [ ] 目次: アクティブ項目「2.2 Reflow: 経路の直線化」に rgba(143,174,203,0.14) 面+inset 2px 0 #8FAECB バー+9×11 しおり、節インデント 22px、「参考文献」弱表示 #7A7F87、フッタ 2 項目「今日の読書 42分」/「位置は自動保存」(viewer-shell §5.3。この 1 点のみ確定デザイン画像と意図的に差分)
- [ ] 対訳: カラム見出し 10.5px #7A7F87(「✦ AI翻訳」のみ #8FAECB)、見出し 16.5px #F0EEE9+イタリック原題 13.5px #7A7F87、原文 13.8px/1.72 #C4C7CC Source Serif 4、訳文 14.8px/2.0 #DEDCD7 Noto Serif JP、column-gap 34px row-gap 18px、太字対応保持
- [ ] 図参照: 原文「Figure 2」点線下線 #8FAECB、訳文「図2」アクティブ(bg rgba(143,174,203,0.14)+outline 1px rgba(143,174,203,0.40)+radius 2px)、引用「[29]」#8FAECB weight600 下線なし
- [ ] ポップオーバー: w400、bg #22262D、border #3A404A、radius 10px、shadow 0 22px 52px rgba(8,10,13,0.55)、z-index 5、矢印 9×9 rotate45 top:-5px left:44px、参照段落基準 top:40px/left:-20px、プレースホルダ 170px #191C21/#2E343D「図2(原論文の画像)」、英キャプション italic 10.5px #9BA1A9(--pr-text-sub2)、和キャプション 12px #DEDCD7、ボタン 3 個 h23px(プライマリ bg #8FAECB 文字 #181B20 / セカンダリ border #3A404A #C9CCD1 / AI #8FAECB weight600)、フォントが IBM Plex Sans JP に戻ること
- [ ] タブ行: 「図表」アクティブ(#8FAECB+inset 0 -2px)、非選択 #9BA1A9(--pr-text-sub2)、件数「6」「4」10px #7A7F87
- [ ] 図表カード 3 枚: サムネ 52×38 #191C21/#2E343D、選択中「図2」カード(rgba面+rgba(143,174,203,0.40)枠+#F0EEE9+「(表示中)」)、サブ「§2.2 · p.5」9.5px #7A7F87、区切り線 1px #262B32 margin 6px 0
- [ ] 参考文献 4 行: 番号 IBM Plex Mono #7A7F87、会議名 italic #9BA1A9(--pr-text-sub2)、[12] 展開行(bg #22262D border #333942、「arXiv」#8FAECB、ボタン h22px 2 種)
- [ ] §4.7 の全 UI 文言が一字一句一致(スペース・中点・矢印・✦ 含む)
- [ ] アクセント 4 色切替時、上記のアクセント由来色がすべて `--pr-ad/-ads/-adm` 対応値に追随する(green/purple/terracotta の 3 ストーリー追加)

### 6.2 機能検証

- [ ] 訳文「図2」・原文「Figure 2」いずれのクリックでもポップオーバーがその場に開き、**スクロール位置が 1px も変化しない**(docs/04 §15)
- [ ] ポップオーバー開と同時に図表タブ「図2」カードが選択中+「(表示中)」になり、閉じても選択が残る
- [ ] 「図の位置へ移動 →」で図ブロックへスムーズスクロール+一時ハイライト(1200ms フェード)
- [ ] 「拡大」でライトボックス(スクリム+キャプション対訳)が開き、Esc/スクリム/×で閉じる
- [ ] 「✦ この図を説明」でチャットタブへ切替わり、図ブロックをアンカーにした quick_action=`explain_figure` の SSE 送信が開始される
- [ ] 本文「[29]」クリックで図表タブへ切替+[29] 行が展開表示され、本文はスクロールしない
- [ ] 参考文献行クリックで排他展開(既存展開行は閉じる)
- [ ] 「+ この論文も取り込む」→ 202 → ボタン「取り込み中…」→ ジョブ完了で「ライブラリに有り ✓」+成功 Toast。409 duplicate でも「ライブラリに有り ✓」に収束
- [ ] 「ライブラリに有り ✓」クリックで `/papers/{library_item_id}` へ遷移
- [ ] 「arXiv」リンクが新規タブで `https://arxiv.org/abs/{arxiv_id}` を開く
- [ ] `arxiv_id: null` の文献では取り込みボタン・arXiv リンクが表示されない
- [ ] figures 未着時のクリックでローディングポップオーバー → データ着で内容置換。フェッチ失敗時「再試行」導線
- [ ] 図表 0 件 / 参考文献 0 件で EmptyState 文言(§5.7)が表示される
- [ ] `?mode=parallel&panel=figures` で開くと対訳モード+図表タブで初期化され、`panel` は消費後 URL から除去される。以後のリロードではタブが sessionStorage から復元される(viewer-shell §3.1・§6.3)
- [ ] Esc 優先順位: ライトボックス > (選択メニュー → 検索ドロップダウン → Popover → )図表ポップオーバー > 検索フォーカス解除(§5.9 / viewer-shell §10)
- [ ] キーボードのみで 参照オープン → 3 ボタン操作 → 文献展開 → 取り込み まで到達できる(focus-visible リング表示)
- [ ] ダーク⇄ライト切替(`data-theme` 書き換えのみ)で本画面の全要素が再レンダリングなしに追随し、`dark:` 分岐コードが存在しない(コードレビュー基準)
- [ ] 翻訳ジョブ SSE の progress で目次ヘッダ「翻訳 N%」が更新される
