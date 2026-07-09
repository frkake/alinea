# 画面 4d: 語彙帳

> **対象読者と前提**: 本書は「Alinea — 論文読解ワークベンチ」の apps/web(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4 + TanStack Query v5 + Zustand)実装者向けに、画面 4d(語彙帳 — 論文から育てる英語学習)を確定デザインと 100% 一致させるための完全仕様である。機能仕様は [docs/11-vocabulary.md](../../docs/11-vocabulary.md) を正、ピクセル値は確定デザイン抽出 `extract/4d.md` を正とする(本書 §4 に全量転記済み)。共通コンポーネント名は [plans/08-design-system.md](../08-design-system.md)、API エンドポイント名は [plans/03-api.md](../03-api.md)、DB スキーマは [plans/02-data-model.md](../02-data-model.md)(`vocab_entries`、02 §4.8)に従う。本書に書かれた値・識別子・文言が実装の正であり、独自の解釈・丸めを禁止する。

## 1. 概要とルート

- **ルートパス(確定)**: `/vocab`。1d 計画書 §1「本書で確定する画面間ルート」の決定(語彙帳 = `/vocab`)に従う。1b 計画書の 409 duplicate 導線 `/vocab/{vocab_id}` とも整合する。
- **ファイル(確定)**: `apps/web/src/app/(app)/vocab/[[...vocabId]]/page.tsx`(オプショナルキャッチオール)。
  - `/vocab` — 一覧先頭エントリを自動選択して表示(決定: URL は `/vocab` のまま書き換えない。選択 ID は `vocabId ?? 一覧先頭の id` で導出し、リダイレクトによる履歴・リクエストの揺れを避ける)。
  - `/vocab/{vocab_id}` — 当該エントリを選択状態で表示(1b「既に語彙帳にあります」トーストの「開く」、通知等からのディープリンク)。`vocabId = params.vocabId?.[0]`。
  - 行クリックによる選択変更は `router.replace('/vocab/' + id + '?' + searchParams.toString(), { scroll: false })`(shallow。履歴を汚さない。決定)。**決定: 選択変更の replace では現在のクエリ文字列(`kind` / `due` / `q` / `sort`)を必ず維持する**(維持しないとフィルタが選択操作で解除されてしまう)。以降本書の「`/vocab/{id}` へ replace」表記はすべてクエリ維持を含む。
- **URL クエリ(確定)**: フィルタ・検索・ソートは plans/03 §11.1 のクエリ語彙と同名で URL に持つ — `kind`(`word | collocation | idiom`。省略=すべて)/ `due`(`true`)/ `q` / `sort`(`added_at | term`。省略=`added_at`)。例: `/vocab?kind=idiom&due=true&sort=term`。
- **認証**: 必須。ルートグループ `(app)` のレイアウト(`apps/web/src/app/(app)/layout.tsx`)がセッション確認(`GET /api/auth/me`)を行い、401 は `/login?next=/vocab` へ `router.replace`。CSR(SSR は共有ページ 4c のみ — plans/01 §2.1)。
- **共通シェル**: トップバー(グローバル検索・◷ 通知・アバター)+左サイドバーは 1d/1e/4a/4e と共有する共通シェル `LibraryShell`(`apps/web/src/components/shell/LibraryShell.tsx`。1e 計画書 §1 の決定)を再利用する。4d 抽出のサイドバーに「+ 新規コレクション」ボタンとフッタ「設定 · エクスポート」が描かれていないのは省略描画とみなし、シェル完全形(4a 準拠)で描画する(1e 計画書 §1 の決定に従う。VRT 基準もシェル完全形で撮る)。
- **画面の役割**: ユーザー単位・全論文横断の 1 冊の語彙帳(docs/11 §1)。(1) ビューアの「語彙に追加」で文脈ごと保存された語彙のマスター/ディテール閲覧(左リスト+右詳細 400px)、(2) 種別・復習期フィルタと語彙帳内検索、(3) AI 生成コンテンツ(語義/解釈/語源/コツ/近い表現)の閲覧・編集・再生成、(4) 忘却曲線ベース SRS の自己評価と復習セッション。登録の入口はビューアのみで、この画面に手入力追加 UI は無い(docs/11 §2)。

## 2. データ要件

### 2.1 使用 API エンドポイント一覧(plans/03 の名前)

| # | エンドポイント | 用途 | 呼び出しタイミング |
|---|---|---|---|
| 1 | `GET /api/vocab`(03 §11.1) | 語彙リスト+件数(`counts: { all, word, collocation, idiom, due }`)。クエリ `kind` / `due` / `q` / `sort` / `cursor` / `limit=50` | マウント時+フィルタ/検索/ソート変更時。無限スクロール(useInfiniteQuery) |
| 2 | `GET /api/vocab/{vocab_id}`(03 §11.3) | 右詳細パネル(`VocabEntryDetail`) | 選択エントリ変更時。`generation: "pending"` の間は 2,000ms ポーリング(§2.4) |
| 3 | `PATCH /api/vocab/{vocab_id}`(03 §11.4) | フィールド編集(`ai.*` / `kind` / `term` / `pos_label` / `ipa`) | 編集保存時(mutation) |
| 4 | `DELETE /api/vocab/{vocab_id}`(03 §11.5) | エントリ削除 | ⋯ メニュー「削除」→ 取り消しトースト消滅時(遅延実行。plans/02 §1 の決定) |
| 5 | `POST /api/vocab/{vocab_id}/regenerate`(03 §11.6) | 生成失敗時の「生成を再試行」/ ⋯ メニュー「AI 生成をやり直す」 | クリック時(mutation)→ 202 `{ job_id }` 受領後 #2 のポーリングへ |
| 6 | `GET /api/vocab/review-queue`(03 §11.7) | 復習セッションの出題リスト(`VocabEntryDetail[]`、次回復習日の古い順・最大 100) | 「復習をはじめる」クリック時 |
| 7 | `POST /api/vocab/{vocab_id}/review`(03 §11.8) | 自己評価(`{ result: "again" \| "good" }`)→ `{ srs, next_review_display }` | 詳細フッタ/復習モーダルの評価ボタンクリック時(mutation) |
| 8 | `GET /api/auth/me`(03 §2.6) | トップバーのアバター・通知未読ドット(共通シェル) | シェルマウント時。staleTime 60,000ms |
| 9 | `GET /api/library-items/facets`(03 §5.2) | サイドバー「ライブラリ 41」(共通シェル) | シェルマウント時。staleTime 60,000ms |
| 10 | `GET /api/collections`(03 §13.1) | サイドバー「コレクション」節(共通シェル) | シェルマウント時。staleTime 60,000ms |
| 11 | `GET /api/saved-filters`(03 §5.14) | サイドバー「保存フィルタ」節(共通シェル) | シェルマウント時。staleTime 60,000ms |
| 12 | `GET /api/search/preview`(03 §15.2) | トップバー ⌘K 検索ドロップダウン(共通シェル。詳細は 1e 計画書) | 検索入力 200ms デバウンス |
| 13 | `GET /api/notifications`(03 §16.1) | 通知ポップオーバー(共通シェル。詳細は 4a 計画書) | ◷ クリック時 |

- サイドバー「語彙帳 46」・見出しサブ「46 語」・チップ「すべて 46」は同一ソース #1 の `counts.all` を使う(docs/11 の受け入れ基準「サイドバーの語彙帳バッジは総語数」)。シェル用の件数クエリ(1d 計画 `qk.vocabCounts` = `GET /api/vocab?limit=1`)は、本画面では #1 のレスポンスから `setQueryData(qk.vocabCounts, …)` で充足し、追加リクエストを発行しない(決定)。
- **`counts` はフィルタ非依存の全体件数**(03 §11.1)。チップの数字はフィルタ適用中も変わらない。

### 2.2 TanStack Query のキー設計

`apps/web/src/lib/query-keys.ts` に集約(1d 計画書と同一ファイル)。

```ts
// apps/web/src/lib/query-keys.ts(4d 関連の追加分)
export type VocabListParams = {
  kind?: 'word' | 'collocation' | 'idiom';
  due?: boolean;
  q?: string;
  sort: 'added_at' | 'term';
};
export const qk = {
  // …既存(me / libraryFacets / collections / savedFilters / vocabCounts / notifications)…
  vocabList: (params: VocabListParams) => ['vocab', 'list', params] as const, // GET /api/vocab(useInfiniteQuery)
  vocabEntry: (id: string) => ['vocab', 'entry', id] as const,                // GET /api/vocab/{id}
  vocabReviewQueue: ['vocab', 'review-queue'] as const,                        // GET /api/vocab/review-queue
} as const;
```

- 一覧: `useInfiniteQuery({ queryKey: qk.vocabList(params), initialPageParam: undefined, getNextPageParam: (last) => last.next_cursor, staleTime: 30_000 })`。`q` は 200ms デバウンス後の値でキーを構成する。
- 詳細: `useQuery({ queryKey: qk.vocabEntry(id), staleTime: 30_000, refetchInterval: (q) => q.state.data?.generation === 'pending' ? 2_000 : false })`。
- ミューテーション後の無効化(確定):
  - PATCH 成功 → `setQueryData(qk.vocabEntry(id), response)` + `invalidateQueries({ queryKey: ['vocab', 'list'] })`(種別・見出し語が一覧列・件数に出るため)。
  - review 成功 → `setQueryData` で該当詳細の `srs` をレスポンス値に差し替え + `invalidateQueries({ queryKey: ['vocab'] })`(`counts.due` = チップ「復習期」・ボタンバッジの更新)。
  - DELETE 成功(トースト消滅で確定)→ `invalidateQueries({ queryKey: ['vocab'] })`。
  - regenerate 202 → `invalidateQueries({ queryKey: qk.vocabEntry(id) })`(→ `pending` に変わりポーリング開始)。

### 2.3 クライアント状態(Zustand)

一覧の選択・フィルタ・ソートは URL(§1)が正であり、Zustand には持たない。Zustand は復習セッションのみ:

```ts
// apps/web/src/stores/vocab-review.ts
import { create } from 'zustand';
import type { VocabEntryDetail, ReviewResult } from '@alinea/api-client';

interface VocabReviewStore {
  queue: VocabEntryDetail[];      // 出題残(先頭 = 現在のカード)。[] かつ open=false が初期状態
  open: boolean;
  flipped: boolean;               // false=表(語義伏せ) / true=裏
  results: { id: string; result: ReviewResult }[];  // 完了画面の集計用
  start: (items: VocabEntryDetail[]) => void;       // open=true, flipped=false
  flip: () => void;
  answer: (result: ReviewResult) => void;  // 先頭を除去。'again' は末尾へ再エンキュー(docs/11 §7.2)
  close: () => void;                        // 途中終了可。評価済みのみ確定(P3)
}
export const useVocabReviewStore = create<VocabReviewStore>(/* 上記シグネチャ通り */);
```

### 2.4 リアルタイム更新(決定)

- **SSE 購読は行わない**。本画面が追う非同期処理は語彙 AI 生成(`enrich_vocab_entry`、plans/01 §4.3)のみで、p50 3 秒(docs/09 §1)と短く、`generation_job_id` は保存元のビューア側にしか渡らないため、ジョブ SSE(03 §21.2)ではなく **詳細 GET の 2,000ms ポーリング**(§2.2)で `pending → done | failed` を検知する。`done` になった時点でポーリングを止め、`invalidateQueries({ queryKey: ['vocab', 'list'] })`(一覧の短形語義を埋める)。
- 一覧・件数の他デバイス同期はしない(`refetchOnWindowFocus: true` の既定挙動のみ)。

## 3. コンポーネント分解

```
apps/web/src/app/(app)/vocab/[[...vocabId]]/page.tsx  … VocabPage(画面固有)
└─ LibraryShell                                        … 共通シェル(1e 計画 §1。SearchBox 'global'・SidebarNav・通知 Popover)
   └─ VocabPage 本体(main 領域)
      ├─ VocabHeader                                   … 画面固有(タイトル/件数サブ/語彙検索/復習をはじめる)
      ├─ VocabFilterRow                                … 画面固有
      │   ├─ FilterChip ×4(08 §5.6)                   … 共通(すべて/単語/コロケーション/イディオム)
      │   └─ DueFilterChip                             … 画面固有(琥珀系「復習期 12」)
      ├─ VocabList                                     … 画面固有(Card 08 §5.9 面)
      │   ├─ VocabListHeader                           … 画面固有(4 列+ソート表示)
      │   ├─ VocabListRow ×n                           … 画面固有(VocabKindBadge 内包)
      │   ├─ EmptyState(08 §5.21)                     … 共通(0 件時)
      │   └─ VocabListFooterNote                       … 画面固有(常設注記)
      ├─ VocabDetailPanel                              … 画面固有(Card 面、w=400px)
      │   ├─ VocabDetailHeader                         … 画面固有(見出し語/IPA/VocabKindBadge/メタ行/⋯)
      │   │   └─ Popover(08 §5.10, width=180)         … 共通(⋯ オーバーフローメニュー)
      │   ├─ VocabDetailBody                           … 画面固有
      │   │   └─ EditableVocabSection ×6               … 画面固有(見出し+本文+ホバー編集)
      │   └─ VocabDetailFooter                         … 画面固有(次の復習/まだあやしい/✓ 覚えた)
      └─ VocabReviewModal                              … 画面固有
          └─ Modal(08 §5.11, width=460)               … 共通
```

共通コンポーネント(plans/08 の名前をそのまま使用): `LibraryShell` 内の `SearchBox`(§5.13 `global`)・`SidebarNav`(§5.14)・`Popover`(§5.10)/ `FilterChip`(§5.6)/ `Card`(§5.9)/ `EmptyState`(§5.21)/ `Modal`(§5.11)/ `MagnifierIcon`(§6.1)/ Toast は `useToast()`(§5.20)。

画面固有コンポーネント(配置: `apps/web/src/components/vocab/`)の props 型:

```ts
import type { VocabKind, VocabEntrySummary, VocabEntryDetail, ReviewResult } from '@alinea/api-client';

interface VocabHeaderProps {
  total: number;                       // counts.all → 「46 語 — 読んだ論文の文脈から」
  dueCount: number;                    // counts.due → ボタン内バッジ「12」
  searchValue: string;                 // URL ?q= と同期(200ms デバウンス)
  onSearchChange: (v: string) => void;
  onStartReview: () => void;           // GET /api/vocab/review-queue → store.start()
  reviewLoading: boolean;              // review-queue 取得中はボタン opacity:0.7・多重クリック防止
}

interface VocabFilterRowProps {
  counts: { all: number; word: number; collocation: number; idiom: number; due: number };
  kind: VocabKind | null;              // null = 「すべて」選択中(排他単一選択。決定)
  dueOnly: boolean;                    // 「復習期」は種別と独立のトグル(決定)
  onKindChange: (kind: VocabKind | null) => void;
  onDueToggle: () => void;
}

interface VocabListProps {
  entries: VocabEntrySummary[];
  selectedId: string | null;
  sort: 'added_at' | 'term';
  onSortChange: (sort: 'added_at' | 'term') => void;
  onSelect: (id: string) => void;      // router.replace('/vocab/'+id)
  onReachEnd: () => void;              // fetchNextPage(スクロール末尾 200px 手前)
  isFetchingNextPage: boolean;
  emptyVariant: 'no-entries' | 'no-match' | null;  // §5.4
}

interface VocabListRowProps {
  entry: VocabEntrySummary;
  selected: boolean;
  onClick: () => void;
}

interface VocabKindBadgeProps {
  kind: VocabKind;                     // word=単語(グレー) / collocation=コロケーション(青系) / idiom=イディオム(紫系)
  size: 'list' | 'detail';             // list: h16px/9px(一覧行) / detail: h17px/9.5px(詳細ヘッダ)
}

interface VocabDetailPanelProps {
  vocabId: string | null;              // null = リスト 0 件(§5.4 空表示)
}

interface EditableVocabSectionProps {
  heading: string;                     // 「文脈での語義」等(逐語。§4.2.6)
  variant: 'plain' | 'card' | 'amber' | 'quote';  // §4.2.6 の 4 体裁
  html: React.ReactNode;               // 表示体(太字・イタリック・mark 含む)
  rawValue: string;                    // 編集用プレーン値(PATCH に送る)
  fieldKey: 'context_meaning' | 'interpretation' | 'etymology' | 'mnemonic' | 'related_expressions';
  edited: boolean;                     // edited_fields に含まれるか(再生成スキップ対象)
  generation: 'pending' | 'done' | 'failed';
  onSave: (fieldKey: string, value: string) => void;  // PATCH /api/vocab/{id}
}

interface VocabDetailFooterProps {
  srs: VocabEntryDetail['srs'];
  onReview: (result: ReviewResult) => void;  // POST /api/vocab/{id}/review
  pending: boolean;                          // mutation 中は両ボタン disabled
}

interface VocabReviewModalProps {}           // 状態はすべて useVocabReviewStore から取得
```

## 4. レイアウト・スタイル完全仕様

出典: `extract/4d.md`(確定デザイン `論文読解システム デザイン.dc.html` id="4d")。フレーム外要素(ポップオーバー等)はこの画面のデザインには存在しない。フレームは 1440×900px 単一。デザインの「フレーム」装飾(border 1px #D6D3C9 / radius 10px / shadow `--pr-shadow-frame`)はデザインキャンバス上の表現であり、実アプリではビューポート全面に描画する(plans/08 §7.1)。1440px 超では固定幅(サイドバー 216px・詳細パネル 400px)を維持し、語彙リスト(flex:1)が広がる(plans/08 §7.2)。

### 4.0 デザイナー注記(逐語。実装対象外 — デザインキャンバスのラベル)

- バッジ「4d」/ タイトル「**語彙帳 — 論文から育てる英語学習**」/ 説明「**ビューアの「語彙に追加」から文脈つきで保存(仕様04 §4) / 語義・語源・イディオムの解釈方法・覚えるコツ / 復習キュー**」

### 4.1 レイアウト構造

フレーム全体: 背景 `#F4F3EF`(`--pr-bg-app-alt`)、文字色 `#1E2227`(`--pr-text`)、display:flex; flex-direction:column。

```
┌────────────────────────────────────────────────────────────── 1440px ─┐
│ トップバー h=52px  #FFFFFF  border-bottom:1px #E6E3DA                  │
│ [Aロゴ+Alinea w=198] [検索バー w=460 h=32] …flex:1… [◷30×30] [YK30×30] │
├───────────┬───────────────────────────────────────────────────────────┤
│ 左サイド   │ メインエリア flex:1  padding:16px 22px  縦flex gap:12px    │
│ w=216px   │ ┌ 見出し行(語彙帳 / 46語 / 検索 / 復習をはじめるボタン) ┐ │
│ #F7F6F2   │ ├ フィルタチップ行(すべて46/単語28/…/復習期12 + ヒント) ┤ │
│ border-   │ ├──────────────── flex:1 gap:14px ────────────────────────┤ │
│ right:1px │ │ 語彙リスト(flex:1, 白カード) │ 詳細パネル w=400px 白 │ │
│ #E7E4DB   │ │  ヘッダ行+8行+フッタ注記      │  ヘッダ/本文6節/フッタ │ │
│           │ └───────────────────────────────┴────────────────────────┘ │
└───────────┴───────────────────────────────────────────────────────────┘
```

- 本体行: `flex:1; display:flex; min-height:0`。
- 左サイドバー: width:216px flex:none、背景 `#F7F6F2`(`--pr-bg-pane`)、border-right:1px solid `#E7E4DB`(`--pr-border-pane`)、padding:12px 10px、縦 flex gap:2px、font-size:12.5px、color `#3A3E44`(`--pr-text-nav`)。
- メイン: `flex:1; min-width:0; padding:16px 22px`、縦 flex gap:12px、overflow:hidden。
- リスト+詳細の 2 カラム: `display:flex; gap:14px; flex:1; min-height:0`。

### 4.2 コンポーネント詳細(上から順)

#### 4.2.1 トップバー(h=52px, flex:none)— 共通シェル `LibraryShell`

背景 `#FFFFFF`(`--pr-bg-card`)、border-bottom:1px solid `#E6E3DA`(`--pr-border-header`)、display:flex; align-items:center; gap:14px; padding:0 18px。

1. ロゴ群(flex, gap:8px, width:198px)
   - ロゴマーク: 22×22px、border-radius:6px、背景 `var(--pr-a)`(#3E5C76)、文字「A」#FFFFFF、11.5px/700、inline-flex 中央。
   - ワードマーク「Alinea」: 14.5px/700、letter-spacing:0.5px。
2. グローバル検索バー(= `SearchBox variant='global'`): flex, gap:8px, height:32px, width:460px、背景 `#F1EFE9`(`--pr-bg-inset`)、border-radius:7px、padding:0 12px、12px、color `#8A8E94`(`--pr-text-icon`)。
   - 虫眼鏡 SVG 12×12(`MagnifierIcon`: circle cx5 cy5 r3.6 + 線 M8 8→10.6 10.6、stroke currentColor 1.3、linecap round)。
   - プレースホルダ「ライブラリ全体を検索 — 本文・訳文・メモ・チャット」。
   - 右端(margin-left:auto)キーキャップ「⌘K」: border:1px solid `#DAD7CD`(`--pr-border-keycap`)、border-radius:3px、padding:0 5px、9.5px、背景 #FFFFFF、font-family `var(--pr-font-mono)`。
3. スペーサ flex:1。
4. 履歴/通知アイコンボタン「◷」: 30×30px、border-radius:7px、border:1px solid `#E2DFD5`、color `#5B6067`(`--pr-text-sub`)、13px、position:relative。
5. アバター「YK」: 30×30px、border-radius:50%、背景 `var(--pr-as)`、文字 `var(--pr-a)`、11px/700。イニシャルは `me.display_name` から生成(共通シェル)。

#### 4.2.2 左サイドバー(w=216px)— 共通シェル `SidebarNav`(08 §5.14)

各行 padding:7px 10px(ナビ項目)/ 6px 10px(コレクション・保存フィルタ項目)、border-radius:6px(ナビのみ)。数値バッジ = `CountBadge variant='nav'`(font-size:10.5px、color `#9A9EA4` = `--pr-text-muted`。選択行は色継承)。

- 「ホーム」(通常)— href=`/`
- 「ライブラリ」+ 右端数値「41」(flex, gap:8px, ラベル flex:1)— href=`/library`。件数 = `facets.quick.all`
- 「語彙帳」+「46」 — **選択中**: 背景 `var(--pr-as)`、color `var(--pr-a)`、font-weight:600。件数 = `counts.all`
- セクション見出し「コレクション」: 10.5px/600、color `#9A9EA4`、letter-spacing:0.4px、padding:14px 10px 4px
- 「輪読会 2026-07」+「5」/「Diffusion 蒸留」+「8」(= `GET /api/collections` の `items[].{name, item_count}`)
- セクション見出し「保存フィルタ」(同スタイル)
- 「締切あり」+「3」/「cs.CV の未読」+「7」(= `GET /api/saved-filters` の `items[].{name, count}`)
- (シェル完全形: 「+ 新規コレクション」とフッタ「設定 · エクスポート」を 4a 準拠で描画。§1 の決定)

#### 4.2.3 メイン見出し行(`VocabHeader`。flex, gap:12px)

- タイトル「語彙帳」16px/700。
- サブ「46 語 — 読んだ論文の文脈から」11.5px、color `#9A9EA4`。数値 = `counts.all`。書式: `{counts.all} 語 — 読んだ論文の文脈から`。
- スペーサ flex:1。
- 語彙検索ボックス: inline-flex gap:6px、height:28px、padding:0 10px、背景 #FFFFFF、border:1px solid `#DDD9CF`(`--pr-border-control`)、border-radius:6px、11.5px、color `#8A8E94`、width:220px。`MagnifierIcon` 11×11(同形状)+プレースホルダ「語彙を検索」。グローバル検索(⌘K)とは別の専用 `<input>`(docs/11 §5.1)。
- プライマリボタン「復習をはじめる」: inline-flex gap:7px、height:28px、padding:0 13px、border-radius:6px、背景 `var(--pr-a)`、#FFFFFF、11.5px/600。内包カウントバッジ「12」: 9.5px/500、opacity:0.8、border:1px solid rgba(255,255,255,0.4)、border-radius:3px、padding:0 5px。数値 = `counts.due`。

#### 4.2.4 フィルタチップ行(`VocabFilterRow`。flex, gap:6px)

すべて height:22px、inline-flex、padding:0 10px、border-radius:999px、11px。ラベル書式: `{名称} {件数}`(半角スペース区切り)。

- 「すべて 46」— 選択中(`FilterChip selected`): 背景 `#26292E`(`--pr-elev-bg`。両テーマ共通)、文字 #FFFFFF、weight:600。
- 「単語 28」「コロケーション 12」「イディオム 6」— 非選択(`FilterChip`): border:1px solid `#DDD9CF`、color `#3C4046`(`--pr-text-mid`)、背景 #FFFFFF。
- 「復習期 12」— 強調チップ(画面固有 `DueFilterChip`): gap:5px、border:1px solid #E4CFA6、color #8A6A24(`--pr-ann-important-chip-fg`)、背景 #FFF9F0、weight:600。琥珀系の**常時強調表示**であり、種別チップの選択トーンとは別(docs/11 §5.1)。
- 右端ヒント(margin-left:auto): 「本文で選択 → 「語彙に追加」で文脈ごと保存されます」10.5px、color `#9A9EA4`。空状態に頼らず常時表示(docs/11 §5.1)。

#### 4.2.5 語彙リスト(`VocabList`。左カード, flex:1)

`Card` 面: 背景 #FFFFFF、border:1px solid `#E2DFD5`(`--pr-border-card`)、border-radius:10px、overflow:hidden、縦 flex、min-width:0。

行グリッド共通: `grid-template-columns: 1.25fr 1.35fr 170px 56px`、align-items:center、gap:8px、padding: ヘッダ 8px 16px / データ行 10px 16px。

- ヘッダ行(`VocabListHeader`): border-bottom:1px solid `#ECE9DF`(`--pr-border-soft`)、10.5px/600、color `#9A9EA4`。セル文言: 「語彙 ↑」「文脈での語義」「出典」「追加」。デザインは語彙列で昇順ソート中の状態(「↑」)を示す。ソートインジケータの表示規則は §5.3。
- データ行(`VocabListRow`。区切り border-bottom:1px solid `#F4F1E9`(`--pr-border-row`)、最終行は border なし):
  - 1 列目: flex gap:7px — 語彙本体 12.5px/600、font-family `var(--pr-font-en)`('Source Serif 4', Georgia, serif)+ 種別バッジ(`VocabKindBadge size='list'`: height:16px、padding:0 6px、border-radius:3px、9px/600)。
    - バッジ色: 単語=背景 #F1EFE9 / 文字 #777B81、コロケーション=背景 rgba(88,132,170,0.16) / 文字 #4A6E8E、イディオム=背景 rgba(110,90,126,0.14) / 文字 #6E5A7E(docs/11 §3 の 3 分類固定)。
  - 2 列目(文脈での語義 = `meaning_short`): 11.5px、color `#3C4046`。
  - 3 列目(出典 = `source.display`): 10px、color `#9A9EA4`。
  - 4 列目(追加 = `added_at` の相対表示。§5.6): 10px、color `#9A9EA4`。
  - 選択中行: 背景 `var(--pr-as)`(rgba(62,92,118,0.10))。
- デザイン描画データ(8 行。上から — シードデータ・VRT 基準):

| 語彙 | 種別 | 文脈での語義 | 出典 | 追加 |
|---|---|---|---|---|
| **boil down to**(選択中) | イディオム | 要するに〜に帰着する | Rectified Flow · §2.1 | 昨日 |
| **circumvent** | 単語 | (問題・手続きを)迂回して避ける | Rectified Flow · §2.2 | 昨日 |
| **albeit** | 単語 | 〜ではあるが(文語・簡潔な譲歩) | Adversarial Diffusion… · §4 | 3日前 |
| **in tandem with** | コロケーション | 〜と連動して・同時並行で | Progressive Distillation · §2.3 | 6/28 |
| **amortize** | 単語 | (コストを)前払いして複数回で回収する | InstaFlow · §3.1 | 6/24 |
| **shed light on** | イディオム | 〜を解き明かす・光を当てる | Score-Based SDE · §5 | 6/20 |
| **hinge on** | コロケーション | 〜にかかっている・〜次第である | Flow Matching · §1 | 6/15 |
| **de facto** | 単語 | 事実上の(標準) | Consistency Models · §2 | 6/12 |

- スペーサ `flex:1`(空 div。行がカード高さに満たない場合にフッタを最下段へ)。
- フッタ注記(`VocabListFooterNote`): padding:9px 16px、border-top:1px solid `#ECE9DF`、10.5px、color `#9A9EA4`: 「語義・語源・コツは保存時に文脈から自動生成されます(編集可)· 復習は忘却曲線に沿って出題」。
- 行領域(ヘッダ除く)は `overflow-y:auto`(内部スクロール。決定: デザインは 8 行で収まる状態のみを描くため、スクロールコンテナは行領域+スペーサとし、ヘッダ行とフッタ注記は固定)。

#### 4.2.6 詳細パネル(`VocabDetailPanel`。右カード, w=400px flex:none)

`Card` 面: 背景 #FFFFFF、border:1px solid `#E2DFD5`、border-radius:10px、縦 flex、overflow:hidden。

**ヘッダ(`VocabDetailHeader`)**: padding:14px 16px、border-bottom:1px solid `#F0EDE4`(`--pr-border-hair`)、縦 flex gap:5px。

- 1 行目(flex gap:8px, align-items:baseline):
  - 見出し語「boil down to」(`term`)16px/700、`var(--pr-font-en)`。
  - 発音記号「/ˌbɔɪl ˈdaʊn tə/」(`ipa`)10.5px、color `#9A9EA4`、`var(--pr-font-mono)`。`ipa` が null(生成前)は非表示。
  - バッジ「イディオム」(`VocabKindBadge size='detail'`): height:17px、padding:0 7px、border-radius:3px、背景 rgba(110,90,126,0.14)、color #6E5A7E、9.5px/600。
  - ⋯ オーバーフローボタン(margin-left:auto。**決定**: docs/11 §6.3 が定める削除・再生成メニューの取っ手。デザイン 4d には未描画だが docs が必須とするため追加する): 20×20px、border-radius:5px、グリフ「⋯」15px、color `#5B6067`(`--pr-text-sub`)、letter-spacing:1px(08 §6.2)。ホバー背景 `--pr-bg-hover`。クリックで `Popover`(width:180、placement `bottom-end`)。メニュー項目は §5.8。
- 2 行目(flex gap:6px、10.5px、color `#9A9EA4`): 「句動詞 · Rectified Flow §2.1 で追加 · 」+ バッジ「AI生成 · 編集可」。
  - テキスト部の書式: `{pos_label} · {source.display の「 · 」を半角スペース 1 個に置換した文字列} で追加 · `(決定: 詳細メタ行はデザイン逐語で論文名と § の間に中黒が無いため、`source.display` から導出する)。`pos_label` が null の間は種別ラベル(単語/コロケーション/イディオム)を代置。
  - バッジ「AI生成 · 編集可」(画面固有スタイル。08 §5.19 `AIBadge generated` とは寸法が異なるため専用 span と決定): height:14px、padding:0 5px、border:1px solid `#DDD9CF`、border-radius:3px、8.5px/600、color `#8A8E94`。`generation === 'done'` のとき表示。

**本文(`VocabDetailBody`)**: padding:14px 16px、縦 flex gap:12px、flex:1、`overflow-y:auto`(決定: デザインは overflow:hidden の収まった状態を描くが、長文編集時に切れて消えるのは P3 違反のため縦スクロールを許可する)。

各セクション(`EditableVocabSection`): 縦 flex gap:5px。セクション見出しは共通で 10.5px/700、color `#9A9EA4`、letter-spacing:0.4px(「✦ 覚えるコツ」のみ color #8A6A24)。

1. **文脈での語義**(`variant='plain'`、`ai.context_meaning.long`): 本文 12px、line-height:1.7、color `#24272B`(`--pr-text-body`)— 「(複雑なものが)煮詰まって**結局〜に帰着する**(太字 `<b>`)。この文では「学習目的は結局、単純な最小二乗回帰になる」。」
2. **文脈センテンス**(`variant='quote'`、`context_sentence` + `highlight`): 11px、line-height:1.7、color `#5B6067`、border-left:2px solid `#E2DFD5`、padding-left:9px、`var(--pr-font-en)`、italic。
   - 英文: 「With this choice, the training objective *boils down to* a simple least squares regression problem.」— `highlight.start`〜`highlight.end` の範囲を `<mark>`: 背景 rgba(196,148,50,0.30)(`--pr-ann-important-chip-bg`)、border-radius:2px、padding:0 1px、font-style:normal。
   - 末尾に注記 span(font-family `var(--pr-font-ui)`、color `#9A9EA4`、9.5px、font-style:normal): 「 — §2.1 · 原文で見る →」。「原文で見る →」部分がリンク(§5.7)。§ 表記は `source.display` の § 部分。
   - 本セクションは**編集不可**(原文の引用のため。決定)。
3. **解釈のしかた(句動詞の読み方)**(`variant='card'`、`ai.interpretation`): 本文 11.5px、line-height:1.75、color `#3C4046`、背景 `#FAF9F5`(`--pr-bg-hover`)、border-radius:7px、padding:9px 11px — 「**boil**(煮る)+ **down**(量が減る方向)+ **to**(到達点)。煮詰めて水分を飛ばすと本質だけが残り、「to 以下」に行き着く — 句動詞は動詞の物理イメージ+方向詞で読む。」(boil/down/to は `<b>`)。見出し文言は生成側が語彙タイプに応じて変える。**決定(保存形式を確定)**: `ai.interpretation` の 1 行目が「(」で始まり「)」で終わる場合、その 1 行目を見出し補足とみなし、見出しは固定文字列「解釈のしかた」+その 1 行目(例:「解釈のしかた(句動詞の読み方)」)、本文は 2 行目以降を表示する。1 行目がこの形式でない場合は見出し「解釈のしかた」のみ+全文を本文表示。編集 textarea には 1 行目を含む生文字列全体を表示・保存する(フィールドは 1 本のまま)。
4. **語源メモ**(`variant='plain'`、`ai.etymology`): 11px、line-height:1.7、color `#5B6067` — 「boil ← ラテン語 *bullīre*(イタリック `<i>`)(泡立つ)。bubble、ebullient(沸き立つ→熱狂的な)と同族。」
5. **✦ 覚えるコツ**(`variant='amber'`、`ai.mnemonic`。見出し色 #8A6A24): 本文 11.5px、line-height:1.75、color `#3C4046`、背景 #FFF9F0、border:1px solid #EEDDB8、border-radius:7px、padding:9px 11px — 「カレーを煮詰めるイメージ。枝葉(水分)が飛んで、ルー(本質)だけが鍋に残る。「議論を煮詰めると、結局 X だ」。」
6. **よく出る形・近い表現**(`variant='plain'`、`ai.related_expressions`): 11px、line-height:1.8、color `#5B6067`。英語表現部は `var(--pr-font-en)`、color `#33373C`(`--pr-text-en`)— 「it boils down to *whether*(イタリック)… / come down to(ほぼ同義)/ amount to(積み上がって〜になる)」

- 生成テキスト内の装飾(`<b>` / `<i>` / 英語部の書体切替)は、生成出力を Markdown サブセット(`**bold**` / `*italic*` / バッククォート無し)として保存し、表示時に変換する(**決定**: HTML を保存しない。plans/03 §11 のフィールドは string であり、編集 textarea でもそのまま扱えるため)。

**フッタ(`VocabDetailFooter`)**: padding:11px 16px、border-top:1px solid `#F0EDE4`、flex gap:8px, align-items:center。

- テキスト「次の復習: 明日(2 回目)」10.5px、color `#9A9EA4`。書式は §5.6。
- セカンダリボタン「まだあやしい」(margin-left:auto): height:26px、padding:0 12px、border:1px solid `#DDD9CF`、border-radius:6px、11px、color `#5B6067`、背景 #FFFFFF。
- プライマリボタン「✓ 覚えた」: height:26px、padding:0 12px、border-radius:6px、背景 `var(--pr-a)`、#FFFFFF、11px/600。

### 4.3 全 UI 文言(逐語)

トップバー: 「A」「Alinea」「ライブラリ全体を検索 — 本文・訳文・メモ・チャット」「⌘K」「◷」「YK」

サイドバー: 「ホーム」「ライブラリ」「41」「語彙帳」「46」「コレクション」「輪読会 2026-07」「5」「Diffusion 蒸留」「8」「保存フィルタ」「締切あり」「3」「cs.CV の未読」「7」

見出し行: 「語彙帳」「46 語 — 読んだ論文の文脈から」「語彙を検索」「復習をはじめる」「12」

フィルタ行: 「すべて 46」「単語 28」「コロケーション 12」「イディオム 6」「復習期 12」「本文で選択 → 「語彙に追加」で文脈ごと保存されます」

リストヘッダ: 「語彙 ↑」「文脈での語義」「出典」「追加」

リスト行(8 行の全セル文言は §4.2.5 の表のとおり)

リストフッタ: 「語義・語源・コツは保存時に文脈から自動生成されます(編集可)· 復習は忘却曲線に沿って出題」

詳細パネル:
- 「boil down to」「/ˌbɔɪl ˈdaʊn tə/」「イディオム」
- 「句動詞 · Rectified Flow §2.1 で追加 · 」「AI生成 · 編集可」
- 「文脈での語義」「(複雑なものが)煮詰まって結局〜に帰着する。この文では「学習目的は結局、単純な最小二乗回帰になる」。」
- 「文脈センテンス」「With this choice, the training objective boils down to a simple least squares regression problem.」「 — §2.1 · 原文で見る →」
- 「解釈のしかた(句動詞の読み方)」「boil(煮る)+ down(量が減る方向)+ to(到達点)。煮詰めて水分を飛ばすと本質だけが残り、「to 以下」に行き着く — 句動詞は動詞の物理イメージ+方向詞で読む。」
- 「語源メモ」「boil ← ラテン語 bullīre(泡立つ)。bubble、ebullient(沸き立つ→熱狂的な)と同族。」
- 「✦ 覚えるコツ」「カレーを煮詰めるイメージ。枝葉(水分)が飛んで、ルー(本質)だけが鍋に残る。「議論を煮詰めると、結局 X だ」。」
- 「よく出る形・近い表現」「it boils down to whether… / come down to(ほぼ同義)/ amount to(積み上がって〜になる)」
- 「次の復習: 明日(2 回目)」「まだあやしい」「✓ 覚えた」

### 4.4 データフィールド写像(API → UI)

| UI | ソース(plans/03 §11) |
|---|---|
| 一覧: 語彙 / 種別バッジ / 文脈での語義 / 出典 / 追加 | `VocabEntrySummary.term` / `.kind` / `.meaning_short` / `.source.display` / `.added_at` |
| 件数(すべて/単語/コロケーション/イディオム/復習期) | `GET /api/vocab` の `counts.{all, word, collocation, idiom, due}` |
| 詳細ヘッダ: 見出し語 / IPA / 種別 / 品詞 / 追加元 | `VocabEntryDetail.term` / `.ipa` / `.kind` / `.pos_label` / `.source.display` |
| 6 セクション | `.ai.context_meaning.long` / `.context_sentence`+`.highlight` / `.ai.interpretation` / `.ai.etymology` / `.ai.mnemonic` / `.ai.related_expressions` |
| 「AI生成 · 編集可」/ 失敗表示 | `.generation` / `.ai.generation_error` / `.ai.edited_fields` |
| 「原文で見る →」 | `.anchor`(AnchorRef)→ `/papers/{source.library_item_id}?mode=source&block={anchor.block_id}` |
| 「次の復習: 明日(2 回目)」 | `.srs.next_review_at` / `.srs.review_count`(§5.6 の書式)。評価直後は `POST …/review` の `next_review_display` をそのまま表示 |

## 5. 状態とインタラクション

### 5.1 デザインに描かれた状態(そのまま実装)

- サイドバー「語彙帳」がアクティブ(`--pr-as` 背景 + `--pr-a` 文字 + weight 600)。他項目は非アクティブ。
- フィルタチップ「すべて」が選択中(elev 黒背景・白文字)。種別 3 チップは非選択。「復習期」は琥珀系の常時強調(選択時の変化は §5.2)。
- リスト 1 行目が選択中(背景 `--pr-as`)で右詳細パネルと連動(マスター/ディテール)。
- 語彙種別バッジ 3 種(単語=グレー / コロケーション=青系 / イディオム=紫系)。
- 「復習をはじめる」に復習待ち件数バッジ。文脈センテンス内の対象語ハイライト。「AI生成 · 編集可」バッジ。SRS 進捗表示+2 択評価ボタン。列ソートインジケータ(「語彙 ↑」)。

### 5.2 フィルタ・検索(インタラクション)

- 種別チップは**排他単一選択**(決定: 「すべて」の存在が単一選択を示すため。API の `kind` 複数指定は使わない)。クリックで `?kind=` を書き換え(すべて=削除)、`router.replace`。選択中チップの再クリックは何もしない。
- 「復習期」チップは独立トグル(決定)。ON で `?due=true`。ON 状態の見た目(デザイン未描画。**決定**): 背景 #8A6A24・文字 #FFFFFF・weight 600・border なし(琥珀トーンを保ったまま選択チップと同じ「地色反転」規則を適用。`--pr-elev-bg` は使わない)。
- 「語彙を検索」: 入力を 200ms デバウンスして `?q=` へ反映 → `GET /api/vocab?q=`(見出し語・語義のインクリメンタル検索。03 §11.1)。`q` を含む一覧クエリの取得中(`isFetching`)はフィールド右端に 10×10px のスピナー(1.5px 枠、`--pr-text-muted`、回転 800ms linear infinite。決定: 表示条件は「入力中」ではなくフェッチ中)。フォーカス時の枠: border 1.5px `var(--pr-a)` + `box-shadow: 0 0 0 3px var(--pr-as)`(08 §5.13 SearchBox のフォーカス様式と統一。決定)。
- フィルタ・検索・ソート変更時、現在選択中のエントリが結果に含まれない場合は結果先頭を自動選択する(`/vocab/{先頭id}`(クエリ維持)へ replace。決定)。結果が 0 件の場合は選択を解除し `/vocab`(クエリ維持、パス id なし)へ replace、詳細パネルは `EmptyState` title「語彙が選択されていません」を表示する(決定)。

### 5.3 ソート(決定)

- 既定ソートは**追加日の降順**(docs/11 §5.2 の決定。`sort=added_at`・URL 省略)。ヘッダ表示は「追加 ↓」。
- 「語彙」ヘッダクリック → `sort=term`(昇順固定。03 §11.1)、ヘッダ表示「語彙 ↑」。「追加」ヘッダクリック → `sort=added_at`(降順固定)、表示「追加 ↓」。同一列の再クリックによる昇降トグルは行わない(API が方向を固定しているため。決定)。
- インジケータ「↑」「↓」はアクティブ列のみラベル末尾に半角スペース+矢印で付く。「文脈での語義」「出典」はソート不可(cursor:default)。
- デザインの描画(「語彙 ↑」がアクティブ)は `sort=term` 状態として再現する。**注意**: デザインの行順(追加日降順)とヘッダ表示(語彙 ↑)はデザイン内部で不整合であり、実装は行順をソートキーに必ず一致させる。VRT 基準は §6 のとおり 2 状態を撮る。

### 5.4 ローディング・空・エラー(デザイン未描画。決定)

- **一覧初回ローディング**: スケルトン 8 行。各行は 4.2.5 のグリッドで、1 列目に 120×12px、2 列目に 220×11px、3 列目に 110×10px、4 列目に 32×10px の角丸 3px 矩形(背景 `--pr-bg-muted`、opacity パルス 1.2s)。ヘッダ行・フッタ注記は実表示。
- **詳細ローディング**: ヘッダに 140×16px+セクション 6 個分に「見出し実表示+本文 2 行分(100%×11px、70%×11px)」のスケルトン。
- **語彙 0 件(フィルタなし)**: リストカード内に `EmptyState` — title「まだ語彙がありません」/ description「ビューアで本文(英語原文)を選択し、「語彙に追加」を選ぶと、文脈センテンスごとここに保存されます。」/ action なし。詳細パネルは `EmptyState` — title「語彙が選択されていません」のみ。フィルタ行・フッタ注記は表示を維持(件数は 0)。
- **フィルタ/検索 0 件**: title「該当する語彙がありません」/ description「フィルタか検索語を変えてみてください。」/ action `{ label: '絞り込みを解除', onClick: () => router.replace('/vocab') }`。
- **一覧取得エラー**: リストカード内中央に 12px `--pr-text-sub`「語彙帳を読み込めませんでした」+ 再試行ボタン(EmptyState の action 様式、label「再読み込み」→ `refetch()`)。
- **詳細取得エラー**: 詳細パネル内に同様式「この語彙を読み込めませんでした」+「再読み込み」。404(削除済み ID へのディープリンク)は Toast `{ kind: 'error', message: 'この語彙は見つかりませんでした' }` を出し `/vocab` へ replace。
- **行ホバー**: 背景 `var(--pr-bg-hover)`(#FAF9F5)、cursor:pointer(選択中行はホバーでも `--pr-as` を維持)。ボタン・チップのホバー: プライマリは `filter: brightness(0.95)`、枠付きは背景 `--pr-bg-hover`(決定。全画面共通の慣例に一致)。
- **キーボード**: リスト行は `role="listbox"`/`role="option"`。↑/↓ で選択移動(選択 = 詳細表示。`router.replace`。端ではそれ以上動かない — ラップしない。決定)、フォーカスリングは 08 §5 共通(`outline: 1.5px solid var(--pr-acc)`)。

### 5.5 AI 生成の進行・失敗(docs/11 §2・§4)

- `generation === 'pending'`(保存直後に開いた場合): 詳細の編集可能 5 セクション(1・3・4・5・6)の本文位置に「✦ 生成中…」(11.5px、color `--pr-text-muted`、`✦` は `var(--pr-a)`)+ §5.4 の 2 行スケルトン。IPA・pos_label は非表示。「AI生成 · 編集可」バッジ非表示。一覧行の語義列は「生成中…」(11.5px、`--pr-text-muted`)。2,000ms ポーリング(§2.4)。
- `generation === 'failed'`: 本文先頭(セクション 1 の位置)に失敗カード — 背景 `var(--pr-warn-bg)`、**border なし**(決定: 枠線は付けない)、border-radius:7px、padding:9px 11px、11.5px、color `var(--pr-warn)`。文言「学習コンテンツの生成に失敗しました — {ai.generation_error}」+ 右端リンクボタン「生成を再試行」(11px/600、color `var(--pr-warn)`、下線)→ `POST /api/vocab/{id}/regenerate`(fields 省略=未編集全フィールド)。語彙本体・文脈センテンス・出典は表示されたまま(黙って捨てない。P3)。一覧行の語義列は「生成に失敗 — 再試行できます」(11.5px、`var(--pr-warn)`)。

### 5.6 日時・SRS 表示書式(決定)

- 一覧「追加」列(クライアント整形。03 §1.6): 当日=「今日」、前日=「昨日」、2〜6 日前=「{n}日前」、7 日以上=「{M}/{D}」(同年)、年跨ぎ=「{YYYY}/{M}/{D}」。
- フッタ「次の復習」: `next_review_at` が 当日以前=「今日」、翌日=「明日」、2〜6 日後=「{n}日後」、7 日以降=「{M}/{D}」(同年)、年跨ぎ=「{YYYY}/{M}/{D}」(決定: 追加列と同じ絶対表記規則)。全体書式「次の復習: {日付表示}({review_count + 1} 回目)」。評価直後はレスポンスの `next_review_display` をそのまま表示(サーバー文字列が正)。
- **習得済み**(`srs.next_review_at === null`。段階 5 を good で通過): テキスト「習得済み — 復習キューから外れています」、ボタンはセカンダリ「復習に戻す」1 個のみ(→ `POST …/review { result: 'again' }` = 段階 1・翌日。docs/11 §7.1)。

### 5.7 詳細パネルのアクション

- **「原文で見る →」**: `router.push('/papers/{source.library_item_id}?mode=source&block={anchor.block_id}')`(viewer-shell §3.1 の URL 契約・1h 計画 §5.6 の根拠チップと同一規則)。ビューアが該当ブロックへスクロールし一時ハイライトする。
- **「✓ 覚えた」/「まだあやしい」**: `POST /api/vocab/{id}/review`。成功でフッタ表示を `next_review_display` に更新し、`counts.due` を再取得(§2.2)。mutation 中は両ボタン `disabled`(opacity:0.5)。楽観更新はしない(SRS 計算はサーバーが正。決定)。
- **フィールド編集**(docs/11 §4「すべてユーザー編集可能」): 編集可能 5 セクション(1・3・4・5・6)は、セクションホバー時に見出し右端へリンク「編集」(10px/600、color `var(--pr-a)`)を表示(決定: デザイン未描画のためバッジ「編集可」の含意を最小 UI で実装)。クリックで本文が textarea に切替 — width:100%、min-height:64px、padding:8px 10px、border:1px solid `#DDD9CF`、border-radius:6px、font:本文と同サイズ、値は Markdown サブセット生文字列。下に右寄せボタン行(gap:6px): 「キャンセル」(h24、枠付きセカンダリ様式)/「保存」(h24、プライマリ様式)→ `PATCH /api/vocab/{id}` の `ai.{fieldKey}`(`context_meaning` は long のみ編集し、short は既存値を維持して送る)。保存成功でサーバーが `edited_fields` に追加し、以後の再生成で上書きされない(03 §11.6)。Esc=キャンセル。
- **見出し情報の編集**(term / ipa / pos_label / kind): ⋯ メニュー(§5.8)の「見出しを編集」から `Modal`(width:460、labelledBy="vocab-edit-title")。フォーム 4 行 — 「見出し語」(text input)、「発音記号(IPA)」(text input、mono)、「分類ラベル」(text input、例: 句動詞)、「種別」(`SegmentedControl` 3 値: 単語/コロケーション/イディオム)。フッタ「キャンセル」/「保存」→ `PATCH /api/vocab/{id}`。決定: ヘッダ直編集ではなくモーダルに集約(ヘッダの視覚をデザインから変えないため)。

### 5.8 ⋯ オーバーフローメニュー(docs/11 §6.3。決定)

`Popover`(width:180、caret なし、placement `bottom-end`)内にメニュー項目(各 padding:8px 12px、11.5px、color `--pr-text-mid`、ホバー背景 `--pr-bg-hover`):

1. 「見出しを編集」→ §5.7 のモーダル。
2. 「AI 生成をやり直す」→ `POST /api/vocab/{id}/regenerate`(fields 省略)。`edited_fields` は常にスキップされる(03 §11.6)。実行後 Toast `{ kind: 'info', message: '再生成をはじめました' }`。
3. 区切り線(1px `--pr-border-hair`)。
4. 「削除」(color `var(--pr-warn)`)→ 即座に一覧から当該行を除去(楽観)+ Toast `{ kind: 'info', message: '「{term}」を削除しました', action: { label: '元に戻す' } }`(表示 6,000ms)。トースト消滅時に `DELETE /api/vocab/{id}` を発行、「元に戻す」で発行をキャンセルして行を復元(フロントエンドの遅延実行。plans/02 §1 の決定)。トースト存続中に画面遷移・アンマウントが発生した場合は即時 `DELETE` を発行して確定する(決定: 遅延削除を黙って失わない)。削除確定後は隣接行(下、無ければ上)を自動選択。最終行削除で 0 件になった場合は §5.4 の空状態表示(`/vocab` へ replace)。

### 5.9 復習セッション(`VocabReviewModal`。docs/11 §7.2。デザイン未描画 — 本書で確定)

- **起動**: 「復習をはじめる」クリック → `GET /api/vocab/review-queue` → `useVocabReviewStore.start(items)`。`counts.due === 0` のときボタンは disabled(opacity:0.5、バッジ非表示、`title="復習期の語彙はありません"`。決定)。取得失敗時は Toast `{ kind: 'error', message: '復習キューを取得できませんでした' }` を出しモーダルは開かない(決定)。`items` が空(件数の陳腐化)の場合はモーダルを開かず Toast `{ kind: 'info', message: '復習期の語彙はありません' }` + `invalidateQueries({ queryKey: ['vocab'] })`(決定)。
- **器**: `Modal`(width:460、labelledBy="vocab-review-title"、dismissible=true。Esc/スクリムで途中終了 — 評価済みのみ確定、未評価はスケジュール不変。docs/11 §7.2)。
- **ヘッダ**: padding:14px 16px、border-bottom:1px solid `--pr-border-hair`。「復習」12.5px/700 + 右端に進捗「{評価済み数 + 1} / {総数}」10.5px、`--pr-text-muted`、mono。**決定(進捗の定義)**: 総数 = 起動時のキュー長(`again` の再エンキューで増やさない)。評価済み数 = `good` で消化したカード数(`again` は未消化扱いで分子に数えない)。
- **カード面(表)**: padding:18px 16px、縦 flex gap:12px。内容は詳細パネルの体裁を再利用(docs/11 §7.2「詳細パネルの既存構成を再利用」) — 見出し語行(term 16px/700 `--pr-font-en` + IPA + `VocabKindBadge detail`)+ 文脈センテンス(§4.2.6-2 と同一体裁、対象語 mark、「原文で見る →」注記は**非表示**)。語義は伏せる。下部中央にプライマリボタン「答えを見る」(h28、padding:0 16px、11.5px/600)。
- **カード面(裏)**: 表の内容+「文脈での語義」(§4.2.6-1 体裁)+「✦ 覚えるコツ」(§4.2.6-5 体裁。mnemonic が null なら省略)。フッタ(padding:11px 16px、border-top:1px `--pr-border-hair`、右寄せ gap:8px): 「まだあやしい」(セカンダリ h26)/「✓ 覚えた」(プライマリ h26)— §4.2.6 フッタと同スタイル。
- **評価**: ボタンで `POST /api/vocab/{id}/review` を非同期発行(応答を待たず次カードへ。失敗時は Toast `{ kind: 'error', message: '評価を保存できませんでした' }` を出しキュー末尾へ戻す。決定)。`again` のカードは同一セッションの末尾へ再エンキュー(docs/11 §7.1)。
- **キーボード**(決定): Space=「答えを見る」(表のみ)、`1`=まだあやしい、`2`=✓ 覚えた(裏のみ)、Esc=終了。
- **完了画面**: 全カード消化で「復習が終わりました」14px/700 + 「{総数} 語中 {good 数} 語 ✓ 覚えた」11.5px `--pr-text-sub` + プライマリ「閉じる」(h28)。**決定**: `good 数` = 各カードの**初回評価**が `good` だった件数(`results` の各 id の最初の要素で判定。一度でも `again` を付けた語は数えない)。閉じると `invalidateQueries({ queryKey: ['vocab'] })`(due 件数・フッタ表示更新)。

### 5.10 グローバル(共通シェル)

- ⌘K / Ctrl+K でグローバル検索ドロップダウン(1e 計画書の仕様)。◷ で通知ポップオーバー(4a 計画書)。アバターでユーザーメニュー(4a 計画書)。本画面固有の追加挙動なし。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Playwright `toHaveScreenshot`、ビューポート 1440×900、シードデータは §4.2.5 の 8 行+詳細「boil down to」(§4.2.6 の全セクション文言)。

- [ ] **VRT-1(デザイン再現状態)**: `/vocab/{boilDownToId}?sort=term` — ヘッダ「語彙 ↑」、1 行目選択、詳細パネル全節表示。§4 の全寸法・色・フォント・文言(サイドバー 216px / 検索 460×32 / チップ h22 / リスト grid `1.25fr 1.35fr 170px 56px` / 詳細 400px / mark rgba(196,148,50,0.30) / コツカード #FFF9F0+#EEDDB8)がデザイン HTML と一致する。既知の意図的差分は 2 点のみ: 詳細ヘッダの ⋯ ボタン(§4.2.6 の決定)とシェル完全形(§1 の決定)。
- [ ] **VRT-2(既定状態)**: `/vocab` — ヘッダ「追加 ↓」で行順同一(追加日降順)、先頭行自動選択。
- [ ] **VRT-3**: `?kind=idiom` 選択状態(チップ「イディオム 6」が elev 黒背景・白文字、行 2 件)。
- [ ] **VRT-4**: `?due=true`(「復習期」チップが背景 #8A6A24・白文字)。
- [ ] **VRT-5**: 空状態(語彙 0 件)・検索 0 件(§5.4 の文言どおり)。
- [ ] **VRT-6**: `generation: 'pending'` の詳細(✦ 生成中…+スケルトン)と `'failed'` の失敗カード。
- [ ] **VRT-7**: 復習モーダルの表・裏・完了画面(§5.9)。
- [ ] **VRT-8**: ダークモード(`data-theme="dark"`)で VRT-1 相当(トークン自動追随。琥珀系・種別バッジ色は固定値のまま)。

### 6.2 機能検証

- [ ] `/vocab` で `GET /api/vocab` が発行され、サイドバー「語彙帳」・見出しサブ・チップ「すべて」の件数がすべて `counts.all` で一致する(docs/11 受け入れ基準)
- [ ] チップ「復習期」の件数と「復習をはじめる」バッジがともに `counts.due` で一致し、`counts.due === 0` でボタンが disabled になる
- [ ] 種別チップ・復習期チップ・検索・ソートが URL クエリ(`kind` / `due` / `q` / `sort`)に反映され、リロード後も同一状態が復元される
- [ ] 「語彙を検索」はクエリ `q`(語彙帳内のみ)を叩き、グローバル検索(⌘K)と混ざらない
- [ ] 行クリック / ↑↓ キーで `/vocab/{id}` へ replace され、`GET /api/vocab/{id}` の内容が詳細パネルに表示される(選択行ハイライト)
- [ ] 「語彙」「追加」ヘッダクリックでソートが切り替わり、行順がソートキーと一致する(「文脈での語義」「出典」は不可)
- [ ] 一覧 50 件超で末尾スクロールにより `next_cursor` ページが追加取得される
- [ ] 文脈センテンスの `highlight.start/end` 範囲だけが mark でハイライトされる
- [ ] 「原文で見る →」で `/papers/{library_item_id}?mode=source&block={block_id}` へ遷移し、該当センテンスがハイライトされた状態でビューアが開く(docs/11 受け入れ基準)
- [ ] 編集可能 5 セクションの「編集」→保存で `PATCH /api/vocab/{id}` が発行され、詳細パネルに反映される(`meaning_short` は long 編集では変わらない — §5.7)。「見出しを編集」での `term` / `kind` 変更は一覧行にも反映される。編集済みフィールドは「AI 生成をやり直す」で上書きされない(`edited_fields`)
- [ ] `generation: 'pending'` 中は 2,000ms ポーリングで自動更新され、`failed` では失敗理由+「生成を再試行」が表示される(語彙・文脈・出典は消えない)
- [ ] 「✓ 覚えた」で次回表示が `next_review_display` に更新され、段階 5 通過で「習得済み — 復習キューから外れています」+「復習に戻す」になる。「まだあやしい」で「次の復習: 明日」系表示に戻る(docs/11 §7.1 の固定段階 1/3/7/14/30 日)
- [ ] 復習セッション: 表(語義伏せ)→「答えを見る」→ 裏 → 評価で次カードへ。`again` は同一セッション末尾に再出題。Esc 途中終了で評価済みのみ確定・未評価は不変。完了画面後に due 件数が減る
- [ ] ⋯ メニュー「削除」→ トースト「元に戻す」6 秒以内で復元でき、無操作で `DELETE /api/vocab/{id}` が発行される。削除後は隣接行が自動選択される
- [ ] 存在しない `/vocab/{id}` はエラートースト+`/vocab` へ復帰する
- [ ] 未認証アクセスは `/login?next=/vocab` へリダイレクトされる

## 付記: 本書で新たに確定した実装決定の一覧

1. ルート `/vocab` + オプショナルキャッチオール `[[...vocabId]]`(選択エントリをパスで表現、フィルタ/検索/ソートはクエリ)。`/vocab` では先頭エントリを URL 書き換えなしで導出選択し、選択変更の replace は常に現在のクエリを維持する(§1・§5.2)。
2. 既定ソート=追加日降順(ヘッダ「追加 ↓」)。デザインの「語彙 ↑」は `sort=term` 状態として再現し、行順は常にソートキーに一致させる。昇降トグルなし(API が方向固定)。
3. 種別チップは排他単一選択、「復習期」は独立トグル(ON=背景 #8A6A24・白文字)。
4. AI 生成の進行検知は SSE ではなく詳細 GET の 2,000ms ポーリング(`pending` の間のみ)。
5. 詳細ヘッダに ⋯ オーバーフローメニューを追加(docs/11 §6.3 の削除・再生成・見出し編集。デザイン未描画の必須機能)。削除はトースト遅延実行 6,000ms。
6. フィールド編集はセクションホバーの「編集」リンク → インライン textarea → PATCH。見出し情報(term/ipa/pos_label/kind)は ⋯ →「見出しを編集」モーダル(width 460)。
7. 生成テキストは Markdown サブセット(`**` / `*`)で保存し表示時に変換(HTML 非保存)。
8. 復習セッションは Modal(width 460)のフラッシュカード: 表=見出し語+文脈センテンス、裏=+語義+コツ、キーボード Space/1/2/Esc、`again` は末尾再エンキュー、評価は非同期発行。
9. 空・ローディング・エラー・ホバー・フォーカスの各状態文言とスケルトン形状(§5.4)。日時・SRS 表示書式(§5.6)。習得済みフッタは「復習に戻す」1 ボタン。
10. 詳細メタ行は `source.display` の「 · 」を半角スペースに置換して構成。文脈センテンスは編集不可。詳細本文は overflow-y:auto。
