# 画面 4a: ライブラリ カード+通知ポップオーバー

> 対象読者と前提: 本書は「Alinea — 論文読解ワークベンチ」の apps/web(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4)実装者向けに、確定デザイン画面 4a(ライブラリ カードビュー+通知ポップオーバー)をピクセル一致で実装するための完全仕様である。機能仕様は docs/06(ライブラリと進捗管理)・docs/02(取り込み)を正、ピクセル値は抽出ファイル extract/4a.md を正とする。共通コンポーネント名は plans/08-design-system.md、API 名は plans/03-api.md、データ型は plans/03 §1.7・plans/02 のものを必ず使う。本書に書かれた値・識別子・文言が実装の正であり、独自の解釈・丸めを禁止する。

## 1. 概要とルート

- **ルートパス(確定)**: `/library`(`apps/web/src/app/(app)/library/page.tsx`)。1b の戻り先 `/library` と同一ルート。
  - **ビュー切替**はクエリパラメータ `?view=card|table` で表現する。`card` が本画面 4a、`table` が画面 1e。決定: **省略時は常に `card`。localStorage は使わない**(1e 計画書 §1 の決定と同一。localStorage による既定切替は SSR とのハイドレーション不整合を生むため、状態は URL のみで表現する)。URL 変更は `router.replace`(履歴を積まない)。
  - フィルタ・ソートもクエリパラメータで表現する: `?quick=all|unread|in_progress|done|recheck`(既定 `all`)、`?sort=updated_at|added_at|title|deadline|reading_time|comprehension|priority`(既定 `updated_at`)、`?order=desc|asc`(sort キーごとの既定方向は 1e 計画書 §5.4 の表と同一 — `title`・`deadline` のみ既定 `asc`、他 5 キーは既定 `desc`。決定)、`?filter_id=sf_…`(保存フィルタ適用)、`?collection_id=col_…`・`?tag=`・`?status=`・`?quality=`・`?year=`(属性フィルタ。1e 側で操作するが URL 語彙は共通)。クエリ語彙は `GET /api/library-items`(plans/03 §5.1)のクエリと 1:1 対応。決定: `view`/`quick`/`sort`/`order` に語彙外の値が指定された場合は各パラメータの既定値として扱う(URL の書き換えは行わない)。
- **認証**: 必須(session Cookie)。未認証は `/login?next=/library` へ 302(App Router の `middleware.ts` で判定)。
- **画面の役割**: ライブラリの既定ビュー。サムネイル+書誌+✦AI要約+ステータス/タグ/締切/メタで「積んだ論文の顔」を一覧し(docs/06 §8.2)、クイックフィルタ 5 種で絞り込む。同時に本画面はアプリ共通トップバーの**通知ポップオーバー**(翻訳完了/ステータス変更提案/締切リマインド。docs/06 §7)の基準仕様を確定させる画面である。トップバー(`AppTopBar`)・サイドバー(`SidebarNav`)・通知(`NotificationBell` 一式)は 1d/1e/4a/4d/4e 共通の `LibraryShell`(`apps/web/src/components/shell/LibraryShell.tsx`。1e 計画書 §1・4d・4e と同一の名前とパス)として実装し、スタイル・挙動の仕様は本書が正とする。
- 関連ルート(サイドバーの遷移先。確定): ホーム=`/`(1d)、語彙帳=`/vocab`(4d)、コレクション=`/collections/[collectionId]`(4b)、横断検索=`/search`(4e)、設定=`/settings`(4f)、エクスポート=`/settings?category=export`(4f のエクスポートカテゴリを開く。決定: 専用ルートは作らない)。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 用途 | 取得/実行タイミング |
|---|---|---|---|
| 1 | `GET /api/library-items` | カード一覧(`quick`/`sort`/`order`/`filter_id` 等+`cursor`/`limit=50`) | ルート表示時+クエリパラメータ変化時。無限スクロール(§2.4) |
| 2 | `GET /api/library-items/facets` | クイックフィルタ件数(すべて 41/未読 12/途中 4/読了 23/要再確認 2)+見出しの総数「41 本」(`quick.all`) | ルート表示時+フィルタ変化時(`quick` 以外のフィルタを引数に渡す) |
| 3 | `GET /api/collections` | サイドバー「コレクション」(名前・締切 `deadline`/`days_left`・件数 `item_count`) | LibraryShell マウント時 |
| 4 | `GET /api/saved-filters` | サイドバー「保存フィルタ」(名前・`count`) | LibraryShell マウント時 |
| 5 | `GET /api/vocab?limit=1` | サイドバー「語彙帳」件数(`counts.all`=46) | LibraryShell マウント時(items は捨て、counts のみ使用。決定: 件数専用 API は追加しない) |
| 6 | `GET /api/auth/me` | 未読通知数 `unread_notifications`(ベルの琥珀ドット)+アバターイニシャル | LibraryShell マウント時 |
| 7 | `GET /api/notifications` | 通知リスト(`limit` 既定 20)+`unread` | ◷ ボタンで開いた時(開くたび再取得。staleTime 0) |
| 8 | `POST /api/notifications/read-all` | 「すべて既読にする」 | ヘッダリンククリック時 |
| 9 | `PATCH /api/notifications/{notification_id}` | 個別既読化(`{ read: true }`) | 通知内リンク(「読み始める →」「コレクションを開く →」)クリック時に fire-and-forget |
| 10 | `POST /api/notifications/{notification_id}/action` | ステータス提案の 2 択(`{ action: "apply" \| "dismiss" }`) | 「変更する」/「そのまま」クリック時 |
| 11 | `GET /api/search/preview?q=`(plans/03 §15.2。`limit` 固定 3) | グローバル検索ドロップダウン(⌘K。200ms デバウンス。1e 計画書と同値) | SearchBox 入力時(ドロップダウン仕様は 1e 計画書に委譲) |
| 12 | `GET /api/events` | ユーザー単位 SSE(`notification.created` / `job.progress`) | LibraryShell マウント時に常時接続(plans/01 §5) |

- カードのフィールドはすべて `LibraryItemSummary`(plans/03 §1.7)から取る。カード用の追加 API は無い。
- カードクリック → `/papers/{id}`(1b)へ `router.push`。API 呼び出しなし(位置復元はビューア側)。

### 2.2 TanStack Query キー設計(確定)

`apps/web/src/lib/query-keys.ts` の `qk` に追記する(1b で確立済みの集約方針。文字列リテラル直書き禁止)。

```ts
export const qk = {
  // …1b 定義分…
  me:            () => ['me'] as const,
  library:       (params: LibraryQueryParams) => ['library', params] as const,          // useInfiniteQuery
  libraryFacets: (params: Omit<LibraryQueryParams, 'quick' | 'sort' | 'order'>) =>
                   ['library-facets', params] as const,
  collections:   () => ['collections'] as const,
  savedFilters:  () => ['saved-filters'] as const,
  vocabCounts:   () => ['vocab-counts'] as const,
  notifications: () => ['notifications'] as const,
};

// URL クエリと 1:1。undefined キーは含めない(キー安定化のため正規化してから渡す)
export type LibraryQueryParams = {
  quick: 'all' | 'unread' | 'in_progress' | 'done' | 'recheck';
  sort: 'updated_at' | 'added_at' | 'title' | 'deadline' | 'reading_time' | 'comprehension' | 'priority';
  order: 'desc' | 'asc';
  filter_id?: string;
  collection_id?: string;
  status?: string[];
  tag?: string[];
  quality?: 'A' | 'B';
  year?: number[];
  q?: string;
};
```

- `staleTime`(決定): `library`=30_000ms / `libraryFacets`=30_000ms / `collections`・`savedFilters`・`vocabCounts`=60_000ms / `me`=60_000ms / `notifications`=0(開くたび最新)。
- invalidate 規則(決定):
  - 通知アクション(#10)成功 → レスポンスの `notification` を `qk.notifications()` に `setQueryData` で差し替え+`library_item` が返れば `qk.library(*)`・`qk.libraryFacets(*)` を invalidate(ステータスが変わるため)+`qk.me()` を invalidate(未読数)。
  - `read-all`(#8)成功 → `qk.notifications()` の全項目を `read: true` に楽観更新+`qk.me()` の `unread_notifications` を 0 に `setQueryData`。
  - 個別既読(#9)→ `qk.notifications()` 該当項目を楽観更新(失敗時もロールバックしない。既読化の失敗は無害なため。決定)。

### 2.3 リアルタイム更新

- **ユーザー SSE**: LibraryShell が `GET /api/events`(plans/01 §5。`text/event-stream`、`Last-Event-ID` 再開対応)に EventSource で常時接続する。
  - `event: notification.created`(`{notification_id, kind, payload}`)受信 → `qk.me()` を invalidate(ベルの未読ドット即時点灯)。ポップオーバーが開いていれば `qk.notifications()` も invalidate。
  - `event: job.progress` / `job.failed` 受信で `library_item_id` が現在の一覧に含まれる場合 → `qk.library(*)` を invalidate(翻訳完了で `pipeline` が null になりカード表示が確定するため)。
- **ポーリングフォールバック**(発動条件・間隔は plans/01 §5 の決定どおり): EventSource が 3 回連続で接続失敗した場合、`qk.me()` に `refetchInterval: 30_000` を有効化(未読ドット)。決定: plans/01 §5 は通知バッジのポーリング先に `GET /api/notifications?unread_only=true` を挙げるが、`unread_only` は plans/03 §16.1 に未定義のため、本シェルでは未読数を含む `GET /api/auth/me`(`qk.me()`)のポーリングで代替する。SSE 復帰で解除。カード一覧はフォールバック時ポーリングしない(手動リロードで足りる。決定)。

### 2.4 無限スクロール(カード一覧)

`useInfiniteQuery` + cursor 方式(plans/03 §1.5)。`limit=50` 固定。グリッド末尾に高さ 1px の番兵 div を置き、IntersectionObserver(`root` = グリッドのスクロールコンテナ、`rootMargin: '600px 0px'`)で `fetchNextPage()`。取得中はグリッド末尾にスケルトンカード 3 枚(§5.1)を表示する。`next_cursor: null` で番兵を外す。決定: 仮想化(react-virtual 等)は導入しない。理由: 1 ページ 50 件×3 列のカード DOM は軽量で、docs/09 の性能目標に対し過剰設計となるため。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

■=画面固有(`apps/web/src/features/library/` および `apps/web/src/components/shell/` 配下)、□=共通(plans/08 §5・§6 の名前)。

```
■ LibraryPage(page.tsx。Server Component: searchParams の解決のみ)
└─ ■ LibraryShell(client。1d/1e/4a/4d/4e 共通骨格。components/shell/)
   ├─ ■ AppTopBar(h52)
   │  ├─ ■ AppLogo(「A」マーク+「Alinea」)
   │  ├─ □ SearchBox(variant='global')+ ■ SearchDropdown(1e 計画書の固有コンポーネント。仕様・実装は 1e 計画書 §4.3・§5.3)
   │  ├─ ■ NotificationBell(◷+未読ドット)
   │  │  └─ □ Popover(width 352, placement='bottom-end', caretOffset={side:'right', px:26})
   │  │     └─ ■ NotificationPopover
   │  │        ├─ ■ NotificationPopoverHeader(「通知」/「すべて既読にする」)
   │  │        └─ ■ NotificationItem × n(kind 別 3 変種。□ AiMark)/ □ EmptyState
   │  └─ ■ UserAvatar(「YK」)
   ├─ □ SidebarNav(w216。main/sections/footer)
   └─ ■ LibraryView(features/library/。view=card 時)
      ├─ ■ LibraryHeaderRow
      │  ├─ 見出し「ライブラリ」+件数「41 本」
      │  ├─ □ SegmentedControl(size='sm', options=カード/テーブル)
      │  └─ ■ SortMenu(「並び: 更新日 ▾」+ □ Popover width 180)
      ├─ ■ QuickFilterChips(□ FilterChip size='md' × 5)
      └─ ■ PaperCardGrid(3 列グリッド+番兵)
         ├─ ■ PaperCard × n
         │  ├─ □ Card(padding='none')
         │  ├─ ■ CardThumbnail(+ ■ CardQualityMonogram)
         │  ├─ □ AiMark(✦)
         │  ├─ □ ProgressBar(color='accent', height=3)
         │  ├─ □ StatusPill(size='sm', variant='pill', interactive=false)
         │  ├─ □ TagChip / □ DeadlineBadge(variant='chip', withLabel)/ □ PriorityBadge(withPrefix)
         │  └─ (右端メタテキスト)
         ├─ ■ PaperCardSkeleton × n(ローディング)
         └─ □ EmptyState(空/エラー)
```

- `view=table` 時は `LibraryView` が `LibraryTable`(□。plans/08 §5.15)側ツリーに切り替わる(1e 計画書)。`LibraryHeaderRow`・`QuickFilterChips` は両ビュー共通。

### 3.2 画面固有コンポーネントの props 型

```ts
import type { LibraryItemSummary, Notification, ReadingStatus } from '@alinea/api-client';
import type { LibraryQueryParams } from '@/lib/query-keys';

// components/shell/LibraryShell.tsx
interface LibraryShellProps {
  activeNav: 'home' | 'library' | 'vocab' | `collection:${string}` | `filter:${string}` | 'settings';
  sidebar?: React.ReactNode;   // 既定 = □ SidebarNav。4e が SearchFacetRail を渡す(4e 計画書 §1 の決定)。4a では未指定
  children: React.ReactNode;
}

// components/shell/NotificationBell.tsx(状態は内部 useState。グローバルストア不使用)
interface NotificationBellProps { unreadCount: number } // qk.me() の unread_notifications

// components/shell/NotificationPopover.tsx
interface NotificationPopoverProps { onClose: () => void }

// components/shell/NotificationItem.tsx
interface NotificationItemProps {
  notification: Notification;                       // plans/03 §16.1 の判別共用体
  onNavigate: (href: string) => void;               // 既読化(fire-and-forget)+ router.push + onClose
  onSuggestionAction: (id: string, action: 'apply' | 'dismiss') => void;
}

// features/library/LibraryView.tsx
interface LibraryViewProps { params: LibraryQueryParams; view: 'card' | 'table' }

// features/library/SortMenu.tsx
interface SortMenuProps {
  sort: LibraryQueryParams['sort'];
  order: LibraryQueryParams['order'];
  onChange: (sort: LibraryQueryParams['sort'], order: LibraryQueryParams['order']) => void;
}

// features/library/QuickFilterChips.tsx
interface QuickFilterChipsProps {
  active: LibraryQueryParams['quick'];
  counts: { all: number; unread: number; in_progress: number; done: number; recheck: number } | undefined; // facets 取得前 undefined
  onChange: (quick: LibraryQueryParams['quick']) => void;
}

// features/library/PaperCard.tsx
interface PaperCardProps {
  item: LibraryItemSummary;
  onOpen: (id: string) => void;   // /papers/{id}
}

// features/library/CardThumbnail.tsx
interface CardThumbnailProps {
  thumbnailUrl: string | null;
  quality: 'A' | 'B';
  alt: string;                    // 論文タイトル
}
```

- ソート表示ラベル対応(固定): `updated_at`=更新日 / `added_at`=追加日 / `title`=タイトル / `deadline`=締切 / `reading_time`=読書時間 / `comprehension`=理解度 / `priority`=優先度。

## 4. レイアウト・スタイル完全仕様(extract/4a.md 全量)

値の後ろの `--pr-*` は plans/08 §2 のトークン名。実装はトークン(Tailwind ユーティリティ §4.3 規約)で書き、hex 直書きは禁止。

### 4.0 デザイナー注記(デザインカタログ上の逐語。アプリには実装しない)

- バッジ `4a`(アンカーリンク href="#4a"、inline-flex 中央、min-width:32px、height:22px、background:#2B2E33、color:#FFFFFF、border-radius:6px、font-size:12px、font-weight:700)/ タイトル「ライブラリ — カードビュー+通知」(15px/700、#1E2227)/ 説明「サムネイル+書誌+3行要約+ステータス(仕様06 §5) / 通知ポップオーバー(翻訳完了・ステータス提案・締切)」(12px、#777B81)。ルート div: `id="4a"`、`data-screen-label="4a ライブラリカード"`、width:1440px。フレーム外の別置き要素は無し(通知ポップオーバーはフレーム内 absolute)。

### 4.1 フレームと全体構造

デザインフレーム: 1440×900px、background:#F4F3EF(`--pr-bg-app-alt`)、border:1px solid #D6D3C9、border-radius:10px、box-shadow:0 20px 44px rgba(28,30,34,0.12)、overflow:hidden、縦 flex、color:#1E2227(`--pr-text`)、position:relative。実アプリではフレーム装飾(border/radius/shadow)を付けずビューポート全面に描画する(plans/08 §7.1)。

```
┌──────────────────────────────────────────────────────────────── 1440px ─┐
│ トップバー h:52px #FFFFFF 下線1px #E6E3DA                                │
│ [Aロゴ+Alinea w:198] [検索バー w:460 h:32] [flex:1] [◷通知30×30] [YK30×30]│
├──────────────┬───────────────────────────────────────────────────────────┤
│ サイドバー    │ メインエリア(flex:1, padding:16px 22px, 縦flex gap:12px) │
│ w:216px      │  ┌ 見出し行: ライブラリ / 41本 / [カード|テーブル] / 並び ┐│
│ #F7F6F2      │  ├ フィルタチップ行: すべて41/未読12/途中4/読了23/要再確認2││
│ 右線1px      │  ├ カードグリッド 3列 1fr×3 gap:14px overflow-y:auto      ││
│ #E7E4DB      │  │  [カード×6: サムネ108px + 本文]                        ││
│              │  └─────────────────────────────────────────────────────── ┘│
└──────────────┴───────────────────────────────────────────────────────────┘
  ※通知ポップオーバー: position:absolute; top:50px; right:56px; width:352px; z-index:6
    (トップバーの◷アイコンの直下に重なって表示。上向き矢印つき)
```

- トップバー: height:52px、flex:none、background:#FFFFFF(`--pr-bg-card`)、border-bottom:1px solid #E6E3DA(`--pr-border-header`)、display:flex、align-items:center、gap:14px、padding:0 18px。
- 本体行: flex:1、display:flex、min-height:0。
- サイドバー: width:216px、flex:none、background:#F7F6F2(`--pr-bg-pane`)、border-right:1px solid #E7E4DB(`--pr-border-pane`)、padding:12px 10px、縦 flex、gap:2px、font-size:12.5px、color:#3A3E44(`--pr-text-nav`)。= □ SidebarNav 既定スタイルそのまま。
- メインエリア: flex:1、min-width:0、padding:16px 22px、縦 flex、gap:12px、overflow:hidden。
- カードグリッド: display:grid、grid-template-columns:1fr 1fr 1fr、gap:14px、overflow-y:auto、min-height:0、align-content:start、padding:2px 2px 8px。1440px 超でも 3 列固定でカードが広がる(plans/08 §7.2)。

### 4.2 トップバー(■ AppTopBar、h:52px)

1. **■ AppLogo**(display:flex; align-items:center; gap:8px; width:198px)
   - ロゴマーク「A」: inline-flex 中央、22×22px、border-radius:6px、background:var(--pr-a)(既定 #3E5C76)、color:#FFFFFF、font-size:11.5px、font-weight:700。
   - ワードマーク「Alinea」: font-size:14.5px、font-weight:700、letter-spacing:0.5px。
   - 全体を `/` へのリンク(`<Link href="/">`)にする(決定)。
2. **□ SearchBox(variant='global')**(display:flex; align-items:center; gap:8px; height:32px; width:460px; background:#F1EFE9=`--pr-bg-inset`; border-radius:7px; padding:0 12px; font-size:12px; color:#8A8E94=`--pr-text-icon`)
   - 先頭に □ MagnifierIcon 12×12(viewBox 0 0 12 12、circle cx=5 cy=5 r=3.6 stroke=currentColor stroke-width=1.3 + path "M8 8l2.6 2.6" stroke-width=1.3 linecap:round)。
   - プレースホルダ文言(逐語): `ライブラリ全体を検索 — 本文・訳文・メモ・チャット`
   - □ Keycap「⌘K」: margin-left:auto、border:1px solid #DAD7CD(`--pr-border-keycap`)、border-radius:3px、padding:0 5px、font-size:9.5px、background:#FFFFFF(`--pr-bg-control`)、font-family:var(--pr-font-mono)。
3. スペーサー(flex:1)。
4. **■ NotificationBell**「◷」: position:relative、inline-flex 中央、30×30px、border-radius:7px、font-size:13px。
   - **アクティブ状態(ポップオーバー開)**: border:1px solid var(--pr-am)(rgba(62,92,118,0.32))、background:var(--pr-as)(rgba(62,92,118,0.10))、color:var(--pr-a)。デザインはこの状態で描画。
   - 非アクティブ状態(決定: デザイン未描画): border:1px solid transparent、background:none、color:#5B6067(`--pr-text-sub`)。ホバー: background:var(--pr-bg-inset)。理由: 1a 等のヘッダアイコンボタンの静止表現と揃え、開閉が枠+淡面の出現で明確に分かる。
   - 未読ドット: position:absolute、top:5px、right:5px、6×6px、border-radius:50%、background:#C49432(`--pr-amber`)。`unread_notifications > 0` で表示。
5. **■ UserAvatar**「YK」: inline-flex 中央、30×30px、border-radius:50%、background:var(--pr-as)、color:var(--pr-a)、font-size:11px、font-weight:700。表示文字は `display_name` から導出したイニシャル 2 文字(決定: 空白区切り先頭 2 語の頭文字を大文字で。1 語なら先頭 2 文字)。`avatar_url` 非 null なら円形 `<img>` に置換。

### 4.3 通知ポップオーバー(■ NotificationPopover。□ Popover 上に描画)

- コンテナ(□ Popover): position 実装は fixed(plans/08 §5.10)だが結果座標はデザインと一致させる — **トップバー下端-2px(=viewport top 50px)、viewport 右端から 56px**、width:352px、background:#FFFFFF(`--pr-bg-pop`)、border:1px solid #DDD9CF(`--pr-border-pop`)、border-radius:10px、box-shadow:0 24px 56px rgba(28,30,34,0.18)(`--pr-shadow-pop`)、z-index:var(--z-popover)(=6)、overflow:hidden。`placement='bottom-end'` で ◷ ボタン(right:62px 位置)にアンカーし右端 56px を得る。
- キャレット: absolute、top:-5px、right:26px、9×9px、background 同面色、border-left+border-top:1px solid #DDD9CF、transform:rotate(45deg)。`caretOffset={side:'right', px:26}`。
- **ヘッダ行(■ NotificationPopoverHeader)**(display:flex; align-items:center; padding:10px 14px; border-bottom:1px solid #F0EDE4=`--pr-border-hair`)
  - 「通知」: font-size:12px、font-weight:700。
  - 「すべて既読にする」: margin-left:auto、font-size:10.5px、color:var(--pr-a)、font-weight:600。`<button>`。決定: 未読 0 件・ローディング・空・エラーのいずれの状態でも常に表示・活性のまま(API は冪等で `updated: 0` が返るだけのため。disabled 切替は行わない)。
- **通知リスト**(縦 flex)。各 ■ NotificationItem: display:flex、gap:10px、padding:10px 14px。決定: リストは max-height:420px、overflow-y:auto(収まらない分は内部スクロール。デザインは 3 件のため未描画)。表示は最初のページ(最新 20 件)のみで、ポップオーバー内のページング・追加読み込みは行わない(決定。それ以前の通知は既読化により実質不要になるため)。
- 未読項目共通: border-bottom:1px solid #F4F1E9(`--pr-border-row`)、background:#FCFBF6(`--pr-bg-unread`)、未読ドット 7×7px、border-radius:50%、background:var(--pr-a)、flex:none、margin-top:5px。
- 既読項目共通: border-bottom・background なし(白)。ドット位置ホルダ: 7×7px、background:transparent(位置だけ確保)、margin-top:5px。
- 決定: 項目間の border-bottom は「最終項目以外すべて」に付ける(デザインの項目 1・2 に一致)。

**変種 1: kind='translation_complete'**(デザイン項目 1=未読)
- テキスト列(縦 flex gap:3px; min-width:0):
  - 本文(font-size:11.5px; line-height:1.55): `<b>翻訳が完了しました</b>` + ` — {paper タイトル}`。タイトルは 48 文字超なら先頭 46 文字+`…`(決定。デザイン表示 `Stochastic Interpolants: A Unifying Framework…` はこの規則の出力)。
  - メタ行(font-size:10px; color:#9A9EA4=`--pr-text-muted`): `{相対時刻} · ` + リンク `読み始める →`(color:var(--pr-a)、font-weight:600)→ `/papers/{library_item_id}`。
  - デザイン逐語: `翻訳が完了しました — Stochastic Interpolants: A Unifying Framework…` / `昨日 19:40 · 読み始める →`

**変種 2: kind='status_suggestion'**(デザイン項目 2=未読・アクションボタンつき)
- テキスト列(縦 flex gap:5px; min-width:0):
  - 本文(font-size:11.5px; line-height:1.55): □ AiMark `✦` + ` {タイトル} を 3 分以上読んでいます。` + `<b>「読んでいる」にしますか?</b>`(`reason='read_3min'`)。`reason='reached_end'` の場合(決定: デザイン未描画): `✦ {タイトル} を最後まで読みました。` + `<b>「読んだ」にしますか?</b>`。太字内のラベルは `suggested_status` の日本語ラベル(reading=読んでいる / done=読んだ)。
  - ボタン行(display:flex; gap:6px):
    - プライマリ「変更する」: inline-flex、height:22px、padding:0 10px、border-radius:5px、background:var(--pr-a)、color:#FFFFFF、font-size:10.5px、font-weight:600。→ `action:'apply'`
    - セカンダリ「そのまま」: inline-flex、height:22px、padding:0 10px、border:1px solid #DDD9CF(`--pr-border-control`)、border-radius:5px、font-size:10.5px、color:#5B6067(`--pr-text-sub`)。→ `action:'dismiss'`
  - 補足(font-size:9.5px; color:#9A9EA4): `ステータスは勝手に変わりません — 提案のみ(設定で変更可)`
  - デザイン逐語: `✦ InstaFlow を 3 分以上読んでいます。「読んでいる」にしますか?` / `変更する` / `そのまま` / `ステータスは勝手に変わりません — 提案のみ(設定で変更可)`
- `resolved` 非 null の表示(決定: デザイン未描画): ボタン行を 1 行テキストに置換 — `applied` → `✓ 「{ラベル}」に変更しました`(font-size:10.5px、color:#659471=`--pr-green`、weight 600)、`dismissed` → `そのままにしました`(font-size:10.5px、color:#9A9EA4)。補足行は残す。

**変種 3: kind='deadline_reminder'**(デザイン項目 3=既読)
- テキスト列(縦 flex gap:3px):
  - 本文(font-size:11.5px; line-height:1.55; color:#3C4046=`--pr-text-mid`): `{name} の締切まで ` + `<b>{days_left} 日</b>`(color:#A05A42=`--pr-warn`)+ ` — 未着手 {untouched_count} 本`。
  - メタ行(font-size:10px; color:#9A9EA4): `{相対時刻} · ` + リンク `コレクションを開く →` → `/collections/{collection_id}`。決定(抽出値優先): **この変種のリンクはメタ行の継承色 #9A9EA4(font-weight 通常、下線なし)とし、ホバーで color:var(--pr-a) にする**。変種 1 のリンク(var(--pr-a)/600)とはリンク色規則が異なるが、いずれも抽出値どおり。リンク色は kind に紐づく固定規則であり、read/未読には依存しない。
  - デザイン逐語: `輪読会 2026-07 の締切まで 10 日 — 未着手 1 本`(「10 日」が太字・#A05A42)/ `今日 8:00 · コレクションを開く →`

**相対時刻の整形規則(決定)**: `created_at`(UTC)をローカル時刻に変換し、同日=`今日 H:mm`、前日=`昨日 H:mm`、それ以前=`M/D H:mm`(年跨ぎは `YYYY/M/D H:mm`)。分は 2 桁ゼロ埋め、時はゼロ埋めなし(「今日 8:00」「昨日 19:40」に一致)。

### 4.4 サイドバー(□ SidebarNav、w:216px)

font-size:12.5px、color:#3A3E44。各ナビ項目 padding:7px 10px(コレクション/保存フィルタ項目は 6px 10px)、border-radius:6px。項目・件数・バッジのスタイルは plans/08 §5.14 の定義そのまま。データバインドは以下:

| # | 項目(逐語) | データ源 | 表示 |
|---|---|---|---|
| 1 | `ホーム` | 固定。href=`/` | 非選択 |
| 2 | `ライブラリ`(選択中) | 固定。href=`/library`。件数 `41`=facets `quick.all` | display:flex、align-items:center、gap:8px、background:var(--pr-as)、color:var(--pr-a)、font-weight:600。ラベル flex:1、右端に件数(□ CountBadge variant='nav'。font-size:10.5px、色は継承=var(--pr-a)) |
| 3 | `語彙帳` | href=`/vocab`。件数 `46`=`GET /api/vocab` の `counts.all` | 件数 font-size:10.5px、color:#9A9EA4 |
| 4 | セクション見出し `コレクション` | 固定 | font-size:10.5px、font-weight:600、color:#9A9EA4、letter-spacing:0.4px、padding:14px 10px 4px |
| 5 | `輪読会 2026-07` | `GET /api/collections` items 順 | ラベル flex:1、□ DeadlineBadge(chip)`7/16`(inline-flex、height:16px、padding:0 6px、border-radius:3px、background:rgba(176,104,79,0.14)=`--pr-warn-bg`、color:#A05A42、font-size:9.5px、font-weight:600。`deadline` 非 null 時のみ)、件数 `5`=`item_count`(font-size:10.5px、color:#9A9EA4) |
| 6 | `Diffusion 蒸留` | 同上 | 件数 `8` |
| 7 | `講義: 生成モデル` | 同上 | 件数 `12` |
| 8 | セクション見出し `保存フィルタ` | 固定 | #4 と同スタイル |
| 9 | `締切あり` | `GET /api/saved-filters` items 順。href=`/library?filter_id={id}` | 件数 `3`=`count` |
| 10 | `cs.CV の未読` | 同上 | 件数 `7` |
| 11 | スペーサー(flex:1) | — | — |
| 12 | フッタ `設定 · エクスポート` | 「設定」→`/settings`、「エクスポート」→`/settings?category=export` の 2 リンク+区切り「 · 」 | padding:6px 10px + padding-top:12px、color:#777B81(`--pr-text-sub2`)、font-size:11.5px、border-top:1px solid #E7E4DB、margin-top:8px |

- コレクションの締切バッジ表示形式: `deadline`(YYYY-MM-DD)を `M/D` に整形。

### 4.5 メインエリア見出し行(■ LibraryHeaderRow。display:flex; align-items:center; gap:12px)

- ページタイトル「ライブラリ」: font-size:16px、font-weight:700。
- 件数「41 本」: font-size:11.5px、color:#9A9EA4。値=facets `quick.all`(半角数字+半角スペース+「本」)。
- **□ SegmentedControl(size='sm')**(display:flex; background:#EFEDE6=`--pr-bg-muted`; border-radius:7px; padding:2px; gap:2px; margin-left:6px)
  - 「カード」(選択中): height:22px、inline-flex、padding:0 10px、border-radius:5px、font-size:11px、background:#FFFFFF(`--pr-bg-seg-selected`)、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)(`--pr-shadow-seg`)。
  - 「テーブル」(非選択): 同寸、color:#5B6067、背景なし。
  - `options=[{value:'card',label:'カード'},{value:'table',label:'テーブル'}]`、`ariaLabel='表示形式'`。
- スペーサー(flex:1)。
- **■ SortMenu**「並び: 更新日 ▾」: font-size:11.5px、color:#5B6067、`▾` は font-size:9px、color:#9A9EA4。`<button>`。クリックで □ Popover(width 180、placement='bottom-end'、caret なし。1e の SortMenu と同一様式)を開き、7 ソートキーを縦リスト(各行 height:28px、padding:0 12px、font-size:11.5px、選択中は color:var(--pr-a)/600+末尾 `✓`、ホバー bg `--pr-bg-hover`。決定)で表示。ラベルは §3.2 の対応表。選択で `?sort=`+`?order=`(§1 のキー別既定方向)を `router.replace` し Popover を閉じる。決定: 選択中キーを再選択した場合は `order` を反転する(1e の列ヘッダ再クリック反転と同じ規則。カードビューで昇順に到達する唯一の操作)。トリガーの表示ラベルは常に現在の `sort` の日本語名(「並び: {名} ▾」。order は表示に含めない)。

### 4.6 ステータスフィルタチップ行(■ QuickFilterChips。display:flex; align-items:center; gap:6px)

□ FilterChip(size='md')× 5。`label` と `count` を分離して渡す(表示は「{label} {count}」)。

- 選択中チップ「すべて 41」: height:22px、inline-flex、padding:0 10px、border-radius:999px、background:#26292E(`--pr-elev-bg`。両テーマ共通)、color:#FFFFFF、font-size:11px、font-weight:600。
- 非選択チップ(共通): height:22px、padding:0 10px、border:1px solid #DDD9CF(`--pr-border-control`)、border-radius:999px、font-size:11px、color:#3C4046(`--pr-text-mid`)、background:#FFFFFF(`--pr-bg-control`)。
- 5 チップ(逐語・対応値): `すべて 41`(all)/ `未読 12`(unread)/ `途中 4`(in_progress)/ `読了 23`(done)/ `要再確認 2`(recheck)。件数=facets `quick`。

### 4.7 論文カード(■ PaperCard。共通構造)

カード外枠 = □ Card: background:#FFFFFF(`--pr-bg-card`)、border:1px solid #E2DFD5(`--pr-border-card`)、border-radius:10px、overflow:hidden、縦 flex。カード全体がクリック領域(`cursor:pointer`、`role="link"`。§5.4)。

- **■ CardThumbnail(サムネイル帯)**: height:108px、background:#EFEDE6(`--pr-bg-thumb`)、border-bottom:1px solid #E7E4DB(`--pr-border-pane`)、flex 中央、color:#B0B4BA(`--pr-text-thumb`)、font-size:10px、position:relative。
  - `thumbnail_url` 非 null: `<img>` を width:100%、height:108px、object-fit:cover で表示(alt=タイトル)。デザインのプレースホルダ文言(「図1(概観)」等)はデモデータ上の図キャプションであり、実装では画像そのものを表示する。
  - `thumbnail_url` null(決定: デザイン未描画): 中央にテキスト `図なし`(10px、#B0B4BA)。
  - **■ CardQualityMonogram**(左上バッジ `A`): absolute、top:8px、left:8px、17×17px、border-radius:4px、background:#FFFFFF(`--pr-bg-card`)、color:var(--pr-a)、font-size:10px、font-weight:700、box-shadow:0 1px 4px rgba(28,30,34,0.14)(`--pr-shadow-mono`)、中央揃え。表示文字=`quality_level`(A/B)。決定: `B` の文字色は #777B81(`--pr-text-sub2`)(□ QualityBadge の B 配色に整合。デザインは A のみ描画)。`title` 属性は □ QualityBadge と同文言。
- **本文部**: padding:11px 13px、縦 flex、gap:6px、flex:1。
  - タイトル: font-size:12.5px、font-weight:600、line-height:1.5、2 行クランプ(display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden)。値=`paper.title`。
  - 書誌行: font-size:10.5px、color:#9A9EA4。表示=`{paper.authors_short を「Liu et al.」形式に短縮} · {paper.venue ?? paper.year}`。決定: 著者表示は `paper.authors_short`(カンマ区切りの姓リスト。例 "Liu, Gong, Liu")を `", "` で分割し、1 要素=そのまま、2 要素=`{A}, {B}`(そのまま。例 "Salimans, Ho")、3 要素以上=`{先頭要素} et al.`。`paper.authors` 配列は使わない。venue 非 null なら `{venue}`(年を含む文字列)、null なら `{year}`、両方 null なら区切り「 · 」ごと省略して著者のみ(決定)。
  - AI 要約: font-size:10.5px、line-height:1.6、color:#5B6067(`--pr-text-sub`)、2 行クランプ。先頭に □ AiMark `✦`+半角スペース。値=`summary_3line` の 3 要素を区切りなしで連結(各要素は「。」終端。決定)。`summary_3line` null(生成中)は行ごと非表示(決定)。
  - **フッタ行**(margin-top:auto):
    - `status === 'reading'` のカードのみ: 縦 flex gap:6px で「進捗バー+ステータス行」。□ ProgressBar: height:3px、border-radius:2px、トラック background:#ECE9DF(`--pr-border-soft`)、フィル absolute inset:0、width:`{progress_pct}%`(デザイン=42%)、border-radius:2px、background:var(--pr-a)。
    - ステータス行(display:flex; align-items:center; gap:6px):
      - □ StatusPill(size='sm'): inline-flex、gap:5px、height:20px、padding:0 8px、border:1px solid #DDD9CF、border-radius:999px、font-size:10px、background:#FFFFFF。中に 6×6px 円形ドット(色=`STATUS_COLORS[status]`)+ラベル(`STATUS_LABELS[status]`)。interactive=false(カード上では変更不可。変更はビューアかテーブルで行う。決定)。
      - □ TagChip: height:17px、inline-flex、padding:0 6px、border-radius:3px、background:#F1EFE9(`--pr-bg-inset`)、color:#5B6067、font-size:9.5px。表示=`tags[0]` のみ(決定: デザインどおり 1 個。2 個以上は表示しない)。tags 空なら非表示。
      - □ DeadlineBadge(variant='chip', withLabel=true): `deadline` 非 null 時に表示 — height:17px、padding:0 6px、border-radius:3px、background:rgba(176,104,79,0.14)、color:#A05A42、font-size:9.5px、font-weight:600、ラベル `締切 {M/D}`。決定: 締切チップ表示時は TagChip を出さない(デザインのカード 3 はタグなし+締切チップのみ。スロットは 1 つ)。
      - 右端メタ: margin-left:auto、font-size:9.5px、color:#9A9EA4。内容規則(§4.8)。優先度「高」表示時のみ color:#A05A42、font-weight:600。

**ステータスドット色の対応**(= `STATUS_COLORS`。plans/08 §2.4):

- 読んでいる(reading)= var(--pr-acc)(既定 #3E5C76)
- 読んだ(done)= #659471
- すぐ読む(up_next)= #C49432
- 読む予定(planned)= #9AA0A6
- 保留(on_hold)= #B0ACA2
- あとで再読(reread)= #8E7AA6(デザイン 4a には未登場だが 6 値の一部。1e と同色)

### 4.8 右端メタの表示規則(確定)

`h` 表記=`reading_seconds_total / 3600` を小数 1 桁(`toFixed(1)`)+`h`。丸めの結果 `0.0h` になる場合(180 秒未満)もそのまま表示する(決定。表中の各行の表示条件のみで出し分け、値による追加の非表示条件は設けない)。

| 条件 | 表示 | デザイン例 |
|---|---|---|
| `status === 'done'` かつ `comprehension` 非 null | `理解 {comprehension}/5 · {h}h` | `理解 4/5 · 5.1h`(カード 2)、`理解 5/5 · 2.4h`(カード 4) |
| `status === 'done'` かつ `comprehension` null | `{h}h`(決定) | — |
| 上記以外で `priority` 非 null | `優先: 高` / `優先: 中` / `優先: 低`(□ PriorityBadge withPrefix。「高」のみ #A05A42/600、中・低は #9A9EA4) | `優先: 高`(カード 3)、`優先: 中`(カード 5) |
| 上記以外で `reading_seconds_total > 0` | `{h}h` | `3.2h`(カード 1)、`0.6h`(カード 6) |
| 上記以外 | 非表示(決定) | — |

### 4.9 デザイン描画データ(6 カード。VRT フィクスチャに使用)

| # | サムネ文言 | タイトル | 書誌 | 要約 | ステータス | タグ/チップ | 右端メタ | 進捗バー |
|---|---|---|---|---|---|---|---|---|
| 1 | 図1(概観) | Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow | Liu et al. · ICLR 2023 | ✦ 直線に近い経路のODEを最小二乗回帰で学習し、生成と転移を統一。reflowで1ステップ生成へ。 | 読んでいる | flow | 3.2h | あり(42%) |
| 2 | 図2(蒸留図) | Consistency Models | Song et al. · ICML 2023 | ✦ 任意時刻から起点への写像を直接学習し、1〜数ステップ生成。蒸留・単独学習の両方に対応。 | 読んだ | distillation | 理解 4/5 · 5.1h | なし |
| 3 | 図1(ADD) | Adversarial Diffusion Distillation | Sauer et al. · 2024 | ✦ 敵対的損失とスコア蒸留を組み合わせ、1〜4ステップの高品質生成を実現。SDXL Turbo の基盤。 | すぐ読む | 締切 7/16(赤系チップ) | 優先: 高(#A05A42, 600) | なし |
| 4 | 図3(半減蒸留) | Progressive Distillation for Fast Sampling of Diffusion Models | Salimans, Ho · ICLR 2022 | ✦ ステップ数を半減させる蒸留を反復し、4ステップで高品質サンプリングを達成。 | 読んだ | distillation | 理解 5/5 · 2.4h | なし |
| 5 | 図1(InstaFlow) | InstaFlow: One Step is Enough for High-Quality Diffusion-Based Text-to-Image Generation | Liu et al. · 2024 | ✦ reflow を Stable Diffusion に適用し、1ステップの T2I 生成を初めて実用品質で実現。 | 読む予定 | text-to-image | 優先: 中 | なし |
| 6 | 図2(SDE) | Score-Based Generative Modeling through Stochastic Differential Equations | Song et al. · ICLR 2021 | ✦ スコアベース生成をSDEで統一し、逆時間SDE/確率フローODEによるサンプリングを提示。 | 保留 | score | 0.6h | なし |

全カードの左上モノグラムは `A`。

### 4.10 全 UI 文言(逐語・完全リスト)

トップバー: `訳` / `Alinea` / `ライブラリ全体を検索 — 本文・訳文・メモ・チャット` / `⌘K` / `◷` / `YK`

通知ポップオーバー: `通知` / `すべて既読にする` / `翻訳が完了しました — Stochastic Interpolants: A Unifying Framework…`(「翻訳が完了しました」が太字)/ `昨日 19:40 · 読み始める →` / `✦ InstaFlow を 3 分以上読んでいます。「読んでいる」にしますか?`(「「読んでいる」にしますか?」が太字)/ `変更する` / `そのまま` / `ステータスは勝手に変わりません — 提案のみ(設定で変更可)` / `輪読会 2026-07 の締切まで 10 日 — 未着手 1 本`(「10 日」が太字・#A05A42)/ `今日 8:00 · コレクションを開く →`

サイドバー: `ホーム` / `ライブラリ` `41` / `語彙帳` `46` / `コレクション` / `輪読会 2026-07` `7/16` `5` / `Diffusion 蒸留` `8` / `講義: 生成モデル` `12` / `保存フィルタ` / `締切あり` `3` / `cs.CV の未読` `7` / `設定 · エクスポート`

メイン見出し・フィルタ: `ライブラリ` `41 本` / `カード` / `テーブル` / `並び: 更新日 ▾` / `すべて 41` / `未読 12` / `途中 4` / `読了 23` / `要再確認 2`

カード: §4.9 の表の全文字列(サムネ文言・タイトル・書誌・要約・ステータスラベル・タグ・`締切 7/16`・右端メタ)+モノグラム `A`。

## 5. 状態とインタラクション

### 5.1 一覧の状態

| 状態 | 表示 |
|---|---|
| ローディング(初回) | 見出し「ライブラリ」は即描画。件数「41 本」・チップ件数は facets 取得まで非表示(チップは幅 64px×5 のピル形スケルトンで代替)。グリッドに ■ PaperCardSkeleton × 9(3 列×3 行)。決定: スケルトン形状=カード外枠(□ Card)+サムネ帯 108px(bg `--pr-bg-muted`)+本文部にバー 3 本(height 12px/10px/10px、border-radius:4px、bg `--pr-bg-inset`、width 90%/70%/50%、縦 gap 8px)。全体に opacity 1→0.55→1 の 1200ms ease-in-out 無限パルス |
| 追加ページ取得中 | グリッド末尾に PaperCardSkeleton × 3 |
| 空(フィルタなし・0 件) | □ EmptyState(グリッド領域中央)。判定: フィルタ未適用(quick=all かつ属性フィルタ・filter_id なし)かつ 0 件(決定: 1e §5.2 と同じ判定)。決定文言(1e と同一): title=`まだ論文がありません`、description=`ブラウザ拡張で arXiv ページを開き「保存」すると、ここに並びます`。action なし(取り込みは拡張のみのため) |
| 空(フィルタあり・0 件) | □ EmptyState。決定文言(1e §5.2 と同一): title=`条件に一致する論文がありません`、description=`フィルタを解除するか、検索語を変えてください`、action=`{ label: 'フィルタをすべて解除' }` → `quick=all`+全属性フィルタ・`filter_id` を除去 |
| エラー(一覧取得失敗) | □ EmptyState。決定文言(1e §5.2 と同一): title=`読み込みに失敗しました`、description=Problem レスポンスの `title`(Problem 形式でない場合は固定文言 `通信に失敗しました`)、action=`{ label: '再試行' }` → `refetch()` |
| facets 取得失敗 | チップは件数なしラベルのみ(`すべて` 等)で描画し操作可能のまま(決定。一覧本体を巻き込まない) |

### 5.2 カードの状態

- **静止**: §4.7。
- **ホバー**(決定: デザイン未描画): border-color を var(--pr-am) に変更+box-shadow:0 2px 6px rgba(28,30,34,0.08)(`--pr-shadow-float`)+cursor:pointer。transition:border-color 120ms ease-out, box-shadow 120ms ease-out。
- **フォーカス**(キーボード): plans/08 §5 共通の `focus-visible` リング(outline:1.5px solid var(--pr-acc); outline-offset:1px)。
- **翻訳処理中**(`pipeline` 非 null。決定: デザイン 4a 未描画、1d の視覚言語を縮退流用): AI 要約行の位置に `翻訳中 {progress_pct}%`(font-size:10.5px、color:#9A9EA4)+□ ProgressBar(height 3、accent)を表示。`stage='failed'` は `取り込みに失敗しました`(10.5px、#A05A42)。
- ステータス 6 値それぞれのドット色は §4.7 の対応表。「読んでいる」のみ進捗バー付き(§4.7)。

### 5.3 通知の状態

| 状態 | 表示 |
|---|---|
| ベル・未読あり | ◷+琥珀ドット(6px #C49432) |
| ベル・未読なし | ◷ のみ(ドット非表示) |
| ベル・開 | アクティブ表示(枠 var(--pr-am)+面 var(--pr-as)+文字 var(--pr-a)) |
| ポップオーバー・ローディング | 決定: 項目形スケルトン × 3(各行: ドット位置 7px 円 bg `--pr-bg-muted`+バー 2 本 width 85%/40%、height 10px、bg `--pr-bg-inset`、padding 10px 14px)。パルスは §5.1 と同じ |
| ポップオーバー・空 | □ EmptyState(padding は既定)。決定文言: title=`通知はありません`、description なし |
| ポップオーバー・エラー | 決定文言: title=`通知を取得できませんでした`、action=`{ label: '再試行' }` |
| 項目・未読 | bg #FCFBF6+アクセントドット 7px |
| 項目・既読 | 白背景+透明ドット(位置保持) |
| 提案・未消化 | 「変更する / そのまま」ボタン行 |
| 提案・消化済み(`resolved` 非 null) | §4.3 変種 2 の置換テキスト |

### 5.4 インタラクション一覧(遷移・API 対応)

1. **⌘K / Ctrl+K**: LibraryShell のグローバルキーマップが □ SearchBox(global)へフォーカス。検索バークリックも同等。入力で SearchDropdown(1e 計画書 §5.3 の仕様)。Esc でクローズ+ブラー。
2. **◷ クリック**: ポップオーバー開閉トグル。開いたら `qk.notifications()` を fetch。外側クリック・Esc で閉じる(□ Popover 既定)。開いただけでは既読化しない(決定: デザインは開いた状態で未読 2 件が残っている)。
3. **すべて既読にする**: `POST /api/notifications/read-all` → §2.2 の楽観更新。ポップオーバーは開いたまま(決定)。
4. **読み始める →**: `PATCH /api/notifications/{id}`(read:true、fire-and-forget)→ ポップオーバーを閉じて `router.push('/papers/{library_item_id}')`。
5. **変更する / そのまま**: `POST /api/notifications/{id}/action`。ボタンは実行中 disabled(opacity:0.5)。成功でレスポンスの notification に差し替え(§5.3 消化済み表示)。apply 成功時は □ Toast(plans/08 §5.20)`✓ 「{suggested_status の日本語ラベル}」に変更しました`(reading=`✓ 「読んでいる」に変更しました` / done=`✓ 「読んだ」に変更しました`)を表示(決定)+ライブラリ一覧 invalidate。409 `conflict`(消化済み)は `qk.notifications()` を invalidate して最新化(決定: エラー Toast は出さない)。
6. **コレクションを開く →**: #4 と同じ既読化+`router.push('/collections/{collection_id}')`。
7. **SSE `notification.created`**: ベルのドット即時点灯(§2.3)。ポップオーバー表示中は先頭に新項目が挿入される(invalidate 経由)。
8. **サイドバー**: 各項目 `<Link>`。現在ルートの項目がアクティブ表示(4a では「ライブラリ」)。
9. **カード/テーブル切替**: □ SegmentedControl → `?view=` を `router.replace`(localStorage は使わない。§1)。データは共有(query キーは view 非依存)。
10. **並び: 更新日 ▾**: §4.5 のドロップダウン。選択 → `?sort=`/`?order=` 更新 → `qk.library` 再取得。開いている間 `▾` の色は var(--pr-a)(決定)。
11. **クイックフィルタチップ**: クリック → `?quick=` 更新 → 一覧再取得。選択中チップの再クリックは何もしない(決定)。
12. **カードクリック**: `router.push('/papers/{item.id}')`。カード全体が対象。内部にリンク・ボタンは持たない(StatusPill も非 interactive)ためイベント衝突なし。
13. **カードグリッドスクロール**: グリッド自身が `overflow-y:auto`(ヘッダ行・チップ行は固定)。末尾で無限ロード(§2.4)。
14. **アバタークリック**(決定: デザイン未描画): `/settings` へ遷移(メニューは v2)。

### 5.5 キーボード・a11y

- チップ行は `role="radiogroup"`(□ FilterChip 群)、SegmentedControl は plans/08 §5.1 の radiogroup 実装。
- カードは `<a>`(Next Link)として実装し Tab 順に含める。`aria-label` = `{タイトル} — {ステータスラベル}`。
- ◷ ボタン: `aria-label="通知"`、`aria-haspopup="dialog"`、`aria-expanded`。未読ドットは `aria-hidden`(未読数は `aria-label="通知 — 未読 {n} 件"` に含める。決定)。
- 通知ポップオーバー内は □ Popover の Esc/外側クリック閉鎖+フォーカスは開いた時に「すべて既読にする」へ(決定)。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Storybook + Playwright VRT。フィクスチャは §4.9 の 6 カード+§4.3 の通知 3 件(未読 2・既読 1)+§4.4 のサイドバー件数を再現するモックデータを用い、1440×900・ライトテーマ・アクセント slate で確定デザインと比較する。

- [ ] トップバー: h52px・ロゴブロック w198px・検索バー w460×h32px(bg #F1EFE9、radius 7px)・⌘K キーキャップ・◷ 30×30px・アバター 30×30px が抽出値と一致
- [ ] ◷ アクティブ状態(枠 rgba(62,92,118,0.32)+面 rgba(62,92,118,0.10)+文字 #3E5C76)+未読ドット 6px #C49432(top:5px right:5px)
- [ ] 通知ポップオーバー: top 50px・右端から 56px・w352px・radius 10px・shadow 0 24px 56px rgba(28,30,34,0.18)・キャレット 9×9px(top:-5px right:26px、45° 回転、左上 2 辺 border)
- [ ] 通知項目: 未読 bg #FCFBF6+7px ドット/既読 白+透明ドット、本文 11.5px/1.55、メタ 10px #9A9EA4、区切り #F4F1E9(最終項目なし)
- [ ] 提案通知: ✦ アクセント色、ボタン「変更する」(h22px、bg #3E5C76、白、10.5px/600)/「そのまま」(h22px、枠 #DDD9CF、#5B6067)、補足 9.5px #9A9EA4 が逐語一致
- [ ] 締切通知: 「10 日」が太字 #A05A42、本文色 #3C4046
- [ ] サイドバー: w216px・bg #F7F6F2・右線 #E7E4DB・「ライブラリ」アクティブ(bg var(--pr-as)、色 var(--pr-a)、600)・締切バッジ 7/16(h16px、bg rgba(176,104,79,0.14)、#A05A42)・見出し 10.5px/600/ls0.4px・フッタ「設定 · エクスポート」(11.5px、#777B81、上線)
- [ ] 見出し行: 「ライブラリ」16px/700+「41 本」11.5px #9A9EA4+セグメント(トラック #EFEDE6、選択「カード」白+shadow-seg、h22px)+「並び: 更新日 ▾」(11.5px #5B6067、▾ 9px #9A9EA4)
- [ ] チップ行: 選択「すべて 41」(h22px、bg #26292E、白、11px/600)+非選択 4 チップ(枠 #DDD9CF、#3C4046、白地)、gap 6px
- [ ] カードグリッド: 3 列 1fr 均等・gap 14px・padding 2px 2px 8px
- [ ] カード: 枠 #E2DFD5/radius 10px・サムネ帯 108px(bg #EFEDE6、下線 #E7E4DB)・モノグラム A(17×17px、白地、var(--pr-a)、shadow-mono、top:8 left:8)・本文 padding 11px 13px/gap 6px・タイトル 12.5px/600/1.5/2 行クランプ・書誌 10.5px #9A9EA4・要約 ✦+10.5px/1.6 #5B6067/2 行クランプ
- [ ] カード 1 のみ進捗バー(h3px、トラック #ECE9DF、フィル 42% var(--pr-a))、ステータスピル 5 種のドット色(#3E5C76/#659471/#C49432/#9AA0A6/#B0ACA2)、タグチップ(h17px、#F1EFE9)、締切チップ(h17px、rgba(176,104,79,0.14)、#A05A42、600)、右端メタ 9.5px(カード 3 のみ #A05A42/600)が抽出値と一致
- [ ] §4.10 の全 UI 文言が逐語一致(スペース・全半角・記号を含む)
- [ ] ダークテーマ+アクセント 4 色でトークン追随が崩れない(トークン経由色のみ使用の確認)

### 6.2 機能検証

- [ ] `/library` 未認証アクセスで `/login?next=/library` へリダイレクト
- [ ] `GET /api/library-items`(quick/sort/order/filter_id)と URL クエリが 1:1 で同期し、リロード・共有 URL で状態が復元される
- [ ] クイックフィルタ 5 種の件数が `GET /api/library-items/facets` の `quick` と一致し、クリックで一覧が絞り込まれる(未読=planned+up_next、途中=reading+on_hold、読了=done、要再確認=reread。docs/06 §1)
- [ ] ソート 7 キー×昇降順が機能し、`deadline`/`comprehension` の null が常に末尾(plans/03 §5.1)
- [ ] 50 件超のライブラリで無限スクロールが動作し、cursor 重複・欠落なし
- [ ] カードの右端メタが §4.8 の規則どおり(done=理解 n/5 · h、priority あり=優先: X、それ以外=h または非表示)
- [ ] `status='reading'` のカードのみ進捗バー(`progress_pct`)が表示される
- [ ] カード⇄テーブル切替が `?view=` に反映され、URL 直開き・リロードで復元される(`view` 省略時は `card`)
- [ ] 未読通知があるときのみベルに琥珀ドット。`GET /api/auth/me` の `unread_notifications` と一致
- [ ] ◷ クリックで開閉、外側クリック・Esc で閉じる。開いても自動既読化されない
- [ ] 「すべて既読にする」で全項目が既読表示になり、ドットが消える(`POST /api/notifications/read-all`)
- [ ] 「読み始める →」で該当論文(`/papers/{id}`)が開き、当該通知が既読化される
- [ ] 「変更する」で `POST /api/notifications/{id}/action {action:'apply'}` → ライブラリ一覧・facets・未読数が更新され、対象論文のステータスが「読んでいる」になる。「そのまま」で dismiss され再提案されない
- [ ] 消化済み提案(resolved 非 null)はボタンが出ず、409 発生時は最新状態へ再同期される
- [ ] SSE `notification.created` 受信から 1 秒以内にベルのドットが点灯する。SSE 切断時は 30 秒ポーリングにフォールバックする
- [ ] サイドバーの全リンク(ホーム/ライブラリ/語彙帳/コレクション×n/保存フィルタ×n/設定/エクスポート)が §1 のルートへ遷移し、件数(facets `quick.all`・vocab `counts.all`・collections `item_count`・saved-filters `count`)が API 値と一致
- [ ] ⌘K(macOS)/ Ctrl+K(Windows/Linux)でグローバル検索がフォーカスされる
- [ ] ローディング・空(2 種)・エラーの各状態が §5.1・§5.3 の決定どおり表示される
- [ ] axe による自動 a11y チェックで違反 0(radiogroup・dialog・aria-label 群)
