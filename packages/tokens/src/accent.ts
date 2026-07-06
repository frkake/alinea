// packages/tokens/src/accent.ts
// アクセント導出規則(plans/08 §2.3 の逐語)。透過率は変更禁止:
// soft=0.10 / border=0.32 / dark-soft=0.14 / dark-border=0.40 / selection=0.22 / dark-selection=0.30
export const ACCENTS = {
  slate: { label: "スレートブルー", light: "#3E5C76", dark: "#8FAECB" },
  green: { label: "緑", light: "#4A6B57", dark: "#96BBA3" },
  purple: { label: "紫", light: "#6E5A7E", dark: "#B3A1C4" },
  terracotta: { label: "テラコッタ", light: "#7A5C48", dark: "#C4A78F" },
} as const;

export type AccentKey = keyof typeof ACCENTS; // 'slate' | 'green' | 'purple' | 'terracotta'
export const DEFAULT_ACCENT: AccentKey = "slate";

export function hexToRgb(hex: string): [number, number, number] {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) throw new Error(`invalid hex: ${hex}`);
  const n = parseInt(m[1]!, 16);
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
    "--pr-a": light, // アクセント本体
    "--pr-as": rgba(l, 0.1), // 淡い容器背景
    "--pr-am": rgba(l, 0.32), // 枠線
    "--pr-ad": dark, // ダークモード用アクセント
    "--pr-ads": rgba(d, 0.14),
    "--pr-adm": rgba(d, 0.4),
    "--pr-selection": rgba(l, 0.22), // ::selection(ライト)
    "--pr-selection-dark": rgba(d, 0.3), // ::selection(ダーク)。決定: 0.30
  };
}
