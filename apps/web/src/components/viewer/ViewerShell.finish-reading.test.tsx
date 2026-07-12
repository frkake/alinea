import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { libraryItemsUpdate, type LibraryItemSummary, type ViewerInit } from "@alinea/api-client";
import { ViewerShell } from "@/components/viewer/ViewerShell";
import { useFinishReadingStore } from "@/components/library/finishReadingStore";
import { useViewerStore } from "@/stores/viewer-store";

const sseHarness = vi.hoisted(() => ({
  onEvent: undefined as
    | ((event: { type: string; data: unknown; lastEventId: string }) => void)
    | undefined,
}));

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    libraryItemsUpdate: vi.fn(),
    translationsSectionTranslate: vi.fn(),
  };
});

const replace = vi.fn();
const currentSearch = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, back: vi.fn(), push: vi.fn() }),
  useSearchParams: () => currentSearch,
}));

// ViewerShell 固有ロジック(1g §2.3 の発火配線)だけを検証するため、周辺フック/タブ本体は
// 軽量スタブに置き換える(それぞれ別レーン所有・本テストの対象外)。
vi.mock("@/hooks/use-pdf-availability", () => ({ usePdfAvailability: () => true }));
vi.mock("@/hooks/use-reading-position", () => ({ useReadingPosition: () => undefined }));
vi.mock("@/hooks/use-reading-session", () => ({ useReadingSession: () => undefined }));
vi.mock("@/hooks/use-viewer-keymap", () => ({ useViewerKeymap: () => undefined }));
vi.mock("@/lib/sse", () => ({
  useSSE: (options: {
    onEvent?: (event: { type: string; data: unknown; lastEventId: string }) => void;
  }) => {
    sseHarness.onEvent = options.onEvent;
    return { connected: false, fallbackActive: true, lastEventId: "" };
  },
}));
vi.mock("@/components/chat/ChatPanel", () => ({ ChatPanel: () => null }));
vi.mock("@/components/viewer/FiguresPanel", () => ({ FiguresPanel: () => null }));
vi.mock("@/components/viewer/InfoPanel", () => ({ InfoPanel: () => null }));

// jsdom は ResizeObserver / 2D canvas context を実装しない(mode=pdf 分岐は未使用だが
// pdf/PdfPane.tsx が静的 import されるためモジュール読み込み時に必要)。
class FakeResizeObserver {
  observe(): void {}
  disconnect(): void {}
  unobserve(): void {}
}
vi.stubGlobal("ResizeObserver", FakeResizeObserver);
beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    {} as unknown as CanvasRenderingContext2D,
  );
});

function makeLibraryItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
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

function makeViewer(overrides: Partial<ViewerInit> = {}): ViewerInit {
  return {
    library_item: makeLibraryItem(),
    revision: {
      id: "rev_1",
      quality_level: "A",
      source_version: null,
      parser_version: "1.0",
      page_count: 8,
      figure_count: 0,
      table_count: 0,
      created_at: "2026-07-02T00:00:00Z",
    },
    newer_revision: null,
    toc: [],
    translation: null,
    counts: { annotations: 0, resources: 0, figures: 0, notes: 0 },
    last_position: null,
    license_card: { license: "cc-by", figure_reuse: "allowed", message: "" },
    ingest_timeline: [],
    today_reading_minutes: 0,
    ...overrides,
  };
}

function renderWithClient(viewer: ViewerInit) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateQueries = vi.spyOn(client, "invalidateQueries");
  // 実アプリでは ViewerPage 側の useQuery(["viewer", itemId]) がキャッシュへ入れる値。
  // ViewerShell.onStatusChange はこのキャッシュから prevStatus を読むため、単体テストでも
  // 同じキーへ事前投入しておく(qc.getQueryData が undefined のままだと判定できない)。
  client.setQueryData(["viewer", "li_1"], viewer);
  const rendered = render(
    <QueryClientProvider client={client}>
      <ViewerShell itemId="li_1" viewer={viewer} mode="translation" onModeChange={vi.fn()}>
        <div>本文</div>
      </ViewerShell>
    </QueryClientProvider>,
  );
  return { ...rendered, invalidateQueries };
}

// M1 統合ポリッシュ: ビューアからの読了フロー起動(ヘッダの StatusPill 変更経路)。
// LibraryCard と同じ発火規約 — done への PATCH 成功で useFinishReadingStore を開く。
describe("ViewerShell status change → finish-reading flow (1g §2.3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sseHarness.onEvent = undefined;
    useFinishReadingStore.setState({ item: null });
    useViewerStore.setState({
      panelOpen: false,
      tocOpen: false,
      itemId: null,
      revisionId: null,
    });
  });

  test("changing the header StatusPill to 読んだ opens the finish-reading dialog store", async () => {
    const updated = makeLibraryItem({ status: "done" });
    vi.mocked(libraryItemsUpdate).mockResolvedValue({ data: updated } as never);

    renderWithClient(makeViewer());

    const pill = screen.getByRole("button", { name: /読んでいる/ });
    fireEvent.click(pill);
    const menu = await screen.findByRole("menu");
    fireEvent.click(within(menu).getByText("読んだ"));

    await waitFor(() => {
      expect(libraryItemsUpdate).toHaveBeenCalledWith({
        path: { item_id: "li_1" },
        body: { status: "done" },
      });
    });
    await waitFor(() => expect(useFinishReadingStore.getState().item).toEqual(updated));
  });

  test("changing between two non-done statuses does not open the dialog", async () => {
    const updated = makeLibraryItem({ status: "on_hold" });
    vi.mocked(libraryItemsUpdate).mockResolvedValue({ data: updated } as never);

    renderWithClient(makeViewer());

    const pill = screen.getByRole("button", { name: /読んでいる/ });
    fireEvent.click(pill);
    const menu = await screen.findByRole("menu");
    fireEvent.click(within(menu).getByText("保留"));

    await waitFor(() =>
      expect(libraryItemsUpdate).toHaveBeenCalledWith({
        path: { item_id: "li_1" },
        body: { status: "on_hold" },
      }),
    );
    expect(useFinishReadingStore.getState().item).toBeNull();
  });

  test("already-done → done again does not reopen the dialog", async () => {
    const updated = makeLibraryItem({ status: "done" });
    vi.mocked(libraryItemsUpdate).mockResolvedValue({ data: updated } as never);

    renderWithClient(makeViewer({ library_item: makeLibraryItem({ status: "done" }) }));

    const pill = screen.getByRole("button", { name: /読んだ/ });
    fireEvent.click(pill);
    const menu = await screen.findByRole("menu");
    fireEvent.click(within(menu).getByText("読んだ"));

    await act(async () => {
      await Promise.resolve();
    });
    expect(useFinishReadingStore.getState().item).toBeNull();
  });

  test("invalidates only translated PDF caches after the current item's job finishes", async () => {
    const { invalidateQueries } = renderWithClient(
      makeViewer({
        translation: {
          style: "natural",
          set_id: "set-1",
          status: "complete",
          progress_pct: 100,
          section_selection: null,
        },
      }),
    );

    act(() => {
      sseHarness.onEvent?.({
        type: "job.updated",
        data: { job_id: "job-1", library_item_id: "li_1" },
        lastEventId: "1-0",
      });
    });

    await waitFor(() => {
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ["pdf-data", "pap_1", "translated"],
      });
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ["pdf-available", "pap_1", "translated"],
      });
    });
    expect(invalidateQueries).toHaveBeenCalledTimes(2);
  });

  test("ignores job completion events for a different library item", () => {
    const { invalidateQueries } = renderWithClient(makeViewer());

    act(() => {
      sseHarness.onEvent?.({
        type: "job.updated",
        data: { job_id: "job-2", library_item_id: "li_other" },
        lastEventId: "2-0",
      });
    });

    expect(invalidateQueries).not.toHaveBeenCalled();
  });
});
