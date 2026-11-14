"""The two approval state machines (docs/03 §7, ADR-0014). Pure: no I/O, no clock.

Each **variant** (one account's post) moves once, from ``pending`` to a terminal outcome.
The **request** state is derived from its variants, apart from three request-wide
overrides (``cancelled``, ``awaiting_edit``, ``superseded``) that the service applies.

Quorum is per variant. A reject is a veto for the variants it names, whatever the mode:
recovering from an unwanted veto is cheaper than recovering from an unwanted post.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final


class VariantApproval(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RequestState(StrEnum):
    PENDING = "pending"
    AWAITING_EDIT = "awaiting_edit"
    APPROVED = "approved"
    PARTIALLY_APPROVED = "partially_approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class Quorum(StrEnum):
    ANY_OF = "any_of"
    ALL_OF = "all_of"


OPEN_REQUEST_STATES: Final = frozenset({RequestState.PENDING, RequestState.AWAITING_EDIT})
TERMINAL_VARIANT: Final = frozenset(VariantApproval) - {VariantApproval.PENDING}
# ADR-0014 §1: without any approval, a mix resolves to the "least final" outcome.
_NO_APPROVAL_PRECEDENCE: Final = (
    VariantApproval.REJECTED,
    VariantApproval.EXPIRED,
    VariantApproval.SUPERSEDED,
    VariantApproval.CANCELLED,
)


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VariantTally:
    state: VariantApproval
    required: int
    quorum: Quorum
    approvals: int = 0
    rejections: int = 0

    @property
    def needed(self) -> int:
        """Approvals that settle this variant. ``all_of`` policies are snapshotted with
        ``required`` equal to the size of the named approver list, so both modes reduce to
        a count here."""
        return max(1, self.required)


def vote(tally: VariantTally, action: str) -> VariantTally:
    """One approver's approve or reject on one pending variant."""
    if tally.state is not VariantApproval.PENDING:
        raise InvalidTransition(f"variant is {tally.state}, not pending")
    if action == "reject":
        return replace(tally, state=VariantApproval.REJECTED, rejections=tally.rejections + 1)
    if action == "approve":
        approvals = tally.approvals + 1
        done = approvals >= tally.needed
        return replace(
            tally,
            approvals=approvals,
            state=VariantApproval.APPROVED if done else VariantApproval.PENDING,
        )
    raise InvalidTransition(f"unknown action {action!r}")


def end(tally: VariantTally, outcome: VariantApproval) -> VariantTally:
    """Supersede, expire or cancel a pending variant. Decided variants keep their outcome:
    an approval is never undone, a later edit makes a new version instead."""
    if outcome not in (
        VariantApproval.SUPERSEDED,
        VariantApproval.EXPIRED,
        VariantApproval.CANCELLED,
    ):
        raise InvalidTransition(f"{outcome} is reached by a vote, not ended into")
    if tally.state is not VariantApproval.PENDING:
        return tally
    return replace(tally, state=outcome)


def derive(states: Iterable[VariantApproval]) -> RequestState:
    """The request state from its variants' states (ADR-0014 §1)."""
    vs = list(states)
    if not vs:
        return RequestState.CANCELLED  # nothing left to decide
    if any(s is VariantApproval.PENDING for s in vs):
        return RequestState.PENDING
    distinct = set(vs)
    if distinct == {VariantApproval.APPROVED}:
        return RequestState.APPROVED
    if VariantApproval.APPROVED in distinct:
        return RequestState.PARTIALLY_APPROVED
    for outcome in _NO_APPROVAL_PRECEDENCE:
        if outcome in distinct:
            return RequestState(outcome.value)
    raise AssertionError("unreachable")  # pragma: no cover


def is_open(state: str) -> bool:
    return state in {s.value for s in OPEN_REQUEST_STATES}


# ---------------------------------------------------------------- policy merging


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One level's approval policy, as far as one variant is concerned."""

    level: str  # workspace | account_group | social_account | settings
    required: int
    quorum: Quorum
    approvers: frozenset[str]  # empty = anyone with Approve on the account
    remind_minutes: int
    escalate_minutes: int
    expires_minutes: int
    validity_minutes: int
    # Named people to escalate to (P2-M2.8); empty = the workspace's Admins, as before.
    escalate_to: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    required: int
    quorum: Quorum
    approvers: frozenset[str] | None  # None = anyone eligible
    remind_minutes: int
    escalate_minutes: int
    expires_minutes: int
    validity_minutes: int
    sources: tuple[str, ...]
    escalate_to: frozenset[str] = frozenset()


DEFAULT_RULE: Final = PolicyRule(
    level="system",
    required=1,
    quorum=Quorum.ANY_OF,
    approvers=frozenset(),
    remind_minutes=120,
    escalate_minutes=240,
    expires_minutes=720,
    validity_minutes=1440,
)


class NoCommonApprover(ValueError):
    pass


def strictest(rules: Iterable[PolicyRule]) -> EffectivePolicy:
    """Strictest-wins across levels (docs/07 §7.1, ADR-0014): the most approvers, all_of
    over any_of, the narrowest set of named approvers (their intersection), the shortest
    timers and validity window. Adding a permissive level can never weaken a strict one."""
    given = list(rules)
    rs = [DEFAULT_RULE, *given]
    # Timers come from the policies that exist; the system default only fills a gap (so a
    # workspace may choose a 48-hour expiry), while counts still start from the default.
    # Rules from level settings carry only a count (their timers are placeholders).
    timed = [r for r in given if not r.level.startswith("settings")] or [DEFAULT_RULE]
    named = [r.approvers for r in rs if r.approvers]
    approvers: frozenset[str] | None = None
    if named:
        approvers = frozenset.intersection(*named)
        if not approvers:
            raise NoCommonApprover("the policies that apply name no approver in common")
    quorum = Quorum.ALL_OF if any(r.quorum is Quorum.ALL_OF for r in rs) else Quorum.ANY_OF
    required = max(r.required for r in rs)
    if quorum is Quorum.ALL_OF and approvers:
        required = max(required, len(approvers))
    if approvers is not None and required > len(approvers):
        raise NoCommonApprover(
            f"{required} approvals are required but only {len(approvers)} approver(s) qualify"
        )
    return EffectivePolicy(
        required=required,
        quorum=quorum,
        approvers=approvers,
        remind_minutes=min(r.remind_minutes for r in timed),
        escalate_minutes=min(r.escalate_minutes for r in timed),
        expires_minutes=min(r.expires_minutes for r in timed),
        validity_minutes=min(r.validity_minutes for r in timed),
        sources=tuple(r.level for r in rs if r.level != "system"),
        # Everyone any level names hears about an escalation (a union, not strictest).
        escalate_to=frozenset().union(*(r.escalate_to for r in rs)),
    )
