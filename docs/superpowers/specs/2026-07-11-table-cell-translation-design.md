# Table Cell Translation Design

## Goal

Translate prose in table cells generically across arXiv HTML, LaTeX, and extracted PDF tables,
render the same physical-cell mapping in the Viewer and generated Japanese PDF, and keep the
primary translation denominator at one unit per table block.

## Persisted contract

`translation_units.content_ja` keeps the existing inline-list contract for non-table blocks and
legacy table captions. New table results use this strict versioned object:

```json
{
  "kind": "table",
  "version": 1,
  "caption": [{"t": "text", "v": "日本語キャプション"}],
  "cells": [["手法", null, "精度"], [null, null, null]]
}
```

- `caption` is `Inline[] | null`. `null` means no translated caption.
- `cells` is `(string | null)[][] | null` and has exactly the same physical row/cell shape as the
  canonical source grid. A string replaces that cell's visible prose; `null` preserves the source.
- `text_ja` remains a plain searchable projection: translated caption followed by translated cell
  strings. Rendering never treats that projection as a table caption when the typed object exists.
- Unknown keys, versions, wrong dimensions, oversized values, controls, or a translated value in a
  non-target cell fail closed. Legacy `Inline[]` remains caption-only and never counts as cell work.

## Canonical source grid

Create `alinea_core.translation.table_cells` as the single parser/validator used by translation,
Viewer wire output, article sources, and Japanese-PDF replacement.

- Parse bounded HTML (`table/tr/th/td`, colspan/rowspan) and bounded LaTeX tabular/tabularx forms,
  including multicolumn/multirow wrappers. PDF extraction already stores detected rows as HTML.
- Assign physical IDs `r{row}c{cell}`; spans do not create synthetic cells.
- Preserve a cleaned visible source string, header/span metadata, protected math fragments, and
  LaTeX replacement offsets/structural wrappers where available.
- Enforce limits before parsing (raw bytes, rows, cells, cell length, nesting/depth). Malformed input
  returns an explicit unsupported result rather than partially shifted rows.
- Select translation targets generically: prose with translatable alphabetic text is targeted;
  numeric/symbol/math/URL-only and compact uppercase identifier cells remain source. Already-
  Japanese-only cells remain source. No paper IDs, titles, labels, or domain-specific cell values.

Viewer source-table rendering must consume the canonical grid supplied by the API, with its current
raw parser only as a legacy fallback. This makes persisted translation indices and displayed cells
identical.

## Translation execution

- `reason="table"` always requests table cells. Other reasons request them when the resolved stored
  plan has `translate_table_cells=true`; caption translation remains available in both modes.
- Translate caption and selected cells as pseudo targets (`{block_id}::caption`,
  `{block_id}::r0c0`, ...) through the existing placeholder verification and retry path. Structured
  output must contain only the exact expected IDs; duplicates, omissions, unknown IDs, or broken
  placeholders fail the table unit atomically.
- The table source hash includes the canonical caption/grid plus whether cells were requested, so a
  valid caption-only legacy unit cannot suppress an explicit cell request.
- A table remains one `TranslationUnit` and one primary progress item. Cell work never enlarges the
  primary/auxiliary block denominator.
- Succeeded job reuse is valid for `work_kind="table"` only when the typed result matches the current
  source grid and all target cells are present. Literal/full work also requires cell completeness
  when its effective plan enables table cells.

## Frontend behavior

- Overlay translated strings onto canonical physical cells while preserving row/column spans,
  header styling, math rendering, and horizontal overflow containment.
- Typed `caption` is rendered as the Japanese caption; legacy inline content remains supported.
- Each table exposes `この表を翻訳` whenever cell work is absent. The visible button calls the
  existing section-translate endpoint with `block_id`, follows its job SSE, and invalidates the
  exact units/viewer queries on completion. Pending, success, failure, and retry states are visible.
- Translation and bilingual modes both provide the action. Source mode continues to show source.

## Japanese PDF and articles

- For LaTeX sources, replace only the visible body of physical cells with escaped translated text.
  Preserve tabular alignment, multicolumn/multirow wrappers, labels, rules, row spacing, math-only
  cells, and row separators. Any mapping mismatch leaves the complete source table unchanged and
  records a warning; never emit a partially shifted table.
- Caption replacement uses the typed caption when present and retains legacy behavior.
- Article table sources use the canonical grid and overlay the same translated cell matrix.

## Verification

TDD covers HTML/LaTeX/PDF-style grids; spans; nested commands; mixed prose/math; numeric-only cells;
malformed/oversized tables; exact output IDs; legacy captions; setting on/off; explicit table work;
job reuse; Viewer overlay/action/SSE; overflow layout; article rows; LaTeX replacement; and compile-
level Japanese-PDF regression. Production logic is corpus-independent.
