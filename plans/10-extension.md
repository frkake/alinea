# 10. ブラウザ拡張(apps/extension)完全実装設計 — WXT / MV3

> **対象読者と前提**: 本書は「訳読 / YAKUDOKU」のブラウザ拡張(apps/extension: WXT 0.20.7 + React 19 + TypeScript 5、Manifest V3、Chrome / Edge)の実装者向け。機能仕様の正は docs/08(拡張)と docs/02(取り込み)、ピクセル仕様の正は確定デザイン 3a(extract/3a.md)。API 契約は plans/03(パス・スキーマ・エラー形式)に **完全に一致** させ、本書はエンドポイントを一切再発明しない。トークン・色値は packages/tokens(plans/08)を使う。plans/00・01 と plans/03 の間の不整合(パス表記・クッキー名・CSRF 方式・manifest 権限)は本書では **plans/03 を正** として解消し、§15「⚠ 基盤への追加要求」に修正要求を列挙する。

## 1. 責務と全体像

- 拡張は本プロダクト **唯一の取り込み経路**(docs/08 §1。例外: 参考文献の「+この論文も取り込む」のみ web からも `POST /api/ingest/arxiv`)。
- 提供する面は 3 つだけ: **① ツールバーポップアップ(幅 372px・4 状態+補助状態)**、**② ツールバーアイコンのバッジ(スピナー/チェック/琥珀ドット)**、**③ arXiv ページ内「訳 保存」ピル(オプトイン・arxiv.org 限定)**。読解機能・翻訳表示・チャットは一切持たない(docs/08 §7)。
- 原則 **URL のみ送信・取得と解析はサーバー**。タブ内容の送信は状態 4 の「このタブの PDF を送信」の明示クリック時のみ(docs/08 §4)。
- 進捗のリアルタイム化は **SSE を使わずポーリングで確定**(決定。plans/01 §3.1: MV3 service worker は SSE 常時接続を維持できない)。間隔: **ポップアップ表示中 2,000ms / バックグラウンド(処理中ジョブあり)15,000ms**。
- UI 言語は日本語のみ(spec-decisions D Q5)。`_locales` は作らない。

## 2. プロジェクト構成(WXT ファイルツリー)

plans/00 §2 の骨格(`entrypoints/popup/`, `background.ts`, `arxiv-pill.content.ts`, `lib/api.ts`, `lib/pdf-detect.ts`)を維持し、以下に確定する。

```
apps/extension/
├── package.json                  # name: "@yakudoku/extension"
│                                 # scripts: dev / build / zip / zip:edge / test / lint / typecheck / compile
├── wxt.config.ts                 # §3.1(manifest 定義の単一ソース)
├── tsconfig.json                 # extends ルート。paths: {"@/*": ["./src/*"]}
├── vitest.config.ts
├── .env                          # WXT_API_BASE_URL=http://localhost:8000 / WXT_APP_BASE_URL=http://localhost:3000
├── .env.production               # WXT_API_BASE_URL=https://yakudoku.app / WXT_APP_BASE_URL=https://yakudoku.app
├── public/
│   └── icons/                    # §9.2 の全 PNG(16/32/48/128 × 各状態)
│       ├── icon-{16,32,48,128}.png            # 通常(「訳」角丸ロゴ)
│       ├── icon-dot-{16,32,48,128}.png        # 通常+琥珀ドット(#C49432)
│       ├── check-{16,32,48,128}.png           # 完了チェック
│       ├── check-dot-{16,32,48,128}.png       # 完了チェック+琥珀ドット
│       └── spinner-{0..7}-{16,32,48}.png      # 処理中スピナー 8 フレーム(ドット焼き込み済み)
└── src/
    ├── entrypoints/
    │   ├── popup/
    │   │   ├── index.html        # <html lang="ja">。Google Fonts <link>(IBM Plex Sans JP 400/500/600/700)
    │   │   ├── main.tsx          # ReactDOM.createRoot + tokens CSS import(§6.1)
    │   │   ├── App.tsx           # 状態機械(§5)とビューの出し分け
    │   │   ├── popup.css         # §6.1(3a の逐語スタイル値)
    │   │   ├── states/
    │   │   │   ├── SaveForm.tsx      # 状態1: 保存前(§6.2)
    │   │   │   ├── Saved.tsx         # 状態2: 保存直後(§6.3)
    │   │   │   ├── Existing.tsx      # 状態3: 既にライブラリ(§6.4)
    │   │   │   ├── GenericPdf.tsx    # 状態4: 一般ページ PDF(§6.5)
    │   │   │   ├── Unsupported.tsx   # 補助: 対応外ページ(§6.6)
    │   │   │   ├── LoginPrompt.tsx   # 補助: 未ログイン(§4.3)
    │   │   │   ├── ErrorView.tsx     # 補助: 判定失敗・オフライン(§6.6)
    │   │   │   └── SettingsView.tsx  # 補助: ⚙ 拡張設定(§10.2)
    │   │   └── components/
    │   │       ├── PopupHeader.tsx   # ロゴ+タイトル+バッジ+⚙(§6.1)
    │   │       ├── RecentIngests.tsx # 直近の取り込み3件フッタ(§8)
    │   │       ├── StatusPillGroup.tsx  # 3択単一選択ピル
    │   │       ├── TagInput.tsx         # チップ+自由入力+提案
    │   │       ├── CollectionSelect.tsx # 追加先ドロップダウン
    │   │       ├── StatusDropdown.tsx   # 状態3「ステータス変更 ▾」(6値)
    │   │       ├── FailedQueueBanner.tsx # 失敗キュー再試行 UI(§11.4)
    │   │       └── Spinner.tsx          # 11×11 CSS スピナー
    │   ├── background.ts         # バッジ管理・ポーリング・ピル用メッセージ処理(§9)
    │   └── arxiv-pill.content.ts # 「訳 保存」ピル(registration: "runtime"、§10)
    ├── lib/
    │   ├── api.ts                # packages/api-client の createClient ラッパ(§4.1)
    │   ├── arxiv.ts              # arXiv URL 正規化・判定正規表現(§5.2)
    │   ├── pdf-detect.ts         # タブ内 PDF 判定・書誌ローカル推定(§11.1)
    │   ├── pipeline.ts           # PipelineState → 表示行の写像(§7.3。純関数・Vitest 対象)
    │   ├── format.ts             # 「今日 8:02」「昨日 21:52」「7/01」の相対表記(§8.2)
    │   ├── storage.ts            # chrome.storage.local の型付きキー定義(§2.1)
    │   ├── queue.ts              # 失敗キュー(storage.local + IndexedDB)(§11.3)
    │   ├── badge.ts              # アイコン差し替えロジック(§9.2)
    │   └── messages.ts           # runtime メッセージの discriminated union(§10.3)
    └── assets/                   # (空。トークン CSS は @yakudoku/tokens から import)
```

- 決定: React コンポーネントは apps/web と共有しない(plans/08 §1.3 の決定に従う)。共有するのは `@yakudoku/tokens`(CSS 変数・TS 定数)と `@yakudoku/api-client`(型+fetch クライアント)のみ。
- 決定: WXT の `srcDir` は `"src"`(plans/00 のツリーに一致させる)。

### 2.1 storage.local の型付きキー(lib/storage.ts で一元定義)

| キー | 型 | 用途 | TTL/更新 |
|---|---|---|---|
| `cache:recentIngests` | `{ fetchedAt: number; items: RecentIngest[] }` | 直近の取り込み 3 件の即時表示(§8) | ポップアップ開時+ポーリングごとに上書き |
| `cache:collections` | `{ fetchedAt: number; items: { id: string; name: string }[] }` | 追加先ドロップダウン | 60,000ms 超で再取得 |
| `cache:me` | `{ fetchedAt: number; unread: number } \| null` | 琥珀ドット判定・ログイン状態キャッシュ | 60,000ms 超で再取得。401 で null |
| `settings:arxivPillEnabled` | `boolean`(既定 `false`) | ページ内ピルのオプトイン(§10) | 設定ビューで変更 |
| `ui:accent` | `"slate" \| "green" \| "purple" \| "terracotta"`(既定 `"slate"`。plans/08 §2.3 の `AccentKey` と同一値 — accents.css の `html[data-accent="…"]` セレクタに一致させる) | ポップアップ・ピルのアクセント色(§6.1) | §6.1 の同期規則 |
| `queue:failedSaves` | `FailedSave[]`(§11.3) | arXiv URL 保存の失敗キュー | 再試行成功/破棄で削除 |
| `badge:state` | `{ mode: "idle" \| "processing" \| "check"; checkSince: number \| null }` | SW 再起動をまたぐバッジ状態(§9.3) | background が更新 |

PDF バイト列の失敗キューのみ storage.local ではなく IndexedDB に置く(§11.3。storage.local の 10MB 制限回避)。

## 3. manifest(MV3)の完全形

### 3.1 wxt.config.ts(完全形)

```ts
// apps/extension/wxt.config.ts
import { defineConfig } from "wxt";

export default defineConfig({
  srcDir: "src",
  modules: ["@wxt-dev/module-react"],
  manifest: ({ mode }) => ({
    name: "訳読 — 論文読解ワークベンチ",
    description:
      "arXiv 論文を2クリックで訳読ライブラリへ保存。URL のみを送信し、取得・解析はサーバーで実行します。",
    version: "1.0.0",
    action: {
      default_title: "訳読に保存",
      // default_popup / default_icon は WXT が entrypoints/popup と public/icons から自動生成
    },
    permissions: ["activeTab", "storage", "cookies", "alarms", "scripting"],
    host_permissions:
      mode === "development"
        ? ["http://localhost/*"]          // 開発: web=:3000 / api=:8000(match pattern はポートを区別しない)
        : ["https://yakudoku.app/*"],
    optional_host_permissions: ["https://arxiv.org/*"],
    minimum_chrome_version: "120",
  }),
});
```

### 3.2 ビルド後 manifest.json(本番・確定形)

```json
{
  "manifest_version": 3,
  "name": "訳読 — 論文読解ワークベンチ",
  "description": "arXiv 論文を2クリックで訳読ライブラリへ保存。URL のみを送信し、取得・解析はサーバーで実行します。",
  "version": "1.0.0",
  "action": {
    "default_title": "訳読に保存",
    "default_popup": "popup.html",
    "default_icon": { "16": "icons/icon-16.png", "32": "icons/icon-32.png", "48": "icons/icon-48.png" }
  },
  "background": { "service_worker": "background.js" },
  "icons": { "16": "icons/icon-16.png", "32": "icons/icon-32.png", "48": "icons/icon-48.png", "128": "icons/icon-128.png" },
  "permissions": ["activeTab", "storage", "cookies", "alarms", "scripting"],
  "host_permissions": ["https://yakudoku.app/*"],
  "optional_host_permissions": ["https://arxiv.org/*"],
  "minimum_chrome_version": "120"
}
```

### 3.3 権限決定表(すべて理由付き)

| 権限 | 決定 | 用途と限定 |
|---|---|---|
| `activeTab` | 採用 | 現在タブの URL・タイトル取得(ポップアップ判定)と、状態 4 でユーザーがボタンを押したときの一時的オリジン権限によるタブ内 PDF の `fetch`(§11.2)。全タブ読み取り権限(`tabs` の URL 常時読取や `<all_urls>`)は **使わない** |
| `storage` | 採用 | §2.1 のキャッシュ・失敗キュー・設定。閲覧履歴は保存しない |
| `cookies` | 採用 | `https://yakudoku.app` の セッションクッキー `yk_session` の **存在確認のみ**(値は使わない。§4.2)と `chrome.cookies.onChanged` によるログイン/ログアウト即時検知。host_permissions が yakudoku.app 限定のため読み取り可能ドメインも yakudoku.app に限定される |
| `alarms` | 採用(決定) | 処理中ジョブのバッジポーリング(§9.3)と未読ドット更新。MV3 SW は 30 秒アイドルで停止するため `setInterval` では実現できない |
| `scripting` | 採用(決定) | ページ内ピルの **動的登録**(`chrome.scripting.registerContentScripts`)。manifest 静的宣言だと既定オフ(docs/08 §5)を表現できないため |
| `host_permissions: https://yakudoku.app/*` | 採用(決定) | ① API 呼び出しでセッションクッキーを same-site 扱いで送るため(plans/01 §6.4)、② `chrome.cookies` の対象ドメイン。**この 1 ドメインのみ** |
| `optional_host_permissions: https://arxiv.org/*` | 採用 | ページ内ピル用。既定は未付与で、設定オン時に `chrome.permissions.request()` で要求(plans/00 §2 の方針どおりオプトイン) |
| 不採用: `tabs` / `webRequest` / `<all_urls>` / `notifications` / `offscreen` | — | docs/08 §7(閲覧履歴収集・自動スキャンをしない)。通知本体はサイト側 4a |

⚠ plans/00 §2 の「permissions は activeTab + storage の 2 つ、host_permissions は arxiv.org のみ」は plans/01 §6.4(yakudoku.app の host_permissions がクッキー認証の前提)と両立しないため、本表に更新が必要(§15-1)。

## 4. 認証 — セッションクッキー共有

### 4.1 API クライアント(lib/api.ts)

```ts
// src/lib/api.ts
import { createApiClient } from "@yakudoku/api-client";

export const api = createApiClient({
  baseUrl: `${import.meta.env.WXT_API_BASE_URL}/api`, // 本番: https://yakudoku.app/api(plans/03 §1.1。/v1 プレフィックスなし)
  credentials: "include",                              // yk_session クッキー同送
});
export const APP_BASE_URL: string = import.meta.env.WXT_APP_BASE_URL;
```

- クッキーは `yk_session`(HttpOnly / Secure / SameSite=Lax。plans/03 §1.3)。拡張は host_permissions を持つため Chromium が same-site 扱いにし、Lax のまま送信される(plans/01 §6.4 の決定)。
- CSRF: plans/03 §1.3 は「非 GET リクエストの Origin 検証」。拡張ページ発のリクエストの `Origin` は `chrome-extension://{EXTENSION_ID}` になるため、サーバーの許可 Origin リストに拡張オリジンを追加する必要がある(**§15-2 の追加要求**。環境変数 `EXTENSION_ALLOWED_ORIGINS` にカンマ区切りで Chrome/Edge の各ストア配布 ID を設定。`APP_ENV=development` では `chrome-extension://*` を許可)。
- 拡張トークン(`yk_ext_…`、plans/03 §2.7)は v1 では使わない(決定)。クッキーが使えない将来環境(Safari 等)のフォールバックとして API 側の実装のみ維持する(plans/03 §1.2.1 のとおり)。

### 4.2 ログイン状態の判定順序

1. `chrome.cookies.get({ url: `${APP_BASE_URL}/`, name: "yk_session" })` — クッキー不存在なら **ネットワークを介さず** 未ログイン確定(ポップアップを 0ms で LoginPrompt に)。決定: クッキー判定 URL は文字列を直書きせず `APP_BASE_URL`(§4.1 のビルド時 env)を使う。本番 `https://yakudoku.app/`、開発 `http://localhost:3000/`(以降のコード例で `https://yakudoku.app/` と書いた箇所も同様に `APP_BASE_URL` に読み替える)。
2. クッキーがあれば `GET /api/auth/me`(§2.1 の `cache:me` が 60,000ms 以内ならスキップ)。**401** → LoginPrompt。200 → `unread_notifications` を `cache:me` に保存(琥珀ドットの源泉。plans/03 §2.6)。
3. `chrome.cookies.onChanged`(background)で `yk_session` の追加/削除を検知したら `cache:me` を破棄し、バッジを即時再評価する(§9.4)。

### 4.3 未ログイン UI(LoginPrompt)

- ヘッダ: ロゴ「訳」20×20 + タイトル「ログインが必要です」12.5px/700 + 右端「⚙」。
- 本文(padding:12px 14px): 説明 10.5px #5B6067 line-height:1.65「訳読のアカウントでログインすると、このページの論文をライブラリに保存できます。」+ プライマリボタン(h:34px, radius:7px, 背景 var(--pr-a), 白 12.5px/700)「ログインして続ける」。
- クリックで `chrome.tabs.create({ url: `${APP_BASE_URL}/login?from=extension` })`(plans/01 §6.4 の導線)。コールバック連携は実装しない — ログイン完了後にポップアップを開き直せばクッキーが有効(同 §6.4 の決定)。
- フッタの「直近の取り込み」は未ログイン時は表示しない(キャッシュも 401 検知時に消去する — 決定: 共有 PC でのデータ残留防止)。

## 5. ポップアップの状態機械

### 5.1 ビュー型(discriminated union)

```ts
// App.tsx 内
import type { components } from "@yakudoku/api-client";
type IngestCheck = components["schemas"]["IngestCheckResponse"];  // plans/03 §3.1
type Job = components["schemas"]["Job"];                          // plans/03 §1.7

type PopupView =
  | { view: "loading" }                                            // スケルトン(§6.6)
  | { view: "login" }                                              // §4.3
  | { view: "save_form"; check: IngestCheck; tabUrl: string }      // 状態1
  | { view: "saved"; libraryItemId: string; jobId: string; title: string; thumbnailUrl: string | null } // 状態2
  | { view: "existing"; saved: NonNullable<IngestCheck["saved"]>; title: string }                        // 状態3
  | { view: "generic_pdf"; tabUrl: string; titleGuess: string | null }                                   // 状態4
  | { view: "unsupported" }                                        // 補助(docs/08 §7 の決定)
  | { view: "error"; message: string; retry: () => void }          // 補助
  | { view: "settings"; back: PopupView };                         // ⚙(§10.2)
```

### 5.2 arXiv URL 判定正規表現(lib/arxiv.ts)

サーバー判定(`GET /api/ingest/check` の `kind`)が正だが、スケルトンの出し分けとピルの適用判定のためクライアントにも同等の正規化を持つ。

```ts
// src/lib/arxiv.ts
/** 新形式 2209.03003 / 旧形式 cs/9901002・math.GT/0309136 に対応(docs/02 §2) */
export const ARXIV_URL_RE =
  /^https?:\/\/(?:www\.)?arxiv\.org\/(?:abs|pdf|html)\/(?<id>\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?\/\d{7})(?<ver>v\d+)?(?:\.pdf)?\/?(?:[?#].*)?$/;

export function parseArxivUrl(url: string): { arxivId: string; version: string | null } | null {
  const m = ARXIV_URL_RE.exec(url);
  return m?.groups ? { arxivId: m.groups.id, version: m.groups.ver ?? null } : null;
}
```

### 5.3 判定フロー(分岐図)

```
ポップアップ open
 ├─ chrome.tabs.query({active:true, currentWindow:true}) → tab
 │   └─ tab.url が http(s) 以外(chrome:// 等・undefined)────────────→ unsupported
 ├─ §4.2 手順1–2(クッキー→ /api/auth/me)
 │   └─ 未ログイン ─────────────────────────────────────────────→ login
 ├─ (並行)cache:recentIngests を読み即時フッタ描画。ARXIV_URL_RE で
 │   マッチすればフォーム骨格のスケルトンを先行表示(view: loading)
 ├─ GET /api/ingest/check?url={encodeURIComponent(tab.url)}
 │   ├─ ネットワーク失敗 ────────────────────────────────────────→ error(再試行ボタン)
 │   ├─ res.saved !== null ─────────────────────────────────────→ existing(状態3)
 │   ├─ res.kind === "arxiv" ───────────────────────────────────→ save_form(状態1)
 │   ├─ res.kind === "pdf" ─────────────────────────────────────→ generic_pdf(状態4)
 │   └─ res.kind === "unsupported" ─────────────────────────────→ unsupported
 └─ save_form で保存成功(202)────────────────────────────────────→ saved(状態2)
     └─ 保存で 409 duplicate(plans/03 §3.2)──────────────────────→ existing(problem.existing を流用)
```

### 5.4 初期化コード(App.tsx の要点)

```tsx
async function resolveInitialView(): Promise<PopupView> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url ?? "";
  if (!/^https?:\/\//.test(url)) return { view: "unsupported" };

  const cookie = await chrome.cookies.get({ url: "https://yakudoku.app/", name: "yk_session" });
  if (!cookie) return { view: "login" };
  const me = await getMeCached();                 // §4.2 手順2(401 → null)
  if (!me) return { view: "login" };

  const { data, error } = await api.GET("/ingest/check", { params: { query: { url } } });
  if (error || !data) return { view: "error", message: "ページの判定に失敗しました", retry: init };

  if (data.saved) return { view: "existing", saved: data.saved, title: data.bib?.title ?? tab.title ?? "" };
  if (data.kind === "arxiv") return { view: "save_form", check: data, tabUrl: url };
  if (data.kind === "pdf")
    return { view: "generic_pdf", tabUrl: url, titleGuess: guessPdfTitle(tab) };  // §11.1
  return { view: "unsupported" };
}
```

- 性能目標(決定): クッキー存在+キャッシュ命中時、`/ingest/check` 応答までスケルトン表示。**ポップアップ open → 状態確定を p50 600ms / p95 1,500ms** とする(check API 自体は Redis 24h キャッシュ済み。plans/03 §3.1)。

## 6. UI 実装(3a の完全スタイル値)

### 6.1 共通シェル

- ポップアップ実体は `<body>` = 372px 固定(`body { width: 372px; margin: 0 }`)。モック上の外枠 border(#D6D3C9)・box-shadow・角丸 10px・吹き出し矢印は **Chrome のポップアップウィンドウ表現に置き換わるため実装しない**(決定。内部レイアウト値のみ実装)。
- main.tsx で `import "@yakudoku/tokens/css/tokens.css"; import "@yakudoku/tokens/css/accents.css"; import "./popup.css";`(plans/08 §1.3)。書体は index.html の `<link>` で Google Fonts(IBM Plex Sans JP 400/500/600/700)を読み、オフライン時は `system-ui` フォールバック(`font-family: 'IBM Plex Sans JP', system-ui, sans-serif`)。
- テーマ: **ライト固定**(確定デザイン 3a にダーク版が存在しないため — 決定)。アクセントは `<html data-accent={ui:accent}>`。`ui:accent` はポップアップが `GET /api/settings` を **呼ばず**、apps/web がログイン中に `chrome.runtime` 連携を持たないため、次の規則で同期する(決定): 拡張設定ビュー(§10.2)にアクセント 4 色スウォッチを置き、storage.local `ui:accent` に保存する(サイト側設定とは独立。既定 `slate` = #3E5C76。キー名は plans/08 §2.3 の `AccentKey` に一致)。
- ヘッダ(全状態共通・PopupHeader.tsx): `padding:11px 14px; border-bottom:1px solid #F0EDE4; display:flex; align-items:center; gap:8px`。ロゴ 20×20 `border-radius:5px; background:var(--pr-a)` 白文字「訳」10.5px/700。タイトル 12.5px/700 #1E2227。バッジは状態ごと(§6.2〜6.5)。右端 `margin-left:auto` に「⚙」12px #9A9EA4(クリックで `settings` ビューへ)。
- 本文コンテナ: `padding:12px 14px; display:flex; flex-direction:column;`(gap は状態ごとに 11px / 10px)。
- フッタ「直近の取り込み」(§8)は **全状態で表示**(docs/08 §3.1 の決定: 右列モックの省略は省略とみなす)。ただし login ビューでは非表示(§4.3)。

popup.css の代表値(逐語。全クラスはこの規則で 3a 抽出値をそのまま写す):

```css
/* popup.css(抜粋 — 値はすべて extract/3a.md の逐語) */
body { width: 372px; margin: 0; background: #fff; font-family: 'IBM Plex Sans JP', system-ui, sans-serif; }
.hdr { display: flex; align-items: center; gap: 8px; padding: 11px 14px; border-bottom: 1px solid #F0EDE4; }
.hdr-logo { width: 20px; height: 20px; border-radius: 5px; background: var(--pr-a); color: #fff;
            font-size: 10.5px; font-weight: 700; display: grid; place-items: center; }
.hdr-title { font-size: 12.5px; font-weight: 700; color: #1E2227; }
.badge-arxiv { height: 17px; padding: 0 7px; border-radius: 999px; background: rgba(101,148,113,0.16);
               color: #4C7458; font-size: 9.5px; font-weight: 700; display: inline-flex; align-items: center; }
.badge-pdf { height: 17px; padding: 0 7px; border-radius: 999px; background: #F1EFE9; color: #777B81;
             font-size: 9.5px; font-weight: 700; display: inline-flex; align-items: center; }
.badge-ok { width: 16px; height: 16px; border-radius: 50%; background: rgba(101,148,113,0.16);
            color: #4C7458; font-size: 9px; font-weight: 700; display: grid; place-items: center; }
.gear { margin-left: auto; font-size: 12px; color: #9A9EA4; background: none; border: 0; cursor: pointer; }
.body { padding: 12px 14px; display: flex; flex-direction: column; gap: 11px; }
.pill { height: 22px; padding: 0 9px; border: 1px solid #DDD9CF; border-radius: 999px;
        font-size: 10.5px; color: #5B6067; background: #fff; display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
.pill[aria-pressed="true"] { border-color: var(--pr-a); color: var(--pr-a); background: var(--pr-as); font-weight: 600; }
.row { display: flex; align-items: center; gap: 8px; }
.row-label { width: 58px; flex: none; font-size: 10.5px; font-weight: 600; color: #5B6067; }
.tagbox { flex: 1; border: 1px solid #DDD9CF; border-radius: 6px; padding: 4px 7px;
          display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.tagchip { height: 18px; padding: 0 6px; border-radius: 3px; background: #F1EFE9; color: #3C4046;
           font-size: 10px; display: inline-flex; align-items: center; gap: 3px; }
.tagchip button { color: #9A9EA4; background: none; border: 0; cursor: pointer; }
.taginput { flex: 1; min-width: 40px; border: 0; outline: 0; font-size: 10px; color: #3C4046; }
.taginput::placeholder { color: #B0B4BA; }
.tagsuggest { margin-left: auto; font-size: 9.5px; color: var(--pr-a); background: none; border: 0; cursor: pointer; }
.select { display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 9px;
          border: 1px solid #DDD9CF; border-radius: 6px; font-size: 10.5px; color: #3C4046; background: #fff; }
.memo { flex: 1; height: 24px; padding: 0 9px; border: 1px solid #EAE7DE; border-radius: 6px; font-size: 10.5px; }
.memo::placeholder { color: #B0B4BA; }
.btn-save { display: flex; align-items: center; justify-content: center; gap: 7px; height: 34px;
            border-radius: 7px; background: var(--pr-a); color: #fff; font-size: 12.5px; font-weight: 700; border: 0; cursor: pointer; }
.keycap { font-size: 9.5px; font-weight: 500; opacity: 0.75; border: 1px solid rgba(255,255,255,0.4);
          border-radius: 3px; padding: 0 5px; }
.privacy { font-size: 9.5px; color: #9A9EA4; text-align: center; margin-top: -3px; }
.recent { border-top: 1px solid #F0EDE4; padding: 9px 14px 11px; display: flex; flex-direction: column;
          gap: 6px; background: #FCFBF8; }
.recent-h { font-size: 9.5px; font-weight: 700; color: #9A9EA4; letter-spacing: 0.4px; }
.recent-row { display: flex; align-items: center; gap: 7px; font-size: 10.5px; color: #3C4046;
              cursor: pointer; background: none; border: 0; text-align: left; }
.recent-title { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.recent-meta { font-size: 9.5px; color: #9A9EA4; flex: none; }
.spin { width: 11px; height: 11px; flex: none; border-radius: 50%; border: 2px solid var(--pr-am);
        border-top-color: var(--pr-a); animation: rot 0.8s linear infinite; }
@keyframes rot { to { transform: rotate(360deg); } }
.warnbox { font-size: 10px; color: #8A6A24; line-height: 1.65; background: #FFF9F0;
           border: 1px solid #E4CFA6; border-radius: 6px; padding: 8px 10px; }
.progress { height: 3px; border-radius: 2px; background: #ECE9DF; overflow: hidden; }
.progress > i { display: block; height: 100%; background: var(--pr-a); transition: width 300ms; }
```

### 6.2 状態 1: 保存前フォーム(SaveForm.tsx)

構成と値は extract/3a §2.4 の逐語。本文 `gap:11px` で上から:

1. **書誌ブロック**(column, gap:4px): タイトル 12.5px/600 line-height:1.5(`check.bib.title`)/ メタ 10.5px #9A9EA4 `{authors_short} · {venue} · arXiv:{arxiv_id} {arxiv_version}`(venue が null なら区切りごと省略)/ 品質見込み 10px #4C7458 — `check.latex_available === true` のとき「✓ LaTeX ソースあり — 品質レベル A 見込み」、`false` のとき(決定・デザイン未掲載の対偶)グレー #9A9EA4 で「LaTeX ソースなし — 品質レベル B(PDF 由来)見込み」。
2. **ステータス行**: ラベル「ステータス」(58px)+ 3 択単一選択ピル `planned`(「読む予定」・既定選択・✓ 前置)/ `up_next`(「すぐ読む」)/ `reading`(「読んでいる」)。`role="radiogroup"`、`aria-pressed` でスタイル切替(.pill 参照)。
3. **タグ行**: ラベル「タグ」+ .tagbox。チップ「{tag} ×」(× クリックで削除)/ 自由入力(placeholder「追加…」、Enter または `,` で確定 — ただし **IME 変換確定の Enter(`e.isComposing`)と保存の Enter を区別**: 入力欄にフォーカスがありテキストが空でないときの Enter はタグ確定、空のときは保存)/ 提案 `.tagsuggest`「提案: {suggested_tags の未採用先頭 1 件} +」— クリックでチップ化し次の提案を表示(承認式。docs/02 §7)。
4. **追加先行**: ラベル「追加先」+ CollectionSelect(`.select`。既定表示「コレクションなし ▾」、選択肢は `GET /api/collections` の一覧を更新降順。`cache:collections` 60 秒)+ メモ入力 `.memo`(placeholder「ひとことメモ…」→ `quick_note`)。
5. **保存ボタン** `.btn-save`: 「保存」+ keycap「⏎」。
6. **プライバシー注記** `.privacy`: 「URL のみを送信します — 取得・解析はサーバーで実行」(常時表示。docs/08 §1)。

**Enter 保存の確定仕様**(決定): `window` の `keydown` で `key === "Enter" && !e.isComposing` かつ (a) フォーカスがタグ入力に無い、または (b) タグ入力が空、のとき保存を実行。`repeat` は無視。保存実行中はボタンを `disabled` + ボタン内スピナーにし二重送信を防ぐ(Idempotency-Key でも防御。§7.1)。

### 6.3 状態 2: 保存直後(Saved.tsx)

- ヘッダ: タイトル「保存しました」+ `.badge-ok`「✓」。
- 本文(gap:10px):
  - 論文カード `border:1px solid #E2DFD5; border-radius:8px; padding:10px 12px; display:flex; gap:10px`。サムネイル 40×52 `border-radius:4px; background:#EFEDE6; border:1px solid #E0DDD3`(決定: v1 は **常にプレースホルダ**。`GET /api/jobs/{id}` の Job 型にサムネイル URL が含まれず、ポップアップは短命のため追加取得しない。3a §2.5 もプレースホルダ表示。`PopupView.saved.thumbnailUrl` は常に null で渡す)。右カラム(gap:4px, min-width:0, flex:1): タイトル 11.5px/600 line-height:1.45 2 行クランプ / パイプライン進捗行(§7.3)9.5px gap:6px / `.progress` バー(width = `progress_pct`%)。
  - ボタン行(gap:6px): プライマリ flex:1 h:30px radius:6px 背景 var(--pr-a) 白 11.5px/600「サイトで開く ↗」→ `chrome.tabs.create({ url: `${APP_BASE_URL}/papers/${libraryItemId}` })`(plans/03 §3.4 の `viewer_url` と同形)/ セカンダリ h:30px padding:0 12px border:1px solid #DDD9CF radius:6px 11.5px #5B6067「閉じる」→ `window.close()`。
  - 説明 9.5px #9A9EA4 line-height:1.6: 「進捗はツールバーのバッジにも表示されます(処理中スピナー → 完了チェック)。読める部分から開けます。」
- 進捗は §7.2 の 2,000ms ポーリングで更新する。決定: 202 受領直後・初回ポーリング応答前は `stage: "queued"`・`progress_pct: 0` として進捗行とバーを描画する(空白やスケルトンにしない)。

### 6.4 状態 3: 既にライブラリ(Existing.tsx)

- ヘッダ: タイトル「既にライブラリにあります」(バッジなし)。
- 本文(gap:10px):
  - 情報行(gap:8px, 11px #5B6067): ステータスピル(h:22px, border:1px solid #DDD9CF, radius:999px, 10.5px, 文字 #1E2227, 左に 6×6 円ドット — ドット色は `--st-*` トークンの現ステータス色)+「{added_at を YYYY/MM/DD} 追加 · 進捗 {progress_pct}%」。
  - 前回位置 10.5px #9A9EA4: 「前回: {last_position.section_display} · {saved_at の相対表記}」(§8.2 の format.ts)。`last_position` が null なら行ごと省略(決定)。
  - ボタン行: プライマリ「続きから開く ↗」→ `${APP_BASE_URL}/papers/${library_item_id}`(位置復元はサーバー保存の last_position によりアプリ側で行われる)/ セカンダリ「ステータス変更 ▾」→ StatusDropdown(6 値 `planned/up_next/reading/done/reread/on_hold` を日本語ラベル+ドット色で列挙)。選択で `PATCH /api/library-items/{id} { status }`(session|ext スコープ内。plans/03 §5.4)→ 成功でピル表示を更新(楽観更新はしない — 決定)。失敗(4xx/5xx/ネットワーク断)時はピル表示を変更せず、情報行の下に `.warnbox`「ステータスを変更できませんでした — もう一度お試しください」を表示する(決定)。
- 取り込みパイプラインが未完(`saved.pipeline` 非 null)の場合は情報行の下に §7.3 の進捗行+バーを追加表示する(決定: 状態 2 と同一コンポーネント)。

### 6.5 状態 4: 一般ページ PDF(GenericPdf.tsx)

- ヘッダ: タイトル「訳読に保存」+ `.badge-pdf`「PDF を表示中」。
- 本文(gap:10px):
  - 書誌(gap:3px): タイトル 11.5px/600(`titleGuess`。null なら「(タイトル不明の PDF)」)+ インラインバッジ「書誌は推定」(h:14px, padding:0 5px, radius:3px, 背景 #F1EFE9, 文字 #8A8E94, 9px/600)/ URL 10.5px #9A9EA4(中央省略 `text-overflow` で 1 行)。
  - `.warnbox`: 「このページはサーバーから取得できない可能性があります(学内ネットワーク等)。ボタンを押したときだけ、このタブの PDF を直接送信します — 自動送信はしません。」
  - プライマリボタン h:32px radius:7px 背景 var(--pr-a) 白 12px/700: 「このタブの PDF を送信」→ §11.2。
  - 注記 `.privacy`(margin-top:-4px): 「private 論文として保存され、共有されません」。
- 送信成功(202)→ 状態 2 と同一の Saved ビューへ遷移(決定: タイトルは `title_guess`。`title_guess` が null の場合は状態 4 と同じ「(タイトル不明の PDF)」を表示する)。

### 6.6 補助状態

- **loading**: ヘッダ(タイトル「訳読に保存」)+ 本文に高さ 96px のスケルトン(背景 #F6F4EE、radius:6px、pulse アニメーション)+ フッタ(キャッシュがあれば実データ)。
- **unsupported**: ヘッダ「訳読に保存」+ グレーバッジ「対応外ページ」(`.badge-pdf` と同スタイル — 決定)。本文 10.5px #5B6067 line-height:1.65: 「このページは取り込みに対応していません。arXiv の論文ページ、または PDF を表示中のタブで保存できます。」+ フッタ(docs/08 §7 の決定どおり案内+直近のみ)。
- **error**: `.warnbox` に「{message} — ネットワーク接続を確認してください。」+ セカンダリボタン「再試行」(h:30px)。黙って空表示にしない(P3)。

## 7. 保存 API 呼び出しと保存直後の進捗

### 7.1 保存(状態 1 → 2)

```ts
// SaveForm.tsx の submit
const idempotencyKey = crypto.randomUUID();          // 再試行キューでも同一キーを使い回す(§11.3)
const { data, error, response } = await api.POST("/ingest/arxiv", {
  headers: { "Idempotency-Key": idempotencyKey },    // plans/03 §3.2(Redis 24h・同キー再送は初回応答を再生)
  body: {
    url: tabUrl,
    status,                                          // "planned" | "up_next" | "reading"
    tags,                                            // 確定チップのみ(提案の未採用分は送らない)
    collection_id: collectionId,                     // 未選択は null
    quick_note: memo.trim() === "" ? null : memo.trim(),  // → LibraryItem.one_line_note
  },
});
if (response.status === 202 && data) {
  await chrome.runtime.sendMessage({ type: "INGEST_STARTED", jobId: data.job_id }); // §9.4
  setView({ view: "saved", libraryItemId: data.library_item_id, jobId: data.job_id, title: bib.title, thumbnailUrl: null });
} else if (response.status === 409 && error?.code === "duplicate") {
  setView({ view: "existing", saved: error.existing, title: bib.title });  // plans/03 §3.2 の 409 本文
} else if (response.status === 401) {
  setView({ view: "login" });
} else if (error) {
  showFormError(error.title);                        // 422/429/5xx はフォーム上部に琥珀バナー(5xx もキューに入れない — 決定: arXiv URL 保存のキュー対象はネットワーク断のみ)
}
// ネットワーク断: openapi-fetch は fetch 例外(TypeError)を throw するため、
// 上記全体を try/catch で包み、catch 節で失敗キューへ入れる(決定):
//   await enqueueFailedSave({ id: idempotencyKey, kind: "arxiv", request: body, title: bib.title, failedAt: Date.now(), lastError: "network" }); // §11.3
```

### 7.2 進捗の取得 — **2,000ms ポーリングに決定**

- 決定: 保存直後の進捗は `GET /api/jobs/{job_id}`(session|ext スコープ。plans/03 §21.1)を **2,000ms 間隔**でポーリングする。SSE(`GET /api/jobs/{id}/events`)は使わない。理由: ① plans/01 §3.1 の決定(拡張はポーリングのみ)、② ポップアップは短命でありポーリングで十分、③ EventSource はクッキー必須+再接続管理が MV3 ポップアップのライフサイクルと相性が悪い。
- 実装: `useJobPolling(jobId)` フック。`setInterval(2000)` で取得し、`status ∈ {succeeded, failed}` で停止。ポップアップが閉じればタイマーは自動消滅する(以後はバックグラウンドの 15,000ms ポーリングが引き継ぐ。§9.3)。
- 失敗時(`status: "failed"`): 進捗行を `.warnbox` に差し替え「取り込みに失敗しました({job.error.detail})」+ ボタン「サイトで確認 ↗」(`/papers/{library_item_id}`)。P3: 段階+理由+導線の 3 点セット(plans/01 §8)。

### 7.3 stage → 表示行の写像(lib/pipeline.ts・純関数)

パイプライン 3 分節「書誌 / 構造化 / 翻訳」(docs/02 §5.1 の集約表示)。

```ts
export type PipelineRow = { label: string; state: "done" | "active" | "pending" }[];

const ORDER = ["queued","fetching","parsing","structuring","translating_abstract",
               "readable","translating_body","complete"] as const;

export function toPipelineRow(stage: string, progressPct: number): PipelineRow {
  const i = ORDER.indexOf(stage as any);
  const bibDone = i >= ORDER.indexOf("parsing");          // 取得完了=書誌確定
  const structDone = i >= ORDER.indexOf("translating_abstract");
  return [
    { label: bibDone ? "✓ 書誌" : "書誌…",            state: bibDone ? "done" : "active" },
    { label: structDone ? "✓ 構造化" : bibDone ? "構造化中…" : "構造化",
      state: structDone ? "done" : bibDone ? "active" : "pending" },
    { label: stage === "complete" ? "✓ 翻訳完了"
           : structDone ? `翻訳中 ${progressPct}%` : "翻訳",
      state: stage === "complete" ? "done" : structDone ? "active" : "pending" },
  ];
}
```

- 表示色: `done` = #659471 / `active` = var(--pr-a) + font-weight:600(翻訳分節)/ `pending` = #5B6067(9.5px)。`waiting_quota` は翻訳分節を「翻訳待機中(クォータ)」表示にする(決定。plans/03 §21.2)。

## 8. 直近の取り込み 3 件(RecentIngests.tsx)

### 8.1 データフローと表示

1. マウント時に storage.local `cache:recentIngests` を読み **0ms で描画**(前回値)。
2. 並行して `GET /api/ingest/recent?limit=3`(plans/03 §3.4)を取得 → 差し替え+キャッシュ上書き。
3. items のいずれかが処理中(`pipeline.stage ∉ {complete, failed}`)である間、**2,000ms 間隔**で同エンドポイントを再取得(ポップアップ表示中のみ)。
4. 行クリック → `chrome.tabs.create({ url: `${APP_BASE_URL}${item.viewer_url}` })`(docs/08 §3.1 の決定: 「サイトで開く ↗」と同じ挙動)。

行レイアウト(`.recent-row`): 左アイコン 11px 固定幅 — 処理中 = `.spin` / 完了 = 「✓」#659471 10px / 失敗 = 「⚠」#C49432 10px(決定: 失敗色は琥珀。デザイン未掲載)。中央タイトル ellipsis。右メタ 9.5px #9A9EA4 — 処理中 = 「翻訳中 {progress_pct}%」/ 完了 = `completed_at` の相対表記 / 失敗 = 「失敗 · 開く →」(docs/08 §6: 失敗表示+サイト導線)。

### 8.2 時刻の相対表記(lib/format.ts・確定規則)

| 条件(ローカルタイム) | 表記 |
|---|---|
| 当日 | `今日 8:02`(H:mm) |
| 前日 | `昨日 21:52` |
| 同年内 | `7/01`(M/DD) |
| それ以前 | `2025/12/01`(YYYY/MM/DD) |

状態 3 の「前回: … · 昨日 21:52」も同関数を使う(一昨日以前かつ同年は `7/01 21:52` ではなく `7/01` のみ — 決定: 3a の完了時刻表記と統一)。

## 9. ツールバーバッジ(background.ts)

### 9.1 バッジ状態機械

```
idle ──(INGEST_STARTED / PILL_SAVED / recent に処理中あり)──→ processing
processing ──(アクティブジョブ 0 件になった)──→ check(完了チェック表示)
check ──(表示 10,000ms 経過)──→ idle
任意状態: unread_notifications > 0 ⇔ 琥珀ドット付きアイコン変種を使用
```

- **琥珀ドット(#C49432)の表示条件(確定)**: `処理中ジョブあり || unread_notifications > 0`(docs/08 §8 受け入れ基準「処理中/通知ありの間」)。スピナーフレームには全てドットを焼き込む(処理中は常にドット条件成立のため)。idle/check は `unread > 0` のときのみ `-dot` 変種。
- アイコンは `chrome.action.setIcon({ path })` の **PNG 差し替え**で表現する(タスク指示どおり)。`setBadgeText` の文字バッジは使わない(決定: デザインはドット表現であり数字バッジは存在しない)。

### 9.2 アイコンアセット(public/icons/)

- ベース: 角丸正方形(radius 比 4/17)背景 #3E5C76(アクセント既定固定 — 決定: バッジはユーザーアクセントに追随しない。再ビルド不要性を優先)+ 白「訳」。
- ドット: 右上に直径 7/24 比の円 #C49432、縁 1.5px 白抜き(3a §2.2 の値を各サイズへ比例縮小)。
- スピナー: ベースを 20% 減光し、中央に円弧(track rgba(62,92,118,0.32) / head #3E5C76)を 45° 刻みで回転させた 8 フレーム。`spinner-{n}` は 16/32/48 のみ(128 は store 用静止アイコンのみで十分)。

### 9.3 ポーリングと MV3 ライフサイクル(確定実装)

```ts
// src/entrypoints/background.ts(要点)
export default defineBackground(() => {
  chrome.runtime.onInstalled.addListener(() => ensureAlarms());
  chrome.runtime.onStartup.addListener(() => ensureAlarms());

  function ensureAlarms() {
    chrome.alarms.create("yk-poll", { periodInMinutes: 0.5 });   // 30秒 = chrome.alarms の最小周期
    chrome.alarms.create("yk-unread", { periodInMinutes: 5 });
  }

  chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name === "yk-unread") return refreshUnreadAndIcon();
    if (alarm.name === "yk-check-clear") return clearCheckIfExpired();  // check → idle(下記の決定)
    if (alarm.name !== "yk-poll") return;
    const active = await pollRecentAndUpdateIcon();      // 1回目(0s)
    if (!active) return;                                 // 処理中なしなら即終了(SW はアイドル停止)
    // 処理中あり: このハンドラの Promise を ~29 秒保留にして SW を生存させ、
    // 250ms ごとのスピナーフレーム更新と 15 秒後の 2 回目ポーリングを行う
    await animateSpinnerFor(29_000, { pollAgainAfterMs: 15_000 }); // 実効ポーリング間隔 15,000ms
  });
  // メッセージ: ポップアップ/ピルからの即時反映
  chrome.runtime.onMessage.addListener((msg: RuntimeMessage, _s, sendResponse) => {
    if (msg.type === "INGEST_STARTED") { setBadgeMode("processing"); chrome.alarms.create("yk-poll", { when: Date.now(), periodInMinutes: 0.5 }); }
    if (msg.type === "PILL_CHECK" || msg.type === "PILL_SAVE") { handlePillMessage(msg).then(sendResponse); return true; }
  });
  chrome.cookies.onChanged.addListener(({ cookie }) => {
    // ドメイン判定は APP_BASE_URL のホスト名で行う(本番 "yakudoku.app" / 開発 "localhost")
    if (cookie.name === "yk_session" && cookie.domain.includes(new URL(APP_BASE_URL).hostname)) invalidateMeAndRefreshIcon();
  });
});
```

- `pollRecentAndUpdateIcon()`: `GET /api/ingest/recent?limit=3` → キャッシュ更新 → アクティブ有無を判定 → `badge:state` 遷移(processing→check 遷移時に `checkSince` を記録し、one-shot アラーム `yk-check-clear` を `{ when: Date.now() + 10_000 }` で作成。発火時に `checkSince` から 10,000ms 以上経過していれば idle へ — 決定: 次回 `yk-poll` を待たず正確に 10 秒でチェックを消す)→ `chrome.action.setIcon`。未ログイン(401)時は idle アイコン+ドットなしに戻し、ポーリングを止める(`yk-poll` はアラーム自体は残すがハンドラ先頭の クッキー存在チェックで即 return — 決定: 未ログイン中の無駄な HTTP を出さない)。
- `refreshUnreadAndIcon()`: `GET /api/auth/me` → `unread_notifications` を `cache:me` に保存しドット再評価(通知本体はサイト側 4a。docs/08 §3.2)。
- バックグラウンドの実効ポーリング間隔は **15,000ms**(plans/01 §3.1 の決定と一致)。スピナーは 250ms/フレーム(8 フレームで 2 秒/回転)。

### 9.4 ポップアップ・ピルとの連携

- ポップアップの保存成功時 `INGEST_STARTED` メッセージ → バッジ即時 processing 化(次アラームを待たない)。
- 完了チェックへの遷移は background のポーリングが検知する(ポップアップが閉じていても機能する)。

## 10. arXiv ページ内「訳 保存」ピル(オプトイン)

### 10.1 登録と解除(既定オフ)

- manifest には **静的 content_scripts を宣言しない**。SettingsView のトグル ON 時(ユーザージェスチャー内)に:

```ts
const granted = await chrome.permissions.request({ origins: ["https://arxiv.org/*"] });
if (!granted) { setToggle(false); return; }
await chrome.scripting.registerContentScripts([{
  id: "arxiv-pill",
  matches: ["https://arxiv.org/abs/*"],       // abs ページ限定(docs/08 §5)
  js: ["content-scripts/arxiv-pill.js"],      // WXT が arxiv-pill.content.ts からビルド
  runAt: "document_idle",
  persistAcrossSessions: true,
}]);
await storage.set("settings:arxivPillEnabled", true);
```

- OFF 時: `chrome.scripting.unregisterContentScripts({ ids: ["arxiv-pill"] })` + `chrome.permissions.remove({ origins: ["https://arxiv.org/*"] })`。
- WXT 側エントリは `defineContentScript({ matches: ["https://arxiv.org/abs/*"], registration: "runtime", main })`。

### 10.2 SettingsView(⚙ の中身・確定)

ポップアップ内ビュー(独立オプションページは持たない — 決定: 権限要求のユーザージェスチャーをポップアップ内で完結させる)。項目は上から:

1. トグル「arXiv ページに保存ボタンを表示」(既定オフ)+ 説明 9.5px #9A9EA4「arxiv.org の論文ページに『訳 保存』ボタンを追加します。有効化時に arxiv.org へのアクセス権限を求めます。」(Toggle スタイルは plans/08 §5.8 の値を 372px 幅に流用)。
2. アクセント 4 色スウォッチ(#3E5C76 / #4A6B57 / #6E5A7E / #7A5C48。選択で `ui:accent` 保存・即時反映)。
3. リンク行「訳読の設定を開く ↗」→ `${APP_BASE_URL}/settings`(4f の「ブラウザ拡張」カテゴリはサイト側の案内面。拡張ローカル設定はここが正 — 決定)。
4. フッタ小文字 9.5px #9A9EA4: バージョン表記「訳読拡張 v{manifest.version}」。
- 戻る: ヘッダ左に「←」(⚙ の代わり)。`settings.back` のビューへ復帰。

### 10.3 ピルの実装(arxiv-pill.content.ts)

- 挿入位置: `h1.title`(arXiv abs のタイトル要素)の末尾に inline-flex で追加。**Shadow DOM**(WXT `createShadowRootUi`)でホスト CSS から隔離し、`z-index: 2147483000`(plans/08 §7.3 の確定値)。
- スタイル(3a §2.3 逐語): `display:inline-flex; align-items:center; gap:5px; height:24px; padding:0 10px; border:1px solid var(--pr-am); border-radius:999px; background:var(--pr-as); color:var(--pr-a); font-size:10.5px; font-weight:700; margin-top:3px; margin-left:10px; cursor:pointer`。内部ミニロゴ 13×13 radius:3px 背景 var(--pr-a) 白「訳」8px + テキスト「保存」。アクセント変数は `ui:accent` から解決した実値を Shadow 内 `:host` にインライン展開(既定 #3E5C76 系)。
- ライフサイクル(すべて background 経由のメッセージ。content script から直接 API を呼ばない — 決定: host_permissions による same-site クッキー送信は拡張コンテキスト発が条件のため):

| メッセージ | 処理 | ピル表示 |
|---|---|---|
| 初期化: `{ type: "PILL_CHECK", url }` | background が `GET /api/ingest/check` | `saved` 非 null → 最初から「✓ 保存済み」/ 未ログイン → ピル非表示(決定)/ 判定失敗(ネットワーク断・5xx)→ ピル非表示・再試行しない(決定: ページ再読み込みで再判定) |
| クリック: `{ type: "PILL_SAVE", url }` | background が `POST /api/ingest/arxiv`(既定値: `status:"planned"`・タグなし・コレクションなし・メモなし = 状態 1 の既定値保存。docs/08 §5)+ `INGEST_STARTED` 相当のバッジ処理 | 送信中: ラベル「保存中…」+ opacity:0.7 / 202 または 409 duplicate → 「✓ 保存済み」/ 401 → `${APP_BASE_URL}/login?from=extension` を新規タブで開きピルは元に戻す / 失敗 → 「保存できませんでした」を 3,000ms 表示後に元へ戻す(キューには入れない — 決定: ピルは 1 クリック UI であり再試行はポップアップに誘導) |

- 「✓ 保存済み」の確定スタイル(決定・デザイン注記はテキストのみ): `border:1px solid rgba(101,148,113,0.4); background:rgba(101,148,113,0.16); color:#4C7458; cursor:default`、ミニロゴの代わりに「✓」。

## 11. タブ内 PDF 直接送信

### 11.1 PDF 判定と書誌ローカル推定(lib/pdf-detect.ts)

- 一次判定はサーバー(`/api/ingest/check` の `kind:"pdf"`。URL パス末尾 `.pdf`(クエリ除去後・大文字小文字無視)による分類)。クライアントは表示用の補助のみ:

```ts
export function guessPdfTitle(tab: chrome.tabs.Tab): string | null {
  // Chrome PDF ビューアは tab.title に PDF メタデータの Title(なければファイル名)を入れる
  const t = (tab.title ?? "").trim();
  if (t === "" || /\.pdf$/i.test(t)) {
    const m = /\/([^\/?#]+)\.pdf(?:[?#]|$)/i.exec(tab.url ?? "");
    return m ? decodeURIComponent(m[1]).replace(/[_-]+/g, " ") : null;  // ファイル名フォールバック
  }
  return t;
}
```

### 11.2 送信フロー(明示クリック時のみ)

```ts
async function sendTabPdf(tabUrl: string, titleGuess: string | null) {
  // activeTab: ポップアップを開いた時点でタブのオリジンへの一時ホスト権限が付与済み
  const idempotencyKey = crypto.randomUUID();  // クリックごとに生成。§11.3 の失敗キュー再試行では同一キーを使い回す(§7.1 と同規則)
  const res = await fetch(tabUrl, { credentials: "include", signal: AbortSignal.timeout(120_000) });   // 学内ネットワーク等でもタブと同条件で取得
  const blob = await res.blob();
  if (blob.size > 52_428_800) throw new UploadError("50MB を超える PDF は送信できません");  // plans/03 §3.3 の 413 と同値
  const head = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
  if (String.fromCharCode(...head) !== "%PDF-") throw new UploadError("PDF として読み取れませんでした");

  const form = new FormData();
  form.append("file", blob, "paper.pdf");
  form.append("meta", JSON.stringify({
    source_url: tabUrl, title_guess: titleGuess,
    status: "planned", tags: [], collection_id: null, quick_note: null,   // 状態4はフォーム項目を持たない(3a準拠)
  }));
  return api.POST("/ingest/pdf", {
    headers: { "Idempotency-Key": idempotencyKey },
    body: form,                                        // multipart/form-data
  });
}
```

- タイムアウト: `AbortSignal.timeout(120_000)` を **タブ内 PDF の `fetch` と `POST /ingest/pdf` の両方に各 120,000ms** 適用する(50MB×低速回線を考慮 — 決定)。タイムアウト発火はネットワーク断と同扱いで §11.3 のキューへ。送信中はボタンを「送信中…」+スピナー+disabled。
- 409 `duplicate`(同一 SHA-256)→ 状態 3 表示に切り替え。413/415/422 → `.warnbox` にサーバーの `title` を表示。ネットワーク断・5xx → §11.3 のキューへ。

### 11.3 失敗キュー(lib/queue.ts・ブラウザ再起動をまたいで保持)

| 種別 | 置き場所 | レコード |
|---|---|---|
| arXiv URL 保存の失敗 | storage.local `queue:failedSaves` | `{ id: string(=idempotencyKey); kind: "arxiv"; request: IngestArxivRequest; title: string; failedAt: number; lastError: string }` |
| PDF 送信の失敗 | **IndexedDB**(DB `yakudoku-ext` v1 / objectStore `failed_uploads`, keyPath `id`) | `{ id: string(=idempotencyKey); kind: "pdf"; meta: IngestPdfMeta; blob: Blob; titleGuess: string \| null; failedAt: number; lastError: string }` |

- 決定: PDF バイト列は storage.local(既定上限 10MB)に入らないため IndexedDB に置く。`unlimitedStorage` 権限は追加しない(拡張オリジンの IndexedDB 既定クォータで 50MB は保持可能)。
- 決定: **自動再送はしない**(docs/08 §6 は「再試行ボタンを表示」。PDF の「自動送信はしない」原則とも整合)。再試行は §11.4 の UI からのみ。再試行時は保存時と同じ Idempotency-Key を使い、二重取り込みを防ぐ(plans/03 §3.2)。
- キュー上限(決定): 各種別 10 件。超過時は最古を破棄し `.warnbox` で明示(黙って捨てない)。成功・手動破棄で削除。

### 11.4 再試行 UI(FailedQueueBanner.tsx)

- キューが 1 件以上あるとき、**全ビュー共通で本文の最上部**に琥珀バナーを表示: `.warnbox` 流用、文言「送信できなかった保存が {n} 件あります」。
- バナー展開(クリック)で各行: タイトル(ellipsis)+ 失敗時刻(§8.2 表記)+「再試行」(10.5px, var(--pr-a), 下線なしボタン)+「破棄」(10.5px, #9A9EA4)。
- 再試行成功 → 行を消し、`INGEST_STARTED` で状態 2 相当のバッジ処理。再失敗 → `lastError` を更新し行内に 9.5px #8A6A24 で表示。

## 12. Chrome / Edge ストア対応

### 12.1 配布物

- `pnpm --filter @yakudoku/extension zip`(Chrome Web Store 用)/ `zip:edge`(Microsoft Edge Add-ons 用。同一コード・同一 manifest。plans/00 §8 の CI ジョブ `extension` が両 zip を artifact 化)。
- 拡張 ID はストアごとに別になるため、`EXTENSION_ALLOWED_ORIGINS`(§15-2)には両ストアの ID を登録する(Edge も Chromium のため Origin スキームは `chrome-extension://`)。

### 12.2 ストア掲載文(確定)

- **単一目的(single purpose)**: 「閲覧中の学術論文(arXiv ページまたは PDF)を、ユーザーの明示操作でユーザー自身の訳読ライブラリに保存する。」
- **権限説明文**(審査フォームへ逐語で提出):

| 権限 | 説明文 |
|---|---|
| `activeTab` | 保存ボタンを押したタブの URL とタイトルを読み取るため、および arXiv 以外の PDF をユーザーがボタンで明示的に送信する場合にそのタブの PDF を読み取るために使用します。タブの自動スキャンは行いません。 |
| `storage` | 直近の取り込み履歴のキャッシュ、送信に失敗した保存の再試行キュー、拡張の設定(ページ内ボタンのオン/オフ等)を保存するために使用します。閲覧履歴は収集しません。 |
| `cookies` | yakudoku.app のログイン状態(セッションクッキーの有無)を確認するためだけに使用します。対象は yakudoku.app ドメインのみで、他サイトの Cookie にはアクセスできません。 |
| `alarms` | 保存した論文の処理進捗をツールバーアイコンに反映するための定期確認(15〜30 秒間隔、処理中のみ)に使用します。 |
| `scripting` | ユーザーが設定で有効にした場合のみ、arxiv.org の論文ページに「訳 保存」ボタンを追加するために使用します。既定では無効です。 |
| `host: yakudoku.app` | 本サービス自身の API(論文の保存・進捗取得)を呼び出すために使用します。 |
| `optional host: arxiv.org` | 設定で有効化した場合のみ、arXiv 論文ページ内に保存ボタンを表示するために使用します。 |

- **データ利用の開示**(Chrome「プライバシーへの取り組み」/ Edge 同等欄): 収集するユーザーデータ = 「ユーザーが保存操作をしたページの URL(および PDF 送信を明示選択した場合のみその PDF ファイル)」。用途 = アプリ機能のみ。第三者提供・広告利用・売却 = なし。リモートコード実行 = なし。
- スクリーンショット素材: 状態 1〜4 のポップアップ(1280×800 フレーム内に 372px ポップアップを配置)。

### 12.3 バージョニング

- `manifest.version` は `MAJOR.MINOR.PATCH`。API 破壊変更を伴うリリースはサーバー側が旧パス据え置きで移行する方針(plans/03 §1.1)のため、拡張の強制アップデート機構は持たない(決定)。

## 13. テスト(Vitest)

| 対象 | テスト |
|---|---|
| `lib/arxiv.ts` | 新形式/旧形式/pdf/html/バージョン付き/クエリ付き URL のパース、非 arXiv URL の否定(プロパティテスト: `arxiv.org` を含むだけの偽 URL を弾く) |
| `lib/pipeline.ts` | 全 stage × progress_pct の写像スナップショット(「✓ 書誌」「翻訳中 12%」「✓ 翻訳完了」「翻訳待機中(クォータ)」) |
| `lib/format.ts` | 今日/昨日/同年/前年の境界(23:59→0:00、12/31→1/1) |
| `lib/pdf-detect.ts` | tab.title あり/ファイル名フォールバック/URL エンコード復元 |
| `lib/queue.ts` | fake-indexeddb で enqueue→retry 成功削除→上限 10 件の最古破棄 |
| SaveForm | Testing Library: Enter 保存(IME composing 中は発火しない)、タグ入力中 Enter はタグ確定、提案クリックでチップ化、409 応答で existing 遷移 |
| バッジ状態機械 | idle→processing→check(10s)→idle、unread によるドット変種選択(`badge.ts` を chrome API モックで) |

## 14. 受け入れ基準

docs/08 §8 の全項目に加えて:

- [ ] manifest が §3.2 と一致し、`tabs`・`<all_urls>`・`webRequest` を含まない
- [ ] 未ログイン時、ネットワークアクセスなし(クッキー不存在)で LoginPrompt が出て、ログイン後にポップアップを開き直すと状態判定が通る
- [ ] 保存 → 状態 2 の進捗が 2,000ms 間隔で更新され、ポップアップを閉じてもバッジがスピナー(実効 15,000ms 更新)→完了チェック(10 秒)→通常に遷移する
- [ ] `unread_notifications > 0` の間、idle/チェックアイコンに琥珀ドット変種が使われる
- [ ] 409 duplicate 応答で保存フォームから状態 3 に遷移し、重複 LibraryItem が作られない
- [ ] 機内モードで保存 → 失敗キューに入り、ブラウザ再起動後もバナーに残り、「再試行」が同一 Idempotency-Key で成功する
- [ ] 50MB 超 PDF・非 PDF(%PDF- マジック不一致)がクライアント側で 413/415 相当のメッセージで拒否される
- [ ] ピルのトグル ON 時のみ arxiv.org 権限が要求され、OFF で権限ごと解除される。保存後・保存済みページで「✓ 保存済み」表示になる
- [ ] popup.css の色・寸法値が extract/3a.md の逐語値と一致する(スナップショット照合)

## 15. ⚠ 基盤への追加要求(不整合の解消依頼)

1. **plans/00-tech-stack §2(拡張の権限決定)の更新**: 「permissions は `activeTab` + `storage` の 2 つ、host_permissions は `https://arxiv.org/*` のみ」は、plans/01 §6.4 が前提とするクッキー認証(`host_permissions: https://yakudoku.app/*` が必須)と両立しない。本書 §3.3 の権限表(`activeTab, storage, cookies, alarms, scripting` + host `yakudoku.app` + optional host `arxiv.org`)への更新を要求する。
2. **plans/03-api §1.3(Origin 検証)への追記**: 拡張発の非 GET リクエストは `Origin: chrome-extension://{EXTENSION_ID}` を送るため、許可 Origin に拡張オリジンを追加する必要がある。要求: 環境変数 `EXTENSION_ALLOWED_ORIGINS`(カンマ区切り。Chrome/Edge 各ストア配布 ID)を plans/00 §5.2 の .env.example に追加し、`APP_ENV=development` では `chrome-extension://` スキームを一律許可する。
3. **セッションクッキー名の不一致**: plans/03 §1.3 = `yk_session` / plans/00 §1.6 = `ykd_session`。本書は plans/03 の **`yk_session`** を採用した。plans/00 の修正を要求する(§4.2 の `chrome.cookies.get` が名前に依存)。
4. **plans/01 のパス表記・CSRF 記述の更新**: §2.4・§3.1・§6.4 の `/api/v1/...`(例 `GET /api/v1/ingests/recent`, `GET /api/v1/me`)と「X-CSRF-Token を /api/v1/me ヘッダで取得」は、plans/03 §1.1(バージョニングなし `/api/*`)・§1.3(Origin 検証・CSRF トークンなし)・§3.4(`GET /api/ingest/recent`)と矛盾する。本書は plans/03 を正とした。
5. **拡張トークンスコープの将来課題**(v1 では影響なし): クッキーが使えない環境でトークン運用に切り替える場合、状態 1 のコレクション選択に使う `GET /api/collections` が plans/03 §1.2.1 のスコープ外。トークン導入時にスコープへの追加を要求する。
