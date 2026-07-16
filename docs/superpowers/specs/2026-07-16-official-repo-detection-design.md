# Official Repository Detection Design

Date: 2026-07-16
Status: Approved
Scope: Automatic GitHub repository URL extraction during arXiv paper ingest (docs/12-resources.md §5)

## Goals

- During arXiv ingest, extract a high-confidence official GitHub repository URL from already-fetched Atom API data and store it in `papers.official_repo_url`.
- Once stored, the Resources tab's `_current_suggestion` logic (already wired in `apps/api/src/alinea_api/routers/resources.py:489-504`) will surface the suggestion card to users.
- No new network calls; reuse the Atom API response already fetched in `_stage_fetching`.
- Be conservative: only write `official_repo_url` when confidence is high. Never overwrite an existing non-null value on reingest.

## Problem statement

`papers.official_repo_url` is never populated during real ingest — only by seed data. The Resources tab already renders a proposal card if the field is set, but it is never set. This feature closes the gap by extracting the URL from arXiv Atom metadata during ingest.

## Data sources (no new network calls)

The arXiv Atom API response (already fetched in `_stage_fetching` via `fetch_metadata`) includes:

- `<arxiv:comment>` — frequently contains lines like "Code: https://github.com/owner/repo" or "Code available at github.com/owner/repo"
- `<summary>` (abstract) — sometimes contains "implementation at github.com/owner/repo"

Priority order per docs/12 §5.2: comment field first, abstract second. Multiple candidates in the same source → pick the first occurrence.

## Extraction heuristic

Pattern: `github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)`

Exclusions (noise reduction):
- Owner or repo starting with `.` (relative path artifact)
- Known non-repo paths: only `owner/repo` accepted (exactly two path segments)
- Gist URLs: `github.com/gist/...` → skip (owner == "gist")
- Deep paths like `github.com/owner/repo/blob/main/...` → normalize to `owner/repo` (strip trailing path)
- Repos ending in `.git` → strip the `.git` suffix

Confidence filter (conservative):
- Only extract from comment and abstract fields, not from arbitrary document links
- Return `None` if no confident match

## Data flow

```
fetch_metadata()  →  ArxivMeta.official_repo_url (str | None)
                              ↓
_apply_metadata(paper, meta)  →  if meta.official_repo_url is not None:
                                     paper.official_repo_url = meta.official_repo_url
```

`ArxivMeta` gains one new optional field: `official_repo_url: str | None = None`.

## Write policy

- `_apply_metadata` sets `paper.official_repo_url` only when `meta.official_repo_url is not None`.
- This preserves manually set or seed values and avoids erasing a confirmed URL on reingest.
- The normalized URL written is `https://github.com/{owner}/{repo}` (canonical form).

## Test strategy

Pure-unit tests (no network, no DB, no pipeline):
- Feed raw Atom XML with a comment containing `github.com/gnobitab/RectifiedFlow` → assert extracted URL.
- Feed Atom XML with comment only containing venue text (no GitHub URL) → assert `None`.
- Feed Atom XML with GitHub URL only in abstract → assert extraction succeeds.
- Feed Atom XML with Gist URL in comment → assert `None`.
- Feed Atom XML with `.git`-suffixed URL → assert suffix stripped.
- Feed Atom XML with deep path `github.com/owner/repo/blob/main/README.md` → assert normalized to `owner/repo`.

All tests live in `packages/py-core/tests/test_official_repo_detection.py`.
