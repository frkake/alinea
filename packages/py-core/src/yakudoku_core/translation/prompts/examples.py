"""対訳例集(docs/03 §5「仕様アセット」・plans/06 §5.4)。

同一定数をプロンプト(:mod:`yakudoku_core.translation.prompts`)とリグレッション
テストの両方で使う。確定数: GOOD 8 対・BAD 6 対(plans/06 §5.1)。GOOD 例は
:func:`yakudoku_core.translation.verify_tokens` を通過し、BAD 例は意図した理由で
不合格になる(トークン欠落 / 限定の脱落 / 数値欠落 / 未訳 / 長さ逸脱)。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TranslationExample(BaseModel):
    """1 対の対訳例。``kind='good'`` は良訳、``'bad'`` は反例(``note`` に理由)。"""

    kind: Literal["good", "bad"]
    source: str  # プレースホルダ化済み英文
    target: str  # 日本語訳(GOOD はトークン検証通過)
    note: str = ""  # BAD の不合格理由(逐語)


# GOOD 8 対(トークンをちょうど 1 回ずつ含む・「だ・である」調)。
GOOD_EXAMPLES: tuple[TranslationExample, ...] = (
    TranslationExample(
        kind="good",
        source="We train with ⟦CIT:ref-12⟧ using the loss in ⟦REF:eq-5⟧, "
        "where ⟦MATH:m-1⟧ denotes the drift.",
        target="⟦REF:eq-5⟧ の損失を用いて ⟦CIT:ref-12⟧ に従い学習する。"
        "ここで ⟦MATH:m-1⟧ はドリフトを表す。",
    ),
    TranslationExample(
        kind="good",
        source="This ⟦EM:e-1⟧may⟦/EM:e-1⟧ improve sample quality, "
        "although we do not verify it at scale.",
        target="これはサンプル品質を改善する⟦EM:e-1⟧可能性がある⟦/EM:e-1⟧が、"
        "大規模には検証していない。",
    ),
    TranslationExample(
        kind="good",
        source="See ⟦REF:fig-2⟧ for the trajectory of the process.",
        target="この過程の軌跡については ⟦REF:fig-2⟧ を参照する。",
    ),
    TranslationExample(
        kind="good",
        source="The method ⟦CIT:ref-3⟧ scales to large datasets.",
        target="この手法 ⟦CIT:ref-3⟧ は大規模データセットに拡張できる。",
    ),
    TranslationExample(
        kind="good",
        source="We define the loss ⟦MATH:m-1⟧ in ⟦REF:eq-1⟧.",
        target="損失 ⟦MATH:m-1⟧ を ⟦REF:eq-1⟧ で定義する。",
    ),
    TranslationExample(
        kind="good",
        source="Code is available at ⟦URL:u-1⟧.",
        target="コードは ⟦URL:u-1⟧ で公開している。",
    ),
    TranslationExample(
        kind="good",
        source="As shown in ⟦REF:tab-1⟧, accuracy improves consistently.",
        target="⟦REF:tab-1⟧ に示すとおり、精度は一貫して向上する。",
    ),
    TranslationExample(
        kind="good",
        source="The full proof appears in ⟦FN:fn-1⟧.",
        target="完全な証明は ⟦FN:fn-1⟧ に示す。",
    ),
)

# BAD 6 対(反例)。プロンプトで「してはならないこと」を示し、テストで検出を確認する。
BAD_EXAMPLES: tuple[TranslationExample, ...] = (
    TranslationExample(
        kind="bad",
        source="The paths of the rectified flow avoid crossing each other (⟦REF:fig-2⟧).",
        target="整流フローの経路は互いに交差しない。",
        note="⟦REF:fig-2⟧ を削除しており不合格(placeholder_mismatch)。",
    ),
    TranslationExample(
        kind="bad",
        source="We hypothesize that straighter paths reduce discretization error.",
        target="より直線的な経路は離散化誤差を減らす。",
        note="we hypothesize の限定を落としており不合格。正: 「…減らすという仮説を立てる。」",
    ),
    TranslationExample(
        kind="bad",
        source="The model achieves 92.5 percent accuracy on the benchmark.",
        target="このモデルはベンチマークで高い精度を達成する。",
        note="数値 92.5 が訳文から欠落しており不合格(number_mismatch)。",
    ),
    TranslationExample(
        kind="bad",
        source="We use ⟦MATH:m-1⟧ and ⟦MATH:m-2⟧ together.",
        target="⟦MATH:m-1⟧ を使う。",
        note="⟦MATH:m-2⟧ が欠落しており不合格(placeholder_mismatch)。",
    ),
    TranslationExample(
        kind="bad",
        source="This may work in practice.",
        target="This may work in practice.",
        note="英文がそのまま残っており不合格(untranslated)。",
    ),
    TranslationExample(
        kind="bad",
        source="The results are consistent.",
        target="結果は一貫している。ドリフトはノイズを表し、輸送は直線化され、"
        "サンプル品質が大幅に向上し、離散化誤差も減少する。",
        note="原文にない情報を大量に補っており不合格(length_outlier・幻覚)。",
    ),
)

TRANSLATION_EXAMPLES: tuple[TranslationExample, ...] = GOOD_EXAMPLES + BAD_EXAMPLES


def format_examples(examples: tuple[TranslationExample, ...]) -> str:
    """system[0] 埋め込み用の対訳例テキスト(plans/06 §5.1 の書式)。"""
    lines: list[str] = []
    for ex in examples:
        tag = "GOOD" if ex.kind == "good" else "BAD"
        lines.append(f"[{tag}] 原文: {ex.source}")
        suffix = f"   ← {ex.note}" if ex.note else ""
        lines.append(f"      訳文: {ex.target}{suffix}")
    return "\n".join(lines)
