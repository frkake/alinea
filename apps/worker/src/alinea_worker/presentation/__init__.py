"""Presentation generation adapters (paper -> PPTX via pinned ppt-master).

The heavy lifting lives in :mod:`alinea_worker.presentation.ppt_master`, which
wraps the vendored ``ppt-master`` submodule behind a locked-down subprocess
boundary: fixed script order, ``shell=False``, an allow-listed environment (no
LLM API keys), a per-command timeout, and PPTX package validation.
"""

from __future__ import annotations

from alinea_worker.presentation.ppt_master import (
    ALLOWED_ENV,
    PPT_MASTER_REVISION,
    SCRIPT_ORDER,
    CommandResult,
    ConversionResult,
    PptMasterAdapter,
    PptMasterCommand,
    PptMasterError,
    PptMasterQualityError,
    validate_pptx_package,
)

__all__ = [
    "ALLOWED_ENV",
    "PPT_MASTER_REVISION",
    "SCRIPT_ORDER",
    "CommandResult",
    "ConversionResult",
    "PptMasterAdapter",
    "PptMasterCommand",
    "PptMasterError",
    "PptMasterQualityError",
    "validate_pptx_package",
]
