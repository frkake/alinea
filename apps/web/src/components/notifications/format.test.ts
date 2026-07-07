import { describe, expect, test } from "vitest";
import { formatRelativeNotificationTime, truncateNotificationTitle } from "@/components/notifications/format";

describe("formatRelativeNotificationTime (4a §4.3)", () => {
  const now = new Date(2026, 6, 7, 8, 0); // 2026/7/7 8:00 ローカル

  test("same day renders 今日 H:mm", () => {
    expect(formatRelativeNotificationTime(new Date(2026, 6, 7, 8, 0).toISOString(), now)).toBe(
      "今日 8:00",
    );
  });

  test("previous day renders 昨日 H:mm with zero-padded minute", () => {
    expect(formatRelativeNotificationTime(new Date(2026, 6, 6, 19, 40).toISOString(), now)).toBe(
      "昨日 19:40",
    );
  });

  test("same year, older date renders M/D H:mm", () => {
    expect(formatRelativeNotificationTime(new Date(2026, 5, 20, 9, 5).toISOString(), now)).toBe(
      "6/20 9:05",
    );
  });

  test("year boundary renders YYYY/M/D H:mm", () => {
    expect(formatRelativeNotificationTime(new Date(2025, 11, 3, 0, 0).toISOString(), now)).toBe(
      "2025/12/3 0:00",
    );
  });
});

describe("truncateNotificationTitle (4a §4.3)", () => {
  test("keeps short titles untouched", () => {
    expect(truncateNotificationTitle("Rectified Flow")).toBe("Rectified Flow");
  });

  test("truncates titles over 48 chars to 46 chars + ellipsis", () => {
    const long = "Stochastic Interpolants: A Unifying Framework for Flows and Diffusions";
    const result = truncateNotificationTitle(long);
    expect(result).toBe(`${long.slice(0, 46)}…`);
    expect(result.length).toBe(47);
  });
});
