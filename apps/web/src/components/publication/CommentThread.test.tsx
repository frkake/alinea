import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  publicationCommentsCreate,
  publicationCommentsDelete,
  publicationCommentsHide,
  publicationCommentsRestore,
  publicationCommentsUpdate,
} from "@alinea/api-client";
import type { CommentOut } from "@alinea/api-client";
import { CommentThread, publicationCommentKeys } from "@/components/publication/CommentThread";
import { ToastViewport } from "@/components/ui/Toast";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    publicationCommentsCreate: vi.fn(),
    publicationCommentsUpdate: vi.fn(),
    publicationCommentsDelete: vi.fn(),
    publicationCommentsHide: vi.fn(),
    publicationCommentsRestore: vi.fn(),
  };
});

const SLUG = "rectified-flow-abc123";

function comment(overrides: Partial<CommentOut> = {}): CommentOut {
  return {
    id: "c1",
    block_id: "1",
    parent_id: null,
    body: "とても分かりやすい解説でした。",
    status: "visible",
    created_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-02T00:00:00Z",
    ...overrides,
  };
}

/**
 * CommentOut は投稿者 id を返さない(Task 25 のプライバシー設計)。したがって編集・削除は
 * 認証ユーザー全員に提示し、投稿者本人でなければサーバーが 403 を返す(クライアントは
 * その problem をトーストで伝える)。モデレーション(hide/restore)は isPublisher で描画を絞る。
 */
function renderThread(props: {
  comments: CommentOut[];
  isAuthenticated?: boolean;
  isPublisher?: boolean;
}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <CommentThread
        slug={SLUG}
        blockId="1"
        blockLabel="なぜ直線なのか"
        comments={props.comments}
        isAuthenticated={props.isAuthenticated ?? false}
        isPublisher={props.isPublisher ?? false}
      />
      <ToastViewport />
    </QueryClientProvider>,
  );
  return { client, invalidateSpy };
}

describe("CommentThread (Task 26 ブロック別コメント)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("shows existing visible comments; anonymous users see no post form", () => {
    renderThread({ comments: [comment()], isAuthenticated: false });
    expect(screen.getByText("とても分かりやすい解説でした。")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  test("posting a comment calls the SDK with the block_id and invalidates only this publication's comment query", async () => {
    const user = userEvent.setup();
    vi.mocked(publicationCommentsCreate).mockResolvedValue({ data: comment({ id: "c-new" }) } as never);
    const { invalidateSpy } = renderThread({ comments: [], isAuthenticated: true });

    await user.type(screen.getByRole("textbox"), "新しいコメント");
    await user.click(screen.getByRole("button", { name: /投稿/ }));

    await waitFor(() => {
      expect(publicationCommentsCreate).toHaveBeenCalledWith({
        path: { slug: SLUG },
        body: { block_id: "1", body: "新しいコメント" },
        throwOnError: true,
      });
    });
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: publicationCommentKeys.comments(SLUG) });
    });
    // 他 publication のクエリを invalidate しない(該当スラッグだけ)。
    for (const call of invalidateSpy.mock.calls) {
      const key = (call[0] as { queryKey?: unknown[] } | undefined)?.queryKey;
      if (Array.isArray(key)) {
        expect(key).toEqual(publicationCommentKeys.comments(SLUG));
      }
    }
  });

  test("replying to a root comment sends parent_id (one level only)", async () => {
    const user = userEvent.setup();
    vi.mocked(publicationCommentsCreate).mockResolvedValue({ data: comment({ id: "c-reply", parent_id: "c1" }) } as never);
    renderThread({ comments: [comment()], isAuthenticated: true });

    await user.click(screen.getByRole("button", { name: /返信/ }));
    const replyBox = screen.getByRole("textbox", { name: "返信を入力" });
    await user.type(replyBox, "返信です");
    // 返信フォームの投稿ボタン(返信入力欄の直後)を明示的に押す。
    const replyForm = replyBox.closest("div") as HTMLElement;
    await user.click(within(replyForm).getByRole("button", { name: /投稿/ }));

    await waitFor(() => {
      expect(publicationCommentsCreate).toHaveBeenCalledWith({
        path: { slug: SLUG },
        body: { block_id: "1", body: "返信です", parent_id: "c1" },
        throwOnError: true,
      });
    });
  });

  test("authenticated users can edit and delete a comment (server enforces authorship)", async () => {
    const user = userEvent.setup();
    vi.mocked(publicationCommentsUpdate).mockResolvedValue({ data: comment({ body: "編集後" }) } as never);
    vi.mocked(publicationCommentsDelete).mockResolvedValue({ data: undefined } as never);
    renderThread({ comments: [comment()], isAuthenticated: true });

    await user.click(screen.getByRole("button", { name: /編集/ }));
    const editBox = screen.getByRole("textbox", { name: "コメントを編集" });
    await user.clear(editBox);
    await user.type(editBox, "編集後");
    await user.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => {
      expect(publicationCommentsUpdate).toHaveBeenCalledWith({
        path: { slug: SLUG, comment_id: "c1" },
        body: { body: "編集後" },
        throwOnError: true,
      });
    });

    await user.click(screen.getByRole("button", { name: /削除/ }));
    await waitFor(() => {
      expect(publicationCommentsDelete).toHaveBeenCalledWith({
        path: { slug: SLUG, comment_id: "c1" },
        throwOnError: true,
      });
    });
  });

  test("non-publishers see no hide action", () => {
    renderThread({ comments: [comment()], isAuthenticated: true, isPublisher: false });
    expect(screen.queryByRole("button", { name: /非表示/ })).not.toBeInTheDocument();
  });

  test("the publisher can hide a visible comment and only this publication's query is invalidated", async () => {
    const user = userEvent.setup();
    vi.mocked(publicationCommentsHide).mockResolvedValue({ data: comment({ status: "hidden", body: "" }) } as never);
    const { invalidateSpy } = renderThread({ comments: [comment()], isAuthenticated: true, isPublisher: true });

    await user.click(screen.getByRole("button", { name: /非表示/ }));
    await waitFor(() => {
      expect(publicationCommentsHide).toHaveBeenCalledWith({
        path: { slug: SLUG, comment_id: "c1" },
        throwOnError: true,
      });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: publicationCommentKeys.comments(SLUG) });
  });

  test("hidden comments show a moderation placeholder and a restore action for the publisher", async () => {
    const user = userEvent.setup();
    vi.mocked(publicationCommentsRestore).mockResolvedValue({ data: comment({ status: "visible" }) } as never);
    renderThread({
      comments: [comment({ status: "hidden", body: "" })],
      isAuthenticated: true,
      isPublisher: true,
    });
    expect(screen.getByText(/非表示にされました/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /再表示/ }));
    await waitFor(() => {
      expect(publicationCommentsRestore).toHaveBeenCalledWith({
        path: { slug: SLUG, comment_id: "c1" },
        throwOnError: true,
      });
    });
  });

  test("deleted comments are shown as a tombstone without body and without moderation actions", () => {
    renderThread({
      comments: [comment({ status: "deleted", body: "" })],
      isAuthenticated: true,
      isPublisher: true,
    });
    expect(screen.getByText(/削除されました/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /非表示/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /再表示/ })).not.toBeInTheDocument();
  });
});
