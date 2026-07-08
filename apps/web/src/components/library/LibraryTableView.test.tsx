import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  libraryItemsBulk,
  libraryItemsUpdate,
  type LibraryItemSummary,
} from "@yakudoku/api-client";
import { LibraryTableView } from "@/components/library/LibraryTableView";
import { useFinishReadingStore } from "@/components/library/finishReadingStore";

vi.mock("@yakudoku/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@yakudoku/api-client")>();
  return {
    ...actual,
    libraryItemsUpdate: vi.fn(),
    libraryItemsBulk: vi.fn(),
    collectionsList: vi.fn(),
    tagsList: vi.fn(),
  };
});

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function makeItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id: "li_1",
    paper: {
      id: "pap_1",
      title: "Rectified Flow",
      authors: ["Xingchao Liu"],
      authors_short: "Liu, Gong, Liu",
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
    ...overrides,
  };
}

// VT-LIB-01 / VT-LIB-02(10 列部)
describe("LibraryTableView", () => {
  test("renders the fixed 10 columns and dashes for unsupplied values", () => {
    renderWithClient(
      <LibraryTableView
        items={[makeItem()]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );

    // 10 列固定 = 全選択チェックボックス + 9 見出し
    const rows = screen.getAllByRole("row");
    expect(rows[0]?.children).toHaveLength(10);

    for (const label of [
      "論文",
      "ステータス",
      "品質",
      "タグ",
      "優先度",
      "締切",
      "読書時間",
      "理解度",
      "追加日",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByRole("checkbox", { name: "すべて選択" })).toBeInTheDocument();

    // 未供給列(優先度・締切・読書時間・理解度)は「—」
    expect(screen.getAllByText("—")).toHaveLength(4);
  });

  test("renders supplied values instead of dashes", () => {
    renderWithClient(
      <LibraryTableView
        items={[
          makeItem({
            priority: "high",
            deadline: "2026-07-16",
            reading_seconds_total: 11520,
            comprehension: 4,
          }),
        ]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );
    expect(screen.getByText("Rectified Flow")).toBeInTheDocument();
    expect(screen.getByText("7/16")).toBeInTheDocument();
    expect(screen.getByText("3.2h")).toBeInTheDocument();
    expect(screen.getByText("4/5")).toBeInTheDocument();
    expect(screen.getByText("高")).toBeInTheDocument();
  });

  test("column-header click requests a sort change", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const onSortChange = vi.fn();
    renderWithClient(
      <LibraryTableView
        items={[makeItem()]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={onSortChange}
        onOpenRow={() => {}}
      />,
    );
    await user.click(screen.getByText("追加日"));
    expect(onSortChange).toHaveBeenCalledWith({ key: "added_at", dir: "asc" });
  });
});

// M1 統合ポリッシュ: 1e テーブルの StatusPill を interactive にし、PATCH+ダッシュボード/
// ライブラリ invalidate+done 遷移時に読了ダイアログを開く(LibraryCard と同じ発火規約)。
describe("LibraryTableView interactive StatusPill (1e §4.7 / M1 統合ポリッシュ)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useFinishReadingStore.setState({ item: null });
  });

  test("changing the row status to 読んだ patches the item and opens the finish-reading dialog", async () => {
    const updated = makeItem({ status: "done" });
    vi.mocked(libraryItemsUpdate).mockResolvedValue({ data: updated } as never);

    renderWithClient(
      <LibraryTableView
        items={[makeItem({ status: "reading" })]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Rectified Flow のステータスを変更" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /読んだ/ }));

    await waitFor(() =>
      expect(libraryItemsUpdate).toHaveBeenCalledWith({
        path: { item_id: "li_1" },
        body: { status: "done" },
        throwOnError: true,
      }),
    );
    await waitFor(() => expect(useFinishReadingStore.getState().item).toEqual(updated));
  });

  test("clicking the status pill does not trigger the row's onOpenRow navigation", () => {
    const onOpenRow = vi.fn();
    renderWithClient(
      <LibraryTableView
        items={[makeItem({ status: "reading" })]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={onOpenRow}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Rectified Flow のステータスを変更" }));
    expect(onOpenRow).not.toHaveBeenCalled();
  });

  test("clicking a processing row opens the reader without forcing PDF mode", () => {
    const onOpenRow = vi.fn();
    renderWithClient(
      <LibraryTableView
        items={[
          makeItem({
            pipeline: { job_id: "job_1", stage: "structuring", status: "running", progress_pct: 35 },
          }),
        ]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={onOpenRow}
      />,
    );

    fireEvent.click(screen.getByText("Rectified Flow"));

    expect(onOpenRow).toHaveBeenCalledWith("li_1");
  });

  test("changing to a non-done status does not open the finish-reading dialog", async () => {
    const updated = makeItem({ status: "on_hold" });
    vi.mocked(libraryItemsUpdate).mockResolvedValue({ data: updated } as never);

    renderWithClient(
      <LibraryTableView
        items={[makeItem({ status: "reading" })]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Rectified Flow のステータスを変更" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /保留/ }));

    await waitFor(() =>
      expect(libraryItemsUpdate).toHaveBeenCalledWith({
        path: { item_id: "li_1" },
        body: { status: "on_hold" },
        throwOnError: true,
      }),
    );
    expect(useFinishReadingStore.getState().item).toBeNull();
  });
});

// M2-14: 複数選択→BulkActionBar(1e §4.8・§5.5、plans/03 §5.6)
describe("LibraryTableView bulk actions (M2-14)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("selecting rows shows the bulk action bar with the correct count", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    renderWithClient(
      <LibraryTableView
        items={[
          makeItem({ id: "li_1" }),
          makeItem({
            id: "li_2",
            paper: { ...makeItem().paper, title: "Consistency Models" },
          }),
        ]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );
    expect(screen.queryByRole("toolbar", { name: "一括操作" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Rectified Flow を選択" }));
    expect(screen.getByText("1 件を選択中")).toBeInTheDocument();
  });

  test("bulk status change calls libraryItemsBulk with the selected ids and invalidates the list", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    vi.mocked(libraryItemsBulk).mockResolvedValue({ data: { updated: 1 } } as never);

    renderWithClient(
      <LibraryTableView
        items={[makeItem({ id: "li_1" })]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );
    await user.click(screen.getByRole("checkbox", { name: "Rectified Flow を選択" }));
    await user.click(screen.getByRole("button", { name: /ステータス変更/ }));
    await user.click(screen.getByRole("menuitem", { name: /読んだ/ }));

    await waitFor(() =>
      expect(libraryItemsBulk).toHaveBeenCalledWith({
        body: { ids: ["li_1"], op: "set_status", status: "done" },
      }),
    );
    // 成功後も選択は維持される(§5.5 の決定)。
    expect(screen.getByText("1 件を選択中")).toBeInTheDocument();
  });

  test("a failed bulk operation keeps the selection", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    vi.mocked(libraryItemsBulk).mockResolvedValue({
      error: { title: "見つかりません", code: "not_found" },
    } as never);

    renderWithClient(
      <LibraryTableView
        items={[makeItem({ id: "li_1" })]}
        sort={{ key: "updated_at", dir: "desc" }}
        onSortChange={() => {}}
        onOpenRow={() => {}}
      />,
    );
    await user.click(screen.getByRole("checkbox", { name: "Rectified Flow を選択" }));
    await user.click(screen.getByRole("button", { name: /ステータス変更/ }));
    await user.click(screen.getByRole("menuitem", { name: /読んだ/ }));

    await waitFor(() => expect(libraryItemsBulk).toHaveBeenCalled());
    expect(screen.getByText("1 件を選択中")).toBeInTheDocument();
  });
});
