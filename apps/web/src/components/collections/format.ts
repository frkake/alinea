import type { CollectionEntry } from "@/components/collections/types";
import { cardAuthors, venueOrYear } from "@/components/library/format";

/** "YYYY-MM-DD" → "M/D"(先頭ゼロなし)。plans/09-screens/4b §3.3。 */
export function formatDeadlineShort(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  return `${Number(m[2])}/${Number(m[3])}`;
}

/** 締切バッジ本文(4b §3.3・§4.3.1)。超過・当日・残り n 日の 3 分岐。 */
export function formatDeadlineBadge(iso: string, daysLeft: number): string {
  const short = formatDeadlineShort(iso);
  if (daysLeft > 0) return `締切 ${short} — 残り ${daysLeft} 日`;
  if (daysLeft === 0) return `締切 ${short} — 今日`;
  return `締切 ${short} — 超過 ${Math.abs(daysLeft)} 日`;
}

/** 未着手判定(4b §3.3 `isUnstarted`)。 */
export function isUnstarted(status: string, progressPct: number): boolean {
  return progressPct === 0 && status !== "done";
}

/** エントリ行のサブ行(4b §3.3 `formatSubLine`)。 */
export function formatSubLine(entry: CollectionEntry): string {
  const parts: string[] = [];
  const authors = cardAuthors(entry.library_item.paper.authors_short);
  if (authors) parts.push(authors);
  const vy = venueOrYear(entry.library_item.paper);
  if (vy) parts.push(vy);
  if (entry.presentation_minutes != null) parts.push(`発表 ${entry.presentation_minutes} 分`);
  if (entry.assignee != null && !entry.assignee_is_self) parts.push(`担当: ${entry.assignee}`);
  if (entry.note != null && entry.note !== "") parts.push(entry.note);
  return parts.join(" · ");
}

/** 共有 URL の表示形("https://" を落とす。4b §3.3)。 */
export function displayShareUrl(url: string): string {
  return url.replace(/^https?:\/\//, "");
}
