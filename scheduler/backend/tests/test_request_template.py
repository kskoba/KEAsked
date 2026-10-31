"""
request_template: the month's blank request xlsx built from the committed
master must carry exactly the edits the scheduler used to make by hand, and
must still parse through importer.py (the same layout sked parses).
"""

from __future__ import annotations

import datetime as dt
import io

import openpyxl
import pytest

from scheduler.backend import month_calendar as mc
from scheduler.backend import request_template as rt
from scheduler.backend.importer import import_single_file


def _sheet(data: bytes):
    return openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]


def _yellow_days(ws, row):
    out = []
    for c in range(2, 34):
        cell = ws.cell(row, c)
        if cell.value in (None, ""):
            continue
        if rt._is_yellow(cell):
            out.append(int(ws.cell(3, c).value))   # the day number, whichever row was checked
    return out


def test_march_2027_layout_marks_and_return_line():
    marks = mc.suggested_marks(2027, 3) + [
        mc.MarkedDay(dt.date(2027, 3, d), "EPSB Spring Break") for d in (27, 28, 29, 30, 31)
    ]
    data = rt.build_request_template(2027, 3, marks, closes_at=dt.date(2027, 1, 31))
    ws = _sheet(data)
    assert [ws.cell(3, c).value for c in range(2, 33)] == list(range(1, 32))
    assert ws.cell(3, 33).value is None                       # 32nd day column unused
    assert ws.cell(4, 2).value == "M" and ws.cell(4, 5).value == "R" and ws.cell(4, 8).value == "SU"
    assert _yellow_days(ws, 3) == [26, 27, 28, 29, 30, 31]
    assert _yellow_days(ws, 4) == [26, 27, 28, 29, 30, 31]
    # Labels sit in row 2 above the first day of each run (col B = day 1).
    assert ws.cell(2, 2 + 25).value == "Good Friday"
    assert ws.cell(2, 2 + 26).value == "EPSB Spring Break"
    assert ws.cell(2, 1).value is None                         # A2 stays free for the physician comment
    assert ws.cell(1, 1).value == rt.NAME_PLACEHOLDER
    assert ws.cell(60, 1).value == "Return by MIDNIGHT January 31 to keasked@gmail.com"
    # Every shift row carries its code on every day; DOC/NOC rows included.
    assert all(ws.cell(7, c).value == "DOC" for c in range(2, 33))
    assert all(ws.cell(19, c).value == "NOC" for c in range(2, 33))
    assert ws.cell(8, 2).value == "6RA" and ws.cell(29, 32).value == "24RI"
    # The hidden count rows and their formulas survived the round trip.
    assert all(ws.row_dimensions[r].hidden for r in range(30, 38))
    assert str(ws.cell(33, 2).value).startswith("=IF(COUNTBLANK(B8:B11)")
    assert ws.column_dimensions["AH"].hidden
    assert rt.template_filename(2027, 3) == "March 2027 Master Preferences with 0600 and night Q.xlsx"


def test_december_spillover_and_february_short_month():
    dec = _sheet(rt.build_request_template(2026, 12, mc.suggested_marks(2026, 12)))
    assert [dec.cell(3, c).value for c in range(2, 33)] == list(range(2, 32)) + [1]
    assert dec.cell(4, 32).value == "F"                       # Jan 1 2027 is a Friday
    assert _yellow_days(dec, 3) == [24, 25, 26, 27, 28, 29, 30, 31, 1]
    assert dec.cell(2, 2 + 22).value == mc.CHRISTMAS_WINDOW_LABEL   # above Dec 24
    assert dec.cell(2, 32).value == "Jan"                     # above the spill-over day

    feb = _sheet(rt.build_request_template(2027, 2, mc.suggested_marks(2027, 2)))
    assert [feb.cell(3, c).value for c in range(2, 30)] == list(range(1, 29))
    assert all(feb.cell(3, c).value is None for c in range(30, 34))
    assert all(feb.cell(r, 30).value is None for r in range(7, 30))   # no codes in unused columns
    assert _yellow_days(feb, 3) == [15]                       # Family Day


def test_generated_template_imports_through_importer(tmp_path):
    data = rt.build_request_template(2026, 12, mc.suggested_marks(2026, 12), closes_at=dt.date(2026, 10, 31))
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.worksheets[0]
    ws["A1"] = "Test Physician"
    ws["AK38"] = 10
    ws["AP40"] = 9
    ws["AP42"] = 11
    ws["AK59"] = 2
    ws["AK61"] = 1
    path = tmp_path / "Test - December 2026.xlsx"
    wb.save(path)
    sub = import_single_file(path, 2026, 12, physician_id_override="Test")
    assert sub.shifts_requested == 10
    assert sub.spillover_days and sub.spillover_days[0].date == dt.date(2027, 1, 1)
    offered = sorted(day.date for day in sub.days if day.wants_to_work)
    assert offered[0] == dt.date(2026, 12, 2) and offered[-1] == dt.date(2026, 12, 31) and len(offered) == 30
    assert all(len(day.available_blocks) == 5 for day in sub.days if day.wants_to_work)


def test_marks_outside_the_period_are_rejected():
    with pytest.raises(ValueError):
        rt.build_request_template(2027, 1, [mc.MarkedDay(dt.date(2027, 1, 1), "New Year's Day")])
