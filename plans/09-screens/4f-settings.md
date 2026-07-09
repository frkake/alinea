# 画面 4f: 設定(翻訳既定・計測と提案・エクスポート)

> 対象読者と前提: 本書は「Alinea」の apps/web(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4)実装者向けに、確定デザイン画面 4f(設定)を 1px 単位で再現するための完全仕様である。ピクセル値・UI 文言は抽出ファイル extract/4f.md を正とし、機能仕様は docs/03(翻訳)・docs/04 §6(計測)・docs/06 §2(提案)・docs/08 §5(拡張)・docs/09 §3〜§4・§7.1(LLM/BYOK/カテゴリ構成)、API は plans/03 §17〜§18、共通コンポーネント・トークンは plans/08 の識別子をそのまま使う。本書に無い選択肢は存在しない(すべて確定済み)。

## 1. 概要とルート

- **ルート**: `/settings`(App Router: `apps/web/src/app/(app)/settings/page.tsx`)。
  - **カテゴリ指定**: URL クエリ `category={id}`。id は 8 カテゴリ固定: `account` / `display` / `translation` / `reading` / `chat` / `notifications` / `export` / `extension`(1d 計画書 §1 と同一の語彙。順序も左ナビの表示順と同一)。
  - **決定: `category` 省略時・不正値時の既定は `account`**。理由: 1d 計画書で「アバター → `/settings`(アカウントカテゴリ=既定表示)」と確定済み。不正値はエラーにせず `account` へ正規化する(URL は書き換えない)。
  - カテゴリ切替は `router.replace`(`useSearchParams` + `useRouter`)で `?category=` のみ書き換える(履歴を汚さない)。
- **認証**: 必須(plans/03 の `session` 区分)。未ログインは `(app)` グループの layout が `/login?next=/settings` へ `redirect()`。
- **画面の役割**:
  1. ユーザー設定(`users.settings` JSONB、plans/02 §users)の閲覧・変更。翻訳既定(自然訳/直訳、直訳はオンデマンド生成)・読書計測の明示とオフ(docs/04 §6)・ステータス提案の3段階(docs/06 §2)。
  2. データエクスポート(P5: ロックインしない)。論文単位 Markdown / BibTeX・CSV / JSON 一括。
  3. アカウント(プロフィール・BYOK API キー・月次クォータ)、表示、チャット、通知、ブラウザ拡張の各設定(docs/09 §7.1 の 8 カテゴリ対応表)。
- **決定: 左ナビはカテゴリ切替方式(1 カテゴリ = 1 コンテンツ表示)とする**。extract/4f.md §5 は「ナビ選択はセクションへのジャンプ/ハイライトと解釈可能。実装時は要確認」としているが、本書で確定する: ナビ項目クリックで `?category=` を書き換え、コンテンツペインは選択カテゴリのセクションのみを表示する。理由: (1) 1d 計画書が既に `/settings?category=export` 等のカテゴリ単位リンクを確定している。(2) 8 カテゴリを 1 カラムに縦積みすると「アカウント→表示→翻訳→読書→チャット→通知→エクスポート→拡張」の順序上、デザインフレームの「翻訳/読書の計測と提案/エクスポート」の隣接が成立しない。(3) デザイナー注記タイトル「設定 — 翻訳・計測と提案・エクスポート」は 3 カテゴリを 1 フレームに合成した省略描画を示す。ピクセル一致検証は §6.1 の合成ストーリーで担保する。
- **レンダリング方式(決定)**: `page.tsx` は Client Component(`"use client"`)+ TanStack Query による CSR。サーバープリフェッチは行わない(v1)。理由: 1e 計画書 §1 と同一(セッション依存データ、スケルトンで初期体感を担保)。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 用途 | 取得/実行タイミング |
|---|---|---|---|
| 1 | `GET /api/settings`(§17.1) | 全設定オブジェクト+`available_models`。全カテゴリの初期値 | ページマウント時に 1 回 |
| 2 | `PATCH /api/settings`(§17.2) | 設定の部分更新(deep merge) | 各コントロール変更の即時(操作ごとに 1 リクエスト。デバウンスなし — すべて離散操作のため) |
| 3 | `GET /api/auth/me`(§2.6) | トップバーのアバターイニシャル+アカウントカテゴリのプロフィール表示 | ページマウント時 |
| 4 | `POST /api/auth/logout`(§2.5) | アカウント「ログアウト」 | ボタン押下時。成功で `/login` へ `router.push` |
| 5 | `DELETE /api/auth/account`(§2.8) | アカウント削除 | 確認モーダルの「削除する」押下時 |
| 6 | `GET /api/settings/api-keys`(§17.3) | BYOK キー一覧(マスク表示) | `category=account` 表示時 |
| 7 | `PUT /api/settings/api-keys/{provider}`(§17.3) | BYOK キー登録・上書き | キー入力ポップオーバーの「保存」押下時 |
| 8 | `DELETE /api/settings/api-keys/{provider}`(§17.3) | BYOK キー削除 | 「削除」押下(確認なし。再入力で戻せるため) |
| 9 | `GET /api/settings/quota`(§17.4) | 月次クォータ表示 | `category=account` 表示時 |
| 10 | `GET /api/library-items`(§5.1) | 論文単位 Markdown の対象選択モーダル(`q` + `limit=20`) | モーダル内入力の 200ms デバウンス後 |
| 11 | `GET /api/library-items/{id}/export/markdown`(§18) | 論文単位 Markdown ダウンロード | モーダルで論文選択時(アンカー `href` 直接 GET) |
| 12 | `GET /api/export/bibtex`(§18) | BibTeX ダウンロード(フィルタ無指定=全件) | 形式選択ポップオーバーの「BibTeX (.bib)」押下時 |
| 13 | `GET /api/export/csv`(§18) | CSV ダウンロード(全件) | 同「CSV (.csv)」押下時 |
| 14 | `POST /api/export/full`(§18) | JSON 一括エクスポートのジョブ起動 | 「エクスポート ⤓」押下時。202 → job_id |
| 15 | `GET /api/export/full/{job_id}`(§18) | ジョブ進捗+`download_url`(署名 URL、有効 24 時間) | 2,000ms 間隔ポーリング(下記 2.3) |

- UI キーと API キーの対訳(plans/03 §17.1 の注記どおり**変換は web 層**):

| UI コントロール(逐語) | UI 状態 | API キー・値 |
|---|---|---|
| 「付録(Appendix)を自動翻訳しない」 | ON | `translation.auto_translate_appendix: false` |
| 「表のセル内テキストを翻訳しない」 | ON | `translation.translate_table_cells: false` |
| 「30 ページ超の論文はセクション選択を提案」 | ON | `translation.suggest_section_selection_over_30_pages: true` |
| 「● 自然訳(既定)」/「○ 直訳」 | 選択 | `translation.default_style: "natural" \| "literal"` |
| 「読書時間を計測する」 | ON | `reading.track_reading_time: true` |
| 「自動適用」/「提案する(既定)」/「提案しない」 | 選択 | `reading.status_transition: "auto" \| "suggest" \| "off"` |

### 2.2 TanStack Query キー設計

キーはすべて `apps/web/src/features/settings/queries.ts` に定数化する。

```ts
// apps/web/src/features/settings/queries.ts
export const settingsKeys = {
  root: ['settings'] as const,
  detail: ['settings', 'detail'] as const,        // GET /api/settings
  apiKeys: ['settings', 'api-keys'] as const,     // GET /api/settings/api-keys
  quota: ['settings', 'quota'] as const,          // GET /api/settings/quota
  exportJob: (jobId: string) => ['settings', 'export-job', jobId] as const,
  paperPicker: (q: string) => ['settings', 'paper-picker', q] as const,
} as const;
export const shellKeys = { me: ['auth', 'me'] as const };   // 1e 計画書と同一キー
```

- `staleTime`(決定): detail = **60,000ms** / apiKeys = **60,000ms** / quota = **30,000ms** / me = **60,000ms** / paperPicker = **60,000ms**(`placeholderData: keepPreviousData`)。
- **PATCH は楽観更新**: `onMutate` で `settingsKeys.detail` を `cancelQueries` → スナップショット → deep merge した新値を `setQueryData`。`onError` でスナップショット復元+Toast(§5.6)。`onSettled` で `settingsKeys.detail` を `invalidateQueries`。
- 無効化規則: `PUT/DELETE /api/settings/api-keys/{provider}` 成功 → `settingsKeys.apiKeys` と `settingsKeys.quota`(`byok_active` が変わるため)。`DELETE /api/auth/account` 成功 → `queryClient.clear()` して `/login` へ。

### 2.3 リアルタイム更新(決定)

**SSE は使わない。ポーリングは JSON 一括エクスポートのジョブのみ。** `POST /api/export/full` の 202 受領後、`useQuery(settingsKeys.exportJob(jobId))` を `refetchInterval: 2000`(2,000ms)で回し、`download_url !== null` になった時点でポーリング停止+アンカー生成クリックでダウンロード開始。`job.status === "failed"` でもポーリング停止+Toast。**決定: 進行中 `jobId` は `SettingsPage`(page.tsx)の state で保持する** — カテゴリ切替で `ExportSettings` がアンマウントされてもポーリングとダウンロード発火を継続し、エクスポートカテゴリへ戻ったとき「準備中…」表示が復元される(ページ離脱(アンマウント)で state は破棄し、再訪時は通常表示に戻る。ジョブ自体はサーバー側で完走する)。その他の設定データは自ユーザー操作でのみ変化するため、TanStack Query 既定の `refetchOnWindowFocus: true` のみとする。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

`共通` = plans/08 §5、`固有` = 本画面(`apps/web/src/features/settings/` 配下)。

```
(app)/settings/page.tsx                       固有(SettingsPage)
└─ SettingsShell                              固有
   ├─ SettingsTopBar                          固有(ロゴ+「設定」+アバター。検索・通知なし=4f 実測)
   ├─ SidebarNav                              共通(§5.14。sections=[] の縮退版 — §5.14 の使用規定どおり)
   └─ SettingsContent                         固有(カテゴリ出し分け+中央 720px カラム)
      ├─ [category=translation] TranslationSettings        固有
      │  ├─ SettingsSection(見出し「翻訳」)                 固有
      │  │  └─ Card                                        共通(§5.9, padding='none')
      │  │     ├─ TranslationStyleRow(ラジオカード2枚)      固有
      │  │     ├─ SettingToggleRow ×3                       固有(内部に Toggle 共通 §5.8)
      │  ├─ SettingsSection(見出し「翻訳モデル」)★未描画    固有
      │  │  └─ Card > ModelRoutingRow ×2(translation / retranslation)
      ├─ [category=reading] ReadingSettings                固有
      │  └─ SettingsSection(見出し「読書の計測と提案」)
      │     └─ Card
      │        ├─ SettingToggleRow(読書時間を計測する)
      │        └─ StatusTransitionRow(SegmentedControl 共通 §5.1, size='lg')
      ├─ [category=export] ExportSettings                  固有
      │  └─ SettingsSection(見出し「エクスポート」+補足)
      │     └─ Card(padding='md') > ExportFormatCard ×3    固有
      │        ├─ ExportPaperPickerModal(Modal 共通 §5.11 + 画面固有検索 input — §4.6 #1)
      │        └─ ExportFormatPopover(Popover 共通 §5.10)
      ├─ [category=account] AccountSettings                固有 ★未描画
      │  ├─ SettingsSection(「プロフィール」)> Card > ProfileRow ×3 + ログアウト/削除行
      │  ├─ SettingsSection(「API キー(BYOK)」)> Card > ApiKeyRow ×5(+ ApiKeyEditPopover)
      │  ├─ SettingsSection(「今月の利用量」)> Card > QuotaRow ×3(ProgressBar 共通 §5.12)
      │  └─ SettingsSection(「モデルルーティング(詳細)」)> Card > ModelRoutingRow ×5 + SettingToggleRow ×1
      ├─ [category=display] DisplaySettings                固有 ★未描画
      ├─ [category=chat] ChatSettings                      固有 ★未描画
      ├─ [category=notifications] NotificationSettings     固有 ★未描画
      └─ [category=extension] ExtensionSettings            固有 ★未描画
```

★未描画 = デザインに描かれていないカテゴリ(§4.7 で確定)。描画済み 3 カテゴリのピクセル仕様が §4 の主対象。

### 3.2 画面固有コンポーネントの props 型

```ts
// apps/web/src/features/settings/types.ts
import type { components } from '@alinea/api-client';
export type UserSettings = components['schemas']['UserSettings'];        // plans/03 §17.1 の JSON 全体
export type SettingsCategory =
  | 'account' | 'display' | 'translation' | 'reading'
  | 'chat' | 'notifications' | 'export' | 'extension';

// SettingsShell.tsx
interface SettingsShellProps { category: SettingsCategory; children: React.ReactNode }

// SettingsSection.tsx — 見出し+子(カード)を縦 flex gap:12px で包む
interface SettingsSectionProps {
  title: string;                       // 例 「翻訳」 font 14px/700
  titleNote?: string;                  // 例 「データはいつでも持ち出せます(P5)」 font 10.5px #9A9EA4 margin-left:6px
  children: React.ReactNode;
}

// SettingToggleRow.tsx — トグル行(padding 12px 18px)
interface SettingToggleRowProps {
  title: string;                       // font 12px/600
  description: string;                 // font 10.5px var(--pr-text-muted)
  checked: boolean;
  onChange: (next: boolean) => void;   // 内部で Toggle(共通 §5.8)を使用
  divider?: boolean;                   // true で border-bottom 1px var(--pr-border-hair)(=#F0EDE4)
  disabled?: boolean;
}

// TranslationStyleRow.tsx — ラジオカード行(padding 14px 18px)
interface TranslationStyleRowProps {
  value: 'natural' | 'literal';
  onChange: (next: 'natural' | 'literal') => void;
}

// StatusTransitionRow.tsx — セグメント行(padding 14px 18px)
interface StatusTransitionRowProps {
  value: 'auto' | 'suggest' | 'off';
  onChange: (next: 'auto' | 'suggest' | 'off') => void;
}

// ExportFormatCard.tsx
interface ExportFormatCardProps {
  title: string;                       // 「論文単位 Markdown」等 font 12px/700
  description: string;                 // font 10.5px var(--pr-text-muted)
  onExport: () => void;               // 「エクスポート ⤓」クリック
  busyLabel?: string | null;          // JSON 一括のジョブ進行中「準備中…」。非 null で置換表示
}

// ExportPaperPickerModal.tsx
interface ExportPaperPickerModalProps { open: boolean; onClose: () => void }

// ModelRoutingRow.tsx — 用途別 LLM ルーティング 1 行
type LlmUseCase = 'translation' | 'retranslation' | 'chat' | 'summary'
  | 'article' | 'vocab' | 'figure_dsl' | 'figure_image';
interface ModelRoutingRowProps {
  useCase: LlmUseCase;
  label: string;                       // 例 「全文翻訳」
  description: string;
  value: { provider: string; model: string };
  availableModels: Record<string, { model: string; label: string }[]>;   // GET /api/settings の available_models
  onChange: (next: { provider: string; model: string }) => void;
  divider?: boolean;
}

// SettingsSelect.tsx — 画面固有セレクト(ModelRoutingRow・表示カテゴリで使用)
interface SettingsSelectProps<T extends string> {
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  width?: number;                      // px。既定 160
  ariaLabel: string;
}

// ApiKeyRow.tsx
interface ApiKeyRowProps {
  provider: 'openai' | 'anthropic' | 'google' | 'deepseek' | 'xai';
  masked: string | null;               // null=未設定。例 "sk-…3fA"
  createdAt: string | null;
  onSave: (apiKey: string) => void;    // PUT
  onDelete: () => void;                // DELETE
  divider?: boolean;
}

// QuotaRow.tsx
interface QuotaRowProps { label: string; used: number; limit: number; unit: string; divider?: boolean }
```

- 使用する共通コンポーネント(plans/08 §5 の名前・仕様を変更しない): `SidebarNav`(§5.14)/ `Toggle`(§5.8)/ `SegmentedControl`(§5.1)/ `Card`(§5.9)/ `Popover`(§5.10)/ `Modal`(§5.11)/ `ProgressBar`(§5.12)/ `SearchBox`(§5.13)/ `Toast`(§5.20)/ `EmptyState`(§5.21)。
- **決定: セレクトは共通化せず画面固有 `SettingsSelect` とする**。理由: 16 画面中セレクト UI が必要なのは 4f のみで、plans/08 に該当コンポーネントが存在しないため。実装は button + Popover(§5.10)合成。寸法は §4.7.1。

## 4. レイアウト・スタイル完全仕様

extract/4f.md の全内容を取り込む。デザイン由来の生値はトークン対応(plans/08 §2)を併記する: `#F4F3EF`=`var(--pr-bg)`、`#FFFFFF`=`var(--pr-bg-card)`、`#E6E3DA`=`var(--pr-border-header)`、`#F7F6F2`=`var(--pr-bg-pane)`、`#E7E4DB`=`var(--pr-border-pane)`、`#E2DFD5`=`var(--pr-border-card)`、`#F0EDE4`=`var(--pr-border-hair)`、`#DDD9CF`=`var(--pr-border-control)`、`#EFEDE6`=`var(--pr-bg-muted)`、`#1E2227`=`var(--pr-text)`、`#3A3E44`=`var(--pr-text-nav)`、`#3C4046`=`var(--pr-text-mid)`、`#5B6067`=`var(--pr-text-sub)`、`#9A9EA4`=`var(--pr-text-muted)`、`#3E5C76`=`var(--pr-a)`(=`var(--pr-acc)`)、`rgba(62,92,118,0.10)`=`var(--pr-as)`(=`var(--pr-acc-s)`)。

### 4.0 デザイナー注記(逐語 — UI の一部ではない。実装対象外の参照情報)

フレーム直前の注記行(`display:flex;align-items:baseline;gap:10px;margin-bottom:12px`):

- バッジ: 「4f」 — `<a href="#4f">`、inline-flex 中央寄せ、min-width:32px / height:22px、背景 #2B2E33、文字色 #FFFFFF、border-radius:6px、font-size:12px、font-weight:700、text-decoration:none
- タイトル(font-size:15px / font-weight:700 / color:#1E2227): 「設定 — 翻訳・計測と提案・エクスポート」
- 説明文(font-size:12px / color:#777B81): 「翻訳スタイル既定(直訳はオンデマンド生成) / 読書計測の明示とオフ(仕様04 §6) / 提案の3段階(仕様06 §2) / P5 ロックインしない」
- ルート要素属性: `id="4f"`、`data-screen-label="4f 設定"`、width:1440px

フレーム外・下側の別状態ポップオーバー/バリエーション: **なし**(この画面はフレーム 1 枚のみ)。

### 4.1 レイアウト構造

```
┌────────────────────────────── 1440 × 860 ──────────────────────────────┐
│ トップバー h:52 (#FFFFFF, 下線 1px #E6E3DA)                              │
│ [Aロゴ+「Alinea」 w:198] [設定]  ……(flex:1 スペーサ)……  [YK アバター]     │
├───────────┬─────────────────────────────────────────────────────────────┤
│ 左ナビ     │ コンテンツ領域 (flex:1, overflow:hidden,                     │
│ w:216     │  justify-content:center で中央寄せ)                          │
│ #F7F6F2   │  ┌── 中央カラム w:720, padding:26px 0, 縦flex gap:22 ──┐     │
│ 右線1px   │  │ セクション「翻訳」(見出し+カード)                    │     │
│ #E7E4DB   │  │ セクション「読書の計測と提案」(見出し+カード)         │     │
│           │  │ セクション「エクスポート」(見出し+カード)             │     │
│           │  └────────────────────────────────────────────────┘     │
└───────────┴─────────────────────────────────────────────────────────────┘
```

- フレーム全体: width:1440px / height:860px、背景 #F4F3EF、border:1px solid #D6D3C9、border-radius:10px、box-shadow: 0 20px 44px rgba(28,30,34,0.12)、overflow:hidden、縦 flex、文字色 #1E2227。※ border/box-shadow/1440×860 はデザインカタログのフレーム装飾。実アプリではビューポート全面(min-height:100vh、背景 `var(--pr-bg)`)とし、フレーム枠線・影は付けない(1e 計画書と同じ扱い)。
- トップバー: height:52px、flex:none、背景 #FFFFFF、border-bottom:1px solid #E6E3DA、横 flex(align-items:center)、gap:14px、padding:0 18px
- 本体行: flex:1、横 flex、min-height:0
- 左ナビ: width:216px、flex:none、背景 #F7F6F2、border-right:1px solid #E7E4DB、padding:14px 10px、縦 flex gap:2px、font-size:12.5px、color:#3A3E44。※ SidebarNav(§5.14)の padding 既定は 12px 10px だが、**4f 実測は 14px 10px**。`className` 合成で `padding:14px 10px` を指定する(§5.14 の縮退版利用規定に基づく)。
- コンテンツ: flex:1、min-width:0、**overflow-y:auto**(決定: デザインの `overflow:hidden` は静的モック用。実装はカテゴリ内容が 808px を超えた場合に縦スクロール)、横 flex で justify-content:center、align-items:flex-start。内側カラムは width:720px、padding:26px 0、縦 flex、gap:22px。
- フォント: フレーム全体で UI フォント(IBM Plex Sans JP = `var(--pr-font-ui)`)を継承(個別の font-family 指定なし)。**この画面に SVG アイコンはない**。アイコン的表現はすべてテキスト文字(●, ○, ⤓, 訳, YK)。

### 4.2 トップバー(SettingsTopBar、h:52)

1. ロゴブロック: 横 flex、align-items:center、gap:8px、width:198px
   - ロゴマーク「A」: inline-flex 中央寄せ、22×22px、border-radius:6px、背景 var(--pr-a, #3E5C76)、文字 #FFFFFF、font-size:11.5px、font-weight:700。クリックで `/`(ダッシュボード 1d)へ遷移(決定: 全画面共通のロゴ挙動)。
   - ワードマーク「Alinea」: font-size:14.5px、font-weight:700、letter-spacing:0.5px
2. 画面名ラベル「設定」: font-size:12.5px、color:#5B6067
3. スペーサ: flex:1
4. アバター「YK」: inline-flex 中央寄せ、30×30px、border-radius:50%、背景 var(--pr-as, rgba(62,92,118,0.10))、文字色 var(--pr-a, #3E5C76)、font-size:11px、font-weight:700。イニシャルは `GET /api/auth/me` の `display_name` から生成(決定: 空白区切り先頭 2 語の頭文字を大文字化。1 語なら先頭 2 文字)。クリック挙動なし(すでに設定画面のため。決定)。

※ 4f のトップバーには検索・通知アイコンが**存在しない**(実測)。`LibraryShell` の TopBar は使わず `SettingsTopBar` を固有実装する。

### 4.3 左ナビ(設定カテゴリ、上から 8 項目)

各項目: padding:7px 10px、border-radius:6px。非選択項目は装飾なし=親の font-size:12.5px / color:#3A3E44 を継承。選択中: 背景 var(--pr-as, rgba(62,92,118,0.10))、文字色 var(--pr-a, #3E5C76)、font-weight:600。

| # | ラベル(逐語) | category id | href |
|---|---|---|---|
| 1 | アカウント | `account` | `/settings?category=account` |
| 2 | 表示 | `display` | `/settings?category=display` |
| 3 | 翻訳(デザインでは選択中) | `translation` | `/settings?category=translation` |
| 4 | 読書の計測と提案 | `reading` | `/settings?category=reading` |
| 5 | チャット | `chat` | `/settings?category=chat` |
| 6 | 通知 | `notifications` | `/settings?category=notifications` |
| 7 | エクスポート | `export` | `/settings?category=export` |
| 8 | ブラウザ拡張 | `extension` | `/settings?category=extension` |

実装: `SidebarNav`(共通 §5.14)に `main` = 上記 8 項目、`sections=[]`、`footer` なしで渡す。§5.14 の `SidebarNavItem.active` を「`item.id === 現在の category`」の項目のみ true にする(props にトップレベル `active` は存在しない)。

### 4.4 セクション「翻訳」(category=translation の描画済み部分)

- セクションラッパ(`SettingsSection`): 縦 flex gap:12px。見出し「翻訳」: font-size:14px、font-weight:700
- カード(`Card` padding='none'): 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:10px、overflow:hidden。行間は border-bottom:1px solid #F0EDE4 で区切る(最終行は下線なし)。

#### 4.4.1 行1: 既定の翻訳スタイル(TranslationStyleRow。padding:14px 18px、border-bottom:1px solid #F0EDE4、縦 flex gap:10px)

- ラベル「既定の翻訳スタイル」: font-size:12px、font-weight:600
- 選択カード 2 枚の横 flex(gap:10px)、各カード flex:1、padding:11px 13px、縦 flex gap:3px:
  - **選択中カード「● 自然訳(既定)」**(`value==='natural'` のとき): border:1.5px solid var(--pr-a, #3E5C76)、背景 var(--pr-as, rgba(62,92,118,0.10))、border-radius:8px
    - タイトル「● 自然訳(既定)」: font-size:12px、font-weight:700、color:var(--pr-a, #3E5C76)(● はラジオ選択マークをテキストで表現)
    - 説明「こなれた学術日本語。取り込み時に自動生成」: font-size:10.5px、color:#5B6067、line-height:1.6
  - **非選択カード「○ 直訳」**: border:1px solid #DDD9CF、border-radius:8px、背景指定なし=白
    - タイトル「○ 直訳」: font-size:12px、font-weight:600、color:#3C4046
    - 説明「原文の語順・構文を写像。文単位で対応を追える。初回切替時にオンデマンド生成」: font-size:10.5px、color:#9A9EA4、line-height:1.6
  - 選択が `literal` の場合は装飾が入れ替わる(決定): 直訳カードが選択中装飾(1.5px 枠+--pr-as 面+タイトル weight 700・color --pr-a)になり、マークは「●」に、自然訳カードは非選択装飾+「○」になる。ラベル文字列「自然訳(既定)」「直訳」自体は不変(「(既定)」は工場出荷既定の意)。
  - 選択中カードの枠 1.5px と非選択 1px の差による 0.5px レイアウトシフトは `box-shadow: inset 0 0 0 0.5px var(--pr-a)` + border 1px で吸収する(決定: 外形寸法を両状態で一致させる)。
- 補足注記「切替は表示の切替であり、原文表示とは独立です。文体は「だ・である」調に固定」: font-size:10px、color:#9A9EA4

#### 4.4.2 行2〜4: トグル行(SettingToggleRow。共通構造)

各行: padding:12px 18px、横 flex(align-items:center)、gap:12px。左にテキスト列(縦 flex gap:2px、flex:1)= タイトル(font-size:12px、font-weight:600)+説明(font-size:10.5px、color:#9A9EA4)。右に `Toggle`(共通 §5.8)。

- **トグルスイッチ(ON 状態、3 行とも同一)**: トラック 30×17px、border-radius:9px、背景 var(--pr-a, #3E5C76)、position:relative、flex:none。ノブ 13×13px、border-radius:50%、背景 #FFFFFF、absolute top:2px / right:2px(右寄せ=ON)。OFF 状態・遷移・disabled は plans/08 §5.8 の決定どおり(トラック `var(--pr-border-check)`、ノブ left:2px、transition 120ms ease-out)。
- 行2(border-bottom:1px solid #F0EDE4): タイトル「付録(Appendix)を自動翻訳しない」/ 説明「開いたとき・ボタンでオンデマンド翻訳(コスト対策)」— ON(= `auto_translate_appendix: false`)
- 行3(border-bottom:1px solid #F0EDE4): タイトル「表のセル内テキストを翻訳しない」/ 説明「数値・記号が主のため。表単位の「この表を翻訳」は常に利用可」— ON(= `translate_table_cells: false`)
- 行4(下線なし): タイトル「30 ページ超の論文はセクション選択を提案」/ 説明「全文翻訳の前に翻訳対象を選べます(既定は全選択)」— ON(= `suggest_section_selection_over_30_pages: true`)

### 4.5 セクション「読書の計測と提案」(category=reading)

- 見出し「読書の計測と提案」: font-size:14px、font-weight:700
- カード: 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:10px、overflow:hidden

#### 4.5.1 行1: トグル行(padding:12px 18px、border-bottom:1px solid #F0EDE4、構造は 4.4.2 と同一)

- タイトル「読書時間を計測する」/ 説明「タブ前面かつ操作中のみ記録。統計と「読んでいる」提案に使用。いつでもオフにできます」— トグル ON(30×17px、var(--pr-a)、ノブ右)= `track_reading_time: true`

#### 4.5.2 行2: ステータスの自動遷移(StatusTransitionRow。padding:14px 18px、縦 flex gap:9px)

- ラベル「ステータスの自動遷移」: font-size:12px、font-weight:600
- セグメンテッドコントロール(`SegmentedControl` 共通 §5.1、**size='lg'**: h26px・padding 0 14px・font 11.5px — 4f 実測と §5.1 の lg 定義が一致): 横 flex、背景 #EFEDE6(=`var(--pr-bg-muted)`)、border-radius:7px、padding:2px、gap:2px、align-self:flex-start
  - 「自動適用」(value=`auto`): 非選択、color:#5B6067
  - 「提案する(既定)」(value=`suggest`): **選択中**、背景 #FFFFFF(=`var(--pr-bg-seg-selected)`)、font-weight:600、box-shadow: 0 1px 2px rgba(28,30,34,0.10)(=`var(--pr-shadow-seg)`)
  - 「提案しない」(value=`off`): 非選択、color:#5B6067
- 補足注記「ステータスは勝手に変わりません。3 分以上読んだとき・最終ページ付近で 1 回だけ提案します」: font-size:10px、color:#9A9EA4

### 4.6 セクション「エクスポート」(category=export)

- 見出し行: 「エクスポート」(font-size:14px、font-weight:700)+ インライン補足 `<span>`「データはいつでも持ち出せます(P5)」(font-size:10.5px、font-weight:400、color:#9A9EA4、margin-left:6px)
- カード: 背景 #FFFFFF、border:1px solid #E2DFD5、border-radius:10px、padding:14px 18px、横 flex gap:10px。中に 3 枚のエクスポートカード(`ExportFormatCard`。各 flex:1、border:1px solid #DDD9CF、border-radius:8px、padding:11px 13px、縦 flex gap:4px):

| # | タイトル(font 12px/700) | 説明(font 10.5px #9A9EA4 line-height:1.6) | アクション |
|---|---|---|---|
| 1 | 論文単位 Markdown | メモ+注釈+チャットを 1 ファイルに(原文引用・アンカー付き)。Obsidian 互換の体裁 | エクスポート ⤓ |
| 2 | BibTeX / CSV | 書誌+ステータス+タグ+日付。主要リファレンスマネージャで読み込み可 | エクスポート ⤓ |
| 3 | JSON 一括 | 全データの一括エクスポート | エクスポート ⤓ |

- アクションリンク「エクスポート ⤓」: font-size:11px、color:var(--pr-a, #3E5C76)、font-weight:600、margin-top:2px(⤓ = 下向き矢印のダウンロード記号。テキストグリフ。plans/08 §6.2)

各アクションの確定挙動(デザイン未描画 — 決定):

1. **論文単位 Markdown**: クリックで `ExportPaperPickerModal` を開く。Modal(共通 §5.11、`width={520}`、`labelledBy="export-paper-picker-title"`)、タイトル「エクスポートする論文を選択」(font 14px/700、padding 16px 18px 10px)。本文: 検索 input(**決定: SearchBox 共通 §5.13 は使わない** — variant が `global`/`in-paper` の固定幅・キーキャップ付きの 2 種のみでモーダル内に適合しないため画面固有 input とする。width 100%(左右 padding 18px 内)、h28px、bg `var(--pr-bg-inset)`、border なし、border-radius 6px、padding 0 10px、font 11.5px、プレースホルダ「タイトル・著者で検索」color `var(--pr-text-icon)`。フォーカス: `outline: 1.5px solid var(--pr-acc); outline-offset: 1px`。モーダルオープン時にオートフォーカス)+結果リスト(`GET /api/library-items?q={q}&limit=20`、初期表示は `q` なし=更新順 20 件。リスト領域 max-height 320px・overflow-y:auto)。0 件時(決定): リスト位置に「該当する論文がありません」(font 11.5px、color `var(--pr-text-muted)`、text-align:center、padding 24px 0。ライブラリ自体が空の初期表示も同文言)。行: padding:9px 12px、border-bottom 1px `var(--pr-border-hair)`、タイトル font 12px/600 1 行省略+メタ(著者・年)font 10.5px `var(--pr-text-muted)`、ホバー bg `var(--pr-bg-hover)`。行クリックで `GET /api/library-items/{id}/export/markdown` を hidden `<a download>` クリックで取得しモーダルを閉じる。理由: エンドポイントが論文 id を要求し、一括 Markdown API は存在しないため対象選択 UI が必須。
2. **BibTeX / CSV**: クリックで `ExportFormatPopover`(Popover 共通 §5.10、`width={180}`、`placement='bottom-start'`、`caret={false}`(決定: メニュー用途はキャレットなし — 1e 検索ドロップダウンと同じ扱い)、リンク直下 4px)を開く。項目 2 行(h28px、font 11.5px、padding 0 10px、ホバー bg `var(--pr-bg-hover)`): 「BibTeX (.bib)」→ `GET /api/export/bibtex`、「CSV (.csv)」→ `GET /api/export/csv`。いずれもフィルタ無指定=全件。
3. **JSON 一括**: クリックで `POST /api/export/full` → 202 `{job_id}` を受領し、リンク表示を「準備中…」(font 11px、color `var(--pr-text-muted)`、weight 600、pointer-events:none)に置換して §2.3 のポーリング開始。`download_url` 取得で hidden `<a>` クリック→ダウンロード開始し、表示を「エクスポート ⤓」に復帰。失敗時は Toast「エクスポートの準備に失敗しました。もう一度お試しください」+表示復帰。

### 4.7 未描画カテゴリの確定仕様(決定 — デザインに描かれていない 5 カテゴリ+翻訳モデルカード)

すべて描画済みセクションのパターン(SettingsSection 見出し 14px/700、Card、SettingToggleRow 12px/600+10.5px 説明、行区切り 1px `var(--pr-border-hair)`、行 padding 12px 18px / 14px 18px)を**そのまま再利用**し、新しい寸法・色を発明しない。設定項目とキーは plans/03 §17.1 の設定オブジェクトと docs/09 §7.1 の対応表に 1:1 で従う。

#### 4.7.1 SettingsSelect(画面固有セレクト。ModelRoutingRow・表示カテゴリで使用)

- トリガー button: height:28px、padding:0 24px 0 10px、border:1px `var(--pr-border-control)`、border-radius:6px、背景 `var(--pr-bg-control)`、font-size:11.5px、color:`var(--pr-text)`、右端に「▾」(font 9px、color `var(--pr-text-muted)`、右 10px)。width は props(既定 160px)。
- メニュー: Popover(共通 §5.10、`placement='bottom-start'`、`caret={false}`)、width はトリガーと同幅、トリガー直下 4px。項目 h28px、font 11.5px、padding 0 10px、ホバー bg `var(--pr-bg-hover)`、選択中 bg `var(--pr-acc-s)`・color `var(--pr-acc)`・weight 600。
- a11y: `role="listbox"` / 項目 `role="option"` `aria-selected`。↑↓ で移動、Enter 確定、Esc 閉じる。

#### 4.7.2 ModelRoutingRow(用途別 LLM ルーティング行)

行 padding:12px 18px、横 flex、gap:12px。左テキスト列(flex:1)= ラベル 12px/600+説明 10.5px `var(--pr-text-muted)`。右に SettingsSelect ×2 横並び(gap:8px): プロバイダ(width 120px、選択肢はその用途で許可されたプロバイダ。plans/03 §17.1: テキスト用途 `openai|anthropic|google|deepseek`、`figure_image` のみ `openai|google|xai`。表示ラベル: OpenAI / Anthropic / Google / DeepSeek / xAI)+モデル(width 200px、選択肢は `available_models[provider]` の `{model, label}`)。プロバイダ変更時、モデルは `available_models[新provider][0].model` に自動リセット。変更ごとに `PATCH /api/settings` で `llm_routing.{useCase}` を更新。

#### 4.7.3 category=translation の追加カード「翻訳モデル」

翻訳セクション(§4.4)の下、gap:22px で続く 2 つ目の SettingsSection。見出し「翻訳モデル」。Card 内 2 行:

| 行 | ラベル | 説明 | キー |
|---|---|---|---|
| 1(divider) | 全文翻訳 | 取り込み時の自動翻訳・オンデマンド翻訳に使用 | `llm_routing.translation` |
| 2 | 再翻訳(高品質) | 段落単位の「✦ 高品質で再翻訳」に使用 | `llm_routing.retranslation` |

#### 4.7.4 category=account(セクション 4 つ、gap:22px)

1. **「プロフィール」**: Card 内 4 行(データは `GET /api/auth/me`)。
   - 行1(divider): ラベル「表示名」+右に値 `display_name`(font 11.5px、color `var(--pr-text-sub)`)。行構造: padding 12px 18px、横 flex space-between、align-items:center。
   - 行2(divider): 「メールアドレス」+ `email`。
   - 行3(divider): 「ログイン方法」+ `providers` を「Google · GitHub · メール」形式で連結表示(表示名変換: `google`→Google / `github`→GitHub / `email`→メール。配列順のまま「 · 」区切り)。
   - 行4: 左「ログアウト」ボタン(h28px、padding 0 12px、border 1px `var(--pr-border-control)`、radius 6px、font 11.5px/600、color `var(--pr-text-mid)`、bg `var(--pr-bg-control)`)→ `POST /api/auth/logout` → `/login` へ。右端「アカウントを削除」テキストリンク(font 11px、color `var(--pr-warn)`(#A05A42。plans/08 に danger トークンは存在せず、破壊的操作は全画面 `--pr-warn` で統一 — 4b「無効化する」と同じ)、weight 600)→ 確認 Modal(共通 §5.11、`width={380}`、`labelledBy="delete-account-title"`。タイトル「アカウントを削除しますか?」font 14px/700、本文「すべてのデータが完全に削除されます。この操作は取り消せません。先に JSON 一括エクスポートでのバックアップをおすすめします」font 12px `var(--pr-text-sub)` line-height:1.7、フッタ右寄せ「キャンセル」(h26px、border 1px `var(--pr-border-control)`、bg `var(--pr-bg-control)`、font 11px)+「削除する」(h26px、bg `var(--pr-warn)`、文字 #FFFFFF、font 11px/600)— 4b RevokeShareModal と同一様式)→ `DELETE /api/auth/account`。決定: 削除リクエスト中は「削除する」を disabled(opacity 0.5)、失敗時はモーダルを開いたまま Toast(error)「アカウントを削除できませんでした。もう一度お試しください」。
2. **「API キー(BYOK)」**(titleNote: 「設定するとクォータを消費しません」): Card 内 5 行(`openai` / `anthropic` / `google` / `deepseek` / `xai`。表示名 OpenAI / Anthropic / Google / DeepSeek / xAI)。各行 = ApiKeyRow: 左にプロバイダ名 12px/600+説明(設定済み: `masked`(例 "sk-…3fA")+「 · 登録: {created_at を YYYY/M/D}」、未設定: 「未設定」)10.5px `var(--pr-text-muted)`。右にボタン: 設定済み=「再設定」+「削除」、未設定=「設定」(いずれも h24px、padding 0 10px、font 11px/600、border 1px `var(--pr-border-control)`、radius 6px。「削除」のみ color `var(--pr-warn)`)。「設定/再設定」クリックで ApiKeyEditPopover(Popover 共通 §5.10、`width={300}`、`placement='bottom-end'`(アンカー=当該ボタン、直下 4px)、`caret={false}`。内側 padding 12px、横 flex gap:8px): `<input type="password">`(flex:1、h28px、border 1px `var(--pr-border-control)`、radius 6px、font 11.5px、padding 0 10px、プレースホルダ「API キーを貼り付け」。オープン時オートフォーカス)+「保存」ボタン(h28px、padding 0 12px、bg `var(--pr-acc)`、color #FFFFFF、radius 6px、font 11.5px/600。決定: 前後空白トリム後 0 文字なら disabled(opacity 0.5)。送信値もトリム後)→ `PUT /api/settings/api-keys/{provider}`。決定: 成功でポップオーバーを閉じる(成功 Toast なし — §5.4)。422 は §5.4 の inline エラー。カード下の補足注記(padding 0 18px 12px、font 10px、color `var(--pr-text-muted)`): 「キーは暗号化して保存され、再表示はできません(再入力のみ)」(docs/09 §4)。
3. **「今月の利用量」**(titleNote: `GET /api/settings/quota` の `period` を「2026年7月」形式で表示): Card 内 3 行 QuotaRow(padding 12px 18px、divider): 左ラベル 12px/600(「全文翻訳」=`usage.translation_papers`・unit「本」/「チャットメッセージ」=`usage.chat_messages`・unit「件」/「画像生成」=`usage.images`・unit「枚」)+右に「{used} / {limit} {unit}」(font 11.5px、color `var(--pr-text-sub)`)、下に ProgressBar(共通 §5.12、`value = used/limit×100`(§5.12 のクランプ規定どおり 100 上限)、width 100%、margin-top:6px)。**決定: BYOK 判定の対応** — 「全文翻訳」「チャットメッセージ」は `byok_active.text`、「画像生成」は `byok_active.image` を参照し、true の項目は ProgressBar の代わりに「BYOK 利用中(クォータ非消費)」(font 10.5px、color `var(--pr-acc)`、weight 600)。
4. **「モデルルーティング(詳細)」**: Card 内 ModelRoutingRow ×5(`summary`「要約」/ `article`「記事生成」/ `vocab`「語彙生成」/ `figure_dsl`「概要図データ生成」/ `figure_image`「解説図画像」)+ 最終行 SettingToggleRow「概要図をラスター画像で生成」/「オフ(既定)では SVG 決定的レンダリング。オンで画像生成 API を使用」= `llm_routing.overview_figure_raster_mode`(既定 OFF)。配置理由: spec-decisions F9「LLM プロバイダ・モデル選択と BYOK は『翻訳』『チャット』『アカウント』カテゴリ内に追加する(デザインのカテゴリ構成は変えない)」に従い、翻訳・チャット以外の用途をアカウントに集約。

#### 4.7.5 category=display(セクション「表示」、Card 1 枚 6 行)

値域は plans/03 §17.1 のとおり。行はすべて padding 12px 18px、横 flex、左ラベル列(flex:1)+右コントロール。

| 行 | ラベル / 説明 | コントロール | キー |
|---|---|---|---|
| 1(divider) | テーマ / ライト・ダーク・OS 設定に追従 | SegmentedControl lg: 「ライト」`light`・「ダーク」`dark`・「システム」`system` | `display.theme` |
| 2(divider) | アクセントカラー / リンク・選択・ハイライトの基調色 | 色スウォッチ 4 個横並び(gap:8px)。各 22×22px 円、bg = #3E5C76 / #4A6B57 / #6E5A7E / #7A5C48。選択中: `box-shadow: 0 0 0 2px var(--pr-bg-card), 0 0 0 3.5px var(--pr-acc)`。`role="radiogroup"`+各スウォッチ `role="radio"` `aria-checked`、aria-label は plans/08 §2.3 ACCENTS の label(「スレートブルー」「緑」「紫」「テラコッタ」) | `display.accent` |
| 3(divider) | 本文の書体 / 訳文・原文本文に適用 | SegmentedControl lg: 「明朝」`serif`・「ゴシック」`sans` | `display.body_font` |
| 4(divider) | 本文サイズ / 14〜20px | Stepper(下記)0.5 刻み、表示「16.5px」 | `display.font_size_px` |
| 5(divider) | 行間 / 1.6〜2.4 | Stepper 0.05 刻み、表示「2.15」 | `display.line_height` |
| 6 | 本文幅 / 600〜840px | Stepper 20 刻み、表示「720px」 | `display.content_width_px` |

- Stepper(画面固有・display 専用): 「−」「+」ボタン各 22×22px(border 1px `var(--pr-border-control)`、radius 6px、font 12px、color `var(--pr-text-mid)`、ホバー bg `var(--pr-bg-hover)`、端値で opacity 0.4+disabled)+中央に現在値(width 52px、text-align:center、font 11.5px)。

#### 4.7.6 category=chat(セクション「チャット」、Card 2 枚)

1. Card 1 行: SettingToggleRow「注釈・メモを文脈に含める」/「オンにすると「さっき疑問ハイライトした箇所」のような参照が通じます(既定オン)」= `chat.include_annotations_and_notes`(既定 ON。docs/05 §2)。
2. SettingsSection「チャットモデル」: Card 内 ModelRoutingRow ×1(`chat`「チャット」/「論文についての質疑・定型チップに使用」= `llm_routing.chat`)。

#### 4.7.7 category=notifications(セクション「通知」、Card 1 枚 3 行 — docs/06 §7 の通知 3 種)

| 行 | タイトル / 説明 | キー(既定すべて ON) |
|---|---|---|
| 1(divider) | 翻訳完了 / 取り込み・全文翻訳が終わったとき | `notifications.translation_complete` |
| 2(divider) | ステータス提案 / 「読んでいる」「読了」への変更提案(✦) | `notifications.status_suggestion` |
| 3 | 締切リマインド / コレクションの締切が近づいたとき | `notifications.deadline_reminder` |

#### 4.7.8 category=extension(セクション「ブラウザ拡張」、Card 1 枚 1 行)

- SettingToggleRow「arXiv ページ内に「A 保存」ボタンを表示」/「arxiv.org の論文ページ限定のオプトイン。既定はオフ」= `extension.arxiv_inline_button`(既定 OFF。docs/08 §5)。

### 4.8 全 UI 文言(逐語)

#### トップバー
- 訳 / Alinea / 設定 / YK

#### 左ナビ
- アカウント / 表示 / 翻訳 / 読書の計測と提案 / チャット / 通知 / エクスポート / ブラウザ拡張

#### セクション「翻訳」
- 翻訳
- 既定の翻訳スタイル
- ● 自然訳(既定)
- こなれた学術日本語。取り込み時に自動生成
- ○ 直訳
- 原文の語順・構文を写像。文単位で対応を追える。初回切替時にオンデマンド生成
- 切替は表示の切替であり、原文表示とは独立です。文体は「だ・である」調に固定
- 付録(Appendix)を自動翻訳しない
- 開いたとき・ボタンでオンデマンド翻訳(コスト対策)
- 表のセル内テキストを翻訳しない
- 数値・記号が主のため。表単位の「この表を翻訳」は常に利用可
- 30 ページ超の論文はセクション選択を提案
- 全文翻訳の前に翻訳対象を選べます(既定は全選択)

#### セクション「読書の計測と提案」
- 読書の計測と提案
- 読書時間を計測する
- タブ前面かつ操作中のみ記録。統計と「読んでいる」提案に使用。いつでもオフにできます
- ステータスの自動遷移
- 自動適用
- 提案する(既定)
- 提案しない
- ステータスは勝手に変わりません。3 分以上読んだとき・最終ページ付近で 1 回だけ提案します

#### セクション「エクスポート」
- エクスポート
- データはいつでも持ち出せます(P5)
- 論文単位 Markdown
- メモ+注釈+チャットを 1 ファイルに(原文引用・アンカー付き)。Obsidian 互換の体裁
- エクスポート ⤓
- BibTeX / CSV
- 書誌+ステータス+タグ+日付。主要リファレンスマネージャで読み込み可
- JSON 一括
- 全データの一括エクスポート

(未描画カテゴリの文言は §4.7 の各表・本文中の「」内が確定文言。)

### 4.9 データフィールド(画面 ⇄ API 対応の完全表)

| 画面コントロール | API キー(plans/03 §17.1) | 型/値域 | 工場出荷既定 |
|---|---|---|---|
| 既定の翻訳スタイル | `translation.default_style` | `"natural" \| "literal"` | `"natural"`(自然訳) |
| 付録を自動翻訳しない(否定形 UI) | `translation.auto_translate_appendix` | boolean | `false`(UI は ON) |
| 表セルを翻訳しない(否定形 UI) | `translation.translate_table_cells` | boolean | `false`(UI は ON) |
| 30 ページ超セクション選択提案 | `translation.suggest_section_selection_over_30_pages` | boolean | `true`(ON) |
| 読書時間を計測する | `reading.track_reading_time` | boolean | `true`(ON) |
| ステータスの自動遷移 | `reading.status_transition` | `"auto" \| "suggest" \| "off"` | `"suggest"`(提案する) |
| テーマ | `display.theme` | `light \| dark \| system` | `system` |
| アクセント | `display.accent` | `#3E5C76 \| #4A6B57 \| #6E5A7E \| #7A5C48` | `#3E5C76` |
| 本文書体 | `display.body_font` | `serif \| sans` | `serif` |
| 本文サイズ | `display.font_size_px` | 14–20(0.5 刻み) | 16.5 |
| 行間 | `display.line_height` | 1.6–2.4(0.05 刻み) | 2.15 |
| 本文幅 | `display.content_width_px` | 600–840(20 刻み) | 720 |
| 注釈・メモを文脈に含める | `chat.include_annotations_and_notes` | boolean | `true` |
| 通知 3 種 | `notifications.translation_complete / status_suggestion / deadline_reminder` | boolean ×3 | すべて `true` |
| arXiv ページ内ボタン | `extension.arxiv_inline_button` | boolean | `false` |
| 用途別ルーティング ×8+ラスターモード | `llm_routing.*` | `{provider, model}` ×8 + boolean | plans/03 §17.1 の既定 |

固定パラメータ(文言埋め込み。設定不可): セクション選択提案の閾値 = 30 ページ / 読書提案の条件 = 3 分以上・最終ページ付近・提案 1 回 / 訳文文体 = だ・である調。

## 5. 状態とインタラクション

### 5.1 デザインに描かれた状態(初期表示 = デザインフレームの再現条件)

- 左ナビ「翻訳」選択中(`?category=translation`): bg `var(--pr-as)` + color `var(--pr-a)` + weight 600。他 7 項目は非選択。
- 翻訳スタイル: 「自然訳(既定)」選択中(1.5px `--pr-a` 枠+`--pr-as` 面+● マーク)、「直訳」非選択(1px #DDD9CF 枠+○ マーク)。
- トグル 4 つ(付録/表セル/30 ページ/計測)すべて ON(トラック `--pr-a`、ノブ右寄せ)。
- セグメンテッドコントロール: 「提案する(既定)」選択中(白背景+shadow)、「自動適用」「提案しない」非選択。
- ホバー状態・ポップオーバー・空状態・通知・フレーム外バリエーションはデザインに描かれていない(以下 §5.2〜§5.6 で決定補完)。

### 5.2 遷移マトリクス

| 操作 | 結果 |
|---|---|
| 左ナビ項目クリック | `router.replace('/settings?category={id}')` → コンテンツペイン切替(コンテンツの scrollTop を 0 に戻す) |
| ラジオカードクリック(非選択側) | 楽観更新で装飾即時入替+`PATCH {"translation":{"default_style":…}}`。直訳への初回切替のオンデマンド生成はビューア側の責務(この画面ではジョブを起動しない) |
| トグルクリック / Space | 楽観更新+`PATCH`(否定形 2 項目は反転変換して送信) |
| セグメントクリック / ←→ | 楽観更新+`PATCH {"reading":{"status_transition":…}}` |
| 「エクスポート ⤓」×3 | §4.6 の 3 挙動(モーダル / ポップオーバー / ジョブ+ポーリング) |
| ロゴクリック | `/` へ遷移 |
| Esc | 開いている Popover / Modal を閉じる(共通 §5.10/§5.11 の挙動) |

### 5.3 ローディング(決定)

`GET /api/settings` 未解決の間、コンテンツカラムにスケルトンを表示する:

- 見出しスケルトン: 64×14px、border-radius:4px、bg `var(--pr-bg-inset)`。
- カードスケルトン: width 720px、Card 枠(border 1px `var(--pr-border-card)`、radius 10px)内に行スケルトン 3 本(各 h:41px、padding 12px 18px 相当で、左に 180×12px+280×10px の 2 本バー(radius 4px、bg `var(--pr-bg-inset)`)、右に 30×17px ピル形バー)。
- 見出し+カードの組を 2 組、gap:22px。全バーに共通パルスアニメーション(opacity 0.55⇄1.0、1,200ms ease-in-out 交互)。
- 左ナビ・トップバーは即時描画(静的データ)。

### 5.4 エラー(決定)

- `GET /api/settings` 失敗: コンテンツカラムに EmptyState(共通 §5.21 の寸法・様式をそのまま使用。アイコン・グリフは付けない — §5.21 の規定): `title`「設定を読み込めませんでした」、`description`「通信状態を確認してから再試行してください」、`action`「再試行」→ `refetch()`。
- `PATCH /api/settings` 失敗(ネットワーク/422): 楽観更新をロールバックし Toast(共通 §5.20。決定: 本画面で出す Toast はすべて `kind: 'error'`)「設定を保存できませんでした。もう一度お試しください」。
- `PUT /api/settings/api-keys` の 422: ポップオーバー内の input 下に inline エラー(font 10px、color `var(--pr-warn)`)「キーの形式が正しくありません」。ポップオーバーは開いたまま。
- `POST /api/auth/logout` 失敗(決定): Toast(error)「ログアウトに失敗しました。もう一度お試しください」。画面遷移しない。
- ダウンロード系 GET の失敗はブラウザ既定に委ね、追加 UI を出さない(アンカー直接 GET のため)。
- 成功時のフィードバックは**出さない**(決定: コントロール自体の状態変化が確認 UI。Toast の乱発を避ける)。

### 5.5 ホバー・フォーカス(決定)

- 左ナビ非選択項目ホバー: bg `var(--pr-bg-hover)`(選択中は変化なし)。cursor:pointer。
- ラジオカード非選択ホバー: border-color `var(--pr-acc-m)`。選択中カードは変化なし。
- 「エクスポート ⤓」ホバー: text-decoration:underline。
- Toggle / SegmentedControl / SettingsSelect / ボタン類のフォーカス: 共通規定(plans/08 §5 共通事項)`outline: 1.5px solid var(--pr-acc); outline-offset: 1px`(focus-visible 時)。
- トグル行はタイトル・説明のクリックでも切替できる(行全体を `<label>` 化。cursor:pointer)。

### 5.6 テーマ設定の即時反映(決定)

`display.theme` / `display.accent` / `display.body_font` の変更は PATCH 成功を待たず、楽観更新と同時に `document.documentElement` の `data-theme` / `data-accent` / `data-body-font` 属性(plans/08 §8.1)と Cookie `yk_theme` / `yk_accent` / `yk_font`(plans/08 §8.2 の FOUC 防止キー。localStorage は使わない)へ即時反映する。ロールバック時は属性・Cookie とも戻す。
- **決定: accent の hex → `data-accent` キー変換**は plans/08 §2.3 の `ACCENTS` 対応表に従い web 層で行う: `#3E5C76`→`slate` / `#4A6B57`→`green` / `#6E5A7E`→`purple` / `#7A5C48`→`terracotta`(API・設定値は hex、DOM 属性と Cookie `yk_accent` はキー名)。
- **決定: `theme: "system"` 選択時**は plans/08 §8.1 のとおり `matchMedia('(prefers-color-scheme: dark)')` の解決値(`light`/`dark`)を `data-theme` に書き、Cookie `yk_theme` には `system` を保存する。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Storybook ストーリー `screens/4f-settings--frame` を VRT 対象とする。このストーリーは**デザインフレームの合成再現**: SettingsShell(ナビ active=翻訳)+コンテンツカラムに「翻訳(§4.4 のカードのみ。翻訳モデルカードは含めない)」「読書の計測と提案」「エクスポート」の 3 セクションを gap:22px で縦積みし、1440×860 ビューポートで design/4f フレームと照合する。

- [ ] フレーム: 1440×860、bg #F4F3EF。トップバー h52・bg #FFFFFF・下線 1px #E6E3DA、padding 0 18px、gap 14px。
- [ ] トップバー: ロゴ 22×22 radius6 #3E5C76+「Alinea」14.5px/700/ls0.5、「設定」12.5px #5B6067、アバター 30×30 円 rgba(62,92,118,0.10)/#3E5C76 11px/700。検索・通知アイコンが無い。
- [ ] 左ナビ: w216、bg #F7F6F2、右線 1px #E7E4DB、padding 14px 10px、gap2、12.5px #3A3E44。8 項目が §4.3 の順・逐語一致。「翻訳」のみ bg rgba(62,92,118,0.10)+#3E5C76+600、項目 padding 7px 10px radius6。
- [ ] コンテンツ: 中央 720px、padding 26px 0、セクション gap 22px、見出し 14px/700。
- [ ] 翻訳カード: bg #FFFFFF、border 1px #E2DFD5、radius 10、行区切り 1px #F0EDE4(最終行なし)。行1 padding 14px 18px gap10、ラジオカード 2 枚 flex:1 gap10・padding 11px 13px・radius8。選択中= 1.5px #3E5C76 枠+rgba(62,92,118,0.10) 面+タイトル 12px/700 #3E5C76、非選択= 1px #DDD9CF 枠+12px/600 #3C4046。説明 10.5px(#5B6067 / #9A9EA4)lh1.6。補足 10px #9A9EA4。両カードの外形寸法が一致(0.5px シフトなし)。
- [ ] トグル行 ×4: padding 12px 18px、タイトル 12px/600+説明 10.5px #9A9EA4(gap2)、トラック 30×17 radius9 #3E5C76、ノブ 13×13 白・top2/right2。
- [ ] 計測カード行2: ラベル 12px/600、セグメント h26・padding 0 14px・font 11.5px、トラック bg #EFEDE6 radius7 padding2 gap2、選択中セグメント白+600+shadow 0 1px 2px rgba(28,30,34,0.10)、非選択 #5B6067。補足 10px #9A9EA4。
- [ ] エクスポート: 見出しに補足 span 10.5px #9A9EA4(margin-left 6px)。カード padding 14px 18px、内カード 3 枚 flex:1・1px #DDD9CF・radius8・padding 11px 13px・gap4。タイトル 12px/700、説明 10.5px #9A9EA4 lh1.6、リンク 11px #3E5C76/600(margin-top 2px)。
- [ ] 全 UI 文言が §4.8 と逐語一致(●○⤓ のグリフ、「(既定)」「(P5)」の括弧含む)。SVG アイコンが 1 つも無い。
- [ ] ダークモード(`data-theme=dark`)でトークン置換のみで破綻しない(ハードコード色が無い)。

### 6.2 機能検証

- [ ] `/settings` が `?category=account` 相当(アカウント)を既定表示し、不正な `category` 値でも account へフォールバックする。未ログインは `/login?next=/settings` へリダイレクト。
- [ ] 左ナビ 8 項目のクリックで `?category=` が `router.replace` され(履歴が増えない)、対応カテゴリのみが表示される。1d からのリンク `/settings?category=export` / `/settings?category=reading` が直接該当カテゴリを開く。
- [ ] 6 コントロール(スタイル/トグル×4/セグメント)の変更がそれぞれ 1 回の `PATCH /api/settings` を送り、否定形 UI 2 項目(付録・表セル)は反転した boolean が送信される(ON → `false`)。
- [ ] PATCH 失敗時に UI がロールバックし、Toast「設定を保存できませんでした。もう一度お試しください」が出る。
- [ ] 論文単位 Markdown: モーダルで検索(200ms デバウンス)→ 行クリックで `GET /api/library-items/{id}/export/markdown` がダウンロードされモーダルが閉じる。
- [ ] BibTeX / CSV: ポップオーバーの 2 項目からそれぞれ `.bib` / `.csv`(UTF-8 BOM)が全件でダウンロードされる。
- [ ] JSON 一括: 押下で 202 → 表示が「準備中…」→ 2,000ms ポーリング → `download_url` 取得で自動ダウンロード+表示復帰。失敗時は Toast+表示復帰。
- [ ] アカウント: プロフィール表示(me の値)、ログアウトで `/login` へ、削除は確認モーダル経由で `DELETE /api/auth/account`。
- [ ] BYOK: 5 プロバイダのキー登録(PUT)→ マスク表示(例 "sk-…3fA")、平文の再表示 UI が存在しない、削除(DELETE)後「未設定」表示。登録/削除で quota の `byok_active` 表示が更新される。
- [ ] クォータ: 3 項目の used/limit と ProgressBar、BYOK 有効項目は「BYOK 利用中(クォータ非消費)」表示。
- [ ] 表示カテゴリ: テーマ/アクセント/書体の変更が即時に `data-theme`/`data-accent`/`data-body-font` と Cookie(`yk_theme`/`yk_accent`/`yk_font`)へ反映され(accent は hex→キー名変換)、失敗時は属性・Cookie ごと戻る。Stepper が値域端で disabled。
- [ ] モデルルーティング: プロバイダ変更でモデル選択肢が `available_models[provider]` に切替わり先頭値に自動リセット、`figure_image` のプロバイダ選択肢が `openai|google|xai` のみ。
- [ ] トグル行が `role="switch"`+`aria-checked`、セグメントが `role="radiogroup"`、行ラベルクリックで切替可、全コントロールがキーボード操作可(Tab/Space/矢印/Esc)。
- [ ] `reading.track_reading_time=false` にするとビューアの計測が止まる(viewer-shell 計画書 §計測の参照設定。結合テスト)。
