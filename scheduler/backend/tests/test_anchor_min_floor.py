"""
Tests for the 0/0-stated minimum-anchor-shift floor in generator_cpsat.py:
a physician who explicitly states they want ZERO 0600h AND ZERO 2400h
shifts still gets a soft pull toward at least one anchor shift (scaled by
their total requested count), so anchor coverage doesn't end up entirely
concentrated on whoever happened to ask for it. Solo/uncontested synthetic
models, same technique used for the Dickey/RScheirer singleton-night
diagnosis -- full availability, nothing else competing for the slot, so
the solver's choice directly reflects whether this specific soft term
fired.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES

YEAR, MONTH = 2027, 1
DAYS_IN_MONTH = calendar.monthrange(YEAR, MONTH)[1]


def _full_availability_submission(physician_id: str, shifts_requested: int, **kwargs) -> PhysicianSubmission:
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d),
            wants_to_work=True,
            available_blocks=frozenset(range(5)),
            requested_shifts=frozenset(ALL_SHIFT_CODES),
        )
        for d in range(1, DAYS_IN_MONTH + 1)
    ]
    return PhysicianSubmission(
        physician_id=physician_id, physician_name=physician_id,
        year=YEAR, month=MONTH,
        shifts_requested=shifts_requested, shifts_min=0, shifts_max=shifts_requested + 2,
        days=days, **kwargs,
    )


def _anchor_total(result, pid: str) -> int:
    return sum(
        1 for a in result.assignments
        if a.physician_id == pid and a.shift.time in ("0600h", "2400h")
    )


def test_zero_zero_stated_still_gets_at_least_one_anchor_shift():
    sub = _full_availability_submission(
        "Test", shifts_requested=6,
        shifts_2400h_stated=True, shifts_2400h_requested=0,
        shifts_0600h_stated=True, shifts_0600h_requested=0,
    )
    roster = {"Test": PhysicianConfig(id="Test", name="Test")}
    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    assert _anchor_total(result, "Test") >= 1


def test_unstated_anchor_preference_is_unaffected():
    """No 0/0 statement at all (both unstated) -- the floor must never apply."""
    sub = _full_availability_submission("Test", shifts_requested=6)
    roster = {"Test": PhysicianConfig(id="Test", name="Test")}
    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    # No assertion on the anchor count itself (unstated has its own,
    # separate fallback-cap behavior) -- this just confirms generation
    # still succeeds normally and doesn't crash when neither anchor type
    # is stated.
    assert result.assignments


def test_explicit_positive_request_takes_priority_over_the_floor():
    """A physician who explicitly wants 2400h shifts must not be affected
    by the 0/0 floor logic (it should simply never trigger for them)."""
    sub = _full_availability_submission(
        "Test", shifts_requested=6,
        shifts_2400h_stated=True, shifts_2400h_requested=3,
        shifts_0600h_stated=True, shifts_0600h_requested=0,
    )
    roster = {"Test": PhysicianConfig(id="Test", name="Test")}
    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    n2400 = sum(
        1 for a in result.assignments
        if a.physician_id == "Test" and a.shift.time == "2400h"
    )
    assert n2400 >= 1


def test_floor_never_exceeds_the_existing_anchor_tolerance_cap():
    """The floor is bounded by the same per-type cap the overage-tolerance
    valve already enforces (anchor_target_tolerance, default 1 per type) --
    it must never force a hard cap violation."""
    sub = _full_availability_submission(
        "Test", shifts_requested=15,
        shifts_2400h_stated=True, shifts_2400h_requested=0,
        shifts_0600h_stated=True, shifts_0600h_requested=0,
    )
    roster = {"Test": PhysicianConfig(id="Test", name="Test")}
    gen = CpsatScheduleGenerator([sub], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    n2400 = sum(
        1 for a in result.assignments
        if a.physician_id == "Test" and a.shift.time == "2400h"
    )
    n0600 = sum(
        1 for a in result.assignments
        if a.physician_id == "Test" and a.shift.time == "0600h"
    )
    # Default anchor_target_tolerance is 1 per type -- so at most 1 of each.
    assert n2400 <= 1
    assert n0600 <= 1
