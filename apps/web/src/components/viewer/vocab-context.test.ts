import { describe, expect, test } from "vitest";
import { extractVocabContext } from "@/components/viewer/vocab-context";

describe("extractVocabContext", () => {
  test("selects the sentence containing the highlighted range", () => {
    const text =
      "Rectified flow learns a straight transport map. It uses an EMA teacher for distillation. Results improve steadily.";
    const start = text.indexOf("EMA teacher");
    const end = start + "EMA teacher".length;
    const result = extractVocabContext(text, start, end);
    expect(result.contextSentence).toBe("It uses an EMA teacher for distillation.");
    expect(
      result.contextSentence.slice(result.highlightStart, result.highlightEnd),
    ).toBe("EMA teacher");
  });

  test("falls back to a window around the selection when there is no sentence punctuation", () => {
    const text = "a".repeat(500) + "TARGET" + "b".repeat(500);
    const start = text.indexOf("TARGET");
    const end = start + "TARGET".length;
    const result = extractVocabContext(text, start, end);
    expect(result.contextSentence).toContain("TARGET");
    expect(
      result.contextSentence.slice(result.highlightStart, result.highlightEnd),
    ).toBe("TARGET");
  });

  test("clamps out-of-range offsets", () => {
    const text = "Short sentence.";
    const result = extractVocabContext(text, -5, 9999);
    expect(result.contextSentence).toBe("Short sentence.");
    expect(result.highlightStart).toBe(0);
    expect(result.highlightEnd).toBe(result.contextSentence.length);
  });
});
