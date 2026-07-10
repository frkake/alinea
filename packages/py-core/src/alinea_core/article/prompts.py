"""記事生成プロンプト(plans/07 §4.4・§4.7・§4.8、逐語)。"""

from __future__ import annotations

from typing import Literal

from alinea_core.article.sources import ArticleSources

Preset = Literal["beginner", "implementer", "researcher", "reading_group"]

# §4.7 構成プリセット 4 種の章立て定義(逐語)。
PRESET_OUTLINES: dict[str, str] = {
    "beginner": (
        "1. 背景となる前提知識(この論文を読むのに必要な概念を補う) 2. 何が課題か "
        "3. 提案のアイデア(比喩と図を中心に、数式なしで) 4. 何がうれしいか(結果と意味) "
        "5. 議論したい点。専門用語には初出で注釈を添える。"
    ),
    "implementer": (
        "1. TL;DR(3 行) 2. 手法の構成要素(入出力・モデル) "
        "3. 学習手順・損失・ハイパーパラメータ(Markdown 表で) "
        "4. 実装の落とし穴(本文の記述から予想される注意点) 5. 再現チェックリスト(箇条書き) "
        "6. 議論したい点。擬似コードと表を積極的に使う。"
    ),
    "researcher": (
        "1. 位置づけ(何が新しいか、既存手法との差分) 2. 手法の核心(主要な数式を含む詳細記述) "
        "3. 実験の批判的読解(設定の妥当性・ベースラインの選び方) 4. 限界と展望 5. 議論したい点。"
    ),
    "reading_group": (
        "発表フロー順に構成する: 1. 背景 2. 課題 3. 手法 4. 実験 5. 議論の論点。"
        "「議論したい点」を最も厚く構成し(4〜6 項目)、発表中に問いかけられる形の疑問文にする。"
    ),
}

# preset ごとの include_math 既定(plans/03 §19.2・plans/07 §4.1)。
PRESET_INCLUDE_MATH_DEFAULT: dict[str, bool] = {
    "beginner": False,
    "implementer": True,
    "researcher": True,
    "reading_group": False,
}

_MATH_TRUE = "重要な数式は $$...$$(KaTeX)で本文に含めてよい"
_MATH_FALSE = "数式を使わず、言葉と比喩で説明する"

# 逐語の各段落を、内容(改行位置)を変えずに物理行 <100 字で保つため暗黙連結で組む
# (apps/api/chat/prompts.py の CHAT_SYSTEM_PREAMBLE_TEMPLATE と同じ規約)。
_ARTICLE_SYSTEM_TEMPLATE = (
    "あなたは論文読解ワークベンチ「Alinea」の記事構成者です。"
    "ユーザーが読み終えた論文について、ユーザー自身の読解の痕跡"
    "(訳文・メモ・注釈・チャットでの議論)を素材に、ブログ風の読み物"
    "(記事)を JSON で構成します。\n"
    "\n"
    "## 構成の原則\n"
    "1. 記事は与えられた素材だけから構成する。本文にない主張・数値を作らない。"
    "各ブロックの根拠を evidence 配列に、素材中の [ブロックID|位置] の "
    "ブロックID で示す(段落粒度まで特定する)。\n"
    "2. 定型の論文要約ではなく、ユーザーのメモ・チャットで議論された論点を軸に"
    "再構成する。ユーザーが引っかかった箇所(★疑問 の注釈)は必ず取り上げる。\n"
    "3. タイトルは「{{論文の通称}} を読む: {{核心を一言で}}」の型を目安にした"
    "日本語(60 文字以内)。\n"
    "4. 文体は常体(だ・である)。段落は 3〜6 文。専門用語の初出には短い言い換え"
    "を添える。\n"
    "5. quote_source ブロックの text_en は、指定した block_id の原文から一語一句"
    "そのまま抜き出す(改変・省略記号の追加をしない)。印象的な原文を 1〜3 箇所"
    "引用する。\n"
    "6. figure_embed は素材の図表リストで「転載: 可」の図だけを指定する。"
    "「転載: 不可」の図に触れたい場合は本文で言及するに留める(埋め込まない)。\n"
    "7. explainer_figure(解説図)は最大 2 個。image_brief_en には描いてほしい"
    "概念図・比喩の視覚的内容を英語で書く。文字・数字・数式を画像に含める指示を"
    "書かない(重要な情報はすべて caption_ja に書く)。\n"
    "8. discussion(議論したい点)ブロックをちょうど 1 個、記事の末尾近くに置く。"
    "項目は 2〜6 個。★疑問 の注釈に由来する項目は origin を user_highlight とし、"
    "その annotation_id を書く。それ以外は origin を ai とする。\n"
    "9. 数式の扱い: {math_rule}\n"
    "10. 出典・免責・生成日は書かない(システムが付与する)。\n"
    "\n"
    "## 章立ての骨子(この順序・粒度を目安に heading を置く)\n"
    "{outline}"
)

# §4.8 ブロック単体の再生成用(縮約版)。原則 1〜7・9 を再利用。
_ARTICLE_BLOCK_SYSTEM_TEMPLATE = (
    "あなたは論文読解ワークベンチ「Alinea」の記事構成者です。"
    "記事内の 1 ブロックだけを書き直します。\n"
    "\n"
    "## 構成の原則\n"
    "1. 記事は与えられた素材だけから構成する。本文にない主張・数値を作らない。"
    "各ブロックの根拠を evidence 配列に、素材中の [ブロックID|位置] の "
    "ブロックID で示す(段落粒度まで特定する)。\n"
    "2. 定型の論文要約ではなく、ユーザーのメモ・チャットで議論された論点を軸に"
    "再構成する。ユーザーが引っかかった箇所(★疑問 の注釈)は必ず取り上げる。\n"
    "3. タイトルは書き直さない(ブロック単体の再生成のため)。\n"
    "4. 文体は常体(だ・である)。段落は 3〜6 文。専門用語の初出には短い言い換え"
    "を添える。\n"
    "5. quote_source ブロックの text_en は、指定した block_id の原文から一語一句"
    "そのまま抜き出す(改変・省略記号の追加をしない)。印象的な原文を 1〜3 箇所"
    "引用する。\n"
    "6. figure_embed は素材の図表リストで「転載: 可」の図だけを指定する。"
    "「転載: 不可」の図に触れたい場合は本文で言及するに留める(埋め込まない)。\n"
    "7. explainer_figure(解説図)は最大 2 個。image_brief_en には描いてほしい"
    "概念図・比喩の視覚的内容を英語で書く。文字・数字・数式を画像に含める指示を"
    "書かない(重要な情報はすべて caption_ja に書く)。\n"
    "9. 数式の扱い: {math_rule}\n"
    "10. 抽象的な要約で済ませず、手法の構成要素、処理手順、学習/推論条件、データセット、"
    "ベースライン、評価指標、アブレーション、失敗例、限界を素材にある範囲で具体的に書く。\n"
    "11. 研究者向けでは再現・批判的検討に必要な細部を優先し、主張ごとに根拠を付ける。"
    "追加リソースは論文本文と区別し、実装上の補足や著者説明として明示して活用する。"
)


def build_article_system_prompt(preset: str, *, include_math: bool) -> str:
    """ARTICLE_SYSTEM(§4.4 逐語)。"""
    outline = PRESET_OUTLINES[preset]
    math_rule = _MATH_TRUE if include_math else _MATH_FALSE
    return _ARTICLE_SYSTEM_TEMPLATE.format(outline=outline, math_rule=math_rule)


def build_article_block_system_prompt(*, include_math: bool) -> str:
    """ARTICLE_BLOCK_SYSTEM(§4.8 縮約版)。"""
    math_rule = _MATH_TRUE if include_math else _MATH_FALSE
    return _ARTICLE_BLOCK_SYSTEM_TEMPLATE.format(math_rule=math_rule)


def build_material_text(sources: ArticleSources) -> str:
    """記事生成に使う全素材。追加リソースも生成時点の内容を取り込む。"""
    parts = [
        sources.bibliography_text,
        sources.summary_text,
        "# 訳文本文\n" + sources.body_text if sources.body_text else "",
        sources.figures_text,
        sources.notes_text,
        sources.annotations_text,
        sources.chat_text,
        sources.resources_text,
    ]
    return "\n\n".join(p for p in parts if p)


def build_regenerate_suffix(
    *,
    instructions_history: list[str],
    instruction: str,
    current_article_plain: str,
) -> str:
    """✦指示つき再生成(§4.4)の追加末尾。"""
    history = "\n".join(f"- {i}" for i in instructions_history) or "(なし)"
    return (
        "## これまでの指示履歴\n"
        f"{history}\n"
        "## 今回の指示(最優先)\n"
        f"{instruction}\n"
        "## 現在の記事(参考。指示に関係ない部分の構成は維持してよい)\n"
        f"{current_article_plain}"
    )


def build_article_user_prompt(
    sources: ArticleSources,
    *,
    regenerate_suffix: str | None = None,
) -> str:
    material = build_material_text(sources)
    if regenerate_suffix:
        return f"{material}\n\n{regenerate_suffix}"
    return material


def build_block_rewrite_user_prompt(
    *,
    headings_outline: str,
    neighbor_blocks_plain: str,
    target_block_json: str,
    evidence_source_excerpt: str,
    instruction: str | None,
) -> str:
    """§4.8 のブロック書き直しユーザーメッセージ。"""
    final_instruction = instruction or "内容の主旨を保ったまま、より読みやすく書き直してください。"
    return (
        "## 記事の全体構成(見出しのみ)\n"
        f"{headings_outline}\n"
        "## 前後のブロック(参考)\n"
        f"{neighbor_blocks_plain}\n"
        "## 書き直し対象ブロック\n"
        f"{target_block_json}\n"
        "## 根拠に使える素材(対象ブロックの evidence が指す原文+関連セクション)\n"
        f"{evidence_source_excerpt}\n"
        "## 指示\n"
        f"{final_instruction}"
    )


__all__ = [
    "PRESET_INCLUDE_MATH_DEFAULT",
    "PRESET_OUTLINES",
    "Preset",
    "build_article_block_system_prompt",
    "build_article_system_prompt",
    "build_article_user_prompt",
    "build_block_rewrite_user_prompt",
    "build_material_text",
    "build_regenerate_suffix",
]
