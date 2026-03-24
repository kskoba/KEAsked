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
}

_MIN_HOURS_BETWEEN_SHIFTS = 23


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


def is_spacing_ok(prev_shift: Shift, next_shift: Shift) -> bool:
    """
    Return True if assigning *next_shift* on the day after *prev_shift*
    respects the 23-hour minimum gap between start times.
    """
    return hours_between(prev_shift, next_shift) >= _MIN_HOURS_BETWEEN_SHIFTS


def is_next_shift_ok(prev_shift: Shift, days_gap: int, next_shift: Shift) -> bool:
    """
    Return True if scheduling *next_shift* is allowed given that *prev_shift*
    was worked *days_gap* calendar days earlier.

    Rules:
      days_gap == 1: standard 22-hour minimum between start times.
      days_gap == 2 and prev is 2400h: next shift must start at noon or later
          (36-hour rest rule — earliest allowed is 1200h on the third calendar day).
      days_gap >= 2 otherwise: no spacing restriction.

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
        return is_spacing_ok(prev_shift, next_shift)
    if days_gap == 2 and prev_shift.time == "2400h":
        return next_shift.start_hour >= 12
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
