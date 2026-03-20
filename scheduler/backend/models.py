"""
Core data models for the scheduling system.

These are plain Python dataclasses — no ORM, no external schema library —
so the module loads fast and can be used in both the backend and tests.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum


class Weekday(int, Enum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


WEEKEND_WEEKDAYS: frozenset[Weekday] = frozenset(
    {Weekday.FRIDAY, Weekday.SATURDAY, Weekday.SUNDAY}
)


@dataclass
class DayAvailability:
    """
    Parsed availability for one calendar day from a single physician's sheet.

    Attributes
    ----------
    date:
        The calendar date.
    wants_to_work:
        True when the physician marked row-5 (the "Z" row) for this day.
    available_blocks:
        Set of block indices (0–4) for which the physician marked the entire
        block available.  Partial blocks are NOT included.
    requested_shifts:
        Specific shift codes the physician explicitly requested for this day.
        These are the preferred assignments within the available blocks.
    """

    date: datetime.date
    wants_to_work: bool
    available_blocks: frozenset[int] = field(default_factory=frozenset)
    requested_shifts: frozenset[str] = field(default_factory=frozenset)
    doc_available: bool = False   # physician listed DOC (day on call) for this day
    noc_available: bool = False   # physician listed NOC (night on call) for this day

    # ------------------------------------------------------------------ #
    # Derived properties used by the validator
    # ------------------------------------------------------------------ #

    @property
    def weekday(self) -> Weekday:
        return Weekday(self.date.weekday())

    @property
    def is_weekend(self) -> bool:
        return self.weekday in WEEKEND_WEEKDAYS

    @property
    def valid_block_count(self) -> int:
        return len(self.available_blocks)

    @property
    def is_valid_day(self) -> bool:
        """
        A day is valid only if the physician marked the Z row AND has at
        least 2 complete blocks available.
        """
        return self.wants_to_work and self.valid_block_count >= 2

    @property
    def is_anchored(self) -> bool:
        """
        An anchored day is a valid day that contains at least one anchor
        block (0600h block index 0, or 2400h block index 4).
        """
        from scheduler.backend.shifts import ANCHOR_BLOCK_INDICES

        return self.is_valid_day and bool(
            self.available_blocks & ANCHOR_BLOCK_INDICES
        )

    @property
    def is_valid_weekend(self) -> bool:
        return self.is_valid_day and self.is_weekend


@dataclass
class PhysicianSubmission:
    """
    Everything extracted from one physician's monthly request spreadsheet.

    Attributes
    ----------
    physician_id:
        Unique identifier (e.g. employee number or canonical name string).
    physician_name:
        Display name.
    year, month:
        The scheduling period this submission covers.
    shifts_requested:
        How many shifts the physician is requesting this month.
    days:
        One DayAvailability per calendar day in the month.
    source_file:
        Path to the originating Excel file (for traceability).
    """

    physician_id: str
    physician_name: str
    year: int
    month: int

    # Shift counts read from the Excel submission (cells AK38, AP40, AP42).
    # shifts_requested  — the physician's target N for this month (AK38).
    # shifts_min        — minimum they are willing to accept (AP40).
    # shifts_max        — maximum allowable; scheduler may use up to this
    #                     value only when needed to fill uncovered shifts (AP42).
    #                     The sheet enforces shifts_max <= shifts_requested + 2.
    shifts_requested: int
    shifts_min: int = 0
    shifts_max: int = 0

    # Anchor shift targets read from the Excel submission.
    # shifts_2400h_requested — how many 2400h shifts desired this month (AK59).
    # shifts_0600h_requested — how many 0600h shifts desired this month (AK61).
    # 0 means not specified; scheduler falls back to the global anchor cap.
    shifts_2400h_requested: int = 0
    shifts_0600h_requested: int = 0

    days: list[DayAvailability] = field(default_factory=list)
    source_file: str = ""

    # Per-physician rule overrides.  Keys are rule identifiers from the
    # validator (e.g. "min_valid_days"); values are replacement thresholds
    # or None to disable the rule entirely for this physician.
    rule_overrides: dict[str, object] = field(default_factory=dict)


@dataclass
class ValidationIssue:
    severity: str           # "error" | "warning"
    rule: str               # short rule id, e.g. "min_valid_days"
    message: str
    physician_id: str = ""


@dataclass
class ValidationResult:
    physician_id: str
    physician_name: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]
