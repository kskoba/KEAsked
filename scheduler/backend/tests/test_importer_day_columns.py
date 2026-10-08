"""
importer: day-number -> column mapping on the department's templates.

Real finding (2026-10-07): the January 2027 template has NO Jan 1 column
(its DATE row runs 2..31), because Jan 1 is scheduled with December. The
importer's fallback for a day number missing from the DATE row was the
fixed offset for day 1 -- column B -- which on that template IS Jan 2. All
78 January submissions came in with Jan 1 identical to Jan 2, and the
solver happily staffed Jan 1 from availability nobody had given. The
December template has the mirror quirk: it runs 2..31 then a trailing "1"
(Jan 1) in column AG, which used to be read as Dec 1.

Now: a day with no column is NOT offered; a trailing wrapped day number is
the next month's leading day and lands in PhysicianSubmission.spillover_days.
"""

from __future__ import annotations

import datetime

import openpyxl

from scheduler.backend import importer
from scheduler.backend.importer import import_single_file


def _write_sheet(path, name: str, day_numbers: list, marks: dict[int, dict[str, str]]):
    """
    A minimal template: row-3 DATE labels from `day_numbers` (None = blank
    header cell), starting in column B. `marks[col_offset]` writes cells for
    that column: {"Z": "z", "shift:<code>": "x", "DOC": "DOC"}.
    """
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sheet1"
    ws.cell(row=1, column=1, value=name)
    ws.cell(row=importer._DATE_ROW, column=1, value="DATE")
    ws.cell(row=importer._Z_ROW, column=1, value="Service Days")
    for i, d in enumerate(day_numbers):
        if d is not None:
            ws.cell(row=importer._DATE_ROW, column=importer._FIRST_DAY_COL + i, value=d)
    shift_rows = {code: row for row, code in importer._ROW_TO_SHIFT_CODE.items()}
    for i, cells in marks.items():
        col = importer._FIRST_DAY_COL + i
        for key, val in cells.items():
            if key == "Z":
                ws.cell(row=importer._Z_ROW, column=col, value=val)
            elif key == "DOC":
                ws.cell(row=importer._DOC_ROW, column=col, value=val)
            elif key.startswith("shift:"):
                ws.cell(row=shift_rows[key[6:]], column=col, value=val)
    ws.cell(row=importer._N_SHIFTS_ROW, column=importer._N_SHIFTS_COL, value=6)
    wb.save(path)


def _day(sub, d: int):
    return next(x for x in sub.days if x.date.day == d)


def test_january_template_without_a_jan_1_column_does_not_borrow_jan_2(tmp_path):
    f = tmp_path / "Test - January 2027.xlsx"
    # DATE row 2..31 in columns B..AF; Jan 2 (column B) is marked available.
    _write_sheet(f, "Test", list(range(2, 32)), {0: {"Z": "z", "shift:1200h NEHC": "x"}})
    sub = import_single_file(f, 2027, 1)

    assert _day(sub, 2).wants_to_work and "1200h NEHC" in _day(sub, 2).requested_shifts
    jan1 = _day(sub, 1)
    assert jan1.wants_to_work is False and not jan1.requested_shifts and not jan1.available_blocks
    assert sub.spillover_days == []
    assert len(sub.days) == 31


def test_december_template_trailing_1_is_jan_1_spillover_not_dec_1(tmp_path):
    f = tmp_path / "Test - December 2026.xlsx"
    # DATE row 2..31 in B..AF, then 1 (= Jan 1 2027) in AG, which is marked available.
    _write_sheet(f, "Test", list(range(2, 32)) + [1], {30: {"Z": "z", "shift:2400h NEHC": "x", "DOC": "DOC"}})
    sub = import_single_file(f, 2026, 12)

    assert _day(sub, 1).wants_to_work is False                 # Dec 1 lives on the November sheet
    assert len(sub.spillover_days) == 1
    spill = sub.spillover_days[0]
    assert spill.date == datetime.date(2027, 1, 1)
    assert spill.wants_to_work and "2400h NEHC" in spill.requested_shifts and spill.doc_available


def test_full_month_template_is_unchanged(tmp_path):
    f = tmp_path / "Test - March 2027.xlsx"
    _write_sheet(f, "Test", list(range(1, 32)), {0: {"Z": "z", "shift:1200h NEHC": "x"}, 30: {"Z": "z"}})
    sub = import_single_file(f, 2027, 3)
    assert _day(sub, 1).wants_to_work and _day(sub, 31).wants_to_work and not _day(sub, 15).wants_to_work
    assert sub.spillover_days == []


def test_legacy_sheet_without_date_labels_falls_back_to_fixed_columns(tmp_path):
    f = tmp_path / "Test - March 2027.xlsx"
    _write_sheet(f, "Test", [None] * 31, {0: {"Z": "z"}, 4: {"Z": "z"}})
    sub = import_single_file(f, 2027, 3)
    assert _day(sub, 1).wants_to_work and _day(sub, 5).wants_to_work and not _day(sub, 2).wants_to_work
