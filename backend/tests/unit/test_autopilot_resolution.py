"""P3-M3: mode resolution is strictest-wins, and company accounts must opt in themselves."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from mera.application.autopilot import STRICTNESS, resolve

MODE = st.sampled_from([None, "manual", "assisted", "full"])


@given(MODE, st.lists(MODE, max_size=4), MODE, st.booleans())
def test_never_looser_than_any_level_that_says_something(
    ws: str | None, groups: list[str | None], own: str | None, company: bool
) -> None:
    out = resolve(ws, groups, own, company=company)
    for level in [ws, *groups, own]:
        if level is not None:
            assert STRICTNESS[out] <= STRICTNESS[level]
    if company and own in (None, "manual"):
        assert out == "manual"
    if all(level is None for level in [ws, *groups, own]):
        assert out == "manual"


def test_a_group_on_full_never_carries_a_company_page() -> None:
    assert resolve("full", ["full"], None, company=True) == "manual"
    assert resolve("full", ["full"], "full", company=True) == "full"
    assert resolve("full", ["assisted"], "full", company=False) == "assisted"
