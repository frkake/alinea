/**
 * SSR 表示用の日付整形(plans/09-screens/4c §2.4)。
 *
 * タイムゾーンは `Asia/Tokyo` 固定(決定: サービスは日本語 UI・日本ユーザー前提で、匿名
 * 閲覧者のロケールを取得する手段を SSR で持たないため)。
 */

/** ISO 8601 → 「2026-07-06」(JST)。 */
export function formatDateYmd(iso: string): string {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo" }).format(new Date(iso));
}

/** ISO 8601 date → 「7/16」(JST、先頭ゼロなし)。 */
export function formatDateMd(iso: string): string {
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00+09:00" : ""));
  const f = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "numeric",
    day: "numeric",
  });
  return f.format(d).replace("月", "/").replace("日", "");
}
