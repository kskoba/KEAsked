"""
Excel importer for physician shift-request submissions.

Layout (confirmed from sample KS- June 2026 ... .xlsx):
  Row 1,  Col A       : Physician name
  Row 3,  Col B+      : Day numbers (1 … 30/31); Col B = day 1
  Row 4,  Col B+      : Day-of-week abbreviations (M/T/W/R/F/S/SU)
  Row 5,  Col B+      : Service Days – "Z" means physician wants to work
  Row 38, Col AK (37) : shifts_requested  (labelled "N")

  Shift rows (1-based), skip row 7 (DOC on-call) and row 19 (NOC):
    Block 0  0600h  : rows  8–11  (all 4 must be non-blank)
    Block 1  0900-1200h : rows 12–16  (all 5 must be non-blank)
    Block 2  1400-1700h : rows 17,18,20,21  (row 19 excluded)
    Block 3  1800-2000h : rows 22–25  (all 4 must be non-blank)
    Block 4  2400h  : rows 26–29  (all 4 must be non-blank)

  Availability: non-empty cell = available; empty = not available.
"""

from __future__ import annotations

import calendar
import datetime
from pathlib import Path

import openpyxl

from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import BLOCKS


# --------------------------------------------------------------------------- #
# Fixed layout constants (1-based Excel addresses)
# --------------------------------------------------------------------------- #

_NAME_ROW = 1
_NAME_COL = 1          # Col A

_N_SHIFTS_ROW = 38
_N_SHIFTS_COL = 37     # Col AK  — requested (N)
_MIN_SHIFTS_ROW = 40
_MIN_SHIFTS_COL = 42   # Col AP  — minimum
_MAX_SHIFTS_ROW = 42
_MAX_SHIFTS_COL = 42   # Col AP  — maximum (≤ N+2)

_N_2400H_ROW = 59
_N_2400H_COL = 37      # Col AK  — requested 2400h shifts
_N_0600H_ROW = 61
_N_0600H_COL = 37      # Col AK  — requested 0600h shifts

_Z_ROW = 5             # "Service Days" row
_FIRST_DAY_COL = 2     # Col B = day 1

# Block definitions: list of (excel_rows,) that must ALL be non-empty.
# Rows are 1-based.
_BLOCK_ROWS: list[list[int]] = [
    [8, 9, 10, 11],          # Block 0 — 0600h
    [12, 13, 14, 15, 16],    # Block 1 — 0900–1200h
    [17, 18, 20, 21],        # Block 2 — 1400–1700h  (row 19 = NOC, excluded)
    [22, 23, 24, 25],        # Block 3 — 1800–2000h
    [26, 27, 28, 29],        # Block 4 — 2400h
]

# Sanity-check: number of blocks must match shifts.py
assert len(_BLOCK_ROWS) == len(BLOCKS), (
    f"Block count mismatch: importer has {len(_BLOCK_ROWS)}, "
    f"shifts.py has {len(BLOCKS)}"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _cell(ws, row: int, col: int):
    """Read a cell value (1-based row and column)."""
    return ws.cell(row=row, column=col).value


def _is_filled(v) -> bool:
    """Return True when a cell is considered 'available' (non-empty)."""
    if v is None:
        return False
    return str(v).strip() != ""


def _day_col(day_num: int) -> int:
    """Return the 1-based Excel column for a given day number (1-based)."""
    return _FIRST_DAY_COL + day_num - 1


# --------------------------------------------------------------------------- #
# Core parser
# --------------------------------------------------------------------------- #

def _parse_worksheet(
    ws,
    year: int,
    month: int,
    source_file: str = "",
    physician_id_override: str | None = None,
) -> PhysicianSubmission:
    """Parse one worksheet into a PhysicianSubmission."""

    physician_name = str(_cell(ws, _NAME_ROW, _NAME_COL) or "").strip()
    physician_id = physician_id_override or physician_name

    def _int_cell(row, col, default=0):
        try:
            return int(_cell(ws, row, col) or default)
        except (TypeError, ValueError):
            return default

    shifts_requested = _int_cell(_N_SHIFTS_ROW, _N_SHIFTS_COL)
    shifts_min = _int_cell(_MIN_SHIFTS_ROW, _MIN_SHIFTS_COL, shifts_requested)
    shifts_max = _int_cell(_MAX_SHIFTS_ROW, _MAX_SHIFTS_COL, shifts_requested)
    shifts_2400h_requested = _int_cell(_N_2400H_ROW, _N_2400H_COL)
    shifts_0600h_requested = _int_cell(_N_0600H_ROW, _N_0600H_COL)

    days_in_month = calendar.monthrange(year, month)[1]
    days: list[DayAvailability] = []

    for day_num in range(1, days_in_month + 1):
        col = _day_col(day_num)
        date = datetime.date(year, month, day_num)

        # --- Z marker (wants to work) ---
        wants = str(_cell(ws, _Z_ROW, col) or "").strip().upper() == "Z"

        # --- Block availability ---
        available_blocks: set[int] = set()
        for block_idx, row_list in enumerate(_BLOCK_ROWS):
            if all(_is_filled(_cell(ws, r, col)) for r in row_list):
                available_blocks.add(block_idx)

        days.append(
            DayAvailability(
                date=date,
                wants_to_work=wants,
                available_blocks=frozenset(available_blocks),
            )
        )

    return PhysicianSubmission(
        physician_id=physician_id,
        physician_name=physician_name,
        year=year,
        month=month,
        shifts_requested=shifts_requested,
        shifts_min=shifts_min,
        shifts_max=shifts_max,
        shifts_2400h_requested=shifts_2400h_requested,
        shifts_0600h_requested=shifts_0600h_requested,
        days=days,
        source_file=source_file,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def import_single_file(
    path: str | Path,
    year: int,
    month: int,
    physician_id_override: str | None = None,
) -> PhysicianSubmission:
    """
    Import one physician's Excel submission file.

    Returns a PhysicianSubmission.  Call validator.validate() on the result
    to check for errors.
    """
    path = Path(path)
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    return _parse_worksheet(
        ws=ws,
        year=year,
        month=month,
        source_file=str(path),
        physician_id_override=physician_id_override or path.stem,
    )


def import_directory(
    directory: str | Path,
    year: int,
    month: int,
    glob_pattern: str = "*.xlsx",
) -> list[PhysicianSubmission]:
    """
    Import all matching Excel files from a directory.

    Returns one PhysicianSubmission per file.  Files that cannot be parsed
    are skipped with a warning printed to stdout.
    """
    directory = Path(directory)
    submissions: list[PhysicianSubmission] = []
    for path in sorted(directory.glob(glob_pattern)):
        try:
            sub = import_single_file(path, year, month)
            submissions.append(sub)
        except Exception as exc:
            print(f"[importer] WARNING: could not parse {path.name}: {exc}")
    return submissions
