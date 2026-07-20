import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { libraryItemsSimilar, type SimilarPapersResponse } from "@alinea/api-client";
import { SimilarPapers } from "@/components/viewer/SimilarPapers";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, libraryItemsSimilar: vi.fn() };
});

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function response(overrides: Partial<SimilarPapersResponse> = {}): SimilarPapersResponse {
  return { items: [], indexing: false, ...overrides };
}

describe("SimilarPapers (S12)", () => {
  beforeEach(() => vi.clearAllMocks());

  test("renders neighbors with title, authors, similarity and library link", async () => {
    vi.mocked(libraryItemsSimilar).mockResolvedValue({
      data: response({
        items: [
          {
            library_item_id: "li_near",
            title: "Consistency Models",
            authors: ["Yang Song", "Prafulla Dhariwal"],
            similarity: 0.92,
          },
          {
            library_item_id: "li_far",
            title: "Progressive Distillation",
            authors: ["Tim Salimans"],
            similarity: 0.71,
          },
        ],
      }),
    } as never);

    renderWithClient(<SimilarPapers itemId="li_flow" />);

    expect(await screen.findByText("似た論文")).toBeInTheDocument();
    expect(screen.getByText("Consistency Models")).toBeInTheDocument();
    expect(screen.getByText("Yang Song, Prafulla Dhariwal")).toBeInTheDocument();
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.getByText("71%")).toBeInTheDocument();
    const link = screen.getByText("Consistency Models").closest("a");
    expect(link).toHaveAttribute("href", "/papers/li_near");
    // 呼び出し引数(所有 item の similar)。
    expect(libraryItemsSimilar).toHaveBeenCalledWith({
      path: { item_id: "li_flow" },
      throwOnError: true,
    });
  });

  test("hides the whole section when there are no items and not indexing (flag off)", async () => {
    vi.mocked(libraryItemsSimilar).mockResolvedValue({ data: response() } as never);
    const { container } = renderWithClient(<SimilarPapers itemId="li_flow" />);
    await waitFor(() => expect(libraryItemsSimilar).toHaveBeenCalled());
    expect(screen.queryByText("似た論文")).not.toBeInTheDocument();
    expect(container.querySelectorAll('[data-testid="similar-paper-row"]')).toHaveLength(0);
  });

  test("shows an indexing hint when the target has no embedding yet", async () => {
    vi.mocked(libraryItemsSimilar).mockResolvedValue({
      data: response({ items: [], indexing: true }),
    } as never);
    renderWithClient(<SimilarPapers itemId="li_flow" />);
    expect(await screen.findByText("似た論文")).toBeInTheDocument();
    expect(
      screen.getByText("埋め込みを準備しています。少し待つと似た論文が表示されます。"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("similar-paper-row")).not.toBeInTheDocument();
  });
});
