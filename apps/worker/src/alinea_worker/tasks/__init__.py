"""arq タスク実装(各 ``jobs.kind`` のハンドラ)。

このパッケージを import すると各ハンドラが :data:`alinea_worker.main.HANDLERS` に登録される。
ワーカー起動時(``alinea_worker.main`` のエントリ)にこのパッケージを import すること。
"""

from alinea_worker.main import HANDLERS
from alinea_worker.tasks.export_user_data import run_export_full_job
from alinea_worker.tasks.fetch_resource_meta import run_fetch_resource_meta_job
from alinea_worker.tasks.generate_explainer_figure import run_figure_job
from alinea_worker.tasks.generate_vocab_ai import run_generate_vocab_ai
from alinea_worker.tasks.ingest import ingest_paper
from alinea_worker.tasks.translate import run_translation_job

# kind='translation'(全 reason を run_translation_job が振り分ける。plans/06 §3.1)。
HANDLERS["translation"] = run_translation_job
# kind='ingest'(8 段階ステートマシン。plans/05 §2・M0-18)。
HANDLERS["ingest"] = ingest_paper
# kind='vocab'(語彙 8 フィールド AI 生成。plans/07 §7・M2-11)。
HANDLERS["vocab"] = run_generate_vocab_ai
# kind='resource_meta'(リソースメタ再取得。plans/03 §12・M2-13)。
HANDLERS["resource_meta"] = run_fetch_resource_meta_job
# kind='figure'(payload.figure_kind で overview/explainer を振り分け。plans/07 §5・§6)。
HANDLERS["figure"] = run_figure_job
# kind='export'(JSON 一括エクスポート。plans/03 §18・M2-15)。
HANDLERS["export"] = run_export_full_job

__all__ = [
    "ingest_paper",
    "run_export_full_job",
    "run_fetch_resource_meta_job",
    "run_figure_job",
    "run_generate_vocab_ai",
    "run_translation_job",
]
