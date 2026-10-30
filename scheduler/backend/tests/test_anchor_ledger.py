"""
Anchor-overage ledger (item 5 of the 2026-10-29 jan6 feedback round,
features anchor_ledger / anchor_overage_redirect): who worked more 2400h or
0600h than they stated is remembered for three months, and an unrequested
anchor of that type then costs them 450 instead of 150 (or 60 for a
night person), so the same physicians don't absorb the overage every month.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend import anchor_ledger
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, Shift

YEAR, MONTH = 2027, 1
DAYS = calendar.monthrange(YEAR, MONTH)[1]
DAY_CODES = frozenset(c for c in ALL_SHIFT_CODES if not c.startswith(("2400h", "0600h")))


def _sub(pid: str, year: int, month: int, requested: int, nights: int | None = None, earlies: int | None = None,
         codes_by_day: dict[int, frozenset] | None = None) -> PhysicianSubmission:
    n = calendar.monthrange(year, month)[1]
    days = [DayAvailability(date=datetime.date(year, month, d), wants_to_work=True,
                            available_blocks=frozenset(range(5)),
                            requested_shifts=(codes_by_day or {}).get(d, frozenset(ALL_SHIFT_CODES)))
            for d in range(1, n + 1)]
    return PhysicianSubmission(physician_id=pid, physician_name=pid, year=year, month=month,
                               shifts_requested=requested, shifts_min=0, shifts_max=requested, days=days,
                               shifts_2400h_stated=nights is not None, shifts_2400h_requested=nights or 0,
                               shifts_0600h_stated=earlies is not None, shifts_0600h_requested=earlies or 0)


def _result(year, month, nights: dict[str, list[int]]) -> ScheduleResult:
    assignments = [Assignment(date=datetime.date(year, month, d), shift=Shift("2400h", "NEHC"),
                              physician_id=pid, physician_name=pid) for pid, ds in nights.items() for d in ds]
    return ScheduleResult(year=year, month=month, assignments=assignments)


def test_record_and_recall_within_three_months(tmp_path):
    path = tmp_path / "ledger.yaml"
    subs = [_sub("A", 2026, 12, 8, nights=3), _sub("B", 2026, 12, 8, nights=3), _sub("C", 2026, 12, 8)]
    written = anchor_ledger.record_month(_result(2026, 12, {"A": [2, 3, 9, 10], "B": [5, 6, 7], "C": [12, 13]}), subs, path)
    assert [(e["physician_id"], e["type"], e["over"]) for e in written] == [("A", "2400h", 1)]   # B at request, C blank
    assert anchor_ledger.recent_overage(2027, 1, path=path) == {"A": {"2400h"}}
    assert anchor_ledger.recent_overage(2027, 3, path=path) == {"A": {"2400h"}}     # 3 months back still counts
    assert anchor_ledger.recent_overage(2027, 4, path=path) == {}                   # expired
    assert anchor_ledger.recent_overage(2026, 12, path=path) == {}                  # the month itself never counts for itself


def test_re_recording_a_month_replaces_its_entries(tmp_path):
    path = tmp_path / "ledger.yaml"
    subs = [_sub("A", 2026, 12, 8, nights=3)]
    anchor_ledger.record_month(_result(2026, 12, {"A": [2, 3, 9, 10]}), subs, path)
    anchor_ledger.record_month(_result(2026, 12, {"A": [2, 3, 9]}), subs, path)     # corrected schedule: no overage
    assert anchor_ledger.load(path) == []
    anchor_ledger.record_month(_result(2026, 11, {"A": [2, 3, 9, 10]}), [_sub("A", 2026, 11, 8, nights=3)], path)
    anchor_ledger.record_month(_result(2026, 12, {"A": [2, 3, 9, 10]}), subs, path)
    assert sorted((e["year"], e["month"]) for e in anchor_ledger.load(path)) == [(2026, 11), (2026, 12)]


def test_ledger_steers_an_unrequested_night_to_the_physician_not_on_it():
    """Two identical physicians, blank anchor cells, one shift each; the
    only thing on offer is a single 2400h NEHC slot on day 10 (plus a day
    shift on day 20 for whoever misses it). The one carried over on the
    ledger for 2400h must not be the one who takes the night."""
    offer = {10: frozenset({"2400h NEHC"}), 20: frozenset({"1200h NEHC", "1200h RAH A side"})}
    subs = [_sub("OnLedger", YEAR, MONTH, 1, codes_by_day=offer), _sub("Clean", YEAR, MONTH, 1, codes_by_day=offer)]
    for s in subs:   # only days 10 and 20 are offered at all
        for d in s.days:
            if d.date.day not in offer:
                d.wants_to_work = False
                d.requested_shifts = frozenset()
                d.available_blocks = frozenset()
    roster = {s.physician_id: PhysicianConfig(id=s.physician_id, name=s.physician_id, max_consecutive_nights=1)
              for s in subs}
    gen = CpsatScheduleGenerator(subs, roster, {}, anchor_ledger={"OnLedger": {"2400h"}})
    result = gen.generate(YEAR, MONTH, time_limit=20.0, num_workers=4)
    nights = {a.physician_id for a in result.assignments if a.shift.time == "2400h"}
    assert nights == {"Clean"}

    gen2 = CpsatScheduleGenerator(subs, roster, {}, anchor_ledger={"Clean": {"2400h"}})
    result2 = gen2.generate(YEAR, MONTH, time_limit=20.0, num_workers=4)
    assert {a.physician_id for a in result2.assignments if a.shift.time == "2400h"} == {"OnLedger"}


def test_overage_lands_below_share_before_anchor_heavy():
    """Six night slots (one per day) and only two physicians can take
    them. Heavy stated 4 nights and 0 0600h for 10 shifts (target 4);
    Light stated 1 night and 1 0600h for 10 shifts (fair share 4, two
    below). Five slots are stated units; the sixth is overage: Heavy's 5th
    night is a beyond-target step (500 + 60), Light's 2nd a within-share
    unit at the below-share rate (90), so Light takes it."""
    night_days = {4, 8, 12, 16, 20, 24}
    offer = {d: (frozenset({"2400h RAH B side"}) | DAY_CODES) if d in night_days else DAY_CODES for d in range(1, DAYS + 1)}
    heavy = _sub("Heavy", YEAR, MONTH, 10, nights=4, earlies=0, codes_by_day=offer)
    light = _sub("Light", YEAR, MONTH, 10, nights=1, earlies=1, codes_by_day=offer)
    roster = {p: PhysicianConfig(id=p, name=p, max_consecutive_nights=1, allow_isolated_nights=True, night_ramp_in=False)
              for p in ("Heavy", "Light")}
    gen = CpsatScheduleGenerator([heavy, light], roster, {})
    result = gen.generate(YEAR, MONTH, time_limit=30.0, num_workers=4)
    nights = {p: sum(1 for a in result.assignments if a.physician_id == p and a.shift.time == "2400h") for p in ("Heavy", "Light")}
    assert nights == {"Heavy": 4, "Light": 2}
