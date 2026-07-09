# 画面 4b: コレクション詳細

> 対象読者と前提: 本書は「Alinea」のフロントエンド実装者向けに、確定デザイン画面 4b(コレクション詳細 — 順序付きリスト・締切・担当・共有リンク管理)を 1px の差分なく実装するための計画書である。機能仕様は docs/06(ライブラリと進捗管理)§4〜§5 を正、ピクセル値は抽出ファイル extract/4b.md を正とする。共通コンポーネント名・トークン名は plans/08-design-system.md、API エンドポイント名・型名は plans/03-api.md、テーブル定義は plans/02-data-model.md §4.10 のものをそのまま使う。技術スタック: Next.js 15(App Router)+ React 19 + TypeScript 5 + Tailwind CSS v4 + TanStack Query v5 + Zustand + dnd-kit(`@dnd-kit/core` 6.3.1)+ `packages/api-client`(openapi-fetch 生成クライアント)。

## 1. 概要とルート

- **ルートパス(確定)**: `/collections/{collection_id}`。ファイルは `apps/web/src/app/(app)/collections/[collectionId]/page.tsx`。
- **関連ルート(確定。本画面から遷移する先)**: ホーム=`/`(1d)、ライブラリ=`/library`(1e/4a)、語彙帳=`/vocab`(4d)、リーダー=`/papers/{library_item_id}`(1a 計画 §1 で確定済み)、共有ページ=`/c/{token}`(4c。新規タブで開く)、保存フィルタ適用=`/library?filter_id={sf_id}`。
- **認証**: 必須(HTTPOnly セッションクッキー)。`(app)` レイアウトが `GET /api/auth/me` でセッション確認し、未認証は `/login` へリダイレクト(plans/01 §2.1)。CSR 画面(SSR しない)。
- **画面の役割**: 手動キュレーションの順序付きリスト(docs/06 §4)の管理画面。輪読会・講義・プロジェクト単位で論文を並べ、締切・説明文・発表担当・発表時間を管理し、読了進捗を集計表示する。右上カードで閲覧専用共有リンク(閲覧のみ・アカウント不要・noindex。共有ページは 4c)を発行・管理する。
- **URL 状態**: クエリ・ハッシュは使わない(確定)。編集ポップオーバー等の開閉はすべてローカル state。

## 2. データ要件

### 2.1 使用 API エンドポイント一覧(plans/03 の名前)

| # | エンドポイント | 用途 | 取得/実行タイミング |
|---|---|---|---|
| 1 | `GET /api/collections/{collection_id}` | 本画面の主データ(名前・締切・説明・進捗・share・entries) | ルート初回マウント時。初期描画のブロッキング取得 |
| 2 | `GET /api/collections` | サイドバー「コレクション」一覧(名前+件数+締切ミニバッジ) | LibraryShell マウント時(共有シェル) |
| 3 | `GET /api/library-items/facets` | サイドバー「ライブラリ 41」の件数(`quick.all`) | LibraryShell マウント時 |
| 4 | `GET /api/vocab?limit=1` | サイドバー「語彙帳 46」の件数(`counts.all`) | LibraryShell マウント時 |
| 5 | `GET /api/saved-filters` | サイドバー「保存フィルタ」(名前+`count`) | LibraryShell マウント時 |
| 6 | `GET /api/auth/me` | アバターイニシャル「YK」 | `(app)` レイアウト(セッション確認と兼用) |
| 7 | `PATCH /api/collections/{collection_id}` | 説明文のインライン編集・締切の変更/削除・名前変更 | 「説明を編集」保存時・締切ポップオーバー保存時 |
| 8 | `PUT /api/collections/{collection_id}/entries/order` | ドラッグ並べ替えの確定 | ドロップ確定時(全 `entry_ids` を新順序で送る) |
| 9 | `POST /api/collections/{collection_id}/entries` | 「+ 論文を追加」 | 追加ポップオーバーで論文選択時 |
| 10 | `PATCH /api/collection-entries/{entry_id}` | 担当・発表時間・注記の編集 | エントリ編集ポップオーバー保存時 |
| 11 | `DELETE /api/collection-entries/{entry_id}` | 「コレクションから外す」 | オーバーフローメニュー選択時 |
| 12 | `GET /api/library-items?q=&limit=10` | 追加ポップオーバーの候補検索(書誌の簡易絞り込み) | 追加ポップオーバー入力 300ms デバウンス |
| 13 | `POST /api/collections/{collection_id}/share` | 共有リンク発行(未発行/無効化後の再発行) | 「共有リンクを発行」ボタン |
| 14 | `PATCH /api/collections/{collection_id}/share` | 「共有ページにメモを含める」トグル | トグル操作時(楽観的更新) |
| 15 | `DELETE /api/collections/{collection_id}/share` | 「リンクを無効化」 | 無効化確認モーダルの確定時 |
| 16 | `POST /api/collections` | サイドバー「+ 新規コレクション」 | 行内入力(1d §5.9)の Enter |

### 2.2 TanStack Query キー設計(確定)

```ts
// apps/web/src/features/collections/queryKeys.ts
export const collectionKeys = {
  list:   () => ['collections'] as const,   // = 1e 計画 §2.2 の shellKeys.collections と同一リテラル(共有シェルのサイドバー一覧と同一キャッシュ)
  detail: (collectionId: string) => ['collection', collectionId] as const,
};

// シェル系キーは新設せず、1e 計画 §2.2 で確定済みの shellKeys / libraryKeys
// (apps/web/src/features/library/queries.ts)をそのまま import する(決定):
//   me = shellKeys.me(['auth','me']) / facets = libraryKeys.facets({})(['library','facets',{}])
//   vocabCounts = shellKeys.vocabCounts(['vocab','counts']) / savedFilters = shellKeys.savedFilters(['saved-filters'])
// 追加候補検索(AddPaperPopover)
export const addPaperSearchKey = (q: string) => ['libraryItems', 'addPaperSearch', q] as const;
```

- `staleTime`(確定): `collectionKeys.detail` = 30,000ms。`collectionKeys.list` / シェル系キー = 60,000ms(1e §2.2 と同値)。`addPaperSearchKey` = 0(`enabled: q.length > 0`、`placeholderData: keepPreviousData`)。
- ミューテーション時の無効化(確定):
  - `PATCH /api/collections/{id}` → 成功レスポンスで `setQueryData(collectionKeys.detail(id))` 即時反映+`invalidateQueries(collectionKeys.list())`(サイドバーの締切ミニバッジ・名前)。
  - `PUT …/entries/order` → 楽観的更新(§5.4)。`onSettled` で `collectionKeys.detail(id)` を invalidate。
  - `POST …/entries` / `DELETE /api/collection-entries/{id}` → `collectionKeys.detail(id)` と `collectionKeys.list()`(件数)を invalidate。
  - `PATCH /api/collection-entries/{id}` → 楽観的更新+`onSettled` invalidate。
  - share 系 3 本 → `collectionKeys.detail(id)` を invalidate(PATCH は楽観的更新併用)。
  - `POST /api/collections` → `collectionKeys.list()` invalidate 後、`router.push('/collections/' + res.id)`。

### 2.3 リアルタイム更新

- **SSE・ポーリングは使わない(確定)**。本画面のデータは自分の操作でのみ変化する(読書進捗は別画面で進む)ため、`refetchOnWindowFocus: true`(TanStack Query 既定)+ staleTime 30 秒で十分。理由: plans/01 §5 の SSE 対象(翻訳ジョブ・通知)に本画面のリソースは含まれない。
- 他画面で読書して戻った場合はウィンドウフォーカス時の再取得で進捗バー・行ステータスが更新される。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`共通` = plans/08 §5〜6 の共通コンポーネント。`shell` = 管理系画面共通シェル `LibraryShell`(`apps/web/src/components/shell/LibraryShell.tsx`。1e 計画 §1・4a・4d・4e と同一の名前とパス。スタイル・挙動の正は 4a 計画。決定)。無印 = 本画面固有(配置: `apps/web/src/features/collections/`)。

```
CollectionDetailPage (app/(app)/collections/[collectionId]/page.tsx)
└─ LibraryShell                      shell … トップバー+サイドバー+main の骨格
   ├─ AppTopBar                      shell
   │  ├─ (ロゴ「A」+「Alinea」)
   │  ├─ SearchBox (variant='global')  共通 §5.13(⌘K で GlobalSearchOverlay を開く。1e 仕様)
   │  ├─ NotificationBell「◷」       shell(クリックで NotificationPopover。4a 仕様)
   │  └─ (アバター「YK」)
   ├─ SidebarNav                     共通 §5.14(shell が描画。CountBadge 'nav' / DeadlineBadge 'chip' を内包)
   │  └─ NewCollectionInlineInput    shell(「+ 新規コレクション」クリックで行内入力。1d §5.9)
   └─ main
      ├─ CollectionHeader
      │  ├─ (パンくず「ライブラリ / コレクション」)
      │  ├─ (タイトル+締切バッジ+「5 本 · 順序付き」)
      │  │  └─ DeadlinePopover       (締切バッジクリックで開く。Popover 共通 §5.10)
      │  ├─ CollectionDescription    (「説明を編集」インライン編集)
      │  └─ ProgressBar              共通 §5.12(height=4, color='green')+「3/5 読了」
      ├─ ShareLinkCard               (Card 共通 §5.9 / Toggle 共通 §5.8)
      │  └─ RevokeShareModal         (「リンクを無効化」確認。Modal 共通 §5.11 width=380)
      └─ CollectionEntryList         (Card 共通 §5.9 / dnd-kit DndContext+SortableContext)
         ├─ (ヘッダ行「発表順 — ドラッグで並べ替え」)
         │  └─ AddPaperPopover       (「+ 論文を追加」で開く。Popover 共通 §5.10 width=360)
         ├─ EntryRow ×N              (useSortable)
         │  ├─ (⋮⋮ / 順序番号 / サムネ / タイトルブロック)
         │  ├─ StatusPill            共通 §5.2(variant='dot-label')
         │  ├─ EntryVariableSlot     (未着手 / ミニ進捗 / 理解 n/5 / —)
         │  ├─ EntryOverflowMenu     (「⋯」→ Popover 共通 §5.10 width=180)
         │  └─ EntryMetaPopover      (担当・発表時間・注記の編集フォーム)
         ├─ EmptyState               共通 §5.21(entries が 0 件のとき)
         └─ (フッタ行)
```

### 3.2 画面固有コンポーネントの props 型(確定)

型は `packages/api-client` 生成型を参照する。`CollectionDetail` = `GET /api/collections/{id}` の Response 型、`CollectionEntry` = plans/03 §13.1 の型。

```ts
// apps/web/src/features/collections/CollectionHeader.tsx
interface CollectionHeaderProps {
  collection: CollectionDetail;                          // name/deadline/days_left/description/progress
  onPatch: (patch: { name?: string; description?: string | null;
                     deadline?: string | null }) => void; // PATCH /api/collections/{id}
}

// apps/web/src/features/collections/ShareLinkCard.tsx
interface ShareLinkCardProps {
  share: CollectionDetail['share'];   // { status, token, url, include_notes, included_note_count }
  onIssue: () => void;                // POST  …/share
  onToggleNotes: (next: boolean) => void; // PATCH …/share(楽観的)
  onRevoke: () => void;               // DELETE …/share(確認モーダル経由)
}

// apps/web/src/features/collections/CollectionEntryList.tsx
interface CollectionEntryListProps {
  collectionId: string;
  entries: CollectionEntry[];         // order 昇順
  onReorder: (entryIds: string[]) => void;      // PUT …/entries/order
  onAdd: (libraryItemId: string) => void;       // POST …/entries
  onPatchEntry: (entryId: string, patch: {
    assignee?: string | null; assignee_is_self?: boolean;
    presentation_minutes?: number | null; note?: string | null }) => void;
  onRemoveEntry: (entryId: string) => void;     // DELETE /api/collection-entries/{id}
}

// apps/web/src/features/collections/EntryRow.tsx
interface EntryRowProps {
  entry: CollectionEntry;
  isLast: boolean;                    // 最終行は border-bottom なし
  onPatch: (patch: Parameters<CollectionEntryListProps['onPatchEntry']>[1]) => void; // entryId を部分適用済み
  onRemove: () => void;
}

// apps/web/src/features/collections/EntryMetaPopover.tsx
interface EntryMetaPopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement>;
  initial: { assignee: string | null; assigneeIsSelf: boolean;
             presentationMinutes: number | null; note: string | null };
  onSave: (v: EntryMetaPopoverProps['initial']) => void;
}

// apps/web/src/features/collections/AddPaperPopover.tsx
interface AddPaperPopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement>;
  existingLibraryItemIds: ReadonlySet<string>;  // 追加済み判定(「追加済み」表示)
  onSelect: (libraryItemId: string) => void;
}

// apps/web/src/features/collections/DeadlinePopover.tsx
interface DeadlinePopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement>;
  value: string | null;               // "YYYY-MM-DD"
  onSave: (deadline: string | null) => void;
}
```

「+ 新規コレクション」の行内入力(NewCollectionInlineInput)はシェル側実装(1d §5.9)のため本画面では props を定義しない。

### 3.3 表示用導出ロジック(確定)

`apps/web/src/features/collections/format.ts` に集約する:

- `formatDeadlineShort(iso: string): string` — `"2026-07-16"` → `"7/16"`(月/日、先頭ゼロなし)。
- `formatDeadlineBadge(iso: string, daysLeft: number): string` — `daysLeft > 0` → `締切 7/16 — 残り 10 日` / `daysLeft === 0` → `締切 7/16 — 今日` / `daysLeft < 0` → `締切 7/16 — 超過 3 日`(決定: 期限超過・当日の文言はデザイン未描画のため本書で確定。色・形状は通常時と同一)。
- `formatAuthorsEtAl(authors: string[]): string` — 姓のみ使用。1 名=`姓`、2 名=`姓1, 姓2`、3 名以上=`姓1 et al.`(決定: デザインの「Sauer et al.」「Salimans, Ho」表記の再現規則。`PaperBib.authors_short` は「Liu, Gong, Liu」形式のためここでは使わない)。
- `formatSubLine(entry: CollectionEntry): string` — 以下を「 · 」で連結: ①`formatAuthorsEtAl` ②`paper.venue ?? String(paper.year)`(両方 null なら省略)③`presentation_minutes != null` なら `発表 ${n} 分` ④`assignee != null && !assignee_is_self` なら `担当: ${assignee}` ⑤`note != null` なら note そのまま。例: `Liu et al. · ICLR 2023 · 発表 25 分 · 担当: 佐藤` / `Song, Dhariwal · 2023 · 予備(時間があれば)`。
- `isUnstarted(item: LibraryItemSummary): boolean` — `item.progress_pct === 0 && item.status !== 'done'`(「未着手」判定)。
- ハイライト行判定: `entry.assignee_is_self && isUnstarted(entry.library_item) && entry.library_item.status !== 'reading'`(docs/06 §4.1「未着手かつ自分担当」。決定: §4.3.2 可変スロットの「未着手」表示と同一述語に統一し、reading 中の行はハイライトしない)。
- ステータスラベル(固定マップ): `planned`=読む予定 / `up_next`=すぐ読む / `reading`=読んでいる / `done`=読んだ / `reread`=あとで再読 / `on_hold`=保留。ドット色は `STATUS_COLORS`(tokens §2.4: `--pr-status-*`)。決定: API `Status` 値と `STATUS_COLORS` キーの差(`planned`→`to_read`、`up_next`→`read_next`、他は同名)は 1e 計画で確定済みの変換表 `apps/web/src/lib/status.ts` を import して吸収する(本画面で写像を再定義しない)。
- ミニ進捗ラベル: `last_position.section_display` の先頭トークン(最初の半角スペースまで。例 `"§2.1 整流フロー"` → `"§2.1"`)+ ` · ${progress_pct}%`。決定: `last_position == null`(reading だが位置未保存)のときは `${progress_pct}%` のみを表示する。
- 進捗バー値: `Math.round(progress.done / progress.total * 100)`(total=0 のときは 0)。
- 共有 URL 表示: `share.url.replace(/^https?:\/\//, '')` → `alinea.app/c/x8Kf3qPw`。

## 4. レイアウト・スタイル完全仕様

出典: extract/4b.md(元 HTML 行 394〜506、コンテナ `width:1440px`)。直値はトークン名を併記する。実アプリではフレーム(border/radius/shadow)は描画せずビューポート全面(plans/08 §7.1)。1440px 超はメインエリア(flex:1)が広がり、1440px 未満は最小 560px までフルード(plans/08 §7.2)。

### 4.0 フレーム(デザインキャンバス基準)

1440×900px、背景 #F4F3EF(`--pr-bg-app-alt`)、border 1px solid #D6D3C9、border-radius 10px、box-shadow `0 20px 44px rgba(28,30,34,0.12)`、overflow:hidden、flex column、文字色 #1E2227(`--pr-text`)。

```
┌──────────────────────────────── 1440×900 ─────────────────────────────────┐
│ トップバー h=52 (#FFFFFF, 下border 1px #E6E3DA)                            │
│ [Aロゴ+Alinea w=198] [検索バー 460×32] …(flex:1)… [◷ 30×30] [YK 30×30]     │
├───────────┬───────────────────────────────────────────────────────────────┤
│ サイドバー │ メインエリア flex:1 padding:18px 26px, flex column gap:14px    │
│ w=216     │ ┌ ヘッダ行 (flex, gap:18px, align-items:flex-start) ─────────┐ │
│ #F7F6F2   │ │ 左: パンくず/タイトル+締切バッジ/説明/進捗バー (flex:1)     │ │
│ 右border  │ │ 右: 共有リンクカード w=330 (#FFF, border, radius:10)        │ │
│ 1px       │ └────────────────────────────────────────────────────────────┘ │
│ #E7E4DB   │ ┌ リストカード (#FFF, border 1px #E2DFD5, radius:10, flex:1) ┐ │
│ padding:  │ │ ヘッダ行「発表順 — ドラッグで並べ替え」/「+ 論文を追加」    │ │
│ 12px 10px │ │ 行1(選択ハイライト --pr-as) … 行5                           │ │
│           │ │ (flex:1 スペーサ)                                           │ │
│           │ │ フッタ行(上border 1px #ECE9DF)                              │ │
│           │ └────────────────────────────────────────────────────────────┘ │
└───────────┴───────────────────────────────────────────────────────────────┘
```

本体は `flex:1; display:flex; min-height:0` の 2 カラム(サイドバー+メイン)。メインは `flex:1; min-width:0; overflow:hidden`。

### 4.1 トップバー(AppTopBar)

h=52px、flex:none、背景 #FFFFFF(`--pr-bg-card`)、border-bottom 1px solid #E6E3DA(`--pr-border-header`)、flex row、align-items:center、gap:14px、padding:0 18px。

1. ロゴブロック(flex, align-items:center, gap:8px, width:198px)
   - ロゴマーク: 「A」1 文字。inline-flex 中央、22×22px、border-radius:6px、背景 `var(--pr-a)`(既定 #3E5C76)、文字 #FFFFFF、font-size:11.5px、font-weight:700。
   - ワードマーク: 「Alinea」font-size:14.5px、font-weight:700、letter-spacing:0.5px。
2. 検索バー = SearchBox 共通 §5.13 `variant='global'`(flex, align-items:center, gap:8px, height:32px, width:460px, 背景 #F1EFE9=`--pr-bg-inset`, border-radius:7px, padding:0 12px, font-size:12px, 文字色 #8A8E94=`--pr-text-icon`)
   - MagnifierIcon 共通 §6.1、12×12(viewBox 0 0 12 12。circle cx=5 cy=5 r=3.6 stroke=currentColor stroke-width=1.3 + 線 M8 8 l2.6 2.6 stroke-width=1.3 linecap round)。
   - プレースホルダテキスト: 「ライブラリ全体を検索 — 本文・訳文・メモ・チャット」
   - 右端(margin-left:auto)キーバッジ Keycap 共通 §5.22: 「⌘K」border 1px solid #DAD7CD(`--pr-border-keycap`)、border-radius:3px、padding:0 5px、font-size:9.5px、背景 #FFFFFF(`--pr-bg-control`)、font-family `'IBM Plex Mono', monospace`(`--pr-font-mono`)。
3. スペーサ(flex:1)
4. 時計アイコンボタン(NotificationBell。4a §4.2 が正): 「◷」position:relative、inline-flex 中央、30×30px、border-radius:7px、border 1px solid #E2DFD5(`--pr-border-card`)、色 #5B6067(`--pr-text-sub`)、font-size:13px。テキストグリフ(plans/08 §6.2。SVG 化しない)。
5. アバター: 「YK」(`GET /api/auth/me` のイニシャル)inline-flex 中央、30×30px、border-radius:50%、背景 `var(--pr-as)`(rgba(62,92,118,0.10))、文字 `var(--pr-a)`、font-size:11px、font-weight:700。

### 4.2 左サイドバー(LibraryShell が描画する SidebarNav 共通 §5.14)

width:216px、flex:none、背景 #F7F6F2(`--pr-bg-pane`)、border-right 1px solid #E7E4DB(`--pr-border-pane`)、padding:12px 10px、flex column、gap:2px、font-size:12.5px、文字色 #3A3E44(`--pr-text-nav`)。

- ナビ項目「ホーム」(`href='/'`): padding:7px 10px、border-radius:6px。
- 「ライブラリ」(`href='/library'`): flex row, gap:8px, padding:7px 10px, radius:6px。ラベル(flex:1)+件数「41」= CountBadge 共通 §5.7 `variant='nav'`(font-size:10.5px、#9A9EA4=`--pr-text-muted`)。値は `facets.quick.all`。
- 「語彙帳」(`href='/vocab'`): 同構造、件数「46」(= `GET /api/vocab?limit=1` の `counts.all`)。
- セクション見出し「コレクション」: font-size:10.5px、font-weight:600、#9A9EA4、letter-spacing:0.4px、padding:14px 10px 4px。
- 選択中項目「輪読会 2026-07」(現在の `collection_id` と一致する項目が `active`): flex row, gap:8px, padding:6px 10px, border-radius:6px, 背景 `var(--pr-as)`, 文字 `var(--pr-a)`, font-weight:600。
  - ラベル flex:1
  - 締切ミニバッジ「7/16」= DeadlineBadge 共通 §5.5 `variant='chip'`: inline-flex、height:16px、padding:0 6px、border-radius:3px、背景 rgba(176,104,79,0.14)(`--pr-warn-bg`)、文字 #A05A42(`--pr-warn`)、font-size:9.5px、font-weight:600。`deadline != null` の項目のみ。
  - 件数「5」: font-size:10.5px(色は親の `--pr-a` を継承 = CountBadge `nav` の継承挙動)。値は `item_count`。
- 「Diffusion 蒸留」: padding:6px 10px、件数「8」(10.5px #9A9EA4)。
- 「講義: 生成モデル」: 同、件数「12」。
- 「+ 新規コレクション」: padding:6px 10px、色 #9A9EA4、font-size:11.5px。クリックで行内入力に置換(NewCollectionInlineInput。1d §5.9。§5.8)。
- セクション見出し「保存フィルタ」: 「コレクション」見出しと同スタイル。
- 「締切あり」件数「3」/「cs.CV の未読」件数「7」: 各 padding:6px 10px。`GET /api/saved-filters` の `items[].name` と `count`。クリックで `/library?filter_id={id}`。
- フッタ「設定 · エクスポート」は描画する(決定: extract/4b.md のサイドバーには存在しないが、1e 計画 §1 の「シェル完全形が正・抽出の欠落は省略描画」の決定に従い、共有シェルの `footer`(plans/08 §5.14 の様式)をそのまま描画する。4d と同じ扱い。VRT 基準もシェル完全形で撮る)。

### 4.3 メインエリア

flex:1、min-width:0、padding:18px 26px、flex column、gap:14px、overflow:hidden。

#### 4.3.1 ヘッダ行(CollectionHeader + ShareLinkCard)

flex row、align-items:flex-start、gap:18px。

**左ブロック(CollectionHeader)**(flex column, gap:7px, flex:1, min-width:0):

- パンくず: 「ライブラリ / コレクション」font-size:10.5px、#9A9EA4(`--pr-text-muted`)。「ライブラリ」部分は `/library` へのリンク(装飾なし・同色。決定)。
- タイトル行(flex, align-items:center, gap:10px):
  - 「輪読会 2026-07」(`collection.name`)font-size:19px、font-weight:700。
  - 締切バッジ: 「締切 7/16 — 残り 10 日」inline-flex、height:19px、padding:0 8px、border-radius:4px、背景 rgba(176,104,79,0.14)(`--pr-warn-bg`)、文字 #A05A42(`--pr-warn`)、font-size:10.5px、font-weight:700。DeadlineBadge 共通(h16/17)と寸法が異なるため画面固有要素として実装する(決定)。クリックで DeadlinePopover(§5.7)。`deadline == null` のときはバッジの代わりにテキストリンク「締切を設定」(font-size:10.5px、color `var(--pr-a)`、font-weight:600。決定: 未設定状態はデザイン未描画のため本書で確定)。
  - 「5 本 · 順序付き」font-size:11px、#9A9EA4。`{entries.length} 本 · 順序付き`(「順序付き」は固定文言)。
- 説明文(font-size:12px、#5B6067=`--pr-text-sub`、line-height:1.7): `collection.description` の直後にインラインリンク風テキスト「説明を編集」(color `var(--pr-a)`、font-weight:600、前に半角スペース)。逐語例: 「7/16(木)の輪読会で扱う候補。発表担当は各自 1 本、当日までに「読んだ」まで進めておく。」`description == null` のときはリンク文言を「説明を追加」に変える(決定)。
- 進捗行(flex, align-items:center, gap:10px, margin-top:2px):
  - 進捗バー = ProgressBar 共通 §5.12(`height={4}` `color='green'`): width:220px、height:4px、border-radius:2px、トラック #ECE9DF(`--pr-border-soft`)、position:relative。フィル: position:absolute、inset:0、width:60%(= `progress.done/total`)、border-radius:2px、背景 #659471(`--pr-green`)。
  - ラベル「3/5 読了」(`${done}/${total} 読了`)font-size:10.5px、#5B6067。

**右ブロック = 共有リンクカード(ShareLinkCard)**(width:330px、flex:none、背景 #FFFFFF(`--pr-bg-card`)、border 1px solid #E2DFD5(`--pr-border-card`)、border-radius:10px、padding:12px 14px、flex column、gap:9px。Card 共通 §5.9 + 内側 padding は呼び出し側指定):

- 見出し行(flex, gap:7px): 「閲覧用共有リンク」font-size:11.5px、font-weight:700 + ステータスバッジ「発行済み」(inline-flex、height:16px、padding:0 6px、border-radius:3px、背景 rgba(101,148,113,0.16)(`--pr-src-note-bg`)、文字 #4C7458(`--pr-src-note-fg`)、font-size:9.5px、font-weight:700)。
- URL 行(flex, align-items:center, gap:6px, border 1px solid #E7E4DB(`--pr-border-pane`), border-radius:6px, padding:6px 9px, 背景 #FBFAF7(`--pr-bg-app`)):
  - URL「alinea.app/c/x8Kf3qPw」font-family `'IBM Plex Mono', monospace`、font-size:10.5px、#3C4046(`--pr-text-mid`)、flex:1、white-space:nowrap、overflow:hidden、text-overflow:ellipsis。
  - 「コピー」font-size:10.5px、color `var(--pr-a)`、font-weight:700。button 要素。
- 属性ブロック(flex column, gap:4px, font-size:10.5px, #5B6067):
  - 行1: 「閲覧のみ · アカウント不要 · noindex」(固定文言)
  - 行2(flex, align-items:center, gap:6px): 「共有ページにメモを含める」+ Toggle 共通 §5.8(margin-left:auto。**本画面の実測寸法**: トラック 26×15px、border-radius:8px、背景 `var(--pr-a)`=ON。ノブ absolute top:2px right:2px、11×11px、border-radius:50%、#FFFFFF。決定: Toggle に `size='sm'` を追加し 26×15/ノブ 11×11 をこの寸法で定義する。4f の 30×17 は `size='md'` 既定。OFF 状態は plans/08 §5.8 の OFF 様式(トラック `var(--pr-border-check)`、ノブ top:2px left:2px)を sm 寸法で適用する)+「1 件」(`${included_note_count} 件`。#3C4046、font-weight:600)。
- フッタ行(flex, gap:10px, font-size:10.5px): リンク「共有ページを確認 →」(`href='/c/' + share.token`、`target='_blank' rel='noopener'`、color `var(--pr-a)`、font-weight:600、text-decoration:none。デザインの「→ 4c」の 4c はデザイナー用参照であり実装では出さない)+ 右寄せ(margin-left:auto)button「リンクを無効化」(#9A9EA4)。

共有未発行/無効化状態の描画は §5.5 で確定。

#### 4.3.2 論文リストカード(CollectionEntryList)

背景 #FFFFFF(`--pr-bg-card`)、border 1px solid #E2DFD5(`--pr-border-card`)、border-radius:10px、overflow:hidden、flex:1、flex column(= Card 共通 §5.9)。

ヘッダ行(flex, align-items:center, gap:8px, padding:9px 16px, border-bottom 1px solid #ECE9DF(`--pr-border-soft`), font-size:10.5px, font-weight:600, #9A9EA4):
- 左「発表順 — ドラッグで並べ替え」/ 右(margin-left:auto)button「+ 論文を追加」(同スタイル。クリックで AddPaperPopover §5.6)。

リスト行共通構造(EntryRow。flex, align-items:center, gap:12px, padding:11px 16px, 行間 border-bottom 1px solid #F4F1E9(`--pr-border-row`)。最終行は border-bottom なし):

1. ドラッグハンドル「⋮⋮」: 色 #C6C3B9、font-size:11px、letter-spacing:-2px。`cursor:grab`、dnd-kit の `listeners`/`attributes` を付与、`tabIndex=0`(キーボード並べ替え §5.4)。※#C6C3B9 はこの画面固有の実測値であり直値で実装(トークン未定義。決定)。
2. 順序番号バッジ: inline-flex 中央、20×20px、border-radius:50%、font-size:10.5px、font-weight:700。ハイライト行(行1)のみ背景 #26292E(`--pr-elev-bg`)/文字 #FFFFFF、他は背景 #EFEDE6(`--pr-bg-muted`)/文字 #5B6067(`--pr-text-sub`)。表示値は `entry.order`。
3. サムネイル: 30×40px、border-radius:3px、背景 #EFEDE6(`--pr-bg-thumb`)、border 1px solid #E0DDD3(`--pr-border-thumb`)、flex:none。`thumbnail_url != null` なら `<img>`(object-fit:cover)、null ならプレースホルダ面のみ。
4. タイトルブロック(flex:1, min-width:0):
   - タイトル(`paper.title`): display:block、font-size:12.5px、font-weight:600、white-space:nowrap+text-overflow:ellipsis+overflow:hidden。
   - サブ行(`formatSubLine`): display:block、font-size:10px、#9A9EA4。
5. (`assignee_is_self` の行のみ)担当バッジ「担当: 自分」: inline-flex、height:18px、padding:0 8px、border-radius:999px(ピル)、背景 `var(--pr-a)`、文字 #FFFFFF、font-size:9.5px、font-weight:700。
6. 読書ステータス = StatusPill 共通 §5.2 `variant='dot-label'`(inline-flex, align-items:center, gap:5px, font-size:11px): 7×7px 円形ドット(border-radius:50%)+ラベル。ドット色は `--pr-status-*`: すぐ読む=#C49432(`--pr-status-read-next`)/ 読んでいる=`var(--pr-acc)`(`--pr-status-reading`)/ 読んだ=#659471(`--pr-status-done`)。4b 未描画の他 3 値も同マップで描画(読む予定 #9AA0A6 / あとで再読 #8E7AA6 / 保留 #B0ACA2。決定)。
7. 可変スロット(EntryVariableSlot。§3.3 の規則):
   - 未着手(`isUnstarted` かつ status≠reading): 「未着手」font-size:10.5px、#A05A42(`--pr-warn`)、font-weight:600。
   - `status==='reading'`: ミニ進捗(width:90px、flex column、gap:3px)。バー: height:3px、border-radius:2px、トラック #ECE9DF(`--pr-border-soft`)、position:relative。フィル: absolute、inset:0、width:42%(= `progress_pct`)、背景 `var(--pr-a)`(ProgressBar 共通 `height={3}` `color='accent'`)。ラベル「§2.1 · 42%」font-size:9px、#9A9EA4。
   - `status==='done'` かつ `comprehension != null`: 「理解 4/5」(`理解 ${n}/5`)font-size:10.5px、#5B6067。
   - 上記以外(done 未評価・着手済みの非 reading): 「—」font-size:10.5px、#9A9EA4。
8. 行末: ハイライト行は主ボタン「読み始める」(inline-flex、height:24px、padding:0 12px、border-radius:6px、背景 `var(--pr-a)`、文字 #FFFFFF、font-size:11px、font-weight:600。クリックで `/papers/{library_item_id}` へ遷移)。その他の行はオーバーフローメニュー「⋯」(font-size:13px、#B0B4BA(`--pr-text-thumb`)。button、クリック領域 24×24px 中央配置。決定)。

デザイン描画の 5 行(初期 VRT フィクスチャとして固定):

- 行1(背景 `var(--pr-as)` のハイライト行): 番号 1(黒地反転)/ タイトル「Adversarial Diffusion Distillation」/ サブ「Sauer et al. · 2024 · 発表 25 分」/ バッジ「担当: 自分」/ ステータス「すぐ読む」(#C49432)/ 「未着手」(10.5px、#A05A42、weight 600)/ ボタン「読み始める」。
- 行2: 番号 2 / 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」/ サブ「Liu et al. · ICLR 2023 · 発表 25 分 · 担当: 佐藤」/ 「読んでいる」(`--pr-a`)/ ミニ進捗 width:90px・フィル 42%・ラベル「§2.1 · 42%」/ 「⋯」。
- 行3: 番号 3 / 「Consistency Models」/ サブ「Song et al. · ICML 2023 · 担当: 田中」/ 「読んだ」(緑)/ 「理解 4/5」(10.5px、#5B6067)/ 「⋯」。
- 行4: 番号 4 / 「Progressive Distillation for Fast Sampling of Diffusion Models」/ サブ「Salimans, Ho · ICLR 2022 · 担当: 鈴木」/ 「読んだ」(緑)/ 「理解 5/5」(10.5px、#5B6067)/ 「⋯」。
- 行5(border-bottom なし): 番号 5 / 「Improved Techniques for Training Consistency Models」/ サブ「Song, Dhariwal · 2023 · 予備(時間があれば)」/ 「読んだ」(緑)/ 「—」(10.5px、#9A9EA4)/ 「⋯」。

スペーサ(flex:1)の後、フッタ行(flex, align-items:center, gap:8px, padding:9px 16px, border-top 1px solid #ECE9DF(`--pr-border-soft`), font-size:10.5px, #9A9EA4):
「1 論文は複数のコレクションに入れられます · 並び順は共有ページにも反映」(固定文言)。

### 4.4 全 UI 文言(逐語。実装はこの文字列をそのまま使う)

トップバー: 「A」「Alinea」「ライブラリ全体を検索 — 本文・訳文・メモ・チャット」「⌘K」「◷」「YK」

サイドバー: 「ホーム」「ライブラリ」41 /「語彙帳」46 /「コレクション」/「輪読会 2026-07」「7/16」5 /「Diffusion 蒸留」8 /「講義: 生成モデル」12 /「+ 新規コレクション」/「保存フィルタ」/「締切あり」3 /「cs.CV の未読」7 /「設定 · エクスポート」(シェル完全形。§4.2 の決定)

ヘッダ: 「ライブラリ / コレクション」「輪読会 2026-07」「締切 7/16 — 残り 10 日」「5 本 · 順序付き」「7/16(木)の輪読会で扱う候補。発表担当は各自 1 本、当日までに「読んだ」まで進めておく。」「説明を編集」「3/5 読了」

共有リンクカード: 「閲覧用共有リンク」「発行済み」「alinea.app/c/x8Kf3qPw」「コピー」「閲覧のみ · アカウント不要 · noindex」「共有ページにメモを含める」「1 件」「共有ページを確認 →」「リンクを無効化」

リストカード: 「発表順 — ドラッグで並べ替え」「+ 論文を追加」「⋮⋮」「1」「2」「3」「4」「5」「Adversarial Diffusion Distillation」「Sauer et al. · 2024 · 発表 25 分」「担当: 自分」「すぐ読む」「未着手」「読み始める」「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」「Liu et al. · ICLR 2023 · 発表 25 分 · 担当: 佐藤」「読んでいる」「§2.1 · 42%」「⋯」「Consistency Models」「Song et al. · ICML 2023 · 担当: 田中」「読んだ」「理解 4/5」「Progressive Distillation for Fast Sampling of Diffusion Models」「Salimans, Ho · ICLR 2022 · 担当: 鈴木」「理解 5/5」「Improved Techniques for Training Consistency Models」「Song, Dhariwal · 2023 · 予備(時間があれば)」「—」「1 論文は複数のコレクションに入れられます · 並び順は共有ページにも反映」

### 4.5 データフィールド対応表

| 画面要素 | API フィールド(plans/03 §13.1) |
|---|---|
| タイトル「輪読会 2026-07」 | `collection.name` |
| 締切バッジ「締切 7/16 — 残り 10 日」/ サイドバーミニバッジ「7/16」 | `deadline` + `days_left` |
| 説明文 | `description` |
| 「5 本 · 順序付き」 | `entries.length` |
| 進捗バー 60% / 「3/5 読了」 | `progress.done` / `progress.total` |
| 共有バッジ・URL・トグル・「1 件」 | `share.status` / `share.url` / `share.include_notes` / `share.included_note_count` |
| 順序番号 | `entries[].order`(1 始まり) |
| タイトル/著者/会議年/サムネ | `entries[].library_item.paper.{title, authors, venue, year}` / `library_item.thumbnail_url` |
| 「発表 25 分」/「担当: 佐藤」/「担当: 自分」/「予備(時間があれば)」 | `presentation_minutes` / `assignee` / `assignee_is_self` / `note` |
| ステータスドット/「未着手」 | `library_item.status` / `library_item.progress_pct` |
| 「§2.1 · 42%」 | `library_item.last_position.section_display` + `library_item.progress_pct` |
| 「理解 4/5」 | `library_item.comprehension` |
| サイドバー件数 41 / 46 / コレクション件数 / 保存フィルタ件数 | `facets.quick.all` / vocab `counts.all` / `GET /api/collections` の `item_count` / `SavedFilter.count` |

## 5. 状態とインタラクション

### 5.1 デザインに描かれた状態(初期表示)

- サイドバー「輪読会 2026-07」= 選択中(背景 `--pr-as`、文字 `--pr-a`、weight 600)+締切ミニバッジ「7/16」。他ナビ項目は非選択。
- 共有リンク = `status:'active'`(緑バッジ「発行済み」)、`include_notes:true`(トグル ON、ノブ右寄せ)、`included_note_count:1`。
- 行1 = ハイライト(自分担当かつ未着手): 背景 `--pr-as`、番号バッジ黒地反転、「読み始める」CTA。
- 読書ステータス 3 値の描き分け(すぐ読む #C49432 / 読んでいる `--pr-a` / 読んだ #659471)と可変スロット 4 変種(未着手 / ミニ進捗 / 理解 n/5 / —)。
- コレクション全体進捗 60% フィル(緑)+「3/5 読了」。
- ポップオーバー・フレーム外バリエーションなし(extract §0)。

### 5.2 ローディング(決定: デザイン未描画のため本書で確定)

`collectionKeys.detail` 解決前はシェル(トップバー・サイドバー)を即時描画し、メインエリアにスケルトンを表示する。スケルトン片は背景 #EFEDE6(`--pr-bg-muted`)、border-radius:4px、`animation: alinea-pulse 1.2s ease-in-out infinite`(opacity 1→0.55→1。tokens 隣接 ui 層 CSS に定義):

- パンくず位置: 120×10px。タイトル位置: 220×19px。締切バッジ位置: 130×19px。説明位置: 420×12px。進捗バー位置: 220×4px。
- 共有カード位置: 330×150px の Card 枠のみ(内部にスケルトン 3 本: 90×11 / 302×27 / 160×10)。
- リストカード: ヘッダ行(実物)+スケルトン行 5 本(各行: 20×20 円 + 30×40 矩形 + タイトル 45%幅×12px + サブ 30%幅×10px。padding・境界線は実行と同一)。
- サイドバーのコレクション・保存フィルタは `collectionKeys.list` 等の解決までラベル無しの 100×12px スケルトン 3 本。

### 5.3 エラー・空(決定)

- **詳細取得 404**(削除済み・他人の ID): メインエリア全面に EmptyState 共通 §5.21 — title「コレクションが見つかりません」/ description「削除されたか、URL が正しくない可能性があります。」/ action「ライブラリへ戻る」→ `/library`。
- **詳細取得 5xx / ネットワーク**: EmptyState — title「読み込みに失敗しました」/ description「時間をおいて再試行してください。」/ action「再試行」→ `refetch()`。
- **エントリ 0 件**: リストカードのヘッダ・フッタは表示したまま、行領域に EmptyState — title「まだ論文がありません」/ description「「+ 論文を追加」でライブラリの論文をこのコレクションに追加できます。」/ action「+ 論文を追加」(AddPaperPopover を開く)。進捗行は「0/0 読了」ではなくバー+ラベルごと非表示(決定)。
- **ミューテーション失敗**: Toast 共通 §5.20 `kind:'error'`。文言は各節に記載。409/422 は `Problem.title` をそのまま本文に使わず、本書の固定文言を出す。

### 5.4 ドラッグ並べ替え(PUT …/entries/order)

- 実装: `@dnd-kit/core` 6.3.1 + `@dnd-kit/sortable`(`verticalListSortingStrategy`)。決定: センサーは `PointerSensor`(activationConstraint: distance 4px)+ `KeyboardSensor`(ハンドルにフォーカスし Space で持ち上げ、↑/↓ で移動、Space で確定、Esc でキャンセル)。ドラッグ起点はハンドル「⋮⋮」のみ(行全体では掴めない。行クリックと競合させない)。
- ドラッグ中の見た目(決定): 持ち上げた行は `DragOverlay` で複製描画 — 背景 #FFFFFF(`--pr-bg-card`)、box-shadow `var(--pr-shadow-banner)`、border-radius:6px、cursor:grabbing。元位置の行は opacity:0.4。挿入位置は行間に 2px の水平線 `var(--pr-acc)` を表示。
- 確定処理: ドロップで即座にローカル順序を差し替え(楽観的更新: `setQueryData(collectionKeys.detail(id))` で `entries` を新順序+`order` を 1 から振り直し)、`PUT /api/collections/{id}/entries/order` に**全** `entry_ids` を新順序で送る(plans/03 §13.2: 不足は 422)。
- 失敗時: 旧順序へロールバック+Toast error「× 並べ替えを保存できませんでした」+action「再試行」(同 payload を再送)。
- 並び順は共有ページ 4c にも反映される(サーバー側で `collection_entries.position` を共有 API が参照)。

### 5.5 共有リンクカードの状態遷移

`share.status` の 3 値で描画を切り替える(active はデザインどおり §4.3.1):

- **`none` / `revoked`(決定: 未発行の描画)**: バッジを「未発行」(背景 #F1EFE9=`--pr-bg-inset`、文字 #777B81=`--pr-text-sub2`、他寸法は「発行済み」と同一)に差し替え。URL 行の代わりに主ボタン「共有リンクを発行」(height:24px、padding:0 12px、border-radius:6px、背景 `var(--pr-a)`、文字 #FFFFFF、font-size:11px、weight 600、align-self:flex-start)。属性行1「閲覧のみ · アカウント不要 · noindex」は表示、トグル行とフッタ行は非表示。`revoked` の場合は属性行1 の下に補足「以前のリンクは無効です。再発行すると新しい URL になります。」(font-size:10px、#9A9EA4)。
- **発行**: 「共有リンクを発行」→ `POST …/share` → 201 で active 描画へ。409 `conflict`(既に active)は detail を invalidate して再描画のみ。失敗 Toast error「× 共有リンクを発行できませんでした」。
- **コピー**: `navigator.clipboard.writeText(share.url)`。成功でボタン文言を「コピーしました」に 2,000ms 差し替え(同スタイル。決定)。失敗時のみ Toast error「× コピーできませんでした」。
- **メモ含めるトグル**: Toggle 操作で楽観的に `include_notes` を反転+`PATCH …/share`。レスポンスで `included_note_count` を含め detail を invalidate。OFF 時の件数表示(決定): 「1 件」の位置に「0 件」ではなく `included_note_count` の実値を常時表示(OFF でも「1 件」= 含める設定にした場合に共有される one_line_note 非空件数。plans/03 §13.3)。失敗はロールバック+Toast error「× 設定を変更できませんでした」。
- **リンクを無効化**: クリックで RevokeShareModal(Modal 共通 §5.11、`width={380}`、labelledBy=`revoke-share-title`)。見出し「共有リンクを無効化しますか?」(font-size:14px、weight 700)、本文「リンクを知っている人は閲覧できなくなります。再発行すると新しい URL になります。」(font-size:12px、#5B6067、line-height:1.7)、フッタ右寄せに「キャンセル」(h26px、border 1px `--pr-border-control`、bg `--pr-bg-control`、font 11px)+「無効化する」(h26px、bg `var(--pr-warn)`、文字 #FFFFFF、font 11px、weight 600)。確定で `DELETE …/share` → 成功 Toast success「✓ 共有リンクを無効化しました」+ revoked 描画へ。(モーダル各値は決定: デザイン未描画のため 1g のモーダル様式に寸法を合わせた。)
- **共有ページを確認 →**: `/c/{token}` を新規タブで開く(active のときのみ表示)。

### 5.6 論文の追加(AddPaperPopover)

決定(デザイン未描画のため本書で確定):

- 「+ 論文を追加」をアンカーに Popover 共通 §5.10(`width={360}` `placement='bottom-end'` `caret={true}` caretOffset right:26px)。
- 内部: 検索入力(height:30px、背景 `--pr-bg-inset`、border-radius:6px、padding:0 10px、font-size:11.5px、プレースホルダ「タイトル・著者で検索」、autoFocus)+ 結果リスト(最大 10 件、`GET /api/library-items?q={q}&limit=10`、300ms デバウンス。決定: リストは max-height:320px、overflow-y:auto)。
- 結果行(flex、gap:10px、padding:8px 12px、hover 背景 `--pr-bg-hover`): サムネ 22×29px + タイトル(font-size:11.5px、weight 600、ellipsis)+著者行(font-size:9.5px、#9A9EA4)。追加済み(`existingLibraryItemIds` に含まれる)は右端に「追加済み」(font-size:9.5px、#9A9EA4)を出し disabled。
- 行クリック → `POST …/entries` → 成功でリスト末尾に行が増える(invalidate)+ポップオーバーは開いたまま(連続追加可)。409 `duplicate` は Toast error「× すでにこのコレクションにあります」。
- `q` 空: 「ライブラリから検索して追加します」(font-size:10.5px、#9A9EA4、padding:12px)。結果 0 件: 「見つかりませんでした」(同スタイル)。

### 5.7 コレクション属性の編集

- **説明インライン編集(決定)**: 「説明を編集」クリックで説明文ブロックを編集モードに置換 — `<textarea>`(width:100%、max-width:560px、min-height:56px、font-size:12px、line-height:1.7、border 1px `--pr-border-control`、border-radius:6px、padding:8px 10px、背景 `--pr-bg-card`)+ 直下に「保存」(h24px、bg `var(--pr-a)`、白字、font 11px)/「キャンセル」(h24px、font 11px、#777B81)。保存 = `PATCH /api/collections/{id}` `{ description }`(空文字は `null` 送信)。⌘Enter=保存、Esc=キャンセル。失敗 Toast error「× 説明を保存できませんでした」。
- **締切編集(DeadlinePopover。決定)**: 締切バッジ(または「締切を設定」)をアンカーに Popover(`width={220}` `placement='bottom-start'`)。内部: `<input type="date">`(height:28px、font-size:11.5px、border 1px `--pr-border-control`、border-radius:6px)+ 行(gap:8px)「保存」(h24px、primary)/「締切を削除」(font-size:10.5px、#9A9EA4。`deadline != null` のときのみ)。保存 = `PATCH` `{ deadline: "YYYY-MM-DD" | null }`。成功でヘッダバッジ・サイドバーミニバッジ両方が更新される(list invalidate)。失敗 Toast error「× 締切を保存できませんでした」(決定)。
- **エントリ属性編集(EntryMetaPopover。決定)**: オーバーフローメニュー「担当・発表情報を編集」から開く(アンカー=「⋯」ボタン、`width={260}` `placement='bottom-end'`)。フィールド(ラベル font-size:10px、#9A9EA4 / 入力 height:28px、font-size:11.5px、border 1px `--pr-border-control`、radius 6px): ①「担当」テキスト入力+チェックボックス「自分が担当」(14×14px、plans/08 §5.15 のチェック様式。ON で `assignee_is_self:true`、テキストは無効化しプレースホルダ「自分」)②「発表時間(分)」number 入力(min=1, max=999)③「注記」テキスト入力(プレースホルダ「予備(時間があれば) など」)。フッタ「保存」(primary h24px)/「キャンセル」。保存 = `PATCH /api/collection-entries/{id}`(空は null。決定: 「自分が担当」ON のときの送信値は `{ assignee: null, assignee_is_self: true }`)。§2.2 のとおり楽観的更新で即時反映し、失敗時はロールバック+Toast error「× 保存できませんでした」(決定)。
- **オーバーフローメニュー「⋯」(決定)**: Popover(`width={180}` `placement='bottom-end'`)。項目(padding:7px 12px、font-size:11.5px、#3C4046、hover 背景 `--pr-bg-hover`): 「開く」(→ `/papers/{library_item_id}`)/「担当・発表情報を編集」(→ EntryMetaPopover)/「コレクションから外す」(color `var(--pr-warn)`)。「外す」= `DELETE /api/collection-entries/{id}`(確認なし。楽観的に行を除去し `order` を詰める)→ Toast success「✓ コレクションから外しました(論文はライブラリに残ります)」。失敗はロールバック+Toast error「× 操作に失敗しました」。
- **ハイライト行の編集導線(決定)**: ハイライト行は「⋯」の代わりに CTA を持つため、行ホバー時のみ「⋯」を CTA の左(gap:12px の位置)に表示する(非ホバー時は非表示。既定状態の VRT はデザインと一致)。

### 5.8 新規コレクション(NewCollectionInlineInput。決定: 1d §5.9 に従う)

サイドバー「+ 新規コレクション」はモーダルではなく、1d §5.9 で確定済みの行内入力(NewCollectionInlineInput。シェル側実装)をそのまま使う: クリックで当該行が入力(height:26px、プレースホルダ「コレクション名」、autoFocus)に置換 → Enter で `POST /api/collections` `{ name }`(前後空白 trim。trim 後空は送信しない)→ 201 で `collectionKeys.list()` invalidate+`/collections/{id}` へ遷移。Esc または空値 blur で取消。締切はこの時点では設定せず、遷移先の DeadlinePopover(§5.7)で設定する。失敗 Toast error「× 作成できませんでした」(決定)。

### 5.9 その他のインタラクション

- **読み始める**: `/papers/{library_item_id}` へ遷移するのみ。ステータスは変更しない(P6「ステータス自動変更はせず提案のみ」)。
- **行ホバー(決定)**: 非ハイライト行は hover で背景 `var(--pr-bg-hover)`(#FAF9F5)。ハイライト行は hover でも `--pr-as` のまま。カーソルは行=default、ハンドル=grab、ボタン/リンク=pointer。
- **行クリック(決定)**: 行本体(ハンドル・ボタン・メニュー以外)のクリックでも `/papers/{library_item_id}` へ遷移する。
- **⌘K / 検索バークリック**: GlobalSearchOverlay(1e 計画で定義する共通横断検索ドロップダウン)を開く。本画面では表示のみ担い、実装は 1e 計画に従う。
- **◷ ボタン**: 通知 Popover(4a 計画の NotificationPopover、width 352、caret right:26px)を開く。
- **フォーカス**: すべてのインタラクティブ要素は `focus-visible` で `outline: 1.5px solid var(--pr-acc); outline-offset: 1px`(plans/08 §5 共通)。
- **ダークモード**: 全直値はトークン参照のため `html[data-theme="dark"]` で自動追随(plans/08 §8)。警告色 #A05A42・緑 #659471・すぐ読む #C49432 はテーマ非依存固定(plans/08 §2.2)。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Playwright + `@storybook/test-runner` のスクリーンショット比較(1440×900、ライトテーマ、アクセント slate、デザイン描画データのフィクスチャ)で以下を検証する:

- [ ] トップバー: h52px、ロゴブロック w198px、検索バー 460×32px(bg #F1EFE9、radius 7px)、⌘K キーキャップ(mono 9.5px)、◷ 30×30px(border #E2DFD5)、アバター 30×30px 円(bg rgba(62,92,118,0.10))。
- [ ] サイドバー: w216px、bg #F7F6F2、border-right #E7E4DB。「輪読会 2026-07」がアクティブ様式(bg --pr-as、文字 --pr-a、weight 600)+締切ミニバッジ「7/16」(h16px、bg rgba(176,104,79,0.14)、#A05A42、9.5px/600)+件数「5」がアクセント色継承。
- [ ] ヘッダ: パンくず 10.5px #9A9EA4、タイトル 19px/700、締切バッジ h19px・padding 0 8px・radius 4px・10.5px/700、「5 本 · 順序付き」11px #9A9EA4、説明 12px #5B6067 lh1.7+「説明を編集」アクセント 600、進捗バー 220×4px(トラック #ECE9DF、フィル 60% #659471)+「3/5 読了」10.5px #5B6067。
- [ ] 共有カード: w330px、padding 12px 14px、gap 9px。「発行済み」バッジ(h16px、bg rgba(101,148,113,0.16)、#4C7458)。URL 行(border #E7E4DB、radius 6px、padding 6px 9px、bg #FBFAF7、URL は IBM Plex Mono 10.5px #3C4046 ellipsis)。トグル 26×15px ON(bg --pr-a、ノブ 11×11 右寄せ)。フッタ「共有ページを確認 →」アクセント/「リンクを無効化」#9A9EA4。
- [ ] リストカード: ヘッダ行 padding 9px 16px・border-bottom #ECE9DF・10.5px/600 #9A9EA4。行 padding 11px 16px・gap 12px・行間 border #F4F1E9・最終行 border なし。フッタ行 border-top #ECE9DF・逐語文言。
- [ ] 行1: 背景 --pr-as、番号バッジ 20×20 円 #26292E/#FFF、「担当: 自分」ピル(h18px、radius 999px、bg --pr-a、白 9.5px/700)、「未着手」#A05A42 10.5px/600、「読み始める」h24px・padding 0 12px・radius 6px・bg --pr-a・白 11px/600。
- [ ] 行2〜5: 番号バッジ #EFEDE6/#5B6067、サムネ 30×40(bg #EFEDE6、border #E0DDD3、radius 3px)、タイトル 12.5px/600 ellipsis、サブ行 10px #9A9EA4、ステータスドット 7×7px(#C49432 / --pr-a / #659471)、行2 ミニ進捗 90px・バー 3px・フィル 42%・ラベル 9px #9A9EA4、行3「理解 4/5」・行5「—」、「⋯」13px #B0B4BA。
- [ ] 追加 VRT 状態: (a) 共有未発行(「未発行」バッジ+「共有リンクを発行」ボタン)(b) エントリ 0 件 EmptyState (c) ローディングスケルトン (d) ダークテーマ全景 (e) 無効化確認モーダル。

### 6.2 機能検証チェックリスト

- [ ] `/collections/{id}` 初回表示で `GET /api/collections/{id}` が 1 回だけ呼ばれ、entries が `order` 昇順で描画される。
- [ ] 未認証アクセスは `/login` へリダイレクトされる。404 ID は「コレクションが見つかりません」EmptyState。
- [ ] ドラッグ&ドロップで行を移動すると即座に順序番号が振り直され、`PUT …/entries/order` に全 entry_ids が新順序で送られる。失敗時は旧順序に戻り、error Toast+「再試行」が出る。キーボード(Space→矢印→Space)でも並べ替えできる。
- [ ] 「+ 論文を追加」で検索→選択すると `POST …/entries` が呼ばれ末尾に行が追加される。重複追加は 409 で「× すでにこのコレクションにあります」Toast。追加済み論文は候補で disabled。
- [ ] 「⋯」→「コレクションから外す」で行が即時消え `DELETE /api/collection-entries/{id}` が呼ばれる。LibraryItem 自体は削除されない(ライブラリ 41 件のまま)。
- [ ] EntryMetaPopover で担当/自分フラグ/発表時間/注記を保存すると `PATCH /api/collection-entries/{id}` が呼ばれ、サブ行・「担当: 自分」ピル・ハイライト状態が仕様どおり再計算される(自分担当+未着手にするとハイライト+CTA が現れる)。
- [ ] 「説明を編集」→保存で `PATCH /api/collections/{id}` が呼ばれ本文が更新される。Esc でキャンセル、⌘Enter で保存。
- [ ] 締切バッジから日付を変更/削除でき、ヘッダバッジとサイドバーミニバッジの両方に反映される。`days_left` の 3 文言(残り n 日 / 今日 / 超過 n 日)が正しく出る。
- [ ] 進捗バーと「3/5 読了」が `progress.done/total` と一致する(status=done の件数)。
- [ ] 共有: 未発行→「共有リンクを発行」で active になり 8 文字トークン URL が表示される。「コピー」でクリップボードに `https://alinea.app/c/{token}` が入り文言が 2 秒「コピーしました」になる。トグルで `PATCH …/share` が飛び `included_note_count` 表示が維持される。「リンクを無効化」は確認モーダル経由で `DELETE …/share`、revoked 描画+再発行導線になる。再発行すると**新しい**トークンになる。
- [ ] 「共有ページを確認 →」が `/c/{token}` を新規タブで開く。
- [ ] 「読み始める」・行クリック・「開く」が `/papers/{library_item_id}` へ遷移し、ステータスは変更されない(P6)。
- [ ] サイドバー「+ 新規コレクション」→行内入力に名前を入れ Enter で `/collections/{新ID}` へ遷移し、サイドバー一覧に件数 0 で現れる(Esc / 空値 blur で取消)。
- [ ] 他画面で読書後に戻る(ウィンドウフォーカス)と行ステータス・ミニ進捗・全体進捗が再取得で更新される。
- [ ] `role`/`aria`: Toggle=`role="switch"`、モーダル=`role="dialog" aria-modal`、ドラッグハンドルがフォーカス可能、Toast=`aria-live="polite"`。
