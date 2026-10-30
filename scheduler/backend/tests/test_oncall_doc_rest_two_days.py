"""
On-call rest, item 1 of the 2026-10-29 jan6 feedback round: a shift that
starts 1700h or later TWO days before a DOC blocks that DOC (feature
doc_rest_after_late_shift_two_days, on by default). The day in between was
already covered -- only a 0600h may precede a DOC -- but a 2400h on the 24th
followed by a DOC on the 26th slipped through: the night ends 0600 on the
25th and the call can activate at 0500 on the 26th. Replayed on the Oct 12
January run: 11 of 54 calls in that shape -> 0, fill unchanged (53/60).
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleGenerator, ScheduleResult
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import Shift

YEAR, MONTH = 2027, 1
DAYS = calendar.monthrange(YEAR, MONTH)[1]


def _sub(pid: str, doc_days: set[int]) -> PhysicianSubmission:
    days = [DayAvailability(date=datetime.date(YEAR, MONTH, d), wants_to_work=True,
                            available_blocks=frozenset(range(5)), requested_shifts=frozenset(),
                            doc_available=d in doc_days, noc_available=False)
            for d in range(1, DAYS + 1)]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=4, shifts_min=0, shifts_max=4, days=days)


def _result(shifts: dict[str, list[tuple[int, str]]]) -> ScheduleResult:
    """Shifts as (day, time) per physician, plus a call-less Filler working
    every day so every date counts as a scheduled day."""
    assignments = [Assignment(date=datetime.date(YEAR, MONTH, d), shift=Shift("0900h", "NEHC"),
                              physician_id="Filler", physician_name="Filler") for d in range(1, DAYS + 1)]
    for pid, items in shifts.items():
        for d, t in items:
            assignments.append(Assignment(date=datetime.date(YEAR, MONTH, d), shift=Shift(t, "NEHC"),
                                          physician_id=pid, physician_name=pid))
    return ScheduleResult(year=YEAR, month=MONTH, assignments=assignments, unfilled=[], issues=[], stats=None, on_calls=[])


def _run(config: dict, shifts, doc_day: int):
    subs = [_sub("Doc", {doc_day}), _sub("Filler", set())]
    roster = {s.physician_id: PhysicianConfig(id=s.physician_id, name=s.physician_id) for s in subs}
    gen = ScheduleGenerator(subs, roster, config)
    return {(c.date.day, c.call_type): c.physician_id for c in gen.assign_on_calls(_result(shifts)).on_calls}


def test_night_two_days_before_blocks_the_doc():
    """2400h on Jan 12 (Tue), DOC offered Jan 14 only -> no call."""
    assert _run({}, {"Doc": [(12, "2400h")]}, 14) == {}


def test_evening_two_days_before_blocks_the_doc_but_an_afternoon_does_not():
    assert _run({}, {"Doc": [(12, "1800h")]}, 14) == {}
    assert _run({}, {"Doc": [(12, "1700h")]}, 14) == {}
    assert _run({}, {"Doc": [(12, "1600h")]}, 14) == {(14, "DOC"): "Doc"}
    assert _run({}, {"Doc": [(12, "1400h")]}, 14) == {(14, "DOC"): "Doc"}


def test_three_days_before_is_fine_and_the_feature_flag_restores_the_old_rule():
    assert _run({}, {"Doc": [(11, "2400h")]}, 14) == {(14, "DOC"): "Doc"}
    off = {"features": {"doc_rest_after_late_shift_two_days": False}}
    assert _run(off, {"Doc": [(12, "2400h")]}, 14) == {(14, "DOC"): "Doc"}
