# 画面 5a: ビューア リソースタブ

> 対象読者と前提: 本書は「Alinea — 論文読解ワークベンチ」のフロントエンド/バックエンド実装者向けに、確定デザイン画面 5a(論文ビューア — リソースタブ)を 1px の差分なく実装するための計画書である。機能仕様は docs/12(リソース)・docs/04(ビューア)を正、ピクセル値は抽出ファイル extract/5a.md を正とする。ビューアの共通骨格(ヘッダ・左レール・サイドパネル枠・キーマップ・読書位置保存)は plans/09-screens/viewer-shell.md が所有し、本書は viewer-shell §11 の分担表どおり **`ResourcesTab`(確定リソースカード 4 種・公式実装の自動検出カード・URL 追加フッター)** を所有する。共通コンポーネント名・トークン名は plans/08-design-system.md、API エンドポイント名・型名は plans/03-api.md、テーブルは plans/02-data-model.md のものをそのまま使う。技術スタック: Next.js 15(App Router)+ React 19 + TypeScript 5 + Tailwind CSS v4 + TanStack Query v5 + Zustand + `packages/api-client`(OpenAPI 生成 TS クライアント)。

## 1. 概要とルート

- **ルートパス(確定)**: `/papers/{itemId}`(ファイル: `apps/web/src/app/(app)/papers/[itemId]/page.tsx`)。viewer-shell §3.1 の URL 契約に従う。画面 5a は「訳文モード(`?mode=translation`)+サイドパネル『リソース』タブがアクティブ」の状態であり、独立ルートを持たない。外部からリソースタブを直接開く深リンクは `?panel=resources`(viewer-shell §3.1 の `panel` クエリ。初期化時に 1 回消費され URL から除去される)。
- **認証**: 必須(HTTPOnly セッションクッキー)。未認証は `(app)` レイアウトが `/login` へリダイレクト。CSR 画面(SSR しない)。
- **画面の役割**: 論文外の学習材料(GitHub 実装・YouTube 発表動画・発表スライド・解説記事)を論文にひも付け、1 クリックで開けるようにする(docs/12 §1)。URL 貼り付けだけで種類を自動判定し(P2)、arXiv 由来の公式実装は破線カードで「提案」する(勝手に追加しない。P6)。リソースは外部リンク+メタデータ+ひとことメモのみで、コンテンツ本体は取り込まない(P7)。
- **担当範囲(viewer-shell §11 の分担)**: 本書が実装を所有するのは `apps/web/src/components/viewer/panel/ResourcesTab.tsx` とその子孫のみ。ヘッダ・左レール・タブ行・本文ペイン(訳文モード= 1b 担当の `TranslationPane`)は参照実装であり、本書 §4 にピクセル検証用の実測値を全掲する(値の実装所有者は viewer-shell / 1b)。

## 2. データ要件

### 2.1 使用 API エンドポイント一覧(plans/03-api の名前)

| # | エンドポイント | plans/03 | 用途 | 取得/実行タイミング |
|---|---|---|---|---|
| 1 | `GET /api/library-items/{id}/viewer` | §6.1 | タブ件数バッジ `counts.resources`、目次 `toc`(§チップのサジェスト候補・ジャンプ先解決) | ルート初回マウント(viewer-shell 所有) |
| 2 | `GET /api/library-items/{id}/resources` | §12.1 | 確定リソース一覧+公式実装提案(`suggestion`)+件数 | リソースタブ初回アクティブ化時 |
| 3 | `POST /api/library-items/{id}/resources` | §12.2 | URL 貼り付け追加(kind 自動判定・メタ同期取得 3 秒タイムアウト) | フッター「追加」クリック/入力欄 Enter |
| 4 | `PATCH /api/resources/{resource_id}` | §12.3 | タイトル編集・種類変更・ひとことメモの作成/編集/削除 | カードメニュー・メモインライン編集の保存 |
| 5 | `DELETE /api/resources/{resource_id}` | §12.4 | 削除(「元に戻す」トースト付き遅延実行) | カードメニュー「削除」→トースト 6,000ms 経過後 |
| 6 | `POST /api/resources/{resource_id}/refresh-meta` | §12.5 | メタ再取得 | カードメニュー「メタを再取得」 |
| 7 | `POST /api/library-items/{id}/resource-suggestion/accept` | §12.6 | 提案カード「+ 追加」(201: `official: true` の ResourceLink) | クリック時 |
| 8 | `POST /api/library-items/{id}/resource-suggestion/dismiss` | §12.6 | 提案カード「無視」(204。無視リストに永続記録) | クリック時 |

使用する型(plans/03 §12.1 の完全形をそのまま使用): `ResourceLink`(`id / kind / url / official / title / source_label / thumbnail_url / meta / meta_fetched / note / created_at`)、`ResKind = 'github' | 'youtube' | 'slides' | 'article'`。

### 2.2 TanStack Query キー設計(確定)

```ts
// apps/web/src/features/resources/queryKeys.ts
export const resourceKeys = {
  list: (liId: string) => ['resources', liId] as const,
};
```

- `['resources', liId]`: `GET /api/library-items/{id}/resources` の全応答(`{ items, suggestion, count }`)。`staleTime: 30_000ms`、`refetchOnWindowFocus: true`(既定)。タブ本体はアクティブ時のみマウント(viewer-shell §6.3)されるため、タブを開くたびに stale なら再取得される。
- **取得タイミング**: `ResourcesTab` マウント時(=タブアクティブ化時)。プリフェッチはしない。決定。理由: リソースは読書の補助動線であり、初期表示 p50 2 秒(docs/09)のクリティカルパスに載せない。
- **ミューテーション成功時の無効化(確定)**:
  - 追加(#3)/提案追加(#7)/削除(#5)成功 → `resourceKeys.list(liId)` と `['viewer', liId]`(タブ件数バッジ `counts.resources` の更新。viewer-shell §6.1)。
  - 編集(#4)/メタ再取得(#6)成功 → `resourceKeys.list(liId)` のみ(件数不変)。
  - 提案却下(#8)成功 → `resourceKeys.list(liId)` のみ(提案は件数に数えない。docs/12 §5)。
- **楽観更新(確定)**: 提案「+ 追加」「無視」とメモ保存は `onMutate` で `setQueryData` により即時反映し、失敗時ロールバック+Toast `error`。URL 追加はサーバー判定(kind・メタ)が結果を決めるため楽観更新しない(ボタンのペンディング表示のみ。§5.4)。

### 2.3 リアルタイム更新

- **SSE・ポーリングは使わない**。決定。理由: リソースはユーザー自身の同期操作でのみ変化し(メタ取得も POST 応答に同期内包。plans/03 §12.2)、他クライアントとの競合はウィンドウフォーカス時の再取得(staleTime 30s)で十分。ジョブ種別 `resource_meta`(plans/02 §ジョブ)は再取得(#6)のサーバー内部処理であり、フロントは 200 応答を待つだけで購読しない。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`共通` = plans/08 §5〜6、`shell` = viewer-shell 所有。無印 = 本画面固有(配置: `apps/web/src/components/viewer/panel/resources/`。`ResourcesTab.tsx` のみ `panel/` 直下)。

```
ReadPage (app/(app)/papers/[itemId]/page.tsx)          shell
└─ ViewerShell (mode='translation')                    shell
   ├─ ViewerHeader / TocRail / TranslationPane          shell / 1b 担当(§4.1〜4.3 に実測値のみ全掲)
   └─ SidePanel (340px)                                shell
      ├─ SidePanelTabs                                 共通 §5.16(active='resources', counts={annotations, resources})
      └─ ResourcesTab                                  ← 本書所有
         ├─ ResourceSuggestionCard                     … 破線の公式実装提案カード(§4.5-a)
         ├─ ResourceCard ×N                            … 確定リソースカード(§4.5-b〜e)
         │  ├─ ResourceKindIcon                        … 26×26px 種類アイコン
         │  ├─ OfficialBadge                           … 「公式実装」緑バッジ(official=true のみ)
         │  ├─ YouTubeThumbnail                        … kind='youtube' のみ(96px+時間バッジ)
         │  ├─ ResourceNoteBox                         … 💬 ひとことメモ(表示)
         │  │  └─ EvidenceChip (size='note')           共通 §5.18(§参照チップ。§3.3 で変種を確定)
         │  ├─ ResourceNoteEditor                      … メモのインライン編集(§5.7)
         │  │  └─ SectionSuggestPopover                … 「§」入力時のセクション候補(Popover 共通 §5.10)
         │  └─ ResourceCardMenu                        … ホバー「⋯」+ Popover 共通 §5.10(width 180)
         ├─ ResourceListSkeleton / EmptyState          共通 §5.21(空状態)
         └─ ResourceAddFooter                          … URL 入力+「追加」+ヘルプ文(§4.6)
```

### 3.2 画面固有コンポーネントの props 型(完全形)

```tsx
// apps/web/src/components/viewer/panel/ResourcesTab.tsx
// props なし。viewer-shell §6.5 の契約どおり useViewerStore() と useParams() から
// itemId / revisionId / mode を取得する。

// apps/web/src/components/viewer/panel/resources/ResourceSuggestionCard.tsx
interface ResourceSuggestionCardProps {
  suggestion: { url: string; detected_from: 'arxiv_page' };
  onAccept: () => void;          // POST …/resource-suggestion/accept
  onDismiss: () => void;         // POST …/resource-suggestion/dismiss
  pending: boolean;              // accept/dismiss の実行中(両ボタン disabled)
}

// apps/web/src/components/viewer/panel/resources/ResourceCard.tsx
import type { ResourceLink, ResKind } from '@alinea/api-client';
interface ResourceCardProps {
  resource: ResourceLink;
  flash: boolean;                // 重複追加時の既存カードハイライト(§5.5)。2,000ms で親が false に戻す
  onJumpSection: (sectionId: string) => void;   // §チップ → viewer-store.requestScroll
  onEdit: (patch: { title?: string; kind?: ResKind; note?: string | null }) => void; // PATCH
  onRefreshMeta: () => void;     // POST …/refresh-meta
  onDelete: () => void;          // 遅延 DELETE(§5.8)
}

// apps/web/src/components/viewer/panel/resources/ResourceKindIcon.tsx
interface ResourceKindIconProps { kind: ResKind; sourceLabel: string }

// apps/web/src/components/viewer/panel/resources/YouTubeThumbnail.tsx
interface YouTubeThumbnailProps {
  thumbnailUrl: string | null;   // null=ダークプレースホルダのみ
  durationSeconds: number | null;// null=時間バッジ非表示
  url: string;                   // 再生ボタンクリックで新規タブ(埋め込み再生はしない。docs/12 §6)
}

// apps/web/src/components/viewer/panel/resources/ResourceNoteBox.tsx
interface ResourceNoteBoxProps {
  note: string;                            // "[[sec:sec-3|§2.2]]" 記法を含む生文字列
  onJumpSection: (sectionId: string) => void;
  onStartEdit: () => void;
}

// apps/web/src/components/viewer/panel/resources/ResourceNoteEditor.tsx
interface ResourceNoteEditorProps {
  initialNote: string;
  toc: TocNode[];                          // §サジェスト候補(['viewer', liId] のキャッシュから)。型は plans/03 §6.1 の TocNode(@alinea/api-client)
  onSave: (note: string | null) => void;   // 空文字は null(メモ削除)に正規化して PATCH
  onCancel: () => void;
}

// apps/web/src/components/viewer/panel/resources/ResourceAddFooter.tsx
interface ResourceAddFooterProps {
  onAdd: (url: string) => void;
  pending: boolean;              // POST 実行中(「追加」→「追加中…」、入力 disabled)
  errorMessage: string | null;   // 422 時のインラインエラー(§5.6)
}
```

### 3.3 共通コンポーネントの本画面での確定事項

- **EvidenceChip に `size='note'` 変種を追加する(決定)**。plans/08 §5.18 は 5a のメモ内 §チップを EvidenceChip の使用箇所と定めるが、既存 2 変種(inline h16px / header h17px)と 5a 実測が異なるため、実測値で第 3 変種を確定する: **height:15px、padding:0 5px、border:1px solid `var(--pr-acc-m)`、color:`var(--pr-acc)`、background:なし(透明)、border-radius:3px、font-size:9px、font-weight:600、vertical-align:1px**。anchor は `{ type: 'section', sectionNumber: '2.2' }`、label は「§2.2」。
- **CountBadge `variant='tab'`**(共通 §5.7)がタブの「4」「6」を描画(viewer-shell §6.1 所有)。
- **Popover**(共通 §5.10): カードメニュー width 180px `bottom-end` caret なし / §サジェスト width 220px `bottom-start` caret なし。
- **Toast**(共通 §5.20)・**EmptyState**(共通 §5.21)を §5 の各状態で使用。

## 4. レイアウト・スタイル完全仕様

出典: extract/5a.md(逐語・全量)。フレームは 1440×900px、背景 #FBFAF7(`--pr-bg-app`)、文字色 #1E2227(`--pr-text`)。デザインの外枠(border:1px solid #D6D3C9、border-radius:10px、box-shadow:0 20px 44px rgba(28,30,34,0.12)、overflow:hidden)はキャンバス表現であり実アプリでは描かない(plans/08 §7.1)。ルートは flex 縦、`height:100dvh`。

```
┌──────────────────────────────────────────────────────────────────┐
│ トップバー h=52px 背景#FFFFFF border-bottom:1px #E6E3DA          │
│ ‹ [論文タイトル] [A] [●読んでいる▾] …(flex:1)…                  │
│ [訳文|対訳|原文|PDF|記事] [スタイル:自然訳▾] [🔍検索 /] ⋯       │
├────┬──────────────────────────────────┬──────────────────────────┤
│左  │ 本文カラム(中央寄せ, 幅680px)    │ サイドパネル w=340px      │
│レー│ 見出し 3.1 CIFAR-10 画像生成     │ ┌タブ行(border-bottom)   │
│ル  │ 段落1(ハイライト・表1リンク含む) │ │チャット/メモ/注釈6/図表 │
│44px│ 段落2                            │ │/[リソース4]/情報        │
│    │                                  │ ├本体 #FCFBF8 p=12 gap=9 │
│☰   │                                  │ │[自動検出カード(破線)]  │
│🔖  │                                  │ │[GitHubカード]           │
│🔍  │                                  │ │[YouTubeカード]          │
│    │                                  │ │[スライドカード]         │
│    │                                  │ │[解説記事カード]         │
│    │                                  │ ├フッター(URL入力+追加)  │
└────┴──────────────────────────────────┴──────────────────────────┘
```

- メイン領域: `flex:1; display:flex; min-height:0`。
- 左レール: width:44px、flex:none、背景 #F7F6F2(`--pr-bg-pane`)、border-right:1px solid #E7E4DB(`--pr-border-pane`)、flex 縦、align-items:center、padding:12px 0、gap:14px。
- 本文エリア: `flex:1; min-width:0; display:flex; justify-content:center; overflow:hidden`。内側カラム width:680px、padding:34px 0 0、font-family:`var(--pr-jp,'Noto Serif JP'),serif`(パネル 340px 時の本文 680px は viewer-shell §6.2 の連動規則)。
- サイドパネル: width:340px、flex:none、背景 #FFFFFF(`--pr-bg-card`)、border-left:1px solid #E7E4DB(`--pr-border-pane`)、flex 縦。

### 4.1 トップバー(実装所有: viewer-shell §4。実測値の全掲)

高さ 52px、flex:none、背景 #FFFFFF(`--pr-bg-card`)、border-bottom:1px solid #E6E3DA(`--pr-border-header`)、flex 横並び、align-items:center、gap:10px、padding:0 16px。

1. 戻る記号「‹」: font-size:16px、color:#8A8E94(`--pr-text-icon`)、width:20px、text-align:center。
2. 論文タイトル: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」font-size:13px、font-weight:600、max-width:330px、white-space:nowrap、overflow:hidden、text-overflow:ellipsis。
3. 優先度バッジ「A」(`QualityBadge` 共通 §5.3): 18×18px、inline-flex 中央、border-radius:4px、背景 `var(--pr-acc-s)`(rgba(62,92,118,0.10))、文字色 `var(--pr-acc)`(#3E5C76)、font-size:10.5px、font-weight:700。
4. ステータスピル「読んでいる」(`StatusPill` 共通 §5.2): inline-flex、align-items:center、gap:5px、height:24px、padding:0 9px、border:1px solid #DDD9CF(`--pr-border-control`)、border-radius:999px、font-size:11.5px、font-weight:500、背景 #FFFFFF(`--pr-bg-control`)。内部ドット 7×7px、border-radius:50%、背景 `var(--pr-acc)`。末尾「▾」color:#9A9EA4(`--pr-text-muted`)、font-size:9px。
5. スペーサ: `div flex:1`。
6. 表示モードセグメント(`SegmentedControl` 共通 §5.1 size='md'): 外枠 flex、背景 #EFEDE6(`--pr-bg-muted`)、border-radius:7px、padding:2px、gap:2px。各セグメント height:24px、padding:0 11px、border-radius:5px、font-size:11.5px。「訳文」選択中: 背景 #FFFFFF(`--pr-bg-seg-selected`)、color:#1E2227、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)(`--pr-shadow-seg`)。「対訳」「原文」「PDF」「記事」非選択: color:#5B6067(`--pr-text-sub`)、背景なし。
7. スタイルセレクタ「スタイル: 自然訳」+「▾」(color:#9A9EA4、font-size:9px): inline-flex、gap:5px、height:26px、padding:0 10px、border:1px solid #DDD9CF、border-radius:6px、font-size:11.5px、color:#3C4046(`--pr-text-mid`)。
8. 検索ボックス(`SearchBox` 共通 §5.13 variant='in-paper'): inline-flex、gap:6px、height:26px、padding:0 10px、背景 #F1EFE9(`--pr-bg-inset`)、border-radius:6px、font-size:11.5px、color:#8A8E94、width:150px。虫眼鏡 SVG 11×11(viewBox 0 0 12 12、circle cx=5 cy=5 r=3.6 stroke currentColor stroke-width 1.3+斜線 M8 8→10.6 10.6、stroke-linecap:round)。プレースホルダ「この論文内を検索」。キーキャップ「/」(`Keycap` 共通 §5.22): margin-left:auto、border:1px solid #DAD7CD(`--pr-border-keycap`)、border-radius:3px、padding:0 4px、font-size:9.5px、背景 #FFFFFF。
9. オーバーフローメニュー「⋯」: font-size:15px、color:#5B6067、letter-spacing:1px。

### 4.2 左レール(実装所有: viewer-shell §5.2。実測値の全掲)

- 「☰」目次アイコン: font-size:13px、color:#5B6067(`--pr-text-sub`。アクティブ寄りの濃色)。
- `BookmarkIcon` SVG 10×12(viewBox 0 0 10 12、path M1 1h8v10L5 8.5 1 11V1z fill currentColor=リボン型塗りつぶし): color:#9A9EA4(`--pr-text-muted`)。
- `MagnifierIcon` SVG 12×12(circle r=3.6 stroke-width 1.3+ハンドル線、stroke-linecap:round): color:#9A9EA4。

### 4.3 本文カラム(実装所有: 1b `TranslationPane`。実測値の全掲)

width:680px、padding:34px 0 0、フォント `var(--pr-jp,'Noto Serif JP'),serif`。

1. セクション見出し: font-family:'IBM Plex Sans JP',sans-serif、font-size:19px、font-weight:700、margin-bottom:14px、color:#1E2227。テキスト「3.1 CIFAR-10 画像生成」+後続スパン「— Unconditional Image Generation」(color:#8A8E94、font-weight:400、font-size:14px、font-family:'Source Serif 4',Georgia,serif、font-style:italic)。
2. 段落 1: font-size:16.5px、line-height:2.15、color:#24272B(`--pr-text-body`)、margin-bottom:18px。文中ハイライトスパン「2 回の reflow の後、1 ステップ生成の FID は 4.85 まで改善し」: 背景 rgba(196,148,50,0.26)(`--pr-ann-important-bg`)、border-radius:2px、padding:0 1px(`HighlightMark` 共通 §5.17)。文中参照リンク「表1」: color:`var(--pr-acc)`、border-bottom:1px dotted `var(--pr-acc)`、font-weight:600。
3. 段落 2: font-size:16.5px、line-height:2.15、color:#24272B(margin-bottom なし)。

本文逐語: 「CIFAR-10 の無条件生成において、1-整流フローは 127 ステップで FID 2.58 を達成し、同一アーキテクチャの拡散モデルと同等の品質をより少ない計算で実現する。2 回の reflow の後、1 ステップ生成の FID は 4.85 まで改善し、従来の拡散 ODE 蒸留(FID 8.91)を大きく上回る(表1)。」/「実験は NCSN++ アーキテクチャを用い、学習率などのハイパーパラメータは公開実装の既定値に従った。詳細な設定は付録 C に示す。」

### 4.4 サイドパネル タブ行(実装所有: viewer-shell §6.1 / `SidePanelTabs` 共通 §5.16。実測値の全掲)

display:flex、border-bottom:1px solid #ECE9DF(`--pr-border-soft`)、padding:0 6px。各タブ: padding:10px 8px 8px、font-size:12px。

- 非選択タブ: color:#777B81(`--pr-text-sub2`)—「チャット」「メモ」「注釈」「図表」「情報」。
- 「注釈」タブにカウント「6」(`CountBadge` variant='tab': font-size:10px、color:#9A9EA4)。
- 選択中タブ「リソース」: font-weight:600、color:`var(--pr-acc)`、box-shadow:`inset 0 -2px var(--pr-acc)`(下線インジケータ)。カウント「4」(font-size:10px、色は親継承=アクセント色)。

### 4.5 リソース一覧本体(本書所有: `ResourcesTab`)

コンテナ: `flex:1; overflow-y:auto; padding:12px; display:flex; flex-direction:column; gap:9px; background:var(--pr-bg-feed)`(#FCFBF8)。決定: デザインの `overflow:hidden` はモック上の固定表示であり、実装は縦スクロール(docs/12 §6「一覧はタブ内で縦スクロール」)。並び順: 提案カード(あれば)が常に先頭、確定リソースは `created_at` 昇順(新規が末尾。docs/12 §7)。決定: クライアントで再ソートせず、`items` の応答順(plans/03 §12.1 が昇順を保証)をそのまま描画する。

#### a) 公式実装 自動検出カード(`ResourceSuggestionCard`)

- 外枠: border:1px dashed #CBC7BA(`--pr-border-dashed-suggest`。§4.7=plans/08 §2 収載)、border-radius:8px、padding:10px 12px、flex 縦、gap:7px、背景 #FBFAF7(`--pr-bg-app`)。
- 本文: font-size:11px、line-height:1.65、color:#3C4046(`--pr-text-mid`)。先頭スパン「✦ 公式実装を検出しました」(✦=`AiMark` 共通 §5.19): color:`var(--pr-acc)`、font-weight:700。続けて「 — arXiv ページのリンクから」。`<br>` 後に候補 URL「github.com/gnobitab/RectifiedFlow」: font-family:'IBM Plex Mono',monospace、font-size:10.5px、color:#5B6067(`--pr-text-sub`)。表示は `suggestion.url` からスキーム(`https://`)を除いた文字列。
- ボタン行(flex、gap:6px):
  - 「+ 追加」プライマリボタン: inline-flex、height:22px、padding:0 11px、border-radius:5px、背景 `var(--pr-acc)`、color:#FFFFFF、font-size:10.5px、font-weight:600。
  - 「無視」テキストボタン: height:22px、padding:0 10px、font-size:10.5px、color:#9A9EA4(`--pr-text-muted`)、背景・枠なし。

#### b) GitHub リソースカード(`ResourceCard` kind='github')

- 外枠: 背景 #FFFFFF(`--pr-bg-card`)、border:1px solid #E2DFD5(`--pr-border-card`)、border-radius:8px、padding:10px 12px、flex 縦、gap:7px。
- ヘッダ行(flex、gap:9px、align-items:flex-start):
  - アイコン「GH」(`ResourceKindIcon`): 26×26px、inline-flex 中央、border-radius:6px、背景 #26292E(`--pr-elev-bg`)、文字 #FFFFFF、font-size:9px、font-weight:700、flex:none、letter-spacing:0.3px。
  - 中央カラム(flex 縦、gap:2px、min-width:0、flex:1):
    - 1 行目(flex、gap:6px、align-items:center): リポジトリ名「gnobitab/RectifiedFlow」font-size:12px、font-weight:600、font-family:'IBM Plex Mono',monospace、white-space:nowrap+overflow:hidden+text-overflow:ellipsis。バッジ「公式実装」(`OfficialBadge`): height:15px、padding:0 5px、border-radius:3px、背景 rgba(101,148,113,0.16)、color:#4C7458、font-size:8.5px、font-weight:700、flex:none(§4.7 のトークン `--pr-official-bg` / `--pr-official-fg`)。
    - 2 行目メタ: 「GitHub · Python · ★ 1.2k · 更新 2023-11」font-size:10px、color:#9A9EA4(`--pr-text-muted`)。整形規則は §4.8。
  - 「開く ↗」リンク: font-size:11px、color:`var(--pr-acc)`、font-weight:600、flex:none。`<a target="_blank" rel="noopener noreferrer">`。
- ひとことメモ欄(`ResourceNoteBox`): font-size:11px、line-height:1.65、color:#3C4046(`--pr-text-mid`)、背景 #F7F5EF(`--pr-bg-comment`)、border-radius:5px、padding:6px 9px。内容「💬 train_reflow.py が §2.2 の手順に対応。データペアの再生成は generate_data.py」。「§2.2」はインラインチップ(`EvidenceChip` size='note'。§3.3): inline-flex、height:15px、padding:0 5px、border:1px solid `var(--pr-acc-m)`(rgba(62,92,118,0.32))、color:`var(--pr-acc)`、border-radius:3px、font-size:9px、font-weight:600、vertical-align:1px。

#### c) YouTube リソースカード(`ResourceCard` kind='youtube')

- 外枠: b) と同一(#FFFFFF、1px solid #E2DFD5、radius 8px、padding 10px 12px、flex 縦、gap:7px)。
- ヘッダ行(flex、gap:9px、align-items:flex-start):
  - アイコン「▶」: 26×26px、border-radius:6px、背景 #B3423A(`--pr-youtube`。§4.7)、文字 #FFFFFF、font-size:10px、flex:none。
  - 中央: タイトル「ICLR 2023 Oral — Flow Straight and Fast」font-size:12px、font-weight:600、line-height:1.45(通常フォント。等幅は GitHub のみ)。メタ「YouTube · 12:34 · 著者発表」font-size:10px、color:#9A9EA4(v1 の実データ整形は §4.8。「著者発表」はモック値でありフィールドを持たない)。
  - 「開く ↗」: b) と同一。
- サムネイル(`YouTubeThumbnail`): height:96px、border-radius:5px、背景 #26292E(`--pr-elev-bg`。プレースホルダ面)、flex 中央、position:relative、overflow:hidden。`thumbnail_url` があれば `<img>` を `object-fit:cover` で全面表示(プレースホルダの上層)。
  - 中央再生ボタン: 34×24px、border-radius:5px、背景 #B3423A、文字 #FFFFFF、font-size:11px、「▶」。
  - 右下時間バッジ: position:absolute、bottom:6px、right:8px、font-size:9px、color:#FFFFFF、背景 rgba(0,0,0,0.7)、border-radius:3px、padding:1px 5px、font-family:'IBM Plex Mono',monospace、「12:34」。`meta.duration_seconds` が null なら非表示。
- ひとことメモ欄(b と同スタイル): 「💬 8:20〜 reflow の直感的な説明が分かりやすい」。

#### d) スライド(PDF)リソースカード(`ResourceCard` kind='slides'、メモなし)

- 外枠: 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:8px、padding:10px 12px、**flex 横並び**、gap:9px、align-items:flex-start(メモ欄なし・1 行構成。メモを付ければ b と同じ縦 flex+メモ欄構成に切り替わる。docs/12 §6)。
- アイコン「PDF」: 26×26px、border-radius:6px、背景 rgba(196,148,50,0.18)(`--pr-ann-important-count-bg`)、color:#8A6A24(`--pr-ann-important-chip-fg`)、font-size:8.5px、font-weight:700、flex:none。
- 中央: タイトル「発表スライド(ICLR 2023)」font-size:12px、font-weight:600、line-height:1.45。メタ「iclr.cc · PDF · 24 枚」font-size:10px、color:#9A9EA4。
- 「開く ↗」: b) と同一。

#### e) 解説記事リソースカード(`ResourceCard` kind='article'、メモなし)

- 外枠: d) と同一構成。
- アイコン「Z」(Zenn): 26×26px、border-radius:6px、背景 rgba(88,132,170,0.18)(`--pr-article-icon-bg`。§4.7)、color:#4A6E8E(`--pr-article-icon-fg`)、font-size:11px、font-weight:700、flex:none。表示文字は `source_label` の先頭 1 文字を大文字化(§4.8)。
- 中央: タイトル「Rectified Flow を図で理解する」font-size:12px、font-weight:600、line-height:1.45。メタ「zenn.dev · 解説記事 · 15 min」font-size:10px、color:#9A9EA4。
- 「開く ↗」: b) と同一。

#### カード共通の追加要素(デザイン未描画。§5.7〜5.8 の決定に対応)

- 「⋯」メニューボタン: ヘッダ行の「開く ↗」の右、margin-left:6px、width:16px、font-size:13px、color:#9A9EA4、letter-spacing:1px。既定 `opacity:0`、カード `:hover` および `:focus-within` で `opacity:1`(transition 120ms ease-out)。`aria-label="リソースの操作"`。

### 4.6 サイドパネル フッター(本書所有: `ResourceAddFooter`)

- コンテナ: padding:10px 12px、border-top:1px solid #ECE9DF(`--pr-border-soft`)、flex 縦、gap:7px、背景 #FFFFFF(`--pr-bg-card`)。
- URL 入力行: flex、align-items:center、gap:8px、border:1px solid #DDD9CF(`--pr-border-control`)、border-radius:7px、padding:7px 10px。
  - `<input type="url">`: flex:1、font-size:11.5px、枠・背景なし。プレースホルダ「URL を貼り付け — 種類を自動判定」color:#9A9EA4(`--pr-text-muted`)。
  - 「追加」ボタン: inline-flex 中央、height:22px、padding:0 11px、border-radius:5px、背景 `var(--pr-acc)`、color:#FFFFFF、font-size:10.5px、font-weight:600。
- ヘルプテキスト: font-size:9.5px、color:#9A9EA4、line-height:1.6。「GitHub・YouTube・スライド・解説記事など。タイトルとサムネイルは自動取得、ひとことメモを添えられます。」(逐語。仕様文の「〜など」禁止規則の対象外=デザイン確定文言)。

### 4.7 本画面で使用する追加トークン(plans/08 §2 に収載済み。単一ソースは 08)

extract/5a.md の実測値のうち既存トークンに一致しないものは、色をコンポーネントに直書きせず以下のトークンを使う。これらは plans/08 §2(packages/tokens css/tokens.css)に収載済みで、定義の単一ソースは plans/08 である。以下は参照用の再掲であり、トークン名・値は 08 と一致させる(乖離した場合は 08 が正)。ダーク値は 5a のダーク描画が存在しないため 08 側で確定済み。

```css
:root {
  --pr-border-dashed-suggest: #CBC7BA;      /* 5a 提案カードの破線(付録破線 #D5D1C5 とは別値) */
  --pr-official-bg: rgba(101, 148, 113, 0.16);  /* 「公式実装」バッジ面(緑 #659471 系・透過) */
  --pr-official-fg: #4C7458;                    /* 同 文字(= --pr-src-note-fg と同値だが意味が異なるため別名) */
  --pr-youtube: #B3423A;                        /* YouTube アイコン・再生ボタン(両テーマ共通) */
  --pr-article-icon-bg: rgba(88, 132, 170, 0.18);  /* 解説記事アイコン面(青 #5884AA 系・透過) */
  --pr-article-icon-fg: #4A6E8E;                   /* 同 文字 */
}
html[data-theme="dark"] {
  --pr-border-dashed-suggest: #333942;      /* 決定: ダークの付録破線と同値(1c 系統) */
  --pr-official-fg: #7FA98B;                /* 決定: ダーク本文上の視認性確保(idea 系明色) */
  --pr-article-icon-fg: #7C9FBE;            /* 決定: 同上(question 系明色) */
  /* 決定: 透過面 2 種(official-bg / article-icon-bg)と不透明 1 種(youtube)はダークでも同値のまま(再定義しない) */
}
```

### 4.8 データフィールドと表示整形(確定)

| 表示 | フィールド(plans/03 §12.1) | 整形規則(決定) |
|---|---|---|
| GitHub タイトル | `title`(= owner/repo に正規化済み) | 等幅 'IBM Plex Mono'。他 kind は通常フォント |
| メタ行(共通) | `source_label` + kind 別項目 | 「 · 」(半角スペース+中黒+半角スペース)で連結。null 項目はセグメントごと省略 |
| GitHub メタ | `meta.language` / `meta.stars` / `meta.updated_at` | 「GitHub · {language} · ★ {stars整形} · 更新 {YYYY-MM}」。stars: 1,000 未満=整数そのまま、1,000 以上=`(stars/1000)` 小数 1 桁+「k」(末尾 .0 は落とす。1200→「1.2k」、15000→「15k」。決定: 100 万以上も「k」のまま=「M」表記は導入しない。1500000→「1500k」)。updated_at(ISO 日付)→ `YYYY-MM` |
| YouTube メタ | `meta.duration_seconds` | 「YouTube · {M:SS}」。3600 秒以上は `H:MM:SS`。754→「12:34」。**「著者発表」属性は v1 では表示しない**(docs/12 §3.2 の決定: 自動判定せずフィールドを持たない。モックの「著者発表」はユーザーがタイトル/メモで補った例) |
| スライド メタ | `meta.format` / `meta.pages` | 「{source_label} · PDF · {pages} 枚」(枚の前に半角スペース) |
| 記事 メタ | `meta.reading_minutes` | 「{source_label} · 解説記事 · {reading_minutes} min」(min の前に半角スペース)。「解説記事」は固定ラベル |
| メタ取得失敗 | `meta_fetched === false` | タイトル= `url`(スキーム除去)、メタ行=「{source_label} · タイトル・メタ取得不可」。決定(plans/03 §12.1 の注記文言をそのまま採用) |
| 記事アイコン文字 | `source_label` | 先頭 1 文字を `toUpperCase()`(「zenn.dev」→「Z」)。空文字列時は「W」。決定 |
| 時間バッジ | `meta.duration_seconds` | メタ行と同一整形(「12:34」) |
| メモ | `note` | `[[sec:{section_id}\|{label}]]` を EvidenceChip(size='note')に変換、他はプレーンテキスト(改行は `<br>`、Markdown 装飾は解釈しない。決定)。表示時に先頭へ「💬 」(絵文字+半角スペース)を付加する。💬 はデータに含めない。決定 |
| 提案カード URL | `suggestion.url` | スキーム(`https://`)除去して等幅表示 |

## 5. 状態とインタラクション

### 5.1 ローディング(決定。デザイン未描画)

タブアクティブ化後、`['resources', liId]` 取得完了まで **`ResourceListSkeleton`**: カード形スケルトン 3 枚(height:62px、border-radius:8px、background:`var(--pr-bg-muted)`、`animation: alinea-pulse 1.2s ease-in-out infinite`(opacity 1→0.55→1)、gap:9px)。フッターは即時描画するが「追加」ボタンは disabled(opacity:0.5)。300ms 未満で解決した場合もフラッシュ防止はしない(単純化を優先。決定)。

### 5.2 空状態(決定。デザイン未描画)

`items.length === 0` かつ `suggestion === null` のとき `EmptyState`(共通 §5.21):

- タイトル: 「リソースはまだありません」
- 説明: 「下の入力欄に URL を貼り付けると、GitHub 実装・動画・スライド・解説記事をこの論文にひも付けられます。」
- アクションなし(追加動線はフッターが常設のため)。

`suggestion` のみ存在する場合は提案カードだけを表示(EmptyState は出さない)。

### 5.3 エラー(決定。デザイン未描画)

- 一覧取得失敗(5xx/ネットワーク): `EmptyState` タイトル「リソースを読み込めませんでした」+アクション「再試行」(`refetch()`)。
- ミューテーション失敗(§5.4〜5.9 の各操作): Toast `error`(文言は各節)+楽観更新のロールバック。

### 5.4 URL 追加フロー(フッター)

1. 入力欄で Enter または「追加」クリック。クライアント検証: 値を `trim()` した後(決定)、`new URL(value)` が成功し、プロトコルが `http:` / `https:` であること。失敗時(空文字含む)は POST せずインラインエラー(§5.6)。
2. POST 実行中: 「追加」→ 文言「追加中…」+disabled(opacity:0.5)、入力欄 disabled。サーバーがメタを同期取得(3 秒タイムアウト)するため応答まで数秒かかり得る。クライアント側の追加タイムアウトは設けない(fetch 既定に委ねる。決定)。スピナーは付けない(文言変化のみ。決定)。
3. **201**: 入力欄クリア、一覧末尾に新カード、`['viewer', liId]` invalidate(タブバッジ 4→5)、一覧を末尾へ `scrollTo({ behavior: 'smooth' })`。Toast は出さない(カード出現自体がフィードバック。決定)。`meta_fetched: false` の場合もカードは成立(§4.8 の取得不可表示)。
4. **409 duplicate**: 応答の `existing.resource_id` のカードへ `scrollIntoView({ behavior:'smooth', block:'center' })`+フラッシュ強調(§5.5)+Toast `info`「すでに追加されています」。入力値は保持(消さない)。
5. **422**: インラインエラー「URL の形式が正しくありません」(§5.6)。
6. **5xx / ネットワーク失敗(決定)**: Toast `error`「リソースを追加できませんでした」。入力値は保持し、「追加」ボタン・入力欄を通常状態へ戻す(再試行はユーザーの再クリック)。

### 5.5 重複カードのフラッシュ強調(決定)

`ResourceCardProps.flash=true` の間、カードに `outline: 1.5px solid var(--pr-acc); outline-offset: 1px` を付与。2,000ms 後に親が false へ戻し outline を除去(transition なしの即時付け外し)。フラッシュ中に同じカードへ再度 409 が来た場合はタイマーを再起動する(残り時間をリセットして再び 2,000ms。決定)。

### 5.6 フッターのインラインエラー(決定)

ヘルプテキストの位置を一時的に差し替えて表示: font-size:9.5px、line-height:1.6、color:`var(--pr-warn)`(#A05A42)。文言「URL の形式が正しくありません」。入力値の変更で消えてヘルプテキストに戻る。

### 5.7 ひとことメモの表示・編集

- **表示**: `note !== null` のとき `ResourceNoteBox`(§4.5-b)。`[[sec:…|§2.2]]` は EvidenceChip(size='note')としてレンダリングし、クリックで `onJumpSection(section_id)` → `useViewerStore().requestScroll({ kind:'section', sectionId })`(viewer-shell §2.3)。訳文モードの本文が該当セクション先頭へスクロールする。パネルは閉じない。
- **編集開始**: メモボックスのクリック(チップ・リンク以外の領域)、またはカードメニュー「メモを編集」/「メモを追加」(note null 時のラベル)。同時に開けるエディタは 1 カードのみ(決定): 別カードで編集を開始したら、開いていたエディタはキャンセル扱いで閉じる(未保存の変更は破棄。確認ダイアログなし)。
- **エディタ(`ResourceNoteEditor`。決定: デザイン未描画)**: メモボックスと同位置に `<textarea>`(rows 自動伸長、min-height:40px、font-size:11px、line-height:1.65、padding:6px 9px、border:1px solid `var(--pr-acc-m)`、border-radius:5px、background:`var(--pr-bg-comment)`、autofocus)。下部右寄せに「保存」(h20px、padding 0 9px、bg `var(--pr-acc)`、白、font 10px/600、radius 4px)と「キャンセル」(h20px、font 10px、color `var(--pr-text-muted)`)。キー: `⌘/Ctrl+Enter`=保存、`Esc`=キャンセル、Enter=改行。
- **§サジェスト**: textarea 内で「§」を入力すると `SectionSuggestPopover`(Popover width 220px、`bottom-start`、caret なし)を表示。候補は `['viewer', liId]` キャッシュの `toc` を平坦化した `number` 非 null の節(行: h28px、padding 0 10px、font 11px、「§{number} {title_ja ?? title_en}」を ellipsis)。「§」以降の続き入力で `number` 前方一致フィルタ。候補が 0 件のとき(番号付き節がない toc、またはフィルタ結果 0 件)は Popover を表示しない/既表示なら閉じる(決定)。`↓`/`↑`/`Enter` で選択、選択すると入力中の「§…」を `[[sec:{section_id}|§{number}]]` に置換してチップ記法を挿入。`Esc` で候補のみ閉じる。
- **保存**: `PATCH /api/resources/{id} { note }`(空白のみは `note: null`)。楽観更新(§2.2)。失敗時 Toast `error`「メモを保存できませんでした」。

### 5.8 カードメニュー(「⋯」。docs/12 §7 の確定 UI)

Popover(width 180px、`bottom-end`、caret なし)。各行 h30px、padding 0 12px、font 11.5px、color `var(--pr-text-mid)`、hover bg `var(--pr-bg-hover)`。項目(順序固定):

1. 「メモを編集」/ note null 時「メモを追加」→ §5.7 のエディタを開く。
2. 「タイトルを編集」→ タイトルをインライン `<input>` 化(font-size:12px、font-weight:600、border-bottom:1px solid `var(--pr-acc-m)` のみ、Enter=保存で `PATCH { title }`、Esc=キャンセル。決定)。保存値は `trim()` し、空白のみのときは PATCH を発行せずキャンセル扱い(元タイトルに戻す。決定)。
3. 「種類を変更」→ サブ選択(同 Popover 内で 4 行に差し替え): GitHub 実装 / YouTube 動画 / スライド(PDF)/ 解説記事。現在値は bg `var(--pr-acc-s)`+weight 600。選択で `PATCH { kind }`(アイコン・メタ行の表示形が即時切替)。
4. 「メタを再取得」→ `POST /api/resources/{id}/refresh-meta`。実行中は行を disabled。200 で一覧 invalidate、失敗時 Toast `error`「メタ情報を取得できませんでした」。
5. 区切り線(1px `var(--pr-border-hair)`)+「削除」(color `var(--pr-warn)`)→ §5.9。

### 5.9 削除と「元に戻す」(plans/02 §1 の遅延実行方式)

1. 「削除」クリックで即座にカードを一覧から非表示(クライアント状態)+タブバッジを -1 表示(`['viewer']` キャッシュの `setQueryData`)。
2. Toast(action 付き・6,000ms): 「リソースを削除しました」+アクション「元に戻す」。
3. 「元に戻す」→ DELETE を発行せずカード・バッジを復元。
4. 6,000ms 経過(または後続 Toast による置換)→ `DELETE /api/resources/{resource_id}` を発行。204 で確定、失敗時はカード復元+Toast `error`「削除できませんでした」。
5. トースト保留中のページ離脱(`pagehide`)時は `fetch(url, { method: 'DELETE', keepalive: true, credentials: 'include' })` で DELETE を確定送信する(`navigator.sendBeacon` は POST 限定のため使わない。決定)。送信結果は検証しない(fire-and-forget)。

### 5.10 公式実装提案カード

- **「+ 追加」**: `POST …/resource-suggestion/accept`。楽観更新: 提案カードを除去し、`official: true`・kind='github' の仮カードを一覧末尾に追加(タイトル= `suggestion.url` のスキーム除去表示=§4.8 の規則、メタ行「GitHub」のみ)。201 応答の ResourceLink で置換+`['viewer']` invalidate(バッジ +1)。失敗時は提案カードを復元+Toast `error`「追加できませんでした」。
- **「無視」**: `POST …/resource-suggestion/dismiss`。楽観更新: カードを即除去(バッジは不変=提案は数えない)。204 で確定。失敗時は復元+Toast `error`「操作に失敗しました」。無視は永続で、再取り込み後も再提案されない(サーバー側 `resource_links.status='dismissed'` 行が担保)。
- 実行中(`pending`)は両ボタン disabled(opacity:0.5)。確認ダイアログは両操作とも出さない(1 クリック確定。docs/12 §5)。

### 5.11 「開く ↗」とサムネイル

- 「開く ↗」: `<a href={resource.url} target="_blank" rel="noopener noreferrer">`。hover 時 `text-decoration: underline`。決定。
- YouTube サムネイルの再生ボタン・サムネイル面クリック: 同じく `url` を新規タブで開く(埋め込みプレーヤーは持たない。docs/12 §6)。カーソルは `pointer`。
- サムネイル画像の読み込み失敗(`onError`): `<img>` を除去しダークプレースホルダ(#26292E+再生ボタン)にフォールバック。決定。

### 5.12 ホバー・フォーカス状態(決定。デザイン未描画)

- カード面: hover での背景変化なし(「⋯」の出現のみ。§4.5)。
- 「+ 追加」「追加」プライマリボタン hover: `filter: brightness(0.94)`(plans/08 の焦点色を汚さない範囲の共通手当)。テキストボタン「無視」hover: color `var(--pr-text-sub)`。
- すべてのインタラクティブ要素の `focus-visible`: `outline: 1.5px solid var(--pr-acc); outline-offset: 1px`(plans/08 §5 共通規約)。
- EvidenceChip(note)hover: bg `var(--pr-acc-s)`。決定。

### 5.13 状態遷移一覧(タブ本体)

```
[loading(スケルトン)] ─取得成功→ [一覧(提案あり/なし × 0〜N件)]
                      └取得失敗→ [エラー EmptyState「再試行」]
[一覧] ─URL追加(201)→ [一覧+1(末尾スクロール)]
       ─URL追加(409)→ [一覧(既存カードへフラッシュ 2,000ms)]
       ─URL追加(422/検証NG)→ [一覧+インラインエラー]
       ─提案「+ 追加」→ [提案除去+公式カード追加]
       ─提案「無視」→ [提案除去]
       ─メモ編集開始→ [カード内エディタ] ─保存/キャンセル→ [一覧]
       ─削除→ [カード非表示+Undoトースト] ─元に戻す→ [一覧] / ─6s経過→ DELETE 確定
```

## 6. 受け入れ基準

ピクセル一致検証(ビジュアルリグレッション対象。Playwright スクリーンショット、ビューポート 1440×900、ライトテーマ・アクセント既定 #3E5C76、モックデータは extract/5a.md の逐語値):

- [ ] サイドパネルが 340px・タブ行で「リソース」がアクティブ(weight 600・アクセント色・inset 0 -2px 下線)、カウント「4」がアクセント継承色、「注釈」に「6」(#9A9EA4)で表示される
- [ ] 一覧本体が bg #FCFBF8・padding 12px・gap 9px で、提案カード→GitHub→YouTube→スライド→解説記事の順に描画される
- [ ] 提案カードが border 1px dashed #CBC7BA・radius 8px・bg #FBFAF7 で、「✦ 公式実装を検出しました」(アクセント色 700)+「 — arXiv ページのリンクから」+等幅 10.5px #5B6067 の「github.com/gnobitab/RectifiedFlow」+「+ 追加」(h22px・bg アクセント・白 10.5px/600)+「無視」(#9A9EA4)が逐語一致する
- [ ] GitHub カード: 「GH」アイコン 26×26px bg #26292E、等幅 12px/600 の「gnobitab/RectifiedFlow」、「公式実装」バッジ(h15px、bg rgba(101,148,113,0.16)、#4C7458、8.5px/700)、メタ「GitHub · Python · ★ 1.2k · 更新 2023-11」(10px #9A9EA4)、「開く ↗」(11px アクセント 600)
- [ ] GitHub カードのメモ欄が bg #F7F5EF・radius 5px・padding 6px 9px・11px/1.65 #3C4046 で「💬 train_reflow.py が §2.2 の手順に対応。データペアの再生成は generate_data.py」を表示し、「§2.2」チップが h15px・border 1px rgba(62,92,118,0.32)・radius 3px・9px/600 アクセント色である
- [ ] YouTube カード: 「▶」アイコン bg #B3423A、タイトル・メタ逐語一致、サムネイル h96px radius 5px bg #26292E、中央再生ボタン 34×24px bg #B3423A、右下バッジ「12:34」(9px 白・bg rgba(0,0,0,0.7)・等幅)、メモ「💬 8:20〜 reflow の直感的な説明が分かりやすい」
- [ ] スライド/解説記事カードが 1 行構成(横 flex)で、「PDF」アイコン(bg rgba(196,148,50,0.18)・#8A6A24)/「Z」アイコン(bg rgba(88,132,170,0.18)・#4A6E8E)、メタ「iclr.cc · PDF · 24 枚」「zenn.dev · 解説記事 · 15 min」が逐語一致する
- [ ] フッター: border-top 1px #ECE9DF、入力行(border 1px #DDD9CF・radius 7px・padding 7px 10px)、プレースホルダ「URL を貼り付け — 種類を自動判定」、「追加」ボタン(h22px・アクセント)、ヘルプ文 9.5px #9A9EA4 が逐語一致する
- [ ] シェル部(ヘッダ・左レール・本文 680px カラム・ハイライト・「表1」dotted リンク)が §4.1〜4.3 の実測値と一致する(viewer-shell / 1b の VRT と共有)
- [ ] ダークテーマで本画面の追加トークン 6 種(§4.7。定義は plans/08 §2 が単一ソース)が適用され、透過面 3 種は同値のまま崩れない

機能検証:

- [ ] `/papers/{li_id}?panel=resources` でリソースタブがアクティブな状態で開き、クエリが URL から除去される
- [ ] タブ初回アクティブ化で `GET /api/library-items/{id}/resources` が呼ばれ、取得中はスケルトン 3 枚、0 件時は「リソースはまだありません」、失敗時は「再試行」付きエラーが出る
- [ ] GitHub / YouTube / PDF / 任意ブログの URL 追加がそれぞれ `github` / `youtube` / `slides` / `article` カードになり(判定はサーバー)、追加中は「追加中…」で二重送信できない
- [ ] 409 応答で既存カードへスクロール+2 秒フラッシュ+Toast「すでに追加されています」、不正 URL でインラインエラー「URL の形式が正しくありません」が出る
- [ ] `meta_fetched: false` のリソースがタイトル= URL・メタ行「タイトル・メタ取得不可」で成立し、メニュー「メタを再取得」で復旧できる
- [ ] メタ整形が §4.8 の規則どおり(stars 1200→「1.2k」、duration 754→「12:34」、null 項目はセグメント省略、YouTube に「著者発表」を表示しない)
- [ ] メモの追加・編集・削除(空保存= null)が PATCH で保存され、「§」入力でセクションサジェストが開き、確定チップのクリックで本文該当セクションへジャンプする
- [ ] 提案カード「+ 追加」で `official: true` の「公式実装」バッジ付きカードになりバッジ件数が +1、「無視」でカードが消え件数は不変、リロード・再取り込み後も再提案されない
- [ ] カードメニューからタイトル編集・種類変更ができ、削除は Undo トースト(6 秒)で取り消せる。取り消さなければ DELETE が確定する
- [ ] 「開く ↗」・サムネイル・再生ボタンが新規タブ(`noopener noreferrer`)で外部 URL を開く
- [ ] 追加/削除の成功でタブ件数バッジ(`viewer.counts.resources`)が同期し、提案カードは件数に数えられない
- [ ] キーボードのみで全操作(追加・メニュー・メモ編集・提案の確定/却下)が可能で、`focus-visible` リングが表示される
