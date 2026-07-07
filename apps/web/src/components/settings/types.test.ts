import { describe, expect, test } from "vitest";
import { accentKeyForHex, isSettingsCategory } from "@/components/settings/types";

describe("isSettingsCategory (4f §1: 省略時・不正値時は account へ正規化)", () => {
  test("accepts all 8 category ids", () => {
    for (const id of [
      "account",
      "display",
      "translation",
      "reading",
      "chat",
      "notifications",
      "export",
      "extension",
    ]) {
      expect(isSettingsCategory(id)).toBe(true);
    }
  });

  test("rejects unknown / undefined values", () => {
    expect(isSettingsCategory(undefined)).toBe(false);
    expect(isSettingsCategory("")).toBe(false);
    expect(isSettingsCategory("csv")).toBe(false);
    expect(isSettingsCategory("Account")).toBe(false);
  });
});

describe("accentKeyForHex (plans/08 §2.3 ACCENTS 対応)", () => {
  test("maps each of the 4 hex values to its data-accent key", () => {
    expect(accentKeyForHex("#3E5C76")).toBe("slate");
    expect(accentKeyForHex("#4A6B57")).toBe("green");
    expect(accentKeyForHex("#6E5A7E")).toBe("purple");
    expect(accentKeyForHex("#7A5C48")).toBe("terracotta");
  });

  test("falls back to slate for an unknown hex (defensive)", () => {
    expect(accentKeyForHex("#000000")).toBe("slate");
  });
});
