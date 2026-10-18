"""
Post-solve repair: hand a floor physician an anchor shift that somebody
else holds as OVERAGE.

User rule (2026-10-10): a physician with no positive anchor request (the
0/0 or blank "floor" population) must never displace anyone's STATED
2400h/0600h request -- "floor physicians should only be given a shift to
avoid making others go over their requested number of anchor shifts".

Real case: P57 (10 shifts, explicit 0/0, nights offered on five weekend
dates only) sat at 0 of 2 anchors in cpsatv2-jan4/5/6 while P51 and
P92 each held a 4th night against 3 requested and P31 a 6th against
5 -- on P57's own dates. January's 120 night slots carry more stated
night requests than that, so a night for P57 can only come from such an
overage night, and reaching it inside CP-SAT means a multi-swap chain
(his own same-day shift has to move for the 23 h spacing rule, and so
on). The solver's floor stage ended its 60 s unproven with him at zero
three runs running, and the final objective values the trade at only the
overage step plus the floor bonus minus the chain's own costs.

This pass makes the trade directly: for each floor physician still below
their floor, find an anchor of a type they offered that day whose holder
has more of that type than they stated (unstated counts as 0), give it to
the floor physician, and give the holder one of the floor physician's
non-anchor shifts in return. Both shift counts are unchanged, the holder
stays at or above their stated anchor count, and every move is
rule-checked through the generator's post-solve checker (availability,
forbidden sites, one shift per day, consecutive-day and rest-spacing
limits, night-run cap) plus the two rules that checker lacks: HC-10 (same
code on adjacent days) and HC-13b (the holder must not be left with an
isolated night). Never touches an only_2400h/only_0600h physician's
anchors (their whole request is anchors), never takes a floor physician
below their own floor.

Operates on the generator's incremental state: call
``gen.resync_from_result(result)`` first if an earlier pass mutated the
result directly (the /api/generate pipeline does).
"""

from __future__ import annotations

import datetime
from collections import defaultdict

from scheduler.backend.generator import Assignment, ScheduleResult
from scheduler.backend.weekend_repair import _repeats_adjacent_code

_ANCHOR_TIMES = ("0600h", "2400h")
_FLOOR_MIN_REQUESTED = 3   # no floor below 3 requested shifts (user rule 2026-10-10); mirrors the solver
_ANCHOR_BLOCK_IDX = {"2400h": 4, "0600h": 0}   # importer.py's block layout


def _stated(sub, time_str: str) -> int:
    if time_str == "2400h":
        return sub.shifts_2400h_requested if sub.shifts_2400h_stated else 0
    return sub.shifts_0600h_requested if sub.shifts_0600h_stated else 0


def _only(cfg, time_str: str) -> bool:
    if cfg is None:
        return False
    return bool(cfg.only_2400h) if time_str == "2400h" else bool(cfg.only_0600h)


def _offers(sub, d: datetime.date, time_str: str) -> bool:
    for day in list(sub.days) + list(getattr(sub, "spillover_days", [])):
        if day.date != d:
            continue
        if any(code.startswith(time_str) for code in day.requested_shifts):
            return True
        return _ANCHOR_BLOCK_IDX[time_str] in day.available_blocks
    return False


def _available_days(sub, time_str: str) -> int:
    return sum(1 for day in list(sub.days) + list(getattr(sub, "spillover_days", []))
               if _offers(sub, day.date, time_str))


def floor_target(gen, pid: str) -> int:
    """How many anchors the floor guarantees this physician (0 when not
    floor-eligible), mirroring the solver's zero_zero_floor_info bound."""
    sub = gen.submissions.get(pid)
    cfg = gen.roster.get(pid)
    if sub is None:
        return 0
    if cfg is not None and (cfg.anchor_floor_exempt or cfg.only_2400h or cfg.only_0600h):
        return 0
    if _stated(sub, "2400h") > 0 or _stated(sub, "0600h") > 0:
        return 0
    if (sub.shifts_requested or 0) < _FLOOR_MIN_REQUESTED:
        return 0
    available = _available_days(sub, "2400h") + _available_days(sub, "0600h")
    if available == 0:
        return 0
    threshold = getattr(gen, "_anchor_min_floor_full_threshold", 8)
    target = 2 if (sub.shifts_requested or 0) >= threshold else 1
    return min(target, available)


def _anchor_counts(result: ScheduleResult) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"2400h": 0, "0600h": 0})
    for a in result.assignments:
        if a.shift.time in _ANCHOR_TIMES:
            counts[a.physician_id][a.shift.time] += 1
    return counts


def floor_shortfalls(gen, result: ScheduleResult) -> list[tuple[str, int]]:
    """(pid, units still missing) for every floor physician below their floor."""
    counts = _anchor_counts(result)
    out = []
    for pid in gen.submissions:
        need = floor_target(gen, pid) - sum(counts[pid].values())
        if need > 0:
            out.append((pid, need))
    out.sort(key=lambda x: -x[1])
    return out


def _is_overage(gen, counts, holder: str, time_str: str) -> bool:
    """Holder has more of this anchor type than they stated, and losing one
    keeps them at their stated count and (if floor-eligible) at their floor."""
    sub = gen.submissions.get(holder)
    if sub is None or _only(gen.roster.get(holder), time_str):
        return False
    if counts[holder][time_str] <= _stated(sub, time_str):
        return False
    return sum(counts[holder].values()) - 1 >= floor_target(gen, holder)


def _leaves_isolated_night(gen, pid: str, d: datetime.date) -> bool:
    """HC-13b for the holder: removing pid's 2400h on d must not leave a
    neighbouring night of theirs without an adjacent night."""
    cfg = gen.roster.get(pid)
    if cfg is not None and (getattr(cfg, "allow_isolated_nights", False)
                            or getattr(cfg, "max_consecutive_nights", None) == 1):
        return False
    nights = {ad for ad, s in gen._pid_to_slots.get(pid, []) if s.time == "2400h" and ad != d}
    one = datetime.timedelta(days=1)
    for nd in (d - one, d + one):
        if nd in nights and (nd - one) not in nights and (nd + one) not in nights:
            return True
    return False


def _orphaned_night(gen, pid: str, d: datetime.date) -> datetime.date | None:
    """The neighbouring night of pid's that removing d would leave isolated, if any."""
    cfg = gen.roster.get(pid)
    if cfg is not None and (getattr(cfg, "allow_isolated_nights", False)
                            or getattr(cfg, "max_consecutive_nights", None) == 1):
        return None
    nights = {ad for ad, s in gen._pid_to_slots.get(pid, []) if s.time == "2400h" and ad != d}
    one = datetime.timedelta(days=1)
    for nd in (d - one, d + one):
        if nd in nights and (nd - one) not in nights and (nd + one) not in nights:
            return nd
    return None


def _undo_trade(gen, p: str, mine: Assignment, q: str, theirs: Assignment) -> None:
    """Reverse a successful _try_trade: p is back on mine, q back on theirs."""
    gen._unassign(q, mine.date, mine.shift)
    gen._unassign(p, theirs.date, theirs.shift)
    gen._assign(p, mine.date, mine.shift)
    gen._assign(q, theirs.date, theirs.shift)
    mine.physician_id, theirs.physician_id = theirs.physician_id, mine.physician_id
    mine.physician_name, theirs.physician_name = theirs.physician_name, mine.physician_name


def _try_trade(gen, p: str, mine: Assignment, q: str, theirs: Assignment,
               check_isolation: bool = True) -> bool:
    """p takes theirs (an anchor), q takes mine (a non-anchor); apply only if both are clean."""
    if check_isolation and theirs.shift.time == "2400h" and _leaves_isolated_night(gen, q, theirs.date):
        return False
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
    mine.physician_id, theirs.physician_id = theirs.physician_id, mine.physician_id
    mine.physician_name, theirs.physician_name = theirs.physician_name, mine.physician_name
    return True


def repair_anchor_floors(gen, result: ScheduleResult, max_rounds: int = 3) -> tuple[ScheduleResult, int]:
    """
    Give floor physicians below their floor an overage anchor as described
    in the module docstring. Returns (result, number_of_trades). Mutates
    result.assignments' physician fields and the generator's state in
    place, keeping the two consistent.
    """
    trades = 0
    for _ in range(max_rounds):
        made = False
        for p, _need in floor_shortfalls(gen, result):
            sub_p = gen.submissions[p]
            counts = _anchor_counts(result)
            mine_options = [a for a in result.assignments
                            if a.physician_id == p and a.shift.time not in _ANCHOR_TIMES]
            if not mine_options:
                continue
            candidates = [
                a for a in result.assignments
                if a.shift.time in _ANCHOR_TIMES and a.physician_id != p
                and _offers(sub_p, a.date, a.shift.time)
                and _is_overage(gen, counts, a.physician_id, a.shift.time)
            ]
            # Biggest overage first, then the holder with the most anchors overall.
            candidates.sort(key=lambda a: (
                -(counts[a.physician_id][a.shift.time] - _stated(gen.submissions[a.physician_id], a.shift.time)),
                -sum(counts[a.physician_id].values()),
            ))
            done = False
            for theirs in candidates:
                q = theirs.physician_id
                # Same-day swap first (p's days unchanged), then nearest in time.
                mine_sorted = sorted(mine_options, key=lambda a: abs((a.date - theirs.date).days))
                for mine in mine_sorted:
                    if _try_trade(gen, p, mine, q, theirs):
                        trades += 1; made = True; done = True
                        break
                if done:
                    break
                # The holder's night is one of a pair: taking it alone would
                # leave the other night isolated (HC-13b). Split the pair
                # between TWO floor physicians -- p takes this night, a
                # second floor physician r takes the pair-mate, the holder
                # gets a non-anchor shift back from each. Real case: P92's
                # Jan 2+3 (4th night for 3 requested) with P57 offering
                # Jan 2 and P37 most nights.
                if theirs.shift.time != "2400h":
                    continue
                orphan_day = _orphaned_night(gen, q, theirs.date)
                if orphan_day is None:
                    continue
                sub_q = gen.submissions[q]
                if counts[q]["2400h"] - 2 < _stated(sub_q, "2400h") or \
                        sum(counts[q].values()) - 2 < floor_target(gen, q):
                    continue
                orphan = next((a for a in result.assignments
                               if a.physician_id == q and a.date == orphan_day and a.shift.time == "2400h"), None)
                if orphan is None:
                    continue
                partners = [r for r, _n in floor_shortfalls(gen, result)
                            if r != p and _offers(gen.submissions[r], orphan_day, "2400h")]
                for mine in mine_sorted:
                    if not _try_trade(gen, p, mine, q, theirs, check_isolation=False):
                        continue
                    for r in partners:
                        r_options = sorted(
                            (a for a in result.assignments
                             if a.physician_id == r and a.shift.time not in _ANCHOR_TIMES),
                            key=lambda a: abs((a.date - orphan_day).days))
                        for mine_r in r_options:
                            # The pair-mate must not itself orphan a third night of q's.
                            if _try_trade(gen, r, mine_r, q, orphan):
                                trades += 2; made = True; done = True
                                break
                        if done:
                            break
                    if done:
                        break
                    _undo_trade(gen, p, mine, q, theirs)
                if done:
                    break
        if not made:
            break
    return result, trades
