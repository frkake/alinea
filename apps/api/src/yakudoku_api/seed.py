"""開発シードデータ投入(M0-25 / plans/12 §14・plans/00 §9)。

入口(plans/00 §9・plans/12 §14.1 の逐語):

    python -m yakudoku_api.seed --sample rectified-flow [--reset] [--scale N] [--full]

Rectified Flow(arXiv:2209.03003)を全テスト・VR・開発の共通データ源として投入する。
フィクスチャは ``seed_data/rectified_flow/``(JSON + assets)。ブロック安定 ID は
``yakudoku_core.parsing.block_ids.assign_block_ids`` で決定的に導出し、
``block_search_index`` は ``yakudoku_core.search.rebuild`` で再構築する。

投入エンティティ: users(``dev@yakudoku.test``)/ papers / source_assets /
document_revisions / translation_sets + translation_units / library_items(reading_position 込み)/
chat_threads(メイン)+ chat_messages。

冪等性: 既に投入済み(arXiv:2209.03003 の paper が存在)なら何もしない。``--reset`` は
本シード由来のデータ(RF 論文 + dev 所有のスケール複製)だけを削除して再投入する
(他エージェント・他ユーザーのデータには触れない)。``--scale N`` はライブラリ件数を
N 件水増しし、``--full`` は全対象セクションを訳済みにする
(既定は要旨 + 先頭セクションのみ訳済み = 取り込み直後の状態)。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

# ローカル HTTP(MinIO)はプロキシを迂回する(企業プロキシ環境。plans/00・MEMORY)。
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from yakudoku_core.db.models import (
    ChatMessage,
    ChatThread,
    DocumentRevision,
    LibraryItem,
    Paper,
    SourceAsset,
    TranslationSet,
    TranslationUnit,
    User,
)
from yakudoku_core.document.blocks import Block, DocumentContent
from yakudoku_core.document.plaintext import chat_content_to_plain
from yakudoku_core.parsing.block_ids import assign_block_ids
from yakudoku_core.search.rebuild import rebuild_block_search_index
from yakudoku_core.settings import get_settings
from yakudoku_core.storage.s3 import S3Storage, StorageKeys
from yakudoku_core.translation.placeholder import encode_block

ARXIV_ID = "2209.03003"
SOURCE_VERSION = "v1"
PARSER_VERSION = "arxiv-html-2026.07.1"
DEV_EMAIL = "dev@yakudoku.test"
MEMBER_EMAIL = "member@yakudoku.test"
SEED_MODEL = "seed-fixture"
FIXTURE_DIR = Path(__file__).resolve().parent / "seed_data" / "rectified_flow"

# 既定訳(取り込み直後)で訳すトップレベルセクション: 要旨 + 先頭セクション。
DEFAULT_TRANSLATED_TOP_SECTIONS = frozenset({"sec-0", "sec-1"})
STATUS_CYCLE = ("planned", "up_next", "reading", "done", "reread", "on_hold")
_STATUS_LABELS = {
    "planned": "yellow",
    "up_next": "green",
    "reading": "blue",
    "done": "pink",
    "reread": "yellow",
    "on_hold": "green",
}


def _load_json(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text("utf-8"))


def _load_document() -> DocumentContent:
    """document.json を読み、決定的なブロック安定 ID を(再)付与する。

    ``assign_block_ids`` は内容から ID を導出するため、フィクスチャに焼き込まれた ID と
    一致する(冪等)。DB へ入る ID が ``document.stable_id`` 由来であることを構造的に保証する。
    """
    content = DocumentContent.model_validate(_load_json("document.json"))
    assign_block_ids(content.sections)
    return content


def _block_by_id(content: DocumentContent) -> dict[str, Block]:
    return {blk.id: blk for _sec, blk in content.iter_blocks()}


def _block_top_section(content: DocumentContent) -> dict[str, str]:
    """block_id -> トップレベルセクション ID の写像。"""
    mapping: dict[str, str] = {}

    def walk(top_id: str, sec: Any) -> None:
        for blk in sec.blocks:
            mapping[blk.id] = top_id
        for sub in sec.sections:
            walk(top_id, sub)

    for top in content.sections:
        walk(top.id, top)
    return mapping


# --- reset(本シード由来データのみ削除) ------------------------------------------
async def _reset(session: AsyncSession, dev_id: str) -> None:
    # スケール複製(dev 所有・private・タイトル接頭辞一致)を先に削除。
    await session.execute(
        text(
            "DELETE FROM papers WHERE owner_user_id = :uid AND visibility = 'private' "
            "AND title LIKE 'Flow Straight and Fast%'"
        ),
        {"uid": dev_id},
    )
    # 本体(RF 論文)。cascade で revisions/sets/units/source_assets/library_items→chat 等が消える。
    await session.execute(text("DELETE FROM papers WHERE arxiv_id = :aid"), {"aid": ARXIV_ID})
    await session.flush()


# --- users -------------------------------------------------------------------------
async def _get_or_create_user(session: AsyncSession, email: str, display_name: str) -> User:
    existing = (await session.execute(select(User).where(User.email == email))).scalars().first()
    if existing is not None:
        return existing
    user = User(email=email, display_name=display_name)
    session.add(user)
    await session.flush()
    return user


# --- source assets(DB 行 + MinIO ベストエフォートアップロード) --------------------
async def _upload_best_effort(
    storage: S3Storage, bucket: str, key: str, body: bytes, ct: str
) -> None:
    try:
        await storage.put(bucket, key, body, content_type=ct)
    except Exception as exc:
        print(f"  [warn] asset upload skipped ({key}): {exc}")


async def _insert_source_assets(session: AsyncSession, storage: S3Storage, paper_id: str) -> None:
    specs = [
        (
            "arxiv_html",
            "arxiv-abs.html",
            "text/html",
            StorageKeys.arxiv_html(paper_id, SOURCE_VERSION),
            "https://arxiv.org/abs/2209.03003",
        ),
        (
            "arxiv_latex",
            "eprint.tar.gz",
            "application/gzip",
            StorageKeys.latex_tar(paper_id, SOURCE_VERSION),
            "https://arxiv.org/e-print/2209.03003",
        ),
    ]
    for kind, fname, ct, key, src_url in specs:
        body = (FIXTURE_DIR / "assets" / fname).read_bytes()
        session.add(
            SourceAsset(
                paper_id=paper_id,
                kind=kind,
                source_url=src_url,
                source_version=SOURCE_VERSION,
                storage_key=key,
                content_type=ct,
                byte_size=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        )
        await _upload_best_effort(storage, storage.sources_bucket, key, body, ct)
    # サムネイル(assets バケット)
    thumb = (FIXTURE_DIR / "assets" / "thumbnail.png").read_bytes()
    thumb_key = StorageKeys.thumbnail(paper_id)
    await _upload_best_effort(storage, storage.assets_bucket, thumb_key, thumb, "image/png")

    # 本文・記事フィクスチャが参照する図アセット(legacy key)。既存 document.json は
    # `figures/fig-1.png` 形式を持つため、同じキーに実体を補填する。
    for path in sorted((FIXTURE_DIR / "assets").glob("fig-*.png")):
        await _upload_best_effort(
            storage,
            storage.assets_bucket,
            f"figures/{path.name}",
            path.read_bytes(),
            "image/png",
        )
    explainer = FIXTURE_DIR / "assets" / "explainer-0.png"
    if explainer.exists():
        await _upload_best_effort(
            storage,
            storage.assets_bucket,
            "renders/explainer/seed/v1.png",
            explainer.read_bytes(),
            "image/png",
        )


# --- translation sets / units ------------------------------------------------------
def _glossary_snapshot() -> list[dict[str, Any]]:
    terms = _load_json("glossary_global.json").get("terms", [])
    return [
        {
            "source_term": t["source_term"],
            "target_term": t["target_term"],
            "policy": t.get("policy", "translate"),
            "origin": "global",
        }
        for t in terms
    ]


def _make_unit(
    set_id: str, block: Block, payload: dict[str, Any], *, state: str
) -> TranslationUnit:
    return TranslationUnit(
        set_id=set_id,
        block_id=block.id,
        source_hash=encode_block(block.model_dump()).source_hash,
        content_ja=payload.get("content_ja", []),
        text_ja=payload.get("text_ja", ""),
        state=state,
        quality_flags=list(payload.get("quality_flags", [])),
        model=SEED_MODEL,
    )


async def _insert_translations(
    session: AsyncSession,
    revision_id: str,
    content: DocumentContent,
    dev_id: str,
    *,
    full: bool,
) -> None:
    blocks = _block_by_id(content)
    top = _block_top_section(content)
    natural = _load_json("translation_natural.json")
    literal = _load_json("translation_literal.json")
    snapshot = _glossary_snapshot()

    def in_default_scope(block_id: str) -> bool:
        return full or top.get(block_id) in DEFAULT_TRANSLATED_TOP_SECTIONS

    # --- natural shared ---
    covered = [bid for bid in natural if bid in blocks and in_default_scope(bid)]
    natural_status = "complete" if full else "partial"
    natural_set = TranslationSet(
        revision_id=revision_id,
        style="natural",
        scope="shared",
        glossary_snapshot=snapshot,
        status=natural_status,
    )
    session.add(natural_set)
    await session.flush()
    for bid in covered:
        session.add(_make_unit(natural_set.id, blocks[bid], natural[bid], state="machine"))

    # --- literal shared(§1 のみ・オンデマンド途中状態を再現)---
    literal_set = TranslationSet(
        revision_id=revision_id,
        style="literal",
        scope="shared",
        glossary_snapshot=snapshot,
        status="partial",
    )
    session.add(literal_set)
    await session.flush()
    for bid, payload in literal.items():
        if bid in blocks:
            session.add(_make_unit(literal_set.id, blocks[bid], payload, state="machine"))

    # --- personal fork(dev・自然訳の 1 unit を edited で上書き)---
    fork_bid = "blk-0-p1-5d87"
    if fork_bid in blocks and fork_bid in natural:
        personal_set = TranslationSet(
            revision_id=revision_id,
            style="natural",
            scope="personal",
            user_id=dev_id,
            base_set_id=natural_set.id,
            glossary_snapshot=snapshot,
            status="partial",
        )
        session.add(personal_set)
        await session.flush()
        edited = dict(natural[fork_bid])
        edited_text = (
            "整流フロー(rectified flow)は 2 分布間の輸送写像を学ぶ、実に単純な手法である。"
        )
        edited["content_ja"] = [{"t": "text", "v": edited_text}]
        edited["text_ja"] = edited_text
        session.add(_make_unit(personal_set.id, blocks[fork_bid], edited, state="edited"))


# --- library item + chat -----------------------------------------------------------
async def _insert_library_and_chat(
    session: AsyncSession, revision_id: str, paper_id: str, dev_id: str, thumb_key: str
) -> None:
    item = LibraryItem(
        user_id=dev_id,
        paper_id=paper_id,
        status="reading",
        priority="high",
        tags=["生成モデル", "最適輸送", "輪読会"],
        suggested_tags=["拡散モデル"],
        one_line_note="直線経路で 1 ステップ生成。reflow が肝。",
        understanding=3,
        importance="high",
        reading_position={
            "revision_id": revision_id,
            "block_id": "blk-2-1-p1-9eca",
            "view_mode": "translation",
        },
        total_active_seconds=42 * 60,
        thumbnail_key=thumb_key,
    )
    session.add(item)
    await session.flush()

    chat = _load_json("chat.json")
    thread = ChatThread(
        library_item_id=item.id,
        title=chat["thread"].get("title", "メイン"),
        is_main=chat["thread"].get("is_main", True),
    )
    session.add(thread)
    await session.flush()

    for msg in chat["messages"]:
        content = msg["content"]
        session.add(
            ChatMessage(
                thread_id=thread.id,
                role=msg["role"],
                content=content,
                text_plain=chat_content_to_plain(content),
                context_anchors=_anchors_with_revision(msg.get("context_anchors", []), revision_id),
                evidence_anchors=_anchors_with_revision(
                    msg.get("evidence_anchors", []), revision_id
                ),
                status=msg.get("status", "complete"),
                error=msg.get("error"),
                provider=msg.get("provider", ""),
                model=msg.get("model", ""),
            )
        )


def _anchors_with_revision(anchors: list[dict[str, Any]], revision_id: str) -> list[dict[str, Any]]:
    return [{**a, "revision_id": revision_id} for a in anchors]


# --- scale dummies -----------------------------------------------------------------
async def _insert_scale_dummies(
    session: AsyncSession, dev_id: str, base_title: str, scale: int
) -> None:
    for k in range(1, scale + 1):
        status = STATUS_CYCLE[k % len(STATUS_CYCLE)]
        paper = Paper(
            title=f"{base_title} (サンプル複製 {k})",
            authors=[{"name": "Xingchang Liu"}],
            abstract="スケールテスト用のダミー書誌。",
            license="unknown",
            visibility="private",
            owner_user_id=dev_id,
            bib_estimated=True,
        )
        session.add(paper)
        await session.flush()
        session.add(
            LibraryItem(
                user_id=dev_id,
                paper_id=paper.id,
                status=status,
                priority=("high", "mid", "low")[k % 3],
                tags=[_STATUS_LABELS[status]],
                queue_order=k,
                one_line_note="",
            )
        )


# --- orchestration -----------------------------------------------------------------
async def seed_rectified_flow(
    session: AsyncSession, *, reset: bool, full: bool, scale: int
) -> str | None:
    """Rectified Flow シードを投入する。投入した paper.id を返す(スキップ時 None)。"""
    dev = await _get_or_create_user(session, DEV_EMAIL, "開発ユーザー")
    dev_id = dev.id
    # 共有・担当テスト用の 2 人目ユーザー(plans/12 §14.2)
    await _get_or_create_user(session, MEMBER_EMAIL, "メンバー")

    existing = (
        (await session.execute(select(Paper).where(Paper.arxiv_id == ARXIV_ID))).scalars().first()
    )
    if existing is not None:
        if not reset:
            print(f"[seed] {ARXIV_ID} は投入済み。--reset で再投入します。スキップ。")
            return None
        await _reset(session, dev_id)

    bib = _load_json("bib.json")
    content = _load_document()

    published_on = bib.get("published_on")
    published_date = dt.date.fromisoformat(published_on) if published_on else None
    paper = Paper(
        arxiv_id=bib["arxiv_id"],
        doi=bib.get("doi"),
        title=bib["title"],
        authors=bib["authors"],
        abstract=bib["abstract"],
        abstract_ja=bib.get("abstract_ja"),
        summary_lines=bib.get("summary_lines"),
        published_on=published_date,
        venue=bib.get("venue"),
        arxiv_categories=bib.get("arxiv_categories", []),
        license=bib["license"],
        visibility="public",
        latest_version=bib.get("latest_version"),
        official_repo_url=bib.get("official_repo_url"),
        extracted_terms=bib.get("extracted_terms", []),
    )
    session.add(paper)
    await session.flush()
    paper_id = paper.id
    thumb_key = StorageKeys.thumbnail(paper_id)
    paper.thumbnail_key = thumb_key

    storage = S3Storage(get_settings())
    await _insert_source_assets(session, storage, paper_id)

    revision = DocumentRevision(
        paper_id=paper_id,
        source_version=SOURCE_VERSION,
        parser_version=PARSER_VERSION,
        quality_level=content.quality_level,
        source_format="arxiv_html",
        content=content.model_dump(mode="json", exclude_none=True),
        stats={"block_count": len(list(content.iter_blocks())), "quality": content.quality_level},
    )
    session.add(revision)
    await session.flush()
    revision_id = revision.id
    paper.latest_revision_id = revision_id

    await rebuild_block_search_index(session, revision_id, content)
    await _insert_translations(session, revision_id, content, dev_id, full=full)
    await _insert_library_and_chat(session, revision_id, paper_id, dev_id, thumb_key)

    if scale > 0:
        await _insert_scale_dummies(session, dev_id, bib["title"], scale)

    await session.commit()
    return paper_id


async def _run(sample: str, *, reset: bool, full: bool, scale: int) -> None:
    if sample != "rectified-flow":
        raise SystemExit(f"unknown sample: {sample}")
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        async with maker() as session:
            paper_id = await seed_rectified_flow(session, reset=reset, full=full, scale=scale)
    finally:
        await engine.dispose()
    if paper_id is not None:
        mode = "全文訳" if full else "要旨+先頭セクション訳"
        extra = f" / スケール複製 {scale} 件" if scale else ""
        print(f"[seed] {ARXIV_ID} 完了({mode}{extra}) paper_id={paper_id}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m yakudoku_api.seed", description="開発シード投入"
    )
    parser.add_argument("--sample", required=True, choices=["rectified-flow"])
    parser.add_argument("--reset", action="store_true", help="本シード由来データを削除して再投入")
    parser.add_argument("--scale", type=int, default=0, help="ライブラリのダミー件数を N 件水増し")
    parser.add_argument("--full", action="store_true", help="全対象セクションを訳済みにする")
    args = parser.parse_args(argv)
    asyncio.run(_run(args.sample, reset=args.reset, full=args.full, scale=args.scale))


if __name__ == "__main__":
    main()
