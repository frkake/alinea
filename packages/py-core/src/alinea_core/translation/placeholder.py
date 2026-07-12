"""プレースホルダプロトコル(plans/06 §4・docs/03 §4)。

翻訳 LLM に本文を渡す前に、``text`` 以外のインライン(数式・引用・参照・URL・コード・
脚注・強調)を ``⟦KIND:id⟧`` 形式のトークンへ置換して保護し(:func:`encode_block` /
:func:`protect`)、LLM 出力を検証し(:func:`verify_tokens` / :func:`validate`)、元の
インライン列へ復元する(:func:`decode_translation` / :func:`restore`)。

不変条件(最重要。HP-01): ``restore(protect(b), protect(b).text) == b``。
記号は 1 文字も変えない(docs/03 §3 原則3)——原子トークンは復元時に元インライン
オブジェクトをそのまま再利用することで構造的に保証する。

トークン書式(plans/06 §4.1、docs/03 §4 の逐語): ``⟦KIND:id⟧``(括弧は U+27E6 / U+27E7)。

- ``MATH`` ← ``math_inline`` / ``CIT`` ← ``citation`` / ``REF`` ← ``ref`` /
  ``FN`` ← ``footnote_ref`` / ``URL`` ← ``url`` / ``CODE`` ← ``code_inline``(原子)
- ``EM`` ← ``emphasis``(対トークン ``⟦EM:e-1⟧`` … ``⟦/EM:e-1⟧``。内部テキストは翻訳対象)

``citation`` / ``ref`` の安全な id は参照先 ``ref`` 値そのもの、文法外の id とその他は
出現順の連番。同一 id が同一ブロック内で再出現するときは ``#2`` を付す
(``⟦REF:fig-2#2⟧``)。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import xxhash
from pydantic import BaseModel

# ⟦KIND:id⟧(開始/終了の両形式を捕捉)。LaTeX の label で一般的な
# ``fig:overview`` / ``eq:loss`` / DOI の ``doi:10.1000/example`` をそのまま参照 id として
# 保護できるよう ``:`` と ``/`` も許可する。
TOKEN_RE = re.compile(r"⟦(/?)(MATH|CIT|REF|FN|URL|CODE|EM):([A-Za-z0-9_.#:/-]+)⟧")
_TOKEN_ID_RE = re.compile(r"[A-Za-z0-9_.#:/-]+")
_MAX_LITERAL_TOKEN_ID_CHARS = 256
# トークンを取り除いた後に残る裸の括弧(改変・破損の検出用)
BRACKET_RE = re.compile(r"[⟦⟧]")

# docs/01 §4.2 の Inline 型 → 原子トークン KIND(plans/06 §4.1)
_ATOMIC_KIND: dict[str, str] = {
    "math_inline": "MATH",
    "citation": "CIT",
    "ref": "REF",
    "footnote_ref": "FN",
    "url": "URL",
    "code_inline": "CODE",
}
# 連番 id の接頭辞(citation / ref は ref 値を使うため対象外)
_SEQ_PREFIX: dict[str, str] = {
    "MATH": "m",
    "CIT": "c",
    "REF": "r",
    "FN": "fn",
    "URL": "u",
    "CODE": "k",
    "EM": "e",
}


class TokenEntry(BaseModel):
    """1 トークンと復元用の元インライン。"""

    token: str  # "⟦MATH:m-1⟧" / "⟦EM:e-1⟧"(開始形)
    kind: str  # MATH / CIT / REF / FN / URL / CODE / EM
    inline: dict[str, Any]  # 元 Inline(復元用。EM は {"t": "emphasis"} の外殻のみ)
    paired: bool = False  # EM のみ True
    separator_before: bool = False  # 直前も原子トークンなら LLM 用空白を復元時に除く


class EncodedBlock(BaseModel):
    """プレースホルダ化済みブロック(= ProtectedText。plans/12 §7)。"""

    block_id: str
    text: str  # プレースホルダ化済み原文(LLM に渡す)
    tokens: list[TokenEntry]
    source_hash: str  # §4.5


class VerifyResult(BaseModel):
    """トークン検証結果(= ValidationResult。plans/06 §4.4)。"""

    ok: bool
    missing: list[str]  # 出力に現れなかったトークン
    duplicated: list[str]  # 2 回以上現れたトークン
    unknown: list[str]  # 原文に存在しないトークン(改変・捏造)
    malformed: bool  # ⟦⟧ の残骸(TOKEN_RE 不一致の括弧)がある
    em_order_ok: bool  # 各 EM ペアで開始が終了より前

    def __bool__(self) -> bool:
        return self.ok


class PlaceholderMismatchError(ValueError):
    """検証に失敗したトークン列を復元しようとした(P3。壊れた訳は復元しない。HP-03)。"""

    def __init__(self, result: VerifyResult) -> None:
        super().__init__(
            "placeholder verification failed: "
            f"missing={result.missing} duplicated={result.duplicated} "
            f"unknown={result.unknown} malformed={result.malformed} "
            f"em_order_ok={result.em_order_ok}"
        )
        self.result = result


def compute_source_hash(text: str, tokens: list[TokenEntry]) -> str:
    """プレースホルダ化済みテキストとトークン列から source_hash を算出する(§4.5)。

    本文もインライン構成も変わらなければリビジョンをまたいで一致し、翻訳キャッシュの
    移送に使える(plans/02 §4.4 の ``translation_units.source_hash`` と同一・xxhash64 hex)。
    """
    joined = "\x1e".join(te.token for te in tokens)
    return str(xxhash.xxh64(text + "\x1f" + joined).hexdigest())


def _block_inlines(block: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Block(docs/01 §4.1)または Inline 列から翻訳対象インライン列を得る(plans/06 §4.2)。

    figure / table はキャプション、list は項目を ``\\n- `` で連結、heading は title 文字列を
    テキスト片 1 個として扱う(heading はインライン列を持たないためトークン化は発生しない)。
    """
    if isinstance(block, list):
        return block
    btype = block.get("type")
    if btype == "heading":
        return [{"t": "text", "v": block.get("title") or ""}]
    if btype in ("figure", "table"):
        caption = block.get("caption") or []
        return list(caption)
    if btype == "list":
        joined: list[dict[str, Any]] = []
        for i, item in enumerate(block.get("items") or []):
            if i:
                joined.append({"t": "text", "v": "\n- "})
            joined.extend(item)
        return joined
    return list(block.get("inlines") or [])


def encode_block(block: dict[str, Any] | list[dict[str, Any]]) -> EncodedBlock:
    """ブロック(または Inline 列)の ``text`` 以外のインラインをトークン化する(§4.2)。"""
    counters: Counter[str] = Counter()
    used: Counter[str] = Counter()
    tokens: list[TokenEntry] = []
    parts: list[str] = []
    last_was_atomic = False

    def _emit(kind: str, ident: str, inline: dict[str, Any], *, paired: bool = False) -> str:
        nonlocal last_was_atomic
        key = f"{kind}:{ident}"
        used[key] += 1
        if used[key] > 1:  # 同一参照の再出現
            ident = f"{ident}#{used[key]}"
        tok = f"⟦{kind}:{ident}⟧"
        separator_before = last_was_atomic
        tokens.append(
            TokenEntry(
                token=tok,
                kind=kind,
                inline=inline,
                paired=paired,
                separator_before=separator_before,
            )
        )
        last_was_atomic = not paired
        return (" " if separator_before else "") + tok

    def _walk(inlines: list[dict[str, Any]]) -> None:
        nonlocal last_was_atomic
        for inl in inlines:
            t = inl.get("t")
            if t == "text":
                value = inl.get("v") or ""
                parts.append(value)
                if value:
                    last_was_atomic = False
            elif t == "emphasis":
                last_was_atomic = False
                counters["EM"] += 1
                ident = f"e-{counters['EM']}"
                parts.append(_emit("EM", ident, {"t": "emphasis"}, paired=True))
                children = inl.get("children")
                if isinstance(children, list):  # 入れ子インライン(plans/06 §4.2)
                    _walk(children)
                else:  # 実装 IR は emphasis.v に平文(docs/01 §4.2)
                    parts.append(inl.get("v") or "")
                parts.append(f"⟦/EM:{ident}⟧")
                last_was_atomic = False
            elif t in _ATOMIC_KIND:
                kind = _ATOMIC_KIND[t]
                if t in ("citation", "ref"):
                    literal_ident = str(inl.get("ref") or "")
                    if (
                        len(literal_ident) <= _MAX_LITERAL_TOKEN_ID_CHARS
                        and _TOKEN_ID_RE.fullmatch(literal_ident) is not None
                    ):
                        ident = literal_ident
                    else:
                        counters[kind] += 1
                        ident = f"{_SEQ_PREFIX[kind]}-{counters[kind]}"
                else:
                    counters[kind] += 1
                    ident = f"{_SEQ_PREFIX[kind]}-{counters[kind]}"
                parts.append(_emit(kind, ident, inl))
            else:  # 未知型は保護側に倒す(削除されるより残す。plans/06 §4.2)
                counters["CODE"] += 1
                parts.append(_emit("CODE", f"k-{counters['CODE']}", inl))

    _walk(_block_inlines(block))
    text = "".join(parts)
    block_id = block.get("id", "") if isinstance(block, dict) else ""
    return EncodedBlock(
        block_id=block_id,
        text=text,
        tokens=tokens,
        source_hash=compute_source_hash(text, tokens),
    )


def verify_tokens(encoded: EncodedBlock, output_ja: str) -> VerifyResult:
    """全トークンがちょうど 1 回ずつ現れることを検証する(§4.4)。

    合格条件: 欠落・重複・不明が空、括弧残骸なし、EM の順序正。トークンの順序移動は自由。
    失敗時の ``missing`` / ``duplicated`` / ``unknown`` はプロンプト再構成再試行(§4.6)の
    フィードバックに使う。
    """
    expected: Counter[str] = Counter()
    for te in encoded.tokens:
        expected[te.token] += 1
        if te.paired:
            expected[te.token.replace("⟦EM:", "⟦/EM:")] += 1
    found: Counter[str] = Counter(m.group(0) for m in TOKEN_RE.finditer(output_ja))
    missing = sorted((expected - found).elements())
    duplicated = sorted(t for t, c in found.items() if c > expected.get(t, 0) and t in expected)
    unknown = sorted(t for t in found if t not in expected)
    stripped = TOKEN_RE.sub("", output_ja)
    malformed = BRACKET_RE.search(stripped) is not None
    em_order_ok = all(
        output_ja.find(te.token) < output_ja.find(te.token.replace("⟦EM:", "⟦/EM:"))
        for te in encoded.tokens
        if te.paired
        if te.token in found and te.token.replace("⟦EM:", "⟦/EM:") in found
    )
    ok = not missing and not duplicated and not unknown and not malformed and em_order_ok
    return VerifyResult(
        ok=ok,
        missing=missing,
        duplicated=duplicated,
        unknown=unknown,
        malformed=malformed,
        em_order_ok=em_order_ok,
    )


def decode_translation(encoded: EncodedBlock, output_ja: str) -> list[dict[str, Any]]:
    """LLM 出力を元のインライン列へ復元する(§4.3)。

    原子トークンは :class:`TokenEntry` の元インラインをそのまま再利用する(1 文字も変えない
    保証は「元オブジェクトの再利用」で構造的に満たす)。``⟦EM:…⟧`` 〜 ``⟦/EM:…⟧`` 区間は
    ``{"t": "emphasis", "children": [区間の再帰デコード]}`` になる。前提: ``output_ja`` は
    :func:`verify_tokens` を通過済み(:func:`restore` が保証する)。
    """
    lookup: dict[str, TokenEntry] = {te.token: te for te in encoded.tokens}
    end_to_start: dict[str, str] = {
        te.token.replace("⟦EM:", "⟦/EM:"): te.token for te in encoded.tokens if te.paired
    }
    root: list[dict[str, Any]] = []
    stack: list[list[dict[str, Any]]] = [root]
    pos = 0
    identity_output = output_ja == encoded.text
    for m in TOKEN_RE.finditer(output_ja):
        segment = output_ja[pos : m.start()]
        entry = lookup.get(m.group(0))
        if (
            identity_output
            and entry is not None
            and entry.separator_before
            and segment.endswith(" ")
        ):
            segment = segment[:-1]
        stack[-1].append({"t": "text", "v": segment})
        pos = m.end()
        tok = m.group(0)
        if tok in end_to_start:  # EM 終了 → 区間を emphasis にまとめる
            children = stack.pop()
            start = lookup[end_to_start[tok]]
            stack[-1].append({**start.inline, "children": children})
        elif tok in lookup and lookup[tok].paired:  # EM 開始 → 新しい区間を開く
            stack.append([])
        elif tok in lookup:  # 原子トークン → 元インライン再利用
            stack[-1].append(lookup[tok].inline)
        else:  # 検証未通過で呼ばれた場合の保険(通常は restore が弾く)
            stack[-1].append({"t": "text", "v": tok})
    stack[-1].append({"t": "text", "v": output_ja[pos:]})
    return root


def restore(protected: EncodedBlock, llm_output: str) -> list[dict[str, Any]]:
    """検証を通したうえで復元する。不合格なら :class:`PlaceholderMismatchError`(HP-03)。

    plans/12 §7 の ``restore(protected, llm_output) -> RestoredInlines``。壊れた訳を復元して
    見せることはしない(P3)。パイプライン(§4.6)はこの例外を捕捉して原文フォールバック
    (``quality_flags={placeholder_mismatch}``)に倒す。
    """
    result = verify_tokens(protected, llm_output)
    if not result.ok:
        raise PlaceholderMismatchError(result)
    return decode_translation(protected, llm_output)


# plans/12 §7 / docs/03 §4 の呼称に合わせた別名(protect / validate)。
protect = encode_block
validate = verify_tokens
