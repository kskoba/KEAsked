#!/usr/bin/env python3
"""
compare_schedules.py — Side-by-side comparison of two KEA Physician Scheduler
Excel output files.

Usage:
    python tools/compare_schedules.py file1.xlsx file2.xlsx
    python tools/compare_schedules.py file1.xlsx file2.xlsx --names "CP-SAT" "Human"
    python tools/compare_schedules.py file1.xlsx file2.xlsx --no-color

Format expected:
    The Excel files use the KEA grid layout:
      - Repeating week blocks separated by blank rows
      - Each week block starts with a header row (col B = "SUN") and a date row
      - Within each week block, rows alternate: shift-name row / time-code row
      - Columns B-H correspond to SUN-SAT; the day number appears in the dates row
      - Physician name (or None) appears in the shift-name rows under each date column
      - Col A (and mirrored in col I or J) = shift label or time code
    Group A:  RAH A, RAH B  (target 38%)
    Group B:  RAH I, NECHC/NEHC, RAH Float  (target 62%)
    2400h shifts: time code starts with "24" (2400-0600, 2400-0800)
"""

# ---------------------------------------------------------------------------
# Requirements check
# ---------------------------------------------------------------------------
import sys

def _check_requirements():
    missing = []
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        missing.append("openpyxl")
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("PyYAML")
    if missing:
        print(f"ERROR: Missing required packages: {', '.join(missing)}")
        print(f"Install with: pip install {' '.join(missing)}")
        sys.exit(1)

_check_requirements()

# ---------------------------------------------------------------------------
# Standard imports
# ---------------------------------------------------------------------------
import argparse
import io
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import openpyxl
import yaml

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------
_USE_COLOR = True

_RESET  = "\033[0m"
_RED    = "\033[31m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"


def _c(text: str, *codes: str) -> str:
    if not _USE_COLOR:
        return text
    return "".join(codes) + str(text) + _RESET


def red(t):    return _c(t, _RED)
def green(t):  return _c(t, _GREEN)
def yellow(t): return _c(t, _YELLOW)
def bold(t):   return _c(t, _BOLD)
def cyan(t):   return _c(t, _CYAN)
def dim(t):    return _c(t, _DIM)


# ---------------------------------------------------------------------------
# Physician config helpers
# ---------------------------------------------------------------------------

_PHYSICIANS_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scheduler", "config", "physicians.yaml",
)
_DEFAULT_MAX_CONSEC = 3


def load_physician_max_consec(yaml_path: str = _PHYSICIANS_YAML) -> dict[str, int]:
    """
    Load physicians.yaml and return a dict mapping physician name ->
    scheduling.max_consecutive_shifts (defaults to _DEFAULT_MAX_CONSEC if the
    field is absent or the file cannot be read).
    """
    result: dict[str, int] = {}
    try:
        with open(yaml_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        for phys in data.get("physicians", []):
            name = phys.get("name") or phys.get("id")
            if not name:
                continue
            max_c = (phys.get("scheduling") or {}).get(
                "max_consecutive_shifts", _DEFAULT_MAX_CONSEC
            )
            result[name] = int(max_c)
    except Exception:
        pass  # silently fall back to defaults
    return result


# ---------------------------------------------------------------------------
# Site-group mapping
# Group A = RAH A / RAH B (target 38%)
# Group B = RAH I / NECHC / RAH Float (target 62%)
# DOC / NOC / AM CALL / PM CALL / DAY ON CALL / NIGHT ON CALL = on-call (not grouped)
# ---------------------------------------------------------------------------
_GROUP_A_PATTERNS = re.compile(r"^rah\s*[ab]\b", re.IGNORECASE)
_GROUP_B_PATTERNS = re.compile(r"^(rah\s*i\b|nechc|nehc|rah\s*float)", re.IGNORECASE)
_CALL_PATTERNS    = re.compile(r"(doc|noc|am\s*call|pm\s*call|day\s*on\s*call|night\s*on\s*call)", re.IGNORECASE)
_TIME_CODE_RE     = re.compile(r"^\s*\d{4}[-–]\d{4}", )   # e.g. "0600-1200", "2400-0800"
_ONCALL_TIME_RE   = re.compile(r"^(day on call|night on call)$", re.IGNORECASE)
_IS_2400          = re.compile(r"^24")


def site_group(shift_name: str) -> Optional[str]:
    """Return 'A', 'B', 'call', or None (unknown)."""
    s = shift_name.strip()
    if _GROUP_A_PATTERNS.match(s):
        return "A"
    if _GROUP_B_PATTERNS.match(s):
        return "B"
    if _CALL_PATTERNS.search(s):
        return "call"
    return None


def is_time_code(val: str) -> bool:
    s = str(val).strip()
    return bool(_TIME_CODE_RE.match(s)) or bool(_ONCALL_TIME_RE.match(s))


def is_2400(time_code: str) -> bool:
    return bool(_IS_2400.match(str(time_code).strip()))


# ---------------------------------------------------------------------------
# Name normalisation for cross-schedule matching
# ---------------------------------------------------------------------------

def canon_name(name: str) -> str:
    """
    Canonical key for matching physician names across schedules.

    Handles:
      - Case differences:      "ZHANG"  == "Zhang"
      - Reversed order:        "Brown T" == "TBrown"
      - CamelCase vs spaced:   "LamRico" == "Lam-Rico"
      - Hyphen / space:        "Lam-Rico" == "Lam Rico"

    Algorithm: split into tokens on whitespace, hyphens, underscores, and
    camelCase boundaries; lowercase each token; sort alphabetically; join.
    """
    if not name:
        return ""
    # Split on explicit separators first
    parts = re.split(r'[\s\-_]+', name.strip())
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.isupper() or part.islower():
            # All-caps (e.g. "ZHANG") or all-lowercase — treat as single token
            tokens.append(part.lower())
        else:
            # Mixed case — split on camelCase: "TBrown" → ["T", "Brown"]
            sub = re.findall(r'[A-Z][a-z]*|[A-Z]+(?=[A-Z]|$)|[a-z]+', part)
            tokens.extend(t.lower() for t in sub) if sub else tokens.append(part.lower())
    return ''.join(sorted(tokens))


def unify_physician_names(s1: 'Schedule', s2: 'Schedule') -> None:
    """
    Remap physician names in both schedules so that equivalent names
    (same canonical key) resolve to the same display string.

    The display name chosen is the one from *s2* if it appears there
    (typically the human reference), otherwise the one from *s1*.
    Both schedules' assignments are mutated in place.
    """
    # Build canon_key → best display name (prefer s2)
    canon_to_display: dict[str, str] = {}
    for a in s1.assignments:
        key = canon_name(a.physician)
        if key and key not in canon_to_display:
            canon_to_display[key] = a.physician
    for a in s2.assignments:
        key = canon_name(a.physician)
        if key:
            canon_to_display[key] = a.physician  # s2 overwrites, so s2 name wins

    # Remap all assignments in both schedules
    for a in s1.assignments:
        key = canon_name(a.physician)
        if key in canon_to_display:
            a.physician = canon_to_display[key]
    for a in s2.assignments:
        key = canon_name(a.physician)
        if key in canon_to_display:
            a.physician = canon_to_display[key]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Assignment:
    date: int           # day-of-month (1-31)
    shift_name: str     # e.g. "RAH A", "NECHC"
    time_code: str      # e.g. "0600-1200", "2400-0600"
    physician: str      # name as it appears in the cell
    group: Optional[str] = None  # 'A', 'B', 'call'


@dataclass
class Schedule:
    name: str
    source: str
    assignments: list[Assignment] = field(default_factory=list)
    # Dates that appear in the file (to compute total possible slots)
    all_dates: set[int] = field(default_factory=set)
    # Shift slots (date, shift_name, time_code) seen in the file (filled or not)
    all_slots: list[tuple[int, str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _normalise_date(raw) -> Optional[int]:
    """Convert a date cell value to int day-of-month, or None."""
    if raw is None:
        return None
    try:
        v = int(float(str(raw)))
        if 1 <= v <= 31:
            return v
    except (ValueError, TypeError):
        pass
    return None


_PLACEHOLDER_RE = re.compile(r"^[-=_*#.]{2,}$")  # rows of dashes/equals used as separators

def _normalise_name(raw) -> Optional[str]:
    """Return physician name or None if cell is empty or is a placeholder/separator."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Skip visual separator strings like "---", "====", "***", etc.
    if _PLACEHOLDER_RE.match(s):
        return None
    return s


# Equivalent shift names across schedule formats.
# DOC/NOC (AI export) == AM CALL/PM CALL (human schedule) — same row position, same meaning.
_SHIFT_NAME_ALIASES: dict[str, str] = {
    "DOC": "AM CALL",
    "NOC": "PM CALL",
}


def parse_schedule(path: str, name: str) -> Schedule:
    """
    Parse a KEA scheduler XLSX output file into a Schedule object.

    Layout (per week block):
        Row k+0: header  — col[1]="SUN", col[2]="MON" ... col[7]="SAT"
        Row k+1: dates   — col[1..7] = day numbers (int or None)
        Row k+2: shift   — col[0]=shift_name, col[1..7]=physician or None
        Row k+3: time    — col[0]=time_code (e.g. "0600-1200"), rest None
        ... repeats for each shift in the week ...
        Row k+n: blank   — separates weeks
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = [tuple(cell for cell in row) for row in ws.iter_rows(values_only=True)]

    schedule = Schedule(name=name, source=path)

    # Find week-block start rows: row where col[1] == "SUN" (header row)
    week_starts = [i for i, r in enumerate(rows) if len(r) > 1 and str(r[1]).strip().upper() == "SUN"]

    for ws_idx, week_start in enumerate(week_starts):
        # dates row is immediately after the header row
        date_row_idx = week_start + 1
        if date_row_idx >= len(rows):
            continue
        date_row = rows[date_row_idx]

        # Determine end of this week block
        if ws_idx + 1 < len(week_starts):
            week_end = week_starts[ws_idx + 1]
        else:
            week_end = len(rows)

        # Build mapping: column_index -> day_number
        # Columns 1..7 = SUN..SAT
        col_to_date: dict[int, int] = {}
        for col_idx in range(1, 8):
            if col_idx < len(date_row):
                d = _normalise_date(date_row[col_idx])
                if d is not None:
                    col_to_date[col_idx] = d
                    schedule.all_dates.add(d)

        if not col_to_date:
            continue  # empty/trailing week block

        # Walk assignment rows within this week block (starting after the date row)
        data_start = date_row_idx + 1
        i = data_start
        while i < week_end:
            row = rows[i]
            if not row or len(row) < 2:
                i += 1
                continue

            cell0 = str(row[0]).strip() if row[0] is not None else ""

            # Skip blank rows
            if not cell0:
                i += 1
                continue

            # Skip pure time-code rows (e.g. "0600-1200")
            if is_time_code(cell0):
                i += 1
                continue

            # This should be a shift-name row: col[0] = shift name
            # Normalise equivalent names (e.g. DOC == AM CALL, NOC == PM CALL).
            shift_name = _SHIFT_NAME_ALIASES.get(cell0, cell0)

            # Look ahead for the time-code row (should be i+1)
            time_code = ""
            if i + 1 < week_end:
                next_row = rows[i + 1]
                next0 = str(next_row[0]).strip() if next_row and next_row[0] is not None else ""
                if is_time_code(next0):
                    time_code = next0.strip()

            grp = site_group(shift_name)

            # Read physician assignments for each valid date column
            for col_idx, day in col_to_date.items():
                if col_idx < len(row):
                    physician = _normalise_name(row[col_idx])
                else:
                    physician = None

                # Record this slot (regardless of whether it's filled)
                schedule.all_slots.append((day, shift_name, time_code))

                if physician:
                    schedule.assignments.append(Assignment(
                        date=day,
                        shift_name=shift_name,
                        time_code=time_code,
                        physician=physician,
                        group=grp,
                    ))

            i += 1  # move to next row (the time-code row is skipped on next iteration)

    return schedule


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def fill_rate(schedule: Schedule) -> tuple[int, int]:
    """Returns (filled, total) slot counts."""
    total = len(schedule.all_slots)
    filled = len(schedule.assignments)
    return filled, total


def unfilled_slots(schedule: Schedule) -> list[tuple[int, str, str]]:
    """
    Returns list of (date, shift_name, time_code) for slots that have no
    physician assigned.
    """
    filled_keys: set[tuple[int, str, str]] = set()
    for a in schedule.assignments:
        filled_keys.add((a.date, a.shift_name, a.time_code))

    missing = []
    seen = set()
    for slot in schedule.all_slots:
        key = slot
        if key not in filled_keys and key not in seen:
            missing.append(slot)
            seen.add(key)
    missing.sort()
    return missing


def physician_shift_counts(schedule: Schedule) -> dict[str, int]:
    """Returns {physician: total_shift_count}."""
    counts: dict[str, int] = defaultdict(int)
    for a in schedule.assignments:
        counts[a.physician] += 1
    return dict(counts)


def physician_group_counts(schedule: Schedule) -> dict[str, dict[str, int]]:
    """Returns {physician: {'A': n, 'B': n, 'call': n}}."""
    result: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in schedule.assignments:
        g = a.group or "other"
        result[a.physician][g] += 1
    return {k: dict(v) for k, v in result.items()}


def singleton_count(schedule: Schedule) -> tuple[int, dict[str, list[int]]]:
    """
    Count isolated 2400h shifts: a 2400h assignment on date D for physician P
    is a singleton if P has no 2400h assignment on D-1 and no 2400h assignment
    on D+1.

    Returns (total_singleton_count, {physician: [singleton_dates]})
    """
    # Build {physician: set_of_2400h_dates}
    nights: dict[str, set[int]] = defaultdict(set)
    for a in schedule.assignments:
        if is_2400(a.time_code):
            nights[a.physician].add(a.date)

    singletons: dict[str, list[int]] = {}
    total = 0
    for phys, dates in nights.items():
        solo_dates = sorted(
            d for d in dates
            if (d - 1) not in dates and (d + 1) not in dates
        )
        if solo_dates:
            singletons[phys] = solo_dates
            total += len(solo_dates)
    return total, singletons


def any_singleton_count(schedule: Schedule) -> tuple[int, dict[str, list[int]]]:
    """
    Count isolated working days (any shift type): a day D worked by physician P
    is a singleton if P has no assignment on D-1 and no assignment on D+1.

    Returns (total_singleton_count, {physician: [singleton_dates]})
    """
    worked: dict[str, set[int]] = defaultdict(set)
    for a in schedule.assignments:
        worked[a.physician].add(a.date)

    singletons: dict[str, list[int]] = {}
    total = 0
    for phys, dates in worked.items():
        solo_dates = sorted(
            d for d in dates
            if (d - 1) not in dates and (d + 1) not in dates
        )
        if solo_dates:
            singletons[phys] = solo_dates
            total += len(solo_dates)
    return total, singletons


def consecutive_run_violations(
    schedule: Schedule,
    max_consecutive: int = 3,
    physician_max_consec: Optional[dict[str, int]] = None,
) -> dict[str, list[tuple[int, int, int]]]:
    """
    Find physicians working more consecutive days than their personal limit.

    If physician_max_consec is provided, each physician's limit is looked up
    from that dict (falling back to max_consecutive if not present).  Otherwise
    max_consecutive is used as a global threshold for all physicians.

    Returns {physician: [(start_date, end_date, run_length), ...]}
    """
    # Build {physician: sorted list of worked dates}
    worked: dict[str, set[int]] = defaultdict(set)
    for a in schedule.assignments:
        worked[a.physician].add(a.date)

    violations: dict[str, list[tuple[int, int, int]]] = {}
    for phys, dates in worked.items():
        # Resolve this physician's personal cap
        if physician_max_consec is not None:
            # Try exact match, then canonical-name lookup
            phys_cap = physician_max_consec.get(phys)
            if phys_cap is None:
                key = canon_name(phys)
                phys_cap = next(
                    (v for k, v in physician_max_consec.items() if canon_name(k) == key),
                    max_consecutive,
                )
        else:
            phys_cap = max_consecutive

        sorted_dates = sorted(dates)
        runs = []
        run_start = sorted_dates[0]
        run_end = sorted_dates[0]
        for d in sorted_dates[1:]:
            if d == run_end + 1:
                run_end = d
            else:
                length = run_end - run_start + 1
                if length > phys_cap:
                    runs.append((run_start, run_end, length))
                run_start = d
                run_end = d
        # Check final run
        length = run_end - run_start + 1
        if length > phys_cap:
            runs.append((run_start, run_end, length))
        if runs:
            violations[phys] = runs
    return violations


def overall_group_split(schedule: Schedule) -> tuple[int, int]:
    """Returns (group_A_count, group_B_count)."""
    a = sum(1 for x in schedule.assignments if x.group == "A")
    b = sum(1 for x in schedule.assignments if x.group == "B")
    return a, b


def ab_ratio_per_physician(schedule: Schedule) -> dict[str, tuple[int, int, float]]:
    """
    Returns {physician: (A_count, B_count, A_fraction)} for physicians with
    at least one A or B shift.
    """
    gc = physician_group_counts(schedule)
    result = {}
    for phys, counts in gc.items():
        a = counts.get("A", 0)
        b = counts.get("B", 0)
        if a + b > 0:
            result[phys] = (a, b, a / (a + b))
    return result


def _rmse(values: list[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def ab_rmse(schedule: Schedule, target_a: float = 0.38) -> float:
    """
    RMSE of per-physician A-fraction deviation from the target.
    Only physicians with >= 2 total A+B shifts are included.
    """
    ratios = ab_ratio_per_physician(schedule)
    deviations = [
        frac - target_a
        for (a, b, frac) in ratios.values()
        if (a + b) >= 2
    ]
    return _rmse(deviations)


def shift_count_rmse(s1: Schedule, s2: Schedule) -> tuple[float, float]:
    """
    For physicians appearing in both schedules, compute RMSE of
    (actual_shifts - median_requested) from each schedule.

    Since we don't have requested counts in the xlsx, we use the
    per-physician mean across both schedules as the "expected" baseline.
    Returns (rmse_s1, rmse_s2).
    """
    c1 = physician_shift_counts(s1)
    c2 = physician_shift_counts(s2)
    all_phys = set(c1.keys()) | set(c2.keys())
    if not all_phys:
        return 0.0, 0.0

    dev1, dev2 = [], []
    for p in all_phys:
        n1 = c1.get(p, 0)
        n2 = c2.get(p, 0)
        mean = (n1 + n2) / 2.0
        dev1.append(n1 - mean)
        dev2.append(n2 - mean)
    return _rmse(dev1), _rmse(dev2)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _truncate(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    return s[:width - 1] + "~"


def _pct(n: int, d: int) -> str:
    if d == 0:
        return "N/A"
    return f"{100.0 * n / d:.1f}%"


def _bar(n: int, total: int, width: int = 20) -> str:
    if total == 0:
        return " " * width
    filled_w = round(width * n / total)
    return "#" * filled_w + "." * (width - filled_w)


def _col_width(label: str, v1: str, v2: str, min_w: int = 8) -> int:
    return max(min_w, len(label), len(v1), len(v2))


def _hdr(title: str, width: int = 78) -> str:
    return bold(f"\n{'-' * width}\n  {title}\n{'-' * width}")


def _row2(label: str, v1: str, v2: str,
          label_w: int = 28, val_w: int = 22) -> str:
    return f"  {label:<{label_w}} {v1:<{val_w}} {v2}"


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def _section_fill_rate(s1: Schedule, s2: Schedule) -> str:
    f1, t1 = fill_rate(s1)
    f2, t2 = fill_rate(s2)
    p1, p2 = _pct(f1, t1), _pct(f2, t2)

    # Colour: higher fill% is better
    pct1_f = f1 / t1 if t1 else 0
    pct2_f = f2 / t2 if t2 else 0
    if abs(pct1_f - pct2_f) < 0.001:
        cp1, cp2 = p1, p2
    elif pct1_f > pct2_f:
        cp1, cp2 = green(p1), red(p2)
    else:
        cp1, cp2 = red(p1), green(p2)

    lines = [_hdr("FILL RATE")]
    lines.append(_row2("Filled / Total", f"{f1}/{t1} ({cp1})", f"{f2}/{t2} ({cp2})"))
    return "\n".join(lines)


def _section_unfilled(s1: Schedule, s2: Schedule) -> str:
    u1 = unfilled_slots(s1)
    u2 = unfilled_slots(s2)
    lines = [_hdr("UNFILLED SLOTS")]

    cnt1 = str(len(u1))
    cnt2 = str(len(u2))
    if len(u1) < len(u2):
        cnt1 = green(cnt1)
        cnt2 = red(cnt2)
    elif len(u1) > len(u2):
        cnt1 = red(cnt1)
        cnt2 = green(cnt2)

    lines.append(_row2("Count", cnt1, cnt2))

    # List unfilled slots side-by-side
    max_show = 40
    def _fmt_slot(s):
        date, shift, time = s
        return f"  Day {date:>2}  {_truncate(shift, 12):<12}  {time}"

    lines.append("")
    w = 38
    for i in range(max(len(u1), len(u2))):
        if i >= max_show:
            remaining = max(len(u1), len(u2)) - max_show
            lines.append(f"  {yellow(f'... {remaining} more ...')}")
            break
        left  = _fmt_slot(u1[i]) if i < len(u1) else ""
        right = _fmt_slot(u2[i]) if i < len(u2) else ""
        lines.append(f"  {left:<{w}}  {right}")

    return "\n".join(lines)


def _section_physician_shifts(s1: Schedule, s2: Schedule) -> str:
    """Per-physician shift counts; flags large deviations."""
    c1 = physician_shift_counts(s1)
    c2 = physician_shift_counts(s2)
    all_phys = sorted(set(c1.keys()) | set(c2.keys()), key=str.lower)

    lines = [_hdr("PER-PHYSICIAN SHIFT COUNTS")]
    lines.append(_row2("Physician", s1.name, s2.name, 24, 10))
    lines.append("  " + "-" * 46)

    for phys in all_phys:
        n1 = c1.get(phys, 0)
        n2 = c2.get(phys, 0)
        diff = n1 - n2

        sv1 = str(n1) if n1 else dim("-")
        sv2 = str(n2) if n2 else dim("-")

        if diff > 1:
            sv1 = green(str(n1))
            sv2 = red(str(n2))
        elif diff < -1:
            sv1 = red(str(n1))
            sv2 = green(str(n2))

        diff_str = ""
        if diff > 0:
            diff_str = green(f"+{diff}")
        elif diff < 0:
            diff_str = red(str(diff))
        else:
            diff_str = dim("=")

        lines.append(f"  {_truncate(phys, 22):<22}  {sv1:<6}  {sv2:<6}  {diff_str}")

    return "\n".join(lines)


def _section_ab_ratio(s1: Schedule, s2: Schedule, target_a: float = 0.38) -> str:
    r1 = ab_ratio_per_physician(s1)
    r2 = ab_ratio_per_physician(s2)
    all_phys = sorted(set(r1.keys()) | set(r2.keys()), key=str.lower)

    # Sort by worst deviation from target (average across both)
    def _dev(phys):
        d1 = abs(r1[phys][2] - target_a) if phys in r1 else 0
        d2 = abs(r2[phys][2] - target_a) if phys in r2 else 0
        return -(d1 + d2) / 2   # negative for descending sort

    all_phys.sort(key=_dev)

    lines = [_hdr(f"GROUP A:B RATIO PER PHYSICIAN  (target A={target_a*100:.0f}% / B={100-target_a*100:.0f}%)")]
    lines.append(_row2("Physician", f"{s1.name} A:B (A%)", f"{s2.name} A:B (A%)", 24, 18))
    lines.append("  " + "-" * 60)

    for phys in all_phys:
        def _fmt_ratio(r, target):
            if not r:
                return dim("-")
            a, b, frac = r
            s = f"{a}:{b} ({frac*100:.0f}%)"
            if abs(frac - target) > 0.15:
                return red(s)
            if abs(frac - target) > 0.08:
                return yellow(s)
            return green(s)

        v1 = _fmt_ratio(r1.get(phys), target_a)
        v2 = _fmt_ratio(r2.get(phys), target_a)
        lines.append(f"  {_truncate(phys, 22):<22}  {v1:<28}  {v2}")

    return "\n".join(lines)


def _section_singletons(s1: Schedule, s2: Schedule) -> str:
    tot1, sing1 = singleton_count(s1)
    tot2, sing2 = singleton_count(s2)

    lines = [_hdr("SINGLETON 2400h NIGHTS  (isolated: no adjacent 2400h for same physician)")]

    s_tot1 = str(tot1)
    s_tot2 = str(tot2)
    if tot1 < tot2:
        s_tot1 = green(s_tot1)
        s_tot2 = red(s_tot2)
    elif tot1 > tot2:
        s_tot1 = red(s_tot1)
        s_tot2 = green(s_tot2)

    lines.append(_row2("Total singletons", s_tot1, s_tot2))
    lines.append("")

    all_phys = sorted(set(sing1.keys()) | set(sing2.keys()), key=str.lower)
    if not all_phys:
        lines.append("  (none)")
    else:
        lines.append(_row2("Physician", f"{s1.name} days", f"{s2.name} days", 24, 20))
        lines.append("  " + "-" * 54)
        for phys in all_phys:
            d1 = ", ".join(str(d) for d in sing1.get(phys, []))
            d2 = ", ".join(str(d) for d in sing2.get(phys, []))
            v1 = red(d1) if d1 else dim("-")
            v2 = red(d2) if d2 else dim("-")
            lines.append(f"  {_truncate(phys, 22):<22}  {v1:<30}  {v2}")

    return "\n".join(lines)


def _section_any_singletons(s1: Schedule, s2: Schedule) -> str:
    tot1, sing1 = any_singleton_count(s1)
    tot2, sing2 = any_singleton_count(s2)

    lines = [_hdr("SINGLETON WORKING DAYS  (any shift: no adjacent working day for same physician)")]

    s_tot1 = str(tot1)
    s_tot2 = str(tot2)
    if tot1 < tot2:
        s_tot1 = green(s_tot1)
        s_tot2 = red(s_tot2)
    elif tot1 > tot2:
        s_tot1 = red(s_tot1)
        s_tot2 = green(s_tot2)

    lines.append(_row2("Total isolated days", s_tot1, s_tot2))

    # Breakdown: how many physicians have ≥1 singleton working day
    phys1 = sum(1 for v in sing1.values() if v)
    phys2 = sum(1 for v in sing2.values() if v)
    lines.append(_row2("Physicians affected", str(phys1), str(phys2)))
    lines.append("")

    all_phys = sorted(set(sing1.keys()) | set(sing2.keys()), key=str.lower)
    if not all_phys:
        lines.append("  (none)")
    else:
        lines.append(_row2("Physician", f"{s1.name} days", f"{s2.name} days", 24, 20))
        lines.append("  " + "-" * 54)
        for phys in all_phys:
            d1 = ", ".join(str(d) for d in sing1.get(phys, []))
            d2 = ", ".join(str(d) for d in sing2.get(phys, []))
            v1 = red(d1) if d1 else dim("-")
            v2 = red(d2) if d2 else dim("-")
            lines.append(f"  {_truncate(phys, 22):<22}  {v1:<30}  {v2}")

    return "\n".join(lines)


def _section_consecutive(s1: Schedule, s2: Schedule, max_consec: int = 3) -> str:
    v1 = consecutive_run_violations(s1, max_consec)
    v2 = consecutive_run_violations(s2, max_consec)

    lines = [_hdr(f"CONSECUTIVE RUN VIOLATIONS  (max {max_consec} days)")]

    cnt1 = sum(len(runs) for runs in v1.values())
    cnt2 = sum(len(runs) for runs in v2.values())

    s_cnt1 = str(cnt1)
    s_cnt2 = str(cnt2)
    if cnt1 < cnt2:
        s_cnt1 = green(s_cnt1)
        s_cnt2 = red(s_cnt2)
    elif cnt1 > cnt2:
        s_cnt1 = red(s_cnt1)
        s_cnt2 = green(s_cnt2)

    lines.append(_row2("Total violations", s_cnt1, s_cnt2))

    all_phys = sorted(set(v1.keys()) | set(v2.keys()), key=str.lower)
    if not all_phys:
        lines.append("  " + green("No violations found."))
    else:
        lines.append("")
        for phys in all_phys:
            def _fmt_runs(runs):
                if not runs:
                    return dim("-")
                parts = [f"days {r[0]}–{r[1]} ({r[2]}d)" for r in runs]
                return red(", ".join(parts))
            lines.append(f"  {_truncate(phys, 22):<22}  {_fmt_runs(v1.get(phys, []))}")
            lines.append(f"  {'':22}  {s1.name}: {_fmt_runs(v1.get(phys, [])):<40}  {s2.name}: {_fmt_runs(v2.get(phys, []))}")

    return "\n".join(lines)


def _section_overall_split(s1: Schedule, s2: Schedule, target_a: float = 0.38) -> str:
    a1, b1 = overall_group_split(s1)
    a2, b2 = overall_group_split(s2)

    def _pct_split(a, b):
        total = a + b
        if total == 0:
            return "N/A", "N/A"
        return f"{100.0*a/total:.1f}%", f"{100.0*b/total:.1f}%"

    pa1, pb1 = _pct_split(a1, b1)
    pa2, pb2 = _pct_split(a2, b2)

    def _colour_pct(pct_str, target):
        try:
            v = float(pct_str.rstrip("%")) / 100
            if abs(v - target) <= 0.03:
                return green(pct_str)
            if abs(v - target) <= 0.07:
                return yellow(pct_str)
            return red(pct_str)
        except ValueError:
            return pct_str

    lines = [_hdr(f"OVERALL GROUP A:B SPLIT  (target A={target_a*100:.0f}% / B={100-target_a*100:.0f}%)")]
    lines.append(_row2("Group A shifts", f"{a1} ({_colour_pct(pa1, target_a)})", f"{a2} ({_colour_pct(pa2, target_a)})"))
    lines.append(_row2("Group B shifts", f"{b1} ({_colour_pct(pb1, 1-target_a)})", f"{b2} ({_colour_pct(pb2, 1-target_a)})"))
    lines.append(_row2("A:B ratio", f"{a1}:{b1}", f"{a2}:{b2}"))
    return "\n".join(lines)


def _section_summary(s1: Schedule, s2: Schedule, target_a: float = 0.38) -> str:
    """Compact diff table of key metrics."""
    f1, t1 = fill_rate(s1)
    f2, t2 = fill_rate(s2)
    u1 = len(unfilled_slots(s1))
    u2 = len(unfilled_slots(s2))
    sing1, _ = singleton_count(s1)
    sing2, _ = singleton_count(s2)
    rmse1 = ab_rmse(s1, target_a)
    rmse2 = ab_rmse(s2, target_a)
    sc_rmse1, sc_rmse2 = shift_count_rmse(s1, s2)

    fill1_pct = f1 / t1 if t1 else 0
    fill2_pct = f2 / t2 if t2 else 0

    def _better(val1, val2, higher_is_better=True):
        """Return (coloured_v1, coloured_v2)."""
        if abs(val1 - val2) < 1e-6:
            return f"{val1:.3f}", f"{val2:.3f}"
        if higher_is_better:
            better, worse = (val1, val2) if val1 > val2 else (val2, val1)
            cv1 = green(f"{val1:.3f}") if val1 == better else red(f"{val1:.3f}")
            cv2 = green(f"{val2:.3f}") if val2 == better else red(f"{val2:.3f}")
        else:
            better, worse = (val1, val2) if val1 < val2 else (val2, val1)
            cv1 = green(f"{val1:.3f}") if val1 == better else red(f"{val1:.3f}")
            cv2 = green(f"{val2:.3f}") if val2 == better else red(f"{val2:.3f}")
        return cv1, cv2

    lines = [_hdr("SUMMARY COMPARISON")]
    lines.append(_row2("Metric", s1.name, s2.name, 30, 18))
    lines.append("  " + "-" * 58)

    # Fill % (higher = better)
    if abs(fill1_pct - fill2_pct) < 1e-6:
        cf1, cf2 = f"{fill1_pct*100:.2f}%", f"{fill2_pct*100:.2f}%"
    elif fill1_pct > fill2_pct:
        cf1, cf2 = green(f"{fill1_pct*100:.2f}%"), red(f"{fill2_pct*100:.2f}%")
    else:
        cf1, cf2 = red(f"{fill1_pct*100:.2f}%"), green(f"{fill2_pct*100:.2f}%")
    lines.append(_row2("Fill rate", cf1, cf2))

    # Unfilled count (lower = better)
    cu1 = green(str(u1)) if u1 < u2 else (red(str(u1)) if u1 > u2 else str(u1))
    cu2 = green(str(u2)) if u2 < u1 else (red(str(u2)) if u2 > u1 else str(u2))
    lines.append(_row2("Unfilled slots", cu1, cu2))

    # Singleton count (lower = better)
    ss1 = green(str(sing1)) if sing1 < sing2 else (red(str(sing1)) if sing1 > sing2 else str(sing1))
    ss2 = green(str(sing2)) if sing2 < sing1 else (red(str(sing2)) if sing2 > sing1 else str(sing2))
    lines.append(_row2("Singleton 2400h nights", ss1, ss2))

    # A:B RMSE (lower = better)
    cv1, cv2 = _better(rmse1, rmse2, higher_is_better=False)
    lines.append(_row2("A:B RMSE (per physician)", cv1, cv2))

    # Shift-count RMSE (lower = better)
    cv1, cv2 = _better(sc_rmse1, sc_rmse2, higher_is_better=False)
    lines.append(_row2("Shift-count RMSE", cv1, cv2))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Consecutive violations — cleaner implementation
# ---------------------------------------------------------------------------

def _section_consecutive_v2(
    s1: Schedule,
    s2: Schedule,
    max_consec: int = 3,
    physician_max_consec: Optional[dict[str, int]] = None,
) -> str:
    v1 = consecutive_run_violations(s1, max_consec, physician_max_consec)
    v2 = consecutive_run_violations(s2, max_consec, physician_max_consec)

    cnt1 = sum(len(runs) for runs in v1.values())
    cnt2 = sum(len(runs) for runs in v2.values())

    if physician_max_consec:
        hdr_detail = "exceeds personal max_consecutive_shifts from physicians.yaml"
    else:
        hdr_detail = f">{max_consec} consecutive days"
    lines = [_hdr(f"CONSECUTIVE RUN VIOLATIONS  ({hdr_detail})")]

    if cnt1 == 0 and cnt2 == 0:
        lines.append(f"  {green('None found in either schedule.')}")
        return "\n".join(lines)

    s_cnt1 = green(str(cnt1)) if cnt1 < cnt2 else (red(str(cnt1)) if cnt1 > cnt2 else str(cnt1))
    s_cnt2 = green(str(cnt2)) if cnt2 < cnt1 else (red(str(cnt2)) if cnt2 > cnt1 else str(cnt2))
    lines.append(_row2("Total violations", s_cnt1, s_cnt2))
    lines.append("")

    # Build a helper to show each physician's personal cap in the detail line
    def _personal_cap(phys: str) -> str:
        if not physician_max_consec:
            return str(max_consec)
        cap = physician_max_consec.get(phys)
        if cap is None:
            key = canon_name(phys)
            cap = next(
                (v for k, v in physician_max_consec.items() if canon_name(k) == key),
                max_consec,
            )
        return str(cap)

    all_phys = sorted(set(v1.keys()) | set(v2.keys()), key=str.lower)
    for phys in all_phys:
        runs1 = v1.get(phys, [])
        runs2 = v2.get(phys, [])

        def _fmt(runs):
            if not runs:
                return dim("(ok)")
            parts = [f"days {r[0]}-{r[1]} ({r[2]}d)" for r in runs]
            return red(", ".join(parts))

        cap_label = f"max={_personal_cap(phys)}"
        lines.append(f"  {_truncate(phys, 22):<22}  [{cap_label}]")
        lines.append(f"    {s1.name}: {_fmt(runs1)}")
        lines.append(f"    {s2.name}: {_fmt(runs2)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main report
# ---------------------------------------------------------------------------

def print_report(
    s1: Schedule,
    s2: Schedule,
    target_a: float = 0.38,
    max_consec: int = 3,
    physician_max_consec: Optional[dict[str, int]] = None,
) -> None:
    print()
    print(bold("=" * 78))
    print(bold("  SCHEDULE COMPARISON"))
    print(bold(f"  Schedule A: {s1.name}  ({os.path.basename(s1.source)})"))
    print(bold(f"  Schedule B: {s2.name}  ({os.path.basename(s2.source)})"))
    print(bold("=" * 78))

    print(_section_fill_rate(s1, s2))
    print(_section_unfilled(s1, s2))
    print(_section_overall_split(s1, s2, target_a))
    print(_section_ab_ratio(s1, s2, target_a))
    print(_section_singletons(s1, s2))
    print(_section_any_singletons(s1, s2))
    print(_section_consecutive_v2(s1, s2, max_consec, physician_max_consec))
    print(_section_physician_shifts(s1, s2))
    print(_section_summary(s1, s2, target_a))
    print()
    print(bold("=" * 78))
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _USE_COLOR

    # Ensure Unicode output works on Windows terminals
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-16"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, io.UnsupportedOperation):
            pass

    parser = argparse.ArgumentParser(
        description="Compare two KEA Physician Scheduler XLSX output files side by side.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file1", help="Path to the first schedule XLSX file.")
    parser.add_argument("file2", help="Path to the second schedule XLSX file.")
    parser.add_argument(
        "--names", nargs=2, metavar=("NAME1", "NAME2"), default=None,
        help='Labels for the two schedules (e.g. --names "CP-SAT" "Human").',
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable ANSI colour output.",
    )
    parser.add_argument(
        "--target-a", type=float, default=0.38, metavar="FRAC",
        help="Target fraction of shifts that should be Group A (default: 0.38).",
    )
    parser.add_argument(
        "--max-consecutive", type=int, default=3, metavar="N",
        help=(
            "Fallback maximum consecutive working days when a physician is not "
            "listed in physicians.yaml (default: 3).  Per-physician limits from "
            "physicians.yaml take precedence over this value."
        ),
    )
    parser.add_argument(
        "--physicians-yaml", default=_PHYSICIANS_YAML, metavar="PATH",
        help=(
            f"Path to physicians.yaml (default: {_PHYSICIANS_YAML}).  "
            "Set to '' to disable per-physician limits and use --max-consecutive for all."
        ),
    )
    args = parser.parse_args()

    if args.no_color:
        _USE_COLOR = False

    name1 = args.names[0] if args.names else os.path.splitext(os.path.basename(args.file1))[0]
    name2 = args.names[1] if args.names else os.path.splitext(os.path.basename(args.file2))[0]

    # Load per-physician consecutive-shift caps from physicians.yaml
    phys_max_consec: Optional[dict[str, int]] = None
    yaml_path = args.physicians_yaml
    if yaml_path:
        loaded = load_physician_max_consec(yaml_path)
        if loaded:
            phys_max_consec = loaded
            print(f"Loaded per-physician max_consecutive_shifts for {len(loaded)} physicians from {yaml_path}")
        else:
            print(
                f"WARNING: could not load physicians.yaml from {yaml_path}; "
                f"falling back to --max-consecutive={args.max_consecutive} for all physicians.",
                file=sys.stderr,
            )

    print(f"Parsing {args.file1} ...")
    try:
        s1 = parse_schedule(args.file1, name1)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {args.file2} ...")
    try:
        s2 = parse_schedule(args.file2, name2)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    unify_physician_names(s1, s2)
    print_report(
        s1, s2,
        target_a=args.target_a,
        max_consec=args.max_consecutive,
        physician_max_consec=phys_max_consec,
    )


if __name__ == "__main__":
    main()
