import { describe, expect, test } from "vitest";
import manifest from "./manifest";

/**
 * PWA インストール可能性(S13 / M3、docs/10 §5 + spec 2026-07-16-pwa-offline-design)。
 * Next.js が `app/manifest.ts` を `/manifest.webmanifest` として配信し <head> に自動リンクする。
 */
describe("web app manifest (app/manifest.ts)", () => {
  const m = manifest();

  test("declares installable identity + standalone display", () => {
    expect(m.name).toBe("Alinea");
    expect(m.short_name).toBe("Alinea");
    expect(m.description).toBeTruthy();
    expect(m.start_url).toBe("/");
    expect(m.scope).toBe("/");
    expect(m.display).toBe("standalone");
    expect(m.lang).toBe("ja");
  });

  test("theme/background colors match the app palette (tokens: app bg #FBFAF7)", () => {
    // layout.tsx の viewport.themeColor と一致させる(スプラッシュ=初回描画色)。
    expect(m.theme_color).toBe("#FBFAF7");
    expect(m.background_color).toBe("#FBFAF7");
  });

  test("ships 192/512 'any' icons plus a 512 maskable icon", () => {
    const icons = m.icons ?? [];
    const sizes = icons.map((i) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");

    const maskable = icons.filter((i) => (i.purpose ?? "").includes("maskable"));
    expect(maskable.length).toBeGreaterThan(0);
    expect(maskable.some((i) => i.sizes === "512x512")).toBe(true);

    // すべての icon は type と実在しそうな src(public 配下の絶対パス)を持つ。
    for (const icon of icons) {
      expect(icon.type).toBe("image/png");
      expect(icon.src.startsWith("/")).toBe(true);
    }
  });
});
