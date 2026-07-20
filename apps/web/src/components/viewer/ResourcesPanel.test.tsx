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

// 生成 SDK は内部で本物の `Response` を消費する(response.headers / response.text() /
// response.json())ため、テストの疑似応答も本物の Response で組み立てる。
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// 生成 SDK は `fetch(request)` を単一の `Request` で呼ぶ(url+init の 2 引数ではない)。
// モックが受け取る第一引数(string / URL / Request)から URL・メソッドを取り出す。
function calledUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}
function calledMethod(input: RequestInfo | URL, init?: RequestInit): string {
  if (input instanceof Request) return input.method;
  return (init?.method ?? "GET").toUpperCase();
}
async function calledBody(input: RequestInfo | URL, init?: RequestInit): Promise<string> {
  if (input instanceof Request) return await input.clone().text();
  return (init?.body as string) ?? "";
}

// ResourcesPanel は github の active カード描画時に code-analysis 一覧(Task 22)も引く。
// GET のうち /code-analysis は空の RunsResponse を、それ以外(resources / settings)は
// 与えられた list ボディを返して、カード描画が落ちないようにする。
function routeGet(input: RequestInfo | URL, listBody: unknown): Response {
  if (calledUrl(input).includes("/code-analysis")) {
    return jsonResponse({ runs: [], correspondences: [], current_result: null });
  }
  return jsonResponse(listBody);
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
      suggestions: [{ url: "https://github.com/x/y", detected_from: "arxiv_page" }],
      count: 2,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => routeGet(input, listBody)),
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (calledMethod(input, init) === "POST") {
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
    const postCall = fetchMock.mock.calls.find(
      ([input, init]) => calledMethod(input, init) === "POST",
    ) as [RequestInfo | URL, RequestInit | undefined];
    expect(calledUrl(postCall[0])).toContain("/api/library-items/li_1/resources");
    expect(JSON.parse(await calledBody(postCall[0], postCall[1]))).toEqual({
      url: "https://github.com/gnobitab/RectifiedFlow",
    });
  });

  test("duplicate URL (409) flashes the existing card and shows an info toast", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (calledMethod(input, init) === "POST") {
        return jsonResponse({ code: "duplicate", existing: { resource_id: "res_1" } }, 409);
      }
      return routeGet(input, { items: [resource()], suggestion: null, count: 1 });
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (calledUrl(input).endsWith("/resource-suggestion/accept")) {
        return jsonResponse(resource({ id: "official", official: true }), 201);
      }
      return jsonResponse({
        items: [],
        suggestion: { url: "https://github.com/gnobitab/RectifiedFlow", detected_from: "arxiv_page" },
        suggestions: [
          { url: "https://github.com/gnobitab/RectifiedFlow", detected_from: "arxiv_page" },
        ],
        count: 0,
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithClient(<ResourcesPanel />);
    await screen.findByText("✦ 公式実装を検出しました");

    fireEvent.click(screen.getByText("+ 追加"));

    await waitFor(() => {
      const accept = fetchMock.mock.calls.find(([input]) =>
        calledUrl(input as RequestInfo | URL).endsWith("/resource-suggestion/accept"),
      );
      expect(accept).toBeDefined();
      expect(calledUrl(accept![0] as RequestInfo | URL)).toContain(
        "/api/library-items/li_1/resource-suggestion/accept",
      );
      expect(calledMethod(accept![0] as RequestInfo | URL)).toBe("POST");
    });
  });

  // Task 18: Hugging Face 由来の複数候補を表示し、ID 指定で個別採用する。
  test("renders multiple Hugging Face suggestions and accepts one by resource id", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (calledUrl(input).endsWith("/accept-suggestion")) {
        return jsonResponse(resource({ id: "res_hf", kind: "huggingface" }), 200);
      }
      return jsonResponse({
        items: [],
        suggestion: {
          url: "https://github.com/facebookresearch/llama",
          detected_from: "huggingface_paper",
          resource_id: "sug_github",
          kind: "github",
          relation: "github",
          official_candidate: true,
        },
        suggestions: [
          {
            url: "https://github.com/facebookresearch/llama",
            detected_from: "huggingface_paper",
            resource_id: "sug_github",
            kind: "github",
            relation: "github",
            title: "facebookresearch/llama",
            official_candidate: true,
          },
          {
            url: "https://huggingface.co/meta-llama/Llama-2-7b",
            detected_from: "huggingface_paper",
            resource_id: "sug_model",
            kind: "huggingface",
            relation: "model",
            title: "meta-llama/Llama-2-7b",
            official_candidate: false,
            meta: { repo_type: "model", repo_id: "meta-llama/Llama-2-7b", downloads: 700000 },
          },
        ],
        count: 0,
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithClient(<ResourcesPanel />);

    // github 公式候補 + Hugging Face Model 候補の両方が出る。
    await screen.findByText("✦ 公式実装を検出しました");
    await screen.findByText("🤗 Hugging Face Model");

    // Model 候補を採用 → ID 指定の accept-suggestion を叩く。
    const acceptButtons = screen.getAllByText("+ 追加");
    fireEvent.click(acceptButtons[1]!);

    await waitFor(() => {
      const accept = fetchMock.mock.calls.find(([input]) =>
        calledUrl(input as RequestInfo | URL).endsWith("/accept-suggestion"),
      );
      expect(accept).toBeDefined();
      expect(calledUrl(accept![0] as RequestInfo | URL)).toContain(
        "/api/resources/sug_model/accept-suggestion",
      );
      expect(calledMethod(accept![0] as RequestInfo | URL)).toBe("POST");
    });
  });

  test("collapses suggestions beyond the first few and expands on click", async () => {
    const many = Array.from({ length: 6 }).map((_, i) => ({
      url: `https://huggingface.co/org/model-${i}`,
      detected_from: "huggingface_paper" as const,
      resource_id: `sug_${i}`,
      kind: "huggingface" as const,
      relation: "model",
      title: `org/model-${i}`,
      official_candidate: false,
      meta: { repo_type: "model", repo_id: `org/model-${i}` },
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ items: [], suggestion: many[0], suggestions: many, count: 0 })),
    );
    renderWithClient(<ResourcesPanel />);

    await screen.findByText("他 3 件の候補を表示");
    // 折り畳み時は 3 件だけ描画。
    expect(screen.getAllByText("+ 追加")).toHaveLength(3);
    fireEvent.click(screen.getByText("他 3 件の候補を表示"));
    await waitFor(() => expect(screen.getAllByText("+ 追加")).toHaveLength(6));
  });
});
