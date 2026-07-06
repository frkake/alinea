import type { CSSProperties, ReactNode } from "react";
import { CountBadge } from "@/components/ui/CountBadge";
import { DeadlineBadge } from "@/components/ui/DeadlineBadge";

/** サイドバーナビ(plans/08 §5.14)。 */
export interface SidebarNavItem {
  id: string;
  label: string;
  href: string;
  count?: number;
  deadline?: string | null;
  active?: boolean;
}

export interface SidebarNavProps {
  /** ホーム/ライブラリ/語彙帳。 */
  main: SidebarNavItem[];
  /** コレクション/保存フィルタ。 */
  sections: Array<{ heading: string; items: SidebarNavItem[] }>;
  footer?: ReactNode;
}

function NavRow({ item, dense }: { item: SidebarNavItem; dense: boolean }) {
  const style: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: dense ? "6px 10px" : "7px 10px",
    borderRadius: 6,
    textDecoration: "none",
    color: item.active ? "var(--pr-acc)" : "var(--pr-text-nav)",
    background: item.active ? "var(--pr-acc-s)" : "transparent",
    fontWeight: item.active ? 600 : 400,
  };
  return (
    <a href={item.href} aria-current={item.active ? "page" : undefined} style={style}>
      <span style={{ flex: 1 }}>{item.label}</span>
      {item.deadline ? <DeadlineBadge date={item.deadline} variant="chip" /> : null}
      {typeof item.count === "number" ? (
        <CountBadge count={item.count} variant="nav" />
      ) : null}
    </a>
  );
}

export function SidebarNav({ main, sections, footer }: SidebarNavProps) {
  return (
    <nav
      aria-label="サイドバー"
      style={{
        width: 216,
        flex: "none",
        background: "var(--pr-bg-pane)",
        borderRight: "1px solid var(--pr-border-pane)",
        padding: "12px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        fontSize: 12.5,
        color: "var(--pr-text-nav)",
      }}
    >
      {main.map((item) => (
        <NavRow key={item.id} item={item} dense={false} />
      ))}

      {sections.map((section) => (
        <div key={section.heading} style={{ display: "contents" }}>
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 600,
              color: "var(--pr-text-muted)",
              letterSpacing: "0.4px",
              padding: "14px 10px 4px",
            }}
          >
            {section.heading}
          </div>
          {section.items.map((item) => (
            <NavRow key={item.id} item={item} dense />
          ))}
        </div>
      ))}

      {footer ? (
        <div
          style={{
            marginTop: 8,
            borderTop: "1px solid var(--pr-border-pane)",
            padding: "6px 10px",
            paddingTop: 12,
            color: "var(--pr-text-sub2)",
            fontSize: 11.5,
          }}
        >
          {footer}
        </div>
      ) : null}
    </nav>
  );
}
