"""M1 acceptance: RLS isolates workspaces on its own, independent of the repository filter.

ADR-0006 promises two independent layers. These tests remove layer 1 deliberately and
prove layer 2 still holds.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from mera.domain.context import WorkspaceContext
from mera.domain.value_objects import Role, uuid7
from mera.infrastructure.db.models import User, Workspace, WorkspaceMember
from mera.infrastructure.db.repositories.members import WorkspaceMemberRepository
from mera.infrastructure.db.session import (
    UnsafeDatabaseRole,
    assert_rls_bound,
    global_transaction,
    workspace_transaction,
)

pytestmark = pytest.mark.integration


async def _make_workspace(sm: async_sessionmaker[AsyncSession], name: str) -> uuid.UUID:
    ws_id = uuid7()
    async with workspace_transaction(sm, WorkspaceContext(ws_id)) as s:
        s.add(Workspace(id=ws_id, name=name, slug=f"{name}-{ws_id.hex[:8]}"))
    return ws_id


async def _make_user(sm: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    user_id = uuid7()
    async with global_transaction(sm) as s:
        s.add(User(id=user_id, email=f"{user_id.hex}@example.test", display_name="Test"))
    return user_id


async def test_rls_blocks_cross_tenant_reads_without_repository_filter(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ws_a = await _make_workspace(app_sessionmaker, "a")
    ws_b = await _make_workspace(app_sessionmaker, "b")
    user = await _make_user(app_sessionmaker)

    async with workspace_transaction(app_sessionmaker, WorkspaceContext(ws_a)) as s:
        await WorkspaceMemberRepository(s, WorkspaceContext(ws_a)).add(user, Role.OWNER)

    # Context B, and a raw select with NO workspace filter: layer 1 is removed on purpose.
    async with workspace_transaction(app_sessionmaker, WorkspaceContext(ws_b)) as s:
        rows = (await s.execute(select(WorkspaceMember))).scalars().all()
        workspaces = (await s.execute(select(Workspace))).scalars().all()
    assert rows == []
    assert [w.id for w in workspaces] == [ws_b]

    # Sanity: the row is visible from its own workspace.
    async with workspace_transaction(app_sessionmaker, WorkspaceContext(ws_a)) as s:
        rows = (await s.execute(select(WorkspaceMember))).scalars().all()
    assert [r.workspace_id for r in rows] == [ws_a]


async def test_no_context_means_zero_rows_not_all_rows(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _make_workspace(app_sessionmaker, "c")
    async with global_transaction(app_sessionmaker) as s:
        assert (await s.execute(select(Workspace))).scalars().all() == []


async def test_with_check_blocks_writing_into_another_workspace(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ws_a = await _make_workspace(app_sessionmaker, "d")
    ws_b = await _make_workspace(app_sessionmaker, "e")
    user = await _make_user(app_sessionmaker)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with workspace_transaction(app_sessionmaker, WorkspaceContext(ws_b)) as s:
            s.add(WorkspaceMember(workspace_id=ws_a, user_id=user, role="owner"))
            await s.flush()


async def test_tenant_setting_does_not_leak_past_the_transaction(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    ws = await _make_workspace(app_sessionmaker, "f")
    async with app_sessionmaker() as s:
        async with s.begin():
            await s.execute(
                text("SELECT set_config('mera.workspace_id', :v, true)"), {"v": str(ws)}
            )
        async with s.begin():
            value = (
                await s.execute(text("SELECT current_setting('mera.workspace_id', true)"))
            ).scalar()
    assert value in (None, "")


async def test_app_role_cannot_bypass_rls(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert row == (False, False)


async def test_every_tenant_table_has_enable_and_force_rls(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                      AND (c.relname = 'workspace' OR EXISTS (
                        SELECT 1 FROM information_schema.columns col
                        WHERE col.table_schema = 'public' AND col.table_name = c.relname
                          AND col.column_name = 'workspace_id'))
                    """
                )
            )
        ).all()
    assert rows, "expected tenant tables"
    unprotected = [name for name, enabled, forced in rows if not (enabled and forced)]
    assert unprotected == []


async def test_startup_guard_accepts_app_role_and_rejects_owner(
    app_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    await assert_rls_bound(app_engine)
    with pytest.raises(UnsafeDatabaseRole, match="bypasses row-level security"):
        await assert_rls_bound(owner_engine)


async def test_app_role_cannot_rewrite_alembic_version(app_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.begin() as conn:
            await conn.execute(text("UPDATE alembic_version SET version_num = '0000'"))
