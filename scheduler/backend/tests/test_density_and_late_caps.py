"""
Items 2-4 of the 2026-10-29 jan6 feedback round:
  * PhysicianConfig.max_shifts_per_7_days -- hard cap in any rolling 7-day
    window (P19: three admin days a week, at most 3 shifts in 7; the jan6
    solve gave her 4 in Jan 8-14).
  * PhysicianConfig.max_late_shifts_per_month -- hard cap on 1700h+ starts
    (P19 wants one late evening a month; jan6 gave an 1800h and a 2000h).
  * feature weekly_density_penalty -- soft roster-wide pacing: 5th shift in
    the trailing 7 days 50, 5 in 6 days 100, 6 in 7 days 400 (P10's 2 on /
    1 off / 3 on, P31's 6 in 7 days).
Solo/uncontested models, same technique as the other rule tests.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig, _parse_physician, physician_config_to_raw
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, SHIFT_CODE_LOOKUP

YEAR, MONTH = 2027, 1
DAYS = calendar.monthrange(YEAR, MONTH)[1]
DAY_CODES = frozenset(c for c in ALL_SHIFT_CODES
                      if not c.startswith("2400h") and SHIFT_CODE_LOOKUP[c].start_hour < 17)
LATE_CODES = frozenset(c for c in ALL_SHIFT_CODES
                       if not c.startswith("2400h") and SHIFT_CODE_LOOKUP[c].start_hour >= 17)
ONE = datetime.timedelta(days=1)


def _sub(pid: str, requested: int, codes_by_day: dict[int, frozenset]) -> PhysicianSubmission:
    days = [DayAvailability(date=datetime.date(YEAR, MONTH, d), wants_to_work=d in codes_by_day,
                            available_blocks=frozenset(range(5)) if d in codes_by_day else frozenset(),
                            requested_shifts=codes_by_day.get(d, frozenset()))
            for d in range(1, DAYS + 1)]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=requested, shifts_min=0, shifts_max=requested, days=days)


def _solve(sub, cfg: PhysicianConfig, config: dict | None = None, seconds: float = 20.0):
    gen = CpsatScheduleGenerator([sub], {sub.physician_id: cfg}, config or {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=seconds, num_workers=4)
    return {a.date: a.shift for a in result.assignments if a.physician_id == sub.physician_id}


def _max_in_window(dates, width: int) -> int:
    ds = sorted(dates)
    return max((sum(1 for x in ds if d <= x < d + width * ONE) for d in ds), default=0)


def test_max_shifts_per_7_days_is_a_hard_cap_and_the_request_is_still_reachable():
    """P19's real January offer shape: 12 days, 7 requested, cap 3 -> all 7
    placed, never more than 3 in any 7 days."""
    offered = {d: DAY_CODES for d in (7, 8, 9, 10, 12, 14, 21, 22, 23, 24, 26, 28)}
    sched = _solve(_sub("P19", 7, offered), PhysicianConfig(id="P19", name="P19", max_consecutive_shifts=2,
                                                            max_shifts_per_7_days=3))
    assert len(sched) == 7
    assert _max_in_window(sched, 7) <= 3


def test_max_late_shifts_per_month_caps_evening_starts():
    """Late shifts offered every day, day shifts on five days only, 8
    requested: without the cap the month fills with evenings; with a cap
    of 1 the physician gets the five day shifts plus one late one."""
    offered = {d: (LATE_CODES | (DAY_CODES if d in (3, 10, 17, 24, 31) else frozenset())) for d in range(1, DAYS + 1)}
    cfg = PhysicianConfig(id="P19", name="P19", max_consecutive_shifts=3, max_late_shifts_per_month=1)
    sched = _solve(_sub("P19", 8, offered), cfg)
    late = [d for d, s in sched.items() if s.start_hour >= 17]
    assert len(late) <= 1
    assert len(sched) == 6


def test_density_penalty_spreads_six_shifts_instead_of_three_off_three():
    """Fourteen days offered, 6 requested, SIAR 4. A 3 on / 1 off / 3 on
    shape costs 650 under the density terms while two runs separated by
    two or more days off cost nothing and earn the same adjacency
    bonuses, so the solve must land at most 4 in any 7 days and never 5 in
    6 days."""
    offered = {d: DAY_CODES for d in range(1, 15)}
    sched = _solve(_sub("P10", 6, offered), PhysicianConfig(id="P10", name="P10", max_consecutive_shifts=4))
    assert len(sched) == 6
    assert _max_in_window(sched, 7) <= 4
    assert _max_in_window(sched, 6) <= 4


def test_density_feature_flag_off_still_solves():
    offered = {d: DAY_CODES for d in range(1, 15)}
    sched = _solve(_sub("P10", 6, offered), PhysicianConfig(id="P10", name="P10", max_consecutive_shifts=4),
                   config={"features": {"weekly_density_penalty": False}}, seconds=10.0)
    assert len(sched) == 6


def test_new_roster_fields_round_trip_through_yaml():
    raw = {"id": "P19", "name": "P19", "scheduling": {"max_consecutive_shifts": 2,
                                                       "max_shifts_per_7_days": 3,
                                                       "max_late_shifts_per_month": 1}}
    cfg = _parse_physician(raw)
    assert (cfg.max_shifts_per_7_days, cfg.max_late_shifts_per_month) == (3, 1)
    back = physician_config_to_raw(cfg)["scheduling"]
    assert back["max_shifts_per_7_days"] == 3 and back["max_late_shifts_per_month"] == 1
    plain = physician_config_to_raw(_parse_physician({"id": "X", "name": "X"}))["scheduling"]
    assert "max_shifts_per_7_days" not in plain and "max_late_shifts_per_month" not in plain
