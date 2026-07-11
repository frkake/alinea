# LaTeX Source-Order Evaluator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LaTeX figure/structure discovery respect literal boundaries, actual class/package load order, definition/call order, and fail closed for invoked unsupported structural definitions.

**Architecture:** Keep archive extraction and the existing block parser, but add a single ordered source evaluator in `latex_parser.py`. It recursively loads only declared class/package/input files, mutates one macro/frontmatter state in source order, expands supported calls immediately, snapshots `maketitle`, and records unsupported definitions for invocation-time structural reachability checks; the downstream parser receives no final user-macro dictionary, preventing later renewals from changing earlier output.

**Tech Stack:** Python 3.12, stdlib regex/dataclasses, pytest, Ruff, mypy.

---

### Task 1: Literal-aware top-level scanner

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`
- Test: `packages/py-core/tests/test_latex_parser.py`

- [ ] **Step 1: Add failing literal-structure tests**

Add parameterized cases covering inline `verb`/`verb*` and `verbatim`, `lstlisting`, and `minted` bodies containing fake `section`, `begin{figure|center}`, and `includegraphics`; assert code/code-inline preservation, one real section only, and no fake figures.

- [ ] **Step 2: Verify RED**

Run: `uv run --project packages/py-core pytest packages/py-core/tests/test_latex_parser.py -k literal_top_level -q`

Expected: fake structure is currently emitted from `_iter_top_level`.

- [ ] **Step 3: Make `_iter_top_level` consume literal regions atomically**

Use `_next_literal_region(text, cursor)` alongside section/begin/appendix candidates. Inline literal source is appended to the current text node; block literal environments become one `env` node with raw inner content. Never search for structural tokens between a literal region's start/end.

- [ ] **Step 4: Verify GREEN and existing literal tests**

Run: `uv run --project packages/py-core pytest packages/py-core/tests/test_latex_parser.py -k 'literal or inline_verb' -q`

### Task 2: Ordered loaded-source evaluator

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`
- Test: `packages/py-core/tests/test_latex_parser.py`

- [ ] **Step 1: Add failing definition/load-order matrix**

Cover call→renew→call, graphic→text renewal, two `maketitle` snapshots around renewal, class→preamble→body overrides, unused/unloaded style isolation, non-alphabetical `documentclass`/`usepackage` order, recursive package load/cycle safety, and ordered `input` definitions.

- [ ] **Step 2: Verify RED**

Run: `uv run --project packages/py-core pytest packages/py-core/tests/test_latex_parser.py -k 'source_order or load_order or maketitle_snapshot' -q`

Expected: the current final macro dictionary retroactively changes earlier calls and loads unused styles.

- [ ] **Step 3: Add state and ordered evaluator**

Introduce `_LatexEvaluationState` containing supported definitions, unsupported definitions, frontmatter fields, loaded package/class names, active input stack, and bounded expansion counters. Add `_evaluate_latex_source(name, files, state, emit)` that scans control words/literals once and handles:

```python
if command in {"documentclass", "usepackage", "RequirePackage"}:
    load_declared_sources_in_argument_order(emit=False)
elif command in {"input", "include"}:
    evaluate_resolved_input(emit=emit)
elif supported_definition:
    state.define(parsed_definition)
elif frontmatter_field:
    state.assign_field(evaluate_argument_now())
elif command == "maketitle":
    output += state.render_maketitle_graphics_snapshot()
elif command in state.definitions:
    output += evaluate_instantiated_body_now()
else:
    output += original_command
```

Resolve relative names deterministically, load `.cls`/`.sty` only when declared, preserve declaration order, and use active/loaded sets to terminate cycles. Expand supported macro calls at their call site; pass `{}` to `_LatexParser` so a final dictionary cannot reinterpret earlier content.

- [ ] **Step 4: Preserve ordinary text/table behavior**

Keep standard LaTeX formatting commands in expanded source and flatten visible inline formatting when producing table raw text, so existing prose/caption/heading/table macro tests retain their assertions.

- [ ] **Step 5: Verify GREEN**

Run the new matrix plus existing custom macro, figure, bibliography, and input tests.

### Task 3: Invocation-time fail-closed unsupported structural macros

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`
- Test: `packages/py-core/tests/test_latex_parser.py`

- [ ] **Step 1: Add failing unsupported-definition tests**

Add loaded and unloaded/unused cases for `NewDocumentCommand`, `newrobustcmd`, and `newcommandx`, including nested structural dependencies, renewals, and an argument carrying `includegraphics`. Loaded+invoked structural cases must raise `LatexParseError(kind="unsupported_structural_macro")`; uninvoked/unloaded cases must parse without figures or filename leakage.

- [ ] **Step 2: Verify RED**

Run: `uv run --project packages/py-core pytest packages/py-core/tests/test_latex_parser.py -k unsupported_structural_macro -q`

- [ ] **Step 3: Parse unsupported definition envelopes generically**

Record macro name, argument specification, body, and source order for xparse/etoolbox/newcommandx families without pretending to implement their argument languages. On invocation, traverse the current supported+unsupported dependency graph and invocation argument source. If it can reach `includegraphics`, `maketitle`, sectioning, or an environment-producing structural macro, raise the stable error; otherwise preserve the invocation for normal fallback rendering.

- [ ] **Step 4: Verify GREEN and no path leakage**

Run all unsupported tests and assert the error happens only at evaluated calls outside literal regions.

### Task 4: Full verification and audit

**Files:**
- Verify all changed Python and plan files; do not commit before reviewer approval.

- [ ] **Step 1:** Run focused LaTeX tests, then `packages/py-core/tests`.
- [ ] **Step 2:** Run `apps/worker/tests` to retain COMMIT/fetch/checkpoint coverage.
- [ ] **Step 3:** Run Ruff over every changed Python file and mypy over all changed production source files.
- [ ] **Step 4:** Run `git diff --check`, production hardcode scan, and unbounded-read scan.
- [ ] **Step 5:** Self-review literal/load-order/unsupported graphs, recursion and output bounds, input/package cycles, and ordinary text/table regressions. Keep the worktree uncommitted.
