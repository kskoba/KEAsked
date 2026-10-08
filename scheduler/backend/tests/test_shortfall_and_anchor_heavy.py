"""
Two user rules from the cpsatv2-jan3 review (2026-10-07):

  * Shortfall is spread, never piled: when requests exceed the slots on
    offer, nobody should finish more than one shift under their request
    while someone else sits at theirs (Krisik got 6 of 8 with 13 open
    days while seven colleagues were exactly at request). Implemented as
    escalating per-physician shortfall steps -- in the final objective
    and inside the casual-priority tiers, which freeze each physician's
    count before the final objective runs.
  * An anchor-heavy physician (stated anchors above half their requested
    shifts) pays double for the first anchor beyond their target, so an
    unavoidable extra night goes to someone lighter first (Fisher: 7 of
    11 anchors asked, handed a 6th night). Roster anchor people
    (anchor_preference / only_* / anchor_floor_exempt) are not affected.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission

YEAR, MONTH = 2027, 1
DAYS = calendar.monthrange(YEAR, MONTH)[1]
CFG = {"anchor_shifts": {"anchor_target_tolerance": 1, "default_2400h_cap_unstated": 4,
                         "anchor_share_target": 0.40, "anchor_min_floor_full_threshold": 8}}


def _sub(pid: str, requested: int, days, codes: set[str] | None, *, r2400=0, s2400=False,
         r0600=0, s0600=False, maximum: int | None = None) -> PhysicianSubmission:
    """Available for exactly `codes` on the given days of the month, nothing
    else. `days` may also be a {day: codes} mapping for per-day codes."""
    by_day = days if isinstance(days, dict) else {d: codes for d in days}
    avail = []
    for d in range(1, DAYS + 1):
        on = d in by_day
        avail.append(DayAvailability(date=datetime.date(YEAR, MONTH, d), wants_to_work=on,
                                     available_blocks=frozenset(range(5)) if on else frozenset(),
                                     requested_shifts=frozenset(by_day[d]) if on else frozenset()))
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=requested, shifts_min=0,
                               shifts_max=requested if maximum is None else maximum,
                               shifts_2400h_requested=r2400, shifts_2400h_stated=s2400,
                               shifts_0600h_requested=r0600, shifts_0600h_stated=s0600, days=avail)


def _cfg(pid: str, **kw) -> PhysicianConfig:
    return PhysicianConfig(id=pid, name=pid, **kw)


def _solve(subs, roster, time_limit=20.0):
    gen = CpsatScheduleGenerator(subs, roster, CFG)
    return gen.generate(YEAR, MONTH, time_limit=time_limit, num_workers=4)


def _count(result, pid: str) -> int:
    return sum(1 for a in result.assignments if a.physician_id == pid)


def _nights(result, pid: str) -> int:
    return sum(1 for a in result.assignments if a.physician_id == pid and a.shift.time == "2400h")


# --------------------------------------------------------------------------- #
# Shortfall spread
# --------------------------------------------------------------------------- #

def test_shortfall_is_spread_one_per_physician_not_piled():
    """Three physicians each ask for 4 of the same single daily slot over 10
    days: 12 requested, 10 on offer. The request bonus alone (100 per
    essential unit, 50 per marginal one) would be indifferent between 4/4/2
    and 4/3/3 -- both lose two marginal units -- and A and B carry a 1.5x
    priority weight, which on its own makes piling the loss on C strictly
    better (1100 vs 1075 bonus points). The escalating shortfall step
    (300 for C's second missing shift) is what flips it to 4/3/3."""
    code = {"1200h NEHC"}
    subs = [_sub(p, 4, range(1, 11), code) for p in ("A", "B", "C")]
    roster = {"A": _cfg("A", priority_weight=1.5), "B": _cfg("B", priority_weight=1.5), "C": _cfg("C")}
    res = _solve(subs, roster)
    got = sorted(_count(res, p) for p in ("A", "B", "C"))
    assert sum(got) == 10, got
    assert got == [3, 3, 4], got          # never 2/4/4


def test_shortfall_spread_holds_inside_the_casual_tiers():
    """Same shape with a casual physician present, which switches the solver
    onto its lexicographic request tiers. Those tiers freeze everyone's count
    before the final objective runs, so the spread has to be won inside
    them: no normal physician may finish two short, and the one slot left
    after the normals' protected units should reach the casual rather than
    a normal physician's contestable extra."""
    code = {"1200h NEHC"}
    subs = [_sub(p, 4, range(1, 11), code) for p in ("A", "B", "C")] + [_sub("D", 2, range(1, 11), code)]
    roster = {p: _cfg(p) for p in ("A", "B", "C")}
    roster["D"] = _cfg("D", casual=True)
    res = _solve(subs, roster)
    normals = {p: _count(res, p) for p in ("A", "B", "C")}
    assert sum(normals.values()) + _count(res, "D") == 10, (normals, _count(res, "D"))
    assert all(n >= 3 for n in normals.values()), normals
    assert _count(res, "D") >= 1, (normals, _count(res, "D"))


# --------------------------------------------------------------------------- #
# Anchor-heavy first step
# --------------------------------------------------------------------------- #

def test_extra_night_goes_to_the_lighter_physician_not_the_anchor_heavy_one():
    """Four night slots on offer (Mon-Thu, one distinct site each so the
    no-same-code-on-consecutive-days rule never bites), two takers. A asked
    for 3 shifts with 2 nights (67% anchors: heavy); B asked for 2 shifts
    with 1 night (50%: not heavy). Both only offered nights, so someone has
    to take a night beyond their target. A carries a 3x priority weight,
    so on request bonuses alone A=3/B=1 beats A=2/B=2 by 100 points (850
    vs 750) and the plain anchor penalties (500 + 150 either way) tie, so
    without the rule A takes the 3rd night. The heavy first step (1000
    instead of 500 for A) is what hands it to B."""
    codes = {4: {"2400h NEHC"}, 5: {"2400h RAH A side"}, 6: {"2400h RAH B side"}, 7: {"2400h RAH I side"}}
    subs = [_sub("A", 3, codes, None, r2400=2, s2400=True, r0600=0, s0600=True),
            _sub("B", 2, codes, None, r2400=1, s2400=True, r0600=0, s0600=True)]
    roster = {"A": _cfg("A", max_consecutive_nights=4, priority_weight=3.0), "B": _cfg("B", max_consecutive_nights=4)}
    res = _solve(subs, roster)
    assert _nights(res, "A") + _nights(res, "B") == 4, (_nights(res, "A"), _nights(res, "B"))
    assert _nights(res, "A") == 2, (_nights(res, "A"), _nights(res, "B"))
    assert _nights(res, "B") == 2
