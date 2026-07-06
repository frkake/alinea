# 画面 4e: 横断検索結果(ライブラリ横断検索 — 全結果)

> 対象読者と前提: 本書は「訳読 / YAKUDOKU — 論文読解ワークベンチ」の apps/web(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4)実装者向けに、確定デザイン画面 4e(ライブラリ横断検索の全結果画面。S5「想起」)をピクセル一致で実装するための完全仕様である。ピクセル値・UI 文言は抽出ファイル extract/4e.md を正、機能仕様は docs/06 §9(横断検索)を正とする。共通コンポーネント名は plans/08-design-system.md、API 名・型は plans/03-api.md、トークンは packages/tokens(plans/08 §2)の識別子をそのまま使う。本書に無い選択肢は存在しない(すべて確定済み)。

## 1. 概要とルート

- **ルートパス(確定)**: `/search`(App Router: `apps/web/src/app/(app)/search/page.tsx`)。
  - **URL クエリで全状態を表現する**(plans/03 §15.1 のクエリ語彙に対応):
    | クエリ | 値 | 既定(URL から省略) |
    |---|---|---|
    | `q` | 検索語(1〜200 字) | なし(必須。無い場合は §5.4 の未入力状態) |
    | `source` | `all` \| `body` \| `notes` \| `chat` \| `article` | `all` |
    | `paper` | `library_item_id`(`li_…`。「論文で絞る」) | なし(全論文) |
    | `sort` | `relevance` \| `recency` | `relevance` |
  - 例: `/search?q=EMA%20teacher&source=body&sort=relevance`。画面 4e は `/search?q=EMA+teacher`(source=all、sort=relevance)の状態である。
  - **URL 書き換えは `router.replace`**(ファセット・ソート変更。履歴を汚さない)。ただし **`q` の変更(Enter による再検索)のみ `router.push`**(検索語単位で戻れるようにする。決定: 1e ドロップダウンの「すべての結果を表示 →」も `router.push` で本ルートへ来るため、検索語の変遷が履歴になる)。クエリ⇄型付きオブジェクトの変換は専用フック `useSearchQueryState()`(`apps/web/src/features/search/useSearchQueryState.ts`)に一元化する。
- **認証**: 必須(plans/03 の `session` 区分)。未ログインは `(app)` グループの layout が `/login?next=/search%3Fq%3D…` へ `redirect()`。
- **画面の役割**: ライブラリ全体(本文·原文 / 本文·訳文 / メモ・注釈 / チャット履歴 / 記事)を横断検索した全結果を、ヒット源バッジで源を明示しながら論文単位グループで一覧し、クリック 1 回で源ごとの該当位置(ビューアのアンカー / チャットスレッド / メモ / 記事モードのセクション)へジャンプさせる。日英クロス検索(日本語クエリ→訳文、英語クエリ→原文)に対応する。
- **共通シェル**: トップバーは 1d/1e/4a/4d と共有の `LibraryShell`(`apps/web/src/components/shell/LibraryShell.tsx`)を使う。**決定: `LibraryShell` に `sidebar?: React.ReactNode` prop(既定 = `SidebarNav`)を追加し、4e はアプリナビの代わりに画面固有の `SearchFacetRail` を渡す**。理由: 抽出 4e の左レール(w216)はファセット(ヒット源/論文で絞る)であり、ホーム/ライブラリ等のナビ項目は描かれていない(抽出が正)。plans/08 §5.14 の「使用画面: …4e」は幅 216px・面色 `--pr-bg-pane` という様式の共有を指すものとし、コンポーネントとしての `SidebarNav` は 4e では使わない(plans/08 への追記事項 → §7)。
- **レンダリング方式(決定)**: `page.tsx` は Client Component(`"use client"`)+ TanStack Query による CSR。サーバープリフェッチは行わない(v1)。理由: 1e と同一方針(セッション依存データ+スケルトンで初期体感を担保)。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 用途 | 取得タイミング |
|---|---|---|---|
| 1 | `GET /api/search`(§15.1) | 結果グループ・ヒット・ファセット件数・サマリ(`q`/`source`/`library_item_id`/`sort`/`cursor`/`limit=10`) | `q` 確定時(初期表示・Enter 再検索)+ファセット/ソート変更時+スクロール末尾(cursor) |
| 2 | `GET /api/auth/me`(§2.6) | アバターイニシャル「YK」・通知未読ドット | シェルのマウント時(LibraryShell 側) |
| 3 | `GET /api/notifications`(§16.1) | ◷ ボタンのポップオーバー(4a 仕様。本書では配置のみ) | ◷ クリック時 |

- URL クエリ `paper` は API パラメータ `library_item_id` に転写する。`limit` は既定の 10(グループ数)を明示送信する。
- **リアルタイム更新(決定): なし**。SSE・ポーリングとも使わない。検索結果はリクエスト時点のスナップショットで十分であり(S5 想起はユーザー起点)、`staleTime: 30_000`(30 秒)+ウィンドウ再フォーカス時の再取得は `refetchOnWindowFocus: false` とする(スクロール位置とハイライトの安定を優先。決定)。

### 2.2 TanStack Query キー設計

横断検索のキーは `apps/web/src/features/search/queries.ts` に集約する(1e の `searchKeys.preview` もここへ移設し、`features/library/queries.ts` からは re-export する。決定 — 検索機能のキーを 1 ファイルに保つため)。

```ts
// apps/web/src/features/search/queries.ts
export type SearchResultsParams = {
  q: string;
  source: 'all' | 'body' | 'notes' | 'chat' | 'article';
  paper: string | null;          // library_item_id
  sort: 'relevance' | 'recency';
};

export const searchKeys = {
  preview: (q: string) => ['search', 'preview', q] as const,          // 1e ドロップダウン
  results: (params: SearchResultsParams) => ['search', 'results', params] as const, // 4e(無限クエリ)
} as const;
```

- 結果一覧は `useInfiniteQuery`(`getNextPageParam: (last) => last.next_cursor`)。`placeholderData: keepPreviousData` を指定し、ファセット/ソート切替時に前結果を薄く保持する(§5.6)。
- `enabled: q.trim().length > 0`。`q` が空のときはクエリを発行しない(§5.4)。
- ファセット件数はエンドポイント #1 のレスポンス `facets` をそのまま使う(別リクエストなし)。**ファセット件数のセマンティクス(決定)**: `facets` は `q` のみを適用した全体件数(`source`/`library_item_id` フィルタ非適用)、`total`/`paper_count`(サマリ行)はフィルタ適用後の値。理由: ファセットは「他の絞り込み先の件数」を常に見せる UI であるため。API 実装(plans/11-search)への確定事項。

### 2.3 型(plans/03 §15.1 のレスポンス型をそのまま使用)

`packages/api-client` 生成型 `SearchResponse` / `SearchHit` / `LibraryItemSummary` / `AnchorRef` を使う。本書が必要とする追加フィールド(`groups[].article_context`・`SearchHit.paired_translation`)は §7 の plans/03 追記事項として確定する。

## 3. コンポーネント分解

```
■ SearchPage(apps/web/src/app/(app)/search/page.tsx。"use client")
└─ LibraryShell                                  共有シェル(1e §1)
   ├─ (トップバー) ロゴ / SearchBox(variant='global') / NotificationButton ◷ / アバター
   ├─ sidebar = SearchFacetRail                  固有
   │   ├─ FacetSectionHeading(見出し)            固有(内部片)
   │   ├─ FacetItem × 5(ヒット源)+ × N(論文)   固有(件数は CountBadge variant='nav')
   │   └─ (フッター説明文)                        固有(内部片)
   └─ SearchResultsColumn                        固有
      ├─ SearchSummaryBar                        固有
      │   └─ SortSelect(Popover §5.10 使用)      固有
      ├─ SearchGroupCard × N                     固有(Card §5.9 の面仕様)
      │   ├─ (グループヘッダ) サムネ / タイトル / メタ / StatusPill(§5.2)/ 件数
      │   └─ SearchHitRow × n                    固有
      │       ├─ SourceBadge(§5.22)              共通
      │       ├─ (スニペット。<mark class="yk-search-hit">)
      │       └─ (メタ行+ジャンプリンク)
      ├─ SearchResultsSkeleton                   固有(§5.5)
      ├─ EmptyState(§5.21)                       共通(0件・未入力・エラー)
      └─ (無限スクロール sentinel <div>)
```

共通コンポーネント(plans/08 §5 の props を変更なしで使用): `SearchBox` / `SourceBadge` / `StatusPill` / `CountBadge` / `Popover` / `Card` / `EmptyState`。

画面固有コンポーネント(配置: `apps/web/src/features/search/components/`)の props 型:

```ts
// SearchFacetRail.tsx
interface SearchFacetRailProps {
  source: SearchResultsParams['source'];
  paper: string | null;
  facets: SearchResponse['facets'] | null;   // ローディング中・エラー時は null(§5.5)
  isError: boolean;                          // true かつ facets null → 件数もパルスバーも出さない(§5.4)
  onSourceChange: (s: SearchResultsParams['source']) => void;
  onPaperChange: (libraryItemId: string | null) => void; // 選択中を再クリックで null(解除)
}

// SearchResultsColumn.tsx
interface SearchResultsColumnProps {
  params: SearchResultsParams;
  query: UseInfiniteQueryResult<InfiniteData<SearchResponse>>;
  onSortChange: (s: 'relevance' | 'recency') => void;
}

// SearchSummaryBar.tsx
interface SearchSummaryBarProps {
  q: string;
  total: number;
  paperCount: number;
  sort: 'relevance' | 'recency';
  onSortChange: (s: 'relevance' | 'recency') => void;
}

// SearchGroupCard.tsx
interface SearchGroupCardProps {
  group: SearchResponse['groups'][number];   // { library_item, article_context, hits }
  q: string;                                 // 再検索リンク等には使わない。カードルート要素の data-search-q 属性にのみ付与(E2E/VRT の検証用。決定)
}

// SearchHitRow.tsx
interface SearchHitRowProps {
  hit: SearchHit;
  lang: 'en' | 'ja';                 // 描画行の言語(paired_translation 展開後。§4.5)
  variantLabel: string;              // SourceBadge に渡す表示文言(§4.5 の写像)
  href: string;                      // §5.3 の遷移先 URL
}
```

## 4. レイアウト・スタイル完全仕様

extract/4e.md の全値を正として転記する(トークン名は plans/08 §2 の対応値)。デザインの「フレーム」(width:1440px、height:860px — 注: この画面のみ 900 ではなく 860、border 1px `--pr-border-frame`(#D6D3C9)、border-radius:10px、box-shadow 0 20px 44px rgba(28,30,34,0.12))はデザインキャンバス上の表現であり、実アプリではビューポート全面に描画する(plans/08 §7.1)。縦はフルード(トップバー 52px 固定+本体 `flex:1; min-height:0`)。

### 4.1 ルート面とレイアウト構造

ルート面: `background: var(--pr-bg-app-alt)`(#F4F3EF)、`color: var(--pr-text)`(#1E2227)、縦 flex、overflow:hidden。

```
┌──────────────────────────────────────────────────────────────┐
│ トップバー h=52px 白 #FFFFFF / 下線 #E6E3DA                    │
│ [訳|訳読 w=198] [検索ボックス w=460 フォーカス状態] …[◷][YK]  │
├───────────┬──────────────────────────────────────────────────┤
│ ファセット │ 結果カラム flex:1 padding:16px 26px               │
│ w=216px   │ ├ 結果サマリ行(件数・並び順)                     │
│ #F7F6F2   │ └ 縦スクロール結果リスト gap:14px                 │
│ 右線      │    ├ グループ1: Consistency Models(3ヒット)      │
│ #E7E4DB   │    ├ グループ2: Progressive Distillation(2ヒット) │
│           │    └ グループ3: 記事: Rectified Flow を読む(1)    │
│ 下端に    │                                                   │
│ クロス検索│                                                   │
│ の説明文  │                                                   │
└───────────┴──────────────────────────────────────────────────┘
```

- トップバー: height:52px、flex:none、`background: var(--pr-bg-card)`(#FFFFFF)、`border-bottom: 1px solid var(--pr-border-header)`(#E6E3DA)、display:flex、align-items:center、gap:14px、padding:0 18px。
- 本体: flex:1、display:flex、min-height:0。
  - 左ファセットレール: width:216px、flex:none、`background: var(--pr-bg-pane)`(#F7F6F2)、`border-right: 1px solid var(--pr-border-pane)`(#E7E4DB)、padding:14px 10px、縦 flex、gap:2px、font-size:12px、`color: var(--pr-text-nav)`(#3A3E44)。
  - 右結果カラム: flex:1、min-width:0、padding:16px 26px、縦 flex、gap:12px、overflow:hidden。

### 4.2 トップバー(h52。LibraryShell)

1. **ロゴブロック**(flex、align-items:center、gap:8px、width:198px)
   - アプリマーク「訳」: inline-flex 中央、22×22px、border-radius:6px、`background: var(--pr-acc)`(#3E5C76)、color:#FFFFFF、font-size:11.5px、font-weight:700。
   - ワードマーク「訳読」: font-size:14.5px、font-weight:700、letter-spacing:0.5px。
2. **検索ボックス**: `SearchBox variant='global'`(w460px、h32px、border-radius:7px、padding:0 12px)。4e は**クエリ確定表示状態**(§5.1 状態 B): `background: var(--pr-bg-card)`(#FFFFFF)、`border: 1.5px solid var(--pr-acc)`(#3E5C76)、`box-shadow: 0 0 0 3px var(--pr-acc-s)`(rgba(62,92,118,0.10) のフォーカスリング)、font-size:12.5px、`color: var(--pr-text)`。
   - 虫眼鏡 `MagnifierIcon`(plans/08 §6): 12×12、viewBox 0 0 12 12、`color: var(--pr-text-icon)`(#8A8E94)。円 cx=5 cy=5 r=3.6 stroke currentColor 幅 1.3+柄 path `M8 8l2.6 2.6` stroke 幅 1.3 linecap:round、fill:none。
   - 入力済みクエリテキスト: 「EMA teacher」(プレーンテキスト、12.5px、#1E2227)。
   - クリアボタン「×」: margin-left:auto、font-size:11px、`color: var(--pr-text-muted)`(#9A9EA4)。⌘K キーキャップ・「esc で閉じる」ヒントはこの状態では表示しない(抽出どおり)。
3. スペーサー(flex:1)。
4. **通知ボタン ◷**(`NotificationButton`。4a 仕様): position:relative、inline-flex 中央、30×30px、border-radius:7px、`border: 1px solid var(--pr-border-control-2)`(#E2DFD5)、`color: var(--pr-text-sub)`(#5B6067)、font-size:13px。未読時は琥珀ドット(#C49432、6px — 4e 抽出では未描画=未読なし)。
5. **ユーザーアバター「YK」**: inline-flex 中央、30×30px、border-radius:50%、`background: var(--pr-acc-s)`、`color: var(--pr-acc)`、font-size:11px、font-weight:700。

### 4.3 左ファセットレール(w216。SearchFacetRail)

コンテナ: padding:14px 10px、縦 flex、gap:2px、font-size:12px、`color: var(--pr-text-nav)`(#3A3E44)。

1. セクション見出し「ヒット源」: font-size:10.5px、font-weight:600、`color: var(--pr-text-muted)`(#9A9EA4)、letter-spacing:0.4px、padding:0 10px 6px。
2. **ファセット項目(選択中)**(4e では「すべて」): flex、align-items:center、gap:8px、padding:6px 10px、border-radius:6px、`background: var(--pr-acc-s)`(rgba(62,92,118,0.10))、`color: var(--pr-acc)`(#3E5C76)、font-weight:600。ラベル span は flex:1。件数(例「12」)は `CountBadge variant='nav'`: font-size:10.5px、色は親を継承(=アクセント)。
3. **非選択項目**(共通構造: flex、align-items:center、gap:8px、padding:6px 10px。radius・背景なし。ラベル flex:1。件数 font-size:10.5px、`color: var(--pr-text-muted)`):
   - 「本文(原文・訳文)」— 6(`source=body`)
   - 「メモ・注釈」— 3(`source=notes`)
   - 「チャット履歴」— 2(`source=chat`)
   - 「記事」— 1(`source=article`)
4. セクション見出し「論文で絞る」: 見出しと同スタイル、padding:16px 10px 6px。
5. **論文フィルタ項目**(非選択項目と同構造。ラベル span に `white-space:nowrap; overflow:hidden; text-overflow:ellipsis`):
   - 「Consistency Models」— 7
   - 「Progressive Distillation」— 3
   - 「Adversarial Diffusion Dist…」— 2(三点リーダは CSS ellipsis による切り詰めで再現する。HTML に「…」を直書きしない。決定)
6. スペーサー(flex:1)。
7. **フッター説明文**: font-size:10px、`color: var(--pr-text-muted)`、line-height:1.7、padding:0 10px 4px。文言逐語: 「日本語クエリは訳文に、英語クエリは原文にヒットします(クロス検索)」。

補足(決定): ファセット項目は `<button type="button">` で実装(role 属性なし、`aria-pressed` で選択状態)。「論文で絞る」の選択中様式は「すべて」と同一(`--pr-acc-s` 地+`--pr-acc` 文字+weight 600)。デザインのサンプルデータで「Adversarial Diffusion Dist…」グループが結果リストに描かれていないのは 4 番目のグループがスクロール外にあるため、記事グループ(Rectified Flow)が論文ファセットに無いのはサンプルデータの省略とみなす — 実装では**記事ヒットもその論文の行として「論文で絞る」に計上する**。

### 4.4 結果カラムヘッダ行(SearchSummaryBar)

flex、align-items:center、gap:10px。

- サマリ: font-size:13px、`color: var(--pr-text-mid)`(#3C4046) — 「「**EMA teacher**」の結果 **12 件** · 3 論文」。「EMA teacher」(=`q` のエコーバック)と「12 件」(=`total`)を `<b>` 太字にする。書式: `「{q}」の結果 {total} 件 · {paper_count} 論文`。
- スペーサー(flex:1)。
- ソートセレクタ(SortSelect): font-size:11.5px、`color: var(--pr-text-sub)`(#5B6067) — 「並び: 関連度」+下向き三角「▾」(font-size:9px、`color: var(--pr-text-muted)`)。`<button>` で実装、クリックで Popover を開く(挙動は本書 §5.2。Popover コンポーネント自体は plans/08 §5.10)。

### 4.5 結果リスト

コンテナ: 縦 flex、gap:14px、overflow-y:auto、min-height:0、padding-bottom:8px。

#### 論文グループカード(SearchGroupCard。共通スタイル)

- カード面: `background: var(--pr-bg-card)`(#FFFFFF)、`border: 1px solid var(--pr-border-card)`(#E2DFD5)、border-radius:10px、overflow:hidden(= `Card` §5.9 の面仕様)。
- **グループヘッダ**: flex、align-items:center、gap:10px、padding:10px 16px、`border-bottom: 1px solid var(--pr-border-hair)`(#F0EDE4)、`background: var(--pr-bg-feed)`(#FCFBF8)。
  - サムネイル: 24×32px、border-radius:3px、`background: var(--pr-bg-thumb)`(#EFEDE6)、`border: 1px solid var(--pr-border-thumb)`(#E0DDD3)、flex:none。`library_item.thumbnail_url` があれば `<img>`(object-fit:cover)、null ならプレースホルダ矩形。
  - 論文タイトル: font-size:12.5px、font-weight:700(`library_item.paper.title`)。
  - 著者・会議: font-size:10.5px、`color: var(--pr-text-muted)` — 書式 `{authors_short} · {venue}`(例「Song et al. · ICML 2023」「Salimans, Ho · ICLR 2022」)。
  - ステータスピル: `StatusPill size='xs'`(§7 で plans/08 に追加。inline-flex、gap:5px、height:19px、padding:0 8px、`border: 1px solid var(--pr-border-control)`(#DDD9CF)、border-radius:999px、font-size:10px。ドット 6×6px 円、色は `STATUS_COLORS[status]` — 4e 描画は「読んだ」= `--pr-status-done` #659471)。`variant='pill'`、`interactive=false`。**記事グループには表示しない**。
  - 件数: margin-left:auto、font-size:10.5px、`color: var(--pr-text-muted)` — 書式 `{hits内の件数} 件`(例「7 件」。ヒット源/論文フィルタ適用後のグループ内件数をサーバーが返す)。
- **記事グループのヘッダ変種**(`article_context` が非 null かつ hits が全て `source==='article'` のとき): サムネイルは同プレースホルダ、タイトル= `記事: {article_context.title}`(例「記事: Rectified Flow を読む」)、メタ= `記事(自動構成) · {generated_on を日付表記(下記決定)で}`(例「記事(自動構成) · 7/06」)、ステータスピルなし、件数(例「1 件」)。
- **ヒット一覧コンテナ**: padding:6px 16px 12px、縦 flex、gap:2px(抽出のグループ3は flex 指定なしだがヒット 1 件で描画同一のため、常に flex column gap:2px で統一する。決定)。

#### ヒット行(SearchHitRow。共通スタイル)

- 行: `<a href={…}>`(ブロックリンク)、display:flex、gap:10px、padding:8px 10px、border-radius:7px。既定背景なし、**ホバー/キーボードフォーカス時 `background: var(--pr-bg-hover)`(#FAF9F5)**(抽出のグループ1・1件目の描画=この状態)。
- **ソースバッジ**: `SourceBadge size='sm'`(§7 で plans/08 に size を追加): inline-flex、align-items:center、height:16px、padding:0 6px、border-radius:3px、font-size:9px、font-weight:700、flex:none、margin-top:2px。ラベルと色の写像(決定):
  | 行の種類 | ラベル(逐語) | bg / fg |
  |---|---|---|
  | `source==='body'` 原文行(lang='en') | 「本文 · 原文」 | `--pr-src-body-bg`(rgba(62,92,118,0.10))/ `--pr-src-body-fg`(#3E5C76) |
  | `source==='body'` 訳文行(lang='ja') | 「本文 · 訳文」 | 同上 |
  | `source==='chat'` | 「チャット」 | `--pr-src-chat-bg`(rgba(110,90,126,0.14))/ `--pr-src-chat-fg`(#6E5A7E) |
  | `source==='note'` \| `'annotation'` | 「メモ」 | `--pr-src-note-bg`(rgba(101,148,113,0.16))/ `--pr-src-note-fg`(#4C7458) |
  | `source==='article'` | 「記事」 | `--pr-src-article-bg`(#F1EFE9)/ `--pr-src-article-fg`(#777B81) |
- 本文カラム: 縦 flex、gap:3px、min-width:0、flex:1。
  - **スニペット**: font-size:11.5px、line-height:1.7、`color: var(--pr-text-en)`(#33373C)。フォント(源に応じて固定切替):
    | 行 | font-family |
    |---|---|
    | 原文ヒット(`snippet_lang==='en'`) | `var(--pr-font-en)`(`'Source Serif 4', Georgia, serif`) |
    | 訳文ヒット(`snippet_lang==='ja'` かつ `source==='body'`) | `var(--pr-jp), serif`(既定 'Noto Serif JP'。data-body-font 切替に追随) |
    | チャット / メモ / 記事 | 継承(UI 既定フォント `--pr-font-ui`) |
    レンダリングは `dangerouslySetInnerHTML`(サーバーがサニタイズ済み。plans/03 §15.1)。
  - **ハイライト `<mark class="yk-search-hit">`**(plans/08 §5.17 の決定): `background: rgba(196,148,50,0.30)`(琥珀 30%)、border-radius:2px、padding:0 1px、color:inherit。
  - **メタ行**: font-size:10px、`color: var(--pr-text-muted)`(#9A9EA4)。`hit.display`(位置情報)+末尾にジャンプリンク span: `color: var(--pr-acc)`(#3E5C76)、font-weight:600、margin-left:6px。ジャンプリンク文言(逐語。`target.kind` の写像):
    | `target.kind` | 文言 |
    |---|---|
    | `viewer` | 「該当位置へ →」 |
    | `chat` | 「スレッドを開く →」 |
    | `note` | 「メモを開く →」 |
    | `article` | 「記事モードで開く →」 |
- **原文・訳文ペア行の展開**: `matched_in: ["source","translation"]` のヒット(§7 の `paired_translation` 付き)は**2 行に展開して描画**する(件数は 1 のまま)。1 行目=原文行(`snippet`、バッジ「本文 · 原文」、メタ `{display}`)、2 行目=訳文行(`paired_translation.snippet`、バッジ「本文 · 訳文」、メタ `{display}(同一ブロックの訳文 — 原文ヒットと同一視)`)。両行とも「該当位置へ →」。

#### デザイン描画のサンプルデータ(逐語。VRT・Storybook のフィクスチャに使用)

グループ1: Consistency Models(ヘッダ: Song et al. · ICML 2023 / 読んだ / 7 件)
1. ヒット1(ホバー状態描画 bg #FAF9F5): バッジ「本文 · 原文」/ スニペット(Source Serif 4): 「…the target network is an [EMA teacher] updated by θ⁻ ← μθ⁻ + (1−μ)θ, which stabilizes online distillation and avoids collapse…」/ メタ: 「§3.2 Training via Distillation · p.5」+「該当位置へ →」
2. ヒット2: バッジ「本文 · 訳文」/ スニペット(Noto Serif JP): 「…ターゲットネットワークは [EMA 教師(EMA teacher)]であり、θ⁻ ← μθ⁻ + (1−μ)θ で更新される。これはオンライン蒸留を安定化し…」/ メタ: 「§3.2(同一ブロックの訳文 — 原文ヒットと同一視)」+「該当位置へ →」
3. ヒット3: バッジ「チャット」/ スニペット: 「Q: [EMA teacher] を使うオンライン蒸留と、固定教師の offline 蒸留の違いは? — A: オンラインでは教師も学習中のモデルの移動平均で…」/ メタ: 「メインスレッド · 6/28」+「スレッドを開く →」

グループ2: Progressive Distillation for Fast Sampling of Diffusion Models(ヘッダ: Salimans, Ho · ICLR 2022 / 読んだ / 3 件)
1. ヒット1: バッジ「メモ」/ スニペット: 「[EMA teacher] の減衰 0.999 が安定。オンライン蒸留では student と同時更新のタイミングに注意(自分の実装は 10 step ごと)」/ メタ: 「メモ · 6/20 · 根拠: §2.3」+「メモを開く →」
2. ヒット2: バッジ「本文 · 原文」/ スニペット(Source Serif 4): 「…we initialize the student from the [EMA teacher] weights and halve the number of sampling steps at each iteration…」/ メタ: 「§2.3 Distillation Procedure · p.4」+「該当位置へ →」

グループ3: 記事: Rectified Flow を読む(ヘッダ: 「記事(自動構成) · 7/06」/ ステータスピルなし / 1 件)
1. ヒット1: バッジ「記事」/ スニペット: 「…蒸留(教師に [EMA teacher] を使う手法を含む)は reflow 後に適用すると効果が大きい…」/ メタ: 「「なぜ直線なのか」セクション」+「記事モードで開く →」

※ [ ] 内が `<mark class="yk-search-hit">` ハイライト部。訳文側は対訳語「EMA 教師(EMA teacher)」全体が 1 つの mark。

#### メタ行 `display` の書式(サーバー導出。plans/03 §15.1 の `display` に対応)

| 源 | 書式 | 例 |
|---|---|---|
| 本文(原文行) | `§{番号} {セクション名} · p.{ページ}` | 「§3.2 Training via Distillation · p.5」 |
| 本文(訳文行) | `§{番号}(同一ブロックの訳文 — 原文ヒットと同一視)`(接尾辞はクライアントが付与) | 「§3.2(同一ブロックの訳文 — 原文ヒットと同一視)」 |
| チャット | `{スレッド名} · {M/D}` | 「メインスレッド · 6/28」 |
| メモ | `メモ · {M/D}` +根拠アンカーがあれば ` · 根拠: {display}` | 「メモ · 6/20 · 根拠: §2.3」 |
| 記事 | `「{セクション見出し}」セクション` | 「「なぜ直線なのか」セクション」 |

**日付表記の書式(決定)**: 本画面の `{M/D}` 表記(チャット・メモの `display`、記事グループの `generated_on`)はすべて「月=先頭ゼロなし、日=2 桁ゼロ埋め」とする(例: 「6/28」「6/20」「7/06」「12/03」— 抽出 4e の「7/06」と整合)。`display` 内の日付はサーバーが同書式で導出し、`generated_on` のみクライアントが同書式でフォーマットする。

### 4.6 全 UI 文言(逐語)

- トップバー: 「訳」「訳読」「EMA teacher」(クエリ)「×」「◷」「YK」
- ファセット: 「ヒット源」「すべて / 12」「本文(原文・訳文) / 6」「メモ・注釈 / 3」「チャット履歴 / 2」「記事 / 1」「論文で絞る」「Consistency Models / 7」「Progressive Distillation / 3」「Adversarial Diffusion Dist… / 2」「日本語クエリは訳文に、英語クエリは原文にヒットします(クロス検索)」
- 結果ヘッダ: 「「EMA teacher」の結果 12 件 · 3 論文」「並び: 関連度 ▾」
- ジャンプリンク: 「該当位置へ →」「スレッドを開く →」「メモを開く →」「記事モードで開く →」
- ステータスピル: 「読んだ」
- グループ・ヒットの文言は §4.5 のサンプルデータのとおり。

## 5. 状態とインタラクション

### 5.1 検索ボックス(トップバー)

| 状態 | 挙動・様式 |
|---|---|
| A. 非フォーカス+クエリ表示 | **決定**: 4e ではクエリが URL にある間、フォーカス有無にかかわらず抽出のアクティブ様式(border 1.5px `--pr-acc`+3px リング+白地)を維持する(デザインの描画を画面既定とみなす)。×ボタン表示。⌘K キーキャップは非表示 |
| B. フォーカス中 | 同一様式。入力編集可。**4e ではプレビュードロップダウン(1e)は開かない**(決定。既に全結果画面にいるため)。Enter で `q` を確定(前後空白トリム)し `router.push('/search?q=…')` → 再検索。**決定**: トリム後 0 字での Enter は何もしない(URL 不変)。トリム後の値が URL の `q` と同一の場合は `router.push` せず `refetch()` を呼ぶ(同一 URL への push で履歴を重複させない)。Esc でブラー(値は URL の `q` に戻す) |
| C. × クリック | 入力値をローカルにクリアしフォーカス。**URL は変更しない**(Enter まで結果は前クエリのまま。決定 — 誤クリアで結果を失わない)。**決定**: × は入力値が 1 字以上のときのみ表示(クリア後は非表示) |
| D. 入力値と URL の同期 | URL の `q` 変更(戻る/進む含む)で入力値を上書き |
| E. 入力長上限 | **決定**: `<input maxLength={200}>`(plans/03 §15.1 の `q` 上限 200 字。1e §5 と同じ決定)。クライアントで 201 字以上は入力できないため 422 は通常発生しない |

### 5.2 ファセット・ソート

- **ヒット源ファセット**: 単一選択。クリックで `source` を `router.replace`。「すべて」= `source` パラメータ削除。選択中項目の再クリックは何もしない(「すべて」で解除する)。
- **論文ファセット**: 単一選択トグル。クリックで `paper={library_item_id}`、**選択中の項目を再クリックすると解除**(`paper` 削除)。
- **SortSelect**: クリックで `Popover`(width:180、placement:'bottom-end'、caret:true)を開く。項目 2 行(決定 — StatusPill の 6 値ドロップダウンと同様式): 各行 padding:7px 12px、font-size:11.5px、`color: var(--pr-text-mid)`、ホバー bg `--pr-bg-hover`。選択中行は `color: var(--pr-acc)`・font-weight:600・行頭「✓ 」。選択肢(決定): 「関連度」(`relevance`)/「新しい順」(`recency`)。選択で `sort` を `router.replace` し閉じる。
- ファセット/ソート変更時は結果リストのスクロール位置を先頭へ戻す(決定)。

### 5.3 ヒット行のジャンプ(源別遷移先 URL。決定)

`target` から `href` を導出するヘルパー `hrefForSearchTarget()`(`apps/web/src/features/search/hrefForSearchTarget.ts`)を定義する:

| `target.kind` | href |
|---|---|
| `viewer`(原文行) | `/papers/{library_item_id}?mode=source&block={anchor.block_id}` |
| `viewer`(訳文行) | `/papers/{library_item_id}?mode=translation&block={anchor.block_id}` |
| `chat` | `/papers/{library_item_id}?panel=chat&thread={thread_id}&message={message_id}` |
| `note` | `/papers/{library_item_id}?panel=notes&note={note_id}` |
| `article` | `/papers/{library_item_id}?mode=article&block={article_block_id}` |

- 決定(原文/訳文でモードを分ける理由): ヒットした言語の本文でハイライト語を再現できる表示モードへ遷移する(P1 忠実性)。
- ビューア側は `?block=` / `?message=` / `?note=` で該当要素へスクロールし、根拠チップジャンプと同一の一時強調(2000ms)を行う(ビューア計画書への追記事項 → §7)。
- 行全体が `<a>`。修飾クリック(⌘/Ctrl+クリック)で新規タブが開けること。行内のジャンプリンク span は装飾であり、個別の `<a>` にしない(決定 — 入れ子リンク禁止)。

### 5.4 結果領域の状態一覧

| 状態 | 条件 | 描画(決定) |
|---|---|---|
| 未入力 | `q` がトリム後 0 字(`/search` 直アクセス、`q=%20` 等の空白のみを含む。決定) | `EmptyState`: タイトル「検索語を入力してください」/ 説明「⌘K でライブラリ全体を検索できます — 本文・訳文・メモ・チャット・記事」。ファセットレールは見出し 2 つ+フッター説明文のみ(項目なし)。サマリ行非表示 |
| ローディング(初回) | `isPending` | §5.5 スケルトン。ファセット件数は非表示(ラベルのみ、件数の代わりに幅 14×10px のパルスバー) |
| 結果あり | `total > 0` | §4 の描画 |
| 0 件 | `total === 0` | `EmptyState`: タイトル「「{q}」に一致する結果はありません」/ 説明「別の言い回しを試してください。日本語クエリは訳文に、英語クエリは原文にヒットします(クロス検索)」。**決定**: ファセットはレスポンス `facets` の値をそのまま表示する(§2.2 のセマンティクスにより `facets` は `q` のみ適用の件数。フィルタ起因の 0 件時は他ファセットの非 0 件数が絞り込み解除の導線になる。`q` 自体が 0 件なら全ファセット 0) |
| エラー | `isError` | `EmptyState`: タイトル「検索に失敗しました」/ 説明「通信状態を確認してもう一度お試しください」/ アクション「再試行」(`refetch()`)。**決定**: ファセットはラベルのみ表示(件数もパルスバーも出さない。`SearchFacetRail` に `isError: boolean` prop を追加して区別)。サマリ行非表示 |
| 追加ページ取得中 | `isFetchingNextPage` | リスト末尾にグループカードスケルトン 1 枚(§5.5 と同形状) |
| フィルタ切替中 | `isPlaceholderData` | 前結果を `opacity:0.55` で保持(pointer-events:none)。新結果到着で差し替え |

### 5.5 スケルトン(SearchResultsSkeleton。決定)

- サマリ行: バー 1 本(width:260px、height:13px、border-radius:3px、`background: var(--pr-bg-muted)`)。
- グループカードスケルトン ×2: カード面は実描画(§4.5 と同 border/radius)。ヘッダ: サムネ形状 24×32px+バー 2 本(width 45% × height 11px / width 25% × height 9px、radius 3px、`background: var(--pr-bg-muted)`)。ヒット行 ×3: バッジ形状 48×16px(radius 3px)+バー 2 本(width 100% × height 11px / width 55% × height 9px)。
- 全体に `animation: yk-pulse 1.2s ease-in-out infinite`(opacity 1→0.55→1。1e §5.2 と同一 keyframes)。

### 5.6 無限スクロール

- 結果リスト末尾に高さ 1px の sentinel `<div>` を置き、`IntersectionObserver`(root=結果リスト、rootMargin:'300px')で可視化したら `fetchNextPage()`。`next_cursor: null` で sentinel を外す。
- 末端表示(決定): 全件読み込み後は何も表示しない(デザインに終端表示は存在しないため付けない)。

### 5.7 ホバー・フォーカス

- ヒット行: ホバーで `background: var(--pr-bg-hover)`(#FAF9F5)。`focus-visible` 時は同背景+共通アウトライン(`outline: 1.5px solid var(--pr-acc); outline-offset: 1px`。plans/08 §5 共通規約)。transition なし(デザインに存在しないため。決定)。
- ファセット項目・SortSelect・×・◷: `focus-visible` 共通アウトラインのみ。ファセット非選択項目のホバー(決定): `background: var(--pr-bg-hover)`、border-radius:6px。
- キーボード(決定): Tab 順=検索ボックス → ×(表示時のみ)→ ◷ → アバター → ファセット項目(上から)→ ソート → ヒット行(グループ順)。DOM 順のまま(`tabindex` 指定なし)。矢印キーによる独自ナビゲーションは実装しない(v2)。

### 5.8 タイトル・計測

- `document.title`(決定): `「{q}」の検索結果 — 訳読`。未入力時は `検索 — 訳読`。
- 読書時間計測・SSE・通知ポーリングはこの画面では行わない。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Playwright + MSW フィクスチャ(§4.5 のサンプルデータ逐語)で 1440×860 スクリーンショットを撮り、extract/4e.md の描画と一致すること:

- [ ] **VRT-4e-A(4e 完全再現)**: `/search?q=EMA+teacher`。トップバー(検索ボックス アクティブ様式+「EMA teacher」+×)、ファセット(「すべて」選択中、5+3 項目+件数、フッター説明文)、サマリ「「EMA teacher」の結果 12 件 · 3 論文」+「並び: 関連度 ▾」、グループ 3 枚(ヒット 3/2/1 件、グループ1の 1 件目をホバー状態で撮影 bg #FAF9F5)。
- [ ] ソースバッジ 4 色: 本文=rgba(62,92,118,0.10)/#3E5C76、チャット=rgba(110,90,126,0.14)/#6E5A7E、メモ=rgba(101,148,113,0.16)/#4C7458、記事=#F1EFE9/#777B81。h16px・font 9px/700。
- [ ] `<mark class="yk-search-hit">` が rgba(196,148,50,0.30)・radius 2px・padding 0 1px。訳文行では「EMA 教師(EMA teacher)」全体が 1 マーク。
- [ ] スニペットフォント: 原文行=Source Serif 4、訳文行=Noto Serif JP(`--pr-jp`)、チャット/メモ/記事=UI フォント。11.5px/1.7。
- [ ] グループヘッダ: サムネ 24×32px、タイトル 12.5px/700、メタ 10.5px #9A9EA4、「読んだ」ピル h19px+緑ドット #659471(記事グループには無し)、右端「n 件」。
- [ ] ファセットレール w216px・#F7F6F2・右線 #E7E4DB。選択中項目 `--pr-acc-s` 地+`--pr-acc` 文字+600。論文名の ellipsis(「Adversarial Diffusion Dist…」)。
- [ ] **VRT-4e-B(状態)**: スケルトン(§5.5)/ 0 件 / 未入力 / エラーの各ストーリーが存在し形状が §5.4・§5.5 のとおり。

### 6.2 機能検証

- [ ] `/search?q=…` 未認証アクセスが `/login?next=…` へリダイレクトされる。
- [ ] `GET /api/search` が `q`/`source`/`library_item_id`/`sort`/`limit=10` を URL 状態から正しく転写して 1 回だけ発行される(ファセット・ソート変更ごとに再発行、`q` 空では発行されない)。
- [ ] ヒット源ファセットのクリックで `source` が `router.replace` され件数・結果が更新される。「すべて」で解除。論文ファセットは再クリックで解除される。
- [ ] `matched_in: ["source","translation"]` のヒットが原文行+訳文行の 2 行に展開され、訳文行メタに「(同一ブロックの訳文 — 原文ヒットと同一視)」が付き、総件数は 1 として数えられる。
- [ ] 源別遷移: 原文行→`?mode=source&block=`、訳文行→`?mode=translation&block=`、チャット→`?panel=chat&thread=&message=`、メモ→`?panel=notes&note=`、記事→`?mode=article&block=` へ遷移し、ビューアで該当要素が一時強調される。⌘クリックで新規タブ。
- [ ] ソートドロップダウン(関連度/新しい順)の切替で `sort=recency` が反映され、選択中に ✓ が付く。
- [ ] 無限スクロール: 末尾到達で `cursor` 付き追加取得、`next_cursor: null` 以降は発行されない。
- [ ] 検索ボックス: × でローカルクリア(URL 不変)、Enter で `router.push` 再検索、ブラウザバックで前クエリの結果に戻る。
- [ ] 日英クロス: 英語クエリのフィクスチャで訳文行がヒット表示される(MSW 上の契約テスト)。
- [ ] a11y: ファセット `aria-pressed`、ヒット行が `<a>` で入れ子リンクなし、`focus-visible` アウトライン、`document.title` が §5.8 のとおり。

## 7. 他計画書への追記事項(本書で確定した差分)

1. **plans/03 §15.1**: レスポンスに以下を追加する(API 実装前に反映必須):
   - `groups[].article_context: { title: string; generated_on: string } | null` — グループ内に `source==='article'` のヒットを含む場合のみ非 null(記事タイトル・生成日)。ヒットが記事のみのグループは 4e で記事グループ表示になる。
   - `SearchHit.paired_translation: { snippet: string } | null` — `matched_in` が `["source","translation"]` のとき、訳文側スニペット(`<mark>` 付きサニタイズ済み HTML)。`snippet` は原文側とする。
   - ファセット件数のセマンティクス(§2.2 の決定: `facets` は `q` のみ適用、`total`/`paper_count` はフィルタ適用後)を明記。
   - `snippet` のコメント `<mark class="hit">` を **`<mark class="yk-search-hit">` に修正**する(クラス名は plans/08 §5.17 の決定 `.yk-search-hit` が正。サーバーはこのクラス名で `<mark>` を出力する)。
2. **plans/08 §5.2 StatusPill**: `size: 'xs'`(h19px、padding 0 8px、font 10px、ドット 6×6px。4e グループヘッダ)を追加。
3. **plans/08 §5.22 SourceBadge**: `size?: 'sm' | 'md'` を追加(`sm`=font 9px・4e ヒット行、`md`=font 9.5px・1e ドロップダウン。高さは両者 16px)。
4. **plans/08 §5.14 / 1e §1**: `LibraryShell` に `sidebar?: React.ReactNode`(既定 `SidebarNav`)を追加し、4e は `SearchFacetRail` を渡す(4e に SidebarNav は描画されない)。
5. **ビューア計画書(1a/1b/1h)**: クエリ `?block=` / `?thread=&message=` / `?note=` による初期スクロール+2000ms 一時強調(根拠チップジャンプと同一機構)を受け付けること。
6. **1e §2.2**: `searchKeys` を `apps/web/src/features/search/queries.ts` へ移設(`features/library/queries.ts` から re-export)。
