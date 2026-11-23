from __future__ import annotations

import pytest

from mera.api.middleware import request_log
from mera.api.middleware.request_log import RequestLogMiddleware


class _CapturedLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **values: object) -> None:
        self.calls.append((event, values))


@pytest.mark.asyncio
async def test_request_log_records_safe_completion_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _CapturedLogger()
    monkeypatch.setattr(request_log, "log", captured)

    async def app(scope: object, receive: object, send: object) -> None:
        await send({"type": "http.response.start", "status": 201, "headers": []})  # type: ignore[misc]
        await send({"type": "http.response.body", "body": b"ok"})  # type: ignore[misc]

    middleware = RequestLogMiddleware(app, service="mera-api")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/oauth/callback",
        "query_string": b"code=secret-value",
        "state": {"request_id": "request-123"},
    }
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await middleware(scope, receive, send)  # type: ignore[arg-type]

    assert len(messages) == 2
    assert len(captured.calls) == 1
    event, values = captured.calls[0]
    assert event == "http.request.completed"
    assert values | {"duration_ms": 0} == {
        "message": "HTTP request completed.",
        "service": "mera-api",
        "request_id": "request-123",
        "method": "POST",
        "path": "/api/v1/oauth/callback",
        "status_code": 201,
        "response_bytes": 2,
        "duration_ms": 0,
    }
    assert isinstance(values["duration_ms"], float)
    assert values["duration_ms"] >= 0
    assert "secret-value" not in str(captured.calls)
