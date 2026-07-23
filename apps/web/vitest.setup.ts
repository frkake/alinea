import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { client } from "@alinea/api-client";

// 生成 SDK(@alinea/api-client)は内部で `new Request(url, ...)` を組み立てる。
// 本番は baseUrl:"" の相対 `/api/...` を使うが(同一オリジン)、テストの jsdom 環境が
// 依存する Node/undici は相対 URL を絶対化する base を持たないため
// `new Request("/api/...")` が "Failed to parse URL" で throw する。
// テスト時のみ絶対 baseUrl を与えて Request 構築を成立させる(credentials 等の他設定は
// mergeConfigs で保持される)。SDK を叩くテストは fetch に渡る Request/URL から
// パスを取り出して検証する。
// なお `@alinea/api-client` を丸ごと差し替える test(vi.mock)では client が
// 差し替え後の値になり得るため、存在を確認してから設定する。
client?.setConfig?.({ baseUrl: "http://localhost" });

afterEach(() => {
  cleanup();
});
