"use client";

/**
 * 相対日時表記(1b §5.11)。当日=「今日 H:mm」/ 前日=「昨日 H:mm」/
 * 同年=「M/D H:mm」/ 前年以前=「YYYY/M/D」。時は 0 埋めなし、分は 0 埋め。
 */
export function formatRelativeDay(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const hm = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const dayDiff = Math.round((startOf(now) - startOf(d)) / 86_400_000);
  if (dayDiff === 0) return `今日 ${hm}`;
  if (dayDiff === 1) return `昨日 ${hm}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

export interface ResumeBannerProps {
  /** last_position.section_display(例 "§3.1 実験")。 */
  sectionDisplay: string;
  /** last_position.saved_at(ISO)。 */
  savedAt: string;
  onResume: () => void;
  onDismiss: () => void;
}

/** 前回位置バナー(1b §4.4)。本文上部中央に浮遊。 */
export function ResumeBanner({ sectionDisplay, savedAt, onResume, onDismiss }: ResumeBannerProps) {
  return (
    <div
      role="status"
      style={{
        position: "absolute",
        top: 14,
        left: 12,
        right: 12,
        maxWidth: 720,
        margin: "0 auto",
        zIndex: "var(--z-banner)" as unknown as number,
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        justifyContent: "center",
        gap: "6px 8px",
        minWidth: 0,
        background: "var(--pr-bg-card)",
        border: "1px solid var(--pr-border-control)",
        borderRadius: 14,
        padding: "7px 8px 7px 16px",
        boxShadow: "var(--pr-shadow-banner)",
        overflow: "hidden",
      }}
    >
      <span
        title={`前回はここまで: ${sectionDisplay} · ${formatRelativeDay(savedAt)}`}
        style={{
          flex: "1 1 220px",
          minWidth: 0,
          maxWidth: "100%",
          fontSize: 12,
          color: "var(--pr-text-mid)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        前回はここまで: <b>{sectionDisplay}</b>
        <span style={{ color: "var(--pr-text-muted)" }}> · {formatRelativeDay(savedAt)}</span>
      </span>
      <button
        type="button"
        onClick={onResume}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          height: 24,
          maxWidth: "100%",
          minWidth: 0,
          padding: "0 12px",
          borderRadius: 999,
          border: "none",
          background: "var(--pr-acc)",
          color: "#FFFFFF",
          fontSize: 11.5,
          fontWeight: 600,
          fontFamily: "inherit",
          cursor: "pointer",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          flex: "0 1 auto",
        }}
      >
        続きから ↓
      </button>
      <button
        type="button"
        aria-label="閉じる"
        onClick={onDismiss}
        style={{
          border: "none",
          background: "transparent",
          fontSize: 12,
          color: "var(--pr-text-muted)",
          padding: "0 6px",
          cursor: "pointer",
          flex: "none",
        }}
      >
        ×
      </button>
    </div>
  );
}
