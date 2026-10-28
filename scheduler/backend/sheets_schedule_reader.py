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
from scheduler.backend.shifts import CALL_TYPE_BY_LABEL, EXPORT_SHIFT_LOOKUP, SHIFT_CODE_LOOKUP, normalize_label

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


def _week_dates(date_row: list, year: int, month: int, cell) -> dict[int, datetime.date]:
    """
    Column (1..7 = SUN..SAT) -> real date for one week's date row. Cells may
    be ints, floats, digit strings, real datetimes, or blank; the real sheet
    has had a blank date cell on a worked day (Dec 1 2026) and a datetime
    with the WRONG year on the spill-over Jan 1 cell. So: anchor on every
    cell that parses to a day of THIS month, then derive every column from
    a known neighbour by offset (SUN..SAT are consecutive days). Columns
    that land AFTER the month end are returned as real next-month dates --
    the department schedules the partial last week with this month, so the
    caller files those as spill-over. Columns before the month start are
    dropped (they belong to the previous month's sheet).
    """
    import calendar as _cal

    days_in_month = _cal.monthrange(year, month)[1]
    first = datetime.date(year, month, 1)
    known: dict[int, int] = {}
    for c in range(1, 8):
        val = cell(date_row, c)
        if val in (None, ""):
            continue
        day = None
        if isinstance(val, (datetime.datetime, datetime.date)):
            if (val.year, val.month) == (year, month):
                day = val.day
        else:
            try:
                day = int(float(str(val).strip()))
            except (ValueError, TypeError):
                day = None
        if day is not None and 1 <= day <= days_in_month:
            known[c] = day
    if not known:
        return {}
    anchor_col, anchor_day = next(iter(known.items()))
    out: dict[int, datetime.date] = {}
    for c in range(1, 8):
        d = first + datetime.timedelta(days=anchor_day - 1 + (c - anchor_col))
        if d >= first:
            out[c] = d
    return out


# Learner-type colours as on the department's December 2026 sheet (legend
# cells, column C of the first week). The sheet's own LEGEND block wins when
# present; these are the fallback. Hex, lowercase.
DEFAULT_LEARNER_LEGEND: dict[str, str] = {
    "4a86e8": "EM resident",
    "ff0000": "Off service resident",
    "9900ff": "Elective resident",
    "ff00ff": "Elective student",
    "00ff00": "Core Student",
    "ffff00": "Shadow student",
    "d9d2e9": "NP student",
    "9bbb59": "NP",
}
_NO_LEARNER_TEXTS = {"", "no learner", "no learners", "none", "---", "-", "n/a"}


def _rgb(hexs: str) -> tuple[int, int, int]:
    return int(hexs[0:2], 16), int(hexs[2:4], 16), int(hexs[4:6], 16)


def read_legend(values: list[list], fills: list[list]) -> dict[str, str]:
    """
    The sheet's own colour legend: the cell reading LEGEND and the cells
    directly below it, until a blank, each (text, fill) -> {fill: text}.
    Empty dict when the sheet has none.
    """
    for r, row in enumerate(values):
        for c, v in enumerate(row):
            if str(v or "").strip().casefold() != "legend":
                continue
            out: dict[str, str] = {}
            rr = r + 1
            while rr < len(values):
                text = str(values[rr][c] if c < len(values[rr]) else "").strip()
                fill = fills[rr][c] if rr < len(fills) and c < len(fills[rr]) else ""
                if not text:
                    break
                if fill:
                    out[fill] = text
                rr += 1
            return out
    return {}


def learner_type_for(fill: str, legend: dict[str, str], tolerance: int = 90) -> str:
    """Nearest legend colour within tolerance (sum of RGB channel distances), else ''."""
    if not fill:
        return ""
    best, best_d = "", tolerance + 1
    for hexs, label in legend.items():
        try:
            d = sum(abs(a - b) for a, b in zip(_rgb(fill), _rgb(hexs)))
        except ValueError:
            continue
        if d < best_d:
            best, best_d = label, d
    return best


def extract_learners(values: list[list], fills: list[list], year: int, month: int) -> dict[tuple, list[dict]]:
    """
    {("shift"|"call", date, code): [{"name","type","color"}]} for every
    located slot whose learner row (the row under the shift) has a name.
    Text: surname(s), "A/B" = two learners sharing the cell's colour;
    "No learner"/"---"/blank = none. Colour: the cell's fill unless it is
    just the row's own fill (the amber call rows), resolved to a type via
    the sheet's legend, falling back to DEFAULT_LEARNER_LEGEND.
    """
    legend = read_legend(values, fills) or dict(DEFAULT_LEARNER_LEGEND)
    out: dict[tuple, list[dict]] = {}

    def at(grid, r0, c0):
        row = grid[r0] if r0 < len(grid) else []
        return row[c0] if c0 < len(row) else ""

    for key, (sheet_row, col_letter) in locate_cells(values, year, month).items():
        r0 = sheet_row            # learner row is the next row -> 0-based index == sheet_row
        c0 = ord(col_letter) - 65
        text = str(at(values, r0, c0) or "").strip()
        if text.casefold() in _NO_LEARNER_TEXTS:
            continue
        fill = str(at(fills, r0, c0) or "")
        row_fill = str(at(fills, r0, 0) or "")
        if fill and fill == row_fill:
            fill = ""
        ltype = learner_type_for(fill, legend)
        # a stray symbol in a learner cell ("≈" on the real December sheet) is not a learner
        names = [n.strip() for n in text.replace(",", "/").split("/") if any(ch.isalpha() for ch in n)]
        if not names:
            continue
        out[key] = [{"name": n, "type": ltype, "color": f"#{fill}" if fill else ""} for n in names]
    return out


def locate_cells(grid: list[list], year: int, month: int) -> dict[tuple, tuple[int, str]]:
    """
    Where each (date, kind, code) lives in a master-sheet grid, as
    (1-based row, column letter) -- the write-side counterpart of
    parse_schedule_grid, walking the sheet exactly the same way so the
    push lands names wherever THIS sheet keeps them, whether or not it has
    a blank spacer row between weeks (KEAsked's own export does; the
    department's hand-built sheets do not -- the reader learned that the
    hard way on the real December 2026 sheet). Keys: ("shift", date,
    "<time> <site>") for regular shifts, ("call", date, "DOC"|"NOC") for
    the on-call rows. Spill-over dates (next month, last week row) are
    included. The row directly below each shift row (learner pairing) is
    never a key -- it is skipped, same as the reader.
    """

    def cell(row: list, col: int):
        return row[col] if col < len(row) else None

    cols = ["B", "C", "D", "E", "F", "G", "H"]
    out: dict[tuple, tuple[int, str]] = {}
    i, n = 0, len(grid)
    while i < n:
        if cell(grid[i], 1) != "SUN":
            i += 1
            continue
        i += 1
        if i >= n:
            break
        col_to_date = _week_dates(grid[i], year, month, cell)
        i += 1
        while i < n:
            site_row = grid[i]
            site_label_raw = cell(site_row, 0)
            if site_label_raw is None or str(site_label_raw).strip() == "":
                break
            sheet_row = i + 1
            i += 1
            if i >= n:
                break
            time_row = grid[i]
            i += 1
            entry = EXPORT_SHIFT_LOOKUP.get((normalize_label(site_label_raw), normalize_label(cell(time_row, 0))))
            if not entry:
                continue
            time_code, site_code = entry
            for c, d in col_to_date.items():
                if time_code is None:
                    key = ("call", d, CALL_TYPE_BY_LABEL.get(normalize_label(site_label_raw), normalize_label(site_label_raw)))
                else:
                    key = ("shift", d, f"{time_code} {site_code}")
                out[key] = (sheet_row, cols[c - 1])
    return out


def parse_schedule_grid(
    grid: list[list], year: int, month: int, roster: dict[str, PhysicianConfig],
    unresolved: list[str] | None = None,
) -> ScheduleResult:
    """
    The grid-walk itself, over rows of cell values as the Sheets API returns
    them (strings, ragged rows) or as openpyxl yields them (typed values,
    None for blanks). Shared by the credentialed and public-link readers.

    unresolved: pass a list to be TOLERANT of names that match no roster
    physician -- they are appended here (deduplicated, in sheet order) and
    the cell is skipped, instead of the whole parse failing. The default
    (None) keeps the strict behaviour every existing caller relies on.
    """

    def cell(row: list, col: int):
        return row[col] if col < len(row) else None

    index = build_alias_index(roster)

    def resolve_id(name: str) -> str | None:
        pid = resolve_physician_id(name, index)
        if pid is None:
            if unresolved is not None:
                if name not in unresolved:
                    unresolved.append(name)
                return None
            raise ScheduleSheetParseError(
                f"Unresolved physician name {name!r} in the {year}-{month:02d} master sheet. "
                "Add an alias to physicians.yaml or correct the sheet before importing."
            )
        return pid

    assignments: list[Assignment] = []
    on_calls: list[OnCallAssignment] = []
    unfilled: list[UnfilledSlot] = []
    spillover: list[Assignment] = []
    spillover_on_calls: list[OnCallAssignment] = []

    def _in_month(d: datetime.date) -> bool:
        return (d.year, d.month) == (year, month)

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
        col_to_date = _week_dates(grid[i], year, month, cell)

        i += 1
        while i < n:
            site_row = grid[i]
            site_label_raw = cell(site_row, 0)
            if site_label_raw is None or str(site_label_raw).strip() == "":
                # End of this week's block. Do NOT consume the row: in the
                # real department sheet there is no blank spacer row between
                # weeks, so this empty-col-A row IS the next week's SUN
                # header, and the outer loop must get to see it. (Swallowing
                # it here silently dropped every other week -- confirmed on
                # the real December 2026 sheet, 2026-10-07.)
                break

            i += 1
            if i >= n:
                break
            time_row = grid[i]
            i += 1  # skip the time-range/learner row entirely -- never read its day columns

            site_label = normalize_label(site_label_raw)
            time_label = normalize_label(cell(time_row, 0))

            entry = EXPORT_SHIFT_LOOKUP.get((site_label, time_label))
            if not entry:
                continue
            time_code, site_code = entry

            for c, d in col_to_date.items():
                cell_val = cell(site_row, c)
                name = str(cell_val).strip() if cell_val is not None else ""
                if not name or name in ("---", "None"):
                    if time_code is not None and _in_month(d):
                        shift_code = f"{time_code} {site_code}"
                        shift = SHIFT_CODE_LOOKUP.get(shift_code)
                        if shift is not None:
                            unfilled.append(UnfilledSlot(date=d, shift=shift, candidates=[]))
                    continue

                pid = resolve_id(name)
                if pid is None:
                    continue
                if time_code is None:
                    oc = OnCallAssignment(
                        date=d, call_type=CALL_TYPE_BY_LABEL.get(site_label, site_label),
                        physician_id=pid, physician_name=name,
                    )
                    (on_calls if _in_month(d) else spillover_on_calls).append(oc)
                else:
                    shift_code = f"{time_code} {site_code}"
                    shift = SHIFT_CODE_LOOKUP.get(shift_code)
                    if shift is None:
                        continue
                    a = Assignment(date=d, shift=shift, physician_id=pid, physician_name=name)
                    (assignments if _in_month(d) else spillover).append(a)

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
        spillover=spillover,
        spillover_on_calls=spillover_on_calls,
    )
