# Figure Isolation and Atomicity Hardening Design

Date: 2026-07-11
Status: Approved
Scope: Generic arXiv figure materialization, SVG sanitization, thumbnails, and S3 staging

## Goals

- Decode and render every untrusted figure and thumbnail only in a disposable spawn child with Linux resource limits and a wall deadline.
- Bound all figure work by one document-wide deadline rather than multiplying per-item timeouts.
- Preserve common safe SVG semantics without treating arbitrary attributes as CSS or permitting active/external content.
- Retain a valid HTML image fallback when an inline SVG fails validation.
- Publish revision-specific immutable thumbnail objects only after the database transaction succeeds, and clean every newly staged key after any failure or cancellation.

No decision may depend on an arXiv ID, title, author, or paper-specific source fragment.

## Architecture

### Disposable process supervisor

One spawn-based supervisor owns process start, Linux `CPU`/`AS`/`FSIZE`/`NOFILE` limits, wall timeout, termination, kill, join, and lifecycle diagnostics. Figure and thumbnail workers use this supervisor. The parent performs only byte-size and magic checks; Pillow and document renderers run in the child. Unknown magic fails with `unsupported_figure_format` before Pillow is called.

Production pipeline code calls only asynchronous isolated APIs. Synchronous decode/render helpers are named and documented as trusted child/test boundaries and are not exported as normal production entry points. Raster limits are 25 million aggregate decoded pixels, 12,000 pixels per dimension, and 128 frames; child virtual memory is capped at 512 MiB.

A `MaterializationDeadline` created at structuring start exposes the remaining document budget. Each fetch, figure child, and thumbnail child receives `min(operation_limit, remaining)`. Exhaustion produces `materialization_timeout` without starting more work.

### SVG semantic sanitizer

SVG XML is parsed with byte, element, depth, and text limits. Active elements and attributes are rejected. `href`/`src` must be internal fragments. CSS validation runs only for `style` and known presentation attributes. Geometry/path/transform fields use restricted syntax; `id`, `class`, `role`, `aria-*`, and XML language/space use bounded inert text validation. Unknown/data attributes and non-SVG metadata namespace elements are removed. The sanitized tree is serialized and only those bytes reach the renderer.

The HTML parser may store both inline SVG raw and an image fallback source in the transient parsed model. The pipeline tries SVG first and fetches the image only if SVG materialization fails. Whether either path succeeds or fails, author raw is cleared before revision serialization.

### Immutable S3 staging

Figure and thumbnail keys are staged under revision-specific immutable paths. Thumbnail base and retina keys are siblings under `thumbnails/{paper_id}/{revision_id}/`. From the first successful PUT through search indexing, thumbnail generation, and DB commit, a single `try/except BaseException` tracks new keys. Cleanup is shielded, best effort, and never replaces the original exception.

Existing paper thumbnails are never overwritten or deleted during failed reingest. On success, `paper.thumbnail_key` switches to the new base key in the same DB commit. Library deletion derives a retina sibling only from a strictly parsed current thumbnail key and also retains legacy-prefix cleanup.

Superseded revision and legacy thumbnail pairs remain under the same paper-owned prefix as compatibility/history assets. This avoids breaking older `LibraryItem.thumbnail_key` pointers; deterministic revision paths keep them attributable, and Paper deletion removes the whole thumbnail prefix. A failed reingest restores the in-memory old pointer and never deletes that old pair.

## Failure codes

- `unsupported_figure_format`: unknown magic before decoder entry.
- `image_too_large`: dimension, frame, or aggregate decoded-pixel limit.
- `conversion_timeout` / `thumbnail_timeout`: child wall timeout.
- `conversion_lifecycle` / `thumbnail_lifecycle`: child cannot be killed and reaped deterministically.
- `materialization_timeout`: document deadline exhausted.
- Existing `unsafe_vector`, `asset_too_large`, and cleanup behavior remain stable.

## Test strategy

Twenty minimal RED reproductions cover: all-format isolation, unknown magic, pixel/frame/AS limits, thumbnail isolation, document deadline, process leaks/lifecycle, trusted-only boundaries; safe SVG semantics and representative Matplotlib/Inkscape/arXiv structures; mixed SVG/image fallback; cleanup after index, thumbnail PUT, cancellation, and commit failures; immutable thumbnail publication; and strict retina sibling deletion.

Existing figure/parser/pipeline integration suites, Ruff, formatting, mypy, web typecheck, and viewer tests remain required before the single implementation commit.
