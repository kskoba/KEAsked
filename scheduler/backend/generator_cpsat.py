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
import sys
from collections import defaultdict
from typing import Callable, Optional

_FROZEN = getattr(sys, 'frozen', False)

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
    _HARD_VIOLATION_RULES,
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
# Solution callback — captures variable values during the solve so we never
# need to call solver.value() after solve() returns (which can deadlock in
# PyInstaller frozen binaries when worker threads have not fully terminated).
# ---------------------------------------------------------------------------

class _SolutionCallback(_cp_model.CpSolverSolutionCallback if _ORTOOLS_AVAILABLE else object):
    """Saves the best solution's shift-variable values on each improving solution."""

    def __init__(self, shift_vars: dict):
        if _ORTOOLS_AVAILABLE:
            super().__init__()
        self._shift_vars = shift_vars          # (pid, d_idx, shift_code) -> IntVar
        self.best_values: dict = {}            # (pid, d_idx, shift_code) -> 0 or 1
        self.best_objective: float = float('-inf')
        self.best_bound: float = 0.0

    def on_solution_callback(self) -> None:
        obj = self.objective_value
        if obj > self.best_objective:
            self.best_objective = obj
            self.best_values = {k: self.value(v) for k, v in self._shift_vars.items()}
            self.best_bound = self.best_objective_bound


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
            gen = ScheduleGenerator(list(self.submissions.values()), self.roster, self.config)
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

                        # Unavailable block — unless a flat-file specific shift overrides it.
                        # Flat-file imports set _shift_avail entries per-shift rather than
                        # per-block, so a physician may be available for a single shift even
                        # if the full block is not in _avail.
                        if not avail_for_block:
                            if day_specific_shifts is None or shift.code not in day_specific_shifts:
                                model.add(var == 0)
                                continue
                            # else: specific shift is explicitly available — fall through

                        # Flat-file specific shift restriction (when block IS available)
                        elif day_specific_shifts is not None and shift.code not in day_specific_shifts:
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

                        # Forbidden shift times (e.g. no 0600h or 2400h)
                        forbidden_times = set(cfg.forbidden_shift_times) if cfg else set()
                        if shift.time in forbidden_times:
                            model.add(var == 0)
                            continue

        # ----------------------------------------------------------------
        # HC-7: Max shifts hard cap (cap_at_requested overrides shifts_max)
        # ----------------------------------------------------------------
        for pid in pids:
            sub = self.submissions[pid]
            cfg = _get_cfg(pid)
            if cfg and cfg.cap_at_requested and sub.shifts_requested > 0:
                hard_max = sub.shifts_requested
            else:
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
        # HC-9: 23h spacing between adjacent-day shifts
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
        # The global anchor_max is a FALLBACK for physicians who have not
        # explicitly requested specific numbers of anchor shifts.  For
        # physicians like LamRico who request 16 2400h shifts, the global
        # cap of 4 must NOT override their explicit per-physician request —
        # doing so is the bug that caused them to receive only 4-6 shifts.
        for pid in pids:
            sub = self.submissions[pid]
            anchor_vars = []
            for d_idx in range(len(all_dates)):
                for block in BLOCKS:
                    for shift in block:
                        if shift.time in ("0600h", "2400h"):
                            anchor_vars.append(shifts[(pid, d_idx, shift.code)])

            if anchor_vars:
                pid_cap_2400 = 0
                pid_cap_0600 = 0

                # Per-physician 2400h cap
                if sub.shifts_2400h_requested > 0:
                    pid_cap_2400 = sub.shifts_2400h_requested + self._anchor_tol
                    vars_2400 = [
                        shifts[(pid, d_idx, shift.code)]
                        for d_idx in range(len(all_dates))
                        for block in BLOCKS
                        for shift in block
                        if shift.time == "2400h"
                    ]
                    if vars_2400:
                        model.add(sum(vars_2400) <= pid_cap_2400)

                # Per-physician 0600h cap
                if sub.shifts_0600h_requested > 0:
                    pid_cap_0600 = sub.shifts_0600h_requested + self._anchor_tol
                    vars_0600 = [
                        shifts[(pid, d_idx, shift.code)]
                        for d_idx in range(len(all_dates))
                        for block in BLOCKS
                        for shift in block
                        if shift.time == "0600h"
                    ]
                    if vars_0600:
                        model.add(sum(vars_0600) <= pid_cap_0600)

                # Combined cap: use global default only when per-physician
                # requested totals don't already exceed it.  This prevents the
                # global cap from silently overriding explicit high-volume requests.
                explicit_total = pid_cap_2400 + pid_cap_0600
                effective_cap = max(self._anchor_max, explicit_total)
                model.add(sum(anchor_vars) <= effective_cap)

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
        #   +50  per slot filled that the physician specifically requested (date bonus)
        #   +50  per shift up to physician's requested count (capped IntVar)
        #   +3   per shift assigned (small linear incentive to fill toward max)
        #   +30  per Group A shift up to A-floor target (raises A:B balance incentive)
        #   +6   per preferred Group B site shift (group_b_site_preference)
        #   +20  per A→B or B→A consecutive pair (alternation reward)
        #   -40  per A→A consecutive pair (penalty)
        #   +18  per consecutive 2400h night pair (singleton clustering)
        #   +10  per consecutive working-day pair, any shift type (reduces isolated days)
        #   -30  per consecutive 2400h pair for prefer_singleton_nights physicians
        #   -35  per 4-consecutive-day run (for physicians with mc >= 4)
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
                            weight = 150 if honor else 5
                            request_bonus_terms.append(
                                weight * shifts[(pid, d_idx, shift_code)]
                            )

        # Soft: per-physician requested-count bonus
        # Use a capped IntVar so the marginal reward for the kth shift is:
        #   k <= requested : +1000 (fill) + 50 (cap bonus) + 3 (linear) = 1053
        #   k >  requested : +1000 (fill) + 3 (linear)                  = 1003
        # This strongly prefers completing underscheduled physicians before
        # overscheduling physicians already at their requested count.
        physician_shift_exprs: dict[str, object] = {}
        for pid in pids:
            physician_shift_exprs[pid] = sum(
                shifts[(pid, d_idx, shift.code)]
                for d_idx in range(len(all_dates))
                for block in BLOCKS
                for shift in block
            )

        # Per-physician per-day "worked any shift" BoolVar — used for run-length penalties.
        # HC-2 guarantees sum of shift vars per (pid, day) is 0 or 1, so equality is safe.
        worked_bool: dict[tuple, object] = {}
        for pid in pids:
            for d_idx in range(len(all_dates)):
                day_vars = [
                    shifts[(pid, d_idx, shift.code)]
                    for block in BLOCKS
                    for shift in block
                ]
                w = model.new_bool_var(f"w_{pid}_{d_idx}")
                model.add(sum(day_vars) == w)
                worked_bool[(pid, d_idx)] = w

        deficit_penalty_terms = []
        for pid in pids:
            sub = self.submissions[pid]
            effective_requested = sub.shifts_requested
            if effective_requested == 0 and sub.shifts_max > 0:
                effective_requested = min(10, sub.shifts_max)

            if effective_requested > 0:
                # Capped bonus: strongly rewards filling up to requested count.
                # new_int_var upper-bound = effective_requested, and the add()
                # constraint forces bonus <= actual assigned count. The maximiser
                # will push bonus up to min(assigned, requested).
                bonus = model.new_int_var(0, effective_requested, f"reqbonus_{pid}")
                model.add(bonus <= physician_shift_exprs[pid])
                deficit_penalty_terms.append(50 * bonus)

            # Small linear term: slight incentive to fill toward max even above requested.
            deficit_penalty_terms.append(3 * physician_shift_exprs[pid])

        # Soft: Group A/B balance — consecutive alternation reward/penalty.
        # Rather than a weak per-shift bonus, we reward A→B or B→A consecutive
        # working pairs (+20) and penalise A→A consecutive pairs (-25).
        # This directly enforces the user requirement: "if 2 shifts in a row,
        # 1 should be Group A, 1 should be Group B."
        #
        # Step 1: create per-(physician, day) BoolVars for working Group A / Group B.
        group_a_bool: dict[tuple, object] = {}  # (pid, d_idx) -> BoolVar
        group_b_bool: dict[tuple, object] = {}
        for pid in pids:
            for d_idx in range(len(all_dates)):
                a_vars = [
                    shifts[(pid, d_idx, shift.code)]
                    for block in BLOCKS for shift in block
                    if shift.site_group == SiteGroup.A
                ]
                b_vars = [
                    shifts[(pid, d_idx, shift.code)]
                    for block in BLOCKS for shift in block
                    if shift.site_group == SiteGroup.B
                ]
                if a_vars:
                    wa = model.new_bool_var(f"ga_{pid}_{d_idx}")
                    # HC-2 ensures at most one shift per physician per day → sum is 0 or 1
                    model.add(sum(a_vars) == wa)
                    group_a_bool[(pid, d_idx)] = wa
                if b_vars:
                    wb = model.new_bool_var(f"gb_{pid}_{d_idx}")
                    model.add(sum(b_vars) == wb)
                    group_b_bool[(pid, d_idx)] = wb

        # Step 1b: per-physician A-floor bonus.
        # Rewards assigning Group A shifts up to the target A count (~38% of requested).
        # Using a capped IntVar so the marginal value of an A shift drops once the
        # target is met — avoids over-shooting in either direction.
        group_balance_terms = []
        for pid in pids:
            sub = self.submissions[pid]
            eff_req = sub.shifts_requested or min(10, sub.shifts_max)
            target_a = max(1, round(eff_req * self._group_a_target))
            a_total_expr = sum(
                shifts[(pid, d_idx, shift.code)]
                for d_idx in range(len(all_dates))
                for block in BLOCKS
                for shift in block
                if shift.site_group == SiteGroup.A
            )
            a_floor = model.new_int_var(0, target_a, f"afloor_{pid}")
            model.add(a_floor <= a_total_expr)
            group_balance_terms.append(30 * a_floor)

        # Step 1c: group_b_site_preference — small bonus for preferred Group B site shifts.
        # This implements the per-physician within-Group-B site preference from physicians.yaml.
        # Weight is small (+6) so it only influences tie-breaking within equivalent options.
        _B_PREF_SITES: dict[str, frozenset] = {
            "nehc":  frozenset({"NEHC"}),
            "rah":   frozenset({"RAH I side", "RAH F side"}),
            "rah_f": frozenset({"RAH F side"}),
        }
        for pid in pids:
            cfg = _get_cfg(pid)
            if not (cfg and cfg.group_b_site_preference):
                continue
            preferred_sites = _B_PREF_SITES.get(cfg.group_b_site_preference)
            if not preferred_sites:
                continue
            for d_idx in range(len(all_dates)):
                for block in BLOCKS:
                    for shift in block:
                        if shift.site in preferred_sites:
                            group_balance_terms.append(
                                6 * shifts[(pid, d_idx, shift.code)]
                            )

        # Step 2: build objective terms for consecutive pairs
        for pid in pids:
            for d_idx in range(len(all_dates) - 1):
                wa1 = group_a_bool.get((pid, d_idx))
                wb1 = group_b_bool.get((pid, d_idx))
                wa2 = group_a_bool.get((pid, d_idx + 1))
                wb2 = group_b_bool.get((pid, d_idx + 1))

                # Reward A→B alternation
                if wa1 is not None and wb2 is not None:
                    ab = model.new_bool_var(f"ab_{pid}_{d_idx}")
                    model.add_implication(ab, wa1)
                    model.add_implication(ab, wb2)
                    model.add(wa1 + wb2 <= 1 + ab)
                    group_balance_terms.append(20 * ab)

                # Reward B→A alternation
                if wb1 is not None and wa2 is not None:
                    ba = model.new_bool_var(f"ba_{pid}_{d_idx}")
                    model.add_implication(ba, wb1)
                    model.add_implication(ba, wa2)
                    model.add(wb1 + wa2 <= 1 + ba)
                    group_balance_terms.append(20 * ba)

                # Penalise A→A consecutive (prefer alternation)
                if wa1 is not None and wa2 is not None:
                    aa = model.new_bool_var(f"aa_{pid}_{d_idx}")
                    model.add_implication(aa, wa1)
                    model.add_implication(aa, wa2)
                    model.add(wa1 + wa2 <= 1 + aa)
                    group_balance_terms.append(-40 * aa)  # penalty when A follows A

        # Soft: Singleton clustering — bonus for consecutive 2400h nights by the same physician.
        # This encourages the solver to cluster night shifts into runs of 2+ days rather than
        # spreading them as isolated singletons (which is harder on physicians and on the roster).
        # We create a per-(physician, day) BoolVar = 1 iff physician works any 2400h on that day,
        # then add +25 per consecutive-night pair.
        night_bool: dict[tuple, object] = {}
        for pid in pids:
            for d_idx in range(len(all_dates)):
                night_vars_for_day = [
                    shifts[(pid, d_idx, shift.code)]
                    for block in BLOCKS
                    for shift in block
                    if shift.time == "2400h"
                ]
                if night_vars_for_day:
                    nb = model.new_bool_var(f"nb_{pid}_{d_idx}")
                    # HC-2 guarantees at most one shift per physician per day,
                    # so sum(night_vars_for_day) is 0 or 1 — safe to equate with a BoolVar.
                    model.add(sum(night_vars_for_day) == nb)
                    night_bool[(pid, d_idx)] = nb

        clustering_bonus_terms = []
        for pid in pids:
            # Skip clustering bonus for physicians who prefer singleton nights.
            pid_cfg = _get_cfg(pid)
            if pid_cfg and pid_cfg.prefer_singleton_nights:
                continue
            for d_idx in range(len(all_dates) - 1):
                nb1 = night_bool.get((pid, d_idx))
                nb2 = night_bool.get((pid, d_idx + 1))
                if nb1 is None or nb2 is None:
                    continue
                consec = model.new_bool_var(f"cnight_{pid}_{d_idx}")
                # consec == (nb1 AND nb2): standard linearization
                model.add_implication(consec, nb1)
                model.add_implication(consec, nb2)
                # If both are 1 the solver must set consec=1 (since we're maximizing)
                model.add(nb1 + nb2 <= 1 + consec)
                clustering_bonus_terms.append(18 * consec)

        # Anti-clustering penalty for prefer_singleton_nights physicians.
        # These physicians want isolated 2400h shifts, so consecutive nights are penalised.
        for pid in pids:
            pid_cfg = _get_cfg(pid)
            if not (pid_cfg and pid_cfg.prefer_singleton_nights):
                continue
            for d_idx in range(len(all_dates) - 1):
                nb1 = night_bool.get((pid, d_idx))
                nb2 = night_bool.get((pid, d_idx + 1))
                if nb1 is None or nb2 is None:
                    continue
                consec = model.new_bool_var(f"anti_cnight_{pid}_{d_idx}")
                model.add_implication(consec, nb1)
                model.add_implication(consec, nb2)
                model.add(nb1 + nb2 <= 1 + consec)
                clustering_bonus_terms.append(-30 * consec)  # penalty for consecutive nights

        # Soft: Any-shift clustering bonus — reward consecutive working days.
        # Mirrors the 2400h singleton logic but for all shift types: a bonus for
        # each adjacent (day, day+1) pair where the physician works both days.
        # This discourages isolated single-day assignments across the whole roster.
        # Weight is kept below the 2400h bonus (12) since nights cluster more tightly.
        any_cluster_terms = []
        for pid in pids:
            # Note: prefer_singleton_nights physicians are NOT excluded here.
            # The anti-clustering penalty above handles their nights; we still
            # want to reward consecutive day shifts for them.
            for d_idx in range(len(all_dates) - 1):
                wb1 = worked_bool.get((pid, d_idx))
                wb2 = worked_bool.get((pid, d_idx + 1))
                if wb1 is None or wb2 is None:
                    continue
                consec = model.new_bool_var(f"consec_any_{pid}_{d_idx}")
                model.add_implication(consec, wb1)
                model.add_implication(consec, wb2)
                model.add(wb1 + wb2 <= 1 + consec)
                any_cluster_terms.append(10 * consec)

        # ----------------------------------------------------------------
        # HC-11: Late-shift rest privilege (rest_after_late_shift)
        # If a physician works a 1600h, 1800h, or 2000h shift on day d,
        # they must have day d+1 off entirely.
        # ----------------------------------------------------------------
        _LATE_TIMES = {"1600h", "1800h", "2000h"}
        for pid in pids:
            cfg = _get_cfg(pid)
            if not (cfg and cfg.rest_after_late_shift):
                continue
            for d_idx in range(len(all_dates) - 1):
                for block in BLOCKS:
                    for shift in block:
                        if shift.time not in _LATE_TIMES:
                            continue
                        late_var = shifts[(pid, d_idx, shift.code)]
                        for block2 in BLOCKS:
                            for shift2 in block2:
                                model.add_implication(
                                    late_var,
                                    shifts[(pid, d_idx + 1, shift2.code)].negated()
                                )

        # ----------------------------------------------------------------
        # HC-12: Max consecutive days with 1800h shift
        # ----------------------------------------------------------------
        for pid in pids:
            cfg = _get_cfg(pid)
            mc1800 = cfg.max_consecutive_1800h if cfg else 3
            if mc1800 >= 3:
                continue  # default: no effective constraint
            win = mc1800 + 1
            for start in range(len(all_dates) - mc1800):
                window_vars = [
                    shifts[(pid, d_idx, shift.code)]
                    for d_idx in range(start, start + win)
                    for block in BLOCKS
                    for shift in block
                    if shift.time == "1800h"
                ]
                if window_vars:
                    model.add(sum(window_vars) <= mc1800)

        # Soft penalty for 4-consecutive-day runs.
        # Physicians with max_consecutive_shifts >= 4 are ALLOWED to work 4 days in a
        # row (hard constraint), but we discourage it as a last resort.
        # Physicians with mc < 4 already cannot (HC-8), so skip them.
        run_penalty_terms = []
        for pid in pids:
            if _max_consec(pid) < 4:
                continue
            for d_idx in range(len(all_dates) - 3):
                w0 = worked_bool.get((pid, d_idx))
                w1 = worked_bool.get((pid, d_idx + 1))
                w2 = worked_bool.get((pid, d_idx + 2))
                w3 = worked_bool.get((pid, d_idx + 3))
                if w0 is None or w1 is None or w2 is None or w3 is None:
                    continue
                run4 = model.new_bool_var(f"run4_{pid}_{d_idx}")
                model.add_implication(run4, w0)
                model.add_implication(run4, w1)
                model.add_implication(run4, w2)
                model.add_implication(run4, w3)
                model.add(w0 + w1 + w2 + w3 <= 3 + run4)
                run_penalty_terms.append(-35 * run4)

        # Soft: Monday avoidance penalty (-20 per Monday shift)
        monday_penalty_terms = []
        for pid in pids:
            cfg = _get_cfg(pid)
            if not (cfg and cfg.avoid_mondays):
                continue
            for d_idx, d in enumerate(all_dates):
                if d.weekday() != 0:  # 0 = Monday
                    continue
                for block in BLOCKS:
                    for shift in block:
                        monday_penalty_terms.append(-5 * shifts[(pid, d_idx, shift.code)])

        # Composite objective (all terms are non-negative rewards; maximize)
        objective_terms = [1000 * filled_expr]
        objective_terms.extend(request_bonus_terms)
        objective_terms.extend(deficit_penalty_terms)
        objective_terms.extend(group_balance_terms)
        objective_terms.extend(clustering_bonus_terms)
        objective_terms.extend(any_cluster_terms)
        objective_terms.extend(run_penalty_terms)
        objective_terms.extend(monday_penalty_terms)

        model.maximize(sum(objective_terms))

        # ----------------------------------------------------------------
        # Solve
        # ----------------------------------------------------------------
        solver = _cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_search_workers = num_workers
        solver.parameters.log_search_progress = False

        # Solution callback: saves variable values on each improving solution so
        # we use the saved dict in _build_result instead of calling solver.value()
        # after solve() returns (avoids potential thread-state issues in frozen binaries).
        solution_cb = _SolutionCallback(shifts)

        logger.info(
            "CP-SAT: starting solve for %d-%02d with time_limit=%.0fs, workers=%d",
            year, month, time_limit, num_workers,
        )
        status = solver.solve(model, solution_cb)
        wall_time = solver.wall_time
        logger.info(
            "CP-SAT: status=%s  objective=%.0f  wall_time=%.1fs",
            solver.status_name(status),
            solution_cb.best_objective if solution_cb.best_values else 0.0,
            wall_time,
        )

        if progress_callback:
            progress_callback(100, 100, -solution_cb.best_objective)

        # ----------------------------------------------------------------
        # Extract solution
        # ----------------------------------------------------------------
        if status in (_cp_model.OPTIMAL, _cp_model.FEASIBLE) and solution_cb.best_values:
            result = self._build_result(
                year, month, all_dates, solution_cb.best_values, shift_by_code
            )
            # Attach solver quality info to stats
            if result.stats:
                is_optimal = status == _cp_model.OPTIMAL
                obj = solution_cb.best_objective
                bound = solution_cb.best_bound
                if is_optimal or abs(bound) < 1e-6:
                    gap_pct = 0.0
                else:
                    gap_pct = max(0.0, (bound - obj) / max(abs(bound), 1.0) * 100.0)
                result.stats.solver_status = "optimal" if is_optimal else "feasible"
                result.stats.optimality_gap_pct = round(gap_pct, 2)
                logger.info(
                    "CP-SAT quality: status=%s  obj=%.0f  bound=%.0f  gap=%.2f%%",
                    result.stats.solver_status, obj, bound, gap_pct,
                )
            return result

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

    # ------------------------------------------------------------------
    # Mutable-state helpers (mirrors ScheduleGenerator for post-generation use)
    # ------------------------------------------------------------------

    def _assign(self, pid: str, d: datetime.date, shift: Shift) -> None:
        self._slot_to_pid[(d, shift.code)] = pid
        self._pid_to_slots[pid].append((d, shift))
        self._shift_count[pid] += 1
        if shift.time in ("0600h", "2400h"):
            self._anchor_count[pid] += 1
        if d.weekday() in _WEEKEND_WEEKDAYS:
            self._weekend_keys[pid].add(_weekend_key(d))

    def _unassign(self, pid: str, d: datetime.date, shift: Shift) -> None:
        self._slot_to_pid.pop((d, shift.code), None)
        self._pid_to_slots[pid] = [
            (ad, s) for ad, s in self._pid_to_slots[pid]
            if not (ad == d and s.code == shift.code)
        ]
        self._shift_count[pid] = max(0, self._shift_count[pid] - 1)
        if shift.time in ("0600h", "2400h"):
            self._anchor_count[pid] = max(0, self._anchor_count[pid] - 1)
        if d.weekday() in _WEEKEND_WEEKDAYS:
            self._weekend_keys[pid] = {
                _weekend_key(ad) for ad, _ in self._pid_to_slots[pid]
                if ad.weekday() in _WEEKEND_WEEKDAYS
            }

    def _check_constraints(
        self, pid: str, d: datetime.date, shift: Shift, block_idx: int | None = None
    ) -> list[ViolationReason] | None:
        """Constraint check for post-generation use (check-violations endpoint)."""
        if block_idx is None:
            block_idx = SHIFT_TO_BLOCK[shift.code]
        violations = self._check_constraints_simple(pid, d, shift, block_idx)
        return violations if violations else None

    def assign_manual(
        self,
        physician_id: str,
        d: datetime.date,
        shift: Shift,
    ) -> list[ViolationReason]:
        """Force-assign physician_id to (d, shift), returning any rule violations."""
        violations = self._check_constraints(physician_id, d, shift) or []
        self._assign(physician_id, d, shift)
        return violations

    def _build_result(
        self,
        year: int,
        month: int,
        all_dates: list[datetime.date],
        saved_values: dict,
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
                        val = saved_values.get((pid, d_idx, shift.code), 0)
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

    def _near_miss_candidates(
        self, d: datetime.date, shift: Shift, max_n: int = 10
    ) -> list[CandidateOption]:
        # Only two things truly disqualify a physician: already working that day,
        # or marked unavailable. All other violations are shown as warnings only.
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
            # Exclude physicians who have hard violations — they are truly unavailable
            if any(v.rule in _HARD_VIOLATION_RULES for v in violations):
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

        day_specific = self._shift_avail.get((pid, d))
        if (pid, d, block_idx) not in self._avail:
            # For flat-file imports, a specific shift in this block is still valid
            # even if the full block isn't in _avail.
            if day_specific is None or shift.code not in day_specific:
                v.append(ViolationReason(rule="availability", description=f"Not available for block {block_idx} on {d}"))
        elif day_specific is not None and shift.code not in day_specific:
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
