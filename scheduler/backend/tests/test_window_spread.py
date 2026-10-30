"""
Proportional spread over rolling two-week windows (feature
window_spread_penalty, 2026-10-30, item 7 of the jan6 feedback round):
P10 complained of 8 of 12 shifts inside Jan 19-31; jan9 still had 6-7
there, plus P92 7 of 8 in the first two weeks and P39 7 of 10. A physician
may hold ceil(requested x offered days in the window / offered days in the
month) shifts per 14-day window (step 7, plus one ending on the last day)
before paying 80 per extra shift.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator, window_spread_caps
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES

YEAR, MONTH = 2027, 1
DAYS = calendar.monthrange(YEAR, MONTH)[1]
NON_NIGHT = frozenset(c for c in ALL_SHIFT_CODES if not c.startswith("2400h"))


def test_caps_follow_the_physicians_own_availability():
    # P10-like: 12 shifts, 27 of 30 days offered, 11 in the first 15
    offered = [d <= 12 or 15 < d for d in range(2, 32)]   # days 2-12 and 16-31
    assert sum(offered) == 27
    caps = dict(((st, en), cap) for st, en, cap in window_spread_caps(12, offered))
    # last window = the final 14 days, all offered: ceil(12 * 14 / 27) = 7
    assert caps[(16, 30)] == 7
    # first window = days 2-15: 11 offered -> ceil(12 * 11 / 27) = 5
    assert caps[(0, 14)] == 5
    # somebody who only offered days 1-15: windows with none of their days
    # are omitted, the first window (14 of their 15 days) allows all 8,
    # and days 8-21 (8 of 15 offered) cap at ceil(8 * 8 / 15) = 5 -- the
    # spread is proportional WITHIN their own availability
    assert window_spread_caps(8, [d <= 15 for d in range(1, 32)]) == [(7, 21, 5)]
    assert window_spread_caps(0, offered) == []


def _sub(pid: str) -> PhysicianSubmission:
    """Every day offered (whole blocks); days 18-31 also carry per-code
    requests, worth the 5-point request bonus each -- the only pull
    toward the end of the month."""
    days = []
    for d in range(1, DAYS + 1):
        days.append(DayAvailability(
            date=datetime.date(YEAR, MONTH, d), wants_to_work=True,
            available_blocks=frozenset(range(5)),
            requested_shifts=NON_NIGHT if d >= 18 else frozenset(),
        ))
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=12, shifts_min=0, shifts_max=12,
                               shifts_2400h_stated=True, shifts_2400h_requested=0,
                               shifts_0600h_stated=True, shifts_0600h_requested=0, days=days)


def _max_window(result, pid: str, window: int = 14) -> int:
    days = sorted(a.date.day for a in result.assignments if a.physician_id == pid)
    return max(sum(1 for x in days if st <= x < st + window) for st in range(1, DAYS - window + 2))


def _solve(flag: bool):
    pid = "Spread"
    cfg = {"Spread": PhysicianConfig(id=pid, name=pid, night_ramp_in=False)}
    gen = CpsatScheduleGenerator([_sub(pid)], cfg, {"features": {"window_spread_penalty": flag}},
                                 trailing_assignments=None)
    return gen.generate(YEAR, MONTH, time_limit=30.0, num_workers=4)


def test_two_week_windows_hold_the_proportional_share():
    # 12 shifts over 31 offered days: ceil(12 * 14 / 31) = 6 per 14-day window
    on = _solve(True)
    assert sum(1 for a in on.assignments if a.physician_id == "Spread") == 12
    assert _max_window(on, "Spread") <= 6, _max_window(on, "Spread")


# No flag-off control at the solve level: a solo physician is already held
# near 6 per fortnight by the weekend-clump, 4-run and site-variety terms,
# so the 5-point request bonus cannot clump them -- the clumping this term
# answers comes from contention in a full roster (P10/P92/P39, January
# 2027), which is what the real-data run checks.
