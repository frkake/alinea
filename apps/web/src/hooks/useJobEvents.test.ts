import { renderHook, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { jobsGet } from "@alinea/api-client";
import { useJobEvents } from "@/hooks/useJobEvents";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, jobsGet: vi.fn() };
});

/** テスト用の EventSource スタブ(InfoPanel.test.tsx と同方針)。 */
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
  dispatchNamed(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    for (const cb of this.listeners[type] ?? []) cb(event);
  }
  dispatchConnError(): void {
    const event = {} as MessageEvent<string>;
    for (const cb of this.listeners.error ?? []) cb(event);
  }
}

function firstInstance(): MockEventSource {
  const source = MockEventSource.instances[0];
  if (!source) throw new Error("no MockEventSource instance was created");
  return source;
}

describe("useJobEvents", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.clearAllMocks();
  });

  test("does nothing when jobId is null", () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    renderHook(() => useJobEvents(null, {}));
    expect(MockEventSource.instances).toHaveLength(0);
  });

  test("connects to /api/jobs/{id}/events and forwards progress/done", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const onProgress = vi.fn();
    const onDone = vi.fn();
    renderHook(() => useJobEvents("job_1", { onProgress, onDone }));

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(MockEventSource.instances[0]?.url).toBe("/api/jobs/job_1/events");

    act(() => {
      firstInstance().dispatchNamed("progress", {
        job_id: "job_1",
        status: "running",
        progress_pct: 40,
      });
    });
    expect(onProgress).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: "job_1", progress_pct: 40 }),
    );

    act(() => {
      firstInstance().dispatchNamed("done", { result: { foo: "bar" } });
    });
    expect(onDone).toHaveBeenCalledWith({ foo: "bar" });
    expect(MockEventSource.instances[0]?.closed).toBe(true);
  });

  test("treats a data-bearing `error` event as a terminal semantic error (Problem)", async () => {
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    const onError = vi.fn();
    renderHook(() => useJobEvents("job_1", { onError }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      firstInstance().dispatchNamed("error", { title: "失敗しました", code: "ingest_failed" });
    });
    expect(onError).toHaveBeenCalledWith({ title: "失敗しました", code: "ingest_failed" });
    expect(MockEventSource.instances[0]?.closed).toBe(true);
  });

  test("falls back to polling GET /api/jobs/{id} after 3 consecutive connection failures", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    vi.mocked(jobsGet).mockResolvedValue({
      data: { id: "job_1", kind: "article_generate", status: "succeeded", progress_pct: 100 },
    } as never);
    const onDone = vi.fn();
    renderHook(() => useJobEvents("job_1", { onDone }));

    await vi.waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = firstInstance();

    act(() => {
      source.dispatchConnError();
      source.dispatchConnError();
      source.dispatchConnError();
    });
    expect(source.closed).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(jobsGet).toHaveBeenCalledWith({ path: { job_id: "job_1" } });
    expect(onDone).toHaveBeenCalled();
    vi.useRealTimers();
  });

  test("stops polling when the job no longer exists", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
    vi.mocked(jobsGet).mockResolvedValue({
      data: undefined,
      error: { status: 404, code: "not_found", title: "見つかりません" },
      response: { status: 404 },
    } as never);
    const onError = vi.fn();
    renderHook(() => useJobEvents("missing_job", { onError }));

    await vi.waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = firstInstance();
    act(() => {
      source.dispatchConnError();
      source.dispatchConnError();
      source.dispatchConnError();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(jobsGet).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ status: 404, code: "not_found" }),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(jobsGet).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
