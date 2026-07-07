import { describe, expect, test } from "vitest";
import {
  formatAddedRelative,
  formatDetailMetaLine,
  formatNextReviewDate,
  formatNextReviewDisplay,
  parseInterpretation,
} from "@/components/vocab/format";

const NOW = new Date("2026-07-08T12:00:00");

describe("formatAddedRelative (4d §5.6)", () => {
  test.each([
    ["2026-07-08T09:00:00", "今日"],
    ["2026-07-07T09:00:00", "昨日"],
    ["2026-07-05T09:00:00", "3日前"],
    ["2026-07-01T09:00:00", "7/1"],
    ["2025-12-12T09:00:00", "2025/12/12"],
  ])("%s -> %s", (iso, expected) => {
    expect(formatAddedRelative(iso, NOW)).toBe(expected);
  });
});

describe("formatNextReviewDate (4d §5.6)", () => {
  test.each([
    ["2026-07-08T09:00:00", "今日"],
    ["2026-07-09T09:00:00", "明日"],
    ["2026-07-11T09:00:00", "3日後"],
    ["2026-07-15T09:00:00", "7/15"],
    ["2027-01-02T09:00:00", "2027/1/2"],
  ])("%s -> %s", (iso, expected) => {
    expect(formatNextReviewDate(iso, NOW)).toBe(expected);
  });
});

describe("formatNextReviewDisplay", () => {
  test("normal entries show 次の復習: {date}({review_count+1} 回目)", () => {
    expect(
      formatNextReviewDisplay({ stage: 2, next_review_at: "2026-07-09T00:00:00", review_count: 1, history: [] }, NOW),
    ).toBe("次の復習: 明日(2 回目)");
  });

  test("mastered entries (next_review_at=null) show the mastered copy", () => {
    expect(formatNextReviewDisplay({ stage: 5, next_review_at: null, review_count: 6, history: [] }, NOW)).toBe(
      "習得済み — 復習キューから外れています",
    );
  });
});

describe("parseInterpretation (4d §4.2.6-3)", () => {
  test("splits a parenthesized first line into a heading suffix + body", () => {
    const raw = "(句動詞の読み方)\nboil(煮る)+ down(量が減る方向)+ to(到達点)。";
    expect(parseInterpretation(raw)).toEqual({
      headingSuffix: "(句動詞の読み方)",
      body: "boil(煮る)+ down(量が減る方向)+ to(到達点)。",
    });
  });

  test("falls back to no suffix when the first line is not a full parenthesized clause", () => {
    const raw = "boil+down+to は句動詞。";
    expect(parseInterpretation(raw)).toEqual({ headingSuffix: null, body: raw });
  });
});

describe("formatDetailMetaLine (4d §4.2.6)", () => {
  test("collapses the source display's middle-dot separator into a single space", () => {
    expect(formatDetailMetaLine("句動詞", "イディオム", "Rectified Flow · §2.1")).toBe(
      "句動詞 · Rectified Flow §2.1 で追加 · ",
    );
  });

  test("falls back to the kind label while pos_label is still null (pending generation)", () => {
    expect(formatDetailMetaLine(null, "イディオム", "Rectified Flow · §2.1")).toBe(
      "イディオム · Rectified Flow §2.1 で追加 · ",
    );
  });
});
