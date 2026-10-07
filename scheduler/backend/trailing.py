"""
Cross-month continuity: turn the *previous* month's finalized schedule into
the ``trailing_assignments`` dict that ``CpsatScheduleGenerator`` consumes
so HC-8 (SIAR), HC-9 (23h/36h spacing + extended post-late rest) and
HC-13/13b/13c (NIAR + night adjacency) reason about what each physician
actually worked in the last few days of e.g. December instead of treating
January 1st as if everyone starts the month fully rested.

Pure functions only -- no server state, no I/O -- so the whole derivation
is unit-testable with a synthetic ``ScheduleResult``. The source of that
``ScheduleResult`` (a locally exported xlsx via server._parse_schedule_xlsx,
or the master Google Sheet via sheets_schedule_reader) is the caller's
business; see /api/generate in scheduler/api/server.py.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import ScheduleResult
from scheduler.backend.models import PhysicianSubmission
from scheduler.backend.shifts import Shift, SiteGroup

# The longest look-back any boundary-crossing hard rule needs when a
# physician's own caps are small: HC-9 looks up to 3 days back for the
# extended-rest-after-late-shift rule (gap in (1, 2, 3)), and HC-13b/13c
# look 1-2 days back for night adjacency. HC-8/HC-13 need exactly the
# physician's own max_consecutive_shifts / max_consecutive_nights days.
_MIN_TRAILING_WINDOW_DAYS = 3

# Fallback when a physician has no roster entry at all (mirrors the
# generator's own self._max_consec_default so the window is never shorter
# than what HC-8 will actually inspect for an unconfigured physician).
_DEFAULT_MAX_CONSEC = 3


def previous_month(year: int, month: int) -> tuple[int, int]:
    """(year, month) of the calendar month immediately before the given one."""
    if month == 1:
        return year - 1, 12
    return year, month - 1


def trailing_window_days(cfg: PhysicianConfig | None) -> int:
    """
    How many days back from the start of the target month a physician's
    real prior-month shifts can still influence a boundary-crossing hard
    rule: max(max_consecutive_shifts, max_consecutive_nights, 3).

    Anything older than this can't participate in any HC-8/HC-9/HC-13
    window that also touches day 0, so it is dropped rather than carried
    along as dead weight.
    """
    if cfg is None:
        return max(_DEFAULT_MAX_CONSEC, _MIN_TRAILING_WINDOW_DAYS)
    return max(
        int(cfg.max_consecutive_shifts),
        int(cfg.max_consecutive_nights),
        _MIN_TRAILING_WINDOW_DAYS,
    )


def build_trailing_assignments(
    prior: ScheduleResult,
    roster: dict[str, PhysicianConfig],
    year: int,
    month: int,
) -> dict[str, list[tuple[datetime.date, Shift]]]:
    """
    Build the ``trailing_assignments`` mapping for a solve of ``year``/``month``
    from the finalized ``ScheduleResult`` of the month immediately before it.

    - Only *regular* shifts are included. On-call (DOC/NOC) lives in
      ``prior.on_calls`` and is never consulted: HC-8/HC-9/HC-13 only ever
      reason about regular shifts (decided in the original design).
    - Each physician keeps only the last ``trailing_window_days(cfg)`` days
      of the prior month; earlier days can't reach any boundary window.
    - Physicians with nothing inside their window are omitted entirely,
      which the generator treats identically to "no data" for them.

    Raises ``ValueError`` if ``prior`` is not actually the previous calendar
    month -- feeding e.g. a November schedule into a January solve would
    silently enforce nonsense, so the mismatch is a hard error here and the
    caller decides whether to degrade to ``None``.
    """
    exp_year, exp_month = previous_month(year, month)
    if (prior.year, prior.month) != (exp_year, exp_month):
        raise ValueError(
            f"Trailing schedule is for {prior.year}-{prior.month:02d} but a "
            f"{year}-{month:02d} solve needs {exp_year}-{exp_month:02d}."
        )

    first_day = datetime.date(year, month, 1)
    out: dict[str, list[tuple[datetime.date, Shift]]] = {}
    for a in sorted(prior.assignments, key=lambda a: a.date):
        # Defensive: an Assignment should always be a regular shift, but a
        # foreign/odd Shift with no start hour would blow up HC-9 later.
        if a.shift is None or not _looks_like_regular_shift(a.shift):
            continue
        window = trailing_window_days(roster.get(a.physician_id))
        cutoff = first_day - datetime.timedelta(days=window)
        if a.date < cutoff or a.date >= first_day:
            continue
        out.setdefault(a.physician_id, []).append((a.date, a.shift))
    return out


def _looks_like_regular_shift(shift: Shift) -> bool:
    """True if the shift carries a parseable start hour (i.e. it's a regular shift code)."""
    try:
        shift.start_hour
    except Exception:
        return False
    return True


def summarize_trailing(trailing: dict[str, list[tuple[datetime.date, Shift]]]) -> str:
    """One-line human summary for logs / status endpoints."""
    if not trailing:
        return "no trailing shifts"
    n_shifts = sum(len(v) for v in trailing.values())
    dates = [d for v in trailing.values() for d, _ in v]
    return (
        f"{len(trailing)} physician(s), {n_shifts} regular shift(s), "
        f"{min(dates).isoformat()}..{max(dates).isoformat()}"
    )


# --------------------------------------------------------------------------- #
# Month-to-month carry-over metrics
# --------------------------------------------------------------------------- #
# Beyond the last-few-days rest/consecutive rules above, two whole-month
# facts about the prior month feed soft objective terms in the next solve
# (see generator_cpsat.py's "carry-over" blocks):
#
#   acute debt  -- a physician who worked a disproportionately non-acute
#                  month (acute share below the Group A target) gets extra
#                  weight on an additional acute (RAH A/B) shift next month,
#                  unless they are structurally barred from acute sites
#                  (e.g. Krisik / Francescutti, forbidden_sites covers both).
#   overage     -- a physician who was scheduled over their own requested
#                  count last month is discouraged (not forbidden) from
#                  going over again two months running. Needs the prior
#                  month's *requested* count, which only the prior month's
#                  submissions carry -- a schedule xlsx alone can't say it.


@dataclass(frozen=True)
class PriorMonthSummary:
    """What one physician actually did in the previous month (regular shifts only)."""
    shifts_worked: int
    acute_shifts: int
    # The prior month's requested count, when its submissions were supplied;
    # None means unknown, and the overage carry-over is skipped for them.
    shifts_requested: int | None = None

    @property
    def non_acute_shifts(self) -> int:
        return self.shifts_worked - self.acute_shifts

    def acute_debt(self, group_a_target: float) -> int:
        """Whole acute shifts short of the target share for the volume worked (0 if none)."""
        if self.shifts_worked <= 0:
            return 0
        expected = round(self.shifts_worked * group_a_target)
        return max(0, expected - self.acute_shifts)

    @property
    def overage(self) -> int:
        """Shifts worked beyond the requested count (0 if unknown or not over)."""
        if self.shifts_requested is None or self.shifts_requested <= 0:
            return 0
        return max(0, self.shifts_worked - self.shifts_requested)


def build_prior_month_summaries(
    prior: ScheduleResult,
    prior_submissions: list[PhysicianSubmission] | None = None,
) -> dict[str, PriorMonthSummary]:
    """
    Per-physician whole-month tallies from the prior month's finalized
    schedule. On-call is excluded (it lives in prior.on_calls). A physician
    with a prior submission but zero shifts still gets an entry (worked 0),
    so an under-used physician isn't mistaken for an absent one.
    """
    worked: dict[str, int] = {}
    acute: dict[str, int] = {}
    for a in prior.assignments:
        if a.shift is None or not _looks_like_regular_shift(a.shift):
            continue
        worked[a.physician_id] = worked.get(a.physician_id, 0) + 1
        if a.shift.site_group == SiteGroup.A:
            acute[a.physician_id] = acute.get(a.physician_id, 0) + 1

    requested: dict[str, int] = {}
    for sub in prior_submissions or []:
        if (sub.year, sub.month) != (prior.year, prior.month):
            continue
        requested[sub.physician_id] = requested.get(sub.physician_id, 0) + int(sub.shifts_requested)

    out: dict[str, PriorMonthSummary] = {}
    for pid in set(worked) | set(requested):
        out[pid] = PriorMonthSummary(
            shifts_worked=worked.get(pid, 0),
            acute_shifts=acute.get(pid, 0),
            shifts_requested=requested.get(pid),
        )
    return out


def acute_eligible(cfg: PhysicianConfig | None) -> bool:
    """False when every acute (Group A) site is forbidden for this physician."""
    if cfg is None:
        return True
    acute_sites = {site for site, grp in _SITE_GROUPS.items() if grp == SiteGroup.A}
    return not acute_sites <= set(cfg.forbidden_sites)


def _site_groups() -> dict[str, SiteGroup]:
    from scheduler.backend import shifts as _shifts
    return dict(_shifts._SITE_TO_GROUP)


_SITE_GROUPS = _site_groups()


def summarize_prior_month(
    summaries: dict[str, PriorMonthSummary],
    roster: dict[str, PhysicianConfig],
    group_a_target: float,
) -> str:
    """One-line human summary of which carry-over terms will be active."""
    if not summaries:
        return "no prior-month summaries"
    debt = [p for p, s in summaries.items() if s.acute_debt(group_a_target) > 0 and acute_eligible(roster.get(p))]
    over = [p for p, s in summaries.items() if s.overage > 0]
    known = sum(1 for s in summaries.values() if s.shifts_requested is not None)
    return (
        f"{len(summaries)} physician(s); acute debt: {len(debt)} "
        f"({', '.join(sorted(debt)) or 'none'}); went over requested: {len(over)} "
        f"({', '.join(sorted(over)) or 'none'}); requested count known for {known}"
    )
