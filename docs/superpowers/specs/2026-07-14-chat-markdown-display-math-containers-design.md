# Chat Markdown Display-Math Container Preservation Design

## Goal

Render same-line `$$...$$` expressions as display math without moving surrounding prose, display math, or code outside their original Markdown container.

## Problem

The current renderer rewrites source text before Markdown parsing. Its inserted blank lines do not retain blockquote markers or list-item continuation indentation, so same-line display math can escape a blockquote or list item. The source-range protection list also omits indented code blocks.

## Design

Replace source-string rewriting with a remark AST transformer that runs after `remark-math`.

- Identify `inlineMath` nodes whose source range is explicitly delimited by `$$...$$`.
- Split the containing paragraph into optional leading paragraph content, a flow `math` node, and optional trailing paragraph content.
- Splice those nodes into the paragraph's existing parent. This preserves the parent blockquote, list item, and nested container structure.
- Leave existing multiline `math` nodes untouched.
- Do not transform table cells, headings, links, HTML, inline code, fenced code, or indented code; they are not eligible paragraph-level `inlineMath` nodes.

## Alternatives Considered

1. Preserve and extend the source-string scanner. Rejected because correct blockquote/list reconstruction requires duplicating Markdown container parsing.
2. Leave `$$...$$` inline inside containers. Rejected because rendering becomes inconsistent by location.
3. Transform parsed Markdown AST. Chosen because the parser already supplies the required container hierarchy and code boundaries.

## Acceptance Criteria

- Root paragraphs, blockquotes, list items, and nested containers retain their original hierarchy when containing same-line `$$...$$`.
- An existing multiline display-math block inside a blockquote remains unchanged.
- Indented and fenced code containing `$$...$$` remains unchanged.
- Existing evidence-marker behavior and supported Markdown rendering remain unchanged.
- New focused tests fail before the transformer is implemented and pass afterward, followed by the full Web test suite, typecheck, and lint.
