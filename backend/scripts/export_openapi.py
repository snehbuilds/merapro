"""Write the API's OpenAPI document to packages/sdk/openapi.json (input to SDK generation).

Builds the app without starting it, so no database is needed. Run from backend/:
    uv run python scripts/export_openapi.py [--check]
``--check`` exits 1 if the committed file differs: the CI drift check (docs/10 §7).
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MERA_ENV", "development")
os.environ.setdefault("MERA_SECRET_KEY", "export-only-secret-key-0123456789abcdef")
os.environ.setdefault("MERA_MASTER_KEY", base64.b64encode(bytes(range(32))).decode())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://export:x@localhost:1/export")

from mera.api.main import create_app

TARGET = Path(__file__).resolve().parents[2] / "packages" / "sdk" / "openapi.json"


def main() -> int:
    spec = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != spec:
            print(f"{TARGET} is out of date: run `make sdk`", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(spec, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
