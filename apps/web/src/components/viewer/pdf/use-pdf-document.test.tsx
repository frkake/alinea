import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { loadPdfjs } from "@/lib/pdfjs";
import { usePdfDocument } from "./use-pdf-document";

vi.mock("@/lib/pdfjs", () => ({ loadPdfjs: vi.fn() }));

function queryWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("usePdfDocument cache identity", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        arrayBuffer: () => Promise.resolve(new Uint8Array([1, 2, 3]).buffer),
      }),
    );
    vi.mocked(loadPdfjs).mockResolvedValue({
      getDocument: () => ({
        promise: Promise.resolve({
          numPages: 1,
          destroy: vi.fn(),
        }),
      }),
    } as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("refetches translated bytes when the effective translation set changes", async () => {
    const { rerender } = renderHook(
      ({ translationSetId }: { translationSetId: string | null }) =>
        usePdfDocument("paper-1", true, "translated", "natural", {
          revisionId: "rev-1",
          translationSetId,
        }),
      {
        initialProps: { translationSetId: null as string | null },
        wrapper: queryWrapper(),
      },
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

    rerender({ translationSetId: "personal-set-1" });

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });
});
