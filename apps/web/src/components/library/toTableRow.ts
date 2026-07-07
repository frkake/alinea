import type { LibraryItemSummary } from "@yakudoku/api-client";
import type { LibraryTableRow } from "@/components/ui/LibraryTable";
import {
  formatReadingHours,
  formatShortDate,
  tableBibLine,
  toPriority,
  toQuality,
  toReadingStatus,
} from "@/components/library/format";

/**
 * LibraryItemSummary → LibraryTableRow(1e §2.6 の決定)。
 * 未供給列(priority/deadline/reading_hours/comprehension が null・0)は
 * LibraryTable 側で「—」に描画される。
 */
export function toTableRow(item: LibraryItemSummary): LibraryTableRow {
  return {
    id: item.id,
    title: item.paper.title,
    titleBadge: item.source === "upload" ? "pdf_import" : undefined,
    authorsLine: tableBibLine(item),
    thumbnailUrl: item.thumbnail_url ?? null,
    status: toReadingStatus(item.status),
    quality: toQuality(item.quality_level),
    tags: item.tags,
    priority: toPriority(item.priority),
    deadline: formatShortDate(item.deadline),
    readingHours: formatReadingHours(item.reading_seconds_total),
    comprehension: item.comprehension ?? null,
    addedAt: formatShortDate(item.added_at) ?? "—",
  };
}
