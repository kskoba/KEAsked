"""
Post-solve repair of Fri+Sun-without-Saturday weekends.

A physician working Friday and Sunday but not the Saturday between has the
worst of both: two weekend days consumed, a broken weekend, and (for the
cap) one full weekend cluster spent on a split. Comparing a real solve
with the human-built November schedule (2026-10-07), 11 of the solver's 12
splits were for physicians who had OFFERED that Saturday, so a proper
weekend was available -- just not chosen.

Two one-for-one trades can end a split without creating another:

  A. Saturday-only partner: trade one side of the split (the Friday, else
     the Sunday) with the Saturday shift of a physician who works ONLY that
     Saturday that weekend. The split physician ends up with Sat+Sun or
     Fri+Sat; the partner ends up with a single Friday or Sunday instead of
     a single Saturday. Rare in practice (2 such weekends in a real
     November), so usually it's:
  B. Complete someone else's weekend: give the split physician's Friday to
     a physician already working Sat+Sun that weekend (or the Sunday to one
     working Fri+Sat), in exchange for one of that partner's WEEKDAY shifts.
     The partner's weekend becomes a full Fri+Sat+Sun, the split physician
     keeps a single weekend day, both shift counts are unchanged, and no
     new weekend is touched by anyone. This is exactly the human
     scheduler's instinct ("whoever is there, give them the whole weekend").

Both physicians keep their shift counts. Every candidate trade is rule-checked through the
generator's own post-solve checker (availability, forbidden sites, one
shift per day, consecutive-day and rest-spacing limits, night runs), the
same path /api/swap uses, and is applied only when BOTH sides come back
clean. To leave anchor fairness untouched, the two shifts must be the same
start time, or both non-anchor.

Operates on the generator's incremental state, so call
``gen.resync_from_result(result)`` first if an earlier pass mutated the
result directly.
"""

from __future__ import annotations

import datetime
from collections import defaultdict

from scheduler.backend.generator import Assignment, ScheduleResult

_FRI, _SAT, _SUN = 4, 5, 6
_ANCHOR_TIMES = ("0600h", "2400h")


def _weekend_key(d: datetime.date) -> datetime.date | None:
    """The Friday that anchors d's Fri/Sat/Sun cluster, or None for a weekday."""
    wd = d.weekday()
    if wd in (_FRI, _SAT, _SUN):
        return d - datetime.timedelta(days=wd - _FRI)
    return None


def _compatible(a: Assignment, b: Assignment) -> bool:
    """Trading these two shifts must not change either physician's anchor load."""
    if a.shift.time == b.shift.time:
        return True
    return a.shift.time not in _ANCHOR_TIMES and b.shift.time not in _ANCHOR_TIMES


def find_splits(result: ScheduleResult) -> list[tuple[str, datetime.date, Assignment, Assignment]]:
    """(pid, friday_key, friday_assignment, sunday_assignment) for every Fri+Sun-without-Sat weekend."""
    by: dict[str, dict[datetime.date, dict[int, Assignment]]] = defaultdict(lambda: defaultdict(dict))
    for a in result.assignments:
        k = _weekend_key(a.date)
        if k is not None:
            by[a.physician_id][k][a.date.weekday()] = a
    out = []
    for pid, weeks in by.items():
        for k, days in weeks.items():
            if _FRI in days and _SUN in days and _SAT not in days:
                out.append((pid, k, days[_FRI], days[_SUN]))
    return out


def repair_weekend_splits(gen, result: ScheduleResult, max_rounds: int = 3) -> tuple[ScheduleResult, int]:
    """
    Trade away Fri+Sun splits as described in the module docstring. Returns
    (result, number_of_trades). Mutates result.assignments' physician fields
    and the generator's state in place, keeping the two consistent.
    """
    trades = 0
    for _ in range(max_rounds):
        made = False
        for pid, key, fri_a, sun_a in find_splits(result):
            sat = key + datetime.timedelta(days=1)
            # Partners: physicians whose only shift that weekend is this Saturday.
            partner_sats = [
                a for a in result.assignments
                if a.date == sat and a.physician_id != pid
                and not any(
                    b.physician_id == a.physician_id and b.date != sat and _weekend_key(b.date) == key
                    for b in result.assignments
                )
            ]
            done = False
            # Strategy A: Saturday-only partner.
            for mine in (fri_a, sun_a):            # prefer giving up the Friday
                for theirs in sorted(partner_sats, key=lambda a: (a.shift.time != mine.shift.time, a.shift.code)):
                    if not _compatible(mine, theirs):
                        continue
                    if _try_trade(gen, pid, mine, theirs.physician_id, theirs):
                        trades += 1; made = True; done = True
                        break
                if done:
                    break
            if done:
                continue
            # Strategy B: complete a partner's weekend with my Friday (partner
            # has Sat+Sun) or my Sunday (partner has Fri+Sat), taking one of
            # their weekday shifts in return.
            weekend_days_by_pid: dict[str, set[int]] = defaultdict(set)
            for a in result.assignments:
                if _weekend_key(a.date) == key:
                    weekend_days_by_pid[a.physician_id].add(a.date.weekday())
            for mine, need in ((fri_a, {_SAT, _SUN}), (sun_a, {_FRI, _SAT})):
                partners = [q for q, days in weekend_days_by_pid.items() if q != pid and days == need]
                weekday_shifts = [
                    a for a in result.assignments
                    if a.physician_id in partners and _weekend_key(a.date) is None and _compatible(mine, a)
                ]
                # Nearest weekday shift first: a swap close in time disturbs the
                # partner's own week least.
                weekday_shifts.sort(key=lambda a: (abs((a.date - mine.date).days), a.shift.time != mine.shift.time))
                for theirs in weekday_shifts:
                    if _try_trade(gen, pid, mine, theirs.physician_id, theirs):
                        trades += 1; made = True; done = True
                        break
                if done:
                    break
        if not made:
            break
    return result, trades


def _repeats_adjacent_code(gen, pid: str, d: datetime.date, shift) -> bool:
    """HC-10: the identical shift code on an adjacent worked day (the post-solve
    checker doesn't cover this rule, so the repair enforces it itself)."""
    cfg = gen.roster.get(pid)
    if cfg and shift.code in getattr(cfg, "allow_repeat_shift_codes", []):
        return False
    for ad, s in gen._pid_to_slots.get(pid, []):
        if abs((ad - d).days) == 1 and s.code == shift.code:
            return True
    return False


def _try_trade(gen, p: str, mine: Assignment, q: str, theirs: Assignment) -> bool:
    """Rule-check p into theirs' slot and q into mine's slot; apply both only if clean."""
    gen._unassign(p, mine.date, mine.shift)
    gen._unassign(q, theirs.date, theirs.shift)
    viol_p = gen._check_constraints(p, theirs.date, theirs.shift) or []
    if not viol_p and _repeats_adjacent_code(gen, p, theirs.date, theirs.shift):
        viol_p = ["hc10"]
    if viol_p:
        gen._assign(p, mine.date, mine.shift)
        gen._assign(q, theirs.date, theirs.shift)
        return False
    gen._assign(p, theirs.date, theirs.shift)
    viol_q = gen._check_constraints(q, mine.date, mine.shift) or []
    if not viol_q and _repeats_adjacent_code(gen, q, mine.date, mine.shift):
        viol_q = ["hc10"]
    if viol_q:
        gen._unassign(p, theirs.date, theirs.shift)
        gen._assign(p, mine.date, mine.shift)
        gen._assign(q, theirs.date, theirs.shift)
        return False
    gen._assign(q, mine.date, mine.shift)
    # Mirror in the result: the two Assignment records exchange physicians.
    mine.physician_id, theirs.physician_id = theirs.physician_id, mine.physician_id
    mine.physician_name, theirs.physician_name = theirs.physician_name, mine.physician_name
    return True
