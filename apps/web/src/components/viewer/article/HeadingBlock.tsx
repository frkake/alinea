import type { HeadingContentOut } from "@alinea/api-client";

/** 見出しブロック(1h §4.7)。level 2 = 19px、level 3 = 15.5px(デザイン未描画分の決定)。 */
export function HeadingBlock({ heading }: { heading: HeadingContentOut }) {
  const level3 = heading.level === 3;
  return (
    <div
      style={{
        fontSize: level3 ? 15.5 : 19,
        fontWeight: 700,
        marginBottom: level3 ? 6 : 8,
        fontFamily: "var(--pr-font-ui)",
        color: "var(--pr-text)",
      }}
    >
      {heading.text}
    </div>
  );
}
