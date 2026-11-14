"""Real Postgres via testcontainers. Skips cleanly when Docker is unavailable.

Two connections matter:
- ``owner_url``: the superuser that runs migrations (bypasses RLS — never used by the app).
- ``app_url``: ``mera_app``, a NOSUPERUSER NOBYPASSRLS login, exactly like production.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

BACKEND = Path(__file__).resolve().parents[2]
APP_PASSWORD = "app-test-pw"


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def postgres_urls() -> Iterator[tuple[str, str]]:
    if not _docker_available():
        pytest.skip("Docker is not running; integration tests need testcontainers Postgres")
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        owner_url = pg.get_connection_url()
        # Create the login role first, as infra/postgres/init does in Compose.
        import psycopg

        libpq = owner_url.replace("postgresql+asyncpg://", "postgresql://")
        with psycopg.connect(libpq, autocommit=True) as conn:
            conn.execute(
                f"CREATE ROLE mera_app LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS"
            )
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        cfg.set_main_option("sqlalchemy.url", owner_url)
        command.upgrade(cfg, "head")

        user_part = owner_url.split("://", 1)[1].split("@", 1)[0]
        app_url = owner_url.replace(user_part, f"mera_app:{APP_PASSWORD}", 1)
        yield owner_url, app_url


@pytest.fixture(scope="session")
async def app_engine(postgres_urls: tuple[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres_urls[1])
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def owner_engine(postgres_urls: tuple[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres_urls[0])
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def app_sessionmaker(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture(scope="session")
async def api(postgres_urls: tuple[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, running its lifespan against the RLS-bound app role."""
    # The suite signs in far more often than the 10/min auth limit allows from one client;
    # rate limiting has its own tests with it enabled (see make_api).
    async with make_api(postgres_urls, rate_limit_enabled=False) as c:
        yield c


@asynccontextmanager
async def make_api(
    postgres_urls: tuple[str, str], *, client_ip: str = "127.0.0.1", **overrides: object
) -> AsyncIterator[httpx.AsyncClient]:
    from mera.api.main import create_app
    from mera.config import Settings, get_settings

    settings = Settings(
        **{
            **get_settings().model_dump(),
            "database_url": postgres_urls[1],
            "sandbox_llm": True,
            # Uploads and renditions go to a throwaway directory, not the working tree.
            "media_root": tempfile.mkdtemp(prefix="mera-media-"),
            **overrides,
        }
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=(client_ip, 50000))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as c,
    ):
        yield c
