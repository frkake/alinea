import { describe, expect, test } from "vitest";
import { SKIP_OFFSET_ATTR, textOffsetWithin } from "@/components/viewer/text-offset";

function el(html: string): HTMLDivElement {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div;
}

/** テスト用フィクスチャの Text ノード取得(non-null assertion を使わないための小道具)。 */
function asText(node: ChildNode | null | undefined): Text {
  if (!(node instanceof Text)) throw new Error("expected a text node in test fixture");
  return node;
}

describe("textOffsetWithin (1b §5.5 anchor 構築)", () => {
  test("plain text node: offset is the raw character index", () => {
    const root = el("Hello world");
    expect(textOffsetWithin(root, asText(root.firstChild), 6)).toBe(6);
  });

  test("sums preceding sibling text node lengths", () => {
    const root = el("<span>abc</span><span>def</span>");
    const secondTextNode = asText(root.querySelectorAll("span")[1]?.firstChild);
    // "abc" (3) + 2 文字目("de") = 5
    expect(textOffsetWithin(root, secondTextNode, 2)).toBe(5);
  });

  test(`elements with ${SKIP_OFFSET_ATTR} are excluded from the count`, () => {
    const root = el(`<button ${SKIP_OFFSET_ATTR}="">2</button><span>abc</span>`);
    const span = asText(root.querySelector("span")?.firstChild);
    // skip 対象の "2" は数えない → "abc" の 2 文字目までは 2。
    expect(textOffsetWithin(root, span, 2)).toBe(2);
  });

  test("nested elements accumulate offsets across depths", () => {
    const root = el("<p>ab<em>cd</em>ef</p>");
    const ef = asText(root.querySelector("p")?.lastChild);
    // "ab"(2) + "cd"(2) + 1 = 5
    expect(textOffsetWithin(root, ef, 1)).toBe(5);
  });
});
