from __future__ import annotations

import base64
import os

# Minimal valid environment for unit tests; integration fixtures override DATABASE_URL.
os.environ.setdefault("MERA_ENV", "development")
os.environ.setdefault("MERA_SECRET_KEY", "test-secret-key-0123456789abcdef-0123456789")
os.environ.setdefault("MERA_MASTER_KEY", base64.b64encode(bytes(range(32))).decode())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://mera_app:x@localhost:5432/mera")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("SANDBOX_MODE", "true")


import asyncio
import sys

import pytest

if sys.platform == "win32":
    # psycopg (procrastinate's driver) cannot run on the default Proactor loop.
    @pytest.fixture(scope="session")
    def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
        return asyncio.WindowsSelectorEventLoopPolicy()
