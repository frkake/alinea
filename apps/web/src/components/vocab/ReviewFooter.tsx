"use client";

import type { VocabSrs } from "@alinea/api-client";
import { formatNextReviewDisplay } from "@/components/vocab/format";
import type { ReviewResult } from "@/components/vocab/types";

export interface ReviewFooterProps {
  srs: VocabSrs;
  /** 評価直後は `POST …/review` の `next_review_display` をそのまま表示する(4d §4.4)。 */
  nextReviewDisplayOverride?: string;
  onReview: (result: ReviewResult) => void;
  pending?: boolean;
}

/**
 * 詳細パネルフッタ(4d §4.2.6)。「次の復習: 明日(2 回目)」+「まだあやしい」/「✓ 覚えた」。
 * 習得済み(`srs.next_review_at === null`)は文言+「復習に戻す」1 ボタンのみ(4d §5.6)。
 */
export function ReviewFooter({ srs, nextReviewDisplayOverride, onReview, pending = false }: ReviewFooterProps) {
  const mastered = srs.next_review_at === null;
  const displayText = nextReviewDisplayOverride ?? formatNextReviewDisplay(srs);

  return (
    <div
      style={{
        padding: "11px 16px",
        borderTop: "1px solid var(--pr-border-hair)",
        display: "flex",
        alignItems: "center",
        gap: 8,
        flex: "none",
      }}
    >
      <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{displayText}</span>
      {mastered ? (
        <button
          type="button"
          disabled={pending}
          onClick={() => onReview("again")}
          style={{ ...secondaryButtonStyle, marginLeft: "auto", opacity: pending ? 0.5 : 1 }}
        >
          復習に戻す
        </button>
      ) : (
        <>
          <button
            type="button"
            disabled={pending}
            onClick={() => onReview("again")}
            style={{ ...secondaryButtonStyle, marginLeft: "auto", opacity: pending ? 0.5 : 1 }}
          >
            まだあやしい
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => onReview("good")}
            style={{ ...primaryButtonStyle, opacity: pending ? 0.5 : 1 }}
          >
            ✓ 覚えた
          </button>
        </>
      )}
    </div>
  );
}

const secondaryButtonStyle = {
  height: 26,
  padding: "0 12px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  fontSize: 11,
  color: "var(--pr-text-sub)",
  background: "#FFFFFF",
  cursor: "pointer",
  fontFamily: "inherit",
} as const;

const primaryButtonStyle = {
  height: 26,
  padding: "0 12px",
  border: "none",
  borderRadius: 6,
  fontSize: 11,
  fontWeight: 600,
  color: "#FFFFFF",
  background: "var(--pr-acc)",
  cursor: "pointer",
  fontFamily: "inherit",
} as const;
