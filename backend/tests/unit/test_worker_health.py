from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from mera.workers.health import heartbeat, is_healthy


def test_missing_file_is_unhealthy(tmp_path: Path) -> None:
    assert not is_healthy(tmp_path / "nope")


def test_fresh_and_stale(tmp_path: Path) -> None:
    f = tmp_path / "beat"
    f.write_text("x")
    assert is_healthy(f)
    old = time.time() - 120
    os.utime(f, (old, old))
    assert not is_healthy(f)


async def test_heartbeat_writes_until_cancelled(tmp_path: Path) -> None:
    f = tmp_path / "beat"
    task = asyncio.create_task(heartbeat(f, interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert is_healthy(f)
