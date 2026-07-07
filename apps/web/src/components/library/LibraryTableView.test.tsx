import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { LibraryItemSummary } from "@yakudoku/api-client";
import { LibraryTableView } from "@/components/library/LibraryTableView";

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
    render(
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
    render(
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
    render(
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
