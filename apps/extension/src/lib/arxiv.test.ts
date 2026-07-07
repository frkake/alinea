import { expect, test } from "vitest";

import { detectArxiv } from "./arxiv";

// VT-XTU-01(計画 Task 31 Step 1)。
test("detects arxiv abs url", () => {
  expect(detectArxiv("https://arxiv.org/abs/2209.03003")?.id).toBe("2209.03003");
  expect(detectArxiv("https://example.com")).toBeNull();
});

test("extracts version from abs url", () => {
  const ref = detectArxiv("https://arxiv.org/abs/2209.03003v3");
  expect(ref).toEqual({ id: "2209.03003", version: "3" });
});

test("detects pdf url and strips .pdf", () => {
  expect(detectArxiv("https://arxiv.org/pdf/2209.03003.pdf")?.id).toBe("2209.03003");
  expect(detectArxiv("https://arxiv.org/pdf/2209.03003v2")).toEqual({
    id: "2209.03003",
    version: "2",
  });
});

test("handles old-style ids and www host", () => {
  expect(detectArxiv("https://www.arxiv.org/abs/hep-th/9901001")?.id).toBe("hep-th/9901001");
});

test("rejects non-arxiv and malformed urls", () => {
  expect(detectArxiv("https://arxiv.org/list/cs.LG/recent")).toBeNull();
  expect(detectArxiv("not a url")).toBeNull();
  expect(detectArxiv("https://arxiv.org.evil.com/abs/2209.03003")).toBeNull();
});
