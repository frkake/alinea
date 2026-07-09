import type { AttributionContentOut } from "@alinea/api-client";

/** 出典ブロック(1h §4.11)。`locked: true` — 削除不可・ホバーツールバー非表示。 */
export function AttributionBlock({ attribution }: { attribution: AttributionContentOut }) {
  return (
    <div
      style={{
        border: "1px solid var(--pr-border-card)",
        borderRadius: 8,
        background: "var(--pr-bg-inset)",
        padding: "10px 14px",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <div style={{ fontSize: 10.5, lineHeight: 1.7, color: "var(--pr-text-sub)", flex: 1 }}>
        {attribution.text}
      </div>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          height: 18,
          padding: "0 8px",
          borderRadius: 4,
          background: "var(--pr-bg-locked-badge)",
          color: "var(--pr-text-icon)",
          fontSize: 9.5,
          fontWeight: 600,
          flex: "none",
        }}
      >
        自動挿入 · 削除不可
      </span>
    </div>
  );
}
