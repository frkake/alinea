import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { ExportSettings } from "@/components/settings/ExportSettings";
import { triggerDownload } from "@/components/settings/download";

vi.mock("@/components/settings/download", () => ({ triggerDownload: vi.fn() }));

const libraryItemsList = vi.fn();
vi.mock("@yakudoku/api-client", () => ({
  libraryItemsList: (...args: unknown[]) => libraryItemsList(...args),
}));

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function item(overrides: Partial<{ id: string; title: string; authors_short: string; year: number | null }> = {}) {
  const merged = {
    id: "item-1",
    title: "Rectified Flow",
    authors_short: "Liu et al.",
    year: 2023,
    ...overrides,
  };
  return {
    id: merged.id,
    status: "unread",
    tags: [],
    suggested_tags: [],
    quality_level: "A",
    source: "arxiv",
    progress_pct: 0,
    reading_seconds_total: 0,
    paper: {
      id: merged.id,
      title: merged.title,
      authors: [merged.authors_short],
      authors_short: merged.authors_short,
      year: merged.year,
    },
  };
}

describe("ExportSettings (4f §4.6, M1-17 scope: Markdown + BibTeX)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    libraryItemsList.mockResolvedValue({ data: { items: [item()] } });
  });

  test("renders exactly the Markdown + BibTeX cards (CSV/JSON hidden until M2-15)", () => {
    renderWithClient(<ExportSettings />);
    expect(screen.getByText("論文単位 Markdown")).toBeInTheDocument();
    expect(screen.getByText("BibTeX")).toBeInTheDocument();
    expect(screen.queryByText("CSV")).not.toBeInTheDocument();
    expect(screen.queryByText("JSON 一括")).not.toBeInTheDocument();
  });

  test("BibTeX export triggers a direct download of /api/export/bibtex", async () => {
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);
    await user.click(screen.getByRole("button", { name: "BibTeX をエクスポート" }));
    expect(triggerDownload).toHaveBeenCalledWith("/api/export/bibtex");
  });

  test("Markdown export opens the paper picker, searches, and downloads on row click", async () => {
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);

    await user.click(screen.getByRole("button", { name: "論文単位 Markdown をエクスポート" }));
    expect(await screen.findByText("エクスポートする論文を選択")).toBeInTheDocument();
    await screen.findByText("Rectified Flow");

    const input = screen.getByPlaceholderText("タイトル・著者で検索");
    fireEvent.change(input, { target: { value: "rectified" } });
    await waitFor(
      () =>
        expect(libraryItemsList).toHaveBeenCalledWith(
          expect.objectContaining({ query: { q: "rectified", limit: 20 } }),
        ),
      { timeout: 1000 },
    );

    await user.click(screen.getByText("Rectified Flow"));
    expect(triggerDownload).toHaveBeenCalledWith("/api/library-items/item-1/export/markdown");
    await waitFor(() =>
      expect(screen.queryByText("エクスポートする論文を選択")).not.toBeInTheDocument(),
    );
  });

  test("empty search result shows the 該当する論文がありません message", async () => {
    libraryItemsList.mockResolvedValue({ data: { items: [] } });
    const user = userEvent.setup();
    renderWithClient(<ExportSettings />);
    await user.click(screen.getByRole("button", { name: "論文単位 Markdown をエクスポート" }));
    expect(await screen.findByText("該当する論文がありません")).toBeInTheDocument();
  });
});
