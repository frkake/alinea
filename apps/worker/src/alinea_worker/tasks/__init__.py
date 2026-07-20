"""arq タスク実装(各 ``jobs.kind`` のハンドラ)。

このパッケージを import すると各ハンドラが :data:`alinea_worker.main.HANDLERS` に登録される。
ワーカー起動時(``alinea_worker.main`` のエントリ)にこのパッケージを import すること。
"""

from alinea_worker.main import HANDLERS
from alinea_worker.tasks.export_paper import run_export_paper_job
from alinea_worker.tasks.export_user_data import run_export_full_job
from alinea_worker.tasks.extract_vocab_candidates import run_extract_vocab_candidates
from alinea_worker.tasks.fetch_resource_meta import run_fetch_resource_meta_job
from alinea_worker.tasks.generate_explainer_figure import run_figure_job
from alinea_worker.tasks.generate_vocab_ai import run_generate_vocab_ai
from alinea_worker.tasks.import_user_data import run_import_full_job
from alinea_worker.tasks.ingest import ingest_paper
from alinea_worker.tasks.translate import run_translation_job

# kind='translation'(全 reason を run_translation_job が振り分ける。plans/06 §3.1)。
HANDLERS["translation"] = run_translation_job
# kind='ingest'(8 段階ステートマシン。plans/05 §2・M0-18)。
HANDLERS["ingest"] = ingest_paper
# kind='vocab'(語彙 8 フィールド AI 生成。plans/07 §7・M2-11)。
HANDLERS["vocab"] = run_generate_vocab_ai
# kind='vocab_extract'(AI 単語抽出。S7。spec 2026-07-16-ai-word-extraction-design)。
HANDLERS["vocab_extract"] = run_extract_vocab_candidates
# kind='resource_meta'(リソースメタ再取得。plans/03 §12・M2-13)。
HANDLERS["resource_meta"] = run_fetch_resource_meta_job
# kind='figure'(payload.figure_kind で overview/explainer を振り分け。plans/07 §5・§6)。
HANDLERS["figure"] = run_figure_job
# kind='export'(JSON 一括エクスポート。plans/03 §18・M2-15)。
HANDLERS["export"] = run_export_full_job
# kind='paper_export'(論文単位スタンドアロンエクスポート。Feature S3・Task 11)。
HANDLERS["paper_export"] = run_export_paper_job
# kind='import'(zip 展開+冪等マージ復元。完全データ移行 Task 4)。
HANDLERS["import"] = run_import_full_job

__all__ = [
    "ingest_paper",
    "run_export_full_job",
    "run_export_paper_job",
    "run_extract_vocab_candidates",
    "run_fetch_resource_meta_job",
    "run_figure_job",
    "run_generate_vocab_ai",
    "run_import_full_job",
    "run_translation_job",
]
