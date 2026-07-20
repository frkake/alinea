"""Hugging Face 関連ソース候補: resource_links.kind 値域拡張(Task 18)。

docs/superpowers/specs/2026-07-17-huggingface-code-correspondence-design.md §3-§4。

Hugging Face Paper API から導出した関連ソース(Paper Page / Model / Dataset / Space /
githubRepo / projectPage)を ``resource_links`` に ``status='suggested'`` で保存できるよう、
``ck_resource_links_kind`` に ``huggingface`` と ``project`` を追加する(既存値を保った SUPERSET)。

- ``huggingface``: Hugging Face の Paper Page / Model / Dataset / Space。
- ``project``: 論文の公式プロジェクトページ(Paper API の projectPage 由来のみ)。

Integration note: plan の記載は 0016 だが、統合後の実 alembic head は ``0020_code_analysis``。
本 migration はその head へ一意な revision id ``0021_huggingface_resources`` で連結する。並行タスク
(T22)が SDK を再生成しても API スキーマ側の衝突はない。DB が届かない環境では適用を Task 32 へ委譲する。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_huggingface_resources"
down_revision: str | None = "0020_code_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 既存 4 種 + huggingface + project(SUPERSET)。
_KIND_WITH_HF = (
    "ALTER TABLE resource_links ADD CONSTRAINT ck_resource_links_kind "
    "CHECK (kind IN ('github', 'youtube', 'slides', 'article', 'huggingface', 'project'))"
)
_KIND_ORIGINAL = (
    "ALTER TABLE resource_links ADD CONSTRAINT ck_resource_links_kind "
    "CHECK (kind IN ('github', 'youtube', 'slides', 'article'))"
)


def upgrade() -> None:
    op.execute("ALTER TABLE resource_links DROP CONSTRAINT IF EXISTS ck_resource_links_kind")
    op.execute(_KIND_WITH_HF)


def downgrade() -> None:
    # 新種の行を先に除去してから 0001 相当の値域へ戻す。
    op.execute("DELETE FROM resource_links WHERE kind IN ('huggingface', 'project')")
    op.execute("ALTER TABLE resource_links DROP CONSTRAINT IF EXISTS ck_resource_links_kind")
    op.execute(_KIND_ORIGINAL)
