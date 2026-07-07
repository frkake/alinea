import { DeadlineBadge } from "@/components/ui/DeadlineBadge";
import { formatDateMd, formatDateYmd } from "@/lib/format";
import { renderInlineMath } from "@/lib/katex-ssr";

/** コレクションヘッダブロック(plans/09-screens/4c §4.3)。 */
export interface ShareCollectionHeaderProps {
  name: string;
  description: string | null;
  sharedBy: string;
  updatedAt: string;
  itemCount: number;
  deadline: string | null;
}

export function ShareCollectionHeader({
  name,
  description,
  sharedBy,
  updatedAt,
  itemCount,
  deadline,
}: ShareCollectionHeaderProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0, color: "var(--pr-text)" }}>
        {renderInlineMath(name)}
      </h1>
      {description !== null ? (
        <p
          style={{
            fontSize: 12,
            color: "var(--pr-text-sub)",
            lineHeight: 1.7,
            margin: 0,
          }}
        >
          {renderInlineMath(description)}
        </p>
      ) : null}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 11,
          color: "var(--pr-text-muted)",
        }}
      >
        <span>
          {sharedBy} さんが共有 · 更新 {formatDateYmd(updatedAt)} · {itemCount} 本
          {deadline === null ? "" : " · "}
        </span>
        {deadline !== null ? (
          <DeadlineBadge date={formatDateMd(deadline)} variant="chip" withLabel fontSize={11} />
        ) : null}
      </div>
    </div>
  );
}
