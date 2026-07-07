import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { MOBILE_MAX_WIDTH, useIsMobile, useMediaQuery } from "@/hooks/useMediaQuery";
import { mockMatchMedia } from "@/test-utils/mockMatchMedia";

afterEach(() => {
  vi.unstubAllGlobals();
});

// mobile.md §2: MOBILE_MAX_WIDTH = 767 / useMediaQuery は matchMedia の初期値を採用する。
describe("useMediaQuery / useIsMobile (mobile.md §2)", () => {
  test("MOBILE_MAX_WIDTH is 767", () => {
    expect(MOBILE_MAX_WIDTH).toBe(767);
  });

  test("returns false when matchMedia is unavailable (SSR-safe default, desktop)", () => {
    const { result } = renderHook(() => useMediaQuery("(max-width: 767px)"));
    expect(result.current).toBe(false);
  });

  test("useIsMobile reflects matchMedia(max-width: 767px).matches === true", () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  test("useIsMobile reflects matchMedia(max-width: 767px).matches === false", () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });
});
