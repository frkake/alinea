# Final Figure Safety Hardening Design

## Goal

Close the remaining generic safety gaps in SVG sanitization, cancellation cleanup, and PDF-extracted figure publication without adding paper-specific cases or trusting renderer behavior.

## Safety boundaries

1. Author-controlled SVG is parsed and rebuilt before any renderer receives it. Numeric semantics are finite, transform functions have valid arity, active CSS is rejected, and unknown inert CSS is removed.
2. Cancellation does not end an ingest coroutine until its owned cleanup work and isolated child supervisor have reached a terminal state. Cleanup is bounded and task outcomes are always retrieved.
3. Every PDF-extracted figure passes through the same isolated materialization boundary as LaTeX and HTML figures. S3 receives only validated payload bytes and payload-derived metadata.

## SVG numeric and transform semantics

Transform attributes use a complete token parse rather than a permissive character-class match. Every argument must be a syntactically complete finite number, and each function accepts only its SVG arities: `matrix(6)`, `translate(1|2)`, `scale(1|2)`, `rotate(1|3)`, and `skewX/skewY(1)`.

CSS values retain the existing dangerous-content precheck and balanced-function checks. A lexical finite-number pass then recognizes numbers only at CSS token boundaries, separates an optional unit from the numeric portion, and rejects non-finite values. Quoted strings, `url(...)` targets, hash colors/fragments, and digits embedded in identifiers are not numeric tokens.

## CSS sanitization and rebuilding

Style attributes and style elements are rebuilt from parsed declarations. Known presentation properties are validated and serialized; unknown inert properties are omitted without rejecting the SVG. The dangerous-content precheck runs over the original declaration text before omission, so dropping unknown declarations cannot hide an active construct.

Known properties containing external references remain rejected. An external URL that occurs only in an unknown inert declaration is removed with that declaration and never reaches the renderer. Selectors remain restricted to the existing passive subset. Empty sanitized style attributes are removed, and empty style rules may serialize to no rule.

## Cancellation completion

Revision staging owns its deletion task. On failure or cancellation it waits through repeated cancellation requests until deletion completes, fails, or reaches a bounded cleanup deadline. At the deadline it cancels and drains the deletion task; cleanup errors are logged but never replace the original failure.

Each isolated conversion owns a cooperative thread cancellation event. The supervisor polls that event alongside its process pipe, terminates/kills/reaps the child, and only then lets the async wrapper re-raise the original `CancelledError`. The async wrapper shields and drains the thread task through repeated cancellation requests. Figure, thumbnail, and HTML-fetch materialization all use this same boundary.

## PDF-extracted figures

`_structure_pdf` creates one document materialization deadline and passes it to both `_save_pdf_assets` and thumbnail generation. `_save_pdf_assets` gives every extracted byte string to `_materialize_figure_payload`, applies the per-input and isolated-output limits, and counts both source and canonical output toward the aggregate budget. It publishes `payload.content` using `payload.ext` and `payload.content_type`; malformed, oversized, timed-out, or deadline-exhausted figures remain unset and produce structured failures.

## Verification

Tests cover renderer-before rejection, safe lexer boundaries, style rebuilding, repeated cancellation and cleanup failure/deadline paths, child/task reaping through figure/thumbnail/fetch calls, and PDF validation/metadata/budget/deadline behavior. The existing parser, ingest, PDF upload, API, static Python, and web viewer suites remain required.
