import type { SVGProps } from "react";

/** SVG アイコン基盤(plans/08 §6.1)。stroke/fill: currentColor、size=px。 */
export type IconProps = SVGProps<SVGSVGElement> & { size?: number };

/** 虫眼鏡(1a/1b/1e/4a)。viewBox 0 0 12 12。ヘッダ内 11px・グローバル検索 12px。 */
export function MagnifierIcon({ size = 12, ...rest }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="none" aria-hidden="true" {...rest}>
      <circle cx="5" cy="5" r="3.6" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 8l2.6 2.6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

/** しおり(1a 目次・1b レール・1c)。viewBox 0 0 10 12。
    目次内(1a/1c)= size=11(9×11)、1b 注釈レール = size=12(10×12)。 */
export function BookmarkIcon({ size = 12, ...rest }: IconProps) {
  const w = Math.round((size * 10) / 12);
  return (
    <svg width={w} height={size} viewBox="0 0 10 12" fill="none" aria-hidden="true" {...rest}>
      <path d="M1 1h8v10L5 8.5 1 11V1z" fill="currentColor" />
    </svg>
  );
}
