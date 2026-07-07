"""フィクスチャ読み込みヘルパ(packages/figures テスト専用)。

決定: この共有ヘルパを ``conftest.py`` ではなく ``_data.py``(通常モジュール)に置く。
複数のテストディレクトリ(``packages/figures/tests`` と ``apps/worker/tests`` など)を
1 回の pytest 実行で同時に指定すると、双方の ``conftest.py`` が ``__init__.py`` を持たないため
``conftest`` という同名モジュールとして ``sys.modules`` に衝突登録され得る
(``from conftest import ...`` が別ディレクトリの conftest を指してしまう)。モジュール名を
``_data`` に分離してこの衝突を避ける。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = Path(__file__).parent / "golden"


def load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


__all__ = ["FIXTURES_DIR", "GOLDEN_DIR", "load_fixture"]
