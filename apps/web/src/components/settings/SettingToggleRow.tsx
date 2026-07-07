import { Toggle } from "@/components/ui/Toggle";

/** トグル行(4f §4.4.2)。左テキスト列 + 右 Toggle。 */
export interface SettingToggleRowProps {
  title: string;
  description: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  divider?: boolean;
  disabled?: boolean;
}

export function SettingToggleRow({
  title,
  description,
  checked,
  onChange,
  divider = false,
  disabled = false,
}: SettingToggleRowProps) {
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
      <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{title}</span>
        <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{description}</span>
      </div>
      <Toggle checked={checked} onChange={onChange} disabled={disabled} ariaLabel={title} />
    </div>
  );
}
