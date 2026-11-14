"""Post templates (P2-M1.4, docs/04 §4.5).

A template fills in what a person would otherwise type every time: the brief, an outline
for the Writer to follow, default hashtags and the usual target groups. Starting from a
template creates an ordinary post set; each account's post is still written for that
account and approved on its own, so a template can never put identical text on several
accounts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.hashtags import normalize_tag
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.errors import constraint_name
from mera.infrastructure.db.models import AccountGroup, PostTemplate, Workspace
from mera.infrastructure.db.repositories.audit import record

MAX_TEMPLATES = 200
MAX_TAGS = 30


@dataclass(frozen=True, slots=True)
class TemplateFields:
    name: str | None = None
    description: str | None = None
    topic: str | None = None
    outline: str | None = None
    hashtags: list[str] | None = None
    target_group_ids: list[uuid.UUID] | None = None


@dataclass(frozen=True, slots=True)
class TemplateView:
    id: uuid.UUID
    name: str
    description: str | None
    topic: str
    outline: str | None
    hashtags: list[str]
    target_group_ids: list[uuid.UUID]
    use_count: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AppliedTemplate:
    """What a new post set takes from the template."""

    title: str
    topic: str
    outline: str | None
    hashtags: list[str]
    account_group_ids: list[uuid.UUID] = field(default_factory=list)

    def brief(self, topic: str | None) -> str:
        """The brief a post set starts from: the person's own brief when they wrote one,
        else the template's, with the template's outline and hashtags either way."""
        return brief_with_outline(topic or self.topic, self.outline, self.hashtags)


def _view(t: PostTemplate) -> TemplateView:
    return TemplateView(
        t.id,
        t.name,
        t.description,
        t.topic,
        t.outline,
        list(t.hashtags),
        list(t.target_group_ids),
        t.use_count,
        t.updated_at,
    )


def brief_with_outline(topic: str, outline: str | None, hashtags: list[str] | None = None) -> str:
    parts = [topic.strip()]
    if outline and outline.strip():
        parts.append(f"Structure to follow (from the template):\n{outline.strip()}")
    if hashtags:
        parts.append("Hashtags the template suggests: " + " ".join(f"#{h}" for h in hashtags))
    return "\n\n".join(parts)


class TemplateService(WorkspaceScopedService):
    async def list_templates(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[TemplateView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            rows = (await s.execute(select(PostTemplate).order_by(PostTemplate.name))).scalars()
            return [_view(t) for t in rows]

    async def create(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        fields: TemplateFields,
    ) -> TemplateView:
        if not (fields.name or "").strip():
            raise InvalidInput("Name the template.", code="invalid_name", field="name")
        if not (fields.topic or "").strip():
            raise InvalidInput("Write the brief.", code="invalid_topic", field="topic")
        try:
            async with self._tx(user, workspace_id, meta) as s:
                principal = await load_user_principal(s, user, workspace_id)
                rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
                await self._writable(s, workspace_id)
                if len(list((await s.execute(select(PostTemplate.id))).scalars())) >= MAX_TEMPLATES:
                    raise InvalidInput(
                        f"At most {MAX_TEMPLATES} templates.", code="template_limit_reached"
                    )
                t = PostTemplate(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    name="",
                    topic="",
                    created_by=user.user_id,
                )
                await _apply(s, t, fields)
                s.add(t)
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "post_template.created",
                        workspace_id=workspace_id,
                        subject_type="post_template",
                        subject_id=t.id,
                        after={"name": t.name},
                    ),
                )
                return _view(t)
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_post_template_name":
                raise
            raise ConflictError(
                "A template with that name exists.", code="template_name_taken", field="name"
            ) from exc

    async def update(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        meta: RequestMeta,
        fields: TemplateFields,
    ) -> TemplateView:
        try:
            async with self._tx(user, workspace_id, meta) as s:
                principal = await load_user_principal(s, user, workspace_id)
                rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
                await self._writable(s, workspace_id)
                t = await s.get(PostTemplate, template_id, with_for_update=True)
                if t is None:
                    raise NotFoundError("No such template.", code="template_not_found")
                await _apply(s, t, fields)
                t.updated_at = datetime.now(UTC)
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "post_template.updated",
                        workspace_id=workspace_id,
                        subject_type="post_template",
                        subject_id=t.id,
                        after={"name": t.name},
                    ),
                )
                return _view(t)
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_post_template_name":
                raise
            raise ConflictError(
                "A template with that name exists.", code="template_name_taken", field="name"
            ) from exc

    async def delete(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            t = await s.get(PostTemplate, template_id, with_for_update=True)
            if t is None:
                raise NotFoundError("No such template.", code="template_not_found")
            await s.delete(t)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "post_template.deleted",
                    workspace_id=workspace_id,
                    subject_type="post_template",
                    subject_id=template_id,
                    before={"name": t.name},
                ),
            )

    async def apply(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        meta: RequestMeta,
    ) -> AppliedTemplate:
        """Read a template for a new post set and count the use. Groups that no longer
        exist are dropped; targeting is still checked against the caller's scope when the
        set is created."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            t = await s.get(PostTemplate, template_id)
            if t is None:
                raise InvalidInput(
                    "Unknown template.", code="unknown_template", field="template_id"
                )
            live = set(
                (
                    await s.execute(
                        select(AccountGroup.id).where(AccountGroup.id.in_(t.target_group_ids))
                    )
                ).scalars()
            )
            await s.execute(
                update(PostTemplate)
                .where(PostTemplate.id == t.id)
                .values(use_count=PostTemplate.use_count + 1)
            )
            return AppliedTemplate(
                title=t.name,
                topic=t.topic,
                outline=t.outline,
                hashtags=list(t.hashtags),
                account_group_ids=[g for g in t.target_group_ids if g in live],
            )

    async def _writable(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        ws = await s.get(Workspace, workspace_id)
        assert ws is not None
        self._ensure_writable(ws)


async def _apply(s: AsyncSession, t: PostTemplate, f: TemplateFields) -> None:
    if f.name is not None:
        name = f.name.strip()
        if not 1 <= len(name) <= 120:
            raise InvalidInput("Up to 120 characters.", code="invalid_name", field="name")
        t.name = name
    if f.description is not None:
        t.description = f.description.strip()[:500] or None
    if f.topic is not None:
        topic = f.topic.strip()
        if not 1 <= len(topic) <= 2000:
            raise InvalidInput("Up to 2000 characters.", code="invalid_topic", field="topic")
        t.topic = topic
    if f.outline is not None:
        outline = f.outline.strip()
        if len(outline) > 4000:
            raise InvalidInput("Up to 4000 characters.", code="invalid_outline", field="outline")
        t.outline = outline or None
    if f.hashtags is not None:
        tags = list(dict.fromkeys(normalize_tag(h) for h in f.hashtags))
        if len(tags) > MAX_TAGS:
            raise InvalidInput(f"At most {MAX_TAGS} hashtags.", code="too_many_hashtags")
        t.hashtags = tags
    if f.target_group_ids is not None:
        ids = list(dict.fromkeys(f.target_group_ids))
        found = set(
            (await s.execute(select(AccountGroup.id).where(AccountGroup.id.in_(ids)))).scalars()
        )
        if set(ids) - found:
            raise InvalidInput("Unknown group.", code="unknown_group", field="target_group_ids")
        t.target_group_ids = ids
