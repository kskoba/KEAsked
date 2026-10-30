"""
0/0 anchor floor vs. stated requesters (real case: P57, cpsatv2-jan4/5/6).

P57 (10 shifts, explicit 0/0) offered 2400h on five weekend dates only.
January's 120 night slots carried 118 stated 2400h requests and the floor
was solved only in its own stage AFTER stage B had locked every
requester's slots, so his first night needed a multi-swap chain the 60 s
floor pass never found (FEASIBLE, unproven, in a local replay; forcing
P57 >= 1 in that stage with 300 s did find a schedule). Three runs in a
row left him at 0 of 2 while P51 and P92 held a 4th night against 3
requested.

User rule (2026-10-10): a floor physician NEVER displaces a stated
2400h/0600h request -- "floor physicians should only be given a shift to
avoid making others go over their requested number of anchor shifts".
So the floor's first unit was decided inside stage B at a weight below
every stated unit.

REVERSED 2026-10-29 (item 8 of the jan6 feedback round, feature
anchor_floor_as_request, on by default): the Oct 12 January run still
left P57 at 0 of 2 while three nights went out as overage, because every
slot on his five offered dates was held by someone exactly at their
stated request. The floor is now treated as a stated request of 1-2
anchors (either type) at the same standing as everyone else's units, so a
requester may finish one night short. The first test pins the new
behaviour and, with the flag off, the old one; the second (the floor takes
what would otherwise be overage) holds either way.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES

YEAR, MONTH = 2027, 1
DAYS = calendar.monthrange(YEAR, MONTH)[1]
NON_NIGHT = frozenset(c for c in ALL_SHIFT_CODES if not c.startswith("2400h") and not c.startswith("0600h"))
NIGHT = frozenset(c for c in ALL_SHIFT_CODES if c.startswith("2400h"))


def _sub(pid: str, shifts_requested: int, night_days: set[int], **kwargs) -> PhysicianSubmission:
    """Every day offered for day/evening shifts; 2400h offered only on night_days."""
    days = [
        DayAvailability(
            date=datetime.date(YEAR, MONTH, d), wants_to_work=True,
            available_blocks=frozenset(range(5)),
            requested_shifts=NON_NIGHT | (NIGHT if d in night_days else frozenset()),
        )
        for d in range(1, DAYS + 1)
    ]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=YEAR, month=MONTH,
                               shifts_requested=shifts_requested, shifts_min=0,
                               shifts_max=shifts_requested, days=days, **kwargs)


def _zz(night_days: set[int]) -> PhysicianSubmission:
    return _sub("ZeroZero", 10, night_days,
                shifts_2400h_stated=True, shifts_2400h_requested=0,
                shifts_0600h_stated=True, shifts_0600h_requested=0)


def _nights(result, pid: str) -> int:
    return sum(1 for a in result.assignments if a.physician_id == pid and a.shift.time == "2400h")


def _solve(subs, config: dict | None = None):
    # Requesters may work a lone night (P08-style roster flag) so the
    # fixture's single-night days are legal for them; a floor-eligible
    # physician is isolation-exempt already.
    roster = {s.physician_id: PhysicianConfig(id=s.physician_id, name=s.physician_id,
                                              allow_isolated_nights=True) for s in subs}
    gen = CpsatScheduleGenerator(subs, roster, config or {}, trailing_assignments=None)
    return gen.generate(YEAR, MONTH, time_limit=40.0, num_workers=4)


def test_floor_competes_as_a_stated_request_when_a_slot_is_contested():
    """Four requesters each want 2 nights and can only work day 10 and day
    20; ZeroZero can only work nights on day 10 (4 slots, 5 claimants).
    New rule: the floor's unit has the same standing as a stated unit, so
    stage A (everyone's first) puts ZeroZero on day 10 and one requester
    finishes a night short. With the feature off, the 2026-10-10 rule
    holds: every requester keeps both nights and the floor gets nothing."""
    requesters = [_sub(f"Req{i}", 6, {10, 20}, shifts_2400h_stated=True, shifts_2400h_requested=2)
                  for i in range(4)]
    result = _solve([_zz({10})] + requesters)
    assert _nights(result, "ZeroZero") == 1
    assert sorted(_nights(result, r.physician_id) for r in requesters) == [1, 2, 2, 2]

    old = _solve([_zz({10})] + requesters, config={"features": {"anchor_floor_as_request": False}})
    assert [_nights(old, r.physician_id) for r in requesters] == [2, 2, 2, 2]
    assert _nights(old, "ZeroZero") == 0


def test_floor_takes_the_night_that_would_otherwise_be_someones_overage():
    """Same shape, but the requesters asked for ONE night each: day 20
    covers every stated unit, so day 10's four slots can only be filled by
    overage (a 2nd, unrequested night) or by the floor physician. The
    floor's first unit must win one of them -- the P57 case: P51 and
    P92 held a 4th night for 3 requested while he had none."""
    requesters = [_sub(f"Req{i}", 6, {10, 20}, shifts_2400h_stated=True, shifts_2400h_requested=1)
                  for i in range(4)]
    result = _solve([_zz({10})] + requesters)

    assert _nights(result, "ZeroZero") == 1
    assert all(_nights(result, r.physician_id) >= 1 for r in requesters)
