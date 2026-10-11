"""Контракт API в файл для фронта: `python -m app.tools.export_openapi [путь]`.

По умолчанию пишет `../frontend/src/api/openapi.json`; из него `npm run gen:api` строит типы.
Тест `test_openapi_snapshot_is_current` ловит расхождение, если схему забыли обновить.
"""

import json
import sys
from pathlib import Path

from app.main import create_app

DEFAULT = Path(__file__).resolve().parents[3] / "frontend" / "src" / "api" / "openapi.json"


def schema_text() -> str:
    return json.dumps(create_app().openapi(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    out.write_text(schema_text(), encoding="utf-8", newline=chr(10))
    print(f"записано: {out}")
