"""プレースホルダプロトコルのテスト(plans/06 §4・plans/12 §7・docs/03 §4)。

- PY-TR-01(unit): 11 ブロック型ごとの保護対象(math/citation/ref/url/code/footnote)が
  漏れなくトークン化される。
- HP-01〜04(property・Hypothesis): 往復不変 / 順序置換の受理 / 変異の拒絶 / 検証の完全性と
  任意 Unicode 入力での安全性。

Hypothesis の strategy は plans/12 §7.1 を実装 IR(docs/01 §4.2 の Inline 形式)へ写像した。
性質(HP-01〜04)の定義は plans/12 §7.2 の逐語。EM(対トークン)は §7.1 の inline_kind から
除外されているため、専用の unit テストで検証する。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal

import pytest
from alinea_core.translation.placeholder import (
    TOKEN_RE,
    PlaceholderMismatchError,
    compute_source_hash,
    decode_translation,
    encode_block,
    protect,
    restore,
    validate,
    verify_tokens,
)
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import DrawFn, SearchStrategy

# ---------------------------------------------------------------------------
# strategies(plans/12 §7.1 を実装 IR へ写像)
# ---------------------------------------------------------------------------

# サロゲート(単独サロゲートは utf-8 化できず regex/ハッシュを壊す)を除外する Unicode カテゴリ
_NO_SURROGATES: tuple[Literal["Cs"], ...] = ("Cs",)

# ⟦⟧(U+27E6/U+27E7)とサロゲートを含まない任意テキスト(和英混在・絵文字・結合文字を含む)
text_fragment = st.text(
    alphabet=st.characters(blacklist_characters="⟦⟧", blacklist_categories=_NO_SURROGATES),
    min_size=0,
    max_size=120,
)

# トークン安全な id/ref 値(TOKEN_RE の id 文字集合内。# は連番付与と衝突するため除外)
safe_ref = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.:", min_size=1, max_size=10)


@st.composite
def atomic_inline(draw: DrawFn) -> dict[str, Any]:
    """原子トークン化される 6 種のインライン(docs/01 §4.2。EM は除外 — plans/12 §7.1)。"""
    kind = draw(
        st.sampled_from(["math_inline", "citation", "ref", "url", "code_inline", "footnote_ref"])
    )
    if kind == "citation":
        return {"t": "citation", "ref": draw(safe_ref)}
    if kind == "ref":
        ref_kind = draw(st.sampled_from(["equation", "figure", "table", "section"]))
        return {"t": "ref", "kind": ref_kind, "ref": draw(safe_ref)}
    if kind == "url":
        return {
            "t": "url",
            "v": draw(text_fragment),
            "href": "http://example.com/" + draw(safe_ref),
        }
    if kind == "footnote_ref":
        return {"t": "footnote_ref", "ref": draw(safe_ref)}
    # math_inline / code_inline は v に平文
    return {"t": kind, "v": draw(safe_ref)}


@st.composite
def block_inlines(draw: DrawFn) -> list[dict[str, Any]]:
    """text と原子インラインの交互列(先頭・末尾は text、原子 0〜30 個。plans/12 §7.1)。"""
    n = draw(st.integers(min_value=0, max_value=30))
    parts: list[dict[str, Any]] = [{"t": "text", "v": draw(text_fragment)}]
    for _ in range(n):
        parts.append(draw(atomic_inline()))
        parts.append({"t": "text", "v": draw(text_fragment)})
    return parts


@st.composite
def wellbehaved_output(draw: DrawFn, tokens: list[str]) -> str:
    """LLM の正常応答: トークン順序の任意置換 + 周辺テキスト差し替え(plans/12 §7.1)。"""
    order = list(draw(st.permutations(tokens)))
    glue = [draw(text_fragment) for _ in range(len(order) + 1)]
    return "".join(g + t for g, t in zip(glue, [*order, ""], strict=True)).rstrip()


@st.composite
def messy_output(draw: DrawFn, tokens: list[str]) -> str:
    """任意 Unicode(⟦⟧・部分トークン・重複を含みうる)+ 実トークン断片(HP-04 の安全性検証)。"""
    token_piece: SearchStrategy[str] = st.sampled_from(tokens) if tokens else st.just("")
    pieces = draw(
        st.lists(
            st.one_of(
                st.text(alphabet=st.characters(blacklist_categories=_NO_SURROGATES), max_size=20),
                token_piece,
                st.sampled_from(["⟦", "⟧", "⟦MATH:zzz⟧", "⟦/EM:e-9⟧", "⟦CIT:ghost⟧"]),
            ),
            min_size=0,
            max_size=12,
        )
    )
    return "".join(pieces)


# 独立実装 oracle(正規表現。plans/12 §7.2 HP-04)
_ORACLE_RE = re.compile(r"⟦/?(?:MATH|CIT|REF|FN|URL|CODE|EM):[A-Za-z0-9_.#:-]+⟧")


def _expected_multiset(tokens: list[dict[str, Any]]) -> Counter[str]:
    # atomic のみ(block_inlines は EM を生成しない)なので token をそのまま数える
    return Counter(t["token"] for t in tokens)


def _oracle_ok(protected_tokens: list[str], output: str) -> bool:
    expected: Counter[str] = Counter(protected_tokens)
    found: Counter[str] = Counter(_ORACLE_RE.findall(output))
    if found != expected:
        return False
    stripped = _ORACLE_RE.sub("", output)
    return "⟦" not in stripped and "⟧" not in stripped


def _canon(inline: dict[str, Any]) -> str:
    return repr(sorted(inline.items()))


# ---------------------------------------------------------------------------
# PY-TR-01(unit): 保護対象が漏れなくトークン化される
# ---------------------------------------------------------------------------


def test_py_tr_01_all_inline_kinds_tokenized() -> None:
    block: dict[str, Any] = {
        "id": "blk-3-p2-a1f9",
        "type": "paragraph",
        "inlines": [
            {"t": "text", "v": "We train "},
            {"t": "citation", "ref": "ref-12"},
            {"t": "text", "v": " with the loss in "},
            {"t": "math_inline", "v": "x^2"},
            {"t": "text", "v": " see "},
            {"t": "ref", "kind": "equation", "ref": "eq-5"},
            {"t": "text", "v": " at "},
            {"t": "url", "v": "site", "href": "http://x"},
            {"t": "text", "v": " and "},
            {"t": "code_inline", "v": "f()"},
            {"t": "text", "v": " note "},
            {"t": "footnote_ref", "ref": "fn-1"},
            {"t": "text", "v": "."},
        ],
    }
    enc = protect(block)
    assert enc.block_id == "blk-3-p2-a1f9"
    assert {te.kind for te in enc.tokens} == {"CIT", "MATH", "REF", "URL", "CODE", "FN"}
    # citation/ref の id は ref 値そのもの、連番系は出現順(plans/06 §4.1)
    assert "⟦CIT:ref-12⟧" in enc.text
    assert "⟦REF:eq-5⟧" in enc.text
    assert "⟦MATH:m-1⟧" in enc.text
    assert "⟦URL:u-1⟧" in enc.text
    assert "⟦CODE:k-1⟧" in enc.text
    assert "⟦FN:fn-1⟧" in enc.text
    # 記号は 1 文字も変えず復元される(非 text インラインが原文どおり)
    restored = restore(enc, enc.text)
    assert [i for i in restored if i["t"] != "text"] == [
        i for i in block["inlines"] if i["t"] != "text"
    ]
    # text も欠落しない
    assert "".join(i["v"] for i in restored if i["t"] == "text") == "".join(
        i["v"] for i in block["inlines"] if i["t"] == "text"
    )


def test_py_tr_01_block_type_extraction() -> None:
    cit: dict[str, Any] = {"t": "citation", "ref": "ref-1"}
    inlines: list[dict[str, Any]] = [{"t": "text", "v": "a "}, cit, {"t": "text", "v": " b"}]
    # inlines を持つ本文コンテナ(paragraph/quote/theorem/footnote/algorithm)
    for bt in ("paragraph", "quote", "theorem", "footnote", "algorithm"):
        enc = protect({"id": f"blk-{bt}", "type": bt, "inlines": inlines})
        assert [te.token for te in enc.tokens] == ["⟦CIT:ref-1⟧"]
    # heading: title を 1 テキスト片として扱う(トークン化なし)
    enc = protect({"id": "blk-h", "type": "heading", "title": "Method"})
    assert enc.tokens == []
    assert enc.text == "Method"
    # figure / table: キャプションのみ
    for bt in ("figure", "table"):
        enc = protect({"id": f"blk-{bt}", "type": bt, "caption": [{"t": "text", "v": "Fig "}, cit]})
        assert [te.token for te in enc.tokens] == ["⟦CIT:ref-1⟧"]
    # list: 項目を "\n- " で連結
    enc = protect(
        {
            "id": "blk-l",
            "type": "list",
            "items": [
                [{"t": "text", "v": "one "}, cit],
                [{"t": "text", "v": "two "}, {"t": "citation", "ref": "ref-2"}],
            ],
        }
    )
    assert [te.token for te in enc.tokens] == ["⟦CIT:ref-1⟧", "⟦CIT:ref-2⟧"]
    assert "\n- " in enc.text
    # 翻訳対象外(equation/code/reference_entry)は本文インラインを持たず 0 トークン
    for bt in ("equation", "code", "reference_entry"):
        enc = protect({"id": f"blk-{bt}", "type": bt})
        assert enc.tokens == []


def test_duplicate_ref_gets_hash_suffix() -> None:
    # 同一 ref を同一ブロックで 2 回参照 → 2 回目以降は #n(plans/06 §4.1)
    enc = protect(
        [
            {"t": "text", "v": "compare "},
            {"t": "ref", "kind": "figure", "ref": "fig-2"},
            {"t": "text", "v": " and "},
            {"t": "ref", "kind": "figure", "ref": "fig-2"},
        ]
    )
    assert [te.token for te in enc.tokens] == ["⟦REF:fig-2⟧", "⟦REF:fig-2#2⟧"]
    assert verify_tokens(enc, enc.text).ok


def test_latex_colon_ref_roundtrips() -> None:
    """LaTeX で標準的な ``fig:name`` を常に検証可能にする。"""
    inline = {"t": "ref", "kind": "figure", "ref": "fig:overview"}
    enc = protect([{"t": "text", "v": "See "}, inline])

    assert enc.text.endswith("⟦REF:fig:overview⟧")
    assert verify_tokens(enc, enc.text).ok
    assert [item for item in restore(enc, enc.text) if item["t"] != "text"] == [inline]


def test_unknown_inline_type_protected_as_code() -> None:
    # 未知型は削除されるより保護側に倒す(plans/06 §4.2)
    inlines: list[dict[str, Any]] = [
        {"t": "text", "v": "a"},
        {"t": "weird_thing", "data": 1},
        {"t": "text", "v": "b"},
    ]
    enc = protect(inlines)
    assert [te.kind for te in enc.tokens] == ["CODE"]
    assert enc.tokens[0].token == "⟦CODE:k-1⟧"
    assert restore(enc, enc.text) == inlines


# ---------------------------------------------------------------------------
# emphasis(対トークン。plans/12 §7.1 の property strategy からは除外)
# ---------------------------------------------------------------------------


def test_emphasis_paired_roundtrip() -> None:
    inlines: list[dict[str, Any]] = [
        {"t": "text", "v": "This "},
        {"t": "emphasis", "children": [{"t": "text", "v": "may"}]},
        {"t": "text", "v": " help "},
        {"t": "citation", "ref": "ref-3"},
        {"t": "text", "v": "."},
    ]
    enc = protect(inlines)
    assert "⟦EM:e-1⟧may⟦/EM:e-1⟧" in enc.text
    assert validate(enc, enc.text).ok
    assert restore(enc, enc.text) == inlines


def test_emphasis_v_form_encoded() -> None:
    # 実装 IR の emphasis は v に平文(docs/01 §4.2)
    enc = protect(
        [{"t": "text", "v": "a"}, {"t": "emphasis", "v": "bold"}, {"t": "text", "v": "c"}]
    )
    assert "⟦EM:e-1⟧bold⟦/EM:e-1⟧" in enc.text
    assert enc.text == "a⟦EM:e-1⟧bold⟦/EM:e-1⟧c"


def test_emphasis_order_violation_rejected() -> None:
    inlines: list[dict[str, Any]] = [
        {"t": "text", "v": "x"},
        {"t": "emphasis", "children": [{"t": "text", "v": "y"}]},
    ]
    enc = protect(inlines)
    # 開始と終了を入れ替える → em_order_ok=False(§4.4)
    bad = (
        enc.text.replace("⟦EM:e-1⟧", "\x00")
        .replace("⟦/EM:e-1⟧", "⟦EM:e-1⟧")
        .replace("\x00", "⟦/EM:e-1⟧")
    )
    result = validate(enc, bad)
    assert not result.em_order_ok
    assert not result.ok


# ---------------------------------------------------------------------------
# source_hash(§4.5)
# ---------------------------------------------------------------------------


def test_source_hash_deterministic_and_sensitive() -> None:
    base = [{"t": "text", "v": "hello "}, {"t": "math_inline", "v": "x"}, {"t": "text", "v": "!"}]
    a = protect(list(base))
    b = protect(list(base))
    assert a.source_hash == b.source_hash  # 決定的
    assert len(a.source_hash) == 16  # xxhash64 hex
    # 本文(プレースホルダ化済みテキスト)が変われば hash も変わる
    d = protect(
        [{"t": "text", "v": "goodbye "}, {"t": "math_inline", "v": "x"}, {"t": "text", "v": "!"}]
    )
    assert a.source_hash != d.source_hash
    # インライン構成(トークン列)が変われば hash も変わる
    e = protect(
        [{"t": "text", "v": "hello "}, {"t": "citation", "ref": "r1"}, {"t": "text", "v": "!"}]
    )
    assert a.source_hash != e.source_hash
    # 数式の中身だけが変わっても text/token 列は不変 → hash も不変(plans/06 §4.5 の意図。
    # 訳文プロンプトには ⟦MATH:m-1⟧ しか出ず、latex 本体はブロック外で描画されるため)
    c = protect(
        [{"t": "text", "v": "hello "}, {"t": "math_inline", "v": "y"}, {"t": "text", "v": "!"}]
    )
    assert a.source_hash == c.source_hash


def test_compute_source_hash_matches_encoded() -> None:
    enc = protect([{"t": "text", "v": "z "}, {"t": "citation", "ref": "r9"}])
    assert enc.source_hash == compute_source_hash(enc.text, enc.tokens)


# ---------------------------------------------------------------------------
# 検証・復元の周辺契約
# ---------------------------------------------------------------------------


def test_verify_result_is_truthy() -> None:
    enc = protect([{"t": "text", "v": "hi"}])
    assert bool(validate(enc, "こんにちは")) is True
    bad = protect(
        [{"t": "text", "v": "a"}, {"t": "citation", "ref": "r1"}, {"t": "text", "v": "b"}]
    )
    assert bool(validate(bad, "トークンなし")) is False


def test_restore_raises_with_verify_result() -> None:
    enc = protect(
        [{"t": "text", "v": "a"}, {"t": "citation", "ref": "r1"}, {"t": "text", "v": "b"}]
    )
    with pytest.raises(PlaceholderMismatchError) as excinfo:
        restore(enc, "トークンが欠けている訳文")
    assert "⟦CIT:r1⟧" in excinfo.value.result.missing


def test_decode_translation_keeps_unknown_token_as_text() -> None:
    # 検証を通らない出力を decode に直接渡した場合の保険経路(不明トークンはテキスト化)
    enc = protect(
        [{"t": "text", "v": "a"}, {"t": "citation", "ref": "r1"}, {"t": "text", "v": "b"}]
    )
    out = decode_translation(enc, "x⟦CIT:r1⟧y⟦MATH:zzz⟧z")
    assert any(i["t"] == "citation" for i in out)
    assert "⟦MATH:zzz⟧" in [i["v"] for i in out if i["t"] == "text"]


def test_encode_block_and_protect_are_the_same() -> None:
    inlines = [{"t": "text", "v": "a"}, {"t": "citation", "ref": "r1"}]
    assert protect(inlines) == encode_block(inlines)


# ---------------------------------------------------------------------------
# HP-01〜04(property・Hypothesis。plans/12 §7.2)
# ---------------------------------------------------------------------------


@given(inlines=block_inlines())
@settings(max_examples=1000, deadline=None, print_blob=True)
@example(inlines=[{"t": "text", "v": ""}])  # n=0(トークンなし)
@example(
    inlines=[
        {"t": "text", "v": "図"},
        {"t": "ref", "kind": "figure", "ref": "fig-2"},
        {"t": "text", "v": "を参照"},
    ]
)
def test_hp01_roundtrip(inlines: list[dict[str, Any]]) -> None:
    """HP-01 往復不変: restore(protect(b), protect(b).text) == b(LLM 恒等応答)。"""
    protected = protect(inlines)
    assert restore(protected, protected.text) == inlines


@given(data=st.data(), inlines=block_inlines())
@settings(max_examples=1000, deadline=None, print_blob=True)
def test_hp02_order_permutation_accepted(
    data: st.DataObject, inlines: list[dict[str, Any]]
) -> None:
    """HP-02 順序置換の受理: 任意の well-behaved 応答で validate 合格・restore 正当。"""
    protected = protect(inlines)
    tokens = [te.token for te in protected.tokens]
    output = data.draw(wellbehaved_output(tokens))
    result = validate(protected, output)
    assert result.ok, result
    restored = restore(protected, output)
    # text 部分が応答テキスト(トークン除去後)と一致
    assert "".join(i["v"] for i in restored if i["t"] == "text") == TOKEN_RE.sub("", output)
    # 非 text インライン集合(id 付き)が元と一致(順序は問わない)
    original_nontext = [i for i in inlines if i["t"] != "text"]
    restored_nontext = [i for i in restored if i["t"] != "text"]
    assert sorted(map(_canon, restored_nontext)) == sorted(map(_canon, original_nontext))


@given(
    data=st.data(),
    inlines=block_inlines(),
    mutation=st.sampled_from(["drop", "duplicate", "mutate_id", "break_bracket"]),
)
@settings(max_examples=1000, deadline=None, print_blob=True)
def test_hp03_mutation_rejected(
    data: st.DataObject, inlines: list[dict[str, Any]], mutation: str
) -> None:
    """HP-03 変異の拒絶: drop/duplicate/mutate_id/break_bracket を validate が不合格にし、
    restore は例外を送出する(壊れた訳を復元しない)。"""
    protected = protect(inlines)
    tokens = [te.token for te in protected.tokens]
    assume(len(tokens) >= 1)
    target = data.draw(st.sampled_from(tokens))
    if mutation == "drop":
        mutated = protected.text.replace(target, "", 1)
    elif mutation == "duplicate":
        mutated = protected.text.replace(target, target + target, 1)
    elif mutation == "mutate_id":
        mutated = protected.text.replace(target, target[:-1] + "-x⟧", 1)
    else:  # break_bracket(開き括弧を落とす)
        mutated = protected.text.replace(target, target[1:], 1)
    assume(mutated != protected.text)
    assert not validate(protected, mutated).ok
    with pytest.raises(PlaceholderMismatchError):
        restore(protected, mutated)


@given(data=st.data(), inlines=block_inlines())
@settings(max_examples=500, deadline=None, print_blob=True)
def test_hp04_validate_completeness_and_safety(
    data: st.DataObject, inlines: list[dict[str, Any]]
) -> None:
    """HP-04 検証の完全性 + 任意 Unicode 入力での安全性: validate 合格 ⟺ 独立 oracle が合格。
    任意の(⟦⟧・断片・重複を含む)出力で validate は例外を出さない。"""
    protected = protect(inlines)
    tokens = [te.token for te in protected.tokens]
    output = data.draw(
        st.one_of(
            st.just(protected.text),
            wellbehaved_output(tokens),
            messy_output(tokens),
            st.text(alphabet=st.characters(blacklist_categories=_NO_SURROGATES), max_size=200),
        )
    )
    assert validate(protected, output).ok == _oracle_ok(tokens, output)


def test_hp04_zero_token_block_always_ok() -> None:
    """トークン 0 個のブロックは常に合格(plans/12 §7.2 HP-04)。"""
    protected = protect([{"t": "text", "v": "hello world"}])
    assert protected.tokens == []
    assert _expected_multiset([t.model_dump() for t in protected.tokens]) == Counter()
    assert validate(protected, "こんにちは、世界。").ok
    assert validate(protected, "").ok
