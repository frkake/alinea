# Bounded tar metadata and xz decoding implementation plan

> **For agentic workers:** Execute inline with test-driven development. The task owner explicitly requires no commit.

**Goal:** Reject hostile tar extension metadata and oversized xz decoder dictionaries before recursive/object/memory amplification while preserving legal tar, PAX/GNU long names, concatenated xz, and raw/gzip/bzip2 single-TeX compatibility.

**Architecture:** A `TarFile`/`TarInfo` boundary counts every raw header before `tarfile` processes it. The bounded `TarInfo` implements the CPython 3.12 PAX path with a forward, capped record scanner and rejects sparse/negative-size metadata before expansion. Forward-only gzip, bzip2, and xz readers share a concatenated-stream cap; xz additionally creates each `LZMADecompressor(FORMAT_AUTO, memlimit=...)` under a decoder-memory cap.

**Tech stack:** Python 3.12 stdlib (`tarfile`, `lzma`, `io`), pytest, Ruff, mypy.

---

### Task 1: Lock hostile metadata and compatibility contracts

**Files:**
- Modify: `packages/py-core/tests/test_latex_parser.py`

- [ ] Add a helper that builds checksum-valid raw PAX/GNU extension headers without relying on `tarfile`'s writer normalization.
- [ ] Add RED tests for 500 consecutive local PAX headers and consecutive GNU long-name headers. Assert `LatexParseError.kind == "invalid_archive"`, never raw `RecursionError`.
- [ ] Add RED tests for framed `GNU.sparse.size=abc`, a small valid `GNU.sparse.map`, a large repeated sparse map, and old GNU sparse typeflag. Assert rejection before `tarfile` sparse conversion.
- [ ] Add RED tests for per-header PAX bytes, cumulative PAX bytes, per-header/cumulative record counts, total raw headers, extension header count, and extension recursion depth using monkeypatched small limits.
- [ ] Add compatibility tests for local/global PAX path metadata, consecutive PAX then normal member, GNU long name/link, multiple normal members after extensions, and payload sizes at exact 512-byte padding boundaries.
- [ ] Run the focused selection and record expected raw `RecursionError`, `ValueError`, or missing-limit failures before production edits.

### Task 2: Bound tar metadata before stdlib expansion

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`

- [ ] Add deterministic constants: raw header cap, extension header cap, extension depth cap, per-PAX byte/record caps, and cumulative PAX byte/record caps.
- [ ] Add a per-archive metadata budget owned by a private `TarFile` subclass. Initialize it before `TarFile.__init__` calls `next()` and force the bounded `TarInfo` class.
- [ ] Override `TarInfo.fromtarfile()` using the four-line CPython 3.12 flow: read 512 bytes, call `frombuf`, assign offset, charge/check the parsed raw header, then call `_proc_member`. Use `try/finally` to decrement recursion depth.
- [ ] Override `_proc_pax()` with the CPython 3.12 semantics for global/local headers, charset decoding, recursive next-header processing, PAX field application, and size-offset recalculation. Replace the unbounded record loop with a forward scanner that caps digits, framing, bytes, and object count before appending records.
- [ ] Reject any `GNU.sparse.*` PAX key and `GNUTYPE_SPARSE` header before stdlib sparse handling. Reject oversized GNU long-name/link payloads before `_proc_gnulong` reads them.
- [ ] Reject negative sizes immediately after raw-header parsing, after local/global PAX application, and defensively in the yielded-member loop.
- [ ] Open archives with the bounded `TarFile` subclass. Preserve custom `LatexParseError`; normalize `tarfile.TarError`, `ValueError`, `OverflowError`, and fallback `RecursionError` to `invalid_archive`. Do not catch `MemoryError`, `KeyboardInterrupt`, or `SystemExit`.
- [ ] Run all Task 1 tests and the pre-existing archive/long-name/exact-EOF suite.

### Task 3: Add bounded concatenated compression readers

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`
- Modify: `packages/py-core/tests/test_latex_parser.py`

- [ ] Add RED tests that mutate a valid xz LZMA2 property to request a 4 GiB dictionary and repair the block-header CRC. Assert stable `invalid_archive`; where supported, run a subprocess with a bounded address space and assert no multi-GiB virtual-memory reservation.
- [ ] Add tests for normal gzip/bzip2/xz tar, concatenated streams whose decompressed bytes form one tar stream, `unused_data`, trailing zero/junk compatibility, CRC/checksum failures, truncated streams, and exact/+1 stream-count boundaries including thousands of empty streams.
- [ ] Implement a forward `io.RawIOBase` reader around `LZMADecompressor(format=FORMAT_AUTO, memlimit=128 MiB)`. Read compressed input in fixed chunks, always pass caller `max_length`, create a fresh limited decoder for each xz stream, consume `unused_data` before reading more input, and raise `EOFError` for an unfinished stream.
- [ ] Replace `GzipFile` and `BZ2File` with equivalent forward `zlib.decompressobj(wbits=31)` and `BZ2Decompressor` readers. Charge the shared stream budget before every new decoder and reuse the gzip reader for the 32 MiB single-source fallback.
- [ ] Preserve stdlib trailing-data behavior only for non-xz bytes after at least one complete stream; a following xz magic that fails decoding remains `invalid_archive`.
- [ ] Add RED matrices for XZ stream padding: padding-only EOF and split tar streams with 0/4/8/12 bytes succeed; 1/2/3/5 bytes fail; partial magic and a full magic after padding are distinguished across compressed-input chunk boundaries.
- [ ] Implement a forward XZ padding scanner that retains only the current chunk, counts leading zero bytes across reads, requires a four-byte multiple before EOF/next data, and completes partial XZ magic before deciding whether bytes are a next stream or ignored trailing junk.
- [ ] Charge the shared stream cap and construct the memlimited decoder only after valid padding and complete XZ magic. Verify exactly 1,024 padded streams succeed, stream 1,025 fails, and a padded second 4 GiB-dictionary stream is rejected by the decoder memory limit.
- [ ] Replace `LZMAFile` in the decompressor context with this reader and rerun all xz/archive tests.

### Task 4: Preserve BZh-prefixed raw TeX fallback

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`
- Modify: `packages/py-core/tests/test_latex_parser.py`

- [ ] Add RED raw-source tests beginning with ASCII `BZh` for UTF-8 and Latin-1 TeX, plus a checksum-valid raw tar whose first filename begins `BZh`.
- [ ] Add corrupt bzip2 controls proving binary compressed input still yields `invalid_archive`.
- [ ] Before compression magic detection, prefer a bounded checksum-valid raw tar header. On a bzip2 probe failure, permit raw fallback only when the original bytes pass the generic text-control gate and decode to text containing `\\documentclass`; never use a paper identifier.
- [ ] Rerun all single-file fallback and corruption tests.

### Task 5: Independent review and full verification

**Files:**
- Verify: all changed Python and test files

- [ ] Fuzz raw/gzip/bzip2/xz header mutations, truncation points, PAX framing, sparse keys, concatenated xz boundaries, and xz dictionary properties. Confirm no stdlib exception escapes.
- [ ] Request the existing independent tar reviewer to audit caps-before-processing, CPython compatibility, xz memory bounds, exception taxonomy, and fallback behavior without editing.
- [ ] Run `uv run pytest packages/py-core/tests/test_latex_parser.py -q`.
- [ ] Run `uv run pytest packages/py-core/tests -q`.
- [ ] Run the focused worker suite and `uv run pytest apps/worker/tests -q`.
- [ ] Run Ruff, mypy, and `git diff --check` over py-core and worker.
- [ ] Report exact counts and warnings. Do not commit.
