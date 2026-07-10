import type { EvidenceItemOut } from "@alinea/api-client";
import { renderArticleMarkdown } from "@/components/viewer/article/markdown";
import { ArticleEvidenceChips } from "@/components/viewer/article/ArticleEvidenceChips";
import type { AnchorRef } from "@/components/viewer/article/types";

/** 段落ブロック(1h §4.7)。Noto Serif JP 14.5px/lh2.0。末尾に根拠チップ(docs/07 §2.4)。 */
export function ParagraphBlock({
  markdown,
  includeMath,
  evidence,
  onJumpToAnchor,
}: {
  markdown: string;
  includeMath: boolean;
  evidence: EvidenceItemOut[];
  onJumpToAnchor: (anchor: AnchorRef) => void;
}) {
  return (
    <div
      style={{
        fontFamily: "var(--pr-jp, 'Noto Serif JP'), serif",
        fontSize: 14.5,
        lineHeight: 2,
        color: "var(--pr-text-body)",
      }}
    >
      {renderArticleMarkdown(markdown, includeMath)}
      {evidence.length > 0 ? (
        <div
          data-testid="article-evidence-chips"
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "flex-start",
            gap: 4,
            minWidth: 0,
            marginTop: 8,
          }}
        >
          <ArticleEvidenceChips evidence={evidence} onJumpToAnchor={onJumpToAnchor} size="inline" />
        </div>
      ) : null}
    </div>
  );
}
