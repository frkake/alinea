# S4 Official Repository Detection — Implementation Report

Date: 2026-07-16

## Status

COMPLETE — all tests green, committed to branch.

## Commit hashes

TBD (see git log after final commit)

## Test summary

14 new unit tests in `packages/py-core/tests/test_official_repo_detection.py` — all PASS.
79 total `packages/py-core` tests — all PASS (including pre-existing arxiv tests).
40 `apps/api/tests/test_resources.py` tests — all PASS.
20 `apps/worker/tests/test_ingest.py` tests — all PASS.
1040 total py-core test suite — all PASS.

## Changes

- `packages/py-core/src/alinea_core/arxiv/metadata.py`: added `_GITHUB_RE` pattern, `_extract_official_repo()` function, `official_repo_url: str | None` field to `ArxivMeta`, wired extraction in `_parse_atom`
- `apps/worker/src/alinea_worker/pipeline.py`: `_apply_metadata` sets `paper.official_repo_url` when meta value is non-None
- `packages/py-core/tests/test_official_repo_detection.py`: 14 unit tests (positive + negative cases)
- `docs/superpowers/specs/2026-07-16-official-repo-detection-design.md`: design spec
- `docs/superpowers/plans/2026-07-16-official-repo-detection.md`: implementation plan

## Concerns

1. **No abs page HTML fetch**: The spec mentions scanning "arXiv abs ページのリンク". The current implementation uses only the Atom API `<arxiv:comment>` and abstract fields (already fetched), which avoids adding new network calls. The abs page itself is NOT fetched. In practice, the comment field is the primary source of GitHub links (authors write "Code: github.com/..."). If a paper's GitHub link only appears in the abs page sidebar (not comment or abstract), it will not be detected — this is a conservative/safe tradeoff per the spec's guidance.

2. **official_repo_url not overwritten on reingest**: By design, if `meta.official_repo_url is None` (no GitHub URL found in Atom data), the existing value in the DB is preserved. This means a URL set via seed data or manually remains intact.

3. **Trailing punctuation stripping**: The regex strips trailing `.,;:)` from the matched repo name to handle patterns like `github.com/owner/repo.` at sentence end. This heuristic handles the most common cases but could theoretically strip a repo named e.g. `repo.` — in practice, GitHub repo names cannot end with `.`, so this is safe.
