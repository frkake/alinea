/** 空状態(plans/08 §5.21)。アイコン・イラストは使わない。 */
export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "32px 16px",
        gap: 6,
      }}
    >
      <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--pr-text-sub2)" }}>{title}</div>
      {description ? (
        <div
          style={{
            fontSize: 11,
            color: "var(--pr-text-muted)",
            lineHeight: 1.6,
            textAlign: "center",
          }}
        >
          {description}
        </div>
      ) : null}
      {action ? (
        <button
          type="button"
          onClick={action.onClick}
          style={{
            height: 26,
            padding: "0 12px",
            border: "1px solid var(--pr-border-control)",
            borderRadius: 6,
            fontSize: 11,
            color: "var(--pr-text-mid)",
            background: "var(--pr-bg-control)",
            cursor: "pointer",
            fontFamily: "inherit",
            marginTop: 4,
          }}
        >
          {action.label}
        </button>
      ) : null}
    </div>
  );
}
