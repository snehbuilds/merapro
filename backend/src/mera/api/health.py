"""``/healthz`` (process is up) and ``/readyz`` (DB reachable, migrations applied)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from mera.api.errors import ServiceUnavailable
from mera.infrastructure.db.session import global_transaction

router = APIRouter(tags=["health"])
log = structlog.get_logger(__name__)


class Health(BaseModel):
    status: str


class Readiness(BaseModel):
    status: str
    database: str
    migration_revision: str


@router.get("/healthz", response_model=Health)
async def healthz() -> Health:
    return Health(status="ok")


@router.get("/readyz", response_model=Readiness)
async def readyz(request: Request) -> Readiness:
    sessionmaker = request.app.state.sessionmaker
    try:
        async with global_transaction(sessionmaker) as session:
            revision = (
                await session.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except Exception as exc:  # any driver/network failure means not ready
        log.warning("readyz.database_unavailable", error_type=type(exc).__name__, error=str(exc))
        raise ServiceUnavailable("Database is unavailable.", headers={"Retry-After": "5"}) from exc
    if revision is None:
        raise ServiceUnavailable("Migrations have not been applied.", code="migrations_pending")
    return Readiness(status="ready", database="ok", migration_revision=revision)
