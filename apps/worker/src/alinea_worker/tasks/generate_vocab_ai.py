"""``kind='vocab'`` ジョブ: 語彙 AI 生成(plans/07 §7、docs/11 §4・§8)。

- ``POST /api/vocab``(pending 保存)直後に enqueue される単発ジョブ。structured 出力
  (schema ``vocab_content_v1``。plans/07 §7.2 逐語)で 9 フィールド(``kind`` 含む)を生成し、
  ``vocab_entries.edited_fields`` に含まれないものだけ書き込む。
- 再生成(``POST /api/vocab/{id}/regenerate``)も同じハンドラを使う。``payload["fields"]`` が
  指定されていれば対象をさらに絞る(``None`` = 未編集フィールド全部。``edited_fields`` は
  常に除外する二重防御。plans/07 §7.1)。
- チェーン全滅(``ProviderChainExhausted``)時は ``vocab_entries.generation_status='failed'`` +
  ``generation_error`` を保存し、見出し語・文脈・出典は消さない(P3。docs/11 §2)。ジョブ自体も
  明示的に ``status='failed'`` として確定させ、自動リトライで同じ失敗を繰り返させない
  (:mod:`alinea_worker.tasks.translate` の ``RetranslateBlockedError`` と同方針)。
- LLM ルータは ``ctx['router']``(worker 起動時に task='translation' で 1 本構築される共有
  ルータ。plans/04 §9。task 別チェーン切替・per-user 計測は未実装 — followups。
  :mod:`alinea_worker.bootstrap` の既知の制約と同じ)。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from alinea_core.db.models import Job, LibraryItem, Paper, VocabEntry
from alinea_core.jobs.store import JobStore
from alinea_llm.errors import ProviderChainExhausted
from alinea_llm.types import ContentPart, JsonSchemaSpec, LLMRequest, Message
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VOCAB_TASK = "vocab"
_SCHEMA_NAME = "vocab_content_v1"

# plans/07 §7.2(DB カラム名と 1:1。kind を含む 9 種)。
ALL_FIELDS: tuple[str, ...] = (
    "kind",
    "pos_label",
    "ipa",
    "meaning_short",
    "meaning_long",
    "interpretation",
    "etymology",
    "mnemonic",
    "related_forms",
)

_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(ALL_FIELDS),
    "properties": {
        "kind": {"enum": ["word", "collocation", "idiom"]},
        "pos_label": {"type": "string", "maxLength": 12},
        "ipa": {"type": "string", "maxLength": 60},
        "meaning_short": {"type": "string", "maxLength": 30},
        "meaning_long": {"type": "string", "maxLength": 200},
        "interpretation": {"type": "string", "maxLength": 260},
        "etymology": {"type": "string", "maxLength": 200},
        "mnemonic": {"type": "string", "maxLength": 200},
        "related_forms": {"type": "string", "maxLength": 200},
    },
}

# plans/07 §7.2 VOCAB_SYSTEM(逐語。1 行 100 文字制限のため行内で分割するが文言は不変)。
VOCAB_SYSTEM = "\n".join(
    [
        "あなたは学術英語の語彙学習コンテンツ作成者です。"
        "論文の文脈センテンスの中で使われた語彙について、"
        "日本語学習者向けの学習コンテンツを JSON で出力します。",
        "",
        "## フィールドの書き方",
        "- kind: 語彙の種別。単一語なら word、決まった語の組合せなら collocation、"
        "字面から意味が推測しにくい定型表現なら idiom。",
        "- pos_label: 細かい分類ラベル(例: 句動詞 / 他動詞 / 副詞 / 形容詞 / 前置詞句)。",
        "- ipa: 発音記号。スラッシュで囲む(例: /ˌbɔɪl ˈdaʊn tə/)。",  # noqa: RUF001
        "- meaning_short: この文脈での語義の短形(一覧表示用、30 文字以内。"
        "例: 要するに〜に帰着する)。",
        "- meaning_long: この文脈での語義の長形。"
        "辞書義の羅列ではなく、この文でどういう意味かを説明する。"
        "キーとなる訳語を **太字** にし、「この文では「…」」の形で文脈への当てはめを 1 文添える。",
        "- interpretation: 解釈のしかた。"
        "次に似た表現に出会ったとき自力で読めるようになる「読み方の型」を"
        "解説する(例: 句動詞は動詞の物理イメージ+方向詞で読む、のような分解)。",
        "- etymology: 語源メモ。語根と同族語を 1〜2 文で"
        "(例: boil ← ラテン語 bullīre(泡立つ)。bubble、ebullient と同族。)。",
        "- mnemonic: 覚えるコツ。具体的なイメージ・場面による記憶フック"
        "(例: カレーを煮詰めるイメージ。枝葉が飛んで本質だけが残る。)。",
        "- related_forms: よく出る形・近い表現。頻出パターンと類義表現をスラッシュ区切りで"
        "(例: it boils down to whether… / come down to(ほぼ同義)/ amount to)。",
        "",
        "## 規則",
        "- すべて日本語で書く(英語表現の例示部分を除く)。",
        "- 文脈センテンスでの意味を最優先する。多義語でも文脈外の語義は書かない。",
        "- 誇張・絵文字を使わない。落ち着いた学習ノートの文体。",
    ]
)

_FRIENDLY_FAILURE_MESSAGE = (
    "語彙コンテンツの生成に失敗しました。しばらくしてから「生成を再試行」をお試しください。"
)

_SECTION_LABEL_SQL = text(
    "SELECT section_label FROM block_search_index "
    "WHERE revision_id = CAST(:rid AS uuid) AND block_id = :bid LIMIT 1"
)


def _user_prompt(entry: VocabEntry, *, paper_title: str, section_label: str) -> str:
    source = " ".join(p for p in (paper_title, section_label) if p)
    return (
        f"語彙: {entry.term}\n"
        f"文脈センテンス: {entry.context_sentence}"
        f"(対象語は {entry.context_hl_start}〜{entry.context_hl_end} 文字目)\n"
        f"出典: {source}"
    )


async def _paper_context(session: AsyncSession, entry: VocabEntry) -> tuple[str, str]:
    """出典行の素材(論文タイトル・節ラベル)。プロンプト用途のみで欠損しても続行する。"""
    item = await session.get(LibraryItem, entry.library_item_id)
    paper_title = ""
    if item is not None:
        paper = await session.get(Paper, item.paper_id)
        paper_title = paper.title if paper is not None else ""

    anchor = entry.context_anchor if isinstance(entry.context_anchor, dict) else {}
    revision_id = str(anchor.get("revision_id", ""))
    block_id = str(anchor.get("block_id", ""))
    section_label = ""
    if revision_id and block_id:
        row = (
            await session.execute(_SECTION_LABEL_SQL, {"rid": revision_id, "bid": block_id})
        ).first()
        if row is not None:
            section_label = str(row[0])
    return paper_title, section_label


def _build_request(entry: VocabEntry, *, paper_title: str, section_label: str) -> LLMRequest:
    user_text = _user_prompt(entry, paper_title=paper_title, section_label=section_label)
    return LLMRequest(
        model="",  # Router がチェーンの model で上書きする
        system=[ContentPart(type="text", text=VOCAB_SYSTEM)],
        messages=[
            Message(role="user", parts=[ContentPart(type="text", text=user_text)]),
        ],
        max_output_tokens=2048,
        effort="none",
        json_schema=JsonSchemaSpec(name=_SCHEMA_NAME, json_schema=_JSON_SCHEMA),
        timeout_s=30.0,
        metadata={"task": VOCAB_TASK},
    )


def _target_fields(entry: VocabEntry, requested: list[str] | None) -> set[str]:
    edited = {f for f in (entry.edited_fields or []) if f in ALL_FIELDS}
    base = {f for f in requested if f in ALL_FIELDS} if requested is not None else set(ALL_FIELDS)
    return base - edited


async def _mark_failed(store: JobStore, job: Job, entry: VocabEntry, message: str) -> None:
    """チェーン全滅時: 語彙は残しつつジョブを failed 確定する(P3。docs/11 §2)。"""
    session = store.session
    entry.generation_status = "failed"
    entry.generation_error = message
    await session.commit()

    job.status = "failed"
    job.error = json.dumps(
        {"stage": job.stage, "code": "provider_chain_exhausted", "message": message},
        ensure_ascii=False,
    )
    job.finished_at = dt.datetime.now(dt.UTC)
    await session.commit()


async def run_generate_vocab_ai(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='vocab'`` ジョブのハンドラ(新規生成・regenerate 共通)。"""
    session = store.session
    payload = job.payload or {}
    vocab_id = str(payload.get("vocab_id", ""))
    entry = await session.get(VocabEntry, vocab_id)
    if entry is None:
        raise LookupError(f"vocab entry not found: {vocab_id}")

    requested = payload.get("fields")
    targets = _target_fields(entry, requested if isinstance(requested, list) else None)

    paper_title, section_label = await _paper_context(session, entry)
    request = _build_request(entry, paper_title=paper_title, section_label=section_label)
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
        await _mark_failed(store, job, entry, _FRIENDLY_FAILURE_MESSAGE)
        return

    data = resp.parsed or {}
    for name in targets:
        if name in data and data[name] is not None:
            setattr(entry, name, data[name])
    entry.generation_status = "complete"
    entry.generation_error = None
    await session.commit()
    await store.succeed(str(job.id), {"vocab_id": str(entry.id), "fields": sorted(targets)})


__all__ = ["ALL_FIELDS", "VOCAB_SYSTEM", "run_generate_vocab_ai"]
