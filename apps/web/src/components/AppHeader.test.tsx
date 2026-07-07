import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { AppHeader } from "@/components/AppHeader";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/library",
}));

const authMe = vi.fn();
const searchPreview = vi.fn();
vi.mock("@yakudoku/api-client", () => ({
  authMe: (...args: unknown[]) => authMe(...args),
  searchPreview: (...args: unknown[]) => searchPreview(...args),
}));

function renderHeader(props: Parameters<typeof AppHeader>[0] = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppHeader {...props} />
    </QueryClientProvider>,
  );
}

// VT-UI-01: シェル(トップバー)描画+ベル・検索ボックスの配線(M1-08 / M1-13)
describe("AppHeader (VT-UI-01)", () => {
  beforeEach(() => {
    push.mockClear();
    authMe.mockReset().mockResolvedValue({ data: { user: { id: "u1" }, unread_notifications: 0 } });
    searchPreview.mockReset().mockResolvedValue({ data: { total: 0, items: [] } });
  });

  test("renders product name", () => {
    renderHeader();
    expect(screen.getByText(/訳読/)).toBeInTheDocument();
  });

  test("renders wordmark, global search and account controls", () => {
    renderHeader();
    expect(screen.getByText("YAKUDOKU")).toBeInTheDocument();
    expect(
      screen.getByLabelText("ライブラリ全体を検索 — 本文・訳文・メモ・チャット"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("通知")).toBeInTheDocument();
    expect(screen.getByLabelText("アカウント")).toBeInTheDocument();
  });

  test("hides search box when showSearch is false", () => {
    renderHeader({ showSearch: false });
    expect(
      screen.queryByLabelText("ライブラリ全体を検索 — 本文・訳文・メモ・チャット"),
    ).toBeNull();
  });

  test("shows the amber unread dot once /api/auth/me reports unread notifications", async () => {
    authMe.mockResolvedValue({ data: { user: { id: "u1" }, unread_notifications: 3 } });
    const { container } = renderHeader();
    await waitFor(() => {
      expect(container.querySelector('[aria-label="通知"] span')).not.toBeNull();
    });
  });

  test("typing 2+ chars opens the dropdown with preview results and Enter navigates", async () => {
    searchPreview.mockResolvedValue({
      data: {
        total: 1,
        items: [
          {
            source: "body",
            matched_in: ["source"],
            display: "§3.2",
            snippet: "…EMA teacher…",
            snippet_lang: "en",
            target: {
              kind: "viewer",
              library_item_id: "li_1",
              anchor: { revision_id: "rev_1", block_id: "blk_1", display: "§3.2" },
            },
            library_item: { id: "li_1", title: "Consistency Models" },
          },
        ],
      },
    });
    const user = userEvent.setup();
    renderHeader();
    const input = screen.getByLabelText("ライブラリ全体を検索 — 本文・訳文・メモ・チャット");
    await user.type(input, "EMA teacher");

    await waitFor(() => {
      expect(searchPreview).toHaveBeenCalledWith({ query: { q: "EMA teacher" }, throwOnError: true });
    });
    await waitFor(() => {
      expect(screen.getByText("Consistency Models")).toBeInTheDocument();
    });

    await user.keyboard("{Enter}");
    expect(push).toHaveBeenCalledWith("/papers/li_1?block=blk_1&hl=EMA+teacher");
  });

  test("Escape blurs the input without clearing its value", async () => {
    const user = userEvent.setup();
    renderHeader();
    const input = screen.getByLabelText("ライブラリ全体を検索 — 本文・訳文・メモ・チャット");
    await user.type(input, "ab");
    await user.keyboard("{Escape}");
    expect(input).toHaveValue("ab");
  });
});
