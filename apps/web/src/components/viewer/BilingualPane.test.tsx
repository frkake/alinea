import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  annotationsList,
  annotationsCreate,
  translationsListUnits,
  viewerGetDocument,
  type TranslationUnitItem,
} from "@alinea/api-client";
import { TranslationColumnHeader } from "@/components/viewer/TranslationColumnHeader";
import { BilingualPane, BilingualParagraph } from "@/components/viewer/BilingualPane";
import type { DocBlock } from "@/components/viewer/document-types";
import { useTableTranslation } from "@/hooks/use-table-translation";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    annotationsList: vi.fn(),
    annotationsCreate: vi.fn(),
    translationsListUnits: vi.fn(),
    viewerGetDocument: vi.fn(),
  };
});

// useAnnotationSelection → useRouter (next/navigation App Router コンテキストはユニットテスト対象外)。
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock("@/hooks/use-table-translation", () => ({ useTableTranslation: vi.fn() }));

class FakeIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);

// jsdom does not implement Range.getBoundingClientRect; stub it so resolveSelectionAnchor works.
Range.prototype.getBoundingClientRect = () =>
  ({ top: 100, left: 50, bottom: 120, right: 200, width: 150, height: 20 }) as DOMRect;

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

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
    render(
      <TranslationColumnHeader style="literal" pairSync={false} onTogglePairSync={onToggle} />,
    );
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
    render(
      <BilingualParagraph
        block={block}
        unit={unit({ text_ja: null, quality_flags: ["untranslated"] })}
      />,
    );
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
    expect(
      container.querySelector('[data-side="source"] mark.alinea-search-hit'),
    ).toHaveTextContent("flow");
    expect(container.querySelector('[data-side="translation"] mark.alinea-search-hit')).toBeNull();
  });

  test("marks a ?hl= match in the translation (Japanese) column", () => {
    const { container } = render(
      <BilingualParagraph block={block} unit={unit()} searchHighlight="フロー" />,
    );
    expect(
      container.querySelector('[data-side="translation"] mark.alinea-search-hit'),
    ).toHaveTextContent("フロー");
    expect(container.querySelector('[data-side="source"] mark.alinea-search-hit')).toBeNull();
  });
});

describe("BilingualParagraph data-block-id parity", () => {
  const block: DocBlock = {
    id: "blk-p1",
    type: "paragraph",
    inlines: [{ t: "text", v: "The rectified flow is an ODE." }],
  };
  function unit(): TranslationUnitItem {
    return {
      unit_id: "u1",
      block_id: "blk-p1",
      text_ja: "整流フローは常微分方程式である。",
      content_ja: null,
      state: "machine",
      quality_flags: [],
      proposal: null,
    };
  }

  test("translation cell carries data-block-id so selections resolve to a block", () => {
    const { container } = render(<BilingualParagraph block={block} unit={unit()} />);
    const translationCell = container.querySelector('[data-side="translation"]');
    expect(translationCell).toHaveAttribute("data-block-id", "blk-p1");
  });
});

describe("BilingualPane table translation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(annotationsList).mockResolvedValue({
      data: {
        items: [],
        counts: {
          all: 0,
          important: 0,
          question: 0,
          idea: 0,
          term: 0,
          with_comment: 0,
          unplaced: 0,
        },
      },
    } as never);
    vi.mocked(translationsListUnits).mockResolvedValue({
      data: { set_id: "set-1", items: [] },
    } as never);
    vi.mocked(viewerGetDocument).mockResolvedValue({
      data: {
        revision_id: "revision-1",
        quality_level: "A",
        sections: [
          {
            id: "section-1",
            heading: { number: "2", title: "Results" },
            blocks: [
              {
                id: "table-1",
                type: "table",
                raw: "<table><tr><td>Source result</td></tr></table>",
                source_grid: {
                  supported: true,
                  source_format: "html",
                  reason: null,
                  rows: [
                    [
                      {
                        id: "r0c0",
                        source: "Source result",
                        header: false,
                        rowspan: 1,
                        colspan: 1,
                        translatable: true,
                        math: [],
                        latex_body_start: null,
                        latex_body_end: null,
                        latex_wrappers: [],
                      },
                    ],
                  ],
                },
              },
            ],
          },
        ],
      },
    } as never);
  });

  test("shows the canonical table translation action in bilingual mode", async () => {
    const start = vi.fn();
    vi.mocked(useTableTranslation).mockReturnValue({
      status: "idle",
      error: null,
      start,
      retry: vi.fn(),
    });

    renderWithClient(
      <BilingualPane
        itemId="item-1"
        revisionId="revision-1"
        style="natural"
        translationSetId="set-1"
        translationStatus="complete"
        toc={[]}
        lastPosition={null}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "この表を翻訳" }));
    expect(start).toHaveBeenCalledOnce();
    expect(useTableTranslation).toHaveBeenCalledWith({
      itemId: "item-1",
      revisionId: "revision-1",
      style: "natural",
      translationSetId: "set-1",
      sectionId: "section-1",
      blockId: "table-1",
    });
  });

  test("removes the action after the exact units query contains complete typed cells", async () => {
    vi.mocked(useTableTranslation).mockReturnValue({
      status: "succeeded",
      error: null,
      start: vi.fn(),
      retry: vi.fn(),
    });
    vi.mocked(translationsListUnits).mockResolvedValue({
      data: {
        set_id: "set-1",
        items: [
          {
            unit_id: "unit-table-1",
            block_id: "table-1",
            text_ja: "翻訳済み結果",
            content_ja: {
              kind: "table",
              version: 1,
              caption: null,
              cells: [["翻訳済み結果"]],
            },
            state: "machine",
            quality_flags: [],
            proposal: null,
          },
        ],
      },
    } as never);

    renderWithClient(
      <BilingualPane
        itemId="item-1"
        revisionId="revision-1"
        style="natural"
        translationSetId="set-1"
        translationStatus="complete"
        toc={[]}
        lastPosition={null}
      />,
    );

    expect(await screen.findByText("翻訳済み結果")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "この表を翻訳" })).not.toBeInTheDocument();
    expect(screen.queryByText("表を翻訳しました")).not.toBeInTheDocument();
    expect(useTableTranslation).toHaveBeenCalledWith(
      expect.objectContaining({ sectionId: "section-1", blockId: "table-1" }),
    );
  });
});

describe("BilingualPane annotation creation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(annotationsList).mockResolvedValue({
      data: { items: [], counts: { all: 0, important: 0, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 } },
    } as never);
    vi.mocked(translationsListUnits).mockResolvedValue({
      data: {
        set_id: "set-1",
        items: [
          {
            unit_id: "u1",
            block_id: "blk-p1",
            text_ja: "整流フローは常微分方程式である。",
            content_ja: null,
            state: "machine",
            quality_flags: [],
            proposal: null,
          },
        ],
      },
    } as never);
    vi.mocked(viewerGetDocument).mockResolvedValue({
      data: {
        revision_id: "revision-1",
        quality_level: "A",
        sections: [
          {
            id: "section-1",
            heading: { number: "1", title: "Intro" },
            blocks: [{ id: "blk-p1", type: "paragraph", inlines: [{ t: "text", v: "The rectified flow is an ODE." }] }],
          },
        ],
      },
    } as never);
    vi.mocked(annotationsCreate).mockResolvedValue({ data: {} } as never);
  });

  test("highlighting a source-cell selection creates a source-side annotation", async () => {
    renderWithClient(
      <BilingualPane
        itemId="item-1"
        revisionId="revision-1"
        style="natural"
        translationSetId="set-1"
        translationStatus="complete"
        toc={[]}
        lastPosition={null}
      />,
    );
    const sourceText = await screen.findByText("The rectified flow is an ODE.");
    // Select the whole source cell text.
    const range = document.createRange();
    range.selectNodeContents(sourceText);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    fireEvent.pointerUp(sourceText);
    // 4 color dots + comment; click the first color dot ("重要でハイライト").
    fireEvent.click(await screen.findByLabelText("重要でハイライト"));
    await waitFor(() => expect(annotationsCreate).toHaveBeenCalled());
    // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
    expect(vi.mocked(annotationsCreate).mock.calls[0]![0]).toMatchObject({
      path: { item_id: "item-1" },
      body: { kind: "highlight", anchor: { side: "source", block_id: "blk-p1" } },
    });
  });
});
