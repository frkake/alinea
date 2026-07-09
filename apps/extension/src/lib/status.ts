// 読書ステータスの型・ラベル・ドット色。値の正は @alinea/tokens(plans/08 §2)。
import { STATUS_COLORS, STATUS_LABELS, type ReadingStatus } from "@alinea/tokens";

/** API の Status 値(plans/03 §1.6)。tokens の ReadingStatus と同一。 */
export type Status = ReadingStatus;

/** 状態1 で選べる 3 択(3a §4.4)。 */
export type SaveStatus = Extract<Status, "planned" | "up_next" | "reading">;

export const SAVE_STATUS_OPTIONS: SaveStatus[] = ["planned", "up_next", "reading"];

/** 状態3 のステータス変更ドロップダウンの 6 値(3a §5.3)。 */
export const ALL_STATUSES: Status[] = [
  "planned",
  "up_next",
  "reading",
  "done",
  "reread",
  "on_hold",
];

export function statusLabel(status: string): string {
  return STATUS_LABELS[status as Status] ?? status;
}

export function statusColor(status: string): string {
  return STATUS_COLORS[status as Status] ?? "var(--pr-text-muted)";
}
