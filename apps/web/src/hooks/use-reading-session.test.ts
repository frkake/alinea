import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { settingsGet } from "@yakudoku/api-client";
import { useReadingSession } from "@/hooks/use-reading-session";

vi.mock("@yakudoku/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@yakudoku/api-client")>();
  return { ...actual, settingsGet: vi.fn() };
});

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useReadingSession (plans/07 §8.1 / plans/03 §5.9)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(settingsGet).mockResolvedValue({ data: { reading: { track_reading_time: true } } } as never);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
    Object.defineProperty(window.navigator, "sendBeacon", {
      value: vi.fn(() => true),
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("sends a 30s heartbeat POST with accumulated active_seconds while visible", async () => {
    renderHook(() => useReadingSession({ itemId: "li_1" }));
    await flushMicrotasks();

    act(() => {
      vi.advanceTimersByTime(30_000);
    });

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/library-items/li_1/reading-sessions");
    const body = JSON.parse(init.body as string);
    expect(body.active_seconds).toBe(30);
    expect(typeof body.client_session_id).toBe("string");
  });

  test("does not send anything when reading.track_reading_time is false", async () => {
    vi.mocked(settingsGet).mockResolvedValue({
      data: { reading: { track_reading_time: false } },
    } as never);
    renderHook(() => useReadingSession({ itemId: "li_1" }));
    await flushMicrotasks();

    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    expect(fetch).not.toHaveBeenCalled();
  });

  test("sendBeacon fires immediately on visibilitychange(hidden) and pagehide", async () => {
    renderHook(() => useReadingSession({ itemId: "li_1" }));
    await flushMicrotasks();

    act(() => {
      vi.advanceTimersByTime(5_000);
    });

    act(() => {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(navigator.sendBeacon).toHaveBeenCalledTimes(1);

    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(navigator.sendBeacon).toHaveBeenCalledTimes(2);
  });

  test("does nothing when disabled", async () => {
    renderHook(() => useReadingSession({ itemId: "li_1", enabled: false }));
    await flushMicrotasks();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(settingsGet).not.toHaveBeenCalled();
  });
});
