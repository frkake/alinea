"""共有コンパクションプリミティブ(docs/05 §3 圧縮モード)のユニットテスト。

`compact_document_context` は「全セクション要約 + 関連セクション全文」を決定的に組む。
LLM に依存しない(抽出的なリード文要約)。予算内なら全文をそのまま返す。
"""

from __future__ import annotations

from alinea_core.document.context_compaction import (
    RenderedSection,
    compact_document_context,
    estimate_tokens,
    render_full,
)


def _section(i: int, *, extra_word: str = "") -> RenderedSection:
    """LEAD/MIDDLE/TAIL の 3 文 + パディングを持つ 1 ブロックのセクション。"""
    body = (
        f"[b{i}|§{i} ¶1] LEAD_{i} sentence. MIDDLE_{i} sentence {extra_word}. "
        f"TAIL_{i} sentence. " + ("padding filler text " * 40)
    )
    return RenderedSection(section_id=f"s{i}", header=f"## [s{i}|§{i}] Section {i}", body_lines=(body,))


def test_under_budget_returns_verbatim_full_render() -> None:
    sections = [
        RenderedSection("s1", "## [s1|§1] Intro", ("[b1|§1 ¶1] hello world",)),
        RenderedSection("s2", "## [s2|§2] Method", ("[b2|§2 ¶1] method text",)),
    ]
    out = compact_document_context(
        sections, budget=10_000, preamble="# ctx", note="COMPRESSION_NOTE"
    )
    assert out == render_full("# ctx", sections)
    assert out == (
        "# ctx\n## [s1|§1] Intro\n[b1|§1 ¶1] hello world\n## [s2|§2] Method\n[b2|§2 ¶1] method text"
    )
    assert "COMPRESSION_NOTE" not in out  # 予算内では注記も要約もしない


def test_over_budget_keeps_all_headers_and_summarizes_late_sections() -> None:
    # 8 セクション。各全文 ~300 語で予算超過、要約は小さい。
    sections = [_section(i, extra_word="quantization" if i == 5 else "") for i in range(1, 9)]
    out = compact_document_context(
        sections,
        budget=600,
        preamble="# 論文コンテキスト",
        note="COMPRESSION_NOTE",
        query="how does quantization work",  # s5 のみ一致
        anchor_section_ids=("s2",),  # 選択アンカーは s2
    )

    # 予算超過なので注記が付き、全セクションの見出しは必ず残る(末尾も落ちない)。
    assert "COMPRESSION_NOTE" in out
    for i in range(1, 9):
        assert f"## [s{i}|§{i}] Section {i}" in out
    assert "## [s8|§8] Section 8" in out  # 末尾セクションの見出しが残る

    # アンカー(s2)と質問一致(s5)は全文 → TAIL 文まで含む。
    assert "TAIL_2 sentence" in out
    assert "TAIL_5 sentence" in out

    # 関連の低いセクション(s1)は要約のみ → LEAD は残るが TAIL は落ちる。
    assert "LEAD_1" in out
    assert "TAIL_1 sentence" not in out

    # 予算内に収まる。
    assert estimate_tokens(out) <= 600


def test_summary_only_document_stays_within_budget() -> None:
    # 全文だけでなく要約合計も大きいケースでも、最終ガードで予算を超えない。
    sections = [_section(i) for i in range(1, 40)]
    out = compact_document_context(
        sections, budget=500, preamble="# ctx", note="NOTE"
    )
    assert estimate_tokens(out) <= 500


def test_empty_preamble_matches_full_render_without_leading_blank_line() -> None:
    sections = [RenderedSection("s1", "## [s1|§1] Intro", ("[b1|§1 ¶1] hi",))]
    out = compact_document_context(sections, budget=10_000, preamble="", note="NOTE")
    assert out == "## [s1|§1] Intro\n[b1|§1 ¶1] hi"


def test_article_body_compaction_keeps_late_section_where_tail_truncation_dropped_it() -> None:
    """記事本文の圧縮: 後方セクションが要約で残る(旧 _truncate_tail_to_budget は落としていた)。"""
    from alinea_core.article.sources import _render_translated_sections
    from alinea_core.document.blocks import (
        Block,
        DocumentContent,
        Section,
        SectionHeading,
    )
    from alinea_core.document.inlines import Inline

    sections = []
    for i in range(1, 21):
        text = (
            f"LEAD_{i} translated sentence. TAIL_{i} unique_marker_{i}. "
            + ("filler text " * 60)
        )
        sections.append(
            Section(
                id=f"sec-{i}",
                heading=SectionHeading(number=str(i), title=f"Topic {i}"),
                blocks=[
                    Block(id=f"blk-{i}", type="paragraph", inlines=[Inline(t="text", v=text)])
                ],
            )
        )
    content = DocumentContent(quality_level="A", sections=sections)
    rendered, block_source_text = _render_translated_sections(content, {}, include_math=False)
    full = render_full("", rendered)
    budget = estimate_tokens(full) // 3  # 予算超過(圧縮モードに入る)

    out = compact_document_context(rendered, budget=budget, preamble="", note="ARTICLE_NOTE")

    # 旧挙動: 末尾切詰めは最終セクションを丸ごと落とす。
    import tiktoken

    enc = tiktoken.get_encoding("o200k_base")
    old = enc.decode(enc.encode(full, disallowed_special=())[:budget])
    assert "Topic 20" not in old

    # 圧縮モード: 最終セクションの見出し + リード要約が残り、予算内。
    assert "[sec-20|§20]" in out
    assert "LEAD_20 translated sentence" in out
    assert estimate_tokens(out) <= budget
    # block_source_text は全ブロック維持。
    assert set(block_source_text) == {f"blk-{i}" for i in range(1, 21)}
