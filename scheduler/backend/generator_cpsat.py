"""
CP-SAT based schedule generator for the emergency physician scheduling system.

Uses Google OR-Tools CP-SAT solver to find an optimal assignment of physicians
to shift slots for a given month.  Produces the same ScheduleResult output type
as generator.py so both approaches are interchangeable from server.py's perspective.

Usage:
    from scheduler.backend.generator_cpsat import CpsatScheduleGenerator
    gen = CpsatScheduleGenerator(submissions, roster, config)
    result = gen.generate(year, month, time_limit=90, num_workers=8)

Requirements:
    pip install ortools
"""

from __future__ import annotations

import calendar
import datetime
import logging
from collections import defaultdict
from typing import Callable, Optional

from scheduler.backend.config import PhysicianConfig
from scheduler.backend.generator import (
    Assignment,
    CandidateOption,
    OnCallAssignment,
    ScheduleResult,
    ScheduleStats,
    UnfilledSlot,
    ViolationReason,
    _DEFAULT_ANCHOR_MAX,
    _DEFAULT_MAX_CONSEC,
    _DEFAULT_MAX_WEEKENDS,
    _WEEKEND_WEEKDAYS,
    _weekend_key,
)
from scheduler.backend.models import PhysicianSubmission
from scheduler.backend.shifts import (
    BLOCKS,
    SHIFT_TO_BLOCK,
    Shift,
    SiteGroup,
    is_next_shift_ok,
)

logger = logging.getLogger(__name__)

try:
    from ortools.sat.python import cp_model as _cp_model
    _ORTOOLS_AVAILABLE = True
except ImportError:
    _ORTOOLS_AVAILABLE = False
    logger.warning(
        "ortools is not installed — CpsatScheduleGenerator will fall back to "
        "the greedy ScheduleGenerator.  Install with: pip install ortools"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_shifts() -> list[Shift]:
    """Return a flat list of every Shift across all blocks."""
    return [shift for block in BLOCKS for shift in block]


def _shift_by_code() -> dict[str, Shift]:
    return {shift.code: shift for block in BLOCKS for shift in block}


# ---------------------------------------------------------------------------
# CP-SAT solver
# ---------------------------------------------------------------------------

class CpsatScheduleGenerator:
    """
    Constraint-programming (CP-SAT) based schedule generator.

    Produces the same ScheduleResult as ScheduleGenerator but uses OR-Tools
    CP-SAT to search for a provably optimal (or near-optimal) solution within
    the given time limit.

    Parameters
    ----------
    submissions:
        List of PhysicianSubmission objects parsed from the monthly Excel files.
    roster:
        Physician configuration dict keyed by physician_id.
    config:
        Global scheduler config dict (from scheduler_config.yaml).
    """

    def __init__(
        self,
        submissions: list[PhysicianSubmission],
        roster: dict[str, PhysicianConfig],
        config: dict,
    ) -> None:
        self.submissions: dict[str, PhysicianSubmission] = {
            s.physician_id: s for s in submissions
        }
        self.roster = roster
        self.config = config

        # Config shortcuts (mirrors ScheduleGenerator.__init__)
        anchor_cfg = config.get("anchor_shifts", {})
        self._anchor_max: int = anchor_cfg.get("anchor_max_total", _DEFAULT_ANCHOR_MAX)
        self._anchor_tol: int = anchor_cfg.get("anchor_target_tolerance", 1)
        self._max_weekends: int = (
            config.get("weekends", {}).get("max_weekends_per_month", _DEFAULT_MAX_WEEKENDS)
        )
        self._max_consec_default: int = (
            config.get("consecutive", {}).get("max_consecutive_shifts", _DEFAULT_MAX_CONSEC)
        )
        self._group_a_target: float = (
            config.get("site_distribution", {}).get("group_a_target", 0.40)
        )

        # Case-insensitive roster lookups
        self._roster_lower: dict = {k.lower(): v for k, v in roster.items()}
        self._roster_by_name: dict = {v.name.lower(): v for v in roster.values()}
        self._pid_lower: dict[str, str] = {pid.lower(): pid for pid in self.submissions}

        # Availability indices
        # (pid, date, block_idx) -> True
        self._avail: set[tuple] = set()
        # (pid, date) -> frozenset[shift_code]  (flat-file specific shifts)
        self._shift_avail: dict[tuple, frozenset] = {}
        for sub in submissions:
            for day in sub.days:
                for b in day.available_blocks:
                    self._avail.add((sub.physician_id, day.date, b))
                if day.requested_shifts:
                    self._shift_avail[(sub.physician_id, day.date)] = day.requested_shifts

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate(
        self,
        year: int,
        month: int,
        time_limit: float = 60.0,
        num_workers: int = 8,
        progress_callback: Optional[Callable] = None,
    ) -> ScheduleResult:
        """
        Solve the scheduling problem with CP-SAT and return a ScheduleResult.

        If OR-Tools is not installed this falls back to the greedy
        ScheduleGenerator so the caller never gets an import error.

        Parameters
        ----------
        year, month:
            The scheduling period.
        time_limit:
            Maximum solver wall-clock time in seconds.  The solver returns
            the best solution found when the limit is reached.
        num_workers:
            Number of parallel search workers for CP-SAT.  CP-SAT releases
            the GIL internally so this genuinely uses multiple cores.
        progress_callback:
            Optional callable(current, total, best_score).  CP-SAT does not
            provide per-iteration callbacks so this is called once at the
            midpoint (50%) as a "Solving…" indicator and once on completion.
        """
        if not _ORTOOLS_AVAILABLE:
            logger.warning("Falling back to greedy ScheduleGenerator (ortools not installed).")
            from scheduler.backend.generator import ScheduleGenerator
            gen = ScheduleGenerator(self.submissions, self.roster, self.config)
            return gen.run_best_of(
                400, year, month,
                progress_callback=progress_callback,
            )

        if progress_callback:
            progress_callback(0, 100, 0.0)

        days_in_month = calendar.monthrange(year, month)[1]
        all_dates = [datetime.date(year, month, d) for d in range(1, days_in_month + 1)]
        all_shifts_flat = _all_shifts()
        shift_by_code = _shift_by_code()
        pids = list(self.submissions.keys())

        # Signal "solving" at 50% before blocking on the solver
        if progress_callback:
            progress_callback(50, 100, 0.0)

        model = _cp_model.CpModel()

        # ----------------------------------------------------------------
        # Decision variables
        # shifts[(pid, d_idx, shift_code)] = BoolVar
        # ----------------------------------------------------------------
        shifts: dict[tuple, object] = {}
        for pid in pids:
            for d_idx, d in enumerate(all_dates):
                for block in BLOCKS:
                    for shift in block:
                        shifts[(pid, d_idx, shift.code)] = model.new_bool_var(
                            f"s_{pid}_{d_idx}_{shift.code}"
                        )

        # ----------------------------------------------------------------
        # Hard constraint helpers
        # ----------------------------------------------------------------

        def _get_cfg(pid: str) -> Optional[PhysicianConfig]:
            return (
                self.roster.get(pid)
                or self._roster_lower.get(pid.lower())
                or self._roster_by_name.get(pid.lower())
            )

        def _forbidden_sites(pid: str) -> list[str]:
            cfg = _get_cfg(pid)
            return cfg.forbidden_sites if cfg else []

        def _max_consec(pid: str) -> int:
            cfg = _get_cfg(pid)
            return cfg.max_consecutive_shifts if cfg else self._max_consec_default

        def _eff_max_weekends(pid: str) -> int:
            cfg = _get_cfg(pid)
            if cfg and cfg.max_weekends is not None:
                return cfg.max_weekends
            return self._max_weekends

        def _anchor_limit(pid: str, shift: Shift) -> int:
            sub = self.submissions[pid]
            if shift.time == "2400h" and sub.shifts_2400h_requested > 0:
                return sub.shifts_2400h_requested + self._anchor_tol
            if shift.time == "0600h" and sub.shifts_0600h_requested > 0:
                return sub.shifts_0600h_requested + self._anchor_tol
            return self._anchor_max

        # ----------------------------------------------------------------
        # HC-1: At most one physician per slot per day
        # ----------------------------------------------------------------
        for d_idx in range(len(all_dates)):
            for block in BLOCKS:
                for shift in block:
                    model.add(
                        sum(shifts[(pid, d_idx, shift.code)] for pid in pids) <= 1
                    )

        # ----------------------------------------------------------------
        # HC-2: Physician works at most one shift per day
        # ----------------------------------------------------------------
        for pid in pids:
            for d_idx in range(len(all_dates)):
                model.add(
                    sum(
                        shifts[(pid, d_idx, shift.code)]
                        for block in BLOCKS
                        for shift in block
                    ) <= 1
                )

        # ----------------------------------------------------------------
        # HC-3: Availability — block-level
        # HC-4: Forbidden sites
        # HC-5: only_2400h restriction
        # HC-6: Flat-file specific shift availability
        # ----------------------------------------------------------------
        for pid in pids:
            cfg = _get_cfg(pid)
            forbidden = _forbidden_sites(pid)
            only_nights = cfg.only_2400h if cfg else False

            for d_idx, d in enumerate(all_dates):
                day_specific_shifts = self._shift_avail.get((pid, d))

                for block_idx, block in enumerate(BLOCKS):
                    avail_for_block = (pid, d, block_idx) in self._avail

                    for shift in block:
                        var = shifts[(pid, d_idx, shift.code)]

                        # Unavailable block
                        if not avail_for_block:
                            model.add(var == 0)
                            continue

                        # Flat-file specific shift restriction
                        if day_specific_shifts is not None and shift.code not in day_specific_shifts:
                            model.add(var == 0)
                            continue

                        # Forbidden site
                        if shift.site in forbidden:
                            model.add(var == 0)
                            continue

                        # only_2400h restriction
                        if only_nights and shift.time != "2400h":
                            model.add(var == 0)
                            continue

        # ----------------------------------------------------------------
        # HC-7: Max shifts hard cap
        # ----------------------------------------------------------------
        for pid in pids:
            sub = self.submissions[pid]
            hard_max = sub.shifts_max if sub.shifts_max > 0 else sub.shifts_requested
            if hard_max > 0:
                model.add(
                    sum(
                        shifts[(pid, d_idx, shift.code)]
                        for d_idx in range(len(all_dates))
                        for block in BLOCKS
                        for shift in block
                    ) <= hard_max
                )

        # ----------------------------------------------------------------
        # HC-8: Consecutive day limit
        # For any window of (max_consec+1) consecutive days, physician
        # works at most max_consec of them.
        # ----------------------------------------------------------------
        for pid in pids:
            mc = _max_consec(pid)
            window_size = mc + 1
            if window_size <= len(all_dates):
                for start in range(len(all_dates) - mc):
                    window_vars = []
                    for d_idx in range(start, start + window_size):
                        for block in BLOCKS:
                            for shift in block:
                                window_vars.append(shifts[(pid, d_idx, shift.code)])
                    model.add(sum(window_vars) <= mc)

        # ----------------------------------------------------------------
        # HC-9: 22h spacing between adjacent-day shifts
        # For each pair of shifts on consecutive days (or 2-days-apart with
        # 2400h), add: var1 + var2 <= 1 if the gap would be < 22h.
        # ----------------------------------------------------------------
        for pid in pids:
            for d_idx in range(len(all_dates) - 1):
                d1 = all_dates[d_idx]
                d2 = all_dates[d_idx + 1]
                # consecutive days: gap == 1
                for block1 in BLOCKS:
                    for shift1 in block1:
                        for block2 in BLOCKS:
                            for shift2 in block2:
                                if not is_next_shift_ok(shift1, 1, shift2):
                                    model.add(
                                        shifts[(pid, d_idx, shift1.code)]
                                        + shifts[(pid, d_idx + 1, shift2.code)] <= 1
                                    )

            # 2-day gap: only 2400h → next morning matters (36h rest rule)
            for d_idx in range(len(all_dates) - 2):
                for block1 in BLOCKS:
                    for shift1 in block1:
                        if shift1.time != "2400h":
                            continue
                        for block2 in BLOCKS:
                            for shift2 in block2:
                                if not is_next_shift_ok(shift1, 2, shift2):
                                    model.add(
                                        shifts[(pid, d_idx, shift1.code)]
                                        + shifts[(pid, d_idx + 2, shift2.code)] <= 1
                                    )

        # ----------------------------------------------------------------
        # HC-10: Same-shift-code on adjacent days forbidden
        # ----------------------------------------------------------------
        for pid in pids:
            for d_idx in range(len(all_dates) - 1):
                for block in BLOCKS:
                    for shift in block:
                        model.add(
                            shifts[(pid, d_idx, shift.code)]
                            + shifts[(pid, d_idx + 1, shift.code)] <= 1
                        )

        # ----------------------------------------------------------------
        # HC-11: Anchor shift limit (0600h + 2400h)
        # ----------------------------------------------------------------
        # We use the global anchor_max as the cap.  Per-physician per-type
        # caps require knowing the shift type at variable-selection time;
        # we approximate by capping combined anchor count per physician.
        for pid in pids:
            sub = self.submissions[pid]
            # Use the stricter of global cap and per-physician request+tol
            anchor_vars = []
            for d_idx in range(len(all_dates)):
                for block in BLOCKS:
                    for shift in block:
                        if shift.time in ("0600h", "2400h"):
                            anchor_vars.append(shifts[(pid, d_idx, shift.code)])

            if anchor_vars:
                # Global cap
                global_cap = self._anchor_max
                # Per-physician 2400h cap
                if sub.shifts_2400h_requested > 0:
                    cap_2400 = sub.shifts_2400h_requested + self._anchor_tol
                    vars_2400 = [
                        shifts[(pid, d_idx, shift.code)]
                        for d_idx in range(len(all_dates))
                        for block in BLOCKS
                        for shift in block
                        if shift.time == "2400h"
                    ]
                    if vars_2400:
                        model.add(sum(vars_2400) <= cap_2400)
                # Per-physician 0600h cap
                if sub.shifts_0600h_requested > 0:
                    cap_0600 = sub.shifts_0600h_requested + self._anchor_tol
                    vars_0600 = [
                        shifts[(pid, d_idx, shift.code)]
                        for d_idx in range(len(all_dates))
                        for block in BLOCKS
                        for shift in block
                        if shift.time == "0600h"
                    ]
                    if vars_0600:
                        model.add(sum(vars_0600) <= cap_0600)
                # Combined global anchor cap
                model.add(sum(anchor_vars) <= global_cap)

        # ----------------------------------------------------------------
        # HC-12: Weekend limit
        # For each physician, for each distinct weekend cluster (Fri/Sat/Sun),
        # create a BoolVar that is 1 iff the physician works any shift in that
        # cluster.  Then cap the total weekend BoolVars.
        # ----------------------------------------------------------------
        # Group date indices by weekend key
        weekend_clusters: dict[tuple, list[int]] = defaultdict(list)
        for d_idx, d in enumerate(all_dates):
            if d.weekday() in _WEEKEND_WEEKDAYS:
                weekend_clusters[_weekend_key(d)].append(d_idx)

        for pid in pids:
            max_we = _eff_max_weekends(pid)
            if not weekend_clusters:
                continue

            weekend_worked_vars = []
            for wk_key, d_indices in weekend_clusters.items():
                # BoolVar = 1 iff physician works any shift in this cluster
                wv = model.new_bool_var(f"wknd_{pid}_{wk_key[2]}")
                # Sum of all shift vars in this cluster
                cluster_shifts = [
                    shifts[(pid, d_idx, shift.code)]
                    for d_idx in d_indices
                    for block in BLOCKS
                    for shift in block
                ]
                if cluster_shifts:
                    # wv == 1 iff any cluster shift == 1
                    total_cluster = sum(cluster_shifts)
                    # wv >= each individual var  →  wv == 1 if any is 1
                    for cv in cluster_shifts:
                        model.add(wv >= cv)
                    # wv <= total  →  wv can only be 1 if at least one is 1
                    model.add(wv <= total_cluster)
                    weekend_worked_vars.append(wv)

            if weekend_worked_vars:
                model.add(sum(weekend_worked_vars) <= max_we)

        # ----------------------------------------------------------------
        # HC-13: Max consecutive nights (NIAR)
        # ----------------------------------------------------------------
        for pid in pids:
            cfg = _get_cfg(pid)
            max_nights = cfg.max_consecutive_nights if cfg else self._max_consec_default
            window_size = max_nights + 1
            if window_size <= len(all_dates):
                night_vars_by_day = []
                for d_idx in range(len(all_dates)):
                    day_night_vars = [
                        shifts[(pid, d_idx, shift.code)]
                        for block in BLOCKS
                        for shift in block
                        if shift.time == "2400h"
                    ]
                    night_vars_by_day.append(day_night_vars)

                for start in range(len(all_dates) - max_nights):
                    window_night_vars = []
                    for d_idx in range(start, start + window_size):
                        window_night_vars.extend(night_vars_by_day[d_idx])
                    model.add(sum(window_night_vars) <= max_nights)

        # ----------------------------------------------------------------
        # Objective function
        # Maximize: filled slots (primary) + soft bonuses
        #
        # Soft terms (all scaled so filled slots dominate):
        #   +50  per slot filled that the physician specifically requested
        #   -20  per shift below physician's minimum (min_deficit)
        #   -2   per shift below physician's requested count (req_deficit)
        #   -5   per singleton 2400h (approximated via penalty vars)
        # ----------------------------------------------------------------

        # Total filled slots
        all_shift_vars = [
            shifts[(pid, d_idx, shift.code)]
            for pid in pids
            for d_idx in range(len(all_dates))
            for block in BLOCKS
            for shift in block
        ]
        filled_expr = sum(all_shift_vars)

        # Soft: requests bonus
        request_bonus_terms = []
        for pid in pids:
            sub = self.submissions[pid]
            cfg = _get_cfg(pid)
            honor = cfg.honor_all_requests if cfg else False
            for d_idx, d in enumerate(all_dates):
                day_shifts_req = self._shift_avail.get((pid, d))
                if day_shifts_req:
                    for shift_code in day_shifts_req:
                        if (pid, d_idx, shift_code) in shifts:
                            weight = 50 if honor else 5
                            request_bonus_terms.append(
                                weight * shifts[(pid, d_idx, shift_code)]
                            )

        # Soft: per-physician min/req deficit penalty vars
        # Instead of creating penalty vars (which would require integer variables),
        # we use the shift count directly in the objective.
        physician_shift_exprs: dict[str, object] = {}
        for pid in pids:
            physician_shift_exprs[pid] = sum(
                shifts[(pid, d_idx, shift.code)]
                for d_idx in range(len(all_dates))
                for block in BLOCKS
                for shift in block
            )

        deficit_penalty_terms = []
        for pid in pids:
            sub = self.submissions[pid]
            effective_requested = sub.shifts_requested
            if effective_requested == 0 and sub.shifts_max > 0:
                effective_requested = min(10, sub.shifts_max)
            effective_min = sub.shifts_min if sub.shifts_min > 0 else effective_requested

            # Reward for each shift assigned up to the min (strong signal)
            min_weight = 20 if sub.shifts_requested <= 5 else 10
            if effective_min > 0:
                # Add capped reward: min(physician_total, effective_min) * min_weight
                # Approximated as: physician_total * min_weight  (bounded by hard cap)
                deficit_penalty_terms.append(
                    min_weight * physician_shift_exprs[pid]
                )
            # Moderate reward for each shift assigned up to requested
            if effective_requested > 0:
                deficit_penalty_terms.append(
                    2 * physician_shift_exprs[pid]
                )

        # Soft: Group A/B balance per physician
        # Reward Group A shifts when below target, Group B when above target.
        # Use a moderate per-shift coefficient.
        group_balance_terms = []
        group_a_target_scaled = int(self._group_a_target * 100)  # e.g. 40
        for pid in pids:
            for d_idx in range(len(all_dates)):
                for block in BLOCKS:
                    for shift in block:
                        var = shifts[(pid, d_idx, shift.code)]
                        if shift.site_group == SiteGroup.A:
                            # Small reward for Group A shifts (up to target)
                            group_balance_terms.append(2 * var)
                        # Group B is the default — no extra reward

        # Composite objective (all terms are non-negative rewards; maximize)
        objective_terms = [1000 * filled_expr]
        objective_terms.extend(request_bonus_terms)
        objective_terms.extend(deficit_penalty_terms)
        objective_terms.extend(group_balance_terms)

        model.maximize(sum(objective_terms))

        # ----------------------------------------------------------------
        # Solve
        # ----------------------------------------------------------------
        solver = _cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_search_workers = num_workers
        solver.parameters.log_search_progress = False

        logger.info(
            "CP-SAT: starting solve for %d-%02d with time_limit=%.0fs, workers=%d",
            year, month, time_limit, num_workers,
        )
        status = solver.solve(model)
        logger.info(
            "CP-SAT: status=%s  objective=%.0f  wall_time=%.1fs",
            solver.status_name(status),
            solver.objective_value,
            solver.wall_time,
        )

        if progress_callback:
            progress_callback(100, 100, -solver.objective_value)

        # ----------------------------------------------------------------
        # Extract solution
        # ----------------------------------------------------------------
        if status in (_cp_model.OPTIMAL, _cp_model.FEASIBLE):
            return self._build_result(
                year, month, all_dates, shifts, solver, shift_by_code
            )

        # No feasible solution found — return empty result with all slots unfilled
        logger.warning("CP-SAT: no feasible solution found (status=%s)", solver.status_name(status))
        result = ScheduleResult(year=year, month=month)
        all_shifts_obj = _all_shifts()
        # Rebuild a minimal state for near-miss candidates
        self._pid_to_slots: dict[str, list] = defaultdict(list)
        self._slot_to_pid: dict[tuple, str] = {}
        self._shift_count: dict[str, int] = defaultdict(int)
        self._anchor_count: dict[str, int] = defaultdict(int)
        self._weekend_keys: dict[str, set] = defaultdict(set)
        for d in all_dates:
            for shift in all_shifts_obj:
                candidates = self._near_miss_candidates(d, shift)
                result.unfilled.append(UnfilledSlot(date=d, shift=shift, candidates=candidates))
                result.issues.append(f"{d.strftime('%b %d')} {shift.code}: no eligible physician")
        result.stats = self._compute_stats(result)
        return result

    # ------------------------------------------------------------------
    # Post-solve result construction
    # ------------------------------------------------------------------

    def _build_result(
        self,
        year: int,
        month: int,
        all_dates: list[datetime.date],
        shifts: dict,
        solver,
        shift_by_code: dict[str, Shift],
    ) -> ScheduleResult:
        """Convert CP-SAT solution values into a ScheduleResult."""
        result = ScheduleResult(year=year, month=month)
        pids = list(self.submissions.keys())

        # Rebuild mutable state for near-miss candidate computation
        self._pid_to_slots: dict[str, list] = defaultdict(list)
        self._slot_to_pid: dict[tuple, str] = {}
        self._shift_count: dict[str, int] = defaultdict(int)
        self._anchor_count: dict[str, int] = defaultdict(int)
        self._weekend_keys: dict[str, set] = defaultdict(set)

        for d_idx, d in enumerate(all_dates):
            for block in BLOCKS:
                for shift in block:
                    assigned_pid = None
                    for pid in pids:
                        val = solver.value(shifts[(pid, d_idx, shift.code)])
                        if val:
                            assigned_pid = pid
                            break
                    if assigned_pid:
                        self._slot_to_pid[(d, shift.code)] = assigned_pid
                        self._pid_to_slots[assigned_pid].append((d, shift))
                        self._shift_count[assigned_pid] += 1
                        if shift.time in ("0600h", "2400h"):
                            self._anchor_count[assigned_pid] += 1
                        if d.weekday() in _WEEKEND_WEEKDAYS:
                            self._weekend_keys[assigned_pid].add(_weekend_key(d))
                        result.assignments.append(Assignment(
                            date=d,
                            shift=shift,
                            physician_id=assigned_pid,
                            physician_name=self.submissions[assigned_pid].physician_name,
                        ))
                    else:
                        candidates = self._near_miss_candidates(d, shift)
                        result.unfilled.append(
                            UnfilledSlot(date=d, shift=shift, candidates=candidates)
                        )
                        result.issues.append(
                            f"{d.strftime('%b %d')} {shift.code}: no eligible physician"
                        )

        result.stats = self._compute_stats(result)
        return result

    # ------------------------------------------------------------------
    # Near-miss candidates (adapted from ScheduleGenerator)
    # ------------------------------------------------------------------

    _HARD_DISQUALIFIERS = frozenset({
        "availability",
        "shift_not_available",
        "forbidden_site",
    })

    def _near_miss_candidates(
        self, d: datetime.date, shift: Shift, max_n: int = 10
    ) -> list[CandidateOption]:
        block_idx = SHIFT_TO_BLOCK[shift.code]
        results = []
        for pid, sub in self.submissions.items():
            # Never offer someone already working this day
            if any(ad == d for ad, _ in self._pid_to_slots.get(pid, [])):
                continue
            # Never offer someone who marked themselves unavailable on this day
            day_avail = next((day for day in sub.days if day.date == d), None)
            if day_avail is None or not day_avail.wants_to_work:
                continue
            violations = self._check_constraints_simple(pid, d, shift, block_idx)
            if violations:
                if any(v.rule in self._HARD_DISQUALIFIERS for v in violations):
                    continue
                fit = self._near_miss_score(pid, d, shift)
                deficit = max(0, sub.shifts_requested - self._shift_count.get(pid, 0))
                results.append((len(violations), -fit, -deficit, pid, violations))
        results.sort()
        return [
            CandidateOption(
                physician_id=pid,
                physician_name=self.submissions[pid].physician_name,
                violations=violations,
            )
            for _, _, _, pid, violations in results[:max_n]
        ]

    def _check_constraints_simple(
        self,
        pid: str,
        d: datetime.date,
        shift: Shift,
        block_idx: int,
    ) -> list[ViolationReason]:
        """Lightweight constraint check used for near-miss candidate ranking."""
        v: list[ViolationReason] = []
        sub = self.submissions[pid]
        cfg = (
            self.roster.get(pid)
            or self._roster_lower.get(pid.lower())
            or self._roster_by_name.get(pid.lower())
        )

        if (pid, d, block_idx) not in self._avail:
            v.append(ViolationReason(rule="availability", description=f"Not available for block {block_idx} on {d}"))

        day_specific = self._shift_avail.get((pid, d))
        if day_specific is not None and shift.code not in day_specific:
            v.append(ViolationReason(rule="shift_not_available", description=f"Only available for: {', '.join(sorted(day_specific))}"))

        forbidden = cfg.forbidden_sites if cfg else []
        if shift.site in forbidden:
            v.append(ViolationReason(rule="forbidden_site", description=f"{shift.site} is a forbidden site"))

        if cfg and cfg.only_2400h and shift.time != "2400h":
            v.append(ViolationReason(rule="shift_type_restriction", description=f"Restricted to 2400h shifts only"))

        pid_slots = self._pid_to_slots.get(pid, [])
        if any(ad == d for ad, _ in pid_slots):
            v.append(ViolationReason(rule="already_assigned_today", description=f"Already has a shift on {d}"))

        hard_max = sub.shifts_max if sub.shifts_max > 0 else sub.shifts_requested
        if self._shift_count.get(pid, 0) >= hard_max > 0:
            v.append(ViolationReason(rule="max_shifts", description=f"Already at maximum shifts ({hard_max})"))

        return v

    def _near_miss_score(self, pid: str, d: datetime.date, shift: Shift) -> float:
        sub = self.submissions[pid]
        block_idx = SHIFT_TO_BLOCK[shift.code]
        score = 0.0
        if (pid, d, block_idx) in self._avail:
            score += 3.0
        cfg = self.roster.get(pid) or self._roster_lower.get(pid.lower())
        forbidden = cfg.forbidden_sites if cfg else []
        if shift.site not in forbidden:
            score += 2.0
        if self._shift_count.get(pid, 0) < sub.shifts_requested:
            score += 1.0
        return score

    # ------------------------------------------------------------------
    # Stats (identical logic to ScheduleGenerator._compute_stats)
    # ------------------------------------------------------------------

    def _compute_stats(self, result: ScheduleResult) -> ScheduleStats:
        filled = len(result.assignments)
        total = filled + len(result.unfilled)
        group_a = sum(1 for a in result.assignments if a.shift.site_group == SiteGroup.A)
        group_b = filled - group_a
        counts: dict[str, int] = defaultdict(int)
        night_dates: dict[str, set] = defaultdict(set)
        for a in result.assignments:
            counts[a.physician_id] += 1
            if a.shift.time == "2400h":
                night_dates[a.physician_id].add(a.date)
        singletons: dict[str, int] = defaultdict(int)
        for pid, nights in night_dates.items():
            for d in nights:
                prev = d - datetime.timedelta(days=1)
                nxt = d + datetime.timedelta(days=1)
                if prev not in nights and nxt not in nights:
                    singletons[pid] += 1
        return ScheduleStats(
            total_slots=total,
            filled_slots=filled,
            unfilled_slots=len(result.unfilled),
            group_a_count=group_a,
            group_b_count=group_b,
            group_a_pct=round(group_a / filled, 3) if filled else 0.0,
            group_b_pct=round(group_b / filled, 3) if filled else 0.0,
            physician_counts=dict(counts),
            physician_singletons=dict(singletons),
        )

    # ------------------------------------------------------------------
    # Delegate repair_pass and assign_on_calls to ScheduleGenerator
    # (they operate only on the ScheduleResult + state restored from it,
    #  so they work identically regardless of how the result was produced)
    # ------------------------------------------------------------------

    def repair_pass(self, result: ScheduleResult, n_attempts: int = 50) -> ScheduleResult:
        """Delegate post-solve repair to ScheduleGenerator."""
        from scheduler.backend.generator import ScheduleGenerator
        gen = ScheduleGenerator(list(self.submissions.values()), self.roster, self.config)
        # Restore state so repair_pass sees the CP-SAT assignments
        gen._restore_state_from_result(result)
        return gen.repair_pass(result, n_attempts)

    def assign_on_calls(self, result: ScheduleResult) -> ScheduleResult:
        """Delegate on-call assignment to ScheduleGenerator."""
        from scheduler.backend.generator import ScheduleGenerator
        gen = ScheduleGenerator(list(self.submissions.values()), self.roster, self.config)
        return gen.assign_on_calls(result)
