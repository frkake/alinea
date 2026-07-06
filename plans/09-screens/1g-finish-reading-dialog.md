# 画面 1g: 読了フロー(モーダル)

> **対象読者と前提**: 本書は「訳読 / YAKUDOKU」の画面 1g(読了フロー — 軽量ダイアログ、仕様上の識別子 S3)を実装するフロントエンドエンジニア向けの実装仕様である。ピクセル仕様の正は確定デザイン抽出 `extract/1g.md`、機能仕様の正は [docs/06-library.md](../../docs/06-library.md) §3、API 名は [plans/03-api.md](../03-api.md)、共通コンポーネント名・トークン名は [plans/08-design-system.md](../08-design-system.md) に従う。技術スタックは確定済み(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4 + TanStack Query v5 + Zustand)。

## 1. 概要とルート

- **本画面は独立ルートを持たないグローバルモーダル**である。Next.js のページではなく、認証済みレイアウト `apps/web/src/app/(app)/layout.tsx` に常駐する `<FinishReadingDialogHost />` が描画する。
- **決定**: 認証済み画面のどこであっても(ビューア 1a/1b/1c/2a/1h、ライブラリ 1e/4a、ダッシュボード 1d、コレクション 4b)、単一 LibraryItem のステータスが UI 操作で `done`(読んだ)に変更されて API が成功した時点で開く。理由: docs/06 §3 は「ステータスを『読んだ』に変更すると出す」とだけ定め、画面を限定していないため、トリガを 1 箇所(ホスト)に集約する。
- 確定デザインのフレームはビューア(1b 訳文モード)の上に開いた状態を描いている。背景ビューアのルートは `/papers/[itemId]`(viewer-shell §3.1 で確定)。ビジュアルリグレッションはこのルート上で撮る(§6)。
- 認証: 必須(セッションクッキー)。未認証ではホスト自体がマウントされない。
- 画面の役割: 読了直後に (1) 読了日・累計読書時間の自動記録を確認させ、(2) 理解度(1〜5)・重要度(低/中/高)・ひとことメモを**任意で**入力させ、(3) チャット要約のメモ昇格と記事モードへの導線を提示する。**全項目スキップ可・入力を強制しない**(P6)。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 用途 | 呼び出しタイミング |
|---|---|---|---|
| 1 | `PATCH /api/library-items/{id}`(03 §5.4) | (a) `{ status: "done" }` — ダイアログを開く**前提操作**(StatusPill 側が実行)。(b) `{ comprehension, importance, one_line_note }` — 「保存」クリック時 | (a) ステータス変更時 (b) 保存クリック時 |
| 2 | `GET /api/library-items/{id}/chat/threads`(03 §10.1) | メインスレッド(`is_main: true`)の ID 取得 | ダイアログ open 時にプリフェッチ |
| 3 | `GET /api/chat/threads/{thread_id}/messages`(03 §10.2) | 既存の「詳細要約」(`quick_action: "detailed_summary"`)応答の有無を判定 | #2 解決後にプリフェッチ(`limit=50`) |
| 4 | `POST /api/chat/threads/{thread_id}/messages`(03 §10.3, SSE) | 詳細要約が未生成の場合の生成(`{ content: "", quick_action: "detailed_summary" }`) | カード「✦ 要約をメモに保存」クリック時(未生成時のみ) |
| 5 | `POST /api/library-items/{id}/notes`(03 §9) | 詳細要約応答のメモ昇格(`{ content_md, source_message_id }`。anchors はサーバーが複写) | カードクリック時(生成済み)/ #4 の `done` 受信直後 |

- ダイアログ本体の表示データ(`finished_at` / `reading_seconds_total` / `comprehension` / `importance` / `one_line_note`)は、開く直前の `PATCH`(#1a)のレスポンス `LibraryItemSummary` をそのまま使う。**ダイアログ自身は初期表示のための GET を発行しない**(ローディング状態が原理的に発生しない設計)。
- リアルタイム更新: 不要。**決定**: SSE 購読・ポーリングともに行わない。理由: 表示値はすべて開いた瞬間のスナップショットで足り、開いている数十秒の間に他デバイスから変わる値を追う要件がない。

### 2.2 TanStack Query キー設計

```ts
// apps/web/src/queries/keys.ts(該当分)
export const qk = {
  libraryItem: (id: string) => ['library-item', id] as const,
  libraryItems: () => ['library-items'] as const,             // 一覧系のプレフィックス
  chatThreads: (libraryItemId: string) => ['chat', 'threads', libraryItemId] as const,
  chatMessages: (threadId: string) => ['chat', 'messages', threadId] as const,
} as const;
```

- open 時プリフェッチ: `queryClient.prefetchQuery({ queryKey: qk.chatThreads(item.id), staleTime: 30_000 })` → 解決後 `qk.chatMessages(mainThreadId)` を同様にプリフェッチ。
- 「保存」成功時の無効化(**決定**): `invalidateQueries({ queryKey: qk.libraryItem(id) })` と `invalidateQueries({ queryKey: qk.libraryItems() })`(テーブル 1e の理解度・重要度列、ダッシュボード統計に反映)。加えて `setQueryData(qk.libraryItem(id), response)` で即時反映。
- メモ昇格成功時: `invalidateQueries({ queryKey: ['notes', item.id] })`(サイドパネル「メモ」タブの一覧キー。画面 1a 仕様と共通)。

### 2.3 クライアント状態(Zustand)

```ts
// apps/web/src/stores/finish-reading.ts
import { create } from 'zustand';
import type { LibraryItemSummary } from '@yakudoku/api-client';

interface FinishReadingStore {
  item: LibraryItemSummary | null;      // null = 閉じている
  open: (item: LibraryItemSummary) => void;   // PATCH {status:"done"} 成功レスポンスを渡す
  close: () => void;
}
export const useFinishReadingStore = create<FinishReadingStore>((set) => ({
  item: null,
  open: (item) => set({ item }),
  close: () => set({ item: null }),
}));
```

- 発火規約(**決定**): `StatusPill`(08 §5.2)の `onChange` を処理する共通ミューテーションフック `usePatchLibraryItem` が、`prev.status !== 'done' && next === 'done'` かつ PATCH 成功のとき `useFinishReadingStore.getState().open(response)` を呼ぶ。通知 4a の `status_suggestion`「変更する」も同フック経由のため同条件で開く。**一括操作(`POST /api/library-items/bulk`)では開かない**。

## 3. コンポーネント分解

```
apps/web/src/app/(app)/layout.tsx
└─ FinishReadingDialogHost                    … 画面固有(ストア購読のみ)
   └─ Modal (08 §5.11, width=460, labelledBy="finish-dialog-title")   … 共通
      └─ FinishReadingDialog                  … 画面固有
         ├─ FinishDialogHeader                … 画面固有(✓バッジ+タイトル+×+メタ行)
         ├─ ComprehensionDots                 … 画面固有(理解度 1–5)
         ├─ SegmentedControl (08 §5.1, size='md-wide')                … 共通(重要度)
         ├─ OneLineNoteInput                  … 画面固有(textarea)
         ├─ FollowupActionCard ×2             … 画面固有(導線カード)
         └─ FinishDialogFooter                … 画面固有(すべてスキップ+保存)
```

共通コンポーネント(plans/08 の名前をそのまま使用): `Modal`(§5.11)/ `SegmentedControl`(§5.1)/ Toast は `useToast()`(§5.20)。
**決定**: 重要度セグメントは抽出値が padding 0 16px(08 §5.1 の `md`=0 11px と不一致)のため、`SegmentedControl` に size `'md-wide'`(h24px・padding 0 16px・font 11.5px・radius 5px)を追加して使う。**決定**: 1g 実装 PR で 08 §5.1 の寸法表と `size?: 'sm' | 'md' | 'md-wide' | 'lg'` を同時更新する(本項が追記の根拠)。画面固有の複製は作らない。

画面固有コンポーネントの props(配置: `apps/web/src/components/finish-reading/`):

```ts
// FinishReadingDialog.tsx
interface FinishReadingDialogProps {
  item: LibraryItemSummary;            // PATCH {status:"done"} のレスポンス
  onClose: () => void;
}

// ComprehensionDots.tsx
interface ComprehensionDotsProps {
  value: number | null;                // 1–5、null=未選択
  onChange: (value: number | null) => void;
}

// OneLineNoteInput.tsx — forwardRef<HTMLTextAreaElement> で textarea を公開する(初期フォーカス用)
interface OneLineNoteInputProps {
  value: string;
  onChange: (value: string) => void;
}
// 決定: 初期フォーカスの機構は Modal の `initialFocusRef` に一本化する(DOM autoFocus 属性は使わない)。
// `initialFocusRef?: React.RefObject<HTMLElement>`(指定時は「最初のフォーカサブル」より優先)は
// plans/08 §5.11 の ModalProps に定義済み。
// FinishReadingDialog が OneLineNoteInput の ref を Modal の initialFocusRef へ渡す。

// FollowupActionCard.tsx
type FollowupCardState = 'idle' | 'loading' | 'done' | 'error';
interface FollowupActionCardProps {
  title: string;                       // 「✦ 要約をメモに保存」/「記事モードで読み返す →」
  description: string;
  state: FollowupCardState;
  onClick: () => void;
}
```

補助関数(`apps/web/src/lib/format.ts` に追加):

```ts
/** 3時間12分 / 42分 / 1分未満。0 秒は null を返す(メタ行から時間部分を省く) */
export function formatReadingDuration(totalSeconds: number): string | null {
  if (totalSeconds <= 0) return null;
  if (totalSeconds < 60) return '1分未満';
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  return h > 0 ? `${h}時間${m}分` : `${m}分`;
}

/** finished_at (ISO 8601 UTC) → 端末ローカルの "YYYY-MM-DD" */
export function formatFinishedDate(iso: string): string; // 実装は Intl.DateTimeFormat('sv-SE')

export const COMPREHENSION_LABELS: Record<1 | 2 | 3 | 4 | 5, string> = {
  1: 'ほぼ分からなかった',
  2: 'ところどころ分かった',
  3: '半分くらい追えた',
  4: 'だいたい追えた',
  5: '完全に理解した',
};  // docs/06 §3 の確定ラベル。表示形式「n/5 — ラベル」
```

## 4. レイアウト・スタイル完全仕様

出典: `extract/1g.md`(確定デザイン逐語)。トークン名は plans/08 §2.1 の対応値を併記する。実装ではトークンを参照し、直値の記述は禁止(08 §7.3 の規則に同じ)。

### 4.0 デザイナー注記(逐語・実装対象外のカタログ表記)

- バッジ「1g」: インラインブロック、min-width:32px、height:22px、背景 #2B2E33、文字 #FFFFFF、border-radius:6px、font-size:12px、font-weight:700、`#1g` へのアンカーリンク、中央揃え
- タイトル(太字): 「読了フロー — 軽量ダイアログ(S3)」(font-size:15px、font-weight:700、色 #1E2227)
- 説明文(グレー): 「全項目スキップ可・入力を強制しない / 読了日と読書時間は自動 / チャット→メモ昇格への導線」(font-size:12px、色 #777B81)
- 注記行レイアウト: display:flex、align-items:baseline、gap:10px、margin-bottom:12px
- ラッパ `div#1g` の data 属性: `data-screen-label="1g 読了フロー"`、width:1440px
- HTML コメント(逐語): `<!-- 背景のビューア(簡略) -->`、`<!-- 読了ダイアログ -->`
- フレーム外の別状態バリエーションは存在しない(フレーム内で完結)。

### 4.1 フレームとレイヤ構成

フレーム: 1440×840px、背景 #FBFAF7(`--pr-bg-app`)、border:1px solid #D6D3C9(`--pr-border-frame`)、border-radius:10px、box-shadow:0 20px 44px rgba(28,30,34,0.12)(`--pr-shadow-frame`)、overflow:hidden、position:relative、文字色 #1E2227(`--pr-text`)。
(※他画面のフレームは 1440×900 だが、この画面のフレーム height は 840px。実装ではフレームはカタログ用であり、アプリでは基準ビューポート 1440×900 のビューア上に fixed オーバーレイで開く)

```
┌────────────────────────────────────────────────────────────── 1440×840 ─┐
│ ┌─ ビューアヘッダ 52px 高 / 白 #FFFFFF / 下線 1px #E6E3DA ────────────┐ │
│ │ ‹  論文タイトル  [A]  (●読んだ ▾)  …spacer…                        │ │
│ │ [訳文|対訳|原文|PDF|記事]  [スタイル: 自然訳 ▾] [🔍この論文内を検索 /] ⋯│ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ ┌─ 本文領域(flex:1、中央寄せ、padding-top:34px)────────────────────┐ │
│ │        720px 幅カラム(Noto Serif JP)                               │ │
│ │        「5 結論 — Conclusion」見出し + 段落×2                        │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ ███ 全面オーバーレイ rgba(30,32,36,0.38)(inset:0)█████████████████████ │
│                                                                          │
│            ┌── 読了ダイアログ 460px 幅 / 画面中央 ──┐                   │
│            │ ヘッダ: ✓ 「読んだ」にしました    ×    │                   │
│            │  読了日 … · 累計読書時間 …(自動記録)   │                   │
│            │ 本体: 理解度 ●●●●○  4/5 — だいたい追えた│                   │
│            │       重要度 [低|中|高*]                 │                   │
│            │       ひとことメモ — 何に使えるか        │                   │
│            │       [テキスト入力ボックス(入力済み)] │                   │
│            │       [✦要約をメモに保存][記事モードで→]│                   │
│            │ フッタ: すべてスキップ …… [保存]        │                   │
│            └──────────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────────────────────┘
```

レイヤ構成(デザインはいずれも position:absolute。実装ではスクリムとダイアログを `position: fixed` + `z-index: var(--z-modal)`(=8)にする — 08 §5.11 Modal の規定どおり):

1. 背景ビューア: inset:0、display:flex、flex-direction:column(ヘッダ 52px 固定 + 本文 flex:1)— **画面 1b そのもの**。本画面の実装対象はレイヤ 2・3 のみ
2. スクリムオーバーレイ: inset:0、background:rgba(30,32,36,0.38)(`--pr-scrim`)
3. 読了ダイアログ: top:50%、left:50%、transform:translate(-50%,-50%)、width:460px

### 4.2 背景ビューア — ヘッダバー(1b の該当状態。1g では「参照仕様」)

- コンテナ: height:52px、flex:none、background:#FFFFFF(`--pr-bg-card`)、border-bottom:1px solid #E6E3DA(`--pr-border-header`)、display:flex、align-items:center、gap:10px、padding:0 16px
- 戻る矢印: テキスト「‹」、font-size:16px、色 #8A8E94(`--pr-text-icon`)、width:20px、text-align:center
- 論文タイトル: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」、font-size:13px、font-weight:600、max-width:330px、white-space:nowrap、overflow:hidden、text-overflow:ellipsis
- 「A」バッジ: inline-flex 中央揃え、18×18px、border-radius:4px、background:var(--pr-as)(=rgba(62,92,118,0.10))、color:var(--pr-a)(=#3E5C76)、font-size:10.5px、font-weight:700、テキスト「A」→ 共通 `QualityBadge`(08 §5.3)
- ステータスピル「読んだ ▾」: inline-flex、align-items:center、gap:5px、height:24px、padding:0 9px、border:1px solid #DDD9CF(`--pr-border-control`)、border-radius:999px、font-size:11.5px、font-weight:500、background:#FFFFFF(`--pr-bg-control`)。内包: 緑ドット(7×7px、border-radius:50%、background:#659471=`--pr-status-done`)+テキスト「読んだ」+「▾」(色 #9A9EA4=`--pr-text-muted`、font-size:9px)→ 共通 `StatusPill`(08 §5.2、`status='done'` 表示・`interactive`)
- スペーサ: flex:1
- 表示モードセグメント: コンテナ display:flex、background:#EFEDE6(`--pr-bg-muted`)、border-radius:7px、padding:2px、gap:2px。各セグメント: height:24px、inline-flex、align-items:center、padding:0 11px、border-radius:5px、font-size:11.5px → 共通 `SegmentedControl`(size='md')
  - 「訳文」(選択中): background:#FFFFFF(`--pr-bg-seg-selected`)、color:#1E2227、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)(`--pr-shadow-seg`)
  - 「対訳」「原文」「PDF」「記事」(非選択): color:#5B6067(`--pr-text-sub`)、背景なし
- スタイルセレクタ: 「スタイル: 自然訳 ▾」— inline-flex、gap:5px、height:26px、padding:0 10px、border:1px solid #DDD9CF、border-radius:6px、font-size:11.5px、color:#3C4046(`--pr-text-mid`)。「▾」は色 #9A9EA4、font-size:9px
- 検索ボックス: inline-flex、gap:6px、height:26px、padding:0 10px、background:#F1EFE9(`--pr-bg-inset`)、border-radius:6px、font-size:11.5px、color:#8A8E94、width:150px → 共通 `SearchBox`(variant='in-paper')
  - 虫眼鏡 SVG アイコン: 11×11(viewBox 0 0 12 12)、circle cx=5 cy=5 r=3.6 stroke:currentColor stroke-width:1.3 + 斜め線 path M8 8l2.6 2.6 stroke-linecap:round
  - プレースホルダテキスト「この論文内を検索」
  - キーキャップ「/」: margin-left:auto、border:1px solid #DAD7CD(`--pr-border-keycap`)、border-radius:3px、padding:0 4px、font-size:9.5px、background:#FFFFFF
- オーバーフローメニュー: テキスト「⋯」、font-size:15px、色 #5B6067、letter-spacing:1px

### 4.3 背景ビューア — 本文領域(1b の該当状態。1g では「参照仕様」)

- 外枠: flex:1、display:flex、justify-content:center、padding-top:34px
- 本文カラム: width:720px、font-family:var(--pr-jp, 'Noto Serif JP'), serif
- セクション見出し: font-family:'IBM Plex Sans JP', sans-serif、font-size:19px、font-weight:700、margin-bottom:14px。テキスト「5 結論 」+ 英語併記 span「— Conclusion」(color:#8A8E94、font-weight:400、font-size:14px、font-family:'Source Serif 4', Georgia, serif、font-style:italic)
- 段落1: font-size:16px、line-height:2.1、color:#24272B(`--pr-text-body`)、margin-bottom:18px。テキスト「本研究では、2分布間の輸送を直線に近い経路で学習する整流フローを提案した。reflow による再帰的な直線化は輸送コストを単調に減少させ、極少ステップでの高品質な生成を可能にする。」
- 段落2: font-size:16px、line-height:2.1、color:#24272B。テキスト「今後の課題としては、より大規模なデータセットへの拡張、および条件付き生成への応用が挙げられる。」

### 4.4 スクリムオーバーレイ

- position:absolute(実装: fixed)、inset:0、background:rgba(30,32,36,0.38)(`--pr-scrim`)。背景ビューア全面を覆う。共通 `Modal` が描画する。

### 4.5 読了ダイアログ(モーダル本体)

- コンテナ: position:absolute(実装: fixed)、top:50%、left:50%、transform:translate(-50%,-50%)、width:460px、background:#FFFFFF(`--pr-bg-card`)、border-radius:14px、box-shadow:0 32px 80px rgba(20,22,26,0.35)(`--pr-shadow-modal`)、overflow:hidden → 共通 `Modal`(width=460 既定)

#### 4.5.1 ヘッダ部(FinishDialogHeader)

- padding:20px 24px 0、display:flex、flex-direction:column、gap:4px
- 1 行目: display:flex、align-items:center、gap:9px
  - チェックバッジ: inline-flex 中央揃え、24×24px、border-radius:50%(正円)、background:rgba(101,148,113,0.16)(**決定**: 同値の `--pr-src-note-bg` を参照する。直値を書かない)、color:#4C7458(同じく `--pr-src-note-fg`)、font-size:12px、font-weight:700、テキスト「✓」
  - タイトル: 「「読んだ」にしました」、font-size:15px、font-weight:700。`id="finish-dialog-title"`(Modal の `labelledBy` 対象)
  - 閉じるボタン: 「×」、margin-left:auto、font-size:14px、色 #9A9EA4(`--pr-text-muted`)。`aria-label="閉じる"`、クリック領域は 24×24px(**決定**: 視覚は文字のまま、`<button>` の当たり判定のみ拡大)
- 2 行目(メタ情報): 「読了日 2026-07-06 · 累計読書時間 3時間12分(自動記録)」、font-size:11px、色 #9A9EA4、padding-left:33px(チェックバッジ幅 24px + gap 9px に揃えたインデント)
  - データ結合: `読了日 {formatFinishedDate(item.finished_at)} · 累計読書時間 {formatReadingDuration(item.reading_seconds_total)}(自動記録)`。`formatReadingDuration` が null(0 秒)の場合は「読了日 {日付}(自動記録)」(**決定**: 計測 OFF ユーザーで「0分」を出さない)

#### 4.5.2 本体部

- padding:18px 24px、display:flex、flex-direction:column、gap:16px

**行 1 理解度(ComprehensionDots)**:
- 行: display:flex、align-items:center、gap:12px
- ラベル「理解度」: font-size:11.5px、font-weight:600、色 #5B6067(`--pr-text-sub`)、width:56px
- ドット群: display:flex、gap:6px、align-items:center
  - 塗りドット(選択値以下、デザインでは ×4): 各 22×22px、border-radius:50%、background:var(--pr-a)(実装は `--pr-acc`。ダーク追随)
  - 空ドット(選択値超、デザインでは ×1): 22×22px、border-radius:50%、border:1.5px solid #D5D1C5(`--pr-border-dashed` と同値。同トークンを参照)、box-sizing:border-box、背景なし
  - 補足テキスト: 「4/5 — だいたい追えた」、font-size:11px、色 #777B81(`--pr-text-sub2`)、margin-left:4px。形式「{value}/5 — {COMPREHENSION_LABELS[value]}」
- a11y(**決定**): `role="radiogroup"` `aria-label="理解度"`、各ドット `role="radio"` `aria-checked` `aria-label="{n}/5 — {ラベル}"`。左右矢印キーで移動

**行 2 重要度(SegmentedControl size='md-wide')**:
- 行: display:flex、align-items:center、gap:12px
- ラベル「重要度」: font-size:11.5px、font-weight:600、色 #5B6067、width:56px
- セグメントコントロール: display:flex、background:#EFEDE6(`--pr-bg-muted`)、border-radius:7px、padding:2px、gap:2px。各セグメント height:24px、inline-flex、align-items:center、padding:0 16px、border-radius:5px、font-size:11.5px
  - 「低」「中」(非選択): color:#5B6067
  - 「高」(選択中): background:#FFFFFF(`--pr-bg-seg-selected`)、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)(`--pr-shadow-seg`)
- options: `[{value:'low',label:'低'},{value:'mid',label:'中'},{value:'high',label:'高'}]`(API 列挙 `Importance`)

**行 3 ひとことメモ(OneLineNoteInput)**:
- ブロック: display:flex、flex-direction:column、gap:6px
- ラベル: 「ひとことメモ 」(font-size:11.5px、font-weight:600、色 #5B6067)+ 補足 span「— 何に使えるか」(color:#9A9EA4、font-weight:400)
- 入力ボックス(入力済み状態): border:1px solid #DDD9CF(`--pr-border-control`)、border-radius:8px、padding:10px 12px、font-size:12.5px、line-height:1.7、color:#24272B(`--pr-text-body`)
  - 入力テキスト(デザイン例): 「reflow は蒸留の前処理として有効。うちの T2I 蒸留パイプラインで試す。実装は Appendix D 参照。」
  - テキスト末尾のキャレット(デザインの疑似カーソル: inline-block、width:1px、height:13px、background:var(--pr-a)、vertical-align:-2px)は**フォーカス中 textarea の実キャレット**として実装する: `caret-color: var(--pr-acc)`
  - **決定**: 実装は `<textarea>`(自動リサイズ、min 1 行=約 21px、max 5 行=約 106px、resize:none、outline:none、placeholder なし — ラベル補足「— 何に使えるか」が入力ガイドのため)。フォーカス時 border:1px solid var(--pr-acc)(デザイン未描画のフォーカス表現。SearchBox の様式に合わせ控えめに枠色のみ変更)

**行 4 導線カード 2 枚(FollowupActionCard ×2)**:
- 行: display:flex、gap:8px。各カード: flex:1、border:1px solid #E2DFD5(`--pr-border-card`)、border-radius:8px、padding:10px 12px、display:flex、flex-direction:column、gap:3px、background:#FBFAF7(`--pr-bg-app`)
- カード 1:
  - タイトル: 「✦ 要約をメモに保存」、font-size:11.5px、font-weight:600、color:var(--pr-acc)(=#3E5C76)
  - 説明: 「チャットの「詳細要約」を根拠つきで保存」、font-size:10px、color:#9A9EA4(`--pr-text-muted`)、line-height:1.5
- カード 2:
  - タイトル: 「記事モードで読み返す →」、font-size:11.5px、font-weight:600、color:var(--pr-acc)
  - 説明: 「メモとチャットから読み物を自動構成」、font-size:10px、color:#9A9EA4、line-height:1.5
- **決定**: カードは `<button>`(全面クリック可、cursor:pointer、text-align:left)

#### 4.5.3 フッタ部(FinishDialogFooter)

- padding:14px 24px、border-top:1px solid #F0EDE4(`--pr-border-hair`)、display:flex、align-items:center
- 左: テキストリンク「すべてスキップ」、font-size:11.5px、色 #9A9EA4(`--pr-text-muted`)。`<button>`
- 右: プライマリボタン「保存」、margin-left:auto、inline-flex、align-items:center、height:32px、padding:0 18px、border-radius:7px、background:var(--pr-acc)(=#3E5C76)、color:#FFFFFF、font-size:12.5px、font-weight:600

### 4.6 全 UI 文言(逐語)

デザイナー注記(カタログのみ): 「1g」/「読了フロー — 軽量ダイアログ(S3)」/「全項目スキップ可・入力を強制しない / 読了日と読書時間は自動 / チャット→メモ昇格への導線」

背景ビューア(ヘッダ): 「‹」/「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」/「A」/「読んだ」/「▾」(ステータスピル内)/「訳文」/「対訳」/「原文」/「PDF」/「記事」/「スタイル: 自然訳」/「▾」(スタイルセレクタ内)/「この論文内を検索」/「/」(検索ショートカットキーキャップ)/「⋯」

背景ビューア(本文): 「5 結論」/「— Conclusion」/「本研究では、2分布間の輸送を直線に近い経路で学習する整流フローを提案した。reflow による再帰的な直線化は輸送コストを単調に減少させ、極少ステップでの高品質な生成を可能にする。」/「今後の課題としては、より大規模なデータセットへの拡張、および条件付き生成への応用が挙げられる。」

読了ダイアログ: 「✓」/「「読んだ」にしました」/「×」/「読了日 2026-07-06 · 累計読書時間 3時間12分(自動記録)」/「理解度」/「4/5 — だいたい追えた」/「重要度」/「低」/「中」/「高」/「ひとことメモ」/「— 何に使えるか」/「reflow は蒸留の前処理として有効。うちの T2I 蒸留パイプラインで試す。実装は Appendix D 参照。」/「✦ 要約をメモに保存」/「チャットの「詳細要約」を根拠つきで保存」/「記事モードで読み返す →」/「メモとチャットから読み物を自動構成」/「すべてスキップ」/「保存」

### 4.7 データフィールド対応表

| 表示 | データソース(plans/03 の型) |
|---|---|
| ダイアログタイトル | 固定文言 |
| 読了日 | `LibraryItemSummary.finished_at`(status→done で自動記録。03 §5.4)→ `formatFinishedDate` |
| 累計読書時間 | `LibraryItemSummary.reading_seconds_total`(ReadingSession 集計)→ `formatReadingDuration` |
| 理解度 | `LibraryItemSummary.comprehension`(1–5 / null)。ラベルは `COMPREHENSION_LABELS` |
| 重要度 | `LibraryItemSummary.importance`(`low`/`mid`/`high` / null) |
| ひとことメモ | `LibraryItemSummary.one_line_note` |
| カード 1 の対象 | メインスレッドの最新 `quick_action==='detailed_summary'` かつ `status==='complete'` の assistant `ChatMessage` |
| カード 2 の遷移先 | `/papers/{item.id}?mode=article`(記事モード 1h) |

## 5. 状態とインタラクション

### 5.1 開閉

| 操作 | 挙動 |
|---|---|
| ステータスが UI 操作で `done` になり PATCH 成功 | `open(response)`。フォーカスは **ひとことメモ textarea** へ(**決定**: デザインのキャレット表現準拠。Modal の `initialFocusRef`(08 §5.11 に定義済みの prop)で既定の「最初のフォーカサブル」を上書き) |
| 「×」/ Esc / スクリムクリック /「すべてスキップ」 | 未保存入力を破棄して閉じる(4 経路とも同一挙動。確認ダイアログは出さない — 全項目任意のため)。フォーカスはトリガ元要素(open 時点の `document.activeElement`。通常は StatusPill、通知 4a 経由なら「変更する」ボタン)へ戻す。**決定**: トリガ元が DOM から消えていた場合(1e の行再描画等)は `document.body` へフォールバックする(Modal §5.11 の標準戻し処理に含める) |
| 「保存」/ Ctrl+Enter(mac: Cmd+Enter) | §5.2 の保存処理 |
| 再度 `done` に変更(reread→done 等) | 前回値(`comprehension`/`importance`/`one_line_note`)をプリフィルして再度開く。`finished_at` は初回値のまま(サーバー仕様 03 §5.4) |

- 開いている間は背景スクロールロック+フォーカストラップ(Modal §5.11 の規定)。
- ダイアログはアンマウントで状態を完全破棄する(閉じて再度開いたとき、編集途中の値は残らない — サーバー値からのプリフィルのみ)。

### 5.2 保存

1. 「保存」クリックで `PATCH /api/library-items/{id}` に `{ comprehension, importance, one_line_note }` を常に 3 フィールドとも送る(null 可。**決定**: 差分送信にしない — 「未選択に戻した」を表現できるようにするため)。`one_line_note` は §5.5 の正規化(`trim().slice(0, 500)`、空文字→null)を適用して送る。
2. 送信中: 「保存」ボタンは `disabled` + opacity:0.6、ラベルは「保存」のまま(スピナーを足さない — 200ms 未満想定の軽量 PATCH)。
3. 成功: `setQueryData`+invalidate(§2.2)→ 閉じる → `toast({ kind: 'success', message: '読了メモを保存しました' })`(Toast 08 §5.20)。
4. 失敗(ネットワーク/5xx): 閉じずに `toast({ kind: 'error', message: '保存に失敗しました — もう一度お試しください' })`、ボタンを再有効化。入力値は保持(P3: 黙って壊れない)。

### 5.3 理解度ドット

- クリックで 1〜5 を選択。ドット n をクリック → `value = n`(n 以下が塗り、超が空)。
- **決定**: 選択中の値のドットを再クリックすると `null`(未選択)に戻す。理由: 「入力を強制しない」ため、誤タップの取り消し手段を与える。
- `value === null` のとき: ドット 5 個すべて空スタイル、補足テキストは「未選択」(font-size:11px、色 #9A9EA4=`--pr-text-muted`。**決定**: デザイン未描画の未選択状態の補完。レイアウトシフト防止のため空文字にしない)。
- ホバー(**決定**): 空ドットは border-color:var(--pr-acc) に 120ms transition。塗りドットは opacity:0.85。

### 5.4 重要度セグメント

- `SegmentedControl` の選択切替。`importance === null` のときは 3 セグメントとも非選択スタイル。
- **決定**: 選択中セグメントの再クリックで `null` に戻す(理解度と同じ取り消し規則。SegmentedControl 標準にはない挙動のため、1g のラッパで `onChange` 前段に同値判定を挟む)。

### 5.5 ひとことメモ

- 自由テキスト。最大 500 文字(**決定**: `one_line_note` の運用上限。超過分は入力段階で切り捨てず、カウンタも出さず、UI は静かに保つ — 「管理を強要しない」トーン)。
- 送信時の正規化(**決定**、§5.2 と一体): `const v = value.trim().slice(0, 500)`(前後空白除去 → 先頭 500 UTF-16 コード単位に切り詰め)、`v === '' ? null : v` を `one_line_note` として送る。
- IME 変換中の Ctrl+Enter は保存を発火しない(`isComposing` 判定)。

### 5.6 カード 1「✦ 要約をメモに保存」

状態機械(`FollowupCardState`):

| 状態 | 表示 | 遷移 |
|---|---|---|
| `idle` | デザインどおり | クリック → 既存の詳細要約メッセージが**ある**: `POST /api/library-items/{id}/notes` に `{ content_md, source_message_id }` → 成功で `done`、失敗(ネットワーク/4xx/5xx)で `error` / **ない**: `loading` へ |
| `loading` | タイトル「✦ 要約を生成中…」、説明はそのまま、カード全体 opacity:0.7、disabled(**決定**: スピナーは置かずタイトル文言で示す) | `POST /api/chat/threads/{mainThreadId}/messages` `{ content: "", quick_action: "detailed_summary" }`(SSE)。`done` イベント受信 → 上記 POST notes を自動実行 → `done` / `error` イベント・切断 → `error` |
| `done` | タイトル「✓ メモに保存しました」(color:var(--pr-green)=#659471)、disabled | 終端。あわせて `toast({ kind: 'success', message: '要約をメモに保存しました' })` |
| `error` | タイトル「✦ 要約をメモに保存」、説明を「保存できませんでした — もう一度お試しください」(color:var(--pr-warn)=#A05A42)に差し替え | クリックで `idle` からやり直し |

- 「既存の詳細要約メッセージ」の判定(**決定**): §2.1 #3 のプリフェッチ結果(最新 50 件、追加ページングしない)の中で、`role==='assistant'` かつ `quick_action==='detailed_summary'` かつ `status==='complete'` の最新 1 件。50 件より古くに埋もれている場合は「未生成」とみなして再生成する(重複生成は許容 — 回答は履歴に追加されるだけで破壊的でない)。
- `content_md` の構築(**決定**): 対象メッセージの `blocks` のうち `type==='markdown'` の `text` を `\n\n` で連結する。`[[ev:n]]` トークンは変換せずそのまま含める(anchors はサーバー複写で対応が保たれる)。`type==='aside'`(「論文外の知識」「推測」)は含めない。
- 生成中にダイアログを閉じた場合(**決定**): クライアントの SSE fetch を abort し、メモ自動保存は行わない。「保存」成功による close も同じ扱い(保存処理は生成完了を待たない)。サーバー側で完了した回答はチャット履歴に残る(03 §10.3 の回復経路)ため、後からチャットタブの「↑ メモに保存」で昇格できる。
- 429(`quota_exceeded`): `error` 状態 + 説明文を「クォータを超過しています — 設定を確認してください」に差し替え。

### 5.7 カード 2「記事モードで読み返す →」

- クリック → ダイアログを閉じ、`router.push('/papers/{item.id}?mode=article')`。ビューア外(1e 等)から開いていた場合も同 URL へ遷移する。
- 記事が未生成(`GET /api/library-items/{id}/article` が 404)の場合の生成 CTA は**記事モード画面(1h)側の責務**。本カードは無条件に遷移するだけ(loading/done 状態を持たない。常に `idle`)。

### 5.8 ホバー・フォーカス(デザイン未描画分の決定)

| 要素 | 決定 |
|---|---|
| 導線カード | hover: background:var(--pr-bg-card)(#FFFFFF)、transition background-color 120ms |
| 「保存」 | hover: opacity:0.9 / active: opacity:0.8 |
| 「すべてスキップ」 | hover: color:var(--pr-text-sub)(#5B6067)+ text-decoration:underline |
| 「×」 | hover: color:var(--pr-text-sub) |
| キーボードフォーカス | 全インタラクティブ要素に共通 `focus-visible` リング(08 §5 共通事項: outline 1.5px var(--pr-acc)、offset 1px) |

- ローディング/空/スケルトン: 本ダイアログは PATCH レスポンスを持って開くため**初期ローディング状態・スケルトンは存在しない**(§2.1)。空状態も存在しない(全フィールド null 許容の入力 UI)。
- ダークモード: トークン参照により自動追随(1g 固有のダーク描画はデザインに無い。`--pr-src-note-bg`/`--pr-src-note-fg`/`--pr-scrim` はテーマ非分岐値のためそのまま)。

## 6. 受け入れ基準

### 6.1 ピクセル一致(ビジュアルリグレッション対象)

Playwright + `toHaveScreenshot`。ストーリー: シード論文(Rectified Flow, arXiv:2209.03003)のビューア `/papers/{seedItemId}` で status を done に変更し、理解度 4・重要度 high・メモにデザイン逐語文を入力した状態。ビューポート 1440×900。

- [ ] スクリムが `rgba(30,32,36,0.38)` で全面を覆い、ダイアログが width:460px・画面中央・border-radius:14px・shadow `0 32px 80px rgba(20,22,26,0.35)` である
- [ ] ヘッダ: ✓ バッジ 24×24px 正円(bg rgba(101,148,113,0.16) / 文字 #4C7458)、タイトル 15px/700、× が右端 14px #9A9EA4
- [ ] メタ行が 11px #9A9EA4、padding-left:33px で「読了日 YYYY-MM-DD · 累計読書時間 n時間m分(自動記録)」形式
- [ ] 理解度: 塗りドット 4 個(22×22px、var(--pr-acc))+空ドット 1 個(border 1.5px #D5D1C5)、補足「4/5 — だいたい追えた」11px #777B81
- [ ] 重要度: トラック #EFEDE6 radius 7px padding 2px、セグメント h24px padding 0 16px、「高」のみ白背景+shadow-seg+600
- [ ] メモ入力: border 1px #DDD9CF radius 8px padding 10px 12px、文字 12.5px/1.7 #24272B、キャレット色 var(--pr-acc)
- [ ] 導線カード 2 枚: flex:1・gap:8px・border #E2DFD5・radius 8px・bg #FBFAF7、タイトル 11.5px/600 var(--pr-acc)、説明 10px #9A9EA4/1.5
- [ ] フッタ: border-top 1px #F0EDE4、「すべてスキップ」11.5px #9A9EA4 左、「保存」h32px padding 0 18px radius 7px bg var(--pr-acc) 白 12.5px/600 右
- [ ] ラベル「理解度」「重要度」が width:56px で入力群と gap:12px、本体の行間 gap:16px、ヘッダ padding 20px 24px 0・本体 18px 24px・フッタ 14px 24px
- [ ] 全 UI 文言が §4.6 と一字一句一致(「「読んだ」にしました」の鉤括弧入れ子を含む)
- [ ] 未選択バリアント: 理解度 null(全ドット空+「未選択」)・重要度 null(全セグメント非選択)のスクリーンショット

### 6.2 機能検証

- [ ] StatusPill で `done` に変更 → PATCH 成功後にダイアログが開き、初期フォーカスがメモ textarea にある
- [ ] ビューア以外(ライブラリ 1e のステータス変更)でも開く。`POST /api/library-items/bulk` の一括 done では開かない
- [ ] 「×」/ Esc / スクリムクリック /「すべてスキップ」の 4 経路すべてで、PATCH を発行せずに閉じる(ネットワークタブで検証)
- [ ] 「保存」で `PATCH /api/library-items/{id}` に `comprehension` / `importance` / `one_line_note` の 3 フィールドが常に送られ、成功トースト「読了メモを保存しました」が出る
- [ ] 保存失敗(5xx モック)でダイアログが閉じず、入力値が保持され、エラートーストが出る
- [ ] 読了日・累計読書時間は表示のみで編集 UI が存在しない。`reading_seconds_total: 0` のとき時間部分が省かれる
- [ ] 理解度: 各ドットクリックで 1〜5 が選べ、補足が「n/5 — {docs/06 §3 のラベル}」に変わる。選択中ドットの再クリックで未選択に戻る
- [ ] 重要度: 低/中/高の切替、選択中の再クリックで未選択に戻る
- [ ] 再読了(reread→done)時、前回の理解度・重要度・メモがプリフィルされ、`finished_at` は初回値のまま
- [ ] カード 1: 詳細要約が既存 → notes POST(`source_message_id` 付き)→「✓ メモに保存しました」。未生成 → 「✦ 要約を生成中…」→ SSE 完了 → 自動保存。SSE エラーで error 表示、再クリックで復帰
- [ ] カード 1 の生成中に閉じる → SSE abort、メモは作られず、チャット履歴には回答が残る
- [ ] カード 2: `/papers/{id}?mode=article` へ遷移してダイアログが閉じる
- [ ] Ctrl/Cmd+Enter で保存(IME 変換確定の Enter では発火しない)、Tab 巡回がダイアログ内に閉じる(フォーカストラップ)、`role="dialog"` `aria-modal` `aria-labelledby` が正しい
- [ ] 閉じたあとフォーカスがトリガ元 StatusPill に戻る
- [ ] ダークモード(`html[data-theme="dark"]`)でトークン追随し、直値の色がハードコードされていない(ソース grep で `#3E5C76` 等が 1g コンポーネントに現れない)
