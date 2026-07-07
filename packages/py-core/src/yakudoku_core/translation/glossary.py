"""用語集スナップショット凍結(plans/06 §8.1-§8.2・docs/03 §7)。

M0-17 の範囲は **スナップショット部のみ**(3 層マージの凍結・ハッシュ・プロンプト反映)。
3 層 CRUD・逆引き検索・訳語変更フローは M1-14/M1-15。

適用優先度: 論文ローカル(paper)> ユーザー(user)> グローバル既定(global)。翻訳時に適用
用語のスナップショットを凍結し、TranslationSet に記録する(再現性)。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yakudoku_core.db.models import Glossary, GlossaryTerm

# origin の優先順(数値が大きいほど優先)。docs/03 §7 の 3 層優先度。
_ORIGIN_PRIORITY = {"global": 0, "user": 1, "paper": 2}


def glossary_hash(snapshot: list[dict[str, Any]]) -> str:
    """スナップショットの正準ハッシュ(plans/06 §8.1)。

    ``sha256(canonical_json(snapshot))[:16]``。canonical_json はキー順固定・空白なしの
    UTF-8。plans/03 §7.1 の ``glossary_snapshot_id`` はこの値を返す(導出識別子)。
    """
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


async def build_snapshot(
    session: AsyncSession,
    *,
    user_id: str | None,
    library_item_id: str | None,
    shared: bool,
) -> tuple[list[dict[str, Any]], str]:
    """3 層マージして ``GlossarySnapshotJson`` と ``glossary_hash`` を返す(plans/06 §8.1)。

    - 収集: ``scope='global'`` の全語、(shared でなければ)``scope='user' AND user_id`` と
      ``scope='paper' AND library_item_id`` の語。**shared 構築時は global のみ**
      (plans/02 §3.4 の制約)。paper スコープからは**ユーザー確定語のみ**
      (``auto_extracted=false``。自動抽出の提案は共有可能性を壊さないため除外)。
    - マージ: ``lower(source_term)`` をキーに paper > user > global で 1 語 1 訳に確定。
    - 正規化: ``source_term`` の小文字順でソートし平坦化。
    """
    stmt = select(GlossaryTerm, Glossary.scope).join(
        Glossary, Glossary.id == GlossaryTerm.glossary_id
    )
    if shared:
        stmt = stmt.where(Glossary.scope == "global")
    else:
        from sqlalchemy import and_, or_

        conditions = [Glossary.scope == "global"]
        if user_id is not None:
            conditions.append(and_(Glossary.scope == "user", Glossary.user_id == user_id))
        if library_item_id is not None:
            conditions.append(
                and_(
                    Glossary.scope == "paper",
                    Glossary.library_item_id == library_item_id,
                    GlossaryTerm.auto_extracted.is_(False),
                )
            )
        stmt = stmt.where(or_(*conditions))

    rows = (await session.execute(stmt)).all()

    # lower(source_term) をキーに、origin 優先度の高い語で上書き確定。
    merged: dict[str, dict[str, Any]] = {}
    for term, scope in rows:
        entry = {
            "source_term": term.source_term,
            "target_term": term.target_term,
            "policy": term.policy,
            "origin": scope,
        }
        key = term.source_term.lower()
        current = merged.get(key)
        if current is None or _ORIGIN_PRIORITY[scope] >= _ORIGIN_PRIORITY[current["origin"]]:
            merged[key] = entry

    snapshot = [merged[k] for k in sorted(merged)]
    return snapshot, glossary_hash(snapshot)


def format_glossary_lines(snapshot: list[dict[str, Any]]) -> str:
    """system[1] の用語表テキスト(plans/06 §5.3・§8.2)。空なら ``(用語表なし)``。"""
    if not snapshot:
        return "(用語表なし)"
    lines: list[str] = []
    for e in snapshot:
        src = e["source_term"]
        tgt = e["target_term"]
        policy = e.get("policy", "translate")
        if policy == "keep_original":
            lines.append(f"- {src} → 原語のまま [policy=keep_original]")
        elif policy == "both":
            lines.append(f"- {src} → {tgt} [初出時のみ「{tgt}({src})」と併記] [policy=both]")
        else:
            lines.append(f"- {src} → {tgt} [policy=translate]")
    return "\n".join(lines)
