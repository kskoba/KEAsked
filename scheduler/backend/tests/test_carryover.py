"""
Month-to-month carry-over tests (scheduler/backend/trailing.py's
PriorMonthSummary + the two "carry-over" soft terms in generator_cpsat.py):

  * acute debt -- someone who worked a disproportionately non-acute month
    gets extra weight on an additional acute (RAH A/B) shift next month,
    unless structurally barred from acute sites (Krisik/Francescutti).
  * repeat overage -- someone scheduled over their requested count last
    month is discouraged from going over again this month.

The solver tests are deliberately *contested*: two physicians identical in
every respect except last month's record compete for one scarce slot, and
the test is run mirrored (debt on A, then debt on B) so an arbitrary
tie-break can't pass it by luck.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend import trailing as trailing_mod
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import Shift

YEAR, MONTH = 2027, 1
DEC_YEAR = YEAR - 1
A_SIDE = Shift("1200h", "RAH A side")
I_SIDE = Shift("1000h", "RAH I side")
NEHC = Shift("1200h", "NEHC")
GROUP_A_TARGET = 0.38
CFG = {"site_distribution": {"group_a_target": GROUP_A_TARGET}}


def _dec(day: int) -> datetime.date:
    return datetime.date(DEC_YEAR, 12, day)


def _cfg(pid: str, **kw) -> PhysicianConfig:
    return PhysicianConfig(id=pid, name=pid, **kw)


def _december(assignments: list[tuple[str, int, Shift]]) -> ScheduleResult:
    return ScheduleResult(
        year=DEC_YEAR, month=12,
        assignments=[Assignment(date=_dec(d), shift=sh, physician_id=pid, physician_name=pid)
                     for pid, d, sh in assignments],
    )


def _dec_submission(pid: str, requested: int) -> PhysicianSubmission:
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=DEC_YEAR, month=12,
                               shifts_requested=requested, shifts_min=0, shifts_max=requested + 2, days=[])


def _submission(pid: str, available: dict[int, frozenset], requested: int, maximum: int) -> PhysicianSubmission:
    """Available only on the given days, only for the given shift codes."""
    days_in_month = calendar.monthrange(YEAR, MONTH)[1]
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d),
            wants_to_work=d in available,
            available_blocks=frozenset(range(5)) if d in available else frozenset(),
            requested_shifts=available.get(d, frozenset()),
        )
        for d in range(1, days_in_month + 1)
    ]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=requested, shifts_min=0, shifts_max=maximum, days=days)


def _by_pid(result) -> dict[str, list[Assignment]]:
    out: dict[str, list[Assignment]] = {}
    for a in result.assignments:
        out.setdefault(a.physician_id, []).append(a)
    return out


# --------------------------------------------------------------------------- #
# PriorMonthSummary
# --------------------------------------------------------------------------- #

def test_summary_tallies_regular_shifts_and_acute_share():
    dec = _december([("A", 1, A_SIDE), ("A", 3, I_SIDE), ("A", 5, NEHC), ("B", 2, NEHC)])
    out = trailing_mod.build_prior_month_summaries(dec)
    assert out["A"] == trailing_mod.PriorMonthSummary(shifts_worked=3, acute_shifts=1, shifts_requested=None)
    assert out["A"].non_acute_shifts == 2
    assert out["B"].acute_shifts == 0 and out["B"].shifts_requested is None


def test_acute_debt_is_whole_shifts_below_target_share():
    # 10 worked at 38% target -> expected 4 acute.
    assert trailing_mod.PriorMonthSummary(10, 4).acute_debt(GROUP_A_TARGET) == 0
    assert trailing_mod.PriorMonthSummary(10, 1).acute_debt(GROUP_A_TARGET) == 3
    assert trailing_mod.PriorMonthSummary(10, 7).acute_debt(GROUP_A_TARGET) == 0   # over-acute is not a debt
    assert trailing_mod.PriorMonthSummary(0, 0).acute_debt(GROUP_A_TARGET) == 0
    assert trailing_mod.PriorMonthSummary(1, 0).acute_debt(GROUP_A_TARGET) == 0    # round(0.38) == 0: too few to judge


def test_overage_needs_a_known_requested_count():
    assert trailing_mod.PriorMonthSummary(10, 4, shifts_requested=8).overage == 2
    assert trailing_mod.PriorMonthSummary(8, 4, shifts_requested=8).overage == 0
    assert trailing_mod.PriorMonthSummary(10, 4, shifts_requested=None).overage == 0
    assert trailing_mod.PriorMonthSummary(10, 4, shifts_requested=0).overage == 0


def test_summary_takes_requested_count_from_prior_submissions_only_for_that_month():
    dec = _december([("A", d, NEHC) for d in range(1, 11)])
    subs = [_dec_submission("A", 8), _dec_submission("Idle", 4)]
    wrong_month = PhysicianSubmission(physician_id="A", physician_name="A", year=YEAR, month=MONTH,
                                      shifts_requested=99, days=[])
    out = trailing_mod.build_prior_month_summaries(dec, subs + [wrong_month])
    assert out["A"].shifts_requested == 8 and out["A"].overage == 2
    # Submitted but never scheduled: still present, worked 0, so they're not mistaken for absent.
    assert out["Idle"] == trailing_mod.PriorMonthSummary(0, 0, shifts_requested=4)


def test_acute_eligibility_follows_forbidden_sites():
    assert trailing_mod.acute_eligible(None)
    assert trailing_mod.acute_eligible(_cfg("x", forbidden_sites=["RAH A side"]))          # B side still open
    assert not trailing_mod.acute_eligible(_cfg("krisik", forbidden_sites=["RAH A side", "RAH B side", "RAH I side", "RAH F side"]))
    assert not trailing_mod.acute_eligible(_cfg("francescutti", forbidden_sites=["RAH A side", "RAH B side", "RAH F side"]))


def test_prior_month_summary_line_names_the_carryover_physicians():
    roster = {"Krisik": _cfg("Krisik", forbidden_sites=["RAH A side", "RAH B side"])}
    summaries = {
        "Debt": trailing_mod.PriorMonthSummary(10, 0),
        "Krisik": trailing_mod.PriorMonthSummary(8, 0),           # in debt on paper, but can't work acute
        "Over": trailing_mod.PriorMonthSummary(10, 4, shifts_requested=8),
    }
    line = trailing_mod.summarize_prior_month(summaries, roster, GROUP_A_TARGET)
    assert "acute debt: 1 (Debt)" in line
    assert "went over requested: 1 (Over)" in line


# --------------------------------------------------------------------------- #
# Solver: acute debt
# --------------------------------------------------------------------------- #

def _solve_contested_acute(debtor: str):
    """Two identical physicians, one acute slot (RAH A 1200h) and one non-acute
    (NEHC 1200h) on Jan 1. Only `debtor` has an acute debt from December."""
    avail = {1: frozenset({A_SIDE.code, NEHC.code})}
    subs = [_submission("Alpha", avail, 1, 1), _submission("Beta", avail, 1, 1)]
    roster = {"Alpha": _cfg("Alpha"), "Beta": _cfg("Beta")}
    prior = {debtor: trailing_mod.PriorMonthSummary(shifts_worked=10, acute_shifts=0)}
    gen = CpsatScheduleGenerator(subs, roster, CFG, prior_month=prior)
    return gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)


def test_acute_debt_wins_the_contested_acute_slot_both_ways():
    for debtor, other in (("Alpha", "Beta"), ("Beta", "Alpha")):
        result = _solve_contested_acute(debtor)
        got = _by_pid(result)
        assert [a.shift.code for a in got[debtor]] == [A_SIDE.code], debtor
        assert [a.shift.code for a in got[other]] == [NEHC.code], other


def test_acute_debt_is_ignored_for_a_physician_barred_from_acute_sites():
    """Krisik-shaped roster entry: in debt on paper, but every acute site is
    forbidden, so the carry-over must not be built (and the solve must still
    place her on the one site she can work)."""
    avail = {1: frozenset({A_SIDE.code, NEHC.code})}
    subs = [_submission("Krisik", avail, 1, 1), _submission("Beta", avail, 1, 1)]
    roster = {
        "Krisik": _cfg("Krisik", forbidden_sites=["RAH A side", "RAH B side", "RAH I side", "RAH F side"]),
        "Beta": _cfg("Beta"),
    }
    prior = {"Krisik": trailing_mod.PriorMonthSummary(shifts_worked=8, acute_shifts=0)}
    gen = CpsatScheduleGenerator(subs, roster, CFG, prior_month=prior)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)
    got = _by_pid(result)
    assert [a.shift.code for a in got["Krisik"]] == [NEHC.code]
    assert [a.shift.code for a in got["Beta"]] == [A_SIDE.code]


# --------------------------------------------------------------------------- #
# Solver: repeat overage
# --------------------------------------------------------------------------- #

def _solve_contested_overage(was_over: str):
    """Two identical physicians each requesting 1 (max 2). Three non-acute
    slots exist across Jan 4 and Jan 6 (Mon/Wed: non-adjacent so HC-10's
    same-code-on-adjacent-days rule can't interfere, weekdays so the weekend
    cap can't either), so exactly one of them must go to 2 shifts. Only
    `was_over` exceeded their requested count in December."""
    avail = {4: frozenset({I_SIDE.code, NEHC.code}), 6: frozenset({I_SIDE.code})}
    subs = [_submission("Alpha", avail, 1, 2), _submission("Beta", avail, 1, 2)]
    roster = {"Alpha": _cfg("Alpha"), "Beta": _cfg("Beta")}
    prior = {was_over: trailing_mod.PriorMonthSummary(shifts_worked=10, acute_shifts=4, shifts_requested=8)}
    gen = CpsatScheduleGenerator(subs, roster, CFG, prior_month=prior)
    return gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)


def test_repeat_overage_goes_to_the_physician_who_was_not_over_last_month_both_ways():
    for was_over, other in (("Alpha", "Beta"), ("Beta", "Alpha")):
        result = _solve_contested_overage(was_over)
        got = _by_pid(result)
        assert len(result.assignments) == 3, "the penalty must never leave one of the 3 reachable slots empty"
        assert len(got[was_over]) == 1, was_over
        assert len(got[other]) == 2, other


def test_repeat_overage_penalty_never_beats_filling_a_slot():
    """Only one physician exists and they were over last month: the 3rd slot
    still gets filled (overage is discouraged, not forbidden)."""
    avail = {4: frozenset({I_SIDE.code}), 6: frozenset({I_SIDE.code})}
    subs = [_submission("Alpha", avail, 1, 2)]
    prior = {"Alpha": trailing_mod.PriorMonthSummary(shifts_worked=10, acute_shifts=4, shifts_requested=8)}
    gen = CpsatScheduleGenerator(subs, {"Alpha": _cfg("Alpha")}, CFG, prior_month=prior)
    result = gen.generate(YEAR, MONTH, time_limit=10.0, num_workers=4)
    assert len(_by_pid(result)["Alpha"]) == 2
