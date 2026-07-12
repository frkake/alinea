import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { translationsSectionTranslate } from "@alinea/api-client";
import { useJobEvents, type UseJobEventsOptions } from "@/hooks/useJobEvents";
import { useTableTranslation, type UseTableTranslationInput } from "@/hooks/use-table-translation";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return { ...actual, translationsSectionTranslate: vi.fn() };
});

vi.mock("@/hooks/useJobEvents", () => ({ useJobEvents: vi.fn() }));

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  let events: UseJobEventsOptions | null = null;
  vi.mocked(useJobEvents).mockImplementation((_jobId, options) => {
    events = options;
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const initialProps: UseTableTranslationInput = {
    itemId: "item-1",
    revisionId: "revision-1",
    style: "natural",
    translationSetId: "set-1",
    sectionId: "section-1",
    blockId: "table-1",
  };
  const hook = renderHook((input: UseTableTranslationInput) => useTableTranslation(input), {
    initialProps,
    wrapper,
  });
  return { hook, invalidate, events: () => events };
}

describe("useTableTranslation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("starts one table job, follows SSE, and invalidates only the exact viewer caches", async () => {
    vi.mocked(translationsSectionTranslate).mockResolvedValue({
      data: { job_id: "job-1" },
    } as never);
    const { hook, invalidate, events } = setup();

    await act(async () => {
      await hook.result.current.start();
    });

    expect(translationsSectionTranslate).toHaveBeenCalledWith({
      path: { set_id: "set-1", section_id: "section-1" },
      body: { block_id: "table-1" },
      throwOnError: true,
    });
    expect(hook.result.current.status).toBe("pending");
    expect(vi.mocked(useJobEvents).mock.calls.at(-1)?.[0]).toBe("job-1");

    await act(async () => {
      events()?.onProgress?.({
        job_id: "job-1",
        status: "running",
        progress_pct: 50,
      });
      events()?.onDone?.(null);
    });

    await waitFor(() => expect(hook.result.current.status).toBe("succeeded"));
    expect(invalidate).toHaveBeenCalledTimes(3);
    expect(invalidate).toHaveBeenNthCalledWith(1, {
      queryKey: ["units", "revision-1", "natural", "section-1"],
      exact: true,
    });
    expect(invalidate).toHaveBeenNthCalledWith(2, {
      queryKey: ["document", "revision-1"],
      exact: true,
    });
    expect(invalidate).toHaveBeenNthCalledWith(3, {
      queryKey: ["viewer", "item-1"],
      exact: true,
    });
  });

  test("surfaces submission and SSE errors and retries the same physical table", async () => {
    vi.mocked(translationsSectionTranslate)
      .mockRejectedValueOnce({ title: "開始できませんでした" })
      .mockResolvedValueOnce({ data: { job_id: "job-2" } } as never)
      .mockResolvedValueOnce({ data: { job_id: "job-3" } } as never);
    const { hook, events } = setup();

    await act(async () => {
      await hook.result.current.start();
    });
    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.error).toBe("開始できませんでした");

    await act(async () => {
      await hook.result.current.retry();
    });
    expect(hook.result.current.status).toBe("pending");
    expect(vi.mocked(useJobEvents).mock.calls.at(-1)?.[0]).toBe("job-2");

    act(() => {
      events()?.onError?.({ title: "モデルが応答しませんでした" });
    });
    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.error).toBe("モデルが応答しませんでした");

    await act(async () => {
      await hook.result.current.retry();
    });
    expect(translationsSectionTranslate).toHaveBeenCalledTimes(3);
    expect(vi.mocked(useJobEvents).mock.calls.at(-1)?.[0]).toBe("job-3");
  });

  test("drops a pending job when the viewer translation identity changes", async () => {
    vi.mocked(translationsSectionTranslate)
      .mockResolvedValueOnce({ data: { job_id: "job-old" } } as never)
      .mockResolvedValueOnce({ data: { job_id: "job-new" } } as never);
    const { hook, invalidate, events } = setup();

    await act(async () => {
      await hook.result.current.start();
    });
    expect(vi.mocked(useJobEvents).mock.calls.at(-1)?.[0]).toBe("job-old");

    hook.rerender({
      itemId: "item-1",
      revisionId: "revision-1",
      style: "literal",
      translationSetId: "set-2",
      sectionId: "section-1",
      blockId: "table-1",
    });

    expect(hook.result.current.status).toBe("idle");
    expect(vi.mocked(useJobEvents).mock.calls.at(-1)?.[0]).toBeNull();
    act(() => {
      events()?.onDone?.({ fallback: 0 });
    });
    expect(hook.result.current.status).toBe("idle");
    expect(invalidate).not.toHaveBeenCalled();

    await act(async () => {
      await hook.result.current.start();
    });
    expect(translationsSectionTranslate).toHaveBeenLastCalledWith({
      path: { set_id: "set-2", section_id: "section-1" },
      body: { block_id: "table-1" },
      throwOnError: true,
    });
    expect(vi.mocked(useJobEvents).mock.calls.at(-1)?.[0]).toBe("job-new");
  });

  test("keeps fallback completion retryable instead of showing false success", async () => {
    vi.mocked(translationsSectionTranslate)
      .mockResolvedValueOnce({ data: { job_id: "job-fallback" } } as never)
      .mockResolvedValueOnce({ data: { job_id: "job-retry" } } as never);
    const { hook, events } = setup();

    await act(async () => {
      await hook.result.current.start();
    });
    act(() => {
      events()?.onDone?.({ fallback: 1 });
    });

    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.error).toContain("翻訳できませんでした");
    await act(async () => {
      await hook.result.current.retry();
    });
    expect(vi.mocked(useJobEvents).mock.calls.at(-1)?.[0]).toBe("job-retry");
  });
});
