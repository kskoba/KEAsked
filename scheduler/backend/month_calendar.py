"""
Period calendar rules for the monthly shift-request template.

Pure functions, no I/O. Everything the scheduler used to encode by hand in
the month's blank xlsx lives here:

- which calendar days a request period covers (`period_days`): by default
  November runs Nov 1 -> Dec 1 and December Dec 2 -> Jan 1 (the department's
  spill-over convention, so holiday planning can see the first day of the
  next month); January therefore starts on the 2nd; every other month is the
  whole month (user-confirmed ranges 2026-10-08). These are DEFAULTS: the
  scheduler may type the period's first and last day in the pane (user
  request 2026-10-31), so every function here takes an optional explicit
  range and `validate_period_range` says what an acceptable one is.
- Alberta's statutory general holidays (`alberta_statutory_holidays`) and
  the optional ones employers may observe (`alberta_optional_holidays`).
- the suggested yellow "holiday / bonus window" marks for a period
  (`suggested_marks`): every statutory holiday in the period, plus the fixed
  Dec 24 -> Jan 1 "Christmas Rules Apply" window the department has painted
  on every December template on record (2024, 2025, 2026). The scheduler
  toggles days on or off in the Send Monthly Shift Requests pane; the EPSB
  spring break moves every year and is always a manual mark.
- `labelled_runs`: contiguous marked days sharing a label, which is how the
  template carries the label (row 2 above the first day of the run) and how
  the email lists the windows.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from typing import Iterable

from dateutil.easter import easter

# Months whose template carries the 1st of the following month as a trailing
# column. The month after each of these starts on the 2nd.
SPILLOVER_MONTHS: tuple[int, ...] = (11, 12)

# Physician-facing day-of-week letters (Monday=0 ... Sunday=6). "R" for
# Thursday is this practice's convention (mirrors importer._DOW_LABELS).
DOW_LABELS: dict[int, str] = {0: "M", 1: "T", 2: "W", 3: "R", 4: "F", 5: "S", 6: "SU"}

CHRISTMAS_WINDOW_LABEL = "Christmas Rules Apply"
_CHRISTMAS_WINDOW_START = (12, 24)   # inclusive, month/day
_CHRISTMAS_WINDOW_END = (1, 1)       # inclusive, month/day (the next year)

_MONDAY = 0


@dataclass(frozen=True)
class MarkedDay:
    """One yellow day in the template with the label shown above its run."""

    date: dt.date
    label: str


def period_id(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def period_label(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


# A period may run at most this many days past the end of its month (the
# department's convention is one; the template itself fits 32 columns).
MAX_SPILLOVER_DAYS = 7


def default_period_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    """The department's conventional first and last day for `year`/`month`'s sheet."""
    prev_month = previous_month(year, month)[1]
    first_day = 2 if prev_month in SPILLOVER_MONTHS else 1
    last_day = calendar.monthrange(year, month)[1]
    last = dt.date(year, month, last_day)
    if month in SPILLOVER_MONTHS:
        last += dt.timedelta(days=1)
    return dt.date(year, month, first_day), last


def validate_period_range(year: int, month: int, first: dt.date, last: dt.date) -> None:
    """
    Raise ValueError unless `first`..`last` is a sensible sheet range for
    `year`/`month`: it starts inside the month and ends in the month or at
    most MAX_SPILLOVER_DAYS into the next one.
    """
    if (first.year, first.month) != (year, month):
        raise ValueError(
            f"the period's first day must fall in {period_label(year, month)} (got {first.isoformat()})"
        )
    if last < first:
        raise ValueError(f"the period's last day {last.isoformat()} is before its first day {first.isoformat()}")
    month_end = dt.date(year, month, calendar.monthrange(year, month)[1])
    if last > month_end + dt.timedelta(days=MAX_SPILLOVER_DAYS):
        raise ValueError(
            f"the period's last day may run at most {MAX_SPILLOVER_DAYS} days past "
            f"{month_end:%b %-d} (got {last.isoformat()})"
        )


def resolve_period_range(
    year: int, month: int, first_day: dt.date | None = None, last_day: dt.date | None = None,
) -> tuple[dt.date, dt.date]:
    """The explicit range where given, the default otherwise; validated."""
    dfirst, dlast = default_period_range(year, month)
    first = first_day or dfirst
    last = last_day or dlast
    validate_period_range(year, month, first, last)
    return first, last


def period_days(
    year: int, month: int, first_day: dt.date | None = None, last_day: dt.date | None = None,
) -> list[dt.date]:
    """Every calendar day the request period for `year`/`month` covers, in order."""
    first, last = resolve_period_range(year, month, first_day, last_day)
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]


def spillover_day(
    year: int, month: int, first_day: dt.date | None = None, last_day: dt.date | None = None,
) -> dt.date | None:
    """The first next-month day in this period's template, if any."""
    for d in period_days(year, month, first_day, last_day):
        if d.month != month:
            return d
    return None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    while d.weekday() != weekday:
        d += dt.timedelta(days=1)
    return d + dt.timedelta(days=7 * (n - 1))


def _victoria_day(year: int) -> dt.date:
    """Last Monday on or before May 24."""
    d = dt.date(year, 5, 24)
    while d.weekday() != _MONDAY:
        d -= dt.timedelta(days=1)
    return d


def alberta_statutory_holidays(year: int) -> dict[dt.date, str]:
    """Alberta's nine statutory general holidays (Employment Standards Code)."""
    good_friday = easter(year) - dt.timedelta(days=2)
    return {
        dt.date(year, 1, 1): "New Year's Day",
        _nth_weekday(year, 2, _MONDAY, 3): "Family Day",
        good_friday: "Good Friday",
        _victoria_day(year): "Victoria Day",
        dt.date(year, 7, 1): "Canada Day",
        _nth_weekday(year, 9, _MONDAY, 1): "Labour Day",
        _nth_weekday(year, 10, _MONDAY, 2): "Thanksgiving",
        dt.date(year, 11, 11): "Remembrance Day",
        dt.date(year, 12, 25): "Christmas Day",
    }


def alberta_optional_holidays(year: int) -> dict[dt.date, str]:
    """Days Alberta employers commonly observe but need not: shown as hints, never pre-marked."""
    return {
        easter(year) + dt.timedelta(days=1): "Easter Monday",
        _nth_weekday(year, 8, _MONDAY, 1): "Heritage Day",
        dt.date(year, 12, 26): "Boxing Day",
    }


def in_christmas_window(d: dt.date) -> bool:
    return (d.month, d.day) >= _CHRISTMAS_WINDOW_START or (d.month, d.day) <= _CHRISTMAS_WINDOW_END


def suggested_marks(
    year: int, month: int, first_day: dt.date | None = None, last_day: dt.date | None = None,
) -> list[MarkedDay]:
    """
    The pre-ticked yellow days for a period: every Alberta statutory holiday
    that falls inside it, with the Dec 24 -> Jan 1 window relabelled as the
    department's Christmas rules window (it swallows Christmas Day and New
    Year's Day, as the hand-made templates did). The relabel only applies
    when the sheet actually reaches Dec 24: a January sheet that someone
    starts on the 1st shows New Year's Day under its own name.
    """
    days = period_days(year, month, first_day, last_day)
    statutory: dict[dt.date, str] = {}
    for y in sorted({d.year for d in days}):
        statutory.update(alberta_statutory_holidays(y))
    christmas = any((d.month, d.day) >= _CHRISTMAS_WINDOW_START for d in days)
    out: list[MarkedDay] = []
    for d in days:
        if christmas and in_christmas_window(d):
            out.append(MarkedDay(d, CHRISTMAS_WINDOW_LABEL))
        elif d in statutory:
            out.append(MarkedDay(d, statutory[d]))
    return out


def optional_holidays_in_period(
    year: int, month: int, first_day: dt.date | None = None, last_day: dt.date | None = None,
) -> list[MarkedDay]:
    days = period_days(year, month, first_day, last_day)
    optional: dict[dt.date, str] = {}
    for y in sorted({d.year for d in days}):
        optional.update(alberta_optional_holidays(y))
    return [MarkedDay(d, optional[d]) for d in days if d in optional]


def labelled_runs(marks: Iterable[MarkedDay]) -> list[tuple[dt.date, dt.date, str]]:
    """
    Group marked days into (first_day, last_day, label) runs: consecutive
    calendar days carrying the same label. Unlabelled days form their own
    runs with an empty label. Input order does not matter.
    """
    ordered = sorted(marks, key=lambda m: m.date)
    runs: list[tuple[dt.date, dt.date, str]] = []
    for m in ordered:
        label = (m.label or "").strip()
        if runs:
            start, end, run_label = runs[-1]
            if run_label == label and m.date == end + dt.timedelta(days=1):
                runs[-1] = (start, m.date, label)
                continue
        runs.append((m.date, m.date, label))
    return runs


def describe_runs(marks: Iterable[MarkedDay]) -> list[str]:
    """Human lines like 'Dec 24 - Jan 1: Christmas Rules Apply' for emails and the UI."""
    lines: list[str] = []
    for start, end, label in labelled_runs(marks):
        span = _fmt(start) if start == end else f"{_fmt(start)} - {_fmt(end)}"
        lines.append(f"{span}: {label}" if label else span)
    return lines


def _fmt(d: dt.date) -> str:
    return f"{calendar.month_abbr[d.month]} {d.day}"
