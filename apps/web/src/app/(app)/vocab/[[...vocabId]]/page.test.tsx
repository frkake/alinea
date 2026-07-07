import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { VocabListResponse } from "@yakudoku/api-client";

const replace = vi.fn();
const push = vi.fn();
let mockParams: { vocabId?: string[] } = {};
let mockSearch = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push }),
  useParams: () => mockParams,
  useSearchParams: () => mockSearch,
}));

const vocabList = vi.fn();
const vocabReviewQueue = vi.fn();
const vocabDelete = vi.fn();
const vocabGet = vi.fn();

vi.mock("@yakudoku/api-client", () => ({
  vocabList: (...args: unknown[]) => vocabList(...args),
  vocabReviewQueue: (...args: unknown[]) => vocabReviewQueue(...args),
  vocabDelete: (...args: unknown[]) => vocabDelete(...args),
  // VocabDetail は独自にフェッチする(未選択なら呼ばれない)。この統合テストでは詳細内容を
  // 検証しないため、常に保留のまま(読み込み中表示)にしておく。
  vocabGet: (...args: unknown[]) => vocabGet(...args),
}));

import VocabPage from "./page";

function listResponse(overrides: Partial<VocabListResponse> = {}): VocabListResponse {
  return {
    items: [
      {
        id: "v_boil",
        kind: "idiom",
        term: "boil down to",
        meaning_short: "要するに〜に帰着する",
        source: { library_item_id: "li_1", paper_title: "Rectified Flow", display: "Rectified Flow · §2.1" },
        added_at: "2026-07-07T00:00:00",
        generation: "done",
      },
      {
        id: "v_circumvent",
        kind: "word",
        term: "circumvent",
        meaning_short: "迂回して避ける",
        source: { library_item_id: "li_1", paper_title: "Rectified Flow", display: "Rectified Flow · §2.2" },
        added_at: "2026-07-06T00:00:00",
        generation: "done",
      },
    ],
    next_cursor: null,
    total: 2,
    counts: { all: 46, word: 28, collocation: 12, idiom: 6, due: 12 },
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <VocabPage />
    </QueryClientProvider>,
  );
}

describe("VocabPage (VT-VOC-01 / VT-VOC-02 / VT-VOC-04 integration)", () => {
  beforeEach(() => {
    replace.mockClear();
    push.mockClear();
    mockParams = {};
    mockSearch = new URLSearchParams();
    vocabList.mockReset().mockResolvedValue({ data: listResponse() });
    vocabReviewQueue.mockReset();
    vocabDelete.mockReset();
    vocabGet.mockReset().mockReturnValue(new Promise(() => undefined));
  });

  test("renders header counts and the fetched rows from GET /api/vocab", async () => {
    renderPage();
    expect(await screen.findByText("46 語 — 読んだ論文の文脈から")).toBeInTheDocument();
    expect(screen.getByText("boil down to")).toBeInTheDocument();
    expect(screen.getByText("circumvent")).toBeInTheDocument();
    expect(vocabList).toHaveBeenCalledWith(
      expect.objectContaining({ query: expect.objectContaining({ sort: "added_at", limit: 50 }) }),
    );
  });

  test("clicking a kind chip replaces the URL with ?kind=<kind> while keeping the current selection", async () => {
    mockParams = { vocabId: ["v_boil"] };
    renderPage();
    await screen.findByText("boil down to");
    fireEvent.click(screen.getByRole("button", { name: /^イディオム/ }));
    expect(replace).toHaveBeenCalledWith("/vocab/v_boil?kind=idiom", { scroll: false });
  });

  test("toggling the 復習期 chip replaces the URL with ?due=true", async () => {
    renderPage();
    await screen.findByText("boil down to");
    fireEvent.click(screen.getByRole("button", { name: "復習期 12" }));
    expect(replace).toHaveBeenCalledWith("/vocab?due=true", { scroll: false });
  });

  test("clicking the 語彙 sort header replaces the URL with ?sort=term", async () => {
    renderPage();
    await screen.findByText("boil down to");
    fireEvent.click(screen.getByText("語彙"));
    expect(replace).toHaveBeenCalledWith("/vocab?sort=term", { scroll: false });
  });

  test("selecting a row replaces the URL to /vocab/{id} preserving existing query (VT-VOC-01)", async () => {
    mockSearch = new URLSearchParams("kind=word&sort=term");
    renderPage();
    await screen.findByText("boil down to");
    fireEvent.click(screen.getByText("circumvent"));
    expect(replace).toHaveBeenCalledWith("/vocab/v_circumvent?kind=word&sort=term", { scroll: false });
  });

  test("VocabSearchBox input debounces into ?q= without touching global search state (VT-VOC-02)", async () => {
    renderPage();
    await screen.findByText("boil down to");
    const input = screen.getByPlaceholderText("語彙を検索");
    fireEvent.change(input, { target: { value: "boil" } });
    await waitFor(
      () => {
        expect(replace).toHaveBeenCalledWith("/vocab?q=boil", { scroll: false });
      },
      { timeout: 1000 },
    );
  });

  test("the 復習をはじめる button start count matches counts.due (VT-VOC-04)", async () => {
    renderPage();
    await screen.findByText("boil down to");
    expect(screen.getByRole("button", { name: /復習をはじめる/ })).toHaveTextContent("12");
  });
});
