import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { libraryItemsUpdate, notesCreate, type LibraryItemSummary } from "@alinea/api-client";
import { FinishReadingDialog } from "@/components/library/FinishReadingDialog";
import { useToast } from "@/components/ui/Toast";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    libraryItemsUpdate: vi.fn(),
    notesCreate: vi.fn(),
  };
});

vi.mock("@/components/ui/Toast", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/ui/Toast")>();
  return { ...actual, useToast: vi.fn() };
});

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

function makeItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id: "li_1",
    paper: {
      id: "pap_1",
      title: "Flow Straight and Fast",
      authors: ["Xingchang Liu"],
      authors_short: "Liu",
      venue: "ICLR 2023",
      year: 2023,
      arxiv_id: "2209.03003",
      license: "cc-by",
      visibility: "public",
      abstract: "",
    },
    status: "done",
    priority: null,
    deadline: null,
    tags: [],
    suggested_tags: [],
    quality_level: "A",
    source: "arxiv",
    progress_pct: 100,
    comprehension: null,
    importance: null,
    reading_seconds_total: 11_520, // 3h12m
    one_line_note: null,
    summary_3line: null,
    added_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-06T00:00:00Z",
    finished_at: "2026-07-06T12:00:00Z",
    ...overrides,
  };
}

function renderDialog(item: LibraryItemSummary, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <FinishReadingDialog item={item} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { ...utils, onClose };
}

describe("FinishReadingDialog (M1-06 / 1g)", () => {
  const toastMock = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useToast).mockReturnValue(toastMock);
  });

  test("renders the header title and meta line with formatted date/duration", () => {
    renderDialog(makeItem());
    expect(screen.getByText("「読んだ」にしました")).toBeInTheDocument();
    expect(screen.getByText("読了日 2026-07-06 · 累計読書時間 3時間12分(自動記録)")).toBeInTheDocument();
  });

  test("omits the duration segment when reading_seconds_total is 0", () => {
    renderDialog(makeItem({ reading_seconds_total: 0 }));
    expect(screen.getByText("読了日 2026-07-06(自動記録)")).toBeInTheDocument();
  });

  test("shows 未選択 for comprehension when null", () => {
    renderDialog(makeItem({ comprehension: null }));
    expect(screen.getByText("未選択")).toBeInTheDocument();
  });

  test("prefills comprehension/importance/note from the item", () => {
    renderDialog(
      makeItem({ comprehension: 4, importance: "high", one_line_note: "既存のメモ" }),
    );
    expect(screen.getByText("4/5 — だいたい追えた")).toBeInTheDocument();
    expect(screen.getByLabelText("ひとことメモ")).toHaveValue("既存のメモ");
  });

  test("clicking a comprehension dot selects it; re-clicking clears it", async () => {
    const user = userEvent.setup();
    renderDialog(makeItem({ comprehension: null }));
    const dots = screen.getAllByRole("radio", { name: /\/5 —/ });
    await user.click(dots[3] as HTMLElement); // 4/5
    expect(screen.getByText("4/5 — だいたい追えた")).toBeInTheDocument();
    await user.click(dots[3] as HTMLElement);
    expect(screen.getByText("未選択")).toBeInTheDocument();
  });

  test("clicking すべてスキップ closes without any PATCH call", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDialog(makeItem());
    await user.click(screen.getByText("すべてスキップ"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(libraryItemsUpdate).not.toHaveBeenCalled();
  });

  test("clicking 保存 PATCHes all three fields, invalidates, toasts, and closes", async () => {
    const user = userEvent.setup();
    vi.mocked(libraryItemsUpdate).mockResolvedValue({
      data: makeItem({ comprehension: 5, importance: "high", one_line_note: "test" }),
    } as never);
    const { onClose } = renderDialog(makeItem({ one_line_note: "  test  " }));

    await user.click(screen.getByRole("radio", { name: "5/5 — 完全に理解した" }));
    await user.click(screen.getByRole("radio", { name: "高" }));
    await user.click(screen.getByText("保存"));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(libraryItemsUpdate).toHaveBeenCalledWith({
      path: { item_id: "li_1" },
      body: { comprehension: 5, importance: "high", one_line_note: "test" },
      throwOnError: true,
    });
    expect(toastMock).toHaveBeenCalledWith({ kind: "success", message: "読了メモを保存しました" });
  });

  test("save failure keeps the dialog open, preserves input, and shows an error toast", async () => {
    const user = userEvent.setup();
    vi.mocked(libraryItemsUpdate).mockRejectedValue(new Error("boom"));
    const { onClose } = renderDialog(makeItem());

    const note = screen.getByLabelText("ひとことメモ");
    await user.type(note, "壊れないで");
    await user.click(screen.getByText("保存"));

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        kind: "error",
        message: "保存に失敗しました — もう一度お試しください",
      }),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(note).toHaveValue("壊れないで");
  });

  test("hides the summary-to-note card when summary_3line is absent", () => {
    renderDialog(makeItem({ summary_3line: null }));
    expect(screen.queryByText("✦ 要約をメモに保存")).not.toBeInTheDocument();
  });

  test("summary-to-note card creates a note from the joined 3-line summary", async () => {
    const user = userEvent.setup();
    vi.mocked(notesCreate).mockResolvedValue({ data: {} } as never);
    renderDialog(makeItem({ summary_3line: ["一行目", "二行目", "三行目"] }));

    await user.click(screen.getByText("✦ 要約をメモに保存"));
    await waitFor(() => expect(screen.getByText("✓ メモに保存しました")).toBeInTheDocument());
    expect(notesCreate).toHaveBeenCalledWith({
      path: { item_id: "li_1" },
      body: { content_md: "一行目\n二行目\n三行目" },
      throwOnError: true,
    });
  });

  test("summary-to-note card shows a retryable error state on failure", async () => {
    const user = userEvent.setup();
    vi.mocked(notesCreate).mockRejectedValueOnce(new Error("boom"));
    renderDialog(makeItem({ summary_3line: ["一行目"] }));

    await user.click(screen.getByText("✦ 要約をメモに保存"));
    expect(await screen.findByText("保存できませんでした — もう一度お試しください")).toBeInTheDocument();

    vi.mocked(notesCreate).mockResolvedValueOnce({ data: {} } as never);
    await user.click(screen.getByText("✦ 要約をメモに保存"));
    await waitFor(() => expect(screen.getByText("✓ メモに保存しました")).toBeInTheDocument());
  });

  test("renders the article-mode follow-up card even without a summary (M2-07)", () => {
    renderDialog(makeItem({ summary_3line: null }));
    expect(screen.getByText("記事モードで読み返す →")).toBeInTheDocument();
    expect(screen.getByText("メモとチャットから読み物を自動構成")).toBeInTheDocument();
  });

  test("clicking the article-mode follow-up card closes the dialog and navigates unconditionally", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDialog(makeItem({ summary_3line: ["一行目"] }));
    await user.click(screen.getByText("記事モードで読み返す →"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(pushMock).toHaveBeenCalledWith("/papers/li_1?mode=article");
  });

  test("Ctrl+Enter triggers save", async () => {
    const user = userEvent.setup();
    vi.mocked(libraryItemsUpdate).mockResolvedValue({ data: makeItem() } as never);
    const { onClose } = renderDialog(makeItem());

    const note = screen.getByLabelText("ひとことメモ");
    note.focus();
    await user.keyboard("{Control>}{Enter}{/Control}");

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(libraryItemsUpdate).toHaveBeenCalledTimes(1);
  });
});
