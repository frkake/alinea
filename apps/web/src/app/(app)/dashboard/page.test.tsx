import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import DashboardPage from "./page";
import { mockMatchMedia } from "@/test-utils/mockMatchMedia";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const dashboardGet = vi.fn();
vi.mock("@alinea/api-client", () => ({
  dashboardGet: (...args: unknown[]) => dashboardGet(...args),
  libraryItemsSetQueueOrder: vi.fn(),
}));

function makeData() {
  return {
    continue_reading: [],
    up_next_queue: [
      {
        id: "li_1",
        paper: {
          id: "pap_1",
          title: "Paper 1",
          authors: ["A"],
          authors_short: "A",
          venue: "ICLR",
          year: 2024,
          arxiv_id: "1",
          license: "cc-by",
          visibility: "public",
          abstract: "",
        },
        status: "up_next",
        priority: null,
        deadline: null,
        tags: [],
        suggested_tags: [],
        quality_level: "A",
        source: "arxiv",
        progress_pct: 0,
        comprehension: null,
        reading_seconds_total: 0,
        added_at: "2026-07-02T00:00:00Z",
        updated_at: "2026-07-02T00:00:00Z",
      },
    ],
    deadlines: { collections: [], items: [] },
    recent: { week_count: 0, items: [] },
    stats: { week: { finished_count: 0, reading_hours: 0 }, weekly_hours: [] },
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DashboardPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// mobile.md §5.2 / §1.2 実装 3: 縦積み 1 カラム + 並べ替え(操作系)は非描画。
describe("DashboardPage mobile reduction (mobile.md §5.2)", () => {
  test("desktop: shows the up-next reorder buttons", async () => {
    mockMatchMedia(false);
    dashboardGet.mockResolvedValue({ data: makeData() });
    renderPage();
    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    expect(screen.getAllByRole("button", { name: "上へ移動" }).length).toBeGreaterThan(0);
  });

  test("mobile: hides the up-next reorder buttons but keeps the item visible", async () => {
    mockMatchMedia(true);
    dashboardGet.mockResolvedValue({ data: makeData() });
    renderPage();
    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "上へ移動" })).toBeNull();
    expect(screen.queryByRole("button", { name: "下へ移動" })).toBeNull();
  });
});
