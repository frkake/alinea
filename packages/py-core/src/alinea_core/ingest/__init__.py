"""取り込みパイプラインの共有ロジック(plans/05 §7・§8・§10・§2.2・§11.3)。

worker(apps/worker)と api(apps/api)から共用する。arq タスク駆動そのものは
apps/worker/src/alinea_worker/pipeline.py が担う。
"""

from alinea_core.ingest.completeness import (
    DocumentCompleteness,
    assess_document_completeness,
)
from alinea_core.ingest.dedupe import (
    FUZZY_TITLE_THRESHOLD,
    PaperBibView,
    detect_duplicate,
    find_fuzzy_duplicate,
    first_author_family,
    is_fuzzy_duplicate,
    normalize_title,
)
from alinea_core.ingest.joblog import (
    append_log,
    build_timeline,
    fetch_timeline_message,
    log,
    log_entry,
    now_iso,
    project_ingest_log,
    structuring_timeline_message,
    translation_timeline_message,
)
from alinea_core.ingest.progress import (
    FIXED_STAGE_PROGRESS,
    STAGE_ORDER,
    body_progress,
    count_active_body_jobs,
    finalize_ingest_if_body_complete,
    first_translatable_section,
    readable_upto,
    stage_index,
)
from alinea_core.ingest.reanchor import ReanchorStats, reanchor_paper
from alinea_core.ingest.thumbnail import (
    CARD_2X_SIZE,
    CARD_SIZE,
    render_thumbnail,
    select_thumbnail_figure,
)

__all__ = [
    "CARD_2X_SIZE",
    "CARD_SIZE",
    "FIXED_STAGE_PROGRESS",
    "FUZZY_TITLE_THRESHOLD",
    "STAGE_ORDER",
    "DocumentCompleteness",
    "PaperBibView",
    "ReanchorStats",
    "append_log",
    "assess_document_completeness",
    "body_progress",
    "build_timeline",
    "count_active_body_jobs",
    "detect_duplicate",
    "fetch_timeline_message",
    "finalize_ingest_if_body_complete",
    "find_fuzzy_duplicate",
    "first_author_family",
    "first_translatable_section",
    "is_fuzzy_duplicate",
    "log",
    "log_entry",
    "normalize_title",
    "now_iso",
    "project_ingest_log",
    "readable_upto",
    "reanchor_paper",
    "render_thumbnail",
    "select_thumbnail_figure",
    "stage_index",
    "structuring_timeline_message",
    "translation_timeline_message",
]
