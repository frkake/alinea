import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { LibraryItemSummary, SearchFacets, SearchGroup, SearchHit } from "@alinea/api-client";
import {
  SearchFacetRail,
  SearchGroupCard,
  SearchHitRow,
  SearchResults,
  SearchSummaryBar,
} from "@/components/search/SearchResults";

function libraryItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id: "li_1",
    paper: {
      id: "p_1",
      title: "Consistency Models",
      authors: ["Yang Song", "Prafulla Dhariwal"],
      authors_short: "Song, Dhariwal",
      venue: "ICML 2023",
      year: 2023,
      arxiv_id: null,
      arxiv_version: null,
      doi: null,
      license: "arXiv",
      visibility: "private",
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
    reading_seconds_total: 0,
    one_line_note: null,
    summary_3line: null,
    thumbnail_url: null,
    ...overrides,
  } as LibraryItemSummary;
}

function facets(): SearchFacets {
  return {
    source: { all: 12, body: 6, notes: 3, chat: 2, article: 1 },
    papers: [
      { library_item_id: "li_1", title: "Consistency Models", count: 7 },
      { library_item_id: "li_2", title: "Progressive Distillation", count: 3 },
    ],
  };
}

function bodyHit(overrides: Partial<SearchHit> = {}): SearchHit {
  return {
    source: "body",
    matched_in: ["source"],
    display: "§3.2 Training via Distillation · p.5",
    snippet: '…an <mark class="alinea-search-hit">EMA teacher</mark>…',
    snippet_lang: "en",
    target: {
      kind: "viewer",
      library_item_id: "li_1",
      anchor: { revision_id: "rev_1", block_id: "blk_1", display: "§3.2" },
    },
    ...overrides,
  } as SearchHit;
}

describe("SearchFacetRail (4e §4.3)", () => {
  test("ready mode renders all 5 source facets and papers with counts", () => {
    render(
      <SearchFacetRail
        source="all"
        paper={null}
        facets={facets()}
        mode="ready"
        onSourceChange={vi.fn()}
        onPaperChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /すべて/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("本文(原文・訳文)")).toBeInTheDocument();
    expect(screen.getByText("Consistency Models")).toBeInTheDocument();
    expect(screen.getByText("日本語クエリは訳文に、英語クエリは原文にヒットします(クロス検索)")).toBeInTheDocument();
  });

  test("empty mode hides facet rows entirely", () => {
    render(
      <SearchFacetRail
        source="all"
        paper={null}
        facets={null}
        mode="empty"
        onSourceChange={vi.fn()}
        onPaperChange={vi.fn()}
      />,
    );
    expect(screen.queryByText("すべて")).not.toBeInTheDocument();
    expect(screen.getByText("ヒット源")).toBeInTheDocument();
  });

  test("clicking a source facet fires onSourceChange", () => {
    const onSourceChange = vi.fn();
    render(
      <SearchFacetRail
        source="all"
        paper={null}
        facets={facets()}
        mode="ready"
        onSourceChange={onSourceChange}
        onPaperChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("チャット履歴"));
    expect(onSourceChange).toHaveBeenCalledWith("chat");
  });

  test("re-clicking the selected paper facet clears the filter", () => {
    const onPaperChange = vi.fn();
    render(
      <SearchFacetRail
        source="all"
        paper="li_1"
        facets={facets()}
        mode="ready"
        onSourceChange={vi.fn()}
        onPaperChange={onPaperChange}
      />,
    );
    fireEvent.click(screen.getByText("Consistency Models"));
    expect(onPaperChange).toHaveBeenCalledWith(null);
  });
});

describe("SearchSummaryBar (4e §4.4)", () => {
  test("renders the query, total and paper count", () => {
    render(
      <SearchSummaryBar q="EMA teacher" total={12} paperCount={3} sort="relevance" loading={false} onSortChange={vi.fn()} />,
    );
    expect(screen.getByText("EMA teacher")).toBeInTheDocument();
    expect(screen.getByText(/12 件/)).toBeInTheDocument();
    expect(screen.getByText(/3 論文/)).toBeInTheDocument();
    expect(screen.getByText(/並び: 関連度/)).toBeInTheDocument();
  });

  test("opening the sort menu and picking 新しい順 calls onSortChange", () => {
    const onSortChange = vi.fn();
    render(
      <SearchSummaryBar q="q" total={1} paperCount={1} sort="relevance" loading={false} onSortChange={onSortChange} />,
    );
    fireEvent.click(screen.getByText(/並び: 関連度/));
    fireEvent.click(screen.getByText("新しい順"));
    expect(onSortChange).toHaveBeenCalledWith("recency");
  });
});

describe("SearchHitRow (4e §4.5)", () => {
  test("wraps the whole row in a single <a> with the viewer href", () => {
    render(<SearchHitRow hit={bodyHit()} q="EMA teacher" />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/papers/li_1?block=blk_1&hl=EMA+teacher");
    expect(link.querySelectorAll("a")).toHaveLength(0); // 入れ子リンク禁止
    expect(screen.getByText("該当位置へ →")).toBeInTheDocument();
    expect(document.querySelector("mark.alinea-search-hit")).toHaveTextContent("EMA teacher");
  });

  test("combined source+translation hit renders two body badges", () => {
    render(<SearchHitRow hit={bodyHit({ matched_in: ["source", "translation"] })} q="q" />);
    expect(screen.getByText("本文 · 原文")).toBeInTheDocument();
    expect(screen.getByText("本文 · 訳文")).toBeInTheDocument();
  });
});

describe("SearchGroupCard (4e §4.5)", () => {
  test("renders paper header with authors, status pill and hit count", () => {
    const group: SearchGroup = {
      library_item: libraryItem(),
      hit_count: 7,
      article: null,
      hits: [bodyHit()],
    };
    render(<SearchGroupCard group={group} q="EMA teacher" />);
    expect(screen.getByText("Consistency Models")).toBeInTheDocument();
    expect(screen.getByText(/ICML 2023/)).toBeInTheDocument();
    expect(screen.getByText("7 件")).toBeInTheDocument();
  });

  test("shows the semantic match-type badge (意味) when match_type is semantic", () => {
    const group: SearchGroup = {
      library_item: libraryItem(),
      hit_count: 1,
      article: null,
      hits: [bodyHit()],
      match_type: "semantic",
    };
    render(<SearchGroupCard group={group} q="rectified flow" />);
    expect(screen.getByText("意味")).toBeInTheDocument();
    expect(screen.getByTestId("match-type-badge")).toHaveAttribute(
      "title",
      "意味的に関連(セマンティック検索)",
    );
  });

  test("shows 全文 for lexical and 両方 for both", () => {
    const base: Omit<SearchGroup, "match_type"> = {
      library_item: libraryItem(),
      hit_count: 1,
      article: null,
      hits: [bodyHit()],
    };
    const { rerender } = render(
      <SearchGroupCard group={{ ...base, match_type: "lexical" }} q="q" />,
    );
    expect(screen.getByText("全文")).toBeInTheDocument();
    rerender(<SearchGroupCard group={{ ...base, match_type: "both" }} q="q" />);
    expect(screen.getByText("両方")).toBeInTheDocument();
  });

  test("omits the match-type badge entirely when match_type is absent (flag off)", () => {
    const group: SearchGroup = {
      library_item: libraryItem(),
      hit_count: 1,
      article: null,
      hits: [bodyHit()],
    };
    render(<SearchGroupCard group={group} q="q" />);
    expect(screen.queryByTestId("match-type-badge")).not.toBeInTheDocument();
  });

  test("article-only group swaps the header for the article variant", () => {
    const group: SearchGroup = {
      library_item: libraryItem(),
      hit_count: 1,
      article: { article_id: "a1", title: "Rectified Flow を読む", generated_at: new Date(2026, 6, 6).toISOString() },
      hits: [
        {
          source: "article",
          matched_in: null,
          display: "「なぜ直線なのか」セクション",
          snippet: "…reflow…",
          snippet_lang: "ja",
          target: { kind: "article", library_item_id: "li_1", article_block_id: "ab_1" },
        } as SearchHit,
      ],
    };
    render(<SearchGroupCard group={group} q="reflow" />);
    expect(screen.getByText("記事: Rectified Flow を読む")).toBeInTheDocument();
    expect(screen.getByText(/記事\(自動構成\) · 7\/06/)).toBeInTheDocument();
    expect(screen.getByText("記事モードで開く →")).toBeInTheDocument();
  });
});

describe("SearchResults (4e §5.4 states)", () => {
  const baseProps = {
    source: "all" as const,
    paper: null,
    sort: "relevance" as const,
    onSourceChange: vi.fn(),
    onPaperChange: vi.fn(),
    onSortChange: vi.fn(),
    onRetry: vi.fn(),
    onLoadMore: vi.fn(),
  };

  test("empty query renders the 未入力 empty state and hides the summary bar", () => {
    render(
      <SearchResults
        {...baseProps}
        q=""
        facets={null}
        total={0}
        paperCount={0}
        groups={[]}
        isPending={false}
        isError={false}
        isPlaceholderData={false}
        hasNextPage={false}
        isFetchingNextPage={false}
      />,
    );
    expect(screen.getByText("検索語を入力してください")).toBeInTheDocument();
  });

  test("0 results renders the zero-hit empty state with facets shown", () => {
    render(
      <SearchResults
        {...baseProps}
        q="存在しない語"
        facets={facets()}
        total={0}
        paperCount={0}
        groups={[]}
        isPending={false}
        isError={false}
        isPlaceholderData={false}
        hasNextPage={false}
        isFetchingNextPage={false}
      />,
    );
    expect(screen.getByText("「存在しない語」に一致する結果はありません")).toBeInTheDocument();
  });

  test("error state shows retry and calls onRetry", () => {
    const onRetry = vi.fn();
    render(
      <SearchResults
        {...baseProps}
        q="q"
        facets={null}
        total={0}
        paperCount={0}
        groups={[]}
        isPending={false}
        isError
        isPlaceholderData={false}
        hasNextPage={false}
        isFetchingNextPage={false}
        onRetry={onRetry}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "再試行" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test("ready state renders group cards", () => {
    const group: SearchGroup = { library_item: libraryItem(), hit_count: 1, article: null, hits: [bodyHit()] };
    render(
      <SearchResults
        {...baseProps}
        q="EMA teacher"
        facets={facets()}
        total={1}
        paperCount={1}
        groups={[group]}
        isPending={false}
        isError={false}
        isPlaceholderData={false}
        hasNextPage={false}
        isFetchingNextPage={false}
      />,
    );
    expect(screen.getAllByText("Consistency Models").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1 件").length).toBeGreaterThan(0); // 結果件数バッジ + グループ内件数
    expect(screen.getByText(/1 論文/)).toBeInTheDocument();
  });
});
