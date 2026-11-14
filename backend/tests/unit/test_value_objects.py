from __future__ import annotations

import itertools

import pytest

from mera.domain.value_objects import Role, uuid7

ORDERED = [Role.VIEWER, Role.APPROVER, Role.EDITOR, Role.ADMIN, Role.OWNER]


def test_role_order_is_viewer_approver_editor_admin_owner() -> None:
    assert sorted(Role) == ORDERED


@pytest.mark.parametrize(("a", "b"), list(itertools.combinations(ORDERED, 2)))
def test_role_comparisons_follow_rank_not_alphabet(a: Role, b: Role) -> None:
    # a ranks strictly below b. Alphabetically "admin" < "approver" < "editor", which is the
    # bug this guards against.
    assert a < b
    assert a <= b
    assert b > a
    assert b >= a
    assert not a.at_least(b)
    assert b.at_least(a)


def test_approver_sits_below_editor() -> None:
    assert Role.APPROVER < Role.EDITOR
    assert not Role.APPROVER.at_least(Role.EDITOR)


def test_role_still_behaves_as_string() -> None:
    assert Role("admin") is Role.ADMIN
    assert {Role.ADMIN: 1}["admin"] == 1


def test_uuid7_version_and_variant() -> None:
    u = uuid7()
    assert u.version == 7
    assert u.variant == "specified in RFC 4122"


def test_uuid7_is_monotonic_within_a_burst() -> None:
    ids = [uuid7() for _ in range(5000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_role_comparison_with_non_role_is_not_implemented() -> None:
    assert Role.ADMIN.__lt__(3) is NotImplemented
    assert Role.ADMIN.__le__(3) is NotImplemented
    assert Role.ADMIN.__gt__(3) is NotImplemented
    assert Role.ADMIN.__ge__(3) is NotImplemented


def test_uuid7_counter_overflow_rolls_into_next_millisecond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mera.domain.value_objects import ids

    monkeypatch.setattr(ids, "_counter", ids._COUNTER_MAX)
    monkeypatch.setattr(ids, "_last_ms", 10**15)  # far future: forces the same-ms branch
    a = ids.uuid7()
    b = ids.uuid7()
    assert a < b


def test_domain_error_code_override() -> None:
    from mera.domain.errors import DomainError

    assert DomainError("x").code == "domain_error"
    assert DomainError("x", code="custom").code == "custom"
