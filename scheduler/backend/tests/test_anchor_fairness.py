"""
Anchor-shift fairness + related fixes from the 2026-10-07 real January solve
(cpsatv2-jan1.xlsx):

  * a physician who left the 0600h cell BLANK got 8 of 8 shifts as 0600h
    (Garcea), another got 2 unwanted 0600h on top of 4 requested nights
    (Bacon, 60% anchors), while explicit 0/0 physicians with 8 shifts
    (Wittmeier, Thirsk) sat at 1 anchor each -- blanks were free, explicit
    zeros cost 500, so the solver dumped anchors on whoever left the box
    empty. Now: everyone carries up to a fair share (40% of requested)
    before anyone is pushed past their own share, blank or not.
  * the 0/0 floor is 2 at >= 8 requested (was 10), and is no longer stuck
    at 1 for someone who can only work one anchor type.
  * a physician who asks for exactly ONE 2400h shift gets it (Grishin got
    0: the no-isolated-night rule demanded a 2nd, penalised, night).
  * post-solve RAH A <-> RAH B trades even out lopsided acute sides.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend import acute_balance
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, Shift

YEAR, MONTH = 2027, 1
DAYS = calendar.monthrange(YEAR, MONTH)[1]
CFG = {"anchor_shifts": {"anchor_target_tolerance": 1, "default_2400h_cap_unstated": 4,
                         "anchor_share_target": 0.40, "anchor_min_floor_full_threshold": 8}}


def _sub(pid: str, requested: int, *, r0600=0, s0600=False, r2400=0, s2400=False, codes=None) -> PhysicianSubmission:
    codes = frozenset(codes) if codes else frozenset(ALL_SHIFT_CODES)
    days = [DayAvailability(date=datetime.date(YEAR, MONTH, d), wants_to_work=True,
                            available_blocks=frozenset(range(5)), requested_shifts=codes)
            for d in range(1, DAYS + 1)]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=requested, shifts_min=0, shifts_max=requested,
                               shifts_0600h_requested=r0600, shifts_0600h_stated=s0600,
                               shifts_2400h_requested=r2400, shifts_2400h_stated=s2400, days=days)


def _cfg(pid: str, **kw) -> PhysicianConfig:
    return PhysicianConfig(id=pid, name=pid, **kw)


def _anchors(result, pid: str) -> int:
    return sum(1 for a in result.assignments if a.physician_id == pid and a.shift.time in ("0600h", "2400h"))


def _nights(result, pid: str) -> int:
    return sum(1 for a in result.assignments if a.physician_id == pid and a.shift.time == "2400h")


def _solve(subs, roster, time_limit=20.0):
    gen = CpsatScheduleGenerator(subs, roster, CFG)
    return gen.generate(YEAR, MONTH, time_limit=time_limit, num_workers=4)


# --------------------------------------------------------------------------- #
# Fair share
# --------------------------------------------------------------------------- #

def test_blank_anchor_cell_is_no_longer_a_free_dumping_ground():
    """Garcea shape: 8 shifts, 0600h blank, 2400h explicit 0, nights impossible.
    Solo with full availability the solver prefers anchor slots (they carry a
    higher fill weight), so without a cost on blanks she'd come out ~8/8."""
    sub = _sub("Garcea", 8, s0600=False, r2400=0, s2400=True)
    roster = {"Garcea": _cfg("Garcea", max_consecutive_nights=0, forbidden_shift_times=["2400h"])}
    res = _solve([sub], roster)
    n = _anchors(res, "Garcea")
    assert n <= 3, n               # fair share of 8 is 3; must not blow past it (solo, nothing forces any)


def test_zero_zero_physician_with_eight_shifts_reaches_floor_of_two():
    """Wittmeier shape: 8 shifts, explicit 0/0, no nights. Used to be stuck at 1
    because the per-type cap (0 + tolerance 1) made 2 unreachable."""
    sub = _sub("Wittmeier", 8, r0600=0, s0600=True, r2400=0, s2400=True)
    roster = {"Wittmeier": _cfg("Wittmeier", max_consecutive_nights=0, forbidden_shift_times=["2400h"])}
    res = _solve([sub], roster)
    assert _anchors(res, "Wittmeier") >= 2


def test_blank_blank_physician_gets_the_same_floor_as_an_explicit_zero_zero():
    """MacGougan shape (jan2): both anchor cells blank, 10 shifts, 0 anchors
    while explicit 0/0 physicians carried 2. A blank is "no preference" and
    must not rank below "I'd rather not": same floor of 2 at >= 8 shifts."""
    sub = _sub("MacGougan", 10, s0600=False, s2400=False)
    res = _solve([sub], {"MacGougan": _cfg("MacGougan")})
    assert _anchors(res, "MacGougan") >= 2


def test_blank_0600h_with_explicit_zero_nights_gets_the_floor_too():
    """Rosenblum shape (jan2): 0600h blank, 2400h explicit 0, 8 shifts, 0 anchors."""
    sub = _sub("Rosenblum", 8, s0600=False, r2400=0, s2400=True)
    res = _solve([sub], {"Rosenblum": _cfg("Rosenblum")})
    assert _anchors(res, "Rosenblum") >= 2


def test_admin_anchor_exempt_physician_is_not_brought_up_to_the_floor():
    """MacGougan with the admin toggle: blank/blank, 10 shifts -> no floor, no
    fair share; solo he takes no anchors at all."""
    sub = _sub("MacGougan", 10, s0600=False, s2400=False)
    res = _solve([sub], {"MacGougan": _cfg("MacGougan", anchor_floor_exempt=True)})
    assert _anchors(res, "MacGougan") == 0
    assert sum(1 for a in res.assignments if a.physician_id == "MacGougan") == 10


def test_admin_anchor_exempt_still_honours_an_explicit_anchor_request():
    """Haager with the toggle but a real request for 2 0600h still gets them."""
    sub = _sub("Haager", 9, r0600=2, s0600=True, r2400=0, s2400=True)
    res = _solve([sub], {"Haager": _cfg("Haager", anchor_floor_exempt=True)})
    assert sum(1 for a in res.assignments if a.physician_id == "Haager" and a.shift.time == "0600h") == 2


def test_excess_anchors_spread_to_fair_share_before_anyone_goes_over():
    """Three identical physicians, 10 shifts each, available Jan 1-16 only,
    offering the four 0600h slots plus a single 1200h NEHC slot per day: 16
    non-anchor slots for 30 shifts, so at least 14 anchors are forced. Fair
    share is 4 each (12 total). One left 0600h blank, two are explicit 0/0.
    Everyone must be brought up to their share before the excess lands, and
    the excess must not pile up on one person."""
    codes = {"0600h RAH A side", "0600h RAH B side", "0600h NEHC", "0600h RAH I side", "1200h NEHC"}
    def sub(pid, **kw):
        s_ = _sub(pid, 10, codes=codes, **kw)
        for d in s_.days:
            if d.date.day > 16:
                d.wants_to_work = False
                d.available_blocks = frozenset()
                d.requested_shifts = frozenset()
        return s_
    subs = [
        sub("Blank", s0600=False, r2400=0, s2400=True),
        sub("ZeroA", r0600=0, s0600=True, r2400=0, s2400=True),
        sub("ZeroB", r0600=0, s0600=True, r2400=0, s2400=True),
    ]
    roster = {p: _cfg(p, max_consecutive_nights=0, forbidden_shift_times=["2400h"]) for p in ("Blank", "ZeroA", "ZeroB")}
    res = _solve(subs, roster, time_limit=40.0)
    got = {p: _anchors(res, p) for p in roster}
    assert sum(got.values()) >= 14, got
    assert min(got.values()) >= 4, got     # everyone reaches their fair share first
    assert max(got.values()) <= 6, got     # the 2 forced excess anchors don't snowball on one person


def test_explicit_large_anchor_request_is_still_honoured_in_full():
    """KLam shape: 16 nights of 16 requested is his target, not overage."""
    sub = _sub("KLam", 16, r2400=16, s2400=True, r0600=0, s0600=True)
    roster = {"KLam": _cfg("KLam", only_2400h=True, max_consecutive_nights=4, max_consecutive_shifts=4,
                           prefer_clustered_nights=True)}
    res = _solve([sub], roster, time_limit=30.0)
    assert _nights(res, "KLam") >= 12


def test_only_0600h_physician_is_exempt_from_fair_share_and_fully_scheduled():
    """Garcea's real shape: only_0600h in the roster, 0600h cell blank, 8
    requested. Every shift she can work is an anchor, so fair-share penalties
    must not apply (they'd make her 4th+ shift cost more than it earns)."""
    sub = _sub("Garcea", 8, s0600=False, r2400=0, s2400=True)
    roster = {"Garcea": _cfg("Garcea", only_0600h=True, max_consecutive_nights=0)}
    res = _solve([sub], roster)
    mine = [a for a in res.assignments if a.physician_id == "Garcea"]
    assert len(mine) == 8
    assert all(a.shift.time == "0600h" for a in mine)


# --------------------------------------------------------------------------- #
# Single requested night
# --------------------------------------------------------------------------- #

def test_physician_requesting_exactly_one_night_gets_it():
    """Grishin shape: requested 2400h = 1 (stated). Previously 0, because the
    no-isolated-night rule needed a 2nd night the overage penalty forbade."""
    sub = _sub("Grishin", 12, r0600=6, s0600=True, r2400=1, s2400=True)
    res = _solve([sub], {"Grishin": _cfg("Grishin")}, time_limit=30.0)
    assert _nights(res, "Grishin") == 1


def test_roster_night_cap_of_zero_yields_to_an_explicit_night_request():
    """Grishin's real shape: physicians.yaml says max_consecutive_nights: 0
    (from the survey), but his January submission asks for 1 night. The
    dated request wins: he gets his night."""
    sub = _sub("Grishin", 12, r0600=6, s0600=True, r2400=1, s2400=True)
    res = _solve([sub], {"Grishin": _cfg("Grishin", max_consecutive_nights=0)}, time_limit=30.0)
    assert _nights(res, "Grishin") == 1


def test_forbidden_night_time_still_wins_over_a_night_request():
    sub = _sub("Francescutti", 6, r0600=1, s0600=True, r2400=1, s2400=True)
    cfg = _cfg("Francescutti", max_consecutive_nights=0, forbidden_shift_times=["2000h", "2400h"])
    res = _solve([sub], {"Francescutti": cfg}, time_limit=20.0)
    assert _nights(res, "Francescutti") == 0


# --------------------------------------------------------------------------- #
# A/B balance pass
# --------------------------------------------------------------------------- #

def _res(entries):
    return ScheduleResult(year=YEAR, month=MONTH, assignments=[
        Assignment(date=datetime.date(YEAR, MONTH, d), shift=Shift(t, s), physician_id=p, physician_name=p)
        for p, d, t, s in entries])


def test_balance_pass_trades_same_slot_pairs_to_even_out_sides():
    # Lopsided: Alpha 0A/3B, Beta 3A/0B, on three non-adjacent days at the same time.
    res = _res([("Alpha", d, "1200h", "RAH B side") for d in (4, 11, 18)]
               + [("Beta", d, "1200h", "RAH A side") for d in (4, 11, 18)])
    res, trades = acute_balance.balance_acute_sides(res, {"Alpha": _cfg("Alpha"), "Beta": _cfg("Beta")})
    rep = acute_balance.acute_side_report(res)
    assert trades >= 1
    assert abs(rep["Alpha"][0] - rep["Alpha"][1]) <= 1
    assert abs(rep["Beta"][0] - rep["Beta"][1]) <= 1
    # Nothing but the site changed: same physicians still cover the same (date, time).
    assert sorted((a.date.day, a.shift.time) for a in res.assignments) == sorted([(4, "1200h")] * 2 + [(11, "1200h")] * 2 + [(18, "1200h")] * 2)


def test_balance_pass_respects_forbidden_sites():
    res = _res([("Alpha", 4, "1200h", "RAH B side"), ("Alpha", 11, "1200h", "RAH B side"),
                ("Beta", 4, "1200h", "RAH A side"), ("Beta", 11, "1200h", "RAH A side")])
    roster = {"Alpha": _cfg("Alpha", forbidden_sites=["RAH A side"]), "Beta": _cfg("Beta")}
    _, trades = acute_balance.balance_acute_sides(res, roster)
    assert trades == 0


def test_balance_pass_never_creates_an_adjacent_same_code_or_run_repeat():
    # Alpha works Jan 4 (1200h RAH A) and Jan 5 (1200h RAH B); Beta works Jan 5 (1200h RAH A).
    # Trading Jan 5 would give Alpha 1200h RAH A on two adjacent days (HC-10) -> refused.
    res = _res([("Alpha", 4, "1200h", "RAH A side"), ("Alpha", 5, "1200h", "RAH B side"),
                ("Alpha", 20, "1200h", "RAH B side"), ("Alpha", 25, "1200h", "RAH B side"),
                ("Beta", 5, "1200h", "RAH A side")])
    _, trades = acute_balance.balance_acute_sides(res, {"Alpha": _cfg("Alpha"), "Beta": _cfg("Beta")})
    assert trades == 0
    assert [a.shift.site for a in sorted(res.assignments, key=lambda a: (a.physician_id, a.date))][:2] == ["RAH A side", "RAH B side"]


# --------------------------------------------------------------------------- #
# NEHC repeats within a run (lower-weight variant of the site-variety rule)
# --------------------------------------------------------------------------- #

def test_nehc_is_not_repeated_within_a_run_when_an_alternative_exists():
    """N Lam shape: a 3-day run that came out NEHC, RAH B, NEHC. Offer three
    distinct sites on three consecutive days; the run should use each once."""
    codes = {"1200h NEHC", "1000h RAH I side", "1200h RAH A side"}
    sub = _sub("LamN", 3, codes=codes)
    for d in sub.days:
        if d.date.day not in (4, 5, 6):
            d.wants_to_work = False
            d.available_blocks = frozenset()
            d.requested_shifts = frozenset()
    res = _solve([sub], {"LamN": _cfg("LamN")})
    sites = [a.shift.site for a in sorted(res.assignments, key=lambda a: a.date) if a.physician_id == "LamN"]
    assert len(sites) == 3
    assert len(set(sites)) == 3, sites
