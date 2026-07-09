import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { SearchHitWithPaper } from "@alinea/api-client";
import { SearchDropdown, SearchPreviewItem } from "@/components/search/SearchDropdown";

function hit(overrides: Partial<SearchHitWithPaper> = {}): SearchHitWithPaper {
  return {
    source: "body",
    matched_in: ["source"],
    display: "§3.2 Training via Distillation · p.5",
    snippet: '…the target network is an <mark class="alinea-search-hit">EMA teacher</mark>…',
    snippet_lang: "en",
    target: { kind: "viewer", library_item_id: "li_1", anchor: { revision_id: "rev_1", block_id: "blk_1", display: "§3.2" } },
    library_item: { id: "li_1", title: "Consistency Models" },
    ...overrides,
  };
}

// VT-LIB-01 相当(1e §4.3 検索ドロップダウン)
describe("SearchPreviewItem (1e §4.3)", () => {
  test("renders badge, title, meta and snippet html", () => {
    render(<SearchPreviewItem hit={hit()} active={false} onClick={vi.fn()} onMouseEnter={vi.fn()} />);
    expect(screen.getByText("本文でヒット")).toBeInTheDocument();
    expect(screen.getByText("Consistency Models")).toBeInTheDocument();
    expect(screen.getByText(/§3\.2 Training via Distillation/)).toBeInTheDocument();
    expect(document.querySelector("mark.alinea-search-hit")).toHaveTextContent("EMA teacher");
  });

  test("jump link only renders on the active row", () => {
    const { rerender } = render(
      <SearchPreviewItem hit={hit()} active={false} onClick={vi.fn()} onMouseEnter={vi.fn()} />,
    );
    expect(screen.queryByText("該当位置へ →")).not.toBeInTheDocument();
    rerender(<SearchPreviewItem hit={hit()} active onClick={vi.fn()} onMouseEnter={vi.fn()} />);
    expect(screen.getByText("該当位置へ →")).toBeInTheDocument();
  });

  test("clicking and hovering call the handlers", () => {
    const onClick = vi.fn();
    const onMouseEnter = vi.fn();
    render(<SearchPreviewItem hit={hit()} active={false} onClick={onClick} onMouseEnter={onMouseEnter} />);
    fireEvent.mouseEnter(screen.getByRole("option"));
    fireEvent.click(screen.getByRole("option"));
    expect(onMouseEnter).toHaveBeenCalledTimes(1);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("SearchDropdown (1e §4.3 / §5.3)", () => {
  const baseProps = {
    query: "EMA teacher",
    loading: false,
    isError: false,
    onHoverIndex: vi.fn(),
    onSelect: vi.fn(),
    onShowAll: vi.fn(),
    onRetry: vi.fn(),
  };

  test("first load (never fetched) shows the 検索中 header and no body", () => {
    render(<SearchDropdown {...baseProps} loading total={null} items={[]} activeIndex={-1} />);
    expect(screen.getByText("「EMA teacher」を検索中…")).toBeInTheDocument();
    expect(screen.queryByText(/一致する結果はありません/)).not.toBeInTheDocument();
  });

  test("renders header count, items and the footer 'show all' link", () => {
    render(
      <SearchDropdown {...baseProps} total={2} items={[hit(), hit({ source: "chat", target: { kind: "chat", library_item_id: "li_1", thread_id: "th_1", message_id: "msg_1" } })]} activeIndex={0} />,
    );
    expect(screen.getAllByText(/2 件/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("option")).toHaveLength(2);
    const footer = screen.getByText("すべての結果を表示(2 件)→");
    fireEvent.click(footer);
    expect(baseProps.onShowAll).toHaveBeenCalledTimes(1);
  });

  test("0 results shows the empty row and hides the footer", () => {
    render(<SearchDropdown {...baseProps} total={0} items={[]} activeIndex={-1} />);
    expect(screen.getByText("一致する結果はありません")).toBeInTheDocument();
    expect(screen.queryByText(/すべての結果を表示/)).not.toBeInTheDocument();
  });

  test("error state shows retry and calls onRetry", () => {
    const onRetry = vi.fn();
    render(<SearchDropdown {...baseProps} isError total={0} items={[]} activeIndex={-1} onRetry={onRetry} />);
    fireEvent.click(screen.getByText("再試行"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
