/** 共有者のメモボックス(plans/09-screens/4c §4.5)。 */
export interface SharedNoteBoxProps {
  /** `one_line_note` 由来のプレーンテキスト。Markdown 解釈はしない(決定)。 */
  note: string;
}

export function SharedNoteBox({ note }: SharedNoteBoxProps) {
  return (
    <div
      style={{
        display: "flex",
        gap: 8,
        background: "var(--pr-bg-hover)",
        border: "1px solid #EFECE3", // トークンに存在しない 4c 固有色(決定。plans/09-screens/4c §4.5)
        borderRadius: 7,
        padding: "8px 11px",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          height: 16,
          padding: "0 6px",
          borderRadius: 3,
          background: "var(--pr-src-note-bg)",
          color: "var(--pr-src-note-fg)",
          fontSize: 9,
          fontWeight: 700,
          flex: "none",
          marginTop: 1,
        }}
      >
        共有者のメモ
      </span>
      <span style={{ fontSize: 11, lineHeight: 1.65, color: "var(--pr-text-mid)" }}>{note}</span>
    </div>
  );
}
