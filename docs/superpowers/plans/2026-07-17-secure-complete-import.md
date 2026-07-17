# Secure Complete Backup Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make full-data import safe for untrusted ZIPs and restore every exported user-visible category without data loss.

**Architecture:** Keep the archive format compatible, but treat both `data.json` and `manifest.json` as untrusted. The importer builds an allowlist of asset references from restored metadata, assigns destination-scoped storage keys, and only then writes verified members. A ZIP-validation helper owns resource limits; the API owns bounded upload reading; serializers/importers own round-trip fidelity.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL, S3-compatible storage, Python `zipfile`, pytest, ruff, mypy, pnpm/turbo.

---

### Task 1: Bound the HTTP upload before storage

**Files:**
- Modify: `apps/api/src/alinea_api/routers/export.py:18-57,519-550`
- Modify: `apps/api/tests/test_import_api.py:80-150`

- [ ] **Step 1: Write the failing API tests**

```python
async def test_import_full_rejects_upload_larger_than_limit(auth: tuple[AsyncClient, str]) -> None:
    client, _ = auth
    oversized = b"x" * (_MAX_IMPORT_ARCHIVE_BYTES + 1)
    response = await client.post(
        "/api/import/full",
        files={"file": ("backup.zip", oversized, "application/zip")},
    )
    assert response.status_code == 413

async def test_import_full_accepts_zip_within_limit(auth: tuple[AsyncClient, str]) -> None:
    client, _ = auth
    response = await client.post(
        "/api/import/full",
        files={"file": ("backup.zip", _make_fake_zip(), "application/zip")},
    )
    assert response.status_code == 202
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest apps/api/tests/test_import_api.py -k 'larger_than_limit or within_limit' -v`

Expected: the oversized test fails because `start_import_full` currently calls `file.read()` with no bound.

- [ ] **Step 3: Write the minimal implementation**

```python
_MAX_IMPORT_ARCHIVE_BYTES = 100 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024

async def _read_limited_import_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK_BYTES):
        total += len(chunk)
        if total > _MAX_IMPORT_ARCHIVE_BYTES:
            raise ProblemException("payload_too_large")
        chunks.append(chunk)
    return b"".join(chunks)
```

Replace `await file.read()` with this helper. Do not create the S3 object or job before it returns.

- [ ] **Step 4: Run the API import tests**

Run: `uv run --no-sync pytest apps/api/tests/test_import_api.py -v`

Expected: all tests pass, including the new 413 regression test.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/alinea_api/routers/export.py apps/api/tests/test_import_api.py
git commit -m "fix(import): bound archive uploads before storage"
```

### Task 2: Validate ZIP metadata before reading members

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py:32-85,803-865`
- Modify: `apps/worker/tests/test_import_bulk.py:324-360`

- [ ] **Step 1: Write failing ZIP-resource-limit tests**

```python
async def test_import_job_rejects_zip_with_excessive_uncompressed_size(
    db_session: AsyncSession,
) -> None:
    archive = _archive_with_declared_member_size("data.json", _MAX_ZIP_MEMBER_BYTES + 1)
    done = await _run_archive(db_session, archive)
    assert done.status in ("queued", "failed")
    assert "import_bad_archive" in str(done.error)

async def test_import_job_rejects_manifest_asset_not_referenced_by_payload(
    db_session: AsyncSession,
) -> None:
    archive = _archive_with_unreferenced_asset("assets/foreign/key.pdf")
    done = await _run_archive(db_session, archive)
    assert done.status in ("queued", "failed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest apps/worker/tests/test_import_bulk.py -k 'excessive_uncompressed or unreferenced_asset' -v`

Expected: failure because the current implementation has no `ZipInfo` limits or member allowlist.

- [ ] **Step 3: Write the minimal validator**

```python
_MAX_ZIP_ENTRIES = 2_000
_MAX_ZIP_MEMBER_BYTES = 100 * 1024 * 1024
_MAX_ZIP_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_MAX_ZIP_COMPRESSION_RATIO = 100

def _validated_members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > _MAX_ZIP_ENTRIES:
        raise ValueError("too_many_zip_entries")
    total = 0
    members: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if info.is_dir() or info.filename.startswith("/") or ".." in info.filename.split("/"):
            raise ValueError("unsafe_zip_member")
        if info.file_size > _MAX_ZIP_MEMBER_BYTES:
            raise ValueError("zip_member_too_large")
        if info.compress_size and info.file_size / info.compress_size > _MAX_ZIP_COMPRESSION_RATIO:
            raise ValueError("zip_compression_ratio_exceeded")
        total += info.file_size
        if total > _MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError("zip_uncompressed_total_too_large")
        members[info.filename] = info
    return members
```

Require exactly one `manifest.json` and `data.json`; read only validated members. Reject malformed metadata before `import_data_json` or an S3 write.

- [ ] **Step 4: Run the worker archive tests**

Run: `uv run --no-sync pytest apps/worker/tests/test_import_bulk.py -k 'invalid_schema or excessive_uncompressed or unreferenced_asset' -v`

Expected: all selected archive-rejection tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/alinea_worker/tasks/import_user_data.py apps/worker/tests/test_import_bulk.py
git commit -m "fix(import): validate zip resource limits before extraction"
```

### Task 3: Restore missing backup metadata

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py:76-833`
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py:43-800`
- Modify: `apps/worker/tests/test_export_bulk.py:270-355`
- Modify: `apps/worker/tests/test_import_bulk.py:81-185`

- [ ] **Step 1: Write the failing complete-round-trip test**

```python
async def test_import_restores_settings_figures_candidates_and_latest_revision(
    db_session: AsyncSession,
) -> None:
    source = await _seed_complete_export_data(db_session)
    payload = await _detached_payload(db_session, source["user_id"])
    await _delete_source_user(db_session, source["user_id"])
    target = await _make_user(db_session)

    await import_data_json(db_session, target["user_id"], payload)

    restored_user = await db_session.get(User, target["user_id"])
    assert restored_user.settings == payload["settings"]
    assert await _count_rows(db_session, OverviewFigure) == len(payload["overview_figures"])
    assert await _count_rows(db_session, ExplainerFigure) == len(payload["explainer_figures"])
    assert await _count_rows(db_session, VocabCandidate) == len(payload["vocab_candidates"])
    assert (await _target_paper(db_session, target["user_id"])).latest_revision_id is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest apps/worker/tests/test_import_bulk.py -k 'settings_figures_candidates_and_latest_revision' -v`

Expected: failure on missing candidate/figure/settings restoration.

- [ ] **Step 3: Serialize the missing fields and models**

Add `latest_revision_id` to each exported library paper record, add `_serialize_vocab_candidates` scoped through the user’s library items, and retain all persisted fields required by existing model rows. Keep `byok_api_keys` and credentials excluded.

```python
"latest_revision_id": str(paper.latest_revision_id) if paper.latest_revision_id else None,
"vocab_candidates": await _serialize_vocab_candidates(session, library_item_ids),
```

- [ ] **Step 4: Restore in dependency order**

Restore normal user settings after loading the target user; restore candidates after vocab entries; restore figures after articles. Keep explicit old-to-new maps for papers, library items, articles, and vocab entries. Set `Paper.latest_revision_id` only when its mapped revision belongs to that paper.

```python
await imp.restore_user_settings(data.get("settings"))
await imp.restore_overview_figures(data.get("overview_figures") or [])
await imp.restore_explainer_figures(data.get("explainer_figures") or [])
await imp.restore_vocab_candidates(data.get("vocab_candidates") or [])
await imp.restore_latest_revisions(library)
```

- [ ] **Step 5: Run complete export/import tests**

Run: `uv run --no-sync pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py -v`

Expected: all round-trip tests pass and retain the new categories.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/alinea_worker/tasks/export_user_data.py \
  apps/worker/src/alinea_worker/tasks/import_user_data.py \
  apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py
git commit -m "fix(import): restore complete backup metadata"
```

### Task 4: Re-key and allowlist restored assets

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py:803-865`
- Modify: `apps/worker/tests/test_import_bulk.py:191-234`

- [ ] **Step 1: Write the failing S3-overwrite regression test**

```python
async def test_import_rekeys_assets_and_never_overwrites_manifest_key(
    db_session: AsyncSession,
) -> None:
    storage = S3Storage()
    foreign_key = "sources/foreign/private/original.pdf"
    await storage.put(storage.sources_bucket, foreign_key, b"protected")
    archive = await _archive_claiming_source_key(db_session, foreign_key, b"attacker bytes")

    await _run_archive(db_session, archive)

    assert await storage.get(storage.sources_bucket, foreign_key) == b"protected"
    restored = await _restored_source_asset(db_session)
    assert restored.storage_key != foreign_key
    assert await storage.get(storage.sources_bucket, restored.storage_key) == b"attacker bytes"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest apps/worker/tests/test_import_bulk.py -k rekeys_assets -v`

Expected: the foreign object is overwritten before the fix.

- [ ] **Step 3: Implement the destination asset map**

Build an allowlist from imported `source_assets`, `overview_figures`, and `explainer_figures`, keyed by their old storage key and expected logical bucket. Generate a destination key from target user, asset kind, and restored entity ID; store that key in the restored DB row. Ignore `manifest.bucket`, and reject manifest records outside the allowlist.

```python
def _restored_asset_key(user_id: str, kind: str, entity_id: str, source_key: str) -> str:
    suffix = Path(source_key).suffix.lower()
    safe_suffix = suffix if len(suffix) <= 12 else ""
    return f"imports/restored/{user_id}/{kind}/{entity_id}{safe_suffix}"
```

Make the importer return `old_key -> (bucket, destination_key)`. After SHA-256 verification, the job writes only the mapped destination key.

- [ ] **Step 4: Run security and normal round-trip tests**

Run: `uv run --no-sync pytest apps/worker/tests/test_import_bulk.py -k 'rekeys_assets or roundtrip_restores_assets' -v`

Expected: the protected object stays unchanged and restored metadata points to the copied asset.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/alinea_worker/tasks/import_user_data.py apps/worker/tests/test_import_bulk.py
git commit -m "fix(import): isolate restored asset storage keys"
```

### Task 5: Restore the verification gate

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py:814-819`
- Modify: `apps/api/src/alinea_api/routers/export.py:499-501`
- Modify: `apps/worker/tests/test_easy_reason.py:1-6`
- Modify: `apps/worker/tests/test_import_bulk.py:38-67`
- Modify: `packages/py-core/tests/test_official_repo_detection.py:7-10`
- Modify: `packages/py-core/tests/translation/test_easy_prompt.py:1-4`

- [ ] **Step 1: Write the failing missing-upload-key test**

```python
async def test_import_job_retries_when_payload_has_no_upload_key(db_session: AsyncSession) -> None:
    done = await _run_job(db_session, payload={})
    assert done.status in ("queued", "failed")
    assert "import_bad_payload" in str(done.error)
```

- [ ] **Step 2: Run the test to verify the current optional key flow is invalid**

Run: `uv run --no-sync pytest apps/worker/tests/test_import_bulk.py -k missing_upload_key -v`

Expected: failure before the type-safe explicit payload check exists.

- [ ] **Step 3: Apply minimal type and lint fixes**

```python
upload_key = (job.payload or {}).get("upload_key")
if not isinstance(upload_key, str) or not upload_key:
    await store.fail_with_retry(str(job.id), {"code": "import_bad_payload"})
    return
archive = await storage.get(storage.assets_bucket, upload_key)
```

Replace bare `dict` annotations with `dict[str, Any]`, add the missing `-> None` test annotation, remove unused imports, and format new test modules with ruff.

- [ ] **Step 4: Run final verification**

Run:

```bash
uv run --no-sync pytest packages/py-core/tests packages/llm/tests
uv run --no-sync pytest apps/api/tests/test_import_api.py apps/api/tests/test_standalone_export.py apps/api/tests/test_standalone_html.py apps/api/tests/test_vocab_candidates.py apps/api/tests/test_easy_style.py apps/api/tests/test_revision_diff.py apps/api/tests/test_settings_api.py apps/api/tests/test_chat.py
uv run --no-sync pytest apps/worker/tests/test_export_bulk.py apps/worker/tests/test_import_bulk.py apps/worker/tests/test_extract_vocab_candidates.py apps/worker/tests/test_easy_reason.py
uv run --no-sync ruff check $(git diff --name-only main...HEAD -- '*.py')
pnpm turbo build lint typecheck --force
(cd apps/api && uv run --no-sync alembic heads)
```

Expected: every command exits 0 and Alembic prints only `0011_easy_translation_style (head)`.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/alinea_worker/tasks/import_user_data.py apps/api/src/alinea_api/routers/export.py \
  apps/worker/tests/test_easy_reason.py apps/worker/tests/test_import_bulk.py \
  packages/py-core/tests/test_official_repo_detection.py packages/py-core/tests/translation/test_easy_prompt.py
git commit -m "fix: restore import verification gate"
```

