import { expect, test, vi } from "vitest";

// VT-XTU-02: タブ内 PDF 判定・書誌ローカル推定(plans/10 §11.1)。
import { guessPdfTitle, hasPdfMagic, MAX_PDF_BYTES, validatePdfBlob } from "./pdf-detect";

test("uses the tab title as-is when it is not a bare filename", () => {
  expect(guessPdfTitle({ title: "Rectified Flow: A Simple Approach", url: "https://example.org/x.pdf" })).toBe(
    "Rectified Flow: A Simple Approach",
  );
});

test("falls back to the URL filename when title is empty", () => {
  const title = guessPdfTitle({
    title: "",
    url: "https://cs.example.edu/papers/attention_is-all_you-need.pdf",
  });
  expect(title).toBe("attention is all you need");
});

test("falls back to the URL filename when title is itself a bare *.pdf name", () => {
  const title = guessPdfTitle({ title: "paper.pdf", url: "https://host/dir/2209.03003.pdf?x=1" });
  expect(title).toBe("2209.03003");
});

test("decodes percent-encoded filenames", () => {
  const title = guessPdfTitle({ title: null, url: "https://host/dir/%E8%AB%96%E6%96%87.pdf" });
  expect(title).toBe(decodeURIComponent("%E8%AB%96%E6%96%87"));
});

test("returns null when no filename can be extracted (no automatic guess)", () => {
  expect(guessPdfTitle({ title: "", url: "https://host/dir/" })).toBeNull();
  expect(guessPdfTitle({ title: null, url: null })).toBeNull();
});

// arXiv URL 判別: arXiv の直リンク PDF でもファイル名抽出ロジックは変わらない
// (kind==="arxiv" か "pdf" かの判定自体はサーバー/lib/arxiv.ts が担当し、本モジュールは関与しない)。
test("works the same way for arxiv.org pdf URLs (kind classification is not this module's job)", () => {
  expect(guessPdfTitle({ title: "", url: "https://arxiv.org/pdf/2209.03003v3.pdf" })).toBe("2209.03003v3");
});

test("guessPdfTitle performs no network I/O (pure detection only, never auto-sends)", () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  guessPdfTitle({ title: "", url: "https://host/dir/paper.pdf" });
  expect(fetchSpy).not.toHaveBeenCalled();
  fetchSpy.mockRestore();
});

test("hasPdfMagic checks the %PDF- signature", () => {
  const enc = new TextEncoder();
  expect(hasPdfMagic(enc.encode("%PDF-1.7 rest"))).toBe(true);
  expect(hasPdfMagic(enc.encode("<html>"))).toBe(false);
  expect(hasPdfMagic(enc.encode("%PD"))).toBe(false);
});

test("validatePdfBlob rejects blobs over 50MB without reading the body", async () => {
  const bigBlob = { size: MAX_PDF_BYTES + 1, slice: vi.fn() } as unknown as Blob;
  const result = await validatePdfBlob(bigBlob);
  expect(result).toEqual({ ok: false, message: "50MB を超える PDF は送信できません" });
  expect((bigBlob as unknown as { slice: ReturnType<typeof vi.fn> }).slice).not.toHaveBeenCalled();
});

test("validatePdfBlob rejects non-PDF content", async () => {
  const blob = new Blob(["<html>not a pdf</html>"], { type: "text/html" });
  const result = await validatePdfBlob(blob);
  expect(result).toEqual({ ok: false, message: "PDF として読み取れませんでした" });
});

test("validatePdfBlob accepts a real PDF-looking blob", async () => {
  const blob = new Blob(["%PDF-1.4\n...body..."], { type: "application/pdf" });
  const result = await validatePdfBlob(blob);
  expect(result).toEqual({ ok: true });
});
