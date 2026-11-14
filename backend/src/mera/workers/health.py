"""Worker liveness for Compose and orchestrators.

The worker touches a heartbeat file every ``INTERVAL`` seconds from its own event loop, so a
wedged loop stops the heartbeat even while the process is alive.
``python -m mera.workers.health`` exits 0 if the file is fresher than ``STALE_AFTER``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

HEARTBEAT_PATH = Path(os.environ.get("MERA_WORKER_HEARTBEAT", "/tmp/mera-worker-heartbeat"))  # noqa: S108
INTERVAL = 15.0
STALE_AFTER = 60.0


async def heartbeat(path: Path, *, interval: float = INTERVAL) -> None:
    while True:
        await asyncio.to_thread(path.write_text, str(time.time()), encoding="utf-8")
        await asyncio.sleep(interval)


def is_healthy(path: Path, *, now: float | None = None, stale_after: float = STALE_AFTER) -> bool:
    try:
        age = (now if now is not None else time.time()) - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age < stale_after


if __name__ == "__main__":
    sys.exit(0 if is_healthy(HEARTBEAT_PATH) else 1)
