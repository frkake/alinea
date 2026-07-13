import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  chatListMessages,
  chatListThreads,
  type AnchorRef,
  type ChatMessage as ChatMessageData,
} from "@alinea/api-client";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { ChatComposer, CHAT_DISCLAIMER } from "@/components/chat/ChatComposer";
import { QuickActionChips } from "@/components/chat/QuickActionChips";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    chatListThreads: vi.fn(),
    chatListMessages: vi.fn(),
  };
});

function anchorRef(overrides: Partial<AnchorRef> = {}): AnchorRef {
  return {
    revision_id: "rev_1",
    block_id: "blk-1",
    start: null,
    end: null,
    quote: null,
    side: "source",
    display: "¶2",
    ...overrides,
  };
}

function assistantMessage(overrides: Partial<ChatMessageData> = {}): ChatMessageData {
  return {
    id: "msg-a1",
    role: "assistant",
    blocks: [{ type: "markdown", text: "式(5)は回帰です。", evidence: [] }],
    context_anchors: [],
    quick_action: null,
    status: "complete",
    error: null,
    created_at: "2026-07-07T12:00:00Z",
    ...overrides,
  };
}

// VT-VIEW-09: アシスタント回答に「AI生成」バッジと根拠チップが出る
describe("ChatMessage assistant (VT-VIEW-09)", () => {
  test("shows AI generated badge and inline evidence chip", () => {
    const onEvidenceJump = vi.fn();
    const anchor = anchorRef();
    render(
      <ChatMessage
        message={assistantMessage({
          blocks: [
            {
              type: "markdown",
              text: "結局 [[ev:1]] の回帰に帰着します。",
              evidence: [{ ref: 1, display: "¶2", anchor }],
            },
          ],
        })}
        onEvidenceJump={onEvidenceJump}
      />,
    );
    expect(screen.getByText("AI生成")).toBeInTheDocument();
    fireEvent.click(screen.getByText("¶2"));
    expect(onEvidenceJump).toHaveBeenCalledWith(anchor);
  });

  test("renders Markdown blocks with headings, GFM tables, and display math", () => {
    const { container } = render(
      <ChatMessage
        message={assistantMessage({
          blocks: [
            {
              type: "markdown",
              text: [
                "## 解析結果",
                "",
                "| 指標 | 値 |",
                "| --- | --- |",
                "| 損失 | $L$ |",
                "",
                "同じ行の表示数式 $$x^2$$ です。",
              ].join("\n"),
              evidence: [],
            },
          ],
        })}
      />,
    );

    expect(screen.getByRole("heading", { name: "解析結果" })).toHaveProperty("tagName", "H2");
    const table = container.querySelector(".alinea-chat-table-scroll table");
    expect(table).toHaveTextContent("損失");
    expect(table?.querySelector(".katex")).not.toBeNull();
    expect(container.querySelectorAll(".alinea-chat-math-block .katex-display")).toHaveLength(1);
  });

  test("renders outside-knowledge aside Markdown with its label", () => {
    const { container } = render(
      <ChatMessage
        message={assistantMessage({
          blocks: [
            { type: "markdown", text: "本文の要点です。", evidence: [] },
            { type: "aside", label: "outside_knowledge", text: "**一般則** は $x^2$ です。" },
          ],
        })}
      />,
    );
    expect(screen.getByText("論文外の知識")).toBeInTheDocument();
    expect(screen.getByText("一般則")).toHaveProperty("tagName", "STRONG");
    expect(container.querySelector(".katex")).not.toBeNull();
  });
});

describe("ChatMessage user Markdown regression", () => {
  test("keeps user Markdown literal without strong text or KaTeX", () => {
    const { container } = render(
      <ChatMessage
        message={assistantMessage({
          role: "user",
          blocks: [{ type: "markdown", text: "**そのまま** $x$", evidence: [] }],
        })}
      />,
    );

    expect(screen.getByText("**そのまま** $x$")).toBeInTheDocument();
    expect(container.querySelector("strong")).toBeNull();
    expect(container.querySelector(".katex")).toBeNull();
  });
});

// VT-VIEW-07: SSE ストリーミング表示(生成中インジケータ / 逐次テキスト)
describe("ChatMessage streaming (VT-VIEW-07)", () => {
  test("shows typing indicator while streaming with no content yet", () => {
    render(<ChatMessage message={assistantMessage({ blocks: [] })} streaming />);
    expect(screen.getByLabelText("生成中")).toBeInTheDocument();
    // ストリーミング中はアクション行を出さない
    expect(screen.queryByText("再生成")).toBeNull();
  });

  test("renders accumulated delta text during streaming", () => {
    render(
      <ChatMessage
        message={assistantMessage({
          blocks: [{ type: "markdown", text: "整流フローは", evidence: [] }],
        })}
        streaming
      />,
    );
    expect(screen.getByText("整流フローは")).toBeInTheDocument();
  });
});

// VT-VIEW-12: 回答アクション(再生成・コピー)
describe("ChatMessage actions (VT-VIEW-12)", () => {
  test("regenerate and copy call their handlers with the message", () => {
    const onRegenerate = vi.fn();
    const onCopy = vi.fn();
    const msg = assistantMessage();
    render(<ChatMessage message={msg} onRegenerate={onRegenerate} onCopy={onCopy} />);
    fireEvent.click(screen.getByText("再生成"));
    expect(onRegenerate).toHaveBeenCalledWith("msg-a1");
    fireEvent.click(screen.getByText("コピー"));
    expect(onCopy).toHaveBeenCalledWith(msg);
  });

  // docs/05 §8: 「↑ メモに保存」でチャット回答をメモへ昇格。
  test("↑ メモに保存 calls onSaveToNote with the message id", () => {
    const onSaveToNote = vi.fn();
    const msg = assistantMessage();
    render(<ChatMessage message={msg} onSaveToNote={onSaveToNote} />);
    fireEvent.click(screen.getByText("↑ メモに保存"));
    expect(onSaveToNote).toHaveBeenCalledWith("msg-a1");
  });

  test("error message shows retry link and calls onRetry", () => {
    const onRetry = vi.fn();
    render(
      <ChatMessage
        message={assistantMessage({
          status: "error",
          blocks: [],
          error: {
            type: "about:blank",
            title: "回答の生成に失敗しました",
            status: 502,
            code: "provider_error",
          },
        })}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText(/回答の生成に失敗しました/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("再試行"));
    expect(onRetry).toHaveBeenCalledWith("msg-a1");
  });
});

// VT-VIEW-10: 入力エリア(免責文固定・送信活性制御)
describe("ChatComposer (VT-VIEW-10)", () => {
  test("shows the fixed disclaimer verbatim", () => {
    render(<ChatComposer onSend={vi.fn()} />);
    expect(screen.getByText(CHAT_DISCLAIMER)).toBeInTheDocument();
  });

  test("send is disabled when empty, enabled after typing, and calls onSend", () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} />);
    const send = screen.getByLabelText("送信");
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText("この論文について質問"), {
      target: { value: "この式の意味は?" },
    });
    expect(send).not.toBeDisabled();
    fireEvent.click(send);
    expect(onSend).toHaveBeenCalledWith("この式の意味は?");
  });

  test("does not send while streaming (disabled)", () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} disabled />);
    const send = screen.getByLabelText("送信");
    expect(send).toBeDisabled();
    fireEvent.click(send);
    expect(onSend).not.toHaveBeenCalled();
  });
});

// VT-VIEW-11: 定型チップ 5 種
describe("QuickActionChips (VT-VIEW-11)", () => {
  const labels = ["3行要約", "初心者向け解説", "貢献と限界", "実験設定の整理", "実装の要点"];

  test("renders the 5 fixed suggestion chips in order", () => {
    render(<QuickActionChips onPick={vi.fn()} />);
    for (const label of labels) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  test("picking a chip fires the matching quick_action", () => {
    const onPick = vi.fn();
    render(<QuickActionChips onPick={onPick} />);
    fireEvent.click(screen.getByText("3行要約"));
    expect(onPick).toHaveBeenCalledWith("summary_3line");
  });

  test("chips are disabled while streaming", () => {
    render(<QuickActionChips onPick={vi.fn()} disabled />);
    for (const label of labels) {
      expect(screen.getByText(label)).toBeDisabled();
    }
  });
});

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// M1 統合ポリッシュ: チャットのディープリンク(?thread=/?message=。plans/11 §7・plans/09 1e §5.3-4)。
// ViewerPage が viewer-store に積んだ pendingChatThreadId/pendingChatMessageId を
// ChatPanel が消費し、該当スレッドを選択+メッセージへスクロールする。
describe("ChatPanel deep link (M1 統合ポリッシュ)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    useViewerStore.setState({
      pendingChatThreadId: null,
      pendingChatMessageId: null,
    });
    vi.mocked(chatListThreads).mockResolvedValue({
      data: {
        items: [
          { id: "th-main", title: "メイン", is_main: true, created_at: "2026-07-01T00:00:00Z" },
          {
            id: "th-sub",
            title: "サブスレッド",
            is_main: false,
            created_at: "2026-07-02T00:00:00Z",
          },
        ],
      },
    } as never);
    vi.mocked(chatListMessages).mockImplementation(async ({ path }) => {
      const threadId = path.thread_id;
      if (threadId === "th-sub") {
        return {
          data: {
            items: [
              {
                id: "msg-2",
                role: "assistant",
                blocks: [{ type: "markdown", text: "サブスレッドの回答です。", evidence: [] }],
                context_anchors: [],
                quick_action: null,
                status: "complete",
                error: null,
                created_at: "2026-07-02T00:00:00Z",
              },
              {
                id: "msg-1",
                role: "user",
                blocks: [{ type: "markdown", text: "質問です。", evidence: [] }],
                context_anchors: [],
                quick_action: null,
                status: "complete",
                error: null,
                created_at: "2026-07-02T00:00:01Z",
              },
            ],
          },
        } as never;
      }
      return { data: { items: [] } } as never;
    });
  });

  test("selects the ?thread= target instead of the main thread and scrolls to ?message=, then consumes both", async () => {
    useViewerStore.setState({ pendingChatThreadId: "th-sub", pendingChatMessageId: "msg-2" });
    renderWithClient(<ChatPanel itemId="li_1" />);

    expect(await screen.findByText("サブスレッド")).toBeInTheDocument();
    await screen.findByText("サブスレッドの回答です。");

    await waitFor(() => {
      expect(useViewerStore.getState().pendingChatThreadId).toBeNull();
      expect(useViewerStore.getState().pendingChatMessageId).toBeNull();
    });
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  test("falls back to the main thread when there is no deep-link target", async () => {
    renderWithClient(<ChatPanel itemId="li_1" />);
    expect(await screen.findByText("メイン")).toBeInTheDocument();
  });
});
