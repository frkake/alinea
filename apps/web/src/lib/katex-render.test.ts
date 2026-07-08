import { describe, expect, test } from "vitest";
import { renderBlockMath, renderInlineMath } from "@/lib/katex-render";

describe("katex-render recovery", () => {
  test("wraps align rows before rendering block math", () => {
    const html = renderBlockMath("\\nabla f(x) &= x + 1");

    expect(html).toContain("katex");
    expect(html).not.toContain("yk-math-fallback");
    expect(html).not.toContain("katex-error");
  });

  test("renders known paper-specific macros", () => {
    const html = renderInlineMath("\\student(\\cdot\\mid x)");

    expect(html).toContain("katex");
    expect(html).toContain("student");
    expect(html).not.toContain("yk-math-fallback");
  });

  test("recovers unknown textual macros as operators", () => {
    const html = renderInlineMath("\\mymodel(y)");

    expect(html).toContain("katex");
    expect(html).toContain("mymodel");
    expect(html).not.toContain("yk-math-fallback");
  });

  test("maps mathbbm to a supported blackboard-bold command", () => {
    const html = renderInlineMath("\\mathbbm{1}[x]");

    expect(html).toContain("katex");
    expect(html).not.toContain("yk-math-fallback");
  });
});
