# 08. デザインシステム実装計画(packages/tokens + 共通UIコンポーネント)

> 対象読者と前提: 本書は「訳読 / YAKUDOKU — 論文読解ワークベンチ」のフロントエンド実装者向けに、確定デザイン(16画面)のビジュアルシステムをコードとして確定させる計画書である。機能仕様は docs/00〜12(特に docs/04 §14、docs/09 §6・§7.2)を正とし、ピクセル値は抽出ファイル(extract/_global.md および各画面 extract/<ID>.md)の値をそのまま採用する。技術スタックは確定済み: pnpm workspaces + Turborepo、apps/web = Next.js 15(App Router)+ React 19 + TypeScript 5 + Tailwind CSS v4、`packages/tokens` = デザイントークン単一ソース。本書に書かれた値・識別子が実装の正であり、デザインHTML と差分を作らないこと(docs/09 §8 受け入れ基準)。

## 1. パッケージ構成と責務

### 1.1 packages/tokens のファイル構成

```
packages/tokens/
├── package.json
├── tsconfig.json
├── scripts/
│   └── gen-accents.ts        # accent.ts から css/accents.css を決定的に生成
├── css/
│   ├── tokens.css            # 基本トークン(:root=ライト、[data-theme="dark"]=ダーク)
│   ├── accents.css           # 生成物。アクセント4色 × --pr-a系6変数+selection
│   ├── fonts.css             # フォントスタック変数と --pr-jp 切替
│   └── theme.css             # Tailwind v4 @theme マッピング
└── src/
    ├── accent.ts             # アクセント導出規則(JS関数。§2.3)
    ├── tokens.ts             # TS定数(ステータス色・注釈色・z-index等のエクスポート)
    └── index.ts              # re-export
```

### 1.2 package.json(完全形)

```json
{
  "name": "@yakudoku/tokens",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": "./dist/index.js"
    },
    "./css/tokens.css": "./css/tokens.css",
    "./css/accents.css": "./css/accents.css",
    "./css/fonts.css": "./css/fonts.css",
    "./css/theme.css": "./css/theme.css"
  },
  "scripts": {
    "gen": "tsx scripts/gen-accents.ts",
    "build": "pnpm gen && tsc -p tsconfig.json",
    "test": "vitest run"
  },
  "devDependencies": {
    "tsx": "^4.19.0",
    "typescript": "^5.6.0",
    "vitest": "^3.0.0"
  }
}
```

### 1.3 責務の分担

- **packages/tokens**: CSS変数・フォント指定・Tailwind theme・TS定数のみ。React コンポーネントは含まない。
- **apps/web/src/components/ui/**: 共通 React コンポーネント(§5)。apps/web 専用。
- **apps/web/src/components/icons/**: インラインSVGアイコン(§6)。
- **apps/extension**: `@yakudoku/tokens/css/tokens.css`・`accents.css`・`fonts.css` を popup にインポートして色・書体を共有する。決定: React コンポーネントは共有しない(拡張の UI は小規模で MV3 バンドル制約があるため、拡張側に専用コンポーネントを持つ)。

## 2. CSS トークン完全定義

### 2.1 css/tokens.css(完全形)

値の出典: ライト系は extract/_global.md・1a・1b・1e・4a・4f、ダーク系は extract/1c.md。ダークで未描画の面(ライブラリ等)の対応値は本書で確定し「決定:」を付す(§2.2)。

```css
/* @yakudoku/tokens — css/tokens.css
   出典: 確定デザイン『論文読解システム デザイン.dc.html』ヘッダコメント+Tweaksスクリプト。
   このファイルの値を書き換える場合は必ずデザイン抽出ファイルと突き合わせること。 */

:root {
  /* ── 背景・面(ライト) ─────────────────────────── */
  --pr-bg-canvas: #E8E6DF;      /* 最下層キャンバス */
  --pr-bg-app: #FBFAF7;         /* ビューア系画面の地 */
  --pr-bg-app-alt: #F4F3EF;     /* ライブラリ・設定系画面の地 */
  --pr-bg-pane: #F7F6F2;        /* 目次ペイン・サイドバー */
  --pr-bg-card: #FFFFFF;        /* カード・パネル・ヘッダ */
  --pr-bg-pop: #FFFFFF;         /* ポップオーバー面(ダークで #22262D に分岐) */
  --pr-bg-control: #FFFFFF;     /* ピル・キーキャップ等の小物面 */
  --pr-bg-inset: #F1EFE9;       /* 検索ボックス・タグチップのくぼみ面 */
  --pr-bg-muted: #EFEDE6;       /* セグメンテッドコントロールのトラック */
  --pr-bg-thumb: #EFEDE6;       /* サムネイルプレースホルダ */
  --pr-bg-feed: #FCFBF8;        /* チャットメッセージ領域・注釈リスト背景 */
  --pr-bg-hover: #FAF9F5;       /* リスト行のホバー/キーボード選択 */
  --pr-bg-unread: #FCFBF6;      /* 未読通知の行背景 */
  --pr-bg-knowledge: #F5F3EC;   /* 「論文外の知識」ボックス */
  --pr-bg-knowledge-label: #E7E4DA;
  --pr-bg-comment: #F7F5EF;     /* 注釈コメントボックス */
  --pr-bg-seg-selected: #FFFFFF;/* セグメント選択中の面 */

  /* 常時ダークの浮遊UI(選択メニュー・一括操作バー・選択中FilterChip・Toast)
     ライト/ダーク両テーマで同一値を使う(1b・1e の描画値) */
  --pr-elev-bg: #26292E;
  --pr-elev-fg: #E8E6E1;
  --pr-elev-fg-muted: #9BA1A9;
  --pr-elev-divider: #4A4E55;

  /* ── 文字(ライト) ────────────────────────────── */
  --pr-text: #1E2227;           /* 基準文字色(見出し・強) */
  --pr-text-body: #24272B;      /* 訳文本文 */
  --pr-text-en: #33373C;        /* 原文英語本文 */
  --pr-text-mid: #3C4046;       /* コントロールラベル */
  --pr-text-nav: #3A3E44;       /* サイドバー項目 */
  --pr-text-sub: #5B6067;       /* 補助1 */
  --pr-text-sub2: #777B81;      /* 補助2 */
  --pr-text-muted: #9A9EA4;     /* 淡 */
  --pr-text-icon: #8A8E94;      /* アイコン・プレースホルダ */
  --pr-text-faint: #B6BAC0;     /* 最淡(段落対応⇄ 等) */
  --pr-text-thumb: #B0B4BA;     /* サムネイル内ラベル */
  --pr-text-eq: #6A6E74;        /* 式番号・知識ラベル文字 */

  /* ── 境界(ライト) ────────────────────────────── */
  --pr-border-frame: #D6D3C9;
  --pr-border-header: #E6E3DA;  /* ヘッダ下線 */
  --pr-border-pane: #E7E4DB;    /* ペイン境界 */
  --pr-border-soft: #ECE9DF;    /* タブ行下線・カラム見出し下線 */
  --pr-border-card: #E2DFD5;    /* カード枠 */
  --pr-border-hair: #F0EDE4;    /* カード内行区切り */
  --pr-border-row: #F4F1E9;     /* テーブル行区切り */
  --pr-border-control: #DDD9CF; /* ピル・ボタン・チップ枠 */
  --pr-border-pop: #DDD9CF;     /* ポップオーバー枠(ダークで #3A404A) */
  --pr-border-keycap: #DAD7CD;
  --pr-border-dashed: #D5D1C5;  /* 未翻訳付録の破線 */
  --pr-border-quote: #D8D5CB;   /* 引用ブロック左線 */
  --pr-border-check: #C9C5BA;   /* チェックボックス枠 */
  --pr-border-thumb: #E0DDD3;   /* サムネイル枠 */

  /* ── 注釈4色(固定。テーマ非依存) ─────────────── */
  --pr-ann-important: #C49432;
  --pr-ann-important-bg: rgba(196, 148, 50, 0.26);
  --pr-ann-important-chip-bg: rgba(196, 148, 50, 0.30);
  --pr-ann-important-chip-fg: #8A6A24;
  --pr-ann-important-count-bg: rgba(196, 148, 50, 0.18); /* 目次の注釈数バッジ */
  --pr-ann-question: #5884AA;
  --pr-ann-question-bg: rgba(88, 132, 170, 0.22);
  --pr-ann-idea: #659471;
  --pr-ann-idea-bg: rgba(101, 148, 113, 0.22);
  --pr-ann-term: #82827E;
  --pr-ann-term-bg: rgba(130, 130, 126, 0.18);

  /* ── ステータス6色(1e。読んでいる=アクセント連動) ── */
  --pr-status-to-read: #9AA0A6;
  --pr-status-read-next: #C49432;
  --pr-status-reading: var(--pr-acc);
  --pr-status-done: #659471;
  --pr-status-reread: #8E7AA6;
  --pr-status-on-hold: #B0ACA2;

  /* ── 意味色 ──────────────────────────────────── */
  --pr-warn: #A05A42;                      /* 締切・優先度: 高 */
  --pr-warn-bg: rgba(176, 104, 79, 0.14);  /* 締切バッジ面 */
  --pr-amber: #C49432;                     /* 琥珀(未読ドット・すぐ読む) */
  --pr-green: #659471;                     /* 緑(読了・翻訳済み✓) */
  --pr-green-check: #7E9C88;               /* 目次の翻訳済み✓グリフ(1a 実測。テーマ非依存) */

  /* ── 横断検索ヒット源バッジ(1e・4e) ───────────── */
  --pr-src-body-bg: var(--pr-acc-s);
  --pr-src-body-fg: var(--pr-acc);
  --pr-src-note-bg: rgba(101, 148, 113, 0.16);
  --pr-src-note-fg: #4C7458;
  --pr-src-chat-bg: rgba(110, 90, 126, 0.14);
  --pr-src-chat-fg: #6E5A7E;
  --pr-src-article-bg: #F1EFE9;
  --pr-src-article-fg: #777B81;

  /* ── 画面固有色(5a・1h・1d) ─────────────────── */
  --pr-youtube: #B3423A;                          /* YouTube アイコン面・再生ボタン(5a。白文字。テーマ非依存) */
  --pr-article-icon-bg: rgba(88, 132, 170, 0.18); /* 解説記事アイコン面(5a Zenn「Z」等) */
  --pr-article-icon-fg: #4A6E8E;                  /* 解説記事アイコン文字(5a) */
  --pr-official-fg: #4C7458;                      /* 「公式実装」バッジ文字(5a。面は --pr-src-note-bg と同値) */
  --pr-bg-locked-badge: #E4E1D7;                  /* 「自動挿入 · 削除不可」バッジ面(1h)・統計棒グラフ過去週(1d) */

  /* ── 影 ─────────────────────────────────────── */
  --pr-shadow-seg: 0 1px 2px rgba(28, 30, 34, 0.10);      /* セグメント選択 */
  --pr-shadow-float: 0 2px 6px rgba(28, 30, 34, 0.08);     /* 「対」ボタン */
  --pr-shadow-mono: 0 1px 4px rgba(28, 30, 34, 0.14);      /* サムネ上モノグラム */
  --pr-shadow-banner: 0 8px 24px rgba(28, 30, 34, 0.10);   /* 前回位置バナー */
  --pr-shadow-menu: 0 10px 28px rgba(20, 22, 26, 0.35);    /* 選択メニュー */
  --pr-shadow-bar: 0 16px 40px rgba(20, 22, 26, 0.35);     /* 一括操作バー・Toast */
  --pr-shadow-pop: 0 24px 56px rgba(28, 30, 34, 0.18);     /* ポップオーバー */
  --pr-shadow-modal: 0 32px 80px rgba(20, 22, 26, 0.35);   /* モーダル */
  --pr-shadow-frame: 0 20px 44px rgba(28, 30, 34, 0.12);   /* 1440フレーム(共有ページ埋め込み等) */

  /* ── モーダルスクリム(1g) ───────────────────── */
  --pr-scrim: rgba(30, 32, 36, 0.38);

  /* ── z-index 階層(§7.3) ─────────────────────── */
  --z-content: 0;
  --z-banner: 3;
  --z-selection-menu: 4;
  --z-inline-popover: 5;
  --z-floating-bar: 5;
  --z-dropdown: 6;
  --z-popover: 6;
  --z-toast: 7;
  --z-modal: 8;

  /* ── アクセント意味エイリアス(テーマで分岐) ────── */
  --pr-acc: var(--pr-a);
  --pr-acc-s: var(--pr-as);
  --pr-acc-m: var(--pr-am);
}

/* ══════════════ ダークモード(1c の実測値) ══════════════ */
html[data-theme="dark"] {
  --pr-bg-canvas: #14171B;
  --pr-bg-app: #181B20;
  --pr-bg-app-alt: #181B20;
  --pr-bg-pane: #1B1E24;
  --pr-bg-card: #1E2228;
  --pr-bg-pop: #22262D;
  --pr-bg-control: #22262D;
  --pr-bg-inset: #14171B;
  --pr-bg-muted: #14171B;
  --pr-bg-thumb: #191C21;
  --pr-bg-feed: #1B1E24;
  --pr-bg-hover: #22262D;
  --pr-bg-unread: #22262D;
  --pr-bg-knowledge: #22262D;
  --pr-bg-knowledge-label: #2C313A;
  --pr-bg-comment: #22262D;
  --pr-bg-seg-selected: #2C313A;

  --pr-text: #F0EEE9;           /* 1c 実測: 見出し・選択中の強文字 */
  --pr-text-body: #DEDCD7;
  --pr-text-en: #C4C7CC;
  --pr-text-mid: #C9CCD1;
  --pr-text-nav: #C9CCD1;
  --pr-text-sub: #9BA1A9;
  --pr-text-sub2: #9BA1A9;      /* 1c 実測 */
  --pr-text-muted: #7A7F87;
  --pr-text-icon: #7A7F87;
  --pr-text-faint: #5C626B;
  --pr-text-thumb: #5C626B;
  --pr-text-eq: #8A9099;

  --pr-border-frame: #101216;
  --pr-border-header: #2A2F37;
  --pr-border-pane: #2A2F37;
  --pr-border-soft: #262B32;
  --pr-border-card: #2A2F37;
  --pr-border-hair: #262B32;
  --pr-border-row: #262B32;
  --pr-border-control: #333942;
  --pr-border-pop: #3A404A;
  --pr-border-keycap: #333942;
  --pr-border-dashed: #333942;
  --pr-border-quote: #3A404A;
  --pr-border-check: #4A4E55;
  --pr-border-thumb: #2E343D;

  --pr-article-icon-fg: #7C9FBE;
  --pr-official-fg: #7FA98B;
  --pr-bg-locked-badge: #2C313A;

  --pr-shadow-pop: 0 22px 52px rgba(8, 10, 13, 0.55);
  --pr-shadow-frame: 0 20px 44px rgba(28, 30, 34, 0.20);

  /* アクセントはダーク対応色系へ切替(--pr-a 系の単純流用禁止。1c) */
  --pr-acc: var(--pr-ad);
  --pr-acc-s: var(--pr-ads);
  --pr-acc-m: var(--pr-adm);
}

/* テキスト選択色(Tweaks 準拠。accents.css がアクセント別に上書き) */
::selection { background: var(--pr-selection); }
```

### 2.2 ダーク側で本書が確定した対応値(決定一覧)

1c に描かれていない面のダーク値は以下のとおり決定した。理由: いずれも 1c に存在する近傍面(#22262D=浮き面 / #1B1E24=ペイン / #14171B=くぼみ)への写像であり、新色を発明しない。

| トークン | ライト | ダーク(決定) |
|---|---|---|
| `--pr-bg-app-alt` | #F4F3EF | #181B20(アプリ地に統合) |
| `--pr-bg-feed` | #FCFBF8 | #1B1E24 |
| `--pr-bg-hover` / `--pr-bg-unread` / `--pr-bg-knowledge` / `--pr-bg-comment` | 各値 | #22262D |
| `--pr-bg-knowledge-label` | #E7E4DA | #2C313A |
| `--pr-border-card` | #E2DFD5 | #2A2F37 |
| `--pr-border-check` | #C9C5BA | #4A4E55 |
| `--pr-bg-canvas` | #E8E6DF | #14171B |

ダーク文字トークンは 1c 実測に従う(決定ではなく実測): `--pr-text` は **#F0EEE9**(1c の見出し・選択中セグメント・選択中図表カードの強文字色。#E8E6E1 は 1c のフレーム基準色だが、テーマ非依存トークンとしては常時ダーク浮遊UIの `--pr-elev-fg` にのみ残る)、`--pr-text-sub2` は **#9BA1A9**(`--pr-text-sub` と同値になる)。本文用の追加トークン新設(--pr-text-strong 等)は行わず、1c に実在しない値は使わない。

注釈4色・ステータス6色(読んでいる除く)・警告色 `#A05A42`・琥珀 `#C49432`・緑 `#659471`・ヒット源バッジ色はテーマ非依存の固定値とする。決定。理由: 1c ではこれらの UI が描かれていないが、いずれも彩度低めの中間色でダーク地 #181B20 上でもコントラスト比 3:1 以上を満たし、色の意味の一貫性(F1)を優先する。

**規則(単一ソース): 特定画面でしか使わない色(画面固有色。例: `--pr-youtube` `--pr-article-icon-bg` `--pr-article-icon-fg` `--pr-official-fg` `--pr-bg-locked-badge`)も、すべて tokens.css にトークンとして収載する。コンポーネント内の色定数・hex 直書きは禁止**(§4.3 の ESLint ルールで CI 検出)。`--pr-article-icon-fg` のダーク値 #7C9FBE、`--pr-official-fg` のダーク値 #7FA98B、`--pr-bg-locked-badge` のダーク値 #2C313A は本書決定(近傍色への写像。新色を発明しない)。`--pr-youtube` #B3423A と `--pr-article-icon-bg` はテーマ非依存の固定値。

### 2.3 アクセント導出規則 — src/accent.ts(完全形)

Tweaks スクリプトの導出規則(_global.md)を JS 関数として固定する。透過率は **soft=0.10 / border=0.32 / dark-soft=0.14 / dark-border=0.40 / selection=0.22 / dark-selection=0.30(本書決定)** で、変更禁止。

```ts
// packages/tokens/src/accent.ts
export const ACCENTS = {
  slate:      { label: 'スレートブルー', light: '#3E5C76', dark: '#8FAECB' },
  green:      { label: '緑',            light: '#4A6B57', dark: '#96BBA3' },
  purple:     { label: '紫',            light: '#6E5A7E', dark: '#B3A1C4' },
  terracotta: { label: 'テラコッタ',    light: '#7A5C48', dark: '#C4A78F' },
} as const;

export type AccentKey = keyof typeof ACCENTS; // 'slate' | 'green' | 'purple' | 'terracotta'
export const DEFAULT_ACCENT: AccentKey = 'slate';

export function hexToRgb(hex: string): [number, number, number] {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) throw new Error(`invalid hex: ${hex}`);
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

const rgba = (rgb: [number, number, number], a: number) =>
  `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;

/** Tweaks スクリプトと同一の導出規則。テストで各値を固定化する。 */
export function accentVars(key: AccentKey): Record<string, string> {
  const { light, dark } = ACCENTS[key];
  const l = hexToRgb(light);
  const d = hexToRgb(dark);
  return {
    '--pr-a':   light,          // アクセント本体
    '--pr-as':  rgba(l, 0.10),  // 淡い容器背景
    '--pr-am':  rgba(l, 0.32),  // 枠線
    '--pr-ad':  dark,           // ダークモード用アクセント
    '--pr-ads': rgba(d, 0.14),
    '--pr-adm': rgba(d, 0.40),
    '--pr-selection': rgba(l, 0.22),       // ::selection(ライト)
    '--pr-selection-dark': rgba(d, 0.30),  // ::selection(ダーク)。決定: デザイン未定義のため 0.30 とする
  };
}
```

`scripts/gen-accents.ts` は 4 アクセント分の CSS を決定的に出力する(同一入力→バイト同一。CI で `pnpm gen` 後に git diff が空であることを検証する):

```ts
// packages/tokens/scripts/gen-accents.ts
import { writeFileSync } from 'node:fs';
import { ACCENTS, accentVars, type AccentKey, DEFAULT_ACCENT } from '../src/accent.ts';

let css = `/* 生成物: pnpm gen で再生成。手編集禁止 */\n`;
for (const key of Object.keys(ACCENTS) as AccentKey[]) {
  const vars = accentVars(key);
  const sel = key === DEFAULT_ACCENT
    ? `:root, html[data-accent="${key}"]`
    : `html[data-accent="${key}"]`;
  css += `${sel} {\n`;
  for (const [k, v] of Object.entries(vars)) css += `  ${k}: ${v};\n`;
  css += `}\n`;
}
css += `html[data-theme="dark"] { --pr-selection: var(--pr-selection-dark); }\n`;
writeFileSync(new URL('../css/accents.css', import.meta.url), css);
```

生成される `css/accents.css` の既定アクセント部(参考。全4ブロック生成):

```css
:root, html[data-accent="slate"] {
  --pr-a: #3E5C76;
  --pr-as: rgba(62,92,118,0.10);
  --pr-am: rgba(62,92,118,0.32);
  --pr-ad: #8FAECB;
  --pr-ads: rgba(143,174,203,0.14);
  --pr-adm: rgba(143,174,203,0.40);
  --pr-selection: rgba(62,92,118,0.22);
  --pr-selection-dark: rgba(143,174,203,0.30);
}
```

### 2.4 src/tokens.ts(TS定数エクスポート)

コンポーネントのロジック(例: SVG生成、拡張のバッジ色)から色値を参照するための定数。CSS と二重管理になるため、値は tokens.css と同一であることを vitest のスナップショットで検証する。

```ts
// packages/tokens/src/tokens.ts
export const STATUS_COLORS = {
  planned:  '#9AA0A6', // 読む予定
  up_next:  '#C49432', // すぐ読む
  reading:  'var(--pr-acc)', // 読んでいる(アクセント連動)
  done:     '#659471', // 読んだ
  reread:   '#8E7AA6', // あとで再読
  on_hold:  '#B0ACA2', // 保留
} as const;

export const STATUS_LABELS = {
  planned: '読む予定', up_next: 'すぐ読む', reading: '読んでいる',
  done: '読んだ', reread: 'あとで再読', on_hold: '保留',
} as const;

export const ANNOTATION_COLORS = {
  important: { fg: '#C49432', bg: 'rgba(196,148,50,0.26)', label: '重要' },
  question:  { fg: '#5884AA', bg: 'rgba(88,132,170,0.22)', label: '疑問' },
  idea:      { fg: '#659471', bg: 'rgba(101,148,113,0.22)', label: 'アイデア' },
  term:      { fg: '#82827E', bg: 'rgba(130,130,126,0.18)', label: '用語' },
} as const;

export const WARN_COLOR = '#A05A42';
export const WARN_BG = 'rgba(176,104,79,0.14)';
export const AMBER = '#C49432';
export const GREEN = '#659471';

export const Z_INDEX = {
  content: 0, banner: 3, selectionMenu: 4, inlinePopover: 5,
  floatingBar: 5, dropdown: 6, popover: 6, toast: 7, modal: 8,
} as const;

export const BASE_VIEWPORT = { width: 1440, height: 900 } as const;
export const MIN_APP_WIDTH = 1200; // §7.2 決定値
```

## 3. フォント

### 3.1 Google Fonts 読み込み(_global.md 指定そのまま)

`apps/web/src/app/layout.tsx` の `<head>` に以下を逐語で入れる。決定: `next/font` は使わない。理由: デザインが「実装で同一にする」と読み込みクエリを明示しており、セルフホスト変換でウェイト・光学サイズ軸の取り違えが起きる余地を残さないため。

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous" />
<link
  href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+JP:wght@400;500;600;700&family=Noto+Serif+JP:wght@400;500;600;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400;1,8..60,600&family=IBM+Plex+Mono:wght@400;500&display=swap"
  rel="stylesheet"
/>
```

### 3.2 css/fonts.css(完全形)

```css
/* @yakudoku/tokens — css/fonts.css */
:root {
  /* UI・見出し */
  --pr-font-ui: 'IBM Plex Sans JP', 'Hiragino Sans', 'Noto Sans JP', sans-serif;
  /* 訳文本文(既定=明朝)。設定「表示 > 本文書体」でゴシックへ切替 */
  --pr-jp: 'Noto Serif JP', 'Hiragino Mincho ProN', serif;
  /* 原文英語(デザイン逐語のフォールバック Georgia を維持) */
  --pr-font-en: 'Source Serif 4', Georgia, serif;
  /* コード・ID・キーキャップ */
  --pr-font-mono: 'IBM Plex Mono', ui-monospace, 'SFMono-Regular', monospace;
}

/* 本文書体切替(4f「表示」カテゴリ)。html[data-body-font] で制御 */
html[data-body-font="sans"] {
  --pr-jp: 'IBM Plex Sans JP', 'Hiragino Sans', 'Noto Sans JP', sans-serif;
}

html, body {
  font-family: var(--pr-font-ui);
  color: var(--pr-text);
  background: var(--pr-bg-app);
}
```

- `data-body-font` の値域: `"serif"`(既定・属性省略と同義)/ `"sans"`。
- 決定: フォールバックに Hiragino / Noto 系を追加する。理由: Google Fonts 到達不能時に和文がシステム明朝/ゴシックへ自然に落ちるため。第一候補ファミリ名はデザインと完全一致。

### 3.3 書体の使い分け(固定規則)

| 用途 | フォント変数 | 代表値(実測) |
|---|---|---|
| UI・見出し・カード・設定 | `--pr-font-ui` | 10〜16px、見出し 700 |
| 訳文本文 | `--pr-jp` | 対訳 14.8px/2.0、ゆったり 16.5px/2.15 |
| 原文英語・数式・英語キャプション | `--pr-font-en` | 13.8px/1.72(対訳)、italic 併用 |
| LaTeX・引用番号・キーキャップ(⌘K)・「t で開閉」 | `--pr-font-mono` | 9.5〜11px |

## 4. Tailwind CSS v4 テーマ設定

### 4.1 css/theme.css(完全形)

`@theme inline` を使い、ユーティリティが CSS 変数を参照する形にする(テーマ・アクセント切替に実行時追随させるため)。Tailwind 既定パレットは無効化し、トークン外の色の使用をビルドで不可能にする。

```css
/* @yakudoku/tokens — css/theme.css(Tailwind v4) */
@theme inline {
  /* 既定パレット・フォントを無効化(トークン外の値を禁止) */
  --color-*: initial;
  --font-*: initial;
  --shadow-*: initial;

  /* 背景・面 */
  --color-canvas: var(--pr-bg-canvas);
  --color-app: var(--pr-bg-app);
  --color-app-alt: var(--pr-bg-app-alt);
  --color-pane: var(--pr-bg-pane);
  --color-card: var(--pr-bg-card);
  --color-pop: var(--pr-bg-pop);
  --color-control: var(--pr-bg-control);
  --color-inset: var(--pr-bg-inset);
  --color-muted: var(--pr-bg-muted);
  --color-thumb: var(--pr-bg-thumb);
  --color-feed: var(--pr-bg-feed);
  --color-hover: var(--pr-bg-hover);
  --color-unread: var(--pr-bg-unread);
  --color-knowledge: var(--pr-bg-knowledge);
  --color-comment: var(--pr-bg-comment);
  --color-seg-selected: var(--pr-bg-seg-selected);
  --color-elev: var(--pr-elev-bg);
  --color-elev-fg: var(--pr-elev-fg);
  --color-elev-muted: var(--pr-elev-fg-muted);
  --color-elev-divider: var(--pr-elev-divider);

  /* 文字 */
  --color-ink: var(--pr-text);
  --color-ink-body: var(--pr-text-body);
  --color-ink-en: var(--pr-text-en);
  --color-ink-mid: var(--pr-text-mid);
  --color-ink-nav: var(--pr-text-nav);
  --color-ink-sub: var(--pr-text-sub);
  --color-ink-sub2: var(--pr-text-sub2);
  --color-ink-muted: var(--pr-text-muted);
  --color-ink-icon: var(--pr-text-icon);
  --color-ink-faint: var(--pr-text-faint);
  --color-ink-eq: var(--pr-text-eq);

  /* 境界 */
  --color-line-frame: var(--pr-border-frame);
  --color-line-header: var(--pr-border-header);
  --color-line-pane: var(--pr-border-pane);
  --color-line-soft: var(--pr-border-soft);
  --color-line-card: var(--pr-border-card);
  --color-line-hair: var(--pr-border-hair);
  --color-line-row: var(--pr-border-row);
  --color-line-control: var(--pr-border-control);
  --color-line-pop: var(--pr-border-pop);
  --color-line-keycap: var(--pr-border-keycap);
  --color-line-dashed: var(--pr-border-dashed);
  --color-line-quote: var(--pr-border-quote);
  --color-line-check: var(--pr-border-check);
  --color-line-thumb: var(--pr-border-thumb);

  /* アクセント(意味エイリアス経由。テーマ追随) */
  --color-accent: var(--pr-acc);
  --color-accent-soft: var(--pr-acc-s);
  --color-accent-border: var(--pr-acc-m);

  /* 注釈・ステータス・意味色 */
  --color-ann-important: var(--pr-ann-important);
  --color-ann-question: var(--pr-ann-question);
  --color-ann-idea: var(--pr-ann-idea);
  --color-ann-term: var(--pr-ann-term);
  --color-status-to-read: var(--pr-status-to-read);
  --color-status-read-next: var(--pr-status-read-next);
  --color-status-reading: var(--pr-status-reading);
  --color-status-done: var(--pr-status-done);
  --color-status-reread: var(--pr-status-reread);
  --color-status-on-hold: var(--pr-status-on-hold);
  --color-warn: var(--pr-warn);
  --color-amber: var(--pr-amber);
  --color-green: var(--pr-green);

  /* フォント */
  --font-ui: var(--pr-font-ui);
  --font-body: var(--pr-jp);
  --font-en: var(--pr-font-en);
  --font-mono: var(--pr-font-mono);

  /* 影 */
  --shadow-seg: var(--pr-shadow-seg);
  --shadow-float: var(--pr-shadow-float);
  --shadow-banner: var(--pr-shadow-banner);
  --shadow-menu: var(--pr-shadow-menu);
  --shadow-bar: var(--pr-shadow-bar);
  --shadow-pop: var(--pr-shadow-pop);
  --shadow-modal: var(--pr-shadow-modal);
}
```

### 4.2 apps/web/src/app/globals.css(取り込み順)

```css
@import 'tailwindcss';
@import '@yakudoku/tokens/css/tokens.css';
@import '@yakudoku/tokens/css/accents.css';
@import '@yakudoku/tokens/css/fonts.css';
@import '@yakudoku/tokens/css/theme.css';
```

### 4.3 数値ユーティリティの扱い(規則)

- デザインの寸法は 0.5px 刻み(11.5px 等)が多い。**フォントサイズ・高さ・パディングは Tailwind の任意値記法を使い、抽出値をそのまま書く**(例: `text-[11.5px] h-[24px] px-[9px] rounded-[999px]`)。spacing スケールへの丸めは禁止。
- 色は必ず §4.1 のトークンユーティリティ(`bg-card` `text-ink-sub` `border-line-control` 等)か CSS 変数の任意値(`bg-[var(--pr-ann-important-bg)]`)で書く。hex の直書きは ESLint ルール(`no-restricted-syntax` で `#[0-9A-Fa-f]{3,8}` を JSX の className/style 内で禁止)により CI で落とす。

## 5. 共通コンポーネント仕様(apps/web/src/components/ui/)

共通事項:

- 配置: `apps/web/src/components/ui/<Name>.tsx`。1ファイル1コンポーネント、named export。
- すべて `className` の追加合成(`clsx`)を許可するが、色・寸法の上書きは不可(トークン準拠を崩さない)。
- ダークモードはトークン参照により自動追随。個別分岐を書かない(常時ダーク UI の §5.6 FilterChip 選択・§5.20 Toast・選択メニューを除く)。
- インタラクティブ要素は `focus-visible` 時に `outline: 1.5px solid var(--pr-acc); outline-offset: 1px` を共通適用する。決定(デザイン未描画のキーボードフォーカス表現。SearchBox のフォーカスリング様式と統一)。

### 5.1 SegmentedControl

| 項目 | 内容 |
|---|---|
| 用途・使用画面 | 表示モード切替(1a/1b/1c/2a/1h ヘッダ、5値)、カード⇄テーブル(1e/4a)、目次/ページ(2a)、ステータス自動遷移3値(4f) |
| トラック | `background: var(--pr-bg-muted)`、border-radius:7px、padding:2px、gap:2px |
| セグメント寸法 | size=`sm`: h22px・padding 0 10px・font 11px / `md`(既定): h24px・padding 0 11px・font 11.5px / `lg`: h26px・padding 0 14px・font 11.5px。border-radius:5px |
| 選択中 | `background: var(--pr-bg-seg-selected)`、`color: var(--pr-text)`、font-weight:600、`box-shadow: var(--pr-shadow-seg)`(ダークでは shadow なし。1c 実測: 背景 #2C313A のみ。`html[data-theme=dark] & { box-shadow: none }`) |
| 非選択 | `color: var(--pr-text-sub)`(ダーク #9BA1A9=--pr-text-sub)、背景なし |
| a11y | `role="radiogroup"` + 各セグメント `role="radio"` `aria-checked`。矢印キーで移動 |

```ts
interface SegmentedControlProps<T extends string> {
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  size?: 'sm' | 'md' | 'lg'; // 既定 'md'
  ariaLabel: string;
}
```

### 5.2 StatusPill

| 項目 | 内容 |
|---|---|
| 使用画面 | ビューアヘッダ(1a/1b/1c/2a/1h)、ライブラリテーブル行(1e はピル無しのドット+ラベル)、カードフッタ(4a)、拡張(3a) |
| 寸法 `md` | inline-flex、gap:5px、h24px、padding 0 9px、border 1px `var(--pr-border-control)`、border-radius:999px、font 11.5px/500、bg `var(--pr-bg-control)`。ドット 7×7px 円 |
| 寸法 `sm` | h20px、padding 0 8px、font 10px、ドット 6×6px(4a カード) |
| ドット色 | `STATUS_COLORS[status]`(§2.4)。読んでいる=`var(--pr-acc)` |
| interactive | 末尾に「▾」(`color: var(--pr-text-muted)`、font 9px)。クリックで 6 値ドロップダウン(Popover §5.10、width 180px と決定)を開く |
| 変種 `dot-label` | ピル枠なしのドット+ラベル(flex gap:5px、font 11px)。1e テーブルのステータスセル |

```ts
import type { ReadingStatus } from '@yakudoku/api-client';
interface StatusPillProps {
  status: ReadingStatus; // 'planned'|'up_next'|'reading'|'done'|'reread'|'on_hold'(API 値。@yakudoku/api-client の型と一致)
  size?: 'md' | 'sm';
  variant?: 'pill' | 'dot-label'; // 既定 'pill'
  interactive?: boolean;          // true で ▾ 付きドロップダウン
  onChange?: (next: ReadingStatus) => void;
}
```

### 5.3 QualityBadge

| 項目 | 内容 |
|---|---|
| 使用画面 | ビューアヘッダ(18×18px)、ライブラリテーブル品質セル(17×17px)、拡張 3a、2a 情報パネル |
| A | bg `var(--pr-acc-s)`、color `var(--pr-acc)`、border-radius:4px、font 10.5px(sm は 10px)/700、中央揃え。`title="品質レベルA: LaTeXソースから完全構造化"` |
| B | bg `var(--pr-bg-inset)`、color `var(--pr-text-sub2)`。`title="品質レベルB: PDF由来"`(決定: B のツールチップ文言。A の様式に合わせた最短表現) |

```ts
interface QualityBadgeProps { level: 'A' | 'B'; size?: 18 | 17 } // px。既定 18
```

### 5.4 PriorityBadge

テキストのみのバッジ。font 11px。`high`: `color: var(--pr-warn)` weight 600 /「高」、`mid`: `color: var(--pr-text-sub2)` /「中」、`low`: `color: var(--pr-text-muted)` /「低」。`withPrefix=true` で「優先: 高」表記(4a カード。右端メタ位置では 9.5px)。使用画面: 1e テーブル、4a カード。1d はチップ形の画面固有 PriorityChip を使用(本コンポーネントは使わない)。

```ts
interface PriorityBadgeProps { priority: 'high' | 'mid' | 'low'; withPrefix?: boolean }
```

### 5.5 DeadlineBadge

| 変種 | スタイル | 使用箇所 |
|---|---|---|
| `chip` | inline-flex、h16px(サイドバー)/h17px(カード)、padding 0 6px、border-radius:3px、bg `var(--pr-warn-bg)`、color `var(--pr-warn)`、font 9.5px/600。ラベルは `withLabel=false`(既定)→「7/16」、`withLabel=true` →「締切 7/16」 | 4a サイドバー・カード、4b |
| `text` | font 11px、color `var(--pr-warn)`、weight 600。値なしは「—」`color: var(--pr-text-muted)` | 1e テーブル締切セル |

```ts
interface DeadlineBadgeProps {
  date: string | null;            // 'M/D' 表示文字列(整形は呼び出し側)
  variant?: 'chip' | 'text';      // 既定 'chip'
  withLabel?: boolean;            // true で「締切 」接頭辞
}
```

### 5.6 FilterChip

| 状態 | スタイル |
|---|---|
| 選択中 | h22px、padding 0 10px、border-radius:999px、**bg #26292E(`var(--pr-elev-bg)`。両テーマ共通)**、color #FFFFFF、font 11px/600、枠なし |
| 非選択 | 同寸法、border 1px `var(--pr-border-control)`、color `var(--pr-text-mid)`、bg `var(--pr-bg-control)` |
| 色ドット付き | 先頭に 7×7px 円(gap:4px)。1b 注釈フィルタは h20px・padding 0 8px・font 10.5px(size='sm') |
| 適用中(解除可能) | border 1px `var(--pr-acc-m)`、color `var(--pr-acc)`、bg `var(--pr-acc-s)`、weight 600、末尾「×」。例「タグ: distillation ×」 |

```ts
interface FilterChipProps {
  label: string;
  count?: number;
  selected?: boolean;
  dotColor?: string;              // 注釈色等。CSS変数文字列可
  removable?: boolean;            // true でアクセント適用中スタイル+×
  size?: 'md' | 'sm';             // md=h22/11px, sm=h20/10.5px
  onClick?: () => void;
  onRemove?: () => void;
}
```

使用画面: 1e/4a クイックフィルタ、1b 注釈フィルタ、4d 種別フィルタ、4e ヒット源フィルタ。

### 5.7 CountBadge

数値カウントの 3 変種を 1 コンポーネントに集約:

| variant | スタイル | 使用箇所 |
|---|---|---|
| `annotation` | min-width:15px、h15px、border-radius:8px、bg `var(--pr-ann-important-count-bg)`、color `var(--pr-ann-important-chip-fg)`、font 9.5px/600、中央揃え | 1a 目次の注釈数 |
| `tab` | 素のテキスト。font 10px、color `var(--pr-text-muted)`、margin-left:3px | サイドパネルタブ「注釈 6」 |
| `nav` | 素のテキスト。font 10.5px、color `var(--pr-text-muted)`(アクティブ項目内では継承=アクセント) | サイドバー件数「41」 |

```ts
interface CountBadgeProps { count: number; variant: 'annotation' | 'tab' | 'nav' }
```

### 5.8 Toggle

| 状態 | スタイル |
|---|---|
| ON | トラック 30×17px、border-radius:9px、bg `var(--pr-acc)`。ノブ 13×13px、border-radius:50%、bg #FFFFFF、position absolute **top:2px; right:2px**(4f 実測) |
| OFF | **決定**: トラック bg `var(--pr-border-check)`(ライト #C9C5BA / ダーク #4A4E55)、ノブ **top:2px; left:2px**。理由: OFF はデザイン未描画。チェックボックス未選択の枠色を無彩色トラックとして流用し、新色を発明しない |
| 遷移 | ノブ位置・トラック色とも `transition: 120ms ease-out` と決定 |
| disabled | opacity:0.5、pointer-events:none と決定 |
| a11y | `role="switch"` `aria-checked`。Space で切替 |

```ts
interface ToggleProps { checked: boolean; onChange: (next: boolean) => void; disabled?: boolean; ariaLabel: string }
```

使用画面: 4f(翻訳3行+計測1行)、4b「共有ページにメモを含める」、3a オプトイン。

### 5.9 Card

基本の白カード。bg `var(--pr-bg-card)`、border 1px `var(--pr-border-card)`、border-radius:10px、overflow:hidden。padding は既定なし(子が制御)。`padding` prop で `'md'`=14px 18px(設定カード)を提供。使用画面: 全画面のカード面(論文カード 4a、設定カード 4f、3行要約カード 1b は radius 10px+padding 16px 20px を呼び出し側指定)。

```ts
interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  padding?: 'none' | 'md'; // 既定 'none'
  as?: 'div' | 'section' | 'article';
}
```

### 5.10 Popover

| 項目 | 内容 |
|---|---|
| 面 | bg `var(--pr-bg-pop)`、border 1px `var(--pr-border-pop)`、border-radius:10px、`box-shadow: var(--pr-shadow-pop)`、overflow:hidden |
| キャレット | 9×9px、bg 同面色、border-left+border-top 1px 同枠色、`transform: rotate(45deg)`、top:-5px。水平位置は `caretOffset`(right または left からの px。通知=right:26px、図表参照=left:44px) |
| 実装 | `position: fixed` + アンカー矩形からの手動配置(Floating UI は使わず自前実装。placement は `bottom-start` / `bottom-end` の 2 種のみと決定。上方向・横方向の配置やビューポート衝突時の自動反転は実装しない) |
| z-index | `z-index: var(--z-popover)`。ただし横断検索ドロップダウン(1e)のみ `var(--z-dropdown)`(値は同じ 6) |
| 開閉 | 外側クリック・Esc で閉じる。開閉アニメーションなし(デザインに存在しないため付けない。決定) |

```ts
interface PopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement>;
  width: number;                  // 通知=352, 図表参照=400, 検索ドロップダウン=560, ステータス=180
  placement?: 'bottom-start' | 'bottom-end';
  caret?: boolean;                // 既定 true。検索ドロップダウンは false
  caretOffset?: { side: 'left' | 'right'; px: number };
  children: React.ReactNode;
}
```

既知インスタンス: 通知(4a: width 352、caret right 26px、アンカー=◷ボタン、ヘッダ行 padding 10px 14px + 「すべて既読にする」)、図表参照(1c: width 400、画像領域 height 170px、ボタン行 3 個 h23px)、横断検索ドロップダウン(1e: width 560、caret なし)。

### 5.11 Modal

| 項目 | 内容 |
|---|---|
| スクリム | `position: fixed; inset: 0; background: var(--pr-scrim)`(rgba(30,32,36,0.38))、`z-index: var(--z-modal)` |
| ダイアログ | 中央配置(top:50%/left:50%/translate(-50%,-50%))、width 460px(既定。1g 読了フロー)、bg `var(--pr-bg-card)`、border-radius:14px、`box-shadow: var(--pr-shadow-modal)`、overflow:hidden |
| 挙動 | Esc・スクリムクリックで `onClose`(`dismissible=false` で無効化可)。フォーカストラップ、開いた時に `initialFocusRef` 指定要素へ(省略時は最初のフォーカサブル要素)。背景スクロールロック |
| a11y | `role="dialog"` `aria-modal="true"` `aria-labelledby` |

```ts
interface ModalProps {
  open: boolean;
  onClose: () => void;
  width?: number;        // 既定 460
  dismissible?: boolean; // 既定 true
  labelledBy: string;
  initialFocusRef?: React.RefObject<HTMLElement>; // 開いた時のフォーカス先を明示指定(1g が使用)。省略時は最初のフォーカサブル要素
  children: React.ReactNode;
}
```

使用画面: 1g 読了フロー、図の「拡大」ライトボックス(1c。決定: width は画像に応じ最大 1080px)。

### 5.12 ProgressBar

height 3px(既定)、border-radius:2px、トラック `var(--pr-border-soft)`(#ECE9DF)、フィルは `color` prop: `'accent'`(既定)=`var(--pr-acc)`(読書進捗 4a/1d)/ `'green'`=`var(--pr-green)`(読了進捗 4b・翻訳完了系)。`value` は 0〜100 の数値、width:`${value}%`。範囲外は 0〜100 にクランプする(決定)。`role="progressbar"` + `aria-valuenow`(`aria-valuemin=0` / `aria-valuemax=100`)。`height` は既定 3px。`height=4` は 4b コレクションヘッダの読了進捗(w220px・緑)のみ(4b 実測)。

```ts
interface ProgressBarProps { value: number; color?: 'accent' | 'green'; height?: 3 | 4 }
```

### 5.13 SearchBox

| variant | スタイル |
|---|---|
| `global`(1e/4a トップバー) | w460px、h32px、border-radius:7px、padding 0 12px、font 12px。非フォーカス: bg `var(--pr-bg-inset)`、color `var(--pr-text-icon)`、キーキャップ「⌘K」(mono)。フォーカス: bg `var(--pr-bg-card)`、**border 1.5px `var(--pr-acc)`、`box-shadow: 0 0 0 3px var(--pr-acc-s)`**、右端ヒント「esc で閉じる」(font 10px、`var(--pr-text-muted)`) |
| `in-paper`(ビューアヘッダ) | w150px、h26px、border-radius:6px、padding 0 10px、bg `var(--pr-bg-inset)`、font 11.5px、キーキャップ「/」 |

- 先頭に MagnifierIcon(§6)12×12(in-paper は 11×11)、gap 8px(in-paper 6px)。
- キーキャップ共通: margin-left:auto、border 1px `var(--pr-border-keycap)`、border-radius:3px、padding 0 4px(⌘K は 0 5px)、font 9.5px、bg `var(--pr-bg-control)`。⌘K のみ `font-family: var(--pr-font-mono)`。
- ショートカット登録(`⌘K`/`Ctrl+K`、`/`)は呼び出し側(グローバルキーマップ)が行い、本コンポーネントは表示のみ。

```ts
interface SearchBoxProps {
  variant: 'global' | 'in-paper';
  value: string;
  onChange: (v: string) => void;
  onFocusChange?: (focused: boolean) => void;
  placeholder: string; // global: 'ライブラリ全体を検索 — 本文・訳文・メモ・チャット' / in-paper: 'この論文内を検索'
  shortcutLabel: '⌘K' | '/';
}
```

### 5.14 SidebarNav

w216px、bg `var(--pr-bg-pane)`、border-right 1px `var(--pr-border-pane)`、padding 12px 10px、縦 flex gap:2px、font 12.5px、color `var(--pr-text-nav)`。

- 項目: padding 7px 10px(コレクション・保存フィルタは 6px 10px)、border-radius:6px。アクティブ: bg `var(--pr-acc-s)`、color `var(--pr-acc)`、weight 600。件数は CountBadge `nav`、締切は DeadlineBadge `chip`。
- セクション見出し: font 10.5px/600、color `var(--pr-text-muted)`、letter-spacing:0.4px、padding 14px 10px 4px。
- フッタ: 「設定 · エクスポート」padding 6px 10px + padding-top:12px、color `var(--pr-text-sub2)`、font 11.5px、border-top 1px `var(--pr-border-pane)`、margin-top:8px。

```ts
interface SidebarNavItem {
  id: string;
  label: string;
  href: string;
  count?: number;
  deadline?: string | null; // 'M/D'
  active?: boolean;
}
interface SidebarNavProps {
  main: SidebarNavItem[];                       // ホーム/ライブラリ/語彙帳
  sections: Array<{ heading: string; items: SidebarNavItem[] }>; // コレクション/保存フィルタ
  footer?: React.ReactNode;
}
```

使用画面: 1d/1e/4a/4d(4e は SearchFacetRail を LibraryShell の sidebar prop で差し込むため本コンポーネントは使わない)。管理系画面の共通シェル LibraryShell(`components/shell/LibraryShell.tsx`)は `sidebar` prop を持ち、既定で SidebarNav を、4e では SearchFacetRail を受け取る。4f の設定カテゴリナビは同スタイルの縮退版(items のみ)として本コンポーネントを `sections=[]` で再利用する。

### 5.15 Table(LibraryTable)

汎用テーブルは作らず、ライブラリ専用 `LibraryTable` を共通化する(10 列固定がデザイン仕様のため)。

- コンテナ: Card 面(bg card、border 1px `var(--pr-border-card)`、radius 10px、overflow:hidden)、縦 flex、最下段に flex:1 スペーサ。
- グリッド(ヘッダ・行共通): `grid-template-columns: 34px 1fr 108px 44px 168px 64px 66px 76px 64px 64px`、align-items:center、gap:8px、padding 8px 14px。
- ヘッダ行: border-bottom 1px `var(--pr-border-soft)`、font 10.5px/600、color `var(--pr-text-muted)`。ソート中の列はラベル末尾に「↑」/「↓」。
- 行: border-bottom 1px `var(--pr-border-row)`(最終行なし)。選択中行 bg `var(--pr-acc-s)`。ホバー行 bg `var(--pr-bg-hover)`(決定)。
- チェックボックス: 14×14px、border 1.5px `var(--pr-border-check)`、radius 3px。チェック済: bg `var(--pr-acc)`、白「✓」9px。
- セル書式は 1e §2.6 の実測値に完全準拠(タイトル 12px/600 ellipsis、著者行 10px muted、サムネ 26×34px 等)。

```ts
interface LibraryTableRow {
  id: string;
  title: string;
  titleBadge?: 'pdf_import';     // 「PDF 取り込み」インラインバッジ
  authorsLine: string;           // 'Liu, Gong, Liu · ICLR 2023 · arXiv:2209.03003'
  thumbnailUrl: string | null;
  status: ReadingStatus;
  quality: 'A' | 'B';
  tags: string[];
  priority: 'high' | 'mid' | 'low' | null;
  deadline: string | null;
  readingHours: number | null;   // 3.2 → '3.2h'
  comprehension: number | null;  // 1..5 → '4/5'
  addedAt: string;               // 'M/D'
}
type SortKey = 'title' | 'status' | 'quality' | 'priority' | 'deadline'
  | 'reading_time' | 'comprehension' | 'added_at' | 'updated_at';
interface LibraryTableProps {
  rows: LibraryTableRow[];
  selectedIds: ReadonlySet<string>;
  onToggleSelect: (id: string) => void;
  onToggleSelectAll: () => void;
  sort: { key: SortKey; dir: 'asc' | 'desc' };
  onSortChange: (s: { key: SortKey; dir: 'asc' | 'desc' }) => void;
  onOpenRow: (id: string) => void;
}
```

### 5.16 SidePanelTabs

排他 6 タブ。タブ行: flex、border-bottom 1px `var(--pr-border-soft)`、padding 0 6px。各タブ: padding 10px 9px 8px、font 12px。アクティブ: weight 600、color `var(--pr-acc)`、**`box-shadow: inset 0 -2px var(--pr-acc)`**(inset 下線)。非アクティブ: color `var(--pr-text-sub2)`。件数は CountBadge `tab`。

```ts
type SidePanelTabId = 'chat' | 'notes' | 'annotations' | 'figures' | 'resources' | 'info';
interface SidePanelTabsProps {
  active: SidePanelTabId;
  counts: Partial<Record<SidePanelTabId, number>>; // 描画例: annotations=6, resources=4
  onChange: (tab: SidePanelTabId) => void;
}
```

タブラベル(固定): チャット / メモ / 注釈 / 図表 / リソース / 情報。使用画面: 1a/1b/1c/2a/5a(1h でも開閉可能。docs/04 §7)。

### 5.17 HighlightMark

本文ハイライトの `<mark>`。`background: var(--pr-ann-<color>-bg)`、border-radius:2px、padding 0 1px。`annotationNumber` があれば直後に丸数字チップ: 14×14px、border-radius:50%、bg `var(--pr-ann-important-chip-bg)`(色種別ごとに同透過 0.30 の chip-bg を使用。question/idea/term の chip-bg・chip-fg は本書で決定: bg=各色 rgba 0.30、fg は important=#8A6A24 に倣い各色の暗色 #4A6E8E(question。1h 由来バッジ・記事アイコン文字 `--pr-article-icon-fg` と同一値。#3D5F80 は廃止)/#47694F(idea)/#5C5C59(term))、font 9px/700、vertical-align:4px、margin-left:2px。

```ts
interface HighlightMarkProps {
  color: 'important' | 'question' | 'idea' | 'term';
  annotationNumber?: number;
  onClickAnnotation?: () => void; // 注釈タブ該当カードへ
  children: React.ReactNode;
}
```

使用画面: 1a/1b/1c 本文、4e 検索スニペット(検索ヒットは `color: 'important'` 相当の bg rgba(196,148,50,0.30) 固定 `<mark>` で、本コンポーネントの `variant='search-hit'` を追加せず素の mark クラス `.yk-search-hit` として `apps/web/src/app/globals.css` に定義する。決定)。

### 5.18 EvidenceChip(根拠チップ)

inline-flex、h16px(本文インライン)/h17px(メッセージヘッダ)、padding 0 6px、border 1px `var(--pr-acc-m)`、color `var(--pr-acc)`、bg `var(--pr-acc-s)`、border-radius:4px、font 9.5px(インライン)/10px(ヘッダ)、weight 600、vertical-align:2px、連続時 margin-left:3px。クリックで本文該当アンカーへジャンプ+強調(docs/05)。

```ts
type EvidenceAnchor =
  | { type: 'section'; sectionNumber: string }            // '§2.1'
  | { type: 'paragraph'; sectionNumber: string; para: number } // '§2.1 ¶4'
  | { type: 'equation'; eqNumber: number }                 // '式(5)'
  | { type: 'figure'; figNumber: number }                  // '図2'
  | { type: 'table'; tableNumber: number };                // '表1'
interface EvidenceChipProps {
  anchor: EvidenceAnchor;
  label: string;          // 表示文字列は生成側が確定(例 '式(5) · §2.1')
  size?: 'inline' | 'header';
  onJump: (anchor: EvidenceAnchor) => void;
}
```

使用画面: 1a チャット、1h 記事モード(§/表 チップ)、5a メモ内 § 参照チップ。

### 5.19 AIBadge / AiMark

- `AiMark`: アクセント色の「✦」1 文字スパン(`color: var(--pr-acc)`)。AI 生成要素の接頭辞(要約カード、提案通知、ボタン)。
- `AIBadge`: 3 変種。
  - `generated`「AI生成」: h15px、padding 0 5px、border 1px `var(--pr-border-control)`、border-radius:3px、font 9px/600、color `var(--pr-text-icon)`。
  - `external`「論文外の知識」: h15px、padding 0 5px、bg `var(--pr-bg-knowledge-label)`、border-radius:3px、font 9px/700、color `var(--pr-text-eq)`、枠なし。
  - `guess`「推測」: `external` と同スタイル(決定: デザイン未描画。免責文で言及される第3ラベルのため external と同格の見た目とする)。

```ts
interface AIBadgeProps { variant: 'generated' | 'external' | 'guess' }
```

### 5.20 Toast

デザイン未描画のため本書で確定する。決定: 一括操作バー(1e)の視覚言語を流用する。

- 位置: `position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%)`、`z-index: var(--z-toast)`。BulkActionBar 表示中は bottom: 74px(バー高さ 42px + gap 10px)へ退避。
- 面: bg `var(--pr-elev-bg)`、color `var(--pr-elev-fg)`、border-radius:10px、padding 10px 18px、`box-shadow: var(--pr-shadow-bar)`、font 12px。複数行は縦 flex gap:4px。
- 種別接頭辞: `success`=「✓ 」(color `var(--pr-green)`)、`error`=「× 」(color `var(--pr-warn)`)、`info`=接頭辞なし。
- アクションリンク: margin-left:14px、color `var(--pr-elev-fg)`、weight 600、下線なし(例「元に戻す」)。
- 表示時間: 4000ms 自動消滅(action 付きは 6000ms)。同時 1 件のみ(後着優先で置換)。`aria-live="polite"`。

```ts
interface ToastOptions {
  kind: 'info' | 'success' | 'error';
  message: string;
  action?: { label: string; onClick: () => void };
}
// 呼び出しは useToast() フック(Zustand ストア yk-toast)経由: toast({ kind, message, action })
```

### 5.21 EmptyState

デザインの空状態文言(「未配置 0 件」等)は各画面固有だが、リスト空表示の共通枠を確定する。決定:

- 中央寄せ縦 flex、padding 32px 16px、gap 6px。
- タイトル: font 12.5px/600、color `var(--pr-text-sub2)`。
- 説明: font 11px、color `var(--pr-text-muted)`、line-height 1.6、text-align:center。
- アクション(任意): h26px、padding 0 12px、border 1px `var(--pr-border-control)`、border-radius:6px、font 11px、color `var(--pr-text-mid)`、bg `var(--pr-bg-control)`。
- アイコン・イラストは使わない(デザインの禁欲的トーン維持)。

```ts
interface EmptyStateProps { title: string; description?: string; action?: { label: string; onClick: () => void } }
```

### 5.22 その他の共通片(コンポーネント化するもの)

| 名前 | 仕様 | 使用画面 |
|---|---|---|
| `Keycap` | border 1px `var(--pr-border-keycap)`、radius 3px、padding 0 4px、font 9.5px、bg `var(--pr-bg-control)`。mono オプション | SearchBox、対訳ポップ「t で開閉」 |
| `BulkActionBar` | fixed bottom:22px 中央、bg `var(--pr-elev-bg)`、color `var(--pr-elev-fg)`、radius 10px、padding 10px 18px、shadow bar、z `var(--z-floating-bar)`。区切り縦線 1×16px `var(--pr-elev-divider)` | 1e |
| `SelectionMenu` | flex、gap:2px、bg `var(--pr-elev-bg)`、radius 8px、padding 5px 7px、shadow menu、z `var(--z-selection-menu)`。色ドット 4 個(15×15px 円、margin 0 2px、注釈4色)+縦線+テキストアクション(font 11.5px、color `var(--pr-elev-fg)`、padding 0 6px): コメント / ✦ AIに質問 / 語彙に追加 / コピー | 1b・1a・1h |
| `ResumeBanner` | 中央上部 absolute top:14px、ピル形(radius 999px)、bg card、border 1px `var(--pr-border-control)`、padding 7px 8px 7px 16px、shadow banner、z `var(--z-banner)`。CTA「続きから ↓」h24px bg `var(--pr-acc)` 白 | 1b・1d |
| `TagChip` | h17px、padding 0 6px、radius 3px、bg `var(--pr-bg-inset)`、color `var(--pr-text-sub)`、font 10px(カードは 9.5px) | 1e/4a/3a |
| `SourceBadge` | 検索ヒット源: h16px、padding 0 6px、radius 3px、font 9.5px/700。body/note/chat/article の 4 種(色は `--pr-src-*`) | 1e/4e |

## 6. アイコン

### 6.1 SVG アイコン(インライン React コンポーネント)

配置: `apps/web/src/components/icons/index.tsx`。すべて `stroke/fill: currentColor`、`size` prop(px)で `width/height` を指定、`viewBox` 固定。デザイン抽出の path を逐語で使う。

```tsx
// apps/web/src/components/icons/index.tsx
import type { SVGProps } from 'react';
type IconProps = SVGProps<SVGSVGElement> & { size?: number };

/** 虫眼鏡(1a/1b/1e/4a)。viewBox 0 0 12 12。ヘッダ内 11px・グローバル検索 12px */
export function MagnifierIcon({ size = 12, ...rest }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="none" {...rest}>
      <circle cx="5" cy="5" r="3.6" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 8l2.6 2.6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

/** しおり(1a 目次・1b レール・1c)。viewBox 0 0 10 12。
    決定: 目次内(1a/1c)= size=11(9×11)、1b 注釈レール = size=12(10×12)。抽出実測どおり */
export function BookmarkIcon({ size = 12, ...rest }: IconProps) {
  const w = Math.round((size * 10) / 12);
  return (
    <svg width={w} height={size} viewBox="0 0 10 12" {...rest}>
      <path d="M1 1h8v10L5 8.5 1 11V1z" fill="currentColor" />
    </svg>
  );
}
```

### 6.2 テキストグリフ(SVG 化しない)

確定デザインはアイコンの大半をテキスト文字で表現している。**決定: 以下はフォントグリフのまま実装し、アイコンフォント・SVG 置換をしない**(1px 単位でデザインと一致させる最短経路のため)。

| グリフ | 意味 | 代表スタイル |
|---|---|---|
| `‹` `›` | 戻る・ページ送り | 16px、`var(--pr-text-icon)` |
| `▾` | ドロップダウン | 8.5〜9px、`var(--pr-text-muted)` |
| `⋯` | オーバーフローメニュー | 15px、`var(--pr-text-sub)`、letter-spacing:1px |
| `☰` | 目次展開 | 13px、`var(--pr-text-sub)` |
| `◷` | 通知 | 13px、`var(--pr-text-sub)`(1d 実測 #5B6067)。未読ドットは別要素: 6×6px 円、絶対配置 top:5px/right:5px、bg `var(--pr-amber)`(§5.10 アンカー) |
| `✦` | AI 生成マーク | `var(--pr-acc)` |
| `⟨⟨` | ペイン折りたたみ | `var(--pr-text-faint)` |
| `⇄` | 段落対応 | `var(--pr-text-faint)` |
| `↑` | 送信・メモに保存 | 送信ボタン 12px 白 |
| `⤓` | ダウンロード/エクスポート | `var(--pr-acc)` |
| `✓` | 完了・翻訳済み | 目次の翻訳済み=10px `var(--pr-green-check)`。チェックボックス内=9px 白(§5.15)。Toast success 接頭辞=`var(--pr-green)`(§5.20)。この 3 用法以外で使わない |
| `×` | 閉じる・解除 | 12px、`var(--pr-text-muted)` |
| `●` `○` | ラジオ選択(4f) | 選択色/枠色 |
| `💬` | 注釈コメント | 絵文字のまま |

### 6.3 追加方針

新規アイコンが必要になった場合は (1) まずテキストグリフ候補を検討、(2) SVG化する場合は 12×12 viewBox・stroke-width 1.3・currentColor に統一する。ライブラリ(lucide 等)は導入しない。決定。

## 7. レイアウト基準

### 7.1 基準ビューポート

- デスクトップ基準 **1440×900px**(docs/09 §6)。全画面の寸法・余白は 1440px 幅での実測値を正とする。
- デザインの「フレーム」(border 1px #D6D3C9、radius 10px、shadow)はデザインキャンバス上の表現であり、実アプリではビューポート全面に描画する(frame 用トークンは共有ページのカード等に転用)。

### 7.2 コンテンツ幅の規則(決定)

- **1440px 超**: 固定幅ペイン(サイドバー 216px、目次 232px、サイドパネル 320/340px)は固定のまま、中央ペイン(flex:1)が広がる。中央ペイン内の読み物カラム(訳文 720px、設定 720px)は中央寄せを維持し、背景面が左右に拡張される。ライブラリのカードグリッドは `1fr 1fr 1fr` の 3 列固定でカードが広がる(列数は増やさない)。テーブルは `1fr` の論文列が広がる。
- **1440px 未満**: 固定幅ペインは固定のまま、中央ペインがフルードに縮小する。中央ペインの最小幅は 560px。
- **デスクトップは min-width 1200px**(`MIN_APP_WIDTH`)。`body { min-width: 1200px }` とし、それ未満のウィンドウでは水平スクロールを許容する。**<768px のモバイル縮退レイアウト(対象=閲覧+ステータス変更のみ)は plans/09-screens/mobile.md が所有(M1 実装)**。本書はデスクトップレイアウトのみを規定する。
- 高さ: ヘッダ/トップバー 52px 固定、本体 `flex:1; min-height:0` で内部スクロール。900px はあくまで基準であり縦は常にフルード。

### 7.3 z-index 階層表(確定)

デザイン実測値をそのまま階層として採用し、CSS 変数(§2.1)で参照する。直値の記述は禁止。

| 層 | 変数 | 値 | 用途(実測元) |
|---|---|---|---|
| 基層 | `--z-content` | 0 | 本文・パネル |
| バナー | `--z-banner` | 3 | 前回位置バナー(1b) |
| 選択メニュー | `--z-selection-menu` | 4 | ダーク選択ツールバー(1b) |
| インラインポップ | `--z-inline-popover` | 5 | 図表参照ポップオーバー(1c) |
| 浮遊バー | `--z-floating-bar` | 5 | 一括操作バー(1e) |
| ドロップダウン | `--z-dropdown` | 6 | 検索ドロップダウン(1e) |
| ポップオーバー | `--z-popover` | 6 | 通知(4a)・ステータス切替 |
| トースト | `--z-toast` | 7 | Toast(決定) |
| モーダル | `--z-modal` | 8 | 読了フロー(1g)・拡大表示 |

拡張機能(3a)のページ内「訳 保存」ピルはホストページと競合するため `z-index: 2147483000` と決定(コンテンツスクリプト内のみ。アプリ内階層とは独立)。

## 8. ダークモード・テーマ切替の適用方式

### 8.1 属性駆動

`<html>` の 3 属性で全テーマ状態を表現する:

| 属性 | 値域 | 既定 | 対応トークン |
|---|---|---|---|
| `data-theme` | `light` / `dark` | `light` | §2.1 のダークブロック |
| `data-accent` | `slate` / `green` / `purple` / `terracotta` | `slate` | §2.3 accents.css |
| `data-body-font` | `serif` / `sans` | `serif` | §3.2 `--pr-jp` |

設定画面(4f「表示」)の選択肢は テーマ=ライト/ダーク/システム、アクセント=4色、本文書体=明朝/ゴシック。「システム」は保存値であり、`data-theme` へは解決後の `light`/`dark` を書く。

### 8.2 FOUC 防止と永続化(決定)

- 永続化は Cookie `yk_theme`(値: `light|dark|system`)、`yk_accent`、`yk_font` の 3 つ。`SameSite=Lax`、有効期限 365 日。ログインユーザーは UserSettings(DB)が正で、ログイン時にサーバー値で Cookie を上書きする。
- SSR: Next.js の root layout が Cookie を読み、`<html data-theme=... data-accent=... data-body-font=...>` を初期レンダリングに埋め込む(ちらつきなし)。
- `system` の解決: `<head>` 先頭のインラインスクリプト(3 行)が `matchMedia('(prefers-color-scheme: dark)')` を評価して `data-theme` を上書きし、`change` イベントで追随する。
- 切替 UI はどの画面でも 1 クリック反映(属性書き換えのみ。リロード不要)。トランジションアニメーションは付けない。

### 8.3 コンポーネント側の規約

- 色は必ずトークン経由。`data-theme` での条件分岐・`dark:` バリアントの使用は原則禁止(トークンが吸収する)。
- 例外は §5.1 セグメント選択 shadow(ダークで none)のみ `html[data-theme=dark]` セレクタを許可。
- 常時ダーク UI(SelectionMenu / BulkActionBar / FilterChip 選択中 / Toast)は `--pr-elev-*` を使い、テーマで変化しない。

## 9. 品質保証(受け入れ基準)

- [ ] `accentVars('slate')` の出力が §2.3 の CSS 例とバイト一致する(vitest スナップショット)。4 色すべてで soft=0.10 / border=0.32 / dark-soft=0.14 / dark-border=0.40 を検証
- [ ] `pnpm gen` 再実行で `css/accents.css` に diff が出ない(CI)
- [ ] tokens.css の全 hex が本書 §2 の表と一致する(docs/09 §8「実装の CSS トークン値が確定デザインと一致」)
- [ ] Storybook(apps/web に併設)に §5 の全コンポーネント × 全状態(ON/OFF・選択/非選択・ライト/ダーク・アクセント4色)のストーリーがあり、Playwright `toHaveScreenshot`(plans/12 の `visual` プロジェクト・Docker イメージ内実行)でピクセル回帰を検出する
- [ ] Tailwind ビルドで既定パレット色(`bg-red-500` 等)が使用不能であること
- [ ] `::selection` がアクセント切替に追随する(4 色 × 2 テーマ)
- [ ] WCAG 2.1 AA: `--pr-text-sub` 以上の本文系テキストがそれぞれの地に対しコントラスト比 4.5:1 以上(自動チェックを CI に含める)

## 10. 実装順序

1. packages/tokens(tokens.css → accent.ts + gen → fonts.css → theme.css → tokens.ts → テスト)
2. apps/web に globals.css 取り込み+layout.tsx のフォントタグ・テーマ属性 SSR
3. icons(2 SVG + グリフ規約)
4. ui コンポーネントをレイヤ順に: Card / Keycap / CountBadge / TagChip → SegmentedControl / Toggle / FilterChip / StatusPill / QualityBadge / PriorityBadge / DeadlineBadge / ProgressBar → SearchBox / SidebarNav / SidePanelTabs → Popover / Modal / Toast / EmptyState → HighlightMark / EvidenceChip / AIBadge → LibraryTable / BulkActionBar / SelectionMenu / ResumeBanner / SourceBadge
5. Storybook + VRT 整備(§9)
