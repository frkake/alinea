import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  articlesBlockRewrite,
  figuresRegenerateExplainer,
  type ArticleBlockOut,
} from "@alinea/api-client";
import { ArticleBlockItem } from "@/components/viewer/article/ArticleBlockItem";
import { articleKeys } from "@/components/viewer/article/queries";
import type { Article } from "@/components/viewer/article/types";
import { MockEventSource, firstEventSource } from "@/components/viewer/article/test-utils";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    articlesBlockRewrite: vi.fn(),
    figuresRegenerateExplainer: vi.fn(),
  };
});

function headingBlock(overrides: Partial<ArticleBlockOut> = {}): ArticleBlockOut {
  return {
    id: "ablk_1",
    type: "heading",
    content: { heading: { level: 2, text: "なぜ「直線」なのか" } },
    evidence: [
      { ref: 1, display: "§1", anchor: { revision_id: "rev_1", block_id: "blk-1", display: "§1" } },
    ],
    origin: "ai",
    locked: false,
    ...overrides,
  };
}

function attributionBlock(): ArticleBlockOut {
  return {
    id: "ablk_9",
    type: "attribution",
    content: { attribution: { text: "出典: …" } },
    evidence: [],
    origin: "ai",
    locked: true,
  };
}

function renderBlock(
  block: ArticleBlockOut,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <ArticleBlockItem
          libraryItemId="li_1"
          articleId="art_1"
          block={block}
          revisionId="rev_1"
          includeMath={false}
          onJumpToAnchor={vi.fn()}
        />
      </QueryClientProvider>,
    ),
  };
}

// VT-VIEW-15: ホバーで「✦ 書き直し指示 / 再生成 / 根拠を表示」の 3 操作が出現する
describe("ArticleBlockItem hover toolbar (VT-VIEW-15)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.reset();
  });

  test("hovering shows the 3-action toolbar after the delay; leaving hides it", async () => {
    const { container } = renderBlock(headingBlock());
    const wrapper = container.querySelector("[data-block-id='ablk_1']") as HTMLElement;
    expect(screen.queryByText("✦ 書き直し指示")).not.toBeInTheDocument();

    fireEvent.mouseEnter(wrapper);
    expect(await screen.findByText("✦ 書き直し指示")).toBeInTheDocument();
    expect(screen.getByText("再生成")).toBeInTheDocument();
    expect(screen.getByText("根拠を表示")).toBeInTheDocument();

    fireEvent.mouseLeave(wrapper);
    await waitFor(() => expect(screen.queryByText("✦ 書き直し指示")).not.toBeInTheDocument());
  });

  test("hides 根拠を表示 when the block has no evidence", async () => {
    const { container } = renderBlock(headingBlock({ evidence: [] }));
    const wrapper = container.querySelector("[data-block-id='ablk_1']") as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    expect(await screen.findByText("✦ 書き直し指示")).toBeInTheDocument();
    expect(screen.queryByText("根拠を表示")).not.toBeInTheDocument();
  });

  test("locked blocks (attribution) never show the toolbar on hover", async () => {
    const { container } = renderBlock(attributionBlock());
    const wrapper = container.querySelector("[data-block-id='ablk_9']") as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    await new Promise((r) => setTimeout(r, 150));
    expect(screen.queryByText("✦ 書き直し指示")).not.toBeInTheDocument();
  });

  test("attribution blocks render source metadata as chips", () => {
    renderBlock({
      ...attributionBlock(),
      content: {
        attribution: {
          text: '出典: Xingchao Liu, Chengyue Gong, Qiang Liu. "Flow Straight and Fast." ICLR. arXiv:2209.03003 (2023) · ライセンス CC BY 4.0',
        },
      },
    });

    expect(screen.getByText("Flow Straight and Fast")).toBeInTheDocument();
    expect(screen.getByText("著者")).toBeInTheDocument();
    expect(screen.getByText("Xingchao Liu, Chengyue Gong, Qiang Liu")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "arXiv:2209.03003" })).toHaveAttribute(
      "href",
      "https://arxiv.org/abs/2209.03003",
    );
    expect(screen.getByText("ライセンス")).toBeInTheDocument();
    expect(screen.getByText("CC BY 4.0")).toBeInTheDocument();
  });

  test("focusing the block also shows the toolbar (keyboard a11y)", async () => {
    const { container } = renderBlock(headingBlock());
    const wrapper = container.querySelector("[data-block-id='ablk_1']") as HTMLElement;
    fireEvent.focus(wrapper);
    expect(await screen.findByText("✦ 書き直し指示")).toBeInTheDocument();
  });

  test("clicking 再生成 calls articlesBlockRewrite with no instruction", async () => {
    const user = userEvent.setup();
    vi.mocked(articlesBlockRewrite).mockResolvedValue({ data: { job_id: "job_1" } } as never);
    const { container } = renderBlock(headingBlock());
    const wrapper = container.querySelector("[data-block-id='ablk_1']") as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    await user.click(await screen.findByText("再生成"));
    expect(articlesBlockRewrite).toHaveBeenCalledWith({
      path: { article_id: "art_1", block_id: "ablk_1" },
      body: { instruction: undefined },
      throwOnError: true,
    });
  });

  test("submitting the rewrite popover calls articlesBlockRewrite with the instruction", async () => {
    const user = userEvent.setup();
    vi.mocked(articlesBlockRewrite).mockResolvedValue({ data: { job_id: "job_1" } } as never);
    const { container } = renderBlock(headingBlock());
    const wrapper = container.querySelector("[data-block-id='ablk_1']") as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    await user.click(await screen.findByText("✦ 書き直し指示"));
    await user.type(screen.getByLabelText("書き直し指示"), "もっと平易に");
    await user.click(screen.getByText("✦ 書き直す"));
    expect(articlesBlockRewrite).toHaveBeenCalledWith({
      path: { article_id: "art_1", block_id: "ablk_1" },
      body: { instruction: "もっと平易に" },
      throwOnError: true,
    });
  });

  test("explainer_figure blocks call figuresRegenerateExplainer instead of articlesBlockRewrite", async () => {
    const user = userEvent.setup();
    vi.mocked(figuresRegenerateExplainer).mockResolvedValue({ data: { job_id: "job_2" } } as never);
    const block: ArticleBlockOut = {
      id: "ablk_2",
      type: "explainer_figure",
      content: {
        explainer: { figure_id: "exf_1", image_url: "https://x/img.png", caption: "解説" },
      },
      evidence: [],
      origin: "ai",
      locked: false,
    };
    const { container } = renderBlock(block);
    const wrapper = container.querySelector("[data-block-id='ablk_2']") as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    await user.click(await screen.findByText("再生成"));
    expect(figuresRegenerateExplainer).toHaveBeenCalledWith({
      path: { figure_id: "exf_1" },
      body: { instruction: undefined },
      throwOnError: true,
    });
    expect(articlesBlockRewrite).not.toHaveBeenCalled();
  });

  test("on job done, patches only the rewritten block in the ['article', liId] cache", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const user = userEvent.setup();
    vi.mocked(articlesBlockRewrite).mockResolvedValue({ data: { job_id: "job_1" } } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const other: ArticleBlockOut = {
      ...headingBlock(),
      id: "ablk_other",
      content: { heading: { level: 2, text: "other" } },
    };
    const seedArticle = { blocks: [headingBlock(), other] } as unknown as Article;
    client.setQueryData(articleKeys.article("li_1"), seedArticle);

    const { container } = renderBlock(headingBlock(), client);
    const wrapper = container.querySelector("[data-block-id='ablk_1']") as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    await user.click(await screen.findByText("再生成"));

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const updatedBlock = { ...headingBlock(), content: { heading: { level: 2, text: "更新後" } } };
    act(() => {
      firstEventSource().dispatch("done", {
        job_id: "job_1",
        status: "succeeded",
        result: { block: updatedBlock },
      });
    });

    await waitFor(() => {
      const cached = client.getQueryData<Article>(articleKeys.article("li_1"));
      expect(cached?.blocks.find((b) => b.id === "ablk_1")?.content.heading?.text).toBe("更新後");
      expect(cached?.blocks.find((b) => b.id === "ablk_other")).toBeTruthy();
    });
  });
});
