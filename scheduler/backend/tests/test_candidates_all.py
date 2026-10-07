"""/api/candidates?include_all=true lists every physician with a submission,
flagging hard-blocked ones instead of hiding them (the replace dialog must be
searchable: a physician marked unavailable may have agreed to a trade)."""

from __future__ import annotations

import calendar
import datetime

import pytest

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, Shift

YEAR, MONTH = 2027, 1
DAY = datetime.date(YEAR, MONTH, 12)


def _sub(pid: str, unavailable: set[datetime.date] = frozenset()) -> PhysicianSubmission:
    days = []
    for d in range(1, calendar.monthrange(YEAR, MONTH)[1] + 1):
        date = datetime.date(YEAR, MONTH, d); ok = date not in unavailable
        days.append(DayAvailability(date=date, wants_to_work=ok,
                                    available_blocks=frozenset(range(5)) if ok else frozenset(),
                                    requested_shifts=frozenset(ALL_SHIFT_CODES) if ok else frozenset()))
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=6, shifts_min=0, shifts_max=8, days=days)


@pytest.fixture
def server():
    from scheduler.api import server as server_mod
    saved = {k: server_mod._state[k] for k in ("result", "year", "month", "submissions", "roster", "generator", "scheduler_config")}
    yield server_mod
    server_mod._state.update(saved)


def test_include_all_lists_everyone_and_flags_hard_blocks(server):
    subs = [_sub("Free"), _sub("Unavail", unavailable={DAY}), _sub("Busy")]
    roster = {s.physician_id: PhysicianConfig(id=s.physician_id, name=s.physician_id) for s in subs}
    result = ScheduleResult(year=YEAR, month=MONTH, assignments=[
        Assignment(date=DAY, shift=Shift("1200h", "NEHC"), physician_id="Busy", physician_name="Busy"),      # same day -> blocked
        Assignment(date=DAY, shift=Shift("0600h", "RAH A side"), physician_id="Free", physician_name="Free"),  # the slot being replaced
    ])
    server._state.update(result=result, year=YEAR, month=MONTH, submissions=subs, roster=roster,
                         generator=None, scheduler_config={})

    resp = server.get_candidates(DAY.isoformat(), "0600h RAH A side", include_all=True)
    by = {c.physician_id: c for c in resp.candidates}

    assert set(by) == {"Free", "Unavail", "Busy"}                       # nobody hidden
    assert by["Free"].is_hard_blocked is False                           # current occupant, slot vacated for the check
    assert by["Unavail"].is_hard_blocked and any(v.rule in ("availability", "shift_not_available") for v in by["Unavail"].violations)
    assert by["Busy"].is_hard_blocked and any(v.rule == "already_assigned_today" for v in by["Busy"].violations)
    # Ordering: clean first, blocked last.
    assert [c.physician_id for c in resp.candidates][0] == "Free"
    assert all(c.is_hard_blocked for c in resp.candidates[-2:])

    # Default behaviour unchanged: blocked physicians excluded.
    default = server.get_candidates(DAY.isoformat(), "0600h RAH A side")
    assert all(not c.is_hard_blocked for c in default.candidates)
    # The check must leave the schedule untouched.
    assert server._state["result"].assignments[1].physician_id == "Free"
