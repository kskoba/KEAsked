"""
Read the master Google Sheet for a given month back into a ScheduleResult,
so it can become the active schedule in KEAsked with the same swap/edit
tools available as any other load (see server.py's _apply_loaded_schedule).

Mirrors server.py's _parse_schedule_xlsx almost exactly -- same grid-walk
algorithm, same EXPORT_SHIFT_LOOKUP/SHIFT_CODE_LOOKUP tables (shifts.py) --
but reads via the Sheets API instead of openpyxl, and resolves physician
identity through physician_resolver.build_alias_index/resolve_physician_id
rather than _parse_schedule_xlsx's weaker local exact-name-match (which
silently fabricates a bogus physician_id on an unresolved name instead of
erroring -- a known inconsistency in that function, not one to repeat
here). An unresolved name in the master Sheet is a real problem (it means
this reader doesn't know who that physician is) and must surface as a
clear error, per physician_resolver's own documented intent.

Never reads the learner-pairing row (one row below each shift's physician
row in the real department file) -- this reader only ever produces
physician-assignment data.
"""

from __future__ import annotations

import datetime
import io
import re

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import (
    Assignment,
    OnCallAssignment,
    ScheduleResult,
    ScheduleStats,
    UnfilledSlot,
)
from scheduler.backend.google_sheets_client import (
    GoogleSheetsApiError,
    GoogleSheetsConfig,
    find_month_file_id,
    read_range,
)
from scheduler.backend.physician_resolver import build_alias_index, resolve_physician_id
from scheduler.backend.shifts import EXPORT_SHIFT_LOOKUP, SHIFT_CODE_LOOKUP

# Generous enough for a 6-week month view (header + date row + ~23 shift
# pairs * 6 weeks + gap rows); read the whole used range in one call rather
# than guessing exact row counts cell-by-cell.
_MAX_ROWS = 400
_MAX_COLS_A1 = "I"


class ScheduleSheetParseError(ValueError):
    pass


_SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_SPREADSHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def spreadsheet_id_from_url(url_or_id: str) -> str:
    """
    The spreadsheet id from a docs.google.com link (any /edit, /view,
    ?gid=... suffix), or the string itself if it already is a bare id.
    Raises ScheduleSheetParseError for anything else.
    """
    text = (url_or_id or "").strip()
    m = _SPREADSHEET_URL_RE.search(text)
    if m:
        return m.group(1)
    if _SPREADSHEET_ID_RE.match(text):
        return text
    raise ScheduleSheetParseError(f"Not a Google Sheets link or spreadsheet id: {url_or_id!r}")


def parse_schedule_from_sheet(
    config: GoogleSheetsConfig, year: int, month: int, roster: dict[str, PhysicianConfig]
) -> ScheduleResult:
    """Locate the month's file in the configured Drive folder and parse it (needs credentials)."""
    file_id = find_month_file_id(config, year, month)
    if not file_id:
        raise ScheduleSheetParseError(
            f"No master-sheet file found for {year}-{month:02d} in the configured Drive folder."
        )
    return parse_schedule_from_spreadsheet_id(config, file_id, year, month, roster)


def parse_schedule_from_spreadsheet_id(
    config: GoogleSheetsConfig, spreadsheet_id: str, year: int, month: int, roster: dict[str, PhysicianConfig]
) -> ScheduleResult:
    """Parse a specific spreadsheet (e.g. from a pasted link) through the Sheets API (needs credentials)."""
    try:
        grid = read_range(config, spreadsheet_id, f"A1:{_MAX_COLS_A1}{_MAX_ROWS}")
    except GoogleSheetsApiError as exc:
        raise ScheduleSheetParseError(f"Could not read master sheet for {year}-{month:02d}: {exc}") from exc
    return parse_schedule_grid(grid, year, month, roster)


def download_public_sheet_grid(spreadsheet_id: str, timeout: float = 30.0) -> list[list]:
    """
    Fetch a spreadsheet's first tab as a grid WITHOUT any Google credentials,
    via the public xlsx export URL. Only works when the sheet is shared as
    "anyone with the link can view" -- otherwise Google answers with a sign-in
    page (HTML, not a workbook), which surfaces here as a clear error rather
    than a confusing parse failure further down. This is the no-setup path
    for reading a prior month's schedule while google_sheets.yaml (service
    account) isn't set up on a machine.
    """
    import httpx
    import openpyxl

    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=xlsx"
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ScheduleSheetParseError(f"Could not download spreadsheet {spreadsheet_id!r}: {exc}") from exc
    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or "html" in ctype.lower():
        raise ScheduleSheetParseError(
            f"Spreadsheet {spreadsheet_id!r} is not publicly readable (HTTP {resp.status_code}, {ctype or 'no content-type'}). "
            "Share it as 'anyone with the link can view', or set up google_sheets.yaml so it can be read with credentials."
        )
    try:
        wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=True, read_only=True)
    except Exception as exc:
        raise ScheduleSheetParseError(f"Downloaded spreadsheet {spreadsheet_id!r} is not a readable workbook: {exc}") from exc
    ws = wb.worksheets[0]
    return [list(row) for row in ws.iter_rows(min_row=1, max_row=_MAX_ROWS, max_col=9, values_only=True)]


def parse_schedule_grid(
    grid: list[list], year: int, month: int, roster: dict[str, PhysicianConfig]
) -> ScheduleResult:
    """
    The grid-walk itself, over rows of cell values as the Sheets API returns
    them (strings, ragged rows) or as openpyxl yields them (typed values,
    None for blanks). Shared by the credentialed and public-link readers.
    """

    def cell(row: list, col: int):
        return row[col] if col < len(row) else None

    index = build_alias_index(roster)

    def resolve_id(name: str) -> str:
        pid = resolve_physician_id(name, index)
        if pid is None:
            raise ScheduleSheetParseError(
                f"Unresolved physician name {name!r} in the {year}-{month:02d} master sheet. "
                "Add an alias to physicians.yaml or correct the sheet before importing."
            )
        return pid

    assignments: list[Assignment] = []
    on_calls: list[OnCallAssignment] = []
    unfilled: list[UnfilledSlot] = []

    i = 0
    n = len(grid)
    while i < n:
        row = grid[i]

        if cell(row, 1) != "SUN":
            i += 1
            continue

        i += 1
        if i >= n:
            break
        date_row = grid[i]
        col_to_date: dict[int, datetime.date] = {}
        for c in range(1, 8):
            val = cell(date_row, c)
            if val not in (None, ""):
                try:
                    col_to_date[c] = datetime.date(year, month, int(val))
                except (ValueError, TypeError):
                    pass

        i += 1
        while i < n:
            site_row = grid[i]
            site_label_raw = cell(site_row, 0)
            if site_label_raw is None or str(site_label_raw).strip() == "":
                i += 1
                break

            i += 1
            if i >= n:
                break
            time_row = grid[i]
            i += 1  # skip the time-range/learner row entirely -- never read its day columns

            site_label = str(site_label_raw).strip()
            time_label_raw = cell(time_row, 0)
            time_label = str(time_label_raw).strip() if time_label_raw is not None else ""

            entry = EXPORT_SHIFT_LOOKUP.get((site_label, time_label))
            if not entry:
                continue
            time_code, site_code = entry

            for c, d in col_to_date.items():
                cell_val = cell(site_row, c)
                name = str(cell_val).strip() if cell_val is not None else ""
                if not name or name in ("---", "None"):
                    if time_code is not None:
                        shift_code = f"{time_code} {site_code}"
                        shift = SHIFT_CODE_LOOKUP.get(shift_code)
                        if shift is not None:
                            unfilled.append(UnfilledSlot(date=d, shift=shift, candidates=[]))
                    continue

                if time_code is None:
                    on_calls.append(
                        OnCallAssignment(
                            date=d, call_type=site_label, physician_id=resolve_id(name), physician_name=name
                        )
                    )
                else:
                    shift_code = f"{time_code} {site_code}"
                    shift = SHIFT_CODE_LOOKUP.get(shift_code)
                    if shift is None:
                        continue
                    assignments.append(
                        Assignment(date=d, shift=shift, physician_id=resolve_id(name), physician_name=name)
                    )

    filled = len(assignments)
    total = filled + len(unfilled)
    group_a = sum(1 for a in assignments if a.shift.site_group.value == "A")
    group_b = filled - group_a

    physician_counts: dict[str, int] = {}
    for a in assignments:
        physician_counts[a.physician_id] = physician_counts.get(a.physician_id, 0) + 1

    nights_by_pid: dict[str, list[datetime.date]] = {}
    for a in assignments:
        if a.shift.time == "2400h":
            nights_by_pid.setdefault(a.physician_id, []).append(a.date)
    physician_singletons: dict[str, int] = {}
    for pid, dates in nights_by_pid.items():
        dates_sorted = sorted(dates)
        count = sum(
            1
            for j, d in enumerate(dates_sorted)
            if not (j > 0 and (d - dates_sorted[j - 1]).days == 1)
            and not (j < len(dates_sorted) - 1 and (dates_sorted[j + 1] - d).days == 1)
        )
        if count:
            physician_singletons[pid] = count

    stats = ScheduleStats(
        total_slots=total,
        filled_slots=filled,
        unfilled_slots=len(unfilled),
        group_a_count=group_a,
        group_b_count=group_b,
        group_a_pct=round(group_a / filled, 3) if filled else 0.0,
        group_b_pct=round(group_b / filled, 3) if filled else 0.0,
        physician_counts=physician_counts,
        physician_singletons=physician_singletons,
        solver_status=None,
        optimality_gap_pct=None,
    )

    return ScheduleResult(
        year=year,
        month=month,
        assignments=assignments,
        unfilled=unfilled,
        issues=[],
        stats=stats,
        on_calls=on_calls,
    )
