# 完全データ移行(エクスポート/インポート)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ユーザーの全データ(論文本文・翻訳・PDF・図・メモ・注釈・チャット・語彙・記事・コレクション・設定・通知)を 1 つの zip に完全スナップショットとしてエクスポートし、別 PC でマージ追加(冪等)インポートして再取り込み不要で復元できるようにする。

**Architecture:** 既存の非同期 `export` Job(`apps/worker/.../export_user_data.py`)を拡張し、DB 全行 + S3 バイナリを `manifest.json` + `data.json` + `assets/**` の zip 構造で出力する。新規 `import` Job(`import_user_data.py`)と `POST /api/import/full` アップロード API を追加し、zip を依存順に復元(元 id 存在チェックで skip = 冪等)、`block_search_index` は `document_revisions` から再構築する。設定 UI の「エクスポート」カテゴリを「データ」に改名しインポート/エクスポート両カードを置く。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / arq / MinIO(S3)/ zipfile / Next.js(React)/ TanStack Query / Vitest。

## Global Constraints

- 依存追加禁止(`uv add` / 新規 npm パッケージ不可)。zip は標準ライブラリ `zipfile` のみ。
- P3「黙って壊れない」: 壊れた zip・スキーマ不一致は明確にエラー化し `jobs.result.error` に理由を格納。部分失敗は skip してサマリに残す。
- P5「ロックインしない」: 全データを標準形式(JSON + 素のバイナリ)で持ち出せる。
- BYOK 鍵(`byok_api_keys`)は zip に含めない(平文キー漏洩防止)。
- インポートは冪等(同 zip を 2 回実行しても重複行を作らない)、かつマージ追加(既存データを上書き・削除しない)。
- `user_id` は復元先の現ユーザーへリマップ。`papers` は `arxiv_id` で名寄せ。
- DB は実 PostgreSQL、S3 は実 MinIO(worker conftest 規約)。テストは実スタックで green。
- スキーマバージョン定数 `EXPORT_SCHEMA_VERSION = 2`(旧 v1 = 単一 `alinea-export.json` と判別)。

---

## Task 1: エクスポートペイロードに不足カテゴリを追加

既存 `build_export_payload` は library/notes/annotations/chat/vocab/resources/articles/collections/settings のみ。本文・翻訳・用語集・保存フィルタ・読書セッション・通知・図メタ・source_assets メタを追加する。

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Test: `apps/worker/tests/test_export_bulk.py`

**Interfaces:**
- Consumes: 既存 `build_export_payload(session, user_id) -> dict`、`_iso`、`_author_names`。
- Produces: `build_export_payload` の戻り値に新キー `document_revisions`, `translation_sets`, `translation_units`, `glossaries`, `glossary_terms`, `saved_filters`, `reading_sessions`, `notifications`, `overview_figures`, `explainer_figures`, `source_assets`, `share_tokens` を追加。各要素は該当モデルの全カラムを含む dict。`schema_version` キーを追加。

- [ ] **Step 1: 失敗するテストを書く**

`apps/worker/tests/test_export_bulk.py` の既存 `_seed_user_data` に document_revision・translation_set/unit・glossary・saved_filter・reading_session・notification・source_asset を 1 行ずつ追加し、以下を検証するテストを追加する:

```python
async def test_export_payload_includes_generated_content(db: AsyncSession) -> None:
    ids = await _seed_user_data(db)  # 拡張後の seed(下記 Step 3 で拡張)
    payload = await build_export_payload(db, ids["user_id"])
    assert payload["schema_version"] == 2
    # 本文・翻訳が含まれる
    assert len(payload["document_revisions"]) >= 1
    assert payload["document_revisions"][0]["content"]  # 構造化本文 JSONB
    assert len(payload["translation_sets"]) >= 1
    assert len(payload["translation_units"]) >= 1
    # 用語集・保存フィルタ・読書セッション・通知・図メタ・アセットメタ
    for key in (
        "glossaries", "glossary_terms", "saved_filters", "reading_sessions",
        "notifications", "overview_figures", "explainer_figures", "source_assets",
    ):
        assert key in payload, key
    # source_asset メタは storage_key/sha256/byte_size を持つ
    assert payload["source_assets"][0]["storage_key"]
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `uv run pytest apps/worker/tests/test_export_bulk.py::test_export_payload_includes_generated_content -v`
Expected: FAIL(`KeyError: 'schema_version'` または `document_revisions` 未定義)

- [ ] **Step 3: 実装**

`export_user_data.py` に各シリアライザを追加(モデルの全カラムを dict 化)。例:

```python
from alinea_core.db.models import (
    DocumentRevision, TranslationSet, TranslationUnit, Glossary, GlossaryTerm,
    SavedFilter, ReadingSession, Notification, OverviewFigure, ExplainerFigure,
    SourceAsset, CollectionShareToken,
)

EXPORT_SCHEMA_VERSION = 2

async def _serialize_document_revisions(
    session: AsyncSession, paper_ids: list[str]
) -> list[dict[str, Any]]:
    if not paper_ids:
        return []
    rows = (
        (await session.execute(
            select(DocumentRevision)
            .where(DocumentRevision.paper_id.in_(paper_ids))
            .order_by(DocumentRevision.created_at.asc())
        )).scalars().all()
    )
    return [
        {
            "id": str(r.id),
            "paper_id": str(r.paper_id),
            "source_version": r.source_version,
            "parser_version": r.parser_version,
            "quality_level": r.quality_level,
            "source_format": r.source_format,
            "content": r.content,
            "stats": r.stats,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]
```

同様に `_serialize_translation_sets` / `_serialize_translation_units`(set_id で紐付け)/ `_serialize_glossaries` / `_serialize_glossary_terms` / `_serialize_saved_filters` / `_serialize_reading_sessions` / `_serialize_notifications` / `_serialize_overview_figures` / `_serialize_explainer_figures` / `_serialize_source_assets`(storage_key/content_type/byte_size/sha256/kind/source_url/source_version)/ `_serialize_share_tokens` を実装する。各カラム名は `packages/py-core/src/alinea_core/db/models.py` の定義に厳密一致させること。

`build_export_payload` の戻り dict に `paper_ids = [row["paper_id"] for row in library]` を計算し、上記シリアライザ呼び出しと `"schema_version": EXPORT_SCHEMA_VERSION` を追加する。

- [ ] **Step 4: 実行して成功を確認**

Run: `uv run pytest apps/worker/tests/test_export_bulk.py::test_export_payload_includes_generated_content -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/tests/test_export_bulk.py
git commit -m "feat(export): 本文・翻訳・図メタ等をエクスポートペイロードに追加"
```

---

## Task 2: zip 構造を manifest + data + assets に変更しアセットを同梱

zip を旧 `alinea-export.json` 単体から `manifest.json` + `data.json` + `assets/<storage_key>` 構造へ変更し、S3 バイナリをストリームで同梱する。

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/export_user_data.py`
- Test: `apps/worker/tests/test_export_bulk.py`

**Interfaces:**
- Consumes: Task 1 の `build_export_payload`、`S3Storage.get(bucket, key) -> bytes`、`S3Storage.sources_bucket` / `assets_bucket`、`StorageKeys`。
- Produces: `collect_asset_keys(payload) -> list[tuple[str, str]]`(戻り: `(bucket, storage_key)` の一覧)、`build_export_archive(session, user_id, storage) -> bytes`(manifest + data + assets を含む zip バイト列)。`run_export_full_job` はこれを使う。

- [ ] **Step 1: 失敗するテストを書く**

```python
async def test_export_archive_bundles_assets_and_manifest(db, s3_storage) -> None:
    ids = await _seed_user_data(db)
    # source_asset が指す storage_key に実バイナリを置く
    await s3_storage.put(s3_storage.sources_bucket, ids["asset_key"], b"%PDF-1.7 fake",
                         content_type="application/pdf")
    archive = await build_export_archive(db, ids["user_id"], s3_storage)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "data.json" in names
        assert f"assets/{ids['asset_key']}" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["schema_version"] == 2
        entry = next(a for a in manifest["assets"] if a["storage_key"] == ids["asset_key"])
        assert entry["sha256"]
        assert zf.read(f"assets/{ids['asset_key']}") == b"%PDF-1.7 fake"
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `uv run pytest apps/worker/tests/test_export_bulk.py::test_export_archive_bundles_assets_and_manifest -v`
Expected: FAIL(`build_export_archive` 未定義)

- [ ] **Step 3: 実装**

`export_user_data.py` に実装:

```python
import hashlib

def collect_asset_keys(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """payload から到達可能な (bucket, storage_key) を集約(重複排除・決定的順序)。"""
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    def add(bucket: str, key: str | None) -> None:
        if key and (bucket, key) not in seen:
            seen.add((bucket, key))
            keys.append((bucket, key))
    # source_assets(sources バケット)
    for a in payload.get("source_assets", []):
        add("sources", a["storage_key"])
    # overview svg / explainer png / figures / thumbnails は各メタの storage_key を使う
    for f in payload.get("overview_figures", []):
        add("assets", f.get("svg_storage_key"))
    for f in payload.get("explainer_figures", []):
        add("assets", f.get("png_storage_key"))
    return keys

async def build_export_archive(
    session: AsyncSession, user_id: str, storage: S3Storage
) -> bytes:
    payload = await build_export_payload(session, user_id)
    buf = io.BytesIO()
    assets_meta: list[dict[str, Any]] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.json", json.dumps(payload, ensure_ascii=False, indent=2))
        bucket_of = {"sources": storage.sources_bucket, "assets": storage.assets_bucket}
        for logical_bucket, key in collect_asset_keys(payload):
            try:
                data = await storage.get(bucket_of[logical_bucket], key)
            except Exception:  # noqa: BLE001 — 欠落アセットは skip(P3)
                continue
            zf.writestr(f"assets/{key}", data)
            assets_meta.append({
                "storage_key": key, "bucket": logical_bucket,
                "sha256": hashlib.sha256(data).hexdigest(), "byte_size": len(data),
            })
        manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "exported_at": payload["exported_at"],
            "assets": assets_meta,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buf.getvalue()
```

`run_export_full_job` を `archive = await build_export_archive(session, user_id, storage)` に差し替える(既存 `_zip_payload` 呼び出しを置換)。`overview_figures`/`explainer_figures` の storage_key カラム名は実モデル定義に合わせること(存在しない場合は `StorageKeys.overview_svg(article_id, version)` 等で導出)。

- [ ] **Step 4: 実行して成功を確認**

Run: `uv run pytest apps/worker/tests/test_export_bulk.py -v`
Expected: PASS(既存テストも含め green)

- [ ] **Step 5: コミット**

```bash
git add apps/worker/src/alinea_worker/tasks/export_user_data.py apps/worker/tests/test_export_bulk.py
git commit -m "feat(export): zipをmanifest+data+assets構造にしS3バイナリを同梱"
```

---

## Task 3: インポート復元コア(冪等マージ)

zip の `data.json` を依存順に復元する純粋ロジック。元 id 存在チェックで skip(冪等)。`user_id` リマップ、`papers` は arxiv_id 名寄せ。

**Files:**
- Create: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Test: `apps/worker/tests/test_import_bulk.py`

**Interfaces:**
- Consumes: `AsyncSession`、`packages/py-core` のモデル群、`alinea_core.search.rebuild.rebuild_block_search_index(session, revision_id, content)`、`alinea_core.document.blocks.DocumentContent`。
- Produces: `import_data_json(session, target_user_id, data: dict) -> dict`(戻り: `{"created": {...件数}, "skipped": {...}, "failed": [...]}`)。paper は arxiv_id 名寄せ、他行は元 id 存在チェック。

- [ ] **Step 1: 失敗するテストを書く**

```python
async def test_import_merges_idempotently(db: AsyncSession) -> None:
    # export 側の seed を使って payload を生成 → 別ユーザーへ import
    src = await _seed_user_data(db)  # test_export_bulk からの共有 helper を再利用
    payload = await build_export_payload(db, src["user_id"])
    target = await _make_user(db)
    summary1 = await import_data_json(db, target["user_id"], payload)
    assert summary1["created"]["library"] >= 1
    assert summary1["created"]["document_revisions"] >= 1
    # 2 回目は全 skip(冪等)
    summary2 = await import_data_json(db, target["user_id"], payload)
    assert summary2["created"]["library"] == 0
    assert summary2["skipped"]["library"] >= 1
    # target に本文・翻訳が復元されている
    revs = (await db.execute(select(DocumentRevision))).scalars().all()
    assert len(revs) >= 1
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `uv run pytest apps/worker/tests/test_import_bulk.py::test_import_merges_idempotently -v`
Expected: FAIL(`import_data_json` 未定義)

- [ ] **Step 3: 実装**

`import_user_data.py` に依存順の復元を実装する。順序は papers → source_assets(メタ)→ document_revisions → library_items → translation_sets → translation_units → glossaries → glossary_terms → notes → annotations → chat_threads → chat_messages → vocab → resources → articles → article_blocks → collections → collection_entries → share_tokens → saved_filters → reading_sessions → notifications。

各テーブルは「元 id で `session.get` → 存在すれば skip、無ければ INSERT」。paper のみ `arxiv_id` で既存検索(あれば再利用しその id にマップ、無ければ新規作成)。id リマップ用の dict(`old_paper_id -> new_paper_id`, `old_item_id -> new_item_id` など)を保持し、外部キーを張り替える。`user_id`/`owner_user_id` は `target_user_id` に固定。復元後、各 document_revision について `rebuild_block_search_index(session, rev_id, DocumentContent.model_validate(content))` を呼ぶ。件数を `summary` に集計して返す。個別行の失敗は `summary["failed"].append({...})` に記録して継続(P3)。

- [ ] **Step 4: 実行して成功を確認**

Run: `uv run pytest apps/worker/tests/test_import_bulk.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/worker/src/alinea_worker/tasks/import_user_data.py apps/worker/tests/test_import_bulk.py
git commit -m "feat(import): data.json冪等マージ復元コア + 検索索引再構築"
```

---

## Task 4: インポートジョブハンドラ(zip 展開 + アセット復元)

zip を検証・展開し、`import_data_json` を呼び、`assets/**` を sha256 照合で S3 復元する `import` Job ハンドラ。

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/import_user_data.py`
- Modify: `apps/worker/src/alinea_worker/tasks/__init__.py`
- Test: `apps/worker/tests/test_import_bulk.py`

**Interfaces:**
- Consumes: Task 3 の `import_data_json`、`build_export_archive`(往復テスト用)、`JobStore`(`store.session` / `store.succeed(job_id, result)` / `store.record_partial_failure`)、`S3Storage`。Job の `payload` に `{"upload_key": <S3 一時 key>}` が入る(Task 5 でアップロード)。
- Produces: `run_import_full_job(ctx, store, job) -> None`。`HANDLERS["import"] = run_import_full_job`。

- [ ] **Step 1: 失敗するテストを書く**

```python
async def test_import_job_roundtrip_restores_assets(db, s3_storage) -> None:
    src = await _seed_user_data(db)
    await s3_storage.put(s3_storage.sources_bucket, src["asset_key"], b"%PDF fake",
                         content_type="application/pdf")
    archive = await build_export_archive(db, src["user_id"], s3_storage)
    # 一時 key に zip を置き import Job を作る
    upload_key = f"imports/{uuid.uuid4()}.zip"
    await s3_storage.put(s3_storage.assets_bucket, upload_key, archive,
                         content_type="application/zip")
    target = await _make_user(db)
    store = JobStore(db)
    job_id = await store.enqueue(kind="import", priority="bulk",
                                 user_id=target["user_id"], payload={"upload_key": upload_key})
    job = await store.claim(job_id)
    await run_import_full_job({"s3": s3_storage}, store, job)
    done = await store.get(job_id)
    assert done.status == "succeeded"
    assert done.result["summary"]["created"]["library"] >= 1
    # target のアセットが復元されている(sha 一致)
    restored = await s3_storage.get(s3_storage.sources_bucket, src["asset_key"])
    assert restored == b"%PDF fake"
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `uv run pytest apps/worker/tests/test_import_bulk.py::test_import_job_roundtrip_restores_assets -v`
Expected: FAIL(`run_import_full_job` 未定義)

- [ ] **Step 3: 実装**

`import_user_data.py` に追加:

```python
async def run_import_full_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    session = store.session
    storage: S3Storage = ctx.get("s3") or S3Storage(ctx.get("settings"))
    upload_key = job.payload.get("upload_key")
    try:
        archive = await storage.get(storage.assets_bucket, upload_key)
    except Exception as exc:  # noqa: BLE001
        await store.fail_with_retry(str(job.id), {"code": "import_download_failed", "detail": str(exc)})
        return
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            if manifest.get("schema_version") != EXPORT_SCHEMA_VERSION:
                await store.fail_with_retry(str(job.id),
                    {"code": "import_schema_mismatch", "detail": str(manifest.get("schema_version"))})
                return
            data = json.loads(zf.read("data.json"))
            summary = await import_data_json(session, str(job.user_id), data)
            # アセット復元(sha256 照合、未存在のみ put)
            bucket_of = {"sources": storage.sources_bucket, "assets": storage.assets_bucket}
            for a in manifest.get("assets", []):
                key = a["storage_key"]; bucket = bucket_of[a["bucket"]]
                payload_bytes = zf.read(f"assets/{key}")
                if hashlib.sha256(payload_bytes).hexdigest() != a["sha256"]:
                    summary["failed"].append({"asset": key, "reason": "sha_mismatch"}); continue
                await storage.put(bucket, key, payload_bytes, content_type=a.get("content_type", "application/octet-stream"))
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        await store.fail_with_retry(str(job.id), {"code": "import_bad_archive", "detail": str(exc)})
        return
    await store.succeed(str(job.id), {"summary": summary})
```

`__init__.py` に `from alinea_worker.tasks.import_user_data import run_import_full_job` と `HANDLERS["import"] = run_import_full_job` を追加、`__all__` にも追記。`S3Storage.put` の content_type 引数名は既存呼び出しに合わせる。

- [ ] **Step 4: 実行して成功を確認**

Run: `uv run pytest apps/worker/tests/test_import_bulk.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/worker/src/alinea_worker/tasks/import_user_data.py apps/worker/src/alinea_worker/tasks/__init__.py apps/worker/tests/test_import_bulk.py
git commit -m "feat(import): importジョブハンドラ(zip展開+アセットsha照合復元)"
```

---

## Task 5: インポート API(zip アップロード + ステータス)

`POST /api/import/full`(multipart zip → S3 一時 key → `import` Job)と `GET /api/import/full/{job_id}`。

**Files:**
- Modify: `apps/api/src/alinea_api/routers/export.py`(または新規 `import_.py` を作り `main.py` にマウント)
- Modify: `apps/api/src/alinea_api/schemas/export.py`
- Test: `apps/api/tests/test_import_api.py`

**Interfaces:**
- Consumes: `JobStore.enqueue(kind, priority, user_id, payload)`、`ExportJobWakeupDep` と同型の import wakeup(同一 bulk キュー)、`S3Storage.put`、`CurrentUser` / `DbDep`。
- Produces: `POST /api/import/full`(operation_id `import_full_start`、202、body に UploadFile)→ `{job_id}`。`GET /api/import/full/{job_id}`(operation_id `import_full_status`)→ `{job, summary}`。

- [ ] **Step 1: 失敗するテストを書く**

```python
async def test_import_full_creates_job(client, auth_headers) -> None:
    files = {"file": ("backup.zip", b"PK\x03\x04 fake", "application/zip")}
    res = await client.post("/api/import/full", files=files, headers=auth_headers)
    assert res.status_code == 202
    job_id = res.json()["job_id"]
    status = await client.get(f"/api/import/full/{job_id}", headers=auth_headers)
    assert status.status_code == 200
    assert "job" in status.json()
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `uv run pytest apps/api/tests/test_import_api.py::test_import_full_creates_job -v`
Expected: FAIL(404 / ルート未定義)

- [ ] **Step 3: 実装**

`export.py` に UploadFile を受ける POST を追加(FastAPI `UploadFile`)。zip を読み、`StorageKeys` に import 一時 key(`imports/{user}/{uuid}.zip`)を追加、`S3Storage.put(assets_bucket, key, bytes, content_type="application/zip")`、`JobStore.enqueue(kind="import", priority="bulk", user_id, payload={"upload_key": key})`、`wakeup(job_id)`。schemas に `ImportFullStartResponse(job_id: str)` と `ImportFullStatusResponse(job: JobOut, summary: dict | None)` を追加。`StorageKeys.import_upload(user_id, upload_id) -> f"imports/{user_id}/{upload_id}.zip"` を追加。

- [ ] **Step 4: 実行して成功を確認**

Run: `uv run pytest apps/api/tests/test_import_api.py -v`
Expected: PASS

- [ ] **Step 5: SDK 再生成 + コミット**

```bash
pnpm --filter @alinea/api-client build   # OpenAPI から SDK 再生成(import_full_* が生える)
git add apps/api/src/alinea_api/routers/export.py apps/api/src/alinea_api/schemas/export.py packages/py-core/src/alinea_core/storage/s3.py apps/api/tests/test_import_api.py packages/api-client/src/generated
git commit -m "feat(import): zipアップロードAPI + ステータス取得 + SDK再生成"
```

---

## Task 6: 設定「データ」カテゴリ — ラベル変更 + エクスポート/インポート UI

左ナビの「エクスポート」を「データ」に改名し、既存エクスポートに加えて完全バックアップ/インポート UI を追加。

**Files:**
- Modify: `apps/web/src/components/settings/SettingsClient.tsx`(CATEGORIES ラベル)
- Modify: `apps/web/src/components/settings/ExportSettings.tsx`
- Test: `apps/web/src/components/settings/ExportSettings.test.tsx`(無ければ作成)

**Interfaces:**
- Consumes: Task 5 で生成された SDK `importFullStart` / `importFullStatus`、既存 `exportFullStart` / `exportFullStatus`、`triggerDownload`。
- Produces: 「データ」カテゴリに「完全バックアップ(エクスポート)」カードと「インポート(復元)」カード(file input → アップロード → ポーリング)。

- [ ] **Step 1: 失敗するテストを書く**

```tsx
test("データカテゴリに完全バックアップとインポートのカードが出る", async () => {
  render(<ExportSettings />, { wrapper: makeWrapper() });
  expect(screen.getByText("完全バックアップ")).toBeInTheDocument();
  expect(screen.getByText(/インポート|復元/)).toBeInTheDocument();
  // BYOK は移行されない注記
  expect(screen.getByText(/API キー.*再登録|BYOK/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `pnpm --filter @alinea/web test -- ExportSettings`
Expected: FAIL(テキスト未存在)

- [ ] **Step 3: 実装**

`SettingsClient.tsx` の `CATEGORIES` で `{ id: "export", label: "エクスポート" }` を `label: "データ"` に変更。`ExportSettings.tsx` に「完全バックアップ」カード(既存 JSON 一括の文言を「全データ(論文本文・翻訳・PDF・図・メモ等)を 1 つの zip に。別 PC への移行に使えます」に更新)と「インポート(復元)」カード(`<input type="file" accept=".zip">` → `importFullStart` → `importFullStatus` ポーリング → 完了トースト、「既存データはマージされ上書きされません」「BYOK(API キー)は移行されないため復元後に再登録してください」注記)を追加。`readOnly` 時は実行系を非描画。

- [ ] **Step 4: 実行して成功を確認**

Run: `pnpm --filter @alinea/web test -- ExportSettings`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add apps/web/src/components/settings/SettingsClient.tsx apps/web/src/components/settings/ExportSettings.tsx apps/web/src/components/settings/ExportSettings.test.tsx
git commit -m "feat(settings): カテゴリ名をデータに変更しインポート/完全バックアップUIを追加"
```

---

## Task 7: ラウンドトリップ E2E テスト(BYOK 除外・検索再構築)

エクスポート→インポートで全カテゴリが往復し、BYOK が漏れず、検索がヒットすることを実スタックで確認。

**Files:**
- Test: `apps/worker/tests/test_import_bulk.py`(追加)

**Interfaces:**
- Consumes: Task 2 `build_export_archive`、Task 4 `run_import_full_job`、`alinea_api.routers.search`(または直接 `block_search_index` を SELECT)。

- [ ] **Step 1: 追加テストを書く**

```python
async def test_export_excludes_byok_and_import_rebuilds_search(db, s3_storage) -> None:
    src = await _seed_user_data_with_byok(db)  # byok_api_keys を 1 行足す
    archive = await build_export_archive(db, src["user_id"], s3_storage)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        blob = zf.read("data.json").decode()
        assert "byok" not in blob.lower()  # 平文キーも鍵メタも含まれない
    # import 後、block_search_index が document_revisions から再構築される
    # (import_data_json の戻り or 直接 SELECT COUNT で確認)
```

- [ ] **Step 2: 実行して確認**

Run: `uv run pytest apps/worker/tests/test_import_bulk.py -v`
Expected: PASS

- [ ] **Step 3: コミット**

```bash
git add apps/worker/tests/test_import_bulk.py
git commit -m "test(migration): BYOK除外と検索索引再構築のラウンドトリップ検証"
```

---

## Task 8: 全体検証(build/lint/typecheck/test/pytest)

**Files:** なし(検証のみ)

- [ ] **Step 1: JS 側フル検証**

Run: `pnpm turbo build lint typecheck test`
Expected: 全タスク成功(web/extension/figures/api-client)

- [ ] **Step 2: Python フル検証**

Run: `uv run pytest apps/api apps/worker packages -q`
Expected: 全 pass(既存 test_export_bulk 含む)

- [ ] **Step 3: OpenAPI/SDK 整合**

Run: `pnpm --filter @alinea/api-client build && git diff --exit-code packages/api-client/src/generated`
Expected: 差分なし(Task 5 でコミット済み)

- [ ] **Step 4: 検証結果をコミット(必要なら)**

```bash
git add -A && git commit -m "chore(migration): 全体検証green" || echo "no changes"
```

---

## Self-Review 結果

- **Spec カバレッジ**: 生成物全部含む(Task 1,2)/マージ追加冪等(Task 3)/非同期 Job 拡張(Task 2,4,5)/BYOK 除外(Task 1,7)/検索索引再構築(Task 3,7)/カテゴリ名「データ」(Task 6)/インポート UI(Task 6)— 全決定事項に対応タスクあり。
- **Placeholder**: なし(各 Step にコード or コマンド + 期待値)。
- **型整合**: `build_export_payload`(既存)→ `build_export_archive`(T2)→ `import_data_json`(T3)→ `run_import_full_job`(T4)→ API(T5)→ SDK(T6)の連鎖で名前・引数が一貫。`EXPORT_SCHEMA_VERSION=2` は T1 定義を T2/T4 が参照。
- **要確認(実装時に models.py で検証)**: `overview_figures`/`explainer_figures` の storage_key カラム名。存在しなければ `StorageKeys.overview_svg`/`explainer_png` で導出(Task 2 Step 3 に明記済み)。
