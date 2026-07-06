# 画面 4c: コレクション共有ページ(匿名)

> **対象読者と前提**: 本書は「訳読 / YAKUDOKU」の画面 4c(コレクション共有ページ — アカウント不要・閲覧専用・noindex)を実装するフロントエンドエンジニア向けの実装仕様である。ピクセル仕様の正は確定デザイン抽出 `extract/4c.md`、機能仕様の正は [docs/06-library.md](../../docs/06-library.md) §5 と [docs/09-nonfunctional.md](../../docs/09-nonfunctional.md) §4、API 名は [plans/03-api.md](../03-api.md) §14.1、共通コンポーネント名・トークン名は [plans/08-design-system.md](../08-design-system.md)、SSR 方式は [plans/01-architecture.md](../01-architecture.md) §3.5 に従う。技術スタックは確定済み(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4)。本画面は **v1 で唯一の公開(未認証)画面** であり、アプリの認証済みレイアウト・TanStack Query・Zustand を一切使わない。

## 1. 概要とルート

- **ルート**: `apps/web/src/app/c/[token]/page.tsx` → 公開 URL `https://yakudoku.app/c/{token}`(例 `https://yakudoku.app/c/x8Kf3qPw`)。docs/06 §4.2・plans/01 §2 の確定 URL 形式。
- **認証**: 不要(`anonymous`)。認証済みルートグループ `(app)` の外に置き、セッション確認・`GET /api/auth/me`(plans/03 §2.6)を実行しない。ログイン済みユーザーが開いた場合も同一の匿名ビューを表示する(決定: ログイン状態での出し分けはしない。理由: デザインに出し分けバリエーションが存在せず、docs/06 §5 も単一ビューを定めるため)。
- **レンダリング方式**: **SSR(React Server Component)のみ**。plans/01 §2 のとおり、apps/web で SSR するのは本画面だけ。RSC が `API_INTERNAL_URL`(compose 内部 `http://api:8000`)の匿名エンドポイントを fetch して HTML を確定させる。クライアント JS は不要(決定: 本画面のコンポーネントはすべて Server Component とし、`"use client"` を置かない。唯一の例外は Next.js が Client Component を要求する `error.tsx`(§5.4)で、これはエラー時のみ配信される。理由: 通常表示のインタラクションがリンク遷移 2 種のみで、ハイドレーションコストを 0 にできる)。
- **noindex(二重指定)**: (1) `<meta name="robots" content="noindex, nofollow">`(`generateMetadata` の `robots`)、(2) レスポンスヘッダ `X-Robots-Tag: noindex`(`apps/web/next.config.ts` の `headers()` で `source: '/c/:token'` に付与)。API レスポンス自体にも `X-Robots-Tag: noindex` が付く(plans/03 §14.1)。
- **キャッシュ**: `fetch(..., { next: { revalidate: 60 } })` + レスポンスヘッダ `Cache-Control: public, s-maxage=60, stale-while-revalidate=300`(plans/01 §2 の確定値。`next.config.ts` の `headers()` で付与)。
- **テーマ・アクセント**: 匿名閲覧者はユーザー設定を持たないため、`<html data-theme="light" data-accent="slate">` 固定(決定。理由: デザインはライト+スレートブルー `#3E5C76` のみで描かれており、共有ページにテーマ切替 UI は存在しない)。
- **画面の役割**: コレクション共有リンクの受け手(アカウント不要)に、(1) コレクションの書誌情報(タイトル・説明・共有者・更新日・本数・締切)、(2) 番号順の論文カード(書誌+✦要約+許可されたメモのみ)を閲覧専用で表示し、(3) ヘッダー CTA「訳読をはじめる」でサービス登録へ誘導する。編集・並べ替え・削除の UI は一切描画しない(docs/06 §5)。
- **OGP**: 静的画像のみ(plans/01 §2 の決定「動的 OG 画像は生成しない」)。`og:image` は `apps/web/public/og/collection-default.png` → URL `/og/collection-default.png`(1200×630px、固定 1 枚。plans/01 §2 の確定ファイル名)。`og:title`=コレクション名、`og:description`=説明文先頭 120 字、`og:site_name`=「訳読 / YAKUDOKU」(いずれも plans/01 §2。§2.5 の実装形)。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 認証 | 用途 | 呼び出しタイミング |
|---|---|---|---|---|
| 1 | `GET /api/share/collections/{token}`(03 §14.1、一覧 #100) | `anonymous` | 画面の全表示データ | RSC の初回レンダリング時にサーバー側で 1 回 |

- 呼び出し形: `fetch(`${process.env.API_INTERNAL_URL}/api/share/collections/${token}`, { next: { revalidate: 60 } })`。環境変数 `API_INTERNAL_URL`(dev: `http://localhost:8000`、prod: compose 内部 `http://api:8000`。plans/01 §8.4 の確定値)。
- **fetch ラッパ**(決定): `apps/web/src/app/c/[token]/fetch-share.ts` に `fetchShareCollection(token: string): Promise<ShareCollectionResponse | null>` を置く。契約: (1) token 形式不一致(§2.1 の事前検証)と API 404 は `null` を返す、(2) それ以外の非 2xx とネットワークエラーは throw する。`page.tsx` と `generateMetadata` の両方がこれを呼び(URL・オプションが同一のため Next.js の fetch dedupe により実リクエストは 1 回)、`page.tsx` は `null` のとき `notFound()` を呼ぶ。
- レスポンス型(plans/03 §14.1 の完全形。OpenAPI 生成クライアント `@yakudoku/api-client` の型名は `ShareCollectionResponse`):

```ts
// packages/api-client(OpenAPI 生成)より
export interface ShareCollectionResponse {
  collection: {
    name: string;                    // 「輪読会 2026-07」
    description: string | null;
    shared_by: string;               // 表示名(「YK さんが共有」)
    updated_at: string;              // ISO 8601
    deadline: string | null;         // ISO 8601 date、null = 締切なし
    item_count: number;              // 「5 本」
  };
  include_notes: boolean;
  items: {
    order: number;                   // 表示順序番号(1 始まり)
    title: string;
    authors_short: string;           // 「Liu, Gong, Liu」
    venue_year: string | null;       // 「2024」「ICLR 2023」
    arxiv_url: string | null;
    summary_3line: string[] | null;  // ✦ 要約(要素に ①②③ は含まない)
    shared_note: string | null;      // include_notes=true のときのみ非 null
  }[];
}
```

- **404 の扱い**: revoked・不存在 token はどちらも API が 404 を返す(区別しない。plans/03 §14.1)。RSC は `notFound()` を呼び、`apps/web/src/app/c/[token]/not-found.tsx` を表示する(§5.3)。
- **token の事前検証**(決定): token は 8 文字の英数 `[A-Za-z0-9]{8}`(plans/03 §13.3)なので、RSC は fetch 前に `/^[A-Za-z0-9]{8}$/` で検証し、不一致なら API を呼ばずに `notFound()` する。理由: 明らかに不正な URL で API のレート枠(120 回/分/IP。plans/03 §1.8)を消費しない。
- **429/5xx の扱い**: fetch が 404 以外のエラーの場合は throw し、`apps/web/src/app/c/[token]/error.tsx`(Client Component。Next.js の要求)を表示する(§5.4)。

### 2.2 TanStack Query のキー設計

**不使用**(決定)。本画面は Server Component のみで完結し、クライアント側のデータ取得・キャッシュ層を持たない。`QueryClientProvider` も本ルートには置かない。理由: 表示データは 1 fetch のスナップショットで完結し、クライアント再取得の要件がない。

### 2.3 リアルタイム更新

**なし**(決定)。SSE 購読・ポーリングとも行わない。共有元がコレクションを更新した場合は、`revalidate: 60` により最長 60 秒+CDN の `stale-while-revalidate: 300` の範囲で新内容に置き換わる。閲覧中のページを動的に書き換える要件はない(閲覧専用の静的ビュー)。

### 2.4 表示用整形(サーバー側ユーティリティ)

`apps/web/src/lib/format.ts` に追加(決定: タイムゾーンは `Asia/Tokyo` 固定。理由: サービスは日本語 UI・日本ユーザー前提で、匿名閲覧者のロケールを取得する手段を SSR で持たない):

```ts
/** ISO 8601 → 「2026-07-06」(JST) */
export function formatDateYmd(iso: string): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(new Date(iso));
}

/** ISO 8601 date → 「7/16」(JST、先頭ゼロなし) */
export function formatDateMd(iso: string): string {
  const d = new Date(iso + (iso.length === 10 ? 'T00:00:00+09:00' : ''));
  const f = new Intl.DateTimeFormat('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric' });
  return f.format(d).replace('月', '/').replace('日', '');
}
```

- **✦ 要約の整形**(決定): `summary_3line` の各要素は文末「。」を持つ日本語文であり(plans/03 §5.1 注記「①②③は表示側」)、4c では **①②③ を付けず、要素を空文字で連結して 1 段落として表示**する(`'✦ ' + summary_3line.join('')`)。理由: 確定デザインのカード要約は番号なしの連続文(「✦ 敵対的損失とスコア蒸留を組み合わせ、1〜4 ステップでの高品質生成を実現。SDXL Turbo の基盤技術。」)であり、①②③形式はライブラリカード 4a・ダッシュボード 1d 用の表示形。
- **数式の SSR**(決定): タイトル・要約・メモに含まれるインライン数式 `$…$` は `katex.renderToString`(`throwOnError: false`)でサーバーレンダリングする。理由: docs/09 §7「数式描画は KaTeX 相当で、サーバーサイドレンダリング可能であること(共有ページ・記事モードの初期表示)」。KaTeX CSS は本ルートのレイアウトで `import 'katex/dist/katex.min.css'` する。`$` を含まない文字列にはコストが発生しない(単純な文字列分割で `$…$` 区間のみ変換する共通関数 `renderInlineMath(text: string): ReactNode` を `apps/web/src/lib/katex-ssr.tsx` に置く)。

### 2.5 メタデータ(generateMetadata)

```ts
// apps/web/src/app/c/[token]/page.tsx 内
export async function generateMetadata({ params }: { params: Promise<{ token: string }> }): Promise<Metadata> {
  const data = await fetchShareCollection((await params).token); // §2.1 のラッパ。null = 404(Next.js が fetch を dedupe)
  if (!data) return { title: '訳読 — 共有ページ', robots: { index: false, follow: false } };
  const description =
    data.collection.description ??
    `${data.collection.shared_by} さんが共有した ${data.collection.item_count} 本の論文コレクション`;
  return {
    title: `${data.collection.name} — 訳読で共有されたコレクション`,
    description,
    robots: { index: false, follow: false },
    openGraph: {
      title: data.collection.name,                 // og:title = コレクション名(plans/01 §2)
      description: description.slice(0, 120),      // og:description = 説明文先頭 120 字(plans/01 §2)
      siteName: '訳読 / YAKUDOKU',                 // og:site_name(plans/01 §2)
      images: [{ url: '/og/collection-default.png', width: 1200, height: 630 }],
    },
  };
}
```

## 3. コンポーネント分解

```
apps/web/src/app/c/[token]/
├─ page.tsx                         … RSC。token 検証 → fetch → 描画
├─ not-found.tsx                    … 404(無効・失効リンク)ビュー(§5.3)
└─ error.tsx                        … 5xx/429 ビュー("use client"。§5.4)

page.tsx のツリー(すべて Server Component):
└─ SharePage                        … 画面固有(page.tsx 本体。全幅コンテナ)
   ├─ ShareHeader                   … 画面固有(縮退ヘッダ: ロゴ+バッジ+CTA)
   └─ <main>(中央カラム 820px)
      ├─ ShareCollectionHeader     … 画面固有(タイトル+説明+メタ行)
      │  └─ DeadlineBadge (08 §5.5, variant='chip', withLabel=true)   … 共通
      ├─ SharePaperCard × N        … 画面固有
      │  ├─ Card (08 §5.9, as='article', padding='none')              … 共通
      │  └─ SharedNoteBox          … 画面固有(「共有者のメモ」ボックス)
      ├─ EmptyState (08 §5.21)     … 共通(items が 0 件のときのみ。§5.2)
      └─ ShareFooterNote           … 画面固有(フッター注記行)
```

- 共通コンポーネント(plans/08 の名前をそのまま使用): `Card`(§5.9)/ `DeadlineBadge`(§5.5)/ `EmptyState`(§5.21)。いずれも Client 専用 API を使わないため Server Component として import 可能。
- **決定**: `DeadlineBadge` の `chip` 変種は 08 §5.5 で font 9.5px/600 だが、4c の実測は「親の 11px を継承」。`DeadlineBadge` に prop `fontSize?: 9.5 | 11`(既定 `9.5`)を追加して 4c は `fontSize={11}` で使う。画面固有の複製は作らない。決定: 08 §5.5 の `DeadlineBadgeProps` に `fontSize?: 9.5 | 11`(既定 `9.5`)を追記する(この追記は 4c 実装タスクの一部として plans/08 §5.5 に反映する。既存利用画面 4a・4b は既定値のため影響なし)。
- **決定**: 「共有者のメモ」ラベルバッジは `SourceBadge`(08 §5.22、font 9.5px)と寸法が異なる(4c 実測 font 9px)ため流用せず、`SharedNoteBox` 内に直接描画する。色はトークン `--pr-src-note-bg`(rgba(101,148,113,0.16))・`--pr-src-note-fg`(#4C7458)と完全一致するのでトークンを参照する。
- **決定**: ヘッダーのロゴマーク「訳」+ワードマーク「訳読」は共通コンポーネント化しない(認証済み画面のヘッダは各画面仕様で確定するため、4c は `ShareHeader` 内に直接描画)。

画面固有コンポーネントの props(配置: `apps/web/src/components/share/`。1ファイル1コンポーネント、named export):

```ts
// ShareHeader.tsx — 静的。props なし(CTA リンク先は内部固定)
export function ShareHeader(): JSX.Element;

// ShareCollectionHeader.tsx
interface ShareCollectionHeaderProps {
  name: string;
  description: string | null;
  sharedBy: string;          // 表示名(「YK」)
  updatedAt: string;         // ISO 8601 → formatDateYmd で整形
  itemCount: number;
  deadline: string | null;   // ISO 8601 date → formatDateMd で整形
}

// SharePaperCard.tsx
interface SharePaperCardProps {
  order: number;
  title: string;
  authorsShort: string;
  venueYear: string | null;
  arxivUrl: string | null;
  summary3line: string[] | null;
  sharedNote: string | null;
}

// SharedNoteBox.tsx
interface SharedNoteBoxProps {
  note: string;              // プレーンテキスト(one_line_note 由来。plans/03 §13.3)
}

// ShareFooterNote.tsx — 静的。props なし
export function ShareFooterNote(): JSX.Element;
```

## 4. レイアウト・スタイル完全仕様

ピクセル値の正は `extract/4c.md`。デザインフレーム(1440×780px、border 1px `#D6D3C9`、border-radius 10px、box-shadow `0 20px 44px rgba(28,30,34,0.12)`、overflow:hidden)はデザインキャンバス上の表現であり、**実アプリではフレーム装飾を除きビューポート全面に描画する**(plans/08 §7.1)。すなわちページルートは `min-height: 100vh; display: flex; flex-direction: column; background: #E3E1D9; color: var(--pr-text)`(#1E2227)とし、縦はドキュメントスクロールでフルード(デザインの `height: 780px` + `overflow: hidden` は「リストが下に続く」ことのアートボード表現。§5.6)。横は plans/08 §7.2 の最小幅規則(`body { min-width: 1200px }`)に従う。

- **決定**: 本文背景 `#E3E1D9` はトークン(08 §2.1)に存在しない 4c 固有色のため、ページローカルにリテラル指定する(Tailwind `bg-[#E3E1D9]`)。共有ページはダークテーマ非対応(§1)なのでテーマ分岐は不要。

### 4.0 デザイナー注記(フレーム外。実装対象外だが照合用に転記)

- バッジ「4c」: インラインフレックス、中央揃え、min-width:32px、height:22px、背景 #2B2E33、文字色 #FFFFFF、border-radius:6px、font-size:12px、font-weight:700、`#4c` へのアンカーリンク(text-decoration:none)。
- 太字タイトル(font-size:15px、font-weight:700、color:#1E2227)「コレクション共有ページ — 閲覧専用(アカウント不要)」。
- グレー説明文(font-size:12px、color:#777B81)「トークンURLで閲覧 / 書誌+要約+共有者が許可したメモのみ / noindex」。
- 注記行: display:flex; align-items:baseline; gap:10px; margin-bottom:12px。ルートコンテナ: `<div id="4c" data-screen-label="4c コレクション共有ページ" style="width:1440px">`。
- フレーム外・下側の追加要素: なし(この画面はフレーム 1 枚のみ)。

### 4.1 レイアウト構造

```
┌──────────────────────────────── 1440 × 780 ────────────────────────────────┐
│ ヘッダー(h:52px, 白 #FFFFFF, 下線 1px #E6E3DA, padding:0 24px)             │
│ [訳]ロゴ  訳読  [共有されたコレクション — 閲覧専用]   …spacer…              │
│                     自分のライブラリで論文を読むには  [訳読をはじめる]      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 本文(flex:1, overflow:hidden, 中央寄せ, padding-top:28px, 背景 #E3E1D9)   │
│        ┌──────────── 中央カラム width:820px, 縦flex, gap:14px ─────────┐   │
│        │ ヘッダブロック(縦flex, gap:6px)                              │   │
│        │   タイトル「輪読会 2026-07」(22px bold)                       │   │
│        │   説明文(12px グレー)                                        │   │
│        │   メタ行(11px)… YK さんが共有 · 更新 … · 5 本 · [締切 7/16]  │   │
│        │ カードリスト(縦flex, gap:10px)                               │   │
│        │   ┌ カード1(白, 角丸10, 番号①+書誌+✦要約)               │   │
│        │   ┌ カード2(同上+「共有者のメモ」ボックス付き)             │   │
│        │   ┌ カード3(番号③+書誌+✦要約)                            │   │
│        │   ┌ カード4(opacity:0.88, 要約なし。以降は overflow で切れる)│   │
│        │ フッター注記行(10.5px グレー, padding:2px 4px 20px)          │   │
│        └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

- フレーム寸法: width:1440px / height:780px(注: 他画面の 900px と異なりこの画面は 780px)。実装ではビューポート全面+縦フルード(冒頭の決定)。
- 本文エリア: `flex:1; display:flex; justify-content:center; padding-top:28px`。デザインの `overflow:hidden` は採用しない(決定。§5.6 — 実アプリではドキュメントスクロールでリスト全件を表示する)。
- 中央カラム: `width:820px; display:flex; flex-direction:column; gap:14px`。
- デザイン上の注意: コレクションは「5 本」だがマークアップ上カードは 4 枚のみで、4 枚目は opacity:0.88・要約行なし・下端切れ=「下方向にコンテンツが続く」ことのアートボード表現。**実装では全 N 件を等しく opacity:1 で描画する**(§5.6 の決定)。

### 4.2 ヘッダーバー(`ShareHeader`)

- コンテナ: `height:52px; flex:none; background:var(--pr-bg-card)`(#FFFFFF)`; border-bottom:1px solid var(--pr-border-header)`(#E6E3DA)`; display:flex; align-items:center; gap:10px; padding:0 24px`。
- 子要素(左→右):
  1. ロゴマーク「訳」: inline-flex 中央揃え、22×22px、border-radius:6px、背景 `var(--pr-acc)`(#3E5C76)、文字 #FFFFFF、font-size:11.5px、font-weight:700。
  2. ワードマーク「訳読」: font-size:14.5px; font-weight:700; letter-spacing:0.5px。
  3. モードバッジ「共有されたコレクション — 閲覧専用」: inline-flex、height:19px、padding:0 8px、border-radius:4px、背景 `var(--pr-bg-inset)`(#F1EFE9)、文字色 `var(--pr-text-sub2)`(#777B81)、font-size:10.5px、font-weight:600。
  4. スペーサー: `div` flex:1。
  5. 誘導テキスト「自分のライブラリで論文を読むには」: font-size:11.5px; color:`var(--pr-text-sub)`(#5B6067)。
  6. CTA ボタン「訳読をはじめる」: `next/link` の `<Link href={`/login?next=${encodeURIComponent(`/c/${token}`)}`}>`。inline-flex、height:28px、padding:0 13px、border-radius:6px、border:1px solid `var(--pr-acc-m)`(rgba(62,92,118,0.32))、文字色 `var(--pr-acc)`(#3E5C76)、背景 `var(--pr-acc-s)`(rgba(62,92,118,0.10))、font-size:11.5px、font-weight:600、text-decoration:none。
     - 決定: リンク先は `/login?next=/c/{token}`(ログイン画面。`next` はログイン後の戻り先パス)。理由: v1 に独立ランディングページの画面は存在せず、未認証導線は `/login` に集約されている(plans/01 §2)。未認証リダイレクトの `?next={戻り先パス}` 方式に全画面で統一。

### 4.3 コレクションヘッダブロック(`ShareCollectionHeader`。縦 flex、gap:6px)

- タイトル: font-size:22px; font-weight:700(色は継承 `var(--pr-text)` #1E2227)。データ: `collection.name`(例「輪読会 2026-07」)。見出しレベルは `<h1>`(決定。ページ唯一の h1)。
- 説明文: font-size:12px; color:`var(--pr-text-sub)`(#5B6067); line-height:1.7。データ: `collection.description`。例文言: 「7/16(木)の輪読会で扱う候補。発表担当は各自 1 本、当日までに「読んだ」まで進めておく。」。null のとき要素ごと省略(§5.5)。
- メタ行: display:flex; align-items:center; gap:8px; font-size:11px; color:`var(--pr-text-muted)`(#9A9EA4)。
  - テキスト: 「{shared_by} さんが共有 · 更新 {formatDateYmd(updated_at)} · {item_count} 本 · 」+ 締切バッジ。例: 「YK さんが共有 · 更新 2026-07-06 · 5 本 · 」。
  - 締切バッジ「締切 7/16」= `DeadlineBadge`(variant='chip'、withLabel=true、fontSize=11、date=formatDateMd(deadline)): inline-flex、height:16px、padding:0 6px、border-radius:3px、背景 `var(--pr-warn-bg)`(rgba(176,104,79,0.14))、文字色 `var(--pr-warn)`(#A05A42)、font-weight:600、font-size は親の 11px 継承。
  - deadline が null のとき、末尾の「 · 」とバッジをまとめて省略する(§5.5)。

### 4.4 論文カード(`SharePaperCard`。共通スタイル)

- コンテナ: `Card`(08 §5.9、as='article'、padding='none')に追加クラスで `border-color: var(--pr-border-control)`(#DDD9CF)`; padding:14px 18px; display:flex; gap:14px; overflow:visible` を指定。背景 `var(--pr-bg-card)`(#FFFFFF)、border-radius:10px は Card 既定。
  - 決定: Card 既定の枠色は `var(--pr-border-card)`(#E2DFD5)だが 4c 実測は #DDD9CF(= `var(--pr-border-control)`)のため、`className` で枠色のみ上書きする(`CardProps extends React.HTMLAttributes<HTMLDivElement>` なので可能)。
- 左: 番号バッジ — inline-flex 中央揃え、22×22px、border-radius:50%(正円)、背景 `var(--pr-elev-bg)`(#26292E)、文字 #FFFFFF、font-size:11px、font-weight:700、flex:none、margin-top:2px。データ: `order`。
- 右: 本文カラム — display:flex; flex-direction:column; gap:5px; min-width:0; flex:1。
  - タイトル行: font-size:13.5px; font-weight:600; line-height:1.5。データ: `title`(インライン数式は §2.4 の KaTeX SSR)。
  - 書誌行: font-size:11px; color:`var(--pr-text-muted)`(#9A9EA4)。データ: 「{authors_short} · {venue_year} · 」+ arXiv リンク。
    - 「arXiv ↗」: `<a href={arxivUrl} target="_blank" rel="noopener noreferrer nofollow">`、color:`var(--pr-acc)`(#3E5C76)、font-weight:600、text-decoration:none(リンク表現)。「↗」はテキスト文字。
    - `venue_year` が null のとき「{authors_short} · 」+リンク、`arxiv_url` が null のときリンクと直前の「 · 」を省略(§5.5)。
  - 要約行(任意): font-size:11.5px; line-height:1.7; color:`var(--pr-text-sub)`(#5B6067)。表示は「✦ 」+ `summary_3line.join('')`(§2.4 の決定)。`summary_3line` が null のとき行ごと省略。
  - 共有者メモボックス(任意。`SharedNoteBox`): `shared_note` が非 null のときのみ。

#### デザイン収録データ(シード=ビジュアルリグレッション用。§6.1)

- カード1(番号 1): タイトル「Adversarial Diffusion Distillation」/ 書誌「Sauer, Lorenz, Blattmann, Rombach · 2024 · arXiv ↗」/ 要約「✦ 敵対的損失とスコア蒸留を組み合わせ、1〜4 ステップでの高品質生成を実現。SDXL Turbo の基盤技術。」
- カード2(番号 2、メモ付き): タイトル「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」/ 書誌「Liu, Gong, Liu · ICLR 2023 · arXiv ↗」/ 要約「✦ 直線に近い経路の ODE を最小二乗回帰で学習し、生成と転移を統一。reflow の反復で 1 ステップ生成へ。」/ メモ「reflow は蒸留の前処理として有効。§2.2 と図2 を中心に議論したい。」
- カード3(番号 3): タイトル「Consistency Models」/ 書誌「Song, Dhariwal, Chen, Sutskever · ICML 2023 · arXiv ↗」/ 要約「✦ 任意時刻から起点への写像を直接学習し、1〜数ステップ生成。蒸留・単独学習の両方に対応。」
- カード4(番号 4、デザインでは opacity:0.88・要約なし): タイトル「Progressive Distillation for Fast Sampling of Diffusion Models」/ 書誌「Salimans, Ho · ICLR 2022 · arXiv ↗」。実装では opacity:1・要約はデータどおり(§5.6)。

### 4.5 共有者メモボックス(`SharedNoteBox`)

- コンテナ: display:flex; gap:8px; background:`var(--pr-bg-hover)`(#FAF9F5); border:1px solid #EFECE3; border-radius:7px; padding:8px 11px。
  - 決定: 枠色 #EFECE3 はトークンに存在しない 4c 固有色のためリテラル指定する(最近似の `--pr-border-hair` #F0EDE4 で代用しない。抽出値をそのまま使う規則)。
- ラベルバッジ「共有者のメモ」: inline-flex、height:16px、padding:0 6px、border-radius:3px、背景 `var(--pr-src-note-bg)`(rgba(101,148,113,0.16))、文字色 `var(--pr-src-note-fg)`(#4C7458)、font-size:9px、font-weight:700、flex:none、margin-top:1px。
- メモ本文: font-size:11px; line-height:1.65; color:`var(--pr-text-mid)`(#3C4046)。データ: `shared_note`(one_line_note 由来のプレーンテキスト。Markdown 解釈はしない — 決定。理由: plans/03 §13.3 の決定により共有されるメモは `one_line_note` のみで、1g の入力 UI は素の textarea)。

### 4.6 フッター注記行(`ShareFooterNote`)

- display:flex; align-items:center; gap:8px; font-size:10.5px; color:`var(--pr-text-muted)`(#9A9EA4); padding:2px 4px 20px。
- テキスト(逐語・固定): 「このページは閲覧専用です · アカウント不要 · 検索エンジンには登録されません(noindex) · メモは共有者が許可したもののみ表示」

### 4.7 フォント

画面全体は UI フォント(IBM Plex Sans JP。packages/tokens の `--font-ui`)。論文タイトル(英語)にも個別のフォント指定はなく継承。SVG アイコンは本画面には存在しない(「↗」「✦」「·」はテキスト文字)。フォント読み込みは本ルートのレイアウトでも 08 §3.1 の Google Fonts タグを出力する(認証済みレイアウトと共通の `apps/web/src/app/layout.tsx` ルートレイアウトが担う)。

### 4.8 全 UI 文言(逐語)

ヘッダー:
- 訳
- 訳読
- 共有されたコレクション — 閲覧専用
- 自分のライブラリで論文を読むには
- 訳読をはじめる

コレクションヘッダ(シードデータ):
- 輪読会 2026-07
- 7/16(木)の輪読会で扱う候補。発表担当は各自 1 本、当日までに「読んだ」まで進めておく。
- YK さんが共有 · 更新 2026-07-06 · 5 本 ·
- 締切 7/16

カード1: 「1」「Adversarial Diffusion Distillation」「Sauer, Lorenz, Blattmann, Rombach · 2024 · 」「arXiv ↗」「✦ 敵対的損失とスコア蒸留を組み合わせ、1〜4 ステップでの高品質生成を実現。SDXL Turbo の基盤技術。」

カード2: 「2」「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」「Liu, Gong, Liu · ICLR 2023 · 」「arXiv ↗」「✦ 直線に近い経路の ODE を最小二乗回帰で学習し、生成と転移を統一。reflow の反復で 1 ステップ生成へ。」「共有者のメモ」「reflow は蒸留の前処理として有効。§2.2 と図2 を中心に議論したい。」

カード3: 「3」「Consistency Models」「Song, Dhariwal, Chen, Sutskever · ICML 2023 · 」「arXiv ↗」「✦ 任意時刻から起点への写像を直接学習し、1〜数ステップ生成。蒸留・単独学習の両方に対応。」

カード4: 「4」「Progressive Distillation for Fast Sampling of Diffusion Models」「Salimans, Ho · ICLR 2022 · 」「arXiv ↗」

フッター:
- このページは閲覧専用です · アカウント不要 · 検索エンジンには登録されません(noindex) · メモは共有者が許可したもののみ表示

### 4.9 データフィールド対応表

| 画面表示 | API フィールド(§2.1) | DB 由来(plans/02) |
|---|---|---|
| タイトル「輪読会 2026-07」 | `collection.name` | `collections.name` |
| 説明文 | `collection.description` | `collections.description` |
| 「YK さんが共有」 | `collection.shared_by` | `users.display_name` |
| 「更新 2026-07-06」 | `collection.updated_at` | `collections.updated_at` |
| 「5 本」 | `collection.item_count` | `collection_entries` の件数 |
| 「締切 7/16」 | `collection.deadline` | `collections.deadline` |
| 番号バッジ 1〜N | `items[].order` | `collection_entries.position`(順序保持) |
| 論文タイトル | `items[].title` | `papers.title` |
| 著者 | `items[].authors_short` | `papers` の書誌 |
| 「2024」「ICLR 2023」 | `items[].venue_year` | `papers` の書誌 |
| 「arXiv ↗」 | `items[].arxiv_url` | `papers` の arXiv URL |
| ✦ 要約 | `items[].summary_3line` | `papers.summary_lines`(共有資産) |
| 共有者のメモ | `items[].shared_note` | `library_items.one_line_note`(`include_notes=true` のときのみ) |
| (非表示だが前提)共有トークン | URL パス `{token}` | `collection_share_tokens.token`(`status='active'`) |
| (ページ属性)noindex | — | 共有ページ属性(docs/09 §4) |

個人資産(進捗・注釈・リソース・読書統計)は API レスポンスに含まれず、画面にも一切表示しない(docs/09 §4)。

## 5. 状態とインタラクション

### 5.1 通常状態(デザイン描画済み)

- 表示のみ。カード自体にホバー・選択などの操作表現はなし(閲覧専用)。編集・削除・並べ替えの UI は一切存在しない。
- メモありカード(カード2 相当)= `shared_note` 非 null のときのみ `SharedNoteBox` を描画。
- 締切バッジ = `deadline` 非 null のときのみ描画(暖色 `var(--pr-warn-bg)` / `var(--pr-warn)`)。
- ヘッダーの「共有されたコレクション — 閲覧専用」バッジは常時表示(未ログイン閲覧モードの状態表示)。

### 5.2 空状態(デザイン未描画 → 決定)

- `items.length === 0` のとき、カードリスト位置に `EmptyState`(08 §5.21)を表示する。
  - title: 「このコレクションにはまだ論文がありません」(font 12.5px/600、color `var(--pr-text-sub2)`)
  - description: 「共有者が論文を追加すると、ここに表示されます。」(font 11px、color `var(--pr-text-muted)`)
  - action: なし。
- コレクションヘッダとフッター注記は通常どおり表示する。

### 5.3 404(無効・失効リンク。デザイン未描画 → 決定)

`apps/web/src/app/c/[token]/not-found.tsx`。API 404(revoked と不存在は区別されない)と token 形式不一致で表示。

- レイアウト: `ShareHeader` は通常どおり表示(CTA 導線を維持)。本文は背景 #E3E1D9、中央カラム 820px、padding-top:96px、中央寄せ縦 flex、gap:8px、text-align:center。
- 見出し: 「このリンクは無効です」— font-size:16px、font-weight:700、color `var(--pr-text)`。
- 説明: 「共有リンクが無効化されたか、URL が間違っています。共有した相手に新しいリンクを確認してください。」— font-size:12px、color `var(--pr-text-sub)`、line-height:1.7。
- HTTP ステータス: 404(`notFound()` により Next.js が付与)。`X-Robots-Tag: noindex` は `headers()` 設定により同様に付く。

### 5.4 エラー状態(5xx / 429。デザイン未描画 → 決定)

`apps/web/src/app/c/[token]/error.tsx`(`"use client"`)。

- レイアウト・スタイルは §5.3 と同一枠(`ShareHeader` も同様に表示する。`ShareHeader` は静的で Client 専用 API を使わないため、`"use client"` ファイルから import してもそのまま動作する)。見出し: 「ページを表示できません」。説明: 「一時的な問題が発生しました。しばらく待ってからもう一度お試しください。」。
- 再試行ボタン: EmptyState の action と同スタイル(h26px、padding 0 12px、border 1px `var(--pr-border-control)`、border-radius:6px、font 11px、color `var(--pr-text-mid)`、bg `var(--pr-bg-control)`)、ラベル「再読み込み」、クリックで Next.js の `reset()` を呼ぶ。

### 5.5 フィールド欠落時の縮退規則(決定)

| フィールド | null / 空のときの描画 |
|---|---|
| `collection.description` | 説明文の `<p>` ごと省略(タイトルとメタ行の gap:6px は維持) |
| `collection.deadline` | メタ行末尾の「 · 」+締切バッジを省略(「… · 5 本」で終わる) |
| `items[].venue_year` | 書誌行を「{authors_short} · arXiv ↗」とする |
| `items[].arxiv_url` | 「arXiv ↗」リンクと直前の「 · 」を省略 |
| `items[].summary_3line` | 要約行を省略(カード4 相当の見た目) |
| `items[].shared_note` | `SharedNoteBox` を省略。`include_notes=false` のとき全カードで省略 |

権利フラグにより縮退した論文(docs/09 §5「書誌のみに縮退」)は API 側で `summary_3line: null` として返るため、画面側の追加分岐は不要。

### 5.6 デザインのアートボード表現の実装置換(決定)

- カード4 の `opacity:0.88` と本文エリアの `overflow:hidden` は「リストが下に続く」ことのデザイン表現であり、**実装しない**。全カードを opacity:1 で描画し、ページ全体をドキュメントスクロールさせる(html 既定スクロール)。5 本目以降のカードもデータどおり全件描画する(決定: 仮想化・ページングは行わず、件数によらず `items` 全件を素朴に描画する。理由: コレクションのエントリ数に機能上の上限はなく(plans/03 §13.2)、閲覧専用の静的リストは数百件でも SSR 描画で実用上問題ないため)。
- フレームの height:780px / border / radius / shadow も実装しない(§4 冒頭)。

### 5.7 インタラクション一覧

| 要素 | 操作 | 挙動 |
|---|---|---|
| CTA「訳読をはじめる」 | クリック | `/login?next=/c/{token}` へ内部遷移(`next/link`) |
| CTA「訳読をはじめる」 | ホバー | 背景を `color-mix(in srgb, var(--pr-acc) 16%, transparent)` に変更、`transition: background-color 120ms ease-out`(決定: デザイン未描画。--pr-acc-s の 0.10 を 0.16 へ強めるだけで新色を発明しない) |
| 「arXiv ↗」 | クリック | 外部リンク。新規タブ(`target="_blank" rel="noopener noreferrer nofollow"`) |
| 「arXiv ↗」 | ホバー | `text-decoration: underline`(決定: デザイン未描画。色・weight は変えない) |
| ページ | スクロール | ドキュメント縦スクロール(カード全件) |
| 上記以外 | — | 一切のインタラクションなし(閲覧専用) |

- ローディング状態: なし(SSR で HTML が完成して届く。`loading.tsx` は置かない — 決定。理由: fetch は 1 本で revalidate キャッシュが効き、ストリーミング分割の意味がない)。
- キーボード: 通常のタブ順(CTA → 各 arXiv リンク)。フォーカスリングはブラウザ既定を `outline: 2px solid var(--pr-acc); outline-offset: 2px` に統一(決定。08 のフォーカス規約に追随)。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Playwright VRT(plans/08 §9)。シードデータ = §4.4 のデザイン収録データ(コレクション「輪読会 2026-07」、説明文・メタ・締切 7/16、論文 5 件 — うち先頭 4 件はデザインと同一書誌、2 件目のみ shared_note あり、4 件目は summary_3line: null)。ビューポート 1440×780 で `/c/{seedToken}` を開き、確定デザイン `div#4c` と照合する。

**意図的差分(照合から除外する既知差分)**: (1) フレーム装飾(border/radius/shadow)なし、(2) カード4 の opacity 1.0、(3) 本文 overflow による下端切れなし(780px クリップで代替)。それ以外は一致必須:

- [ ] ヘッダー: h52px・bg #FFFFFF・下線 1px #E6E3DA・padding 0 24px・gap 10px
- [ ] ロゴ「訳」22×22px radius 6px bg #3E5C76、ワードマーク「訳読」14.5px/700/ls0.5px
- [ ] モードバッジ「共有されたコレクション — 閲覧専用」h19px・padding 0 8px・radius 4px・bg #F1EFE9・#777B81・10.5px/600
- [ ] 誘導文言 11.5px #5B6067、CTA h28px・padding 0 13px・radius 6px・border rgba(62,92,118,0.32)・bg rgba(62,92,118,0.10)・#3E5C76・11.5px/600
- [ ] 本文背景 #E3E1D9、中央カラム 820px、padding-top 28px、カラム gap 14px
- [ ] タイトル 22px/700、説明文 12px #5B6067 lh1.7、メタ行 11px #9A9EA4 gap 8px
- [ ] 締切バッジ h16px・padding 0 6px・radius 3px・bg rgba(176,104,79,0.14)・#A05A42・600・font-size 11px(親継承)
- [ ] カード: bg #FFFFFF・border 1px #DDD9CF・radius 10px・padding 14px 18px・gap 14px、リスト gap 10px
- [ ] 番号バッジ 22×22px 正円 bg #26292E #FFFFFF 11px/700 margin-top 2px
- [ ] カード本文カラム gap 5px、タイトル 13.5px/600 lh1.5、書誌 11px #9A9EA4、「arXiv ↗」#3E5C76/600
- [ ] 要約行 11.5px lh1.7 #5B6067、先頭「✦ 」、①②③ が付かない
- [ ] メモボックス bg #FAF9F5・border 1px #EFECE3・radius 7px・padding 8px 11px・gap 8px
- [ ] ラベル「共有者のメモ」h16px・padding 0 6px・radius 3px・bg rgba(101,148,113,0.16)・#4C7458・9px/700・margin-top 1px
- [ ] フッター注記 10.5px #9A9EA4 padding 2px 4px 20px、文言逐語一致
- [ ] 全 UI 文言が §4.8 と逐語一致(シードデータ含む)
- [ ] フォントが IBM Plex Sans JP で描画される(英語タイトルも継承)

### 6.2 機能検証

- [ ] 未ログイン(クッキーなし)で `/c/{token}` を開くと 200 で全内容が表示される(docs/09 §8 受け入れ基準)
- [ ] レスポンスヘッダに `X-Robots-Tag: noindex` があり、HTML に `<meta name="robots" content="noindex, nofollow">` がある
- [ ] レスポンスヘッダに `Cache-Control: public, s-maxage=60, stale-while-revalidate=300` がある
- [ ] 表示内容が書誌+✦要約+許可メモのみで、進捗・注釈・担当者・読書統計等の個人資産が HTML ソースに一切含まれない(docs/09 §4)
- [ ] `include_notes=false` の共有では全カードで「共有者のメモ」ボックスが出ない。`true` では `one_line_note` 非空のカードのみに出る
- [ ] カードの表示順が `items[].order` 昇順(=共有元の並べ替えが反映。docs/06 §11 受け入れ基準)
- [ ] revoked された token・存在しない token・形式不正(8 文字英数以外)の token はすべて §5.3 の 404 ビュー(HTTP 404)になり、レスポンスから有効/失効が区別できない
- [ ] 共有元がコレクションを更新後、60 秒+SWR 猶予以内に再アクセスで新内容が表示される(revalidate 60)
- [ ] 「訳読をはじめる」クリックで `/login?next=/c/{token}` へ遷移する
- [ ] 「arXiv ↗」クリックで `items[].arxiv_url` が新規タブで開き、`rel` に noopener noreferrer nofollow が揃っている
- [ ] `deadline` / `description` / `venue_year` / `arxiv_url` / `summary_3line` / `shared_note` がそれぞれ null のとき §5.5 どおりに縮退する
- [ ] `items` が 0 件のとき §5.2 の EmptyState が表示される
- [ ] タイトルに `$…$` を含む論文で数式が SSR 済み HTML(KaTeX)として届く(クライアント JS 無効でも描画される)
- [ ] JavaScript 無効のブラウザでページ全体が閲覧でき、リンク 2 種が機能する(RSC のみで構成される検証)
- [ ] `document.title` が「{コレクション名} — 訳読で共有されたコレクション」になる
- [ ] 編集・並べ替え・削除に相当する UI 要素(ボタン・ハンドル・メニュー)が DOM に存在しない
- [ ] axe による自動チェックで `<h1>` が 1 つ、リンクに識別可能な名前があり、WCAG 2.1 AA のコントラスト違反がない(メタ行 #9A9EA4 は 11px 補助テキストとして 08 §9 の基準に従う)
