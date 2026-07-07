import { STATUS_LABELS, type ReadingStatus } from "@yakudoku/tokens";
import type { LibraryItemSummary, PaperBib } from "@yakudoku/api-client";

/**
 * ライブラリ画面の書誌・日付・数値の派生表記(1e §2.6/§4、4a §4.7/§4.8 の決定を集約)。
 * API 値 → 画面表示文字列の変換はここ 1 箇所に閉じる。
 */

/** API の Status(plans/03 §1.6)は @yakudoku/tokens の ReadingStatus キーと同一。安全にキャストする。 */
export function toReadingStatus(status: string): ReadingStatus {
  return Object.prototype.hasOwnProperty.call(STATUS_LABELS, status)
    ? (status as ReadingStatus)
    : "planned";
}

/** 品質レベル(A/B)。想定外値は B にフォールバック。 */
export function toQuality(level: string): "A" | "B" {
  return level === "A" ? "A" : "B";
}

/** 優先度(high/mid/low)。null/想定外は null。 */
export function toPriority(priority: string | null | undefined): "high" | "mid" | "low" | null {
  return priority === "high" || priority === "mid" || priority === "low" ? priority : null;
}

/** "YYYY-MM-DD" → "M/D"(ゼロ埋めなし)。null/空は null(未設定=「—」の判定に使う)。 */
export function formatShortDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  return `${Number(m[2])}/${Number(m[3])}`;
}

/** reading_seconds_total → 小数1桁の時間。0 は null(=「—」)。 */
export function formatReadingHours(seconds: number): number | null {
  if (!seconds) return null;
  return Math.round((seconds / 3600) * 10) / 10;
}

/** カード用: authors_short(", " 区切りの姓)を「Liu et al.」形式へ(4a §4.7 の決定)。 */
export function cardAuthors(authorsShort: string): string {
  const parts = authorsShort
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length <= 2) return parts.join(", ");
  return `${parts[0]} et al.`;
}

/** venue(年を含む文字列)優先、なければ year(4a §4.7 の決定)。 */
export function venueOrYear(paper: PaperBib): string | null {
  if (paper.venue) return paper.venue;
  if (paper.year != null) return String(paper.year);
  return null;
}

/**
 * テーブルの著者・出典行(1e §2.6 の決定)。
 * authors_short · venue(=年込み)+ arXiv:{id}(あれば)/ source=upload かつ arxiv なしは「アップロード」。
 */
export function tableBibLine(item: LibraryItemSummary): string {
  const paper = item.paper;
  const parts: string[] = [];
  if (paper.authors_short) parts.push(paper.authors_short);
  const vy = venueOrYear(paper);
  if (vy) parts.push(vy);
  let line = parts.join(" · ");
  if (paper.arxiv_id) {
    line += `${line ? " · " : ""}arXiv:${paper.arxiv_id}`;
  } else if (item.source === "upload") {
    line += `${line ? " · " : ""}アップロード`;
  }
  return line;
}

/** カードの書誌行(4a §4.7): cardAuthors · venue(=年込み)。両方 null なら著者のみ。 */
export function cardBibLine(paper: PaperBib): string {
  const authors = cardAuthors(paper.authors_short);
  const vy = venueOrYear(paper);
  return [authors, vy].filter(Boolean).join(" · ");
}
