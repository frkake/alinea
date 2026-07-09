"""LLM ルーティング設定テーブル + シード(plans/04 §10・§15、plans/07 §9.2)。

0001_initial には plans/04 §10・§15 の 3 テーブル(llm_models / llm_task_routes /
user_task_model_overrides)が漏れているため、この 1 本で追加する。列挙は TEXT+CHECK、
命名は plans/04 §10・§15 の逐語。併せて packages/llm/models.yaml・routing.yaml の
シード値を投入し、plans/07 §9.2 の月次クォータ上限(quota_limits)の既定値もシードする
(0001 が seed していないための逸脱修正)。

Revision ID: 0002_llm_routing
Revises: 0001_initial
Create Date: 2026-07-06
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import alinea_llm
import sqlalchemy as sa
import yaml  # type: ignore[import-untyped]
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0002_llm_routing"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


# ------------------------------------------------------------------
# DDL(plans/04 §15 の CREATE TABLE 逐語)
# ------------------------------------------------------------------
UPGRADE_DDL = r"""
CREATE TABLE llm_models (
    id            TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    modality      TEXT NOT NULL CHECK (modality IN ('text', 'image')),
    display_name  TEXT NOT NULL,
    spec          JSONB NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT true,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_llm_models_updated_at BEFORE UPDATE ON llm_models
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE llm_task_routes (
    task            TEXT PRIMARY KEY CHECK (task IN (
                      'translation', 'retranslation_escalation', 'chat', 'summary',
                      'article', 'overview_figure_dsl', 'vocab', 'explainer_image')),
    chain           TEXT[] NOT NULL,
    params          JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_llm_task_routes_updated_at BEFORE UPDATE ON llm_task_routes
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE user_task_model_overrides (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task        TEXT NOT NULL,
    model_id    TEXT NOT NULL REFERENCES llm_models(id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, task)
);
CREATE TRIGGER trg_user_task_model_overrides_updated_at BEFORE UPDATE
    ON user_task_model_overrides
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

DOWNGRADE_DDL = r"""
DROP TABLE IF EXISTS user_task_model_overrides CASCADE;
DROP TABLE IF EXISTS llm_task_routes CASCADE;
DROP TABLE IF EXISTS llm_models CASCADE;
"""

# plans/07 §9.2 の月次クォータ既定上限(quota_limits にシード)。
QUOTA_DEFAULTS: dict[str, int] = {
    "translation_papers": 30,
    "chat_messages": 500,
    "images": 20,
    "article_generations": 30,
    "vocab_generations": 300,
}


def _llm_seed_dir() -> Path:
    # packages/llm/{models.yaml,routing.yaml}(alinea_llm パッケージの 2 つ上)
    return Path(alinea_llm.__file__).resolve().parents[2]


def _load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((_llm_seed_dir() / name).read_text(encoding="utf-8"))


def _model_rows() -> list[dict[str, Any]]:
    data = _load_yaml("models.yaml")
    rows: list[dict[str, Any]] = []
    for raw in data.get("text_models") or []:
        spec = {
            k: v
            for k, v in raw.items()
            if k in {"context_window", "max_output_tokens", "pricing", "capabilities"}
        }
        rows.append(
            {
                "id": raw["id"],
                "provider": raw["provider"],
                "modality": "text",
                "display_name": raw["display_name"],
                "spec": spec,
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    for raw in data.get("image_models") or []:
        spec = {k: v for k, v in raw.items() if k in {"pricing_per_image"}}
        rows.append(
            {
                "id": raw["id"],
                "provider": raw["provider"],
                "modality": "image",
                "display_name": raw["display_name"],
                "spec": spec,
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    return rows


def _route_rows() -> list[dict[str, Any]]:
    data = _load_yaml("routing.yaml")
    rows: list[dict[str, Any]] = []
    for task, spec in (data.get("tasks") or {}).items():
        params = {k: v for k, v in spec.items() if k != "chain"}
        rows.append({"task": task, "chain": list(spec["chain"]), "params": params})
    return rows


def upgrade() -> None:
    for stmt in filter(None, (s.strip() for s in UPGRADE_DDL.split(";"))):
        op.execute(stmt)

    bind = op.get_bind()

    model_stmt = sa.text(
        "INSERT INTO llm_models (id, provider, modality, display_name, spec, enabled) "
        "VALUES (:id, :provider, :modality, :display_name, :spec, :enabled) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(sa.bindparam("spec", type_=JSONB()))
    for row in _model_rows():
        bind.execute(model_stmt, row)

    route_stmt = sa.text(
        "INSERT INTO llm_task_routes (task, chain, params) "
        "VALUES (:task, :chain, :params) "
        "ON CONFLICT (task) DO NOTHING"
    ).bindparams(
        sa.bindparam("chain", type_=ARRAY(sa.Text())),
        sa.bindparam("params", type_=JSONB()),
    )
    for row in _route_rows():
        bind.execute(route_stmt, row)

    quota_stmt = sa.text(
        "INSERT INTO quota_limits (key, monthly_limit) VALUES (:key, :monthly_limit) "
        "ON CONFLICT (key) DO NOTHING"
    )
    for key, limit in QUOTA_DEFAULTS.items():
        bind.execute(quota_stmt, {"key": key, "monthly_limit": limit})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM quota_limits WHERE key = ANY(:keys)").bindparams(
            sa.bindparam("keys", type_=ARRAY(sa.Text()))
        ),
        {"keys": list(QUOTA_DEFAULTS.keys())},
    )
    for stmt in filter(None, (s.strip() for s in DOWNGRADE_DDL.split(";"))):
        op.execute(stmt)
