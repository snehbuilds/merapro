"""Outbound webhooks (P3-M4.3, docs/04 §4.14, docs/14 §2.6).

An Admin registers an https URL and the event types it wants. Events come from the
``event`` outbox every other part of Mera already writes to (``post.scheduled``,
``post.published``, ``approval.granted``, …). A worker turns new events into deliveries and
sends them:

- **Signed.** ``Mera-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">``
  under the endpoint's secret, which is shown once (creation, rotation) and stored
  encrypted. The receiver recomputes it and refuses old timestamps.
- **Outbound rules.** https only, the guarded transport (public addresses only, connect to
  the address checked), no redirects, no proxy from the environment, a short timeout.
- **Retries.** 1 minute, 5 minutes, 30 minutes, 2 hours, 6 hours; then the delivery fails.
  Twenty failures in a row switch the endpoint off with a reason.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.key_provider import KeyProvider
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import Event, WebhookDelivery, WebhookEndpoint, Workspace
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import system_scan_transaction, workspace_transaction
from mera.infrastructure.http.safe_fetch import FetchError, guarded_transport, validate_public_url

log = structlog.get_logger(__name__)

PURPOSE: Final = "webhook.secret"
BACKOFF: Final = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)
DISABLE_AFTER: Final = 20
MAX_ENDPOINTS: Final = 20
EVENT_TYPES: Final = (  # what the event outbox carries today
    "account.needs_reauth",
    "approval.cancelled",
    "approval.edit_requested",
    "approval.escalated",
    "approval.expired",
    "approval.granted",
    "approval.reminded",
    "approval.requested",
    "metrics.synced",
    "post.dead_lettered",
    "post.failed",
    "post.published",
    "post.scheduled",
)


def sign(secret: str, body: bytes, at: int) -> str:
    mac = hmac.new(secret.encode(), f"{at}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={at},v1={mac}"


class WebhookService(WorkspaceScopedService):
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        key_provider: KeyProvider,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_http: bool = False,
    ) -> None:
        super().__init__(sm)
        self._kp = key_provider
        self._t = transport
        self._allow_http = allow_http

    # ------------------------------------------------------------------ admin

    async def _admin(self, s: AsyncSession, user: AuthenticatedUser, ws: uuid.UUID) -> None:
        principal = await load_user_principal(s, user, ws)
        rbac.require(principal, Permission.MANAGE_WORKSPACE, ws)

    def _check_url(self, url: str) -> str:
        url = url.strip()
        if url.startswith("http://") and self._allow_http:
            return url
        if not url.startswith("https://") or (problem := validate_public_url(url)):
            raise InvalidInput(
                "A webhook needs a public https URL.", code="invalid_url", field="url"
            )
        del problem
        return url

    @staticmethod
    def _check_events(events: list[str]) -> list[str]:
        bad = [e for e in events if e not in EVENT_TYPES and e != "*"]
        if bad or not events:
            raise InvalidInput(
                f"Choose events from: {', '.join(EVENT_TYPES)}, or *.",
                code="invalid_events",
                field="events",
            )
        return sorted(set(events))

    def _seal(self, ws: uuid.UUID, endpoint_id: uuid.UUID, secret: str) -> str:
        return self._kp.encrypt(
            secret.encode(), workspace_id=ws, purpose=PURPOSE, record_id=endpoint_id
        )

    def _open(self, ep: WebhookEndpoint) -> str:
        return self._kp.decrypt(
            ep.secret_ct, workspace_id=ep.workspace_id, purpose=PURPOSE, record_id=ep.id
        ).decode()

    async def create(
        self,
        user: AuthenticatedUser,
        ws: uuid.UUID,
        meta: RequestMeta,
        *,
        url: str,
        events: list[str],
        description: str | None = None,
    ) -> tuple[WebhookEndpoint, str]:
        url, events = self._check_url(url), self._check_events(events)
        secret = "whsec_" + secrets.token_urlsafe(32)
        async with self._tx(user, ws, meta) as s:
            await self._admin(s, user, ws)
            count = len(list((await s.execute(select(WebhookEndpoint.id))).scalars()))
            if count >= MAX_ENDPOINTS:
                raise InvalidInput(
                    "Twenty webhooks is the most a workspace can have.", code="limit"
                )
            latest = await s.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))
            ep_id = uuid7()
            ep = WebhookEndpoint(
                id=ep_id,
                workspace_id=ws,
                url=url,
                description=(description or "")[:200] or None,
                events=events,
                secret_ct=self._seal(ws, ep_id, secret),
                last_event_id=int(latest or 0),  # from now on, not the past
                created_by_user_id=user.user_id,
            )
            s.add(ep)
            await record(s, self._entry(user, meta, "webhook.created", ws, ep_id, {"url": url}))
            await s.flush()
            await s.refresh(ep)
            return ep, secret

    async def list_endpoints(
        self, user: AuthenticatedUser, ws: uuid.UUID, meta: RequestMeta
    ) -> list[WebhookEndpoint]:
        async with self._tx(user, ws, meta) as s:
            await self._admin(s, user, ws)
            return list(
                (
                    await s.execute(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at))
                ).scalars()
            )

    async def update(
        self,
        user: AuthenticatedUser,
        ws: uuid.UUID,
        meta: RequestMeta,
        endpoint_id: uuid.UUID,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        active: bool | None = None,
        rotate_secret: bool = False,
    ) -> tuple[WebhookEndpoint, str | None]:
        secret = None
        async with self._tx(user, ws, meta) as s:
            await self._admin(s, user, ws)
            ep = await s.get(WebhookEndpoint, endpoint_id, with_for_update=True)
            if ep is None:
                raise NotFoundError("Webhook not found.")
            if url is not None:
                ep.url = self._check_url(url)
            if events is not None:
                ep.events = self._check_events(events)
            if active is not None:
                ep.active = active
                if active:
                    ep.consecutive_failures, ep.disabled_reason = 0, None
            if rotate_secret:
                secret = "whsec_" + secrets.token_urlsafe(32)
                ep.secret_ct = self._seal(ws, ep.id, secret)
            ep.updated_at = datetime.now(UTC)
            await record(
                s,
                self._entry(
                    user,
                    meta,
                    "webhook.updated",
                    ws,
                    ep.id,
                    {"rotated": rotate_secret, "active": ep.active},
                ),
            )
            await s.flush()
            await s.refresh(ep)
            return ep, secret

    async def delete(
        self, user: AuthenticatedUser, ws: uuid.UUID, meta: RequestMeta, endpoint_id: uuid.UUID
    ) -> None:
        async with self._tx(user, ws, meta) as s:
            await self._admin(s, user, ws)
            ep = await s.get(WebhookEndpoint, endpoint_id)
            if ep is None:
                raise NotFoundError("Webhook not found.")
            await s.delete(ep)
            await record(s, self._entry(user, meta, "webhook.deleted", ws, endpoint_id, {}))

    async def deliveries(
        self, user: AuthenticatedUser, ws: uuid.UUID, meta: RequestMeta, endpoint_id: uuid.UUID
    ) -> list[WebhookDelivery]:
        async with self._tx(user, ws, meta) as s:
            await self._admin(s, user, ws)
            return list(
                (
                    await s.execute(
                        select(WebhookDelivery)
                        .where(WebhookDelivery.endpoint_id == endpoint_id)
                        .order_by(WebhookDelivery.created_at.desc())
                        .limit(100)
                    )
                ).scalars()
            )

    async def test(
        self, user: AuthenticatedUser, ws: uuid.UUID, meta: RequestMeta, endpoint_id: uuid.UUID
    ) -> WebhookDelivery:
        """A ``ping`` delivery, sent at once."""
        async with self._tx(user, ws, meta) as s:
            await self._admin(s, user, ws)
            ep = await s.get(WebhookEndpoint, endpoint_id)
            if ep is None:
                raise NotFoundError("Webhook not found.")
            d = WebhookDelivery(
                id=uuid7(),
                workspace_id=ws,
                endpoint_id=ep.id,
                event_type="ping",
                payload={"type": "ping", "workspace_id": str(ws), "data": {}},
            )
            s.add(d)
            await s.flush()
            delivery_id = d.id
        await self._send(ws, delivery_id)
        async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
            out = await s.get(WebhookDelivery, delivery_id)
            assert out is not None
            return out

    @staticmethod
    def _entry(
        user: AuthenticatedUser,
        meta: RequestMeta,
        action: str,
        ws: uuid.UUID,
        sid: uuid.UUID,
        after: dict[str, Any],
    ) -> AuditEntry:
        return AuditEntry(
            action=action,
            actor_type="user",
            actor_id=user.user_id,
            actor_label=user.display_name,
            surface=meta.surface,
            workspace_id=ws,
            subject_type="webhook_endpoint",
            subject_id=sid,
            after=after,
            ip_address=meta.ip_address,
            request_id=meta.request_id,
        )

    # ------------------------------------------------------------------ worker

    async def run(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        async with system_scan_transaction(self._sm) as s:
            spaces = list(
                (
                    await s.execute(select(Workspace.id).where(Workspace.deleted_at.is_(None)))
                ).scalars()
            )
        out = {"queued": 0, "sent": 0, "failed": 0}
        for ws in spaces:
            q, sent, failed = await self.run_workspace(ws, now)
            out["queued"] += q
            out["sent"] += sent
            out["failed"] += failed
        return out

    async def run_workspace(self, ws: uuid.UUID, now: datetime) -> tuple[int, int, int]:
        ctx = WorkspaceContext(ws)
        queued = 0
        async with workspace_transaction(self._sm, ctx) as s:
            endpoints = list(
                (
                    await s.execute(
                        select(WebhookEndpoint)
                        .where(WebhookEndpoint.active.is_(True))
                        .with_for_update()
                    )
                ).scalars()
            )
            for ep in endpoints:
                events = list(
                    (
                        await s.execute(
                            select(Event)
                            .where(Event.id > ep.last_event_id)
                            .order_by(Event.id)
                            .limit(500)
                        )
                    ).scalars()
                )
                for e in events:
                    ep.last_event_id = e.id
                    if "*" not in ep.events and e.event_type not in ep.events:
                        continue
                    s.add(
                        WebhookDelivery(
                            id=uuid7(),
                            workspace_id=ws,
                            endpoint_id=ep.id,
                            event_id=e.id,
                            event_type=e.event_type,
                            payload={
                                "id": e.id,
                                "type": e.event_type,
                                "created_at": e.created_at.isoformat(),
                                "workspace_id": str(ws),
                                "subject": {
                                    "type": e.subject_type,
                                    "id": str(e.subject_id) if e.subject_id else None,
                                },
                                "social_account_id": str(e.social_account_id)
                                if e.social_account_id
                                else None,
                                "account_group_id": str(e.account_group_id)
                                if e.account_group_id
                                else None,
                                "data": e.payload,
                            },
                            next_attempt_at=now,
                        )
                    )
                    queued += 1
            due = list(
                (
                    await s.execute(
                        select(WebhookDelivery.id)
                        .where(
                            WebhookDelivery.state == "pending",
                            WebhookDelivery.next_attempt_at <= now,
                        )
                        .order_by(WebhookDelivery.next_attempt_at)
                        .limit(200)
                    )
                ).scalars()
            )
        sent = failed = 0
        for delivery_id in due:
            outcome = await self._send(ws, delivery_id, now)
            sent += outcome == "delivered"
            failed += outcome == "failed"
        return queued, sent, failed

    async def _send(
        self, ws: uuid.UUID, delivery_id: uuid.UUID, now: datetime | None = None
    ) -> str:
        now = now or datetime.now(UTC)
        ctx = WorkspaceContext(ws)
        async with workspace_transaction(self._sm, ctx) as s:
            d = await s.get(WebhookDelivery, delivery_id)
            ep = await s.get(WebhookEndpoint, d.endpoint_id) if d else None
            if d is None or ep is None or d.state != "pending":
                return "skipped"
            body = json.dumps(d.payload, separators=(",", ":"), default=str).encode()
            at = int(time.time())
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mera-Webhooks/1",
                "Mera-Event": d.event_type,
                "Mera-Delivery": str(d.id),
                "Mera-Signature": sign(self._open(ep), body, at),
            }
            url = ep.url
        status: int | None = None
        error: str | None = None
        try:
            transport = self._t or guarded_transport()
            async with httpx.AsyncClient(
                transport=transport, timeout=10, follow_redirects=False, trust_env=False
            ) as c:
                r = await c.post(url, content=body, headers=headers)
            status = r.status_code
            ok = 200 <= status < 300
            if not ok:
                error = f"HTTP {status}"
        except (httpx.HTTPError, FetchError) as exc:
            ok, error = False, type(exc).__name__
        async with workspace_transaction(self._sm, ctx) as s:
            d = await s.get(WebhookDelivery, delivery_id, with_for_update=True)
            ep = await s.get(WebhookEndpoint, d.endpoint_id, with_for_update=True) if d else None
            if d is None or ep is None:
                return "skipped"
            d.attempts += 1
            d.last_status, d.last_error = status, error
            if ok:
                d.state, d.delivered_at = "delivered", now
                ep.consecutive_failures = 0
                return "delivered"
            ep.consecutive_failures += 1
            if ep.consecutive_failures >= DISABLE_AFTER:
                ep.active = False
                ep.disabled_reason = f"{DISABLE_AFTER} deliveries in a row failed; last: {error}"
            if d.attempts > len(BACKOFF) or d.event_type == "ping":
                d.state = "failed"
                return "failed"
            d.next_attempt_at = now + BACKOFF[d.attempts - 1]
            return "retrying"
