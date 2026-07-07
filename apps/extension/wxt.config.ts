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
  manifest: {
    // 3a §5.9(確定 manifest)。
    name: "訳読 — 論文をライブラリへ",
    description: "arXiv 論文の URL だけを送ってライブラリへ保存します(取得・解析はサーバー)。",
    // 権限(plans/10 §3.3): 現在タブ URL 判定 + storage + arXiv ページ内ピルの動的登録(scripting)。
    // scripting 自体はサイトアクセスを持たない(実際の arxiv.org アクセスは下の
    // optional_host_permissions をユーザーがオプトインで許可した場合のみ)。
    permissions: ["activeTab", "storage", "scripting"],
    // API 呼び出し(セッションクッキー共有)に必要なホスト権限。開発は localhost:3000(3a §5.9 決定)。
    host_permissions: ["http://localhost:3000/*", "https://yakudoku.app/*"],
    // ページ内「訳 保存」ピル(オプトイン・将来)。既定では要求しない。
    optional_host_permissions: ["https://arxiv.org/*"],
    action: {
      default_title: "訳読に保存",
    },
  },
});
