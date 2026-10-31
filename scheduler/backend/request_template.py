
"""
Build the month's blank shift-request xlsx from the committed master.

Until 2026-10 the scheduler copied last month's template by hand each
month: retyped rows 3-4 (day numbers and weekday letters), painted the
holiday / bonus window yellow, typed its label in row 2, and edited the
"Return by MIDNIGHT ..." line in A60 (which went stale more than once).
This module does exactly those edits to `scheduler/data/
request_template_master.xlsx` -- the February 2027 blank sheet pulled from
sked, which has no names in it -- and returns the bytes. Everything else
in the file (the 23 shift rows and their codes, the hidden count rows
30-37 and their formulas, the question area at AG38:AT61, fills, widths,
the hidden column AH) is left untouched so that sked's grid parser
(public/app.js), sked's export patcher (templateFill.ts) and KEAsked's
own importer.py keep seeing the layout they were written against.

The file is still the interchange format with sked for now (uploaded per
period, patched per physician on export, parsed back by importer.py), so
it must stay byte-for-byte compatible in layout; when sked renders from a
period definition instead, this builder remains the xlsx source for the
scheduler-side "Export All Preferences to .xlsx" and can be retired with
the last legacy user.

Layout conventions reproduced from the department's hand-made templates
(Oct 2026, Nov 2026, Dec 2024/2025/2026, Mar 2024/2025/2026):
- row 3: day numbers from column B, one column per period day (max 32,
  B..AG, which is as far as the hidden formulas reach);
- row 4: weekday letters M T W R F S SU;
- rows 2, 3 and 4 of a marked day filled solid yellow FFFF00 (sked reads
  row 3's fill; the human eye reads the block);
- row 2 above the first day of each labelled run: the label ("Christmas
  Rules Apply", "Thanksgiving", "EPSB Spring Break"); above a spill-over
  day the next month's abbreviation ("Jan") when nothing else sits there;
- A1 "insert name here" (sked writes the physician's name there on export);
- A2 empty (sked writes the physician's per-month comment there);
- A60 "Return by MIDNIGHT <Month> <day> to <mailbox>".
"""

from __future__ import annotations

import datetime as dt
import io
from copy import copy
from pathlib import Path
from typing import Iterable

import openpyxl
from openpyxl.styles import PatternFill

from scheduler.backend.month_calendar import (
    DOW_LABELS,
    MarkedDay,
    labelled_runs,
    period_days,
    period_label,
)

MASTER_PATH = Path(__file__).resolve().parent.parent / "data" / "request_template_master.xlsx"

# The mailbox physicians have always been told to return the sheet to
# (user decision 2026-10-31: keep the original address).
RETURN_TO_MAILBOX = "keasked@gmail.com"

YELLOW_ARGB = "FFFFFF00"
NAME_PLACEHOLDER = "insert name here"

_ROW_NAME = 1
_ROW_LABEL = 2
_ROW_DATE = 3
_ROW_DOW = 4
_ROW_RETURN_BY = 60
_GRID_ROWS = range(7, 30)          # DOC, the 22 shift rows, NOC -- see importer._BLOCK_ROWS
_FIRST_DAY_COL = 2                 # column B
_MAX_DAY_COLS = 32                 # B..AG: the hidden count formulas stop at AG
_SHIFT_LABEL_COL = 37              # column AK: "0600 RAH Acute" etc.
_EXPECTED_SHIFT_ROWS = 23


class MasterTemplateError(RuntimeError):
    """The committed master no longer has the layout this builder (and the parsers) expect."""


def template_filename(year: int, month: int) -> str:
    """The department's traditional file name; sked reads month + year out of it."""
    return f"{period_label(year, month)} Master Preferences with 0600 and night Q.xlsx"


def return_by_line(closes_at: dt.date, mailbox: str = RETURN_TO_MAILBOX) -> str:
    return f"Return by MIDNIGHT {closes_at.strftime('%B')} {closes_at.day} to {mailbox}"


def _check_master(ws) -> dict[int, str]:
    """Sanity-check the master and return the shift code per grid row (column B)."""
    if str(ws.cell(_ROW_DATE, 1).value or "").strip().upper() != "DATE":
        raise MasterTemplateError("A3 should read DATE")
    labels = [ws.cell(r, _SHIFT_LABEL_COL).value for r in _GRID_ROWS]
    if sum(1 for v in labels if v not in (None, "")) != _EXPECTED_SHIFT_ROWS:
        raise MasterTemplateError(
            f"expected {_EXPECTED_SHIFT_ROWS} labelled shift rows in column AK, "
            f"found {sum(1 for v in labels if v not in (None, ''))}"
        )
    codes = {r: str(ws.cell(r, _FIRST_DAY_COL).value or "").strip() for r in _GRID_ROWS}
    missing = [r for r, c in codes.items() if not c]
    if missing:
        raise MasterTemplateError(f"master column B has no shift code on rows {missing}")
    return codes


def _is_yellow(cell) -> bool:
    fill = cell.fill
    return bool(
        fill is not None and fill.fill_type == "solid"
        and fill.fgColor is not None and fill.fgColor.type == "rgb"
        and str(fill.fgColor.rgb).upper().endswith("FFFF00")
    )


def build_request_template(
    year: int,
    month: int,
    marks: Iterable[MarkedDay] = (),
    closes_at: dt.date | None = None,
    mailbox: str = RETURN_TO_MAILBOX,
    master_path: Path | str = MASTER_PATH,
) -> bytes:
    """
    The blank request template for `year`/`month` as xlsx bytes.

    `marks` are the yellow days (any order; a mark outside the period is an
    error -- the UI only offers period days). `closes_at` fills A60; None
    leaves the master's line untouched.
    """
    days = period_days(year, month)
    if len(days) > _MAX_DAY_COLS:
        raise ValueError(f"{len(days)} days do not fit the template's {_MAX_DAY_COLS} day columns")
    marks = list(marks)
    period_set = set(days)
    outside = sorted(m.date for m in marks if m.date not in period_set)
    if outside:
        raise ValueError(f"marked days outside the period: {', '.join(d.isoformat() for d in outside)}")
    label_by_date = {m.date: (m.label or "").strip() for m in marks}

    wb = openpyxl.load_workbook(Path(master_path))
    ws = wb.worksheets[0]
    codes = _check_master(ws)

    # The master's own unmarked header fill (solid white in every template
    # on record); taken from the first day column that is not yellow.
    plain_fill = None
    for i in range(_MAX_DAY_COLS):
        c = ws.cell(_ROW_DATE, _FIRST_DAY_COL + i)
        if not _is_yellow(c):
            plain_fill = copy(c.fill)
            break
    if plain_fill is None:
        plain_fill = PatternFill(fill_type=None)
    yellow_fill = PatternFill(fill_type="solid", fgColor=YELLOW_ARGB, bgColor=YELLOW_ARGB)

    col_of: dict[dt.date, int] = {}
    for i in range(_MAX_DAY_COLS):
        col = _FIRST_DAY_COL + i
        ws.cell(_ROW_LABEL, col).value = None
        if i >= len(days):
            # Unused trailing column (28-30 day months): blank but styled.
            ws.cell(_ROW_DATE, col).value = None
            ws.cell(_ROW_DOW, col).value = None
            for r in _GRID_ROWS:
                ws.cell(r, col).value = None
            for r in (_ROW_LABEL, _ROW_DATE, _ROW_DOW):
                ws.cell(r, col).fill = copy(plain_fill)
            continue
        d = days[i]
        col_of[d] = col
        ws.cell(_ROW_DATE, col).value = d.day
        ws.cell(_ROW_DOW, col).value = DOW_LABELS[d.weekday()]
        for r in _GRID_ROWS:
            ws.cell(r, col).value = codes[r]
        # Rows 2-4 of a marked day are yellow (the hand-made December sheets
        # painted the label row too, so the window reads as one block).
        fill = yellow_fill if d in label_by_date else plain_fill
        for r in (_ROW_LABEL, _ROW_DATE, _ROW_DOW):
            ws.cell(r, col).fill = copy(fill)

    for start, _end, label in labelled_runs(marks):
        if label:
            ws.cell(_ROW_LABEL, col_of[start]).value = label
    for d in days:
        if d.month != month and ws.cell(_ROW_LABEL, col_of[d]).value in (None, ""):
            ws.cell(_ROW_LABEL, col_of[d]).value = d.strftime("%b")

    ws.cell(_ROW_NAME, 1).value = NAME_PLACEHOLDER
    ws.cell(_ROW_LABEL, 1).value = None
    if closes_at is not None:
        ws.cell(_ROW_RETURN_BY, 1).value = return_by_line(closes_at, mailbox)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
