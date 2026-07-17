# S9 Anki エクスポート Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/vocab/export/anki` endpoint that renders vocab entries as a Anki-importable TSV, plus a Web UI button on the vocab page.

**Architecture:** Mirror the existing Markdown export endpoint (`vocab_export_markdown`) exactly — same filter params, same `_vocab_filters` / `_detail_out` pipeline — but render via a new `_render_anki_tsv()` function. The TSV uses Anki's native text-import format (header directives + 3-column: Front/Back/tags). Web UI adds an "Ankiへ書き出す" button to `VocabHeader` that assembles the download URL from the current URL search params.

**Tech Stack:** FastAPI (Python), SQLAlchemy async, Pydantic, React + TypeScript, @tanstack/react-query, vitest

## Global Constraints

- No new Python dependencies (`uv add` is forbidden; genanki is flagged as future v2).
- Mirror filter params exactly: `kind`, `due`, `q`, `library_item_id`, `sort`.
- `operation_id`: `vocab_export_anki`
- TSV file name: `alinea-vocab-{YYYYMMDD}.txt`
- Content-Type: `text/plain; charset=utf-8`
- Follow existing code style: Japanese comments, structlog, same import ordering.
- TDD: write failing tests first, then implement.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `apps/api/src/alinea_api/routers/vocab.py` | Modify (lines ~485 end of markdown section) | Add `_render_anki_tsv()` + `export_vocab_anki` endpoint |
| `apps/api/tests/test_vocab.py` | Modify (append) | Add PY-VOC-10 tests |
| `packages/api-client/openapi.json` | Regenerate | SDK source |
| `packages/api-client/src/generated/sdk.gen.ts` | Regenerate | SDK types |
| `apps/web/src/components/vocab/VocabHeader.tsx` | Modify | Add Anki export button |
| `apps/web/src/components/vocab/VocabHeader.test.tsx` | Create | TS-VOCAB-ANKI tests |

---

### Task 1: Backend — `_render_anki_tsv()` pure function + unit test

**Files:**
- Modify: `apps/api/src/alinea_api/routers/vocab.py`
- Modify: `apps/api/tests/test_vocab.py`

**Interfaces:**
- Produces: `_render_anki_tsv(entries: list[VocabEntryDetail]) -> str`

- [ ] **Step 1: Write the failing unit test**

Append to `apps/api/tests/test_vocab.py` after the existing `PY-VOC-08` section:

```python
# ============================================================================
# PY-VOC-10: Anki TSV エクスポート
# ============================================================================
def test_render_anki_tsv_fields() -> None:
    """_render_anki_tsv() が Front/Back/tags 列を正しく組み立てる。"""
    from alinea_api.routers.vocab import _render_anki_tsv
    from alinea_api.schemas.vocab import (
        VocabAi,
        VocabEntryDetail,
        VocabHighlight,
        VocabMeaning,
        VocabSource,
        VocabSrs,
    )
    from alinea_api.schemas.chat import AnchorRef

    entry = VocabEntryDetail(
        id="test-id",
        kind="word",
        term="reflow",
        meaning_short="リフロー",
        source=VocabSource(
            library_item_id="lib-id",
            paper_title="Rectified Flow Paper",
            display="Rectified Flow · §2.1",
        ),
        added_at="2026-01-01",
        generation="done",
        pos_label="noun",
        ipa="/ˈriːfloʊ/",
        anchor=AnchorRef(block_id="blk", display="§2.1"),
        context_sentence="The reflow procedure straightens paths.",
        highlight=VocabHighlight(start=4, end=10),
        ai=VocabAi(
            context_meaning=VocabMeaning(short="リフロー", long="パスを整列させる手順"),
            interpretation="経路の整列手法",
            etymology=None,
            mnemonic="re + flow = 再び流す",
        ),
        srs=VocabSrs(stage=1, next_review_at="2026-01-02", review_count=0, history=[]),
    )

    tsv = _render_anki_tsv([entry])
    lines = tsv.splitlines()

    # ヘッダ行
    assert lines[0] == "#separator:tab"
    assert lines[1] == "#html:true"
    assert lines[2] == "#tags column:3"

    # カード行
    assert len(lines) == 4
    parts = lines[3].split("\t")
    assert len(parts) == 3

    front, back, tags = parts
    # Front
    assert "reflow" in front
    assert "noun" in front
    assert "/ˈriːfloʊ/" in front

    # Back
    assert "リフロー" in back
    assert "パスを整列させる手順" in back
    assert "The reflow procedure straightens paths." in back
    assert "経路の整列手法" in back
    assert "re + flow = 再び流す" in back
    assert "Rectified Flow · §2.1" in back

    # Tags
    assert "alinea" in tags
    assert "word" in tags
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
uv run --directory apps/api pytest apps/api/tests/test_vocab.py::test_render_anki_tsv_fields -v 2>&1 | tail -20
```

Expected: `FAILED` with `ImportError` or `cannot import name '_render_anki_tsv'`.

- [ ] **Step 3: Implement `_render_anki_tsv()` in vocab.py**

In `apps/api/src/alinea_api/routers/vocab.py`, add after the `_render_vocab_markdown` function (after line 485, before the `# ============================================================================` of create section):

```python
# ============================================================================
# Anki TSV エクスポート(§11.9。docs/11 §9・PY-VOC-10)
# ============================================================================
import re as _re


def _paper_slug(title: str) -> str:
    """論文タイトルから Anki タグ用 slug を生成する(ASCII + _ のみ・最大 30 文字)。"""
    slug = _re.sub(r"[^A-Za-z0-9]+", "_", title)
    slug = slug.strip("_")[:30].rstrip("_")
    return slug or "paper"


def _render_anki_tsv(entries: list[VocabEntryDetail]) -> str:
    """VocabEntryDetail リストを Anki テキストインポート形式(TSV)に変換する。

    仕様(docs/superpowers/specs/2026-07-16-anki-export-design.md):
    - 1〜3 行目: Anki ヒント行(separator/html/tags)
    - 4 行目以降: Front<TAB>Back<TAB>tags
    - セル内改行は \\n(Anki は <br> として表示)
    """
    header = "#separator:tab\n#html:true\n#tags column:3"
    card_lines: list[str] = []

    for e in entries:
        # --- Front ---
        front_parts = [e.term]
        meta_parts: list[str] = []
        if e.pos_label:
            meta_parts.append(e.pos_label)
        if e.ipa:
            meta_parts.append(e.ipa)
        if meta_parts:
            front_parts.append("  ".join(meta_parts))
        front = "\n".join(front_parts)

        # --- Back ---
        back_parts: list[str] = []
        if e.ai.context_meaning:
            if e.ai.context_meaning.short:
                back_parts.append(e.ai.context_meaning.short)
            if e.ai.context_meaning.long:
                back_parts.append(e.ai.context_meaning.long)
        back_parts.append("---")
        back_parts.append(f"文脈: {e.context_sentence}")
        if e.ai.interpretation:
            back_parts.append(f"解釈: {e.ai.interpretation}")
        if e.ai.etymology:
            back_parts.append(f"語源: {e.ai.etymology}")
        if e.ai.mnemonic:
            back_parts.append(f"覚えるコツ: {e.ai.mnemonic}")
        back_parts.append(f"出典: {e.source.display}")
        back = "\n".join(back_parts)

        # --- Tags ---
        slug = _paper_slug(e.source.paper_title)
        tags = f"alinea {e.kind} {slug}"

        card_lines.append(f"{front}\t{back}\t{tags}")

    return header + "\n" + "\n".join(card_lines)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
uv run --directory apps/api pytest apps/api/tests/test_vocab.py::test_render_anki_tsv_fields -v 2>&1 | tail -10
```

Expected: `PASSED`.

- [ ] **Step 5: Commit**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
git add apps/api/src/alinea_api/routers/vocab.py apps/api/tests/test_vocab.py
git commit -m "feat(api): add _render_anki_tsv() pure function with PY-VOC-10 unit test"
```

---

### Task 2: Backend — `GET /api/vocab/export/anki` endpoint + integration tests

**Files:**
- Modify: `apps/api/src/alinea_api/routers/vocab.py`
- Modify: `apps/api/tests/test_vocab.py`

**Interfaces:**
- Consumes: `_render_anki_tsv(entries: list[VocabEntryDetail]) -> str` (Task 1)
- Consumes: `_vocab_filters(user_id, kind, due, q, library_item_id, today)` (existing)
- Consumes: `_detail_out(db, entry, cache)` (existing)
- Consumes: `today_jst()` (existing)
- Produces: `GET /api/vocab/export/anki` → `Response(text/plain)`

- [ ] **Step 1: Write the failing integration tests**

Append to `apps/api/tests/test_vocab.py` (after `test_render_anki_tsv_fields`):

```python
async def test_export_anki_tsv_structure(vocab_ctx: SimpleNamespace) -> None:
    """PY-VOC-10a: エンドポイントが正しい TSV ヘッダと Content-Disposition を返す。"""
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    assert created.status_code == 201

    resp = await vocab_ctx.client.get("/api/vocab/export/anki")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "attachment" in resp.headers["content-disposition"]
    assert "alinea-vocab-" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.txt"')

    text = resp.text
    lines = text.splitlines()
    assert lines[0] == "#separator:tab"
    assert lines[1] == "#html:true"
    assert lines[2] == "#tags column:3"
    # カード行が 1 行以上
    assert len(lines) >= 4
    # カード行はタブ 2 本(3 列)
    assert lines[3].count("\t") == 2


async def test_export_anki_contains_term_and_context(vocab_ctx: SimpleNamespace) -> None:
    """PY-VOC-10b: カード内に term と context_sentence が含まれる。"""
    created = await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))
    assert created.status_code == 201

    resp = await vocab_ctx.client.get("/api/vocab/export/anki")
    assert resp.status_code == 200

    text = resp.text
    assert "reflow" in text
    assert "The reflow procedure straightens paths." in text
    assert "alinea" in text


async def test_export_anki_filter_kind(vocab_ctx: SimpleNamespace) -> None:
    """PY-VOC-10c: kind=word フィルタで idiom は除外される。"""
    # word エントリ追加
    await vocab_ctx.client.post("/api/vocab", json=_create_payload(vocab_ctx))

    # idiom エントリ追加(別の term)
    idiom_payload = dict(_create_payload(vocab_ctx))
    idiom_payload["term"] = "hit the ground running"
    # anchor は共有でよい(term で重複チェックされる)
    await vocab_ctx.client.post("/api/vocab", json=idiom_payload)

    # kind=word のみ要求
    resp = await vocab_ctx.client.get("/api/vocab/export/anki?kind=word")
    assert resp.status_code == 200

    card_lines = [l for l in resp.text.splitlines() if not l.startswith("#")]
    # word タグのみ
    for line in card_lines:
        tags = line.split("\t")[2]
        assert "word" in tags
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
uv run --directory apps/api pytest apps/api/tests/test_vocab.py::test_export_anki_tsv_structure -v 2>&1 | tail -10
```

Expected: `FAILED` with 404 or `not found`.

- [ ] **Step 3: Add the endpoint to vocab.py**

In `apps/api/src/alinea_api/routers/vocab.py`, add the endpoint after `_render_anki_tsv` function:

```python
@router.get("/api/vocab/export/anki", operation_id="vocab_export_anki")
async def export_vocab_anki(
    user: CurrentUser,
    db: DbDep,
    kind: Annotated[list[str] | None, Query()] = None,
    due: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    library_item_id: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "added_at",
) -> Response:
    """語彙帳を Anki テキストインポート形式(TSV)でエクスポートする(docs/11 §9・PY-VOC-10)。

    フィルタは /api/vocab/export/markdown と同一。
    """
    _validate_kind(kind)
    if sort not in _SORTS:
        raise ProblemException("validation_error", detail="sort は added_at|term のみ有効です")

    today = today_jst()
    conds = _vocab_filters(
        str(user.id), kind=kind, due=due, q=q, library_item_id=library_item_id, today=today
    )
    asc = sort == "term"
    col = func.lower(VocabEntry.term) if asc else VocabEntry.created_at
    stmt = (
        select(VocabEntry)
        .where(*conds)
        .order_by(col.asc() if asc else col.desc(), VocabEntry.id.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    cache = _SourceCache()
    entries = [await _detail_out(db, e, cache) for e in rows]
    content = _render_anki_tsv(entries)
    filename = f"alinea-vocab-{today.strftime('%Y%m%d')}.txt"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 4: Run all three new integration tests**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
uv run --directory apps/api pytest apps/api/tests/test_vocab.py -k "anki" -v 2>&1 | tail -20
```

Expected: all 4 PASSED (`test_render_anki_tsv_fields`, `test_export_anki_tsv_structure`, `test_export_anki_contains_term_and_context`, `test_export_anki_filter_kind`).

- [ ] **Step 5: Run the full vocab test suite**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
uv run --directory apps/api pytest apps/api/tests/test_vocab.py -q 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
git add apps/api/src/alinea_api/routers/vocab.py apps/api/tests/test_vocab.py
git commit -m "feat(api): add GET /api/vocab/export/anki TSV endpoint (PY-VOC-10)"
```

---

### Task 3: SDK Regeneration

**Files:**
- Modify: `packages/api-client/openapi.json`
- Regenerate: `packages/api-client/src/generated/sdk.gen.ts`
- Regenerate: `packages/api-client/src/generated/types.gen.ts`

**Interfaces:**
- Consumes: `GET /api/vocab/export/anki` (Task 2)
- Produces: `vocabExportAnki` function exported from `@alinea/api-client`

- [ ] **Step 1: Export updated OpenAPI schema**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
uv run --directory apps/api python -m alinea_api.export_openapi > packages/api-client/openapi.json
```

- [ ] **Step 2: Verify the new endpoint appears in openapi.json**

```bash
grep -A5 "vocab_export_anki\|/api/vocab/export/anki" /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5/packages/api-client/openapi.json | head -20
```

Expected: shows the new path and operation_id.

- [ ] **Step 3: Regenerate SDK**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5/packages/api-client
pnpm generate 2>&1 | tail -10
```

Expected: exits cleanly, updates `src/generated/`.

- [ ] **Step 4: Verify `vocabExportAnki` exported**

```bash
grep "vocabExportAnki" /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5/packages/api-client/src/generated/sdk.gen.ts | head -5
```

Expected: finds the function.

- [ ] **Step 5: Commit**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
git add packages/api-client/openapi.json packages/api-client/src/generated/
git commit -m "chore(sdk): regenerate after adding vocab_export_anki endpoint"
```

---

### Task 4: Web UI — Anki button in VocabHeader + tests

**Files:**
- Modify: `apps/web/src/components/vocab/VocabHeader.tsx`
- Create: `apps/web/src/components/vocab/VocabHeader.test.tsx`
- Modify: `apps/web/src/app/(app)/vocab/[[...vocabId]]/page.tsx`

**Interfaces:**
- Consumes: `triggerDownload(url: string): void` from `@/components/settings/download`
- Consumes: `VocabHeaderProps` (currently: total, dueCount, searchValue, searchFetching, onSearchChange, onStartReview, reviewLoading)
- Produces: extended `VocabHeaderProps` with `onAnkiExport: () => void`

- [ ] **Step 1: Write the failing frontend tests**

Create `apps/web/src/components/vocab/VocabHeader.test.tsx`:

```tsx
/**
 * VocabHeader テスト — Anki エクスポートボタン(TS-VOCAB-ANKI)
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { VocabHeader } from "@/components/vocab/VocabHeader";

const defaultProps = {
  total: 5,
  dueCount: 2,
  searchValue: "",
  searchFetching: false,
  onSearchChange: vi.fn(),
  onStartReview: vi.fn(),
  reviewLoading: false,
  onAnkiExport: vi.fn(),
};

describe("VocabHeader — Anki export button (TS-VOCAB-ANKI)", () => {
  it("renders the Anki export button", () => {
    render(<VocabHeader {...defaultProps} />);
    expect(screen.getByRole("button", { name: /Anki/i })).toBeInTheDocument();
  });

  it("calls onAnkiExport when Anki button is clicked", async () => {
    const onAnkiExport = vi.fn();
    render(<VocabHeader {...defaultProps} onAnkiExport={onAnkiExport} />);
    await userEvent.click(screen.getByRole("button", { name: /Anki/i }));
    expect(onAnkiExport).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
pnpm --filter @alinea/web test --run -- VocabHeader 2>&1 | tail -20
```

Expected: FAIL because `onAnkiExport` prop does not exist yet.

- [ ] **Step 3: Update VocabHeader.tsx**

Replace the contents of `apps/web/src/components/vocab/VocabHeader.tsx`:

```tsx
"use client";

import { VocabSearchBox } from "@/components/vocab/VocabSearchBox";

export interface VocabHeaderProps {
  total: number;
  dueCount: number;
  searchValue: string;
  searchFetching: boolean;
  onSearchChange: (v: string) => void;
  onStartReview: () => void;
  reviewLoading: boolean;
  /** Anki TSV エクスポートトリガ(S9)。 */
  onAnkiExport: () => void;
}

/** 見出し行(4d §4.2.3)。「語彙帳」「{n} 語 — 読んだ論文の文脈から」+ 検索 + 復習をはじめる + Anki エクスポート。 */
export function VocabHeader({
  total,
  dueCount,
  searchValue,
  searchFetching,
  onSearchChange,
  onStartReview,
  reviewLoading,
  onAnkiExport,
}: VocabHeaderProps) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 16, fontWeight: 700 }}>語彙帳</span>
      <span style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>
        {total} 語 — 読んだ論文の文脈から
      </span>
      <span style={{ flex: 1 }} />
      <VocabSearchBox value={searchValue} onChange={onSearchChange} fetching={searchFetching} />
      <button
        type="button"
        onClick={onAnkiExport}
        title="現在のフィルタ結果を Anki へ書き出す"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          height: 28,
          padding: "0 13px",
          borderRadius: 6,
          border: "1px solid var(--pr-border)",
          background: "var(--pr-bg-panel)",
          color: "var(--pr-text-mid)",
          fontSize: 11.5,
          fontWeight: 600,
          fontFamily: "inherit",
          cursor: "pointer",
        }}
      >
        Ankiへ書き出す
      </button>
      <button
        type="button"
        onClick={onStartReview}
        disabled={dueCount === 0 || reviewLoading}
        title={dueCount === 0 ? "復習期の語彙はありません" : undefined}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          height: 28,
          padding: "0 13px",
          borderRadius: 6,
          border: "none",
          background: "var(--pr-acc)",
          color: "#FFFFFF",
          fontSize: 11.5,
          fontWeight: 600,
          fontFamily: "inherit",
          cursor: dueCount === 0 ? "default" : "pointer",
          opacity: dueCount === 0 || reviewLoading ? 0.7 : 1,
        }}
      >
        復習をはじめる
        {dueCount > 0 ? (
          <span
            style={{
              fontSize: 9.5,
              fontWeight: 500,
              opacity: 0.8,
              border: "1px solid rgba(255,255,255,0.4)",
              borderRadius: 3,
              padding: "0 5px",
            }}
          >
            {dueCount}
          </span>
        ) : null}
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Update page.tsx to wire `onAnkiExport`**

In `apps/web/src/app/(app)/vocab/[[...vocabId]]/page.tsx`:

1. Add import at top of file (with other imports):
```tsx
import { triggerDownload } from "@/components/settings/download";
```

2. Add helper function before the `return` statement (after `handleOpenSource`):
```tsx
  function handleAnkiExport(): void {
    const sp = new URLSearchParams();
    if (kind) sp.set("kind", kind);
    if (dueOnly) sp.set("due", "true");
    if (q) sp.set("q", q);
    if (sort !== "added_at") sp.set("sort", sort);
    const qs = sp.toString();
    triggerDownload(`/api/vocab/export/anki${qs ? `?${qs}` : ""}`);
  }
```

3. Add `onAnkiExport` prop to `<VocabHeader>` (after `reviewLoading`):
```tsx
        onAnkiExport={handleAnkiExport}
```

- [ ] **Step 5: Run frontend tests**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
pnpm --filter @alinea/web test --run -- VocabHeader 2>&1 | tail -20
```

Expected: 2 tests PASSED.

- [ ] **Step 6: Run full web test suite**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
pnpm --filter @alinea/web test --run 2>&1 | tail -20
```

Expected: All pass (no regressions).

- [ ] **Step 7: Commit**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
git add apps/web/src/components/vocab/VocabHeader.tsx \
        apps/web/src/components/vocab/VocabHeader.test.tsx \
        apps/web/src/app/'(app)'/vocab/'[[...vocabId]]'/page.tsx
git commit -m "feat(web): add Anki export button to VocabHeader (S9 TS-VOCAB-ANKI)"
```

---

### Task 5: Write SDD report + final full test run

**Files:**
- Create: `.superpowers/sdd/s9-report.md`

- [ ] **Step 1: Run full API test suite**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
uv run --directory apps/api pytest apps/api -q 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 2: Run full web test suite**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
pnpm --filter @alinea/web test --run 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 3: Write SDD report**

Create `.superpowers/sdd/s9-report.md` with:

```markdown
# S9 Anki Export — SDD Report

**Status:** COMPLETE  
**Date:** 2026-07-16

## Format Decision

**Chose: TSV (option b)**  
Rationale: no-casual-dep norm prohibits `genanki`. Anki v2.1+ natively imports tab-separated text with Front/Back/tags columns. `.apkg` via genanki flagged as future v2 needing user approval.

## Commits

(fill in commit hashes after completion)

## Test Summary

- PY-VOC-10: 4 tests PASSED (unit + 3 integration)
- TS-VOCAB-ANKI: 2 tests PASSED

## Decisions Needing User Review

- `.apkg` format (genanki dep): not implemented. User must approve `uv add genanki` before v2.

## Blocking Concerns

None.
```

- [ ] **Step 4: Commit**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a948c6aef510f35a5
git add .superpowers/sdd/s9-report.md docs/superpowers/specs/2026-07-16-anki-export-design.md docs/superpowers/plans/2026-07-16-anki-export.md
git commit -m "docs: add S9 Anki export spec + plan + SDD report"
```
