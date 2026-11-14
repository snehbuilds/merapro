"""Workspace cloning (P3-M5.1 and 5.2, docs/03 §3.2).

A clone copies how a workspace is set up and nothing it has done or holds: settings,
brand voices, content pillars, sources (and which groups they feed), approval policies,
group *shapes*, the hashtag registry and post templates. It never copies connected
accounts, credentials, content, history or members: carrying an account into a clone
would put one account under two workspaces' authority. The person cloning becomes the
new workspace's only Owner. Autopilot starts at manual: it is earned per workspace, on
the accounts that workspace holds.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import Forbidden
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.role import Role
from mera.infrastructure.db.base import Base
from mera.infrastructure.db.models import (
    AccountGroup,
    ApprovalPolicy,
    BrandVoice,
    ContentPillar,
    Hashtag,
    PostTemplate,
    Source,
    SourceGroupLink,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import workspace_transaction

# Run state that belongs to the original, not to a fresh copy.
_RESET: Final = {
    "id",
    "workspace_id",
    "created_at",
    "updated_at",
    "created_by",
    "created_by_user_id",
    "http_etag",
    "http_last_modified",
    "last_fetched_at",
    "last_success_at",
    "last_error",
    "last_http_status",
    "last_yield",
    "consecutive_failures",
    "failing_since",
    "next_fetch_at",
    "use_count",
    "first_used_at",
    "learned_preferences",
}


def _copy(row: Base, ws: uuid.UUID, **override: Any) -> dict[str, Any]:
    out = {c.key: getattr(row, c.key) for c in row.__table__.columns if c.key not in _RESET}
    out.update(id=uuid7(), workspace_id=ws, **override)
    return out


class CloneService(WorkspaceScopedService):
    def __init__(self, sm: Any, workspaces: Any) -> None:
        super().__init__(sm)
        self._workspaces = workspaces

    async def clone(
        self,
        user: AuthenticatedUser,
        source_id: uuid.UUID,
        meta: RequestMeta,
        *,
        name: str,
        slug: str,
    ) -> Any:
        async with self._tx(user, source_id, meta) as s:
            principal = await load_user_principal(s, user, source_id)
            if principal.role is not Role.OWNER:
                raise Forbidden("Only an Owner can clone a workspace.", code="owner_only")
            src = await s.get(Workspace, source_id)
            assert src is not None
            snapshot = await self._read(s)
            settings = dict(src.settings or {})
            timezone, wtype = src.timezone, src.workspace_type
        settings["autopilot_mode"] = "manual"
        settings.pop("analytics", None)
        from mera.domain.value_objects.workspace import WorkspaceType

        view = await self._workspaces.create(
            user,
            name=name,
            slug=slug,
            workspace_type=WorkspaceType(wtype),
            timezone=timezone,
            meta=meta,
        )
        new_id: uuid.UUID = view.id
        async with workspace_transaction(self._sm, WorkspaceContext(new_id)) as s:
            ws = await s.get(Workspace, new_id, with_for_update=True)
            assert ws is not None
            ws.settings, ws.cloned_from_workspace_id = settings, source_id
            groups: dict[uuid.UUID, uuid.UUID] = {}
            for g in snapshot["groups"]:
                data = _copy(g, new_id, content_pillar_ids=[], default_brand_voice_id=None)
                data["settings"] = {
                    k: v
                    for k, v in (data.get("settings") or {}).items()
                    if k not in ("autopilot_mode", "assisted_threshold")
                }
                groups[g.id] = data["id"]
                s.add(AccountGroup(**data))
            await s.flush()
            pillars: dict[uuid.UUID, uuid.UUID] = {}
            for p in snapshot["pillars"]:
                if p.account_group_id is not None and p.account_group_id not in groups:
                    continue
                data = _copy(p, new_id, account_group_id=groups.get(p.account_group_id))
                pillars[p.id] = data["id"]
                s.add(ContentPillar(**data))
            voices: dict[uuid.UUID, uuid.UUID] = {}
            for v in snapshot["voices"]:
                if v.scope_type == "social_account":
                    continue  # an account's own voice stays with the account
                scope = groups.get(v.scope_id) if v.scope_type == "account_group" else new_id
                if v.scope_type == "account_group" and scope is None:
                    continue
                data = _copy(v, new_id, scope_id=scope)
                voices[v.id] = data["id"]
                s.add(BrandVoice(**data))
            await s.flush()
            for g in snapshot["groups"]:
                ng = await s.get(AccountGroup, groups[g.id])
                assert ng is not None
                ng.content_pillar_ids = [pillars[p] for p in g.content_pillar_ids if p in pillars]
                ng.default_brand_voice_id = (
                    voices.get(g.default_brand_voice_id) if g.default_brand_voice_id else None
                )
            sources: dict[uuid.UUID, uuid.UUID] = {}
            for src_row in snapshot["sources"]:
                data = _copy(src_row, new_id)
                sources[src_row.id] = data["id"]
                s.add(Source(**data))
            await s.flush()
            for link in snapshot["links"]:
                if link.source_id in sources and link.account_group_id in groups:
                    s.add(
                        SourceGroupLink(
                            **_copy(
                                link,
                                new_id,
                                source_id=sources[link.source_id],
                                account_group_id=groups[link.account_group_id],
                            )
                        )
                    )
            for pol in snapshot["policies"]:
                if pol.scope_type == "social_account":
                    continue
                if pol.scope_type == "account_group" and pol.scope_id not in groups:
                    continue
                s.add(
                    ApprovalPolicy(
                        **_copy(
                            pol,
                            new_id,
                            scope_id=groups.get(pol.scope_id)
                            if pol.scope_type == "account_group"
                            else None,
                            # People are not copied, so neither are named approvers.
                            approver_user_ids=[],
                            escalate_to_user_ids=[],
                            escalation_chain=[],
                        )
                    )
                )
            for h in snapshot["hashtags"]:
                s.add(Hashtag(**_copy(h, new_id)))
            for t in snapshot["templates"]:
                s.add(
                    PostTemplate(
                        **_copy(
                            t,
                            new_id,
                            target_group_ids=[groups[g] for g in t.target_group_ids if g in groups],
                        )
                    )
                )
            await record(
                s,
                AuditEntry(
                    action="workspace.cloned",
                    actor_type="user",
                    actor_id=user.user_id,
                    actor_label=user.display_name,
                    surface=meta.surface,
                    workspace_id=new_id,
                    subject_type="workspace",
                    subject_id=new_id,
                    after={"from": str(source_id), "groups": len(groups), "sources": len(sources)},
                    ip_address=meta.ip_address,
                    request_id=meta.request_id,
                ),
            )
        return view

    @staticmethod
    async def _read(s: AsyncSession) -> dict[str, list[Any]]:
        async def all_of(model: Any) -> list[Any]:
            return list((await s.execute(select(model))).scalars())

        return {
            "groups": await all_of(AccountGroup),
            "pillars": await all_of(ContentPillar),
            "voices": await all_of(BrandVoice),
            "sources": await all_of(Source),
            "links": await all_of(SourceGroupLink),
            "policies": await all_of(ApprovalPolicy),
            "hashtags": await all_of(Hashtag),
            "templates": await all_of(PostTemplate),
        }
