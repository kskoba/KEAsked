"""
Flat-table importer for SingleJune.xlsx style submissions.

Format: one row per physician per date.
  A  physician           – physician display name
  B  date                – ISO date string or date object
  C  available           – bool
  D  allowed_shift_codes – comma-separated short codes, or None
  E  requested_shifts    – int (N)
  F  min_shifts          – int
  G  max_shifts          – int
  H  requested_0600      – int or None
  I  requested_2400      – int or None
  J  only_nechc          – bool (ignored by scheduler for now)
  K  only_nechc_or_intake
  L  cannot_work_nechc
  M  night_exempt
  N  weekend_exempt

Short code → canonical Shift mapping
  NE = NEHC, RA = RAH A side / RAH F side (1600h only), RB = RAH B side,
  RI = RAH I side.  DOC / NOC are skipped (not modelled as Shift objects).

A block is considered available for a day if at least one of the block's
canonical shifts is listed in allowed_shift_codes.

NOTE: this is a standalone adapter for testing; it does not modify importer.py.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import openpyxl

from scheduler.backend.models import DayAvailability, PhysicianSubmission
from scheduler.backend.shifts import BLOCKS, Shift, SHIFT_TO_BLOCK


# --------------------------------------------------------------------------- #
# Short-code → Shift object lookup
# --------------------------------------------------------------------------- #

# Build from canonical Shift objects so we stay in sync with shifts.py.
# RA codes (6RA, 12RA, 18RA, 24RA) expand to BOTH RAH A side AND RAH B side
# because the flat file does not distinguish between the two sub-groups.
_CODE_TO_SHIFTS: dict[str, list[Shift]] = {
    "6NE":  [Shift("0600h", "NEHC")],
    "6RA":  [Shift("0600h", "RAH A side"), Shift("0600h", "RAH B side")],
    "6RB":  [Shift("0600h", "RAH B side")],
    "6RI":  [Shift("0600h", "RAH I side")],
    "9NE":  [Shift("0900h", "NEHC")],
    "10RI": [Shift("1000h", "RAH I side")],
    "12NE": [Shift("1200h", "NEHC")],
    "12RA": [Shift("1200h", "RAH A side"), Shift("1200h", "RAH B side")],
    "12RB": [Shift("1200h", "RAH B side")],
    "14RI": [Shift("1400h", "RAH I side")],
    "15NE": [Shift("1500h", "NEHC")],
    # 16RA: the Excel still uses the old "RA" suffix; the canonical 1600h
    # shift was updated to RAH F side in shifts.py.
    "16RA": [Shift("1600h", "RAH F side")],
    "16RI": [Shift("1600h", "RAH F side")],  # legacy code; maps to the same 1600h RAH F slot
    "17NE": [Shift("1700h", "NEHC")],
    "18RA": [Shift("1800h", "RAH A side"), Shift("1800h", "RAH B side")],
    "18RB": [Shift("1800h", "RAH B side")],
    "18RI": [Shift("1800h", "RAH I side")],
    "20NE": [Shift("2000h", "NEHC")],
    "24NE": [Shift("2400h", "NEHC")],
    "24RA": [Shift("2400h", "RAH A side"), Shift("2400h", "RAH B side")],
    "24RB": [Shift("2400h", "RAH B side")],
    "24RI": [Shift("2400h", "RAH I side")],
}

# Validate: every mapped Shift must exist in SHIFT_TO_BLOCK
_unknown = [
    (code, s.code)
    for code, shifts in _CODE_TO_SHIFTS.items()
    for s in shifts
    if s.code not in SHIFT_TO_BLOCK
]
if _unknown:
    raise ValueError(
        f"importer_flat: shift code(s) not found in shifts.py BLOCKS: {_unknown}"
    )

# Precompute: for each block index, the set of canonical shift codes in it.
_BLOCK_SHIFT_CODES: list[frozenset[str]] = [
    frozenset(s.code for s in block)
    for block in BLOCKS
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _to_date(value) -> datetime.date | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _parse_codes(raw: str | None) -> tuple[frozenset[int], frozenset[str], bool, bool]:
    """
    Parse a comma-separated code string into
    (available_blocks, available_shift_codes, doc_available, noc_available).

    available_blocks: block indices where at least one shift is listed.
    available_shift_codes: canonical shift.code strings for matched codes.
    doc_available: True if 'DOC' token is present.
    noc_available: True if 'NOC' token is present.
    """
    if not raw:
        return frozenset(), frozenset(), False, False

    canonical: set[str] = set()
    doc = False
    noc = False
    for token in str(raw).split(","):
        token = token.strip().upper()
        if token == "DOC":
            doc = True
            continue
        if token == "NOC":
            noc = True
            continue
        shifts = _CODE_TO_SHIFTS.get(token)
        if shifts is not None:
            for shift in shifts:
                canonical.add(shift.code)
        # else: unknown codes — silently skipped

    blocks: set[int] = set()
    for block_idx, block_codes in enumerate(_BLOCK_SHIFT_CODES):
        if canonical & block_codes:
            blocks.add(block_idx)

    return frozenset(blocks), frozenset(canonical), doc, noc


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Core parser
# --------------------------------------------------------------------------- #

def _build_submissions(
    ws,
    year: int,
    month: int,
    source_file: str,
) -> list[PhysicianSubmission]:
    """
    Read all rows from the flat worksheet and group into per-physician
    PhysicianSubmission objects for the requested year/month.
    """
    # Collect rows by physician name (preserve insertion order).
    physician_rows: dict[str, list] = {}

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        name = str(row[0]).strip()
        date = _to_date(row[1])
        if date is None or date.year != year or date.month != month:
            continue
        physician_rows.setdefault(name, []).append(row)

    submissions: list[PhysicianSubmission] = []

    for name, rows in physician_rows.items():
        # Per-physician metadata comes from the first row (same for all rows).
        first = rows[0]
        shifts_requested  = _int_or(first[4], 0)
        shifts_min        = _int_or(first[5], shifts_requested)
        shifts_max        = _int_or(first[6], shifts_requested)
        shifts_0600h      = _int_or(first[7], 0)
        shifts_2400h      = _int_or(first[8], 0)

        # Sort rows by date.
        rows_sorted = sorted(rows, key=lambda r: _to_date(r[1]))

        days: list[DayAvailability] = []
        for row in rows_sorted:
            date = _to_date(row[1])
            available = bool(row[2])
            code_str = row[3] if available else None

            available_blocks, available_shift_codes, doc, noc = _parse_codes(code_str)

            days.append(DayAvailability(
                date=date,
                wants_to_work=available,
                available_blocks=available_blocks,
                requested_shifts=available_shift_codes,
                doc_available=doc,
                noc_available=noc,
            ))

        submissions.append(PhysicianSubmission(
            physician_id=name,
            physician_name=name,
            year=year,
            month=month,
            shifts_requested=shifts_requested,
            shifts_min=shifts_min,
            shifts_max=shifts_max,
            shifts_0600h_requested=shifts_0600h,
            shifts_2400h_requested=shifts_2400h,
            days=days,
            source_file=source_file,
        ))

    return submissions


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def import_flat_file(
    path: str | Path,
    year: int,
    month: int,
) -> list[PhysicianSubmission]:
    """
    Import a flat-table Excel file (SingleJune.xlsx style).

    Returns one PhysicianSubmission per physician found for year/month.
    Dates outside the requested month are silently ignored.
    """
    path = Path(path)
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    return _build_submissions(ws, year=year, month=month, source_file=str(path))
