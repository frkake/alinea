"""チャットのシステムプロンプトと定型アクション(plans/07 §2.6・§2.7、docs/05 §6・§7)。

- ``CHAT_SYSTEM_PREAMBLE_TEMPLATE``: system[0] 静的プリアンブル(§2.6 逐語)。
- ``QUICK_ACTION_TEMPLATES``: 定型アクションの user メッセージ本文(§2.7 逐語)。
- ラベル/免責文(docs/05 §6・1a 逐語)は「AI生成」「論文外の知識」「推測」表示のための定数。
"""

from __future__ import annotations

from yakudoku_api.schemas.chat import QuickAction

# --- system[0] 静的プリアンブル(plans/07 §2.6 逐語) -------------------------
# 逐語の 1 段落を、内容(改行位置)を変えずに物理行 <100 字で保つため暗黙連結で組む。
CHAT_SYSTEM_PREAMBLE_TEMPLATE = (
    "あなたは論文読解ワークベンチ「訳読」の読解アシスタントです。"
    "ユーザーは「論文コンテキスト」に与えられた 1 本の論文を日本語で深く読解しています。"
    "あなたの役割は答えを述べることではなく、原文のどこを読めばよいかまで案内することです。\n"
    "\n"
    "## 回答の原則\n"
    "1. 回答は必ず論文コンテキストの原文を根拠にする。"
    "原文に書かれていないことを本文として断定しない。\n"
    "2. 本文に根拠がある主張には、主張の直後に根拠マーカーを付ける。"
    "書式は [[evidence:ブロックID]]。"
    "ブロックIDは論文コンテキストの各行頭に [ブロックID|位置] の形で示されている。\n"
    "   - 可能な限り段落・数式・図表の粒度で特定する。"
    "段落を特定できない場合のみセクションID([[evidence:sec-2-2]] の形)を使う。\n"
    "   - 1 つの主張につきマーカーは最大 3 個。回答全体で最大 20 個。\n"
    "3. 論文本文に由来しない一般知識・実装慣行・周辺文献の内容を補う場合は、"
    "その部分だけを独立した段落として <outside_knowledge> と </outside_knowledge> で囲む。\n"
    "4. 本文から断定できない推論・仮説を述べる場合は、"
    "その部分だけを独立した段落として <speculation> と </speculation> で囲む。\n"
    "5. 論文に記載のない事実を問われたら「この論文には記載がありません」と明示する。"
    "推測で埋めない(仮説を述べる場合は 4 に従う)。\n"
    "6. 回答は日本語。文体は常体(だ・である)ではなく、です・ます調。"
    "数式は $...$ または $$...$$(KaTeX 互換)。表が適切な場合は Markdown テーブルを使う。"
    "長い回答には ### 見出しを使ってよい。\n"
    "7. 出力に上記以外の独自マーカー・脚注記法・URL・免責文を含めない(免責は UI が表示する)。\n"
    "\n"
    "## 論文メタデータ\n"
    "タイトル: {title}\n"
    "著者: {authors_short} / 発表: {venue_year} / arXiv: {arxiv_id}"
)


def format_system_preamble(
    *, title: str, authors_short: str, venue_year: str, arxiv_id: str
) -> str:
    """system[0] を論文メタデータで埋める(§2.6)。"""
    return CHAT_SYSTEM_PREAMBLE_TEMPLATE.format(
        title=title or "(不明)",
        authors_short=authors_short or "(不明)",
        venue_year=venue_year or "(不明)",
        arxiv_id=arxiv_id or "(なし)",
    )


# --- 定型アクション(plans/07 §2.7 逐語テンプレート) -------------------------
QUICK_ACTION_TEMPLATES: dict[QuickAction, str] = {
    "summary_3line": (
        "この論文を次の 3 行で要約してください。①課題(何が問題か) ②手法(どう解いたか) "
        "③結果(何がどれだけ良くなったか)。"
        "各行は 80 文字以内で、行ごとに根拠マーカーを付けてください。"
    ),
    "beginner_explain": (
        "この論文を、この分野の前提知識がない読者に向けて解説してください。必要な前提概念"
        "(既存手法・用語)を先に短く補ってから、提案手法のアイデアを比喩や具体例を交えて説明して"
        "ください。前提知識の補いは <outside_knowledge> ブロックに分離してください。"
    ),
    "contributions_limits": (
        "この論文について次の 3 点を整理してください。### 主張されている貢献(箇条書き・各項目に"
        "根拠マーカー) ### 明示されている限界(論文自身が認めている制約) ### 暗黙の限界"
        "(本文の実験設定・仮定から読み取れるが明示されていない制約 — こちらは <speculation> "
        "ブロックで)。"
    ),
    "experiment_setup": (
        "この論文の実験設定を Markdown 表で整理してください。列: 実験 / データセット / "
        "ベースライン / 評価指標 / 主要ハイパーパラメータ。表の下に、本文に記載が見つからなかった"
        "項目を「記載なし」として列挙してください。各行に根拠マーカーを付けてください。"
    ),
    "implementation_points": (
        "この論文を再実装するために必要な情報を抽出してください。### 構成要素(モデル・入出力) "
        "### 学習手順(損失・最適化・スケジュール) ### 擬似コード(Python 風、コードブロックで) "
        "### 本文から読み取れない実装判断(<speculation> ブロックで代替案を提示)。根拠マーカーを"
        "付けてください。"
    ),
    "expert_summary": (
        "この分野の研究者向けに、この論文の技術的要点を 300 文字程度で要約してください。新規性が"
        "既存手法のどこを変えた点にあるかを中心に。根拠マーカーを付けてください。"
    ),
    "related_work_position": (
        "この論文が関連研究の中でどこに位置づくかを整理してください。本文の関連研究セクションで"
        "言及されている系譜(根拠マーカー付き)と、本文外の一般知識による補足(<outside_knowledge> "
        "ブロック)を分けて説明してください。"
    ),
    "detailed_summary": (
        "この論文の詳細要約を作成してください。セクション構成に沿って、### 見出し(§番号付き)ごとに "
        "2〜4 文で要約し、各見出しの要約に根拠マーカーを付けてください。全体で 600〜1,200 文字。"
        "最後に「結論と限界」を 2 文で。"
    ),
    "explain_equation": (
        "この式が何を意味するか、各記号の意味と式全体が最小化/表現しているものを直感的に説明して"
        "ください。"
    ),
    "explain_figure": (
        "この図が何を示しているか、軸・凡例・読み取るべきポイントを説明してください。"
    ),
}

# 常設チップ 5 種(docs/05 §7・1a)。入力欄上のチップ行。
PERSISTENT_QUICK_ACTIONS: tuple[QuickAction, ...] = (
    "summary_3line",
    "beginner_explain",
    "contributions_limits",
    "experiment_setup",
    "implementation_points",
)

# 入力候補 2 種(docs/05 §7)。入力ボックスのフォーカス時/入力途中の候補ポップアップ。
SUGGESTED_QUICK_ACTIONS: tuple[QuickAction, ...] = (
    "expert_summary",
    "related_work_position",
)

# 導線アクション 3 種(発生箇所から送信。3行要約カード・数式ブロック・図ポップオーバー)。
LEAD_IN_QUICK_ACTIONS: tuple[QuickAction, ...] = (
    "detailed_summary",
    "explain_equation",
    "explain_figure",
)

# 要約系(同一内容を再生成しない・§2.7.1 のリプレイ対象)。
REUSABLE_QUICK_ACTIONS: frozenset[QuickAction] = frozenset({"summary_3line", "detailed_summary"})

# --- AI 生成の明示(docs/05 §6・1a 逐語) -------------------------------------
AI_GENERATED_LABEL = "AI生成"
OUTSIDE_KNOWLEDGE_LABEL = "論文外の知識"
SPECULATION_LABEL = "推測"
# 入力エリア下部の固定免責文(docs/05 §1 逐語)。
CHAT_DISCLAIMER = (
    "回答は原文を根拠にします。本文にない内容は「論文外の知識」「推測」と表示されます。"
)


def resolve_user_content(quick_action: QuickAction | None, content: str) -> str:
    """quick_action を user メッセージ本文へ展開する(§2.7)。

    quick_action 指定時はテンプレートを本文とする(表示上もこのテキストが白カードに出る)。
    未知の quick_action や未指定時は与えられた content をそのまま使う。
    """
    if quick_action is not None:
        template = QUICK_ACTION_TEMPLATES.get(quick_action)
        if template is not None:
            return template
    return content


__all__ = [
    "AI_GENERATED_LABEL",
    "CHAT_DISCLAIMER",
    "CHAT_SYSTEM_PREAMBLE_TEMPLATE",
    "LEAD_IN_QUICK_ACTIONS",
    "OUTSIDE_KNOWLEDGE_LABEL",
    "PERSISTENT_QUICK_ACTIONS",
    "QUICK_ACTION_TEMPLATES",
    "REUSABLE_QUICK_ACTIONS",
    "SPECULATION_LABEL",
    "SUGGESTED_QUICK_ACTIONS",
    "format_system_preamble",
    "resolve_user_content",
]
