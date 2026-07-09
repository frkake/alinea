import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { LibraryItemSummary } from "@alinea/api-client";
import LibraryPage from "./page";
import { ToastViewport } from "@/components/ui/Toast";
import { mockMatchMedia } from "@/test-utils/mockMatchMedia";

const push = vi.fn();
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => searchParams,
}));

const libraryItemsFacets = vi.fn();
const libraryItemsList = vi.fn();
const savedFiltersCreate = vi.fn();
const libraryItemsDelete = vi.fn();
vi.mock("@alinea/api-client", () => ({
  libraryItemsFacets: (...args: unknown[]) => libraryItemsFacets(...args),
  libraryItemsList: (...args: unknown[]) => libraryItemsList(...args),
  savedFiltersCreate: (...args: unknown[]) => savedFiltersCreate(...args),
  libraryItemsDelete: (...args: unknown[]) => libraryItemsDelete(...args),
}));

function makeItem(id: string): LibraryItemSummary {
  return {
    id,
    paper: {
      id: `pap_${id}`,
      title: `Paper ${id}`,
      authors: ["Alice Liu"],
      authors_short: "Liu",
      venue: "ICLR 2023",
      year: 2023,
      arxiv_id: "2209.03003",
      license: "cc-by",
      visibility: "public",
      abstract: "",
    },
    status: "reading",
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
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LibraryPage />
      <ToastViewport />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// mobile.md §5.1: モバイルはカードビューのみ・1 カラム。ビュー切替(操作系)は非描画。
describe("LibraryPage mobile reduction (mobile.md §5.1)", () => {
  test("desktop: defaults to the table view and shows the view switch", async () => {
    mockMatchMedia(false);
    libraryItemsFacets.mockResolvedValue({ data: { quick: { all: 1 } } });
    libraryItemsList.mockResolvedValue({ data: { items: [makeItem("1")] } });
    renderPage();

    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    expect(screen.getByRole("radiogroup", { name: "表示形式" })).toBeInTheDocument();
    // テーブルビュー既定: LibraryTable のチェックボックス列が存在する。
    expect(screen.getByRole("checkbox", { name: "すべて選択" })).toBeInTheDocument();
  });

  test("mobile: forces the card view (no table checkboxes) and hides the view switch", async () => {
    mockMatchMedia(true);
    libraryItemsFacets.mockResolvedValue({ data: { quick: { all: 1 } } });
    libraryItemsList.mockResolvedValue({ data: { items: [makeItem("1")] } });
    renderPage();

    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    expect(screen.queryByRole("radiogroup", { name: "表示形式" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "すべて選択" })).toBeNull();
  });
});

// M2-14: 属性フィルタ・保存フィルタ・filter_id 適用(plans/03 §5.1・§5.14、1e §4.5・§4.6)
describe("LibraryPage attribute filters and saved filters (M2-14)", () => {
  test("changing an attribute filter refetches facets and the list with the new query", async () => {
    mockMatchMedia(false);
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const facetsData = {
      quick: { all: 1, unread: 1, in_progress: 0, done: 0, recheck: 0 },
      status: { planned: 1, up_next: 0, reading: 0, done: 0, reread: 0, on_hold: 0 },
      tags: [],
      collections: [],
      quality: { A: 1, B: 0 },
      years: [{ year: 2023, count: 1 }],
    };
    libraryItemsFacets.mockResolvedValue({ data: facetsData });
    libraryItemsList.mockResolvedValue({ data: { items: [makeItem("1")] } });
    renderPage();

    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    libraryItemsList.mockClear();

    await user.click(screen.getByRole("button", { name: "年 ▾" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /2023/ }));

    await waitFor(() =>
      expect(libraryItemsList).toHaveBeenCalledWith(
        expect.objectContaining({ query: expect.objectContaining({ year: [2023] }) }),
      ),
    );
  });

  test("filter_id in the URL is forwarded to the list query", async () => {
    mockMatchMedia(false);
    searchParams = new URLSearchParams("filter_id=sf_1");
    libraryItemsFacets.mockResolvedValue({ data: { quick: { all: 0 } } });
    libraryItemsList.mockResolvedValue({ data: { items: [] } });
    renderPage();

    await waitFor(() =>
      expect(libraryItemsList).toHaveBeenCalledWith(
        expect.objectContaining({ query: expect.objectContaining({ filter_id: "sf_1" }) }),
      ),
    );
    searchParams = new URLSearchParams();
  });

  test("この条件を保存 is disabled with no filters applied and enabled once a quick filter is set", async () => {
    mockMatchMedia(false);
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    libraryItemsFacets.mockResolvedValue({
      data: { quick: { all: 1, unread: 1, in_progress: 0, done: 0, recheck: 0 } },
    });
    libraryItemsList.mockResolvedValue({ data: { items: [makeItem("1")] } });
    renderPage();

    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "この条件を保存" })).toBeDisabled();

    await user.click(screen.getByText("未読"));
    expect(screen.getByRole("button", { name: "この条件を保存" })).toBeEnabled();
  });

  test("saving a filter posts the current quick/sort conditions and shows a success toast", async () => {
    mockMatchMedia(false);
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    libraryItemsFacets.mockResolvedValue({
      data: { quick: { all: 1, unread: 1, in_progress: 0, done: 0, recheck: 0 } },
    });
    libraryItemsList.mockResolvedValue({ data: { items: [makeItem("1")] } });
    savedFiltersCreate.mockResolvedValue({
      data: { id: "sf_1", name: "未読だけ", conditions: {}, sort: {}, count: 1 },
    });
    renderPage();

    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    await user.click(screen.getByText("未読"));
    await user.click(screen.getByRole("button", { name: "この条件を保存" }));
    await user.type(screen.getByLabelText("フィルタ名"), "未読だけ");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(savedFiltersCreate).toHaveBeenCalledWith({
        body: {
          name: "未読だけ",
          conditions: {
            quick: "unread",
            status: undefined,
            tags: undefined,
            collection_id: undefined,
            quality: undefined,
            years: undefined,
          },
          sort: { key: "updated_at", order: "desc" },
        },
      }),
    );
    expect(await screen.findByText("保存フィルタ「未読だけ」を作成しました")).toBeInTheDocument();
  });
});


describe("LibraryPage delete wiring", () => {
  test("table view deletes a library item after confirmation", async () => {
    mockMatchMedia(false);
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    libraryItemsFacets.mockResolvedValue({ data: { quick: { all: 1 } } });
    libraryItemsList.mockResolvedValue({ data: { items: [makeItem("1")] } });
    libraryItemsDelete.mockResolvedValue({});
    renderPage();

    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Paper 1 を削除" }));

    expect(screen.getByText("ライブラリから削除しますか?")).toBeInTheDocument();
    expect(libraryItemsDelete).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "削除する" }));

    await waitFor(() =>
      expect(libraryItemsDelete).toHaveBeenCalledWith({ path: { item_id: "1" }, throwOnError: true }),
    );
    expect(await screen.findByText("論文を削除しました")).toBeInTheDocument();
  });
});
