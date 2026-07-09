import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { articlesGenerate, articlesGet, type ArticleOut } from "@alinea/api-client";
import { ArticlePane } from "@/components/viewer/article/ArticlePane";
import { useViewerStore } from "@/stores/viewer-store";
import { ToastViewport } from "@/components/ui/Toast";
import { MockEventSource, firstEventSource } from "@/components/viewer/article/test-utils";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    articlesGet: vi.fn(),
    articlesGenerate: vi.fn(),
  };
});

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn(), back: vi.fn() }),
}));

// jsdom は IntersectionObserver を実装しない(先頭可視ブロック追従の観測用スタブ)。
class FakeIntersectionObserver {
  observe(): void {}
  disconnect(): void {}
  unobserve(): void {}
}
vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);

function article(overrides: Partial<ArticleOut> = {}): ArticleOut {
  return {
    id: "art_1",
    library_item_id: "li_1",
    title: "Rectified Flow を読む",
    preset: "beginner",
    include_math: false,
    version: 1,
    generated_at: "2026-07-06T00:00:00Z",
    disclaimer: "訳文・メモ・チャット履歴から自動構成 · 2026-07-06 · 元の論文とは別物です — 根拠チップから原文へ",
    overview_figure: null,
    blocks: [
      {
        id: "ablk_1",
        type: "heading",
        content: { heading: { level: 2, text: "なぜ「直線」なのか" } },
        evidence: [],
        origin: "ai",
        locked: false,
      },
      {
        id: "ablk_2",
        type: "quote_source",
        content: {
          quote: {
            text_en: "Straight paths are computationally attractive.",
            anchor: { revision_id: "rev_1", block_id: "blk-2-2-p1", display: "§2.2 ¶3" },
          },
        },
        evidence: [],
        origin: "ai",
        locked: false,
      },
      {
        id: "ablk_9",
        type: "attribution",
        content: { attribution: { text: "出典: …" } },
        evidence: [],
        origin: "ai",
        locked: true,
      },
    ],
    ...overrides,
  };
}

function renderPane(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <ArticlePane libraryItemId="li_1" revisionId="rev_1" />
        <ToastViewport />
      </QueryClientProvider>,
    ),
  };
}

function notFound(): Partial<{ status: number; code: string; title: string }> {
  return { status: 404, code: "not_found", title: "見つかりません" };
}

describe("ArticlePane", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.reset();
    useViewerStore.setState({
      pendingScrollTarget: null,
      panelOpen: false,
      activeTab: "chat",
    });
  });

  test("shows the loading skeleton while the article query is pending", () => {
    vi.mocked(articlesGet).mockReturnValue(new Promise(() => {}) as never);
    renderPane();
    expect(screen.getByLabelText("記事を読み込み中")).toBeInTheDocument();
  });

  test("shows a generic error state with retry on non-404 errors", async () => {
    vi.mocked(articlesGet).mockRejectedValue({ status: 500, title: "内部エラー" });
    renderPane();
    expect(await screen.findByText("読み込みに失敗しました")).toBeInTheDocument();
    expect(screen.getByText("再試行")).toBeInTheDocument();
  });

  test("shows the generate CTA on 404, generates on submit, and renders the article once the job completes", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const user = userEvent.setup();
    vi.mocked(articlesGet).mockRejectedValueOnce(notFound()).mockResolvedValueOnce({ data: article() } as never);
    vi.mocked(articlesGenerate).mockResolvedValue({ data: { job_id: "job_1" } } as never);

    renderPane();
    expect(await screen.findByText("この論文の記事はまだありません")).toBeInTheDocument();

    await user.click(screen.getByText("✦ 記事を生成"));
    expect(articlesGenerate).toHaveBeenCalledWith({
      path: { item_id: "li_1" },
      body: { preset: "beginner", include_math: false },
      throwOnError: true,
    });
    expect(await screen.findByText(/✦ 記事を生成しています…/)).toBeInTheDocument();

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    act(() => {
      firstEventSource().dispatch("done", { job_id: "job_1", status: "succeeded", result: {} });
    });

    expect(await screen.findByText("Rectified Flow を読む")).toBeInTheDocument();
  });

  test("renders the article title, meta row, and blocks (heading/quote/attribution)", async () => {
    vi.mocked(articlesGet).mockResolvedValue({ data: article() } as never);
    renderPane();
    expect(await screen.findByText("Rectified Flow を読む")).toBeInTheDocument();
    expect(screen.getByText("AI生成")).toBeInTheDocument();
    expect(screen.getByText("なぜ「直線」なのか")).toBeInTheDocument();
    expect(screen.getByText(/Straight paths are computationally attractive/)).toBeInTheDocument();
    expect(screen.getByText("自動挿入 · 削除不可")).toBeInTheDocument();
  });

  test("原文で見る → jumps to mode=source with the anchor's block queued for scroll", async () => {
    const user = userEvent.setup();
    vi.mocked(articlesGet).mockResolvedValue({ data: article() } as never);
    renderPane();
    await screen.findByText("Rectified Flow を読む");

    await user.click(screen.getByText("原文で見る →"));
    expect(replaceMock).toHaveBeenCalledWith("/papers/li_1?mode=source", { scroll: false });
    expect(useViewerStore.getState().pendingScrollTarget).toEqual({ kind: "block", blockId: "blk-2-2-p1" });
  });
});
