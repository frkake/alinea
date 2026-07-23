"""Add presentation_artifacts table + presentation LLM/job kinds (Task 28).

Revision ID: 0018_presentation_artifacts
Revises: 0017_publication_comments

論文→PPTX プレゼンテーション生成(Task 28)の成果物メタデータを保持する
``presentation_artifacts`` を新設する。成果物は library_item ごとに最新版のみを持つ
(``library_item_id`` UNIQUE)。PPTX 本体は S3(assets バケット)の job 別 key
``presentations/{library_item_id}/{job_id}.pptx`` を指し、再生成時も既存 key を上書きしない
(no-overwrite key。旧成功は失敗した再生成でも生き残る)。

併せてルーティング/ジョブの値域へ ``presentation`` を追加する:
- ``ck_jobs_kind`` に ``presentation`` を union(既存値域を保った超集合。0013/0016 と同方針)。
- ``ck_llm_task_routes_task`` に ``presentation``(0002 の無名 inline CHECK を named 制約へ置換)。
- ``ck_user_task_model_overrides_task`` を新設し ``presentation`` を含む既知タスクへ限定する
  (0002 は user_task_model_overrides.task に CHECK を付けていない。設定ブリッジが
  task='presentation' を upsert するため値域を明示する)。

Integration note: plan の記載は 0021 だが、統合後の実 alembic head は
``0017_publication_comments``(T24=0015 / T19=0016 / T25=0017 が線形化済み)。本 migration は
その head へ一意な revision id ``0018_presentation_artifacts`` で連結する。並行タスクが同時に
別 head を作り得るため複数 head が生じるが、統合コントローラが merge 時に線形化する(必要なら
down_revision を付け替える)。DB が届かない/整合しない環境では適用を Task 32 へ委譲する。

派生データではない: PPTX は LLM+ppt-master で生成した成果物で、完全バックアップ
(export_user_data)に metadata + PPTX バイトを含める(BYOK 秘密鍵は一切保存しない)。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import alinea_llm
import sqlalchemy as sa
import yaml  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0018_presentation_artifacts"
down_revision: str | None = "0017_publication_comments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --------------------------------------------------------------------------- #
# presentation_artifacts
# --------------------------------------------------------------------------- #
_CREATE_TABLE = """
CREATE TABLE presentation_artifacts (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    library_item_id      UUID        NOT NULL UNIQUE
                                     REFERENCES library_items(id) ON DELETE CASCADE,
    source_revision_id   UUID        NOT NULL REFERENCES document_revisions(id),
    generation_job_id    UUID,
    preset               TEXT        NOT NULL,
    audience             TEXT        NOT NULL,
    instruction          TEXT        NOT NULL DEFAULT '',
    model_provider       TEXT        NOT NULL DEFAULT '',
    model_id             TEXT        NOT NULL DEFAULT '',
    ppt_master_revision  TEXT        NOT NULL DEFAULT '',
    pptx_storage_key     TEXT        NOT NULL,
    generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_presentation_artifacts_preset
        CHECK (preset IN ('reading_group', 'research_talk', 'implementation'))
)
"""

_SOURCE_REVISION_INDEX = (
    "CREATE INDEX ix_presentation_artifacts_source_revision "
    "ON presentation_artifacts (source_revision_id)"
)
_TRIGGER = (
    "CREATE TRIGGER trg_presentation_artifacts_updated_at BEFORE UPDATE "
    "ON presentation_artifacts FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
)


# --------------------------------------------------------------------------- #
# ck_jobs_kind に 'presentation' を union(0017 適用後の値域 = base + import +
# vocab_extract + paper_export + index_embeddings に presentation を足す)。
# --------------------------------------------------------------------------- #
_JOBS_KIND_WITH_PRESENTATION = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'paper_export', 'import', 'account_delete', "
    "'index_embeddings', 'presentation'))"
)
_JOBS_KIND_ORIGINAL = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'paper_export', 'import', 'account_delete', "
    "'index_embeddings'))"
)

# LLM タスク値域(0002 の 8 タスク)+ presentation。
_TASKS_WITH_PRESENTATION = (
    "'translation', 'retranslation_escalation', 'chat', 'summary', 'article', "
    "'overview_figure_dsl', 'vocab', 'explainer_image', 'presentation'"
)
_TASKS_ORIGINAL = (
    "'translation', 'retranslation_escalation', 'chat', 'summary', 'article', "
    "'overview_figure_dsl', 'vocab', 'explainer_image'"
)


def upgrade() -> None:
    op.execute(_CREATE_TABLE)
    op.execute(_SOURCE_REVISION_INDEX)
    op.execute(_TRIGGER)

    # jobs.kind 値域を presentation を含む超集合へ。
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_WITH_PRESENTATION)

    # llm_task_routes.task: 0002 の無名 inline CHECK(llm_task_routes_task_check)を
    # named 制約 ck_llm_task_routes_task へ置換し presentation を許可する。
    op.execute("ALTER TABLE llm_task_routes DROP CONSTRAINT IF EXISTS llm_task_routes_task_check")
    op.execute("ALTER TABLE llm_task_routes DROP CONSTRAINT IF EXISTS ck_llm_task_routes_task")
    op.execute(
        "ALTER TABLE llm_task_routes ADD CONSTRAINT ck_llm_task_routes_task "
        f"CHECK (task IN ({_TASKS_WITH_PRESENTATION}))"
    )

    # user_task_model_overrides.task: 0002 は CHECK 未設定。設定ブリッジが task を upsert する
    # ため named 制約で既知タスク(+ presentation)へ限定する。
    op.execute(
        "ALTER TABLE user_task_model_overrides "
        "DROP CONSTRAINT IF EXISTS ck_user_task_model_overrides_task"
    )
    op.execute(
        "ALTER TABLE user_task_model_overrides ADD CONSTRAINT ck_user_task_model_overrides_task "
        f"CHECK (task IN ({_TASKS_WITH_PRESENTATION}))"
    )

    # presentation ルートを llm_task_routes へシードする(routing.yaml の presentation を投入。
    # 0002 は後発タスクをシードしないため、ここで投入する。冪等 ON CONFLICT DO NOTHING)。
    _seed_presentation_route()


def _seed_presentation_route() -> None:
    seed_dir = Path(alinea_llm.__file__).resolve().parents[2]
    data: dict[str, Any] = yaml.safe_load((seed_dir / "routing.yaml").read_text(encoding="utf-8"))
    spec = (data.get("tasks") or {}).get("presentation")
    if not spec:
        return
    params = {k: v for k, v in spec.items() if k != "chain"}
    stmt = sa.text(
        "INSERT INTO llm_task_routes (task, chain, params) "
        "VALUES ('presentation', :chain, :params) ON CONFLICT (task) DO NOTHING"
    ).bindparams(
        sa.bindparam("chain", type_=ARRAY(sa.Text())),
        sa.bindparam("params", type_=JSONB()),
    )
    op.get_bind().execute(stmt, {"chain": list(spec["chain"]), "params": params})


def downgrade() -> None:
    # user_task_model_overrides の CHECK を外す(0002 の状態 = CHECK 無しへ戻す)。
    op.execute("DELETE FROM user_task_model_overrides WHERE task = 'presentation'")
    op.execute(
        "ALTER TABLE user_task_model_overrides "
        "DROP CONSTRAINT IF EXISTS ck_user_task_model_overrides_task"
    )

    # llm_task_routes を 0002 相当(named ck_llm_task_routes_task で 8 タスク)へ戻す。
    op.execute("DELETE FROM llm_task_routes WHERE task = 'presentation'")
    op.execute("ALTER TABLE llm_task_routes DROP CONSTRAINT IF EXISTS ck_llm_task_routes_task")
    op.execute(
        "ALTER TABLE llm_task_routes ADD CONSTRAINT ck_llm_task_routes_task "
        f"CHECK (task IN ({_TASKS_ORIGINAL}))"
    )

    # ck_jobs_kind を 0017 相当へ戻す(presentation の job 行を先に除去)。
    op.execute("DELETE FROM jobs WHERE kind = 'presentation'")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_ORIGINAL)

    op.execute("DROP TABLE IF EXISTS presentation_artifacts CASCADE")
