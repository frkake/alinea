import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { authMe, publicationCommentsList } from "@alinea/api-client";
import type { PublicArticleOut } from "@alinea/api-client";
import { PublishedArticle } from "@/components/publication/PublishedArticle";
import { ToastViewport } from "@/components/ui/Toast";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    authMe: vi.fn(),
    publicationCommentsList: vi.fn(),
  };
});

function sampleArticle(overrides: Partial<PublicArticleOut> = {}): PublicArticleOut {
  return {
    slug: "rectified-flow-abc123",
    title: "Rectified Flow をやさしく解説",
    visibility: "public",
    snapshot_version: 3,
    noindex: false,
    published_at: "2026-07-01T00:00:00Z",
    paper_meta: {
      title: "Flow Straight and Fast",
      authors: ["Alice Zhang", "Bob Li"],
      arxiv_id: "2209.03003",
      doi: "10.1234/rf",
      venue: "ICLR 2023",
      published_on: "2022-09-07",
      license: "CC BY 4.0",
    },
    blocks: [
      {
        type: "heading",
        block_id: "0",
        content: { heading: { level: 2, text: "なぜ直線なのか" } },
        evidence: [],
      },
      {
        type: "paragraph",
        block_id: "1",
        content: { markdown: "確率フローを**直線化**することで速く生成できる。" },
        evidence: [{ ref: 1, paper_title: "Flow Straight and Fast", section: "§2 手法" }],
      },
      {
        type: "attribution",
        block_id: "2",
        content: { attribution: { text: "この記事はAIが生成した解説であり、元の論文とは別物です。" } },
        evidence: [],
      },
    ],
    ...overrides,
  };
}

function renderArticle(article: PublicArticleOut): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PublishedArticle article={article} />
      <ToastViewport />
    </QueryClientProvider>,
  );
}

describe("PublishedArticle (Task 26 公開ページ本体)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(publicationCommentsList).mockResolvedValue({ data: [] } as never);
  });

  test("renders the article title, sanitized blocks, bibliography, publisher attribution, and published date", async () => {
    vi.mocked(authMe).mockRejectedValue({ status: 401 });
    renderArticle(sampleArticle());

    // タイトル
    expect(screen.getByRole("heading", { name: "Rectified Flow をやさしく解説", level: 1 })).toBeInTheDocument();
    // サニタイズ済みブロック(見出し・段落・出典)
    expect(screen.getByText("なぜ直線なのか")).toBeInTheDocument();
    expect(screen.getByText(/確率フローを/)).toBeInTheDocument();
    // 出典(attribution)ブロックの逐語。
    expect(screen.getByText("この記事はAIが生成した解説であり、元の論文とは別物です。")).toBeInTheDocument();
    // 書誌(paper_meta)
    expect(screen.getByText("Flow Straight and Fast")).toBeInTheDocument();
    expect(screen.getByText(/Alice Zhang/)).toBeInTheDocument();
    expect(screen.getByText(/ICLR 2023/)).toBeInTheDocument();
    expect(screen.getByText(/CC BY 4\.0/)).toBeInTheDocument();
    // 公開者(Alinea 由来である旨)と公開日時
    expect(screen.getByText(/Alinea/)).toBeInTheDocument();
    expect(screen.getByText(/2026/)).toBeInTheDocument();
  });

  test("anonymous visitors see a login CTA and no comment post form", async () => {
    vi.mocked(authMe).mockRejectedValue({ status: 401 });
    renderArticle(sampleArticle());

    await waitFor(() => {
      expect(screen.getAllByRole("link", { name: /ログイン/ }).length).toBeGreaterThan(0);
    });
    const loginLink = screen.getAllByRole("link", { name: /ログイン/ })[0];
    expect(loginLink).toHaveAttribute("href", expect.stringContaining("/login"));
    // 未ログインでは投稿フォーム(textbox)を出さない。
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  test("authenticated visitors get a comment post form per block", async () => {
    vi.mocked(authMe).mockResolvedValue({
      data: {
        user: { id: "u1", display_name: "私", email: "me@example.com", providers: [], created_at: "" },
        unread_notifications: 0,
      },
    } as never);
    renderArticle(sampleArticle());

    await waitFor(() => {
      expect(screen.getAllByRole("textbox").length).toBeGreaterThan(0);
    });
    // ログイン CTA は出さない。
    expect(screen.queryByRole("link", { name: /ログイン/ })).not.toBeInTheDocument();
  });

  test("unlisted snapshots still render the body (noindex is handled by metadata, not this component)", async () => {
    vi.mocked(authMe).mockRejectedValue({ status: 401 });
    renderArticle(sampleArticle({ visibility: "unlisted", noindex: true }));
    expect(screen.getByRole("heading", { name: "Rectified Flow をやさしく解説", level: 1 })).toBeInTheDocument();
  });
});
