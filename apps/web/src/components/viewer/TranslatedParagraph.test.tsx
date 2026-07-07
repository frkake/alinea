import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { TranslationUnitItem } from "@yakudoku/api-client";
import { TranslatedParagraph, type PlacedHighlight } from "@/components/viewer/TranslatedParagraph";
import type { DocBlock } from "@/components/viewer/document-types";

function block(overrides: Partial<DocBlock> = {}): DocBlock {
  return { id: "blk-1", type: "paragraph", inlines: [{ t: "text", v: "Hello world" }], ...overrides };
}

function unit(overrides: Partial<TranslationUnitItem> = {}): TranslationUnitItem {
  return {
    unit_id: "unit_1",
    block_id: "blk-1",
    text_ja: "拡散モデルは反復ステップを要する。以上。",
    state: "generated",
    quality_flags: [],
    proposal: null,
    ...overrides,
  };
}

// M1-02/03: 本文ハイライト描画(4 色 + 注釈番号チップ) — TranslatedParagraph 配線
describe("TranslatedParagraph highlight rendering (1b §4.5-5 / §5.6)", () => {
  test("renders no <mark> when there are no highlights", () => {
    const { container } = render(
      <TranslatedParagraph
        block={block()}
        unit={unit()}
        parallelLabel="¶1 / 1 Introduction"
        popOpen={false}
        onTogglePop={vi.fn()}
      />,
    );
    expect(container.querySelector("mark")).toBeNull();
  });

  test("wraps the offset range in a HighlightMark with the annotation number chip", () => {
    const highlights: PlacedHighlight[] = [
      { id: "ann_1", start: 0, end: 6, color: "important", number: 2 },
    ];
    const onAnnotationClick = vi.fn();
    render(
      <TranslatedParagraph
        block={block()}
        unit={unit()}
        parallelLabel="¶1 / 1 Introduction"
        popOpen={false}
        onTogglePop={vi.fn()}
        highlights={highlights}
        onAnnotationClick={onAnnotationClick}
      />,
    );
    const mark = screen.getByText("拡散モデルは");
    expect(mark.tagName).toBe("MARK");
    expect(mark).toHaveStyle({ background: "var(--pr-ann-important-bg)" });

    const chip = screen.getByRole("button", { name: "注釈 2 を表示" });
    fireEvent.click(chip);
    expect(onAnnotationClick).toHaveBeenCalledWith("ann_1");
  });

  test("renders text before/after the highlighted range unmarked", () => {
    const highlights: PlacedHighlight[] = [
      { id: "ann_1", start: 3, end: 6, color: "question", number: 1 },
    ];
    const { container } = render(
      <TranslatedParagraph
        block={block()}
        unit={unit({ text_ja: "本文A範囲B残り" })}
        parallelLabel="¶1 / 1 Introduction"
        popOpen={false}
        onTogglePop={vi.fn()}
        highlights={highlights}
      />,
    );
    const marks = container.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveTextContent("範囲B");
    // 前後の非ハイライト部分もそのまま残る(丸数字チップの「1」は別要素として付加される)。
    expect(container.querySelector("p")?.textContent).toBe("本文A範囲B1残り");
  });
});

// plans/11 §7: 検索ヒット遷移の `?hl=` は遷移先ブロックのみを yk-search-hit でマークする。
describe("TranslatedParagraph searchHighlight (plans/11 §7)", () => {
  test("wraps case-insensitive matches of the hl query in a yk-search-hit mark", () => {
    const { container } = render(
      <TranslatedParagraph
        block={block()}
        unit={unit({ text_ja: "整流フローを提案する。" })}
        parallelLabel="¶1 / 1 Introduction"
        popOpen={false}
        onTogglePop={vi.fn()}
        searchHighlight="フロー"
      />,
    );
    const mark = container.querySelector("mark.yk-search-hit");
    expect(mark).not.toBeNull();
    expect(mark).toHaveTextContent("フロー");
  });

  test("does not mark text that already overlaps an annotation highlight", () => {
    const highlights: PlacedHighlight[] = [
      { id: "ann_1", start: 0, end: 4, color: "term", number: 1 },
    ];
    const { container } = render(
      <TranslatedParagraph
        block={block()}
        unit={unit({ text_ja: "整流フローを提案する。" })}
        parallelLabel="¶1 / 1 Introduction"
        popOpen={false}
        onTogglePop={vi.fn()}
        highlights={highlights}
        searchHighlight="整流"
      />,
    );
    // 「整流」は注釈ハイライト範囲(0-4)と重なるため yk-search-hit は付かない。
    expect(container.querySelector("mark.yk-search-hit")).toBeNull();
    expect(container.querySelector("mark.yk-highlight-term")).toHaveTextContent("整流フロ");
  });
});

// mobile.md §4.4: モバイルではホバー用「対」ボタンを非描画にし、段落タップで対訳ポップを開閉する。
describe("TranslatedParagraph mobile tap-to-toggle (mobile.md §4.4)", () => {
  test("hides the hover 対 toggle button and toggles the pop on paragraph tap instead", () => {
    const onTogglePop = vi.fn();
    render(
      <TranslatedParagraph
        block={block()}
        unit={unit()}
        parallelLabel="¶1 / 1 Introduction"
        popOpen={false}
        onTogglePop={onTogglePop}
        isMobile
      />,
    );
    expect(screen.queryByRole("button", { name: "対訳を表示" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /拡散モデル/ }));
    expect(onTogglePop).toHaveBeenCalledTimes(1);
  });

  test("clicking the annotation chip does not also toggle the pop (event does not double-fire)", () => {
    const onTogglePop = vi.fn();
    const onAnnotationClick = vi.fn();
    const highlights: PlacedHighlight[] = [
      { id: "ann_1", start: 0, end: 6, color: "important", number: 2 },
    ];
    render(
      <TranslatedParagraph
        block={block()}
        unit={unit()}
        parallelLabel="¶1 / 1 Introduction"
        popOpen={false}
        onTogglePop={onTogglePop}
        highlights={highlights}
        onAnnotationClick={onAnnotationClick}
        isMobile
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "注釈 2 を表示" }));
    expect(onAnnotationClick).toHaveBeenCalledWith("ann_1");
    expect(onTogglePop).not.toHaveBeenCalled();
  });

  test("keeps the desktop hover button and does not tap-toggle when isMobile is false", () => {
    const onTogglePop = vi.fn();
    render(
      <TranslatedParagraph
        block={block()}
        unit={unit()}
        parallelLabel="¶1 / 1 Introduction"
        popOpen={false}
        onTogglePop={onTogglePop}
      />,
    );
    expect(screen.getByRole("button", { name: "対訳を表示" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("拡散モデルは反復ステップを要する。以上。"));
    expect(onTogglePop).not.toHaveBeenCalled();
  });
});
