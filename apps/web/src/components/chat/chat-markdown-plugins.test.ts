import type { Root } from "mdast";
import { unified } from "unified";
import { describe, expect, test } from "vitest";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkParse from "remark-parse";
import {
  EVIDENCE_PROPERTY,
  remarkDisplayMath,
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

function transformDisplayMath(markdown: string): Root {
  const processor = unified()
    .use(remarkParse)
    .use(remarkGfm)
    .use(remarkMath)
    .use(remarkDisplayMath(markdown));
  return processor.runSync(processor.parse(markdown)) as Root;
}

describe("remarkDisplayMath", () => {
  test("keeps same-line display math inside a blockquote", () => {
    const tree = transformDisplayMath("> before $$x^2$$ after");

    expect(tree).toMatchObject({
      children: [
        {
          type: "blockquote",
          children: [
            { type: "paragraph", children: [{ type: "text", value: "before " }] },
            { type: "math", value: "x^2" },
            { type: "paragraph", children: [{ type: "text", value: " after" }] },
          ],
        },
      ],
    });
  });

  test("keeps same-line display math inside a list item", () => {
    const tree = transformDisplayMath("- before $$x^2$$ after");

    expect(tree).toMatchObject({
      children: [
        {
          type: "list",
          children: [
            {
              type: "listItem",
              children: [
                { type: "paragraph", children: [{ type: "text", value: "before " }] },
                { type: "math", value: "x^2" },
                { type: "paragraph", children: [{ type: "text", value: " after" }] },
              ],
            },
          ],
        },
      ],
    });
  });

  test("does not convert double-dollar pairs inside indented or fenced code", () => {
    const markdown = ["    $$indented$$", "", "```text", "$$fenced$$", "```"].join("\n");
    const tree = transformDisplayMath(markdown);

    expect(tree).toMatchObject({
      children: [
        { type: "code", value: "$$indented$$" },
        { type: "code", lang: "text", value: "$$fenced$$" },
      ],
    });
  });

  test("leaves existing multiline display math inside a blockquote unchanged", () => {
    const tree = transformDisplayMath(["> $$", "> x^2", "> $$"].join("\n"));

    expect(tree).toMatchObject({
      children: [{ type: "blockquote", children: [{ type: "math", value: "x^2" }] }],
    });
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
