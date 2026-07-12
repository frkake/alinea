# Generic LaTeX Loaders and Unsupported Arguments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate supported LaTeX loader commands at their source position and prevent unsupported macros from leaking structural single-token arguments into normal evaluation.

**Architecture:** Replace command-specific loader branches with a metadata-driven dispatcher that shares one bounded filename reader while preserving the existing distinction between load-once class/style files and repeatable `input` files. Derive a finite invocation-argument layout from each unsupported definition family and consume exactly that layout with the existing TeX single-token reader before structural reachability analysis.

**Tech Stack:** Python 3.12, dataclasses, regular expressions, pytest, Ruff, mypy

---

### Task 1: Generic source-order loader dispatcher

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`
- Test: `packages/py-core/tests/test_latex_parser.py`

- [ ] **Step 1: Write failing loader tests**

Add parametrized tests for unbraced `input`/`include`, `LoadClass`, `LoadClassWithOptions`, optional `LoadClass` options, and `RequirePackageWithOptions`. Each loaded file defines or renews a macro whose invocation yields one expected figure; unresolved unbraced filenames must not appear in visible text or error messages.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest packages/py-core/tests/test_latex_parser.py -k 'unbraced_input or unbraced_include or loadclass or requirepackagewithoptions' -q
```

Expected: failures show missing figures or leaked loader filenames.

- [ ] **Step 3: Add loader metadata and bounded filename reading**

Define a frozen loader specification with `suffix`, `loaded_once`, `allow_unbraced`, and `allow_multiple`. Map `documentclass`, `LoadClass`, and `LoadClassWithOptions` to `.cls`; map `usepackage`, `RequirePackage`, and `RequirePackageWithOptions` to `.sty`; map `input` and `include` to `.tex`. The reader must skip balanced square options, accept balanced braces, and for input-family commands accept only a bounded unbraced filename token ending at whitespace, braces, brackets, comments, or a control-sequence boundary.

- [ ] **Step 4: Dispatch loaders generically at encounter position**

For load-once class/style sources call `_evaluate_loaded_source`; for repeatable input sources resolve relative names and call `_evaluate_latex_file` with the caller's `emit` value. Consume recognized loader syntax even when resolution fails so filenames cannot become visible body text. Keep `loaded_sources` and `active_sources` as the existing load-once and cycle guards.

- [ ] **Step 5: Verify GREEN and loader regressions**

Run the RED command, then all source/load-order tests. Expected: all pass.

### Task 2: Unsupported macro single-token arguments

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`
- Test: `packages/py-core/tests/test_latex_parser.py`

- [ ] **Step 1: Write failing argument-semantics tests**

Cover `NewDocumentCommand` with one `m`, multiple `m`, optional `O{...}` plus mandatory `m`, and zero arguments. Mandatory arguments must accept `{...}`, a control word, or one character. Structural mandatory arguments must raise `unsupported_structural_macro` without exposing asset filenames; zero-argument definitions must leave the following structural macro untouched.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest packages/py-core/tests/test_latex_parser.py -k 'unsupported_single_token or unsupported_multiple_mandatory or unsupported_optional_mandatory or unsupported_zero_argument' -q
```

Expected: structural single-token cases currently emit ghost figures instead of failing closed.

- [ ] **Step 3: Derive bounded family-specific layouts**

Parse xparse `m` and leading optional argument forms from `argument_spec`; parse etoolbox/newcommandx numeric arity and optional defaults where representable. Return an exact ordered list of optional and mandatory arguments, an empty list for exact zero arity, and an unknown result for unsupported syntax.

- [ ] **Step 4: Consume exactly the derived invocation layout**

Use `_read_square` for present optional arguments and `_read_macro_argument` for each mandatory argument so braces, control words, and one-character tokens are consumed. Feed all consumed argument source to `_source_reaches_document_structure`; never consume a following token for exact zero arity. For an unknown layout, preserve bounded grouped consumption and fail closed if a probed argument can reach document structure.

- [ ] **Step 5: Verify GREEN and unsupported-macro regressions**

Run the RED command and all unsupported structural macro tests. Expected: all pass with generic error messages.

### Task 3: Full verification and self-review

**Files:**
- Verify only; no commits are permitted for this review cycle.

- [ ] **Step 1: Run focused and full suites**

Run the full LaTeX parser file, py-core suite, focused worker LaTeX pipeline suite, and full worker suite. Expected: no failures beyond existing skips.

- [ ] **Step 2: Run static and repository checks**

Run Ruff, mypy on changed production files, `git diff --check`, forbidden unbounded-read scans, and production-line scans for paper IDs, fixture filenames, and fixture titles. Expected: all clean.

- [ ] **Step 3: Self-review failure boundaries**

Confirm source-position ordering, relative paths, extensions, class/style loaded-once behavior, repeatable input behavior, cycles, unresolved filename suppression, exact zero-argument behavior, bounded consumption, literal exclusion, and generic filename-free errors. Leave all changes uncommitted and return exact evidence to the reviewer.
