"""Workspace roles (docs/03 §3).

Ordered ``viewer < approver < editor < admin < owner``. Checks are "at least X", never set
membership. Approver deliberately sits below Editor: a reviewer who approves but never
drafts.
"""

from __future__ import annotations

from enum import StrEnum

_ORDER = ("viewer", "approver", "editor", "admin", "owner")


class Role(StrEnum):
    VIEWER = "viewer"
    APPROVER = "approver"
    EDITOR = "editor"
    ADMIN = "admin"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return _ORDER.index(self.value)

    # str already defines the rich comparisons alphabetically, so all four are overridden
    # explicitly; functools.total_ordering would silently keep str's versions.
    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return NotImplemented
        return self.rank >= other.rank

    def __hash__(self) -> int:
        return str.__hash__(self)

    def at_least(self, minimum: Role) -> bool:
        return self >= minimum
