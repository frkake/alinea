"""arq タスク実装(各 ``jobs.kind`` のハンドラ)。

このパッケージを import すると各ハンドラが :data:`yakudoku_worker.main.HANDLERS` に登録される。
ワーカー起動時(``yakudoku_worker.main`` のエントリ)にこのパッケージを import すること。
"""

from yakudoku_worker.main import HANDLERS
from yakudoku_worker.tasks.translate import run_translation_job

# kind='translation'(全 reason を run_translation_job が振り分ける。plans/06 §3.1)。
HANDLERS["translation"] = run_translation_job

__all__ = ["run_translation_job"]
