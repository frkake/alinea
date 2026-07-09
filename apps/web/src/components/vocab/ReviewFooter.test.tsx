import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { VocabSrs } from "@alinea/api-client";
import { ReviewFooter } from "@/components/vocab/ReviewFooter";

function srs(overrides: Partial<VocabSrs> = {}): VocabSrs {
  return { stage: 1, next_review_at: null, review_count: 0, history: [], ...overrides };
}

// VT-VOC-03: ReviewFooter — 「次の復習: 明日(2 回目)」整形・2 ボタン(まだあやしい/✓ 覚えた)。
describe("ReviewFooter (VT-VOC-03)", () => {
  test('formats "次の復習: 明日(N 回目)" from srs.next_review_at + review_count', () => {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    render(
      <ReviewFooter srs={srs({ next_review_at: tomorrow.toISOString(), review_count: 1 })} onReview={vi.fn()} />,
    );
    expect(screen.getByText("次の復習: 明日(2 回目)")).toBeInTheDocument();
    expect(screen.getByText("まだあやしい")).toBeInTheDocument();
    expect(screen.getByText("✓ 覚えた")).toBeInTheDocument();
  });

  test("prefers the server-provided next_review_display override after a review", () => {
    render(
      <ReviewFooter
        srs={srs({ next_review_at: new Date().toISOString(), review_count: 3 })}
        nextReviewDisplayOverride="次の復習: 7日後(4 回目)"
        onReview={vi.fn()}
      />,
    );
    expect(screen.getByText("次の復習: 7日後(4 回目)")).toBeInTheDocument();
  });

  test("clicking まだあやしい/✓ 覚えた calls onReview with again/good", () => {
    const onReview = vi.fn();
    render(<ReviewFooter srs={srs({ next_review_at: new Date().toISOString() })} onReview={onReview} />);
    fireEvent.click(screen.getByText("まだあやしい"));
    expect(onReview).toHaveBeenCalledWith("again");
    fireEvent.click(screen.getByText("✓ 覚えた"));
    expect(onReview).toHaveBeenCalledWith("good");
  });

  test("disables both buttons while pending", () => {
    render(<ReviewFooter srs={srs({ next_review_at: new Date().toISOString() })} onReview={vi.fn()} pending />);
    expect(screen.getByText("まだあやしい").closest("button")).toBeDisabled();
    expect(screen.getByText("✓ 覚えた").closest("button")).toBeDisabled();
  });

  test("mastered entries (next_review_at=null) show 習得済み + a single 復習に戻す button", () => {
    const onReview = vi.fn();
    render(<ReviewFooter srs={srs({ next_review_at: null, review_count: 5 })} onReview={onReview} />);
    expect(screen.getByText("習得済み — 復習キューから外れています")).toBeInTheDocument();
    expect(screen.queryByText("✓ 覚えた")).toBeNull();
    fireEvent.click(screen.getByText("復習に戻す"));
    expect(onReview).toHaveBeenCalledWith("again");
  });
});
