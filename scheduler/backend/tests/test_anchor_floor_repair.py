"""
anchor_floor_repair.repair_anchor_floors: a floor physician below their
floor takes an anchor its holder has beyond their STATED request, giving a
non-anchor shift back; stated requests are never touched (user rule,
2026-10-10, from Lung's 0/2 in cpsatv2-jan4/5/6).
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend import anchor_floor_repair
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, Shift

YEAR, MONTH = 2026, 11
D = lambda d: datetime.date(YEAR, MONTH, d)  # noqa: E731
NEHC_1200 = Shift("1200h", "NEHC")
NEHC_1500 = Shift("1500h", "NEHC")
RAHI_1400 = Shift("1400h", "RAH I side")
NIGHT_NEHC = Shift("2400h", "NEHC")
NIGHT_A = Shift("2400h", "RAH A side")
NIGHT_B = Shift("2400h", "RAH B side")


def _sub(pid: str, requested: int = 10, **kwargs) -> PhysicianSubmission:
    days = [DayAvailability(date=D(d), wants_to_work=True, available_blocks=frozenset(range(5)),
                            requested_shifts=frozenset(ALL_SHIFT_CODES))
            for d in range(1, calendar.monthrange(YEAR, MONTH)[1] + 1)]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=requested, shifts_min=0, shifts_max=requested + 2,
                               days=days, **kwargs)


def _floor_sub(pid: str = "P", requested: int = 10) -> PhysicianSubmission:
    return _sub(pid, requested, shifts_2400h_stated=True, shifts_2400h_requested=0,
                shifts_0600h_stated=True, shifts_0600h_requested=0)


def _result(entries) -> ScheduleResult:
    return ScheduleResult(year=YEAR, month=MONTH, assignments=[
        Assignment(date=d, shift=sh, physician_id=p, physician_name=p) for p, d, sh in entries])


def _gen(subs, roster=None):
    roster = roster or {s.physician_id: PhysicianConfig(id=s.physician_id, name=s.physician_id) for s in subs}
    gen = CpsatScheduleGenerator(subs, roster, {})
    return gen


def _shifts(result, pid):
    return sorted((a.date.day, a.shift.code) for a in result.assignments if a.physician_id == pid)


def test_floor_physician_takes_an_overage_night_and_gives_a_day_shift_back():
    """Q stated 1 night and holds 2 (days 10 and 20, non-adjacent so neither
    is a pair); P (0/0, floor 2) holds none. P takes a night, Q takes P's
    day shift, both counts unchanged, Q still at their stated 1."""
    res = _result([("Q", D(10), NIGHT_NEHC), ("Q", D(20), NIGHT_A),
                   ("P", D(10), NEHC_1200), ("P", D(15), NEHC_1500)])
    subs = [_floor_sub("P"), _sub("Q", shifts_2400h_stated=True, shifts_2400h_requested=1)]
    roster = {"P": PhysicianConfig(id="P", name="P"),
              "Q": PhysicianConfig(id="Q", name="Q", allow_isolated_nights=True)}
    gen = _gen(subs, roster); gen.resync_from_result(res)

    res, n = anchor_floor_repair.repair_anchor_floors(gen, res)

    assert n == 1
    p_nights = [c for d, c in _shifts(res, "P") if c.startswith("2400h")]
    q_nights = [c for d, c in _shifts(res, "Q") if c.startswith("2400h")]
    assert len(p_nights) == 1 and len(q_nights) == 1
    assert len(_shifts(res, "P")) == 2 and len(_shifts(res, "Q")) == 2
    # Generator state mirrors the result.
    night_slot = next(a for a in res.assignments if a.physician_id == "P" and a.shift.time == "2400h")
    assert gen._slot_to_pid[(night_slot.date, night_slot.shift.code)] == "P"


def test_a_stated_request_is_never_taken():
    """Q stated 2 nights and holds exactly 2: nothing is overage, P stays at 0."""
    res = _result([("Q", D(10), NIGHT_NEHC), ("Q", D(20), NIGHT_A),
                   ("P", D(10), NEHC_1200), ("P", D(15), NEHC_1500)])
    subs = [_floor_sub("P"), _sub("Q", shifts_2400h_stated=True, shifts_2400h_requested=2)]
    roster = {"P": PhysicianConfig(id="P", name="P"),
              "Q": PhysicianConfig(id="Q", name="Q", allow_isolated_nights=True)}
    gen = _gen(subs, roster); gen.resync_from_result(res)

    res, n = anchor_floor_repair.repair_anchor_floors(gen, res)

    assert n == 0
    assert _shifts(res, "P") == [(10, "1200h NEHC"), (15, "1500h NEHC")]


def test_holder_is_not_left_with_an_isolated_night():
    """Q (1 night stated) holds the pair 10+11 and a lone 20. Taking day 10
    would isolate day 11 (HC-13b), so the pass must take day 20 instead --
    P offers nights every day, so both are reachable."""
    res = _result([("Q", D(10), NIGHT_NEHC), ("Q", D(11), NIGHT_A), ("Q", D(20), NIGHT_B),
                   ("P", D(5), NEHC_1200), ("P", D(15), NEHC_1500), ("P", D(25), RAHI_1400)])
    subs = [_floor_sub("P"), _sub("Q", shifts_2400h_stated=True, shifts_2400h_requested=1)]
    gen = _gen(subs); gen.resync_from_result(res)

    res, n = anchor_floor_repair.repair_anchor_floors(gen, res)

    assert n >= 1
    q_nights = sorted(d for d, c in _shifts(res, "Q") if c.startswith("2400h"))
    assert 10 in q_nights and 11 in q_nights
    assert [d for d, c in _shifts(res, "P") if c.startswith("2400h")] == [20]


def test_only_2400h_physicians_nights_are_never_overage():
    res = _result([("K", D(10), NIGHT_NEHC), ("K", D(20), NIGHT_A),
                   ("P", D(10), NEHC_1200), ("P", D(15), NEHC_1500)])
    subs = [_floor_sub("P"), _sub("K", requested=2)]
    roster = {"P": PhysicianConfig(id="P", name="P"),
              "K": PhysicianConfig(id="K", name="K", only_2400h=True, allow_isolated_nights=True)}
    gen = _gen(subs, roster); gen.resync_from_result(res)

    res, n = anchor_floor_repair.repair_anchor_floors(gen, res)
    assert n == 0


def test_floor_targets_mirror_the_solver():
    subs = [_floor_sub("P", 10), _floor_sub("S", 5), _floor_sub("T", 3), _floor_sub("U", 2), _floor_sub("Z", 0),
            _sub("Q", shifts_2400h_stated=True, shifts_2400h_requested=1), _sub("A", 10)]
    roster = {s.physician_id: PhysicianConfig(id=s.physician_id, name=s.physician_id) for s in subs}
    roster["A"].anchor_floor_exempt = True
    gen = _gen(subs, roster)
    assert anchor_floor_repair.floor_target(gen, "P") == 2       # >= 8 requested
    assert anchor_floor_repair.floor_target(gen, "S") == 1
    assert anchor_floor_repair.floor_target(gen, "T") == 1       # 3 requested: smallest floor
    assert anchor_floor_repair.floor_target(gen, "U") == 0       # 2 or fewer requested: no floor
    assert anchor_floor_repair.floor_target(gen, "Z") == 0       # 0 requested (Chee): no floor
    assert anchor_floor_repair.floor_target(gen, "Q") == 0       # stated requester
    assert anchor_floor_repair.floor_target(gen, "A") == 0       # admin exempt


def test_a_pair_of_overage_nights_is_split_between_two_floor_physicians():
    """Velji's shape: Q stated 2 nights and holds 4 as two pairs (10+11,
    20+21). Taking one night alone would isolate its mate, so the pass
    hands day 10 to P and day 11 to R (both floor physicians), and Q takes
    a day shift from each -- Q ends at exactly the stated 2, still a pair."""
    res = _result([("Q", D(10), NIGHT_NEHC), ("Q", D(11), NIGHT_A), ("Q", D(20), NIGHT_B), ("Q", D(21), NIGHT_NEHC),
                   ("P", D(10), NEHC_1200), ("P", D(15), NEHC_1500),
                   ("R", D(11), NEHC_1200), ("R", D(16), RAHI_1400)])
    subs = [_floor_sub("P"), _floor_sub("R"), _sub("Q", shifts_2400h_stated=True, shifts_2400h_requested=2)]
    gen = _gen(subs); gen.resync_from_result(res)

    res, n = anchor_floor_repair.repair_anchor_floors(gen, res)

    assert n == 2
    q_nights = sorted(d for d, c in _shifts(res, "Q") if c.startswith("2400h"))
    assert q_nights in ([10, 11], [20, 21])
    p_nights = [d for d, c in _shifts(res, "P") if c.startswith("2400h")]
    r_nights = [d for d, c in _shifts(res, "R") if c.startswith("2400h")]
    assert len(p_nights) == 1 and len(r_nights) == 1 and abs(p_nights[0] - r_nights[0]) == 1
    assert len(_shifts(res, "Q")) == 4 and len(_shifts(res, "P")) == 2 and len(_shifts(res, "R")) == 2


def test_pair_split_is_rolled_back_when_no_partner_can_take_the_mate():
    """Same, but R is unavailable on all four of Q's night days: the first
    half of each split would succeed on its own and must be undone, leaving
    Q's pairs intact and the generator state identical."""
    res = _result([("Q", D(10), NIGHT_NEHC), ("Q", D(11), NIGHT_A), ("Q", D(20), NIGHT_B), ("Q", D(21), NIGHT_NEHC),
                   ("P", D(10), NEHC_1200), ("P", D(15), NEHC_1500),
                   ("R", D(5), NEHC_1200), ("R", D(16), RAHI_1400)])
    r_sub = _floor_sub("R")
    r_sub.days = [DayAvailability(date=d.date, wants_to_work=d.date.day not in (10, 11, 20, 21),
                                  available_blocks=d.available_blocks if d.date.day not in (10, 11, 20, 21) else frozenset(),
                                  requested_shifts=d.requested_shifts if d.date.day not in (10, 11, 20, 21) else frozenset())
                  for d in r_sub.days]
    subs = [_floor_sub("P"), r_sub, _sub("Q", shifts_2400h_stated=True, shifts_2400h_requested=2)]
    gen = _gen(subs); gen.resync_from_result(res)

    res, n = anchor_floor_repair.repair_anchor_floors(gen, res)

    assert n == 0
    assert sorted(d for d, c in _shifts(res, "Q") if c.startswith("2400h")) == [10, 11, 20, 21]
    assert gen._slot_to_pid[(D(10), NIGHT_NEHC.code)] == "Q"
    assert gen._slot_to_pid[(D(10), NEHC_1200.code)] == "P"
