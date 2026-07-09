import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { figuresGetOverview } from "@alinea/api-client";
import { OverviewFigureFrame } from "@/components/viewer/article/OverviewFigureFrame";
import type { OverviewFigureRef } from "@/components/viewer/article/types";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, figuresGetOverview: vi.fn() };
});

function figure(overrides: Partial<OverviewFigureRef> = {}): OverviewFigureRef {
  return {
    id: "ovf_1",
    version: 2,
    generated_at: "2026-07-06T00:00:00Z",
    svg_url: "/api/overview-figures/ovf_1/versions/2/svg",
    raster_url: null,
    evidence: [
      {
        display: "§1",
        anchor: { revision_id: "rev_1", block_id: "blk-1", display: "§1" },
      },
    ],
    dsl: {
      layout: "flow-3",
      cards: [],
      connectors: [],
      footer: { generated_by: "✦ AI 生成 · Alinea", date: "2026-07-06" },
    },
    ...overrides,
  };
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// VT-VIEW-13: 「AI生成 · 版 N」・「✦ 書き直し指示」・「SVG ⤓」(download 属性)
describe("OverviewFigureFrame (VT-VIEW-13)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("renders the version badge, rewrite button, and a download link with the download attribute", () => {
    renderWithClient(
      <OverviewFigureFrame
        figure={figure()}
        articleId="art_1"
        rewriting={false}
        onRewrite={vi.fn()}
        onRestoreVersion={vi.fn()}
        onJumpToAnchor={vi.fn()}
      />,
    );
    expect(screen.getByText("AI生成 · 版 2")).toBeInTheDocument();
    expect(screen.getByText("✦ 書き直し指示")).toBeInTheDocument();
    const link = screen.getByText("SVG ⤓");
    expect(link).toHaveAttribute("href", "/api/overview-figures/ovf_1/versions/2/svg?download=true");
    expect(link).toHaveAttribute("download");
  });

  test("renders the footer generated-by/date text and an evidence chip", () => {
    renderWithClient(
      <OverviewFigureFrame
        figure={figure()}
        articleId="art_1"
        rewriting={false}
        onRewrite={vi.fn()}
        onRestoreVersion={vi.fn()}
        onJumpToAnchor={vi.fn()}
      />,
    );
    expect(screen.getByText("✦ AI 生成 · Alinea · 2026-07-06")).toBeInTheDocument();
    expect(screen.getByText("§1")).toBeInTheDocument();
  });

  test("clicking an evidence chip calls onJumpToAnchor with the chip's anchor", () => {
    const onJumpToAnchor = vi.fn();
    renderWithClient(
      <OverviewFigureFrame
        figure={figure()}
        articleId="art_1"
        rewriting={false}
        onRewrite={vi.fn()}
        onRestoreVersion={vi.fn()}
        onJumpToAnchor={onJumpToAnchor}
      />,
    );
    fireEvent.click(screen.getByText("§1"));
    expect(onJumpToAnchor).toHaveBeenCalledWith(
      expect.objectContaining({ block_id: "blk-1", display: "§1" }),
    );
  });

  test("submitting the rewrite popover calls onRewrite with the typed instruction", async () => {
    const user = userEvent.setup();
    const onRewrite = vi.fn();
    renderWithClient(
      <OverviewFigureFrame
        figure={figure()}
        articleId="art_1"
        rewriting={false}
        onRewrite={onRewrite}
        onRestoreVersion={vi.fn()}
        onJumpToAnchor={vi.fn()}
      />,
    );
    await user.click(screen.getByText("✦ 書き直し指示"));
    await user.type(screen.getByLabelText("書き直し指示"), "実験を削って手法を厚く");
    await user.click(screen.getByText("✦ 書き直す"));
    expect(onRewrite).toHaveBeenCalledWith("実験を削って手法を厚く");
  });

  test("shows the rewriting overlay with progress percentage", () => {
    renderWithClient(
      <OverviewFigureFrame
        figure={figure()}
        articleId="art_1"
        rewriting
        rewritingProgressPct={42}
        onRewrite={vi.fn()}
        onRestoreVersion={vi.fn()}
        onJumpToAnchor={vi.fn()}
      />,
    );
    expect(screen.getByText("✦ 書き直し中… 42%")).toBeInTheDocument();
  });

  test("clicking the version badge opens the version popover and fetches versions", async () => {
    const user = userEvent.setup();
    vi.mocked(figuresGetOverview).mockResolvedValue({
      data: {
        ...figure(),
        versions: [
          { version: 2, generated_at: "2026-07-06T00:00:00Z" },
          { version: 1, generated_at: "2026-07-05T00:00:00Z" },
        ],
      },
    } as never);
    renderWithClient(
      <OverviewFigureFrame
        figure={figure()}
        articleId="art_1"
        rewriting={false}
        onRewrite={vi.fn()}
        onRestoreVersion={vi.fn()}
        onJumpToAnchor={vi.fn()}
      />,
    );
    await user.click(screen.getByText("AI生成 · 版 2"));
    expect(await screen.findByText("この版に戻す")).toBeInTheDocument();
    expect(figuresGetOverview).toHaveBeenCalledWith({ path: { article_id: "art_1" }, throwOnError: true });
  });

  test("clicking この版に戻す calls onRestoreVersion with the target version", async () => {
    const user = userEvent.setup();
    const onRestoreVersion = vi.fn();
    vi.mocked(figuresGetOverview).mockResolvedValue({
      data: {
        ...figure(),
        versions: [
          { version: 2, generated_at: "2026-07-06T00:00:00Z" },
          { version: 1, generated_at: "2026-07-05T00:00:00Z" },
        ],
      },
    } as never);
    renderWithClient(
      <OverviewFigureFrame
        figure={figure()}
        articleId="art_1"
        rewriting={false}
        onRewrite={vi.fn()}
        onRestoreVersion={onRestoreVersion}
        onJumpToAnchor={vi.fn()}
      />,
    );
    await user.click(screen.getByText("AI生成 · 版 2"));
    await user.click(await screen.findByText("この版に戻す"));
    expect(onRestoreVersion).toHaveBeenCalledWith(1);
  });
});
