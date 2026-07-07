/**
 * 通知ポップオーバーの文字列整形(4a §4.3 の決定)。
 * - 相対時刻: 同日「今日 H:mm」/ 前日「昨日 H:mm」/ 同年「M/D H:mm」/ 年跨ぎ「YYYY/M/D H:mm」
 *   (時はゼロ埋めなし、分は2桁ゼロ埋め)。
 * - タイトルの省略: 48 文字超は先頭 46 文字+「…」。
 */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** `created_at`(ISO・UTC)→ ローカル時刻の相対表示(4a §4.3 決定)。 */
export function formatRelativeNotificationTime(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const time = `${d.getHours()}:${pad2(d.getMinutes())}`;

  if (isSameLocalDay(d, now)) return `今日 ${time}`;

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (isSameLocalDay(d, yesterday)) return `昨日 ${time}`;

  if (d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}/${d.getDate()} ${time}`;
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${time}`;
}

/** 通知本文中の論文タイトル省略(4a §4.3: 48 字超は先頭 46 字+「…」)。 */
export function truncateNotificationTitle(title: string, max = 46): string {
  return title.length > 48 ? `${title.slice(0, max)}…` : title;
}
