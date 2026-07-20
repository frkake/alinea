"""PresentationRunner tests (Task 29): grounding, SVG safety, atomic replace.

No live LLM, no external image fetch, no temp-dir residue. A fake router returns
a valid SlidePlan then per-slide SVGs; the ppt-master step is a stub that writes
a structurally valid PPTX; S3 is a fake recording store. The privacy boundary is
proven by asserting per-secret sentinels are absent from BOTH the serialized
packet and every captured LLM request.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    DocumentRevision,
    Job,
    LibraryItem,
    Note,
    Paper,
    PresentationArtifact,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import DocumentContent
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import StorageKeys
from alinea_llm.router import LLMRouter
from alinea_llm.structured import attach_parsed
from alinea_llm.types import LLMRequest, LLMResponse, StreamEvent
from alinea_worker.presentation.runner import (
    MAX_PPTX_BYTES,
    PptxArtifactError,
    PresentationError,
    PresentationRunner,
    SlidePlanValidationError,
    build_notes_markdown,
    plan_slides,
    replace_presentation_artifact,
    validate_pptx_bytes,
    validate_slide_plan,
)
from alinea_worker.presentation.schemas import SlidePlan, SlidePlanDocument
from alinea_worker.presentation.source_packet import build_source_packet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "presentation" / "paper_document.json"


def _uid() -> str:
    return str(uuid.uuid4())


def _content() -> DocumentContent:
    return DocumentContent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _min_pptx(slide_count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
            '/package/2006/content-types"/>',
        )
        zf.writestr("ppt/presentation.xml", "<p:presentation/>")
        for index in range(1, slide_count + 1):
            zf.writestr(f"ppt/slides/slide{index}.xml", "<p:sld/>")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Deterministic fake router
# --------------------------------------------------------------------------- #
def _valid_plan(revision_id: str, slide_count: int = 12) -> dict[str, Any]:
    """A grounded plan: anchors + figures that exist, numbers from the paper."""
    slides: list[dict[str, Any]] = [
        {
            "index": 1,
            "title": "Rectified Flow",
            "claims": ["直線化された輸送で高速生成を実現する。"],
            "evidence_anchors": [f"{revision_id}:blk-abs-1"],
            "figure_ids": [],
            "speaker_notes": "タイトルスライド。",
            "layout": "title",
        },
        {
            "index": 2,
            "title": "課題",
            "claims": ["拡散モデルは多ステップで低速。"],
            "evidence_anchors": [f"{revision_id}:blk-1-1"],
            "figure_ids": [],
            "speaker_notes": "背景の説明。",
            "layout": "content",
        },
        {
            "index": 3,
            "title": "手法",
            "claims": ["reflow で経路を直線化する。"],
            "evidence_anchors": [f"{revision_id}:blk-2-1", f"{revision_id}:blk-eq-1"],
            "figure_ids": [f"{revision_id}:blk-fig-1"],
            "speaker_notes": "図1で直線化を示す。",
            "layout": "figure",
        },
        {
            "index": 4,
            "title": "結果",
            "claims": ["FID 3.2 を達成。"],
            "evidence_anchors": [f"{revision_id}:blk-3-1"],
            "figure_ids": [f"{revision_id}:blk-tab-1"],
            "speaker_notes": "表1で比較する。",
            "layout": "comparison",
        },
    ]
    while len(slides) < slide_count - 1:
        i = len(slides) + 1
        slides.append(
            {
                "index": i,
                "title": f"補足 {i}",
                "claims": ["直線化により推論が速くなる。"],
                "evidence_anchors": [f"{revision_id}:blk-1-2"],
                "figure_ids": [],
                "speaker_notes": "補足の説明。",
                "layout": "content",
            }
        )
    slides.append(
        {
            "index": slide_count,
            "title": "まとめ",
            "claims": ["直線化で一段生成が可能。"],
            "evidence_anchors": [f"{revision_id}:blk-5-1"],
            "figure_ids": [],
            "speaker_notes": "結論。",
            "layout": "summary",
        }
    )
    return {"slides": slides}


_GOOD_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720">'
    '<rect x="0" y="0" width="1280" height="720" fill="#0b1021"/>'
    '<text x="80" y="200" font-size="64" fill="#ffffff">スライド</text></svg>'
)


class RecordingRouterProvider:
    """Records every request; returns a valid plan then valid per-slide SVGs.

    ``plan_override`` lets a test inject an invalid plan; ``svg_override`` lets a
    test inject an unsafe SVG. On the *second* plan call (repair) it returns
    ``repair_plan`` if given, else the good plan.
    """

    name = "fake-presentation"

    def __init__(
        self,
        *,
        revision_id: str,
        slide_count: int = 12,
        plan_override: dict[str, Any] | None = None,
        repair_plan: dict[str, Any] | None = None,
        svg_override: str | None = None,
    ) -> None:
        self.revision_id = revision_id
        self.slide_count = slide_count
        self.plan_override = plan_override
        self.repair_plan = repair_plan
        self.svg_override = svg_override
        self.requests: list[LLMRequest] = []
        self.plan_calls = 0

    def _request_text(self, req: LLMRequest) -> str:
        parts = [p.text or "" for p in req.system]
        for msg in req.messages:
            parts += [p.text or "" for p in msg.parts]
        return "\n".join(parts)

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        spec = req.json_schema
        assert spec is not None
        if spec.name == "presentation_slide_plan_v1":
            self.plan_calls += 1
            if self.plan_calls == 1 and self.plan_override is not None:
                data: dict[str, Any] = self.plan_override
            elif self.plan_calls >= 2 and self.repair_plan is not None:
                data = self.repair_plan
            else:
                data = _valid_plan(self.revision_id, self.slide_count)
        elif spec.name == "presentation_slide_svg_v1":
            data = {"svg": self.svg_override if self.svg_override is not None else _GOOD_SVG}
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected schema {spec.name}")
        resp = LLMResponse(
            text=json.dumps(data, ensure_ascii=False),
            provider=self.name,
            model="fake-model",
            request_id=f"fake-{len(self.requests)}",
        )
        return attach_parsed(resp, spec)

    async def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(self, req: LLMRequest) -> Any:  # pragma: no cover
        raise NotImplementedError
        yield StreamEvent(type="end")

    async def count_tokens(self, req: LLMRequest) -> int:  # pragma: no cover
        return 1


class _FakeFactory:
    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def for_job(self, *, user_id: str, task: str) -> LLMRouter:
        return self._router


def _router_for(provider: RecordingRouterProvider) -> LLMRouter:
    return LLMRouter([("fake", "fake-model", provider)])


# --------------------------------------------------------------------------- #
# Fake S3 storage with failure injection
# --------------------------------------------------------------------------- #
class FakeS3:
    def __init__(
        self, *, fail_put: bool = False, fail_delete: bool = False
    ) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_put = fail_put
        self.fail_delete = fail_delete

    @property
    def assets_bucket(self) -> str:
        return "assets"

    async def put(
        self, bucket: str, key: str, body: bytes, *, content_type: str = "", metadata: Any = None
    ) -> None:
        if self.fail_put:
            raise RuntimeError("injected S3 put failure")
        self.objects[key] = body

    async def get(self, bucket: str, key: str) -> bytes:
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    async def delete_many(self, bucket: str, keys: Any) -> None:
        if self.fail_delete:
            raise RuntimeError("injected S3 delete failure")
        for key in keys:
            self.objects.pop(key, None)
            self.deleted.append(key)


# --------------------------------------------------------------------------- #
# ppt-master adapter stub
# --------------------------------------------------------------------------- #
class StubAdapter:
    """Writes a structurally valid PPTX with one slide per input SVG."""

    def __init__(self, *, slide_count_override: int | None = None) -> None:
        self.calls = 0
        self.slide_count_override = slide_count_override
        self.notes_seen: str | None = None

    def convert(self, *, svg_source_dir: Path, notes_path: Path | None, work_dir: Path) -> Any:
        self.calls += 1
        from types import SimpleNamespace

        svg_files = sorted(Path(svg_source_dir).glob("*.svg"))
        count = self.slide_count_override or len(svg_files)
        if notes_path is not None and Path(notes_path).exists():
            self.notes_seen = Path(notes_path).read_text(encoding="utf-8")
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        pptx_path = work_dir / "deck.pptx"
        pptx_path.write_bytes(_min_pptx(count))
        return SimpleNamespace(pptx_path=pptx_path, slide_count=count, project_dir=work_dir)


# --------------------------------------------------------------------------- #
# DB seed
# --------------------------------------------------------------------------- #
async def _seed(db: AsyncSession) -> dict[str, Any]:
    user = User(id=_uid(), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=_uid(),
        arxiv_id=f"2209.{uuid.uuid4().hex[:5]}",
        title="Rectified Flow",
        authors=[{"name": "Xingchao Liu"}, {"name": "Qiang Liu"}],
        venue="ICLR 2023",
        published_on=dt.date(2022, 9, 7),
        license="cc-by-4.0",
        visibility="private",
        owner_user_id=user.id,
    )
    db.add(paper)
    await db.flush()
    revision = DocumentRevision(
        id=_uid(),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=_content().model_dump(),
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    item = LibraryItem(id=_uid(), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(item)
    await db.commit()
    return {"user": user, "paper": paper, "revision": revision, "item": item}


async def _enqueue_claim(
    db: AsyncSession, *, seed: dict[str, Any], instruction: str | None = None
) -> Job:
    store = JobStore(db)
    payload: dict[str, Any] = {
        "library_item_id": str(seed["item"].id),
        "source_revision_id": str(seed["revision"].id),
        "preset": "research_talk",
        "audience": "researcher",
    }
    if instruction:
        payload["instruction"] = instruction
    job_id = await store.enqueue(
        kind="presentation",
        priority="bulk",
        user_id=str(seed["user"].id),
        paper_id=str(seed["paper"].id),
        library_item_id=str(seed["item"].id),
        payload=payload,
    )
    job = await store.claim(job_id)
    assert job is not None
    return job


# =========================================================================== #
# Slide-plan validation (Step 3)
# =========================================================================== #
def _packet_from_content() -> Any:
    from types import SimpleNamespace

    paper = Paper(
        id=_uid(),
        arxiv_id="2209.03003",
        title="Rectified Flow",
        authors=[{"name": "Xingchao Liu"}],
        venue="ICLR 2023",
        published_on=dt.date(2022, 9, 7),
        license="cc-by-4.0",
    )
    revision = SimpleNamespace(id=_uid(), content=_content().model_dump())
    packet = build_source_packet(
        paper=paper,
        revision=revision,
        fetched_figure_keys={"figures/paper/rev/blk-fig-1.png"},
    )
    return packet


def test_validate_rejects_nonexistent_anchor() -> None:
    packet = _packet_from_content()
    plan = SlidePlanDocument.model_validate(_valid_plan(packet.revision_id, 12))
    plan.slides[1].evidence_anchors = [f"{packet.revision_id}:blk-does-not-exist"]
    errors = validate_slide_plan(plan, packet, preset="research_talk")
    assert any("存在しない evidence anchor" in e for e in errors)


def test_validate_rejects_duplicate_figure() -> None:
    packet = _packet_from_content()
    plan = SlidePlanDocument.model_validate(_valid_plan(packet.revision_id, 12))
    fig = f"{packet.revision_id}:blk-fig-1"
    plan.slides[2].figure_ids = [fig]
    plan.slides[3].figure_ids = [fig]  # duplicate use of the same figure
    errors = validate_slide_plan(plan, packet, preset="research_talk")
    assert any("重複使用" in e for e in errors)


def test_validate_rejects_ungrounded_number() -> None:
    packet = _packet_from_content()
    plan = SlidePlanDocument.model_validate(_valid_plan(packet.revision_id, 12))
    plan.slides[3].claims = ["FID 999.9 を達成。"]  # not in the paper
    errors = validate_slide_plan(plan, packet, preset="research_talk")
    assert any("根拠なし" in e for e in errors)


def test_validate_rejects_out_of_range_slide_count() -> None:
    packet = _packet_from_content()
    plan = SlidePlanDocument.model_validate(_valid_plan(packet.revision_id, 12))
    plan.slides = plan.slides[:3]  # below research_talk minimum (12)
    errors = validate_slide_plan(plan, packet, preset="research_talk")
    assert any("スライド枚数" in e for e in errors)


def test_valid_plan_has_no_errors() -> None:
    packet = _packet_from_content()
    plan = SlidePlanDocument.model_validate(_valid_plan(packet.revision_id, 14))
    errors = validate_slide_plan(plan, packet, preset="research_talk")
    assert errors == []


async def test_plan_slides_repairs_once_then_succeeds() -> None:
    packet = _packet_from_content()
    bad = _valid_plan(packet.revision_id, 12)
    bad["slides"][1]["evidence_anchors"] = [f"{packet.revision_id}:blk-nope"]
    provider = RecordingRouterProvider(
        revision_id=packet.revision_id, plan_override=bad
    )  # 1st call bad, 2nd (repair) good
    router = _router_for(provider)
    from types import SimpleNamespace

    job = SimpleNamespace(id=_uid(), user_id=_uid(), library_item_id=_uid())
    plan, resp = await plan_slides(
        router, packet=packet, preset="research_talk", audience="researcher",
        instruction=None, job=job,
    )
    assert provider.plan_calls == 2  # exactly one repair attempt
    assert validate_slide_plan(plan, packet, preset="research_talk") == []
    # The repair prompt carried the previous validation error.
    assert any("検証エラー" in "\n".join(p.text or "" for p in r.messages[0].parts)
               for r in provider.requests)


async def test_plan_slides_fails_at_planning_after_repair() -> None:
    packet = _packet_from_content()
    bad = _valid_plan(packet.revision_id, 12)
    bad["slides"][1]["evidence_anchors"] = [f"{packet.revision_id}:blk-nope"]
    provider = RecordingRouterProvider(
        revision_id=packet.revision_id, plan_override=bad, repair_plan=bad
    )
    router = _router_for(provider)
    from types import SimpleNamespace

    job = SimpleNamespace(id=_uid(), user_id=_uid(), library_item_id=_uid())
    with pytest.raises(SlidePlanValidationError) as caught:
        await plan_slides(
            router, packet=packet, preset="research_talk", audience="researcher",
            instruction=None, job=job,
        )
    assert caught.value.stage == "planning"
    assert provider.plan_calls == 2  # initial + exactly one repair, no more


# =========================================================================== #
# PPTX independent validation (Step 6)
# =========================================================================== #
def test_validate_pptx_bytes_accepts_and_hashes(tmp_path: Path) -> None:
    data = _min_pptx(4)
    count, sha = validate_pptx_bytes(data, expected_slides=4, work_dir=tmp_path)
    assert count == 4
    assert len(sha) == 64
    assert not list(tmp_path.glob("*.pptx"))  # probe file removed


def test_validate_pptx_bytes_rejects_slide_count_mismatch(tmp_path: Path) -> None:
    with pytest.raises(PptxArtifactError):
        validate_pptx_bytes(_min_pptx(3), expected_slides=5, work_dir=tmp_path)


def test_validate_pptx_bytes_rejects_zero_and_oversize(tmp_path: Path) -> None:
    with pytest.raises(PptxArtifactError):
        validate_pptx_bytes(b"", expected_slides=1, work_dir=tmp_path)
    with pytest.raises(PptxArtifactError):
        validate_pptx_bytes(
            b"x" * (MAX_PPTX_BYTES + 1), expected_slides=1, work_dir=tmp_path
        )


def test_validate_pptx_bytes_rejects_broken_zip(tmp_path: Path) -> None:
    with pytest.raises(PptxArtifactError):
        validate_pptx_bytes(b"not a zip", expected_slides=0, work_dir=tmp_path)


# =========================================================================== #
# Full runner (green path) + no temp residue
# =========================================================================== #
async def test_runner_generates_and_uploads_atomically(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    seed = await _seed(db_session)
    job = await _enqueue_claim(db_session, seed=seed)
    store = JobStore(db_session)
    provider = RecordingRouterProvider(revision_id=str(seed["revision"].id))
    storage = FakeS3()
    adapter = StubAdapter()
    ctx = {"user_router_factory": _FakeFactory(_router_for(provider)), "s3": storage}

    runner = PresentationRunner(ctx, store, job, adapter=adapter)
    artifact = await runner.run()

    # Artifact row points at the job-specific key, which now holds the PPTX.
    expected_key = StorageKeys.presentation_pptx(str(seed["item"].id), str(job.id))
    assert artifact.pptx_storage_key == expected_key
    assert expected_key in storage.objects
    assert artifact.model_provider == provider.name
    # Notes/total.md was written and handed to ppt-master.
    assert adapter.notes_seen is not None and "# 01" in adapter.notes_seen
    # Job succeeded.
    finished = await store.get(str(job.id))
    assert finished is not None and finished.status == "succeeded"
    # No temp-dir residue.
    assert not list(tmp_path.glob("presentation-*"))


async def test_runner_passes_only_paper_material_to_llm_no_private_sentinels(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    seed = await _seed(db_session)

    # Private objects that must NEVER reach the packet or the LLM.
    note_secret = f"NOTE-{uuid.uuid4().hex}"
    annotation_secret = f"ANNOT-{uuid.uuid4().hex}"
    chat_secret = f"CHAT-{uuid.uuid4().hex}"
    translation_secret = f"TRANS-{uuid.uuid4().hex}"
    article_secret = f"ARTICLE-{uuid.uuid4().hex}"
    api_key = f"sk-BYOK-{uuid.uuid4().hex}"

    item_id = str(seed["item"].id)
    db_session.add(Note(id=_uid(), library_item_id=item_id, title="x", body_md=note_secret))
    db_session.add(
        Annotation(
            id=_uid(),
            library_item_id=item_id,
            kind="comment",
            color="question",
            body=annotation_secret,
            anchor={"block_id": "blk-1-1", "quote": annotation_secret},
        )
    )
    thread = ChatThread(id=_uid(), library_item_id=item_id, title="t", is_main=True)
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        ChatMessage(
            thread_id=thread.id,
            role="user",
            content={"segments": [{"md": chat_secret}]},
            text_plain=chat_secret,
        )
    )
    tset = TranslationSet(id=_uid(), revision_id=str(seed["revision"].id), style="natural")
    db_session.add(tset)
    await db_session.flush()
    db_session.add(
        TranslationUnit(
            set_id=tset.id,
            block_id="blk-1-1",
            source_hash="h",
            content_ja={"kind": "text"},
            text_ja=translation_secret,
        )
    )
    article = Article(id=_uid(), library_item_id=item_id, title="a", preset="beginner")
    db_session.add(article)
    await db_session.flush()
    db_session.add(
        ArticleBlock(
            article_id=article.id,
            position=0,
            type="paragraph",
            content={"md": article_secret},
            text_plain=article_secret,
        )
    )
    await db_session.commit()

    job = await _enqueue_claim(db_session, seed=seed, instruction="平易な言葉で強調して")
    store = JobStore(db_session)
    provider = RecordingRouterProvider(revision_id=str(seed["revision"].id))
    ctx = {"user_router_factory": _FakeFactory(_router_for(provider)), "s3": FakeS3()}

    await PresentationRunner(ctx, store, job, adapter=StubAdapter()).run()

    # The packet handed to planning (assemble the same way the runner did).
    from types import SimpleNamespace

    packet = build_source_packet(
        paper=seed["paper"],
        revision=SimpleNamespace(id=str(seed["revision"].id), content=_content().model_dump()),
    )
    serialized = packet.model_dump_json()
    secrets = (note_secret, annotation_secret, chat_secret, translation_secret,
               article_secret, api_key)
    for secret in secrets:
        assert secret not in serialized

    # And absent from EVERY captured LLM request (system + user text).
    assert provider.requests
    for req in provider.requests:
        blob = "\n".join(
            [p.text or "" for p in req.system]
            + [p.text or "" for msg in req.messages for p in msg.parts]
        )
        for secret in secrets:
            assert secret not in blob


async def test_runner_rejects_unsafe_svg_before_export(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    seed = await _seed(db_session)
    job = await _enqueue_claim(db_session, seed=seed)
    store = JobStore(db_session)
    unsafe = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
        '<script>alert(1)</script></svg>'
    )
    provider = RecordingRouterProvider(revision_id=str(seed["revision"].id), svg_override=unsafe)
    adapter = StubAdapter()
    ctx = {"user_router_factory": _FakeFactory(_router_for(provider)), "s3": FakeS3()}

    with pytest.raises(PresentationError) as caught:
        await PresentationRunner(ctx, store, job, adapter=adapter).run()
    assert caught.value.stage == "authoring_slides"
    # ppt-master was never invoked on unsafe SVG.
    assert adapter.calls == 0
    # No temp residue.
    assert not list(tmp_path.glob("presentation-*"))


# =========================================================================== #
# Atomic replacement — failure injection (Step 6)
# =========================================================================== #
async def _make_existing_artifact(
    db: AsyncSession, seed: dict[str, Any], *, old_key: str, storage: FakeS3
) -> PresentationArtifact:
    artifact = PresentationArtifact(
        id=_uid(),
        library_item_id=str(seed["item"].id),
        source_revision_id=str(seed["revision"].id),
        generation_job_id=_uid(),
        preset="research_talk",
        audience="researcher",
        pptx_storage_key=old_key,
        ppt_master_revision="old-rev",
    )
    db.add(artifact)
    await db.commit()
    storage.objects[old_key] = _min_pptx(3)
    return artifact


async def test_replace_upload_failure_keeps_old_artifact_downloadable(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    seed = await _seed(db_session)
    job = await _enqueue_claim(db_session, seed=seed)
    storage = FakeS3(fail_put=True)
    old_key = "presentations/old/prev.pptx"
    await _make_existing_artifact(db_session, seed, old_key=old_key, storage=storage)

    with pytest.raises(RuntimeError, match="injected S3 put failure"):
        await replace_presentation_artifact(
            db_session,
            storage,
            job=job,
            library_item_id=str(seed["item"].id),
            source_revision_id=str(seed["revision"].id),
            preset="research_talk",
            audience="researcher",
            instruction="",
            model_provider="fake",
            model_id="m",
            pptx_bytes=_min_pptx(4),
            expected_slides=4,
            work_dir=tmp_path,
        )

    # DB row untouched: still points at the old, still-present key.
    row = (
        await db_session.execute(
            select(PresentationArtifact).where(
                PresentationArtifact.library_item_id == str(seed["item"].id)
            )
        )
    ).scalar_one()
    assert row.pptx_storage_key == old_key
    assert old_key in storage.objects  # old PPTX still downloadable


async def test_replace_old_key_delete_failure_still_succeeds(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    seed = await _seed(db_session)
    job = await _enqueue_claim(db_session, seed=seed)
    storage = FakeS3(fail_delete=True)
    old_key = "presentations/old/prev.pptx"
    await _make_existing_artifact(db_session, seed, old_key=old_key, storage=storage)

    artifact = await replace_presentation_artifact(
        db_session,
        storage,
        job=job,
        library_item_id=str(seed["item"].id),
        source_revision_id=str(seed["revision"].id),
        preset="research_talk",
        audience="researcher",
        instruction="",
        model_provider="fake",
        model_id="m",
        pptx_bytes=_min_pptx(4),
        expected_slides=4,
        work_dir=tmp_path,
    )

    new_key = StorageKeys.presentation_pptx(str(seed["item"].id), str(job.id))
    # New artifact published successfully despite the stale-delete failure.
    assert artifact.pptx_storage_key == new_key
    assert new_key in storage.objects
    # Cleanup retry was recorded on the job log (partial failure, not job failure).
    refreshed = await db_session.get(Job, str(job.id), populate_existing=True)
    assert refreshed is not None
    assert any(
        entry.get("error", {}).get("code") == "stale_pptx_delete_failed"
        for entry in refreshed.log
        if isinstance(entry, dict)
    )


async def test_replace_first_generation_inserts_new_row(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    seed = await _seed(db_session)
    job = await _enqueue_claim(db_session, seed=seed)
    storage = FakeS3()

    artifact = await replace_presentation_artifact(
        db_session,
        storage,
        job=job,
        library_item_id=str(seed["item"].id),
        source_revision_id=str(seed["revision"].id),
        preset="research_talk",
        audience="researcher",
        instruction="",
        model_provider="fake",
        model_id="m",
        pptx_bytes=_min_pptx(4),
        expected_slides=4,
        work_dir=tmp_path,
    )
    new_key = StorageKeys.presentation_pptx(str(seed["item"].id), str(job.id))
    assert artifact.pptx_storage_key == new_key
    assert storage.deleted == []  # nothing to delete on first generation


async def test_replace_bad_pptx_never_touches_db_or_storage(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    seed = await _seed(db_session)
    job = await _enqueue_claim(db_session, seed=seed)
    storage = FakeS3()
    old_key = "presentations/old/prev.pptx"
    await _make_existing_artifact(db_session, seed, old_key=old_key, storage=storage)

    with pytest.raises(PptxArtifactError):
        await replace_presentation_artifact(
            db_session,
            storage,
            job=job,
            library_item_id=str(seed["item"].id),
            source_revision_id=str(seed["revision"].id),
            preset="research_talk",
            audience="researcher",
            instruction="",
            model_provider="fake",
            model_id="m",
            pptx_bytes=b"not a zip",  # fails validation before any upload
            expected_slides=4,
            work_dir=tmp_path,
        )
    row = (
        await db_session.execute(
            select(PresentationArtifact).where(
                PresentationArtifact.library_item_id == str(seed["item"].id)
            )
        )
    ).scalar_one()
    assert row.pptx_storage_key == old_key
    assert old_key in storage.objects
    new_key = StorageKeys.presentation_pptx(str(seed["item"].id), str(job.id))
    assert new_key not in storage.objects  # never uploaded


# =========================================================================== #
# Notes assembly
# =========================================================================== #
def test_build_notes_markdown_uses_grounded_notes() -> None:
    plan = SlidePlanDocument(
        slides=[
            SlidePlan(index=1, title="T", claims=[], evidence_anchors=[], speaker_notes="note-a", layout="title"),
            SlidePlan(index=2, title="U", claims=[], evidence_anchors=[], speaker_notes="", layout="content"),
        ]
    )
    md = build_notes_markdown(plan)
    assert "# 01" in md and "note-a" in md
    assert "# 02" in md and "U" in md  # empty notes fall back to the title
