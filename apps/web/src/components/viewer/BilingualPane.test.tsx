import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { TranslationUnitItem } from "@yakudoku/api-client";
import { TranslationColumnHeader } from "@/components/viewer/TranslationColumnHeader";
import { BilingualParagraph } from "@/components/viewer/BilingualPane";
import type { DocBlock } from "@/components/viewer/document-types";

// VT-VIEW-03: 対訳モード — 段落単位 2 カラム + 「段落対応 ⇄」トグル
describe("TranslationColumnHeader (VT-VIEW-03)", () => {
  test("renders 原文/訳文 headers, AI翻訳, and 段落対応 toggle", () => {
    render(<TranslationColumnHeader style="natural" pairSync onTogglePairSync={vi.fn()} />);
    expect(screen.getByText("原文 — ENGLISH")).toBeInTheDocument();
    expect(screen.getByText("訳文 — 自然訳")).toBeInTheDocument();
    expect(screen.getByText("✦ AI翻訳")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "段落対応" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
  });

  test("clicking 段落対応 toggles pair sync", () => {
    const onToggle = vi.fn();
    render(<TranslationColumnHeader style="literal" pairSync={false} onTogglePairSync={onToggle} />);
    // literal スタイル名も追随する
    expect(screen.getByText("訳文 — 直訳")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "段落対応" }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

describe("BilingualParagraph (VT-VIEW-03)", () => {
  const block: DocBlock = {
    id: "blk-p1",
    type: "paragraph",
    inlines: [{ t: "text", v: "The rectified flow is an ODE." }],
  };

  function unit(overrides: Partial<TranslationUnitItem> = {}): TranslationUnitItem {
    return {
      unit_id: "u1",
      block_id: "blk-p1",
      text_ja: "整流フローは常微分方程式である。",
      content_ja: null,
      state: "machine",
      quality_flags: [],
      proposal: null,
      ...overrides,
    };
  }

  test("renders source (left) and translation (right) cells for a paragraph pair", () => {
    const { container } = render(<BilingualParagraph block={block} unit={unit()} />);
    expect(screen.getByText("The rectified flow is an ODE.")).toBeInTheDocument();
    expect(screen.getByText("整流フローは常微分方程式である。")).toBeInTheDocument();
    // 左=原文セル / 右=訳文セルの 2 カラム構造
    expect(container.querySelector('[data-side="source"]')).not.toBeNull();
    expect(container.querySelector('[data-side="translation"]')).not.toBeNull();
  });

  test("shows 翻訳中… when the unit is not yet translated", () => {
    render(<BilingualParagraph block={block} unit={null} />);
    expect(screen.getByText("翻訳中…")).toBeInTheDocument();
  });

  test("shows failure notice when a failure flag is present", () => {
    render(<BilingualParagraph block={block} unit={unit({ text_ja: null, quality_flags: ["untranslated"] })} />);
    expect(screen.getByText("この段落の翻訳に失敗しました")).toBeInTheDocument();
  });
});

// M1 統合ポリッシュ: BilingualPane の hl パリティ(注釈ハイライト・?hl= 一時マークを
// TranslationPane と同じ部品(text-offset/HighlightMark)で両カラムに適用)。
describe("BilingualParagraph hl parity (M1 統合ポリッシュ)", () => {
  const block: DocBlock = {
    id: "blk-p1",
    type: "paragraph",
    inlines: [{ t: "text", v: "The rectified flow is an ODE." }],
  };

  function unit(overrides: Partial<TranslationUnitItem> = {}): TranslationUnitItem {
    return {
      unit_id: "u1",
      block_id: "blk-p1",
      text_ja: "整流フローは常微分方程式である。",
      content_ja: null,
      state: "machine",
      quality_flags: [],
      proposal: null,
      ...overrides,
    };
  }

  test("wraps the translation-side highlight range with a HighlightMark + chip", () => {
    const onAnnotationClick = vi.fn();
    render(
      <BilingualParagraph
        block={block}
        unit={unit()}
        translationHighlights={[{ id: "ann_1", start: 0, end: 5, color: "important", number: 3 }]}
        onAnnotationClick={onAnnotationClick}
      />,
    );
    const mark = screen.getByText("整流フロー");
    expect(mark.tagName).toBe("MARK");
    fireEvent.click(screen.getByRole("button", { name: "注釈 3 を表示" }));
    expect(onAnnotationClick).toHaveBeenCalledWith("ann_1");
  });

  test("wraps the source-side highlight range (English inlines) with a HighlightMark + chip", () => {
    const onAnnotationClick = vi.fn();
    render(
      <BilingualParagraph
        block={block}
        unit={unit()}
        sourceHighlights={[{ id: "ann_2", start: 4, end: 18, color: "question", number: 1 }]}
        onAnnotationClick={onAnnotationClick}
      />,
    );
    const mark = screen.getByText("rectified flow");
    expect(mark.tagName).toBe("MARK");
    fireEvent.click(screen.getByRole("button", { name: "注釈 1 を表示" }));
    expect(onAnnotationClick).toHaveBeenCalledWith("ann_2");
  });

  test("marks a ?hl= match in the source (English) column", () => {
    const { container } = render(
      <BilingualParagraph block={block} unit={unit()} searchHighlight="flow" />,
    );
    expect(container.querySelector('[data-side="source"] mark.yk-search-hit')).toHaveTextContent("flow");
    expect(container.querySelector('[data-side="translation"] mark.yk-search-hit')).toBeNull();
  });

  test("marks a ?hl= match in the translation (Japanese) column", () => {
    const { container } = render(
      <BilingualParagraph block={block} unit={unit()} searchHighlight="フロー" />,
    );
    expect(container.querySelector('[data-side="translation"] mark.yk-search-hit')).toHaveTextContent("フロー");
    expect(container.querySelector('[data-side="source"] mark.yk-search-hit')).toBeNull();
  });
});
