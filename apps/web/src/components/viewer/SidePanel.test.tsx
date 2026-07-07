import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { annotationsList } from "@yakudoku/api-client";
import { notesList } from "@yakudoku/api-client";
import { SidePanel } from "@/components/viewer/SidePanel";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@yakudoku/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@yakudoku/api-client")>();
  return {
    ...actual,
    annotationsList: vi.fn(async () => ({
      data: { items: [], counts: { all: 0, important: 0, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 } },
    })),
    notesList: vi.fn(async () => ({ data: { items: [] } })),
  };
});

function resetStore() {
  useViewerStore.setState({
    panelOpen: true,
    activeTab: "chat",
    style: "natural",
    tocOpen: true,
    itemId: "li_test",
    revisionId: "rev_test",
  });
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// M1-03 / M1-04: サイドパネルに メモ・注釈 タブを追加(milestone="M1")。
describe("SidePanel tabs milestone=M1", () => {
  beforeEach(() => {
    resetStore();
    vi.clearAllMocks();
  });

  test("M1 shows 5 tabs including メモ and 注釈, still hides リソース", () => {
    renderWithClient(<SidePanel milestone="M1" />);
    expect(screen.getByRole("tab", { name: "チャット" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "メモ" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "注釈" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "図表" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "情報" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "リソース" })).toBeNull();
  });

  test("switching to 注釈 mounts AnnotationListPanel directly (no renderTab prop needed)", () => {
    useViewerStore.setState({ activeTab: "annotations" });
    renderWithClient(<SidePanel milestone="M1" />);
    expect(annotationsList).toHaveBeenCalled();
  });

  test("switching to メモ mounts NotesPanel directly", () => {
    useViewerStore.setState({ activeTab: "notes" });
    renderWithClient(<SidePanel milestone="M1" />);
    expect(notesList).toHaveBeenCalled();
  });

  test("注釈 tab uses the 320px panel width; other tabs use 340px", () => {
    useViewerStore.setState({ activeTab: "annotations" });
    const { container, rerender } = renderWithClient(<SidePanel milestone="M1" />);
    expect((container.firstChild as HTMLElement).style.width).toBe("320px");

    act(() => {
      useViewerStore.setState({ activeTab: "figures" });
    });
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <SidePanel milestone="M1" />
      </QueryClientProvider>,
    );
    expect((container.firstChild as HTMLElement).style.width).toBe("340px");
  });

  test("clicking the active tab closes the panel (viewer-shell §6.4)", () => {
    useViewerStore.setState({ activeTab: "chat" });
    renderWithClient(<SidePanel milestone="M1" />);
    fireEvent.click(screen.getByRole("tab", { name: "チャット" }));
    expect(useViewerStore.getState().panelOpen).toBe(false);
  });
});

describe("SidePanel default milestone stays M0 (backward compatibility)", () => {
  beforeEach(resetStore);

  test("no milestone prop still restricts to the 3 M0 tabs", () => {
    renderWithClient(<SidePanel />);
    expect(screen.queryByRole("tab", { name: "メモ" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "注釈" })).toBeNull();
  });
});
