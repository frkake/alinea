import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { usePdfAvailability } from "./use-pdf-availability";

function queryWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("usePdfAvailability cache identity", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        body: { cancel: vi.fn().mockResolvedValue(undefined) },
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("refetches availability when the revision changes", async () => {
    const { rerender } = renderHook(
      ({ revisionId }: { revisionId: string }) =>
        usePdfAvailability("paper-1", "translated", "natural", {
          revisionId,
          translationSetId: "set-1",
        }),
      {
        initialProps: { revisionId: "rev-1" },
        wrapper: queryWrapper(),
      },
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

    rerender({ revisionId: "rev-2" });

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });
});
