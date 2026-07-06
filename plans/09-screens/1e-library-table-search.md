# 画面 1e: ライブラリ テーブル+横断検索ドロップダウン+一括操作

> 対象読者と前提: 本書は「訳読 / YAKUDOKU」の apps/web(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4)実装者向けに、確定デザイン画面 1e(ライブラリ テーブルビュー。クイックフィルタ+一括操作+横断検索ドロップダウン)を 1px 単位で再現するための完全仕様である。ピクセル値・UI 文言は抽出ファイル extract/1e.md を正とし、機能仕様は docs/06(§1・§8・§9)、API は plans/03、共通コンポーネント・トークンは plans/08 の識別子をそのまま使う。本書に無い選択肢は存在しない(すべて確定済み)。

## 1. 概要とルート

- **ルート**: `/library`(App Router: `apps/web/src/app/(app)/library/page.tsx`)。
  - **表示モード**: URL クエリ `view=card | table`。画面 1e は `view=table`。**決定: `view` 省略時の既定は `card`(画面 4a)**。理由: 4a がライブラリの主描画であり、localStorage による既定切替は SSR とのハイドレーション不整合を生むため URL のみで状態を表現する。
  - **フィルタ・ソートも URL クエリで表現**し、plans/03 §5.1 のクエリ語彙と同名にする: `quick` / `status`(複数可)/ `tag`(複数可)/ `collection_id` / `quality` / `year`(複数可)/ `filter_id` / `sort` / `order`。例: `/library?view=table&tag=distillation&sort=updated_at&order=desc`。
  - URL 書き換えは `router.replace`(`useRouter` / `useSearchParams`)で行い、履歴を汚さない(戻るボタン 1 回でライブラリ外へ戻れる)。専用フック `useLibraryQueryState()`(`apps/web/src/features/library/useLibraryQueryState.ts`)がクエリ⇄型付きオブジェクトの変換を一元管理する。
- **認証**: 必須(plans/03 の `session` 区分)。未ログインは `(app)` グループの layout が `/login?next=/library` へ `redirect()`。
- **画面の役割**:
  1. 蔵書 41 本規模のライブラリを 10 列固定テーブルで一覧・絞り込み・ソートする(S 系ジョブ「管理」)。
  2. トップバーのグローバル検索(⌘K)から本文・訳文・メモ・チャット・記事を横断するインクリメンタル検索プレビューを出し、該当位置へジャンプする(S5「想起」)。
  3. 行の複数選択→フローティング一括操作バーで一括ステータス変更 / タグ追加 / コレクション追加を行う(一括操作はテーブルビュー専用。docs/06 §8.5)。
- **共通シェル**: トップバー+サイドバーは `LibraryShell`(`apps/web/src/components/shell/LibraryShell.tsx`)としてホーム(1d)/ライブラリ(1e/4a)/語彙帳(4d)/検索結果(4e)と共有する。**決定: シェルの完全形(通知ボタン ◷・サイドバー「+ 新規コレクション」・フッタ「設定 · エクスポート」)は 4a・docs/06 §8.1・plans/08 §5.14 を正とし、1e 抽出のトップバー/サイドバーにこれらが描かれていないのは検索状態にフォーカスした省略描画とみなす**。理由: 同一ルートのビュー切替でシェルが増減するのは仕様矛盾になるため。
- **レンダリング方式(決定)**: `page.tsx` は Client Component(`"use client"`)+ TanStack Query による CSR。サーバープリフェッチは行わない(v1)。理由: 全データがセッション依存・高頻度変化であり、スケルトン表示(§5.2)で初期体感を担保する。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 用途 | 取得タイミング |
|---|---|---|---|
| 1 | `GET /api/library-items`(§5.1) | テーブル行データ。`view=table` の全クエリを転写、`limit=50` | マウント時+フィルタ/ソート変更時+スクロール末尾(cursor) |
| 2 | `GET /api/library-items/facets`(§5.2) | クイックフィルタピル件数・属性ドロップダウン選択肢+件数・見出し「41 本」・サイドバー「ライブラリ 41」 | マウント時+属性フィルタ変更時(`quick` は送らない) |
| 3 | `GET /api/search/preview`(§15.2) | 横断検索ドロップダウン(上位 3 件+total) | 検索入力の 200ms デバウンス後 |
| 4 | `GET /api/saved-filters`(§5.14) | サイドバー「保存フィルタ」一覧(名前+件数) | シェルのマウント時 |
| 5 | `POST /api/saved-filters`(§5.14) | 「この条件を保存」 | 保存ポップオーバーの「保存」押下時 |
| 6 | `GET /api/collections`(§13.1) | サイドバー「コレクション」一覧(名前+件数+締切ミニバッジ)/一括「コレクションへ」の選択肢 | シェルのマウント時 |
| 7 | `GET /api/vocab?limit=1`(§11.1) | サイドバー「語彙帳 46」の件数(`counts.all`) | シェルのマウント時 |
| 8 | `GET /api/auth/me`(§2.6) | アバターイニシャル・通知未読ドット | シェルのマウント時 |
| 9 | `POST /api/library-items/bulk`(§5.6) | 一括操作バーの 3 アクション | アクション実行時 |
| 10 | `GET /api/tags`(§5.13) | 一括「タグ追加」の入力補完 | タグ追加ポップオーバー内で入力 200ms デバウンス |

### 2.2 TanStack Query キー設計

キーはすべて `apps/web/src/features/library/queries.ts` に定数化する。

```ts
// apps/web/src/features/library/queries.ts
import type { LibraryListParams } from './useLibraryQueryState';

export const libraryKeys = {
  list: (params: LibraryListParams) => ['library', 'list', params] as const,   // 無限クエリ(cursor)
  facets: (params: Omit<LibraryListParams, 'quick' | 'sort' | 'order'>) =>
    ['library', 'facets', params] as const,
} as const;
export const searchKeys = {
  preview: (q: string) => ['search', 'preview', q] as const,
};
export const shellKeys = {
  me: ['auth', 'me'] as const,
  savedFilters: ['saved-filters'] as const,
  collections: ['collections'] as const,
  vocabCounts: ['vocab', 'counts'] as const,
  tagSuggest: (q: string) => ['tags', 'suggest', q] as const,
};
```

- 一覧は `useInfiniteQuery`(`getNextPageParam: (last) => last.next_cursor`)。`params` はキー安定化のため配列値をソート済み・undefined 除去で正規化する。
- `staleTime`(決定): list / facets = **30,000ms**、search preview = **60,000ms**、saved-filters / collections / vocab counts / me = **60,000ms**。`placeholderData: keepPreviousData` を list / facets / preview に設定し、フィルタ切替・文字追加入力時に前結果を出したまま更新する(ちらつき防止)。
- 無効化規則: `POST /api/library-items/bulk` 成功 → `['library']` プレフィックスを `invalidateQueries`(list と facets の両方が落ちる)。`POST /api/saved-filters` 成功 → `shellKeys.savedFilters`。

### 2.3 リアルタイム更新(決定)

**SSE・ポーリングは使わない。** 理由: 本画面のデータは自ユーザー操作でのみ変化し(P6: ステータスは勝手に変わらない)、翻訳パイプライン進捗カードはカードビュー(4a)とダッシュボード(1d)の責務。TanStack Query の既定 `refetchOnWindowFocus: true` を維持し、タブ復帰時に最新化する。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`共通` = plans/08 §5 の共通コンポーネント、`固有` = 本画面(`apps/web/src/features/library/` 配下)。

```
(app)/layout.tsx
└─ LibraryShell                                 固有(シェル共有: apps/web/src/components/shell/)
   ├─ TopBar                                    固有
   │  ├─ LogoBlock(「訳」+「訳読」)            固有(shell 内ローカル)
   │  ├─ GlobalSearch                           固有
   │  │  ├─ SearchBox(variant="global")        共通 §5.13
   │  │  └─ SearchDropdown                      固有
   │  │     ├─ Popover(width=560, caret=false) 共通 §5.10
   │  │     ├─ SearchPreviewItem ×3             固有
   │  │     │  └─ SourceBadge                   共通 §5.22
   │  │     └─ (フッタ「すべての結果を表示…」)
   │  ├─ NotificationButton(◷+未読ドット)      固有(4a 仕様。本書では配置のみ)
   │  └─ Avatar(「YK」)                         固有(shell 内ローカル)
   └─ SidebarNav                                 共通 §5.14
      └─ CountBadge(variant="nav")              共通 §5.7
library/page.tsx(view=table 分岐)
└─ LibraryTablePage                              固有
   ├─ LibraryToolbar(見出し行)                  固有
   │  ├─ SegmentedControl(カード/テーブル)      共通 §5.1(size="sm")
   │  ├─ SortMenu(「並び: 更新日 ▾」)           固有 + Popover(width=180)
   │  └─ SaveFilterPopover(「この条件を保存」)  固有 + Popover(width=260)
   ├─ QuickFilterRow                             固有
   │  ├─ FilterChip ×5(すべて/未読/…)          共通 §5.6
   │  └─ AttributeFilterDropdown ×5              固有 + Popover(width=220)
   │     └─ FilterChip(removable。適用中チップ) 共通 §5.6
   ├─ LibraryTable                               共通 §5.15
   │  ├─ StatusPill(variant="dot-label")        共通 §5.2
   │  ├─ QualityBadge(size=17)                  共通 §5.3
   │  ├─ TagChip                                 共通 §5.22
   │  ├─ PriorityBadge                           共通 §5.4
   │  ├─ DeadlineBadge(variant="text")          共通 §5.5
   │  ├─ LibraryTableSkeleton                    固有(§5.2)
   │  └─ EmptyState                              共通 §5.21
   └─ BulkActionBar                              共通 §5.22
      ├─ BulkStatusMenu                          固有
      ├─ BulkTagPopover                          固有
      └─ BulkCollectionPopover                   固有
```

### 3.2 画面固有コンポーネントの props 型

```ts
// apps/web/src/features/library/GlobalSearch.tsx
interface GlobalSearchProps {
  /** 検索ボックス左端の水平位置合わせ用。既定 230(1e 実測: ドロップダウン left:230px) */
  dropdownLeft?: number;
}

// apps/web/src/features/library/SearchDropdown.tsx
import type { components } from '@yakudoku/api-client';
type SearchPreviewHit = components['schemas']['SearchHit_with_paper'];
interface SearchDropdownProps {
  query: string;                      // 表示中の確定クエリ(デバウンス後)
  open: boolean;
  loading: boolean;
  total: number;
  items: SearchPreviewHit[];          // 上位3件
  activeIndex: number;                // キーボード選択中(0..items.length-1、-1=なし)
  anchorRef: React.RefObject<HTMLElement>;
  onNavigate: (hit: SearchPreviewHit) => void;  // §5.4 の遷移規則
  onShowAll: () => void;              // /search?q=… へ
  onClose: () => void;
}

// apps/web/src/features/library/SearchPreviewItem.tsx
interface SearchPreviewItemProps {
  hit: SearchPreviewHit;
  query: string;
  active: boolean;                    // true=bg #FAF9F5+ジャンプ行表示
  onClick: () => void;
}

// apps/web/src/features/library/LibraryToolbar.tsx
import type { SortKey } from '@/components/ui/LibraryTable';
interface LibraryToolbarProps {
  total: number;                              // 「41 本」
  view: 'card' | 'table';
  onViewChange: (v: 'card' | 'table') => void;
  sort: { key: SortKey; dir: 'asc' | 'desc' };
  onSortChange: (s: { key: SortKey; dir: 'asc' | 'desc' }) => void;
  onSaveFilter: (name: string) => Promise<void>;   // POST /api/saved-filters
  canSaveFilter: boolean;                          // フィルタ or 非既定ソートが1つ以上適用中のみ true
}

// apps/web/src/features/library/QuickFilterRow.tsx
import type { components } from '@yakudoku/api-client';
type Facets = components['schemas']['LibraryFacets'];   // §5.2 レスポンス
type Status = components['schemas']['Status'];          // plans/03 §1.6(BulkStatusMenuProps でも同型を使う)
type Quick = 'all' | 'unread' | 'in_progress' | 'done' | 'recheck';
interface QuickFilterRowProps {
  facets: Facets | undefined;         // undefined=読み込み中(件数を「–」表示)
  quick: Quick;
  onQuickChange: (q: Quick) => void;
  applied: AppliedFilters;
  onApply: (next: AppliedFilters) => void;
  savedFilterName: string | null;     // filter_id 適用中の名前(チップ「フィルタ: 締切あり ×」)
  onClearSavedFilter: () => void;
}
type AppliedFilters = {
  status: Status[]; tags: string[]; collectionId: string | null;
  quality: 'A' | 'B' | null; years: number[];
};

// apps/web/src/features/library/AttributeFilterDropdown.tsx
interface AttributeFilterDropdownProps {
  label: 'ステータス' | 'タグ' | 'コレクション' | '品質' | '年';
  mode: 'multi' | 'single';           // status/tag/year=multi、collection/quality=single
  options: { value: string; label: string; count: number }[];
  selected: string[];
  onChange: (values: string[]) => void;
}

// apps/web/src/features/library/BulkStatusMenu.tsx ほか一括系
interface BulkStatusMenuProps { onSelect: (status: Status) => void }
interface BulkTagPopoverProps { onSubmit: (tags: string[]) => void }
interface BulkCollectionPopoverProps {
  collections: { id: string; name: string }[];
  onSelect: (collectionId: string) => void;
}
```

- `LibraryTable` / `BulkActionBar` / `SidebarNav` / `SearchBox` / `FilterChip` / `StatusPill` / `QualityBadge` / `PriorityBadge` / `DeadlineBadge` / `TagChip` / `SourceBadge` / `SegmentedControl` / `Popover` / `EmptyState` / `CountBadge` の props は plans/08 §5 の定義を変更なしで使う。
- **決定(列挙値マッピング)**: API の `Status`(`planned | up_next | reading | done | reread | on_hold`、plans/03 §1.6)と `@yakudoku/tokens` の `STATUS_COLORS` / `STATUS_LABELS` キー(`to_read | read_next | …`、plans/08 §2.4)の対応は `apps/web/src/lib/status.ts` の変換表で吸収する: `planned→to_read`、`up_next→read_next`、`reading→reading`、`done→done`、`reread→reread`、`on_hold→on_hold`。理由: API 列挙は確定済みで変更不可、tokens 側キー名も plans/08 で確定済みのため、境界 1 箇所で写像する。
- **決定(行データ変換)**: `LibraryItemSummary → LibraryTableRow` の整形は `apps/web/src/features/library/toTableRow.ts` に集約する。
  - `authorsLine` = `authors_short + ' · ' + venue + ' ' + year(venue が null なら year のみ)+(arxiv_id があれば ' · arXiv:' + arxiv_id)`。source=upload かつ arxiv_id なしは末尾 `' · アップロード'`。
  - `readingHours` = `reading_seconds_total === 0 ? null : round(reading_seconds_total / 3600, 小数1桁)`(表示 `3.2h`)。
  - `addedAt` / `deadline` = `M/D`(ゼロ埋めなし。`2026-07-16 → 7/16`)。
  - `titleBadge` = `source === 'upload' ? 'pdf_import' : undefined`。

## 4. レイアウト・スタイル完全仕様

値の出典: extract/1e.md(逐語)。色はすべて plans/08 のトークンで書き、実測 hex を括弧で併記する。実アプリではデザインキャンバスのフレーム装飾(1440×900、border 1px #D6D3C9、radius 10px、shadow `--pr-shadow-frame`)は描画せず、ビューポート全面を使う(plans/08 §7.1)。基準ビューポートは 1440×900px、`body { min-width: 1200px }`。

### 4.1 レイアウト構造

ルート面: `background: var(--pr-bg-app-alt)`(#F4F3EF)、`color: var(--pr-text)`(#1E2227)、縦 flex、`position: relative`。

```
┌──────────────────────────────────────────────────────────────┐1440
│ トップバー h52 白 (ロゴ/検索ボックス460px/スペーサ/アバター)     │
│   └(overlay)検索ドロップダウン 560px @top:48px,left:230px z:6  │
├───────────┬──────────────────────────────────────────────────┤
│ サイドバー │ メインカラム flex:1 padding:16px 22px gap:12px     │
│ w216      │  ├ 見出し行(タイトル/件数/ビュー切替/並び/保存)     │
│ #F7F6F2   │  ├ クイックフィルタ行(ピル+ドロップダウン群)       │
│ 右境界線   │  ├ テーブル(白カード、flex:1、ヘッダ+行×7)        │
│ #E7E4DB   │  └(overlay)一括操作バー @bottom:22px 中央 z:5      │
└───────────┴──────────────────────────────────────────────────┘
                                                          h900
```

- トップバー: height:52px、flex:none、`background: var(--pr-bg-card)`(#FFFFFF)、`border-bottom: 1px solid var(--pr-border-header)`(#E6E3DA)、display:flex / align-items:center / gap:14px、padding:0 18px。
- ボディ: flex:1 / display:flex / min-height:0。
- サイドバー: width:216px、flex:none、`background: var(--pr-bg-pane)`(#F7F6F2)、`border-right: 1px solid var(--pr-border-pane)`(#E7E4DB)、padding:12px 10px、flex-direction:column / gap:2px、font-size:12.5px、`color: var(--pr-text-nav)`(#3A3E44)。
- メインカラム: flex:1、min-width:0、padding:16px 22px、flex-direction:column / gap:12px、overflow:hidden。
- 検索ドロップダウン: position:absolute、top:48px、left:230px、width:560px、`z-index: var(--z-dropdown)`(6)。検索ボックス直下に重なる(1440px 超のビューポートでもロゴブロック 198px+gap 14px+18px padding=左端 230px は固定なので、left は px 固定で一致する)。
- 一括操作バー: `position: fixed`、bottom:22px、left:50%、transform:translateX(-50%)、`z-index: var(--z-floating-bar)`(5)。視覚的にはテーブル下部中央に浮遊(1e の絶対配置と同結果)。

### 4.2 トップバー(h52)

1. ロゴブロック(width:198px、flex、gap:8px):
   - ロゴマーク: 22×22px、border-radius:6px、`background: var(--pr-acc)`、色 #FFFFFF、font-size:11.5px、font-weight:700、中央配置。文言「訳」。
   - ワードマーク: 「訳読」font-size:14.5px、font-weight:700、letter-spacing:0.5px。
2. 検索ボックス(`SearchBox variant="global"`。フォーカス状態の実測):
   - width:460px、height:32px、`background: var(--pr-bg-card)`、`border: 1.5px solid var(--pr-acc)`、border-radius:7px、padding:0 12px、font-size:12.5px、`color: var(--pr-text)`、`box-shadow: 0 0 0 3px var(--pr-acc-s)`(フォーカスリング)。flex / align-items:center / gap:8px。
   - 虫眼鏡 `MagnifierIcon` 12×12、`color: var(--pr-text-icon)`(#8A8E94)。円(cx5,cy5,r3.6、stroke-width:1.3)+柄(M8 8→l2.6 2.6、stroke-linecap:round)。
   - 入力値例「EMA teacher」。**決定: デザインの擬似カーソル(width:1px / height:14px 縦棒、#3E5C76、opacity:0.7)は実装せず、実 `<input>` の `caret-color: var(--pr-acc)` で表現する**。理由: 実入力要素のネイティブキャレットが同視覚を提供し、擬似要素は IME と競合するため。
   - 右端(margin-left:auto)ヒント: 「esc で閉じる」font-size:10px、`color: var(--pr-text-muted)`(#9A9EA4)。フォーカス時のみ表示。非フォーカス時は plans/08 §5.13 の inset 面+プレースホルダ「ライブラリ全体を検索 — 本文・訳文・メモ・チャット」+キーキャップ「⌘K」。
3. 通知ボタン ◷(4a 仕様。未読時に琥珀ドット #C49432 6px。詳細は 4a 計画書)。
4. スペーサ(flex:1)。※通知ボタンはスペーサの後・アバターの前。
5. ユーザーアバター: 30×30px、border-radius:50%、`background: var(--pr-acc-s)`、`color: var(--pr-acc)`、font-size:11px、font-weight:700。文言=`display_name` のイニシャル 2 文字(例「YK」。導出規則は 4a 計画書の決定に従う: 空白区切り先頭 2 語の頭文字を大文字で、1 語なら先頭 2 文字。`avatar_url` 非 null なら円形 `<img>` に置換)。

### 4.3 検索ドロップダウン(オーバーレイ、開いた状態)

- コンテナ: `Popover`(width=560、caret=false、placement="bottom-start")。面: `background: var(--pr-bg-pop)`(#FFFFFF)、`border: 1px solid var(--pr-border-pop)`(#DDD9CF)、border-radius:10px、`box-shadow: var(--pr-shadow-pop)`(0 24px 56px rgba(28,30,34,0.18))、overflow:hidden。position:absolute top:48px / left:230px / `z-index: var(--z-dropdown)`。
- ヘッダ行: padding:10px 14px、`border-bottom: 1px solid var(--pr-border-hair)`(#F0EDE4)、flex / gap:8px、font-size:11px、`color: var(--pr-text-sub2)`(#777B81)。
  - 左: 「「EMA teacher」の結果 12 件」— クエリ部分は `<b>`(font-weight:700、`color: var(--pr-text)`)。テンプレート: `「{q}」の結果 {total} 件`。
  - 右(margin-left:auto): 「本文・訳文・メモ・チャット・記事を横断」(固定文言)。
- 結果リスト: padding:8px 6px、縦 flex。各結果アイテム: padding:8px 10px、border-radius:7px、縦 flex / gap:4px。
  - **アクティブ行(キーボード選択中 / ホバー)**: `background: var(--pr-bg-hover)`(#FAF9F5)。
  - 1 行目(flex / gap:7px):
    - 種別バッジ = `SourceBadge`: height:16px、padding:0 6px、border-radius:3px、font-size:9.5px、font-weight:700。**決定(ラベル・色の写像)**: `source==='body'`→「本文でヒット」(bg `--pr-src-body-bg`/fg `--pr-src-body-fg` = アクセント)/ `source==='note' | 'annotation'`→「あなたのメモ」(bg `--pr-src-note-bg` rgba(101,148,113,0.16) / fg #4C7458)/ `source==='chat'`→「チャット履歴」(bg `--pr-src-chat-bg` rgba(110,90,126,0.14) / fg #6E5A7E)/ `source==='article'`→「記事でヒット」(bg `--pr-src-article-bg` #F1EFE9 / fg #777B81)。理由: 1e に描かれた 3 種の逐語+4e の記事バッジ色を流用し、annotation はファセット定義(docs/06: notes=メモ・注釈)に従い「あなたのメモ」に合流させる。
    - タイトル: `hit.library_item.title`。font-size:11.5px / font-weight:600。続けてメタ(`color: var(--pr-text-muted)` / font-weight:400): 「· §3.2 · 読んだ」「· メモ · 6/20」「· メインスレッド · 6/28」= `· {display} · {meta}`。**決定: `SearchHit_with_paper` にサーバー導出の補足表記 `meta: string | null`(body=論文ステータスの日本語ラベル、note/chat/article=M/D 日付)を追加する(plans/03 §15.2 への追記事項)**。理由: 1e 逐語の「· 読んだ」「· 6/20」は現行スキーマの `display` だけでは再現できない。`meta` が null の間は「· {display}」のみ表示。
  - 2 行目(スニペット): font-size:11.5px、line-height:1.7、`color: var(--pr-text-mid)`(#3C4046)。フォント(決定。4e と同規則): `source==='body' && snippet_lang==='en'` → `var(--pr-font-en)`('Source Serif 4',Georgia,serif)/ `source==='body' && snippet_lang==='ja'` → `var(--pr-jp)` / その他 → `var(--pr-font-ui)`。
    - 検索語ハイライトは API の `snippet` に含まれる `<mark class="hit">` をそのまま描画(サニタイズ済み HTML。plans/03 §15.1)。mark スタイル(`.yk-search-hit`、plans/08 §5.17 決定): `background: rgba(196,148,50,0.30)`、`color: var(--pr-text)`、border-radius:2px、padding:0 1px。
    - 描画例(逐語): 「…the target network is an EMA teacher updated by θ⁻ ← μθ⁻ + (1−μ)θ, which stabilizes online distillation…」/「EMA teacher の減衰 0.999 が安定。オンライン蒸留では student と同時更新に注意…」/「Q: EMA teacher を使うオンライン蒸留と offline 蒸留の違いは?…」
  - 3 行目(ジャンプ行): font-size:10px、`color: var(--pr-acc)`、font-weight:600。**アクティブ行のみ表示**(1e では結果 1=ハイライト行だけが持つ)。文言は `target.kind` で切替(決定。plans/03 §15.1 の遷移先ラベルと一致): `viewer`→「該当位置へジャンプ →」/ `note`→「メモを開く →」/ `chat`→「スレッドを開く →」/ `article`→「記事モードで開く →」。
- フッタ: padding:9px 14px、`border-top: 1px solid var(--pr-border-hair)`、font-size:11px、`color: var(--pr-acc)`、font-weight:600。文言「すべての結果を表示({total} 件)→」。クリック/Enter(アクティブ行なし時)で `/search?q={q}` へ遷移(4e)。

1e 描画時の 3 結果(参照データ。VRT フィクスチャに使用):

| # | バッジ | タイトル+メタ | スニペット(フォント) |
|---|---|---|---|
| 1(アクティブ) | 本文でヒット | Consistency Models · §3.2 · 読んだ | 「…the target network is an EMA teacher updated by θ⁻ ← μθ⁻ + (1−μ)θ, which stabilizes online distillation…」(Source Serif 4)+「該当位置へジャンプ →」 |
| 2 | あなたのメモ | Progressive Distillation · メモ · 6/20 | 「EMA teacher の減衰 0.999 が安定。オンライン蒸留では student と同時更新に注意…」(UI) |
| 3 | チャット履歴 | Consistency Models · メインスレッド · 6/28 | 「Q: EMA teacher を使うオンライン蒸留と offline 蒸留の違いは?…」(UI) |

### 4.4 サイドバー(w216。`SidebarNav`)

項目共通: padding:7px 10px(コレクション/保存フィルタ項目は 6px 10px)、border-radius:6px、font-size:12.5px。

1. 「ホーム」(通常項目。href=`/`)。
2. 「ライブラリ」(選択中): `background: var(--pr-acc-s)`、`color: var(--pr-acc)`、font-weight:600。flex / gap:8px。ラベル flex:1 + 右端件数「41」(CountBadge nav、font-size:10.5px。アクティブ項目内では色継承=アクセント)。href=`/library`。
3. 「語彙帳」+件数「46」(font-size:10.5px / `color: var(--pr-text-muted)`)。href=`/vocabulary`。
4. セクション見出し「コレクション」: font-size:10.5px、font-weight:600、`color: var(--pr-text-muted)`、letter-spacing:0.4px、padding:14px 10px 4px。
5. 「輪読会 2026-07」+件数「5」/「Diffusion 蒸留」+件数「8」(件数 10.5px / muted)。href=`/collections/{id}`。締切のあるコレクションは DeadlineBadge chip(7/16)を件数の左に併記(4a 仕様)。
6. セクション見出し「保存フィルタ」(同スタイル)。
7. 「締切あり」+件数「3」/「cs.CV の未読」+件数「7」。href=`/library?filter_id={sf_id}`(view は省略=既定 card。テーブルで使う場合はビュー切替で移る)。
8. フッタ「設定 · エクスポート」(plans/08 §5.14。1e では省略描画)。

データ写像: ライブラリ件数=`facets.quick.all`、語彙帳=`GET /api/vocab?limit=1` の `counts.all`、コレクション=`GET /api/collections` の `items[].{name,item_count,deadline}`、保存フィルタ=`GET /api/saved-filters` の `items[].{name,count}`。

### 4.5 メイン見出し行(`LibraryToolbar`。flex / align-items:center / gap:12px)

- ページタイトル「ライブラリ」: font-size:16px、font-weight:700。
- 件数「41 本」: font-size:11.5px、`color: var(--pr-text-muted)`。値=`facets.quick.all`(quick フィルタ適用中でも総数を出す。1e の「41 本」とピル「すべて 41」が一致しているため)。
- ビュー切替 `SegmentedControl`(size="sm"、margin-left:6px): トラック `background: var(--pr-bg-muted)`(#EFEDE6)、border-radius:7px、padding:2px、gap:2px。
  - 「カード」(非選択): height:22px、padding:0 10px、border-radius:5px、font-size:11px、`color: var(--pr-text-sub)`(#5B6067)。
  - 「テーブル」(選択中): 同寸法、`background: var(--pr-bg-seg-selected)`(#FFFFFF)、font-weight:600、`box-shadow: var(--pr-shadow-seg)`。
- スペーサ(flex:1)。
- ソート表示「並び: 更新日 ▾」: font-size:11.5px、`color: var(--pr-text-sub)`。「▾」は font-size:9px / `color: var(--pr-text-muted)`。クリックで SortMenu(§5.6)。ラベルは現在の `sort.key` の日本語名(§5.6 の表)。
- ボタン「この条件を保存」: height:26px、padding:0 10px、`border: 1px solid var(--pr-border-control)`(#DDD9CF)、border-radius:6px、font-size:11px、`color: var(--pr-text-mid)`(#3C4046)、`background: var(--pr-bg-control)`(#FFFFFF)。テーブルビューのみ表示(docs/06 §8.1)。

### 4.6 クイックフィルタ行(`QuickFilterRow`。flex / align-items:center / gap:6px)

ステータスピル(`FilterChip`、border-radius:999px、height:22px、padding:0 10px、font-size:11px):

- 「すべて 41」(選択中): `background: var(--pr-elev-bg)`(#26292E)、color:#FFFFFF、font-weight:600、枠なし。
- 「未読 12」「途中 4」「読了 23」「要再確認 2」(非選択): `border: 1px solid var(--pr-border-control)`、`color: var(--pr-text-mid)`、`background: var(--pr-bg-control)`。
- ラベル⇄値の写像(plans/03 §1.6 `Quick`): すべて=`all` / 未読=`unread` / 途中=`in_progress` / 読了=`done` / 要再確認=`recheck`。件数=`facets.quick.*`。

区切り縦線: width:1px、height:16px、`background: #E2DFD5`(=`var(--pr-border-card)`)、margin:0 4px。

ドロップダウンフィルタ(`AttributeFilterDropdown` のトリガーボタン。border-radius:6px、height:22px、padding:0 10px、font-size:11px、gap:4px):

- 未適用: 「ステータス ▾」「タグ ▾」「コレクション ▾」「品質 ▾」「年 ▾」— `border: 1px solid var(--pr-border-control)`、`color: var(--pr-text-mid)`、`background: var(--pr-bg-control)`。「▾」は font-size:8.5px / `color: var(--pr-text-muted)`。
- 適用中(`FilterChip removable`): 「タグ: distillation ×」— `border: 1px solid var(--pr-acc-m)`、`color: var(--pr-acc)`、`background: var(--pr-acc-s)`、font-weight:600。末尾「×」(解除)。
- **決定(適用中の置換規則)**: 各属性は未適用時ドロップダウンボタン、適用時は同位置がチップに置き換わる(1e ではタグのみ適用中でボタン「タグ ▾」が消えチップになっている)。値 1 個=「{属性}: {値} ×」、複数=「{属性}: {先頭値} +{n-1} ×」。チップ本体クリックでドロップダウン再オープン、「×」で当該属性の全値解除。
- **決定(filter_id 適用中)**: 保存フィルタ適用中は条件を URL に展開せず、チップ「フィルタ: {名前} ×」を属性ドロップダウン群の左(縦線の直後)に表示する。×で `filter_id` を外す。保存フィルタ+明示クエリの併用は plans/03 §5.1 どおり(明示側が上書き)。

### 4.7 テーブル(`LibraryTable`)

コンテナ: `background: var(--pr-bg-card)`、`border: 1px solid var(--pr-border-card)`(#E2DFD5)、border-radius:10px、overflow:hidden、flex:1、縦 flex。行領域は `overflow-y: auto`(内部スクロール)。最下段に flex:1 の空スペーサで余白吸収(行数が少ないとき)。

グリッド(ヘッダ・全行共通):

```css
grid-template-columns: 34px 1fr 108px 44px 168px 64px 66px 76px 64px 64px;
align-items: center; gap: 8px; padding: 8px 14px;
```

列: [チェックボックス | 論文 | ステータス | 品質 | タグ | 優先度 | 締切 | 読書時間 | 理解度 | 追加日]

**ヘッダ行**: `border-bottom: 1px solid var(--pr-border-soft)`(#ECE9DF)、font-size:10.5px、font-weight:600、`color: var(--pr-text-muted)`。

- 全選択チェックボックス(未チェック): 14×14px、`border: 1.5px solid var(--pr-border-check)`(#C9C5BA)、border-radius:3px。
- 列ラベル: 「論文 ↑」(ソート昇順インジケータ付き)「ステータス」「品質」「タグ」「優先度」「締切」「読書時間」「理解度」「追加日」。ソート中の列のみ「↑」(asc)/「↓」(desc)をラベル末尾に付ける。

**データ行共通**: `border-bottom: 1px solid var(--pr-border-row)`(#F4F1E9。最終行は border なし)。選択中行 `background: var(--pr-acc-s)`。ホバー行 `background: var(--pr-bg-hover)`(plans/08 §5.15 決定)。

- チェックボックス チェック済: 14×14px、`background: var(--pr-acc)`、border-radius:3px、白「✓」font-size:9px、中央配置。未チェック: 14×14px、`border: 1.5px solid var(--pr-border-check)`、border-radius:3px。
- 論文セル: flex / gap:10px / min-width:0。
  - サムネイル: 26×34px、border-radius:3px、`background: var(--pr-bg-thumb)`(#EFEDE6)、`border: 1px solid var(--pr-border-thumb)`(#E0DDD3)、flex:none。`thumbnail_url` があれば `object-fit: cover` の `<img>`、なければプレースホルダ面のまま。
  - タイトル: font-size:12px、font-weight:600、white-space:nowrap / overflow:hidden / text-overflow:ellipsis(1 行省略)。
  - 著者・出典行: font-size:10px、`color: var(--pr-text-muted)`。
  - `titleBadge='pdf_import'` の行はタイトル末尾にインラインバッジ「PDF 取り込み」: height:14px、padding:0 5px、border-radius:3px、`background: var(--pr-bg-inset)`(#F1EFE9)、`color: var(--pr-text-icon)`(#8A8E94)、font-size:9px、font-weight:600、vertical-align:1px。
- ステータスセル(`StatusPill variant="dot-label"`): flex / gap:5px、font-size:11px。ドット 7×7px 円形+ラベル。ドット色: 読んでいる=`var(--pr-acc)` / 読んだ=#659471 / すぐ読む=#C49432 / あとで再読=#8E7AA6 / 読む予定=#9AA0A6 / 保留=#B0ACA2(`STATUS_COLORS`)。
- 品質セル(`QualityBadge size=17`): 17×17px バッジ、border-radius:4px、font-size:10px、font-weight:700。A=`background: var(--pr-acc-s)` / `color: var(--pr-acc)`。B=`background: var(--pr-bg-inset)`(#F1EFE9)/ `color: var(--pr-text-sub2)`(#777B81)。
- タグセル: flex / gap:4px / overflow:hidden(全行に統一適用。抽出注記の「行 5 のみ overflow 指定なし」はデザイン HTML の揺れであり、はみ出し防止のため全行に付ける。決定)。`TagChip`: height:17px、padding:0 6px、border-radius:3px、`background: var(--pr-bg-inset)`、`color: var(--pr-text-sub)`(#5B6067)、font-size:10px。
- 優先度セル(`PriorityBadge`): font-size:11px。高=`color: var(--pr-warn)`(#A05A42)/ font-weight:600。中=`color: var(--pr-text-sub2)`(#777B81)。低=`color: var(--pr-text-muted)`(#9A9EA4)。null=「—」(muted)。
- 締切セル(`DeadlineBadge variant="text"`): font-size:11px。値あり(7/16)=`color: var(--pr-warn)` / font-weight:600。なし「—」=muted。
- 読書時間セル: font-size:11px。値あり=`color: var(--pr-text-mid)`(#3C4046)。「—」=muted。
- 理解度セル: font-size:11px。値あり(`n/5`)=`color: var(--pr-text-mid)`。「—」=muted。
- 追加日セル: font-size:11px、`color: var(--pr-text-sub2)`(#777B81)。

**1e 描画の 7 行(VRT フィクスチャ。上から)**:

1. 【選択中・背景 `--pr-acc-s`・✓チェック】Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow / Liu, Gong, Liu · ICLR 2023 · arXiv:2209.03003 / 読んでいる(`--pr-acc` ドット)/ A / diffusion, flow / 高 / — / 3.2h / — / 7/02
2. Consistency Models / Song, Dhariwal, Chen, Sutskever · ICML 2023 / 読んだ(#659471)/ A / distillation / 中 / — / 5.1h / 4/5 / 6/28
3. 【選択中・背景 `--pr-acc-s`・✓チェック】Adversarial Diffusion Distillation / Sauer, Lorenz, Blattmann, Rombach · 2024 / すぐ読む(#C49432)/ A / distillation, GAN / 高 / 7/16(警告色太字)/ — / — / 7/01
4. Progressive Distillation for Fast Sampling of Diffusion Models / Salimans, Ho · ICLR 2022 / 読んだ / A / distillation / 中 / — / 2.4h / 5/5 / 6/20
5. On Distillation of Guided Diffusion Models / Meng et al. · CVPR 2023 / あとで再読(#8E7AA6)/ A / distillation, CFG / 低 / — / 1.8h / 3/5 / 6/15
6. BOOT: Data-free Distillation of Denoising Diffusion Models(タイトル末尾に「PDF 取り込み」バッジ)/ Gu et al. · 2023 · アップロード / 読む予定(#9AA0A6)/ B(グレー品質バッジ)/ distillation / 中 / — / — / — / 6/12
7. Score-Based Generative Modeling through Stochastic Differential Equations / Song et al. · ICLR 2021 / 保留(#B0ACA2)/ A / score / 低 / — / 0.6h / — / 5/30

(行は計 7 行。1 と 3 が選択中の 2 行。ヘッダの全選択は未チェック)

### 4.8 一括操作バー(`BulkActionBar`。フローティング)

- コンテナ: `position: fixed`、bottom:22px、left:50%、transform:translateX(-50%)、flex / align-items:center / gap:14px、`background: var(--pr-elev-bg)`(#26292E)、`color: var(--pr-elev-fg)`(#E8E6E1)、border-radius:10px、padding:10px 18px、`box-shadow: var(--pr-shadow-bar)`(0 16px 40px rgba(20,22,26,0.35))、`z-index: var(--z-floating-bar)`。両テーマ共通の常時ダーク UI(plans/08 §8.3)。
- 「2 件を選択中」: font-size:12px、font-weight:600。テンプレート `{n} 件を選択中`。
- 区切り縦線: width:1px、height:16px、`background: var(--pr-elev-divider)`(#4A4E55)。
- アクション(各 font-size:12px、`color: var(--pr-elev-fg)`): 「ステータス変更 ▾」(▾は font-size:9px / `color: var(--pr-elev-fg-muted)` #9BA1A9)、「タグ追加」、「コレクションへ」。
- 「選択解除 ×」: font-size:12px、`color: var(--pr-elev-fg-muted)`。

### 4.9 全 UI 文言(逐語)

- トップバー: 「訳」「訳読」「esc で閉じる」+プレースホルダ「ライブラリ全体を検索 — 本文・訳文・メモ・チャット」+「⌘K」。
- 検索ドロップダウン: 「「{q}」の結果 {n} 件」「本文・訳文・メモ・チャット・記事を横断」「本文でヒット」「あなたのメモ」「チャット履歴」「記事でヒット」「該当位置へジャンプ →」「メモを開く →」「スレッドを開く →」「記事モードで開く →」「すべての結果を表示({n} 件)→」。
- サイドバー: 「ホーム」「ライブラリ」「語彙帳」「コレクション」「保存フィルタ」「+ 新規コレクション」「設定 · エクスポート」。
- 見出し行: 「ライブラリ」「{n} 本」「カード」「テーブル」「並び: {ソート名} ▾」「この条件を保存」。
- クイックフィルタ行: 「すべて {n}」「未読 {n}」「途中 {n}」「読了 {n}」「要再確認 {n}」「ステータス ▾」「タグ ▾」「コレクション ▾」「品質 ▾」「年 ▾」「{属性}: {値} ×」。
- テーブルヘッダ: 「論文」「ステータス」「品質」「タグ」「優先度」「締切」「読書時間」「理解度」「追加日」(+ソート中列に「 ↑」/「 ↓」)。
- 一括操作バー: 「{n} 件を選択中」「ステータス変更 ▾」「タグ追加」「コレクションへ」「選択解除 ×」。
- 未設定値: 「—」。PDF 由来バッジ: 「PDF 取り込み」。

## 5. 状態とインタラクション

### 5.1 状態一覧(デザイン既描画)

| 状態 | 内容(§4 の該当節) |
|---|---|
| 検索アクティブ | フォーカスリング(1.5px `--pr-acc` + 0 0 0 3px `--pr-acc-s`)+入力値+「esc で閉じる」(§4.2) |
| 検索ドロップダウン開 | 上位 3 件+アクティブ行 bg #FAF9F5+種別バッジ 3 色+mark ハイライト+フッタ(§4.3) |
| ビュー切替 | 「テーブル」選択中(白+shadow)、「カード」非選択(§4.5) |
| クイックフィルタ選択 | 「すべて 41」黒地白字。他は白枠(§4.6) |
| 属性フィルタ適用中 | 「タグ: distillation ×」アクセントチップ(§4.6) |
| 行選択 | 行 1・3 チェック済(✓+行 bg `--pr-acc-s`)。ヘッダ全選択は未チェック(§4.7) |
| 一括操作バー表示 | 選択 ≥1 件で下部中央に出現(§4.8) |
| 列ソート | 「論文 ↑」昇順インジケータ。ツールバー表示と併存(§5.6) |
| 品質バッジ 2 種 / ステータスドット 6 種 / PDF 取り込みバッジ | §4.7 |
| サイドバー選択 | 「ライブラリ」アクティブ(§4.4) |

### 5.2 デザイン未描画の必須状態(すべて決定)

- **テーブル初回ローディング**: `LibraryTableSkeleton`。ヘッダ行は実描画し、データ行の代わりにスケルトン行 ×7(同グリッド・同 padding)。各行: チェックボックス列=空、論文セル=サムネ形状 26×34px(`background: var(--pr-bg-muted)`、radius 3px)+バー 2 本(width 60% × height 10px / width 40% × height 8px、radius 3px、`background: var(--pr-bg-muted)`)、他列=width 70% × height 10px のバー 1 本。全体に `animation: yk-pulse 1.2s ease-in-out infinite`(opacity 1→0.55→1)。
- **追加ページ取得中**(cursor): テーブル最下行の下に height:36px の行、中央に「読み込み中…」font-size:11px / `color: var(--pr-text-muted)`。
- **空状態(フィルタ起因)**: `EmptyState` — title「条件に一致する論文がありません」/ description「フィルタを解除するか、検索語を変えてください」/ action「フィルタをすべて解除」(クリックで quick=all+全属性フィルタ・filter_id 解除)。
- **空状態(ライブラリ 0 件)**: `EmptyState` — title「まだ論文がありません」/ description「ブラウザ拡張で arXiv ページを開き「保存」すると、ここに並びます」/ action なし。判定: フィルタ未適用かつ `facets.quick.all === 0`。
- **一覧エラー**: テーブル領域中央に `EmptyState` — title「読み込みに失敗しました」/ description=Problem の `title`(例「サービスを一時的に利用できません」。**決定: レスポンスが Problem 形式でない場合(ネットワーク断等)は固定文言「通信に失敗しました」**)/ action「再試行」(`refetch()`)。
- **検索プレビュー ローディング**: ドロップダウンは開いたまま、結果リスト位置に 1 行「検索中…」(padding:8px 10px、font-size:11.5px、`color: var(--pr-text-muted)`)。ヘッダは前回結果の件数を保持(`keepPreviousData`)。初回はヘッダ左を「「{q}」を検索中…」にする。
- **検索プレビュー 0 件**: ヘッダ「「{q}」の結果 0 件」+リスト位置に「一致する結果はありません」(同スタイル)。フッタ非表示。
- **検索プレビュー エラー**: リスト位置に「検索に失敗しました — 再試行」(「再試行」は `color: var(--pr-acc)` / weight 600、クリックで refetch)。
- **ホバー**: 行=bg `--pr-bg-hover`+`cursor: pointer`。白地ボタン/ピル/ドロップダウントリガー=bg `--pr-bg-hover`。一括バーのアクション=`color: #FFFFFF` へ。ドロップダウン内リスト行=bg `--pr-bg-hover`。transition なし(デザインに存在しないため付けない)。
- **フォーカス(キーボード)**: 全インタラクティブ要素に `focus-visible: outline 1.5px solid var(--pr-acc); outline-offset: 1px`(plans/08 §5 共通)。
- **一括操作の実行中**: バー内アクションを `opacity: 0.5; pointer-events: none` にし、完了で復帰。

### 5.3 グローバル検索のインタラクション

1. 起動: 検索ボックスクリック、または `⌘K` / `Ctrl+K`(`LibraryShell` の `keydown` リスナー。`event.preventDefault()`)。フォーカスでアクティブ様式へ。
2. 入力: 200ms デバウンス後、**前後空白トリム後 1 文字以上**で `GET /api/search/preview?q={トリム後の値}` を発行しドロップダウンを開く(決定: 空白のみの入力は空扱い)。入力が空になったら閉じる(クエリは発行しない)。入力は `maxLength=200`(plans/03 §15.1 の `q` 上限 200 字に合わせる。決定)。
3. キーボード: `↓`/`↑` でアクティブ行を 0→1→2→(フッタ)→0 と循環移動(フッタも選択対象。アクティブ時は背景 `--pr-bg-hover`)。**決定: ドロップダウンを開いた直後・結果が更新された直後は先頭(index 0)をアクティブにする**(1e の描画=結果 1 アクティブと一致。結果 0 件時は `activeIndex=-1`)。`Enter`=アクティブ行の遷移(行なし時はフッタと同じ全結果画面へ)。`Esc`=ドロップダウンを閉じて入力を blur(入力値は保持)。外側クリックでも閉じる。
4. 遷移規則(`target.kind`。決定。ビューア側計画書(1a/1b/1c/1h)・4e はこの形式に従う — §7 項 3):
   - `viewer` → `/papers/{library_item_id}?block={anchor.block_id}`(ビューアが該当ブロックへスクロール+一時ハイライト)
   - `note` → `/papers/{library_item_id}?panel=notes&note={note_id}`
   - `chat` → `/papers/{library_item_id}?panel=chat&thread={thread_id}&message={message_id}`
   - `article` → `/papers/{library_item_id}?mode=article&block={article_block_id}`
5. フッタ / 全結果: `/search?q={encodeURIComponent(q)}`(画面 4e)へ `router.push`。
6. マウスホバーでもアクティブ行が移動する(キーボードと同一状態を共有)。

### 5.4 フィルタ・ソートのインタラクション

- クイックフィルタピル: クリックで `quick` を排他切替(再クリックでの解除はしない。「すべて」が解除に相当)。切替時に選択(§5.5)を全解除する(決定。フィルタで不可視になった行の巻き添え操作を防ぐ)。
- 属性ドロップダウン: トリガークリックで `Popover`(width=220、placement="bottom-start")。パネル内: 行 height:28px、padding:0 12px、font-size:11.5px、`color: var(--pr-text-mid)`、左にチェックボックス 14×14px(single モードは 7×7px ドット表示)、右端に件数(font-size:10.5px、muted)。選択肢と件数は `facets` から(ステータス=6 値+`STATUS_LABELS`、タグ=上位 100、コレクション、品質 A/B、年=降順)。チェック切替は即時適用(URL 更新→list/facets refetch)。multi=status/tag/year(API OR)、single=collection/quality。**決定: single モードで選択済みの行を再クリックした場合は選択解除(未適用に戻す)**。
- 適用中チップの「×」: 当該属性の値をすべて外す。
- ソートメニュー(`SortMenu`): 「並び: {名} ▾」クリックで `Popover`(width=180、bottom-end)。選択肢と既定方向(決定):

| 表示名 | sort | 既定 order |
|---|---|---|
| 更新日 | `updated_at` | desc |
| 追加日 | `added_at` | desc |
| タイトル | `title` | asc |
| 締切 | `deadline` | asc |
| 読書時間 | `reading_time` | desc |
| 理解度 | `comprehension` | desc |
| 優先度 | `priority` | desc |

- 列ヘッダソート(決定): ソート可能列は API 対応列のみ — 論文=`title`、優先度=`priority`、締切=`deadline`、読書時間=`reading_time`、理解度=`comprehension`、追加日=`added_at`。ステータス・品質・タグ列はソート不可(カーソル default)。同一列の再クリックで asc⇄desc 反転、別列クリックで上表の既定方向。列ソートとツールバー表示は同一状態(`sort`/`order`)を共有し、「並び: タイトル ▾」のように連動する(1e の「並び: 更新日」+「論文 ↑」併存はソートキーが `updated_at` の描画時点を示すが、実装では単一状態とする。決定。理由: 2 系統のソートが並存すると結果順の解釈が非決定になる)。
- 「この条件を保存」: クリックで `Popover`(width=260、bottom-end)。内容: ラベル「フィルタ名」(font-size:10.5px/600、muted)+テキスト入力(height:28px、border 1px `--pr-border-control`、radius 6px、font-size:12px、padding 0 10px)+右寄せボタン「保存」(height:26px、padding 0 12px、`background: var(--pr-acc)`、白字、radius 6px、font-size:11px/600)。空名は保存不可(決定: 前後空白トリム後 0 文字なら「保存」を disabled opacity 0.5。送信値もトリム後の文字列)。実行: `POST /api/saved-filters { name, conditions: 現在の quick+属性フィルタ, sort: {key, order} }` → 成功で Popover を閉じ、Toast `{ kind: 'success', message: '保存フィルタ「{name}」を作成しました' }`+`shellKeys.savedFilters` 無効化。フィルタもソートも既定のままなら本ボタンは disabled(`canSaveFilter=false`)。

### 5.5 行選択と一括操作

- 選択状態は `LibraryTablePage` の `useState<Set<string>>`(ページローカル。ビュー切替・フィルタ変更・アンマウントで消える。**決定: ソート変更・追加ページ取得(cursor)では選択を維持する** — 行集合が変わらないため)。
- 行チェックボックス: クリックでトグル(`stopPropagation` で行クリックと分離。ヒット領域は 34px 列全体)。`Shift+クリック` で直前クリック行との範囲選択(決定。**意味論: アンカー=最後にチェックボックス操作した行。アンカーから当該行までの現在の表示順の全行を「選択」状態にする(トグルではなく追加。既選択行は選択のまま)。アンカーが未定(選択後にフィルタ変更等)なら通常トグル**)。
- ヘッダ全選択: 未選択→取得済み全行を選択 / 一部選択→全選択 / 全選択→全解除。一部選択の表現(決定): `background: var(--pr-acc)`+白「−」(font-size:9px)。
- 行クリック(チェックボックス以外): `/papers/{library_item_id}` へ遷移(リーダーを開く)。
- 選択 ≥1 件で `BulkActionBar` を表示、0 件で非表示。表示・非表示は即時(アニメーションなし。決定)。
- 「ステータス変更 ▾」: バー上方に `BulkStatusMenu` — `position: absolute; bottom: calc(100% + 8px); left: 0` のパネル(決定: 基準は各トリガーボタンの `position: relative` ラッパー。3 メニューとも自ボタンの左端に揃う)(width:180px、`background: var(--pr-bg-pop)`、`border: 1px solid var(--pr-border-pop)`、radius 10px、`box-shadow: var(--pr-shadow-pop)`。決定: Popover コンポーネントは bottom 系配置のみのため、バー内ローカル配置で同面スタイルを再現する)。6 行(各 height:30px、padding 0 12px、font-size:11.5px、ドット 7×7px+`STATUS_LABELS`)。選択で `POST /api/library-items/bulk { ids, op: "set_status", status }`。
- 「タグ追加」: 同配置の `BulkTagPopover`(width:240px)。タグ入力(height:28px)+`GET /api/tags?q={入力値}&limit=8` の補完リスト(決定: リクエストで `limit=8` を指定)+Enter でチップ化(複数可。**決定: 補完に無い文字列も Enter で新規タグとしてチップ化できる**(plans/03 §5.6 の `add_tags` は任意文字列を受ける)。トリム後空文字と重複はチップ化しない)+「追加」ボタン(チップ 0 個時は disabled。決定)→ `op: "add_tags"`。
- 「コレクションへ」: 同配置の `BulkCollectionPopover`(width:240px)。`GET /api/collections` の一覧(名前+件数)から 1 つ選択 → `op: "add_to_collection"`。
- 実行成功: Toast `{ kind: 'success', message: '{updated} 件を更新しました' }`+`['library']` 無効化。**選択は維持する**(決定。「タグ追加→コレクションへ」の連続操作のため)。失敗: Toast `{ kind: 'error', message: Problem.title }`、選択維持。
- 「選択解除 ×」: 選択を全解除しバーを閉じる。`Esc` でも同じ(検索ドロップダウンが開いていない時のみ。決定: Esc の優先順位は 開いているメニュー > 検索ドロップダウン > 選択解除)。

### 5.6 ページング・スクロール

- テーブル行領域の末尾に `IntersectionObserver` の番兵を置き、可視化で `fetchNextPage()`(limit=50)。`next_cursor: null` で停止。
- フィルタ・ソート変更時はクエリキーが変わるため自動で先頭ページから再取得。スクロール位置はトップへ戻す(決定)。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Storybook + Playwright VRT(plans/08 §9)。フィクスチャは §4.7 の 7 行+§4.3 の 3 結果を固定データで再現する。

- [ ] **VRT-1e-A(基準状態)**: `/library?view=table&tag=distillation`、検索非フォーカス、選択なし。テーブル 10 列グリッド `34px 1fr 108px 44px 168px 64px 66px 76px 64px 64px`・行 padding 8px 14px・全色トークンが §4.7 と一致。
- [ ] **VRT-1e-B(1e 完全再現)**: 検索ボックスに「EMA teacher」入力+ドロップダウン開(結果 1 アクティブ)+行 1・3 選択+一括操作バー「2 件を選択中」+チップ「タグ: distillation ×」+ピル「すべて 41」選択。extract/1e.md の描画と一致(通知ボタン ◷ を除く。§1 の決定によりシェルは 4a 完全形)。
- [ ] 検索ドロップダウン: width 560px / top 48px / left 230px / z-index 6 / shadow `0 24px 56px rgba(28,30,34,0.18)`。種別バッジ 3 色(アクセント/緑 rgba(101,148,113,0.16)+#4C7458/紫 rgba(110,90,126,0.14)+#6E5A7E)、mark=rgba(196,148,50,0.30)。
- [ ] 一括操作バー: #26292E 地 / radius 10px / padding 10px 18px / shadow `0 16px 40px rgba(20,22,26,0.35)` / 縦線 #4A4E55 / bottom 22px 中央。ダークテーマでも同一色(常時ダーク UI)。
- [ ] ステータスドット 6 色・品質 A/B・優先度 3 色・締切警告色 #A05A42・「PDF 取り込み」バッジが §4.7 の値と一致。
- [ ] ダークモード・アクセント 4 色切替でトークン参照面(テーブル・サイドバー・ドロップダウン)が破綻しない(VRT 8 バリアント)。
- [ ] ホバー行 bg #FAF9F5、選択行 bg `--pr-acc-s`、スケルトン(§5.2 形状)の各ストーリーが存在する。

### 6.2 機能検証チェックリスト

- [ ] `/library?view=table` で `GET /api/library-items` と `GET /api/library-items/facets` が正しいクエリ(§5.1 語彙)で発行され、件数「41 本」=`facets.quick.all`、ピル件数が facets と一致する。
- [ ] クイックフィルタ・属性フィルタ・ソートの全操作が URL クエリに反映され、URL 直開きで同一状態が復元される(リロード耐性)。
- [ ] フィルタ結合が API 仕様どおり(同一属性 OR・異属性 AND)。適用中チップの「×」で当該属性のみ解除される。
- [ ] `⌘K` / `Ctrl+K` でフォーカス、1 文字以上+200ms で `GET /api/search/preview` が 1 回だけ発行される(デバウンス検証)。連続入力中に古いレスポンスが新しい表示を上書きしない。
- [ ] ドロップダウンで `↓↑` 循環・`Enter` 遷移・`Esc` クローズ・外側クリッククローズが動作。ジャンプ行はアクティブ行のみ表示され、`target.kind` 別に §5.3-4 の URL へ遷移する。
- [ ] フッタ「すべての結果を表示({n} 件)→」で `/search?q=` へ遷移する。0 件時はフッタ非表示。
- [ ] 行チェック・Shift 範囲選択・全選択(未/一部/全の 3 状態)が動作し、選択 ≥1 でバー出現・「{n} 件を選択中」が追随する。
- [ ] 一括 3 アクションが `POST /api/library-items/bulk` の正しい `op` で発行され、成功で Toast+一覧/facets が再取得され、選択が維持される。失敗時にエラー Toast が出て選択が保持される。
- [ ] 「この条件を保存」→ 名前入力 → `POST /api/saved-filters` → サイドバー「保存フィルタ」に即時反映(件数付き)。条件未適用時はボタン disabled。
- [ ] 列ヘッダソート(可能 6 列)とツールバー「並び ▾」が単一状態で連動し、`deadline`/`comprehension` の null が常に末尾(サーバー保証の表示確認)。
- [ ] 無限スクロールで 50 件超のライブラリが末尾まで読める。フィルタ変更でスクロール位置がトップへ戻る。
- [ ] ローディング・空(2 種)・エラー・検索中・検索 0 件・検索エラーの各状態が §5.2 の文言どおり表示される。
- [ ] 行クリックで `/papers/{id}` へ遷移。チェックボックス列クリックは遷移しない。
- [ ] ビュー切替「カード」で `/library?view=card`(4a)へ切替(フィルタクエリは維持)。
- [ ] キーボードのみで全操作(フィルタ開閉・選択・一括実行・検索)が可能で、`focus-visible` リングが表示される。axe による a11y 自動チェックで違反 0。

## 7. 本書で確定した plans/03・他計画書への反映事項

1. `GET /api/search/preview` の `SearchHit_with_paper` に `meta: string | null`(サーバー導出の補足表記: body=ステータス日本語ラベル、note/chat/article=M/D 日付)を追加する(§4.3 の決定。plans/03 §15.2 に追記が必要)。
2. plans/08 §5.2 の `ReadingStatus`(`to_read` 等)と plans/03 §1.6 の `Status`(`planned` 等)は値が不一致。本書は境界写像(§3.2)で吸収したが、plans/08 の型を `Status` に揃える修正を推奨。
3. ビューアのディープリンク形式(`?block=` / `?panel=` / `?thread=` / `?message=` / `?mode=article`)は §5.3-4 で確定した。ビューア画面(1a/1b/1c/1h)・検索結果画面(4e)の計画書はこの形式に従うこと。
