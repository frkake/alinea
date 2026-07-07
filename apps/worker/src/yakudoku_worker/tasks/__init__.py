"""arq タスク実装(各 ``jobs.kind`` のハンドラ)。

このパッケージを import すると各ハンドラが :data:`yakudoku_worker.main.HANDLERS` に登録される。
ワーカー起動時(``yakudoku_worker.main`` のエントリ)にこのパッケージを import すること。
"""

from yakudoku_worker.main import HANDLERS
from yakudoku_worker.tasks.ingest import ingest_paper
from yakudoku_worker.tasks.translate import run_translation_job

# kind='translation'(全 reason を run_translation_job が振り分ける。plans/06 §3.1)。
HANDLERS["translation"] = run_translation_job
# kind='ingest'(8 段階ステートマシン。plans/05 §2・M0-18)。
HANDLERS["ingest"] = ingest_paper

__all__ = ["ingest_paper", "run_translation_job"]
