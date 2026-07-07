import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";
import { BulkActionBar } from "@/components/library/BulkActionBar";

const collectionsList = vi.fn();
const tagsList = vi.fn();
vi.mock("@yakudoku/api-client", () => ({
  collectionsList: (...args: unknown[]) => collectionsList(...args),
  tagsList: (...args: unknown[]) => tagsList(...args),
}));

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// VT-LIB-02: LibraryTable + BulkActionBar(10 列固定ヘッダ・複数選択でフローティングバー)
describe("BulkActionBar (1e §4.8 / §5.5)", () => {
  test("is hidden when nothing is selected", () => {
    renderWithClient(
      <BulkActionBar
        selectedCount={0}
        onClearSelection={() => {}}
        onSetStatus={() => {}}
        onAddTags={() => {}}
        onAddToCollection={() => {}}
      />,
    );
    expect(screen.queryByRole("toolbar", { name: "一括操作" })).not.toBeInTheDocument();
  });

  test("shows the selected count and the 3 actions plus clear-selection", () => {
    renderWithClient(
      <BulkActionBar
        selectedCount={2}
        onClearSelection={() => {}}
        onSetStatus={() => {}}
        onAddTags={() => {}}
        onAddToCollection={() => {}}
      />,
    );
    expect(screen.getByText("2 件を選択中")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ステータス変更/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "タグ追加" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "コレクションへ" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "選択解除 ×" })).toBeInTheDocument();
  });

  test("clicking 選択解除 clears the selection", async () => {
    const user = userEvent.setup();
    const onClearSelection = vi.fn();
    renderWithClient(
      <BulkActionBar
        selectedCount={1}
        onClearSelection={onClearSelection}
        onSetStatus={() => {}}
        onAddTags={() => {}}
        onAddToCollection={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "選択解除 ×" }));
    expect(onClearSelection).toHaveBeenCalled();
  });

  test("status menu selection calls onSetStatus with the chosen status and closes", async () => {
    const user = userEvent.setup();
    const onSetStatus = vi.fn();
    renderWithClient(
      <BulkActionBar
        selectedCount={2}
        onClearSelection={() => {}}
        onSetStatus={onSetStatus}
        onAddTags={() => {}}
        onAddToCollection={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: /ステータス変更/ }));
    await user.click(screen.getByRole("menuitem", { name: /読んだ/ }));
    expect(onSetStatus).toHaveBeenCalledWith("done");
  });

  test("tag popover: typing and pressing Enter chips the free-text tag, disabled until at least one chip", async () => {
    tagsList.mockResolvedValue({ data: { items: [] } });
    const user = userEvent.setup();
    const onAddTags = vi.fn();
    renderWithClient(
      <BulkActionBar
        selectedCount={2}
        onClearSelection={() => {}}
        onSetStatus={() => {}}
        onAddTags={onAddTags}
        onAddToCollection={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "タグ追加" }));
    const addButton = () => screen.getByRole("button", { name: "追加" });
    expect(addButton()).toBeDisabled();

    await user.type(screen.getByLabelText("タグを追加"), "solver{Enter}");
    expect(addButton()).toBeEnabled();
    await user.click(addButton());
    expect(onAddTags).toHaveBeenCalledWith(["solver"]);
  });

  test("collection popover: selecting a collection calls onAddToCollection and closes", async () => {
    collectionsList.mockResolvedValue({
      data: { items: [{ id: "col_1", name: "輪読会 2026-07", item_count: 5 }] },
    });
    const user = userEvent.setup();
    const onAddToCollection = vi.fn();
    renderWithClient(
      <BulkActionBar
        selectedCount={2}
        onClearSelection={() => {}}
        onSetStatus={() => {}}
        onAddTags={() => {}}
        onAddToCollection={onAddToCollection}
      />,
    );
    await user.click(screen.getByRole("button", { name: "コレクションへ" }));
    await waitFor(() => expect(screen.getByText("輪読会 2026-07")).toBeInTheDocument());
    await user.click(screen.getByText("輪読会 2026-07"));
    expect(onAddToCollection).toHaveBeenCalledWith("col_1");
  });

  test("is dimmed and non-interactive while busy", () => {
    renderWithClient(
      <BulkActionBar
        selectedCount={1}
        busy
        onClearSelection={() => {}}
        onSetStatus={() => {}}
        onAddTags={() => {}}
        onAddToCollection={() => {}}
      />,
    );
    const bar = screen.getByRole("toolbar", { name: "一括操作" });
    expect(bar).toHaveStyle({ opacity: "0.5", pointerEvents: "none" });
  });
});
