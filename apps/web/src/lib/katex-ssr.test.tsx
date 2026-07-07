import { render } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { renderInlineMath } from "@/lib/katex-ssr";

describe("renderInlineMath", () => {
  test("`$` を含まない文字列はそのまま返す(コスト0)", () => {
    const result = renderInlineMath("敵対的損失とスコア蒸留を組み合わせ。");
    expect(result).toBe("敵対的損失とスコア蒸留を組み合わせ。");
  });

  test("`$…$` を含む文字列は KaTeX HTML を挟んでレンダリングされる", () => {
    const { container } = render(<div>{renderInlineMath("損失 $L$ を最小化する。")}</div>);
    expect(container.textContent).toContain("損失");
    expect(container.textContent).toContain("を最小化する。");
    expect(container.querySelector(".katex")).not.toBeNull();
  });

  test("複数の数式区間を扱える", () => {
    const { container } = render(<div>{renderInlineMath("$a$ と $b$ の関係")}</div>);
    expect(container.querySelectorAll(".katex").length).toBe(2);
    expect(container.textContent).toContain("と");
    expect(container.textContent).toContain("の関係");
  });
});
