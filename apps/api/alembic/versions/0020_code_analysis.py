"""GitHub コード対応解析: estimates / runs / correspondences + job/route 値域拡張(Task 21)。

docs/superpowers/specs/2026-07-17-huggingface-code-correspondence-design.md §10-§11。

新設テーブル:
- ``code_analysis_estimates`` — 実行前見積り(commit SHA・対象規模・概算 token/費用・失効時刻)。
- ``code_analysis_runs`` — 解析実行単位。一意制約
  ``(user_id, revision_id, resource_id, commit_sha, analysis_version)`` で二重課金を防ぐ。
- ``code_correspondences`` — サーバー検証済みの対応(paper 側 anchor + code 側の path/行範囲/excerpt)。

値域拡張(いずれも既存値を保った superset):
- ``ck_jobs_kind`` に ``code_analysis``。
- ``ck_jobs_status`` に ``waiting_budget``(予算不足で外部 API を呼ばず待機する状態)。
- ``ck_llm_task_routes_task`` / ``ck_user_task_model_overrides_task`` に ``code_analysis``。
- routing.yaml の ``code_analysis`` ルート(既定 claude-sonnet-5)を llm_task_routes へ seed。

Integration note: plan の記載は 0018 だが、統合後の実 alembic head は
``0019_jats_source_format``(T17=0019 / T19=0016 / T13=0020系 が線形化済み)。本 migration は
その head へ一意な revision id ``0020_code_analysis`` で連結する。並行タスクが別 head を作った
場合は統合コントローラが merge 時に線形化する。DB が届かない環境では適用を Task 32 へ委譲する。

派生データではない: runs / correspondences は生成コストの高いユーザーデータ。完全バックアップ
(export/import)へ含める。BYOK 秘密鍵はこれらのテーブルへ一切保存しない。
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

revision: str = "0020_code_analysis"
down_revision: str | None = "0019_jats_source_format"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATE_ESTIMATES = """
CREATE TABLE code_analysis_estimates (
    id                        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                   UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    library_item_id           UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    resource_id               UUID        NOT NULL REFERENCES resource_links(id) ON DELETE CASCADE,
    revision_id               UUID        NOT NULL REFERENCES document_revisions(id) ON DELETE CASCADE,
    commit_sha                TEXT        NOT NULL,
    analysis_version          TEXT        NOT NULL,
    files                     INT         NOT NULL DEFAULT 0,
    estimated_input_tokens    BIGINT      NOT NULL DEFAULT 0,
    estimated_output_tokens   BIGINT      NOT NULL DEFAULT 0,
    estimated_embedding_tokens BIGINT     NOT NULL DEFAULT 0,
    estimated_cost_usd        NUMERIC(12, 4) NOT NULL DEFAULT 0,
    model_id                  TEXT        NOT NULL DEFAULT '',
    section_ids               TEXT[]      NOT NULL DEFAULT '{}',
    expires_at                TIMESTAMPTZ NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_RUNS = """
CREATE TABLE code_analysis_runs (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    library_item_id    UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    resource_id        UUID        NOT NULL REFERENCES resource_links(id) ON DELETE CASCADE,
    revision_id        UUID        NOT NULL REFERENCES document_revisions(id) ON DELETE CASCADE,
    commit_sha         TEXT        NOT NULL,
    analysis_version   TEXT        NOT NULL,
    trigger            TEXT        NOT NULL DEFAULT 'on_demand',
    status             TEXT        NOT NULL DEFAULT 'queued',
    stale              BOOLEAN     NOT NULL DEFAULT false,
    estimated_cost_usd NUMERIC(12, 4) NOT NULL DEFAULT 0,
    actual_cost_usd    NUMERIC(12, 8) NOT NULL DEFAULT 0,
    error              TEXT,
    job_id             UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_code_analysis_runs_status
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled', 'waiting_budget')),
    CONSTRAINT ck_code_analysis_runs_trigger
        CHECK (trigger IN ('on_demand', 'automatic', 'rerun')),
    CONSTRAINT uq_code_analysis_runs_target
        UNIQUE (user_id, revision_id, resource_id, commit_sha, analysis_version)
)
"""

_CREATE_CORRESPONDENCES = """
CREATE TABLE code_correspondences (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id         UUID        NOT NULL REFERENCES code_analysis_runs(id) ON DELETE CASCADE,
    position       INT         NOT NULL DEFAULT 0,
    paper_anchor   JSONB       NOT NULL,
    claim_text     TEXT        NOT NULL DEFAULT '',
    path           TEXT        NOT NULL,
    symbol         TEXT        NOT NULL DEFAULT '',
    start_line     INT         NOT NULL,
    end_line       INT         NOT NULL,
    code_excerpt   TEXT        NOT NULL DEFAULT '',
    explanation_ja TEXT        NOT NULL DEFAULT '',
    confidence     TEXT        NOT NULL DEFAULT 'medium',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_code_correspondences_confidence
        CHECK (confidence IN ('high', 'medium', 'low'))
)
"""

_INDEXES = (
    "CREATE INDEX ix_code_analysis_estimates_user ON code_analysis_estimates (user_id, expires_at)",
    "CREATE INDEX ix_code_analysis_runs_item ON code_analysis_runs (library_item_id, status)",
    "CREATE INDEX ix_code_analysis_runs_user ON code_analysis_runs (user_id)",
    "CREATE INDEX ix_code_correspondences_run ON code_correspondences (run_id, position)",
)
_RUNS_TRIGGER = (
    "CREATE TRIGGER trg_code_analysis_runs_updated_at BEFORE UPDATE ON code_analysis_runs "
    "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
)


# --------------------------------------------------------------------------- #
# ck_jobs_kind: 0019 適用後の値域 + code_analysis。
# --------------------------------------------------------------------------- #
_JOBS_KIND_WITH_CA = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'paper_export', 'import', 'account_delete', "
    "'index_embeddings', 'presentation', 'code_analysis'))"
)
_JOBS_KIND_ORIGINAL = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'paper_export', 'import', 'account_delete', "
    "'index_embeddings', 'presentation'))"
)

# ck_jobs_status: 0008 適用後の値域 + waiting_budget。
_JOBS_STATUS_WITH_BUDGET = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status CHECK (status IN "
    "('queued', 'running', 'waiting_quota', 'waiting_input', 'waiting_budget', "
    "'succeeded', 'failed', 'canceled'))"
)
_JOBS_STATUS_ORIGINAL = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status CHECK (status IN "
    "('queued', 'running', 'waiting_quota', 'waiting_input', "
    "'succeeded', 'failed', 'canceled'))"
)

# LLM タスク値域(0018 の 9 タスク)+ code_analysis。
_TASKS_WITH_CA = (
    "'translation', 'retranslation_escalation', 'chat', 'summary', 'article', "
    "'overview_figure_dsl', 'vocab', 'explainer_image', 'presentation', 'code_analysis'"
)
_TASKS_ORIGINAL = (
    "'translation', 'retranslation_escalation', 'chat', 'summary', 'article', "
    "'overview_figure_dsl', 'vocab', 'explainer_image', 'presentation'"
)

# notifications.kind 値域(0001 の 3 種)+ 予算不足通知。
_NOTIF_KIND_WITH_CA = (
    "ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind CHECK (kind IN "
    "('translation_complete', 'status_suggestion', 'deadline_reminder', "
    "'code_analysis_waiting_budget'))"
)
_NOTIF_KIND_ORIGINAL = (
    "ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind CHECK (kind IN "
    "('translation_complete', 'status_suggestion', 'deadline_reminder'))"
)

# usage_records.task 値域(0001 の inline 無名 CHECK)+ code_analysis。名前付き制約へ置換する。
_USAGE_TASK_WITH_CA = (
    "ALTER TABLE usage_records ADD CONSTRAINT ck_usage_records_task CHECK (task IN "
    "('translation', 'retranslation_escalation', 'chat', 'summary', 'article', "
    "'overview_figure_dsl', 'vocab', 'explainer_image', 'key_test', 'code_analysis'))"
)
_USAGE_TASK_ORIGINAL = (
    "ALTER TABLE usage_records ADD CONSTRAINT ck_usage_records_task CHECK (task IN "
    "('translation', 'retranslation_escalation', 'chat', 'summary', 'article', "
    "'overview_figure_dsl', 'vocab', 'explainer_image', 'key_test'))"
)


def upgrade() -> None:
    op.execute(_CREATE_ESTIMATES)
    op.execute(_CREATE_RUNS)
    op.execute(_CREATE_CORRESPONDENCES)
    for stmt in _INDEXES:
        op.execute(stmt)
    op.execute(_RUNS_TRIGGER)

    # jobs.kind / jobs.status を superset へ。
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_WITH_CA)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_status")
    op.execute(_JOBS_STATUS_WITH_BUDGET)

    # llm_task_routes.task / user_task_model_overrides.task に code_analysis を許可。
    op.execute("ALTER TABLE llm_task_routes DROP CONSTRAINT IF EXISTS llm_task_routes_task_check")
    op.execute("ALTER TABLE llm_task_routes DROP CONSTRAINT IF EXISTS ck_llm_task_routes_task")
    op.execute(
        "ALTER TABLE llm_task_routes ADD CONSTRAINT ck_llm_task_routes_task "
        f"CHECK (task IN ({_TASKS_WITH_CA}))"
    )
    op.execute(
        "ALTER TABLE user_task_model_overrides "
        "DROP CONSTRAINT IF EXISTS ck_user_task_model_overrides_task"
    )
    op.execute(
        "ALTER TABLE user_task_model_overrides ADD CONSTRAINT ck_user_task_model_overrides_task "
        f"CHECK (task IN ({_TASKS_WITH_CA}))"
    )

    # notifications.kind に予算不足通知を許可。
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS ck_notifications_kind")
    op.execute(_NOTIF_KIND_WITH_CA)

    # usage_records.task に code_analysis を許可(0001 の inline 無名 CHECK を named へ置換)。
    op.execute("ALTER TABLE usage_records DROP CONSTRAINT IF EXISTS usage_records_task_check")
    op.execute("ALTER TABLE usage_records DROP CONSTRAINT IF EXISTS ck_usage_records_task")
    op.execute(_USAGE_TASK_WITH_CA)

    _seed_code_analysis_route()


def _seed_code_analysis_route() -> None:
    seed_dir = Path(alinea_llm.__file__).resolve().parents[2]
    data: dict[str, Any] = yaml.safe_load((seed_dir / "routing.yaml").read_text(encoding="utf-8"))
    spec = (data.get("tasks") or {}).get("code_analysis")
    if not spec:
        return
    params = {k: v for k, v in spec.items() if k != "chain"}
    stmt = sa.text(
        "INSERT INTO llm_task_routes (task, chain, params) "
        "VALUES ('code_analysis', :chain, :params) ON CONFLICT (task) DO NOTHING"
    ).bindparams(
        sa.bindparam("chain", type_=ARRAY(sa.Text())),
        sa.bindparam("params", type_=JSONB()),
    )
    op.get_bind().execute(stmt, {"chain": list(spec["chain"]), "params": params})


def downgrade() -> None:
    # usage_records.task を 0001 相当(named 制約で戻す)。code_analysis 実績行を先に除去。
    op.execute("DELETE FROM usage_records WHERE task = 'code_analysis'")
    op.execute("ALTER TABLE usage_records DROP CONSTRAINT IF EXISTS ck_usage_records_task")
    op.execute(_USAGE_TASK_ORIGINAL)

    # notifications.kind を 0001 相当へ戻す(予算不足通知を先に除去)。
    op.execute("DELETE FROM notifications WHERE kind = 'code_analysis_waiting_budget'")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS ck_notifications_kind")
    op.execute(_NOTIF_KIND_ORIGINAL)

    op.execute("DELETE FROM user_task_model_overrides WHERE task = 'code_analysis'")
    op.execute(
        "ALTER TABLE user_task_model_overrides "
        "DROP CONSTRAINT IF EXISTS ck_user_task_model_overrides_task"
    )
    op.execute(
        "ALTER TABLE user_task_model_overrides ADD CONSTRAINT ck_user_task_model_overrides_task "
        f"CHECK (task IN ({_TASKS_ORIGINAL}))"
    )

    op.execute("DELETE FROM llm_task_routes WHERE task = 'code_analysis'")
    op.execute("ALTER TABLE llm_task_routes DROP CONSTRAINT IF EXISTS ck_llm_task_routes_task")
    op.execute(
        "ALTER TABLE llm_task_routes ADD CONSTRAINT ck_llm_task_routes_task "
        f"CHECK (task IN ({_TASKS_ORIGINAL}))"
    )

    # waiting_budget の job 行を先に寄せてから status 値域を戻す。
    op.execute("UPDATE jobs SET status = 'failed' WHERE status = 'waiting_budget'")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_status")
    op.execute(_JOBS_STATUS_ORIGINAL)

    op.execute("DELETE FROM jobs WHERE kind = 'code_analysis'")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_ORIGINAL)

    op.execute("DROP TABLE IF EXISTS code_correspondences CASCADE")
    op.execute("DROP TABLE IF EXISTS code_analysis_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS code_analysis_estimates CASCADE")
