import { describe, expect, test } from "vitest";
import { resolveSelectionAnchor } from "@/hooks/annotation-selection-resolve";
import { SOURCE_TEXT_ATTR } from "@/components/viewer/text-offset";

/** Build a DOM subtree, select `text` inside the node matching `selector`, return its Range. */
function selectWithin(html: string, selector: string): { range: Range; text: string } {
  const host = document.createElement("div");
  host.innerHTML = html;
  document.body.appendChild(host);
  const target = host.querySelector(selector) as HTMLElement;
  const textNode = target.firstChild as Text;
  const range = document.createRange();
  range.setStart(textNode, 0);
  range.setEnd(textNode, textNode.length);
  // Mock getBoundingClientRect since jsdom doesn't support it
  Object.defineProperty(range, "getBoundingClientRect", {
    value: () => ({
      top: 0,
      left: 0,
      bottom: 20,
      right: 100,
      width: 100,
      height: 20,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }),
  });
  return { range, text: textNode.textContent ?? "" };
}

describe("resolveSelectionAnchor", () => {
  test("returns null when no [data-block-id] ancestor exists", () => {
    const { range, text } = selectWithin(`<p class="x">loose text</p>`, ".x");
    expect(resolveSelectionAnchor(range, text, "translation")).toBeNull();
  });

  test("uses defaultSide=source when block has no side markers (source pane)", () => {
    const { range, text } = selectWithin(
      `<div data-block-id="blk-1">Hello world</div>`,
      '[data-block-id="blk-1"]',
    );
    const sel = resolveSelectionAnchor(range, text, "source");
    expect(sel).toMatchObject({ blockId: "blk-1", side: "source", quote: "Hello world", start: 0 });
    expect(sel?.end).toBe("Hello world".length);
    expect(sel?.sourceFullText).toBe("Hello world");
  });

  test("prefers data-side over defaultSide (bilingual translation cell)", () => {
    const { range, text } = selectWithin(
      `<div data-block-id="blk-2" data-side="translation">訳文テキスト</div>`,
      '[data-block-id="blk-2"]',
    );
    const sel = resolveSelectionAnchor(range, text, "source");
    expect(sel?.side).toBe("translation");
    expect(sel?.sourceFullText).toBeUndefined();
  });

  test("SOURCE_TEXT_ATTR inside a block forces source and sets sourceFullText", () => {
    const { range, text } = selectWithin(
      `<div data-block-id="blk-3"><span ${SOURCE_TEXT_ATTR}>Original sentence.</span></div>`,
      `[${SOURCE_TEXT_ATTR}]`,
    );
    const sel = resolveSelectionAnchor(range, text, "translation");
    expect(sel?.side).toBe("source");
    expect(sel?.sourceFullText).toBe("Original sentence.");
  });
});
