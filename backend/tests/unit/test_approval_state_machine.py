"""The approval state machines and strictest-wins policy merging (M6.2, M6.9, M6.14)."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mera.domain.state_machines.approval import (
    InvalidTransition,
    NoCommonApprover,
    PolicyRule,
    Quorum,
    RequestState,
    VariantApproval,
    VariantTally,
    derive,
    end,
    strictest,
    vote,
)

V = VariantApproval
R = RequestState


def tally(required: int = 1, quorum: Quorum = Quorum.ANY_OF) -> VariantTally:
    return VariantTally(V.PENDING, required, quorum)


def test_one_approval_settles_any_of_one() -> None:
    assert vote(tally(), "approve").state is V.APPROVED


def test_two_required_needs_two_votes_and_a_reject_is_a_veto() -> None:
    t = vote(tally(2, Quorum.ALL_OF), "approve")
    assert (t.state, t.approvals) == (V.PENDING, 1)
    assert vote(t, "approve").state is V.APPROVED
    assert vote(t, "reject").state is V.REJECTED


def test_terminal_variants_take_no_more_votes_and_are_never_undone() -> None:
    done = vote(tally(), "approve")
    with pytest.raises(InvalidTransition):
        vote(done, "reject")
    assert end(done, V.SUPERSEDED).state is V.APPROVED  # approval stands; a new version moves on
    with pytest.raises(InvalidTransition):
        end(tally(), V.APPROVED)


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([V.PENDING, V.APPROVED], R.PENDING),  # still accepting decisions for the pending one
        ([V.APPROVED, V.APPROVED], R.APPROVED),
        ([V.APPROVED, V.REJECTED], R.PARTIALLY_APPROVED),
        ([V.APPROVED, V.EXPIRED], R.PARTIALLY_APPROVED),  # expiry is per variant
        ([V.REJECTED, V.REJECTED], R.REJECTED),
        ([V.REJECTED, V.EXPIRED], R.REJECTED),
        ([V.EXPIRED, V.SUPERSEDED], R.EXPIRED),
        ([V.SUPERSEDED, V.SUPERSEDED], R.SUPERSEDED),
        ([V.SUPERSEDED, V.CANCELLED], R.SUPERSEDED),
        ([V.CANCELLED], R.CANCELLED),
        ([], R.CANCELLED),
    ],
)
def test_request_state_is_derived(states: list[VariantApproval], expected: RequestState) -> None:
    assert derive(states) is expected


events = st.lists(
    st.tuples(
        st.integers(0, 5),
        st.sampled_from(["approve", "reject", "supersede", "expire", "cancel"]),
    ),
    max_size=40,
)


# docs/11 M6 acceptance: the property holds over at least 10,000 generated sequences.
@settings(max_examples=10_000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(st.tuples(st.integers(1, 3), st.sampled_from(list(Quorum))), min_size=1, max_size=6),
    events,
)
def test_random_event_sequences_keep_the_invariants(
    shapes: list[tuple[int, Quorum]], seq: list[tuple[int, str]]
) -> None:
    variants = [VariantTally(V.PENDING, req, q) for req, q in shapes]
    for idx, ev in seq:
        i = idx % len(variants)
        before = variants[i]
        try:
            if ev in ("approve", "reject"):
                after = vote(before, ev)
            else:
                after = end(
                    before,
                    {"supersede": V.SUPERSEDED, "expire": V.EXPIRED, "cancel": V.CANCELLED}[ev],
                )
        except InvalidTransition:
            assert before.state is not V.PENDING  # only terminal variants refuse
            continue
        # A terminal state never changes again; approval needs the full count.
        if before.state is not V.PENDING:
            assert after == before
        if after.state is V.APPROVED and before.state is V.PENDING:
            assert after.approvals >= after.needed
        variants[i] = after
    state = derive(v.state for v in variants)
    states = {v.state for v in variants}
    # Never "approved" unless every variant was; never terminal while one is pending.
    assert (state is R.APPROVED) == (states == {V.APPROVED})
    assert (state is R.PENDING) == (V.PENDING in states)
    if state is R.PARTIALLY_APPROVED:
        assert V.APPROVED in states
        assert states != {V.APPROVED}
    # Nothing expired or rejected ever becomes approved.
    assert not (state is R.APPROVED and (V.EXPIRED in states or V.REJECTED in states))


def rule(level: str, **kw: object) -> PolicyRule:
    base: dict[str, object] = {
        "level": level,
        "required": 1,
        "quorum": Quorum.ANY_OF,
        "approvers": frozenset(),
        "remind_minutes": 120,
        "escalate_minutes": 240,
        "expires_minutes": 720,
        "validity_minutes": 1440,
    }
    base.update(kw)
    return PolicyRule(**base)  # type: ignore[arg-type]


def test_strictest_wins_across_levels() -> None:
    # docs/07 §7.1: a permissive group cannot weaken its company page's stricter policy.
    eff = strictest(
        [
            rule("account_group", required=1, expires_minutes=2880),
            rule(
                "social_account",
                required=2,
                quorum=Quorum.ALL_OF,
                approvers=frozenset({"a", "b", "c"}),
                expires_minutes=600,
            ),
        ]
    )
    assert (eff.required, eff.quorum, eff.approvers) == (3, Quorum.ALL_OF, frozenset("abc"))
    assert eff.expires_minutes == 600
    # A workspace may choose a longer expiry than the system default.
    assert strictest([rule("workspace", expires_minutes=2880)]).expires_minutes == 2880
    assert strictest([]).required == 1


def test_named_approvers_intersect_and_must_be_satisfiable() -> None:
    eff = strictest(
        [
            rule("workspace", approvers=frozenset("ab")),
            rule("account_group", approvers=frozenset("bc")),
        ]
    )
    assert eff.approvers == frozenset("b")
    with pytest.raises(NoCommonApprover):
        strictest(
            [
                rule("workspace", approvers=frozenset("a")),
                rule("social_account", approvers=frozenset("b")),
            ]
        )
    with pytest.raises(NoCommonApprover):
        strictest([rule("workspace", required=3, approvers=frozenset("ab"))])


@given(st.lists(st.integers(1, 5), min_size=1, max_size=4))
def test_adding_a_level_never_lowers_the_required_count(counts: list[int]) -> None:
    rules = [rule(f"l{i}", required=c) for i, c in enumerate(counts)]
    assert strictest(rules).required == max(counts)
    assert strictest([*rules, rule("extra", required=1)]).required == max(counts)


def test_vote_refuses_an_unknown_action() -> None:
    from mera.domain.state_machines.approval import (
        InvalidTransition,
        Quorum,
        VariantApproval,
        VariantTally,
        vote,
    )

    with pytest.raises(InvalidTransition, match="unknown action"):
        vote(VariantTally(VariantApproval.PENDING, 1, Quorum.ANY_OF), "maybe")
