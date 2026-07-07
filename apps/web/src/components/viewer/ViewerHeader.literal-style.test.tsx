import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ViewerHeader } from "@/components/viewer/ViewerHeader";
import { useViewerStore } from "@/stores/viewer-store";

/** テスト用の EventSource スタブ(InfoPanel.test.tsx と同方針。ジョブ SSE を模擬)。 */
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

  removeEventListener(): void {
    // 未使用。
  }

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
  title: "Flow Straight and Fast",
  qualityLevel: "A" as const,
  status: "reading" as const,
  mode: "translation" as const,
  onModeChange: vi.fn(),
  onStatusChange: vi.fn(),
  onBack: vi.fn(),
};

describe("ViewerHeader 直訳スタイル切替(M2-15。plans/06 §10.2・1b §4.2-7)", () => {
  beforeEach(() => {
    useViewerStore.setState({
      revisionId: "rev_1",
      activeSectionId: "sec-2",
      style: "natural",
      literalStatus: "unknown",
      literalJobId: null,
      literalSetId: null,
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
        json: async () => ({ set_id: "ts_1", job_id: null }),
      })),
    );
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: /スタイル: 自然訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "直訳" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/revisions/rev_1/translations",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ style: "literal", priority_section_id: "sec-2" }),
      }),
    );
    await waitFor(() => expect(useViewerStore.getState().literalStatus).toBe("ready"));
    expect(useViewerStore.getState().literalSetId).toBe("ts_1");
    expect(MockEventSource.instances).toHaveLength(0);
  });

  test("未生成(202)なら生成中インジケータを表示し、SSE done で ready になる", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ set_id: "ts_1", job_id: "job_9" }),
      })),
    );
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: /スタイル: 自然訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "直訳" }));

    await waitFor(() => expect(useViewerStore.getState().literalStatus).toBe("generating"));
    expect(screen.getByText(/スタイル: 直訳\(生成中…\)/)).toBeInTheDocument();
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(MockEventSource.instances[0]?.url).toBe("/api/jobs/job_9/events");

    act(() => {
      MockEventSource.instances[0]?.emit("done", {});
    });

    await waitFor(() => expect(useViewerStore.getState().literalStatus).toBe("ready"));
    expect(MockEventSource.instances[0]?.closed).toBe(true);
    expect(screen.queryByText(/生成中/)).not.toBeInTheDocument();
  });

  test("SSE error で literalStatus が unknown に戻る(再試行可能)", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ set_id: "ts_1", job_id: "job_9" }),
      })),
    );
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: /スタイル: 自然訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "直訳" }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0]?.emit("error", {});
    });

    await waitFor(() => expect(useViewerStore.getState().literalStatus).toBe("unknown"));
  });

  test("直訳ボタン再クリックは status!==unknown の間 POST を再送しない(冪等)", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ set_id: "ts_1", job_id: null }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithClient(<ViewerHeader {...baseProps} />);

    await user.click(screen.getByRole("button", { name: /スタイル: 自然訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "直訳" }));
    await waitFor(() => expect(useViewerStore.getState().literalStatus).toBe("ready"));

    await user.click(screen.getByRole("button", { name: /スタイル: 直訳/ }));
    await user.click(screen.getByRole("menuitem", { name: "直訳" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
