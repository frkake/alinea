"""共有テスト補助(実 PostgreSQL に対して回す統合テスト向け)。

``testdb`` はテスト DB を pytest セッション(pytest-xdist の worker)ごとに分離し、
マイグレーションを適用して用意する。0002 のシード(llm_models / llm_task_routes /
quota_limits)を保持したまま各 suite を order-independent にするのが目的。
"""

from __future__ import annotations
