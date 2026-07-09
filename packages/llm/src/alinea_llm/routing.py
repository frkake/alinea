"""タスクルーティング(plans/04 §8・§15)。

routing.yaml はシード。実行時の正は DB(llm_task_routes / user_task_model_overrides)。
RouteResolver はタスク→モデルチェーンを解決する(YAML ベースの既定実装 + DB 上書きは
apps/api 側で拡張)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

# 8 タスクで固定(DB の CHECK 制約とも一致。§8)
Task = Literal[
    "translation",
    "retranslation_escalation",
    "chat",
    "summary",
    "article",
    "overview_figure_dsl",
    "vocab",
    "explainer_image",
]

TASKS: tuple[str, ...] = (
    "translation",
    "retranslation_escalation",
    "chat",
    "summary",
    "article",
    "overview_figure_dsl",
    "vocab",
    "explainer_image",
)


class RetryConfig(BaseModel):
    """モデル内リトライ規則(§9.1 / routing.yaml retry_defaults)。"""

    max_attempts: int = 3  # 初回 + リトライ2回
    backoff_base_s: float = 1.0
    backoff_factor: float = 4.0  # → 1s, 4s(+ full jitter)
    jitter_s: float = 1.0
    respect_retry_after: bool = True
    retry_after_cap_s: float = 30.0


class TaskRoute(BaseModel):
    task: str
    chain: list[str]
    effort: str = "none"
    max_output_tokens: int = 4096
    timeout_s: float = 120.0
    streaming: bool = False
    structured: bool = False
    # explainer_image 用
    size: str | None = None
    quality: str | None = None


class RoutingConfig(BaseModel):
    version: str = ""
    retry_defaults: RetryConfig = Field(default_factory=RetryConfig)
    tasks: dict[str, TaskRoute] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> RoutingConfig:
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        retry = RetryConfig(**(data.get("retry_defaults") or {}))
        tasks: dict[str, TaskRoute] = {}
        for name, spec in (data.get("tasks") or {}).items():
            tasks[name] = TaskRoute(task=name, **spec)
        return cls(version=str(data.get("version", "")), retry_defaults=retry, tasks=tasks)


class RouteResolver:
    """タスク→モデルチェーン解決の既定実装(YAML)。

    解決順(§15): ユーザー上書き(先頭挿入)→ 既定チェーン → disabled / キー未設定除外。
    DB ベースの解決は apps/api 側で本クラスを継承・置換する。
    """

    def __init__(
        self,
        config: RoutingConfig,
        *,
        available_providers: set[str] | None = None,
        model_provider: dict[str, str] | None = None,
        overrides: dict[tuple[str | None, str], str] | None = None,
    ) -> None:
        self._config = config
        self._available = available_providers
        self._model_provider = model_provider or {}
        self._overrides = overrides or {}

    def route(self, task: str) -> TaskRoute:
        return self._config.tasks[task]

    def chain_for(self, task: str, user_id: str | None = None) -> list[str]:
        base = list(self._config.tasks[task].chain)
        override = self._overrides.get((user_id, task))
        if override and override not in base:
            base.insert(0, override)
        elif override:
            base.remove(override)
            base.insert(0, override)
        if self._available is None:
            return base
        return [m for m in base if self._model_provider.get(m, "") in self._available]
