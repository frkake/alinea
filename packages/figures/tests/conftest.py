"""packages/figures テスト用フィクスチャ。

フィクスチャ読み込みの実体は :mod:`_data`(通常モジュール)に置く(conftest.py 同名衝突を
避けるため。同モジュールの docstring 参照)。
"""

from __future__ import annotations

from typing import Any

import pytest
from _data import load_fixture


@pytest.fixture
def rectified_flow_fixture() -> dict[str, Any]:
    return load_fixture("overview_rectified_flow.json")
