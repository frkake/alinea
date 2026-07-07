import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { notesCreate, notesDelete, notesList, notesUpdate, type Note } from "@yakudoku/api-client";
import { NotesPanel } from "@/components/viewer/NotesPanel";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@yakudoku/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@yakudoku/api-client")>();
  return {
    ...actual,
    notesList: vi.fn(),
    notesCreate: vi.fn(async () => ({ data: undefined })),
    notesUpdate: vi.fn(async () => ({ data: undefined })),
    notesDelete: vi.fn(async () => ({ data: undefined })),
  };
});

function note(overrides: Partial<Note> = {}): Note {
  return {
    id: "note_1",
    content_md: "これはメモです。",
    source: null,
    anchors: [],
    created_at: "2026-07-06T21:12:00",
    updated_at: "2026-07-06T21:12:00",
    ...overrides,
  };
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("NotesPanel", () => {
  beforeEach(() => {
    useViewerStore.setState({ itemId: "li_1", requestScroll: vi.fn() });
    vi.clearAllMocks();
  });

  test("empty state prompts to write a note", async () => {
    vi.mocked(notesList).mockResolvedValue({ data: { items: [] } } as never);
    renderWithClient(<NotesPanel />);
    expect(await screen.findByText("メモはまだありません")).toBeInTheDocument();
  });

  test("lists notes and shows a チャットより badge for chat-sourced notes", async () => {
    vi.mocked(notesList).mockResolvedValue({
      data: { items: [note({ source: { chat_message_id: "msg_1" } })] },
    } as never);
    renderWithClient(<NotesPanel />);
    expect(await screen.findByText("これはメモです。")).toBeInTheDocument();
    expect(screen.getByText("チャットより")).toBeInTheDocument();
  });

  test("creating a note calls the API and clears the draft", async () => {
    vi.mocked(notesList).mockResolvedValue({ data: { items: [] } } as never);
    renderWithClient(<NotesPanel />);
    await screen.findByText("メモはまだありません");
    const textarea = screen.getByLabelText("新しいメモ");
    fireEvent.change(textarea, { target: { value: "新しいメモ内容" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() =>
      expect(notesCreate).toHaveBeenCalledWith({
        path: { item_id: "li_1" },
        body: { content_md: "新しいメモ内容" },
      }),
    );
    expect((textarea as HTMLTextAreaElement).value).toBe("");
  });

  test("editing a note saves via PATCH", async () => {
    vi.mocked(notesList).mockResolvedValue({ data: { items: [note()] } } as never);
    renderWithClient(<NotesPanel />);
    await screen.findByText("これはメモです。");
    fireEvent.click(screen.getByText("編集"));
    const textarea = screen.getByLabelText("メモを編集");
    fireEvent.change(textarea, { target: { value: "編集後のメモ" } });
    fireEvent.blur(textarea);
    await waitFor(() =>
      expect(notesUpdate).toHaveBeenCalledWith({
        path: { note_id: "note_1" },
        body: { content_md: "編集後のメモ" },
      }),
    );
  });

  test("deleting a note calls the delete API", async () => {
    vi.mocked(notesList).mockResolvedValue({ data: { items: [note()] } } as never);
    renderWithClient(<NotesPanel />);
    await screen.findByText("これはメモです。");
    fireEvent.click(screen.getByText("削除"));
    await waitFor(() => expect(notesDelete).toHaveBeenCalledWith({ path: { note_id: "note_1" } }));
  });

  // plans/11 §7: 検索ヒット遷移「メモ」(?note=)。該当メモへスクロール+一発消費。
  test("scrolls to and flashes the note matching pendingNoteId, then consumes it", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(notesList).mockResolvedValue({
      data: { items: [note({ id: "note_1" }), note({ id: "note_2", content_md: "2つ目" })] },
    } as never);
    useViewerStore.setState({ pendingNoteId: "note_2" });
    renderWithClient(<NotesPanel />);
    await screen.findByText("2つ目");

    await waitFor(() => expect(useViewerStore.getState().pendingNoteId).toBeNull());
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  test("anchor chips request a scroll to the source block", async () => {
    const requestScroll = vi.fn();
    useViewerStore.setState({ requestScroll });
    vi.mocked(notesList).mockResolvedValue({
      data: {
        items: [
          note({
            anchors: [
              {
                revision_id: "rev_1",
                block_id: "blk-9",
                start: null,
                end: null,
                quote: null,
                side: "source",
                display: "§2.1",
              },
            ],
          }),
        ],
      },
    } as never);
    renderWithClient(<NotesPanel />);
    const chip = await screen.findByText("§2.1");
    fireEvent.click(chip);
    expect(requestScroll).toHaveBeenCalledWith({ kind: "block", blockId: "blk-9" });
  });
});
