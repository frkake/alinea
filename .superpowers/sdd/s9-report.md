# S9 Anki Export — SDD Report

**Status:** COMPLETE
**Date:** 2026-07-16

## Format Decision

**Chose: TSV (option b)**
Rationale: no-casual-dep norm prohibits `genanki`. Anki v2.1+ natively imports tab-separated text with Front/Back/tags columns. `.apkg` via genanki flagged as future v2 needing user approval.

## Commits

- `cd7c010` feat(api): add _render_anki_tsv() pure function with PY-VOC-10 unit test
- `b3a3070` feat(api): add GET /api/vocab/export/anki TSV endpoint (PY-VOC-10)
- `7eab6c7` chore(sdk): regenerate after adding vocab_export_anki endpoint
- `664d6fe` feat(web): add Anki export button to VocabHeader (S9 TS-VOCAB-ANKI)

## Test Summary

- PY-VOC-10: 4 tests PASSED (1 unit: test_render_anki_tsv_fields + 3 integration: test_export_anki_tsv_structure, test_export_anki_contains_term_and_context, test_export_anki_filter_kind)
- TS-VOCAB-ANKI: 2 tests PASSED (renders Anki export button, calls onAnkiExport on click)
- Full vocab suite: 17/17 PASSED
- Full web suite: 741/741 PASSED

## Decisions Needing User Review

- `.apkg` format (genanki dep): not implemented. User must approve `uv add genanki` before v2 implementation. See `docs/superpowers/specs/2026-07-16-anki-export-design.md` §9 for details.

## Blocking Concerns

None.
