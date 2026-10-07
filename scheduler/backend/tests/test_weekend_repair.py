"""
weekend_repair.repair_weekend_splits: trade one side of a Fri+Sun-without-
Saturday weekend with a Saturday-only physician, rule-checked through the
generator, never creating a new split. Plus CpsatScheduleGenerator.
resync_from_result, which the pass relies on.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend import weekend_repair
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, Shift

YEAR, MONTH = 2026, 11          # Nov 2026: Fri 6 / Sat 7 / Sun 8 is a full weekend
FRI, SAT, SUN = (datetime.date(YEAR, MONTH, d) for d in (6, 7, 8))
NEHC_1200 = Shift("1200h", "NEHC")
NEHC_1500 = Shift("1500h", "NEHC")
RAHI_1000 = Shift("1000h", "RAH I side")
NIGHT = Shift("2400h", "NEHC")


def _sub(pid: str, requested: int = 6, unavailable: set[datetime.date] = frozenset()) -> PhysicianSubmission:
    days = []
    for d in range(1, calendar.monthrange(YEAR, MONTH)[1] + 1):
        date = datetime.date(YEAR, MONTH, d)
        avail = date not in unavailable
        days.append(DayAvailability(date=date, wants_to_work=avail,
                                    available_blocks=frozenset(range(5)) if avail else frozenset(),
                                    requested_shifts=frozenset(ALL_SHIFT_CODES) if avail else frozenset()))
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=requested, shifts_min=0, shifts_max=requested + 2, days=days)


def _result(entries) -> ScheduleResult:
    return ScheduleResult(year=YEAR, month=MONTH, assignments=[
        Assignment(date=d, shift=sh, physician_id=p, physician_name=p) for p, d, sh in entries])


def _gen(subs, roster=None):
    roster = roster or {s.physician_id: PhysicianConfig(id=s.physician_id, name=s.physician_id) for s in subs}
    return CpsatScheduleGenerator(subs, roster, {})


def _weekend_shape(result, pid):
    return sorted(a.date.day for a in result.assignments if a.physician_id == pid and a.date in (FRI, SAT, SUN))


def test_split_is_repaired_by_trading_with_a_saturday_only_physician():
    res = _result([("P", FRI, NEHC_1200), ("P", SUN, NEHC_1500), ("Q", SAT, NEHC_1200)])
    gen = _gen([_sub("P"), _sub("Q")])
    gen.resync_from_result(res)

    res, n = weekend_repair.repair_weekend_splits(gen, res)

    assert n == 1
    assert _weekend_shape(res, "P") == [7, 8]          # Friday traded away: P now has Sat+Sun
    assert _weekend_shape(res, "Q") == [6]             # Q has a lone Friday, still one weekend touched
    assert weekend_repair.find_splits(res) == []
    # Generator state mirrors the result.
    assert gen._slot_to_pid[(SAT, NEHC_1200.code)] == "P"
    assert gen._slot_to_pid[(FRI, NEHC_1200.code)] == "Q"


def test_trade_refused_when_it_would_break_spacing_or_repeat_a_code_on_adjacent_days():
    # Friday trade: P would hold Sat 1200h then Sun 1000h -> 22h gap (spacing).
    # Sunday trade: P would hold Fri 1200h NEHC then Sat 1200h NEHC -> same code on adjacent days (HC-10).
    res = _result([("P", FRI, NEHC_1200), ("P", SUN, RAHI_1000), ("Q", SAT, NEHC_1200)])
    gen = _gen([_sub("P"), _sub("Q")])
    gen.resync_from_result(res)
    _, n = weekend_repair.repair_weekend_splits(gen, res)
    assert n == 0
    assert _weekend_shape(res, "P") == [6, 8]


def test_no_trade_when_partner_would_be_left_with_a_split():
    # Q works Sat AND Sun: taking Q's Saturday would give Q Fri+Sun.
    res = _result([("P", FRI, NEHC_1200), ("P", SUN, RAHI_1000), ("Q", SAT, NEHC_1200), ("Q", SUN, NEHC_1200)])
    gen = _gen([_sub("P"), _sub("Q")])
    gen.resync_from_result(res)
    _, n = weekend_repair.repair_weekend_splits(gen, res)
    assert n == 0


def test_no_trade_when_split_physician_did_not_offer_saturday():
    res = _result([("P", FRI, NEHC_1200), ("P", SUN, RAHI_1000), ("Q", SAT, NEHC_1200)])
    gen = _gen([_sub("P", unavailable={SAT}), _sub("Q")])
    gen.resync_from_result(res)
    _, n = weekend_repair.repair_weekend_splits(gen, res)
    assert n == 0
    assert _weekend_shape(res, "P") == [6, 8]


def test_trade_never_moves_an_anchor_onto_someone_who_did_not_have_one():
    # The only Saturday on offer is a night; P's Friday is a daytime shift.
    res = _result([("P", FRI, NEHC_1200), ("P", SUN, RAHI_1000), ("Q", SAT, NIGHT)])
    gen = _gen([_sub("P"), _sub("Q")])
    gen.resync_from_result(res)
    _, n = weekend_repair.repair_weekend_splits(gen, res)
    assert n == 0


def test_resync_from_result_replaces_stale_state():
    res = _result([("P", FRI, NEHC_1200), ("Q", SAT, NEHC_1200)])
    gen = _gen([_sub("P"), _sub("Q")])
    gen._assign("P", SUN, NIGHT)                       # stale entry not in the result
    gen.resync_from_result(res)
    assert (SUN, NIGHT.code) not in gen._slot_to_pid
    assert gen._shift_count["P"] == 1 and gen._shift_count["Q"] == 1
    assert gen._anchor_count["P"] == 0


def test_split_is_repaired_by_completing_a_partners_weekend():
    """Strategy B: no Saturday-only physician exists; R works Sat+Sun. P's Friday
    goes to R (full weekend), P takes R's Tuesday. Nobody touches a new weekend."""
    TUE = datetime.date(YEAR, MONTH, 10)
    # R's Saturday must not share a code with P's Friday (HC-10) nor sit
    # within 23h of it; 1200h Fri -> 1500h Sat is 27h.
    res = _result([("P", FRI, NEHC_1200), ("P", SUN, NEHC_1500),
                   ("R", SAT, NEHC_1500), ("R", SUN, Shift("1700h", "NEHC")), ("R", TUE, NEHC_1200)])
    gen = _gen([_sub("P"), _sub("R")])
    gen.resync_from_result(res)

    res, n = weekend_repair.repair_weekend_splits(gen, res)

    assert n == 1
    assert _weekend_shape(res, "P") == [8]                 # single Sunday, no split
    assert _weekend_shape(res, "R") == [6, 7, 8]           # R now has the whole weekend
    assert sorted(a.date.day for a in res.assignments if a.physician_id == "P") == [8, 10]
    assert weekend_repair.find_splits(res) == []
