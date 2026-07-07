import type { ReactNode } from "react";

/**
 * ラベル+説明(左)/ コントロール(右)の汎用行(4f §4.7.5)。
 * SettingToggleRow と同じ寸法(padding:12px 18px)で、右側の内容だけ差し替える。
 */
export interface SettingsControlRowProps {
  title: string;
  description?: string;
  divider?: boolean;
  children: ReactNode;
}

export function SettingsControlRow({
  title,
  description,
  divider = false,
  children,
}: SettingsControlRowProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 18px",
        borderBottom: divider ? "1px solid var(--pr-border-hair)" : undefined,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{title}</span>
        {description ? (
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{description}</span>
        ) : null}
      </div>
      <div style={{ display: "flex", alignItems: "center", flex: "none" }}>{children}</div>
    </div>
  );
}
