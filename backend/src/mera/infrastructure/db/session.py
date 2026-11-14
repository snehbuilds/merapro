"""Async engine and the tenant-scoped transaction (ADR-0006, layer 2).

``mera.workspace_id`` is set with ``set_config(..., is_local => true)`` — the equivalent of
``SET LOCAL`` — so it lives exactly as long as the transaction and can never leak to the
next request that borrows the pooled connection. Never use a session-level ``SET`` here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from mera.config import Settings
from mera.domain.context import WorkspaceContext

TENANT_SETTING = "mera.workspace_id"
USER_SETTING = "mera.user_id"


def create_engine(settings: Settings, *, url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
    )


class UnsafeDatabaseRole(RuntimeError):
    """The app connection could bypass row-level security."""


async def assert_rls_bound(engine: AsyncEngine) -> None:
    """Refuse to run as a role that bypasses RLS.

    A superuser or BYPASSRLS role silently disables tenancy layer 2 (ADR-0006). Pointing
    ``DATABASE_URL`` at the owner role is an easy configuration mistake, so check it at
    startup rather than trusting the URL.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user, rolsuper OR rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
    if row[1]:
        raise UnsafeDatabaseRole(
            f"database role {row[0]!r} bypasses row-level security; "
            "DATABASE_URL must use the mera_app role"
        )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


# Settings recorded on a session are re-applied at the start of *every* transaction the
# session begins. So a service that calls ``session.commit()`` mid-block keeps its tenant
# context in the transaction that autobegins next, instead of silently losing it
# (carried forward from the Sprint 1 review).
_INFO_KEY = "mera.transaction_settings"


@event.listens_for(Session, "after_begin")
def _reapply_settings(session: Session, transaction: object, connection: Connection) -> None:
    for key, value in session.info.get(_INFO_KEY, {}).items():
        connection.execute(text("SELECT set_config(:k, :v, true)"), {"k": key, "v": value})


async def _bind(session: AsyncSession, key: str, value: str) -> None:
    session.info.setdefault(_INFO_KEY, {})[key] = value
    await session.execute(text("SELECT set_config(:k, :v, true)"), {"k": key, "v": value})


async def set_workspace(session: AsyncSession, ctx: WorkspaceContext) -> None:
    await _bind(session, TENANT_SETTING, str(ctx.workspace_id))


@asynccontextmanager
async def workspace_transaction(
    sessionmaker: async_sessionmaker[AsyncSession], ctx: WorkspaceContext
) -> AsyncIterator[AsyncSession]:
    """Open a transaction with the tenant context set. Commits on success."""
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, ctx)
        yield session


@asynccontextmanager
async def user_transaction(
    sessionmaker: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    """A transaction in which an *authenticated* user can read their own memberships and
    workspaces across tenants (ADR-0006 addendum). Never call this with an unverified id:
    it is the one place identity widens reads."""
    async with sessionmaker() as session, session.begin():
        await _bind(session, USER_SETTING, str(user_id))
        yield session


# Settings that let a presented secret read exactly the one row it names (migration 0004).
_LOOKUP_SETTINGS = frozenset(
    {
        "mera.api_key_hash",
        "mera.invitation_token_hash",
        "mera.connect_state_hash",
        "mera.connect_code_hash",
        "mera.mcp_code_hash",
        "mera.mcp_token_hash",
        "mera.mcp_refresh_hash",
        "mera.mcp_flow_state_hash",
    }
)
SYSTEM_SCAN_SETTING = "mera.system_scan"


@asynccontextmanager
async def secret_lookup_transaction(
    sessionmaker: async_sessionmaker[AsyncSession], setting: str, secret_hash: str
) -> AsyncIterator[AsyncSession]:
    """A transaction in which the row whose hash matches ``secret_hash`` becomes readable.
    Read-only by policy: writes still need ``workspace_transaction``."""
    if setting not in _LOOKUP_SETTINGS:
        raise ValueError(f"unknown lookup setting {setting!r}")
    async with sessionmaker() as session, session.begin():
        await _bind(session, setting, secret_hash)
        yield session


@asynccontextmanager
async def system_scan_transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Workspace rows (only) become readable, so a periodic job can find the workspaces to
    iterate (ADR-0006 addendum 2). Read anything *inside* a workspace with
    ``workspace_transaction``. Workers only; never reachable from a request."""
    async with sessionmaker() as session, session.begin():
        await _bind(session, SYSTEM_SCAN_SETTING, "on")
        yield session


@asynccontextmanager
async def security_scan_transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Security events become readable for the alert scanner (P3-M7.1, migration 0025).
    Workers only; never reachable from a request."""
    async with sessionmaker() as session, session.begin():
        await _bind(session, "mera.security_scan", "on")
        yield session


@asynccontextmanager
async def channel_transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
    channel_type: str,
    external_id: str,
    *,
    pairing_code_hash: str | None = None,
) -> AsyncIterator[AsyncSession]:
    """The bot worker acting for one inbound chat (migration 0012): that chat's bindings and
    lockout row become visible, and, while pairing, the one code whose hash was presented.
    Nothing else: what the chat may *do* is then decided inside workspace transactions."""
    async with sessionmaker() as session, session.begin():
        await _bind(session, "mera.channel_type", channel_type)
        await _bind(session, "mera.channel_external_id", external_id)
        if pairing_code_hash is not None:
            await _bind(session, "mera.pairing_code_hash", pairing_code_hash)
        yield session


@asynccontextmanager
async def global_transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A transaction with no tenant context: tenant tables return zero rows.

    For global tables only (``user``) and health checks.
    """
    async with sessionmaker() as session, session.begin():
        yield session
