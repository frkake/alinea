"""読解チャット(M0-21 / plans/07 §2・plans/03 §10・docs/05)。

- PY-CHAT-01: 文脈構築(system[0] プリアンブル + system[1] 論文文脈 + 履歴 + 選択周辺)。
- PY-CHAT-02: ストリーム変換([[evidence:ID]] → [[ev:n]] + evidence + aside、DB segments)。
- PY-CHAT-03: 壊れた根拠の除去(実在しない block_id は除く。P1)。
- PY-CHAT-04: 定型アクション(常設5 + 入力候補2 + 導線3、逐語テンプレート)。
- PY-CHAT-05: メインスレッド自動作成(GET threads)。
- PY-CHAT-06: regenerate(旧回答を残し新回答を追記。P3)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.chat.context_builder import build_chat_request, render_document_context
from yakudoku_api.chat.evidence import BlockRow, EvidenceValidator, derive_display, verify_evidence
from yakudoku_api.chat.prompts import (
    PERSISTENT_QUICK_ACTIONS,
    QUICK_ACTION_TEMPLATES,
    SUGGESTED_QUICK_ACTIONS,
    resolve_user_content,
)
from yakudoku_api.chat.stream_pipeline import SseEvent, StreamPipeline
from yakudoku_api.deps import get_settings_dep
from yakudoku_api.routers.chat import get_chat_provider_factory
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_api.settings import ApiSettings
from yakudoku_core.db.models import DocumentRevision, LibraryItem, Paper
from yakudoku_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from yakudoku_core.document.inlines import Inline
from yakudoku_core.search.rebuild import rebuild_block_search_index
from yakudoku_llm.testing.fake_provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# 共通データ
# ---------------------------------------------------------------------------
def _make_document() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(
                        id="blk-1-p1-aaaa",
                        type="paragraph",
                        inlines=[Inline(t="text", v="Rectified flow straightens the transport.")],
                    ),
                    Block(
                        id="blk-1-eq1-bbbb",
                        type="equation",
                        latex=r"\min_v \int_0^1 \|(X_1-X_0)-v(X_t,t)\|^2 dt",
                        number="1",
                    ),
                ],
            )
        ],
    )


def _validator() -> EvidenceValidator:
    return EvidenceValidator(
        "rev-1",
        [
            BlockRow("blk-real", "equation", "sec-2", "§2.1", None, "式(5)"),
            BlockRow("blk-para", "paragraph", "sec-2", "§2.1", 4, None),
        ],
    )


def _fake_factory(provider: str, api_key: str) -> FakeLLMProvider:
    return FakeLLMProvider(name=provider)


def _parse_sse(text: str) -> list[dict[str, str | None]]:
    frames: list[dict[str, str | None]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event: str | None = None
        data: str | None = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if event is not None:
            frames.append({"event": event, "data": data})
    return frames


# ---------------------------------------------------------------------------
# PY-CHAT-01: 文脈構築
# ---------------------------------------------------------------------------
def test_context_builder_packs_paper_and_history() -> None:
    content = _make_document()
    req = build_chat_request(
        content=content,
        revision_id="rev-1",
        title="Flow Straight and Fast",
        authors_short="Liu, Gong, Liu",
        venue_year="ICLR 2023",
        arxiv_id="2209.03003",
        user_content="この式の意味は?",
        history=[("user", "前の質問"), ("assistant", "前の回答 (§1 ¶1)")],
        context_anchors=[{"block_id": "blk-1-eq1-bbbb"}],
    )

    # system[0]: プリアンブル + 論文メタデータ、キャッシュ境界。
    sys0 = req.system[0].text or ""
    assert "訳読" in sys0
    assert "[[evidence:ブロックID]]" in sys0  # モデルへの根拠マーカー指示(§2.6)
    assert "Flow Straight and Fast" in sys0
    assert "2209.03003" in sys0
    assert req.system[0].cache_hint is True

    # system[1]: 論文コンテキスト(行頭 [block_id|位置])。原文を正とする。
    sys1 = req.system[1].text or ""
    assert sys1.startswith("# 論文コンテキスト")
    assert "[blk-1-p1-aaaa|§1 ¶1]" in sys1
    assert "[blk-1-eq1-bbbb|式(1)] $$" in sys1  # 数式は $$…$$、element_label 表記
    assert req.system[1].cache_hint is True

    # messages: 履歴(時系列)+ 今回の質問(選択周辺全文つき)。
    roles = [m.role for m in req.messages]
    assert roles == ["user", "assistant", "user"]
    user_text = req.messages[-1].parts[0].text or ""
    assert "この式の意味は?" in user_text
    assert "# 選択箇所の周辺" in user_text
    assert "blk-1-eq1-bbbb" in user_text  # ±2 ブロックにアンカーが含まれる

    assert req.prompt_cache_key == "chat:rev-1"
    assert req.metadata["task"] == "chat"
    assert req.max_output_tokens == 8192


def test_render_document_context_excludes_reference_entries() -> None:
    content = _make_document()
    content.sections[0].blocks.append(
        Block(id="blk-1-ref1-cccc", type="reference_entry", raw="Some reference")
    )
    rendered = render_document_context(content, "rev-1")
    assert "blk-1-ref1-cccc" not in rendered  # reference_entry は文脈に含めない(§2.2.2)


# ---------------------------------------------------------------------------
# PY-CHAT-02: ストリーム変換
# ---------------------------------------------------------------------------
def test_stream_pipeline_transforms_markers_asides_and_drops_ghosts() -> None:
    pipeline = StreamPipeline(_validator())
    model_text = (
        "整流フローの学習目的は最小二乗回帰に帰着します[[evidence:blk-real]]。"
        "存在しない根拠[[evidence:blk-ghost]]は消えます。"
        "<outside_knowledge>実装では t を一様サンプリングします。</outside_knowledge>"
    )
    events: list[SseEvent] = []
    for i in range(0, len(model_text), 20):  # delta 境界でマーカー/タグが分断される
        events.extend(pipeline.feed(model_text[i : i + 20]))
    events.extend(pipeline.finish())

    deltas = [e for e in events if e.event == "delta"]
    evidences = [e for e in events if e.event == "evidence"]

    # 実在する根拠のみ 1 件、display は §2.5.2(equation → element_label)。
    assert len(evidences) == 1
    assert evidences[0].data["ref"] == 1
    assert evidences[0].data["display"] == "式(5)"
    assert evidences[0].data["anchor"]["block_id"] == "blk-real"
    assert "display" not in evidences[0].data["anchor"]  # anchor は Anchor(display なし)

    all_delta_text = "".join(str(d.data["text"]) for d in deltas)
    assert "[[ev:1]]" in all_delta_text
    assert "[[ev:2]]" not in all_delta_text  # ghost は ref を割り当てられない
    assert "blk-ghost" not in all_delta_text
    assert "[[evidence:" not in all_delta_text  # 生マーカーは配信されない
    assert pipeline.dropped == 1

    # aside は label 付きで別ブロック。
    aside_deltas = [d for d in deltas if d.data.get("block_type") == "aside"]
    assert aside_deltas
    assert aside_deltas[0].data.get("label") == "outside_knowledge"
    assert aside_deltas[0].data["block_index"] > deltas[0].data["block_index"]

    # DB 層: segments(⟦A:n⟧)+ evidence_anchors(display なし)+ text_plain(展開)。
    seg_types = [s["type"] for s in pipeline.segments]
    assert "text" in seg_types
    assert "outside_knowledge" in seg_types
    md_all = " ".join(s["md"] for s in pipeline.segments)
    assert "⟦A:1⟧" in md_all
    anchors = pipeline.evidence_anchors_json()
    assert len(anchors) == 1
    assert anchors[0]["block_id"] == "blk-real"
    assert "display" not in anchors[0]
    assert "(式(5))" in pipeline.text_plain()  # ⟦A:1⟧ → (表示位置)


def test_stream_pipeline_auto_closes_unterminated_aside() -> None:
    pipeline = StreamPipeline(_validator())
    events = list(pipeline.feed("<speculation>著者は暗黙に仮定している"))
    events.extend(pipeline.finish())
    assert [s["type"] for s in pipeline.segments] == ["speculation"]
    aside = [e for e in events if e.data.get("block_type") == "aside"]
    assert aside and aside[0].data.get("label") == "speculation"


def test_derive_display_rules() -> None:
    eq = BlockRow("blk-e", "equation", "sec-2", "§2.1", None, "式(5)")
    para = BlockRow("blk-p", "paragraph", "sec-2", "§2.1", 4, None)
    assert derive_display(eq) == "式(5)"
    assert derive_display(para) == "§2.1 ¶4"
    assert derive_display(eq, context_chip=True) == "式(5) · §2.1"  # 1a 逐語


# ---------------------------------------------------------------------------
# PY-CHAT-03: 壊れた根拠の除去
# ---------------------------------------------------------------------------
async def test_broken_evidence_anchor_is_stripped() -> None:
    anchors = [{"block_id": "blk-real"}, {"block_id": "blk-nonexistent"}]
    verified = await verify_evidence(anchors, existing_block_ids={"blk-real"})
    assert verified == [{"block_id": "blk-real"}]  # 実在しない根拠は除去(P1)


# ---------------------------------------------------------------------------
# PY-CHAT-04: 定型アクション
# ---------------------------------------------------------------------------
def test_quick_action_templates_are_verbatim() -> None:
    assert set(PERSISTENT_QUICK_ACTIONS) == {
        "summary_3line",
        "beginner_explain",
        "contributions_limits",
        "experiment_setup",
        "implementation_points",
    }
    assert set(SUGGESTED_QUICK_ACTIONS) == {"expert_summary", "related_work_position"}
    assert len(QUICK_ACTION_TEMPLATES) == 10  # 常設5 + 入力候補2 + 導線3

    # 逐語テンプレート(§2.7)。quick_action 指定で本文展開、未指定は content そのまま。
    assert resolve_user_content("summary_3line", "") == QUICK_ACTION_TEMPLATES["summary_3line"]
    assert QUICK_ACTION_TEMPLATES["summary_3line"].startswith(
        "この論文を次の 3 行で要約してください。①課題"
    )
    assert "Markdown 表で整理" in QUICK_ACTION_TEMPLATES["experiment_setup"]
    assert "<outside_knowledge>" in QUICK_ACTION_TEMPLATES["beginner_explain"]
    assert resolve_user_content(None, "自由質問") == "自由質問"


# ---------------------------------------------------------------------------
# 統合フィクスチャ(DB + SSE エンドポイント)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def chat_ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[SimpleNamespace]:
    from yakudoku_api.main import app

    user = await upsert_user_by_email(
        db_session, f"chat-{uuid.uuid4().hex}@example.com", provider="email"
    )
    paper = Paper(
        title="Flow Straight and Fast",
        authors=["Liu", "Gong", "Liu"],
        visibility="private",
        owner_user_id=user.id,
        published_on=dt.date(2022, 9, 7),
        venue="ICLR 2023",
    )
    db_session.add(paper)
    await db_session.flush()
    content = _make_document()
    rev = DocumentRevision(
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(),
    )
    db_session.add(rev)
    await db_session.flush()
    paper.latest_revision_id = rev.id
    await rebuild_block_search_index(db_session, str(rev.id), content)
    item = LibraryItem(user_id=user.id, paper_id=paper.id, status="reading")
    db_session.add(item)
    await db_session.flush()
    await db_session.commit()

    token = await create_session(redis_client, user.id)
    client.cookies.set("yk_session", token)
    test_settings = ApiSettings(anthropic_api_key="test-op-key")
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[get_chat_provider_factory] = lambda: _fake_factory
    try:
        yield SimpleNamespace(user_id=str(user.id), item_id=str(item.id), revision_id=str(rev.id))
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)
        app.dependency_overrides.pop(get_chat_provider_factory, None)
        await db_session.rollback()
        await purge_user(db_session, str(user.id))
        await db_session.commit()


# ---------------------------------------------------------------------------
# PY-CHAT-05: メインスレッド自動作成
# ---------------------------------------------------------------------------
async def test_main_thread_is_auto_created(client: AsyncClient, chat_ctx: SimpleNamespace) -> None:
    resp = await client.get(f"/api/library-items/{chat_ctx.item_id}/chat/threads")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["is_main"] is True
    assert items[0]["title"] == "メイン"
    assert items[0]["message_count"] == 0

    # 冪等: 再取得しても 1 本のまま。
    again = await client.get(f"/api/library-items/{chat_ctx.item_id}/chat/threads")
    assert len(again.json()["items"]) == 1


# ---------------------------------------------------------------------------
# PY-CHAT-06: regenerate
# ---------------------------------------------------------------------------
async def test_regenerate_appends_new_answer(
    client: AsyncClient, chat_ctx: SimpleNamespace
) -> None:
    threads = (await client.get(f"/api/library-items/{chat_ctx.item_id}/chat/threads")).json()[
        "items"
    ]
    thread_id = threads[0]["id"]

    # 送信(SSE): start → delta* → done。
    resp = await client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={"content": "整流フローとは何ですか?"},
    )
    assert resp.status_code == 200
    frames = _parse_sse(resp.text)
    events = [f["event"] for f in frames]
    assert events[0] == "start"
    assert "delta" in events
    assert events[-1] == "done"

    msgs = (await client.get(f"/api/chat/threads/{thread_id}/messages")).json()["items"]
    assistants = [m for m in msgs if m["role"] == "assistant" and m["status"] == "complete"]
    assert assistants
    assert assistants[0]["blocks"]  # 回答本文がある(AI生成 = assistant ロール)
    asst_id = assistants[0]["id"]
    before = len(msgs)

    # 再生成: 旧回答は残し、新回答を新規メッセージとして追記(§10.4・P3)。
    resp2 = await client.post(f"/api/chat/messages/{asst_id}/regenerate", json={})
    assert resp2.status_code == 200
    assert any(f["event"] == "done" for f in _parse_sse(resp2.text))

    msgs2 = (await client.get(f"/api/chat/threads/{thread_id}/messages")).json()["items"]
    assert len(msgs2) == before + 1  # 新 assistant を追記(user は同一質問なので増えない)
    assert any(m["id"] == asst_id for m in msgs2)  # 旧回答は残る
    assert len([m for m in msgs2 if m["role"] == "assistant"]) == 2


# PY-CHAT-02: 選択アンカー + quick_action 付き送信の SSE、空内容の 400。
async def test_send_message_with_quick_action_and_anchor(
    client: AsyncClient, chat_ctx: SimpleNamespace
) -> None:
    threads = (await client.get(f"/api/library-items/{chat_ctx.item_id}/chat/threads")).json()[
        "items"
    ]
    thread_id = threads[0]["id"]

    resp = await client.post(
        f"/api/chat/threads/{thread_id}/messages",
        json={
            "content": "",
            "quick_action": "summary_3line",
            "context_anchors": [
                {
                    "revision_id": chat_ctx.revision_id,
                    "block_id": "blk-1-p1-aaaa",
                    "side": "source",
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    frames = _parse_sse(resp.text)
    assert frames[0]["event"] == "start"
    assert frames[-1]["event"] == "done"

    # 空内容 + 選択アンカー無し → 400。
    bad = await client.post(f"/api/chat/threads/{thread_id}/messages", json={"content": "   "})
    assert bad.status_code == 400


async def test_new_thread_crud_and_main_thread_undeletable(
    client: AsyncClient, chat_ctx: SimpleNamespace
) -> None:
    # メインを用意してから新規スレッド作成。
    main = (await client.get(f"/api/library-items/{chat_ctx.item_id}/chat/threads")).json()[
        "items"
    ][0]
    created = await client.post(
        f"/api/library-items/{chat_ctx.item_id}/chat/threads", json={"title": "数式の確認"}
    )
    assert created.status_code == 201
    thread = created.json()
    assert thread["is_main"] is False
    assert thread["title"] == "数式の確認"

    # PATCH でタイトル変更。
    patched = await client.patch(f"/api/chat/threads/{thread['id']}", json={"title": "実装メモ"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "実装メモ"

    # メインスレッドは削除不可(409)。
    del_main = await client.delete(f"/api/chat/threads/{main['id']}")
    assert del_main.status_code == 409

    # 非メインは削除可(204)。
    del_sub = await client.delete(f"/api/chat/threads/{thread['id']}")
    assert del_sub.status_code == 204
