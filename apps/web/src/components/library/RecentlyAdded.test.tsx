import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";
import type { LibraryItemSummary } from "@yakudoku/api-client";
import { RecentlyAdded, formatAddedAt } from "@/components/library/RecentlyAdded";

const NOW = new Date("2026-07-08T12:00:00");

function makeItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id: "li_1",
    paper: {
      id: "pap_1",
      title: "Scaling Rectified Flow Transformers",
      authors: ["Patrick Esser"],
      authors_short: "Esser",
      venue: undefined,
      year: 2024,
      arxiv_id: "2403.03206",
      license: "cc-by",
      visibility: "public",
      abstract: "",
    },
    status: "planned",
    priority: null,
    deadline: null,
    tags: [],
    suggested_tags: [],
    quality_level: "A",
    source: "arxiv",
    progress_pct: 0,
    comprehension: null,
    reading_seconds_total: 0,
    added_at: "2026-07-08T08:02:00",
    updated_at: "2026-07-08T08:02:00",
    ...overrides,
  };
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// 1d §5.5: 最近追加カードの変種決定規則
describe("RecentlyAdded variants", () => {
  test("expanded card (added today, complete): shows summary and tags", () => {
    renderWithClient(
      <RecentlyAdded
        weekCount={1}
        items={[
          makeItem({
            summary_3line: ["整流フローをT2I基盤モデルへ拡張", "ノイズ配分を再重み付け", "高解像度でDiTを上回る品質"],
            tags: ["flow"],
            suggested_tags: ["cs.CV"],
          }),
        ]}
        onOpen={() => {}}
        now={NOW}
      />,
    );
    expect(screen.getByText(/整流フローをT2I基盤モデルへ拡張/)).toBeInTheDocument();
    expect(screen.getByText("flow")).toBeInTheDocument();
    expect(screen.getByText("提案: cs.CV +")).toBeInTheDocument();
  });

  test("processing card: shows stage checklist and hides start-reading button before readable", () => {
    renderWithClient(
      <RecentlyAdded
        weekCount={1}
        items={[
          makeItem({
            pipeline: {
              job_id: "job_1",
              stage: "structuring",
              status: "running",
              progress_pct: 35,
              readable_upto: null,
            },
          }),
        ]}
        onOpen={() => {}}
        now={NOW}
      />,
    );
    expect(screen.getByText("✓ 書誌")).toBeInTheDocument();
    expect(screen.getByText("解析中…")).toBeInTheDocument();
    expect(screen.getByText("解析中です")).toBeInTheDocument();
    expect(screen.queryByText("読み始める")).not.toBeInTheDocument();
  });

  test("processing card at translating_body with readable_upto: shows start-reading button", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    renderWithClient(
      <RecentlyAdded
        weekCount={1}
        items={[
          makeItem({
            pipeline: {
              job_id: "job_1",
              stage: "translating_body",
              status: "running",
              progress_pct: 68,
              readable_upto: "§3",
            },
          }),
        ]}
        onOpen={onOpen}
        now={NOW}
      />,
    );
    expect(screen.getByText("✓ アブスト訳・要約")).toBeInTheDocument();
    expect(screen.getByText("本文翻訳中 68%")).toBeInTheDocument();
    expect(screen.getByText(/§3 まで読めます/)).toBeInTheDocument();
    await user.click(screen.getByText("読み始める"));
    expect(onOpen).toHaveBeenCalledWith("li_1");
  });

  test("waiting_quota shows クォータ待機中", () => {
    renderWithClient(
      <RecentlyAdded
        weekCount={1}
        items={[
          makeItem({
            pipeline: {
              job_id: "job_1",
              stage: "translating_body",
              status: "waiting_quota",
              progress_pct: 60,
              readable_upto: "§2",
            },
          }),
        ]}
        onOpen={() => {}}
        now={NOW}
      />,
    );
    expect(screen.getByText("クォータ待機中")).toBeInTheDocument();
  });

  test("failed card shows retry affordance", () => {
    renderWithClient(
      <RecentlyAdded
        weekCount={1}
        items={[
          makeItem({
            pipeline: {
              job_id: "job_1",
              stage: "fetching",
              status: "failed",
              progress_pct: 10,
              failed_reason: "boom",
            },
          }),
        ]}
        onOpen={() => {}}
        now={NOW}
      />,
    );
    expect(screen.getByText("× 取り込みに失敗しました")).toBeInTheDocument();
    expect(screen.getByText("再試行")).toBeInTheDocument();
  });

  test("condensed card (added yesterday, complete, no suggestion)", () => {
    renderWithClient(
      <RecentlyAdded
        weekCount={1}
        items={[makeItem({ added_at: "2026-07-07T19:40:00" })]}
        onOpen={() => {}}
        now={NOW}
      />,
    );
    expect(screen.getByText(/✓ 翻訳完了/)).toBeInTheDocument();
    expect(screen.getByText(/読める状態です/)).toBeInTheDocument();
  });

  test("condensed card with a suggested tag", () => {
    renderWithClient(
      <RecentlyAdded
        weekCount={1}
        items={[makeItem({ added_at: "2026-07-07T18:12:00", suggested_tags: ["solver"] })]}
        onOpen={() => {}}
        now={NOW}
      />,
    );
    expect(screen.getByText(/提案タグ/)).toBeInTheDocument();
    expect(screen.getByText("solver +")).toBeInTheDocument();
  });

  test("empty state", () => {
    renderWithClient(<RecentlyAdded weekCount={0} items={[]} onOpen={() => {}} now={NOW} />);
    expect(screen.getByText("今週追加された論文はありません")).toBeInTheDocument();
  });

  // mobile.md §5.2: 縦積み 1 カラム(カード幅 100%)。
  test("isMobile switches the card grid to a single column", () => {
    const { container } = renderWithClient(
      <RecentlyAdded weekCount={1} items={[makeItem()]} onOpen={() => {}} now={NOW} isMobile />,
    );
    expect(container.querySelector('[style*="grid-template-columns"]')).toHaveStyle({
      gridTemplateColumns: "1fr",
    });
  });
});

describe("formatAddedAt", () => {
  test.each([
    ["2026-07-08T08:02:00", "今日 8:02"],
    ["2026-07-07T19:40:00", "昨日 19:40"],
    ["2026-07-06T18:12:00", "7/6 18:12"],
  ])("%s -> %s", (iso, expected) => {
    expect(formatAddedAt(iso, NOW)).toBe(expected);
  });
});
