import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { CollectionDetail } from "@/components/collections/types";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useParams: () => ({ collectionId: "col_1" }),
}));

import CollectionDetailPage from "./page";

function makeDetail(overrides: Partial<CollectionDetail> = {}): CollectionDetail {
  return {
    id: "col_1",
    name: "輪読会 2026-07",
    description: "初回の輪読会",
    deadline: "2026-07-16",
    days_left: 10,
    progress: { done: 1, total: 2 },
    share: { status: "none", token: null, url: null, include_notes: false, included_note_count: 0 },
    entries: [
      {
        id: "ce_1",
        order: 1,
        assignee: null,
        assignee_is_self: true,
        presentation_minutes: 25,
        note: null,
        library_item: {
          id: "li_1",
          paper: {
            id: "pap_1",
            title: "Adversarial Diffusion Distillation",
            authors: ["Sauer"],
            authors_short: "Sauer",
            venue: null,
            year: 2024,
            arxiv_id: "1",
            license: "cc-by",
            visibility: "public",
            abstract: "",
          },
          status: "up_next",
          priority: null,
          deadline: null,
          tags: [],
          suggested_tags: [],
          quality_level: "A",
          source: "arxiv",
          progress_pct: 0,
          comprehension: null,
          reading_seconds_total: 0,
          added_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        },
      },
      {
        id: "ce_2",
        order: 2,
        assignee: "佐藤",
        assignee_is_self: false,
        presentation_minutes: null,
        note: null,
        library_item: {
          id: "li_2",
          paper: {
            id: "pap_2",
            title: "Consistency Models",
            authors: ["Song"],
            authors_short: "Song",
            venue: "ICML 2023",
            year: 2023,
            arxiv_id: "2",
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
          comprehension: 4,
          reading_seconds_total: 0,
          added_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        },
      },
    ],
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CollectionDetailPage />
    </QueryClientProvider>,
  );
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  push.mockClear();
});

// M2-09: plans/09-screens/4b-collection-detail.md。
describe("CollectionDetailPage (4b)", () => {
  test("読み込み後にヘッダー・進捗・エントリを表示する", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, makeDetail()));
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    await waitFor(() => expect(screen.getByText("輪読会 2026-07")).toBeInTheDocument());
    expect(screen.getByText("締切 7/16 — 残り 10 日")).toBeInTheDocument();
    expect(screen.getByText("1/2 読了")).toBeInTheDocument();
    expect(screen.getByText("Adversarial Diffusion Distillation")).toBeInTheDocument();
    expect(screen.getByText("Consistency Models")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/collections/col_1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  test("404 は「コレクションが見つかりません」EmptyState を表示し、ライブラリへ戻れる", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(404, { code: "not_found", title: "見つかりません", detail: null }),
      );
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("コレクションが見つかりません")).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByText("ライブラリへ戻る"));
    expect(push).toHaveBeenCalledWith("/library");
  });

  test("共有リンクの発行で POST が飛び、発行済み表示に切り替わる", async () => {
    const activeShare = {
      status: "active" as const,
      token: "x8Kf3qPw",
      url: "http://localhost:3000/c/x8Kf3qPw",
      include_notes: false,
      included_note_count: 0,
    };
    let issued = false;
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/collections/col_1" && (!init || init.method === undefined)) {
        return Promise.resolve(
          jsonResponse(200, issued ? makeDetail({ share: activeShare }) : makeDetail()),
        );
      }
      if (url === "/api/collections/col_1/share" && init?.method === "POST") {
        issued = true;
        return Promise.resolve(jsonResponse(201, activeShare));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await waitFor(() => expect(screen.getByText("輪読会 2026-07")).toBeInTheDocument());
    expect(screen.getByText("未発行")).toBeInTheDocument();

    await userEvent.click(screen.getByText("共有リンクを発行"));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/collections/col_1/share",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() => expect(screen.getByText("発行済み")).toBeInTheDocument());
  });
});
