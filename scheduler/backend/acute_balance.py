"""
Post-solve RAH A <-> RAH B balancing ("easy final pass adjustment").

RAH A side and RAH B side are the two acute sites. They run identical
shift times (0600h / 1200h / 1800h / 2400h), so on any day where one
physician holds the A-side slot and another holds the B-side slot at the
same time, the two can trade sites without touching anyone's days, times,
rest spacing, consecutive-shift count, weekend load or anchor count. The
solver's objective doesn't care which acute side a physician lands on, so
it routinely leaves someone with (say) 0 A / 5 B for the month. A real
January solve had N Lam at 0/4, Grishin at 0/5, Schindler at 4/0, Whiteside
at 4/0, and so on.

This pass greedily applies such same-slot trades whenever they reduce the
roster-wide sum of |A - B| per physician, subject to the per-physician
rules that DO depend on site:

  * forbidden_sites -- never move someone onto a site they can't work;
  * HC-10 -- the identical shift code on two adjacent worked days is
    forbidden (unless listed in allow_repeat_shift_codes);
  * max_consecutive_same_site, when set -- a hard per-physician cap on
    consecutive days at one site;
  * the soft "no repeated site within a run of consecutive days" rule --
    a trade must not introduce a same-site repeat within either
    physician's run that wasn't already there.

Pure function over a ScheduleResult; the caller recomputes stats.
"""

from __future__ import annotations

import datetime
from collections import defaultdict

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.shifts import SHIFT_CODE_LOOKUP, Shift

A_SIDE = "RAH A side"
B_SIDE = "RAH B side"
_OTHER = {A_SIDE: B_SIDE, B_SIDE: A_SIDE}


def _imbalance(counts: dict[str, dict[str, int]], pid: str) -> int:
    c = counts.get(pid, {})
    return abs(c.get(A_SIDE, 0) - c.get(B_SIDE, 0))


def _by_pid_day(result: ScheduleResult) -> dict[str, dict[datetime.date, Assignment]]:
    out: dict[str, dict[datetime.date, Assignment]] = defaultdict(dict)
    for a in result.assignments:
        out[a.physician_id][a.date] = a
    return out


def _run_sites(day_map: dict[datetime.date, Assignment], d: datetime.date) -> list[str]:
    """Sites worked on the other days of the maximal consecutive-day run containing d."""
    sites: list[str] = []
    step = datetime.timedelta(days=1)
    cur = d - step
    while cur in day_map:
        sites.append(day_map[cur].shift.site)
        cur -= step
    cur = d + step
    while cur in day_map:
        sites.append(day_map[cur].shift.site)
        cur += step
    return sites


def _consecutive_same_site(day_map: dict[datetime.date, Assignment], d: datetime.date, site: str) -> int:
    """Length of the same-site streak through d if d were worked at `site`."""
    step = datetime.timedelta(days=1)
    n = 1
    cur = d - step
    while cur in day_map and day_map[cur].shift.site == site:
        n += 1
        cur -= step
    cur = d + step
    while cur in day_map and day_map[cur].shift.site == site:
        n += 1
        cur += step
    return n


def _move_allowed(
    cfg: PhysicianConfig | None,
    day_map: dict[datetime.date, Assignment],
    a: Assignment,
    new_shift: Shift,
) -> bool:
    new_site = new_shift.site
    if cfg and new_site in cfg.forbidden_sites:
        return False
    step = datetime.timedelta(days=1)
    # HC-10: identical shift code on an adjacent worked day.
    repeat_ok = bool(cfg and new_shift.code in cfg.allow_repeat_shift_codes)
    if not repeat_ok:
        for nb in (a.date - step, a.date + step):
            if nb in day_map and day_map[nb].shift.code == new_shift.code:
                return False
    # Hard per-physician cap on consecutive days at one site.
    if cfg and cfg.max_consecutive_same_site is not None:
        if _consecutive_same_site(day_map, a.date, new_site) > cfg.max_consecutive_same_site:
            return False
    # Soft same-site-within-a-run rule: never make it worse.
    others = _run_sites(day_map, a.date)
    had_repeat = a.shift.site in others
    would_repeat = new_site in others
    if would_repeat and not had_repeat:
        return False
    return True


def balance_acute_sides(
    result: ScheduleResult,
    roster: dict[str, PhysicianConfig],
    max_rounds: int = 20,
) -> tuple[ScheduleResult, int]:
    """
    Trade RAH A <-> RAH B between pairs of physicians working the same
    date and start time, whenever it lowers the total per-physician
    |A - B| imbalance and breaks no site-dependent rule for either
    physician. Mutates assignments' shifts in place and returns
    (result, number_of_trades_made).
    """
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in result.assignments:
        if a.shift.site in _OTHER:
            counts[a.physician_id][a.shift.site] += 1

    by_pid_day = _by_pid_day(result)

    # (date, time) -> {site: Assignment}
    slots: dict[tuple[datetime.date, str], dict[str, Assignment]] = defaultdict(dict)
    for a in result.assignments:
        if a.shift.site in _OTHER:
            slots[(a.date, a.shift.time)][a.shift.site] = a

    trades = 0
    for _ in range(max_rounds):
        improved = False
        for key in sorted(slots):
            pair = slots[key]
            a_asg, b_asg = pair.get(A_SIDE), pair.get(B_SIDE)
            if a_asg is None or b_asg is None or a_asg.physician_id == b_asg.physician_id:
                continue
            pa, pb = a_asg.physician_id, b_asg.physician_id
            before = _imbalance(counts, pa) + _imbalance(counts, pb)
            # Simulate: pa goes A->B, pb goes B->A.
            counts[pa][A_SIDE] -= 1; counts[pa][B_SIDE] += 1
            counts[pb][B_SIDE] -= 1; counts[pb][A_SIDE] += 1
            after = _imbalance(counts, pa) + _imbalance(counts, pb)
            # Undo the simulation; apply for real only if it helps and is legal.
            counts[pa][A_SIDE] += 1; counts[pa][B_SIDE] -= 1
            counts[pb][B_SIDE] += 1; counts[pb][A_SIDE] -= 1
            if after >= before:
                continue
            new_a_shift = SHIFT_CODE_LOOKUP[f"{a_asg.shift.time} {B_SIDE}"]
            new_b_shift = SHIFT_CODE_LOOKUP[f"{b_asg.shift.time} {A_SIDE}"]
            if not _move_allowed(roster.get(pa), by_pid_day[pa], a_asg, new_a_shift):
                continue
            if not _move_allowed(roster.get(pb), by_pid_day[pb], b_asg, new_b_shift):
                continue
            a_asg.shift = new_a_shift
            b_asg.shift = new_b_shift
            counts[pa][A_SIDE] -= 1; counts[pa][B_SIDE] += 1
            counts[pb][B_SIDE] -= 1; counts[pb][A_SIDE] += 1
            pair[A_SIDE], pair[B_SIDE] = b_asg, a_asg
            trades += 1
            improved = True
        if not improved:
            break
    return result, trades


def acute_side_report(result: ScheduleResult) -> dict[str, tuple[int, int]]:
    """pid -> (A count, B count), for logging/tests."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for a in result.assignments:
        if a.shift.site == A_SIDE:
            out[a.physician_id][0] += 1
        elif a.shift.site == B_SIDE:
            out[a.physician_id][1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}
