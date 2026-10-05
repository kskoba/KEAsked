"""
Cross-month-boundary continuity tests for CpsatScheduleGenerator's
trailing_assignments parameter (HC-8/SIAR, HC-9 gap-1/2/3 spacing,
HC-13/13b/13c NIAR + adjacency). Each test restricts a synthetic
physician's availability to exactly one day (January 1st) so the solver's
choice to fill or leave it empty directly reflects whether the
cross-boundary constraint fired -- no month in isolation, these only make
sense with a `trailing_assignments` dict standing in for "what the master
Google Sheet reader would have returned for December."

Real CP-SAT solves -- slower than the pure-validator tests in this
directory, but still well under a second each at this tiny scale.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, Shift

YEAR, MONTH = 2027, 1  # January -- immediately after a real month boundary
_DEC_YEAR = YEAR - 1


def _dec(day: int) -> datetime.date:
    return datetime.date(_DEC_YEAR, 12, day)


def _jan1_only_submission(physician_id: str, **kwargs) -> PhysicianSubmission:
    """A physician available only on January 1st -- isolates the boundary check."""
    days_in_month = calendar.monthrange(YEAR, MONTH)[1]
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d),
            wants_to_work=(d == 1),
            available_blocks=frozenset(range(5)),
            requested_shifts=frozenset(ALL_SHIFT_CODES),
        )
        for d in range(1, days_in_month + 1)
    ]
    return PhysicianSubmission(
        physician_id=physician_id, physician_name=physician_id,
        year=YEAR, month=MONTH, shifts_requested=1, shifts_min=0, shifts_max=1,
        days=days, **kwargs,
    )


def _jan1_assigned(result) -> list:
    return [a for a in result.assignments if a.date == datetime.date(YEAR, MONTH, 1)]


# --------------------------------------------------------------------------- #
# HC-8 (SIAR / max_consecutive_shifts)
# --------------------------------------------------------------------------- #

def test_siar_blocks_day_one_when_trailing_run_is_at_cap():
    sub = _jan1_only_submission("Test")
    cfg = PhysicianConfig(id="Test", name="Test", max_consecutive_shifts=4)
    roster = {"Test": cfg}
    trailing = {"Test": [(_dec(d), Shift("0600h", "RAH A side")) for d in (28, 29, 30, 31)]}

    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=trailing)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)

    assert _jan1_assigned(result) == []


def test_siar_allows_day_one_when_trailing_run_has_headroom():
    sub = _jan1_only_submission("Test")
    cfg = PhysicianConfig(id="Test", name="Test", max_consecutive_shifts=4)
    roster = {"Test": cfg}
    trailing = {"Test": [(_dec(d), Shift("0600h", "RAH A side")) for d in (29, 30, 31)]}

    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=trailing)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)

    assert len(_jan1_assigned(result)) == 1


def test_siar_unaffected_with_no_trailing_data():
    sub = _jan1_only_submission("Test")
    cfg = PhysicianConfig(id="Test", name="Test", max_consecutive_shifts=4)
    roster = {"Test": cfg}

    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)

    assert len(_jan1_assigned(result)) == 1


# --------------------------------------------------------------------------- #
# HC-13 / HC-13b / HC-13c (NIAR + isolated-night / night-gap-night adjacency)
# --------------------------------------------------------------------------- #

def test_niar_allows_day_one_night_completing_a_trailing_run():
    sub = _jan1_only_submission(
        "Test", shifts_2400h_requested=1, shifts_2400h_stated=True,
    )
    cfg = PhysicianConfig(
        id="Test", name="Test", max_consecutive_shifts=10, max_consecutive_nights=2, only_2400h=True,
    )
    roster = {"Test": cfg}
    trailing = {"Test": [(_dec(31), Shift("2400h", "RAH A side"))]}

    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=trailing)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)

    assert len(_jan1_assigned(result)) == 1


def test_niar_blocks_day_one_night_when_trailing_run_is_at_cap():
    sub = _jan1_only_submission(
        "Test", shifts_2400h_requested=1, shifts_2400h_stated=True,
    )
    cfg = PhysicianConfig(
        id="Test", name="Test", max_consecutive_shifts=10, max_consecutive_nights=2, only_2400h=True,
    )
    roster = {"Test": cfg}
    trailing = {"Test": [(_dec(30), Shift("2400h", "RAH A side")), (_dec(31), Shift("2400h", "RAH A side"))]}

    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=trailing)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)

    assert _jan1_assigned(result) == []


def test_niar_still_forbids_an_isolated_night_with_no_trailing_data():
    """Unchanged pre-existing behavior (HC-13b): a lone night with no
    adjacent night anywhere is still forbidden for a non-singleton physician,
    confirming trailing_assignments=None doesn't weaken this rule."""
    sub = _jan1_only_submission(
        "Test", shifts_2400h_requested=1, shifts_2400h_stated=True,
    )
    cfg = PhysicianConfig(
        id="Test", name="Test", max_consecutive_shifts=10, max_consecutive_nights=2, only_2400h=True,
    )
    roster = {"Test": cfg}

    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)

    assert _jan1_assigned(result) == []


# --------------------------------------------------------------------------- #
# HC-9 (23h/36h spacing, gap==2/3 late-shift lookahead)
# --------------------------------------------------------------------------- #

def test_late_trailing_shift_blocks_gap_two_day():
    sub = _jan1_only_submission("Test")
    cfg = PhysicianConfig(id="Test", name="Test", max_consecutive_shifts=10)
    roster = {"Test": cfg}
    # Late shift on Dec 30, Dec 31 off (no trailing entry) -- Jan 1 is gap=2
    # from the late shift and must still be blocked, the same way an
    # in-month late shift blocks the day after next.
    trailing = {"Test": [(_dec(30), Shift("2400h", "RAH A side"))]}

    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=trailing)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)

    assert _jan1_assigned(result) == []


def test_late_trailing_shift_unaffected_with_no_trailing_data():
    sub = _jan1_only_submission("Test")
    cfg = PhysicianConfig(id="Test", name="Test", max_consecutive_shifts=10)
    roster = {"Test": cfg}

    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)

    assert len(_jan1_assigned(result)) == 1
