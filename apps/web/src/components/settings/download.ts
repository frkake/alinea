/**
 * 同一オリジン GET を hidden `<a>` クリックで発火してダウンロードさせる(4f §4.6)。
 * サーバーは `Content-Disposition: attachment` を返すため、ページ遷移せずダウンロードのみ発生する。
 */
export function triggerDownload(url: string): void {
  if (typeof document === "undefined") return;
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}
