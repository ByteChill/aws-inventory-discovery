"""Writes the API's OpenAPI schema to a file (default: ../frontend/openapi.json).

DATABASE_URL=... DATABASE_SCHEMA=... uv run python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402

schema = app.openapi()
# FastAPI names operations "<function>_api_v1_<path>_<method>"; the client only needs the function.
for path_item in schema["paths"].values():
    for operation in path_item.values():
        operation["operationId"] = operation["operationId"].split("_api_v1_")[0]

default = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"
target = Path(sys.argv[1]) if len(sys.argv) > 1 else default
target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {target}")
