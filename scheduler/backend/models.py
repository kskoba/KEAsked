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
        block available.  Partial blocks are NOT included.  This is a
        submission-quality signal for the validator only (min_valid_blocks,
        anchored-day rules) — it nudges physicians toward offering whole
        blocks, but some legitimately don't and have allowances there.
        Scheduling itself must not rely on it; see requested_shifts.
    requested_shifts:
        The specific shift codes this physician is available for on this
        day, cell by cell — independent of whether every row in that
        shift's block was filled. This is what the generator actually
        schedules against: available for 2400h RAH A but not 2400h NEHC
        means available for exactly that, not "the whole 2400h block" and
        not "nothing". For flat-file imports this instead holds whatever
        specific shifts were explicitly requested; the two importers don't
        currently distinguish "available for" from "requested", which is
        fine given each is used by a differently-shaped source file.
    """

    date: datetime.date
    wants_to_work: bool
    available_blocks: frozenset[int] = field(default_factory=frozenset)
    requested_shifts: frozenset[str] = field(default_factory=frozenset)
    doc_available: bool = False   # physician listed DOC (day on call) for this day
    noc_available: bool = False   # physician listed NOC (night on call) for this day
    # Specific shift code(s) this physician marked as their actual
    # preferred pick for this day, in the sheet's "Preferred" row --
    # distinct from requested_shifts, which is everything they're merely
    # available for. Almost always 0 or 1 code; can be 2 when the sheet's
    # own shorthand is ambiguous between two sites at the same time (e.g.
    # "18RA" printed identically for both 1800h RAH A side and 1800h RAH B
    # side) -- in that case both are kept as candidates rather than
    # guessing which one was meant. Currently only populated by the
    # per-physician xlsx importer (importer.py); sked's own web grid has
    # an equivalent "desirable" mark (grid state 2) that isn't wired into
    # this yet -- a deliberate follow-up, not an oversight.
    preferred_shifts: frozenset[str] = field(default_factory=frozenset)

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

    def is_valid_day(self, min_blocks: int = 2) -> bool:
        """
        A day is valid only if the physician marked the Z row AND has at
        least `min_blocks` complete blocks available.

        Default is 2. Physicians restricted to a single shift type per day
        (e.g. only_2400h) will never mark more than 1 block on any day by
        design — for those, the caller should pass 1, sourced from that
        physician's `min_blocks_per_day` rule_override in physicians.yaml.
        """
        return self.wants_to_work and self.valid_block_count >= min_blocks

    def is_anchored(self, min_blocks: int = 2) -> bool:
        """
        An anchored day is a valid day that contains at least one anchor
        block (0600h block index 0, or 2400h block index 4).
        """
        from scheduler.backend.shifts import ANCHOR_BLOCK_INDICES

        return self.is_valid_day(min_blocks) and bool(
            self.available_blocks & ANCHOR_BLOCK_INDICES
        )

    def is_valid_weekend(self, min_blocks: int = 2) -> bool:
        return self.is_valid_day(min_blocks) and self.is_weekend


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
    #
    # A physician who explicitly types "0" (deliberately wants none) and one
    # who leaves the cell blank (never said) both parse to the same int 0
    # here — real submissions confirmed both cases exist for both fields,
    # and they mean opposite things for scheduling: an explicit 0 is a hard
    # request to honor, a blank one is no signal to guess from at all. The
    # *_stated flags below carry that distinction; the plain int fields
    # stay exactly as before (0 = "no number", never None) so any existing
    # code doing `> 0` — including the deprecated generator.py, which this
    # session does not touch — keeps working unchanged. Always check the
    # matching *_stated flag before treating a 0 here as "not specified".
    shifts_2400h_requested: int = 0
    shifts_0600h_requested: int = 0
    shifts_2400h_stated: bool = False
    shifts_0600h_stated: bool = False

    days: list[DayAvailability] = field(default_factory=list)
    source_file: str = ""

    # Availability for the NEXT month's leading day(s) when this month's
    # template carries them (the December sheet ends with a Jan 1 column,
    # for holiday planning). Dated in the next month; kept out of `days`
    # so every in-month consumer stays month-bound. Not scheduled yet --
    # recorded so the data isn't lost (see importer._parse_day_column).
    spillover_days: list[DayAvailability] = field(default_factory=list)

    # Other non-empty text found in row 1 besides physician_name itself
    # (per-xlsx submissions only) — some physicians type their name in the
    # wrong cell (e.g. next to a leftover "insert name here" in A1) rather
    # than leaving it blank. Tried as identity-resolution fallbacks; see
    # server.py's _resolve_submission_id.
    raw_name_candidates: list[str] = field(default_factory=list)

    # Per-physician rule overrides.  Keys are rule identifiers from the
    # validator (e.g. "min_valid_days"); values are replacement thresholds
    # or None to disable the rule entirely for this physician.
    rule_overrides: dict[str, object] = field(default_factory=dict)

    # True when most of this sheet's own row-4 day-of-week labels (M/T/W/
    # R/F/S/SU) don't match the actual weekday for the year/month the
    # import was run under — the sheet is real, but the wrong month/year
    # was selected when importing it (see importer.py's _parse_worksheet
    # and validator.py's check_month_mismatch). Every other check on this
    # submission is unreliable until this is fixed, since every date is
    # potentially assigned to the wrong day of the week.
    month_mismatch: bool = False

    def __post_init__(self) -> None:
        # shifts_max is supposed to be an upper bound on shifts_requested
        # (the sheet's own rule: shifts_max <= shifts_requested + 2, see
        # the docstring above) -- but a real submission can still state a
        # max BELOW its own requested count (confirmed, January 2027:
        # KLam and MRico each had requested=8, max=7). A hard floor below
        # the target is nonsensical -- raise max to match requested rather
        # than let shifts_max silently undercut it downstream. Applied
        # once here, not per-importer, so every submission gets it
        # regardless of source (xlsx, flat file, sked, a merged/combined
        # submission, or a synthetic one).
        if self.shifts_max < self.shifts_requested:
            self.shifts_max = self.shifts_requested


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
