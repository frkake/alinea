import type { MetadataRoute } from "next";

/**
 * PWA Web アプリマニフェスト(S13 / M3、docs/10 §5 + spec 2026-07-16-pwa-offline-design)。
 *
 * Next.js が `app/manifest.ts` を `/manifest.webmanifest` として配信し、<head> に
 * `<link rel="manifest">` を自動挿入する(新規依存ゼロ)。色は既存デザイントークン
 * (packages/tokens: アプリ地 #FBFAF7 / スレートアクセント #3E5C76)を再利用し、
 * theme_color は layout.tsx の viewport.themeColor と一致させる。
 *
 * アイコンは brand マーク(app/icon.svg)から一度だけ生成し public/icons に
 * 静的コミット済み(生成手順: apps/web/scripts/gen-pwa-icons.mjs)。maskable は
 * Android のアダプティブアイコンで glyph が切れないよう全面スレート背景の版を使う。
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "Alinea",
    short_name: "Alinea",
    description: "英語論文を日本語で深く読むための研究者向けワークベンチ。",
    start_url: "/",
    scope: "/",
    display: "standalone",
    lang: "ja",
    dir: "ltr",
    categories: ["education", "productivity"],
    theme_color: "#FBFAF7",
    background_color: "#FBFAF7",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        src: "/icons/icon-512-maskable.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
