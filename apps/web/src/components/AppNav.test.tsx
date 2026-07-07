import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { AppNav } from "@/components/AppNav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/library",
}));

const vocabList = vi.fn();
const savedFiltersList = vi.fn();
vi.mock("@yakudoku/api-client", () => ({
  vocabList: (...args: unknown[]) => vocabList(...args),
  savedFiltersList: (...args: unknown[]) => savedFiltersList(...args),
}));

function renderNav() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppNav />
    </QueryClientProvider>,
  );
}

// VT-VOC-04: サイドバーバッジ = 総語数(counts.all。plans/09-screens/4d §2.1・docs/11 受け入れ基準)
describe("AppNav vocab badge (VT-VOC-04)", () => {
  beforeEach(() => {
    vocabList.mockReset().mockResolvedValue({
      data: {
        items: [],
        next_cursor: null,
        total: 46,
        counts: { all: 46, word: 28, collocation: 12, idiom: 6, due: 12 },
      },
    });
    savedFiltersList.mockReset().mockResolvedValue({ data: { items: [] } });
  });

  test("shows the vocab nav item with the total entry count badge", async () => {
    renderNav();
    expect(await screen.findByText("語彙帳")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("46")).toBeInTheDocument();
    });
  });

  test("does not show a badge before counts have loaded", () => {
    vocabList.mockReset().mockReturnValue(new Promise(() => undefined));
    renderNav();
    expect(screen.getByText("語彙帳")).toBeInTheDocument();
    expect(screen.queryByText("46")).not.toBeInTheDocument();
  });
});

// M2-14: サイドバー「保存フィルタ」節(1e §4.4・plans/03 §5.14)。
describe("AppNav saved filters section (M2-14)", () => {
  test("shows the saved-filters heading and each filter's derived count", async () => {
    vocabList.mockReset().mockResolvedValue({ data: { counts: { all: 0 } } });
    savedFiltersList.mockReset().mockResolvedValue({
      data: {
        items: [
          { id: "sf_1", name: "締切あり", conditions: {}, sort: { key: "deadline", order: "asc" }, count: 3 },
          { id: "sf_2", name: "cs.CV の未読", conditions: {}, sort: { key: "updated_at", order: "desc" }, count: 7 },
        ],
      },
    });
    renderNav();

    expect(await screen.findByText("保存フィルタ")).toBeInTheDocument();
    expect(screen.getByText("締切あり")).toBeInTheDocument();
    expect(screen.getByText("cs.CV の未読")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /締切あり/ })).toHaveAttribute(
      "href",
      "/library?filter_id=sf_1",
    );
  });

  test("omits the section entirely when there are no saved filters", async () => {
    vocabList.mockReset().mockResolvedValue({ data: { counts: { all: 0 } } });
    savedFiltersList.mockReset().mockResolvedValue({ data: { items: [] } });
    renderNav();

    await waitFor(() => expect(savedFiltersList).toHaveBeenCalled());
    expect(screen.queryByText("保存フィルタ")).not.toBeInTheDocument();
  });
});
