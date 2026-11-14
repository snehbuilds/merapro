"""Domain-level failures. Transport layers map these to their own wire formats."""

from __future__ import annotations


class DomainError(Exception):
    code: str = "domain_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class PermissionDenied(DomainError):
    """The principal may not perform this action. ``reason`` is for logs and audit only;
    callers must not reveal it to the client beyond a generic message."""

    code = "forbidden"

    def __init__(self, reason: str, *, permission: str | None = None) -> None:
        super().__init__("You do not have permission to do that.")
        self.reason = reason
        self.permission = permission
