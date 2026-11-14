"""P2-M0 go-live fixes that need the database: the browser's address through the web app
(M0.9) and monthly audit partitions created with their tenancy rules (M0.9)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import make_api
from tests.integration.test_identity_m2 import PASSWORD, _uniq

pytestmark = pytest.mark.integration

SECRET = "web-proxy-secret-for-tests-0123456789"


async def _login(api, email: str, headers: dict[str, str]) -> int:  # type: ignore[no-untyped-def]
    r = await api.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=headers
    )
    return r.status_code


async def test_audit_and_lockout_use_the_browser_address(
    postgres_urls: tuple[str, str], owner_engine: AsyncEngine
) -> None:
    async with make_api(postgres_urls, client_ip="10.9.9.9", web_proxy_secret=SECRET) as api:
        name = _uniq("fwd")
        email = f"{name}@example.com"
        r = await api.post(
            "/api/v1/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": name},
        )
        assert r.status_code == 201
        web = {"x-mera-proxy-secret": SECRET, "x-mera-client-ip": "203.0.113.7"}
        assert await _login(api, email, web) == 200
        # Without the secret the header is ignored: the socket peer is recorded.
        assert await _login(api, email, {"x-mera-client-ip": "198.51.100.1"}) == 200

        async with owner_engine.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT host(ip_address), surface FROM audit_log "
                        "WHERE action = 'auth.login_succeeded' AND actor_label = :n "
                        "ORDER BY id"
                    ),
                    {"n": name},
                )
            ).all()
        assert [tuple(r) for r in rows] == [("203.0.113.7", "web"), ("10.9.9.9", "api")]

        # Login lockout is per browser address: one address failing does not lock out
        # everybody else behind the same web server.
        bad = {"email": email, "password": "wrong password, quite long"}
        attacker = {"x-mera-proxy-secret": SECRET, "x-mera-client-ip": "203.0.113.66"}
        codes = [
            (await api.post("/api/v1/auth/login", json=bad, headers=attacker)).status_code
            for _ in range(12)
        ]
        assert 429 in codes
        assert await _login(api, email, web) == 200


_PARTS = (
    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
    "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies, "
    "has_table_privilege('mera_app', c.oid, 'SELECT') AS app_select, "
    "has_table_privilege('mera_app', c.oid, 'INSERT') AS app_insert "
    "FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
    "WHERE i.inhparent = 'audit_log'::regclass AND c.relname <> 'audit_log_default' "
    "ORDER BY c.relname"
)


async def test_monthly_audit_partitions_carry_their_tenancy_rules(
    owner_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    async with owner_engine.connect() as c:
        months = (
            (
                await c.execute(
                    text(
                        "SELECT to_char(date_trunc('month', now() AT TIME ZONE 'UTC') "
                        '+ make_interval(months => g), \'"audit_log_y"YYYY"m"MM\') '
                        "FROM generate_series(1, 2) g"
                    )
                )
            )
            .scalars()
            .all()
        )
        parts = {r.relname: r for r in (await c.execute(text(_PARTS))).all()}
    # The two coming months always exist (the current one too, unless the default
    # partition already held its rows when the migration ran).
    assert set(months) <= set(parts)
    for row in parts.values():
        assert row.relrowsecurity is True
        assert row.relforcerowsecurity is True
        assert row.policies == 2
        assert row.app_select is False
        assert row.app_insert is False

    # The app role may run the function (the daily task does), and it is idempotent.
    async with app_engine.begin() as c:
        assert (await c.execute(text("SELECT mera_ensure_audit_partitions(2)"))).scalar() == 0
    async with app_engine.begin() as c:
        with pytest.raises(Exception, match="months_ahead must be between 0 and 12"):
            await c.execute(text("SELECT mera_ensure_audit_partitions(13)"))
    # It cannot read a partition directly, only through the parent's policies.
    async with app_engine.begin() as c:
        with pytest.raises(Exception, match="permission denied"):
            await c.execute(text(f"SELECT count(*) FROM {months[0]}"))  # noqa: S608 (name from pg)


async def test_future_rows_land_in_their_month_and_stay_tenant_scoped(
    owner_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    import uuid

    ws = uuid.uuid4()
    async with app_engine.begin() as c:
        await c.execute(text("SELECT set_config('mera.workspace_id', :w, true)"), {"w": str(ws)})
        await c.execute(
            text(
                "INSERT INTO audit_log (created_at, workspace_id, actor_type, surface, action) "
                "VALUES (date_trunc('month', now()) + interval '1 month 1 day', :w, "
                "'system', 'worker', 'test.partition')"
            ),
            {"w": str(ws)},
        )
        part = (
            await c.execute(
                text("SELECT tableoid::regclass::text FROM audit_log WHERE workspace_id = :w"),
                {"w": str(ws)},
            )
        ).scalar_one()
        assert part.startswith("audit_log_y")
    # Another workspace sees nothing through the parent.
    async with app_engine.begin() as c:
        await c.execute(
            text("SELECT set_config('mera.workspace_id', :w, true)"), {"w": str(uuid.uuid4())}
        )
        n = (
            await c.execute(text("SELECT count(*) FROM audit_log WHERE action = 'test.partition'"))
        ).scalar_one()
        assert n == 0


async def test_preflight_database_probe(postgres_urls: tuple[str, str]) -> None:
    from mera import preflight
    from mera.config import Settings, get_settings

    base = get_settings().model_dump()
    app = Settings(**{**base, "database_url": postgres_urls[1]})
    checks = {c.name: c for c in await preflight._database(app)}
    assert checks["role"].status == "ok"
    assert checks["migrations"].status == "ok"
    # The head of the migration scripts, whatever it is now: the probe checks it is applied.
    assert checks["migrations"].detail == f"At revision {preflight._alembic_head()}."

    # Pointing DATABASE_URL at the owner (an RLS-bypassing superuser) is the classic slip.
    owner = Settings(**{**base, "database_url": postgres_urls[0]})
    [check] = await preflight._database(owner)
    assert check.status == "fail"
    assert "mera_app" in check.detail
