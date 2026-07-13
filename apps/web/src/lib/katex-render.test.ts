import { describe, expect, test } from "vitest";
import { createKatexMacros, renderBlockMath, renderInlineMath } from "@/lib/katex-render";

describe("katex-render recovery", () => {
  test("creates isolated macro maps with the shared student macro", () => {
    const first = createKatexMacros();
    const second = createKatexMacros();
    first["\\student"] = "mutated";

    expect(first).not.toBe(second);
    expect(second["\\student"]).toBe("\\operatorname{student}");
  });

  test("wraps align rows before rendering block math", () => {
    const html = renderBlockMath("\\nabla f(x) &= x + 1");

    expect(html).toContain("katex");
    expect(html).not.toContain("alinea-math-fallback");
    expect(html).not.toContain("katex-error");
  });

  test("wraps an outer alignment row that contains a nested matrix", () => {
    const html = renderBlockMath(String.raw`V &= \begin{pmatrix}0 & I \\ 0 & 0\end{pmatrix}`);

    expect(html).toContain("katex");
    expect(html).not.toContain("alinea-math-fallback");
    expect(html).not.toContain("katex-error");
  });

  test("recovers an undefined textual macro without a paper-specific mapping", () => {
    const html = renderInlineMath("\\student(\\cdot\\mid x)");

    expect(html).toContain("katex");
    expect(html).toContain("student");
    expect(html).not.toContain("alinea-math-fallback");
  });

  test("recovers unknown textual macros as operators", () => {
    const html = renderInlineMath("\\mymodel(y)");

    expect(html).toContain("katex");
    expect(html).toContain("mymodel");
    expect(html).not.toContain("alinea-math-fallback");
  });

  test("maps mathbbm to a supported blackboard-bold command", () => {
    const html = renderInlineMath("\\mathbbm{1}[x]");

    expect(html).toContain("katex");
    expect(html).not.toContain("alinea-math-fallback");
  });

  test("does not expose raw LaTeX when an irrecoverable formula falls back", () => {
    const html = renderInlineMath(String.raw`\begin{broken}`);

    expect(html).toContain("alinea-math-fallback");
    expect(html).toContain("数式を表示できません");
    expect(html).not.toContain(String.raw`\begin`);
  });
});
