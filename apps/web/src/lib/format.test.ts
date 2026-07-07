import { describe, expect, test } from "vitest";
import { formatDateMd, formatDateYmd } from "@/lib/format";

describe("formatDateYmd", () => {
  test("ISO 8601 → 「YYYY-MM-DD」(JST)", () => {
    expect(formatDateYmd("2026-07-06T10:00:00Z")).toBe("2026-07-06");
  });

  test("UTC 深夜でも JST 側の日付になる(日付境界)", () => {
    // 2026-07-05T15:30:00Z = 2026-07-06T00:30:00+09:00
    expect(formatDateYmd("2026-07-05T15:30:00Z")).toBe("2026-07-06");
  });
});

describe("formatDateMd", () => {
  test("date-only ISO → 「M/D」(先頭ゼロなし)", () => {
    expect(formatDateMd("2026-07-16")).toBe("7/16");
  });

  test("1 桁の月日でも先頭ゼロが付かない", () => {
    expect(formatDateMd("2026-01-05")).toBe("1/5");
  });

  test("datetime 文字列も受け付ける", () => {
    expect(formatDateMd("2026-07-16T00:00:00Z")).toBe("7/16");
  });
});
