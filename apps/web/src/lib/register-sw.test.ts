import { afterEach, describe, expect, test, vi } from "vitest";
import { registerServiceWorker } from "./register-sw";

/**
 * Service Worker 登録のプログレッシブエンハンスメント(spec 2026-07-16-pwa-offline-design §C)。
 * 非対応ブラウザ/SSR では no-op(投げない)、対応時のみ /sw.js を scope "/" で登録する。
 */
describe("registerServiceWorker (progressive enhancement)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  test("no-op when serviceWorker is unsupported (no throw, no register)", () => {
    // navigator は存在するが serviceWorker 未対応、というブラウザを模す。
    vi.stubGlobal("navigator", {} as Navigator);
    expect(() => registerServiceWorker()).not.toThrow();
  });

  test("registers /sw.js with scope '/' when supported", () => {
    const register = vi.fn().mockResolvedValue({} as ServiceWorkerRegistration);
    vi.stubGlobal("navigator", { serviceWorker: { register } } as unknown as Navigator);

    registerServiceWorker();

    expect(register).toHaveBeenCalledTimes(1);
    expect(register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
  });

  test("swallows registration rejection (does not surface an unhandled rejection)", async () => {
    const register = vi.fn().mockRejectedValue(new Error("boom"));
    vi.stubGlobal("navigator", { serviceWorker: { register } } as unknown as Navigator);

    expect(() => registerServiceWorker()).not.toThrow();
    // マイクロタスクを流して reject が catch 済みであることを確認(未処理拒否を出さない)。
    await Promise.resolve();
    expect(register).toHaveBeenCalledTimes(1);
  });
});
