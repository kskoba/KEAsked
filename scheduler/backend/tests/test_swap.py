"""
Manual swap of two filled slots (POST /api/swap).

Background (user-reported bug, 2026-10-07): the Electron swap flow used to be
composed client-side out of two one-sided /api/check-violations calls and two
sequential /api/assign calls. Each of those only vacates the *target slot's*
occupant, never the moving physician's own shift -- so when both physicians
work the SAME day (A on "0600h RAH A side", B on "0600h RAH B side") the
pre-check reported "Already has a shift on <date>" for both, and the first
/api/assign hard-400'd with "already has a shift on this day". The swap was
impossible even though the post-swap schedule is perfectly legal.

/api/swap evaluates the exchange as a whole: both physicians are lifted out of
their current slots first, then each is checked (and placed) in the other's
slot, so violations reflect the final state only. Genuine conflicts -- a
physician who has a *third* shift on the destination day that is not part of
the swap, or a 2400h -> 0600h spacing clash created by the swap -- must still
be reported.
"""
from __future__ import annotations

import calendar
import datetime

import pytest

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, Shift

YEAR, MONTH = 2027, 1
A_0600 = Shift("0600h", "RAH A side")
B_0600 = Shift("0600h", "RAH B side")
I_1000 = Shift("1000h", "RAH I side")
A_2400 = Shift("2400h", "RAH A side")


def _d(day: int) -> datetime.date:
    return datetime.date(YEAR, MONTH, day)


def _sub(pid: str, n: int = 10) -> PhysicianSubmission:
    days_in_month = calendar.monthrange(YEAR, MONTH)[1]
    return PhysicianSubmission(
        physician_id=pid, physician_name=f"Dr {pid}", year=YEAR, month=MONTH,
        shifts_requested=n, shifts_min=0, shifts_max=n,
        days=[
            DayAvailability(
                date=_d(d), wants_to_work=True,
                available_blocks=frozenset(range(5)),
                requested_shifts=frozenset(ALL_SHIFT_CODES),
            )
            for d in range(1, days_in_month + 1)
        ],
    )


def _result(assignments: list[tuple[str, int, Shift]]) -> ScheduleResult:
    return ScheduleResult(
        year=YEAR, month=MONTH,
        assignments=[
            Assignment(date=_d(d), shift=sh, physician_id=pid, physician_name=f"Dr {pid}")
            for pid, d, sh in assignments
        ],
    )


@pytest.fixture
def server(monkeypatch):
    from scheduler.api import server as server_mod

    keys = ("result", "year", "month", "submissions", "roster", "generator", "scheduler_config")
    saved = {k: server_mod._state[k] for k in keys}
    monkeypatch.setattr(server_mod.google_sheets_client, "load_google_sheets_config", lambda: None)
    yield server_mod
    server_mod._state.update(saved)


def _load(server, assignments: list[tuple[str, int, Shift]], pids=("A", "B")) -> ScheduleResult:
    """Make `assignments` the active schedule; the generator is rebuilt lazily by _require_generator."""
    result = _result(assignments)
    server._state.update({
        "result": result, "year": YEAR, "month": MONTH,
        "roster": {pid: PhysicianConfig(id=pid, name=f"Dr {pid}") for pid in pids},
        "submissions": [_sub(pid) for pid in pids],
        "scheduler_config": {},
        "generator": None,
    })
    return result


def _swap(server, a: tuple[int, Shift], b: tuple[int, Shift], dry_run: bool = False):
    from scheduler.api.schemas import SwapRequest, SwapSlotRef
    return server.swap_assignments(SwapRequest(
        a=SwapSlotRef(date=_d(a[0]).isoformat(), shift_code=a[1].code),
        b=SwapSlotRef(date=_d(b[0]).isoformat(), shift_code=b[1].code),
        dry_run=dry_run,
    ))


def _who(result: ScheduleResult, day: int, shift: Shift) -> Assignment:
    return next(a for a in result.assignments if a.date == _d(day) and a.shift.code == shift.code)


def _hard(side) -> list[str]:
    return [v.rule for v in side.violations if v.is_hard]


# --------------------------------------------------------------------------- #
# The reported case: two physicians exchanging shifts on the same day
# --------------------------------------------------------------------------- #

def test_same_day_a_side_b_side_swap_has_no_violations_and_exchanges_the_slots(server):
    result = _load(server, [("A", 10, A_0600), ("B", 10, B_0600)])

    resp = _swap(server, (10, A_0600), (10, B_0600))

    assert resp.success and resp.applied
    assert _hard(resp.a) == [] and _hard(resp.b) == []
    assert not any(v.rule == "already_assigned_today" for v in resp.a.violations + resp.b.violations)

    # Slots are exchanged in the active result ...
    assert _who(result, 10, A_0600).physician_id == "B"
    assert _who(result, 10, B_0600).physician_id == "A"
    assert _who(result, 10, A_0600).is_manual and _who(result, 10, B_0600).is_manual
    assert len(result.assignments) == 2
    # ... and in the generator's live state, so later checks see the swap.
    gen = server._state["generator"]
    assert gen._slot_to_pid[(_d(10), A_0600.code)] == "B"
    assert gen._slot_to_pid[(_d(10), B_0600.code)] == "A"
    assert gen._shift_count["A"] == 1 and gen._shift_count["B"] == 1
    assert [s.code for _, s in gen._pid_to_slots["A"]] == [B_0600.code]
    assert [s.code for _, s in gen._pid_to_slots["B"]] == [A_0600.code]


def test_dry_run_reports_the_same_clean_result_without_touching_anything(server):
    result = _load(server, [("A", 10, A_0600), ("B", 10, B_0600)])

    resp = _swap(server, (10, A_0600), (10, B_0600), dry_run=True)

    assert resp.success and not resp.applied
    assert _hard(resp.a) == [] and _hard(resp.b) == []
    assert _who(result, 10, A_0600).physician_id == "A"
    assert _who(result, 10, B_0600).physician_id == "B"
    gen = server._state["generator"]
    assert gen._slot_to_pid[(_d(10), A_0600.code)] == "A"
    assert gen._slot_to_pid[(_d(10), B_0600.code)] == "B"
    assert gen._shift_count["A"] == 1 and gen._shift_count["B"] == 1


def test_old_one_sided_check_would_have_flagged_double_booking(server):
    """Documents why the swap can't be composed from /api/check-violations:
    checking A into B's slot with A's own same-day shift still in place is a
    false double-booking."""
    from scheduler.api.schemas import ManualAssignRequest

    _load(server, [("A", 10, A_0600), ("B", 10, B_0600)])
    one_sided = server.check_violations(ManualAssignRequest(
        date=_d(10).isoformat(), shift_code=B_0600.code, physician_id="A",
    ))
    assert any(v.rule == "already_assigned_today" for v in one_sided["violations"])


# --------------------------------------------------------------------------- #
# Genuine conflicts must still be reported
# --------------------------------------------------------------------------- #

def test_third_shift_on_destination_day_still_warns(server):
    # A works Jan 10 0600h and ALSO Jan 12 1000h; B works Jan 12 0600h.
    # Swapping A's Jan 10 with B's Jan 12 puts A on two Jan 12 shifts.
    _load(server, [("A", 10, A_0600), ("A", 12, I_1000), ("B", 12, B_0600)])

    resp = _swap(server, (10, A_0600), (12, B_0600), dry_run=True)

    assert "already_assigned_today" in _hard(resp.a)
    assert resp.a.physician_id == "A" and resp.a.date == _d(12).isoformat()
    assert _hard(resp.b) == []          # B on Jan 10 is fine


def test_spacing_clash_created_by_the_swap_still_warns(server):
    # A: Jan 10 0600h + Jan 11 0600h. B: Jan 10 2400h.
    # After the swap A would work Jan 10 2400h then Jan 11 0600h -> spacing.
    _load(server, [("A", 10, A_0600), ("A", 11, A_0600), ("B", 10, A_2400)])

    resp = _swap(server, (10, A_0600), (10, A_2400), dry_run=True)

    assert any(r in ("spacing_23h", "post_late_shift_rest") for r in _hard(resp.a))
    assert "already_assigned_today" not in _hard(resp.a)
    assert _hard(resp.b) == []


def test_applied_swap_with_violations_is_still_applied_and_reported(server):
    result = _load(server, [("A", 10, A_0600), ("A", 12, I_1000), ("B", 12, B_0600)])

    resp = _swap(server, (10, A_0600), (12, B_0600))

    assert resp.applied
    assert "already_assigned_today" in _hard(resp.a)
    assert _who(result, 12, B_0600).physician_id == "A"
    assert _who(result, 10, A_0600).physician_id == "B"
    assert len(result.assignments) == 3


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #

def test_swap_rejects_empty_slot_same_slot_and_same_physician(server):
    from fastapi import HTTPException

    _load(server, [("A", 10, A_0600), ("A", 11, A_0600), ("B", 10, B_0600)])

    with pytest.raises(HTTPException) as exc:
        _swap(server, (10, A_0600), (20, B_0600))      # Jan 20 B side is unfilled
    assert exc.value.status_code == 400 and "unfilled" in exc.value.detail.lower()

    with pytest.raises(HTTPException) as exc:
        _swap(server, (10, A_0600), (10, A_0600))
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        _swap(server, (10, A_0600), (11, A_0600))      # both are A's
    assert exc.value.status_code == 400 and "same physician" in exc.value.detail.lower()
