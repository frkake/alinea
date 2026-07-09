import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { articlesGet, type ArticleOut } from "@alinea/api-client";
import { ViewerHeader } from "@/components/viewer/ViewerHeader";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, articlesGet: vi.fn() };
});

function article(): ArticleOut {
  return {
    id: "art_1",
    library_item_id: "li_1",
    title: "T",
    preset: "beginner",
    include_math: false,
    version: 1,
    generated_at: "2026-07-06T00:00:00Z",
    disclaimer: "d",
    overview_figure: null,
    blocks: [],
  };
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const baseProps = {
  itemId: "li_1",
  title: "Flow Straight and Fast",
  qualityLevel: "A" as const,
  status: "reading" as const,
  onModeChange: vi.fn(),
  onStatusChange: vi.fn(),
  onBack: vi.fn(),
};

// 1h §4.2-7: mode=article のみこのスロットに「✦ 指示つき再生成」を表示し、
// 他モードの「スタイル: 自然訳 ▾」は隠す。
describe("ViewerHeader — article mode slot (1h §4.2-7)", () => {
  beforeEach(() => {
    useViewerStore.setState({ style: "natural", literalStatus: "unknown" });
  });

  test("mode=translation shows the style selector, not the regenerate button", () => {
    renderWithClient(<ViewerHeader {...baseProps} mode="translation" />);
    expect(screen.getByText(/スタイル: 自然訳/)).toBeInTheDocument();
    expect(screen.queryByText("指示つき再生成")).not.toBeInTheDocument();
  });

  test("mode=article shows the regenerate button once the article loads, not the style selector", async () => {
    vi.mocked(articlesGet).mockResolvedValue({ data: article() } as never);
    renderWithClient(<ViewerHeader {...baseProps} mode="article" />);
    await waitFor(() => expect(screen.getByText("指示つき再生成")).toBeInTheDocument());
    expect(screen.queryByText(/スタイル:/)).not.toBeInTheDocument();
  });

  test("shows all 5 segments including 記事", () => {
    renderWithClient(<ViewerHeader {...baseProps} mode="translation" />);
    expect(screen.getByText("記事")).toBeInTheDocument();
  });
});
