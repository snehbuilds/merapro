"""procrastinate app and task registry (M1.7, ADR-0003).

One worker image; queues are chosen at startup (docs/01 §2.1). Task modules declare tasks
on a ``procrastinate.Blueprint`` so importing them never needs settings or a database;
``build_app`` attaches every blueprint to a concrete app.
"""

from __future__ import annotations

from typing import Final

import procrastinate

QUEUES: Final[tuple[str, ...]] = (
    "ingest",
    "agents",
    "media",
    "publish",
    "accounts",
    "metrics",
    "approvals",
    "notify",
    "integrations",
)


def build_app(dsn: str) -> procrastinate.App:
    from mera.workers.tasks import BLUEPRINTS  # local import: tasks import application code

    app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=dsn))
    for namespace, blueprint in BLUEPRINTS.items():
        app.add_tasks_from(blueprint, namespace=namespace)
    return app
