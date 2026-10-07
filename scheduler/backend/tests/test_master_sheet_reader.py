"""
sheets_schedule_reader.parse_schedule_grid against the quirks of the REAL
department master sheet (confirmed on the December 2026 file, 2026-10-07),
which differ from the app's own tidy export:

  * no blank spacer row between weeks -- the next week's SUN header sits
    directly under the previous week's last shift row (the old parser
    swallowed it and silently dropped every other week);
  * a blank date cell on a worked day (Dec 1) and a datetime with the wrong
    year on the spill-over Jan 1 cell;
  * on-call rows labelled "AM CALL" / "PM CALL" rather than DOC / NOC;
  * the Float row's real hours, 1600-2400 (the app's export table used to
    say 1600-0459, the on-call end time -- corrected the same day; files
    exported with the old label must still load);
  * stray whitespace in labels ("NECHC ", "  1800-0000").
"""

from __future__ import annotations

import datetime

from scheduler.backend import sheets_schedule_reader as r
from scheduler.backend.config import PhysicianConfig
from scheduler.backend.shifts import EXPORT_SHIFTS, EXPORT_SHIFT_LOOKUP

YEAR, MONTH = 2026, 12
ROSTER = {p: PhysicianConfig(id=p, name=p) for p in ("Alpha", "Beta", "Gamma")}


def _week_block(header_dates: list, names_by_row: dict[tuple[str, str], list]) -> list[list]:
    """One week in the real sheet's layout: SUN header, date row, then (site row, time row) pairs, no spacer."""
    rows = [[None, "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"], [None, *header_dates]]
    real_labels = {("DOC", "0500-1559"): ("AM CALL", "0500-1559"),
                   ("NOC", "1600-0459"): ("PM CALL", "1600-0459")}
    for site_label, time_label, _tc, _sc in EXPORT_SHIFTS:
        site_label, time_label = real_labels.get((site_label, time_label), (site_label, time_label))
        names = names_by_row.get((site_label, time_label), [None] * 7)
        rows.append([site_label, *names])
        rows.append([time_label, *([None] * 7)])   # learner row -- never read
    return rows


def test_consecutive_weeks_without_spacer_rows_are_all_parsed():
    week1 = _week_block([None, None, 1, 2, 3, 4, 5], {("RAH A", "0600-1200"): [None, None, "Alpha", "Alpha", "Alpha", "Alpha", "Alpha"]})
    week2 = _week_block([6, 7, 8, 9, 10, 11, 12], {("RAH A", "0600-1200"): ["Beta"] * 7})
    week3 = _week_block([13, 14, 15, 16, 17, 18, 19], {("RAH A", "0600-1200"): ["Gamma"] * 7})
    res = r.parse_schedule_grid(week1 + week2 + week3, YEAR, MONTH, ROSTER)
    by_pid = {}
    for a in res.assignments:
        by_pid.setdefault(a.physician_id, []).append(a.date.day)
    assert sorted(by_pid["Alpha"]) == [1, 2, 3, 4, 5]
    assert sorted(by_pid["Beta"]) == [6, 7, 8, 9, 10, 11, 12]     # week 2 used to vanish
    assert sorted(by_pid["Gamma"]) == [13, 14, 15, 16, 17, 18, 19]


def test_blank_and_wrong_year_date_cells_are_inferred_from_neighbours():
    # Dec 1 2026 is a Tuesday: TUE cell blank, WED a float, THU a string, as in the real file.
    week1 = _week_block([None, None, None, 2.0, "3", 4, 5.0], {("RAH B", "1200-1800"): [None, None, "Alpha", None, None, None, None]})
    # Last week: Jan 1 spills over as a datetime carrying the wrong year; must be ignored, not crash.
    week5 = _week_block([27, 28, 29, 30, 31, datetime.datetime(2026, 1, 1), None],
                        {("RAH B", "1200-1800"): [None, None, None, None, "Beta", "Gamma", None]})
    res = r.parse_schedule_grid(week1 + week5, YEAR, MONTH, ROSTER)
    got = {(a.physician_id, a.date) for a in res.assignments}
    assert ("Alpha", datetime.date(2026, 12, 1)) in got          # blank TUE cell inferred as Dec 1
    assert ("Beta", datetime.date(2026, 12, 31)) in got
    assert not any(pid == "Gamma" for pid, _ in got)              # the Jan 1 column is outside the month


def test_real_sheet_labels_for_float_and_on_call_rows():
    names = {
        ("RAH Float", "1600-2400"): ["Alpha", None, None, None, None, None, None],
        ("AM CALL", "0500-1559"): ["Beta", None, None, None, None, None, None],
        ("PM CALL", "1600-0459"): ["Gamma", None, None, None, None, None, None],
    }
    res = r.parse_schedule_grid(_week_block([6, 7, 8, 9, 10, 11, 12], names), YEAR, MONTH, ROSTER)
    assert [(a.physician_id, a.shift.code) for a in res.assignments] == [("Alpha", "1600h RAH F side")]
    assert sorted((o.physician_id, o.call_type) for o in res.on_calls) == [("Beta", "DOC"), ("Gamma", "NOC")]


def test_labels_tolerate_stray_whitespace():
    block = _week_block([6, 7, 8, 9, 10, 11, 12], {})
    for row in block:
        if row[0] == "NECHC":
            row[0] = "NECHC "
        if row[0] == "1800-0000":
            row[0] = "  1800-0000"
    # put a name on the padded NECHC 0600-1400 row and on RAH A 1800-0000
    for i, row in enumerate(block):
        if row[0] == "NECHC " and block[i + 1][0] == "0600-1400":
            row[1] = "Alpha"
        if row[0] == "RAH A" and block[i + 1][0] == "  1800-0000":
            row[1] = "Beta"
    res = r.parse_schedule_grid(block, YEAR, MONTH, ROSTER)
    assert sorted((a.physician_id, a.shift.code) for a in res.assignments) == [("Alpha", "0600h NEHC"), ("Beta", "1800h RAH A side")]


def test_export_table_float_row_is_1600_2400_and_legacy_label_still_loads():
    assert ("RAH Float", "1600-2400", "1600h", "RAH F side") in EXPORT_SHIFTS
    assert EXPORT_SHIFT_LOOKUP[("RAH Float", "1600-0459")] == ("1600h", "RAH F side")
    assert EXPORT_SHIFT_LOOKUP[("AM CALL", "0500-1559")] == (None, None)
    assert EXPORT_SHIFT_LOOKUP[("PM CALL", "1600-0459")] == (None, None)
