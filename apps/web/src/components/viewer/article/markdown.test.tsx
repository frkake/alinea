import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { renderArticleMarkdown } from "@/components/viewer/article/markdown";

describe("renderArticleMarkdown (1h §4.7)", () => {
  test("renders bold, italic, inline code, and links", () => {
    render(
      <div>
        {renderArticleMarkdown(
          "**太字** と *斜体* と `code` と [参照](https://example.com/x)",
          false,
        )}
      </div>,
    );
    expect(screen.getByText("太字")).toHaveProperty("tagName", "B");
    expect(screen.getByText("斜体")).toHaveProperty("tagName", "I");
    expect(screen.getByText("code")).toHaveProperty("tagName", "CODE");
    const link = screen.getByText("参照");
    expect(link).toHaveProperty("tagName", "A");
    expect(link).toHaveAttribute("href", "https://example.com/x");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  test("renders raw GitHub URLs and Hugging Face links as provider chips", () => {
    render(
      <div>
        {renderArticleMarkdown(
          "実装 https://github.com/gnobitab/RectifiedFlow と [HF](https://huggingface.co/stabilityai/stable-diffusion-3-medium)",
          false,
        )}
      </div>,
    );
    const github = screen.getByRole("link", { name: "GitHub gnobitab/RectifiedFlow" });
    expect(github).toHaveAttribute("href", "https://github.com/gnobitab/RectifiedFlow");
    expect(github).toHaveTextContent("GH");
    expect(github).toHaveTextContent("gnobitab/RectifiedFlow");

    const hf = screen.getByRole("link", {
      name: "Hugging Face stabilityai/stable-diffusion-3-medium",
    });
    expect(hf).toHaveAttribute(
      "href",
      "https://huggingface.co/stabilityai/stable-diffusion-3-medium",
    );
    expect(hf).toHaveTextContent("HF");
  });

  test("renders a simple bullet list as <ul><li>", () => {
    const { container } = render(<div>{renderArticleMarkdown("- 一つ目\n- 二つ目", false)}</div>);
    const items = container.querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("一つ目");
    expect(items[1]).toHaveTextContent("二つ目");
  });

  test("does not render math when includeMath is false", () => {
    const { container } = render(<div>{renderArticleMarkdown("速度 $v$ を学習する", false)}</div>);
    expect(container.textContent).toContain("$v$");
    expect(container.querySelector(".katex")).toBeNull();
  });

  test("renders KaTeX output when includeMath is true", () => {
    const { container } = render(<div>{renderArticleMarkdown("速度 $v$ を学習する", true)}</div>);
    expect(container.querySelector(".katex")).not.toBeNull();
  });
});
