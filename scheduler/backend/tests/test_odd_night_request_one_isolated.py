"""
HC-13b: a physician under a two-night cap (max_consecutive_nights 2, no
allow_isolated_nights) who states an ODD 2400h request of 3+ may hold
exactly one lone night (user decision 2026-10-30). Before this, only even
counts were reachable: P31 asked for 5 and got 6 in every January solve,
P92/P51/P39 asked for 3 and got 4 or 2, while the human schedule hit all
four exactly with pairs plus one lone night.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES

YEAR, MONTH = 2027, 1
DAYS = calendar.monthrange(YEAR, MONTH)[1]
NON_NIGHT = frozenset(c for c in ALL_SHIFT_CODES if not c.startswith("2400h"))
# two pairs and one single day on offer: 5 nights only with a lone night
NIGHT_DAYS = {3, 4, 10, 11, 20}


def _sub(pid: str, nights: int) -> PhysicianSubmission:
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d), wants_to_work=True,
            available_blocks=frozenset(range(5)),
            requested_shifts=frozenset(ALL_SHIFT_CODES) if d in NIGHT_DAYS else NON_NIGHT,
        )
        for d in range(1, DAYS + 1)
    ]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=10, shifts_min=0, shifts_max=10,
                               shifts_2400h_stated=True, shifts_2400h_requested=nights, days=days)


def _nights(result, pid):
    return sorted(a.date.day for a in result.assignments if a.physician_id == pid and a.shift.time == "2400h")


def _lone(days: list[int]) -> list[int]:
    s = set(days)
    return [d for d in days if d - 1 not in s and d + 1 not in s]


def _solve(pid, nights):
    cfg = PhysicianConfig(id=pid, name=pid, max_consecutive_nights=2, night_ramp_in=False)
    gen = CpsatScheduleGenerator([_sub(pid, nights)], {pid: cfg}, {}, trailing_assignments=None)
    return gen.generate(YEAR, MONTH, time_limit=30.0, num_workers=4)


def test_odd_request_reaches_its_count_with_exactly_one_lone_night():
    got = _nights(_solve("Odd", 5), "Odd")
    assert len(got) == 5, got
    assert len(_lone(got)) == 1, got


def test_even_request_still_gets_no_lone_night():
    got = _nights(_solve("Even", 4), "Even")
    assert len(got) == 4, got
    assert _lone(got) == [], got


def test_three_night_cap_gets_no_allowance():
    """5 = 3 + 2 is reachable in runs, so no lone night is granted."""
    pid = "Cap3"
    cfg = PhysicianConfig(id=pid, name=pid, max_consecutive_nights=3, night_ramp_in=False)
    gen = CpsatScheduleGenerator([_sub(pid, 5)], {pid: cfg}, {}, trailing_assignments=None)
    got = _nights(gen.generate(YEAR, MONTH, time_limit=30.0, num_workers=4), pid)
    assert _lone(got) == [], got
