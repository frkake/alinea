import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, test, vi } from "vitest";
import { SavedFilterList } from "@/components/library/SavedFilterList";

const savedFiltersList = vi.fn();
vi.mock("@alinea/api-client", () => ({
  savedFiltersList: (...args: unknown[]) => savedFiltersList(...args),
}));

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SavedFilterList />
    </QueryClientProvider>,
  );
}

// PY-LIB-07 に対応するフロント側: サイドバー「保存フィルタ」節(1e §4.4)
describe("SavedFilterList", () => {
  test("renders each saved filter with its derived count and a filter_id link", async () => {
    savedFiltersList.mockResolvedValue({
      data: {
        items: [
          { id: "sf_1", name: "締切あり", conditions: {}, sort: { key: "deadline", order: "asc" }, count: 3 },
        ],
      },
    });
    renderWithClient();

    await waitFor(() => expect(screen.getByText("締切あり")).toBeInTheDocument());
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /締切あり/ })).toHaveAttribute(
      "href",
      "/library?filter_id=sf_1",
    );
  });

  test("renders nothing when there are no saved filters", async () => {
    savedFiltersList.mockResolvedValue({ data: { items: [] } });
    const { container } = renderWithClient();

    await waitFor(() => expect(savedFiltersList).toHaveBeenCalled());
    // SidebarNav は main=[] かつ sections=[] のとき何も出さない(ヘッダ・行なし)。
    expect(container.querySelectorAll("a")).toHaveLength(0);
  });
});
