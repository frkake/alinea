import type { ReactNode } from "react";

/** 設定セクション(4f §3.2): 見出し + カードを縦 flex gap:12px で包む。 */
export interface SettingsSectionProps {
  title: string;
  titleNote?: string;
  children: ReactNode;
}

export function SettingsSection({ title, titleNote, children }: SettingsSectionProps) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700, display: "flex", alignItems: "baseline" }}>
        {title}
        {titleNote ? (
          <span
            style={{
              marginLeft: 6,
              fontSize: 10.5,
              fontWeight: 400,
              color: "var(--pr-text-muted)",
            }}
          >
            {titleNote}
          </span>
        ) : null}
      </h2>
      {children}
    </section>
  );
}
