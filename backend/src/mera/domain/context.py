"""The tenant context every repository call and job runs under (ADR-0006)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    workspace_id: uuid.UUID
