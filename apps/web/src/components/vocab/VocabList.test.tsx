import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { VocabEntrySummary } from "@yakudoku/api-client";
import { VocabList } from "@/components/vocab/VocabList";

function entry(overrides: Partial<VocabEntrySummary> = {}): VocabEntrySummary {
  return {
    id: "v_1",
    kind: "idiom",
    term: "boil down to",
    meaning_short: "要するに〜に帰着する",
    source: { library_item_id: "li_1", paper_title: "Rectified Flow", display: "Rectified Flow · §2.1" },
    added_at: new Date().toISOString(),
    generation: "done",
    ...overrides,
  };
}

const noop = () => undefined;

// VT-VOC-01: VocabList — 種別チップ 3 分類+復習期チップ絞り込み・ソート(行順・ヘッダ表示)。
describe("VocabList (VT-VOC-01)", () => {
  test("renders rows with term, kind badge, meaning, source, and relative added date", () => {
    render(
      <VocabList
        entries={[entry()]}
        selectedId={null}
        sort="added_at"
        onSortChange={noop}
        onSelect={noop}
        onReachEnd={noop}
        isFetchingNextPage={false}
        emptyVariant={null}
        onClearFilters={noop}
      />,
    );
    expect(screen.getByText("boil down to")).toBeInTheDocument();
    expect(screen.getByText("イディオム")).toBeInTheDocument();
    expect(screen.getByText("要するに〜に帰着する")).toBeInTheDocument();
    expect(screen.getByText("Rectified Flow · §2.1")).toBeInTheDocument();
    expect(screen.getByText("今日")).toBeInTheDocument();
    expect(screen.getByText("語彙")).toBeInTheDocument(); // sort='added_at' の間はソート矢印なし
    expect(screen.getByText("追加 ↓")).toBeInTheDocument();
  });

  test("clicking a row calls onSelect with its id", () => {
    const onSelect = vi.fn();
    render(
      <VocabList
        entries={[entry({ id: "v_42" })]}
        selectedId={null}
        sort="added_at"
        onSortChange={noop}
        onSelect={onSelect}
        onReachEnd={noop}
        isFetchingNextPage={false}
        emptyVariant={null}
        onClearFilters={noop}
      />,
    );
    fireEvent.click(screen.getByRole("option"));
    expect(onSelect).toHaveBeenCalledWith("v_42");
  });

  test("header shows 語彙 ↑ when sort=term and 追加 ↓ when sort=added_at; clicking dispatches onSortChange", () => {
    const onSortChange = vi.fn();
    const { rerender } = render(
      <VocabList
        entries={[entry()]}
        selectedId={null}
        sort="added_at"
        onSortChange={onSortChange}
        onSelect={noop}
        onReachEnd={noop}
        isFetchingNextPage={false}
        emptyVariant={null}
        onClearFilters={noop}
      />,
    );
    expect(screen.getByText("追加 ↓")).toBeInTheDocument();
    fireEvent.click(screen.getByText(/^語彙/));
    expect(onSortChange).toHaveBeenCalledWith("term");

    rerender(
      <VocabList
        entries={[entry()]}
        selectedId={null}
        sort="term"
        onSortChange={onSortChange}
        onSelect={noop}
        onReachEnd={noop}
        isFetchingNextPage={false}
        emptyVariant={null}
        onClearFilters={noop}
      />,
    );
    expect(screen.getByText("語彙 ↑")).toBeInTheDocument();
    fireEvent.click(screen.getByText(/^追加/));
    expect(onSortChange).toHaveBeenCalledWith("added_at");
  });

  test("ArrowDown/ArrowUp move selection without wrapping past the ends", () => {
    const onSelect = vi.fn();
    render(
      <VocabList
        entries={[entry({ id: "a" }), entry({ id: "b" }), entry({ id: "c" })]}
        selectedId="a"
        sort="added_at"
        onSortChange={noop}
        onSelect={onSelect}
        onReachEnd={noop}
        isFetchingNextPage={false}
        emptyVariant={null}
        onClearFilters={noop}
      />,
    );
    const listbox = screen.getByRole("listbox");
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    expect(onSelect).toHaveBeenCalledWith("b");
    onSelect.mockClear();
    fireEvent.keyDown(listbox, { key: "ArrowUp" });
    // selectedId prop はまだ "a"(呼び出し側の再レンダリング前)なので、上限から先には動かない。
    expect(onSelect).not.toHaveBeenCalled();
  });

  test("shows pending/failed meaning text per row generation state", () => {
    render(
      <VocabList
        entries={[
          entry({ id: "p", generation: "pending", meaning_short: null }),
          entry({ id: "f", generation: "failed", meaning_short: null }),
        ]}
        selectedId={null}
        sort="added_at"
        onSortChange={noop}
        onSelect={noop}
        onReachEnd={noop}
        isFetchingNextPage={false}
        emptyVariant={null}
        onClearFilters={noop}
      />,
    );
    expect(screen.getByText("生成中…")).toBeInTheDocument();
    expect(screen.getByText("生成に失敗 — 再試行できます")).toBeInTheDocument();
  });

  test("empty variant no-entries shows the onboarding empty state", () => {
    render(
      <VocabList
        entries={[]}
        selectedId={null}
        sort="added_at"
        onSortChange={noop}
        onSelect={noop}
        onReachEnd={noop}
        isFetchingNextPage={false}
        emptyVariant="no-entries"
        onClearFilters={noop}
      />,
    );
    expect(screen.getByText("まだ語彙がありません")).toBeInTheDocument();
  });

  test("empty variant no-match shows the clear-filters action", () => {
    const onClearFilters = vi.fn();
    render(
      <VocabList
        entries={[]}
        selectedId={null}
        sort="added_at"
        onSortChange={noop}
        onSelect={noop}
        onReachEnd={noop}
        isFetchingNextPage={false}
        emptyVariant="no-match"
        onClearFilters={onClearFilters}
      />,
    );
    fireEvent.click(screen.getByText("絞り込みを解除"));
    expect(onClearFilters).toHaveBeenCalledTimes(1);
  });
});
