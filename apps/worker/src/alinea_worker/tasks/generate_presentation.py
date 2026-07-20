"""``kind='presentation'`` job: paper -> grounded editable Japanese PPTX (Task 29).

The heavy lifting lives in :class:`alinea_worker.presentation.runner.PresentationRunner`.
This module is the thin arq handler: it constructs the ppt-master adapter (from
the pinned submodule + dedicated venv) unless one is injected via ``ctx``, then
delegates. Registration into ``HANDLERS['presentation']`` happens in
:mod:`alinea_worker.tasks`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
from alinea_core.db.models import Job
from alinea_core.jobs.store import JobStore

from alinea_worker.presentation.ppt_master import PptMasterAdapter
from alinea_worker.presentation.runner import PptMasterConverter, PresentationRunner

log = structlog.get_logger("alinea.worker.presentation")

# Repo-root-relative locations of the pinned submodule + its dedicated venv.
# ``apps/worker/src/alinea_worker/tasks/generate_presentation.py`` -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_SUBMODULE_ROOT = _REPO_ROOT / "vendor" / "ppt-master"
_DEFAULT_VENV_PYTHON = _REPO_ROOT / ".venv-ppt-master" / "bin" / "python"


def _resolve_adapter(ctx: dict[str, Any]) -> PptMasterConverter | None:
    """Return an injected adapter, else build one from the pinned submodule.

    The subprocess environment is the adapter's allow-list (no LLM keys), so the
    upstream scripts can neither reach an LLM nor the network at runtime.
    """

    injected: PptMasterConverter | None = ctx.get("ppt_master_adapter")
    if injected is not None:
        return injected

    submodule_root = Path(
        os.environ.get("PPT_MASTER_SUBMODULE_ROOT", str(_DEFAULT_SUBMODULE_ROOT))
    )
    python_executable = Path(
        os.environ.get("PPT_MASTER_PYTHON", str(_DEFAULT_VENV_PYTHON))
    )
    if not (submodule_root / "skills" / "ppt-master" / "scripts").is_dir():
        return None
    if not python_executable.exists():
        return None
    return PptMasterAdapter(
        submodule_root=submodule_root,
        python_executable=python_executable,
        timeout_s=180.0,
    )


async def run_presentation_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """Entry point for ``jobs(kind='presentation')`` (registered in tasks/__init__)."""

    adapter = _resolve_adapter(ctx)
    runner = PresentationRunner(ctx, store, job, adapter=adapter)
    await runner.run()
    log.info("presentation.completed", job_id=str(job.id))


__all__ = ["run_presentation_job"]
