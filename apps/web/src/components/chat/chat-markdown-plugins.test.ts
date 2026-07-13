import type { Root } from "mdast";
import { unified } from "unified";
import { describe, expect, test } from "vitest";
import {
  EVIDENCE_PROPERTY,
  normalizeDisplayMath,
  remarkEvidence,
  replaceEvidenceMarkers,
} from "@/components/chat/chat-markdown-plugins";

function rootWithParagraph(value: string): Root {
  return {
    type: "root",
    children: [
      {
        type: "paragraph",
        children: [{ type: "text", value }],
      },
    ],
  };
}

describe("normalizeDisplayMath", () => {
  test("turns a complete same-line expression into a trimmed display block", () => {
    expect(normalizeDisplayMath("前 $$ x^2 $$ 後")).toBe("前 \n\n$$\nx^2\n$$\n\n 後");
  });

  test("leaves an existing multiline display block in order with its prose", () => {
    const markdown = ["前の文章", "$$", "x^2", "$$", "後の文章"].join("\n");

    expect(normalizeDisplayMath(markdown)).toBe(markdown);
  });

  test("does not rewrite dollar pairs inside inline code spans", () => {
    expect(normalizeDisplayMath("`$$x$$` と ``$$y$$``")).toBe("`$$x$$` と ``$$y$$``");
  });

  test("does not rewrite dollar pairs inside backtick and tilde fenced code", () => {
    const markdown = ["```text", "$$backtick$$", "```", "~~~text", "$$tilde$$", "~~~~"].join("\n");

    expect(normalizeDisplayMath(markdown)).toBe(markdown);
  });

  test("requires a fence closing delimiter with the same character and sufficient length", () => {
    const markdown = ["````text", "$$still code$$", "```"].join("\n");

    expect(normalizeDisplayMath(markdown)).toBe(markdown);
  });

  test("does not rewrite dollar pairs inside a blockquoted tilde fence", () => {
    const markdown = ["> ~~~text", "> $$notMath$$", "> ~~~"].join("\n");

    expect(normalizeDisplayMath(markdown)).toBe(markdown);
  });

  test("does not rewrite dollar pairs inside a list-indented tilde fence", () => {
    const markdown = ["- item", "    ~~~text", "    $$notMath$$", "    ~~~"].join("\n");

    expect(normalizeDisplayMath(markdown)).toBe(markdown);
  });

  test("recognizes a longer backtick closing fence inside a blockquote", () => {
    const markdown = ["> ```text", "> $$notMath$$", "> ````"].join("\n");

    expect(normalizeDisplayMath(markdown)).toBe(markdown);
  });

  test("recognizes a blockquote tilde closer without post-marker whitespace", () => {
    const markdown = ["> ~~~text", "> $$notMath$$", ">~~~", "", "$$after$$"].join("\n");
    const normalized = normalizeDisplayMath(markdown);

    expect(normalized).toContain("> $$notMath$$\n>~~~");
    expect(normalized).toContain("$$\nafter\n$$");
  });

  test("recognizes a longer blockquote backtick closer without post-marker whitespace", () => {
    const markdown = ["> ````text", "> $$notMath$$", ">`````", "", "$$after$$"].join("\n");
    const normalized = normalizeDisplayMath(markdown);

    expect(normalized).toContain("> $$notMath$$\n>`````");
    expect(normalized).toContain("$$\nafter\n$$");
  });

  test("recognizes a list fence when its closer uses a smaller valid continuation indent", () => {
    const markdown = ["- item", "    ~~~text", "    $$notMath$$", "  ~~~", "", "$$after$$"].join(
      "\n",
    );
    const normalized = normalizeDisplayMath(markdown);

    expect(normalized).toContain("    $$notMath$$\n  ~~~");
    expect(normalized).toContain("$$\nafter\n$$");
  });

  test("leaves an unfinished streaming expression visible", () => {
    expect(normalizeDisplayMath("途中 $$x^2")).toBe("途中 $$x^2");
  });

  test("does not use escaped dollar pairs as delimiters", () => {
    expect(normalizeDisplayMath("\\$$x^2$$")).toBe("\\$$x^2$$");
  });
});

describe("replaceEvidenceMarkers", () => {
  test("replaces each verified marker while preserving surrounding text", () => {
    const tree = rootWithParagraph("前 [[ev:12]] 中 [[ev:3]] 後");

    replaceEvidenceMarkers(tree);

    expect(tree).toEqual({
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "text", value: "前 " },
            {
              type: "link",
              url: "#",
              children: [{ type: "text", value: "" }],
              data: { hProperties: { [EVIDENCE_PROPERTY]: 12 } },
            },
            { type: "text", value: " 中 " },
            {
              type: "link",
              url: "#",
              children: [{ type: "text", value: "" }],
              data: { hProperties: { [EVIDENCE_PROPERTY]: 3 } },
            },
            { type: "text", value: " 後" },
          ],
        },
      ],
    });
  });

  test("does not change marker-like content in inline or block code", () => {
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "text", value: "本文 [[ev:1]]" },
            { type: "inlineCode", value: "[[ev:2]]" },
          ],
        },
        { type: "code", value: "[[ev:3]]" },
      ],
    };

    replaceEvidenceMarkers(tree);

    const paragraph = tree.children[0];
    const code = tree.children[1];
    expect(paragraph).toMatchObject({
      type: "paragraph",
      children: [
        { type: "text", value: "本文 " },
        { type: "link", data: { hProperties: { [EVIDENCE_PROPERTY]: 1 } } },
        { type: "inlineCode", value: "[[ev:2]]" },
      ],
    });
    expect(code).toMatchObject({ type: "code", value: "[[ev:3]]" });
  });

  test("only converts syntax with decimal digits", () => {
    const tree = rootWithParagraph("[[ev:]] [[ev:-1]] [[ev:1.5]] [[ev:1a]]");

    replaceEvidenceMarkers(tree);

    expect(tree).toEqual(rootWithParagraph("[[ev:]] [[ev:-1]] [[ev:1.5]] [[ev:1a]]"));
  });

  test("leaves markers literal inside link and link-reference ancestors", () => {
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            {
              type: "link",
              url: "https://example.com",
              children: [
                {
                  type: "emphasis",
                  children: [{ type: "text", value: "See [[ev:12]]" }],
                },
              ],
            },
            { type: "text", value: " と " },
            {
              type: "linkReference",
              identifier: "source",
              label: "source",
              referenceType: "full",
              children: [{ type: "text", value: "[[ev:13]]" }],
            },
          ],
        },
      ],
    };

    replaceEvidenceMarkers(tree);

    expect(tree).toEqual({
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            {
              type: "link",
              url: "https://example.com",
              children: [
                {
                  type: "emphasis",
                  children: [{ type: "text", value: "See [[ev:12]]" }],
                },
              ],
            },
            { type: "text", value: " と " },
            {
              type: "linkReference",
              identifier: "source",
              label: "source",
              referenceType: "full",
              children: [{ type: "text", value: "[[ev:13]]" }],
            },
          ],
        },
      ],
    });
  });

  test("is exposed through the remark transformer", () => {
    const tree = rootWithParagraph("[[ev:7]]");

    unified().use(remarkEvidence).runSync(tree);

    expect(tree).toMatchObject({
      children: [
        {
          children: [{ type: "link", data: { hProperties: { [EVIDENCE_PROPERTY]: 7 } } }],
        },
      ],
    });
  });
});
