import type { CSSProperties, ReactNode } from "react";

/**
 * 共有ページ(4c)専用のテーマ固定スコープ。
 *
 * 4c は匿名閲覧者向けの唯一の公開画面で、テーマ切替 UI を持たず常にライト+アクセント
 * 「slate」で表示する(決定。plans/09-screens/4c §1「匿名閲覧者はユーザー設定を持たない
 * ため `<html data-theme="light" data-accent="slate">` 固定」)。ルートレイアウトの
 * `<html>` 要素は Cookie/OS 設定から動的に data-theme/data-accent を決めるため(所有外)、
 * この階層で該当トークンを light+slate の値に固定して上書きする(CSS カスタムプロパティは
 * DOM の子孫方向に再宣言できるため、祖先の `<html>` の値に関わらずこの配下で確定する)。
 *
 * 値の出典: packages/tokens/css/tokens.css の `:root` ブロック(ライト)・
 * packages/tokens/css/accents.css の `:root, html[data-accent="slate"]` ブロック。
 */
const LIGHT_SLATE_VARS: Record<string, string> = {
  "--pr-bg-card": "#ffffff",
  "--pr-border-header": "#e6e3da",
  "--pr-acc": "#3E5C76",
  "--pr-acc-s": "rgba(62,92,118,0.10)",
  "--pr-acc-m": "rgba(62,92,118,0.32)",
  "--pr-bg-inset": "#f1efe9",
  "--pr-text": "#1e2227",
  "--pr-text-sub": "#5b6067",
  "--pr-text-sub2": "#777b81",
  "--pr-text-muted": "#9a9ea4",
  "--pr-text-mid": "#3c4046",
  "--pr-warn": "#a05a42",
  "--pr-warn-bg": "rgba(176,104,79,0.14)",
  "--pr-border-control": "#ddd9cf",
  "--pr-bg-control": "#ffffff",
  "--pr-bg-hover": "#faf9f5",
  "--pr-src-note-bg": "rgba(101,148,113,0.16)",
  "--pr-src-note-fg": "#4c7458",
  "--pr-elev-bg": "#26292e",
};

export function ShareThemeScope({ children }: { children: ReactNode }) {
  return (
    <div style={{ ...(LIGHT_SLATE_VARS as CSSProperties), minHeight: "100%" }}>{children}</div>
  );
}
