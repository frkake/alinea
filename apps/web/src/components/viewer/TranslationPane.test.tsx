import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within, fireEvent, act, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  annotationsCreate,
  annotationsList,
  translationsListUnits,
  viewerGetDocument,
  vocabCreate,
} from "@yakudoku/api-client";
import { SummaryCard } from "@/components/viewer/SummaryCard";
import { EquationBlock } from "@/components/viewer/EquationBlock";
import { SelectionMenu } from "@/components/viewer/SelectionMenu";
import { TranslationPane } from "@/components/viewer/TranslationPane";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@yakudoku/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@yakudoku/api-client")>();
  return {
    ...actual,
    viewerGetDocument: vi.fn(),
    translationsListUnits: vi.fn(),
    annotationsList: vi.fn(),
    annotationsCreate: vi.fn(),
    vocabCreate: vi.fn(),
  };
});

// 「語彙に追加」(M2-17 wiring)は router.push で /vocab/{id} へ遷移する(next/navigation App
// Router コンテキストはこのユニットテストの render 対象外のため mock する)。
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

// jsdom は IntersectionObserver を実装しない(先頭可視ブロック追従用)。
class FakeIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);

// VT-VIEW-02: 訳文モード — ✦3行要約カード・KaTeX ブロック数式
describe("SummaryCard (VT-VIEW-02)", () => {
  test("renders 3 summary lines, AI badge, and 詳細要約 link", () => {
    const lines = [
      "整流フローを提案。",
      "reflow で経路を直線化。",
      "少ステップで高品質生成。",
    ];
    render(<SummaryCard lines={lines} onDetailedSummary={vi.fn()} />);
    expect(screen.getByText("3行要約")).toBeInTheDocument();
    expect(screen.getByText("AI生成")).toBeInTheDocument();
    expect(screen.getByText("詳細要約 →")).toBeInTheDocument();
    for (const line of lines) {
      expect(screen.getByText(line)).toBeInTheDocument();
    }
  });

  test("shows generating placeholder when lines are null", () => {
    render(<SummaryCard lines={null} />);
    expect(screen.getByText("✦ 要約を生成しています…")).toBeInTheDocument();
  });
});

describe("EquationBlock KaTeX (VT-VIEW-02)", () => {
  test("renders KaTeX output and hover actions", () => {
    const { container } = render(<EquationBlock latex="E = mc^2" number="(1)" />);
    // KaTeX が数式 HTML を生成している(.katex クラス)。
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(screen.getByText("この式を説明")).toBeInTheDocument();
    expect(screen.getByText("LaTeXをコピー")).toBeInTheDocument();
    expect(screen.getByText("(1)")).toBeInTheDocument();
  });
});

// VT-VIEW-05: 選択メニュー — M0 は ✦AIに質問 / コピー の 2 項目のみ
describe("SelectionMenu (VT-VIEW-05)", () => {
  test("M0 selection menu shows only ask-AI and copy", () => {
    render(<SelectionMenu milestone="M0" />);
    const menu = screen.getByRole("menu", { name: "選択メニュー" });
    // ✦AIに質問(✦ は AiMark span)/ コピー の 2 項目。ボタン直下テキストで照合。
    expect(within(menu).getByText("AIに質問")).toBeInTheDocument();
    expect(within(menu).getByText("コピー")).toBeInTheDocument();
    expect(screen.queryByText("語彙に追加")).toBeNull();
    expect(screen.queryByText("コメント")).toBeNull();
    // トップレベルの操作は 2 項目(✦AIに質問 / コピー)のみ。
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(2);
  });
});

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// M1-02/03: TranslationPane 本配線 — SelectionMenu(M1)→注釈作成、本文ハイライト描画
describe("TranslationPane M1 wiring (1b §5.5 / §5.6)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useViewerStore.setState({
      panelOpen: true,
      activeTab: "chat",
      selection: null,
      currentBlockId: null,
      activeSectionId: null,
      pendingScrollTarget: null,
      pendingHighlightQuery: null,
      bilingualPopToggleSignal: 0,
      bookmarkToggleSignal: 0,
    });
    vi.mocked(viewerGetDocument).mockResolvedValue({
      data: {
        revision_id: "rev_1",
        quality_level: "A",
        sections: [
          {
            id: "sec-1",
            heading: { number: "1", title: "Introduction" },
            blocks: [
              {
                id: "blk-1",
                type: "paragraph",
                inlines: [{ t: "text", v: "The rectified flow is an ODE." }],
              },
            ],
          },
        ],
      },
    } as never);
    vi.mocked(translationsListUnits).mockResolvedValue({
      data: {
        items: [
          {
            unit_id: "unit_1",
            block_id: "blk-1",
            text_ja: "整流フローは常微分方程式である。",
            state: "machine",
            quality_flags: [],
            proposal: null,
          },
        ],
      },
    } as never);
    vi.mocked(annotationsList).mockResolvedValue({
      data: {
        items: [],
        counts: { all: 0, important: 0, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 },
      },
    } as never);
    vi.mocked(annotationsCreate).mockResolvedValue({ data: undefined } as never);
  });

  test("clicking a color dot in the M1 selection menu creates a highlight anchored at the selection offsets", async () => {
    renderWithClient(
      <TranslationPane
        itemId="li_1"
        revisionId="rev_1"
        style="natural"
        toc={[]}
        summaryLines={null}
        lastPosition={null}
      />,
    );
    await screen.findByText("整流フローは常微分方程式である。");

    act(() => {
      useViewerStore.setState({
        selection: {
          blockId: "blk-1",
          side: "translation",
          quote: "整流フロー",
          start: 0,
          end: 5,
          rect: { top: 10, left: 10, bottom: 20, right: 40 },
        },
      });
    });

    const menu = await screen.findByRole("menu", { name: "選択メニュー" });
    fireEvent.click(within(menu).getByLabelText("重要でハイライト"));

    await waitFor(() =>
      expect(annotationsCreate).toHaveBeenCalledWith({
        path: { item_id: "li_1" },
        body: {
          kind: "highlight",
          color: "important",
          anchor: {
            revision_id: "rev_1",
            block_id: "blk-1",
            start: 0,
            end: 5,
            quote: "整流フロー",
            side: "translation",
          },
          comment: null,
        },
      }),
    );
    // メニューは作成操作後に閉じる。
    expect(useViewerStore.getState().selection).toBeNull();
  });

  test("「語彙に追加」(side=source)で文脈センテンスを抽出し POST /api/vocab して /vocab/{id} へ遷移する (M2-17)", async () => {
    vi.mocked(vocabCreate).mockResolvedValue({
      data: { entry: { id: "vocab_1" }, generation_job_id: "job_1" },
      response: { status: 201 },
    } as never);

    renderWithClient(
      <TranslationPane
        itemId="li_1"
        revisionId="rev_1"
        style="natural"
        toc={[]}
        summaryLines={null}
        lastPosition={null}
      />,
    );
    await screen.findByText("整流フローは常微分方程式である。");

    act(() => {
      useViewerStore.setState({
        selection: {
          blockId: "blk-1",
          side: "source",
          quote: "ODE",
          start: 25,
          end: 28,
          rect: { top: 10, left: 10, bottom: 20, right: 40 },
          sourceFullText: "The rectified flow is an ODE. It transports two distributions.",
        },
      });
    });

    const menu = await screen.findByRole("menu", { name: "選択メニュー" });
    fireEvent.click(within(menu).getByRole("menuitem", { name: "語彙に追加" }));

    await waitFor(() =>
      expect(vocabCreate).toHaveBeenCalledWith({
        body: {
          library_item_id: "li_1",
          term: "ODE",
          anchor: {
            revision_id: "rev_1",
            block_id: "blk-1",
            start: 25,
            end: 28,
            quote: "ODE",
            side: "source",
          },
          context_sentence: "The rectified flow is an ODE.",
          highlight: { start: 25, end: 28 },
        },
      }),
    );
    expect(useViewerStore.getState().selection).toBeNull();
  });

  test("renders a HighlightMark for a placed translation-side annotation and jumps the panel on chip click", async () => {
    vi.mocked(annotationsList).mockResolvedValue({
      data: {
        items: [
          {
            id: "ann_1",
            kind: "highlight",
            color: "important",
            anchor: {
              revision_id: "rev_1",
              block_id: "blk-1",
              start: 0,
              end: 5,
              quote: "整流フロー",
              side: "translation",
              display: "§1",
            },
            comment: null,
            placed: true,
            created_at: "2026-07-06T21:12:00",
            updated_at: "2026-07-06T21:12:00",
          },
        ],
        counts: { all: 1, important: 1, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 },
      },
    } as never);

    renderWithClient(
      <TranslationPane
        itemId="li_1"
        revisionId="rev_1"
        style="natural"
        toc={[]}
        summaryLines={null}
        lastPosition={null}
      />,
    );

    const mark = await screen.findByText("整流フロー");
    expect(mark.tagName).toBe("MARK");
    const chip = screen.getByRole("button", { name: "注釈 1 を表示" });
    fireEvent.click(chip);

    expect(useViewerStore.getState().activeTab).toBe("annotations");
    expect(useViewerStore.getState().pendingAnnotationId).toBe("ann_1");
  });
});
