import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { AnchorRef, EvidenceRef } from "@alinea/api-client";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

function anchorRef(overrides: Partial<AnchorRef> = {}): AnchorRef {
  return {
    revision_id: "rev-chat-markdown",
    block_id: "block-evidence",
    start: 24,
    end: 52,
    quote: "Rectified flow learns a transport path.",
    side: "source",
    display: "§2.1",
    ...overrides,
  };
}

const evidence: EvidenceRef[] = [
  {
    ref: 1,
    display: "§2.1",
    anchor: anchorRef(),
  },
  {
    ref: 2,
    display: "式(5)",
    anchor: anchorRef({ block_id: "block-equation", start: null, end: null, display: "式(5)" }),
  },
];

describe("ChatMarkdown", () => {
  test("renders safe GFM blocks with scrollable tables and styled code blocks", () => {
    const { container } = render(
      <ChatMarkdown
        text={[
          "# GFM heading",
          "",
          "**strong** and *emphasis*",
          "",
          "1. ordered one",
          "2. ordered two",
          "",
          "- unordered one",
          "- unordered two",
          "",
          "> quoted text",
          "",
          "---",
          "",
          "| Name | Value |",
          "| --- | --- |",
          "| loss | 0.1 |",
          "",
          "```ts",
          "const answer = 42;",
          "```",
        ].join("\n")}
        evidence={[]}
      />,
    );

    expect(screen.getByRole("heading", { name: "GFM heading" })).toHaveProperty("tagName", "H1");
    expect(screen.getByText("strong")).toHaveProperty("tagName", "STRONG");
    expect(screen.getByText("emphasis")).toHaveProperty("tagName", "EM");
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelector("blockquote")).toHaveTextContent("quoted text");
    expect(container.querySelector("hr")).not.toBeNull();
    expect(container.querySelector(".alinea-chat-table-scroll")).toHaveAttribute("role", "region");
    expect(container.querySelector(".alinea-chat-table-scroll")).toHaveAttribute(
      "aria-label",
      "Markdown表",
    );
    expect(container.querySelector(".alinea-chat-table-scroll")).toHaveAttribute("tabindex", "0");
    expect(container.querySelector(".alinea-chat-table-scroll table")).not.toBeNull();
    expect(container.querySelector("pre.alinea-chat-code-block")).toHaveTextContent(
      "const answer = 42;",
    );
  });

  test("renders verified evidence markers in headings and cells and leaves code markers literal", () => {
    const onEvidenceJump = vi.fn();
    const { container } = render(
      <ChatMarkdown
        text={[
          "## Evidence heading [[ev:1]]",
          "",
          "| Claim | Evidence |",
          "| --- | --- |",
          "| Flow matching | [[ev:2]] |",
          "",
          "Unknown [[ev:99]] marker.",
          "",
          "Inline `[[ev:1]]` stays literal.",
          "",
          "```text",
          "[[ev:2]]",
          "```",
        ].join("\n")}
        evidence={evidence}
        onEvidenceJump={onEvidenceJump}
      />,
    );

    const headingChip = screen.getByRole("button", { name: "§2.1" });
    const tableChip = screen.getByRole("button", { name: "式(5)" });
    expect(headingChip.closest("h2")).not.toBeNull();
    expect(tableChip.closest("td")).not.toBeNull();

    fireEvent.click(headingChip);
    fireEvent.click(tableChip);
    expect(onEvidenceJump).toHaveBeenNthCalledWith(1, evidence[0]?.anchor);
    expect(onEvidenceJump).toHaveBeenNthCalledWith(2, evidence[1]?.anchor);
    expect(screen.queryByText("[[ev:99]]")).toBeNull();
    expect(screen.queryByRole("button", { name: "[[ev:1]]" })).toBeNull();
    expect(container.querySelector("code")).toHaveTextContent("[[ev:1]]");
    expect(container.querySelector("pre code")).toHaveTextContent("[[ev:2]]");
  });

  test("renders only safe links, drops raw scripts, and replaces remote images with alt text", () => {
    const { container } = render(
      <ChatMarkdown
        text={[
          "[safe](https://example.com/docs) and [unsafe](javascript:alert(1))",
          "",
          "before<script>alert('xss')</script>after",
          "",
          "![remote chart](https://example.com/chart.png)",
        ].join("\n")}
        evidence={[]}
      />,
    );

    expect(screen.getByRole("link", { name: "safe" })).toHaveAttribute(
      "href",
      "https://example.com/docs",
    );
    expect(screen.getByRole("link", { name: "safe" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "safe" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
    expect(screen.queryByRole("link", { name: "unsafe" })).toBeNull();
    expect(container).toHaveTextContent("unsafe");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("画像: remote chart")).toHaveClass("alinea-chat-image-alt");
  });

  test("replaces a Markdown image without alt text with the Japanese fallback prefix", () => {
    const { container } = render(
      <ChatMarkdown text="![](https://example.com/empty-alt.png)" evidence={[]} />,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".alinea-chat-image-alt")).toHaveTextContent("画像:");
  });

  test("renders inline and complete display math without using code blocks", () => {
    const { container } = render(
      <ChatMarkdown
        text={["Inline $v_t$.", "", "Same line $$x^2$$.", "", "$$", "\\frac{a}{b}", "$$"].join(
          "\n",
        )}
        evidence={[]}
      />,
    );

    expect(container.querySelectorAll(".katex")).toHaveLength(3);
    expect(container.querySelectorAll(".alinea-chat-math-block .katex-display")).toHaveLength(2);
    expect(container.querySelector("pre.alinea-chat-code-block .katex-display")).toBeNull();
    expect(container.querySelector("p .katex")?.closest(".alinea-chat-math-block")).toBeNull();
  });

  test("renders normalized display math in exactly one math block", () => {
    const { container } = render(<ChatMarkdown text="$$x$$" evidence={[]} />);

    expect(container.querySelectorAll(".alinea-chat-math-block")).toHaveLength(1);
    expect(container.querySelector(".alinea-chat-math-block .alinea-chat-math-block")).toBeNull();
    expect(container.querySelector(".alinea-chat-math-block .katex-display")).not.toBeNull();
  });

  test("keeps same-line display math inside blockquotes and list items", () => {
    const { container } = render(
      <ChatMarkdown
        text={["> before $$x^2$$ after", "", "- before $$y^2$$ after"].join("\n")}
        evidence={[]}
      />,
    );

    expect(container.querySelector("blockquote .alinea-chat-math-block .katex-display")).not.toBeNull();
    expect(container.querySelector("blockquote")).toHaveTextContent("before");
    expect(container.querySelector("blockquote")).toHaveTextContent("after");
    expect(container.querySelector("li .alinea-chat-math-block .katex-display")).not.toBeNull();
    expect(container.querySelector("li")).toHaveTextContent("before");
    expect(container.querySelector("li")).toHaveTextContent("after");
  });

  test("preserves link destinations and table cells that contain double-dollar pairs", () => {
    const { container } = render(
      <ChatMarkdown
        text={[
          "[safe](https://example.com/$$x$$)",
          "",
          "| Metric | Value |",
          "| --- | --- |",
          "| loss | $$x^2$$ |",
        ].join("\n")}
        evidence={[]}
      />,
    );

    expect(screen.getByRole("link", { name: "safe" })).toHaveAttribute(
      "href",
      "https://example.com/$$x$$",
    );
    expect(container.querySelector(".alinea-chat-table-scroll td")).toHaveTextContent("loss");
    expect(container.querySelector(".alinea-chat-table-scroll td:last-child")).not.toBeNull();
    expect(container.querySelector(".alinea-chat-math-block")).toBeNull();
  });

  test("preserves angle-bracket link destinations that contain double-dollar pairs", () => {
    render(<ChatMarkdown text="[x](<https://example.com/)$$x$$>)" evidence={[]} />);

    expect(screen.getByRole("link", { name: "x" })).toHaveAttribute(
      "href",
      "https://example.com/)$$x$$",
    );
  });

  test("preserves spaced angle-bracket link destinations that contain double-dollar pairs", () => {
    render(<ChatMarkdown text="[x]( <https://example.com/)$$x$$> )" evidence={[]} />);

    expect(screen.getByRole("link", { name: "x" })).toHaveAttribute(
      "href",
      "https://example.com/)$$x$$",
    );
  });

  test("preserves reference-style link destinations that contain double-dollar pairs", () => {
    render(
      <ChatMarkdown
        text={["[safe][id]", "", "[id]: https://example.com/$$x$$"].join("\n")}
        evidence={[]}
      />,
    );

    expect(screen.getByRole("link", { name: "safe" })).toHaveAttribute(
      "href",
      "https://example.com/$$x$$",
    );
  });

  test("preserves GFM literal autolinks that contain double-dollar pairs", () => {
    render(<ChatMarkdown text="https://example.com/$$x$$" evidence={[]} />);

    expect(screen.getByRole("link", { name: "https://example.com/$$x$$" })).toHaveAttribute(
      "href",
      "https://example.com/$$x$$",
    );
  });

  test("keeps double-dollar pairs in blockquoted GFM table cells", () => {
    const { container } = render(
      <ChatMarkdown
        text={["> | Metric | Value |", "> | --- | --- |", "> | loss | $$x^2$$ |"].join("\n")}
        evidence={[]}
      />,
    );

    expect(container.querySelector("blockquote .alinea-chat-table-scroll td")).toHaveTextContent(
      "loss",
    );
    expect(
      container.querySelector("blockquote .alinea-chat-table-scroll td:last-child"),
    ).not.toBeNull();
    expect(container.querySelector(".alinea-chat-math-block")).toBeNull();
  });

  test("keeps math-labelled fenced code as an ordinary code block", () => {
    const { container } = render(
      <ChatMarkdown text={["```math", "x^2", "```"].join("\n")} evidence={[]} />,
    );

    expect(container.querySelector("pre.alinea-chat-code-block")).toHaveTextContent("x^2");
    expect(container.querySelector(".alinea-chat-math-block")).toBeNull();
    expect(container.querySelector(".katex")).toBeNull();
  });

  test("keeps raw HTML with math-looking attributes inert", () => {
    const { container } = render(
      <ChatMarkdown text={'<a href="https://example.com/$$x$$">link</a>'} evidence={[]} />,
    );

    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector(".alinea-chat-math-block")).toBeNull();
    expect(container).toHaveTextContent("link");
    expect(container).not.toHaveTextContent("$$x$$");
  });

  test("uses the shared student macro in chat math", () => {
    const { container } = render(<ChatMarkdown text="$\\student(x)$" evidence={[]} />);

    expect(container.querySelector(".katex")).toHaveTextContent("student");
  });

  test("shows malformed math as readable KaTeX error content", () => {
    const { container } = render(<ChatMarkdown text="$\\notacommand{$" evidence={[]} />);

    expect(container.querySelector(".katex-error")).toHaveTextContent("\\notacommand{");
  });

  test("keeps unfinished math literal until its display delimiter arrives", () => {
    const { container, rerender } = render(
      <ChatMarkdown text="途中 $$\\frac{a}{b}" evidence={[]} />,
    );

    expect(container.querySelector(".katex")).toBeNull();
    expect(container).toHaveTextContent("途中 $$\\frac{a}{b}");

    rerender(<ChatMarkdown text="途中 $$\\frac{a}{b}$$" evidence={[]} />);

    expect(container.querySelector(".alinea-chat-math-block .katex-display")).not.toBeNull();
  });
});
