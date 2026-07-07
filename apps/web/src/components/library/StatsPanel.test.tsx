import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { StatsPanel, barHeightPct } from "@/components/library/StatsPanel";

// 1d §4.7: 統計棒グラフの高さ算出(基準 max(5h, 週最大)・最小4%・全0週)
describe("barHeightPct", () => {
  const weeklyHours = [1.5, 2.75, 2.0, 1.1, 3.2, 2.4, 0.6, 1.9, 3.5, 2.6, 2.2, 4.2];

  test("matches the VRT seed's demo heights (basis = 5h since week max < 5h)", () => {
    const heights = weeklyHours.map((h) => barHeightPct(h, weeklyHours));
    expect(heights).toEqual([30, 55, 40, 22, 64, 48, 12, 38, 70, 52, 44, 84]);
  });

  test("normalizes to the week max when it exceeds 5h", () => {
    const values = [10, 5];
    expect(barHeightPct(10, values)).toBe(100);
    expect(barHeightPct(5, values)).toBe(50);
  });

  test("returns 4% for an all-zero range", () => {
    const values = [0, 0, 0];
    expect(barHeightPct(0, values)).toBe(4);
  });

  test("clamps to a minimum of 4% for very small nonzero values", () => {
    expect(barHeightPct(0.05, [5])).toBe(4);
  });
});

describe("StatsPanel", () => {
  test("renders finished count and reading hours with a fixed decimal", () => {
    render(<StatsPanel finishedCount={3} readingHours={4.2} weeklyHours={Array(12).fill(0) as number[]} />);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("本 読了")).toBeInTheDocument();
    expect(screen.getByText("4.2")).toBeInTheDocument();
    expect(screen.getByText("時間")).toBeInTheDocument();
  });

  test("links '詳細 →' to the reading settings category", () => {
    render(<StatsPanel finishedCount={0} readingHours={0} weeklyHours={Array(12).fill(0) as number[]} />);
    expect(screen.getByText("詳細 →")).toHaveAttribute("href", "/settings?category=reading");
  });
});
