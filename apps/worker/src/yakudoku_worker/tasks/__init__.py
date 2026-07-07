"""arq タスク実装(各 ``jobs.kind`` のハンドラ)。

このパッケージを import すると各ハンドラが :data:`yakudoku_worker.main.HANDLERS` に登録される。
ワーカー起動時(``yakudoku_worker.main`` のエントリ)にこのパッケージを import すること。
"""

from yakudoku_worker.main import HANDLERS
from yakudoku_worker.tasks.fetch_resource_meta import run_fetch_resource_meta_job
from yakudoku_worker.tasks.generate_vocab_ai import run_generate_vocab_ai
from yakudoku_worker.tasks.ingest import ingest_paper
from yakudoku_worker.tasks.translate import run_translation_job

# kind='translation'(全 reason を run_translation_job が振り分ける。plans/06 §3.1)。
HANDLERS["translation"] = run_translation_job
# kind='ingest'(8 段階ステートマシン。plans/05 §2・M0-18)。
HANDLERS["ingest"] = ingest_paper
# kind='vocab'(語彙 8 フィールド AI 生成。plans/07 §7・M2-11)。
HANDLERS["vocab"] = run_generate_vocab_ai
# kind='resource_meta'(リソースメタ再取得。plans/03 §12・M2-13)。
HANDLERS["resource_meta"] = run_fetch_resource_meta_job

__all__ = [
    "ingest_paper",
    "run_fetch_resource_meta_job",
    "run_generate_vocab_ai",
    "run_translation_job",
]
