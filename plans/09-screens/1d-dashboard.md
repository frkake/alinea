# 画面 1d: ダッシュボード(ホーム)

> 対象読者と前提: 本書は「Alinea — 論文読解ワークベンチ」のフロントエンド実装者向けに、画面 1d(ダッシュボード)を確定デザインと 100% 一致させるための完全仕様である。機能仕様は docs/06 §6(ダッシュボード)・docs/02(取り込みパイプライン)を正とし、ピクセル値は抽出ファイル extract/1d.md の値を逐語で採用する(本書 §4 に全量転記済み)。共通コンポーネント名は plans/08-design-system.md、API エンドポイント名は plans/03-api.md、データ型は plans/02-data-model.md に従う。技術スタック: Next.js 15(App Router)+ React 19 + TypeScript 5 + Tailwind CSS v4 + TanStack Query v5 + Zustand。

## 1. 概要とルート

- **ルートパス(確定)**: `/`。ファイルは `apps/web/src/app/(app)/page.tsx`。ルートグループ `(app)` は認証必須画面(1d/1e/4a/4d/4e/4f/4b/ビューア系)の共通レイアウト `apps/web/src/app/(app)/layout.tsx`(LibraryShell = トップバー+サイドバー。components/shell/LibraryShell.tsx)を持つ。
- **認証**: 必須。`(app)/layout.tsx` がセッション確認(`GET /api/auth/me`)を行い、401 なら `/login?next=/` へ `router.replace` する(plans/01 §2.1)。CSR(SSR しない。plans/01 の決定: SSR は共有ページ 4c のみ)。
- **画面の役割**: ログイン直後の画面。「次に何を読むか」に 5 秒で答える(docs/06 §6)。構成は 5 ブロック — 続きを読む / すぐ読むキュー / 締切が近い / 最近追加 / 控えめな統計(今週)。
- **本書で確定する画面間ルート(1d から遷移する先)**:
  - ライブラリ(1e/4a): `/library`
  - 語彙帳(4d): `/vocab`
  - コレクション詳細(4b): `/collections/{collection_id}`
  - 横断検索全結果(4e): `/search?q={q}`
  - 設定(4f): `/settings`(カテゴリ指定は `/settings?category={id}`。id は 4f 計画書の 8 カテゴリ: `account` / `display` / `translation` / `reading` / `chat` / `notifications` / `export` / `extension`)
  - ビューア(1a/1b/1c/2a/1h): `/papers/{library_item_id}`。決定: ビューアのルートは `/papers/[itemId]`(App Router のパラメータ名 `[itemId]` = library_item_id)とする。理由: LibraryItem がユーザー資産の単位(plans/02)であり、URL に paper_id を出すより短く一意。ブロック深リンクは `?block=blk-…` クエリで表す(ハッシュ不可)。前回位置・表示モードはビューア側が `GET /api/library-items/{id}/viewer` の `last_position` から復元するため、1d はクエリパラメータを付けない。

## 2. データ要件

### 2.1 使用 API エンドポイント一覧(plans/03 の名前)

| # | エンドポイント | 用途 | 取得タイミング |
|---|---|---|---|
| 1 | `GET /api/dashboard`(plans/03 §5.12) | メイン領域の全データ(continue_reading / up_next_queue / deadlines / recent / stats) | ページマウント時。staleTime 15,000ms |
| 2 | `GET /api/auth/me`(§2.6) | トップバーのアバターイニシャル・通知未読ドット(`unread_notifications`) | LibraryShell マウント時(全画面共通)。staleTime 60,000ms |
| 3 | `GET /api/library-items/facets`(§5.2) | サイドバー「ライブラリ」件数(`quick.all` = 41) | LibraryShell マウント時。staleTime 60,000ms |
| 4 | `GET /api/collections`(§13.1) | サイドバー「コレクション」節(名称・件数 `item_count`・締切 `deadline`) | LibraryShell マウント時。staleTime 60,000ms |
| 5 | `GET /api/saved-filters`(§5.14) | サイドバー「保存フィルタ」節(名称・`count`) | LibraryShell マウント時。staleTime 60,000ms |
| 6 | `GET /api/vocab?limit=1`(§11.1) | サイドバー「語彙帳」件数(レスポンス `counts.all` = 46)。決定: 件数専用エンドポイントは追加せず本 API の counts を流用する | LibraryShell マウント時。staleTime 60,000ms |
| 7 | `PUT /api/library-items/queue-order`(§5.7) | キューのドラッグ並べ替え確定 | ドロップ時(mutation) |
| 8 | `PATCH /api/library-items/{id}`(§5.4) | 提案タグの承認(`tags` 全置換) | 提案タグチップクリック時(mutation) |
| 9 | `GET /api/search/preview`(§15.2) | トップバー検索のドロップダウン(共通シェル。詳細仕様は 1e 計画書) | 検索入力 200ms デバウンス |
| 10 | `GET /api/notifications`(§16.1) | 通知ポップオーバー(共通シェル。詳細仕様は 4a 計画書) | ◷ クリック時 |

### 2.2 TanStack Query のキー設計

キーはすべて `as const` のタプル。`apps/web/src/lib/query-keys.ts` に集約する。

```ts
// apps/web/src/lib/query-keys.ts(1d 関連の抜粋)
export const qk = {
  me: ['me'] as const,                                  // GET /api/auth/me
  dashboard: ['dashboard'] as const,                    // GET /api/dashboard
  libraryFacets: ['library-items', 'facets', {}] as const, // GET /api/library-items/facets(無フィルタ)
  collections: ['collections', 'list'] as const,        // GET /api/collections
  savedFilters: ['saved-filters'] as const,             // GET /api/saved-filters
  vocabCounts: ['vocab', 'counts'] as const,            // GET /api/vocab?limit=1(counts のみ使用)
  searchPreview: (q: string) => ['search', 'preview', q] as const,
  notifications: ['notifications', 'list'] as const,
} as const;
```

- `useQuery({ queryKey: qk.dashboard, queryFn, staleTime: 15_000, refetchOnWindowFocus: true })`。
- ミューテーション後の無効化(確定):
  - キュー並べ替え成功 → 無効化なし(楽観更新をサーバー応答で確定)。失敗 → `invalidateQueries({ queryKey: qk.dashboard })`。
  - 提案タグ承認成功 → `setQueryData(qk.dashboard, …)` で該当 `LibraryItemSummary` をレスポンス値に差し替え(全体 refetch はしない)。
  - 通知の既読化・2 択消化(ポップオーバー内) → `invalidateQueries({ queryKey: qk.me })`(未読ドット更新)。

### 2.3 リアルタイム更新(SSE + ポーリングフォールバック)

plans/01 §5 の決定に従う。LibraryShell がユーザー単位 SSE `GET /api/events` に EventSource で常時接続する(plans/01 は `/api/v1/events` と表記するが、plans/03 §1.1 の「URL バージョニングは行わない」決定に合わせ実装パスは `/api/events` とする。決定)。1d が消費するイベント:

| event | 1d での処理 |
|---|---|
| `job.progress` | `data.library_item_id` が `recent.items` に含まれる場合、`setQueryData(qk.dashboard)` で該当カードの `pipeline.stage`(= payload `stage`)/ `pipeline.progress_pct`(= payload `progress_percent`。plans/01 §5 のフィールド名)を差し替える(リフェッチしない)。決定: `readable_upto` は payload に含まれないため部分更新しない — `stage` が前回値から変化したイベント(readable 到達・complete 到達を含む)では 2,000ms スロットルの `invalidateQueries(qk.dashboard)` を併用し、`readable_upto` と完了形カードへの切替はリフェッチで取得する。`library_item_id` が `recent.items` に含まれない場合も同スロットルで `invalidateQueries(qk.dashboard)`(新規取り込みがカード追加になるため) |
| `job.failed` | 該当カードの `pipeline.stage='failed'` を反映(§5.6 の失敗表示) |
| `translation.unit_completed` | `total_progress` を該当カードの `pipeline.progress_pct` に反映 |
| `notification.created` | `setQueryData(qk.me)` で `unread_notifications` を +1(未読ドット即時表示) |

- **ポーリングフォールバック(plans/01 §5 の値)**: EventSource が 3 回連続で接続失敗している間、`qk.dashboard` に `refetchInterval: 10_000` を適用する(処理中カードが 1 枚以上あるときのみ。全カード完了時は interval を付けない)。SSE 復帰でポーリング停止。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`■` = plans/08 の共通コンポーネント、`◇` = 共通シェル(apps/web/src/components/shell/。1d/1e/4a/4d/4e で共用。LibraryShell(定義の正は 4a 計画書)を使用)、`●` = 1d 固有(apps/web/src/app/(app)/_components/)。

```
(app)/layout.tsx
└─ ◇ LibraryShell
   ├─ ◇ AppTopBar
   │  ├─ ◇ AppLogo(Aマーク+Alinea)
   │  ├─ ■ SearchBox(variant='global')+ ◇ GlobalSearchDropdown(■ Popover width=560。詳細は 1e 計画書)
   │  ├─ ◇ NotificationButton(◷+未読ドット)+ ◇ NotificationPopover(■ Popover width=352。詳細は 4a 計画書)
   │  └─ ◇ UserAvatar
   ├─ ■ SidebarNav(main / sections / footer。■ CountBadge variant='nav'、■ DeadlineBadge variant='chip')
   │  └─ ● NewCollectionInlineInput(「+ 新規コレクション」クリック時の行内入力。§5.9)
   └─ {children} = page.tsx
      └─ ● DashboardPage
         ├─ ● ContinueReadingSection
         │  └─ ● ContinueReadingCard ×0..3(■ Card、■ ProgressBar color='accent')
         ├─ 下段グリッド
         │  ├─ 左列
         │  │  ├─ ● UpNextQueueSection
         │  │  │  ├─ ● QueueList(dnd-kit SortableContext)
         │  │  │  │  └─ ● QueueRow ×n(● PriorityChip、■ DeadlineBadge variant='chip' withLabel)
         │  │  │  └─ ● QueueWarningBanner
         │  │  └─ ● RecentlyAddedSection
         │  │     └─ ● RecentCard ×0..6(■ Card、■ QualityBadge size=14、■ ProgressBar、■ TagChip、● SuggestedTagChip、■ AiMark)
         │  └─ 右列
         │     ├─ ● DeadlineSection → ● DeadlineCard(■ Card、■ ProgressBar color='green'、● DaysLeftBadge)
         │     └─ ● WeeklyStatsSection → ● WeeklyStatsCard(■ Card、● WeeklyBars)
         └─ ● DashboardSkeleton / ■ EmptyState(§5)
```

- 決定: `QualityBadge` の `size` 値域に `14` を追加する(plans/08 §5.3 への追補。1d のメタ行内 14×14px・font 9px 用。色は既存定義のまま)。
- 決定: `TagChip` に `size?: 17 | 18`(既定 17)を追加する(plans/08 §5.16 への追補)。1d の最近追加カードのタグ行は実測 height 18px・padding 0 7px・font 10px のため `size=18` を使う。色・radius は既存定義のまま。
- 決定: 1d キューの優先度バッジは共通 `PriorityBadge`(plans/08 §5.4。テキストのみ)を使わず、画面固有 `PriorityChip` とする。理由: 1d の実測はチップ形(height 17px・背景付き)で PriorityBadge の定義と一致しないため。
- ドラッグ並べ替えライブラリ(決定): `@dnd-kit/core` + `@dnd-kit/sortable`(v6 系)。理由: React 19 対応・キーボード操作サポート・リストソート専用 API を持つ最小構成。

### 3.2 固有コンポーネントの props 型

```ts
// apps/web/src/app/(app)/_components/ 配下。型は packages/api-client 生成型を参照する
import type { LibraryItemSummary, Status } from '@alinea/api-client';

// ContinueReadingSection.tsx
interface ContinueReadingSectionProps { items: LibraryItemSummary[] } // 最大3(API保証)

// ContinueReadingCard.tsx
interface ContinueReadingCardProps { item: LibraryItemSummary }       // last_position は non-null 前提

// UpNextQueueSection.tsx
interface UpNextQueueSectionProps { items: LibraryItemSummary[] }

// QueueRow.tsx
interface QueueRowProps {
  item: LibraryItemSummary;
  index: number;                       // 順位番号 = index + 1
  isLast: boolean;                     // 最終行は border-bottom なし
}

// PriorityChip.tsx
interface PriorityChipProps { priority: 'high' | 'mid' | 'low' }      // ラベル「優先: 高/中/低」

// QueueWarningBanner.tsx
interface QueueWarningBannerProps {
  count: number;                       // キュー本数
  onOrganize: () => void;              // 「整理する」→ /library?status=up_next
  onDismiss: () => void;               // 「×」
}

// RecentlyAddedSection.tsx
interface RecentlyAddedSectionProps { weekCount: number; items: LibraryItemSummary[] }

// RecentCard.tsx(変種はコンポーネント内部で item から導出。§5.5)
interface RecentCardProps { item: LibraryItemSummary }

// SuggestedTagChip.tsx(「提案: cs.CV +」/「solver +」)
interface SuggestedTagChipProps {
  tag: string;
  withPrefix: boolean;                 // true=「提案: {tag} +」(タグ行) / false=「{tag} +」(状態行内)
  pending: boolean;                    // mutation 中は opacity 0.5
  onAccept: (tag: string) => void;
}

// DeadlineSection.tsx
interface DeadlineSectionProps {
  collections: { id: string; name: string; deadline: string; days_left: number;
                 done_count: number; total_count: number }[];
  items: { library_item_id: string; title: string; deadline: string;
           assignee_self: boolean; status: Status }[];
}

// DaysLeftBadge.tsx(「残り 10 日」)
interface DaysLeftBadgeProps { daysLeft: number }

// WeeklyStatsCard.tsx
interface WeeklyStatsCardProps {
  finishedCount: number;               // stats.week.finished_count
  readingHours: number;                // stats.week.reading_hours(小数1桁表示)
  weeklyHours: number[];               // stats.weekly_hours(12要素・古→新)
}

// WeeklyBars.tsx
interface WeeklyBarsProps { values: number[] }  // 12要素。最終要素のみアクセント色
```

### 3.3 表示整形ユーティリティ(確定)

`apps/web/src/lib/format.ts` に置く。1d 以外(1e/4a/3a)も同一関数を使う。

```ts
/** 著者表示: 1人=姓 / 2人=「{姓1} & {姓2}」 / 3人以上=「{姓1} et al.」 */
export function formatAuthorsEtAl(authors: string[]): string;

/** 発表先表示: venue があれば venue(年を含む文字列。例 "ICLR 2023")、なければ String(year) */
export function formatVenueYear(venue: string | null, year: number | null): string;

/** 前回読書・日単位の相対時刻: 当日=「今日」/ 1日前=「昨日」/ 2〜6日前=「n日前」/ 7〜13日前=「先週」/ 14日以上=「M/D」 */
export function formatRelativeDay(iso: string, now?: Date): string;

/** 追加日時: 当日=「今日 H:mm」/ 1日前=「昨日 H:mm」/ 2日以上前=「M/D H:mm」(H は 0 埋めなし24時間制) */
export function formatAddedAt(iso: string, now?: Date): string;

/** 締切日: "YYYY-MM-DD" → 「M/D」(0 埋めなし) */
export function formatDeadline(date: string): string;
```

- 決定: 相対時刻の基準はクライアントのローカルタイムゾーン。日境界は暦日(00:00)比較。
- 決定(境界値): `formatAuthorsEtAl([])` は空文字を返す。`formatVenueYear(null, null)` も空文字を返す。メタ行(著者・会議)は非空の要素のみを「 · 」で連結し、両方空なら行自体を描画しない(高さ詰め)。
- 決定(数値表示): `reading_hours` は `toFixed(1)`(四捨五入・小数 1 桁固定)。WeeklyBars の `title` 内 `{hours}` も同じく小数 1 桁+「h」(例「4.2h」)。
- 決定: 締切アイテムのステータス表示マップ — `planned` / `up_next` →「未着手」(色 #A05A42・font-weight:600)、`reading` / `on_hold` →「読書中」(通常色)、`done` →「読了」、`reread` →「再読予定」(いずれも通常色 #777B81)。デザインに描かれているのは「未着手」のみで、他は本書決定。

## 4. レイアウト・スタイル完全仕様

出典: extract/1d.md(逐語)。色は実装時に plans/08 のトークン(例 #E2DFD5 = `var(--pr-border-card)`、#5B6067 = `var(--pr-text-sub)`)経由で書くこと(hex 直書きは CI で禁止。plans/08 §4.3)。以下の hex はデザイン実測値であり、トークンとの対応は plans/08 §2.1 の表に従う。

### 4.0 デザイナー注記(参考。実装対象外)

- バッジ `1d`、タイトル「ダッシュボード(ホーム)」、説明「「次に何を読むか」に5秒で答える / 続きを読む・すぐ読むキュー・締切・最近追加・控えめな統計」。data-screen-label 属性値 `1d ダッシュボード`。
- フレーム外の別状態ポップオーバー/バリエーション: この画面には存在しない。

### 4.1 レイアウト構造

フレーム全体: width:1440px / height:900px、背景 #F4F3EF、文字色 #1E2227(実アプリではフレーム border/radius/shadow は描かずビューポート全面。plans/08 §7.1)。

```
┌──────────────────────────────── 1440×900 ────────────────────────────────┐
│ トップバー h=52 (#FFFFFF, 下線1px #E6E3DA)                                │
│ [Aロゴ+Alinea w=198][検索バー w=460]……[◷通知][YKアバター]         │
├──────────┬────────────────────────────────────────────────────────────────┤
│ ナビ      │ メイン(flex:1, padding:20px 26px, 縦flex gap:18px)             │
│ サイドバー │ ┌─ 続きを読む: 3カードグリッド(1fr 1fr 1fr, gap:12px) ─┐      │
│ w=216    │ ├─ 下段グリッド(1fr 340px, gap:14px, flex:1) ──────────┤      │
│ #F7F6F2  │ │ 左列(縦flex gap:18px):                │ 右列(340px):    │      │
│ 右線1px  │ │  ・すぐ読むキュー(リスト, max-h:196)   │ ・締切が近い     │      │
│ #E7E4DB  │ │  ・キュー警告バナー                    │ ・今週(統計)     │      │
│          │ │  ・最近追加(2列グリッド, スクロール)    │                 │      │
│          │ └───────────────────────────────────────┴─────────────────┘      │
└──────────┴────────────────────────────────────────────────────────────────┘
```

- トップバー: height:52px、flex:none、背景 #FFFFFF、border-bottom:1px solid #E6E3DA、display:flex / align-items:center / gap:14px / padding:0 18px。
- 本体: flex:1、display:flex、min-height:0。
- ナビサイドバー: width:216px、flex:none、背景 #F7F6F2、border-right:1px solid #E7E4DB、padding:12px 10px、縦flex gap:2px、font-size:12.5px、色 #3A3E44。
- メイン: flex:1、min-width:0、padding:20px 26px、縦flex gap:18px、overflow:hidden。
- 下段グリッド: display:grid、grid-template-columns:1fr 340px、gap:14px、flex:1、min-height:0。
  - 左列: 縦flex、gap:18px、min-width:0、min-height:0。
  - 右列: 縦flex、gap:18px、min-width:0。
- 1440px 超のビューポートでは右列 340px・サイドバー 216px 固定のまま左列(1fr)が広がる。最小幅 1200px(plans/08 §7.2)。

### 4.2 トップバー(◇ AppTopBar。h=52, #FFFFFF, border-bottom:1px #E6E3DA, flex gap:14px, padding:0 18px)

1. ロゴブロック ◇ AppLogo(flex / align-items:center / gap:8px / width:198px。クリックで `/` へ):
   - 「A」マーク: inline-flex中央寄せ、22×22px、border-radius:6px、背景 var(--pr-a,#3E5C76)、文字色 #FFFFFF、font-size:11.5px、font-weight:700。
   - 「Alinea」: font-size:14.5px、font-weight:700、letter-spacing:0.5px。
   - 「Alinea」: font-size:9.5px、色 #B0B4BA、letter-spacing:1.2px、margin-top:2px。
2. 検索バー ■ SearchBox variant='global'(flex / align-items:center / gap:8px / height:32px / width:460px、背景 #F1EFE9、border-radius:7px、padding:0 12px、font-size:12px、色 #8A8E94):
   - 虫眼鏡 ■ MagnifierIcon 12×12(viewBox 0 0 12 12。円: cx=5, cy=5, r=3.6, stroke currentColor, stroke-width 1.3。柄: (8,8)→(10.6,10.6) の線、stroke-width 1.3、linecap round)。
   - プレースホルダ文言「ライブラリ全体を検索 — 本文・訳文・メモ・チャット」。
   - 右端(margin-left:auto)にキーバッジ「⌘K」: border:1px solid #DAD7CD、border-radius:3px、padding:0 5px、font-size:9.5px、背景 #FFFFFF、font-family 'IBM Plex Mono',monospace(■ Keycap mono)。
3. スペーサ(flex:1)。
4. 通知アイコンボタン ◇ NotificationButton: position:relative、inline-flex中央寄せ、30×30px、border-radius:7px、border:1px solid #E2DFD5、色 #5B6067、font-size:13px、グリフ「◷」。右上に未読ドット: position:absolute、top:5px / right:5px、6×6px、border-radius:50%、背景 #C49432(`unread_notifications > 0` のとき表示)。
5. アバター ◇ UserAvatar「YK」: inline-flex中央寄せ、30×30px、border-radius:50%、背景 var(--pr-as,rgba(62,92,118,0.10))、文字色 var(--pr-a,#3E5C76)、font-size:11px、font-weight:700。表示文字は `display_name` から導出したイニシャル 2 文字(決定: 空白区切りの先頭 2 語の頭文字を大文字化。1 語なら先頭 2 文字。`avatar_url` があれば画像を円形表示しイニシャルは出さない)。

### 4.3 ナビサイドバー(■ SidebarNav。w=216, #F7F6F2, padding:12px 10px, 縦flex gap:2px, font-size:12.5px, 色 #3A3E44)

- 「ホーム」(1d ではアクティブ): flex / align-items:center / gap:8px / padding:7px 10px、border-radius:6px、背景 var(--pr-as,rgba(62,92,118,0.10))、文字色 var(--pr-a,#3E5C76)、font-weight:600。href=`/`。
- 「ライブラリ」: 同レイアウト(背景なし)。ラベルspanはflex:1、右端に件数(デモ値「41」= `facets.quick.all`。■ CountBadge variant='nav': font-size:10.5px、色 #9A9EA4)。href=`/library`。
- 「語彙帳」: 同上、件数(デモ値「46」= `GET /api/vocab?limit=1` の `counts.all`)。href=`/vocab`。
- セクション見出し「コレクション」: font-size:10.5px、font-weight:600、色 #9A9EA4、letter-spacing:0.4px、padding:14px 10px 4px。
- コレクション行(`GET /api/collections` の items 順。padding:6px 10px、border-radius:6px、href=`/collections/{id}`):
  - デモ行1「輪読会 2026-07」: ラベルflex:1 + 締切バッジ「7/16」(■ DeadlineBadge variant='chip': inline-flex、height:16px、padding:0 6px、border-radius:3px、背景 rgba(176,104,79,0.14)、文字色 #A05A42、font-size:9.5px、font-weight:600。`deadline` 非 null のとき表示)+ 件数「5」(= `item_count`)。
  - デモ行2「Diffusion 蒸留」: 件数「8」。
  - デモ行3「講義: 生成モデル」: 件数「12」。
- 「+ 新規コレクション」: padding:6px 10px、色 #9A9EA4、font-size:11.5px。クリック挙動は §5.9。
- セクション見出し「保存フィルタ」: 「コレクション」と同スタイル。
- 保存フィルタ行(`GET /api/saved-filters` の items 順。href=`/library?filter_id={id}`): デモ「締切あり / 3」「cs.CV の未読 / 7」「高優先度 / 4」(件数 = `count`)。
- スペーサ(flex:1)。
- フッタ「設定 · エクスポート」: padding:6px 10px(padding-top:12px)、色 #777B81、font-size:11.5px、border-top:1px solid #E7E4DB、margin-top:8px。決定: 「設定」「エクスポート」は個別のリンクスパン(「 · 」は非リンク)。「設定」→ `/settings`、「エクスポート」→ `/settings?category=export`。

### 4.4 「続きを読む」セクション(● ContinueReadingSection。縦flex gap:10px)

- 見出し「続きを読む」: font-size:12px、font-weight:700、色 #5B6067。
- カードグリッド: grid-template-columns:1fr 1fr 1fr、gap:12px。データ = `continue_reading`(最大3。件数分だけカードを描き、残りセルは空)。

各カード ● ContinueReadingCard 共通(■ Card ベース): 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:10px、padding:12px 14px、display:flex、gap:12px。カード全体がクリック対象(`/papers/{id}`)。
- サムネイル: 56×74px、border-radius:5px、背景 #EFEDE6、border:1px solid #E0DDD3、flex:none。`thumbnail_url` があれば `object-fit:cover` の画像、null なら中央寄せプレースホルダテキスト(「図1」等の代替として「—」。デザインのプレースホルダ文言は実データ差し込み前提。色 #B0B4BA、font-size:9px)。
- 右側縦flex(gap:5px、min-width:0、flex:1):
  - タイトル(= `paper.title`): font-size:12.5px、font-weight:600、line-height:1.5、2行クランプ(-webkit-line-clamp:2、overflow:hidden)。
  - 著者・会議(= `formatAuthorsEtAl(paper.authors)` + ' · ' + `formatVenueYear(...)`): font-size:10.5px、色 #9A9EA4。
  - 下寄せブロック(margin-top:auto、縦flex gap:5px):
    - 進捗バー ■ ProgressBar(value=`progress_pct`, color='accent'): height:3px、border-radius:2px、トラック背景 #ECE9DF、position:relative。バー本体はspan(position:absolute、inset:0、width:進捗%、border-radius:2px、背景 var(--pr-a,#3E5C76))。
    - 下行(flex / align-items:center、font-size:10.5px、色 #777B81): 左に「前回: {last_position.section_display} · {formatRelativeDay(last_position.saved_at)}」、右端(margin-left:auto)に「再開 →」(色 var(--pr-a,#3E5C76)、font-weight:700)。

デモデータ(VRT シード値): カード1: サムネ「図1」/「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」/「Liu et al. · ICLR 2023」/ 進捗 42% /「前回: §2.1 整流フロー · 昨日」。カード2: サムネ「図1」/「Consistency Models」/「Song et al. · ICML 2023」/ 進捗 78% /「前回: §5 実験 · 3日前」。カード3: サムネ「図2」/「Flow Matching for Generative Modeling」/「Lipman et al. · ICLR 2023」/ 進捗 12% /「前回: §1 はじめに · 先週」。

### 4.5 「すぐ読むキュー」セクション(● UpNextQueueSection。左列上段、縦flex gap:10px)

- 見出し行(flex / align-items:baseline / gap:8px): 「すぐ読むキュー」(font-size:12px、font-weight:700、色 #5B6067)+ サブ「{n} 本 · ドラッグで並べ替え」(font-size:10.5px、色 #9A9EA4。n = `up_next_queue.length`。デモ「6 本 · ドラッグで並べ替え」)。
- リストコンテナ ● QueueList: 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:10px、max-height:196px、overflow-y:auto。
- 各行 ● QueueRow(flex / align-items:center / gap:10px / padding:9px 14px、行間罫線 border-bottom:1px solid #F0EDE4。最終行は罫線なし):
  1. ドラッグハンドル「⋮⋮」: 色 #C6C3B9、font-size:11px、letter-spacing:-2px。`cursor:grab`。
  2. 順位番号: font-size:11px、色 #9A9EA4、width:12px。
  3. タイトル(= `paper.title`): font-size:12.5px、font-weight:600、flex:1、white-space:nowrap、overflow:hidden、text-overflow:ellipsis。
  4. 著者・年(= `formatAuthorsEtAl` + ' · ' + `formatVenueYear`): font-size:10.5px、色 #9A9EA4。
  5. 優先度バッジ ● PriorityChip: inline-flex、height:17px、padding:0 6px、border-radius:3px、font-size:9.5px。
     - 「優先: 高」: 背景 rgba(176,104,79,0.14)、文字色 #A05A42、font-weight:700。
     - 「優先: 中」「優先: 低」: 背景 #F1EFE9、文字色 #777B81、font-weight:600。
     - `priority` が null の行はバッジ非表示(決定)。
  6. 締切バッジ(`deadline` 非 null の行のみ)■ DeadlineBadge variant='chip' withLabel: 「締切 7/16」、背景 rgba(176,104,79,0.14)、色 #A05A42、height:17px、font-weight:600。

デモ行データ(VRT シード値):
1. 「Adversarial Diffusion Distillation」「Sauer et al. · 2024」「優先: 高」「締切 7/16」
2. 「InstaFlow: One Step is Enough for High-Quality Diffusion-Based Text-to-Image Generation」「Liu et al. · 2024」「優先: 中」
3. 「On the Importance of Noise Scheduling for Diffusion Models」「Chen · 2023」「優先: 中」
4. 「Elucidating the Design Space of Diffusion-Based Generative Models」「Karras et al. · NeurIPS 2022」「優先: 低」
5. 「Understanding Diffusion Objectives as the ELBO with Simple Data Augmentation」「Kingma, Gao · NeurIPS 2023」「優先: 低」
6. 「Minimizing Trajectory Curvature of ODE-based Generative Models」「Lee et al. · ICML 2023」「優先: 低」

- キュー警告バナー ● QueueWarningBanner(リスト直下): flex / align-items:center / gap:7px、font-size:10.5px、文字色 #8A6A24、背景 #FFF9F0、border:1px solid #EEDDB8、border-radius:7px、padding:6px 11px。
  文言「キューが {n} 本になっています — 積みすぎかも?」(デモ n=6)+ 右寄せ(margin-left:auto)のアクション「整理する」(font-weight:600、色 #8A6A24)+ 閉じる「×」(色 #B8A26E)。表示条件・解除は §5.4。

### 4.6 「最近追加」セクション(● RecentlyAddedSection。左列下段、縦flex gap:10px、flex:1、min-height:0)

- 見出し行(flex / baseline / gap:8px): 「最近追加」(12px/700/#5B6067)+ サブ「今週 {week_count} 本」(10.5px/#9A9EA4。デモ「今週 6 本」)。
- カードグリッド: grid-template-columns:1fr 1fr、gap:12px、overflow-y:auto、min-height:0、align-content:start、padding-right:2px。データ = `recent.items`(最大6。デザインの表示カードは4枚、残り2本はスクロール外)。

カードA(展開形=要約付き)● RecentCard: 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:10px、padding:12px 14px、縦flex gap:8px。
- 上段(flex gap:11px): サムネ 48×64px(border-radius:5px、背景 #EFEDE6、border:1px solid #E0DDD3、中央「図1」#B0B4BA 9px)+ 縦flex(gap:4px、min-width:0):
  - タイトル「Scaling Rectified Flow Transformers for High-Resolution Image Synthesis」(12.5px/600/line-height:1.5)。
  - メタ行(10.5px/#9A9EA4): 「Esser et al. · 2024 · [A] · 今日 8:02」。[A] はソースバッジ = ■ QualityBadge size=14: inline-flex中央寄せ 14×14px、border-radius:3px、背景 var(--pr-as)、文字色 var(--pr-a)、font-size:9px、font-weight:700、vertical-align:-2px。
- 要約ボックス: font-size:11px、line-height:1.7、色 #3C4046、背景 #FAF9F5、border-radius:6px、padding:8px 10px。先頭ラベル「✦ 3行要約」(色 var(--pr-a)、font-weight:700。✦ は ■ AiMark)、続き「 — ① 整流フローをT2I基盤モデルへ拡張 ② ノイズ配分を再重み付けした学習 ③ 高解像度でDiTを上回る品質…」。組み立て規則: ` — ① {summary_3line[0]} ② {summary_3line[1]} ③ {summary_3line[2]}`、-webkit-line-clamp:2(決定: 末尾の「…」はクランプによる省略表現)。
- タグ行(flex gap:5px):
  - タグチップ ■ TagChip「flow」「text-to-image」(= `tags`): height:18px、inline-flex、padding:0 7px、border-radius:3px、背景 #F1EFE9、色 #5B6067、font-size:10px。
  - 提案タグチップ ● SuggestedTagChip「提案: cs.CV +」(= `suggested_tags[0]`、withPrefix=true): 同寸法だが背景なし、border:1px dashed #D5D1C5、色 #9A9EA4。

カードB(取り込み処理中): 同カード外観、縦flex gap:9px。
- 上段: サムネ 48×64px は未生成表現 — 背景 #F4F2EC、border:1px dashed #D5D1C5、中央「…」(色 #C6C3B9、9px)。
  - タイトル「Improved Techniques for Training Consistency Models」。
  - メタ「Song & Dhariwal · 2023 · 今日 8:04」(ソースバッジなし=品質未確定)。
- パイプライン状態ブロック(縦flex gap:6px):
  - ステータス行(flex gap:7px、font-size:10.5px、色 #5B6067): 「✓ 書誌」(色 #659471)「✓ アブスト訳・要約」(色 #659471)「本文翻訳中 {progress_pct}%」(色 var(--pr-a)、font-weight:600。デモ 68%)。表示規則は §5.5。
  - 進捗バー ■ ProgressBar: height:3px、トラック #ECE9DF、バー width:{progress_pct}%(デモ 68%)、背景 var(--pr-a)。
  - 下行(flex / space-between): 左「{readable_upto} まで読めます · 開いたセクションを優先翻訳」(font-size:10px、色 #9A9EA4。デモ「§3 まで読めます · 開いたセクションを優先翻訳」)、右にプライマリボタン「読み始める」(inline-flex、height:24px、padding:0 12px、border-radius:6px、背景 var(--pr-a,#3E5C76)、文字色 #FFFFFF、font-size:11px、font-weight:600)。
- カードB本体はクリック対象にしない(「読み始める」ボタンのみ遷移。決定: `readable` 到達前の誤タップ防止)。`pipeline.readable_upto` が null(stage が readable 未達)の間は下行の左テキストを「解析中です」とし「読み始める」を非表示(決定)。

カードC(翻訳完了・縮約形): 同カード外観、横flex gap:11px。
- サムネ「図2」(通常スタイル)。
- タイトル「Stochastic Interpolants: A Unifying Framework for Flows and Diffusions」。
- メタ「Albergo et al. · 2023 · [A] · 昨日 19:40」([A] バッジはカードAと同スタイル)。
- 状態行(font-size:10.5px、色 #5B6067): 「✓ 翻訳完了」(✓と語は色 #659471)+「 — 読める状態です」。

カードD(PDF取り込み・提案タグ・縮約形): 同カード外観、横flex gap:11px。
- サムネ「図1」。
- タイトル「DPM-Solver++: Fast Solver for Guided Sampling of Diffusion Models」。
- メタ「Lu et al. · 2023 · [B] PDF 取り込み · 昨日 18:12」。[B] バッジ = ■ QualityBadge size=14 level='B': 14×14px、border-radius:3px、背景 #F1EFE9、文字色 #777B81、font-size:9px、font-weight:700、vertical-align:-2px。「PDF 取り込み」テキストは `source === 'upload'` のとき表示。
- 状態行(10.5px/#5B6067): 「✓ 翻訳完了」(#659471)+「 · 提案タグ: 」+ ● SuggestedTagChip「solver +」(withPrefix=false、チップ枠なしのテキスト表現: 色 var(--pr-a)、font-weight:600)。

### 4.7 右列(340px、縦flex gap:18px)

#### 「締切が近い」カード(● DeadlineSection。縦flex gap:10px)
- 見出し「締切が近い」(12px/700/#5B6067)。
- カード ● DeadlineCard(■ Card): 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:10px、padding:12px 14px、縦flex gap:11px。
  - 上段=コレクション単位(縦flex gap:6px。クリックで `/collections/{id}`):
    - 行1(flex gap:8px): 「輪読会 2026-07」(= `name`。12.5px/600)+ ● DaysLeftBadge「残り {days_left} 日」(height:17px、padding:0 6px、radius:3px、背景 rgba(176,104,79,0.14)、色 #A05A42、9.5px/700。デモ「残り 10 日」。決定: `days_left === 0` は「今日が締切」、負値は「期限超過」と表示し同スタイル)。
    - 行2(10.5px/#777B81): 「コレクション · {total_count} 本中 {done_count} 本読了」(デモ「コレクション · 5 本中 3 本読了」)。
    - 進捗バー ■ ProgressBar color='green'(value = done_count/total_count×100): height:3px、トラック #ECE9DF、バー width:60%、背景 #659471(緑=読了進捗)。
  - 区切り線: height:1px、背景 #F0EDE4。
  - 下段=論文単位(縦flex gap:4px。クリックで `/papers/{library_item_id}`):
    - タイトル「Adversarial Diffusion Distillation」(font-size:12px、font-weight:600、line-height:1.5)。
    - メタ行(10.5px/#777B81): 「{担当} · 締切 {M/D} · {状態}」(デモ「担当発表 · 締切 7/16 · 未着手」。「担当発表」は `assignee_self === true` のとき表示、false なら省略して「締切 7/16 · 未着手」。状態語は §3.3 のマップ。「未着手」のみ色 #A05A42、font-weight:600)。
- 複数件の表示規則(決定): `deadlines.collections` は締切昇順で最大 2 件、`deadlines.items` は最大 3 件を 1 枚のカード内に描画し、各要素間に上記区切り線を挟む。超過分は表示しない(締切の全量はコレクション詳細 4b で見る)。

#### 「今週」統計カード(● WeeklyStatsSection。縦flex gap:10px)
- 見出し「今週」(12px/700/#5B6067)。
- カード ● WeeklyStatsCard(■ Card): 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:10px、padding:14px、縦flex gap:12px。
  - 数値行(flex gap:18px): 「{finished_count}」(font-size:20px、font-weight:700、letter-spacing:-0.3px)+単位「本 読了」(font-size:11px、font-weight:500、色 #9A9EA4、margin-left:3px)/「{reading_hours}」(小数1桁固定。デモ「4.2」)+「時間」(同スタイル)。デモ「3 本 読了」「4.2 時間」。
  - 棒グラフ ● WeeklyBars(flex / align-items:flex-end / gap:4px / height:44px、各バー flex:1、border-radius:2px): 12本。デモの高さ 30%・55%・40%・22%・64%・48%・12%・38%・70%・52%・44%・84%。高さ算出規則(決定): `height% = round(hours / max(5, ...weekly_hours) * 100)`、ただし最小 4%(0 時間でも 44px×4% ≈ 2px の痕跡を残す)。全週 0 のときは全バー 4%。基準値 5 時間の根拠: デモ高さは hours ÷ 5h の百分率と完全一致する(例: 今週 4.2h → 84%)。週最大が 5h を超える場合のみ max(weekly_hours) 正規化に切り替わる(最大バー = 100%)。VRT シードの weekly_hours は `[1.5, 2.75, 2.0, 1.1, 3.2, 2.4, 0.6, 1.9, 3.5, 2.6, 2.2, 4.2]`(古→新)で確定。1〜11本目は背景 #E4E1D7、最終(今週)の12本目のみ背景 var(--pr-a,#3E5C76)。各バーに `title="{n}週前 · {hours}h"`(最終は「今週 · {hours}h」)。
  - フッタ行(flex / space-between、font-size:10px、色 #9A9EA4): 左「直近 12 週の読書時間」、右「詳細 →」(色 var(--pr-a)、font-weight:600)。決定: 「詳細 →」は `/settings?category=reading`(4f「読書の計測と提案」カテゴリ)へ遷移する。理由: v1 に統計詳細画面は存在せず(16画面インベントリ外)、読書時間の計測設定・説明がある実在の到達先へ繋ぐ。統計詳細画面は v2:(専用画面を追加し遷移先を差し替える)。

### 4.8 全UI文言(逐語。実装文字列はこのとおりに書く)

トップバー: 「A」「Alinea」「Alinea」「ライブラリ全体を検索 — 本文・訳文・メモ・チャット」「⌘K」「◷」「YK(デモ)」

サイドバー: 「ホーム」「ライブラリ」「語彙帳」「コレクション」「+ 新規コレクション」「保存フィルタ」「設定 · エクスポート」(デモ行: 「輪読会 2026-07 / 7/16 / 5」「Diffusion 蒸留 / 8」「講義: 生成モデル / 12」「締切あり / 3」「cs.CV の未読 / 7」「高優先度 / 4」)

続きを読む: 「続きを読む」「前回: 」「再開 →」

すぐ読むキュー: 「すぐ読むキュー」「{n} 本 · ドラッグで並べ替え」「⋮⋮」「優先: 高」「優先: 中」「優先: 低」「締切 {M/D}」「キューが {n} 本になっています — 積みすぎかも?」「整理する」「×」

最近追加: 「最近追加」「今週 {n} 本」「✦ 3行要約」「 — ① {…} ② {…} ③ {…}」「提案: {tag} +」「✓ 書誌」「✓ アブスト訳・要約」「本文翻訳中 {n}%」「{§n} まで読めます · 開いたセクションを優先翻訳」「読み始める」「✓ 翻訳完了 — 読める状態です」「✓ 翻訳完了 · 提案タグ: {tag} +」「PDF 取り込み」

右列: 「締切が近い」「残り {n} 日」「コレクション · {m} 本中 {n} 本読了」「担当発表 · 締切 {M/D} · 未着手」「今週」「{n} 本 読了」「{n.n} 時間」「直近 12 週の読書時間」「詳細 →」

状態系(デザイン外・本書決定の文言。定義箇所): 「解析中…」(処理中カードのステータス行、translating_abstract 以前。§5.5)「クォータ待機中」(§5.5)「解析中です」(処理中カード下行、readable 未達。§4.6)「× 取り込みに失敗しました」「再試行」(失敗形。§5.5)「今日が締切」「期限超過」(§4.7)「 · タグ: {tag}」(提案承認後。§5.8)。空状態・エラー・トースト文言は §5.2〜§5.8 の逐語に従う

### 4.9 データフィールド対応表

| UI 要素 | ソースフィールド(`GET /api/dashboard`) |
|---|---|
| 続きを読むカード | `continue_reading[].paper.title / authors / venue / year / thumbnail_url`、`progress_pct`、`last_position.section_display / saved_at` |
| キュー行 | `up_next_queue[]`(§5.7 の手動順)、`priority`、`deadline`、`paper.*` |
| 締切カード上段 | `deadlines.collections[].name / deadline / days_left / done_count / total_count` |
| 締切カード下段 | `deadlines.items[].title / deadline / assignee_self / status` |
| 最近追加カード | `recent.items[].paper.*`、`summary_3line`、`tags`、`suggested_tags`、`quality_level`、`source`、`pipeline.stage / progress_pct / readable_upto`、`added_at` |
| 統計カード | `stats.week.finished_count / reading_hours`、`stats.weekly_hours`(12要素・古→新) |
| サイドバー件数 | `facets.quick.all` / `vocab counts.all` / `collections[].item_count / deadline` / `saved_filters[].count` |
| 未読ドット | `GET /api/auth/me` の `unread_notifications` |

## 5. 状態とインタラクション

### 5.1 デザインに描かれた状態(そのまま実装)

- ナビ「ホーム」選択中(背景 var(--pr-as)、文字色 var(--pr-a)、weight 600)。他ナビ項目は通常状態。
- 通知アイコンの未読ドット(6px、#C49432)= `unread_notifications > 0`。
- 続きを読むカードの進捗 3 バリエーション(42% / 78% / 12%)= `progress_pct` の連続値。
- 優先度バッジ 3 種(高=警告色 / 中・低=グレー)、行 1 のみ締切バッジ。
- キュー積みすぎ警告バナー(§5.4)。
- 最近追加カードの 4 バリエーション(§5.5)。
- ソースバッジ A(アクセント淡色)/ B(グレー)。
- 締切カードの緑進捗バー(60% = 3/5)と「未着手」警告色。
- 統計棒グラフ: 過去 11 週グレー #E4E1D7、今週のみ var(--pr-a)。

### 5.2 ローディング(決定: スケルトン)

`isPending` の間、メイン領域に ● DashboardSkeleton を表示する。シェル(トップバー・サイドバー)は LibraryShell 側で即描画(サイドバー件数のみ個別ローディング: 件数テキストを空にする。スピナーは出さない)。

- スケルトン共通片: `div.alinea-skeleton` — 背景 var(--pr-bg-muted,#EFEDE6)、border-radius:4px、アニメーション `alinea-pulse 1.2s ease-in-out infinite alternate`(opacity 0.55 → 1.0)。
- 形状(実カードと同寸の枠内に配置):
  - 続きを読む: 見出しテキスト実表示+カード枠3枚(border 1px #E2DFD5、radius 10px、padding 12px 14px)。各カード内: 56×74px 矩形(radius 5px)+ 高さ10px 幅100% / 高さ10px 幅70%(タイトル2行)+ 高さ8px 幅50%(メタ)+ 高さ3px 幅100%(バー位置)。
  - キュー: リスト枠(radius 10px)内に行スケルトン 4 本(各: 高さ12px、幅 それぞれ 90% / 75% / 85% / 70%、padding 9px 14px 相当の余白)。
  - 最近追加: カード枠 4 枚、各内部に 48×64px 矩形+テキスト行 2 本(高さ10px 幅100% / 60%)。
  - 右列: 締切カード枠(内部: 高さ12px 幅60%、高さ10px 幅80%、高さ3px 幅100%)+ 統計カード枠(内部: 高さ20px 幅40%、高さ44px 幅100%)。
- 300ms 未満で解決した場合もスケルトンは最低 0ms(遅延表示・最低表示時間の制御はしない。決定: 単純化優先)。

### 5.3 エラー・空状態(決定)

- **取得エラー**(`GET /api/dashboard` 失敗): メイン領域全体を ■ EmptyState に置換 — title「ダッシュボードを読み込めませんでした」、description は Problem.title(RFC 7807)、action「再読み込み」= `refetch()`。黙って空表示にしない(P3)。
- **セクション別空状態**(データ 0 件。いずれも ■ EmptyState をセクション見出しの下に置く。見出しは常に表示):
  - 続きを読む 0 件: title「読みかけの論文はありません」、description「ライブラリから論文を開くとここに表示されます」、action なし。
  - キュー 0 件: リストコンテナの代わりに title「すぐ読むキューは空です」、description「ライブラリで論文を「すぐ読む」にするとここに並びます」。警告バナーは当然非表示。
  - 締切 0 件(collections・items とも空): title「締切はありません」、description なし、カード枠なしで EmptyState のみ。
  - 最近追加 0 件: title「今週追加された論文はありません」、description「取り込みはブラウザ拡張から行えます」。サブ見出しは「今週 0 本」。
  - 統計: 常に表示(全 0 でも「0 本 読了」「0.0 時間」+全バー最小高)。
- **通信不能時の再試行**: TanStack Query 既定(指数バックオフ 3 回)。

### 5.4 キュー警告バナーの表示・解除(決定)

- 表示条件: `up_next_queue.length >= 6` かつ「未解除」(docs/06 §6.2「6 本以上で表示」)。
- 解除の永続化: Zustand ストア `useDashboardUiStore`(persist ミドルウェア、name: `alinea-dashboard-ui` — これが localStorage の実キー名)のフィールド `queueWarnDismissedCount: number | null` に「× を押した時点の本数」を保存する(独立した localStorage キーは作らない。決定)。表示判定は `count >= 6 && count !== queueWarnDismissedCount`。本数が変化(7 本に増える・6 本に戻る等)したら再表示される。
- 「整理する」: `/library?status=up_next` へ遷移(1e をすぐ読むステータスで絞った状態。キューの削減=ステータス変更は 1e の一括操作で行う)。
- 「×」: バナーを即時非表示+上記保存。API 呼び出しなし。

### 5.5 最近追加カードの変種決定規則(決定)

`RecentCard` は `item` から次の順で変種を導出する:

1. `pipeline !== null && pipeline.stage !== 'complete' && pipeline.stage !== 'failed'` → **処理中形(カードB)**。ステータス行の表示規則: 「✓ 書誌」は stage が `structuring` 以降で表示、「✓ アブスト訳・要約」は `readable` 以降で表示、「本文翻訳中 {progress_pct}%」は `translating_body` のとき表示(それ以前は「解析中…」を同位置・同スタイル(色 var(--pr-a)、600)で表示)。`stage === 'waiting_quota'` は「本文翻訳中」の代わりに「クォータ待機中」(同スタイル)。
2. `pipeline?.stage === 'failed'` → **失敗形(本書決定。デザイン未描画)**: カードB の外観で、パイプライン状態ブロックを 1 行に置換 — 「× 取り込みに失敗しました」(font-size:10.5px、色 #A05A42、font-weight:600)+ 右端に「再試行」ボタン(「読み始める」と同寸: h24px、padding 0 12px、radius 6px。ただし背景なし・border 1px #DDD9CF・色 #3C4046)。「再試行」は `POST /api/papers/{paper_id}/reingest` を呼び `invalidateQueries(qk.dashboard)`。黙って壊れない(P3)。
3. 完了(pipeline が null または complete)かつ `added_at` が**今日** → **展開形(カードA)**: 要約ボックス(`summary_3line` 非 null 時)+タグ行(`tags` + `suggested_tags` 先頭 1 件)。`summary_3line` が null なら要約ボックスを省略しタグ行のみ。
4. 完了かつ `added_at` が昨日以前 → **縮約形(カードC/D)**: 状態行「✓ 翻訳完了 — 読める状態です」。`suggested_tags` があれば「✓ 翻訳完了 · 提案タグ: {suggested_tags[0]} +」に置換(カードD)。

- 決定(全変種共通): ソースバッジ ■ QualityBadge は `quality_level` 非 null のときのみメタ行に表示する(null = 品質未確定で非表示。カードB がこの状態)。「PDF 取り込み」テキストは `source === 'upload'` のときバッジ直後に表示する(§4.6 カードD)。

理由: デザインの 4 枚は「今日追加の 2 枚=展開形・処理中」「昨日追加の 2 枚=縮約形」で構成されており、追加日による情報量の減衰として解釈が一意に定まる。

### 5.6 ドラッグ並べ替え(dnd-kit)

- ハンドル「⋮⋮」のみ `listeners` を付与(行全体ドラッグは不可。行クリック遷移と共存させるため)。キーボード: ハンドルにフォーカスして Space で持ち上げ、↑↓ で移動、Space で確定、Esc で取消(dnd-kit KeyboardSensor 既定)。
- ドラッグ中の行: `box-shadow: var(--pr-shadow-float)`、背景 #FFFFFF、`z-index: var(--z-selection-menu)`、他行はギャップを空けて追従(dnd-kit の transform)。決定(デザイン未描画)。
- ドロップ確定: 楽観更新 — `setQueryData(qk.dashboard)` で `up_next_queue` を新順序に並べ替え、`PUT /api/library-items/queue-order` に `{ library_item_ids: [全 ID 新順序] }` を送る。順位番号は配列 index+1 で即時再計算。
- 失敗時: 旧順序へロールバック+■ Toast `{ kind: 'error', message: '並べ替えを保存できませんでした' }`+`invalidateQueries(qk.dashboard)`。

### 5.7 クリック・ホバー(決定を含む全対象)

| 要素 | クリック | ホバー(決定) |
|---|---|---|
| 続きを読むカード全体 / 「再開 →」 | `/papers/{id}` へ遷移(前回位置・モードはビューアが復元) | border-color を #E2DFD5 → var(--pr-am) に 120ms ease-out。cursor:pointer |
| キュー行(ハンドル以外) | `/papers/{id}` | 行背景 var(--pr-bg-hover,#FAF9F5) |
| 最近追加カード(完了形 A/C/D) | `/papers/{id}` | カード border-color → var(--pr-am) |
| 最近追加カードB「読み始める」 | `/papers/{id}`(部分読書。開いたセクションの優先翻訳はビューアが `POST /api/translation-sets/{set_id}/prioritize` を発火) | 背景の明度 92% フィルタ(`filter: brightness(0.92)`) |
| 提案タグチップ | §5.8 | border-color → var(--pr-am)、色 → var(--pr-a) |
| 締切カード上段 | `/collections/{id}` | 行1 タイトルに underline |
| 締切カード下段 | `/papers/{library_item_id}` | タイトルに underline |
| 「詳細 →」 | `/settings?category=reading` | underline |
| サイドバー各項目 | §4.3 の href | 非アクティブ項目の背景 var(--pr-bg-card,#FFFFFF) |
| 通知 ◷ | NotificationPopover 開閉(仕様は 4a 計画書) | border-color → var(--pr-border-control) |
| アバター | `/settings`(アカウントカテゴリ=既定表示) | 同上 |
| ロゴ | `/`(1d 表示中は scroll top のみ) | なし |
| 検索バー | フォーカス(§5.10) | なし |

- フォーカスリング: すべてのインタラクティブ要素に `focus-visible { outline: 1.5px solid var(--pr-acc); outline-offset: 1px }`(plans/08 §5 共通)。

### 5.8 提案タグの承認

- クリックで `PATCH /api/library-items/{id}` に `{ tags: [...item.tags, tag] }` を送る(plans/03 §5.4: suggested_tags に含まれる値が tags に入ると当該提案は消化)。
- 送信中: チップ opacity 0.5・pointer-events:none(`pending`)。
- 成功: レスポンスの `LibraryItemSummary` で `setQueryData` — チップが確定 ■ TagChip 表示に変わる。カードDの状態行では「 · 提案タグ: {tag} +」の部分が「 · タグ: {tag}」(色 #777B81、weight 400)に変わる(決定)。
- 失敗: ■ Toast `{ kind: 'error', message: 'タグを追加できませんでした' }`、チップは元の提案状態へ戻す。
- 1d では提案の却下(×)UI は持たない(デザインに無い。却下は 4a カードの `DELETE /api/library-items/{id}/tag-suggestions/{tag}` で行う)。

### 5.9 「+ 新規コレクション」(決定。デザイン未描画のフロー)

クリックで当該行が行内入力に置き換わる(● NewCollectionInlineInput): height:26px、背景 #FFFFFF、border:1px solid var(--pr-am)、border-radius:6px、padding:0 8px、font-size:11.5px、プレースホルダ「コレクション名」、autoFocus。Enter で `POST /api/collections` `{ name }` → 201 後 `invalidateQueries(qk.collections)` し `/collections/{id}` へ遷移。Esc または空値 blur で取消(元の「+ 新規コレクション」表示に戻す)。名前重複はサーバーが許容するためクライアント検証は空文字のみ。

### 5.10 グローバル検索・キーボードショートカット

- `⌘K` / `Ctrl+K`: 検索バーへフォーカス(LibraryShell のグローバルキーマップ。SearchBox は表示のみ。plans/08 §5.13)。
- フォーカス時の外観変化・インクリメンタル検索ドロップダウン(`GET /api/search/preview`、Popover width 560)・Enter で `/search?q={q}` へ遷移、の詳細仕様は画面 1e 計画書 `plans/09-screens/1e-library.md` に定める(1d は同一の ◇ GlobalSearchDropdown をそのまま使う)。
- `Esc`: ドロップダウン/ポップオーバーを閉じる。

### 5.11 状態遷移まとめ(メイン領域)

```
pending ──成功──▶ loaded(各セクション: データあり | EmptyState)
   │                    ▲       │
   └──失敗──▶ error ──「再読み込み」┘
loaded 中: SSE job.progress/translation.unit_completed → RecentCard を部分更新
          SSE 断 → refetchInterval 10s(処理中カードあり時)
          window focus → refetch(staleTime 15s 超過時)
```

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

VRT は Playwright スクリーンショット(1440×900、DPR=1、ライトテーマ・アクセント slate、フォント読み込み完了待ち)で、extract/1d.md のデモデータを再現するシードフィクスチャ(plans/00 C10 の開発サンプルデータに 1d 用シードを追加)を用いる。決定: シードの `thumbnail_url` はすべて null とし、サムネイルはプレースホルダ表示(§4.4 の「—」/ カードB の「…」)で撮影する(デザインの「図1」「図2」文言はモック用であり再現対象外。サムネ枠の寸法・色のみ検証する)。

- [ ] トップバー: h52px・背景 #FFFFFF・下線 #E6E3DA。ロゴブロック w198px、検索バー w460×h32・背景 #F1EFE9・radius 7px・「⌘K」キーキャップ、◷ ボタン 30×30・未読ドット 6px #C49432(top:5px/right:5px)、アバター 30×30 円形
- [ ] サイドバー: w216px・背景 #F7F6F2・右線 #E7E4DB。「ホーム」アクティブ(背景 var(--pr-as)・文字 var(--pr-a)・600)。コレクション行の締切バッジ「7/16」(h16px、rgba(176,104,79,0.14)/#A05A42)。件数 10.5px #9A9EA4。フッタ「設定 · エクスポート」に上線
- [ ] メイン: padding 20px 26px・縦 gap 18px。下段グリッド `1fr 340px` gap 14px
- [ ] 続きを読む: 3 カード(1fr×3、gap 12px)。カード padding 12px 14px・radius 10px・border #E2DFD5。サムネ 56×74。タイトル 12.5px/600 の 2 行クランプ。進捗バー h3px・width 42%/78%/12%・色 var(--pr-a)。「再開 →」var(--pr-a)/700
- [ ] キュー: リスト max-height 196px・overflow-y:auto で 6 行中スクロール表示。行 padding 9px 14px・罫線 #F0EDE4(最終行なし)。「優先: 高」= rgba(176,104,79,0.14)/#A05A42/700、「優先: 中/低」= #F1EFE9/#777B81/600、h17px。行1のみ「締切 7/16」バッジ
- [ ] 警告バナー: 背景 #FFF9F0・border #EEDDB8・radius 7px・文字 #8A6A24。文言「キューが 6 本になっています — 積みすぎかも?」+「整理する」600 +「×」#B8A26E
- [ ] 最近追加: 2 列グリッド・gap 12px・見出しサブ「今週 6 本」。カードA: 要約ボックス(背景 #FAF9F5・11px/1.7・「✦ 3行要約」var(--pr-a)/700)+タグチップ h18px #F1EFE9 +提案チップ破線 #D5D1C5。カードB: 破線サムネ+「✓ 書誌」「✓ アブスト訳・要約」#659471 +「本文翻訳中 68%」var(--pr-a)/600 +バー 68% +「読み始める」h24px 背景 var(--pr-a) 白 11px/600。カードC: 「✓ 翻訳完了 — 読める状態です」。カードD: [B] バッジ #F1EFE9/#777B81 +「PDF 取り込み」+「solver +」var(--pr-a)/600
- [ ] ソースバッジ: 14×14px・radius 3px・font 9px/700・vertical-align:-2px(A=var(--pr-as)/var(--pr-a)、B=#F1EFE9/#777B81)
- [ ] 締切カード: 「残り 10 日」バッジ h17px。「コレクション · 5 本中 3 本読了」10.5px #777B81。緑バー 60% #659471。区切り線 #F0EDE4。「未着手」#A05A42/600
- [ ] 統計カード: 数値 20px/700/-0.3px+単位 11px #9A9EA4。棒グラフ h44px・gap 4px・12 本(高さ 30/55/40/22/64/48/12/38/70/52/44/84%)・1〜11 本目 #E4E1D7・12 本目 var(--pr-a)。フッタ 10px「直近 12 週の読書時間」「詳細 →」var(--pr-a)/600
- [ ] ダークテーマ・アクセント 4 色でトークン追随が崩れない(Storybook VRT: DashboardPage ×{light,dark}×{slate,green,purple,terracotta} の 8 スナップショット)
- [ ] スケルトン・空状態・エラー状態・警告バナー非表示状態の各スナップショット(§5.2/§5.3 の形状・文言どおり)

### 6.2 機能検証チェックリスト

- [ ] 未認証で `/` にアクセスすると `/login?next=/` へリダイレクトされる
- [ ] `GET /api/dashboard` のレスポンスが §4.9 の対応どおりに描画される(continue_reading 0〜3 件、recent 0〜6 件、queue 0〜n 件で崩れない)
- [ ] 「再開 →」・カードクリックで `/papers/{id}` に遷移し、ビューアが `last_position` の位置・モードで開く
- [ ] キュー行をドラッグすると順位番号が即時更新され、`PUT /api/library-items/queue-order` に全 ID の新順序が送られる。失敗時はロールバック+エラートースト「並べ替えを保存できませんでした」
- [ ] ハンドルのキーボード操作(Space→↑↓→Space)で並べ替えできる
- [ ] キュー 5 本以下で警告バナー非表示、6 本以上で表示。「×」で消え、リロード後も再表示されない(localStorage キー `alinea-dashboard-ui` 内の `queueWarnDismissedCount`)。本数が変わると再表示。「整理する」で `/library?status=up_next` へ遷移
- [ ] 処理中カードが SSE `job.progress` / `translation.unit_completed` で 5% 刻みに進捗更新され、`complete` 到達で完了形カードに変わる(リロード不要)
- [ ] SSE 断(3 回連続失敗)時、処理中カードありなら 10 秒ポーリングで進捗が進む。SSE 復帰でポーリング停止
- [ ] `pipeline.stage='failed'` のカードに「× 取り込みに失敗しました」+「再試行」が出て、再試行で `POST /api/papers/{paper_id}/reingest` が呼ばれる
- [ ] 「読み始める」は `readable_upto` 非 null のときのみ表示され、クリックで部分読書が開始できる
- [ ] 提案タグチップのクリックで `PATCH /api/library-items/{id}`(tags 全置換)が送られ、成功で確定タグ表示に変わる。失敗でトースト+提案状態へ復帰
- [ ] 通知ドットが `unread_notifications > 0` で表示され、SSE `notification.created` で即時点灯する
- [ ] `⌘K` / `Ctrl+K` で検索バーにフォーカスが移る
- [ ] 「+ 新規コレクション」で行内入力 → Enter → 作成 → `/collections/{id}` 遷移、Esc で取消
- [ ] サイドバー件数(ライブラリ/語彙帳/コレクション/保存フィルタ)が各 API の値と一致する
- [ ] 相対時刻・締切表示が §3.3 の規則どおり(境界: 今日/昨日/2日前/6日前/7日前/14日前をユニットテストで固定)
- [ ] 統計カードの棒高さ算出(基準 max(5h, 週最大) 正規化・最小 4%・全 0 週・週最大 5h 超)がユニットテストで固定されている
- [ ] axe による自動 a11y チェックで critical 違反 0 件(ドラッグハンドルに `aria-label="ドラッグして並べ替え"`、進捗バーに `aria-valuenow`、バナーに `role="status"`)
