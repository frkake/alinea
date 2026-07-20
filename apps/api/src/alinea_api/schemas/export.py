"""export エンドポイントの DTO と Markdown/BibTeX レンダラ(M1-16。plans/03 §18、docs/00 P5、
docs/06 §10・§11)。

- レスポンスは ``text/markdown`` / ``application/x-bibtex`` の添付ファイル(JSON ではない)
  ため、他スキーマモジュールと異なり本モジュールは「DB から取り出した値を受け取り文字列を
  返す純関数」を主体とする(``schemas/library.py`` の ``build_paper_bib`` 等と同じ方針)。
  DB アクセスは ``routers/export.py`` の責務とし、本モジュールは pytest から直接呼べる
  純関数として単体テスト可能にする(PY-EXP-02)。
- ファイル名(決定・plans/03 §18): arXiv 論文は ``{arxiv_id}.md``、それ以外はタイトルの
  ASCII slug(小文字・ハイフン区切り・最大 80 字)``.md``。
- CSV・全量 JSON エクスポートは M2-15(本書では実装しない)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

import yaml  # type: ignore[import-untyped]  # PyYAML は uvicorn[standard] 経由(型スタブ未導入)
from pydantic import BaseModel

from alinea_api.schemas.common import PaperBib

# ============================================================================
# 論文単位スタンドアロンエクスポート API(Feature S3・Task 12)
# ============================================================================
# 選択可能な成果物(worker の ``export_paper._ALL_ARTIFACTS`` および availability API と 1:1)。
StandaloneArtifact = Literal[
    "source_html",
    "translation_html",
    "bilingual_html",
    "article_html",
    "pdf_original",
    "pdf_translated",
    "pdf_bilingual",
]


class PaperExportRequest(BaseModel):
    """``POST .../export/standalone`` の本文。複数選択した成果物(値域は Literal で検証)。"""

    artifacts: list[StandaloneArtifact]

# ============================================================================
# レンダリング入力(DB から解決済みの値のみを持つ、DB 非依存の値オブジェクト)
# ============================================================================


@dataclass(frozen=True)
class ExportNote:
    title: str
    body_md: str


@dataclass(frozen=True)
class ExportAnnotation:
    """plans/03 §8.1 Annotation の表示済み値(``anchor.display`` は §2.2 のセクション表記)。"""

    kind: str  # "highlight" | "bookmark"
    color: str | None
    comment: str | None
    quote: str | None
    display: str
    placed: bool


@dataclass(frozen=True)
class ExportChatMessage:
    role: str  # "user" | "assistant"
    text: str


@dataclass(frozen=True)
class ExportChatThread:
    title: str
    messages: list[ExportChatMessage] = field(default_factory=list)


@dataclass(frozen=True)
class ExportResource:
    kind: str
    title: str
    url: str
    note_md: str = ""


# ============================================================================
# ファイル名(§18 決定)
# ============================================================================
_SLUG_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")


def slugify_title(title: str, *, max_len: int = 80) -> str:
    """タイトルの ASCII slug(小文字・ハイフン区切り・最大 ``max_len`` 字)。"""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_COLLAPSE_RE.sub("-", ascii_title.lower()).strip("-")
    return (slug or "paper")[:max_len].strip("-") or "paper"


def export_filename(paper: PaperBib, *, suffix: str = "") -> str:
    """arXiv 論文は ``{arxiv_id}{suffix}.md``、それ以外はタイトル slug(§18 決定)。"""
    base = paper.arxiv_id if paper.arxiv_id else slugify_title(paper.title)
    return f"{base}{suffix}.md"


# ============================================================================
# 注釈の Markdown 化(export/markdown・export/annotations の共通部)
# ============================================================================
def _annotation_lines(annotations: list[ExportAnnotation]) -> list[str]:
    if not annotations:
        return ["_注釈はありません。_"]
    lines: list[str] = []
    for ann in annotations:
        marker = "🔖" if ann.kind == "bookmark" else "🖍"
        color = f"[{ann.color}] " if ann.color else ""
        loc = f"({ann.display})" if ann.display else ""
        quote = f"「{ann.quote}」" if ann.quote else ""
        placed_note = " *(未配置)*" if not ann.placed else ""
        head = " ".join(part for part in (marker + " " + color + quote, loc) if part.strip())
        lines.append(f"- {head}{placed_note}".rstrip())
        if ann.comment:
            lines.append(f"  > {ann.comment}")
    return lines


def render_annotations_markdown(*, paper_title: str, annotations: list[ExportAnnotation]) -> str:
    """GET .../export/annotations(plans/03 §18・docs/04 §5「⤓ Markdown エクスポート」)。"""
    lines = [f"# {paper_title} — 注釈", ""]
    lines.extend(_annotation_lines(annotations))
    lines.append("")
    return "\n".join(lines)


# ============================================================================
# 論文単位 Markdown(Obsidian 互換 front-matter。plans/03 §18)
# ============================================================================
def _front_matter(
    paper: PaperBib,
    *,
    status: str,
    priority: str | None,
    tags: list[str],
    added_at: str,
    finished_at: str | None,
) -> str:
    data: dict[str, object] = {
        "title": paper.title,
        "authors": list(paper.authors),
        "year": paper.year,
        "venue": paper.venue,
        "arxiv_id": paper.arxiv_id,
        "doi": paper.doi,
        "status": status,
        "priority": priority,
        "tags": list(tags),
        "added_at": added_at,
        "finished_at": finished_at,
        "source": "alinea",
    }
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{body}---\n"


def render_paper_markdown(
    *,
    paper: PaperBib,
    status: str,
    priority: str | None,
    tags: list[str],
    added_at: str,
    finished_at: str | None,
    one_line_note: str,
    notes: list[ExportNote],
    annotations: list[ExportAnnotation],
    chat_threads: list[ExportChatThread],
    resources: list[ExportResource],
) -> str:
    """GET /api/library-items/{id}/export/markdown(plans/03 §18)。

    Obsidian 互換 front-matter + 書誌 + メモ + 注釈 + チャット履歴 + リソース一覧
    (種類・タイトル・URL・メモ)を 1 つの Markdown にまとめる。
    """
    lines: list[str] = [
        _front_matter(
            paper,
            status=status,
            priority=priority,
            tags=tags,
            added_at=added_at,
            finished_at=finished_at,
        ).rstrip("\n"),
        "",
        f"# {paper.title}",
        "",
        f"**著者**: {', '.join(paper.authors) or '—'}  ",
    ]
    if paper.venue:
        lines.append(f"**会議・雑誌**: {paper.venue}  ")
    if paper.arxiv_id:
        lines.append(f"**arXiv**: {paper.arxiv_id}  ")
    if paper.doi:
        lines.append(f"**DOI**: {paper.doi}  ")
    lines.append("")
    if one_line_note:
        lines.extend([f"> {one_line_note}", ""])

    lines.append("## メモ")
    lines.append("")
    if notes:
        for note in notes:
            lines.extend([f"### {note.title or '(無題)'}", "", note.body_md, ""])
    else:
        lines.extend(["_メモはありません。_", ""])

    lines.append("## 注釈")
    lines.append("")
    lines.extend(_annotation_lines(annotations))
    lines.append("")

    lines.append("## チャット履歴")
    lines.append("")
    if chat_threads:
        for thread in chat_threads:
            lines.extend([f"### {thread.title}", ""])
            for msg in thread.messages:
                speaker = "あなた" if msg.role == "user" else "アシスタント"
                lines.extend([f"**{speaker}**: {msg.text}", ""])
    else:
        lines.extend(["_チャット履歴はありません。_", ""])

    lines.append("## リソース")
    lines.append("")
    if resources:
        for res in resources:
            note_part = f" — {res.note_md}" if res.note_md else ""
            lines.append(f"- [{res.kind}] {res.title or res.url} — {res.url}{note_part}")
    else:
        lines.append("_リソースはありません。_")
    lines.append("")

    return "\n".join(lines)


# ============================================================================
# BibTeX(docs/06 §10・§11「主要リファレンスマネージャで読み込める」)
# ============================================================================
_KEY_STRIP_RE = re.compile(r"[^a-z0-9]")


def _cite_key_base(paper: PaperBib) -> str:
    last = paper.authors[0].split()[-1] if paper.authors and paper.authors[0].split() else "paper"
    ascii_last = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode("ascii")
    slug = _KEY_STRIP_RE.sub("", ascii_last.lower()) or "paper"
    year = str(paper.year) if paper.year is not None else "nd"
    return f"{slug}{year}"


def unique_cite_key(paper: PaperBib, used: set[str]) -> str:
    """既出キーと衝突しないよう a/b/c… を付す(複数論文の一括 BibTeX 用)。"""
    base = _cite_key_base(paper)
    key = base
    suffix = ord("a")
    while key in used:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    used.add(key)
    return key


def _bibtex_escape(value: str) -> str:
    return value.replace("{", "\\{").replace("}", "\\}")


def render_bibtex_entry(paper: PaperBib, *, cite_key: str) -> str:
    """1 件の BibTeX エントリ(必須フィールド author/title/year。arXiv は eprint も。PY-EXP-02)。"""
    entry_type = "misc" if paper.arxiv_id else "article"
    fields: list[tuple[str, str]] = [
        ("author", " and ".join(paper.authors) if paper.authors else "Unknown"),
        ("title", paper.title),
    ]
    if paper.year is not None:
        fields.append(("year", str(paper.year)))
    if paper.arxiv_id:
        fields.append(("eprint", paper.arxiv_id))
        fields.append(("archivePrefix", "arXiv"))
    if paper.venue:
        fields.append(("journal" if entry_type == "article" else "note", paper.venue))
    if paper.doi:
        fields.append(("doi", paper.doi))
    body = ",\n".join(f"  {k} = {{{_bibtex_escape(v)}}}" for k, v in fields)
    return f"@{entry_type}{{{cite_key},\n{body}\n}}"


def render_bibtex(papers: list[PaperBib]) -> str:
    """GET /api/export/bibtex(無指定=全件。plans/03 §18)。"""
    used: set[str] = set()
    entries = [
        render_bibtex_entry(paper, cite_key=unique_cite_key(paper, used)) for paper in papers
    ]
    return "\n\n".join(entries) + ("\n" if entries else "")


# ============================================================================
# インポート API スキーマ(完全データ移行 Task 5)
# ============================================================================
# NOTE: Pydantic モデルは routers/export.py に直接定義する(循環 import 回避のため
#       schemas/export.py は DB 非依存の純関数に限定する方針)。
