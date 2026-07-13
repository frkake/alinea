# 09-screens/viewer-shell. ビューアシェル共通実装仕様(1a/1b/1c/2a/1h/5a 共有)

> 対象読者と前提: 本書は「Alinea — 論文読解ワークベンチ」のビューア6画面(1a 対訳 / 1b 訳文 / 1c ダーク・図表 / 2a PDF / 1h 記事 / 5a リソース)が共有する「ビューアシェル」の実装計画書である。機能仕様は docs/04-viewer.md を正とし、ピクセル値は抽出ファイル(extract/1a.md・1b.md・1c.md・2a.md・1h.md・5a.md)の実測値をそのまま採用する。共通 UI コンポーネントとデザイントークンは plans/08-design-system.md、API は plans/03-api.md の定義を参照し、本書では再定義しない。技術スタックは確定済み(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4 / TanStack Query + Zustand)。デザイン抽出値と docs に無い実装詳細は本書がすべて確定させる(「決定:」表記)。

## 1. 対象範囲とコンポーネント構成

### 1.1 シェルの責務(本書が確定するもの)

ビューア6画面はすべて次の共通骨格を持つ。この骨格=「ビューアシェル」を単一実装で共有する。

```
┌──────────────────────────────────────────────────────────────┐
│ ViewerHeader h=52px(§4)                                      │
├────────┬──────────────────────────────────┬──────────────────┤
│ TocRail │ 本文ペイン(children。モード別に    │ SidePanel        │
│ 44px ⇄ │ 各画面ファイルが実装)              │ 340px/320px(§6) │
│ TocPane │                                  │ 排他6タブ         │
│ 232px  │                                  │(開閉可)          │
│ (§5)   │                                  │                  │
└────────┴──────────────────────────────────┴──────────────────┘
```

シェルが所有するもの: ヘッダ全要素(§4)/ 左レール⇄目次ペイン(§5)/ サイドパネルの枠・タブ・幅・排他制御(§6)/ 表示モードルーティング(§3)/ 論文内検索(§7)/ 読書位置自動保存と読書時間計測(§8)/ キーボードショートカット(§10)/ viewer-store(§2.3)。

シェルが所有しないもの(各画面ファイル担当。分担の完全表は §11): 本文ペインの中身、サイドパネル各タブの本体、選択メニュー、対訳ポップ、図表参照ポップオーバー、前回位置バナー、読了フローモーダル。

### 1.2 ファイル構成(apps/web)

```
apps/web/src/
├── app/(app)/papers/[itemId]/
│   └── page.tsx                       # ルート。クエリ正規化(§3)+ ViewerShell 合成
├── components/viewer/
│   ├── ViewerShell.tsx                # 本書 §1.3
│   ├── ViewerHeader.tsx               # §4
│   ├── StyleSelector.tsx              # §4.2-7
│   ├── ViewerOverflowMenu.tsx         # §4.2-9
│   ├── InPaperSearch.tsx              # §7
│   ├── toc/
│   │   ├── TocRail.tsx                # §5.2(44px 折畳レール)
│   │   ├── TocPane.tsx                # §5.3(232px 目次ペイン)
│   │   └── TocRow.tsx
│   ├── panel/
│   │   ├── SidePanel.tsx              # §6(枠・幅・開閉)
│   │   └──(タブ本体 6 種は各画面ファイル担当。§11)
│   └── article/ArticleRegenerateButton.tsx  # 1h 担当所有。シェルは配置のみ(§4.3)
├── hooks/
│   ├── useViewerBootstrap.ts          # §2.2
│   ├── useViewerKeymap.ts             # §10
│   ├── useReadingPosition.ts          # §8.1
│   └── useReadingSession.ts           # §8.2
└── stores/viewer-store.ts             # §2.3
```

### 1.3 ViewerShell の props(完全形)

```tsx
// apps/web/src/components/viewer/ViewerShell.tsx
import type { SidePanelTabId } from '@/components/ui/SidePanelTabs';

export type ViewerMode = 'translation' | 'parallel' | 'source' | 'pdf' | 'article';
// LastPosition.mode(plans/03-api §1.7)と同一トークン。URL クエリ ?mode= の値でもある(§3)

export interface ViewerShellProps {
  itemId: string;                  // li_…(LibraryItem ID)
  mode: ViewerMode;                // page.tsx がクエリ正規化後に渡す(§3.2)
  children: React.ReactNode;       // 本文ペイン(モード別コンポーネント。§11)
  leftPane?: React.ReactNode;      // 2a 専用: 目次/ページ切替サイドバー。指定時は
                                   // TocRail/TocPane を描画せず常時 232px 展開でこれを描画(§5.5)
}
```

- サイドパネルのタブ本体 6 種(`ChatTab` / `NotesTab` / `AnnotationsTab` / `FiguresTab` / `ResourcesTab` / `InfoTab`)は props で受けず、`SidePanel.tsx` が直接 import する。決定。理由: 6 タブは全モード共通で差し替えが存在しないため、合成点を増やさない。
- モード別の本文ペインコンポーネント(children に入るもの): `TranslationPane`(訳文)/ `BilingualPane`(対訳)/ `SourcePane`(原文)/ `PdfPane`(PDF)/ `ArticlePane`(記事)。いずれも `components/viewer/` 配下、実装は各画面ファイル担当。

### 1.4 フレームとレイアウトの確定値

- ルート: `display:flex; flex-direction:column; height:100dvh; background:var(--pr-bg-app); color:var(--pr-text)`。デザインの 1440×900 フレーム(border・radius・shadow)はキャンバス表現であり実アプリでは描かない(plans/08 §7.1)。最小幅 1200px・1440px 超の伸長規則は plans/08 §7.2 に従う。
- ヘッダ下の本体行: `flex:1; display:flex; min-height:0`(1a 実測)。
- 3 ペインの幅: 左=44px(レール)または 232px(ペイン)固定、本文=`flex:1; min-width:0`、右=340px または 320px 固定(§6.2)。パネル閉時は右 0px(非描画)。

## 2. データ取得と状態管理

### 2.1 使用 API(すべて plans/03-api 定義)

| 用途 | エンドポイント |
|---|---|
| ビューア初期化(書誌・目次・翻訳進捗・タブ件数・前回位置・今日の読書分) | `GET /api/library-items/{id}/viewer`(03-api §6.1) |
| ステータス変更 | `PATCH /api/library-items/{id}`(§5.4) |
| 直訳セットのオンデマンド生成(スタイル切替時) | `POST /api/revisions/{revision_id}/translations`(§7.3) |
| 論文内検索 | `GET /api/revisions/{revision_id}/search`(§6.7) |
| 読書位置自動保存 | `PUT /api/library-items/{id}/position`(§5.8) |
| 読書時間計測 | `POST /api/library-items/{id}/reading-sessions`(§5.9) |
| 付録のオンデマンド翻訳(目次から) | `POST /api/translation-sets/{set_id}/sections/{section_id}/translate`(§7.5) |

### 2.2 TanStack Query キー(シェル使用分。命名確定)

```ts
['viewer', itemId]                                   // GET …/viewer。staleTime 30_000ms
['in-paper-search', revisionId, query]               // GET …/search。enabled: query.length >= 2。staleTime 30_000ms
```

- `['viewer', itemId]` は SSE `translation.unit_completed` / `job.progress` / `job.failed` イベント受信時に `invalidateQueries`(plans/01 §5 のイベント型定義)。SSE 不通時のポーリングフォールバックは plans/01 §5 の既定(翻訳状態 5,000ms)。
- 404 は Next.js `notFound()`。パイプライン処理中(`translation.status === 'pending'`)でもシェルは描画し、本文ペイン側が部分読書 UI を出す(各画面ファイル担当)。

### 2.3 stores/viewer-store.ts(Zustand。完全形)

```ts
// apps/web/src/stores/viewer-store.ts
import { create } from 'zustand';
import type { SidePanelTabId } from '@/components/ui/SidePanelTabs';
import type { Preset } from '@/components/viewer/article/types';

export type TranslationStyle = 'natural' | 'literal';
export type PendingScrollTarget =
  | { kind: 'block'; blockId: string }
  | { kind: 'section'; sectionId: string }
  | null;

export interface ViewerSelection {
  blockId: string;
  side: 'source' | 'translation';
  quote: string;
  start: number | null;
  end: number | null;
  rect: { top: number; left: number; bottom: number; right: number };
  sourceFullText?: string;
}

interface ViewerStoreState {
  itemId: string | null;
  revisionId: string | null;

  // 目次(§5)
  tocOpen: boolean;                       // true=232pxペイン / false=44pxレール
  activeSectionId: string | null;         // 現在位置ハイライト(スクロール連動)

  // サイドパネル(§6)
  panelOpen: boolean;
  activeTab: SidePanelTabId;              // 'chat'|'notes'|'annotations'|'figures'|'resources'|'info'

  // 翻訳スタイル・直訳生成(§4.4)
  style: TranslationStyle;
  literalStatus: 'unknown' | 'generating' | 'ready';
  literalJobId: string | null;
  literalSetId: string | null;

  // 読書位置(§8)・モード間の位置引き継ぎ(§3.4)
  currentBlockId: string | null;          // 先頭可視ブロック
  pendingScrollTarget: PendingScrollTarget;

  // 論文内検索(§7)
  searchOpen: boolean;
  searchQuery: string;

  // キーボード操作シグナル(§10。0 起点で +1)
  bilingualPopToggleSignal: number;
  bookmarkToggleSignal: number;

  // 記事の再生成状態(1h §5.3)
  articleRegenerating: boolean;
  articleRegenProgressPct: number;
  activeArticlePreset: Preset | null;

  // テキスト選択(選択メニュー。null=非表示)
  selection: ViewerSelection | null;

  // 検索・深リンクの一発消費ターゲット
  pendingAnnotationId: string | null;
  pendingNoteId: string | null;
  pendingReferenceId: string | null;
  pendingHighlightQuery: string | null;
  pendingChatThreadId: string | null;
  pendingChatMessageId: string | null;

  // actions
  initViewer(itemId: string, revisionId: string): void;
  setTocOpen(open: boolean): void;
  setPanel(open: boolean, tab?: SidePanelTabId): void;
  setStyle(style: TranslationStyle): void;
  setLiteralGeneration(state: {
    status: 'unknown' | 'generating' | 'ready';
    jobId?: string | null;
    setId?: string | null;
  }): void;
  setCurrentBlock(blockId: string, sectionId: string): void;
  setArticleRegenState(state: { regenerating: boolean; progressPct?: number }): void;
  setActiveArticlePreset(preset: Preset): void;
  requestScroll(target: PendingScrollTarget): void;
  consumeScroll(): void;
  openSearch(query?: string): void;
  closeSearch(): void;
  setSearchQuery(query: string): void;
  toggleBilingualPop(): void;
  toggleBookmark(): void;
  setSelection(selection: ViewerSelection | null): void;
  requestAnnotationFocus(annotationId: string): void;
  consumeAnnotationFocus(): void;
  requestNoteFocus(noteId: string): void;
  consumeNoteFocus(): void;
  requestReferenceFocus(refId: string): void;
  consumeReferenceFocus(): void;
  setPendingHighlightQuery(query: string | null): void;
  requestChatFocus(target: { threadId?: string | null; messageId?: string | null }): void;
  consumeChatFocus(): void;
}
```

- ストアは論文単位に 1 つ(ページマウントで `initViewer` を呼ぶ)。**永続化(決定)**: `tocOpen` は `localStorage['alinea-toc-open:{itemId}']`(値: `"1"` / `"0"`)、`activeTab`・`panelOpen` は `sessionStorage['alinea-viewer-panel:{itemId}']`(値: `"chat"` 等 / `"closed"`)、`style` は `localStorage['alinea-viewer-style:{itemId}']`、`activeArticlePreset` は `localStorage['alinea-article-preset:{itemId}']`。理由: 目次開閉・書体系・記事プリセットは再訪でも維持し、タブはセッション内文脈とする。
- `mode` は URL クエリが正でストアには持たない。`block` / `section` / `panel` / `hl` / `annotation` / `note` / `thread` / `message` の補助クエリはページで一度だけ読み、対応するスクロール・深リンクの一発消費状態へ移して URL から除去する(§3)。

## 3. 表示モードのルーティング(URL 契約)

### 3.1 ルートとクエリ(確定)

- ページルート: `/papers/{itemId}`(`apps/web/src/app/(app)/papers/[itemId]/page.tsx`)。
- **表示モードは URL クエリ `?mode=` に決定**。値域は `translation` / `parallel` / `source` / `pdf` / `article` の 5 値(`ViewerMode`。`LastPosition.mode` と同一トークン)。
- 補助クエリ(いずれも任意。**初期化時に 1 回消費し、`router.replace` で URL から除去する**。決定):

| クエリ | 値 | 用途 |
|---|---|---|
| `block` | `blk-…` | 深リンク位置。横断検索 4e「該当位置へ」、PDF⇄訳文相互リンク、根拠ジャンプの外部遷移 |
| `section` | `sec-…` | セクション深リンク(目次共有・「続きから」外部導線) |
| `panel` | `chat` / `notes` / `annotations` / `figures` / `resources` / `info` | 初期アクティブタブ+パネルを開く。4e「スレッドを開く」= `?panel=chat&thread=th_…`(`thread` の消費は ChatTab 担当) |
| `hl` | 検索語 | 遷移先ブロックだけを一時マークする |
| `annotation` / `note` | `ann_…` / `note_…` | 対応タブの該当カードを一度だけフォーカスする |
| `thread` / `message` | `th_…` / `msg_…` | ChatPanel が該当スレッドを選択し、メッセージへスクロールする |

### 3.2 正規化規則(page.tsx。決定)

1. `mode` が 5 値のいずれでもない(欠落含む)場合: `viewer` ブートストラップの `last_position.mode`、それも無ければ `translation` へ `router.replace`(履歴を汚さない)。
2. `mode=pdf` かつ PDF アセットが無い論文(品質 A で原文 PDF 未取得)は `translation` へ `router.replace` でフォールバックする。決定: Toast は出さない(2a §9 の「黙って壊さず遷移」と同一挙動。PDF なしはヘッダの PDF セグメント disabled+tooltip で伝える)。PDF 有無の判定は `GET /api/papers/{paper_id}/pdf` の 404(2a と同一判定。結果はページ内でメモ化し再問い合わせしない)。
3. `block` / `section` 指定時は `pendingScrollTarget` にセットし前回位置バナーを抑止(1b 担当側が store を参照)。

### 3.3 モード切替の遷移(確定)

- ヘッダのセグメント(§4.2-6)クリックで `router.replace('/papers/{itemId}?mode={next}', { scroll: false })`。**push ではなく replace に決定**。理由: モード切替は同一文書の別ビューであり、ブラウザバックは「ライブラリへ戻る」であるべき(1 論文内でモードを何度も切り替えても履歴が伸びない)。
- キー `m` はセグメント順(訳文→対訳→原文→PDF→記事→訳文)で循環(§10)。

### 3.4 モード間の位置保持(確定)

- 切替時、`viewer-store.currentBlockId`(先頭可視ブロック)を `pendingScrollTarget: { kind:'block', blockId }` に積む。遷移先ペインはマウント時にこれを消費し、対応位置へ即時スクロール(アニメーションなし)する。
- PDF モードとの対応は block の `page`+`bbox`(03-api §6.3)を用いる。`bbox` を持たないブロック(品質 A の一部)は所属セクション先頭ページへフォールバック。逆方向(PDF→構造化)は現在ページの `≒ §` 対応先セクション先頭(2a 担当)。
- 記事モードは原文ブロックと 1:1 対応を持たないため、`currentBlockId` の所属セクションに対応する記事ブロック(根拠チップが同セクションを指す最初のブロック)へ、無ければ記事先頭へ。決定。

## 4. ヘッダ完全仕様(ViewerHeader)

### 4.1 コンテナ

`height:52px; flex:none; background:var(--pr-bg-card); border-bottom:1px solid var(--pr-border-header); display:flex; align-items:center; gap:10px; padding:0 16px`(1a/1b/1c/2a/1h/5a 全画面で同一実測)。

### 4.2 要素(左から。全モード共通部)

1. **戻る「‹」**: テキストグリフ、font-size:16px、color:`var(--pr-text-icon)`、width:20px、text-align:center。クリックで `router.back()`、履歴が無い(直接遷移)場合は `router.push('/library')`。決定: 「履歴が無い」判定は `window.history.length <= 1`(新規タブ・深リンク直開き)。`aria-label="戻る"`。
2. **論文タイトル**: `library_item.paper.title`。font-size:13px、font-weight:600、max-width:330px、white-space:nowrap、overflow:hidden、text-overflow:ellipsis。`title` 属性に全文。クリック動作なし。
3. **品質バッジ**: `QualityBadge`(plans/08 §5.3)size 18。A=`title="品質レベルA: LaTeXソースから完全構造化"` / B=`title="品質レベルB: PDF由来"`。
4. **ステータスピル**: `StatusPill`(plans/08 §5.2)`size='md' variant='pill' interactive`。ドット 7×7px=`STATUS_COLORS[status]`、ラベルは `Status` の日本語(planned=読む予定 / up_next=すぐ読む / reading=読んでいる / done=読んだ / reread=あとで再読 / on_hold=保留)、末尾「▾」9px muted。クリックで Popover(width 180px、placement `bottom-start`)に 6 値リスト(各行 h30px、padding 0 12px、font 11.5px、ドット 7×7px+ラベル、現在値は bg `var(--pr-acc-s)` weight 600)。選択で `PATCH /api/library-items/{id} { status }` を楽観更新(失敗時ロールバック+Toast `error`)。**`done` を選択した場合は PATCH 送信前に読了フローモーダル(1g)を開き、モーダルの「保存」/「すべてスキップ」が PATCH を発行する**。決定。理由: 1g は読了時の必須経由点(docs/06 §3)。モーダル実装は 1g 担当。
5. **スペーサー**: `flex:1`。
6. **表示モード切替**: `SegmentedControl`(plans/08 §5.1)`size='md'`(トラック bg `var(--pr-bg-muted)` radius 7px padding 2px gap 2px / セグメント h24px padding 0 11px radius 5px font 11.5px)。options 固定:

```ts
const MODE_OPTIONS = [
  { value: 'translation', label: '訳文' },
  { value: 'parallel',    label: '対訳' },
  { value: 'source',      label: '原文' },
  { value: 'pdf',         label: 'PDF' },
  { value: 'article',     label: '記事' },
] as const satisfies ReadonlyArray<{ value: ViewerMode; label: string }>;
```

   選択中: bg `var(--pr-bg-seg-selected)`、color `var(--pr-text)`、weight 600、shadow `var(--pr-shadow-seg)`(ダークは shadow なし)。非選択: color `var(--pr-text-sub)`。`ariaLabel="表示モード"`。
7. **スタイルセレクタ / ✦指示つき再生成**(§4.3 の差し替え規則): §4.4。
8. **論文内検索ボックス**: `SearchBox`(plans/08 §5.13)`variant='in-paper'`(w150px、h26px、radius 6px、padding 0 10px、bg `var(--pr-bg-inset)`、font 11.5px、placeholder「この論文内を検索」、先頭 `MagnifierIcon` 11×11、右端 `Keycap`「/」)。クリックまたは `/` で §7 の検索状態へ。
9. **オーバーフローメニュー「⋯」**: font-size:15px、color:`var(--pr-text-sub)`、letter-spacing:1px、`aria-label="その他"`。クリックで Popover(width 200px、placement `bottom-end`、caret なし)。**メニュー項目(決定。デザイン未描画のため本書で確定)**、各行 h30px・padding 0 12px・font 11.5px・color `var(--pr-text-mid)`、区切りは 1px `var(--pr-border-hair)`:
   1. 「サイドパネルを表示」/「サイドパネルを隠す」(`panelOpen` トグル。§6.4)
   2. 「注釈 Markdown ⤓」(`GET /api/library-items/{id}/export/annotations`。plans/03-api §18。情報タブと同機能)
   3. 「原文 PDF ⤓」(`GET /api/papers/{paper_id}/pdf`。PDF なしの論文(§3.2-2 と同じ 404 判定)では非表示)
   4. 「再取り込み」(`POST /api/papers/{paper_id}/reingest`。確認 Popover なしで即時発行+Toast `info`「再取り込みを開始しました」。決定)
   5. 「処理ログ」(情報タブを開きタイムライン位置へ。`setPanel(true,'info')`)

   理由: いずれも情報タブ(2a)に存在する機能の再掲であり、新機能を発明しない。

### 4.3 記事モード差し替え規則(確定)

- **`mode === 'article'` のとき、7 番スロット(スタイルセレクタの位置)を「✦ 指示つき再生成」ボタンに差し替える**(1h 実測)。他モード(translation / parallel / source / pdf)ではスタイルセレクタを表示する(1a/1b/1c/2a/5a 実測)。差し替えはこの 1 スロットのみで、他の 8 要素は全モード不変。
- ボタン仕様(1h 実測): inline-flex、gap:5px、height:26px、padding:0 10px、border:1px solid `var(--pr-border-control)`、border-radius:6px、font-size:11.5px、color:`var(--pr-acc)`、font-weight:600。文言「✦ 指示つき再生成」(✦=`AiMark`)。
- ボタン実体は `components/viewer/article/ArticleRegenerateButton.tsx`(クリック後の指示入力ポップオーバーと `POST /api/articles/... regenerate` 呼び出しを含む)で **1h 担当が実装**。シェルは「この位置にこのコンポーネントを置く」ことだけを持つ。

### 4.4 スタイルセレクタ(StyleSelector)

- 見た目(1a 実測): inline-flex、gap:5px、height:26px、padding:0 10px、border:1px solid `var(--pr-border-control)`、border-radius:6px、font-size:11.5px、color:`var(--pr-text-mid)`。表示は「スタイル: 自然訳」+「▾」(color `var(--pr-text-muted)`、font-size:9px)。ラベル対応: `natural`=自然訳 / `literal`=直訳。
- クリックで Popover(width 180px、`bottom-end`)に 2 行(自然訳 / 直訳。現在値はアクセント淡背景+weight 600)。
- 切替時の挙動(決定):
  1. `viewer-store.style` を更新+`localStorage['alinea-viewer-style:{itemId}']` へ保存。
  2. 切替先が `literal` で、`GET /api/revisions/{revision_id}/translations` の一覧に literal の TranslationSet が無い、または `status !== 'complete'` の場合、`POST /api/revisions/{revision_id}/translations { style: 'literal', priority_section_id: <activeSectionId> }` を発行(202)。決定: オンデマンド生成が存在するのは literal のみ(03-api §7.3 の Request は `style: "literal"` 固定)。`natural` への切替で natural セットが未 complete の場合は POST を発行しない(パイプラインが生成中であり、進捗は SSE で追随する)。いずれも未翻訳ブロックは本文ペインが原文+「翻訳中…」で表示(P3。表示は各ペイン担当)。
  3. 本文ペインの翻訳クエリはキーに `style` を含むため自動で再取得される。
- `mode === 'source'` と `mode === 'pdf'` でもセレクタは表示・操作可能(2a 実測でセレクタが存在する)。原文/PDF 表示自体には作用せず、対訳ポップ・相互リンク先の訳文スタイルを規定する。

## 5. 左レール(44px)⇄ 目次ペイン(232px)

### 5.1 開閉状態と既定(確定)

- 2 状態: **展開**=`TocPane`(w232px)/ **折畳**=`TocRail`(w44px)。トグルは ペインヘッダの「⟨⟨」(折畳)とレールの「☰」(展開)。
- モード別の既定(docs/04 §1。ユーザー未操作時):

| モード | 既定 |
|---|---|
| 対訳(parallel) | 展開(1a/1c) |
| PDF(pdf) | 展開(2a。ただし §5.5 の leftPane 差し替え) |
| 訳文(translation) | 折畳(1b/5a) |
| 記事(article) | 折畳(1h) |
| 原文(source) | 折畳。決定。理由: 訳文と同じ 1 カラム読書レイアウトのため(デザイン未提示の補完) |

- ユーザーが明示的にトグルした場合は `localStorage['alinea-toc-open:{itemId}']` に保存し、以後そのアイテムでは**全モードでその値を既定より優先**する。決定。理由: 「読書の邪魔をしない」— モード切替のたびに開閉が勝手に変わらないこと(P6)。

### 5.2 TocRail(44px 折畳レール。1b/1h/5a 実測)

- コンテナ: width:44px、flex:none、background:`var(--pr-bg-pane)`、border-right:1px solid `var(--pr-border-pane)`、flex 縦、align-items:center、padding:12px 0、gap:14px。
- アイコン 3 つ(上から):
  1. 「☰」: font-size:13px、color:`var(--pr-text-sub)`。クリック→`setTocOpen(true)`。`aria-label="目次を開く"`。
  2. `BookmarkIcon` 10×12: color:`var(--pr-text-muted)`。クリック→`onToggle(true)` で目次を開く(`aria-label="ブックマーク"`)。ブックマーク絞り込み状態は持たない。
  3. `MagnifierIcon` 12×12: color:`var(--pr-text-muted)`。クリック→ヘッダ検索へフォーカス(キー `/` と同一動作)。

### 5.3 TocPane(232px 目次ペイン。1a 実測)

- コンテナ: width:232px、flex:none、background:`var(--pr-bg-pane)`、border-right:1px solid `var(--pr-border-pane)`、flex 縦、padding:10px 8px 8px。
- **ヘッダ行**(padding:0 8px 8px、flex、space-between): 左「目次」(font 11px/600、color `var(--pr-text-icon)`)/ 右「翻訳 {progress_pct}%」(font 10.5px、color `var(--pr-text-icon)`。`viewer.translation.progress_pct` を四捨五入整数)+「⟨⟨」(margin-left:6px、color `var(--pr-text-faint)`。クリックで折畳、`aria-label="目次を折りたたむ"`)。
- **目次リスト**(flex 縦、gap:1px、font-size:12.3px、color `var(--pr-text-nav)`、flex:1、overflow-y:auto): データは `viewer.toc: TocNode[]`(2 階層)。各行 `TocRow`:
  - 共通: flex、align-items:center、gap:6px、padding:4px 8px、border-radius:5px。サブ節(children 内)は padding:4px 8px 4px 22px。ラベルは「{number} {title_ja}」、`title_ja` が null の節は `title_en` を表示。
  - **現在位置(activeSectionId 一致)**: background:`var(--pr-acc-s)`、color:`var(--pr-acc)`、font-weight:600、box-shadow:`inset 2px 0 var(--pr-acc)`。
  - 行末の付加要素(順序: 注釈数 → ブックマーク → ✓):
    - 翻訳済み「✓」(`translated===true`): font-size:10px、color:#7E9C88(固定値。plans/08 §6.2 の完了グリフ色)。
    - 注釈数バッジ(`annotation_count > 0`): `CountBadge` variant `annotation`(min-width:15px、h15px、radius 8px、bg `var(--pr-ann-important-count-bg)`、color `var(--pr-ann-important-chip-fg)`、font 9.5px/600)。
    - ブックマーク(`bookmarked===true`): `BookmarkIcon` 9×11、color:`var(--pr-acc)`。
  - 分母外セクション(`in_progress_denominator===false`。参考文献等): color:`var(--pr-text-icon)`、付加要素なし。
  - クリック: 該当節先頭へスクロール(`requestScroll({kind:'section', sectionId})`)。
- **未翻訳付録ボックス**(`on_demand===true` の節。リスト末尾): margin:8px 6px 0、border:1px dashed `var(--pr-border-dashed)`、border-radius:6px、padding:8px 9px、flex 縦、gap:5px。1 行目「{number} {title_ja} 」(font 11.5px、`var(--pr-text-sub)`)+「— 未翻訳」(`var(--pr-text-muted)`)。2 行目「開くと翻訳します(オンデマンド)」(font 10.5px、`var(--pr-text-muted)`、line-height:1.5)。クリックで該当節へスクロール+`POST /api/translation-sets/{set_id}/sections/{section_id}/translate` を発行(`set_id` = `viewer.translation.set_id`。202。多重クリックはジョブ進行中なら再送しない)。
- **ペインフッタ**(padding:8px 8px 2px、border-top:1px solid `var(--pr-border-pane)`、flex、space-between、font-size:10.5px、color:`var(--pr-text-muted)`): 左「今日の読書 {today_reading_minutes}分」/ 右「位置は自動保存」。決定: ダークモード(1c)は「今日の読書 42分 · 自動保存」の 1 行連結で描かれているが、**両テーマとも 1a の 2 項目レイアウトに統一**する。理由: 同一情報の簡略描画差であり、テーマでレイアウトを分岐させない(plans/08 §8.3 の規約)。`reading.track_reading_time=false` のユーザーでは左項目を非表示にし右項目のみ。

### 5.4 現在位置の追従(activeSectionId)

本文ペインは IntersectionObserver で先頭可視ブロックを検出し `setCurrentBlock(blockId, sectionId)` を呼ぶ(実装は各ペイン担当、契約は本書)。`activeSectionId` の変化で TocRow のハイライトが移る。目次クリック起因のスクロール中(600ms)は追従を抑止し、クリック先を即座にアクティブ化する。決定。

### 5.5 PDF モードの左サイドバー(leftPane 差し替え)

- 2a では左サイドバーが「目次 / ページ」セグメント+ページサムネイルを持つ(w232px、padding:10px 8px 8px、フッタ「24 ページ · 4.1 MB」「⤓ 原文PDF」)。この中身は **2a 担当**が `PdfSidebar` として実装し、`ViewerShell` に `leftPane` prop で渡す。
- `leftPane` 指定時のシェル挙動(決定): 44px レールへの折畳は提供しない(2a に折畳 UI が存在しないため常時 232px)。「目次」セグメント選択時のツリー描画には `TocPane` の内部リスト(`TocRow` 群)を再利用する(export して 2a 側が組み込む)。

## 6. サイドパネル(排他6タブ)

### 6.1 タブ行

`SidePanelTabs`(plans/08 §5.16)をそのまま使用。タブ ID とラベル(固定): `chat`=チャット / `notes`=メモ / `annotations`=注釈 / `figures`=図表 / `resources`=リソース / `info`=情報。アクティブ: weight 600、color `var(--pr-acc)`、`box-shadow: inset 0 -2px var(--pr-acc)`。非アクティブ: color `var(--pr-text-sub2)`。

**件数バッジ(確定)**: `counts` は `viewer.counts` から `annotations` と `resources` の 2 タブのみ渡す(1a/1b/1c/2a/5a 全画面で件数表示はこの 2 タブのみ)。0 件のときはバッジ非表示。チャット/メモ/図表/情報には件数を出さない。注釈作成・リソース追加の成功時は `['viewer', itemId]` を invalidate してバッジを更新する。

### 6.2 幅の規則(確定)

- **既定 340px**(1a 対訳=チャット、1c=図表、2a=情報、5a=リソースの全実測)。
- **例外: `activeTab === 'annotations'` のときのみ 320px**(1b 注釈パネルの実測)。タブを注釈から他へ移すと 340px に戻る。
- **画面別仕様が優先**: 各画面ファイルがこの規則と異なる幅を明記した場合はそちらが正(現時点で例外は上記 1b の注釈タブのみ)。
- 本文カラム幅との連動(訳文モード): パネル 320px 時=本文カラム 720px(1b)、パネル 340px 時=680px(5a)。この対応は `TranslationPane` 側で `panelWidth` を参照して切り替える(docs/04 §3 と一致)。対訳・PDF・記事は本文が flex 伸縮/固定 760px のため連動しない。
- パネルコンテナ: `width:{340|320}px; flex:none; background:var(--pr-bg-card); border-left:1px solid var(--pr-border-pane); display:flex; flex-direction:column; min-height:0`。幅変更はアニメーションなし(即時)。決定。

### 6.3 排他制御

- 常に 1 タブのみアクティブ(`activeTab`)。タブ本体は `flex:1; min-height:0` 領域に**アクティブ分のみマウント**する。決定。ただし `ChatTab` のみ非アクティブ時もアンマウントしない(`display:none` 退避)。理由: SSE ストリーミング中の回答生成をタブ切替で中断させないため。他 5 タブ(notes / annotations / figures / resources / info)は再マウント+クエリキャッシュで十分。
- 初期タブ: `?panel=` クエリ(§3.1)>`sessionStorage` 保存値 > 既定 `'chat'`。決定(デザイン各画面のアクティブタブはモック上の提示であり、モード連動はさせない)。

### 6.4 開閉(確定)

- `panelOpen=false` でパネル全体(タブ行含む)を非描画。既定: 記事モード初回=閉(1h 実測はパネルなし)、他モード=開。`sessionStorage` 保存値があればそれを優先。モード切替では変更しない(記事へ移っても開いていれば開いたまま。1h の「閉」はあくまで初回既定)。決定。
- 開閉手段: (1) アクティブタブの再クリックで閉じる、(2) ⋯メニュー「サイドパネルを表示/隠す」、(3) キー `c`(閉状態から `chat` で開く。§10)。閉→開はタブ行ごと再表示し直前のアクティブタブを復元。

### 6.5 タブ本体の所有(契約のみ)

| タブ | コンポーネント | 実装担当 |
|---|---|---|
| chat | `components/viewer/panel/ChatTab.tsx` | 1a 画面ファイル(仕様は docs/05) |
| notes | `components/viewer/panel/NotesTab.tsx` | 1a 画面ファイル(docs/05 §メモ) |
| annotations | `components/viewer/panel/AnnotationsTab.tsx` | 1b 画面ファイル(docs/04 §9) |
| figures | `components/viewer/panel/FiguresTab.tsx`(図表一覧+参考文献) | 1c 画面ファイル(docs/04 §10.2) |
| resources | `components/viewer/panel/ResourcesTab.tsx` | 5a 画面ファイル(docs/12) |
| info | `components/viewer/panel/InfoTab.tsx` | 2a 画面ファイル(docs/04 §10.3) |

各タブ本体は props を受けず、`useViewerStore()` と `useParams()` から文脈(itemId / revisionId / mode)を取得する。決定。

## 7. 論文内検索(InPaperSearch)

- 起動: 検索ボックスクリックまたはキー `/`。`searchOpen=true` でボックスが実 input 化しフォーカス(見た目は不変。フォーカスリングは plans/08 §5 共通規約)。
- クエリ 2 文字以上で `GET /api/revisions/{revision_id}/search?q=&limit=50` を 300ms デバウンス実行。対象は原文・訳文の両面、訳文ヒットは原文ブロックと同一視して 1 件(API 仕様)。
- **結果ドロップダウン(決定。デザイン未描画のため本書で確定)**: `Popover`(width 300px、placement `bottom-end`、caret なし、アンカー=検索ボックス)。各行: padding 8px 12px、border-bottom 1px `var(--pr-border-hair)`。1 行目=`display`(「§2.2 ¶3」。font 10px、color `var(--pr-text-muted)`)、2 行目=`snippet`(font 11.5px、color `var(--pr-text-body)`、2 行 clamp。`<mark>` は `.alinea-search-hit`=bg rgba(196,148,50,0.30)、plans/08 §5.17)。アクティブ行は `InPaperSearch` のローカル `activeIndex` で bg `var(--pr-bg-hover)`。ヒット 0 件は `EmptyState` 縮小版「一致なし」(font 11px、muted、padding 16px)。
- キー操作: `↓`/`↑` で行移動、`Enter` でアクティブ行の `block_id` へジャンプ(`requestScroll`)+ドロップダウンは開いたまま(連続ジャンプ可)、`Esc` で `closeSearch()`(input blur+ドロップダウン閉+クエリ保持)。
- **目次マーカー(docs/04 §12 の決定を実装)**: 検索結果が開いている間、ヒットを含む節の `TocRow` 行末に 5×5px の丸ドット(background:`var(--pr-amber)`、margin-left:auto)を表示する。`closeSearch()` で消える。
- PDF モードでは同一 UI でヒット先を page+bbox 位置へジャンプ(bbox 無しは節先頭ページ)。記事モードではヒット先が原文位置のため、`Enter` で `?mode=translation&block=…` へ遷移する(記事本文自体は検索対象外。横断検索 4e が担当)。決定。

## 8. 読書位置と読書時間(シェル所有フック)

### 8.1 useReadingPosition

- `currentBlockId` の変化を 5,000ms デバウンスで `PUT /api/library-items/{id}/position { revision_id, block_id, mode }` に送る(03-api §5.8)。`pagehide` 時は `navigator.sendBeacon` で即時送信。
- `mode` はその時点の `ViewerMode` をそのまま送る(モード別に位置を分けない。最新 1 件が正)。

### 8.2 useReadingSession

- マウント時に `client_session_id = crypto.randomUUID()` を生成。**アクティブ判定(決定)**: `document.visibilityState==='visible'` かつ直近 60 秒以内に pointer / key / scroll イベントあり。アクティブ秒数を 1 秒粒度で加算。
- 送信: 60,000ms ごと+`visibilitychange`(hidden)+`pagehide`(sendBeacon)で `POST /api/library-items/{id}/reading-sessions`(冪等 upsert)。レスポンスの `today_reading_minutes` で目次フッタ表示を更新する(`['viewer', itemId]` のキャッシュを `setQueryData` で部分更新。決定)。
- 設定 `reading.track_reading_time=false`(`GET /api/settings`)なら計測・送信とも行わない。

## 9. ダークモードでのシェルの色差し替え

シェルの実装はすべて plans/08 のトークン経由で色を書くため、`html[data-theme="dark"]` で自動追随する。**個別分岐は書かない**(唯一の例外はセグメント選択 shadow。plans/08 §8.3)。検証用に、1c 実測値とトークンの対応(シェル該当分)を確定する:

| シェル要素 | ライト実測 | ダーク実測(1c) | 使用トークン |
|---|---|---|---|
| ヘッダ面 / 下線 | #FFFFFF / #E6E3DA | #1E2228 / #2A2F37 | `--pr-bg-card` / `--pr-border-header` |
| ヘッダ文字(タイトル) | #1E2227 | #E8E6E1 | `--pr-text` |
| 「‹」・「▾」・翻訳% | #8A8E94 / #9A9EA4 | #7A7F87 | `--pr-text-icon` / `--pr-text-muted` |
| セグメントトラック | #EFEDE6 | #14171B | `--pr-bg-muted` |
| セグメント選択中 面/文字 | #FFFFFF / #1E2227(+shadow) | #2C313A / #F0EEE9(shadow なし) | `--pr-bg-seg-selected` / `--pr-text` |
| セグメント非選択文字 | #5B6067 | #9BA1A9 | `--pr-text-sub` |
| ステータスピル 枠/面/文字 | #DDD9CF / #FFFFFF / 既定 | #333942 / #22262D / #C9CCD1 | `--pr-border-control` / `--pr-bg-control` / `--pr-text-mid` |
| ピルドット(読んでいる)・品質バッジ | var(--pr-a) / var(--pr-as) | var(--pr-ad) #8FAECB / var(--pr-ads) | `--pr-acc` / `--pr-acc-s`(エイリアスが分岐を吸収) |
| スタイルセレクタ 枠/文字 | #DDD9CF / #3C4046 | #333942 / #C9CCD1 | `--pr-border-control` / `--pr-text-mid` |
| 検索ボックス 面/文字/キーキャップ | #F1EFE9 / #8A8E94 / 枠#DAD7CD 面#FFFFFF | #14171B / #7A7F87 / 枠#333942 面#22262D | `--pr-bg-inset` / `--pr-text-icon` / `--pr-border-keycap`+`--pr-bg-control` |
| 目次ペイン 面/右境界 | #F7F6F2 / #E7E4DB | #1B1E24 / #2A2F37 | `--pr-bg-pane` / `--pr-border-pane` |
| 目次行文字 / 分母外 | #3A3E44 / #8A8E94 | #C9CCD1 / #7A7F87 | `--pr-text-nav` / `--pr-text-icon` |
| 目次アクティブ行 | var(--pr-as)+var(--pr-a) | var(--pr-ads)+var(--pr-ad) | `--pr-acc-s`+`--pr-acc` |
| サイドパネル 面/左境界/タブ下線 | #FFFFFF / #E7E4DB / #ECE9DF | #1E2228 / #2A2F37 / #262B32 | `--pr-bg-card` / `--pr-border-pane` / `--pr-border-soft` |
| タブ非選択/件数 | #777B81 / #9A9EA4 | #9BA1A9 / #7A7F87 | `--pr-text-sub2` / `--pr-text-muted` |

- 目次の翻訳済み「✓」色 #7E9C88 と注釈数バッジ色はテーマ非依存の固定値(plans/08 §2.2 の決定に従う)。
- テーマ切替(`data-theme` / `data-accent` / `data-body-font`)の適用機構は plans/08 §8 のとおりで、シェルは関与しない。

## 10. キーボードショートカット(useViewerKeymap)

シェルがビューアページ全体のキーマップを一元登録する(`window` の `keydown`、capture なし)。**入力要素(input / textarea / contenteditable)フォーカス中はすべて無効**(例外: 検索 input 内の `↑`/`↓`/`Enter`/`Esc` は §7 の挙動)。修飾キー付き(Ctrl/⌘/Alt)は素通しする。

| キー | 動作 | 出典 |
|---|---|---|
| `/` | 論文内検索へフォーカス(§7)。`preventDefault` | デザイン確定(全画面キーキャップ表示) |
| `t` | 現在段落の対訳ポップ開閉(シェルは `viewer-store.toggleBilingualPop()` を呼ぶのみ。§2.3 の `bilingualPopToggleSignal` を 1b の `TranslationPane` が購読して実処理)。`mode === 'translation'` のみ有効 | デザイン確定(1b「t で開閉」) |
| `j` / `k` | 次/前の段落ブロックへ移動(本文ペインへ `requestScroll` 委譲) | docs/04 §14 決定 |
| `m` | 表示モード循環(訳文→対訳→原文→PDF→記事→訳文。§3.3) | docs/04 §14 決定 |
| `c` | チャットタブを開く(`setPanel(true,'chat')`+入力欄フォーカス) | docs/04 §14 決定 |
| `b` | 現在セクションのブックマークをトグル(付与=`POST /api/library-items/{id}/annotations { kind: 'bookmark', anchor: <現在セクション> }`、解除=`DELETE /api/annotations/{annotation_id}`。03-api §8.2/§8.3。処理は 1b 担当、キー登録はシェル) | docs/04 §14 決定 |
| `Esc` | 開いている浮遊 UI を 1 つ閉じる(優先順: 選択メニュー → 検索ドロップダウン → Popover → 対訳ポップ/図表ポップオーバー)。何も無ければ何もしない | 決定(plans/08 §5.10/5.11 の開閉規約と整合) |

- キーヒントの UI 表示はデザインどおり 2 箇所のみ(検索キーキャップ「/」、対訳ポップ「t で開閉」)。ショートカット一覧画面は作らない。決定。
- `aria-keyshortcuts` を検索ボックス(`"/"`)とモードセグメント(`"m"`)に付与する。

## 11. 各画面ファイルとの分担境界(確定)

「シェル=枠と横断機構、画面ファイル=本文ペインと担当タブ本体」で分割する。重複実装・二重定義を禁止する。

| 担当 | 所有物(実装・仕様の正) |
|---|---|
| **本書(viewer-shell)** | ViewerShell / ViewerHeader(全要素・差し替え規則)/ TocRail / TocPane / SidePanel の枠・タブ・幅・開閉 / InPaperSearch / viewer-store / useViewerKeymap(キー登録)/ useReadingPosition / useReadingSession / `?mode=` ほか URL 契約 / モード間位置引き継ぎ |
| **1a(対訳)** | `BilingualPane`(2 カラムグリッド・カラム見出し・段落対応⇄・数式ブロックとアクション・チャット根拠の本文強調描画)/ `ChatTab` / `NotesTab` |
| **1b(訳文)** | `TranslationPane`(720/680px カラム・3行要約カード・前回位置バナー・段落ホバー「対」・対訳ポップ・`t` の実処理・`b` の実処理)/ `SelectionMenu` の呼び出し(コンポーネント自体は plans/08 §5.22)/ `AnnotationsTab` |
| **1c(ダーク・図表)** | 図表参照ポップオーバー(width 400)/ `SourcePane` との共有インライン参照リンク挙動 / `FiguresTab`(図表一覧+参考文献・「+この論文も取り込む」) |
| **2a(PDF)** | `PdfPane`(PDF.js・ツールバー・bbox 選択チップ・相互リンク)/ `PdfSidebar`(leftPane。目次/ページ切替+サムネイル)/ `InfoTab` |
| **1h(記事)** | `ArticlePane`(記事ブロック・概要図・ホバーツールバー・出典ブロック)/ `ArticleRegenerateButton`(シェルは配置のみ。§4.3) |
| **5a(リソース)** | `ResourcesTab`(カード 4 種・自動検出カード・URL 追加フッタ) |
| **1g(読了フロー)** | 読了モーダル本体。シェルはステータスピルで `done` 選択時に開くトリガのみ(§4.2-4) |

境界の運用規則(確定):

1. シェルの寸法・色・文言を画面ファイル側で上書きしない。画面固有の差(例: 注釈タブ 320px、記事モードのヘッダ差し替え)は**本書に規則として記載済みのもののみ**存在する。新たな差分が必要になった場合は本書を先に改訂する。
2. 本文ペイン→シェルへの通知は viewer-store の action(`setCurrentBlock` / `requestScroll` の消費)のみ。ペイン同士の直接参照は禁止。
3. タブ本体→本文への操作(根拠ジャンプ・注釈ジャンプ・図表ジャンプ)はすべて `requestScroll` を経由する。根拠ジャンプ時は `viewer-chat-store` の `setChatEvidence` が対象 `blockId`/`display` を更新し、`BilingualPane` が本文側「✦ チャットの根拠」強調とバッジを描画する。

## 12. 受け入れ基準

- [ ] `/papers/{itemId}` を `mode` 無しで開くと `last_position.mode`(無ければ `translation`)に正規化され、URL に `?mode=` が入る
- [ ] ヘッダのセグメントで 5 モードを切替でき、切替後も直前の可視ブロック相当の位置が表示される(履歴は増えない)
- [ ] 記事モードでのみスタイルセレクタが「✦ 指示つき再生成」に差し替わり、他 8 要素は不変である
- [ ] ステータスピルから 6 値を変更でき、`done` 選択時は読了フローモーダルを経由する
- [ ] 目次ペインが翻訳進捗%・節ごと✓・注釈数バッジ・ブックマーク・未翻訳付録(オンデマンド)・今日の読書分を表示し、「⟨⟨」⇄「☰」で 232px⇄44px を往復する。開閉状態は同一論文の再訪で維持される
- [ ] サイドパネルが排他 6 タブで、件数バッジは注釈・リソースのみ。注釈タブ選択時のみ幅 320px、他タブは 340px(訳文モードの本文カラム幅 720/680px が連動)
- [ ] アクティブタブ再クリックとキー `c`、⋯メニューでパネルを開閉できる。チャットのストリーミングはタブ切替で中断しない
- [ ] `/` で検索が開き、原文・訳文両面のヒット(訳文ヒットは原文と同一視)を表示、Enter でジャンプ、ヒットを含む節に目次マーカーが出る
- [ ] キー `t`・`j`/`k`・`m`・`b`・`Esc` が §10 の表どおり動作し、入力フォーカス中は発火しない
- [ ] ダークモードでシェル全要素が §9 の表の 1c 実測値と一致する(VRT: ライト/ダーク × アクセント 4 色)
- [ ] 読書位置が 5 秒デバウンスで保存され、別デバイスで前回位置から再開できる。読書時間は設定オフで送信されない
