"""
Spill-over days: the department's master sheet staffs the partial last week
row of each month (the December 2026 sheet staffs Jan 1), and by convention
those days belong to THAT month. So, for January:

  * the December sheet's Jan 1 column is parsed as `spillover`, not dropped
    and not mixed into December's own assignments;
  * January is solved from Jan 2 (start_day), with Jan 1 enforced as an
    already-worked day through trailing_assignments;
  * no on-call is assigned on Jan 1 by the January solve;
  * spill-over shifts count toward December's whole-month tallies.
"""

from __future__ import annotations

import calendar
import datetime

from scheduler.backend import sheets_schedule_reader as r
from scheduler.backend import trailing as trailing_mod
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import ALL_SHIFT_CODES, EXPORT_SHIFTS, Shift

DEC = (2026, 12)
JAN = (2027, 1)
ROSTER = {p: PhysicianConfig(id=p, name=p) for p in ("Alpha", "Beta", "Gamma")}


def _week_block(header_dates: list, names_by_row: dict[tuple[str, str], list]) -> list[list]:
    rows = [[None, "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"], [None, *header_dates]]
    for site_label, time_label, _tc, _sc in EXPORT_SHIFTS:
        names = names_by_row.get((site_label, time_label), [None] * 7)
        rows.append([site_label, *names])
        rows.append([time_label, *([None] * 7)])
    return rows


# --------------------------------------------------------------------------- #
# Reader
# --------------------------------------------------------------------------- #

def test_december_sheet_jan_1_column_is_parsed_as_spillover_with_the_right_year():
    # Real December 2026 last-week row: 27..31 then a datetime carrying the WRONG year, then blank.
    last_week = _week_block(
        [27, 28, 29, 30, 31, datetime.datetime(2026, 1, 1), None],
        {("RAH A", "0600-1200"): [None, None, None, None, "Alpha", "Beta", None],
         ("DOC", "0500-1559"): [None, None, None, None, None, "Gamma", None]},
    )
    res = r.parse_schedule_grid(last_week, *DEC, ROSTER)

    assert [(a.physician_id, a.date) for a in res.assignments] == [("Alpha", datetime.date(2026, 12, 31))]
    assert [(a.physician_id, a.date, a.shift.code) for a in res.spillover] == [("Beta", datetime.date(2027, 1, 1), "0600h RAH A side")]
    assert [(o.physician_id, o.date, o.call_type) for o in res.spillover_on_calls] == [("Gamma", datetime.date(2027, 1, 1), "DOC")]
    # No phantom unfilled slots for the spill-over day, and nothing for the blank Saturday (Jan 2).
    assert all(u.date.month == 12 for u in res.unfilled)


def test_leading_previous_month_cells_are_ignored():
    # December's first week: Sun Nov 29 / Mon Nov 30 blank in the real sheet; if someone filled them, drop them.
    first_week = _week_block([None, None, 1, 2, 3, 4, 5],
                             {("RAH A", "0600-1200"): ["Alpha", None, "Beta", None, None, None, None]})
    res = r.parse_schedule_grid(first_week, *DEC, ROSTER)
    assert [(a.physician_id, a.date.day) for a in res.assignments] == [("Beta", 1)]
    assert res.spillover == []


# --------------------------------------------------------------------------- #
# Trailing / start day
# --------------------------------------------------------------------------- #

def _dec_result(spill: list[tuple[str, int, Shift]], dec: list[tuple[str, int, Shift]] = ()) -> ScheduleResult:
    return ScheduleResult(
        year=2026, month=12,
        assignments=[Assignment(date=datetime.date(2026, 12, d), shift=s, physician_id=p, physician_name=p) for p, d, s in dec],
        spillover=[Assignment(date=datetime.date(2027, 1, d), shift=s, physician_id=p, physician_name=p) for p, d, s in spill],
    )


def test_start_day_follows_contiguous_staffed_spillover_days():
    night = Shift("2400h", "NEHC")
    assert trailing_mod.spillover_start_day(_dec_result([("Alpha", 1, night)]), *JAN) == 2
    assert trailing_mod.spillover_start_day(_dec_result([]), *JAN) == 1
    assert trailing_mod.spillover_start_day(_dec_result([("Alpha", 2, night)]), *JAN) == 1   # not contiguous from the 1st


def test_trailing_window_includes_spillover_day_when_solving_from_jan_2():
    night = Shift("2400h", "NEHC")
    prior = _dec_result([("Alpha", 1, night)], dec=[("Alpha", 31, night)])
    out = trailing_mod.build_trailing_assignments(prior, ROSTER, *JAN, first_day=datetime.date(2027, 1, 2))
    assert [d for d, _ in out["Alpha"]] == [datetime.date(2026, 12, 31), datetime.date(2027, 1, 1)]
    # Default (solving from the 1st) must NOT include Jan 1 -- it would then be a solve day.
    out_default = trailing_mod.build_trailing_assignments(prior, ROSTER, *JAN)
    assert [d for d, _ in out_default["Alpha"]] == [datetime.date(2026, 12, 31)]


def test_spillover_counts_toward_decembers_tallies():
    acute = Shift("1200h", "RAH A side")
    prior = _dec_result([("Alpha", 1, acute)], dec=[("Alpha", 30, acute)])
    summ = trailing_mod.build_prior_month_summaries(prior)
    assert summ["Alpha"].shifts_worked == 2 and summ["Alpha"].acute_shifts == 2


# --------------------------------------------------------------------------- #
# Solver: start_day + Jan 1 enforced as worked
# --------------------------------------------------------------------------- #

def _jan_submission(pid: str, days: set[int], codes=None) -> PhysicianSubmission:
    codes = frozenset(codes) if codes else frozenset(ALL_SHIFT_CODES)
    return PhysicianSubmission(
        physician_id=pid, physician_name=pid, year=2027, month=1,
        shifts_requested=1, shifts_min=0, shifts_max=1,
        days=[DayAvailability(date=datetime.date(2027, 1, d), wants_to_work=(d in days),
                              available_blocks=frozenset(range(5)) if d in days else frozenset(),
                              requested_shifts=codes if d in days else frozenset())
              for d in range(1, calendar.monthrange(2027, 1)[1] + 1)],
    )


def test_solving_from_jan_2_creates_no_jan_1_slots():
    gen = CpsatScheduleGenerator([_jan_submission("Alpha", {1, 2})], {"Alpha": ROSTER["Alpha"]}, {})
    res = gen.generate(2027, 1, time_limit=10.0, num_workers=4, start_day=2)
    assert all(a.date.day >= 2 for a in res.assignments)
    assert all(u.date.day >= 2 for u in res.unfilled)
    assert len(res.unfilled) + len(res.assignments) == 30 * 21        # 30 days of slots, not 31


def test_jan_1_night_from_the_december_sheet_blocks_a_jan_2_0600h():
    """Alpha worked the Jan 1 night on the December sheet. Solving from Jan 2
    with that as trailing data, a Jan 2 0600h (an 6h turnaround) is refused;
    a Jan 2 night (completing the pair) is allowed."""
    cfg = PhysicianConfig(id="Alpha", name="Alpha", max_consecutive_nights=2)
    trailing = {"Alpha": [(datetime.date(2027, 1, 1), Shift("2400h", "NEHC"))]}

    gen = CpsatScheduleGenerator([_jan_submission("Alpha", {2}, {"0600h NEHC"})], {"Alpha": cfg}, {},
                                 trailing_assignments=trailing)
    res = gen.generate(2027, 1, time_limit=10.0, num_workers=4, start_day=2)
    assert [a for a in res.assignments if a.physician_id == "Alpha"] == []

    gen = CpsatScheduleGenerator([_jan_submission("Alpha", {2}, {"2400h NEHC"})], {"Alpha": cfg}, {},
                                 trailing_assignments=trailing)
    res = gen.generate(2027, 1, time_limit=10.0, num_workers=4, start_day=2)
    assert [(a.date.day, a.shift.code) for a in res.assignments if a.physician_id == "Alpha"] == [(2, "2400h NEHC")]


def test_on_call_is_not_assigned_on_an_unscheduled_leading_day():
    from scheduler.backend.generator import ScheduleGenerator
    sub = _jan_submission("Alpha", {1, 2, 3})
    for d in sub.days:
        d.doc_available = d.date.day == 1          # Jan 1 is the ONLY call day offered
    gen = ScheduleGenerator([sub], {"Alpha": ROSTER["Alpha"]}, {})
    # A January result solved from Jan 2: slots exist on Jan 2.., none on Jan 1.
    res = ScheduleResult(year=2027, month=1, assignments=[
        Assignment(date=datetime.date(2027, 1, 3), shift=Shift("1200h", "NEHC"), physician_id="Alpha", physician_name="Alpha")])
    from scheduler.backend.generator import UnfilledSlot
    res.unfilled = [UnfilledSlot(date=datetime.date(2027, 1, 2), shift=Shift("1200h", "NEHC"), candidates=[])]
    res = gen.assign_on_calls(res)
    # Before the guard this produced a Jan 1 DOC; Jan 1 belongs to December's sheet.
    assert res.on_calls == []


# --------------------------------------------------------------------------- #
# Scheduling the spill-over day WITH its month (a December solve staffs Jan 1)
# --------------------------------------------------------------------------- #

def _dec_submission(pid: str, days: set[int], jan1: bool, codes=None) -> PhysicianSubmission:
    codes = frozenset(codes) if codes else frozenset(ALL_SHIFT_CODES)
    sub = PhysicianSubmission(
        physician_id=pid, physician_name=pid, year=2026, month=12,
        shifts_requested=2, shifts_min=0, shifts_max=2,
        days=[DayAvailability(date=datetime.date(2026, 12, d), wants_to_work=(d in days),
                              available_blocks=frozenset(range(5)) if d in days else frozenset(),
                              requested_shifts=codes if d in days else frozenset())
              for d in range(1, 32)],
    )
    if jan1:
        sub.spillover_days = [DayAvailability(date=datetime.date(2027, 1, 1), wants_to_work=True,
                                              available_blocks=frozenset(range(5)), requested_shifts=codes)]
    return sub


def test_december_solve_schedules_jan_1_from_the_sheets_jan_1_column():
    # Only Alpha offered Jan 1 (via the December template's trailing column).
    subs = [_dec_submission("Alpha", {30}, jan1=True, codes={"1200h NEHC"}),
            _dec_submission("Beta", {30}, jan1=False, codes={"1200h NEHC"})]
    gen = CpsatScheduleGenerator(subs, {p: ROSTER[p] for p in ("Alpha", "Beta")}, {})
    res = gen.generate(2026, 12, time_limit=10.0, num_workers=4)

    jan1 = [a for a in res.assignments if a.date == datetime.date(2027, 1, 1)]
    assert [a.physician_id for a in jan1] == ["Alpha"]
    assert any(u.date == datetime.date(2027, 1, 1) for u in res.unfilled)          # Jan 1 slots exist
    assert len(res.unfilled) + len(res.assignments) == 32 * 21                      # 31 Dec days + Jan 1
    assert (res.year, res.month) == (2026, 12)


def test_december_solve_without_spillover_flag_stays_within_december():
    subs = [_dec_submission("Alpha", {30}, jan1=True, codes={"1200h NEHC"})]
    gen = CpsatScheduleGenerator(subs, {"Alpha": ROSTER["Alpha"]}, {})
    res = gen.generate(2026, 12, time_limit=10.0, num_workers=4, include_spillover=False)
    assert all(a.date.month == 12 for a in res.assignments) and all(u.date.month == 12 for u in res.unfilled)
    assert len(res.unfilled) + len(res.assignments) == 31 * 21


def test_export_round_trip_keeps_jan_1_as_spillover_dated_2027(tmp_path):
    from scheduler.api import server
    res = ScheduleResult(year=2026, month=12, assignments=[
        Assignment(date=datetime.date(2026, 12, 31), shift=Shift("1200h", "NEHC"), physician_id="Alpha", physician_name="Alpha"),
        Assignment(date=datetime.date(2027, 1, 1), shift=Shift("1200h", "NEHC"), physician_id="Beta", physician_name="Beta"),
    ])
    path = tmp_path / "schedule_2026_12.xlsx"
    server._build_export_workbook(res).save(path)
    back = server._parse_schedule_xlsx(path, ROSTER)

    assert [(a.physician_id, a.date) for a in back.assignments] == [("Alpha", datetime.date(2026, 12, 31))]
    assert [(a.physician_id, a.date) for a in back.spillover] == [("Beta", datetime.date(2027, 1, 1))]
    assert all(u.date.month == 12 for u in back.unfilled)
    # And a solver-made December (Jan 1 in assignments) moves January's start day too.
    assert trailing_mod.spillover_start_day(res, 2027, 1) == 2
    assert trailing_mod.spillover_start_day(back, 2027, 1) == 2


def test_on_call_is_assigned_on_a_scheduled_spillover_day():
    from scheduler.backend.generator import ScheduleGenerator, UnfilledSlot
    sub = _dec_submission("Alpha", {30}, jan1=True)
    sub.spillover_days[0].doc_available = True
    gen = ScheduleGenerator([sub], {"Alpha": ROSTER["Alpha"]}, {})
    res = ScheduleResult(year=2026, month=12,
                         unfilled=[UnfilledSlot(date=datetime.date(2027, 1, 1), shift=Shift("1200h", "NEHC"), candidates=[])])
    res = gen.assign_on_calls(res)
    assert [(oc.physician_id, oc.date, oc.call_type) for oc in res.on_calls] == [("Alpha", datetime.date(2027, 1, 1), "DOC")]


def test_november_solve_schedules_dec_1_the_same_way():
    """November's template carries Dec 1; the same mechanism extends November to Nov 1..Dec 1."""
    codes = frozenset({"1200h NEHC"})
    sub = PhysicianSubmission(
        physician_id="Alpha", physician_name="Alpha", year=2026, month=11,
        shifts_requested=2, shifts_min=0, shifts_max=2,
        # Nov 29, not 30: the identical shift code on two ADJACENT days is a hard rule (HC-10).
        days=[DayAvailability(date=datetime.date(2026, 11, d), wants_to_work=(d == 29),
                              available_blocks=frozenset(range(5)) if d == 29 else frozenset(),
                              requested_shifts=codes if d == 29 else frozenset()) for d in range(1, 31)],
    )
    sub.spillover_days = [DayAvailability(date=datetime.date(2026, 12, 1), wants_to_work=True,
                                          available_blocks=frozenset(range(5)), requested_shifts=codes)]
    gen = CpsatScheduleGenerator([sub], {"Alpha": ROSTER["Alpha"]}, {})
    res = gen.generate(2026, 11, time_limit=10.0, num_workers=4)
    assert sorted(a.date for a in res.assignments if a.physician_id == "Alpha") == [datetime.date(2026, 11, 29), datetime.date(2026, 12, 1)]
    assert len(res.unfilled) + len(res.assignments) == 31 * 21     # 30 Nov days + Dec 1
    assert (res.year, res.month) == (2026, 11)
