# Official Repository Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** During arXiv ingest, extract a GitHub repository URL from already-fetched Atom metadata and populate `papers.official_repo_url` so the Resources tab can surface the "公式実装" suggestion card.

**Architecture:** Extend `ArxivMeta` with `official_repo_url`, implement extraction logic in `metadata.py`, wire into `_apply_metadata` in `pipeline.py`. No new network calls; no new dependencies.

**Tech Stack:** Python 3.12, re, alinea_core.arxiv.metadata, pytest.

---

### Task 1: Implement and test extraction heuristic (RED → GREEN)

**Files:**

- Modify: `packages/py-core/src/alinea_core/arxiv/metadata.py`
- Create: `packages/py-core/tests/test_official_repo_detection.py`

- [ ] Write RED tests in `packages/py-core/tests/test_official_repo_detection.py`:
  - Positive: comment contains `https://github.com/gnobitab/RectifiedFlow` → returns `https://github.com/gnobitab/RectifiedFlow`
  - Positive: comment contains bare `github.com/owner/repo` without scheme → returns `https://github.com/owner/repo`
  - Positive: URL only in abstract (no comment URL) → returns `https://github.com/owner/repo`
  - Positive: deep path `github.com/owner/repo/blob/main/README.md` → normalized to `https://github.com/owner/repo`
  - Positive: `.git`-suffixed URL `github.com/owner/repo.git` → stripped to `https://github.com/owner/repo`
  - Negative: comment has no GitHub URL → returns `None`
  - Negative: Gist URL `github.com/gist/abc123` → returns `None` (owner == "gist")
  - Negative: owner starts with `.` → returns `None`
  - Negative: empty comment and abstract → returns `None`
  - Priority: GitHub URL in both comment and abstract → returns comment's URL (comment takes priority)
- [ ] Add `_GITHUB_RE` pattern and `_extract_official_repo` function to `metadata.py`
- [ ] Add `official_repo_url: str | None = None` field to `ArxivMeta`
- [ ] Populate `official_repo_url` in `_parse_atom` by calling `_extract_official_repo`
- [ ] Run `uv run pytest packages/py-core/tests/test_official_repo_detection.py -q` and require all GREEN

### Task 2: Wire into pipeline _apply_metadata (RED → GREEN)

**Files:**

- Modify: `apps/worker/src/alinea_worker/pipeline.py`

- [ ] In `_apply_metadata`, add: `if meta.official_repo_url is not None: paper.official_repo_url = meta.official_repo_url`
- [ ] Run `uv run pytest apps/worker -q` and require all GREEN (no regressions)
- [ ] Run `uv run pytest apps/api -q` and require all GREEN (no regressions)

### Task 3: Full test suite

**Files:**

- No new files

- [ ] Run `uv run pytest apps/worker apps/api packages/py-core -q` and require all GREEN
- [ ] Commit all changes with message referencing S4
