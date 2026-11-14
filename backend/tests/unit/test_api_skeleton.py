from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from mera.api.errors import Conflict
from mera.api.main import create_app
from mera.config import get_settings


class Body(BaseModel):
    name: str


@pytest.fixture
def app() -> FastAPI:
    app = create_app(get_settings())

    @app.get("/_test/conflict")
    async def conflict() -> None:
        raise Conflict("Already approved.", code="approval_already_decided")

    @app.post("/_test/validate")
    async def validate(body: Body) -> Body:
        return body

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_request_id_is_minted_and_echoed(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert len(r.headers["x-request-id"]) >= 8


async def test_safe_incoming_request_id_is_kept(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-12345"})
    assert r.headers["x-request-id"] == "trace-abc-12345"


async def test_unsafe_incoming_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})
    assert r.headers["x-request-id"] != "bad id\nwith newline"


async def test_domain_error_uses_envelope(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/conflict")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "approval_already_decided"
    assert err["message"] == "Already approved."
    assert err["request_id"] == r.headers["x-request-id"]
    assert err["docs_url"].endswith("/approval_already_decided")


async def test_unknown_route_uses_envelope(client: httpx.AsyncClient) -> None:
    r = await client.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_validation_error_lists_fields(client: httpx.AsyncClient) -> None:
    r = await client.post("/_test/validate", json={})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["field_errors"][0]["field"] == "name"


async def test_unhandled_error_does_not_leak_internals(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/boom")
    assert r.status_code == 500
    assert "secret internals" not in r.text
    err = r.json()["error"]
    assert err["code"] == "internal_error"
    # The 500 is the case where a client most needs an id to quote.
    assert err["request_id"]
    assert r.headers["x-request-id"] == err["request_id"]


async def test_openapi_is_served_under_api_v1(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "Mera API"
