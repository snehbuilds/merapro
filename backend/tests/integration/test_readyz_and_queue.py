"""M1 acceptance: /readyz reports the applied revision; a job goes enqueue → worker → result."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from mera.api.main import create_app
from mera.config import Settings, get_settings
from mera.infrastructure.queue.app import build_app

pytestmark = pytest.mark.integration


async def test_readyz_reports_alembic_head(postgres_urls: tuple[str, str]) -> None:
    settings = Settings(**{**get_settings().model_dump(), "database_url": postgres_urls[1]})
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        r = await client.get("/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    from mera.preflight import _alembic_head

    assert body["migration_revision"] == _alembic_head()


async def test_job_is_enqueued_executed_and_succeeds(postgres_urls: tuple[str, str]) -> None:
    dsn = postgres_urls[1].replace("postgresql+asyncpg://", "postgresql://")
    app = build_app(dsn)
    async with app.open_async():
        job_id = await app.configure_task("system:echo").defer_async(message="hello")
        # wait=False: process what is queued, then return.
        await asyncio.wait_for(
            app.run_worker_async(queues=["notify"], wait=False, install_signal_handlers=False),
            timeout=30,
        )
        job = await app.job_manager.get_job_status_async(job_id)
    assert job.value == "succeeded"
