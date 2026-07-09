import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  annotationsCreate,
  annotationsDelete,
  annotationsList,
  annotationsUpdate,
  type Annotation,
} from "@alinea/api-client";
import { AnnotationListPanel } from "@/components/viewer/AnnotationListPanel";
import { ToastViewport } from "@/components/ui/Toast";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    annotationsList: vi.fn(),
    annotationsCreate: vi.fn(async () => ({ data: undefined })),
    annotationsDelete: vi.fn(async () => ({ data: undefined })),
    annotationsUpdate: vi.fn(async () => ({ data: undefined })),
  };
});

function ann(overrides: Partial<Annotation> = {}): Annotation {
  return {
    id: "ann_1",
    kind: "highlight",
    color: "important",
    anchor: {
      revision_id: "rev_1",
      block_id: "blk-1",
      start: 0,
      end: 10,
      quote: "拡散モデルは反復ステップを要する",
      side: "source",
      display: "§1 はじめに",
    },
    comment: null,
    placed: true,
    created_at: "2026-07-06T21:12:00",
    updated_at: "2026-07-06T21:12:00",
    ...overrides,
  };
}

function mockList(items: Annotation[]) {
  const counts = {
    all: items.length,
    important: items.filter((a) => a.color === "important").length,
    question: items.filter((a) => a.color === "question").length,
    idea: items.filter((a) => a.color === "idea").length,
    term: items.filter((a) => a.color === "term").length,
    with_comment: items.filter((a) => a.comment != null).length,
    unplaced: items.filter((a) => !a.placed).length,
  };
  vi.mocked(annotationsList).mockResolvedValue({ data: { items, counts } } as never);
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      {ui}
      <ToastViewport />
    </QueryClientProvider>,
  );
}

// VT-VIEW-08: AnnotationListPanel — フィルタ + 未配置 0 件表示 + Markdown エクスポート導線
describe("AnnotationListPanel (VT-VIEW-08)", () => {
  beforeEach(() => {
    useViewerStore.setState({ itemId: "li_1", requestScroll: vi.fn() });
    vi.clearAllMocks();
  });

  test("renders filter chips with server counts and an unplaced footer + export link", async () => {
    mockList([
      ann({ id: "a1", color: "important" }),
      ann({ id: "a2", color: "question" }),
      ann({ id: "a3", color: "idea" }),
    ]);
    renderWithClient(<AnnotationListPanel />);

    expect(await screen.findByText("すべて")).toBeInTheDocument();
    expect(screen.getByText("重要")).toBeInTheDocument();
    expect(screen.getByText("疑問")).toBeInTheDocument();
    expect(screen.getByText("アイデア")).toBeInTheDocument();
    expect(screen.getByText("コメントのみ")).toBeInTheDocument();
    expect(screen.getByText("未配置 0 件")).toBeInTheDocument();
    const exportLink = screen.getByText("⤓ Markdown エクスポート");
    expect(exportLink).toHaveAttribute("href", "/api/library-items/li_1/export/annotations");
  });

  test("filtering to 重要 shows only important-colored cards", async () => {
    mockList([ann({ id: "a1", color: "important" }), ann({ id: "a2", color: "question" })]);
    renderWithClient(<AnnotationListPanel />);
    expect(await screen.findAllByText(/拡散モデルは反復ステップを要する/)).toHaveLength(2);

    fireEvent.click(screen.getByText("重要"));
    expect(screen.getAllByText(/拡散モデルは反復ステップを要する/)).toHaveLength(1);
  });

  test("コメントのみ filter shows only annotations with a comment", async () => {
    mockList([
      ann({ id: "a1", color: "important", comment: null }),
      ann({ id: "a2", color: "idea", comment: "ここが本質" }),
    ]);
    renderWithClient(<AnnotationListPanel />);
    await screen.findAllByText(/拡散モデルは反復ステップを要する/);
    fireEvent.click(screen.getByText("コメントのみ"));
    expect(screen.getByText("💬 ここが本質")).toBeInTheDocument();
    expect(screen.getAllByText(/拡散モデルは反復ステップを要する/)).toHaveLength(1);
  });

  test("empty filter result shows the フィルタを変更してください empty state", async () => {
    mockList([ann({ id: "a1", color: "important" })]);
    renderWithClient(<AnnotationListPanel />);
    await screen.findByText(/拡散モデルは反復ステップを要する/);
    fireEvent.click(screen.getByText("疑問"));
    expect(screen.getByText("該当する注釈がありません")).toBeInTheDocument();
  });

  test("zero annotations shows the empty library state", async () => {
    mockList([]);
    renderWithClient(<AnnotationListPanel />);
    expect(await screen.findByText("注釈はまだありません")).toBeInTheDocument();
  });

  test("unplaced annotations are dimmed with a 未配置 suffix and cannot jump", async () => {
    const requestScroll = vi.fn();
    useViewerStore.setState({ requestScroll });
    mockList([ann({ id: "a1", placed: false })]);
    renderWithClient(<AnnotationListPanel />);
    await screen.findByText(/· 未配置/);
    fireEvent.click(screen.getByText(/· 未配置/));
    expect(requestScroll).not.toHaveBeenCalled();
  });

  test("clicking a placed card requests a scroll to its block", async () => {
    const requestScroll = vi.fn();
    useViewerStore.setState({ requestScroll });
    mockList([ann({ id: "a1" })]);
    renderWithClient(<AnnotationListPanel />);
    const card = await screen.findByText(/拡散モデルは反復ステップを要する/);
    fireEvent.click(card);
    expect(requestScroll).toHaveBeenCalledWith({ kind: "block", blockId: "blk-1" });
  });

  test("deleting a card removes it optimistically and calls the delete API", async () => {
    mockList([ann({ id: "a1" })]);
    renderWithClient(<AnnotationListPanel />);
    const card = (await screen.findByText(/拡散モデルは反復ステップを要する/)).closest(
      '[role="button"]',
    ) as HTMLElement;
    fireEvent.mouseEnter(card);
    fireEvent.click(screen.getByLabelText("注釈を削除"));
    await waitFor(() => expect(annotationsDelete).toHaveBeenCalledWith({ path: { annotation_id: "a1" } }));

    fireEvent.click(screen.getByText("元に戻す"));
    await waitFor(() =>
      expect(annotationsCreate).toHaveBeenCalledWith({
        path: { item_id: "li_1" },
        body: {
          kind: "highlight",
          color: "important",
          anchor: {
            revision_id: "rev_1",
            block_id: "blk-1",
            start: 0,
            end: 10,
            quote: "拡散モデルは反復ステップを要する",
            side: "source",
          },
          comment: null,
        },
      }),
    );
  });

  // plans/11 §7: 検索ヒット遷移「注釈」(?annotation=)。該当カードへスクロール+一発消費。
  test("scrolls to and flashes the card matching pendingAnnotationId, then consumes it", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    mockList([ann({ id: "a1" }), ann({ id: "a2", color: "question" })]);
    useViewerStore.setState({ pendingAnnotationId: "a2" });
    renderWithClient(<AnnotationListPanel />);
    await screen.findAllByText(/拡散モデルは反復ステップを要する/);

    await waitFor(() => expect(useViewerStore.getState().pendingAnnotationId).toBeNull());
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  test("clicking a comment turns it into an editable textarea, saved via PATCH on blur", async () => {
    mockList([ann({ id: "a1", comment: "元のコメント" })]);
    renderWithClient(<AnnotationListPanel />);
    const comment = await screen.findByText("💬 元のコメント");
    fireEvent.click(comment);
    const textarea = screen.getByLabelText("コメントを編集");
    fireEvent.change(textarea, { target: { value: "更新後のコメント" } });
    fireEvent.blur(textarea);
    await waitFor(() =>
      expect(annotationsUpdate).toHaveBeenCalledWith({
        path: { annotation_id: "a1" },
        body: { comment: "更新後のコメント" },
      }),
    );
  });
});
