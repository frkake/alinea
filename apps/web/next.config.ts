import type { NextConfig } from "next";

/**
 * dev 用: /api/* を内部 API へプロキシ(plans/00 §2、Global Constraints)。
 * 本番は Caddy が同一オリジンで振り分けるため、この rewrites は開発時のみ有効。
 * API_INTERNAL_URL 未設定時は docker-compose の既定ポート 8000 を使う。
 */
const apiInternalUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Lint は turbo lint(ルート flat config)で実行するため build 時はスキップする。
  eslint: { ignoreDuringBuilds: true },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiInternalUrl}/api/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        // 共有ページ(4c)は検索エンジン非掲載+60 秒 CDN キャッシュ(plans/09-screens/4c §1・§6.2)。
        source: "/c/:token",
        headers: [
          { key: "X-Robots-Tag", value: "noindex" },
          { key: "Cache-Control", value: "public, s-maxage=60, stale-while-revalidate=300" },
        ],
      },
    ];
  },
};

export default nextConfig;
