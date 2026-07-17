"""``kind='vocab_extract'`` ジョブ: AI 単語抽出(S7)。

docs/superpowers/specs/2026-07-16-ai-word-extraction-design.md。

- ``POST /api/library-items/{id}/vocab-candidates/extract`` から enqueue される単発ジョブ。
  論文本文(最新リビジョン)を LLM に渡し、重要語・難語・コロケーション・イディオムの候補を
  structured 出力(schema ``vocab_candidates_v1``)で受け取り、``vocab_candidates`` に保存する。
- **提案のみ(P6)**: ここでは本物の ``vocab_entries`` は作らない。ユーザーが accept したときに
  API 側で既存の ``POST /api/vocab`` フローに載せて 9 フィールド生成を回す。
- **fail-closed**: LLM 出力は信用しない。block_id が実在し、term がそのブロック本文に実在し、
  kind が値域内の候補だけを残す。文脈センテンスとハイライト位置はサーバー側で導出する。
- **重複排除**: 既に語彙帳(``vocab_entries``。ユーザー横断。docs/11 §1)にある語、および既に
  同一論文の候補(status 問わず = dismissed も含む)にある語は提案しない。
- LLM ルータは ``ctx['router']``(task='vocab' を再利用。docs/11 §8 の小型モデル階層)。
  チェーン全滅(``ProviderChainExhausted``)時はジョブを failed 確定し、候補は作らない(P3)。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from alinea_core.db.models import Job, LibraryItem, Paper, VocabCandidate, VocabEntry
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.jobs.store import JobStore
from alinea_llm.errors import ProviderChainExhausted
from alinea_llm.types import ContentPart, JsonSchemaSpec, LLMRequest, Message
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

VOCAB_TASK = "vocab"
_SCHEMA_NAME = "vocab_candidates_v1"

# 1 回の抽出で保存する候補の上限(spec 決定 3。一度に見直せる件数)。ワーカー側で厳密に切り詰める。
MAX_CANDIDATES = 20
# structured 出力スキーマの構造的な上限(product 上限より緩く取り、超過分はワーカーが切り詰める)。
_SCHEMA_MAX_ITEMS = 60
# LLM に渡す本文の上限文字数(コスト・レイテンシ抑制)。
MAX_CONTEXT_CHARS = 24_000
# 文脈センテンスの前後マージン(文分割できないときの窓)。
_CONTEXT_WINDOW = 160
_KINDS = ("word", "collocation", "idiom")
_SENTENCE_ENDINGS = (". ", "? ", "! ")

_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": _SCHEMA_MAX_ITEMS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["term", "kind", "block_id"],
                "properties": {
                    "term": {"type": "string", "maxLength": 80},
                    "kind": {"enum": list(_KINDS)},
                    "block_id": {"type": "string", "maxLength": 120},
                    "reason": {"type": "string", "maxLength": 120},
                },
            },
        }
    },
}

VOCAB_EXTRACT_SYSTEM = "\n".join(
    [
        "あなたは学術英語の語彙学習コンテンツ作成者です。",
        "与えられた論文本文から、日本語話者の学習者にとって重要・難解で学ぶ価値のある英語語彙を"
        "抽出し、候補を JSON で出力します。",
        "",
        "## 抽出の方針",
        "- 一般学術英語(単語 / コロケーション / イディオム)を対象にする。専門用語・固有名詞・"
        "略語・数式・記号・URL は対象外(それらは用語集の領分)。",
        "- 各候補には、その語が実際に出現した block_id を必ず付ける。本文に無い語は出さない。",
        "- kind: 単一語なら word、決まった語の組合せなら collocation、字面から意味が推測しにくい"
        "定型表現なら idiom。",
        f"- 候補は最大 {MAX_CANDIDATES} 件。重要度の高い順に絞る。",
        "- reason(任意): なぜ学ぶ価値があるかを一言(日本語、短く)。",
    ]
)

_FRIENDLY_FAILURE_MESSAGE = (
    "単語候補の抽出に失敗しました。しばらくしてから「AI候補を抽出」をお試しください。"
)


def _iter_block_text(content: DocumentContent) -> dict[str, str]:
    """block_id -> 検索用平文。paragraph 系のブロックのみを対象にする。"""
    out: dict[str, str] = {}
    for _sec, blk in content.iter_blocks():
        if blk.type in ("figure", "table", "equation", "code", "reference_entry"):
            continue
        text = block_to_plain(blk)
        if text:
            out[blk.id] = text
    return out


def _render_context(block_text: dict[str, str]) -> str:
    """LLM に渡す ``[block_id] text`` 平文(上限まで)。"""
    lines: list[str] = []
    used = 0
    for block_id, text in block_text.items():
        line = f"[{block_id}] {text}"
        if used + len(line) > MAX_CONTEXT_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _context_sentence(block_text: str, lo: int, hi: int) -> tuple[str, int, int]:
    """term(``block_text[lo:hi]``)を含むセンテンスとその中でのハイライト位置を返す。"""
    start = 0
    for ending in _SENTENCE_ENDINGS:
        idx = block_text.rfind(ending, 0, lo)
        if idx != -1:
            start = max(start, idx + len(ending))
    end = len(block_text)
    for ending in _SENTENCE_ENDINGS:
        idx = block_text.find(ending, hi)
        if idx != -1:
            end = min(end, idx + 1)  # 句読点まで含める
    # 極端に長い段落(文分割できない)には窓で保険をかける。
    if end - start > _CONTEXT_WINDOW * 4:
        start = max(0, lo - _CONTEXT_WINDOW)
        end = min(len(block_text), hi + _CONTEXT_WINDOW)
    window = block_text[start:end]
    sentence = window.strip()
    # 対象語の絶対位置 lo は window 内では lo-start。strip で落ちた先頭空白ぶんを補正する
    # (find だと同語の先行出現に吸われてハイライトがズレるため、実位置から算出する)。
    stripped_lead = len(window) - len(window.lstrip())
    hl_start = max(0, (lo - start) - stripped_lead)
    hl_end = hl_start + (hi - lo)
    return sentence, hl_start, hl_end


def _build_request(context: str) -> LLMRequest:
    return LLMRequest(
        model="",
        system=[ContentPart(type="text", text=VOCAB_EXTRACT_SYSTEM)],
        messages=[Message(role="user", parts=[ContentPart(type="text", text=context)])],
        max_output_tokens=2048,
        effort="none",
        json_schema=JsonSchemaSpec(name=_SCHEMA_NAME, json_schema=_JSON_SCHEMA),
        timeout_s=45.0,
        metadata={"task": VOCAB_TASK},
    )


def _normalize_term(term: str) -> str:
    return term.strip().lower()


async def _existing_terms(session: AsyncSession, *, user_id: str, item_id: str) -> set[str]:
    """再提案しない語(既存の語彙帳 + 既存候補)の正規化集合。"""
    entry_terms = (
        await session.execute(
            select(func.lower(func.trim(VocabEntry.term))).where(VocabEntry.user_id == user_id)
        )
    ).scalars().all()
    candidate_terms = (
        await session.execute(
            select(func.lower(func.trim(VocabCandidate.term))).where(
                VocabCandidate.library_item_id == item_id
            )
        )
    ).scalars().all()
    return {t for t in [*entry_terms, *candidate_terms] if t}


async def _mark_failed(store: JobStore, job: Job, message: str) -> None:
    """チェーン全滅時: ジョブを failed 確定する(候補は作らない。P3)。

    LLM 失敗時点ではまだ候補を session へ add していないので rollback は不要。
    """
    session = store.session
    stage = job.stage  # rollback/expire 前に確定させる。
    job.status = "failed"
    job.error = json.dumps(
        {"stage": stage, "code": "provider_chain_exhausted", "message": message},
        ensure_ascii=False,
    )
    job.finished_at = dt.datetime.now(dt.UTC)
    await session.commit()


async def run_extract_vocab_candidates(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='vocab_extract'`` のハンドラ。"""
    session = store.session
    payload = job.payload or {}
    library_item_id = str(payload.get("library_item_id", ""))
    item = await session.get(LibraryItem, library_item_id) if library_item_id else None
    if item is None:
        await store.succeed(str(job.id), {"candidates_created": 0, "skipped": "no_item"})
        return

    paper = await session.get(Paper, item.paper_id)
    revision = await get_latest_paper_revision(session, paper) if paper is not None else None
    if revision is None:
        await store.succeed(str(job.id), {"candidates_created": 0, "skipped": "no_revision"})
        return

    content = DocumentContent.model_validate(revision.content)
    block_text = _iter_block_text(content)
    if not block_text:
        await store.succeed(str(job.id), {"candidates_created": 0, "skipped": "no_text"})
        return

    request = _build_request(_render_context(block_text))
    router = ctx["router"]
    try:
        resp = await router.complete(
            VOCAB_TASK,
            request=request,
            mode="structured",
            user_id=str(job.user_id) if job.user_id else None,
            library_item_id=str(job.library_item_id) if job.library_item_id else None,
            job_id=str(job.id),
        )
    except ProviderChainExhausted:
        await _mark_failed(store, job, _FRIENDLY_FAILURE_MESSAGE)
        return

    proposed = (resp.parsed or {}).get("candidates") or []
    skip = await _existing_terms(session, user_id=str(item.user_id), item_id=str(item.id))

    created = 0
    for raw in proposed:
        if created >= MAX_CANDIDATES:
            break
        if not isinstance(raw, dict):
            continue
        term = str(raw.get("term", "")).strip()
        kind = str(raw.get("kind", ""))
        block_id = str(raw.get("block_id", ""))
        reason = str(raw.get("reason", "") or "")
        norm = _normalize_term(term)
        if not term or kind not in _KINDS or block_id not in block_text:
            continue  # fail-closed: 実在しない block / 不正な kind。
        text = block_text[block_id]
        pos = text.lower().find(norm)
        if pos == -1:
            continue  # fail-closed: term がブロック本文に実在しない。
        if norm in skip:
            continue  # dedup: 既存語彙 / 既存候補。
        sentence, hl_start, hl_end = _context_sentence(text, pos, pos + len(term))
        candidate = VocabCandidate(
            user_id=str(item.user_id),
            library_item_id=str(item.id),
            term=term,
            kind=kind,
            context_anchor={
                "revision_id": str(revision.id),
                "block_id": block_id,
                "start": None,
                "end": None,
                "quote": text[pos : pos + len(term)],
                "side": "source",
            },
            context_sentence=sentence,
            context_hl_start=hl_start,
            context_hl_end=hl_end,
            reason=reason,
            status="pending",
        )
        try:
            async with session.begin_nested():  # SAVEPOINT: 1 行の衝突で全体を巻き戻さない。
                session.add(candidate)
                await session.flush()
        except IntegrityError:
            # 並行実行での一意制約衝突(冪等)。この行だけ捨てる。
            skip.add(norm)
            continue
        skip.add(norm)  # 同一抽出内での重複語も畳む。
        created += 1

    await session.commit()
    await store.succeed(str(job.id), {"candidates_created": created})


__all__ = [
    "MAX_CANDIDATES",
    "VOCAB_EXTRACT_SYSTEM",
    "run_extract_vocab_candidates",
]
