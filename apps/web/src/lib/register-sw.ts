/**
 * Service Worker 登録(PWA / S13・M3、spec 2026-07-16-pwa-offline-design §C)。
 *
 * プログレッシブエンハンスメント: `serviceWorker` 非対応の環境(古いブラウザ・SSR)では
 * 何もせず戻る。対応時のみ public/sw.js を scope "/" で登録する。登録失敗は握りつぶす
 * (SW はあくまで加速・オフライン耐性の付加機能であり、失敗してもアプリは通常動作する)。
 *
 * ここでは HTML/`/api/*`/auth は一切キャッシュしない(SW 側の fetch ハンドラで素通し)。
 * SSR と 401→/login リダイレクト(lib/auth-redirect.ts)には影響しない。
 */
export function registerServiceWorker(): void {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return;
  }
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
    // 登録失敗は無視(付加機能のため)。
  });
}
