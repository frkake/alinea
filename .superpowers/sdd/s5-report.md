# S5 Vocab Export UI — Implementation Report

**Status:** DONE

**Commits:**
- `3c42b17` feat(web): add onExportMarkdown prop + button to VocabHeader (S5)
- `7716118` feat(web): wire vocab Markdown export in VocabPage — respects active filters (S5)

**Test summary:** 2 new tests in `VocabHeader.test.tsx` pass (VT-S5-01 render, VT-S5-02 click); full web test suite green (336/336).

**Concerns:** None. URL construction is a direct `URLSearchParams` build over the already-decoded filter state in `page.tsx`. API-side validation handles any edge cases (invalid `sort`, etc.). `triggerDownload` helper reused as-is from `settings/download.ts`.
