"""Application-level failures, mapped to the error envelope by the API layer."""

from __future__ import annotations


class ApplicationError(Exception):
    code: str = "error"

    def __init__(self, message: str, *, code: str | None = None, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        if code is not None:
            self.code = code


class NotFoundError(ApplicationError):
    """Also raised for another workspace's resources: deliberately indistinguishable."""

    code = "not_found"


class WorkspaceNotFound(NotFoundError):
    """Not a member, a key for another workspace, or no such workspace: indistinguishable to
    the caller, audited as a probe (docs/09 §10)."""


class ConflictError(ApplicationError):
    code = "conflict"


class AuthenticationFailed(ApplicationError):
    code = "invalid_credentials"


class TooManyAttempts(ApplicationError):
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServiceUnavailableError(ApplicationError):
    code = "service_unavailable"


class InvalidInput(ApplicationError):
    code = "invalid_input"


class Forbidden(ApplicationError):
    """Authenticated, but this credential type cannot do this (e.g. an API key on a
    user-only endpoint). Role and scope denials use the domain's PermissionDenied."""

    code = "forbidden"


class Gone(ApplicationError):
    """The thing existed and can no longer be acted on: an expired approval, a used or
    revoked approval token (ADR-0014 §3)."""

    code = "gone"
