"""リビジョン間ブロック ID 引き継ぎ(carryover。docs/01 §4.3・plans/05 §4.5)。

新リビジョン作成時(arXiv v 更新・parser_version 更新・reingest・B→A 昇格)に、
旧リビジョンのブロックと新リビジョンのブロックを
「内容ハッシュ一致 → 前後関係 → 編集距離」の 3 パスで対応付け、一致したブロックへ
旧 ID をそのまま与える(ID は不透明識別子。パス③で内容が変わっても旧 ID を引き継ぐ)。

編集距離パスは rapidfuzz が未導入のため標準ライブラリ difflib.SequenceMatcher を用いる
(比率は 0.0-1.0。閾値 0.90 は rapidfuzz.fuzz.ratio>=90 と等価の意図)。
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import pairwise

from yakudoku_core.document.blocks import Block, Section
from yakudoku_core.parsing.block_ids import block_source_hash, content_basis, normalize_for_hash

# 編集距離パス③のしきい値(正規化テキストの類似度)。
FUZZY_THRESHOLD = 0.90


@dataclass
class CarryOverStats:
    """carryover の結果統計(jobs.log info 記録用)。"""

    total: int = 0
    carried: int = 0
    by_hash: int = 0
    by_order: int = 0
    by_fuzzy: int = 0

    @property
    def carried_ratio(self) -> float:
        return self.carried / self.total if self.total else 0.0


def flatten_blocks(sections: list[Section]) -> list[Block]:
    """セクションツリーを文書順のブロック列へ平坦化する。"""
    out: list[Block] = []

    def walk(sec: Section) -> None:
        out.extend(sec.blocks)
        for sub in sec.sections:
            walk(sub)

    for sec in sections:
        walk(sec)
    return out


@dataclass
class _Ref:
    """carryover 内部で使うブロック参照(位置つき)。"""

    block: Block
    pos: int
    hash: str = ""
    fuzz_basis: str = ""


def _index_unique(refs: list[_Ref]) -> dict[tuple[str, str], _Ref]:
    """(type, source_hash) が双方で一意なペアのみを引ける索引(重複キーは除外)。"""
    seen: dict[tuple[str, str], _Ref] = {}
    dup: set[tuple[str, str]] = set()
    for r in refs:
        key = (str(r.block.type), r.hash)
        if key in seen:
            dup.add(key)
        else:
            seen[key] = r
    for key in dup:
        seen.pop(key, None)
    return seen


def _segments(
    old_refs: list[_Ref],
    new_refs: list[_Ref],
    anchors: list[tuple[int, int]],
) -> list[tuple[list[_Ref], list[_Ref]]]:
    """確定アンカー対の間の区間(旧・新)を返す。両側で単調増加する対のみ使う。"""
    ordered = sorted(anchors, key=lambda p: p[1])
    monotonic: list[tuple[int, int]] = []
    last_old = -1
    for old_pos, new_pos in ordered:
        if old_pos > last_old:
            monotonic.append((old_pos, new_pos))
            last_old = old_pos
    bounds = [(-1, -1), *monotonic, (len(old_refs), len(new_refs))]
    segs: list[tuple[list[_Ref], list[_Ref]]] = []
    for (o0, n0), (o1, n1) in pairwise(bounds):
        old_seg = old_refs[o0 + 1 : o1]
        new_seg = new_refs[n0 + 1 : n1]
        if old_seg or new_seg:
            segs.append((old_seg, new_seg))
    return segs


def carry_over_ids(old_blocks: list[Block], new_sections: list[Section]) -> CarryOverStats:
    """一致した新ブロックへ旧 ID を与える(破壊的に new_sections のブロックを更新)。"""
    new_blocks = flatten_blocks(new_sections)

    def _mk(blocks: list[Block]) -> list[_Ref]:
        return [
            _Ref(
                block=b,
                pos=i,
                hash=block_source_hash(b),
                fuzz_basis=normalize_for_hash(content_basis(b)),
            )
            for i, b in enumerate(blocks)
        ]

    old_refs = _mk(old_blocks)
    new_refs = _mk(new_blocks)
    stats = CarryOverStats(total=len(new_refs))
    matched_new: set[int] = set()

    # パス①: source_hash(type + 16 桁)完全一致。双方で一意なペアのみ確定。
    by_hash_old = _index_unique(old_refs)
    by_hash_new = _index_unique(new_refs)
    anchors: list[tuple[int, int]] = []
    for key, o in by_hash_old.items():
        n = by_hash_new.get(key)
        if n is not None:
            n.block.id = o.block.id
            matched_new.add(n.pos)
            anchors.append((o.pos, n.pos))
            stats.carried += 1
            stats.by_hash += 1

    # パス②: アンカー間区間で同種ブロック数が両側一致するとき出現順で対応付け。
    for old_seg, new_seg in _segments(old_refs, new_refs, anchors):
        types = {r.block.type for r in old_seg} | {r.block.type for r in new_seg}
        for t in types:
            olds = [r for r in old_seg if r.block.type == t]
            news = [r for r in new_seg if r.block.type == t and r.pos not in matched_new]
            if olds and len(olds) == len(news):
                for o, n in zip(olds, news, strict=True):
                    n.block.id = o.block.id
                    matched_new.add(n.pos)
                    stats.carried += 1
                    stats.by_order += 1

    # パス③: 残りは同種で類似度 >= 閾値の最良ペアを貪欲(スコア降順・1 対 1)に対応付け。
    used_old: set[int] = {a[0] for a in anchors}
    candidates: list[tuple[float, int, int]] = []
    remaining_new = [r for r in new_refs if r.pos not in matched_new]
    remaining_old = [r for r in old_refs if r.pos not in used_old]
    for n in remaining_new:
        for o in remaining_old:
            if o.block.type != n.block.type:
                continue
            score = SequenceMatcher(None, o.fuzz_basis, n.fuzz_basis).ratio()
            if score >= FUZZY_THRESHOLD:
                candidates.append((score, o.pos, n.pos))
    candidates.sort(key=lambda c: c[0], reverse=True)
    old_by_pos = {r.pos: r for r in old_refs}
    new_by_pos = {r.pos: r for r in new_refs}
    for _score, old_pos, new_pos in candidates:
        if old_pos in used_old or new_pos in matched_new:
            continue
        new_by_pos[new_pos].block.id = old_by_pos[old_pos].block.id
        used_old.add(old_pos)
        matched_new.add(new_pos)
        stats.carried += 1
        stats.by_fuzzy += 1

    return stats
