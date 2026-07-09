import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { articlesGet, articlesRegenerate, type ArticleOut } from "@alinea/api-client";
import { ArticleRegenerateButton } from "@/components/viewer/article/ArticleRegenerateButton";
import { useViewerStore } from "@/stores/viewer-store";
import { ToastViewport } from "@/components/ui/Toast";
import { MockEventSource, firstEventSource } from "@/components/viewer/article/test-utils";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, articlesGet: vi.fn(), articlesRegenerate: vi.fn() };
});

function article(overrides: Partial<ArticleOut> = {}): ArticleOut {
  return {
    id: "art_1",
    library_item_id: "li_1",
    title: "T",
    preset: "beginner",
    include_math: false,
    version: 1,
    generated_at: "2026-07-06T00:00:00Z",
    disclaimer: "d",
    overview_figure: null,
    blocks: [],
    ...overrides,
  };
}

function renderButton() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ArticleRegenerateButton itemId="li_1" />
      <ToastViewport />
    </QueryClientProvider>,
  );
}

describe("ArticleRegenerateButton (1h §4.2-7, §5.3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.reset();
    useViewerStore.setState({ articleRegenerating: false, articleRegenProgressPct: 0 });
  });

  test("renders nothing while the article hasn't loaded (e.g. 404/未生成)", async () => {
    vi.mocked(articlesGet).mockRejectedValue({ status: 404 });
    const { container } = renderButton();
    await waitFor(() => expect(articlesGet).toHaveBeenCalled());
    expect(container.querySelector("button")).toBeNull();
  });

  test("renders the button once the article is loaded and opens the popover with current preset", async () => {
    const user = userEvent.setup();
    vi.mocked(articlesGet).mockResolvedValue({ data: article({ preset: "researcher", include_math: true }) } as never);
    renderButton();
    const btn = await screen.findByText("指示つき再生成");
    await user.click(btn);
    expect(screen.getByRole("radio", { name: "研究者向け" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "数式を含める" })).toHaveAttribute("aria-checked", "true");
  });

  test("submitting starts a regenerate job and mirrors progress into the shared viewer-store banner state", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const user = userEvent.setup();
    vi.mocked(articlesGet).mockResolvedValue({ data: article() } as never);
    vi.mocked(articlesRegenerate).mockResolvedValue({ data: { job_id: "job_1" } } as never);
    renderButton();

    await user.click(await screen.findByText("指示つき再生成"));
    await user.click(screen.getByText("✦ 再生成"));

    expect(articlesRegenerate).toHaveBeenCalledWith({
      path: { article_id: "art_1" },
      body: { instruction: undefined, preset: "beginner", include_math: false },
      throwOnError: true,
    });
    expect(useViewerStore.getState().articleRegenerating).toBe(true);

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    act(() => {
      firstEventSource().dispatch("progress", { job_id: "job_1", status: "running", progress_pct: 55 });
    });
    expect(useViewerStore.getState().articleRegenProgressPct).toBe(55);

    act(() => {
      firstEventSource().dispatch("done", { job_id: "job_1", status: "succeeded", result: {} });
    });
    await waitFor(() => expect(useViewerStore.getState().articleRegenerating).toBe(false));
    expect(await screen.findByText(/✓ 記事を再生成しました/)).toBeInTheDocument();
  });

  test("429 quota_exceeded shows the fixed quota-exceeded toast and clears regenerating state", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const user = userEvent.setup();
    vi.mocked(articlesGet).mockResolvedValue({ data: article() } as never);
    vi.mocked(articlesRegenerate).mockRejectedValue({ status: 429, code: "quota_exceeded" });
    renderButton();

    await user.click(await screen.findByText("指示つき再生成"));
    await user.click(screen.getByText("✦ 再生成"));

    expect(await screen.findByText("今月の生成クォータを使い切りました")).toBeInTheDocument();
    expect(useViewerStore.getState().articleRegenerating).toBe(false);
  });
});
