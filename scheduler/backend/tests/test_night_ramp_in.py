"""
Tests for PhysicianConfig.night_ramp_in (soft, Roster Editor toggle, ON by
default): don't start a run of 2400h shifts cold off a day off -- want a
shift the day before. Added 2026-10-28 when the rule moved out of
scheduler_config.yaml's night_chain_ramp_in list (P92) onto the roster,
for P24 ("one midnight at a time, never isolated"), then made the default
(user: most physicians prefer a ramp-in).

Solo/uncontested synthetic model: with max_consecutive_nights=1 every
night is a run start, so every night should have a shift the day before.
2400h is only offered on a few fixed days: in a solo solve every slot is
empty and a 2400h slot pays +300 over a day slot (_FILL_WEIGHT_2400H), so
with unconstrained availability the solver would spend the last shift of
the request on an extra cold night rather than a day shift -- a solo-model
artifact (in a real roster someone else fills that night), not what this
rule is about.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES

YEAR, MONTH = 2027, 1
DAYS_IN_MONTH = calendar.monthrange(YEAR, MONTH)[1]
NIGHT_DAYS = (10, 17, 24)
NON_NIGHT_CODES = frozenset(c for c in ALL_SHIFT_CODES if not c.startswith("2400h"))


def _submission(pid: str, shifts: int, nights: int,
                night_days: tuple[int, ...] = NIGHT_DAYS) -> PhysicianSubmission:
    """Every shift offered every day, except 2400h only on night_days."""
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d),
            wants_to_work=True,
            available_blocks=frozenset(range(5)),
            requested_shifts=frozenset(ALL_SHIFT_CODES) if d in night_days else NON_NIGHT_CODES,
        )
        for d in range(1, DAYS_IN_MONTH + 1)
    ]
    return PhysicianSubmission(
        physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
        shifts_requested=shifts, shifts_min=0, shifts_max=shifts,
        shifts_2400h_stated=True, shifts_2400h_requested=nights,
        days=days,
    )


def _by_date(result, pid: str) -> dict[datetime.date, str]:
    return {a.date: a.shift.time for a in result.assignments if a.physician_id == pid}


def _night_dates(sched: dict) -> list[datetime.date]:
    return sorted(d for d, t in sched.items() if t == "2400h")


def _cfg(pid: str, ramp: bool) -> PhysicianConfig:
    return PhysicianConfig(id=pid, name=pid, max_consecutive_nights=1,
                           allow_isolated_nights=True, night_ramp_in=ramp)


def test_night_ramp_in_puts_a_shift_before_every_single_night():
    sub = _submission("P24", 10, 3)
    gen = CpsatScheduleGenerator([sub], {"P24": _cfg("P24", True)}, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=20.0, num_workers=4)
    sched = _by_date(result, "P24")
    nights = _night_dates(sched)
    assert len(nights) == 3
    for d in nights:
        assert sched.get(d - datetime.timedelta(days=1)) is not None, f"{d}: cold midnight"


def test_toggle_off_leaves_the_physician_free_to_take_cold_midnights():
    """Control: same model without the toggle. Nothing forces a day-before
    shift, so with the three night days spread a week apart and 10 shifts
    to place, at least one night may come straight off a day off -- but
    the real check is that the term isn't applied: the solve must still
    deliver the nights and must not be *required* to ramp."""
    sub = _submission("Other", 4, 3)   # 4 shifts for 3 nights: ramping all 3 is impossible
    gen = CpsatScheduleGenerator([sub], {"Other": _cfg("Other", False)}, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=20.0, num_workers=4)
    sched = _by_date(result, "Other")
    assert len(_night_dates(sched)) == 3


def test_toggle_only_applies_to_that_physician():
    """Two physicians in one solve: only the toggled one must ramp. The
    other asks for a single shift, a night -- a ramp-in would need a 2nd
    shift he didn't ask for, so if the term leaked he'd lose the night or
    gain a shift."""
    sub_a = _submission("P24", 10, 3)
    sub_b = _submission("Other", 1, 1, night_days=(5,))
    roster = {"P24": _cfg("P24", True), "Other": _cfg("Other", False)}
    gen = CpsatScheduleGenerator([sub_a, sub_b], roster, {}, trailing_assignments=None)
    result = gen.generate(YEAR, MONTH, time_limit=20.0, num_workers=4)
    sched_a = _by_date(result, "P24")
    for d in _night_dates(sched_a):
        assert sched_a.get(d - datetime.timedelta(days=1)) is not None, f"{d}: cold midnight"
    assert list(_by_date(result, "Other").values()) == ["2400h"]


def test_roster_round_trip_and_update_allowlist():
    """Default is ON; only an explicit False is written back to yaml. The
    Roster Editor's PUT rebuild includes the field (the field-allowlist bug
    class that silently wiped fields before)."""
    from scheduler.backend.config import physician_config_to_raw, _parse_physician
    assert _parse_physician({"id": "X", "name": "X", "scheduling": {}}).night_ramp_in is True
    off = _parse_physician({"id": "X", "name": "X", "scheduling": {"night_ramp_in": False}})
    assert off.night_ramp_in is False
    assert physician_config_to_raw(off)["scheduling"]["night_ramp_in"] is False
    on = _parse_physician({"id": "X", "name": "X", "scheduling": {}})
    assert "night_ramp_in" not in physician_config_to_raw(on)["scheduling"]
    import inspect
    from scheduler.api import server
    src = inspect.getsource(server.update_physician)
    assert "night_ramp_in=body.night_ramp_in" in src
