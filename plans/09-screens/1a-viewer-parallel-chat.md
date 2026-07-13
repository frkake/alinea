# 画面 1a: ビューア 対訳+チャットパネル

> 対象読者と前提: 本書は「Alinea」のフロントエンド実装者向けに、確定デザイン画面 1a(論文ビューア — 対訳モード・高密度、左右分割+チャットパネル根拠チップ)を 1px の差分なく実装するための計画書である。機能仕様は docs/04(ビューア)・docs/05(読解チャット)・docs/03(翻訳)を正、ピクセル値は抽出ファイル extract/1a.md を正とする。共通コンポーネント名・トークン名は plans/08-design-system.md、API エンドポイント名・型名は plans/03-api.md のものをそのまま使う。技術スタック: Next.js 15(App Router)+ React 19 + TypeScript 5 + Tailwind CSS v4 + TanStack Query v5 + Zustand + KaTeX + `packages/api-client`(openapi-fetch 生成クライアント+`sseFetch()`)。

## 1. 概要とルート

- **ルートパス**: `/papers/[itemId]`(viewer-shell §3.1 が正)。ファイルは `apps/web/src/app/(app)/papers/[itemId]/page.tsx`。論文ビューアの 6 画面(1a/1b/1c/2a/1h/5a)はすべてこの単一ルートであり、表示モード・サイドパネルタブ・テーマの違いは URL クエリとクライアント状態で表現する。理由: モードはヘッダのセグメントでワンクリック切替(docs/04 §2)であり、ページ遷移を挟むと「読む流れを切らない」(P1/P6)に反するため。
- **URL 状態(確定)**:
  - `?mode=` — `translation | parallel | source | pdf | article`(`LastPosition["mode"]` と同値)。省略時は `last_position.mode`、それも無ければ `translation`。画面 1a は `?mode=parallel`。
  - `?tab=` — `chat | notes | annotations | figures | resources | info`(plans/08 §5.16 `SidePanelTabId`)。省略時は `chat`。画面 1a は `?tab=chat`(省略と同値)。
  - `?block=blk-…` — ブロックへの深いリンク(根拠チップの共有・通知からの遷移。ハッシュ `#blk-…` は使わない)。存在すれば初期スクロール位置として `last_position` より優先する。
  - モード・タブ変更は `history.replaceState`(履歴を汚さない。決定)。
- **認証**: 必須(session クッキー)。`(app)` レイアウトが `GET /api/auth/me` でセッション確認し、未認証は `/login?next={戻り先パス}` へリダイレクト(plans/01 §2.1)。CSR 画面(SSR しない)。
- **画面の役割**: 深い読解(S2)の主戦場。左=目次、中央=段落単位の原文/訳文 2 カラム対訳、右=読解チャット(根拠チップ)。チャットが参照中の数式(例: 式(5))が本文側でアクセント強調され、チャット⇔本文が双方向にリンクする状態を描いた画面である。

## 2. データ要件

### 2.1 使用 API エンドポイント一覧(plans/03 の名前)

| # | エンドポイント | 用途 | 取得/実行タイミング |
|---|---|---|---|
| 1 | `GET /api/library-items/{id}/viewer` | 初期化複合(書誌・目次・翻訳進捗・タブ件数・前回位置・今日の読書分) | ルート初回マウント時。初期描画のブロッキング取得 |
| 2 | `GET /api/revisions/{revision_id}/document?section_id=` | 本文ブロック(セクション単位) | 表示セクション+前後 1 セクションを先読み。ETag/304 対応 |
| 3 | `GET /api/revisions/{revision_id}/translations/{style}/units?section_id=` | 訳文ユニット(セクション単位) | #2 と同時に並列取得 |
| 4 | `GET /api/revisions/{revision_id}/translations` | スタイル別 TranslationSet 一覧(スタイルセレクタの状態) | ヘッダのスタイルドロップダウン初回オープン時 |
| 5 | `GET /api/library-items/{id}/annotations` | ハイライト・注釈番号チップ・目次注釈数の照合 | 初回マウント時(#1 の直後、非ブロッキング) |
| 6 | `GET /api/library-items/{id}/chat/threads` | スレッド一覧(「スレッド: メイン ▾」) | チャットタブ表示時(1a では初回マウント時) |
| 7 | `GET /api/chat/threads/{thread_id}/messages` | メッセージ履歴 | アクティブスレッド確定時。`cursor` で過去方向に追加取得 |
| 8 | `POST /api/chat/threads/{thread_id}/messages` | 質問送信(SSE ストリーミング) | 送信ボタン/Enter/サジェストチップ/「✦ この式を説明」 |
| 9 | `POST /api/chat/messages/{message_id}/regenerate` | 「再生成」(SSE) | 回答アクション行「再生成」 |
| 10 | `POST /api/library-items/{id}/notes` | 「↑ メモに保存」(`source_message_id` 指定) | 回答アクション行 |
| 11 | `PUT /api/library-items/{id}/position` | 読書位置の自動保存 | 先頭可視ブロック変化から 5,000ms デバウンス |
| 12 | `POST /api/library-items/{id}/reading-sessions` | 読書時間計測(「今日の読書 42分」の加算) | 60 秒ごとのハートビート+`visibilitychange`/`pagehide` 時 |
| 13 | `PATCH /api/library-items/{id}` | ステータス変更(「読んでいる ▾」) | ステータスピルのドロップダウン選択時 |
| 14 | `POST /api/translation-sets/{set_id}/prioritize` | 開いたセクションの優先翻訳繰り上げ | 未翻訳ブロックを含むセクション表示時。同一セクションにつきページセッション中 1 回のみ(クライアントで section_id を記録して重複発火しない — 決定) |
| 15 | `POST /api/translation-sets/{set_id}/sections/{section_id}/translate` | 付録のオンデマンド翻訳(「開くと翻訳します」) | 目次の `on_demand: true` セクションを開いた時 |
| 16 | `GET /api/jobs/{job_id}/events` | 翻訳ジョブ進捗 SSE(「翻訳 96%」の更新) | #15 の 202 応答後、および `translation.status != "complete"` の間 |
| 17 | `GET /api/revisions/{revision_id}/search?q=` | 論文内検索(`/`) | 検索ボックス入力 300ms デバウンス |
| 18 | `GET /api/revisions/{revision_id}/blocks/{block_id}` | 根拠ジャンプ先が未ロードセクションの時の単発解決・引用プレビュー | 根拠チップクリック時(対象セクション未取得の場合のみ) |
| 19 | `PATCH /api/annotations/{annotation_id}` / `POST /api/library-items/{id}/annotations` | 選択メニューからのハイライト作成・編集 | SelectionMenu 操作時 |

### 2.2 TanStack Query キー設計(確定)

キーは配列リテラル。第 1 要素はリソース名(plans/03 の tag 名)、以降は ID・パラメータの順。

```ts
// apps/web/src/features/viewer/queryKeys.ts
export const viewerKeys = {
  viewer:       (liId: string) => ['viewer', liId] as const,
  document:     (revId: string, sectionId: string) => ['document', revId, sectionId] as const,
  units:        (revId: string, style: Style, sectionId: string) =>
                  ['translationUnits', revId, style, sectionId] as const,
  translations: (revId: string) => ['translations', revId] as const,
  annotations:  (liId: string) => ['annotations', liId] as const,
  block:        (revId: string, blockId: string) => ['block', revId, blockId] as const,
  inPaperSearch:(revId: string, q: string) => ['inPaperSearch', revId, q] as const,
  chatThreads:  (liId: string) => ['chatThreads', liId] as const,
  chatMessages: (threadId: string) => ['chatMessages', threadId] as const,
};
```

- `staleTime`: `document` / `units` / `block` = `Infinity`(リビジョンは不変データ。plans/03 §6.3)。`viewer` = 30,000ms。`annotations` / `chatThreads` / `chatMessages` = 0(操作起点で `invalidateQueries`)。
- `chatMessages` は `useInfiniteQuery`(`next_cursor` は過去方向、`limit=50` 既定)。
- ミューテーション成功時の無効化: メモ保存→`['notes', liId]`(メモタブのキー)、ステータス変更→`viewerKeys.viewer(liId)` と `['libraryItems']`、注釈作成→`viewerKeys.annotations(liId)` と `viewerKeys.viewer(liId)`(目次バッジ件数)。

### 2.3 リアルタイム更新

- **チャット送信/再生成**: `sseFetch()`(packages/api-client 同梱、fetch + ReadableStream)で `POST` の SSE を受信。イベント `start` / `delta` / `evidence` / `done` / `error`(plans/03 §10.3 の確定形式)。`delta` は `block_index` 昇順・同一 index の `text` を連結。`done` 受信後に `chatMessages` を invalidate して確定形へ置換(SSE 切断時の回復経路も同じ)。チャット SSE は再接続再開なし(plans/03 §1.9)。
- **翻訳進捗**: `translation.status !== "complete"` の間、アクティブ翻訳ジョブ(`GET /api/library-items/{id}/jobs?active=true` で取得)の `GET /api/jobs/{job_id}/events` を EventSource で購読し、`progress` イベントで目次ヘッダの「翻訳 N%」と節の ✓ を更新(`viewerKeys.viewer` のキャッシュを `setQueryData` で部分更新)。EventSource が 3 回連続失敗した場合は `viewerKeys.viewer` の `refetchInterval: 5000` にフォールバックし、SSE 復帰で停止(plans/01 §5)。
- **読書位置**: `PUT /api/library-items/{id}/position` を 5,000ms デバウンス(plans/03 §5.8)。`mode` には現在の表示モードを送る。
- **読書時間**: `client_session_id`(ページロード時に `crypto.randomUUID()`)で `POST /api/library-items/{id}/reading-sessions` を 60,000ms 間隔+`document.visibilitychange`(hidden 遷移)+`pagehide` で送信(`navigator.sendBeacon` フォールバック)。「アクティブ」判定はタブ前面かつ直近 60 秒以内に pointermove / keydown / scroll があること(docs/04 §11)。応答の `today_reading_minutes` で目次フッタを更新。設定 `reading.track_reading_time=false` なら送信しない。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`共通` = plans/08 §5〜6 の共通コンポーネント。無印 = 本画面(ビューア)固有(配置: `apps/web/src/features/viewer/`)。

```
ReadPage (app/(app)/papers/[itemId]/page.tsx)
└─ ViewerShell                        … 3ペイン+ヘッダの骨格。useViewerStore を供給
   ├─ ViewerHeader
   │  ├─ (戻る「‹」/タイトル)
   │  ├─ QualityBadge                 共通 §5.3
   │  ├─ StatusPill (interactive)     共通 §5.2 + Popover 共通 §5.10 (width 180)
   │  ├─ SegmentedControl             共通 §5.1 (表示モード5値, size='md')
   │  ├─ StyleSelector                … 「スタイル: 自然訳 ▾」+ Popover
   │  ├─ SearchBox (variant='in-paper') 共通 §5.13 + InPaperSearchPopover
   │  └─ (オーバーフロー「⋯」)
   ├─ TocPane
   │  ├─ TocHeader (「目次」「翻訳 96%」「⟨⟨」)
   │  ├─ TocRow ×N (CountBadge 共通 §5.7 variant='annotation' / BookmarkIcon 共通 §6.1)
   │  ├─ OnDemandSectionBox (「付録 A 証明 — 未翻訳」)
   │  └─ TocFooter (「今日の読書 42分」「位置は自動保存」)
   ├─ ParallelView                    … mode='parallel' の本文ペイン
   │  ├─ ColumnHeader (「原文 — ENGLISH」/「訳文 — 自然訳 ✦ AI翻訳」「段落対応 ⇄」)
   │  ├─ SectionHeading (「2.1 整流フロー — Rectified Flow」)
   │  ├─ ParagraphPair ×N
   │  │  ├─ SourceParagraph (HighlightMark 共通 §5.17)
   │  │  └─ TranslationParagraph (HighlightMark / CrossRefLink / CitationLink)
   │  ├─ EquationBlock (KaTeX + 参照中バッジ + アクション行)
   │  └─ SelectionMenu                共通 §5.22(テキスト選択時)
   └─ SidePanel
      ├─ SidePanelTabs                共通 §5.16
      └─ ChatPanel (tab='chat')
         ├─ ThreadBar (「スレッド: メイン ▾」+ Popover / 「コンテキスト: この論文」)
         ├─ ChatMessageList
         │  ├─ DateSeparator (「今日 21:47」)
         │  ├─ UserMessageCard (EvidenceChip 共通 §5.18 size='header' + 引用ブロック)
         │  └─ AssistantMessage
         │     ├─ (「✦ アシスタント」= AiMark 共通 §5.19) + AIBadge (variant='generated')
         │     ├─ MessageMarkdown ([[ev:n]] → EvidenceChip size='inline')
         │     ├─ AsideBox (AIBadge variant='external'|'guess')
         │     └─ MessageActions (「↑ メモに保存」「再生成」「コピー」)
         └─ ChatInputArea
            ├─ SuggestChips (定型5種)
            ├─ ChatInputBox (textarea + 送信「↑」)
            └─ (免責文)
```

### 3.2 画面固有コンポーネントの props 型(確定)

```ts
// apps/web/src/features/viewer/types.ts
import type { LibraryItemSummary, TocNode, Style, Anchor, AnchorRef,
              ChatThread, ChatMessage, Block, Annotation } from '@alinea/api-client';

export type ViewerMode = 'translation' | 'parallel' | 'source' | 'pdf' | 'article';

interface ViewerHeaderProps {
  libraryItem: LibraryItemSummary;
  mode: ViewerMode;
  onModeChange: (m: ViewerMode) => void;
  style: Style;                          // 'natural' | 'literal'
  availableStyles: { style: Style; status: 'pending' | 'partial' | 'complete' }[];
  onStyleChange: (s: Style) => void;
}

interface TocPaneProps {
  toc: TocNode[];                        // plans/03 §6.1
  progressPct: number;                   // 「翻訳 96%」
  currentSectionId: string;              // 選択中ハイライト
  todayReadingMinutes: number;
  onSelect: (sectionId: string) => void; // on_demand 節は内部で plans/03 §7.5 API を発火
  onCollapse: () => void;                // 「⟨⟨」→ 44px レール(1b 仕様)へ
}

interface ParallelViewProps {
  revisionId: string;
  style: Style;
  sections: { section: TocNode; blocks: Block[] }[];   // ロード済みセクション
  unitsByBlockId: ReadonlyMap<string, { text_ja: string | null; state: 'machine'|'edited'|'protected' }>;
  annotations: Annotation[];
  chatEvidenceBlockId: string | null;    // 「✦ チャットの根拠」強調対象(§5.4)
  onVisibleBlockChange: (blockId: string, sectionId: string) => void; // 位置保存・目次同期
}

interface ParagraphPairProps {
  block: Block;                          // type='paragraph'
  translation: { text_ja: string | null; state: 'machine'|'edited'|'protected' } | null;
  highlights: Annotation[];              // このブロックにアンカーされた highlight
  annotationNumbers: ReadonlyMap<string, number>; // annotation_id → 丸数字
  onCrossRefClick: (ref: { kind: 'figure'|'equation'|'table'; refId: string }) => void;
  onCitationClick: (refId: string) => void;
}

interface EquationBlockProps {
  block: Block;                          // type='equation'。latex / number を持つ
  referencedByChat: boolean;             // true で強調状態+浮きバッジ
  onExplain: (anchor: Anchor) => void;   // 「✦ この式を説明」→ ChatPanel へ
  onCopyLatex: (latex: string) => void;  // クリップボード+Toast
}

interface ChatPanelProps {
  libraryItemId: string;
  threads: ChatThread[];
  activeThreadId: string;
  onThreadChange: (id: string) => void;
  pendingAnchors: AnchorRef[];           // 「✦ この式を説明」/ SelectionMenu「✦ AIに質問」で積まれた引用チップ(この 2 導線のみ。決定)
  onRemovePendingAnchor: (blockId: string) => void;
}

interface AssistantMessageProps {
  message: ChatMessage;                  // role='assistant'
  streaming: boolean;                    // SSE 受信中は true(アクション行非表示)
  onSaveToNote: (messageId: string) => void;
  onRegenerate: (messageId: string) => void;
  onCopy: (messageId: string) => void;
  onEvidenceJump: (anchor: AnchorRef) => void;
}

interface SuggestChipsProps {
  onPick: (qa: 'summary_3line'|'beginner_explain'|'contributions_limits'
             |'experiment_setup'|'implementation_points') => void;
  disabled: boolean;                     // ストリーミング中 true
}
```

### 3.3 クライアント状態(Zustand ストア `useViewerStore`)

```ts
// apps/web/src/features/viewer/store.ts
interface ViewerState {
  mode: ViewerMode;
  panelTab: SidePanelTabId;              // plans/08 §5.16
  tocCollapsed: boolean;                 // 対訳モード既定 false(1a は展開状態)
  style: Style;                          // 既定 'natural'
  currentSectionId: string;
  currentBlockId: string | null;
  chatEvidenceBlockId: string | null;    // §5.4 の強調対象
  flashBlockId: string | null;           // 根拠ジャンプの一時ハイライト
  pendingAnchors: AnchorRef[];           // 送信前の引用チップ
  activeThreadId: string | null;
  searchOpen: boolean;
  pairSyncEnabled: boolean;              // 「段落対応 ⇄」トグル。既定 true。非永続(セッション内のみ。決定)
}
```

## 4. レイアウト・スタイル完全仕様

出典: extract/1a.md(逐語)。色値の後の `→ トークン` は packages/tokens の対応 CSS 変数(plans/08 §2)であり、実装ではトークン側を書く(hex 直書きは ESLint で禁止。plans/08 §4.3)。アクセント変数 `--pr-a` / `--pr-as` / `--pr-am` は実装では意味エイリアス `--pr-acc` / `--pr-acc-s` / `--pr-acc-m` を使う。

### 4.0 デザイナー注記(フレーム外。実装対象外の参考)

- バッジ「1a」・タイトル「論文ビューア — 対訳モード・高密度」・説明「S2 深い読解 / サイドパネル=読解チャット(根拠チップ) / 式(5)がチャットの根拠として参照中」はデザインキャンバス上の注記であり UI の一部ではない。
- フレーム: 1440×900px、background:#FBFAF7(→ `--pr-bg-app`)、border:1px solid #D6D3C9、border-radius:10px、box-shadow:0 20px 44px rgba(28,30,34,0.12)。フレームの枠・影・角丸はデザインキャンバス表現であり、実アプリではビューポート全面に描画する(plans/08 §7.1)。overflow:hidden、flex-column、color:#1E2227(→ `--pr-text`)。
- 画面 1a はフレーム 1 枚で完結(フレーム外の別状態要素なし)。

### 4.1 レイアウト構造

```
┌──────────────────────────────────────────────────────────────┐
│ ヘッダ h=52px #FFF  border-bottom:1px #E6E3DA                 │
│ ‹ タイトル [A] (●読んでいる▾) …spacer… [訳文|対訳|原文|PDF|記事]│
│  [スタイル:自然訳▾] [🔍この論文内を検索 /] ⋯                   │
├──────────┬──────────────────────────────┬────────────────────┤
│ 目次ペイン │ 本文ペイン(対訳・2カラムgrid)   │ サイドパネル(チャット)│
│ w=232px   │ flex:1                        │ w=340px            │
│ #F7F6F2   │ 左=原文(英) 右=訳文(日)        │ #FFFFFF            │
│ border-   │ column-gap:34px               │ border-left:1px    │
│ right:1px │ 中央に式(5)ブロック(全幅)      │ #E7E4DB            │
│ #E7E4DB   │                               │ タブ行/スレッド行/  │
│ 下部:読書  │                               │ メッセージ域/入力域 │
│ 時間フッタ │                               │                    │
└──────────┴──────────────────────────────┴────────────────────┘
```

- ヘッダ下の本体: `flex:1; display:flex; min-height:0` の横並び 3 ペイン。
- 目次ペイン: width:232px、flex:none、background:#F7F6F2(→ `--pr-bg-pane`)、border-right:1px solid #E7E4DB(→ `--pr-border-pane`)、flex-column、padding:10px 8px 8px。
- 本文ペイン: flex:1、min-width:0、padding:18px 34px 0、flex-column、overflow:hidden(内部の本文領域がスクロールコンテナ)。
- サイドパネル: width:340px、flex:none、background:#FFFFFF(→ `--pr-bg-card`)、border-left:1px solid #E7E4DB(→ `--pr-border-pane`)、flex-column。
- 1440px 超では中央ペイン(flex:1)のみ拡張、1440px 未満では中央ペインが縮小(最小 560px)。アプリ最小幅 1200px。1200px 未満は対訳を段落交互レイアウトに切替(docs/04 §4。本画面のスコープ外の縮退)。

### 4.2 ヘッダ(ViewerHeader)

h=52px、flex:none、background:#FFFFFF(→ `--pr-bg-card`)、border-bottom:1px solid #E6E3DA(→ `--pr-border-header`)、flex 横並び、align-items:center、gap:10px、padding:0 16px。左から:

1. 戻る矢印: テキスト「‹」、font-size:16px、color:#8A8E94(→ `--pr-text-icon`)、width:20px、text-align:center。クリックで直前のライブラリ系画面へ(`router.back()`。履歴が無ければ `/library`。決定)。
2. 論文タイトル: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」、font-size:13px、font-weight:600、max-width:330px、white-space:nowrap、overflow:hidden、text-overflow:ellipsis。データ: `library_item.paper.title`。
3. `QualityBadge`(共通): 18×18px、border-radius:4px、background:var(--pr-as)、color:var(--pr-a)、font-size:10.5px、font-weight:700、中央揃え、文字「A」。`title="品質レベルA: LaTeXソースから完全構造化"`。データ: `library_item.quality_level`。
4. `StatusPill`(共通、interactive): inline-flex、gap:5px、height:24px、padding:0 9px、border:1px solid #DDD9CF(→ `--pr-border-control`)、border-radius:999px、font-size:11.5px、font-weight:500、background:#FFFFFF(→ `--pr-bg-control`)。先頭に 7×7px 丸ドット(border-radius:50%、background:var(--pr-a) — 「読んでいる」はアクセント連動)。ラベル「読んでいる」。末尾「▾」(color:#9A9EA4 → `--pr-text-muted`、font-size:9px)。データ: `library_item.status`(表示は plans/03 `Status` → `STATUS_LABELS` の日本語)。
5. スペーサー: `flex:1`。
6. `SegmentedControl`(共通、size='md'): トラック background:#EFEDE6(→ `--pr-bg-muted`)、border-radius:7px、padding:2px、gap:2px。各セグメント height:24px、padding:0 11px、border-radius:5px、font-size:11.5px。ラベル(順序固定): 「訳文」「対訳」「原文」「PDF」「記事」。
   - 非選択(訳文/原文/PDF/記事): color:#5B6067(→ `--pr-text-sub`)、背景なし。
   - 選択中(対訳): background:#FFFFFF(→ `--pr-bg-seg-selected`)、color:#1E2227(→ `--pr-text`)、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)(→ `--pr-shadow-seg`)。
7. StyleSelector(固有): 「スタイル: 自然訳」+「▾」(#9A9EA4、9px)。inline-flex、gap:5px、height:26px、padding:0 10px、border:1px solid #DDD9CF、border-radius:6px、font-size:11.5px、color:#3C4046(→ `--pr-text-mid`)。ラベルは `style` の日本語(natural=「自然訳」/ literal=「直訳」)。クリックで `Popover`(共通、width 180px、placement='bottom-end'、caret なし。決定: ステータスドロップダウンと同じ幅)を開き 2 項目を表示。`literal` 未生成(items に無い)の選択はオンデマンド生成 `POST /api/revisions/{revision_id}/translations` を発火(docs/03 §5)。
8. `SearchBox`(共通、variant='in-paper'): inline-flex、gap:6px、height:26px、padding:0 10px、background:#F1EFE9(→ `--pr-bg-inset`)、border-radius:6px、font-size:11.5px、color:#8A8E94(→ `--pr-text-icon`)、width:150px。先頭に MagnifierIcon 11×11(viewBox 0 0 12 12、circle cx=5 cy=5 r=3.6 stroke currentColor 1.3、ハンドル線 M8 8l2.6 2.6 stroke 1.3 round)。プレースホルダ「この論文内を検索」。右端に `Keycap`「/」: margin-left:auto、border:1px solid #DAD7CD(→ `--pr-border-keycap`)、border-radius:3px、padding:0 4px、font-size:9.5px、background:#FFFFFF(→ `--pr-bg-control`)。
9. オーバーフローメニュー「⋯」: font-size:15px、color:#5B6067(→ `--pr-text-sub`)、letter-spacing:1px。クリックで `Popover`(width 180px、placement='bottom-end'。決定)を開く。項目(決定。docs/04 §10.3 のエクスポート導線を再掲): 「再取り込み」= `POST /api/papers/{paper_id}/reingest`(plans/03 §4.2、確認 Modal を挟む)/「注釈 Markdown ⤓」= `GET /api/library-items/{id}/export/annotations`(添付ダウンロード)/「原文 PDF ⤓」= `GET /api/papers/{paper_id}/pdf`(新規タブで開く)。

### 4.3 目次ペイン(TocPane, w=232px)

**ヘッダ行**(flex、justify-content:space-between、padding:0 8px 8px):
- 左「目次」: font-size:11px、font-weight:600、color:#8A8E94(→ `--pr-text-icon`)。
- 右「翻訳 96%」: font-size:10.5px、color:#8A8E94。続けて折りたたみ記号「⟨⟨」(margin-left:6px、color:#B6BAC0 → `--pr-text-faint`)。データ: `viewer.translation.progress_pct`。

**目次リスト**: flex-column、gap:1px、font-size:12.3px、color:#3A3E44(→ `--pr-text-nav`)、flex:1、overflow:hidden(決定: 実装では `overflow-y:auto` とし、スクロールバーは `scrollbar-width:none` で非表示。デザインは 900px 内に全行が収まる想定のため見た目は同一)。各行(TocRow)共通: flex、align-items:center、gap:6px、padding:4px 8px、border-radius:5px。サブ節は padding:4px 8px 4px 22px。行末の記号:
- 翻訳済みチェック「✓」: color:#7E9C88(固定値。`--pr-green` #659471 とは別のデザイン実測値のためリテラルで保持 — 決定: tokens.css に `--pr-toc-check: #7E9C88` を追加せず、TocRow のローカル CSS 変数 `--toc-check:#7E9C88` として定義)、font-size:10px。データ: `TocNode.translated`。
- 注釈数バッジ = `CountBadge`(variant='annotation'): min-width:15px、height:15px、border-radius:8px、background:rgba(196,148,50,0.18)(→ `--pr-ann-important-count-bg`)、color:#8A6A24(→ `--pr-ann-important-chip-fg`)、font-size:9.5px、font-weight:600、中央揃え。データ: `TocNode.annotation_count`(0 は非表示)。
- `BookmarkIcon`(共通): 9×11(viewBox 0 0 10 12、path M1 1h8v10L5 8.5 1 11V1z、fill:currentColor)、color:var(--pr-a)。データ: `TocNode.bookmarked`。

**行一覧(1a の描画データ。上から)**:
1. 「アブストラクト」+ ✓
2. 「1 はじめに」+ バッジ「2」+ ✓
3. 「2 手法」+ ✓
4. 「2.1 整流フロー」【選択中】: background:var(--pr-as)、color:var(--pr-a)、font-weight:600、box-shadow:inset 2px 0 var(--pr-a)(左縁 2px アクセントバー)。バッジ「3」+ ✓。インデント 22px。
5. 「2.2 Reflow: 経路の直線化」+ BookmarkIcon + ✓(インデント 22px)
6. 「2.3 蒸留との関係」+ ✓(インデント 22px)
7. 「3 実験」+ ✓
8. 「3.1 CIFAR-10 画像生成」+ バッジ「1」+ ✓(インデント 22px)
9. 「3.2 高解像度画像への拡張」+ ✓(インデント 22px)
10. 「3.3 ドメイン転移」+ ✓(インデント 22px)
11. 「4 関連研究」+ ✓
12. 「5 結論」+ ✓
13. 「参考文献」: color:#8A8E94(→ `--pr-text-icon`)、記号なし(`in_progress_denominator: false` の淡色表示)。

ラベルは `TocNode.number + ' ' + title_ja`(number が null なら title_ja のみ。title_ja が null の未翻訳節は title_en)。

**未翻訳付録ボックス(OnDemandSectionBox)**: リスト末尾、margin:8px 6px 0、border:1px dashed #D5D1C5(→ `--pr-border-dashed`)、border-radius:6px、padding:8px 9px、flex-column、gap:5px。
- 1行目: 「付録 A 証明 」+「— 未翻訳」(後半 color:#9A9EA4 → `--pr-text-muted`)。font-size:11.5px、color:#5B6067(→ `--pr-text-sub`)。
- 2行目: 「開くと翻訳します(オンデマンド)」font-size:10.5px、color:#9A9EA4、line-height:1.5。
- データ: `TocNode.on_demand === true` の節。クリックで該当セクションへスクロール+`POST /api/translation-sets/{set_id}/sections/{section_id}/translate`。

**ペインフッタ(TocFooter)**: padding:8px 8px 2px、border-top:1px solid #E7E4DB(→ `--pr-border-pane`)、flex、justify-content:space-between、font-size:10.5px、color:#9A9EA4(→ `--pr-text-muted`)。左「今日の読書 42分」(データ: `viewer.today_reading_minutes` → `今日の読書 ${n}分`)/ 右「位置は自動保存」(固定文言)。

### 4.4 本文ペイン(ParallelView)

**カラムヘッダ行**: grid、grid-template-columns:1fr 1fr、column-gap:34px、padding-bottom:8px、border-bottom:1px solid #ECE9DF(→ `--pr-border-soft`)。
- 左: 「原文 — ENGLISH」font-size:10.5px、color:#9A9EA4(→ `--pr-text-muted`)、font-weight:600、letter-spacing:0.4px。
- 右: 「訳文 — 自然訳 」(スタイル名は `style` に追随)+「✦ AI翻訳」(color:var(--pr-a))+ 右寄せ(float:right)「段落対応 ⇄」(color:#B6BAC0 → `--pr-text-faint`)。同じ 10.5px 仕様。

**本文領域**: flex:1、overflow:hidden(実装: `overflow-y:auto`。スクロールコンテナ)、padding-top:16px。

**節見出し(SectionHeading)**: 「2.1 整流フロー 」font-size:16.5px、font-weight:700、margin-bottom:14px、font-family:'IBM Plex Sans JP'(→ `--pr-font-ui`)。続けて「— Rectified Flow」color:#8A8E94(→ `--pr-text-icon`)、font-weight:400、font-size:13.5px、font-family:'Source Serif 4',Georgia,serif(→ `--pr-font-en`)、font-style:italic。データ: `TocNode.number + title_ja` + `title_en`。

**対訳グリッド**: grid、grid-template-columns:1fr 1fr、column-gap:34px、row-gap:18px。段落ペアは同一 grid の行として並べる(左右セルが同じ grid row に入るため段落対応が構造的に保証される — 決定: 行ごとのサブグリッドではなく単一 grid に `SourceParagraph` / `TranslationParagraph` を交互配置する)。
- 原文段落(左列)共通: font-family:'Source Serif 4',Georgia,serif(→ `--pr-font-en`)、font-size:13.8px、line-height:1.72、color:#33373C(→ `--pr-text-en`)。数式変数はイタリック(`<i>`)+ `<sub>`/`<sup>`。
- 訳文段落(右列)共通: font-family:var(--pr-jp,'Noto Serif JP'),serif、font-size:14.8px、line-height:2.0、color:#24272B(→ `--pr-text-body`)。

**段落ペア1**(整流フローの定義): 両段落内に用語ハイライト(グレー)= `HighlightMark`(color='term'): background:rgba(130,130,126,0.18)(→ `--pr-ann-term-bg`)、border-radius:2px、padding:0 1px — 原文「rectified flow」/訳文「整流フロー(rectified flow)」。
- 原文逐語: Given empirical observations of two distributions *π*₀, *π*₁ on ℝᵈ, the rectified flow induced from (*X*₀, *X*₁) is an ordinary differential equation (ODE) over time *t* ∈ [0, 1], d*Z_t* = *v*(*Z_t*, *t*)d*t*, which converts *Z*₀ from *π*₀ to a *Z*₁ following *π*₁. The drift *v* is set to drive the flow to follow the direction (*X*₁ − *X*₀) of the linear path pointing from *X*₀ to *X*₁ as much as possible.
- 訳文逐語: ℝᵈ 上の2つの分布 *π*₀, *π*₁ からの経験的観測が与えられたとき、(*X*₀, *X*₁) から誘導される整流フロー(rectified flow)は、時刻 *t* ∈ [0, 1] 上の常微分方程式(ODE) d*Z_t* = *v*(*Z_t*, *t*)d*t* であり、*π*₀ に従う *Z*₀ を *π*₁ に従う *Z*₁ へと変換する。ドリフト *v* は、*X*₀ から *X*₁ へ向かう直線経路の方向 (*X*₁ − *X*₀) に可能な限り沿ってフローが進むように定められる。

**段落ペア2**(最小二乗回帰の導入。短文・末尾コロン):
- 原文逐語: This is achieved by solving a simple least squares regression problem that fits *v* to the direction (*X*₁ − *X*₀):
- 訳文逐語: これは、*v* を方向 (*X*₁ − *X*₀) に適合させる単純な最小二乗回帰問題を解くことで達成される:

**数式ブロック 式(5)(EquationBlock。チャット根拠として参照中の強調状態)**: grid-column:1/-1(全幅)、position:relative、padding:16px 20px、background:var(--pr-as)、border:1px solid var(--pr-am)、border-radius:8px、flex 中央揃え。
- 右上浮きバッジ: position:absolute、top:-9px、right:14px、inline-flex、gap:4px、height:18px、padding:0 7px、background:var(--pr-a)、color:#FFFFFF、border-radius:4px、font-size:10px、font-weight:600。文言「✦ チャットの根拠 · 式(5)」(`✦` + ` チャットの根拠 · ` + AnchorRef.display)。
- 数式本体: font-family:'Source Serif 4'、italic、font-size:15.5px、color:#24272B(→ `--pr-text-body`)。内容: min_v ∫₀¹ 𝔼‖(X₁ − X₀) − v(X_t, t)‖² dt,   X_t = tX₁ + (1 − t)X₀(sub/sup 表記、カンマの後に `&nbsp;`×3)。実装は KaTeX display レンダリング(`block.latex`)+ 上記フォントサイズ・色を KaTeX コンテナに適用。LaTeX ソースを `aria-label` に付与(docs/04 §14)。
- 式番号「(5)」: position:absolute、right:18px(垂直中央)、font-family:'Source Serif 4'、font-size:13px、color:#6A6E74(→ `--pr-text-eq`)。データ: `block.number`。
- 非参照時(通常状態): background・border・浮きバッジなし、padding は同値(レイアウトシフトを起こさないため padding は状態間で不変 — 決定: 通常時は `border:1px solid transparent`)。

**数式直下アクションボタン行**: grid-column:1/-1、flex、justify-content:flex-end、gap:6px、margin-top:-8px。
- 「✦ この式を説明」: inline-flex、gap:4px、height:22px、padding:0 9px、border:1px solid #DDD9CF(→ `--pr-border-control`)、background:#FFFFFF(→ `--pr-bg-control`)、border-radius:5px、font-size:10.5px、color:var(--pr-a)、font-weight:600。
- 「LaTeX をコピー」: 同形状、color:#5B6067(→ `--pr-text-sub`)、font-family:'IBM Plex Mono',monospace(→ `--pr-font-mono`)。

**段落ペア3**(線形補間の因果化): ハイライト(黄)= `HighlightMark`(color='important', annotationNumber=1): background:rgba(196,148,50,0.26)(→ `--pr-ann-important-bg`)、border-radius:2px、padding:0 1px — 原文「causalizes the paths of linear interpolation」/訳文「線形補間の経路を因果化し」。訳文側ハイライト直後に注釈番号チップ「1」: inline-flex、14×14px、border-radius:50%、background:rgba(196,148,50,0.30)(→ `--pr-ann-important-chip-bg`)、color:#8A6A24(→ `--pr-ann-important-chip-fg`)、font-size:9px、font-weight:700、vertical-align:4px、margin-left:2px。訳文 div は position:relative。
- 原文逐語: The *X_t* in rectified flow is a linear interpolation of *X*₀ and *X*₁. Naively, evolving *X_t* requires knowing the terminal point *X*₁, which is not causal. By fitting the drift *v* with the direction (*X*₁ − *X*₀), the rectified flow causalizes the paths of linear interpolation, yielding a flow that can be simulated without seeing the future.
- 訳文逐語: 整流フローにおける *X_t* は *X*₀ と *X*₁ の線形補間である。素朴には、*X_t* を時間発展させるには終端点 *X*₁ を知る必要があり、これは因果的でない。ドリフト *v* を方向 (*X*₁ − *X*₀) に適合させることで、整流フローは線形補間の経路を因果化し①、未来を見ることなくシミュレート可能なフローを得る。(①=丸数字注釈チップ「1」)

**段落ペア4**(条件付き期待値と経路非交差): ハイライト(青)= `HighlightMark`(color='question'): background:rgba(88,132,170,0.22)(→ `--pr-ann-question-bg`)、border-radius:2px、padding:0 1px — 原文「conditional expectation 𝔼[X₁ − X₀ | X_t = z]」/訳文「条件付き期待値 𝔼[X₁ − X₀ | X_t = z]」。
- 訳文内の相互参照リンク(CrossRefLink): 「図2」「式(5)」= color:var(--pr-a)、border-bottom:1px dotted var(--pr-a)、font-weight:600。データ: inline `{ t:'ref', kind:'figure'|'equation', ref }`。
- 訳文内の文献参照(CitationLink): 「[12]」= color:var(--pr-a)、font-weight:600、下線なし。データ: inline `{ t:'citation', ref:'ref-12' }`。
- 原文逐語: In particular, *v*(*z*, *t*) equals the conditional expectation 𝔼[*X*₁ − *X*₀ | *X_t* = *z*], and the paths of the rectified flow avoid crossing each other (Figure 2).
- 訳文逐語: 特に、*v*(*z*, *t*) は条件付き期待値 𝔼[*X*₁ − *X*₀ | *X_t* = *z*] に等しく、整流フローの経路は互いに交差しない(図2)。この性質は、式(5) の解が輸送コストを増やさないこと [12] の証明でも中心的な役割を果たす。
- 注: この段落ペアは訳文が原文より 1 文多い(自然訳の文数不一致は仕様。対応は段落粒度で保証 — docs/04 §4)。

### 4.5 サイドパネル: チャット(ChatPanel, w=340px, #FFFFFF, border-left:1px solid #E7E4DB)

**タブ行 = `SidePanelTabs`(共通)**: flex、border-bottom:1px solid #ECE9DF(→ `--pr-border-soft`)、padding:0 6px。各タブ padding:10px 9px 8px、font-size:12px。
- 「チャット」【選択中】: font-weight:600、color:var(--pr-a)、box-shadow:inset 0 -2px var(--pr-a)。
- 「メモ」「注釈 6」「図表」「リソース 4」「情報」: color:#777B81(→ `--pr-text-sub2`)。カウント数字(6, 4)= `CountBadge`(variant='tab'): font-size:10px、color:#9A9EA4(→ `--pr-text-muted`)。データ: `viewer.counts.annotations` / `viewer.counts.resources`(0 は数字非表示)。

**スレッド/コンテキスト行(ThreadBar)**: flex、align-items:center、gap:6px、padding:7px 12px、border-bottom:1px solid #F0EDE4(→ `--pr-border-hair`)、font-size:11px、color:#777B81(→ `--pr-text-sub2`)。
- 左: 「スレッド: メイン ▾」—「メイン」部分は color:#3C4046(→ `--pr-text-mid`)、font-weight:600。「▾」font-size:8.5px。データ: アクティブ `ChatThread.title`。クリックで `Popover`(width 180px、placement='bottom-start'。決定): スレッド一覧(メイン先頭)+ 最下段「+ 新しいスレッド」(選択でタイトル入力インライン、`POST /api/library-items/{id}/chat/threads`)。
- スペーサー(flex:1)。
- 右: チップ「コンテキスト: この論文」inline-flex、gap:4px、height:18px、padding:0 7px、background:#F1EFE9(→ `--pr-bg-inset`)、border-radius:4px、font-size:10px。固定文言・非インタラクティブ。

**メッセージ領域(ChatMessageList)**: flex:1、overflow:hidden(実装: `overflow-y:auto`)、padding:12px、flex-column、gap:12px、background:#FCFBF8(→ `--pr-bg-feed`)。

1. 日時セパレータ(DateSeparator): 「今日 21:47」align-self:center、font-size:10px、color:#B0B4BA(→ `--pr-text-thumb`)。表示規則(決定): 直前メッセージと 10 分以上空いた場合に挿入。日付部は 今日/昨日/`M/D`、時刻は `HH:mm`(クライアント整形。plans/03 §1.6)。
2. ユーザーメッセージカード(UserMessageCard): background:#FFFFFF(→ `--pr-bg-card`)、border:1px solid #E2DFD5(→ `--pr-border-card`)、border-radius:8px、padding:10px 12px、flex-column、gap:6px。
   - ヘッダ行(flex、gap:6px): 根拠チップ = `EvidenceChip`(size='header')「式(5) · §2.1」: inline-flex、height:17px、padding:0 6px、border:1px solid var(--pr-am)、color:var(--pr-a)、background:var(--pr-as)、border-radius:4px、font-size:10px、font-weight:600。データ: `ChatMessage.context_anchors[].display`。右端(margin-left:auto)に「あなた」font-size:10px、color:#9A9EA4(→ `--pr-text-muted`)。
   - 引用ブロック: 「min_v ∫ 𝔼‖(X₁−X₀) − v(X_t, t)‖² dt …」font-family:'Source Serif 4'(→ `--pr-font-en`)、italic、font-size:10.5px、color:#777B81(→ `--pr-text-sub2`)、border-left:2px solid #D8D5CB(→ `--pr-border-quote`)、padding-left:8px。データ: `context_anchors[0].quote`(先頭 80 字+「 …」。決定: 80 字超は切り詰め)。
   - 本文: 「この式が最小化しているものを、直感的に説明して。」font-size:12.6px、line-height:1.7、color:#24272B(→ `--pr-text-body`)。
3. アシスタント回答(AssistantMessage。カードなし、flex-column、gap:7px、padding:0 2px):
   - ヘッダ行: 「✦ アシスタント」color:var(--pr-a)、font-size:11px、font-weight:700(`✦`= `AiMark` 共通)。バッジ「AI生成」= `AIBadge`(variant='generated'): inline-flex、height:15px、padding:0 5px、border:1px solid #DDD9CF(→ `--pr-border-control`)、border-radius:3px、font-size:9px、color:#8A8E94(→ `--pr-text-icon`)、font-weight:600。
   - 段落(MessageMarkdown。font-size:12.6px、line-height:1.85、color:#24272B): `MessageBlock{type:'markdown'}` の text をリッチな GFM Markdown として安全にレンダリングし、インライン/フェンスドコード、インライン/ブロック KaTeX、`[[ev:n]]` トークンの `EvidenceChip`(size='inline')展開に対応する。表・コード・ブロック数式は横スクロールラッパーに収め、生の HTML と画像取得は無効にする。
     - 段落1逐語: 「式(5)は「**位置と時刻だけから、直線補間の進む向きを当てる**」回帰です。ペア (X₀, X₁) を結ぶ補間 X_t の速度は常に X₁−X₀ なので、これを教師に v を最小二乗で学習します 」+ 根拠チップ「式(5)」「§2.1」。
     - 段落2逐語: 「複数の直線が同じ点を通る場合、v はそれらの向きの**条件付き期待値** 𝔼[X₁−X₀ | X_t] に収束します。交差する経路が「平均の向き」に置き換わるため、得られるODEの経路は交差しません 」+ 根拠チップ「§2.1 ¶4」「図2」。
   - インライン根拠チップ共通(`EvidenceChip` size='inline'): inline-flex、height:16px、padding:0 6px、border:1px solid var(--pr-am)、color:var(--pr-a)、background:var(--pr-as)、border-radius:4px、font-size:9.5px、font-weight:600、vertical-align:2px、2 個目以降 margin-left:3px。
   - 論文外知識ボックス(AsideBox): font-size:12.3px、line-height:1.8、color:#5B6067(→ `--pr-text-sub`)、background:#F5F3EC(→ `--pr-bg-knowledge`)、border-radius:6px、padding:8px 10px。先頭にラベルバッジ = `AIBadge`(variant='external')「論文外の知識」: inline-flex、height:15px、padding:0 5px、background:#E7E4DA(→ `--pr-bg-knowledge-label`)、border-radius:3px、font-size:9px、color:#6A6E74(→ `--pr-text-eq`)、font-weight:700、margin-right:5px、vertical-align:1px。本文逐語「実装では t を [0,1] から一様サンプリングし、ミニバッチでこの回帰を解くのが一般的です。」データ: `MessageBlock{type:'aside', label:'outside_knowledge'}`。`label:'speculation'` は同一様式でラベル「推測」(`AIBadge` variant='guess'。docs/05 §6)。
   - アクション行(MessageActions): flex、gap:12px、font-size:10.5px、color:#8A8E94(→ `--pr-text-icon`)、padding-top:2px。「↑ メモに保存」(color:var(--pr-a)、font-weight:600)/「再生成」/「コピー」。

**入力エリア(ChatInputArea)**: padding:10px 12px、border-top:1px solid #ECE9DF(→ `--pr-border-soft`)、flex-column、gap:8px、background:#FFFFFF(→ `--pr-bg-card`)。
- サジェストチップ行(SuggestChips。flex、flex-wrap:wrap、gap:5px)。各チップ: height:21px、inline-flex、padding:0 8px、border:1px solid #DDD9CF(→ `--pr-border-control`)、border-radius:999px、font-size:10.5px、color:#3C4046(→ `--pr-text-mid`)。文言(順序固定): 「3行要約」「初心者向け解説」「貢献と限界」「実験設定の整理」「実装の要点」。対応 `quick_action`: `summary_3line` / `beginner_explain` / `contributions_limits` / `experiment_setup` / `implementation_points`。
- 入力ボックス(ChatInputBox): flex、align-items:center、gap:8px、border:1px solid #DDD9CF(→ `--pr-border-control`)、border-radius:8px、padding:8px 10px。プレースホルダ「この論文について質問…」(flex:1、font-size:12px、color:#9A9EA4 → `--pr-text-muted`)。実装は自動伸長 textarea(1〜5 行、rows=1、max-height 5 行分で内部スクロール — 決定)。送信ボタン: 24×24px、border-radius:6px、background:var(--pr-a)、color:#FFFFFF、font-size:12px、グリフ「↑」。
- 免責文(固定・逐語): 「回答は原文を根拠にします。本文にない内容は「論文外の知識」「推測」と表示されます。」font-size:10px、color:#9A9EA4(→ `--pr-text-muted`)、line-height:1.5。

### 4.6 全 UI 文言(逐語一覧)

- ヘッダ: `‹` / `Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow` / `A`(title: `品質レベルA: LaTeXソースから完全構造化`)/ `読んでいる ▾` / `訳文` `対訳` `原文` `PDF` `記事` / `スタイル: 自然訳 ▾` / `この論文内を検索` + キーキャップ `/` / `⋯`
- 目次: `目次` / `翻訳 96%` `⟨⟨` / `アブストラクト` / `1 はじめに` / `2 手法` / `2.1 整流フロー` / `2.2 Reflow: 経路の直線化` / `2.3 蒸留との関係` / `3 実験` / `3.1 CIFAR-10 画像生成` / `3.2 高解像度画像への拡張` / `3.3 ドメイン転移` / `4 関連研究` / `5 結論` / `参考文献` / `付録 A 証明 — 未翻訳` / `開くと翻訳します(オンデマンド)` / `今日の読書 42分` / `位置は自動保存`
- 本文: `原文 — ENGLISH` / `訳文 — 自然訳 ✦ AI翻訳` / `段落対応 ⇄` / `2.1 整流フロー — Rectified Flow` / (§4.4 の段落逐語 4 ペア) / `✦ チャットの根拠 · 式(5)` / `(5)` / `✦ この式を説明` / `LaTeX をコピー`
- サイドパネル: `チャット` `メモ` `注釈 6` `図表` `リソース 4` `情報` / `スレッド: メイン ▾` / `コンテキスト: この論文` / `今日 21:47` / `式(5) · §2.1` / `あなた` / `min_v ∫ 𝔼‖(X₁−X₀) − v(X_t, t)‖² dt …` / `この式が最小化しているものを、直感的に説明して。` / `✦ アシスタント` / `AI生成` / (§4.5 の回答段落逐語 2 本) / `論文外の知識` / `実装では t を [0,1] から一様サンプリングし、ミニバッチでこの回帰を解くのが一般的です。` / `↑ メモに保存` `再生成` `コピー` / `3行要約` `初心者向け解説` `貢献と限界` `実験設定の整理` `実装の要点` / `この論文について質問…` / `↑`(送信)/ `回答は原文を根拠にします。本文にない内容は「論文外の知識」「推測」と表示されます。`

### 4.7 データフィールド対応表

| UI 要素 | データ源(plans/03 の型) |
|---|---|
| タイトル・品質 A・ステータス | `viewer.library_item.paper.title` / `.quality_level` / `.status` |
| 翻訳 96%・スタイル名 | `viewer.translation.progress_pct` / `.style` |
| 目次行(✓/バッジ/しおり/淡色/破線ボックス) | `TocNode.translated` / `.annotation_count` / `.bookmarked` / `.in_progress_denominator` / `.on_demand` |
| 今日の読書 42分 | `viewer.today_reading_minutes`(reading-sessions 応答で更新) |
| 段落原文・数式 | `GET …/document` の `Block`(`inlines` / `latex` / `number`) |
| 段落訳文 | `GET …/translations/{style}/units` の `text_ja`(`block_id` で結合) |
| ハイライト・丸数字 | `Annotation`(`anchor` / `color` / `comment`)。丸数字は表示中セクション内の comment 付き注釈の出現順連番(決定) |
| タブ件数 注釈 6 / リソース 4 | `viewer.counts.annotations` / `.resources` |
| スレッド名・メッセージ | `ChatThread.title` / `ChatMessage.blocks` / `.context_anchors` |
| 根拠チップ表記 | `AnchorRef.display`(サーバー導出。クライアントで整形しない) |

## 5. 状態とインタラクション

### 5.1 デザインに描かれた状態(1a の描画そのもの)

1. 表示モード「対訳」選択中(白背景+shadow+太字)、他 4 モード非選択。
2. ステータス「読んでいる」(アクセントドット付きピル、▾ で変更可)。
3. 品質バッジ「A」hover で title ツールチップ。
4. 目次「2.1 整流フロー」選択中(アクセント淡背景+inset 左 2px バー+太字)。
5. 目次の翻訳済み ✓ / 「参考文献」淡色 / 「付録 A 証明」破線ボックス(オンデマンド)。
6. 目次注釈数バッジ(1 はじめに=2、2.1=3、3.1=1)、「2.2」にブックマーク。
7. 式(5) がチャット根拠として参照中(アクセント淡背景+枠+浮きバッジ)。
8. 本文ハイライト 3 色(グレー用語 / 黄+丸数字「1」 / 青)。
9. チャット: ユーザー白カード(根拠チップ+引用)と AI 回答(カードなし+AI生成バッジ+インラインチップ+論文外知識ボックス)の 2 表現。
10. チャットタブ選択中(アクセント+下端 2px)。タブ件数 注釈 6 / リソース 4。

### 5.2 遷移・操作(確定挙動)

| 操作 | 挙動 |
|---|---|
| 「‹」 | `router.back()`。履歴なしは `/library` へ |
| ステータスピル ▾ | Popover(180px)で 6 値選択 → `PATCH /api/library-items/{id} { status }`。楽観更新+失敗時ロールバック+Toast(kind='error') |
| 表示モード切替 | `useViewerStore.mode` 変更+`?mode=` replaceState。現在の先頭可視ブロックへ位置を引き継ぐ |
| スタイル ▾ | natural⇄literal 切替。literal 未生成なら `POST …/translations` → 生成中は訳文列に「翻訳中…」 |
| `/` キー / 検索クリック | SearchBox フォーカス。入力 300ms デバウンスで `GET …/search?q=`。結果は Popover(width 300px、caret なし、placement='bottom-end' — InPaperSearch は viewer-shell §7 が正)にヒット行(display + snippet の `<mark>`)、クリックでブロックへジャンプ+一時ハイライト。Esc で閉じる |
| 目次「⟨⟨」 | `tocCollapsed=true` → 44px アイコンレール(☰/しおり/検索。1b 仕様)。☰ で再展開 |
| 目次行クリック | 該当セクション先頭へ `scrollIntoView({behavior:'auto'})`。未ロードなら §2.1 #2/#3 を取得してから。`on_demand` 節は plans/03 §7.5 API を併発 |
| スクロール | IntersectionObserver(rootMargin `0px 0px -70% 0px`)で先頭可視ブロック判定 → 目次選択行更新+5s デバウンス位置保存 |
| 「段落対応 ⇄」クリック | 対応ハイライトのトグル(`useViewerStore.pairSyncEnabled`。ON: ホバー中の段落ペア左右両方に background:var(--pr-bg-hover)。既定 ON — 決定)。ボタンは `aria-pressed` を持ち、ON はデザイン通り color:#B6BAC0、OFF は同色で opacity:0.55(決定) |
| 「✦ この式を説明」 | ブロック全体 Anchor(`start:null,end:null,quote:数式プレーンテキスト`)を `pendingAnchors` に積み、チャットタブへ切替+入力欄フォーカス。1a のチャットはこの操作で「この式が最小化しているものを、直感的に説明して。」を送信した後の状態 |
| 「LaTeX をコピー」 | `navigator.clipboard.writeText(block.latex)` → Toast(kind='success', message='LaTeX をコピーしました') |
| 訳文内「図2」「式(5)」 | その場ポップオーバー(1c 仕様の図表参照 Popover、width 400)。スクロールしない(docs/04 §7) |
| 「[12]」 | サイドパネルを図表タブへ切替+参考文献該当項目を展開(docs/04 §7 決定) |
| 黄ハイライト+丸数字「1」 | クリックで注釈タブへ切替+該当注釈カードへスクロール |
| テキスト選択 | `SelectionMenu`(共通)表示: 4 色ドット / コメント / ✦ AIに質問 / 語彙に追加 / コピー。「✦ AIに質問」は選択範囲の Anchor(quote=選択テキスト)を `pendingAnchors` に積み、チャットタブへ切替+入力欄フォーカス(「✦ この式を説明」と同じ流れ — 決定)。「語彙に追加」は原文選択時のみ活性(docs/04 §8) |
| 根拠チップ(チャット側) | `onEvidenceJump`: 対象ブロックへスクロール+`flashBlockId` 設定。一時ハイライト: background を var(--pr-acc-s) にして 2,000ms 後に 400ms でフェード解除(決定)。equation/figure アンカーは `chatEvidenceBlockId` も更新し「✦ チャットの根拠」常時強調を移す |
| 本文側「✦ チャットの根拠」強調の決定規則 | `chatEvidenceBlockId` の初期値 = アクティブスレッド最新ユーザーメッセージの `context_anchors[0].block_id`(1a では式(5))。`context_anchors` が空(または `context_anchors` を持つユーザーメッセージが無い)場合は `null`(強調なし。決定)。以後はチップクリックで更新。スレッド切替でリセットして再計算(決定) |
| スレッド ▾ | Popover でスレッド切替/新規作成。切替で `chatMessages` を新スレッドで取得 |
| サジェストチップ | `POST …/messages { content:'', quick_action }` を即送信(入力欄経由しない — 決定。P2 の 1 アクション原則) |
| 送信(「↑」/ Enter) | `content` + `pendingAnchors`(あれば)を送信。Shift+Enter は改行、IME 変換中(`KeyboardEvent.isComposing === true`)の Enter は送信しない(決定)。送信後 `pendingAnchors` クリア |
| 「↑ メモに保存」 | `POST …/notes { content_md, source_message_id }`(anchors はサーバー複写)→ Toast(kind='success', message='メモに保存しました', action={label:'メモを開く'}) |
| 「再生成」 | `POST /api/chat/messages/{message_id}/regenerate`(SSE)。旧回答は残し新回答を追加(plans/03 §10.4) |
| 「コピー」 | 回答 Markdown をコピー(根拠チップは display のテキスト展開。docs/05 §6)→ Toast(kind='success', message='コピーしました') |

### 5.3 チャットストリーミング状態(決定)

- 送信直後: ユーザーカードを楽観追加(`start` イベントの `user_message_id` で確定 ID に差替)。アシスタント位置にヘッダ行(「✦ アシスタント」+「AI生成」)+本文空の要素を追加し、`delta` を逐次追記(KaTeX/チップ置換は受信テキストの段落確定ごとに実行)。
- ストリーミング中: 送信ボタン・サジェストチップは非活性(opacity:0.45、cursor:default)。アクション行は `done` 後に表示。自動スクロールは最下部追随(ユーザーが 80px 以上上へスクロールしたら追随停止、最下部復帰で再開 — 決定)。
- `evidence` イベント: 対応 `[[ev:n]]` を `EvidenceChip` に置換(anchor 実在検証はサーバー側。plans/03 §10.3)。
- `error` イベント / 接続断: アシスタント位置に Problem.title(例「回答の生成に失敗しました」)を font-size:12.6px、color:var(--pr-warn) で表示し、右に「再試行」リンク(color:var(--pr-a)、font-weight:600)。「再試行」= 失敗したアシスタントメッセージの `POST /api/chat/messages/{message_id}/regenerate`(失敗回答は履歴に残り `message_id` を持つため。決定)。失敗回答は履歴に残す(P3)。`done` 不達時は 2,000ms 後に `chatMessages` を invalidate して確定形を取得。
- 初回トークン p50 5 秒(docs/05 §9)。`start` 受信から最初の `delta` まで、アシスタントヘッダ下に「…」(3 ドット、font-size:12.6px、color:var(--pr-text-muted)、400ms 間隔で透明度パルス)を表示(決定)。

### 5.4 デザイン未描画の必須状態(決定)

| 状態 | 決定内容 |
|---|---|
| 初期ローディング(viewer 取得中) | ヘッダ・3 ペインの骨格は即描画。目次: 幅 88〜168px(行ごとに 168/120/88/152px の循環)× h13px、border-radius:4px、background:var(--pr-bg-muted) のバー 13 本(gap 9px)。本文: 2 カラムグリッドに段落 4 組分のスケルトン(各段落 = 幅 100%×3 本+幅 62%×1 本、h13px、gap 8px、同色)。チャット: メッセージ域中央に何も出さず入力エリアのみ描画。すべて `animation: alinea-pulse 1.2s ease-in-out infinite`(opacity 1→0.55→1) |
| セクション本文の追加ロード | 対象セクション位置に上記段落スケルトン 2 組。取得失敗時はその位置に `EmptyState`(title='本文を読み込めませんでした', action={label:'再試行'}) |
| 訳文未翻訳ブロック | 右列に原文はそのまま、訳セルに「翻訳中…」font-size:12px、color:var(--pr-text-muted)(docs/04 §2)。`quality_flags` に `placeholder_mismatch` を含み `text_ja:null` の場合(`text_ja:null` で返るのはこのフラグのみ。plans/03 §7.2)は「この段落の翻訳に失敗しました · 再翻訳」(再翻訳リンクは color:var(--pr-a)) |
| チャット履歴なし | メッセージ域中央に `EmptyState`(共通): title「まだ会話がありません」、description「下の定型チップか入力欄から、この論文について質問できます。」 |
| viewer 取得エラー | 画面中央に `EmptyState`: title「論文を読み込めませんでした」、description=Problem.title、action={label:'再試行', onClick:refetch} |
| 送信ボタン非活性 | 入力空 or ストリーミング中: opacity:0.45、cursor:default、クリック無効 |
| ホバー | 目次行: background:var(--pr-bg-muted)(#EFEDE6)。サジェストチップ・アクションボタン(この式を説明/LaTeX をコピー): border-color:var(--pr-acc-m)、color は変更なし。チャットアクション行リンク: text-decoration:underline。セグメント非選択: color:var(--pr-text)。根拠チップ: background:var(--pr-acc-m) の 50% = そのまま var(--pr-acc-s) 維持で border-color:var(--pr-acc)(いずれも transition:120ms ease-out) |
| キーボードフォーカス | 全インタラクティブ要素 `focus-visible`: outline:1.5px solid var(--pr-acc)、outline-offset:1px(plans/08 §5 共通) |
| オフライン/位置保存失敗 | 位置保存・reading-sessions の失敗はリトライ(次回デバウンス発火に相乗り)し、UI 通知しない(読書を妨げない。P6) |
| ダークモード | トークン参照で自動追随(1c が正)。本画面固有の分岐コードは書かない |

### 5.5 キーボードショートカット(この画面で有効)

`/`=論文内検索フォーカス、`t`=対訳ポップ(訳文モードのみ。対訳モードでは無効)、`m`=表示モード循環(セグメント順: 訳文→対訳→原文→PDF→記事→訳文。決定)、`c`=チャットタブ、`b`=現在セクションのブックマークトグル、`j`/`k`=段落移動(docs/04 §14)。入力要素フォーカス中は無効。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Playwright + Storybook VRT(plans/08 §9)。フィクスチャは extract/1a.md の描画データ(Rectified Flow 論文・段落 4 ペア・式(5)・チャット 2 往復)を API モックで再現し、1440×900 で確定デザイン 1a とスクリーンショット比較する。

- [ ] ヘッダ h=52px、各要素の寸法・フォント・色が §4.2 と一致(タイトル max-width:330px の ellipsis、セグメント選択中の shadow 含む)
- [ ] 目次ペイン w=232px、選択行の inset 2px バー+アクセント淡背景、注釈数バッジ(15px 丸)、ブックマーク SVG、「参考文献」の淡色、破線ボックスの余白(margin:8px 6px 0 / padding:8px 9px)が一致
- [ ] 対訳グリッドが 1fr 1fr / column-gap:34px / row-gap:18px で、原文 13.8px/1.72 Source Serif 4・訳文 14.8px/2.0 Noto Serif JP が一致
- [ ] 式(5) 強調状態: 全幅・padding:16px 20px・var(--pr-as) 地+var(--pr-am) 枠+radius 8px、浮きバッジ top:-9px right:14px h18px、式番号 right:18px 13px #6A6E74 が一致
- [ ] ハイライト 3 色の background 値(rgba(130,130,126,0.18) / rgba(196,148,50,0.26) / rgba(88,132,170,0.22))と丸数字チップ 14×14px vertical-align:4px が一致
- [ ] サイドパネル w=340px、タブ行(選択中 inset 0 -2px)、スレッド行、メッセージ域 background #FCFBF8、ユーザーカード(border #E2DFD5、radius 8px)、根拠チップ(h17px/h16px、font 10px/9.5px)、論文外知識ボックス(#F5F3EC、ラベル #E7E4DA)が一致
- [ ] 入力エリア: サジェストチップ h21px radius 999px、入力ボックス radius 8px、送信ボタン 24×24px アクセント地、免責文 10px が一致
- [ ] §4.6 の全 UI 文言が逐語一致(句読点・中黒・スペース含む)
- [ ] ダーク(data-theme="dark")・アクセント 4 色切替で新規ハードコード色が現れない(トークン追随)

### 6.2 機能検証

- [ ] `/papers/{itemId}?mode=parallel` で認証済みユーザーに本画面が表示され、未認証は `/login?next={戻り先パス}` へリダイレクトされる
- [ ] 表示モード 5 値がワンクリックで切替わり、`?mode=` が replaceState で追随し、読書位置が保たれる
- [ ] 目次行クリックで該当セクションへジャンプし、スクロールで目次の選択行が追随する
- [ ] `on_demand` の付録を開くと plans/03 §7.5 API が発火し、ジョブ SSE で「翻訳 N%」と ✓ が更新される
- [ ] 読書位置が 5 秒デバウンスで保存され(`PUT …/position`)、reading-sessions ハートビートで「今日の読書 N分」が増える(設定オフ時は送信されない)
- [ ] 「✦ この式を説明」で式(5) のアンカーが引用チップとして積まれ、送信後のユーザーカードにチップ「式(5) · §2.1」+引用プレビューが表示される
- [ ] チャット送信が SSE で逐次表示され、`[[ev:n]]` が `evidence` イベントで根拠チップに置換される。チップクリックで本文該当ブロックへジャンプ+2 秒の一時ハイライト
- [ ] チャットが参照中の式(5) が本文側でアクセント強調+「✦ チャットの根拠 · 式(5)」バッジ付きで表示され、チップクリックで強調対象が移る(双方向リンク)
- [ ] `aside` ブロックが「論文外の知識」(speculation は「推測」)ラベル付きボックスで区別表示される
- [ ] 「↑ メモに保存」でメモが作成され(根拠アンカー引継ぎ)、成功 Toast にアクション「メモを開く」が出る。「再生成」で旧回答が残ったまま新回答が追加される。「コピー」で根拠チップがテキスト展開された Markdown が得られる
- [ ] サジェストチップ 5 種が `quick_action` 付きで即送信される。ストリーミング中は送信・チップが非活性
- [ ] SSE 切断時、確定メッセージが `GET …/messages` の再取得で復元される。`error` イベントで失敗回答が「再試行」付きで履歴に残る(P3)
- [ ] `/` で検索フォーカス、ヒットクリックでブロックへジャンプする。`Esc` で閉じる
- [ ] ステータスピルから 6 値を変更でき、失敗時にロールバック+エラー Toast が出る
- [ ] 選択メニューからハイライト作成 → 本文・目次バッジ・注釈タブ件数が即時更新される
- [ ] KaTeX 数式に LaTeX ソースの `aria-label` が付き、「LaTeX をコピー」が成功 Toast を出す
