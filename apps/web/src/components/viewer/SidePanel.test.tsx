import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { annotationsList } from "@alinea/api-client";
import { notesList } from "@alinea/api-client";
import { SidePanel } from "@/components/viewer/SidePanel";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    annotationsList: vi.fn(async () => ({
      data: {
        items: [],
        counts: {
          all: 0,
          important: 0,
          question: 0,
          idea: 0,
          term: 0,
          with_comment: 0,
          unplaced: 0,
        },
      },
    })),
    notesList: vi.fn(async () => ({ data: { items: [] } })),
  };
});

function resetStore() {
  window.localStorage.clear();
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

  test("panel width can be resized and is persisted", () => {
    const { container } = renderWithClient(<SidePanel milestone="M1" />);
    const panel = container.firstChild as HTMLElement;
    const separator = screen.getByRole("separator", { name: "サイドパネルの幅を変更" });

    expect(panel.style.width).toBe("380px");
    fireEvent(separator, new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 400 }));
    fireEvent(window, new MouseEvent("pointermove", { bubbles: true, clientX: 350 }));
    fireEvent(window, new MouseEvent("pointerup", { bubbles: true }));

    expect(panel.style.width).toBe("430px");
    expect(window.localStorage.getItem("alinea-viewer-side-panel-width")).toBe("430");
  });

  test("panel width can be adjusted from the keyboard", () => {
    const { container } = renderWithClient(<SidePanel milestone="M1" />);
    const separator = screen.getByRole("separator", { name: "サイドパネルの幅を変更" });

    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect((container.firstChild as HTMLElement).style.width).toBe("400px");
  });

  test("clicking the active tab closes the panel (viewer-shell §6.4)", () => {
    useViewerStore.setState({ activeTab: "chat" });
    renderWithClient(<SidePanel milestone="M1" />);
    fireEvent.click(screen.getByRole("tab", { name: "チャット" }));
    expect(useViewerStore.getState().panelOpen).toBe(false);
  });

  test("collapse button closes the open panel without relying on tab reclick", () => {
    useViewerStore.setState({ activeTab: "figures" });
    renderWithClient(<SidePanel milestone="M1" />);
    fireEvent.click(screen.getByLabelText("サイドパネルを折りたたむ"));
    expect(useViewerStore.getState().panelOpen).toBe(false);
    expect(useViewerStore.getState().activeTab).toBe("figures");
  });

  test("closed panel keeps a rail that can reopen the active tab", () => {
    useViewerStore.setState({ panelOpen: false, activeTab: "figures" });
    renderWithClient(<SidePanel milestone="M1" />);
    expect(screen.getByLabelText("サイドパネルを開く")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("サイドパネルを開く"));
    expect(useViewerStore.getState().panelOpen).toBe(true);
    expect(useViewerStore.getState().activeTab).toBe("figures");
  });

  test("closed rail opens a requested tab directly", () => {
    useViewerStore.setState({ panelOpen: false, activeTab: "chat" });
    renderWithClient(<SidePanel milestone="M1" />);
    fireEvent.click(screen.getByLabelText("注釈を開く"));
    expect(useViewerStore.getState().panelOpen).toBe(true);
    expect(useViewerStore.getState().activeTab).toBe("annotations");
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

// M2-13: サイドパネルに リソース タブを追加(plans/13 §4 M2-13。docs/12・plans/09-screens/5a)。
describe("SidePanel tabs milestone=M2", () => {
  beforeEach(() => {
    resetStore();
    vi.clearAllMocks();
    // 生成 SDK は本物の Response を消費する(headers / text / json)ため疑似応答も本物で返す。
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ items: [], suggestion: null, count: 0 }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("M2 shows all 6 tabs including リソース", () => {
    renderWithClient(<SidePanel milestone="M2" />);
    expect(screen.getByRole("tab", { name: "チャット" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "メモ" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "注釈" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "図表" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "リソース" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "情報" })).toBeInTheDocument();
  });

  test("switching to リソース mounts ResourcesPanel directly and fetches the list", async () => {
    useViewerStore.setState({ activeTab: "resources" });
    renderWithClient(<SidePanel milestone="M2" />);
    await screen.findByText("リソースはまだありません");
    // 生成 SDK は `fetch(request)` を単一の Request で呼ぶため、URL は Request から取り出す。
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const listCall = fetchMock.mock.calls.find((call: unknown[]) => {
      const input = call[0] as RequestInfo | URL;
      const u = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      return u.includes("/api/library-items/li_test/resources");
    });
    expect(listCall).toBeDefined();
  });

  test("M1 still hides リソース (M2 タブ追加は既存タブに影響しない)", () => {
    renderWithClient(<SidePanel milestone="M1" />);
    expect(screen.queryByRole("tab", { name: "リソース" })).toBeNull();
  });
});

// Task-8: サイドパネルに 単語候補 タブを追加(milestone="M3")。
describe("SidePanel tabs milestone=M3", () => {
  beforeEach(() => {
    resetStore();
    vi.clearAllMocks();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ items: [], suggestion: null, count: 0 }),
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("M3 shows 単語候補 tab", () => {
    renderWithClient(<SidePanel milestone="M3" />);
    expect(screen.getByRole("tab", { name: "単語候補" })).toBeInTheDocument();
  });

  test("M2 still hides 単語候補 (M3 タブ追加は既存タブに影響しない)", () => {
    renderWithClient(<SidePanel milestone="M2" />);
    expect(screen.queryByRole("tab", { name: "単語候補" })).toBeNull();
  });

  test("switching to 単語候補 mounts VocabCandidatesPanel directly", async () => {
    useViewerStore.setState({ activeTab: "vocab-candidates" });
    const mockList = await import("@alinea/api-client");
    vi.spyOn(mockList, "vocabCandidatesList").mockResolvedValue({
      data: { items: [], count: 0 },
    } as never);
    renderWithClient(<SidePanel milestone="M3" />);
    await screen.findByRole("button", { name: "単語候補を抽出" });
  });

  test("単語候補 tab shows pending count badge when counts prop has vocab-candidates", () => {
    renderWithClient(<SidePanel milestone="M3" counts={{ "vocab-candidates": 3 }} />);
    // The tab button contains the label and CountBadge renders the number as text
    const tab = screen.getByRole("tab", { name: /単語候補/ });
    expect(tab).toBeInTheDocument();
    // CountBadge renders the count as inline text within the tab button
    expect(tab.textContent).toContain("3");
  });
});
