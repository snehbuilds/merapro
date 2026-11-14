"""The drafting job on the ``agents`` queue (M5.13).

One job per post set at a time (``lock``), so a rewrite never races a generation of the
same set. The job is idempotent in effect: it only ever adds versions.

A revision requested from an approval card (``trigger="approval_edit"``, M6.10) asks for
approval again, for exactly the revised accounts, as soon as their new versions exist.
"""

from __future__ import annotations

import uuid

import procrastinate
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.drafting.factory import build_drafting
from mera.application.drafting.pipeline import Drafting
from mera.application.services.approval_service import ApprovalService
from mera.config import get_settings
from mera.infrastructure.db.session import create_engine, create_sessionmaker
from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer

blueprint = procrastinate.Blueprint()
TASK_NAME = "drafting:draft"

_deps: tuple[async_sessionmaker[AsyncSession], Drafting, ApprovalService] | None = None


def _worker_deps() -> tuple[async_sessionmaker[AsyncSession], Drafting, ApprovalService]:
    global _deps
    if _deps is None:
        settings = get_settings()
        sm = create_sessionmaker(create_engine(settings))
        _deps = (
            sm,
            build_drafting(settings, sm),
            ApprovalService(sm, ProcrastinateDeferrer(settings.libpq_dsn)),
        )
    return _deps


@blueprint.task(name="draft", queue="agents")
async def draft_task(
    workspace_id: str,
    post_set_id: str,
    mode: str = "create",
    variant_ids: list[str] | None = None,
    instruction: str | None = None,
    user_id: str | None = None,
    trigger: str = "manual",
) -> None:
    _sm, drafting, approvals = _worker_deps()
    ws, ps = uuid.UUID(workspace_id), uuid.UUID(post_set_id)
    ids = [uuid.UUID(v) for v in variant_ids] if variant_ids else None
    await drafting.draft(
        ws,
        ps,
        variant_ids=ids,
        mode=mode,
        instruction=instruction,
        user_id=uuid.UUID(user_id) if user_id else None,
    )
    if trigger == "approval_edit" and ids and user_id:
        await approvals.resubmit_after_edit(ws, ps, ids, uuid.UUID(user_id))


@blueprint.task(name="orchestrate", queue="agents")
async def orchestrate_task(workspace_id: str, run_id: str, user_id: str) -> None:
    """P2-M7.3: one orchestration run; resumable from its first unfinished stage."""
    from mera.application.services.orchestration_service import OrchestrationService
    from mera.infrastructure.queue.deferrer import ProcrastinateDeferrer
    from mera.workers.tasks.media import worker_media

    sm, drafting, _approvals = _worker_deps()
    orch = OrchestrationService(
        sm, drafting, ProcrastinateDeferrer(get_settings().libpq_dsn), worker_media()
    )
    await orch.run(uuid.UUID(workspace_id), uuid.UUID(run_id), uuid.UUID(user_id))
