# 画面 3a: ブラウザ拡張ポップアップ(4状態)

> **対象読者と前提**: 本書は「訳読 / YAKUDOKU」のブラウザ拡張(`apps/extension` = WXT + TypeScript + React 19、Manifest V3、Chrome / Edge)のポップアップ UI・ツールバーアイコン・arXiv ページ内注入ピルを、確定デザイン(抽出 `extract/3a.md`)と docs/08(機能仕様)に 100% 忠実に実装するための完全仕様である。API 名は plans/03、トークン名・共通スタイル規約は plans/08、アーキテクチャ(認証・ポーリング決定)は plans/01 に従う。本書に無い拡張の挙動は v1 に存在しない。

## 1. 概要とルート

- **Next.js ルート: なし**。本画面は `apps/web` の画面ではなく、`apps/extension` のポップアップである。デザインフレーム 3a のブラウザウィンドウモック(タブバー・アドレスバー・arXiv ページ)は文脈提示であり、**実装対象はポップアップ本体(幅 372px)・ツールバーアイコン・arXiv ページ内注入ピルの 3 つ**。
- エントリポイント構成(WXT 規約。決定):

```
apps/extension/
├── wxt.config.ts                     // manifest 定義(§5.9)
├── entrypoints/
│   ├── popup/
│   │   ├── index.html                // <html><body> に width:372px
│   │   ├── main.tsx                  // QueryClientProvider + <PopupApp/>
│   │   └── App.tsx                   // PopupApp(状態ルータ)
│   ├── background.ts                 // バッジ更新ポーリング・再試行キュー
│   ├── arxiv-pill.content.ts         // ページ内「訳 保存」ピル(動的登録)
│   └── options/
│       ├── index.html
│       └── main.tsx                  // オプションページ(§5.8)
├── components/                       // ポップアップ固有コンポーネント(§3)
├── lib/
│   ├── api.ts                        // packages/api-client の createClient ラッパ
│   ├── format.ts                     // 相対時刻フォーマッタ(§5.6)
│   ├── queue.ts                      // 再試行キュー(§5.7)
│   └── badge.ts                      // ツールバーアイコン状態(§5.5)
└── assets/icons/                     // icon-{16,32,48,128}.png / icon-dot-*.png / icon-check-*.png / icon-spin-{0..7}-*.png
```

- **役割**: 論文取り込みの唯一の経路(docs/08 §1)。ツールバーの「訳」アイコンクリックでポップアップを開き、現在タブの URL を判定して 4 状態のいずれかを表示する。「ツールバー → 保存」の 2 クリックでライブラリへ。
- **認証**: 必要。サイトと同一のセッションクッキー(`yk_session`)を `host_permissions: ["https://yakudoku.app/*"]` + `fetch(…, { credentials: "include" })` で共用する(plans/01 §6.4)。
  - 決定: **v1 の拡張は拡張トークン(`yk_ext_…`)を実装しない**。plans/03 §1.2.1 の拡張トークンスコープは API 側に確保済みの将来フォールバック(Safari 等)であり、Chrome / Edge ではクッキー共有で完結する。理由: plans/01 §6.4 の決定(専用トークン不要)に従い、トークン保管・失効 UI を持たないことで拡張を薄く保つ。
  - 決定: CSRF は plans/03 §1.3 の Origin 検証方式に従い、apps/api の Origin 許可リストに拡張オリジンを追加する。環境変数 `YK_ALLOWED_EXTENSION_ORIGINS`(カンマ区切り。例 `chrome-extension://<ChromeのID>,chrome-extension://<EdgeのID>`)。plans/01 §6.4 の `X-CSRF-Token` ヘッダ方式は採用しない(plans/03 が API 仕様の正)。
  - 未ログイン(401)時は状態 0(ログイン導線。§5.1)を表示する。
- **API オリジン**: 環境変数 `WXT_APP_ORIGIN`(開発 `http://localhost:3000` / 本番 `https://yakudoku.app`)。全 API 呼び出しは `${WXT_APP_ORIGIN}/api/*`、「サイトで開く ↗」等の新規タブも `${WXT_APP_ORIGIN}` 起点。
- **テーマ**(決定): ポップアップ・注入ピル・オプションページは **ライトテーマ固定・アクセント slate(#3E5C76)固定**。理由: 確定デザイン 3a はライト+スレートブルーのみ描画。ユーザーのアクセント設定(4f)は `GET /api/auth/me` に含まれず、拡張への同期機構を作らない(拡張を薄く保つ)。`packages/tokens` の `css/tokens.css` + `css/fonts.css` を取り込み、`data-accent` は付与しない(`--pr-a` 既定値 #3E5C76 のまま)。

## 2. データ要件

### 2.1 使用 API エンドポイント(名称は plans/03)

| # | エンドポイント | 認証 | 用途 | 呼び出しタイミング |
|---|---|---|---|---|
| 1 | `GET /api/auth/me` | session | ログイン判定・`unread_notifications`(琥珀ドット) | ポップアップ開時+バックグラウンドポーリング |
| 2 | `GET /api/ingest/check?url=` | session | 状態判定(arxiv/pdf/unsupported)・書誌プレビュー・`latex_available`・`suggested_tags`・`saved`(状態3判定) | ポップアップ開時(タブ URL 確定後) |
| 3 | `POST /api/ingest/arxiv` | session | 状態1「保存」/ページ内ピル保存。`Idempotency-Key` 付与 | 保存実行時 |
| 4 | `POST /api/ingest/pdf` | session | 状態4「このタブの PDF を送信」(multipart) | 明示クリック時のみ |
| 5 | `GET /api/ingest/recent?limit=3` | session | フッタ「直近の取り込み」 | ポップアップ開時+表示中 2,000ms ポーリング |
| 6 | `GET /api/jobs/{job_id}` | session | 状態2のパイプライン進捗 | 保存直後から表示中 2,000ms ポーリング |
| 7 | `PATCH /api/library-items/{id}` | session | 状態3「ステータス変更 ▾」 | ドロップダウン選択時 |
| 8 | `GET /api/collections` | session | 状態1「追加先」ドロップダウンの選択肢 | ポップアップ開時 |

### 2.2 TanStack Query キー設計(決定)

QueryClient は popup 内に 1 個(`gcTime: 0`、ポップアップは短命なので永続キャッシュしない)。

| キー | fetch | staleTime | refetchInterval |
|---|---|---|---|
| `['ext', 'me']` | GET /api/auth/me | 30_000ms | なし |
| `['ext', 'check', tabUrl]` | GET /api/ingest/check | 0(開くたび取得) | なし |
| `['ext', 'collections']` | GET /api/collections | 60_000ms | なし |
| `['ext', 'recent']` | GET /api/ingest/recent?limit=3 | 0 | **2,000ms**(決定: 「処理中行」= `pipeline.stage` が `complete` でも `failed` でもない行。処理中行が 1 件以上ある間のみポーリングし、全行が complete/failed なら停止) |
| `['ext', 'job', jobId]` | GET /api/jobs/{jobId} | 0 | **2,000ms**(status が succeeded/failed で停止) |

- mutation: `['ext','save-arxiv']` → POST /api/ingest/arxiv、`['ext','send-pdf']` → POST /api/ingest/pdf、`['ext','patch-item']` → PATCH /api/library-items/{id}。

### 2.3 リアルタイム更新(決定: ポーリング。SSE 不使用)

plans/01 §3.1 の決定に従う。MV3 service worker は SSE 常時接続を維持できないため、拡張は SSE(`GET /api/jobs/{id}/events`)を使わない。

- **ポップアップ表示中**: 2,000ms 間隔(上表)。
- **バックグラウンド(background.ts)**: アクティブジョブ(`chrome.storage.local` の `yk_active_jobs: string[]`)がある間、`setTimeout` チェーンで **15,000ms** 間隔に各アクティブジョブの `GET /api/jobs/{job_id}` と `GET /api/auth/me`(琥珀ドット用 `unread_notifications`)を呼びツールバーアイコンを更新する(決定: `/api/ingest/recent` のレスポンスは `job_id` を含まず完了判定に使えないためバックグラウンドでは呼ばない。`Job.status` が succeeded/failed になった ID は `yk_active_jobs` から除去する)(fetch 自体が service worker のアイドルタイマーをリセットするため常駐可)。加えて安全網として `chrome.alarms.create("yk-poll", { periodInMinutes: 1 })` を張り、service worker 再起動後もポーリングを再開する(決定)。アクティブジョブが 0 件になったらタイマーと alarm を解除する。決定: アクティブジョブが無い間はバックグラウンドで `me` をポーリングしない — 琥珀ドットはポップアップ開時(`['ext','me']` 取得時)とジョブ完了時の 2 契機でのみ更新される(遅延許容。拡張を薄く保つ)。

### 2.4 状態判定ロジック(ポップアップ開時)

1. `chrome.tabs.query({ active: true, currentWindow: true })` で現在タブの `url` / `title` を取得。
2. `['ext','me']` → 401 なら**状態 0(未ログイン)**で終了。
3. `['ext','check', url]` のレスポンスで分岐:
   - `saved !== null` → **状態 3(既にライブラリ)**
   - `kind === "arxiv"` → **状態 1(保存前)**
   - `kind === "pdf"` → **状態 4(一般ページ PDF)**
   - `kind === "unsupported"` → **状態 5(非対応ページ。§5.1)**
4. 状態 1 で保存成功(202)→ **状態 2(保存直後)** へ同一ポップアップ内遷移。保存が 409 `duplicate` → レスポンス本文の `existing` で**状態 3** を描画。

## 3. コンポーネント分解

決定: **拡張は `apps/web/src/components/ui/*` を import しない**(アプリ間依存を作らない。plans/08 §1.3 の責務分担)。`packages/tokens` の CSS のみ共有し、ポップアップ固有コンポーネントを `apps/extension/components/` に実装する。見た目の規約(focus-visible リング `outline: 1.5px solid var(--pr-acc); outline-offset: 1px`、テキストグリフ方針 plans/08 §6.2)は plans/08 に一致させる。

```
PopupApp                                  … 状態ルータ(state 0..5)
├── PopupHeader                           … 全状態共通ヘッダ
│   └── PopupBadge                        … 検出/成功/PDFバッジ(3変種)
├── (state 1) SavePanel
│   ├── BibPreview                        … 書誌+品質見込み
│   ├── StatusPillRow                     … ステータス3択ピル
│   ├── TagField                          … タグチップ+入力+提案
│   ├── DestinationRow                    … コレクションドロップダウン+メモ入力
│   ├── SaveButton                        … 保存 ⏎(プライマリ)
│   └── PrivacyNote                       … URL のみ送信 注記
├── (state 2) SavedPanel
│   ├── PipelineCard                      … サムネ+タイトル+進捗行+ProgressBarMini
│   └── PopupButtonRow                    … サイトで開く ↗ / 閉じる
├── (state 3) ExistingPanel
│   ├── ExistingStatusRow                 … ドット付きピル+追加日・進捗
│   ├── StatusDropdown                    … 6値ステータス変更(Popover 相当)
│   └── PopupButtonRow                    … 続きから開く ↗ / ステータス変更 ▾
├── (state 4) PdfPanel
│   ├── PdfBibPreview                     … タイトル+「書誌は推定」+URL
│   ├── WarnBox                           … 琥珀警告ボックス
│   └── SaveButton(variant="pdf")        … このタブの PDF を送信
├── (state 0) LoginPanel / (state 5) UnsupportedPanel
└── RecentIngestsFooter                   … 直近の取り込み(全状態共通)
    └── SpinnerDot                        … 11×11 スピナー
```

固有コンポーネントの props 型(`apps/extension/components/` 配下、named export):

```ts
// PopupHeader.tsx
interface PopupHeaderProps {
  title: string;                            // '訳読に保存' | '保存しました' | '既にライブラリにあります'
  badge?: { kind: 'detect' | 'success' | 'pdf'; label?: string };
  // detect: 緑ピル「arXiv 論文を検出」/ success: 16×16円✓ / pdf: グレーピル「PDF を表示中」
  onOpenSettings: () => void;               // ⚙ → chrome.runtime.openOptionsPage()
}

// BibPreview.tsx
interface BibPreviewProps {
  title: string;
  metaLine: string;                          // 'Liu, Gong, Liu · ICLR 2023 · arXiv:2209.03003 v3'
  latexAvailable: boolean | null;            // true → '✓ LaTeX ソースあり — 品質レベル A 見込み'
}

// StatusPillRow.tsx
import type { Status } from '@yakudoku/api-client';   // 'planned'|'up_next'|'reading'|…
interface StatusPillRowProps {
  value: Extract<Status, 'planned' | 'up_next' | 'reading'>; // 既定 'planned'
  onChange: (v: 'planned' | 'up_next' | 'reading') => void;
}

// TagField.tsx
interface TagFieldProps {
  tags: string[];
  suggested: string[];                       // check.suggested_tags(未追加の先頭1件のみ表示)
  onAdd: (tag: string) => void;
  onRemove: (tag: string) => void;
}

// DestinationRow.tsx
interface DestinationRowProps {
  collections: { id: string; name: string }[];
  collectionId: string | null;               // null = 「コレクションなし」
  onCollectionChange: (id: string | null) => void;
  note: string;
  onNoteChange: (v: string) => void;
}

// SaveButton.tsx
interface SaveButtonProps {
  variant: 'arxiv' | 'pdf';                  // arxiv: h34「保存 ⏎」/ pdf: h32「このタブの PDF を送信」
  loading: boolean;
  onClick: () => void;
}

// PipelineCard.tsx
import type { PipelineState } from '@yakudoku/api-client';
interface PipelineCardProps { title: string; pipeline: PipelineState }

// PopupButtonRow.tsx
interface PopupButtonRowProps {
  primary: { label: string; onClick: () => void };    // 'サイトで開く ↗' 等
  secondary: { label: string; onClick: () => void };  // '閉じる' / 'ステータス変更 ▾'
}

// ExistingStatusRow.tsx
import type { Status, LastPosition } from '@yakudoku/api-client';
interface ExistingStatusRowProps {
  status: Status;
  addedAt: string;                           // ISO → '2026/07/02 追加'
  progressPct: number;                       // 42 → '進捗 42%'
  lastPosition: LastPosition | null;         // '前回: §2.1 整流フロー · 昨日 21:52'
}

// StatusDropdown.tsx(状態3のステータス変更。plans/08 §5.2 の 6値ドロップダウン仕様を移植)
interface StatusDropdownProps {
  open: boolean;
  current: Status;
  onSelect: (s: Status) => void;             // PATCH /api/library-items/{id}
  onClose: () => void;
}

// PdfBibPreview.tsx(状態4 の書誌ブロック。決定)
interface PdfBibPreviewProps {
  titleGuess: string;                        // §4.7 のタイトル推定規則で導出
  displayUrl: string;                        // §4.7 の短縮 URL(hostname + "/…/" + ファイル名)
}

// WarnBox.tsx
interface WarnBoxProps { children: React.ReactNode }

// RecentIngestsFooter.tsx
interface RecentIngestRow {
  libraryItemId: string;
  title: string;
  pipeline: PipelineState;
  completedAt: string | null;
  viewerUrl: string;                         // '/papers/li_…'
}
interface RecentIngestsFooterProps {
  items: RecentIngestRow[];                  // 最大3件
  onOpen: (viewerUrl: string) => void;       // chrome.tabs.create({ url: WXT_APP_ORIGIN + viewerUrl })
}
```

ページ内注入ピル(コンテンツスクリプト。React 不使用・素の DOM。決定: ホストページを汚さないため Shadow DOM `mode:"closed"` 内に描画):

```ts
// entrypoints/arxiv-pill.content.ts 内
type PillState = 'idle' | 'saving' | 'saved' | 'error';
```

## 4. レイアウト・スタイル完全仕様

以下は `extract/3a.md` の全内容(逐語)。**実装対象**はポップアップ(§4.4〜4.7)・ツールバーアイコン(§4.2 の「訳」アイコン)・ページ内注入ピル(§4.3)であり、ブラウザクローム・arXiv ページ本体はデザイン上のモック(VRT ではフィクスチャとして再現。§6)。トークン名は plans/08 の `tokens.css` 名で注記する(`--pr-a`=`--pr-acc`、`--pr-as`=`--pr-acc-s`、`--pr-am`=`--pr-acc-m` はエイリアス関係。以下デザイン原文の `var(--pr-a)` 系表記を維持)。

### 4.0 デザイナー注記(逐語。UI の一部ではない)

フレーム上部:
- バッジ: 「3a」(min-width:32px, height:22px, 背景 #2B2E33, 白文字, border-radius:6px, font-size:12px, font-weight:700)
- タイトル(15px/700/#1E2227): 「ブラウザ拡張(Chrome / Edge) — 取り込みの主経路」
- 説明(12px/#777B81): 「ツールバー → 保存の2クリックでライブラリへ / URLのみ送信・解析はサーバー / 右列はポップアップの他状態」

右列の各バリエーション見出し(11px/700/#5B6067):
- 「保存直後 — 同じポップアップ内で結果+進捗」
- 「既にライブラリにある論文 — 重複を作らない」
- 「一般ページで PDF 表示中 — 明示操作でのみタブ内容を送信」

ブラウザページ内左下の注記(font-size:10px, color:#9A9EA4, IBM Plex Sans JP, 左に幅22px×1pxの水平線 #D5D1C5, gap:7px, position:absolute left:26px bottom:14px):
- 「ページ内ボタン(§4 オプトイン・arXiv 限定)は保存後「✓ 保存済み」表示に変わります」

このフレームは 1440×900 単一フレームではなく、幅 1440px コンテナに「ブラウザウィンドウモック(900×850)」+「右列(幅 400px、他状態 3 種)」を gap:36px で横並び配置した構成。

### 4.1 フレームのレイアウト構造(デザイン原文)

```
┌ コンテナ width:1440 ──────────────────────────────────────────────┐
│ [3a] ブラウザ拡張(Chrome / Edge)… (バッジ+タイトル+説明, margin-bottom:12px) │
│ ┌ ブラウザウィンドウ 900×850 ─────────────┐  gap:36px ┌ 右列 400 ─────────┐ │
│ │ タブバー h38 (#DEE1E6)                    │           │ 見出し「保存直後…」  │ │
│ │ アドレスバー h42 (#F6F7F8)                │           │ [ポップアップ 372w]  │ │
│ │ ┌ ページ本体 flex:1 (#FFFFFF) ─────────┐ │           │ gap:20px            │ │
│ │ │ arXivヘッダ h34 (#B31B1B)             │ │           │ 見出し「既に…」      │ │
│ │ │ 論文abs本文 (2カラム: flex:1 + 150px) │ │           │ [ポップアップ 372w]  │ │
│ │ │      ┌拡張ポップアップ 372w──┐        │ │           │ 見出し「一般ページ…」│ │
│ │ │      │(絶対配置 top:6 right:64)│      │ │           │ [ポップアップ 372w]  │ │
│ │ │      └────────────────────┘        │ │           └───────────────────┘ │
│ │ └─────────────────────────────────┘ │                                   │
│ └─────────────────────────────────────┘                                   │
└──────────────────────────────────────────────────────────────────┘
```

- 全体コンテナ: `width:1440px`。ヘッダ行 `display:flex; align-items:baseline; gap:10px; margin-bottom:12px`。
- 本体: `display:flex; gap:36px; align-items:flex-start`。
- ブラウザウィンドウ: `width:900px; height:850px; background:#DEE1E6; border:1px solid #C8CBD0; border-radius:10px; box-shadow:0 20px 44px rgba(28,30,34,0.14); overflow:hidden; display:flex; flex-direction:column; position:relative`。
- 右列: `width:400px; display:flex; flex-direction:column; gap:20px`。各バリエーションブロックは `display:flex; flex-direction:column; gap:8px`(見出し+ポップアップ)。

### 4.2 ブラウザクローム(モック。ツールバーアイコンのみ実装対象)

#### タブバー(h:38px, flex:none)
- `display:flex; align-items:flex-end; padding:6px 10px 0; gap:6px`(背景 #DEE1E6)。
- アクティブタブ: `display:flex; align-items:center; gap:7px; height:32px; background:#F6F7F8; border-radius:8px 8px 0 0; padding:0 12px; width:280px`
  - ファビコン: 12×12 円形(border-radius:50%)、背景 #B31B1B、opacity:0.85、flex:none。
  - タブタイトル: 11px, #3C4046, white-space:nowrap + ellipsis: 「[2209.03003] Flow Straight and Fast: Learn…」
  - 閉じるボタン: `margin-left:auto`, 11px, #9AA0A6, テキスト「×」。
- 新規タブボタン: 「+」13px, #5F6368, `padding:0 4px 6px`。

#### アドレスバー(h:42px, flex:none, 背景 #F6F7F8)
- `display:flex; align-items:center; gap:10px; padding:0 12px`。
- ナビ矢印: 「←」13px #5F6368 / 「→」13px #B8BCC1(戻れない=淡色)/ 「⟳」12px #5F6368。
- URL欄: `flex:1; height:28px; background:#FFFFFF; border:1px solid #DFE1E5; border-radius:14px; display:flex; align-items:center; gap:7px; padding:0 12px; font-size:11.5px; color:#3C4046`
  - 鍵アイコン SVG 10×11(viewBox 0 0 10 11): 南京錠(角丸長方形の本体 x1 y4.5 w8 h5.5 rx1 + 上部アーチ)、stroke #5F6368, stroke-width 1.2。
  - URLテキスト: 「arxiv.org/abs/2209.03003」
- パズルピースアイコン SVG 14×14: 拡張機能メニューの定番形状(4辺に凸凹のあるジグソーピース)、stroke #5F6368, stroke-width 1.1。
- **訳読拡張のツールバーアイコン(実装対象)**: 外枠 `position:relative; 24×24; border-radius:6px; background:#E8EAED`(=ピン留め状態の枠)。内部に 17×17, border-radius:4px, 背景 var(--pr-a), 白文字「訳」9.5px/700。右上に通知バッジドット: `position:absolute; top:-1px; right:-1px; 7×7; border-radius:50%; background:#C49432; border:1.5px solid #F6F7F8`(琥珀=処理中/未読あり)。→ 実装は PNG アイコンセットで再現(§5.5)。
- プロフィールアバター: 24×24 円形, 背景 var(--pr-as), 文字色 var(--pr-a), 「YK」9.5px/700。
- ブラウザメニュー: 「⋮」13px #5F6368, letter-spacing:1px。

### 4.3 arXiv abs ページ(モック)+ページ内注入ピル(実装対象)

ページ本体: flex:1, 背景 #FFFFFF, overflow:hidden, position:relative。

#### arXiv ヘッダバー(h:34px, 背景 #B31B1B)
- `display:flex; align-items:center; padding:0 18px; gap:14px`。
- ロゴ: 「arXiv」 Source Serif 4, 15px/600, #FFFFFF, italic。
- パンくず: 「arxiv.org > cs > arXiv:2209.03003」 10.5px, rgba(255,255,255,0.85)。
- 右端(margin-left:auto): 「Help | Advanced Search」 10.5px, rgba(255,255,255,0.85)。

#### 本文エリア(padding:22px 26px; display:flex; gap:22px)
左カラム(flex:1; min-width:0):
- 分野: 「Computer Science > Machine Learning」 10.5px, #5F6368, margin-bottom:8px。
- 投稿情報: 「[Submitted on 7 Sep 2022 (v1), last revised 25 Feb 2023 (this version, v3)]」 10px, #8A8E94, margin-bottom:4px。
- タイトル行(display:flex; align-items:flex-start; gap:10px; margin-bottom:8px):
  - 論文タイトル: Source Serif 4, 19px/600, line-height:1.4, #1E2227: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」
  - **ページ内注入「保存」ピルボタン(実装対象)**(flex:none): `display:inline-flex; align-items:center; gap:5px; height:24px; padding:0 10px; border:1px solid var(--pr-am); border-radius:999px; background:var(--pr-as); color:var(--pr-a); font-size:10.5px; font-weight:700; margin-top:3px`。内部に 13×13, border-radius:3px, 背景 var(--pr-a), 白文字「訳」8px のミニロゴ+テキスト「保存」。`z-index: 2147483000`(plans/08 §7.3。コンテンツスクリプト内のみ)。Shadow DOM 内に描画し、フォントは `font-family:'IBM Plex Sans JP', sans-serif`(ホストページに Google Fonts を注入しない。システムフォールバック許容。決定)。
- 著者: 「Xingchao Liu, Chengyue Gong, Qiang Liu」 12px, #1A5276(arXivリンク青), margin-bottom:14px。
- Abstract ブロック: `background:#FAFAFA; border-left:2px solid #DADCE0; padding:12px 16px`, 本文 Source Serif 4, 12.5px, line-height:1.7, #33373C。先頭に太字ラベル「Abstract:」(IBM Plex Sans JP, 11.5px, bold)。本文「We present rectified flow, a surprisingly simple approach to learning (neural) ordinary differential equation (ODE) models to transport between two empirically observed distributions π₀ and π₁, hence providing a unified solution to generative modeling and domain transfer…」
- メタタグ行(display:flex; gap:8px; margin-top:14px; font-size:10.5px; color:#5F6368): 「Subjects: **Machine Learning (cs.LG)**」(太字部あり)と「Cite as: arXiv:2209.03003」。各 `border:1px solid #DADCE0; border-radius:3px; padding:2px 7px`。

右カラム(width:150px; flex:none; font-size:11px; color:#1A5276; flex-direction:column; gap:6px; padding-top:26px):
- 見出し「Access Paper:」(font-weight:700, #3C4046, 10px)
- リンク: 「· View PDF」「· TeX Source」「· Other Formats」

ページ左下注記: §4.0 記載のとおり(absolute left:26px bottom:14px)。

### 4.4 ポップアップ 状態1: 保存前(メイン)

- デザイン上の配置: ページ本体内 `position:absolute; top:6px; right:64px`(ツールバー「訳」アイコン直下を模す)。
- 本体: `width:372px; background:#FFFFFF; border:1px solid #D6D3C9; border-radius:10px; box-shadow:0 24px 56px rgba(20,22,26,0.30); font-family:'IBM Plex Sans JP'; overflow:hidden`。
- 吹き出し矢印: `position:absolute; top:-5px; right:24px; 9×9; background:#FFFFFF; border-left:1px solid #D6D3C9; border-top:1px solid #D6D3C9; transform:rotate(45deg)`。
- 決定(実ポップアップへの適用): 実際の `chrome.action` ポップアップウィンドウでは **border・box-shadow・吹き出し矢印・border-radius を描画しない**(ブラウザのポップアップクロームが枠と影を担い、デザインの矢印はアンカー表現のため)。`html, body { width:372px; margin:0; background:#FFFFFF; font-family:'IBM Plex Sans JP', sans-serif; }`。それ以外の内部寸法・色は以下を 1px 単位で一致させる。VRT では border 付きコンテナで撮影する(§6)。

#### ヘッダ(padding:11px 14px; border-bottom:1px solid #F0EDE4; display:flex; align-items:center; gap:8px)
- ロゴ: 20×20, border-radius:5px, 背景 var(--pr-a), 白文字「訳」10.5px/700。
- タイトル: 「訳読に保存」 12.5px/700。
- 検出バッジ: h:17px, padding:0 7px, border-radius:999px, 背景 rgba(101,148,113,0.16), 文字 #4C7458, 9.5px/700: 「arXiv 論文を検出」
  - 決定: この色対は拡張ローカル CSS 変数 `--ext-ok-bg: rgba(101,148,113,0.16); --ext-ok-fg: #4C7458;` として `popup.css` に定義する(tokens.css の `--pr-src-note-*` と同値だが意味が異なるため流用しない)。
- 設定ギア: margin-left:auto, 「⚙」12px, #9A9EA4(テキストグリフ。plans/08 §6.2 方針)。

#### 本文(padding:12px 14px; display:flex; flex-direction:column; gap:11px)
1. 書誌ブロック(column, gap:4px):
   - タイトル: 12.5px/600, line-height:1.5: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」
   - メタ: 10.5px, #9A9EA4: 「Liu, Gong, Liu · ICLR 2023 · arXiv:2209.03003 v3」(`check.bib.authors_short · venue year · arXiv:{arxiv_id} {arxiv_version}` の連結。venue/year が null の要素は「 · 」ごと省略。決定: venue と year の片方のみ null の場合は非 null 側だけを表示、`arxiv_version` が null なら「 {arxiv_version}」部分を省略して「arXiv:2209.03003」とする)
   - 品質見込み: 10px, #4C7458(緑): 「✓ LaTeX ソースあり — 品質レベル A 見込み」(`latex_available === true` のとき。§5.2 に false/null 時の決定)
2. ステータス行(display:flex; align-items:center; gap:8px):
   - ラベル「ステータス」 10.5px/600, #5B6067, width:58px, flex:none。
   - ピル群(gap:4px):
     - 選択中「✓ 読む予定」: h:22px, padding:0 9px, border:1px solid var(--pr-a), border-radius:999px, 10.5px, color var(--pr-a), 背景 var(--pr-as), font-weight:600, 内部 gap:4px(✓ とラベル)。
     - 非選択「すぐ読む」「読んでいる」: h:22px, padding:0 9px, border:1px solid #DDD9CF, border-radius:999px, 10.5px, #5B6067。
   - 対応値: 読む予定=`planned` / すぐ読む=`up_next` / 読んでいる=`reading`(plans/03 §1.6)。
3. タグ行(display:flex; align-items:center; gap:8px):
   - ラベル「タグ」(同上 width:58px)。
   - タグ入力枠: flex:1; border:1px solid #DDD9CF; border-radius:6px; padding:4px 7px; display:flex; flex-wrap:wrap; gap:4px; align-items:center。
     - タグチップ「flow ×」: h:18px, padding:0 6px, border-radius:3px, 背景 #F1EFE9, 文字 #3C4046 10px(× は #9A9EA4, gap:3px)。
     - プレースホルダ「追加…」 10px, #B0B4BA(実装は透明 input、幅 min 48px)。
     - サジェスト(margin-left:auto): 「提案: distillation +」 9.5px, var(--pr-a)。`check.suggested_tags` のうち未追加の先頭 1 件。
4. 追加先行(display:flex; align-items:center; gap:8px):
   - ラベル「追加先」(同上 width:58px)。
   - コレクション選択ドロップダウン: inline-flex; align-items:center; gap:5px; h:24px; padding:0 9px; border:1px solid #DDD9CF; border-radius:6px; 10.5px; #3C4046: 「輪読会 2026-07 ▾」(▾ は 8px, #9A9EA4)。
   - メモ入力(flex:1): h:24px; padding:0 9px; border:1px solid #EAE7DE; border-radius:6px; 10.5px; プレースホルダ色 #B0B4BA: 「ひとことメモ…」(入力値の文字色は #3C4046。決定)
5. 保存ボタン(プライマリ): `display:flex; align-items:center; justify-content:center; gap:7px; height:34px; border-radius:7px; background:var(--pr-a); color:#FFFFFF; font-size:12.5px; font-weight:700; width:100%`: 「保存」+キーキャップ「⏎」(9.5px/500, opacity:0.75, border:1px solid rgba(255,255,255,0.4), border-radius:3px, padding:0 5px)。
6. プライバシー注記: 9.5px, #9A9EA4, text-align:center, margin-top:-3px: 「URL のみを送信します — 取得・解析はサーバーで実行」

#### フッタ「直近の取り込み」(border-top:1px solid #F0EDE4; padding:9px 14px 11px; column; gap:6px; 背景 #FCFBF8)
- セクション見出し: 9.5px/700, #9A9EA4, letter-spacing:0.4px: 「直近の取り込み」
- 行1(処理中): display:flex; align-items:center; gap:7px; 10.5px #3C4046。左に 11×11 スピナー(円形, border:2px solid var(--pr-am), border-top-color:var(--pr-a)。決定: `animation: spin 800ms linear infinite`)。タイトル(flex:1, ellipsis)「Improved Techniques for Training Consistency Models」、右に「翻訳中 68%」(9.5px, #9A9EA4)。
- 行2(完了): 左「✓」(#659471, 10px, width:11px 中央揃え)、「Scaling Rectified Flow Transformers for High-Res…」、右「今日 8:02」。
- 行3(完了): 「✓」+「Adversarial Diffusion Distillation」、右「7/01」。
- データ: `GET /api/ingest/recent?limit=3`。右端表示は処理中=`stage` の日本語+`progress_pct`(§5.4 のマッピング)、完了=`completed_at` の相対表記(§5.6)。
- 決定: `items` が 0 件のとき(取り込み履歴なし、または `recent` の取得失敗)はフッタ全体(border-top 含む)を非表示にする。空見出しだけの表示はしない。

### 4.5 ポップアップ 状態2: 保存直後

- デザイン右列枠: width:372px; 背景 #FFFFFF; border:1px solid #D6D3C9; border-radius:10px; box-shadow:0 12px 32px rgba(20,22,26,0.14); overflow:hidden(右列 3 つ共通。矢印なし)。実ポップアップでは §4.4 の決定どおり枠なし。
- ヘッダ(padding:11px 14px; border-bottom:1px solid #F0EDE4): ロゴ「訳」20×20(同前)/ タイトル「保存しました」12.5px/700 / 成功バッジ: 16×16 円形, 背景 rgba(101,148,113,0.16), 文字 #4C7458 9px/700「✓」/ 右端「⚙」。
- 本文(padding:12px 14px; column; gap:10px):
  - 論文カード: border:1px solid #E2DFD5; border-radius:8px; padding:10px 12px; display:flex; gap:10px。
    - サムネイル: 40×52, border-radius:4px, 背景 #EFEDE6, border:1px solid #E0DDD3(プレースホルダ。決定: v1 の状態 2 は常にプレースホルダ。サムネイル生成はパイプライン後段で完了前のため)。
    - 右(column; gap:4px; min-width:0; flex:1):
      - タイトル 11.5px/600, line-height:1.45, 2行クランプ(`-webkit-line-clamp:2`): 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」
      - パイプライン進捗行(display:flex; gap:6px; 9.5px; #5B6067): 「✓ 書誌」(#659471)「✓ 構造化」(#659471)「翻訳中 12%」(var(--pr-a), font-weight:600)。ステージマッピングは §5.4。
      - プログレスバー: h:3px, border-radius:2px, トラック #ECE9DF, 塗り `width:{progress_pct}%` 背景 var(--pr-a)。
  - ボタン行(display:flex; gap:6px):
    - プライマリ(flex:1): h:30px, border-radius:6px, 背景 var(--pr-a), 白 11.5px/600: 「サイトで開く ↗」
    - セカンダリ: h:30px, padding:0 12px, border:1px solid #DDD9CF, border-radius:6px, 11.5px, #5B6067: 「閉じる」
  - 説明: 9.5px, #9A9EA4, line-height:1.6: 「進捗はツールバーのバッジにも表示されます(処理中スピナー → 完了チェック)。読める部分から開けます。」
- 決定: 状態 2 でもフッタ「直近の取り込み」を表示する(docs/08 §3.1 の決定。今保存した論文が先頭行に入る)。状態 3・4 も同様。

### 4.6 ポップアップ 状態3: 既にライブラリにある

- ヘッダ: ロゴ「訳」/ タイトル「既にライブラリにあります」12.5px/700 / 右端「⚙」(バッジなし)。
- 本文(padding:12px 14px; column; gap:10px):
  - 情報行(display:flex; align-items:center; gap:8px; 11px; #5B6067):
    - ステータスピル: inline-flex; align-items:center; gap:5px; h:22px; padding:0 9px; border:1px solid #DDD9CF; border-radius:999px; 10.5px; 背景 #FFFFFF; 文字 #1E2227。左に 6×6 円ドット(背景 var(--pr-a)=「読んでいる」の色。他ステータスのドット色は plans/08 §2 の STATUS_COLORS: planned=#9AA0A6 / up_next=#C49432 / reading=var(--pr-acc) / done=#659471 / reread=#8E7AA6 / on_hold=#B0ACA2。注: `packages/tokens` の `STATUS_COLORS` 定数のキー名は `to_read`/`read_next`(plans/08 §2.4 のまま)であり、API の `Status` 値 `planned`→`to_read`、`up_next`→`read_next` に読み替えるマッピングを `lib/` 側で持つ。決定)+ラベル「読んでいる」。
    - テキスト「2026/07/02 追加 · 進捗 42%」(`saved.added_at` を `YYYY/MM/DD` 整形+`saved.progress_pct`)。
  - 前回位置: 10.5px, #9A9EA4: 「前回: §2.1 整流フロー · 昨日 21:52」(`saved.last_position.section_display` + `saved_at` の相対表記 §5.6。`last_position === null` なら行ごと非表示。決定)。
  - ボタン行(display:flex; gap:6px):
    - プライマリ(flex:1): h:30px, border-radius:6px, 背景 var(--pr-a), 白 11.5px/600: 「続きから開く ↗」
    - セカンダリ: h:30px, padding:0 12px, border:1px solid #DDD9CF, border-radius:6px, 11.5px, #5B6067: 「ステータス変更 ▾」

### 4.7 ポップアップ 状態4: 一般ページで PDF 表示中

- ヘッダ: ロゴ「訳」/ タイトル「訳読に保存」12.5px/700 / グレーバッジ: h:17px, padding:0 7px, border-radius:999px, 背景 #F1EFE9, 文字 #777B81, 9.5px/700: 「PDF を表示中」/ 右端「⚙」。
- 本文(padding:12px 14px; column; gap:10px):
  - 書誌(column; gap:3px):
    - タイトル 11.5px/600, line-height:1.5: 「BOOT: Data-free Distillation of Denoising Diffusion Models」+インラインバッジ「書誌は推定」(h:14px, padding:0 5px, border-radius:3px, 背景 #F1EFE9, 文字 #8A8E94, 9px/600, vertical-align:1px)。
    - タイトル推定(決定): `tab.title` から拡張子 `.pdf`(大文字小文字不問)と末尾の「 - 」以降のビューア名サフィックスを除去した文字列。空になった場合は URL のファイル名(`paper_412.pdf`)を表示。`POST /api/ingest/pdf` の `meta.title_guess` に同値を送る。
    - URL: 10.5px, #9A9EA4: 「journals.example.edu/…/paper_412.pdf」(決定: 表示は `hostname + "/…/" + ファイル名` に短縮。全長 48 文字超のとき中間を「…」1 個で置換)。
  - 警告ボックス: 10px, #8A6A24, line-height:1.65, 背景 #FFF9F0, border:1px solid #E4CFA6, border-radius:6px, padding:8px 10px: 「このページはサーバーから取得できない可能性があります(学内ネットワーク等)。ボタンを押したときだけ、このタブの PDF を直接送信します — 自動送信はしません。」
  - プライマリボタン: h:32px, border-radius:7px, 背景 var(--pr-a), 白 12px/700, width:100%: 「このタブの PDF を送信」
  - 注記: 9.5px, #9A9EA4, text-align:center, margin-top:-4px: 「private 論文として保存され、共有されません」

### 4.8 全 UI 文言(逐語)

| 区分 | 文言 |
|---|---|
| ブラウザクローム(モック) | 「[2209.03003] Flow Straight and Fast: Learn…」「×」「+」「←」「→」「⟳」「arxiv.org/abs/2209.03003」「訳」「YK」「⋮」 |
| arXiv ページ(モック) | 「arXiv」「arxiv.org > cs > arXiv:2209.03003」「Help \| Advanced Search」「Computer Science > Machine Learning」「[Submitted on 7 Sep 2022 (v1), last revised 25 Feb 2023 (this version, v3)]」「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」「Xingchao Liu, Chengyue Gong, Qiang Liu」「Abstract: We present rectified flow, …(§4.3 全文)」「Subjects: Machine Learning (cs.LG)」「Cite as: arXiv:2209.03003」「Access Paper:」「· View PDF」「· TeX Source」「· Other Formats」 |
| ページ内ピル | 「訳 保存」→ 保存後「✓ 保存済み」 |
| 状態1 | 「訳」「訳読に保存」「arXiv 論文を検出」「⚙」「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」「Liu, Gong, Liu · ICLR 2023 · arXiv:2209.03003 v3」「✓ LaTeX ソースあり — 品質レベル A 見込み」「ステータス」「✓ 読む予定」「すぐ読む」「読んでいる」「タグ」「flow ×」「追加…」「提案: distillation +」「追加先」「輪読会 2026-07 ▾」「ひとことメモ…」「保存 ⏎」「URL のみを送信します — 取得・解析はサーバーで実行」 |
| フッタ | 「直近の取り込み」「Improved Techniques for Training Consistency Models / 翻訳中 68%」「Scaling Rectified Flow Transformers for High-Res… / 今日 8:02」「Adversarial Diffusion Distillation / 7/01」 |
| 状態2 | 「保存しました」「✓」「✓ 書誌」「✓ 構造化」「翻訳中 12%」「サイトで開く ↗」「閉じる」「進捗はツールバーのバッジにも表示されます(処理中スピナー → 完了チェック)。読める部分から開けます。」 |
| 状態3 | 「既にライブラリにあります」「読んでいる」(ドット付きピル)「2026/07/02 追加 · 進捗 42%」「前回: §2.1 整流フロー · 昨日 21:52」「続きから開く ↗」「ステータス変更 ▾」 |
| 状態4 | 「訳読に保存」「PDF を表示中」「BOOT: Data-free Distillation of Denoising Diffusion Models」「書誌は推定」「journals.example.edu/…/paper_412.pdf」「このページはサーバーから取得できない可能性があります(学内ネットワーク等)。ボタンを押したときだけ、このタブの PDF を直接送信します — 自動送信はしません。」「このタブの PDF を送信」「private 論文として保存され、共有されません」 |

### 4.9 データフィールド(デザイン → API フィールド対応)

| デザイン上の値 | API フィールド(plans/03) |
|---|---|
| タイトル / 著者短縮 / 会議 / arXiv ID+版 | `GET /api/ingest/check` → `bib.title` / `bib.authors_short` / `bib.venue`+`bib.year` / `arxiv_id`+`arxiv_version` |
| LaTeX ソース有無・品質 A 見込み | `latex_available` |
| 検出ソース種別 / URL(一般 PDF) | `kind`("arxiv"/"pdf"/"unsupported")/ タブ URL |
| 書誌の信頼度(確定/推定) | `kind === "pdf"` のとき推定(「書誌は推定」バッジ固定表示) |
| 読書ステータス / タグ / 提案タグ / コレクション / ひとことメモ | `POST /api/ingest/arxiv` の `status` / `tags` / (`check.suggested_tags`) / `collection_id` / `quick_note` |
| パイプライン段階・翻訳進捗% | `GET /api/jobs/{id}` → `Job.stage` / `Job.progress_pct`(= `PipelineState`) |
| 追加日 / 読書進捗 / 現ステータス / 前回位置 / 前回閲覧時刻 | `check.saved` → `added_at` / `progress_pct` / `status` / `last_position.section_display` / `last_position.saved_at` |
| 取り込み履歴(タイトル・状態・時刻) | `GET /api/ingest/recent` → `title` / `pipeline` / `completed_at` |
| private フラグ | `POST /api/ingest/pdf` はサーバー側で `visibility: "private"` 固定(plans/03 §3.3) |

## 5. 状態とインタラクション

### 5.1 状態機械(全 7 状態。4 状態=デザイン描画、3 状態=決定で補完)

| 状態 | 発火条件 | 内容 |
|---|---|---|
| 0 未ログイン(決定) | `GET /api/auth/me` が 401 | ヘッダ「訳読に保存」(バッジなし)+本文: 説明「保存にはログインが必要です。」(11.5px, #5B6067, text-align:center, padding:20px 14px 8px)+プライマリボタン(h:32px, radius:7px, bg var(--pr-a), 白 12px/700, margin:0 14px 16px)「ログイン」。クリックで `chrome.tabs.create({ url: WXT_APP_ORIGIN + "/login?from=extension" })` し `window.close()`(plans/01 §6.4)。フッタなし |
| L ローディング(決定) | `check` 取得中 | ヘッダ「訳読に保存」+本文にスケルトン: 書誌タイトル 2 本(h:12px, width:100%/72%, radius:3px, bg #F1EFE9)+メタ 1 本(h:10px, width:55%)+行 3 本(h:22px, width:100%)。`animation: ext-pulse 1200ms ease-in-out infinite`(opacity 1→0.55→1)。300ms 未満で解決した場合もちらつき防止のガードはしない(ポップアップは毎回新規マウントのため。決定)。フッタ「直近の取り込み」は状態 L の間は非表示(check 解決後の遷移先状態で表示。決定) |
| 1 保存前 | `kind==="arxiv" && saved===null` | §4.4 |
| 2 保存直後 | 状態 1 で保存成功(202) | §4.5 |
| 3 既にライブラリ | `saved !== null`、または保存が 409 `duplicate` | §4.6 |
| 4 一般ページ PDF | `kind==="pdf"` | §4.7 |
| 5 非対応ページ(決定) | `kind==="unsupported"` | ヘッダ「訳読に保存」+グレーバッジ「対応外のページ」(状態4バッジと同スタイル: bg #F1EFE9, #777B81, 9.5px/700)+本文説明(10.5px, #9A9EA4, line-height:1.7, padding:14px): 「このページからは取り込めません。arXiv の論文ページ、または PDF を表示中のタブで開いてください。」+フッタ「直近の取り込み」は表示(docs/08 §7 の決定) |

エラー状態(決定):
- `check` が通信失敗: 状態 5 の枠で説明を「サーバーに接続できません。ネットワークを確認して開き直してください。」+セカンダリボタン「再試行」(h:26px, border:1px solid #DDD9CF, radius:6px, 11px, #5B6067。クリックで `queryClient.invalidateQueries(['ext','check'])`)。
- 保存(POST /api/ingest/arxiv)失敗(409 以外): 状態 1 に留まり、保存ボタン直下に 9.5px #A05A42 の行「送信に失敗しました — 再試行キューに保存しました」を表示し、リクエストを再試行キュー(§5.7)へ積む。429 の場合の文言は「回数制限に達しました — しばらくして再試行されます」。決定: キューに積むのはネットワークエラー・5xx・429 のみ。それ以外の 4xx(422 等の恒久エラー)は再送しても成功しないためキューに積まず、文言は「送信に失敗しました」のみ表示する。
- PDF 送信失敗: 状態 4 に留まり、ボタン直下に 9.5px #A05A42「送信に失敗しました」+ボタンラベルを「再送信」に変更(バイト列は押下時に再取得。キューには積まない。§5.7)。413(サーバー判定)およびクライアント側 50MB 超判定(§5.3)は「PDF が 50MB を超えています — 送信できません」でボタン disabled。クライアント側 PDF 判定不合格(§5.3)は「このタブから PDF を取得できませんでした」でボタンは enabled のまま(再試行可。決定)。

### 5.2 状態1 のインタラクション

- **ステータスピル**: 3 択単一選択トグル(`role="radiogroup"`)。クリックで選択切替。選択中スタイルは §4.4(アクセント枠+淡アクセント面+✓)。初期値 `planned`。
- **品質見込み行**: `latex_available === true` → 「✓ LaTeX ソースあり — 品質レベル A 見込み」(#4C7458)。決定: `false` → 「LaTeX ソースなし — 品質レベル B 見込み」(10px, #9A9EA4、✓なし)。`null`(判定不能)→ 行非表示。
- **タグ**: チップ「×」クリックで削除。入力欄(透明 input)で文字入力し **Enter または「,」で確定追加**(IME 変換中 `isComposing===true` の Enter は無視。空白のみ・重複は追加しない。決定)。「提案: distillation +」クリックで当該タグを追加し、サジェスト表示は `suggested_tags` の次の未追加候補へ進む(残なしで非表示)。初期チップ(決定): arXiv のプライマリカテゴリ由来タグが `suggested_tags[0]` に来るが**初期チップは空**(デザインの「flow ×」は入力済み例示とみなす。自動付与はしない — P6)。
- **追加先ドロップダウン**(決定): カスタムドロップダウン(ネイティブ `<select>` 不使用)。開くとトリガ直下 `top:calc(100%+4px); left:0` に幅 200px のリスト(bg #FFFFFF, border:1px solid #DDD9CF, radius:6px, box-shadow:0 12px 32px rgba(20,22,26,0.14), padding:4px)。項目 h:28px, padding:0 9px, 10.5px, #3C4046, radius:4px, ホバー bg #FAF9F5。先頭に「コレクションなし」(選択時 `collection_id: null`)。選択中項目は先頭に「✓」(var(--pr-a))。初期値: 「コレクションなし」(決定: 前回選択の記憶はしない — 保存先の暗黙持ち越しを避ける)。決定: `GET /api/collections` が 0 件または取得失敗のときは選択肢が「コレクションなし」のみのドロップダウンを表示する(行自体は消さない。エラー表示もしない)。トリガのラベルは選択名+「 ▾」。外側クリック・Esc で閉じる。
- **メモ入力**: 1 行テキスト。最大 200 文字(超過入力は無視。決定。plans/02 の `one_line_note` に整合)。
- **保存**: ボタンクリックまたは **Enter キー**(`document` の keydown。ただしフォーカスがタグ入力・メモ入力にあり かつ タグ入力は未確定テキストありの場合はタグ確定を優先。メモ入力上の Enter は保存を実行する。決定)。実行中はボタンを disabled+ラベル「保存中…」(スピナーなし。決定)。成功(202)→ 状態 2 へ。`job_id` を `chrome.storage.local` の `yk_active_jobs` に追加(バックグラウンドポーリング開始)。409 → 状態 3(`existing` を使用)。
- **⚙**: `chrome.runtime.openOptionsPage()`(§5.8)。
- **ホバー**(決定・全状態共通): ボタン・ピル・チップ・履歴行は `filter: brightness(0.97)`(プライマリボタンは `brightness(1.08)`)+ `cursor: pointer`。transition 100ms。デザイン未描画のため最小限とする。

### 5.3 状態2・3・4 のインタラクション

- 状態2 **進捗ポーリング**: `['ext','job', jobId]` を 2,000ms 間隔。`Job.stage`/`progress_pct` を §5.4 でマッピングし進捗行+バーを更新。`failed` 時(決定): 進捗行を「取り込み失敗 — {Job.error.detail}」(9.5px, #A05A42)に置換し、プライマリを「サイトで確認 ↗」に変える(遷移先同じ)。
- 状態2 「**サイトで開く ↗**」: `chrome.tabs.create({ url: `${WXT_APP_ORIGIN}/papers/${library_item_id}` })` → `window.close()`。処理途中でも開ける(ビューア側が部分表示)。
- 状態2 「**閉じる**」: `window.close()`。
- 状態3 「**続きから開く ↗**」: `chrome.tabs.create({ url: `${WXT_APP_ORIGIN}/papers/${saved.library_item_id}` })`(ビューアが `last_position` から復元する。URL パラメータは付けない。決定)。
- 状態3 「**ステータス変更 ▾**」: クリックでボタン直上に StatusDropdown(幅 180px。plans/08 §5.2 の 6 値ドロップダウン仕様: bg #FFFFFF, border:1px solid #DDD9CF, radius:10px, shadow 0 12px 32px rgba(20,22,26,0.14), 項目 h:30px, padding:0 12px, 11.5px, 各行に 7×7 ステータス色ドット+ラベル、現在値に「✓」)。選択で `PATCH /api/library-items/{id} { status }` → 成功で情報行のピルを即時更新しドロップダウンを閉じる。失敗はピル横に「変更できませんでした」(9.5px, #A05A42, 3秒で消える。決定)。
- 状態4 「**このタブの PDF を送信**」: 明示クリック時のみ。(1) `fetch(tab.url, { credentials: "include" })` で PDF バイト列を取得(activeTab の一時ホスト権限を利用。決定: コンテンツスクリプト注入方式は採らない — メッセージサイズ制限を避ける。決定: `AbortController` でタイムアウト 60,000ms、タイムアウトは「送信に失敗しました」扱い)。(2) 判定(決定): サイズは取得後の `blob.size > 50 * 1024 * 1024` で判定(Content-Length ヘッダは信用しない)。PDF 判定はレスポンス Content-Type が `application/pdf` を含む、**または**先頭 5 バイトが `%PDF-` のいずれかを満たせば可(`application/octet-stream` で配信するサーバー対策)。不合格は §5.1 のエラー表示。(3) `POST /api/ingest/pdf`(multipart: `file` + `meta={ source_url: tab.url, title_guess, status: "planned", tags: [], collection_id: null, quick_note: null }`、`Idempotency-Key` 付与)。取得中〜送信中はボタン disabled+「送信中…」。202 → 状態 2 へ(以降 arXiv 保存と同一)。
- **履歴行クリック**(全状態のフッタ): `chrome.tabs.create({ url: WXT_APP_ORIGIN + item.viewer_url })`(API フィールド `viewer_url` → コンポーネント props では camelCase の `viewerUrl` に詰め替える。§3 の `RecentIngestRow`。docs/08 §3.1 の決定)。失敗した取り込み行(`pipeline.stage === "failed"`)は左を「×」(#A05A42)、右を「失敗」(9.5px, #A05A42)とし、クリックで同じくサイトへ(決定)。

### 5.4 パイプライン表示マッピング(決定)

`PipelineState.stage` → 状態2 進捗行・フッタ右端文言:

| stage | 進捗行(状態2) | フッタ右端 |
|---|---|---|
| `queued` / `fetching` | 「書誌 取得中」(var(--pr-a)/600)のみ | 「取得中」 |
| `parsing` / `structuring` | 「✓ 書誌」+「構造化中」(var(--pr-a)/600) | 「構造化中」 |
| `translating_abstract` / `readable` / `translating_body` | 「✓ 書誌」「✓ 構造化」「翻訳中 {progress_pct}%」 | 「翻訳中 {progress_pct}%」 |
| `complete` | 「✓ 書誌」「✓ 構造化」「✓ 翻訳完了」(全て #659471) | `completed_at` の相対表記 |
| `failed` | 「取り込み失敗 — {理由}」(#A05A42) | 「失敗」 |
| `waiting_quota` | 「✓ 書誌」「✓ 構造化」「翻訳待機中」(#9A9EA4) | 「待機中」 |

プログレスバー値 = `progress_pct`(0–100)。「✓」の緑=#659471。

### 5.5 ツールバーアイコンのバッジ(決定: PNG アイコンセット切替)

MV3 の `chrome.action.setBadgeText` はテキストバッジしか描けずデザインの 7×7 ドットを再現できないため、**プリレンダ PNG を `chrome.action.setIcon` で切り替える**:

| 状態 | アイコン | 発火条件 |
|---|---|---|
| 通常 | `icon-{16,32}.png`(角丸正方形 bg #3E5C76+白「訳」) | 既定 |
| 琥珀ドット | `icon-dot-{16,32}.png`(右上に #C49432 ドット、白 1.5px 縁取り) | `unread_notifications > 0` かつ処理中ジョブなし |
| 処理中スピナー | `icon-spin-{0..7}-{16,32}.png` を 125ms 間隔で巡回(8 フレーム、var(--pr-a) 円弧) | `yk_active_jobs` が 1 件以上(service worker 稼働中のみ回転。停止中は spin-0 で静止) |
| 完了チェック | `icon-check-{16,32}.png`(右上に #659471 の ✓ ドット) | 全アクティブジョブ終了時(決定: succeeded が 1 件以上ある場合)に 4,000ms 表示 → 通常(未読あればドット)へ。全件 failed の場合はチェックを表示せず直接 通常/ドット へ(決定) |

- バックグラウンドは §2.3 のポーリング(15,000ms)で各アクティブジョブの `GET /api/jobs/{job_id}` と `me` を確認しアイコンを更新する。
- 未読通知の本体はサイト側ポップオーバーで扱う。拡張は存在示唆のみ(docs/08 §3.2)。

### 5.6 相対時刻フォーマット(決定。`lib/format.ts`)

- `formatCompletedAt(iso)`: 当日 → 「今日 H:MM」/ 前日 → 「昨日 H:MM」/ 同年 → 「M/DD」(月ゼロ埋めなし・日 2 桁ゼロ埋め。デザイン「7/01」に一致。決定)/ それ以前 → 「YYYY/M/DD」。時刻は H ゼロ埋めなし・MM 2 桁(「今日 8:02」)。
- `formatLastSeen(iso)`(状態3 前回位置): 当日 → 「今日 H:MM」/ 前日 → 「昨日 H:MM」(「昨日 21:52」)/ それ以前 → 「M/DD H:MM」。
- `formatAddedAt(iso)`: 常に「YYYY/MM/DD」(ゼロ埋め。「2026/07/02 追加」)。

### 5.7 再試行キュー(docs/08 §6。決定)

- `chrome.storage.local` キー `yk_retry_queue`: `{ id: string(UUID=Idempotency-Key); body: IngestArxivRequest; created_at: string; attempts: number }[]`。**対象は `POST /api/ingest/arxiv` のみ**(PDF バイト列は storage に保持しない。PDF 失敗はポップアップ内再送のみ)。
- background.ts が起動時+ポーリング周期で先頭から再送(同一 `Idempotency-Key` なので二重登録されない。plans/03 §3.2)。成功・409 で除去。決定: §2.3 のタイマーと alarm は「アクティブジョブあり **または** `yk_retry_queue` 非空(自動再送対象あり)」の間維持する(キューだけ残っていても再送が回るように)。`attempts` 上限 20 回。決定: 20 回を超えたエントリは**自動再送の対象から外す**がキューには残す(黙って捨てない — P3)。ポップアップを開くとキュー件数が 1 件以上のとき保存ボタン下に「未送信 {n} 件 — 再試行」行(9.5px, #A05A42, 下線なしボタン)を表示し、クリックで上限超過分も含め全件を即時再送する(手動再送は `attempts` を 0 にリセット)。
- ブラウザ再起動後も storage.local に残る(受け入れ基準)。

### 5.8 オプションページ(⚙ の遷移先。決定)

`entrypoints/options/`。ライトテーマ・幅 520px 中央。項目は 2 つのみ:

1. 「ページ内『訳 保存』ボタン(arXiv 限定)」 — Toggle(plans/08 §5.8 と同寸: トラック 30×17px、ON bg var(--pr-a))。既定 **OFF**。ON 操作時に `chrome.permissions.request({ origins: ["https://arxiv.org/*"] })` → 許可されたら `chrome.scripting.registerContentScripts([{ id: "yk-arxiv-pill", matches: ["https://arxiv.org/abs/*"], js: ["content-scripts/arxiv-pill.js"], runAt: "document_idle" }])`。OFF で `unregisterContentScripts` + `permissions.remove`。設定値は `chrome.storage.sync` キー `yk_inline_pill_enabled`。
2. 「ログイン状態」 — `GET /api/auth/me` の `display_name` / 未ログイン表示+「ログイン」リンク。

サイト側 4f「ブラウザ拡張」カテゴリはインストール案内とこのオプションページへの説明のみ(拡張の設定実体は拡張内。決定)。

### 5.9 manifest(wxt.config.ts で生成。確定)

```jsonc
{
  "manifest_version": 3,
  "name": "訳読 — 論文をライブラリへ",
  "action": { "default_popup": "popup.html" },
  "background": { "service_worker": "background.js" },
  "options_ui": { "page": "options.html", "open_in_tab": true },
  "permissions": ["activeTab", "storage", "alarms", "scripting"],
  "host_permissions": ["https://yakudoku.app/*"],
  "optional_host_permissions": ["https://arxiv.org/*"],
  "icons": { "16": "icons/icon-16.png", "32": "icons/icon-32.png",
             "48": "icons/icon-48.png", "128": "icons/icon-128.png" }
}
```

- 全サイト常駐スクリプトなし(docs/08 §1)。開発ビルドは `host_permissions` に `http://localhost:3000/*` を追加。

### 5.10 ページ内ピルの状態遷移(§4.3 の実装対象)

| 状態 | 表示 | スタイル差分 |
|---|---|---|
| `idle` | ミニロゴ+「保存」 | §4.3 のとおり |
| `saving` | 「保存中…」 | opacity:0.6, pointer-events:none(決定) |
| `saved` | 「✓ 保存済み」 | 文字・枠は同色のまま(デザイナー注記どおり文言のみ変化)。クリック無効(決定) |
| `error` | 「保存」+`title="送信に失敗しました。クリックで再試行"` | idle に戻し再クリック可(決定)。401 のときのみ `title="ログインが必要です — サイトでログインしてください"`(表示・挙動は他の error と同じ。決定) |

- クリックで `POST /api/ingest/arxiv { url: location.href }`(状態1の既定値: status=planned、tags/collection/メモなし)。既に保存済み(check または 409)なら初期表示から `saved`。
- 挿入位置: `h1.title` 要素の直後に inline-flex で追加(arXiv abs ページの DOM 構造変化時は挿入をスキップしてエラーを出さない。決定)。

## 6. 受け入れ基準

### ピクセル一致検証(ビジュアルリグレッション対象)

VRT は Playwright(`apps/extension/tests/vrt/`)で popup.html をモック API(MSW)+固定日時(2026-07-06T09:00+09:00)+固定フィクスチャ(Rectified Flow 論文)で描画し、幅 372px・デザイン枠(border 1px #D6D3C9、radius 10px)付きコンテナで撮影する。

- [ ] 状態1: ヘッダ(padding 11px 14px、ロゴ 20×20 radius 5px、バッジ h17 rgba(101,148,113,0.16)/#4C7458)・書誌(12.5px/600 + 10.5px #9A9EA4 + 10px #4C7458)・ステータスピル(h22、選択中 var(--pr-a) 枠+var(--pr-as) 面+✓、非選択 #DDD9CF 枠 #5B6067)・タグ枠(#DDD9CF radius 6px、チップ h18 #F1EFE9、提案 9.5px var(--pr-a))・追加先(h24 ドロップダウン+メモ #EAE7DE 枠)・保存ボタン(h34 radius 7px var(--pr-a)、⏎ キーキャップ opacity 0.75)・注記(9.5px #9A9EA4 中央)がデザインと一致
- [ ] フッタ: bg #FCFBF8、border-top #F0EDE4、見出し 9.5px/700 letter-spacing 0.4px、スピナー 11×11(border 2px var(--pr-am)/top var(--pr-a))、行 10.5px #3C4046、右端 9.5px #9A9EA4、3 件表示が一致
- [ ] 状態2: 成功バッジ 16×16 円、論文カード(border #E2DFD5 radius 8px、サムネ 40×52 #EFEDE6/#E0DDD3、タイトル 2 行クランプ、進捗行 9.5px「✓ 書誌」「✓ 構造化」#659471+「翻訳中 12%」var(--pr-a)/600、バー h3 トラック #ECE9DF・塗り 12%)・ボタン行(h30 プライマリ flex:1+「閉じる」)・説明 9.5px #9A9EA4 が一致
- [ ] 状態3: ドット付きピル(h22、6×6 ドット var(--pr-a)、文字 #1E2227)+「2026/07/02 追加 · 進捗 42%」+「前回: §2.1 整流フロー · 昨日 21:52」(10.5px #9A9EA4)+「続きから開く ↗」「ステータス変更 ▾」が一致
- [ ] 状態4: グレーバッジ「PDF を表示中」(#F1EFE9/#777B81)、「書誌は推定」バッジ(h14 9px #8A8E94)、警告ボックス(#FFF9F0/#E4CFA6/#8A6A24 10px lh1.65)、ボタン h32、注記中央 9.5px が一致
- [ ] 状態0(未ログイン)・状態5(非対応)・ローディングスケルトン・保存失敗表示・ステータス変更ドロップダウン(w180)の各ストーリーが §5.1〜5.3 の決定仕様と一致
- [ ] ページ内ピル: h24 radius 999px var(--pr-am)枠/var(--pr-as)面/var(--pr-a)文字 10.5px/700、ミニロゴ 13×13、idle→saved の文言変化(arXiv abs の静的 HTML フィクスチャ上で撮影)
- [ ] フォントが IBM Plex Sans JP で描画される(popup.html にフォント同梱。Google Fonts への実行時リクエストをしない — 拡張のオフライン動作とストア審査のため。決定: `@fontsource/ibm-plex-sans-jp` の woff2 400/500/600/700 をバンドル)

### 機能検証

- [ ] arXiv abs ページでツールバーアイコン→「保存」の 2 クリックでライブラリに入る(`POST /api/ingest/arxiv` が 1 回だけ発火)
- [ ] ポップアップ開時に `GET /api/ingest/check` が呼ばれ、arxiv/saved/pdf/unsupported で状態 1/3/4/5 に正しく分岐する
- [ ] 保存前に書誌プレビューと `latex_available` による「✓ LaTeX ソースあり — 品質レベル A 見込み」が表示される(false 時は B 見込み文言)
- [ ] ステータス 3 択(planned/up_next/reading)・タグ(提案クリック追加・× 削除・Enter 確定・IME 変換中 Enter 無視)・コレクション・ひとことメモが `POST /api/ingest/arxiv` のボディに正しく載る
- [ ] メモ入力フォーカス中を含め Enter キーで保存が実行される(タグ入力の未確定テキストがある場合はタグ確定が優先)
- [ ] 保存成功で同一ポップアップ内が状態 2 に切り替わり、2,000ms ポーリングで進捗行・プログレスバーが §5.4 のマッピングどおり更新される
- [ ] 「サイトで開く ↗」「続きから開く ↗」「履歴行クリック」が `${WXT_APP_ORIGIN}/papers/{library_item_id}` を新規タブで開く
- [ ] 保存が 409 `duplicate` のとき状態 3 が `existing` の内容で表示され、重複レコードが作られない
- [ ] 状態 3 の「ステータス変更 ▾」で 6 値から選択でき `PATCH /api/library-items/{id}` が発火、ピルが即時更新される
- [ ] 状態 4 で自動送信が発生せず、「このタブの PDF を送信」クリック時のみ `POST /api/ingest/pdf`(multipart、meta.source_url/title_guess 付き)が発火する。50MB 超・非 PDF はエラー表示で送信しない
- [ ] フッタに直近 3 件(処理中=スピナー+進捗率 / 完了=緑✓+今日 H:MM・M/D / 失敗=×+失敗)が表示される
- [ ] 401 時に状態 0 が表示され、「ログイン」で `/login?from=extension` が開く。ログイン後の開き直しで通常状態になる
- [ ] `POST /api/ingest/arxiv` の失敗が `yk_retry_queue`(storage.local)に `Idempotency-Key` 付きで保持され、ブラウザ再起動後も残り、background の再送で成功時に除去される
- [ ] 処理中ジョブがある間ツールバーアイコンがスピナーフレームになり、完了で 4,000ms チェック表示 → `unread_notifications > 0` なら琥珀ドットへ遷移する
- [ ] ページ内ピルは既定 OFF。オプションページの Toggle ON で `permissions.request`(arxiv.org)+動的登録され、abs ページのタイトル横に表示、クリック保存後「✓ 保存済み」に変わる。OFF で登録解除される
- [ ] manifest が §5.9 と一致し、全サイト常駐スクリプト・不要権限がない(Chrome / Edge 両ストア審査要件)
- [ ] 拡張からの非 GET リクエストが Origin 検証(`YK_ALLOWED_EXTENSION_ORIGINS`)を通過する(結合テスト)
