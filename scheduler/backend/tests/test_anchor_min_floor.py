"""
Tests for the 0/0-stated minimum-anchor-shift floor in generator_cpsat.py:
a physician who explicitly states they want ZERO 0600h AND ZERO 2400h
shifts still gets a guaranteed floor of at least one anchor shift (scaled
by their total requested count), so anchor coverage doesn't end up
entirely concentrated on whoever happened to ask for it.

This floor is its own lexicographic stage (dedicated solver pass + lock
the result), not a soft bonus competing in the final objective -- a
soft-bonus-only version of this shipped first and was confirmed broken
against real January data: 5 of 7 eligible physicians still got 0 anchor
shifts despite the bonus, and that deficit is exactly what concentrated
anchor load onto whoever was cheapest to use instead (one physician alone
ended up with 64% of his month as anchor shifts). The solo tests below
use the same uncontested technique as the Dickey/RScheirer singleton-night
diagnosis; test_floor_survives_contention below is the one that actually
reproduces the real-data failure mode and proves the guaranteed stage
holds up against it.
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


def test_floor_survives_contention_from_uncapped_blank_physicians():
    """Reproduces the real-data failure mode this floor was redesigned to
    fix: a 0/0-stated physician competing against physicians with blank
    (unstated, uncapped) anchor fields -- exactly the shape that let one
    real physician (Wrubleski, real January data) absorb 64% of his month
    as anchor shifts while 5 of 7 0/0-stated physicians got zero. The
    soft-bonus-only version of this floor failed this exact scenario in
    production; the guaranteed lexicographic stage must not."""
    sub_zz = _full_availability_submission(
        "ZeroZero", shifts_requested=6,
        shifts_2400h_stated=True, shifts_2400h_requested=0,
        shifts_0600h_stated=True, shifts_0600h_requested=0,
    )
    # Both left fully unstated -- blank anchor fields, no cap at all,
    # happy to take every shift going (the real "cheap filler" shape).
    filler_a = _full_availability_submission("FillerA", shifts_requested=15)
    filler_b = _full_availability_submission("FillerB", shifts_requested=15)
    roster = {
        "ZeroZero": PhysicianConfig(id="ZeroZero", name="ZeroZero"),
        "FillerA": PhysicianConfig(id="FillerA", name="FillerA"),
        "FillerB": PhysicianConfig(id="FillerB", name="FillerB"),
    }
    gen = CpsatScheduleGenerator([sub_zz, filler_a, filler_b], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=30.0, num_workers=4)

    assert _anchor_total(result, "ZeroZero") >= 1
