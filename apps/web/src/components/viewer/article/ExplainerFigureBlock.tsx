import type { ExplainerContentOut } from "@yakudoku/api-client";
import { AIBadge } from "@/components/ui/AIBadge";

/** 解説図ブロック(1h §4.9 決定。docs/07 §2.3)。出典行を AIBadge に置き換える。 */
export function ExplainerFigureBlock({ explainer }: { explainer: ExplainerContentOut }) {
  return (
    <div
      style={{
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        background: "var(--pr-bg-card)",
        overflow: "hidden",
      }}
    >
      <img
        src={explainer.image_url}
        alt={explainer.caption}
        style={{
          display: "block",
          margin: "14px 16px 0",
          width: "calc(100% - 32px)",
          height: "auto",
          borderRadius: 6,
          border: "1px solid var(--pr-border-thumb)",
        }}
      />
      <div style={{ padding: "10px 16px 12px", display: "flex", flexDirection: "column", gap: 5 }}>
        <div
          style={{
            fontFamily: "var(--pr-jp, 'Noto Serif JP'), serif",
            fontSize: 12,
            lineHeight: 1.75,
            color: "var(--pr-text-body)",
          }}
        >
          {explainer.caption}
        </div>
        <AIBadge variant="generated" />
      </div>
    </div>
  );
}
