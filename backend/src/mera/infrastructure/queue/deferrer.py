"""Deferring jobs from the API process (M5).

procrastinate's async connector needs psycopg's async mode, which cannot run on Windows'
default Proactor loop (the API's dev loop). Deferring is one INSERT, so the API uses the
**sync** connector in a worker thread instead: it works on any event loop and never blocks
the loop. Tasks are addressed by name (``namespace:task``), so the API does not import
worker code.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Protocol

import procrastinate
from procrastinate.exceptions import AlreadyEnqueued


class Deferrer(Protocol):
    async def defer(
        self,
        task_name: str,
        *,
        queue: str,
        lock: str | None = None,
        queueing_lock: str | None = None,
        **kwargs: Any,
    ) -> bool:
        """Queue a job. ``False`` when an identical job is already waiting."""
        ...


class ProcrastinateDeferrer:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._app: procrastinate.App | None = None
        self._lock = threading.Lock()

    def _open(self) -> procrastinate.App:
        with self._lock:
            if self._app is None:
                app = procrastinate.App(
                    connector=procrastinate.SyncPsycopgConnector(conninfo=self._dsn)
                )
                app.open()
                self._app = app
            return self._app

    async def defer(
        self,
        task_name: str,
        *,
        queue: str,
        lock: str | None = None,
        queueing_lock: str | None = None,
        **kwargs: Any,
    ) -> bool:
        def go() -> bool:
            app = self._open()
            try:
                app.configure_task(
                    task_name,
                    allow_unknown=True,
                    queue=queue,
                    lock=lock,
                    queueing_lock=queueing_lock,
                ).defer(**kwargs)
            except AlreadyEnqueued:
                return False
            return True

        return await asyncio.to_thread(go)

    def close(self) -> None:
        with self._lock:
            if self._app is not None:
                self._app.close()
                self._app = None
