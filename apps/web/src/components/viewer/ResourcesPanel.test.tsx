import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { ResourcesPanel } from "@/components/viewer/ResourcesPanel";
import type { ResourceLink, ResourceListResponse } from "@/components/viewer/resources/types";
import { ToastViewport } from "@/components/ui/Toast";
import { useViewerStore } from "@/stores/viewer-store";

function resource(overrides: Partial<ResourceLink> = {}): ResourceLink {
  return {
    id: "res_1",
    kind: "github",
    url: "https://github.com/gnobitab/RectifiedFlow",
    official: false,
    title: "gnobitab/RectifiedFlow",
    source_label: "GitHub",
    thumbnail_url: null,
    meta: { language: "Python", stars: 1200, updated_at: "2023-11-15" },
    meta_fetched: true,
    note: null,
    created_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      {ui}
      <ToastViewport />
    </QueryClientProvider>,
  );
}

describe("ResourcesPanel", () => {
  beforeEach(() => {
    useViewerStore.setState({ itemId: "li_1", requestScroll: vi.fn() });
    vi.unstubAllGlobals();
    // jsdom は scrollIntoView/scrollTo を実装しない(§5.4/§5.5 のスクロール挙動用スタブ)。
    Element.prototype.scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollTo = vi.fn();
  });

  // VT-VIEW-18: ResourceTabBadge — active 件数のみ表示(suggested を数えない)
  test("renders exactly `count` resource cards; the suggestion card is not one of them (VT-VIEW-18)", async () => {
    const listBody: ResourceListResponse = {
      items: [resource({ id: "a", title: "gnobitab/RectifiedFlow" }), resource({ id: "b", title: "other/repo" })],
      suggestion: { url: "https://github.com/x/y", detected_from: "arxiv_page" },
      count: 2,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(listBody)),
    );
    const { container } = renderWithClient(<ResourcesPanel />);

    await screen.findByText("gnobitab/RectifiedFlow");
    await screen.findByText("other/repo");
    expect(screen.getByText("✦ 公式実装を検出しました")).toBeInTheDocument();
    // タブ件数バッジ(SidePanelTabs 側。viewer-shell 所有)が参照する count と同じ数だけ
    // data-resource-id を持つカードが描画され、提案カードはそこに含まれない。
    expect(container.querySelectorAll("[data-resource-id]")).toHaveLength(listBody.count);
  });

  test("empty state prompts pasting a URL when there are no items and no suggestion", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ items: [], suggestion: null, count: 0 })),
    );
    renderWithClient(<ResourcesPanel />);
    expect(await screen.findByText("リソースはまだありません")).toBeInTheDocument();
  });

  test("error state shows a retry action", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ code: "internal_error" }, 500)),
    );
    renderWithClient(<ResourcesPanel />);
    expect(await screen.findByText("リソースを読み込めませんでした")).toBeInTheDocument();
    expect(screen.getByText("再試行")).toBeInTheDocument();
  });

  test("adding a URL posts to the create endpoint and clears the input", async () => {
    let listCalls = 0;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        listCalls += 1;
        return jsonResponse(resource({ id: "new" }), 201);
      }
      return jsonResponse({ items: [], suggestion: null, count: 0 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithClient(<ResourcesPanel />);
    await screen.findByText("リソースはまだありません");

    fireEvent.change(screen.getByLabelText("リソースの URL"), {
      target: { value: "https://github.com/gnobitab/RectifiedFlow" },
    });
    fireEvent.click(screen.getByText("追加"));

    await waitFor(() => expect(listCalls).toBe(1));
    const [url, init] = fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "POST") as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/library-items/li_1/resources");
    expect(JSON.parse(init.body as string)).toEqual({
      url: "https://github.com/gnobitab/RectifiedFlow",
    });
  });

  test("duplicate URL (409) flashes the existing card and shows an info toast", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse({ code: "duplicate", existing: { resource_id: "res_1" } }, 409);
      }
      return jsonResponse({ items: [resource()], suggestion: null, count: 1 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithClient(<ResourcesPanel />);
    await screen.findByText("gnobitab/RectifiedFlow");

    fireEvent.change(screen.getByLabelText("リソースの URL"), {
      target: { value: "https://github.com/gnobitab/RectifiedFlow?utm_source=share" },
    });
    fireEvent.click(screen.getByText("追加"));

    await waitFor(() => expect(screen.getByText("すでに追加されています")).toBeInTheDocument());
  });

  test("official implementation suggestion accept posts to the accept endpoint", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (typeof url === "string" && url.endsWith("/resource-suggestion/accept")) {
        return jsonResponse(resource({ id: "official", official: true }), 201);
      }
      return jsonResponse({
        items: [],
        suggestion: { url: "https://github.com/gnobitab/RectifiedFlow", detected_from: "arxiv_page" },
        count: 0,
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithClient(<ResourcesPanel />);
    await screen.findByText("✦ 公式実装を検出しました");

    fireEvent.click(screen.getByText("+ 追加"));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/library-items/li_1/resource-suggestion/accept",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});
