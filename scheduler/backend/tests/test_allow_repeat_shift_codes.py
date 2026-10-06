"""
Tests for allow_repeat_shift_codes (HC-10 per-physician exception) and
allow_isolated_nights (decoupled half of prefer_singleton_nights' HC-13b
exemption, without its anti-clustering penalty). Added for Dickey
(2027-01): his submission only ever offers one 2400h site, and he
explicitly prefers back-to-back nights at that one site -- the original
fix (prefer_singleton_nights) actively discouraged exactly that.

Solo/uncontested synthetic models, same technique used for the original
Dickey/RScheirer singleton-night diagnosis this session.
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
NIGHT_CODE = "2400h RAH I side"


def _single_site_night_submission(physician_id: str, shifts_requested: int) -> PhysicianSubmission:
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d),
            wants_to_work=True,
            available_blocks=frozenset(range(5)),
            requested_shifts=frozenset([NIGHT_CODE]),
        )
        for d in range(1, DAYS_IN_MONTH + 1)
    ]
    return PhysicianSubmission(
        physician_id=physician_id, physician_name=physician_id,
        year=YEAR, month=MONTH,
        shifts_requested=shifts_requested, shifts_min=0, shifts_max=shifts_requested,
        shifts_2400h_stated=True, shifts_2400h_requested=shifts_requested,
        days=days,
    )


def _night_count(result, pid: str) -> int:
    return sum(1 for a in result.assignments if a.physician_id == pid and a.shift.code == NIGHT_CODE)


def test_allow_repeat_shift_codes_permits_clustered_same_site_nights():
    cfg = PhysicianConfig(
        id="Dickey", name="Dickey", max_consecutive_nights=2,
        allow_repeat_shift_codes=[NIGHT_CODE], allow_isolated_nights=True,
        prefer_clustered_nights=True,
    )
    sub = _single_site_night_submission("Dickey", 4)
    gen = CpsatScheduleGenerator([sub], {"Dickey": cfg}, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    assert _night_count(result, "Dickey") == 4


def test_allow_isolated_nights_permits_a_genuine_solo_night():
    cfg = PhysicianConfig(
        id="Dickey", name="Dickey", max_consecutive_nights=2,
        allow_repeat_shift_codes=[NIGHT_CODE], allow_isolated_nights=True,
    )
    sub = _single_site_night_submission("Dickey", 1)
    gen = CpsatScheduleGenerator([sub], {"Dickey": cfg}, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    assert _night_count(result, "Dickey") == 1


def test_without_allow_repeat_shift_codes_single_site_still_deadlocked():
    """Regression: a physician who is NOT given the exception, with only
    one 2400h site and no isolation exemption, stays structurally
    deadlocked (0 nights) -- confirms the exception is actually load-
    bearing and not just always-on behavior."""
    cfg = PhysicianConfig(id="NoFix", name="NoFix", max_consecutive_nights=2)
    sub = _single_site_night_submission("NoFix", 4)
    gen = CpsatScheduleGenerator([sub], {"NoFix": cfg}, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    assert _night_count(result, "NoFix") == 0


def test_prefer_singleton_nights_physicians_unaffected():
    """A physician who genuinely prefers isolation (prefer_singleton_nights,
    no allow_repeat_shift_codes) must still be kept from clustering --
    HC-10 still blocks repeating their one site, and the anti-clustering
    penalty still applies, exactly as before this change."""
    cfg = PhysicianConfig(id="Other", name="Other", max_consecutive_nights=2, prefer_singleton_nights=True)
    sub = _single_site_night_submission("Other", 2)
    gen = CpsatScheduleGenerator([sub], {"Other": cfg}, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    dates = sorted(a.date for a in result.assignments if a.physician_id == "Other" and a.shift.code == NIGHT_CODE)
    # Both nights deliverable (each individually isolated, HC-10 still
    # blocks adjacency since no allow_repeat_shift_codes exception exists).
    assert len(dates) == 2
    for i in range(len(dates) - 1):
        assert (dates[i + 1] - dates[i]).days > 1


def test_max_consecutive_nights_of_one_is_automatically_isolation_exempt():
    """Reproduces a third real failure (Breton/McKinnon/Schindler, real
    January data): max_consecutive_nights=1 hard-caps any night run at 1
    (via HC-13 itself), but HC-13b otherwise requires >=2 adjacent nights
    for ANY 2400h at all -- a contradiction that's mathematically
    impossible regardless of availability, with no per-physician flag
    needed to trigger it (the cap value itself is the signal). Full
    multi-site availability here specifically to isolate this exemption
    from the separate single-site/HC-10 deadlock Dickey's fix covers."""
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d),
            wants_to_work=True,
            available_blocks=frozenset(range(5)),
            requested_shifts=frozenset(ALL_SHIFT_CODES),
        )
        for d in range(1, DAYS_IN_MONTH + 1)
    ]
    sub = PhysicianSubmission(
        physician_id="OneNight", physician_name="OneNight", year=YEAR, month=MONTH,
        shifts_requested=10, shifts_min=0, shifts_max=10,
        shifts_2400h_stated=True, shifts_2400h_requested=2,
        days=days,
    )
    cfg = PhysicianConfig(id="OneNight", name="OneNight", max_consecutive_nights=1)
    gen = CpsatScheduleGenerator([sub], {"OneNight": cfg}, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=15.0, num_workers=4)

    n2400 = sum(1 for a in result.assignments if a.physician_id == "OneNight" and a.shift.time == "2400h")
    assert n2400 >= 1
