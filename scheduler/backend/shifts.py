"""
Canonical shift and block definitions.

Each BLOCK is a group of shifts that must all be marked available together
to count as a valid block for a given day.

Source: Shifts.md
"""

from dataclasses import dataclass
from enum import Enum


# --------------------------------------------------------------------------- #
# Site classification
# --------------------------------------------------------------------------- #

class SiteGroup(Enum):
    """
    Group A: RAH A side and RAH B side  — target 38% of scheduled shifts.
    Group B: RAH I side, NEHC, RAH F    — target 62% of scheduled shifts.
    """
    A = "A"   # RAH A / RAH B
    B = "B"   # RAH I / NEHC / RAH F

# Target fraction of total monthly shifts that should fall in each group.
SITE_GROUP_TARGETS: dict[SiteGroup, float] = {
    SiteGroup.A: 0.38,
    SiteGroup.B: 0.62,
}

# Which sites belong to which group
_SITE_TO_GROUP: dict[str, SiteGroup] = {
    "RAH A side": SiteGroup.A,
    "RAH B side": SiteGroup.A,
    "RAH I side": SiteGroup.B,
    "NEHC":       SiteGroup.B,
    "RAH F side": SiteGroup.B,
}


# --------------------------------------------------------------------------- #
# Spacing constraint
# --------------------------------------------------------------------------- #

# Map time label → start hour (24-hour, integer).
# 2400h is treated as 24 (not 0) so spacing arithmetic stays simple.
# DOC/NOC (on-call) are included here — not real BLOCKS shifts, but giving
# them a start hour lets assign_on_calls() reuse the same rest-gap check
# (hours_between/is_spacing_ok) that regular shifts use, via a throwaway
# Shift(time="DOC"/"NOC", site="") — see generator.py's assign_on_calls().
_START_HOURS: dict[str, int] = {
    "0600h": 6,
    "0900h": 9,
    "1000h": 10,
    "1200h": 12,
    "1400h": 14,
    "1500h": 15,
    "1600h": 16,
    "1700h": 17,
    "1800h": 18,
    "2000h": 20,
    "2400h": 24,   # midnight; kept as 24 so spacing is always positive
    "DOC": 5,      # Day On Call — 0500h to 1600h
    "NOC": 16,     # Night On Call — 1600h to 0500h
}

_MIN_HOURS_BETWEEN_SHIFTS = 23
_MAX_HOURS_BETWEEN_SHIFTS = 36


# --------------------------------------------------------------------------- #
# Shift
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Shift:
    time: str   # e.g. "0600h"
    site: str   # e.g. "RAH A side"

    @property
    def code(self) -> str:
        return f"{self.time} {self.site}"

    @property
    def start_hour(self) -> int:
        """Start time as an integer hour (0–24). 2400h → 24."""
        return _START_HOURS[self.time]

    @property
    def site_group(self) -> SiteGroup:
        return _SITE_TO_GROUP[self.site]

    def __str__(self) -> str:
        return self.code


def hours_between(earlier: Shift, later: Shift) -> float:
    """
    Hours from the start of *earlier* to the start of *later*, assuming
    *later* is on the next calendar day.  Uses start times only.
    """
    return (later.start_hour + 24) - earlier.start_hour


def violates_min_spacing(prev_shift: Shift, next_shift: Shift) -> bool:
    """
    True if *next_shift* the day after *prev_shift* would give less than
    the 23-hour minimum rest between start times. This is the HARD rule —
    never allowed, no exceptions.
    """
    return hours_between(prev_shift, next_shift) < _MIN_HOURS_BETWEEN_SHIFTS


def violates_max_spacing(prev_shift: Shift, next_shift: Shift) -> bool:
    """
    True if *next_shift* the day after *prev_shift* would be more than the
    36-hour maximum gap between start times (e.g. a 0600h shift followed by
    a 2400h shift the very next day). This is a SOFT rule — strongly
    preferred against, but the CP-SAT model may still accept it under
    heavy penalty rather than leave a slot unfilled (see generator_cpsat.py
    HC-9's `long_gap` handling). Expected to hold in the large majority of
    a solved schedule, not treated as an absolute constraint.
    """
    return hours_between(prev_shift, next_shift) > _MAX_HOURS_BETWEEN_SHIFTS


def is_spacing_ok(prev_shift: Shift, next_shift: Shift) -> bool:
    """
    Return True if assigning *next_shift* on the day after *prev_shift*
    respects both the 23-hour minimum and 36-hour maximum gap between
    start times. Used where there's no objective to make the max-gap side
    soft against (e.g. on-call eligibility in assign_on_calls, which
    already degrades gracefully — an unfilled call slot doesn't block
    schedule release, so there's no need for a separate soft path there).
    """
    return not violates_min_spacing(prev_shift, next_shift) and not violates_max_spacing(prev_shift, next_shift)


_LATE_SHIFT_MIN_START_HOUR = 20  # 2000h or later triggers the extended-rest rules below
# 1600h-1800h starts (ending 0000-0200 under the 8h shift length) get a
# narrower rule: the day after next may not start before 0900h. Added
# 2026-10-07 after a real solve put K Smith on 1800h -> day off -> 0600h:
# home around 0400h, then an 0600h start the following morning. 0900h and
# later on that day are fine; the day in between is already governed by
# the plain 23h rule (after an 1800h only 1700h+ fits the next day).
_EVENING_SHIFT_MIN_START_HOUR = 16
_POST_EVENING_MIN_NEXT_START_HOUR = 9


def is_next_shift_ok(prev_shift: Shift, days_gap: int, next_shift: Shift) -> bool:
    """
    Return True if scheduling *next_shift* is allowed given that *prev_shift*
    was worked *days_gap* calendar days earlier. This checks the HARD rules
    only (see violates_min_spacing) — the soft 36-hour maximum for
    days_gap==1 is handled separately in generator_cpsat.py's CP-SAT
    objective, not here, since a plain bool can't express "soft."

    Rules:
      days_gap == 1: 23-hour minimum between start times.
      prev starts at 2000h or later (a "late" shift -- 2000h/2400h and
      anything after): the calendar day right after is blocked outright
      (days_gap == 2 never allowed, regardless of next_shift), and the day
      after THAT (days_gap == 3) still requires next shift to start at noon
      or later.
      days_gap >= 2 otherwise (prev started before 2000h): no spacing
      restriction.

    Widened 2026-09-28 from "prev is exactly 2400h, next allowed at noon on
    day 2" after a real schedule (cpsat-oct17) produced a 2400h shift on the
    2nd followed by a 1200h shift on the 4th -- technically satisfying the
    old rule (next_shift.start_hour >= 12 at days_gap==2) but only ~28 hours
    of actual rest once the night shift's own ~8-hour duration is accounted
    for, not the ~36 the rule's name implied. Confirmed directly: a late
    shift needs a full extra day, and 2000h-start shifts (which also run
    into the early morning) need the same protection a 2400h start gets,
    not just literal midnight starts.

    Parameters
    ----------
    prev_shift:
        The last shift the physician worked.
    days_gap:
        Calendar days between the two shifts (1 = consecutive days).
    next_shift:
        The shift being considered for assignment.
    """
    if days_gap == 1:
        return not violates_min_spacing(prev_shift, next_shift)
    if prev_shift.start_hour >= _LATE_SHIFT_MIN_START_HOUR:
        if days_gap == 2:
            return False
        if days_gap == 3:
            return next_shift.start_hour >= 12
    elif prev_shift.start_hour >= _EVENING_SHIFT_MIN_START_HOUR:
        if days_gap == 2:
            return next_shift.start_hour >= _POST_EVENING_MIN_NEXT_START_HOUR
    return True


# --------------------------------------------------------------------------- #
# Block definitions (order = block index 0–4)
# --------------------------------------------------------------------------- #

BLOCKS: list[list[Shift]] = [
    # Block 0 — 0600h
    [
        Shift("0600h", "RAH A side"),
        Shift("0600h", "RAH B side"),
        Shift("0600h", "NEHC"),
        Shift("0600h", "RAH I side"),
    ],
    # Block 1 — 0900h / 1000h / 1200h
    [
        Shift("0900h", "NEHC"),
        Shift("1000h", "RAH I side"),
        Shift("1200h", "RAH A side"),
        Shift("1200h", "RAH B side"),
        Shift("1200h", "NEHC"),
    ],
    # Block 2 — 1400h / 1500h / 1600h / 1700h
    [
        Shift("1400h", "RAH I side"),
        Shift("1500h", "NEHC"),
        Shift("1600h", "RAH F side"),   # updated from RAH A side
        Shift("1700h", "NEHC"),
    ],
    # Block 3 — 1800h / 2000h
    [
        Shift("1800h", "RAH A side"),
        Shift("1800h", "RAH B side"),
        Shift("1800h", "RAH I side"),
        Shift("2000h", "NEHC"),
    ],
    # Block 4 — 2400h
    [
        Shift("2400h", "RAH A side"),
        Shift("2400h", "RAH B side"),
        Shift("2400h", "NEHC"),
        Shift("2400h", "RAH I side"),
    ],
]

# Flat lookup: shift code -> block index
SHIFT_TO_BLOCK: dict[str, int] = {
    shift.code: block_idx
    for block_idx, block in enumerate(BLOCKS)
    for shift in block
}

# All shift codes as a set — used for input validation
ALL_SHIFT_CODES: frozenset[str] = frozenset(SHIFT_TO_BLOCK.keys())

# Anchor blocks: 0600h (index 0) and 2400h (index 4)
ANCHOR_BLOCK_INDICES: frozenset[int] = frozenset({0, 4})


# --------------------------------------------------------------------------- #
# Export/display shape — the grid layout used by both the xlsx export
# (server.py's _build_export_workbook/_parse_schedule_xlsx) and the master
# Google Sheet push/read-back (google_sheets_client.py/
# sheets_schedule_reader.py). Lives here, not in server.py, specifically so
# a second reader/writer can import it without a circular import back into
# server.py — this is display/row-layout knowledge, not request-handling
# logic, so it belongs with the rest of the shift domain data.
# --------------------------------------------------------------------------- #

# (site_label, time_label, time_code, site_code) — site_label/time_label are
# the row headers a human reads (xlsx or the department's real Sheet);
# time_code+site_code combine to a real Shift.code. (None, None) marks the
# two on-call sentinel rows (DOC/NOC), which carry no Shift object.
EXPORT_SHIFTS: list[tuple[str, str, str | None, str | None]] = [
    ("DOC",       "0500-1559",  None,     None),           # Day on call row — same hour range the human schedule uses
    ("RAH A",     "0600-1200",  "0600h",  "RAH A side"),
    ("RAH B",     "0600-1200",  "0600h",  "RAH B side"),
    ("NECHC",     "0600-1400",  "0600h",  "NEHC"),
    ("RAH I",     "0600-1400",  "0600h",  "RAH I side"),
    ("NECHC",     "0900-1700",  "0900h",  "NEHC"),
    ("RAH I",     "1000-1800",  "1000h",  "RAH I side"),
    ("RAH A",     "1200-1800",  "1200h",  "RAH A side"),
    ("RAH B",     "1200-1800",  "1200h",  "RAH B side"),
    ("NECHC",     "1200-2000",  "1200h",  "NEHC"),
    ("RAH I",     "1400-2200",  "1400h",  "RAH I side"),
    ("NECHC",     "1500-2300",  "1500h",  "NEHC"),
    ("NOC",       "1600-0459",  None,     None),           # Night on call row — same hour range the human schedule uses
    ("RAH Float", "1600-2400",  "1600h",  "RAH F side"),  # Float runs 1600-2400 (corrected 2026-10-07; 0459 is the on-call end time)
    ("NECHC",     "1700-0100",  "1700h",  "NEHC"),
    ("RAH A",     "1800-0000",  "1800h",  "RAH A side"),
    ("RAH B",     "1800-0000",  "1800h",  "RAH B side"),
    ("RAH I",     "1800-0200",  "1800h",  "RAH I side"),
    ("NECHC",     "2000-0400",  "2000h",  "NEHC"),
    ("RAH A",     "2400-0600",  "2400h",  "RAH A side"),
    ("RAH B",     "2400-0600",  "2400h",  "RAH B side"),
    ("NECHC",     "2400-0800",  "2400h",  "NEHC"),
    ("RAH I",     "2400-0800",  "2400h",  "RAH I side"),
]

# Reverse lookup: (site_label, time_label) -> (time_code, site_code)
EXPORT_SHIFT_LOOKUP: dict[tuple[str, str], tuple[str | None, str | None]] = {
    (site_label, time_label): (time_code, site_code)
    for site_label, time_label, time_code, site_code in EXPORT_SHIFTS
}
# Back-compat: files exported before on-call rows carried a real hour range
# used the literal label as the time row. Keep these loadable.
EXPORT_SHIFT_LOOKUP[("DOC", "Day On Call")] = (None, None)
EXPORT_SHIFT_LOOKUP[("NOC", "Night On Call")] = (None, None)
# Back-compat: schedules exported before 2026-10-07 carried the on-call end
# time on the Float row by mistake. Keep those files loadable.
EXPORT_SHIFT_LOOKUP[("RAH Float", "1600-0459")] = ("1600h", "RAH F side")
# The real department master sheet labels the on-call rows "AM CALL" /
# "PM CALL" rather than DOC / NOC. Same rows, same hours.
EXPORT_SHIFT_LOOKUP[("AM CALL", "0500-1559")] = (None, None)
EXPORT_SHIFT_LOOKUP[("PM CALL", "1600-0459")] = (None, None)

# Canonical on-call type for every on-call row label the readers accept.
CALL_TYPE_BY_LABEL: dict[str, str] = {
    "DOC": "DOC", "AM CALL": "DOC",
    "NOC": "NOC", "PM CALL": "NOC",
}


def normalize_label(value) -> str:
    """Collapse the stray whitespace hand-edited sheets accumulate ('NECHC ', '  1800-0000')."""
    return " ".join(str(value).split()) if value is not None else ""

# Flat shift-code -> Shift object lookup (used by the xlsx loader and the
# master-sheet reader).
SHIFT_CODE_LOOKUP: dict[str, Shift] = {
    shift.code: shift
    for block in BLOCKS
    for shift in block
}

# Pre-computed per-group shift sets (used by the scheduler)
SHIFTS_BY_GROUP: dict[SiteGroup, frozenset[str]] = {
    group: frozenset(
        s.code
        for block in BLOCKS
        for s in block
        if s.site_group == group
    )
    for group in SiteGroup
}
