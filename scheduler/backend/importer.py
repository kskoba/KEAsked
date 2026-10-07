"""
Excel importer for physician shift-request submissions.

Layout (confirmed from sample KS- June 2026 ... .xlsx):
  Row 1,  Col A       : Physician name
  Row 3,  Col B+      : Day numbers (1 … 30/31); Col B = day 1
  Row 4,  Col B+      : Day-of-week abbreviations (M/T/W/R/F/S/SU)
  Row 5,  Col B+      : Service Days – "Z" means physician wants to work
  Row 7,  Col B+      : Day On Call (DOC) – physician types "DOC" on days available
  Row 19, Col B+      : Night On Call (NOC) – physician types "NOC" on days available
  Row 38, Col AK (37) : shifts_requested  (labelled "N")

  Shift rows (1-based), rows 7 (DOC) and 19 (NOC) are on-call, not regular shifts:
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
import re
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
_PREFERRED_ROW = 6     # "Preferred" row -- physician's specific pick for the day, if any
_DATE_ROW = 3          # Day-of-month number row (e.g. "2", "3", ...)
_DOW_ROW = 4           # Day-of-week abbreviation row
_FIRST_DAY_COL = 2     # Col B = day 1 in the usual layout (see day_to_col below)

_DOC_ROW = 7           # Day On Call — physician types "DOC" on days they're available
_NOC_ROW = 19          # Night On Call — physician types "NOC" on days they're available

# Physician-facing day-of-week abbreviations, keyed by Python's
# date.weekday() (Monday=0 ... Sunday=6). "R" for Thursday (not "T") is
# this practice's own convention, avoiding a T/Th clash with Tuesday.
_DOW_LABELS: dict[int, str] = {0: "M", 1: "T", 2: "W", 3: "R", 4: "F", 5: "S", 6: "SU"}

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

# Row -> shift code, positional within each block (row_list[i] is that
# block's specific cell for BLOCKS[block_idx][i] — e.g. row 26 is
# "2400h RAH A side", row 28 is "2400h NEHC", etc.). Built once from the
# same structure available_blocks itself uses.
_ROW_TO_SHIFT_CODE: dict[int, str] = {
    row: shift.code
    for block_idx, row_list in enumerate(_BLOCK_ROWS)
    for row, shift in zip(row_list, BLOCKS[block_idx])
}


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


def _parse_anchor_value(raw) -> tuple[int, bool]:
    """
    Parse a physician's raw AK59/AK61 (2400h/0600h requested) cell value.

    Returns (value, stated) — stated is False only for a genuinely blank
    cell or free text with no number in it at all (e.g. "whatever", "all",
    "about half", "No pref"); value is 0 in that case since there's
    nothing to use. Confirmed against real submissions this needs to
    handle:
      - A plain number, including a literal 0 (an explicit "I want
        none" — stated=True, distinct from a blank cell).
      - Free-text ranges ("3 or 4", "1 to 2", "6-8") — takes the LOWER
        end. Policy changed 2026-10-06 from higher-end: a range means the
        low number is what they want and the high number is what they'd
        tolerate; reading it high used up all of a physician's shifts on
        one anchor type (RScheirer's "6/8" became 8 of 8 as nights,
        leaving no room for the float he also wanted). A ceiling phrase
        ("up to 4", "at most 4", "no more than 4", "max 4") still reads as
        its number — there the number IS the tolerance, not a floor.
      - Excel silently reinterpreting a typed range like "4/6" as a date
        — min(month, day) is the range's low end (locale-agnostic: the
        two typed numbers land in month/day in either order).
      - Spelled-out counts ("two", "one or two") are converted first.
      - Genuinely non-numeric free text with no digits at all — no
        number to extract, so treated the same as blank (stated=False)
        rather than guessing.
    """
    if raw is None or isinstance(raw, bool):
        return 0, False
    if isinstance(raw, (int, float)):
        return int(raw), True
    if isinstance(raw, datetime.datetime):
        return min(raw.month, raw.day), True
    text = str(raw).strip()
    if not text:
        return 0, False
    # Spelled-out counts ("two", "one or two") -- found in a real Jan 2027
    # submission (Haager: "two") that was silently landing as blank, i.e.
    # no stated request at all, so the anchor floor never protected it.
    for word, digit in _NUMBER_WORDS.items():
        text = re.sub(rf"\b{word}\b", digit, text, flags=re.IGNORECASE)
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not numbers:
        return 0, False
    if _CEILING_PHRASE_RE.search(text):
        return max(numbers), True
    return min(numbers), True


_CEILING_PHRASE_RE = re.compile(r"\b(up to|at most|no more than|max(imum)?)\b", re.IGNORECASE)


_NUMBER_WORDS = {
    "zero": "0", "none": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


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

    # Some physicians type their name in the wrong cell (e.g. leaving
    # "insert name here" in A1 and writing their real name a few columns
    # over instead). Capture every other non-empty text cell in row 1 as a
    # fallback identity candidate — resolution still only ever exact-matches
    # against the roster (see physician_resolver.py), this just gives it
    # more strings to try.
    raw_name_candidates: list[str] = []
    max_col = min(ws.max_column or 1, 20)
    for col in range(_NAME_COL + 1, max_col + 1):
        val = str(_cell(ws, _NAME_ROW, col) or "").strip()
        if val:
            raw_name_candidates.append(val)

    def _int_cell(row, col, default=0):
        try:
            return int(_cell(ws, row, col) or default)
        except (TypeError, ValueError):
            return default

    shifts_requested = _int_cell(_N_SHIFTS_ROW, _N_SHIFTS_COL)
    shifts_min = _int_cell(_MIN_SHIFTS_ROW, _MIN_SHIFTS_COL, shifts_requested)
    shifts_max = _int_cell(_MAX_SHIFTS_ROW, _MAX_SHIFTS_COL, shifts_requested)
    shifts_2400h_requested, shifts_2400h_stated = _parse_anchor_value(_cell(ws, _N_2400H_ROW, _N_2400H_COL))
    shifts_0600h_requested, shifts_0600h_stated = _parse_anchor_value(_cell(ws, _N_0600H_ROW, _N_0600H_COL))

    days_in_month = calendar.monthrange(year, month)[1]
    days: list[DayAvailability] = []
    dow_labeled = 0
    dow_mismatched = 0

    # Map day-number -> column from the sheet's own row-3 DATE labels,
    # rather than assuming day 1 always sits in _FIRST_DAY_COL. Confirmed
    # necessary, not theoretical: the January 2027 template starts at day
    # 2 (column B), because day 1 was scheduled together with December --
    # every physician's actual day-2..31 data was landing one column to
    # the left of where a fixed day_num -> column formula expected it,
    # shifting every date and tripping check_month_mismatch for every
    # single January file. Falls back to the fixed-offset mapping for any
    # day number the row doesn't label (e.g. a genuinely missing day 1),
    # which then has no availability data and is correctly left as "not
    # submitted" rather than guessed at.
    max_col = min(ws.max_column or 1, _FIRST_DAY_COL + days_in_month + 10)
    day_to_col: dict[int, int] = {}
    for col in range(_FIRST_DAY_COL, max_col + 1):
        try:
            d = int(_cell(ws, _DATE_ROW, col))
        except (TypeError, ValueError):
            continue
        if 1 <= d <= days_in_month and d not in day_to_col:
            day_to_col[d] = col

    for day_num in range(1, days_in_month + 1):
        col = day_to_col.get(day_num, _day_col(day_num))
        date = datetime.date(year, month, day_num)

        # --- Z marker (wants to work) ---
        wants = str(_cell(ws, _Z_ROW, col) or "").strip().upper() == "Z"

        # --- Block availability (submission-quality signal for the
        # validator's min_valid_blocks/anchored-day rules ONLY — this
        # stays "every row in the block filled", unchanged. It exists to
        # nudge physicians toward offering whole blocks; some legitimately
        # don't and have allowances there, which is exactly why scheduling
        # itself must not rely on it — see available_shifts below.) ---
        available_blocks: set[int] = set()
        for block_idx, row_list in enumerate(_BLOCK_ROWS):
            if all(_is_filled(_cell(ws, r, col)) for r in row_list):
                available_blocks.add(block_idx)

        # --- Per-shift availability (what the generator actually schedules
        # against). Each row stands on its own: a physician available for
        # 2400h RAH A but not 2400h NEHC on the same day is available for
        # exactly that — not "the whole 2400h block" and not "nothing" —
        # regardless of whether that makes the block count as valid above.
        available_shifts: set[str] = {
            shift_code
            for row, shift_code in _ROW_TO_SHIFT_CODE.items()
            if _is_filled(_cell(ws, row, col))
        }

        # --- Preferred shift (the "Preferred" row, above the main grid) ---
        # The sheet's own convention is shorthand text (e.g. "15NE",
        # "18RA") that's ALSO exactly what's typed into that shift's own
        # row for this day -- so rather than parsing the shorthand
        # ourselves (ambiguous on its own: "18RA" is printed identically
        # for both 1800h RAH A side and 1800h RAH B side in this
        # template), match it against this physician's own per-row text
        # for this same day and take whichever row(s) it equals. Usually
        # resolves to exactly one shift code; two when the sheet's
        # shorthand is genuinely ambiguous between two sites, in which
        # case both are kept as candidates. DOC/NOC rows are deliberately
        # excluded here -- on-call assignment isn't part of the same
        # shifts[] mechanism this feeds (see generator_cpsat.py's
        # honor_all_requests bonus), so a "NOC"-style preferred entry has
        # nothing to resolve against and is silently ignored, not an error.
        preferred_raw = str(_cell(ws, _PREFERRED_ROW, col) or "").strip()
        preferred_shifts: set[str] = set()
        if preferred_raw.upper() == "N":
            # Bare "N" is a different, coarser convention some physicians
            # use for "a night shift, any site" rather than a specific
            # site+time code (confirmed against a real submission,
            # RScheirer, 2026-09-29). Every 2400h code he's actually
            # available for that day is kept as a candidate -- same
            # "don't guess a single site" principle as the site-ambiguous
            # case below, just starting from a coarser signal.
            preferred_shifts = {
                shift_code for shift_code in available_shifts if shift_code.startswith("2400h")
            }
        elif preferred_raw:
            for row, shift_code in _ROW_TO_SHIFT_CODE.items():
                cell_text = str(_cell(ws, row, col) or "").strip()
                if cell_text and cell_text.upper() == preferred_raw.upper():
                    preferred_shifts.add(shift_code)

        # --- On-call availability (Day On Call / Night On Call) — physicians
        # type "DOC" / "NOC" on the days they're available for each, on their
        # own dedicated rows separate from the regular shift grid. ---
        doc_available = _is_filled(_cell(ws, _DOC_ROW, col))
        noc_available = _is_filled(_cell(ws, _NOC_ROW, col))

        days.append(
            DayAvailability(
                date=date,
                wants_to_work=wants,
                available_blocks=frozenset(available_blocks),
                requested_shifts=frozenset(available_shifts),
                doc_available=doc_available,
                noc_available=noc_available,
                preferred_shifts=frozenset(preferred_shifts),
            )
        )

        # --- Day-of-week sanity check ---
        # The physician's own row-4 label for this column should match the
        # actual weekday of (year, month, day_num). If most labeled days
        # disagree, the year/month selected for this import doesn't match
        # what this sheet was actually filled out for (e.g. an October
        # sheet imported under a leftover "June" selection) — every date
        # in this submission is then off, silently, until that's fixed.
        dow_label = str(_cell(ws, _DOW_ROW, col) or "").strip().upper()
        if dow_label:
            dow_labeled += 1
            if dow_label != _DOW_LABELS[date.weekday()]:
                dow_mismatched += 1

    month_mismatch = dow_labeled > 0 and (dow_mismatched / dow_labeled) > 0.5

    # If no "Z" was found anywhere in the whole submission, this physician
    # almost certainly forgot to mark row 5 at all — not that they want
    # zero days all month. Fall back to treating any day they otherwise
    # marked availability for as a potential working day, rather than
    # discarding the whole submission as "wants nothing".
    if not any(d.wants_to_work for d in days):
        for d in days:
            if d.available_blocks:
                d.wants_to_work = True

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
        shifts_2400h_stated=shifts_2400h_stated,
        shifts_0600h_stated=shifts_0600h_stated,
        days=days,
        source_file=source_file,
        raw_name_candidates=raw_name_candidates,
        month_mismatch=month_mismatch,
    )


# A file exported from Apple Numbers always gets this exact cover sheet
# name, inserted before the real content -- "This document was exported
# from Numbers. Each table was converted to an Excel worksheet..." with a
# Numbers-sheet-name -> Excel-worksheet-name mapping table. No real
# physician submission would ever be named this. Confirmed twice against
# real submissions (Samoraj, Mrochuk) -- in both cases the actual
# preference grid was the sheet immediately after this one.
_NUMBERS_EXPORT_SUMMARY_SHEET_NAME = "Export Summary"


def _first_real_worksheet(wb):
    """
    The worksheet import_single_file should actually parse -- normally
    just the first one, but skips a leading "Export Summary" cover sheet
    left behind by an Apple Numbers export, which otherwise gets silently
    parsed as if it were the submission (empty name, no dates, nothing --
    not an error, just wrong data flowing through as if the physician
    submitted blank).
    """
    for ws in wb.worksheets:
        if ws.title.strip() != _NUMBERS_EXPORT_SUMMARY_SHEET_NAME:
            return ws
    return wb.worksheets[0]


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
    ws = _first_real_worksheet(wb)
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
