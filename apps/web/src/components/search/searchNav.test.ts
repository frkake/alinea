import { describe, expect, test } from "vitest";
import {
  formatArticleDate,
  hrefForSearchTarget,
  jumpLabelForTarget,
  previewBadge,
  resultsBadges,
  snippetFontVar,
  type SearchHitTarget,
} from "@/components/search/searchNav";

describe("hrefForSearchTarget (plans/11 §7)", () => {
  test("viewer target with anchor scrolls to the block and passes hl", () => {
    const target: SearchHitTarget = {
      kind: "viewer",
      library_item_id: "li_1",
      anchor: { revision_id: "rev_1", block_id: "blk_5", side: "source", display: "¶2" },
    };
    expect(hrefForSearchTarget(target, "EMA teacher")).toBe(
      "/papers/li_1?block=blk_5&hl=EMA+teacher",
    );
  });

  test("viewer target without anchor (biblio hit) opens the paper head", () => {
    const target: SearchHitTarget = { kind: "viewer", library_item_id: "li_1", anchor: null };
    expect(hrefForSearchTarget(target, "q")).toBe("/papers/li_1");
  });

  test("note target opens the notes panel", () => {
    const target: SearchHitTarget = {
      kind: "note",
      library_item_id: "li_1",
      note_id: "note_1",
    };
    expect(hrefForSearchTarget(target, "q")).toBe(
      "/papers/li_1?panel=notes&note=note_1&hl=q",
    );
  });

  test("chat target opens the chat panel with thread and message", () => {
    const target: SearchHitTarget = {
      kind: "chat",
      library_item_id: "li_1",
      thread_id: "th_1",
      message_id: "msg_1",
    };
    expect(hrefForSearchTarget(target, "q")).toBe(
      "/papers/li_1?panel=chat&thread=th_1&message=msg_1&hl=q",
    );
  });

  test("article target opens article mode at the block", () => {
    const target: SearchHitTarget = {
      kind: "article",
      library_item_id: "li_1",
      article_block_id: "ablk_1",
    };
    expect(hrefForSearchTarget(target, "q")).toBe(
      "/papers/li_1?view=article&article_block=ablk_1&hl=q",
    );
  });
});

describe("jumpLabelForTarget", () => {
  test("maps each target kind to its verbatim label", () => {
    expect(jumpLabelForTarget("viewer")).toBe("該当位置へ →");
    expect(jumpLabelForTarget("note")).toBe("メモを開く →");
    expect(jumpLabelForTarget("chat")).toBe("スレッドを開く →");
    expect(jumpLabelForTarget("article")).toBe("記事モードで開く →");
  });
});

describe("resultsBadges (4e §4.5 / plans/11 §4)", () => {
  test("body source-only hit renders a single 原文 badge", () => {
    expect(resultsBadges({ source: "body", matched_in: ["source"] })).toEqual([
      { tone: "body", label: "本文 · 原文" },
    ]);
  });

  test("body translation-only hit renders a single 訳文 badge", () => {
    expect(resultsBadges({ source: "body", matched_in: ["translation"] })).toEqual([
      { tone: "body", label: "本文 · 訳文" },
    ]);
  });

  test("body combined hit renders two badges side by side", () => {
    expect(resultsBadges({ source: "body", matched_in: ["source", "translation"] })).toEqual([
      { tone: "body", label: "本文 · 原文" },
      { tone: "body", label: "本文 · 訳文" },
    ]);
  });

  test("note and annotation both fold into the メモ badge", () => {
    expect(resultsBadges({ source: "note", matched_in: null })).toEqual([
      { tone: "note", label: "メモ" },
    ]);
    expect(resultsBadges({ source: "annotation", matched_in: null })).toEqual([
      { tone: "note", label: "メモ" },
    ]);
  });

  test("chat and article render their own badge", () => {
    expect(resultsBadges({ source: "chat", matched_in: null })).toEqual([
      { tone: "chat", label: "チャット" },
    ]);
    expect(resultsBadges({ source: "article", matched_in: null })).toEqual([
      { tone: "article", label: "記事" },
    ]);
  });
});

describe("previewBadge (1e §4.3 / plans/11 §4)", () => {
  test("maps each source to the dropdown label", () => {
    expect(previewBadge("body")).toEqual({ tone: "body", label: "本文でヒット" });
    expect(previewBadge("note")).toEqual({ tone: "note", label: "あなたのメモ" });
    expect(previewBadge("annotation")).toEqual({ tone: "note", label: "あなたのメモ" });
    expect(previewBadge("chat")).toEqual({ tone: "chat", label: "チャット履歴" });
    expect(previewBadge("article")).toEqual({ tone: "article", label: "記事でヒット" });
  });
});

describe("snippetFontVar", () => {
  test("body english hit uses the serif english font", () => {
    expect(snippetFontVar({ source: "body", snippet_lang: "en" })).toBe("var(--pr-font-en)");
  });
  test("body japanese hit uses the JP serif font", () => {
    expect(snippetFontVar({ source: "body", snippet_lang: "ja" })).toBe("var(--pr-jp)");
  });
  test("non-body hits use the UI font regardless of snippet_lang", () => {
    expect(snippetFontVar({ source: "chat", snippet_lang: "ja" })).toBe("var(--pr-font-ui)");
  });
});

describe("formatArticleDate (4e §4.5 決定: 月ゼロ埋めなし・日2桁ゼロ埋め)", () => {
  test("zero-pads the day but not the month", () => {
    expect(formatArticleDate(new Date(2026, 6, 6).toISOString())).toBe("7/06");
    expect(formatArticleDate(new Date(2026, 11, 3).toISOString())).toBe("12/03");
  });
});
