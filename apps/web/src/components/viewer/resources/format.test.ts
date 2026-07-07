import { describe, expect, test } from "vitest";
import {
  articleIconLabel,
  formatDuration,
  formatStars,
  formatUpdatedMonth,
  metaLine,
  parseNoteSegments,
  stripScheme,
} from "./format";
import type { ResourceLink } from "./types";

function resource(overrides: Partial<ResourceLink> = {}): ResourceLink {
  return {
    id: "res_1",
    kind: "github",
    url: "https://github.com/gnobitab/RectifiedFlow",
    official: false,
    title: "gnobitab/RectifiedFlow",
    source_label: "GitHub",
    thumbnail_url: null,
    meta: {},
    meta_fetched: true,
    note: null,
    created_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

describe("formatStars (plans/09-screens/5a §4.8)", () => {
  test("under 1000 stays an integer", () => {
    expect(formatStars(999)).toBe("999");
  });
  test("1200 -> 1.2k", () => {
    expect(formatStars(1200)).toBe("1.2k");
  });
  test("15000 -> 15k (trailing .0 dropped)", () => {
    expect(formatStars(15000)).toBe("15k");
  });
  test("1500000 -> 1500k (no M suffix)", () => {
    expect(formatStars(1_500_000)).toBe("1500k");
  });
  test("null is omitted", () => {
    expect(formatStars(null)).toBeNull();
  });
});

describe("formatDuration", () => {
  test("754 -> 12:34", () => {
    expect(formatDuration(754)).toBe("12:34");
  });
  test("65 -> 1:05", () => {
    expect(formatDuration(65)).toBe("1:05");
  });
  test(">= 3600 -> H:MM:SS", () => {
    expect(formatDuration(3725)).toBe("1:02:05");
  });
  test("null is omitted", () => {
    expect(formatDuration(null)).toBeNull();
  });
});

describe("formatUpdatedMonth", () => {
  test("ISO date -> YYYY-MM", () => {
    expect(formatUpdatedMonth("2023-11-15T00:00:00Z")).toBe("2023-11");
  });
  test("null is omitted", () => {
    expect(formatUpdatedMonth(null)).toBeNull();
  });
});

describe("articleIconLabel", () => {
  test("first char uppercased", () => {
    expect(articleIconLabel("zenn.dev")).toBe("Z");
  });
  test("empty string falls back to W", () => {
    expect(articleIconLabel("")).toBe("W");
  });
});

describe("metaLine (§4.8 の整形規則)", () => {
  test("github: language/stars/updated joined with 中黒", () => {
    const r = resource({
      kind: "github",
      meta: { language: "Python", stars: 1200, updated_at: "2023-11-15" },
    });
    expect(metaLine(r)).toBe("GitHub · Python · ★ 1.2k · 更新 2023-11");
  });

  test("github: null segments are omitted", () => {
    const r = resource({ kind: "github", meta: { language: null, stars: null, updated_at: null } });
    expect(metaLine(r)).toBe("GitHub");
  });

  test("youtube: duration formatted, no 著者発表 attribute", () => {
    const r = resource({
      kind: "youtube",
      source_label: "YouTube",
      meta: { duration_seconds: 754 },
    });
    expect(metaLine(r)).toBe("YouTube · 12:34");
  });

  test("slides: source_label · PDF · N 枚", () => {
    const r = resource({ kind: "slides", source_label: "iclr.cc", meta: { format: "pdf", pages: 24 } });
    expect(metaLine(r)).toBe("iclr.cc · PDF · 24 枚");
  });

  test("article: source_label · 解説記事 · N min", () => {
    const r = resource({
      kind: "article",
      source_label: "zenn.dev",
      meta: { reading_minutes: 15 },
    });
    expect(metaLine(r)).toBe("zenn.dev · 解説記事 · 15 min");
  });

  test("meta_fetched=false shows the 取得不可 fallback regardless of kind", () => {
    const r = resource({ kind: "article", source_label: "example.com", meta_fetched: false, meta: {} });
    expect(metaLine(r)).toBe("example.com · タイトル・メタ取得不可");
  });
});

describe("stripScheme", () => {
  test("removes https:// only", () => {
    expect(stripScheme("https://github.com/gnobitab/RectifiedFlow")).toBe(
      "github.com/gnobitab/RectifiedFlow",
    );
  });
});

describe("parseNoteSegments", () => {
  test("splits plain text and chip tokens", () => {
    const segs = parseNoteSegments("train_reflow.py が [[sec:sec-3|§2.2]] の手順に対応。");
    expect(segs).toEqual([
      { type: "text", text: "train_reflow.py が " },
      { type: "chip", text: "§2.2", sectionId: "sec-3" },
      { type: "text", text: " の手順に対応。" },
    ]);
  });

  test("plain text with no chip returns a single text segment", () => {
    expect(parseNoteSegments("plain memo")).toEqual([{ type: "text", text: "plain memo" }]);
  });
});
