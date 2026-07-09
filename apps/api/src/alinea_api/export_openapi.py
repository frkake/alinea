"""OpenAPI スキーマを stdout へ出す。`python -m alinea_api.export_openapi`(plans/03 §1.10)。"""

from __future__ import annotations

import json

from alinea_api.main import app


def main() -> None:
    print(json.dumps(app.openapi(), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
