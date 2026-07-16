import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ViewerHeader } from "@/components/viewer/ViewerHeader";
import { useViewerStore } from "@/stores/viewer-store";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  closed = false;
  private listeners: Record<string, ((e: MessageEvent<string>) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: EventListener): void {
    (this.listeners[type] ??= []).push(cb as (e: MessageEvent<string>) => void);
  }

  removeEventListener(): void {}

  close(): void {
    this.closed = true;
  }

  emit(type: string, data?: unknown): void {
    const event = { data: data === undefined ? "" : JSON.stringify(data) } as MessageEvent<string>;
    for (const cb of this.listeners[type] ?? []) cb(event);
  }
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const baseProps = {
  itemId: "li_1",
  title: "Flow Straight and Fast",
  qualityLevel: "A" as const,
  status: "reading" as const,
  mode: "translation" as const,
  onModeChange: vi.fn(),
  onStatusChange: vi.fn(),
  onBack: vi.fn(),
};

describe("ViewerHeader やさしい訳スタイル切替(S11 M3)", () => {
  beforeEach(() => {
    useViewerStore.setState({
      revisionId: "rev_1",
      activeSectionId: "sec-2",
      style: "natural",
      easyStatus: "unknown",
      easyJobId: null,
      easySetId: null,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    MockEventSource.instances.length = 0;
  });

  test("既に complete なら POST 後すぐ ready になり SSE は張らない(job_id: null)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ set_id: "ts_2", job_id: null }),
      })),
    );
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: /スタイル: 自然訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "やさしい訳" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/revisions/rev_1/translations",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ style: "easy", priority_section_id: "sec-2" }),
      }),
    );
    await waitFor(() => expect(useViewerStore.getState().easyStatus).toBe("ready"));
    expect(useViewerStore.getState().easySetId).toBe("ts_2");
    expect(MockEventSource.instances).toHaveLength(0);
  });

  test("未生成(202)なら生成中インジケータを表示し、SSE done で ready になる", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ set_id: "ts_2", job_id: "job_9" }),
      })),
    );
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: /スタイル: 自然訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "やさしい訳" }));

    await waitFor(() => expect(useViewerStore.getState().easyStatus).toBe("generating"));
    expect(screen.getByText(/スタイル: やさしい訳\(生成中…\)/)).toBeInTheDocument();
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0]?.emit("done", {});
    });

    await waitFor(() => expect(useViewerStore.getState().easyStatus).toBe("ready"));
    expect(MockEventSource.instances[0]?.closed).toBe(true);
    expect(screen.queryByText(/生成中/)).not.toBeInTheDocument();
  });

  test("SSE error で easyStatus が unknown に戻る(再試行可能)", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ set_id: "ts_2", job_id: "job_9" }),
      })),
    );
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: /スタイル: 自然訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "やさしい訳" }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0]?.emit("error", {});
    });

    await waitFor(() => expect(useViewerStore.getState().easyStatus).toBe("unknown"));
  });

  test("やさしい訳ボタン再クリックは status!==unknown の間 POST を再送しない(冪等)", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ set_id: "ts_2", job_id: null }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: /スタイル: 自然訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "やさしい訳" }));
    await waitFor(() => expect(useViewerStore.getState().easyStatus).toBe("ready"));

    await user.click(screen.getByRole("button", { name: /スタイル: やさしい訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "やさしい訳" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
