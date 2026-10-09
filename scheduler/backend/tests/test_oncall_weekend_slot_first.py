"""
Weekend on-call slots are filled slot-first from physicians who already work
that Fri-Sun weekend (user rule, 2026-10-09).

Comparing cpsatv2-nov2 / jan3 with the human November and January schedules
showed the solver isolating 81-95% of its weekend calls (a Fri/Sat/Sun call
with no regular shift by that physician anywhere in the Fri-Sun cluster)
against 46-47% for the human schedules. Cause: the on-call pass was
physician-first and everyone's list put weekdays first, so weekend slots
went to whoever was left. Phase A now walks the weekend slots and gives
each to an eligible physician who has a regular shift that weekend; Phase B
is the old physician-first pass for everything else. Replayed on the real
nov2/jan3 regular schedules: 13/16 -> 7/16 and 19/20 -> 7/21 isolated,
fill 49 -> 50 and 52 -> 51.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleGenerator, ScheduleResult
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import Shift

YEAR, MONTH = 2027, 1          # Jan 1 2027 is a Friday; Jan 8-10 is Fri-Sun
DAYS = calendar.monthrange(YEAR, MONTH)[1]
CFG: dict = {}


def _sub(pid: str, doc_days: set[int]) -> PhysicianSubmission:
    days = [DayAvailability(date=datetime.date(YEAR, MONTH, d), wants_to_work=True,
                            available_blocks=frozenset(range(5)), requested_shifts=frozenset(),
                            doc_available=d in doc_days, noc_available=False)
            for d in range(1, DAYS + 1)]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=4, shifts_min=0, shifts_max=4, days=days)


def _result(shifts: dict[str, list[int]]) -> ScheduleResult:
    """A schedule with the given shifts (0600h starts, so a DOC call the
    next day clears the 23h spacing rule exactly), plus a call-less
    'Filler' working every day so each date counts as a scheduled day
    (the pass only considers dates that carry at least one slot)."""
    shifts = {**shifts, "Filler": list(range(1, DAYS + 1))}
    assignments = [Assignment(date=datetime.date(YEAR, MONTH, d), shift=Shift("0900h" if pid == "Filler" else "0600h", "NEHC"),
                              physician_id=pid, physician_name=pid, is_manual=False)
                   for pid, ds in shifts.items() for d in ds]
    return ScheduleResult(year=YEAR, month=MONTH, assignments=assignments, unfilled=[], issues=[], stats=None, on_calls=[])


def _calls(result) -> dict[tuple[int, str], str]:
    return {(c.date.day, c.call_type): c.physician_id for c in result.on_calls}


def test_weekend_slot_goes_to_the_physician_who_works_that_weekend():
    """Sat Jan 9 DOC. Away has no shift all weekend but is DOC-available on
    Jan 9 and nothing else; Here works Fri Jan 8 (0600h, so Saturday DOC is
    legal) and is DOC-available Jan 9. Physician-first order would hand
    the slot to whoever comes first in the dict (Away); slot-first gives
    it to Here."""
    subs = [_sub("Away", {9}), _sub("Here", {9})]
    roster = {"Away": PhysicianConfig(id="Away", name="Away"), "Here": PhysicianConfig(id="Here", name="Here")}
    gen = ScheduleGenerator(subs, roster, CFG)
    res = gen.assign_on_calls(_result({"Here": [8], "Away": [14, 15]}))
    assert _calls(res).get((9, "DOC")) == "Here", _calls(res)


def test_weekend_slot_still_filled_by_an_isolated_physician_when_nobody_works_that_weekend():
    """Phase A places only attached calls; the old fallback must still fill
    the slot with whoever is available so fill rate never drops."""
    subs = [_sub("Away", {9})]
    roster = {"Away": PhysicianConfig(id="Away", name="Away")}
    gen = ScheduleGenerator(subs, roster, CFG)
    res = gen.assign_on_calls(_result({"Away": [14, 15]}))
    assert _calls(res).get((9, "DOC")) == "Away", _calls(res)


def test_attached_physician_is_not_pulled_off_a_weekday_slot_they_would_otherwise_take():
    """Here works Fri Jan 8 and is DOC-available Sat Jan 9 AND Tue Jan 12.
    Weekday-first ordering used to send Here to Tuesday and leave Saturday
    to Away; slot-first takes Here for Saturday first, and Away then takes
    Tuesday (attached: Away works Jan 14)."""
    subs = [_sub("Away", {9, 12}), _sub("Here", {9, 12})]
    roster = {"Away": PhysicianConfig(id="Away", name="Away"), "Here": PhysicianConfig(id="Here", name="Here")}
    gen = ScheduleGenerator(subs, roster, CFG)
    res = gen.assign_on_calls(_result({"Here": [8], "Away": [14, 15]}))
    calls = _calls(res)
    assert calls.get((9, "DOC")) == "Here", calls
    assert calls.get((12, "DOC")) == "Away", calls


def test_independent_linkage_physician_is_left_to_phase_b():
    """A physician whose call_linkage is 'independent' asked for calls away
    from their shifts, so Phase A must not attach them to their own
    weekend even when they are the only one working it. Solo is also
    DOC-available on Tue Jan 12 (no shifts nearby), which their own
    ranking prefers; Phase B sends them there and Away gets Saturday."""
    subs = [_sub("Solo", {9, 12}), _sub("Away", {9})]
    roster = {"Solo": PhysicianConfig(id="Solo", name="Solo", call_linkage="independent"),
              "Away": PhysicianConfig(id="Away", name="Away")}
    gen = ScheduleGenerator(subs, roster, CFG)
    res = gen.assign_on_calls(_result({"Solo": [8], "Away": [14, 15]}))
    calls = _calls(res)
    assert calls.get((9, "DOC")) == "Away", calls
    assert calls.get((12, "DOC")) == "Solo", calls


# --------------------------------------------------------------------------- #
# Rest around a call follows department practice (2026-10-09, all four allowed)
# --------------------------------------------------------------------------- #

def _call_case(call_day: int, call_type: str, shifts: list[tuple[int, str]]):
    """Physician 'A' offers exactly one call; works the given (day, 'time site') shifts.
    A call-less 'Filler' makes every day a scheduled day."""
    days = [DayAvailability(date=datetime.date(YEAR, MONTH, d), wants_to_work=True,
                            available_blocks=frozenset(range(5)), requested_shifts=frozenset(),
                            doc_available=(d == call_day and call_type == "DOC"),
                            noc_available=(d == call_day and call_type == "NOC"))
            for d in range(1, DAYS + 1)]
    sub = PhysicianSubmission(physician_id="A", physician_name="A", year=YEAR, month=MONTH,
                              shifts_requested=4, shifts_min=0, shifts_max=4, days=days)
    assignments = [Assignment(date=datetime.date(YEAR, MONTH, d), shift=Shift(*code.split(" ", 1)),
                              physician_id="A", physician_name="A") for d, code in shifts]
    assignments += [Assignment(date=datetime.date(YEAR, MONTH, d), shift=Shift("0900h", "NEHC"),
                               physician_id="Filler", physician_name="Filler") for d in range(1, DAYS + 1)]
    res = ScheduleResult(year=YEAR, month=MONTH, assignments=assignments)
    gen = ScheduleGenerator([sub], {"A": PhysicianConfig(id="A", name="A")}, CFG)
    return [(o.physician_id, o.date.day, o.call_type) for o in gen.assign_on_calls(res).on_calls]


def test_doc_may_be_followed_by_a_next_day_shift_from_1000h():
    assert _call_case(12, "DOC", [(13, "1200h NEHC")]) == [("A", 12, "DOC")]
    assert _call_case(12, "DOC", [(13, "0600h NEHC")]) == []            # 0600h the next morning is still too soon


def test_noc_may_be_followed_only_by_the_next_nights_2400h():
    assert _call_case(12, "NOC", [(13, "2400h NEHC")]) == [("A", 12, "NOC")]
    assert _call_case(12, "NOC", [(13, "1200h NEHC")]) == []


def test_doc_may_follow_a_previous_day_0600h_only():
    assert _call_case(12, "DOC", [(11, "0600h NEHC")]) == [("A", 12, "DOC")]
    assert _call_case(12, "DOC", [(11, "1200h NEHC")]) == []


def test_noc_may_follow_a_previous_day_shift_that_ends_by_0200():
    assert _call_case(12, "NOC", [(11, "1800h RAH A side")]) == [("A", 12, "NOC")]
    assert _call_case(12, "NOC", [(11, "2400h NEHC")]) == []            # a night ending 0800 is too close
