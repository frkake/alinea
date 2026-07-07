import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { AppNav } from "@/components/AppNav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/library",
}));

const vocabList = vi.fn();
vi.mock("@yakudoku/api-client", () => ({
  vocabList: (...args: unknown[]) => vocabList(...args),
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
