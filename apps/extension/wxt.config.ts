import react from "@vitejs/plugin-react";
import { defineConfig, type WxtViteConfig } from "wxt";

// WXT + React(Manifest V3)。plans/09-screens/3a §1・§5.9、plans/10-extension.md。
// React は @wxt-dev/module-react を使わず @vitejs/plugin-react を vite プラグインとして注入する
// (このワークスペースにインストール済みの依存に合わせるため。deviations 参照)。
export default defineConfig({
  // 3a §1 のエントリポイント構成(src/entrypoints, src/lib, src/components)。
  srcDir: "src",
  // @vitejs/plugin-react は vite7 の Plugin 型、WXT は vite6 の型を期待するため型が食い違う
  // (hotUpdate フックの差)。実行時は互換なので WxtViteConfig へキャストする。
  vite: () => ({ plugins: [react()] }) as unknown as WxtViteConfig,
  // headless 開発機(DISPLAY 無し・CHROME_PATH 未設定)では自動ブラウザ起動が失敗し
  // pnpm dev 全体が落ちるため無効化。拡張は .output/chrome-mv3-dev を手動で
  // chrome://extensions に読み込む(E2E は Playwright 自前の Chromium を使うため影響なし)。
  webExt: {
    disabled: true,
  },
  // wxt の dev サーバ既定ポートは 3000 で Next.js(@alinea/web)と衝突し、
  // pnpm dev で先に起動した側が 3000 を奪って web が 3001 へ逃げてしまう。
  // 拡張の HMR 用サーバは 3717 に固定する(plans/00 §9: web=3000 が正)。
  dev: {
    server: {
      port: 3717,
    },
  },
  manifest: {
    // 3a §5.9(確定 manifest)。
    name: "Alinea — 論文をライブラリへ",
    description: "arXiv 論文の URL だけを送ってライブラリへ保存します(取得・解析はサーバー)。",
    // 権限(plans/10 §3.3): 現在タブ URL 判定 + storage + arXiv ページ内ピルの動的登録(scripting)。
    // scripting 自体はサイトアクセスを持たない(実際の arxiv.org アクセスは下の
    // optional_host_permissions をユーザーがオプトインで許可した場合のみ)。
    permissions: ["activeTab", "storage", "scripting"],
    // API 呼び出し(セッションクッキー共有)に必要なホスト権限。開発は localhost:3000(3a §5.9 決定)。
    host_permissions: ["http://localhost:3000/*", "https://alinea.app/*"],
    // ページ内「A 保存」ピル(オプトイン・将来)。既定では要求しない。
    optional_host_permissions: ["https://arxiv.org/*"],
    action: {
      default_title: "Alineaに保存",
    },
  },
});
