import { renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { useSSE } from "@/lib/sse";

// jsdom には EventSource が実装されていないため、フォールバック経路を検証する。
describe("useSSE", () => {
  test("falls back to polling when EventSource is unavailable", () => {
    const onFallbackChange = vi.fn();
    const { result } = renderHook(() => useSSE({ onFallbackChange }));
    expect(result.current.fallbackActive).toBe(true);
    expect(onFallbackChange).toHaveBeenCalledWith(true);
  });

  test("stays idle when disabled", () => {
    const onFallbackChange = vi.fn();
    const { result } = renderHook(() => useSSE({ enabled: false, onFallbackChange }));
    expect(result.current.fallbackActive).toBe(false);
    expect(result.current.connected).toBe(false);
    expect(onFallbackChange).not.toHaveBeenCalled();
  });
});
