"""翻訳プロンプトテンプレート(plans/06 §5-6 の逐語・完全形)。

3 層構成(Anthropic キャッシュ可能プレフィックス順): ``system[0]``(静的・リリース単位)→
``system[1]``(論文スコープ・TranslationSet 単位)→ ``messages[0]``(バッチ単位)。

``PROMPT_VERSION`` はテンプレート・対訳例・プレースホルダ規約のいずれかの変更で上げる
(plans/06 §9.3)。変更時は末尾連番を上げる。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from yakudoku_core.translation.prompts.examples import (
    BAD_EXAMPLES,
    GOOD_EXAMPLES,
    format_examples,
)

# plans/06 §5・§16-1(translation_sets.prompt_version の既定と一致)。
PROMPT_VERSION = "tr-2026-07-06.1"

# arXiv カテゴリ → 分野プロファイル(plans/06 §5.3・docs/03 §5)。第一カテゴリで決定。
FIELD_PROFILES: dict[str, str] = {
    "cs.LG": "機械学習。損失・最適化・汎化などの ML 標準訳語感覚に従う。",
    "stat.ML": "機械学習。損失・最適化・汎化などの ML 標準訳語感覚に従う。",
    "cs.CV": "コンピュータビジョン。",
    "cs.CL": "自然言語処理。",
    "cs.RO": "ロボティクス。",
}
_DEFAULT_PROFILE = "一般的な計算機科学。"


def field_profile(categories: list[str] | None) -> str:
    """arXiv カテゴリ列から分野プロファイル文を決定する(第一カテゴリ優先)。"""
    for cat in categories or []:
        if cat in FIELD_PROFILES:
            return FIELD_PROFILES[cat]
    return _DEFAULT_PROFILE


# --- system[0] 静的プリアンブル(plans/06 §5.1 の逐語) -----------------------------

_LEAD = (
    "あなたは機械学習・計算機科学分野の英日学術翻訳者である。"
    "与えられた英語論文のブロックを日本語に翻訳する。"
)

_RULES = "\n".join(
    [
        "## 翻訳規則(優先順)",
        (
            "1. 忠実性: 原文の主張・限定・ニュアンス(may / might / we hypothesize 等)を保つ。"
            "要約・省略・補足・意訳による情報の追加をしない。"
        ),
        (
            "2. 段落対応: 入力の1ブロックを出力の1ブロックに訳す。"
            "文の分割・結合は許すが、ブロックをまたぐ再構成・順序入替をしない。"
        ),
        (
            "3. トークン完全保持: ⟦KIND:id⟧ 形式のトークンは"
            "数式・引用・参照・URL・コードの保護記号である。"
            "各トークンを訳文に「ちょうど1回ずつ」含める。日本語の語順に合わせた位置の移動は自由。"
            "削除・複製・内容の改変・翻訳は禁止。"
            "⟦EM:…⟧ と ⟦/EM:…⟧ は強調範囲の開始と終了の対であり、両方を残し開始を先に置く。"
        ),
        "4. 用語一貫性: 「用語表」がある場合は必ずその訳語に従う。",
        "5. 固有名詞: 著者名・組織名・モデル名・データセット名・手法の固有名は原語のまま。",
    ]
)

_STYLE_NATURAL = "\n".join(
    [
        "## 文体規定",
        "- 「だ・である」調に固定する。体言止めを多用しない。",
        "- 学術書として自然で読みやすい日本語にする。逐語直訳調(「〜するところの」等)を避ける。",
        (
            "- 慣用のカタカナ語は無理に訳さない"
            "(attention → アテンション、fine-tuning → ファインチューニング)。"
            "定訳のある語は定訳を使う"
            "(neural network → ニューラルネットワーク、generalization → 汎化)。"
        ),
        (
            "- 初出の頭字語は「日本語訳(English, ABBR)」形式で訳す"
            "(例: 大規模言語モデル(Large Language Model, LLM))。"
            "同じ入力内での2回目以降は略語のみ。"
        ),
        (
            "- 用語表で policy=both の語は、この入力内での初出時のみ「訳語(原語)」形式で併記する"
            "(例: 整流フロー(rectified flow))。2回目以降は訳語のみ。"
        ),
        (
            "- theorem / lemma / corollary / proposition / definition / remark の種別名は "
            "定理 / 補題 / 系 / 命題 / 定義 / 注意 と訳す。"
        ),
        "- 見出し(heading)ブロックは見出し本文のみを簡潔に訳す。節番号・原題を訳文に含めない。",
    ]
)

_STYLE_LITERAL = """## 文体規定(直訳)
- 「だ・である」調に固定する。
- 原文の語順・構文を可能な限り写像する。文の分割・結合をせず、原文1文=訳文1文の対応を保つ。
- 関係詞・分詞構文は構造が見える形で訳す(自然さより構文対応を優先する)。
- カタカナ語・定訳・頭字語・用語表の扱いは自然訳と同じ。"""

_OUTPUT = "\n".join(
    [
        "## 出力",
        (
            "指定された JSON スキーマに厳密に従い、JSON オブジェクトのみを出力する。"
            "説明文・前置き・コードフェンスを含めない。"
            '各要素の "id" は入力ブロックの id をそのまま返す。'
        ),
    ]
)


def build_system_preamble(style: str = "natural") -> str:
    """system[0]: 静的プリアンブル(plans/06 §5.1/§5.2)。スタイル別に 2 系統。

    ``style='literal'`` では「文体規定」節を直訳版に差し替える(他節は共通)。対訳例は
    GOOD 2・BAD 2 を埋め込む(plans/06 §5.1)。
    """
    style_section = _STYLE_LITERAL if style == "literal" else _STYLE_NATURAL
    examples = format_examples(GOOD_EXAMPLES[:2] + BAD_EXAMPLES[:2])
    return "\n\n".join([_LEAD, _RULES, style_section, _OUTPUT, "## 対訳例\n" + examples])


# --- system[1] 論文スコープ文脈(plans/06 §5.3 のテンプレート) ---------------------


def build_paper_context(
    *,
    title: str,
    authors_short: str,
    profile_text: str,
    toc_outline: str,
    glossary_lines: str,
) -> str:
    """system[1]: 論文スコープ文脈(TranslationSet 単位)。"""
    return (
        f"# 対象論文\n"
        f"タイトル: {title}\n"
        f"著者: {authors_short}\n"
        f"分野プロファイル: {profile_text}\n\n"
        f"# 論文の見出しツリー(位置把握用。翻訳対象ではない)\n"
        f"{toc_outline}\n\n"
        f"# 用語表(この論文の訳で必ず従う)\n"
        f"{glossary_lines}"
    )


# --- messages[0] バッチ user メッセージ(plans/06 §5.4 のテンプレート) --------------


class TargetBlock(BaseModel):
    """user メッセージに列挙する翻訳対象ブロック(id を保って訳す)。"""

    block_id: str
    block_type: str
    text: str  # プレースホルダ化済み


def build_user_message(
    *,
    section_path_display: str,
    targets: list[TargetBlock],
    prev_source_blocks: list[str] | None = None,
    prev_translations: list[str] | None = None,
    next_source_block: str | None = None,
    instruction: str = "",
    retranslate_note: bool = False,
    feedback: str = "",
) -> str:
    """バッチ単位の user メッセージ(plans/06 §5.4)。

    文脈が存在しない小見出しは丸ごと省略する。``feedback`` は検証失敗時の再構成再試行
    (plans/06 §4.6)で末尾に付す。
    """
    parts: list[str] = ["# 文脈(参考情報。翻訳しない)"]
    parts.append(f"## 現在のセクション: {section_path_display}")
    if prev_source_blocks:
        parts.append("## 直前のブロック(原文):\n" + "\n".join(prev_source_blocks))
    if prev_translations:
        parts.append("## 直前のブロックの既訳:\n" + "\n".join(prev_translations))
    if next_source_block:
        parts.append("## 直後のブロック(原文):\n" + next_source_block)

    lines = [f"# 翻訳対象ブロック({len(targets)}件。id を保ってすべて訳す)"]
    for t in targets:
        lines.append(f"[{t.block_id}] ({t.block_type}) {t.text}")
    parts.append("\n".join(lines))

    if retranslate_note:
        parts.append(
            "# 注意: 前回の訳はユーザーに「訳がおかしい」と指摘された。原文に忠実に訳し直すこと。"
        )
    if instruction:
        parts.append(f"# 追加指示(ユーザー): {instruction}")
    if feedback:
        parts.append(feedback)
    return "\n\n".join(parts)


# --- structured output スキーマ(plans/06 §7) --------------------------------------


class TranslatedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # 入力の block_id をそのまま返す
    ja: str  # プレースホルダ入り訳文


class TranslationBatchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    translations: list[TranslatedBlock]
