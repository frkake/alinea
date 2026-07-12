import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  translationsSelectSections,
  type SectionSelectionState,
  type TocNode,
} from "@alinea/api-client";
import { LongPaperSectionSelection } from "./LongPaperSectionSelection";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, translationsSelectSections: vi.fn() };
});

const selection: SectionSelectionState = {
  required: true,
  selectable_section_ids: ["sec-1", "sec-1a", "sec-2"],
  selected_section_ids: [],
};

const toc: TocNode[] = [
  {
    section_id: "sec-1",
    number: "1",
    title_en: "Introduction",
    title_ja: null,
    translated: false,
    in_progress_denominator: false,
    on_demand: true,
    annotation_count: 0,
    bookmarked: false,
    children: [
      {
        section_id: "sec-1a",
        number: "1.1",
        title_en: "Background with a deliberately long title that must wrap inside the dialog",
        title_ja: null,
        translated: false,
        in_progress_denominator: false,
        on_demand: true,
        annotation_count: 0,
        bookmarked: false,
        children: [],
      },
    ],
  },
  {
    section_id: "sec-2",
    number: "2",
    title_en: "Method",
    title_ja: null,
    translated: false,
    in_progress_denominator: false,
    on_demand: true,
    annotation_count: 0,
    bookmarked: false,
    children: [],
  },
];

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return { invalidate };
}

function component() {
  return (
    <LongPaperSectionSelection
      itemId="item-1"
      setId="set-1"
      pageCount={42}
      toc={toc}
      selection={selection}
    />
  );
}

describe("LongPaperSectionSelection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("opens with every selectable section checked and submits a canonical subset", async () => {
    vi.mocked(translationsSelectSections).mockResolvedValue({
      data: { set_id: "set-1", job_id: "job-1", section_ids: ["sec-2"] },
    } as never);
    const { invalidate } = renderWithClient(component());

    expect(screen.getByRole("dialog", { name: "翻訳するセクションを選択" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "1 Introduction" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /1.1 Background/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "2 Method" })).toBeChecked();

    fireEvent.click(screen.getByRole("checkbox", { name: "1 Introduction" }));
    expect(screen.getByRole("checkbox", { name: "1 Introduction" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /1.1 Background/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "2 Method" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "選択したセクションを翻訳" }));

    await waitFor(() =>
      expect(translationsSelectSections).toHaveBeenCalledWith({
        path: { set_id: "set-1" },
        body: { section_ids: ["sec-2"] },
        throwOnError: true,
      }),
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["viewer", "item-1"], exact: true });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("prevents an empty submission and restores all sections", () => {
    renderWithClient(component());

    fireEvent.click(screen.getByRole("button", { name: "すべて解除" }));
    expect(screen.getByText("1つ以上のセクションを選択してください")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "選択したセクションを翻訳" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "すべて選択" }));
    expect(screen.getByRole("button", { name: "全文を翻訳" })).toBeEnabled();
  });

  test("can be dismissed without losing the proposal and reopened from the viewer", () => {
    renderWithClient(component());

    fireEvent.click(screen.getByRole("button", { name: "後で選ぶ" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "翻訳するセクションを選択" }));
    expect(screen.getByRole("dialog", { name: "翻訳するセクションを選択" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "2 Method" })).toBeChecked();
  });

  test("keeps the chosen sections on an API error and allows retry", async () => {
    vi.mocked(translationsSelectSections)
      .mockRejectedValueOnce({ detail: "選択を保存できませんでした" })
      .mockResolvedValueOnce({
        data: { set_id: "set-1", job_id: "job-1", section_ids: ["sec-1", "sec-1a"] },
      } as never);
    renderWithClient(component());
    fireEvent.click(screen.getByRole("checkbox", { name: "2 Method" }));

    fireEvent.click(screen.getByRole("button", { name: "選択したセクションを翻訳" }));
    expect(await screen.findByText("選択を保存できませんでした")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "1 Introduction" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "2 Method" })).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "再試行" }));

    await waitFor(() => expect(translationsSelectSections).toHaveBeenCalledTimes(2));
  });
});
