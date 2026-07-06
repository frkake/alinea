// packages/tokens/src/tokens.ts
// コンポーネントのロジック(SVG生成・拡張のバッジ色)から色値を参照する定数。
// 値は tokens.css と同一であることを vitest のスナップショットで検証する(plans/08 §2.4)。
export const STATUS_COLORS = {
  planned: "#9AA0A6", // 読む予定
  up_next: "#C49432", // すぐ読む
  reading: "var(--pr-acc)", // 読んでいる(アクセント連動)
  done: "#659471", // 読んだ
  reread: "#8E7AA6", // あとで再読
  on_hold: "#B0ACA2", // 保留
} as const;

export const STATUS_LABELS = {
  planned: "読む予定",
  up_next: "すぐ読む",
  reading: "読んでいる",
  done: "読んだ",
  reread: "あとで再読",
  on_hold: "保留",
} as const;

export const ANNOTATION_COLORS = {
  important: { fg: "#C49432", bg: "rgba(196,148,50,0.26)", label: "重要" },
  question: { fg: "#5884AA", bg: "rgba(88,132,170,0.22)", label: "疑問" },
  idea: { fg: "#659471", bg: "rgba(101,148,113,0.22)", label: "アイデア" },
  term: { fg: "#82827E", bg: "rgba(130,130,126,0.18)", label: "用語" },
} as const;

export const WARN_COLOR = "#A05A42";
export const WARN_BG = "rgba(176,104,79,0.14)";
export const AMBER = "#C49432";
export const GREEN = "#659471";

export const Z_INDEX = {
  content: 0,
  banner: 3,
  selectionMenu: 4,
  inlinePopover: 5,
  floatingBar: 5,
  dropdown: 6,
  popover: 6,
  toast: 7,
  modal: 8,
} as const;

export const BASE_VIEWPORT = { width: 1440, height: 900 } as const;
export const MIN_APP_WIDTH = 1200; // §7.2 決定値

export type ReadingStatus = keyof typeof STATUS_COLORS;
export type AnnotationColor = keyof typeof ANNOTATION_COLORS;
