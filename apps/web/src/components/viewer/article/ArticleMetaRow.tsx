import { AIBadge } from "@/components/ui/AIBadge";

/** 記事メタ行(1h §4.5)。AIBadge('generated') + 免責文(サーバー逐語生成)。 */
export function ArticleMetaRow({ disclaimer }: { disclaimer: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--pr-text-muted)" }}>
      <AIBadge variant="generated" />
      <span>{disclaimer}</span>
    </div>
  );
}
