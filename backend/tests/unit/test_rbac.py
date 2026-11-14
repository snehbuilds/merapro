"""RBAC and member-scope policy (M2 acceptance: each role against the 04 §2.6 matrix)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mera.domain.errors import PermissionDenied
from mera.domain.policies.rbac import (
    ACCOUNT_SCOPED,
    KNOWN_SCOPES,
    MIN_ROLE,
    Permission,
    allowed,
    require,
)
from mera.domain.principal import MemberScopeSet, Principal, PrincipalKind
from mera.domain.value_objects import Role


@contextmanager
def denied(reason: str) -> Iterator[None]:
    """Assert PermissionDenied with an internal reason matching ``reason``. The client
    message is deliberately generic, so tests check the reason instead."""
    with pytest.raises(PermissionDenied) as exc:
        yield
    assert re.search(reason, exc.value.reason), exc.value.reason
    assert exc.value.message == "You do not have permission to do that."


WS = uuid.UUID(int=1)
OTHER_WS = uuid.UUID(int=2)
GROUP_A = uuid.UUID(int=10)
GROUP_B = uuid.UUID(int=11)
ACCOUNT_A = uuid.UUID(int=20)
ACCOUNT_B = uuid.UUID(int=21)

V, AP, E, AD, OW = Role.VIEWER, Role.APPROVER, Role.EDITOR, Role.ADMIN, Role.OWNER

# docs/04 §2.6 transcribed independently of MIN_ROLE, so a drift in either fails here.
MATRIX: dict[Permission, set[Role]] = {
    Permission.READ: {V, AP, E, AD, OW},
    Permission.EDIT_POSTS: {E, AD, OW},
    Permission.UPLOAD_MEDIA: {E, AD, OW},
    Permission.APPROVE: {AP, E, AD, OW},
    Permission.PUBLISH: {E, AD, OW},
    Permission.REPLY: {E, AD, OW},
    Permission.CONFIGURE_CONTENT: {E, AD, OW},
    Permission.MANAGE_GROUPS: {E, AD, OW},
    Permission.CONNECT_ACCOUNTS: {AD, OW},
    Permission.MANAGE_MCP: {AD, OW},
    Permission.MANAGE_MEMBERS: {AD, OW},
    Permission.MANAGE_WORKSPACE: {AD, OW},
    Permission.ENGAGE_KILL_SWITCH: {AP, E, AD, OW},
    Permission.CHANGE_AUTOPILOT: {AD, OW},
    Permission.READ_AUDIT_LOG: {AD, OW},
    Permission.ARCHIVE_WORKSPACE: {AD, OW},
    Permission.DELETE_WORKSPACE: {OW},
}


def user(
    role: Role | None,
    *,
    ws: uuid.UUID | None = WS,
    scopes: frozenset[str] | None = None,
    member_scope: MemberScopeSet | None = None,
) -> Principal:
    return Principal(
        kind=PrincipalKind.USER if scopes is None else PrincipalKind.API_KEY,
        user_id=uuid.UUID(int=99),
        workspace_id=ws,
        role=role,
        token_scopes=scopes,
        member_scope=member_scope or MemberScopeSet(),
    )


def test_matrix_covers_every_permission() -> None:
    assert set(MATRIX) == set(Permission) == set(MIN_ROLE)


@pytest.mark.parametrize("permission", list(Permission))
@pytest.mark.parametrize("role", list(Role))
def test_each_role_against_the_matrix(role: Role, permission: Permission) -> None:
    assert allowed(user(role), permission, WS) is (role in MATRIX[permission])


def test_approver_can_approve_and_kill_but_not_draft() -> None:
    p = user(Role.APPROVER)
    assert allowed(p, Permission.APPROVE, WS)
    assert allowed(p, Permission.ENGAGE_KILL_SWITCH, WS)
    assert not allowed(p, Permission.EDIT_POSTS, WS)


def test_other_workspace_is_denied_even_for_owner() -> None:
    with denied("not bound"):
        require(user(Role.OWNER), Permission.READ, OTHER_WS)


def test_unbound_principal_is_denied() -> None:
    with denied("not bound"):
        require(user(Role.OWNER, ws=None), Permission.READ, WS)


def test_principal_without_role_is_denied() -> None:
    with denied("no role"):
        require(user(None), Permission.READ, WS)


# ---- token scopes: intersection with role ----


def test_key_scope_cannot_exceed_creator_role() -> None:
    # docs/09 §5.2: a key created with approvals:decide by a Viewer grants nothing.
    viewer_key = user(Role.VIEWER, scopes=frozenset({"approvals:decide"}))
    assert not allowed(viewer_key, Permission.APPROVE, WS)


def test_key_without_matching_scope_is_denied() -> None:
    admin_key = user(Role.ADMIN, scopes=frozenset({"analytics:read"}))
    assert allowed(admin_key, Permission.READ, WS)
    with denied("token scopes"):
        require(admin_key, Permission.PUBLISH, WS)


def test_unknown_scope_grants_nothing() -> None:
    assert not allowed(user(Role.OWNER, scopes=frozenset({"everything"})), Permission.READ, WS)


# ---- member scope ----

SCOPED_TO_A = MemberScopeSet(restricted=True, group_ids=frozenset({GROUP_A}))


def test_scoped_editor_acts_inside_their_group() -> None:
    p = user(Role.EDITOR, member_scope=SCOPED_TO_A)
    require(p, Permission.EDIT_POSTS, WS, account_group_id=GROUP_A, social_account_id=ACCOUNT_B)


def test_scoped_editor_is_denied_outside_their_group() -> None:
    p = user(Role.EDITOR, member_scope=SCOPED_TO_A)
    with denied("outside"):
        require(p, Permission.EDIT_POSTS, WS, account_group_id=GROUP_B, social_account_id=ACCOUNT_B)


def test_scope_by_account() -> None:
    p = user(Role.APPROVER, member_scope=MemberScopeSet(True, account_ids=frozenset({ACCOUNT_A})))
    assert allowed(p, Permission.APPROVE, WS, social_account_id=ACCOUNT_A)
    assert not allowed(p, Permission.APPROVE, WS, social_account_id=ACCOUNT_B)


def test_scoped_read_without_subject_is_allowed_for_filtered_lists() -> None:
    assert allowed(user(Role.VIEWER, member_scope=SCOPED_TO_A), Permission.READ, WS)


def test_scoped_write_without_subject_is_denied() -> None:
    p = user(Role.APPROVER, member_scope=SCOPED_TO_A)
    with denied("without a subject"):
        require(p, Permission.ENGAGE_KILL_SWITCH, WS)


@pytest.mark.parametrize("permission", sorted(set(Permission) - ACCOUNT_SCOPED))
def test_scoped_admin_cannot_do_workspace_level_actions(permission: Permission) -> None:
    p = user(Role.OWNER, member_scope=SCOPED_TO_A)
    with denied("workspace-level"):
        require(p, permission, WS, account_group_id=GROUP_A)


# ---- properties: every factor only narrows ----

uuids = st.sampled_from([GROUP_A, GROUP_B, ACCOUNT_A, ACCOUNT_B, None])
scope_sets = st.builds(
    MemberScopeSet,
    restricted=st.booleans(),
    group_ids=st.frozensets(st.sampled_from([GROUP_A, GROUP_B])),
    account_ids=st.frozensets(st.sampled_from([ACCOUNT_A, ACCOUNT_B])),
)


# Generation is fast; the health check trips when a heavy import (LiteLLM) ran just before.
@settings(suppress_health_check=[HealthCheck.too_slow])
@given(
    role=st.sampled_from(list(Role)),
    permission=st.sampled_from(list(Permission)),
    scopes=st.none() | st.frozensets(st.sampled_from(sorted(KNOWN_SCOPES))),
    member_scope=scope_sets,
    account=uuids,
    group=uuids,
)
def test_narrowing_never_widens(
    role: Role,
    permission: Permission,
    scopes: frozenset[str] | None,
    member_scope: MemberScopeSet,
    account: uuid.UUID | None,
    group: uuid.UUID | None,
) -> None:
    narrowed = user(role, scopes=scopes, member_scope=member_scope)
    if allowed(narrowed, permission, WS, social_account_id=account, account_group_id=group):
        # Removing the token scopes and the member scope can only keep it allowed...
        assert allowed(
            user(role), permission, WS, social_account_id=account, account_group_id=group
        )
        # ...and the role alone must already grant it.
        assert role in MATRIX[permission]


def test_unrestricted_scope_covers_everything() -> None:
    assert MemberScopeSet().covers(social_account_id=ACCOUNT_B, account_group_id=None)
    assert MemberScopeSet().covers(social_account_id=None, account_group_id=None)
