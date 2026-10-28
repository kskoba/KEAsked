"""
Push to Master Sheet writes names where THIS sheet keeps its rows
(sheets_schedule_reader.locate_cells), so a hand-built department sheet
with no blank row between weeks and KEAsked's own export (which has one)
both land correctly. Found 2026-10-28 while writing the manual: the push
assumed the export's gap row, which the real December 2026 sheet lacks.
"""

from __future__ import annotations

import datetime

from scheduler.api import server
from scheduler.backend import sheets_schedule_reader as reader
from scheduler.backend.generator import Assignment, OnCallAssignment, ScheduleResult
from scheduler.backend.shifts import EXPORT_SHIFTS, SHIFT_CODE_LOOKUP

YEAR, MONTH = 2027, 2   # Feb 2027 starts on a Monday


def _grid(gap_row: bool) -> list[list]:
    """Two weeks of a master sheet: SUN..SAT header, date row, then per shift a name row + learner row."""
    g: list[list] = []
    week_starts = [datetime.date(2027, 1, 31), datetime.date(2027, 2, 7)]
    for ws in week_starts:
        g.append(["", "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])
        g.append([""] + [str((ws + datetime.timedelta(days=i)).day) if (ws + datetime.timedelta(days=i)).month == MONTH else "" for i in range(7)])
        for site_label, time_label, _, _ in EXPORT_SHIFTS:
            g.append([site_label] + [""] * 7)
            g.append([time_label] + [""] * 7)   # learner row, label in col A like the real sheet
        if gap_row:
            g.append([])
    return g


def _result() -> ScheduleResult:
    sh = SHIFT_CODE_LOOKUP["0600h RAH A side"]
    return ScheduleResult(
        year=YEAR, month=MONTH,
        # Feb 1 also carries a regular shift: the push only writes days the
        # schedule covers with regular slots (so a month solved from the 2nd
        # never wipes the 1st), and a lone on-call does not count as coverage.
        assignments=[Assignment(date=datetime.date(2027, 2, 9), shift=sh, physician_id="P", physician_name="P"),
                     Assignment(date=datetime.date(2027, 2, 1), shift=SHIFT_CODE_LOOKUP["0600h RAH B side"], physician_id="R", physician_name="R")],
        on_calls=[OnCallAssignment(date=datetime.date(2027, 2, 1), call_type="NOC", physician_id="Q", physician_name="Q")],
    )


NOC_TIME = next(t for s_, t, _, _ in EXPORT_SHIFTS if s_ == "NOC")


def _row_of(grid, label, week_index, time_label="0600-1200"):
    """1-based sheet row of the week_index-th (site label, time label) pair."""
    seen = -1
    for i, row in enumerate(grid[:-1]):
        nxt = grid[i + 1]
        if row and row[0] == label and nxt and nxt[0] == time_label:
            seen += 1
            if seen == week_index:
                return i + 1
    raise AssertionError((label, time_label))


def test_locate_cells_matches_both_layouts():
    for gap in (False, True):
        grid = _grid(gap)
        loc = reader.locate_cells(grid, YEAR, MONTH)
        # Feb 9 2027 is a Tuesday in week 2 -> column D; RAH A 0600h row of that week
        assert loc[("shift", datetime.date(2027, 2, 9), "0600h RAH A side")] == (_row_of(grid, "RAH A", 1), "D")
        # Feb 1 is a Monday in week 1 -> column C; NOC row
        assert loc[("call", datetime.date(2027, 2, 1), "NOC")] == (_row_of(grid, "NOC", 0, NOC_TIME), "C")


def _push(monkeypatch, grid):
    writes = {}
    monkeypatch.setattr(server.google_sheets_client, "find_month_file_id", lambda c, y, m: "FILE")
    monkeypatch.setattr(server.google_sheets_client, "read_range", lambda c, f, r: grid)
    monkeypatch.setattr(server.google_sheets_client, "spreadsheet_url", lambda f: "https://sheet/FILE")
    def write(c, f, data):
        writes.update({d["range"]: d["values"][0][0] for d in data}); return len(data)
    monkeypatch.setattr(server.google_sheets_client, "write_ranges", write)
    cells, url, skipped = server._push_result_to_master_sheet(_result(), object())
    return writes, cells, skipped


def test_push_lands_names_on_a_sheet_without_gap_rows(monkeypatch):
    grid = _grid(gap_row=False)
    writes, cells, skipped = _push(monkeypatch, grid)
    assert writes[f"D{_row_of(grid, 'RAH A', 1)}"] == "P"
    assert writes[f"C{_row_of(grid, 'NOC', 0, NOC_TIME)}"] == "Q"
    # the learner row under RAH A is never written
    assert f"D{_row_of(grid, 'RAH A', 1) + 1}" not in writes
    assert skipped == 0


def test_push_lands_names_on_a_sheet_with_gap_rows_too(monkeypatch):
    grid = _grid(gap_row=True)
    writes, cells, skipped = _push(monkeypatch, grid)
    assert writes[f"D{_row_of(grid, 'RAH A', 1)}"] == "P"
    assert writes[f"C{_row_of(grid, 'NOC', 0, NOC_TIME)}"] == "Q"
    assert skipped == 0


def test_push_reports_cells_the_sheet_has_no_row_for(monkeypatch):
    grid = _grid(gap_row=False)
    grid = [r for r in grid if not (r and r[0] in ("NOC", NOC_TIME))]   # sheet lacks the NOC row entirely
    writes, cells, skipped = _push(monkeypatch, grid)
    assert skipped >= 1 and not any(v == "Q" for v in writes.values())
