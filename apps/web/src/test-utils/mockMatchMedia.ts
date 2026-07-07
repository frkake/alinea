import { vi } from "vitest";

/**
 * `window.matchMedia` を固定値でスタブする(mobile.md §6-5「Vitest(matchMedia モック)」)。
 * `useMediaQuery`/`useIsMobile` はこの値を初期状態としてそのまま採用する。
 * 呼び出し側の `afterEach`(vitest.setup.ts の cleanup)では自動で戻らないため、
 * テスト側で `vi.unstubAllGlobals()` 等は不要(`stubGlobal` は describe/test 単位で上書きする運用)。
 */
export function mockMatchMedia(matches: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}
